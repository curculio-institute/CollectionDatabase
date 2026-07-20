"""Label PDF renderer.

Reproduces the original two-column ODT template format using Fira Sans Compressed
as a substitute for Context Condensed SSi.

Three label kinds — all 18 mm wide; heights are *minimums* that grow with text:

  data           18 × 2.5 mm min  — locality / date / collector
  determination  18 × 4.9 mm min  — taxon name + determiner
  identifier     18 × ~6.1 mm     — QR + collection name + big number (sizes to
                                    content; QR is the ~5.5 mm floor, stays under 7 mm)

One output surface: ``grouped_sheet(...)`` — the composite Print-queue page:
per-specimen columns of data / identifier / determination bands, plus a "New
identifiers" group for freshly reserved codes. Everything printable flows through
the print queue; there is no standalone per-kind sheet.

Within the grouped sheet, labels tile with a small gap (border-collapse: separate,
``_LABEL_GAP_BORDERED``) so each keeps its own complete border yet a single cut down
the gap separates two neighbours (no leftover strip, not two cuts). Each type's
border ("black" cut-guide line or "none") is a config choice — see
AppConfig.label_border_* / _border_decl.

All return raw PDF bytes.

Original template metrics (converted from inches):
  Column width : 0.7083 in = 18.0 mm
  Data row     : 0.0979 in =  2.5 mm  (2 text lines)
  Det. row     : 0.1924 in =  4.9 mm  (3 text lines)
  Line height  : 0.0555 in =  1.41 mm
  Font size    : 4 pt  (Context Condensed SSi → Fira Sans Compressed)
  Cell padding : L 0.53 mm  R 0.49 mm  T/B 0.19 mm
"""
from __future__ import annotations

import base64
import html as _html
import io
import re as _re
from dataclasses import dataclass, replace as _replace
from functools import lru_cache
from html.parser import HTMLParser as _HTMLParser
from pathlib import Path as _Path
from typing import Optional

import qrcode
from weasyprint import HTML
from weasyprint.formatting_structure.boxes import LineBox as _LineBox

from app.services.label_text import (abbreviate_name, format_coords,
                                     format_geo_prefix)
from app.vocab import SEX_SYMBOLS


def _e(v: str | None) -> str:
    """HTML-escape a field value; returns empty string for None."""
    return _html.escape(v) if v else ""


# ---------------------------------------------------------------------------
# Constants matching the original template
# ---------------------------------------------------------------------------

_W          = "18mm"
_FONT       = "'Fira Sans Compressed', 'Fira Sans Condensed', Arial Narrow, sans-serif"
_FONT_SIZE  = "4pt"


# The label font is BUNDLED (app/static/fonts) and referenced by absolute file://
# URL, not left to the host OS. WeasyPrint renders the PDF server-side, and on a
# fresh Windows/macOS machine 'Fira Sans Compressed' is not installed — WeasyPrint
# would silently fall back to a different font with different metrics and change the
# label width (clipping on an 18 mm label). Embedding the four faces here makes the
# rendered PDF byte-identical on every platform. `HTML(string=…)` is called with no
# base_url, so relative url() cannot resolve — absolute file:// URLs are required.
_FONTS_DIR = _Path(__file__).resolve().parent.parent / "static" / "fonts"


def _font_face(style: str, weight: str, filename: str) -> str:
    return f"""@font-face {{
    font-family: 'Fira Sans Compressed';
    font-style: {style};
    font-weight: {weight};
    src: url('{(_FONTS_DIR / filename).as_uri()}') format('truetype');
}}"""


_FONT_FACE_CSS = "\n".join((
    _font_face("normal", "400", "FiraSansCompressed-Regular.ttf"),
    _font_face("normal", "700", "FiraSansCompressed-Bold.ttf"),
    _font_face("italic", "400", "FiraSansCompressed-Italic.ttf"),
    _font_face("italic", "700", "FiraSansCompressed-BoldItalic.ttf"),
)) + "\n"

# Text-metric rules that make every backend lay out identically. Chromium auto-applies
# text-rendering:optimizeSpeed at these micro sizes, which DISABLES kerning — glyphs then
# use default advance widths and spread out (e.g. "48.320 0"), so a Chromium label came
# out wider (and wrapped differently) than the same WeasyPrint label. geometricPrecision
# forces kerning on regardless of size; font-kerning:normal states it explicitly. Applied
# to both the render CSS and the fit-measurement CSS so the width WeasyPrint measures is
# the width every backend prints. WeasyPrint already kerns, so these are no-ops for it.
_TEXT_METRICS = "text-rendering: geometricPrecision; font-kerning: normal;"
_LINE_H     = "1.41mm"   # 0.0555 in
_PAD        = "0.19mm 0.53mm"   # top/bottom  left/right — fit-measurement box (width only)

# Vertical padding inside a label band — the ROBUST clearance that keeps ink off the
# border in every engine. WeasyPrint's line boxes leave slack above the first line, but
# Chromium seats a first line flush with the content-box top, so at the tight 1em
# line-height a bold-italic ascender (the determination genus) crossed the top border and
# a cap (the id collection name) touched it. The fix is not per-engine slack but explicit
# padding sized to the worst-case ascender/descender overshoot at 4pt, so the same values
# hold no matter which backend renders. Horizontal padding is unchanged (0.53mm data/det,
# 0.5mm id) so the one-line fit width (_FIT_CSS / _PAD) still matches what prints.
_PAD_TOP    = "0.45mm"   # first-line ascenders clear the top border
_PAD_BOT    = "0.3mm"    # last-line descenders clear the bottom border
_PAD_LR     = "0.53mm"   # data / determination — matches _PAD's horizontal (fit width)
_PAD_LR_ID  = "0.5mm"    # identifier band
# Gap between neighbouring labels on the grouped sheet — matched to the mybioform
# "Etikettenmuster" reference (measured 2026-07-08: ~0.1 mm hairline borders,
# ~0.42–0.47 mm gap on both axes). The gap is the cut lane: it must be wide enough
# that a single blade pass drops between two neighbouring hairlines with no leftover
# strip, so we track the reference ~0.4 mm.
_LABEL_GAP_BORDERED = "0.4mm"


def _border_decl(choice: str, backend: str = "weasyprint") -> str:
    """CSS declaration drawing a band's 0.1 mm hairline cut guide, or '' for none.

    ``"black"`` → a thin hairline (~0.1 mm, matching the Etikettenmuster reference);
    anything else (``"none"``) → no line. Default is black (AppConfig.label_border_*).

    The declaration is **backend-specific**, because no single CSS property draws a
    0.1 mm line in both engines: WeasyPrint renders a real 0.1 mm ``border`` as a fill,
    but Chromium floors any ``border`` to one device pixel (0.75 pt ≈ 0.27 mm, ~2.6×
    too thick). For Chromium the hairline is drawn as an **inset box-shadow**, which it
    paints at true thickness — and which WeasyPrint, conversely, does not render at all.
    Measured both ways (2026-07-20); this split is deliberate, not redundant."""
    if choice != "black":
        return ""
    if backend == "chromium":
        return "box-shadow: inset 0 0 0 0.1mm #000;"
    return "border: 0.1mm solid #000;"


# ---------------------------------------------------------------------------
# Shared base CSS
# ---------------------------------------------------------------------------

_BASE_CSS = _FONT_FACE_CSS + f"""
@page {{ size: A4; margin: 5mm; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {_FONT}; font-size: {_FONT_SIZE}; line-height: {_LINE_H};
       {_TEXT_METRICS} }}
/* Block flow with inline-block items (not flex): flex containers do not
   fragment across pages in WeasyPrint, which wasted page 1 on any multi-page
   sheet. Block flow lays items left-to-right, wraps, AND paginates. */
.sheet {{ line-height: 0; }}
.label {{
    display: inline-block;
    vertical-align: top;
    line-height: {_LINE_H};
    width: {_W};
    margin: 0 0.3mm 0.3mm 0;
    border: 0.1mm dashed #aaa;
    padding: {_PAD};
    page-break-inside: avoid;
    overflow: hidden;
}}
em {{ font-style: italic; }}
"""


# ---------------------------------------------------------------------------
# Data labels  (18 × 2.5 mm, 2 lines)
# ---------------------------------------------------------------------------
# Format matches original:
#   Line 1:  Country, Region: Locality lat, lon, habitat
#   Line 2:  leg. Collector  Date
# The "Country, Region" prefix collapses ONE of the two to its ISO code when the pair is
# too long for 18 mm ("Germany, BW" / "GR, Peloponnese Region") — never both, so the label
# keeps a name a human can read. See label_text.format_geo_prefix.

@dataclass
class DataLabel:
    country: Optional[str]                  = None
    country_code: Optional[str]             = None      # ISO 3166-1, from country.iso_code
    state_province: Optional[str]           = None
    state_province_code: Optional[str]      = None      # ISO 3166-2, from state_province.iso_code
    municipality: Optional[str]             = None
    county: Optional[str]                   = None
    locality: Optional[str]                 = None
    verbatim_locality: Optional[str]        = None
    latitude: Optional[float]               = None
    longitude: Optional[float]              = None
    coordinate_uncertainty_m: Optional[float] = None
    elevation_min: Optional[int]            = None
    elevation_max: Optional[int]            = None
    event_date: Optional[str]               = None
    recorded_by: Optional[str]              = None
    habitat: Optional[str]                  = None
    associated_species: Optional[list[str]] = None
    # Print-only override typed in the queue (#37): when set, the label prints
    # this verbatim (one <div> per line) instead of the composed fields. Lets the
    # user abbreviate/extend for label fit without touching the record.
    text_override: Optional[str]            = None


# ---------------------------------------------------------------------------
# Print-only override (#37/#45/#46)
# ---------------------------------------------------------------------------
# An override may be stored in one of two forms on print_queue.text_override:
#   * legacy plaintext  — no markup; printed one <div> per line (escaped).
#   * formatted HTML     — the sanitized innerHTML captured from the WYSIWYG
#                          contenteditable editor; keeps the name's italics/bold.
# `_override_html` picks the path by whether the stored string contains a tag;
# `sanitize_override_html` is the single gatekeeper for the HTML form, both when
# storing (UI) and when rendering (here), so only a tiny safe subset ever prints.

# Inline emphasis we keep; the browser's <b>/<i> map onto our house <strong>/<em>.
_OVERRIDE_INLINE = {"em", "strong"}
_OVERRIDE_TAG_MAP = {"b": "strong", "i": "em", "em": "em", "strong": "strong"}
# Block/break tags become a line structure of <div>s (matching the auto labels).
_OVERRIDE_BLOCK = {"div", "p"}


class _OverrideSanitizer(_HTMLParser):
    """Reduce contenteditable HTML to the safe label subset.

    Keeps only inline <em>/<strong> emphasis (with <b>→<strong>, <i>→<em>),
    drops every attribute, and rebuilds the line structure as <div> blocks from
    the source's block/<br> boundaries. Unknown tags are unwrapped (their text
    survives, their markup does not)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = []
        self._cur: list[str] = []
        self._open: list[str] = []   # stack of emitted inline tags (for clean close)

    def _newline(self) -> None:
        self._lines.append("".join(self._cur))
        self._cur = []

    def handle_starttag(self, tag, attrs):
        if tag in _OVERRIDE_BLOCK:
            if self._cur:
                self._newline()
        elif tag == "br":
            self._newline()
        elif tag in _OVERRIDE_TAG_MAP:
            mapped = _OVERRIDE_TAG_MAP[tag]
            self._open.append(mapped)
            self._cur.append(f"<{mapped}>")
        else:
            # An unknown tag is not markup we understand — it is TEXT the user wants printed.
            # `Quercus <robur>` is a real thing to write on a label, and dropping it silently
            # removed part of the label (#67). Emit it escaped, exactly as typed.
            self._cur.append(_html.escape(self.get_starttag_text() or f"<{tag}>"))

    def handle_startendtag(self, tag, attrs):
        # `<br/>` is handled by handle_starttag; anything else self-closing is text.
        if tag in _OVERRIDE_BLOCK or tag == "br" or tag in _OVERRIDE_TAG_MAP:
            self.handle_starttag(tag, attrs)
            return
        self._cur.append(_html.escape(self.get_starttag_text() or f"<{tag}/>"))

    def handle_endtag(self, tag):
        if tag in _OVERRIDE_BLOCK:
            if self._cur:
                self._newline()
        elif tag in _OVERRIDE_TAG_MAP and self._open:
            mapped = _OVERRIDE_TAG_MAP[tag]
            if mapped in self._open:
                # close back to (and including) the matching tag
                while self._open:
                    t = self._open.pop()
                    self._cur.append(f"</{t}>")
                    if t == mapped:
                        break
        elif tag not in _OVERRIDE_TAG_MAP and tag != "br":
            self._cur.append(_html.escape(f"</{tag}>"))

    def handle_data(self, data):
        self._cur.append(_html.escape(data))

    def result(self) -> str:
        while self._open:                       # close anything left open
            self._cur.append(f"</{self._open.pop()}>")
        if self._cur:
            self._newline()
        lines = [ln for ln in self._lines if ln.strip()]
        return "".join(f"<div>{ln}</div>" for ln in lines)


def sanitize_override_html(html: str) -> str:
    """Reduce arbitrary (contenteditable) HTML to the safe label subset:
    <div> lines containing only <em>/<strong>. Returns '' for empty content."""
    p = _OverrideSanitizer()
    p.feed(html or "")
    p.close()
    return p.result()


def _looks_like_html(text: str) -> bool:
    """True only for markup this label subset actually understands.

    The old test was `<\\w+` — any angle-bracketed word — so a plain-text override reading
    `Quercus <robur>` was taken for HTML and `<robur>` was stripped out of the printed label
    (#67). A tag we do not emit is not markup: it is text. Only div/p/br/b/i/em/strong mean
    "this override is formatted".
    """
    return bool(_re.search(r"</?(?:div|p|br|b|i|em|strong)\b", text or "", _re.I))


def _override_html(text: str) -> str:
    """Render a print-only override. Formatted-HTML overrides are sanitized and
    kept (italics/bold survive); legacy plaintext is escaped one <div> per line."""
    if _looks_like_html(text):
        return sanitize_override_html(text)
    return "".join(f"<div>{_e(line)}</div>" for line in text.split("\n") if line.strip())


def canonical_override(text: str | None) -> str | None:
    """The canonical stored form of an override — sanitized HTML, or None for "no override".

    Store and render must agree (#67). The editor hands us a contenteditable's `innerHTML`,
    which is already entity-encoded: typing `R & D` yields `R &amp; D`. Treated as plaintext
    that was escaped a *second* time and printed as the literal `R &amp; D`. Sanitizing on the
    way in makes the DB hold one canonical form, so rendering is a pass-through and the printed
    label is what the preview showed.

    Anything that reduces to nothing — '', whitespace, `<div></div>` — is **None**, not an empty
    override: a blank label pinned to a specimen is a curation error, and the record still holds
    the data, so falling back to the auto text is always the honest answer.

    Input here is **always HTML** — it comes from a contenteditable's innerHTML (or the dialog's
    raw-HTML source box) — so it is always sanitised, never sniffed. Sniffing is what broke
    `R &amp; D`: no tags, so it looked like plaintext and was escaped a second time.
    (`_override_html` still sniffs, because it must also render *legacy* plaintext overrides
    stored before the editor existed.)
    """
    if text is None:
        return None
    return sanitize_override_html(text) or None


def _rendered_override(text: str | None) -> str | None:
    """The override as it will print, or None when there is effectively none (see above)."""
    if text is None:
        return None
    return _override_html(text) or None


def label_auto_html(lbl) -> str:
    """The composed, *formatted* inner HTML of a Data/DeterminationLabel ignoring
    any override — used to seed the WYSIWYG editor and to detect 'edited' state."""
    base = _replace(lbl, text_override=None)
    return _data_inner_html(base) if isinstance(base, DataLabel) else _det_inner_html(base)


def label_plaintext(lbl) -> str:
    """Plain-text rendering of a Data/DeterminationLabel as it will print —
    one line per ``<div>``, tags stripped, entities unescaped. Used to pre-fill
    the print-queue override editor so the user tweaks the real label text (#37)."""
    inner = _data_inner_html(lbl) if isinstance(lbl, DataLabel) else _det_inner_html(lbl)
    lines = _re.findall(r"<div[^>]*>(.*?)</div>", inner, _re.S)
    text = "\n".join(_re.sub(r"<[^>]+>", "", ln) for ln in lines)
    return _html.unescape(text).strip()


def _data_inner_html(lbl: DataLabel) -> str:
    # An override that renders to nothing falls back to the auto text — it must never print a
    # BLANK label (#67). `text_override is not None` treated '' as a real override.
    ov = _rendered_override(lbl.text_override)
    if ov is not None:
        return ov
    return "".join(f"<div>{t}</div>"
                   for t in [_data_line1(lbl), _data_line2(lbl)] if t)


def _data_line1(lbl: DataLabel) -> str:
    _c_text, _s_text = format_geo_prefix(lbl.country, lbl.country_code,
                                         lbl.state_province, lbl.state_province_code)
    prefix = ", ".join(_e(t) for t in (_c_text, _s_text) if t)

    parts: list[str] = []

    for f in (lbl.municipality, lbl.verbatim_locality or lbl.locality):
        if f:
            parts.append(_e(f))

    coords = format_coords(lbl.latitude, lbl.longitude, lbl.coordinate_uncertainty_m)
    if coords:
        parts.append(coords)

    if lbl.elevation_min is not None:
        elev = (f"{lbl.elevation_min}–{lbl.elevation_max} m"
                if lbl.elevation_max and lbl.elevation_max != lbl.elevation_min
                else f"{lbl.elevation_min} m")
        parts.append(elev)

    if lbl.habitat:
        parts.append(_e(lbl.habitat))

    if lbl.associated_species:
        for sp in lbl.associated_species:
            parts.append(f"<em>{_html.escape(sp)}</em>")

    body = ", ".join(parts)
    if prefix:
        return f"{prefix}: {body}" if body else prefix
    return body


def _data_line2(lbl: DataLabel) -> str:
    date = _e(lbl.event_date) if lbl.event_date else ""

    def _build(name: str | None) -> str:
        leg = f"leg. {_e(name)}" if name else ""
        return "  ".join(p for p in [leg, date] if p)

    full = _build(lbl.recorded_by)
    # Full collector name when it fits one line; otherwise abbreviate it
    # ("John Doe" -> "J. Doe"). Same rule as the determiner (_det_line3).
    if not lbl.recorded_by or _fits_one_line(full):
        return full
    return _build(abbreviate_name(lbl.recorded_by))


# ---------------------------------------------------------------------------
# Determination labels  (18 × 4.9 mm, 3 lines)
# ---------------------------------------------------------------------------
# Format matches original:
#   Line 1:  Genus (Subgenus)  [or Genus s.str. / Genus s.l.]
#   Line 2:  species authorship  [italic]
#   Line 3:  det. Determiner  Year

@dataclass
class DeterminationLabel:
    genus: Optional[str]                  = None
    subgenus: Optional[str]               = None
    subgenus_qualifier: Optional[str]     = None   # e.g. "s.str.", "s.l."
    specific_epithet: Optional[str]       = None
    infraspecific_epithet: Optional[str]  = None
    authorship: Optional[str]             = None
    qualifier: Optional[str]              = None   # e.g. "cf.", "aff.", "?"
    determiner: Optional[str]             = None
    year: Optional[str]                   = None
    sex: Optional[str]                    = None
    type_status: Optional[str]            = None   # e.g. "Holotype", "Paratype"
    # Print-only override typed in the queue (#37) — see DataLabel.text_override.
    text_override: Optional[str]          = None


def _det_inner_html(lbl: "DeterminationLabel") -> str:
    """Full determination-label body — the print-only override verbatim if set,
    else the type-status line (if any) + composed name block + determiner/year."""
    ov = _rendered_override(lbl.text_override)     # '' → fall back to auto, never blank (#67)
    if ov is not None:
        return ov
    ts = (f'<div style="text-transform:uppercase;letter-spacing:.04em">'
          f'{_e(lbl.type_status)}</div>') if lbl.type_status else ""
    det = _det_line3(lbl)
    return ts + _det_name_html(lbl) + (f"<div>{det}</div>" if det else "")


def _bi(text: str) -> str:
    """Wrap already-escaped text in bold-italic tags."""
    return f"<strong><em>{text}</em></strong>"


def _det_line1(lbl: DeterminationLabel) -> str:
    genus = _bi(_e(lbl.genus)) if lbl.genus else ""
    if lbl.subgenus:
        if lbl.subgenus == lbl.genus:
            suffix = _e(lbl.subgenus_qualifier) or "s.str."
            return f"{genus} {suffix}".strip()
        sg = f"(<em>{_e(lbl.subgenus)}</em>)"
        return f"{genus} {sg}".strip() if genus else sg
    return genus


def _det_line2(lbl: DeterminationLabel) -> str:
    parts = []
    if lbl.qualifier:
        parts.append(_e(lbl.qualifier))
    epithet = " ".join(_e(p) for p in [lbl.specific_epithet, lbl.infraspecific_epithet] if p)
    if epithet:
        parts.append(_bi(epithet))
    if lbl.authorship:
        parts.append(_e(lbl.authorship))
    if lbl.sex:
        sym = SEX_SYMBOLS.get(lbl.sex.lower())
        if sym:
            parts.append(sym)
    return " ".join(parts)


def _det_line3(lbl: DeterminationLabel) -> str:
    year = _e(lbl.year) if lbl.year else ""

    def _build(name: str | None) -> str:
        det = f"det. {_e(name)}" if name else ""
        return "  ".join(p for p in [det, year] if p)

    full = _build(lbl.determiner)
    # Full determiner name when it fits the label on one line; otherwise
    # abbreviate it ("John Doe" -> "J. Doe").
    if not lbl.determiner or _fits_one_line(full):
        return full
    return _build(abbreviate_name(lbl.determiner))


def _det_name_html(lbl: DeterminationLabel) -> str:
    """Scientific-name block(s) for the determination label.

    Keep the traditional line break after the genus/subgenus when the broken
    two-line layout fits the label width (preferred look). When a name +
    authorship is too long for that, collapse the break and let the whole name
    flow and wrap as one block, so the label grows taller instead of clipping.
    """
    l1 = _det_line1(lbl)
    l2 = _det_line2(lbl)
    if l1 and l2 and _fits_one_line(l1) and _fits_one_line(l2):
        return f"<div>{l1}</div><div>{l2}</div>"          # enough space → keep genus break
    name = " ".join(p for p in (l1, l2) if p)
    return f"<div>{name}</div>" if name else ""           # tight → flow + grow


# Self-contained CSS for fit measurement: a plain *block* box at the label's
# content width + font. Deliberately independent of the sheet/.label layout (which
# is inline-block for pagination) so the line-box count reflects only text
# wrapping, not an anonymous line box around an inline-block.
_FIT_CSS = _FONT_FACE_CSS + f"""
@page {{ size: 60mm 60mm; margin: 0; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {_FONT}; font-size: {_FONT_SIZE}; line-height: {_LINE_H};
       {_TEXT_METRICS} }}
.m {{ width: {_W}; padding: {_PAD}; }}
"""


@lru_cache(maxsize=2048)
def _fits_one_line(inner_html: str) -> bool:
    """True if `inner_html` lays out on a single line at the 18 mm label content
    width (data and determination labels share the same width + font). Measured
    with WeasyPrint because character count is a poor proxy for width in a
    proportional condensed font (e.g. wide "M…" vs narrow "i…"). On any error,
    default to True — the label grows rather than clips, so a wrong "fits" never
    loses data."""
    try:
        html = (f'<html><head><style>{_FIT_CSS}</style></head>'
                f'<body><div class="m">{inner_html}</div></body></html>')
        doc = HTML(string=html).render()
        lines, stack = 0, [doc.pages[0]._page_box]
        while stack:
            box = stack.pop()
            if isinstance(box, _LineBox):
                lines += 1
            stack.extend(getattr(box, "children", None) or [])
        return lines <= 1
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Identifier labels  (18 × 5.5 mm, QR + code)
# ---------------------------------------------------------------------------

def _qr_data_url(data: str) -> str:
    qr = qrcode.QRCode(
        version=1, box_size=12, border=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _split_identifier_code(code: str) -> tuple[str, str]:
    """Split 'JJPC-03963' into ('JJPC-', '03963').
    Returns ('', code) for codes without a hyphen (legacy format)."""
    idx = code.rfind("-")
    if idx < 0:
        return "", code
    return code[:idx + 1], code[idx + 1:]


# Available width (mm) for the number beside the QR, and the condensed-font digit
# advance in mm per point per character. Used to auto-size the number so a longer code
# (e.g. 6-digit 000023 / 100000) shrinks to fit instead of overflowing. The cap also
# respects the label height (name + prefix + number stack in ~6.5 mm).
_ID_NUM_AVAIL_MM = 10.0
_NUM_ADVANCE_MM_PER_PT = 0.182   # Fira Sans Compressed digit advance (measured; the
                                 # number now prints regular weight, slightly narrower than
                                 # this bold-measured bound, so auto-sizing stays conservative)
_ID_NUM_MAX_PT = 10.5
_ID_NUM_MIN_PT = 6.5


def _id_number_font_pt(num: str) -> float:
    """Point size for the big number so it fits the fixed text column beside the QR.

    Digit advance is proportional to font size, so the width of ``n`` chars is
    ``n × pt × _NUM_ADVANCE_MM_PER_PT``; solve for the pt that fills the available
    width, then clamp. 4–5 digit codes hit the cap; 6+ digits shrink to fit."""
    n = max(len(num), 1)
    pt = _ID_NUM_AVAIL_MM / (n * _NUM_ADVANCE_MM_PER_PT)
    return round(max(_ID_NUM_MIN_PT, min(_ID_NUM_MAX_PT, pt)), 1)


def _id_label_inner(code: str, collection_name: str = "") -> str:
    """Inner HTML for one identifier label: tiny full collection-name line, then a
    row with the QR on the left and — to its right — the collection-code prefix
    (``JJPC-``, small) stacked *over* the sequence number (``00304``, large + bold,
    auto-sized to fit).

    Splitting the prefix onto its own line lets the number print big and legible
    (redesign 2026-07-07); the QR still encodes the whole ``JJPC-00304``. The prefix
    keeps its trailing hyphen (the DB codes are ``JJPC-00304``, so the two lines read
    ``JJPC-`` / ``00304``). The tiny full-name line is kept (#56).

    Layout follows the user's own template: **QR on the left**, and to its right a
    centred stack of three lines — full collection name (small) / ``JJPC-`` / the big
    number. The number is auto-sized (``_id_number_font_pt``) so a longer code shrinks
    to fit rather than overflow. Self-contained so it renders identically in the Labels
    batch sheet AND the Print-queue grouped cell.
    """
    prefix, num = _split_identifier_code(code)     # ('JJPC-', '00304')
    qr = _qr_data_url(code)
    name_html = (
        f'<div class="id-collname">{_e(collection_name)}</div>'
        if collection_name else ""
    )
    prefix_html = f'<div class="id-prefix">{_e(prefix)}</div>' if prefix else ""
    num_pt = _id_number_font_pt(num)
    return (
        f'<div class="id-label">'
        f'<img class="id-qr" src="{qr}">'
        f'<div class="id-text">{name_html}{prefix_html}'
        f'<div class="id-number" style="font-size:{num_pt}pt">{_e(num)}</div></div>'
        f'</div>'
    )


# Shared CSS for the identifier label — the self-contained block inside the grouped
# cell. QR left; a left-aligned name / prefix / number stack right. The block sizes
# to its content (no min-height floor) so the border hugs the QR/text with no dead
# vertical space — important for borderless labels, where any slack reads as a large
# gap between one row's number and the next row's text.
_ID_TEXT_CSS = """
.id-label {
    display: flex; flex-direction: row; align-items: center; gap: 0.6mm;
    width: 100%;
    font-family: 'Fira Sans Compressed', 'Fira Sans Condensed', 'Arial Narrow', sans-serif;
    font-weight: 400;
}
.id-qr { width: 5.5mm; height: 5.5mm; flex-shrink: 0; image-rendering: pixelated; }
/* left-aligned so the name / prefix / number hug the QR instead of floating in the
   centre of the wide column. */
.id-text { flex: 1; min-width: 0; text-align: left; line-height: 1.0; overflow: hidden; }
/* All regular weight (not bold): at these micro sizes bold thickens/fills the digit
   counters on a real printer; regular stays cleaner (decided 2026-07-07). */
.id-collname {
    /* line-height 1.4 (not the id-text default 1.0) gives the caps top clearance:
       Chromium seats a line-height:1.0 first line's glyph tops right at the label's
       overflow:hidden edge and clips them ("Jilg Private Collection" lost its tops);
       the extra leading drops the glyphs clear. WeasyPrint had clearance already, so
       this only adds a hair of space there. (measured 2026-07-20) */
    line-height: 1.4;
    font-size: 2.5pt; font-weight: 400; letter-spacing: 0;
    white-space: nowrap; overflow: hidden;
}
.id-prefix { font-size: 3.4pt; font-weight: 400; letter-spacing: 0.3pt; }
/* font-size is set inline per label (auto-sized to fit; see _id_number_font_pt). */
.id-number { font-weight: 400; letter-spacing: 0.2pt; white-space: nowrap; }
"""


def _collection_of(code: str, names: dict[str, str] | None) -> str:
    """Full collection name for a code, by its prefix (``JJPC-00304`` → ``JJPC``)."""
    if not names:
        return ""
    prefix, _ = _split_identifier_code(code)
    return names.get(prefix.rstrip("-"), "")


# ---------------------------------------------------------------------------
# Grouped sheet — print queue output
# ---------------------------------------------------------------------------
# Layout: the sheet is a wrapping flow of groups (one per queue addition, e.g.
# one Mounting Session save or one batch of reserved codes). Within a group,
# labels are a column-per-specimen grid with three bands stacked TOP→BOTTOM:
# data (occurrence) / identifier / determination. A specimen's own labels touch
# (no row gap) so they stay associated while cutting; neighbouring specimens get
# a small gap; whole groups get a large gap. Each group prints a small origin
# header; the sheet prints a small "Printed:" timestamp at the top.
#
# These gap/border metrics are intentionally named constants — expect to tune
# them by eye once a real PDF is inspected.

_LABEL_W_MM  = 18.0    # one label column width (matches _W)
_SPEC_GAP_MM = 1.2     # between specimens within a group (small, horizontal)
_GROUP_GAP = "6mm"     # between separate queue-addition groups (large)
_SPEC_GAP  = f"{_SPEC_GAP_MM}mm"
_CHUNK_GAP = "1.5mm"   # between wrapped specimen-runs inside one group (vertical)
_LABELS_PER_ROW = 10   # specimens per row before a group wraps (18mm each on A4)


def _chunk_width_mm(n: int) -> float:
    """Exact width of an n-column chunk: n labels + (n-1) inter-specimen gaps."""
    return n * _LABEL_W_MM + max(n - 1, 0) * _SPEC_GAP_MM


@dataclass
class SpecimenLabels:
    """One specimen's labels within a group (any field may be absent)."""
    data:          Optional[DataLabel]          = None
    determination: Optional[DeterminationLabel] = None
    id_code:       Optional[str]                = None
    # Preview-only metadata (ignored by the print PDF). When the sheet is rendered
    # editable=True (the WYSIWYG Print-queue preview), the data / determination boxes
    # carry these as contenteditable hooks so a click maps to the queue row and edits
    # apply to every identical label. None everywhere on the print path.
    data_qid:      Optional[int]                = None
    det_qid:       Optional[int]                = None
    data_ident:    Optional[str]                = None
    det_ident:     Optional[str]                = None
    co_id:         Optional[int]                = None


@dataclass
class LabelGroup:
    source:    Optional[str]              # origin header, e.g. "Mounting Session"
    specimens: list[SpecimenLabels]


# Layout uses an HTML table per chunk rather than CSS grid/flex: WeasyPrint's
# grid `justify-content` and inline-block shrink-to-fit are both unreliable here
# (columns stretch or wrap across group boundaries), but table layout is solid.
# Each group is an inline-block box that wraps with a large margin; inside it a
# fixed-layout table has one column per specimen and one row per band (data /
# identifier / determination). The chunk table is `border-collapse: separate` with
# a small `_LABEL_GAP`, so every label keeps its own complete border and neighbours
# are **separated by a thin gap** — small enough for one cut down the gap (no
# leftover strip, not two cuts). The large `_GROUP_GAP` between groups keeps
# different queue actions separable (decided 2026-07-07: small gap within a group,
# large gap between groups). Per-band borders come from config (AppConfig.label_border_*).

def _grouped_css(borders: dict[str, str] | None = None,
                 backend: str = "weasyprint") -> str:
    borders = borders or {}
    bd = _border_decl(borders.get("data", "black"), backend)
    bt = _border_decl(borders.get("determination", "black"), backend)
    bi = _border_decl(borders.get("identifier", "black"), backend)
    return _BASE_CSS + _ID_TEXT_CSS + f"""
.printed-at {{ font-size: 5pt; color: #666; margin-bottom: 3mm; }}
.group {{ display: inline-block; vertical-align: top; line-height: {_LINE_H}; margin: 0 {_GROUP_GAP} {_GROUP_GAP} 0; }}
/* A big identifier-only grid must span pages: an inline-block box is atomic (never splits),
   so a 450-label grid overflows a single page instead of flowing (#132). Make it block-level
   and let the grid break BETWEEN rows (each row kept whole below). */
.group-block {{ display: block; }}
.group-header {{ font-size: 5pt; color: #666; margin-bottom: 0.4mm; letter-spacing: 0.2pt; }}
.chunk {{ table-layout: fixed; border-collapse: separate; border-spacing: {_LABEL_GAP_BORDERED}; page-break-inside: avoid; }}
/* The identifier grid may be taller than a page — break between rows, never mid-row, so no
   label is split and no page is left mostly empty (#132). Overrides .chunk's avoid. */
.id-grid {{ page-break-inside: auto; }}
.id-grid tr {{ page-break-inside: avoid; break-inside: avoid; }}
.chunk + .chunk {{ margin-top: {_CHUNK_GAP}; }}
.cell {{ width: 18mm; padding: 0; vertical-align: top; }}
.lbl-data {{
    min-height: 2.5mm;
    {bd} padding: {_PAD_TOP} {_PAD_LR} {_PAD_BOT}; overflow: hidden;
    font-size: {_FONT_SIZE};
}}
.lbl-det {{
    min-height: 4.9mm;
    /* overflow hidden (not visible): a first line whose bold-italic ascenders would
       otherwise poke past the border is kept inside it — the vertical padding is sized
       so real text never actually reaches the clip, so nothing is lost (see _PAD_TOP). */
    {bt} padding: {_PAD_TOP} {_PAD_LR} {_PAD_BOT}; overflow: hidden;
    font-size: {_FONT_SIZE};
}}
.lbl-id {{
    {bi} padding: {_PAD_TOP} {_PAD_LR_ID} {_PAD_BOT};
    overflow: hidden;
}}
"""


# When the sheet is rendered editable (the WYSIWYG Print-queue preview), a data /
# determination box becomes contenteditable and tagged so the browser can map a click
# back to its queue row (data-qid) and highlight/apply to every identical label
# (data-ident). The print PDF passes editable=False, so these attributes never appear
# on paper. The class hook (pq-edit) is what the preview's blur listener matches.
def _edit_cell(base_cls: str, inner: str, qid: Optional[int],
               ident: Optional[str], editable: bool) -> str:
    """A band cell; when editable and it has a queue id, the box is contenteditable
    and tagged (data-qid maps a click to the row; data-ident groups identical labels)."""
    if editable and qid is not None:
        id_attr = f' data-ident="{_e(ident)}"' if ident else ""
        div = (f'<div class="{base_cls} pq-edit" contenteditable="true" '
               f'data-qid="{qid}"{id_attr}>{inner}</div>')
    else:
        div = f'<div class="{base_cls}">{inner}</div>'
    return f'<td class="cell">{div}</td>'


def _data_cell(sp: SpecimenLabels, editable: bool = False) -> str:
    if sp.data is None:
        return '<td class="cell"></td>'
    return _edit_cell("lbl-data", _data_inner_html(sp.data),
                      sp.data_qid, sp.data_ident, editable)


def _det_cell(sp: SpecimenLabels, editable: bool = False) -> str:
    if sp.determination is None:
        return '<td class="cell"></td>'
    return _edit_cell("lbl-det", _det_inner_html(sp.determination),
                      sp.det_qid, sp.det_ident, editable)


def _id_cell(code: Optional[str], names: dict[str, str] | None = None) -> str:
    if not code:
        return '<td class="cell"></td>'
    inner = _id_label_inner(code, _collection_of(code, names))
    return f'<td class="cell"><div class="lbl-id">{inner}</div></td>'


def _group_html(group: LabelGroup, names: dict[str, str] | None = None,
                editable: bool = False) -> str:
    specs = group.specimens
    if not specs:
        return ""
    has_data = any(s.data is not None for s in specs)
    has_id   = any(s.id_code for s in specs)
    has_det  = any(s.determination is not None for s in specs)
    id_only  = has_id and not has_data and not has_det

    chunks: list[str] = []
    if id_only:
        # Identifier-only group (e.g. "New identifiers"): tile every code into ONE
        # multi-row table so the vertical gap between rows is a single border-spacing
        # (~0.4 mm cut lane), matching the horizontal gap — a uniform grid like the
        # Etikettenmuster reference. Wrapping each run into its own chunk table would
        # instead stack two border-spacings + the chunk margin (~2.3 mm), which reads
        # as far too much vertical space between rows.
        id_rows = [
            "<tr>" + "".join(_id_cell(s.id_code, names)
                             for s in specs[start:start + _LABELS_PER_ROW]) + "</tr>"
            for start in range(0, len(specs), _LABELS_PER_ROW)
        ]
        chunks.append(f'<table class="chunk id-grid">{"".join(id_rows)}</table>')
    else:
        for start in range(0, len(specs), _LABELS_PER_ROW):
            chunk = specs[start:start + _LABELS_PER_ROW]
            rows: list[str] = []
            # Band order top→bottom: data, identifier, determination. Each present
            # band is a table row with one cell per column (empty <td> if missing)
            # so columns stay aligned across bands.
            if has_data:
                rows.append("<tr>" + "".join(_data_cell(s, editable) for s in chunk) + "</tr>")
            if has_id:
                rows.append("<tr>" + "".join(_id_cell(s.id_code, names) for s in chunk) + "</tr>")
            if has_det:
                rows.append("<tr>" + "".join(_det_cell(s, editable) for s in chunk) + "</tr>")
            chunks.append(f'<table class="chunk">{"".join(rows)}</table>')

    header = f'<div class="group-header">{_e(group.source)}</div>' if group.source else ""
    # A large identifier grid is block-level so it can flow across pages; small mixed
    # groups stay inline-block so several sit side by side (#132).
    group_cls = "group group-block" if id_only else "group"
    return f'<div class="{group_cls}">{header}{"".join(chunks)}</div>'


def _grouped_html(groups: list[LabelGroup], printed_at: str,
                  names: dict[str, str] | None = None,
                  borders: dict[str, str] | None = None,
                  backend: str = "weasyprint", editable: bool = False) -> str:
    body = "".join(_group_html(g, names, editable) for g in groups if g.specimens)
    stamp = f'<div class="printed-at">Printed: {_e(printed_at)}</div>'
    return (f"<html><head><style>{_grouped_css(borders, backend)}</style></head>"
            f'<body>{stamp}<div class="sheet">{body}</div></body></html>')


# ---------------------------------------------------------------------------
# WYSIWYG preview (Print-queue tab)
# ---------------------------------------------------------------------------
# The preview must show EXACTLY what prints, so it renders the SAME label markup +
# CSS as the PDF — not a separate approximation. It is embedded inline in the app
# page (not the print PDF's full document), so:
#   * the CSS is SCOPED under a container (.pq-sheet) — the label CSS carries a
#     global `* { margin:0; padding:0 }` reset and a bare `body {…}` rule that would
#     otherwise clobber the whole app; @page is dropped (meaningless on screen);
#   * the sheet is rendered editable=True so data/determination boxes are
#     contenteditable + tagged for the click-to-edit / hand-abbreviation flow.
# Zoom is applied by the UI as `transform: scale()` on the container — a POST-layout
# paint scale, so the labels lay out once at true physical size (wrapping == print)
# and only the pixels are magnified; lines can never re-break (measured: browsers
# agree on the layout to <0.5 mm, so what you see is what prints).
_PREVIEW_SCOPE = ".pq-sheet"


def preview_css(borders: dict[str, str] | None = None) -> str:
    """The label CSS, scoped for safe inline embedding in the app page."""
    # Browsers render the box-shadow hairline (the 'chromium' border variant), same as
    # the Chromium print backend — so preview and print match.
    css = _grouped_css(borders, "chromium")
    # Drop @page (no meaning on screen).
    css = _re.sub(r"@page\s*\{[^}]*\}", "", css)
    # Scope the two global rules that would leak into the whole app.
    css = css.replace("* { box-sizing: border-box; margin: 0; padding: 0; }",
                      f"{_PREVIEW_SCOPE} * {{ box-sizing: border-box; margin: 0; padding: 0; }}")
    css = css.replace("body {", f"{_PREVIEW_SCOPE} {{", 1)
    return css


def preview_html(groups: list[LabelGroup], printed_at: str,
                 names: dict[str, str] | None = None,
                 borders: dict[str, str] | None = None) -> str:
    """The queued sheet as an editable, scoped HTML fragment for the app page (no
    <html>/<head>; the CSS is injected once via `preview_css`). Layout is identical
    to the print PDF — same builder, editable=True."""
    body = "".join(_group_html(g, names, True) for g in groups if g.specimens)
    stamp = f'<div class="printed-at">Printed: {_e(printed_at)}</div>'
    return f'<div class="pq-sheet">{stamp}<div class="sheet">{body}</div></div>'


def grouped_sheet(groups: list[LabelGroup], printed_at: str,
                  names: dict[str, str] | None = None,
                  borders: dict[str, str] | None = None,
                  backend: str = "weasyprint") -> bytes:
    """Render queued labels as a grouped, column-aligned sheet (see module note).

    ``names`` maps collection code → full name for the identifier band (#56).
    ``borders`` maps ``"data"``/``"determination"``/``"identifier"`` → ``"black"``
    | ``"none"`` (AppConfig.label_border_*); default black.
    ``backend`` selects the renderer (``"weasyprint"`` | ``"chromium"``); the HTML
    is identical either way (see ``pdf_backend.render_pdf``)."""
    from app.services.pdf_backend import render_pdf
    return render_pdf(_grouped_html(groups, printed_at, names, borders, backend), backend)



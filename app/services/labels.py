"""Label PDF renderer.

Reproduces the original two-column ODT template format using Fira Sans Compressed
as a substitute for Context Condensed SSi.

Three label types — all 18 mm wide; heights are *minimums* that grow with text:

  data_sheet(rows)            18 × 2.5 mm min  — locality / date / collector
  determination_sheet(rows)   18 × 4.9 mm min  — taxon name + determiner
  identifier_sheet(codes)     18 × 7.0 mm min  — QR + collection name + big number

Labels tile with a small `_LABEL_GAP` between them (border-collapse: separate), so
each keeps its own complete border yet a single cut down the gap separates two
neighbours (no leftover strip, not two cuts). Each type's border ("black" cut-guide
line or "none") is a config choice — see AppConfig.label_border_* / _border_rule.

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
from typing import Optional

import qrcode
from weasyprint import HTML
from weasyprint.formatting_structure.boxes import LineBox as _LineBox

from app.services.label_text import abbreviate_name, format_coords, format_country
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
_LINE_H     = "1.41mm"   # 0.0555 in
_PAD        = "0.19mm 0.53mm"   # top/bottom  left/right
_TILE_PER_ROW = 10       # 18mm labels per row on A4 (10 × 18 = 180 mm < 200 mm)
# Small gap between neighbouring labels: enough to *separate* a black border (each
# label keeps its own complete rectangle), but small enough that a single cut down
# the middle parts them with no leftover strip — not two cuts (decided 2026-07-07).
_LABEL_GAP  = "0.6mm"


def _border_rule(choice: str) -> str:
    """CSS `border` shorthand for a per-label-type border config choice.

    ``"black"`` → a thin solid cut-guide line; anything else (``"none"``) → no
    border. Default is black everywhere (see AppConfig.label_border_*)."""
    return "0.15mm solid #000" if choice == "black" else "none"


def _tiled_sheet(inner_htmls: list[str], *, border: str,
                 cell_extra: str = "", extra_css: str = "") -> bytes:
    """Render inner-label HTMLs as a table with a small ``_LABEL_GAP`` between
    labels (``border-collapse: separate``): each label keeps its own complete
    border and neighbours are separated, yet the gap is small enough for one cut
    per edge with no leftover strip. Cells are a fixed 18 mm wide and wrap every
    ``_TILE_PER_ROW``. ``border`` picks the per-type border via ``_border_rule``;
    ``cell_extra`` adds a per-type ``.tcell`` rule (e.g. a min-height floor)."""
    rule = _border_rule(border)
    rows: list[str] = []
    for start in range(0, len(inner_htmls), _TILE_PER_ROW):
        chunk = inner_htmls[start:start + _TILE_PER_ROW]
        tds = "".join(f'<td class="tcell">{h}</td>' for h in chunk)
        rows.append(f"<tr>{tds}</tr>")
    css = f"""
    @page {{ size: A4; margin: 5mm; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: {_FONT}; font-size: {_FONT_SIZE}; line-height: {_LINE_H}; }}
    em {{ font-style: italic; }}
    .tsheet {{ border-collapse: separate; border-spacing: {_LABEL_GAP}; }}
    .tcell {{ width: {_W}; border: {rule}; padding: {_PAD};
              vertical-align: top; overflow: hidden; page-break-inside: avoid; }}
    {cell_extra}
    {extra_css}
    """
    html = (f"<html><head><meta charset='utf-8'><style>{css}</style></head>"
            f'<body><table class="tsheet">{"".join(rows)}</table></body></html>')
    return HTML(string=html).write_pdf()


# ---------------------------------------------------------------------------
# Shared base CSS
# ---------------------------------------------------------------------------

_BASE_CSS = f"""
@page {{ size: A4; margin: 5mm; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {_FONT}; font-size: {_FONT_SIZE}; line-height: {_LINE_H}; }}
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
#   Line 1:  Country: Region, Locality lat, lon, habitat
#   Line 2:  leg. Collector  Date

@dataclass
class DataLabel:
    country: Optional[str]                  = None
    country_code: Optional[str]             = None
    state_province: Optional[str]           = None
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
    return bool(_re.search(r"<\w+|</\w+|<br", text or ""))


def _override_html(text: str) -> str:
    """Render a print-only override. Formatted-HTML overrides are sanitized and
    kept (italics/bold survive); legacy plaintext is escaped one <div> per line."""
    if _looks_like_html(text):
        return sanitize_override_html(text)
    return "".join(f"<div>{_e(line)}</div>" for line in text.split("\n"))


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
    if lbl.text_override is not None:
        return _override_html(lbl.text_override)
    return "".join(f"<div>{t}</div>"
                   for t in [_data_line1(lbl), _data_line2(lbl)] if t)


def _data_line1(lbl: DataLabel) -> str:
    country_str = format_country(lbl.country, lbl.country_code, html=True)

    parts: list[str] = []

    for f in (lbl.state_province, lbl.municipality, lbl.verbatim_locality or lbl.locality):
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
    if country_str:
        return f"{country_str}: {body}" if body else country_str
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


def data_sheet(rows: list[DataLabel], *, border: str = "black") -> bytes:
    """PDF sheet of data/locality labels (18 × 2.5 mm min, grows with text)."""
    inners = [_data_inner_html(lbl) for lbl in rows]
    return _tiled_sheet(inners, border=border,
                        cell_extra=".tcell { min-height: 2.5mm; }")


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
    if lbl.text_override is not None:
        return _override_html(lbl.text_override)
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
_FIT_CSS = f"""
@page {{ size: 60mm 60mm; margin: 0; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {_FONT}; font-size: {_FONT_SIZE}; line-height: {_LINE_H}; }}
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


def determination_sheet(rows: list[DeterminationLabel], *, border: str = "black") -> bytes:
    """PDF sheet of determination labels (18 × 4.9 mm min, grows with text)."""
    inners = [_det_inner_html(lbl) for lbl in rows]
    return _tiled_sheet(inners, border=border,
                        cell_extra=".tcell { min-height: 4.9mm; overflow: visible; }")


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


# Shared CSS for the identifier label — included in both the batch sheet and the
# grouped cell. ``.id-label`` is the whole self-contained block and owns its min-height
# floor (no ``height:100%`` dependency on the container), so it renders the same
# regardless of what wraps it. QR left; a centred name / prefix / number stack right.
_ID_TEXT_CSS = """
.id-label {
    display: flex; flex-direction: row; align-items: center; gap: 0.6mm;
    min-height: 6.5mm; width: 100%;
    font-family: 'Fira Sans Compressed', 'Fira Sans Condensed', 'Arial Narrow', sans-serif;
    font-weight: 400;
}
.id-qr { width: 5.5mm; height: 5.5mm; flex-shrink: 0; image-rendering: pixelated; }
/* left-aligned so the name / prefix / number hug the QR instead of floating in the
   centre of the wide column. */
.id-text { flex: 1; min-width: 0; text-align: left; line-height: 1.05; overflow: hidden; }
/* All regular weight (not bold): at these micro sizes bold thickens/fills the digit
   counters on a real printer; regular stays cleaner (decided 2026-07-07). */
.id-collname {
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


def identifier_sheet(codes: list[str], names: dict[str, str] | None = None,
                     *, border: str = "black") -> bytes:
    """PDF sheet of identifier labels (18 × 7 mm min), tiled edge-to-edge.

    ``names`` maps a collection code (the code prefix) → its full name, printed in
    tiny letters above the code (see repositories.name_map). Unknown prefixes just
    omit the name line. ``border`` ∈ {"black", "none"} (AppConfig.label_border_identifier)."""
    inners = [_id_label_inner(c, _collection_of(c, names)) for c in codes]
    return _tiled_sheet(
        inners, border=border, extra_css=_ID_TEXT_CSS,
        cell_extra=".tcell { vertical-align: middle; padding: 0.2mm 0.5mm; }",
    )


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

def _grouped_css(borders: dict[str, str] | None = None) -> str:
    borders = borders or {}
    bd = _border_rule(borders.get("data", "black"))
    bt = _border_rule(borders.get("determination", "black"))
    bi = _border_rule(borders.get("identifier", "black"))
    return _BASE_CSS + _ID_TEXT_CSS + f"""
.printed-at {{ font-size: 5pt; color: #666; margin-bottom: 3mm; }}
.group {{ display: inline-block; vertical-align: top; line-height: {_LINE_H}; margin: 0 {_GROUP_GAP} {_GROUP_GAP} 0; }}
.group-header {{ font-size: 5pt; color: #666; margin-bottom: 0.4mm; letter-spacing: 0.2pt; }}
.chunk {{ table-layout: fixed; border-collapse: separate; border-spacing: {_LABEL_GAP}; page-break-inside: avoid; }}
.chunk + .chunk {{ margin-top: {_CHUNK_GAP}; }}
.cell {{ width: 18mm; padding: 0; vertical-align: top; }}
.lbl-data {{
    min-height: 2.5mm;
    border: {bd}; padding: 0.19mm 0.53mm; overflow: hidden;
    font-size: {_FONT_SIZE};
}}
.lbl-det {{
    min-height: 4.9mm;
    border: {bt}; padding: 0.19mm 0.53mm; overflow: visible;
    font-size: {_FONT_SIZE};
}}
.lbl-id {{
    min-height: 6.5mm;
    border: {bi}; padding: 0.2mm 0.5mm;
    overflow: hidden;
}}
"""


def _data_cell(d: Optional[DataLabel]) -> str:
    if d is None:
        return '<td class="cell"></td>'
    return f'<td class="cell"><div class="lbl-data">{_data_inner_html(d)}</div></td>'


def _det_cell(d: Optional[DeterminationLabel]) -> str:
    if d is None:
        return '<td class="cell"></td>'
    return f'<td class="cell"><div class="lbl-det">{_det_inner_html(d)}</div></td>'


def _id_cell(code: Optional[str], names: dict[str, str] | None = None) -> str:
    if not code:
        return '<td class="cell"></td>'
    inner = _id_label_inner(code, _collection_of(code, names))
    return f'<td class="cell"><div class="lbl-id">{inner}</div></td>'


def _group_html(group: LabelGroup, names: dict[str, str] | None = None) -> str:
    specs = group.specimens
    if not specs:
        return ""
    has_data = any(s.data is not None for s in specs)
    has_id   = any(s.id_code for s in specs)
    has_det  = any(s.determination is not None for s in specs)

    chunks: list[str] = []
    for start in range(0, len(specs), _LABELS_PER_ROW):
        chunk = specs[start:start + _LABELS_PER_ROW]
        rows: list[str] = []
        # Band order top→bottom: data, identifier, determination. Each present
        # band is a table row with one cell per column (empty <td> if missing)
        # so columns stay aligned across bands.
        if has_data:
            rows.append("<tr>" + "".join(_data_cell(s.data) for s in chunk) + "</tr>")
        if has_id:
            rows.append("<tr>" + "".join(_id_cell(s.id_code, names) for s in chunk) + "</tr>")
        if has_det:
            rows.append("<tr>" + "".join(_det_cell(s.determination) for s in chunk) + "</tr>")
        chunks.append(f'<table class="chunk">{"".join(rows)}</table>')

    header = f'<div class="group-header">{_e(group.source)}</div>' if group.source else ""
    return f'<div class="group">{header}{"".join(chunks)}</div>'


def _grouped_html(groups: list[LabelGroup], printed_at: str,
                  names: dict[str, str] | None = None,
                  borders: dict[str, str] | None = None) -> str:
    body = "".join(_group_html(g, names) for g in groups if g.specimens)
    stamp = f'<div class="printed-at">Printed: {_e(printed_at)}</div>'
    return (f"<html><head><style>{_grouped_css(borders)}</style></head>"
            f'<body>{stamp}<div class="sheet">{body}</div></body></html>')


def grouped_sheet(groups: list[LabelGroup], printed_at: str,
                  names: dict[str, str] | None = None,
                  borders: dict[str, str] | None = None) -> bytes:
    """Render queued labels as a grouped, column-aligned sheet (see module note).

    ``names`` maps collection code → full name for the identifier band (#56).
    ``borders`` maps ``"data"``/``"determination"``/``"identifier"`` → ``"black"``
    | ``"none"`` (AppConfig.label_border_*); default black."""
    return HTML(string=_grouped_html(groups, printed_at, names, borders)).write_pdf()



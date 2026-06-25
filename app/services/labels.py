"""Label PDF renderer.

Reproduces the original two-column ODT template format using Fira Sans Compressed
as a substitute for Context Condensed SSi.

Three label types — all ≤ 18 mm wide:

  data_sheet(rows)            18 × 2.5 mm   — locality / date / collector
  determination_sheet(rows)   18 × 4.9 mm   — taxon name + determiner
  identifier_sheet(codes)     18 × 5.5 mm   — QR code + 4-char identifier

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
    # ("Jakob Jilg" -> "J. Jilg"). Same rule as the determiner (_det_line3).
    if not lbl.recorded_by or _fits_one_line(full):
        return full
    return _build(abbreviate_name(lbl.recorded_by))


_DATA_CSS = _BASE_CSS + ".label { min-height: 2.5mm; }"


def data_sheet(rows: list[DataLabel]) -> bytes:
    """PDF sheet of data/locality labels (18 × 2.5 mm)."""
    items = []
    for lbl in rows:
        items.append(f'<div class="label">{_data_inner_html(lbl)}</div>')
    html = (f"<html><head><style>{_DATA_CSS}</style></head>"
            f'<body><div class="sheet">{"".join(items)}</div></body></html>')
    return HTML(string=html).write_pdf()


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
    # abbreviate it ("Jakob Jilg" -> "J. Jilg").
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


# min-height keeps the historical 4.9 mm floor; overflow:visible lets a long name
# wrap and grow the label instead of being clipped (overriding _BASE_CSS).
_DET_CSS = _BASE_CSS + ".label { min-height: 4.9mm; overflow: visible; }"

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


def determination_sheet(rows: list[DeterminationLabel]) -> bytes:
    """PDF sheet of determination labels (18 × 4.9 mm)."""
    items = []
    for lbl in rows:
        items.append(f'<div class="label">{_det_inner_html(lbl)}</div>')
    html = (f"<html><head><style>{_DET_CSS}</style></head>"
            f'<body><div class="sheet">{"".join(items)}</div></body></html>')
    return HTML(string=html).write_pdf()


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


def _id_label_inner(code: str) -> str:
    """Inner HTML for one identifier label: QR image + two-line code text."""
    prefix, number = _split_identifier_code(code)
    qr = _qr_data_url(code)
    prefix_html = f'<div class="id-prefix">{_e(prefix)}</div>' if prefix else ""
    return (
        f'<img src="{qr}">'
        f'<div class="id-text">'
        f'{prefix_html}'
        f'<div class="id-number">{_e(number)}</div>'
        f'</div>'
    )


# Shared CSS for the two-line code column — included in every identifier-label CSS block.
_ID_TEXT_CSS = """
.id-text {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: 'Fira Code', 'DejaVu Sans Mono', monospace;
    font-weight: bold;
    text-align: center;
    line-height: 1.3;
}
.id-prefix { font-size: 4pt;  letter-spacing: 0.3pt; }
.id-number  { font-size: 8pt;  letter-spacing: 0.5pt; }
"""

_ID_CSS = _BASE_CSS + _ID_TEXT_CSS + """
.label {
    height: 5.5mm;
    display: flex;
    align-items: center;
    gap: 0.8mm;
    padding: 0.3mm 0.5mm;
}
.label img { width: 4.5mm; height: 4.5mm; flex-shrink: 0; image-rendering: pixelated; }
"""


def identifier_sheet(codes: list[str]) -> bytes:
    """PDF sheet of identifier-only labels (18 × 5.5 mm)."""
    items = [
        f'<div class="label">{_id_label_inner(c)}</div>'
        for c in codes
    ]
    html = (f"<html><head><style>{_ID_CSS}</style></head>"
            f'<body><div class="sheet">{"".join(items)}</div></body></html>')
    return HTML(string=html).write_pdf()


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
# identifier / determination). `border-spacing` gives the small inter-specimen
# gap with zero vertical gap, so a specimen's column stays together for cutting.
_GROUPED_CSS = _BASE_CSS + _ID_TEXT_CSS + f"""
.printed-at {{ font-size: 5pt; color: #666; margin-bottom: 3mm; }}
.group {{ display: inline-block; vertical-align: top; line-height: {_LINE_H}; margin: 0 {_GROUP_GAP} {_GROUP_GAP} 0; }}
.group-header {{ font-size: 5pt; color: #666; margin-bottom: 0.4mm; letter-spacing: 0.2pt; }}
.chunk {{ table-layout: fixed; border-collapse: separate; border-spacing: {_SPEC_GAP} 0; page-break-inside: avoid; }}
.chunk + .chunk {{ margin-top: {_CHUNK_GAP}; }}
.cell {{ width: 18mm; padding: 0; vertical-align: top; }}
.lbl-data {{
    min-height: 2.5mm;
    border: 0.1mm dashed #aaa; padding: 0.19mm 0.53mm; overflow: hidden;
    font-size: {_FONT_SIZE};
}}
.lbl-det {{
    min-height: 4.9mm;
    border: 0.1mm dashed #aaa; padding: 0.19mm 0.53mm; overflow: visible;
    font-size: {_FONT_SIZE};
}}
.lbl-id {{
    height: 5.5mm;
    border: 0.1mm dashed #aaa; padding: 0.3mm 0.5mm;
    display: flex; align-items: center; gap: 0.8mm;
}}
.lbl-id img {{ width: 4.5mm; height: 4.5mm; flex-shrink: 0; image-rendering: pixelated; }}
"""


def _data_cell(d: Optional[DataLabel]) -> str:
    if d is None:
        return '<td class="cell"></td>'
    return f'<td class="cell"><div class="lbl-data">{_data_inner_html(d)}</div></td>'


def _det_cell(d: Optional[DeterminationLabel]) -> str:
    if d is None:
        return '<td class="cell"></td>'
    return f'<td class="cell"><div class="lbl-det">{_det_inner_html(d)}</div></td>'


def _id_cell(code: Optional[str]) -> str:
    if not code:
        return '<td class="cell"></td>'
    return f'<td class="cell"><div class="lbl-id">{_id_label_inner(code)}</div></td>'


def _group_html(group: LabelGroup) -> str:
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
            rows.append("<tr>" + "".join(_id_cell(s.id_code) for s in chunk) + "</tr>")
        if has_det:
            rows.append("<tr>" + "".join(_det_cell(s.determination) for s in chunk) + "</tr>")
        chunks.append(f'<table class="chunk">{"".join(rows)}</table>')

    header = f'<div class="group-header">{_e(group.source)}</div>' if group.source else ""
    return f'<div class="group">{header}{"".join(chunks)}</div>'


def _grouped_html(groups: list[LabelGroup], printed_at: str) -> str:
    body = "".join(_group_html(g) for g in groups if g.specimens)
    stamp = f'<div class="printed-at">Printed: {_e(printed_at)}</div>'
    return (f"<html><head><style>{_GROUPED_CSS}</style></head>"
            f'<body>{stamp}<div class="sheet">{body}</div></body></html>')


def grouped_sheet(groups: list[LabelGroup], printed_at: str) -> bytes:
    """Render queued labels as a grouped, column-aligned sheet (see module note)."""
    return HTML(string=_grouped_html(groups, printed_at)).write_pdf()



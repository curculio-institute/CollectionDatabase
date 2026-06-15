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
from dataclasses import dataclass
from typing import Optional

import qrcode
from weasyprint import HTML

from app.services.label_text import abbreviate_name, format_coords, format_country


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
.sheet {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.3mm;
    align-content: flex-start;
}}
.label {{
    width: {_W};
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
    # When set, bypasses computed formatting and renders this plain text directly.
    text_override: Optional[str]            = None


def _data_line1(lbl: DataLabel) -> str:
    if lbl.text_override is not None:
        return _e(lbl.text_override)

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
    if lbl.text_override is not None:
        return ""
    name = abbreviate_name(lbl.recorded_by)
    leg  = f"leg. {_e(name)}" if name else ""
    date = _e(lbl.event_date) if lbl.event_date else ""
    return "  ".join(p for p in [leg, date] if p)


_DATA_CSS = _BASE_CSS + ".label { min-height: 2.5mm; }"


def data_sheet(rows: list[DataLabel]) -> bytes:
    """PDF sheet of data/locality labels (18 × 2.5 mm)."""
    items = []
    for lbl in rows:
        l1 = _data_line1(lbl)
        l2 = _data_line2(lbl)
        lines = "".join(f"<div>{t}</div>" for t in [l1, l2] if t)
        items.append(f'<div class="label">{lines}</div>')
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

_SEX_SYMBOL: dict[str, str] = {"male": "♂", "female": "♀"}


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
        sym = _SEX_SYMBOL.get(lbl.sex.lower())
        if sym:
            parts.append(sym)
    return " ".join(parts)


def _det_line3(lbl: DeterminationLabel) -> str:
    det  = f"det. {_e(lbl.determiner)}" if lbl.determiner else ""
    year = _e(lbl.year) if lbl.year else ""
    return "  ".join(p for p in [det, year] if p)


_DET_CSS = _BASE_CSS + ".label { height: 4.9mm; }"


def determination_sheet(rows: list[DeterminationLabel]) -> bytes:
    """PDF sheet of determination labels (18 × 4.9 mm)."""
    items = []
    for lbl in rows:
        l1 = _det_line1(lbl)
        l2 = _det_line2(lbl)
        l3 = _det_line3(lbl)
        lines = "".join(f"<div>{t}</div>" for t in [l1, l2, l3] if t)
        items.append(f'<div class="label">{lines}</div>')
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
.group {{ display: inline-block; vertical-align: top; margin: 0 {_GROUP_GAP} {_GROUP_GAP} 0; }}
.group-header {{ font-size: 5pt; color: #666; margin-bottom: 0.4mm; letter-spacing: 0.2pt; }}
.chunk {{ table-layout: fixed; border-collapse: separate; border-spacing: {_SPEC_GAP} 0; }}
.chunk + .chunk {{ margin-top: {_CHUNK_GAP}; }}
.cell {{ width: 18mm; padding: 0; vertical-align: top; }}
.lbl-data {{
    min-height: 2.5mm;
    border: 0.1mm dashed #aaa; padding: 0.19mm 0.53mm; overflow: hidden;
    font-size: {_FONT_SIZE};
}}
.lbl-det {{
    height: 4.9mm;
    border: 0.1mm dashed #aaa; padding: 0.19mm 0.53mm; overflow: hidden;
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
    lines = "".join(f"<div>{t}</div>" for t in [_data_line1(d), _data_line2(d)] if t)
    return f'<td class="cell"><div class="lbl-data">{lines}</div></td>'


def _det_cell(d: Optional[DeterminationLabel]) -> str:
    if d is None:
        return '<td class="cell"></td>'
    lines = "".join(f"<div>{t}</div>" for t in [_det_line1(d), _det_line2(d), _det_line3(d)] if t)
    return f'<td class="cell"><div class="lbl-det">{lines}</div></td>'


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


def grouped_sheet(groups: list[LabelGroup], printed_at: str) -> bytes:
    """Render queued labels as a grouped, column-aligned sheet (see module note)."""
    body = "".join(_group_html(g) for g in groups if g.specimens)
    stamp = f'<div class="printed-at">Printed: {_e(printed_at)}</div>'
    html = (f"<html><head><style>{_GROUPED_CSS}</style></head>"
            f'<body>{stamp}<div class="sheet">{body}</div></body></html>')
    return HTML(string=html).write_pdf()



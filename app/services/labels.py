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

from app.services.label_text import abbreviate_name


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


_COUNTRY_THRESHOLD = 10  # chars; longer names use the 2-letter code


def _data_line1(lbl: DataLabel) -> str:
    if lbl.text_override is not None:
        return _e(lbl.text_override)

    country = lbl.country or ""
    code = lbl.country_code or ""
    if country and len(country) > _COUNTRY_THRESHOLD and code:
        country_str = _e(code)
    else:
        country_str = _e(country)

    parts: list[str] = []

    for f in (lbl.state_province, lbl.municipality, lbl.verbatim_locality or lbl.locality):
        if f:
            parts.append(_e(f))

    if lbl.latitude is not None and lbl.longitude is not None:
        coords = f"{lbl.latitude:.4f}, {lbl.longitude:.4f}"
        if lbl.coordinate_uncertainty_m is not None:
            u = lbl.coordinate_uncertainty_m
            coords += f" ±{round(u)}m" if u < 1000 else f" ±{u / 1000:.1f}km"
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
# Combined sheet — print queue output
# ---------------------------------------------------------------------------

_COMBINED_CSS = _BASE_CSS + _ID_TEXT_CSS + """
/* data labels */
.lbl-data {
    width: 18mm; min-height: 2.5mm;
    border: 0.1mm dashed #aaa;
    padding: 0.19mm 0.53mm;
    overflow: hidden;
}
/* determination labels */
.lbl-det {
    width: 18mm; height: 4.9mm;
    border: 0.1mm dashed #aaa;
    padding: 0.19mm 0.53mm;
    overflow: hidden;
}
/* identifier labels */
.lbl-id {
    width: 18mm; height: 5.5mm;
    border: 0.1mm dashed #aaa;
    padding: 0.3mm 0.5mm;
    display: flex; align-items: center; gap: 0.8mm;
}
.lbl-id img { width: 4.5mm; height: 4.5mm; flex-shrink: 0; image-rendering: pixelated; }
/* section break: forces subsequent labels to a new flex row */
.section-break { flex: 0 0 100%; height: 2mm; }
"""


def combined_sheet(
    data_rows: list[DataLabel],
    det_rows:  list[DeterminationLabel],
    id_codes:  list[str],
) -> bytes:
    """Single PDF with all three label types, each group on its own run of the sheet."""
    items: list[str] = []

    for lbl_data in data_rows:
        l1, l2 = _data_line1(lbl_data), _data_line2(lbl_data)
        lines = "".join(f"<div>{t}</div>" for t in [l1, l2] if t)
        items.append(f'<div class="lbl-data">{lines}</div>')

    if data_rows and (det_rows or id_codes):
        items.append('<div class="section-break"></div>')

    for lbl_det in det_rows:
        l1, l2, l3 = _det_line1(lbl_det), _det_line2(lbl_det), _det_line3(lbl_det)
        lines = "".join(f"<div>{t}</div>" for t in [l1, l2, l3] if t)
        items.append(f'<div class="lbl-det">{lines}</div>')

    if det_rows and id_codes:
        items.append('<div class="section-break"></div>')

    for code in id_codes:
        items.append(f'<div class="lbl-id">{_id_label_inner(code)}</div>')

    html = (f"<html><head><style>{_COMBINED_CSS}</style></head>"
            f'<body><div class="sheet">{"".join(items)}</div></body></html>')
    return HTML(string=html).write_pdf()


# ---------------------------------------------------------------------------
# Occurrence labels — convenience wrapper
# ---------------------------------------------------------------------------
# Kept for the UI: generates a data label + identifier label pair per specimen.

@dataclass
class OccurrenceLabel:
    code: str
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
    taxon: Optional[str]                    = None
    associated_species: Optional[list[str]] = None


def occurrence_sheet(rows: list[OccurrenceLabel]) -> bytes:
    """Data label + identifier label for each specimen, interleaved on one sheet."""
    data_rows = [DataLabel(
        country=r.country, country_code=r.country_code,
        state_province=r.state_province, municipality=r.municipality,
        county=r.county,
        locality=r.locality, verbatim_locality=r.verbatim_locality,
        latitude=r.latitude, longitude=r.longitude,
        coordinate_uncertainty_m=r.coordinate_uncertainty_m,
        elevation_min=r.elevation_min, elevation_max=r.elevation_max,
        event_date=r.event_date, recorded_by=r.recorded_by, habitat=r.habitat,
        associated_species=r.associated_species,
    ) for r in rows]

    # Build interleaved HTML: data label then identifier label per specimen,
    # grouped in pairs so they print side by side and are easy to associate.
    data_items = []
    for lbl in data_rows:
        l1, l2 = _data_line1(lbl), _data_line2(lbl)
        lines = "".join(f"<div>{t}</div>" for t in [l1, l2] if t)
        data_items.append(f'<div class="data-label">{lines}</div>')

    id_items = [
        f'<div class="id-label">{_id_label_inner(r.code)}</div>'
        for r in rows
    ]

    css = _BASE_CSS + _ID_TEXT_CSS + """
.sheet { display: flex; flex-wrap: wrap; gap: 0.3mm; align-content: flex-start; }
.data-label {
    width: 18mm; min-height: 2.5mm;
    border: 0.1mm dashed #aaa;
    padding: 0.19mm 0.53mm;
    overflow: hidden;
}
.id-label {
    width: 18mm; height: 5.5mm;
    border: 0.1mm dashed #aaa;
    padding: 0.3mm 0.5mm;
    display: flex; align-items: center; gap: 0.8mm;
}
.id-label img { width: 4.5mm; height: 4.5mm; flex-shrink: 0; image-rendering: pixelated; }
"""
    # Print: all data labels first, then all identifier labels
    html = (f"<html><head><style>{css}</style></head>"
            f'<body><div class="sheet">'
            f'{"".join(data_items)}'
            f'{"".join(id_items)}'
            f'</div></body></html>')
    return HTML(string=html).write_pdf()

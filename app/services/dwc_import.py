"""DwC CSV parser for the Import & Assign workflow.

The spreadsheet lives only in per-connection session memory — nothing is
written to the DB by this service.  The calling UI resolves taxa, fills
per-specimen fields, validates, and calls the normal save_specimen_entry path.
"""
from __future__ import annotations

import csv
import io
import re


# ---------------------------------------------------------------------------
# Column-name normalisation
# ---------------------------------------------------------------------------
# The importer expects valid Darwin Core: every column header is a DwC term.
# We deliberately do NOT map informal synonyms ("leg", "lat", "species", …) nor
# remap one DwC term onto a different column — standardising header names is
# precisely what Darwin Core is for, and a value that feeds a given column must
# arrive under that column's own DwC term. The only normalisation is casing and
# separators, handled by _norm_key ("ScientificName", "scientific name" and
# "SCIENTIFICNAME" all resolve to "scientificName"). _ALIASES is therefore
# *derived* from the supported-term list: exactly one entry per term, no
# hand-maintained spelling variants (which silently go dead once _norm_key runs).

def _norm_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", raw.lower())


# Darwin Core terms the Import & Assign workflow reads (row_to_* below, the
# prefill resolver, and _SEARCH_FIELDS). Add a term here only when code reads it.
_DWC_TERMS: tuple[str, ...] = (
    # Taxon / identification
    "scientificName", "scientificNameAuthorship", "acceptedNameUsage",
    "genus", "specificEpithet", "identifiedBy", "dateIdentified",
    "identificationQualifier", "identificationRemarks",
    # Collecting event
    "eventDate", "verbatimEventDate", "recordedBy",
    "country", "countryCode", "stateProvince", "county", "municipality",
    "island", "locality", "verbatimLocality",
    "decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters",
    "minimumElevationInMeters", "maximumElevationInMeters",
    "habitat", "samplingProtocol", "fieldNumber",
    # Specimen
    "sex", "individualCount", "preparations", "lifeStage", "typeStatus",
    "materialEntityRemarks",
    # Identifiers / provenance
    "occurrenceID", "catalogNumber", "verbatimLabel",
)

# normalised header → canonical DwC term (casing restored).
_ALIASES: dict[str, str] = {_norm_key(t): t for t in _DWC_TERMS}

_SEARCH_FIELDS = [
    "scientificName", "genus", "specificEpithet",
    "eventDate", "verbatimEventDate",
    "country", "stateProvince", "county", "locality", "verbatimLocality",
    "recordedBy", "identifiedBy",
    "occurrenceID", "catalogNumber",
]


def _normalise_row(raw: dict[str, str]) -> dict[str, str]:
    return {_ALIASES.get(_norm_key(k), k): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_csv(content: bytes | str) -> list[dict[str, str]]:
    """Parse a DwC CSV (UTF-8-sig or latin-1) and return normalised rows.

    Empty rows are dropped.  Column names are normalised to canonical DwC
    camelCase (or left as-is if unrecognised).
    """
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
    else:
        text = content

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        # A row with more values than headers lands its surplus under
        # csv.DictReader's restkey (None). That almost always means an
        # unescaped comma inside a field, which shifts every column after it —
        # the whole row is misaligned and its values can no longer be trusted.
        # Refuse the import loudly, naming the row, rather than crash opaquely
        # in _norm_key(None) (#68) or silently keep shifted data (never skip
        # silently, #62). Trailing empty delimiters (harmless) are dropped.
        surplus = raw.pop(None, None)
        if surplus:
            extra = [v for v in surplus if (v or "").strip()]
            if extra:
                raise ValueError(
                    f"Row at line {reader.line_num} has more values than there "
                    f"are columns (surplus: {extra}). This usually means an "
                    f"unescaped comma inside a field — quote that field (or fix "
                    f"the row) and re-upload. No rows were imported."
                )
        norm = _normalise_row(raw)
        if any(v for v in norm.values()):
            rows.append(norm)
    return rows


def search_rows(rows: list[dict], term: str) -> list[dict]:
    """Return rows where any search field contains `term` (case-insensitive).

    Returns the first 100 rows when term is empty.
    """
    if not term.strip():
        return rows[:100]
    t = term.strip().lower()
    return [r for r in rows if any(t in (r.get(f) or "").lower() for f in _SEARCH_FIELDS)]


def row_summary(row: dict) -> str:
    """One-line display string for the search results list."""
    parts = []
    name = (row.get("scientificName") or "").strip()
    if name:
        parts.append(name)
    loc = (row.get("locality") or row.get("verbatimLocality")
           or row.get("stateProvince") or row.get("country") or "")
    if loc:
        parts.append(loc)
    d = row.get("eventDate") or row.get("verbatimEventDate") or ""
    if d:
        parts.append(d)
    leg = row.get("recordedBy") or ""
    if leg:
        parts.append(f"leg. {leg}")
    return "  |  ".join(parts) if parts else "(empty row)"


def row_scientific_name(row: dict) -> str:
    return (row.get("scientificName") or "").strip()


def normalise_row_dates(row: dict) -> tuple[dict, str | None]:
    """Parse a row's eventDate + dateIdentified to ISO 8601, preserving the raw (#1).

    Returns ``(overrides, error)``. On success *overrides* carries ISO ``event_date`` /
    ``date_identified`` (and ``verbatim_event_date`` when parsing reformatted the eventDate
    and the row gave no verbatim of its own) to apply over ``row_to_event_fields`` /
    ``row_to_determination_fields``. On a parse failure it returns ``({}, message)`` so the
    caller **refuses the row** — a spreadsheet's ``15.07.2005`` must never land verbatim in
    ``dwc:eventDate`` (CLAUDE.md §2). The raw string is kept in verbatimEventDate so a
    European-order misread (DD.MM) is always auditable.
    """
    from app.services.dates import parse_dwc_date

    raw_ed = (row.get("eventDate") or "").strip()
    iso_ed, err = parse_dwc_date(raw_ed, allow_interval=True)
    if err:
        return ({}, f"eventDate {raw_ed!r}: {err}")

    raw_di = (row.get("dateIdentified") or "").strip()
    iso_di, err = parse_dwc_date(raw_di, allow_interval=True, no_future=True)
    if err:
        return ({}, f"dateIdentified {raw_di!r}: {err}")

    overrides = {"event_date": iso_ed, "date_identified": iso_di}
    if raw_ed and raw_ed != iso_ed and not (row.get("verbatimEventDate") or "").strip():
        overrides["verbatim_event_date"] = raw_ed
    return (overrides, None)


def row_to_event_fields(row: dict) -> dict:
    return {
        "country":                          row.get("country") or "",
        # Not an event column (0057): resolves the country vocab row by (name, code).
        "country_iso":                      row.get("countryCode") or "",
        "state_province":                   row.get("stateProvince") or "",
        "county":                           row.get("county") or "",
        "municipality":                     row.get("municipality") or "",
        "island":                           row.get("island") or "",
        "locality":                         row.get("locality") or "",
        "verbatim_locality":                row.get("verbatimLocality") or "",
        "event_date":                       row.get("eventDate") or "",
        "verbatim_event_date":              row.get("verbatimEventDate") or "",
        "recorded_by":                      row.get("recordedBy") or "",
        "decimal_latitude":                 row.get("decimalLatitude") or "",
        "decimal_longitude":                row.get("decimalLongitude") or "",
        "coordinate_uncertainty_in_meters": row.get("coordinateUncertaintyInMeters") or "",
        "minimum_elevation_in_meters":      row.get("minimumElevationInMeters") or "",
        "maximum_elevation_in_meters":      row.get("maximumElevationInMeters") or "",
        "habitat":                          row.get("habitat") or "",
        "sampling_protocol":                row.get("samplingProtocol") or "",
        "field_number":                     row.get("fieldNumber") or "",
        "verbatim_label":                   row.get("verbatimLabel") or "",
    }


def row_to_determination_fields(row: dict) -> dict:
    return {
        "sex":             row.get("sex") or "",
        "type_status":     row.get("typeStatus") or "",
        "identified_by":   row.get("identifiedBy") or "",
        "date_identified": row.get("dateIdentified") or "",
        "identification_qualifier": (row.get("identificationQualifier") or "").strip(),
        "identification_remarks":   (row.get("identificationRemarks") or "").strip(),
        "verbatim_identification": row_scientific_name(row),
    }


def row_to_specimen_prefill(row: dict) -> dict:
    """Fields from the spreadsheet that can pre-fill the per-specimen inputs."""
    return {
        "individual_count":  row.get("individualCount") or "1",
        "preparations":      row.get("preparations") or "",
        "life_stage":        row.get("lifeStage") or "",
        "occurrence_remarks": row.get("materialEntityRemarks") or "",
    }

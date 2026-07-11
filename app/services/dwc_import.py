"""DwC CSV parser for the Import & Assign workflow.

The spreadsheet lives only in per-connection session memory — nothing is
written to the DB by this service.  The calling UI resolves taxa, fills
per-specimen fields, validates, and calls the normal save_specimen_entry path.
"""
from __future__ import annotations

import csv
import io
import re

from app.vocab import IDENTIFICATION_QUALIFIERS


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
    # Biological association (host plant etc. — the specimen is the subject,
    # this the object; #6). associatedOrganisms is the term the staged data uses;
    # associatedTaxa is a distinct DwC term and is NOT aliased onto it.
    "associatedOrganisms",
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


def row_host_name(row: dict) -> str:
    """The associated-organism (host plant) name for this row, or "" (#6).

    A single scientific name (possibly open-nomenclature, e.g. ``Silene cf. otites``
    or a bare genus ``Salix sp.``). The specimen is the association subject; this is
    the object. One host per row. Returned verbatim — the raw is what a
    "not attached" warning shows.
    """
    return (row.get("associatedOrganisms") or "").strip()


# Open-nomenclature / rank tokens that qualify a host name but are not part of it, so they
# must be dropped before the name is used as a taxon-search query (real host data carries
# "Betula sp.", "Silene cf. otites", bare genera like "Salix").
#
# Each token maps to its CANONICAL form — which is deliberately one of the values the DB's
# CHECK on dwc:identificationQualifier already allows (vocab.IDENTIFICATION_QUALIFIERS). The
# qualifier is not noise to be discarded: "Betula sp." asserts the species is undetermined,
# and dropping it would record the host as a flat determination of *Betula*, a claim the data
# never made. So the same table both strips the token from the search query AND recovers it as
# the association's qualifier (§2: never silently drop input).
_HOST_QUALIFIER_CANONICAL: dict[str, str] = {
    "cf": "cf.",      "cf.": "cf.",
    "aff": "aff.",    "aff.": "aff.",
    "nr": "nr.",      "nr.": "nr.",
    "sp": "sp.",      "sp.": "sp.",
    "spp": "spp.",    "spp.": "spp.",
    "gr": "gr.",      "gr.": "gr.",
    "agg": "agg.",    "agg.": "agg.",
    "indet": "indet.", "indet.": "indet.",
    "?": "?",
}
_HOST_QUALIFIER_TOKENS = frozenset(_HOST_QUALIFIER_CANONICAL)

# Guard: a canonical form that the DB would reject is a bug, not a runtime surprise.
assert set(_HOST_QUALIFIER_CANONICAL.values()) <= set(IDENTIFICATION_QUALIFIERS)


def host_search_query(name: str) -> str:
    """Strip open-nomenclature qualifiers from a host name so it can seed a
    taxon search (#6). ``Betula sp.`` → ``Betula``; ``Silene cf. otites`` →
    ``Silene otites``; a bare genus is returned unchanged. The user still
    confirms the actual taxon from the search results."""
    kept = [t for t in (name or "").split()
            if t.lower() not in _HOST_QUALIFIER_TOKENS]
    return " ".join(kept)


def host_qualifier(name: str) -> str | None:
    """The open-nomenclature qualifier carried by a host name, canonicalised to the
    identificationQualifier vocabulary — the other half of host_search_query().

    ``Betula sp.`` → ``sp.``; ``Silene cf. otites`` → ``cf.``; ``Salix`` → ``None``.
    The first qualifying token wins (a name carrying two is malformed, not meaningful).
    It seeds the import's qualifier field, which the user still confirms before saving.
    """
    for tok in (name or "").split():
        canonical = _HOST_QUALIFIER_CANONICAL.get(tok.lower())
        if canonical:
            return canonical
    return None


def parse_individual_count(raw) -> tuple[int, str | None]:
    """Parse a DwC individualCount cell defensively.

    Returns ``(count, warning)``. The standard value is **1**: an empty/missing
    cell, or a value that is not a whole number (e.g. ``"F"``), falls back to 1 —
    but a non-empty unparseable value also yields a ``warning`` string so the
    caller can surface it rather than coerce it silently (§2). A deliberate,
    valid ``0`` is preserved (the DB CHECK allows ``>= 0``); a negative value is
    treated as unparseable (the DB would reject it) and falls back to 1 + warning.
    """
    if raw is None:
        return 1, None
    s = str(raw).strip()
    if not s:
        return 1, None
    try:
        n = int(s)
    except ValueError:
        return 1, f"individualCount {s!r} is not a whole number — defaulted to 1."
    if n < 0:
        return 1, f"individualCount {s!r} is negative — defaulted to 1."
    return n, None


def row_to_specimen_prefill(row: dict) -> dict:
    """Fields from the spreadsheet that can pre-fill the per-specimen inputs."""
    return {
        "individual_count":  row.get("individualCount") or "1",
        "preparations":      row.get("preparations") or "",
        "life_stage":        row.get("lifeStage") or "",
        "occurrence_remarks": row.get("materialEntityRemarks") or "",
    }

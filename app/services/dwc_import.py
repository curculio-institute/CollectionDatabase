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
# Maps lowercased / stripped variants → canonical DwC camelCase key.

_ALIASES: dict[str, str] = {
    # Taxon / identification
    "scientificname":                   "scientificName",
    "scientific_name":                  "scientificName",
    "taxon":                            "scientificName",
    "species":                          "scientificName",
    "genus":                            "genus",
    "specificepithet":                  "specificEpithet",
    "specific_epithet":                 "specificEpithet",
    "infraspecificepithet":             "infraspecificEpithet",
    "infraspecific_epithet":            "infraspecificEpithet",
    "scientificnameauthorship":         "scientificNameAuthorship",
    "authorship":                       "scientificNameAuthorship",
    "author":                           "scientificNameAuthorship",
    "taxonrank":                        "taxonRank",
    "rank":                             "taxonRank",
    "identifiedby":                     "identifiedBy",
    "identified_by":                    "identifiedBy",
    "det":                              "identifiedBy",
    "dateidentified":                   "dateIdentified",
    "date_identified":                  "dateIdentified",
    # Collecting event
    "eventdate":                        "eventDate",
    "event_date":                       "eventDate",
    "date":                             "eventDate",
    "verbatimeventdate":                "verbatimEventDate",
    "verbatim_event_date":              "verbatimEventDate",
    "recordedby":                       "recordedBy",
    "recorded_by":                      "recordedBy",
    "leg":                              "recordedBy",
    "collector":                        "recordedBy",
    "country":                          "country",
    "countrycode":                      "countryCode",
    "country_code":                     "countryCode",
    "stateprovince":                    "stateProvince",
    "state_province":                   "stateProvince",
    "state":                            "stateProvince",
    "province":                         "stateProvince",
    "county":                           "county",
    "municipality":                     "municipality",
    "locality":                         "locality",
    "verbatimlocality":                 "verbatimLocality",
    "verbatim_locality":                "verbatimLocality",
    "decimallatitude":                  "decimalLatitude",
    "decimal_latitude":                 "decimalLatitude",
    "latitude":                         "decimalLatitude",
    "lat":                              "decimalLatitude",
    "decimallongitude":                 "decimalLongitude",
    "decimal_longitude":                "decimalLongitude",
    "longitude":                        "decimalLongitude",
    "lon":                              "decimalLongitude",
    "lng":                              "decimalLongitude",
    "coordinateuncertaintyinmeters":    "coordinateUncertaintyInMeters",
    "coordinate_uncertainty_in_meters": "coordinateUncertaintyInMeters",
    "minimumelevationinmeters":         "minimumElevationInMeters",
    "minimum_elevation_in_meters":      "minimumElevationInMeters",
    "elevmin":                          "minimumElevationInMeters",
    "maximumelevationinmeters":         "maximumElevationInMeters",
    "maximum_elevation_in_meters":      "maximumElevationInMeters",
    "elevmax":                          "maximumElevationInMeters",
    "elevation":                        "minimumElevationInMeters",
    "elev":                             "minimumElevationInMeters",
    "habitat":                          "habitat",
    "samplingprotocol":                 "samplingProtocol",
    "sampling_protocol":                "samplingProtocol",
    "method":                           "samplingProtocol",
    "fieldnumber":                      "fieldNumber",
    "field_number":                     "fieldNumber",
    # Specimen
    "sex":                              "sex",
    "individualcount":                  "individualCount",
    "individual_count":                 "individualCount",
    "count":                            "individualCount",
    "n":                                "individualCount",
    "preparations":                     "preparations",
    "prep":                             "preparations",
    "lifestage":                        "lifeStage",
    "life_stage":                       "lifeStage",
    "typestatus":                       "typeStatus",
    "type_status":                      "typeStatus",
    "occurrenceremarks":                "occurrenceRemarks",
    "occurrence_remarks":               "occurrenceRemarks",
    "remarks":                          "occurrenceRemarks",
    # Higher taxonomy
    "family":                           "family",
    "subfamily":                        "subfamily",
    "tribe":                            "tribe",
    "subtribe":                         "subtribe",
    "subgenus":                         "subgenus",
    "order":                            "order",
    # Identifiers / provenance
    "occurrenceid":                     "occurrenceID",
    "occurrence_id":                    "occurrenceID",
    "catalognumber":                    "catalogNumber",
    "catalog_number":                   "catalogNumber",
    "verbatimlabel":                    "verbatimLabel",
    "verbatim_label":                   "verbatimLabel",
    "fieldnotes":                       "verbatimLabel",
}

_SEARCH_FIELDS = [
    "scientificName", "genus", "specificEpithet",
    "eventDate", "verbatimEventDate",
    "country", "stateProvince", "county", "locality", "verbatimLocality",
    "recordedBy", "identifiedBy",
    "occurrenceID", "catalogNumber",
]


def _norm_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", raw.lower())


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


def row_to_event_fields(row: dict) -> dict:
    return {
        "country":                          row.get("country") or "",
        "country_code":                     row.get("countryCode") or "",
        "state_province":                   row.get("stateProvince") or "",
        "county":                           row.get("county") or "",
        "municipality":                     row.get("municipality") or "",
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
        "identified_by":   row.get("identifiedBy") or "",
        "date_identified": row.get("dateIdentified") or "",
        "verbatim_identification": row_scientific_name(row),
    }


def row_to_specimen_prefill(row: dict) -> dict:
    """Fields from the spreadsheet that can pre-fill the per-specimen inputs."""
    return {
        "sex":               row.get("sex") or "",
        "individual_count":  row.get("individualCount") or "1",
        "preparations":      row.get("preparations") or "",
        "life_stage":        row.get("lifeStage") or "",
        "type_status":       row.get("typeStatus") or "",
        "occurrence_remarks":row.get("occurrenceRemarks") or "",
    }

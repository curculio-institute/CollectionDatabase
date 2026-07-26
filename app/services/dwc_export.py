"""Darwin Core export — eligibility policy and the occurrence projection (#149).

Two things live here, and the split matters:

* **`export_decision()`** — *may* this specimen leave the building, and with which fields
  blanked. Pure policy, derived from the three `confidential` flags and person consent.
* **the occurrence projection** — *what* a specimen looks like as a DwC row.

Both the TaxonWorks sync comparison and the emitted CSV read the same two functions, so a
record the comparison calls "withheld" can never be the record the CSV writes, and a field
the CSV emits can never be a field the comparison forgot to diff.

Withholding is deliberately **loud in the report and silent in the file**: an ineligible
specimen is listed, with its reason, in the sync tab — it simply never reaches the CSV. A
blanked name is written as *no value at all*, never a placeholder (see AppConfig for why).
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_config
from app.models import CollectingEvent, CollectionObject, Person
from app.services import taxa


# ── Eligibility ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExportDecision:
    """Whether a specimen may be exported, and what must be withheld from its row.

    `reasons` is populated only when `eligible` is False; `blank_fields` only when the
    record IS exported but a field must be emitted empty. The two are never both set —
    a record that is not exported has no fields to blank.
    """
    eligible: bool
    reasons: tuple[str, ...] = ()
    blank_fields: tuple[str, ...] = ()
    # Why each blanked field was blanked, for the report ("recordedBy: has not consented").
    blank_notes: tuple[str, ...] = ()

    @property
    def withheld(self) -> bool:
        return not self.eligible


def _recorded_by(event: CollectingEvent | None) -> Person | None:
    return event.recorded_by_person if event is not None else None


def export_decision(
    co: CollectionObject,
    *,
    event: CollectingEvent | None = None,
) -> ExportDecision:
    """Decide whether `co` may be exported to TaxonWorks, and what must be blanked.

    `event` defaults to the specimen's own collecting event; pass it explicitly only to
    avoid a lazy load when the caller already holds it.

    The rules, in the order the issue states them (#149 Step 1.2 / Step 3.2):

    1. the specimen is flagged confidential           → withheld
    2. its collecting event is flagged confidential    → withheld (the event withholds
       *all* of its specimens — you cannot keep the occurrence but blank the locality,
       that breaks the record)
    3. the event's recordedBy person is confidential   → **withheld, always**
    4. the recordedBy person is neither confidential
       nor consent_approved                           → recordedBy blanked, or withheld
       (`AppConfig.tw_export_nonconsent`)

    Rule 3 has **no setting**: a confidential collector is never exported, in any form.
    The flag exists to be obeyed, not weighed — so there is deliberately no configuration
    that turns it into "export the record without the name". Rule 4 covers the genuinely
    undecided case (nobody asked the person yet), and that is the only choice offered.

    The two rules cannot overlap: `confidential` and `consent_approved` are mutually
    exclusive at the DB level, so a confidential person also reads as "not consented" —
    rule 4 therefore tests `not person.confidential` and rule 3 owns that case alone.

    `identifiedBy` is never blanked and never withholds a record.
    """
    cfg = get_config()
    event = event if event is not None else co.collecting_event

    reasons: list[str] = []
    blank_fields: list[str] = []
    blank_notes: list[str] = []

    if co.confidential:
        reasons.append("specimen is flagged confidential")
    if event is not None and event.confidential:
        reasons.append("collecting event is flagged confidential")

    person = _recorded_by(event)
    if person is not None:
        if person.confidential:
            # No setting here, by design — a confidential collector is never exported.
            reasons.append(f"recordedBy {person.full_name} is confidential")
        elif not person.consent_approved:
            if cfg.tw_export_nonconsent == "consented_only":
                reasons.append(f"recordedBy {person.full_name} has not consented")
            else:                                    # "name_removed" — the default
                blank_fields.append("recordedBy")
                blank_notes.append(f"recordedBy: {person.full_name} has not consented")

    if reasons:
        # A withheld record has no fields to blank — drop them so the report cannot show
        # a redaction for a row that is never written.
        return ExportDecision(eligible=False, reasons=tuple(reasons))
    return ExportDecision(
        eligible=True,
        blank_fields=tuple(blank_fields),
        blank_notes=tuple(blank_notes),
    )


# ── Occurrence projection (#149 Step 3) ─────────────────────────────────────────
#
# Column set verified against SpeciesFileGroup/taxonworks @ 897f385,
# app/models/dataset_record/darwin_core/occurrence.rb. We emit only columns TW's
# importer actually maps — a `[Not mapped]` column is silently dropped on import, so
# emitting it would make the file *look* like it carried data it did not. Confirmed
# `[Not mapped]` and therefore NEVER emitted: lifeStage (:912), disposition (:940),
# otherCatalogNumbers (:950), associatedTaxa (:743), municipality (:1097),
# locality (:1099), occurrenceStatus, verbatimIdentification (absent entirely).

# Header order = the column order TW's importer expects; also the file's write order.
DWC_COLUMNS: tuple[str, ...] = (
    "occurrenceID",
    "catalogNumber",
    "institutionCode",
    "collectionCode",
    "basisOfRecord",
    "individualCount",
    "sex",
    "preparations",
    "typeStatus",
    "occurrenceRemarks",
    "recordedBy",
    "eventDate",
    "verbatimEventDate",
    "country",
    "stateProvince",
    "county",
    "verbatimLocality",
    "decimalLatitude",
    "decimalLongitude",
    "geodeticDatum",
    "coordinateUncertaintyInMeters",
    "minimumElevationInMeters",
    "maximumElevationInMeters",
    "verbatimElevation",
    "habitat",
    "samplingProtocol",
    "eventRemarks",
    "fieldNumber",
    "scientificName",
    "scientificNameAuthorship",
    "taxonRank",
    "nomenclaturalCode",
    "identificationQualifier",
    "identifiedBy",
    "dateIdentified",
)

# import_dataset/darwin_core/occurrences.rb:8-11 — a dataset with no occurrenceID/
# scientificName/basisOfRecord column fails validation outright, so these three are
# always written (even empty) rather than only-when-present like the rest.
_REQUIRED_HEADER_COLUMNS = ("occurrenceID", "scientificName", "basisOfRecord")
assert set(_REQUIRED_HEADER_COLUMNS) <= set(DWC_COLUMNS)


@dataclass(frozen=True)
class RowProblem:
    """A specimen that is eligible for export but whose row TW would reject.

    Never written to the file — TW's DwC importer is CREATE-ONLY, so a bad row that
    half-imports cannot be corrected via the API afterwards. `message` says both what
    is wrong and what to fix, so the report is actionable without opening this file.
    """
    catalog_number: str
    column: str
    message: str


@dataclass(frozen=True)
class ExportResult:
    """The outcome of exporting a batch of specimens.

    `withheld` (policy: `export_decision` says no) and `refused` (eligible, but the
    row would be invalid for TW) are deliberately separate — the former is a privacy
    decision, the latter a data-quality one, and they are fixed in different places
    (Controlled Vocabularies / person consent vs. Records).
    """
    tsv: str
    written: tuple[str, ...]
    withheld: tuple[tuple[str, tuple[str, ...]], ...]
    redacted: tuple[tuple[str, tuple[str, ...]], ...]
    refused: tuple[RowProblem, ...]
    preparations_used: tuple[str, ...]
    row_count: int


def _s(value: object) -> str:
    """Stringify a raw column value for the TSV: None -> "", else str().strip()."""
    if value is None:
        return ""
    return str(value).strip()


def _current_determination(co: CollectionObject):
    """The determination with is_current == 1, or None. A specimen may have none
    (never identified) or, transiently, more than the DB partial-unique index
    should allow — the first match is taken, same as the rest of the app."""
    for det in co.determinations:
        if det.is_current == 1:
            return det
    return None


def _format_uncertainty(raw: float | None) -> str:
    """coordinateUncertaintyInMeters as a bare integer string.

    occurrence.rb:1138-1140 requires ``/\\A[+-]?\\d+\\z/`` — a float like "10.0"
    errors the row, so a present value is rounded to the nearest metre. When `raw`
    cannot be coerced (NaN/Infinity — malformed data, not a normal export value) the
    raw string form is returned instead so the row-validation step below can catch
    and refuse it rather than silently writing something TW would reject.
    """
    if raw is None:
        return ""
    try:
        return str(int(round(float(raw))))
    except (ValueError, OverflowError):
        return _s(raw)


def _verbatim_locality(ev: CollectingEvent | None) -> str:
    """TW drops both `locality` and `municipality` (occurrence.rb:1097/1099) but
    keeps `verbatimLocality` verbatim, so any free-text place detail must fold into
    it or it is lost on import.

    1. `ev.verbatim_locality`, if set, wins outright — it is what was on the
       physical label, the ground truth.
    2. Otherwise the non-empty of municipality/locality, joined with ", " — our own
       reading of the place, used only when there is no verbatim string to prefer.

    The two forms are never concatenated together.
    """
    if ev is None:
        return ""
    if ev.verbatim_locality:
        return _s(ev.verbatim_locality)
    parts = [_s(p) for p in (ev.municipality, ev.locality) if p]
    return ", ".join(parts)


def occurrence_row(
    session: Session,
    co: CollectionObject,
    *,
    decision: ExportDecision | None = None,
) -> dict[str, str]:
    """Project one specimen to a DwC occurrence row.

    Returns every key in `DWC_COLUMNS` (missing data -> ""), all values already
    stringified and stripped. Applies `decision.blank_fields` (computing the
    decision itself when not supplied — callers iterating many specimens should
    pass it in, since `export_occurrences` already computed it once per row).
    """
    if decision is None:
        decision = export_decision(co)

    ev = co.collecting_event
    det = _current_determination(co)
    tx = det.taxon if det is not None else None
    repo = co.repository

    row: dict[str, str] = {c: "" for c in DWC_COLUMNS}

    inst_code = _s(repo.institution_code) if repo is not None else ""
    coll_code = _s(repo.collection_code) if repo is not None else ""
    cat_num = _s(co.catalog_number)

    # Deterministic on purpose: re-exporting the same specimen must produce the
    # same occurrenceID, or a re-import would create a second TW record. Left
    # empty when the triplet cannot be formed (repository not fully set up) —
    # and `_validate_row` then refuses the row rather than writing it.
    if inst_code and coll_code:
        row["occurrenceID"] = f"{inst_code}:{coll_code}:{cat_num}"
    row["catalogNumber"] = cat_num
    row["institutionCode"] = inst_code
    row["collectionCode"] = coll_code

    row["basisOfRecord"] = _s(co.basis_of_record)
    # occurrence.rb:883 + :310 — total == '1' is what makes TW create a Specimen
    # rather than a Lot. An empty value would default to 1 anyway; write it
    # explicitly so the file states the count rather than relying on TW's default.
    row["individualCount"] = str(co.individual_count) if co.individual_count else "1"

    # sex/typeStatus live on the determination, not the specimen.
    row["sex"] = _s(det.sex) if det is not None else ""
    row["preparations"] = _s(co.preparation.name) if co.preparation is not None else ""
    row["typeStatus"] = _s(det.type_status) if det is not None else ""
    row["occurrenceRemarks"] = _s(co.occurrence_remarks)

    if "recordedBy" in decision.blank_fields:
        row["recordedBy"] = ""
    elif ev is not None and ev.recorded_by_person is not None:
        row["recordedBy"] = _s(ev.recorded_by_person.full_name)

    if ev is not None:
        row["eventDate"] = _s(ev.event_date)
        row["verbatimEventDate"] = _s(ev.verbatim_event_date)
        row["country"] = _s(ev.country_obj.name) if ev.country_obj is not None else ""
        row["stateProvince"] = (
            _s(ev.state_province_obj.name) if ev.state_province_obj is not None else ""
        )
        row["county"] = _s(ev.county_obj.name) if ev.county_obj is not None else ""
        row["verbatimLocality"] = _verbatim_locality(ev)
        row["decimalLatitude"] = _s(ev.decimal_latitude)
        row["decimalLongitude"] = _s(ev.decimal_longitude)
        row["geodeticDatum"] = _s(ev.geodetic_datum)
        row["coordinateUncertaintyInMeters"] = _format_uncertainty(
            ev.coordinate_uncertainty_in_meters
        )
        row["minimumElevationInMeters"] = _s(ev.minimum_elevation_in_meters)
        row["maximumElevationInMeters"] = _s(ev.maximum_elevation_in_meters)
        row["verbatimElevation"] = _s(ev.verbatim_elevation)
        # habitat/samplingProtocol resolve to TW's verbatim_habitat (:1047) /
        # verbatim_method (:1050) — not a controlled field on TW's side.
        row["habitat"] = _s(ev.habitat_obj.name) if ev.habitat_obj is not None else ""
        row["samplingProtocol"] = (
            _s(ev.sampling_protocol_obj.name) if ev.sampling_protocol_obj is not None else ""
        )
        row["eventRemarks"] = _s(ev.event_remarks)
        row["fieldNumber"] = _s(ev.field_number)

    # scientificName is the name AS DETERMINED, not resolved to the accepted name.
    # `verbatimIdentification` has no TW import path at all (absent from
    # occurrence.rb), so resolving a synonym determination to its accepted name here
    # would delete the determination-as-made from the record with nothing left to
    # preserve it. TW models synonymy itself and matches the protonym, so exporting
    # the synonym name — det.taxon, composed WITH authorship via
    # taxa.compose_full_name — imports correctly even when it is a synonym.
    if tx is not None:
        row["scientificName"] = _s(taxa.compose_full_name(session, tx))
        row["scientificNameAuthorship"] = _s(tx.scientific_name_authorship)
        row["taxonRank"] = _s(tx.taxon_rank)
        # occurrence.rb:1454-1459 accepts only iczn/icn/icnp/icvcn (lowercase).
        row["nomenclaturalCode"] = _s(tx.nomenclatural_code).lower()

    if det is not None:
        row["identificationQualifier"] = _s(det.identification_qualifier)
        # identifiedBy is NEVER blanked, at any export setting — see export_decision.
        if det.identified_by_person is not None:
            row["identifiedBy"] = _s(det.identified_by_person.full_name)
        row["dateIdentified"] = _s(det.date_identified)

    return row


def _validate_row(row: dict[str, str]) -> list[RowProblem]:
    """Every reason a would-be-eligible row must not reach the file.

    Refuse loudly, never silently rewrite: a value TW will reject is reported and
    the row is dropped, not "fixed" — TW's importer is CREATE-ONLY, so a half-wrong
    row cannot be corrected afterwards via the API. All violations are collected (not
    just the first) so one look at `refused` shows everything that needs fixing.
    """
    cat_num = row["catalogNumber"]
    problems: list[RowProblem] = []

    # 6. Cannot happen — catalog_number is NOT NULL at the DB level — but the
    # invariant is asserted here rather than trusted blindly.
    if not cat_num:
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="catalogNumber",
            message="catalogNumber is empty, which violates the database's NOT NULL "
                    "constraint; this indicates data corruption — investigate before "
                    "exporting.",
        ))

    # 1. occurrence.rb:849-850 — only these two values are accepted.
    basis = row["basisOfRecord"]
    if basis and basis.strip().lower() not in {"preservedspecimen", "fossilspecimen"}:
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="basisOfRecord",
            message=f"basisOfRecord '{basis}' is not PreservedSpecimen or "
                    f"FossilSpecimen (TW rejects anything else); change it on the "
                    f"specimen in Records before exporting.",
        ))

    # 2. occurrence.rb:892 — sex must be a single word.
    sex = row["sex"]
    if sex and any(ch.isspace() for ch in sex):
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="sex",
            message=f"sex '{sex}' contains whitespace (TW accepts only a single "
                    f"word); fix it on the determination in Records before exporting.",
        ))

    # 3. occurrence.rb:1394-1403 — unlike eventDate, dateIdentified rejects a range.
    date_identified = row["dateIdentified"]
    if "/" in date_identified:
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="dateIdentified",
            message=f"dateIdentified '{date_identified}' is a date interval (TW "
                    f"rejects a '/' range here, unlike eventDate); set a single date "
                    f"on the determination in Records before exporting.",
        ))

    # 4. occurrence.rb:1138-1140 — must be a bare integer. A correctly formatted
    # value is always digits-only (the CHECK constraint forbids negative metres);
    # anything else means _format_uncertainty could not coerce the source value.
    uncertainty = row["coordinateUncertaintyInMeters"]
    if uncertainty and not uncertainty.isdigit():
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="coordinateUncertaintyInMeters",
            message=f"coordinateUncertaintyInMeters '{uncertainty}' is not a whole "
                    f"number of metres (TW requires a bare integer); fix the "
                    f"collecting event in Records before exporting.",
        ))

    # 7. occurrenceID is the deterministic DwC triplet institutionCode:collectionCode:
    # catalogNumber, and it is the only thing stopping a re-export from minting a second
    # TaxonWorks record for one specimen (TW keeps it as
    # Identifier::Local::Import::Dwc). It comes out empty exactly when the repository is
    # missing a code — `dwc:institutionCode` is nullable, so this is reachable, not
    # theoretical. TW also needs the (institutionCode, collectionCode) pair to resolve
    # the Namespace that binds the catalogNumber (occurrence.rb:497-509), so a row
    # without it imports with no catalog number to diff against, and the next export
    # uploads the specimen again.
    if not row["occurrenceID"]:
        missing = " and ".join(
            name for name in ("institutionCode", "collectionCode") if not row[name]
        )
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="occurrenceID",
            message=f"occurrenceID cannot be formed because the collection has no "
                    f"{missing or 'institutionCode/collectionCode'}; set it on the "
                    f"collection in Controlled Vocabularies before exporting. Without "
                    f"it TaxonWorks cannot resolve the catalog-number namespace, and a "
                    f"later export would upload this specimen a second time.",
        ))

    # 5. No current determination (or its taxon) means no name at all.
    if not row["scientificName"]:
        problems.append(RowProblem(
            catalog_number=cat_num,
            column="scientificName",
            message="no current determination; identify it in Records before "
                    "exporting.",
        ))

    return problems


def _write_tsv(rows: list[dict[str, str]]) -> str:
    """Serialise rows to the complete TSV file content.

    import_dataset/darwin_core.rb:315-317 — TW's default `col_sep` for a flat
    upload is TAB, not comma; a comma CSV parses as one column with Import Settings
    left at their default, so every row looks like it imported but is garbage.
    QUOTE_MINIMAL matches TW's `quote_char='"'` reader, so this round-trips.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(DWC_COLUMNS)
    for row in rows:
        writer.writerow([row[c] for c in DWC_COLUMNS])
    return buf.getvalue()


def export_occurrences(session: Session, cos: Iterable[CollectionObject]) -> ExportResult:
    """Run `export_decision` per specimen, validate the eligible rows, and write
    the TSV. Withheld specimens (policy) never reach validation; refused rows
    (eligible but invalid for TW) never reach the file — both are reported so
    nothing is silently dropped."""
    written: list[str] = []
    withheld: list[tuple[str, tuple[str, ...]]] = []
    redacted: list[tuple[str, tuple[str, ...]]] = []
    refused: list[RowProblem] = []
    preparations_used: set[str] = set()
    rows: list[dict[str, str]] = []

    for co in cos:
        decision = export_decision(co)
        if not decision.eligible:
            withheld.append((co.catalog_number, decision.reasons))
            continue

        row = occurrence_row(session, co, decision=decision)
        problems = _validate_row(row)
        if problems:
            refused.extend(problems)
            continue

        if decision.blank_notes:
            redacted.append((co.catalog_number, decision.blank_notes))
        if row["preparations"]:
            preparations_used.add(row["preparations"])
        rows.append(row)
        written.append(row["catalogNumber"])

    return ExportResult(
        tsv=_write_tsv(rows),
        written=tuple(written),
        withheld=tuple(withheld),
        redacted=tuple(redacted),
        refused=tuple(refused),
        preparations_used=tuple(sorted(preparations_used)),
        row_count=len(written),
    )

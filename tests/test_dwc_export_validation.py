"""A row TaxonWorks would reject is refused, never silently written (#149, §5c).

TW's DwC importer is CREATE-ONLY: a half-wrong row that imports cannot be corrected
through the API afterwards, so `_validate_row` refuses rather than rewrites.
"""
import pytest

from app.services.dwc_export import DWC_COLUMNS, _validate_row


def _row(**overrides) -> dict[str, str]:
    """A minimally valid row — every column present and empty, then the fields that
    must be non-empty filled in, mirroring `occurrence_row`'s output shape."""
    row = {c: "" for c in DWC_COLUMNS}
    row.update({
        "occurrenceID": "JJPC:JJPC:JJPC-00001",
        "catalogNumber": "JJPC-00001",
        "institutionCode": "JJPC",
        "collectionCode": "JJPC",
        "basisOfRecord": "PreservedSpecimen",
        "scientificName": "Otiorhynchus crypticus",
    })
    row.update(overrides)
    return row


def test_a_complete_row_has_no_problems():
    assert _validate_row(_row()) == []


def test_missing_institution_code_refuses_the_row():
    """`dwc:institutionCode` is nullable, so an unformed triplet is reachable. Without
    it TW cannot resolve the namespace that binds the catalogNumber, and the next export
    would upload the specimen a second time."""
    problems = _validate_row(_row(occurrenceID="", institutionCode=""))
    assert [p.column for p in problems] == ["occurrenceID"]
    assert "institutionCode" in problems[0].message


def test_missing_both_codes_names_both():
    problems = _validate_row(
        _row(occurrenceID="", institutionCode="", collectionCode="")
    )
    assert "institutionCode and collectionCode" in problems[0].message


def test_an_empty_catalog_number_does_not_also_report_occurrence_id():
    """A blank catalogNumber still forms a truthy triplet, so it is reported once, by
    its own check — not twice."""
    problems = _validate_row(_row(catalogNumber="", occurrenceID="JJPC:JJPC:"))
    assert [p.column for p in problems] == ["catalogNumber"]


@pytest.mark.parametrize("basis", ["HumanObservation", "MaterialSample"])
def test_basis_of_record_outside_the_two_accepted_values(basis):
    problems = _validate_row(_row(basisOfRecord=basis))
    assert [p.column for p in problems] == ["basisOfRecord"]


def test_all_violations_are_collected_not_just_the_first():
    problems = _validate_row(_row(
        occurrenceID="", institutionCode="", sex="not sure",
        dateIdentified="2024-01-01/2024-02-01", scientificName="",
    ))
    assert {p.column for p in problems} == {
        "occurrenceID", "sex", "dateIdentified", "scientificName",
    }

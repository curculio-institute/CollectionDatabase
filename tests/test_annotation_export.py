"""Export of annotations (spec "Export"): only linked + verified terms the batch uses,
header = tw_name, event annotations on every row of that event, local-only listed, an
unconfirmed link blocks, association annotations never exported."""
import csv
import io

from app.models import BiologicalRelationship
from app.models.base import _utcnow
from app.services import annotations as ann
from app.services import biological as bio
from app.services.annotations import AnnotationItem
from app.services.dwc_export import DWC_COLUMNS, export_occurrences
from tests.annotation_helpers import (SANDBOX, SFG, make_event, make_specimen, make_term,
                                      species)


def _rows(tsv: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))


def _tagged(session, cat, term, **kw):
    co = make_specimen(session, cat, **kw)
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(term.id)])
    return co


def test_no_annotations_leaves_the_header_unchanged(session):
    co = make_specimen(session, "ANNX-00100")
    result = export_occurrences(session, [co], host=SFG)
    assert result.tsv.splitlines()[0].split("\t") == list(DWC_COLUMNS)
    assert result.annotation_columns == ()


def test_specimen_tag_uses_the_tw_name(session):
    t = make_term(session, name="adventive (local)", tw_id=1764, tw_name="Adventive",
                  tw_host=SFG)
    a = _tagged(session, "ANNX-00101", t)
    b = make_specimen(session, "ANNX-00102")
    rows = _rows(export_occurrences(session, [a, b], host=SFG).tsv)
    col = "TW:Tag:CollectionObject:Adventive"
    assert [r[col] for r in rows] == ["true", ""]
    assert not any("adventive (local)" in k for k in rows[0])


def test_event_data_attribute_repeats_on_every_row_of_the_event(session):
    t = make_term(session, "data_attribute", "EUNIS habitat", event=True, tw_id=3,
                  tw_name="EUNIS habitat", tw_host=SFG)
    ev = make_event(session)
    ann.set_annotations(session, "event", ev.id, [AnnotationItem(t.id, "G1.6")])
    cos = [make_specimen(session, f"ANNX-0011{i}", event_id=ev.id) for i in range(2)]
    rows = _rows(export_occurrences(session, cos, host=SFG).tsv)
    assert [r["TW:DataAttribute:CollectingEvent:EUNIS habitat"] for r in rows] == ["G1.6"] * 2


def test_only_used_terms_become_columns(session):
    make_term(session, name="Unused", tw_id=9, tw_name="Unused", tw_host=SFG)
    co = make_specimen(session, "ANNX-00120")
    assert export_occurrences(session, [co], host=SFG).annotation_columns == ()


def test_local_only_terms_are_listed_not_emitted(session):
    t = make_term(session, name="Needs re-mount")
    co = _tagged(session, "ANNX-00121", t)
    result = export_occurrences(session, [co], host=SFG)
    assert result.annotation_local_only == (("Needs re-mount", 1),)
    assert "Needs re-mount" not in result.tsv


def test_unverified_link_blocks_and_writes_no_file(session):
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=None)
    co = _tagged(session, "ANNX-00122", t)
    result = export_occurrences(session, [co], host=SFG)
    assert result.tsv == ""
    assert result.annotation_blocked[0][0] == "Adventive"
    assert "not verified" in result.annotation_blocked[0][1]


def test_term_verified_on_another_host_blocks(session):
    """Review Focus 4: the id belongs to sfg; the configured instance is the sandbox."""
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG)
    co = _tagged(session, "ANNX-00123", t)
    result = export_occurrences(session, [co], host=SANDBOX)
    assert result.tsv == ""
    assert SFG in result.annotation_blocked[0][1] and SANDBOX in result.annotation_blocked[0][1]


def test_association_annotations_are_never_exported(session):
    t = make_term(session, specimen=False, association=True, tw_id=5, tw_name="Reared",
                  tw_host=SFG)
    co = make_specimen(session, "ANNX-00124")
    rel = BiologicalRelationship(name="feeds on", created_at=_utcnow(), updated_at=_utcnow())
    session.add(rel)
    session.flush()
    ba = bio.save_biological_association(session, collection_object_id=co.id,
                                         biological_relationship_id=rel.id,
                                         object_taxon_id=species(session).id)
    ann.set_annotations(session, "association", ba.id, [AnnotationItem(t.id)])
    result = export_occurrences(session, [co], host=SFG)
    assert result.annotation_columns == () and "Reared" not in result.tsv


def test_column_order_is_stable(session):
    tag = make_term(session, name="B tag", tw_id=1, tw_name="B tag", tw_host=SFG)
    tag2 = make_term(session, name="A tag", tw_id=2, tw_name="A tag", tw_host=SFG)
    da = make_term(session, "data_attribute", "Altitude", tw_id=3, tw_name="Altitude",
                   tw_host=SFG)
    co = make_specimen(session, "ANNX-00125")
    ann.set_annotations(session, "specimen", co.id, [
        AnnotationItem(tag.id), AnnotationItem(tag2.id), AnnotationItem(da.id, "350 m")])
    headers = [c.header for c in export_occurrences(session, [co], host=SFG).annotation_columns]
    assert headers == ["TW:Tag:CollectionObject:A tag", "TW:Tag:CollectionObject:B tag",
                       "TW:DataAttribute:CollectionObject:Altitude"]


def test_header_collision_blocks_both_terms(session):
    """Two linked, verified tags with different tw_id but the same tw_name would write
    one column twice — both are blocked and no file is written."""
    t1 = make_term(session, name="Adventive A", tw_id=1, tw_name="Adventive", tw_host=SFG)
    t2 = make_term(session, name="Adventive B", tw_id=2, tw_name="Adventive", tw_host=SFG)
    a = _tagged(session, "ANNX-00130", t1)
    ann.set_annotations(session, "specimen", a.id,
                        [AnnotationItem(t1.id), AnnotationItem(t2.id)])
    result = export_occurrences(session, [a], host=SFG)
    assert result.tsv == ""
    assert {n for n, _ in result.annotation_blocked} == {"Adventive A", "Adventive B"}
    assert all("shares the export column" in r for _, r in result.annotation_blocked)

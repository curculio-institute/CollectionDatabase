"""Biological-association compare (spec 2026-10-17): TaxonWorks fetches re-verified,
the host-name rule, and the pure comparison."""
import asyncio

import httpx
import pytest

import app.config as config_mod
import app.services.tw_association_compare as cmp
from app.config import AppConfig
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_association_compare import TwName


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance",
                        AppConfig(tw_base="https://sfg.taxonworks.org/api/v1", tw_token="t"))


def _serve(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cmp, "_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=5))


# ── host_verdict ────────────────────────────────────────────────────────────────

def test_host_verdict_exact_and_whitespace():
    assert cmp.host_verdict("Typha", "L.", TwName(1, "Typha", "L.", "genus")) == ("match", ())
    assert cmp.host_verdict("Typha  latifolia", "L.",
                            TwName(1, "Typha latifolia", "L.", "species"))[0] == "match"


def test_host_verdict_subgenus_only_difference():
    verdict, notes = cmp.host_verdict("Carabus (Eucarabus) arvensis", "Herbst, 1784",
                                      TwName(1, "Carabus arvensis", "Herbst, 1784", "species"))
    assert verdict == "subgenus"
    assert any("subgenus" in n for n in notes)


def test_host_verdict_authorship_differs_is_a_difference():
    verdict, notes = cmp.host_verdict("Typha", "L.", TwName(1, "Typha", "Mill.", "genus"))
    assert verdict == "differs"
    assert "authorship" in notes[0]


def test_host_verdict_missing_authorship_matches_with_note():
    verdict, notes = cmp.host_verdict("Typha", None, TwName(1, "Typha", "L.", "genus"))
    assert verdict == "match"
    assert any("authorship not compared" in n for n in notes)


def test_host_verdict_transliteration_matches_with_note():
    verdict, notes = cmp.host_verdict("Otiorhynchini", "Schönherr, 1826",
                                      TwName(1, "Otiorhynchini", "Schoenherr, 1826", "tribe"))
    assert verdict == "match"
    assert any("spelled differently" in n for n in notes)


def test_host_verdict_other_name_differs():
    verdict, notes = cmp.host_verdict("Carex", "L.", TwName(1, "Typha", "L.", "genus"))
    assert verdict == "differs"
    assert "name" in notes[0]


# ── fetch_associations ──────────────────────────────────────────────────────────

def _basic(ba_id, co=501, obj=("AnatomicalPart", 687), otu=(9001, 1471869),
           label="rhizome: Typha L."):
    return {"id": ba_id, "relationship": "collected from",
            "subject": {"id": co, "type": "CollectionObject", "label": f"CO {co}"},
            "object": {"id": obj[1], "type": obj[0], "label": label},
            "subject_otu_id": otu[0], "object_otu_id": otu[1]}


def _plain(ba_id, co=501, obj=("AnatomicalPart", 687), rel=92):
    return {"id": ba_id, "biological_relationship_id": rel,
            "biological_association_subject_type": "CollectionObject",
            "biological_association_subject_id": co,
            "biological_association_object_type": obj[0],
            "biological_association_object_id": obj[1]}


def _routes(basic_rows, plain_rows):
    def handler(r):
        if r.url.path.endswith("/biological_associations/basic"):
            return httpx.Response(200, json=basic_rows)
        if r.url.path.endswith("/biological_associations"):
            return httpx.Response(200, json=plain_rows)
        pytest.fail(f"unexpected request {r.url}")
    return handler


def test_fetch_associations_joins_basic_and_plain(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(321383)], [_plain(321383)]))
    got = asyncio.run(cmp.fetch_associations([501]))
    t = got[321383]
    assert t.relationship_id == 92 and t.relationship_name == "collected from"
    assert (t.subject.type, t.subject.id, t.subject.otu_id) == ("CollectionObject", 501, 9001)
    assert (t.object.type, t.object.id, t.object.otu_id, t.object.label) == (
        "AnatomicalPart", 687, 1471869, "rhizome: Typha L.")


def test_fetch_associations_sends_the_ids_as_collection_object_filter(cfg, monkeypatch):
    seen = []

    def handler(r):
        seen.append(r.url.params.get_list("collection_object_id[]"))
        return _routes([], [])(r)
    _serve(monkeypatch, handler)
    asyncio.run(cmp.fetch_associations([501, 502]))
    assert seen == [["501", "502"], ["501", "502"]]


def test_fetch_associations_batches_at_100_ids(cfg, monkeypatch):
    batches = []

    def handler(r):
        batches.append(len(r.url.params.get_list("collection_object_id[]")))
        return _routes([], [])(r)
    _serve(monkeypatch, handler)
    asyncio.run(cmp.fetch_associations(list(range(1, 251))))
    assert sorted(batches) == [50, 50, 100, 100, 100, 100]


def test_fetch_associations_rejects_a_row_for_another_specimen(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(1, co=999)], [_plain(1, co=999)]))
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_lists_that_disagree_are_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(1), _basic(2)], [_plain(1)]))
    with pytest.raises(TaxonWorksUnreachable, match="disagree"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_sides_that_disagree_are_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(1)], [_plain(1, obj=("Otu", 5))]))
    with pytest.raises(TaxonWorksUnreachable, match="different sides"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_missing_otu_key_is_a_failure(cfg, monkeypatch):
    row = _basic(1)
    del row["object_otu_id"]
    _serve(monkeypatch, _routes([row], [_plain(1)]))
    with pytest.raises(TaxonWorksUnreachable, match="object_otu_id"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_missing_relationship_id_is_a_failure(cfg, monkeypatch):
    row = _plain(1)
    row["biological_relationship_id"] = None
    _serve(monkeypatch, _routes([_basic(1)], [row]))
    with pytest.raises(TaxonWorksUnreachable, match="relationship id"):
        asyncio.run(cmp.fetch_associations([501]))


def test_empty_input_never_touches_the_network(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: pytest.fail("no request expected"))
    assert asyncio.run(cmp.fetch_associations([])) == {}
    assert asyncio.run(cmp.fetch_otu_names([])) == {}


# ── fetch_otu_names ─────────────────────────────────────────────────────────────

def _otu_routes(otus, names):
    def handler(r):
        if r.url.path.endswith("/otus"):
            return httpx.Response(200, json=otus)
        if r.url.path.endswith("/taxon_names"):
            return httpx.Response(200, json=names)
        pytest.fail(f"unexpected request {r.url}")
    return handler


def test_fetch_otu_names_follows_otu_to_taxon_name(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes(
        [{"id": 1471869, "taxon_name_id": 1655474}, {"id": 77, "taxon_name_id": None}],
        [{"id": 1655474, "cached": "Typha", "cached_author_year": "L.", "rank": "genus"}]))
    got = asyncio.run(cmp.fetch_otu_names([1471869, 77]))
    assert got[1471869] == TwName(1655474, "Typha", "L.", "genus")
    assert got[77] is None


def test_fetch_otu_names_missing_otu_is_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes([{"id": 1, "taxon_name_id": 10}],
                                    [{"id": 10, "cached": "Typha", "rank": "genus"}]))
    with pytest.raises(TaxonWorksUnreachable, match=r"OTU\(s\) \[2\]"):
        asyncio.run(cmp.fetch_otu_names([1, 2]))


def test_fetch_otu_names_missing_taxon_name_is_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes([{"id": 1, "taxon_name_id": 10}], []))
    with pytest.raises(TaxonWorksUnreachable, match=r"taxon name\(s\) \[10\]"):
        asyncio.run(cmp.fetch_otu_names([1]))


def test_fetch_otu_names_foreign_otu_is_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes([{"id": 999, "taxon_name_id": 10}], []))
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        asyncio.run(cmp.fetch_otu_names([1]))


# ── compare_associations (pure; temporary database, no network) ─────────────────

from app.models import BiologicalAssociation, BiologicalRelationship, Taxon  # noqa: E402
from app.models.base import _utcnow  # noqa: E402
from app.services import annotations as ann  # noqa: E402
from app.services import biological as bio  # noqa: E402
from app.services import field_occurrence as fo_svc  # noqa: E402
from app.services import specimens as spec_svc  # noqa: E402
from app.services.annotations import AnnotationItem  # noqa: E402
from app.services.tw_annotation_compare import TwDatum, TwTag  # noqa: E402
from app.services.tw_association_compare import TwAssociation, TwSide  # noqa: E402
from tests.annotation_helpers import (SANDBOX, SFG, make_event, make_specimen,  # noqa: E402
                                      make_term, species)

TYPHA = TwName(1655474, "Typha", "L.", "genus")


def _rel(session, name="collected from", tw_id=92, host=SFG):
    # get-or-create: migration 0013 seeds relationship rows ("collected from" with
    # taxonworksID 92, "[legacy] is vector of" with 60), so a fresh insert would collide
    # on name / UNIQUE(taxonworksID); reset the link state each time.
    r = session.query(BiologicalRelationship).filter_by(name=name).one_or_none()
    if r is None:
        r = BiologicalRelationship(name=name, created_at=_utcnow(), updated_at=_utcnow())
        session.add(r)
    if tw_id is not None:       # another row from an earlier test may hold this id
        for other in session.query(BiologicalRelationship).filter(
                BiologicalRelationship.taxonworks_id == tw_id,
                BiologicalRelationship.id != r.id):
            other.tw_host = None
            other.tw_name = None
            other.taxonworks_id = None
        session.flush()
    r.taxonworks_id = tw_id
    r.tw_name = name if tw_id is not None else None
    r.tw_host = host if tw_id is not None else None
    session.flush()
    return r


def _plant(session, genus="Typha", author="L."):
    t = Taxon(name_element=genus, scientific_name=genus, taxon_rank="genus",
              nomenclatural_code="ICN", scientific_name_authorship=author,
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def _on(session, co, rel, host_taxon):
    return bio.save_biological_association(
        session, collection_object_id=co.id, biological_relationship_id=rel.id,
        object_taxon_id=host_taxon.id)


def _tw(ba_id, otu=1471869, rel=92, co=501, obj_type="AnatomicalPart",
        label="rhizome: Typha L.", name="collected from"):
    return TwAssociation(ba_id, rel, name, TwSide("CollectionObject", co, 9001, f"CO {co}"),
                         TwSide(obj_type, 687, otu, label))


def _cmp(session, co, tw_assocs=(), names=None, **kw):
    base = dict(repository_id=co.repository_id,
                specimens=[(co.catalog_number, co.id, 501)], ambiguous=(),
                tw_assocs={t.id: t for t in tw_assocs},
                names={1471869: TYPHA} if names is None else names,
                tw_tags={}, tw_data={}, host=SFG)
    base.update(kw)
    return cmp.compare_associations(session, **base)


def test_anatomical_part_host_matches_by_its_otu_name(session):
    """JJPC-00102: TaxonWorks 'collected from → rhizome: Typha L.' (an anatomical part);
    local 'collected from → Typha L.'. The part is ignored, the OTU's name matches."""
    co = make_specimen(session, "ANNX-00300")
    _on(session, co, _rel(session), _plant(session))
    r = _cmp(session, co, [_tw(321383)])
    assert r.confirmed_count == 1
    assert (r.to_add, r.only_on_tw, r.host_differs, r.cant_compare) == ((), (), (), ())


def test_local_only_association_is_to_add(session):
    co = make_specimen(session, "ANNX-00301")
    a = _on(session, co, _rel(session), _plant(session))
    r = _cmp(session, co)
    (row,) = r.to_add
    assert (row.catalog_number, row.tw_specimen_id, row.local_id) == ("ANNX-00301", 501, a.id)
    assert (row.relationship, row.host, row.host_kind) == ("collected from", "Typha L.", "taxon")


def test_tw_only_association_shows_the_full_label(session):
    co = make_specimen(session, "ANNX-00302")
    r = _cmp(session, co, [_tw(9)])
    (row,) = r.only_on_tw
    assert (row.tw_id, row.relationship, row.host, row.host_kind) == (
        9, "collected from", "rhizome: Typha L.", "AnatomicalPart")


def test_single_leftover_pair_is_host_differs(session):
    co = make_specimen(session, "ANNX-00303")
    _on(session, co, _rel(session), _plant(session, "Carex"))
    r = _cmp(session, co, [_tw(9)])
    (row,) = r.host_differs
    assert (row.host, row.tw_host) == ("Carex L.", "rhizome: Typha L.")
    assert "name" in row.reason
    assert r.to_add == () and r.only_on_tw == ()


def test_several_leftovers_are_not_paired(session):
    co = make_specimen(session, "ANNX-00304")
    rel = _rel(session)
    _on(session, co, rel, _plant(session, "Carex"))
    _on(session, co, rel, _plant(session, "Juncus"))
    r = _cmp(session, co, [_tw(9)])
    assert len(r.to_add) == 2 and len(r.only_on_tw) == 1 and r.host_differs == ()


def test_two_local_against_one_on_tw(session):
    co = make_specimen(session, "ANNX-00305")
    rel, typha = _rel(session), _plant(session)
    _on(session, co, rel, typha)
    _on(session, co, rel, typha)
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1 and len(r.to_add) == 1


def test_authorship_difference_is_host_differs(session):
    co = make_specimen(session, "ANNX-00306")
    _on(session, co, _rel(session), _plant(session, "Typha", "Mill."))
    r = _cmp(session, co, [_tw(9)])
    (row,) = r.host_differs
    assert "authorship" in row.reason


def test_field_occurrence_host_uses_its_current_identification(session):
    ev = make_event(session)
    co = make_specimen(session, "ANNX-00307", event_id=ev.id)
    fo = fo_svc.create_field_occurrence(session, collecting_event_id=ev.id,
                                        taxon_id=_plant(session).id)
    bio.save_biological_association(session, collection_object_id=co.id,
                                    biological_relationship_id=_rel(session).id,
                                    object_field_occurrence_id=fo.id)
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1


def test_qualified_host_that_matches_is_listed_with_its_note(session):
    ev = make_event(session)
    co = make_specimen(session, "ANNX-00308", event_id=ev.id)
    fo = fo_svc.create_field_occurrence(session, collecting_event_id=ev.id,
                                        taxon_id=_plant(session).id,
                                        identification_qualifier="cf.")
    bio.save_biological_association(session, collection_object_id=co.id,
                                    biological_relationship_id=_rel(session).id,
                                    object_field_occurrence_id=fo.id)
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1
    (row,) = r.confirmed_with_notes
    assert any("cf." in n for n in row.notes)
    assert row.local_id is not None and row.tw_id == 9
    assert row.tw_host == "rhizome: Typha L."


def test_confirmed_pair_with_missing_authorship_is_listed_with_note(session):
    co = make_specimen(session, "ANNX-00318")
    _on(session, co, _rel(session), _plant(session, "Typha", None))
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1
    (row,) = r.confirmed_with_notes
    assert any("authorship not compared" in n for n in row.notes)


def test_exact_confirmed_pair_stays_count_only(session):
    co = make_specimen(session, "ANNX-00319")
    _on(session, co, _rel(session), _plant(session))
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1 and r.confirmed_with_notes == ()


def test_no_current_identification_cannot_be_compared(session):
    ev = make_event(session)
    co = make_specimen(session, "ANNX-00309", event_id=ev.id)
    fo = fo_svc.create_field_occurrence(session, collecting_event_id=ev.id,
                                        taxon_id=_plant(session).id)
    for det in fo.determinations:
        det.is_current = 0
    session.flush()
    bio.save_biological_association(session, collection_object_id=co.id,
                                    biological_relationship_id=_rel(session).id,
                                    object_field_occurrence_id=fo.id)
    r = _cmp(session, co, [_tw(9)])
    reasons = sorted(row.reason for row in r.cant_compare)
    assert len(reasons) == 2
    assert any("no current identification" in x for x in reasons)
    assert any("could not be compared" in x for x in reasons)   # the TW one, demoted
    assert r.only_on_tw == ()


def test_unverified_relationship_demotes_the_specimen(session):
    co = make_specimen(session, "ANNX-00310")
    _on(session, co, _rel(session, "feeds on", tw_id=50, host=SANDBOX), _plant(session))
    _on(session, co, _rel(session), _plant(session, "Carex"))
    r = _cmp(session, co, [_tw(9)])
    assert r.to_add == () and r.only_on_tw == () and r.host_differs == ()
    assert len(r.cant_compare) == 3
    assert any("not verified" in row.reason for row in r.cant_compare)


def test_tw_otu_without_taxon_name_cannot_be_compared(session):
    co = make_specimen(session, "ANNX-00311")
    r = _cmp(session, co, [_tw(9, otu=77)], names={77: None})
    (row,) = r.cant_compare
    assert "no taxon name" in row.reason


def test_specimen_as_object(session):
    """A local specimen that is the OBJECT (e.g. a parasitoid reared from it): anchored
    on the object; the other side is the subject specimen's current identification."""
    co = make_specimen(session, "ANNX-00312")
    parasitoid = make_specimen(session, "ANNX-00313")      # not on TaxonWorks
    rel = _rel(session, "parasitoid of", tw_id=60)
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_collection_object_id=parasitoid.id,
                                      object_collection_object_id=co.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    t = TwAssociation(9, 60, "parasitoid of",
                      TwSide("CollectionObject", 777, 5, "CO 777"),
                      TwSide("CollectionObject", 501, 9001, "CO 501"))
    r = _cmp(session, co, [t],
             names={5: TwName(2, "Otiorhynchus crypticus", None, "species")})
    assert r.confirmed_count == 1


def test_association_between_two_specimens_on_tw_is_compared_once(session):
    a = make_specimen(session, "ANNX-00314")
    b = make_specimen(session, "ANNX-00315")
    rel = _rel(session, "parasitoid of", tw_id=60)
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_collection_object_id=a.id,
                                      object_collection_object_id=b.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    t = TwAssociation(9, 60, "parasitoid of",
                      TwSide("CollectionObject", 501, 9001, "CO 501"),
                      TwSide("CollectionObject", 502, 5, "CO 502"))
    r = _cmp(session, a, [t], specimens=[(a.catalog_number, a.id, 501),
                                         (b.catalog_number, b.id, 502)],
             names={5: TwName(2, "Otiorhynchus crypticus", None, "species")})
    # compared once, anchored on the subject — not repeated from the object's side
    assert r.confirmed_count == 1
    assert r.to_add == () and r.only_on_tw == ()


def test_counts_of_what_was_not_compared(session):
    co = make_specimen(session, "ANNX-00316")
    rel, typha = _rel(session), _plant(session)
    not_on_tw = make_specimen(session, "ANNX-00317")
    _on(session, not_on_tw, rel, typha)
    numberless = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number=None,
        repository_id=co.repository_id)
    session.flush()
    _on(session, numberless, rel, typha)
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_taxon_id=species(session).id,
                                      object_collection_object_id=co.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    # a taxon-subject association with a taxon object (no specimen): not this collection's
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_taxon_id=species(session).id,
                                      object_taxon_id=typha.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    r = _cmp(session, co)
    assert (r.not_on_tw_count, r.numberless_count, r.taxon_subject_count) == (1, 1, 1)
    assert r.checked_specimens == 1


# ── association annotations ─────────────────────────────────────────────────────

def test_matched_association_annotations_are_compared(session):
    co = make_specimen(session, "ANNX-00320")
    a = _on(session, co, _rel(session), _plant(session))
    tag = make_term(session, name="Adventive", association=True, tw_id=1764,
                    tw_name="Adventive", tw_host=SFG)
    ann.set_annotations(session, "association", a.id, [AnnotationItem(tag.id)])
    r = _cmp(session, co, [_tw(9)], tw_tags={9: [TwTag(5, "Unknown here")]})
    got = sorted((f.finding.target, f.finding.category, f.finding.name)
                 for f in r.annotation_findings)
    assert got == [("association", "missing_on_tw", "Adventive"),
                   ("association", "unlinked_on_tw", "Unknown here")]
    assert r.annotation_findings[0].tw_specimen_id == 501
    assert r.annotation_findings[0].association == "collected from → Typha L."


def test_to_add_lists_its_annotations(session):
    co = make_specimen(session, "ANNX-00321")
    a = _on(session, co, _rel(session), _plant(session))
    tag = make_term(session, name="Adventive", association=True, tw_id=1764,
                    tw_name="Adventive", tw_host=SFG)
    da = make_term(session, "data_attribute", "Plant part", association=True)
    ann.set_annotations(session, "association", a.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "rhizome")])
    (row,) = _cmp(session, co).to_add
    assert row.annotations == ("tag Adventive",
                               "data attribute Plant part = rhizome (local only — never sent)")


def test_only_on_tw_lists_its_annotations(session):
    co = make_specimen(session, "ANNX-00322")
    r = _cmp(session, co, [_tw(9)], tw_tags={9: [TwTag(1764, "Adventive")]},
             tw_data={9: [TwDatum(3, "Plant part", "rhizome")]})
    (row,) = r.only_on_tw
    assert row.annotations == ("tag Adventive", "data attribute Plant part = rhizome")


def test_unverified_terms_are_listed(session):
    co = make_specimen(session, "ANNX-00323")
    make_term(session, name="Adventive", association=True, tw_id=1764,
              tw_name="Adventive", tw_host=SANDBOX)
    assert _cmp(session, co).unverified_terms == ("Adventive",)


# ── run_association_compare (wiring; fetchers replaced) ─────────────────────────

def test_run_fetches_names_for_the_other_side_and_annotations_by_association(
        session, monkeypatch):
    from app.services.tw_compare import CatalogIndex, TwCatalogEntry
    co = make_specimen(session, "ANNX-00330")
    entry = TwCatalogEntry("ANNX-00330", "00330", 501, None, "ANNX")
    index = CatalogIndex((entry,), {"ANNX-00330": (entry,)})
    calls = {}

    async def fake_assocs(ids):
        calls["assocs"] = ids
        return {9: _tw(9)}

    async def fake_names(ids):
        calls["names"] = ids
        return {1471869: TYPHA}

    async def fake_tags(object_type, ids):
        calls["tags"] = (object_type, ids)
        return {}

    async def fake_data(object_type, ids):
        calls["data"] = (object_type, ids)
        return {}
    monkeypatch.setattr(cmp, "fetch_associations", fake_assocs)
    monkeypatch.setattr(cmp, "fetch_otu_names", fake_names)
    monkeypatch.setattr(cmp.ann_cmp, "fetch_tags", fake_tags)
    monkeypatch.setattr(cmp.ann_cmp, "fetch_data_attributes", fake_data)
    r = asyncio.run(cmp.run_association_compare(
        session, repository_id=co.repository_id, index=index, host=SFG))
    assert calls == {"assocs": [501], "names": [1471869],
                     "tags": ("BiologicalAssociation", [9]),
                     "data": ("BiologicalAssociation", [9])}
    assert len(r.only_on_tw) == 1

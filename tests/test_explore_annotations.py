"""Explore facets for annotations (spec "Explore facets", 2026-10-16): tags and data
attributes are facets; a chip matches a specimen when the annotation is on the specimen,
on its collecting event, or on one of its biological associations — the same in every view."""
import json

from app.models import BiologicalRelationship
from app.models.base import _utcnow
from app.services import annotations as ann
from app.services import biological as bio
import app.services.explore as ex
from app.services.annotations import AnnotationItem
from tests.annotation_helpers import make_event, make_specimen, make_term, species


def _assoc(session, co):
    rel = session.query(BiologicalRelationship).filter_by(name="feeds on").first()
    if rel is None:
        rel = BiologicalRelationship(name="feeds on", created_at=_utcnow(),
                                     updated_at=_utcnow())
        session.add(rel)
        session.flush()
    return bio.save_biological_association(session, collection_object_id=co.id,
                                           biological_relationship_id=rel.id,
                                           object_taxon_id=species(session).id)


def _world(session):
    """Four specimens: tag on the specimen / on its event / on its association / none."""
    tag = make_term(session, "tag", "Adventive", specimen=True, event=True, association=True,
                    css_color="#ff0000")
    da = make_term(session, "data_attribute", "Altitude", specimen=True, event=True,
                   association=True, tw_id=7, tw_name="Altitude")
    ev_s, ev_e, ev_a, ev_n = (make_event(session) for _ in range(4))
    on_spec = make_specimen(session, "EXA-1", event_id=ev_s.id)
    on_event = make_specimen(session, "EXA-2", event_id=ev_e.id)
    on_assoc = make_specimen(session, "EXA-3", event_id=ev_a.id)
    plain = make_specimen(session, "EXA-4", event_id=ev_n.id)
    ann.set_annotations(session, "specimen", on_spec.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "350 m")])
    ann.set_annotations(session, "event", ev_e.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "1200 m")])
    ba = _assoc(session, on_assoc)
    ann.set_annotations(session, "association", ba.id, [AnnotationItem(tag.id)])
    session.flush()
    return dict(tag=tag, da=da, on_spec=on_spec, on_event=on_event, on_assoc=on_assoc,
                plain=plain, ev_s=ev_s, ev_e=ev_e, ev_a=ev_a, ev_n=ev_n)


def _cats(session, filters):
    return {r.catalog for r in ex.query_specimens(session, filters)}


def _chip(f: ex.Facet) -> dict:
    return ex.chip_dict(f)


def _facet(session, term, kind, label):
    return next(f for f in ex.search_facets(session, term)
                if f.kind == kind and f.label == label)


# ── suggestions ──────────────────────────────────────────────────────────────

def test_suggests_tag_with_colour_and_local_flag(session):
    w = _world(session)
    f = _facet(session, "adven", "annotation_term", "Adventive")
    assert f.key == w["tag"].id and f.tag == "Tag"
    assert f.css_color == "#ff0000" and f.local is True


def test_suggests_data_attribute_term_and_value_facets(session):
    w = _world(session)
    f = _facet(session, "altit", "annotation_term", "Altitude")
    assert f.tag == "Data attribute" and f.local is False
    vals = [f for f in ex.search_facets(session, "altitude") if f.kind == "annotation_value"]
    assert {f.label for f in vals} == {"Altitude = 350 m", "Altitude = 1200 m"}
    assert all(f.tag == "Data attribute" for f in vals)
    v = _facet(session, "1200", "annotation_value", "Altitude = 1200 m")
    assert v.key == [w["da"].id, "1200 m"]
    # "name value" tokens across both parts
    assert _facet(session, "alti 350", "annotation_value", "Altitude = 350 m")


def test_tag_terms_offer_no_value_facets(session):
    _world(session)
    assert not [f for f in ex.search_facets(session, "adventive")
                if f.kind == "annotation_value"]


def test_null_offers_annotations_none(session):
    _world(session)
    f = _facet(session, "null", "annotations", "NULL")
    assert f.null is True and f.tag == "Annotations" and f.key is None


# ── matching (anywhere) ──────────────────────────────────────────────────────

def test_tag_matches_on_specimen_event_and_association(session):
    w = _world(session)
    chip = _chip(_facet(session, "adventive", "annotation_term", "Adventive"))
    assert _cats(session, [chip]) == {"EXA-1", "EXA-2", "EXA-3"}


def test_data_attribute_term_matches_any_value(session):
    _world(session)
    chip = _chip(_facet(session, "altitude", "annotation_term", "Altitude"))
    assert _cats(session, [chip]) == {"EXA-1", "EXA-2"}


def test_value_facet_matches_only_that_value(session):
    _world(session)
    chip = _chip(_facet(session, "1200", "annotation_value", "Altitude = 1200 m"))
    assert _cats(session, [chip]) == {"EXA-2"}
    chip = _chip(_facet(session, "350", "annotation_value", "Altitude = 350 m"))
    assert _cats(session, [chip]) == {"EXA-1"}


def test_not_group_excludes_every_placement_and_keeps_unannotated(session):
    _world(session)
    chip = _chip(_facet(session, "adventive", "annotation_term", "Adventive"))
    assert _cats(session, [{"op": "not", "facets": [chip]}]) == {"EXA-4"}
    vchip = _chip(_facet(session, "1200", "annotation_value", "Altitude = 1200 m"))
    assert _cats(session, [{"op": "not", "facets": [vchip]}]) == {"EXA-1", "EXA-3", "EXA-4"}


def test_annotations_none_and_its_negation(session):
    _world(session)
    chip = _chip(_facet(session, "null", "annotations", "NULL"))
    assert _cats(session, [chip]) == {"EXA-4"}
    assert _cats(session, [{"op": "not", "facets": [chip]}]) == {"EXA-1", "EXA-2", "EXA-3"}


def test_specimen_without_event_is_handled(session):
    tag = make_term(session, "tag", "Lonely")
    co = make_specimen(session, "EXA-9")            # no collecting event at all
    chip = _chip(_facet(session, "lonely", "annotation_term", "Lonely"))
    assert _cats(session, [chip]) == set()
    assert _cats(session, [{"op": "not", "facets": [chip]}]) == {"EXA-9"}
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(tag.id)])
    assert _cats(session, [chip]) == {"EXA-9"}


def test_events_view_is_the_events_of_matching_specimens(session):
    w = _world(session)
    chip = _chip(_facet(session, "adventive", "annotation_term", "Adventive"))
    ids = {g.event_id for g in ex.events(session, [chip])}
    assert ids == {w["ev_s"].id, w["ev_e"].id, w["ev_a"].id}


def test_chip_dict_round_trips_through_json(session):
    _world(session)
    for term, kind, label in (("adventive", "annotation_term", "Adventive"),
                              ("1200", "annotation_value", "Altitude = 1200 m"),
                              ("null", "annotations", "NULL")):
        chip = _chip(_facet(session, term, kind, label))
        back = json.loads(json.dumps(chip))
        assert back == chip
        assert _cats(session, [back]) == _cats(session, [chip])
    chip = _chip(_facet(session, "adventive", "annotation_term", "Adventive"))
    assert chip["css_color"] == "#ff0000" and chip["local"] is True


def test_suggestion_html_shows_term_colour_and_local_badge():
    from app.ui.explore import facet_value_html
    html = facet_value_html("annotation_term", "Adventive", 1, "#0000ff", True)
    assert "background:#0000ff" in html and "color:#fff" in html and "local" in html
    html = facet_value_html("annotation_value", "Altitude = 350 m", [3, "350 m"], None, False)
    assert ">Altitude<" in html and "350 m" in html and "local" not in html
    assert facet_value_html("taxon", "A<b>", 1) == "<span>A&lt;b&gt;</span>"

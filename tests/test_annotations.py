"""annotations.py — the local vocabulary and the annotations on records (spec: Data model,
Service layer, Merge rules, Staged annotations)."""
import re

import pytest

import app.services.annotations as ann
from app.services.annotations import AnnotationItem
from tests.annotation_helpers import SFG, make_event, make_specimen, make_term


# ── colour and value validation ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("#abc", "#abc"), (" #AABBCC ", "#AABBCC"), ("", None), (None, None), ("  ", None)])
def test_normalize_css_color_accepts(raw, expected):
    assert ann.normalize_css_color(raw) == expected


@pytest.mark.parametrize("raw", ["red", "#abcd", "abc", "#12345", "#ggg"])
def test_normalize_css_color_refuses_and_names_the_value(raw):
    with pytest.raises(ValueError, match=re.escape(raw)):
        ann.normalize_css_color(raw)


@pytest.mark.parametrize("raw,colour,warns", [
    ("#254d0d", "#254d0d", False), (None, None, False), ("", None, False),
    ("red", None, True), ("#12345", None, True), (12, None, True)])
def test_tw_css_color_never_converts(raw, colour, warns):
    got, warning = ann.tw_css_color(raw)
    assert got == colour
    assert (warning is not None) is warns


def test_clean_value():
    assert ann.clean_value("  G1.6 ") == "G1.6"
    for bad in ("", "   ", None, "a\tb", "a\nb", "a\rb"):
        with pytest.raises(ValueError):
            ann.clean_value(bad)


@pytest.mark.parametrize("bad, why", [
    ("G1  6", "repeated spaces"),
    ("G1\u00a06", "non-breaking space"),
    ("G1\u20036", "U\\+2003"),
])
def test_clean_value_refuses_what_taxonworks_would_rewrite(bad, why):
    """TW's normalize_value! squeezes spaces / strips NBSP — a value it rewrites would be
    a permanent value_differs, so it is refused (not normalised) and named."""
    with pytest.raises(ValueError, match=why):
        ann.clean_value(bad)


def test_clean_value_strips_edge_nbsp_and_keeps_single_spaces():
    assert ann.clean_value("\u00a0G1 6 ") == "G1 6"


# ── terms ──────────────────────────────────────────────────────────────────────

def test_create_term_refuses_case_insensitive_duplicate(session):
    ann.create_term(session, kind="tag", name="Adventive")
    with pytest.raises(ValueError, match="already exists"):
        ann.create_term(session, kind="tag", name="adventive")
    ann.create_term(session, kind="data_attribute", name="adventive")   # other kind: fine


def test_create_term_refuses_unknown_kind(session):
    with pytest.raises(ValueError):
        ann.create_term(session, kind="note", name="x")


def test_local_colour_refused_while_linked(session):
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG, css_color="#ff8800")
    with pytest.raises(ValueError, match="TaxonWorks"):
        ann.update_term(session, t.id, css_color="#000")
    ann.update_term(session, t.id, css_color="#ff8800")      # unchanged: not an edit


def test_kind_change_refused_while_used_or_linked(session):
    t = make_term(session)
    co = make_specimen(session, "ANNX-00001")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    with pytest.raises(ValueError, match="used by 1"):
        ann.update_term(session, t.id, kind="data_attribute")
    linked = make_term(session, name="Linked", tw_id=5, tw_name="Linked")
    with pytest.raises(ValueError, match="Unlink"):
        ann.update_term(session, linked.id, kind="data_attribute")


def test_delete_refused_while_used(session):
    t = make_term(session)
    co = make_specimen(session, "ANNX-00002")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    with pytest.raises(ValueError, match="still used by 1"):
        ann.delete_term(session, t.id)


# ── set (replace) and add (staged) ─────────────────────────────────────────────

def test_set_annotations_replaces_the_set(session):
    tag = make_term(session)
    da = make_term(session, "data_attribute", "EUNIS habitat")
    co = make_specimen(session, "ANNX-00003")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, " G1.6 ")])
    views = ann.views_for(session, "specimen", co.id)
    assert [(v.name, v.value) for v in views] == [("Adventive", None), ("EUNIS habitat", "G1.6")]
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(da.id, "G1.7")])
    assert [(v.name, v.value) for v in ann.views_for(session, "specimen", co.id)] == [
        ("EUNIS habitat", "G1.7")]


def test_a_new_term_must_be_allowed_but_an_existing_one_stays(session):
    t = make_term(session, specimen=True)
    co = make_specimen(session, "ANNX-00004")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    ann.update_term(session, t.id, allow_specimen=False)
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])   # kept: fine
    other = make_term(session, name="Other", specimen=False)
    with pytest.raises(ValueError, match="not offered for specimens"):
        ann.set_annotations(session, "specimen", co.id,
                            [AnnotationItem(t.id), AnnotationItem(other.id)])


def test_tag_with_value_and_duplicate_term_refused(session):
    t = make_term(session)
    co = make_specimen(session, "ANNX-00005")
    with pytest.raises(ValueError, match="no value"):
        ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id, "x")])
    with pytest.raises(ValueError, match="twice"):
        ann.set_annotations(session, "specimen", co.id,
                            [AnnotationItem(t.id), AnnotationItem(t.id)])


def test_refused_set_annotations_leaves_the_existing_set_unchanged(session):
    keep = make_term(session, name="Keep")
    drop = make_term(session, name="Drop")
    other = make_term(session, name="NotOffered", specimen=False)
    co = make_specimen(session, "ANNX-00006")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(keep.id), AnnotationItem(drop.id)])
    with pytest.raises(ValueError, match="not offered"):
        ann.set_annotations(session, "specimen", co.id,
                            [AnnotationItem(keep.id), AnnotationItem(other.id)])
    session.flush()
    assert {a.term_id for a in ann.annotations_for(session, "specimen", co.id)} \
        == {keep.id, drop.id}


def test_kind_change_onto_a_taken_name_is_refused_by_name(session):
    make_term(session, "data_attribute", "Habitat")
    tag = make_term(session, "tag", "Habitat")
    with pytest.raises(ValueError, match="Habitat"):
        ann.update_term(session, tag.id, kind="data_attribute")
    assert tag.kind == "tag"


def test_add_annotations_is_additive_and_idempotent(session):
    tag = make_term(session, event=True)
    da = make_term(session, "data_attribute", "EUNIS habitat", event=True)
    ev = make_event(session)
    ann.add_annotations(session, "event", ev.id, [AnnotationItem(tag.id)])
    ann.add_annotations(session, "event", ev.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "G1.6")])
    ann.add_annotations(session, "event", ev.id, [AnnotationItem(da.id, "G1.6")])
    assert len(ann.annotations_for(session, "event", ev.id)) == 2


def test_add_annotations_conflicting_value_refuses(session):
    """Review Focus 3: a staged value on a reused event that already has another value."""
    da = make_term(session, "data_attribute", "EUNIS habitat", event=True)
    ev = make_event(session)
    ann.add_annotations(session, "event", ev.id, [AnnotationItem(da.id, "G1.6")])
    with pytest.raises(ValueError) as exc:
        ann.add_annotations(session, "event", ev.id, [AnnotationItem(da.id, "G1.7")])
    assert "G1.6" in str(exc.value) and "G1.7" in str(exc.value)
    assert [a.value for a in ann.annotations_for(session, "event", ev.id)] == ["G1.6"]


# ── merge ──────────────────────────────────────────────────────────────────────

def test_merge_refuses_kind_mismatch_and_a_linked_absorbed_term(session):
    tag = make_term(session)
    da = make_term(session, "data_attribute", "Adventive")
    with pytest.raises(ValueError, match="kind"):
        ann.merge_terms(session, keep_id=tag.id, absorb_id=da.id)
    linked = make_term(session, name="Linked", tw_id=9, tw_name="Linked")
    with pytest.raises(ValueError, match="Unlink"):
        ann.merge_terms(session, keep_id=tag.id, absorb_id=linked.id)


def test_merge_folds_duplicates_and_repoints_the_rest(session):
    keep = make_term(session, name="Adventive")
    absorb = make_term(session, name="adventive (typo)")
    co1 = make_specimen(session, "ANNX-00010")
    co2 = make_specimen(session, "ANNX-00011")
    ann.set_annotations(session, "specimen", co1.id,
                        [AnnotationItem(keep.id), AnnotationItem(absorb.id)])
    ann.set_annotations(session, "specimen", co2.id, [AnnotationItem(absorb.id)])
    plan = ann.merge_terms(session, keep_id=keep.id, absorb_id=absorb.id)
    assert (plan.fold, plan.repoint, plan.conflicts) == (1, 1, ())
    assert [v.name for v in ann.views_for(session, "specimen", co1.id)] == ["Adventive"]
    assert [v.name for v in ann.views_for(session, "specimen", co2.id)] == ["Adventive"]
    assert all(t.id != absorb.id for t in ann.list_terms(session))


def test_merge_refuses_conflicting_values(session):
    """Review Focus 2: one record, both terms, different values — never pick one."""
    keep = make_term(session, "data_attribute", "EUNIS habitat")
    absorb = make_term(session, "data_attribute", "EUNIS")
    co = make_specimen(session, "ANNX-00012")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(keep.id, "G1.6"), AnnotationItem(absorb.id, "G1.7")])
    preview = ann.merge_preview(session, keep_id=keep.id, absorb_id=absorb.id)
    assert preview.conflicts == (f"specimen #{co.id}: 'G1.6' vs 'G1.7'",)
    with pytest.raises(ValueError, match=f"specimen #{co.id}"):
        ann.merge_terms(session, keep_id=keep.id, absorb_id=absorb.id)


def test_views_report_colour_and_link(session):
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG, css_color="#ff8800")
    co = make_specimen(session, "ANNX-00013")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    (v,) = ann.views_for(session, "specimen", co.id)
    assert (v.css_color, v.linked) == ("#ff8800", True)

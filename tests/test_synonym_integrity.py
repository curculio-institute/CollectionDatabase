"""Synonym-integrity service ops, guard trigger, and the manual audit.

Atomic-name model (Epic #30): a name is parented under its OWN lineage, so a
synonym keeps its own parent and its own name; status is encoded solely by
acceptedNameUsageID. The only surviving write-time invariant is:
  * acceptedNameUsageID points to an accepted name, never a synonym (CHAINED_SYNONYM)
The strict synonym-parent-match rule (trigger + SYNONYM_PARENT_MISMATCH audit)
was retired in migration 0033.
"""
import pathlib
import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import (
    synonymize, make_accepted, reparent, verify_taxon_consistency,
)


def test_parent_and_accepted_writes_are_centralised():
    """Chokepoint guard: every mutation of parentNameUsageID / acceptedNameUsageID
    on an existing row must live in app/services/taxa.py, which routes them through
    synonymize / make_accepted / reparent so the synonym invariants hold. A direct
    write elsewhere bypasses the cascades — fail loudly here."""
    pat = re.compile(r"\.(parent_name_usage_id|accepted_name_usage_id)\s*=(?!=)")
    app = pathlib.Path("app")
    allowed = app / "services" / "taxa.py"
    offenders = [
        f"{p}:{n}: {line.strip()}"
        for p in app.rglob("*.py") if p != allowed
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if pat.search(line)
    ]
    assert not offenders, (
        "direct parent/accepted writes outside taxa.py bypass the synonym cascades:\n"
        + "\n".join(offenders)
    )


def mk(session, name, *, rank="species", parent=None, code="ICZN"):
    """Create an accepted taxon (no synonym link, so setup never trips a trigger)."""
    t = Taxon(scientific_name=name, taxon_rank=rank,
              parent_name_usage_id=(parent.id if parent else None),
              nomenclatural_code=code, created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    return t


# ---------------------------------------------------------------------------
# synonymize
# ---------------------------------------------------------------------------

def test_synonymize_keeps_own_parent(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    accepted = mk(session, "Otiorhynchus norici", parent=genus)
    other_genus = mk(session, "Curculio", rank="genus")
    syn = mk(session, "Curculio rubidus", parent=other_genus)

    synonymize(session, name_id=syn.id, accepted_id=accepted.id)
    session.refresh(syn)
    assert syn.accepted_name_usage_id == accepted.id
    assert syn.parent_name_usage_id == other_genus.id   # kept its OWN lineage
    assert syn.scientific_name == "Curculio rubidus"    # no name rewrite


def test_synonymize_resolves_target_to_terminal(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    accepted = mk(session, "Otiorhynchus norici", parent=genus)
    mid = mk(session, "Otiorhynchus midicus", parent=genus)
    synonymize(session, name_id=mid.id, accepted_id=accepted.id)  # mid → accepted
    newsyn = mk(session, "Otiorhynchus novus", parent=genus)
    # Point at the synonym `mid`; must land on the terminal accepted name.
    synonymize(session, name_id=newsyn.id, accepted_id=mid.id)
    session.refresh(newsyn)
    assert newsyn.accepted_name_usage_id == accepted.id


def test_synonymize_flattens_existing_synonyms(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    b = mk(session, "Otiorhynchus b", parent=genus)
    sub = mk(session, "Otiorhynchus sub", parent=genus)
    synonymize(session, name_id=sub.id, accepted_id=b.id)   # sub → b
    synonymize(session, name_id=b.id, accepted_id=a.id)     # b → a; sub must follow
    session.refresh(sub); session.refresh(b)
    assert b.accepted_name_usage_id == a.id
    assert sub.accepted_name_usage_id == a.id               # flattened, not chained


def test_synonymize_rejects_self_and_code_mismatch(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    with pytest.raises(ValueError):
        synonymize(session, name_id=a.id, accepted_id=a.id)
    plant_genus = mk(session, "Rosa", rank="genus", code="ICN")
    plant = mk(session, "Rosa canina", parent=plant_genus, code="ICN")
    with pytest.raises(ValueError):
        synonymize(session, name_id=a.id, accepted_id=plant.id)


def test_synonymize_rejects_name_with_children(session):
    fam = mk(session, "Curculionidae", rank="family")
    genus = mk(session, "Otiorhynchus", rank="genus", parent=fam)
    mk(session, "Otiorhynchus norici", parent=genus)              # subordinate taxon
    other = mk(session, "Brachyderidae", rank="family")
    with pytest.raises(ValueError):
        synonymize(session, name_id=genus.id, accepted_id=other.id)


# ---------------------------------------------------------------------------
# make_accepted / reparent
# ---------------------------------------------------------------------------

def test_make_accepted_clears_link(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s = mk(session, "Otiorhynchus s", parent=genus)
    synonymize(session, name_id=s.id, accepted_id=a.id)
    make_accepted(session, s.id)
    session.refresh(s)
    assert s.accepted_name_usage_id is None


def test_status_toggle_is_a_pure_one_field_flip(session):
    """synonymize → make_accepted round-trip touches only acceptedNameUsageID:
    parentNameUsageID and scientific_name are unchanged throughout."""
    g_acc = mk(session, "Otiorhynchus", rank="genus")
    g_own = mk(session, "Curculio", rank="genus")
    accepted = mk(session, "Otiorhynchus fortis", parent=g_acc)
    name = mk(session, "Curculio forticollis", parent=g_own)
    before_parent = name.parent_name_usage_id
    before_sci = name.scientific_name

    synonymize(session, name_id=name.id, accepted_id=accepted.id)
    session.refresh(name)
    assert name.accepted_name_usage_id == accepted.id
    assert name.parent_name_usage_id == before_parent   # own genus, unmoved
    assert name.scientific_name == before_sci

    make_accepted(session, name.id)
    session.refresh(name)
    assert name.accepted_name_usage_id is None
    assert name.parent_name_usage_id == before_parent
    assert name.scientific_name == before_sci


def test_reparent_leaves_synonyms_alone(session):
    """In the atomic model a synonym carries its own lineage, so re-homing an
    accepted name does NOT move its synonyms."""
    g1 = mk(session, "Otiorhynchus", rank="genus")
    g2 = mk(session, "Otiorhynchus2", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=g1)
    s = mk(session, "Otiorhynchus s", parent=g1)
    synonymize(session, name_id=s.id, accepted_id=a.id)
    reparent(session, taxon_id=a.id, new_parent_id=g2.id)
    session.refresh(a); session.refresh(s)
    assert a.parent_name_usage_id == g2.id
    assert s.parent_name_usage_id == g1.id     # synonym kept its own parent


def test_reparent_refuses_synonym(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s = mk(session, "Otiorhynchus s", parent=genus)
    synonymize(session, name_id=s.id, accepted_id=a.id)
    with pytest.raises(ValueError):
        reparent(session, taxon_id=s.id, new_parent_id=genus.id)


# ---------------------------------------------------------------------------
# guard trigger (loud DB-level failure from any write path)
# ---------------------------------------------------------------------------

def test_synonym_with_foreign_parent_is_allowed(session):
    """The retired strict rule used to reject this; in the atomic model a synonym
    under a different genus than its accepted name is the normal case."""
    genus = mk(session, "Otiorhynchus", rank="genus")
    other = mk(session, "Curculio", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s = mk(session, "Curculio s", parent=other)
    s.accepted_name_usage_id = a.id            # foreign parent, no longer rejected
    session.flush()                            # must NOT raise
    session.refresh(s)
    assert s.parent_name_usage_id == other.id


def test_trigger_blocks_chained_synonym(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s1 = mk(session, "Otiorhynchus s1", parent=genus)
    synonymize(session, name_id=s1.id, accepted_id=a.id)   # s1 is now a synonym
    s2 = mk(session, "Otiorhynchus s2", parent=genus)
    s2.accepted_name_usage_id = s1.id                      # points at a synonym → chained
    with pytest.raises(DatabaseError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# verify_taxon_consistency (manual audit)
# ---------------------------------------------------------------------------

def test_verify_clean_for_cross_genus_synonym(session):
    """A synonym under a *different* genus than its accepted name is valid in the
    atomic model — the audit reports no issue (the retired SYNONYM_PARENT_MISMATCH
    would have flagged it)."""
    g_acc = mk(session, "Otiorhynchus", rank="genus")
    g_own = mk(session, "Curculio", rank="genus")
    accepted = mk(session, "Otiorhynchus fortis", parent=g_acc)
    syn = mk(session, "Curculio forticollis", parent=g_own)
    synonymize(session, name_id=syn.id, accepted_id=accepted.id)
    assert verify_taxon_consistency(session) == []


def test_verify_reports_chained_synonym_from_raw_sql(session):
    """The audit still catches drift the surviving trigger can't see at write
    time: a chained synonym introduced by raw SQL (accepted name later becomes a
    synonym itself)."""
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    mid = mk(session, "Otiorhynchus mid", parent=genus)
    s = mk(session, "Otiorhynchus s", parent=genus)
    synonymize(session, name_id=s.id, accepted_id=mid.id)
    assert verify_taxon_consistency(session) == []
    # Make `mid` a synonym of `a` via raw SQL — no trigger re-checks the existing
    # rows that point at `mid`, so `s` is now chained through `mid`.
    session.execute(
        text('UPDATE taxon SET "dwc:acceptedNameUsageID" = :a WHERE id = :i'),
        {"a": a.id, "i": mid.id},
    )
    session.expire_all()   # raw SQL bypassed the ORM; re-read like a fresh session
    issues = verify_taxon_consistency(session)
    kinds = {i["issue"] for i in issues}
    assert "CHAINED_SYNONYM" in kinds
    assert any(i["taxon_id"] == s.id for i in issues)


def test_cross_genus_synonym_composes_to_own_name(session):
    """A cross-genus synonym composes from its own lineage (own genus), not its
    accepted name's — composition is uniform for valid names and synonyms."""
    from app.services.taxa import compose_scientific_name

    def mke(name, element, *, rank="species", parent=None, code="ICZN"):
        t = Taxon(scientific_name=name, name_element=element, taxon_rank=rank,
                  parent_name_usage_id=(parent.id if parent else None),
                  nomenclatural_code=code, created_at=_utcnow(), updated_at=_utcnow())
        session.add(t); session.flush()
        return t

    g_acc = mke("Otiorhynchus", "Otiorhynchus", rank="genus")
    g_own = mke("Curculio", "Curculio", rank="genus")
    accepted = mke("Otiorhynchus fortis", "fortis", parent=g_acc)
    syn = mke("Curculio forticollis", "forticollis", parent=g_own)
    synonymize(session, name_id=syn.id, accepted_id=accepted.id)
    session.refresh(syn)
    # Composes under its OWN genus, and still links to the accepted name (so the
    # tree displays it under Otiorhynchus fortis via acceptedNameUsageID).
    assert compose_scientific_name(session, syn) == "Curculio forticollis"
    assert syn.accepted_name_usage_id == accepted.id

"""Synonym-integrity service ops, guard triggers, and the manual audit.

Invariants (migration 0031 triggers + service ops):
  * a synonym's parent == its accepted name's parent (SYNONYM_PARENT_MISMATCH)
  * acceptedNameUsageID points to an accepted name, never a synonym (CHAINED_SYNONYM)
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

def test_synonymize_copies_accepted_parent(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    accepted = mk(session, "Otiorhynchus norici", parent=genus)
    other_genus = mk(session, "Curculio", rank="genus")
    syn = mk(session, "Curculio rubidus", parent=other_genus)

    synonymize(session, name_id=syn.id, accepted_id=accepted.id)
    session.refresh(syn)
    assert syn.accepted_name_usage_id == accepted.id
    assert syn.parent_name_usage_id == genus.id   # took the accepted name's parent


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


def test_reparent_cascades_to_synonyms(session):
    g1 = mk(session, "Otiorhynchus", rank="genus")
    g2 = mk(session, "Otiorhynchus2", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=g1)
    s = mk(session, "Otiorhynchus s", parent=g1)
    synonymize(session, name_id=s.id, accepted_id=a.id)
    reparent(session, taxon_id=a.id, new_parent_id=g2.id)
    session.refresh(a); session.refresh(s)
    assert a.parent_name_usage_id == g2.id
    assert s.parent_name_usage_id == g2.id     # synonym followed its accepted name


def test_reparent_refuses_synonym(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s = mk(session, "Otiorhynchus s", parent=genus)
    synonymize(session, name_id=s.id, accepted_id=a.id)
    with pytest.raises(ValueError):
        reparent(session, taxon_id=s.id, new_parent_id=genus.id)


# ---------------------------------------------------------------------------
# guard triggers (loud DB-level failure from any write path)
# ---------------------------------------------------------------------------

def test_trigger_blocks_synonym_with_wrong_parent(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    other = mk(session, "Curculio", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s = mk(session, "Otiorhynchus s", parent=genus)
    s.accepted_name_usage_id = a.id
    s.parent_name_usage_id = other.id          # != accepted's parent → Inv1
    with pytest.raises(DatabaseError):
        session.flush()
    session.rollback()


def test_trigger_blocks_chained_synonym(session):
    genus = mk(session, "Otiorhynchus", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=genus)
    s1 = mk(session, "Otiorhynchus s1", parent=genus)
    synonymize(session, name_id=s1.id, accepted_id=a.id)   # s1 is now a synonym
    s2 = mk(session, "Otiorhynchus s2", parent=genus)
    s2.parent_name_usage_id = s1.parent_name_usage_id      # satisfy Inv1 in isolation
    s2.accepted_name_usage_id = s1.id                      # points at a synonym → Inv2
    with pytest.raises(DatabaseError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# verify_taxon_consistency (manual audit)
# ---------------------------------------------------------------------------

def test_verify_reports_drift_from_raw_reparent(session):
    g1 = mk(session, "Otiorhynchus", rank="genus")
    g2 = mk(session, "Otiorhynchus2", rank="genus")
    a = mk(session, "Otiorhynchus a", parent=g1)
    s = mk(session, "Otiorhynchus s", parent=g1)
    synonymize(session, name_id=s.id, accepted_id=a.id)
    assert verify_taxon_consistency(session) == []
    # Re-parent the accepted name via raw SQL (no trigger fires on the accepted
    # row) → the synonym's stored parent goes stale. The audit must catch it.
    session.execute(
        text('UPDATE taxon SET "dwc:parentNameUsageID" = :p WHERE id = :i'),
        {"p": g2.id, "i": a.id},
    )
    session.expire_all()   # raw SQL bypassed the ORM; re-read like a fresh session
    issues = verify_taxon_consistency(session)
    kinds = {i["issue"] for i in issues}
    assert "SYNONYM_PARENT_MISMATCH" in kinds
    assert any(i["taxon_id"] == s.id for i in issues)

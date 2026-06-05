"""Schema constraint tests — each test verifies that the DB rejects invalid data.

A silent wrong value is worse than a loud failure (CLAUDE.md §2).
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Taxon,
    CollectingEvent,
    CollectionObject,
    TaxonDetermination,
    BiologicalRelationship,
    BiologicalAssociation,
)
from app.models.base import _utcnow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _taxon(session, name="Carabus coriaceus") -> Taxon:
    parts = name.split()
    sci_name = " ".join(parts[:2]) if len(parts) >= 2 else parts[0]
    rank = "species" if len(parts) >= 2 else "genus"
    t = Taxon(
        scientific_name=sci_name,
        taxon_rank=rank,
        taxonomic_status="accepted",
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


def _event(session) -> CollectingEvent:
    ce = CollectingEvent(created_at=_utcnow(), updated_at=_utcnow())
    session.add(ce)
    session.flush()
    return ce


_obj_counter = 0

def _obj(session, collecting_event=None, cat_num: str | None = None, cat_ns: str = "TEST") -> CollectionObject:
    global _obj_counter
    _obj_counter += 1
    co = CollectionObject(
        collecting_event_id=collecting_event.id if collecting_event else None,
        catalog_number=cat_num or f"T-{_obj_counter:04d}",
        catalog_namespace=cat_ns,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(co)
    session.flush()
    return co


def _rel(session, name="collected_on") -> BiologicalRelationship:
    # Use the seeded relationship if it exists, otherwise insert one
    from sqlalchemy import text
    row = session.execute(
        text("SELECT id FROM biological_relationship WHERE name = :n"), {"n": name}
    ).first()
    if row:
        from sqlalchemy.orm import Session
        return session.get(BiologicalRelationship, row[0])
    br = BiologicalRelationship(
        name=name, created_at=_utcnow(), updated_at=_utcnow()
    )
    session.add(br)
    session.flush()
    return br


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def test_seed_biological_relationships(session):
    from sqlalchemy import text
    rows = session.execute(
        text("SELECT name FROM biological_relationship ORDER BY name")
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"collected_on", "feeds_on", "parasitizes", "reared_from", "associated_with"} <= names


# ---------------------------------------------------------------------------
# Foreign key constraints
# ---------------------------------------------------------------------------

def test_fk_collection_object_rejects_missing_event(session):
    co = CollectionObject(
        collecting_event_id=999999,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(co)
    with pytest.raises(IntegrityError):
        session.flush()


def test_synonymization_link(session):
    """Synonymising a taxon: set taxonomicStatus + acceptedNameUsageID."""
    accepted = _taxon(session, "Curtonotus aeneus")
    synonym  = _taxon(session, "Amara aenea")
    synonym.accepted_name_usage_id = accepted.id
    session.flush()
    session.refresh(synonym)
    assert synonym.accepted_name_usage_id == accepted.id


def test_accepted_name_usage_fk_rejects_missing_taxon(session):
    """acceptedNameUsageID must point to an existing taxon row."""
    t = _taxon(session, "Amara aenea")
    t.accepted_name_usage_id = 999999
    with pytest.raises(IntegrityError):
        session.flush()


def test_fk_restrict_blocks_deleting_accepted_taxon(session):
    """Cannot delete an accepted taxon while a synonym still points to it."""
    accepted = _taxon(session, "Curtonotus aeneus")
    synonym  = _taxon(session, "Amara aenea")
    synonym.taxonomic_status = "synonym"
    synonym.accepted_name_usage_id = accepted.id
    session.flush()
    session.delete(accepted)
    with pytest.raises(IntegrityError):
        session.flush()


def test_fk_taxon_determination_rejects_missing_taxon(session):
    co = _obj(session)
    td = TaxonDetermination(
        collection_object_id=co.id,
        taxon_id=999999,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(td)
    with pytest.raises(IntegrityError):
        session.flush()


def test_fk_restrict_delete_taxon_with_determination(session):
    """Deleting a Taxon referenced by a TaxonDetermination should be blocked."""
    t = _taxon(session, "Dytiscus marginalis")
    co = _obj(session)
    td = TaxonDetermination(
        collection_object_id=co.id,
        taxon_id=t.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(td)
    session.flush()
    session.delete(t)
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# CHECK constraints — collecting_event
# ---------------------------------------------------------------------------

def test_check_latitude_too_high(session):
    ce = CollectingEvent(
        decimal_latitude=91.0, decimal_longitude=10.0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_latitude_too_low(session):
    ce = CollectingEvent(
        decimal_latitude=-91.0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_longitude_out_of_range(session):
    ce = CollectingEvent(
        decimal_latitude=50.0, decimal_longitude=181.0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_uncertainty_negative(session):
    ce = CollectingEvent(
        coordinate_uncertainty_in_meters=-1.0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_country_code_length(session):
    ce = CollectingEvent(
        country_code="DEU",  # 3 chars — must be exactly 2
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    with pytest.raises(IntegrityError):
        session.flush()


def test_valid_coordinates_accepted(session):
    ce = CollectingEvent(
        decimal_latitude=48.137, decimal_longitude=11.575,
        country_code="DE", coordinate_uncertainty_in_meters=50.0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    session.flush()
    assert ce.id is not None


# ---------------------------------------------------------------------------
# CHECK constraints — collection_object
# ---------------------------------------------------------------------------

def test_check_individual_count_negative(session):
    co = CollectionObject(
        individual_count=-1,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(co)
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# Exclusive-arc CHECK — biological_association
# ---------------------------------------------------------------------------

def test_ba_subject_both_set_rejected(session):
    t = _taxon(session)
    co = _obj(session)
    co2 = _obj(session)
    br = _rel(session)
    ba = BiologicalAssociation(
        biological_relationship_id=br.id,
        subject_collection_object_id=co.id,
        subject_taxon_id=t.id,        # both set — violates exclusive arc
        object_collection_object_id=co2.id,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ba)
    with pytest.raises(IntegrityError):
        session.flush()


def test_ba_subject_neither_set_rejected(session):
    co = _obj(session)
    br = _rel(session)
    ba = BiologicalAssociation(
        biological_relationship_id=br.id,
        subject_collection_object_id=None,
        subject_taxon_id=None,         # neither set — violates exclusive arc
        object_collection_object_id=co.id,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ba)
    with pytest.raises(IntegrityError):
        session.flush()


def test_ba_object_both_set_rejected(session):
    t = _taxon(session)
    co = _obj(session)
    co2 = _obj(session)
    br = _rel(session)
    ba = BiologicalAssociation(
        biological_relationship_id=br.id,
        subject_collection_object_id=co.id,
        object_collection_object_id=co2.id,
        object_taxon_id=t.id,          # both set — violates exclusive arc
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ba)
    with pytest.raises(IntegrityError):
        session.flush()


def test_catalog_number_unique_within_namespace(session):
    """Same catalogNumber in the same namespace must be rejected."""
    for _ in range(2):
        co = CollectionObject(
            catalog_namespace="Jilg",
            catalog_number="0001",
            created_at=_utcnow(), updated_at=_utcnow(),
        )
        session.add(co)
    with pytest.raises(IntegrityError):
        session.flush()


def test_catalog_number_same_number_different_namespace_allowed(session):
    """Same number in different namespaces is fine."""
    for ns in ("Jilg", "MFNB"):
        session.add(CollectionObject(
            catalog_namespace=ns, catalog_number="0001",
            created_at=_utcnow(), updated_at=_utcnow(),
        ))
    session.flush()  # must not raise


def test_catalog_number_required(session):
    """Inserting a collection_object without catalogNumber must be rejected."""
    co = CollectionObject(
        catalog_namespace="Jilg",
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(co)
    with pytest.raises(IntegrityError):
        session.flush()


def test_ba_specimen_on_host_taxon_accepted(session):
    """Typical case: beetle specimen collected_on host-plant taxon."""
    host = _taxon(session, "Quercus robur")
    beetle_event = _event(session)
    beetle = _obj(session, beetle_event)
    br = _rel(session, "collected_on")
    ba = BiologicalAssociation(
        biological_relationship_id=br.id,
        subject_collection_object_id=beetle.id,
        object_taxon_id=host.id,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ba)
    session.flush()
    assert ba.id is not None

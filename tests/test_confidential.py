"""Confidential privacy flag on person / collection_object / collecting_event.

Local-only flag (migration 0043) that drives DwC export withholding:
- confidential person → name obscured in export;
- confidential specimen / event → dropped from export.
This module covers persistence + the 0/1 CHECK; export wiring lands with Phase 3.
"""
import pytest
from sqlalchemy.exc import IntegrityError

import app.services.persons as persons_svc
from app.services.events import create_collecting_event, update_collecting_event
from app.services.specimens import create_collection_object, update_collection_object
from app.models import Person, CollectionObject, CollectingEvent


def test_person_confidential_round_trip(session):
    p = persons_svc.create_person(session, full_name="Jane Doe", confidential=True)
    session.flush()
    assert p.confidential == 1
    persons_svc.update_person(session, p.id, full_name="Jane Doe", confidential=False)
    assert session.get(Person, p.id).confidential == 0


def test_person_defaults_not_confidential(session):
    p = persons_svc.create_person(session, full_name="Public Collector")
    assert p.confidential == 0


def test_specimen_confidential_round_trip(session):
    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="cf01", collection_code="Test", institution_code="Test",
        confidential=1,
    )
    assert session.get(CollectionObject, co.id).confidential == 1
    update_collection_object(session, co.id, confidential=0)
    assert session.get(CollectionObject, co.id).confidential == 0


def test_event_confidential_round_trip(session):
    ce = create_collecting_event(session, locality="Secret site", confidential=1)
    assert session.get(CollectingEvent, ce.id).confidential == 1
    update_collecting_event(session, ce.id, confidential=0)
    assert session.get(CollectingEvent, ce.id).confidential == 0


def test_confidential_check_rejects_out_of_range(session):
    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="cf02", collection_code="Test", institution_code="Test",
    )
    session.flush()
    with pytest.raises(IntegrityError):
        co.confidential = 2
        session.flush()

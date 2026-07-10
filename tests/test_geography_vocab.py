"""Geography controlled vocabularies on collecting_event (#40):
country / stateProvince / administrative_region / county / island resolved by FK,
created via the events service, mergeable, searchable."""
import app.services.events as ev_svc
from app.models import CollectingEvent, Country, StateProvince, AdministrativeRegion, County
from app.services.vocabularies import country_vocab, state_province_vocab


def test_create_resolves_geo_names_to_fk_rows(session):
    ev = ev_svc.create_collecting_event(
        session,
        country="Germany", state_province="Bavaria",
        administrative_region="Oberbayern", county="Landkreis Berchtesgadener Land",
        locality="Königssee",
    )
    session.flush()
    got = session.get(CollectingEvent, ev.id)
    assert got.country_obj.name == "Germany"
    assert got.state_province_obj.name == "Bavaria"
    assert got.administrative_region_obj.name == "Oberbayern"
    assert got.county_obj.name == "Landkreis Berchtesgadener Land"
    assert got.locality == "Königssee"          # locality stays free text
    # rows actually created in the vocab tables
    assert session.query(Country).filter_by(name="Germany").count() == 1
    assert session.query(AdministrativeRegion).filter_by(name="Oberbayern").count() == 1


def test_same_country_name_reused_not_duplicated(session):
    e1 = ev_svc.create_collecting_event(session, country="Germany", locality="A")
    e2 = ev_svc.create_collecting_event(session, country="Germany", locality="B")
    session.flush()
    assert e1.country_id == e2.country_id
    assert session.query(Country).filter_by(name="Germany").count() == 1


def test_update_clears_geo_when_blank(session):
    ev = ev_svc.create_collecting_event(session, country="Germany", county="LK Test")
    session.flush()
    ev_svc.update_collecting_event(session, ev.id, county="")   # blank → None
    session.expire_all()
    got = session.get(CollectingEvent, ev.id)
    assert got.county_id is None
    assert got.country_obj.name == "Germany"


def test_merge_country_repoints_event_fk(session):
    keep = country_vocab.get_or_create(session, "Germany")
    absorb = country_vocab.get_or_create(session, "Deutschland")   # variant to fold
    ev = ev_svc.create_collecting_event(session, country="Deutschland", locality="X")
    session.flush()
    country_vocab.merge(session, keep_id=keep.id, absorb_id=absorb.id)
    session.expire_all()
    assert session.get(Country, absorb.id) is None
    assert session.get(CollectingEvent, ev.id).country_id == keep.id


def test_search_event_by_state_province_name(session):
    ev_svc.create_collecting_event(session, country="Germany", state_province="Bavaria", locality="X")
    session.flush()
    hits = ev_svc.search_collecting_events(session, "Bavaria")
    assert len(hits) >= 1


# ── stateProvince ISO 3166-2 code (migration 0055) ──────────────────────────────
# The code is a property of the state, not of the event, so it lives once on the vocab
# row. The geocoder identifies the state *by* this tag and now carries it through.

def test_state_iso_code_is_stamped_on_the_vocab_row(session):
    ev_svc.create_collecting_event(
        session, country="Germany", state_province="Bavaria",
        state_province_iso="DE-BY", locality="Uffing")
    session.flush()
    assert session.query(StateProvince).filter_by(name="Bavaria").one().iso_code == "DE-BY"


def test_state_iso_code_is_not_an_event_column(session):
    """`state_province_iso` is consumed by _resolve_geo_fields, never set on the event."""
    ev = ev_svc.create_collecting_event(
        session, country="Germany", state_province="Bavaria", state_province_iso="DE-BY")
    session.flush()
    assert not hasattr(ev, "state_province_iso")


def test_state_iso_code_is_optional(session):
    """Existing rows have no code; a save without one must not fail or blank it."""
    ev_svc.create_collecting_event(session, state_province="Hesse")
    session.flush()
    assert session.query(StateProvince).filter_by(name="Hesse").one().iso_code is None
    # a later save that carries the code fills it in
    ev_svc.create_collecting_event(session, state_province="Hesse", state_province_iso="DE-HE")
    session.flush()
    assert session.query(StateProvince).filter_by(name="Hesse").one().iso_code == "DE-HE"
    # and a later save WITHOUT a code leaves it intact
    ev_svc.create_collecting_event(session, state_province="Hesse")
    session.flush()
    assert session.query(StateProvince).filter_by(name="Hesse").one().iso_code == "DE-HE"


def test_conflicting_state_iso_code_is_refused_loudly(session):
    """One state name cannot honestly carry two subdivision codes — refuse, never overwrite."""
    import pytest
    ev_svc.create_collecting_event(session, state_province="Bavaria", state_province_iso="DE-BY")
    session.flush()
    with pytest.raises(ValueError, match="already recorded as"):
        ev_svc.create_collecting_event(
            session, state_province="Bavaria", state_province_iso="DE-BW")


def test_greek_region_carries_its_iso_code(session):
    """GR-J sits at admin_level 5, DE-BY at 4 — the code, not the level, identifies a state."""
    ev_svc.create_collecting_event(
        session, country="Greece", state_province="Peloponnese Region",
        state_province_iso="GR-J", locality="Tripoli")
    session.flush()
    row = session.query(StateProvince).filter_by(name="Peloponnese Region").one()
    assert row.iso_code == "GR-J"

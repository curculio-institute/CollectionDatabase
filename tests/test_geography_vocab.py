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


# ── country / stateProvince identity is (name, iso_code) — migrations 0055 + 0056 ────
# A subdivision NAME does not identify a subdivision: 40 of the 5,420 ISO 3166-2 names are
# shared across countries. Exact match on (name, code) reuses; anything else creates.

def test_state_iso_code_is_stamped_on_a_new_vocab_row(session):
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


def test_same_name_different_country_gets_its_own_row(session):
    """Limburg is BE-VLI *and* NL-LI. Forcing them into one row loses a real place."""
    ev_svc.create_collecting_event(session, country="Belgium", state_province="Limburg",
                                   state_province_iso="BE-VLI")
    ev_svc.create_collecting_event(session, country="Netherlands", state_province="Limburg",
                                   state_province_iso="NL-LI")
    session.flush()
    rows = session.query(StateProvince).filter_by(name="Limburg").all()
    assert sorted(r.iso_code for r in rows) == ["BE-VLI", "NL-LI"]


def test_a_save_is_never_refused_by_a_code_conflict(session):
    """The old fill-once stamp raised here, refusing a legitimate Dutch-Limburg specimen."""
    ev_svc.create_collecting_event(session, state_province="Punjab", state_province_iso="IN-PB")
    session.flush()
    ev = ev_svc.create_collecting_event(session, state_province="Punjab",
                                        state_province_iso="PK-PB")   # must not raise
    session.flush()
    assert ev.state_province_obj.iso_code == "PK-PB"
    assert session.query(StateProvince).filter_by(name="Punjab").count() == 2


def test_exact_match_on_name_and_code_is_reused(session):
    ev_svc.create_collecting_event(session, state_province="Bavaria", state_province_iso="DE-BY")
    ev_svc.create_collecting_event(session, state_province="Bavaria", state_province_iso="DE-BY")
    session.flush()
    assert session.query(StateProvince).filter_by(name="Bavaria").count() == 1


def test_an_existing_uncoded_row_is_never_mutated(session):
    """A hand-typed 'Limburg' must not be silently declared Dutch by a later geocode."""
    ev_svc.create_collecting_event(session, state_province="Limburg")      # no code
    session.flush()
    ev_svc.create_collecting_event(session, state_province="Limburg", state_province_iso="NL-LI")
    session.flush()
    rows = session.query(StateProvince).filter_by(name="Limburg").all()
    assert sorted((r.iso_code or "") for r in rows) == ["", "NL-LI"]      # merge later


def test_uncoded_name_does_not_duplicate_endlessly(session):
    """IFNULL() in the unique index: exactly one uncoded row per name, not one per save."""
    ev_svc.create_collecting_event(session, state_province="Hesse")
    ev_svc.create_collecting_event(session, state_province="Hesse")
    session.flush()
    assert session.query(StateProvince).filter_by(name="Hesse").count() == 1


def test_country_carries_its_iso_code_the_same_way(session):
    from app.models import Country
    ev_svc.create_collecting_event(session, country="Germany", country_iso="DE")
    session.flush()
    assert session.query(Country).filter_by(name="Germany").one().iso_code == "DE"


def test_greek_region_carries_its_iso_code(session):
    """GR-J sits at admin_level 5, DE-BY at 4 — the code, not the level, identifies a state."""
    ev_svc.create_collecting_event(
        session, country="Greece", state_province="Peloponnese Region",
        state_province_iso="GR-J", locality="Tripoli")
    session.flush()
    assert session.query(StateProvince).filter_by(
        name="Peloponnese Region").one().iso_code == "GR-J"


# ── a state cannot lie outside its country (ISO 3166-2 begins with ISO 3166-1) ────

def test_state_outside_its_country_is_refused(session):
    import pytest
    with pytest.raises(ValueError, match="lies in country DE, not in GR"):
        ev_svc.create_collecting_event(
            session, country="Greece", country_iso="GR",
            state_province="Bavaria", state_province_iso="DE-BY")


def test_matching_country_and_state_codes_save_fine(session):
    ev = ev_svc.create_collecting_event(
        session, country="Germany", country_iso="DE",
        state_province="Bavaria", state_province_iso="DE-BY")
    session.flush()
    assert ev.state_province_obj.iso_code == "DE-BY"


def test_uncoded_levels_assert_nothing_and_are_not_checked(session):
    """A hand-typed state has no code; it cannot contradict the country."""
    ev = ev_svc.create_collecting_event(
        session, country="Greece", country_iso="GR", state_province="Bavaria")
    session.flush()
    assert ev.state_province_obj.iso_code is None


def test_country_iso_identifies_the_vocab_row(session):
    """There is no dwc:countryCode column any more (0057) — the code identifies the row."""
    from app.models import Country
    ev_svc.create_collecting_event(session, country="Germany", country_iso="DE")
    session.flush()
    assert session.query(Country).filter_by(name="Germany").one().iso_code == "DE"


# ── Vocabulary.entries(): one tuple per row, never collapsed by name ──────────────

def test_entries_lists_every_row_including_duplicate_names(session):
    from app.services.vocabularies import state_province_vocab
    ev_svc.create_collecting_event(session, state_province="Limburg", state_province_iso="BE-VLI")
    ev_svc.create_collecting_event(session, state_province="Limburg", state_province_iso="NL-LI")
    session.flush()
    entries = state_province_vocab.entries(session)
    assert sorted(e for e in entries if e[0] == "Limburg") == [
        ("Limburg", "BE-VLI"), ("Limburg", "NL-LI")]
    # options() *does* collapse them — which is why the widget uses entries()
    assert list(state_province_vocab.options(session)).count("Limburg") == 1


def test_display_label_shows_the_code(session):
    from app.models import StateProvince
    from app.services.vocabularies import state_province_vocab
    ev_svc.create_collecting_event(session, state_province="Limburg", state_province_iso="NL-LI")
    session.flush()
    row = session.query(StateProvince).filter_by(name="Limburg").one()
    assert state_province_vocab.display_label(row) == "Limburg (NL-LI)"

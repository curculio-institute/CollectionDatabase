"""Service-layer tests. All DB state rolls back after each test (conftest fixture)."""
import pytest
from app.models import Taxon, CollectingEvent, CollectionObject, TaxonDetermination
from app.models.base import _utcnow
from app.services.taxa import format_scientific_name, search_taxa, TaxonOption
from app.services.events import format_event_summary, search_collecting_events, create_collecting_event
from app.services.specimens import save_specimen_entry, recent_specimens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _taxon(session, genus="Carabus", species="coriaceus", authorship="Linnaeus, 1758"):
    t = Taxon(genus=genus, specific_epithet=species,
              scientific_name_authorship=authorship,
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def _event(session, country="Germany", state="Bavaria", locality="Berchtesgaden",
           event_date="2024-06-15", recorded_by="J. Jilg"):
    ce = CollectingEvent(
        country=country, state_province=state, locality=locality,
        event_date=event_date, recorded_by=recorded_by,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    session.flush()
    return ce


# ---------------------------------------------------------------------------
# format_scientific_name
# ---------------------------------------------------------------------------

def test_format_scientific_name_full():
    t = Taxon(genus="Carabus", specific_epithet="coriaceus",
              scientific_name_authorship="Linnaeus, 1758")
    assert format_scientific_name(t) == "Carabus coriaceus Linnaeus, 1758"


def test_format_scientific_name_no_authorship():
    t = Taxon(genus="Dytiscus", specific_epithet="marginalis")
    assert format_scientific_name(t) == "Dytiscus marginalis"


def test_format_scientific_name_with_subgenus():
    t = Taxon(genus="Amara", subgenus="Amara", specific_epithet="aenea")
    assert format_scientific_name(t) == "Amara (Amara) aenea"


def test_format_scientific_name_all_none():
    t = Taxon(id=99)
    assert format_scientific_name(t) == "taxon #99"


def test_format_scientific_name_genus_only():
    t = Taxon(genus="Ceutorhynchus")
    assert format_scientific_name(t) == "Ceutorhynchus"


# ---------------------------------------------------------------------------
# search_taxa
# ---------------------------------------------------------------------------

def test_search_taxa_empty_query_returns_all(session):
    _taxon(session, "Carabus", "coriaceus")
    _taxon(session, "Dytiscus", "marginalis")
    results = search_taxa(session, "")
    assert len(results) >= 2
    assert all(isinstance(r, TaxonOption) for r in results)


def test_search_taxa_genus_match(session):
    _taxon(session, "Carabus", "coriaceus")
    _taxon(session, "Dytiscus", "marginalis")
    results = search_taxa(session, "cara")
    assert len(results) == 1
    assert results[0].label.startswith("Carabus")


def test_search_taxa_species_match(session):
    _taxon(session, "Carabus", "coriaceus")
    results = search_taxa(session, "coriaceus")
    assert len(results) == 1


def test_search_taxa_no_match_returns_empty(session):
    _taxon(session, "Carabus", "coriaceus")
    results = search_taxa(session, "zzznomatch")
    assert results == []


def test_search_taxa_case_insensitive(session):
    _taxon(session, "Carabus", "coriaceus")
    results = search_taxa(session, "CARABUS")
    assert len(results) == 1


# ---------------------------------------------------------------------------
# format_event_summary
# ---------------------------------------------------------------------------

def test_format_event_summary_full():
    ce = CollectingEvent(
        country="Germany", state_province="Bavaria", locality="Berchtesgaden",
        event_date="2024-06-15", recorded_by="J. Jilg",
    )
    s = format_event_summary(ce)
    assert "Germany" in s
    assert "Bavaria" in s
    assert "2024-06-15" in s
    assert "J. Jilg" in s


def test_format_event_summary_skips_blanks():
    ce = CollectingEvent(country="Spain", locality="Sierra Nevada")
    s = format_event_summary(ce)
    assert "·  ·" not in s
    assert "Spain" in s
    assert "Sierra Nevada" in s


def test_format_event_summary_empty_returns_id_label():
    ce = CollectingEvent()
    ce.id = 7
    s = format_event_summary(ce)
    assert s == "Event #7"


def test_format_event_summary_verbatim_locality_fallback():
    ce = CollectingEvent(verbatim_locality="near the old mill")
    ce.id = 1
    s = format_event_summary(ce)
    assert "near the old mill" in s


# ---------------------------------------------------------------------------
# search_collecting_events
# ---------------------------------------------------------------------------

def test_search_events_by_country(session):
    _event(session, country="Germany")
    _event(session, country="Austria")
    results = search_collecting_events(session, "germany")
    assert len(results) == 1
    assert "Germany" in results[0].summary


def test_search_events_by_recorded_by(session):
    _event(session, recorded_by="J. Jilg")
    _event(session, recorded_by="A. Müller")
    results = search_collecting_events(session, "jilg")
    assert len(results) == 1


def test_search_events_by_locality(session):
    _event(session, locality="Berchtesgaden")
    results = search_collecting_events(session, "Berchtesgaden")
    assert len(results) == 1


def test_search_events_by_date(session):
    _event(session, event_date="2024-06-15")
    _event(session, event_date="2023-08-01")
    results = search_collecting_events(session, "2024")
    assert len(results) == 1


def test_search_events_empty_query_returns_recent_first(session):
    e1 = _event(session)
    e2 = _event(session, country="Austria")
    results = search_collecting_events(session, "")
    assert results[0].id == e2.id   # most recent first


# ---------------------------------------------------------------------------
# save_specimen_entry + recent_specimens
# ---------------------------------------------------------------------------

def test_save_specimen_entry_creates_three_rows(session):
    t = _taxon(session)
    ce = _event(session)

    co = save_specimen_entry(
        session,
        taxon_id=t.id,
        event_id=ce.id,
        event_fields={},
        specimen_fields={
            "catalog_number": "0001", "catalog_namespace": "TEST",
            "sex": "male", "individual_count": 1,
        },
        determination_fields={"identified_by": "J. Jilg"},
    )
    session.flush()

    assert co.id is not None
    assert session.get(CollectionObject, co.id) is not None
    td = session.query(TaxonDetermination).filter_by(collection_object_id=co.id).first()
    assert td is not None
    assert td.taxon_id == t.id
    assert td.is_current == 1


def test_save_specimen_entry_creates_new_event_when_no_event_id(session):
    t = _taxon(session)

    co = save_specimen_entry(
        session,
        taxon_id=t.id,
        event_id=None,
        event_fields={"country": "France", "locality": "Camargue", "event_date": "2024-07-01"},
        specimen_fields={"catalog_number": "0002", "catalog_namespace": "TEST"},
        determination_fields={},
    )
    session.flush()

    assert co.collecting_event_id is not None
    ce = session.get(CollectingEvent, co.collecting_event_id)
    assert ce.country == "France"
    assert ce.locality == "Camargue"


def test_recent_specimens_newest_first(session):
    t = _taxon(session)
    ce = _event(session)

    for num in ("001", "002", "003"):
        save_specimen_entry(
            session,
            taxon_id=t.id, event_id=ce.id,
            event_fields={},
            specimen_fields={"catalog_number": num, "catalog_namespace": "TEST"},
            determination_fields={},
        )
    session.flush()

    rows = recent_specimens(session, limit=10)
    assert rows[0].catalog_number == "003"
    assert rows[-1].catalog_number == "001"


def test_recent_specimens_returns_only_current_determination(session):
    t1 = _taxon(session, "Carabus", "coriaceus")
    t2 = _taxon(session, "Carabus", "violaceus")
    ce = _event(session)

    co = create_object = CollectionObject(
        collecting_event_id=ce.id, catalog_number="X001", catalog_namespace="TEST",
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(co)
    session.flush()

    # old determination (not current)
    old = TaxonDetermination(
        collection_object_id=co.id, taxon_id=t1.id, is_current=0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    # current determination
    current = TaxonDetermination(
        collection_object_id=co.id, taxon_id=t2.id, is_current=1,
        identified_by="J. Jilg", created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add_all([old, current])
    session.flush()

    rows = recent_specimens(session, limit=10)
    matching = [r for r in rows if r.collection_object_id == co.id]
    assert len(matching) == 1
    assert "violaceus" in matching[0].scientific_name
    assert "coriaceus" not in matching[0].scientific_name
    assert matching[0].identified_by == "J. Jilg"

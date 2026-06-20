"""Service-layer tests. All DB state rolls back after each test (conftest fixture)."""
import pytest
from app.models import Taxon, CollectingEvent, CollectionObject, TaxonDetermination
from app.models.person import Person
from app.models.base import _utcnow
from app.services.taxa import (
    format_scientific_name, search_taxa, TaxonOption,
    parse_scientific_name, rank_from_parse, build_manual_taxon_prefill,
)
from app.services.events import format_event_summary, search_collecting_events, create_collecting_event
from app.services.specimens import (
    save_specimen_entry, recent_specimens, update_collection_object,
    finalize_specimen,
)
from app.models import PrintQueue, BiologicalRelationship, BiologicalAssociation
from app.services.identifiers import (
    reserve_sequential_codes, _next_sequential_number,
)
from app.models import LabelCode, LabelBatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _person(session, full_name: str) -> Person:
    existing = session.query(Person).filter_by(full_name=full_name).first()
    if existing:
        return existing
    p = Person(full_name=full_name, created_at=_utcnow(), updated_at=_utcnow())
    session.add(p)
    session.flush()
    return p


def _taxon(session, genus="Carabus", species="coriaceus", authorship="Linnaeus, 1758"):
    sci_name = f"{genus} {species}" if species else genus
    rank = "species" if species else "genus"
    t = Taxon(
        scientific_name=sci_name,
        taxon_rank=rank,
        scientific_name_authorship=authorship,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


def _event(session, country="Germany", state="Bavaria", locality="Berchtesgaden",
           event_date="2024-06-15", recorded_by="J. Jilg"):
    recorded_by_id = _person(session, recorded_by).id if recorded_by else None
    ce = CollectingEvent(
        country=country, state_province=state, locality=locality,
        event_date=event_date, recorded_by_id=recorded_by_id,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(ce)
    session.flush()
    return ce


# ---------------------------------------------------------------------------
# format_scientific_name
# ---------------------------------------------------------------------------

def test_format_scientific_name_full():
    t = Taxon(scientific_name="Carabus coriaceus", taxon_rank="species",
              scientific_name_authorship="Linnaeus, 1758")
    assert format_scientific_name(t) == "Carabus coriaceus Linnaeus, 1758"


def test_format_scientific_name_no_authorship():
    t = Taxon(scientific_name="Dytiscus marginalis", taxon_rank="species")
    assert format_scientific_name(t) == "Dytiscus marginalis"


def test_format_scientific_name_with_subgenus():
    t = Taxon(scientific_name="Amara (Amara) aenea", taxon_rank="species")
    assert format_scientific_name(t) == "Amara (Amara) aenea"


def test_format_scientific_name_no_name():
    t = Taxon(id=99, scientific_name="", taxon_rank="species")
    assert format_scientific_name(t) == "taxon #99"


def test_format_scientific_name_genus_only():
    t = Taxon(scientific_name="Ceutorhynchus", taxon_rank="genus")
    assert format_scientific_name(t) == "Ceutorhynchus"


# ---------------------------------------------------------------------------
# parse_scientific_name / rank_from_parse
# ---------------------------------------------------------------------------

def test_parse_scientific_name_variants():
    assert parse_scientific_name("Sitona") == ("Sitona", None, None, None)
    assert parse_scientific_name("Sitona lineatus") == ("Sitona", None, "lineatus", None)
    assert parse_scientific_name("Sitona (Sitona) lineatus") == ("Sitona", "Sitona", "lineatus", None)
    assert parse_scientific_name("Sitona lineatus allii") == ("Sitona", None, "lineatus", "allii")
    assert parse_scientific_name("Sitona (Sitona) lineatus allii") == ("Sitona", "Sitona", "lineatus", "allii")
    assert parse_scientific_name("") == ("", None, None, None)


def test_rank_from_parse():
    assert rank_from_parse(None, None) == "genus"
    assert rank_from_parse("lineatus", None) == "species"
    assert rank_from_parse("lineatus", "allii") == "subspecies"


# ---------------------------------------------------------------------------
# build_manual_taxon_prefill
# ---------------------------------------------------------------------------

def test_prefill_resolves_genus_parent(session):
    genus = _taxon(session, "Otiorhynchus", species=None)
    pf = build_manual_taxon_prefill(
        session, {"scientificName": "Otiorhynchus norici", "scientificNameAuthorship": "Reitter, 1912"}
    )
    assert pf["scientific_name"] == "Otiorhynchus norici"
    assert pf["taxon_rank"] == "species"
    assert pf["scientific_name_authorship"] == "Reitter, 1912"
    assert pf["parent_name_usage_id"] == genus.id
    assert pf["accepted_name_usage_id"] is None


def test_prefill_prefers_subgenus_over_genus(session):
    genus = _taxon(session, "Otiorhynchus", species=None)
    subg = Taxon(scientific_name="Magnanotius", taxon_rank="subgenus",
                 parent_name_usage_id=genus.id, nomenclatural_code="ICZN",
                 created_at=_utcnow(), updated_at=_utcnow())
    session.add(subg); session.flush()
    pf = build_manual_taxon_prefill(
        session, {"scientificName": "Otiorhynchus (Magnanotius) norici"}
    )
    assert pf["scientific_name"] == "Otiorhynchus (Magnanotius) norici"
    assert pf["parent_name_usage_id"] == subg.id


def test_prefill_no_parent_when_genus_absent(session):
    pf = build_manual_taxon_prefill(session, {"scientificName": "Unknownus novus"})
    assert pf["parent_name_usage_id"] is None
    assert pf["taxon_rank"] == "species"


def test_prefill_strips_leaked_authorship_from_name(session):
    # Authorship accidentally left in the scientificName must not become an epithet.
    pf = build_manual_taxon_prefill(session, {"scientificName": "Otiorhynchus norici Reitter"})
    assert pf["scientific_name"] == "Otiorhynchus norici"
    assert pf["taxon_rank"] == "species"


def test_manual_create_inherits_parent_code(session):
    # Issue #9: a manually-added species must inherit its parent's nomenclatural
    # code, not land NULL. The Import & Assign path is prefill → create_taxon_direct
    # with the code derived from the resolved parent (as the editor does it).
    from app.services.taxa import create_taxon_direct
    genus = _taxon(session, "Otiorhynchus", species=None)
    genus.nomenclatural_code = "ICZN"
    session.flush()

    pf = build_manual_taxon_prefill(session, {"scientificName": "Otiorhynchus norici"})
    assert pf["parent_name_usage_id"] == genus.id

    # The UI inherits the code from the chosen parent before saving.
    parent = session.get(Taxon, pf["parent_name_usage_id"])
    new = create_taxon_direct(
        session,
        scientific_name=pf["scientific_name"],
        taxon_rank=pf["taxon_rank"],
        scientific_name_authorship=pf["scientific_name_authorship"],
        parent_name_usage_id=pf["parent_name_usage_id"],
        accepted_name_usage_id=pf["accepted_name_usage_id"],
        nomenclatural_code=parent.nomenclatural_code,
    )
    assert new.nomenclatural_code == "ICZN"
    assert new.parent_name_usage_id == genus.id


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
        event_date="2024-06-15",
    )
    s = format_event_summary(ce)
    assert "Germany" in s
    assert "Bavaria" in s
    assert "2024-06-15" in s


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
            "catalog_number": "0001", "collection_code": "TEST",
            "institution_code": "TEST",
            "sex": "male", "individual_count": 1,
        },
        determination_fields={},
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
        specimen_fields={"catalog_number": "0002", "collection_code": "TEST", "institution_code": "TEST"},
        determination_fields={},
    )
    session.flush()

    assert co.collecting_event_id is not None
    ce = session.get(CollectingEvent, co.collecting_event_id)
    assert ce.country == "France"
    assert ce.locality == "Camargue"


def test_update_collection_object_gifting_collection_code(session):
    """collection_code may change (gifting); catalog_number/institution_code stay
    immutable; an empty collection_code is rejected loudly (NOT NULL)."""
    t = _taxon(session)
    ce = _event(session)
    co = save_specimen_entry(
        session,
        taxon_id=t.id, event_id=ce.id, event_fields={},
        specimen_fields={"catalog_number": "G001", "collection_code": "TEST",
                         "institution_code": "TEST"},
        determination_fields={},
    )
    session.flush()

    # collection_code changes (re-homed on gifting)
    update_collection_object(session, co.id, collection_code="NHMW")
    assert co.collection_code == "NHMW"

    # catalog_number and institution_code are immutable — ignored
    update_collection_object(session, co.id,
                             catalog_number="ZZZZ", institution_code="OTHER")
    assert co.catalog_number == "G001"
    assert co.institution_code == "TEST"

    # empty collection_code is rejected, not silently blanked (NOT NULL column)
    with pytest.raises(ValueError):
        update_collection_object(session, co.id, collection_code="")
    assert co.collection_code == "NHMW"


def _seed_code(session, code: str) -> None:
    """Insert a single reserved LabelCode (its own batch) for sequence-number tests."""
    now = _utcnow()
    batch = LabelBatch(created_at=now, updated_at=now)
    session.add(batch)
    session.flush()
    session.add(LabelCode(code=code, status="reserved", batch_id=batch.id,
                          created_at=now, updated_at=now))
    session.flush()


def test_next_sequential_number_empty_starts_at_one(session):
    assert _next_sequential_number(session, "TEST") == 1


def test_next_sequential_number_ignores_other_prefixes(session):
    _seed_code(session, "TEST-00007")
    _seed_code(session, "OTHER-09999")   # different prefix — must not count
    assert _next_sequential_number(session, "TEST") == 8


def test_next_sequential_number_survives_six_digit_overflow(session):
    # Past 99999 the suffix widens to 6 digits; "99999" sorts AFTER "100000"
    # as text, so a lexicographic or len==5 scan would miss the real maximum.
    _seed_code(session, "TEST-99999")
    _seed_code(session, "TEST-100000")
    assert _next_sequential_number(session, "TEST") == 100001


def test_reserve_sequential_codes_continues_after_overflow(session):
    _seed_code(session, "TEST-100000")
    _batch_id, codes = reserve_sequential_codes(session, "TEST", 2)
    assert codes == ["TEST-100001", "TEST-100002"]


def test_recent_specimens_newest_first(session):
    t = _taxon(session)
    ce = _event(session)

    for num in ("001", "002", "003"):
        save_specimen_entry(
            session,
            taxon_id=t.id, event_id=ce.id,
            event_fields={},
            specimen_fields={"catalog_number": num, "collection_code": "TEST", "institution_code": "TEST"},
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
        collecting_event_id=ce.id, catalog_number="X001", collection_code="TEST", institution_code="TEST",
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(co)
    session.flush()

    jilg = _person(session, "J. Jilg")
    # old determination (not current)
    old = TaxonDetermination(
        collection_object_id=co.id, taxon_id=t1.id, is_current=0,
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    # current determination
    current = TaxonDetermination(
        collection_object_id=co.id, taxon_id=t2.id, is_current=1,
        identified_by_id=jilg.id, created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add_all([old, current])
    session.flush()

    rows = recent_specimens(session, limit=10)
    matching = [r for r in rows if r.collection_object_id == co.id]
    assert len(matching) == 1
    assert "violaceus" in matching[0].scientific_name
    assert "coriaceus" not in matching[0].scientific_name
    assert matching[0].identified_by == "J. Jilg"


# ---------------------------------------------------------------------------
# finalize_specimen — shared create-time seam (assign code / queue / bio)
# ---------------------------------------------------------------------------

def _saved_co_with_code(session, *, catalog="A001", code="A001"):
    """Create a specimen and a reserved LabelCode `code`; return (co, code)."""
    t = _taxon(session)
    ce = _event(session)
    co = save_specimen_entry(
        session, taxon_id=t.id, event_id=ce.id, event_fields={},
        specimen_fields={"catalog_number": catalog, "collection_code": "TEST",
                         "institution_code": "TEST"},
        determination_fields={},
    )
    _seed_code(session, code)
    session.flush()
    return co, code


def test_finalize_specimen_standard_assigns_code_but_queues_nothing(session):
    """Digitize standard: the reserved code is bound (status→assigned) but NO
    print-queue rows are created — the identifier is pre-printed and the specimen
    carries its own data labels."""
    co, code = _saved_co_with_code(session)

    finalize_specimen(session, collection_object_id=co.id, code=code,
                      queue_labels=False)
    session.flush()

    lc = session.query(LabelCode).filter_by(code=code).one()
    assert lc.status == "assigned"
    assert lc.collection_object_id == co.id
    assert session.query(PrintQueue).count() == 0


def test_finalize_specimen_mounting_queues_full_sheet(session):
    """Mounting: code bound + identifier, data and determination labels all queued."""
    co, code = _saved_co_with_code(session)

    finalize_specimen(session, collection_object_id=co.id, code=code,
                      queue_labels=True)
    session.flush()

    lc = session.query(LabelCode).filter_by(code=code).one()
    assert lc.status == "assigned"
    types = sorted(r.label_type for r in session.query(PrintQueue).all())
    assert types == ["data", "determination", "identifier"]
    # identifier row points at the label code; data/determination at the specimen
    ident = session.query(PrintQueue).filter_by(label_type="identifier").one()
    assert ident.label_code_id == lc.id and ident.collection_object_id is None
    data = session.query(PrintQueue).filter_by(label_type="data").one()
    assert data.collection_object_id == co.id and data.label_code_id is None


def test_finalize_specimen_visiting_no_code_no_queue(session):
    """Visiting: code=None — nothing is assigned or queued (foreign catalogNumber)."""
    co, code = _saved_co_with_code(session)

    finalize_specimen(session, collection_object_id=co.id, code=None)
    session.flush()

    lc = session.query(LabelCode).filter_by(code=code).one()
    assert lc.status == "reserved"            # untouched
    assert lc.collection_object_id is None
    assert session.query(PrintQueue).count() == 0


def test_finalize_specimen_saves_biological_associations(session):
    """Associations are persisted regardless of mode (here: visiting, code=None)."""
    co, _ = _saved_co_with_code(session)
    host = _taxon(session, "Quercus", "robur", authorship="L.")
    rel = BiologicalRelationship(name="collected_on",
                                 created_at=_utcnow(), updated_at=_utcnow())
    session.add(rel)
    session.flush()

    finalize_specimen(
        session, collection_object_id=co.id, code=None,
        associations=[{"rel_id": rel.id, "taxon_id": host.id}],
    )
    session.flush()

    ba = session.query(BiologicalAssociation).filter_by(
        subject_collection_object_id=co.id).one()
    assert ba.object_taxon_id == host.id
    assert ba.biological_relationship_id == rel.id

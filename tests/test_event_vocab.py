"""habitat + sampling_protocol controlled vocabularies on collecting_event."""
import app.services.events as ev_svc
from app.models import CollectingEvent, Habitat, SamplingProtocol
from app.services.vocabularies import habitat_vocab, sampling_protocol_vocab


def test_sampling_protocol_seeded_with_curated_set(session):
    names = set(sampling_protocol_vocab.options(session))
    # The curated starting set from migration 0040 must be present.
    for expected in ("beating", "pitfall trap", "light trap", "sweep net", "rearing"):
        assert expected in names


def test_event_links_habitat_and_protocol_by_fk(session):
    hid = habitat_vocab.get_or_create(session, "alpine meadow").id
    sid = sampling_protocol_vocab.get_or_create(session, "hand collecting").id
    ev = ev_svc.create_collecting_event(
        session, country="Austria", habitat_id=hid, sampling_protocol_id=sid)
    session.flush()
    got = session.get(CollectingEvent, ev.id)
    assert got.habitat_obj.name == "alpine meadow"
    assert got.sampling_protocol_obj.name == "hand collecting"


def test_event_form_snapshot_returns_vocab_names(session):
    hid = habitat_vocab.get_or_create(session, "riverbank").id
    ev = ev_svc.create_collecting_event(session, country="Germany", habitat_id=hid)
    session.flush()
    snap = ev_svc.event_form_snapshot(session, ev.id)
    assert snap["habitat"] == "riverbank"
    assert snap["sampling_protocol"] is None


def test_merge_habitat_repoints_event_fk(session):
    keep = habitat_vocab.get_or_create(session, "deciduous forest")
    absorb = habitat_vocab.get_or_create(session, "decidous forest")   # typo
    ev = ev_svc.create_collecting_event(session, country="X", habitat_id=absorb.id)
    session.flush()
    habitat_vocab.merge(session, keep_id=keep.id, absorb_id=absorb.id)
    session.expire_all()   # merge re-points via raw SQL — drop stale ORM cache
    assert session.get(Habitat, absorb.id) is None
    assert session.get(CollectingEvent, ev.id).habitat_id == keep.id


def test_search_finds_event_by_habitat_name(session):
    hid = habitat_vocab.get_or_create(session, "Fagus-Quercus forest").id
    ev_svc.create_collecting_event(session, country="Y", habitat_id=hid)
    session.flush()
    hits = ev_svc.search_collecting_events(session, "Fagus-Quercus")
    assert len(hits) >= 1

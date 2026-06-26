"""Print-queue grouping tests (m-4). DB rolls back after each test."""
from app.models import LabelCode
import app.services.print_queue as pq
from app.services.specimens import save_specimen_entry, finalize_specimen
from app.services.identifiers import reserve_sequential_codes

# Reuse the specimen/event/taxon builders from the service tests.
from tests.test_services import _taxon, _event


def _enqueue_identifier_batch(session, n, *, group_id, source):
    batch_id, _codes = reserve_sequential_codes(session, "TEST", n)
    for lc in session.query(LabelCode).filter(LabelCode.batch_id == batch_id).all():
        pq.enqueue_identifier(session, lc.id, print_group_id=group_id, source=source)
    session.flush()


def test_next_print_group_id_increments(session):
    assert pq.next_print_group_id(session) == 1
    _enqueue_identifier_batch(session, 1, group_id=1, source=pq.SOURCE_IDENTIFIERS)
    assert pq.next_print_group_id(session) == 2


def test_queued_groups_mounting_aligns_columns(session):
    """A mounting batch becomes one group whose columns each carry the specimen's
    data, determination and its own identifier code (aligned)."""
    t = _taxon(session)
    ce = _event(session)
    _batch, codes = reserve_sequential_codes(session, "TEST", 2)
    gid = pq.next_print_group_id(session)
    for code in codes:
        co = save_specimen_entry(
            session, taxon_id=t.id, event_id=ce.id, event_fields={},
            specimen_fields={"catalog_number": code, "collection_code": "TEST",
                             "institution_code": "TEST"},
            determination_fields={},
        )
        finalize_specimen(session, collection_object_id=co.id, code=code,
                          queue_labels=True, print_group_id=gid,
                          source=pq.SOURCE_MOUNTING)
    session.flush()

    groups = pq.queued_groups(session)
    assert len(groups) == 1
    g = groups[0]
    assert g.source == pq.SOURCE_MOUNTING
    assert len(g.specimens) == 2
    for spec, code in zip(g.specimens, codes):
        assert spec.id_code == code           # identifier aligned to its specimen
        assert spec.data is not None
        assert spec.determination is not None


def test_queued_groups_identifier_only(session):
    """A reserved-code batch is one identifier-only group: columns carry just the
    code, no data/determination."""
    gid = pq.next_print_group_id(session)
    _enqueue_identifier_batch(session, 3, group_id=gid, source=pq.SOURCE_IDENTIFIERS)

    groups = pq.queued_groups(session)
    assert len(groups) == 1
    assert groups[0].source == pq.SOURCE_IDENTIFIERS
    assert len(groups[0].specimens) == 3
    assert all(s.id_code and s.data is None and s.determination is None
               for s in groups[0].specimens)


def test_queued_groups_separates_distinct_additions(session):
    """Two separate enqueue operations yield two distinct groups, in order."""
    g1 = pq.next_print_group_id(session)
    _enqueue_identifier_batch(session, 2, group_id=g1, source=pq.SOURCE_IDENTIFIERS)
    g2 = pq.next_print_group_id(session)
    _enqueue_identifier_batch(session, 1, group_id=g2, source=pq.SOURCE_REPRINT)

    groups = pq.queued_groups(session)
    assert [grp.source for grp in groups] == [pq.SOURCE_IDENTIFIERS, pq.SOURCE_REPRINT]
    assert [len(grp.specimens) for grp in groups] == [2, 1]


def test_build_pdf_smoke(session):
    gid = pq.next_print_group_id(session)
    _enqueue_identifier_batch(session, 2, group_id=gid, source=pq.SOURCE_IDENTIFIERS)
    pdf = pq.build_pdf(session, printed_at="2026-06-12 14:30")
    assert pdf[:4] == b"%PDF"


def test_requeue_after_clear_recovers_codes(session):
    """Generate → clear queue → re-queue: reserved codes are never lost (user req)."""
    batch_id, codes = reserve_sequential_codes(session, "TEST", 4)
    for lc in session.query(LabelCode).filter(LabelCode.batch_id == batch_id).all():
        pq.enqueue_identifier(session, lc.id, print_group_id=1,
                              source=pq.SOURCE_IDENTIFIERS)
    session.flush()
    assert pq.queue_summary(session).total == 4

    pq.clear_queue(session)              # cleared WITHOUT printing
    assert pq.queue_summary(session).total == 0
    # codes still exist, still reserved
    assert (session.query(LabelCode)
            .filter(LabelCode.batch_id == batch_id,
                    LabelCode.status == "reserved").count()) == 4

    added = pq.requeue_batch_identifiers(session, batch_id)
    assert added == 4
    assert pq.queue_summary(session).total == 4


def test_requeue_is_dedup_safe(session):
    """Re-queuing a batch that's already queued adds nothing (no duplicates)."""
    batch_id, _ = reserve_sequential_codes(session, "TEST", 3)
    first = pq.requeue_batch_identifiers(session, batch_id)
    assert first == 3
    again = pq.requeue_batch_identifiers(session, batch_id)
    assert again == 0
    assert pq.queue_summary(session).total == 3

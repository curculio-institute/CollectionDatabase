"""Plain identification labels — determination labels queued WITHOUT a specimen.

Queued from the Labels tab (like identifier labels): the user picks a taxon, determiner,
date, type status, qualifier, sex and a count, and N determination labels land in the print
queue. Nothing else exists to derive them from, so the queue row carries the content itself
(migration 0071) — the third arm of ``ck_print_queue_exclusive_arc``.
"""
import pytest
from sqlalchemy.exc import IntegrityError

import app.services.labels as lbl
import app.services.print_queue as pq
from app.models import PrintQueue
from app.models.base import _utcnow
from app.services import persons as persons_svc

from tests.test_services import _taxon, _person


def _queue(session, n=3, **kw):
    t = kw.pop("taxon", None) or _taxon(session, "Otiorhynchus", "armadillo", "Rossi, 1792")
    base = dict(
        taxon_id=t.id,
        identified_by_id=_person(session, "Jane Roe").id,
        date_identified="2025-03-04",
        type_status=None, identification_qualifier=None, sex=None,
        count=n,
    )
    base.update(kw)
    added = pq.enqueue_plain_determinations(session, **base)
    session.flush()
    return t, added


def _plain_rows(session, taxon):
    return (session.query(PrintQueue)
            .filter(PrintQueue.taxon_id == taxon.id)
            .order_by(PrintQueue.id).all())


class TestEnqueue:
    def test_one_row_per_label_in_one_group(self, session):
        t, added = _queue(session, n=4)
        rows = _plain_rows(session, t)
        assert added == 4 and len(rows) == 4
        assert {r.label_type for r in rows} == {"determination"}
        assert all(r.collection_object_id is None and r.label_code_id is None for r in rows)
        assert len({r.print_group_id for r in rows}) == 1
        assert {r.source for r in rows} == {pq.SOURCE_IDENTIFICATIONS}

    def test_it_counts_as_determination_labels(self, session):
        before = pq.queue_summary(session).n_determination
        _queue(session, n=2)
        assert pq.queue_summary(session).n_determination == before + 2

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_non_positive_count_is_refused(self, session, bad):
        with pytest.raises(ValueError):
            _queue(session, n=bad)

    def test_an_interval_date_is_refused_with_a_sentence(self, session):
        with pytest.raises(ValueError):
            _queue(session, date_identified="2025-01-01/2025-02-01")

    def test_an_unknown_qualifier_is_refused(self, session):
        with pytest.raises(ValueError):
            _queue(session, identification_qualifier="maybe")


class TestRendering:
    def test_every_queued_label_reaches_the_printed_sheet(self, session):
        t, _ = _queue(session, n=3, identification_qualifier="cf.",
                      type_status="Paratype", sex="male")
        qids = {r.id for r in _plain_rows(session, t)}
        cols = [sp for g in pq.queued_groups(session) for sp in g.specimens
                if sp.det_qid in qids]
        assert len(cols) == 3, "a plain label was dropped from the sheet"
        for sp in cols:
            assert sp.data is None and sp.id_code is None and sp.co_id is None
            d = sp.determination
            assert (d.genus, d.specific_epithet) == ("Otiorhynchus", "armadillo")
            assert d.authorship == "Rossi, 1792"
            assert d.qualifier == "cf." and d.type_status == "Paratype"
            assert d.determiner == "Jane Roe" and d.year == "2025" and d.sex == "male"

    def test_they_print_under_their_own_header(self, session):
        t, _ = _queue(session, n=2)
        gid = _plain_rows(session, t)[0].print_group_id
        qids = {r.id for r in _plain_rows(session, t)}
        group = next(g for g in pq.queued_groups(session)
                     if any(sp.det_qid in qids for sp in g.specimens))
        assert group.source == pq.SOURCE_IDENTIFICATIONS and gid is not None

    def test_the_preview_matches_the_sheet(self, session):
        t, _ = _queue(session, n=2)
        qids = {r.id for r in _plain_rows(session, t)}
        cols = [c for g in pq.preview_model(session) for c in g["specimens"]
                if c["det_qid"] in qids]
        assert len(cols) == 2
        for c in cols:
            assert "armadillo" in c["det_html"] and "Jane Roe" in c["det_auto"]
            assert c["co_id"] is None and c["det_ident"]

    def test_the_html_getters_serve_the_editor(self, session):
        t, _ = _queue(session, n=1)
        qid = _plain_rows(session, t)[0].id
        assert "armadillo" in pq.row_auto_html(session, qid)
        assert "armadillo" in pq.row_current_html(session, qid)


    def test_the_rendered_sheet_carries_each_label_editable(self, session):
        t, _ = _queue(session, n=2)
        html = pq.preview_sheet(session)
        for r in _plain_rows(session, t):
            assert f'data-qid="{r.id}"' in html
        assert "armadillo" in html and pq.SOURCE_IDENTIFICATIONS in html


class TestOverride:
    def test_editing_one_label_edits_its_identical_copies(self, session):
        t, _ = _queue(session, n=3)
        rows = _plain_rows(session, t)
        n = pq.set_override_for_identical(session, rows[0].id, "<div>O. armadillo</div>")
        assert n >= 3
        for r in rows:
            session.refresh(r)
            assert r.text_override and "O. armadillo" in r.text_override
        assert "O. armadillo" in pq.row_current_html(session, rows[1].id)

    def test_a_different_plain_label_is_left_alone(self, session):
        t, _ = _queue(session, n=1)
        other = _taxon(session, "Sitona", "lineatus", "Linnaeus, 1758")
        _queue(session, n=1, taxon=other)
        pq.set_override_for_identical(session, _plain_rows(session, t)[0].id, "<div>X</div>")
        row = _plain_rows(session, other)[0]
        session.refresh(row)
        assert row.text_override is None


class TestSchema:
    def _raw(self, session, **kw):
        base = dict(label_type="determination", created_at=_utcnow(), updated_at=_utcnow())
        base.update(kw)
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(PrintQueue(**base))
                session.flush()

    def test_a_determination_row_needs_a_specimen_or_a_taxon(self, session):
        self._raw(session)

    def test_a_data_row_cannot_be_plain(self, session):
        self._raw(session, label_type="data", taxon_id=_taxon(session).id)

    def test_plain_fields_cannot_ride_on_a_non_plain_row(self, session):
        from app.models import LabelCode
        from app.services.identifiers import reserve_sequential_codes
        reserve_sequential_codes(session, "TEST", 1)
        lc = session.query(LabelCode).order_by(LabelCode.id.desc()).first()
        self._raw(session, label_type="identifier", label_code_id=lc.id, sex="male")

    def test_the_db_refuses_an_interval_date(self, session):
        self._raw(session, taxon_id=_taxon(session).id, date_identified="2025-01-01/2025-02-01")

    def test_the_db_refuses_an_unknown_qualifier(self, session):
        self._raw(session, taxon_id=_taxon(session).id, identification_qualifier="maybe")


class TestReferences:
    def test_merging_the_determiner_repoints_queued_labels(self, session):
        keep = _person(session, "Jane Q. Roe")
        t, _ = _queue(session, n=1)
        row = _plain_rows(session, t)[0]
        absorbed_id = row.identified_by_id
        persons_svc.merge_persons(session, keep.id, absorbed_id)
        session.expire_all()
        assert _plain_rows(session, t)[0].identified_by_id == keep.id

    def test_removing_and_clearing_work_as_for_any_row(self, session):
        t, _ = _queue(session, n=2)
        rows = _plain_rows(session, t)
        pq.remove_item(session, rows[0].id)
        session.expire_all()
        assert len(_plain_rows(session, t)) == 1


def test_migration_0071_keeps_queued_rows_and_round_trips(tmp_path):
    """The rebuild must carry an existing queue across untouched (and back)."""
    import sqlite3
    from alembic.command import downgrade, upgrade
    from alembic.config import Config

    db = tmp_path / "mig.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    upgrade(cfg, "0070")

    con = sqlite3.connect(db)
    con.execute("INSERT INTO label_batch (id, created_at, updated_at) VALUES (1, 't', 't')")
    con.execute("INSERT INTO label_code (id, code, batch_id, created_at, updated_at) "
                "VALUES (1, 'TEST-00001', 1, 't', 't')")
    con.execute("INSERT INTO print_queue (id, label_type, print_group_id, source, "
                "text_override, label_code_id, created_at, updated_at) "
                "VALUES (7, 'identifier', 3, 'New identifiers', NULL, 1, 't', 't')")
    con.commit()
    before = con.execute("SELECT id, label_type, print_group_id, source, label_code_id "
                         "FROM print_queue").fetchall()
    con.close()

    upgrade(cfg, "0071")
    con = sqlite3.connect(db)
    after = con.execute("SELECT id, label_type, print_group_id, source, label_code_id "
                        "FROM print_queue").fetchall()
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='print_queue'").fetchone()[0]
    con.close()
    assert after == before
    assert "STRICT" in sql and "ck_print_queue_plain_fields" in sql

    downgrade(cfg, "0070")
    con = sqlite3.connect(db)
    assert con.execute("SELECT id, label_type, print_group_id, source, label_code_id "
                       "FROM print_queue").fetchall() == before
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='print_queue'").fetchone()[0]
    con.close()
    assert "STRICT" in sql and "taxon_id" not in sql

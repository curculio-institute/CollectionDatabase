"""Event locality labels — data (locality) labels queued for a collecting event WITHOUT a
specimen (Labels tab "Locality labels" card, Records event sheet).

The label is read live from the event through the same renderer as a specimen's data
label (migration 0074 — the data arm of ``ck_print_queue_exclusive_arc`` is now a
specimen XOR an event).
"""
import pytest
from sqlalchemy.exc import IntegrityError

import app.services.print_queue as pq
from app.models import PrintQueue
from app.models.base import _utcnow

from tests.test_services import _event, _taxon


def _queue(session, n=3, ev=None):
    ev = ev or _event(session, locality="Watzmann-Ostwand")
    added = pq.enqueue_event_data(session, collecting_event_id=ev.id, count=n)
    session.flush()
    return ev, added


def _rows(session, ev):
    return (session.query(PrintQueue)
            .filter(PrintQueue.collecting_event_id == ev.id)
            .order_by(PrintQueue.id).all())


class TestEnqueue:
    def test_one_row_per_label_in_one_group(self, session):
        ev, added = _queue(session, n=4)
        rows = _rows(session, ev)
        assert added == 4 and len(rows) == 4
        assert {r.label_type for r in rows} == {"data"}
        assert all(r.collection_object_id is None for r in rows)
        assert len({r.print_group_id for r in rows}) == 1
        assert {r.source for r in rows} == {pq.SOURCE_LOCALITY}

    def test_it_counts_as_data_labels(self, session):
        before = pq.queue_summary(session).n_data
        _queue(session, n=2)
        assert pq.queue_summary(session).n_data == before + 2

    @pytest.mark.parametrize("bad", [0, -1, 501, 2.7, True])
    def test_a_bad_count_is_refused(self, session, bad):
        with pytest.raises(ValueError):
            _queue(session, n=bad)

    def test_an_unknown_event_is_refused(self, session):
        with pytest.raises(ValueError, match="not found"):
            pq.enqueue_event_data(session, collecting_event_id=987654, count=1)


class TestRendering:
    def test_every_label_reaches_the_sheet_with_the_event_text(self, session):
        ev, _ = _queue(session, n=3)
        qids = {r.id for r in _rows(session, ev)}
        cols = [sp for g in pq.queued_groups(session) for sp in g.specimens
                if sp.data_qid in qids]
        assert len(cols) == 3, "an event label was dropped from the sheet"
        for sp in cols:
            assert sp.determination is None and sp.id_code is None and sp.co_id is None
            assert sp.data.locality == "Watzmann-Ostwand"
            assert sp.data.recorded_by == "J. Doe"
            assert not sp.data.associated_species

    def test_they_print_under_their_own_header(self, session):
        ev, _ = _queue(session, n=2)
        qids = {r.id for r in _rows(session, ev)}
        group = next(g for g in pq.queued_groups(session)
                     if any(sp.data_qid in qids for sp in g.specimens))
        assert group.source == pq.SOURCE_LOCALITY

    def test_the_preview_matches_the_sheet(self, session):
        ev, _ = _queue(session, n=2)
        qids = {r.id for r in _rows(session, ev)}
        cols = [c for g in pq.preview_model(session) for c in g["specimens"]
                if c["data_qid"] in qids]
        assert len(cols) == 2
        for c in cols:
            assert "Watzmann-Ostwand" in c["data_auto"]
            assert c["co_id"] is None and c["data_ident"]

    def test_the_html_getters_and_sheet_serve_the_editor(self, session):
        ev, _ = _queue(session, n=1)
        qid = _rows(session, ev)[0].id
        assert "Watzmann-Ostwand" in pq.row_auto_html(session, qid)
        assert "Watzmann-Ostwand" in pq.row_current_html(session, qid)
        html = pq.preview_sheet(session)
        assert f'data-qid="{qid}"' in html and pq.SOURCE_LOCALITY in html

    def test_an_edit_applies_to_identical_copies(self, session):
        ev, _ = _queue(session, n=3)
        rows = _rows(session, ev)
        n = pq.set_override_for_identical(session, rows[0].id, "<div>Watzmann E</div>")
        assert n >= 3
        for r in rows:
            session.refresh(r)
            assert "Watzmann E" in r.text_override
        other, _ = _queue(session, n=1, ev=_event(session, locality="Jenner"))
        assert _rows(session, other)[0].text_override is None


class TestSchema:
    def _raw(self, session, **kw):
        base = dict(label_type="data", created_at=_utcnow(), updated_at=_utcnow())
        base.update(kw)
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(PrintQueue(**base))
                session.flush()

    def test_a_data_row_needs_a_specimen_or_an_event(self, session):
        self._raw(session)

    def test_a_determination_row_cannot_carry_an_event(self, session):
        ev = _event(session)
        self._raw(session, label_type="determination",
                  taxon_id=_taxon(session).id, collecting_event_id=ev.id)

    def test_an_identifier_row_cannot_carry_an_event(self, session):
        from app.models import LabelCode
        from app.services.identifiers import reserve_sequential_codes
        reserve_sequential_codes(session, "TEST", 1)
        lc = session.query(LabelCode).order_by(LabelCode.id.desc()).first()
        self._raw(session, label_type="identifier", label_code_id=lc.id,
                  collecting_event_id=_event(session).id)

    def test_removing_and_clearing_work_as_for_any_row(self, session):
        ev, _ = _queue(session, n=2)
        pq.remove_item(session, _rows(session, ev)[0].id)
        session.expire_all()
        assert len(_rows(session, ev)) == 1


def test_migration_0074_keeps_queued_rows_and_round_trips(tmp_path):
    """The rebuild must carry an existing queue across untouched (and back)."""
    import sqlite3
    from alembic.command import downgrade, upgrade
    from alembic.config import Config

    db = tmp_path / "mig.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    upgrade(cfg, "0073")

    con = sqlite3.connect(db)
    con.execute("INSERT INTO label_batch (id, created_at, updated_at) VALUES (1, 't', 't')")
    con.execute("INSERT INTO label_code (id, code, batch_id, created_at, updated_at) "
                "VALUES (1, 'TEST-00001', 1, 't', 't')")
    con.execute("INSERT INTO print_queue (id, label_type, print_group_id, source, "
                "text_override, label_code_id, created_at, updated_at) "
                "VALUES (7, 'identifier', 3, 'New identifiers', NULL, 1, 't', 't')")
    con.commit()
    cols = "id, label_type, print_group_id, source, label_code_id"
    before = con.execute(f"SELECT {cols} FROM print_queue").fetchall()
    con.close()

    upgrade(cfg, "0074")
    con = sqlite3.connect(db)
    assert con.execute(f"SELECT {cols} FROM print_queue").fetchall() == before
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='print_queue'").fetchone()[0]
    con.close()
    assert "STRICT" in sql and "collecting_event_id" in sql
    assert "ck_print_queue_plain_fields" in sql
    assert "REFERENCES collecting_event (id) ON DELETE CASCADE" in sql

    downgrade(cfg, "0073")
    con = sqlite3.connect(db)
    assert con.execute(f"SELECT {cols} FROM print_queue").fetchall() == before
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='print_queue'").fetchone()[0]
    con.close()
    assert "STRICT" in sql and "collecting_event_id" not in sql

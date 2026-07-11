"""#67 — hand-edited print-queue labels printed blank, dropped text, or lost the edit.

A label is a physical artifact pinned to a specimen: a blank or silently-truncated one is a
real curation error, and "preview ≠ printed" defeats the point of having a preview. Four
distinct defects, one test class each.
"""
import app.services.labels as lbl
import app.services.print_queue as pq
from app.models import PrintQueue
from app.services.identifiers import reserve_sequential_codes
from app.services.specimens import (create_collection_object, finalize_specimen,
                                    save_specimen_entry)

from tests.test_services import _taxon, _event
from tests.helpers import ensure_repo


def _data(**kw) -> lbl.DataLabel:
    base = dict(country="Germany", locality="Augsburg", event_date="2024-06-15")
    base.update(kw)
    return lbl.DataLabel(**base)


class TestAnEmptyOverrideNeverPrintsBlank:
    """`text_override is not None` treated '' as a real override → the label printed EMPTY."""

    def test_every_flavour_of_empty_falls_back_to_the_auto_text(self):
        auto = lbl._data_inner_html(_data())
        assert "Augsburg" in auto
        # ('&nbsp;' is empty only when read as HTML — which the STORE does; see below. A legacy
        # plaintext override literally reading "&nbsp;" is text, and printing it is correct.)
        for empty in ("", "   ", "\n", "<div></div>", "<div><br></div>"):
            rendered = lbl._data_inner_html(_data(text_override=empty))
            assert rendered == auto, f"{empty!r} printed {rendered!r}, not the auto text"

    def test_the_canonical_stored_form_of_empty_is_None(self):
        """Cleared at the source too, so the DB never holds a blank override."""
        for empty in ("", "   ", "<div></div>", "<div><br></div>", "&nbsp;"):
            assert lbl.canonical_override(empty) is None

    def test_a_real_override_still_wins_over_the_auto_text(self):
        html = lbl._data_inner_html(_data(text_override="<div>Bavaria</div>"))
        assert "Bavaria" in html and "Augsburg" not in html


class TestAngleBracketTextIsPrintedNotStripped:
    """`_looks_like_html` was `<\\w+` — any angle-bracketed word — so `Quercus <robur>` was taken
    for markup and `<robur>` was silently removed from the printed label."""

    def test_plain_text_in_angle_brackets_survives(self):
        html = lbl._data_inner_html(_data(text_override="Quercus <robur>"))
        assert "&lt;robur&gt;" in html
        assert lbl.label_plaintext(_data(text_override="Quercus <robur>")) == "Quercus <robur>"

    def test_it_survives_alongside_real_markup(self):
        """An unknown tag inside a genuinely formatted override is still text."""
        src = "<em>Quercus</em> <robur>"
        html = lbl._data_inner_html(_data(text_override=src))
        assert "<em>Quercus</em>" in html      # real markup kept
        assert "&lt;robur&gt;" in html         # unknown tag printed literally

    def test_bare_comparison_signs_survive(self):
        assert lbl.label_plaintext(_data(text_override="a < b > c")) == "a < b > c"


class TestStoreAndRenderAgree:
    """The editor hands us a contenteditable's innerHTML, which is already entity-encoded.
    Storing it raw and sniffing at render time escaped it a SECOND time: `R & D` printed as the
    literal `R &amp; D`. Sanitise on store; render is a pass-through."""

    def test_an_ampersand_typed_in_the_editor_prints_as_an_ampersand(self):
        stored = lbl.canonical_override("R &amp; D")          # what the browser gives us
        assert lbl.label_plaintext(_data(text_override=stored)) == "R & D"

    def test_entities_are_decoded_once_not_twice(self):
        stored = lbl.canonical_override("caf&eacute;")
        assert lbl.label_plaintext(_data(text_override=stored)) == "café"

    def test_canonicalising_is_idempotent(self):
        """The stored form is fed back through on the next edit — it must be a fixed point."""
        once = lbl.canonical_override("<em>Quercus</em> &amp; robur")
        assert lbl.canonical_override(once) == once

    def test_formatting_survives_the_round_trip(self):
        stored = lbl.canonical_override("<i>Quercus</i> <b>robur</b>")
        assert "<em>" in stored and "<strong>" in stored     # mapped to the house tags


class TestDeterminationWithoutACurrentIdentification:
    """A determination row on a specimen with no current ID: the preview grouped such rows and
    let you edit, but the store bailed (`_row_auto_identity` → None → `return 0`) and the edit
    was silently dropped — and even a stored override never reached the paper, because
    `_co_to_det_label` returned None."""

    def _specimen_without_det(self, session, code_prefix="TEST"):
        """A real specimen with NO determination — save_specimen_entry always makes one
        (taxon_id is NOT NULL), so the object is created directly."""
        ce = _event(session)
        _b, codes = reserve_sequential_codes(session, code_prefix, 1)
        code = codes[0]
        co = create_collection_object(
            session, collecting_event_id=ce.id, catalog_number=code,
            repository_id=ensure_repo(session, "TEST"),
        )
        finalize_specimen(session, collection_object_id=co.id, code=code,
                          queue_labels=True, print_group_id=pq.next_print_group_id(session),
                          source=pq.SOURCE_MOUNTING)
        session.flush()
        return co

    def _det_row(self, session, co) -> PrintQueue:
        return (session.query(PrintQueue)
                .filter(PrintQueue.label_type == "determination",
                        PrintQueue.collection_object_id == co.id)
                .one())

    def test_the_edit_is_actually_stored(self, session):
        co = self._specimen_without_det(session)
        row = self._det_row(session, co)
        assert pq._row_auto_identity(row) is None      # no auto text to group by

        n = pq.set_override_for_identical(session, row.id, "<div>Carabus sp.</div>")
        assert n == 1, "the edit was silently dropped"
        session.refresh(row)
        assert row.text_override and "Carabus" in row.text_override

    def test_the_stored_edit_reaches_the_printed_label(self, session):
        co = self._specimen_without_det(session)
        row = self._det_row(session, co)
        pq.set_override_for_identical(session, row.id, "<div>Carabus sp.</div>")
        session.flush()

        dl = pq._co_to_det_label(co, row.text_override)
        assert dl is not None, "the override never reached the label"
        assert "Carabus" in lbl._det_inner_html(dl)
        # …and the preview shows the same thing, so preview == printed.
        col = next(c for g in pq.preview_model(session) for c in g["specimens"]
                   if c.get("det_qid") == row.id)
        assert "Carabus" in col["det_html"]

    def test_such_rows_do_NOT_share_one_identity(self, session):
        """They differ only in being empty. Grouping them would stamp one hand-written name
        onto every determination-less specimen in the queue."""
        co_a = self._specimen_without_det(session)
        co_b = self._specimen_without_det(session)
        row_a, row_b = self._det_row(session, co_a), self._det_row(session, co_b)

        n = pq.set_override_for_identical(session, row_a.id, "<div>Carabus sp.</div>")
        session.flush()
        assert n == 1                                   # only its own row
        session.refresh(row_b)
        assert row_b.text_override is None, "the edit leaked onto another specimen"

        # The preview must not claim they are identical either (no shared highlight group).
        cols = {c["det_qid"]: c for g in pq.preview_model(session)
                for c in g["specimens"] if c.get("det_qid")}
        assert cols[row_b.id]["det_ident"] is None

    def test_clearing_it_removes_the_override(self, session):
        co = self._specimen_without_det(session)
        row = self._det_row(session, co)
        pq.set_override_for_identical(session, row.id, "<div>Carabus sp.</div>")
        session.flush()
        pq.set_override_for_identical(session, row.id, "")
        session.flush()
        session.refresh(row)
        assert row.text_override is None
        assert pq._co_to_det_label(co, row.text_override) is None


class TestIdenticalLabelsStillEditTogether:
    """The batch-edit behaviour (#37) must survive the fix: two specimens sharing an event have
    the same auto data text, so editing one data label edits both."""

    def test_two_specimens_on_one_event_share_a_data_override(self, session):
        t = _taxon(session)
        ce = _event(session)
        _b, codes = reserve_sequential_codes(session, "TEST", 2)
        gid = pq.next_print_group_id(session)
        for code in codes:
            co = save_specimen_entry(
                session, taxon_id=t.id, event_id=ce.id, event_fields={},
                specimen_fields={"catalog_number": code,
                                 "repository_id": ensure_repo(session, "TEST")},
                determination_fields={},
            )
            finalize_specimen(session, collection_object_id=co.id, code=code,
                              queue_labels=True, print_group_id=gid,
                              source=pq.SOURCE_MOUNTING)
        session.flush()

        rows = session.query(PrintQueue).filter(PrintQueue.label_type == "data").all()
        assert len(rows) == 2
        n = pq.set_override_for_identical(session, rows[0].id, "<div>Bavaria</div>")
        assert n == 2, "identical data labels must edit together"
        session.flush()
        for r in rows:
            session.refresh(r)
            assert "Bavaria" in (r.text_override or "")

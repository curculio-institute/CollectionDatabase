"""Save stops shared by Digitize and Mounting: staged event annotations on a REUSED event
ask first (confirm_staged_on_existing_event); a staged association whose relationship
was merged or deleted is refused by name (check_staged_relationships)."""
import asyncio
from contextlib import nullcontext

import pytest

import app.services.biological as bio
import app.ui.confirm_dialog as confirm_dialog
from app.ui.annotation_panel import confirm_staged_on_existing_event
from tests.annotation_helpers import make_event, make_specimen


def _handle(items):
    store = list(items)
    cleared = []
    return {"staged_items": store,
            "clear": lambda: (store.clear(), cleared.append(True))}, cleared


def _run(session, handle, event_id, monkeypatch, answer):
    asked = []

    async def fake_choose(**kw):
        asked.append(kw)
        return answer
    monkeypatch.setattr(confirm_dialog, "choose", fake_choose)
    ok = asyncio.run(confirm_staged_on_existing_event(lambda: nullcontext(session),
                                                      handle, event_id))
    return ok, asked


def test_new_event_or_nothing_staged_asks_nothing(session, monkeypatch):
    h, _ = _handle([{"term_id": 1, "value": None}])
    assert _run(session, h, None, monkeypatch, None) == (True, [])
    ev = make_event(session)
    empty, _ = _handle([])
    assert _run(session, empty, ev.id, monkeypatch, None) == (True, [])


@pytest.mark.parametrize("answer, proceeds, dropped", [
    (None, False, False), ("drop", True, True), ("add", True, False)])
def test_reused_event_asks(session, monkeypatch, answer, proceeds, dropped):
    ev = make_event(session)
    make_specimen(session, "STOP-1", event_id=ev.id)
    make_specimen(session, "STOP-2", event_id=ev.id)
    h, cleared = _handle([{"term_id": 1, "value": None}, {"term_id": 2, "value": "x"}])
    ok, asked = _run(session, h, ev.id, monkeypatch, answer)
    assert ok is proceeds
    assert bool(cleared) is dropped and (h["staged_items"] == []) is dropped
    (kw,) = asked
    assert kw["title"] == "Add annotations to an existing event?"
    assert "2 event annotations" in kw["body"] and f"#{ev.id}" in kw["body"]
    assert "2 specimens" in kw["body"]
    assert kw["options"] == [("drop", "Save without them", "secondary"),
                             ("add", f"Add them to event #{ev.id}", "primary")]


def test_staged_association_with_a_gone_relationship_is_refused(session):
    rel = bio.create_relationship(session, "feeds on")
    bio.check_staged_relationships(session, [{"rel_id": rel.id, "rel_name": "feeds on"}])
    with pytest.raises(ValueError, match="'eats' was merged or deleted"):
        bio.check_staged_relationships(session, [{"rel_id": rel.id + 999,
                                                  "rel_name": "eats"}])

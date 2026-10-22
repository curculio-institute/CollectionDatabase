"""Digitize → Mounting: "Recent events" card.

The latest collecting events (by last update), each expandable to the specimens collected
there. "Use" links an event into the shared event form, so the mounting session adds its
specimens to it — e.g. an event saved earlier with "Save event only".

Rows reuse the shared renderers: `record_summary.event_html` for the event line and the
Records event sheet's specimen row (`record_sheet._co_summary` → `specimen_html`).
"""
from __future__ import annotations

from nicegui import ui

import app.services.events as ev_svc
import app.ui.record_summary as rs
from app.models import CollectingEvent
from app.ui.record_sheet import _co_summary

_LIMIT = 8
_MAX_SPECIMENS = 25     # per event: an imported event can hold hundreds


def _load(session_factory) -> list[dict]:
    with session_factory() as s:
        out = []
        for r in ev_svc.recent_events(s, limit=_LIMIT):
            ev = s.get(CollectingEvent, r.id)
            cos = ev.collection_objects[:_MAX_SPECIMENS]
            out.append({"row": r, "specimens": [_co_summary(s, co) for co in cos]})
        return out


def build_recent_events_card(session_factory, *, on_use_event) -> dict:
    """Render the card. ``on_use_event(event_id)`` links the event into the form.
    Returns ``{"refresh": callable}``."""
    ui.add_head_html(rs.CSS)

    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-2 mb-1"):
            ui.label("Recent events").classes("section-label")
            ui.space()
            ui.button("", icon="refresh", on_click=lambda: refresh()) \
                .props("flat dense round").tooltip("Refresh")
        body = ui.column().classes("w-full gap-0")

    def refresh() -> None:
        items = _load(session_factory)
        body.clear()
        with body:
            if not items:
                ui.label("No events yet.").classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
            for it in items:
                r, specimens = it["row"], it["specimens"]
                with ui.row().classes("w-full items-start gap-2 no-wrap"):
                    with ui.expansion().props("dense").classes("flex-1 min-w-0") as exp:
                        with exp.add_slot("header"):
                            ui.html(rs.event_html(summary=r.summary,
                                                  n_specimens=r.n_specimens,
                                                  confidential=r.confidential)) \
                                .classes("w-full")
                        if specimens:
                            for sp in specimens:
                                ui.html(rs.specimen_html(
                                    catalog=sp["catalog"], name=sp["name"], rank=sp["rank"],
                                    authorship=sp["authorship"], qualifier=sp["qualifier"],
                                    hosts=sp["hosts"], sex=sp["sex"], count=sp["count"],
                                    locality="", identified_by=sp["identified_by"],
                                    date_identified=sp["date_identified"],
                                    confidential=sp["confidential"],
                                    event_confidential=sp["event_confidential"],
                                    recorded_by_state=sp["recorded_by_state"],
                                    determination_reasons=sp["determination_reasons"],
                                )).classes("w-full pl-4")
                            more = r.n_specimens - len(specimens)
                            if more > 0:
                                ui.label(f"… and {more} more — see the event in Records.") \
                                    .classes("text-sm italic pl-4") \
                                    .style("color:var(--tp-base-soft)")
                        else:
                            ui.label("No specimens yet.").classes("text-sm italic pl-4") \
                                .style("color:var(--tp-base-soft)")
                    ui.button("Use", icon="input",
                              on_click=lambda _, eid=r.id: on_use_event(eid)) \
                        .props("flat dense no-caps size=sm color=secondary") \
                        .classes("mt-1") \
                        .tooltip("Use this event for the mounting session")

    refresh()
    return {"refresh": refresh}

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import CollectingEvent
from app.models.base import _utcnow
from app.services.label_text import format_locality_label

_FLOAT_ATTRS = frozenset({
    "decimal_latitude",
    "decimal_longitude",
    "coordinate_uncertainty_in_meters",
    "coordinate_precision",
    "minimum_elevation_in_meters",
    "maximum_elevation_in_meters",
})


@dataclass(frozen=True)
class EventOption:
    id: int
    summary: str


def format_event_summary(event: CollectingEvent) -> str:
    """One-line label for the picker dropdown."""
    summary = format_locality_label(event, html=False)
    return summary or f"Event #{event.id}"


def search_collecting_events(
    session: Session, query: str, limit: int = 1000
) -> list[EventOption]:
    """Search across all text-bearing locality/date/collector fields.
    Empty query returns most-recent `limit` events."""
    q = session.query(CollectingEvent)
    if query.strip():
        pat = f"%{query.strip()}%"
        q = q.filter(
            CollectingEvent.country.ilike(pat)
            | CollectingEvent.state_province.ilike(pat)
            | CollectingEvent.county.ilike(pat)
            | CollectingEvent.municipality.ilike(pat)
            | CollectingEvent.island.ilike(pat)
            | CollectingEvent.locality.ilike(pat)
            | CollectingEvent.verbatim_locality.ilike(pat)
            | CollectingEvent.event_date.ilike(pat)
            | CollectingEvent.verbatim_event_date.ilike(pat)
            | CollectingEvent.recorded_by.ilike(pat)
            | CollectingEvent.habitat.ilike(pat)
        )
    q = q.order_by(CollectingEvent.id.desc()).limit(limit)
    return [EventOption(id=e.id, summary=format_event_summary(e)) for e in q]


def get_event(session: Session, event_id: int) -> CollectingEvent | None:
    return session.get(CollectingEvent, event_id)


def create_collecting_event(session: Session, **fields) -> CollectingEvent:
    """Insert a new collecting_event. Coerces '' -> None and str -> float for
    numeric columns. ISO-8601 date strings are stored as-is."""
    ce = CollectingEvent(created_at=_utcnow(), updated_at=_utcnow())
    for attr, val in fields.items():
        if val is None or val == "":
            continue
        if attr in _FLOAT_ATTRS:
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        setattr(ce, attr, val)
    session.add(ce)
    session.flush()
    return ce

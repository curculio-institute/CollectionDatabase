from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import CollectingEvent
from app.models.person import Person
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
        q = (
            q.outerjoin(Person, Person.id == CollectingEvent.recorded_by_id)
            .filter(
                CollectingEvent.country.ilike(pat)
                | CollectingEvent.state_province.ilike(pat)
                | CollectingEvent.county.ilike(pat)
                | CollectingEvent.municipality.ilike(pat)
                | CollectingEvent.island.ilike(pat)
                | CollectingEvent.locality.ilike(pat)
                | CollectingEvent.verbatim_locality.ilike(pat)
                | CollectingEvent.event_date.ilike(pat)
                | CollectingEvent.verbatim_event_date.ilike(pat)
                | Person.full_name.ilike(pat)
                | CollectingEvent.habitat.ilike(pat)
            )
        )
    q = q.order_by(CollectingEvent.id.desc()).limit(limit)
    return [EventOption(id=e.id, summary=format_event_summary(e)) for e in q]


def get_event(session: Session, event_id: int) -> CollectingEvent | None:
    return session.get(CollectingEvent, event_id)


def event_form_snapshot(session: Session, event_id: int) -> dict | None:
    """Snapshot an event into the dict shape build_collecting_event_form.load()
    expects (keys = the form's field names + recorded_by full_name).

    Built inside the session so the lazy recorded_by_person relationship resolves
    before the event detaches (avoids DetachedInstanceError). Returns None if the
    event is missing."""
    ev = session.get(CollectingEvent, event_id)
    if ev is None:
        return None
    return {
        "country":                          ev.country,
        "country_code":                     ev.country_code,
        "state_province":                   ev.state_province,
        "county":                           ev.county,
        "municipality":                     ev.municipality,
        "island":                           ev.island,
        "locality":                         ev.locality,
        "verbatim_locality":                ev.verbatim_locality,
        "event_date":                       ev.event_date,
        "verbatim_event_date":              ev.verbatim_event_date,
        "decimal_latitude":                 ev.decimal_latitude,
        "decimal_longitude":                ev.decimal_longitude,
        "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters,
        "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters,
        "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters,
        "habitat":                          ev.habitat,
        "sampling_protocol":                ev.sampling_protocol,
        "field_number":                     ev.field_number,
        "verbatim_label":                   ev.verbatim_label,
        "recorded_by": ev.recorded_by_person.full_name if ev.recorded_by_person else None,
    }


def update_collecting_event(session: Session, event_id: int, **fields) -> CollectingEvent:
    """Update fields on an existing CollectingEvent. Empty string → None."""
    ev = session.get(CollectingEvent, event_id)
    if ev is None:
        raise ValueError(f"CollectingEvent {event_id} not found")
    for attr, val in fields.items():
        if val == "":
            val = None
        if val is not None and attr in _FLOAT_ATTRS:
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = None
        setattr(ev, attr, val)
    ev.updated_at = _utcnow()
    session.flush()
    return ev


def count_co_at_event(session: Session, event_id: int) -> int:
    """Number of CollectionObjects linked to this event."""
    from app.models import CollectionObject as _CO
    return session.query(_CO).filter(_CO.collecting_event_id == event_id).count()


def copy_and_relink_event(session: Session, co_id: int) -> int:
    """Copy the collecting event linked to co_id, relink only this specimen.

    Returns the new event id. The original event is untouched.
    """
    from sqlalchemy import inspect as _inspect
    from app.models import CollectionObject as _CO
    co = session.get(_CO, co_id)
    if co is None:
        raise ValueError(f"CollectionObject {co_id} not found")
    if co.collecting_event_id is None:
        raise ValueError("Specimen has no collecting event to detach")
    old_ev = co.collecting_event
    new_ev = CollectingEvent(created_at=_utcnow(), updated_at=_utcnow())
    skip = {"id", "created_at", "updated_at"}
    for col_attr in _inspect(CollectingEvent).mapper.column_attrs:
        if col_attr.key in skip:
            continue
        setattr(new_ev, col_attr.key, getattr(old_ev, col_attr.key))
    session.add(new_ev)
    session.flush()
    co.collecting_event_id = new_ev.id
    co.updated_at = _utcnow()
    session.flush()
    return new_ev.id


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

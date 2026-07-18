from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models import CollectingEvent
from app.models.person import Person
from app.models.habitat import Habitat
from app.models.geography import Country, StateProvince, County, Island
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

# Free-text event field names that are actually FK-backed geography vocabularies.
# The form / importer pass them as names; resolve name → *_id (get_or_create) in one
# place so every write path is covered. administrative_region has no DwC term.
_GEO_TEXT_TO_FK = {
    "country":               "country_id",
    "state_province":        "state_province_id",
    "administrative_region": "administrative_region_id",
    "county":                "county_id",
    "island":                "island_id",
}


def _resolve_geo_fields(session: Session, fields: dict) -> dict:
    """Convert any geography NAME keys in `fields` to their FK *_id (creating the
    vocab row when needed; blank → None). Mutates and returns `fields`.

    Also consumes the non-column keys `country_iso` / `state_province_iso`, which identify
    *which* vocab row a name means (Limburg BE-VLI vs NL-LI) — neither is an event column.
    """
    from app.services.vocabularies import (
        country_vocab, state_province_vocab, administrative_region_vocab,
        county_vocab, island_vocab,
    )
    vocabs = {
        "country": country_vocab, "state_province": state_province_vocab,
        "administrative_region": administrative_region_vocab,
        "county": county_vocab, "island": island_vocab,
    }
    # ISO codes for the two code-bearing vocabs. A row's identity is (name, code), so an
    # exact match is reused and anything else creates a new row — never a mutation of an
    # existing row, never a refused save. "Limburg" (BE-VLI) and "Limburg" (NL-LI) coexist;
    # a hand-typed uncoded "Limburg" is a third row the user can merge later. Migration 0056.
    state_iso = (fields.pop("state_province_iso", "") or "").strip().upper()
    # The country's code identifies its vocab row. There is no event column to fall back on:
    # dwc:countryCode was dropped in 0057 because a stored copy drifted from country.iso_code
    # (`Germany` could carry `FR`). The DwC export derives it from the row.
    country_iso = (fields.pop("country_iso", "") or "").strip().upper()
    _check_state_inside_country(country_iso, state_iso)

    codes = {"state_province": state_iso, "country": country_iso}

    # A countryCode with no country name must not be dropped: a DwC file may carry
    # countryCode on a row whose `country` column is blank. `... if val else None`
    # below would skip such a row and silently discard the code, so materialise the
    # country by deriving its name from the code first. ISO 3166-1 alpha-2 is an
    # unambiguous standard, so this is a lookup, not a guess (CLAUDE.md "no hardcoded
    # country codes"). **Country only** — ISO 3166-2 subdivision names are never
    # derived (the 40 shared names are the whole reason the strict identity exists),
    # and `country.name` is NOT NULL so a code-only row is not representable anyway.
    if country_iso and not (fields.get("country") or "").strip():
        derived = _country_name_from_iso(country_iso)
        if derived:
            fields["country"] = derived

    for name_key, id_key in _GEO_TEXT_TO_FK.items():
        if name_key in fields:
            val = (fields.pop(name_key) or "").strip()
            code = codes.get(name_key) or None
            row = vocabs[name_key].get_or_create(session, val, code=code) if val else None
            fields[id_key] = row.id if row else None
    return fields


def _country_name_from_iso(iso_code: str) -> str | None:
    """The ISO 3166-1 English short name for an alpha-2 code, or None if unknown.

    Uses pycountry (the sanctioned source; never a hand-rolled dict). The name matches
    the geocoder's `name:en` for the common cases (`DE` → "Germany", `GB` → "United
    Kingdom"), so a derived row folds cleanly with a geocoded one of the same country.
    """
    import pycountry
    rec = pycountry.countries.get(alpha_2=iso_code.strip().upper())
    return rec.name if rec else None


def _check_state_inside_country(country_iso: str, state_iso: str) -> None:
    """An ISO 3166-2 code begins with the ISO 3166-1 code of its country, by definition.

    So `DE-BY` in country `GR` is not a matter of taste — it is a contradiction the codes
    themselves expose, and it means the event names a state outside its country. Refuse the
    save rather than store an impossible locality (CLAUDE.md §2). Only checked when *both*
    codes are known; an uncoded row asserts nothing and is left alone.
    """
    if not (country_iso and state_iso):
        return
    prefix = state_iso.split("-", 1)[0]
    if prefix != country_iso:
        raise ValueError(
            f"stateProvince {state_iso} lies in country {prefix}, not in {country_iso}. "
            "A state cannot be outside its country — fix the country or the state.")


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
            .outerjoin(Habitat, Habitat.id == CollectingEvent.habitat_id)
            .outerjoin(Country, Country.id == CollectingEvent.country_id)
            .outerjoin(StateProvince, StateProvince.id == CollectingEvent.state_province_id)
            .outerjoin(County, County.id == CollectingEvent.county_id)
            .outerjoin(Island, Island.id == CollectingEvent.island_id)
            .filter(
                CollectingEvent.municipality.ilike(pat)
                | CollectingEvent.locality.ilike(pat)
                | CollectingEvent.verbatim_locality.ilike(pat)
                | CollectingEvent.event_date.ilike(pat)
                | CollectingEvent.verbatim_event_date.ilike(pat)
                | Person.full_name.ilike(pat)
                | Habitat.name.ilike(pat)
                | Country.name.ilike(pat)
                | StateProvince.name.ilike(pat)
                | County.name.ilike(pat)
                | Island.name.ilike(pat)
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
        "country":                          ev.country_obj.name if ev.country_obj else None,
        "country_iso":                      ev.country_obj.iso_code if ev.country_obj else None,
        "state_province":                   ev.state_province_obj.name if ev.state_province_obj else None,
        "state_province_iso":               ev.state_province_obj.iso_code if ev.state_province_obj else None,
        "administrative_region":            ev.administrative_region_obj.name if ev.administrative_region_obj else None,
        "county":                           ev.county_obj.name if ev.county_obj else None,
        "municipality":                     ev.municipality,
        "island":                           ev.island_obj.name if ev.island_obj else None,
        "locality":                         ev.locality,
        "verbatim_locality":                ev.verbatim_locality,
        "event_date":                       ev.event_date,
        "verbatim_event_date":              ev.verbatim_event_date,
        "decimal_latitude":                 ev.decimal_latitude,
        "decimal_longitude":                ev.decimal_longitude,
        "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters,
        "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters,
        "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters,
        "habitat":                          ev.habitat_obj.name if ev.habitat_obj else None,
        "sampling_protocol":                ev.sampling_protocol_obj.name if ev.sampling_protocol_obj else None,
        "field_number":                     ev.field_number,
        "verbatim_label":                   ev.verbatim_label,
        "recorded_by": ev.recorded_by_person.full_name if ev.recorded_by_person else None,
    }


def update_collecting_event(session: Session, event_id: int, **fields) -> CollectingEvent:
    """Update fields on an existing CollectingEvent. Empty string → None."""
    ev = session.get(CollectingEvent, event_id)
    if ev is None:
        raise ValueError(f"CollectingEvent {event_id} not found")
    _resolve_geo_fields(session, fields)
    _reject_unknown_event_keys(fields)
    for attr, val in fields.items():
        if val == "":
            val = None
        if val is not None and attr in _FLOAT_ATTRS:
            try:
                val = float(val)
            except (TypeError, ValueError):
                # Bad number (typo like "48,1"): refuse the save so the user
                # fixes it, rather than silently NULLing a good coordinate (#62).
                raise ValueError(
                    f"{attr.replace('_', ' ')} must be a number (got {val!r}).")
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


def _reject_unknown_event_keys(fields: dict) -> None:
    """Fail loudly on any key that is not a real collecting_event column (after geo
    name→id resolution). A NAME key that a caller forgot to resolve — e.g.
    ``recorded_by`` instead of ``recorded_by_id`` — would otherwise be silently
    dropped by the setattr loop, losing data with no warning (#61; "never skip
    silently")."""
    valid = {c.key for c in sa_inspect(CollectingEvent).mapper.column_attrs}
    unknown = set(fields) - valid
    if unknown:
        raise ValueError(
            "unknown collecting_event field(s): " + ", ".join(sorted(unknown))
            + " — resolve any name key to its FK id (e.g. recorded_by → recorded_by_id) "
            "before saving.")


# Columns that are not part of an event's identity for create/matching purposes.
_EVENT_IDENTITY_SKIP = frozenset({"id", "created_at", "updated_at"})
# Columns whose "unset" stored value is not NULL but a default, so an exact-match
# comparison must treat a missing field as the default, not as NULL.
_EVENT_COLUMN_DEFAULTS = {"geodetic_datum": "WGS84", "confidential": 0}


def _event_data_columns() -> list[str]:
    return [c.key for c in sa_inspect(CollectingEvent).mapper.column_attrs
            if c.key not in _EVENT_IDENTITY_SKIP]


def _normalize_event_fields(session: Session, fields: dict) -> dict:
    """Resolve geo names → FK ids, coerce '' → None and numeric strings → float,
    and return a full ``{column: stored_value}`` map covering *every* data column
    (an unset column takes its default: None, or WGS84 / 0).

    This is exactly what a row created from `fields` stores, so the one map drives
    both the insert and the 100%-identical match — there is no second place the
    normalisation could drift."""
    _resolve_geo_fields(session, fields)
    _reject_unknown_event_keys(fields)
    target: dict = {}
    for col in _event_data_columns():
        raw = fields.get(col)
        if raw is None or raw == "":
            target[col] = _EVENT_COLUMN_DEFAULTS.get(col)
        elif col in _FLOAT_ATTRS:
            try:
                target[col] = float(raw)
            except (TypeError, ValueError):
                # Never skip a bad number silently — refuse the save (#62).
                raise ValueError(
                    f"{col.replace('_', ' ')} must be a number (got {raw!r}).")
        else:
            target[col] = raw
    return target


def _insert_event(session: Session, target: dict) -> CollectingEvent:
    ce = CollectingEvent(created_at=_utcnow(), updated_at=_utcnow())
    for col, val in target.items():
        if val is not None:
            setattr(ce, col, val)
    session.add(ce)
    session.flush()
    return ce


def create_collecting_event(session: Session, **fields) -> CollectingEvent:
    """Insert a new collecting_event. Coerces '' -> None and str -> float for
    numeric columns. ISO-8601 date strings are stored as-is."""
    return _insert_event(session, _normalize_event_fields(session, fields))


def get_or_create_exact_event(
    session: Session, **fields
) -> tuple[CollectingEvent, bool]:
    """Reuse an existing collecting_event that is **100% identical** to `fields`;
    otherwise create one. Returns ``(event, created)``.

    "Identical" means every data column equals what a new row built from these
    fields would store (`_normalize_event_fields` — same geo resolution, float
    coercion and defaults as the insert), so a match is exact: an event that also
    carries a coordinate, a habitat, or any other filled column the new fields lack
    is *not* reused. Erring toward create is safe (at worst a duplicate, the
    current behaviour); a false match would silently attach a specimen to the wrong
    locality, which this must never do (CLAUDE.md §2).

    Used by Import & Assign so a batch sharing one collecting event does not spawn
    1400 identical event rows. Other create paths keep making fresh events.

    If the DB already holds *several* identical rows (e.g. duplicates created before
    this dedup existed, or by a create path that does not dedup), the **oldest** one
    is reused — deterministically, via ``order_by(id)`` — and no new row is added.
    Pre-existing duplicates are left as they are; this prevents new ones, it does not
    merge old ones.
    """
    target = _normalize_event_fields(session, fields)
    q = session.query(CollectingEvent)
    for col, val in target.items():
        attr = getattr(CollectingEvent, col)
        q = q.filter(attr.is_(None) if val is None else attr == val)
    existing = q.order_by(CollectingEvent.id).first()
    if existing is not None:
        return existing, False
    return _insert_event(session, target), True

"""Locality label text formatter — shared by dropdown previews and label PDFs."""
from __future__ import annotations

import html as _html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import CollectingEvent

# Country names longer than this use the 2-letter ISO code instead.
_COUNTRY_THRESHOLD = 10


def abbreviate_name(full_name: str | None) -> str | None:
    """Abbreviate a collector name to initial + surname(s).

    'Jakob Jilg'         → 'J. Jilg'
    'Johann Karl Müller' → 'J. Müller'
    'Müller'             → 'Müller'   (single token, unchanged)
    'J. Jilg'            → 'J. Jilg'  (already abbreviated)
    """
    if not full_name:
        return None
    parts = full_name.strip().split()
    if len(parts) <= 1:
        return full_name
    first = parts[0].rstrip(".")
    if len(first) == 1:
        return full_name  # already an initial
    return f"{first[0]}. {' '.join(parts[1:])}"


def format_coords(
    lat: float | None,
    lon: float | None,
    uncertainty_m: float | None = None,
) -> str:
    """Format decimal coordinates with optional uncertainty.

    Returns e.g. '48.1234, 11.5432 ±250m' or '48.1234, 11.5432 ±2.5km'.
    Returns '' when lat or lon is absent.
    """
    if lat is None or lon is None:
        return ""
    s = f"{lat:.4f}, {lon:.4f}"
    if uncertainty_m is not None:
        if uncertainty_m < 1000:
            s += f" ±{round(uncertainty_m)}m"
        else:
            s += f" ±{uncertainty_m / 1000:.1f}km"
    return s


def format_country(country: str | None, code: str | None, *, html: bool = False) -> str:
    """Country for a locality label: full name, or the 2-letter ISO code when the
    name is longer than the threshold. Shared by the label PDF and the previews."""
    country = country or ""
    code = code or ""
    chosen = code if (country and len(country) > _COUNTRY_THRESHOLD and code) else country
    return (_html.escape(chosen) if html else chosen) if chosen else ""


def format_locality_label(
    ev: "CollectingEvent | None",
    associated_species: list[str] | None = None,
    *,
    html: bool = False,
) -> str:
    """Build a single-string locality label from a CollectingEvent.

    Format: Country: stateProvince, Municipality, Locality, lat lon ±Xm,
            Habitat, associated species, leg. A. Surname Date

    html=True  — HTML-escapes plain parts; wraps associated species in <em>.
    html=False — plain text; for dropdown previews and editable print fields.
    """
    if ev is None:
        return ""

    def _e(v: str | None) -> str:
        return (_html.escape(v) if html else v) if v else ""

    country_str = format_country(ev.country, ev.country_code, html=html)

    parts: list[str] = []

    for field in (ev.state_province, ev.municipality, ev.locality):
        if field:
            parts.append(_e(field))
    if not ev.locality and not ev.municipality and not ev.state_province and ev.verbatim_locality:
        parts.append(_e(ev.verbatim_locality))

    coords = format_coords(
        ev.decimal_latitude,
        ev.decimal_longitude,
        ev.coordinate_uncertainty_in_meters,
    )
    if coords:
        # coords is derived from floats — no HTML special chars
        parts.append(coords)

    _habitat = ev.habitat_obj.name if ev.habitat_obj else None
    if _habitat:
        parts.append(_e(_habitat))

    if associated_species:
        for sp in associated_species:
            if html:
                parts.append(f"<em>{_html.escape(sp)}</em>")
            else:
                parts.append(sp)

    name = abbreviate_name(ev.recorded_by_person.full_name if ev.recorded_by_person else None)
    date = ev.event_date or ""
    leg_tokens = [p for p in [_e(name), _e(date)] if p]
    if leg_tokens:
        parts.append("leg. " + " ".join(leg_tokens))

    body = ", ".join(parts)
    if country_str:
        return f"{country_str}: {body}" if body else country_str
    return body


def format_event_preview_html(ev: "CollectingEvent | None") -> str:
    """HTML event summary for UI previews: the same text as the printed label
    (`format_locality_label`), with the eventDate highlighted prominently. Call
    inside a session — it reads `ev.recorded_by_person`. Returns "" for None."""
    if ev is None:
        return ""
    text = format_locality_label(ev, html=True)
    date = ev.event_date
    if date:
        esc = _html.escape(date)
        highlighted = (f'<b style="color:var(--tp-accent,#1a6fa8); font-size:1.1em; '
                       f'letter-spacing:0.02em">{esc}</b>')
        if esc in text:
            text = text.replace(esc, highlighted)
        else:
            text = f"{highlighted} · {text}" if text else highlighted
    # DB id, so the user can trace a specific event when debugging data.
    if ev.id is not None:
        eid = (f'<span style="color:var(--tp-base-soft,#888); font-weight:600">'
               f'#{ev.id}</span>')
        text = f"{eid} · {text}" if text else eid
    return text

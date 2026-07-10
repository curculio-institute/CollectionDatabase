"""Locality label text formatter — shared by dropdown previews and label PDFs."""
from __future__ import annotations

import html as _html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import CollectingEvent

# Country names longer than this use the 2-letter ISO code instead.
_COUNTRY_THRESHOLD = 10
# Combined length of "Country" + "State" above which one of the two collapses to its ISO
# code. Measured in characters — a crude proxy, but the prefix is the only part of the label
# with a substitute short form, and the real fit test (_fits_one_line, WeasyPrint) governs
# the line as a whole. "Germany"+"Bavaria" = 14 fits; +"Baden-Württemberg" = 24 does not.
_PREFIX_BUDGET = 20


def abbreviate_name(full_name: str | None) -> str | None:
    """Abbreviate a collector name to initial + surname(s).

    'John Doe'         → 'J. Doe'
    'Johann Karl Müller' → 'J. Müller'
    'Müller'             → 'Müller'   (single token, unchanged)
    'J. Doe'            → 'J. Doe'  (already abbreviated)
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


def _state_suffix(state_code: str) -> str:
    """The subdivision part of an ISO 3166-2 code: "DE-BY" → "BY", "GR-J" → "J"."""
    return state_code.split("-", 1)[1] if "-" in state_code else state_code


def format_geo_prefix(
    country: str | None, country_code: str | None,
    state: str | None, state_code: str | None,
) -> tuple[str, str]:
    """The "Country, State" prefix of a locality label, collapsed to fit 18 mm.

    Returns ``(country_text, state_text)``; either may be "". The caller joins them with
    ", " and follows with ": ".

    **At most one of the two ever collapses.** "DE, BY" is a cipher; keeping one name
    written out keeps the label recognisable at a glance:

        Germany, Bavaria             both short — nothing to do
        Germany, BW                  Baden-Württemberg is the long one → it gives way
        GR, Peloponnese Region       the state cannot collapse usefully → the country does

    The longer name gives way, since that is what buys the space. The state collapses to
    the **subdivision suffix** ("BY"), which is unambiguous precisely because the country
    is spelled out beside it — but only when that suffix reads as an abbreviation: **at
    least two letters, no digits**. ``GR-J`` → "J" says nothing; ``LK-2`` → "2" and
    ``FR-2A`` → "2A" read as a typo or a measurement, not a place. In those cases the state
    keeps its name and the **country** collapses instead.

    A row with no ISO code cannot collapse; its name stays and the label grows rather than
    losing the locality. With no state at all, the country falls back to the plain
    long-name rule (``format_country``), so a lone "United Kingdom" still prints "GB".
    """
    country, country_code = country or "", country_code or ""
    state, state_code = state or "", state_code or ""

    if not state:
        return format_country(country, country_code), ""
    if not country:
        return "", state

    if len(country) + len(state) <= _PREFIX_BUDGET:
        return country, state

    suffix = _state_suffix(state_code)
    # The suffix must read as an abbreviation of a name: at least two letters, no digits.
    # One character says nothing ("Greece, J"), and a code with digits reads as a typo or a
    # measurement rather than a place ("Kenya, 400", "France, 2A"). Anything that fails this
    # keeps its full name, and the country collapses instead — there is always that fallback,
    # so refusing an unhelpful abbreviation costs nothing.
    state_collapsible = bool(state_code) and len(suffix) >= 2 and suffix.isalpha()

    # Collapse the longer name — that is where the space is. Fall back to the other when
    # the preferred one has nothing to collapse to.
    if state_collapsible and len(state) >= len(country):
        return country, suffix
    if country_code:
        return country_code, state
    if state_collapsible:
        return country, suffix
    return country, state          # neither has a code: keep both, let the label grow


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

    # country / stateProvince are controlled-vocab FKs (resolve to their name); county
    # is too but is omitted from the label line (kept concise — as before, the line
    # shows state → municipality → locality).
    # The ISO codes come from the vocab rows, not from the event (migrations 0055/0057):
    # one fact, one home. An uncoded row simply has no code to shorten its name with.
    _country = ev.country_obj.name if ev.country_obj else None
    _state = ev.state_province_obj.name if ev.state_province_obj else None
    _country_code = ev.country_obj.iso_code if ev.country_obj else None
    _state_code = ev.state_province_obj.iso_code if ev.state_province_obj else None
    _c_text, _s_text = format_geo_prefix(_country, _country_code, _state, _state_code)
    prefix = ", ".join(_e(t) for t in (_c_text, _s_text) if t)

    parts: list[str] = []

    for field in (ev.municipality, ev.locality):
        if field:
            parts.append(_e(field))
    if not ev.locality and not ev.municipality and not _state and ev.verbatim_locality:
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
    if prefix:
        return f"{prefix}: {body}" if body else prefix
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

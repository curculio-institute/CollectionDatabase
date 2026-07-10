"""Collecting Event form — shared widget across Digitize and Records.

Renders the full collecting-event field block (coordinates + map, location with
reverse-geocode + boundary warnings, date, ecology, recordedBy, verbatim) into
the caller's NiceGUI context. The widget owns the fields, the map picker, the
geocoding, the cascade-wipe, and the read-only toggle; the calling tab owns the
chrome around it — the "Collecting Event" card, the event-search select, the
reuse / detach banner — and the save (create vs update).

This is the single source for the event form (previously hand-built inline in
main.py and again in records_tab.py). Field order, grouping, and any future
tweaks happen here, once.

Boundary (see build_collecting_event_form):
  params  — session_factory; default_recby_fn (push-pin default); on_field_edit
            (dirty-tracking callback, fired on every user edit, suppressed while
            load()/geocode populate fields).
  handle  — collect_fields(); commit(session) → {recorded_by_id, habitat_id,
            sampling_protocol_id}; load(snapshot);
            reset(); set_readonly(bool); plus recby helpers for the tab.

Reuse note: the coordinate-paste JS interceptor is scoped to a per-instance CSS
class (``_coord-lat-<uid>``) so two instances on one page (Digitize + Records)
do not cross-wire. The map picker already uses a per-instance uid.
"""
from __future__ import annotations

import asyncio
import math
import re
import time

import httpx
from nicegui import ui

from app.config import get_config
from app.services.vocabularies import (
    administrative_region_vocab, country_vocab, county_vocab, habitat_vocab,
    island_vocab, sampling_protocol_vocab, state_province_vocab,
)
from app.ui.map_picker import build_map_picker
from app.ui.person_field import build_person_field
from app.ui.vocab_field import build_vocab_field
from app.ui.date_input import attach_date_validation


# ---------------------------------------------------------------------------
# Locality ranking + coordinate-paste parsing (moved from main.py — used only
# by the geocoding logic below).
# ---------------------------------------------------------------------------

_LOCALITY_KV: dict[tuple[str, str], int] = {
    ("natural",  "peak"):           5,
    ("natural",  "spring"):         4,
    ("natural",  "water"):          4,
    ("natural",  "wood"):           4,
    ("natural",  "heath"):          4,
    ("natural",  "wetland"):        4,
    ("natural",  "moor"):           4,
    ("natural",  "scrub"):          3,
    ("natural",  "grassland"):      3,
    ("natural",  "cliff"):          3,
    ("natural",  "sand"):           3,
    ("leisure",  "nature_reserve"): 5,
    ("leisure",  "park"):           3,
    ("boundary", "protected_area"): 4,
    ("landuse",  "forest"):         3,
    ("landuse",  "wood"):           3,
    ("landuse",  "meadow"):         2,
    ("place",    "island"):         2,
    ("place",    "islet"):          2,
    ("place",    "region"):         1,
    ("place",    "hamlet"):         1,
    ("place",    "suburb"):         1,
    ("place",    "village"):        1,
}


def _pick_locality(props_list: list[dict]) -> str:
    """Return the most meaningful collecting locality name from Photon feature properties."""
    best, best_pri = "", -1
    for p in props_list:
        pri = _LOCALITY_KV.get((p.get("osm_key", ""), p.get("osm_value", "")), -1)
        if pri > best_pri and p.get("name"):
            best_pri, best = pri, p["name"]
    return best


# --- Administrative hierarchy from Overpass is_in -------------------------------
# The state is the relation carrying an ISO3166-2 tag, NOT a fixed admin_level:
# DE-BY sits at L4, GR-J at L5, CN-YN at L4. The country carries ISO3166-1, which
# also yields dwc:countryCode from the data (never a hand-rolled dict).
#
# Below the first-order subdivision there is no ISO tag, so these two levels are a
# HEURISTIC, verified across DE/GR/KZ/CH/FR/CN only — see CLAUDE.md "Open (do not
# guess)". They are left empty rather than guessed when the level is absent.
_COUNTY_LEVEL = 6
_MUNI_LEVELS = (7, 8)      # prefer L7 (GR Δήμος) over L8 (its municipal unit)
_REGION_LEVEL = 5          # Regierungsbezirk / prefecture tier — only if not the state


def _admin_name(row: dict) -> str:
    """name:en where OSM has it (country/state everywhere tested), else the local name."""
    return row.get("en") or row.get("nm") or ""


def _admin_level(row: dict) -> int | None:
    try:
        return int(row.get("lvl") or "")
    except ValueError:
        return None


def _resolve_hierarchy(adm_rows: list[dict]) -> dict:
    """Map converted Overpass admin relations onto the collecting-event geography fields."""
    country = next((r for r in adm_rows if r.get("iso1")), None) \
        or next((r for r in adm_rows if _admin_level(r) == 2), None)
    state = next((r for r in adm_rows if r.get("iso2")), None)
    state_lvl = _admin_level(state) if state else None

    def at(level: int) -> dict | None:
        if level == state_lvl:
            return None                      # the state itself is never also a sub-tier
        return next((r for r in adm_rows if _admin_level(r) == level), None)

    region = at(_REGION_LEVEL)
    county = at(_COUNTY_LEVEL)
    muni = next((m for m in (at(lvl) for lvl in _MUNI_LEVELS) if m), None)
    return {
        "country":      _admin_name(country) if country else "",
        "country_code": (country.get("iso1", "") if country else "").upper(),
        "state":        _admin_name(state) if state else "",
        # The ISO 3166-2 code of the state — the very tag that identified it above. Carried
        # through (not discarded) and stored on the state_province vocab row: a label has no
        # room for "Baden-Württemberg" but "DE-BW" fits. Migration 0055.
        "state_code":   (state.get("iso2", "") if state else "").upper(),
        "region":       _admin_name(region) if region else "",
        "county":       _admin_name(county) if county else "",
        "municipality": _admin_name(muni) if muni else "",
    }


# Cap on nearby point-feature locality candidates (enclosing areas are always shown);
# keeps the picker a useful shortlist in feature-dense areas, not an exhaustive dump.
_MAX_LOCALITY_POINTS = 10


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (for ranking nearby locality candidates)."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# Overpass throttles (429) and sheds load (504) on the public instance often enough that a
# single transient failure must not cost the user the whole lookup — measured: an is_in for
# 26.015/101.883 returned 504 once, then answered on the retry. Only these statuses and
# transport errors are retried; a 400 (bad query) is a bug in us and fails immediately.
#
# There is deliberately NO mirror failover. Measured 2026-07-10 on the Augsburg point:
# overpass.kumi.systems and overpass.private.coffee both ReadTimeout at 35 s, overpass.osm.jp
# refuses the connection, and overpass.osm.ch answers **200 OK with zero admin rows** because
# it carries only regional (Swiss) data. Failing over to it would not raise — it would report
# "this point lies in no administrative area" and silently blank the hierarchy, which is the
# exact failure this project forbids (CLAUDE.md §2). One endpoint, honest errors.
#
# `overpass-api.de` itself load-balances between two backends (the status page announces
# `gall.` or `lambert.openstreetmap.de`), which is why the same query costs 1.2 s or 24 s.
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_STATUS_URL = "https://overpass-api.de/api/status"
_OVERPASS_RETRY_STATUS = (429, 502, 503, 504)
_OVERPASS_BACKOFF_S = (1.0, 3.0)          # attempt 1 → 1 s → attempt 2 → 3 s → attempt 3
_OVERPASS_ATTEMPT_TIMEOUT_S = 25.0        # matches the [timeout:25] we ask the server for
_OVERPASS_DEADLINE_S = 40.0               # give up rather than retry into a 90 s wait
_UA = {"User-Agent": "EntomologicalCollection/1.0"}


async def _overpass_post(query: str, *, timeout: float = _OVERPASS_ATTEMPT_TIMEOUT_S,
                         deadline: float = _OVERPASS_DEADLINE_S) -> tuple[list[dict] | None, str]:
    """POST an Overpass QL query.

    Returns ``(elements, "")`` on success, or ``(None, reason)`` where *reason* is a short
    human-readable cause ("timed out", "HTTP 429", …) for the message shown to the user —
    "Overpass unavailable" alone tells them nothing they can act on.
    """
    started = time.monotonic()
    reason = "no response"
    for attempt in range(len(_OVERPASS_BACKOFF_S) + 1):
        # The deadline is a hard cap on the whole call, not just on the sleeps: a fresh
        # 25 s attempt started at t=26 s would run to 51 s. Shrink it to what is left.
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            return None, f"{reason} (gave up after {deadline:.0f} s)"
        try:
            async with httpx.AsyncClient(timeout=min(timeout, remaining)) as cl:
                r = await cl.post(_OVERPASS_URL, data={"data": query}, headers=_UA)
            if r.status_code in _OVERPASS_RETRY_STATUS:
                reason = f"HTTP {r.status_code}"
                if attempt < len(_OVERPASS_BACKOFF_S):
                    if time.monotonic() - started + _OVERPASS_BACKOFF_S[attempt] < deadline:
                        await asyncio.sleep(_OVERPASS_BACKOFF_S[attempt])
                        continue
                    return None, f"{reason} (gave up after {deadline:.0f} s)"
            r.raise_for_status()
            return r.json().get("elements", []), ""
        except httpx.TimeoutException:
            reason = "timed out"
        except httpx.TransportError as ex:
            reason = f"network error ({type(ex).__name__})"
        except httpx.HTTPStatusError as ex:
            code = ex.response.status_code
            if code not in _OVERPASS_RETRY_STATUS:
                # A 4xx is our bug (a malformed query), not a busy server: fail fast.
                return None, f"HTTP {code}"
            reason = f"HTTP {code}"
        except Exception as ex:                       # malformed JSON, etc.
            return None, f"{type(ex).__name__}"
        if attempt >= len(_OVERPASS_BACKOFF_S):
            break
        if time.monotonic() - started + _OVERPASS_BACKOFF_S[attempt] >= deadline:
            return None, f"{reason} (gave up after {deadline:.0f} s)"
        await asyncio.sleep(_OVERPASS_BACKOFF_S[attempt])
    return None, reason


async def _overpass_status() -> str | None:
    """Ask Overpass why it is unhappy: the slot budget for this IP.

    `/api/status` is plain text and reports the per-IP rate limit and free slots, e.g.

        Rate limit: 2
        2 slots available now.
        Slot available after: 2026-07-10T11:42:29Z, in 14 seconds.

    Returns a short phrase for the notification, or None if the status page is unreachable
    (in which case the caller must *suggest* a rate limit rather than assert one).
    """
    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.get(_OVERPASS_STATUS_URL, headers=_UA)
        r.raise_for_status()
        text = r.text
    except Exception:
        return None

    limit = re.search(r"Rate limit:\s*(\d+)", text)
    free = re.search(r"(\d+)\s+slots? available now", text)
    wait = re.search(r"in\s+(\d+)\s+seconds", text)
    if free and limit:
        if int(free.group(1)) > 0:
            return (f"{free.group(1)} of {limit.group(1)} query slots are free, so this looks "
                    "like server load rather than a rate limit")
        return f"0 of {limit.group(1)} query slots are free for this computer"
    if wait:
        return (f"all query slots for this computer are in use — "
                f"the next frees up in about {wait.group(1)} s")
    if limit:
        return f"the server allows {limit.group(1)} concurrent queries from this computer"
    return None


async def _overpass_failure_message(reason: str) -> str:
    """Compose an actionable 'why did Overpass not answer' sentence."""
    status = await _overpass_status()
    if status is None:
        return (f"Overpass did not answer ({reason}); its status page is unreachable too. "
                "The per-computer query-rate limit may have been exceeded — wait a minute "
                "and try again.")
    return f"Overpass did not answer ({reason}): {status}."


class _VocabInput:
    """Adapts a ``build_vocab_field`` handle to the ``ui.input`` interface this form uses.

    The geography levels are FK-backed controlled vocabularies and must behave like one
    (existing entries, "✚ add" for a new name, keyboard nav, live options) — but the form
    reads and writes them as ``.value`` in a dozen places, and the field registry drives
    collect / load / reset / readonly generically. This keeps that contract.

    ``.code`` exposes the ISO code of the *picked row*: two rows can share a name (Limburg
    BE-VLI / NL-LI), so the name alone does not identify the pick.
    """

    __slots__ = ("_h",)

    def __init__(self, handle: dict):
        self._h = handle

    @property
    def value(self) -> str:
        return self._h["get_value"]() or ""

    @value.setter
    def value(self, val) -> None:
        self._h["set_value"]((str(val) if val is not None else "") or None)

    @property
    def code(self) -> str | None:
        return self._h["get_code"]()

    def set_value(self, name: str | None, code: str | None = None) -> None:
        self._h["set_value"](name or None, code)

    def props(self, add: str | None = None, *, remove: str | None = None):
        """Only 'readonly' is ever toggled on these fields by _set_readonly()."""
        if add and "readonly" in add:
            self._h["set_readonly"](True)
        if remove and "readonly" in remove:
            self._h["set_readonly"](False)
        return self


def build_collecting_event_form(
    session_factory,
    *,
    default_recby_fn=None,
    on_field_edit=None,
    footer_slot=None,
) -> dict:
    """Render the collecting-event field block into the current context.

    Returns a handle dict:
      card_fields_present : (no card — caller wraps); see module docstring.
      collect_fields()    : {snake_case field: value} for the save path.
      commit(session)     : resolve the FK-backed fields → a dict of ids
                            {recorded_by_id, habitat_id, sampling_protocol_id}
                            (each may be None); spread into create/update.
      load(snapshot)      : populate every field from a dict (suppresses on_field_edit).
      reset()             : blank every field.
      set_readonly(bool)  : lock/unlock all fields + map (reused-event / view-only).
      recby_refresh()     : refresh the recordedBy options from the DB.
      recby_get()         : current recordedBy value.
    """
    _st = {"populating": False, "editable": True}

    def _fire_edit():
        if not _st["populating"] and on_field_edit:
            on_field_edit()

    # ── cascade-wipe: clearing a coarser admin level blanks the finer ones ──
    def _wipe_from(level: str) -> None:
        # region_in (admin. region) sits between state and county, so a country/state
        # change blanks it too (resolved at runtime — region_in is defined below).
        if level == "country":
            state_in.value = region_in.value = county_in.value = muni_in.value = locality_in.value = ""
            for _b in (_state_warn, _county_warn, _muni_warn, _locality_warn):
                _b.classes(add="hidden")
        elif level == "state":
            region_in.value = county_in.value = muni_in.value = locality_in.value = ""
            for _b in (_county_warn, _muni_warn, _locality_warn):
                _b.classes(add="hidden")
        elif level == "county":
            muni_in.value = locality_in.value = ""
            for _b in (_muni_warn, _locality_warn):
                _b.classes(add="hidden")
        elif level == "muni":
            locality_in.value = ""
            _locality_warn.classes(add="hidden")

    def _on_country_change(_=None):
        if not _st["populating"]:
            _wipe_from("country")
        _fire_edit()

    def _on_state_change(_=None):
        # The picked row's ISO code lives on the field itself: choosing "Bavaria (DE-BY)"
        # from the dropdown carries DE-BY, while a free-typed name carries none. Nothing
        # to clear here.
        if not _st["populating"]:
            _wipe_from("state")
        _fire_edit()

    def _on_county_change(_=None):
        if not _st["populating"]:
            _wipe_from("county")
        _fire_edit()

    def _on_muni_change(_=None):
        if not _st["populating"]:
            _wipe_from("muni")
        _fire_edit()

    def _geocode_input(label, on_change=None, placeholder="", vocab=None):
        """Input + hidden inline warning menu + ok icon + pending spinner.

        Returns (input, btn, tip, items_col, ok_icon, spinner). The spinner marks *this*
        field as awaiting its own source, so a slow lookup shows where it is still working
        instead of the whole form sitting inert.

        With *vocab*, the field is the controlled-vocabulary dropdown (the same UX as the
        person field) rather than a bare text input — these levels are FK-backed vocabs and
        should look like it. The warning menu / ✓ / spinner stay as siblings in the row.
        """
        with ui.row().classes("col-span-1 items-center gap-0 w-full"):
            if vocab is not None:
                inp = _VocabInput(build_vocab_field(
                    session_factory, vocab, label,
                    on_change=on_change, classes="flex-1 min-w-0"))
            else:
                inp = ui.input(label, on_change=on_change,
                               placeholder=placeholder).classes("flex-1 min-w-0")
            with (
                ui.button(icon="warning_amber")
                .props("flat dense round size=xs color=orange")
                .classes("hidden")
            ) as btn:
                tip = ui.tooltip("")
                with ui.menu():
                    ui.label("Centre-point value — click to choose:") \
                        .classes("text-xs text-grey-7 q-px-sm q-pt-xs")
                    ui.separator()
                    items_col = ui.column().classes("q-pa-xs")
            ok_icon = (
                ui.icon("check_circle", size="xs")
                .props("color=positive")
                .classes("hidden")
            )
            spinner = ui.spinner(size="xs").props("color=primary").classes("hidden")
        return inp, btn, tip, items_col, ok_icon, spinner

    def _spin(spinners, on: bool) -> None:
        for sp in spinners:
            sp.classes(remove="hidden") if on else sp.classes(add="hidden")

    def _on_lat_change(e):
        # Fallback when the JS paste interceptor isn't installed yet.
        val = str(e.value) if e.value is not None else ""
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", val.strip())
        if len(nums) >= 2:
            try:
                la, lo = float(nums[0]), float(nums[1])
            except ValueError:
                la = lo = None
            if la is not None and -90 <= la <= 90 and -180 <= lo <= 180:
                lat_in.value = nums[0]
                lon_in.value = nums[1]
        _fire_edit()

    # ── Coordinates (coordinates-first) ─────────────────────────────────────
    ui.label("Coordinates").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-2")
    with ui.grid(columns=5).classes("w-full gap-3 mt-1"):
        lat_in      = ui.input("latitude",      on_change=_on_lat_change)
        lon_in      = ui.input("longitude",     on_change=_fire_edit)
        uncert_in   = ui.input("uncertainty m", on_change=_fire_edit)
        elev_min_in = ui.input("elev min m",    on_change=_fire_edit).classes("col-span-1")
        elev_max_in = ui.input("elev max m",    on_change=_fire_edit).classes("col-span-1")

    # Per-instance JS hook classes so two instances on one page don't collide.
    _coord_sink = ui.element('span').style('display:none')
    _uid = f"ce{_coord_sink.id}"
    lat_in.classes(f"col-span-1 _coord-lat-{_uid}")
    lon_in.classes(f"col-span-1 _coord-lon-{_uid}")
    uncert_in.classes("col-span-1")

    def _on_coord_paste_event(e):
        try:
            d = e.args  # {lat, lon}
            lat_in.value = str(d["lat"])
            lon_in.value = str(d["lon"])
            _fire_edit()
        except (KeyError, TypeError):
            pass

    _coord_sink.on('coord-paste', _on_coord_paste_event)
    _csink_id = _coord_sink.id
    _clid = list(_coord_sink._event_listeners.keys())[-1]

    async def _inject_coord_paste_js():
        # Fire-and-forget: the script self-installs (retries via setTimeout) and
        # emits paste events back — we don't need its return value, so a slow client
        # ack must not raise (e.g. during a busy initial page build). Give it headroom
        # and swallow only the ack timeout; the JS is delivered regardless.
        try:
            await ui.run_javascript(f"""
        (function install() {{
            var latEl = document.querySelector('._coord-lat-{_uid} input');
            var lonEl = document.querySelector('._coord-lon-{_uid} input');
            if (!latEl) {{ setTimeout(install, 300); return; }}
            latEl.addEventListener('paste', function(ev) {{
                var text = (ev.clipboardData || window.clipboardData).getData('text');
                var nums = text.match(/[-+]?\\d+(?:\\.\\d+)?/g);
                if (!nums || nums.length < 2) return;
                var lat = parseFloat(nums[0]), lon = parseFloat(nums[1]);
                if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
                ev.preventDefault();
                var nset = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                nset.call(latEl, String(lat));
                if (lonEl) nset.call(lonEl, String(lon));
                window.socket.emit('event', {{
                    id: {_csink_id},
                    client_id: window.clientId,
                    listener_id: '{_clid}',
                    args: [JSON.stringify({{lat: String(lat), lon: String(lon)}})]
                }});
            }});
        }})();
        """, timeout=5.0)
        except TimeoutError:
            pass   # JS still ran on the client; only the round-trip ack was slow

    ui.timer(0.3, _inject_coord_paste_js, once=True)

    async def _reverse_geocode(lat: float, lon: float) -> dict | None:
        """Fill the geography fields from Overpass is_in (containment) + Photon (nearby names).

        The two services answer different questions and must not be swapped (CLAUDE.md
        "Geocoding: containment vs proximity"):

        * Overpass ``is_in`` returns the polygons that CONTAIN the point → the whole
          administrative hierarchy. The state is the relation tagged ``ISO3166-2``, not a
          fixed admin_level.
        * Photon ``/reverse`` is a PROXIMITY search: its country/state/county/city describe
          the NEAREST FEATURE, not the query point, so they are read only as a degraded
          fallback when Overpass fails. Its features are the locality candidates, filtered
          to those actually inside the uncertainty circle.

        Returns ``{"photon": props|None}`` — ``None`` only when the lookup truly failed.
        An empty Photon response is NOT a failure: it means "no named feature nearby"
        (open sea, steppe, 26.015/101.883), which leaves `locality` blank, not the form.
        """
        for _b in (_cntry_warn, _state_warn, _county_warn, _muni_warn):
            _b.classes(add="hidden")
        _locality_warn.classes(add="hidden")

        # Blank every geocode-owned field before starting. Sources now land one at a time, so
        # without this the previous point's values sit in the form while the new lookup runs —
        # and a source that finds nothing (Photon at 26.015/101.883 returns no feature) would
        # leave them there for good. A locality silently carried over from another specimen's
        # coordinates is exactly the "silent wrong value" this project refuses (CLAUDE.md §2).
        # Empty field + spinner = "being looked up"; empty field after = "nothing there".
        _st["populating"] = True
        for _f in (country_in, state_in, region_in, county_in, muni_in,
                   locality_in, island_in):
            _f.value = ""          # clears the vocab fields' ISO codes with them
        _st["populating"] = False
        _fire_edit()

        # The locality circle scales with the coordinate uncertainty: a precise point → only
        # the very nearest features; a vague one → a wider net. Floor keeps the nearest named
        # feature reachable; cap keeps it sane when uncertainty is huge.
        try:
            _unc = float(uncert_in.value or 0)
        except (TypeError, ValueError):
            _unc = 0.0
        circle_m = int(max(300, min(_unc or 1000, 3000)))

        async def _photon() -> list[dict]:
            """Nearby named features with a computed distance (Photon carries no distance)."""
            async with httpx.AsyncClient(timeout=10) as cl:
                r = await cl.get(
                    "https://photon.komoot.io/reverse",
                    # Photon's radius ceiling is ~1.5 km — 3 km and 10 km return the same
                    # results — so the circle filter below, not this hint, is what binds.
                    params={"lat": lat, "lon": lon, "lang": "en", "limit": 15,
                            "radius": circle_m / 1000},
                    headers={"User-Agent": "EntomologicalCollection/1.0"},
                )
                r.raise_for_status()
                out = []
                for f in r.json().get("features", []):
                    props = f["properties"]
                    geom = (f.get("geometry") or {}).get("coordinates")
                    props["_dist"] = (_haversine_m(lat, lon, geom[1], geom[0])
                                      if geom else float("inf"))
                    out.append(props)
                return out

        async def _overpass() -> dict | None:
            """Enclosing admin hierarchy + named areas/islands, in ONE request. None on failure.

            Deliberately one query, not two parallel ones. The public Overpass instance grants
            an IP only a couple of concurrent slots, so a second request queues behind the
            first rather than running beside it. Measured (6 runs, alternating, 20 s apart):

                one combined query   median  3.7 s, max 13.8 s, 6/6 succeeded
                two parallel queries median 29.2 s, max 37.2 s, 0/6 succeeded

            Splitting serialised the work and added retry backoff on top. The `is_in` is
            evaluated once and both blocks pivot off it, so bundling is also cheaper server-side.
            """
            q = (
                "[out:json][timeout:25];"
                f"is_in({lat},{lon})->.a;"
                # `is_in` yields AREAS — relation(pivot.a) is required; rel.a[...] silently
                # matches nothing. `convert` flattens the tags we need (absent → "").
                "relation(pivot.a)[boundary=administrative][name];"
                'convert adm ::id=id(), lvl=t["admin_level"], nm=t["name"], en=t["name:en"],'
                ' iso1=t["ISO3166-1"], iso2=t["ISO3166-2"];'
                "out;"
                "("
                "  way(pivot.a)[name][boundary=protected_area];"
                "  way(pivot.a)[name][leisure=nature_reserve];"
                '  way(pivot.a)[name][landuse~"^(forest|wood)$"];'
                '  way(pivot.a)[name][place~"^(island|islet)$"];'
                '  relation(pivot.a)[name][boundary~"^(protected_area|national_park)$"];'
                "  relation(pivot.a)[name][leisure=nature_reserve];"
                '  relation(pivot.a)[name][landuse~"^(forest|wood)$"];'
                '  relation(pivot.a)[name][place~"^(island|islet)$"];'
                ");"
                "out tags;"
            )
            elements, err = await _overpass_post(q)
            if elements is None:
                _geo["overpass_error"] = err
                return None
            adm_rows, areas, islands, seen = [], [], [], set()
            for el in elements:
                tags = el.get("tags", {})
                if el.get("type") == "adm":          # converted admin relation
                    adm_rows.append(tags)
                    continue
                n = tags.get("name", "")
                if not n or n in seen:
                    continue
                seen.add(n)
                if tags.get("place") in ("island", "islet"):
                    islands.append(n)
                    continue
                kind = (tags.get("leisure") or tags.get("boundary")
                        or tags.get("landuse") or "area")
                areas.append({"name": n, "kind": kind})
            return {"hier": _resolve_hierarchy(adm_rows),
                    "areas": areas, "islands": islands}

        # ── progressive fill ────────────────────────────────────────────────────
        # Photon answers in ~0.15 s, Overpass in ~3.7 s (median). Each writes its own fields
        # the moment it lands rather than both being gathered and applied together, so the
        # locality appears immediately instead of waiting on the containment query.
        _geo: dict = {"best": None, "areas": [], "points": [], "auto": "",
                      "photon_done": False, "areas_done": False, "overpass_error": ""}

        def _fmt_dist(d: float) -> str:
            return f"{round(d)} m" if d < 1000 else f"{d / 1000:.1f} km"

        def _apply_locality() -> None:
            """Recompute locality from whatever has arrived; never clobber the user's typing.

            Order-independent: a Photon feature outranks an enclosing area, whichever landed
            first. Re-running with more data can only improve the value.
            """
            val = ((_geo["best"]["name"] if _geo["best"] else "")
                   or (_geo["areas"][0]["name"] if _geo["areas"] else ""))
            if not val or locality_in.value not in ("", _geo["auto"]):
                return
            _st["populating"] = True
            locality_in.value = val
            _st["populating"] = False
            _geo["auto"] = val
            _fire_edit()

        def _rebuild_picker() -> None:
            """"Also nearby" menu: enclosing areas (no distance) then nearby points."""
            _alts: list[tuple[str, str]] = []
            _seen_locs: set[str] = {locality_in.value}
            for c in _geo["areas"]:
                if c["name"] not in _seen_locs:
                    _seen_locs.add(c["name"])
                    _alts.append((f"{c['name']}  ·  {c['kind']}", c["name"]))
            for c in _geo["points"]:
                if c["name"] not in _seen_locs:
                    _seen_locs.add(c["name"])
                    _alts.append((f"{c['name']}  ·  {c['kind']}  ·  {_fmt_dist(c['dist'])}", c["name"]))
            if not _alts:
                return
            _locality_items.clear()
            with _locality_items:
                for _label, _name in _alts:
                    async def _pick_alt(nm=_name):
                        locality_in.value = nm
                        _geo["auto"] = nm
                        _fire_edit()
                        _locality_warn.classes(add="hidden")
                    ui.menu_item(_label, on_click=_pick_alt)
            _locality_tip.text = "Also nearby: " + ", ".join(lbl for lbl, _ in _alts[:8])
            _locality_tip.update()
            _locality_warn.classes(remove="hidden")

        def _locality_settled() -> None:
            """Stop the locality spinner only once both of its sources have reported."""
            if _geo["photon_done"] and _geo["areas_done"]:
                _spin([_locality_sp], False)

        async def _fill_photon() -> list[dict]:
            try:
                props = await _photon()
            except Exception:
                props = []
            # Only features genuinely INSIDE the uncertainty circle. Photon happily returns a
            # road 2.8 km away when nothing is near (26.015/101.883) — not a collecting
            # locality, and an empty locality is the correct answer there.
            near = sorted((p for p in props if p["_dist"] <= circle_m),
                          key=lambda p: p["_dist"])[:_MAX_LOCALITY_POINTS]
            ranked = sorted(
                ((_LOCALITY_KV.get((p.get("osm_key", ""), p.get("osm_value", "")), -1),
                  -p["_dist"], p) for p in near if p.get("name")),
                key=lambda t: (t[0], t[1]), reverse=True)
            _geo["best"] = next((p for pri, _, p in ranked if pri >= 0), None)
            _geo["points"] = [
                {"name": p["name"], "kind": p.get("osm_value") or p.get("osm_key") or "place",
                 "dist": p["_dist"]} for p in near if p.get("name")]
            _geo["photon_done"] = True
            _apply_locality()
            _rebuild_picker()
            _locality_settled()
            return props

        async def _fill_overpass() -> dict | None:
            """One wave: the containment hierarchy, the island, and the enclosing areas."""
            res = await _overpass()
            _geo["areas"] = res["areas"] if res else []
            _geo["areas_done"] = True
            if res is not None:
                hier = res["hier"]
                _st["populating"] = True
                # The ISO codes ride along with the names: the geocoder identified *these*
                # rows (Bavaria = DE-BY), so the save must resolve to them and not to a
                # same-named uncoded row.
                country_in.set_value(hier["country"], hier["country_code"])
                state_in.set_value(hier["state"], hier["state_code"])
                region_in.value  = hier["region"]
                county_in.value  = hier["county"]
                muni_in.value    = hier["municipality"]
                # Written unconditionally: no enclosing island means the island field is
                # empty for *this* point, not left showing the previous one.
                island_in.value  = res["islands"][0] if res["islands"] else ""
                _st["populating"] = False
                _fire_edit()
            _spin(_admin_spinners + [_island_sp], False)
            _apply_locality()
            _rebuild_picker()
            _locality_settled()
            return res["hier"] if res else None

        _spin(_admin_spinners + _locality_spinners, True)
        try:
            all_props, hier = await asyncio.gather(_fill_photon(), _fill_overpass())
        finally:
            _spin(_admin_spinners + _locality_spinners, False)

        if hier is None and not all_props:
            # Both sources silent. Say which one failed and why — "geocoding failed" alone
            # leaves the user with nothing to act on (wait? fix coordinates? check the net?).
            msg = await _overpass_failure_message(_geo["overpass_error"] or "no response")
            ui.notify(f"Reverse geocoding failed. {msg} Photon returned nothing either.",
                      type="negative", multi_line=True, timeout=12000,
                      classes="text-left", close_button="OK")
            return None

        if hier is None:
            # Degraded fallback: Photon's hierarchy describes its nearest feature, so it can
            # name a neighbouring municipality — and in Greece a different admin tier entirely.
            # Loud, and it says *why*, because the values may be wrong.
            p0 = all_props[0]
            msg = await _overpass_failure_message(_geo["overpass_error"] or "no response")
            ui.notify(f"{msg} The administrative fields below were taken from the nearest "
                      "named feature instead of the areas containing the point, so they may "
                      "be wrong (and admin. region is left empty). Please check them.",
                      type="warning", multi_line=True, timeout=15000,
                      classes="text-left", close_button="OK")
            _st["populating"] = True
            country_in.set_value(p0.get("country", ""),
                                 (p0.get("countrycode", "") or "").upper())
            # No state code: Photon names the nearest feature's tier, which in Greece is the
            # Decentralized Administration, not the ISO region. Claiming an ISO code for it
            # would be a lie; the field stays uncoded.
            state_in.set_value(p0.get("state", "") or None, None)
            region_in.value  = ""
            county_in.value  = p0.get("county", "")
            muni_in.value    = p0.get("city") or p0.get("locality") or ""
            _st["populating"] = False
            _fire_edit()

        # The boundary check compares Photon perimeter samples; with no Photon centre props
        # there is nothing to compare against, so the caller skips it rather than guess.
        return {"photon": all_props[0] if all_props else None}

    async def _check_boundary_crossing(
        lat: float, lon: float, radius_m: float,
        ok_icons: list | None = None,
    ) -> bool:
        """Warn when the uncertainty circle crosses admin boundaries (samples 4 cardinal points)."""
        def _props_to_snap(props_list: list[dict]) -> dict:
            p = props_list[0] if props_list else {}
            return {
                "country":  p.get("country", ""),
                "code":     (p.get("countrycode", "") or "").upper(),
                "state":    p.get("state", ""),
                "county":   p.get("county", ""),
                "muni":     p.get("city") or p.get("locality") or "",
                "locality": _pick_locality(props_list),
            }

        # The centre snapshot is what the form actually shows (Overpass containment), NOT
        # photon_props — otherwise "keep the centre value" would overwrite a correct field
        # with Photon's nearest-feature guess. The perimeter samples below are still Photon,
        # so a crossing warning is advisory only; see CLAUDE.md "Boundary-crossing check".
        centre = {
            "country":  country_in.value or "",
            "code":     country_in.code or "",
            "state":    state_in.value or "",
            "county":   county_in.value or "",
            "muni":     muni_in.value or "",
            "locality": locality_in.value or "",
        }
        snapshots: list[dict] = [centre]

        perimeter_pts = []
        for a in (0, 90, 180, 270):
            la = lat + (radius_m / 111_320) * math.cos(math.radians(a))
            lo = lon + (radius_m / (111_320 * math.cos(math.radians(lat)))) * math.sin(math.radians(a))
            perimeter_pts.append((la, lo))

        async def _photon_at(cl: httpx.AsyncClient, la: float, lo: float) -> list[dict] | None:
            for attempt in range(3):
                try:
                    if attempt:
                        await asyncio.sleep(0.5 * attempt)
                    rp = await cl.get(
                        "https://photon.komoot.io/reverse",
                        params={"lat": la, "lon": lo, "lang": "en", "limit": 15},
                        headers={"User-Agent": "EntomologicalCollection/1.0"},
                    )
                    if rp.status_code in (429, 503) and attempt < 2:
                        continue
                    rp.raise_for_status()
                    feats = rp.json().get("features", [])
                    return [f["properties"] for f in feats] or None
                except Exception:
                    return None
            return None

        async with httpx.AsyncClient(timeout=10) as cl:
            results = await asyncio.gather(*[_photon_at(cl, la, lo) for la, lo in perimeter_pts])

        for p in results:
            if not p:
                continue
            snap = _props_to_snap(p)
            if snap != centre and snap not in snapshots:
                snapshots.append(snap)

        _any_warn: list[bool] = []
        _ok_shown: list = []

        def _show_warn(btn, tip, items_col, field_key, on_pick, ok_icon=None, label_fn=None):
            centre_val = centre[field_key]
            # Empty is never an offerable value. A sample that names nothing at this tier
            # (no municipality inside a kreisfreie Stadt; no named feature near the point)
            # carries no information, and "set this field to nothing" is not a choice worth
            # a menu row — it rendered as a bare " (centre)" or "(no named feature) (centre)".
            seen: dict[str, dict] = {}
            for snap in snapshots:
                val = snap[field_key]
                if val and val not in seen:
                    seen[val] = snap
            # Warn when some *named* alternative differs from what the field holds. This still
            # fires when the field is empty and a perimeter sample names something (the circle
            # reaches into a municipality the centre is not in) — the case that matters.
            alts = [v for v in seen if v != centre_val]
            if not alts:
                if ok_icon is not None:
                    ok_icon.classes(remove="hidden", add="lookup-ok-fade")
                    _ok_shown.append(ok_icon)
                return
            items_col.clear()
            with items_col:
                for val, snap in seen.items():
                    display = label_fn(val, snap) if label_fn else val
                    marker  = " (centre)" if val == centre_val else ""

                    async def _cb(s=snap):
                        await on_pick(s)

                    ui.menu_item(display + marker, on_click=_cb)
            tip_alts = [label_fn(v, seen[v]) if label_fn else v for v in alts]
            tip.text = "Circle also covers: " + ", ".join(tip_alts)
            tip.update()
            btn.classes(remove="hidden")
            btn.update()
            _any_warn.append(True)

        async def _apply_snap(snap: dict) -> None:
            _st["populating"] = True
            country_in.set_value(snap["country"] or None, snap["code"] or None)
            # Perimeter samples are Photon's; they carry no ISO 3166-2 code, so picking one
            # sets the state name without claiming a code for it.
            state_in.set_value(snap["state"] or None, None)
            county_in.value   = snap["county"]
            muni_in.value     = snap["muni"]
            locality_in.value = snap["locality"]
            _st["populating"] = False
            for _b in (_cntry_warn, _state_warn, _county_warn, _muni_warn, _locality_warn):
                _b.classes(add="hidden")
            _fire_edit()

        icons = ok_icons or [None] * 5
        _show_warn(_cntry_warn,    _cntry_tip,    _cntry_items,    "country",  _apply_snap, ok_icon=icons[0])
        _show_warn(_state_warn,    _state_tip,    _state_items,    "state",    _apply_snap, ok_icon=icons[1])
        _show_warn(_county_warn,   _county_tip,   _county_items,   "county",   _apply_snap, ok_icon=icons[2])
        _show_warn(_muni_warn,     _muni_tip,     _muni_items,     "muni",     _apply_snap, ok_icon=icons[3])
        # No label_fn: an unnamed sample can no longer reach the menu, so the old
        # "(no named feature)" placeholder is unreachable.
        _show_warn(_locality_warn, _locality_tip, _locality_items, "locality", _apply_snap,
                   ok_icon=icons[4])

        if _ok_shown:
            _fading = list(_ok_shown)
            ui.timer(1.4, lambda: [ok.classes(add="hidden", remove="lookup-ok-fade") for ok in _fading], once=True)
        return bool(_any_warn)

    def _on_map_change(lat: float, lon: float, unc):
        lat_in.value    = str(round(lat, 7))
        lon_in.value    = str(round(lon, 7))
        uncert_in.value = str(int(round(unc))) if unc else ""
        _fire_edit()

    _map = build_map_picker(_on_map_change, default_layer=get_config().map_default_layer)

    with ui.row().classes("items-center gap-2 mt-2"):
        def _open_map():
            ro = not _st["editable"]
            try:
                lat = float(lat_in.value)
                lon = float(lon_in.value)
            except (TypeError, ValueError):
                _map["open"]()
                _map["set_readonly"](ro)
                return
            unc = None
            try:
                unc = float(uncert_in.value) if uncert_in.value else None
            except ValueError:
                pass
            _map["fly_to"](lat, lon, unc)
            _map["set_readonly"](ro)

        (
            ui.button("Map", icon="map", on_click=_open_map)
            .props("flat dense size=sm")
            .tooltip("Open map to pick coordinates")
        )

        def _clear_map_coords():
            _map["clear"]()
            lat_in.value = lon_in.value = uncert_in.value = ""
            _fire_edit()

        _clear_coords_btn = (
            ui.button("Clear", icon="clear", on_click=_clear_map_coords)
            .props("flat dense size=sm")
            .tooltip("Remove marker and clear coordinate fields")
        )

        async def _fill_from_coords():
            try:
                lat = float(lat_in.value)
                lon = float(lon_in.value)
            except (TypeError, ValueError):
                ui.notify("Enter valid coordinates first.", type="warning")
                return
            unc: float | None = None
            try:
                unc = float(uncert_in.value) if uncert_in.value else None
            except ValueError:
                pass
            # The lookup can take 25 s (measured: the Overpass hierarchy query varies from
            # 2 s to 24 s), so say so plainly. `loading` on a flat button is easy to miss;
            # the label change + disabled state + the per-field spinners are not.
            _lookup_btn.props("loading=true disable")
            _lookup_btn.set_text("Detecting…")
            _lookup_status.set_text("Looking up nearby features and the enclosing "
                                    "administrative areas…")
            _lookup_status.classes(remove="hidden")
            for _ok in _geocode_ok_icons:
                _ok.classes(add="hidden", remove="lookup-ok-fade")
            _locality_warn.classes(add="hidden")
            try:
                res = await _reverse_geocode(lat, lon)
            finally:
                _lookup_btn.props(remove="loading disable")
                _lookup_btn.set_text("Detect Locations from Coordinates")
                _lookup_status.classes(add="hidden")
            if res is None:
                return
            ui.notify("Location fields filled from coordinates.", type="positive")
            p = res["photon"]
            # No Photon features (open sea, steppe) → no perimeter hierarchies to diff.
            if unc and unc > 0 and p is not None:
                _lookup_status.set_text("Checking whether the uncertainty circle crosses "
                                        "an administrative boundary…")
                _lookup_status.classes(remove="hidden")
                try:
                    await _check_boundary_crossing(lat, lon, unc, ok_icons=_geocode_ok_icons)
                finally:
                    _lookup_status.classes(add="hidden")
            else:
                for _ok in _geocode_ok_icons:
                    _ok.classes(remove="hidden", add="lookup-ok-fade")
                ui.timer(1.4, lambda: [
                    _ok.classes(add="hidden", remove="lookup-ok-fade") for _ok in _geocode_ok_icons
                ], once=True)

        _lookup_btn = (
            ui.button("Detect Locations from Coordinates", icon="auto_fix_high", on_click=_fill_from_coords)
            .props("flat dense size=sm")
            .tooltip("Fill the administrative hierarchy (Overpass) and locality (Photon) "
                     "from the coordinates")
        )
        _lookup_status = (
            ui.label("").classes("hidden text-xs text-grey-6 italic")
        )

    # ── Location ────────────────────────────────────────────────────────────
    ui.label("Location").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
    # The five administrative levels are FK-backed controlled vocabularies (migration 0041),
    # so they get the vocab dropdown; municipality / locality / verbatimLocality are
    # deliberately free text and stay plain inputs. There is no countryCode field: the
    # code belongs to the country vocab row (pill in its dropdown) and is derived at
    # export/label time — a second editable copy drifted (migration 0057).
    with ui.grid(columns=4).classes("w-full gap-3 mt-1"):
        country_in, _cntry_warn, _cntry_tip, _cntry_items, _cntry_ok, _cntry_sp = _geocode_input(
            "country", on_change=_on_country_change, vocab=country_vocab)
        state_in, _state_warn, _state_tip, _state_items, _state_ok, _state_sp = _geocode_input(
            "stateProvince", on_change=_on_state_change, vocab=state_province_vocab)
        # administrative region (Regierungsbezirk tier) — a controlled vocab too, but no DwC
        # term. Filled from the enclosing admin_level-5 boundary (Overpass is_in), when that
        # L5 is not itself the ISO state. Resolved name→id in the events service.
        with ui.row().classes("col-span-1 items-center gap-0 w-full"):
            region_in = _VocabInput(build_vocab_field(
                session_factory, administrative_region_vocab, "admin. region",
                on_change=_fire_edit, classes="flex-1 min-w-0"))
            _region_sp = ui.spinner(size="xs").props("color=primary").classes("hidden")
        county_in, _county_warn, _county_tip, _county_items, _county_ok, _county_sp = _geocode_input(
            "county", on_change=_on_county_change, vocab=county_vocab)
        muni_in, _muni_warn, _muni_tip, _muni_items, _muni_ok, _muni_sp = _geocode_input(
            "municipality", on_change=_on_muni_change)
    _geocode_ok_icons = [_cntry_ok, _state_ok, _county_ok, _muni_ok]

    with ui.grid(columns=3).classes("w-full gap-3 mt-3"):
        locality_in, _locality_warn, _locality_tip, _locality_items, _locality_ok, _locality_sp = \
            _geocode_input("locality", on_change=_fire_edit)
        with ui.row().classes("col-span-1 items-center gap-0 w-full"):
            island_in = _VocabInput(build_vocab_field(
                session_factory, island_vocab, "island",
                on_change=_fire_edit, classes="flex-1 min-w-0"))
            _island_sp = ui.spinner(size="xs").props("color=primary").classes("hidden")
        verblocal_in = ui.input("verbatimLocality", on_change=_fire_edit).classes("col-span-1")
    _geocode_ok_icons.append(_locality_ok)

    # Which spinner belongs to which source — the hierarchy fields come from the Overpass
    # admin query, island/locality from the (slower) enclosing-areas query + Photon.
    _admin_spinners    = [_cntry_sp, _state_sp, _region_sp, _county_sp, _muni_sp]
    _locality_spinners = [_locality_sp, _island_sp]

    # ── Date ────────────────────────────────────────────────────────────────
    ui.label("Date").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
    with ui.grid(columns=3).classes("w-full gap-3 mt-1"):
        edate_in = ui.input("eventDate", placeholder="YYYY-MM-DD or YYYY-MM-DD/YYYY-MM-DD",
                            on_change=_fire_edit).classes("col-span-2")
        attach_date_validation(edate_in, allow_interval=True)
        verbdate_in = ui.input("verbatimEventDate", on_change=_fire_edit).classes("col-span-1")

    # ── Ecology ─────────────────────────────────────────────────────────────
    ui.label("Ecology").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
    with ui.grid(columns=2).classes("w-full gap-3 mt-1"):
        # habitat + samplingProtocol are controlled vocabularies (same dropdown as
        # the person/preparation fields). They are NOT in _event_widgets — their
        # values are FK ids resolved at commit (like recordedBy), not text.
        habitat_field  = build_vocab_field(
            session_factory, habitat_vocab, "habitat",
            on_change=_fire_edit, classes="col-span-1")
        protocol_field = build_vocab_field(
            session_factory, sampling_protocol_vocab, "samplingProtocol",
            on_change=_fire_edit, classes="col-span-1")

    # ── Recorded by ─────────────────────────────────────────────────────────
    ui.label("Recorded by").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
    with ui.grid(columns=2).classes("w-full gap-3 mt-1"):
        with ui.row().classes("col-span-1 items-center gap-1"):
            recby_state = build_person_field(
                session_factory, "recordedBy",
                default_fn=default_recby_fn,
                on_change=_fire_edit,
            )
        fieldnum_in = ui.input("fieldNumber", on_change=_fire_edit).classes("col-span-1")

    verblabel_in = ui.input("verbatimLabel", on_change=_fire_edit).classes("w-full mt-4")

    # Footer: the Confidential flag (left) shares one line with the caller's
    # widgets (event media button, right) to save vertical space. A confidential
    # event withholds all its specimens from the DwC export. Local-only flag.
    with ui.row().classes("w-full items-center justify-between mt-2"):
        conf_chk = (
            ui.checkbox("Confidential")
            .props("dense")
            .tooltip("Withhold this event's specimens from public export — a "
                     "confidential event drops all its specimens from the DwC "
                     "export (TaxonWorks). Local-only flag.")
        )
        conf_chk.on_value_change(lambda e: _fire_edit())
        if footer_slot is not None:
            with ui.row().classes("items-center gap-1"):
                footer_slot()

    # ── field registry: single source for collect / load / reset / readonly ──
    _event_widgets = {
        "country":                          country_in,
        "state_province":                   state_in,
        "administrative_region":            region_in,
        "county":                           county_in,
        "municipality":                     muni_in,
        "island":                           island_in,
        "locality":                         locality_in,
        "verbatim_locality":                verblocal_in,
        "event_date":                       edate_in,
        "verbatim_event_date":              verbdate_in,
        "decimal_latitude":                 lat_in,
        "decimal_longitude":                lon_in,
        "coordinate_uncertainty_in_meters": uncert_in,
        "minimum_elevation_in_meters":      elev_min_in,
        "maximum_elevation_in_meters":      elev_max_in,
        "field_number":                     fieldnum_in,
        "verbatim_label":                   verblabel_in,
    }

    def _collect_fields() -> dict:
        # Text fields only; the FK-backed vocabs (habitat / samplingProtocol) and
        # recordedBy are resolved to ids by commit() — kept out of here so that
        # validation (which calls collect_fields) has no get_or_create side effect.
        out = {name: w.value for name, w in _event_widgets.items()}
        out["confidential"] = 1 if conf_chk.value else 0
        # Not event columns: consumed by events._resolve_geo_fields, which resolves the
        # vocab row by (name, iso_code). Empty when the level was typed by hand, picked
        # from an uncoded row, or the geocoder found no ISO tag.
        out["country_iso"] = country_in.code or ""
        out["state_province_iso"] = state_in.code or ""
        return out

    def _commit(s) -> dict:
        """Resolve the FK-backed fields and return the id triplet to store on the
        event: recordedBy + habitat + samplingProtocol (creating vocab rows as
        needed). Spread into the create/update call alongside collect_fields()."""
        return {
            "recorded_by_id":       recby_state["commit"](s),
            "habitat_id":           habitat_field["commit"](s),
            "sampling_protocol_id": protocol_field["commit"](s),
        }

    def _has_content() -> bool:
        """True if any event field or a FK-backed vocab / recordedBy holds a value."""
        if any(str(w.value or "").strip() for w in _event_widgets.values()):
            return True
        if conf_chk.value:
            return True
        return bool(
            recby_state["get_value"]()
            or habitat_field["get_value"]()
            or protocol_field["get_value"]()
        )

    def _reset() -> None:
        for w in _event_widgets.values():
            w.value = ""
        conf_chk.value = False
        recby_state["set_value"](None)
        habitat_field["set_value"](None)
        protocol_field["set_value"](None)

    def _load(snapshot: dict) -> None:
        """Populate every field from a snapshot dict (suppresses on_field_edit)."""
        def _s(v):
            return "" if v is None else str(v)
        _st["populating"] = True
        for name, w in _event_widgets.items():
            w.value = _s(snapshot.get(name))
        # Re-apply the two coded levels *with* their codes. Without this a name shared by
        # two rows (Limburg BE-VLI / NL-LI) is ambiguous, the field adopts no code, and
        # re-saving would silently re-point the event at the uncoded row.
        country_in.set_value(_s(snapshot.get("country")) or None,
                             snapshot.get("country_iso"))
        state_in.set_value(_s(snapshot.get("state_province")) or None,
                           snapshot.get("state_province_iso"))
        conf_chk.value = bool(snapshot.get("confidential"))
        recby_state["set_value"](snapshot.get("recorded_by") or None)
        habitat_field["set_value"](snapshot.get("habitat") or None)
        protocol_field["set_value"](snapshot.get("sampling_protocol") or None)
        _st["populating"] = False

    def _set_readonly(readonly: bool) -> None:
        editable = not readonly
        _st["editable"] = editable
        for w in _event_widgets.values():
            w.props(remove="readonly") if editable else w.props("readonly")
        conf_chk.set_enabled(editable)
        recby_state["set_readonly"](readonly)
        habitat_field["set_readonly"](readonly)
        protocol_field["set_readonly"](readonly)
        _lookup_btn.set_enabled(editable)
        _lookup_btn.tooltip(
            "Fill country / state / county from coordinates via Photon"
            if editable else "Read-only — detach a copy to edit first"
        )
        _clear_coords_btn.set_enabled(editable)
        _map["set_readonly"](readonly)

    return {
        "collect_fields": _collect_fields,
        "has_content":    _has_content,
        "commit":         _commit,
        "load":           _load,
        "reset":          _reset,
        "set_readonly":   _set_readonly,
        "recby_refresh":  recby_state["refresh"],
        "recby_get":      recby_state["get_value"],
        # Name getters for the FK-backed vocab fields — used by value-based
        # unsaved-changes detection (#47) to snapshot current values without a
        # session (no get_or_create side effect).
        "habitat_get":    habitat_field["get_value"],
        "protocol_get":   protocol_field["get_value"],
    }

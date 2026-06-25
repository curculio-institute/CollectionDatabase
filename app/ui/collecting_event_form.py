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

import httpx
from nicegui import ui

from app.config import get_config
from app.services.vocabularies import habitat_vocab, sampling_protocol_vocab
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


def build_collecting_event_form(
    session_factory,
    *,
    default_recby_fn=None,
    on_field_edit=None,
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

    def _geocode_input(label, on_change=None, placeholder=""):
        """Input + hidden inline warning menu + ok icon. Returns (input, btn, tip, items_col, ok_icon)."""
        with ui.row().classes("col-span-1 items-center gap-0 w-full"):
            inp = ui.input(label, on_change=on_change, placeholder=placeholder).classes("flex-1 min-w-0")
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
        return inp, btn, tip, items_col, ok_icon

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
        """)

    ui.timer(0.3, _inject_coord_paste_js, once=True)

    async def _reverse_geocode(lat: float, lon: float) -> dict | None:
        """Reverse-geocode via Photon (address) + Overpass is_in (enclosing areas).
        Fills fields as a side effect; returns Photon props or None."""
        for _b in (_cntry_warn, _code_warn, _state_warn, _county_warn, _muni_warn):
            _b.classes(add="hidden")
        _locality_warn.classes(add="hidden")

        async def _photon() -> list[dict]:
            async with httpx.AsyncClient(timeout=10) as cl:
                r = await cl.get(
                    "https://photon.komoot.io/reverse",
                    params={"lat": lat, "lon": lon, "lang": "en", "limit": 15},
                    headers={"User-Agent": "EntomologicalCollection/1.0"},
                )
                r.raise_for_status()
                return [f["properties"] for f in r.json().get("features", [])]

        async def _overpass() -> list[dict]:
            q = (
                f"[out:json][timeout:10];"
                f"is_in({lat},{lon})->.a;"
                f"("
                f"  way(pivot.a)[name][boundary=protected_area];"
                f"  way(pivot.a)[name][leisure=nature_reserve];"
                f'  way(pivot.a)[name][landuse~"^(forest|wood)$"];'
                f"  way(pivot.a)[name][place~\"^(island|islet)$\"];"
                f"  relation(pivot.a)[name][boundary=protected_area];"
                f"  relation(pivot.a)[name][leisure=nature_reserve];"
                f'  relation(pivot.a)[name][landuse~"^(forest|wood)$"];'
                f"  relation(pivot.a)[name][place~\"^(island|islet|region)$\"];"
                f");"
                f"out tags;"
            )
            try:
                async with httpx.AsyncClient(timeout=12) as cl:
                    r = await cl.post(
                        "https://overpass-api.de/api/interpreter",
                        data={"data": q},
                        headers={"User-Agent": "EntomologicalCollection/1.0"},
                    )
                    r.raise_for_status()
                    seen: set[str] = set()
                    results: list[dict] = []
                    for el in r.json().get("elements", []):
                        tags = el.get("tags", {})
                        n = tags.get("name", "")
                        if not n or n in seen:
                            continue
                        seen.add(n)
                        place_val = tags.get("place", "")
                        kind = "island" if place_val in ("island", "islet") else "locality"
                        results.append({"name": n, "kind": kind})
                    return results
            except Exception:
                return []

        try:
            all_props, overpass_names = await asyncio.gather(_photon(), _overpass())
        except Exception as ex:
            ui.notify(f"Reverse geocoding failed: {ex}", type="negative")
            return None

        if not all_props:
            ui.notify("Reverse geocoding returned no results.", type="warning")
            return None

        p = all_props[0]
        _st["populating"] = True
        country_in.value = p.get("country", "")
        code_in.value    = p.get("countrycode", "").upper()
        state_in.value   = p.get("state", "")
        county_in.value  = p.get("county", "")
        muni_in.value    = p.get("city") or p.get("locality") or ""
        photon_locality  = _pick_locality(all_props)
        overpass_islands = [r["name"] for r in overpass_names if r["kind"] == "island"]
        overpass_locs    = [r["name"] for r in overpass_names if r["kind"] == "locality"]
        locality_in.value = photon_locality or (overpass_locs[0] if overpass_locs else "")
        island_in.value   = overpass_islands[0] if overpass_islands else ""
        _st["populating"] = False
        _fire_edit()

        _alt_names: list[str] = []
        _seen_locs: set[str] = {locality_in.value}
        for pr in all_props:
            name = pr.get("name", "")
            kv = (pr.get("osm_key", ""), pr.get("osm_value", ""))
            if kv in _LOCALITY_KV and name and name not in _seen_locs:
                _seen_locs.add(name)
                _alt_names.append(name)
        for name in overpass_locs:
            if name not in _seen_locs:
                _seen_locs.add(name)
                _alt_names.append(name)
        if _alt_names:
            _locality_items.clear()
            with _locality_items:
                for _n in _alt_names:
                    async def _pick_alt(nm=_n):
                        locality_in.value = nm
                        _fire_edit()
                        _locality_warn.classes(add="hidden")
                    ui.menu_item(_n, on_click=_pick_alt)
            _locality_tip.text = "Also nearby: " + ", ".join(_alt_names)
            _locality_tip.update()
            _locality_warn.classes(remove="hidden")

        return p

    async def _check_boundary_crossing(
        lat: float, lon: float, radius_m: float, photon_props: dict,
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

        centre = _props_to_snap([photon_props])
        centre["locality"] = locality_in.value
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
            seen: dict[str, dict] = {}
            for snap in snapshots:
                val = snap[field_key]
                if val not in seen:
                    seen[val] = snap
            if len(seen) <= 1:
                if ok_icon is not None:
                    ok_icon.classes(remove="hidden", add="lookup-ok-fade")
                    _ok_shown.append(ok_icon)
                return
            centre_val = centre[field_key]
            alts = [v for v in seen if v != centre_val]
            items_col.clear()
            with items_col:
                for i, (val, snap) in enumerate(seen.items()):
                    display = label_fn(val, snap) if label_fn else val
                    marker  = " (centre)" if i == 0 else ""

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
            country_in.value  = snap["country"]
            code_in.value     = snap["code"]
            state_in.value    = snap["state"]
            county_in.value   = snap["county"]
            muni_in.value     = snap["muni"]
            locality_in.value = snap["locality"]
            _st["populating"] = False
            for _b in (_cntry_warn, _code_warn, _state_warn, _county_warn, _muni_warn, _locality_warn):
                _b.classes(add="hidden")
            _fire_edit()

        icons = ok_icons or [None] * 6
        _show_warn(_cntry_warn,    _cntry_tip,    _cntry_items,    "country",  _apply_snap, ok_icon=icons[0])
        _show_warn(_code_warn,     _code_tip,     _code_items,     "code",     _apply_snap, ok_icon=icons[1],
                   label_fn=lambda c, s: f"{c} ({s['country']})")
        _show_warn(_state_warn,    _state_tip,    _state_items,    "state",    _apply_snap, ok_icon=icons[2])
        _show_warn(_county_warn,   _county_tip,   _county_items,   "county",   _apply_snap, ok_icon=icons[3])
        _show_warn(_muni_warn,     _muni_tip,     _muni_items,     "muni",     _apply_snap, ok_icon=icons[4])
        _show_warn(_locality_warn, _locality_tip, _locality_items, "locality", _apply_snap, ok_icon=icons[5],
                   label_fn=lambda v, s: v if v else "(no named feature)")

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
            _lookup_btn.props("loading=true")
            for _ok in _geocode_ok_icons:
                _ok.classes(add="hidden", remove="lookup-ok-fade")
            _locality_warn.classes(add="hidden")
            p = await _reverse_geocode(lat, lon)
            _lookup_btn.props(remove="loading")
            if p is None:
                return
            ui.notify("Location fields filled from coordinates.", type="positive")
            if unc and unc > 0:
                await _check_boundary_crossing(lat, lon, unc, p, ok_icons=_geocode_ok_icons)
            else:
                for _ok in _geocode_ok_icons:
                    _ok.classes(remove="hidden", add="lookup-ok-fade")
                ui.timer(1.4, lambda: [
                    _ok.classes(add="hidden", remove="lookup-ok-fade") for _ok in _geocode_ok_icons
                ], once=True)

        _lookup_btn = (
            ui.button("Detect Locations from Coordinates", icon="auto_fix_high", on_click=_fill_from_coords)
            .props("flat dense size=sm")
            .tooltip("Fill country / state / county from coordinates via Photon")
        )

    # ── Location ────────────────────────────────────────────────────────────
    ui.label("Location").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
    with ui.grid(columns=5).classes("w-full gap-3 mt-1"):
        country_in, _cntry_warn, _cntry_tip, _cntry_items, _cntry_ok = _geocode_input(
            "country", on_change=_on_country_change)
        code_in, _code_warn, _code_tip, _code_items, _code_ok = _geocode_input(
            "countryCode", on_change=_fire_edit, placeholder="DE")
        state_in, _state_warn, _state_tip, _state_items, _state_ok = _geocode_input(
            "stateProvince", on_change=_on_state_change)
        # administrative region (Regierungsbezirk tier) — a controlled vocab too, but
        # NOT in the Photon cascade (no DwC term; auto-filled from the OSM admin_level-5
        # boundary). Plain input here; resolved name→id in the events service.
        region_in = (ui.input("admin. region", on_change=_fire_edit).classes("col-span-1")
                     .tooltip("Sub-state region (e.g. Oberbayern / Regierungsbezirk) — "
                              "for permit-level queries"))
        county_in, _county_warn, _county_tip, _county_items, _county_ok = _geocode_input(
            "county", on_change=_on_county_change)
        muni_in, _muni_warn, _muni_tip, _muni_items, _muni_ok = _geocode_input(
            "municipality", on_change=_on_muni_change)
    _geocode_ok_icons = [_cntry_ok, _code_ok, _state_ok, _county_ok, _muni_ok]

    with ui.grid(columns=3).classes("w-full gap-3 mt-3"):
        locality_in, _locality_warn, _locality_tip, _locality_items, _locality_ok = _geocode_input(
            "locality", on_change=_fire_edit)
        island_in    = ui.input("island", on_change=_fire_edit).classes("col-span-1")
        verblocal_in = ui.input("verbatimLocality", on_change=_fire_edit).classes("col-span-1")
    _geocode_ok_icons.append(_locality_ok)

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

    # ── field registry: single source for collect / load / reset / readonly ──
    _event_widgets = {
        "country":                          country_in,
        "country_code":                     code_in,
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
        return {name: w.value for name, w in _event_widgets.items()}

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
        return bool(
            recby_state["get_value"]()
            or habitat_field["get_value"]()
            or protocol_field["get_value"]()
        )

    def _reset() -> None:
        for w in _event_widgets.values():
            w.value = ""
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
        recby_state["set_value"](snapshot.get("recorded_by") or None)
        habitat_field["set_value"](snapshot.get("habitat") or None)
        protocol_field["set_value"](snapshot.get("sampling_protocol") or None)
        _st["populating"] = False

    def _set_readonly(readonly: bool) -> None:
        editable = not readonly
        _st["editable"] = editable
        for w in _event_widgets.values():
            w.props(remove="readonly") if editable else w.props("readonly")
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

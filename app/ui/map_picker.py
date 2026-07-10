"""
Leaflet map picker — injected as raw HTML outside the Vue/Quasar app.

Why ui.add_body_html() instead of NiceGUI elements:
  NiceGUI's page template renders {{ body_html }} BEFORE <div id="app">,
  so ui.add_body_html() produces a DOM node that is a sibling of the Vue
  mount point — completely outside Quasar's layout, q-expansion-item
  transitions, and any CSS stacking context they create.  Elements created
  inside @ui.page() (even outside any `with` block) still end up inside
  the Vue app, which puts them under Quasar's q-layout and its children,
  whose transition/overflow CSS interferes with position:fixed.

Interaction model (after iNaturalist):
  - Click map   → place / move marker; initial radius from zoom level
  - Drag marker → move point, circle + handle follow
  - Drag handle → resize uncertainty circle
  - Geocoder    → Nominatim search, flies to result
  - Layer ctrl  → Street map | Satellite | Satellite + labels

Satellite + labels: Esri WorldImagery + WorldBoundariesAndPlaces overlay.
  Geocoder: Photon (photon.komoot.io) — OSM data, better address coverage
  than raw Nominatim, no API key required.

NiceGUI 2.x event bridge (verified against nicegui.js 2.24.2):
  JS → Python:  window.socket.emit("event", {id, client_id, listener_id,
                    args: [JSON.stringify(payload)]})
  Python unwraps single-element list → e.args is the plain dict.
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui


def add_map_assets() -> None:
    """Inject Leaflet + geocoder CSS/JS into the page <head> (once per page)."""
    ui.add_head_html(
        '<link rel="stylesheet" '
        'href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
    )
    ui.add_head_html(
        '<link rel="stylesheet" '
        'href="https://unpkg.com/leaflet-control-geocoder@2.4.0/dist/Control.Geocoder.css">'
    )
    ui.add_head_html(
        '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    )
    ui.add_head_html(
        '<script src="https://unpkg.com/leaflet-control-geocoder@2.4.0/dist/Control.Geocoder.js"></script>'
    )
    ui.add_head_html("""
    <style>
      /* Geocoder control — always light-themed regardless of app dark mode */
      .leaflet-control-geocoder {
        font-family: inherit;
        background: #fff !important;
        border-radius: 4px;
        box-shadow: 0 1px 5px rgba(0,0,0,.3);
      }
      .leaflet-control-geocoder-form input {
        font-family: inherit;
        font-size: .85rem;
        min-width: 220px;
        color: #111 !important;
        background: #fff !important;
        caret-color: #111 !important;
        cursor: text !important;
        border: none;
        outline: none;
        padding: 4px 8px;
      }
      .leaflet-control-geocoder-form input::placeholder {
        color: #999 !important;
      }
      .leaflet-control-geocoder-alternatives {
        background: #fff;
        color: #111;
      }
      .leaflet-control-geocoder-alternatives li:hover {
        background: #f0f4f8;
      }
      .leaflet-control-attribution {
        font-size: .65rem;
        background: rgba(255,255,255,.75) !important;
      }
      ._mp-btn {
        display: inline-flex; align-items: center; gap: 4px;
        background: none; border: none; border-radius: 4px;
        cursor: pointer; padding: 4px 8px;
        font-size: .75rem; font-weight: 500; letter-spacing: .04em;
        color: var(--tp-secondary, #0369a1);
      }
      ._mp-btn:hover { background: rgba(3,105,161,.08); }
    </style>
    """)


def build_map_picker(
    on_change: Callable[[float, float, float | None], None],
    default_layer: str = "street",
) -> dict:
    """
    Build the map-picker and return an API dict::

        {
          'open':            fn()               → show the overlay,
          'set_position':    fn(lat, lon, unc)  → place marker (no fly),
          'fly_to':          fn(lat, lon, unc)  → fly + place marker,
          'set_uncertainty': fn(unc)            → resize circle only,
          'clear':           fn()               → remove marker + circle,
          'set_readonly':    fn(bool)           → view-only: pan/zoom + view
                                                  only; marker/handle snap back,
                                                  clicks + slider inert,
        }

    ``on_change(lat, lon, uncertainty_m)`` fires on every marker move /
    circle resize (never while read-only). `set_readonly` is general-purpose —
    any caller can show an existing point without allowing edits.
    """
    # ── NiceGUI event sink (inside Vue app — only used as a socket relay) ─────
    sink    = ui.element("div").style("display:none")
    sink_id = sink.id

    # uid is the HTML id prefix for this instance's raw-HTML elements.
    # Derived from sink_id which is unique per NiceGUI client session.
    uid = f"mp{sink_id}"

    # ── overlay injected directly into <body> (before <div id="app">) ─────────
    # This ensures position:fixed is relative to the viewport with no
    # Quasar/Vue stacking-context parent anywhere in the ancestor chain.
    ui.add_body_html(f"""
<div id="{uid}ov" style="
    display:none; position:fixed; inset:0; z-index:9999;
    background:rgba(0,0,0,.55);
    align-items:center; justify-content:center;">
  <div style="
      width:92vw; max-width:1500px; height:84vh;
      border-radius:8px; overflow:hidden;
      display:flex; flex-direction:column;
      background:var(--tp-base-foreground,#fff);
      box-shadow:0 24px 80px rgba(0,0,0,.4);">
    <!-- header -->
    <div style="
        display:flex; align-items:center; flex-shrink:0; flex-wrap:wrap;
        padding:6px 16px; gap:8px;
        border-bottom:1px solid var(--tp-base-border,#e2e8f0);">
      <span style="
          font-size:.8rem; font-weight:600; letter-spacing:.06em;
          text-transform:uppercase; color:var(--tp-base-content,#000);">
        Pick location
      </span>
      <!-- coordinate display (hidden until marker placed) -->
      <span id="{uid}coord" style="
          font-size:.82rem; color:var(--tp-base-soft,#9ca3af);">
        Click the map to set a point
      </span>
      <!-- editable uncertainty (hidden until marker placed) -->
      <span id="{uid}uncrow" style="
          display:none; align-items:center; gap:6px; flex:1; min-width:0;
          font-size:.82rem; color:var(--tp-base-soft,#9ca3af);">
        <span>&#xB1;</span>
        <input id="{uid}uncinput" type="number" min="1" step="1"
          style="
            width:70px; font-size:.82rem; flex-shrink:0;
            color:#111; background:#fff; caret-color:#111;
            border:none; border-bottom:1px solid #aaa;
            outline:none; padding:1px 3px; text-align:right;
            -moz-appearance:textfield;"
          title="Uncertainty radius in metres">
        <span style="flex-shrink:0;">m</span>
        <input id="{uid}uncslider" type="range" min="0" max="2000" value="0" step="1"
          style="flex:1; min-width:0; accent-color:var(--tp-secondary,#0369a1); cursor:pointer;"
          title="Uncertainty radius (0–2000 m)">
      </span>
      <button id="{uid}copy" class="_mp-btn" style="display:none;"
        title="Copy latitude, longitude and radius (tab-separated)">
        <i class="material-icons" style="font-size:1.1rem;">content_copy</i>Copy
      </button>
      <span style="flex:1;"></span>
      <button onclick="document.getElementById('{uid}ov').style.display='none'"
        style="background:none;border:none;cursor:pointer;
               font-size:20px;line-height:1;padding:4px 8px;
               color:var(--tp-base-soft,#9ca3af);">&#x2715;</button>
    </div>
    <!-- map -->
    <div id="{uid}map" style="flex:1; overflow:hidden; min-height:0;"></div>
    <!-- footer -->
    <div style="
        display:flex; align-items:center; flex-shrink:0;
        padding:8px 16px; gap:12px;
        border-top:1px solid var(--tp-base-border,#e2e8f0);">
      <button id="{uid}loc" class="_mp-btn">
        <i class="material-icons" style="font-size:1.1rem;">my_location</i>Locate
      </button>
      <span style="flex:1;"></span>
      <button onclick="document.getElementById('{uid}ov').style.display='none'"
        style="background:var(--tp-secondary,#0369a1);color:#fff;
               border:none;cursor:pointer;font-size:.85rem;
               padding:6px 18px;border-radius:6px;">
        Done
      </button>
    </div>
  </div>
</div>
""")

    # ── Python event handler ──────────────────────────────────────────────────
    # Display updates happen client-side in updateDisplay(); Python only
    # needs to forward coords to the form fields via on_change.
    def _on_coords(e) -> None:
        d   = e.args or {}
        lat = d.get("lat")
        lng = d.get("lng")
        unc = d.get("uncertainty")
        if lat is None or lng is None:
            return
        on_change(float(lat), float(lng), float(unc) if unc else None)

    sink.on("map-coords", _on_coords)
    listener_id: str = next(iter(sink._event_listeners.values())).id

    # ── Leaflet init script ───────────────────────────────────────────────────
    # init() retries every 80 ms until the overlay is open (clientHeight > 0).
    # The api object is stored on window so open_map() / clear() can call it.
    _js = f"""
(function() {{
    var SINK        = {sink_id};
    var LISTENER_ID = "{listener_id}";
    var uid         = "{uid}";
    var _done = false;

    function emitCoords(lat, lng, unc) {{
        if (!window.socket || !window.did_handshake) return;
        window.socket.emit("event", {{
            id:          SINK,
            client_id:   window.clientId,
            listener_id: LISTENER_ID,
            args:        [JSON.stringify({{lat:lat, lng:lng, uncertainty:unc||null}})]
        }});
    }}

    function init() {{
        if (typeof L === 'undefined') {{ setTimeout(init, 80); return; }}
        var el = document.getElementById(uid + 'map');
        if (!el || el.clientWidth === 0 || el.clientHeight === 0) {{
            setTimeout(init, 80); return;
        }}
        if (_done) return;
        _done = true;

        var map = L.map(el, {{zoomControl:true}}).setView([48.5, 13.5], 5);

        /* ── Tile layers ─────────────────────────────────────────────────── */
        var osm = L.tileLayer(
            'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
            {{attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
              maxZoom:19}}
        );
        var _img = 'https://server.arcgisonline.com/ArcGIS/rest/services/'
                 + 'World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}';
        var _ref = 'https://server.arcgisonline.com/ArcGIS/rest/services/'
                 + 'Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}';
        var _att = 'Tiles © Esri — Esri, i-cubed, USDA, USGS, AEX, GeoEye, IGN';
        var sat = L.tileLayer(_img, {{attribution:_att, maxZoom:18}});
        var satLabel = L.layerGroup([
            L.tileLayer(_img, {{attribution:_att, maxZoom:18}}),
            L.tileLayer(_ref, {{maxZoom:18}})
        ]);
        var _defaultLayers = {{street: osm, satellite: sat, satellite_labels: satLabel}};
        (_defaultLayers['{default_layer}'] || osm).addTo(map);
        L.control.layers(
            {{'Street map':osm, 'Satellite':sat, 'Satellite + labels':satLabel}},
            {{}},
            {{position:'topright', collapsed:false}}
        ).addTo(map);

        /* ── Photon geocoder (OSM data, better address coverage than Nominatim) */
        if (L.Control && typeof L.Control.geocoder === 'function'
                && L.Control.Geocoder && L.Control.Geocoder.photon) {{
            L.Control.geocoder({{
                geocoder: L.Control.Geocoder.photon({{
                    nameProperties: ['name', 'street', 'city', 'country']
                }}),
                defaultMarkGeocode: false,
                placeholder: 'Search place or address…',
                errorMessage: 'Nothing found.'
            }})
            .on('markgeocode', function(ev) {{
                var g = ev.geocode;
                if (g.bbox) {{
                    map.fitBounds(g.bbox, {{maxZoom:16}});
                }} else {{
                    map.setView(g.center, 14);
                }}
            }})
            .addTo(map)
            ._expand();
        }}

        /* ── State ───────────────────────────────────────────────────────── */
        var marker = null, circle = null, handle = null, unc = 0, ro = false;
        var savedMarkerLatLng = null;   // official position (snap-back when read-only)

        var coordEl   = document.getElementById(uid + 'coord');
        var uncRow    = document.getElementById(uid + 'uncrow');
        var uncInput  = document.getElementById(uid + 'uncinput');
        var uncSlider = document.getElementById(uid + 'uncslider');
        var copyBtn   = document.getElementById(uid + 'copy');

        function updateDisplay(lat, lng, u) {{
            if (coordEl)   coordEl.textContent    = lat.toFixed(6) + ', ' + lng.toFixed(6);
            if (uncRow)    uncRow.style.display   = 'flex';
            if (uncInput)  uncInput.value         = u ? Math.round(u) : '';
            if (uncSlider) uncSlider.value        = Math.min(u ? Math.round(u) : 0, 2000);
            if (copyBtn)   copyBtn.style.display  = 'inline-flex';
        }}

        function resetDisplay() {{
            if (coordEl)   coordEl.textContent  = 'Click the map to set a point';
            if (uncRow)    uncRow.style.display = 'none';
            if (uncInput)  uncInput.value       = '';
            if (uncSlider) uncSlider.value      = 0;
            if (copyBtn)   copyBtn.style.display = 'none';
        }}

        var mkIcon = L.divIcon({{
            className: '',
            html: '<div style="width:14px;height:14px;border-radius:50%;'
                + 'background:#0369a1;border:2.5px solid #fff;'
                + 'box-shadow:0 1px 5px rgba(0,0,0,.55);cursor:move;"></div>',
            iconSize:[14,14], iconAnchor:[7,7]
        }});
        var hdIcon = L.divIcon({{
            className: '',
            html: '<div style="width:14px;height:14px;border-radius:50%;'
                + 'background:#fff;border:2.5px solid #0369a1;'
                + 'box-shadow:0 1px 4px rgba(0,0,0,.4);cursor:ew-resize;"></div>',
            iconSize:[14,14], iconAnchor:[7,7]
        }});

        function eastOf(c, r) {{
            return L.latLng(c.lat,
                c.lng + r / (6378137 * Math.cos(Math.PI*c.lat/180)) * (180/Math.PI));
        }}

        function placeAt(latlng, newUnc) {{
            if (newUnc !== undefined) unc = newUnc;
            if (!marker) {{
                marker = L.marker(latlng,
                    {{icon:mkIcon, draggable:true, zIndexOffset:100}}).addTo(map);
                marker.on('drag', function(ev) {{
                    if (ro) {{ marker.setLatLng(savedMarkerLatLng); return; }}
                    var p = ev.target.getLatLng();
                    if (circle) circle.setLatLng(p);
                    if (handle && unc) handle.setLatLng(eastOf(p, unc));
                    updateDisplay(p.lat, p.lng, unc);
                }});
                marker.on('dragend', function(ev) {{
                    if (ro) {{ marker.setLatLng(savedMarkerLatLng); return; }}
                    var p = ev.target.getLatLng();
                    emitCoords(p.lat, p.lng, unc || null);
                }});
            }} else {{
                marker.setLatLng(latlng);
            }}

            if (unc > 0) {{
                if (!circle) {{
                    circle = L.circle(latlng, {{
                        radius:unc, color:'#0369a1', weight:2,
                        fillColor:'#0369a1', fillOpacity:0.12
                    }}).addTo(map);
                }} else {{
                    circle.setLatLng(latlng); circle.setRadius(unc);
                }}
                var east = eastOf(latlng, unc);
                if (!handle) {{
                    handle = L.marker(east,
                        {{icon:hdIcon, draggable:true, zIndexOffset:200}}).addTo(map);
                    handle.on('drag', function(ev) {{
                        if (!marker) return;
                        if (ro) {{ handle.setLatLng(eastOf(marker.getLatLng(), unc)); return; }}
                        unc = Math.max(1, Math.round(
                            map.distance(marker.getLatLng(), ev.target.getLatLng())));
                        if (circle) circle.setRadius(unc);
                        updateDisplay(marker.getLatLng().lat, marker.getLatLng().lng, unc);
                    }});
                    handle.on('dragend', function(ev) {{
                        if (!marker) return;
                        if (ro) {{ handle.setLatLng(eastOf(marker.getLatLng(), unc)); return; }}
                        var c = marker.getLatLng();
                        unc = Math.max(1, Math.round(
                            map.distance(c, ev.target.getLatLng())));
                        if (circle) circle.setRadius(unc);
                        handle.setLatLng(eastOf(c, unc));
                        emitCoords(c.lat, c.lng, unc);
                    }});
                }} else {{
                    handle.setLatLng(east);
                }}
            }} else {{
                if (circle) {{ circle.remove(); circle = null; }}
                if (handle)  {{ handle.remove(); handle = null; }}
            }}

            updateDisplay(latlng.lat, latlng.lng, unc);
            if (marker) savedMarkerLatLng = marker.getLatLng();
        }}

        /* zoom-based default radius on first click (iNaturalist formula) */
        map.on('click', function(ev) {{
            if (ro) return;
            var r = unc > 0 ? unc
                  : Math.round((1 / Math.pow(2, map.getZoom())) * 2000000);
            placeAt(ev.latlng, r);
            emitCoords(ev.latlng.lat, ev.latlng.lng, r);
        }});

        /* ── Editable uncertainty input + slider ────────────────────────── */
        function applyUncInput() {{
            if (ro || !marker) return;
            var v = parseInt(uncInput.value, 10);
            if (isNaN(v) || v < 1) return;
            placeAt(marker.getLatLng(), v);
            emitCoords(marker.getLatLng().lat, marker.getLatLng().lng, v);
        }}
        if (uncInput) {{
            uncInput.addEventListener('change', applyUncInput);
            uncInput.addEventListener('keydown', function(e) {{
                if (e.key === 'Enter') {{ applyUncInput(); this.blur(); }}
            }});
        }}
        if (uncSlider) {{
            uncSlider.addEventListener('input', function() {{
                if (ro || !marker) return;
                var v = parseInt(this.value, 10);
                placeAt(marker.getLatLng(), v);
                emitCoords(marker.getLatLng().lat, marker.getLatLng().lng, v || null);
            }});
        }}

        /* ── API object ──────────────────────────────────────────────────── */
        window['_mpapi_' + uid] = {{
            open: function() {{
                document.getElementById(uid + 'ov').style.display = 'flex';
                setTimeout(function() {{ map.invalidateSize(); }}, 120);
            }},
            setPosition: function(lat, lng, u) {{
                placeAt(L.latLng(lat, lng), u != null ? u : unc);
                map.setView([lat, lng], Math.max(map.getZoom(), 10));
            }},
            setUncertainty: function(u) {{
                if (marker) placeAt(marker.getLatLng(), u || 0);
                else unc = u || 0;
            }},
            clear: function() {{
                if (marker) {{ marker.remove(); marker = null; }}
                if (circle) {{ circle.remove(); circle = null; }}
                if (handle) {{ handle.remove(); handle = null; }}
                unc = 0;
                resetDisplay();
            }},
            setReadonly: function(v) {{
                ro = v;
                if (uncInput)  uncInput.disabled  = v;
                if (uncSlider) uncSlider.disabled = v;
            }}
        }};

        /* "Locate" button — fly to coordinates currently entered in the form */
        var locBtn = document.getElementById(uid + 'loc');
        if (locBtn) {{
            locBtn.onclick = function() {{
                var latEl = document.querySelector('._coord-lat input');
                var lonEl = document.querySelector('._coord-lon input');
                var uncEl = document.querySelector('._coord-unc input');
                if (!latEl || !lonEl) return;
                var lat = parseFloat(latEl.value);
                var lon = parseFloat(lonEl.value);
                if (isNaN(lat) || isNaN(lon)) return;
                var u = uncEl ? parseFloat(uncEl.value) : NaN;
                placeAt(L.latLng(lat, lon), isNaN(u) ? (unc || 0) : u);
                map.setView([lat, lon], Math.max(map.getZoom(), 10));
            }};
        }}

        /* "Copy" button — lat, lon, radius (tab-separated) to the clipboard */
        if (copyBtn) {{
            var flashCopied = function() {{
                var icon = copyBtn.querySelector('.material-icons');
                if (!icon) return;
                var prev = icon.textContent;
                icon.textContent = 'check';
                setTimeout(function() {{ icon.textContent = prev; }}, 1200);
            }};
            var execCopy = function(text) {{
                var ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.top = '0'; ta.style.left = '0'; ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.focus(); ta.select();
                var ok = false;
                try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
                document.body.removeChild(ta);
                if (ok) flashCopied();
            }};
            copyBtn.onclick = function() {{
                if (!marker) return;
                var c = marker.getLatLng();
                var text = c.lat.toFixed(6) + '\\t' + c.lng.toFixed(6)
                         + '\\t' + (unc ? Math.round(unc) : '');
                /* Clipboard API can reject (focus/permission) even where it
                   exists — always fall back to execCommand on failure. */
                if (navigator.clipboard && navigator.clipboard.writeText) {{
                    navigator.clipboard.writeText(text).then(
                        flashCopied,
                        function() {{ execCopy(text); }}
                    );
                }} else {{
                    execCopy(text);
                }}
            }};
        }}
    }}

    init();
}})();
"""

    ui.timer(0.25, lambda: ui.run_javascript(_js), once=True)

    # ── public API ────────────────────────────────────────────────────────────
    def _run(js: str) -> None:
        ui.run_javascript(js)

    def open_map() -> None:
        # Show the overlay immediately; the init retry loop handles first-time
        # Leaflet startup, and the api.open() call handles invalidateSize.
        _run(
            f"var api=window['_mpapi_{uid}'];"
            f"if(api){{api.open();}}"
            f"else{{document.getElementById('{uid}ov').style.display='flex';"
            f"setTimeout(function(){{var a=window['_mpapi_{uid}'];if(a)a.open();}},300);}}"
        )

    def set_position(lat: float, lon: float, uncertainty_m: float | None = None) -> None:
        u = str(uncertainty_m) if uncertainty_m is not None else "null"
        _run(f"window['_mpapi_{uid}']?.setPosition({lat},{lon},{u});")

    def fly_to(lat: float, lon: float, uncertainty_m: float | None = None) -> None:
        """Open the overlay and set position, waiting for Leaflet init if needed."""
        u = str(uncertainty_m) if uncertainty_m is not None else "null"
        _run(
            f"(function go(){{"
            f"  var api=window['_mpapi_{uid}'];"
            f"  if(api){{api.open();api.setPosition({lat},{lon},{u});return;}}"
            f"  document.getElementById('{uid}ov').style.display='flex';"
            f"  setTimeout(go,100);"
            f"}})();"
        )

    def set_uncertainty(uncertainty_m: float | None) -> None:
        u = str(int(uncertainty_m)) if uncertainty_m is not None else "0"
        _run(f"window['_mpapi_{uid}']?.setUncertainty({u});")

    def clear() -> None:
        _run(f"window['_mpapi_{uid}']?.clear();")

    def set_readonly(read_only: bool) -> None:
        """View-only mode: marker/handle not draggable, map clicks and the
        uncertainty input/slider inert. The map stays viewable."""
        _run(f"window['_mpapi_{uid}']?.setReadonly({str(bool(read_only)).lower()});")

    return {
        "open":            open_map,
        "set_position":    set_position,
        "fly_to":          fly_to,
        "set_uncertainty": set_uncertainty,
        "clear":           clear,
        "set_readonly":    set_readonly,
    }

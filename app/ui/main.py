"""Collection app — main UI.

Two tabs:
  • Specimen Digitization — entry form + recent-specimens table
  • Taxonomy             — checklist tree with species / specimen counts

All DB access goes through app.services — no ORM queries in this file.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import sys

import httpx

from nicegui import ui

from app.database import get_engine, get_session_factory
import app.services as svc
import app.services.taxonomy as tax_svc
import app.services.identifiers as id_svc
import app.services.labels as lbl_svc
import app.services.print_queue as pq_svc
from app.config import get_config, save_config
from app.models import CollectionObject, CollectingEvent, TaxonDetermination, LabelCode
from app.ui.taxon_search import build_taxon_search
from app.ui.identification_list import build_identification_list
from app.ui.import_assign import build_import_assign_tab
from app.ui.controlled_vocab_tab import build_controlled_vocab_tab
from app.ui.map_picker import add_map_assets, build_map_picker
from app.ui.bio_object_search import build_bio_object_search
from app.ui.taxon_editor import build_taxon_editor
from app.ui.records_tab import build_records_tab
from app.services.biological import (
    sync_biological_relationships,
    get_relationship_options,
    save_biological_association,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_IDENTIFIED_BY = "J. Jilg"
DEFAULT_NAMESPACE     = "Jilg"

SAMPLING_PROTOCOLS = [
    "hand collecting", "sweep net", "beating", "pitfall trap",
    "light trap", "sifting", "bark peeling", "rearing", "Berlese funnel",
    "yellow pan trap", "window trap", "observation", "",
]
SEX_OPTIONS         = ["male", "female", "undetermined", ""]
LIFE_STAGE_OPTIONS  = ["adult", "larva", "pupa", "egg", ""]
BASIS_OPTIONS       = ["PreservedSpecimen", "FossilSpecimen", "LivingSpecimen",
                       "HumanObservation", "MachineObservation"]
DISPOSITION_OPTIONS = ["in collection", "on loan", "donated",
                       "exchanged", "missing", "destroyed", ""]

TABLE_COLS = [
    {"name": "id",       "label": "ID",       "field": "id",       "align": "right",  "sortable": True},
    {"name": "catalog",  "label": "Catalog",  "field": "catalog",  "align": "left",   "sortable": True},
    {"name": "species",  "label": "Species",  "field": "species",  "align": "left",   "sortable": True},
    {"name": "sex",      "label": "Sex",      "field": "sex",      "align": "center"},
    {"name": "n",        "label": "n",        "field": "n",        "align": "right"},
    {"name": "country",  "label": "Country",  "field": "country",  "align": "left"},
    {"name": "locality", "label": "Locality", "field": "locality", "align": "left"},
    {"name": "date",     "label": "Date",     "field": "date",     "align": "left",   "sortable": True},
    {"name": "leg",      "label": "leg.",      "field": "leg",      "align": "left"},
    {"name": "det",      "label": "det.",      "field": "det",      "align": "left"},
]

# ---------------------------------------------------------------------------
# Coordinate paste helper
# ---------------------------------------------------------------------------

# (osm_key, osm_value) → priority for DwC locality (higher = preferred).
# Only meaningful collecting localities are included; cemeteries, industrial
# areas, etc. are intentionally absent.
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


def _split_coord_paste(text: str) -> tuple[str, str] | None:
    """
    Return (lat_str, lon_str) if *text* looks like a coordinate pair, else None.

    Handles common copy-paste formats:
      "52.6413478072, 13.486226052"
      "52.5295    13.3793"
      "52.6413478072;13.486226052"
    """
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text.strip())
    if len(nums) < 2:
        return None
    try:
        lat, lon = float(nums[0]), float(nums[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return nums[0], nums[1]


# ---------------------------------------------------------------------------
# Engine (module-level, created once)
# ---------------------------------------------------------------------------

_engine = get_engine()
_sf     = get_session_factory(_engine)

# Backfill parent-rank rows (family/subfamily/tribe/subtribe/genus/subgenus)
# for any species imported before this logic existed.  Idempotent.
with _sf() as _s:
    with _s.begin():
        from app.services.taxa import ensure_higher_taxa as _eht, seed_root_taxa as _srt
        _eht(_s)
        _srt(_s)


def _with_session(fn):
    with _sf() as s:
        return fn(s)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@ui.page("/")
def index():

    # ── dark mode (Quasar integration) ──────────────────────────────────
    dark_mode = ui.dark_mode()

    async def _init_theme():
        is_dark = await ui.run_javascript(
            "document.documentElement.classList.contains('dark')"
        )
        if is_dark:
            dark_mode.enable()
            theme_btn.props("icon=light_mode")
        else:
            dark_mode.disable()
            theme_btn.props("icon=dark_mode")

    async def _toggle_theme():
        is_dark = await ui.run_javascript("""
            const d = document.documentElement.classList.toggle('dark');
            localStorage.setItem('tp-theme', d ? 'dark' : 'light');
            return d;
        """)
        if is_dark:
            dark_mode.enable()
            theme_btn.props("icon=light_mode")
        else:
            dark_mode.disable()
            theme_btn.props("icon=dark_mode")

    # ── Tab-to-complete on select dropdowns ─────────────────────────────
    # When a q-select is focused and the filtered dropdown has exactly one
    # visible item, Tab selects it instead of moving focus away.
    ui.add_head_html("""
    <script>
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Tab') return;
        var active = document.activeElement;
        if (!active) return;
        var qSelect = active.closest('.q-select');
        if (!qSelect) return;
        // q-menu is position:fixed so offsetParent is always null — use computed style
        var menus = document.querySelectorAll('.q-menu');
        var openMenu = null;
        for (var i = 0; i < menus.length; i++) {
            var ms = window.getComputedStyle(menus[i]);
            if (ms.display !== 'none' && ms.visibility !== 'hidden' && ms.opacity !== '0') {
                openMenu = menus[i]; break;
            }
        }
        if (!openMenu) return;
        // Quasar renders only matched options as q-item--clickable in the open menu
        var items = openMenu.querySelectorAll('.q-item--clickable');
        if (items.length === 0) items = openMenu.querySelectorAll('.q-item');
        var visible = [];
        for (var j = 0; j < items.length; j++) {
            var s = window.getComputedStyle(items[j]);
            if (s.display !== 'none' && s.visibility !== 'hidden') visible.push(items[j]);
        }
        if (visible.length !== 1) return;
        e.preventDefault();
        e.stopPropagation();
        visible[0].click();
        // After Quasar processes the click (focus returns to q-select internals),
        // jump directly to the next focusable element after the entire q-select.
        setTimeout(function() {
            var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
                'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
            var all = Array.from(document.querySelectorAll(FOCUSABLE)).filter(function(el) {
                var s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden';
            });
            // Find the last focusable element that lives inside this q-select
            var lastInside = -1;
            for (var k = 0; k < all.length; k++) {
                if (qSelect.contains(all[k])) lastInside = k;
            }
            if (lastInside >= 0 && lastInside + 1 < all.length) {
                all[lastInside + 1].focus();
            }
        }, 30);
    }, true);
    </script>""")

    # ── SVG favicon (vector, sharp at any size; ICO kept as fallback) ────
    ui.add_head_html(
        '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">'
    )

    # ── flash-prevention (runs before CSS paint) ─────────────────────────
    ui.add_head_html("""
    <script>
    (function(){
      var s = localStorage.getItem('tp-theme');
      var d = s === 'dark' || (s === null && window.matchMedia('(prefers-color-scheme: dark)').matches);
      if (d) document.documentElement.classList.add('dark');
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e){
        if (!localStorage.getItem('tp-theme'))
          document.documentElement.classList.toggle('dark', e.matches);
      });
    })();
    </script>""")

    # ── Leaflet + geocoder assets ────────────────────────────────────────
    add_map_assets()

    # ── CSS variables + dark overrides (TaxonPages palette) ─────────────
    ui.add_head_html("""
    <style>
      :root {
        --tp-primary:           rgb(0,0,0);
        --tp-primary-content:   rgb(255,255,255);
        --tp-secondary:         rgb(3,105,161);
        --tp-secondary-hover:   #075985;
        --tp-base-background:   rgb(245,247,251);
        --tp-base-foreground:   rgb(255,255,255);
        --tp-base-muted:        rgb(226,232,240);
        --tp-base-soft:         rgb(156,163,175);
        --tp-base-lighter:      rgb(55,65,81);
        --tp-base-border:       rgb(203,213,225);
        --tp-base-content:      rgb(0,0,0);
      }
      .dark {
        --tp-primary:           rgb(23,23,23);
        --tp-primary-content:   rgb(255,255,255);
        --tp-secondary:         rgb(14,165,233);
        --tp-secondary-hover:   #0284c7;
        --tp-base-background:   rgb(23,23,23);
        --tp-base-foreground:   rgb(38,38,38);
        --tp-base-muted:        rgb(48,48,48);
        --tp-base-soft:         rgb(200,200,200);
        --tp-base-lighter:      rgb(220,220,220);
        --tp-base-border:       rgb(55,55,55);
        --tp-base-content:      rgb(255,255,255);
      }
      body              { background:var(--tp-base-background); color:var(--tp-base-content); }
      .app-header       { background:var(--tp-primary) !important;
                          color:var(--tp-primary-content) !important; padding:.75rem 1.5rem; }
      .app-tabs         { background:var(--tp-base-foreground) !important;
                          border-bottom:1px solid var(--tp-base-border); }
      .app-tabs .q-tab  { color:var(--tp-base-soft) !important; font-size:.82rem; min-height:42px; }
      .app-tabs .q-tab--active      { color:var(--tp-secondary) !important; }
      .app-tabs .q-tabs__indicator  { background:var(--tp-secondary) !important; }
      .section-label    { font-size:.68rem; font-weight:700; letter-spacing:.1em;
                          text-transform:uppercase; color:var(--tp-base-soft); }
      .event-linked     { color:var(--tp-secondary); font-size:.8rem; font-style:italic; }
      .event-new        { color:var(--tp-base-soft);  font-size:.8rem; font-style:italic; }
      .q-card           { border:1px solid var(--tp-base-border) !important;
                          background:var(--tp-base-foreground) !important; }
      .btn-save         { background:var(--tp-secondary) !important; color:#fff !important; }
      .btn-save:hover   { background:var(--tp-secondary-hover) !important; }
      .q-table thead tr th       { color:var(--tp-base-lighter); font-size:.72rem; }
      .q-table tbody tr td       { border-bottom:1px solid var(--tp-base-muted);
                                   color:var(--tp-base-content); }
      .q-table tbody tr:hover td { background:var(--tp-base-background) !important; }
      .q-table__bottom           { color:var(--tp-base-soft); }
      .q-expansion-item__toggle-icon { color:var(--tp-secondary) !important; }
      /* dark: Quasar input / select */
      .dark .q-field__control   { background:var(--tp-base-foreground) !important; }
      .dark .q-field__label     { color:var(--tp-base-soft) !important; }
      .dark .q-field__native,
      .dark .q-field__input     { color:var(--tp-base-content) !important; }
      .dark .q-separator        { background:var(--tp-base-border); }
      .dark .q-item             { color:var(--tp-base-content); }
      .dark .q-menu             { background:var(--tp-base-foreground) !important; }
      .dark .q-checkbox__label  { color:var(--tp-base-content); }
      /* dark: tab panel background */
      .dark .q-tab-panels        { background:var(--tp-base-background) !important; }
      .dark .q-tab-panel         { background:var(--tp-base-background) !important; }
      /* ── taxonomy checklist ────────────────────────────────────────── */
      /* rank-based typography — mirrors scientific paper checklists */
      .rank-family    { font-size:1rem;   font-weight:700;
                        text-transform:uppercase; letter-spacing:.06em; }
      .rank-subfamily { font-size:.9rem;  font-weight:600; }
      .rank-tribe     { font-size:.875rem;font-weight:500; }
      .rank-subtribe  { font-size:.85rem; font-style:italic; }
      .rank-genus     { font-size:.875rem;font-weight:700; font-style:italic; }
      .rank-subgenus  { font-size:.85rem; font-style:italic; }
      .rank-species     { font-size:.85rem; font-style:italic; }
      .rank-subspecies  { font-size:.85rem; font-style:italic; }
      .rank-variety     { font-size:.85rem; font-style:italic; }
      .rank-form        { font-size:.85rem; font-style:italic; }
      .rank-synonym   { font-size:.82rem; font-style:italic;
                        color:var(--tp-base-soft); }
      /* count chips */
      .tax-stat-chip  { display:inline-block; font-size:.65rem; font-weight:600;
                        padding:1px 6px; border-radius:10px; vertical-align:middle; }
      .tax-stat-spp   { background:rgba(3,105,161,.1);  color:var(--tp-secondary); }
      .tax-stat-spec  { background:var(--tp-base-muted); color:var(--tp-base-lighter); }
      .dark .tax-stat-spp  { background:rgba(14,165,233,.15); }
      .dark .tax-stat-spec { background:var(--tp-base-muted); color:var(--tp-base-soft); }
      /* tighten tree row spacing for dense checklist feel */
      .q-tree > .q-tree__node { padding-top:0; padding-bottom:0; }
      .q-tree .q-tree__node-header { padding:2px 4px; min-height:0; }
      /* scrollbar */
      ::-webkit-scrollbar       { width:5px; height:5px; }
      ::-webkit-scrollbar-track { background:var(--tp-base-muted); }
      ::-webkit-scrollbar-thumb { background:var(--tp-base-soft); border-radius:3px; }
            /* ── Beetle (ICZN) icon — from Scan230308213350-0001.svg via potrace */
      .iczn-tab .q-tab__label::before {
        content: '';
        display: inline-block;
        width: 1.7em; height: 1.7em;
        background-image: url('/static/beetle_blue.svg');
        background-size: contain;
        background-repeat: no-repeat;
        background-position: center;
        vertical-align: text-bottom;
        margin-right: 3px;
      }
      .dark .iczn-tab .q-tab__label::before {
        background-image: url('/static/beetle_blue_dark.svg');
      }
      /* Reusable inline beetle — <span class="beetle-icon"></span> */
      .beetle-icon {
        display: inline-block;
        width: 1.65em; height: 1.65em;
        background-image: url('/static/beetle_blue.svg');
        background-size: contain;
        background-repeat: no-repeat;
        background-position: center;
        vertical-align: middle;
      }
      .dark .beetle-icon {
        background-image: url('/static/beetle_blue_dark.svg');
      }
      /* Header beetle (white, larger) */
      .header-beetle {
        display: inline-block;
        width: 2.2rem; height: 2.2rem;
        background-image: url('/static/beetle_white.png');
        background-size: contain;
        background-repeat: no-repeat;
        background-position: center;
        vertical-align: middle;
        flex-shrink: 0;
      }
      @keyframes lookup-fade { from { opacity:1; } to { opacity:0; } }
      .lookup-ok-fade { animation: lookup-fade 1s ease-in 0.3s forwards; }
    </style>""")

    # ── Mutable list — bio-object search reads this on each keystroke ────
    # Mutated in-place by the "Show animals" toggle and the settings dialog.
    bio_codes: list[str] = list(get_config().bio_assoc_default_codes)

    # ── Settings dialog (content appended at end of index()) ─────────────
    settings_dialog = ui.dialog()

    # ── header ───────────────────────────────────────────────────────────
    with ui.header().classes("app-header items-center gap-4"):
        ui.html('<span class="header-beetle"></span>')
        ui.label("Collection").style(
            "font-size:1.1rem; font-weight:300; letter-spacing:.12em;"
        )
        ui.space()
        (
            ui.button(icon="settings", on_click=settings_dialog.open)
            .props("flat round dense")
            .style("color:rgb(156,163,175)")
            .tooltip("Settings")
        )
        (
            ui.button(icon="restart_alt", on_click=lambda: os.execv(sys.executable, [sys.executable] + sys.argv))
            .props("flat round dense")
            .style("color:rgb(156,163,175)")
            .tooltip("Restart server")
        )
        theme_btn = (
            ui.button(icon="dark_mode", on_click=_toggle_theme)
            .props("flat round dense")
            .style("color:rgb(156,163,175)")
            .tooltip("Toggle dark / light mode")
        )

    ui.timer(0.1, _init_theme, once=True)

    # ── Sync TW biological relationships once per session (background) ───
    async def _bio_sync():
        try:
            with _sf() as s:
                with s.begin():
                    await sync_biological_relationships(s)
        except Exception:
            pass  # TW unreachable — local rows serve as fallback

    asyncio.create_task(_bio_sync())

    # ── tab bar ──────────────────────────────────────────────────────────
    with ui.element("div").classes("app-tabs w-full sticky top-0").style("z-index:200"):
        with ui.row().classes("w-full max-w-5xl mx-auto"):
            main_tabs = (
                ui.tabs(value="digitize")
                .props("dense indicator-color=secondary align=left no-caps")
                .classes("app-tabs")
            )
            with main_tabs:
                ui.tab("digitize", label="Specimen Digitization", icon="biotech")
                ui.tab("records",  label="Records",               icon="edit_note")
                ui.tab("import",   label="Import & Assign",       icon="upload_file")
                ui.tab("taxonomy", label="Taxonomy",              icon="account_tree")
                ui.tab("labels",   label="Labels",                icon="label")
                ui.tab("vocab",    label="Controlled Vocabularies", icon="manage_accounts")

    # Cross-tab refresh registry — populated as tabs build, called by earlier tabs.
    _refreshers: dict[str, callable] = {}

    # ── tab panels ───────────────────────────────────────────────────────
    with ui.tab_panels(main_tabs, value="digitize").classes("w-full"):

        # ================================================================
        # TAB: SPECIMEN DIGITIZATION
        # ================================================================
        with ui.tab_panel("digitize"):
            # ── per-connection state ─────────────────────────────────────
            state = {"event_id": None, "populating": False}
            bio_state: dict = {
                "associations": [],  # list of {rel_id, rel_name, taxon_id, taxon_label}
            }

            def _event_opts() -> dict:
                return _with_session(
                    lambda s: {o.id: o.summary
                               for o in svc.search_collecting_events(s, "")}
                )

            def _table_rows() -> list[dict]:
                rows = _with_session(lambda s: svc.recent_specimens(s))
                return [
                    {
                        "id":      str(r.collection_object_id),
                        "catalog": f"{r.collection_code} {r.catalog_number}",
                        "species": r.scientific_name,
                        "sex":     r.sex or "",
                        "n":       str(r.individual_count if r.individual_count is not None else ""),
                        "country": r.country or "",
                        "locality":r.locality or "",
                        "date":    r.event_date or "",
                        "leg":     r.recorded_by or "",
                        "det":     r.identified_by or "",
                    }
                    for r in rows
                ]

            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):

                # ── SPECIMEN ─────────────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    ui.label("Specimen").classes("section-label")
                    ui.separator().classes("mb-3")

                    def _reserved_opts() -> dict:
                        return _with_session(id_svc.reserved_codes)

                    with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                        cat_num = ui.select(
                            options={c: c for c in _reserved_opts()},
                            with_input=True,
                            clearable=True,
                            label="identifier *",
                        ).classes("w-32")
                        sex_sel  = ui.select(SEX_OPTIONS, label="sex").classes("w-28")
                        count_in = ui.number("n", value=1, min=0, precision=0).classes("w-20")
                        preps_in = ui.input("preparations", placeholder="pinned, in ethanol…").classes("flex-1 min-w-40")
                    ui.timer(2.0, lambda: cat_num.__setattr__("options", {c: c for c in _reserved_opts()}))
                    with ui.expansion("More fields").classes("w-full mt-2"):
                        with ui.grid(columns=4).classes("w-full gap-3"):
                            stage_sel = ui.select(LIFE_STAGE_OPTIONS, label="lifeStage").classes("col-span-1")
                            type_in   = ui.input("typeStatus").classes("col-span-1")
                            disp_sel  = ui.select(DISPOSITION_OPTIONS, label="disposition",
                                                   value="in collection").classes("col-span-1")
                            basis_sel = ui.select(BASIS_OPTIONS, label="basisOfRecord",
                                                   value="PreservedSpecimen").classes("col-span-1")
                        rem_in = ui.input("occurrenceRemarks").classes("w-full mt-3")

                # ── IDENTIFICATION ────────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    ui.label("Identifications").classes("section-label")
                    ui.separator().classes("mb-3")
                    det_state = build_identification_list(_sf)

                # ── COLLECTING EVENT ─────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-3 mb-1"):
                        ui.label("Collecting Event").classes("section-label")
                        event_status = ui.label("· new event").classes("event-new")

                    ui.separator().classes("mb-3")

                    event_sel = (
                        ui.select(options=_event_opts(), with_input=True,
                                   clearable=True, label="Search existing events…")
                        .classes("w-full mb-4")
                        .tooltip("Type any locality, date, or collector name")
                    )
                    ui.timer(2.0, lambda: event_sel.__setattr__("options", _event_opts()))

                    def _on_event_field_edit(_=None):
                        if not state["populating"] and state["event_id"] is not None:
                            state["event_id"] = None
                            event_status.set_text("· new event (edited)")
                            event_status.classes(remove="event-linked", add="event-new")

                    def _wipe_from(level: str) -> None:
                        """Clear address fields finer than *level* and hide their stale warnings.

                        Called on manual edits (via on_change) and from warning-dropdown picks.
                        Variables are captured by closure — all exist by the time any callback fires.
                        """
                        if level == "country":
                            state_in.value    = ""
                            county_in.value   = ""
                            muni_in.value     = ""
                            locality_in.value = ""
                            for _b in (_state_warn, _county_warn, _muni_warn, _locality_warn):
                                _b.classes(add="hidden")
                        elif level == "state":
                            county_in.value   = ""
                            muni_in.value     = ""
                            locality_in.value = ""
                            for _b in (_county_warn, _muni_warn, _locality_warn):
                                _b.classes(add="hidden")
                        elif level == "county":
                            muni_in.value     = ""
                            locality_in.value = ""
                            for _b in (_muni_warn, _locality_warn):
                                _b.classes(add="hidden")
                        elif level == "muni":
                            locality_in.value = ""
                            _locality_warn.classes(add="hidden")

                    def _on_country_change(_=None):
                        if not state["populating"]:
                            _wipe_from("country")
                        _on_event_field_edit()

                    def _on_state_change(_=None):
                        if not state["populating"]:
                            _wipe_from("state")
                        _on_event_field_edit()

                    def _on_county_change(_=None):
                        if not state["populating"]:
                            _wipe_from("county")
                        _on_event_field_edit()

                    def _on_muni_change(_=None):
                        if not state["populating"]:
                            _wipe_from("muni")
                        _on_event_field_edit()

                    def _geocode_input(label, on_change=None, placeholder=""):
                        """Input + hidden inline warning/ok icons. Returns (input, btn, tooltip, items_col, ok_icon)."""
                        with ui.row().classes("col-span-1 items-center gap-0 w-full") as row:
                            inp = ui.input(
                                label, on_change=on_change, placeholder=placeholder,
                            ).classes("flex-1 min-w-0")
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
                        # Fallback when JS paste interceptor isn't installed yet.
                        val = str(e.value) if e.value is not None else ""
                        pair = _split_coord_paste(val)
                        if pair:
                            lat_in.value = pair[0]
                            lon_in.value = pair[1]
                        _on_event_field_edit()

                    ui.label("Coordinates").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-2")
                    with ui.grid(columns=5).classes("w-full gap-3 mt-1"):
                        lat_in      = ui.input("latitude",      on_change=_on_lat_change).classes("col-span-1 _coord-lat")
                        lon_in      = ui.input("longitude",     on_change=_on_event_field_edit).classes("col-span-1 _coord-lon")
                        uncert_in   = ui.input("uncertainty m", on_change=_on_event_field_edit).classes("col-span-1 _coord-unc")
                        elev_min_in = ui.input("elev min m",    on_change=_on_event_field_edit).classes("col-span-1")
                        elev_max_in = ui.input("elev max m",    on_change=_on_event_field_edit).classes("col-span-1")

                    # Sink element: receives coord-paste socket events from the JS
                    # paste interceptor below.  Using the NiceGUI socket bridge
                    # (same pattern as map_picker) is more reliable than dispatching
                    # synthetic DOM input events, which Quasar may silently drop.
                    _coord_sink = ui.element('span').style('display:none')

                    def _on_coord_paste_event(e):
                        try:
                            d = e.args  # already a dict: {lat, lon}
                            lat_in.value = str(d["lat"])
                            lon_in.value = str(d["lon"])
                            _on_event_field_edit()
                        except (KeyError, TypeError):
                            pass

                    _coord_sink.on('coord-paste', _on_coord_paste_event)
                    _csink_id  = _coord_sink.id
                    _clid      = list(_coord_sink._event_listeners.keys())[-1]

                    async def _inject_coord_paste_js():
                        await ui.run_javascript(f"""
                        (function install() {{
                            var latEl = document.querySelector('._coord-lat input');
                            var lonEl = document.querySelector('._coord-lon input');
                            if (!latEl) {{ setTimeout(install, 300); return; }}
                            latEl.addEventListener('paste', function(ev) {{
                                var text = (ev.clipboardData || window.clipboardData).getData('text');
                                var nums = text.match(/[-+]?\\d+(?:\\.\\d+)?/g);
                                if (!nums || nums.length < 2) return;
                                var lat = parseFloat(nums[0]), lon = parseFloat(nums[1]);
                                if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
                                ev.preventDefault();
                                // Update native display directly (bypasses Vue/Quasar focus guard).
                                var nset = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                                nset.call(latEl, String(lat));
                                if (lonEl) nset.call(lonEl, String(lon));
                                // Notify Python via NiceGUI socket bridge.
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
                        """Reverse-geocode via Photon (address fields) + Overpass is_in
                        (enclosing named natural/protected areas). Returns Photon
                        properties dict or None on failure. Fills fields as a side effect."""
                        for _b in (_cntry_warn, _code_warn, _state_warn, _county_warn, _muni_warn):
                            _b.classes(add="hidden")
                        _locality_warn.classes(add="hidden")

                        async def _photon() -> list[dict]:
                            """Raises on failure so the outer try/except can notify."""
                            async with httpx.AsyncClient(timeout=10) as cl:
                                r = await cl.get(
                                    "https://photon.komoot.io/reverse",
                                    params={"lat": lat, "lon": lon, "lang": "en", "limit": 15},
                                    headers={"User-Agent": "EntomologicalCollection/1.0"},
                                )
                                r.raise_for_status()
                                return [f["properties"] for f in r.json().get("features", [])]

                        async def _overpass() -> list[dict]:
                            """Overpass is_in: enclosing named natural/protected/island areas.
                            Returns list of {name, kind} dicts where kind is
                            'island' | 'locality'. Best-effort — returns [] on failure."""
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
                            all_props, overpass_names = await asyncio.gather(
                                _photon(), _overpass()
                            )
                        except Exception as ex:
                            ui.notify(f"Reverse geocoding failed: {ex}", type="negative")
                            return None

                        if not all_props:
                            ui.notify("Reverse geocoding returned no results.", type="warning")
                            return None

                        p = all_props[0]
                        state["populating"] = True
                        country_in.value  = p.get("country", "")
                        code_in.value     = p.get("countrycode", "").upper()
                        state_in.value    = p.get("state", "")
                        county_in.value   = p.get("county", "")
                        muni_in.value     = p.get("city") or p.get("locality") or ""
                        photon_locality   = _pick_locality(all_props)
                        overpass_islands  = [r["name"] for r in overpass_names if r["kind"] == "island"]
                        overpass_locs     = [r["name"] for r in overpass_names if r["kind"] == "locality"]
                        # Prefer closest named natural feature (Photon); fall back to first
                        # enclosing area from Overpass if Photon finds nothing.
                        locality_in.value = photon_locality or (overpass_locs[0] if overpass_locs else "")
                        island_in.value   = overpass_islands[0] if overpass_islands else ""
                        state["populating"] = False
                        _on_event_field_edit()

                        # Collect all locality alternatives (Photon + Overpass, deduped, primary excluded).
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
                                        _on_event_field_edit()
                                        _locality_warn.classes(add="hidden")
                                    ui.menu_item(_n, on_click=_pick_alt)
                            _locality_tip.text = "Also nearby: " + ", ".join(_alt_names)
                            _locality_tip.update()
                            _locality_warn.classes(remove="hidden")

                        return p

                    async def _check_boundary_crossing(
                        lat: float, lon: float, radius_m: float,
                        photon_props: dict,
                        ok_icons: list | None = None,
                    ) -> bool:
                        """Check whether the uncertainty circle crosses admin boundaries.

                        Samples 4 cardinal points (N/E/S/W) on the circle perimeter,
                        reverse-geocodes them in parallel via a shared AsyncClient, and
                        compares with the centre.  4 parallel requests is within Photon's
                        per-IP concurrency limit; 8 simultaneous causes 503 errors for the
                        last requests, silently dropping some countries from the result.
                        """
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
                        centre["locality"] = locality_in.value  # already best-picked by _reverse_geocode
                        snapshots: list[dict] = [centre]

                        # 4 cardinal points: N (0°), E (90°), S (180°), W (270°).
                        perimeter_pts = []
                        for a in (0, 90, 180, 270):
                            la = lat + (radius_m / 111_320) * math.cos(math.radians(a))
                            lo = lon + (radius_m / (111_320 * math.cos(math.radians(lat)))) * math.sin(math.radians(a))
                            perimeter_pts.append((la, lo))

                        async def _photon_at(
                            cl: httpx.AsyncClient, la: float, lo: float
                        ) -> list[dict] | None:
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
                            results = await asyncio.gather(
                                *[_photon_at(cl, la, lo) for la, lo in perimeter_pts]
                            )

                        for p in results:
                            if not p:
                                continue
                            snap = _props_to_snap(p)
                            if snap != centre and snap not in snapshots:
                                snapshots.append(snap)

                        _any_warn: list[bool] = []
                        _ok_shown: list = []

                        def _show_warn(btn, tip, items_col, field_key, on_pick,
                                       ok_icon=None, label_fn=None):
                            """Show warning or ok icon depending on boundary ambiguity."""
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
                            tip_alts = [
                                label_fn(v, seen[v]) if label_fn else v for v in alts
                            ]
                            tip.text = "Circle also covers: " + ", ".join(tip_alts)
                            tip.update()
                            btn.classes(remove="hidden")
                            btn.update()
                            _any_warn.append(True)

                        async def _apply_snap(snap: dict) -> None:
                            """Fill address fields from a pre-geocoded perimeter snap."""
                            state["populating"] = True
                            country_in.value  = snap["country"]
                            code_in.value     = snap["code"]
                            state_in.value    = snap["state"]
                            county_in.value   = snap["county"]
                            muni_in.value     = snap["muni"]
                            locality_in.value = snap["locality"]
                            state["populating"] = False
                            for _b in (_cntry_warn, _code_warn, _state_warn,
                                       _county_warn, _muni_warn, _locality_warn):
                                _b.classes(add="hidden")
                            _on_event_field_edit()

                        icons = ok_icons or [None] * 6
                        _show_warn(_cntry_warn,    _cntry_tip,    _cntry_items,
                                   "country",  _apply_snap, ok_icon=icons[0])
                        _show_warn(_code_warn,     _code_tip,     _code_items,
                                   "code",     _apply_snap, ok_icon=icons[1],
                                   label_fn=lambda c, s: f"{c} ({s['country']})")
                        _show_warn(_state_warn,    _state_tip,    _state_items,
                                   "state",    _apply_snap, ok_icon=icons[2])
                        _show_warn(_county_warn,   _county_tip,   _county_items,
                                   "county",   _apply_snap, ok_icon=icons[3])
                        _show_warn(_muni_warn,     _muni_tip,     _muni_items,
                                   "muni",     _apply_snap, ok_icon=icons[4])
                        _show_warn(_locality_warn, _locality_tip, _locality_items,
                                   "locality", _apply_snap, ok_icon=icons[5],
                                   label_fn=lambda v, s: v if v else "(no named feature)")

                        if _ok_shown:
                            _fading = list(_ok_shown)
                            ui.timer(1.4, lambda: [
                                ok.classes(add="hidden", remove="lookup-ok-fade")
                                for ok in _fading
                            ], once=True)
                        return bool(_any_warn)

                    def _on_map_change(lat: float, lon: float, unc):
                        lat_in.value    = str(round(lat, 7))
                        lon_in.value    = str(round(lon, 7))
                        uncert_in.value = str(int(round(unc))) if unc else ""
                        _on_event_field_edit()
                        # Geocoding is triggered manually via the Lookup button, not here.
                        # Firing on every pin placement/drag caused Nominatim 429 errors.

                    _map = build_map_picker(_on_map_change,
                                           default_layer=get_config().map_default_layer)

                    with ui.row().classes("items-center gap-2 mt-2"):
                        def _open_map():
                            try:
                                lat = float(lat_in.value)
                                lon = float(lon_in.value)
                            except (TypeError, ValueError):
                                _map["open"]()
                                return
                            unc = None
                            try:
                                unc = float(uncert_in.value) if uncert_in.value else None
                            except ValueError:
                                pass
                            _map["fly_to"](lat, lon, unc)

                        (
                            ui.button("Map", icon="map", on_click=_open_map)
                            .props("flat dense size=sm")
                            .tooltip("Open map to pick coordinates")
                        )
                        def _clear_map_coords():
                            _map["clear"]()
                            lat_in.value    = ""
                            lon_in.value    = ""
                            uncert_in.value = ""
                            _on_event_field_edit()

                        (
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
                                await _check_boundary_crossing(
                                    lat, lon, unc, p,
                                    ok_icons=_geocode_ok_icons,
                                )
                            else:
                                for _ok in _geocode_ok_icons:
                                    _ok.classes(remove="hidden", add="lookup-ok-fade")
                                ui.timer(1.4, lambda: [
                                    _ok.classes(add="hidden", remove="lookup-ok-fade")
                                    for _ok in _geocode_ok_icons
                                ], once=True)

                        _lookup_btn = (
                            ui.button("Detect Locations from Coordinates", icon="auto_fix_high",
                                      on_click=_fill_from_coords)
                            .props("flat dense size=sm")
                            .tooltip("Fill country / state / county from coordinates via Photon")
                        )

                    ui.label("Location").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
                    with ui.grid(columns=5).classes("w-full gap-3 mt-1"):
                        country_in, _cntry_warn, _cntry_tip, _cntry_items, _cntry_ok = _geocode_input(
                            "country", on_change=_on_country_change)
                        code_in,    _code_warn,  _code_tip,  _code_items,  _code_ok  = _geocode_input(
                            "countryCode", on_change=_on_event_field_edit, placeholder="DE")
                        state_in,   _state_warn, _state_tip, _state_items, _state_ok = _geocode_input(
                            "stateProvince", on_change=_on_state_change)
                        county_in,  _county_warn, _county_tip, _county_items, _county_ok = _geocode_input(
                            "county", on_change=_on_county_change)
                        muni_in,    _muni_warn,  _muni_tip,  _muni_items,  _muni_ok  = _geocode_input(
                            "municipality", on_change=_on_muni_change)
                    _geocode_ok_icons = [_cntry_ok, _code_ok, _state_ok, _county_ok, _muni_ok]

                    with ui.grid(columns=3).classes("w-full gap-3 mt-3"):
                        locality_in, _locality_warn, _locality_tip, _locality_items, _locality_ok = _geocode_input(
                            "locality", on_change=_on_event_field_edit)
                        island_in    = ui.input("island", on_change=_on_event_field_edit).classes("col-span-1")
                        verblocal_in = ui.input("verbatimLocality", on_change=_on_event_field_edit).classes("col-span-1")
                    _geocode_ok_icons.append(_locality_ok)

                    ui.label("Date").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
                    with ui.grid(columns=3).classes("w-full gap-3 mt-1"):
                        edate_in    = ui.input("eventDate", placeholder="YYYY-MM-DD or YYYY-MM-DD/YYYY-MM-DD",
                                                on_change=_on_event_field_edit).classes("col-span-2")
                        verbdate_in = ui.input("verbatimEventDate", on_change=_on_event_field_edit).classes("col-span-1")

                    ui.label("Ecology").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
                    with ui.grid(columns=2).classes("w-full gap-3 mt-1"):
                        habitat_in   = ui.input("habitat",       on_change=_on_event_field_edit).classes("col-span-1")
                        protocol_sel = ui.select(SAMPLING_PROTOCOLS, label="samplingProtocol").classes("col-span-1")

                    ui.label("Recorded by").classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mt-4")
                    def _person_opts_now() -> dict:
                        import app.services.persons as _psvc
                        with _sf() as _s:
                            return _psvc.person_options(_s)

                    with ui.grid(columns=2).classes("w-full gap-3 mt-1"):
                        with ui.row().classes("col-span-1 items-center gap-1"):
                            recby_in = (
                                ui.select(
                                    options=_person_opts_now(),
                                    label="recordedBy",
                                    with_input=True,
                                    clearable=True,
                                )
                                .classes("flex-1")
                                .props("use-input input-debounce=0 new-value-mode=add-unique")
                            )
                            (
                                ui.button("", icon="push_pin")
                                .props("flat dense round size=xs")
                                .tooltip("Insert default name")
                                .on_click(lambda: recby_in.set_value(get_config().default_recorded_by))
                                .bind_visibility_from(recby_in, "value", lambda v: not v)
                            )
                        recby_in.on_value_change(lambda _: _on_event_field_edit())
                        fieldnum_in = ui.input("fieldNumber", on_change=_on_event_field_edit).classes("col-span-1")

                    import app.services.persons as _psvc

                    def _refresh_digitize_person_opts():
                        with _sf() as _s:
                            new_opts = _psvc.person_options(_s)
                        cur = recby_in.value
                        r_opts = dict(new_opts)
                        if cur and cur not in r_opts:
                            r_opts = {cur: cur, **r_opts}
                        recby_in.options = r_opts
                        det_state["refresh_person_opts"]()

                    _refreshers["person_opts"] = _refresh_digitize_person_opts
                    ui.timer(2.0, _refresh_digitize_person_opts)

                    verblabel_in = ui.input("verbatimLabel", on_change=_on_event_field_edit).classes("w-full mt-4")

                    def _on_event_selected(e):
                        eid = e.value
                        if eid is None:
                            state["event_id"] = None
                            event_status.set_text("· new event")
                            event_status.classes(remove="event-linked", add="event-new")
                            return
                        ev = _with_session(lambda s: svc.get_event(s, eid))
                        if ev is None:
                            return
                        state["populating"] = True
                        country_in.value   = ev.country            or ""
                        code_in.value      = ev.country_code       or ""
                        state_in.value     = ev.state_province     or ""
                        county_in.value    = ev.county             or ""
                        muni_in.value      = ev.municipality       or ""
                        island_in.value    = ev.island             or ""
                        locality_in.value  = ev.locality           or ""
                        verblocal_in.value = ev.verbatim_locality   or ""
                        edate_in.value     = ev.event_date         or ""
                        verbdate_in.value  = ev.verbatim_event_date or ""
                        recby_in.value     = ev.recorded_by        or ""
                        lat_in.value       = str(ev.decimal_latitude)  if ev.decimal_latitude  is not None else ""
                        lon_in.value       = str(ev.decimal_longitude) if ev.decimal_longitude is not None else ""
                        uncert_in.value    = str(ev.coordinate_uncertainty_in_meters) if ev.coordinate_uncertainty_in_meters is not None else ""
                        elev_min_in.value  = str(ev.minimum_elevation_in_meters)      if ev.minimum_elevation_in_meters      is not None else ""
                        elev_max_in.value  = str(ev.maximum_elevation_in_meters)      if ev.maximum_elevation_in_meters      is not None else ""
                        habitat_in.value   = ev.habitat            or ""
                        protocol_sel.value = ev.sampling_protocol  or ""
                        fieldnum_in.value  = ev.field_number       or ""
                        verblabel_in.value = ev.verbatim_label     or ""
                        state["populating"] = False
                        state["event_id"] = eid
                        event_status.set_text(f"· linked #{eid}: {ev.country or ''} {ev.state_province or ''}")
                        event_status.classes(remove="event-new", add="event-linked")

                    event_sel.on_value_change(_on_event_selected)

                # ── BIOLOGICAL ASSOCIATIONS ───────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    ui.label("Biological Associations").classes("section-label")
                    ui.separator().classes("mb-3")

                    # Relationship selector
                    rel_options_list = _with_session(get_relationship_options)
                    rel_sel = (
                        ui.select(
                            options={r.id: r.name for r in rel_options_list},
                            label="Relationship",
                            clearable=True,
                        )
                        .classes("w-full mb-3")
                        .tooltip("Select the type of biological association")
                    )
                    ui.timer(2.0, lambda: rel_sel.__setattr__("options", {r.id: r.name for r in _with_session(get_relationship_options)}))

                    # Object taxon search — bio_codes list is read on each keystroke
                    bio_obj_state = build_bio_object_search(_sf, bio_codes)

                    with ui.row().classes("items-center gap-3 mt-3"):
                        show_animals_cb = ui.checkbox(
                            "Show animals too",
                            value=False,
                        )

                        def _on_show_animals(e):
                            if e.value:
                                bio_codes.clear()  # empty = no nomenclatural code filter
                            else:
                                bio_codes.clear()
                                bio_codes.extend(get_config().bio_assoc_default_codes)
                            bio_obj_state["clear"]()

                        show_animals_cb.on_value_change(_on_show_animals)

                        ui.space()

                        def _add_assoc():
                            rel_id   = rel_sel.value
                            taxon_id = bio_obj_state["taxon_id"]
                            if not rel_id:
                                ui.notify("Select a relationship first.", type="warning")
                                return
                            if not taxon_id:
                                ui.notify("Select an associated taxon first.", type="warning")
                                return
                            if taxon_id == -1:
                                ui.notify("Taxon is still being imported — please wait a moment.", type="warning")
                                return
                            rel_name = rel_sel.options.get(rel_id, str(rel_id))
                            bio_state["associations"].append({
                                "rel_id":      rel_id,
                                "rel_name":    rel_name,
                                "taxon_id":    taxon_id,
                                "taxon_label": bio_obj_state["label"],
                            })
                            bio_obj_state["clear"]()
                            rel_sel.value = None
                            _refresh_assoc_list()

                        (
                            ui.button("Add association", icon="add", on_click=_add_assoc)
                            .props("flat color=secondary")
                        )

                    assoc_list_col = ui.column().classes("w-full gap-1 mt-3")

                    def _refresh_assoc_list():
                        assoc_list_col.clear()
                        with assoc_list_col:
                            if not bio_state["associations"]:
                                ui.label("No associations added — associations are saved atomically when the specimen is saved.") \
                                    .classes("text-sm italic") \
                                    .style("color:var(--tp-base-soft)")
                            for i, a in enumerate(bio_state["associations"]):
                                with ui.row().classes("items-center gap-2 w-full"):
                                    ui.icon("link", size="xs") \
                                        .style("color:var(--tp-secondary); opacity:.7")
                                    ui.label(f"{a['rel_name']} — {a['taxon_label']}") \
                                        .classes("text-sm flex-1")
                                    (
                                        ui.button("", icon="close")
                                        .props("flat dense round size=xs")
                                        .on_click(lambda _, idx=i: _remove_assoc(idx))
                                    )

                    def _remove_assoc(idx: int):
                        bio_state["associations"].pop(idx)
                        _refresh_assoc_list()

                    _refresh_assoc_list()

                # ── SAVE BAR ─────────────────────────────────────────────
                with ui.row().classes("w-full items-center gap-4 px-1"):
                    keep_event = ui.checkbox("Keep event")
                    keep_det   = ui.checkbox("Keep determination")
                    ui.space()
                    status_lbl = ui.label("").classes("text-sm italic").style("color:var(--tp-base-soft)")
                    save_btn   = ui.button("Save specimen", icon="save").classes("btn-save")

                # ── RECENT SPECIMENS ──────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-2 mb-1"):
                        ui.label("Recent Specimens").classes("section-label")
                        ui.space()
                        ui.button("", icon="refresh", on_click=lambda: _refresh_table()) \
                            .props("flat dense round").tooltip("Refresh")
                    table = ui.table(
                        columns=TABLE_COLS,
                        rows=_table_rows(),
                        row_key="id",
                        pagination={"rowsPerPage": 50, "sortBy": "id", "descending": True},
                    ).classes("w-full").props("dense flat")

                # ── save / clear logic ────────────────────────────────────

                def _collect_event_fields() -> dict:
                    return {
                        "country":                          country_in.value,
                        "country_code":                     code_in.value,
                        "state_province":                   state_in.value,
                        "county":                           county_in.value,
                        "municipality":                     muni_in.value,
                        "island":                           island_in.value,
                        "locality":                         locality_in.value,
                        "verbatim_locality":                verblocal_in.value,
                        "event_date":                       edate_in.value,
                        "verbatim_event_date":              verbdate_in.value,
                        "recorded_by":                      recby_in.value,
                        "decimal_latitude":                 lat_in.value,
                        "decimal_longitude":                lon_in.value,
                        "coordinate_uncertainty_in_meters": uncert_in.value,
                        "minimum_elevation_in_meters":      elev_min_in.value,
                        "maximum_elevation_in_meters":      elev_max_in.value,
                        "habitat":                          habitat_in.value,
                        "sampling_protocol":                protocol_sel.value,
                        "field_number":                     fieldnum_in.value,
                        "verbatim_label":                   verblabel_in.value,
                    }

                def _collect_specimen_fields() -> dict:
                    return {
                        "catalog_number":    cat_num.value or "",
                        "collection_code": DEFAULT_NAMESPACE,
                        "sex":               sex_sel.value,
                        "individual_count":  int(count_in.value or 1),
                        "preparations":      preps_in.value,
                        "life_stage":        stage_sel.value,
                        "type_status":       type_in.value,
                        "disposition":       disp_sel.value,
                        "basis_of_record":   basis_sel.value,
                        "occurrence_remarks":rem_in.value,
                    }

                def _validate() -> str | None:
                    if not det_state["get_dets"]():
                        return "Add at least one identification."
                    if not cat_num.value:
                        return "Select an identifier code first."
                    cc = code_in.value.strip()
                    if cc and len(cc) != 2:
                        return "countryCode must be exactly 2 characters (or empty)."
                    for label, val, lo, hi in [
                        ("latitude",  lat_in.value,  -90,  90),
                        ("longitude", lon_in.value, -180, 180),
                    ]:
                        if val:
                            try:
                                f = float(val)
                                if not (lo <= f <= hi):
                                    return f"{label} out of range [{lo}, {hi}]."
                            except ValueError:
                                return f"{label} must be a number."
                    if uncert_in.value:
                        try:
                            if float(uncert_in.value) < 0:
                                return "coordinateUncertainty must be ≥ 0."
                        except ValueError:
                            return "coordinateUncertainty must be a number."
                    return None

                def _clear_after_save():
                    cat_num.value   = None
                    rem_in.value    = ""
                    type_in.value   = ""
                    sex_sel.value   = ""
                    count_in.value  = 1
                    preps_in.value  = ""
                    stage_sel.value = ""
                    disp_sel.value  = "in collection"
                    basis_sel.value = "PreservedSpecimen"
                    # Clear bio associations
                    bio_state["associations"].clear()
                    bio_obj_state["clear"]()
                    rel_sel.value = None
                    _refresh_assoc_list()
                    if not keep_event.value:
                        event_sel.value = None
                        state["event_id"] = None
                        event_status.set_text("· new event")
                        event_status.classes(remove="event-linked", add="event-new")
                        for w in (country_in, code_in, state_in, county_in, muni_in,
                                  island_in, locality_in, verblocal_in, edate_in, verbdate_in,
                                  recby_in, lat_in, lon_in, uncert_in, elev_min_in,
                                  elev_max_in, habitat_in, fieldnum_in, verblabel_in):
                            w.value = ""
                        protocol_sel.value = ""
                    if not keep_det.value:
                        det_state["clear"]()

                def _on_save():
                    err = _validate()
                    if err:
                        ui.notify(err, type="negative")
                        return
                    try:
                        dets = det_state["get_dets"]()
                        cur_det  = next((d for d in dets if d["is_current"]), dets[0])
                        rest_det = [d for d in dets if d is not cur_det]
                        code = cat_num.value
                        with _sf() as session:
                            with session.begin():
                                co = svc.save_specimen_entry(
                                    session,
                                    taxon_id=cur_det["taxon_id"],
                                    event_id=state["event_id"],
                                    event_fields=_collect_event_fields(),
                                    specimen_fields=_collect_specimen_fields(),
                                    determination_fields={
                                        "identified_by":            cur_det["identified_by"],
                                        "date_identified":          cur_det["date_identified"],
                                        "identification_qualifier": cur_det["identification_qualifier"],
                                        "identification_remarks":   cur_det["identification_remarks"],
                                    },
                                )
                                for d in rest_det:
                                    svc.create_determination(
                                        session,
                                        collection_object_id=co.id,
                                        taxon_id=d["taxon_id"],
                                        identified_by=d["identified_by"],
                                        date_identified=d["date_identified"],
                                        identification_qualifier=d["identification_qualifier"],
                                        identification_remarks=d["identification_remarks"],
                                        is_current=0,
                                    )
                                id_svc.assign_code(session, code, co.id)
                                saved_id = co.id
                                pq_svc.enqueue_data(session, co.id)
                                pq_svc.enqueue_determination(session, co.id)
                                # Save biological associations atomically with the specimen
                                for assoc in bio_state["associations"]:
                                    save_biological_association(
                                        session,
                                        collection_object_id=co.id,
                                        biological_relationship_id=assoc["rel_id"],
                                        object_taxon_id=assoc["taxon_id"],
                                    )
                        event_sel.options = _event_opts()
                        cat_num.options = {c: c for c in _reserved_opts()}
                        cat_num.update()
                        ui.notify(f"Saved — specimen #{saved_id}  [{code}]", type="positive")
                        status_lbl.set_text(f"Last saved: #{saved_id}")
                    except Exception as exc:
                        ui.notify(f"Save failed: {exc}", type="negative")
                        return
                    _refresh_table()
                    _clear_after_save()
                    for fn in _refreshers.values():
                        fn()

                save_btn.on_click(_on_save)

                def _refresh_table():
                    table.rows = _table_rows()
                    table.update()


        # ================================================================
        # TAB: RECORDS
        # ================================================================
        with ui.tab_panel("records"):
            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):
                build_records_tab(
                    _sf,
                    on_saved=lambda: [fn() for fn in _refreshers.values()],
                )

        # ================================================================
        # TAB: IMPORT & ASSIGN
        # ================================================================
        with ui.tab_panel("import"):
            build_import_assign_tab(_sf, _refreshers)

        # ================================================================
        # TAB: TAXONOMY
        # ================================================================
        with ui.tab_panel("taxonomy"):
            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):

                # ── summary stats ────────────────────────────────────────
                stats = _with_session(tax_svc.get_stats)
                _tax_stat_labels: dict[str, object] = {}
                with ui.row().classes("w-full gap-4"):
                    for label, value in [
                        ("Accepted taxa", stats.total_accepted),
                        ("Species",       stats.total_species),
                        ("Specimens",     stats.total_specimens),
                    ]:
                        with ui.card().classes("shadow-sm px-5 py-3 flex-1 text-center"):
                            _tax_stat_labels[label] = ui.label(str(value)).style(
                                "font-size:1.8rem; font-weight:300; color:var(--tp-secondary);"
                            )
                            ui.label(label).classes("section-label mt-1")

                def _refresh_taxonomy_stats():
                    s = _with_session(tax_svc.get_stats)
                    _tax_stat_labels["Accepted taxa"].set_text(str(s.total_accepted))
                    _tax_stat_labels["Species"].set_text(str(s.total_species))
                    _tax_stat_labels["Specimens"].set_text(str(s.total_specimens))
                _refreshers["taxonomy_stats"] = _refresh_taxonomy_stats

                # ── nomenclatural code tabs + manage buttons ──────────────
                # Current code filter: None = all, "ICZN", "ICN", etc.
                _nomen_filter: dict = {"code": "ICZN"}

                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-0 w-full"):
                        # Nomenclatural code sub-tabs
                        _nomen_tabs = (
                            ui.tabs(value="ICZN")
                            .props("dense indicator-color=secondary align=left no-caps")
                            .style("flex:1; border-bottom:none;")
                        )
                        with _nomen_tabs:
                            ui.tab("ICZN", label="ICZN").classes("iczn-tab")
                            ui.tab("ICN",  label="🌿 ICN")
                            ui.tab("ALL",  label="All codes")

                        ui.space()

                        def _on_saved_taxon():
                            _refresh_taxonomy_stats()
                            _refresh_tree()

                        build_taxon_editor(_sf, _on_saved_taxon)

                # ── checklist card ───────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-2 mb-3"):
                        ui.label("Checklist").classes("section-label")

                    # Filter select — searchable across all rank levels
                    checklist_opts = _with_session(tax_svc.checklist_options)
                    filter_sel = (
                        ui.select(
                            options=checklist_opts,
                            with_input=True,
                            clearable=True,
                            label="Filter by taxon…",
                        )
                        .classes("w-full mb-4")
                        .tooltip("Type a name at any rank to filter the checklist")
                    )

                    tree_data = _with_session(
                        lambda s: tax_svc.build_taxonomy_tree(s, nomenclatural_code="ICZN")
                    )

                    _NODE_SLOT = r"""
                        <div style="display:flex; align-items:baseline; gap:7px; padding:2px 0 1px;">
                          <span v-if="props.node.synonym"
                                style="color:var(--tp-base-soft); font-size:.8rem;
                                       font-style:normal; margin-right:-2px;">=</span>
                          <span :class="'rank-' + props.node.rank">{{ props.node.name }}</span>
                          <span v-if="props.node.auth"
                                style="font-style:normal; font-size:.78rem;
                                       color:var(--tp-base-soft);">{{ props.node.auth }}</span>
                          <span v-if="props.node.spp_count > 0"
                                class="tax-stat-chip tax-stat-spp">
                            {{ props.node.spp_count }}&nbsp;spp.
                          </span>
                          <span v-if="props.node.spec_count > 0"
                                class="tax-stat-chip tax-stat-spec">
                            {{ props.node.spec_count }}&nbsp;spec.
                          </span>
                          <a v-if="props.node.tw_url"
                             :href="props.node.tw_url" target="_blank"
                             title="Open in TaxonPages" @click.stop
                             style="color:var(--tp-secondary); font-size:.72rem;
                                    text-decoration:none; opacity:.6; line-height:1; margin-left:2px;"
                             onmouseover="this.style.opacity='1'"
                             onmouseout="this.style.opacity='.6'">↗</a>
                        </div>
                    """

                    # Always create the tree widget so it can be updated after saves.
                    tax_tree = ui.tree(
                        nodes=tree_data,
                        label_key="label",
                        children_key="children",
                    ).classes("w-full").props("no-connectors dense")
                    tax_tree.add_slot("default-header", _NODE_SLOT)

                    async def _expand():
                        await asyncio.sleep(0.15)
                        await tax_tree.run_method("expandAll")

                    async def _on_filter_change(e):
                        key = e.value or ""
                        code = _nomen_filter["code"]
                        if not key:
                            new_nodes = _with_session(
                                lambda s: tax_svc.build_taxonomy_tree(s, nomenclatural_code=code)
                            )
                        else:
                            part = key.split(":", 1)
                            rank, val = part[0], part[1] if len(part) > 1 else ""
                            if rank in ("species", "taxon"):
                                new_nodes = _with_session(
                                    lambda s, v=val: tax_svc.build_taxonomy_tree(
                                        s, filter_id=int(v), nomenclatural_code=code
                                    )
                                )
                            else:
                                new_nodes = _with_session(
                                    lambda s, r=rank, v=val: tax_svc.build_taxonomy_tree(
                                        s, filter_rank=r, filter_value=v,
                                        nomenclatural_code=code
                                    )
                                )
                        tax_tree._props['nodes'] = new_nodes
                        tax_tree.update()
                        await _expand()

                    filter_sel.on_value_change(_on_filter_change)

                    async def _on_nomen_tab_change(e):
                        tab = e.value
                        _nomen_filter["code"] = None if tab == "ALL" else tab
                        filter_sel.value = None
                        code = _nomen_filter["code"]
                        new_nodes = _with_session(
                            lambda s: tax_svc.build_taxonomy_tree(s, nomenclatural_code=code)
                        )
                        tax_tree._props['nodes'] = new_nodes
                        tax_tree.update()
                        await _expand()

                    _nomen_tabs.on_value_change(_on_nomen_tab_change)

                    def _refresh_tree():
                        filter_sel.options = _with_session(tax_svc.checklist_options)
                        filter_sel.update()
                        code = _nomen_filter["code"]
                        tax_tree._props['nodes'] = _with_session(
                            lambda s: tax_svc.build_taxonomy_tree(s, nomenclatural_code=code)
                        )
                        tax_tree.update()

                    _refreshers["taxonomy_tree"] = _refresh_tree

        # ================================================================
        # TAB: CONTROLLED VOCABULARIES
        # ================================================================
        with ui.tab_panel("vocab"):
            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):
                build_controlled_vocab_tab(
                    _sf,
                    on_person_changed=lambda: _refreshers.get("person_opts") and _refreshers["person_opts"](),
                )

        # ================================================================
        # TAB: LABELS
        # ================================================================
        with ui.tab_panel("labels"):
            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-6"):

                # ── Print queue ──────────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-2 mb-2"):
                        ui.label("Print queue").classes("section-label")
                        ui.space()
                        queue_count_lbl = ui.label("").classes("text-sm") \
                            .style("color:var(--tp-base-soft)")
                        clear_btn  = ui.button("Clear", icon="delete_sweep").props("flat dense")
                        print_btn  = ui.button("Print all", icon="print").props("color=secondary")

                    preview_col = ui.column().classes("w-full gap-1")
                    # Plain-text edits keyed by print_queue.id; passed to build_pdf on print.
                    _label_overrides: dict[int, str] = {}

                    TYPE_ICON  = {"data": "place", "determination": "science", "identifier": "label"}
                    TYPE_COLOR = {"data": "blue-grey", "determination": "teal", "identifier": "secondary"}

                    def _refresh_queue():
                        summary = _with_session(pq_svc.queue_summary)
                        queue_count_lbl.set_text(
                            f"{summary.total} queued  "
                            f"({summary.n_data} data · "
                            f"{summary.n_determination} det · "
                            f"{summary.n_identifier} id)"
                            if summary.total else "empty"
                        )
                        items = _with_session(pq_svc.queue_preview_items)
                        preview_col.clear()
                        with preview_col:
                            if not items:
                                ui.label("Nothing queued yet — labels are added automatically "
                                         "when you save specimens or generate identifier codes.") \
                                  .classes("text-sm italic").style("color:var(--tp-base-soft)")
                            else:
                                for item in items:
                                    with ui.row().classes("items-center gap-2 w-full"):
                                        ui.icon(TYPE_ICON[item["type"]], size="xs") \
                                          .style("color:var(--tp-secondary); opacity:.7")
                                        if item["type"] == "data":
                                            # Editable field — persists user corrections
                                            # across refreshes via _label_overrides.
                                            init_val = _label_overrides.get(
                                                item["id"], item.get("label_text", item["text"])
                                            )
                                            inp = (
                                                ui.input(value=init_val)
                                                .classes("flex-1")
                                                .props("dense outlined")
                                                .style("font-size:0.75rem")
                                            )
                                            inp.on_value_change(
                                                lambda e, qid=item["id"]:
                                                    _label_overrides.__setitem__(qid, e.value)
                                            )
                                        else:
                                            ui.label(item["text"]).classes("text-sm flex-1")
                                        ui.button("", icon="close") \
                                          .props("flat dense round size=xs") \
                                          .on_click(lambda _, qid=item["id"]: _remove_item(qid))

                    def _remove_item(queue_id: int):
                        _label_overrides.pop(queue_id, None)
                        with _sf() as session:
                            with session.begin():
                                pq_svc.remove_item(session, queue_id)
                        _refresh_queue()

                    def _print_all():
                        summary = _with_session(pq_svc.queue_summary)
                        if summary.total == 0:
                            ui.notify("Queue is empty.", type="warning")
                            return
                        pdf = _with_session(
                            lambda s: pq_svc.build_pdf(s, dict(_label_overrides))
                        )
                        ui.download(pdf, filename="labels_queue.pdf",
                                    media_type="application/pdf")
                        with _sf() as session:
                            with session.begin():
                                pq_svc.clear_queue(session)
                        _label_overrides.clear()
                        _refresh_queue()
                        ui.notify("Labels downloaded — queue cleared.", type="positive")

                    def _clear_queue():
                        with _sf() as session:
                            with session.begin():
                                pq_svc.clear_queue(session)
                        _refresh_queue()

                    print_btn.on_click(_print_all)
                    clear_btn.on_click(_clear_queue)
                    _refresh_queue()
                    _refreshers["queue"] = _refresh_queue

                # ── Batch dashboard ──────────────────────────────────────
                stats = _with_session(id_svc.batch_stats)
                _batch_stat_labels: dict[str, object] = {}
                with ui.row().classes("w-full gap-4"):
                    for label, value in [
                        ("Batches",     stats.total_batches),
                        ("Total codes", stats.total_codes),
                        ("Assigned",    stats.total_assigned),
                        ("Staged",      stats.total_reserved),
                    ]:
                        with ui.card().classes("shadow-sm px-5 py-3 flex-1 text-center"):
                            _batch_stat_labels[label] = ui.label(str(value)).style(
                                "font-size:1.6rem; font-weight:300; color:var(--tp-secondary);"
                            )
                            ui.label(label).classes("section-label mt-1")

                _reserved_count_ref = [None]   # filled in by the reserved-codes card below

                def _refresh_batch_stats():
                    s = _with_session(id_svc.batch_stats)
                    _batch_stat_labels["Batches"].set_text(str(s.total_batches))
                    _batch_stat_labels["Total codes"].set_text(str(s.total_codes))
                    _batch_stat_labels["Assigned"].set_text(str(s.total_assigned))
                    _batch_stat_labels["Staged"].set_text(str(s.total_reserved))
                    if _reserved_count_ref[0]:
                        n = sum(b.n_reserved for b in _with_session(id_svc.all_batches_with_reserved))
                        _reserved_count_ref[0].set_text(f"{n} staged")
                    _refresh_batch_sel()
                _refreshers["batch_stats"] = _refresh_batch_stats

                # ── Mode A: identifier-only labels ───────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    ui.label("Identifier labels").classes("section-label mb-2")
                    ui.label(
                        "Pre-print blank identifier labels to pin onto undigitised "
                        "specimens. Each label carries a unique 4-character code and "
                        "QR code. Codes are reserved in the database immediately."
                    ).classes("text-sm mb-4").style("color:var(--tp-base-soft)")

                    with ui.row().classes("items-center gap-4"):
                        count_input = (
                            ui.number("Number of labels", value=20, min=1, max=500, step=1)
                            .classes("w-40")
                        )
                        id_status = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")

                    with ui.row().classes("mt-4 gap-3 items-end"):
                        gen_btn = ui.button("Generate & download", icon="download")

                    def _refresh_batch_sel():
                        batches = _with_session(id_svc.batches_with_reserved)
                        batch_sel.options = {
                            b.batch_id: f"{b.created_at[:16].replace('T', '  ')}  "
                                        f"({b.n_reserved} of {b.n_total} staged)"
                            for b in batches
                        }
                        batch_sel.update()

                    def _generate_id_labels():
                        n = int(count_input.value or 1)
                        with _sf() as session:
                            with session.begin():
                                batch_id, codes = id_svc.reserve_codes(session, n)
                                for lc in session.query(LabelCode).filter(LabelCode.batch_id == batch_id).all():
                                    pq_svc.enqueue_identifier(session, lc.id)
                        pdf = lbl_svc.identifier_sheet(codes)
                        ui.download(pdf, filename=f"identifiers_{codes[0]}-{codes[-1]}.pdf",
                                    media_type="application/pdf")
                        id_status.set_text(f"✓ {n} codes reserved and downloaded")
                        _refresh_batch_stats()
                        _refresh_queue()

                    gen_btn.on_click(_generate_id_labels)

                    ui.separator().classes("my-3")
                    ui.label("Reprint a batch").classes("text-sm font-medium")
                    with ui.row().classes("w-full gap-3 items-end"):
                        batch_sel = ui.select(
                            options={},
                            label="Select batch…",
                            clearable=True,
                        ).classes("flex-1")
                        reprint_btn = ui.button("Reprint", icon="print").props("flat")

                    def _reprint_batch():
                        bid = batch_sel.value
                        if not bid:
                            ui.notify("Select a batch first.", type="warning")
                            return
                        codes = _with_session(lambda s: id_svc.codes_for_batch(s, bid))
                        if not codes:
                            ui.notify("No reserved codes left in this batch.", type="warning")
                            return
                        pdf = lbl_svc.identifier_sheet(codes)
                        ui.download(pdf, filename=f"identifiers_reprint_batch{bid}.pdf",
                                    media_type="application/pdf")
                        id_status.set_text(f"✓ Reprinted {len(codes)} codes from batch {bid}")

                    reprint_btn.on_click(_reprint_batch)
                    _refresh_batch_sel()

                # ── Reserved codes viewer ────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("Reserved codes").classes("section-label")
                        ui.space()
                        reserved_count = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")
                        _reserved_count_ref[0] = reserved_count
                        show_btn = ui.button("Show", icon="visibility").props("flat dense")

                    codes_container = ui.element("div").classes("w-full")
                    codes_visible = {"open": False}

                    def _load_reserved():
                        batches = _with_session(id_svc.all_batches_with_reserved)
                        total = sum(b.n_reserved for b in batches)
                        reserved_count.set_text(f"{total} staged")
                        codes_container.clear()
                        if not batches:
                            with codes_container:
                                ui.label("No reserved codes.") \
                                  .classes("text-sm italic mt-2") \
                                  .style("color:var(--tp-base-soft)")
                            return
                        with codes_container:
                            for b in batches:
                                ts  = b.created_at[:16].replace("T", "  ")
                                note = "" if b.n_reserved == b.n_total \
                                       else f"  · {b.n_total - b.n_reserved} assigned"
                                with ui.column().classes("w-full mt-3 gap-1"):
                                    ui.label(f"{ts}  —  {b.n_reserved} staged{note}") \
                                      .classes("text-xs font-medium") \
                                      .style("color:var(--tp-base-soft)")
                                    codes = _with_session(
                                        lambda s, bid=b.batch_id: id_svc.codes_for_batch(s, bid)
                                    )
                                    with ui.row().classes("flex-wrap gap-1"):
                                        for c in codes:
                                            ui.badge(c).props("outline color=secondary")

                    def _toggle_reserved():
                        codes_visible["open"] = not codes_visible["open"]
                        if codes_visible["open"]:
                            _load_reserved()
                            show_btn.props("flat dense icon=visibility_off")
                            show_btn.set_text("Hide")
                        else:
                            codes_container.clear()
                            show_btn.props("flat dense icon=visibility")
                            show_btn.set_text("Show")

                    show_btn.on_click(_toggle_reserved)
                    # Show total count on load without revealing codes
                    def _init_count(s):
                        n = sum(b.n_reserved for b in id_svc.all_batches_with_reserved(s))
                        reserved_count.set_text(f"{n} staged")
                    _with_session(_init_count)

                # ── Mode B: occurrence labels from existing specimens ────
                with ui.card().classes("w-full shadow-sm"):
                    ui.label("Occurrence labels").classes("section-label mb-2")
                    ui.label(
                        "Generate full data labels for specimens already in the "
                        "database. Select one or more specimens, assign a new "
                        "identifier code to each, and download the label sheet."
                    ).classes("text-sm mb-4").style("color:var(--tp-base-soft)")

                    # Specimen picker — search by catalog number or determination
                    def _specimen_options() -> dict:
                        with _sf() as session:
                            rows = (
                                session.query(
                                    CollectionObject.id,
                                    CollectionObject.catalog_number,
                                    TaxonDetermination.taxon_id,
                                )
                                .outerjoin(
                                    TaxonDetermination,
                                    (TaxonDetermination.collection_object_id == CollectionObject.id)
                                    & (TaxonDetermination.is_current == 1),
                                )
                                .order_by(CollectionObject.id.desc())
                                .limit(500)
                                .all()
                            )
                        return {
                            row.id: f"#{row.id}  {row.catalog_number}"
                            for row in rows
                        }

                    occ_sel = (
                        ui.select(
                            options=_specimen_options(),
                            multiple=True,
                            with_input=True,
                            label="Select specimens…",
                            clearable=True,
                        )
                        .classes("w-full")
                        .props("use-chips")
                    )
                    ui.timer(2.0, lambda: occ_sel.__setattr__("options", _specimen_options()))
                    occ_status = ui.label("").classes("text-sm mt-2").style("color:var(--tp-base-soft)")

                    def _generate_occ_labels():
                        ids = occ_sel.value or []
                        if not ids:
                            occ_status.set_text("Select at least one specimen.")
                            return
                        with _sf() as session:
                            with session.begin():
                                rows: list[lbl_svc.OccurrenceLabel] = []
                                for co_id in ids:
                                    co = session.get(CollectionObject, co_id)
                                    if co is None:
                                        ui.notify(
                                            f"Specimen #{co_id} no longer exists in the database. "
                                            "Clear your selection and re-select.",
                                            type="negative",
                                        )
                                        return
                                    ev = co.collecting_event
                                    # Reserve + assign a fresh code
                                    _batch_id, codes = id_svc.reserve_codes(session, 1)
                                    code = codes[0]
                                    id_svc.assign_code(session, code, co_id)

                                    det = next(
                                        (d for d in co.determinations if d.is_current), None
                                    )
                                    taxon_label = None
                                    if det and det.taxon:
                                        from app.services.taxa import format_scientific_name
                                        taxon_label = format_scientific_name(det.taxon)

                                    assoc_names = [
                                        ba.object_taxon.scientific_name
                                        for ba in co.subject_associations
                                        if ba.object_taxon
                                    ]

                                    rows.append(lbl_svc.OccurrenceLabel(
                                        code=code,
                                        country=ev.country if ev else None,
                                        country_code=ev.country_code if ev else None,
                                        state_province=ev.state_province if ev else None,
                                        municipality=ev.municipality if ev else None,
                                        county=ev.county if ev else None,
                                        locality=ev.locality if ev else None,
                                        verbatim_locality=ev.verbatim_locality if ev else None,
                                        latitude=ev.decimal_latitude if ev else None,
                                        longitude=ev.decimal_longitude if ev else None,
                                        coordinate_uncertainty_m=ev.coordinate_uncertainty_in_meters if ev else None,
                                        elevation_min=ev.minimum_elevation_in_meters if ev else None,
                                        elevation_max=ev.maximum_elevation_in_meters if ev else None,
                                        event_date=ev.event_date if ev else None,
                                        recorded_by=ev.recorded_by if ev else None,
                                        habitat=ev.habitat if ev else None,
                                        taxon=taxon_label,
                                        associated_species=assoc_names or None,
                                    ))

                        pdf = lbl_svc.occurrence_sheet(rows)
                        first = rows[0].code
                        ui.download(pdf, filename=f"labels_{first}.pdf",
                                    media_type="application/pdf")
                        occ_status.set_text(
                            f"✓ {len(rows)} label(s) generated, codes assigned."
                        )

                    ui.button("Generate & download", icon="download") \
                        .classes("mt-4") \
                        .on_click(_generate_occ_labels)

    # Rebuild + expand the taxonomy tree whenever the user switches to that tab.
    async def _on_tab_change(e):
        if e.value == "taxonomy":
            _refresh_taxonomy_stats()
            _refresh_tree()
            await asyncio.sleep(0.15)
            await tax_tree.run_method("expandAll")
        elif e.value == "digitize":
            _refreshers["person_opts"]()

    main_tabs.on_value_change(_on_tab_change)

    # ── Settings dialog content ───────────────────────────────────────────
    # Filled here so bio_codes (defined earlier in index()) is in scope.
    _known_code_labels = {"ICN": "🌿 ICN", "ICZN": "ICZN", "ICNP": "ICNP", "ICVCN": "ICVCN"}
    _code_cbs: dict[str, object] = {}

    with settings_dialog:
        with ui.card().classes("min-w-96"):
            ui.label("Settings").classes("section-label mb-3")
            ui.separator().classes("mb-3")

            # ── TaxonWorks connection ────────────────────────────────────
            ui.label("TaxonWorks connection").classes("text-sm font-medium mb-1")
            cfg_now = get_config()
            tw_base_in = ui.input(
                "API base URL",
                value=cfg_now.tw_base,
                placeholder="https://sfg.taxonworks.org/api/v1",
            ).classes("w-full mt-1")
            tw_token_in = ui.input(
                "Project token",
                value=cfg_now.tw_token,
                password=True,
                password_toggle_button=True,
            ).classes("w-full mt-2")
            tp_base_in = ui.input(
                "TaxonPages base URL",
                value=cfg_now.taxonpages_base,
                placeholder="https://catalog.curculionoidea.org",
            ).classes("w-full mt-2")

            ui.separator().classes("my-3")

            # ── Map default layer ────────────────────────────────────────
            ui.label("Map default layer").classes("text-sm font-medium mb-1")
            _map_layer_opts = {
                "street":           "Street map",
                "satellite":        "Satellite",
                "satellite_labels": "Satellite + labels",
            }
            map_layer_sel = ui.select(
                _map_layer_opts,
                value=get_config().map_default_layer,
                label="Default tile layer",
            ).classes("w-full mt-1")

            ui.separator().classes("my-3")

            # ── Default names ─────────────────────────────────────────────
            ui.label("Default names").classes("text-sm font-medium mb-1")
            ui.label(
                "Inserted with one click in identifiedBy / recordedBy fields."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            import app.services.persons as _psvc_cfg
            with _sf() as _scfg:
                _cfg_person_opts = _psvc_cfg.person_options(_scfg)

            def _make_cfg_person_sel(label, current_val):
                opts = dict(_cfg_person_opts)
                if current_val and current_val not in opts:
                    opts = {current_val: current_val, **opts}
                return (
                    ui.select(opts, label=label, value=current_val or None,
                              with_input=True, clearable=True)
                    .classes("w-full mt-1")
                    .props("use-input input-debounce=0 new-value-mode=add-unique")
                )

            cfg_now_names = get_config()
            idby_default_in = _make_cfg_person_sel(
                "Default identifiedBy", cfg_now_names.default_identified_by
            )
            recby_default_in = _make_cfg_person_sel(
                "Default recordedBy", cfg_now_names.default_recorded_by
            )

            ui.separator().classes("my-3")

            # ── Bio-association default codes ────────────────────────────
            ui.label("Biological association default nomenclatural codes") \
                .classes("text-sm font-medium mb-1")
            ui.label(
                "The bio-association object search filters to these codes by default. "
                "Override per-session with the 'Show animals too' checkbox."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            cfg_now2 = get_config()
            for code, lbl in _known_code_labels.items():
                _code_cbs[code] = ui.checkbox(
                    lbl, value=code in cfg_now2.bio_assoc_default_codes
                )

            def _save_settings():
                selected = [c for c, cb in _code_cbs.items() if cb.value]
                if not selected:
                    ui.notify("Select at least one nomenclatural code.", type="warning")
                    return
                cfg = get_config()
                cfg.tw_base               = tw_base_in.value.strip() or cfg.tw_base
                cfg.tw_token              = tw_token_in.value.strip()
                cfg.taxonpages_base       = tp_base_in.value.strip() or cfg.taxonpages_base
                cfg.map_default_layer     = map_layer_sel.value or "street"
                cfg.default_identified_by = idby_default_in.value or ""
                cfg.default_recorded_by   = recby_default_in.value or ""
                cfg.bio_assoc_default_codes = selected
                save_config(cfg)
                # Propagate to active bio_codes filter in place
                bio_codes.clear()
                bio_codes.extend(selected)
                settings_dialog.close()
                ui.notify("Settings saved.", type="positive")

            with ui.row().classes("mt-4 gap-2 justify-end w-full"):
                ui.button("Cancel", on_click=settings_dialog.close).props("flat")
                ui.button("Save", on_click=_save_settings).props("color=secondary")

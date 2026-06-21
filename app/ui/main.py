"""Collection app — main UI.

Two tabs:
  • Specimen Digitization — entry form + recent-specimens table
  • Taxonomy             — checklist tree with species / specimen counts

All DB access goes through app.services — no ORM queries in this file.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime

from nicegui import ui

from app.database import get_engine, get_session_factory
import app.services as svc
import app.services.taxonomy as tax_svc
import app.services.identifiers as id_svc
import app.services.labels as lbl_svc
import app.services.print_queue as pq_svc
from app.config import get_config, save_config, printed_pdf_dir
import app.services.person_defaults as pd_svc
import app.services.events as ev_svc
from app.services.label_text import format_event_preview_html
from app.models import CollectionObject, CollectingEvent, TaxonDetermination, LabelCode
from app.ui.taxon_search import build_taxon_search
from app.ui.identification_list import build_identification_list
from app.ui.import_assign import build_import_assign_tab
from app.ui.controlled_vocab_tab import build_controlled_vocab_tab
from app.ui.map_picker import add_map_assets
from app.ui.taxon_editor import build_taxon_editor
from app.ui.person_field import build_person_field
from app.ui.records_tab import build_records_tab
from app.ui.mounting_session import build_mounting_session_section
from app.ui.specimen_form import build_specimen_form
from app.ui.collecting_event_form import build_collecting_event_form
from app.ui.event_reuse import build_event_share_banner
from app.services.biological import (
    sync_biological_relationships,
    get_relationship_options,
)
from app.services.validation import validate_event_fields

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Controlled-vocabulary lists live in app/vocab.py (single source of truth).

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
# Engine (module-level, created once)
# ---------------------------------------------------------------------------

_engine = get_engine()
_sf     = get_session_factory(_engine)


def _default_recby() -> str | None:
    with _sf() as s:
        return pd_svc.get_defaults(s)[1]

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

    # ── SVG favicon (vector, sharp at any size) ──────────────────────────
    ui.add_head_html(
        '<link rel="icon" type="image/svg+xml" href="/static/beetle_blue.svg">'
    )

    # ── Notification hover-pause ─────────────────────────────────────────
    # Global window.setTimeout wrapper: when a 1-30 s timer fires (notification
    # range), check document.querySelector('.q-notification:hover').  If a
    # notification is hovered, poll every 150 ms until it isn't, then fire
    # after an 800 ms grace period.  window.clearTimeout is also wrapped so
    # that Quasar's own early-dismiss path sets `cancelled = true` and stops
    # any in-progress poll loop.  Installed before Quasar loads — no timing
    # or DOM-body-null issue possible.
    ui.add_head_html("""
    <script>
    (function () {
      if (window._notifyHoverInit) return;
      window._notifyHoverInit = true;

      var _origST = window.setTimeout;
      var _origCT = window.clearTimeout;
      var _cancelMap = new Map();

      window.clearTimeout = function (id) {
        var cancel = _cancelMap.get(id);
        if (cancel) { cancel(); _cancelMap.delete(id); }
        return _origCT.call(window, id);
      };

      window.setTimeout = function (fn, delay) {
        if (typeof fn !== 'function' || !(delay >= 1000 && delay <= 30000)) {
          return _origST.apply(window, arguments);
        }
        var cancelled = false;
        function fire() { if (!cancelled) fn(); }
        function onFire(everHovered) {
          if (cancelled) return;
          var hovered = !!document.querySelector('.q-notification:hover');
          if (hovered) {
            _origST(function () { onFire(true); }, 150);
          } else if (everHovered) {
            _origST(fire, 800);
          } else {
            fire();
          }
        }
        var id = _origST(function () { onFire(false); }, delay);
        _cancelMap.set(id, function () { cancelled = true; });
        return id;
      };
    })();
    </script>""")

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
        /* Digitize mode accents (segmented toggle) — all ≥4.5:1 with white text,
           used in BOTH themes so the active segment's label stays readable. */
        --mode-standard:        rgb(3,105,161);   /* sky-700  */
        --mode-mounting:        rgb(180,83,9);    /* amber-800 */
        --mode-visiting:        rgb(15,118,110);  /* teal-700  */
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
        /* mode accents inherit the deep, white-text-safe :root set (they read
           fine as saturated fills on the dark surface). */
      }
      body              { background:var(--tp-base-background); color:var(--tp-base-content);
                          font-size:15px; }
      .app-header       { background:var(--tp-primary) !important;
                          color:var(--tp-primary-content) !important; }
      .app-header-row   { padding:.35rem 1.5rem; }
      .app-mode-row     { padding:.3rem 1.5rem;
                          background:var(--tp-base-foreground);
                          border-bottom:1px solid var(--tp-base-border); }
      /* ── Digitize mode segmented control ─────────────────────────────── */
      .seg-toggle       { display:inline-flex; align-items:stretch;
                          border:1px solid var(--tp-base-border);
                          border-radius:9px; overflow:hidden;
                          background:var(--tp-base-background); }
      .seg-btn          { display:inline-flex; align-items:center; gap:6px;
                          padding:5px 14px; font-size:.85rem; font-weight:500;
                          color:var(--tp-base-soft); cursor:pointer;
                          border-right:1px solid var(--tp-base-border);
                          transition:background .12s ease, color .12s ease;
                          user-select:none; white-space:nowrap; line-height:1.3; }
      .seg-btn:last-child { border-right:none; }
      .seg-btn:hover      { background:var(--tp-base-muted);
                            color:var(--tp-base-content); }
      .seg-btn .seg-ico   { font-size:1.15rem; }
      .seg-btn.active     { color:#fff; background:var(--seg-color); }
      .seg-btn.active:hover { color:#fff; background:var(--seg-color);
                              filter:brightness(1.05); }
      /* the active segment's left border should match its fill, not the grey */
      .seg-btn.active + .seg-btn { border-left:none; }
      .app-tabs         { background:var(--tp-base-foreground) !important;
                          border-bottom:1px solid var(--tp-base-border); }
      .app-tabs .q-tab  { color:var(--tp-base-soft) !important; font-size:.9rem; min-height:44px; }
      .app-tabs .q-tab--active      { color:var(--tp-secondary) !important; }
      .app-tabs .q-tabs__indicator  { background:var(--tp-secondary) !important; }
      .section-label    { font-size:.75rem; font-weight:700; letter-spacing:.1em;
                          text-transform:uppercase; color:var(--tp-base-soft); }
      .event-linked     { color:var(--tp-secondary); font-size:.875rem; font-style:italic; }
      .event-new        { color:var(--tp-base-soft);  font-size:.875rem; font-style:italic; }
      /* Quasar dense input / select — make field text and labels readable */
      .q-field__native,
      .q-field__input   { font-size:15px !important; }
      .q-field--dense .q-field__label,
      .q-field--dense .q-field__marginal { font-size:.8rem !important; }
      .q-item__label    { font-size:.9375rem; }
      .q-card           { border:1px solid var(--tp-base-border) !important;
                          background:var(--tp-base-foreground) !important; }
      .btn-save         { background:var(--tp-secondary) !important; color:#fff !important; }
      .btn-save:hover   { background:var(--tp-secondary-hover) !important; }
      .q-table thead tr th       { color:var(--tp-base-lighter); font-size:.8rem; }
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
      .rank-family    { font-size:1.05rem; font-weight:700;
                        text-transform:uppercase; letter-spacing:.06em; }
      .rank-subfamily { font-size:.95rem;  font-weight:600; }
      .rank-tribe     { font-size:.9rem;  font-weight:500; }
      .rank-subtribe  { font-size:.875rem; font-style:italic; }
      .rank-genus     { font-size:.9rem;  font-weight:700; font-style:italic; }
      .rank-subgenus  { font-size:.875rem; font-style:italic; }
      .rank-species     { font-size:.875rem; font-style:italic; }
      .rank-subspecies  { font-size:.875rem; font-style:italic; }
      .rank-variety     { font-size:.875rem; font-style:italic; }
      .rank-form        { font-size:.875rem; font-style:italic; }
      .rank-synonym   { font-size:.85rem; font-style:italic;
                        color:var(--tp-base-soft); }
      /* count chips */
      .tax-stat-chip  { display:inline-block; font-size:.7rem; font-weight:600;
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

    # ── header (two rows: title + tabs — both fixed via q-header) ──────────
    with ui.header().classes("app-header q-pa-none"):
        # Row 1: title + controls
        with ui.row().classes("app-header-row items-center gap-4 w-full"):
            ui.html('<span class="header-beetle"></span>')
            ui.label("Collection").style(
                "font-size:1.1rem; font-weight:300; letter-spacing:.12em;"
            )
            ui.space()
            (
                ui.button(icon="settings", on_click=lambda: _open_settings())
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
        # Row 2: tab bar (light background, always visible via q-header fixed)
        with ui.element("div").classes("app-tabs w-full"):
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
        # Row 3: Digitize mode — segmented control, only visible on Digitize tab.
        # Custom (not ui.toggle) so each segment gets its own accent colour + icon.
        _mode_defs = [
            ("standard", "Standard",                  "biotech",   "var(--mode-standard)"),
            ("mounting", "Mounting Session",          "grid_view", "var(--mode-mounting)"),
            ("visiting", "Digitize other Collection", "museum",    "var(--mode-visiting)"),
        ]
        _mode_state = {"value": "standard", "handler": None}
        _seg_btns: dict[str, object] = {}

        def _set_mode(val: str) -> None:
            if val == _mode_state["value"]:
                return
            _mode_state["value"] = val
            for v, b in _seg_btns.items():
                b.classes(add="active") if v == val else b.classes(remove="active")
            if _mode_state["handler"]:
                _mode_state["handler"](val)

        with ui.row().classes("app-mode-row w-full max-w-5xl mx-auto") as _mode_row:
            with ui.element("div").classes("seg-toggle"):
                for _val, _label, _icon, _color in _mode_defs:
                    _b = (
                        ui.element("div")
                        .classes("seg-btn" + (" active" if _val == "standard" else ""))
                        .style(f"--seg-color:{_color}")
                        .on("click", lambda _e, v=_val: _set_mode(v))
                    )
                    with _b:
                        ui.icon(_icon).classes("seg-ico")
                        ui.label(_label)
                    _seg_btns[_val] = _b
        _mode_row.bind_visibility_from(main_tabs, "value", lambda v: v == "digitize")

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
                        "catalog": id_svc.format_catalog_display(r.collection_code, r.catalog_number),
                        "species": r.scientific_name,
                        "sex":     r.sex or "",
                        "n":       str(r.individual_count if r.individual_count is not None else ""),
                        "country": r.country or "",
                        "locality":r.locality or "",
                        "date":    r.event_date or "",
                        "leg":     r.recorded_by or "",
                        "det":     f"det. {r.identified_by}" if r.identified_by else "",
                    }
                    for r in rows
                ]

            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):

                # ── SPECIMEN ─────────────────────────────────────────────
                # Shared specimen-field block (see app/ui/specimen_form.py).
                # Widgets are unpacked into locals so the save/validate/wipe
                # paths below reference them unchanged.
                spec = build_specimen_form(_sf, identifier_policy="standard")
                specimen_card  = spec["card"]
                # Visiting-collection variant: free-text identity, pure data
                # capture (no reserved code, no print queue). Hidden until the
                # mode toggle selects it; occupies the same slot as the standard
                # card (only one of the two is ever visible).
                spec_visiting = build_specimen_form(_sf, identifier_policy="visiting")
                spec_visiting["card"].set_visibility(False)
                # The save/validate/clear paths read from whichever form is active.
                _active_spec = [spec]

                # ── IDENTIFICATION ────────────────────────────────────────
                with ui.card().classes("w-full shadow-sm") as identification_card:
                    ui.label("Identifications").classes("section-label")
                    ui.separator().classes("mb-3")
                    det_state = build_identification_list(_sf)

                # ── COLLECTING EVENT ─────────────────────────────────────
                with ui.card().classes("w-full shadow-sm"):
                    with ui.row().classes("items-center gap-3 mb-1"):
                        ui.label("Collecting Event").classes("section-label")
                        event_status = ui.html("· new event").classes("event-new")

                    ui.separator().classes("mb-3")

                    event_sel = (
                        ui.select(options=_event_opts(), with_input=True,
                                   clearable=True, label="Search existing events…")
                        .classes("w-full mb-4")
                        .tooltip("Type any locality, date, or collector name")
                    )
                    ui.timer(2.0, lambda: event_sel.set_options(_event_opts()))

                    # Reuse banner (orange "shared by N" + Detach-&-copy); populated
                    # when an existing event is reused, cleared otherwise.
                    event_banner = ui.column().classes("w-full")

                    def _on_event_field_edit(_=None):
                        if not state["populating"] and state["event_id"] is not None:
                            state["event_id"] = None
                            event_status.set_content("· new event (edited)")
                            event_status.classes(remove="event-linked", add="event-new")

                    ce = build_collecting_event_form(
                        _sf,
                        default_recby_fn=_default_recby,
                        on_field_edit=_on_event_field_edit,
                    )

                    def _refresh_person_opts():
                        ce["recby_refresh"]()
                        det_state["refresh_person_opts"]()

                    _refreshers["person_opts"] = _refresh_person_opts

                    def _on_event_selected(e):
                        eid = e.value
                        if eid is None:
                            state["event_id"] = None
                            event_status.set_content("· new event")
                            event_status.classes(remove="event-linked", add="event-new")
                            ce["set_readonly"](False)
                            _hide_reuse_banner()
                            return
                        def _load_event(s):
                            ev = svc.get_event(s, eid)
                            if ev is None:
                                return None
                            # Snapshot everything inside the session; `ev` is detached
                            # after _with_session closes (lazy recorded_by_person would
                            # raise DetachedInstanceError). The widget's load() blanks
                            # None and stringifies numerics.
                            snapshot = {
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
                            preview = format_event_preview_html(ev)
                            n_shared = ev_svc.count_co_at_event(s, eid)
                            return snapshot, preview, n_shared

                        loaded = _with_session(_load_event)
                        if loaded is None:
                            return
                        snapshot, ev_preview, ev_n = loaded
                        state["event_n"] = ev_n
                        ce["load"](snapshot)
                        state["event_id"] = eid
                        event_status.set_content(ev_preview)
                        event_status.classes(remove="event-new", add="event-linked")
                        ce["set_readonly"](True)
                        _show_reuse_banner(eid, ev_n)

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
                    ui.timer(2.0, lambda: rel_sel.set_options({r.id: r.name for r in _with_session(get_relationship_options)}))

                    # Object taxon search — bio_codes list is read on each keystroke
                    bio_obj_state = build_taxon_search(
                        _sf,
                        nomenclatural_codes=bio_codes,
                        sources=("local", "taxonworks", "powo"),
                        placeholder="Type plant or fungus name…",
                    )

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

                # ── MOUNTING SESSION SECTION ─────────────────────────────
                # Built here so it appears below Collecting Event + Bio
                # Associations (the sections shared by both modes).
                # Hidden by default; mode toggle controls visibility.
                with ui.column().classes("w-full gap-4") as ms_section:
                    ms_state = build_mounting_session_section(
                        _sf,
                        collect_event_fields=lambda: ce["collect_fields"](),
                        commit_recby=lambda s: ce["commit"](s),
                        bio_state=bio_state,
                        on_saved=lambda: _ms_on_saved(),
                    )
                ms_section.set_visibility(False)

                # ── SAVE BAR ─────────────────────────────────────────────
                with ui.row().classes("w-full items-center gap-4 px-1") as std_save_row:
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

                # The collecting-event fields, registry, collect/clear, and the
                # editable/read-only toggle now live in build_collecting_event_form
                # (ce handle: collect_fields / reset / set_readonly). The tab keeps
                # the event-reuse chrome below.

                # ── Event reuse: read-only fields + Detach-&-copy ──────────────
                # A reused (existing) event is shown read-only; editing a shared
                # event is only possible in Records. "Detach & copy to edit" turns
                # the fields editable as a NEW event for this specimen (clears the
                # link, so save creates a fresh event — the copy).
                def _hide_reuse_banner():
                    event_banner.clear()

                def _detach_to_edit():
                    state["event_id"] = None
                    ce["set_readonly"](False)
                    _hide_reuse_banner()
                    event_status.set_content("· new event (editable copy)")
                    event_status.classes(remove="event-linked", add="event-new")

                def _show_reuse_banner(eid: int, n: int):
                    event_banner.clear()
                    shared = f" — shared by {n} specimens" if n > 1 else ""
                    msg = (f"Reusing event #{eid}{shared}. Fields are read-only; detach a "
                           f"copy to edit here, or edit the original in the Records tab.")
                    with event_banner:
                        build_event_share_banner(
                            message=msg,
                            button_label="Detach & copy to edit",
                            on_detach=_detach_to_edit,
                        )

                def _collect_specimen_fields() -> dict:
                    active = _active_spec[0]
                    ident = active["get_identifier_fields"]()
                    return {
                        "catalog_number":    ident["catalog_number"],
                        "collection_code":   ident["collection_code"],
                        "institution_code":  ident["institution_code"],
                        "individual_count":  int(active["count_in"].value or 1),
                        "preparations":      active["preps_in"].value,
                        "life_stage":        active["stage_sel"].value,
                        "disposition":       active["disp_sel"].value,
                        "basis_of_record":   active["basis_sel"].value,
                        "occurrence_remarks":active["rem_in"].value,
                    }

                def _validate() -> str | None:
                    active = _active_spec[0]
                    ident = active["get_identifier_fields"]()
                    if active["policy"] == "visiting":
                        if not ident["catalog_number"]:
                            return "Enter the specimen's catalogNumber (host number)."
                        if not ident["collection_code"]:
                            return "Enter the collectionCode (host collection namespace)."
                        if not ident["institution_code"]:
                            return "Enter the institutionCode (host institution)."
                    else:  # standard
                        if not ident["institution_code"]:
                            return "institutionCode is not configured. Open Settings to set it."
                        if not ident["collection_code"]:
                            return "collectionCode is not configured. Open Settings to set it."
                        if not ident["catalog_number"]:
                            return "Select an identifier code first."
                    if not det_state["get_dets"]():
                        return "Add at least one identification."
                    return validate_event_fields(ce["collect_fields"]())

                def _clear_after_save():
                    _active_spec[0]["reset"]()
                    # Clear bio associations
                    bio_state["associations"].clear()
                    bio_obj_state["clear"]()
                    rel_sel.value = None
                    _refresh_assoc_list()
                    if not keep_event.value:
                        event_sel.value = None
                        state["event_id"] = None
                        event_status.set_content("· new event")
                        event_status.classes(remove="event-linked", add="event-new")
                        ce["set_readonly"](False)
                        _hide_reuse_banner()
                        ce["reset"]()
                    if not keep_det.value:
                        det_state["clear"]()

                def _on_save():
                    err = _validate()
                    if err:
                        ui.notify(err, type="negative")
                        return
                    try:
                        active = _active_spec[0]
                        is_visiting = active["policy"] == "visiting"
                        dets = det_state["get_dets"]()
                        cur_det  = next((d for d in dets if d["is_current"]), dets[0])
                        rest_det = [d for d in dets if d is not cur_det]
                        code = active["get_identifier_fields"]()["catalog_number"]
                        with _sf() as session:
                            with session.begin():
                                recby_id = ce["commit"](session)
                                co = svc.save_specimen_entry(
                                    session,
                                    taxon_id=cur_det["taxon_id"],
                                    event_id=state["event_id"],
                                    event_fields={
                                        **ce["collect_fields"](),
                                        "recorded_by_id": recby_id,
                                    },
                                    specimen_fields=_collect_specimen_fields(),
                                    determination_fields={
                                        "sex":                      cur_det.get("sex"),
                                        "type_status":              cur_det.get("type_status"),
                                        "identified_by_id":         cur_det.get("identified_by_id"),
                                        "date_identified":          cur_det["date_identified"],
                                        "identification_qualifier": cur_det["identification_qualifier"],
                                        "identification_remarks":   cur_det["identification_remarks"],
                                        "verbatim_identification":  cur_det.get("verbatim_identification"),
                                    },
                                )
                                for d in rest_det:
                                    svc.create_determination(
                                        session,
                                        collection_object_id=co.id,
                                        taxon_id=d["taxon_id"],
                                        sex=d.get("sex"),
                                        type_status=d.get("type_status"),
                                        identified_by_id=d.get("identified_by_id"),
                                        date_identified=d["date_identified"],
                                        identification_qualifier=d["identification_qualifier"],
                                        identification_remarks=d["identification_remarks"],
                                        verbatim_identification=d.get("verbatim_identification"),
                                        is_current=0,
                                    )
                                saved_id = co.id
                                # Shared finalization seam (see finalize_specimen):
                                # Standard binds the reserved code but queues no
                                # labels — the identifier is pre-printed and pinned
                                # by hand, and the specimen carries its own data
                                # labels. Visiting passes code=None (foreign
                                # catalogNumber, no reserved code). Both still
                                # persist any bio associations atomically.
                                svc.finalize_specimen(
                                    session,
                                    collection_object_id=co.id,
                                    code=None if is_visiting else code,
                                    queue_labels=False,
                                    associations=bio_state["associations"],
                                )
                        event_sel.set_options(_event_opts())
                        spec["refresh_codes"]()
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

                def _ms_on_saved():
                    event_sel.set_options(_event_opts())
                    _refresh_table()
                    for fn in _refreshers.values():
                        fn()

                def _on_mode_toggle(mode):
                    is_ms       = mode == "mounting"
                    is_visiting = mode == "visiting"
                    is_standard = mode == "standard"
                    # Standard and Visiting share the identification card, event
                    # card, bio card and save bar; only the specimen card swaps.
                    # Mounting replaces the specimen + identification section with
                    # its own row table.
                    specimen_card.set_visibility(is_standard)
                    spec_visiting["card"].set_visibility(is_visiting)
                    identification_card.set_visibility(not is_ms)
                    ms_section.set_visibility(is_ms)
                    std_save_row.set_visibility(not is_ms)
                    _active_spec[0] = spec_visiting if is_visiting else spec
                    # Full wipe on every toggle to avoid unsaved state leaking
                    spec["reset"]()
                    spec_visiting["reset"]()
                    det_state["clear"]()
                    bio_state["associations"].clear()
                    bio_obj_state["clear"]()
                    rel_sel.value = None
                    _refresh_assoc_list()
                    event_sel.value = None
                    state["event_id"] = None
                    event_status.set_content("· new event")
                    event_status.classes(remove="event-linked", add="event-new")
                    ce["set_readonly"](False)
                    _hide_reuse_banner()
                    ce["reset"]()
                    ms_state["wipe"]()

                _mode_state["handler"] = _on_mode_toggle


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

                        def _check_consistency():
                            from app.services.taxa import verify_taxon_consistency
                            with _sf() as s:
                                issues = verify_taxon_consistency(s)
                            if not issues:
                                ui.notify("Taxonomy is consistent — no issues found.",
                                          type="positive")
                                return
                            dlg = ui.dialog()
                            with dlg, ui.card().classes("min-w-[480px] max-w-[680px]"):
                                ui.label(f"{len(issues)} consistency issue(s)") \
                                  .classes("section-label mb-2")
                                with ui.column().classes("w-full gap-1"):
                                    for it in issues:
                                        ui.label(f"• [{it['issue']}] {it['name']} — {it['detail']}") \
                                          .classes("text-xs").style("color:var(--tp-base-soft)")
                                with ui.row().classes("justify-end w-full mt-2"):
                                    ui.button("Close", on_click=dlg.close).props("flat")
                            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                            dlg.open()

                        ui.button("Check consistency", icon="fact_check") \
                          .props("flat dense").on_click(_check_consistency)

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
                        now = datetime.now()
                        stamp_human = now.strftime("%Y-%m-%d %H:%M")
                        stamp_file  = now.strftime("%Y%m%d-%H%M%S")
                        pdf = _with_session(
                            lambda s: pq_svc.build_pdf(s, dict(_label_overrides), stamp_human)
                        )
                        # Archive every printed sheet to disk for reprint/audit
                        # before clearing the queue.
                        archive = printed_pdf_dir() / f"labels_{stamp_file}.pdf"
                        archive.write_bytes(pdf)
                        ui.download(pdf, filename=archive.name,
                                    media_type="application/pdf")
                        with _sf() as session:
                            with session.begin():
                                pq_svc.clear_queue(session)
                        _label_overrides.clear()
                        _refresh_queue()
                        ui.notify(f"Labels downloaded — queue cleared. Saved {archive.name}",
                                  type="positive")

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
                        "specimens. Each label carries a unique sequential code "
                        "(e.g. JJPC-00001) and QR code. Codes are reserved in the "
                        "database immediately."
                    ).classes("text-sm mb-4").style("color:var(--tp-base-soft)")

                    with ui.row().classes("items-center gap-4"):
                        count_input = (
                            ui.number("Number of labels", value=20, min=1, max=500, step=1)
                            .classes("w-40")
                        )
                        id_status = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")

                    with ui.row().classes("mt-4 gap-3 items-end"):
                        gen_btn = ui.button("Generate", icon="queue")

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
                        coll_code = get_config().collection_code
                        with _sf() as session:
                            with session.begin():
                                batch_id, codes = id_svc.reserve_sequential_codes(session, coll_code, n)
                                # One print group for this reservation → prints
                                # under a "New identifiers" header on the sheet.
                                group_id = pq_svc.next_print_group_id(session)
                                for lc in session.query(LabelCode).filter(LabelCode.batch_id == batch_id).all():
                                    pq_svc.enqueue_identifier(
                                        session, lc.id,
                                        print_group_id=group_id,
                                        source=pq_svc.SOURCE_IDENTIFIERS,
                                    )
                        # Queue-only: printing happens solely via the Print queue
                        # tab. Emitting a PDF here too risked a double print (print
                        # now + print the queue later = duplicate identifier labels).
                        id_status.set_text(f"✓ {n} codes reserved and added to the print queue")
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

    # Rebuild + expand the taxonomy tree whenever the user switches to that tab.
    async def _on_tab_change(e):
        if e.value == "taxonomy":
            _refresh_taxonomy_stats()
            _refresh_tree()
            await asyncio.sleep(0.15)
            await tax_tree.run_method("expandAll")
        elif e.value == "digitize":
            refresh_persons = _refreshers.get("person_opts")
            if refresh_persons:
                refresh_persons()

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

            # ── Collection identity ──────────────────────────────────────
            ui.label("Collection identity").classes("text-sm font-medium mb-1")
            ui.label(
                "Written into every new record. Required before saving a specimen."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")
            cfg_now_id = get_config()
            institution_code_in = ui.input(
                "institutionCode",
                value=cfg_now_id.institution_code,
                placeholder="e.g. Jilg",
            ).classes("w-full mt-1")
            collection_code_in = ui.input(
                "collectionCode",
                value=cfg_now_id.collection_code,
                placeholder="e.g. Jilg",
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

            with _sf() as _s_init:
                _idby_init, _recby_init = pd_svc.get_defaults(_s_init)
            idby_state = build_person_field(
                _sf, "Default identifiedBy",
                initial_value=_idby_init,
                classes="w-full mt-1",
            )
            recby_state_cfg = build_person_field(
                _sf, "Default recordedBy",
                initial_value=_recby_init,
                classes="w-full mt-1",
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
                cfg.institution_code      = institution_code_in.value.strip()
                cfg.collection_code       = collection_code_in.value.strip()
                cfg.map_default_layer     = map_layer_sel.value or "street"
                with _sf() as _s:
                    with _s.begin():
                        idby_id = idby_state["commit"](_s)
                        recby_id = recby_state_cfg["commit"](_s)
                        pd_svc.set_defaults(
                            _s,
                            identified_by_id=idby_id,
                            recorded_by_id=recby_id,
                        )
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

    def _open_settings():
        cfg = get_config()
        tw_base_in.value        = cfg.tw_base
        tw_token_in.value       = cfg.tw_token
        tp_base_in.value        = cfg.taxonpages_base
        institution_code_in.value = cfg.institution_code
        collection_code_in.value  = cfg.collection_code
        map_layer_sel.value     = cfg.map_default_layer or "street"
        with _sf() as _s:
            _idby, _recby = pd_svc.get_defaults(_s)
        idby_state["set_value"](_idby)
        recby_state_cfg["set_value"](_recby)
        for code, cb in _code_cbs.items():
            cb.value = code in cfg.bio_assoc_default_codes
        settings_dialog.open()

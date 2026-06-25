"""Collection app — main UI.

Two tabs:
  • Specimen Digitization — entry form + recent-specimens table
  • Taxonomy             — checklist tree with species / specimen counts

All DB access goes through app.services — no ORM queries in this file.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime

from nicegui import ui, app

from app.database import get_engine, get_session_factory
import app.services as svc
import app.services.taxonomy as tax_svc
import app.services.identifiers as id_svc
import app.services.labels as lbl_svc
import app.services.print_queue as pq_svc
from app.config import get_config, save_config, printed_pdf_dir, media_dir

# Serve the managed media store so attached images/files render in the browser
# (range-request aware → also handles audio/video). Registered once at import.
app.add_media_files("/media", media_dir())
import app.services.person_defaults as pd_svc
import app.services.events as ev_svc
import app.services.db_safety as db_safety
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
from app.ui.explore import build_explore_panel
from app.ui.mounting_session import build_mounting_session_section
from app.ui.specimen_form import build_specimen_form
from app.ui.collecting_event_form import build_collecting_event_form
from app.ui.event_reuse import build_event_share_banner
from app.ui.media_panel import build_media_button
from app.ui.external_id_panel import build_external_id_button
from app.ui.life_stage_panel import build_life_stage_button
import app.services.media as media_svc
import app.services.external_ids as extid_svc
import app.services.life_stage as lifestage_svc
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

    # ── Digitize single-card stepper: chip styling + arrow-key nav ───────
    # The stepper header (.tp-stepper-bar) is shown only in single-card mode;
    # ←/→ move between cards, but only when the bar is visible and the user is
    # not typing in a field (so arrow keys still work inside inputs/selects).
    ui.add_head_html("""
    <style>
      .tp-step-chip {
        display:flex; align-items:center; gap:6px; cursor:pointer;
        padding:5px 12px; border-radius:999px; font-size:.85rem; font-weight:600;
        color:var(--tp-base-soft); background:var(--tp-base-foreground);
        border:1px solid var(--tp-base-border); user-select:none;
        transition:background .12s, color .12s, border-color .12s;
      }
      .tp-step-chip:hover { border-color:var(--tp-secondary);
                            color:var(--tp-base-content); }
      .tp-step-chip.active { background:var(--tp-secondary); color:#fff;
                             border-color:var(--tp-secondary); }
      .tp-step-num {
        display:inline-flex; align-items:center; justify-content:center;
        min-width:18px; height:18px; padding:0 2px; border-radius:50%;
        font-size:.72rem; background:rgba(0,0,0,.10);
      }
      .tp-step-chip.active .tp-step-num { background:rgba(255,255,255,.25); }
      .tp-step-sep { color:var(--tp-base-soft); opacity:.45; padding:0 2px; }
    </style>
    <script>
    document.addEventListener('keydown', function(e){
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      var a = document.activeElement;
      if (a) {
        var tag = (a.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select'
            || a.isContentEditable) return;
        if (a.closest && a.closest('.q-field, .q-select, .q-editor, .q-menu')) return;
      }
      var bar = document.querySelector('.tp-stepper-bar');
      if (!bar) return;
      var st = window.getComputedStyle(bar);
      if (st.display === 'none' || st.visibility === 'hidden') return;
      if (window.emitEvent) {
        e.preventDefault();
        emitEvent('tp-step-nav', e.key === 'ArrowRight' ? 1 : -1);
      }
    }, true);
    </script>""")

    # ── SVG favicon (vector, sharp at any size) ──────────────────────────
    ui.add_head_html(
        '<link rel="icon" type="image/svg+xml" href="/static/beetle_blue.svg">'
    )

    # ── Print-queue sheet preview (#37): styling + hover-highlight ───────
    # Hovering any data label highlights every data label with the same
    # identity (same collecting event AND biological associations — see
    # print_queue._data_identity), so identical labels are visible at a glance.
    ui.add_head_html("""
    <style>
      .pq-prev        { display:flex; flex-direction:column; gap:14px; }
      .pq-prev-src    { font-size:.7rem; font-weight:700; text-transform:uppercase;
                        letter-spacing:.08em; color:var(--tp-base-soft); margin-bottom:4px; }
      .pq-prev-cols   { display:flex; flex-wrap:wrap; gap:10px; }
      .pq-prev-col    { display:flex; flex-direction:column; gap:3px; width:190px; }
      .pq-prev-label  { border:1px solid var(--tp-base-border); border-radius:3px;
                        padding:3px 6px; font-size:.72rem; line-height:1.3;
                        background:var(--tp-base-foreground); overflow-wrap:anywhere; }
      .pq-prev-id     { font-family:monospace; color:var(--tp-base-lighter); }
      /* WYSIWYG label boxes print like the PDF: genus+species bold-italic,
         subgenus italic, associated species italic — driven by the rendered
         <strong>/<em> markup, NOT a blanket italic on the whole box (#45/#46). */
      .pq-prev-label em     { font-style:italic; }
      .pq-prev-label strong { font-weight:700; }
      .pq-prev-label[data-ident] { cursor:text; transition:background .1s, outline .1s; }
      .pq-prev-label[contenteditable]:focus { outline:2px solid var(--tp-secondary);
                        outline-offset:0; }
      .pq-ident-hl    { outline:2px solid var(--tp-secondary);
                        background:rgba(3,105,161,.10) !important; }
      .pq-prev-edited { background:#fff7ed; border-color:#f59e0b; }
      .dark .pq-prev-edited { background:rgba(245,158,11,.12); }
      /* the "open larger editor" affordance sits flush inside the label box */
      .pq-box-wrap    { position:relative; }
      .pq-box-toggle  { position:absolute; top:1px; right:1px; opacity:0;
                        transition:opacity .1s; }
      .pq-box-wrap:hover .pq-box-toggle { opacity:.5; }
      .pq-box-toggle:hover { opacity:1 !important; }
      /* larger label editor dialog: a readable WYSIWYG area + raw-HTML source */
      .pq-dlg-editor  { min-height:120px; border:1px solid var(--tp-base-border);
                        border-radius:4px; padding:10px 12px; font-size:1rem;
                        line-height:1.5; background:var(--tp-base-foreground);
                        outline:none; overflow-wrap:anywhere; }
      .pq-dlg-editor:focus { outline:2px solid var(--tp-secondary); }
      .pq-dlg-editor em     { font-style:italic; }
      .pq-dlg-editor strong { font-weight:700; }
      .pq-dlg-editor div    { min-height:1.5em; }
      .pq-dlg-source .q-field__native { font-family:monospace; font-size:.85rem;
                        line-height:1.45; min-height:120px; }
      .pq-prev-ctl    { justify-content:flex-end; opacity:.55; }
      .pq-prev-ctl:hover { opacity:1; }
      .pq-prev-empty  { font-size:.85rem; font-style:italic; color:var(--tp-base-soft); }
    </style>
    <script>
    (function(){
      if (window._pqPrevHover) return;
      window._pqPrevHover = true;
      function hl(e, on){
        var el = e.target.closest && e.target.closest('.pq-prev-label[data-ident]');
        if(!el) return;
        var id = el.getAttribute('data-ident');
        document.querySelectorAll('.pq-prev-label[data-ident="'+CSS.escape(id)+'"]')
          .forEach(function(x){ x.classList.toggle('pq-ident-hl', on); });
      }
      document.addEventListener('mouseover', function(e){ hl(e, true); });
      document.addEventListener('mouseout',  function(e){ hl(e, false); });
      // Capture-phase blur (blur does not bubble): when a contenteditable label
      // box loses focus, emit its row id + innerHTML to Python. The DOM node
      // can't ride NiceGUI's normal event args, so we read innerHTML here.
      document.addEventListener('blur', function(e){
        var el = e.target;
        if(el && el.matches && el.matches('.pq-prev-label[contenteditable][data-qid]')){
          emitEvent('pq_edit', { qid: el.getAttribute('data-qid'), html: el.innerHTML });
        }
      }, true);
    })();
    </script>""")

    # ── Unsaved-changes guard (beforeunload) ─────────────────────────────
    # Each data-entry tab pushes its dirty scope here from a Python ui.timer that
    # reads the form's real field VALUES (window.tpSetScope), and warns before a
    # real page close/reload while any scope is set. In-app tab switches keep the
    # SPA alive (form state survives them) so they never trigger this. Python also
    # clears a scope at every deliberate reset (save / mode switch) via
    # window.tpClearDirty().
    ui.add_head_html("""
    <style>
      #tp-unsaved-banner {
        display:none; position:fixed; bottom:16px; left:50%;
        transform:translateX(-50%); z-index:9999;
        background:#b45309; color:#fff; padding:7px 18px; border-radius:9px;
        font-size:.82rem; font-weight:600; letter-spacing:.01em;
        box-shadow:0 2px 10px rgba(0,0,0,.28);
      }
    </style>
    <script>
    (function(){
      if (window._tpDirtyInit) return;
      window._tpDirtyInit = true;
      // Track WHICH areas have unsaved edits (each tab pushes its own scope label
      // via tpSetScope). The banner names them so the user knows where to go.
      var dirty = new Set();
      window._tpDirty = false;
      function banner(){
        var b = document.getElementById('tp-unsaved-banner');
        if(!b){
          b = document.createElement('div');
          b.id = 'tp-unsaved-banner';
          (document.body || document.documentElement).appendChild(b);
        }
        return b;
      }
      function render(){
        var b = banner();
        window._tpDirty = dirty.size > 0;
        if(dirty.size === 0){ b.style.display = 'none'; return; }
        b.textContent = '\\u26A0  Unsaved changes in: ' + Array.from(dirty).join(', ');
        b.style.display = 'block';
      }
      // tpClearDirty(label) clears one area; tpClearDirty() clears all.
      window.tpClearDirty = function(label){
        if(label){ dirty.delete(label); } else { dirty.clear(); }
        render();
      };
      // Authoritative, state-based setter pushed from Python: every data-entry
      // tab (Digitize, Records, Import & Assign) runs a ui.timer that reads the
      // real field VALUES via has_content() and pushes the scope here, so
      // programmatic fills — map picker, Tier-2 push-pins, reverse-geocode — are
      // detected too, not just typed input (#41, #47). There is deliberately no
      // DOM input/change listener anymore.
      window.tpSetScope = function(label, on){
        if(on){ dirty.add(label); } else { dirty.delete(label); }
        render();
      };
      window.addEventListener('beforeunload', function(e){
        if (window._tpDirty){ e.preventDefault(); e.returnValue = ''; return ''; }
      });
    })();
    </script>""")

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
      /* rank-based typography — mirrors published catalogues (e.g. CCPCC) */
      .tax-rank       { font-size:.58rem; font-weight:600; text-transform:uppercase;
                        letter-spacing:.07em; color:var(--tp-base-soft);
                        margin-right:3px; align-self:center; }
      .rank-superfamily { font-size:1.15rem; font-weight:700;
                        text-transform:uppercase; letter-spacing:.05em; }
      .rank-family    { font-size:1.35rem; font-weight:800;
                        text-transform:uppercase; letter-spacing:.03em; }
      .rank-subfamily { font-size:1.12rem; font-weight:700; }
      .rank-tribe     { font-size:1.0rem;  font-weight:600; }
      .rank-subtribe  { font-size:.92rem; font-weight:600; }
      .rank-genus     { font-size:1.05rem; font-weight:700; font-style:italic; }
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
                    ui.tab("explore",  label="Explore",               icon="travel_explore")
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
        # has_content: aggregate "does the Digitize form hold unsaved data?",
        # set after the tab content is built (see _mode_state["has_content"] = …).
        _mode_state = {"value": "standard", "handler": None, "has_content": None}
        _seg_btns: dict[str, object] = {}

        def _set_mode(val: str) -> None:
            if val == _mode_state["value"]:
                return
            _mode_state["value"] = val
            for v, b in _seg_btns.items():
                b.classes(add="active") if v == val else b.classes(remove="active")
            if _mode_state["handler"]:
                _mode_state["handler"](val)

        async def _request_mode(val: str) -> None:
            """Switch Digitize mode, confirming first if the form holds unsaved
            data. A mode switch wipes every card (see _on_mode_toggle), so the
            discard must be explicit — but only when there is something to lose."""
            if val == _mode_state["value"]:
                return
            hc = _mode_state["has_content"]
            if hc and hc():
                with ui.dialog() as dlg, ui.card():
                    ui.label("Discard unsaved data?").classes("text-lg font-medium")
                    ui.label(
                        "Switching mode clears the current form. Anything you have "
                        "entered and not saved will be lost."
                    ).classes("text-sm").style("color:var(--tp-base-soft)")
                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat")
                        ui.button("Discard & switch", on_click=lambda: dlg.submit(True)) \
                            .props("color=negative")
                proceed = await dlg
                dlg.delete()   # per-action dialog — delete to avoid a timer leak
                if not proceed:
                    return
            _set_mode(val)

        with ui.row().classes("app-mode-row w-full max-w-5xl mx-auto") as _mode_row:
            with ui.element("div").classes("seg-toggle"):
                for _val, _label, _icon, _color in _mode_defs:
                    _b = (
                        ui.element("div")
                        .classes("seg-btn" + (" active" if _val == "standard" else ""))
                        .style(f"--seg-color:{_color}")
                        .on("click", lambda _e, v=_val: _request_mode(v))
                    )
                    with _b:
                        ui.icon(_icon).classes("seg-ico")
                        ui.label(_label)
                    _seg_btns[_val] = _b
        _mode_row.bind_visibility_from(main_tabs, "value", lambda v: v == "digitize")

    # ── DB integrity banner ──────────────────────────────────────────────
    # Surfaced loudly when the startup PRAGMA integrity_check (run in run.py
    # before serving) reported a damaged file. Refuse to let the user keep
    # working quietly on a corrupt DB (CLAUDE.md §2: loud failure > silent
    # wrong value). Committed data is otherwise WAL-durable; this is the rare
    # file-corruption case the launch snapshot exists to recover from.
    _dbsafe = db_safety.LAST_RESULT
    if not _dbsafe.ok:
        with ui.element("div").classes("w-full").style(
            "background:#7f1d1d; color:#fff; padding:.6rem 1.5rem;"
        ):
            with ui.row().classes("items-center gap-3 w-full max-w-5xl mx-auto"):
                ui.icon("error", size="sm")
                _snap = (
                    f" A snapshot from before this launch is in data/snapshots/"
                    f" ({_dbsafe.snapshot_path.name})." if _dbsafe.snapshot_path else ""
                )
                ui.label(
                    "Database integrity check FAILED — do not keep working on this "
                    "file. Restore from a backup snapshot before continuing."
                    + _snap
                ).classes("text-sm font-medium")

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

    import json as _json

    def _mark_form_clean(scope: str | None = None):
        """Clear the client-side unsaved-changes flag for one area (or all when
        scope is None). Called after every deliberate reset — successful save,
        mode switch — so the banner / close-warning only flag genuinely unsaved
        edits. `scope` must match a panel's data-dirty-label."""
        arg = _json.dumps(scope) if scope else ""
        ui.run_javascript(f"window.tpClearDirty && window.tpClearDirty({arg})")

    # ── tab panels ───────────────────────────────────────────────────────
    with ui.tab_panels(main_tabs, value="digitize").classes("w-full"):

        # ================================================================
        # TAB: SPECIMEN DIGITIZATION
        # ================================================================
        # Digitize's unsaved-state is detected from the actual field VALUES via
        # _has_any_content() pushed by a ui.timer (see below), so programmatic fills
        # (map picker, push-pins, geocode) count — event-based detection would miss
        # them. Records & Import use the same value-based pattern (#47).
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

            # max-w is set by _apply_digitize_layout (max-w-7xl normal /
            # max-w-4xl single-card); never hard-coded here.
            with ui.column().classes("w-full mx-auto px-4 pt-6 pb-16 gap-4") \
                    as dig_container:

                # ── STEP HEADER (single-card mode only) ──────────────────
                # Clickable step chips; built + highlighted by the layout logic
                # block below. Hidden entirely in normal multi-card layout.
                step_header_row = ui.row().classes(
                    "tp-stepper-bar w-full items-center gap-1 mb-1"
                )
                step_header_row.set_visibility(False)

                # ── SPECIMEN + IDENTIFICATION (paired in normal mode) ─────
                # Normal mode lays these two short cards side-by-side (flex-wrap
                # → stack when narrow); single-card mode shows only the current
                # step's card, so the row simply holds one full-width card.
                # Staged specimen-media controllers (one per specimen card; the bytes
                # are stored now and committed to the new specimen on Save). Each card
                # has its own button in its header; a mode switch wipes both.
                spec_media = {}
                spec_media_v = {}
                spec_extid = {}
                spec_extid_v = {}
                spec_ls = {}
                spec_ls_v = {}

                def _mk_spec_footer(media_holder, extid_holder, ls_holder):
                    # Staged controllers, rendered bottom-right of the specimen card.
                    ls_holder.update(build_life_stage_button(_sf, staged=True))
                    extid_holder.update(build_external_id_button(
                        _sf, target_kind="collection_object", staged=True,
                        tooltip="Specimen resource identifiers (attached on Save)"))
                    media_holder.update(build_media_button(
                        _sf, target_kind="collection_object", staged=True,
                        tooltip="Specimen media (attached on Save)"))

                with ui.row().classes("w-full flex-wrap gap-4 items-start"):
                    # Shared specimen-field block (see app/ui/specimen_form.py).
                    # Widgets are unpacked into locals so the save/validate/wipe
                    # paths below reference them unchanged.
                    spec = build_specimen_form(
                        _sf, identifier_policy="standard",
                        footer_slot=lambda: _mk_spec_footer(spec_media, spec_extid, spec_ls))
                    specimen_card = spec["card"]
                    specimen_card.classes(remove="w-full", add="flex-1 min-w-[360px]")
                    # Visiting-collection variant: free-text identity, pure data
                    # capture (no reserved code, no print queue). Hidden until the
                    # mode toggle selects it; occupies the same slot as the standard
                    # card (only one of the two is ever visible).
                    spec_visiting = build_specimen_form(
                        _sf, identifier_policy="visiting",
                        footer_slot=lambda: _mk_spec_footer(spec_media_v, spec_extid_v, spec_ls_v))
                    spec_visiting["card"].set_visibility(False)
                    spec_visiting["card"].classes(remove="w-full",
                                                  add="flex-1 min-w-[360px]")
                    # The save/validate/clear paths read from whichever form is active.
                    _active_spec = [spec]

                    # ── IDENTIFICATION ────────────────────────────────────
                    with ui.card().classes("shadow-sm flex-1 min-w-[360px]") \
                            as identification_card:
                        with ui.row().classes("items-center gap-2 mb-1 w-full"):
                            ui.label("Identifications").classes("section-label")
                            ui.space()
                            ui.button("Clear", icon="clear",
                                      on_click=lambda: det_state["clear"]()) \
                                .props("flat dense no-caps size=sm color=grey") \
                                .tooltip("Clear unsaved identifications")
                        ui.separator().classes("mb-3")
                        det_state = build_identification_list(_sf)

                def _active_media() -> dict:
                    """The staged media controller for the active specimen card."""
                    return spec_media_v if _active_spec[0] is spec_visiting else spec_media

                def _active_extid() -> dict:
                    """The staged external-id controller for the active specimen card."""
                    return spec_extid_v if _active_spec[0] is spec_visiting else spec_extid

                def _active_ls() -> dict:
                    """The staged life-stage controller for the active specimen card."""
                    return spec_ls_v if _active_spec[0] is spec_visiting else spec_ls

                # ── COLLECTING EVENT ─────────────────────────────────────
                with ui.card().classes("w-full shadow-sm") as event_card:
                    with ui.row().classes("items-center gap-3 mb-1 w-full"):
                        ui.label("Collecting Event").classes("section-label")
                        event_status = ui.html("· new event").classes("event-new")
                        ui.space()
                        ui.button("Clear", icon="clear",
                                  on_click=lambda: _clear_event_card()) \
                            .props("flat dense no-caps size=sm color=grey") \
                            .tooltip("Clear the event selection and fields")

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
                                "country":                          ev.country_obj.name if ev.country_obj else None,
                                "country_code":                     ev.country_code,
                                "state_province":                   ev.state_province_obj.name if ev.state_province_obj else None,
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

                    # Event media (staged; committed to the event on Save)
                    with ui.row().classes("w-full justify-end mt-2"):
                        event_media = build_media_button(
                            _sf, target_kind="collecting_event", staged=True,
                            tooltip="Event media (attached on Save)")

                # ── BIOLOGICAL ASSOCIATIONS ───────────────────────────────
                with ui.card().classes("w-full shadow-sm") as bio_card:
                    with ui.row().classes("items-center gap-2 mb-1 w-full"):
                        ui.label("Biological Associations").classes("section-label")
                        ui.space()
                        ui.button("Clear", icon="clear",
                                  on_click=lambda: _clear_bio_card()) \
                            .props("flat dense no-caps size=sm color=grey") \
                            .tooltip("Clear staged associations")
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
                                # Per-association staged media + external links; persist
                                # across list re-renders (passed as staged_store) and are
                                # committed to the new association id on Save.
                                "media_items": [],
                                "extid_items": [],
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
                                    # Per-association staged external link + media
                                    # (committed on Save).
                                    a.setdefault("media_items", [])
                                    a.setdefault("extid_items", [])
                                    build_external_id_button(
                                        _sf, target_kind="biological_association",
                                        staged=True, staged_store=a["extid_items"],
                                        tooltip="Other party (resource identifier, on Save)")
                                    build_media_button(
                                        _sf, target_kind="biological_association",
                                        staged=True, staged_store=a["media_items"],
                                        tooltip="Association media (attached on Save)")
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
                        commit_event=lambda s: ce["commit"](s),
                        bio_state=bio_state,
                        on_saved=lambda: _ms_on_saved(),
                        event_id_getter=lambda: state["event_id"],
                    )
                ms_section.set_visibility(False)

                # ── SAVE BAR ─────────────────────────────────────────────
                with ui.row().classes("w-full items-center gap-4 px-1") as std_save_row:
                    keep_event = ui.checkbox("Keep event")
                    keep_det   = ui.checkbox("Keep determination")
                    ui.space()
                    status_lbl = ui.label("").classes("text-sm italic").style("color:var(--tp-base-soft)")
                    save_btn   = ui.button("Save specimen", icon="save").classes("btn-save")

                # ── STEP NAV (single-card mode, non-final steps) ─────────
                # Back / Next between cards; shown only in single-card mode and
                # only before the last step (the last step shows the save bar
                # above, whose Save performs the single real commit). Buttons are
                # wired in the layout-logic block below.
                with ui.row().classes("w-full items-center gap-3 px-1") as step_nav_row:
                    back_btn = ui.button("Back", icon="chevron_left").props("flat")
                    ui.space()
                    next_btn = ui.button("Next") \
                        .props("icon-right=chevron_right").classes("btn-save")
                step_nav_row.set_visibility(False)

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
                            actions=[{
                                "label": "Detach & copy to edit",
                                "icon": "fork_right",
                                "on_click": _detach_to_edit,
                                "primary": True,
                            }],
                        )

                def _collect_specimen_fields(session) -> dict:
                    # session: needed to resolve the preparations controlled-vocab
                    # name → preparation_id (get_or_create), like the person fields.
                    active = _active_spec[0]
                    ident = active["get_identifier_fields"]()
                    return {
                        "catalog_number":    ident["catalog_number"],
                        "collection_code":   ident["collection_code"],
                        "institution_code":  ident["institution_code"],
                        "individual_count":  int(active["count_in"].value or 1),
                        "preparation_id":    active["prep_field"]["commit"](session),
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

                # Per-card "Clear" handlers (header buttons). Each resets only its
                # own card's uncommitted fields — used to discard a typo or a
                # wrong pick without touching the other cards or saving.
                def _clear_event_card():
                    event_sel.value = None
                    state["event_id"] = None
                    event_status.set_content("· new event")
                    event_status.classes(remove="event-linked", add="event-new")
                    ce["set_readonly"](False)
                    _hide_reuse_banner()
                    ce["reset"]()

                def _clear_bio_card():
                    bio_state["associations"].clear()
                    bio_obj_state["clear"]()
                    rel_sel.value = None
                    _refresh_assoc_list()

                def _has_any_content() -> bool:
                    """Aggregate: does the Digitize form hold unsaved data in any
                    card? Drives the mode-switch confirm. Checks the active mode's
                    specimen surface (standard/visiting card or the mounting table)
                    plus the shared identification, event and bio cards."""
                    mode = _mode_state["value"]
                    if mode == "mounting":
                        spec_dirty = ms_state["has_content"]()
                    else:
                        spec_dirty = _active_spec[0]["has_content"]()
                    return (
                        spec_dirty
                        or det_state["has_content"]()
                        or ce["has_content"]()
                        or state.get("event_id") is not None
                        or bool(bio_state["associations"])
                        or bool(rel_sel.value)
                        or bool(bio_obj_state["taxon_id"])
                        or _active_media()["has_content"]()
                        or _active_extid()["has_content"]()
                        or _active_ls()["has_content"]()
                        or event_media["has_content"]()
                        or any(a.get("media_items") or a.get("extid_items")
                               for a in bio_state["associations"])
                    )

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
                                event_ids = ce["commit"](session)
                                co = svc.save_specimen_entry(
                                    session,
                                    taxon_id=cur_det["taxon_id"],
                                    event_id=state["event_id"],
                                    event_fields={
                                        **ce["collect_fields"](),
                                        **event_ids,
                                    },
                                    specimen_fields=_collect_specimen_fields(session),
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
                                created_assocs = svc.finalize_specimen(
                                    session,
                                    collection_object_id=co.id,
                                    code=None if is_visiting else code,
                                    queue_labels=False,
                                    associations=bio_state["associations"],
                                )
                                # Attach any media staged during digitize, in the same
                                # transaction → atomic with the save: specimen media to
                                # the new specimen, event media to its event, and each
                                # association's media to its freshly-created row.
                                _active_media()["commit"](session, co.id)
                                _active_extid()["commit"](session, co.id)
                                _active_ls()["commit"](session, co.id)
                                if co.collecting_event_id:
                                    event_media["commit"](session, co.collecting_event_id)
                                for _assoc, _ba in zip(bio_state["associations"], created_assocs):
                                    for _it in _assoc.get("media_items", []):
                                        media_svc.attach_stored(
                                            session,
                                            target_kind="biological_association",
                                            target_id=_ba.id, meta=_it["meta"],
                                            caption=_it["caption"] or None,
                                            category=_it["category"],
                                            license=_it["license"] or None,
                                            rights_holder_id=_it["rights_holder_id"],
                                            is_primary=_it["is_primary"],
                                        )
                                    for _ex in _assoc.get("extid_items", []):
                                        extid_svc.add_identifier(
                                            session,
                                            target_kind="biological_association",
                                            target_id=_ba.id, value=_ex["value"],
                                        )
                        event_sel.set_options(_event_opts())
                        spec["refresh_codes"]()
                        ui.notify(f"Saved — specimen #{saved_id}  [{code}]", type="positive")
                        status_lbl.set_text(f"Last saved: #{saved_id}")
                    except Exception as exc:
                        ui.notify(f"Save failed: {exc}", type="negative")
                        return
                    spec_media["clear"](); spec_media_v["clear"]()   # staged media committed
                    spec_extid["clear"](); spec_extid_v["clear"]()
                    spec_ls["clear"](); spec_ls_v["clear"]()
                    event_media["clear"]()
                    _refresh_table()
                    _clear_after_save()
                    # In single-card mode, return to the first step for the next
                    # specimen (no-op in normal mode).
                    _step_idx[0] = 0
                    _refresh_card_visibility()
                    _mark_form_clean("Specimen Digitization")
                    for fn in _refreshers.values():
                        fn()

                save_btn.on_click(_on_save)

                def _refresh_table():
                    table.rows = _table_rows()
                    table.update()

                def _ms_on_saved():
                    event_sel.set_options(_event_opts())
                    _refresh_table()
                    _mark_form_clean("Specimen Digitization")
                    for fn in _refreshers.values():
                        fn()

                def _on_mode_toggle(mode):
                    is_visiting = mode == "visiting"
                    # Standard and Visiting share the identification card, event
                    # card, bio card and save bar; only the specimen card swaps.
                    # Mounting replaces the specimen + identification section with
                    # its own row table. Card visibility (and the single-card
                    # stepper) is computed in one place — _refresh_card_visibility.
                    _active_spec[0] = spec_visiting if is_visiting else spec
                    _step_idx[0] = 0
                    _apply_digitize_layout()
                    # Full wipe on every toggle to avoid unsaved state leaking
                    spec["reset"]()
                    spec_visiting["reset"]()
                    det_state["clear"]()
                    bio_state["associations"].clear()
                    bio_obj_state["clear"]()
                    rel_sel.value = None
                    _refresh_assoc_list()
                    spec_media["clear"](); spec_media_v["clear"](); event_media["clear"]()
                    spec_extid["clear"](); spec_extid_v["clear"]()
                    spec_ls["clear"](); spec_ls_v["clear"]()
                    event_sel.value = None
                    state["event_id"] = None
                    event_status.set_content("· new event")
                    event_status.classes(remove="event-linked", add="event-new")
                    ce["set_readonly"](False)
                    _hide_reuse_banner()
                    ce["reset"]()
                    ms_state["wipe"]()
                    _mark_form_clean("Specimen Digitization")

                # ── Layout: normal multi-card vs single-card stepper ──────
                # One specimen = one Save; the stepper never commits per card,
                # it only changes which card is visible (the real Save stays on
                # the last step). Mounting keeps its own staging layout and
                # ignores the stepper regardless of the config setting.
                _step_idx = [0]
                _STEP_LABELS = ["Specimen", "Identifications",
                                "Collecting Event", "Biological Associations"]

                def _step_cards():
                    first = (spec_visiting["card"]
                             if _mode_state["value"] == "visiting" else specimen_card)
                    return [first, identification_card, event_card, bio_card]

                # Build the step chips once; _refresh_step_chips toggles `active`.
                _step_chip_els: list = []
                with step_header_row:
                    for _i, _lbl in enumerate(_STEP_LABELS):
                        if _i:
                            ui.label("›").classes("tp-step-sep")
                        _chip = ui.element("div").classes("tp-step-chip") \
                            .on("click", lambda _e, idx=_i: _go_to_step(idx))
                        with _chip:
                            ui.label(str(_i + 1)).classes("tp-step-num")
                            ui.label(_lbl)
                        _step_chip_els.append(_chip)

                def _refresh_card_visibility():
                    mode = _mode_state["value"]
                    is_ms       = mode == "mounting"
                    is_standard = mode == "standard"
                    is_visiting = mode == "visiting"
                    single = (get_config().digitize_layout == "single_card"
                              and not is_ms)
                    cards = _step_cards()
                    _step_idx[0] = max(0, min(_step_idx[0], len(cards) - 1))
                    cur_card = cards[_step_idx[0]]
                    # Base visibility from create mode; in single-card mode a base-
                    # visible card is only shown when it is the current step.
                    base = {
                        specimen_card:          is_standard,
                        spec_visiting["card"]:  is_visiting,
                        identification_card:    not is_ms,
                        event_card:             True,
                        bio_card:               True,
                    }
                    for card, vis in base.items():
                        card.set_visibility(vis and (not single or card is cur_card))
                    ms_section.set_visibility(is_ms)
                    last = _step_idx[0] == len(cards) - 1
                    step_header_row.set_visibility(single)
                    step_nav_row.set_visibility(single and not last)
                    std_save_row.set_visibility((not is_ms) and (not single or last))
                    for i, chip in enumerate(_step_chip_els):
                        (chip.classes(add="active") if i == _step_idx[0]
                         else chip.classes(remove="active"))
                    back_btn.set_enabled(_step_idx[0] > 0)

                def _apply_digitize_layout():
                    single = (get_config().digitize_layout == "single_card"
                              and _mode_state["value"] != "mounting")
                    dig_container.classes(remove="max-w-7xl max-w-4xl")
                    dig_container.classes(add="max-w-4xl" if single else "max-w-7xl")
                    _refresh_card_visibility()

                def _go_to_step(i: int):
                    _step_idx[0] = max(0, min(i, len(_step_cards()) - 1))
                    _refresh_card_visibility()

                def _step_nav(delta: int):
                    if (get_config().digitize_layout != "single_card"
                            or _mode_state["value"] == "mounting"):
                        return
                    _go_to_step(_step_idx[0] + delta)

                back_btn.on_click(lambda: _step_nav(-1))
                next_btn.on_click(lambda: _step_nav(1))
                ui.on("tp-step-nav", lambda e: _step_nav(int(e.args)))

                # Apply the configured layout now (also re-applied on mode switch
                # and after saving the Settings dialog).
                _apply_digitize_layout()

                _mode_state["handler"] = _on_mode_toggle
                _mode_state["has_content"] = _has_any_content

                # State-based unsaved-changes detection for Digitize: poll the
                # real field values (not DOM events) so map/push-pin/geocode fills
                # are seen too. Push to the banner only when the state flips.
                _dig_dirty = [False]

                def _sync_dig_dirty():
                    cur = _has_any_content()
                    if cur != _dig_dirty[0]:
                        _dig_dirty[0] = cur
                        ui.run_javascript(
                            "window.tpSetScope && window.tpSetScope("
                            f"'Specimen Digitization', {'true' if cur else 'false'})"
                        )

                ui.timer(1.0, _sync_dig_dirty)


        # ================================================================
        # TAB: RECORDS
        # ================================================================
        with ui.tab_panel("records"):
            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):
                def _records_saved():
                    _mark_form_clean("Records")
                    for fn in _refreshers.values():
                        fn()
                _records_handle = build_records_tab(_sf, on_saved=_records_saved)

                # State-based unsaved-changes detection (#47): poll the loaded form's
                # real field values (not DOM events) so map/geocode fills are seen.
                _rec_dirty = [False]

                def _sync_rec_dirty():
                    cur = _records_handle["has_content"]()
                    if cur != _rec_dirty[0]:
                        _rec_dirty[0] = cur
                        ui.run_javascript(
                            "window.tpSetScope && window.tpSetScope("
                            f"'Records', {'true' if cur else 'false'})"
                        )

                ui.timer(1.0, _sync_rec_dirty)

        # ================================================================
        # TAB: EXPLORE  (#40 — faceted browse over the dataset; drills into Records)
        # ================================================================
        with ui.tab_panel("explore"):
            with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-2"):
                def _explore_open_spec(co_id):
                    _records_handle["open_specimen"](co_id)
                    main_tabs.set_value("records")

                def _explore_open_event(ev_id):
                    _records_handle["open_event"](ev_id)
                    main_tabs.set_value("records")

                _explore_handle = build_explore_panel(
                    _sf,
                    on_open_specimen=_explore_open_spec,
                    on_open_event=_explore_open_event,
                )
                _refreshers["explore"] = _explore_handle["refresh"]

        # ================================================================
        # TAB: IMPORT & ASSIGN
        # ================================================================
        with ui.tab_panel("import"):
            _import_handle = build_import_assign_tab(
                _sf, _refreshers,
                on_saved=lambda: _mark_form_clean("Import & Assign"),
            )

            # State-based unsaved-changes detection (#47): dirty while an assign
            # card is open (a row staged for assignment, not yet saved).
            _imp_dirty = [False]

            def _sync_imp_dirty():
                cur = _import_handle["has_content"]()
                if cur != _imp_dirty[0]:
                    _imp_dirty[0] = cur
                    ui.run_javascript(
                        "window.tpSetScope && window.tpSetScope("
                        f"'Import & Assign', {'true' if cur else 'false'})"
                    )

            ui.timer(1.0, _sync_imp_dirty)

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
                          <span v-if="!props.node.synonym && ['superfamily','family','subfamily','tribe','subtribe','genus','subgenus'].includes(props.node.rank)"
                                class="tax-rank">{{ props.node.rank }}</span>
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

                    # Interactive sheet preview (#37) — the PRIMARY surface. Shows
                    # how the printed sheet groups/lays out; data & determination
                    # labels are edited INLINE (a print-only override). Editing one
                    # label edits ALL identical labels (same auto text = same event
                    # + biological associations). Hovering highlights identical
                    # labels. Identifier labels are read-only (immutable code).
                    preview_box = ui.column().classes("w-full mt-1")

                    def _open_in_records(co_id):
                        if _records_handle and co_id:
                            _records_handle["open_specimen"](co_id)
                        main_tabs.set_value("records")

                    def _edit_label(qid, raw_html):
                        # The WYSIWYG box (or source field) hands back HTML; sanitize
                        # to the safe label subset (italics/bold survive, #45/#46).
                        # Empty or == the row's auto text clears the override (→ auto);
                        # else apply to every identical label (same auto text).
                        clean = lbl_svc.sanitize_override_html(raw_html or "")
                        with _sf() as session:
                            with session.begin():
                                auto_clean = lbl_svc.sanitize_override_html(
                                    pq_svc.row_auto_html(session, qid))
                                new = None if (not clean or clean == auto_clean) else clean
                                n = pq_svc.set_override_for_identical(session, qid, new)
                        verb = "Reset to auto" if new is None else "Applied edit"
                        ui.notify(f"{verb} on {n} identical label{'s' if n != 1 else ''}.", type="info")
                        _refresh_queue()

                    # The contenteditable box's innerHTML cannot ride NiceGUI's event
                    # args (the DOM node is stripped before serialisation), so a global
                    # capture-phase blur listener (added once in head) reads innerHTML
                    # client-side and emits 'pq_edit' with the row id + html.
                    ui.on("pq_edit", lambda e: _edit_label(int(e.args["qid"]), e.args["html"]))

                    def _delete_column(sp):
                        with _sf() as session:
                            with session.begin():
                                for qid in (sp.get("data_qid"), sp.get("det_qid"), sp.get("id_qid")):
                                    if qid:
                                        pq_svc.remove_item(session, qid)
                        _refresh_queue()

                    # Stable DOM ids for the dialog editor (only one open at a time).
                    _DLG_ED, _DLG_SRC = "pq-dlg-editor", "pq-dlg-source"

                    def _open_label_dialog(qid, seed_html):
                        """Larger editor for a queued label — a readable WYSIWYG area
                        with a Bold/Italic toolbar (select text → click), plus a
                        raw-HTML source toggle (#45). The inline box on the sheet is
                        fine for quick tweaks; this window is for longer text and
                        explicit formatting without hand-editing tags."""
                        mode = {"src": False}
                        with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl gap-2"):
                            ui.label("Edit label for print").classes("text-base font-semibold")
                            ui.label("Select text and click B / I to format, or switch to "
                                     "HTML source. Applies to all identical labels; does not "
                                     "change the record.").classes("text-xs") \
                                .style("color:var(--tp-base-soft)")
                            with ui.row().classes("items-center gap-1"):
                                # mousedown.preventDefault keeps the editor's selection
                                # alive (a focused toolbar button would otherwise collapse
                                # it); styleWithCSS=false forces <b>/<i> tags, which the
                                # sanitizer maps to <strong>/<em>.
                                b_btn = ui.button(icon="format_bold").props("flat dense").tooltip("Bold")
                                i_btn = ui.button(icon="format_italic").props("flat dense").tooltip("Italic")
                                b_btn.on("mousedown", js_handler="(e)=>{e.preventDefault();"
                                         "document.execCommand('styleWithCSS',false,false);"
                                         "document.execCommand('bold',false,null);}")
                                i_btn.on("mousedown", js_handler="(e)=>{e.preventDefault();"
                                         "document.execCommand('styleWithCSS',false,false);"
                                         "document.execCommand('italic',false,null);}")
                                ui.space()
                                src_btn = ui.button(icon="code").props("flat dense") \
                                    .tooltip("Toggle HTML source")
                            editor = (ui.element("div")
                                      .props(f'contenteditable=true id={_DLG_ED}')
                                      .classes("pq-dlg-editor"))
                            source = (ui.textarea()
                                      .props(f"id={_DLG_SRC} outlined")
                                      .classes("pq-dlg-source w-full"))
                            source.set_visibility(False)
                            with ui.row().classes("justify-end w-full gap-2 mt-1"):
                                ui.button("Abort").props("flat").on_click(dlg.close)
                                save_btn = ui.button("Save & close").props("color=primary")

                        # Seed both surfaces (editor innerHTML set imperatively so Vue
                        # never re-binds/clobbers it; the textarea via its value).
                        ui.run_javascript(
                            f"document.getElementById('{_DLG_ED}').innerHTML = {json.dumps(seed_html or '')};")
                        source.value = seed_html or ""

                        async def _toggle_src():
                            if not mode["src"]:
                                html = await ui.run_javascript(
                                    f"document.getElementById('{_DLG_ED}').innerHTML")
                                source.value = html or ""
                                editor.set_visibility(False); source.set_visibility(True)
                                mode["src"] = True
                            else:
                                ui.run_javascript(
                                    f"document.getElementById('{_DLG_ED}').innerHTML = "
                                    f"{json.dumps(source.value or '')};")
                                source.set_visibility(False); editor.set_visibility(True)
                                mode["src"] = False
                        src_btn.on_click(_toggle_src)

                        async def _save():
                            html = (source.value if mode["src"]
                                    else await ui.run_javascript(
                                        f"document.getElementById('{_DLG_ED}').innerHTML"))
                            dlg.close()
                            _edit_label(qid, html)
                        save_btn.on_click(_save)

                        # Per-action dialog: delete on close so its timers don't leak.
                        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                        dlg.open()

                    def _editable_box(kind, sp):
                        html_seed = sp["data_html"]      if kind == "data" else sp["det_html"]
                        auto_html = sp["data_auto_html"] if kind == "data" else sp["det_auto_html"]
                        qid       = sp["data_qid"]       if kind == "data" else sp["det_qid"]
                        ident     = sp["data_ident"]     if kind == "data" else sp["det_ident"]
                        cls = f"pq-prev-label pq-prev-{kind}"
                        if (html_seed or "") != (auto_html or ""):
                            cls += " pq-prev-edited"
                        with ui.element("div").classes("pq-box-wrap"):
                            # Primary surface: a contenteditable box rendered with the
                            # real formatted label HTML — what you see prints (#46).
                            # Typing inside a <strong><em> token keeps its styling;
                            # text added outside stays plain (#45). innerHTML is
                            # captured by the global blur listener via data-qid.
                            (ui.html(html_seed or "")
                             .classes(cls)
                             .props(f'contenteditable=true data-ident="{ident}" data-qid="{qid}"')
                             .tooltip("Edit inline for print fit, or open the larger editor "
                                      "(⤢). Applies to all identical labels; does not change "
                                      "the record."))
                            # Larger editor: formatting toolbar + HTML source (#45).
                            (ui.button(icon="open_in_full")
                             .props("flat dense round size=xs")
                             .classes("pq-box-toggle")
                             .tooltip("Open larger editor (Bold / Italic toolbar + HTML source)")
                             .on_click(lambda _, q=qid, h=html_seed or "": _open_label_dialog(q, h)))

                    def _render_column(sp):
                        with ui.element("div").classes("pq-prev-col"):
                            if sp["data_qid"] is not None:
                                _editable_box("data", sp)
                            if sp["id_code"]:
                                with ui.element("div").classes("pq-prev-label pq-prev-id"):
                                    ui.label(sp["id_code"])
                            if sp["det_qid"] is not None:
                                _editable_box("det", sp)
                            with ui.row().classes("pq-prev-ctl items-center gap-0 w-full"):
                                if sp["co_id"]:
                                    ui.button("", icon="open_in_new") \
                                        .props("flat dense round size=xs") \
                                        .tooltip("Open this specimen in Records (substantial edits)") \
                                        .on_click(lambda _, c=sp["co_id"]: _open_in_records(c))
                                ui.button("", icon="close") \
                                    .props("flat dense round size=xs") \
                                    .tooltip("Remove these labels from the queue") \
                                    .on_click(lambda _, s=sp: _delete_column(s))

                    def _refresh_queue():
                        summary = _with_session(pq_svc.queue_summary)
                        queue_count_lbl.set_text(
                            f"{summary.total} queued  "
                            f"({summary.n_data} data · "
                            f"{summary.n_determination} det · "
                            f"{summary.n_identifier} id)"
                            if summary.total else "empty"
                        )
                        model = _with_session(pq_svc.preview_model)
                        preview_box.clear()
                        with preview_box:
                            if not model:
                                ui.label("Nothing queued yet — labels are added automatically "
                                         "when you save specimens or generate identifier codes.") \
                                  .classes("text-sm italic").style("color:var(--tp-base-soft)")
                                return
                            with ui.element("div").classes("pq-prev"):
                                for g in model:
                                    with ui.element("div").classes("pq-prev-group"):
                                        ui.label(g["source"] or "Queued labels").classes("pq-prev-src")
                                        with ui.element("div").classes("pq-prev-cols"):
                                            for sp in g["specimens"]:
                                                _render_column(sp)

                    def _print_all():
                        summary = _with_session(pq_svc.queue_summary)
                        if summary.total == 0:
                            ui.notify("Queue is empty.", type="warning")
                            return
                        now = datetime.now()
                        stamp_human = now.strftime("%Y-%m-%d %H:%M")
                        stamp_file  = now.strftime("%Y%m%d-%H%M%S")
                        pdf = _with_session(
                            lambda s: pq_svc.build_pdf(s, stamp_human)
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
        elif e.value == "labels":
            # Rebuild the print-queue preview now that the panel is visible, so the
            # autogrow label editors size to their content (they can't measure
            # while the tab is hidden — they render collapsed until interacted).
            refresh_queue = _refreshers.get("queue")
            if refresh_queue:
                refresh_queue()

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

            # ── Digitize layout ───────────────────────────────────────────
            ui.label("Digitize layout").classes("text-sm font-medium mb-1")
            ui.label(
                "Multi-card shows all cards on one wide page. Single card shows "
                "one card at a time as a guided stepper (←/→ to move between cards)."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")
            digitize_layout_toggle = ui.toggle(
                {"normal": "Multi-card", "single_card": "Single card"},
                value=get_config().digitize_layout,
            ).props("no-caps")

            ui.separator().classes("my-3")

            # ── Default names ─────────────────────────────────────────────
            ui.label("Default names").classes("text-sm font-medium mb-1")
            ui.label(
                "Inserted with one click in identifiedBy / recordedBy / media "
                "rightsHolder fields."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            with _sf() as _s_init:
                _idby_init, _recby_init, _rights_init = pd_svc.get_defaults(_s_init)
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
            rights_state_cfg = build_person_field(
                _sf, "Default media rightsHolder",
                initial_value=_rights_init,
                classes="w-full mt-1",
            )

            ui.separator().classes("my-3")

            # ── Media default licence (Tier-2 default for the media editor) ──
            ui.label("Default media licence").classes("text-sm font-medium mb-1")
            ui.label(
                "Inserted with one click in a media file's licence field."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")
            from app.vocab import LICENSE_OPTIONS as _LICENSE_OPTIONS
            default_license_sel = ui.select(
                _LICENSE_OPTIONS, value=get_config().default_license or "",
                label="Default licence",
            ).classes("w-full mt-1")

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
                cfg.digitize_layout       = digitize_layout_toggle.value or "normal"
                cfg.default_license       = default_license_sel.value or ""
                with _sf() as _s:
                    with _s.begin():
                        idby_id = idby_state["commit"](_s)
                        recby_id = recby_state_cfg["commit"](_s)
                        rights_id = rights_state_cfg["commit"](_s)
                        pd_svc.set_defaults(
                            _s,
                            identified_by_id=idby_id,
                            recorded_by_id=recby_id,
                            rights_holder_id=rights_id,
                        )
                cfg.bio_assoc_default_codes = selected
                save_config(cfg)
                # Propagate to active bio_codes filter in place
                bio_codes.clear()
                bio_codes.extend(selected)
                # Apply the Digitize layout live (no page reload, so any unsaved
                # form entry survives the settings change).
                _step_idx[0] = 0
                _apply_digitize_layout()
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
        digitize_layout_toggle.value = cfg.digitize_layout or "normal"
        default_license_sel.value = cfg.default_license or ""
        with _sf() as _s:
            _idby, _recby, _rights = pd_svc.get_defaults(_s)
        idby_state["set_value"](_idby)
        recby_state_cfg["set_value"](_recby)
        rights_state_cfg["set_value"](_rights)
        for code, cb in _code_cbs.items():
            cb.value = code in cfg.bio_assoc_default_codes
        settings_dialog.open()

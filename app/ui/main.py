"""Collection app — main UI.

Two tabs:
  • Specimen Digitization — entry form + recent-specimens table
  • Taxonomy             — checklist tree with species / specimen counts

All DB access goes through app.services — no ORM queries in this file.
"""
from __future__ import annotations

import asyncio
import os
import sys

from nicegui import ui, app, run

from app.database import get_engine, get_session_factory
import app.services.repositories as repo_svc
import app.services.taxonworks as tw_svc
import app.services.wcvp as wcvp_svc
import app.services.name_source as ns_svc
import app.services.datasets as ds_svc
from app.config import get_config, reload_config, save_config, media_dir

# Serve the managed media store so attached images/files render in the browser
# (range-request aware → also handles audio/video). Registered once at import.
app.add_media_files("/media", media_dir())
import app.services.person_defaults as pd_svc
import app.services.db_safety as db_safety
import app.services.launcher as launcher
from app.ui.import_assign import build_import_assign_tab
from app.ui.controlled_vocab_tab import build_controlled_vocab_tab
from app.ui.batch_tab import build_batch_tab
from app.ui.bulk_import_tab import build_bulk_import_tab
from app.ui.tw_sync_tab import build_tw_sync_tab
from app.ui.map_picker import add_map_assets
from app.ui.person_field import build_person_field
from app.ui.digitize_tab import build_digitize_tab
from app.ui.taxonomy_tab import build_taxonomy_tab
from app.ui.labels_tab import build_labels_tab
from app.ui.records_tab import build_records_tab
from app.ui.explore import build_explore_panel
import app.ui.record_summary as _record_summary
from app.ui.confirm_dialog import confirm as confirm_dialog
from app.services.biological import sync_biological_relationships

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
    ui.add_head_html(_record_summary.CSS)   # the shared record-summary styles
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

    # ── Enter-to-select on filterable selects (identifier code field) ────
    # The custom person/vocab dropdowns auto-highlight their top match and let
    # Enter pick it + advance (person_field._NAV_SCRIPT). Native Quasar q-selects
    # with typed input (with_input=True — the identifier code picker) do NOT: no
    # option is highlighted and Enter does nothing. Mirror the custom behaviour so
    # the whole form is keyboard-drivable (type → Enter → next field). Scoped to
    # `.q-select--with-input` so plain fixed-list selects (sex, basisOfRecord,
    # lifeStage) are untouched.
    ui.add_head_html("""
    <script>
    (function(){
      var FOCUS_CLS = 'q-manual-focusable--focused';
      function openMenu(){
        var menus = document.querySelectorAll('.q-menu');
        for (var i=0;i<menus.length;i++){
          var s = window.getComputedStyle(menus[i]);
          if (s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0') return menus[i];
        }
        return null;
      }
      function visibleItems(menu){
        var items = menu.querySelectorAll('.q-item--clickable');
        if (items.length===0) items = menu.querySelectorAll('.q-item');
        var out=[];
        for (var j=0;j<items.length;j++){
          var s=window.getComputedStyle(items[j]);
          if (s.display!=='none' && s.visibility!=='hidden') out.push(items[j]);
        }
        return out;
      }
      // Highlight the first filtered option as the user types, so it is visibly the
      // one Enter will take (Quasar leaves nothing highlighted until ArrowDown).
      document.addEventListener('input', function(e){
        var qs = e.target && e.target.closest && e.target.closest('.q-select--with-input');
        if (!qs) return;
        setTimeout(function(){
          var m = openMenu(); if (!m) return;
          var vis = visibleItems(m); if (!vis.length) return;
          if (!vis.some(function(it){return it.classList.contains(FOCUS_CLS);}))
            vis[0].classList.add(FOCUS_CLS);
        }, 60);
      }, true);
      // Enter: take the highlighted option (else the first visible), then advance
      // focus to the next field — the same "type → Enter → next" flow as the
      // custom dropdowns.
      document.addEventListener('keydown', function(e){
        if (e.key !== 'Enter') return;
        var active = document.activeElement;
        var qs = active && active.closest && active.closest('.q-select--with-input');
        if (!qs) return;
        var m = openMenu(); if (!m) return;
        var vis = visibleItems(m); if (!vis.length) return;
        var target = vis.find(function(it){return it.classList.contains(FOCUS_CLS);}) || vis[0];
        e.preventDefault(); e.stopPropagation();
        target.click();
        setTimeout(function(){
          var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
            'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
          var all = Array.from(document.querySelectorAll(FOCUSABLE)).filter(function(el){
            var s = window.getComputedStyle(el);
            return s.display!=='none' && s.visibility!=='hidden';
          });
          var last=-1;
          for (var k=0;k<all.length;k++){ if (qs.contains(all[k])) last=k; }
          if (last>=0 && last+1<all.length) all[last+1].focus();
        }, 30);
      }, true);
    })();
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

    # ── Favicons ─────────────────────────────────────────────────────────
    # One raster PNG for every surface — the browser tab AND the Chromium `--app`
    # window / taskbar icon. The SVG favicon was dropped deliberately: Chromium
    # prefers it when setting the Wayland window icon, and our vector silhouette
    # (beetle_blue.svg) sits ~41px left of centre, so the taskbar showed an
    # off-centre beetle. The framed woodcut PNG is centred — it is the icon we
    # want everywhere — and a sized PNG at a stable /static URL also gives Chromium
    # an unambiguous raster, so the app-window icon is deterministic (no monogram
    # placeholder while an SVG is rasterised). Cold-start icon determinism on
    # Wayland is additionally pinned via the window class → .desktop match; see
    # launcher.open_ui and desktop_entry.
    ui.add_head_html(
        '<link rel="icon" type="image/png" sizes="256x256" href="/static/collection_icon.png">'
        '<link rel="apple-touch-icon" href="/static/collection_icon.png">'
    )

    # ── Print-queue sheet preview (#37): styling + hover-highlight ───────
    # Hovering any data label highlights every data label with the same
    # identity (same collecting event AND biological associations — see
    # print_queue._data_identity), so identical labels are visible at a glance.
    ui.add_head_html("""
    <style>
      /* WYSIWYG print-queue preview: the REAL label sheet (labels.preview_html + the
         scoped label CSS injected separately), zoomed with transform:scale — a POST-
         layout paint, so labels lay out once at true physical size (wrapping == print)
         and lines never re-break. */
      .pq-sheet-wrap  { width:100%; overflow:auto; max-height:80vh; padding:12px;
                        background:#e9e9ec; border:1px solid var(--tp-base-border);
                        border-radius:8px; cursor:grab; }
      .pq-sheet-wrap:active { cursor:grabbing; }
      .dark .pq-sheet-wrap { background:#3a3a3f; }   /* neutral desk; page is white */
      .pq-sheet-scale { transform-origin: top left; transform: scale(var(--pq-zoom, 1.5));
                        width: max-content; }
      /* editable label affordances on the real .pq-edit boxes */
      .pq-sheet .pq-edit         { cursor:text; }
      .pq-sheet .pq-edit:hover   { outline:0.3mm solid var(--tp-secondary); outline-offset:0; }
      .pq-sheet .pq-edit:focus   { outline:0.3mm solid var(--tp-secondary); outline-offset:0; }
      .pq-sheet .pq-ident-hl     { background:rgba(3,105,161,.14) !important; }
      /* an edited (override) label gets a subtle amber tint so overrides are visible */
      .pq-sheet .pq-edited       { background:#fff7ed !important; }
      /* floating hover toolbar (edit / open-in-Records / remove), positioned by JS */
      .pq-tools   { position:fixed; z-index:9999; display:none; gap:1px;
                    background:var(--tp-base-foreground); border:1px solid var(--tp-base-border);
                    border-radius:6px; padding:2px; box-shadow:0 2px 10px rgba(0,0,0,.2); }
      .pq-tools button { border:none; background:none; cursor:pointer; font-size:14px;
                    line-height:1; padding:3px 6px; border-radius:4px; color:var(--tp-base); }
      .pq-tools button:hover { background:var(--tp-base-border); }
      /* remove-choice menu off the ✕ (this label vs. every label for the specimen) */
      .pq-remove-menu { position:fixed; z-index:10000; display:none; flex-direction:column;
                    background:var(--tp-base-foreground); border:1px solid var(--tp-base-border);
                    border-radius:6px; padding:3px; box-shadow:0 2px 10px rgba(0,0,0,.2); }
      .pq-remove-menu button { border:none; background:none; cursor:pointer; font-size:12px;
                    text-align:left; white-space:nowrap; padding:5px 9px; border-radius:4px;
                    color:var(--tp-base); }
      .pq-remove-menu button:hover { background:var(--tp-base-border); }
      .pq-zoombar { display:flex; align-items:center; gap:6px; }
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
      .pq-prev-empty  { font-size:.85rem; font-style:italic; color:var(--tp-base-soft); }
    </style>
    <script>
    (function(){
      if (window._pqPrevHover) return;
      window._pqPrevHover = true;
      // Hovering an editable label highlights every IDENTICAL label (same auto text).
      function hl(e, on){
        var el = e.target.closest && e.target.closest('.pq-edit[data-ident]');
        if(!el) return;
        var id = el.getAttribute('data-ident');
        document.querySelectorAll('.pq-edit[data-ident="'+CSS.escape(id)+'"]')
          .forEach(function(x){ x.classList.toggle('pq-ident-hl', on); });
      }
      document.addEventListener('mouseover', function(e){ hl(e, true); });
      document.addEventListener('mouseout',  function(e){ hl(e, false); });
      // Capture-phase blur: a contenteditable label box hands back its row id + innerHTML.
      document.addEventListener('blur', function(e){
        var el = e.target;
        if(el && el.matches && el.matches('.pq-edit[contenteditable][data-qid]')){
          emitEvent('pq_edit', { qid: el.getAttribute('data-qid'), html: el.innerHTML });
        }
      }, true);
      // Floating hover toolbar: edit (data/det only) / open-in-Records / remove (menu).
      var tools=null, hideT=null, rmenu=null;
      // The ✕ opens a small menu so the user chooses WHAT to remove — this one label,
      // or every label for the specimen — instead of one click nuking the whole stack.
      function mkMenu(){
        if(rmenu) return rmenu;
        rmenu=document.createElement('div'); rmenu.className='pq-remove-menu';
        rmenu.innerHTML='<button data-r="one">Remove this label</button>'+
                        '<button data-r="co">Remove all labels for this specimen</button>';
        document.body.appendChild(rmenu);
        rmenu.addEventListener('mousedown', function(ev){ ev.preventDefault(); });
        rmenu.addEventListener('mouseover', function(){ if(hideT){clearTimeout(hideT);hideT=null;} });
        rmenu.addEventListener('mouseout',  function(){ hideT=setTimeout(hideTools,250); });
        rmenu.addEventListener('click', function(ev){
          var b=ev.target.closest('button'); if(!b) return;
          _tpEmit('pq_tool', {action: b.getAttribute('data-r')==='co' ? 'remove_co' : 'remove_one',
                              qid: tools&&tools._qid||'', co: tools&&tools._co||''});
          hideTools();
        });
        return rmenu;
      }
      function showMenu(){
        var m=mkMenu(), rb=tools.querySelector('[data-a="remove"]');
        var r=rb.getBoundingClientRect();
        m.style.left=Math.max(4, r.left)+'px'; m.style.top=(r.bottom+2)+'px';
        // "Remove all for specimen" only applies when the label belongs to a specimen
        // (a reserved-code identifier standing alone has none).
        m.querySelector('[data-r="co"]').style.display = tools._co ? '' : 'none';
        m.style.display='flex';
      }
      function mkTools(){
        if(tools) return tools;
        tools=document.createElement('div'); tools.className='pq-tools';
        tools.innerHTML='<button data-a="edit" title="Larger editor (formatting)">&#x2922;</button>'+
                        '<button data-a="records" title="Open specimen in Records">&#x2197;</button>'+
                        '<button data-a="remove" title="Remove label…">&#x2715;</button>';
        document.body.appendChild(tools);
        tools.addEventListener('mousedown', function(ev){ ev.preventDefault(); });
        tools.addEventListener('mouseover', function(){ if(hideT){clearTimeout(hideT);hideT=null;} });
        tools.addEventListener('mouseout',  function(){ hideT=setTimeout(hideTools,250); });
        tools.addEventListener('click', function(ev){
          var b=ev.target.closest('button'); if(!b) return;
          var a=b.getAttribute('data-a');
          if(a==='remove'){ showMenu(); return; }   // choose one-vs-all in the menu
          _tpEmit('pq_tool', {action:a, qid:tools._qid||'', co:tools._co||''});
        });
        return tools;
      }
      function hideTools(){ if(tools) tools.style.display='none'; if(rmenu) rmenu.style.display='none'; }
      document.addEventListener('mouseover', function(e){
        var el=e.target.closest && e.target.closest('.pq-sheet [data-qid]');
        if(!el){ return; }
        var t=mkTools();
        t._qid=el.getAttribute('data-qid'); t._co=el.getAttribute('data-co')||'';
        t.querySelector('[data-a="edit"]').style.display = el.classList.contains('pq-edit') ? '' : 'none';
        t.querySelector('[data-a="records"]').style.display = t._co ? '' : 'none';
        var r=el.getBoundingClientRect();
        t.style.left=Math.max(4, r.right-2)+'px'; t.style.top=Math.max(4, r.top-2)+'px';
        t.style.display='flex';
        if(hideT){clearTimeout(hideT);hideT=null;}
      });
      document.addEventListener('mouseout', function(e){
        if(e.target.closest && e.target.closest('.pq-sheet [data-qid]')){
          hideT=setTimeout(hideTools, 250);
        }
      });
      // Zoom: set the CSS var on the scale container (post-layout, no re-wrap).
      window._pqZoom = function(z){
        document.querySelectorAll('.pq-sheet-scale').forEach(function(s){
          s.style.setProperty('--pq-zoom', z); });
      };
      // Fit the A4 page to the preview width (210mm = 793.7 CSS px at 96dpi). Returns the
      // scale so Python can sync the slider.
      window._pqFit = function(){
        var wrap = document.querySelector('.pq-sheet-wrap');
        if(!wrap) return null;
        var z = Math.max(0.4, Math.min(6, (wrap.clientWidth - 28) / 793.7));
        z = Math.round(z*20)/20;
        window._pqZoom(z);
        return z;
      };
      // Drag-to-pan: press on the page background (NOT on an editable label / toolbar /
      // button) and drag to move around; the cursor shows a grab hand. Clicking a label
      // still edits it.
      var pan = null;
      document.addEventListener('mousedown', function(e){
        var wrap = e.target.closest && e.target.closest('.pq-sheet-wrap');
        if(!wrap || e.button !== 0) return;
        if(e.target.closest('.pq-edit') || e.target.closest('button') ||
           e.target.closest('.pq-tools')) return;   // let edits / controls through
        pan = {wrap:wrap, x:e.clientX, y:e.clientY, sl:wrap.scrollLeft, st:wrap.scrollTop};
        wrap.style.cursor = 'grabbing';
        e.preventDefault();
      });
      document.addEventListener('mousemove', function(e){
        if(!pan) return;
        pan.wrap.scrollLeft = pan.sl - (e.clientX - pan.x);
        pan.wrap.scrollTop  = pan.st - (e.clientY - pan.y);
      });
      document.addEventListener('mouseup', function(){
        if(pan){ pan.wrap.style.cursor = ''; pan = null; }
      });
      // Ctrl/⌘ + mouse-wheel zooms the page under the cursor (keeps the point under the
      // pointer stable). Plain wheel still scrolls the page normally.
      window._pqCurZoom = 1.5;
      document.addEventListener('wheel', function(e){
        if(!(e.ctrlKey || e.metaKey)) return;
        var wrap = e.target.closest && e.target.closest('.pq-sheet-wrap');
        if(!wrap) return;
        e.preventDefault();
        var scale = document.querySelector('.pq-sheet-scale');
        var cur = parseFloat(getComputedStyle(scale).transform.split(',')[3]) || window._pqCurZoom;
        // anchor: content point under the cursor before zoom
        var ratio = Math.exp(-e.deltaY * 0.0015);
        var next = Math.max(0.4, Math.min(8, cur * ratio));
        var rect = wrap.getBoundingClientRect();
        var cx = wrap.scrollLeft + (e.clientX - rect.left);
        var cy = wrap.scrollTop  + (e.clientY - rect.top);
        var k = next / cur;
        window._pqZoom(next); window._pqCurZoom = next;
        wrap.scrollLeft = cx * k - (e.clientX - rect.left);
        wrap.scrollTop  = cy * k - (e.clientY - rect.top);
      }, { passive: false });
    })();
    // Bridge for emitting a Python event from inside a Vue TEMPLATE (the checklist
    // tree's row actions, added via add_slot). Vue's runtime-compiled template proxy
    // resolves any identifier that is not on its small allow-list to `undefined` —
    // so neither `emitEvent(...)` nor `window.emitEvent(...)` works there (the latter
    // because `window` itself resolves to undefined). Names starting with '_' are the
    // documented escape: the proxy's has() trap returns false for them, so they fall
    // through to the real global scope. Hence the underscore.
    window._tpEmit = function(name, arg){ emitEvent(name, arg); };
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
        transform:translateX(-50%); z-index:6000;
        background:#b45309; color:#fff; padding:7px 18px; border-radius:9px;
        font-size:.82rem; font-weight:600; letter-spacing:.01em;
        box-shadow:0 2px 10px rgba(0,0,0,.28);
        transition:bottom .2s ease;
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
        // #172: each scope name is its own clickable span (text-only, several may be
        // listed) so the user can jump straight to the tab that holds the unsaved edit.
        var esc = function(s){
          var d = document.createElement('div'); d.textContent = s; return d.innerHTML;
        };
        var links = Array.from(dirty).map(function(l){
          return '<span class="tp-banner-nav" data-label="' + esc(l)
            + '" style="text-decoration:underline;cursor:pointer;">' + esc(l) + '</span>';
        });
        b.innerHTML = '\\u26A0  Unsaved changes in: ' + links.join(', ');
        b.style.display = 'block';
      }
      document.addEventListener('click', function(e){
        var el = e.target.closest && e.target.closest('.tp-banner-nav');
        if(!el) return;
        emitEvent('tp_banner_nav', el.getAttribute('data-label'));
      });
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
      // #55: notifications appear at the bottom too. Lift the banner to hover just
      // above any visible bottom notifications, and drop it back to its resting
      // 16px once they clear — so it never sits on top of a warning the user needs
      // to read.
      function adjustBannerPos(){
        var b = document.getElementById('tp-unsaved-banner');
        if(!b || b.style.display === 'none') return;
        var rest = 16, offset = rest;
        document.querySelectorAll(
          '.q-notifications__list--bottom, .q-notifications__list--bottom-right, '
          + '.q-notifications__list--bottom-left').forEach(function(list){
            if(list.querySelector('.q-notification')){
              offset = Math.max(offset, rest + list.getBoundingClientRect().height + 8);
            }
          });
        b.style.bottom = offset + 'px';
      }
      setInterval(adjustBannerPos, 250);
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
      /* Header is LIGHT overall; only the TITLE row is a grey bar (#142), so the tab row
         and the Digitize mode row sit on light with no grey strip peeking between them.
         Dark mode keeps the near-black title bar. */
      .app-header       { background:var(--tp-base-foreground) !important;
                          color:var(--tp-base-content) !important; }
      .app-header-row   { padding:.35rem 1.5rem;
                          background:rgb(82,82,91); color:var(--tp-primary-content); }
      .dark .app-header-row { background:var(--tp-primary); }
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
      /* fixed-width, right-aligned rank column so the NAMES all start at the same
         offset regardless of the rank word's length (Superfamily vs Family etc.) */
      .tax-rank       { display:inline-block; width:5.6rem; text-align:right;
                        flex-shrink:0; font-size:.58rem; font-weight:600;
                        text-transform:uppercase; letter-spacing:.07em;
                        color:var(--tp-base-soft); margin-right:8px; align-self:center; }
      /* Flatten the tree's per-level indentation so every name lines up at the same
         column — hierarchy is carried by the rank label + type size (catalogue style),
         not by indentation. Collapse/expand still works via the node arrows. */
      .checklist-tree .q-tree__children { padding-left:0; }
      /* reorderable hint (family-and-above): faint, brightens on row hover.
         The actual reorder happens via row-select + the ↑/↓ toolbar buttons. */
      .tax-move-hint { opacity:0; transition:opacity .12s; margin-left:4px;
                       color:var(--tp-base-soft); cursor:default; }
      .checklist-tree.reorder-on .q-tree__node-header:hover .tax-move-hint { opacity:.45; }
      /* Per-row actions (edit, open in TaxonPages): hidden until the row is hovered, so
         the checklist stays clean to READ — it is a checklist first — while the actions
         are one hover away on the row they belong to. Both carry a q-tooltip: an icon
         that appears on hover must still say what it does. */
      .tax-row-action { opacity:0; transition:opacity .12s; margin-left:2px;
                        color:var(--tp-base-soft); cursor:pointer; }
      .checklist-tree .q-tree__node-header:hover .tax-row-action { opacity:.55; }
      .tax-row-action:hover { opacity:1 !important; color:var(--tp-secondary); }
      .tax-tw-link   { font-size:.72rem; text-decoration:none; line-height:1; }
      /* Every rank in TAXON_RANKS must have a style. A rank with none falls through to
         the default body size and reads as a different KIND of row rather than a higher
         one — which is exactly how a mis-ranked 'superorder' hid in plain sight. The
         ranks above order are the rarest, so they were the ones missing. */
      .rank-kingdom,
      .rank-phylum,
      .rank-subphylum,
      .rank-class,
      .rank-subclass,
      .rank-infraclass,
      .rank-superorder,
      .rank-order,
      .rank-suborder,
      .rank-infraorder,
      .rank-series,
      .rank-superfamily { font-size:1.45rem; font-weight:700;
                        text-transform:uppercase; letter-spacing:.05em; }
      .rank-family    { font-size:1.35rem; font-weight:800;
                        text-transform:uppercase; letter-spacing:.03em; }
      .rank-subfamily { font-size:1.12rem; font-weight:700; }
      .rank-supertribe { font-size:1.0rem; font-weight:600; }
      .rank-tribe     { font-size:1.0rem;  font-weight:600; }
      .rank-subtribe  { font-size:.92rem; font-weight:600; }
      .rank-genus     { font-size:1.05rem; font-weight:700; font-style:italic; }
      .rank-subgenus  { font-size:.875rem; font-style:italic; }
      .rank-section    { font-size:.875rem; font-style:italic; }
      .rank-subsection { font-size:.875rem; font-style:italic; }
      .rank-species     { font-size:.875rem; font-style:italic; }
      .rank-subspecies  { font-size:.875rem; font-style:italic; }
      .rank-variety     { font-size:.875rem; font-style:italic; }
      .rank-subvariety  { font-size:.875rem; font-style:italic; }
      .rank-form        { font-size:.875rem; font-style:italic; }
      .rank-subform     { font-size:.875rem; font-style:italic; }
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
      /* ── recent-specimens list (Digitize) — the shared record-summary row
         (record_summary.specimen_html); only the separators are local ─ */
      .rc-list  { display:flex; flex-direction:column; }
      .rc-list > .rs-row { padding:5px 2px; border-bottom:1px solid var(--tp-base-border); }
      .rc-list > .rs-row:last-child { border-bottom:none; }
      .rc-empty { font-size:.82rem; color:var(--tp-base-soft); padding:8px 2px; }
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
                # Re-exec the same process. Force --no-browser so the restart does NOT
                # open a second window in app mode — the current window reconnects to
                # the new server on its own once it is back up.
                ui.button(icon="restart_alt", on_click=lambda: os.execv(
                    sys.executable,
                    [sys.executable, *sys.argv]
                    + ([] if "--no-browser" in sys.argv else ["--no-browser"]),
                ))
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
                    ui.tab("import",   label="Import",                icon="upload_file")
                    ui.tab("batch",    label="Batch tools",           icon="checklist")
                    ui.tab("taxonomy", label="Taxonomy",              icon="account_tree")
                    ui.tab("labels",   label="Labels",                icon="label")
                    # Only when a TaxonWorks connection is configured (#149): with no token
                    # every action in the tab would fail at the first request, so the tab
                    # itself is the honest place to say "not set up" — by not being there.
                    # Read once per page build, so saving a token in Settings surfaces it on
                    # the next page load (the tab tree is built once per client).
                    if get_config().taxonworks_enabled:
                        ui.tab("twsync", label="TaxonWorks",           icon="cloud_sync")
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
                proceed = await confirm_dialog(
                    title="Discard unsaved data?",
                    body="Switching mode clears the current form. Anything you have "
                         "entered and not saved will be lost.",
                    action_label="Discard & switch",
                )
                if not proceed:
                    return
            _set_mode(val)

        # Full-width light bar so the grey header doesn't show through its sides (#142);
        # the toggle stays aligned with the page content via the inner max-w wrapper.
        with ui.row().classes("app-mode-row w-full") as _mode_row:
          with ui.element("div").classes("w-full flex justify-center"):
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

    # ── App-mode fallback notice ─────────────────────────────────────────
    # The launcher (in this same process) sets launcher.app_mode_fallback when
    # "App window" was chosen but no Chromium-class browser was available, so it
    # opened a plain tab instead. A system notification already fired; surface it
    # in the app too, once (clear the flag so a reload doesn't repeat it).
    _app_fb = launcher.app_mode_fallback
    if _app_fb:
        launcher.app_mode_fallback = None
        ui.timer(0.6, lambda m=_app_fb: ui.notify(
            m, type="warning", multi_line=True, timeout=12000, close_button="OK"),
            once=True)

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

    # Geo mirror (QGIS): rebuild data/geo/collection.gpkg after saves. Registered as a
    # refresher (every save path iterates _refreshers) and coalesced — a burst of saves
    # triggers one rebuild — and kicked once on load so the mirror + starter .qgz exist
    # immediately. The rebuild is guarded (geo_mirror.refresh never raises into a save).
    _geo_pending: dict = {"timer": None}

    def _run_geo_refresh():
        _geo_pending["timer"] = None
        import logging
        try:
            import app.services.geo_mirror as _geo
            _geo.refresh(_sf)
        except Exception:                                    # noqa: BLE001
            logging.getLogger(__name__).exception("geo mirror refresh failed")

    def _schedule_geo_refresh():
        if _geo_pending["timer"] is None:
            _geo_pending["timer"] = ui.timer(0.8, _run_geo_refresh, once=True)

    _refreshers["geo_mirror"] = _schedule_geo_refresh
    _schedule_geo_refresh()   # initial build on page load (existing data + starter .qgz)

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
            # _mode_state and bio_codes are passed as the same objects the header's mode
            # switch and Settings mutate in place.
            _digitize_handle = build_digitize_tab(
                _sf, refreshers=_refreshers, mode_state=_mode_state,
                mark_form_clean=_mark_form_clean, bio_codes=bio_codes)


        # ================================================================
        # TAB: RECORDS
        # ================================================================
        with ui.tab_panel("records"):
            # Wide like Explore (#137): the condensed record sheet lays out map + media +
            # details across the reclaimed wide-screen space.
            with ui.column().classes("w-full max-w-[88rem] mx-auto px-4 pt-6 pb-16 gap-4"):
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
            # Wider than the other tabs (#137): Explore has a favorites rail on the left
            # and data/charts on the right that both benefit from reclaimed wide-screen space.
            with ui.column().classes("w-full max-w-[88rem] mx-auto px-4 pt-6 pb-16 gap-2"):
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
        # TAB: IMPORT — row-by-row (Import & Assign) + wholesale (Bulk import)
        # ================================================================
        with ui.tab_panel("import"):
            # Two ways to import share one tab: Import & Assign stamps one specimen at
            # a time (retroactive digitisation); Bulk import stages a whole name
            # checklist (#39). A nested sub-tab picks between them.
            _import_sub = (
                ui.tabs(value="assign")
                .props("dense indicator-color=secondary align=left no-caps")
            )
            with _import_sub:
                ui.tab("assign", label="Import & Assign", icon="upload_file")
                ui.tab("bulk",   label="Bulk import",     icon="library_add")

            with ui.tab_panels(_import_sub, value="assign").classes("w-full"):
                with ui.tab_panel("assign"):
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

                with ui.tab_panel("bulk"):
                    with ui.column().classes("w-full max-w-5xl mx-auto pt-2 pb-16 gap-4"):
                        build_bulk_import_tab(_sf, _refreshers)

        # ================================================================
        # TAB: BATCH TOOLS
        # ================================================================
        with ui.tab_panel("batch"):
            with ui.column().classes("w-full px-4 pt-6 pb-16"):
                build_batch_tab(_sf, _refreshers)

        # ================================================================
        # TAB: TAXONOMY
        # ================================================================
        with ui.tab_panel("taxonomy"):
            _taxonomy_handle = build_taxonomy_tab(
                _sf, refreshers=_refreshers)

        # ================================================================
        # TAB: TAXONWORKS SYNC  (#149)
        # ================================================================
        # Built only when a connection is configured — same condition as the tab itself,
        # or the panel would exist with no tab able to reach it.
        if get_config().taxonworks_enabled:
            with ui.tab_panel("twsync"):
                with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):
                    # Hand a subset of specimens (e.g. "Diverged" for one collection)
                    # straight to Explore's own views — same "drill into another tab"
                    # pattern as _explore_open_spec/_explore_open_event above, just in
                    # the other direction (#149 follow-up).
                    def _twsync_open_explore(groups):
                        _explore_handle["open_groups"](groups)
                        main_tabs.set_value("explore")

                    build_tw_sync_tab(_sf, _refreshers, open_explore=_twsync_open_explore)

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
            build_labels_tab(_sf, refreshers=_refreshers,
                             records_handle=_records_handle, main_tabs=main_tabs)

    # Rebuild + expand the taxonomy tree whenever the user switches to that tab.
    async def _on_tab_change(e):
        if e.value == "taxonomy":
            await _taxonomy_handle["on_shown"]()
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

    # #172: clicking a scope name in the unsaved-changes banner jumps to that tab.
    # Import & Assign additionally needs the Import tab's own sub-tab switched.
    _banner_scope_tabs = {"Specimen Digitization": "digitize", "Records": "records"}

    def _on_banner_nav(e):
        label = e.args
        if label == "Import & Assign":
            main_tabs.set_value("import")
            _import_sub.set_value("assign")
        elif label in _banner_scope_tabs:
            main_tabs.set_value(_banner_scope_tabs[label])

    ui.on("tp_banner_nav", _on_banner_nav)

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

            # Connection test — the values as *typed*, not as last saved, so a bad URL or
            # token is caught here instead of showing up later as an empty taxon search.
            # Lives in the field's append slot: no extra row, no panel.
            async def _test_tw_connection() -> None:
                _btn.props("loading")
                try:
                    msg = await tw_svc.check_connection(
                        base=tw_base_in.value.strip() or cfg_now.tw_base,
                        token=tw_token_in.value.strip(),
                    )
                    ui.notify(msg, type="positive")
                except tw_svc.TaxonWorksUnreachable as exc:
                    ui.notify(str(exc), type="negative", multi_line=True, timeout=8000)
                finally:
                    _btn.props(remove="loading")

            # On the URL field, not the token: the token input's append slot already holds
            # its password-toggle eye.
            with tw_base_in.add_slot("append"):
                _btn = ui.button(icon="wifi_tethering", on_click=_test_tw_connection) \
                    .props("flat dense round size=sm").tooltip("Test connection")

            tp_base_in = ui.input(
                "TaxonPages base URL",
                value=cfg_now.taxonpages_base,
                placeholder="https://catalog.curculionoidea.org",
            ).classes("w-full mt-2")

            # ── Export privacy: collectors who have not consented (#149) ──
            # Only the undecided middle is configurable. A `confidential` collector is
            # NEVER exported — that is not a preference, so it is deliberately absent
            # here (see AppConfig.tw_export_nonconsent). A withheld name is written as
            # no value at all, never a placeholder.
            ui.label("TaxonWorks export: Manage privacy consent").classes("text-sm font-medium mt-3")
            ui.label(
                "If a person is marked \"confidential\", no specimen collected by "
                "that person will be exported. Select the privacy level for persons "
                "who have not explicitly consented to data sharing"
            ).classes("text-xs mb-1").style("color:var(--tp-base-soft)")
            nonconsent_sel = ui.select(
                {
                    "name_removed": "Export the record with their name removed",
                    "consented_only": "Export only data where the collector has consented",
                },
                value=cfg_now.tw_export_nonconsent or "name_removed",
            ).props("dense outlined").classes("w-full")

            ui.separator().classes("my-3")

            # ── Plant names (WCVP) ───────────────────────────────────────
            # The installed release is read from the index's own meta table — no network.
            # The update check is on demand only: this app must launch offline (db_safety
            # runs before the UI serves), so nothing here may touch the network at startup.
            ui.label("Plant names (WCVP)").classes("text-sm font-medium mb-1")
            ui.label(
                "Offline checklist used to import plant names for biological associations. "
                "Names are not part of your database, just as the names from TaxonWorks they "
                "are imported on demand."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            # The same row shape as a Name-datasets row: an installed icon, the facts
            # (version · names · licence), and a ⋮ menu holding the actions. WCVP *is* a name
            # source (it lives in data/name_sources/wcvp), so it should not look like a
            # different kind of thing — and its actions, Remove above all, should not stand on
            # the page as permanent buttons.
            with ui.row().classes("w-full items-center gap-2"):
                _wcvp_icon = ui.icon("check_circle").classes("text-positive")
                ui.label("WCVP").classes("text-xs font-medium")
                _wcvp_status = ui.label().classes("text-xs")
                ui.space()
                with ui.button(icon="more_vert").props("flat dense round size=sm"):
                    with ui.menu() as _wcvp_menu:
                        # The label changes with install state (download → re-download), and a
                        # MenuItem is not a TextElement — its label is baked in at construction.
                        # So carry a ui.label inside it and update that.
                        _wcvp_install_item = ui.menu_item(
                            # The lambda must RETURN the coroutine so NiceGUI awaits it. The
                            # old form — lambda: (menu.close(), _wcvp_install()) — returned a
                            # TUPLE, so the coroutine was created and never awaited and the item
                            # silently did nothing. (menu_item auto-closes; close() is not
                            # needed. A bare reference would NameError: these handlers are
                            # defined below.)
                            on_click=lambda: _wcvp_install(),
                        )
                        with _wcvp_install_item:
                            _wcvp_install_lbl = ui.label("Download and install")
                        _wcvp_check_item = ui.menu_item(
                            "Check for a new release",
                            on_click=lambda: _wcvp_check_update(),
                        )
                        _wcvp_check_item.tooltip("Downloads ~32 KB, not the whole archive")
                        ui.separator()
                        _wcvp_remove_item = ui.menu_item(
                            "Remove",
                            on_click=lambda: _wcvp_remove(),
                        ).classes("text-negative")

            # Progress / update-check line. It is empty most of the time, and an empty
            # label still reserves its line — which was most of this card's dead space.
            # Bind its visibility to its own text so it occupies nothing until it has
            # something to say.
            _wcvp_remote = ui.label().classes("text-xs mt-1 break-all")
            _wcvp_remote.bind_visibility_from(_wcvp_remote, "text", backward=bool)
            # A dialog must be created in a LIVE slot, never inside a menu-item handler: a menu
            # item's slot is the q-menu, which has already closed by then, so the dialog lands
            # in a dead container and the click silently does nothing at all.
            _wcvp_dialogs = ui.element("div")

            def _wcvp_remove() -> None:
                with _wcvp_dialogs:
                    dlg = ui.dialog()
                with dlg, ui.card():
                    ui.label("Remove the WCVP index?").classes("font-medium mb-2")
                    ui.label(
                        "The downloaded archive and the index built from it are deleted "
                        "(~360 MB). Plant names already imported stay in the database — they "
                        "are local taxon rows now. It can be downloaded again at any time."
                    ).classes("text-xs mb-3").style("color:var(--tp-base-soft)")
                    with ui.row().classes("gap-2 justify-end w-full"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        def _go():
                            wcvp_svc.uninstall()
                            dlg.close()
                            _wcvp_remote.set_text("")
                            _wcvp_refresh_installed()
                            ui.notify("WCVP index removed.", type="positive")

                        ui.button("Remove", icon="delete", on_click=_go) \
                            .props("color=negative")
                dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                dlg.open()

            def _wcvp_refresh_installed() -> str | None:
                """Installed release label, or None. Reads the index file, never the network."""
                try:
                    db = wcvp_svc.open_index()
                except wcvp_svc.IndexMissing:
                    _wcvp_icon.props("name=error_outline")
                    _wcvp_icon.classes(replace="text-warning")
                    _wcvp_status.set_text("Not installed — plant search is unavailable.")
                    _wcvp_status.style("color:var(--tp-base-soft)")
                    _wcvp_install_lbl.set_text("Download and install")
                    _wcvp_remove_item.set_visibility(False)
                    return None
                meta = wcvp_svc.index_meta(db)
                db.close()
                _wcvp_icon.props("name=check_circle")
                _wcvp_icon.classes(replace="text-positive")
                _wcvp_status.set_text(
                    f"{meta.get('label', 'WCVP')} · {int(meta.get('rows', 0)):,} names · "
                    f"{meta.get('license', '')}"
                )
                _wcvp_status.style("color:var(--tp-base-soft)")
                _wcvp_install_lbl.set_text("Re-download and rebuild")
                _wcvp_remove_item.set_visibility(True)
                return meta.get("version")

            async def _wcvp_install() -> None:
                """Fetch Kew's archive and build the index into this collection's data folder.

                The index lives beside the collection it serves, so each data folder needs its
                own — and the user must never have to move a file or run a script to get one.
                build_index() writes to a temp file and atomically replaces the target, so a
                failed download or a corrupt archive leaves an existing index untouched.
                """
                _wcvp_install_item.disable()
                _wcvp_check_item.disable()

                # Progress is written from a worker thread; the UI reads it on a timer. Never
                # touch UI elements from the thread itself.
                state = {"phase": "download", "done": 0, "total": None}

                def _progress(phase: str, done: int, total: int | None) -> None:
                    state["phase"], state["done"], state["total"] = phase, done, total

                def _tick() -> None:
                    if state["phase"] == "download":
                        # Name the URL, and report the size the SERVER gives us. The archive
                        # is whatever Kew is serving today; a size baked into this source
                        # would be a guess that goes stale at the next release.
                        done, total = state["done"], state["total"]
                        if total:
                            got = f"{100 * done / total:.0f}% of {total / 1e6:.0f} MB"
                        else:
                            got = f"{done / 1e6:.0f} MB"
                        _wcvp_remote.set_text(
                            f"Downloading from {wcvp_svc.WCVP_DWCA_URL} — {got}")
                    else:
                        _wcvp_remote.set_text("Building the index… (about 15 seconds)")
                    _wcvp_remote.style("color:var(--tp-base-soft)")

                timer = ui.timer(0.3, _tick)
                try:
                    report = await run.io_bound(
                        lambda: wcvp_svc.install(progress=_progress)
                    )
                except Exception as exc:
                    _wcvp_remote.set_text(f"Install failed: {exc}")
                    _wcvp_remote.style("color:var(--tp-danger)")
                    return
                finally:
                    timer.deactivate()
                    timer.delete()          # per the dialog timer-leak rule
                    _wcvp_install_item.enable()
                    _wcvp_check_item.enable()

                _wcvp_refresh_installed()
                # No restart needed: the taxon widgets open the index per search rather than
                # caching a handle (#104), so the next plant search picks this up.
                _wcvp_remote.set_text(
                    f"Installed {report.meta.label} — {report.rows:,} names. "
                    "Plant search is ready."
                )
                _wcvp_remote.style("color:var(--tp-base)")
                ui.notify(
                    f"WCVP installed ({report.rows:,} names). Plant search is ready.",
                    type="positive", timeout=0, close_button="Got it",
                )

            _wcvp_refresh_installed()

            # ── Name datasets (experimental) ──────────────────────────────
            # A user-added Darwin Core Archive, searched LAST (after local / TW / WCVP). The
            # archive states its own nomenclaturalCode and columns (meta.xml), so nothing here
            # is configured by hand — the file is copied into data/name_sources/<slug>/ and
            # indexed. See services/name_source.py + services/datasets.py.
            #
            # UI shape is deliberate. Adding a dataset happens ONCE; importing every name is a
            # rare, heavy, one-way write. So the add flow lives in a dialog behind a small
            # button (a full-width drop zone shouted the least-used control on the page), and
            # "Import all" sits in a per-row ⋮ menu rather than beside the row as a blue button
            # — where it read as the confirm action for the install that had just finished.
            ui.separator().classes("my-3")
            with ui.row().classes("items-center gap-2 mb-1"):
                ui.label("Add more taxonomy import checklists").classes("text-sm font-medium")
                ui.label("EXPERIMENTAL").classes("text-xs px-1 rounded").style(
                    "background:#fef9c3; color:#854d0e; font-weight:700;")
            ui.label(
                "Add more offline checklists (Darwin Core Archives) to import names from — "
                "e.g. a beetle catalogue. Just as with WCVP, names are not automatically "
                "imported into your collection database, they are imported on demand. "
                "Searched last, after the local database, TaxonWorks and WCVP. Experimental, "
                "as the importer was built around one specific dataset and may choke on other "
                "datasets. You may need to adjust the importer on your own if you need to add "
                "another dataset. The expected structure is following the Darwin Core Archive "
                "from WCVP."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            _ds_list = ui.column().classes("w-full gap-1")
            # Dialogs opened from a row's ⋮ menu MUST be created in this host, not inside the
            # click handler. NiceGUI attaches a new element to the slot its creator belongs to,
            # and a menu item's slot is the q-menu — which has just closed by then. A dialog
            # built there lands in a dead container and never renders: the click silently did
            # nothing at all (no error, no dialog). Anchoring them here keeps the slot alive.
            _ds_dialogs = ui.element("div")

            def _ds_refresh() -> None:
                _ds_list.clear()
                datasets = ds_svc.list_datasets()
                if not datasets:
                    with _ds_list:
                        ui.label("No datasets added.").classes("text-xs").style(
                            "color:var(--tp-base-soft)")
                    return
                for ds in datasets:
                    with _ds_list:
                        with ui.row().classes("w-full items-center gap-2"):
                            if not ds.installed:
                                # Registered but not built: offer Rebuild, never Import all —
                                # importing from an index that does not exist can only fail.
                                ui.icon("error_outline").classes("text-negative")
                                ui.label(f"{ds.label} — index missing").classes("text-xs")
                            else:
                                db = ds.open()
                                try:
                                    _, total = ns_svc.count(db, ds.spec)
                                finally:
                                    db.close()
                                ui.icon("check_circle").classes("text-positive")
                                ui.label(f"{ds.label}").classes("text-xs font-medium")
                                ui.label(f"{ds.code} · {total:,} names").classes(
                                    "text-xs").style("color:var(--tp-base-soft)")
                            ui.space()
                            with ui.button(icon="more_vert").props("flat dense round size=sm"):
                                with ui.menu() as menu:
                                    if ds.installed:
                                        ui.menu_item(
                                            "Import all names…",
                                            on_click=lambda d=ds: _ds_import_all(d),
                                        )
                                    ui.menu_item(
                                        "Rebuild index",
                                        # async → must be awaited; return the coroutine.
                                        on_click=lambda d=ds: _ds_rebuild(d),
                                    )
                                    ui.separator()
                                    ui.menu_item(
                                        "Remove dataset…",
                                        on_click=lambda d=ds: _ds_remove(d),
                                    ).classes("text-negative")

            # ── Add dataset (dialog) ──────────────────────────────────────
            def _ds_add_dialog() -> None:
                with _ds_dialogs:
                    dlg = ui.dialog()
                with dlg, ui.card().classes("min-w-[460px] gap-2"):
                    ui.label("Add a name dataset").classes("section-label")
                    hint = ui.label(
                        "Choose a Darwin Core Archive (.zip). It is copied into this "
                        "collection's data folder and indexed. The archive declares its own "
                        "nomenclatural code and columns, so there is nothing to configure."
                    ).classes("text-xs").style("color:var(--tp-base-soft)")

                    up_area = ui.column().classes("w-full")
                    busy = ui.row().classes("w-full items-center gap-2")
                    busy.set_visibility(False)
                    with busy:
                        ui.spinner(size="sm")
                        busy_lbl = ui.label("Copying the archive…").classes("text-xs")

                    done = ui.column().classes("w-full gap-1")
                    done.set_visibility(False)

                    state = {"rows": 0}

                    def _tick():
                        # The total is unknown until the archive is read, so this counts up.
                        # An index build that shows NOTHING reads as a hang (WCVP is 1.45 M
                        # rows) — which is exactly what it looked like before.
                        if state["rows"]:
                            busy_lbl.set_text(f"Indexing… {state['rows']:,} names read")

                    timer = ui.timer(0.2, _tick, active=False)

                    async def _on_upload(e) -> None:
                        name, content = e.name, e.content.read()
                        up_area.set_visibility(False)
                        hint.set_visibility(False)
                        busy.set_visibility(True)
                        busy_lbl.set_text("Copying the archive…")
                        timer.activate()
                        try:
                            ds, report = await run.io_bound(
                                ds_svc.install, content, name,
                                progress=lambda n: state.__setitem__("rows", n),
                            )
                        except Exception as exc:      # noqa: BLE001 — the message IS the product
                            timer.deactivate()
                            busy.set_visibility(False)
                            with done:
                                ui.label(f"Could not add {name}").classes(
                                    "text-sm font-medium").style("color:var(--tp-danger)")
                                ui.label(str(exc)).classes("text-xs")
                            done.set_visibility(True)
                            return
                        finally:
                            timer.deactivate()

                        busy.set_visibility(False)
                        with done:
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("check_circle").classes("text-positive")
                                ui.label(f"{ds.label} installed").classes(
                                    "text-sm font-medium")
                            ui.label(
                                f"{report.rows:,} names · {ds.code} · "
                                f"{report.replaced:,} synonyms"
                            ).classes("text-xs").style("color:var(--tp-base-soft)")
                            ui.label(
                                "These names now appear in the taxon search, after the local "
                                "database, TaxonWorks and WCVP. Picking one imports it (and its "
                                "parent ranks) — you do not need to import anything up front."
                            ).classes("text-xs").style("color:var(--tp-base-soft)")
                        done.set_visibility(True)
                        _ds_refresh()

                    with up_area:
                        ui.upload(on_upload=_on_upload, auto_upload=True, max_files=1) \
                            .props('accept=".zip" flat dense').classes("w-full")

                    with ui.row().classes("w-full justify-end mt-1"):
                        ui.button("Close", on_click=dlg.close).props("flat")

                # Per the dialog timer-leak rule: the timer dies with the dialog.
                dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                dlg.open()

            async def _ds_rebuild(ds) -> None:
                n = ui.notification(f"Rebuilding {ds.label}…", spinner=True, timeout=None)
                # progress fires on the io_bound worker thread, so it may only write
                # state; a ui.timer reads it and updates the notification (never the
                # thread itself — same rule as the WCVP install above). n.message is an
                # attribute: assign it, never call it (calling the str raised
                # "'str' object is not callable").
                state = {"rows": 0}
                timer = ui.timer(0.2, lambda: setattr(
                    n, "message", f"Indexing… {state['rows']:,} names read"))
                try:
                    report = await run.io_bound(
                        ds_svc.rebuild, ds,
                        progress=lambda k: state.__setitem__("rows", k),
                    )
                except Exception as exc:      # noqa: BLE001
                    n.dismiss()
                    ui.notify(f"Rebuild failed: {exc}", type="negative",
                              timeout=0, close_button="Got it")
                    return
                finally:
                    timer.deactivate()
                    timer.delete()          # per the dialog timer-leak rule
                n.dismiss()
                ui.notify(f"Rebuilt {ds.label} — {report.rows:,} names.", type="positive")
                _ds_refresh()

            def _ds_remove(ds) -> None:
                with _ds_dialogs:
                    dlg = ui.dialog()
                with dlg, ui.card():
                    ui.label(f"Remove “{ds.label}”?").classes("font-medium mb-2")
                    ui.label(
                        "The archive and its index are deleted. Names already imported from "
                        "it stay in the database — they are local taxon rows now."
                    ).classes("text-xs mb-3").style("color:var(--tp-base-soft)")
                    with ui.row().classes("gap-2 justify-end w-full"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        def _go():
                            ds_svc.remove(ds)
                            dlg.close()
                            _ds_refresh()
                            ui.notify(f"{ds.label} removed.", type="positive")

                        ui.button("Remove", icon="delete", on_click=_go) \
                            .props("color=negative")
                dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                dlg.open()

            def _ds_import_all(ds) -> None:
                """Import EVERY name in the dataset. Warned first — it is a bulk write."""
                try:
                    db = ds.open()
                    try:
                        importable, total = ns_svc.count(db, ds.spec)
                    finally:
                        db.close()
                except Exception as exc:      # noqa: BLE001
                    ui.notify(f"Cannot read {ds.label}: {exc}", type="negative")
                    return

                with _ds_dialogs:
                    dlg = ui.dialog()
                with dlg, ui.card().classes("min-w-[440px]"):
                    ui.label(f"Import all names from “{ds.label}”?").classes(
                        "font-medium mb-2")
                    ui.label(
                        f"This creates a local taxon row for each of the {importable:,} "
                        f"importable names (of {total:,}), plus their parent ranks. It is a "
                        f"large, one-way write to your database: there is no undo, and the "
                        f"Taxonomy tree will contain the whole checklist whether or not you "
                        f"hold specimens of those taxa."
                    ).classes("text-xs mb-2").style("color:var(--tp-danger)")
                    ui.label(
                        "You do not need this to record specimens: picking a name in the "
                        "taxon search imports it (and its parents) on demand. Import all is "
                        "for working offline from a complete checklist."
                    ).classes("text-xs mb-3").style("color:var(--tp-base-soft)")
                    prog = ui.linear_progress(value=0, show_value=False).classes("w-full")
                    prog.set_visibility(False)
                    prog_lbl = ui.label("").classes("text-xs")
                    with ui.row().classes("gap-2 justify-end w-full mt-2") as btn_row:
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def _go():
                            btn_row.set_visibility(False)
                            prog.set_visibility(True)
                            state = {"done": 0, "total": importable or 1}

                            def _progress(done, total):
                                state["done"], state["total"] = done, total

                            timer = ui.timer(0.2, lambda: (
                                prog.set_value(state["done"] / max(state["total"], 1)),
                                prog_lbl.set_text(
                                    f"{state['done']:,} / {state['total']:,} names…"),
                            ))

                            def _work():
                                with _sf() as session:
                                    with session.begin():
                                        return ds_svc.import_all(
                                            session, ds, progress=_progress)

                            try:
                                report = await run.io_bound(_work)
                            except Exception as exc:   # noqa: BLE001
                                ui.notify(f"Import failed: {exc}", type="negative",
                                          timeout=0, close_button="Got it")
                                dlg.close()
                                return
                            finally:
                                timer.deactivate()
                                timer.delete()     # per the dialog timer-leak rule

                            dlg.close()
                            msg = (f"{ds.label}: imported {report.imported:,} names "
                                   f"({report.created:,} new taxon rows)")
                            if report.refused or report.failed:
                                msg += (f" · {report.refused:,} refused, "
                                        f"{report.failed:,} failed")
                            ui.notify(msg, type="positive", timeout=0,
                                      close_button="Got it")
                            if report.reconstructed_species:
                                # A well-formed archive reconstructs NOTHING. If this fires, the
                                # archive is missing species rows its own subspecies depend on —
                                # say so plainly instead of quietly papering over it.
                                ui.notify(
                                    f"{report.reconstructed_species:,} names had no species "
                                    "parent in the archive, so it was reconstructed from the "
                                    "trinomial (authorship left blank). The archive should "
                                    "supply those species rows.",
                                    type="warning", timeout=0, close_button="Got it",
                                )
                            for r in report.refusals[:5]:
                                ui.notify(r, type="warning", timeout=8000)
                            # The Taxonomy tab's refreshers live in its own scope; the
                            # registry is how any tab reaches them (see _refreshers).
                            fn = _refreshers.get("taxonomy_tree")
                            if fn:
                                fn()

                        ui.button("Import all", icon="download", on_click=_go) \
                            .props("color=negative")
                dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                dlg.open()

            ui.button("Add dataset…", icon="add", on_click=_ds_add_dialog) \
                .props("flat dense no-caps size=sm").classes("mt-1")

            _ds_refresh()
            async def _wcvp_check_update() -> None:
                # Re-read the index first: it may have been rebuilt since this page loaded,
                # and reporting "a newer release is available" for one already installed
                # would be worse than not checking at all.
                installed = _wcvp_refresh_installed()
                _wcvp_remote.set_text("Checking…")
                _wcvp_remote.style("color:var(--tp-base-soft)")
                try:
                    # ~32 KB: eml.xml is the archive's first zip entry and Kew honours Range.
                    meta = await run.io_bound(wcvp_svc.latest_release)
                except Exception as exc:  # network or a changed archive layout — say which
                    _wcvp_remote.set_text(f"Could not check: {exc}")
                    _wcvp_remote.style("color:var(--tp-danger)")
                    return
                if installed is None:
                    _wcvp_remote.set_text(f"Kew is serving {meta.label}.")
                elif meta.version == installed:
                    _wcvp_remote.set_text(f"Up to date — Kew is serving {meta.label}.")
                else:
                    _wcvp_remote.set_text(
                        f"A newer release is available: {meta.label} — "
                        "press “Re-download and rebuild”."
                    )
                _wcvp_remote.style("color:var(--tp-base-soft)")

            ui.separator().classes("my-3")

            # ── Default collection ───────────────────────────────────────
            ui.label("Default collection").classes("text-sm font-medium mb-1")
            ui.label(
                "The home collection stamped on every new specimen (its "
                "repository_id) and used for the catalog-number prefix. Pick one "
                "from the Collections vocabulary; edit a collection's codes in "
                "Controlled Vocabularies."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            # The default is a flag on the repository vocab (repository.is_default,
            # #83), not a string in config.json — so membership and the printed label
            # derive from one chosen row (same DB-integrity rule as person defaults).
            def _repo_opts() -> dict:
                with _sf() as s:
                    return {
                        r.collection_code: f"{r.collection_code} — {r.collection_full_name}"
                        for r in repo_svc.list_repositories(s)
                    }

            with _sf() as _s_def:
                _cur_default = repo_svc.get_default(_s_def)
                _default_code0 = _cur_default.collection_code if _cur_default else None
                _default_inst0 = (_cur_default.institution_code or "") if _cur_default else ""

            _repo_initial_opts = _repo_opts()
            default_collection_sel = ui.select(
                options=_repo_initial_opts,
                value=_default_code0 if _default_code0 in _repo_initial_opts else None,
                label="Default collection (from Collections vocabulary)",
                with_input=True,
            ).classes("w-full mt-1")
            ui.timer(2.0, lambda: default_collection_sel.set_options(_repo_opts()))

            # Read-only echo of the selected collection's codes — edited in the
            # Collections vocabulary card, never here.
            institution_code_in = ui.input(
                "institutionCode", value=_default_inst0,
            ).props("readonly outlined dense").classes("w-full mt-1")
            collection_code_in = ui.input(
                "collectionCode", value=_default_code0 or "",
            ).props("readonly outlined dense").classes("w-full mt-2")

            def _on_default_collection(e):
                code = e.value
                if not code:
                    institution_code_in.value = ""
                    collection_code_in.value = ""
                    return
                with _sf() as s:
                    r = next((x for x in repo_svc.list_repositories(s)
                              if x.collection_code == code), None)
                if r is not None:
                    collection_code_in.value = r.collection_code
                    institution_code_in.value = r.institution_code or ""
            default_collection_sel.on_value_change(_on_default_collection)

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

            # ── Launch mode ───────────────────────────────────────────────
            ui.label("Launch mode").classes("text-sm font-medium mb-1")
            ui.label(
                "How the app opens on startup. Browser tab uses your default "
                "browser. App window opens a chromeless, app-like window "
                "(Chrome/Chromium/Edge/Brave) — still a real browser, so PDFs, "
                "media, and the unsaved-changes guard all keep working. Takes "
                "effect the next time you start the app."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            def _warn_if_no_app_browser(e) -> None:
                # Warn the moment 'App window' is picked on a machine with no
                # Chromium-class browser, so the user isn't surprised at next launch
                # by a plain tab. Firefox has no --app equivalent, so it cannot
                # provide app mode (see launcher).
                if e.value == "app" and not launcher.chromium_available():
                    ui.notify(
                        "App window needs Chrome, Chromium, Edge or Brave — none is "
                        "installed. The app will open in a browser tab until you "
                        "install one.",
                        type="warning", multi_line=True, timeout=10000,
                        close_button="OK")

            launch_mode_toggle = ui.toggle(
                {"tab": "Browser tab", "app": "App window"},
                value=get_config().launch_mode,
                on_change=_warn_if_no_app_browser,
            ).props("no-caps")

            ui.separator().classes("my-3")

            # ── Paper format ──────────────────────────────────────────────
            ui.label("Label sheet paper").classes("text-sm font-medium mb-1")
            ui.label(
                "The page size the label sheet prints on. A4 is the world default; "
                "Letter is used in the US, Canada and Mexico. The labels themselves "
                "are unchanged — only the page they tile onto."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")
            paper_format_toggle = ui.toggle(
                {"A4": "A4", "Letter": "US Letter"},
                value=get_config().paper_format,
            ).props("no-caps")

            ui.separator().classes("my-3")

            # ── Printed-label borders (per type) ──────────────────────────
            ui.label("Printed-label borders").classes("text-sm font-medium mb-1")
            ui.label(
                "A thin black cut-guide line around each label, or none. Set per "
                "label type. Applies to the Labels-tab batch sheet and the print queue."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")
            _cfg_lb = get_config()
            _border_opts = {"black": "Black", "none": "None"}
            with ui.row().classes("gap-4 items-center flex-wrap"):
                with ui.column().classes("gap-0"):
                    ui.label("Data").classes("text-xs").style("color:var(--tp-base-soft)")
                    label_border_data_tog = ui.toggle(
                        _border_opts, value=_cfg_lb.label_border_data).props("no-caps dense")
                with ui.column().classes("gap-0"):
                    ui.label("Determination").classes("text-xs").style("color:var(--tp-base-soft)")
                    label_border_det_tog = ui.toggle(
                        _border_opts, value=_cfg_lb.label_border_determination).props("no-caps dense")
                with ui.column().classes("gap-0"):
                    ui.label("Identifier").classes("text-xs").style("color:var(--tp-base-soft)")
                    label_border_id_tog = ui.toggle(
                        _border_opts, value=_cfg_lb.label_border_identifier).props("no-caps dense")

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
            # _license_options(), not the bare list: a config.json edited by hand can hold a
            # licence outside LICENSE_OPTIONS, and ui.select RAISES on a value it does not
            # know — which would make the whole Settings tab fail to render (#64).
            from app.ui.media_panel import _license_options
            _cur_license = (get_config().default_license or "").strip()
            default_license_sel = ui.select(
                _license_options(_cur_license), value=_cur_license,
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
                # Stored exactly as typed — NO `or cfg.<old>` fallback. Falling back made
                # these fields unclearable: blanking one silently re-saved the previous
                # server/URL, so the app kept talking to a TaxonWorks the user had already
                # moved away from. Empty means "not configured" (cfg.taxonworks_enabled).
                cfg.tw_base               = tw_base_in.value.strip()
                cfg.tw_token              = tw_token_in.value.strip()
                cfg.taxonpages_base       = tp_base_in.value.strip()
                cfg.tw_export_nonconsent  = nonconsent_sel.value or "name_removed"
                cfg.map_default_layer     = map_layer_sel.value or "street"
                cfg.digitize_layout       = digitize_layout_toggle.value or "normal"
                cfg.launch_mode           = launch_mode_toggle.value or "tab"
                cfg.default_license       = default_license_sel.value or ""
                cfg.paper_format          = paper_format_toggle.value or "A4"
                cfg.label_border_data          = label_border_data_tog.value or "black"
                cfg.label_border_determination = label_border_det_tog.value or "black"
                cfg.label_border_identifier    = label_border_id_tog.value or "black"
                with _sf() as _s:
                    with _s.begin():
                        # The default collection is a flag on the repository vocab
                        # (#83), persisted in the DB, not in config.json.
                        _sel_code = default_collection_sel.value
                        if _sel_code:
                            _sel_repo = next(
                                (x for x in repo_svc.list_repositories(_s)
                                 if x.collection_code == _sel_code), None)
                            if _sel_repo is not None:
                                repo_svc.set_default(_s, _sel_repo.id)
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
                _digitize_handle["reset_layout"]()
                # Live-refresh the TaxonWorks tab's Collections report (eligible/not
                # eligible split + the privacy-consent status line both read config)
                # rather than leaving it showing whatever was true at page load.
                _refreshers.get("twsync") and _refreshers["twsync"]()
                settings_dialog.close()
                ui.notify("Settings saved.", type="positive")

            with ui.row().classes("mt-4 gap-2 justify-end w-full"):
                ui.button("Cancel", on_click=settings_dialog.close).props("flat")
                ui.button("Save", on_click=_save_settings).props("color=secondary")

    def _open_settings():
        # Re-read from disk: the cached instance can be stale (edited on disk, or by a
        # second window), and Save writes back every field — so seeding from the cache
        # would both show and re-save values the file no longer holds.
        cfg = reload_config()
        tw_base_in.value        = cfg.tw_base
        tw_token_in.value       = cfg.tw_token
        tp_base_in.value        = cfg.taxonpages_base
        nonconsent_sel.value    = cfg.tw_export_nonconsent or "name_removed"
        # Collection identity comes from the flagged default repository (#83).
        with _sf() as _s_d:
            _def = repo_svc.get_default(_s_d)
            _def_code = _def.collection_code if _def else None
            _def_inst = (_def.institution_code or "") if _def else ""
        default_collection_sel.set_options(_repo_opts())
        default_collection_sel.value = _def_code
        institution_code_in.value = _def_inst
        collection_code_in.value  = _def_code or ""
        map_layer_sel.value     = cfg.map_default_layer or "street"
        digitize_layout_toggle.value = cfg.digitize_layout or "normal"
        launch_mode_toggle.value = cfg.launch_mode or "tab"
        default_license_sel.value = cfg.default_license or ""
        with _sf() as _s:
            _idby, _recby, _rights = pd_svc.get_defaults(_s)
        idby_state["set_value"](_idby)
        recby_state_cfg["set_value"](_recby)
        rights_state_cfg["set_value"](_rights)
        for code, cb in _code_cbs.items():
            cb.value = code in cfg.bio_assoc_default_codes
        settings_dialog.open()

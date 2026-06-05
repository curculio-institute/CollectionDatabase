"""Taxon-search widget.

Search order:
  1. Local database — results appear immediately as you type (debounce 150 ms).
  2. TaxonWorks     — triggered by clicking "Search TaxonWorks for '…'" at the
                      bottom of the dropdown, or automatically when the local DB
                      returns no matches.

On TW selection the taxon is imported (get_or_create_from_tw_data) into the
local DB before the state is updated, so subsequent lookups always hit locally.

Usage (inside a @ui.page function):
    taxon_state = build_taxon_search(session_factory)
    # taxon_state['taxon_id'] is None until the user selects something.

Optional callback:
    taxon_state = build_taxon_search(session_factory, on_select=lambda tid: ...)
"""
from __future__ import annotations

import asyncio
import html as _html_mod
import re

from nicegui import ui

import app.services.taxonworks as tw_svc
import app.services.taxa as svc_taxa


def _strip_tw_info_badges(html: str) -> str:
    """Remove informational feedback-thin badges from TW label_html.

    Strips spans that carry BOTH feedback-thin AND any of:
      feedback-info     → rank label ("species")
      feedback-secondary → genus/subgenus context chip
      feedback-notice   → original combination ("Curculio sulcatus")

    Uses lookaheads so class-attribute order doesn't matter.
    Deliberately leaves &#10003; ✓ and &#10060; ✗ entities untouched —
    those are plain HTML entities, not spans.
    """
    for cls in ("feedback-info", "feedback-secondary", "feedback-notice"):
        html = re.sub(
            rf'<span(?=[^>]*\b{cls}\b)(?=[^>]*\bfeedback-thin\b)[^>]*>.*?</span>',
            "", html, flags=re.DOTALL,
        )
    return html


def _render_tw_label(r: dict, valid_name: str = "") -> str:
    """Clean a TW autocomplete label_html for display.

    Strips rank / context / original-combination badges; keeps the ✓/✗ entity.
    When valid_name is supplied (for synonym entries) appends '= <i>Name</i>'.
    """
    raw = r.get("label_html") or _html_mod.escape(r.get("label") or "")
    cleaned = _strip_tw_info_badges(raw)
    # Collapse &nbsp; separators left by removed badges
    cleaned = re.sub(r"(&nbsp;\s*)+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if valid_name:
        # ✗ is already at the end; append the valid name with ✓
        cleaned = f"{cleaned} = <i>{_html_mod.escape(valid_name)}</i> &#10003;"
    return cleaned


def _local_item_html(name: str, *, is_synonym: bool, accepted: str | None) -> str:
    """Build local-taxon HTML matching TW's ✗ / = Valid Name ✓ style."""
    n = f"<i>{_html_mod.escape(name)}</i>"
    if not is_synonym:
        return n
    if accepted:
        return f"{n} &#10060; = <i>{_html_mod.escape(accepted)}</i> &#10003;"
    return f"{n} &#10060;"


_TW_CSS = """
<style>
/* ── TaxonWorks result-item rendering ─────────────────────────────── */
.tw-result               { font-size:.9rem; line-height:1.5; }
.tw-result i             { font-style:italic; }
.tw-result mark          { background:#fde68a; border-radius:2px; padding:0 1px; }
.dark .tw-result mark    { background:#854d0e; color:#fff; }
.feedback                { display:inline-block; font-size:.67rem; font-weight:600;
                           padding:1px 5px; border-radius:3px; margin-left:4px;
                           vertical-align:middle; line-height:1.6; }
.feedback-thin           { padding:0 4px; font-size:.62rem; }
.feedback-info           { background:#dbeafe; color:#1e40af; }
.feedback-secondary      { background:#f3f4f6; color:#6b7280; }
.feedback-notice         { background:#dcfce7; color:#166534; }
.feedback-warning        { background:#fef9c3; color:#854d0e; }
.feedback-danger         { background:#fee2e2; color:#991b1b; }
.dark .feedback-info     { background:#1e3a5f; color:#93c5fd; }
.dark .feedback-secondary{ background:#303030; color:#9ca3af; }
.dark .feedback-notice   { background:#14432a; color:#86efac; }

/* ── selected-taxon chip ───────────────────────────────────────────── */
.taxon-chip              { display:inline-flex; align-items:center; gap:6px;
                           background:rgb(240,249,255); border:1px solid rgb(186,230,253);
                           border-radius:6px; padding:4px 10px; }
.taxon-chip i            { font-style:italic; color:rgb(3,105,161); }
.taxon-chip .chip-clear  { cursor:pointer; color:rgb(156,163,175); font-size:.8rem; margin-left:2px; }
.taxon-chip .chip-clear:hover { color:rgb(220,38,38); }
.dark .taxon-chip        { background:rgba(14,165,233,.1); border-color:rgba(14,165,233,.3); }
.dark .taxon-chip i      { color:rgb(14,165,233); }
.dark .taxon-chip .chip-clear { color:rgb(200,200,200); }

/* ── dropdown ──────────────────────────────────────────────────────── */
.tw-dropdown             { position:absolute; left:0; right:0; top:calc(100% + 2px); z-index:9999;
                           background:rgb(255,255,255); border:1px solid rgb(203,213,225);
                           border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,.10);
                           max-height:340px; overflow-y:auto; display:none; }
.dark .tw-dropdown       { background:rgb(38,38,38); border-color:rgb(55,55,55);
                           box-shadow:0 8px 24px rgba(0,0,0,.4); }
.tw-dropdown-item        { padding:10px 16px; cursor:pointer;
                           border-bottom:1px solid rgb(243,244,246); transition:background .1s; }
.dark .tw-dropdown-item  { border-color:rgb(48,48,48); }
.tw-dropdown-item:hover  { background:rgb(245,247,251); }
.dark .tw-dropdown-item:hover { background:rgb(48,48,48); }
.tw-dropdown-empty       { padding:12px 16px; color:rgb(156,163,175);
                           font-size:.85rem; font-style:italic; }
.tw-section-label        { padding:4px 16px 2px; font-size:.65rem; font-weight:700;
                           letter-spacing:.08em; text-transform:uppercase;
                           color:rgb(156,163,175); border-bottom:1px solid rgb(243,244,246); }
.dark .tw-section-label  { border-color:rgb(48,48,48); }
.tw-action-row           { padding:8px 16px; font-size:.82rem; color:rgb(3,105,161);
                           cursor:pointer; border-bottom:none; display:flex;
                           align-items:center; gap:6px; }
.dark .tw-action-row     { color:rgb(14,165,233); }
.tw-action-row:hover     { background:rgb(240,249,255); }
.dark .tw-action-row:hover { background:rgba(14,165,233,.08); }

/* TaxonWorks import items — tinted to signal "will be imported" */
.tw-dropdown-item--import            { background:rgba(3,105,161,.04); }
.dark .tw-dropdown-item--import      { background:rgba(14,165,233,.06); }
.tw-dropdown-item--import:hover      { background:rgba(3,105,161,.10) !important; }
.dark .tw-dropdown-item--import:hover{ background:rgba(14,165,233,.13) !important; }
.tw-import-badge { display:inline-flex; align-items:center; gap:2px;
                   background:rgba(3,105,161,.12); color:rgb(3,105,161);
                   border-radius:4px; padding:1px 6px; font-size:.72rem;
                   font-weight:600; margin-right:7px; vertical-align:middle;
                   letter-spacing:.02em; }
.dark .tw-import-badge { background:rgba(14,165,233,.15); color:rgb(14,165,233); }

/* ── focus ring ────────────────────────────────────────────────────── */
.tw-search-wrap .q-field--focused .q-field__control {
  border-color:var(--tp-secondary, rgb(3,105,161)) !important;
  box-shadow:0 0 0 2px rgba(3,105,161,.15) !important;
}
.dark .tw-search-wrap .q-field--focused .q-field__control {
  box-shadow:0 0 0 2px rgba(14,165,233,.2) !important;
}
</style>
"""


def build_taxon_search(session_factory, on_select=None) -> dict:
    """Build the taxon-search widget in the current NiceGUI context.

    Returns a mutable state dict:  {'taxon_id': int | None}

    on_select(taxon_id: int) is called after a taxon is selected.
    """
    ui.add_head_html(_TW_CSS)

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    state: dict = {"taxon_id": None, "_task": None}

    # ── search input + floating dropdown ────────────────────────────
    with ui.element("div").classes("tw-search-wrap").style("position:relative; width:100%"):
        search_inp = (
            ui.input(placeholder="Type genus or species name…")
            .props("clearable outlined dense")
            .classes("w-full")
            .style("font-style:italic")
        )
        dropdown = ui.element("div").classes("tw-dropdown")

    # ── selected-taxon chip (shown after pick) ───────────────────────
    selected_row = ui.element("div").style("display:none; margin-top:6px;")
    with selected_row:
        selected_html = ui.html("").classes("taxon-chip tw-result")

    # ── helpers ──────────────────────────────────────────────────────

    def _hide():
        dropdown.style("display:none")

    def _show():
        dropdown.style("display:block")

    def _set_selected(taxon_id: int, display_html: str):
        state["taxon_id"] = taxon_id
        selected_html.set_content(
            f'{display_html}'
            f'<span class="chip-clear" '
            f'onclick="this.dispatchEvent(new CustomEvent(\'clear\',{{bubbles:true}}))" '
            f'title="Clear">✕</span>'
        )
        selected_row.style(add="display:block;", remove="display:none;")
        search_inp.value = ""
        _hide()
        if on_select:
            on_select(taxon_id)

    def _clear():
        state["taxon_id"] = None
        selected_html.set_content("")
        selected_row.style(add="display:none;", remove="display:block;")

    selected_html.on("clear", lambda _: _clear())

    # ── build dropdown sections ──────────────────────────────────────

    def _build_local_section(local: list) -> None:
        """Render the 'In database' section into the already-cleared dropdown."""
        if not local:
            return
        ui.label("In database").classes("tw-section-label")
        for res in local:
            item = ui.element("div").classes("tw-dropdown-item")
            with item:
                ui.html(_local_item_html(
                    res.label,
                    is_synonym=res.is_synonym,
                    accepted=res.accepted_label,
                ))
            item.on("click", lambda _, r=res: _select_local(r.id, r.label))

    def _select_local(taxon_id: int, label: str):
        _set_selected(taxon_id, f'<i>{_html_mod.escape(label)}</i>')

    # ── TaxonWorks search (appended below local) ─────────────────────

    async def _append_tw_section(term: str) -> None:
        """Fetch TW results and append them below the local section."""
        # Placeholder while loading
        with dropdown:
            tw_sec = ui.element("div")
        with tw_sec:
            ui.label(f"Searching TaxonWorks…").classes("tw-dropdown-empty")

        try:
            results = await tw_svc.search_taxon_names(term)
        except Exception:
            tw_sec.clear()
            return

        tw_sec.clear()
        if not results:
            return

        # Batch-fetch valid names for synonym entries
        valid_name_cache: dict[int, str] = {}
        syn_ids = list({
            r["valid_taxon_name_id"] for r in results
            if r.get("valid_taxon_name_id") and r["valid_taxon_name_id"] != r.get("id")
        })
        if syn_ids:
            async def _get_valid(vid: int) -> tuple[int, str]:
                try:
                    d = await tw_svc.fetch_taxon_name(vid)
                    return vid, (d or {}).get("cached") or ""
                except Exception:
                    return vid, ""
            pairs = await asyncio.gather(*[_get_valid(vid) for vid in syn_ids])
            valid_name_cache = dict(pairs)

        with tw_sec:
            ui.label("TaxonWorks").classes("tw-section-label")
            for r in results:
                vid = r.get("valid_taxon_name_id")
                valid_name = (
                    valid_name_cache.get(vid, "")
                    if vid and vid != r.get("id") else ""
                )
                item = ui.element("div").classes(
                    "tw-result tw-dropdown-item tw-dropdown-item--import"
                )
                with item:
                    ui.html(
                        '<span class="tw-import-badge">✚ add</span>'
                        + _render_tw_label(r, valid_name)
                    )
                item.on("click", lambda _, r=r: asyncio.ensure_future(_on_tw_pick(r)))
        _show()

    async def _on_tw_pick(r: dict):
        tw_id = r["id"]  # import the actual name clicked; get_or_create handles valid-name backfill for synonyms
        try:
            tw_data, otu_id = await asyncio.gather(
                tw_svc.fetch_full_classification(tw_id),
                tw_svc.fetch_otu_id_for_taxon_name(tw_id),
            )
        except Exception as exc:
            ui.notify(f"TaxonWorks fetch failed: {exc}", type="negative")
            return
        if tw_data is None:
            ui.notify("Taxon not found in TaxonWorks.", type="warning")
            return
        try:
            with session_factory() as session:
                with session.begin():
                    taxon = svc_taxa.get_or_create_from_tw_data(
                        session, tw_data, otu_id=otu_id
                    )
                    tid = taxon.id
        except Exception as exc:
            ui.notify(f"Local DB error: {exc}", type="negative")
            return
        _set_selected(tid, r.get("label_html") or r.get("label", ""))

    # ── debounced search on input ────────────────────────────────────

    async def _on_search(e):
        prev = state["_task"]
        if prev and not prev.done():
            prev.cancel()

        term = (e.value or "").strip()
        if len(term) < 2:
            _hide()
            return

        async def _do():
            try:
                await asyncio.sleep(0.15)   # debounce
                local = _with_session(
                    lambda s: svc_taxa.search_taxa_for_display(s, term, limit=10)
                )
                dropdown.clear()
                with dropdown:
                    _build_local_section(local)
                _show()
                # Always search TW — higher taxa (genus, tribe…) may only live there
                await _append_tw_section(term)
            except asyncio.CancelledError:
                pass

        state["_task"] = asyncio.create_task(_do())

    search_inp.on_value_change(_on_search)

    async def _on_blur(_):
        await asyncio.sleep(0.2)
        _hide()

    search_inp.on("blur", _on_blur)

    return state

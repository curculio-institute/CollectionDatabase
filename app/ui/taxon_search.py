"""Taxon-search widget.

States: Empty → Searching → Selected (see docs/design.md §4).

Sources are queried in order; each always runs if listed.
Default order: local DB → TaxonWorks.
Bio-association order: local DB → TaxonWorks → POWO.

State dict: {'taxon_id': int|None, 'label': str, 'clear': callable}
  taxon_id = None   — nothing selected
  taxon_id = -1     — TW/POWO import in progress (do not read yet)
  taxon_id = N > 0  — confirmed local DB id

on_select(taxon_id: int) is called after the DB record is confirmed.
"""
from __future__ import annotations

import asyncio
import html as _html_mod
import re

from nicegui import context as _nicegui_context, ui

import app.services.powo as powo_svc
import app.services.taxonworks as tw_svc
import app.services.taxa as svc_taxa


# ── label helpers ─────────────────────────────────────────────────────────────

def _strip_tw_info_badges(html: str) -> str:
    for cls in ("feedback-info", "feedback-secondary", "feedback-notice"):
        html = re.sub(
            rf'<span(?=[^>]*\b{cls}\b)(?=[^>]*\bfeedback-thin\b)[^>]*>.*?</span>',
            "", html, flags=re.DOTALL,
        )
    return html


def _render_tw_label(r: dict, valid_name: str = "") -> str:
    raw = r.get("label_html") or _html_mod.escape(r.get("label") or "")
    cleaned = _strip_tw_info_badges(raw)
    cleaned = re.sub(r"(&nbsp;\s*)+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if valid_name:
        cleaned = f"{cleaned} = <i>{_html_mod.escape(valid_name)}</i> &#10003;"
    return cleaned


def _local_item_html(
    name: str,
    *,
    is_synonym: bool,
    accepted: str | None,
    nomenclatural_code: str | None = None,
) -> str:
    prefix = "🌿 " if nomenclatural_code == "ICN" else ""
    n = f"{prefix}<i>{_html_mod.escape(name)}</i>"
    if not is_synonym:
        return n
    if accepted:
        return f"{n} &#10060; = <i>{_html_mod.escape(accepted)}</i> &#10003;"
    return f"{n} &#10060;"


def _powo_item_html(r: dict) -> str:
    name    = r.get("name", "")
    auth    = r.get("authors", "")
    family  = r.get("family", "")
    is_syn  = bool(r.get("synonym", False))
    acc_raw = r.get("accepted") or {}
    acc_name = acc_raw.get("name", "")
    acc_auth = acc_raw.get("author", "")

    html = f'🌿 <i>{_html_mod.escape(name)}</i>'
    if auth:
        html += f' {_html_mod.escape(auth)}'
    if is_syn and acc_name:
        html += (
            ' &#10060; = '
            f'<i>{_html_mod.escape(acc_name)}</i>'
            + (f' {_html_mod.escape(acc_auth)}' if acc_auth else "")
            + ' &#10003;'
        )
    elif is_syn:
        html += ' &#10060;'
    if family:
        html += (
            f' <span style="color:var(--tp-base-soft);font-size:.8rem">'
            f'{_html_mod.escape(family)}</span>'
        )
    return html


# ── import badges ─────────────────────────────────────────────────────────────

_TW_BADGE = (
    '<span class="tw-import-badge"'
    ' title="This taxon and its parent taxa were imported from TaxonWorks">'
    '✚ add</span>'
)
_POWO_BADGE = (
    '<span class="powo-import-badge"'
    ' title="This taxon was imported from Plants of the World Online (POWO)">'
    '🌿 add</span>'
)


# ── CSS ───────────────────────────────────────────────────────────────────────

_TW_CSS = """
<style>
/* ── result text ───────────────────────────────────────────────────── */
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

/* ── selected display (replaces the input in Selected state) ───────── */
.tw-selected-display {
  display: none;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(0,0,0,0.24);
  border-radius: 4px;
  min-height: 40px;
  padding: 4px 8px 4px 12px;
  background: white;
  box-sizing: border-box;
  width: 100%;
  cursor: default;
}
.dark .tw-selected-display {
  background: rgb(35,35,35);
  border-color: rgba(255,255,255,0.24);
}
.tw-selected-display:hover { border-color: rgba(0,0,0,0.38); }
.dark .tw-selected-display:hover { border-color: rgba(255,255,255,0.38); }
.tw-selected-content     { flex: 1; min-width: 0; }
.tw-clear-btn            { cursor:pointer; color:rgb(156,163,175); font-size:.85rem;
                           padding:2px 4px; border-radius:2px; flex-shrink:0;
                           line-height:1; }
.tw-clear-btn:hover      { color:rgb(220,38,38); }

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

/* TaxonWorks import items */
.tw-dropdown-item--import            { background:rgba(3,105,161,.04); }
.dark .tw-dropdown-item--import      { background:rgba(14,165,233,.06); }
.tw-dropdown-item--import:hover      { background:rgba(3,105,161,.10) !important; }
.dark .tw-dropdown-item--import:hover{ background:rgba(14,165,233,.13) !important; }
.tw-import-badge { display:inline-flex; align-items:center; gap:2px;
                   background:rgba(3,105,161,.12); color:rgb(3,105,161);
                   border-radius:4px; padding:1px 6px; font-size:.72rem;
                   font-weight:600; margin-right:7px; vertical-align:middle;
                   letter-spacing:.02em; cursor:help; }
.dark .tw-import-badge { background:rgba(14,165,233,.15); color:rgb(14,165,233); }

/* POWO import items */
.tw-dropdown-item--powo            { background:rgba(16,185,129,.04); }
.dark .tw-dropdown-item--powo      { background:rgba(52,211,153,.06); }
.tw-dropdown-item--powo:hover      { background:rgba(16,185,129,.10) !important; }
.dark .tw-dropdown-item--powo:hover{ background:rgba(52,211,153,.13) !important; }
.powo-section-label  { padding:4px 16px 2px; font-size:.65rem; font-weight:700;
                       letter-spacing:.08em; text-transform:uppercase;
                       color:rgb(16,185,129); border-bottom:1px solid rgb(243,244,246); }
.dark .powo-section-label { border-color:rgb(48,48,48); color:rgb(52,211,153); }
.powo-import-badge   { display:inline-flex; align-items:center; gap:2px;
                       background:rgba(16,185,129,.12); color:rgb(5,150,105);
                       border-radius:4px; padding:1px 6px; font-size:.72rem;
                       font-weight:600; margin-right:7px; vertical-align:middle;
                       cursor:help; }
.dark .powo-import-badge { background:rgba(52,211,153,.15); color:rgb(52,211,153); }

/* ── focus ring on input ───────────────────────────────────────────── */
.tw-search-wrap .q-field--focused .q-field__control {
  border-color:var(--tp-secondary, rgb(3,105,161)) !important;
  box-shadow:0 0 0 2px rgba(3,105,161,.15) !important;
}
.dark .tw-search-wrap .q-field--focused .q-field__control {
  box-shadow:0 0 0 2px rgba(14,165,233,.2) !important;
}
</style>
"""


# ── widget ────────────────────────────────────────────────────────────────────

def build_taxon_search(
    session_factory,
    on_select=None,
    *,
    nomenclatural_codes: list[str] | None = None,
    sources: tuple | list = ("local", "taxonworks"),
    placeholder: str = "Enter genus or species name…",
) -> dict:
    """Build the taxon-search widget in the current NiceGUI context.

    Returns {'taxon_id': int|None, 'label': str, 'clear': callable}.

    sources controls which APIs run and in what order. Each listed source
    always runs — no conditional fallback. Valid values: 'local', 'taxonworks',
    'powo'. Default: ('local', 'taxonworks'). Bio-association use case passes
    ('local', 'taxonworks', 'powo').

    nomenclatural_codes filters the local DB section (e.g. ['ICN'] for plants).
    Does not filter TW or POWO results — those are authoritative for their own
    nomenclatural domain.
    """
    ui.add_head_html(_TW_CSS)
    client = _nicegui_context.client

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    state: dict = {"taxon_id": None, "label": "", "_task": None}

    # ── layout ───────────────────────────────────────────────────────────────
    with ui.element("div").classes("tw-search-wrap").style("position:relative; width:100%"):

        search_inp = (
            ui.input(placeholder=placeholder)
            .props("outlined dense")
            .classes("w-full")
        )

        selected_display = ui.element("div").classes("tw-selected-display")
        with selected_display:
            selected_html = ui.html("").classes("tw-selected-content tw-result")
            clear_span    = ui.html('<span class="tw-clear-btn" title="Clear">✕</span>')

        dropdown = ui.element("div").classes("tw-dropdown")

    # ── state transitions ─────────────────────────────────────────────────────

    def _hide_dropdown():
        dropdown.style("display:none")

    def _show_dropdown():
        dropdown.style("display:block")

    def _enter_selected(html_content: str, label: str = "") -> None:
        state["label"] = label
        selected_html.set_content(html_content)
        selected_display.style(add="display:flex;", remove="display:none;")
        search_inp.style(add="display:none;")
        _hide_dropdown()

    def _clear() -> None:
        state["taxon_id"] = None
        state["label"]    = ""
        selected_html.set_content("")
        selected_display.style(add="display:none;", remove="display:flex;")
        search_inp.style(remove="display:none;")
        search_inp.value = ""
        search_inp.run_method("focus")

    clear_span.on("click", lambda _: _clear())

    # ── local section ─────────────────────────────────────────────────────────

    def _build_local_section(local: list) -> None:
        if not local:
            return
        ui.label("In database").classes("tw-section-label")
        for res in local:
            item_html = _local_item_html(
                res.label,
                is_synonym=res.is_synonym,
                accepted=res.accepted_label,
                nomenclatural_code=getattr(res, "nomenclatural_code", None),
            )
            item = ui.element("div").classes("tw-result tw-dropdown-item")
            with item:
                ui.html(item_html)
            item.on("click", lambda _, res=res, h=item_html: _select_local(res, h))

    def _select_local(res, item_html: str) -> None:
        _enter_selected(item_html, label=res.label)
        state["taxon_id"] = res.id
        if on_select:
            on_select(res.id)

    # ── TaxonWorks section ────────────────────────────────────────────────────

    async def _append_tw_section(term: str) -> None:
        with dropdown:
            tw_sec = ui.element("div")
        with tw_sec:
            ui.label("Searching TaxonWorks…").classes("tw-dropdown-empty")

        try:
            results = await tw_svc.search_taxon_names(term)
        except Exception:
            tw_sec.clear()
            return

        tw_sec.clear()
        if not results:
            return

        # Filter out names already in the local DB.
        tw_bare_names = [r.get("name", "") for r in results if r.get("name")]
        if tw_bare_names:
            from app.models import Taxon as _Taxon
            from sqlalchemy import or_

            def _already_local(s) -> set[str]:
                clauses = []
                for n in tw_bare_names:
                    clauses.append(_Taxon.scientific_name == n)
                    clauses.append(_Taxon.scientific_name.endswith(" " + n))
                matched = {
                    row[0] for row in
                    s.query(_Taxon.scientific_name).filter(or_(*clauses)).all()
                }
                found = set()
                for n in tw_bare_names:
                    if n in matched or any(sci.endswith(" " + n) for sci in matched):
                        found.add(n)
                return found

            already_local = _with_session(_already_local)
            results = [r for r in results if r.get("name", "") not in already_local]

        if not results:
            return

        # Batch-fetch valid names for synonym entries.
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
                item_html = _TW_BADGE + _render_tw_label(r, valid_name)
                item = ui.element("div").classes(
                    "tw-result tw-dropdown-item tw-dropdown-item--import"
                )
                with item:
                    ui.html(item_html)
                item.on(
                    "click",
                    lambda _, r=r, h=item_html: asyncio.ensure_future(_on_tw_pick(r, h)),
                )
        _show_dropdown()

    async def _on_tw_pick(r: dict, item_html: str) -> None:
        label = r.get("label") or r.get("name") or ""
        _enter_selected(item_html, label=label)
        state["taxon_id"] = -1  # import in progress

        tw_id = r["id"]
        try:
            tw_data, otu_id = await asyncio.gather(
                tw_svc.fetch_full_classification(tw_id),
                tw_svc.fetch_otu_id_for_taxon_name(tw_id),
            )
        except Exception as exc:
            with client:
                ui.notify(f"TaxonWorks fetch failed: {exc}", type="negative")
            _clear()
            return
        if tw_data is None:
            with client:
                ui.notify("Taxon not found in TaxonWorks.", type="warning")
            _clear()
            return
        try:
            corrections: list[str] = []
            with session_factory() as session:
                with session.begin():
                    taxon = svc_taxa.get_or_create_from_tw_data(
                        session, tw_data, otu_id=otu_id, corrections=corrections
                    )
                    tid = taxon.id
        except Exception as exc:
            with client:
                ui.notify(f"Local DB error: {exc}", type="negative")
            _clear()
            return

        state["taxon_id"] = tid
        with client:
            for msg in corrections:
                ui.notify(
                    f"Taxonomy corrected during import: {msg}",
                    type="warning", timeout=8000,
                )
        if on_select:
            on_select(tid)

    # ── POWO section ──────────────────────────────────────────────────────────

    async def _append_powo_section(term: str) -> None:
        with dropdown:
            powo_sec = ui.element("div")
        with powo_sec:
            ui.label("Searching Plants of the World Online…").classes("tw-dropdown-empty")
        try:
            results = await powo_svc.search_powo(term, limit=8)
        except Exception:
            powo_sec.clear()
            return

        powo_sec.clear()
        if not results:
            return

        with powo_sec:
            ui.label("Plants of the World Online").classes("powo-section-label")
            for r in results:
                name  = r.get("name", "")
                auth  = r.get("authors", "")
                label = f"{name} {auth}".strip() if auth else name
                item_html = _POWO_BADGE + _powo_item_html(r)
                item = ui.element("div").classes(
                    "tw-result tw-dropdown-item tw-dropdown-item--powo"
                )
                with item:
                    ui.html(item_html)
                item.on(
                    "click",
                    lambda _, r=r, lbl=label, h=item_html: asyncio.ensure_future(
                        _on_powo_pick(r, lbl, h)
                    ),
                )
        _show_dropdown()

    async def _on_powo_pick(r: dict, label: str, item_html: str) -> None:
        _enter_selected(item_html, label=label)
        state["taxon_id"] = -1  # import in progress

        # If batch POWO fetch only returned IPNI fields, retry once.
        if r.get("taxonomicStatus") is None and r.get("synonym") is None:
            fq_id = r.get("fqId", "")
            if fq_id:
                try:
                    fresh = await powo_svc.fetch_powo_taxon(fq_id)
                    if fresh:
                        r = {**r, **fresh}
                except Exception:
                    pass

        powo_fields = powo_svc.fields_from_powo(r)

        accepted_fields: dict | None = None
        if powo_fields.get("is_synonym") and powo_fields.get("accepted_fqid"):
            try:
                acc_data = await powo_svc.fetch_powo_taxon(powo_fields["accepted_fqid"])
                if acc_data:
                    accepted_fields = powo_svc.fields_from_powo(acc_data)
            except Exception:
                pass

        try:
            with session_factory() as session:
                with session.begin():
                    taxon = svc_taxa.get_or_create_from_powo_data(
                        session, powo_fields, accepted_fields=accepted_fields
                    )
                    tid = taxon.id
        except Exception as exc:
            with client:
                ui.notify(f"Local DB error: {exc}", type="negative")
            _clear()
            return

        state["taxon_id"] = tid
        if on_select:
            on_select(tid)

    # ── debounced search ──────────────────────────────────────────────────────

    async def _on_search(e):
        if state["taxon_id"] is not None:
            return

        prev = state["_task"]
        if prev and not prev.done():
            prev.cancel()

        term = (e.value or "").strip()
        if len(term) < 2:
            _hide_dropdown()
            return

        async def _do():
            try:
                await asyncio.sleep(0.15)

                dropdown.clear()

                if "local" in sources:
                    local = _with_session(
                        lambda s: svc_taxa.search_taxa_for_display(
                            s, term, limit=10,
                            nomenclatural_codes=nomenclatural_codes or None,
                        )
                    )
                    with dropdown:
                        _build_local_section(local)
                    _show_dropdown()

                if "taxonworks" in sources:
                    await _append_tw_section(term)

                if "powo" in sources:
                    await _append_powo_section(term)

            except asyncio.CancelledError:
                pass

        state["_task"] = asyncio.create_task(_do())

    search_inp.on_value_change(_on_search)

    async def _on_blur(_):
        await asyncio.sleep(0.2)
        _hide_dropdown()

    search_inp.on("blur", _on_blur)

    state["clear"] = _clear
    return state

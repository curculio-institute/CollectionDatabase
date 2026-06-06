"""Bio-association object taxon search widget.

Search order:
  1. Local DB — filtered by nomenclatural_code (default: ICN only).
  2. TaxonWorks — same autocomplete as the identification widget; imports on pick.
  3. POWO (Plants of the World Online) via IPNI — shown when TW returns nothing.

The returned state dict exposes:
  {'taxon_id': int | None, 'label': str, 'clear': Callable}
"""
from __future__ import annotations

import asyncio
import html as _html_mod

from nicegui import ui

import app.services.powo as powo_svc
import app.services.taxa as svc_taxa
import app.services.taxonworks as tw_svc
from app.ui.taxon_search import (
    _TW_CSS,
    _local_item_html,
    _render_tw_label,
)

_POWO_CSS = """
<style>
.powo-section-label  { padding:4px 16px 2px; font-size:.65rem; font-weight:700;
                       letter-spacing:.08em; text-transform:uppercase;
                       color:rgb(16,185,129); border-bottom:1px solid rgb(243,244,246); }
.dark .powo-section-label { border-color:rgb(48,48,48); color:rgb(52,211,153); }
.powo-import-badge   { display:inline-flex; align-items:center; gap:2px;
                       background:rgba(16,185,129,.12); color:rgb(5,150,105);
                       border-radius:4px; padding:1px 6px; font-size:.72rem;
                       font-weight:600; margin-right:7px; vertical-align:middle; }
.dark .powo-import-badge { background:rgba(52,211,153,.15); color:rgb(52,211,153); }
.tw-dropdown-item--powo            { background:rgba(16,185,129,.04); }
.dark .tw-dropdown-item--powo      { background:rgba(52,211,153,.06); }
.tw-dropdown-item--powo:hover      { background:rgba(16,185,129,.10) !important; }
.dark .tw-dropdown-item--powo:hover{ background:rgba(52,211,153,.13) !important; }
</style>
"""


def _powo_item_html(r: dict) -> str:
    """Display HTML for a POWO result: name, author, synonym info, family."""
    name     = r.get("name", "")
    auth     = r.get("authors", "")
    family   = r.get("family", "")
    is_syn   = bool(r.get("synonym", False))
    acc_raw  = r.get("accepted") or {}
    acc_name = acc_raw.get("name", "")
    acc_auth = acc_raw.get("author", "")

    html = f'<i>{_html_mod.escape(name)}</i>'
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


def build_bio_object_search(session_factory, nomenclatural_codes: list[str]) -> dict:
    """Build the bio-association object taxon search widget.

    nomenclatural_codes: DwC codes for local DB filter (e.g. ["ICN"]).
      Pass an empty list to show all codes.

    Returns state dict:
      {'taxon_id': int|None, 'label': str, 'clear': Callable}
    """
    ui.add_head_html(_TW_CSS)
    ui.add_head_html(_POWO_CSS)

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    state: dict = {"taxon_id": None, "label": "", "_task": None}

    with ui.element("div").classes("tw-search-wrap").style("position:relative; width:100%"):
        search_inp = (
            ui.input(placeholder="Type plant or fungus name…")
            .props("clearable outlined dense")
            .classes("w-full")
            .style("font-style:italic")
        )
        dropdown = ui.element("div").classes("tw-dropdown")

    selected_row = ui.element("div").style("display:none; margin-top:6px;")
    with selected_row:
        selected_html = ui.html("").classes("taxon-chip tw-result")

    def _hide():
        dropdown.style("display:none")

    def _show():
        dropdown.style("display:block")

    def _set_selected(taxon_id: int, display_html: str, label: str):
        state["taxon_id"] = taxon_id
        state["label"]    = label
        selected_html.set_content(
            f'{display_html}'
            f'<span class="chip-clear" '
            f'onclick="this.dispatchEvent(new CustomEvent(\'clear\',{{bubbles:true}}))" '
            f'title="Clear">✕</span>'
        )
        selected_row.style(add="display:block;", remove="display:none;")
        search_inp.value = ""
        _hide()

    def _clear():
        state["taxon_id"] = None
        state["label"]    = ""
        selected_html.set_content("")
        selected_row.style(add="display:none;", remove="display:block;")

    state["clear"] = _clear
    selected_html.on("clear", lambda _: _clear())

    # ── Local section ────────────────────────────────────────────────

    def _build_local_section(local: list) -> None:
        if not local:
            return
        ui.label("In database").classes("tw-section-label")
        for res in local:
            item = ui.element("div").classes("tw-dropdown-item")
            with item:
                ui.html(_local_item_html(
                    res.label, is_synonym=res.is_synonym, accepted=res.accepted_label
                ))
            item.on("click", lambda _, r=res: _select_local(r))

    def _select_local(res) -> None:
        html = f'<i>{_html_mod.escape(res.scientific_name or res.label)}</i>'
        if res.authorship:
            html += f' {_html_mod.escape(res.authorship)}'
        if res.is_synonym and res.accepted_label:
            html += f' &#10060; = <i>{_html_mod.escape(res.accepted_label)}</i> &#10003;'
        elif res.is_synonym:
            html += ' &#10060;'
        if res.family:
            html += (
                f' <span style="color:var(--tp-base-soft);font-size:.8rem">'
                f'{_html_mod.escape(res.family)}</span>'
            )
        _set_selected(res.id, html, res.label)

    # ── TaxonWorks section ───────────────────────────────────────────

    async def _append_tw_section(term: str, *, tw_container) -> list[dict]:
        """Fetch TW results, append below local. Returns the raw TW results list."""
        with tw_container:
            ui.label("Searching TaxonWorks…").classes("tw-dropdown-empty")
        try:
            results = await tw_svc.search_taxon_names(term)
        except Exception:
            tw_container.clear()
            return []

        tw_container.clear()
        if not results:
            return []

        # Filter out names already in local DB.
        tw_bare = [r.get("name", "") for r in results if r.get("name")]
        if tw_bare:
            from app.models import Taxon as _Taxon
            from sqlalchemy import or_

            def _already_local(s) -> set[str]:
                clauses = []
                for n in tw_bare:
                    clauses.append(_Taxon.scientific_name == n)
                    clauses.append(_Taxon.scientific_name.endswith(" " + n))
                matched = {row[0] for row in s.query(_Taxon.scientific_name).filter(or_(*clauses)).all()}
                found = set()
                for n in tw_bare:
                    if n in matched or any(sci.endswith(" " + n) for sci in matched):
                        found.add(n)
                return found

            already = _with_session(_already_local)
            results = [r for r in results if r.get("name", "") not in already]

        if not results:
            return []

        valid_cache: dict[int, str] = {}
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
            valid_cache = dict(pairs)

        with tw_container:
            ui.label("TaxonWorks").classes("tw-section-label")
            for r in results:
                vid = r.get("valid_taxon_name_id")
                valid_name = valid_cache.get(vid, "") if vid and vid != r.get("id") else ""
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
        return results

    async def _on_tw_pick(r: dict):
        label     = r.get("label") or r.get("name") or ""
        disp_html = r.get("label_html") or f"<i>{_html_mod.escape(label)}</i>"
        # Show chip immediately so the user has feedback; -1 guards _add_assoc
        # until the DB import completes.
        _set_selected(-1, disp_html, label)
        tw_id = r["id"]
        try:
            tw_data, otu_id = await asyncio.gather(
                tw_svc.fetch_full_classification(tw_id),
                tw_svc.fetch_otu_id_for_taxon_name(tw_id),
            )
        except Exception as exc:
            _clear()
            ui.notify(f"TaxonWorks fetch failed: {exc}", type="negative")
            return
        if tw_data is None:
            _clear()
            ui.notify("Taxon not found in TaxonWorks.", type="warning")
            return
        try:
            with session_factory() as session:
                with session.begin():
                    taxon = svc_taxa.get_or_create_from_tw_data(session, tw_data, otu_id=otu_id)
                    tid   = taxon.id
        except Exception as exc:
            _clear()
            ui.notify(f"Local DB error: {exc}", type="negative")
            return
        state["taxon_id"] = tid  # replace sentinel with real DB id

    # ── POWO section ─────────────────────────────────────────────────

    async def _append_powo_section(term: str) -> None:
        with dropdown:
            powo_sec = ui.element("div")
        with powo_sec:
            ui.label("Searching Plants of the World Online…").classes("tw-dropdown-empty")
        try:
            # search_powo does IPNI search + parallel POWO batch fetch so we have
            # synonym status and accepted-name info without extra clicks.
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
                name_html = _powo_item_html(r)

                item = ui.element("div").classes(
                    "tw-result tw-dropdown-item tw-dropdown-item--powo"
                )
                with item:
                    ui.html('<span class="powo-import-badge">🌿 add</span>' + name_html)
                item.on("click", lambda _, r=r, lbl=label: asyncio.ensure_future(
                    _on_powo_pick(r, lbl)
                ))
        _show()

    async def _on_powo_pick(r: dict, label: str):
        # Show chip immediately with full info; -1 guards _add_assoc until import finishes.
        _set_selected(-1, _powo_item_html(r) or f'<i>{_html_mod.escape(label)}</i>', label)

        # r from search_powo already contains full POWO data.
        # If it only has IPNI fields (POWO fetch failed in batch), retry once.
        if r.get("taxonomicStatus") is None and not r.get("synonym", None) is not None:
            fq_id = r.get("fqId", "")
            if fq_id:
                try:
                    fresh = await powo_svc.fetch_powo_taxon(fq_id)
                    if fresh:
                        r = {**r, **fresh}
                except Exception:
                    pass

        powo_fields = powo_svc.fields_from_powo(r)

        # Fetch accepted-name POWO record if this is a synonym.
        accepted_fields: dict | None = None
        if powo_fields.get("is_synonym") and powo_fields.get("accepted_fqid"):
            try:
                acc_data = await powo_svc.fetch_powo_taxon(powo_fields["accepted_fqid"])
                if acc_data:
                    accepted_fields = powo_svc.fields_from_powo(acc_data)
            except Exception:
                pass  # import synonym without the accepted-name link

        try:
            with session_factory() as session:
                with session.begin():
                    taxon = svc_taxa.get_or_create_from_powo_data(
                        session, powo_fields, accepted_fields=accepted_fields
                    )
                    tid = taxon.id
        except Exception as exc:
            _clear()
            ui.notify(f"Local DB error: {exc}", type="negative")
            return

        state["taxon_id"] = tid  # replace sentinel with real DB id

    # ── Debounced search ─────────────────────────────────────────────

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
                await asyncio.sleep(0.15)
                local = _with_session(
                    lambda s: svc_taxa.search_taxa_for_display(
                        s, term, limit=10,
                        nomenclatural_codes=nomenclatural_codes or None,
                    )
                )
                dropdown.clear()
                with dropdown:
                    _build_local_section(local)
                    tw_container = ui.element("div")
                _show()
                tw_results = await _append_tw_section(term, tw_container=tw_container)
                # POWO fallback: only if TW returned nothing after filtering
                if not tw_results:
                    await _append_powo_section(term)
            except asyncio.CancelledError:
                pass

        state["_task"] = asyncio.create_task(_do())

    search_inp.on_value_change(_on_search)

    async def _on_blur(_):
        await asyncio.sleep(0.2)
        _hide()

    search_inp.on("blur", _on_blur)

    return state

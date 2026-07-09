"""Taxon-search widget.

States: Empty → Searching → Selected (see docs/design.md §4).

Sources are queried in order; each always runs if listed.
Default order: local DB → TaxonWorks.
Bio-association order: local DB → TaxonWorks → WCVP (plants; see docs/plant_names.md).

State dict: {'taxon_id': int|None, 'label': str, 'clear': callable}
  taxon_id = None   — nothing selected
  taxon_id = -1     — TW/WCVP import in progress (do not read yet)
  taxon_id = N > 0  — confirmed local DB id

on_select(taxon_id: int) is called after the DB record is confirmed.
"""
from __future__ import annotations

import asyncio
import html as _html_mod
import re

from nicegui import context as _nicegui_context, ui

import app.services.wcvp as wcvp_svc
import app.services.taxonworks as tw_svc
import app.services.taxa as svc_taxa
import app.services.import_preview as import_preview_svc
from app.services.import_preview import PREVIEW_FIELDS, TaxonChangeRecord
from app.ui.person_field import _NAV_SCRIPT


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


def _wcvp_item_html(row, accepted, reason: str) -> str:
    """One WCVP row.

    A refused name (Unplaced / Misapplied / an unmodelled rank) states its reason and must
    NOT borrow the synonym form `Name ❌ = Accepted ✓` — that form asserts *synonym of*, and a
    misapplication is precisely not a synonymy. See docs/plant_names.md §4.
    """
    e = _html_mod.escape
    html = f'🌿 <i>{e(row.name)}</i>'
    if row.authorship:
        html += f' {e(row.authorship)}'

    if row.is_refused:
        # Name the reason, not the status. A name refused for its RANK is very often a
        # perfectly ordinary synonym, and a "⊘ synonym" badge would say that synonyms cannot
        # be imported — they can, and usually are.
        badge = (f"rank {row.rank or 'none'}" if row.rank_unsupported
                 else row.status.lower())
        html += f' <span class="wcvp-blocked-badge">⊘ {e(badge)}</span>'
        if reason:
            html += (f'<div class="wcvp-blocked-reason">{e(reason)}</div>')
        return html

    if accepted is not None:
        html += (
            ' &#10060; = '
            f'<i>{e(accepted.name)}</i>'
            + (f' {e(accepted.authorship)}' if accepted.authorship else "")
            + ' &#10003;'
        )
    if row.family:
        html += (
            f' <span style="color:var(--tp-base-soft);font-size:.8rem">'
            f'{e(row.family)}</span>'
        )
    return html


# ── import badges ─────────────────────────────────────────────────────────────

_TW_BADGE = (
    '<span class="tw-import-badge"'
    ' title="This taxon and its parent taxa were imported from TaxonWorks">'
    '✚ add</span>'
)
_WCVP_BADGE = (
    '<span class="wcvp-import-badge"'
    ' title="This taxon is imported from the World Checklist of Vascular Plants (WCVP)">'
    '✚ add</span>'
)
# A refused row carries no ✚ add badge: in this widget the badge *means* "clicking imports
# this", so its absence is the primary signal, as it already is for local rows.
_WCVP_BLOCKED_TITLE = (
    "This name cannot be imported: the database records a name only as accepted, or as "
    "replaced by another name. Create it deliberately in the taxon editor if you need it."
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
.tw-section-label        { padding:4px 16px 2px; font-size:.72rem; font-weight:700;
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

/* WCVP import items */
.tw-dropdown-item--wcvp            { background:rgba(16,185,129,.04); }
.dark .tw-dropdown-item--wcvp      { background:rgba(52,211,153,.06); }
.tw-dropdown-item--wcvp:hover      { background:rgba(16,185,129,.10) !important; }
.dark .tw-dropdown-item--wcvp:hover{ background:rgba(52,211,153,.13) !important; }
.wcvp-section-label  { padding:4px 16px 2px; font-size:.72rem; font-weight:700;
                       letter-spacing:.08em; text-transform:uppercase;
                       color:rgb(16,185,129); border-bottom:1px solid rgb(243,244,246); }
.dark .wcvp-section-label { border-color:rgb(48,48,48); color:rgb(52,211,153); }
.wcvp-import-badge   { display:inline-flex; align-items:center; gap:2px;
                       background:rgba(16,185,129,.12); color:rgb(5,150,105);
                       border-radius:4px; padding:1px 6px; font-size:.72rem;
                       font-weight:600; margin-right:7px; vertical-align:middle;
                       cursor:help; }
.dark .wcvp-import-badge { background:rgba(52,211,153,.15); color:rgb(52,211,153); }

/* Refused names: shown so the user learns the name exists rather than inventing it by
   hand, but never importable. Muted, no ✚ add badge, not clickable. */
.tw-dropdown-item--blocked         { background:transparent; opacity:.62;
                                     cursor:not-allowed; }
.tw-dropdown-item--blocked:hover   { background:rgba(0,0,0,.03) !important; }
.dark .tw-dropdown-item--blocked:hover { background:rgba(255,255,255,.04) !important; }
.wcvp-blocked-badge  { display:inline-flex; align-items:center; gap:2px;
                       background:rgba(120,113,108,.14); color:rgb(87,83,78);
                       border-radius:4px; padding:1px 6px; font-size:.7rem;
                       font-weight:600; margin-left:7px; vertical-align:middle;
                       letter-spacing:.02em; }
.dark .wcvp-blocked-badge { background:rgba(214,211,209,.14); color:rgb(214,211,209); }
.wcvp-blocked-reason { font-size:.74rem; color:var(--tp-base-soft);
                       margin-top:1px; font-style:normal; }
.wcvp-hint           { padding:8px 16px; font-size:.78rem; color:var(--tp-base-soft); }

/* ── focus ring on input ───────────────────────────────────────────── */
.tw-search-wrap .q-field--focused .q-field__control {
  border-color:var(--tp-secondary, rgb(3,105,161)) !important;
  box-shadow:0 0 0 2px rgba(3,105,161,.15) !important;
}
.dark .tw-search-wrap .q-field--focused .q-field__control {
  box-shadow:0 0 0 2px rgba(14,165,233,.2) !important;
}

/* ── import preview dialog ─────────────────────────────────────────── */
.imp-change-block   { margin-bottom:16px; }
.imp-change-header  { display:flex;align-items:center;gap:8px;margin-bottom:6px; }
.imp-name           { font-size:.88rem;font-weight:600;font-style:italic;
                      color:var(--tp-base-content); }
.imp-rank           { font-size:.75rem;color:var(--tp-base-soft); }
.imp-table          { width:100%;border-collapse:collapse;font-size:.8rem;margin-left:8px; }
.imp-table th       { text-align:left;padding:2px 8px;color:var(--tp-base-soft);
                      font-weight:600;font-size:.75rem;
                      border-bottom:1px solid var(--tp-base-border); }
.imp-table td       { padding:3px 8px;border-bottom:1px solid var(--tp-base-muted);
                      color:var(--tp-base-content);white-space:nowrap; }
.imp-field          { color:var(--tp-base-soft);font-size:.75rem;font-family:monospace; }
.imp-null           { color:var(--tp-base-soft); }
.imp-cell-new       { background:rgba(16,185,129,.10); }
.dark .imp-cell-new { background:rgba(52,211,153,.12); }
.imp-cell-upd       { background:rgba(251,191,36,.15); }
.dark .imp-cell-upd { background:rgba(251,191,36,.10); }
.tw-dropdown-item.dropdown-item--active { background: rgb(219,234,254) !important; }
.dark .tw-dropdown-item.dropdown-item--active { background: rgb(30,41,59) !important; }
</style>
"""


# ── import preview dialog ─────────────────────────────────────────────────────

def _build_change_html(rec: TaxonChangeRecord) -> str:
    """Render one changed-row section as an HTML string for the preview dialog."""
    is_new = rec.is_new
    badge_cls = "feedback feedback-notice" if is_new else "feedback feedback-warning"
    badge_text = "NEW" if is_new else "UPDATED"

    rows_html = ""
    for field_key, field_label in PREVIEW_FIELDS:
        before_val = rec.before.get(field_key) if rec.before else None
        after_val  = rec.after.get(field_key)
        if not before_val and not after_val:
            continue
        if not is_new and before_val == after_val:
            continue

        def _cell(val: object, extra_cls: str = "") -> str:
            cls_attr = f' class="{extra_cls}"' if extra_cls else ""
            if val is None:
                return f'<td{cls_attr}><span class="imp-null">—</span></td>'
            return f'<td{cls_attr}>{_html_mod.escape(str(val))}</td>'

        before_td = _cell(before_val) if not is_new else ""
        after_td  = _cell(after_val, "imp-cell-new" if is_new else "imp-cell-upd")
        rows_html += (
            f'<tr>'
            f'<td class="imp-field">{_html_mod.escape(field_label)}</td>'
            f'{before_td}{after_td}'
            f'</tr>'
        )

    if not rows_html:
        return ""

    before_th = "<th>Before</th>" if not is_new else ""
    after_label = "Value" if is_new else "After"

    return (
        f'<div class="imp-change-block">'
        f'<div class="imp-change-header">'
        f'<span class="{badge_cls}">{badge_text}</span>'
        f'<span class="imp-name">{_html_mod.escape(rec.scientific_name)}</span>'
        f'<span class="imp-rank">[{_html_mod.escape(rec.taxon_rank)}]</span>'
        f'</div>'
        f'<table class="imp-table">'
        f'<tr><th></th>{before_th}<th>{after_label}</th></tr>'
        f'{rows_html}'
        f'</table>'
        f'</div>'
    )


async def _show_import_preview_dialog(
    changes: list[TaxonChangeRecord],
    source_name: str,
    client,
) -> bool:
    """Show a modal diff table and return True (Apply) or False (Cancel).

    Shows one section per new/modified Taxon row.  If changes is empty the
    dialog is skipped and True is returned immediately.
    """
    if not changes:
        return True

    with client:
        with ui.dialog().props("persistent") as dlg, ui.card().classes("q-pa-lg").style(
            "min-width:520px;max-width:740px;width:90vw"
        ):
            ui.label(f"Import from {source_name}").classes("text-base font-semibold")
            (
                ui.label("Review changes before applying:")
                .classes("text-xs block q-mt-xs q-mb-md")
                .style("color:var(--tp-base-soft)")
            )

            with ui.scroll_area().style("max-height:55vh"):
                for rec in changes:
                    html_block = _build_change_html(rec)
                    if html_block:
                        ui.html(html_block)

            with ui.row().classes("justify-end gap-2 q-mt-md"):
                ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                ui.button(
                    "Apply import",
                    on_click=lambda: dlg.submit(True),
                ).props("color=secondary no-caps")

        dlg.open()

    return await dlg


# ── widget ────────────────────────────────────────────────────────────────────

def build_taxon_search(
    session_factory,
    on_select=None,
    *,
    nomenclatural_codes: list[str] | None = None,
    sources: tuple | list = ("local", "taxonworks"),
    placeholder: str = "Enter genus or species name…",
    initial_taxon_id: int | None = None,
    initial_label: str = "",
) -> dict:
    """Build the taxon-search widget in the current NiceGUI context.

    Returns {'taxon_id': int|None, 'label': str, 'clear': callable}.

    sources controls which sources run and in what order. Each listed source
    always runs — no conditional fallback. Valid values: 'local', 'taxonworks',
    'wcvp'. Default: ('local', 'taxonworks'). Bio-association use case passes
    ('local', 'taxonworks', 'wcvp') — TaxonWorks stays primary, for consistency with
    the published mirror; WCVP supplies plant names neither source knows.

    nomenclatural_codes filters the local DB section (e.g. ['ICN'] for plants).
    Does not filter TW or WCVP results — those are authoritative for their own
    nomenclatural domain.
    """
    ui.add_head_html(_TW_CSS)
    ui.add_head_html(_NAV_SCRIPT)
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
                nomenclatural_code=res.nomenclatural_code,
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

        # Batch-fetch full taxon-name records for all results so we can get both
        # the valid-name label (for synonyms) and the nomenclatural code (for 🌿).
        all_ids = list({
            tid
            for r in results
            for tid in (r["id"], r.get("valid_taxon_name_id"))
            if tid
        })
        detail_cache: dict[int, dict] = {}
        if all_ids:
            async def _fetch_detail(tw_id: int) -> tuple[int, dict]:
                try:
                    d = await tw_svc.fetch_taxon_name(tw_id)
                    return tw_id, d or {}
                except Exception:
                    return tw_id, {}
            detail_cache = dict(await asyncio.gather(*[_fetch_detail(i) for i in all_ids]))

        if nomenclatural_codes:
            allowed = {c.lower() for c in nomenclatural_codes}
            results = [
                r for r in results
                if (detail_cache.get(r["id"], {}).get("nomenclatural_code") or "").lower() in allowed
            ]
            if not results:
                return

        with tw_sec:
            ui.label("TaxonWorks").classes("tw-section-label")
            for r in results:
                vid = r.get("valid_taxon_name_id")
                valid_name = (
                    detail_cache.get(vid, {}).get("cached", "")
                    if vid and vid != r.get("id") else ""
                )
                nomen = (detail_cache.get(r["id"], {}).get("nomenclatural_code") or "").lower()
                prefix = "🌿 " if nomen == "icn" else ""
                item_html = prefix + _TW_BADGE + _render_tw_label(r, valid_name)
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

        mismatch_msgs: list[str] = []

        def _run_preview(session):
            return svc_taxa.get_or_create_from_tw_data(
                session, tw_data, otu_id=otu_id, mismatches=mismatch_msgs
            )

        def _run_apply(session):
            return svc_taxa.get_or_create_from_tw_data(session, tw_data, otu_id=otu_id)

        try:
            with session_factory() as session:
                changes = import_preview_svc.collect_import_preview(
                    session, lambda: _run_preview(session)
                )
        except Exception as exc:
            with client:
                ui.notify(f"Preview failed: {exc}", type="negative")
            _clear()
            return

        confirmed = await _show_import_preview_dialog(changes, "TaxonWorks", client)
        if not confirmed:
            _clear()
            return

        try:
            with session_factory() as session:
                with session.begin():
                    tid = _run_apply(session).id
        except Exception as exc:
            with client:
                ui.notify(f"Local DB error: {exc}", type="negative")
            _clear()
            return

        state["taxon_id"] = tid
        with client:
            for msg in mismatch_msgs:
                ui.notify(f"Taxonomy mismatch: {msg}", type="warning", timeout=8000)
        if on_select:
            on_select(tid)

    # ── WCVP section (plants) ────────────────────────────────────────────────
    #
    # Local SQLite, not an API: POWO and WCVP both sit behind a Cloudflare bot challenge
    # that answers a plain HTTP client with 403 on ~17 of 20 requests, and the old code
    # swallowed that failure — silently importing IPNI-only data with no nomenclatural
    # code, no ancestor authorship, and every synonym recorded as an accepted name.
    # See docs/plant_names.md and issue #98. Nothing here is async or fallible over the
    # network, so nothing here may swallow an exception.

    def _wcvp_index():
        """Open the index once per widget. Absent index is a condition, not an error."""
        if "wcvp_db" not in state:
            try:
                state["wcvp_db"] = wcvp_svc.open_index()
            except wcvp_svc.IndexMissing:
                state["wcvp_db"] = None
        return state["wcvp_db"]

    def _append_wcvp_section(term: str) -> None:
        db = _wcvp_index()
        with dropdown:
            sec = ui.element("div")

        if db is None:
            with sec:
                ui.label("World Checklist of Vascular Plants").classes("wcvp-section-label")
                ui.label(
                    "No plant index installed — run scripts/build_wcvp_index.py"
                ).classes("wcvp-hint")
            _show_dropdown()
            return

        results = wcvp_svc.search(db, term, limit=8)
        if not results:
            return

        with sec:
            ui.label("World Checklist of Vascular Plants").classes("wcvp-section-label")
            for row in results:
                accepted = None if row.is_refused else wcvp_svc.accepted_name(db, row)
                reason = wcvp_svc.refusal_reason(db, row) if row.is_refused else ""
                body = _wcvp_item_html(row, accepted, reason)

                if row.is_refused:
                    # No ✚ add badge and no click handler: the row informs, it does not act.
                    item = ui.element("div").classes(
                        "tw-result tw-dropdown-item tw-dropdown-item--blocked"
                    )
                    item.props(f'title="{_WCVP_BLOCKED_TITLE}"')
                    with item:
                        ui.html(body)
                    continue

                item_html = _WCVP_BADGE + body
                item = ui.element("div").classes(
                    "tw-result tw-dropdown-item tw-dropdown-item--wcvp"
                )
                with item:
                    ui.html(item_html)
                item.on(
                    "click",
                    lambda _, r=row, lbl=row.label, h=item_html: asyncio.ensure_future(
                        _on_wcvp_pick(r, lbl, h)
                    ),
                )
        _show_dropdown()

    async def _on_wcvp_pick(row, label: str, item_html: str) -> None:
        _enter_selected(item_html, label=label)
        state["taxon_id"] = -1  # import in progress

        db = _wcvp_index()
        try:
            fields = wcvp_svc.fields_from_wcvp(db, row)
        except wcvp_svc.NotImportable as exc:
            # A refused row has no click handler, so this is a data problem (e.g. a synonym
            # whose accepted name is missing from Kew's archive). Say so; never guess.
            with client:
                ui.notify(f"Cannot import: {exc}", type="negative", timeout=8000)
            _clear()
            return

        mismatch_msgs: list[str] = []

        def _run_preview(session):
            return svc_taxa.get_or_create_from_wcvp_data(
                session, fields, mismatches=mismatch_msgs
            )

        def _run_apply(session):
            return svc_taxa.get_or_create_from_wcvp_data(session, fields)

        try:
            with session_factory() as session:
                changes = import_preview_svc.collect_import_preview(
                    session, lambda: _run_preview(session)
                )
        except Exception as exc:
            with client:
                ui.notify(f"Preview failed: {exc}", type="negative")
            _clear()
            return

        source = wcvp_svc.index_meta(db).get("label", "WCVP")
        confirmed = await _show_import_preview_dialog(changes, source, client)
        if not confirmed:
            _clear()
            return

        try:
            with session_factory() as session:
                with session.begin():
                    tid = _run_apply(session).id
        except Exception as exc:
            with client:
                ui.notify(f"Local DB error: {exc}", type="negative")
            _clear()
            return

        state["taxon_id"] = tid
        with client:
            for msg in mismatch_msgs:
                ui.notify(f"Taxonomy mismatch: {msg}", type="warning", timeout=8000)
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

                if "wcvp" in sources:
                    # Local index: no await, no network, no failure to swallow.
                    _append_wcvp_section(term)

            except asyncio.CancelledError:
                pass

        state["_task"] = asyncio.create_task(_do())

    search_inp.on_value_change(_on_search)

    async def _on_blur(_):
        await asyncio.sleep(0.2)
        _hide_dropdown()

    search_inp.on("blur", _on_blur)

    state["clear"] = _clear

    if initial_taxon_id and initial_label:
        _enter_selected(_html_mod.escape(initial_label), label=initial_label)
        state["taxon_id"] = initial_taxon_id

    return state

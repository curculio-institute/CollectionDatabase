"""Taxonomy tab: the checklist tree (family → synonyms) with its rank / nomenclatural-code
filters, the taxon editor entry points, "Merge names" and "Check consistency".

Moved verbatim out of main.py. Returns the three things main.py's tab-change handler
needs to rebuild and expand the tree when the tab is shown.
"""
from __future__ import annotations

import asyncio
import json
from nicegui import ui
import app.services.taxonomy as tax_svc
from app.ui.taxon_search import build_taxon_search
from app.ui.taxon_editor import build_taxon_editor

from app.services.taxa import TAXON_RANKS as _TAXON_RANKS

# Ranks the checklist tree prints as a rank label beside the name: everything above
# species. Derived from TAXON_RANKS rather than hand-listed — a hand-listed subset
# silently omitted the ranks above 'order', so a superorder rendered with no label
# and no heading style, reading as a different kind of row rather than a higher one.
_TREE_RANK_LABELS: list[str] = _TAXON_RANKS[:_TAXON_RANKS.index("species")]


def build_taxonomy_tab(session_factory, *, refreshers):
    # Same names the code below used while it lived inline in main.py.
    _sf = session_factory
    _refreshers = refreshers

    def _with_session(fn):
        with _sf() as s:
            return fn(s)

    with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):

        # Summary stat cards (accepted-taxa / species counts) were removed
        # — low value, took vertical space. The refresher stays as a no-op
        # so the existing call sites need no change.
        def _refresh_taxonomy_stats():
            pass
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

                # Reorder mode — arranging the taxonomic sequence is an
                # occasional curatorial action, so it stays behind this
                # toggle (progressive disclosure). When on, the reorder
                # toolbar below appears and orderable rows become
                # selectable to move; off restores plain browsing.
                _reorder_mode = {"on": False}

                def _toggle_reorder():
                    on = not _reorder_mode["on"]
                    _reorder_mode["on"] = on
                    _reorder_bar.set_visibility(on)
                    if on:
                        _reorder_btn.props("color=secondary")
                        tax_tree.classes(add="reorder-on")
                    else:
                        _reorder_btn.props(remove="color=secondary")
                        tax_tree.classes(remove="reorder-on")
                        _sel_taxon["tid"] = None
                        _btn_up.set_enabled(False)
                        _btn_dn.set_enabled(False)
                        _reorder_lbl.set_text(
                            "Select a family or higher rank to reorder")
                        tax_tree._props["selected"] = None
                        tax_tree.update()

                _reorder_btn = (
                    ui.button("Reorder", icon="swap_vert")
                    .props("flat dense")
                    .tooltip("Arrange family-and-above ranks into the "
                             "collection's taxonomic sequence")
                )
                _reorder_btn.on_click(_toggle_reorder)

                def _on_saved_taxon():
                    _refresh_taxonomy_stats()
                    _refresh_tree()

                _taxon_editor = build_taxon_editor(_sf, _on_saved_taxon)

                # The tree's per-row pencil (see _NODE_SLOT) emits the node id —
                # 'taxon-<id>' for a name, 'syn-<id>' for a synonym row (itself a
                # taxon, so it is editable the same way).
                def _on_tax_edit(e):
                    node_id = str(e.args or "")
                    _, _, raw = node_id.rpartition("-")
                    if not raw.isdigit():
                        ui.notify(f"Cannot edit: unrecognised row {node_id!r}.",
                                  type="warning")
                        return
                    _taxon_editor["open_edit"](int(raw))

                ui.on("tax_edit", _on_tax_edit)

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

                def _open_merge_dialog():
                    """Merge two duplicate names into one (de-duplication, NOT
                            synonymisation). Pick the row to keep + the row to delete; every
                            reference moves to the kept row. Guarded by merge_taxa_preview so a
                            synonym-shaped merge is refused with the reason shown."""
                    from app.services.taxa import merge_taxa, merge_taxa_preview
                    picks = {"keep": None, "absorb": None}
                    dlg = ui.dialog()
                    with dlg, ui.card().classes("min-w-[560px] max-w-[720px] gap-2"):
                        ui.label("Merge duplicate names").classes("section-label")
                        with ui.element("div").classes("w-full").style(
                                "background:rgba(217,119,6,.10); "
                                "border:1px solid rgba(217,119,6,.35); "
                                "border-radius:6px; padding:8px 10px;"):
                            with ui.row().classes("items-start gap-2 no-wrap"):
                                ui.icon("warning").style(
                                    "color:#d97706; margin-top:2px")
                                ui.label(
                                    "Merge is for the SAME name written two ways — a "
                                    "typo, or an exact / subgenus duplicate. It is NOT "
                                    "synonymisation. It permanently DELETES one row and "
                                    "moves its specimens, associations and children onto "
                                    "the other. If one name is a synonym of the other, "
                                    "cancel and record that in the taxon editor instead."
                                ).classes("text-xs").style("color:#b45309")

                        ui.label("Keep this name").classes(
                            "text-xs font-medium mt-1")
                        build_taxon_search(
                            _sf, on_select=lambda tid: _pick("keep", tid),
                            sources=("local",),
                            placeholder="Search the checklist…")
                        ui.label("Merge & DELETE this one").classes(
                            "text-xs font-medium mt-1")
                        build_taxon_search(
                            _sf, on_select=lambda tid: _pick("absorb", tid),
                            sources=("local",),
                            placeholder="Search the checklist…")

                        preview_box = ui.column().classes("w-full gap-0 mt-1")
                        with ui.row().classes("justify-end w-full mt-2 gap-2"):
                            ui.button("Cancel", on_click=dlg.close).props("flat")
                            merge_btn = ui.button("Merge", icon="merge_type") \
                                .props("color=negative")
                            merge_btn.set_enabled(False)

                    def _pick(which, tid):
                        picks[which] = tid
                        _refresh_preview()

                    def _refresh_preview():
                        preview_box.clear()
                        merge_btn.set_enabled(False)
                        if not (picks["keep"] and picks["absorb"]):
                            return
                        with _sf() as s:
                            prev = merge_taxa_preview(
                                s, picks["keep"], picks["absorb"])
                        with preview_box:
                            if prev.blocker:
                                ui.label(f"⚠ {prev.blocker}").classes("text-sm") \
                                  .style("color:#d97706")
                                return
                            ui.label(
                                f"Move to “{prev.keep_label}”: "
                                f"{prev.determinations} determination(s), "
                                f"{prev.associations} association(s), "
                                f"{prev.children} child taxon(s), "
                                f"{prev.synonyms} synonym(s)."
                            ).classes("text-sm")
                            ui.label(f"Then delete “{prev.absorb_label}”.") \
                              .classes("text-xs").style("color:var(--tp-base-soft)")
                        merge_btn.set_enabled(True)

                    def _do_merge():
                        try:
                            with _sf() as s:
                                with s.begin():
                                    merge_taxa(s, picks["keep"], picks["absorb"])
                        except Exception as exc:      # noqa: BLE001
                            ui.notify(f"Merge failed: {exc}", type="negative")
                            return
                        ui.notify("Names merged.", type="positive")
                        dlg.close()
                        _refresh_taxonomy_stats()
                        _refresh_tree()

                    merge_btn.on_click(_do_merge)
                    dlg.on_value_change(
                        lambda e: dlg.delete() if not e.value else None)
                    dlg.open()

                ui.button("Check consistency", icon="fact_check") \
                  .props("flat dense").on_click(_check_consistency)
                ui.button("Merge names", icon="merge_type") \
                  .props("flat dense") \
                  .tooltip("Merge two duplicate names (typos / exact duplicates) "
                           "into one — not for synonymising") \
                  .on_click(_open_merge_dialog)

        # ── checklist card ───────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            _collapsed = {"on": False}   # tree starts fully expanded
            with ui.row().classes("items-center gap-2 mb-3 w-full"):
                ui.label("Checklist").classes("section-label")
                ui.space()
                _collapse_btn = (
                    ui.button("Collapse all", icon="unfold_less")
                    .props("flat dense")
                )

                async def _toggle_collapse():
                    _collapsed["on"] = not _collapsed["on"]
                    if _collapsed["on"]:
                        await tax_tree.run_method("collapseAll")
                        _collapse_btn.set_text("Expand all")
                        _collapse_btn.props("icon=unfold_more")
                    else:
                        await tax_tree.run_method("expandAll")
                        _collapse_btn.set_text("Collapse all")
                        _collapse_btn.props("icon=unfold_less")

                _collapse_btn.on_click(_toggle_collapse)

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

            # Reorder toolbar — display-only taxonomic sequence for
            # family-and-above ranks (#40). Selecting an orderable row
            # (native q-tree selection, Python-side on_select) enables
            # the ↑/↓ buttons; below family stays alphabetical.
            _sel_taxon: dict[str, int | None] = {"tid": None}
            _reorder_bar = ui.row().classes("items-center gap-1 mb-3")
            with _reorder_bar:
                ui.icon("swap_vert").classes("text-base") \
                  .style("color:var(--tp-base-soft)")
                _reorder_lbl = (
                    ui.label("Select a family or higher rank to reorder")
                    .classes("text-xs").style("color:var(--tp-base-soft)")
                )
                _btn_up = (
                    ui.button(icon="keyboard_arrow_up")
                    .props("flat dense round size=sm")
                    .tooltip("Move up (taxonomic sequence)")
                )
                _btn_dn = (
                    ui.button(icon="keyboard_arrow_down")
                    .props("flat dense round size=sm")
                    .tooltip("Move down (taxonomic sequence)")
                )
            _btn_up.set_enabled(False)
            _btn_dn.set_enabled(False)
            _reorder_bar.set_visibility(False)   # revealed by the Reorder toggle

            def _on_node_select(e):
                from app.models import Taxon
                if not _reorder_mode["on"]:
                    return
                nid = e.value or ""
                tid = None
                name = None
                if isinstance(nid, str) and nid.startswith("taxon-"):
                    try:
                        cand = int(nid.split("-", 1)[1])
                    except ValueError:
                        cand = None
                    if cand is not None:
                        with _sf() as s:
                            t = s.get(Taxon, cand)
                            if t is not None and t.taxon_rank in tax_svc.ORDERABLE_RANKS:
                                tid = t.id
                                name = t.scientific_name
                _sel_taxon["tid"] = tid
                _btn_up.set_enabled(tid is not None)
                _btn_dn.set_enabled(tid is not None)
                _reorder_lbl.set_text(
                    f"Reorder: {name}" if tid is not None
                    else "Select a family or higher rank to reorder"
                )

            def _do_move(direction: int):
                tid = _sel_taxon["tid"]
                if tid is None:
                    return
                with _sf() as s:
                    with s.begin():
                        tax_svc.move_taxon(s, tid, direction)
                _refresh_tree()
                if "explore" in _refreshers:
                    _refreshers["explore"]()
                # keep the row selected after the rebuild
                tax_tree._props["selected"] = f"taxon-{tid}"
                tax_tree.update()

            _btn_up.on_click(lambda: _do_move(-1))
            _btn_dn.on_click(lambda: _do_move(1))

            tree_data = _with_session(
                lambda s: tax_svc.build_taxonomy_tree(s, nomenclatural_code="ICZN")
            )

            _NODE_SLOT = r"""
                        <div style="display:flex; align-items:baseline; gap:7px; padding:2px 0 1px; cursor:pointer; flex:1;"
                             @click="props.node.children && props.node.children.length ? props.tree.setExpanded(props.key, !props.expanded) : null">
                          <span class="tax-rank">{{ __RANK_LABELS__.includes(props.node.rank) ? props.node.rank : '' }}</span>
                          <span :class="'rank-' + props.node.rank"><span
                                v-if="props.node.synonym"
                                style="color:var(--tp-base-soft); font-style:normal;">=&nbsp;</span>{{ props.node.name }}</span>
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
                             class="tax-row-action tax-tw-link" @click.stop>↗
                            <q-tooltip>Open in TaxonPages</q-tooltip>
                          </a>
                          <q-icon name="edit" size="14px"
                                  class="tax-row-action tax-edit-hint"
                                  @click.stop="_tpEmit('tax_edit', props.node.id)">
                            <q-tooltip>Edit this taxon</q-tooltip>
                          </q-icon>
                          <q-icon v-if="props.node.orderable" name="swap_vert"
                                  size="15px" class="tax-move-hint">
                            <q-tooltip>Click the row, then use the ↑/↓ buttons above to set the taxonomic sequence</q-tooltip>
                          </q-icon>
                        </div>
                    """.replace("__RANK_LABELS__", json.dumps(_TREE_RANK_LABELS))

            # Always create the tree widget so it can be updated after saves.
            tax_tree = ui.tree(
                nodes=tree_data,
                label_key="label",
                children_key="children",
                on_select=_on_node_select,
            ).classes("w-full checklist-tree").props("no-connectors dense")
            tax_tree.add_slot("default-header", _NODE_SLOT)

            async def _expand():
                await asyncio.sleep(0.15)
                await tax_tree.run_method("expandAll")
                # a re-expand (filter/tab change) resets the toggle state
                _collapsed["on"] = False
                _collapse_btn.set_text("Collapse all")
                _collapse_btn.props("icon=unfold_less")

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

    return _refresh_taxonomy_stats, _refresh_tree, tax_tree

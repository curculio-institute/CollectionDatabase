"""Batch tools tab (#78, parent #72).

Build a **collection-scoped** specimen set — by taxon (all specimens of a taxon and its
descendants in the working collection) or by a pasted catalog-number list — then bulk-apply
one operation: set disposition, or reassign the specimens to another collection (give them
away). ``catalog_number`` is never changed.

Everything is scoped to one *working collection*, defaulting to the home collection
(``is_default``); switching to another collection is behind an extra click (progressive
disclosure). Cross-collection specimens can never be listed or modified — the guarantee
lives in ``app/services/batch_ops.py`` (``_load_in_scope`` re-asserts it before any write).
"""
from __future__ import annotations

from nicegui import ui

import app.services.batch_ops as batch
import app.services.repositories as repo_svc
from app.services.vocabularies import disposition_vocab
from app.ui.taxon_search import build_taxon_search
from app.ui.vocab_field import build_vocab_field


def build_batch_tab(session_factory, refreshers: dict | None = None) -> None:
    refreshers = refreshers or {}
    st: dict = {"repo_id": None, "repo_code": "", "matched": [],
                "not_found": [], "foreign": []}

    def _load_default() -> None:
        with session_factory() as s:
            d = repo_svc.get_default(s)
        st["repo_id"] = d.id if d else None
        st["repo_code"] = d.collection_code if d else ""

    def _repo_options(exclude: int | None = None) -> dict:
        with session_factory() as s:
            return {r.id: f"{r.collection_code} — {r.collection_full_name}"
                    for r in repo_svc.list_repositories(s) if r.id != exclude}

    _load_default()

    with ui.card().classes("w-full max-w-5xl mx-auto shadow-sm"):
        ui.label("Batch tools").classes("text-lg font-semibold")
        ui.label("Build a set of specimens from your collection, then set a disposition "
                 "or move them to another collection in one go.") \
            .classes("text-sm").style("color:var(--tp-base-soft)")

        # ── Working collection (progressive disclosure) ──────────────────────
        with ui.row().classes("items-center gap-2 mt-2"):
            wc_label = ui.label().classes("text-sm font-medium")
            change_btn = ui.button("Change", icon="swap_horiz") \
                .props("flat dense no-caps size=sm")
        wc_select = ui.select(
            options=_repo_options(), label="Working collection", with_input=True,
        ).classes("w-full").style("display:none")

        def _sync_wc_label() -> None:
            if st["repo_id"] is None:
                wc_label.set_text("⚠ No default collection set — choose one in Settings, "
                                  "or pick a working collection here.")
                change_btn.set_visibility(True)
                wc_select.style("display:block")
            else:
                wc_label.set_text(f"Working collection: {st['repo_code']} (your collection)")

        def _toggle_change() -> None:
            st["wc_open"] = not st.get("wc_open", False)
            wc_select.style("display:block" if st["wc_open"] else "display:none")
            if st["wc_open"]:
                wc_select.set_options(_repo_options())

        change_btn.on_click(_toggle_change)

        def _on_wc_change(e) -> None:
            if not e.value:
                return
            with session_factory() as s:
                r = s.get(repo_svc.Repository, e.value)
            if r is not None:
                st["repo_id"] = r.id
                st["repo_code"] = r.collection_code
                st["matched"] = []; st["not_found"] = []; st["foreign"] = []
                _sync_wc_label()
                _render_results()
        wc_select.on_value_change(_on_wc_change)

        ui.separator().classes("my-3")

        # ── Build the specimen set ───────────────────────────────────────────
        ui.label("1 · Build the set").classes("text-sm font-semibold")
        mode = ui.toggle({"taxon": "By taxon", "paste": "Paste catalog numbers"},
                         value="taxon").props("no-caps").classes("mt-1")

        with ui.column().classes("w-full mt-2") as taxon_box:
            ui.label("Fetch every specimen of a taxon (and its subordinate taxa) that is "
                     "in the working collection.").classes("text-xs") \
                .style("color:var(--tp-base-soft)")
            with ui.row().classes("w-full items-end gap-2"):
                taxon_state = build_taxon_search(
                    session_factory, sources=("local",),
                    placeholder="Genus or species already in your collection…")
                ui.button("Fetch", icon="search", on_click=lambda: _fetch_by_taxon()) \
                    .props("no-caps color=secondary")

        with ui.column().classes("w-full mt-2") as paste_box:
            paste_in = ui.textarea(
                "Catalog numbers",
                placeholder="Paste catalog numbers — separated by spaces, commas, or new lines",
            ).props("outlined autogrow").classes("w-full")
            ui.button("Match", icon="playlist_add_check",
                      on_click=lambda: _match_pasted()).props("no-caps color=secondary")

        def _sync_mode() -> None:
            taxon_box.set_visibility(mode.value == "taxon")
            paste_box.set_visibility(mode.value == "paste")
        mode.on_value_change(lambda _: _sync_mode())

        ui.separator().classes("my-3")

        # ── Results + apply ──────────────────────────────────────────────────
        results = ui.column().classes("w-full")

        def _guard_repo() -> bool:
            if st["repo_id"] is None:
                ui.notify("Pick a working collection first.", type="warning")
                return False
            return True

        def _fetch_by_taxon() -> None:
            if not _guard_repo():
                return
            tid = taxon_state.get("taxon_id")
            if not tid:
                ui.notify("Choose a taxon first.", type="warning")
                return
            with session_factory() as s:
                st["matched"] = batch.fetch_by_taxon(
                    s, repository_id=st["repo_id"], taxon_id=tid)
            st["not_found"] = []; st["foreign"] = []
            _render_results()

        def _match_pasted() -> None:
            if not _guard_repo():
                return
            numbers = batch.parse_catalog_numbers(paste_in.value or "")
            if not numbers:
                ui.notify("Paste at least one catalog number.", type="warning")
                return
            with session_factory() as s:
                res = batch.match_catalog_numbers(
                    s, repository_id=st["repo_id"], numbers=numbers)
            st["matched"] = res.matched
            st["not_found"] = res.not_found
            st["foreign"] = res.foreign
            _render_results()

        def _copy_catalogs() -> None:
            text = "\n".join(m.catalog for m in st["matched"])
            ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
            ui.notify(f"Copied {len(st['matched'])} catalog number(s).", type="positive")

        def _download_csv() -> None:
            import csv, io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["catalogNumber", "taxon", "disposition"])
            for m in st["matched"]:
                w.writerow([m.catalog, m.taxon_label, m.disposition])
            ui.download(buf.getvalue().encode(), "batch_selection.csv")

        def _render_results() -> None:
            results.clear()
            with results:
                # Problems first — loud, never silently dropped.
                for cat in st["not_found"]:
                    ui.label(f"✗ {cat} — no specimen with this catalog number").classes(
                        "text-xs text-red-600")
                for f in st["foreign"]:
                    ui.label(f"⚠ {f.catalog} — belongs to {f.collection_code}, "
                             f"not your working collection → excluded").classes(
                        "text-xs text-amber-700")

                rows = st["matched"]
                if not rows:
                    ui.label("No specimens in the set yet.").classes("text-sm") \
                        .style("color:var(--tp-base-soft)")
                    return

                with ui.row().classes("items-center gap-2 mt-1"):
                    ui.label(f"{len(rows)} specimen(s) in the working collection") \
                        .classes("text-sm font-medium")
                    ui.button("Copy catalog numbers", icon="content_copy",
                              on_click=_copy_catalogs).props("flat dense no-caps size=sm")
                    ui.button("CSV", icon="download",
                              on_click=_download_csv).props("flat dense no-caps size=sm")

                ui.table(
                    columns=[
                        {"name": "catalog", "label": "Catalog number", "field": "catalog", "align": "left", "sortable": True},
                        {"name": "taxon", "label": "Current taxon", "field": "taxon", "align": "left"},
                        {"name": "disp", "label": "Disposition", "field": "disp", "align": "left"},
                    ],
                    rows=[{"catalog": m.catalog, "taxon": m.taxon_label,
                           "disp": m.disposition or "—"} for m in rows],
                    row_key="catalog",
                    pagination=10,
                ).classes("w-full").props("flat dense")

                # ── Apply ──
                ui.separator().classes("my-3")
                ui.label("2 · Apply to these specimens").classes("text-sm font-semibold")
                op = ui.toggle({"disp": "Set disposition", "move": "Reassign collection"},
                               value="disp").props("no-caps").classes("mt-1")

                with ui.row().classes("w-full items-end gap-2 mt-1") as disp_row:
                    disp_field = build_vocab_field(
                        session_factory, disposition_vocab, "Disposition")
                    ui.button("Apply disposition", icon="check",
                              on_click=lambda: _apply_disposition(disp_field)) \
                        .props("no-caps color=secondary")

                with ui.row().classes("w-full items-end gap-2 mt-1") as move_row:
                    target_sel = ui.select(
                        options=_repo_options(exclude=st["repo_id"]),
                        label="Move to collection", with_input=True,
                    ).classes("flex-1")
                    ui.button("Reassign", icon="drive_file_move",
                              on_click=lambda: _apply_move(target_sel)) \
                        .props("no-caps color=negative")

                def _sync_op() -> None:
                    disp_row.set_visibility(op.value == "disp")
                    move_row.set_visibility(op.value == "move")
                op.on_value_change(lambda _: _sync_op())
                _sync_op()

        def _after_apply() -> None:
            # Refresh cross-tab views that show membership / disposition.
            for key in ("explore", "records", "taxonomy_tree"):
                fn = refreshers.get(key)
                if fn:
                    try:
                        fn()
                    except Exception:
                        pass

        def _apply_disposition(disp_field) -> None:
            if not st["matched"]:
                return
            co_ids = [m.co_id for m in st["matched"]]
            try:
                with session_factory() as s:
                    with s.begin():
                        disp_id = disp_field["commit"](s)
                        n = batch.apply_disposition(
                            s, source_repository_id=st["repo_id"],
                            co_ids=co_ids, disposition_id=disp_id)
                ui.notify(f"Set disposition on {n} specimen(s).", type="positive")
                _refetch(); _after_apply()
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")

        def _apply_move(target_sel) -> None:
            if not st["matched"]:
                return
            if not target_sel.value:
                ui.notify("Choose a target collection.", type="warning")
                return
            co_ids = [m.co_id for m in st["matched"]]
            try:
                with session_factory() as s:
                    with s.begin():
                        n = batch.apply_repository(
                            s, source_repository_id=st["repo_id"], co_ids=co_ids,
                            target_repository_id=int(target_sel.value))
                ui.notify(f"Moved {n} specimen(s) to the target collection.", type="positive")
                # The moved specimens have left the working collection — clear the set.
                st["matched"] = []; st["not_found"] = []; st["foreign"] = []
                _render_results(); _after_apply()
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")

        def _refetch() -> None:
            """Re-pull the current set so the table reflects the applied change."""
            if not st["matched"]:
                return
            co_ids = [m.co_id for m in st["matched"]]
            with session_factory() as s:
                cats = batch.match_catalog_numbers(
                    s, repository_id=st["repo_id"],
                    numbers=[m.catalog for m in st["matched"]])
            st["matched"] = cats.matched
            _render_results()

        # initial paint
        _sync_wc_label()
        _sync_mode()
        _render_results()

        # If no default was set at build time, pick it up once configured.
        def _poll_default() -> None:
            if st["repo_id"] is None:
                _load_default()
                if st["repo_id"] is not None:
                    _sync_wc_label()
        ui.timer(2.0, _poll_default)

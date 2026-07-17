"""Bulk import tab (#39) — the wholesale, staged importer, modelled on TaxonWorks'
Import Dataset (distinct from the row-by-row Import & Assign).

Thin UI over ``app/services/bulk_import.py``: upload a CSV of specimen records → it stages
every row (writing no specimens) → a status grid and a per-reason blocker list show what
will and won't import → "Import ready" runs the idempotent, resumable import in chunks.
Setting the dataset's nomenclatural code re-stages (the resolve-once seam).
"""
from __future__ import annotations

from nicegui import ui

import app.services.bulk_import as bi
from app.vocab import NOMENCLATURAL_CODES

_CODE_OPTS = {c: c for c in NOMENCLATURAL_CODES}


def build_bulk_import_tab(session_factory, refreshers: dict | None = None) -> None:
    refreshers = refreshers or {}
    st: dict = {"dataset_id": None}

    def _sf():
        return session_factory()

    def _select(did: int) -> None:
        st["dataset_id"] = did
        _refresh_detail()

    # ── upload / stage card (top) ───────────────────────────────────────────
    with ui.card().classes("w-full"):
        ui.label("Bulk import specimen records").classes("text-lg font-medium")
        ui.label(
            "Stage a whole DwC/CSV of specimen records, review what will and won't "
            "import, then import in one go. Each row must already carry its identifier "
            "(catalogNumber); a collection column can target other collections (blank = "
            "your default). Records are matched on (collection, catalog number), never "
            "duplicated. To stamp one specimen at a time use Import & Assign."
        ).classes("text-sm").style("color:var(--tp-base-soft)")

        with ui.row().classes("items-center gap-3 mt-2"):
            name_in = ui.input("Dataset name", placeholder="e.g. 2024 accessions") \
                .props("outlined dense").style("min-width:260px")
            code_in = ui.select(_CODE_OPTS, label="Nomenclatural code", value="ICZN") \
                .props("outlined dense").style("min-width:160px")
        ui.label("The code governs the names in this file — it resolves each "
                 "scientificName to a taxon. It is never guessed.") \
            .classes("text-xs").style("color:var(--tp-base-soft)")

        stage_status = ui.label("").classes("text-sm mt-1")

        def _on_upload(e) -> None:
            raw = e.content.read()
            name = (name_in.value or "").strip() or e.name
            try:
                with _sf() as s:
                    ds = bi.create_occurrence_dataset(
                        s, name=name, filename=e.name, content=raw,
                        nomenclatural_code=code_in.value)
                    s.commit()
                    did = ds.id
                    counts = bi.progress(s, did)
            except Exception as exc:                       # noqa: BLE001
                stage_status.set_text(f"Could not stage: {exc}")
                stage_status.style("color:var(--tp-negative)")
                return
            stage_status.set_text(
                f"✓ staged {counts.get('total', 0)} rows — "
                f"{counts.get('ready', 0)} ready, {counts.get('blocked', 0)} blocked")
            stage_status.style("color:var(--tp-secondary)")
            _refresh_lists()
            _select(did)

        ui.upload(label="Choose records CSV…", on_upload=_on_upload, auto_upload=True) \
            .props("accept=.csv,text/csv flat").classes("mt-2")

    # ── dataset list ────────────────────────────────────────────────────────
    ui.separator()
    ui.label("Staged datasets").classes("text-sm font-medium")
    lists = ui.column().classes("w-full gap-1")

    # ── detail of the selected dataset ──────────────────────────────────────
    detail = ui.column().classes("w-full gap-3")

    def _refresh_lists() -> None:
        lists.clear()
        with _sf() as s:
            datasets = [(d.id, d.name, bi.progress(s, d.id))
                        for d in bi.list_datasets(s)]
        with lists:
            if not datasets:
                ui.label("No staged datasets yet.").style("color:var(--tp-base-soft)")
            for did, name, counts in datasets:
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.button(name, icon="folder_open",
                              on_click=lambda d=did: _select(d)) \
                        .props("flat dense align=left").classes("normal-case")
                    ui.label(
                        f"{counts.get('imported', 0)}/{counts.get('total', 0)} imported") \
                        .classes("text-xs").style("color:var(--tp-base-soft)")

    def _refresh_detail() -> None:
        detail.clear()
        did = st["dataset_id"]
        if did is None:
            return
        with _sf() as s:
            ds = bi.get_dataset(s, did)
            if ds is None:
                st["dataset_id"] = None
                return
            counts = bi.progress(s, did)
            blockers = bi.blocker_summary(s, did)
            blocked_sample = [(r.row_index, r.resolved_name, r.error_message)
                              for r in bi.sample_records(s, did, "blocked", 10)]
            name, code, status = ds.name, ds.nomenclatural_code, ds.status

        with detail, ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label(name).classes("text-lg font-medium")
                ui.badge(status).props("color=grey")

            with ui.row().classes("gap-4 items-center"):
                for key, colour in (("ready", "primary"), ("imported", "positive"),
                                    ("blocked", "warning"), ("errored", "negative")):
                    ui.badge(f"{counts.get(key, 0)} {key}").props(f"color={colour}")
                ui.label(f"{counts.get('total', 0)} total") \
                    .style("color:var(--tp-base-soft)")

            with ui.row().classes("items-center gap-2"):
                code_sel = ui.select(_CODE_OPTS, label="Nomenclatural code", value=code) \
                    .props("outlined dense").style("min-width:160px")

                def _apply_code() -> None:
                    with _sf() as s:
                        bi.set_dataset_code(s, did, code_sel.value)
                        s.commit()
                    ui.notify("Re-staged with the new code", type="positive")
                    _refresh_detail()
                ui.button("Apply", on_click=_apply_code).props("flat dense")

            with ui.row().classes("items-center gap-2 mt-1"):
                def _import() -> None:
                    with _sf() as s:
                        while True:
                            c = bi.import_ready(s, did, max_records=500)
                            s.commit()
                            if c.get("remaining", 0) <= 0:
                                break
                        imported_n = bi.progress(s, did).get("imported", 0)
                    for fn in refreshers.values():
                        try:
                            fn()
                        except Exception:
                            pass
                    ui.notify(f"Imported — {imported_n} names now in the tree",
                              type="positive")
                    _refresh_lists()
                    _refresh_detail()

                ready_n = counts.get("ready", 0)
                ui.button(f"Import {ready_n} ready", icon="play_arrow",
                          on_click=_import).props("unelevated").set_enabled(ready_n > 0)

                if counts.get("errored", 0):
                    def _retry() -> None:
                        with _sf() as s:
                            bi.retry_errored(s, did)
                            s.commit()
                        _refresh_detail()
                    ui.button("Retry errored", icon="replay",
                              on_click=_retry).props("flat dense")

                def _delete() -> None:
                    with _sf() as s:
                        bi.delete_dataset(s, did)
                        s.commit()
                    st["dataset_id"] = None
                    ui.notify("Dataset removed (imported names kept)", type="info")
                    _refresh_lists()
                    _refresh_detail()
                ui.button("Remove dataset", icon="delete",
                          on_click=_delete).props("flat dense color=negative")

            if blockers:
                ui.separator()
                ui.label("Not importable yet").classes("text-sm font-medium")
                for reason, n in blockers:
                    ui.label(f"· {n} × {reason}").classes("text-sm") \
                        .style("color:var(--tp-base-soft)")
                if blocked_sample:
                    with ui.expansion("Show blocked rows").classes("w-full"):
                        for idx, rn, msg in blocked_sample:
                            ui.label(f"row {idx + 1}: {rn or '—'} — {msg}") \
                                .classes("text-xs").style("color:var(--tp-base-soft)")

    _refresh_lists()
    _refresh_detail()

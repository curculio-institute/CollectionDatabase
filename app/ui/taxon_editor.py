"""Taxon curation dialogs: New Taxon and Edit Taxon."""
from __future__ import annotations

from nicegui import ui

from app.services.taxa import (
    create_taxon_direct,
    delete_taxon,
    search_taxa,
    update_taxon,
)
from app.models import Taxon

TAXON_RANKS = [
    "kingdom", "phylum", "subphylum", "class", "subclass",
    "superorder", "order", "suborder", "superfamily",
    "family", "subfamily", "tribe", "subtribe",
    "genus", "subgenus", "species", "subspecies", "variety", "form",
]
NOMEN_CODES = {
    "ICZN":  "ICZN",
    "ICN":   "🌿 ICN",
    "ICNP":  "ICNP",
    "ICVCV": "ICVCV",
}


def _taxon_opts(session_factory) -> dict:
    with session_factory() as s:
        taxa = search_taxa(s, "", limit=500)
    return {t.id: t.label for t in taxa}


def _build_taxon_form(container, session_factory, *, taxon: Taxon | None = None):
    """Render all taxon fields into *container*. Returns {get_fields, validate}.

    taxon=None  → new-taxon mode  → parent is required.
    taxon set with parent_name_usage_id=None  → editing a root taxon → parent optional.
    taxon set with parent_name_usage_id set   → editing non-root → parent required.
    """
    editing_root = taxon is not None and taxon.parent_name_usage_id is None

    with container:
        name_in = ui.input(
            "Scientific name (without authorship) *",
            value=taxon.scientific_name if taxon else "",
        ).classes("w-full")

        with ui.row().classes("w-full gap-3"):
            rank_sel = ui.select(
                TAXON_RANKS,
                label="Rank *",
                value=taxon.taxon_rank if taxon else None,
            ).classes("flex-1")
            status_sel = ui.select(
                ["accepted", "synonym"],
                label="Taxonomic status *",
                value=taxon.taxonomic_status if taxon else "accepted",
            ).classes("flex-1")

        auth_in = ui.input(
            "Authorship, e.g. Linnaeus, 1758 or (Linnaeus, 1758)",
            value=taxon.scientific_name_authorship if taxon else "",
        ).classes("w-full")

        nomen_sel = ui.select(
            NOMEN_CODES,
            label="Nomenclatural code *",
            value=taxon.nomenclatural_code if taxon else None,
        ).classes("w-full")

        # Parent taxon — with_input provides client-side search over pre-loaded opts.
        # Root taxa (no parent) are only produced by seeding, not via this form.
        all_taxon_opts = _taxon_opts(session_factory)
        if editing_root:
            # Allow keeping no parent for an existing root row.
            parent_opts = {None: "(root — no parent)", **all_taxon_opts}
            parent_val = None
        else:
            parent_opts = all_taxon_opts
            parent_val = taxon.parent_name_usage_id if taxon else None

        parent_sel = ui.select(
            options=parent_opts,
            with_input=True,
            clearable=not editing_root,
            label="Parent taxon *" if not editing_root else "Parent taxon",
            value=parent_val,
        ).classes("w-full")

        # Accepted name link (synonym → accepted name)
        accepted_sel = ui.select(
            options={None: "(none)", **all_taxon_opts},
            with_input=True,
            clearable=True,
            label="Accepted name (if synonym)",
            value=taxon.accepted_name_usage_id if taxon else None,
        ).classes("w-full")

        tw_in = ui.input(
            "TaxonWorks OTU ID",
            value=str(taxon.taxonworks_otu_id) if taxon and taxon.taxonworks_otu_id else "",
            placeholder="paste OTU id…",
        ).classes("w-full")

    def get_fields() -> dict:
        try:
            otu_id = int(tw_in.value.strip()) if tw_in.value.strip() else None
        except ValueError:
            otu_id = None
        return {
            "scientific_name": name_in.value.strip(),
            "taxon_rank": rank_sel.value or "",
            "taxonomic_status": status_sel.value or "accepted",
            "scientific_name_authorship": auth_in.value.strip() or None,
            "parent_name_usage_id": parent_sel.value or None,
            "accepted_name_usage_id": accepted_sel.value or None,
            "nomenclatural_code": nomen_sel.value or None,
            "taxonworks_otu_id": otu_id,
        }

    def validate(fields: dict) -> str | None:
        if not fields["scientific_name"]:
            return "Scientific name is required."
        if not fields["taxon_rank"]:
            return "Rank is required."
        if not fields["nomenclatural_code"]:
            return "Nomenclatural code is required."
        if not editing_root and not fields["parent_name_usage_id"]:
            return "Parent taxon is required (select a root taxon if top-level)."
        if fields["taxonomic_status"] == "synonym" and not fields["accepted_name_usage_id"]:
            return "Synonyms must link to an accepted name."
        return None

    return {"get_fields": get_fields, "validate": validate}


def build_taxon_editor(session_factory, on_saved: callable) -> None:
    """Render New Taxon and Edit Taxon buttons + their dialogs in the current container."""

    # ── New Taxon dialog ────────────────────────────────────────────────────
    new_dialog = ui.dialog()
    with new_dialog:
        with ui.card().classes("min-w-[480px] max-w-[600px]"):
            ui.label("New Taxon").classes("section-label mb-3")
            ui.separator().classes("mb-3")
            form_col = ui.column().classes("w-full gap-2")
            form_api: dict = {}

            with ui.row().classes("mt-4 gap-2 justify-end w-full"):
                ui.button("Cancel", on_click=new_dialog.close).props("flat")
                save_new_btn = ui.button("Save", icon="save").props("color=secondary")

    def _open_new():
        form_col.clear()
        form_api.clear()
        form_api.update(_build_taxon_form(form_col, session_factory))
        new_dialog.open()

    def _save_new():
        fields = form_api["get_fields"]()
        err = form_api["validate"](fields)
        if err:
            ui.notify(err, type="negative")
            return
        try:
            with session_factory() as s:
                with s.begin():
                    create_taxon_direct(s, **fields)
            new_dialog.close()
            ui.notify("Taxon created.", type="positive")
            on_saved()
        except Exception as exc:
            ui.notify(f"Failed: {exc}", type="negative")

    save_new_btn.on_click(_save_new)

    # ── Edit Taxon dialog ───────────────────────────────────────────────────
    edit_dialog = ui.dialog()
    _edit_state: dict = {"taxon_id": None}

    with edit_dialog:
        with ui.card().classes("min-w-[480px] max-w-[600px]"):
            ui.label("Edit Taxon").classes("section-label mb-3")
            ui.separator().classes("mb-3")

            edit_sel = ui.select(
                options=_taxon_opts(session_factory),
                with_input=True,
                clearable=True,
                label="Select taxon to edit…",
            ).classes("w-full mb-3")

            edit_form_col = ui.column().classes("w-full gap-2")
            edit_form_api: dict = {}

            def _on_edit_select(e):
                tid = e.value
                if not tid:
                    edit_form_col.clear()
                    edit_form_api.clear()
                    _edit_state["taxon_id"] = None
                    delete_btn.disable()
                    return
                _edit_state["taxon_id"] = tid
                with session_factory() as s:
                    taxon = s.get(Taxon, tid)
                    if taxon is None:
                        ui.notify("Taxon not found.", type="warning")
                        return
                    s.expunge(taxon)
                edit_form_col.clear()
                edit_form_api.clear()
                edit_form_api.update(
                    _build_taxon_form(edit_form_col, session_factory, taxon=taxon)
                )
                with session_factory() as s:
                    blocked = (
                        s.query(Taxon).filter(Taxon.parent_name_usage_id == tid).count() > 0
                        or s.query(Taxon).filter(Taxon.accepted_name_usage_id == tid).count() > 0
                    )
                if blocked:
                    delete_btn.disable()
                    delete_btn.tooltip("Cannot delete: taxon has children or synonyms")
                else:
                    delete_btn.enable()
                    delete_btn.tooltip("Delete this taxon permanently")

            edit_sel.on_value_change(_on_edit_select)

            with ui.row().classes("mt-4 gap-2 justify-end w-full"):
                delete_btn = (
                    ui.button("Delete", icon="delete")
                    .props("flat color=negative")
                )
                delete_btn.disable()
                ui.button("Cancel", on_click=edit_dialog.close).props("flat")
                save_edit_btn = ui.button("Save changes", icon="save").props("color=secondary")

    def _open_edit():
        edit_form_col.clear()
        edit_form_api.clear()
        _edit_state["taxon_id"] = None
        edit_sel.options = _taxon_opts(session_factory)
        edit_sel.value = None
        edit_sel.update()
        delete_btn.disable()
        edit_dialog.open()

    def _save_edit():
        tid = _edit_state.get("taxon_id")
        if not tid or not edit_form_api:
            ui.notify("Select a taxon first.", type="warning")
            return
        fields = edit_form_api["get_fields"]()
        err = edit_form_api["validate"](fields)
        if err:
            ui.notify(err, type="negative")
            return
        try:
            with session_factory() as s:
                with s.begin():
                    update_taxon(s, tid, **fields)
            edit_dialog.close()
            ui.notify("Taxon updated.", type="positive")
            on_saved()
        except Exception as exc:
            ui.notify(f"Failed: {exc}", type="negative")

    def _delete_taxon():
        tid = _edit_state.get("taxon_id")
        if not tid:
            return
        confirm_dlg = ui.dialog()
        with confirm_dlg:
            with ui.card():
                ui.label("Delete this taxon permanently?").classes("font-medium mb-3")
                with ui.row().classes("gap-2 justify-end"):
                    ui.button("Cancel", on_click=confirm_dlg.close).props("flat")

                    def _confirmed():
                        try:
                            with session_factory() as s:
                                with s.begin():
                                    delete_taxon(s, tid)
                            confirm_dlg.close()
                            edit_dialog.close()
                            ui.notify("Taxon deleted.", type="positive")
                            on_saved()
                        except Exception as exc:
                            confirm_dlg.close()
                            ui.notify(f"Failed: {exc}", type="negative")

                    ui.button("Delete", icon="delete", on_click=_confirmed).props("color=negative")
        confirm_dlg.open()

    save_edit_btn.on_click(_save_edit)
    delete_btn.on_click(_delete_taxon)

    # ── Render buttons ──────────────────────────────────────────────────────
    with ui.row().classes("items-center gap-2"):
        (
            ui.button("New Taxon", icon="add", on_click=_open_new)
            .props("flat color=secondary")
        )
        (
            ui.button("Edit Taxon", icon="edit", on_click=_open_edit)
            .props("flat")
        )

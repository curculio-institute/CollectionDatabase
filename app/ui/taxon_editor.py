"""Taxon curation dialogs: New Taxon and Edit Taxon."""
from __future__ import annotations

from nicegui import ui

from app.services.taxa import (
    TAXON_RANKS,
    create_taxon_direct,
    delete_taxon,
    search_taxa,
    update_taxon,
)
from app.models import Taxon, TaxonDetermination


def _taxon_opts(session_factory) -> dict:
    with session_factory() as s:
        taxa = search_taxa(s, "", limit=500)
    return {t.id: t.label for t in taxa}


def _build_taxon_form(
    container, session_factory, *, taxon: Taxon | None = None, prefill: dict | None = None
):
    """Render all taxon fields into *container*. Returns {get_fields, validate}.

    taxon=None  → new-taxon mode  → parent is required.
    taxon set with parent_name_usage_id=None  → editing a root taxon → parent optional.
    taxon set with parent_name_usage_id set   → editing non-root → parent required.

    prefill (new-taxon mode only) seeds the fields from a parsed DwC row:
    scientific_name / taxon_rank / scientific_name_authorship / parent_name_usage_id /
    accepted_name_usage_id. Every value is a starting point the user can change; the
    nomenclatural code is still inherited from whichever parent is chosen.
    """
    editing_root = taxon is not None and taxon.parent_name_usage_id is None
    pf = prefill or {}

    with session_factory() as s:
        all_taxa_raw = search_taxa(s, "", limit=500)
        _all_taxa = [
            (t.id, t.label, t.taxon_rank, t.nomenclatural_code)
            for t in all_taxa_raw
        ]

    def _make_parent_opts(rank: str | None) -> dict:
        opts = {}
        for tid, label, t_rank, _t_code in _all_taxa:
            if rank and rank in TAXON_RANKS and t_rank in TAXON_RANKS:
                if TAXON_RANKS.index(t_rank) >= TAXON_RANKS.index(rank):
                    continue
            opts[tid] = label
        return opts

    all_taxon_opts = {tid: label for tid, label, _, _ in _all_taxa}
    # Nomenclatural code is inherited from the chosen parent, never entered by
    # hand: the code must always equal the parent's (a child cannot be governed
    # by a different code than its lineage). Look it up from the loaded taxa.
    code_by_id = {tid: code for tid, _, _, code in _all_taxa}

    with container:
        name_in = ui.input(
            "Scientific name (without authorship) *",
            value=taxon.scientific_name if taxon else pf.get("scientific_name", ""),
        ).classes("w-full")

        rank_sel = ui.select(
            TAXON_RANKS,
            label="Rank *",
            value=taxon.taxon_rank if taxon else pf.get("taxon_rank"),
        ).classes("w-full")
        # Synonymy is controlled solely by the accepted-name link below: a taxon
        # is a synonym iff an accepted name is set. There is no separate status
        # field (taxonomicStatus is derived from the link at DwC export time).

        auth_in = ui.input(
            "Authorship, e.g. Linnaeus, 1758 or (Linnaeus, 1758)",
            value=(taxon.scientific_name_authorship or "") if taxon
            else (pf.get("scientific_name_authorship") or ""),
        ).classes("w-full")

        # Parent taxon: filtered to valid parents (rank above child). The
        # nomenclatural code is inherited from whichever parent is chosen.
        init_parent_opts = _make_parent_opts(
            taxon.taxon_rank if taxon else pf.get("taxon_rank")
        )
        if editing_root:
            init_parent_opts = {None: "(root — no parent)", **init_parent_opts}
            parent_val = None
        else:
            parent_val = taxon.parent_name_usage_id if taxon else pf.get("parent_name_usage_id")

        parent_sel = ui.select(
            options=init_parent_opts,
            with_input=True,
            clearable=not editing_root,
            label="Parent taxon *" if not editing_root else "Parent taxon",
            value=parent_val,
        ).classes("w-full")

        # Accepted-name link — all taxa, no rank filter. Setting this is what
        # makes the taxon a synonym (there is no separate status field).
        accepted_sel = ui.select(
            options={None: "(none — this is an accepted name)", **all_taxon_opts},
            with_input=True,
            clearable=True,
            label="Accepted name (set to mark this taxon a synonym)",
            value=taxon.accepted_name_usage_id if taxon else pf.get("accepted_name_usage_id"),
        ).classes("w-full")

        tw_in = ui.input(
            "TaxonWorks OTU ID",
            value=str(taxon.taxonworks_otu_id) if taxon and taxon.taxonworks_otu_id else "",
            placeholder="paste OTU id…",
        ).classes("w-full")

    def _refresh_parent_opts():
        new_opts = _make_parent_opts(rank_sel.value)
        if editing_root:
            new_opts = {None: "(root — no parent)", **new_opts}
        cur = parent_sel.value
        if cur and cur not in new_opts:
            parent_sel.value = None
            ui.notify("Parent cleared: no longer valid for the selected rank.", type="warning")
        parent_sel.options = new_opts
        parent_sel.update()

    rank_sel.on_value_change(lambda _: _refresh_parent_opts())

    def get_fields() -> dict:
        try:
            raw_otu = (tw_in.value or "").strip()
            otu_id = int(raw_otu) if raw_otu else None
        except ValueError:
            otu_id = None
        parent_id = parent_sel.value or None
        if parent_id:
            # Normal case: inherit the code from the chosen parent.
            nomen_code = code_by_id.get(parent_id)
        else:
            # No parent → editing a seeded root; preserve its existing code.
            nomen_code = taxon.nomenclatural_code if taxon else None
        return {
            "scientific_name": (name_in.value or "").strip(),
            "taxon_rank": rank_sel.value or "",
            "scientific_name_authorship": (auth_in.value or "").strip() or None,
            "parent_name_usage_id": parent_id,
            "accepted_name_usage_id": accepted_sel.value or None,
            "nomenclatural_code": nomen_code,
            "taxonworks_otu_id": otu_id,
        }

    def validate(fields: dict) -> str | None:
        if not fields["scientific_name"]:
            return "Scientific name is required."
        if not fields["taxon_rank"]:
            return "Rank is required."
        if not editing_root and not fields["parent_name_usage_id"]:
            return "Parent taxon is required (select a root taxon if top-level)."
        # Code is inherited from the parent; a NULL here means the parent itself
        # has no code (a data-integrity problem, not a missing user input).
        if not fields["nomenclatural_code"]:
            return "Cannot determine nomenclatural code: the selected parent has none."

        parent_id  = fields.get("parent_name_usage_id")
        child_rank = fields.get("taxon_rank", "")
        if parent_id and child_rank:
            with session_factory() as s:
                parent = s.get(Taxon, parent_id)
            if parent:
                if child_rank in TAXON_RANKS and parent.taxon_rank in TAXON_RANKS:
                    if TAXON_RANKS.index(parent.taxon_rank) >= TAXON_RANKS.index(child_rank):
                        return (
                            f"Rank conflict: '{parent.scientific_name}' is "
                            f"{parent.taxon_rank!r}, which is not above {child_rank!r}."
                        )
        return None

    return {"get_fields": get_fields, "validate": validate}


def open_new_taxon_dialog(
    session_factory, *, prefill: dict | None = None, on_created=None
) -> None:
    """Open a one-off New Taxon dialog and create the taxon on save.

    The single new-taxon dialog shared by every caller (Taxonomy tab's
    "New Taxon" button and Import & Assign's manual add). ``prefill`` seeds the
    form (see _build_taxon_form); ``on_created(new_id)`` runs after a successful
    create — callers use it to refresh a view or route the new taxon back into a
    determination. The code is inherited from the chosen parent, never entered.
    """
    dialog = ui.dialog()
    with dialog:
        with ui.card().classes("min-w-[480px] max-w-[600px]"):
            ui.label("New Taxon").classes("section-label mb-3")
            ui.separator().classes("mb-3")
            form_col = ui.column().classes("w-full gap-2")
            form_api = _build_taxon_form(form_col, session_factory, prefill=prefill)
            with ui.row().classes("mt-4 gap-2 justify-end w-full"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                save_btn = ui.button("Save", icon="save").props("color=secondary")

    def _save():
        fields = form_api["get_fields"]()
        err = form_api["validate"](fields)
        if err:
            ui.notify(err, type="negative")
            return
        try:
            with session_factory() as s:
                with s.begin():
                    new_id = create_taxon_direct(s, **fields).id
            dialog.close()
            ui.notify("Taxon created.", type="positive")
            if on_created:
                on_created(new_id)
        except Exception as exc:
            ui.notify(f"Failed: {exc}", type="negative")

    save_btn.on_click(_save)
    # Per-action dialog: delete it (not just close) when dismissed so any timers
    # the form's selects install don't leak (CLAUDE.md dialog-timer note).
    dialog.on_value_change(lambda e: dialog.delete() if not e.value else None)
    dialog.open()


def build_taxon_editor(session_factory, on_saved: callable) -> None:
    """Render New Taxon and Edit Taxon buttons + their dialogs in the current container."""

    def _open_new():
        open_new_taxon_dialog(session_factory, on_created=lambda _id: on_saved())

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
                # Mirror the guards in delete_taxon() (services/taxa.py): a taxon
                # with children, synonyms, or determinations cannot be deleted.
                with session_factory() as s:
                    has_children = s.query(Taxon).filter(Taxon.parent_name_usage_id == tid).count() > 0
                    has_synonyms = s.query(Taxon).filter(Taxon.accepted_name_usage_id == tid).count() > 0
                    has_dets = s.query(TaxonDetermination).filter(
                        TaxonDetermination.taxon_id == tid
                    ).count() > 0
                if has_children or has_synonyms or has_dets:
                    delete_btn.disable()
                    reasons = [
                        label for label, present in (
                            ("children", has_children),
                            ("synonyms", has_synonyms),
                            ("determinations", has_dets),
                        ) if present
                    ]
                    delete_btn.tooltip("Cannot delete: taxon has " + " and ".join(reasons))
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
        ui.timer(0.2, lambda: edit_sel.run_method("showPopup"), once=True)

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

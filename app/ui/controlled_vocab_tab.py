"""Controlled Vocabularies tab — manage people and other reference lists."""
from __future__ import annotations

from nicegui import ui

import app.services.persons as persons_svc


def build_controlled_vocab_tab(session_factory, *, on_person_changed=None) -> None:
    """Render the Controlled Vocabularies tab into the current container."""

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    # ── People ───────────────────────────────────────────────────────────────
    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-2 mb-1"):
            ui.label("People").classes("section-label")
            ui.label(
                "Names used in identifiedBy and recordedBy fields."
            ).classes("text-sm").style("color:var(--tp-base-soft)")

        ui.separator().classes("mb-3")

        table_rows_ref: list = [None]

        def _load_rows() -> list[dict]:
            people = _with_session(persons_svc.list_persons)
            return [
                {
                    "id":    str(p.id),
                    "full":  p.full_name,
                    "abbr":  p.abbreviated_name or "",
                    "orcid": p.orcid or "",
                }
                for p in people
            ]

        people_table = ui.table(
            columns=[
                {"name": "full",  "label": "Full name",        "field": "full",  "align": "left", "sortable": True},
                {"name": "abbr",  "label": "Abbreviated name",  "field": "abbr",  "align": "left"},
                {"name": "orcid", "label": "ORCID",             "field": "orcid", "align": "left"},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=_load_rows(),
            row_key="id",
        ).classes("w-full").props("flat dense")

        people_table.add_slot("body-cell-actions", """
            <q-td :props="props">
                <q-btn flat dense round icon="edit" size="xs"
                    @click="$parent.$emit('edit', props.row)" />
                <q-btn flat dense round icon="delete" size="xs" color="negative"
                    @click="$parent.$emit('delete', props.row)" />
            </q-td>
        """)

        def _refresh_table():
            people_table.rows = _load_rows()
            people_table.update()

        # ── Edit dialog ───────────────────────────────────────────────────
        edit_state: dict = {"id": None}

        with ui.dialog() as edit_dialog, ui.card().classes("w-96"):
            ui.label("Edit person").classes("section-label mb-2")
            dlg_full  = ui.input("Full name *").classes("w-full")
            dlg_abbr  = ui.input("Abbreviated name", placeholder="J. Jilg").classes("w-full mt-2")
            dlg_orcid = ui.input("ORCID", placeholder="0000-0000-0000-0000").classes("w-full mt-2")

            def _save_edit():
                if not dlg_full.value.strip():
                    ui.notify("Full name is required.", type="warning")
                    return
                try:
                    with session_factory() as s:
                        with s.begin():
                            persons_svc.update_person(
                                s, edit_state["id"],
                                full_name=dlg_full.value,
                                abbreviated_name=dlg_abbr.value or None,
                                orcid=dlg_orcid.value or None,
                            )
                    edit_dialog.close()
                    _refresh_table()
                    if on_person_changed:
                        on_person_changed()
                    ui.notify("Person updated.", type="positive")
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=edit_dialog.close).props("flat no-caps")
                ui.button("Save", on_click=_save_edit).props("no-caps color=secondary")

        def _open_edit(row: dict):
            edit_state["id"] = int(row["id"])
            dlg_full.value   = row["full"]
            dlg_abbr.value   = row["abbr"]
            dlg_orcid.value  = row["orcid"]
            edit_dialog.open()

        def _delete_person(row: dict):
            try:
                with session_factory() as s:
                    with s.begin():
                        persons_svc.delete_person(s, int(row["id"]))
                _refresh_table()
                if on_person_changed:
                    on_person_changed()
                ui.notify("Person deleted.", type="positive")
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")

        people_table.on("edit",   lambda e: _open_edit(e.args))
        people_table.on("delete", lambda e: _delete_person(e.args))

        # ── Add person ────────────────────────────────────────────────────
        ui.separator().classes("my-3")
        ui.label("Add person").classes("text-sm font-semibold mb-2")

        with ui.grid(columns=3).classes("w-full gap-3"):
            add_full  = ui.input("Full name *").classes("col-span-1")
            add_abbr  = ui.input("Abbreviated name", placeholder="J. Jilg").classes("col-span-1")
            add_orcid = ui.input("ORCID", placeholder="0000-0000-0000-0000").classes("col-span-1")

        def _add_person():
            if not add_full.value.strip():
                ui.notify("Full name is required.", type="warning")
                return
            try:
                with session_factory() as s:
                    with s.begin():
                        persons_svc.create_person(
                            s,
                            full_name=add_full.value,
                            abbreviated_name=add_abbr.value or None,
                            orcid=add_orcid.value or None,
                        )
                add_full.value  = ""
                add_abbr.value  = ""
                add_orcid.value = ""
                _refresh_table()
                if on_person_changed:
                    on_person_changed()
                ui.notify("Person added.", type="positive")
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")

        with ui.row().classes("w-full items-center mt-2"):
            ui.space()
            ui.button("Add person", icon="person_add", on_click=_add_person) \
                .props("flat no-caps color=secondary")

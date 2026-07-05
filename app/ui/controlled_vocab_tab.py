"""Controlled Vocabularies tab — manage people and other reference lists."""
from __future__ import annotations

from nicegui import ui

import app.services.persons as persons_svc
import app.services.repositories as repo_svc
from app.services.vocabularies import VOCAB_REGISTRY
from app.ui.person_field import build_person_field


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

        def _load_rows() -> list[dict]:
            people = _with_session(persons_svc.list_persons)
            return [
                {
                    "id":      str(p.id),
                    "full":    p.full_name,
                    "abbr":    p.abbreviated_name or "",
                    "orcid":   p.orcid or "",
                    "conf":    "🔒" if p.confidential else "",
                    "consent": "✅" if p.consent_approved else "",
                }
                for p in people
            ]

        people_table = ui.table(
            columns=[
                {"name": "full",  "label": "Full name",        "field": "full",  "align": "left", "sortable": True},
                {"name": "abbr",  "label": "Abbreviated name",  "field": "abbr",  "align": "left"},
                {"name": "orcid", "label": "ORCID",             "field": "orcid", "align": "left"},
                {"name": "consent", "label": "Consented", "field": "consent", "align": "center"},
                {"name": "conf",  "label": "Confidential",      "field": "conf",  "align": "center"},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=_load_rows(),
            row_key="id",
        ).classes("w-full").props("flat dense")

        # edit → merge → delete
        people_table.add_slot("body-cell-actions", """
            <q-td :props="props">
                <q-btn flat dense round icon="edit" size="xs"
                    @click="$parent.$emit('edit', props.row)" />
                <q-btn flat dense round icon="merge_type" size="sm"
                    style="color: #f97316"
                    @click="$parent.$emit('merge', props.row)"
                    title="Merge with another person" />
                <q-btn flat dense round icon="delete" size="xs" color="negative"
                    @click="$parent.$emit('delete', props.row)" />
            </q-td>
        """)

        def _refresh_table():
            people_table.rows = _load_rows()
            people_table.update()

        ui.timer(2.0, _refresh_table)

        # ── Edit dialog ───────────────────────────────────────────────────
        edit_state: dict = {"id": None}

        with ui.dialog() as edit_dialog, ui.card().classes("w-96"):
            ui.label("Edit person").classes("section-label mb-2")
            dlg_full  = ui.input("Full name *").classes("w-full")
            dlg_abbr  = ui.input("Abbreviated name", placeholder="J. Doe").classes("w-full mt-2")
            dlg_orcid = ui.input("ORCID", placeholder="https://orcid.org/0000-0000-0000-0000").classes("w-full mt-2")
            dlg_consent = (
                ui.checkbox("Consented — export with name")
                .props("dense").classes("mt-2")
                .tooltip("The person was asked and agreed to be published WITH their "
                         "name. A record that consent was obtained.")
            )
            dlg_conf  = (
                ui.checkbox("Confidential — obscure this name on export")
                .props("dense")
                .tooltip("On DwC export, this person's name is replaced with the "
                         "generic privacy label wherever they appear as recordedBy / "
                         "identifiedBy. The records themselves are still exported.")
            )
            # Mutually exclusive — opposite export choices.
            dlg_consent.on_value_change(
                lambda e: e.value and dlg_conf.set_value(False))
            dlg_conf.on_value_change(
                lambda e: e.value and dlg_consent.set_value(False))

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
                                confidential=dlg_conf.value,
                                consent_approved=dlg_consent.value,
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
            dlg_conf.value    = bool(row.get("conf"))
            dlg_consent.value = bool(row.get("consent"))
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
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
            except Exception as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")

        # ── Merge dialog ──────────────────────────────────────────────────
        # Click merge on any row → dialog opens immediately.
        # Dialog contains an inline picker for the merge target.
        # Swap button flips which side is absorbed vs. kept.
        _mctx: dict = {"absorb_id": None, "keep_id": None, "others": {}}

        def _person_label(p) -> str:
            parts = [p.full_name]
            if p.abbreviated_name:
                parts.append(p.abbreviated_name)
            if p.orcid:
                parts.append(p.orcid)
            return " · ".join(parts)

        with ui.dialog() as merge_dialog, ui.card().classes("w-[500px]"):
            ui.label("Merge persons").classes("section-label mb-4")

            # Header row: absorbed → kept
            with ui.row().classes("w-full gap-2 items-center"):
                with ui.column().classes("flex-1 gap-1 min-w-0"):
                    ui.label("Absorbed — deleted after merge") \
                        .classes("text-xs uppercase tracking-wide") \
                        .style("color:#f97316")
                    merge_absorb_label = ui.label("").classes("font-semibold text-sm truncate")

                ui.icon("arrow_forward").style("color:var(--tp-base-soft); font-size:1.5rem; flex-shrink:0")

                with ui.column().classes("flex-1 gap-1 min-w-0"):
                    ui.label("Kept — receives all references") \
                        .classes("text-xs uppercase tracking-wide") \
                        .style("color:var(--tp-secondary)")
                    merge_keep_label = ui.label("").classes("font-semibold text-sm truncate")

            # Target list — rebuilt from scratch on every dialog open
            merge_target_container = ui.element("div").classes("w-full mt-3")

            merge_swap_btn = (
                ui.button("Swap sides", icon="swap_horiz")
                .props("flat no-caps dense")
                .style("color:#f97316")
                .classes("mt-2")
            )

            merge_ref_label = (
                ui.label("")
                .classes("text-sm mt-2")
                .style("color:var(--tp-base-soft)")
            )

            ui.html(
                '<p style="color:#b45309; font-size:.82rem; margin-top:6px">'
                "⚠ The absorbed person row will be permanently deleted."
                "</p>"
            )

            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("Cancel", on_click=merge_dialog.close).props("flat no-caps")
                merge_confirm_btn = (
                    ui.button("Merge", icon="merge_type")
                    .props("no-caps")
                    .style("background:#f97316; color:white")
                )

        def _rebuild_target_list(others: dict[int, str], selected_id: int | None) -> None:
            """Rebuild the target-person list inside the dialog from scratch."""
            merge_target_container.clear()
            with merge_target_container:
                for pid, label in others.items():
                    selected = pid == selected_id
                    row_classes = (
                        "w-full flex items-center gap-2 px-3 py-2 rounded cursor-pointer "
                        + ("outline outline-1 outline-secondary bg-secondary/5" if selected else "hover:bg-slate-50 dark:hover:bg-white/5")
                    )
                    row = ui.element("div").classes(row_classes)
                    with row:
                        ui.icon("radio_button_checked" if selected else "radio_button_unchecked") \
                            .style("color:var(--tp-secondary); font-size:1.1rem; flex-shrink:0")
                        ui.label(label).classes("text-sm truncate")
                    row.on("click", lambda _, p=pid: _select_keep(p))

        def _select_keep(pid: int) -> None:
            _mctx["keep_id"] = pid
            _rebuild_target_list(_mctx["others"], pid)
            _update_merge_preview()

        def _update_merge_preview() -> None:
            keep_id   = _mctx["keep_id"]
            absorb_id = _mctx["absorb_id"]
            if keep_id is None or absorb_id is None:
                merge_keep_label.set_text("")
                merge_ref_label.set_text("")
                return
            with session_factory() as s:
                preview = persons_svc.merge_preview(s, keep_id=keep_id, absorb_id=absorb_id)
            merge_keep_label.set_text(preview.keep_name)
            noun = "reference" if preview.reference_count == 1 else "references"
            merge_ref_label.set_text(
                f'{preview.reference_count} {noun} will be re-pointed to "{preview.keep_name}".'
                if preview.reference_count
                else "No existing references to re-point."
            )

        def _open_merge(row: dict) -> None:
            absorb_id = int(row["id"])
            with session_factory() as s:
                all_people = persons_svc.list_persons(s)
            others = {p.id: _person_label(p) for p in all_people if p.id != absorb_id}
            absorb_name = next((p.full_name for p in all_people if p.id == absorb_id), "?")
            default_keep = next(iter(others), None)

            _mctx["absorb_id"] = absorb_id
            _mctx["keep_id"]   = default_keep
            _mctx["others"]    = others

            merge_absorb_label.set_text(absorb_name)
            _rebuild_target_list(others, default_keep)
            _update_merge_preview()
            merge_dialog.open()

        def _swap_merge() -> None:
            new_absorb = _mctx["keep_id"]
            new_keep   = _mctx["absorb_id"]
            if new_absorb is None:
                return
            with session_factory() as s:
                all_people = persons_svc.list_persons(s)
            others      = {p.id: _person_label(p) for p in all_people if p.id != new_absorb}
            absorb_name = next((p.full_name for p in all_people if p.id == new_absorb), "?")
            keep_id     = new_keep if new_keep in others else next(iter(others), None)

            _mctx["absorb_id"] = new_absorb
            _mctx["keep_id"]   = keep_id
            _mctx["others"]    = others

            merge_absorb_label.set_text(absorb_name)
            _rebuild_target_list(others, keep_id)
            _update_merge_preview()

        merge_swap_btn.on_click(_swap_merge)

        def _confirm_merge() -> None:
            keep_id   = _mctx["keep_id"]
            absorb_id = _mctx["absorb_id"]
            if keep_id is None or absorb_id is None or keep_id == absorb_id:
                ui.notify("Select a target person first.", type="warning")
                return
            try:
                with session_factory() as s:
                    with s.begin():
                        persons_svc.merge_persons(s, keep_id=keep_id, absorb_id=absorb_id)
                merge_dialog.close()
                _refresh_table()
                if on_person_changed:
                    on_person_changed()
                ui.notify("Persons merged.", type="positive")
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
            except Exception as exc:
                ui.notify(f"Merge failed: {exc}", type="negative")

        merge_confirm_btn.on_click(_confirm_merge)

        people_table.on("merge",  lambda e: _open_merge(e.args))
        people_table.on("edit",   lambda e: _open_edit(e.args))
        people_table.on("delete", lambda e: _delete_person(e.args))

        # ── Add person ────────────────────────────────────────────────────
        ui.separator().classes("my-3")
        ui.label("Add person").classes("text-sm font-semibold mb-2")

        with ui.grid(columns=3).classes("w-full gap-3"):
            add_full  = ui.input("Full name *").classes("col-span-1")
            add_abbr  = ui.input("Abbreviated name", placeholder="J. Doe").classes("col-span-1")
            add_orcid = ui.input("ORCID", placeholder="https://orcid.org/0000-0000-0000-0000").classes("col-span-1")
        with ui.row().classes("items-center gap-4 mt-1"):
            add_consent = (
                ui.checkbox("Consented — export with name").props("dense")
                .tooltip("Asked and agreed to be published with their name.")
            )
            add_conf = (
                ui.checkbox("Confidential — obscure on export").props("dense")
                .tooltip("Name replaced with the generic privacy label on export.")
            )
            add_consent.on_value_change(lambda e: e.value and add_conf.set_value(False))
            add_conf.on_value_change(lambda e: e.value and add_consent.set_value(False))

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
                            confidential=add_conf.value,
                            consent_approved=add_consent.value,
                        )
                add_full.value  = ""
                add_abbr.value  = ""
                add_orcid.value = ""
                add_conf.value = False
                add_consent.value = False
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

    # ── Collections / institutions ────────────────────────────────────────────
    _build_repository_card(session_factory)

    # ── Generic single-name vocabularies (preparations, …) ────────────────────
    # Each registry entry gets its own card with the same edit / merge / delete /
    # add affordances as People, but for a single ``name`` column. Future single-
    # name vocabularies appear here automatically (see app/services/vocabularies.py).
    for spec in VOCAB_REGISTRY:
        _build_vocab_section(session_factory, spec)


def _build_repository_card(session_factory) -> None:
    """Collections / institutions card (#56). A multi-column controlled vocabulary
    keyed by collectionCode — the source for the identifier label's full collection
    name, the new-specimen default, and (later) the DwC export / TW sync. DwC-mapping
    columns carry the dwc: name; full names + TW ids are local."""

    def _int_or_none(v):
        try:
            return int(v) if str(v).strip() else None
        except (TypeError, ValueError):
            return None

    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-2 mb-1"):
            ui.label("Collections / Institutions").classes("section-label")
            ui.label("Maps a collectionCode to its full names + TaxonWorks ids; "
                     "prints the collection name on identifier labels.") \
                .classes("text-sm").style("color:var(--tp-base-soft)")
        ui.separator().classes("mb-3")

        def _rows() -> list[dict]:
            with session_factory() as s:
                pmap = {p.id: p.full_name for p in persons_svc.list_persons(s)}
                return [
                    {
                        "id":        str(r.id),
                        "ccode":     r.collection_code,
                        "cname":     r.collection_full_name,
                        "icode":     r.institution_code or "",
                        "iname":     r.institution_full_name or "",
                        "person":    pmap.get(r.person_id, ""),
                        "tw_inst":   "" if r.taxonworks_institution_id is None else str(r.taxonworks_institution_id),
                        "tw_coll":   "" if r.taxonworks_collection_id is None else str(r.taxonworks_collection_id),
                    }
                    for r in repo_svc.list_repositories(s)
                ]

        table = ui.table(
            columns=[
                {"name": "ccode", "label": "dwc:collectionCode", "field": "ccode", "align": "left", "sortable": True},
                {"name": "cname", "label": "Collection full name", "field": "cname", "align": "left"},
                {"name": "person", "label": "Contact / owner", "field": "person", "align": "left"},
                {"name": "icode", "label": "dwc:institutionCode", "field": "icode", "align": "left"},
                {"name": "iname", "label": "Institution full name", "field": "iname", "align": "left"},
                {"name": "tw_inst", "label": "TW institution id", "field": "tw_inst", "align": "right"},
                {"name": "tw_coll", "label": "TW collection id", "field": "tw_coll", "align": "right"},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=_rows(),
            row_key="id",
        ).classes("w-full").props("flat dense")
        table.add_slot("body-cell-actions", """
            <q-td :props="props">
                <q-btn flat dense round icon="edit" size="xs"
                    @click="$parent.$emit('edit', props.row)" />
                <q-btn flat dense round icon="delete" size="xs" color="negative"
                    @click="$parent.$emit('delete', props.row)" />
            </q-td>
        """)

        def _refresh():
            table.rows = _rows()
            table.update()
        ui.timer(2.0, _refresh)

        # ── Edit dialog ──
        edit_state: dict = {"id": None}
        with ui.dialog() as edit_dialog, ui.card().classes("w-[460px]"):
            ui.label("Edit collection").classes("section-label mb-2")
            e_ccode = ui.input("dwc:collectionCode *", placeholder="JJPC").classes("w-full")
            e_cname = ui.input("Collection full name *", placeholder="John Doe Personal Collection").classes("w-full mt-2")
            with ui.row().classes("w-full mt-2"):
                e_person = build_person_field(
                    session_factory, "Contact / owner (person)", classes="w-full")
            e_icode = ui.input("dwc:institutionCode").classes("w-full mt-2")
            e_iname = ui.input("Institution full name").classes("w-full mt-2")
            with ui.row().classes("w-full gap-2 mt-2"):
                e_twi = ui.input("TW institution id").classes("flex-1")
                e_twc = ui.input("TW collection id").classes("flex-1")

            def _save_edit():
                if not e_ccode.value.strip() or not e_cname.value.strip():
                    ui.notify("collectionCode and collection full name are required.", type="warning")
                    return
                try:
                    with session_factory() as s:
                        with s.begin():
                            repo_svc.update_repository(
                                s, edit_state["id"],
                                collection_code=e_ccode.value,
                                collection_full_name=e_cname.value,
                                institution_code=e_icode.value or None,
                                institution_full_name=e_iname.value or None,
                                taxonworks_institution_id=_int_or_none(e_twi.value),
                                taxonworks_collection_id=_int_or_none(e_twc.value),
                                person_id=e_person["commit"](s),
                            )
                    edit_dialog.close()
                    _refresh()
                    ui.notify("Collection updated.", type="positive")
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=edit_dialog.close).props("flat no-caps")
                ui.button("Save", on_click=_save_edit).props("no-caps color=secondary")

        def _open_edit(row: dict):
            edit_state["id"] = int(row["id"])
            e_ccode.value = row["ccode"]; e_cname.value = row["cname"]
            e_icode.value = row["icode"]; e_iname.value = row["iname"]
            e_twi.value = row["tw_inst"]; e_twc.value = row["tw_coll"]
            e_person["set_value"](row.get("person") or None)
            edit_dialog.open()

        def _delete(row: dict):
            try:
                with session_factory() as s:
                    with s.begin():
                        repo_svc.delete_repository(s, int(row["id"]))
                _refresh()
                ui.notify("Collection deleted.", type="positive")
            except Exception as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")

        table.on("edit", lambda e: _open_edit(e.args))
        table.on("delete", lambda e: _delete(e.args))

        # ── Add form ──
        ui.separator().classes("my-3")
        ui.label("Add collection").classes("text-sm font-semibold mb-2")
        with ui.grid(columns=2).classes("w-full gap-3"):
            a_ccode = ui.input("dwc:collectionCode *", placeholder="JJPC")
            a_cname = ui.input("Collection full name *", placeholder="John Doe Personal Collection")
            a_person = build_person_field(
                session_factory, "Contact / owner (person)", classes="w-full")
            a_icode = ui.input("dwc:institutionCode")
            a_iname = ui.input("Institution full name")
            a_twi = ui.input("TW institution id")
            a_twc = ui.input("TW collection id")

        def _add():
            if not a_ccode.value.strip() or not a_cname.value.strip():
                ui.notify("collectionCode and collection full name are required.", type="warning")
                return
            try:
                with session_factory() as s:
                    with s.begin():
                        repo_svc.create_repository(
                            s,
                            collection_code=a_ccode.value,
                            collection_full_name=a_cname.value,
                            institution_code=a_icode.value or None,
                            institution_full_name=a_iname.value or None,
                            taxonworks_institution_id=_int_or_none(a_twi.value),
                            taxonworks_collection_id=_int_or_none(a_twc.value),
                            person_id=a_person["commit"](s),
                        )
                for w in (a_ccode, a_cname, a_icode, a_iname, a_twi, a_twc):
                    w.value = ""
                a_person["set_value"](None)
                _refresh()
                ui.notify("Collection added.", type="positive")
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")

        with ui.row().classes("w-full items-center mt-2"):
            ui.space()
            ui.button("Add collection", icon="add", on_click=_add).props("flat no-caps color=secondary")


def _build_vocab_section(session_factory, spec) -> None:
    """Render one single-name controlled-vocabulary card (edit/merge/delete/add).

    Mirrors the People card but for a ``Vocabulary`` with just a ``name`` column.
    The data-entry fields elsewhere (vocab_field) refresh on their own 2-second
    timer, so a change here propagates without an explicit callback."""
    vocab = spec.vocab

    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-2 mb-1"):
            ui.label(spec.title).classes("section-label")
            ui.label(spec.help).classes("text-sm").style("color:var(--tp-base-soft)")
        ui.separator().classes("mb-3")

        def _load_rows() -> list[dict]:
            with session_factory() as s:
                return [{"id": str(o.id), "name": vocab.display(o)} for o in vocab.list(s)]

        table = ui.table(
            columns=[
                {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=_load_rows(),
            row_key="id",
        ).classes("w-full").props("flat dense")

        table.add_slot("body-cell-actions", """
            <q-td :props="props">
                <q-btn flat dense round icon="edit" size="xs"
                    @click="$parent.$emit('edit', props.row)" />
                <q-btn flat dense round icon="merge_type" size="sm"
                    style="color: #f97316"
                    @click="$parent.$emit('merge', props.row)"
                    title="Merge with another entry" />
                <q-btn flat dense round icon="delete" size="xs" color="negative"
                    @click="$parent.$emit('delete', props.row)" />
            </q-td>
        """)

        def _refresh_table():
            table.rows = _load_rows()
            table.update()

        ui.timer(2.0, _refresh_table)

        # ── Edit dialog ───────────────────────────────────────────────────
        edit_state: dict = {"id": None}
        with ui.dialog() as edit_dialog, ui.card().classes("w-96"):
            ui.label(f"Edit {vocab.noun}").classes("section-label mb-2")
            dlg_name = ui.input("Name *").classes("w-full")

            def _save_edit():
                if not (dlg_name.value or "").strip():
                    ui.notify("Name is required.", type="warning")
                    return
                try:
                    with session_factory() as s:
                        with s.begin():
                            vocab.update(s, edit_state["id"], name=dlg_name.value)
                    edit_dialog.close()
                    _refresh_table()
                    ui.notify(f"{spec.title[:-1] if spec.title.endswith('s') else spec.title} updated.", type="positive")
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=edit_dialog.close).props("flat no-caps")
                ui.button("Save", on_click=_save_edit).props("no-caps color=secondary")

        def _open_edit(row: dict):
            edit_state["id"] = int(row["id"])
            dlg_name.value = row["name"]
            edit_dialog.open()

        def _delete_row(row: dict):
            try:
                with session_factory() as s:
                    with s.begin():
                        vocab.delete(s, int(row["id"]))
                _refresh_table()
                ui.notify("Deleted.", type="positive")
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
            except Exception as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")

        # ── Merge dialog ──────────────────────────────────────────────────
        _mctx: dict = {"absorb_id": None, "keep_id": None, "others": {}}

        with ui.dialog() as merge_dialog, ui.card().classes("w-[500px]"):
            ui.label(f"Merge {spec.title.lower()}").classes("section-label mb-4")
            with ui.row().classes("w-full gap-2 items-center"):
                with ui.column().classes("flex-1 gap-1 min-w-0"):
                    ui.label("Absorbed — deleted after merge") \
                        .classes("text-xs uppercase tracking-wide").style("color:#f97316")
                    merge_absorb_label = ui.label("").classes("font-semibold text-sm truncate")
                ui.icon("arrow_forward").style("color:var(--tp-base-soft); font-size:1.5rem; flex-shrink:0")
                with ui.column().classes("flex-1 gap-1 min-w-0"):
                    ui.label("Kept — receives all references") \
                        .classes("text-xs uppercase tracking-wide").style("color:var(--tp-secondary)")
                    merge_keep_label = ui.label("").classes("font-semibold text-sm truncate")

            merge_target_container = ui.element("div").classes("w-full mt-3")
            merge_swap_btn = (
                ui.button("Swap sides", icon="swap_horiz").props("flat no-caps dense")
                .style("color:#f97316").classes("mt-2")
            )
            merge_ref_label = ui.label("").classes("text-sm mt-2").style("color:var(--tp-base-soft)")
            ui.html('<p style="color:#b45309; font-size:.82rem; margin-top:6px">'
                    "⚠ The absorbed entry will be permanently deleted.</p>")
            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("Cancel", on_click=merge_dialog.close).props("flat no-caps")
                merge_confirm_btn = (
                    ui.button("Merge", icon="merge_type").props("no-caps")
                    .style("background:#f97316; color:white")
                )

        def _rebuild_target_list(others: dict, selected_id) -> None:
            merge_target_container.clear()
            with merge_target_container:
                for oid, lbl in others.items():
                    selected = oid == selected_id
                    row = ui.element("div").classes(
                        "w-full flex items-center gap-2 px-3 py-2 rounded cursor-pointer "
                        + ("outline outline-1 outline-secondary bg-secondary/5" if selected
                           else "hover:bg-slate-50 dark:hover:bg-white/5")
                    )
                    with row:
                        ui.icon("radio_button_checked" if selected else "radio_button_unchecked") \
                            .style("color:var(--tp-secondary); font-size:1.1rem; flex-shrink:0")
                        ui.label(lbl).classes("text-sm truncate")
                    row.on("click", lambda _, o=oid: _select_keep(o))

        def _select_keep(oid) -> None:
            _mctx["keep_id"] = oid
            _rebuild_target_list(_mctx["others"], oid)
            _update_preview()

        def _update_preview() -> None:
            keep_id, absorb_id = _mctx["keep_id"], _mctx["absorb_id"]
            if keep_id is None or absorb_id is None:
                merge_keep_label.set_text("")
                merge_ref_label.set_text("")
                return
            with session_factory() as s:
                preview = vocab.merge_preview(s, keep_id=keep_id, absorb_id=absorb_id)
            merge_keep_label.set_text(preview.keep_name)
            noun = "reference" if preview.reference_count == 1 else "references"
            merge_ref_label.set_text(
                f'{preview.reference_count} {noun} will be re-pointed to "{preview.keep_name}".'
                if preview.reference_count else "No existing references to re-point."
            )

        def _open_merge(row: dict) -> None:
            absorb_id = int(row["id"])
            with session_factory() as s:
                allrows = vocab.list(s)
                others = {o.id: vocab.display(o) for o in allrows if o.id != absorb_id}
                absorb_name = next((vocab.display(o) for o in allrows if o.id == absorb_id), "?")
            default_keep = next(iter(others), None)
            _mctx.update(absorb_id=absorb_id, keep_id=default_keep, others=others)
            merge_absorb_label.set_text(absorb_name)
            _rebuild_target_list(others, default_keep)
            _update_preview()
            merge_dialog.open()

        def _swap_merge() -> None:
            new_absorb, new_keep = _mctx["keep_id"], _mctx["absorb_id"]
            if new_absorb is None:
                return
            with session_factory() as s:
                allrows = vocab.list(s)
                others = {o.id: vocab.display(o) for o in allrows if o.id != new_absorb}
                absorb_name = next((vocab.display(o) for o in allrows if o.id == new_absorb), "?")
            keep_id = new_keep if new_keep in others else next(iter(others), None)
            _mctx.update(absorb_id=new_absorb, keep_id=keep_id, others=others)
            merge_absorb_label.set_text(absorb_name)
            _rebuild_target_list(others, keep_id)
            _update_preview()

        merge_swap_btn.on_click(_swap_merge)

        def _confirm_merge() -> None:
            keep_id, absorb_id = _mctx["keep_id"], _mctx["absorb_id"]
            if keep_id is None or absorb_id is None or keep_id == absorb_id:
                ui.notify("Select a target entry first.", type="warning")
                return
            try:
                with session_factory() as s:
                    with s.begin():
                        vocab.merge(s, keep_id=keep_id, absorb_id=absorb_id)
                merge_dialog.close()
                _refresh_table()
                ui.notify("Merged.", type="positive")
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
            except Exception as exc:
                ui.notify(f"Merge failed: {exc}", type="negative")

        merge_confirm_btn.on_click(_confirm_merge)

        table.on("merge",  lambda e: _open_merge(e.args))
        table.on("edit",   lambda e: _open_edit(e.args))
        table.on("delete", lambda e: _delete_row(e.args))

        # ── Add entry ─────────────────────────────────────────────────────
        ui.separator().classes("my-3")
        ui.label(spec.add_label).classes("text-sm font-semibold mb-2")
        with ui.row().classes("w-full gap-3 items-end"):
            add_name = ui.input("Name *").classes("flex-1")

            def _add_row():
                if not (add_name.value or "").strip():
                    ui.notify("Name is required.", type="warning")
                    return
                try:
                    with session_factory() as s:
                        with s.begin():
                            vocab.create(s, name=add_name.value)
                    add_name.value = ""
                    _refresh_table()
                    ui.notify("Added.", type="positive")
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            ui.button(spec.add_label, icon="add", on_click=_add_row) \
                .props("flat no-caps color=secondary")

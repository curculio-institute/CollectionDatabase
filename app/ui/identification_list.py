"""Shared identification (determination) list widget.

Two modes controlled by co_id:
  In-memory  (co_id is None):  for Digitize — operates on a local list.
             state["get_dets"]()  → list of det dicts for the save handler.
             state["clear"]()     → resets the list (called after save).
  Live       (co_id is int):   for Records — each action hits the DB immediately.
"""
from __future__ import annotations

from datetime import date

from nicegui import ui

from app.config import get_config
from app.models import Taxon
from app.services.taxa import format_scientific_name
from app.ui.taxon_search import build_taxon_search, _local_item_html
import app.services.specimens as sp_svc
import app.services.persons as persons_svc


def _person_select(label: str, value: str | None, session_factory, *, classes: str = "") -> ui.select:
    """A ui.select backed by the person table, with free-text fallback.

    Options are refreshed each time the dropdown is opened, so newly added
    people appear without a page reload.
    """
    with session_factory() as s:
        opts = persons_svc.person_options(s)
    if value and value not in opts:
        opts = {value: value, **opts}
    return (
        ui.select(opts, label=label, value=value, with_input=True, clearable=True)
        .classes(classes)
        .props("use-input input-debounce=0 new-value-mode=add-unique")
    )


def _append_year_btn(inp, *, visible_when_empty: bool = True) -> None:
    """Add a 'today' icon button to inp's append slot that fills the current year."""
    with inp.add_slot("append"):
        btn = (
            ui.button("", icon="push_pin")
            .props("flat dense round size=xs")
            .tooltip("Insert current year")
            .on_click(lambda: inp.set_value(str(date.today().year)))
        )
        if visible_when_empty:
            btn.bind_visibility_from(inp, "value", lambda v: not v)


def build_identification_list(
    session_factory,
    *,
    co_id: int | None = None,
    initial_dets: list[dict] | None = None,
    on_changed: callable | None = None,
) -> dict:
    """Render a determination list widget and return a state dict.

    co_id=None  → in-memory mode (Digitize):
                  state["get_dets"]() returns current list for the save handler.
                  state["clear"]()    resets the list.
    co_id=int   → live-DB mode (Records): each action persists immediately.
    """
    _dets: list[dict] = list(initial_dets or [])

    def _reload_from_db() -> list[dict]:
        result: list[dict] = []
        with session_factory() as s:
            for d in sp_svc.get_determination_history(s, co_id):
                t = d.taxon
                if t:
                    is_syn = t.taxonomic_status == "synonym"
                    acc_label = None
                    if is_syn and t.accepted_name_usage_id:
                        acc = s.get(Taxon, t.accepted_name_usage_id)
                        acc_label = format_scientific_name(acc) if acc else None
                    t_label = format_scientific_name(t)
                else:
                    is_syn, acc_label, t_label = False, None, "?"
                result.append({
                    "id":                       d.id,
                    "taxon_id":                 d.taxon_id,
                    "taxon_label":              t_label,
                    "is_synonym":               is_syn,
                    "accepted_label":           acc_label,
                    "identified_by":            d.identified_by,
                    "date_identified":          d.date_identified,
                    "identification_qualifier": d.identification_qualifier,
                    "identification_remarks":   d.identification_remarks,
                    "is_current":               bool(d.is_current),
                })
        return result

    list_col = ui.column().classes("w-full gap-0")

    def _refresh() -> None:
        nonlocal _dets
        if co_id is not None:
            _dets = _reload_from_db()
        list_col.clear()
        with list_col:
            if not _dets:
                ui.label("No identifications yet.") \
                    .classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
                return
            for i, d in enumerate(_dets):
                _render_row(i, d)

    def _render_row(idx: int, d: dict) -> None:
        chip_html = _local_item_html(
            d["taxon_label"], is_synonym=d["is_synonym"], accepted=d["accepted_label"],
        )
        meta_parts = [
            p for p in [
                d["identified_by"],
                d["date_identified"],
                d["identification_qualifier"],
            ] if p
        ]

        _edit_ref: list = []
        _edit_visible: list = [False]

        with ui.element("div").classes(
            "w-full border-b border-stone-100 dark:border-stone-800"
        ):
            # ── info row ──────────────────────────────────────────────────
            with ui.row().classes("items-center gap-2 w-full py-2 flex-wrap"):
                if d["is_current"]:
                    ui.icon("check_circle", size="sm") \
                        .style("color:var(--tp-secondary)") \
                        .tooltip("Current determination")
                else:
                    ui.icon("history", size="sm") \
                        .style("color:var(--tp-base-soft)") \
                        .tooltip("Retired")

                ui.html(chip_html).classes("tw-result flex-1")

                if meta_parts:
                    ui.label("  ·  ".join(meta_parts)) \
                        .classes("text-xs") \
                        .style("color:var(--tp-base-soft)")

                if not d["is_current"]:
                    def _do_set_current(_=None, det=d, ix=idx):
                        if co_id is not None:
                            try:
                                with session_factory() as s:
                                    with s.begin():
                                        sp_svc.set_determination_as_current(
                                            s, co_id, det["id"]
                                        )
                                if on_changed:
                                    on_changed()
                            except Exception as exc:
                                ui.notify(f"Failed: {exc}", type="negative")
                                return
                        else:
                            for x in _dets:
                                x["is_current"] = False
                            _dets[ix]["is_current"] = True
                        _refresh()

                    ui.button("Set current", on_click=_do_set_current) \
                        .props("flat no-caps dense size=sm color=secondary")

                def _toggle_edit(_=None, er=_edit_ref, ev=_edit_visible):
                    if not er:
                        return
                    if ev[0]:
                        er[0].style(add="display:none", remove="display:block")
                    else:
                        er[0].style(add="display:block", remove="display:none")
                    ev[0] = not ev[0]

                ui.button("", icon="edit", on_click=_toggle_edit) \
                    .props("flat dense round size=xs") \
                    .tooltip("Edit / Delete")

            # ── edit panel (hidden by default) ────────────────────────────
            edit_panel = ui.element("div") \
                .style("display:none") \
                .classes("w-full px-2 pb-3 bg-stone-50 dark:bg-stone-900 rounded-b")
            _edit_ref.append(edit_panel)

            with edit_panel:
                with ui.grid(columns=4).classes("w-full gap-2 mb-2"):
                    with ui.element("div").classes("col-span-1 flex items-center gap-1"):
                        e_idby = _person_select(
                            "identifiedBy", d["identified_by"], session_factory,
                            classes="flex-1",
                        )
                        (
                            ui.button("", icon="push_pin")
                            .props("flat dense round size=xs")
                            .tooltip("Insert default name")
                            .on_click(lambda ib=e_idby: ib.set_value(get_config().default_identified_by) if get_config().default_identified_by else None)
                            .bind_visibility_from(e_idby, "value", lambda v: not v)
                        )
                    e_dtid = ui.input(
                        "dateIdentified",
                        value=d["date_identified"] or "",
                        placeholder="YYYY-MM-DD",
                    ).classes("col-span-1")
                    _append_year_btn(e_dtid, visible_when_empty=False)
                    e_qual = ui.input(
                        "qualifier",
                        value=d["identification_qualifier"] or "",
                        placeholder="cf. / aff.",
                    ).classes("col-span-1")
                    e_rem = ui.input(
                        "remarks",
                        value=d["identification_remarks"] or "",
                    ).classes("col-span-1")

                def _do_save_edit(
                    _=None, det=d, ix=idx,
                    ib=e_idby, dt=e_dtid, ql=e_qual, rm=e_rem,
                ):
                    fields = {
                        "identified_by":            ib.value or None,
                        "date_identified":          dt.value or None,
                        "identification_qualifier": ql.value or None,
                        "identification_remarks":   rm.value or None,
                    }
                    if co_id is not None:
                        try:
                            with session_factory() as s:
                                with s.begin():
                                    sp_svc.update_determination_metadata(
                                        s, det["id"], **fields
                                    )
                            if on_changed:
                                on_changed()
                        except Exception as exc:
                            ui.notify(f"Failed: {exc}", type="negative")
                            return
                    else:
                        _dets[ix].update(fields)
                    _refresh()

                def _do_delete(_=None, det=d, ix=idx):
                    if co_id is not None:
                        try:
                            with session_factory() as s:
                                with s.begin():
                                    sp_svc.delete_determination(s, det["id"])
                            if on_changed:
                                on_changed()
                        except Exception as exc:
                            ui.notify(f"Failed: {exc}", type="negative")
                            return
                    else:
                        _dets.pop(ix)
                    _refresh()

                with ui.row().classes("items-center w-full"):
                    ui.button("Delete", icon="delete", on_click=_do_delete) \
                        .props("flat no-caps dense size=sm color=negative")
                    ui.space()
                    ui.button("Save", icon="check", on_click=_do_save_edit) \
                        .props("flat no-caps dense size=sm color=secondary")

    # ── Add new identification ────────────────────────────────────────────
    ui.separator().classes("my-3")
    ui.label("Add identification") \
        .classes("text-xs font-semibold uppercase tracking-wider text-grey-6 mb-1")

    add_taxon_state = build_taxon_search(session_factory)

    with ui.row().classes("w-full flex-wrap gap-3 items-end mt-2"):
        with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
            add_idby = _person_select("identifiedBy", None, session_factory, classes="flex-1")
            (
                ui.button("", icon="push_pin")
                .props("flat dense round size=xs")
                .tooltip("Insert default name")
                .on_click(lambda: add_idby.set_value(get_config().default_identified_by) if get_config().default_identified_by else None)
                .bind_visibility_from(add_idby, "value", lambda v: not v)
            )
        add_dtid = ui.input("dateIdentified", placeholder="YYYY-MM-DD").classes("w-36")
        _append_year_btn(add_dtid)
        add_qual = ui.input("qualifier", placeholder="cf. / aff.").classes("w-28")
        add_rem  = ui.input("remarks").classes("flex-1 min-w-40")

    def _do_add(_=None):
        new_tid = add_taxon_state["taxon_id"]
        if not new_tid:
            ui.notify("Select a taxon first.", type="warning")
            return
        if new_tid == -1:
            ui.notify("Taxon is still importing — wait a moment.", type="warning")
            return

        already_has_current = any(d["is_current"] for d in _dets)
        is_new_current = not already_has_current

        if co_id is not None:
            try:
                with session_factory() as s:
                    with s.begin():
                        sp_svc.create_determination(
                            s,
                            collection_object_id=co_id,
                            taxon_id=new_tid,
                            identified_by=add_idby.value or None,
                            date_identified=add_dtid.value or None,
                            identification_qualifier=add_qual.value or None,
                            identification_remarks=add_rem.value or None,
                            is_current=1 if is_new_current else 0,
                        )
                if on_changed:
                    on_changed()
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")
                return
        else:
            with session_factory() as s:
                t = s.get(Taxon, new_tid)
                if t:
                    is_syn = t.taxonomic_status == "synonym"
                    acc_label = None
                    if is_syn and t.accepted_name_usage_id:
                        acc = s.get(Taxon, t.accepted_name_usage_id)
                        acc_label = format_scientific_name(acc) if acc else None
                    t_label = format_scientific_name(t)
                else:
                    is_syn, acc_label, t_label = False, None, f"taxon #{new_tid}"

            _dets.append({
                "id":                       None,
                "taxon_id":                 new_tid,
                "taxon_label":              t_label,
                "is_synonym":               is_syn,
                "accepted_label":           acc_label,
                "identified_by":            add_idby.value or None,
                "date_identified":          add_dtid.value or None,
                "identification_qualifier": add_qual.value or None,
                "identification_remarks":   add_rem.value or None,
                "is_current":               is_new_current,
            })

        add_taxon_state["clear"]()
        add_idby.value = None
        add_dtid.value = ""
        add_qual.value = ""
        add_rem.value  = ""
        _refresh()

    with ui.row().classes("w-full items-center mt-2"):
        ui.space()
        ui.button("Add identification", icon="add", on_click=_do_add) \
            .props("flat no-caps color=secondary")

    # Initial render
    _refresh()

    # ── State interface ───────────────────────────────────────────────────────

    def _state_get_dets() -> list[dict]:
        return list(_dets)

    def _state_clear() -> None:
        nonlocal _dets
        _dets = []
        add_taxon_state["clear"]()
        add_idby.value = None
        add_dtid.value = ""
        add_qual.value = ""
        add_rem.value  = ""
        _refresh()

    def _state_refresh_person_opts() -> None:
        with session_factory() as s:
            new_opts = persons_svc.person_options(s)
        cur = add_idby.value
        if cur and cur not in new_opts:
            new_opts = {cur: cur, **new_opts}
        add_idby.options = new_opts

    return {
        "get_dets": _state_get_dets,
        "clear": _state_clear,
        "refresh_person_opts": _state_refresh_person_opts,
    }

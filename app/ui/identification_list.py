"""Shared identification (determination) list widget.

Three modes:
  In-memory  (co_id is None):        for Digitize — operates on a local list.
             state["get_dets"]()  → list of det dicts for the save handler.
             state["clear"]()     → resets the list (called after save).
  Deferred   (co_id, deferred=True): for Records — rows are loaded from the DB once and
             every edit/add/delete/set-current is STAGED in memory. Nothing is written
             until the card's "Save changes" calls state["commit"](session). The Records
             dirty-poll asks state["has_changes"]().
  Live       (co_id, deferred=False): each action hits the DB immediately. No caller uses
             this any more; kept because Import & Assign and Mounting may want it.

Why deferred is the default in Records: the card has one "Save changes" button, so an
identification silently committing on click contradicts it — a Delete could not be undone
by walking away, and there was no confirmation either. Staging makes the whole card atomic
and puts identification edits under the unsaved-changes banner (#54).

Auto-current logic:
  After every Add, the det with the most recent dateIdentified is automatically
  made current. If no dates are present, the most-recently-added det wins.
  The auto-selected row shows a pulsing check_circle icon. The pulse clears
  as soon as the user manually picks a different current.
"""
from __future__ import annotations

from nicegui import ui

from app.ui.type_status_field import build_type_status_field

from app.models import Taxon
import app.services.person_defaults as pd_svc
# Controlled vocabulary — single source of truth (app/vocab.py).
from app.vocab import (SEX_OPTIONS as _SEX_OPTIONS, SEX_SYMBOLS as _SEX_SYMBOL,
                       IDENTIFICATION_QUALIFIER_OPTIONS as _QUAL_OPTIONS)

_TYPE_STATUS_OPTIONS = [
    "Holotype", "Paratype", "Lectotype", "Paralectotype", "Neotype", "Syntype",
]
from app.services.taxa import (
    compose_scientific_name,
    format_scientific_name,
    render_identification,
)
from app.ui.taxon_search import build_taxon_search, _local_item_html
from app.ui.date_input import AUTO_CHANGED_CSS, attach_date_validation, append_year_pin
from app.ui.person_field import build_person_field
from app.ui.choice_field import build_choice_field
import app.services.specimens as sp_svc


def build_identification_list(
    session_factory,
    *,
    co_id: int | None = None,
    initial_dets: list[dict] | None = None,
    on_changed: callable | None = None,
    deferred: bool = False,
) -> dict:
    """Render a determination list widget and return a state dict.

    co_id=None                 → in-memory mode (Digitize).
    co_id=int, deferred=True   → staged mode (Records): edits live in memory until
                                 state["commit"](session) runs inside the card's Save.
    co_id=int, deferred=False  → live mode: each action persists immediately.
    """
    ui.add_head_html(AUTO_CHANGED_CSS)

    # Only live mode writes on click. Deferred and in-memory modes both mutate `_dets`;
    # the difference is that deferred rows carry a DB id and are reconciled against it
    # in commit().
    _persist = co_id is not None and not deferred

    def _default_idby() -> str | None:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[0]

    _dets: list[dict] = list(initial_dets or [])
    # Rows the user removed from a staged list; deleted from the DB on commit.
    _deleted_ids: list[int] = []

    # taxon_id of the most recently auto-set current determination.
    # None = no auto-selection active (user is in manual control).
    _auto_tid: list[int | None] = [None]

    def _reload_from_db() -> list[dict]:
        result: list[dict] = []
        with session_factory() as s:
            for d in sp_svc.get_determination_history(s, co_id):
                t = d.taxon
                # The determination name is FROZEN at save time (verbatim); fall
                # back to the live composed name only for legacy rows with none.
                verbatim = d.verbatim_identification or (
                    compose_scientific_name(s, t) if t else ""
                )
                t_label = render_identification(verbatim, d.identification_qualifier)
                if t:
                    is_syn = t.accepted_name_usage_id is not None
                    acc_label = acc_name = acc_rank = acc_auth = None
                    if is_syn and t.accepted_name_usage_id:
                        acc = s.get(Taxon, t.accepted_name_usage_id)
                        if acc:
                            acc_label = format_scientific_name(acc)
                            acc_name, acc_rank = acc.scientific_name, acc.taxon_rank
                            acc_auth = acc.scientific_name_authorship
                else:
                    is_syn = False
                    acc_label = acc_name = acc_rank = acc_auth = None
                result.append({
                    "id":                       d.id,
                    "taxon_id":                 d.taxon_id,
                    "taxon_label":              t_label,
                    "verbatim_identification":  verbatim,
                    "is_synonym":               is_syn,
                    "accepted_label":           acc_label,
                    "taxon_rank":               t.taxon_rank if t else None,
                    "accepted_name":            acc_name,
                    "accepted_rank":            acc_rank,
                    "accepted_authorship":      acc_auth,
                    "sex":                      d.sex,
                    "type_status":              d.type_status,
                    "identified_by":            d.identified_by_person.full_name if d.identified_by_person else None,
                    "identified_by_id":         d.identified_by_id,
                    "date_identified":          d.date_identified,
                    "identification_qualifier": d.identification_qualifier,
                    "identification_remarks":   d.identification_remarks,
                    "is_current":               bool(d.is_current),
                })
        return result

    # Deferred mode loads the rows once; every later change stays in memory until commit().
    # (Live mode reloads on every _refresh; in-memory mode starts from `initial_dets`.)
    if co_id is not None and deferred:
        _dets = _reload_from_db()
        for _d in _dets:
            _d["_orig_taxon_id"] = _d["taxon_id"]

    def _taxon_display(taxon_id: int) -> dict:
        """Read-only lookup for a staged taxon pick: name, synonym marker, frozen preview.

        Opens a session but writes nothing. The verbatim name shown here is what commit()
        will freeze — computed the same way (compose_scientific_name), so the row never
        previews a name different from the one it will store.
        """
        with session_factory() as s:
            t = s.get(Taxon, taxon_id)
            if t is None:
                return {}
            verbatim = compose_scientific_name(s, t)
            is_syn = t.accepted_name_usage_id is not None
            acc_label = acc_name = acc_rank = acc_auth = None
            if is_syn and t.accepted_name_usage_id:
                acc = s.get(Taxon, t.accepted_name_usage_id)
                if acc:
                    acc_label = format_scientific_name(acc)
                    acc_name, acc_rank = acc.scientific_name, acc.taxon_rank
                    acc_auth = acc.scientific_name_authorship
            t_rank = t.taxon_rank
        return {"taxon_id": taxon_id, "verbatim_identification": verbatim,
                "taxon_label": verbatim, "is_synonym": is_syn,
                "accepted_label": acc_label, "taxon_rank": t_rank,
                "accepted_name": acc_name, "accepted_rank": acc_rank,
                "accepted_authorship": acc_auth}

    _DIRTY_KEYS = ("id", "taxon_id", "sex", "type_status", "identified_by",
                   "date_identified", "identification_qualifier",
                   "identification_remarks", "is_current")

    def _snapshot() -> list[tuple]:
        """Comparable view of the staged list, for the unsaved-changes poll."""
        return [tuple(d.get(k) for k in _DIRTY_KEYS) for d in _dets]

    _baseline: list[list[tuple]] = [_snapshot()]

    # ── Auto-current helpers ──────────────────────────────────────────────────

    def _pick_current_idx() -> int:
        """Return the index of the det that should be current.

        Most recent dateIdentified wins. Ties and no-date cases resolved by
        list position: index 0 (live mode, newest-first) or index -1
        (in-memory mode, most-recently-appended).
        """
        dated = [
            (i, d["date_identified"])
            for i, d in enumerate(_dets)
            if d.get("date_identified")
        ]
        if dated:
            return max(dated, key=lambda x: x[1])[0]
        return 0 if co_id is not None else len(_dets) - 1


    def _auto_assign_and_mark() -> None:
        """Make the most-recent det current and light up its icon.

        In live mode, persists to DB and reloads. In memory mode, updates
        the _dets list in place.
        """
        if not _dets:
            _auto_tid[0] = None
            return

        target_idx = _pick_current_idx()
        target = _dets[target_idx]

        if not target["is_current"]:
            if _persist:
                with session_factory() as s:
                    with s.begin():
                        sp_svc.set_determination_as_current(s, co_id, target["id"])
                _dets[:] = _reload_from_db()
            else:
                for d in _dets:
                    d["is_current"] = False
                _dets[target_idx]["is_current"] = True

        # After any reload the target index may have shifted; find current by flag.
        current = next((d for d in _dets if d["is_current"]), None)
        _auto_tid[0] = current["taxon_id"] if current else None

    # ── Render ────────────────────────────────────────────────────────────────

    list_col = ui.column().classes("w-full gap-0")

    def _refresh() -> None:
        nonlocal _dets
        if _persist:
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
        # Italics by RANK, and never on the authorship — taxa.scientific_name_html owns the
        # convention. `taxon_label` already carries the open-nomenclature qualifier, which the
        # renderer leaves roman ("Otiorhynchus cf. forticollis").
        chip_html = _local_item_html(
            d["taxon_label"],
            is_synonym=d["is_synonym"],
            accepted=d.get("accepted_name") or d.get("accepted_label"),
            taxon_rank=d.get("taxon_rank"),
            accepted_rank=d.get("accepted_rank"),
            accepted_authorship=d.get("accepted_authorship"),
        )
        sex_sym = _SEX_SYMBOL.get((d.get("sex") or "").lower())
        # The qualifier is rendered inline in the determination name (after the
        # genus-group), so it is intentionally not repeated in the meta line.
        meta_parts = [
            p for p in [
                d.get("type_status"),
                sex_sym,
                f"det. {d['identified_by']}" if d["identified_by"] else None,
                d["date_identified"],
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
                    is_auto = (
                        _auto_tid[0] is not None
                        and d["taxon_id"] == _auto_tid[0]
                    )
                    (
                        ui.icon("check_circle", size="sm")
                        .style("color:var(--tp-secondary)")
                        .tooltip("Current determination")
                    )
                    if is_auto:
                        (
                            ui.icon("auto_fix_high", size="sm")
                            .style("color:var(--tp-secondary)")
                            .classes("auto-changed")
                            .tooltip(
                                "Automatically selected — most recent dateIdentified. "
                                "Click 'Set current' on another row to override."
                            )
                        )
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
                        _auto_tid[0] = None  # user takes manual control
                        if _persist:
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
                # Correcting a mis-picked taxon (#54). This is a *correction*, not a
                # re-identification: it re-points taxon_id and re-freezes the verbatim name
                # on the same determination, leaving identifiedBy / dateIdentified alone. A
                # genuine re-identification is a new determination — use "Add identification"
                # below, which keeps the old one in the history.
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    e_taxon = build_taxon_search(
                        session_factory,
                        initial_taxon_id=d["taxon_id"],
                        initial_label=d["taxon_label"],
                        placeholder="Correct the taxon…",
                    )
                with ui.grid(columns=3).classes("w-full gap-2 mb-2"):
                    with ui.element("div").classes("col-span-1 flex items-center gap-1"):
                        e_idby_state = build_person_field(
                            session_factory, "identifiedBy",
                            default_fn=_default_idby,
                            initial_value=d["identified_by"],
                        )
                    e_dtid = ui.input(
                        "dateIdentified",
                        value=d["date_identified"] or "",
                        placeholder="YYYY-MM-DD",
                    ).classes("col-span-1")
                    append_year_pin(e_dtid, visible_when_empty=False)
                    attach_date_validation(e_dtid, no_future=True, allow_interval=True)
                    e_sex = ui.select(
                        _SEX_OPTIONS, label="sex",
                        value=d.get("sex") or "",
                    ).classes("col-span-1 w-28")
                with ui.grid(columns=3).classes("w-full gap-2 mb-2"):
                    e_type = build_type_status_field(
                        initial_value=d.get("type_status") or None,
                        classes="col-span-1",
                    )
                    # Closed open-nomenclature set (DB CHECK), via the person-field custom
                    # dropdown: opens on focus with cf. (first) highlighted, so one Enter adds
                    # it. Cleared = definite ID. get_value() is the qualifier string.
                    e_qual = build_choice_field(
                        _QUAL_OPTIONS, "qualifier",
                        initial_value=d["identification_qualifier"] or None,
                        classes="col-span-1")

                    e_rem = ui.input(
                        "remarks",
                        value=d["identification_remarks"] or "",
                    ).classes("col-span-1")

                def _do_save_edit(
                    _=None, det=d, ix=idx,
                    idby=e_idby_state, sx=e_sex, ts=e_type, dt=e_dtid, ql=e_qual, rm=e_rem,
                    tx=e_taxon,
                ):
                    new_tid = tx["taxon_id"] or det["taxon_id"]
                    if new_tid == -1:
                        ui.notify("Taxon is still importing — wait a moment.", type="warning")
                        return
                    if _persist:
                        try:
                            with session_factory() as s:
                                with s.begin():
                                    idby_id = idby["commit"](s)
                                    sp_svc.update_determination_metadata(
                                        s, det["id"],
                                        sex=sx.value or None,
                                        type_status=ts["get_value"]() or None,
                                        identified_by_id=idby_id,
                                        date_identified=dt.value or None,
                                        identification_qualifier=ql["get_value"]() or None,
                                        identification_remarks=rm.value or None,
                                    )
                                    if new_tid != det["taxon_id"]:
                                        sp_svc.update_determination_taxon(
                                            s, det["id"], taxon_id=new_tid)
                            if on_changed:
                                on_changed()
                        except Exception as exc:
                            ui.notify(f"Failed: {exc}", type="negative")
                            return
                    else:
                        # Staged: nothing is written — not even the person. A new determiner
                        # typed here used to be created immediately, so abandoning the
                        # specimen left a stray name in the People list (#60). The NAME is
                        # held; commit()/the Digitize save resolves it to an id.
                        _dets[ix].update({
                            "sex":              sx.value or None,
                            "type_status":      ts["get_value"]() or None,
                            "identified_by":    idby["get_value"](),
                            "identified_by_id": None,
                            "date_identified":          dt.value or None,
                            "identification_qualifier": ql["get_value"]() or None,
                            "identification_remarks":   rm.value or None,
                        })
                        if new_tid != _dets[ix]["taxon_id"]:
                            _dets[ix].update(_taxon_display(new_tid))
                        # Re-render the name: the qualifier is shown inline. (Live
                        # mode re-renders automatically via _reload_from_db.)
                        _dets[ix]["taxon_label"] = render_identification(
                            _dets[ix].get("verbatim_identification") or "", ql["get_value"]()
                        )
                    _refresh()

                def _do_delete(_=None, det=d, ix=idx):
                    if _persist:
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
                        # Staged rows already in the DB are deleted on commit; a row added
                        # in this session (no id) simply disappears.
                        if det.get("id") is not None:
                            _deleted_ids.append(det["id"])
                        _dets.pop(ix)
                        if _auto_tid[0] == det.get("taxon_id"):
                            _auto_tid[0] = None
                        if on_changed:
                            on_changed()
                    _refresh()

                with ui.row().classes("items-center w-full"):
                    ui.button("Delete", icon="delete", on_click=_do_delete) \
                        .props("flat no-caps dense size=sm color=negative")
                    ui.space()
                    ui.button("Save", icon="check", on_click=_do_save_edit) \
                        .props("flat no-caps dense size=sm color=secondary")

    # ── Add new identification ────────────────────────────────────────────────
    ui.separator().classes("my-3")
    ui.label("Add identification").classes("section-label mb-1")

    add_taxon_state = build_taxon_search(session_factory)

    with ui.row().classes("w-full flex-wrap gap-3 items-end mt-2"):
        with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
            add_idby_state = build_person_field(
                session_factory, "identifiedBy",
                default_fn=_default_idby,
            )
        add_dtid = ui.input("dateIdentified", placeholder="YYYY-MM-DD").classes("w-36")
        append_year_pin(add_dtid)
        attach_date_validation(add_dtid, no_future=True, allow_interval=True)
        add_sex  = ui.select(_SEX_OPTIONS, label="sex").classes("w-28")
        add_type = build_type_status_field(classes="w-36")
        add_qual = build_choice_field(_QUAL_OPTIONS, "qualifier", classes="w-28")

        add_rem  = ui.input("remarks").classes("flex-1 min-w-40")

    def _do_add(_=None):
        new_tid = add_taxon_state["taxon_id"]
        if not new_tid:
            ui.notify("Select a taxon first.", type="warning")
            return
        if new_tid == -1:
            ui.notify("Taxon is still importing — wait a moment.", type="warning")
            return

        if _persist:
            # Live-DB mode: always create not-current, then auto-assign.
            try:
                with session_factory() as s:
                    with s.begin():
                        idby_id = add_idby_state["commit"](s)
                        # Freeze the determination name at save time.
                        new_t = s.get(Taxon, new_tid)
                        verbatim = compose_scientific_name(s, new_t) if new_t else None
                        sp_svc.create_determination(
                            s,
                            collection_object_id=co_id,
                            taxon_id=new_tid,
                            sex=add_sex.value or None,
                            type_status=add_type["get_value"]() or None,
                            identified_by_id=idby_id,
                            date_identified=add_dtid.value or None,
                            identification_qualifier=add_qual["get_value"]() or None,
                            identification_remarks=add_rem.value or None,
                            verbatim_identification=verbatim,
                            is_current=0,
                        )
                _dets[:] = _reload_from_db()
                _auto_assign_and_mark()
                if on_changed:
                    on_changed()
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative")
                return
        else:
            # In-memory mode: append, then auto-assign.
            with session_factory() as s:
                t = s.get(Taxon, new_tid)
                if t:
                    is_syn = t.accepted_name_usage_id is not None
                    acc_label = acc_name = acc_rank = acc_auth = None
                    if is_syn and t.accepted_name_usage_id:
                        acc = s.get(Taxon, t.accepted_name_usage_id)
                        if acc:
                            acc_label = format_scientific_name(acc)
                            acc_name, acc_rank = acc.scientific_name, acc.taxon_rank
                            acc_auth = acc.scientific_name_authorship
                    verbatim = compose_scientific_name(s, t)  # frozen at add time
                    t_rank = t.taxon_rank
                else:
                    is_syn, verbatim = False, f"taxon #{new_tid}"
                    acc_label = acc_name = acc_rank = acc_auth = t_rank = None
            t_label = render_identification(verbatim, add_qual["get_value"]())

            # No person is created here: a determiner typed for a specimen that is never
            # saved would linger in the People list (#60). The name is carried; the save
            # transaction resolves it.
            _dets.append({
                "id":                       None,
                "_orig_taxon_id":           None,
                "taxon_id":                 new_tid,
                "taxon_label":              t_label,
                "verbatim_identification":  verbatim,
                "is_synonym":               is_syn,
                "accepted_label":           acc_label,
                "taxon_rank":               t_rank,
                "accepted_name":            acc_name,
                "accepted_rank":            acc_rank,
                "accepted_authorship":      acc_auth,
                "sex":                      add_sex.value or None,
                "type_status":              add_type["get_value"]() or None,
                "identified_by":            add_idby_state["get_value"](),
                "identified_by_id":         None,
                "date_identified":          add_dtid.value or None,
                "identification_qualifier": add_qual["get_value"]() or None,
                "identification_remarks":   add_rem.value or None,
                "is_current":               False,
            })
            _auto_assign_and_mark()
            if on_changed:
                on_changed()

        add_taxon_state["clear"]()
        add_idby_state["set_value"](None)
        add_dtid.value = ""
        add_sex.value  = ""
        add_type["set_value"](None)
        add_qual["set_value"](None)
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
        _auto_tid[0] = None
        add_taxon_state["clear"]()
        add_idby_state["set_value"](None)
        add_dtid.value = ""
        add_sex.value  = ""
        add_type["set_value"](None)
        add_qual["set_value"](None)
        add_rem.value  = ""
        _refresh()

    def _state_refresh_person_opts() -> None:
        add_idby_state["refresh"]()

    def _state_has_content() -> bool:
        """True if there are determinations, or the add row holds anything yet —
        including a staged taxon or a push-pin-filled identifiedBy / dateIdentified
        (which are set programmatically, not typed, so value inspection is the only
        reliable signal)."""
        return bool(_dets) or bool(add_taxon_state["taxon_id"]) \
            or bool(add_idby_state["get_value"]()) or bool(add_dtid.value) \
            or bool(add_sex.value) or bool(add_type["get_value"]()) \
            or bool(add_qual["get_value"]()) or bool(add_rem.value)

    def _state_has_changes() -> bool:
        """Staged mode: has anything been added, edited, deleted or re-flagged?"""
        return bool(_deleted_ids) or _snapshot() != _baseline[0]

    def _state_commit(session) -> None:
        """Apply the staged determinations to the DB, inside the card's Save transaction.

        Reconciles against the rows loaded at build time: deletions first (so a delete +
        re-add of the same taxon cannot collide), then updates, then creations, and finally
        exactly one `is_current`. Person names are resolved here — not when they were typed
        (#60) — so abandoning the card leaves no stray person.
        """
        if co_id is None:
            raise RuntimeError("commit() requires co_id (staged Records mode)")
        import app.services.persons as persons_svc

        def _person_id(name: str | None) -> int | None:
            name = (name or "").strip()
            if not name:
                return None
            return persons_svc.get_or_create_person(session, full_name=name).id

        for det_id in _deleted_ids:
            sp_svc.delete_determination(session, det_id)
        _deleted_ids.clear()

        for d in _dets:
            idby_id = d.get("identified_by_id") or _person_id(d.get("identified_by"))
            if d.get("id") is None:
                t = session.get(Taxon, d["taxon_id"])
                created = sp_svc.create_determination(
                    session,
                    collection_object_id=co_id,
                    taxon_id=d["taxon_id"],
                    sex=d.get("sex"),
                    type_status=d.get("type_status"),
                    identified_by_id=idby_id,
                    date_identified=d.get("date_identified"),
                    identification_qualifier=d.get("identification_qualifier"),
                    identification_remarks=d.get("identification_remarks"),
                    # Frozen at save time, from the taxon actually stored.
                    verbatim_identification=compose_scientific_name(session, t) if t else None,
                    is_current=0,
                )
                d["id"] = created.id
            else:
                sp_svc.update_determination_metadata(
                    session, d["id"],
                    sex=d.get("sex"),
                    type_status=d.get("type_status"),
                    identified_by_id=idby_id,
                    date_identified=d.get("date_identified"),
                    identification_qualifier=d.get("identification_qualifier"),
                    identification_remarks=d.get("identification_remarks"),
                )
                if d.get("taxon_id") != d.get("_orig_taxon_id"):
                    # Re-points taxon_id AND re-freezes the verbatim name (#54).
                    sp_svc.update_determination_taxon(session, d["id"], taxon_id=d["taxon_id"])
            d["_orig_taxon_id"] = d["taxon_id"]

        current = next((d for d in _dets if d["is_current"]), None)
        if current is not None:
            sp_svc.set_determination_as_current(session, co_id, current["id"])

        _baseline[0] = _snapshot()

    return {
        "get_dets": _state_get_dets,
        "clear": _state_clear,
        "has_content": _state_has_content,
        "has_changes": _state_has_changes,
        "commit": _state_commit,
        "refresh_person_opts": _state_refresh_person_opts,
    }

"""Records tab — view and edit existing specimens and collecting events."""
from __future__ import annotations

from nicegui import ui

from app.config import get_config
import app.services.person_defaults as pd_svc
from app.models import CollectionObject, CollectingEvent, TaxonDetermination, Taxon
import app.services.specimens as sp_svc
import app.services.events as ev_svc
import app.services.identifiers as id_svc
import app.services.biological as bio_svc
from app.services.taxa import (
    compose_scientific_name,
    format_scientific_name,
    render_identification,
)
from app.ui.taxon_search import build_taxon_search, _local_item_html
from app.ui.identification_list import build_identification_list
from app.ui.collecting_event_form import build_collecting_event_form
from app.ui.specimen_form import build_specimen_form
from app.ui.event_reuse import build_event_share_banner
from app.ui.media_panel import build_media_button

_FLOAT_ATTRS = frozenset({
    "decimal_latitude", "decimal_longitude",
    "coordinate_uncertainty_in_meters", "coordinate_precision",
    "minimum_elevation_in_meters", "maximum_elevation_in_meters",
})


def _media_btn(session_factory, *, target_kind, target_id, tooltip="Media"):
    """A compact media icon+popup button (bound mode) for one saved record. The button
    badge indicates how many files are attached (progressive disclosure — the gallery is
    behind the click)."""
    return build_media_button(
        session_factory, target_kind=target_kind,
        target_id_getter=lambda: target_id, tooltip=tooltip,
    )["button"]


def build_records_tab(session_factory, *, on_saved: callable | None = None) -> None:
    """Render the Records tab content into the current NiceGUI container.

    on_saved: called after any successful save so other tabs can refresh.
    """

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    def _default_recby() -> str | None:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[1]

    # ── Search card ─────────────────────────────────────────────────────────
    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-3 mb-3"):
            ui.label("Find record").classes("section-label")
            ui.space()
            mode_spec_btn = ui.button("Specimens", icon="bug_report") \
                .props("no-caps color=secondary dense")
            mode_ev_btn = ui.button("Events", icon="place") \
                .props("no-caps flat dense")

        def _specimen_opts() -> dict:
            rows = _with_session(lambda s: sp_svc.recent_specimens(s, limit=1000))
            return {
                r.collection_object_id: (
                    f"#{r.collection_object_id}  "
                    f"{id_svc.format_catalog_display(r.collection_code, r.catalog_number)}  "
                    f"{r.scientific_name or '—'}"
                )
                for r in rows
            }

        def _event_opts() -> dict:
            rows = _with_session(lambda s: ev_svc.search_collecting_events(s, "", limit=500))
            return {r.id: r.summary for r in rows}

        spec_select = (
            ui.select(
                options=_specimen_opts(),
                with_input=True,
                clearable=True,
                label="Search specimens…",
            )
            .classes("w-full")
        )
        ui.timer(2.0, lambda: spec_select.set_options(_specimen_opts()))
        ev_select = (
            ui.select(
                options=_event_opts(),
                with_input=True,
                clearable=True,
                label="Search events…",
            )
            .classes("w-full")
            .style("display:none")
        )
        ui.timer(2.0, lambda: ev_select.set_options(_event_opts()))

    # ── Detail area ─────────────────────────────────────────────────────────
    detail = ui.column().classes("w-full gap-4")

    # ── Mode toggle ──────────────────────────────────────────────────────────
    def _set_mode_specimen():
        mode_spec_btn.props("color=secondary")
        mode_ev_btn.props("flat")
        spec_select.style(remove="display:none")
        ev_select.style(add="display:none")
        detail.clear()
        spec_select.value = None

    def _set_mode_event():
        mode_spec_btn.props("flat")
        mode_ev_btn.props("color=secondary")
        spec_select.style(add="display:none")
        ev_select.style(remove="display:none")
        detail.clear()
        ev_select.value = None

    mode_spec_btn.on_click(_set_mode_specimen)
    mode_ev_btn.on_click(_set_mode_event)

    # ── Specimen loader ──────────────────────────────────────────────────────
    def _load_specimen(co_id: int) -> None:
        detail.clear()

        with session_factory() as s:
            co = s.get(CollectionObject, co_id)
            if co is None:
                with detail:
                    ui.label("Specimen not found.").classes("text-sm text-negative")
                return

            ev = co.collecting_event
            ev_id = co.collecting_event_id
            ev_n  = ev_svc.count_co_at_event(s, ev_id) if ev_id else 0

            assocs      = bio_svc.get_associations_for_specimen(s, co_id)

            co_snap = {
                "catalog_number":    co.catalog_number,
                "collection_code": co.collection_code,
                "individual_count":  co.individual_count,
                "preparations":      co.preparations,
                "life_stage":        co.life_stage,
                "disposition":       co.disposition,
                "basis_of_record":   co.basis_of_record,
                "occurrence_remarks":co.occurrence_remarks,
            }

            # Snapshot all determinations as plain dicts while session is open (avoids DetachedInstanceError).
            det_snaps: list[dict] = []
            for d in sp_svc.get_determination_history(s, co_id):
                t = d.taxon
                verbatim = d.verbatim_identification or (
                    compose_scientific_name(s, t) if t else ""
                )
                t_label = render_identification(verbatim, d.identification_qualifier)
                if t:
                    is_syn = t.accepted_name_usage_id is not None
                    acc_label = None
                    if is_syn and t.accepted_name_usage_id:
                        acc = s.get(Taxon, t.accepted_name_usage_id)
                        acc_label = format_scientific_name(acc) if acc else None
                else:
                    is_syn, acc_label = False, None
                det_snaps.append({
                    "id":                       d.id,
                    "taxon_label":              t_label,
                    "verbatim_identification":  verbatim,
                    "is_synonym":               is_syn,
                    "accepted_label":           acc_label,
                    "sex":                      d.sex,
                    "type_status":              d.type_status,
                    "identified_by":            d.identified_by_person.full_name if d.identified_by_person else None,
                    "identified_by_id":         d.identified_by_id,
                    "date_identified":          d.date_identified,
                    "identification_qualifier": d.identification_qualifier,
                    "identification_remarks":   d.identification_remarks,
                    "is_current":               bool(d.is_current),
                })

            ev_snap = {
                "country":                          ev.country              if ev else None,
                "country_code":                     ev.country_code         if ev else None,
                "state_province":                   ev.state_province       if ev else None,
                "county":                           ev.county               if ev else None,
                "municipality":                     ev.municipality         if ev else None,
                "island":                           ev.island               if ev else None,
                "locality":                         ev.locality             if ev else None,
                "verbatim_locality":                ev.verbatim_locality    if ev else None,
                "event_date":                       ev.event_date           if ev else None,
                "verbatim_event_date":              ev.verbatim_event_date  if ev else None,
                "recorded_by":                      ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None,
                "habitat":                          ev.habitat              if ev else None,
                "decimal_latitude":                 ev.decimal_latitude     if ev else None,
                "decimal_longitude":                ev.decimal_longitude    if ev else None,
                "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters if ev else None,
                "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters      if ev else None,
                "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters      if ev else None,
                "sampling_protocol":                ev.sampling_protocol    if ev else None,
                "field_number":                     ev.field_number         if ev else None,
                "verbatim_label":                   ev.verbatim_label       if ev else None,
            }

        # Build form inside the detail column
        with detail:
            _build_specimen_form(
                co_id, ev_id, ev_n,
                co_snap, det_snaps, ev_snap, assocs,
            )

    # ── Event loader ─────────────────────────────────────────────────────────
    def _load_event(ev_id: int) -> None:
        detail.clear()

        with session_factory() as s:
            ev = s.get(CollectingEvent, ev_id)
            if ev is None:
                with detail:
                    ui.label("Event not found.").classes("text-sm text-negative")
                return
            n   = ev_svc.count_co_at_event(s, ev_id)
            cos = [
                (c.id, c.collection_code, c.catalog_number)
                for c in ev.collection_objects[:30]
            ]
            ev_snap = {
                "country":                          ev.country,
                "country_code":                     ev.country_code,
                "state_province":                   ev.state_province,
                "county":                           ev.county,
                "municipality":                     ev.municipality,
                "island":                           ev.island,
                "locality":                         ev.locality,
                "verbatim_locality":                ev.verbatim_locality,
                "event_date":                       ev.event_date,
                "verbatim_event_date":              ev.verbatim_event_date,
                "recorded_by":                      ev.recorded_by_person.full_name if ev.recorded_by_person else None,
                "habitat":                          ev.habitat,
                "decimal_latitude":                 ev.decimal_latitude,
                "decimal_longitude":                ev.decimal_longitude,
                "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters,
                "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters,
                "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters,
                "sampling_protocol":                ev.sampling_protocol,
                "field_number":                     ev.field_number,
                "verbatim_label":                   ev.verbatim_label,
            }

        with detail:
            _build_event_form(ev_id, n, cos, ev_snap)

    spec_select.on_value_change(
        lambda e: _load_specimen(e.value) if e.value else detail.clear()
    )
    ev_select.on_value_change(
        lambda e: _load_event(e.value) if e.value else detail.clear()
    )

    # Programmatic open (used by the Print queue "open in Records" link, #37):
    # switch to specimen mode and select the specimen, which loads its detail
    # (event + determinations) for substantial edits — the record is master.
    def _open_specimen(co_id: int) -> None:
        _set_mode_specimen()
        spec_select.value = co_id

    # ── Specimen form ─────────────────────────────────────────────────────────
    def _build_specimen_form(
        co_id, ev_id, ev_n, co_snap, det_snaps, ev_snap, assocs
    ):

        # ── Specimen card ────────────────────────────────────────────────
        # Shared specimen-field block (see app/ui/specimen_form.py), edit policy:
        # catalog_number is immutable (shown read-only in the header); collectionCode
        # is editable (gifting). Remaining fields are seeded from the DB snapshot.
        # Widgets are unpacked into locals so the save path references them unchanged.
        spec = build_specimen_form(
            session_factory,
            identifier_policy="edit",
            initial=co_snap,
            identity_label=f"#{co_id}  {co_snap['catalog_number']}",
        )
        count_in     = spec["count_in"]
        preps_in     = spec["preps_in"]
        stage_sel    = spec["stage_sel"]
        disp_sel     = spec["disp_sel"]
        basis_sel    = spec["basis_sel"]
        rem_in       = spec["rem_in"]
        coll_code_in = spec["coll_code_disp"]

        # Specimen media (icon + popup; badge shows attachment count)
        with ui.row().classes("items-center gap-2 px-1"):
            ui.label("Specimen media").classes("text-sm").style("color:var(--tp-base-soft)")
            _media_btn(session_factory, target_kind="collection_object",
                       target_id=co_id, tooltip="Specimen media")

        # ── Identifications card ──────────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            ui.label("Identifications").classes("section-label mb-2")
            ui.separator().classes("mb-2")
            build_identification_list(
                session_factory,
                co_id=co_id,
                initial_dets=det_snaps,
                on_changed=lambda: on_saved() if on_saved else None,
            )

        # ── Collecting Event card ─────────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-3 mb-1 flex-wrap"):
                ui.label("Collecting Event").classes("section-label")
                if ev_id:
                    ev_label_str = f"Event #{ev_id}"
                    if ev_n > 1:
                        ev_label_str += f" — shared by {ev_n} specimens"
                    ui.label(ev_label_str).classes("text-sm").style(
                        "color:var(--tp-warning, #f59e0b)" if ev_n > 1
                        else "color:var(--tp-base-soft)"
                    )
                    ui.space()
                    _media_btn(session_factory, target_kind="collecting_event",
                               target_id=ev_id, tooltip="Event media")
            ui.separator().classes("mb-3")

            # A shared event (n>1) opens read-only ("view"); the event is only
            # written on Save once the user deliberately unlocks it ("Edit all").
            # A single-specimen event is editable directly — no one else is affected.
            _ev_editable = [ev_n <= 1]

            if ev_n > 1 and ev_id:
                def _detach():
                    try:
                        with session_factory() as s:
                            with s.begin():
                                new_id = ev_svc.copy_and_relink_event(s, co_id)
                        ui.notify(
                            f"Detached — new Event #{new_id} created for this specimen.",
                            type="positive",
                        )
                        _load_specimen(co_id)
                    except Exception as exc:
                        ui.notify(f"Failed: {exc}", type="negative")

                def _unlock_shared(e):
                    if ev_ce:
                        ev_ce["set_readonly"](False)
                    _ev_editable[0] = True
                    e.sender.disable()

                build_event_share_banner(
                    message=f"This event is shared by {ev_n} specimens — editing changes all of them.",
                    actions=[
                        {"label": f"Edit all {ev_n}", "icon": "edit_note", "on_click": _unlock_shared},
                        {"label": "Detach & copy event", "icon": "fork_right",
                         "on_click": _detach, "primary": True},
                    ],
                )

            if ev_id is None:
                ui.label("No collecting event linked.").classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
                ev_ce = None
            else:
                # Shared widget (same form as Digitize); seeded from the snapshot.
                ev_ce = build_collecting_event_form(session_factory, default_recby_fn=_default_recby)
                ev_ce["load"](ev_snap)
                if ev_n > 1:
                    ev_ce["set_readonly"](True)   # view-only until "Edit all" unlocks

        # ── Biological Associations card ───────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            ui.label("Biological Associations").classes("section-label mb-2")
            ui.separator().classes("mb-3")

            assoc_col = ui.column().classes("w-full gap-1 mb-3")

            def _refresh_assoc_list():
                assoc_col.clear()
                cur = _with_session(
                    lambda s: bio_svc.get_associations_for_specimen(s, co_id)
                )
                with assoc_col:
                    if not cur:
                        ui.label("No associations.").classes("text-sm italic") \
                            .style("color:var(--tp-base-soft)")
                    for a in cur:
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.icon("link", size="xs") \
                                .style("color:var(--tp-secondary); opacity:.7")
                            ui.label(f"{a.rel_name} — {a.object_label}").classes("text-sm flex-1")
                            _media_btn(session_factory,
                                       target_kind="biological_association",
                                       target_id=a.id, tooltip="Association media")
                            (
                                ui.button("", icon="close")
                                .props("flat dense round size=xs")
                                .on_click(lambda _, ba_id=a.id: _remove_assoc(ba_id))
                            )

            def _remove_assoc(ba_id: int):
                try:
                    with session_factory() as s:
                        with s.begin():
                            bio_svc.remove_biological_association(s, ba_id)
                    _refresh_assoc_list()
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            _refresh_assoc_list()

            ui.separator().classes("my-2")
            ui.label("Add association").classes("text-sm font-medium mb-1")
            ui.label(
                "Add one association at a time — save associations immediately using the button below."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            rel_opts = _with_session(bio_svc.get_relationship_options)
            assoc_rel_sel = ui.select(
                options={r.id: r.name for r in rel_opts},
                label="Relationship",
                clearable=True,
            ).classes("w-full mb-2")

            bio_codes_local: list[str] = list(get_config().bio_assoc_default_codes)
            bio_state = build_taxon_search(
                session_factory,
                nomenclatural_codes=bio_codes_local,
                sources=("local", "taxonworks", "powo"),
                placeholder="Type plant or fungus name…",
            )

            def _add_assoc():
                rel_id   = assoc_rel_sel.value
                taxon_id = bio_state["taxon_id"]
                if not rel_id:
                    ui.notify("Select a relationship first.", type="warning")
                    return
                if not taxon_id:
                    ui.notify("Select a taxon first.", type="warning")
                    return
                if taxon_id == -1:
                    ui.notify("Taxon is still importing — wait a moment.", type="warning")
                    return
                try:
                    with session_factory() as s:
                        with s.begin():
                            bio_svc.save_biological_association(
                                s,
                                collection_object_id=co_id,
                                biological_relationship_id=rel_id,
                                object_taxon_id=taxon_id,
                            )
                    bio_state["clear"]()
                    assoc_rel_sel.value = None
                    _refresh_assoc_list()
                    ui.notify("Association saved.", type="positive")
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            with ui.row().classes("w-full items-center mt-2"):
                ui.space()
                ui.button("Save association", icon="add", on_click=_add_assoc) \
                    .props("flat no-caps color=secondary")

        # ── Save bar ─────────────────────────────────────────────────────────
        def _collect_co_fields() -> dict:
            return {
                # collection_code may change (gifting); _save rejects an empty value
                # up front (NOT NULL) and update_collection_object never touches
                # catalog_number.
                "collection_code":   (coll_code_in.value or "").strip(),
                "individual_count":  int(count_in.value or 1),
                "preparations":      preps_in.value,
                "life_stage":        stage_sel.value,
                "disposition":       disp_sel.value,
                "basis_of_record":   basis_sel.value,
                "occurrence_remarks":rem_in.value,
            }

        def _collect_ev_fields() -> dict:
            return ev_ce["collect_fields"]() if ev_ce else {}

        def _save():
            if not (coll_code_in.value or "").strip():
                ui.notify("collectionCode cannot be empty.", type="warning")
                return
            try:
                with session_factory() as s:
                    with s.begin():
                        sp_svc.update_collection_object(s, co_id, **_collect_co_fields())
                        ev_fields = _collect_ev_fields()
                        # Only write the shared event if the user unlocked it; in
                        # view mode this is a specimen-only save (the event — and
                        # every other specimen on it — is left untouched).
                        if ev_fields and ev_id and _ev_editable[0]:
                            recby_id = ev_ce["commit"](s)
                            ev_svc.update_collecting_event(
                                s, ev_id,
                                recorded_by_id=recby_id,
                                **ev_fields,
                            )
                ui.notify("Changes saved.", type="positive")
                if on_saved:
                    on_saved()
                _load_specimen(co_id)
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")

        with ui.row().classes("w-full items-center gap-4 px-1"):
            ui.space()
            ui.button("Save changes", icon="save", on_click=_save).classes("btn-save")

    # ── Event form ─────────────────────────────────────────────────────────────
    def _build_event_form(ev_id, n, cos, ev_snap):
        # A shared event (n>1) opens read-only ("view"); the user must click
        # "Edit all" to unlock editing + the Save button, since saving here
        # rewrites the event for every linked specimen.
        shared = n > 1

        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-2 mb-1"):
                ui.label("Collecting Event").classes("section-label")
                ui.label(f"#{ev_id} — {n} specimen{'s' if n != 1 else ''}") \
                    .classes("text-sm").style(
                        "color:var(--tp-warning, #f59e0b)" if shared
                        else "color:var(--tp-base-soft)"
                    )
                ui.space()
                _media_btn(session_factory, target_kind="collecting_event",
                           target_id=ev_id, tooltip="Event media")
            ui.separator().classes("mb-3")

            if cos:
                with ui.expansion(f"Linked specimens ({n})").classes("w-full mb-3"):
                    for c_id, ns, cn in cos:
                        ui.label(f"#{c_id}  {id_svc.format_catalog_display(ns, cn)}") \
                            .classes("text-xs font-mono")
                    if n > len(cos):
                        ui.label(f"… and {n - len(cos)} more") \
                            .classes("text-xs italic").style("color:var(--tp-base-soft)")

            if shared:
                def _unlock(e):
                    ev_ce["set_readonly"](False)
                    save_btn.set_enabled(True)
                    e.sender.disable()

                build_event_share_banner(
                    message=f"This event is shared by {n} specimens — saving changes all of them.",
                    actions=[{"label": f"Edit all {n}", "icon": "edit_note",
                              "on_click": _unlock, "primary": True}],
                )

            # Shared widget (same form as Digitize); seeded from the snapshot.
            ev_ce = build_collecting_event_form(session_factory, default_recby_fn=_default_recby)
            ev_ce["load"](ev_snap)
            if shared:
                ev_ce["set_readonly"](True)   # view-only until "Edit all" unlocks
            # (event media lives behind the icon in the card header above)

        def _save_event():
            try:
                with session_factory() as s:
                    with s.begin():
                        recby_id = ev_ce["commit"](s)
                        ev_svc.update_collecting_event(
                            s, ev_id,
                            recorded_by_id=recby_id,
                            **ev_ce["collect_fields"](),
                        )
                ui.notify(f"Event #{ev_id} saved.", type="positive")
                if on_saved:
                    on_saved()
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")

        with ui.row().classes("w-full items-center gap-4 px-1 mt-2"):
            ui.space()
            save_btn = ui.button("Save event", icon="save", on_click=_save_event).classes("btn-save")
            if shared:
                save_btn.set_enabled(False)   # enabled by the "Edit all" unlock

    return {"open_specimen": _open_specimen}

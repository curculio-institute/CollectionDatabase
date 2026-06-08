"""Records tab — view and edit existing specimens and collecting events."""
from __future__ import annotations

from nicegui import ui

from app.config import get_config
from app.models import CollectionObject, CollectingEvent, TaxonDetermination, Taxon
import app.services.specimens as sp_svc
import app.services.events as ev_svc
import app.services.biological as bio_svc
import app.services.persons as persons_svc
from app.services.taxa import format_scientific_name
from app.ui.taxon_search import build_taxon_search, _local_item_html
from app.ui.identification_list import build_identification_list
from app.ui.bio_object_search import build_bio_object_search
from app.ui.date_input import attach_date_validation

_FLOAT_ATTRS = frozenset({
    "decimal_latitude", "decimal_longitude",
    "coordinate_uncertainty_in_meters", "coordinate_precision",
    "minimum_elevation_in_meters", "maximum_elevation_in_meters",
})

_SAMPLING_PROTOCOLS = [
    "hand collecting", "sweep net", "beating", "pitfall trap",
    "light trap", "sifting", "bark peeling", "rearing", "Berlese funnel",
    "yellow pan trap", "window trap", "observation", "",
]
_SEX_OPTIONS        = ["male", "female", "undetermined", ""]
_LIFE_STAGE_OPTIONS = ["adult", "larva", "pupa", "egg", ""]
_BASIS_OPTIONS      = ["PreservedSpecimen", "FossilSpecimen", "LivingSpecimen",
                       "HumanObservation", "MachineObservation"]
_DISPOSITION_OPTIONS= ["in collection", "on loan", "donated",
                       "exchanged", "missing", "destroyed", ""]


def build_records_tab(session_factory, *, on_saved: callable | None = None) -> None:
    """Render the Records tab content into the current NiceGUI container.

    on_saved: called after any successful save so other tabs can refresh.
    """

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

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
                    f"{r.collection_code} {r.catalog_number}  "
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
        ui.timer(2.0, lambda: spec_select.__setattr__("options", _specimen_opts()))
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
        ui.timer(2.0, lambda: ev_select.__setattr__("options", _event_opts()))

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
                "sex":               co.sex,
                "individual_count":  co.individual_count,
                "preparations":      co.preparations,
                "life_stage":        co.life_stage,
                "type_status":       co.type_status,
                "disposition":       co.disposition,
                "basis_of_record":   co.basis_of_record,
                "occurrence_remarks":co.occurrence_remarks,
            }

            # Snapshot all determinations as plain dicts while session is open (avoids DetachedInstanceError).
            det_snaps: list[dict] = []
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
                det_snaps.append({
                    "id":                       d.id,
                    "taxon_label":              t_label,
                    "is_synonym":               is_syn,
                    "accepted_label":           acc_label,
                    "identified_by":            d.identified_by,
                    "date_identified":          d.date_identified,
                    "identification_qualifier": d.identification_qualifier,
                    "identification_remarks":   d.identification_remarks,
                    "is_current":               bool(d.is_current),
                })

            _f = lambda v: str(v) if v is not None else ""
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
                "recorded_by":                      ev.recorded_by          if ev else None,
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
                "recorded_by":                      ev.recorded_by,
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

    # ── Specimen form ─────────────────────────────────────────────────────────
    def _build_specimen_form(
        co_id, ev_id, ev_n, co_snap, det_snaps, ev_snap, assocs
    ):
        _s = lambda v: str(v) if v is not None else ""

        # ── Specimen card ────────────────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-2 mb-1"):
                ui.label("Specimen").classes("section-label")
                ui.label(
                    f"#{co_id}  {co_snap['collection_code']} {co_snap['catalog_number']}"
                ).classes("text-sm font-mono").style("color:var(--tp-base-soft)")
            ui.separator().classes("mb-3")

            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                sex_sel  = ui.select(
                    _SEX_OPTIONS, label="sex", value=co_snap["sex"]
                ).classes("w-28")
                count_in = ui.number(
                    "n", value=co_snap["individual_count"] or 1, min=0, precision=0
                ).classes("w-20")
                preps_in = ui.input(
                    "preparations", value=co_snap["preparations"] or ""
                ).classes("flex-1 min-w-40")

            with ui.expansion("More fields").classes("w-full mt-2"):
                with ui.grid(columns=4).classes("w-full gap-3"):
                    stage_sel = ui.select(
                        _LIFE_STAGE_OPTIONS, label="lifeStage", value=co_snap["life_stage"]
                    ).classes("col-span-1")
                    type_in   = ui.input(
                        "typeStatus", value=co_snap["type_status"] or ""
                    ).classes("col-span-1")
                    disp_sel  = ui.select(
                        _DISPOSITION_OPTIONS, label="disposition", value=co_snap["disposition"]
                    ).classes("col-span-1")
                    basis_sel = ui.select(
                        _BASIS_OPTIONS, label="basisOfRecord", value=co_snap["basis_of_record"]
                    ).classes("col-span-1")
                rem_in = ui.input(
                    "occurrenceRemarks", value=co_snap["occurrence_remarks"] or ""
                ).classes("w-full mt-3")

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
            ui.separator().classes("mb-3")

            if ev_n > 1 and ev_id:
                with ui.row().classes("items-center gap-3 mb-3"):
                    ui.icon("warning", size="sm").style("color:var(--tp-warning, #f59e0b)")
                    ui.label(
                        f"Editing these fields affects all {ev_n} specimens at this event."
                    ).classes("text-sm").style("color:var(--tp-warning, #f59e0b)")
                    ui.space()

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

                    ui.button("Detach & copy event", icon="fork_right", on_click=_detach) \
                        .props("flat no-caps color=secondary size=sm")

            if ev_id is None:
                ui.label("No collecting event linked.").classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
                # Stub locals so the save handler doesn't reference undefined names
                ev_country_in = ev_code_in = ev_state_in = ev_county_in = None
                ev_muni_in = ev_island_in = ev_locality_in = ev_verblocal_in = None
                ev_edate_in = ev_verbdate_in = ev_recby_in = ev_lat_in = ev_lon_in = None
                ev_uncert_in = ev_elevmin_in = ev_elevmax_in = ev_habitat_in = None
                ev_protocol_sel = ev_fieldnum_in = ev_verblabel_in = None
            else:
                with ui.grid(columns=2).classes("w-full gap-3"):
                    ev_country_in  = ui.input("country",       value=ev_snap["country"] or "").classes("col-span-1")
                    ev_code_in     = ui.input("countryCode",   value=ev_snap["country_code"] or "", placeholder="DE").classes("col-span-1")
                    ev_state_in    = ui.input("stateProvince", value=ev_snap["state_province"] or "").classes("col-span-1")
                    ev_county_in   = ui.input("county",        value=ev_snap["county"] or "").classes("col-span-1")
                    ev_muni_in     = ui.input("municipality",  value=ev_snap["municipality"] or "").classes("col-span-1")
                    ev_island_in   = ui.input("island",        value=ev_snap["island"] or "").classes("col-span-1")
                ev_locality_in  = ui.input("locality",         value=ev_snap["locality"] or "").classes("w-full mt-2")
                ev_verblocal_in = ui.input("verbatimLocality", value=ev_snap["verbatim_locality"] or "").classes("w-full mt-2")
                with ui.grid(columns=3).classes("w-full gap-3 mt-2"):
                    ev_edate_in    = ui.input("eventDate",     value=ev_snap["event_date"] or "").classes("col-span-1")
                    attach_date_validation(ev_edate_in, allow_interval=True)
                    ev_verbdate_in = ui.input("verbatimDate",  value=ev_snap["verbatim_event_date"] or "").classes("col-span-1")
                    with session_factory() as _s:
                        _recby_opts = persons_svc.person_options(_s)
                    _recby_val = ev_snap["recorded_by"] or None
                    if _recby_val and _recby_val not in _recby_opts:
                        _recby_opts = {_recby_val: _recby_val, **_recby_opts}
                    with ui.element("div").classes("col-span-1 flex items-center gap-1"):
                        ev_recby_in = (
                            ui.select(
                                options=_recby_opts,
                                label="recordedBy",
                                value=_recby_val,
                                with_input=True,
                                clearable=True,
                            )
                            .classes("flex-1")
                            .props("use-input input-debounce=0 new-value-mode=add-unique")
                        )
                        (
                            ui.button("", icon="push_pin")
                            .props("flat dense round size=xs")
                            .tooltip("Insert default name")
                            .on_click(lambda: ev_recby_in.set_value(get_config().default_recorded_by))
                            .bind_visibility_from(ev_recby_in, "value", lambda v: not v)
                        )
                with ui.grid(columns=4).classes("w-full gap-3 mt-2"):
                    ev_lat_in    = ui.input("latitude",    value=_s(ev_snap["decimal_latitude"])).classes("col-span-1")
                    ev_lon_in    = ui.input("longitude",   value=_s(ev_snap["decimal_longitude"])).classes("col-span-1")
                    ev_uncert_in = ui.input("uncertainty m", value=_s(ev_snap["coordinate_uncertainty_in_meters"])).classes("col-span-1")
                    ev_habitat_in= ui.input("habitat",     value=ev_snap["habitat"] or "").classes("col-span-1")
                with ui.grid(columns=3).classes("w-full gap-3 mt-2"):
                    ev_elevmin_in  = ui.input("elev min m",  value=_s(ev_snap["minimum_elevation_in_meters"])).classes("col-span-1")
                    ev_elevmax_in  = ui.input("elev max m",  value=_s(ev_snap["maximum_elevation_in_meters"])).classes("col-span-1")
                    ev_protocol_sel= ui.select(_SAMPLING_PROTOCOLS, label="samplingProtocol",
                                               value=ev_snap["sampling_protocol"]).classes("col-span-1")
                with ui.grid(columns=2).classes("w-full gap-3 mt-2"):
                    ev_fieldnum_in  = ui.input("fieldNumber",   value=ev_snap["field_number"] or "").classes("col-span-1")
                    ev_verblabel_in = ui.input("verbatimLabel", value=ev_snap["verbatim_label"] or "").classes("col-span-1")

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
            bio_state = build_bio_object_search(session_factory, bio_codes_local)

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
                "sex":               sex_sel.value,
                "individual_count":  int(count_in.value or 1),
                "preparations":      preps_in.value,
                "life_stage":        stage_sel.value,
                "type_status":       type_in.value,
                "disposition":       disp_sel.value,
                "basis_of_record":   basis_sel.value,
                "occurrence_remarks":rem_in.value,
            }

        def _collect_ev_fields() -> dict:
            if ev_country_in is None:
                return {}
            return {
                "country":                          ev_country_in.value,
                "country_code":                     ev_code_in.value,
                "state_province":                   ev_state_in.value,
                "county":                           ev_county_in.value,
                "municipality":                     ev_muni_in.value,
                "island":                           ev_island_in.value,
                "locality":                         ev_locality_in.value,
                "verbatim_locality":                ev_verblocal_in.value,
                "event_date":                       ev_edate_in.value,
                "verbatim_event_date":              ev_verbdate_in.value,
                "recorded_by":                      ev_recby_in.value,
                "decimal_latitude":                 ev_lat_in.value,
                "decimal_longitude":                ev_lon_in.value,
                "coordinate_uncertainty_in_meters": ev_uncert_in.value,
                "minimum_elevation_in_meters":      ev_elevmin_in.value,
                "maximum_elevation_in_meters":      ev_elevmax_in.value,
                "habitat":                          ev_habitat_in.value,
                "sampling_protocol":                ev_protocol_sel.value,
                "field_number":                     ev_fieldnum_in.value,
                "verbatim_label":                   ev_verblabel_in.value,
            }

        def _save():
            try:
                with session_factory() as s:
                    with s.begin():
                        sp_svc.update_collection_object(s, co_id, **_collect_co_fields())
                        ev_fields = _collect_ev_fields()
                        if ev_fields and ev_id:
                            ev_svc.update_collecting_event(s, ev_id, **ev_fields)
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
        _s = lambda v: str(v) if v is not None else ""

        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-2 mb-1"):
                ui.label("Collecting Event").classes("section-label")
                ui.label(f"#{ev_id} — {n} specimen{'s' if n != 1 else ''}") \
                    .classes("text-sm").style("color:var(--tp-base-soft)")
            ui.separator().classes("mb-3")

            if cos:
                with ui.expansion(f"Linked specimens ({n})").classes("w-full mb-3"):
                    for c_id, ns, cn in cos:
                        ui.label(f"#{c_id}  {ns} {cn}").classes("text-xs font-mono")
                    if n > len(cos):
                        ui.label(f"… and {n - len(cos)} more") \
                            .classes("text-xs italic").style("color:var(--tp-base-soft)")

            with ui.grid(columns=2).classes("w-full gap-3"):
                ev_country_in  = ui.input("country",       value=ev_snap["country"] or "").classes("col-span-1")
                ev_code_in     = ui.input("countryCode",   value=ev_snap["country_code"] or "", placeholder="DE").classes("col-span-1")
                ev_state_in    = ui.input("stateProvince", value=ev_snap["state_province"] or "").classes("col-span-1")
                ev_county_in   = ui.input("county",        value=ev_snap["county"] or "").classes("col-span-1")
                ev_muni_in     = ui.input("municipality",  value=ev_snap["municipality"] or "").classes("col-span-1")
                ev_island_in   = ui.input("island",        value=ev_snap["island"] or "").classes("col-span-1")
            ev_locality_in  = ui.input("locality",         value=ev_snap["locality"] or "").classes("w-full mt-2")
            ev_verblocal_in = ui.input("verbatimLocality", value=ev_snap["verbatim_locality"] or "").classes("w-full mt-2")
            with ui.grid(columns=3).classes("w-full gap-3 mt-2"):
                ev_edate_in    = ui.input("eventDate",    value=ev_snap["event_date"] or "").classes("col-span-1")
                attach_date_validation(ev_edate_in, allow_interval=True)
                ev_verbdate_in = ui.input("verbatimDate", value=ev_snap["verbatim_event_date"] or "").classes("col-span-1")
                with session_factory() as _s:
                    _recby_opts = persons_svc.person_options(_s)
                _recby_val = ev_snap["recorded_by"] or None
                if _recby_val and _recby_val not in _recby_opts:
                    _recby_opts = {_recby_val: _recby_val, **_recby_opts}
                with ui.element("div").classes("col-span-1 flex items-center gap-1"):
                    ev_recby_in = (
                        ui.select(
                            options=_recby_opts,
                            label="recordedBy",
                            value=_recby_val,
                            with_input=True,
                            clearable=True,
                        )
                        .classes("flex-1")
                        .props("use-input input-debounce=0 new-value-mode=add-unique")
                    )
                    (
                        ui.button("", icon="push_pin")
                        .props("flat dense round size=xs")
                        .tooltip("Insert default name")
                        .on_click(lambda: ev_recby_in.set_value(get_config().default_recorded_by))
                        .bind_visibility_from(ev_recby_in, "value", lambda v: not v)
                    )
            with ui.grid(columns=4).classes("w-full gap-3 mt-2"):
                ev_lat_in    = ui.input("latitude",     value=_s(ev_snap["decimal_latitude"])).classes("col-span-1")
                ev_lon_in    = ui.input("longitude",    value=_s(ev_snap["decimal_longitude"])).classes("col-span-1")
                ev_uncert_in = ui.input("uncertainty m",value=_s(ev_snap["coordinate_uncertainty_in_meters"])).classes("col-span-1")
                ev_habitat_in= ui.input("habitat",      value=ev_snap["habitat"] or "").classes("col-span-1")
            with ui.grid(columns=3).classes("w-full gap-3 mt-2"):
                ev_elevmin_in  = ui.input("elev min m", value=_s(ev_snap["minimum_elevation_in_meters"])).classes("col-span-1")
                ev_elevmax_in  = ui.input("elev max m", value=_s(ev_snap["maximum_elevation_in_meters"])).classes("col-span-1")
                ev_protocol_sel= ui.select(_SAMPLING_PROTOCOLS, label="samplingProtocol",
                                           value=ev_snap["sampling_protocol"]).classes("col-span-1")
            with ui.grid(columns=2).classes("w-full gap-3 mt-2"):
                ev_fieldnum_in  = ui.input("fieldNumber",   value=ev_snap["field_number"] or "").classes("col-span-1")
                ev_verblabel_in = ui.input("verbatimLabel", value=ev_snap["verbatim_label"] or "").classes("col-span-1")

        def _save_event():
            fields = {
                "country":                          ev_country_in.value,
                "country_code":                     ev_code_in.value,
                "state_province":                   ev_state_in.value,
                "county":                           ev_county_in.value,
                "municipality":                     ev_muni_in.value,
                "island":                           ev_island_in.value,
                "locality":                         ev_locality_in.value,
                "verbatim_locality":                ev_verblocal_in.value,
                "event_date":                       ev_edate_in.value,
                "verbatim_event_date":              ev_verbdate_in.value,
                "recorded_by":                      ev_recby_in.value,
                "decimal_latitude":                 ev_lat_in.value,
                "decimal_longitude":                ev_lon_in.value,
                "coordinate_uncertainty_in_meters": ev_uncert_in.value,
                "minimum_elevation_in_meters":      ev_elevmin_in.value,
                "maximum_elevation_in_meters":      ev_elevmax_in.value,
                "habitat":                          ev_habitat_in.value,
                "sampling_protocol":                ev_protocol_sel.value,
                "field_number":                     ev_fieldnum_in.value,
                "verbatim_label":                   ev_verblabel_in.value,
            }
            try:
                with session_factory() as s:
                    with s.begin():
                        ev_svc.update_collecting_event(s, ev_id, **fields)
                ui.notify(f"Event #{ev_id} saved.", type="positive")
                if on_saved:
                    on_saved()
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")

        with ui.row().classes("w-full items-center gap-4 px-1 mt-2"):
            ui.space()
            ui.button("Save event", icon="save", on_click=_save_event).classes("btn-save")

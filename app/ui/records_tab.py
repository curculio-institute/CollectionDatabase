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
import app.services.repositories as repo_svc
import html as _html
import app.services.taxa as svc_taxa
import app.ui.record_summary as rs
from app.services.taxa import (
    compose_full_name,
    format_scientific_name,
    split_scientific_name_authorship,
)
from app.vocab import IDENTIFICATION_QUALIFIER_OPTIONS
from app.ui.choice_field import build_choice_field
from app.ui.field_occurrence_editor import open_field_occurrence_editor
from app.ui.taxon_search import build_taxon_search, _local_item_html
from app.ui.identification_list import build_identification_list
from app.ui.collecting_event_form import build_collecting_event_form
from app.ui.specimen_form import build_specimen_form
from app.ui.event_reuse import build_event_share_banner
from app.ui.media_panel import build_media_button
import app.services.media as media_svc
from app.ui.external_id_panel import build_external_id_button
from app.ui.life_stage_panel import build_life_stage_button

_FLOAT_ATTRS = frozenset({
    "decimal_latitude", "decimal_longitude",
    "coordinate_uncertainty_in_meters", "coordinate_precision",
    "minimum_elevation_in_meters", "maximum_elevation_in_meters",
})


def _norm(v):
    """Normalise a field value for unsaved-changes comparison (#47): None and an
    empty/whitespace string are the same 'empty', strings are stripped, so a field
    edited back to its loaded value is correctly seen as no longer dirty."""
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return v


def _media_btn(session_factory, *, target_kind, target_id, tooltip="Media"):
    """A compact media icon+popup button (bound mode) for one saved record. The button
    badge indicates how many files are attached (progressive disclosure — the gallery is
    behind the click)."""
    return build_media_button(
        session_factory, target_kind=target_kind,
        target_id_getter=lambda: target_id, tooltip=tooltip,
    )["button"]


def _ext_btn(session_factory, *, target_kind, target_id, tooltip="Resource identifiers"):
    """A compact external-resource-identifier icon+popup button (bound mode)."""
    return build_external_id_button(
        session_factory, target_kind=target_kind,
        target_id_getter=lambda: target_id, tooltip=tooltip,
    )["button"]


def _ls_btn(session_factory, *, target_id):
    """A compact rearing / life-stage-history icon+popup button (bound mode)."""
    return build_life_stage_button(
        session_factory, target_id_getter=lambda: target_id,
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

        # The picker row carries what actually identifies a specimen in the hand — the name, but
        # also where and when it was collected and by whom, because several specimens share a
        # name and only the event tells them apart. Two strings per row, deliberately:
        #
        #   label — PLAIN text. Quasar filters `with_input` against it and shows it in the input
        #           once selected, so it must not contain markup. It carries every searchable
        #           datum (catalog, name, authorship, locality, country, date, collector), which
        #           is what makes the box a search over all of them and not just the name.
        #   html  — the RICH row shown in the dropdown, with the name italicised BY RANK and the
        #           authorship left roman (taxa.scientific_name_html owns that convention).
        def _specimen_rows() -> list[dict]:
            """One row per specimen: a PLAIN label (Quasar filters + echoes it) and the RICH
            html (record_summary — the single owner of how a record is summarised)."""
            rows = _with_session(lambda s: sp_svc.recent_specimens(s, limit=1000))
            out: list[dict] = []
            for r in rows:
                common = dict(
                    catalog=id_svc.format_catalog_display(r.collection_code, r.catalog_number),
                    name=r.scientific_name or "",
                    authorship=r.authorship,
                    hosts=r.hosts,
                    sex=r.sex,
                    count=r.individual_count,
                    locality=r.place,   # Country: state, municipality, locality (format_place)
                    event_date=r.event_date,
                    recorded_by=r.recorded_by,
                    identified_by=r.identified_by,
                    date_identified=r.date_identified,
                )
                out.append({
                    "value": r.collection_object_id,
                    "label": rs.specimen_plain(**common),
                    "html":  rs.specimen_html(rank=r.taxon_rank,
                                              confidential=r.confidential,
                                              event_confidential=r.event_confidential,
                                              **common),
                })
            return out

        _rows_cache: list[dict] = []   # the rows behind the options; see the wrapper below

        def _apply_specimen_rows() -> None:
            """Rebuild the option list. The rich HTML is re-attached by the wrapper below.

            Two NiceGUI facts make this awkward, and both bite silently:
              * Select maps option values to their INDEX in the props
                (`{"value": 0, "label": …}`) and keeps the real keys in `.options` — so writing
                `_props["options"]` by hand breaks the model-value mapping and the select shows
                NOTHING.
              * `Select.update()` calls `_update_options()`, which REBUILDS `_props["options"]`
                from the plain labels on every update — so an extra key written once is wiped on
                the next interaction (a keystroke, the 2 s refresh timer).
            There is no supported hook, so `_update_options` is wrapped on this instance to
            re-attach the html each time it rebuilds.

            **Only re-set the options when the rows actually changed.** `set_options` resets the
            frontend q-select, discarding whatever the user has typed to filter — so an
            unconditional 2 s refresh made a typed search ("Otiorhynchus") snap back to the full
            list on the next tick. The list rarely changes, so the timer is now almost always a
            no-op and the active filter survives.
            """
            new_rows = _specimen_rows()
            if [(r["value"], r["label"]) for r in new_rows] == \
               [(r["value"], r["label"]) for r in _rows_cache]:
                return                      # unchanged → don't clobber the user's filter typing
            _rows_cache[:] = new_rows
            spec_select.set_options({r["value"]: r["label"] for r in _rows_cache})

        def _attach_option_html() -> None:
            for opt, row in zip(spec_select._props.get("options", []), _rows_cache):
                opt["html"] = row["html"]

        def _event_opts() -> dict:
            rows = _with_session(lambda s: ev_svc.search_collecting_events(s, "", limit=500))
            return {r.id: r.summary for r in rows}

        spec_select = (
            ui.select(
                options={},
                with_input=True,
                clearable=True,
                label="Search specimens…  (catalog, taxon, locality, date, collector)",
            )
            .classes("w-full")
        )
        # The dropdown row is HTML (italics by rank); the input keeps the plain label.
        spec_select.add_slot("option", r"""
            <q-item v-bind="props.itemProps">
              <q-item-section>
                <div v-html="props.opt.html"></div>
              </q-item-section>
            </q-item>
        """)
        _orig_update_options = spec_select._update_options

        def _update_options_keeping_html() -> None:
            _orig_update_options()          # NiceGUI rebuilds options from the labels …
            _attach_option_html()           # … and we put the rich row back on each of them.

        spec_select._update_options = _update_options_keeping_html   # NiceGUI internal (2.24)

        _apply_specimen_rows()
        ui.timer(2.0, _apply_specimen_rows)
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
        _ev_opts_cache: dict = {}

        def _apply_event_opts() -> None:
            opts = _event_opts()
            if opts == _ev_opts_cache:
                return                      # unchanged → keep the user's typed event filter
            _ev_opts_cache.clear(); _ev_opts_cache.update(opts)
            ev_select.set_options(opts)

        _apply_event_opts()
        ui.timer(2.0, _apply_event_opts)

    # ── Detail area ─────────────────────────────────────────────────────────
    detail = ui.column().classes("w-full gap-4")

    # Value-based unsaved-changes detection (#47). After each load we snapshot the
    # loaded form's editable widget values as a baseline; "dirty" is current !=
    # baseline. Unlike DOM-event detection this catches programmatic fills (the map
    # picker / reverse-geocode in the collecting-event form) and clears the moment
    # the form matches what was loaded again. `fn` is None when nothing is loaded.
    _dirty = {"fn": None}

    def _clear_detail():
        detail.clear()
        _dirty["fn"] = None

    # ── Mode toggle ──────────────────────────────────────────────────────────
    def _set_mode_specimen():
        mode_spec_btn.props("color=secondary")
        mode_ev_btn.props("flat")
        spec_select.style(remove="display:none")
        ev_select.style(add="display:none")
        _clear_detail()
        spec_select.value = None

    def _set_mode_event():
        mode_spec_btn.props("flat")
        mode_ev_btn.props("color=secondary")
        spec_select.style(add="display:none")
        ev_select.style(remove="display:none")
        _clear_detail()
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
                "collection_code": co.repository.collection_code,
                "individual_count":  co.individual_count,
                "preparations":      co.preparation.name if co.preparation else None,
                "life_stage":        co.life_stage,
                "disposition":       co.disposition.name if co.disposition else None,
                "basis_of_record":   co.basis_of_record,
                "occurrence_remarks":co.occurrence_remarks,
                "other_catalog_numbers": co.other_catalog_numbers,
                "confidential":      co.confidential,
            }

            # Snapshot all determinations as plain dicts while session is open (avoids DetachedInstanceError).
            det_snaps: list[dict] = []
            for d in sp_svc.get_determination_history(s, co_id):
                t = d.taxon
                verbatim = d.verbatim_identification or (
                    compose_full_name(s, t) if t else ""
                )
                name_bare, authorship = split_scientific_name_authorship(verbatim)
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
                det_snaps.append({
                    "id":                       d.id,
                    "taxon_label":              verbatim,   # plain text for the search-box seed
                    "taxon_name":               name_bare,  # bare name for the renderer
                    "authorship":               authorship,
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

            ev_snap = {
                "country":                          (ev.country_obj.name if ev and ev.country_obj else None),
                # ISO codes identify *which* vocab row (Limburg BE-VLI vs NL-LI); without
                # them a re-save of an ambiguous name would re-point the event (0056).
                "country_iso":                      (ev.country_obj.iso_code if ev and ev.country_obj else None),
                "state_province":                   (ev.state_province_obj.name if ev and ev.state_province_obj else None),
                "state_province_iso":               (ev.state_province_obj.iso_code if ev and ev.state_province_obj else None),
                "administrative_region":            (ev.administrative_region_obj.name if ev and ev.administrative_region_obj else None),
                "county":                           (ev.county_obj.name if ev and ev.county_obj else None),
                "municipality":                     ev.municipality         if ev else None,
                "island":                           (ev.island_obj.name if ev and ev.island_obj else None),
                "locality":                         ev.locality             if ev else None,
                "verbatim_locality":                ev.verbatim_locality    if ev else None,
                "event_date":                       ev.event_date           if ev else None,
                "verbatim_event_date":              ev.verbatim_event_date  if ev else None,
                "recorded_by":                      ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None,
                "confidential":                     ev.confidential if ev else 0,
                "habitat":                          (ev.habitat_obj.name if ev and ev.habitat_obj else None),
                "decimal_latitude":                 ev.decimal_latitude     if ev else None,
                "decimal_longitude":                ev.decimal_longitude    if ev else None,
                "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters if ev else None,
                "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters      if ev else None,
                "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters      if ev else None,
                "sampling_protocol":                (ev.sampling_protocol_obj.name if ev and ev.sampling_protocol_obj else None),
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
                (c.id, c.repository.collection_code, c.catalog_number)
                for c in ev.collection_objects[:30]
            ]
            ev_snap = {
                "country":                          ev.country_obj.name if ev.country_obj else None,
                "country_iso":                      ev.country_obj.iso_code if ev.country_obj else None,
                "state_province":                   ev.state_province_obj.name if ev.state_province_obj else None,
                "state_province_iso":               ev.state_province_obj.iso_code if ev.state_province_obj else None,
                "administrative_region":            ev.administrative_region_obj.name if ev.administrative_region_obj else None,
                "county":                           ev.county_obj.name if ev.county_obj else None,
                "municipality":                     ev.municipality,
                "island":                           ev.island_obj.name if ev.island_obj else None,
                "locality":                         ev.locality,
                "verbatim_locality":                ev.verbatim_locality,
                "event_date":                       ev.event_date,
                "verbatim_event_date":              ev.verbatim_event_date,
                "recorded_by":                      ev.recorded_by_person.full_name if ev.recorded_by_person else None,
                "confidential":                     ev.confidential,
                "habitat":                          ev.habitat_obj.name if ev.habitat_obj else None,
                "decimal_latitude":                 ev.decimal_latitude,
                "decimal_longitude":                ev.decimal_longitude,
                "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters,
                "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters,
                "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters,
                "sampling_protocol":                ev.sampling_protocol_obj.name if ev.sampling_protocol_obj else None,
                "field_number":                     ev.field_number,
                "verbatim_label":                   ev.verbatim_label,
            }

        with detail:
            _build_event_form(ev_id, n, cos, ev_snap)

    spec_select.on_value_change(
        lambda e: _load_specimen(e.value) if e.value else _clear_detail()
    )
    ev_select.on_value_change(
        lambda e: _load_event(e.value) if e.value else _clear_detail()
    )

    # Programmatic open (used by the Print queue "open in Records" link, #37):
    # switch to specimen mode and select the specimen, which loads its detail
    # (event + determinations) for substantial edits — the record is master.
    def _open_specimen(co_id: int) -> None:
        _set_mode_specimen()
        spec_select.value = co_id

    def _open_event(ev_id: int) -> None:
        """Programmatic open (Explore drill-in): switch to event mode + load it."""
        _set_mode_event()
        ev_select.value = ev_id

    # ── Specimen form ─────────────────────────────────────────────────────────
    def _build_specimen_form(
        co_id, ev_id, ev_n, co_snap, det_snaps, ev_snap, assocs
    ):

        # ── Specimen card ────────────────────────────────────────────────
        # Shared specimen-field block (see app/ui/specimen_form.py), edit policy:
        # catalog_number is immutable (shown read-only in the header); collectionCode
        # is editable (gifting). Remaining fields are seeded from the DB snapshot.
        # Widgets are unpacked into locals so the save path references them unchanged.
        # The specimen's life-stage history and resource identifiers are STAGED like the
        # identifications: the handles are kept so "Save changes" can commit them in the
        # same transaction. (Media is still bound — see C2.)
        _sub: dict = {}

        def _build_spec_footer():
            _sub["ls"] = build_life_stage_button(
                session_factory, target_id_getter=lambda: co_id, deferred=True)
            _sub["ext"] = build_external_id_button(
                session_factory, target_kind="collection_object",
                target_id_getter=lambda: co_id,
                tooltip="Specimen resource identifiers", deferred=True)
            _sub["media"] = build_media_button(
                session_factory, target_kind="collection_object",
                target_id_getter=lambda: co_id, tooltip="Specimen media", deferred=True)
            return (
                _sub["ls"]["button"],
                _sub["ext"]["button"],
                _sub["media"]["button"],
            )

        spec = build_specimen_form(
            session_factory,
            identifier_policy="edit",
            initial=co_snap,
            identity_label=f"#{co_id}  {co_snap['catalog_number']}",
            footer_slot=lambda: _build_spec_footer(),
        )
        count_in     = spec["count_in"]
        prep_field   = spec["prep_field"]
        stage_sel    = spec["stage_sel"]
        disp_field   = spec["disp_field"]
        basis_sel    = spec["basis_sel"]
        rem_in       = spec["rem_in"]
        othercat_in  = spec["othercat_in"]
        conf_chk     = spec["conf_chk"]
        coll_code_in = spec["coll_code_disp"]

        # ── Identifications card ──────────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            ui.label("Identifications").classes("section-label mb-2")
            ui.separator().classes("mb-2")
            # Staged: identification edits live in memory until "Save changes" runs
            # id_state["commit"](s). on_changed only nudges the dirty poll — nothing is
            # written, so the cross-tab refresh must not fire here either (#54).
            id_state = build_identification_list(
                session_factory,
                co_id=co_id,
                initial_dets=det_snaps,
                deferred=True,
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
                    except Exception as exc:
                        ui.notify(f"Failed: {exc}", type="negative")
                        return
                    # Post-commit cleanup OUTSIDE the try (#59): the new event is
                    # already created+relinked; a hiccup in the reload must not be
                    # reported as a failure (a retry would detach a SECOND copy).
                    ui.notify(
                        f"Detached — new Event #{new_id} created for this specimen.",
                        type="positive",
                    )
                    _load_specimen(co_id)

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
                # Event media shares the form's Confidential footer line.
                def _ev_footer():
                    if ev_id:
                        _media_btn(session_factory, target_kind="collecting_event",
                                   target_id=ev_id, tooltip="Event media")
                ev_ce = build_collecting_event_form(
                    session_factory, default_recby_fn=_default_recby,
                    footer_slot=_ev_footer)
                ev_ce["load"](ev_snap)
                if ev_n > 1:
                    # view-only until "Edit all" unlocks — and say WHY on every locked control,
                    # not only in the banner above.
                    ev_ce["set_readonly"](
                        True,
                        f"Shared event — {ev_n} specimens use it. Press “Edit all {ev_n}” above "
                        f"to change it (that changes all of them), or “Detach & copy” to give "
                        f"this specimen its own event.",
                    )

        # ── Biological Associations card ───────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            ui.label("Biological Associations").classes("section-label mb-2")
            ui.separator().classes("mb-3")

            assoc_col = ui.column().classes("w-full gap-1 mb-3")

            # Staged, like the identifications above: the rows loaded from the DB, plus any
            # added in this session (id=None), minus any removed (their ids queued in
            # _assoc_deleted). Nothing is written until "Save changes" calls _assoc_commit.
            def _load_assoc_rows(s) -> list[dict]:
                return [{"id": a.id, "rel_id": a.rel_id, "rel_name": a.rel_name,
                         "taxon_id": a.object_taxon_id, "object_label": a.object_label,
                         "qualifier": a.identification_qualifier,
                         "fo_id": a.object_field_occurrence_id}
                        for a in bio_svc.get_associations_for_specimen(s, co_id)]

            _assoc_rows: list[dict] = _with_session(_load_assoc_rows)
            _assoc_deleted: list[int] = []
            _assoc_baseline = [(r["id"], r["rel_id"], r["taxon_id"]) for r in _assoc_rows]

            def _assoc_has_changes() -> bool:
                return (bool(_assoc_deleted)
                        or [(r["id"], r["rel_id"], r["taxon_id"]) for r in _assoc_rows]
                        != _assoc_baseline)

            def _reload_assoc_from_db():
                """After the full observation editor commits directly to the DB, refresh
                the saved rows' labels/qualifiers while keeping any staged (unsaved) adds."""
                staged = [r for r in _assoc_rows if r["id"] is None]
                _assoc_rows[:] = _with_session(_load_assoc_rows) + staged
                _refresh_assoc_list()

            def _refresh_assoc_list():
                assoc_col.clear()
                with assoc_col:
                    if not _assoc_rows:
                        ui.label("No associations.").classes("text-sm italic") \
                            .style("color:var(--tp-base-soft)")
                    for i, a in enumerate(_assoc_rows):
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.icon("link", size="xs") \
                                .style("color:var(--tp-secondary); opacity:.7")
                            _q = a.get("qualifier")
                            _obj = f"{_q} {a['object_label']}" if _q else a['object_label']
                            ui.label(f"{a['rel_name']} — {_obj}") \
                                .classes("text-sm flex-1")
                            if a["id"] is not None:
                                if a.get("fo_id"):
                                    ui.button("", icon="edit_note") \
                                        .props("flat dense round size=sm color=secondary") \
                                        .tooltip("Edit the full observation (field occurrence)") \
                                        .on_click(lambda _, fid=a["fo_id"], aid=a["id"]:
                                                  open_field_occurrence_editor(
                                                      session_factory, fid,
                                                      association_id=aid,
                                                      on_saved=_reload_assoc_from_db))
                                    # The iNaturalist URL / resource identifier belongs to
                                    # the observation (the field occurrence it came from);
                                    # media stays on the association.
                                    _ext_btn(session_factory,
                                             target_kind="field_occurrence",
                                             target_id=a["fo_id"],
                                             tooltip="Observation resource identifier (iNaturalist URL)")
                                _media_btn(session_factory,
                                           target_kind="biological_association",
                                           target_id=a["id"], tooltip="Association media")
                            else:
                                # A staged association has no id yet, so nothing can be
                                # attached to it. Say so rather than showing dead buttons.
                                ui.icon("schedule", size="xs") \
                                    .style("color:var(--tp-base-soft)") \
                                    .tooltip("Saved with the specimen — attach media or a "
                                             "resource identifier afterwards")
                            (
                                ui.button("", icon="close")
                                .props("flat dense round size=xs")
                                .on_click(lambda _, ix=i: _remove_assoc(ix))
                            )

            def _remove_assoc(ix: int):
                row = _assoc_rows.pop(ix)
                if row["id"] is not None:      # existing row: delete it on commit
                    _assoc_deleted.append(row["id"])
                _refresh_assoc_list()

            def _assoc_commit(s) -> None:
                """Apply staged associations inside the card's Save transaction."""
                for ba_id in _assoc_deleted:
                    bio_svc.remove_biological_association(s, ba_id)
                _assoc_deleted.clear()
                for row in _assoc_rows:
                    if row["id"] is None:
                        # The object taxon is recorded as its own HumanObservation
                        # field_occurrence sharing the specimen's event (decided
                        # 2026-07-11); only the qualifier is exposed here.
                        created = bio_svc.save_association_as_field_occurrence(
                            s,
                            collection_object_id=co_id,
                            biological_relationship_id=row["rel_id"],
                            taxon_id=row["taxon_id"],
                            identification_qualifier=row.get("qualifier"),
                        )
                        row["id"] = created.id

            _refresh_assoc_list()

            ui.separator().classes("my-2")
            ui.label("Add association").classes("text-sm font-medium mb-1")
            ui.label(
                "Add one association at a time. Associations are saved with the specimen "
                "when you press “Save changes”."
            ).classes("text-xs mb-2").style("color:var(--tp-base-soft)")

            # Same UI as the Digitize association card: relationship rendered as the
            # custom-dropdown (a peer of the taxon field, not overlooked); the compact
            # qualifier sits on the Add row.
            rel_opts = _with_session(bio_svc.get_relationship_options)
            _rel_name_to_id = {r.name: r.id for r in rel_opts}
            assoc_rel = build_choice_field(
                list(_rel_name_to_id.keys()), "Relationship", classes="w-full mb-2")

            bio_codes_local: list[str] = list(get_config().bio_assoc_default_codes)
            bio_state = build_taxon_search(
                session_factory,
                nomenclatural_codes=bio_codes_local,
                sources=("local", "taxonworks", "wcvp", "datasets"),
                placeholder="Type plant or fungus name…",
            )

            def _add_assoc():
                rel_name = assoc_rel["get_value"]()
                rel_id   = _rel_name_to_id.get(rel_name)
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
                    # Staged: created by _assoc_commit inside "Save changes".
                    _assoc_rows.append({
                        "id":           None,
                        "rel_id":       rel_id,
                        "rel_name":     rel_name,
                        "taxon_id":     taxon_id,
                        "qualifier":    assoc_qual["get_value"](),
                        "fo_id":        None,
                        "object_label": bio_state["label"] or f"taxon #{taxon_id}",
                    })
                    bio_state["clear"]()
                    assoc_rel["set_value"](None)
                    assoc_qual["set_value"](None)
                    _refresh_assoc_list()
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", type="negative")

            # The qualifier — only identification field exposed at data entry; compact,
            # beside the Add button (mirrors Digitize's row beside "Show animals too").
            with ui.row().classes("w-full items-center gap-3 mt-2"):
                assoc_qual = build_choice_field(
                    IDENTIFICATION_QUALIFIER_OPTIONS, "Qualifier", classes="w-40")
                ui.space()
                # "Add", not "Save": it stages the association; the card's Save writes it.
                ui.button("Add association", icon="add", on_click=_add_assoc) \
                    .props("flat no-caps color=secondary")

        # ── Save bar ─────────────────────────────────────────────────────────
        def _collect_co_fields(session) -> dict:
            # session: resolves the preparations controlled-vocab name → preparation_id
            # (get_or_create), like the person fields.
            return {
                # Re-homing to another collection (gifting) re-points the repository
                # FK (#75) — the in-app equivalent of editing ownerInstitutionCode.
                # _save rejects an empty code up front; resolve_id get-or-creates the
                # target repository; catalog_number is never touched.
                "repository_id":     repo_svc.resolve_id(
                    session, collection_code=(coll_code_in.value or "").strip()
                ),
                "individual_count":  int(count_in.value or 1),
                "preparation_id":    prep_field["commit"](session),
                "life_stage":        stage_sel.value,
                "disposition_id":    disp_field["commit"](session),
                "basis_of_record":   basis_sel.value,
                "occurrence_remarks":rem_in.value,
                "other_catalog_numbers": othercat_in.value,
                "confidential":      1 if conf_chk.value else 0,
            }

        def _collect_ev_fields() -> dict:
            return ev_ce["collect_fields"]() if ev_ce else {}

        def _save():
            if not (coll_code_in.value or "").strip():
                ui.notify("collectionCode cannot be empty.", type="warning")
                return
            _media_orphans: list[str] = []
            try:
                with session_factory() as s:
                    with s.begin():
                        sp_svc.update_collection_object(s, co_id, **_collect_co_fields(s))
                        # Staged identifications (add / edit / delete / set-current, and any
                        # new determiner name) are applied in the SAME transaction, so the
                        # card saves atomically or not at all.
                        id_state["commit"](s)
                        _assoc_commit(s)
                        _sub["ls"]["commit"](s, co_id)
                        _sub["ext"]["commit"](s, co_id)
                        _media_orphans = _sub["media"]["commit"](s, co_id) or []
                        ev_fields = _collect_ev_fields()
                        # Only write the shared event if the user unlocked it; in
                        # view mode this is a specimen-only save (the event — and
                        # every other specimen on it — is left untouched).
                        if ev_fields and ev_id and _ev_editable[0]:
                            event_ids = ev_ce["commit"](s)
                            ev_svc.update_collecting_event(
                                s, ev_id,
                                **event_ids,
                                **ev_fields,
                            )
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            # Post-commit cleanup goes OUTSIDE the try: the data is already
            # committed, so a hiccup in on_saved()/reload must not be reported as a
            # "Save failed" (which would prompt a duplicate re-save). #59
            # Bytes are unlinked only now, after the commit succeeded — a rolled-back save
            # must never destroy a still-referenced file (#63).
            for _rel in _media_orphans:
                media_svc.delete_stored_file(_rel)
            ui.notify("Changes saved.", type="positive")
            if on_saved:
                on_saved()
            _load_specimen(co_id)

        with ui.row().classes("w-full items-center gap-4 px-1"):
            ui.space()
            ui.button("Save changes", icon="save", on_click=_save).classes("btn-save")

        # Baseline for unsaved-changes detection (#47): the editable specimen +
        # collecting-event field VALUES as just loaded. The determination /
        # association sub-cards save immediately on their own, so they are not part
        # of this "Save changes" dirty check.
        #
        # NB: reads field values directly (controlled-vocab fields by NAME via
        # get_value), NOT _collect_co_fields()/commit() — the dirty poll must never
        # open a session or get_or_create a vocab row. FK ids are resolved only on
        # real Save.
        def _current_values() -> dict:
            co = {
                "collection_code":    (coll_code_in.value or "").strip(),
                "individual_count":   int(count_in.value or 1),
                "preparations":       prep_field["get_value"](),
                "life_stage":         stage_sel.value,
                "disposition":        disp_field["get_value"](),
                "basis_of_record":    basis_sel.value,
                "occurrence_remarks": rem_in.value,
                "other_catalog_numbers": othercat_in.value,
                "confidential":       1 if conf_chk.value else 0,
            }
            ev = dict(_collect_ev_fields())
            if ev_ce:
                ev["recorded_by"]       = ev_ce["recby_get"]()
                ev["habitat"]           = ev_ce["habitat_get"]()
                ev["sampling_protocol"] = ev_ce["protocol_get"]()
            return _norm({"co": co, "ev": ev})
        _baseline = _current_values()
        # Staged identifications count as unsaved changes too — the whole point of #54.
        _dirty["fn"] = lambda: (_current_values() != _baseline
                                or id_state["has_changes"]()
                                or _assoc_has_changes()
                                or _sub["ls"]["has_changes"]()
                                or _sub["ext"]["has_changes"]()
                                or _sub["media"]["has_changes"]())

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
            # Event media shares the form's Confidential footer line.
            def _ev_footer():
                _media_btn(session_factory, target_kind="collecting_event",
                           target_id=ev_id, tooltip="Event media")
            ev_ce = build_collecting_event_form(
                session_factory, default_recby_fn=_default_recby,
                footer_slot=_ev_footer)
            ev_ce["load"](ev_snap)
            if shared:
                ev_ce["set_readonly"](
                    True,
                    f"Shared event — {n} specimens use it. Press “Edit all {n}” above to change "
                    f"it; saving changes all of them.",
                )

        def _save_event():
            try:
                with session_factory() as s:
                    with s.begin():
                        event_ids = ev_ce["commit"](s)
                        ev_svc.update_collecting_event(
                            s, ev_id,
                            **event_ids,
                            **ev_ce["collect_fields"](),
                        )
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            # Post-commit cleanup OUTSIDE the try (#59): committed already, so
            # reset the dirty baseline + refresh tabs without risking a spurious
            # "Save failed". Clearing the baseline also drops the unsaved-changes
            # banner that otherwise stayed lit after a successful event save.
            ui.notify(f"Event #{ev_id} saved.", type="positive")
            _ev_baseline["v"] = _ev_values()   # saved → no longer dirty
            if on_saved:
                on_saved()

        with ui.row().classes("w-full items-center gap-4 px-1 mt-2"):
            ui.space()
            save_btn = ui.button("Save event", icon="save", on_click=_save_event).classes("btn-save")
            if shared:
                save_btn.set_enabled(False)   # enabled by the "Edit all" unlock

        # Baseline for unsaved-changes detection (#47), as for the specimen form.
        # Reads field VALUES only (vocab fields by name) — never opens a session.
        # A holder so _save_event can reset it without reloading the form.
        def _ev_values() -> dict:
            return _norm({
                **ev_ce["collect_fields"](),
                "recorded_by":       ev_ce["recby_get"](),
                "habitat":           ev_ce["habitat_get"](),
                "sampling_protocol": ev_ce["protocol_get"](),
            })
        _ev_baseline = {"v": _ev_values()}
        _dirty["fn"] = lambda: _ev_values() != _ev_baseline["v"]

    def _has_content() -> bool:
        return bool(_dirty["fn"]) and _dirty["fn"]()

    return {"open_specimen": _open_specimen, "open_event": _open_event,
            "has_content": _has_content}

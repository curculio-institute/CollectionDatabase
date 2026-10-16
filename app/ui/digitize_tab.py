"""Digitize tab (Specimen Digitization): the specimen / identifications / collecting
event / biological associations cards, the Standard / Visiting / Mounting modes, the
two layouts (normal, single-card stepper), the single Save, and the value-based
unsaved-changes poll.

Moved verbatim out of main.py. `mode_state` and `bio_codes` are the SAME objects
main.py's header (mode switch) and Settings (association filter) mutate in place — do
not copy them. Returns a handle: ``reset_layout()`` applies a layout change from
Settings live.
"""
from __future__ import annotations

from nicegui import ui
import app.services.person_defaults as pd_svc
import app.services as svc
import app.services.identifiers as id_svc
import app.services.repositories as repo_svc
import app.services.persons as persons_svc
from app.config import get_config
import app.services.events as ev_svc
from app.services.label_text import format_event_preview_html
from app.ui.taxon_search import build_taxon_search
from app.ui.choice_field import build_choice_field
from app.ui.identification_list import build_identification_list
from app.ui.mounting_session import build_mounting_session_section
from app.ui.specimen_form import build_specimen_form
from app.ui.collecting_event_form import build_collecting_event_form
from app.ui.event_reuse import build_event_share_banner
import app.ui.record_summary as _record_summary
from app.ui.media_panel import build_media_button
from app.ui.external_id_panel import build_external_id_button
from app.ui.life_stage_panel import build_life_stage_button
from app.ui.annotation_panel import (build_annotation_button,
                                     confirm_staged_on_existing_event)
from app.ui.event_completeness import confirm_incomplete_event
from app.ui.check_panel import build_check_panel
from app.ui.confirm_dialog import choose
import app.services.media as media_svc
import app.services.external_ids as extid_svc
import app.services.annotations as ann_svc
from app.services.biological import check_staged_relationships, get_relationship_options
from app.services.validation import validate_event_fields, missing_event_fields
from app.vocab import IDENTIFICATION_QUALIFIER_OPTIONS


def build_digitize_tab(session_factory, *, refreshers, mode_state, mark_form_clean, bio_codes,
                       on_open_event):
    # Same names the code below used while it lived inline in main.py.
    _sf = session_factory
    _refreshers = refreshers
    _mode_state = mode_state
    _mark_form_clean = mark_form_clean

    def _with_session(fn):
        with _sf() as s:
            return fn(s)

    def _default_recby() -> str | None:
        with _sf() as s:
            return pd_svc.get_defaults(s)[1]

    # ── per-connection state ─────────────────────────────────────
    state = {"event_id": None, "populating": False}
    bio_state: dict = {
        "associations": [],  # list of {rel_id, rel_name, taxon_id, taxon_label}
    }

    def _event_opts() -> dict:
        return _with_session(
            lambda s: {o.id: o.summary
                       for o in svc.search_collecting_events(s, "")}
        )

    # Recent specimens are shown as a compact, fully-visible list (the old
    # 10-column table truncated everything) — last 8, rendered through the SAME
    # shared record-summary row every other browse surface uses (Records picker,
    # Explore): catalog + name (qualifier + italics by rank) on top,
    # place/host/date/leg/det beneath, plus the export badges. Confirmation of what
    # was just saved, not a data grid (browsing/search lives in Records/Explore).
    _RECENT_LIMIT = 8

    def _recent_html() -> str:
        rows = _with_session(lambda s: svc.recent_specimens(s, limit=_RECENT_LIMIT))
        if not rows:
            return '<div class="rc-empty">No specimens yet.</div>'
        cards = [
            _record_summary.specimen_html(
                catalog=id_svc.format_catalog_display(
                    r.collection_code, r.catalog_number),
                name=r.scientific_name or "",
                rank=r.taxon_rank,
                authorship=r.authorship,
                qualifier=r.identification_qualifier,
                hosts=r.hosts,
                sex=r.sex,
                count=r.individual_count,
                locality=r.place,
                event_date=r.event_date,
                recorded_by=r.recorded_by,
                identified_by=r.identified_by,
                date_identified=r.date_identified,
                confidential=r.confidential,
                event_confidential=r.event_confidential,
                recorded_by_state=r.recorded_by_state,
                determination_reasons=r.determination_reasons,
            )
            for r in rows
        ]
        return f'<div class="rc-list">{"".join(cards)}</div>'

    # Form column + check panel side by side, centred as a pair. `tp-dig-root` is what
    # main.py's CSS keys on to let sticky positioning work in this tab panel.
    dig_root = ui.row().classes(
        "tp-dig-root w-full justify-center items-start flex-nowrap gap-4")
    # max-w is set by _apply_digitize_layout (max-w-7xl normal / max-w-5xl mounting /
    # max-w-4xl single-card); never hard-coded here.
    with dig_root:
        dig_container = ui.column().classes("flex-1 min-w-0 px-4 pt-6 pb-16 gap-4")
    with dig_container:

        # ── STEP HEADER (single-card mode only) ──────────────────
        # Clickable step chips; built + highlighted by the layout logic
        # block below. Hidden entirely in normal multi-card layout.
        step_header_row = ui.row().classes(
            "tp-stepper-bar w-full items-center gap-1 mb-1"
        )
        step_header_row.set_visibility(False)

        # ── SPECIMEN + IDENTIFICATION (paired in normal mode) ─────
        # Normal mode lays these two short cards side-by-side (flex-wrap
        # → stack when narrow); single-card mode shows only the current
        # step's card, so the row simply holds one full-width card.
        # Staged specimen-media controllers (one per specimen card; the bytes
        # are stored now and committed to the new specimen on Save). Each card
        # has its own button in its header; a mode switch wipes both.
        spec_media = {}
        spec_media_v = {}
        spec_extid = {}
        spec_extid_v = {}
        spec_ls = {}
        spec_ls_v = {}
        spec_ann = {}
        spec_ann_v = {}
        # Persistent backing lists for the staged footer controllers. The footer is
        # rebuilt whenever the form re-renders (layout toggle, field edits); without a
        # persistent store each rebuild would bind a FRESH empty list and silently drop
        # already-staged files — bytes on disk, no DB row (the media-loss bug). Passing
        # these as staged_store makes staged items survive re-renders; a deliberate wipe
        # still clears them via each handle's clear() (see the mode-switch wipe).
        _sm_items, _sm_items_v = [], []          # specimen media (standard / visiting)
        _se_items, _se_items_v = [], []          # specimen ext-ids
        _sl_items, _sl_items_v = [], []          # specimen life-stage
        _sa_items, _sa_items_v = [], []          # specimen annotations

        def _mk_spec_footer(media_holder, extid_holder, ls_holder, ann_holder,
                            media_store, extid_store, ls_store, ann_store):
            # Staged controllers, rendered bottom-right of the specimen card.
            ls_holder.update(build_life_stage_button(
                _sf, staged=True, staged_store=ls_store))
            ann_holder.update(build_annotation_button(
                _sf, target="specimen", staged=True, staged_store=ann_store,
                tooltip="Specimen annotations (added on Save)"))
            extid_holder.update(build_external_id_button(
                _sf, target_kind="collection_object", staged=True,
                staged_store=extid_store,
                tooltip="Specimen resource identifiers (attached on Save)"))
            media_holder.update(build_media_button(
                _sf, target_kind="collection_object", staged=True,
                staged_store=media_store,
                tooltip="Specimen media (attached on Save)"))

        with ui.row().classes("w-full flex-wrap gap-4 items-start"):
            # Shared specimen-field block (see app/ui/specimen_form.py).
            # Widgets are unpacked into locals so the save/validate/wipe
            # paths below reference them unchanged.
            spec = build_specimen_form(
                _sf, identifier_policy="standard",
                footer_slot=lambda: _mk_spec_footer(
                    spec_media, spec_extid, spec_ls, spec_ann,
                    _sm_items, _se_items, _sl_items, _sa_items))
            specimen_card = spec["card"]
            specimen_card.classes(remove="w-full", add="flex-1 min-w-[360px]")
            # Visiting-collection variant: free-text identity, pure data
            # capture (no reserved code, no print queue). Hidden until the
            # mode toggle selects it; occupies the same slot as the standard
            # card (only one of the two is ever visible).
            spec_visiting = build_specimen_form(
                _sf, identifier_policy="visiting",
                footer_slot=lambda: _mk_spec_footer(
                    spec_media_v, spec_extid_v, spec_ls_v, spec_ann_v,
                    _sm_items_v, _se_items_v, _sl_items_v, _sa_items_v))
            spec_visiting["card"].set_visibility(False)
            spec_visiting["card"].classes(remove="w-full",
                                          add="flex-1 min-w-[360px]")
            # The save/validate/clear paths read from whichever form is active.
            _active_spec = [spec]

            # ── IDENTIFICATION ────────────────────────────────────
            with ui.card().classes("shadow-sm flex-1 min-w-[360px]") \
                    as identification_card:
                with ui.row().classes("items-center gap-2 mb-1 w-full"):
                    ui.label("Identifications").classes("section-label")
                    ui.space()
                    ui.button("Clear", icon="clear",
                              on_click=lambda: det_state["clear"]()) \
                        .props("flat dense no-caps size=sm color=grey") \
                        .tooltip("Clear unsaved identifications")
                ui.separator().classes("mb-3")
                det_state = build_identification_list(_sf)

        def _active_media() -> dict:
            """The staged media controller for the active specimen card."""
            return spec_media_v if _active_spec[0] is spec_visiting else spec_media

        def _active_extid() -> dict:
            """The staged external-id controller for the active specimen card."""
            return spec_extid_v if _active_spec[0] is spec_visiting else spec_extid

        def _active_ls() -> dict:
            """The staged life-stage controller for the active specimen card."""
            return spec_ls_v if _active_spec[0] is spec_visiting else spec_ls

        def _active_ann() -> dict:
            """The staged annotation controller for the active specimen card."""
            return spec_ann_v if _active_spec[0] is spec_visiting else spec_ann

        # ── COLLECTING EVENT ─────────────────────────────────────
        with ui.card().classes("w-full shadow-sm") as event_card:
            with ui.row().classes("items-center gap-3 mb-1 w-full"):
                ui.label("Collecting Event").classes("section-label")
                event_status = ui.html("· new event").classes("event-new")
                ui.space()
                ui.button("Clear", icon="clear",
                          on_click=lambda: _clear_event_card()) \
                    .props("flat dense no-caps size=sm color=grey") \
                    .tooltip("Clear the event selection and fields")

            ui.separator().classes("mb-3")

            event_sel = (
                ui.select(options=_event_opts(), with_input=True,
                           clearable=True, label="Search existing events…")
                .classes("w-full mb-4")
                .tooltip("Type any locality, date, or collector name")
            )
            ui.timer(2.0, lambda: event_sel.set_options(_event_opts()))

            # Reuse banner (orange "shared by N" + Detach-&-copy); populated
            # when an existing event is reused, cleared otherwise.
            event_banner = ui.column().classes("w-full")

            def _on_event_field_edit(_=None):
                if not state["populating"] and state["event_id"] is not None:
                    state["event_id"] = None
                    event_status.set_content("· new event (edited)")
                    event_status.classes(remove="event-linked", add="event-new")

            # Event media (staged; committed to the event on Save). Built
            # into the form's footer so it shares the Confidential line.
            event_media: dict = {}
            event_ann: dict = {}
            _ea_items: list = []
            _em_items: list = []   # persistent staged store (survives form re-renders)

            def _event_footer():
                event_media.update(build_media_button(
                    _sf, target_kind="collecting_event", staged=True,
                    staged_store=_em_items,
                    tooltip="Event media (attached on Save)"))
                event_ann.update(build_annotation_button(
                    _sf, target="event", staged=True, staged_store=_ea_items,
                    tooltip="Event annotations (added on Save — also to a reused event)"))

            ce = build_collecting_event_form(
                _sf,
                default_recby_fn=_default_recby,
                on_field_edit=_on_event_field_edit,
                footer_slot=_event_footer,
            )

            def _refresh_person_opts():
                ce["recby_refresh"]()
                det_state["refresh_person_opts"]()

            _refreshers["person_opts"] = _refresh_person_opts

            def _select_event(eid):
                if eid is None:
                    state["event_id"] = None
                    event_status.set_content("· new event")
                    event_status.classes(remove="event-linked", add="event-new")
                    ce["set_readonly"](False)
                    _hide_reuse_banner()
                    return
                def _load_event(s):
                    ev = svc.get_event(s, eid)
                    if ev is None:
                        return None
                    # Snapshot everything inside the session; `ev` is detached
                    # after _with_session closes (lazy recorded_by_person would
                    # raise DetachedInstanceError). The widget's load() blanks
                    # None and stringifies numerics.
                    snapshot = {
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
                        "decimal_latitude":                 ev.decimal_latitude,
                        "decimal_longitude":                ev.decimal_longitude,
                        "coordinate_uncertainty_in_meters": ev.coordinate_uncertainty_in_meters,
                        "minimum_elevation_in_meters":      ev.minimum_elevation_in_meters,
                        "maximum_elevation_in_meters":      ev.maximum_elevation_in_meters,
                        "habitat":                          ev.habitat_obj.name if ev.habitat_obj else None,
                        "sampling_protocol":                ev.sampling_protocol_obj.name if ev.sampling_protocol_obj else None,
                        "field_number":                     ev.field_number,
                        "verbatim_label":                   ev.verbatim_label,
                        "recorded_by": ev.recorded_by_person.full_name if ev.recorded_by_person else None,
                        "confidential": ev.confidential,
                    }
                    preview = format_event_preview_html(ev)
                    n_shared = ev_svc.count_co_at_event(s, eid)
                    return snapshot, preview, n_shared

                loaded = _with_session(_load_event)
                if loaded is None:
                    return
                snapshot, ev_preview, ev_n = loaded
                state["event_n"] = ev_n
                ce["load"](snapshot)
                state["event_id"] = eid
                event_status.set_content(ev_preview)
                event_status.classes(remove="event-new", add="event-linked")
                ce["set_readonly"](True)
                _show_reuse_banner(eid, ev_n)

            event_sel.on_value_change(lambda e: _select_event(e.value))

            def _use_event(eid: int) -> None:
                """Reuse an existing event (check-panel suggestion). Through the picker
                when it lists the event, so the picker shows what is linked; directly
                otherwise (the picker holds only the most recent events)."""
                if eid in event_sel.options and event_sel.value != eid:
                    event_sel.value = eid       # its change handler links the event
                else:
                    # The picker already shows this id (e.g. after Detach & copy), so
                    # assigning it again fires no change — link directly.
                    _select_event(eid)
                if state["event_id"] != eid:
                    # Deleted or merged away since it was suggested: say so, and drop
                    # the cached suggestion rather than leave a dead "Use" button.
                    ui.notify("That event no longer exists.", type="warning")
                    _refresh_checks()

        # ── BIOLOGICAL ASSOCIATIONS ───────────────────────────────
        with ui.card().classes("w-full shadow-sm") as bio_card:
            with ui.row().classes("items-center gap-2 mb-1 w-full"):
                ui.label("Biological Associations").classes("section-label")
                ui.space()
                ui.button("Clear", icon="clear",
                          on_click=lambda: _clear_bio_card()) \
                    .props("flat dense no-caps size=sm color=grey") \
                    .tooltip("Clear staged associations")
            ui.separator().classes("mb-3")

            # Relationship — the custom-dropdown look of the taxon/person fields
            # (snap-to-first, one keystroke), so it reads as a peer of the taxon
            # field below and isn't overlooked. Names map back to their ids.
            rel_options_list = _with_session(get_relationship_options)
            _rel_name_to_id = {r.name: r.id for r in rel_options_list}
            rel_field = build_choice_field(
                list(_rel_name_to_id.keys()), "Relationship", classes="w-full mb-2")

            def _refresh_rel_options() -> None:
                """Relationships can be created / renamed / merged / deleted in
                Controlled Vocabularies: re-query so this card never offers a stale one."""
                fresh = {r.name: r.id for r in _with_session(get_relationship_options)}
                _rel_name_to_id.clear()
                _rel_name_to_id.update(fresh)
                rel_field["set_options"](list(fresh.keys()))

            ui.timer(2.0, _refresh_rel_options)

            # Object taxon search — bio_codes list is read on each keystroke
            bio_obj_state = build_taxon_search(
                _sf,
                nomenclatural_codes=bio_codes,
                sources=("local", "taxonworks", "wcvp", "datasets"),
                placeholder="Type plant or fungus name…",
            )

            with ui.row().classes("items-center gap-3 mt-3"):
                show_animals_cb = ui.checkbox(
                    "Show animals too",
                    value=False,
                )

                def _on_show_animals(e):
                    if e.value:
                        bio_codes.clear()  # empty = no nomenclatural code filter
                    else:
                        bio_codes.clear()
                        bio_codes.extend(get_config().bio_assoc_default_codes)
                    bio_obj_state["clear"]()

                show_animals_cb.on_value_change(_on_show_animals)

                # The qualifier is small — a compact snap-to-first field beside the
                # checkbox, not a full-width row. Only identification field exposed.
                bio_qual = build_choice_field(
                    IDENTIFICATION_QUALIFIER_OPTIONS, "Qualifier", classes="w-40")

                ui.space()

                def _add_assoc() -> bool:
                    """Stage the association being entered. True iff it was staged."""
                    rel_name = rel_field["get_value"]()
                    rel_id   = _rel_name_to_id.get(rel_name)
                    taxon_id = bio_obj_state["taxon_id"]
                    if not rel_id:
                        ui.notify("Select a relationship first.", type="warning")
                        return False
                    if not taxon_id:
                        ui.notify("Select an associated taxon first.", type="warning")
                        return False
                    if taxon_id == -1:
                        ui.notify("Taxon is still being imported — please wait a moment.", type="warning")
                        return False
                    bio_state["associations"].append({
                        "rel_id":      rel_id,
                        "rel_name":    rel_name,
                        "taxon_id":    taxon_id,
                        "qualifier":   bio_qual["get_value"](),
                        "taxon_label": bio_obj_state["label"],
                        # Per-association staged media + external links; persist
                        # across list re-renders (passed as staged_store) and are
                        # committed to the new association id on Save.
                        "media_items": [],
                        "extid_items": [],
                    })
                    bio_obj_state["clear"]()
                    rel_field["set_value"](None)
                    bio_qual["set_value"](None)
                    _refresh_assoc_list()
                    return True

                def _assoc_pending() -> str | None:
                    """What is entered in the add row but not staged, as display text —
                    None when the row is empty. A relationship or taxon alone counts:
                    half an association is still something the user meant to keep."""
                    rel = rel_field["get_value"]() or ""
                    taxon = bio_obj_state["label"] if bio_obj_state["taxon_id"] else ""
                    if not (rel or taxon):
                        return None
                    return " — ".join(x for x in (rel, taxon) if x)

                (
                    ui.button("Add association", icon="add", on_click=_add_assoc)
                    .props("flat color=secondary")
                )

            assoc_list_col = ui.column().classes("w-full gap-1 mt-3")

            def _refresh_assoc_list():
                assoc_list_col.clear()
                with assoc_list_col:
                    if not bio_state["associations"]:
                        ui.label("No associations added — associations are saved atomically when the specimen is saved.") \
                            .classes("text-sm italic") \
                            .style("color:var(--tp-base-soft)")
                    for i, a in enumerate(bio_state["associations"]):
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.icon("link", size="xs") \
                                .style("color:var(--tp-secondary); opacity:.7")
                            _q = a.get("qualifier")
                            _lbl = f"{_q} {a['taxon_label']}" if _q else a['taxon_label']
                            ui.label(f"{a['rel_name']} — {_lbl}") \
                                .classes("text-sm flex-1")
                            # Per-association staged external link + media
                            # (committed on Save).
                            a.setdefault("media_items", [])
                            a.setdefault("extid_items", [])
                            a.setdefault("annotation_items", [])
                            build_annotation_button(
                                _sf, target="association", staged=True,
                                staged_store=a["annotation_items"],
                                tooltip="Association annotations (on Save; stay local)")
                            build_external_id_button(
                                _sf, target_kind="field_occurrence",
                                staged=True, staged_store=a["extid_items"],
                                tooltip="Observation resource identifier "
                                        "(iNaturalist URL, on Save)")
                            build_media_button(
                                _sf, target_kind="biological_association",
                                staged=True, staged_store=a["media_items"],
                                tooltip="Association media (attached on Save)")
                            (
                                ui.button("", icon="close")
                                .props("flat dense round size=xs")
                                .on_click(lambda _, idx=i: _remove_assoc(idx))
                            )

            def _remove_assoc(idx: int):
                bio_state["associations"].pop(idx)
                _refresh_assoc_list()

            _refresh_assoc_list()

        # ── MOUNTING SESSION SECTION ─────────────────────────────
        # Built here so it appears below Collecting Event + Bio
        # Associations (the sections shared by both modes).
        # Hidden by default; mode toggle controls visibility.
        with ui.column().classes("w-full gap-4") as ms_section:
            ms_state = build_mounting_session_section(
                _sf,
                collect_event_fields=lambda: ce["collect_fields"](),
                commit_event=lambda s: ce["commit"](s),
                commit_event_annotations=lambda s, ev_id: event_ann["commit"](s, ev_id),
                bio_state=bio_state,
                on_saved=lambda: _ms_on_saved(),
                event_id_getter=lambda: state["event_id"],
                recorded_by_getter=lambda: ce["recby_get"](),
                confirm_pending=lambda: _resolve_pending(),
                confirm_event_annotations=lambda ev_id: confirm_staged_on_existing_event(
                    _sf, event_ann, ev_id),
            )
        ms_section.set_visibility(False)

        # ── SAVE BAR ─────────────────────────────────────────────
        with ui.row().classes("w-full items-center gap-4 px-1") as std_save_row:
            keep_event = ui.checkbox("Keep event")
            keep_det   = ui.checkbox("Keep determination")
            ui.space()
            status_lbl = ui.label("").classes("text-sm italic").style("color:var(--tp-base-soft)")
            save_btn   = ui.button("Save specimen", icon="save").classes("btn-save")

        # ── STEP NAV (single-card mode, non-final steps) ─────────
        # Back / Next between cards; shown only in single-card mode and
        # only before the last step (the last step shows the save bar
        # above, whose Save performs the single real commit). Buttons are
        # wired in the layout-logic block below.
        with ui.row().classes("tp-sticky-bar w-full items-center gap-3 px-1") \
                as step_nav_row:
            back_btn = ui.button("Back", icon="chevron_left").props("flat")
            ui.space()
            next_btn = ui.button("Next") \
                .props("icon-right=chevron_right").classes("btn-save")
        step_nav_row.set_visibility(False)

        # ── RECENT SPECIMENS ──────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-2 mb-1"):
                ui.label("Recent Specimens").classes("section-label")
                ui.space()
                ui.button("", icon="refresh", on_click=lambda: _refresh_table()) \
                    .props("flat dense round").tooltip("Refresh")
            recent_box = ui.html(_recent_html()).classes("w-full")

        # ── save / clear logic ────────────────────────────────────

        # The collecting-event fields, registry, collect/clear, and the
        # editable/read-only toggle now live in build_collecting_event_form
        # (ce handle: collect_fields / reset / set_readonly). The tab keeps
        # the event-reuse chrome below.

        # ── Event reuse: read-only fields + Detach-&-copy ──────────────
        # A reused (existing) event is shown read-only; editing a shared
        # event is only possible in Records. "Detach & copy to edit" turns
        # the fields editable as a NEW event for this specimen (clears the
        # link, so save creates a fresh event — the copy).
        def _hide_reuse_banner():
            event_banner.clear()

        def _detach_to_edit():
            state["event_id"] = None
            ce["set_readonly"](False)
            _hide_reuse_banner()
            event_status.set_content("· new event (editable copy)")
            event_status.classes(remove="event-linked", add="event-new")

        def _show_reuse_banner(eid: int, n: int):
            event_banner.clear()
            shared = f" — shared by {n} specimens" if n > 1 else ""
            msg = (f"Reusing event #{eid}{shared}. Fields are read-only; detach a "
                   f"copy to edit here, or edit the original in the Records tab.")
            with event_banner:
                build_event_share_banner(
                    message=msg,
                    actions=[{
                        "label": "Detach & copy to edit",
                        "icon": "fork_right",
                        "on_click": _detach_to_edit,
                        "primary": True,
                    }],
                )

        def _collect_specimen_fields(session) -> dict:
            # session: needed to resolve the preparations controlled-vocab
            # name → preparation_id (get_or_create), like the person fields.
            active = _active_spec[0]
            ident = active["get_identifier_fields"]()
            return {
                "catalog_number":    ident["catalog_number"],
                # Membership is the repository FK (#75): the default collection
                # (standard) or the one picked from the vocabulary (visiting).
                "repository_id":     active["commit_repository"](session),
                "individual_count":  int(active["count_in"].value or 1),
                "preparation_id":    active["prep_field"]["commit"](session),
                "life_stage":        active["stage_sel"].value,
                "disposition_id":    active["disp_field"]["commit"](session),
                "basis_of_record":   active["basis_sel"].value,
                "occurrence_remarks":active["rem_in"].value,
                "other_catalog_numbers": active["othercat_in"].value,
                "confidential":      1 if active["conf_chk"].value else 0,
            }

        def _required_missing() -> list[dict]:
            """The required specimen / identification inputs still missing (standard
            and visiting). ONE list for both readers: Save refuses on the first entry's
            ``error``, the check panel shows every entry's ``text`` — so the panel can
            never promise a save that the validation then refuses, or the reverse."""
            out: list[dict] = []
            active = _active_spec[0]
            ident = active["get_identifier_fields"]()
            if active["policy"] == "visiting":
                # Only the collection is required. A foreign collection's
                # specimen may have no catalog number at all (migration 0072).
                if not (active["repo_field"]["get_value"]() or "").strip():
                    out.append({
                        "group": "Specimen", "key": "spec-collection",
                        "text": "Collection is missing",
                        "error": "Choose the collection this specimen belongs to."})
            else:  # standard
                # Membership derives from the default collection's code
                # (#83); institutionCode is optional metadata on the repo.
                if not ident["collection_code"]:
                    out.append({
                        "group": "Specimen", "key": "spec-default-collection",
                        "text": "No default collection is set (Settings)",
                        "error": "No default collection set. Open Settings to choose one."})
                elif not ident["catalog_number"]:
                    out.append({
                        "group": "Specimen", "key": "spec-identifier",
                        "text": "Identifier code is missing",
                        "error": "Select an identifier code first."})
            if not det_state["get_dets"]():
                out.append({
                    "group": "Identifications", "key": "det-missing",
                    "text": "Identification is missing",
                    "error": "Add at least one identification."})
            return out

        def _validate() -> str | None:
            missing = _required_missing()
            if missing:
                return missing[0]["error"]
            return validate_event_fields(ce["collect_fields"]())

        def _clear_after_save():
            _active_spec[0]["reset"]()
            # Clear bio associations
            bio_state["associations"].clear()
            bio_obj_state["clear"]()
            rel_field["set_value"](None)
            bio_qual["set_value"](None)
            _refresh_assoc_list()
            if not keep_event.value:
                event_sel.value = None
                state["event_id"] = None
                event_status.set_content("· new event")
                event_status.classes(remove="event-linked", add="event-new")
                ce["set_readonly"](False)
                _hide_reuse_banner()
                ce["reset"]()
            if not keep_det.value:
                det_state["clear"]()

        # Per-card "Clear" handlers (header buttons). Each resets only its
        # own card's uncommitted fields — used to discard a typo or a
        # wrong pick without touching the other cards or saving.
        def _clear_event_card():
            event_sel.value = None
            state["event_id"] = None
            event_status.set_content("· new event")
            event_status.classes(remove="event-linked", add="event-new")
            ce["set_readonly"](False)
            _hide_reuse_banner()
            ce["reset"]()

        def _clear_bio_card():
            bio_state["associations"].clear()
            bio_obj_state["clear"]()
            rel_field["set_value"](None)
            bio_qual["set_value"](None)
            _refresh_assoc_list()

        def _has_any_content() -> bool:
            """Aggregate: does the Digitize form hold unsaved data in any
                    card? Drives the mode-switch confirm. Checks the active mode's
                    specimen surface (standard/visiting card or the mounting table)
                    plus the shared identification, event and bio cards."""
            mode = _mode_state["value"]
            if mode == "mounting":
                spec_dirty = ms_state["has_content"]()
            else:
                spec_dirty = _active_spec[0]["has_content"]()
            return (
                spec_dirty
                or det_state["has_content"]()
                or ce["has_content"]()
                or state.get("event_id") is not None
                or bool(bio_state["associations"])
                or bool(rel_field["get_value"]())
                or bool(bio_qual["get_value"]())
                or bool(bio_obj_state["taxon_id"])
                or _active_media()["has_content"]()
                or _active_extid()["has_content"]()
                or _active_ls()["has_content"]()
                or _active_ann()["has_content"]()
                or event_media["has_content"]()
                or event_ann["has_content"]()
                or any(a.get("media_items") or a.get("extid_items")
                       or a.get("annotation_items")
                       for a in bio_state["associations"])
            )

        async def _resolve_pending() -> bool:
            """Stop a Save that would drop a row entered but never added.

            An identification or association sitting in its add row is not part of the
            specimen yet, so Save would discard it without a word. Ask instead: add it
            and save, save without it, or cancel. Returns True iff the save may go on.
            Runs BEFORE validation, so "Add at least one identification" is not shown
            for an identification that is merely waiting to be added.
            """
            pending: list[tuple[str, callable]] = []
            if _mode_state["value"] != "mounting" and det_state["has_pending"]():
                pending.append((f"Identification: {det_state['pending_label']()}",
                                det_state["add_pending"]))
            assoc = _assoc_pending()
            if assoc:
                pending.append((f"Biological association: {assoc}", _add_assoc))
            if not pending:
                return True
            try:
                choice = await choose(
                    title="Entered but not added",
                    body="\n".join(text for text, _ in pending)
                         + "\n\nSaving now would leave this out of the specimen.",
                    options=[("skip", "Save without it", "warning"),
                             ("add", "Add and save", "secondary")],
                )
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return False
            if choice is None:
                return False
            if choice == "add":
                # A row that cannot be added (no relationship yet, taxon still
                # importing) has already said why — stop rather than save without it.
                for _text, add in pending:
                    if not add():
                        return False
            return True

        async def _on_save():
            if not await _resolve_pending():
                return
            err = _validate()
            if err:
                ui.notify(err, type="negative")
                return
            try:
                _with_session(lambda s: check_staged_relationships(
                    s, bio_state["associations"]))
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
                return
            # Staged event annotations + a reused event: ask (the event form is read-only
            # now, so they can no longer be seen or removed there).
            try:
                if not await confirm_staged_on_existing_event(_sf, event_ann,
                                                              state["event_id"]):
                    return
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            # Soft completeness confirm (#173) — coordinates / municipality /
            # date / recordedBy are never schema-required, so this only asks;
            # it never blocks. Checked on every save, including a reused event.
            # Its own try/except: it runs before the save's try block below,
            # so an error here (e.g. a client disconnect mid-dialog) would
            # otherwise escape unhandled instead of reporting like any other
            # save failure.
            try:
                proceed = await confirm_incomplete_event(
                    ce["collect_fields"](), recorded_by=bool(ce["recby_get"]())
                )
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            if not proceed:
                return
            try:
                active = _active_spec[0]
                is_visiting = active["policy"] == "visiting"
                dets = det_state["get_dets"]()
                cur_det  = next((d for d in dets if d["is_current"]), dets[0])
                rest_det = [d for d in dets if d is not cur_det]
                code = active["get_identifier_fields"]()["catalog_number"]
                with _sf() as session:
                    with session.begin():
                        # Determiner names are resolved HERE, in the save
                        # transaction — the identification card no longer creates a
                        # person the moment one is typed, so abandoning the specimen
                        # leaves no stray name in the People list (#60).
                        def _det_person_id(d: dict) -> int | None:
                            if d.get("identified_by_id"):
                                return d["identified_by_id"]
                            name = (d.get("identified_by") or "").strip()
                            if not name:
                                return None
                            return persons_svc.get_or_create_person(
                                session, full_name=name).id

                        event_ids = ce["commit"](session)
                        co = svc.save_specimen_entry(
                            session,
                            taxon_id=cur_det["taxon_id"],
                            event_id=state["event_id"],
                            event_fields={
                                **ce["collect_fields"](),
                                **event_ids,
                            },
                            specimen_fields=_collect_specimen_fields(session),
                            determination_fields={
                                "sex":                      cur_det.get("sex"),
                                "type_status":              cur_det.get("type_status"),
                                "identified_by_id":         _det_person_id(cur_det),
                                "date_identified":          cur_det["date_identified"],
                                "identification_qualifier": cur_det["identification_qualifier"],
                                "identification_remarks":   cur_det["identification_remarks"],
                                "verbatim_identification":  cur_det.get("verbatim_identification"),
                            },
                        )
                        for d in rest_det:
                            svc.create_determination(
                                session,
                                collection_object_id=co.id,
                                taxon_id=d["taxon_id"],
                                sex=d.get("sex"),
                                type_status=d.get("type_status"),
                                identified_by_id=_det_person_id(d),
                                date_identified=d["date_identified"],
                                identification_qualifier=d["identification_qualifier"],
                                identification_remarks=d["identification_remarks"],
                                verbatim_identification=d.get("verbatim_identification"),
                                is_current=0,
                            )
                        saved_id = co.id
                        # Shared finalization seam (see finalize_specimen):
                        # Standard binds the reserved code but queues no
                        # labels — the identifier is pre-printed and pinned
                        # by hand, and the specimen carries its own data
                        # labels. Visiting passes code=None (foreign
                        # catalogNumber, no reserved code). Both still
                        # persist any bio associations atomically.
                        created_assocs = svc.finalize_specimen(
                            session,
                            collection_object_id=co.id,
                            code=None if is_visiting else code,
                            queue_labels=False,
                            associations=bio_state["associations"],
                        )
                        # Attach any media staged during digitize, in the same
                        # transaction → atomic with the save: specimen media to
                        # the new specimen, event media to its event, and each
                        # association's media to its freshly-created row.
                        _active_media()["commit"](session, co.id)
                        _active_extid()["commit"](session, co.id)
                        _active_ls()["commit"](session, co.id)
                        _active_ann()["commit"](session, co.id)
                        if co.collecting_event_id:
                            event_media["commit"](session, co.collecting_event_id)
                            event_ann["commit"](session, co.collecting_event_id)
                        for _assoc, _ba in zip(bio_state["associations"], created_assocs):
                            if _assoc.get("annotation_items"):
                                ann_svc.add_annotations(
                                    session, "association", _ba.id,
                                    [ann_svc.AnnotationItem(i["term_id"], i["value"])
                                     for i in _assoc["annotation_items"]])
                            for _it in _assoc.get("media_items", []):
                                media_svc.attach_stored(
                                    session,
                                    target_kind="biological_association",
                                    target_id=_ba.id, meta=_it["meta"],
                                    caption=_it["caption"] or None,
                                    category=_it["category"],
                                    license=_it["license"] or None,
                                    rights_holder_id=_it["rights_holder_id"],
                                    is_primary=_it["is_primary"],
                                )
                            # The iNaturalist URL / resource identifier belongs to
                            # the observation (the field occurrence), not the
                            # association; media above stays on the association.
                            for _ex in _assoc.get("extid_items", []):
                                extid_svc.add_identifier(
                                    session,
                                    target_kind="field_occurrence",
                                    target_id=_ba.object_field_occurrence_id,
                                    value=_ex["value"],
                                )
                event_sel.set_options(_event_opts())
                spec["refresh_codes"]()
                ui.notify(f"Saved — specimen #{saved_id}  [{id_svc.catalog_label(code)}]",
                          type="positive")
                status_lbl.set_text(f"Last saved: #{saved_id}")
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            spec_media["clear"](); spec_media_v["clear"]()   # staged media committed
            spec_extid["clear"](); spec_extid_v["clear"]()
            spec_ls["clear"](); spec_ls_v["clear"]()
            spec_ann["clear"](); spec_ann_v["clear"](); event_ann["clear"]()
            event_media["clear"]()
            _refresh_table()
            _clear_after_save()
            # In single-card mode, return to the first step for the next
            # specimen (no-op in normal mode).
            _step_idx[0] = 0
            _refresh_card_visibility()
            _mark_form_clean("Specimen Digitization")
            for fn in _refreshers.values():
                fn()

        save_btn.on_click(_on_save)

        # ── Check panel findings ──────────────────────────────────
        # What the panel beside the form shows (see app/ui/check_panel.py). Read from
        # the same sources the Save path uses, so the two cannot disagree:
        # missing_event_fields is the Save-time "event is missing…" dialog's own check.
        _ev_match = {"key": None, "rows": []}

        def _matching_events() -> list[dict]:
            """Existing events at the form's coordinates + date (nothing else compared).
            Queried only when that triple changes; a number still being typed simply
            matches nothing — validation, not this panel, reports a malformed one."""
            f = ce["collect_fields"]()
            try:
                key = (float(f["decimal_latitude"]), float(f["decimal_longitude"]),
                       (f["event_date"] or "").strip())
            except (TypeError, ValueError):
                key = None
            if key is None or not key[2]:
                _ev_match.update(key=None, rows=[])
            elif key != _ev_match["key"]:
                _ev_match.update(key=key, rows=_with_session(
                    lambda s: ev_svc.find_events_at(s, *key)))
            return _ev_match["rows"]

        def _check_findings() -> list[dict]:
            out: list[dict] = []
            if _mode_state["value"] == "mounting":
                # Mounting has no identifier field (codes are reserved at Save) and
                # no add row; its required input is an identification per row.
                for n in ms_state["rows_without_identification"]():
                    out.append({"group": "Identifications", "key": f"ms-det-{n}",
                                "text": f"Row {n} has no identification"})
            else:
                det_pending = det_state["has_pending"]()
                for m in _required_missing():
                    # An identification waiting in the add row is reported as
                    # "entered but not added" (with its Add button), not as missing.
                    if m["key"] == "det-missing" and det_pending:
                        continue
                    out.append({k: m[k] for k in ("group", "key", "text")})
                if det_pending:
                    out.append({
                        "group": "Identifications", "key": "det-pending",
                        "text": f"{det_state['pending_label']()} is entered but not added",
                        "fix": ("Add", det_state["add_pending"]),
                    })
            for label in missing_event_fields(
                    ce["collect_fields"](), recorded_by=bool(ce["recby_get"]())):
                # "Coordinates" is the one plural label.
                verb = "are" if label == "Coordinates" else "is"
                out.append({"group": "Collecting Event", "key": f"ev-missing-{label}",
                            "text": f"{label} {verb} missing"})
            # Suggest only while a NEW event is being entered; a reused event is
            # already one of them.
            if state["event_id"] is None:
                for m in _matching_events():
                    n = m["n_specimens"]
                    out.append({
                        "group": "Existing event at these coordinates and date",
                        "key": f"ev-match-{m['id']}",
                        "text": f"{m['label']} · {n} specimen{'' if n == 1 else 's'}",
                        "on_click": lambda eid=m["id"]: on_open_event(eid),
                        "tooltip": "Examine this event in Records",
                        "fix": ("Use", lambda eid=m["id"]: _use_event(eid)),
                    })
            assoc = _assoc_pending()
            if assoc:
                out.append({
                    "group": "Biological Associations", "key": "assoc-pending",
                    "text": f"{assoc} is entered but not added",
                    "fix": ("Add", _add_assoc),
                })
            return out

        def _refresh_checks():
            # Events may have been created, edited or merged elsewhere: drop the
            # cached match so the next evaluation re-queries.
            _ev_match["key"] = None
            _check["refresh"]()

        _refreshers["digitize_checks"] = _refresh_checks

        def _refresh_table():
            recent_box.set_content(_recent_html())

        def _ms_on_saved():
            event_ann["clear"]()
            event_sel.set_options(_event_opts())
            _refresh_table()
            _mark_form_clean("Specimen Digitization")
            for fn in _refreshers.values():
                fn()

        def _on_mode_toggle(mode):
            is_visiting = mode == "visiting"
            # Standard and Visiting share the identification card, event
            # card, bio card and save bar; only the specimen card swaps.
            # Mounting replaces the specimen + identification section with
            # its own row table. Card visibility (and the single-card
            # stepper) is computed in one place — _refresh_card_visibility.
            _active_spec[0] = spec_visiting if is_visiting else spec
            _step_idx[0] = 0
            _apply_digitize_layout()
            # Full wipe on every toggle to avoid unsaved state leaking
            spec["reset"]()
            spec_visiting["reset"]()
            det_state["clear"]()
            bio_state["associations"].clear()
            bio_obj_state["clear"]()
            rel_field["set_value"](None)
            bio_qual["set_value"](None)
            _refresh_assoc_list()
            spec_media["clear"](); spec_media_v["clear"](); event_media["clear"]()
            spec_extid["clear"](); spec_extid_v["clear"]()
            spec_ls["clear"](); spec_ls_v["clear"]()
            spec_ann["clear"](); spec_ann_v["clear"](); event_ann["clear"]()
            event_sel.value = None
            state["event_id"] = None
            event_status.set_content("· new event")
            event_status.classes(remove="event-linked", add="event-new")
            ce["set_readonly"](False)
            _hide_reuse_banner()
            ce["reset"]()
            ms_state["wipe"]()
            _mark_form_clean("Specimen Digitization")

        # ── Layout: normal multi-card vs single-card stepper ──────
        # One specimen = one Save; the stepper never commits per card,
        # it only changes which card is visible (the real Save stays on
        # the last step). Mounting keeps its own staging layout and
        # ignores the stepper regardless of the config setting.
        _step_idx = [0]
        _STEP_LABELS = ["Specimen", "Identifications",
                        "Collecting Event", "Biological Associations"]

        def _step_cards():
            first = (spec_visiting["card"]
                     if _mode_state["value"] == "visiting" else specimen_card)
            return [first, identification_card, event_card, bio_card]

        # Build the step chips once; _refresh_step_chips toggles `active`.
        _step_chip_els: list = []
        with step_header_row:
            for _i, _lbl in enumerate(_STEP_LABELS):
                if _i:
                    ui.label("›").classes("tp-step-sep")
                _chip = ui.element("div").classes("tp-step-chip") \
                    .on("click", lambda _e, idx=_i: _go_to_step(idx))
                with _chip:
                    ui.label(str(_i + 1)).classes("tp-step-num")
                    ui.label(_lbl)
                _step_chip_els.append(_chip)

        def _refresh_card_visibility():
            mode = _mode_state["value"]
            is_ms       = mode == "mounting"
            is_standard = mode == "standard"
            is_visiting = mode == "visiting"
            single = (get_config().digitize_layout == "single_card"
                      and not is_ms)
            cards = _step_cards()
            _step_idx[0] = max(0, min(_step_idx[0], len(cards) - 1))
            cur_card = cards[_step_idx[0]]
            # Base visibility from create mode; in single-card mode a base-
            # visible card is only shown when it is the current step.
            base = {
                specimen_card:          is_standard,
                spec_visiting["card"]:  is_visiting,
                identification_card:    not is_ms,
                event_card:             True,
                bio_card:               True,
            }
            for card, vis in base.items():
                card.set_visibility(vis and (not single or card is cur_card))
            ms_section.set_visibility(is_ms)
            last = _step_idx[0] == len(cards) - 1
            step_header_row.set_visibility(single)
            step_nav_row.set_visibility(single and not last)
            std_save_row.set_visibility((not is_ms) and (not single or last))
            # Single-card: the nav row / Save bar stay anchored to the bottom of the
            # window, so a long card (Collecting Event) never scrolls them away.
            (std_save_row.classes(add="tp-sticky-bar") if single
             else std_save_row.classes(remove="tp-sticky-bar"))
            _sync_next_label()
            for i, chip in enumerate(_step_chip_els):
                (chip.classes(add="active") if i == _step_idx[0]
                 else chip.classes(remove="active"))
            back_btn.set_enabled(_step_idx[0] > 0)

        def _next_adds_identification() -> bool:
            return (_step_cards()[_step_idx[0]] is identification_card
                    and det_state["has_pending"]())

        _next_label = ["Next"]

        def _sync_next_label():
            label = ("Add identification & Next" if _next_adds_identification()
                     else "Next")
            if label != _next_label[0]:
                _next_label[0] = label
                next_btn.set_text(label)

        def _apply_digitize_layout():
            single = (get_config().digitize_layout == "single_card"
                      and _mode_state["value"] != "mounting")
            # Mounting is a row table plus the event card — it needs no more than 5xl.
            width = ("max-w-4xl" if single
                     else "max-w-5xl" if _mode_state["value"] == "mounting"
                     else "max-w-7xl")
            dig_container.classes(remove="max-w-7xl max-w-5xl max-w-4xl")
            dig_container.classes(add=width)
            _refresh_card_visibility()

        def _go_to_step(i: int):
            _step_idx[0] = max(0, min(i, len(_step_cards()) - 1))
            _refresh_card_visibility()

        def _step_nav(delta: int):
            if (get_config().digitize_layout != "single_card"
                    or _mode_state["value"] == "mounting"):
                return
            _go_to_step(_step_idx[0] + delta)

        def _on_next():
            # On the Identifications step, Next first adds the identification being
            # entered — the step most often left by forgetting "Add identification".
            # A failed add (taxon still importing) keeps the user on the step.
            if _next_adds_identification() and not det_state["add_pending"]():
                return
            _step_nav(1)

        back_btn.on_click(lambda: _step_nav(-1))
        next_btn.on_click(_on_next)
        # Value-based, like the unsaved-changes poll: picking a taxon fires no event
        # this tab sees.
        ui.timer(0.5, _sync_next_label)
        ui.on("tp-step-nav", lambda e: _step_nav(int(e.args)))

        # Apply the configured layout now (also re-applied on mode switch
        # and after saving the Settings dialog).
        _apply_digitize_layout()

        _mode_state["handler"] = _on_mode_toggle
        _mode_state["has_content"] = _has_any_content

        # State-based unsaved-changes detection for Digitize: poll the
        # real field values (not DOM events) so map/push-pin/geocode fills
        # are seen too. Push to the banner only when the state flips.
        _dig_dirty = [False]

        def _sync_dig_dirty():
            cur = _has_any_content()
            if cur != _dig_dirty[0]:
                _dig_dirty[0] = cur
                ui.run_javascript(
                    "window.tpSetScope && window.tpSetScope("
                    f"'Specimen Digitization', {'true' if cur else 'false'})"
                )

        ui.timer(1.0, _sync_dig_dirty)

    with dig_root:
        _check = build_check_panel(findings_fn=_check_findings)

    def _reset_layout():
        """Re-apply the configured layout from its first step (Settings changed it)."""
        _step_idx[0] = 0
        _apply_digitize_layout()

    return {"reset_layout": _reset_layout}

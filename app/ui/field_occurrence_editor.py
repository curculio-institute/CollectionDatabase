"""Field-occurrence editor — the full-edit escape hatch (decided 2026-07-11).

Data entry only ever surfaces the qualifier on an association; this modal lets the user
edit the *whole* HumanObservation and its determination when there is a need — taxon,
qualifier, identifiedBy, dateIdentified, typeStatus, plus basisOfRecord / individualCount /
sex / lifeStage / remarks / confidential. Opened from an association row in Records.
"""
from __future__ import annotations

from nicegui import ui

import app.services.field_occurrence as fo_svc
from app.config import get_config
from app.models import FieldOccurrence
from app.services.taxa import format_scientific_name
from app.vocab import IDENTIFICATION_QUALIFIER_OPTIONS, SEX_OPTIONS
from app.ui.taxon_search import build_taxon_search
from app.ui.person_field import build_person_field
from app.ui.choice_field import build_choice_field

# basisOfRecord for an observation — the field_occurrence CHECK set (migration 0059).
_FO_BASIS_OPTIONS = ["HumanObservation", "MachineObservation"]


def open_field_occurrence_editor(session_factory, fo_id: int, on_saved=None) -> None:
    """Open a modal to fully edit the field occurrence `fo_id` and its determination."""
    with session_factory() as s:
        fo = s.get(FieldOccurrence, fo_id)
        if fo is None:
            ui.notify("Observation not found.", type="negative")
            return
        det = fo_svc.current_determination(s, fo)
        cur = {
            "basis_of_record": fo.basis_of_record,
            "individual_count": fo.individual_count,
            "sex": fo.sex or "",
            "life_stage": fo.life_stage or "",
            "occurrence_remarks": fo.occurrence_remarks or "",
            "confidential": bool(fo.confidential),
            "taxon_id": det.taxon_id if det else None,
            "taxon_label": format_scientific_name(det.taxon) if det and det.taxon else "",
            "qualifier": (det.identification_qualifier if det else "") or "",
            "identified_by": (det.identified_by_person.full_name
                              if det and det.identified_by_person else None),
            "date_identified": (det.date_identified if det else "") or "",
            "type_status": (det.type_status if det else "") or "",
        }
        bio_codes = list(get_config().bio_assoc_default_codes)

    dlg = ui.dialog()
    with dlg, ui.card().classes("w-[560px] max-w-full gap-2"):
        ui.label("Edit observation (field occurrence)").classes("text-base font-semibold")
        ui.label("The host / sighting recorded as its own HumanObservation. Data entry only "
                 "sets the qualifier; everything here is editable when needed.") \
            .classes("text-xs").style("color:var(--tp-base-soft)")

        ui.label("Identification").classes("section-label mt-2")
        ts = build_taxon_search(
            session_factory,
            nomenclatural_codes=bio_codes,
            sources=("local", "taxonworks", "wcvp"),
            placeholder="Taxon…",
            initial_taxon_id=cur["taxon_id"],
            initial_label=cur["taxon_label"],
        )
        with ui.row().classes("w-full gap-2"):
            qual_field = build_choice_field(
                IDENTIFICATION_QUALIFIER_OPTIONS, "Qualifier",
                initial_value=cur["qualifier"] or None, classes="flex-1")
            type_in = ui.input("typeStatus", value=cur["type_status"]).classes("flex-1")
        with ui.row().classes("w-full gap-2 items-end"):
            idby = build_person_field(session_factory, "identifiedBy",
                                      initial_value=cur["identified_by"], classes="flex-1")
            date_in = ui.input("dateIdentified", value=cur["date_identified"]).classes("flex-1")

        ui.label("Observation").classes("section-label mt-2")
        with ui.row().classes("w-full gap-2"):
            basis_sel = ui.select(_FO_BASIS_OPTIONS, value=cur["basis_of_record"],
                                  label="basisOfRecord").classes("flex-1")
            count_in = ui.number("individualCount", value=cur["individual_count"],
                                 min=0, precision=0).classes("w-32")
        with ui.row().classes("w-full gap-2"):
            sex_sel = ui.select(SEX_OPTIONS, value=cur["sex"], label="sex").classes("flex-1")
            stage_in = ui.input("lifeStage", value=cur["life_stage"]).classes("flex-1")
        remarks_in = ui.textarea("occurrenceRemarks", value=cur["occurrence_remarks"]) \
            .classes("w-full")
        conf_chk = ui.checkbox("Confidential (withhold from export)", value=cur["confidential"])

        def _save():
            tid = ts["taxon_id"]
            if tid == -1:
                ui.notify("Taxon is still importing — wait a moment.", type="warning")
                return
            try:
                with session_factory() as s:
                    with s.begin():
                        det_fields = {
                            "identification_qualifier": qual_field["get_value"](),
                            "identified_by_id": idby["commit"](s),
                            "date_identified": (date_in.value or "").strip() or None,
                            "type_status": (type_in.value or "").strip() or None,
                        }
                        if tid:
                            det_fields["taxon_id"] = tid
                        fo_svc.update_field_occurrence(
                            s, fo_id,
                            fo_fields={
                                "basis_of_record": basis_sel.value,
                                "individual_count": 1 if count_in.value is None
                                                    else int(count_in.value),
                                "sex": sex_sel.value or None,
                                "life_stage": (stage_in.value or "").strip() or None,
                                "occurrence_remarks": (remarks_in.value or "").strip() or None,
                                "confidential": 1 if conf_chk.value else 0,
                            },
                            det_fields=det_fields,
                        )
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            ui.notify("Observation saved.", type="positive")
            dlg.close()
            if on_saved:
                on_saved()

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Abort", on_click=dlg.close).props("flat")
            ui.button("Save & close", icon="save", on_click=_save).classes("btn-save")

    # Delete the dialog when it closes, per the timer-leak rule.
    dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
    dlg.open()

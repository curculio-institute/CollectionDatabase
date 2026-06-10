"""Specimen form section — shared specimen-field block for create/edit modes.

Renders the "Specimen" card (identifier, count, preparations, lifeStage,
disposition, basisOfRecord, institution/collection code, remarks) used by the
Digitize tab and (later) the Records edit tab and the visiting-collection mode.

``identifier_policy`` controls how the identifier block behaves — it is the only
thing that differs between modes:

  "standard" — catalog_number picked from the reserved-codes dropdown;
               institution/collection code locked to config (display-only).
               Used for normal digitizing where correct numbering is enforced.
  "visiting" — (future) free-text identifier + collection code, any value;
               for digitizing specimens at other collections/museums.
  "edit"     — (future) catalog_number read-only (immutable join key);
               collection_code editable (a specimen can be re-homed on gifting).

The builder is UI-only: it renders fields and exposes the widgets plus a few
helpers (``refresh_codes``, ``reset``).  Saving — code assignment, print-queue
enqueue, DB writes — stays in the calling tab, because create and edit have
genuinely different save paths.
"""
from __future__ import annotations

from nicegui import ui

from app.config import get_config
import app.services.identifiers as id_svc

LIFE_STAGE_OPTIONS  = ["adult", "larva", "pupa", "egg", ""]
BASIS_OPTIONS       = ["PreservedSpecimen", "FossilSpecimen", "LivingSpecimen",
                       "HumanObservation", "MachineObservation"]
DISPOSITION_OPTIONS = ["in collection", "on loan", "donated",
                       "exchanged", "missing", "destroyed", ""]


def build_specimen_form(
    session_factory,
    *,
    identifier_policy: str = "standard",
) -> dict:
    """Render the Specimen card. Returns a handle dict.

    Handle keys:
      card             — the ui.card element (for visibility toggling)
      cat_num, count_in, preps_in, stage_sel, disp_sel, basis_sel,
      inst_code_disp, coll_code_disp, rem_in   — the field widgets
      refresh_codes()  — re-query reserved-code options into cat_num
      reset()          — clear to create-mode defaults
    """
    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    def _reserved_opts() -> dict:
        return _with_session(id_svc.reserved_codes)

    with ui.card().classes("w-full shadow-sm") as card:
        ui.label("Specimen").classes("section-label")
        ui.separator().classes("mb-3")

        with ui.row().classes("w-full flex-wrap gap-3 items-end"):
            cat_num = ui.select(
                options={c: c for c in _reserved_opts()},
                with_input=True,
                clearable=True,
                label="identifier *",
            ).classes("w-32")
            count_in = ui.number("n", value=1, min=0, precision=0).classes("w-20")
            preps_in = ui.input("preparations", placeholder="pinned, in ethanol…").classes("flex-1 min-w-40")
        ui.timer(2.0, lambda: cat_num.set_options({c: c for c in _reserved_opts()}))

        with ui.expansion("More fields").classes("w-full mt-2"):
            with ui.grid(columns=4).classes("w-full gap-3"):
                stage_sel = ui.select(LIFE_STAGE_OPTIONS, label="lifeStage", value="adult").classes("col-span-1")
                disp_sel  = ui.select(DISPOSITION_OPTIONS, label="disposition",
                                       value="in collection").classes("col-span-1")
                basis_sel = ui.select(BASIS_OPTIONS, label="basisOfRecord",
                                       value="PreservedSpecimen").classes("col-span-1")
                _cfg_disp = get_config()
                inst_code_disp = (
                    ui.input("institutionCode", value=_cfg_disp.institution_code)
                    .props("readonly outlined dense")
                    .classes("col-span-1")
                    .tooltip("Set in Settings — applies to every new record")
                )
                coll_code_disp = (
                    ui.input("collectionCode", value=_cfg_disp.collection_code)
                    .props("readonly outlined dense")
                    .classes("col-span-1")
                    .tooltip("Set in Settings — applies to every new record")
                )
            rem_in = ui.input("materialEntityRemarks").classes("w-full mt-3")

        def _refresh_identity_display():
            cfg = get_config()
            inst_code_disp.value = cfg.institution_code
            coll_code_disp.value = cfg.collection_code
        ui.timer(2.0, _refresh_identity_display)

    def refresh_codes() -> None:
        cat_num.set_options({c: c for c in _reserved_opts()})

    def reset() -> None:
        cat_num.value   = None
        count_in.value  = 1
        preps_in.value  = ""
        stage_sel.value = "adult"
        disp_sel.value  = "in collection"
        basis_sel.value = "PreservedSpecimen"
        rem_in.value    = ""

    return {
        "card":           card,
        "cat_num":        cat_num,
        "count_in":       count_in,
        "preps_in":       preps_in,
        "stage_sel":      stage_sel,
        "disp_sel":       disp_sel,
        "basis_sel":      basis_sel,
        "inst_code_disp": inst_code_disp,
        "coll_code_disp": coll_code_disp,
        "rem_in":         rem_in,
        "refresh_codes":  refresh_codes,
        "reset":          reset,
    }

"""Specimen form section — shared specimen-field block across modes.

Renders the "Specimen" card (identifier, count, preparations, lifeStage,
disposition, basisOfRecord, institution/collection code, remarks) used by the
Digitize tab (standard + visiting modes) and the Records edit tab.

``identifier_policy`` controls how the identifier block behaves — it is the only
thing that differs between modes:

  "standard" — catalog_number picked from the reserved-codes dropdown;
               institution/collection code locked to config (display-only).
               Used for normal digitizing where correct numbering is enforced.
  "visiting" — catalog_number, collection_code and institution_code are all
               free-text (any value), shown prominently in the top row; for
               digitizing specimens held at other collections/museums. Pure
               data capture — the caller does not reserve codes or print labels.
  "edit"     — catalog_number/collection_code shown read-only in a header label
               (``identity_label``); other fields seeded from ``initial`` (empty
               DB values stay empty — no create-defaults applied).

The builder is UI-only: it renders fields and exposes the widgets plus helpers
(``get_identity``, ``refresh_codes``, ``reset``).  Saving — code assignment,
print-queue enqueue, DB writes — stays in the calling tab, because the modes
have genuinely different save paths.
"""
from __future__ import annotations

from nicegui import ui

from app.config import get_config
import app.services.identifiers as id_svc
from app.vocab import (
    LIFE_STAGE_OPTIONS, BASIS_OPTIONS, DISPOSITION_OPTIONS, NEW_SPECIMEN_DEFAULTS,
)


def build_specimen_form(
    session_factory,
    *,
    identifier_policy: str = "standard",
    initial: dict | None = None,
    identity_label: str | None = None,
) -> dict:
    """Render the Specimen card. Returns a handle dict.

    See module docstring for the three ``identifier_policy`` values.

    initial:        edit-mode snapshot with keys individual_count, preparations,
                    life_stage, disposition, basis_of_record, occurrence_remarks,
                    and (for get_identity) catalog_number, collection_code.
    identity_label: edit-mode read-only header text, e.g. "#12  Jilg ab12".

    Handle keys:
      card             — the ui.card element (for visibility toggling)
      policy           — the identifier_policy string
      cat_num, count_in, preps_in, stage_sel, disp_sel, basis_sel,
      inst_code_disp, coll_code_disp, rem_in   — the field widgets.
                        In edit mode cat_num/inst_code_disp/coll_code_disp are None.
                        In standard mode inst/coll are read-only config displays;
                        in visiting mode they are editable free-text inputs.
      get_identity()   — {catalog_number, collection_code, institution_code} for
                         the save path (config-backed in standard, typed in
                         visiting, snapshot in edit).
      refresh_codes()  — re-query reserved-code options into cat_num (standard only)
      reset()          — clear to create-mode defaults (standard/visiting)
    """
    is_edit     = identifier_policy == "edit"
    is_visiting = identifier_policy == "visiting"
    is_standard = identifier_policy == "standard"
    init = initial or {}

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    def _reserved_opts() -> dict:
        return _with_session(id_svc.reserved_codes)

    # Field seed values.  Edit mode mirrors the record (empty stays empty);
    # standard/visiting apply create defaults.
    if is_edit:
        v_count = init.get("individual_count") or 1
        v_preps = init.get("preparations") or ""
        v_stage = init.get("life_stage")          # None -> shown empty
        v_disp  = init.get("disposition")
        v_basis = init.get("basis_of_record")
        v_rem   = init.get("occurrence_remarks") or ""
    else:
        v_count, v_preps = NEW_SPECIMEN_DEFAULTS["individual_count"], ""
        v_stage = NEW_SPECIMEN_DEFAULTS["life_stage"]
        v_disp  = NEW_SPECIMEN_DEFAULTS["disposition"]
        v_basis = NEW_SPECIMEN_DEFAULTS["basis_of_record"]
        v_rem = ""

    # Defaults; reassigned per policy below.
    cat_num = inst_code_disp = coll_code_disp = None

    with ui.card().classes("w-full shadow-sm") as card:
        with ui.row().classes("items-center gap-2 mb-1"):
            ui.label("Specimen").classes("section-label")
            if is_edit and identity_label:
                ui.label(identity_label).classes("text-sm font-mono") \
                    .style("color:var(--tp-base-soft)")
            elif is_visiting:
                ui.label("· visiting — free-form identifier") \
                    .classes("text-sm").style("color:var(--tp-base-soft)")
        ui.separator().classes("mb-3")

        if is_visiting:
            # All three identity fields are required free-text; show them up top.
            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                cat_num = ui.input("catalogNumber *", placeholder="host number").classes("w-40")
                coll_code_disp = ui.input("collectionCode *", placeholder="host namespace").classes("w-40")
                inst_code_disp = ui.input("institutionCode *", placeholder="host institution").classes("w-40")
            with ui.row().classes("w-full flex-wrap gap-3 items-end mt-2"):
                count_in = ui.number("n", value=v_count, min=0, precision=0).classes("w-20")
                preps_in = ui.input(
                    "preparations", value=v_preps, placeholder="pinned, in ethanol…"
                ).classes("flex-1 min-w-40")
        else:
            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                if is_standard:
                    cat_num = ui.select(
                        options={c: c for c in _reserved_opts()},
                        with_input=True,
                        clearable=True,
                        label="identifier *",
                    ).classes("w-32")
                count_in = ui.number("n", value=v_count, min=0, precision=0).classes("w-20")
                preps_in = ui.input(
                    "preparations", value=v_preps, placeholder="pinned, in ethanol…"
                ).classes("flex-1 min-w-40")
            if is_standard:
                # Skip the DB read while the card is hidden (Mounting / Visiting mode).
                def _refresh_code_opts():
                    if card.visible:
                        cat_num.set_options({c: c for c in _reserved_opts()})
                ui.timer(2.0, _refresh_code_opts)

        with ui.expansion("More fields").classes("w-full mt-2"):
            with ui.grid(columns=4).classes("w-full gap-3"):
                stage_sel = ui.select(LIFE_STAGE_OPTIONS, label="lifeStage", value=v_stage).classes("col-span-1")
                disp_sel  = ui.select(DISPOSITION_OPTIONS, label="disposition", value=v_disp).classes("col-span-1")
                basis_sel = ui.select(BASIS_OPTIONS, label="basisOfRecord", value=v_basis).classes("col-span-1")
                if is_standard:
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
                elif is_edit:
                    # collectionCode is editable: a specimen may be re-homed to
                    # another collection when gifted. catalog_number stays
                    # immutable (shown read-only in the header).
                    coll_code_disp = (
                        ui.input("collectionCode", value=init.get("collection_code") or "")
                        .props("dense")
                        .classes("col-span-1")
                        .tooltip("Change only when re-homing this specimen to "
                                 "another collection (gifting). catalogNumber is fixed.")
                    )
            rem_in = ui.input("materialEntityRemarks", value=v_rem).classes("w-full mt-3")

        if is_standard:
            def _refresh_identity_display():
                if not card.visible:   # hidden in Mounting / Visiting mode
                    return
                cfg = get_config()
                inst_code_disp.value = cfg.institution_code
                coll_code_disp.value = cfg.collection_code
            ui.timer(2.0, _refresh_identity_display)

    def get_identity() -> dict:
        """Identity triplet for the save path: catalog_number / collection_code /
        institution_code.  Config-backed in standard, typed in visiting, snapshot
        in edit."""
        if is_visiting:
            return {
                "catalog_number":   (cat_num.value or "").strip(),
                "collection_code":  (coll_code_disp.value or "").strip(),
                "institution_code": (inst_code_disp.value or "").strip(),
            }
        if is_edit:
            return {
                "catalog_number":   init.get("catalog_number") or "",
                # collection_code is editable in edit mode (gifting); read live.
                "collection_code":  (coll_code_disp.value or "").strip()
                                    if coll_code_disp is not None
                                    else (init.get("collection_code") or ""),
                "institution_code": "",
            }
        cfg = get_config()  # standard
        return {
            "catalog_number":   cat_num.value or "",
            "collection_code":  cfg.collection_code,
            "institution_code": cfg.institution_code,
        }

    def refresh_codes() -> None:
        if is_standard:
            cat_num.set_options({c: c for c in _reserved_opts()})

    def reset() -> None:
        if cat_num is not None:
            cat_num.value = "" if is_visiting else None
        if is_visiting:
            coll_code_disp.value = ""
            inst_code_disp.value = ""
        count_in.value  = NEW_SPECIMEN_DEFAULTS["individual_count"]
        preps_in.value  = ""
        stage_sel.value = NEW_SPECIMEN_DEFAULTS["life_stage"]
        disp_sel.value  = NEW_SPECIMEN_DEFAULTS["disposition"]
        basis_sel.value = NEW_SPECIMEN_DEFAULTS["basis_of_record"]
        rem_in.value    = ""

    return {
        "card":           card,
        "policy":         identifier_policy,
        "cat_num":        cat_num,
        "count_in":       count_in,
        "preps_in":       preps_in,
        "stage_sel":      stage_sel,
        "disp_sel":       disp_sel,
        "basis_sel":      basis_sel,
        "inst_code_disp": inst_code_disp,
        "coll_code_disp": coll_code_disp,
        "rem_in":         rem_in,
        "get_identity":   get_identity,
        "refresh_codes":  refresh_codes,
        "reset":          reset,
    }

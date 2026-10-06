"""Specimen form section — shared specimen-field block across modes.

Renders the "Specimen" card (identifier, count, preparations, lifeStage,
disposition, basisOfRecord, institution/collection code, remarks) used by the
Digitize tab (standard + visiting modes) and the Records edit tab.

``identifier_policy`` controls how the identifier block behaves — it is the only
thing that differs between modes:

  "standard" — catalog_number picked from the reserved-codes dropdown;
               institution/collection code locked to config (display-only).
               Used for normal digitizing where correct numbering is enforced.
  "visiting" — the holding collection is PICKED from the Collections vocabulary
               (or added by name) and catalog_number is an OPTIONAL free-text field
               — a foreign collection's specimen may have no number at all
               (migration 0072). For digitizing specimens held elsewhere. Pure
               data capture — the caller does not reserve codes or print labels.
  "edit"     — catalog_number shown read-only in a header label
               (``identity_label``) — unless the specimen has none yet, in which
               case it can be entered ONCE; the collection is the same picker
               (re-homing). Other fields seeded from ``initial`` (empty DB values
               stay empty — no create-defaults applied).

The builder is UI-only: it renders fields and exposes the widgets plus helpers
(``get_identifier_fields``, ``refresh_codes``, ``reset``).  Saving — code
assignment, print-queue enqueue, DB writes — stays in the calling tab, because
the modes have genuinely different save paths.
"""
from __future__ import annotations

from nicegui import ui

import app.services.identifiers as id_svc
import app.services.repositories as repo_svc
from app.services.vocabularies import preparation_vocab, disposition_vocab
from app.ui.repository_field import build_repository_field
from app.ui.vocab_field import build_vocab_field
from app.vocab import (
    LIFE_STAGE_OPTIONS, BASIS_OPTIONS, NEW_SPECIMEN_DEFAULTS,
)


def build_specimen_form(
    session_factory,
    *,
    identifier_policy: str = "standard",
    initial: dict | None = None,
    identity_label: str | None = None,
    footer_slot=None,
) -> dict:
    """Render the Specimen card. Returns a handle dict.

    See module docstring for the three ``identifier_policy`` values.

    initial:        edit-mode snapshot with keys individual_count, preparations,
                    life_stage, disposition, basis_of_record, occurrence_remarks,
                    collection_name (seeds the collection picker) and
                    catalog_number (None → the fill-once catalogNumber input).
    identity_label: edit-mode read-only header text, e.g. "#12  Doe ab12".

    Handle keys:
      card             — the ui.card element (for visibility toggling)
      policy           — the identifier_policy string
      cat_num, count_in, stage_sel, disp_field, basis_sel,
      inst_code_disp, coll_code_disp, rem_in   — the field widgets.
      prep_field       — the preparations controlled-vocab field handle (a dict
                        with get_value/set_value/commit; commit(session)→preparation_id).
                        inst/coll_code_disp exist only in standard mode (read-only
                        echoes of the default collection). cat_num is None in edit
                        mode unless the specimen has no catalog number yet.
      repo_field       — the collection picker handle (visiting + edit; None in
                        standard, where the default collection applies).
      get_identifier_fields()
                       — {catalog_number, collection_code, institution_code} of a
                         NEW specimen (the codes are filled in standard mode only).
                         RAISES in edit mode — see the function.
      commit_repository(session)
                       — the repository_id to store: the default collection
                         (standard) or the picked/added one (visiting, edit).
                         Raises ValueError when there is none.
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

    def _default_repo_codes() -> tuple[str, str]:
        """(collection_code, institution_code) of the flagged default collection, or
        ('', '') if none is set (#83). Standard mode stamps a new specimen with the
        default repository; an empty code makes the caller fail loud rather than stub one."""
        with session_factory() as s:
            r = repo_svc.get_default(s)
            return (r.collection_code, r.institution_code or "") if r else ("", "")

    # Field seed values.  Edit mode mirrors the record (empty stays empty);
    # standard/visiting apply create defaults.
    if is_edit:
        v_count = init.get("individual_count") or 1
        v_preps = init.get("preparations") or ""
        v_stage = init.get("life_stage")          # None -> shown empty
        v_disp  = init.get("disposition")
        v_basis = init.get("basis_of_record")
        v_rem   = init.get("occurrence_remarks") or ""
        v_other = init.get("other_catalog_numbers") or ""
    else:
        v_count = NEW_SPECIMEN_DEFAULTS["individual_count"]
        # preparations pre-fills with the flagged Tier-1 default preparation (empty if
        # none is flagged) — data-driven via the vocab (migration 0052), editable.
        with session_factory() as _s:
            v_preps = preparation_vocab.get_default_name(_s) or ""
        v_stage = NEW_SPECIMEN_DEFAULTS["life_stage"]
        v_disp  = None   # disposition starts empty; set manually or in bulk (Batch tools)
        v_basis = NEW_SPECIMEN_DEFAULTS["basis_of_record"]
        v_rem = ""
        v_other = ""

    # Defaults; reassigned per policy below.
    cat_num = inst_code_disp = coll_code_disp = repo_field = None
    # Last reserved-code set pushed to the identifier select. The live-refresh
    # (timer) and refresh_codes() both re-push only when this CHANGES — see A4
    # note on refresh_codes().
    _last_codes: list[str] | None = None

    with ui.card().classes("w-full shadow-sm") as card:
        with ui.row().classes("items-center gap-2 mb-1 w-full"):
            ui.label("Specimen").classes("section-label")
            if is_edit and identity_label:
                ui.label(identity_label).classes("text-sm font-mono") \
                    .style("color:var(--tp-base-soft)")
            elif is_visiting:
                ui.label("· held in another collection") \
                    .classes("text-sm").style("color:var(--tp-base-soft)")
            # Clear button: only in create modes (edit mode shows an existing
            # record — there is no "uncommitted" content to discard).
            if not is_edit:
                ui.space()
                ui.button("Clear", icon="clear", on_click=lambda: reset()) \
                    .props("flat dense no-caps size=sm color=grey") \
                    .tooltip("Clear this card's unsaved fields")
        ui.separator().classes("mb-3")

        if is_visiting:
            # Identity up top: which collection holds it (picked from the vocabulary,
            # or added by name), and its catalog number there — if it has one.
            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                repo_field = build_repository_field(
                    session_factory, "Collection *", exclude_default=True,
                    classes="flex-1 min-w-56")
                cat_num = (
                    ui.input("catalogNumber", placeholder="leave empty if it has none")
                    .classes("w-56")
                    .tooltip("The number this specimen carries in that collection. "
                             "Optional — a specimen without one is recorded without "
                             "one, but cannot be exported to TaxonWorks."))
            with ui.row().classes("w-full flex-wrap gap-3 items-end mt-2"):
                count_in = ui.number("n", value=v_count, min=0, precision=0).classes("w-20")
                prep_field = build_vocab_field(
                    session_factory, preparation_vocab, "preparations",
                    initial_value=v_preps or None, classes="flex-1 min-w-40",
                )
        else:
            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                if is_standard:
                    _last_codes = list(_reserved_opts())
                    cat_num = ui.select(
                        options={c: c for c in _last_codes},
                        with_input=True,
                        clearable=True,
                        label="identifier *",
                    ).classes("w-48")   # wide enough for a full code, e.g. JJPC-00304
                count_in = ui.number("n", value=v_count, min=0, precision=0).classes("w-20")
                prep_field = build_vocab_field(
                    session_factory, preparation_vocab, "preparations",
                    initial_value=v_preps or None, classes="flex-1 min-w-40",
                )
            if is_standard:
                # Skip the DB read while the card is hidden (Mounting / Visiting mode);
                # refresh_codes() itself only re-pushes when the set changed (A4).
                def _refresh_code_opts():
                    if card.visible:
                        refresh_codes()
                ui.timer(2.0, _refresh_code_opts)

        with ui.expansion("More fields").classes("w-full mt-2"):
            with ui.grid(columns=4).classes("w-full gap-3"):
                stage_sel = ui.select(LIFE_STAGE_OPTIONS, label="lifeStage", value=v_stage).classes("col-span-1")
                # disposition is an open controlled vocab (#76) — same custom-dropdown
                # UX as preparations; commit(session) → disposition_id at save time.
                disp_field = build_vocab_field(
                    session_factory, disposition_vocab, "disposition",
                    initial_value=v_disp or None, classes="col-span-1",
                )
                basis_sel = ui.select(BASIS_OPTIONS, label="basisOfRecord", value=v_basis).classes("col-span-1")
                if is_standard:
                    _coll0, _inst0 = _default_repo_codes()
                    inst_code_disp = (
                        ui.input("institutionCode", value=_inst0)
                        .props("readonly outlined dense")
                        .classes("col-span-1")
                        .tooltip("The default collection (set in Settings) — "
                                 "applies to every new record")
                    )
                    coll_code_disp = (
                        ui.input("collectionCode", value=_coll0)
                        .props("readonly outlined dense")
                        .classes("col-span-1")
                        .tooltip("The default collection (set in Settings) — "
                                 "applies to every new record")
                    )
                elif is_edit:
                    # The collection is editable: a specimen may be re-homed to
                    # another collection when gifted. catalog_number stays
                    # immutable (shown read-only in the header).
                    repo_field = build_repository_field(
                        session_factory, "Collection",
                        initial_value=init.get("collection_name") or None,
                        classes="col-span-2")
                    repo_field["element"].tooltip(
                        "Change only when re-homing this specimen to another "
                        "collection (gifting). Its catalogNumber travels with it.")
                    if not init.get("catalog_number"):
                        # Fill-once: a specimen saved without a catalog number may be
                        # given one; from then on it is immutable like any other.
                        cat_num = (
                            ui.input("catalogNumber",
                                     placeholder="none yet — can be set once")
                            .props("dense").classes("col-span-2")
                            .tooltip("This specimen has no catalog number. Enter one "
                                     "here if it has since been given one; after "
                                     "saving it can no longer be changed."))
            othercat_in = (
                ui.input("otherCatalogNumbers", value=v_other)
                .classes("w-full mt-3")
                .tooltip("Catalog numbers this specimen carried at previous owning "
                         "collections (free text). Its own catalogNumber is fixed.")
            )
            rem_in = ui.input("materialEntityRemarks", value=v_rem).classes("w-full mt-3")

        # Footer: the Confidential flag (left) shares one line with the caller's
        # widgets (media / external-id / life-stage buttons, right) to save vertical
        # space. A confidential specimen is dropped entirely from the DwC export.
        with ui.row().classes("w-full items-center justify-between mt-2"):
            conf_chk = (
                ui.checkbox(
                    "Confidential",
                    value=(bool(init.get("confidential")) if is_edit else False),
                )
                .props("dense")
                .tooltip("Withhold from public export — a confidential specimen is "
                         "dropped entirely from the DwC export (TaxonWorks). "
                         "Local-only flag.")
            )
            if footer_slot is not None:
                with ui.row().classes("items-center gap-1"):
                    footer_slot()

        if is_standard:
            def _refresh_identity_display():
                if not card.visible:   # hidden in Mounting / Visiting mode
                    return
                coll, inst = _default_repo_codes()
                inst_code_disp.value = inst
                coll_code_disp.value = coll
            ui.timer(2.0, _refresh_identity_display)

    def get_identifier_fields() -> dict:
        """The identifier of a new specimen: catalog_number, plus the default
        collection's collection_code / institution_code in standard mode (visiting
        leaves the codes empty — its collection is ``commit_repository``'s business,
        and its catalog_number may be empty).

        This is the specimen *identifier* (catalog number), not its identification
        (taxon determination), and it is a value to write — never a row locator.

        Edit mode has no triplet: an existing row is located by its primary key,
        catalog_number is immutable, and institution_code is not edited. Asking
        here is a programming error, so it RAISES rather than returning a blank
        institution_code that a caller could silently save (a loud failure beats a
        silent wrong value — CLAUDE.md §2).
        """
        if is_edit:
            raise RuntimeError(
                "edit mode has no identifier triplet — the row is saved by id, "
                "catalog_number is immutable; read coll_code_disp directly instead."
            )
        if is_visiting:
            return {
                "catalog_number":   (cat_num.value or "").strip(),
                "collection_code":  "",
                "institution_code": "",
            }
        coll, inst = _default_repo_codes()  # standard: stamp with the default collection
        return {
            "catalog_number":   cat_num.value or "",
            "collection_code":  coll,
            "institution_code": inst,
        }

    def commit_repository(session) -> int:
        """The repository_id this specimen belongs to. Standard: the flagged default
        collection. Visiting / edit: the picked collection, created by name if new.
        Call inside the save transaction. Never guesses — no collection, no save."""
        if is_standard:
            repo = repo_svc.get_default(session)
            if repo is None:
                raise ValueError("No default collection set. Open Settings to choose one.")
            return repo.id
        repo_id = repo_field["commit"](session)
        if repo_id is None:
            raise ValueError("Choose the collection this specimen belongs to.")
        return repo_id

    def refresh_codes() -> None:
        """Re-query reserved codes into the identifier select — but push new options
        only when the set actually CHANGED. A4: calling set_options every timer tick
        resets Quasar's in-progress client-side filter, clobbering the
        type→arrow-keys→enter→tab selection workflow. Reserved codes rarely change
        mid-session, so the filter stays put while the user types."""
        nonlocal _last_codes
        if not is_standard:
            return
        codes = list(_reserved_opts())
        if codes != _last_codes:
            _last_codes = codes
            cat_num.set_options({c: c for c in codes})

    def has_content() -> bool:
        """True if the user has entered uncommitted data in this card.

        Edit mode never reports content (it mirrors an existing record, not new
        data). Defaulted dropdowns (lifeStage/disposition/basisOfRecord) are not
        counted — only fields the user actually fills: identifier, preparations,
        remarks, a non-default count, and (visiting) the free-text identity codes.
        """
        if is_edit:
            return False
        if cat_num is not None and (cat_num.value or ""):
            return True
        # preparations is now a Tier-1 default (the flagged preparation, seeded as
        # v_preps) — count it only when the user changed it away from that default,
        # so a freshly loaded form doesn't falsely report unsaved changes.
        if (prep_field["get_value"]() or "").strip() != (v_preps or "").strip():
            return True
        if (rem_in.value or "").strip() or (othercat_in.value or "").strip():
            return True
        try:
            if int(count_in.value or 1) != NEW_SPECIMEN_DEFAULTS["individual_count"]:
                return True
        except (TypeError, ValueError):
            pass
        if is_visiting and (repo_field["get_value"]() or "").strip():
            return True
        if conf_chk.value:
            return True
        return False

    def reset() -> None:
        if cat_num is not None:
            cat_num.value = "" if is_visiting else None
        if is_visiting:
            repo_field["set_value"](None)
        count_in.value  = NEW_SPECIMEN_DEFAULTS["individual_count"]
        with session_factory() as _s:
            prep_field["set_value"](preparation_vocab.get_default_name(_s) or None)
        stage_sel.value = NEW_SPECIMEN_DEFAULTS["life_stage"]
        disp_field["set_value"](None)   # disposition starts empty; set manually
        basis_sel.value = NEW_SPECIMEN_DEFAULTS["basis_of_record"]
        rem_in.value      = ""
        othercat_in.value = ""
        conf_chk.value    = False

    return {
        "card":           card,
        "policy":         identifier_policy,
        "cat_num":        cat_num,
        "count_in":       count_in,
        "prep_field":     prep_field,
        "stage_sel":      stage_sel,
        "disp_field":     disp_field,
        "basis_sel":      basis_sel,
        "inst_code_disp": inst_code_disp,
        "coll_code_disp": coll_code_disp,
        "repo_field":     repo_field,
        "commit_repository": commit_repository,
        "rem_in":         rem_in,
        "othercat_in":    othercat_in,
        "conf_chk":       conf_chk,
        "get_identifier_fields": get_identifier_fields,
        "refresh_codes":  refresh_codes,
        "reset":          reset,
        "has_content":    has_content,
    }

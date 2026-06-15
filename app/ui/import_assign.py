"""Import & Assign tab.

Workflow:
  1. User uploads a DwC CSV → held in per-connection state, never bulk-written.
  2. User types any text to search the in-memory rows (date, locality, taxon, …).
  3. User clicks a row → full preview shown.
  4. Taxon resolved: local DB → TaxonWorks autocomplete → manual entry.
  5. User fills per-specimen fields (identifier, sex, n, preps) and reviews.
  6. Validation + "Save & Assign" → creates event + collection_object + determination.
"""
from __future__ import annotations

import asyncio

from nicegui import ui

import app.services.dwc_import as dwc_svc
import app.services.taxonworks as tw_svc
import app.services.taxa as taxa_svc
import app.services.identifiers as id_svc
import app.services as svc
from app.config import get_config
import app.services.person_defaults as pd_svc
from app.ui.taxon_search import build_taxon_search
from app.ui.date_input import attach_date_validation
from app.ui.person_field import build_person_field
from app.ui.type_status_field import build_type_status_field
# Controlled vocabularies — single source of truth (app/ui/vocab.py).
from app.ui.vocab import SEX_OPTIONS, LIFE_STAGE_OPTIONS

# ---------------------------------------------------------------------------
# Example CSV — downloadable from the upload card
# ---------------------------------------------------------------------------

_EXAMPLE_CSV = (
    "scientificName,genus,specificEpithet,scientificNameAuthorship,family,"
    "eventDate,recordedBy,country,countryCode,stateProvince,county,locality,"
    "decimalLatitude,decimalLongitude,coordinateUncertaintyInMeters,"
    "minimumElevationInMeters,maximumElevationInMeters,habitat,samplingProtocol,"
    "sex,individualCount,preparations,identifiedBy,dateIdentified,materialEntityRemarks\n"
    "Otiorhynchus sulcatus,Otiorhynchus,sulcatus,\"(Fabricius, 1775)\",Curculionidae,"
    "2024-06-15,J. Jilg,Germany,DE,Bavaria,Berchtesgadener Land,"
    "\"Berchtesgaden, Königssee trail\","
    "47.5976,13.0055,50,620,,broadleaf forest edge,hand collecting,"
    "female,3,pinned,J. Jilg,2024-07-01,\n"
    "Curculio nucum,Curculio,nucum,\"Linnaeus, 1758\",Curculionidae,"
    "2024-05-20,J. Jilg,Austria,AT,Styria,,\"Grazer Bergland, Schöckel\","
    "47.1833,15.4667,100,1250,,Fagus-Quercus forest,beating,"
    ",1,pinned,J. Jilg,2024-06-10,reared from hazel nuts\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field_row(label: str, value: str) -> None:
    """Render one key–value pair in the preview grid."""
    if not value:
        return
    with ui.row().classes("gap-2 items-baseline"):
        ui.label(label).classes("text-xs font-medium w-28 shrink-0") \
          .style("color:var(--tp-base-soft)")
        ui.label(value).classes("text-sm")


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_import_assign_tab(session_factory, refreshers: dict) -> None:
    """Build the Import & Assign tab in the current NiceGUI context."""

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    def _default_idby() -> str | None:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[0]

    # ── per-connection state ────────────────────────────────────────────
    state: dict = {
        "rows":       [],       # parsed DwC rows
        "filename":   "",
        "selected":   None,     # currently selected row dict
        "taxon_id":   None,     # resolved local taxon id
    }

    with ui.column().classes("w-full max-w-5xl mx-auto px-4 pt-6 pb-16 gap-4"):

        # ================================================================
        # CARD 1 — Upload
        # ================================================================
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-3 mb-2"):
                ui.label("Spreadsheet").classes("section-label")
                ui.space()
                ui.button("Download example CSV", icon="download") \
                    .props("flat dense size=sm") \
                    .on_click(lambda: ui.download(
                        _EXAMPLE_CSV.encode("utf-8"),
                        filename="dwc_example.csv",
                        media_type="text/csv",
                    )) \
                    .tooltip("Download a two-row sample showing expected column names")

            ui.label(
                "Upload a Darwin Core CSV. Columns are matched by name "
                "(case-insensitive, underscores and spaces ignored). "
                "The file is held in memory for this session only."
            ).classes("text-sm mb-3").style("color:var(--tp-base-soft)")

            upload_status = ui.label("No file loaded.").classes("text-sm italic") \
                .style("color:var(--tp-base-soft)")

            def _on_upload(e):
                try:
                    rows = dwc_svc.parse_csv(e.content.read())
                except Exception as exc:
                    ui.notify(f"Could not parse file: {exc}", type="negative")
                    return
                state["rows"]     = rows
                state["filename"] = e.name
                state["selected"] = None
                state["taxon_id"] = None
                upload_status.set_text(
                    f"✓  {len(rows)} row{'s' if len(rows) != 1 else ''} loaded "
                    f"from {e.name}"
                )
                upload_status.style("color:var(--tp-secondary)")
                _refresh_search("")
                search_card.set_visibility(True)
                assign_card.set_visibility(False)

            ui.upload(
                label="Choose CSV…",
                on_upload=_on_upload,
                auto_upload=True,
            ).props("accept=.csv,text/csv flat").classes("mt-2")

        # ================================================================
        # CARD 2 — Search rows
        # ================================================================
        search_card = ui.card().classes("w-full shadow-sm")
        search_card.set_visibility(False)

        with search_card:
            ui.label("Find record").classes("section-label mb-3")

            search_inp = (
                ui.input(placeholder="Type date, locality, taxon, collector…")
                .classes("w-full mb-3")
                .props("clearable outlined dense")
            )

            results_col = ui.column().classes("w-full gap-1")
            results_status = ui.label("").classes("text-xs italic mt-1") \
                .style("color:var(--tp-base-soft)")

        def _refresh_search(term: str):
            hits = dwc_svc.search_rows(state["rows"], term)
            results_col.clear()
            results_status.set_text(
                f"{len(hits)} match{'es' if len(hits) != 1 else ''}"
                + (" (showing first 100)" if len(hits) == 100 else "")
            )
            with results_col:
                for row in hits:
                    summary = dwc_svc.row_summary(row)
                    btn = (
                        ui.button(summary)
                        .props("flat no-caps align=left")
                        .classes("w-full text-left text-sm")
                        .style("justify-content:flex-start; font-size:.82rem; "
                               "padding:4px 8px; border-radius:4px; "
                               "color:var(--tp-base-content)")
                    )
                    btn.on_click(lambda _, r=row: _select_row(r))

        search_inp.on_value_change(lambda e: _refresh_search(e.value or ""))

        # ================================================================
        # CARD 3 — Preview & Assign
        # ================================================================
        assign_card = ui.card().classes("w-full shadow-sm")
        assign_card.set_visibility(False)

        with assign_card:
            ui.label("Preview & assign").classes("section-label mb-3")

            # ── Event data preview (read-only) ──────────────────────────
            with ui.expansion("Event data", value=True).classes("w-full mb-2"):
                event_preview = ui.column().classes("w-full gap-0 pl-2")

            # ── Taxon resolution ────────────────────────────────────────
            with ui.card().classes("w-full shadow-sm mb-2").style(
                "border-left:3px solid var(--tp-secondary) !important"
            ):
                taxon_header = ui.label("Taxon").classes("section-label mb-2")
                taxon_section = ui.column().classes("w-full gap-2")

            # ── Per-specimen fields ─────────────────────────────────────
            ui.separator().classes("my-2")
            ui.label("Specimen").classes("section-label mb-2")

            def _reserved_opts() -> dict:
                return _with_session(id_svc.reserved_codes)

            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                cat_num = ui.select(
                    options={c: c for c in _reserved_opts()},
                    with_input=True,
                    clearable=True,
                    label="identifier *",
                ).classes("w-32")
                sex_sel   = ui.select(SEX_OPTIONS, label="sex").classes("w-28")
                count_in  = ui.number("n", value=1, min=0, precision=0).classes("w-20")
                preps_in  = ui.input("preparations",
                                     placeholder="pinned, in ethanol…").classes("flex-1 min-w-40")
            ui.timer(2.0, lambda: cat_num.set_options({c: c for c in _reserved_opts()}))

            with ui.row().classes("w-full flex-wrap gap-3 items-end mt-3"):
                stage_sel = ui.select(LIFE_STAGE_OPTIONS, label="lifeStage", value="adult").classes("w-32")
                rem_in    = ui.input("materialEntityRemarks").classes("flex-1 min-w-40")

            # ── Determination meta ──────────────────────────────────────
            ui.separator().classes("my-2")
            ui.label("Determination").classes("section-label mb-2")
            with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
                    id_by_state = build_person_field(
                        session_factory, "identifiedBy",
                        default_fn=_default_idby,
                    )
                dt_id  = ui.input("dateIdentified",
                                  placeholder="YYYY-MM-DD").classes("w-36")
                attach_date_validation(dt_id, no_future=True)
                type_sel = build_type_status_field(classes="w-36")
                qual   = ui.input("qualifier",
                                  placeholder="cf. / aff.").classes("w-28")

            # ── Save bar ────────────────────────────────────────────────
            ui.separator().classes("my-3")
            with ui.row().classes("w-full items-center gap-4"):
                assign_status = ui.label("").classes("text-sm italic flex-1") \
                    .style("color:var(--tp-base-soft)")
                assign_btn = ui.button("Save & assign", icon="save") \
                    .classes("btn-save")

        # ================================================================
        # Logic: select a row
        # ================================================================

        def _select_row(row: dict):
            state["selected"] = row
            state["taxon_id"] = None
            assign_card.set_visibility(True)
            assign_status.set_text("")

            # Fill event preview
            ev = dwc_svc.row_to_event_fields(row)
            event_preview.clear()
            with event_preview:
                _field_row("Country",    ev["country"])
                _field_row("State",      ev["state_province"])
                _field_row("County",     ev["county"])
                _field_row("Locality",   ev["locality"] or ev["verbatim_locality"])
                _field_row("Date",       ev["event_date"] or ev["verbatim_event_date"])
                _field_row("Collector",  ev["recorded_by"])
                lat = ev["decimal_latitude"]
                lon = ev["decimal_longitude"]
                if lat and lon:
                    _field_row("Coords", f"{lat}, {lon}")
                _field_row("Elevation",  ev["minimum_elevation_in_meters"])
                _field_row("Habitat",    ev["habitat"])
                _field_row("Protocol",   ev["sampling_protocol"])

            # Pre-fill per-specimen fields from spreadsheet
            sp = dwc_svc.row_to_specimen_prefill(row)
            count_in.value  = int(sp["individual_count"] or 1)
            preps_in.value  = sp["preparations"]
            stage_sel.value = sp["life_stage"] or "adult"
            rem_in.value    = sp["occurrence_remarks"]

            # Pre-fill determination meta
            det = dwc_svc.row_to_determination_fields(row)
            sex_sel.value = det["sex"]
            type_sel["set_value"](det.get("type_status") or None)
            id_by_state["set_value"](det["identified_by"] or None)
            dt_id.value = det["date_identified"]

            # Refresh identifier dropdown
            cat_num.options = {c: c for c in _reserved_opts()}
            cat_num.update()
            cat_num.value = None

            # Resolve taxon
            asyncio.ensure_future(_resolve_taxon(row))

        # ================================================================
        # Logic: taxon resolution
        # ================================================================

        async def _resolve_taxon(row: dict):
            name = dwc_svc.row_scientific_name(row)
            taxon_section.clear()

            if not name:
                with taxon_section:
                    ui.label("No scientificName in this row — select manually below.") \
                      .classes("text-sm italic").style("color:var(--tp-base-soft)")
                    _build_tw_search(taxon_section, row)
                return

            taxon_header.set_text(f"Taxon — {name}")

            # 1. Check local DB
            local = _with_session(lambda s: taxa_svc.find_taxon_by_name(s, name))
            if local:
                state["taxon_id"] = local.id
                with taxon_section:
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("check_circle", size="sm").style("color:#16a34a")
                        ui.label(taxa_svc.format_scientific_name(local)) \
                          .classes("text-sm italic")
                        ui.label("resolved locally") \
                          .classes("text-xs").style("color:var(--tp-base-soft)")
                return

            # 2. Search TaxonWorks
            with taxon_section:
                searching_lbl = ui.label(f'Searching TaxonWorks for \"{name}\"…') \
                    .classes("text-sm italic").style("color:var(--tp-base-soft)")

            try:
                results = await tw_svc.search_taxon_names(name, limit=8)
            except Exception:
                results = []

            taxon_section.clear()

            if results:
                with taxon_section:
                    ui.label("Not found locally. Select from TaxonWorks:") \
                      .classes("text-xs mb-1").style("color:var(--tp-base-soft)")
                    _build_tw_results(taxon_section, results)
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.label("or").classes("text-xs").style("color:var(--tp-base-soft)")
                        ui.button("Add manually", icon="add").props("flat dense size=sm") \
                          .on_click(lambda: _show_manual_form(taxon_section, row))
            else:
                with taxon_section:
                    with ui.row().classes("items-center gap-2 mb-2"):
                        ui.icon("warning", size="sm").style("color:#d97706")
                        ui.label(f'"{name}" not found in TaxonWorks.') \
                          .classes("text-sm")
                    _build_manual_form(taxon_section, row)

        def _build_tw_search(container, row: dict):
            """Embed the standard TW search widget (for rows with no scientificName)."""
            with container:
                tw_state = build_taxon_search(
                    session_factory,
                    on_select=lambda tid: _set_taxon(tid),
                )

        def _build_tw_results(container, results: list[dict]):
            """Show clickable TaxonWorks autocomplete results."""
            with container:
                for r in results:
                    lhtml = r.get("label_html") or r.get("label", "")
                    item = ui.element("div").classes("tw-result tw-dropdown-item") \
                        .style("padding:6px 10px; cursor:pointer; border-radius:4px; "
                               "border:1px solid var(--tp-base-border); margin-bottom:3px;")
                    with item:
                        ui.html(lhtml)
                    item.on("click", lambda _, r=r: asyncio.ensure_future(_import_tw(r)))

        async def _import_tw(r: dict):
            tw_id = r["id"]  # import the actual name clicked; get_or_create handles valid-name backfill for synonyms
            try:
                tw_data, otu_id = await asyncio.gather(
                    tw_svc.fetch_full_classification(tw_id),
                    tw_svc.fetch_otu_id_for_taxon_name(tw_id),
                )
            except Exception as exc:
                ui.notify(f"TaxonWorks fetch failed: {exc}", type="negative")
                return
            if tw_data is None:
                ui.notify("Taxon not found in TaxonWorks.", type="warning")
                return
            mismatches: list[str] = []
            try:
                with session_factory() as session:
                    with session.begin():
                        taxon = taxa_svc.get_or_create_from_tw_data(
                            session, tw_data, otu_id=otu_id, mismatches=mismatches
                        )
                        tid = taxon.id
            except Exception as exc:
                ui.notify(f"DB error: {exc}", type="negative")
                return
            _set_taxon(tid)
            for msg in mismatches:
                ui.notify(f"Taxonomy mismatch: {msg}", type="warning", timeout=8000)
            taxon_section.clear()
            with taxon_section:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", size="sm").style("color:#16a34a")
                    with session_factory() as s:
                        t = s.get(taxa_svc.Taxon, tid)
                        label = taxa_svc.format_scientific_name(t) if t else f"taxon #{tid}"
                    ui.label(label).classes("text-sm italic")
                    ui.label("imported from TaxonWorks") \
                      .classes("text-xs").style("color:var(--tp-base-soft)")

        def _show_manual_form(container, row: dict):
            container.clear()
            with container:
                _build_manual_form(container, row)

        def _build_manual_form(container, row: dict):
            """Inline form for adding a taxon not found anywhere."""
            with container:
                ui.label("Add taxon manually:") \
                  .classes("text-xs font-medium mb-1").style("color:var(--tp-base-soft)")
                with ui.grid(columns=3).classes("w-full gap-2"):
                    g_in  = ui.input("Genus *",
                                     value=row.get("genus") or "").classes("col-span-1")
                    sp_in = ui.input("specificEpithet",
                                     value=row.get("specificEpithet") or "").classes("col-span-1")
                    au_in = ui.input("authorship",
                                     value=row.get("scientificNameAuthorship") or "").classes("col-span-1")
                with ui.grid(columns=3).classes("w-full gap-2 mt-1"):
                    fam_in = ui.input("family",
                                      value=row.get("family") or "").classes("col-span-1")
                    sfam_in = ui.input("subfamily",
                                       value=row.get("subfamily") or "").classes("col-span-1")
                    sg_in  = ui.input("subgenus",
                                      value=row.get("subgenus") or "").classes("col-span-1")
                ui.label("This taxon will be added without a TaxonWorks link.") \
                  .classes("text-xs mt-1").style("color:var(--tp-base-soft)")

                def _add_manual():
                    genus = g_in.value.strip()
                    if not genus:
                        ui.notify("Genus is required.", type="warning")
                        return
                    try:
                        with session_factory() as session:
                            with session.begin():
                                t = taxa_svc.create_taxon_manual(
                                    session,
                                    genus=genus,
                                    specific_epithet=sp_in.value.strip() or None,
                                    scientific_name_authorship=au_in.value.strip() or None,
                                    family=fam_in.value.strip() or None,
                                    subfamily=sfam_in.value.strip() or None,
                                    subgenus=sg_in.value.strip() or None,
                                )
                                tid = t.id
                    except Exception as exc:
                        ui.notify(f"DB error: {exc}", type="negative")
                        return
                    _set_taxon(tid)
                    container.clear()
                    with container:
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("check_circle", size="sm").style("color:#16a34a")
                            ui.label(f"{genus} {sp_in.value}".strip()).classes("text-sm italic")
                            ui.label("added manually") \
                              .classes("text-xs").style("color:var(--tp-base-soft)")
                    if "taxonomy_stats" in refreshers:
                        refreshers["taxonomy_stats"]()

                ui.button("Add taxon", icon="add").props("color=secondary dense").classes("mt-2") \
                  .on_click(_add_manual)

        def _set_taxon(tid: int):
            state["taxon_id"] = tid

        # ================================================================
        # Logic: validate + save
        # ================================================================

        def _validate() -> str | None:
            if state["taxon_id"] is None:
                return "Resolve the taxon before saving."
            if not cat_num.value:
                return "Select an identifier code."
            if state["selected"] is None:
                return "No record selected."
            ev = dwc_svc.row_to_event_fields(state["selected"])
            cc = ev["country_code"].strip()
            if cc and len(cc) != 2:
                return "countryCode must be exactly 2 characters."
            for label, val, lo, hi in [
                ("latitude",  ev["decimal_latitude"],  -90,  90),
                ("longitude", ev["decimal_longitude"], -180, 180),
            ]:
                if val:
                    try:
                        f = float(val)
                        if not (lo <= f <= hi):
                            return f"{label} out of range [{lo}, {hi}]."
                    except ValueError:
                        return f"{label} must be a number."
            unc = ev["coordinate_uncertainty_in_meters"]
            if unc:
                try:
                    if float(unc) < 0:
                        return "coordinateUncertainty must be ≥ 0."
                except ValueError:
                    return "coordinateUncertainty must be a number."
            return None

        def _on_assign():
            err = _validate()
            if err:
                ui.notify(err, type="negative")
                return
            row  = state["selected"]
            code = cat_num.value
            try:
                with session_factory() as session:
                    with session.begin():
                        idby_id = id_by_state["commit"](session)
                        co = svc.save_specimen_entry(
                            session,
                            taxon_id=state["taxon_id"],
                            event_id=None,
                            event_fields=dwc_svc.row_to_event_fields(row),
                            specimen_fields={
                                "catalog_number":    code,
                                "collection_code":   get_config().collection_code,
                                "institution_code":  get_config().institution_code,
                                "individual_count":  int(count_in.value or 1),
                                "preparations":      preps_in.value,
                                "life_stage":        stage_sel.value,
                                "basis_of_record":   "PreservedSpecimen",
                                "occurrence_remarks":rem_in.value,
                            },
                            determination_fields={
                                "sex":                      sex_sel.value or None,
                                "type_status":              type_sel["get_value"]() or None,
                                "identified_by_id":         idby_id,
                                "date_identified":          dt_id.value,
                                "identification_qualifier": qual.value,
                                "verbatim_identification":  dwc_svc.row_scientific_name(row),
                            },
                        )
                        # Retroactive digitisation: the specimen already carries
                        # its own data + identification labels and the identifier
                        # is pre-printed, so bind the code but queue no labels
                        # (same policy as Digitize standard; see finalize_specimen).
                        svc.finalize_specimen(
                            session,
                            collection_object_id=co.id,
                            code=code,
                            queue_labels=False,
                        )
                        saved_id = co.id
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return

            ui.notify(f"Saved — specimen #{saved_id}  [{code}]", type="positive")
            assign_status.set_text(f"✓ Saved as #{saved_id}")

            # Reset for next specimen
            cat_num.options = {c: c for c in _reserved_opts()}
            cat_num.update()
            cat_num.value  = None
            state["taxon_id"] = None
            assign_card.set_visibility(False)

            for fn in refreshers.values():
                fn()

        assign_btn.on_click(_on_assign)

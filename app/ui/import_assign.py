"""Import & Assign tab.

Retroactive digitisation (Workflow 1) is a *rapid* loop over large batches (~1700
specimens): the CSV row already carries every datum, so per specimen the user only:
  1. finds the matching reference row (one autocomplete selector),
  2. glances at a condensed read-only summary to confirm it's the beetle in hand,
  3. stamps the pre-printed identifier (+ a couple of quick specimen fields),
  4. Save → next.

Everything else (event fields, determination meta, lifeStage, remarks) is saved
straight from the CSV, never shown — see `_on_assign`. The taxon auto-resolves in the
background (local DB → TaxonWorks → installed name datasets, the same order the taxon-search
widget uses) and only surfaces a picker when resolution fails. A user-added checklist is
exactly the source that knows the names TaxonWorks does not, so the loop must consult it —
otherwise the user is sent to "Add manually" for a name the database can already resolve.

Design decision (2026-07-07): the tab is deliberately condensed to this fast path
rather than a full editable form — the reference table is the source of the data; the
user's job is confirm-and-stamp. See CLAUDE.md Workflow 1.
"""
from __future__ import annotations

import asyncio

from nicegui import ui

import app.services.dwc_import as dwc_svc
from app.services.dates import parse_dwc_date
import app.services.taxonworks as tw_svc
import app.services.taxa as taxa_svc
import app.services.identifiers as id_svc
import app.services as svc
import app.services.repositories as repo_svc
import app.services.persons as persons_svc
import app.services.biological as bio_svc
import app.services.name_source as ns_svc
import app.services.datasets as ds_svc
from app.services.biological import get_relationship_options
from app.config import get_config
from app.ui.date_input import attach_date_validation
from app.ui.taxon_search import build_taxon_search, _render_tw_label
from app.ui.taxon_editor import open_new_taxon_dialog
from app.ui.choice_field import build_choice_field
from app.vocab import IDENTIFICATION_QUALIFIER_OPTIONS
from app.ui.vocab_field import build_vocab_field
from app.services.vocabularies import (
    preparation_vocab, habitat_vocab, sampling_protocol_vocab,
)
# Controlled vocabularies — single source of truth (app/vocab.py).
from app.vocab import SEX_OPTIONS, NEW_SPECIMEN_DEFAULTS, IDENTIFICATION_QUALIFIERS

# ---------------------------------------------------------------------------
# Example CSV — downloadable from the upload card
# ---------------------------------------------------------------------------

_EXAMPLE_CSV = (
    "scientificName,genus,specificEpithet,scientificNameAuthorship,"
    "eventDate,verbatimEventDate,recordedBy,country,countryCode,stateProvince,county,locality,"
    "decimalLatitude,decimalLongitude,coordinateUncertaintyInMeters,"
    "minimumElevationInMeters,maximumElevationInMeters,habitat,samplingProtocol,"
    "sex,individualCount,preparations,identifiedBy,dateIdentified,materialEntityRemarks\n"
    # A clean ISO eventDate: nothing to parse.
    "Otiorhynchus sulcatus,Otiorhynchus,sulcatus,\"(Fabricius, 1775)\","
    "2024-06-15,,J. Doe,Germany,DE,Bavaria,Berchtesgadener Land,"
    "\"Berchtesgaden, Königssee trail\","
    "47.5976,13.0055,50,620,,broadleaf forest edge,hand collecting,"
    "female,3,pinned,J. Doe,2024-07-01,\n"
    # eventDate empty, the original label date in verbatimEventDate — an abbreviated
    # range as written on the label. The ⚡ button parses it into eventDate on assign.
    "Curculio nucum,Curculio,nucum,\"Linnaeus, 1758\","
    ",28.-30.08.2023,J. Doe,Austria,AT,Styria,,\"Grazer Bergland, Schöckel\","
    "47.1833,15.4667,100,1250,,Fagus-Quercus forest,beating,"
    ",1,pinned,J. Doe,2024-06-10,reared from hazel nuts\n"
)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_import_assign_tab(session_factory, refreshers: dict, on_saved=None) -> dict:
    """Build the Import & Assign tab in the current NiceGUI context.

    on_saved: optional callback fired after a specimen is successfully assigned —
    used to clear the unsaved-changes guard (see main.py _mark_form_clean).

    Returns a handle with ``has_content()`` for value-based unsaved-changes
    detection (#47): True while a row is staged for assignment but not yet saved
    (the form area is visible); it clears itself on save."""

    def _with_session(fn):
        with session_factory() as s:
            return fn(s)

    # ── per-connection state ────────────────────────────────────────────
    state: dict = {
        "rows":       [],       # parsed DwC rows
        "filename":   "",
        "selected":   None,     # currently selected row dict
        "taxon_id":   None,     # resolved local taxon id
        # The eventDate / dateIdentified inputs of the selected row (rebuilt per row).
        "edate_in":   None,
        "dtid_in":    None,
        # The identification year, kept between rows: a batch of determinations shares
        # one, and the spreadsheet carries none. Shown in the field, never applied blind.
        "det_year":   "",
    }

    def _option_label(row: dict) -> str:
        """One-line, search-rich label for a reference row in the selector.

        Includes taxon · date · locality · collector · coords so Quasar's
        client-side substring filter can find a row by any of them."""
        ev = dwc_svc.row_to_event_fields(row)
        bits = [
            dwc_svc.row_scientific_name(row),
            ev["event_date"] or ev["verbatim_event_date"],
            ev["locality"] or ev["verbatim_locality"]
            or ev["state_province"] or ev["country"],
            f"leg. {ev['recorded_by']}" if ev["recorded_by"] else "",
        ]
        lat, lon = ev["decimal_latitude"], ev["decimal_longitude"]
        if lat and lon:
            bits.append(f"{lat},{lon}")
        return "  ·  ".join(b for b in bits if b) or "(empty row)"

    def _row_options() -> dict:
        return {i: _option_label(r) for i, r in enumerate(state["rows"])}

    with ui.column().classes("w-full max-w-4xl mx-auto px-4 pt-6 pb-16 gap-4"):

        # ================================================================
        # CARD 1 — Find & assign (the rapid loop; on top so no scrolling
        # past the uploader each specimen). Upload card is built last, below.
        # ================================================================
        assign_card = ui.card().classes("w-full shadow-sm")
        assign_card.set_visibility(False)

        with assign_card:
            # ── Selector: type → dropdown of matching rows → Enter picks ──
            # A native `with_input` q-select, so it inherits the global
            # highlight-first + Enter-select + advance-focus behaviour from
            # main.py (scoped to `.q-select--with-input`) — the same keyboard
            # flow as the identifier picker. Type → Enter → Tab → type code →
            # Enter → Enter (save) drives the whole loop without the mouse.
            row_sel = ui.select(
                options={},
                with_input=True,
                clearable=True,
                label="Find specimen record  (taxon, date, locality, collector…)",
            ).classes("w-full")
            row_sel.on_value_change(
                lambda e: _select_row(state["rows"][e.value])
                if e.value is not None else _clear_form())

            # ── Everything below appears only once a row is selected ──────
            form_area = ui.column().classes("w-full gap-2 mt-2")
            form_area.set_visibility(False)

            with form_area:
                # Taxon auto-resolution status (✓ name, or a picker on failure) —
                # the headline confirmation, shown first.
                ui.separator().classes("my-1")
                taxon_status = ui.column().classes("w-full gap-2")
                # Condensed read-only summary of the chosen row.
                summary_box = ui.column().classes("w-full gap-1 pl-1")

                # Assign fields: identifier (required) + a couple of quick,
                # CSV-prefilled overrides. Everything else is saved from the CSV.
                ui.separator().classes("my-1")

                def _reserved_opts() -> dict:
                    return _with_session(id_svc.reserved_codes)

                with ui.row().classes("w-full flex-wrap gap-3 items-end"):
                    cat_num = ui.select(
                        options={c: c for c in _reserved_opts()},
                        with_input=True,
                        clearable=True,
                        label="identifier *",
                    ).classes("w-48")   # wide enough for a full code, e.g. JJPC-00304
                    count_in  = ui.number("n", value=1, min=0, precision=0).classes("w-20")
                    sex_sel   = ui.select(SEX_OPTIONS, label="sex").classes("w-28")
                    prep_field = build_vocab_field(
                        session_factory, preparation_vocab, "preparations",
                        classes="flex-1 min-w-40",
                    )
                    assign_btn = ui.button("Save & assign", icon="save") \
                        .classes("btn-save")

                # Keep the identifier options live, but push a new set only when it
                # actually CHANGED (A4): an unconditional set_options every tick resets
                # Quasar's in-progress client-side filter, clobbering the
                # type→arrow-keys→enter→tab code-selection workflow.
                _last_codes: list[str] = list(_reserved_opts())

                def _sync_code_opts():
                    nonlocal _last_codes
                    codes = list(_reserved_opts())
                    if codes != _last_codes:
                        _last_codes = codes
                        cat_num.set_options({c: c for c in codes})

                ui.timer(2.0, _sync_code_opts)

                assign_status = ui.label("").classes("text-xs italic mt-1") \
                    .style("color:var(--tp-base-soft)")

                # ── Host / biological association (#6) ────────────────────
                # Shown only when the row carries an associatedOrganisms value.
                # The specimen is the subject; the host plant is the object. The
                # host name is auto-fetched into the taxon box (local→TW→WCVP) so
                # the user confirms the highlighted match rather than trusting a
                # silent candidates[0]; the relationship defaults to "collected
                # from" (editable). Staged into finalize_specimen on Save.
                host_area = ui.column().classes("w-full gap-2 mt-1")
                host_area.set_visibility(False)
                _rel_opts = _with_session(get_relationship_options)
                _default_rel_id = next(
                    (r.id for r in _rel_opts if r.name == "collected from"), None)
                with host_area:
                    ui.separator().classes("my-1")
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.icon("eco").classes("text-green-600")
                        ui.label("Host / association").classes("section-label")
                    host_rel_sel = ui.select(
                        options={r.id: r.name for r in _rel_opts},
                        label="Relationship",
                        clearable=True,
                    ).classes("w-full")
                    ui.timer(2.0, lambda: host_rel_sel.set_options(
                        {r.id: r.name for r in _with_session(get_relationship_options)}))
                    host_ts = build_taxon_search(
                        session_factory,
                        nomenclatural_codes=list(get_config().bio_assoc_default_codes),
                        sources=("local", "taxonworks", "wcvp", "datasets"),
                        placeholder="Host plant name…",
                    )
                    # The qualifier the CSV carried ("Betula sp." → sp.). It is stripped from
                    # the search query so the taxon resolves, but it is a scientific claim in
                    # its own right — the species is undetermined — so it is recovered here,
                    # shown, and saved on the association. Editable: the user confirms it like
                    # every other imported value. Same widget as the Digitize assoc card.
                    host_qual = build_choice_field(
                        IDENTIFICATION_QUALIFIER_OPTIONS, "Qualifier", classes="w-40")

        # ================================================================
        # Logic: select / clear a row
        # ================================================================

        def _clear_form():
            state["selected"] = None
            state["taxon_id"] = None
            form_area.set_visibility(False)
            host_area.set_visibility(False)
            host_ts["clear"]()
            host_rel_sel.value = None
            host_qual["set_value"](None)

        def _summary_line(label: str, value: str) -> None:
            if not value:
                return
            with ui.row().classes("gap-2 items-baseline"):
                ui.label(label).classes("text-xs font-medium w-24 shrink-0") \
                  .style("color:var(--tp-base-soft)")
                ui.label(value).classes("text-sm")

        def _build_date_row(ev: dict) -> None:
            """Date line: the verbatim as written, and the ISO date that will be stored.

            A label date ("28.-30.08.2023") is not a DwC eventDate, and the spreadsheet keeps
            it in verbatimEventDate with eventDate empty — so the specimen would save with no
            date at all. The ⚡ button parses the verbatim into the ISO field; the verbatim is
            never touched (it stays the auditable original). Parsing is on the click, not
            automatic: the reading of a European DD.MM is an interpretation, and the user sees
            it in the field before saving. The field is editable, so the dozen values nothing
            can parse ("ca. 2006?") are typed by hand.
            """
            verbatim = ev["verbatim_event_date"]
            raw_iso, _err = parse_dwc_date(ev["event_date"] or "", allow_interval=True)
            with ui.row().classes("gap-2 items-center"):
                ui.label("Date").classes("text-xs font-medium w-24 shrink-0") \
                  .style("color:var(--tp-base-soft)")
                if verbatim:
                    ui.label(verbatim).classes("text-sm")
                edate_in = ui.input(placeholder="YYYY-MM-DD") \
                    .props("dense outlined").classes("w-52")
                edate_in.value = raw_iso or (ev["event_date"] or "")
                attach_date_validation(edate_in, allow_interval=True)
                state["edate_in"] = edate_in

                def _parse_verbatim() -> None:
                    iso, err = parse_dwc_date(verbatim, allow_interval=True)
                    if err:
                        # Never guess a date the parser cannot read — say why and let the
                        # user type it (CLAUDE.md §2, and the reason 35.8.2015 must fail).
                        ui.notify(f"{verbatim!r}: {err}", type="warning", timeout=6000)
                        return
                    edate_in.value = iso

                if verbatim:
                    ui.button(icon="bolt", on_click=_parse_verbatim) \
                        .props("flat dense round size=sm") \
                        .tooltip("Parse the verbatim date into the ISO field")

        def _build_identified_row(det: dict) -> None:
            """Identified line: det. <name>, plus the year when the row carries none.

            dateIdentified is empty in the whole spreadsheet while identifiedBy is not — the
            determination was made, the year just was not recorded in the file. So it is a
            small field beside the name rather than a form of its own. It carries its value
            *while stepping between rows* (a batch of determinations shares a year), but it
            is **cleared on every save** (#130): a value silently inherited across a saved
            specimen is a wrong date waiting to be stamped, so the user re-enters it per
            batch as an explicit act rather than having it persist unseen.
            """
            csv_year = det["date_identified"]
            with ui.row().classes("gap-2 items-center"):
                ui.label("Identified").classes("text-xs font-medium w-24 shrink-0") \
                  .style("color:var(--tp-base-soft)")
                ident = " · ".join(p for p in (
                    f"det. {det['identified_by']}" if det["identified_by"] else "",
                    csv_year,
                    f"type: {det['type_status']}" if det["type_status"] else "",
                ) if p)
                if ident:
                    ui.label(ident).classes("text-sm")
                if csv_year:
                    state["dtid_in"] = None       # the row states the year; nothing to add
                    return
                dtid_in = ui.input(placeholder="year") \
                    .props("dense outlined").classes("w-28")
                dtid_in.value = state["det_year"]
                attach_date_validation(dtid_in, allow_interval=True, no_future=True)
                dtid_in.on_value_change(
                    lambda e: state.update(det_year=(e.value or "").strip()))
                state["dtid_in"] = dtid_in

        def _select_row(row: dict):
            state["selected"] = row
            state["taxon_id"] = None
            form_area.set_visibility(True)
            assign_status.set_text("")

            # Condensed read-only summary — enough to confirm the specimen.
            ev  = dwc_svc.row_to_event_fields(row)
            det = dwc_svc.row_to_determination_fields(row)
            sp  = dwc_svc.row_to_specimen_prefill(row)
            summary_box.clear()
            with summary_box:
                loc = " ".join(p for p in (
                    ev["locality"] or ev["verbatim_locality"],
                    f"({ev['county']})" if ev["county"] else "",
                ) if p) or ev["state_province"] or ev["country"]
                _summary_line("Locality", loc)
                _build_date_row(ev)
                _summary_line("Collector", ev["recorded_by"])
                # Identification meta (saved from the CSV; shown read-only so the
                # determination can be confirmed at a glance) — except the year,
                # which the row may not carry at all.
                _build_identified_row(det)
                lat, lon = ev["decimal_latitude"], ev["decimal_longitude"]
                extra = " · ".join(p for p in (
                    det["sex"],
                    f"{lat}, {lon}" if lat and lon else "",
                    sp["preparations"],
                    sp["occurrence_remarks"],
                ) if p)
                _summary_line("", extra)

            # Quick specimen overrides, pre-filled from the CSV. individualCount
            # is parsed defensively: a non-numeric cell (e.g. "F") must not crash
            # this value-change callback (#4). Defaults to 1 unless the user
            # adjusts the field; a warning surfaces an unparseable original.
            warns: list[str] = []
            cnt, cnt_warn = dwc_svc.parse_individual_count(sp["individual_count"])
            count_in.value = cnt
            if cnt_warn:
                warns.append(cnt_warn)
            # Soft warning (#5): a georeference with no uncertainty radius — the
            # one thing point-radius exists to prevent. Doesn't block the save.
            _unc = (ev["coordinate_uncertainty_in_meters"] or "").strip()
            if lat and lon and not _unc:
                warns.append("No coordinateUncertaintyInMeters — "
                             "the georeference has no radius.")
            if warns:
                assign_status.set_text("  ·  ".join(warns))
            sex_sel.value  = det["sex"] or None
            prep_field["set_value"](sp["preparations"] or None)

            # Host / biological association (#6): only when the row carries one.
            # Auto-fetch the plant name into the taxon box; default the
            # relationship to "collected from" (both editable before Save).
            _host = dwc_svc.row_host_name(row)
            if _host:
                host_area.set_visibility(True)
                host_rel_sel.value = _default_rel_id
                # Seed with the qualifier stripped ("Betula sp." → "Betula") so a
                # multi-token search actually matches; the user confirms the taxon.
                host_ts["set_query"](dwc_svc.host_search_query(_host))
                # …and keep the stripped qualifier rather than discarding it: "sp." says
                # the species is undetermined, which is part of what the row recorded.
                host_qual["set_value"](dwc_svc.host_qualifier(_host))
            else:
                host_area.set_visibility(False)
                host_ts["clear"]()
                host_rel_sel.value = None
                host_qual["set_value"](None)

            # Refresh identifier dropdown; leave it empty for the user to pick.
            cat_num.options = {c: c for c in _reserved_opts()}
            cat_num.update()
            cat_num.value = None

            # Resolve taxon in the background (shown in the summary).
            asyncio.ensure_future(_resolve_taxon(row))

        # ================================================================
        # Logic: taxon resolution
        # ================================================================

        async def _resolve_taxon(row: dict):
            raw = dwc_svc.row_scientific_name(row)
            taxon_status.clear()

            if not raw:
                with taxon_status:
                    ui.label("No scientificName in this row — search for the name:") \
                      .classes("text-sm italic").style("color:var(--tp-base-soft)")
                _build_fallback_search(taxon_status, row, "")
                return

            # The spreadsheet writes the authorship inside scientificName on 406 of its 1413
            # rows ("Bembidion minimum (Fabricius, 1792)"). Searching *that* string matches
            # nothing anywhere — a stored name never carries its author — so every one of those
            # rows used to dead-end at "Add manually" for names the database already holds.
            # Split it: the NAME does the searching, and the AUTHOR is kept as evidence to
            # check the match against (below). The row's own scientificNameAuthorship column
            # wins when it has one; the inline author is the fallback.
            name, inline_author = taxa_svc.split_scientific_name_authorship(raw)
            author = (row.get("scientificNameAuthorship") or "").strip() or inline_author

            def _accept_local(local_id: int, cand_name: str, local_author: str, *, note: str):
                """Resolve to a local candidate, honouring the author-evidence check (§2).

                No authorship on either side → the name stands on its own. Authors agree →
                resolve with a note. Authors DISAGREE → do not auto-pick (a homonym, or the
                row means another combination): show both and let the user say."""
                if not author or not local_author:
                    _set_taxon(local_id, note)
                    return
                if taxa_svc.authorship_matches(author, local_author):
                    _set_taxon(local_id, f"{note} · author matches ({local_author})")
                    return
                with taxon_status:
                    with ui.row().classes("items-start gap-2 mb-1"):
                        ui.icon("warning", size="sm").style("color:#d97706; margin-top:2px")
                        ui.label(
                            f'"{cand_name}" is in the database with authorship {local_author!r}, '
                            f'but this row says {author!r}. Same name, different author — '
                            "confirm which taxon this specimen is."
                        ).classes("text-sm").style("color:#d97706")
                    ui.button(f"Use the local {cand_name} ({local_author})", icon="check") \
                      .props("flat dense size=sm") \
                      .on_click(lambda: _set_taxon(local_id, "confirmed by hand"))
                _build_fallback_search(taxon_status, row, name)

            # 1. Check local DB — exact composed-name match first.
            local = _with_session(
                lambda s: (lambda t: (t.id, t.scientific_name or "", t.scientific_name_authorship or "")
                           if t else None)(taxa_svc.find_taxon_by_name(s, name)))
            if local:
                _accept_local(local[0], local[1], local[2], note="resolved locally")
                return

            # 1b. Subgenus-insensitive local match — the PREVENTION seam. A bare binomial
            # "Carabus arvensis" must resolve to an existing "Carabus (Eucarabus) arvensis"
            # (and vice versa) instead of dead-ending at "Add manually" and spawning a second
            # row for the same species. Act only on a SINGLE candidate; several rows sharing
            # the binomial (different subgenera, or an existing duplicate) is a real ambiguity
            # — present them, never guess (§2).
            subg = _with_session(
                lambda s: [(t.id, t.scientific_name or "", t.scientific_name_authorship or "",
                            t.taxon_rank)
                           for t in taxa_svc.find_species_ignoring_subgenus(s, name)])
            if len(subg) == 1:
                sid, sname, sauth, _ = subg[0]
                _accept_local(sid, sname, sauth,
                              note=f"resolved locally · subgenus normalised → {sname}")
                return
            if len(subg) > 1:
                with taxon_status:
                    with ui.row().classes("items-start gap-2 mb-1"):
                        ui.icon("info", size="sm").style("color:#2563eb; margin-top:2px")
                        ui.label(
                            f'"{name}" matches several names that differ only by subgenus — '
                            "pick the intended one (they may be duplicates to merge later):"
                        ).classes("text-sm")
                    _build_local_candidates(taxon_status, subg)
                    _build_fallback_search(taxon_status, row, name)
                return

            # 2. Search TaxonWorks
            with taxon_status:
                searching_lbl = ui.label(f'Searching TaxonWorks and installed datasets for \"{name}\"…') \
                    .classes("text-sm italic").style("color:var(--tp-base-soft)")

            try:
                results = await tw_svc.search_taxon_names(name, limit=8)
            except Exception:
                results = []

            # Fetch full records for each result + its valid name so synonyms render
            # "syn ❌ = valid ✓" (same as the shared taxon-search widget).
            detail: dict[int, dict] = {}
            ids = list({tid for r in results
                        for tid in (r["id"], r.get("valid_taxon_name_id")) if tid})
            if ids:
                try:
                    _recs = await asyncio.gather(*[tw_svc.fetch_taxon_name(i) for i in ids])
                    detail = {i: (d or {}) for i, d in zip(ids, _recs)}
                except Exception:
                    detail = {}

            taxon_status.clear()

            # 3. Installed name datasets — LAST in the chain, after local and TaxonWorks (the
            # same order the taxon-search widget uses). A beetle catalogue is exactly the source
            # that knows the names TaxonWorks does not, so this loop must consult it or the user
            # is sent to "Add manually" for a name the database can already resolve.
            ds_hits = _dataset_hits(name)

            if results or ds_hits:
                with taxon_status:
                    ui.label("Not found locally. Select a name:") \
                      .classes("text-xs mb-1").style("color:var(--tp-base-soft)")
                    if results:
                        _build_tw_results(taxon_status, results, detail, author)
                    for ds, rows in ds_hits:
                        _build_dataset_results(taxon_status, ds, rows, author)
                    _build_fallback_search(taxon_status, row, name)
            else:
                with taxon_status:
                    with ui.row().classes("items-center gap-2 mb-2"):
                        ui.icon("warning", size="sm").style("color:#d97706")
                        ui.label(
                            f'"{name}" not found in TaxonWorks or any installed dataset.'
                        ).classes("text-sm")
                    _build_fallback_search(taxon_status, row, name)

        def _build_fallback_search(container, row: dict, name: str) -> None:
            """Search bar + "add manually", offered whenever auto-resolution did not settle it.

            The automatic lookup is an *exact* match on the CSV's scientificName, so it misses
            everything a human would find in a heartbeat: a misspelling, a name carrying its
            authorship, a synonym written differently, a genus the row abbreviated. Sending the
            user to "Add manually" for those invents a taxon the database (or TaxonWorks, or an
            installed checklist) already holds — a duplicate, hand-typed, unlinked to any
            backbone. So the ordinary search widget is offered too, seeded with the row's name
            and searching the whole chain (local → TaxonWorks → datasets). Adding by hand stays
            available beside it, for the names no source knows.
            """
            with container:
                ui.label("or search for it:").classes("text-xs mt-2") \
                  .style("color:var(--tp-base-soft)")
                ts = build_taxon_search(
                    session_factory,
                    on_select=lambda tid: _set_taxon(tid, "selected from search"),
                    sources=("local", "taxonworks", "datasets"),
                    placeholder="Search local database, TaxonWorks, datasets…",
                )
                if name:
                    ts["set_query"](name)
                with ui.row().classes("items-center gap-2 mt-2"):
                    ui.label("or").classes("text-xs").style("color:var(--tp-base-soft)")
                    ui.button("Add manually", icon="add").props("flat dense size=sm") \
                      .on_click(lambda: _open_manual_dialog(row))

        def _build_local_candidates(container, cands: list[tuple]) -> None:
            """Clickable local taxa that share a binomial (differing only by subgenus).

            Shown when a bare-binomial CSV name matches more than one local row — genuinely
            different subgenera, or an existing duplicate. The full composed name (subgenus
            included) is rendered so the two are distinguishable; clicking stamps that taxon.
            The subgenus is shown but is not treated as a matching discriminator (it is
            unstable across catalogues), which is exactly why they collided here.
            """
            with container:
                for cid, cname, cauth, crank in cands:
                    item = ui.element("div").classes("tw-result tw-dropdown-item") \
                        .style("padding:6px 10px; cursor:pointer; border-radius:4px; "
                               "border:1px solid var(--tp-base-border); margin-bottom:3px;")
                    with item:
                        ui.html(taxa_svc.render_full_name(
                            cname, authorship=cauth, taxon_rank=crank))
                    item.on("click", lambda _, cid=cid, cname=cname:
                            _set_taxon(cid, f"selected local → {cname}"))

        def _dataset_hits(name: str) -> list[tuple]:
            """(dataset, importable rows) for every installed dataset that knows *name*.

            Exact name matches only. This is the confirm-and-stamp loop, not a browser: a
            fuzzy suggestion here would invite stamping a specimen with a neighbouring name.
            Refused rows (a status or rank the model cannot hold) are dropped rather than shown
            as unclickable — the manual dialog is the escape hatch for those.
            """
            hits = []
            for ds in ds_svc.list_datasets():
                try:
                    spec = ds.spec
                    db = ds.open()
                except (ns_svc.NameSourceError, OSError):
                    continue
                try:
                    rows = [r for r in ns_svc.search(db, name, spec, limit=5)
                            if r.name.lower() == name.lower() and not r.is_refused(spec)]
                finally:
                    db.close()
                if rows:
                    hits.append((ds, rows))
            return hits

        def _author_chip(candidate_author: str, row_author: str) -> None:
            """A ✓ on the candidate whose authorship agrees with the row's.

            The row's author is the one piece of evidence that distinguishes two identical
            names, so where a source states it, say whether it agrees. A *disagreement* is not
            marked as an error here — a checklist and TaxonWorks legitimately differ on the
            brackets, and the user is picking with their eyes open — but agreement is worth
            pointing at, because that is the candidate they almost certainly want.
            """
            if taxa_svc.authorship_matches(row_author, candidate_author):
                ui.html('<span style="background:rgba(16,185,129,.14); color:#047857; '
                        'border-radius:4px; padding:1px 6px; font-size:.72rem; '
                        'font-weight:600; margin-left:6px;">author ✓</span>')

        def _build_dataset_results(container, ds, rows: list, row_author: str = ""):
            """Clickable rows from one installed dataset, rendered like the TaxonWorks ones."""
            rows = sorted(rows, key=lambda r: not taxa_svc.authorship_matches(
                row_author, r.authorship or ""))          # the confirmed author first
            with container:
                ui.label(f"{ds.label} · experimental").classes("text-xs mt-2") \
                  .style("color:var(--tp-base-soft)")
                for r in rows:
                    item = ui.element("div").classes("tw-result tw-dropdown-item") \
                        .style("padding:6px 10px; cursor:pointer; border-radius:4px; "
                               "border:1px solid var(--tp-base-border); margin-bottom:3px;")
                    with item:
                        with ui.row().classes("items-center gap-0 no-wrap"):
                            ui.html(f"📖 <i>{r.name}</i>"
                                    + (f" {r.authorship}" if r.authorship else ""))
                            _author_chip(r.authorship or "", row_author)
                    item.on("click", lambda _, r=r, d=ds: _import_from_dataset(r, d))

        def _import_from_dataset(row, ds) -> None:
            """Import a name (and its lineage) from a dataset, then stamp it on this specimen."""
            try:
                db = ds.open()
                try:
                    chain = ns_svc.chain_for(db, row, ds.spec)
                finally:
                    db.close()
            except ns_svc.NotImportable as exc:
                ui.notify(f"Cannot import: {exc}", type="negative", timeout=8000)
                return

            mismatches: list[str] = []
            try:
                with session_factory() as session:
                    with session.begin():
                        taxon = taxa_svc.get_or_create_from_chain(
                            session, chain["chain"],
                            accepted_chain=chain["accepted_chain"],
                            mismatches=mismatches,
                        )
                        tid = taxon.id
            except Exception as exc:      # noqa: BLE001
                ui.notify(f"DB error: {exc}", type="negative")
                return
            _set_taxon(tid, f"imported from {ds.label}")
            for msg in mismatches:
                ui.notify(f"Taxonomy mismatch: {msg}", type="warning", timeout=8000)

        def _build_tw_results(container, results: list[dict], detail: dict | None = None,
                              row_author: str = ""):
            """Show clickable TaxonWorks autocomplete results.

            Uses the SHARED renderer (_render_tw_label) so synonyms display cleanly with
            their valid name ("… ❌ = Valid name ✓"), same as the taxon-search widget —
            instead of dumping the raw label_html (which showed rank/original-combination
            badges as garbled inline text and never resolved the valid name)."""
            detail = detail or {}

            def _tw_author(r: dict) -> str:
                return (detail.get(r.get("id"), {}) or {}).get("cached_author_year", "") or ""

            results = sorted(results, key=lambda r: not taxa_svc.authorship_matches(
                row_author, _tw_author(r)))               # the confirmed author first
            with container:
                for r in results:
                    vid = r.get("valid_taxon_name_id")
                    valid_name = (detail.get(vid, {}).get("cached", "")
                                  if vid and vid != r.get("id") else "")
                    item = ui.element("div").classes("tw-result tw-dropdown-item") \
                        .style("padding:6px 10px; cursor:pointer; border-radius:4px; "
                               "border:1px solid var(--tp-base-border); margin-bottom:3px;")
                    with item:
                        with ui.row().classes("items-center gap-0 no-wrap"):
                            ui.html(_render_tw_label(r, valid_name))
                            _author_chip(_tw_author(r), row_author)
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
            _set_taxon(tid, "imported from TaxonWorks")
            for msg in mismatches:
                ui.notify(f"Taxonomy mismatch: {msg}", type="warning", timeout=8000)

        def _open_manual_dialog(row: dict):
            """Open the shared New Taxon dialog, prefilled from this DwC row.

            Same dialog as the Taxonomy tab's "New Taxon"; the parent (and its
            inherited nomenclatural code) is pre-resolved from the parsed name when
            the genus/subgenus/species already exists locally. If it doesn't, the
            user creates that ancestor first (it appears as a parent option once
            saved), then the species — a deliberate two-step that guarantees code
            inheritance and no orphan rows.
            """
            with session_factory() as s:
                prefill = taxa_svc.build_manual_taxon_prefill(s, row)
            open_new_taxon_dialog(
                session_factory, prefill=prefill, on_created=_on_manual_created
            )

        def _on_manual_created(tid: int):
            _set_taxon(tid, "added manually")
            if "taxonomy_stats" in refreshers:
                refreshers["taxonomy_stats"]()

        def _set_taxon(tid: int, caption: str = "selected") -> None:
            state["taxon_id"] = tid
            with session_factory() as s:
                t = s.get(taxa_svc.Taxon, tid)
                label = taxa_svc.format_scientific_name(t) if t else f"taxon #{tid}"
            taxon_status.clear()
            with taxon_status:
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.icon("check_circle", size="sm").style("color:#16a34a")
                    ui.label(label).classes("text-sm italic")
                    ui.label(caption).classes("text-xs").style("color:var(--tp-base-soft)")
                    # A selected taxon must be undoable (#import-assign UX) — Change
                    # clears it and opens the full local+TW search to pick another.
                    ui.button("Change", icon="edit").props("flat dense size=sm no-caps") \
                      .on_click(_change_taxon)

        def _change_taxon() -> None:
            state["taxon_id"] = None
            taxon_status.clear()
            with taxon_status:
                ui.label("Search for the correct taxon:").classes("text-xs mb-1") \
                  .style("color:var(--tp-base-soft)")
                build_taxon_search(
                    session_factory,
                    on_select=lambda tid: _set_taxon(tid, "selected"),
                )

        # ================================================================
        # Logic: validate + save
        # ================================================================

        def _ui_dates() -> tuple[str, str, str | None]:
            """(event_date, date_identified, error) — the dates as the *fields* hold them.

            The fields are the authority, not the CSV: they were seeded from it, the ⚡ button
            may have parsed the verbatim into eventDate, and the year field supplies a
            dateIdentified the spreadsheet never carried. Everything is re-parsed here, so a
            hand-typed value gets the same normalisation and the same loud refusal as an
            imported one (#1 — a `15.07.2005` must never land verbatim in dwc:eventDate).
            """
            edate_in, dtid_in = state["edate_in"], state["dtid_in"]
            iso_ed, err = parse_dwc_date(
                (edate_in.value or "").strip() if edate_in else "", allow_interval=True)
            if err:
                return ("", "", f"eventDate: {err}")
            if dtid_in is not None:
                iso_di, err = parse_dwc_date(
                    (dtid_in.value or "").strip(), allow_interval=True, no_future=True)
                if err:
                    return ("", "", f"dateIdentified: {err}")
            else:
                # The row states its own dateIdentified; parse it as before.
                _ovr, err = dwc_svc.normalise_row_dates(state["selected"])
                if err:
                    return ("", "", err)
                iso_di = _ovr["date_identified"]
            return (iso_ed, iso_di, None)

        def _validate() -> str | None:
            if state["taxon_id"] is None:
                return "Resolve the taxon before saving."
            if not cat_num.value:
                return "Select an identifier code."
            if state["selected"] is None:
                return "No record selected."
            ev = dwc_svc.row_to_event_fields(state["selected"])
            cc = ev["country_iso"].strip()
            if cc and len(cc) != 2:
                return "countryCode must be exactly 2 characters."
            # Refuse a bad date rather than store it verbatim (#1).
            _, _, date_err = _ui_dates()
            if date_err:
                return date_err
            # identificationQualifier is a closed set (DB CHECK) — refuse an off-list value
            # up front with the allowed list, rather than letting the save hit the constraint.
            q = (state["selected"].get("identificationQualifier") or "").strip()
            if q and q not in IDENTIFICATION_QUALIFIERS:
                return (f"identificationQualifier {q!r} is not one of: "
                        + ", ".join(IDENTIFICATION_QUALIFIERS) + ".")
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
            # A coordinate pair is atomic — one axis without the other is half a
            # georeference (#5). Reject it rather than save a lat with no lon.
            _lat = (ev["decimal_latitude"] or "").strip()
            _lon = (ev["decimal_longitude"] or "").strip()
            if bool(_lat) != bool(_lon):
                return ("decimalLatitude and decimalLongitude must both be "
                        "present or both empty — a half georeference cannot "
                        "be saved.")
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
                        # Own collection: the flagged default repository is the
                        # membership source for a new specimen (#83/#75).
                        default_repo = repo_svc.get_default(session)
                        if default_repo is None:
                            raise ValueError(
                                "No default collection set — open Settings.")
                        # Hidden determination + specimen fields are saved straight
                        # from the CSV row (only identifier / n / sex / preparations
                        # are surfaced in the fast path). identifiedBy is a person FK,
                        # resolved like recordedBy.
                        det = dwc_svc.row_to_determination_fields(row)
                        sp  = dwc_svc.row_to_specimen_prefill(row)
                        _idby = (det.get("identified_by") or "").strip()
                        idby_id = (
                            persons_svc.get_or_create_person(session, full_name=_idby).id
                            if _idby else None)
                        # habitat + samplingProtocol are controlled vocabularies:
                        # resolve the parsed text → FK ids (get_or_create), like the
                        # event form's commit does for the interactive tabs.
                        event_fields = dwc_svc.row_to_event_fields(row)
                        _hab = (event_fields.pop("habitat", None) or "").strip()
                        _samp = (event_fields.pop("sampling_protocol", None) or "").strip()
                        event_fields["habitat_id"] = (
                            habitat_vocab.get_or_create(session, _hab).id if _hab else None)
                        event_fields["sampling_protocol_id"] = (
                            sampling_protocol_vocab.get_or_create(session, _samp).id if _samp else None)
                        # recordedBy is a person FK like everywhere else — resolve the
                        # parsed name → recorded_by_id inside the save transaction, or the
                        # collector is silently dropped by create_collecting_event (#61).
                        _rec = (event_fields.pop("recorded_by", None) or "").strip()
                        event_fields["recorded_by_id"] = (
                            persons_svc.get_or_create_person(session, full_name=_rec).id
                            if _rec else None)
                        # Dates as the fields hold them — the ⚡-parsed eventDate and the
                        # identification year (_ui_dates; _validate already refused a bad
                        # one). The verbatim is never overwritten: it is the auditable
                        # original a DD.MM misread would have to be checked against (#1).
                        _iso_ed, _iso_di, _ = _ui_dates()
                        event_fields["event_date"] = _iso_ed
                        _raw_ed = (row.get("eventDate") or "").strip()
                        if _raw_ed and _raw_ed != _iso_ed and not event_fields["verbatim_event_date"]:
                            event_fields["verbatim_event_date"] = _raw_ed
                        det["date_identified"] = _iso_di
                        co = svc.save_specimen_entry(
                            session,
                            taxon_id=state["taxon_id"],
                            event_id=None,
                            # Retroactive digitisation of a large batch: many
                            # specimens share one collecting event, so reuse an
                            # existing 100%-identical event rather than inserting a
                            # duplicate per specimen (#event-dedup).
                            reuse_event=True,
                            event_fields=event_fields,
                            specimen_fields={
                                "catalog_number":    code,
                                "repository_id":     default_repo.id,
                                # Preserve a deliberate 0; only a cleared (None)
                                # field falls back to the standard 1 (#4).
                                "individual_count":  1 if count_in.value is None
                                                     else int(count_in.value),
                                "preparation_id":    prep_field["commit"](session),
                                "life_stage":        sp["life_stage"] or NEW_SPECIMEN_DEFAULTS["life_stage"],
                                "basis_of_record":   NEW_SPECIMEN_DEFAULTS["basis_of_record"],
                                "occurrence_remarks":sp["occurrence_remarks"],
                            },
                            determination_fields={
                                "sex":                      sex_sel.value or None,
                                "type_status":              det.get("type_status") or None,
                                "identified_by_id":         idby_id,
                                "date_identified":          det.get("date_identified") or None,
                                # From the CSV now (#3); _validate has checked it is on-list.
                                "identification_qualifier": det.get("identification_qualifier") or None,
                                "identification_remarks":   det.get("identification_remarks") or None,
                                "verbatim_identification":  dwc_svc.row_scientific_name(row),
                            },
                        )
                        # Host / biological association (#6): attach it only when
                        # a taxon actually resolved. A present-but-unresolved host
                        # is NOT dropped silently — the specimen still saves and a
                        # warning names the host to fix in Records.
                        associations = []
                        host_unresolved = ""
                        if dwc_svc.row_host_name(row):
                            _htid = host_ts["taxon_id"]
                            _hrel = host_rel_sel.value
                            if _htid and _htid != -1 and _hrel:
                                associations.append({
                                    "rel_id": _hrel,
                                    "taxon_id": _htid,
                                    "qualifier": host_qual["get_value"]() or None,
                                })
                            else:
                                host_unresolved = dwc_svc.row_host_name(row)
                        # Retroactive digitisation: the specimen already carries
                        # its own data + identification labels and the identifier
                        # is pre-printed, so bind the code but queue no labels
                        # (same policy as Digitize standard; see finalize_specimen).
                        svc.finalize_specimen(
                            session,
                            collection_object_id=co.id,
                            code=code,
                            queue_labels=False,
                            associations=associations,
                        )
                        saved_id = co.id
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return

            if host_unresolved:
                ui.notify(
                    f"Saved #{saved_id} [{code}] — but host {host_unresolved!r} "
                    "wasn't resolved, so no association was attached. Add it in "
                    "Records.", type="warning", timeout=6000)
            else:
                ui.notify(f"Saved — specimen #{saved_id}  [{code}]", type="positive")

            # Reset for the next specimen: clear the selection + fields and drop
            # focus back on the row selector so the loop is keyboard-continuous.
            cat_num.options = {c: c for c in _reserved_opts()}
            cat_num.update()
            cat_num.value = None
            # Empty the carried-over identification year after every save (#130): a
            # dateIdentified left filled from the previous specimen is a silent wrong
            # value waiting to be stamped on the next one. The user re-enters it per
            # batch — an explicit act — rather than inheriting it unseen.
            state["det_year"] = ""
            row_sel.set_value(None)          # fires _clear_form via on_value_change
            _clear_form()
            row_sel.run_method("focus")

            if on_saved:
                on_saved()
            for fn in refreshers.values():
                fn()

        assign_btn.on_click(_on_assign)

        # ================================================================
        # CARD 2 — Upload (kept at the bottom: a once-per-session action,
        # out of the way of the rapid find→assign loop above).
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
            # Which columns the workflow read, and which it ignored. A misspelt or
            # unsupported header is otherwise dropped in silence — "1413 rows loaded"
            # tells the user nothing about whether their localities came with them.
            # The counts are always visible; the lists sit one click away.
            col_report = ui.column().classes("w-full gap-0 mt-1")

            def _show_columns(understood: list[tuple[str, str]], ignored: list[str]) -> None:
                col_report.clear()
                with col_report:
                    title = (f"{len(understood)} column"
                             f"{'' if len(understood) == 1 else 's'} understood")
                    if ignored:
                        title += (f"  ·  {len(ignored)} ignored "
                                  f"(not imported)")
                    with ui.expansion(title).props("dense expand-icon-toggle") \
                            .classes("text-sm w-full"):
                        if ignored:
                            ui.label("Ignored — no field reads these:") \
                                .classes("text-xs font-medium mt-1")
                            ui.label(", ".join(ignored)).classes("text-xs") \
                                .style("color:var(--tp-warning,#b45309)")
                        ui.label("Understood:").classes("text-xs font-medium mt-2")
                        ui.label(", ".join(
                            term if header == term else f"{header} → {term}"
                            for header, term in understood
                        )).classes("text-xs").style("color:var(--tp-base-soft)")

            def _on_upload(e):
                raw = e.content.read()
                try:
                    rows = dwc_svc.parse_csv(raw)
                    understood, ignored = dwc_svc.column_report(raw)
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
                _show_columns(understood, ignored)
                row_sel.set_options(_row_options())
                row_sel.set_value(None)
                assign_card.set_visibility(True)
                form_area.set_visibility(False)
                # Only ever one file is read (the latest), so only one should be
                # listed (#131): clear the upload's file list after each upload,
                # or a re-upload leaves the superseded file shown as still loaded.
                upload_widget.reset()

            upload_widget = ui.upload(
                label="Choose CSV…",
                on_upload=_on_upload,
                auto_upload=True,
            ).props("accept=.csv,text/csv flat").classes("mt-2")

    # Value-based unsaved-changes signal (#47): a visible form_area means a row is
    # staged for assignment and not yet saved. _on_assign clears it on success, so
    # this clears itself. (More precise than the old DOM-event detection, which also
    # fired on searching/uploading.)
    def _has_content() -> bool:
        return bool(form_area.visible)

    return {"has_content": _has_content}

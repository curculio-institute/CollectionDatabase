"""TaxonWorks sync tab (#149) — Compare (Step 1) + Export (Step 3), read-only against TW
until the user explicitly reconciles OTU ids or downloads a spreadsheet.

``app/services/dwc_export.py`` (local eligibility + the DwC projection),
``app/services/tw_sync.py`` (taxon-name / OTU-id check) and the new
``app/services/tw_compare.py`` (the network half of #149's own "Step 1: Compare" —
existence + field-level diff against TaxonWorks' `dwc_occurrences` mirror, verified
against the live sandbox project, see that file's docstring) are the only sources of
truth; this file renders what they report and drives their write actions
(``reconcile_otu_ids`` and the TSV download — never a specimen-record write).

**One collection at a time (required, not optional).** TaxonWorks' DwC-A import maps to a
single Namespace per upload (institutionCode+collectionCode, CLAUDE.md's "TaxonWorks
namespace" section), so a file mixing rows from two collections has nowhere consistent to
land. Every check in this tab is therefore scoped to one repository.

**Checks are per-collection and on demand, never automatic.** A compare issues one
TaxonWorks lookup per local catalog number (`tw_compare.py`'s docstring — `catalogNumber=`
is a real, verified exact-match filter, ~3s for 39 specimens), bounded concurrency so it
never bursts the server. Still never run for every collection the instant the tab opens:
each lookup is still one request against a shared public server, and the user may have
several collections open at once — the Collections table's "Check" stays a deliberate
per-row button (#149 follow-up), the same discipline that already governs Overpass/Photon
calls elsewhere in this app (CLAUDE.md's geocoding section), not merely a speed workaround.

**The tab carries no situation-specific copy.** Connection state (which host, whether
stored OTU ids are trusted here) is a fact about *whatever* TaxonWorks instance is
currently configured, not a standing warning banner — it is shown once, quietly, as part of
whichever collection's check surfaces it, rather than as an always-visible alarm before the
user has asked to check anything (#149 follow-up).

House style, taken from reading ``app/ui/batch_tab.py`` and ``app/ui/bulk_import_tab.py``
in full and ``app/ui/controlled_vocab_tab.py`` in part (used wherever the two disagree,
since it is the closer analogue — a collection-scoped operation with a results list and a
bulk action):

- **One `ui.card().classes("w-full max-w-5xl mx-auto shadow-sm")` per step**, headed by
  ``ui.label(...).classes("section-label")`` — never a raw ``ui.html`` heading.
- **State lives in one small mutable dict** (``state``) closed over by the handlers; no
  globals.
- **A results area is a single `ui.column()` that is `.clear()`ed and rebuilt** on every
  render (`batch_tab._render_results` / `bulk_import_tab._refresh_detail`), never patched
  piecewise.
- **A `ui.table` row action is a slot-emitted event**, exactly `controlled_vocab_tab.py`'s
  edit/merge/delete pattern (`add_slot("body-cell-actions", ...@click="$parent.$emit(...)"
  ...)`, `table.on(name, lambda e: handler(e.args))`) — not a hand-rolled per-row widget.
- **Buttons:** the primary action in a scoped tab is `.props("no-caps color=secondary")`
  (batch_tab's Fetch/Match/Apply-disposition); a low-emphasis follow-on action is
  `.props("flat dense no-caps size=sm")` (batch_tab's Copy/CSV buttons); a button that runs
  a network/long op shows `.props("loading")` while running and `.props(remove="loading")`
  in a `finally` (main.py's `_test_tw_connection`).
- **A disabled button's tooltip lives on a wrapping `ui.element("div")`, not the button
  itself** — a disabled Quasar button swallows hover, so a tooltip on it directly never
  shows (`record_sheet.py`'s Reprint button).
- **Badges** (`ui.badge(...).props(f"color={colour}")`) for small counts, using only colours
  already in use elsewhere (`primary` / `positive` / `warning` / `negative` / `grey`) —
  `bulk_import_tab`'s ready/imported/blocked/errored row.
- **Expansions** (`ui.expansion(...).classes("w-full")`, default closed) for anything
  infrequently relevant — advisory notes, withheld/refused/redacted detail
  (`bulk_import_tab`'s "Show blocked rows"; the progressive-disclosure rule).
- **Downloads** via `ui.download(bytes, filename=..., media_type=...)`, matching
  `explore.py`'s CSV export.
- **`ui.notify(..., type="positive"/"negative"/"warning")`**, `multi_line=True` for a
  longer message (`main.py`'s TW-connection test).
- De-emphasised helper text: `.classes("text-sm").style("color:var(--tp-base-soft)")`.
- **One step (card) visible at a time.** Reuses NiceGUI's own `ui.tabs()` /
  `ui.tab_panels()` with a Prev/Next pair, exactly `controlled_vocab_tab.py`'s own section
  list — **not** the Digitize stepper's `.tp-stepper-bar` chip bar: that file's own comment
  rules out reusing that class for a second, unrelated bar on the same page (a global
  keydown handler grabs the *first* `.tp-stepper-bar` on the page).
- Nothing here touches the network while the page is being built (no data comes from a
  network call at import/build time), and there is no `ui.timer` — nothing on this tab is
  a DB-backed select that can go stale.

**Taxonomy scope (optional, Export step only).** The export can be restricted to one taxon
and its descendants (e.g. "Curculionoidea only") — filtering is done here, in the UI, by
restricting the `CollectionObject` query before it reaches `export_occurrences`/
`taxa_to_export`. Session-only (cleared on page reload) — a full DB-backed "remembered
across restarts" setting was cut as disproportionate machinery for a UI convenience (a
migration + table is `person_defaults`-level ceremony; this is not a load-bearing default
anything else depends on). The picker reuses the existing taxon-search widget
(`taxon_search.py::build_taxon_search`, `sources=("local",)`) and the descendant expansion
(`batch_ops.py::descendant_taxon_ids`) — both already built for Batch tools' identical need.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from nicegui import ui
from sqlalchemy.orm import selectinload

import app.services.repositories as repo_svc
import app.services.taxonworks as tw_svc
import app.services.tw_compare as tw_compare
from app.config import get_config
from app.models import CollectingEvent, CollectionObject, TaxonDetermination
from app.services import dwc_export, tw_sync
from app.services.batch_ops import descendant_taxon_ids
from app.ui.taxon_search import build_taxon_search

_STEPS: tuple[tuple[str, str], ...] = (
    ("collections", "1 · Collections"),
    ("export", "2 · Export"),
    ("cannot", "3 · What cannot be exported"),
)
_STEP_KEYS = [k for k, _ in _STEPS]


def build_tw_sync_tab(session_factory, refreshers: dict | None = None,
                       open_explore=None) -> None:
    """`open_explore(groups)` — hand a facet-group list (Explore's own `state["groups"]`
    shape) to Explore and switch the app to it, exactly the `on_open_specimen`/
    `on_open_event` "drill into another tab" pattern Explore itself already uses toward
    Records, just in the other direction (#149 follow-up: "let me examine these in
    Explore"). `None` when the caller hasn't wired it — every click site degrades to a
    no-op rather than raising, so this file still renders standalone (e.g. in a test)."""
    refreshers = refreshers or {}
    # `result`/`checks` are the Export step's own local+name check (Step 3 of #149).
    # `compare` is per-collection: {repo_id: {"result": CompareResult, "checks": [...],
    # "label": str}} — the Collections step's Step-1 compare, kept separate because the
    # two steps answer different questions (is this exportable vs. does it already match
    # what TaxonWorks holds) and a compare for one collection must not silently gate the
    # Export step's button for a *different* one. `repo_id`/`repo_code` is the Export
    # step's working collection.
    state: dict = {
        "result": None, "checks": None, "repo_id": None, "repo_code": "",
        "compare": {}, "checking_repo": None,
    }

    with session_factory() as _s:
        _default_repo = repo_svc.get_default(_s)
        state["repo_id"] = _default_repo.id if _default_repo else None
        state["repo_code"] = _default_repo.collection_code if _default_repo else ""

    def _repo_options(s) -> dict:
        return {r.id: f"{r.collection_code} — {r.collection_full_name}"
                for r in repo_svc.list_repositories(s)}

    def _open_catalog_numbers(catalog_numbers, label: str) -> None:
        """The generic hand-off to Explore for "these specific specimens" — every
        report-table cell below that isn't the whole-collection case goes through this
        one function, so there is exactly one place that builds a `catalog_numbers`
        facet group (app/services/explore.py)."""
        if open_explore is None:
            return
        if not catalog_numbers:
            ui.notify("Nothing to show — this is empty.", type="info")
            return
        open_explore([{"op": "and", "facets": [{
            "kind": "catalog_numbers", "label": label,
            "key": tuple(catalog_numbers), "tag": "TaxonWorks sync",
        }]}])

    def _render_blocking_names(checks) -> None:
        """Shared by the Export step's pre-flight and the Collections step's per-collection
        compare — both ask "which names does TaxonWorks not (yet) agree with", just for a
        different specimen set, so the rendering is written once."""
        blocking = [c for c in checks if c.blocks_export]
        if not blocking:
            ui.label("No names block the export.").classes("text-sm mt-3") \
                .style("color:var(--tp-base-soft)")
            return
        ui.label(f"Names blocking export ({len(blocking)})") \
            .classes("text-sm font-semibold text-red-600 mt-3")
        for c in blocking:
            with ui.column().classes("w-full gap-0 mt-1"):
                ui.label(
                    f"{c.local_name} ({c.local_rank or '—'}) — {c.status}"
                ).classes("text-sm font-medium")
                for note in c.notes:
                    ui.label(f"· {note}").classes("text-xs") \
                        .style("color:var(--tp-base-soft)")
                if c.candidates:
                    cand_txt = "; ".join(
                        f"{cd.cached} {cd.authorship or ''} ({cd.rank or '—'})".strip()
                        for cd in c.candidates
                    )
                    ui.label(f"· candidates on TaxonWorks: {cand_txt}") \
                        .classes("text-xs").style("color:var(--tp-base-soft)")
                if c.status == "missing":
                    ui.label(
                        "Add this name in TaxonWorks, then run Check again."
                    ).classes("text-xs text-red-600")
                    ui.link(
                        "Open TaxonWorks → New taxon name",
                        f"{tw_svc.web_base()}/tasks/nomenclature/new_taxon_name",
                        new_tab=True,
                    ).classes("text-xs")

    # ── Step navigation — one card visible at a time (NiceGUI tabs, see module docstring
    # for why this is not the Digitize `.tp-stepper-bar` chip bar) ─────────────────────
    with ui.row().classes("w-full max-w-5xl mx-auto items-center gap-2 mb-2 flex-nowrap"):
        prev_btn = ui.button("Prev", icon="chevron_left") \
            .props("flat dense no-caps").tooltip("Previous step (wraps around)")
        with ui.tabs().props("dense inline-label outside-arrows mobile-arrows") \
                .classes("flex-1 min-w-0") as step_tabs:
            for _key, _title in _STEPS:
                ui.tab(_key, label=_title)
        next_btn = ui.button("Next", icon="chevron_right") \
            .props("flat dense no-caps icon-right").tooltip("Next step (wraps around)")

    def _cycle(step: int) -> None:
        cur = step_tabs.value or _STEP_KEYS[0]
        idx = (_STEP_KEYS.index(cur) + step) % len(_STEP_KEYS)
        step_tabs.set_value(_STEP_KEYS[idx])

    prev_btn.on_click(lambda: _cycle(-1))
    next_btn.on_click(lambda: _cycle(+1))

    with ui.tab_panels(step_tabs, value=_STEP_KEYS[0]).classes("w-full").props("keep-alive"):

        # ── Step 1 — Collections (per-collection #149 "Step 1: Compare") ───────────────
        with ui.tab_panel("collections").classes("p-0"):
            with ui.card().classes("w-full max-w-5xl mx-auto shadow-sm"):
                ui.label("Collections").classes("section-label")
                cfg = get_config()
                host = urlsplit(cfg.tw_base).netloc or "(not configured)"
                ui.label(f"Connected to {host}.").classes("text-sm") \
                    .style("color:var(--tp-base-soft)")
                ui.label(
                    "TaxonWorks allows only one collection (namespace) per upload. "
                    "Select a collection to work on:"
                ).classes("text-sm").style("color:var(--tp-base-soft)")

                # ── Working collection — first, not last: everything below (the
                # report, a per-row Check, the Export step) is scoped by or acts on
                # one collection, so picking it is the first decision, not something
                # found at the bottom after already having looked at everything.
                with ui.row().classes("items-center gap-2 mt-2"):
                    wc_label = ui.label().classes("text-sm font-medium")
                    wc_change_btn = ui.button("Change", icon="swap_horiz") \
                        .props("flat dense no-caps size=sm")
                wc_select = ui.select(
                    options={}, label="Working collection (for the Export step)",
                    with_input=True,
                ).classes("w-full").style("display:none")

                def _sync_wc_label() -> None:
                    if state["repo_id"] is None:
                        wc_label.set_text(
                            "⚠ No default collection set — choose one below, or set "
                            "one in Settings."
                        )
                        wc_select.style("display:block")
                    else:
                        wc_label.set_text(f"Working collection: {state['repo_code']}")

                def _toggle_wc_change() -> None:
                    state["wc_open"] = not state.get("wc_open", False)
                    wc_select.style(
                        "display:block" if state["wc_open"] else "display:none")
                    if state["wc_open"]:
                        with session_factory() as s:
                            wc_select.set_options(_repo_options(s))

                wc_change_btn.on_click(_toggle_wc_change)

                def _on_wc_change(e) -> None:
                    if not e.value:
                        return
                    with session_factory() as s:
                        r = s.get(repo_svc.Repository, e.value)
                    if r is None:
                        return
                    state["repo_id"] = r.id
                    state["repo_code"] = r.collection_code
                    # The previous check ran against a different collection — stale
                    # results would let Download hand out a file that no longer
                    # matches what is shown.
                    state["result"] = None
                    state["checks"] = None
                    _sync_wc_label()
                    wc_select.style("display:none")
                    _render_results()
                    _sync_download_enabled()
                    _sync_card2_wc_line()

                wc_select.on_value_change(_on_wc_change)
                _sync_wc_label()

                # Consent policy — read-only here (Settings owns it), but the eligible/
                # not-eligible split right below depends on it, so it must be visible
                # without a trip to Settings to find out which one is active. Only the
                # active option is shown, not both.
                _CONSENT_OPT_TEXT = {
                    "name_removed": "Export the record with their name removed",
                    "consented_only": "Do not export",
                }
                consent_status_label = ui.label().classes("text-xs") \
                    .style("color:var(--tp-base-soft)")

                def _sync_consent_status() -> None:
                    nonconsent = get_config().tw_export_nonconsent or "name_removed"
                    active = _CONSENT_OPT_TEXT.get(nonconsent, nonconsent)
                    consent_status_label.set_text(
                        f"Collectors who did not explicitly consent: {active}"
                    )

                _sync_consent_status()

                ui.separator().classes("my-3")

                report_col = ui.column().classes("w-full mt-2")
                report_state: dict = {"rows": [], "reasons": {}, "table": None}

                def _load_report(s) -> None:
                    rows: list[dict] = []
                    reasons_by_repo: dict[int, tuple] = {}
                    for r in repo_svc.list_repositories(s):
                        cos = (
                            s.query(CollectionObject)
                            .options(
                                selectinload(CollectionObject.collecting_event)
                                .selectinload(CollectingEvent.recorded_by_person)
                            )
                            .filter(CollectionObject.repository_id == r.id)
                            .all()
                        )
                        eligible = sum(
                            1 for co in cos if dwc_export.export_decision(co).eligible)
                        # Already-checked collections keep their real numbers across a
                        # report refresh (e.g. after switching the working collection
                        # below) instead of resetting to "Check pending".
                        cached = state["compare"].get(r.id)
                        pending = "Check pending"
                        rows.append({
                            "repo_id": r.id,
                            "collection": f"{r.collection_code} — "
                                          f"{r.collection_full_name}",
                            "total": len(cos),
                            "eligible": eligible,
                            "not_eligible": len(cos) - eligible,
                            "synced": (cached["result"].synced_count
                                       if cached else pending),
                            "diverged": (len(cached["result"].diverged)
                                         if cached else pending),
                            "not_uploaded": (len(cached["result"].not_on_tw)
                                             if cached else pending),
                        })
                        reasons_by_repo[r.id] = tw_compare.ineligible_specimens(
                            s, repository_id=r.id)
                    report_state["rows"] = rows
                    report_state["reasons"] = reasons_by_repo

                # Every numeric column hands its specimens to Explore on click (#149
                # follow-up — "let me examine these"), except "collection" (a label, not
                # a count) and "actions" (its own Check button). One handler for all six:
                # the column name IS the row dict's own key, so no per-column function.
                _CLICKABLE_COLS = (
                    "total", "not_eligible", "eligible", "not_uploaded", "synced",
                    "diverged",
                )

                def _on_open_col(payload: dict) -> None:
                    repo_id = payload.get("repo_id")
                    col = payload.get("col")
                    row = next(
                        (r for r in report_state["rows"] if r["repo_id"] == repo_id),
                        None)
                    if row is None:
                        return
                    label = row["collection"]

                    if col == "total":
                        # The one column with an existing, exact-match Explore facet
                        # already (every specimen in this repository) — no need to
                        # resolve a catalog-number list for it.
                        if open_explore is not None:
                            open_explore([{"op": "and", "facets": [{
                                "kind": "collection", "label": label,
                                "key": repo_id, "tag": "Collection",
                            }]}])
                        return

                    if col == "not_eligible":
                        # Already resolved for the "Why some are not eligible"
                        # expansion — reuse it rather than re-querying.
                        cats = [cat for cat, _ in
                                report_state["reasons"].get(repo_id, ())]
                        _open_catalog_numbers(cats, f"{label} — not eligible")
                        return

                    if col == "eligible":
                        with session_factory() as s:
                            cats = tw_compare.eligible_specimens(
                                s, repository_id=repo_id)
                        _open_catalog_numbers(list(cats), f"{label} — eligible")
                        return

                    # The remaining three (not_uploaded / synced / diverged) only exist
                    # once this collection's own Check has run — the table shows "Check
                    # pending" for them until then, so there is nothing to hand to
                    # Explore yet.
                    data = state["compare"].get(repo_id)
                    if data is None:
                        ui.notify("Run Check for this collection first.",
                                  type="warning")
                        return
                    result: tw_compare.CompareResult = data["result"]
                    if col == "not_uploaded":
                        _open_catalog_numbers(
                            list(result.not_on_tw), f"{label} — not yet uploaded")
                    elif col == "synced":
                        _open_catalog_numbers(
                            list(result.synced), f"{label} — matches TaxonWorks")
                    elif col == "diverged":
                        _open_catalog_numbers(
                            [d.catalog_number for d in result.diverged],
                            f"{label} — diverged")

                def _render_report() -> None:
                    _sync_consent_status()   # config may have changed since page load
                    report_col.clear()
                    with session_factory() as s:
                        _load_report(s)
                    rows = report_state["rows"]
                    with report_col:
                        if not rows:
                            ui.label(
                                "No collections set up yet — add one in Controlled "
                                "Vocabularies."
                            ).classes("text-sm").style("color:var(--tp-base-soft)")
                            return
                        table = ui.table(
                            columns=[
                                {"name": "collection", "label": "Collection",
                                 "field": "collection", "align": "left"},
                                {"name": "total", "label": "Collection objects (local)",
                                 "field": "total", "align": "right"},
                                {"name": "not_eligible", "label": "Not eligible",
                                 "field": "not_eligible", "align": "right"},
                                {"name": "eligible", "label": "Eligible",
                                 "field": "eligible", "align": "right"},
                                {"name": "not_uploaded", "label": "Not yet uploaded",
                                 "field": "not_uploaded", "align": "right"},
                                {"name": "synced", "label": "Matches TaxonWorks",
                                 "field": "synced", "align": "right"},
                                {"name": "diverged", "label": "Diverged",
                                 "field": "diverged", "align": "right"},
                                {"name": "actions", "label": "",
                                 "field": "actions", "align": "right"},
                            ],
                            rows=rows, row_key="repo_id",
                        ).classes("w-full").props("flat dense")
                        table.add_slot("body-cell-actions", """
                            <q-td :props="props">
                                <q-btn flat dense no-caps size="sm" icon="fact_check"
                                    label="Check"
                                    @click="$parent.$emit('check_repo', props.row)" />
                            </q-td>
                        """)
                        table.on("check_repo", lambda e: _on_check_repo(e.args))
                        for _col in _CLICKABLE_COLS:
                            table.add_slot(f"body-cell-{_col}", f"""
                                <q-td :props="props" class="text-right"
                                    style="cursor:pointer"
                                    @click="$parent.$emit('open_col',
                                        {{repo_id: props.row.repo_id, col: '{_col}'}})">
                                    <span style="text-decoration:underline dotted">
                                        {{{{ props.row.{_col} }}}}
                                    </span>
                                </q-td>
                            """)
                        table.on("open_col", lambda e: _on_open_col(e.args))
                        report_state["table"] = table

                        reasons_by_repo = report_state["reasons"]
                        if any(reasons_by_repo.values()):
                            with ui.expansion("Why some are not eligible") \
                                    .classes("w-full mt-2"):
                                for r in rows:
                                    reasons = reasons_by_repo.get(r["repo_id"]) or ()
                                    if not reasons:
                                        continue
                                    ui.label(r["collection"]).classes(
                                        "text-xs font-medium mt-1")
                                    for cat, why in reasons:
                                        ui.label(f"{cat} — {'; '.join(why)}") \
                                            .classes("text-xs") \
                                            .style("color:var(--tp-base-soft)")

                        ui.label(
                            "Eligible = Not yet uploaded + Matches TaxonWorks + "
                            "Diverged (each specimen is exactly one of the three — "
                            "\"Diverged\" specimens are on TaxonWorks too, just with "
                            "at least one field that differs)."
                        ).classes("text-xs mt-2").style("color:var(--tp-base-soft)")
                        ui.label(
                            "The three show \"Check pending\" until that collection's "
                            "own Check has run — one TaxonWorks lookup per local "
                            "catalog number, a few seconds even for a large shared "
                            "project."
                        ).classes("text-xs").style("color:var(--tp-base-soft)")

                _render_report()
                # So a Settings save (e.g. the privacy-consent policy) can push a live
                # refresh here without the user needing to leave this tab first — the
                # same cross-tab refresh mechanism the taxonomy tree / print queue /
                # Explore already use (main.py's `_refreshers`), not a bespoke one.
                refreshers["twsync"] = _render_report

                compare_status = ui.label("").classes("text-sm mt-2") \
                    .style("color:var(--tp-base-soft)")
                compare_results = ui.column().classes("w-full mt-1")

                def _reconcile_for(repo_id: int) -> None:
                    data = state["compare"].get(repo_id)
                    if data is None or not data["checks"]:
                        return
                    try:
                        with session_factory() as s:
                            with s.begin():
                                n = tw_sync.reconcile_otu_ids(s, data["checks"])
                    except Exception as exc:                    # noqa: BLE001
                        ui.notify(f"Failed: {exc}", type="negative", multi_line=True)
                        return
                    host_after = urlsplit(get_config().tw_base).netloc
                    ui.notify(f"Re-pointed {n} OTU ids to {host_after}.",
                              type="positive")
                    _render_compare_results(repo_id)

                def _render_compare_results(repo_id: int) -> None:
                    compare_results.clear()
                    data = state["compare"].get(repo_id)
                    if data is None:
                        return
                    result: tw_compare.CompareResult = data["result"]
                    checks = data["checks"]
                    with compare_results:
                        ui.separator().classes("my-2")
                        # The counts themselves already live in the report table above
                        # (updated in place after this check) — repeating them here as
                        # a second row of badges was redundant. Only what the table has
                        # no column for gets a badge: duplicates / withheld-but-on-TW
                        # are rare enough that a silent "0" column would be wasted
                        # space, so they surface here instead, only when non-zero.
                        if result.duplicates or result.leaked:
                            with ui.row().classes("gap-3 items-center flex-wrap"):
                                if result.duplicates:
                                    ui.badge(
                                        f"{len(result.duplicates)} duplicate catalog "
                                        f"number(s) on TaxonWorks"
                                    ).props("color=negative")
                                if result.leaked:
                                    ui.badge(
                                        f"{len(result.leaked)} on TaxonWorks but "
                                        f"withheld locally"
                                    ).props("color=negative")

                        # OTU-id provenance — shown only here, contextually, as a fact
                        # about this check rather than a standing warning banner.
                        current_host, recorded_host, trusted = \
                            tw_sync.otu_instance_state()
                        if not trusted:
                            ui.label(
                                f"⚠ OTU ids stored locally were captured on "
                                f"{recorded_host or 'an unknown instance'}; this "
                                f"server is {current_host} — a stored id may name a "
                                f"different entity here."
                            ).classes("text-sm text-amber-700 mt-2")
                        with ui.element("div").classes("inline-flex mt-1"):
                            rec_btn = ui.button(
                                "Reconcile OTU ids by name", icon="sync") \
                                .props("no-caps color=secondary")
                            rec_btn.set_enabled(bool(checks))
                            if not checks:
                                ui.tooltip("No names were checked for this collection")
                        rec_btn.on_click(lambda: _reconcile_for(repo_id))

                        if result.not_on_tw:
                            with ui.expansion(
                                    f"Not yet uploaded ({len(result.not_on_tw)})") \
                                    .classes("w-full mt-2"):
                                for cat in result.not_on_tw:
                                    ui.label(cat).classes("text-xs")

                        if result.diverged:
                            with ui.expansion(f"Diverged ({len(result.diverged)})") \
                                    .classes("w-full mt-2"):
                                for d in result.diverged:
                                    ui.label(d.catalog_number).classes(
                                        "text-xs font-medium mt-1")
                                    for fd in d.field_diffs:
                                        ui.label(f"· {fd}").classes("text-xs") \
                                            .style("color:var(--tp-base-soft)")
                                    ui.link(
                                        "Open in TaxonWorks",
                                        tw_compare.edit_url(d.tw_object_id),
                                        new_tab=True,
                                    ).classes("text-xs")

                        if result.duplicates:
                            with ui.expansion(
                                    f"Duplicate catalog numbers on TaxonWorks "
                                    f"({len(result.duplicates)})") \
                                    .classes("w-full mt-2"):
                                for dup in result.duplicates:
                                    ui.label(
                                        f"{dup.catalog_number} — appears "
                                        f"{len(dup.tw_object_ids)}×"
                                    ).classes("text-xs font-medium mt-1")
                                    for tid, code in zip(
                                            dup.tw_object_ids, dup.institution_codes):
                                        ui.link(
                                            f"{code or '(no institutionCode)'} — "
                                            f"open in TaxonWorks",
                                            tw_compare.edit_url(tid), new_tab=True,
                                        ).classes("text-xs")

                        if result.leaked:
                            with ui.expansion(
                                    f"On TaxonWorks but withheld locally "
                                    f"({len(result.leaked)})") \
                                    .classes("w-full mt-2"):
                                for lk in result.leaked:
                                    ui.label(
                                        f"{lk.catalog_number} — {'; '.join(lk.reasons)}"
                                    ).classes("text-xs font-medium mt-1")
                                    ui.link(
                                        "Open in TaxonWorks",
                                        tw_compare.edit_url(lk.tw_object_id),
                                        new_tab=True,
                                    ).classes("text-xs")

                        ui.label(
                            "Associated media is not compared — TaxonWorks' "
                            "projection carries no key shared with our media_attachment "
                            "rows to match on, so this check does not claim media is "
                            "the same (#149 step 1.6, not yet built)."
                        ).classes("text-xs mt-2").style("color:var(--tp-base-soft)")

                        _render_blocking_names(checks)

                async def _on_check_repo(row: dict) -> None:
                    if state["checking_repo"] is not None:
                        ui.notify("A check is already running.", type="warning")
                        return
                    repo_id = row["repo_id"]
                    state["checking_repo"] = repo_id
                    compare_status.set_text(f"Checking {row['collection']}…")
                    try:
                        with session_factory() as s:
                            cos = (
                                s.query(CollectionObject)
                                .filter(CollectionObject.repository_id == repo_id)
                                .all()
                            )
                            taxa_list = tw_sync.taxa_to_export(s, cos)
                            catalog_numbers = [co.catalog_number for co in cos]

                        def _on_progress(done: int, total: int) -> None:
                            compare_status.set_text(
                                f"Checking {row['collection']} — looked up {done} "
                                f"of {total} catalog number(s) on TaxonWorks…"
                            )

                        tw_by_cat = await tw_compare.fetch_tw_rows_for_catalog_numbers(
                            catalog_numbers, on_progress=_on_progress)

                        compare_status.set_text(
                            f"Checking {row['collection']} — checking "
                            f"{len(taxa_list)} name(s) against TaxonWorks…"
                        )
                        with session_factory() as s:
                            cmp_result = tw_compare.compare_repository(
                                s, tw_by_cat, repository_id=repo_id)
                            # `check_names` needs a live session for the whole call —
                            # see the Export step's identical note below; this is
                            # bounded by tw_sync's own concurrency limit, and no
                            # session is held across the TaxonWorks lookups above.
                            checks = await tw_sync.check_names(s, taxa_list)
                    except tw_svc.TaxonWorksUnreachable as exc:
                        ui.notify(str(exc), type="negative", multi_line=True,
                                  timeout=8000)
                        compare_status.set_text("Check failed.")
                        return
                    finally:
                        state["checking_repo"] = None

                    state["compare"][repo_id] = {
                        "result": cmp_result, "checks": checks,
                        "label": row["collection"],
                    }
                    compare_status.set_text(f"Checked {row['collection']}.")

                    # Re-derive the whole report rather than patching individual
                    # fields on the old row: `eligible`/`not_eligible`/the "why not
                    # eligible" reasons all come from `export_decision`, which reads
                    # `config.tw_export_nonconsent` (and the confidential flags)
                    # fresh on every call — but the report table's *initial* numbers
                    # are from page-build time (`_render_report()` runs once). A
                    # config change made after that point only ever reached the
                    # visible table by patching it back in somewhere; re-running the
                    # same load-and-render this check already paid the query cost
                    # for is simpler than hand-picking which fields to patch, and
                    # cannot drift from it. (This was the actual bug behind
                    # "changing the config isn't applied until restart" — restarting
                    # just forces a fresh page load, which was never the real fix.)
                    _render_report()

                    _render_compare_results(repo_id)

        # ── Step 2 — Export to TaxonWorks (#149 "Step 3: Emit the export spreadsheet") ──
        with ui.tab_panel("export").classes("p-0"):
            with ui.card().classes("w-full max-w-5xl mx-auto shadow-sm"):
                ui.label("Export to TaxonWorks").classes("section-label")
                ui.label(
                    "Check what is ready to export, then download a spreadsheet to "
                    "upload in TaxonWorks' DwC-Archive importer."
                ).classes("text-sm").style("color:var(--tp-base-soft)")
                card2_wc_line = ui.label().classes("text-sm font-medium mt-1")

                def _sync_card2_wc_line() -> None:
                    card2_wc_line.set_text(
                        f"Working collection: {state['repo_code']}"
                        if state["repo_id"] is not None
                        else "⚠ No working collection chosen in step 1 — pick one "
                             "before checking."
                    )

                # ── Scope (optional) — restrict to one taxon and its descendants ──
                # Session-only — starts empty on every page load (see module docstring
                # for why this is not a DB-backed "remembered" setting).
                ui.label("Restrict to a taxon (optional)").classes(
                    "text-sm font-semibold")
                ui.label(
                    "Only specimens currently determined within this taxon (and its "
                    "descendants) are checked and exported — e.g. Curculionoidea "
                    "only. Leave empty to export the whole collection."
                ).classes("text-xs").style("color:var(--tp-base-soft)")
                with ui.row().classes("w-full items-start gap-2 mt-1"):
                    scope_state = build_taxon_search(
                        session_factory, sources=("local",),
                        placeholder="e.g. Curculionoidea — leave empty for the whole "
                                    "collection",
                    )

                ui.separator().classes("my-3")

                # ── Step 1 — check ──
                ui.label("1 · Check collection").classes("text-sm font-semibold")
                with ui.row().classes("items-center gap-3 mt-1"):
                    check_btn = ui.button("Check collection", icon="fact_check") \
                        .props("no-caps color=secondary")
                    check_status = ui.label("").classes("text-sm") \
                        .style("color:var(--tp-base-soft)")

                results = ui.column().classes("w-full mt-2")

                def _scoped_cos(s, scope_id) -> list[CollectionObject]:
                    """The working collection's specimens, restricted to the optional
                    taxon scope. Re-run per session that needs the rows — the returned
                    instances must never outlive `s` (see `_check_collection`).

                    One collection at a time (required, not a convenience) —
                    TaxonWorks' DwC-A import maps to a single Namespace per upload, so
                    a file mixing rows from two collections has nowhere consistent to
                    land.
                    """
                    q = (
                        s.query(CollectionObject)
                        .filter(CollectionObject.repository_id == state["repo_id"])
                        .order_by(CollectionObject.catalog_number)
                    )
                    if scope_id is not None:
                        # Every descendant (species under a genus, subspecies under a
                        # species, …) counts as "in scope" — the same expansion Batch
                        # tools uses for "all specimens of a taxon" (batch_ops.py).
                        scope_ids = descendant_taxon_ids(s, scope_id)
                        q = q.join(
                            TaxonDetermination,
                            (TaxonDetermination.collection_object_id
                             == CollectionObject.id)
                            & (TaxonDetermination.is_current == 1),
                        ).filter(TaxonDetermination.taxon_id.in_(scope_ids))
                    return q.all()

                async def _check_collection() -> None:
                    if state["repo_id"] is None:
                        ui.notify("Pick a working collection first.", type="warning")
                        return
                    check_btn.props("loading")
                    check_status.set_text("Checking collection…")
                    scope_id = scope_state.get("taxon_id")
                    try:
                        # Only catalog numbers (plain strings) may leave this session.
                        # The TaxonWorks lookup below runs with no session open, and
                        # `export_occurrences` afterwards lazy-loads collecting_event /
                        # determinations / repository off each specimen — which raises
                        # DetachedInstanceError once the loading session has closed. So
                        # the second pass re-runs the query in its own session instead
                        # of carrying ORM rows across the await.
                        with session_factory() as s:
                            catalog_numbers = [
                                co.catalog_number for co in _scoped_cos(s, scope_id)
                            ]
                        total_n = len(catalog_numbers)

                        # #149 Step 3.1, verbatim: "Determine collectionObjects that
                        # are eligible for export (Step 1 point 2) AND not on
                        # TaxonWorks." An already-uploaded specimen must never reach
                        # the file — TW's importer is CREATE-ONLY (CLAUDE.md §5), so
                        # re-including it would duplicate the record, not update it.
                        # This also keeps the taxon/OTU check below scoped to names
                        # actually needed by *new* rows — an already-uploaded
                        # specimen identified only to a high rank (e.g. tribe) no
                        # longer wrongly blocks the download for everyone else.
                        def _on_progress(done: int, total: int) -> None:
                            check_status.set_text(
                                f"Checking which of {total} specimen(s) are "
                                f"already on TaxonWorks… {done}/{total}"
                            )
                        tw_by_cat = await tw_compare.fetch_tw_rows_for_catalog_numbers(
                            catalog_numbers, on_progress=_on_progress)
                        already_uploaded = {
                            cat for cat, rows in tw_by_cat.items() if rows
                        }

                        with session_factory() as s:
                            cos = [co for co in _scoped_cos(s, scope_id)
                                   if co.catalog_number not in already_uploaded]
                            already_n = total_n - len(cos)
                            result = dwc_export.export_occurrences(s, cos)
                            taxa_list = tw_sync.taxa_to_export(s, cos)
                            check_status.set_text(
                                f"Checking {len(taxa_list)} name(s) against "
                                f"TaxonWorks…"
                            )
                            # `check_names` needs a live session for the whole call —
                            # it composes each name from ORM attributes while the
                            # network calls are in flight (see its own docstring) —
                            # the one place in this tab a session spans an `await`,
                            # and it is the service's own requirement, not this UI's.
                            checks = await tw_sync.check_names(s, taxa_list)
                    except tw_svc.TaxonWorksUnreachable as exc:
                        ui.notify(str(exc), type="negative", multi_line=True,
                                  timeout=8000)
                        check_status.set_text("Check failed.")
                        return
                    finally:
                        check_btn.props(remove="loading")

                    state["result"] = result
                    state["checks"] = checks
                    state["already_uploaded_n"] = already_n
                    scope_note = (
                        f" (scope: {scope_state.get('label') or 'whole collection'})"
                    )
                    check_status.set_text(
                        f"Checked {total_n} specimen(s), {len(taxa_list)} "
                        f"name(s){scope_note} — {already_n} already on "
                        f"TaxonWorks, excluded from the file."
                    )
                    _render_results()

                check_btn.on_click(_check_collection)

                ui.separator().classes("my-3")

                # ── Step 2 — download ──
                ui.label("2 · Download spreadsheet").classes("text-sm font-semibold")
                with ui.element("div").classes("inline-flex mt-1"):
                    download_btn = ui.button(
                        "Download spreadsheet (.tsv)", icon="download") \
                        .props("no-caps color=secondary")
                    download_tip = ui.tooltip("Run Check collection first")

                def _sync_download_enabled() -> None:
                    checks = state["checks"]
                    if checks is None:
                        download_btn.set_enabled(False)
                        download_tip.set_text("Run Check collection first")
                        return
                    blocking_n = sum(1 for c in checks if c.blocks_export)
                    if blocking_n:
                        download_btn.set_enabled(False)
                        download_tip.set_text(
                            f"{blocking_n} name(s) are not on TaxonWorks yet — add "
                            f"them there first"
                        )
                    else:
                        download_btn.set_enabled(True)
                        download_tip.set_text("Download the spreadsheet")

                def _download() -> None:
                    result = state["result"]
                    if result is None:
                        return
                    filename = f"taxonworks_export_{datetime.now():%Y%m%d_%H%M}.tsv"
                    ui.download(
                        result.tsv.encode("utf-8"), filename=filename,
                        media_type="text/tab-separated-values",
                    )

                download_btn.on_click(_download)
                _sync_download_enabled()

                # The last step of the workflow lives on TaxonWorks itself — a deep
                # link into its DwC-A Import task, derived from the configured
                # instance (never hardcoded to the sandbox), so it always points at
                # whichever server `tw_base` names. Route verified against the
                # TaxonWorks reference clone's routes.rb: `scope :dwca_import ... get
                # :index, as: 'dwca_import_task'` → `/tasks/dwca_import/index`.
                ui.link(
                    "Open TaxonWorks → DwC-A Import",
                    f"{tw_svc.web_base()}/tasks/dwca_import/index",
                    new_tab=True,
                ).classes("text-sm mt-1")

                # Persistent pre-flight checklist — always visible, not tied to a
                # check result (only item 2's list of names is check-dependent; the
                # rest is always true).
                ui.label("Before uploading in TaxonWorks:").classes(
                    "text-xs font-medium mt-3")
                ui.label(
                    "1. Map (institutionCode, collectionCode) → a Namespace in TW's "
                    "DwC-A Import settings, or every row stages as NotReady (the "
                    "catalogNumber has nowhere to live)."
                ).classes("text-xs")
                prep_line = ui.label(
                    "2. These preparation values must exist as PreparationTypes in "
                    "TW — run Check collection to list them."
                ).classes("text-xs")
                ui.label(
                    "3. The file is TAB-separated, with \" as the string delimiter "
                    "(TaxonWorks' own default) — upload it as-is; a comma file would "
                    "parse as one column."
                ).classes("text-xs")
                ui.label(
                    "4. Consider enabling \"restrict to existing nomenclature\" in "
                    "the import settings, so an import can never silently create a "
                    "new name."
                ).classes("text-xs")

                # ── Results render ──
                def _render_results() -> None:
                    results.clear()
                    result = state["result"]
                    checks = state["checks"] or []
                    with results:
                        if result is None:
                            ui.label(
                                "Run \"Check collection\" to see what is ready to "
                                "export."
                            ).classes("text-sm").style("color:var(--tp-base-soft)")
                            return

                        missing_n = sum(1 for c in checks if c.status == "missing")

                        # ── summary counts ──
                        with ui.row().classes("gap-3 items-center flex-wrap"):
                            ui.badge(f"{result.row_count} ready to export") \
                                .props("color=primary")
                            ui.badge(
                                f"{state.get('already_uploaded_n', 0)} already on "
                                f"TaxonWorks (excluded)"
                            ).props("color=grey")
                            ui.badge(f"{len(result.withheld)} withheld") \
                                .props("color=grey")
                            ui.badge(f"{len(result.refused)} refused") \
                                .props("color=negative")
                            ui.badge(f"{missing_n} names missing from TaxonWorks") \
                                .props("color=warning")

                        _render_blocking_names(checks)

                        # ── advisory notes — collapsed, default closed ──
                        advisory = [
                            c for c in checks if c.status == "match" and c.notes
                        ]
                        if advisory:
                            with ui.expansion(f"Advisory notes ({len(advisory)})") \
                                    .classes("w-full mt-2"):
                                for c in advisory:
                                    ui.label(
                                        f"{c.local_name} — {'; '.join(c.notes)}"
                                    ).classes("text-xs") \
                                        .style("color:var(--tp-base-soft)")

                        # ── withheld — collapsed, labelled with count ──
                        if result.withheld:
                            with ui.expansion(
                                    f"Withheld ({len(result.withheld)})") \
                                    .classes("w-full mt-2"):
                                ui.label(
                                    "Withheld by privacy policy — not an error."
                                ).classes("text-xs") \
                                    .style("color:var(--tp-base-soft)")
                                for cat, reasons in result.withheld:
                                    ui.label(f"{cat} — {'; '.join(reasons)}") \
                                        .classes("text-xs")

                        # ── refused — collapsed ──
                        if result.refused:
                            with ui.expansion(f"Refused ({len(result.refused)})") \
                                    .classes("w-full mt-2"):
                                for rp in result.refused:
                                    ui.label(
                                        f"{rp.catalog_number} · {rp.column} — "
                                        f"{rp.message}"
                                    ).classes("text-xs")

                        # ── redacted — one line + collapsed detail ──
                        if result.redacted:
                            ui.label(
                                f"{len(result.redacted)} row(s) export with the "
                                f"collector's name removed."
                            ).classes("text-sm mt-2")
                            with ui.expansion(
                                    f"Show details ({len(result.redacted)})") \
                                    .classes("w-full"):
                                for cat, notes in result.redacted:
                                    for note in notes:
                                        ui.label(f"{cat} — {note}") \
                                            .classes("text-xs") \
                                            .style("color:var(--tp-base-soft)")

                        # ── preparations used — feeds pre-flight checklist item 2 ──
                        if result.preparations_used:
                            prep_line.set_text(
                                "2. These preparation values must exist as "
                                "PreparationTypes in TW: "
                                + ", ".join(result.preparations_used) + "."
                            )
                        else:
                            prep_line.set_text(
                                "2. No preparation values are used by the rows "
                                "ready to export."
                            )

                        _sync_download_enabled()

                _render_results()
                _sync_card2_wc_line()

        # ── Step 3 — What cannot be exported (static, always visible) ──────────────
        with ui.tab_panel("cannot").classes("p-0"):
            with ui.card().classes("w-full max-w-5xl mx-auto shadow-sm"):
                ui.label("What cannot be exported").classes("section-label")
                ui.label(
                    "TaxonWorks' DwC importer has no path for these — create them "
                    "in TaxonWorks by hand."
                ).classes("text-sm").style("color:var(--tp-base-soft)")
                with ui.expansion("Show details").classes("w-full mt-1"):
                    for line in (
                        "Biological associations — associatedTaxa is [Not mapped].",
                        "Media / depictions.",
                        "Life-stage history.",
                        "Disposition.",
                        "Other catalog numbers (otherCatalogNumbers).",
                        "Synonymy — taxonomicStatus is [Not mapped]; TW derives "
                        "status from the name it matches, not from an imported "
                        "value.",
                        "municipality / locality are dropped by TW's importer, "
                        "which is why our export folds them into verbatimLocality.",
                    ):
                        ui.label(f"· {line}").classes("text-xs") \
                            .style("color:var(--tp-base-soft)")

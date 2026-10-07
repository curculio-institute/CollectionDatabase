# Application structure and data safety

> Moved verbatim out of `CLAUDE.md` on 2026-10-07 to keep the always-loaded file compact.
> This file is now the **one home** for the detail below; `CLAUDE.md` keeps only the binding
> rule and a link here. `§n` references are `CLAUDE.md` section numbers: §2 principles and §8
> conventions are in `CLAUDE.md`; §4 → `data_model.md`, `vocabularies_geography.md`,
> `taxonomy.md`; §5 / §5b / §5c → `taxonworks.md`; §6 → `app_structure.md`.

## 6. Application structure

### App tabs

`app/ui/main.py` is the **page shell** — head scripts, the tab bar, the unsaved-changes
banner, Settings — and calls one `build_…` function per tab. **Every tab's UI lives in its
own module** (`digitize_tab.py`, `records_tab.py`, `explore.py`, `taxonomy_tab.py`,
`labels_tab.py` — Labels *and* the Print queue —, `import_assign.py`, `bulk_import_tab.py`,
`tw_sync_tab.py`, `batch_tab.py`, `controlled_vocab_tab.py`); the small `*_field.py` /
`*_panel.py` / `*_form.py` files are widgets shared between tabs. Digitize, Taxonomy and
Labels were extracted from `main.py` on 2026-10-06 as pure moves. Still inline in
`main.py`: **Settings**, and the print queue's client-side JavaScript (it talks to
`labels_tab.py` by event name only). A new tab gets its own module; whatever it needs from
the page (`_refreshers`, another tab's handle) is passed in as an argument, never reached
for through `main.py`'s scope. What the page needs back from a tab is a **handle
dict of callables** (`_records_handle["open_specimen"]`, `_taxonomy_handle["on_shown"]`,
`_digitize_handle["reset_layout"]`) — never the tab's internal widgets or state.

| Tab | Purpose |
|-----|---------|
| **Digitize** | Main specimen entry form: collecting event (search/create), taxon (local-first search + TW fallback), sex, count, preparations, notes. Saves to DB. Standard/Visiting modes queue **no** labels (see "Print-queue policy by create mode"); only Mounting queues a sheet. Two layouts (see "Digitize layout modes"). |
| **Records** | View/edit a single specimen or collecting event (search → detail edit form). The shared `specimen_form` / `collecting_event_form` widgets. Reached directly or drilled into from Explore (`open_specimen` / `open_event` handle). |
| **Explore** | Dataset browse/query (#40): one faceted search bar (taxa / geography / collectors) drives a **drawer-order taxa checklist** (family→genus headers, species rows w/ material count + ⚠ needs-attention, expand → lots) and an **events** view; click drills into Records; CSV export. Service: `app/services/explore.py`. Map view is Phase C (not built). |
| **Taxonomy** | Checklist tree (family → synonyms). Filter by rank. Links to TaxonPages. Rebuilds on every tab switch and on every save (via `_refreshers["taxonomy_tree"]`). |
| **Labels** | Generate identifier label batches (4-char codes). Preview + download PDF. Reprint a whole batch if unused. Staged-codes dashboard. Also queues **plain identification labels** (no specimen) — N copies of a chosen name + determiner/date/sex/type status/qualifier. |
| **Print queue** | Preview and print all staged labels in one grouped PDF (per queue addition; data/identifier/determination column-aligned per specimen). Saves the PDF to `printed_pdf_dir` on print, then clears the queue. |
| **Import** | Two sub-tabs under one tab. **Import & Assign** — the row-by-row retroactive-digitisation flow (upload a DwC CSV; live-filter rows; assign taxon + per-specimen fields; save). **Bulk import** — wholesale, staged import of specimen **records** (#39), modelled on TaxonWorks' Import Dataset: upload → stage every row (writes nothing) → status grid + per-reason blocker list → import the `ready` rows, resumable via a cursor. Service: `app/services/bulk_import.py`. See "Bulk import" below. |
| **TaxonWorks** | Sync with the TaxonWorks mirror (#149). Only present when a connection is configured (`taxonworks_enabled`). Checks every taxon name against the configured instance **before** anything is emitted, reports withheld (privacy) / refused (TW would reject) / redacted specimens, emits the DwC **TSV** for TW's DwC-A Import, and reconciles per-instance OTU ids. See §5c for the contract. |
| **Batch tools** | Build a **collection-scoped** specimen set — by taxon (all specimens of a taxon + descendants in the working collection) or by a pasted catalog-number list — then bulk-apply one op: set disposition, or reassign to another collection (#78). Working collection defaults to the home collection; an extra click switches to another. Cross-collection specimens can **never** be listed or modified (see below). Service: `app/services/batch_ops.py`. |

#### Digitize layout modes (decided)

The Digitize tab offers two layouts, selectable in Settings and persisted as
`AppConfig.digitize_layout` (`"normal"` | `"single_card"`); the toggle applies **live**
(no page reload, so unsaved form entry survives the switch). Both render the *same* cards —
the choice only changes width and which cards are visible (one shared card tree, no
duplicate form):

- **Normal (default):** wide page (`max-w-7xl`) with **Specimen and Identifications paired
  side-by-side**, Collecting Event + Biological Associations full-width below — fits more on
  one screen, less scrolling. (Chosen over a full two-column layout, which read as
  distracting.)
- **Single card (guided stepper):** one card at a time (Specimen → Identifications →
  Collecting Event → Biological Associations) with a clickable step bar, Back/Next, and
  ←/→ arrow keys. **A specimen is still one Save** — the stepper only changes which card is
  visible and never commits per card; the single real Save lives on the last step. **Mounting
  mode ignores the stepper** (it keeps its own multi-specimen staging layout).

**A row entered but not added is never dropped silently.** An identification or biological
association sitting in its add row is not part of the specimen yet; Save stops and asks
(add it and save / save without it / cancel) instead of discarding it, and the check panel
beside the form shows it — together with missing event essentials and existing events at the
same coordinates and date — before Save is pressed. Build detail → design.md "Check panel".

**Why this is a policy, not an incidental:** a specimen record is atomic (specimen + IDs +
event + associations save together), so "commit a card and advance" can only mean *advance
the view*, never a partial DB write. The build detail (single-source visibility function,
arrow-key event, chip styling) is design.md's concern → "Digitize layout modes".

### Service layer (`app/services/`)

| Module | Responsibility |
|--------|---------------|
| `taxa.py` | Taxon search, TW import, parent-row creation, name composition (`compose_scientific_name`/`recompose_subtree`/`element_from_name`), `format_scientific_name()` |
| `taxonomy.py` | Checklist tree builder, stats, filter options |
| `taxonworks.py` | All TW API calls (async). Token hardcoded as `TW_TOKEN` at the top of the file. |
| `events.py` | Collecting event CRUD + search |
| `specimens.py` | `CollectionObject` + `TaxonDetermination` creation |
| `identifiers.py` | `reserve_sequential_codes(coll_code, n)` → `(batch_id, codes_list)` — always unpack the tuple |
| `labels.py` | WeasyPrint HTML → PDF for data (locality/date/collector), determination, and identifier labels |
| `print_queue.py` | Stage + retrieve + clear print-queue items |
| `dwc_import.py` | Parse DwC CSV, field aliasing, row-to-form-field mapping |
| `media.py` | Content-addressed media store (store/dedup/verify/delete) + attachment CRUD (attach to specimen/event/association) |
| `external_ids.py` | External resource identifier CRUD (attach to specimen/association) |
| `life_stage.py` | Reared-specimen life-stage history CRUD + `life_stage_facets()` export projection (Phase 3) |
| `vocab.py` | Generic single-name controlled-vocabulary service (`Vocabulary`: list/options/get_or_create/update/delete/merge, dynamic FK re-pointing) |
| `vocabularies.py` | Vocabulary instances + `VOCAB_REGISTRY` (the Controlled Vocabularies tab renders one section per entry) |
| `repositories.py` | Collections/institutions CRUD (multi-column vocab, #56) + `name_map` (collectionCode→full name) for the identifier label + `display_label` (the one human rendering — `JJPC — Name`, or just the name for a codeless collection) + `get_or_create_by_name` (the collection picker's save-time seam for `collection_object.repository_id`; a new collection gets a name and nothing else) + `delete_repository` guard (blocked while specimens reference it, #72) |
| `explore.py` | Explore-tab querying (#40): `search_facets`, `query_specimens(filters)`, `checklist(filters)` (drawer-order taxa+lots), `events(filters)`, `to_csv`, `counts` |
| `name_source.py` | The generic offline-name-source engine (DwC Archive → SQLite index → search → import chain). WCVP is one instance; user datasets are others. See "Offline name sources" below |
| `datasets.py` | User-added name datasets (**experimental**): install from a chosen file → `data/name_sources/<slug>/`, rebuild, remove, `import_all` |
| `bulk_import.py` | Bulk import (#39): staged wholesale import, two `kind`s — `create_occurrence_dataset` (records, the primary path: catalogNumber required, collection column, dedup on `UNIQUE(repository_id, catalogNumber)`, reuses the event/determination save + the taxon resolver) and `create_taxon_dataset` (name checklist / internal resolver). Shared: `restage` / `set_dataset_code` (resolve-once) / `import_ready` (resumable) / `retry_errored` / `progress` / `blocker_summary`. |
| `dwc_export.py` | DwC export (#149): `export_decision` (the single owner of "may this specimen leave the building, and with which fields blanked" — read by both the comparison and the writer, so they cannot disagree), `occurrence_row` (the projection), `export_occurrences` (validate + write the TSV). See §5c. |
| `tw_sync.py` | TaxonWorks sync (#149): `check_names` (the pre-flight gating any export), `taxa_to_export`, `otu_instance_state` / `reconcile_otu_ids` (per-instance OTU ids, verified in reverse). Read-only except `reconcile_otu_ids`. |
| `batch_ops.py` | Batch tools (#78): `fetch_by_taxon` / `match_catalog_numbers` (both **scoped to a working `repository_id`**) + `apply_disposition` / `apply_repository`. **Cross-collection safety is structural** — `_load_in_scope` re-asserts every specimen belongs to the working collection before any write, so a bulk op can never touch a specimen held in another collection. `catalog_number` is never changed. |

### Data safety (crash recovery + unsaved-changes guard) — decided

The durability guarantee and the three mechanisms that back it (#41):

- **Committed data is durable; in-progress edits are not.** WAL + atomic transactions
  (`database.py`) mean a crash can never leave half a specimen in the DB — committed Saves
  survive. What is lost on a crash or page-close is whatever was typed into a form but not
  yet Saved. This is by design (DB-is-source-of-truth, no half-records), and the guard below
  covers that gap.
- **Startup checks (`app/services/db_safety.py`, run from `run.py` before the UI serves).**
  In order: WAL-checkpoint → **launch snapshot** → thorough `PRAGMA integrity_check`. On
  anything but `ok` the page shows a blocking red banner ("integrity check FAILED") rather
  than letting the user keep working on a damaged file (CLAUDE.md §2). The result is cached
  in `db_safety.LAST_RESULT`; the `@ui.page` handler reads it. `integrity_check` swallows a
  "disk image is malformed" *exception* into a reported problem so severe corruption still
  trips the banner. Tested in `tests/test_db_safety.py`.
- **Snapshots** land in `data/snapshots/collection-<timestamp>.db` (gitignored with `data/`),
  one per launch, **keep last 10** (`DEFAULT_KEEP`). Checkpoint-first so each copy is
  self-contained (the WAL caveat above). Pruned by the timestamp embedded in the filename,
  **not** mtime — `copy2` copies the source's mtime, so mtimes are not creation order.
- **Unsaved-changes detection + banner.** A scope-aware bottom banner ("Unsaved changes in:
  *tab*") plus a `beforeunload` guard fire while a data-entry area has unsaved edits (in-app
  tab switches keep the SPA alive, so they never warn). **The banner lists only areas other
  than the one in view** (`window.tpSetActiveScope`, pushed on every tab / Import sub-tab
  switch): edits in the open tab are in plain sight, so it is purely a reminder of what was
  left behind elsewhere; the `beforeunload` guard still counts every area. Detection is **value-based on every
  data-entry tab** (Digitize, Records, Import & Assign — #41, #47): a per-tab `ui.timer`
  polls the form's real field *values* via `has_content()` and pushes the scope to the client
  via `window.tpSetScope()` only when the boolean flips. This is deliberate — **DOM
  event-based detection only catches typed input**, missing programmatic fills (map picker,
  Tier-2 push-pins, reverse-geocode), which would leave values in fields the app is unaware
  of. There is no longer any `.tp-dirty-scope`/`input`/`change` head-script listener.
  - **Digitize** — `_has_any_content()` aggregates each card's `has_content()`; clears when
    every card is cleared.
  - **Records** — "changed since loaded": after a specimen/event loads, a baseline of the
    editable specimen + collecting-event field values is snapshotted (`_norm` treats None ≡
    empty, strips strings); `has_content()` = current ≠ baseline. Editing a field back to its
    loaded value correctly clears the banner. The determination / association sub-cards save
    immediately on their own and are not part of this check.
  - **Import & Assign** — `has_content()` = an assign card is open (a row staged for
    assignment, not yet saved); it self-clears when the card hides on save.

  Python also clears a scope at every deliberate reset via `window.tpClearDirty(label)`
  (`_mark_form_clean(scope)`): after a save and after a Digitize mode switch (the timer
  agrees on the next tick).
- **Mode-switch confirm + per-card Clear.** Switching Digitize mode wipes the form, so it
  first asks "Discard unsaved data?" — but only when a card actually holds content
  (`_has_any_content()` aggregates each card's `has_content()`). Each of the four Digitize
  cards (Specimen, Identifications, Collecting Event, Biological Associations) has a header
  **Clear** button to reset just its own uncommitted fields.

## 7. Build phases — current status

**Phase 1 — Database structure.** ✅ **Complete.**
18 migrations. 6 STRICT core tables + label/print-queue models. 44 tests. All constraints
verified (FK enforcement, exclusive-arc CHECK, coordinate bounds).

**Phase 2 — Frontend.** ✅ **Largely complete.**
NiceGUI app with 5 tabs: Digitize, Taxonomy, Labels, Print queue, Import & Assign.
Leaflet map view: not yet built (coordinates stored, map tab deferred).
Biological association UI: CRUD in DB, UI not yet built.

**Phase 3 — Validation, export, sync tools.** 🟡 **Started (#149).**
- *DwC export:* ✅ **built** — `dwc_export.py` emits the occurrence **TSV** (not CSV: TW's flat
  upload defaults to tab), gated on the name pre-flight. See §5c for the whole contract; the
  eligibility/privacy policy is `export_decision`. No `TW:` columns are emitted yet.
- *Sync diff:* ✅ **built** — Step 1 of #149, `tw_compare.py` + the TaxonWorks tab's
  Collections step. Existence is answered by the `/identifiers` catalog-number index (**not**
  `dwc_occurrences`, where `catalogNumber` is populated on 1/500 rows and which lags a fresh
  import); `dwc_occurrences` supplies only the field values, and only for catalog numbers the
  index says are there. The two scopes differ on purpose: existence is namespace-**un**scoped
  (a specimen under another namespace is still on TW, and per-namespace identifier uniqueness
  means TW would accept the re-upload silently), the orphan sweep is namespace-scoped. The
  Export step's "already uploaded" exclusion reads the same index, so the file can never
  re-carry a specimen TW already holds.
- *Media compare (step 1.6):* ✅ **built** — `tw_media_compare.py`, reusing the same
  `/identifiers` index the occurrence compare already fetched. Matches local
  `media_attachment` files to TW `Depiction`/`Image` rows by MD5 (`media.md5_fingerprint`,
  migration 0070 — TW's `image_file_fingerprint` is MD5 despite its OpenAPI doc claiming
  SHA256; verified live against sandbox.taxonworks.org). Diagnostic only: TW's DwC importer
  has no media path and there is no public API route to create an Image, so the tab can
  only report local-only files with a deep link to TW's own upload UI and a "Prepare
  files" button that stages them in a temp folder for drag-and-drop.
- *Validation script:* ⬜ required DwC fields, coordinate bounds, determination completeness.
  (Row-level validity for the export specifically is already enforced by `_validate_row`, which
  refuses rather than rewrites — §5c.)
- *Habitat enrichment (GeoPandas):* ⬜ uncertainty-aware spatial join against a European
  habitat layer (EUNIS or CORINE; CRS likely EPSG:3035 / ETRS89-LAEA).

---

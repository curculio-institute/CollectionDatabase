# CLAUDE.md

Project context and working agreement for Claude Code. Read this before writing code.

**Also read `docs/design.md`** — the working design document covering UI conventions,
widget specs, and layer architecture. It overrides CLAUDE.md where they conflict.

---

## To do:
- Workflows: getting printing of locality labels in order, get print queue in order
- Workflows
  - import whole dataset: will need some work on things like having taxon names that are not linked to parentNameUsageID
  - data analysis tools, map of the collection
  - data security: what happens if program crashes unexpectedly? warning when closing page?

---

## Specimen workflows

**Invariant across all workflows:** every specimen in the database must have an identifier
(`catalog_number`). Specimens without identifiers must never be committed. Identifiers are
always physical labels pinned with the specimen.

### Workflow 1 — Retroactive digitisation (Import & Assign)

Use case: specimens already in the collection have data/identification labels but no
identifier label. A reference table with the relevant data exists, but it may contain
records for specimens no longer in the collection, so it cannot be imported wholesale.

Process:
1. Pre-print a batch of identifier labels (Labels tab → reserve codes → print).
2. Go through the physical collection specimen by specimen.
3. In the Import & Assign tab, search for the specimen's data (by date, locality, taxon,
   etc.) and select the matching row from the reference table.
4. Take one pre-printed identifier label, pin it with the specimen.
5. Select that code from the "generated but unused" identifier dropdown.
6. Save → the complete record (data + identifier) is committed to the DB.

Only specimens physically found in the collection ever get a database record. Records in
the reference table that have no matching physical specimen are simply never selected.

### Workflow 2 — New incoming specimen (Digitize tab)

Use case: a fresh specimen arrives and needs to be recorded immediately.

Process:
1. Pre-print identifier labels (can be done in bulk ahead of time).
2. Open the Digitize tab, fill in all fields (collecting event, taxon, sex, count, etc.).
3. Select an unused identifier code from the dropdown.
4. Pin the corresponding label with the specimen and save.

### Workflow 3 — Mounting session (not yet built)

Use case: mounting a batch of specimens that have no labels at all. Need to produce labels
during or immediately after mounting; identifiers may or may not be assigned at this stage.

Key design decisions:
- **Identifier timing:** assign identifiers during mounting (immediate). Identifiers are
  pre-printed in a batch before the mounting session; the user selects one per specimen.
- **Label layout — adjacent sheet layout (decided):** data label and its matching identifier
  label are printed on the same sheet, adjacent in paired rows (data row / identifier row /
  small gap / next pair). Physical proximity on the sheet prevents mix-ups without embedding
  the code in the data label itself.

**Print queue model implication (decided):** the adjacent layout is implemented as a **pair
of separate print queue rows** — one `"data"` row (`collection_object_id`) + one
`"identifier"` row (`label_code_id`) — rather than a combined row type linking both FKs.
The print service interleaves them into the paired sheet layout. This keeps the exclusive-arc
constraint on `print_queue` valid and avoids a special-case label type.

This workflow requires a new UI task (staging tab or modal): the user stages N specimens
that share the same collecting event, sets the specimen count, queues them for printing,
and the print sheet is generated in one go. Identifications are added later via the Records
tab.

### Print-queue policy by create mode (decided)

Every specimen-create path runs the same finalization tail through one seam,
`app/services/specimens.py::finalize_specimen(session, *, collection_object_id, code,
queue_labels=False, associations=())`: bind the reserved identifier code, optionally
queue labels, and persist biological associations. Which modes queue labels is a
deliberate policy, **not** an incidental of each tab:

| Mode | `code` | `queue_labels` | Queues to `print_queue` |
|------|--------|----------------|-------------------------|
| **Digitize standard** | reserved code | `False` | **nothing** — identifier is pre-printed in a batch and pinned by hand; the specimen already carries its own data labels. The code is still bound (`assign_code`). |
| **Digitize visiting** | `None` | — | nothing — foreign `catalogNumber`, no reserved code; bio associations still saved. |
| **Mounting** | reserved code | `True` | identifier + data + determination — a freshly mounted specimen needs its whole sheet printed; the identifier label stays beside its data label so the pair can be matched while cutting (see the adjacent-sheet layout above). |

**Rationale:** standard/visiting specimens already have physical labels; only mounting
produces fresh specimens that need a printed sheet. (Historical note: Digitize standard
formerly enqueued data + determination labels — removed 2026-06-12; it now queues nothing.)

**Planned (not built):** re-printing existing records by sending them to the print queue.
The `enqueue_*` services are standalone, so this is an "enqueue for an existing
`collection_object`, no `assign_code`" path; `finalize_specimen` is create-time only and
need not change.

## Known bugs (audit 2026-06-07) — fix one by one

### Critical
- [x] **C-1** `records_tab.py:295` — Fixed: snapshot all det history as plain dicts inside session in `_load_specimen`; removed detached ORM access.
- [x] **C-2** `import_assign.py:~377` — Fixed: use `r["id"]` (matching `taxon_search.py`); `get_or_create_from_tw_data` handles valid-name backfill. Bug only affected determinations in Import & Assign, not the taxonomy table.
- [x] **C-3** `main.py:~1897` — Fixed: added `if co is None` guard with error notification and early return (not silent skip); added `ui.timer(2.0, ...)` to keep `occ_sel` options live.

### Major
- [x] **M-1** `biological.py:78` — Fixed: filter out `[legacy]` relationships entirely from `get_relationship_options()` rather than sorting them last. Legacy rows remain in the DB (existing associations unaffected) but never appear in any dropdown.
- [x] **M-2** `models/print_queue.py` — Fixed: migration 0017 adds `ck_print_queue_label_type` (`IN ('data','determination','identifier')`) and `ck_print_queue_exclusive_arc` (data/determination→co_id only; identifier→label_code_id only).
- [x] **M-3** `models/label_code.py:13` — Fixed: migration 0018 adds `ck_label_code_status` (`IN ('reserved','assigned')`). Also migration 0019 adds `ck_co_basis_of_record` (`IN ('PreservedSpecimen','FossilSpecimen','HumanObservation')`) and `ck_co_disposition` (NULL or 6-value set) to `collection_object`. Note: `recreate="always"` on `collection_object` requires `PRAGMA foreign_keys = OFF` around the batch operation due to child-table FKs.
- [x] **M-4** `taxa.py:449` + `taxon_editor.py:24` — Fixed: both typos corrected to `"ICVCN"`. Existing Viruses root row patched directly in DB (one-off data fix; seed is idempotent so no migration needed).

### Minor
- [ ] **m-1** `taxon_editor.py:219` — Delete-eligibility check ignores `TaxonDetermination` rows; delete button enables incorrectly then `delete_taxon()` raises a confusing error on click.
- [ ] **m-2** `bio_object_search.py:310` — `not r.get("synonym", None) is not None` is needlessly confusing; simplify to `r.get("synonym") is None`.
- [ ] **m-3** `import_assign.py` — `island` field missing from DwC CSV row mapping; cannot be populated via Import & Assign.
- [ ] **m-4** `labels.py:401` — `occurrence_sheet()` docstring says "interleaved per specimen" but output is all data labels then all id labels in batches.
- [ ] **m-5** `main.py` + `records_tab.py` + `import_assign.py` — `SEX_OPTIONS`, `SAMPLING_PROTOCOLS`, `DEFAULT_IDENTIFIED_BY`, `DEFAULT_NAMESPACE`, etc. duplicated in three files; centralise.
- [ ] **m-6** `main.py:464` — `_refreshers` populated in tab-rendering order; missing keys would cause `KeyError` if tab order changes. Use `.get()` calls or pre-initialise with `None`.
- [ ] **m-7** `labels.py` vs `label_text.py` — `_data_line1()` duplicates locality-formatting logic already in `format_locality_label()`.
- [ ] **m-8** `services/taxa.py:62` — `create_taxon_manual()` has no `nomenclatural_code` parameter; manually created taxa always get `NULL` code.

## 1. What this project is

A **local-first, single-user desktop application** for maintaining an entomological
specimen collection (primary focus: Coleoptera). The local database is the **source of
truth**. TaxonWorks is treated as a downstream **published mirror** that we keep in sync
one-directionally (local → TaxonWorks), with the API used to *verify* state by comparison.

The data model deliberately stays close to what TaxonWorks ingests via its Darwin Core
(DwC) importer, so that local records translate cleanly to a DwC export for upload. We
additionally model **biological associations** (e.g. a beetle collected on a host plant)
following TaxonWorks' `BiologicalAssociation` / `BiologicalRelationship` structure — with
the important caveat (see §5) that these **cannot be imported via DwC** and are therefore
local-master with no automated push.

---

## 2. Non-negotiable principles

- **Local DB is the single source of truth.** TaxonWorks is a mirror, never the master.
- **Sync is one-directional and insert-only** (local → TW). Never design a flow that
  assumes re-importing updates existing TaxonWorks records — it does not (see §5).
- **Data integrity is paramount.** Prefer DB-enforced constraints over application-level
  hope. Enable `PRAGMA foreign_keys = ON` on every connection; use `STRICT` tables; add
  `CHECK` constraints. A silent wrong value is worse than a loud failure.
- **Data transformation happens in versioned, testable Python scripts**, never via ad-hoc
  or LLM-in-the-loop wrangling. Spatial joins, format conversion, and diffing are scripts
  with deterministic output, committed to the repo, with tests.
- **Fetch from APIs or local files, not web scraping.** External state (TaxonWorks) comes
  from its documented API. Reference layers (habitats) come from files on disk.
- **When a TaxonWorks behaviour is assumed, cite where it was verified** (file:line or API
  route) in a code comment, so assumptions can be re-checked against a future TW release.
- **Determinations may target synonyms.** Recording a determination under a name that is
  later synonymised is valid scientific practice. `taxon_determination.taxon_id` may point
  to any `taxon` row, accepted or synonym. The DwC export resolves to the accepted name
  for upload; the verbatim determination name is preserved in `verbatim_identification`.

---

## 3. Tech stack (decided)

| Layer            | Choice                                  | Notes |
|------------------|-----------------------------------------|-------|
| Language         | **Python**                              | Single language across the whole app. Cross-platform. |
| Store            | **SQLite** via **SQLAlchemy** ORM       | Single-file, archival-grade, std-lib driver. ORM is the migration escape hatch to Postgres if ever needed. |
| Schema migrations| **Alembic**                             | All schema changes go through migrations, never manual DDL on a live DB. |
| App / UI         | **NiceGUI** (on FastAPI)                | Pure-Python UI; renders a real web frontend; run in browser at localhost. |
| Labels / PDF     | **WeasyPrint**                          | Generates tiny specimen labels (≤18×7 mm) as PDF. Micro-font via Context Condensed. |
| Spatial          | **GeoPandas**                           | Habitat enrichment as a standalone batch script (Phase 3, not yet built). |
| Future analytics | **DuckDB**                              | Not the store. Optional later layer. Do not introduce yet. |

**Environment:** conda env named `collection`. Do NOT touch `phylogeny` or `catalogue` envs.
Primary dev OS: Arch Linux; all code must remain cross-platform (Windows/macOS).

---

## 4. Data model (implemented)

22 migrations applied. Schema is in production use.

### DwC column naming convention

Darwin Core fields are stored on SQLite columns named **`dwc:columnName`** (colon separator,
camelCase after the colon). SQLAlchemy `mapped_column` maps these to snake_case Python
attributes. Mermaid diagrams use plain camelCase. Do not deviate from this pattern.

### Core tables

| Table | Purpose |
|-------|---------|
| `collection_object` | One physical specimen or lot. `catalog_number` (NOT NULL) is the stable sync join key. `dwc:basisOfRecord`, `dwc:sex`, `dwc:preparations`, `dwc:typeStatus`, etc. |
| `collecting_event` | Where/when collected; shared by many specimens. Full DwC locality + coordinate block. `dwc:eventDate` supports ISO 8601 intervals (`2024-06-15/2024-06-20`). `dwc:recordedBy` FK → `person(full_name)`. |
| `taxon` | Local OTU analogue. DwC parent-link model (GBIF best practices). Columns: `dwc:scientificName` (bare name without authorship), `dwc:taxonRank`, `dwc:taxonomicStatus` ("accepted"/"synonym"), `dwc:scientificNameAuthorship`, `dwc:parentNameUsageID` (self-FK, encodes hierarchy), `dwc:acceptedNameUsageID` (self-FK, marks synonyms), `taxonworksOtuID`. No denormalised rank columns. |
| `taxon_determination` | `collection_object` → `taxon` link. `is_current` flag. `taxon_id` may reference a synonym row (deliberate design). `dwc:identifiedBy` FK → `person(full_name)`. |
| `biological_relationship` | Kind of association (`collected_on`, `feeds_on`, …). |
| `biological_association` | Exclusive-arc pattern: (`subject_collection_object_id` XOR `subject_taxon_id`) and (`object_collection_object_id` XOR `object_taxon_id`). CHECK enforces exactly-one-non-null per role. |
| `label_code` | 4-char alphanumeric specimen identifiers (`[0-9a-z]{4}`, ~1.7 M possibilities). Tied to a `label_batch`. Once used on a specimen they are immutable. |
| `label_batch` | Groups of `label_code` rows with a `created_at` timestamp. Batches can be reprinted only if no code in the batch has been used yet. |
| `print_queue` | Staged label jobs (identifier, locality, identification types) pending a single print run. Items removed after printing. |
| `person_defaults` | Single-row table holding the two push-pin defaults: `default_identified_by` and `default_recorded_by`. Both columns are `TEXT REFERENCES person(full_name) ON DELETE RESTRICT`. See rationale below. |

### Why person defaults live in the DB, not config.json

`config.json` stores environment settings (TW credentials, institution code, UI prefs) that
should survive a database wipe. Person defaults are different: they are **FK references into
the `person` table**, so storing them outside the DB breaks referential integrity in two ways:

1. **Delete**: a plain JSON string has no FK constraint. Deleting a person who is the
   configured default silently succeeded, and `get_or_create_person` in the save path would
   silently recreate them on the next digitizing save — making the delete a no-op.
2. **Merge**: `merge_persons` re-points all DB FK columns from absorbed → kept via
   `_fk_references_to_person` (dynamic `PRAGMA foreign_key_list` discovery). A JSON value is
   invisible to that mechanism, so the absorbed name would persist as the configured default
   after a merge, recreating the deleted row on next save.

With `person_defaults` in the DB:
- `ON DELETE RESTRICT` blocks delete at the SQLite level — no application check needed.
- `_fk_references_to_person` discovers `person_defaults` automatically, so `merge_persons`
  updates the default alongside all specimen records with no extra code.

**Rule:** never store person name references in `config.json`. Any configurable default that
references a DB entity belongs in the DB, not in a flat file.

**Service:** `app/services/person_defaults.py` — `get_defaults(session)` returns
`(identified_by, recorded_by)`; `set_defaults(session, *, identified_by, recorded_by)`
updates the row. Push-pin `default_fn` closures in UI files open their own session and call
`pd_svc.get_defaults(s)[0/1]`.

### Removed from original design

- **`identifier` table** — dropped (migration 0006). `catalog_number` lives directly on
  `collection_object`; `occurrenceID` is not separately stored at this stage.
- **Denormalised rank columns** — removed (migration 0012). `dwc:family`, `dwc:genus`,
  `dwc:specificEpithet`, etc. replaced by the DwC parent-link model.
- **`dwc:taxonomicStatus`** — originally dropped (migration 0011) as redundant with
  `acceptedNameUsageID`; restored in migration 0012 as an explicit CHECK-constrained
  column (`"accepted"` | `"synonym"`), which is required by the DwC Taxon core.

### Parent-rank taxon rows

Every TW species import creates dedicated `taxon` rows for each ancestor rank (genus,
subgenus, tribe, subfamily, family, order) via `_ensure_parent_rows()` in
`app/services/taxa.py`. Each ancestor row is linked to its own parent via
`dwc:parentNameUsageID`. Rows are matched by `(dwc:scientificName, dwc:taxonRank)`.

`ensure_higher_taxa()` is a no-op in the DwC parent-link model (backfill not needed).

---

## 5. TaxonWorks integration constraints (verified against the codebase)

Verified against `SpeciesFileGroup/taxonworks` `main` @ commit `897f385` (2026-06-03).
Re-verify if targeting a different TW release.

- **The DwC importer is CREATE-ONLY; it does not upsert.**
  `DatasetRecord::DarwinCore::Occurrence#import` calls `Specimen.create!`/`Lot.create!`
  unconditionally (`occurrence.rb:310`) and never looks up an existing CollectionObject by
  `occurrenceID` to update it. Re-uploading overlapping records therefore either
  **duplicates** them or **errors** on identifier-uniqueness collision.
  → **The push path may only carry genuinely new records (inserts).** Compute the delta;
  never re-upload the full dataset.
- **Identity preserved on import:** `occurrenceID` → `Identifier::Local::Import::Dwc`
  (`occurrence.rb:378`); `catalogNumber` → `Identifier::Local::CatalogNumber`. Use
  **`catalogNumber` within a defined namespace** as the authoritative, immutable join key
  for diffing local vs TW. Never mutate it locally once assigned.
- **DwC-Archive extensions are staged but `Unsupported`** (not imported). Do not rely on
  the star-schema extension mechanism for anything.
- **Biological associations CANNOT be imported via DwC.** `associatedTaxa` is `[Not mapped]`
  (`occurrence.rb:948`) and extensions are `Unsupported`. They are therefore **local-master
  with no automated push**: create them manually in the TW UI or via TW's internal CRUD API
  as an out-of-band step. They *can* be **read back** for verification via
  `/api/v1/biological_associations`.
- **Read/compare endpoint:** `/api/v1/dwc_occurrences` (auth via `project_token`) returns a
  DwC projection of all collection objects — diff target for the sync tool. It is a
  **generated/cached projection**, so it may lag behind a fresh import; refresh/confirm
  before diffing.
- **Updates/deletes are not in the v1 API.** Keep sync one-directional; treat any
  update/delete as a manual exception.
- **TW synonym detection:** use `cached_is_valid` (reliable) and `cached_valid_taxon_name_id`
  (reliable) from `/api/v1/taxon_names`. Do NOT use `valid_taxon_name_id` alone — it returns
  `null` for valid names, making it ambiguous. `fetch_full_classification()` in
  `app/services/taxonworks.py` handles this and attaches `_valid_tw_data` / `_valid_otu_id`
  to the data dict when the name is a synonym.
- **TW label_html** contains visual-only badge markup (rank, family context, original
  combination). Strip `feedback-secondary`, `feedback-info`, `feedback-notice` + `feedback-thin`
  badges before display; keep `feedback-warning` (✗/✓ synonym indicators).
- **Beyond standard DwC terms**, TW accepts `TW:`-namespaced columns on the occurrence core
  (`TW:DataAttribute:…`, `TW:Namespace:…`, `TW:TaxonDetermination:otu_id`, etc.).

---

## 5b. TaxonWorks known shortcomings and gaps

Verified against `occurrence.rb` @ commit `897f385` (2026-06-03). Re-verify against a
newer release before relying on any of these being fixed or still present.

### DwC importer field gaps

Fields that exist in DwC and are relevant to this project but are **silently ignored** by
TW's DwC importer (marked `[Not mapped]` in `occurrence.rb`):

| DwC field | occurrence.rb line | Impact |
|---|---|---|
| `taxonomicStatus` | ~1513 | Synonym/accepted status not imported; managed locally only |
| `disposition` | ~834 | Specimen disposition not imported; managed locally only |
| `ownerInstitutionCode` | ~728 | Silently ignored; removed from local DB in migration 0015 |
| `associatedTaxa` | ~948 | Biological associations cannot be imported via DwC at all |

### basisOfRecord — only PreservedSpecimen or FossilSpecimen

TW's DwC importer raises a validation error for any `basisOfRecord` value other than
`PreservedSpecimen` or `FossilSpecimen` (`occurrence.rb:743`). Other standard DwC values
(`HumanObservation`, `MachineObservation`, `MaterialSample`, etc.) are rejected.

Note: TaxonWorks **does** have a `FieldOccurrence` model (distinct from `CollectionObject`)
and **exports** field occurrences as `HumanObservation` in DwC. However, there is no DwC
**import** path for `HumanObservation` → `FieldOccurrence`. Field occurrences can only be
created via TW's internal UI or API. This is a TW limitation, not a DwC standard gap.

### FieldOccurrence has no DwC import path

If field sightings (specimens not collected) become relevant, they cannot be pushed to TW
via the DwC sync path. A separate integration using TW's internal CRUD API would be needed.
This is currently out of scope for this project (all records are preserved specimens).

### sex — no fixed controlled vocabulary at import

TW's importer accepts any single-word string for `sex` and dynamically creates biocuration
classes (`occurrence.rb:786`). There is no fixed vocabulary enforced at import; the
constraint is only that the value contains no whitespace. This means our local `sex` values
will always be accepted, but TW may create duplicate biocuration classes if capitalisation
varies (e.g. "Male" vs "male").

### No update or delete via DwC or v1 API

The DwC importer is CREATE-ONLY (`occurrence.rb:310`). The v1 REST API has no `PATCH` or
`DELETE` for collection objects. Any correction to an already-imported record must be done
manually in the TW UI. This constrains the sync direction to insert-only forever.

---

## 6. Application structure

### App tabs (in `app/ui/main.py`)

| Tab | Purpose |
|-----|---------|
| **Digitize** | Main specimen entry form: collecting event (search/create), taxon (local-first search + TW fallback), sex, count, preparations, notes. Saves to DB. Standard/Visiting modes queue **no** labels (see "Print-queue policy by create mode"); only Mounting queues a sheet. |
| **Taxonomy** | Checklist tree (family → synonyms). Filter by rank. Links to TaxonPages. Rebuilds on every tab switch and on every save (via `_refreshers["taxonomy_tree"]`). |
| **Labels** | Generate identifier label batches (4-char codes). Preview + download PDF. Reprint a whole batch if unused. Staged-codes dashboard. |
| **Print queue** | Preview and print all staged labels in one PDF (identifier, locality, identification types). |
| **Import & Assign** | Upload a DwC CSV; live-filter rows; assign taxon + per-specimen fields; save to DB. |

### Service layer (`app/services/`)

| Module | Responsibility |
|--------|---------------|
| `taxa.py` | Taxon search, TW import, parent-row creation, `format_scientific_name()` |
| `taxonomy.py` | Checklist tree builder, stats, filter options |
| `taxonworks.py` | All TW API calls (async). Token hardcoded as `TW_TOKEN` at the top of the file. |
| `events.py` | Collecting event CRUD + search |
| `specimens.py` | `CollectionObject` + `TaxonDetermination` creation |
| `identifiers.py` | `reserve_codes()` → `(batch_id, codes_list)` — always unpack the tuple |
| `labels.py` | WeasyPrint HTML → PDF for identifier, locality, identification labels |
| `print_queue.py` | Stage + retrieve + clear print-queue items |
| `dwc_import.py` | Parse DwC CSV, field aliasing, row-to-form-field mapping |

### Taxon search widget (`app/ui/taxon_search.py`)

- **Local-first**: searches local DB (150 ms debounce), then appends TW results in parallel.
- **Multi-token search**: query is split on whitespace; each token must appear in the name.
  `"Sit lin"` matches `"Sitona lineatus"`.
- **Both sections always shown** unless all TW results are already in the local DB (deduplication
  filters them out, causing the TW section to be skipped entirely).
- **TW deduplication**: before rendering the TW section, bare names from TW results are matched
  against local `dwc:scientificName` via exact match or suffix (`endswith(" " + bare_name)`).
  Names already present locally are removed from the TW list.
- **TW pick imports the clicked name** (synonym or valid) via `fetch_full_classification(r["id"])`.
  `get_or_create_from_tw_data` handles valid-name backfill: imports accepted name first, then
  the synonym with `accepted_name_usage_id` set. The determination `taxon_id` is the clicked
  name, which may be a synonym.
- Synonyms shown with ✗ / `= Valid Name ✓` HTML in local section; TW results in tinted box
  with `✚ add` badge.

---

## 7. Build phases — current status

**Phase 1 — Database structure.** ✅ **Complete.**
18 migrations. 6 STRICT core tables + label/print-queue models. 44 tests. All constraints
verified (FK enforcement, exclusive-arc CHECK, coordinate bounds).

**Phase 2 — Frontend.** ✅ **Largely complete.**
NiceGUI app with 5 tabs: Digitize, Taxonomy, Labels, Print queue, Import & Assign.
Leaflet map view: not yet built (coordinates stored, map tab deferred).
Biological association UI: CRUD in DB, UI not yet built.

**Phase 3 — Validation, export, sync tools.** ⬜ **Not yet started.**
- *Validation script:* required DwC fields, coordinate bounds, determination completeness.
- *Habitat enrichment (GeoPandas):* uncertainty-aware spatial join against a European
  habitat layer (EUNIS or CORINE; CRS likely EPSG:3035 / ETRS89-LAEA).
- *DwC export:* occurrence CSV (standard terms + `TW:` columns) for upload.
- *Sync diff:* pull `/api/v1/dwc_occurrences`, diff on `catalogNumber`, emit new-only.
  Snapshot SQLite before any run.

---

## 8. Conventions for Claude Code

### Git discipline
**Always `git commit` before making any code changes.** Every task starts with a commit of
the current clean working tree so there is always a rollback point. This applies to
experiments, UI tweaks, and small changes — not just large features.

- Schema changes → Alembic migration, never hand-edited DDL on a live DB.
- Data transforms → standalone, deterministic, tested scripts. No LLM in the data path.
- Heavily test the **sync diff** and **habitat ambiguity** logic.
- Comment any TaxonWorks behavioural assumption with its source (`file:line` or API route).
- Don't add dependencies casually; pin them; don't touch other conda envs.
- Keep the UI layer thin; logic lives in service/repository functions callable from scripts.
- `reserve_codes()` returns `(batch_id, codes_list)` — always unpack both values.

### UI conventions

- **Empty/blank option always last** in every `ui.select` list. Never first.
- **Tab-to-complete on all selects**: a global JS listener (injected in `main.py`) auto-selects
  the sole remaining dropdown item on Tab and advances focus to the next field.
- **Cross-tab refresh**: each tab registers a refresh callable in `_refreshers` dict in
  `main.py`; `_on_save` iterates all of them so the taxonomy tree and other views stay
  up-to-date after every save without a page reload.
- **NiceGUI tree updates**: use `tree._props['nodes'] = new_nodes; tree.update()` — do NOT
  assign to `tree.nodes` directly (NiceGUI 2.x).

#### DB-backed selects must stay live — use `ui.timer`

NiceGUI renders each page **once per browser page load**. Any `ui.select` whose options come
from the database is populated at that moment and then frozen for the session. If the user
modifies the underlying table (e.g. adds a person in the Controlled Vocabularies tab) and
then switches back to Digitize, they will see the stale list — silently wrong data.

**NiceGUI does not reliably forward Quasar component events (like `popup-show`) to Python.**
Do not use `sel.on("popup-show", ...)` or similar event hooks for this — they appear wired
but the Python callback is not reliably called.

**The correct pattern is `ui.timer` (NiceGUI's own recommended approach for backend sync).**

For every `ui.select` whose options come from the DB:

1. Write a refresh function that re-queries the DB and sets `sel.options = new_opts`.
   Preserve any free-typed current value that isn't in the new options.
2. Create a `ui.timer(2.0, refresh_fn)` — this fires every 2 seconds and keeps the
   select live without any event wiring.
3. Also call `refresh_fn()` immediately from any write path that changes that table
   (belt-and-suspenders: the timer handles the background case, the direct call handles
   the in-session write case without a 2-second wait).

```python
# After creating the select:
def _refresh_person_opts():
    with session_factory() as s:
        new_opts = persons_svc.person_options(s)
    sel.set_options(new_opts)  # use set_options() — plain `sel.options = x` does NOT push to the frontend

ui.timer(2.0, _refresh_person_opts)   # keeps it live

# In the write path (e.g. controlled_vocab_tab.py after saving a person):
if on_person_changed:
    on_person_changed()               # calls _refresh_person_opts() immediately
```

`ui.timer` created inside a `@ui.page` handler is per-client and stops automatically
when the client disconnects. The overhead on localhost (one DB read every 2 s) is
negligible.

For tabs that rebuild their entire form on each interaction (e.g. Records tab rebuilds
on each specimen selection via `_load_specimen`), no timer is needed — the rebuild
already fetches fresh options from DB at that point.

This does NOT apply to static/hardcoded option lists (sex, basisOfRecord, samplingProtocol,
etc.) defined in Python constants — those never change at runtime.

### Field-filling policy (three tiers)

Every form field falls into exactly one of three categories. This distinction must be
consistent across all tabs (Digitize, Records, Import & Assign) and documented in any
future UI help text.

#### Tier 1 — Auto-filled and editable
Pre-populated with a sensible constant when a new record is created. The user sees the
value and can change it before saving. These are "almost always correct" defaults.

| DwC field | Pre-filled value | Notes |
|-----------|-----------------|-------|
| `basisOfRecord` | `"PreservedSpecimen"` | hardcoded; other values are rare exceptions |
| `disposition` | `"in collection"` | hardcoded; changes only for loans/donations |

#### Tier 2 — One-click configurable default
Field starts **empty**. A small icon button adjacent to the field inserts the configured
default. The user must actively click it — nothing is ever silently applied. This prevents
stale values slipping through on rapid digitizing.

| DwC field | Config key | Icon | Inserted value |
|-----------|-----------|------|----------------|
| `identifiedBy` | `default_identified_by` | `push_pin` | user's full name |
| `recordedBy` | `default_recorded_by` | `push_pin` | user's full name |
| `dateIdentified` | *(derived)* | `push_pin` | current 4-digit year (`"2026"`) |

**Icon:** always `push_pin` for every Tier 2 default button, regardless of field type.

**Placement:** the button must be placed **adjacent** to the field (sibling in a flex row),
**not** inside the field's `add_slot("append")`. Quasar QSelect intercepts all events
inside its append slot and opens the dropdown; `on_click` never fires independently.
For `ui.input` (QInput) the append slot works, but `push_pin` is still placed adjacent
for visual consistency across all Tier 2 fields.

Implementation pattern for a `ui.select` (person) field:
```python
with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
    sel = ui.select(opts, label="identifiedBy", with_input=True, clearable=True).classes("flex-1")
    (
        ui.button("", icon="push_pin")
        .props("flat dense round size=xs")
        .tooltip("Insert default name")
        .on_click(lambda: sel.set_value(get_config().default_identified_by) if get_config().default_identified_by else None)
        .bind_visibility_from(sel, "value", lambda v: not v)
    )
```

For `dateIdentified` insert only the year; the user completes month/day as needed.
Always call `get_config()` inside the lambda at click time — never capture the value at
render time, or the button will be frozen to whatever was configured when the page loaded.

#### Tier 3 — Background invisible default
Written silently into every saved record and every DwC export row. Never shown in any form
field. The user configures these once in the Config tab and then forgets them.

| DwC field | Config key | Notes |
|-----------|-----------|-------|
| `institutionCode` | `institution_code` | injected at export time; not stored per-row in the DB |
| `collectionCode` | *(= `dwc:collectionCode` column)* | stored per-row; value comes from `DEFAULT_NAMESPACE` constant at digitize time |

### TaxonWorks namespace: institutionCode + collectionCode (verified)

Verified against `occurrence.rb` in `~/Downloads/neu/software/taxonworks/`
(commit `897f385`, 2026-06-03):

- **`ownerInstitutionCode` is `[Not mapped]`** (occurrence.rb:728) — TW silently ignores it.
  The column was removed from the DB in migration 0015; do not re-introduce it.
- **`institutionCode`** (occurrence.rb:657–702): TW looks up a `Repository` object by URL,
  acronym, or name. It is also used together with `collectionCode` as a compound key to
  resolve the catalog-number namespace (occurrence.rb:497–509).
- **`collectionCode`** (occurrence.rb:708–718): resolves to the `Namespace` that prefixes
  the `catalogNumber` in TW's internal identifier store.

**How the namespace works in practice:**
TW does NOT prepend `institutionCode`+`collectionCode` directly onto the catalog number.
Instead, the DwC import dataset must be pre-configured with a mapping
`(institutionCode, collectionCode) → TW Namespace`. TW then stores the specimen's
catalog-number identifier as `"[namespace.short_name] [catalogNumber]"`, e.g. `"Jilg ab12"`.
The four-character code is the `catalogNumber` as-is; the namespace label comes from TW.

**DB mapping:**
- `dwc:catalogNumber` (Python: `catalog_number`) — the 4-char code; immutable once assigned.
- `dwc:collectionCode` (Python: `collection_code`) — the namespace short name (e.g. `"Jilg"`);
  stored per-row. **Mutable**: a specimen may be re-homed to another collection when gifted,
  so the Records edit tab allows changing it (`update_collection_object` permits
  `collection_code` but never blanks it — NOT NULL). `catalog_number` remains the immutable
  join key; do not mutate it once assigned.
- `dwc:institutionCode` — **not stored in DB**; injected from `config.institution_code` at
  DwC export time.

For this single-collection setup `institution_code` and `collection_code` are both `"Jilg"`.
Configure the TW import dataset to map `("Jilg", "Jilg") → "Jilg"` namespace before import.

---

## 9. Open questions

- Exact JSON shape, filter parameters, and pagination of `/api/v1/dwc_occurrences`.
- Whether TW's internal CRUD API exposes usable `PATCH`/`DELETE` for collection objects.
- The regeneration/lag behaviour of the `dwc_occurrences` projection after an import.
- Source and licence of the chosen Europe-wide habitat layer (EUNIS vs CORINE), and its CRS.

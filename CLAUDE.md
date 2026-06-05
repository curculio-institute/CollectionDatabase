# CLAUDE.md

Project context and working agreement for Claude Code. Read this before writing code.

---

## To do:
- taxonomy table: Darwin core stores taxonomy strictly by linking parents. Read https://ipt.gbif.org/manual/en/ipt/latest/best-practices-checklists to understand how to store taxonomic information.
The fields would only be taxonID, taxonRank, scientificName, parentNameUsageID and of course scientificNameAuthorship, acceptedNameID, taxonomicStatus.

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

18 migrations applied. Schema is in production use.

### DwC column naming convention

Darwin Core fields are stored on SQLite columns named **`dwc:columnName`** (colon separator,
camelCase after the colon). SQLAlchemy `mapped_column` maps these to snake_case Python
attributes. Mermaid diagrams use plain camelCase. Do not deviate from this pattern.

### Core tables

| Table | Purpose |
|-------|---------|
| `collection_object` | One physical specimen or lot. `catalog_number` (NOT NULL) is the stable sync join key. `dwc:basisOfRecord`, `dwc:sex`, `dwc:preparations`, `dwc:typeStatus`, etc. |
| `collecting_event` | Where/when collected; shared by many specimens. Full DwC locality + coordinate block. `dwc:eventDate` supports ISO 8601 intervals (`2024-06-15/2024-06-20`). |
| `taxon` | Local OTU analogue. Stores denormalised classification columns (`family`…`subgenus`) plus `specific_epithet`, `infraspecific_epithet`. `accepted_name_usage_id` self-FK marks synonyms. `taxonworks_otu_id` links to TW. |
| `taxon_determination` | `collection_object` → `taxon` link. `is_current` flag. `taxon_id` may reference a synonym row (deliberate design). |
| `biological_relationship` | Kind of association (`collected_on`, `feeds_on`, …). |
| `biological_association` | Exclusive-arc pattern: (`subject_collection_object_id` XOR `subject_taxon_id`) and (`object_collection_object_id` XOR `object_taxon_id`). CHECK enforces exactly-one-non-null per role. |
| `label_code` | 4-char alphanumeric specimen identifiers (`[0-9a-z]{4}`, ~1.7 M possibilities). Tied to a `label_batch`. Once used on a specimen they are immutable. |
| `label_batch` | Groups of `label_code` rows with a `created_at` timestamp. Batches can be reprinted only if no code in the batch has been used yet. |
| `print_queue` | Staged label jobs (identifier, locality, identification types) pending a single print run. Items removed after printing. |

### Removed from original design

- **`identifier` table** — dropped (migration 0006). `catalog_number` lives directly on
  `collection_object`; `occurrenceID` is not separately stored at this stage.
- **`taxon.taxonomic_status` column** — dropped (migration 0011); synonym status is encoded
  entirely by `accepted_name_usage_id IS NOT NULL`.

### Parent-rank taxon rows

Every species import also creates dedicated `taxon` rows for each ancestor rank (genus,
subgenus, tribe, subfamily, etc.) so users can select them as determination targets. These
rows have `specific_epithet IS NULL` and appear in search results but are filtered out of
the taxonomy tree unless at least one specimen is actually determined to that rank.

`_ensure_parent_rows()` in `app/services/taxa.py` creates these after every species import.
`ensure_higher_taxa()` backfills them at app startup (idempotent cleanup + recreation).

**Invariant:** a row at rank X must have only ancestor fields + X itself set; all fields
lower than X must be NULL. (Tribe row: `family`, `subfamily`, `tribe` set; `subtribe`,
`genus`, `specific_epithet` all NULL.)

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

## 6. Application structure

### App tabs (in `app/ui/main.py`)

| Tab | Purpose |
|-----|---------|
| **Digitize** | Main specimen entry form: collecting event (search/create), taxon (local-first search + TW fallback), sex, count, preparations, notes. Saves to DB and pushes to print queue. |
| **Taxonomy** | Checklist tree (family → synonyms). Filter by rank. Links to TaxonPages. Rebuilds on every tab switch. |
| **Labels** | Generate identifier label batches (4-char codes). Preview + download PDF. Reprint a whole batch if unused. Staged-codes dashboard. |
| **Print queue** | Preview and print all staged labels in one PDF (identifier, locality, identification types). |
| **Import & Assign** | Upload a DwC CSV; live-filter rows; assign taxon + per-specimen fields; save to DB. |

### Service layer (`app/services/`)

| Module | Responsibility |
|--------|---------------|
| `taxa.py` | Taxon search, TW import, parent-row creation, `format_scientific_name()` |
| `taxonomy.py` | Checklist tree builder, stats, filter options |
| `taxonworks.py` | All TW API calls (async). Token read from `~/.config/tw_token` or env var. |
| `events.py` | Collecting event CRUD + search |
| `specimens.py` | `CollectionObject` + `TaxonDetermination` creation |
| `identifiers.py` | `reserve_codes()` → `(batch_id, codes_list)` — always unpack the tuple |
| `labels.py` | WeasyPrint HTML → PDF for identifier, locality, identification labels |
| `print_queue.py` | Stage + retrieve + clear print-queue items |
| `dwc_import.py` | Parse DwC CSV, field aliasing, row-to-form-field mapping |

### Taxon search widget (`app/ui/taxon_search.py`)

- **Local-first**: searches local DB (150 ms debounce), then appends TW results in parallel.
- **Both sections always shown** — never fallback-only.
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

---

## 9. Open questions

- Exact JSON shape, filter parameters, and pagination of `/api/v1/dwc_occurrences`.
- Whether TW's internal CRUD API exposes usable `PATCH`/`DELETE` for collection objects.
- The regeneration/lag behaviour of the `dwc_occurrences` projection after an import.
- Source and licence of the chosen Europe-wide habitat layer (EUNIS vs CORINE), and its CRS.

# CLAUDE.md

Project context and working agreement for Claude Code. Read this before writing code.

This file is deliberately **compact**: it holds the binding rules and an index. The full
rationale, measurements and recorded dead ends live in the linked `docs/` files — **read the
linked file before working in that area**; the summaries here are not a substitute.

## Documentation map

| File | Owns |
|------|------|
| `docs/workflows.md` | Specimen workflows 1–3, the catalog-number exception for foreign collections, bulk import (#39), print-queue policy by create mode, plain identification labels, label editing/overrides (#37, #45/#46, #67) |
| `docs/data_model.md` | Core tables, media store (#48, #63), external identifiers (#49), life-stage history (#50), confidential/consent flags, why person defaults live in the DB, removed columns |
| `docs/vocabularies_geography.md` | The generic controlled-vocabulary pattern, `(name, iso_code)` identity, label geo prefix, geocoding (Overpass/Photon) with all measured dead ends, locality eligibility, the reverted boundary check |
| `docs/taxonomy.md` | Synonym integrity, synonym-aware search (#151), merging names, offline name sources/datasets, code-specific ranks, parent-rank rows, taxon search widget (#152) |
| `docs/taxonworks.md` | §5 integration constraints, §5b importer gaps, §5c export contract (#149: eligibility, name pre-flight, OTU ids, verified API facts, emitted file), institutionCode/collectionCode namespace |
| `docs/app_structure.md` | Tab table, Digitize layout modes, service-layer module table, data safety (#41) |
| `docs/design.md` | *How the UI is built*: UI conventions, widget and layout specs, templates (field-filling tiers, custom dropdown, grouped print sheet) |
| `docs/schema.html` | Schema reference: tables, columns, every CHECK / STRICT / UNIQUE / FK. **Update it with every schema change.** |
| `docs/field_occurrence.md` | Field occurrence (HumanObservation) design, PR #115 |
| `docs/import_assign_todo.md` | Import & Assign open follow-ups |
| `docs/plant_names.md` | Plant-name lifecycle (local → TaxonWorks → WCVP), the offline WCVP index |
| `docs/archive/` | Superseded documents; not maintained |

**Ownership rule (no duplication):** every topic has **exactly one home.** `CLAUDE.md` owns
the binding rule in one or two lines; the topic file owns the full *what / why*; `design.md`
owns the *how of the UI*. If the same detail is described in two places, that is a bug to fix,
not a precedence to resolve. New decisions go into the topic file, with at most a one-line
rule added here.

## Roadmap / open tasks

Bugs and tasks are **GitHub issues** on
[`curculio-institute/CollectionDatabase`](https://github.com/curculio-institute/CollectionDatabase/issues)
(`gh issue list`, `gh issue view <n> --comments`), not in this file. Close with
`gh issue close <n>` and reference it in the commit (`Fixes #1`).

- #37 print queue: edit labels before printing · #38 printing locality labels · #39 bulk
  import · #40 map view + analysis tools
- #149 syncing with TaxonWorks — export, name pre-flight, Compare and media compare are built
  (`docs/taxonworks.md` §5c). "Recompare" is just running Compare again; nothing to build.
- Epic #30 (atomic taxon names) phases #33–#36 stay open until the branch is merged.
- Historical short-codes: DB-1=#1, U-3=#2, ALT-3=#3, m-1=#4, m-3=#5, m-5=#6, m-6=#7, m-7=#8,
  m-8=#9, U-1=#10, U-2=#11, T-1=#12, m-2=#13.

---

## 1. What this project is

A **local-first, single-user desktop application** for an entomological specimen collection
(primary focus: Coleoptera). The local database is the **source of truth**; TaxonWorks is a
downstream **published mirror**, synced one-directionally (local → TW), with its API used to
*verify* state by comparison. The data model stays close to what TaxonWorks ingests via its
Darwin Core importer. **Biological associations** follow TW's model but cannot be imported
via DwC, so they are local-master with no automated push.

## 2. Non-negotiable principles

- **Local DB is the single source of truth.** TaxonWorks is a mirror, never the master.
- **Sync is one-directional and insert-only** (local → TW). TW's importer does not update.
- **Data integrity is paramount.** Prefer DB-enforced constraints: `PRAGMA foreign_keys = ON`
  on every connection, `STRICT` tables, `CHECK` constraints. **A silent wrong value is worse
  than a loud failure** — never skip, drop, null or guess past bad input; refuse loudly.
- **No fallback defaults** for required fields; make the bad state impossible instead.
- **Data transformation happens in versioned, tested Python scripts**, never ad-hoc or
  LLM-in-the-loop.
- **Fetch from APIs or local files, not web scraping.**
- **Cite where a TaxonWorks behaviour was verified** (file:line or API route) in a code
  comment. Verify against the GitHub source at the pinned commit `897f385`, not a local clone.
- **Determinations may target synonyms**, and the DwC export emits the name **as determined**,
  never resolved to the accepted name. Synonymy is never pushed either way.
- **Determinations freeze the name as used:** `dwc:verbatimIdentification` = the composed full
  name plus authorship at save time (`taxa.compose_full_name`), qualifier-free. Re-classifying
  a taxon never rewrites it; `taxon_id` still drives search, grouping and export.
- **One name renderer — `taxa.render_full_name`** (adapters `render_full_name_of`,
  `render_full_name_frozen`). Genus group and below italic, authorship roman. Doubt qualifiers
  (`cf.` `aff.` `nr.` `?`) **prefix the whole name**; scope qualifiers (`sp.` `spp.` `indet.`
  `agg.` `gr.`) **suffix** it. Never call the private building blocks directly.

## 3. Tech stack (decided)

Python · SQLite via SQLAlchemy · Alembic for every schema change · NiceGUI (on FastAPI) ·
WeasyPrint for label PDFs (≤18×7 mm) · GeoPandas for the planned habitat join · DuckDB only
as a possible later analytics layer (do not introduce yet).

- **The browser is the UI, on purpose** — not a native window. Media and label PDFs open in
  the browser's own viewer, and the unsaved-changes guard relies on `beforeunload`, which a
  pywebview window does not fire. Packaging needs no native window: the launcher starts the
  server and opens the default browser.
- **Environment:** conda env `collection`. Do NOT touch the `phylogeny` or `catalogue` envs.
  Dev OS is Linux; all code must stay cross-platform (Windows/macOS).

## 4. Data model → `docs/data_model.md`, `docs/vocabularies_geography.md`, `docs/taxonomy.md`

Schema is in production use (migrations through 0073). Rules that apply everywhere:

- **DwC column naming:** SQLite columns are named `dwc:columnName` (colon, camelCase);
  SQLAlchemy maps them to snake_case attributes; Mermaid diagrams use plain camelCase.
- **`catalog_number` is the immutable sync join key.** Every specimen in the own (default)
  collection must have one — DB-enforced by triggers. It is nullable **only** in a foreign
  collection, NULL is the only spelling of "no number", it is fill-once, and a numberless
  specimen is never exported. A browse surface must not key a row on it.
- **Collection membership is `collection_object.repository_id`** (NOT NULL FK), the single
  source of truth. Never re-introduce `dwc:collectionCode` / `dwc:institutionCode` columns on
  `collection_object`; codes resolve through the repository at export. A collection is
  identified by its **name**; its code is optional except on the default collection, which is
  a flagged `repository` row (`repositories.get_default`), never a config string. Saves are
  refused when no default is set. Resolution is by pick (`repository_field.py`), never by a
  typed code — `resolve_id` is gone, do not bring it back.
- **Derived values are never stored:** `dwc:taxonomicStatus` (from `acceptedNameUsageID`) and
  `dwc:countryCode` (from `country.iso_code`) are derived at export. Do not re-add either.
- **A configurable default that references a DB entity lives in the DB**, never in
  `config.json` (person defaults, default collection, default preparation). `config.json`
  holds environment settings and file registries only.
- **Open, user-coined vocabularies are FK tables** on the generic `Vocabulary` service
  (adding one = model + migration + one registry entry). **Closed standard lists stay fixed
  CHECK lists** (`sex`, `basisOfRecord`, `identificationQualifier`, `license`).
- **Geography rows are identified by `(name, iso_code)`**, never by name. Exact match → reuse;
  otherwise create. No existing row is ever mutated to carry a code, and no save is refused;
  duplicates are folded with the merge tool. `events._resolve_geo_fields` is the single owner.
- **Geocoding:** Overpass `is_in` owns the administrative hierarchy (containment; state
  identified by its `ISO3166-2` tag); Photon supplies locality candidates only (proximity).
  **One** Overpass request per lookup, one endpoint, no mirror failover, no Nominatim.
  Country and stateProvince in English, lower tiers in the local name. There is currently
  **no boundary-crossing check** — deliberately. Read the measured dead ends before touching
  any of this.
- **Synonymy lives only in `acceptedNameUsageID`;** every name is parented under its **own**
  lineage. Parent / accepted-link writes go only through `taxa.py` (`synonymize`,
  `make_accepted`, `reparent`, `merge_taxa`). Retrieval and counting are synonym-aware through
  the one primitive `taxa.expand_taxon_scope`. `merge_taxa` is de-duplication, not synonymy.
- **Ranks are code-specific** (`RANKS_BY_CODE`); `dwc:nomenclaturalCode` is NOT NULL and is
  inherited or read from the source, never guessed. Subgenus interpolation is ICZN-only.
- **Annotations:** local `annotation_term` vocabulary with an optional ONE TaxonWorks link;
  `tw_host` is earned by a fetch, never assumed; `tw_links.py` is the single owner of link /
  verify for terms and relationships.
- **Media** is copied into a content-addressed store; asset metadata is applied only when the
  `media` row is created; commit first, unlink second.
- **Privacy:** `confidential` on person / specimen / event means **drop the record, never
  obscure it**; `identifiedBy` is never withheld. The flags are never pushed to TaxonWorks.

## Specimen workflows → `docs/workflows.md`

- **Print-queue policy by create mode:** every create path runs through
  `specimens.finalize_specimen`. Digitize standard, Digitize visiting and Import & Assign
  queue **nothing**; only **Mounting** queues identifier + data + determination labels.
- **Import & Assign reuses a collecting event only when it is 100% identical over all
  columns** (`events.get_or_create_exact_event`); erring toward create is the safe side.
- **Bulk import (#39)** is staged: *stage* writes nothing, *import* is resumable.
  `catalogNumber` is required from the data; a named collection that is not set up is
  `blocked`, never fabricated; dedup is the DB's own `UNIQUE(repository_id, catalogNumber)`.
- **Plain identification labels** are print jobs, not records. Every reader goes through
  `print_queue._row_det_label`; never branch on `row.collection_object` for a determination row.
- **Label edits are print-only overrides** (`print_queue.text_override`); the record stays
  master and identifier rows are read-only. An override is sanitised on the way in
  (`labels.canonical_override`, idempotent); one that renders to nothing is `None`; identical
  labels are linked by their auto text; a row with no auto text is edited alone.

## 5. TaxonWorks → `docs/taxonworks.md`

Verified against `SpeciesFileGroup/taxonworks` @ `897f385`. The row mapper is
`app/models/dataset_record/darwin_core/occurrence.rb`; re-grep line numbers, do not trust
cited ones.

- **The DwC importer is CREATE-ONLY** and the v1 API has no update or delete. The push may
  carry only genuinely new records; a record published too early cannot be corrected.
- **Biological associations, DwC-A extensions and `HumanObservation` cannot be imported.**
- **No `/repositories`, `/namespaces`, `/preparation_types` or `/biocuration_classes`
  endpoint** — those TW ids and vocabularies are confirmed by hand.
- **§5b gaps:** `taxonomicStatus`, `disposition`, `ownerInstitutionCode`, `associatedTaxa`,
  `lifeStage`, `otherCatalogNumbers`, `municipality`, `locality` and
  `verbatimIdentification` are not mapped; `basisOfRecord` must be PreservedSpecimen or
  FossilSpecimen.
- **§5c export contract (#149):**
  - `dwc_export.export_decision` is the **single owner** of eligibility, read by the compare,
    the writer and the specimen-row badges. Three absolute grounds, no setting: privacy,
    certainty (any qualifier, a rank above species, or no current determination), and identity
    (no catalog number — rule 6).
  - **Nothing is exported until the name pre-flight passes** (`tw_sync.check_names`; only
    `match` is exportable). A false "missing" is worse than no check.
  - **OTU ids are per-instance**, verified in reverse and never compared; the provenance stamp
    is hard to earn and easy to lose; reconciliation is explicit, never a side effect.
  - **Identity is the `/identifiers` catalog-number index, joined on `cached`** — not
    `dwc_occurrences`. Existence is namespace-unscoped; the orphan sweep is namespace-scoped.
  - **Unknown query params are silently ignored** — an oversized result is a lookup failure,
    never a set of matches.
  - The file is **tab-separated `.tsv`**, emits only columns TW maps, and **refuses an invalid
    row rather than rewriting it**. `TW:` columns: only `TW:Tag:` / `TW:DataAttribute:` for CollectionObject and
    CollectingEvent, linked + verified terms only, behind a term pre-flight. Fix the homonym
    limitation of the OTU check before emitting `TW:TaxonDetermination:otu_id`.

## 6. Application structure → `docs/app_structure.md`

- `app/ui/main.py` is the **page shell** only. **Every tab lives in its own module**; a tab
  gets what it needs from the page as arguments, and hands back a **handle dict of
  callables** — never its internal widgets or state.
- **Keep the UI layer thin;** logic lives in `app/services/` functions callable from scripts.
- **Forms shared across tabs are defined once** (`specimen_form`, `collecting_event_form`),
  never copied.
- A specimen is **one atomic Save** (specimen + IDs + event + associations); layout modes and
  the stepper only change what is visible.
- **A row entered but not added is never dropped silently:** every Digitize Save first runs
  `_resolve_pending` (add and save / save without it / cancel); the check panel shows the
  same before Save. Sticky positioning in a tab needs the `.tp-main-panels` overflow rules.
- **Data safety (#41):** committed data is durable, in-progress edits are not. Startup runs
  checkpoint → snapshot (keep 10) → integrity check, and blocks the UI on failure.
  Unsaved-changes detection is **value-based** (`has_content()` polled by a `ui.timer`), never
  DOM-event-based.

## 7. Build phases — status

Phase 1 (schema) and Phase 2 (frontend) are complete apart from the map view. Phase 3:
DwC export, sync diff and media compare are **built** (§5c); a standalone validation script
and the GeoPandas habitat enrichment are **not**.

## 8. Conventions for Claude Code

### Git discipline

- **Commit before making any code changes** — every task starts from a committed tree, and
  each verified feature is committed before the next one starts.
- Keep `main` as the fallback plus **one** working branch; no parallel feature branches.
- When a fix does not work, revert it before the next attempt; never stack failed attempts.
- Don't add dependencies casually; pin them.

### Migration discipline — never lose constraints

- Schema changes go through an Alembic migration, never hand-edited DDL on a live DB.
- A `batch_alter_table(recreate=...)` or any table rebuild **silently drops STRICT typing,
  CHECK and UNIQUE constraints, FK `ON DELETE` actions, and triggers.** A rebuilding migration
  MUST re-declare all of them, plus server DEFAULTs. This caused DB-1 (migrations 0021/0024,
  restored in 0029).
- `tests/test_schema_integrity.py` guards this; **run the suite after any migration.**
- **The models are not a complete schema mirror** — SQLAlchemy cannot express STRICT, and some
  constraints live only in migration DDL. Never generate rebuild DDL from the models alone;
  prefer adding the constraint to the model.
- **Restoring from a `.db` backup:** the DB is in WAL mode — remove the stale
  `data/collection.db-wal` / `-shm` when swapping the file, or SQLite replays the old WAL.
- Native `ADD COLUMN` preserves STRICT/CHECK/FK; prefer it over a rebuild.

### Code conventions

- `reserve_sequential_codes()` returns `(batch_id, codes_list)` — always unpack both.
- Post-commit cleanup (success notify, wipe, `on_saved`) goes **outside** the try/except, with
  an explicit `return` in the except.
- Heavily test the sync diff and the habitat ambiguity logic.
- Verify UI behaviour by driving the real forms, not by unit tests alone. Ask before any
  action on the database, never seed test data into it, and never leave a test server running
  on port 8080.

### UI conventions (build detail → `docs/design.md`)

- **Empty/blank option is always last** in every `ui.select`.
- **Tab-to-complete** on all selects (global JS listener in `main.py`).
- **Cross-tab refresh:** each tab registers a callable in `_refreshers`; `_on_save` runs all.
- **Tree updates:** `tree._props['nodes'] = new_nodes; tree.update()` — never assign
  `tree.nodes`.
- **DB-backed selects must stay live — use `ui.timer`.** NiceGUI renders a page once per
  load, so a select filled from the DB goes stale. Write a refresh function that re-queries
  and calls `sel.set_options(new_opts)` (plain `sel.options = x` does not reach the frontend),
  run it from `ui.timer(2.0, refresh_fn)`, and also call it directly from any write path that
  changes that table. Do **not** use Quasar event hooks such as `popup-show`; they are not
  reliably forwarded to Python. Not needed for forms rebuilt on each interaction or for
  static option lists.
- **Dialogs created per action** need
  `dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)` or their timers leak.
- Always "label", never "sticker". ICN = 🌿, ICZN = Material Icon `pest_control`.

### Field-filling policy (three tiers; template → `docs/design.md` "Auto-fill tiers")

- **Tier 1 — auto-filled, editable:** `basisOfRecord` = `PreservedSpecimen`, `lifeStage` =
  `adult` (`app/vocab.py::NEW_SPECIMEN_DEFAULTS`); `preparations` from the `preparation` row
  flagged `is_default` (Mounting forces `pinned`). **`disposition` has no create default.**
- **Tier 2 — one-click default:** field starts empty; a `push_pin` button inserts the
  configured default (`identifiedBy`, `recordedBy`, `dateIdentified`). Never applied silently.
- **Tier 3 — invisible default:** the default collection supplies `repository_id` at save.

## 9. Open questions

- Whether TW's internal CRUD API exposes usable `PATCH`/`DELETE` for collection objects.
- The regeneration/lag behaviour of the `dwc_occurrences` projection after an import.
- Source, licence and CRS of the Europe-wide habitat layer (EUNIS vs CORINE).
- `county` / `municipality` tier identification below the ISO subdivision (see
  `docs/vocabularies_geography.md` → "Open (do not guess)").

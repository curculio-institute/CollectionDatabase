# CLAUDE.md

Project context and working agreement for Claude Code. Read this before writing code.

## Documentation map

`CLAUDE.md` is the always-loaded entry point and the index to everything else. Read the
linked file when working in that area:

- **`docs/design.md`** — *how the UI is built*: UI conventions, widget specs, layout specs,
  and reusable implementation templates (field-filling tiers, custom-dropdown checklist,
  the grouped print sheet).
- **`docs/schema.html`** — the database schema reference: tables, columns, and the full
  integrity-constraint list (CHECK / STRICT / UNIQUE / FK).
- **GitHub issues** — all open bugs and tasks live at
  [`curculio-institute/CollectionDatabase`](https://github.com/curculio-institute/CollectionDatabase/issues)
  (`gh issue list`), not in this file.
- **`docs/archive/`** — superseded/historical documents; not maintained.

**Ownership rule (sharp boundary, no duplication):** every topic has **exactly one home.**
CLAUDE.md owns the *what / why* — decisions, policies, contracts, and reference. design.md
owns the *how of the UI* — visual layout, widget construction, templates. When a topic spans
both, CLAUDE.md states the decision and **links** to design.md for the build detail; the
detail is never copied into both. (There is no "design.md overrides CLAUDE.md" rule — if you
find the same thing described in both, that is a bug to fix, not a precedence to resolve.)

---

## Roadmap / open tasks

Tracked as **GitHub issues** (per the ownership rule below — tasks live in the
tracker, not this file), `gh issue list`:

- [#37](https://github.com/curculio-institute/CollectionDatabase/issues/37) — Print queue: edit labels before printing (+ batch-edit identical labels)
- [#38](https://github.com/curculio-institute/CollectionDatabase/issues/38) — Workflow: printing locality labels
- [#39](https://github.com/curculio-institute/CollectionDatabase/issues/39) — Workflow: bulk-import the existing dataset (unlinked taxon names)
- [#40](https://github.com/curculio-institute/CollectionDatabase/issues/40) — Collection map view + data analysis tools

(#41 — data safety: crash recovery + unsaved-changes guard — done; see §8 "Data safety".)

Epic #30 (atomic taxon names) phases #33–#36 also remain open until the
`refactor/atomic-taxon-names` branch is merged.

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
queue_labels=False, print_group_id=None, source=None, associations=())`: bind the reserved
identifier code, optionally queue labels (tagged with a print group + origin header), and
persist biological associations. Which modes queue labels is a deliberate policy, **not** an
incidental of each tab:

| Mode | `code` | `queue_labels` | Queues to `print_queue` |
|------|--------|----------------|-------------------------|
| **Digitize standard** | reserved code | `False` | **nothing** — identifier is pre-printed in a batch and pinned by hand; the specimen already carries its own data labels. The code is still bound (`assign_code`). |
| **Digitize visiting** | `None` | — | nothing — foreign `catalogNumber`, no reserved code; bio associations still saved. |
| **Import & Assign** (retroactive digitisation) | reserved code | `False` | **nothing** — same as standard: the specimen already has its data + identification labels; only the pre-printed identifier is added. |
| **Mounting** | reserved code | `True` | identifier + data + determination — a freshly mounted specimen needs its whole sheet printed, grouped under a "Mounting Session" header. |

**Rationale:** standard / visiting / import specimens already have physical labels; only
mounting produces fresh specimens that need a printed sheet. (Historical note: Digitize
standard and Import & Assign formerly enqueued data + determination labels — removed
2026-06-12; they now queue nothing.)

**How the queued labels are rendered** (the grouped, column-aligned sheet — groups,
per-specimen columns, archival, the planned reprint path) is a UI/layout concern and is
specified in `docs/design.md` → "Grouped print sheet layout". This table is the authoritative
*policy* (which mode queues what); design.md is authoritative for *layout*.

### Editing labels before printing (decided, #37)

A label is *derived* from the linked records (`_co_to_data_label` / `_co_to_det_label` in
`print_queue.py`), but the user can apply a **print-only override** per row to fit the tiny
physical label — abbreviate text too long, or add what the auto-format omits — **without
changing the record**. The record stays master: substantial corrections are made in Records
(every label has an "open in Records" link) and the derived label updates from them.

- **Persistence:** the override is stored on `print_queue.text_override` (nullable TEXT,
  migration 0034 — added via SQLite `ADD COLUMN`, preserving STRICT/CHECK/FK). Blank or
  equal-to-auto clears it back to the auto-composed text.
- **Editable rows:** data + determination. **Identifier** rows are **read-only** (the
  immutable catalog number is the sync join key).
- **Primary surface:** the interactive **sheet preview** (groups → per-specimen columns →
  data / id / determination boxes) is the main UI; data/det labels are edited inline.
- **Identical labels are linked.** Identity is the rendered *auto* text — for a **data**
  label that means the collecting **event *and* the biological associations** (the label is
  composed from both), for a **determination** the name — hashed by `_ident` /
  `_row_auto_identity`. Editing one label applies the override to **every identical** label
  (`set_override_for_identical`); hovering highlights them. This works across batches /
  distinct event rows (identity is content, not the event id).
- **Determination labels** carry the open-nomenclature qualifier (cf./aff.) and type status.

History: a first cut had the queue edit the *record* directly (`update_collecting_event`,
live determination editor); that was reversed — a label is a physical artifact with size
limits, and forcing record edits there mixed concerns. The print-only override was
deliberately (re-)introduced.

**Formatting-aware editing (decided, #45/#46 — done).** The override is **formatted HTML**,
not plain text, so an edit keeps the scientific name's italics/bold on the printed PDF:

- The preview box is a **`contenteditable` rendered with the real formatted label HTML**
  (genus+species bold-italic, subgenus italic, associated species italic) — so the preview
  resembles the PDF (#46). Editing *inside* a `<strong><em>` token keeps its styling; text
  typed outside stays plain (#45's "knows what was edited", achieved structurally, no
  token-classification logic). The inline box is for quick tweaks.
- **Larger editor dialog** (per-box ⤢ button, `_open_label_dialog`): a readable,
  comfortably-sized WYSIWYG area with a **Bold / Italic toolbar** (select text → click;
  `document.execCommand` with `styleWithCSS=false` so it emits `<b>/<i>`, which the sanitizer
  maps to `<strong>/<em>`) — so a user can add explicit formatting without hand-editing tags.
  The toolbar buttons use a client-side `mousedown.preventDefault` handler so clicking them
  doesn't collapse the editor's text selection. The dialog also has a **raw-HTML source
  toggle** (the escape hatch for long/precise markup, which is unreadable in the tiny inline
  box). The editor's `innerHTML` is read on Save via `ui.run_javascript`; seeded imperatively
  so Vue never re-binds it. Standard Abort / Save & close modal, deleted on close (timer-leak
  rule).
- **Single gatekeeper:** `labels.sanitize_override_html()` reduces arbitrary contenteditable
  HTML to a tiny safe subset (`<div>` lines of `<em>`/`<strong>`; `<b>`→`<strong>`,
  `<i>`→`<em>`; all attributes dropped) — applied both on store and on render.
  `labels._override_html` keeps the legacy plaintext path for old overrides (detected by the
  absence of tags). `labels.label_auto_html()` composes the formatted auto HTML used to seed
  the editor and to detect "edited == auto → clear".
- **Capture seam:** a contenteditable's `innerHTML` cannot ride NiceGUI's event args (the DOM
  node is stripped before serialisation), so a **global capture-phase `blur` listener** reads
  `innerHTML` client-side and emits `pq_edit {qid, html}`; the server recomputes the row's
  auto text (`pq_svc.row_auto_html`) to decide store-vs-clear. This also sidesteps a
  `v-html`-bound contenteditable being clobbered mid-edit by an unrelated Vue patch.

Layout/rendering detail is design.md's concern; this section owns the *policy*.

## Open issues → GitHub

Bugs and tasks are tracked as **GitHub issues** on
[`curculio-institute/CollectionDatabase`](https://github.com/curculio-institute/CollectionDatabase/issues),
not in this file. Use `gh issue list` / `gh issue view <n>`. Resolved items live in git
history (and the archived code review under `docs/archive/`).

Historical short-codes used in commits/comments map to issues as follows:

| Code | Issue | Code | Issue |
|------|-------|------|-------|
| DB-1 | [#1](https://github.com/curculio-institute/CollectionDatabase/issues/1) | m-3 | [#5](https://github.com/curculio-institute/CollectionDatabase/issues/5) |
| U-3  | [#2](https://github.com/curculio-institute/CollectionDatabase/issues/2) | m-5 | [#6](https://github.com/curculio-institute/CollectionDatabase/issues/6) |
| ALT-3| [#3](https://github.com/curculio-institute/CollectionDatabase/issues/3) | m-6 | [#7](https://github.com/curculio-institute/CollectionDatabase/issues/7) |
| m-1  | [#4](https://github.com/curculio-institute/CollectionDatabase/issues/4) | m-7 | [#8](https://github.com/curculio-institute/CollectionDatabase/issues/8) |
| m-8  | [#9](https://github.com/curculio-institute/CollectionDatabase/issues/9) | U-1 | [#10](https://github.com/curculio-institute/CollectionDatabase/issues/10) |
| U-2  | [#11](https://github.com/curculio-institute/CollectionDatabase/issues/11) | T-1 | [#12](https://github.com/curculio-institute/CollectionDatabase/issues/12) |
| m-2  | [#13](https://github.com/curculio-institute/CollectionDatabase/issues/13) | | |

When you finish an issue, close it with `gh issue close <n>` (reference it in the commit,
e.g. `Fixes #1`). C-1/2/3, M-1…4, m-4, TX-1 were already resolved before migration to
GitHub (see git history).

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
- **Determinations freeze the name as used (Epic #30, Phase 5).** Every ID point saves
  `dwc:verbatimIdentification` = the *composed* name of the chosen taxon **at save time**
  (qualifier-free); the open-nomenclature qualifier lives separately in
  `dwc:identificationQualifier`. Re-classifying the taxon later never rewrites a saved
  determination's name, yet `taxon_id` still drives search / grouping / export. Display
  goes through **`render_identification(verbatim, qualifier)`** in `taxa.py`, which inserts
  the qualifier **right after the genus-group** by one rule — `Otiorhynchus cf. forticollis`,
  `Otiorhynchus (Nihus) aff. forticollis`, `Otiorhynchus sp.` (a genus-row determination →
  empty rest). No per-qualifier logic, no `sp.` special case.

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
| `collection_object` | One physical specimen or lot. `catalog_number` (NOT NULL) is the stable sync join key. `dwc:basisOfRecord`, `dwc:sex`, `dwc:typeStatus`, etc. `preparation_id` FK → `preparation` (controlled vocab, not free text — migration 0039; see "Controlled vocabularies"). |
| `collecting_event` | Where/when collected; shared by many specimens. Full DwC locality + coordinate block. `dwc:eventDate` supports ISO 8601 intervals (`2024-06-15/2024-06-20`). `dwc:recordedBy` FK → `person(full_name)`. `habitat_id` + `sampling_protocol_id` (migration 0040) and the geography hierarchy `country_id` / `state_province_id` / `administrative_region_id` / `county_id` / `island_id` (migration 0041) are all controlled-vocab FKs (see "Controlled vocabularies"). `municipality` + `locality` stay free text; `dwc:countryCode` stays a per-event column. |
| `taxon` | Local OTU analogue. DwC parent-link model (GBIF best practices). Columns: `name_element` (atomic source of truth — this rank's own epithet/uninomial, e.g. `crypticus`; migration 0032, Epic #30), `dwc:scientificName` (the *composed* full name without authorship, e.g. `Otiorhynchus crypticus`, maintained from `name_element` + the parent chain), `dwc:taxonRank`, `dwc:scientificNameAuthorship`, `dwc:parentNameUsageID` (self-FK, encodes hierarchy), `dwc:acceptedNameUsageID` (self-FK, marks synonyms — its presence *is* synonym status; `taxonomicStatus` is derived at export, not stored, see below), `taxonworksOtuID`. No denormalised rank columns. |
| `taxon_determination` | `collection_object` → `taxon` link. `is_current` flag. `taxon_id` may reference a synonym row (deliberate design). `dwc:identifiedBy` FK → `person(full_name)`. |
| `biological_relationship` | Kind of association (`collected_on`, `feeds_on`, …). |
| `biological_association` | Exclusive-arc pattern: (`subject_collection_object_id` XOR `subject_taxon_id`) and (`object_collection_object_id` XOR `object_taxon_id`). CHECK enforces exactly-one-non-null per role. |
| `label_code` | 4-char alphanumeric specimen identifiers (`[0-9a-z]{4}`, ~1.7 M possibilities). Tied to a `label_batch`. Once used on a specimen they are immutable. |
| `label_batch` | Groups of `label_code` rows with a `created_at` timestamp. Batches can be reprinted only if no code in the batch has been used yet. |
| `print_queue` | Staged label jobs (`label_type` ∈ {data, determination, identifier}) pending a single print run. Items removed after printing. (The `data` label carries locality/date/collector — there is no separate "locality" type.) |
| `person_defaults` | Single-row table holding the push-pin person defaults: `default_identified_by_id`, `default_recorded_by_id`, and `default_rights_holder_id` (media rightsHolder; migration 0036). All are `INTEGER REFERENCES person(id) ON DELETE RESTRICT`. See rationale below. |
| `preparation` | Single-name controlled vocabulary (`id`, `name` UNIQUE) for `collection_object.preparation_id`. First of the single-name vocabularies built on the generic `Vocabulary` service; see "Controlled vocabularies". Migration 0039. |
| `habitat` | Single-name controlled vocabulary for `collecting_event.habitat_id` (was free-text `dwc:habitat`). Migration 0040. |
| `sampling_protocol` | Single-name controlled vocabulary for `collecting_event.sampling_protocol_id` (was the hardcoded `dwc:samplingProtocol` UI list). **Seeded** with the curated method set. Migration 0040. |
| `country` / `state_province` / `county` / `island` / `administrative_region` | The administrative-geography single-name vocabularies on `collecting_event` (migration 0041, #40). Were free-text `dwc:country`/`dwc:stateProvince`/`dwc:county`/`dwc:island`; `administrative_region` (Regierungsbezirk tier) is new and has no DwC term. Resolved name→id in the events service. |
| `media` | One row per stored file (the bytes live content-addressed on disk; see "Media" below). `sha256` UNIQUE (de-dup). `category` (CHECK ∈ {Image, Sound, Video, Document, Sequence, Other}) is the filter key. Audubon-Core-style metadata; `rights_holder_id` is a **person FK** (ON DELETE RESTRICT), `license` is free text. Migration 0035. |
| `media_attachment` | Links a `media` row to exactly one of a `collection_object`, `collecting_event`, or `biological_association` (exclusive-arc CHECK; all FKs ON DELETE CASCADE). Per-attachment `caption` / `is_primary` / `sort_order` (mirrors TaxonWorks' Image↔Depiction split). Migration 0035. |
| `external_identifier` | An external resource link/ID (`source`, `value`, `label`) attached to exactly one of a `collection_object` or a `biological_association` (exclusive-arc CHECK; FKs ON DELETE CASCADE). For an association it denotes the *other party* (optional addition; the association object arc is unchanged). Migration 0037. |
| `life_stage_record` | Reared-specimen life-stage history: per-specimen `(dwc:lifeStage, dwc:basisOfRecord, dwc:eventDate)` rows for *earlier* stages of the same individual (e.g. the wild larva). FK → `collection_object` ON DELETE CASCADE; `basisOfRecord` CHECK mirrors `collection_object`'s. No duplicate specimen/event rows. Migration 0038. |

### Media storage (decided, #48)

Files attached to a specimen / event / association are **copied into a managed,
content-addressed store** (`data/media/<xx>/<sha256>.<ext>`, configured by
`config.media_dir`; served at `/media`). This is deliberate, for *safe & persistent*
storage:

- **Copy-in, never reference-in-place** — the original can move or be deleted without
  breaking us.
- **Content-addressed by SHA-256** — automatic de-duplication (identical bytes → one
  file, one `media` row) and a built-in integrity check (`media.verify_integrity`
  re-hashes and compares).
- **`category`** (Image / Sound / Video / Document / **Sequence** / Other) is a
  first-class, CHECK-constrained field so media is **filterable by kind**; "Sequence"
  covers genetic data (FASTA etc.), which Audubon Core has no native category for.
- **Attachment is a separate row** (`media_attachment`, exclusive-arc to one record) with
  per-attachment caption / primary, mirroring TaxonWorks' Image↔Depiction split but using
  the project's FK-safe exclusive-arc instead of a polymorphic association.
- **`rightsHolder` is a controlled person**, not free text — `media.rights_holder_id` FK →
  `person` (ON DELETE RESTRICT), so delete/merge integrity applies (the same reason person
  defaults live in the DB; `merge_persons` re-points it automatically). Both `rightsHolder`
  and `license` are **Tier-2** fields in the media editor (a push_pin inserts the configured
  default): the rightsHolder default is `person_defaults.default_rights_holder_id` (a person,
  in the DB) and the licence default is `config.default_license` (a plain string).
- Deleting the last attachment of a media asset removes the orphaned `media` row **and**
  its on-disk bytes (`media.delete_attachment`); shared content is kept while still
  referenced. Snapshots cover the `.db` only — `data/media/` is backed up separately, and
  a rolled-back upload can leave an orphan file (bytes are written before the row commits);
  an orphan-sweep is a planned maintenance action.
- **UI is an icon + popup** (`app/ui/media_panel.py` → `build_media_button`): a compact
  button with a **count badge** opens a popup with **batch upload** (many files at once),
  a category filter, and per-item category / primary / delete / details (rightsHolder,
  licence, caption). It runs in two modes: **bound** (Records — writes straight to the DB,
  on the specimen, event, and per-association) and **staged** (Specimen Digitization — the
  record doesn't exist yet, so files are stored and committed to the new records on Save;
  `commit(session, target_id)`). Staged Digitize media covers the **specimen, event, and
  each biological association** — `finalize_specimen` returns the created associations so
  per-association staged media maps to the new ids. Service: `app/services/media.py`.

### External resource identifiers (decided, #49)

A specimen (or a biological association's *other party*) can carry external resource
identifiers — e.g. an iNaturalist observation **URI** (a resolvable, API-queryable
identifier, not merely a "link"). Stored in `external_identifier` (exclusive-arc to a
`collection_object` *or* a `biological_association`; the association's object arc is
**unchanged** — the URI is an optional addition denoting the other party).

- **The user pastes only the URI** (`value`, NOT NULL). `source`/`label` are nullable and
  currently unpopulated — kept on the row for flexibility (a source can be derived from the
  URI later, at export/query time); there is deliberately **no source dropdown or
  auto-detection** in the UI for now.
- **UI** (`app/ui/external_id_panel.py` → `build_external_id_button`): a link-icon button
  with a count badge opens a small modal (list + a single "Resource identifier (URI)" field;
  **Abort / Save & close**, the standard modal pattern). Bound (Records: specimen +
  per-association) and staged (Digitize: specimen + per-association, committed on Save).
  Service: `app/services/external_ids.py`.

### Reared specimens — life-stage history (decided, #50)

A reared specimen is preserved as one stage (e.g. the adult) but was collected in the wild
as an immature (egg/larva/pupa). We record the life-stage history **linked to the specimen,
without duplicating specimens or events** (duplication was rejected as invasive/untidy):

- The preserved stage stays on `collection_object` (`dwc:lifeStage`, `dwc:basisOfRecord`),
  and the specimen's own `collecting_event` carries the original wild date + locality.
- Each earlier stage is a `life_stage_record` row — `(dwc:lifeStage, dwc:basisOfRecord,
  dwc:eventDate)` for the *same individual* (e.g. larva / HumanObservation / wild date).
- **Export (Phase 3, not built):** the preserved specimen → a PreservedSpecimen DwC record;
  each `life_stage_record` → a separate record (the wild larva as a **HumanObservation**,
  sharing the specimen's locality, with its own eventDate), the two **linked** via a derived
  `dwc:associatedOccurrences` / resourceRelationship — **no stored resource-relationship
  table** (the FK to the specimen is the relationship). `life_stage.life_stage_facets()`
  returns the facets (preserved first) for the export to consume.
- **UI:** a timeline-icon button (`app/ui/life_stage_panel.py` → `build_life_stage_button`)
  with a count badge → a small Abort/Save&close modal (lifeStage + basisOfRecord defaulting
  `HumanObservation` + eventDate). Bound in Records, staged in Digitize (committed on Save).

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

### Controlled vocabularies (single-name; the generic pattern, decided)

Some fields are **controlled vocabularies referenced by FK**, not free text, so they can be
**edited and merged like persons** (rename once, re-point everywhere; fold a typo into the
canonical value). Current single-name vocabularies (more may follow — built once, reused):

| Field | Column | Table | Migration | Note |
|-------|--------|-------|-----------|------|
| preparations | `collection_object.preparation_id` | `preparation` | 0039 | was `dwc:preparations` TEXT |
| habitat | `collecting_event.habitat_id` | `habitat` | 0040 | was `dwc:habitat` TEXT |
| samplingProtocol | `collecting_event.sampling_protocol_id` | `sampling_protocol` | 0040 | was a hardcoded UI list; table **seeded** with the curated set |
| country | `collecting_event.country_id` | `country` | 0041 | English name (pycountry); `dwc:countryCode` stays per-event |
| stateProvince | `collecting_event.state_province_id` | `state_province` | 0041 | English name (OSM `name:en`) |
| administrative region | `collecting_event.administrative_region_id` | `administrative_region` | 0041 | Regierungsbezirk tier; **no DwC term** (local field); local name |
| county | `collecting_event.county_id` | `county` | 0041 | local name (Landkreis) |
| island | `collecting_event.island_id` | `island` | 0041 | local name |

**Which fields qualify:** *open, user-coined* vocabularies where consistency tooling (merge)
helps. **Closed standard vocabularies stay fixed CHECK-constrained lists, NOT editable tables**
— `sex`, `basisOfRecord`, `disposition`, `identificationQualifier`, `license` (editability
would create `Male`/`male` duplicates or values TW rejects).

**Geography is the exception that proves the rule (revised, #40):** the administrative levels
*are* controlled vocabs — even though they come from geocoding, the geocoder yields
language/spelling variants (`Deutschland` vs `Germany`) and the faceted Explore search demands
consistency, so they're FK vocabs with merge. `municipality` + `locality` stay free text
(too specific; municipality search is "the map's job"). **Resolution lives in the events
service** (`_resolve_geo_fields` in `events.py` maps the name keys → `*_id` via get_or_create
in `create`/`update_collecting_event`), so the event form keeps its geocode-input widgets +
boundary-warning UI **unchanged** — only the service + the model-read sites resolve by FK.
Language policy: country + stateProvince in English (Photon `lang=en`), the rest local.

**Geocoding upgrades (`collecting_event_form._reverse_geocode`, #40):** Photon (`lang=en`)
gives English country/state but only returns buildings/streets near a point — *not* meaningful
collecting localities — and never the Regierungsbezirk. So one Overpass query adds: the
enclosing **admin_level-5 boundary → administrative_region** (e.g. Oberbayern), and the
**nearest named natural features by radius** — peaks (+elevation), water bodies / waterways,
springs / caves / saddles, and OSM `place=locality` — de-duped by name and **ranked**
(enclosing areas first, then by distance) into the "Also nearby" locality picker. `locality`
auto-fills to the best candidate (the nearest meaningful feature) when Photon has no named one.

The shared mechanism:

- **`app/services/vocab.py` → `Vocabulary`** — generic CRUD + merge for any single-name table
  (`id`, `name` UNIQUE), parameterised by the model. `list` / `options` / `get_or_create` /
  `create` / `update` / `delete` / `merge_preview` / `merge`. Merge and delete-safety
  **re-discover referencing FK columns dynamically** via `PRAGMA foreign_key_list` (same
  mechanism as `merge_persons`), so any FK at the vocab table — present or future — is handled
  with no hardcoded list. `delete` is blocked while referenced (and the FK is `ON DELETE
  RESTRICT` as a DB backstop).
- **`app/services/vocabularies.py`** — instances + a `VOCAB_REGISTRY` of `VocabSpec`s (display
  metadata). **Adding a vocab = a model + a migration + one registry entry**; it then appears
  in the Controlled Vocabularies tab and gets a dropdown field automatically.
- **`app/ui/vocab_field.py` → `build_vocab_field`** — the data-entry widget, the *same*
  custom-dropdown UX as the person field (`✚ add <typed>` + existing matches, no free-text
  escape; reuses person_field's CSS/nav). `get_value()` is the name; **`commit(session)`
  resolves name → id (get_or_create) inside the save transaction and returns the FK** (exactly
  like person `commit`). Used in Digitize/Records (shared `specimen_form`), Import & Assign,
  and Mounting.
- **DwC export** resolves `preparation_id` → `preparation.name` → `dwc:preparations` at export
  time (mirrors `recordedBy`/`identifiedBy`; nothing denormalised on `collection_object`).
- **Controlled Vocabularies tab** renders one card per registry entry (`_build_vocab_section`)
  with edit / merge / delete / add — the generic mirror of the People card.
- **Person stays separate** (not folded into `Vocabulary`): it carries extra columns
  (`abbreviated_name`, `orcid`) and label-printing logic. `Vocabulary` is for the *single-name*
  case only.

### Removed from original design

- **`identifier` table** — dropped (migration 0006). `catalog_number` lives directly on
  `collection_object`; `occurrenceID` is not separately stored at this stage.
- **Denormalised rank columns** — removed (migration 0012). `dwc:family`, `dwc:genus`,
  `dwc:specificEpithet`, etc. replaced by the DwC parent-link model.
- **`dwc:taxonomicStatus`** — **not stored** (dropped in migration 0030). Synonymy is
  encoded *solely* by `acceptedNameUsageID`: a taxon is a synonym iff it links to an
  accepted name, otherwise accepted. The DwC Taxon-core `taxonomicStatus` term is **derived
  from that link at export time**, never stored. History: dropped in 0011 as redundant,
  restored in 0012 as a CHECK-constrained column for DwC compliance, then re-dropped in 0030
  because storing a derived value let it drift out of sync with `acceptedNameUsageID` (one
  row had already drifted). **Do not re-introduce the column** — derive it in the export
  instead (`tests/test_schema_integrity.py::test_taxon_status_column_dropped` guards this).

### Synonym integrity (acceptedNameUsageID is the single source)

A taxon is a synonym **iff** it links to an accepted name. **Status lives *only* in
`acceptedNameUsageID`; the name carries its own lineage.** In the atomic-name model (Epic #30)
every name is parented under its *own* genus — a synonym sits under its own genus, independent
of its accepted name (so *Curculio forticollis*, a synonym of *Otiorhynchus fortis*, stays
parented under *Curculio* and composes to "Curculio forticollis"). This is what makes name
composition uniform for valid names and synonyms, and makes a status flip (synonym ↔ valid) a
pure **one-field toggle with no name rewrite and no re-parenting**. The tree still groups
synonyms under their accepted name via `acceptedNameUsageID`, so display is unaffected.

One invariant remains, enforced by a loud `BEFORE` trigger (migration 0031) that `RAISE`s on
any violating write — from raw SQL too — and re-declared on any future `taxon` rebuild (DB-1
discipline; `test_schema_integrity.py::test_synonym_integrity_triggers_present` guards it):

- **`trg_taxon_accepted_is_terminal`** — `acceptedNameUsageID` must reference an accepted
  (terminal) name, never another synonym. This is GBIF's *chained synonym* rule.

> **Retired (migration 0033):** `trg_taxon_synonym_parent_matches_accepted` — the project's
> former stricter rule that a synonym shared its accepted name's `parentNameUsageID`. It was
> dropped when the model moved to own-lineage parenting. Do **not** re-introduce it.

**Single writers (chokepoint).** Every parent / accepted-link mutation on an *existing* taxon
goes through `app/services/taxa.py`: `synonymize()` (resolve target to terminal, flatten the
name's own synonyms onto it — parent and name untouched), `make_accepted()` (clear the link
only), `reparent()` (re-home an accepted name; synonyms are *not* touched, they carry their
own lineage). A static test
(`test_synonym_integrity.py::test_parent_and_accepted_writes_are_centralised`) fails if any
code outside `taxa.py` assigns these columns directly. **No fallback defaults** — required
links are inherited or the op fails loudly, never guessed.

**Manual audit, not automatic.** `verify_taxon_consistency(session)` is a read-only check
(Taxonomy-tab "Check consistency" button) that reports drift the trigger structurally cannot
catch at write time (e.g. a raw-SQL edit that chains a synonym after the fact). Issue names
follow GBIF's `NameUsageIssue` vocabulary (`CHAINED_SYNONYM`, `PARENT_NAME_USAGE_ID_INVALID`,
`ACCEPTED_NAME_USAGE_ID_INVALID`). It is **not** run at startup.

### Parent-rank taxon rows

Every TW species import creates dedicated `taxon` rows for each ancestor rank (genus,
subgenus, tribe, subfamily, family, order) via `_ensure_parent_rows()` in
`app/services/taxa.py`. Each ancestor row is linked to its own parent via
`dwc:parentNameUsageID`. Every writer (TW, POWO, manual) sets the atomic `name_element`
and **composes** `dwc:scientificName` from it + the parent chain (`compose_scientific_name`);
rows are matched by `(composed dwc:scientificName, dwc:taxonRank)` or OTU id. A subgenus
ancestor is therefore stored composed as `Genus (Subgenus)`, a species ancestor as
`Genus epithet`. A reparent/rename cascades via `recompose_subtree()`. Synonyms are
parented under their **own** lineage (own genus), never the accepted name's (Epic #30).

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
| **Digitize** | Main specimen entry form: collecting event (search/create), taxon (local-first search + TW fallback), sex, count, preparations, notes. Saves to DB. Standard/Visiting modes queue **no** labels (see "Print-queue policy by create mode"); only Mounting queues a sheet. Two layouts (see "Digitize layout modes"). |
| **Records** | View/edit a single specimen or collecting event (search → detail edit form). The shared `specimen_form` / `collecting_event_form` widgets. Reached directly or drilled into from Explore (`open_specimen` / `open_event` handle). |
| **Explore** | Dataset browse/query (#40): one faceted search bar (taxa / geography / collectors) drives a **drawer-order taxa checklist** (family→genus headers, species rows w/ material count + ⚠ needs-attention, expand → lots) and an **events** view; click drills into Records; CSV export. Service: `app/services/explore.py`. Map view is Phase C (not built). |
| **Taxonomy** | Checklist tree (family → synonyms). Filter by rank. Links to TaxonPages. Rebuilds on every tab switch and on every save (via `_refreshers["taxonomy_tree"]`). |
| **Labels** | Generate identifier label batches (4-char codes). Preview + download PDF. Reprint a whole batch if unused. Staged-codes dashboard. |
| **Print queue** | Preview and print all staged labels in one grouped PDF (per queue addition; data/identifier/determination column-aligned per specimen). Saves the PDF to `printed_pdf_dir` on print, then clears the queue. |
| **Import & Assign** | Upload a DwC CSV; live-filter rows; assign taxon + per-specimen fields; save to DB. |

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
| `explore.py` | Explore-tab querying (#40): `search_facets`, `query_specimens(filters)`, `checklist(filters)` (drawer-order taxa+lots), `events(filters)`, `to_csv`, `counts` |

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
- **Migration discipline — never lose constraints.** A `batch_alter_table(recreate=...)`
  (or any table rebuild) reflects columns but **silently drops STRICT typing, CHECK +
  UNIQUE constraints, and FK `ON DELETE` actions**. Any migration that rebuilds a table MUST
  re-declare *all* of them (STRICT, every CHECK, every UNIQUE, every FK action, server
  DEFAULTs). This is what caused DB-1 (migrations 0021/0024); migration 0029 restored it.
  `tests/test_schema_integrity.py` guards against recurrence — it fails loudly if any STRICT
  table loses STRICT, a CHECK, a UNIQUE, or an FK action. Run the suite after any migration.
  - **The models are NOT a complete schema mirror.** SQLAlchemy can't express STRICT, and
    historically some constraints lived *only* in migration DDL: `biological_association`'s
    exclusive-arc CHECKs (unnamed, mig 0007) and `collection_object`'s `UNIQUE(collectionCode,
    catalogNumber)` (undeclared in
    the model until 0029 — which is how 0029's first draft re-dropped it). Generating
    rebuild DDL *from the models* will drop anything the model doesn't declare. Prefer
    adding the constraint to the model so it's authoritative.
  - **Restoring the live DB from a `.db` backup:** the DB is in WAL mode — `rm` the stale
    `data/collection.db-wal`/`-shm` (or checkpoint) when swapping the file, or SQLite
    replays the old WAL and the restore silently appears to do nothing.
- Data transforms → standalone, deterministic, tested scripts. No LLM in the data path.
- Heavily test the **sync diff** and **habitat ambiguity** logic.
- Comment any TaxonWorks behavioural assumption with its source (`file:line` or API route).
- Don't add dependencies casually; pin them; don't touch other conda envs.
- Keep the UI layer thin; logic lives in service/repository functions callable from scripts.
- `reserve_sequential_codes()` returns `(batch_id, codes_list)` — always unpack both values.

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
  tab switches keep the SPA alive, so they never warn). Detection is **value-based on every
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

Every form field falls into exactly one of three tiers; this must be consistent across all
tabs (Digitize, Records, Import & Assign). **The full template — field tables, the Tier-2
`push_pin` placement rule + implementation pattern, and the Tier-3 read-only display
template — lives in `docs/design.md` → "Auto-fill tiers". Use it when adding any new field.**
Summary:

- **Tier 1 — auto-filled, editable.** Pre-filled with a sensible constant the user can change
  before saving (`basisOfRecord` = `"PreservedSpecimen"`, `disposition` = `"in collection"`).
- **Tier 2 — one-click default.** Field starts empty; a `push_pin` button adjacent to it
  inserts the configured default (`identifiedBy`, `recordedBy`, `dateIdentified`). Never
  applied silently — the user must click.
- **Tier 3 — background invisible default.** Written silently into every saved record, never
  shown as an editable field (`institutionCode`, `collectionCode`). Configured once in Settings.

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

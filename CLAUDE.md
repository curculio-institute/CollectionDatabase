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
- **`docs/plant_names.md`** — *the plant name lifecycle*: where plant names come from
  (local → TaxonWorks → WCVP), the offline WCVP index and how it is refreshed, which WCVP
  statuses are importable and which are refused, what an import creates, and why an imported
  name is thereafter local and never rewritten.
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

**One canonical form, stored not sniffed (#67, 2026-07-12).** An override is **sanitised on the
way in** (`labels.canonical_override`, called by `set_override_for_identical`), so the DB holds
exactly one form and rendering is a pass-through — *what the preview showed is what prints*.
Four defects came from getting this wrong, and each is a rule now:

| rule | what went wrong without it |
|---|---|
| **An override that renders to nothing is `None`, not an empty override.** `''`, whitespace, `<div></div>` all clear it → the auto text. | `text_override is not None` treated `''` as real, so the label printed **blank**. A blank label pinned to a specimen is a curation error; the record still holds the data, so falling back to auto is always the honest answer. |
| **A tag we do not emit is text, not markup.** `_looks_like_html` matches only `div/p/br/b/i/em/strong`; the sanitizer emits any *unknown* tag escaped. | `<\w+` took `Quercus <robur>` for HTML and **silently deleted** `<robur>` from the label. |
| **Input from the editor is always HTML — sanitise it, never sniff it.** (`_override_html` still sniffs, but only to render *legacy* plaintext overrides.) | innerHTML is already entity-encoded: `R & D` arrives as `R &amp; D`, looked tagless, was escaped a *second* time, and printed as the literal `R &amp; D`. |
| **A row with no auto text has no identity — editable alone, never grouped.** | Identity is "same auto text". A determination label on a specimen with **no current ID** has none: the preview hashed the `—` placeholder (so it grouped them and offered an edit) while the store returned `0` and **dropped the edit silently**; `_co_to_det_label` then returned `None`, so even a stored override never reached the paper. Grouping them instead would be worse — they share only their *emptiness*, so one hand-written name would be stamped onto every unidentified specimen in the queue. |

`canonical_override` is **idempotent** — the stored form is fed back through the editor on the
next edit, so it must be a fixed point.

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
| App / UI         | **NiceGUI** (on FastAPI)                | Pure-Python UI; renders a real web frontend; **runs in the browser** at localhost — deliberately, not a native window (see below). |
| Labels / PDF     | **WeasyPrint**                          | Generates tiny specimen labels (≤18×7 mm) as PDF. Micro-font via Context Condensed. |
| Spatial          | **GeoPandas**                           | Habitat enrichment as a standalone batch script (Phase 3, not yet built). |
| Future analytics | **DuckDB**                              | Not the store. Optional later layer. Do not introduce yet. |

**The browser is the UI, not an accident (decided).** NiceGUI can open a native window
(`ui.run(native=True)` + `pywebview`), and it was considered. We stay in the browser because:

- **Attached media and label PDFs open in a tab.** The media store is served at `/media`, so an
  image or a print sheet gets the browser's own viewer — zoom, save, print, several open at
  once. A native webview would mean reimplementing that; WebKitGTK cannot even display PDFs.
- **The unsaved-changes guard is a browser contract.** `beforeunload` (main.py) is what warns
  before a page close discards typed-but-unsaved data (#41). A pywebview window does not fire
  it; its `confirm_close` is a generic "really quit?" with no idea which tab is dirty. Going
  native would silently downgrade a deliberate data-safety mechanism.

Packaging (a single executable) does **not** require a native window: the launcher starts the
server and opens the user's default browser.

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
| `collection_object` | One physical specimen or lot. `catalog_number` (NOT NULL) is the stable, immutable sync join key. `repository_id` FK → `repository` (NOT NULL, ON DELETE RESTRICT — migration 0047, #75) is the **single source of truth for collection membership**; the old denormalised `dwc:collectionCode` + `dwc:institutionCode` text columns were dropped (codes resolve through the repository at export; `UNIQUE(repository_id, catalogNumber)`). `dwc:basisOfRecord`, `dwc:sex`, `dwc:typeStatus`, etc. `preparation_id` FK → `preparation` and `disposition_id` FK → `disposition` (controlled vocabs, not free text — migrations 0039/0048; see "Controlled vocabularies"). `dwc:otherCatalogNumbers` (free text) records prior catalog numbers from previous owning institutions (migration 0049, #77; previous institutions themselves are not recorded). |
| `collecting_event` | Where/when collected; shared by many specimens. Full DwC locality + coordinate block. `dwc:eventDate` supports ISO 8601 intervals (`2024-06-15/2024-06-20`). `dwc:recordedBy` FK → `person(full_name)`. `habitat_id` + `sampling_protocol_id` (migration 0040) and the geography hierarchy `country_id` / `state_province_id` / `administrative_region_id` / `county_id` / `island_id` (migration 0041) are all controlled-vocab FKs (see "Controlled vocabularies"). `municipality` + `locality` stay free text. **No `dwc:countryCode` column** — dropped in 0057; it is derived from `country.iso_code` (a stored copy drifted: `Germany` could carry `FR`). |
| `taxon` | Local OTU analogue. DwC parent-link model (GBIF best practices). Columns: `name_element` (atomic source of truth — this rank's own epithet/uninomial, e.g. `crypticus`; migration 0032, Epic #30), `dwc:scientificName` (the *composed* full name without authorship, e.g. `Otiorhynchus crypticus`, maintained from `name_element` + the parent chain), `dwc:taxonRank`, `dwc:scientificNameAuthorship`, `dwc:parentNameUsageID` (self-FK, encodes hierarchy), `dwc:acceptedNameUsageID` (self-FK, marks synonyms — its presence *is* synonym status; `taxonomicStatus` is derived at export, not stored, see below), `taxonworksOtuID`, `ipniID` (the IPNI id of the name a row was imported from — identity like `taxonworksOtuID`, not a `dwc:` term; migration 0053). `dwc:nomenclaturalCode` is **NOT NULL + CHECK**-constrained to the closed list (migration 0054, #96): it is a property of the source or inherited from the parent, never guessed. No denormalised rank columns. |
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
- **An asset's metadata belongs to the photograph, not to the record it hangs on (#63).**
  `license` / `rights_holder_id` / `category` live on the shared `media` row and are applied
  **only when that row is created**. Attaching byte-identical content resolves to the
  *existing* row (that is the point of the content-addressed store), and writing the upload
  form's values onto it silently rewrote the licence of every record already using that
  photograph. Metadata is changed deliberately, via `update_media`. Per-usage fields
  (`caption`, `is_primary`, `sort_order`) live on `media_attachment` and are always set.
  Sharing one photo across several *events* is legitimate — one place, two nearby events.
- Deleting the last attachment of a media asset removes the orphaned `media` row **and**
  its on-disk bytes; shared content is kept while still referenced. **Commit first, unlink
  second** (#63): `delete_attachment()` returns the orphaned relative path without touching
  the disk, and `delete_attachment_and_file()` unlinks it *after* the transaction commits.
  Unlinking inside the transaction is irreversible while the DB half is not — a failed commit
  restored the `media` row pointing at bytes that were already gone, which nothing can repair;
  an orphaned file is merely a tidy-up job. Snapshots cover the `.db` only — `data/media/` is
  backed up separately, and a rolled-back upload can still leave an orphan file (bytes are
  written before the row commits); an orphan-sweep is a planned maintenance action.
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

### Confidential / privacy flag (decided)

Some records must be withheld when the dataset is exported to TaxonWorks (e.g. a
sensitive locality, or a collector who must not be named publicly). A **local-only**
`confidential` flag (`INTEGER NOT NULL DEFAULT 0`, named `CHECK (… IN (0,1))`; migration
0043, native `ADD COLUMN` so STRICT/CHECK/FK on the two STRICT tables are preserved) lives
on **three** tables, with **two different export semantics**:

| Table | Confidential means (at DwC export — **Phase 3, contract only; not yet wired**) |
|-------|------------------------------------------------------------------------------|
| `person` | **Obscure, don't drop.** The occurrence is still exported, but everywhere this person is `recordedBy` / `identifiedBy` the name is replaced with `config.confidential_person_label` (default `"Collector obscured (Privacy Policy)"`). |
| `collection_object` | **Drop.** The specimen is omitted from the export entirely. |
| `collecting_event` | **Drop its specimens.** A confidential event withholds *all* its specimens from the export (you cannot keep the occurrence but blank the locality — that breaks the record). |

The flag is **never pushed** to TaxonWorks (not a DwC term); it only governs what the
exporter emits. It is **not** stored in `config.json` for the same reason person defaults
aren't: the obscure-label *string* is config (survives a DB wipe), but the per-record flags
are DB columns.

- **UI** (progressive disclosure — only the rare sensitive record sets it):
  - **Specimen / Event** — a compact `Confidential` checkbox sharing the card-footer line
    with the media / external-id / life-stage icons (saves vertical space). In the shared
    `specimen_form` (`conf_chk`) and `collecting_event_form` (footer row + `footer_slot`),
    so it appears in both Digitize and Records; seeded from the record, round-tripped on save.
  - **Person** — `Consented` (✅) and `Confidential` (🔒) columns in the Controlled
    Vocabularies → People table + checkboxes in the add / edit dialogs
    (`persons_svc.create_person` / `update_person` take `confidential=` / `consent_approved=`).

**Person consent (migration 0044).** A person also carries `consent_approved` ("Consented —
export with name"): the collector was **asked and agreed** to be published under their name.
It is the **opposite** of `confidential` and the two are **mutually exclusive** — enforced at
three levels: a DB CHECK (`ck_person_consent_xor_confidential`), a service guard
(`persons._check_consent_exclusive`, friendly `ValueError`), and UI auto-uncheck (checking one
clears the other). `consent_approved` is informational/curatorial (the consent audit trail);
`confidential` is what drives export obscuring. ORCID is stored **verbatim** (full
`https://orcid.org/…` URI; the form placeholder shows the URL form) — never reformatted.

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
| disposition | `collection_object.disposition_id` | `disposition` | 0048 | was `dwc:disposition` TEXT + a **closed** CHECK; opened up (#76) so holdings like "loaned to Jeffrey" are recordable. **Seeded** with the former six values. DwC `[Not mapped]` by TW |
| habitat | `collecting_event.habitat_id` | `habitat` | 0040 | was `dwc:habitat` TEXT |
| samplingProtocol | `collecting_event.sampling_protocol_id` | `sampling_protocol` | 0040 | was a hardcoded UI list; table **seeded** with the curated set |
| country | `collecting_event.country_id` | `country` | 0041, **0056/0057** | English name (OSM `name:en`); **`iso_code`** = ISO 3166-1 (`DE`). `dwc:countryCode` is **derived from it at export/label time**, not stored (0057) |
| stateProvince | `collecting_event.state_province_id` | `state_province` | 0041, **0055/0056** | English name (OSM `name:en`); **`iso_code`** = ISO 3166-2 (`DE-BY`) |
| administrative region | `collecting_event.administrative_region_id` | `administrative_region` | 0041 | Regierungsbezirk tier; **no DwC term** (local field); local name |
| county | `collecting_event.county_id` | `county` | 0041 | local name (Landkreis) |
| island | `collecting_event.island_id` | `island` | 0041 | local name |

**Which fields qualify:** *open, user-coined* vocabularies where consistency tooling (merge)
helps. **Closed standard vocabularies stay fixed CHECK-constrained lists, NOT editable tables**
— `sex`, `basisOfRecord`, `identificationQualifier`, `license` (editability would create
`Male`/`male` duplicates or values TW rejects). **`disposition` was on this closed list but
moved to an editable vocab (#76, migration 0048):** it is genuinely user-coined (the user needs
arbitrary holdings like "loaned to Jeffrey"), and DwC `disposition` is `[Not mapped]` by TW, so
freeform values never reach TaxonWorks — the duplicate/reject risk that keeps the others closed
doesn't apply.

**A geography vocab row is identified by `(name, iso_code)`, never by name (decided 2026-07-10;
migration 0056).** A subdivision *name* does not identify a subdivision: of the 5,420 ISO 3166-2
subdivisions, **40 names are shared across different countries** (checked against Wikidata `P300`)
— `Limburg` = `BE-VLI` + `NL-LI`, `Punjab` = `IN-PB` + `PK-PB`, `Central Province` = six countries.
Under `UNIQUE(name)` a Dutch-Limburg specimen either silently reused the Belgian row or, with the
short-lived fill-once ISO stamp, **refused to save**. Both are wrong.

The rule, applied identically to `country` (ISO 3166-1) and `state_province` (ISO 3166-2):

> **exact match on `(name, iso_code)` → reuse; anything else → create a new row.**

**No existing row is ever mutated to carry a code it did not have, and no save is ever refused.**
A hand-typed `Limburg` must not be silently declared Dutch by a later geocode; it stays an uncoded
row, and the user folds it with the **merge tool** if the two really are the same place. Duplicates
are the *expected*, cheap outcome — merge exists precisely for this.

`UNIQUE(name)` is replaced by a unique index on **`(name, IFNULL(iso_code, ''))`**. The `IFNULL` is
load-bearing: SQLite treats `NULL != NULL`, so a plain `UNIQUE(name, iso_code)` would let every
hand-typed save create yet another uncoded duplicate. Result: exactly one uncoded row per name,
plus one row per distinct code. `Vocabulary(code_attr=…)` implements the match; `display_label()`
renders `Limburg (NL-LI)` where a bare name would be ambiguous. Existing rows keep `iso_code =
NULL` and are **not** back-filled — 40 names are ambiguous, so a name→code backfill would be a guess.

**The label's "Country, State:" prefix collapses exactly one side (decided 2026-07-10).** An
18x7 mm label has no room for `Germany, Baden-Württemberg`, but `DE, BW` is a cipher — so **one
of the two always stays written out**, and the *longer* name gives way
(`label_text.format_geo_prefix`, used by both the PDF and the previews):

| country / state | prints | why |
|---|---|---|
| Germany / Bavaria | `Germany, Bavaria:` | both short |
| Germany / Baden-Württemberg | `Germany, BW:` | the state is longer → its subdivision suffix |
| Greece / Peloponnese Region | `GR, Peloponnese Region:` | `GR-J` → suffix `J` is useless → the **country** collapses |
| Sri Lanka / Central Province | `LK, Central Province:` | `LK-2` → a bare digit is useless |
| France / Corsica Region | `FR, Corsica Region:` | `FR-2A` → a letter-digit code is not an abbreviation |
| Germany / Baden-Württemberg (uncoded row) | `DE, Baden-Württemberg:` | no state code to collapse to |

The state collapses to the **suffix** (`BY`), unambiguous precisely because the country stands
beside it — but only when that suffix reads as an abbreviation: **≥2 characters, all letters**.
Measured against all 5,351 ISO 3166-2 codes, that refuses 18 mixed letter-digit codes (`FR-2A`,
`NP-P2`, `CZ-20A`, `GR-A1`, `NL-BQ1`) on top of the 353 single-character and ~49% numeric ones. A row with **no ISO code**
cannot collapse: its name stays and the label grows rather than losing the locality. With no state,
the old long-country rule (`format_country`) still applies, so a lone `United Kingdom` prints `GB`.

**`dwc:countryCode` is not stored (0057).** Once the country row carries `iso_code`, an event
column holding the same fact is a second source that drifts — nothing tied them, so `country =
Germany` with `countryCode = FR` saved happily. Same rule, same reason as `dwc:taxonomicStatus`
(0030): **derive it at export, never store it.** `label_text.format_country()` reads
`country_obj.iso_code`; the DwC-CSV importer's `countryCode` now *resolves the country row* by
`(name, code)` instead of populating a column. The migration first folds each event's code onto
its country row (only where the events agree on one code) so nothing is lost.

Below the first-order subdivision **there is no ISO code**, so `county` / `island` /
`administrative_region` keep `UNIQUE(name)` and remain wrong in the same way, with no honest fix.
`municipality` is free text and stays so — *decided*: a vocab there would invite merging
`Biberbach` into `Biberach`, two real Bavarian places, and the geocoder's `Municipality of Tripoli`
vs `Tripoli` is a naming problem, not a merge problem.

**Geography is the exception that proves the rule (revised, #40):** the administrative levels
*are* controlled vocabs — even though they come from geocoding, the geocoder yields
language/spelling variants (`Deutschland` vs `Germany`) and the faceted Explore search demands
consistency, so they're FK vocabs with merge. `municipality` + `locality` stay free text
(too specific; municipality search is "the map's job"). **Resolution lives in the events
service** (`_resolve_geo_fields` in `events.py` maps the name keys → `*_id` via get_or_create
in `create`/`update_collecting_event`), so the event form keeps its geocode-input widgets +
boundary-warning UI **unchanged** — only the service + the model-read sites resolve by FK.
Language policy: country + stateProvince in English (Photon `lang=en`), the rest local.

**Geocoding: containment vs proximity (decided 2026-07-10; `collecting_event_form._reverse_geocode`, #40).**
The two services answer **different questions**, and the administrative hierarchy must come from
the one that answers *containment*:

- **Overpass `is_in` owns the hierarchy.** It returns the polygons that actually *contain* the
  point. The **state is identified by its `ISO3166-2` tag**, never by `admin_level` — the level
  differs by country (`DE-BY` sits at L4, `GR-J` at L5), so a positional rule is wrong. The
  country relation carries `ISO3166-1`, which supplies `dwc:countryCode` from the data (see
  "No hardcoded country codes"). English names come from `name:en`, present on country/state
  everywhere tested; the lower tiers have none and are stored with their local `name`, which is
  the language policy anyway. `administrative_region` (Regierungsbezirk) is an L5 that is *not*
  itself the ISO state.
- **Photon owns locality candidates only.** `/reverse` is a **proximity search** with an implicit
  radius (~1 km; the `radius` param has a low ceiling — 3 km and 10 km returned the same 1.15 km
  of results). Its `city`/`county`/`state` describe the **nearest feature**, not the query point,
  so they are wrong whenever that feature lies across a boundary (measured: at 47.68,11.12 Photon
  reports `Seehausen am Staffelsee`; the point is inside `Uffing am Staffelsee`). Never read the
  hierarchy from Photon. Candidates must be **distance-filtered against the uncertainty circle**
  using the geometry Photon returns (it carries no distance field).
- Photon *does* return wetlands, streams, peaks and `place=locality` — the earlier claim that it
  yields "only buildings/streets" was false — so the Overpass `around:` block that re-fetched them
  is redundant. (It uniquely supplies a peak's `ele`.) Where nothing is within its radius it
  returns **zero features** (open sea, steppe), which must not blank the form: Overpass still
  answers, and Photon's hierarchy stays only as an explicit degraded fallback when Overpass fails.

**One Overpass request per lookup — never two in parallel (measured 2026-07-10; dead end, do
not retry).** The hierarchy and the enclosing-areas blocks look separable (`is_in` admin ≈ 2–24 s,
areas ≈ 1.2 s), and splitting them into two concurrent requests to fill the island/locality fields
sooner is the obvious move. It is **8× slower and fails outright** — the public instance grants an
IP only a couple of concurrent slots, so the second request queues behind the first, times out, and
the retry backoff compounds. Six runs, alternating, 20 s apart:

| shape | median | max | succeeded |
|---|---|---|---|
| one combined query | **3.72 s** | 13.75 s | 6/6 |
| two parallel queries | 29.19 s | 37.18 s | **0/6** |

`is_in` is evaluated once and both blocks pivot off it, so bundling is cheaper server-side too.
The failure is *silent-ish* in the UI: a failed admin query drops into the degraded Photon
fallback, which sets `administrative_region = ""` (Photon has no Regierungsbezirk), so the symptom
is a mysteriously empty admin-region field, not an error.

**Progressive fill (decided).** Photon (~0.15 s) and Overpass (~3.7 s median) each write **their
own** fields the moment they land — never one `gather` applied after the slowest returns, which
made a locality known at 150 ms wait on a 24 s containment query. Each geocode-owned field carries
its own spinner; the button reads "Detecting…" and disables. **All geocode-owned fields are blanked
before a lookup starts**: with sources landing separately the previous point's values would
otherwise sit in the form during the new lookup, and a source that finds nothing (Photon returns
zero features at 26.015/101.883) would leave them there permanently. Empty + spinner = being looked
up; empty after = nothing there. A locality carried over from another specimen's coordinates is the
"silent wrong value" of §2.

**Nominatim is not used** (rejected for per-IP rate limiting after 429s from firing on every pin
drag; the manual Lookup button removed that condition, but the `ISO3166-2` tag makes its only
remaining advantage — per-country level→field mapping — unnecessary). Its 1 req/s policy also
rules it out for the 4-point perimeter boundary check.

**Overpass retries** (`_overpass_post`): 429/502/503/504 + transport errors, backoff 1 s then 3 s,
three attempts, under a **hard 40 s deadline** (each attempt's timeout is shrunk to the time
remaining, or a fresh 25 s attempt started at t=26 s would run to 51 s). A 4xx is **not** retried —
that is a bug in our query, not a busy server. Measured: `is_in` at 26.015/101.883 returned 504
once, then answered on retry.

**No mirror failover (measured 2026-07-10; dead end, do not add).** `overpass-api.de` allows only
**2 concurrent queries per IP** (`/api/status`), and it load-balances between two backends
(`gall.` / `lambert.openstreetmap.de`) — which is why one `is_in` costs 1.2 s or 24 s. Adding
mirrors looks like the obvious hedge. It is not, on the Augsburg point:

| endpoint | result |
|---|---|
| `overpass-api.de` | 1.13 s, 6 admin rows ✓ |
| `overpass.kumi.systems` | ReadTimeout at 35 s |
| `overpass.private.coffee` | ReadTimeout at 35 s |
| `overpass.osm.jp` | ConnectError |
| `overpass.osm.ch` | **HTTP 200, zero admin rows** — regional (Swiss) data only |

`osm.ch` is the trap: it *succeeds* with an empty result for a German point, so a failover would
silently report "this point lies in no administrative area" and blank the hierarchy — a silent
wrong value (§2), which is worse than the honest failure. One endpoint, honest errors.

**Errors must say why** (`_overpass_status` / `_overpass_failure_message`). "Overpass unavailable"
gives the user nothing to act on. On failure, read `/api/status` (plain text; needs a `User-Agent`
or Apache answers 406) and report the slot budget: *0 of 2 query slots free for this computer*
(rate-limited — wait), or *2 of 2 free, so this looks like server load* (retry now). If the status
page is unreachable too, **suggest** a rate limit, never assert one. The degraded Photon fallback
also states that the fields came from the nearest feature rather than the containing areas, so the
user knows to check them.

**Boundary-crossing check — one combined `is_in` (measured 2026-07-10).** Whether the uncertainty
*circle* crosses a boundary cannot be answered by any single query at the centre. Two dead ends are
already in the history, do not retry them: `relation(around:r)` matches boundary relations by **way
node** proximity, so a small circle inside an admin area returns 0 results even when it visibly
crosses a border (`e4c27bb`); and 8 parallel Photon calls trip 503s, silently dropping points
(`cd2b6c9`, the Basel tripoint lost France). Sampling perimeter points is therefore still required,
and it is a **warning heuristic, not a guarantee** — 4 bearings cannot prove a circle is
boundary-free (`74713ce`).

Overpass takes **several `is_in` in one request**: `is_in(lat,lon)->.pN;
relation(pivot.pN)[boundary=administrative][name]; convert pt ::id=id(), i="N", …; out;` per point.
`convert` stamps the sample index onto each relation so results can be attributed back (absent tags
come back as `""`, not null). Verified at the Basel tripoint: one request, centre + 4 perimeter →
DE / FR / CH all detected; an interior point yields one hierarchy, no false positive. This also
returns the **centre's** full hierarchy, so it subsumes the separate hierarchy lookup.

**Cost: ~9–12 s** (a single `is_in` is ~0.9 s; five is roughly linear), and that shape has returned
504. So it **must not block the form.** Fill from the fast calls first and let this land later —
`is_in` is *areas*, so `relation(pivot.…)` is required (`rel.pN[…]` silently matches nothing).

**Built (#110, 2026-07-12): `_boundary_hierarchies()` in `collecting_event_form.py`.** Centre and
perimeter are now both **containment**, resolved through the **same `_resolve_hierarchy`** — which
is the whole point: the old check sampled the perimeter with **Photon**, whose `/reverse` reports
the *nearest feature's* tiers, so it both invented and missed crossings. Measured on the two points
that matter:

| point | before (Photon perimeter) | after (containment perimeter) |
|---|---|---|
| Peloponnese `37.5089, 22.3745` | perimeter said `Peloponnese, Western Greece and the Ionian` (the **L4** Decentralized Administration) vs the centre's `Peloponnese Region` (**GR-J**, L5) → **false crossing on every Greek lookup** | all 4 samples = `Peloponnese Region` (GR-J) → **no warning** (2.1 s) |
| Basel tripoint | France was silently dropped (503s) | DE `Baden-Württemberg` / CH `Basel-City` / FR `Haut-Rhin` all detected (17.2 s) |

Two consequences worth stating:

- **A warning now offers a state WITH its ISO code** (`GR-J`, `DE-BW`) — containment identifies the
  state *by* that tag. Photon carried none, so a state picked from a warning used to be stored as
  an uncoded vocab row (and `(name, iso_code)` is the vocab's identity).
- **Picking an alternative sets only that field.** `_apply_snap` used to write the whole snapshot,
  so choosing a *municipality* alternative silently overwrote a correct `stateProvince`. A boundary
  warning says "the circle also reaches X **at this tier**"; it is not a claim about the others.
- **Locality is not an administrative tier**, so it is not part of the check. Its warning button
  remains the "also nearby" candidate picker fed by the main geocode.
- **A failed request warns about nothing and shows no ✓.** An empty result would read as "this
  point lies in no administrative area" — the silent wrong value of §2.

**Open (do not guess):** `county` / `municipality` have **no ISO tag** below the first-order
subdivision. `L6`=county and lowest `L7+`=municipality held across DE/GR/KZ, but that is a
three-country regularity, not a standard — validate it before relying on it. France already
strains it (`L5 European Collectivity of Alsace` is not a Regierungsbezirk tier). Germany strains
it too: a **kreisfreie Stadt** is both Kreis and Gemeinde and appears **once**, at L6 — so at
Augsburg (48.3324519, 10.9251308) `county=Augsburg` and `municipality` is **empty**. Copying county
→ municipality would be a guess; leave it empty until the tier can be identified from the data
(e.g. `de:place=city` / `admin_level` + `place` tags), not from its position.

The shared mechanism:

- **`app/services/vocab.py` → `Vocabulary`** — generic CRUD + merge for any single-name table
  (`id`, `name` UNIQUE), parameterised by the model. `list` / `options` / `get_or_create` /
  `create` / `update` / `delete` / `merge_preview` / `merge`. Merge and delete-safety
  **re-discover referencing FK columns dynamically** via `PRAGMA foreign_key_list` (same
  mechanism as `merge_persons`), so any FK at the vocab table — present or future — is handled
  with no hardcoded list. `delete` is blocked while referenced (and the FK is `ON DELETE
  RESTRICT` as a DB backstop). **Optional default flag:** a vocab constructed with
  `has_default=True` (its table needs an `is_default` column + partial-unique index) gets
  `get_default` / `get_default_name` / `set_default` — a single flaggable **Tier-1 autofill
  default** (mirrors repository `is_default`, #83). `preparation` uses it (migration 0052);
  the Controlled-Vocab card shows a ★ toggle when `VocabSpec.supports_default`.
- **`app/services/vocabularies.py`** — instances + a `VOCAB_REGISTRY` of `VocabSpec`s (display
  metadata; `supports_default` opts a vocab into the ★ default affordance). **Adding a vocab =
  a model + a migration + one registry entry**; it then appears in the Controlled Vocabularies
  tab and gets a dropdown field automatically.
- **`app/ui/vocab_field.py` → `build_vocab_field`** — the data-entry widget, the *same*
  custom-dropdown UX as the person field (`✚ add <typed>` + existing matches, no free-text
  escape; reuses person_field's CSS/nav). `get_value()` is the name; **`commit(session)`
  resolves name → id (get_or_create) inside the save transaction and returns the FK** (exactly
  like person `commit`). Used in Digitize/Records (shared `specimen_form`), Import & Assign,
  Mounting, and — since the geography levels are vocabs too — the **collecting-event form**
  (`country` / `stateProvince` / `admin. region` / `county` / `island`; `_VocabInput` adapts
  the handle to the `.value` interface the form's field registry drives).
  - **Code-bearing vocabs** (`code_attr`) additionally carry `get_code()` / `set_value(name,
    code)`, and render the ISO code as a **pill** — on existing rows *and* on `✚ add Greece
    GR`, because a geocoded name can be new to the vocab yet already carry a code. The widget
    lists **`vocab.entries()`** (one row per DB row), never `options()` (a `{name: name}` dict
    that silently collapses `Limburg` BE-VLI / NL-LI into one).
  - The event form does **not** use `commit()` for country/state: `events._resolve_geo_fields`
    stays the single owner of `(name, iso_code)` resolution, so every write path (Digitize,
    Records, Import) resolves identically.
- **DwC export** resolves `preparation_id` → `preparation.name` → `dwc:preparations` at export
  time (mirrors `recordedBy`/`identifiedBy`; nothing denormalised on `collection_object`).
- **Controlled Vocabularies tab** renders one card per registry entry (`_build_vocab_section`)
  with edit / merge / delete / add — the generic mirror of the People card. **One section per
  tab** (People, Collections/Institutions, then the registry), always on — these lists grow
  without bound. Code-bearing vocabs show an **`ISO code` column**, editable in the add + edit
  dialogs (the user must be able to supply a code the geocoder never found), and the **merge
  dialog labels rows with `display_label()`** — without the code, the two `Limburg` rows are
  indistinguishable in the very dialog that permanently deletes one. Build detail (why NiceGUI
  tabs and not the Digitize chip bar) → design.md.
- **Person stays separate** (not folded into `Vocabulary`): it carries extra columns
  (`abbreviated_name`, `orcid`) and label-printing logic. `Vocabulary` is for the *single-name*
  case only.
- **Collections/institutions (`repository`) stay separate too** (multi-column, #56): keyed
  by `dwc:collectionCode`, with `collection_full_name` / `institution_full_name` and the two
  TaxonWorks ids (institution=Repository, collection=Namespace). DwC-mapping columns carry the
  `dwc:` prefix; the rest are local. It is the source for the identifier label's full
  collection name (`repositories.name_map`, resolved by the code prefix `JJPC-00304`→`JJPC`)
  and for the **default collection** (`is_default` flag, migration 0050, #83 — the home
  collection that stamps new specimens' `repository_id` + catalog-number prefix; see the
  namespace section). Carries an optional **contact/owner `person_id`** (FK → `person`, ON
  DELETE RESTRICT, nullable — migration 0051, #79; a single person per collection, no roles;
  `merge_persons`/delete re-point/block it via the dynamic PRAGMA FK discovery). Its own
  Controlled-Vocabularies card (not the generic single-name one). Migration 0045.

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

### Offline name sources — WCVP and user-added datasets (decided 2026-07-11, **experimental**)

A *name source* is a static Darwin Core Archive (Taxon core) indexed into a read-only SQLite
lookup table, searched from the taxon widget and imported name-by-name (or in bulk). WCVP is
one instance (plants, ICN, downloaded from Kew); the user may **add others from a file on their
computer** — e.g. a Coleoptera checklist (ICZN). Engine: `services/name_source.py`; registry:
`services/datasets.py`. Marked **EXPERIMENTAL** in the UI.

- **The archive describes itself; nothing is configured by hand.** `meta.xml` declares the core
  file, its delimiter, and every field **by DwC term URI + column index** — so a field is found
  by *term*, never by the spelling of a CSV header. This is load-bearing: Kew misspells two
  headers (`scientfiicname`), a correctly-spelled archive writes `scientificName`, and both must
  work. `_key()` collapses every spelling — term URI, `taxonID`, `taxonid`, `dwc:taxonID`,
  `dwc_taxon_id`, `taxon_id` — to one lookup key (but never by blindly splitting on `_`, which
  would turn `scientific_name` into `name`). A `default` field in meta.xml is how an archive
  states its own **`nomenclaturalCode`** — so the code is *read*, never guessed from the taxa.
- **Storage:** the chosen file is **copied into** `data/name_sources/<slug>/` (archive + built
  `index.sqlite`) — never referenced in place, exactly as with the media store. Registered in
  `config.json` (`name_sources`), because these are *files and settings*, not DB entities: no FK
  points at them, so the person-defaults rule does not apply.
  **WCVP lives there too** (`data/name_sources/wcvp/`) — it is a name source like any other, so
  there is one place to look for every offline checklist and one place to back up.
  `config.migrate_legacy_dirs()` (run once from `run.py`, before anything reads an index) moves
  an older `data/wcvp` across: a **rename, never a re-download** — the archive is ~88 MB and the
  index ~270 MB, and a "missing" index would otherwise send the user to fetch both again. It
  moves only when the destination does not exist, so it can never overwrite a good install.
- **UI shape is deliberate** (Settings → Name datasets). Adding a dataset happens *once*;
  importing every name is a rare, heavy, one-way write. So the add flow lives in a **dialog
  behind a small "Add dataset…" button** — a full-width drop zone shouted the least-used control
  on the page — and it reports what it is doing (**"Indexing… N names read"**; a build that shows
  nothing reads as a hang, and WCVP is 1.45 M rows) and ends on an explicit **"<name> installed"**
  confirmation that says the names are now searchable and that **nothing needs importing up
  front**. **"Import all" sits in a per-row ⋮ menu**, not beside the row as a button: there it
  read as the *confirm* action for the install that had just finished, and it is the one action
  that must never be clicked by accident. A registered dataset whose index is missing offers
  Rebuild, never Import all.
- **Last in the search chain, always:** local → TaxonWorks → WCVP → *datasets*. A user checklist
  is the fallback when no other source knows the name, never a competitor to them. (`sources`
  tuple in `taxon_search.py`; `"datasets"` is in the default.)
- **Representability, not coercion (§2):** a `NameSourceSpec` carries the code, the selectable
  ranks (**`RANKS_BY_CODE[code]`** — an ICZN source may not offer `variety`), and the status
  partition (accepted / replaced-by-X / refused). An **unknown `taxonomicStatus` raises at build
  time** — whether it means accepted, replaced, or unrepresentable is a decision, not a guess.
  Refused names are still **shown** in search (greyed, no ✚ add), so the user learns the name
  exists instead of hand-inventing it.
- **Import walks the archive's OWN parent chain** (`lineage()` → `chain_for()` →
  `taxa.get_or_create_from_chain()`), rather than reconstructing lineage from denormalised
  family/genus columns the way the WCVP path must (WCVP models no rank above genus). A chain can
  express any lineage the source has — notably **a species under its subgenus** — and it is the
  source's own statement of placement, not our reconstruction. Own-lineage (Epic #30) is
  preserved: a synonym keeps *its own* parents; its accepted name is built as a separate chain.
- **A source may skip a rank the model needs — and the workaround must be loud.** The first
  Coleoptera archive parented all 692 subspecies straight under a **subgenus**, so the chain had
  no species row, the infraspecific name had nothing to compose from, and it silently produced
  `Carabus (Megodontus) None germarii`. Two fixes, both kept:
  - `compose_scientific_name` **never interpolates a missing part** — a bare-epithet name is a
    visible fault, not a plausible-looking lie.
  - `_species_ancestor()` recovers the species from the trinomial, **preferring the archive's own
    species row** (it carries the authorship) and synthesising only the name otherwise, with
    authorship left NULL rather than guessed.

  **That reconstruction is a defect workaround, not a feature.** A reconstructed entry carries no
  `source_id`, `datasets.import_all` counts them into `ImportReport.reconstructed_species`, and a
  non-zero count is **reported to the user** ("the archive should supply those species rows"). A
  well-formed archive reconstructs **nothing**. *(The archive was since fixed at source — it now
  ships the 211 missing species, 10,831 taxa, all 691 subspecies under a Species — so this must
  read 0 for it. If it ever fires again, something regressed; that is the point of the counter.)*
- **`import_all`** creates a taxon row for every importable name (idempotent — same seam as a
  single pick). It is warned first: a large one-way write that puts the whole checklist in the
  Taxonomy tree whether or not specimens are held. It is **not** needed to record specimens —
  picking a name in the search imports it and its parents on demand.
- Removing a dataset deletes the archive + index but **not the names imported from it**: those
  are local taxon rows now (same rule as an imported WCVP name — `docs/plant_names.md` §5).
- The **distribution extension** (locality / threatStatus) is deliberately **ignored** for now.

### Ranks are code-specific, and so is the genus group (decided 2026-07-11)

**A rank belongs to a nomenclatural code, not to a global vocabulary.** TaxonWorks is the
authority here and models the four codes as **four separate hierarchies**
(`app/models/nomenclatural_rank/{iczn,icn,icnp,icvcn}/`, each ordered by walking
`parent_rank`; TW @ `897f385`) — the same rank *name* can be a different rank class in each,
and TW has no `/ranks` API route, so the source is the only authority.

We deliberately model a **curated subset**, not a mirror of TW's ~60 ranks — but the *code
split* must be right, because offering a rank the code does not have invites a silently wrong
name (§2):

| | |
|---|---|
| ICZN only | `superorder`, `superfamily`, `supertribe` — ICN's family group is only family/subfamily/tribe/subtribe |
| ICN only | `section`, `subsection` (genus group) + `variety`, `subvariety`, `form`, `subform` — ICZN has **no rank below subspecies** |

`taxa.TAXON_RANKS` stays the single high→low ordering (hierarchy validation indexes into it);
**`RANKS_BY_CODE` / `ranks_for(code)`** give the selectable subset per code. Each subset is a
**subsequence** of `TAXON_RANKS`, so index-based parent/child rank comparisons stay valid
across codes. An unknown code offers *everything* (a new taxon before its parent is chosen);
the editor narrows the list as soon as a parent supplies the code, and `validate()` refuses a
rank the code lacks. A row whose stored rank is wrong for its code is still **kept in the
dropdown and editable** — a bad state must be reachable by the tool that repairs it.

**The editor asks for the parent BEFORE the rank**, because the code is inherited from the
parent and the code is what decides which ranks exist.

**The two codes write the genus group differently, and both halves matter** (`taxa.py`,
`_ICN_GENUS_CONNECTOR`):

| | subgenus row | species under it |
|---|---|---|
| ICZN | `Otiorhynchus (Nihus)` — brackets | `Otiorhynchus (Nihus) armadillo` — carried into the binomial |
| ICN | `Taraxacum subg. Palustria` — connector | `Taraxacum officinale` — **not** carried; a botanical binomial is genus + epithet |

So the subgenus interpolation in `compose_scientific_name` is **ICZN-only**, and a species
under a botanical subgenus *or* `section` composes as a plain binomial (the section is
classificatory, not part of the name). `Taraxacum sect. Ruderalia` uses the same connector
mechanism as the existing ICN infraspecific terms (`subsp.`/`var.`/`f.`).

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
- **No `/repositories` or `/namespaces` endpoint** (verified absent at TW `aadf21a`,
  2024-10-07; re-check on a newer release). The two TW ids on the `repository` table —
  `taxonworks_institution_id` (=TW **Repository**) and `taxonworks_collection_id` (=TW
  **Namespace**) — therefore **cannot be looked up by name via the public API**. They appear
  only as foreign-key integers on other records: `repository_id` on
  `/api/v1/collection_objects/:id` (id only — not resolvable to a name in v1), and
  `namespace_id` on `/api/v1/identifiers/:id` (resolvable: `extend=[namespace]` embeds
  `id/name/short_name/institution`). So **populate these two columns by hand** (or read them
  back off an already-uploaded record to verify) — **do not plan an automated "fetch the TW
  ids" step** unless a future TW release adds the endpoints.
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
| `repositories.py` | Collections/institutions CRUD (multi-column vocab, #56) + `name_map` (collectionCode→full name) for the identifier label + `resolve_id` (get-or-create the repository for a code, the save-time seam for `collection_object.repository_id`, #75) + `delete_repository` guard (blocked while specimens reference it, #72) |
| `explore.py` | Explore-tab querying (#40): `search_facets`, `query_specimens(filters)`, `checklist(filters)` (drawer-order taxa+lots), `events(filters)`, `to_csv`, `counts` |
| `name_source.py` | The generic offline-name-source engine (DwC Archive → SQLite index → search → import chain). WCVP is one instance; user datasets are others. See "Offline name sources" below |
| `datasets.py` | User-added name datasets (**experimental**): install from a chosen file → `data/name_sources/<slug>/`, rebuild, remove, `import_all` |
| `batch_ops.py` | Batch tools (#78): `fetch_by_taxon` / `match_catalog_numbers` (both **scoped to a working `repository_id`**) + `apply_disposition` / `apply_repository`. **Cross-collection safety is structural** — `_load_in_scope` re-asserts every specimen belongs to the working collection before any write, so a bulk op can never touch a specimen held in another collection. `catalog_number` is never changed. |

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

- **Tier 1 — auto-filled, editable.** Pre-filled with a sensible default the user can change
  before saving (`basisOfRecord` = `"PreservedSpecimen"`; `lifeStage` = `"adult"`). The
  constant defaults live in one place, `app/vocab.py::NEW_SPECIMEN_DEFAULTS`. **`preparations`
  is a *data-driven* Tier-1 default:** the `preparation` row flagged `is_default` (★ in the
  Preparations vocab card; migration 0052, `Vocabulary.get_default`) pre-fills new specimens
  (Digitize standard/visiting, Import), or empty if none is flagged — Mounting still forces
  `"pinned"`. **`disposition` has NO create default** — it starts empty and is set manually or
  in bulk (Batch tools); the former hardcoded `"in collection"` was dropped.
- **Tier 2 — one-click default.** Field starts empty; a `push_pin` button adjacent to it
  inserts the configured default (`identifiedBy`, `recordedBy`, `dateIdentified`). Never
  applied silently — the user must click.
- **Tier 3 — background invisible default.** Applied silently to every saved record, never
  shown as an editable field. The **default collection** — the `repository` row flagged
  `is_default` (migration 0050, #83; chosen once in Settings) — supplies the specimen's
  `repository_id` at save time (`repositories.get_default`, **not** a config string); if none
  is flagged the save is refused. `collectionCode` / `institutionCode` are no longer stored on
  the specimen (migration 0047, #75) — they derive from the repository.

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
catalog-number identifier as `"[namespace.short_name] [catalogNumber]"`, e.g. `"Doe ab12"`.
The four-character code is the `catalogNumber` as-is; the namespace label comes from TW.

**DB mapping (revised — migration 0047, #75):**
- `dwc:catalogNumber` (Python: `catalog_number`) — the 4-char/sequential code; immutable once
  assigned, never mutated.
- **`collection_object.repository_id`** (FK → `repository`, NOT NULL, ON DELETE RESTRICT) is
  the **single source of truth** for collection membership. `collectionCode` /
  `institutionCode` / `ownerInstitutionCode` are **resolved from the repository at DwC export
  time** (the exact DwC term TW reads is settled when the export tool is built; deferred) —
  there is **no `dwc:collectionCode` or `dwc:institutionCode` column on `collection_object`
  any more** (both dropped in 0047). The catalog-number uniqueness scope is
  `UNIQUE(repository_id, catalogNumber)`.
- **Re-homing** a specimen to another collection (gift/exchange) re-points `repository_id`
  (`update_collection_object`, never blanks it — NOT NULL); the in-app equivalent of "editing
  ownerInstitutionCode". The catalog number keeps its original code **prefix**, so after a
  re-home the prefix may no longer match the owning repository — that's expected: the prefix
  is frozen in the immutable identifier, membership is the FK. The identifier *label* still
  resolves its collection name by the code **prefix** (frozen at print time; re-homing never
  reprints the pinned label), so `labels.py` / `name_map` are unaffected.
- **The own/home collection is a flag on the vocab, not a config string (migration 0050,
  #83).** Exactly one `repository` row carries `is_default = 1` (partial-unique index
  `uq_repository_one_default`; service `repositories.get_default` / `set_default`). Standard
  digitize / Mounting / Import read that row and take **both** the catalog-number prefix
  (`collection_code`) and the new specimen's `repository_id` from it — and **refuse to save
  if none is set** ("No default collection set — open Settings"), never stubbing a placeholder.
  The Settings "Default collection" picker flags an existing repository; `config.json` stores
  **no** collection code (same DB-integrity rule as person defaults — a configurable default
  that references a DB entity belongs in the DB, never a flat string).
- **Save-time resolution:** `repositories.resolve_id(session, collection_code=…)` get-or-creates
  the repository by a **freely-typed** code inside the save transaction (mirrors person / vocab
  `commit`). It is used **only** for "Digitize other collection" (visiting) and the Records
  re-home field — the own-collection paths use `get_default` instead, so there is no string to
  silently stub from.

For a single-collection setup, add one repository in Controlled Vocabularies and flag it as
the default in Settings; give it a real `collection_full_name`. Its `collection_code` /
`institution_code` (e.g. a fictional `"Doe"`) then drive catalog numbers and the identifier
label. Configure the TW import dataset to map `(institutionCode, collectionCode) → TW
Namespace` before import.

> **Rule (revised):** never re-introduce `dwc:collectionCode` / `dwc:institutionCode` as
> columns on `collection_object`. Membership is the `repository_id` FK; codes are resolved
> through the repository.

---

## 9. Open questions

- Exact JSON shape, filter parameters, and pagination of `/api/v1/dwc_occurrences`.
- Whether TW's internal CRUD API exposes usable `PATCH`/`DELETE` for collection objects.
- The regeneration/lag behaviour of the `dwc_occurrences` projection after an import.
- Source and licence of the chosen Europe-wide habitat layer (EUNIS vs CORINE), and its CRS.

# Data model

> Moved verbatim out of `CLAUDE.md` on 2026-10-07 to keep the always-loaded file compact.
> This file is now the **one home** for the detail below; `CLAUDE.md` keeps only the binding
> rule and a link here. `§n` references are `CLAUDE.md` section numbers: §2 principles and §8
> conventions are in `CLAUDE.md`; §4 → `data_model.md`, `vocabularies_geography.md`,
> `taxonomy.md`; §5 / §5b / §5c → `taxonworks.md`; §6 → `app_structure.md`.

## 4. Data model (implemented)

22 migrations applied. Schema is in production use.

### DwC column naming convention

Darwin Core fields are stored on SQLite columns named **`dwc:columnName`** (colon separator,
camelCase after the colon). SQLAlchemy `mapped_column` maps these to snake_case Python
attributes. Mermaid diagrams use plain camelCase. Do not deviate from this pattern.

### Core tables

| Table | Purpose |
|-------|---------|
| `collection_object` | One physical specimen or lot. `catalog_number` is the stable, immutable sync join key — NOT NULL in effect for the own collection (triggers), nullable for a specimen held in a foreign one (migration 0072; see "Specimen workflows"). `repository_id` FK → `repository` (NOT NULL, ON DELETE RESTRICT — migration 0047, #75) is the **single source of truth for collection membership**; the old denormalised `dwc:collectionCode` + `dwc:institutionCode` text columns were dropped (codes resolve through the repository at export; `UNIQUE(repository_id, catalogNumber)`). `dwc:basisOfRecord`, `dwc:sex`, `dwc:typeStatus`, etc. `preparation_id` FK → `preparation` and `disposition_id` FK → `disposition` (controlled vocabs, not free text — migrations 0039/0048; see "Controlled vocabularies"). `dwc:otherCatalogNumbers` (free text) records prior catalog numbers from previous owning institutions (migration 0049, #77; previous institutions themselves are not recorded). |
| `collecting_event` | Where/when collected; shared by many specimens. Full DwC locality + coordinate block. `dwc:eventDate` supports ISO 8601 intervals (`2024-06-15/2024-06-20`). `dwc:recordedBy` FK → `person(full_name)`. `habitat_id` + `sampling_protocol_id` (migration 0040) and the geography hierarchy `country_id` / `state_province_id` / `administrative_region_id` / `county_id` / `island_id` (migration 0041) are all controlled-vocab FKs (see "Controlled vocabularies"). `municipality` + `locality` stay free text. **No `dwc:countryCode` column** — dropped in 0057; it is derived from `country.iso_code` (a stored copy drifted: `Germany` could carry `FR`). |
| `taxon` | Local OTU analogue. DwC parent-link model (GBIF best practices). Columns: `name_element` (atomic source of truth — this rank's own epithet/uninomial, e.g. `crypticus`; migration 0032, Epic #30), `dwc:scientificName` (the *composed* full name without authorship, e.g. `Otiorhynchus crypticus`, maintained from `name_element` + the parent chain), `dwc:taxonRank`, `dwc:scientificNameAuthorship`, `dwc:parentNameUsageID` (self-FK, encodes hierarchy), `dwc:acceptedNameUsageID` (self-FK, marks synonyms — its presence *is* synonym status; `taxonomicStatus` is derived at export, not stored, see below), `taxonworksOtuID`, `ipniID` (the IPNI id of the name a row was imported from — identity like `taxonworksOtuID`, not a `dwc:` term; migration 0053). `dwc:nomenclaturalCode` is **NOT NULL + CHECK**-constrained to the closed list (migration 0054, #96): it is a property of the source or inherited from the parent, never guessed. No denormalised rank columns. |
| `taxon_determination` | `collection_object` → `taxon` link. `is_current` flag. `taxon_id` may reference a synonym row (deliberate design). `dwc:identifiedBy` FK → `person(full_name)`. **`dwc:dateIdentified` may NOT be an interval** (`ck_td_date_identified_no_interval`, migration 0069): an identification is made on one date, and TW's importer raises *"Date range for taxon determination is not supported."* (`occurrence.rb:1395-1397` @ `897f385`). Deliberately the opposite of `collecting_event."dwc:eventDate"`, which does allow one. |
| `biological_relationship` | Kind of association (`collected_on`, `feeds_on`, …). |
| `biological_association` | Exclusive-arc pattern: (`subject_collection_object_id` XOR `subject_taxon_id`) and (`object_collection_object_id` XOR `object_taxon_id`). CHECK enforces exactly-one-non-null per role. |
| `label_code` | 4-char alphanumeric specimen identifiers (`[0-9a-z]{4}`, ~1.7 M possibilities). Tied to a `label_batch`. Once used on a specimen they are immutable. |
| `label_batch` | Groups of `label_code` rows with a `created_at` timestamp. Batches can be reprinted only if no code in the batch has been used yet. |
| `print_queue` | Staged label jobs (`label_type` ∈ {data, determination, identifier}) pending a single print run. Items removed after printing. (The `data` label carries locality/date/collector — there is no separate "locality" type.) A `determination` row may instead be a **plain identification label** with no specimen, its content on the row itself (migration 0071 — see "Print-queue policy by create mode"). |
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
| `person` | **Drop the record — never obscure it** (revised 2026-07-25, #149). A confidential person as the event's `recordedBy` withholds the specimen entirely, unconditionally: the flag is set on the rare person who must not be published, so it is obeyed, not weighed, and there is deliberately **no setting** that turns it into "export the record without the name". `identifiedBy` is *never* withheld or blanked — a determiner's name is a scientific attribution, not personal data the collection is asked to protect. **Superseded:** the former placeholder-substitution rule and its `config.confidential_person_label` are **gone** — a placeholder is a claim a downstream aggregator cannot tell from a real collector name, so a withheld name is written as *no value at all*. |
| `collection_object` | **Drop.** The specimen is omitted from the export entirely. |
| `collecting_event` | **Drop its specimens.** A confidential event withholds *all* its specimens from the export (you cannot keep the occurrence but blank the locality — that breaks the record). |

A person additionally carries `consent_approved` (below). The **undecided middle** — neither
`confidential` nor `consent_approved`, i.e. nobody has asked them yet — is the *only* privacy
choice that is configurable, via `AppConfig.tw_export_nonconsent`: `"name_removed"` (default —
export the record with that person's name removed) or `"consented_only"` (export only data
whose collector has consented).

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

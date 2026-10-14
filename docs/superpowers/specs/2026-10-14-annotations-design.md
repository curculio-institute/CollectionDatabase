# Annotations: TaxonWorks tags and data attributes

Status: design, awaiting review (2026-10-14). No code written.

## Goal

Flag specimens, collecting events and biological associations with **TaxonWorks keywords**
(tags, e.g. `Adventive`) and give them **TaxonWorks data attributes** (predicate + value),
show them on the record sheets, and export them to TaxonWorks with the occurrence file.

- The user decides, per keyword and per predicate, whether it may be used on **specimens**,
  **collecting events** and **biological associations**.
- Keywords and predicates are **fetched from TaxonWorks** on request and **cached** in the
  local DB; entering annotations works offline from the cache.
- Keywords are created by the user in TaxonWorks, never by this app.
- First use: the existing TW keyword `Adventive` (id 1764 on sfg.taxonworks.org) on
  specimens. Its TW definition currently targets asserted distributions; the user will
  widen it to cover displaced specimens.

## Verified TaxonWorks behaviour (`SpeciesFileGroup/taxonworks` @ `897f385`)

Re-grep line numbers before citing them in code.

| Fact | Where |
|------|-------|
| Columns `TW:Tag:<Class>:<selector>` and `TW:DataAttribute:<Class>:<selector>`, `<Class>` ∈ `CollectionObject`, `CollectingEvent` | `app/models/dataset_record/darwin_core.rb:22` (data attr), `:32` (tag) |
| Tag selector keeps the header's case (`get_fields_mapping.values`); data-attribute selector is **lower-cased** (`.keys`, downcased at `:82`) | `darwin_core.rb:22–40, 82` |
| Tag lookup: `Keyword.find_by(uri: selector)`, then name `ILIKE` (LIKE-escaped, no wildcards → case-insensitive equality), in the project. Not found → row fails | `occurrence.rb:1672` |
| Tag value: `true`/`1` applies; blank/`false`/`0` does not; anything else fails the row | `occurrence.rb:1672` |
| Data attribute lookup: `Predicate.find_by(uri:)`, then name `ILIKE`. Not found → row fails. Stored as `InternalAttribute` with the cell as value; blank cell → skipped | `occurrence.rb:1695` |
| Keywords and predicates are separate classes — a predicate never answers a tag column | `occurrence.rb:1672`, `:1695` |
| When a row **reuses an existing collecting event**, only that row's *new* event **tags** are added; its event **data attributes are dropped** | `occurrence.rb:469` |
| Biological associations cannot be imported via DwC at all | CLAUDE.md §5 |
| Keyword/predicate listing: `GET /api/v1/controlled_vocabulary_terms?type=Keyword` (or `Predicate`), paged, project-scoped | `config/routes/api_v1.rb:142`; `controlled_vocabulary_terms_controller.rb#api_index`; filter param `type` in `lib/queries/controlled_vocabulary_term/filter.rb` |

Verified live 2026-10-14: the listing works with the project token; sfg project has 38
keywords, none specimen-oriented apart from the reused `Adventive`.

## Data model (migration 0073)

### `tw_term` — the cache

One row per TaxonWorks keyword or predicate ever fetched.

| Column | Rule |
|--------|------|
| `id` | local PK |
| `tw_host` | TEXT NOT NULL — the TW host the row was fetched from (same notion of instance as the OTU provenance stamp, `tw_sync.otu_instance_state`) |
| `tw_id` | INTEGER NOT NULL — the TaxonWorks id; **the identity, within `tw_host`** |
| `kind` | TEXT NOT NULL CHECK IN (`keyword`, `predicate`) |
| `name` | TEXT NOT NULL — as last fetched; CHECK no tab / CR / LF (it becomes a TSV header) |
| `uri`, `definition` | TEXT, as last fetched |
| `previous_name` | TEXT — set when a fetch saw a rename; cleared when the user acknowledges |
| `missing_on_tw` | INTEGER NOT NULL DEFAULT 0, CHECK IN (0,1) |
| `allow_specimen`, `allow_event`, `allow_association` | INTEGER NOT NULL DEFAULT 0, CHECK IN (0,1) |
| | UNIQUE(`tw_host`, `tw_id`, `kind`); UNIQUE(`id`, `kind`) (target of the composite FK below) |

Ids are per instance, so the instance is part of every row rather than one stamp: a term
fetched from the sandbox can never be re-matched to a production term that happens to share
its id. (Host only, like the OTU stamp — the project token is a secret and is not stored in
the DB; two projects on one host are not distinguished, as today.)

### `annotation`

| Column | Rule |
|--------|------|
| `id` | PK |
| `collection_object_id` / `collecting_event_id` / `biological_association_id` | FK, ON DELETE CASCADE; CHECK exactly one non-NULL (exclusive arc) |
| `kind` | TEXT NOT NULL CHECK IN (`tag`, `data_attribute`) |
| `tw_term_id`, `term_kind` | composite FK → `tw_term(id, kind)` ON DELETE RESTRICT; CHECK (`kind`='tag' AND `term_kind`='keyword') OR (`kind`='data_attribute' AND `term_kind`='predicate') |
| `value` | TEXT; CHECK (`kind`='tag' AND `value` IS NULL) OR (`kind`='data_attribute' AND `value` IS NOT NULL AND trim(`value`) <> '' AND no tab/CR/LF) |
| | one annotation per (record, term): three partial UNIQUE indexes, one per target column |

STRICT tables. Migration declares every constraint in DDL (models cannot express STRICT).
Update `docs/schema.html` and `docs/data_model.md`; extend `tests/test_schema_integrity.py`.

"Allowed" switches filter **what the picker offers** only. Turning one off later never
removes or hides existing annotations, and does not affect export.

## Service layer — `app/services/annotations.py`

- `fetch_terms(session)` (async): the **only** network call. Fetches all keywords and all
  predicates (paged; an oversized/short page is a lookup failure, never "the full list"),
  then reconciles **by `tw_id`**:
  - new id → insert (all `allow_*` off);
  - same id, new name → update `name`, set `previous_name`;
  - id absent → `missing_on_tw = 1` (row kept; annotations untouched);
  - id present again → `missing_on_tw = 0`.
  Reconciliation only ever touches rows of the **configured** host. Rows of any other host
  are left as they are and are never offered or exported; fetching from a new host inserts
  that host's terms fresh. Annotations on another host's terms block export until the user
  moves them by an explicit merge — a name is never used to map one instance's terms onto
  another's.
- `allowed_terms(session, target)` — terms offered for `specimen` / `event` / `association`:
  configured host only, excluding `missing_on_tw`.
- `set_annotations(session, target, target_id, items)` — replace the record's annotation set
  inside the caller's transaction (the record's one atomic Save).
- `annotations_for(session, target, target_id)`.

## UI

- **Settings → TaxonWorks → "Keywords & predicates"** card: Fetch button; table of name,
  kind, definition, status (`ok` / `renamed from …` / `missing on TaxonWorks`) and the three
  allow switches.
- **Footer button** (`sell` icon, count badge) on the specimen card, the collecting-event card
  and each association row — same component and the same staged (Digitize) / deferred
  (Records) modes as `external_id_panel.py`. Popup: allowed keywords as checkboxes;
  predicates as pick + value. A term no longer allowed or missing on TW still shows if
  already attached (marked), but cannot be newly added.
- **Record sheets** (specimen, event): one "Annotations" line — tags as chips, data
  attributes as `predicate: value`. Hidden when empty.
- Digitize standard, visiting and Mounting; Records. Not Import & Assign / Bulk import.

## Export

`dwc_export.occurrence_row` gains, only for terms the batch uses:

- specimen: `TW:Tag:CollectionObject:<name>` = `true` / empty;
  `TW:DataAttribute:CollectionObject:<name>` = value / empty;
- its collecting event: `TW:Tag:CollectingEvent:<name>`, `TW:DataAttribute:CollectingEvent:<name>`
  — repeated on each row of that event. Safe today because rows never make TW reuse an
  existing event (no `eventID`); if that ever changes, event data attributes would be dropped
  silently by TW (`occurrence.rb:469`) and this must be revisited.
- Biological-association annotations are **never exported** (not importable); the UI says so.

### Term pre-flight (beside the name pre-flight; nothing exports until both pass)

Fetch the current keyword and predicate lists, then for every term the batch would emit:

1. its `tw_id` still exists, of the same kind;
2. the header selector we would write is its **current** name;
3. that selector resolves to **exactly this id** under TW's own rule: no other term of that
   kind has `uri` equal to the selector (for predicates compare against the **lower-cased**
   selector, as TW does), and no other term of that kind has a name equal to it ignoring case;
4. the term's `tw_host` is the configured host.

Any failure blocks Download and lists the term with its reason ("renamed on TaxonWorks —
fetch again", "deleted on TaxonWorks", "name also used by keyword …", "cache is from another
instance"). A refused export is the safe side: TW's importer is CREATE-ONLY.

Docs: `CLAUDE.md` §5c line "No `TW:` columns yet" → "only `TW:Tag:` / `TW:DataAttribute:`
columns for CollectionObject and CollectingEvent"; full reasoning in `docs/taxonworks.md` §5c.

## Testing

- Schema integrity: STRICT, every CHECK, exclusive arc, composite FK kind agreement, partial
  UNIQUEs, CASCADE / RESTRICT.
- `fetch_terms` reconciliation with a faked API: insert, rename, delete, return, a second
  host with colliding ids left untouched, short/oversized page.
- Export: columns only for used terms, `true`/value cells, event annotations on every row,
  association annotations absent.
- Pre-flight: ok; renamed; deleted; name collision (another term's name ignoring case);
  URI collision (including the lower-cased predicate case); instance mismatch.
- UI driven with Playwright on a spare port against a DB copy — **ask the user first**.

## Out of scope (follow-ups)

- Annotations as Explore facets; in Bulk import and Import & Assign.
- Compare: report annotations added after a specimen was uploaded (they can never reach TW —
  CREATE-ONLY); would use `GET /api/v1/tags` and data-attribute routes.
- Possible existing bug, separate issue: the export emits `fieldNumber` without
  `TW:Namespace:fieldNumber`; `occurrence.rb:448` then looks up a namespace with a NULL name
  and fails the row ("Namespace not found"). Not verified live.

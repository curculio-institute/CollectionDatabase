# Annotations: tags, data attributes and TaxonWorks-linked vocabularies

Status: design, awaiting review (revised 2026-10-16; supersedes the 2026-10-14 draft, which
made TaxonWorks the master of the terms). No code written.

## Goal

Flag specimens, collecting events and biological associations with **tags** and give them
**data attributes** (term + value), show them on the record sheets, export them to
TaxonWorks with the occurrence file, and report divergence from TaxonWorks in Compare.

- Tags and data-attribute terms are a **local controlled vocabulary**. A term may carry an
  **optional link** to a TaxonWorks keyword (tag) or predicate (data attribute); a term
  without a link is **local only** and is never exported.
- The user decides per term whether it may be used on **specimens**, **collecting events**
  and **biological associations**.
- **Biological relationships** (the vocabulary an association uses, e.g. `feeds on`) get
  the same optional-link mechanism and are managed in the same place.
- Terms are never created on TaxonWorks by this app.
- First use: the TW keyword `Adventive` (id 1764 on sfg.taxonworks.org) on specimens. Its
  TW definition currently targets asserted distributions; the user will widen it to cover
  displaced specimens.

Terminology: a **biological relationship** is the vocabulary term; a **biological
association** is the record (subject, relationship, object). Annotations attach to the
association record, never to the relationship term.

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
| Keyword/predicate listing with `id`, `name`, `uri`, `definition`: `GET /api/v1/controlled_vocabulary_terms?type=Keyword` (or `Predicate`), paged, project-scoped | `config/routes/api_v1.rb:142`; `controlled_vocabulary_terms_controller.rb#api_index`; `lib/queries/controlled_vocabulary_term/filter.rb` |
| Relationship listing with `id`, `name`, `inverted_name`: `GET /api/v1/biological_relationships` (no show endpoint) | `app/services/taxonworks.py::fetch_biological_relationships` |
| Tags readable via `GET /api/v1/tags`, filters `keyword_id`, `tag_object_type`, `tag_object_id` (scalars or arrays) | `config/routes/api_v1.rb:179`; `lib/queries/tag/filter.rb` (`PARAMS`, `*_facet`) |
| Data attributes readable via `GET /api/v1/data_attributes`, filters `controlled_vocabulary_term_id`, `attribute_subject_type`, `attribute_subject_id` | `config/routes/api_v1.rb:151`; `lib/queries/data_attribute/filter.rb` |

Verified live 2026-10-14: the keyword listing works with the project token; the sfg project
has 38 keywords, none specimen-oriented apart from the reused `Adventive`.

## Data model (migration 0073)

STRICT tables; the migration declares every constraint in DDL (models cannot express
STRICT). Update `docs/schema.html` and `docs/data_model.md`; extend
`tests/test_schema_integrity.py`.

### The TaxonWorks link (shared by both vocabularies)

| Column | Rule |
|--------|------|
| `tw_id` | INTEGER — the TaxonWorks id; NULL = **local only** |
| `tw_name` | TEXT — the TW name as last fetched; NOT NULL iff `tw_id` is NOT NULL; CHECK no tab / CR / LF (it becomes a TSV header) |
| `tw_host` | TEXT — the host the link was **confirmed** on; NULL with a `tw_id` = **not verified**; CHECK `tw_host` IS NULL OR `tw_id` IS NOT NULL |

**One link per term, at most.** A term mirrors one term of the one TaxonWorks instance in
use; a per-host link set is not part of the design.

**The link is hard to earn.** `tw_host` is written only when a fetch from that host
returned this id with this name (the same discipline as the OTU provenance stamp,
`docs/taxonworks.md`). Nothing ever writes a host it did not observe.

### `annotation_term`

| Column | Rule |
|--------|------|
| `id` | PK |
| `kind` | TEXT NOT NULL CHECK IN (`tag`, `data_attribute`); cannot change while any annotation uses the term (service refuses; composite FK below makes a mismatch impossible) |
| `name` | TEXT NOT NULL, the local name, trimmed non-empty; UNIQUE(`kind`, `name`) |
| `allow_specimen`, `allow_event`, `allow_association` | INTEGER NOT NULL DEFAULT 0 CHECK IN (0,1) |
| `tw_id`, `tw_name`, `tw_host` | the link, as above |
| | UNIQUE(`kind`, `tw_id`) — two local terms never link to the same TW term (export would emit the same column twice); UNIQUE(`id`, `kind`) as the composite-FK target |

The "allow" switches filter **what the picker offers** only. Turning one off later never
removes or hides existing annotations and does not affect export.

### `biological_relationship` (existing table)

Gains `tw_name` and `tw_host` beside the existing `taxonworksID` (which is the link's
`tw_id`). Native `ADD COLUMN`; no rebuild. UNIQUE on `taxonworksID` among non-NULL values
(partial unique index) for the same reason as above.

**Existing rows:** the migration copies the current `name` into `tw_name` for rows that
have a `taxonworksID` and leaves `tw_host` **NULL — not verified.** The host those ids were
fetched from is unknown: the old launch sync recorded neither host nor time, and both sfg
and sandbox have been configured in the past. Today's host is not evidence.

### `annotation`

| Column | Rule |
|--------|------|
| `id` | PK |
| `collection_object_id` / `collecting_event_id` / `biological_association_id` | FK, ON DELETE CASCADE; CHECK exactly one non-NULL (exclusive arc) |
| `term_id`, `term_kind` | composite FK → `annotation_term(id, kind)` ON DELETE RESTRICT |
| `value` | TEXT; CHECK (`term_kind`='tag' AND `value` IS NULL) OR (`term_kind`='data_attribute' AND `value` IS NOT NULL AND trim(`value`) <> '' AND no tab/CR/LF) |
| | one annotation per (record, term): three partial UNIQUE indexes, one per target column |

## Service layer

### `app/services/annotations.py`

- `annotation_term_vocab` — CRUD + merge through the existing `Vocabulary` machinery where
  it fits (delete refused while referenced, merge re-points FKs); the extra columns make it
  a custom card like `person` / `repository`, not a generic single-name entry.
- `allowed_terms(session, target)` — terms offered for `specimen` / `event` / `association`.
- `set_annotations(session, target, target_id, items)` — replace the record's annotation
  set inside the caller's transaction (the record's one atomic Save).
- `annotations_for(session, target, target_id)`.

### `app/services/tw_links.py` — one link/verify mechanism for all three vocabularies

- `fetch_candidates(kind)` (async) — the TW list for `keyword`, `predicate` or
  `relationship`. Paged; an oversized or short page is a **lookup failure**, never "the full
  list". Errors go through `taxonworks.explain_tw_error` and say which failure it is.
- `link(session, row, tw_term, host)` — store `tw_id`, `tw_name`, `tw_host` from a term
  picked out of a fresh fetch (so it is verified at once). Refused if another local term of
  that kind already holds the id.
- `unlink(session, row)` — clear all three; the term becomes local only.
- `verify(session, kind)` (async) — **only on user request.** One fetch from the configured
  host, then per linked row:
  - same id, same name → confirmed: `tw_host` = configured host;
  - same id, different name → `tw_host` cleared (no longer confirmed), state `name
    differs` (both names shown); the user chooses **use TW name** (local `name` and
    `tw_name` := TW name, confirmed), **keep local name** (`tw_name` := TW name, confirmed),
    or **unlink**;
  - id unknown on this host → `tw_host` cleared, state `not found on this instance`; the
    link is never removed automatically.
- `link_state(row)` → persisted: `local_only` / `verified` / `not_verified`. The latest
  verify adds `name_differs` / `not_found` as detail on a `not_verified` row for the
  session; after a reload it reads `not verified` until verified again. Every non-verified
  state blocks export, so nothing depends on the detail surviving.

### Removed: the launch sync

`biological.sync_biological_relationships` and its call in `main.py` are removed. It
silently renamed local relationship rows to the TW name on every launch (which, after a
host change, could re-label a relationship with a *different* TW relationship's name and so
change what existing associations say), and swallowed every error. Relationships are
fetched only through the vocabulary tab.

## UI

### Controlled Vocabularies tab — two new sub-tabs

**Tags & data attributes** and **Biological relationships** (relationships have no
management UI today).

- **Table:** name; type (tags/data attributes only); the three allow switches (tags/data
  attributes only); a **link chip** — `local only` (grey), `TaxonWorks ✓`, `not verified`,
  `name differs: "…"`, `not found on this instance`; edit / merge / delete actions.
- **Link dialog** (from the chip or the edit dialog): fetches the TW list of the matching
  kind, searchable; a TW term already linked to another local term is shown disabled with
  that term's name; pick → linked and verified. **Unlink** makes the term local only.
- **Add:** a local term by name, or **from TaxonWorks** — creates the term with the TW name,
  already linked.
- **Verify** button at the top of each sub-tab: one fetch, updates every chip; rows with a
  differing name offer *use TW name* / *keep local name* / *unlink*. Nothing is decided
  silently.

### Entering annotations

- Footer button (`sell` icon, count badge) on the specimen card, the collecting-event card
  and each association row — same component and the same staged (Digitize) / deferred
  (Records) modes as `external_id_panel.py`. Popup: allowed tags as checkboxes; data
  attributes as pick + value. Local-only terms carry their badge. A term no longer allowed
  still shows if already attached (marked) but cannot be newly added.
- Record sheets (specimen, event): one "Annotations" line — tags as chips, data attributes
  as `name: value`. Hidden when empty.
- Digitize standard, visiting and Mounting; Records. Not Import & Assign / Bulk import.

## Export

`dwc_export.occurrence_row` gains, only for terms the batch uses:

- specimen: `TW:Tag:CollectionObject:<tw_name>` = `true` / empty;
  `TW:DataAttribute:CollectionObject:<tw_name>` = value / empty;
- its collecting event: `TW:Tag:CollectingEvent:<tw_name>`,
  `TW:DataAttribute:CollectingEvent:<tw_name>` — repeated on each row of that event. Safe
  today because rows never make TW reuse an existing event (no `eventID`); if that ever
  changes, TW drops event data attributes silently (`occurrence.rb:469`) and this must be
  revisited.
- The header always uses **`tw_name`**, never the local name.
- Biological-association annotations are **never exported** (not importable); the UI says
  so. Relationships have nothing of their own to export.

| Term state | Export |
|---|---|
| local only | not emitted; the export summary lists it as information ("3 specimens carry local-only tags: …") |
| linked, verified | emitted if the pre-flight passes |
| not verified / name differs / not found | **blocks** export until resolved in the vocabulary tab |

A linked term means "this should reach TaxonWorks"; a link that cannot be confirmed is a
blocker, never a silent omission.

### Term pre-flight (beside the name pre-flight; nothing exports until both pass)

One fresh fetch, then for every term the batch would emit:

1. its `tw_id` exists on the **configured** host, of the same kind, and `tw_host` is that
   host;
2. TW's current name equals the stored `tw_name` (else "renamed on TaxonWorks — verify
   again");
3. that name resolves to **exactly this id** under TW's own rule: no other term of that
   kind has `uri` equal to the selector (for predicates compare against the **lower-cased**
   selector, as TW does), and no other term of that kind has a name equal to it ignoring
   case;
4. the name contains no tab / CR / LF.

Any failure blocks Download and lists the term with its reason. A refused export is the
safe side: TW's importer is CREATE-ONLY.

Docs: `CLAUDE.md` §5c line "No `TW:` columns yet" → "only `TW:Tag:` / `TW:DataAttribute:`
columns for CollectionObject and CollectingEvent, linked terms only"; full reasoning in
`docs/taxonworks.md` §5c.

## Compare

Annotation divergence cannot be repaired by exporting again (CREATE-ONLY), so Compare is
the only place it surfaces. `tw_compare.compare_repository` gains an annotations pass.

**Specimens.** For every specimen on TaxonWorks (its TW CollectionObject id is
`TwCatalogEntry.tw_object_id`), fetch tags and data attributes **by object** —
`/tags?tag_object_type=CollectionObject&tag_object_id[]=…` and
`/data_attributes?attribute_subject_type=CollectionObject&attribute_subject_id[]=…` — in
batches, with the module's bounded concurrency. By object rather than by term, so TW
annotations on terms that are not linked locally are seen too.

| Finding | Meaning |
|---|---|
| missing on TaxonWorks | local annotation on a linked term, absent on TW (e.g. added after upload) — must be added by hand in TW |
| only on TaxonWorks | TW has it on a linked term; local does not |
| value differs | data attribute on both sides with different values |
| unlinked term on TaxonWorks | TW tag / data attribute whose keyword / predicate is not linked to any local term; shown with the TW name so it can be linked or added |

- Local-only terms are never compared.
- Joins are on `tw_id` (keyword / predicate id), never on names.
- **Every response is re-verified:** each returned row must carry the requested object type
  and one of the requested ids; otherwise it is a lookup failure, never a set of matches
  (§5c "unknown query params are silently ignored"; mirrors `_verify_filter_applied`).
- Biological-association annotations: `annotations_associations_not_compared = True` on
  `CompareResult`, stated in the report — the same pattern as `media_not_compared`.
- Results show as an "Annotations" group on the Compare page; each row links to the local
  record and the TW object, like `diverged` rows.

**Collecting events — open, verify before planning.** Neither the catalog index nor
`dwc_occurrences` yields the TW collecting-event id of a specimen. Comparing event
annotations needs a batchable source for it (probably the CollectionObject's
`collecting_event_id` via `GET /api/v1/collection_objects` with an id-array filter). Verify
the route and filter against the source at `897f385` before writing the plan. If no
batchable route exists, event annotations are declared not compared
(`annotations_events_not_compared = True`), never guessed.

## Testing

- Schema integrity: STRICT, every CHECK (link column coupling, value by kind, no tab/CR/LF),
  exclusive arc, composite FK kind agreement, UNIQUE(`kind`, `tw_id`), partial UNIQUEs,
  CASCADE / RESTRICT; migration leaves existing relationship links unverified.
- `tw_links`: link refuses a TW id held by another term; verify with a faked API — confirm,
  name differs (all three resolutions), not found (host cleared, link kept), short /
  oversized page as failure, error message names the cause.
- Removed launch sync: no relationship row changes on startup.
- Export: columns only for used linked terms, header = `tw_name`, `true`/value cells, event
  annotations on every row, local-only absent and listed, association annotations absent,
  unverified link blocks.
- Pre-flight: ok; renamed; deleted; wrong host; name collision (another term's name ignoring
  case); URI collision (incl. the lower-cased predicate case); tab in name.
- Compare: each finding category; response re-verification rejects an unfiltered reply;
  associations (and events, if unresolved) reported as not compared.
- UI driven with Playwright on a spare port against a DB copy — **ask the user first**.

## Out of scope (follow-ups)

- Annotations as Explore facets; in Bulk import and Import & Assign.
- Pushing biological associations (and their relationship links) to TW via the API.
- Possible existing bug, separate issue: the export emits `fieldNumber` without
  `TW:Namespace:fieldNumber`; `occurrence.rb:448` then looks up a namespace with a NULL name
  and fails the row ("Namespace not found"). Not verified live.

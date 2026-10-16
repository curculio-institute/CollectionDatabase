# Annotations: tags, data attributes and TaxonWorks-linked vocabularies

Status: approved 2026-10-16, implemented on `feature/annotations` (revised 2026-10-16;
supersedes the 2026-10-14 draft, which made TaxonWorks the master of the terms).

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
| Every controlled-vocabulary term has a `css_color` (string, nullable); the API listing returns it (`json.extract! … :css_color`). **No format validation** — any string, not necessarily a hex colour | `db/schema.rb:640`; `app/views/controlled_vocabulary_terms/api/v1/_attributes.json.jbuilder:1` (rendered by `api/v1/index.json.jbuilder` from `api_index`); `app/models/controlled_vocabulary_term.rb` (validations `:40–50`, none on `css_color`) |
| Biological relationships have **no** colour column | `db/schema.rb`, `create_table "biological_relationships"` |
| Relationship listing with `id`, `name`, `inverted_name`: `GET /api/v1/biological_relationships` (no show endpoint) | `app/services/taxonworks.py::fetch_biological_relationships` |
| Tags readable via `GET /api/v1/tags`, filters `keyword_id`, `tag_object_type`, `tag_object_id` (scalars or arrays) | `config/routes/api_v1.rb:179`; `lib/queries/tag/filter.rb` (`PARAMS`, `*_facet`) |
| Data attributes readable via `GET /api/v1/data_attributes`, filters `controlled_vocabulary_term_id`, `attribute_subject_type`, `attribute_subject_id` | `config/routes/api_v1.rb:151`; `lib/queries/data_attribute/filter.rb` |

Verified live 2026-10-14: the keyword listing works with the project token; the sfg project
has 38 keywords, none specimen-oriented apart from the reused `Adventive`.

## Data model (migration 0073)

STRICT tables; the migration declares every constraint in DDL (models cannot express
STRICT). Update `docs/schema.html` and `docs/data_model.md`; extend
`tests/test_schema_integrity.py`.

### TaxonWorks link columns (on both `annotation_term` and `biological_relationship`)

**Not a table.** "The link" is three columns that both vocabulary tables carry, defined
once here because both follow the same rules. `annotation_term` declares all three;
`biological_relationship` adds `tw_name` and `tw_host` and reuses its existing
`taxonworksID` as `tw_id`. Columns rather than a separate link table because a term has
**at most one** link (below): with one link per row, plain columns suffice and let the
CHECK and UNIQUE rules be enforced on the term row itself.

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

**"Host" means the network location of the configured TaxonWorks base URL** —
`urlsplit(cfg.tw_base).netloc`, e.g. `sfg.taxonworks.org` for
`https://sfg.taxonworks.org/api/v1` — the same value the OTU check compares
(`tw_sync.py`). It is not the full URL: the path and scheme are irrelevant to which
instance issued an id. A link is confirmed only while its `tw_host` equals the current
configured host; after switching instances every link reads as unconfirmed until re-checked.

### `annotation_term`

| Column | Rule |
|--------|------|
| `id` | PK |
| `kind` | TEXT NOT NULL CHECK IN (`tag`, `data_attribute`); cannot change while any annotation uses the term (service refuses; composite FK below makes a mismatch impossible) |
| `name` | TEXT NOT NULL, the local name, trimmed non-empty; UNIQUE(`kind`, `name`) |
| `allow_specimen`, `allow_event`, `allow_association` | INTEGER NOT NULL DEFAULT 0 CHECK IN (0,1) |
| `tw_id`, `tw_name`, `tw_host` | the link, as above |
| `css_color` | TEXT, nullable — the chip colour; CHECK NULL or `#rgb` / `#rrggbb` (hex digits, either case). Ownership: see "Term colour" below |
| | UNIQUE(`kind`, `tw_id`) — two local terms never link to the same TW term (export would emit the same column twice); UNIQUE(`id`, `kind`) as the composite-FK target |

The "allow" switches filter **what the picker offers** only. Turning one off later never
removes or hides existing annotations and does not affect export.

#### Term colour (`css_color`)

Display only — never exported, never compared as a finding. Named after TW's column.

- **Linked term → TaxonWorks owns the colour.** Linking copies the picked term's
  `css_color`; every Verify overwrites it with the current TW value (TW blank → NULL). It
  is **not editable locally** while linked — a local edit would be wiped by the next Verify.
  This is TW → local display data only; nothing is ever written to TW.
- **Local-only term → the user owns it.** Entered as text, `#rgb` or `#rrggbb`; no colour
  picker. Anything else is refused with a message naming the value. Empty = no colour.
- **Unlink keeps the last colour** as a local one.
- **A TW value that is not `#rgb` / `#rrggbb` is never converted** (TW does not validate the
  column, so it may hold `red` or garbage): the stored colour becomes NULL and the link /
  Verify result says so — "TaxonWorks colour 'red' not usable". The link itself is unaffected.
- No colour on `biological_relationship`: TW has none to mirror and nothing renders a
  relationship as a chip (follow-up if wanted).

### `biological_relationship` (existing table)

Gains `tw_name` and `tw_host` beside the existing `taxonworksID` (which is the link's
`tw_id`). Native `ADD COLUMN`; no rebuild. UNIQUE on `taxonworksID` among non-NULL values
(partial unique index) for the same reason as above; the migration refuses, naming the
rows, if two existing rows already share an id.

**The `tw_name` ↔ `tw_id` coupling is two triggers here, not a CHECK.** SQLite tests an
`ADD COLUMN … CHECK` against every existing row, and a linked row has no `tw_name` until
the backfill below runs — measured (SQLite 3.53): the coupling CHECK is refused. So
`trg_br_tw_link_ins` / `trg_br_tw_link_upd` `RAISE(ABORT)` instead (the 0072 precedent);
the single-column CHECKs (`tw_name` no tab/CR/LF, `tw_host` needs an id) are plain
column CHECKs, which pass on the all-NULL new column.

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
- **Merge rules (terms and relationships):** both sides must be the same kind; the
  **absorbed** entry must be local only (unlink it first) — a link is never silently
  discarded or transferred. A record carrying both terms keeps one annotation: a tag, or a
  data attribute with the same value, is folded; **different values refuse the merge**,
  naming the records.
- `allowed_terms(session, target)` — terms offered for `specimen` / `event` / `association`.
- `set_annotations(session, target, target_id, items)` — replace the record's annotation
  set inside the caller's transaction (the record's one atomic Save).
- `annotations_for(session, target, target_id)`.

### `app/services/tw_links.py` — one link/verify mechanism for all three vocabularies

- `fetch_candidates(kind)` (async) — the TW list for `keyword`, `predicate` or
  `relationship`. Paged; an oversized or short page is a **lookup failure**, never "the full
  list". Errors go through `taxonworks.explain_tw_error` and say which failure it is.
- `link(session, row, tw_term, host)` — store `tw_id`, `tw_name`, `tw_host` from a term
  picked out of a fresh fetch (so it is verified at once), and for annotation terms its
  `css_color` (rules under "Term colour"). Refused if another local term of that kind
  already holds the id.
- `unlink(session, row)` — clear all three; the term becomes local only and keeps its
  colour.
- `verify(session, kind)` (async) — **only on user request.** One fetch from the configured
  host, then per linked row:
  - same id, same name → confirmed: `tw_host` = configured host;
  - same id, different name → `tw_host` cleared (no longer confirmed), state `name
    differs` (both names shown); the user chooses **use TW name** (local `name` and
    `tw_name` := TW name, confirmed), **keep local name** (`tw_name` := TW name, confirmed),
    or **unlink**;
  - id unknown on this host → `tw_host` cleared, state `not found on this instance`; the
    link is never removed automatically.
  - for annotation terms whose id was found, `css_color` := the TW value (unusable value →
    NULL and reported), whatever the name outcome.
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
  `name differs: "…"`, `not found on this instance`; a colour swatch (tags/data attributes
  only); edit / merge / delete actions.
- **Edit dialog colour:** a plain text input (`#rgb` / `#rrggbb`) with a live swatch, no
  picker. Read-only with the note "from TaxonWorks" while the term is linked.
- **Link dialog** (from the chip or the row's Link action): fetches the TW list of the matching
  kind, searchable; a TW term already linked to another local term is shown disabled with
  that term's name; pick → linked and verified. **Unlink** makes the term local only.
- **Add:** a local term by name, or **from TaxonWorks** — creates the term with the TW name,
  already linked.
- **Verify** button at the top of each sub-tab: one fetch, updates every chip; rows with a
  differing name offer *use TW name* / *keep local name* / *unlink*. Nothing is decided
  silently.
- **Link safety:** the chip shows the TW name when it differs (`TaxonWorks ✓ as 'Endemic'`).
  A linked entry cannot be re-linked — unlink first (enforced in `tw_links.link`; a chip
  click on a linked row only explains this). Linking an unlinked entry whose name differs
  from the picked TW term asks *Keep local name* / *Use TaxonWorks name* (Cancel aborts).
- **Add from TaxonWorks** (terms) opens one dialog with a *Tags* tab (keywords) and a *Data
  attributes* tab (predicates), each with its own search and list, fetched lazily on first
  view; the new term's kind is the tab it is picked in, not the Type select (which only
  governs *Add local*). Linking an existing row shows one list of its own kind.
- **Confirmations and hints:** Unlink asks first. Delete of an unused entry asks first; an
  entry still in use shows the refusal with its usage count instead of asking (the service
  refuses it anyway). Every row action and the chip carry a real tooltip.

### Entering annotations

- Footer button (`sell` icon, count badge) on the specimen card, the collecting-event card
  and each association row — same component and the same staged (Digitize) / deferred
  (Records) modes as `external_id_panel.py`. Popup: allowed tags as checkboxes; data
  attributes as pick + value. Local-only terms carry their badge. A term no longer allowed
  still shows if already attached (marked) but cannot be newly added.
- **Staged (Digitize) annotations are added on Save, never a replacement set** — the same
  as staged event media, which also lands on a reused event. Adding a data attribute the
  record already has with another value refuses the Save, naming both values; the same
  value is a no-op. Deferred (Records) edits replace the record's set. Saved association
  rows in Records use the bound mode, like the media and identifier buttons beside them.
- **Save stop — event annotations on a reused event** (Digitize and Mounting): selecting an
  existing event makes its form read-only and hides the annotation button, so staged event
  annotations could no longer be seen or removed. When any are staged and the save targets
  an existing event, Save asks (same shape as the "Entered but not added" stop): *Add
  annotations to an existing event?* naming the count, the event id and how many specimens
  use it — *Save without them* (clears them, saves) / *Add them to event #id*; Cancel aborts
  the save. A new event asks nothing.
- **Values TaxonWorks would rewrite are refused** (`annotations.clean_value`, service only;
  the DB CHECK is unchanged): TW's importer runs `normalize_value!` on every cell — strips
  edge whitespace incl. NBSP and squeezes repeated spaces (taxonworks@897f385
  `app/models/dataset_record/darwin_core.rb:91-94`) — so such a value would be a permanent
  `value_differs` in Compare. A value with repeated spaces, a non-breaking space or any
  other non-ASCII whitespace is refused, naming it; nothing is normalised silently.
- Record sheets (specimen, event): one "Annotations" line — tags as chips, data attributes
  as `name: value`. Hidden when empty.
- **Colour:** a term with a `css_color` renders its chip (tag) or its name (data attribute)
  in that colour — in the record sheets, the footer popup and the vocabulary table; text on
  a coloured chip is black or white by the colour's luminance. No colour = the neutral chip.
  (Inspired by the Curculionoidea TaxonWorks contributor docs, which colour keyword chips
  from TW's `css_color`.)
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
the only place it surfaces. The pass is its own module, `tw_annotation_compare.py`, built
like `tw_media_compare.py` (fetch functions + a pure `compare_annotations` + a
`run_annotation_compare` entry point), reusing the same catalog `index` and
`tw_media_compare.specimens_on_tw` (which already excludes ambiguous catalog numbers).

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
- Only terms whose link is **verified on the configured host** are compared: a `tw_id`
  from another instance names something else. Linked-but-unverified terms are listed as
  not compared, never joined.
- Biological-association annotations are compared by the association compare (`docs/superpowers/specs/2026-10-17-association-compare-design.md`), on matched associations only.
- `/data_attributes`' `api_index` has **no `ORDER BY`** (`data_attributes_controller.rb:50`),
  so pages are not stable: rows are de-duplicated by id and a pull whose distinct ids fall
  short of `pagination-total` is a lookup failure. A TW `ImportAttribute` has no predicate
  id; it is reported as an unlinked term under its `import_predicate`.
- Results show as an "Annotations" group on the Compare page; each row links to the local
  record and the TW object, like `diverged` rows.

**Collecting events — verified 2026-10-16 @ `897f385`.**
`GET /api/v1/collection_objects?collection_object_id[]=…` (`config/routes/api_v1.rb:115` →
`collection_objects_controller.rb#api_index:378`) filters by id through the base filter's
`model_id_facet` (`lib/queries/query/filter.rb:679`, in `shared_and_clauses`; the class's
own `collection_object_id_facet` is commented out, `lib/queries/collection_object/
filter.rb:482`, because the base one covers it), and each row carries
`collecting_event_id` (`app/views/collection_objects/api/v1/_attributes.json.jbuilder:1`).
So event annotations are compared **per specimen**: the local specimen's event annotations
against its TW CollectionObject's event (each imported row created its own TW event, so a
local event shared by many specimens maps to many TW events). Every response is
re-verified (requested ids only). A TW specimen with no event, against local event
annotations, reports them as missing on TaxonWorks.

## Testing

- Schema integrity: STRICT, every CHECK (link column coupling, value by kind, no tab/CR/LF),
  exclusive arc, composite FK kind agreement, UNIQUE(`kind`, `tw_id`), partial UNIQUEs,
  CASCADE / RESTRICT; migration leaves existing relationship links unverified.
- `tw_links`: link refuses a TW id held by another term; verify with a faked API — confirm,
  name differs (all three resolutions), not found (host cleared, link kept), short /
  oversized page as failure, error message names the cause.
- Colour: CHECK accepts `#abc` / `#AABBCC`, rejects `red`, `#abcd`, `abc`; link copies the TW
  colour; verify overwrites it (also on a name difference); TW blank → NULL; TW `red` →
  NULL and reported; local edit refused while linked; unlink keeps the colour.
- Removed launch sync: no relationship row changes on startup.
- Export: columns only for used linked terms, header = `tw_name`, `true`/value cells, event
  annotations on every row, local-only absent and listed, association annotations absent,
  unverified link blocks.
- Pre-flight: ok; renamed; deleted; wrong host; name collision (another term's name ignoring
  case); URI collision (incl. the lower-cased predicate case); tab in name.
- Compare: each finding category; response re-verification rejects an unfiltered reply;
  associations reported as not compared; unverified terms listed, not joined; event
  annotations per specimen; duplicate ids across `/data_attributes` pages de-duplicated.
- UI driven with Playwright on a spare port against a DB copy — **ask the user first**.

## Explore facets (added 2026-10-16, user request)

Tags and data attributes are searchable in Explore's search bar, as facets like taxa,
places and people (`explore.search_facets` / `_apply_filters`).

- **Suggestions:** every annotation term whose name matches the typed tokens — a tag as
  "<name> · Tag", a data attribute as "<name> · Data attribute" (has the attribute, any
  value); a "local" badge on unlinked terms; the chip in the term's colour. Plus **value
  facets** "<name> = <value>" for distinct STORED data-attribute values that match the
  tokens (never free text).
- **Matching — anywhere, the same in every view:** a specimen matches when the annotation is
  on the specimen itself, on its collecting event, or on one of its biological associations
  (where the specimen is the subject). Like every existing facet, a chip filters specimens;
  the Taxa / Specimens / Events / Dashboard / CSV views only present that result (Events =
  the events of the matching specimens). No per-target or per-view meaning (user decision
  2026-10-16; per-target chips are a possible later addition).
- **NOT groups** negate NULL-safely ("NOT Adventive" = no Adventive on the specimen, its
  event or its associations). **NULL:** typing `NULL` adds "Annotations: none" — no
  annotation anywhere on the specimen, its event or its associations; its negation = has at
  least one.
- Facets are stored as plain dicts, so saved searches keep working.

## Out of scope (follow-ups)

- Annotations in Bulk import and Import & Assign.
- Per-target annotation facets ("Adventive · on event") — not needed for now (2026-10-16).
- Pushing biological associations (and their relationship links) to TW via the API.
- **Controlled value lists for data attributes** (code + written-out name, e.g. habitat type
  `G1.6` = `Fagus woodland`) — deferred 2026-10-16. Decided already: a **standard habitat
  code belongs in its own data attribute** (e.g. "EUNIS habitat"), not in the generic
  `collecting_event.habitat_id` vocabulary, which stays the descriptive field note
  (`dwc:habitat`). Sketch: `annotation_value(term_id, code, name)`, a `value_mode`
  (`free` / `controlled`) on `annotation_term`, `annotation.value_id` with a composite FK so
  a value can only come from its own term's list; export emits the code (TW stores a data
  attribute value as a plain string). **Cost of deferring:** the `annotation.value` CHECK
  must change, which in SQLite means rebuilding `annotation` — that migration must
  re-declare every constraint (CLAUDE.md migration discipline). Relevant to the planned
  GeoPandas habitat join (EUNIS vs CORINE still open).
- Possible existing bug, separate issue: the export emits `fieldNumber` without
  `TW:Namespace:fieldNumber`; `occurrence.rb:448` then looks up a namespace with a NULL name
  and fails the row ("Namespace not found"). Not verified live.

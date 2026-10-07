# TaxonWorks: integration constraints, gaps, export contract, namespace

> Moved verbatim out of `CLAUDE.md` on 2026-10-07 to keep the always-loaded file compact.
> This file is now the **one home** for the detail below; `CLAUDE.md` keeps only the binding
> rule and a link here. `§n` references are `CLAUDE.md` section numbers: §2 principles and §8
> conventions are in `CLAUDE.md`; §4 → `data_model.md`, `vocabularies_geography.md`,
> `taxonomy.md`; §5 / §5b / §5c → `taxonworks.md`; §6 → `app_structure.md`.

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


## 5c. Syncing with TaxonWorks — the export contract (#149, in progress)

The **TaxonWorks** tab (`app/ui/tw_sync_tab.py`) is built only when a connection is configured
(`AppConfig.taxonworks_enabled` — a base URL *and* a token; with no token every action would
fail at the first request, so the honest place to say "not set up" is the tab's absence).
Services: `app/services/dwc_export.py` (eligibility + projection + writer) and
`app/services/tw_sync.py` (name pre-flight + OTU reconciliation).

**Source-of-truth correction.** §5/§5b cite `occurrence.rb:NNN`. The file is
**`app/models/dataset_record/darwin_core/occurrence.rb`** (the per-*row* mapper, 1820 lines) —
*not* `app/models/import_dataset/darwin_core/occurrences.rb` (238 lines, the dataset-level
staging class). The **line numbers in §5/§5b are stale for the pinned commit `897f385`**
(§5b cites `associatedTaxa` at ~948; at 897f385 it is at **743**). Re-grep, don't trust them.

### Eligibility has two independent grounds: privacy and certainty (decided 2026-07-26)

`dwc_export.export_decision` is the single owner of "may this specimen leave the building",
read by both the comparison and the writer. It withholds on **three unrelated grounds**, and
`ExportDecision.privacy_reasons` / `.determination_reasons` / `.identity_reasons` keep them
apart:

- **Privacy** — the three `confidential` flags + person consent (the table in "Confidential /
  privacy flag"). Unchanged.
- **Certainty of the current determination** — new. **A qualified identification is not
  exported**, for *any* value in the closed set (`cf.` `aff.` `nr.` `agg.` `gr.` `?` `sp.`
  `spp.` `indet.`): the qualifier **expresses doubt**, and the mirror is a published statement
  of what this collection holds. **A determination above species rank is not exported either**
  (species or below — a subspecies is *more* precise, not less), nor is a specimen with no
  current identification. A rank the model cannot place is reported as *unplaceable*, never as
  "not to species" — we never compared it.
- **Identity — no catalog number** (rule 6, decided 2026-10-06). TaxonWorks itself would
  take the row: its importer creates the CatalogNumber identifier only
  `if attributes.dig(:catalog_number, :identifier)` (`occurrence.rb:344` @ `897f385`). That
  is exactly the problem — its *"Is already in use"* guard (`:366`) sits inside that branch,
  and our Compare recognises "already on TaxonWorks" by catalog number alone. A numberless
  specimen would look not-yet-uploaded forever and be **created again by every export**.
  The specimen row shows it in the catalog slot itself ("no number", with the consequence
  on hover) rather than as a fourth badge. *Not built, deliberately:* matching on a locally
  minted `occurrenceID` instead — TW stores it as `Identifier::Local::Import::Dwc` only when
  the import dataset has a core-record-identifier namespace set and drops it silently
  otherwise (`:378-383`), and whether `/identifiers` returns that type usably is unprobed.

All three are absolute, with **no setting**, for the same reason rule 3 (a confidential collector)
has none: the importer is CREATE-ONLY, so a record published too early can be neither
corrected nor deleted through the API, while a record withheld today exports fine tomorrow.

**The reason travels with the specimen, not in a list of its own (decided 2026-07-29, #170).**
Ineligibility is a property of a record, so it is shown **wherever that record is browsed** —
three badges on the shared specimen row (`record_summary.py`), one per ground, in
`export_decision`'s own rule order. The TaxonWorks tab accordingly has **no "Why some are not
eligible" expansion**: clicking a collection's *Not eligible* box opens the identical
catalog-number list in Explore, where every row carries its own reason. One list, not two —
and a page-level list of every withheld record grows without bound on a real database, while
the badges cost nothing per row. The badge vocabulary (glyphs, the three colours, the
one-badge-per-*ground* rule) is design.md's → "Specimen-row export badges".

**`export_decision` stays the single source for those badges** — a specimen the row calls
withheld can never be one the sync tool disagrees on. So the surfaces that need to explain
*which* ground applies read `ExportDecision.recorded_by_state` (`""` / `confidential` /
`blocked` / `redacted`) and `.determination_reasons`, both added for this, rather than
re-deriving either from `reasons` by string-matching. It takes the current
`TaxonDetermination` as an optional parameter for the same reason it already takes `event`:
the list surfaces have outer-joined it already, and without it each row lazily re-derived it
(measured: 20 specimens, 21 extra queries). Its default is a **sentinel, not `None`** —
`None` is the legitimate "genuinely undetermined" value, and a `None` default would silently
fall back to the lazy lookup for exactly the specimens that most needed the fix.

**TaxonWorks could not carry the qualifier anyway** — worth recording, because the API makes it
look like it could. Its importer folds `identificationQualifier` into the **OTU's name**
(`otu_names` → `otu_attributes[:name]`, `dataset_record/darwin_core/occurrence.rb:1573-1576` @
`897f385`), so `cf.` arrives as an OTU literally named `"cf."` on the protonym — measured on our
own sandbox upload, OTU 1707966, `name: "cf."` on *Dodecastichus geniculatus*. And its
`dwc_occurrences` projection never emits the term back: no entry in
`CollectionObject::DwcExtensions::DWC_OCCURRENCE_MAP`, no `dwc_identification_qualifier` in
`Shared::Dwc::TaxonDeterminationExtensions`, though the column does exist in `db/schema.rb`.
Hence `identificationQualifier` is **not** a `tw_compare._DIFF_FIELDS` entry — it could only
ever report a difference, never agreement (the `occurrenceID` / `stateProvince` rule again).

A **further constraint for any future `TW:` column work**: `occurrence.rb:74`'s
`OTU_ID_INCOMPATIBLE_FIELDS` lists `identificationQualifier` alongside `scientificName`,
`scientificNameAuthorship`, `typeStatus`, `nomenclaturalCode` and the classification terms — so
a file carrying `TW:TaxonDetermination:otu_id` may carry none of them.

### The export is gated on a name pre-flight (decided)

**Nothing is exported until every name it would carry is confirmed present on the currently
configured instance.** A missing name cannot be repaired afterwards, and the failure mode is
silent: with TW's *"restrict to existing nomenclature"* **unset** (its default), the importer
**creates** the missing protonym *and* an OTU (`occurrence.rb:203`, strategy chosen at `:269`),
walking the whole parent chain — so an export quietly injects names, typos included, into the
project's nomenclature. With it set, the row errors instead. Recommend setting it.

`tw_sync.check_names` classifies each name `match` / `missing` / `ambiguous` / `rank_mismatch`
/ `error`; only `match` is exportable. Two safeguards are load-bearing:

- **A false "missing" is worse than no check.** It sends the user to hand-create a name that
  already exists — manufacturing the homonym duplicate the check exists to prevent. So a
  **subgenus-only** difference is a match-with-note, never "missing" (placement is unstable
  and often omitted; see `taxa.binomial_key`).
- **Authorship is the homonym tiebreaker, not decoration.** TW disambiguates same-spelling
  protonyms on `scientificNameAuthorship`, including whether it is parenthesised (a subsequent
  combination) and `year_of_publication` (`occurrence.rb:126-145`). A difference is reported
  (measured: local `Schönherr, 1826` vs TW `Schoenherr, 1826`).

### OTU ids are per-instance and are verified in reverse, never compared (decided)

`taxon.taxonworksOtuID` was captured from whichever instance the name was imported from. **The
same integer denotes a different entity — or nothing — on another server.** So:

- `AppConfig.tw_otu_instance` records the **host** the stored ids belong to; empty or differing
  from `tw_base`'s host ⇒ **untrusted**. (Config, not a per-row column: nothing FKs to a TW id
  and the app mirrors one instance at a time.)
- A stored id is checked by asking TW *what it actually denotes here* —
  `GET /taxon_names?otu_id[]=<id>` — giving `confirmed` / `mismatch` / `absent`. Measured: all
  39 ids captured on `sfg.taxonworks.org` return **zero rows** against `sandbox.taxonworks.org`.
  **Known limitation — `confirmed` does not survive a homonym (2026-07-26, deliberately not
  fixed).** The check compares TW's `cached`, which is the bare name with **no authorship**, so
  where two protonyms share a spelling it cannot tell which one the id denotes. Measured on
  sandbox: `Otiorhynchini` exists twice (taxon_name 2094695 `Schoenherr, 1826`, 2743726
  `Schönherr, 1826`); the stored id 1299727 resolves to the *Schoenherr* one while the local row
  records *Schönherr*, and the check still says `confirmed`. Harmless while **no OTU id is
  emitted anywhere** — the export has no such column — so this is left alone by decision.
  **Fix it before emitting `TW:TaxonDetermination:otu_id`**, by comparing authorship too
  (`authorship_matches`), and note that a bare `ö`/`oe` difference must stay an informational
  note, never a `mismatch` — that transliteration difference is expected and already documented
  above.
- `tw_sync.reconcile_otu_ids` re-points them by name and is called **explicitly**, never as a
  side effect of checking.

**The stamp is asymmetric: hard to earn, easy to lose (decided 2026-07-26).** It is a claim about
*every* stored id in the database, so `reconcile_otu_ids` records it **only** when nothing anywhere
is left unvouched, while `invalidate_otu_provenance` retracts it on a **single** `absent`/`mismatch`
verdict and **never sets it**. A manual fix (`set_stored_otu_id`) never grants it either. This was
learned the hard way twice: stamping after a *partial* reconcile (fixed in 0068-era `2a63a10`) wrote
a stamp claiming 39 ids were sandbox's when 28 were sfg ids resolving to nothing there — and because
the stamp suppresses the tab's warning, the lie hid itself.

**Reconciliation is scoped to `taxa_with_stored_otu_ids`, never to the export's names.** A chain
import stamps an id on **every ancestor it walks** (`taxa._ensure_parent_rows`), so suborders,
families, tribes and subgenera carry ids that no export ever names. Measured: 39 rows held an id and
only 11 were determination targets, so a reconcile scoped to `taxa_to_export` could not reach 28 of
them *by construction* — `unvouched` could never fall to zero and the tool reported a state it
offered no way out of.

**What cannot be resolved automatically is listed, not counted** (`ReconcileResult.unvouched_rows`):
each row names the taxon, the id it still stores, why the match refused, and TaxonWorks' candidates,
with a per-name manual fix. Choosing between homonyms is the user's judgement — which is exactly why
`check_names` refuses to guess it — so the picker labels each candidate with its **authorship**, the
tiebreaker (§5c). **Clearing an id is a legitimate fix:** for a name absent from this instance, an id
captured elsewhere is a false claim about this server, where an empty column claims nothing (§2).

### Verified API facts (probed 2026-07-25; do not re-derive)

Parameters cross-checked three ways: the OpenAPI specs (the user's reference clone at
`0_TaxonWorks/Reference/taxonworks_api/docs/openapi/`), `rdoc.taxonworks.org`'s
`Queries::*::Filter` classes, and live probing.

| fact | consequence |
|---|---|
| **Unknown query params are silently ignored** (a bogus param returns the unfiltered table) | Never trust that a filter applied. An oversized result set is treated as a *lookup failure*, never as matches — a filter that silently didn't apply would read as "not on TW" and, against a CREATE-ONLY importer, push undeletable duplicates. |
| `name[]=<composed name>` + `name_exact=true` matches TW's `cached` exactly | The name pre-flight primitive. The **scalar `name=`** form is NOT this filter — it returned 2257 unrelated names. `rank[]`, `validity`, `otu_id[]`, `nomenclature_code` are also real params. |
| `catalogNumber` is populated on only **1/500** `dwc_occurrences` rows | The DwC projection is **not** a usable identity source. Use `GET /identifiers?type=Identifier::Local::CatalogNumber&identifier_object_type=CollectionObject` (`identifier`, `identifier_object_id`, `namespace_id`). On sandbox 153 CatalogNumbers exist but only **11** are on CollectionObjects — 140 hang on Containers, 2 on Images. |
| `dwc_occurrence_object_id` = the TW `collection_object_id` | Feeds the deep link `{web_base}/tasks/accessions/comprehensive?collection_object_id=N`. `taxonworks.web_base()` derives the web root from the API base (never a second config field). |
| `per=5000` is honored; totals in `pagination-total` headers | The whole occurrence set is ~5 requests. Most rows are `AssertedDistribution`/`MaterialCitation`; filter `dwc_occurrence_object_type=CollectionObject`. |
| **No `/preparation_types`, `/namespaces`, `/repositories`, `/biocuration_classes`** (404) | We **cannot** pre-validate preparations or the namespace mapping — they become a pre-flight checklist the user satisfies in TW by hand. `/otus`, `/people`, `/images`, `/depictions` **do** exist. |
| `extend=[…]` is ignored on index routes — **except `/identifiers`** (measured 2026-07-26: `extend[]=namespace` embeds id/name/short_name/delimiter on the index) | Resolve related records with a second call, but check first: the claim is per-route, not global. |

**The `/identifiers` catalog-number index (probed 2026-07-26; the join key is not the obvious one).**
The identity source §5c already prescribes, now measured in detail — **do not re-derive**:

| fact | consequence |
|---|---|
| **TaxonWorks splits the catalog number.** Local `JJPC-00001` is stored as `identifier="00001"` under namespace 6057 (`short_name="JJPC"`, `delimiter="-"`), and **`cached` = `"JJPC-00001"`** | **`cached` is the join key** against `collection_object.catalog_number`. Matching on `identifier` scored **0 overlap on all 39 specimens**. Accordingly `?identifier[]=JJPC-00001` returns **0 rows** while `?identifier[]=00001` returns 1 — the filter works, it is just not on the full form, and there is **no `cached` filter**. So pull the index and match locally. |
| `type=` and `identifier_object_type=` filter correctly in **both** scalar and `[]` form | The whole project: 429,830 identifiers → 181 CatalogNumbers → **39 on CollectionObjects** (140 Containers, 2 Images). The entire index is **one request**, replacing one lookup per local specimen. |
| `namespace_short_name=` / `namespace_name=` / `namespace_id[]=` all filter (`namespace_short_name=JJPC` → 28 rows, all namespace 6057) | Namespace scoping needs **no** hand-populated `repository.taxonworks_collection_id` (which is NULL). Resolve which namespace is ours *in reverse* from a catalog number known to be uploaded — the OTU-id discipline, applied again. |
| Existence and orphans need **different scopes** | Existence is namespace-**unscoped**: a specimen filed under another namespace is still on TW, and calling it absent re-uploads it (identifier uniqueness is *per namespace*, so TW accepts that duplicate silently). The orphan sweep **is** namespace-scoped, or every other collection in the project counts as our orphan. |
| A **local re-home never changes `catalog_number`** (only `repository_id` moves) | So "on TW under our namespace, not held here" is ambiguous: moved or deleted. A **DB-wide** catalog-number lookup separates them (`tw_compare._orphans`); scoping that lookup to the working collection reports a move as a deletion. |

### The emitted file (decided)

**Tab-separated, `.tsv`** — TW's default `col_sep` for a flat upload is a tab
(`import_dataset/darwin_core.rb:315-317`); a comma file parses as **one column**, so every row
appears to import and is garbage. Header must contain `occurrenceID`, `scientificName`,
`basisOfRecord` or the dataset fails validation outright
(`import_dataset/darwin_core/occurrences.rb:8-11`). `occurrenceID` is the deterministic Darwin
Core triplet `institutionCode:collectionCode:catalogNumber`, so a re-export cannot mint a
second TW record for one specimen.

**Only columns TW actually maps are emitted** — a `[Not mapped]` column is silently dropped, so
emitting it would make the file look like it carried data it did not. Confirmed not mapped at
`897f385`: `lifeStage` (:912), `disposition` (:940), `otherCatalogNumbers` (:950),
`associatedTaxa` (:743), **`municipality` (:1097) and `locality` (:1099)**, `verbatimIdentification`
(absent entirely). Because TW keeps `verbatimLocality` verbatim (:1101) but drops the other two,
our export **folds municipality + locality into `verbatimLocality`** (the event's own verbatim
string wins when present) or that detail is lost.

**An invalid row is refused, never silently rewritten** (the importer is CREATE-ONLY — a
half-wrong row cannot be corrected via the API): `basisOfRecord` outside
PreservedSpecimen/FossilSpecimen (:849), whitespace in `sex` (:892), an interval in
`dateIdentified` (:1394 — unlike `eventDate`, which allows one), a non-integer
`coordinateUncertaintyInMeters` (:1138), or no current determination (now also caught earlier,
as an eligibility rule — this stays as the backstop). **`preparations` must
match an existing TW `PreparationType` or TW errors the row** (:926) — unverifiable via API, so
the distinct values used are listed for the user to confirm.

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
- **A collection is identified by its name, not its code (decided 2026-10-06, migration
  0072).** A private collection ("Frank Lange collection") often has neither a collection
  code nor an institution code, and being forced to invent them put made-up DwC values in
  the vocabulary. So `collection_full_name` is UNIQUE + never blank; `dwc:collectionCode` is
  nullable and unique only where present (partial index). The **default** collection must
  have one (`ck_repository_default_has_code`) — it is the catalog-number prefix. The upgrade
  refuses, rather than renames, when two existing collections share a name.
- **Save-time resolution is by pick, never by typed code.** "Digitize other collection" and
  the Records re-home field share one widget, `app/ui/repository_field.py` — the standard
  vocabulary dropdown (existing collections + `✚ add <name>`) over an adapter onto
  `repository`; `commit(session)` → `repositories.get_or_create_by_name`. A collection added
  that way has a name and **nothing else**; codes are added later in Controlled
  Vocabularies, never guessed. The old `resolve_id` (get-or-create by a freely-typed code)
  is **gone** — do not re-introduce it. The own-collection paths use `get_default`.
- A codeless collection cannot form an `occurrenceID`, so its specimens are refused by the
  export's row validation — give it its codes first.

For a single-collection setup, add one repository in Controlled Vocabularies and flag it as
the default in Settings; give it a real `collection_full_name`. Its `collection_code` /
`institution_code` (e.g. a fictional `"Doe"`) then drive catalog numbers and the identifier
label. Configure the TW import dataset to map `(institutionCode, collectionCode) → TW
Namespace` before import.

> **Rule (revised):** never re-introduce `dwc:collectionCode` / `dwc:institutionCode` as
> columns on `collection_object`. Membership is the `repository_id` FK; codes are resolved
> through the repository.


# Foreign collections: pick from the vocabulary, catalog number optional

Status: design, awaiting review (2026-10-06). No code written.

## Goal

Digitising a specimen held in someone else's collection must not force the user to
invent data. Today "Digitize other collection" demands a `catalogNumber`, a
`collectionCode` and an `institutionCode`, all typed. Many private collections have
none of the three.

After this change:

- The user **picks the collection from the controlled vocabulary** (or adds one by a
  free-typed name such as "Frank Lange collection").
- A collection needs **only a name**. `collectionCode` / `institutionCode` are optional.
- `catalogNumber` may be **empty for a specimen in a foreign collection**, and only there.
- The user's **own collection stays strict**: every specimen in it has a catalog number.
- A specimen without a catalog number is **never exported to TaxonWorks**.

## What already works and does not change

- Membership is `collection_object.repository_id` (FK → `repository`, NOT NULL, ON
  DELETE RESTRICT). Codes are read through the repository, never stored on the specimen.
- Transferring a specimen out of the own collection re-points `repository_id` only; the
  catalog number travels with it unchanged.
- A catalog number can never be reused: the next number is derived from `label_code`
  (`identifiers._next_sequential_number`), whose `code` is globally UNIQUE and whose row
  survives deletion of the specimen (FK `ON DELETE SET NULL`). This is independent of
  which collection currently holds the specimen.
- `label_code`, identifier labels, the print queue, Mounting, Import & Assign.

## Schema (one migration)

### `collection_object`

- `"dwc:catalogNumber"` becomes nullable.
- New CHECK `ck_co_catalog_number_not_blank`:
  `"dwc:catalogNumber" IS NULL OR length(trim("dwc:catalogNumber")) > 0`.
  Without it a second `''` in one collection would collide on the unique constraint,
  and `''` vs NULL would be two spellings of "none".
- `UNIQUE(repository_id, "dwc:catalogNumber")` is kept as is. SQLite treats NULLs as
  distinct, so any number of numberless specimens may share a collection, while a
  filled number stays unique within it. (This is the opposite of the geography
  `IFNULL` index, deliberately: there NULL meant "one uncoded row", here it means
  "no identity claimed".)

### `repository`

- `"dwc:collectionCode"` becomes nullable; same not-blank CHECK.
- `uq_repository_collection_code` becomes a partial unique index
  (`WHERE "dwc:collectionCode" IS NOT NULL`).
- `collection_full_name` becomes the identity: new `UNIQUE(collection_full_name)`,
  plus a not-blank CHECK. The migration **refuses loudly** if existing rows share a
  name; it does not rename or merge them.
- New CHECK: `is_default = 0 OR "dwc:collectionCode" IS NOT NULL`. The default
  collection's code is the catalog-number prefix, so a codeless default is unusable.

### The own-collection invariant (triggers)

A CHECK cannot look at another table, so two `BEFORE` triggers `RAISE(ABORT)`:

- `trg_co_default_requires_catalog_number` (INSERT, and UPDATE OF
  `repository_id`, `"dwc:catalogNumber"` on `collection_object`): refuse when the
  target repository has `is_default = 1` and the catalog number is NULL. Covers
  creating a numberless specimen in the own collection and re-homing one into it.
- `trg_repository_default_requires_catalog_numbers` (UPDATE OF `is_default` on
  `repository`): refuse setting `is_default = 1` on a collection that holds any
  numberless specimen.

Both tables are rebuilt, so the migration re-declares every STRICT, CHECK, UNIQUE,
FK action and server default, and re-creates the triggers.
`tests/test_schema_integrity.py` is extended to guard the new CHECKs, the partial
index and both triggers. `docs/schema.html` is updated.

## Services

- `repositories.py`
  - `create_repository` / `update_repository`: name required, code optional.
  - `display_label(repo)`: the one renderer of a collection for humans —
    `"JJPC — Jakob Jilg collection"` with a code, `"Frank Lange collection"` without.
    Replaces the `f"{code} — {name}"` copies in `batch_tab`, `tw_sync_tab`, `main`
    (Settings), `explore`, `saved_searches`.
  - `get_or_create_by_name(session, name)`: the save-time seam for the picker's
    `✚ add`. `resolve_id(collection_code=…)` (get-or-create by typed code) is
    **removed**; nothing types a code at save time any more.
  - `set_default`: friendly `ValueError` for a codeless collection or one holding
    numberless specimens (the triggers are the backstop).
- `specimens.py`: `catalog_number` optional on create. A NULL number may be filled
  **once** later (`update_collection_object`); a filled number stays immutable.
  Service guard with a friendly message for the own-collection rule.
- `identifiers.format_catalog_display`: returns the shared "no number" rendering for
  a NULL catalog number, and tolerates a NULL collection code.
- `dwc_export.export_decision`: new ground — no catalog number → not eligible.
  It sits beside privacy and certainty with its own reason and its own specimen-row
  badge. The existing "cannot happen" branch for an empty catalog number becomes this
  real rule. Reason for the rule: Compare recognises "already on TaxonWorks" only by
  catalog number, and TW's own duplicate guard (`occurrence.rb:366` @ `897f385`) is
  inside the `if catalog_number` branch (`:344`), so a numberless row would be
  re-exported and silently duplicated on every run. TW itself would accept the row.
- `tw_compare.py`, `tw_media_compare.py`: numberless specimens are excluded from the
  identity join (they are never on TW through us).
- `bulk_import.py`: unchanged — `catalogNumber` stays required, because dedup is the
  `UNIQUE(repository_id, catalogNumber)` pair.
- `batch_ops.py`: by-taxon works unchanged; the pasted catalog-number list cannot
  address numberless specimens (stated in the UI). `apply_repository` into the own
  collection reports numberless specimens as refused rather than failing the batch
  half-way.

## UI

- **New shared widget `repository_field`** (`app/ui/repository_field.py`): the same
  custom-dropdown UX as `vocab_field` — existing collections by `display_label`, plus
  `✚ add <typed name>`; no free-text escape. `commit(session)` returns the
  `repository_id`. Used by both places that choose a foreign collection, so they
  cannot drift:
  - Digitize → "Digitize other collection" (`specimen_form`, `identifier_policy=
    "visiting"`): replaces the three typed inputs. The own collection is not offered.
    The chosen collection's codes are shown read-only when present. `catalogNumber`
    is an optional input.
  - Records → the re-home field (replaces the typed `collectionCode` input).
- **Digitize validation**: visiting requires only a collection. Standard is unchanged.
- **Collections card** (Controlled Vocabularies): name required, code optional; the
  default ★ is unavailable for a codeless collection.
- **Settings → Default collection**: lists only collections with a code.
- **Everywhere a specimen is listed** (Explore rows, Records search, record sheet,
  Batch tools, TaxonWorks tab lists): goes through `format_catalog_display`, so a
  numberless specimen reads "no number" with its collection name.
- **Records**: an empty catalog number shows an editable field; once saved it is
  read-only like every other.
- Explore CSV export: empty cell for a missing number or code.

## Known limitation (accepted)

A numberless specimen has no handle besides its data. Two identical specimens from
one event in the same foreign collection are indistinguishable, and nothing can stop
the same specimen being entered twice. This mirrors the physical situation.

## Out of scope

- Exporting numberless specimens to TaxonWorks. It would need a stable local
  `occurrenceID` and a Compare path on `Identifier::Local::Import::Dwc`, which TW
  only stores when the import dataset has a core-record-identifier namespace set
  (`occurrence.rb:378-383`). Separate follow-up.
- Recording previous owners / provenance of a transferred specimen.

## Testing

- Schema: nullable + CHECKs + partial index + both triggers present and firing from
  raw SQL (integrity test).
- Services: numberless create in a foreign collection succeeds, in the own collection
  is refused; re-home of a numberless specimen into the own collection is refused;
  fill-once then immutable; two numberless specimens coexist, two equal numbers in one
  collection do not; codeless collection create / duplicate-name refusal;
  `set_default` refusals; `export_decision` withholds a numberless specimen.
- UI, driven through the real forms: digitise into a new name-only collection without
  a number; transfer an own specimen to it and confirm the catalog number is kept and
  the next reserved code does not reuse it.

## Docs to update with the change

CLAUDE.md (the specimen invariant, the `collection_object` / `repository` rows, the
print-queue table's visiting row, §5c eligibility, the namespace section's
`resolve_id` paragraph), `docs/schema.html`, `docs/design.md` (the new field and the
export badge).

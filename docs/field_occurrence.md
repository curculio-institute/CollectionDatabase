# Field occurrences (HumanObservation) — design proposal

**Status:** **accepted 2026-07-11** — implementation in progress (migrations 0059+). This is
the design record for the `field_occurrence` model; it is the *what/why*. The schema reference
(`schema.html`) and CLAUDE.md own the settled contract as the build lands; this file is the
rationale.

Related: [[project_media_extid_rearing_done]] (life-stage records, which this does **not**
yet fold in), CLAUDE.md §5/§5b (TaxonWorks FieldOccurrence constraints).

---

## 1. Why

A `collection_object` is a **physically held specimen**: it has a `catalog_number` (NOT
NULL, immutable, = a physical pinned label) and collection membership (`repository_id`).
That invariant is load-bearing across Records, Explore, counts, labels, and TW sync.

But we also need to record things we did **not** collect a specimen for:

- a **host plant** a beetle was collected on (the associated organism, #6);
- a **beetle observed but not collected** (a field sighting);
- (later, maybe) the wild immature stage of a reared specimen.

These are **observations**, not held specimens. Forcing them into `collection_object`
would either fabricate catalog numbers (polluting the specimen space and the sync join
key) or weaken the invariant and require `basisOfRecord`-filtering every specimen surface.

TaxonWorks solves this with a **separate class**: `CollectionObject` (held) vs
`FieldOccurrence` (observed, exported as DwC `HumanObservation`). We mirror that split.

## 2. Scope (decided)

- `field_occurrence` is the **general home for any HumanObservation**, not a host-plant
  feature. Same table serves host plants and standalone beetle sightings.
- **Wired now:** only the host-plant path (via a biological association to a collected
  specimen), because that is what #6 needs.
- **Not surfaced now:** standalone observations. The model is built general so they can get
  their **own Digitize mode/tab later** (beside Standard / Mounting / Digitize-other-
  collection) plus browse surfacing in Explore/Records — but none of that is built yet.
- **Life-stage records are NOT folded in now.** A wild larva *is* conceptually a
  field_occurrence (HumanObservation, same taxon, shared locality, own eventDate, linked to
  the specimen), so `life_stage_record` is a natural future convergence — but folding it in
  means migrating existing data, so it stays as-is for now. Noted, not done.

## 3. The model

### `field_occurrence` (new STRICT table)

Parallel to `collection_object`, stripped of the "physically held" columns.

| Column | Notes |
|---|---|
| `id` | PK |
| `occurrence_id` (TEXT, UNIQUE, NOT NULL) | **The stable identity** — a generated UUID, DwC `occurrenceID`. This is the key resolution to the catalog-number problem: observations are keyed by `occurrenceID`, held specimens by `catalogNumber`; the specimen invariant is untouched. Used for export and diffing. |
| `collecting_event_id` FK → `collecting_event` (NOT NULL, ON DELETE RESTRICT) | An observation shares the where/when model with specimens; `recordedBy` lives on the event, exactly as for specimens. |
| `dwc:basisOfRecord` (TEXT, NOT NULL, CHECK ∈ {HumanObservation, MachineObservation}, default HumanObservation) | Observation basis. Deliberately **not** the specimen CHECK set. |
| `dwc:individualCount` (INTEGER, NOT NULL, default 1, CHECK ≥ 0) | mirrors `collection_object` |
| `dwc:sex` (TEXT, nullable) | |
| `dwc:lifeStage` (TEXT, nullable) | |
| `dwc:occurrenceRemarks` (TEXT, nullable) | |
| `confidential` (INTEGER, NOT NULL, default 0, CHECK ∈ (0,1)) | same privacy semantics as `collection_object` (drop from export) |

**Dropped vs `collection_object`** (all "physically held" concepts): `catalog_number`,
`repository_id`, `preparation_id`, `disposition_id`, `otherCatalogNumbers`. None apply to
something we do not hold.

### Determination — reuse `taxon_determination` via an exclusive arc

Rather than a second determination table, make `taxon_determination`'s subject an
exclusive arc:

> subject = (`collection_object_id` XOR `field_occurrence_id`), enforced by a named CHECK.

This is the elegant part: a field occurrence gets the **entire** determination machinery
for free — `taxon_id`, `is_current`, `identifiedBy`, `verbatimIdentification`, and crucially
**`identification_qualifier`** (the closed CHECK set from #3). So the host-plant qualifier
the user wants exposed is *already there* — no new column. `render_identification()` applies
unchanged.

- **`identifiedBy` defaults to the event's `recordedBy`** at save time (you observed and
  identified it in the field). Not stored redundantly beyond the normal determination FK.
- **At host-plant input, only the qualifier is exposed.** basisOfRecord (HumanObservation),
  identifiedBy (=recordedBy), and the shared event are all automatic/hidden.

### Association linkage — extend `biological_association`'s object arc

Today the object arc is (`object_collection_object_id` XOR `object_taxon_id`). Add
`object_field_occurrence_id` so the arc becomes **exactly one of three**. A host is then a
`field_occurrence` linked to the beetle (`subject_collection_object_id`) by a
`biological_relationship` (default *collected from*). A standalone sighting is just a
`field_occurrence` with no association.

> Keeping `object_taxon_id` too means a lightweight "collected on *Salix* (no observation
> record)" is still expressible; promoting it to a full observation is choosing the
> field_occurrence arc instead.

### External resource identifiers (core — iNaturalist)

A field occurrence very often **originates from an iNaturalist observation**, so its iNat
URL is a defining attribute, not an extra. Extend the `external_identifier` exclusive arc to
include `field_occurrence` (today: `collection_object` XOR `biological_association`) **as part
of the core model, not a later phase**.

**Built exactly like it is for `collection_object`** — no new pattern. The same
`app/services/external_ids.py` and the same `app/ui/external_id_panel.py::build_external_id_button`
(link-icon + count badge → Abort/Save modal, single "Resource identifier (URI)" field), in both
**bound** (Records — writes straight to the DB) and **staged** (Digitize — committed on Save to
the new record's id) modes. The user pastes the observation URI (`value`); `source`/`label` stay
nullable (a source can be derived from the URI later; no iNat auto-detection, per #49). The only
delta from the specimen is the attachment target (`field_occurrence_id` on the arc).

### Media

Extend the `media_attachment` exclusive arc to include `field_occurrence` too, so a host /
sighting can carry its own photo directly (rather than on the linked association). Natural for
observations (iNat records are photo-first); can trail the core table by one migration if
schedule demands, but belongs to the same model.

## 4. Export (Phase 3 contract; not built)

- Each `field_occurrence` → one DwC **HumanObservation** record, keyed by its
  `occurrenceID`, taking `eventDate`/locality from the shared event, `scientificName` +
  `identificationQualifier` from its determination, `recordedBy` from the event,
  `identifiedBy` from the determination.
- When it is an association **object**, the beetle and the host are linked by a **derived**
  `dwc:associatedOccurrences` / resourceRelationship between the two `occurrenceID`s — no
  stored resource-relationship table (the FK *is* the relationship), exactly the pattern
  `life_stage_facets()` already uses.
- **TW sync stays out of the DwC path.** TW's DwC importer has no HumanObservation →
  FieldOccurrence route (CLAUDE.md §5b); pushing field occurrences to TW is a **future
  internal-API integration**, independent of this model. `occurrenceID` is the stable key it
  will diff on.

## 5. Migration plan (Alembic, from 0059)

Each table rebuild must **re-declare every STRICT/CHECK/UNIQUE/FK action** (migration
discipline; the `biological_association` exclusive-arc CHECKs are unnamed from mig 0007 and
have bitten a rebuild before — see CLAUDE.md §8).

1. **Create `field_occurrence`** (STRICT, all constraints above).
2. **Rebuild `taxon_determination`** → subject exclusive arc (`collection_object_id`
   nullable, add `field_occurrence_id`, add the XOR CHECK). Backfill: existing rows all set
   `collection_object_id`, `field_occurrence_id` NULL.
3. **Rebuild `biological_association`** → add `object_field_occurrence_id`, widen the object
   CHECK to exactly-one-of-three. Re-declare the subject arc CHECK verbatim.
4. **Rebuild `external_identifier`** → add `field_occurrence_id`, widen the exclusive arc to
   exactly-one-of (`collection_object`, `biological_association`, `field_occurrence`). **Core**
   — the iNaturalist URL is a defining attribute of an observation.
5. **Rebuild `media_attachment`** → add `field_occurrence_id` to its exclusive arc (may trail
   step 4 by one migration, but same model).

`tests/test_schema_integrity.py` must be updated to assert the new arcs and that nothing was
dropped.

## 6. UI

- **Now (host plant):** the biological-association panels (Digitize / Records / Mounting) and
  the Import & Assign host block create — under the hood — a `field_occurrence`
  (HumanObservation, shared event, determination = host taxon + qualifier, identifiedBy =
  recordedBy) **plus** a `biological_association` linking beetle → field_occurrence. The user
  sees only: pick host taxon, pick qualifier. Everything else is automatic.
- **Later:** a "Field observation" Digitize mode for standalone sightings, and a held-vs-
  observed filter in Explore/Records so observations are browsable as their own occurrences.

## 7. Open questions

- The exact DwC term set TW's FieldOccurrence internal API wants (deferred until that
  integration is scoped).
- Whether standalone observations reserve their own `occurrenceID` prefix/scheme or a bare
  UUID (bare UUID is the current lean — no human-facing code, since there is no label).
- Folding `life_stage_record` in (deferred; §2).

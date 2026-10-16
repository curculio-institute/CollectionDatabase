# Biological-association compare — design

Status: draft for review (2026-10-17). Part of the TaxonWorks sync (#149, `docs/taxonworks.md`).

## Goal

TaxonWorks cannot import biological associations (`associatedTaxa` is `[Not mapped]`,
`docs/taxonworks.md` §5b), so every association is entered on TaxonWorks **by hand**. The
TaxonWorks tab's **Compare** gains a fourth result section, next to occurrences, annotations
and media, that lists the divergence between the local associations and TaxonWorks' — above
all a **worklist of associations (and their tags and data attributes) still to add on
TaxonWorks** — and confirms the ones already there.

Read-only and diagnostic, like the annotation and media compares: nothing is written to
TaxonWorks or to the local database, nothing is stored between runs. After entering
associations by hand, the user runs Compare again.

## Decisions (user, 2026-10-17)

- **Anchor on specimens.** Only associations with a specimen on one side are compared. The app
  never creates a taxon-subject (OTU-to-OTU) association; a row the schema still allows
  (`subject_taxon_id`) is counted as not compared, never skipped silently.
- **The other side is compared by taxon name** (approach 1), not by OTU id. OTU ids are
  per-instance and are never compared (`docs/taxonworks.md` §5c "OTU ids are per-instance").
- **Anatomical parts are ignored for now; only the host's OTU counts.** On TaxonWorks a host is
  often an anatomical part (JJPC-00102: *collected from → rhizome: Typha L.*). Its OTU is
  compared; the part is shown, never compared. A local part vocabulary is not part of this
  design.
- **Tags and data attributes on associations are compared** ("very important"), for every
  matched association, and listed with every association still to add.
- Specimens without a catalog number are never on TaxonWorks under a known identity (rule 6,
  §5c) — their associations are counted as not compared.

## Verified TaxonWorks behaviour

At the pinned commit `897f385` unless noted; live probes on `sfg.taxonworks.org`, 2026-10-17,
read-only. Re-grep line numbers before citing them in code.

| Fact | Evidence |
|---|---|
| `GET /api/v1/biological_associations/basic` returns, per association: `id`, `subject{id,type,label,…}`, `object{id,type,label,…}`, `subject_otu_id`, `object_otu_id`, `relationship` (**name only**), ordered by `biological_associations.id` | `config/routes/api_v1.rb:127`; `biological_associations_controller.rb` `api_index_basic`; `app/views/biological_associations/api/v1/basic.json.jbuilder` |
| `GET /api/v1/biological_associations` returns `biological_relationship_id`, `biological_association_subject_type/_id`, `biological_association_object_type/_id` | `api_v1.rb:128`; `api/v1/_base_attributes.json.jbuilder` |
| Both accept the association filter, incl. `collection_object_id[]` and `collection_object_as_subject_or_as_object=true` | `lib/queries/biological_association/filter.rb` `PARAMS` |
| An anatomical-part side carries the **part's** id and type, but `*_otu_id` is the OTU at the top of its origin chain | probe: JJPC-00102 → association 321383, object `AnatomicalPart 687`, label `"rhizome: Typha L."`, `object_otu_id 1471869` |
| `GET /api/v1/otus?otu_id[]=…` returns rows with `id`, `taxon_name_id` | probe: 1471869 → 1655474, 734711 → 835937 |
| `GET /api/v1/taxon_names?taxon_name_id[]=…` returns `id`, `cached` (no authorship), `cached_author_year`, `rank` | probe: 1655474 → `Typha`, `L.`, genus |
| `BiologicalAssociation` has tags and data attributes | `app/models/biological_association.rb` (`Shared::Tags`, `Shared::DataAttributes`) |
| Tags / data attributes are fetched by `tag_object_type` / `attribute_subject_type` + ids | already used by `tw_annotation_compare.fetch_tags` / `fetch_data_attributes` |
| There is **no** `anatomical_parts` endpoint | `docs/taxonworks.md` §5b "Anatomical parts cannot be fetched" |
| Unknown query params are silently ignored | §5c — every response is checked against what was asked |

## What is compared

### Anchors

`tw_media_compare.specimens_on_tw(session, repository_id=…, index=…)` gives the collection's
specimens TaxonWorks holds (catalog number → TaxonWorks collection object id; ambiguous
catalog numbers excluded and listed). A **local association is anchored** when one of its
specimen sides (subject or object) is such a specimen. An association anchored twice (both
sides are specimens on TaxonWorks) is compared once, anchored on its subject.

TaxonWorks associations are fetched for the anchor ids, as subject or as object, and
de-duplicated by association id.

Not compared, each reported as a count with its reason:

- associations whose specimen is not on TaxonWorks yet (export it first);
- associations on specimens without a catalog number;
- associations on catalog numbers held by more than one TaxonWorks record (listed by number);
- associations with a taxon subject (the app does not create them).

### Matching a local association to a TaxonWorks one

A local association `L` and a TaxonWorks association `T` on the same anchor specimen **match**
when all three hold:

1. **Direction:** the anchor is the subject in both, or the object in both.
2. **Relationship:** `L`'s relationship is linked **and verified on the configured host**
   (`tw_id` set and `tw_host == taxonworks.configured_host()`), and `T.biological_relationship_id
   == tw_id`. Never on names.
3. **Other side:** the names match (below).

Each `T` matches at most one `L` and vice versa. Exact name matches are paired first, then
subgenus-only matches; within a tier, by ascending local id and TaxonWorks id (deterministic).

### The other side's name

- **Local:** the host taxon (`object_taxon`), a field occurrence's current identification
  (`field_occurrence.current_determination`), or a specimen's current identification
  (`is_current == 1`). No current identification → *Can't compare*.
- **TaxonWorks:** `*_otu_id` → `/otus` → `taxon_name_id` → `/taxon_names` → `cached`,
  `cached_author_year`, `rank`, whatever the side's type (OTU, field occurrence, collection
  object, anatomical part). An OTU without a taxon name, or a missing row → *Can't compare*.

The rule mirrors the export's name pre-flight (`tw_sync.check_names`, §5c):

- Compare the composed bare name (`taxa.compose_scientific_name`) with `cached`, whitespace
  collapsed. Equal → name match.
- Equal after `taxa.binomial_key` on both (subgenus-only difference) → match, with a note.
- **Authorship** (`taxa.authorship_matches`): when both sides have one and they differ, the
  host **differs** (same spelling, possibly a homonym). When one side has none → match with a
  note. A difference that disappears after folding `ä ö ü ß` → `ae oe ue ss` on both sides is a
  match with a note (§5c: transliteration differences are expected).
- Synonymy is not resolved: a local synonym against TaxonWorks' accepted name (or the reverse)
  is a *Host differs*, which the user judges. (Determinations may target synonyms by design.)

### Result groups

| Group | Meaning | Shown per row |
|---|---|---|
| **Confirmed** | matched | counted only, not listed |
| **To add on TaxonWorks** | local association with no match | catalog number, relationship, host as recorded locally and its kind (taxon / field occurrence / specimen + catalog number), notes, **its tags and data attributes**, link *open in TaxonWorks* |
| **Only on TaxonWorks** | TaxonWorks association with no match | catalog number, TaxonWorks relationship name, TaxonWorks' full label for the other side (incl. any anatomical part), its tags / data attributes, link |
| **Host differs** | exactly one unmatched `L` and one unmatched `T` left on the same anchor, same direction and same verified relationship, names differ | both hosts side by side, the reason (name / authorship), link |
| **Can't compare** | an association that cannot be judged | the association and the reason (relationship not linked or not verified here; no current identification; TaxonWorks OTU without a taxon name) |

When several unmatched associations remain on one anchor with the same relationship, they are
**not** paired into *Host differs* — that would be a guess; they stay in *To add* / *Only on
TaxonWorks*.

**A false "to add" is worse than no answer** (it sends the user to create a duplicate that
cannot be deleted through the API). So on an anchor where **any** association, local or
TaxonWorks, is *Can't compare*, the anchor's otherwise unmatched associations are reported as
*Can't compare* too, with the reason "another association on this specimen could not be
compared", rather than as *To add* or *Only on TaxonWorks*.

### Tags and data attributes on associations

- **Matched pairs:** compared like a specimen's annotations (`tw_annotation_compare` rules):
  only terms linked and verified on the configured host, joined on `tw_id`; findings
  *missing on TaxonWorks*, *only on TaxonWorks*, *value differs*, *unlinked on TaxonWorks*;
  unverified local terms listed as not compared.
- **To add:** the association's local tags and data attributes are listed with it, each with
  its TaxonWorks name when linked and verified, or marked *local only — never sent* /
  *not verified*.
- **Only on TaxonWorks:** the TaxonWorks tags / data attributes are listed with it.
- The annotation section's current line "Annotations on biological associations are not
  compared" is removed; association annotations now appear in the association section only.
- Association annotations are still **never exported** (TaxonWorks cannot import associations);
  this is a compare, not an export.

## Fetching (all read-only, through `taxonworks.get_with_retry` / `fetch_all_pages`)

1. `/biological_associations/basic` and `/biological_associations`, each with
   `collection_object_id[]` = a batch of ≤ 100 anchor ids and
   `collection_object_as_subject_or_as_object=true`, all pages.
2. `/otus?otu_id[]=` for the distinct `*_otu_id` of the other sides, batched.
3. `/taxon_names?taxon_name_id[]=` for the distinct `taxon_name_id`s, batched.
4. `/tags` and `/data_attributes` with `BiologicalAssociation` and the TaxonWorks association
   ids (reusing `tw_annotation_compare.fetch_tags` / `fetch_data_attributes`).

Independent requests run concurrently, as in the annotation compare.

**Every response is checked, and any failure fails the whole association section:**

- each association row must involve one of the requested anchors (else the filter was ignored);
- the two association endpoints must return the same set of ids — a difference is a lookup
  failure, never a finding;
- `/otus` and `/taxon_names` rows must be among the ids asked for; every id asked for must come
  back;
- an oversized page, a repeated id, or a missing field the comparison relies on is a lookup
  failure (`fetch_all_pages` rules).

## Code shape

- **New `app/services/tw_association_compare.py`**, shaped like `tw_annotation_compare.py`:
  fetch functions (I/O), a pure `compare_associations(session, anchors, tw_rows, names,
  tw_tags, tw_data, *, host)` returning an `AssociationCompareResult`, and
  `run_association_compare(session, *, repository_id, index, host=None)`. Module docstring
  cites the verified facts above.
- `tw_annotation_compare`: its per-record diff is reused for association annotations (extended
  to take an association target), not copied.
- `app/ui/tw_sync_tab.py`: `_render_association_results(result)`, a "Biological associations"
  section after Media — summary line, one `ui.expansion` per non-empty group (default closed),
  count lines for what was not compared. The run is part of the existing Compare.
- Docs: `docs/taxonworks.md` §5c gains "Association compare" (rules + verified facts — the one
  home), `docs/app_structure.md` the module row, the annotations spec its changed line.

## Errors

- A lookup failure shows "Biological associations were not checked: <cause>" (cause from
  `taxonworks.explain_tw_error`). Never a partial list. The other compare sections are
  unaffected.
- No specimens on TaxonWorks in the collection → "No specimens on TaxonWorks yet — nothing to
  compare associations against."
- TaxonWorks not configured → the section is not run (same gate as the other compares).

## Testing

Pure comparison (temporary database, no network):

- match; each group; *Host differs* only for a single leftover pair; several leftovers stay
  unpaired;
- other side as anatomical part (only the OTU's name counts), as OTU, as field occurrence, as
  collection object; local host as taxon, field occurrence (current identification), specimen;
- the anchor as the object; an association anchored on both sides compared once;
- subgenus-only difference → match with note; authorship differs → *Host differs*; authorship
  missing on one side → match with note; `ö`/`oe` → match with note; synonym vs accepted →
  *Host differs*;
- two local vs one TaxonWorks association → one confirmed, one *To add*;
- relationship unlinked / linked but not verified → *Can't compare*, and the anchor's other
  unmatched associations demoted to *Can't compare*;
- no current identification; TaxonWorks OTU without a taxon name;
- association annotations: matched pair findings; listed with *To add* and *Only on TaxonWorks*;
  unverified terms not compared;
- every count line (not on TaxonWorks, no catalog number, ambiguous catalog number, taxon
  subject).

Fetching (mocked HTTP client, no real network): foreign rows → failure; `/basic` vs plain id
sets differ → failure; `/otus` or `/taxon_names` missing or extra ids → failure; oversized
page → failure; duplicates across pages de-duplicated; batching at 100.

In the app: driven on a database copy against the user's TaxonWorks (read-only), only after
asking. Known case: JJPC-00102 (*collected from → rhizome: Typha L.*).

## Not in this version

- A local anatomical-part vocabulary (and comparing the part).
- Comparing association citations or notes.
- Numberless museum specimens (would need an identity other than the catalog number, §5c
  rule 6).
- Writing anything to TaxonWorks; associations stay hand-entered.

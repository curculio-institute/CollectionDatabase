# Taxonomy: synonymy, name sources, ranks, taxon search

> Moved verbatim out of `CLAUDE.md` on 2026-10-07 to keep the always-loaded file compact.
> This file is now the **one home** for the detail below; `CLAUDE.md` keeps only the binding
> rule and a link here. `§n` references are `CLAUDE.md` section numbers: §2 principles and §8
> conventions are in `CLAUDE.md`; §4 → `data_model.md`, `vocabularies_geography.md`,
> `taxonomy.md`; §5 / §5b / §5c → `taxonworks.md`; §6 → `app_structure.md`.

### Synonym integrity (acceptedNameUsageID is the single source)

A taxon is a synonym **iff** it links to an accepted name. **Status lives *only* in
`acceptedNameUsageID`; the name carries its own lineage.** In the atomic-name model (Epic #30)
every name is parented under its *own* genus — a synonym sits under its own genus, independent
of its accepted name (so *Curculio forticollis*, a synonym of *Otiorhynchus fortis*, stays
parented under *Curculio* and composes to "Curculio forticollis"). This is what makes name
composition uniform for valid names and synonyms, and makes a status flip (synonym ↔ valid) a
pure **one-field toggle with no name rewrite and no re-parenting**. The tree still groups
synonyms under their accepted name via `acceptedNameUsageID` for *display* — but until #151
(below), a specimen determined under a synonym was invisible to search/filter/count by the
accepted name, and to the tree's own displayed specimen totals.

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
own lineage), and `merge_taxa()` (below). A static test
(`test_synonym_integrity.py::test_parent_and_accepted_writes_are_centralised`) fails if any
code outside `taxa.py` assigns these columns directly. **No fallback defaults** — required
links are inherited or the op fails loudly, never guessed.

**Search and filtering are synonym-aware (#151, decided 2026-07-26).** A determination
legitimately freezes the name as used (§2) — the person who identified a specimen as
*Entimus formosus* may not agree with a later synonymisation with *Entimus sastrei*, and that
name must never be silently rewritten. But the two rows are the **same OTU**, so filtering,
searching, or counting by *either* name must retrieve/count specimens determined under
**both** — a search is not a determination, and hiding one name's material behind the other
is a silent wrong value (§2) by omission.

- **The single shared primitive: `taxa.expand_taxon_scope(taxon_id, children, accepted_of,
  syn_map)`.** `taxon_id` plus every descendant in the parent/child tree, **plus the synonym
  group of every taxon reached** (`taxa.synonym_group_ids` — the accepted name + all its
  synonyms, whichever of the two `taxon_id` itself is). A synonym is parented under its OWN
  lineage (own genus, possibly a completely different genus than its accepted name's — the
  *Curculio forticollis* / *Otiorhynchus fortis* example above), so it is **never** reached by
  the parent/child walk alone, even when its accepted name is well inside the scope; each
  node's OTU partners are pulled in explicitly as the walk proceeds, so a genus-level filter
  also reaches a synonym filed in an unrelated genus.
- **Three call sites, one primitive, no drift:** `explore.py::_descendant_ids` (the taxon facet behind `query_specimens` / `checklist` /
  `events` / `counts`), `batch_ops.py::descendant_taxon_ids` (Batch tools' "every specimen of
  a taxon"), and — for free, since it calls the same function — the TaxonWorks sync tab's
  taxonomy-scope picker (`tw_sync_tab.py`, "Curculioidea only"). The **verbatim** id-scope
  (`id_scope=("verbatim",)`, matching the frozen `verbatimIdentification` text) is extended
  the same way but narrower: it matches against every name in the searched taxon's *own*
  synonym group only, not the full descendant expansion — mirroring how that scope has always
  searched one node's name, never its children's.
- **The taxonomy tree's `spec_count` rolls up too** (`taxonomy.py::_build_node`): a synonym's
  own row still shows its own count (how many specimens are literally determined under that
  name), and the accepted node's total now *includes* it — so the number displayed in the tree
  finally agrees with what filtering by that accepted name in Explore actually retrieves.
- **What does NOT change:** the drawer-order checklist (`explore.py::checklist`) still groups
  a synonym-determined specimen under **its own** taxonomic position, as its own species row —
  that mirrors what is physically written on the determination label (the Käfersammlung
  layout) and is a display decision, not a retrieval one. Only *retrieval/counting* is
  synonym-aware; *grouping-by-literal-name-used* is unchanged and stays correct.

**Merging duplicate names (de-duplication, NOT synonymisation — decided 2026-07-18).**
`taxa.merge_taxa(session, keep_id, absorb_id)` (Taxonomy tab → "Merge names") folds two rows
that are the **same name written two ways** — a typo, or an exact/subgenus duplicate — into
one: every reference (determinations, biological associations, dataset-record links — all FKs
to `taxon` discovered dynamically via `PRAGMA`, plus absorb's synonyms and children, the
latter re-homed through `reparent` and recomposed) moves onto `keep`, then `absorb` is deleted.
`keep` keeps *its* name/lineage, so choosing which to keep chooses the surviving spelling. A
determination's frozen `verbatimIdentification` is **not** rewritten — the name as recorded
stands; only the live `taxon_id` moves. It is **not** a way to record synonymy: `_merge_blocker`
refuses (loudly, and surfaced in `merge_taxa_preview.blocker`) when the two are **different
ranks** or **either is itself a synonym** — that is `synonymize()`'s job. The UI dialog carries
the same warning. (Because the re-points use raw SQL, `merge_taxa` `expire_all()`s before
deleting `absorb`, or SQLAlchemy would null the NOT-NULL `taxon_id` on determinations it still
thinks `absorb` owns — the same ORM hazard `delete_taxon` guards against.)

**Preventing the duplicate at the source (Import & Assign — decided 2026-07-18).** The duplicate
above (`Carabus arvensis` vs `Carabus (Eucarabus) arvensis`) arose because Import & Assign's
auto-resolver matched on the *exact composed name*, and the subgenus is baked into that name yet
optional in how a binomial is written — so a bare-binomial CSV cell dead-ended at "Add manually"
and spawned a second row. Fix: after an exact `find_taxon_by_name` miss, the resolver falls back
to `find_species_ignoring_subgenus` (matches on `binomial_key` — the name with any `(Subgenus)`
stripped). A **single** subgenus-insensitive candidate resolves to it (no duplicate); **several**
(genuinely different subgenera, or an existing duplicate) are **presented, never auto-picked**
(the silent wrong value of §2). Subgenus placement is unstable across catalogues and often
omitted, so it is deliberately not a matching discriminator — the binomial is the stable key.

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


### Taxon search widget (`app/ui/taxon_search.py`)

- **Local-first**: searches local DB (150 ms debounce), then appends TW results in parallel.
- **Multi-token search**: query is split on whitespace; each token must appear in the name.
  `"Sit lin"` matches `"Sitona lineatus"`.
- **Both sections always shown** unless all TW results are already in the local DB (deduplication
  filters them out; the TW section then says so rather than rendering empty, #152).
- **TW deduplication is on the full composed name, never the bare epithet (fixed #152).** The
  autocomplete payload's `name` field is only the epithet (`"formosus"`, not `"Polydrusus
  formosus"`) — matching on it hid *every* TaxonWorks name sharing that epithet whenever any
  local species happened to end in it (`Entimus formosus` locally silently hid `Polydrusus
  formosus` on TW). The widget now batch-fetches each result's full `taxon_names` record
  (`detail_cache`, already needed for the valid-name label and 🌿 code) **before** filtering, and
  dedupes on `cached` — the composed full name — against local `dwc:scientificName`.
- **TW pick imports the clicked name** (synonym or valid) via `fetch_full_classification(r["id"])`.
  `get_or_create_from_tw_data` handles valid-name backfill: imports accepted name first, then
  the synonym with `accepted_name_usage_id` set. The determination `taxon_id` is the clicked
  name, which may be a synonym.
- Synonyms shown with ✗ / `= Valid Name ✓` HTML in local section; TW results in tinted box
  with `✚ add` badge.


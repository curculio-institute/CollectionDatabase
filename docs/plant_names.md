# Plant name lifecycle

How a plant name gets into the database, what happens to it afterwards, and how the WCVP
index that helps you type it is managed.

Owns the *what/why* of plant names. Dropdown visuals belong in `docs/design.md`; the
schema is `docs/schema.html`. Tracked in issues [#98] (WCVP source) and [#99] (re-check).

[#98]: https://github.com/curculio-institute/CollectionDatabase/issues/98
[#99]: https://github.com/curculio-institute/CollectionDatabase/issues/99

---

## 0. The one-sentence version

WCVP is **a source to import a name quickly** — nothing more. Once a name is in the local
`taxon` table it is *ours*: local edits win, nothing rewrites it, and the database never
claims to follow any release of WCVP.

This is the same relationship the app already has with TaxonWorks, whose version we likewise
do not record. It follows directly from CLAUDE.md §2: **the local DB is the single source of
truth.**

Everything below is a consequence of that sentence.

---

## 1. Where plant names come from

Search order in the taxon widget, unchanged from where POWO sat:

| # | Source | Why it is in this position |
|---|--------|---------------------------|
| 1 | **local `taxon` table** | Already ours. Never re-import a name we hold. |
| 2 | **TaxonWorks** | Primary external source, for consistency with the published mirror. A plant already in TW carries an OTU id; re-importing it from WCVP would duplicate a name the mirror already knows. |
| 3 | **WCVP** | Consulted only when neither knows the name. |

Because TaxonWorks is consulted first, a plant's taxonomic treatment comes from TW when TW
knows it, and from WCVP otherwise. That is deliberate: identity in the mirror matters more
than treatment uniformity, and the treatment is editable locally either way.

### Why WCVP and not another checklist

Four global checklists exist (LCVP, WCVP, WFO, WorldPlants). They disagree on ~300 000 taxon
names, and Schellenberger Costa et al. 2023 (*New Phytologist* 240:1687–1702) — co-authored
by curators of all four — **explicitly decline to recommend one over another.** WCVP is
chosen because:

- it is the backbone **POWO serves**, so plant names imported here previously keep the same
  treatment (this is a repair, not a re-determination);
- it carries **IPNI identifiers**, which that paper endorses as the way to join across
  resources;
- it is not WFO, which in the paper's expert-list test listed **43.1%** of Meliaceae synonyms
  as accepted names.

### Why an offline archive and not an API

There is no usable API. `powo.science.kew.org` and `wcvp.science.kew.org` sit behind a
Cloudflare bot challenge that answers a plain HTTP client with `403` on ~17 of 20 requests
(measured 2026-07-09). It is not client-specific — `pykew`, `kewr` and `taxize` are all
broken by it, because the challenge keys on the request fingerprint. WCVP is distributed as a
static Darwin Core Archive, which is also the access route the paper above cites.

---

## 2. The WCVP folder

`data/wcvp/` holds three files, and explains itself:

| file | what it is |
|------|------------|
| `wcvp_dwca.zip` | Kew's Darwin Core Archive, exactly as downloaded. The primary source. |
| `wcvp.sqlite` | The lookup index built from it. Read-only; the app never writes to it. |
| `README.md` | Generated at install time: release, source, archive SHA-256, citation, licence. |

Keeping the archive costs ~88 MB and buys three things: the folder is self-describing, the
index can be rebuilt with **no network**, and the bytes it came from can be checked against
the recorded hash.

**It is a lookup tool, not data.** Derived, disposable, rebuildable, gitignored. It is *not*
part of your dataset and is **not distributed with the database** — imported names live in
`taxon`, so a recipient of your `.db` gets every plant name you used without the folder.

It lives **inside `data/`** so a data folder is one self-contained bundle and a folder swap
moves everything together. The corollary is that each data folder needs its own — which is
why installing one is a button, not a shell command.

| | |
|---|---|
| Source | `http://sftp.kew.org/pub/data-repositories/WCVP/wcvp_dwca.zip` (~88 MB) |
| Superseded releases | `…/Archive/wcvp_dwca_v<major>.zip` (verified v10–v15) |
| Licence | CC BY 3.0 — attribution required wherever the data is redistributed |
| Contents | 1 448 984 names, ranks Genus and below |
| Build | ~16 s → 258 MB index (+ the ~88 MB archive, kept) |
| Search | 0.1–0.8 ms per keystroke (`name COLLATE NOCASE` index) |

### Getting it

**Settings → Plant names (WCVP) → “Download and install”.** It fetches Kew's archive with a
progress readout naming the URL and the size the server reports and builds the index into the active collection's `data/` folder — no shell,
no file to move. The same button reads “Re-download and rebuild” once an index is present.

The index lives **inside `data/`**, beside the collection it serves, so a data folder is one
self-contained bundle and a folder swap moves everything together. Each data folder therefore
has its own index, and a freshly-swapped folder needs one installing — hence the button.

The command line does the same thing and shares the same code path (`wcvp.install`):

```
python scripts/build_wcvp_index.py                              # download + build
python scripts/build_wcvp_index.py --archive ~/wcvp_dwca.zip    # build from a local file
```

The version is read from the archive's own `eml.xml`, so handing it the wrong file cannot
silently install a different release — the index records what it actually contains. The build
writes to `wcvp.sqlite.building` and atomically replaces the target, so a failed download or a
corrupt archive leaves an existing index untouched.

### Refreshing it

- **No network call at startup.** This is a local-first app; it must launch offline, and
  `db_safety` runs its checkpoint/integrity/snapshot before the UI serves. A hanging HTTP
  request there would block the app on a bad connection.
- A **Settings card** ("Plant names (WCVP)") shows the installed release, read from the
  index's own `meta` table with no network:
  *"WCVP v16.0 (2026-06-04) · 1,448,984 names · CC BY 3.0"*, or offers to install one.
- Its **Check for a new release** button costs **~32 KB, not 85 MB**: `eml.xml` is the first
  entry in the zip (4.9 KB compressed) and Kew's server honours HTTP `Range`, so a ranged
  request returns `206` and yields the version and pubDate (`wcvp.latest_release()`). It
  re-reads the installed index first, so a rebuild while the app is running cannot make it
  report an update that is already installed. If the first zip entry is not `eml.xml` it
  refuses rather than reporting a version read out of the wrong member.
- Rebuilding **replaces the index wholesale and atomically** (build to `wcvp.sqlite.building`,
  then `replace()`). A crash or a bad archive never leaves a half-built index; a rebuild never
  merges into old rows.
- **Refreshing changes nothing about names already imported.** They are local. A new release
  is only a different set of suggestions for names you have not typed yet.

Because superseded releases stay on Kew's server, a rebuild is reversible.

---

## 3. Choosing a name

The dropdown is where the scientific judgement happens, so it shows the evidence needed to
judge, and refuses to make the judgement for you.

**Homonyms are common, not exotic.** Of 18 ordinary host-plant names, 4 have several valid
records; IPNI returns `Chenopodium album` **Bosc. ex Moq.** (Invalid) *before* **L.**
(Accepted). At genus rank, 1 894 names are homonyms — `Torreya` occurs six times, in six
different families.

Therefore:

- **Never resolve a name by taking the first candidate.** Authorship is displayed and the
  user picks. The *New Phytologist* paper makes the same point: author names are "necessary
  for correct assignment", and homonyms have list dependency > 0.5.
- Any **automated** resolver (bulk import, [#39]) must refuse an ambiguous name loudly rather
  than choose.

Each row shows: `🌿` (ICN), the name in italics, the authorship, WCVP's status, and the
family in muted text. Accepted names rank first, then replaced-by-X, then refused.

[#39]: https://github.com/curculio-institute/CollectionDatabase/issues/39

---

## 4. What can be imported, and what is refused

Our model has exactly **two states**: a name is a synonym iff `acceptedNameUsageID` is set,
otherwise it is accepted. There is no third state, and `taxonomicStatus` is **derived from
that column at export** (migration 0030 dropped the stored column so it could not drift).

So the rule is: **import a name only when WCVP's status is representable as _accepted_ or
_replaced by X_. Refuse the rest.** Snapping an unrepresentable status to the nearest state
would publish a false claim to TaxonWorks, and onward to GBIF.

| WCVP status | count | representable as | action |
|---|---|---|---|
| `Accepted` | 434 691 | accepted | import, `acceptedNameUsageID` NULL |
| `Provisionally Accepted` | 3 224 | accepted (tentative) | import, `acceptedNameUsageID` NULL |
| `Synonym` | 880 359 | replaced-by-X | import with a synonym link |
| `Illegitimate` | 48 723 | replaced-by-X | import with a synonym link |
| `Invalid` | 37 317 | replaced-by-X | import with a synonym link |
| `Artificial Hybrid` | 4 391 | replaced-by-X | import with a synonym link |
| `Orthographic` | 2 271 | replaced-by-X | import with a synonym link |
| `Local Biotype` | 1 452 | replaced-by-X | import with a synonym link |
| **`Unplaced`** | **35 347** | **neither** | **refused** |
| **`Misapplied`** | **1 209** | **neither** | **refused** |

**`Unplaced`** — WCVP explicitly declines to say whether the name is accepted or a synonym.
Importing it with a NULL link asserts "accepted", which the source refuses to assert.

**`Misapplied`** — a misapplication is not a synonymy. TaxonWorks declares
`TaxonNameRelationship::Icn::Unaccepting::Misapplication` **disjoint from `Synonym`**;
DwC/GBIF give `misapplied` as a `taxonomicStatus` distinct from `synonym`. A synonym link
would deny what all three sources assert.

For the five importable non-`Synonym` statuses, the *reason* is lost but nothing false is
asserted — they all mean "use that name instead of this one", which is what
`acceptedNameUsageID` says. (TaxonWorks models them as `Homonym`, `OriginallyInvalid`,
`Usage::Misspelling` relationships.)

### Ranks are refused on the same principle

WCVP carries **7 137** otherwise-importable names at ranks this database does not model —
`proles` (2 347), `lusus` (659), `nothosubsp.` (533), `microgène`, `monstr.`, `grex`, and
**2 707 rows with no rank at all**. `SUPPORTED_RANKS` is genus, species, subspecies, variety,
subvariety, form, subform.

Unrepresentable is unrepresentable: refuse the import, show the name, state the rank. Coercing
`Paeonia corallina proles russoi` into a `variety` would assert a rank no authority gave it.

### A synonym whose accepted name is missing

Kew's own data contains dangling `acceptednameusageid` values. The importer refuses such a row
rather than inventing an accepted name or quietly dropping the link.

### Refused names are shown, not hidden

A refusal only teaches something if you can see it. If `Juglans gonroku` simply returned no
result you would conclude the name does not exist and create it by hand — a silent invention,
which is worse.

So refused rows appear in the dropdown, **ranked last, capped at three**, muted, with no
`✚ add` badge (in this widget that badge *means* "clicking imports this"), not clickable, and
each stating its reason without repeating the lie — the synonym form `Name ❌ = Accepted ✓`
asserts *synonym of* and must not be used:

```
🌿 Paeonia officinalis Thunb.   ⊘ misapplied
   in WCVP this name is applied to Paeonia lactiflora

🌿 Juglans gonroku Makino       ⊘ unplaced
   WCVP records no accepted placement for this name
```

This is useful, not merely polite: an old host label reading "on *Paeonia officinalis*" may
not mean what it appears to.

The tooltip states the remedy — the name can be created deliberately in the **taxon editor**,
which is an explicit human assertion rather than a silent import side-effect.

---

## 5. What an import creates

WCVP contains **no rank above Genus**. `family` is a text column with no authorship and no
row of its own. So an import creates at most three rows:

```
Fagaceae            family      no authorship (WCVP has no family rows)
└── Quercus         genus       L.            (a real WCVP row → authorship, IPNI id)
    └── Quercus robur   species L.
```

Every created row gets `nomenclatural_code = "ICN"` — a property of **the source queried**,
not of the payload. WCVP indexes only names governed by the ICN; that is a fact about the
source, not a guess about the row.

### Deriving the lineage

Accepted names chain by identifier; synonyms do not:

| row | `parent_id` | resolution |
|---|---|---|
| subspecies / variety | → the species row | follow the id (exact) |
| accepted species | → the genus row | follow the id (exact) |
| genus | NULL | family from the `family` text column |
| **any synonym** | **NULL** (all 880 359) | genus from the `genus` text column, by name |

The last row is the awkward one. Epic #30 requires a synonym to be parented under **its own**
genus (so *Curculio forticollis*, a synonym of *Otiorhynchus fortis*, stays under *Curculio*),
and WCVP gives synonyms no `parent_id`. The genus must therefore be looked up **by name** —
where 1 894 genus names are homonyms (`Torreya` occurs six times, in six families).

WCVP's `genus` column is the **synonym's own** genus, which is exactly what is needed: it
matches the synonym's own name for 99.998% of synonyms, and the *accepted* name's genus for
only 49.8%. (Kew models synonym lineage the same way we do.) Read the column; never parse the
name — the 19 mismatches are graft-chimaeras written `+ Crataegomespilus`, whose first token
is `+`.

Resolution, in order:

1. Match genus rows on **name + family** (`Torreya` + `Taxaceae`, not `Torreya`), normalising
   a leading `×`/`+` on the genus row's name. Unique for **96.6%** of synonyms.
2. If several match, prefer the single `Accepted` one. Resolves a further **2.2%**.
3. Otherwise — 8 179 ambiguous (e.g. `Ascyrum` L. `Synonym` vs `Ascyrum` Mill. `Illegitimate`:
   same name, same family, different author) and 810 absent (nothogenera like `× Epicattleya`)
   — create the genus row **from the `genus` string with no authorship.**

Step 3 does not refuse and does not guess. The genus *name* is certain; only its authorship is
unknown, and leaving authorship NULL asserts nothing false — whereas picking one of two
authors would. The composed name is identical either way, since composition uses the parent's
`name_element`, not its authorship.

A synonym's genus may itself be a synonym (`Sarothamnus` is), which is correct: own-lineage
parenting is exactly what Epic #30 specifies.

### Why the parent is *not* taken from the accepted name

Tempting, and wrong: `dwc:scientificName` is **composed from the parent chain**
(`compose_scientific_name` walks up to the nearest genus), so the parent *is* the name.
Of 573 637 species-rank synonyms with a resolvable accepted name, **359 744 (62.7%) have a
different genus** from it. Parenting them under the accepted name's genus would:

- **rename 143 738 of them into their own accepted name** — `Cadetia stenocentrum` composes to
  `Dendrobium stenocentrum`, which *is* its accepted name. `get_or_create` matches on
  `(composed scientificName, taxonRank)`, so the synonym row would find the accepted row and
  merge into it: the synonym vanishes and any determination made under it silently becomes a
  determination of the accepted name;
- **fabricate 216 006 names nobody published** — `Cadetia subfalcata` → `Dendrobium subfalcata`
  (the real accepted name is `Dendrobium subfalcatum`; the epithet does not even agree in
  gender, because it was never combined with that genus).

This is why migration 0033 retired `trg_taxon_synonym_parent_matches_accepted` with the
standing instruction **do not re-introduce it**, and why
`test_synonym_integrity.py::test_parent_and_accepted_writes_are_centralised` guards the
writers. The accepted name is recorded by `acceptedNameUsageID`; the tree *displays* synonyms
grouped under it. Neither is the parent.

### Family authorship is not available

WCVP has no family rows, so an imported family row carries a name and a code but no
authorship. Filling it needs a separate static list of family authors — a follow-up, not part
of #98.

### `taxon.ipniID`

WCVP's `scientificnameid` holds the IPNI id (`ipni:304293-2`). The bare id is stored on the
taxon row (migration 0053), recording **which name this is** — identity, like the existing
`taxonworksOtuID`. Unlike a `nameAccordingTo` column it does not become false when the name is
later re-parented by hand, which is why that column was rejected and this one is not.

Named for its source, following `taxonworksOtuID`: a **source-specific** external id is a plain
camelCase local column, not a `dwc:` term. The DwC term `scientificNameID` is deliberately
generic ("an identifier for the nomenclatural details of a scientific name") and could hold a
WFO id just as well; this column only ever holds an IPNI id.

Coverage: 99.1% of accepted names, 99.8% of genera, 98.3% of species; sparse for infraspecific
ranks (`Form` 45.3%). **It can only be captured at import** — names imported without it can
later be matched only by name + authorship.

---

## 6. After the import: the name is yours

Nothing in the app ever rewrites an imported name.

- The name, authorship, rank, parent and accepted link are **copied into `taxon`** and are
  from then on editable in the taxon editor. Local edits win, permanently.
- A **determination freezes the name as used**: `dwc:verbatimIdentification` is written at
  save time (Epic #30, Phase 5). A later change to the taxon — by you, or by a future WCVP —
  can never rewrite what a specimen was identified as.
- **Determinations may target synonyms.** Recording a determination under a name later
  synonymised is valid scientific practice (CLAUDE.md §2). Drift is *information*, not an
  error to correct.
- Refreshing or deleting the WCVP index has **no effect** on any imported name. The index can
  be absent entirely; only the plant search stops working.

---

## 7. Re-checking against WCVP later ([#99])

Optional, manual, read-only — the same shape as the existing `verify_taxon_consistency()`
button. You ask, it reports, you decide. Never automatic, never at startup.

It matters because checklists move. Between the two real releases v15 (Jan 2026) and v16
(Jun 2026), six months apart:

| | |
|---|---|
| names that changed `taxonomicStatus` | 4 341 |
| accepted links that moved | 10 929 |
| taxonids removed / added | 393 / 8 225 |
| shared taxonids carrying the same name | 99.94% |

Examples: `Thapsia foetida` Accepted → Synonym; `Eurya hayatae` Synonym → Accepted.

**Key on identity; compare opinion.** Do *not* look a name up by status or parent — those are
the values under audit. If WCVP flips `Thapsia foetida` to Synonym and we match on
name+status+parent, the lookup *fails*, and the report says "not found in WCVP" instead of
"status changed" — indistinguishable from the 393 genuinely deleted names.

- **Match on:** `ipniID` where present, else `scientificName` + `scientificNameAuthorship`.
- **Compare:** status, accepted link, parent, rank, authorship.
- An **ambiguous match is reported as ambiguous** — WCVP itself contains 1 695 duplicate
  name–author combinations (the paper's Table 1).

The report says what we hold, what the release says, and the consequence. Applying any of it
is a separate, deliberate act.

---

## 8. Distribution

Ship the `.db`. Do not ship the index.

Imported plant names are rows in `taxon`; a recipient gets all of them. The index is a typing
aid for *your* data entry and is rebuildable by anyone from Kew's archive in ~16 s.

The database does **not** claim to follow WCVP v16.0 or any other release, because it does
not: names came from TaxonWorks where TW knew them, from WCVP otherwise, and from your own
editing after that. Per-taxon `dwc:nameAccordingTo` was considered and rejected for exactly
this reason — the claim it would record is not one the dataset makes.

If a name's origin matters for a particular study, `ipniID` (§5) identifies the
IPNI name it was imported from, and the WCVP citation is versioned per release
(v15 `10.34885/rvc3-4d77`, v16 `10.34885/egs6-cp24`).

# Specimen workflows, bulk import, print queue and label editing

> Moved verbatim out of `CLAUDE.md` on 2026-10-07 to keep the always-loaded file compact.
> This file is now the **one home** for the detail below; `CLAUDE.md` keeps only the binding
> rule and a link here. `§n` references are `CLAUDE.md` section numbers: §2 principles and §8
> conventions are in `CLAUDE.md`; §4 → `data_model.md`, `vocabularies_geography.md`,
> `taxonomy.md`; §5 / §5b / §5c → `taxonworks.md`; §6 → `app_structure.md`.

## Specimen workflows

**Invariant across all workflows:** every specimen **in the own (default) collection** must
have an identifier (`catalog_number`); one without must never be committed. Identifiers are
always physical labels pinned with the specimen.

**The one exception — a specimen held in a foreign collection (decided 2026-10-06, migration
0072).** Another collection's specimen may have no catalog number at all, and inventing one
is a false claim about a specimen we do not hold. So `catalog_number` is nullable, **only**
outside the default collection. The rule is DB-enforced, not a form validation:

- `trg_co_default_requires_catalog_number_ins` / `_upd` RAISE on a numberless specimen in the
  default collection — on create **and** on re-homing one into it;
  `trg_repository_default_requires_catalog_numbers` refuses to flag as default a collection
  that holds any. (A CHECK cannot see a second table, hence triggers; a rebuild of either
  table must re-create them — `test_schema_integrity.py` guards it.)
- **NULL is the only spelling of "no number"** (`ck_co_catalog_number_not_blank`). The
  existing `UNIQUE(repository_id, catalogNumber)` is untouched and does the right thing by
  itself: SQLite's NULLs are distinct, so any number of numberless specimens share a
  collection while a filled number stays unique in it. This is deliberately the *opposite*
  of the geography vocab's `IFNULL` index — there NULL means "the one uncoded row", here it
  means "no identity claimed".
- **Fill-once.** A numberless specimen may be given a number later
  (`update_collection_object`); from then on it is immutable like any other.
- **Never exported** — see §5c, rule 6. And nothing can stop the same numberless specimen
  being entered twice: with no handle but its data, two identical ones are
  indistinguishable. That mirrors the physical situation and is accepted.
- **Transfers are unaffected.** A specimen given away keeps its number (only `repository_id`
  moves), and that number can never be handed out again: the next one is derived from
  `label_code` (`identifiers._next_sequential_number`), whose `code` is globally UNIQUE and
  whose row outlives the specimen (`ON DELETE SET NULL`) — independent of who holds it now.
- **Display:** `identifiers.catalog_label` / `format_catalog_display` are the one wording
  ("no number"); a browse surface must not key a row on the catalog number (Batch tools
  re-reads its set by id — `batch_ops.fetch_by_ids`).

### Workflow 1 — Retroactive digitisation (Import & Assign)

Use case: specimens already in the collection have data/identification labels but no
identifier label. A reference table with the relevant data exists, but it may contain
records for specimens no longer in the collection, so it cannot be imported wholesale.

Process:
1. Pre-print a batch of identifier labels (Labels tab → reserve codes → print).
2. Go through the physical collection specimen by specimen.
3. In the Import & Assign tab, search for the specimen's data (by date, locality, taxon,
   etc.) and select the matching row from the reference table.
4. Take one pre-printed identifier label, pin it with the specimen.
5. Select that code from the "generated but unused" identifier dropdown.
6. Save → the complete record (data + identifier) is committed to the DB.

Only specimens physically found in the collection ever get a database record. Records in
the reference table that have no matching physical specimen are simply never selected.

**Collecting-event reuse (decided).** A retroactive-digitisation batch is a long run of
specimens that often share one collecting event, so Import & Assign **reuses an existing
`collecting_event` that is 100% identical** to the row being saved rather than inserting a
duplicate per specimen (`events.get_or_create_exact_event`, passed via
`save_specimen_entry(reuse_event=True)`). "Identical" is exact: every data column must equal
what a new row from these fields *would store* — same geo name→FK resolution, float coercion
and defaults as the insert (one shared normaliser, `_normalize_event_fields`, so create and
match can't drift). An event that additionally carries a coordinate, habitat, or any column
the new fields lack is **not** reused. Erring toward create is safe (at worst a duplicate, the
old behaviour); a false match would silently file a specimen under the wrong locality (§2), so
the comparison is over *all* columns, never just the ones provided. Only Import & Assign opts
in; the other create paths still make a fresh event each save.

### Workflow 2 — New incoming specimen (Digitize tab)

Use case: a fresh specimen arrives and needs to be recorded immediately.

Process:
1. Pre-print identifier labels (can be done in bulk ahead of time).
2. Open the Digitize tab, fill in all fields (collecting event, taxon, sex, count, etc.).
3. Select an unused identifier code from the dropdown.
4. Pin the corresponding label with the specimen and save.

### Workflow 3 — Mounting session (not yet built)

Use case: mounting a batch of specimens that have no labels at all. Need to produce labels
during or immediately after mounting; identifiers may or may not be assigned at this stage.

Key design decisions:
- **Identifier timing:** assign identifiers during mounting (immediate). Identifiers are
  pre-printed in a batch before the mounting session; the user selects one per specimen.
- **Label layout — adjacent sheet layout (decided):** data label and its matching identifier
  label are printed on the same sheet, adjacent in paired rows (data row / identifier row /
  small gap / next pair). Physical proximity on the sheet prevents mix-ups without embedding
  the code in the data label itself.

**Print queue model implication (decided):** the adjacent layout is implemented as a **pair
of separate print queue rows** — one `"data"` row (`collection_object_id`) + one
`"identifier"` row (`label_code_id`) — rather than a combined row type linking both FKs.
The print service interleaves them into the paired sheet layout. This keeps the exclusive-arc
constraint on `print_queue` valid and avoids a special-case label type.

This workflow requires a new UI task (staging tab or modal): the user stages N specimens
that share the same collecting event, sets the specimen count, queues them for printing,
and the print sheet is generated in one go. Identifications are added later via the Records
tab.

### Bulk import — staged wholesale record import (#39, decided)

The **wholesale** ingest path, distinct from the row-by-row **Import & Assign** and modelled
on TaxonWorks' **Import Dataset** (`ImportDataset` / `DatasetRecord`, TW @ `aadf21a`:
`app/models/import_dataset/darwin_core/{occurrences,checklist}.rb`). The whole uploaded file
becomes a **durable, resumable entity** — migration 0064's `import_dataset` +
`import_dataset_record` STRICT tables — not an in-memory parse, so an import can be inspected
before it writes, resumed after a restart, and de-duplicated against what is already imported.

- **Two phases.** *Stage* validates every row and assigns a per-row status — `ready`,
  `blocked` (a reason it cannot import yet), `errored` (a hard failure), `imported` — **writing
  nothing**. *Import* (`import_ready`) creates the records for each `ready` row in a
  time/count-boxed loop with a **resume cursor** on the dataset (TW persists `import_start_id`
  the same way). `retry_errored` re-runs only the failures; `blocker_summary` groups blocked
  rows by distinct reason — the issue's *"surface the ones that can't be resolved"*.
- **Two `kind`s, exactly as TW splits `occurrences.rb` from `checklist.rb`:**
  - **`occurrence` — the primary path (records).** Specimen records that **already carry an
    identifier** (`catalogNumber`, **required** — the "every specimen has an identifier"
    invariant is satisfied *from the data*, not auto-assigned; a row without one is `errored`).
    A **collection column** names the target collection, so **one file can span collections**
    (matched on the repository's full name or code; blank → the home/default via
    `repositories.get_default`). A *named* collection that isn't set up is **`blocked`**, never
    fabricated — its catalog-number format belongs to it (§2). **Dedup is the DB's own
    `UNIQUE(repository_id, catalogNumber)`:** an existing pair is recognised and skipped, never
    duplicated. Each row resolves its `scientificName` via the taxon resolver below, its event
    (`events`) and determination through the **same services the row-by-row Import & Assign save
    uses** — one tested path. Foreign identifiers bind **no** reserved label code
    (`finalize_specimen(code=None)`, the Visiting-mode policy). **Dates:** an eventDate-only file
    parses that column; a **verbatim-only** file (every row of the Käfersammlung export) has its
    `verbatimEventDate` parsed into `eventDate` automatically (`_date_overrides`) — the bulk
    equivalent of Import & Assign's ⚡ button, deterministic, with the verbatim always preserved
    and an unparseable date refused loudly (§2/#1).
  - **`taxon` — a name checklist** (`create_taxon_dataset`), and the internal name-resolution
    engine the occurrence import reuses (`_chain_for_name` → `taxa.get_or_create_from_chain`,
    idempotent → the name dedup). Its nomenclatural code is a dataset-level default (a checklist
    is one code), never guessed; `set_dataset_code` re-stages (the resolve-once seam).
- Removing a dataset keeps everything it imported (local rows now, same rule as a name-source
  dataset); the `taxon_id` / `collection_object_id` FKs on the record are `ON DELETE SET NULL`,
  so deleting the entity forgets the link without deleting the audit record. Service:
  `app/services/bulk_import.py`; UI: `app/ui/bulk_import_tab.py`.

### Print-queue policy by create mode (decided)

Every specimen-create path runs the same finalization tail through one seam,
`app/services/specimens.py::finalize_specimen(session, *, collection_object_id, code,
queue_labels=False, print_group_id=None, source=None, associations=())`: bind the reserved
identifier code, optionally queue labels (tagged with a print group + origin header), and
persist biological associations. Which modes queue labels is a deliberate policy, **not** an
incidental of each tab:

| Mode | `code` | `queue_labels` | Queues to `print_queue` |
|------|--------|----------------|-------------------------|
| **Digitize standard** | reserved code | `False` | **nothing** — identifier is pre-printed in a batch and pinned by hand; the specimen already carries its own data labels. The code is still bound (`assign_code`). |
| **Digitize visiting** ("other collection") | `None` | — | nothing — the collection is picked from the vocabulary, the foreign `catalogNumber` is optional, no reserved code; bio associations still saved. |
| **Import & Assign** (retroactive digitisation) | reserved code | `False` | **nothing** — same as standard: the specimen already has its data + identification labels; only the pre-printed identifier is added. |
| **Mounting** | reserved code | `True` | identifier + data + determination — a freshly mounted specimen needs its whole sheet printed, grouped under a "Mounting Session" header. |

**Rationale:** standard / visiting / import specimens already have physical labels; only
mounting produces fresh specimens that need a printed sheet. (Historical note: Digitize
standard and Import & Assign formerly enqueued data + determination labels — removed
2026-06-12; they now queue nothing.)

**Plain identification labels (decided 2026-10-06; migration 0071).** Besides the four
create modes, the **Labels tab** queues determination labels that belong to **no specimen**
(card "Identification labels": taxon — with the usual import-if-absent — identifiedBy,
dateIdentified, sex, typeStatus, qualifier, and a count), for a series identified at the
bench. Exactly like the identifier labels queued from the card above it, they are print
jobs, **not records**: nothing is written to `taxon_determination`.

- **The queue row carries the content**, because there is no record to derive it from:
  `print_queue.taxon_id` (FK, ON DELETE CASCADE as the DB backstop — `taxa.delete_taxon`
  **refuses** while labels for the name are queued, rather than dropping them silently) + `identified_by_id` (FK → `person`, ON
  DELETE RESTRICT; `merge_persons` re-points it) + `dwc:dateIdentified` / `dwc:typeStatus` /
  `dwc:identificationQualifier` / `dwc:sex`. `ck_print_queue_exclusive_arc` has a third arm
  (a determination row has a specimen **xor** a taxon) and `ck_print_queue_plain_fields`
  keeps those columns NULL on every other row. The name is still read live from the taxon.
- **One row per physical label**, all in one "Identification labels" group, each a
  determination-only column. The print-only override works unchanged: the N copies share
  one auto text, so editing one edits all.
- **One owner of "specimen's determination or plain label":** `print_queue._row_det_label`
  (+ `_is_det_row`, `_det_column_key`). Every reader — sheet, preview, identity, the two
  HTML getters, the override store — goes through it; never branch on
  `row.collection_object` for a determination row again, or a plain label is silently
  skipped. Single write seam: `enqueue_plain_determinations`. UI: `app/ui/plain_det_labels.py`.

**How the queued labels are rendered** (the grouped, column-aligned sheet — groups,
per-specimen columns, archival, the planned reprint path) is a UI/layout concern and is
specified in `docs/design.md` → "Grouped print sheet layout". This table is the authoritative
*policy* (which mode queues what); design.md is authoritative for *layout*.

### Editing labels before printing (decided, #37)

A label is *derived* from the linked records (`_co_to_data_label` / `_co_to_det_label` in
`print_queue.py`), but the user can apply a **print-only override** per row to fit the tiny
physical label — abbreviate text too long, or add what the auto-format omits — **without
changing the record**. The record stays master: substantial corrections are made in Records
(every label has an "open in Records" link) and the derived label updates from them.

- **Persistence:** the override is stored on `print_queue.text_override` (nullable TEXT,
  migration 0034 — added via SQLite `ADD COLUMN`, preserving STRICT/CHECK/FK). Blank or
  equal-to-auto clears it back to the auto-composed text.
- **Editable rows:** data + determination. **Identifier** rows are **read-only** (the
  immutable catalog number is the sync join key).
- **Primary surface:** the interactive **sheet preview** (groups → per-specimen columns →
  data / id / determination boxes) is the main UI; data/det labels are edited inline.
- **Identical labels are linked.** Identity is the rendered *auto* text — for a **data**
  label that means the collecting **event *and* the biological associations** (the label is
  composed from both), for a **determination** the name — hashed by `_ident` /
  `_row_auto_identity`. Editing one label applies the override to **every identical** label
  (`set_override_for_identical`); hovering highlights them. This works across batches /
  distinct event rows (identity is content, not the event id).
- **Determination labels** carry the open-nomenclature qualifier (cf./aff.) and type status.

History: a first cut had the queue edit the *record* directly (`update_collecting_event`,
live determination editor); that was reversed — a label is a physical artifact with size
limits, and forcing record edits there mixed concerns. The print-only override was
deliberately (re-)introduced.

**Formatting-aware editing (decided, #45/#46 — done).** The override is **formatted HTML**,
not plain text, so an edit keeps the scientific name's italics/bold on the printed PDF:

- The preview box is a **`contenteditable` rendered with the real formatted label HTML**
  (genus+species bold-italic, subgenus italic, associated species italic) — so the preview
  resembles the PDF (#46). Editing *inside* a `<strong><em>` token keeps its styling; text
  typed outside stays plain (#45's "knows what was edited", achieved structurally, no
  token-classification logic). The inline box is for quick tweaks.
- **Larger editor dialog** (per-box ⤢ button, `_open_label_dialog`): a readable,
  comfortably-sized WYSIWYG area with a **Bold / Italic toolbar** (select text → click;
  `document.execCommand` with `styleWithCSS=false` so it emits `<b>/<i>`, which the sanitizer
  maps to `<strong>/<em>`) — so a user can add explicit formatting without hand-editing tags.
  The toolbar buttons use a client-side `mousedown.preventDefault` handler so clicking them
  doesn't collapse the editor's text selection. The dialog also has a **raw-HTML source
  toggle** (the escape hatch for long/precise markup, which is unreadable in the tiny inline
  box). The editor's `innerHTML` is read on Save via `ui.run_javascript`; seeded imperatively
  so Vue never re-binds it. Standard Abort / Save & close modal, deleted on close (timer-leak
  rule).
- **Single gatekeeper:** `labels.sanitize_override_html()` reduces arbitrary contenteditable
  HTML to a tiny safe subset (`<div>` lines of `<em>`/`<strong>`; `<b>`→`<strong>`,
  `<i>`→`<em>`; all attributes dropped) — applied both on store and on render.
  `labels._override_html` keeps the legacy plaintext path for old overrides (detected by the
  absence of tags). `labels.label_auto_html()` composes the formatted auto HTML used to seed
  the editor and to detect "edited == auto → clear".
- **Capture seam:** a contenteditable's `innerHTML` cannot ride NiceGUI's event args (the DOM
  node is stripped before serialisation), so a **global capture-phase `blur` listener** reads
  `innerHTML` client-side and emits `pq_edit {qid, html}`; the server recomputes the row's
  auto text (`pq_svc.row_auto_html`) to decide store-vs-clear. This also sidesteps a
  `v-html`-bound contenteditable being clobbered mid-edit by an unrelated Vue patch.

**One canonical form, stored not sniffed (#67, 2026-07-12).** An override is **sanitised on the
way in** (`labels.canonical_override`, called by `set_override_for_identical`), so the DB holds
exactly one form and rendering is a pass-through — *what the preview showed is what prints*.
Four defects came from getting this wrong, and each is a rule now:

| rule | what went wrong without it |
|---|---|
| **An override that renders to nothing is `None`, not an empty override.** `''`, whitespace, `<div></div>` all clear it → the auto text. | `text_override is not None` treated `''` as real, so the label printed **blank**. A blank label pinned to a specimen is a curation error; the record still holds the data, so falling back to auto is always the honest answer. |
| **A tag we do not emit is text, not markup.** `_looks_like_html` matches only `div/p/br/b/i/em/strong`; the sanitizer emits any *unknown* tag escaped. | `<\w+` took `Quercus <robur>` for HTML and **silently deleted** `<robur>` from the label. |
| **Input from the editor is always HTML — sanitise it, never sniff it.** (`_override_html` still sniffs, but only to render *legacy* plaintext overrides.) | innerHTML is already entity-encoded: `R & D` arrives as `R &amp; D`, looked tagless, was escaped a *second* time, and printed as the literal `R &amp; D`. |
| **A row with no auto text has no identity — editable alone, never grouped.** | Identity is "same auto text". A determination label on a specimen with **no current ID** has none: the preview hashed the `—` placeholder (so it grouped them and offered an edit) while the store returned `0` and **dropped the edit silently**; `_co_to_det_label` then returned `None`, so even a stored override never reached the paper. Grouping them instead would be worse — they share only their *emptiness*, so one hand-written name would be stamped onto every unidentified specimen in the queue. |

`canonical_override` is **idempotent** — the stored form is fed back through the editor on the
next edit, so it must be a fixed point.

Layout/rendering detail is design.md's concern; this section owns the *policy*.

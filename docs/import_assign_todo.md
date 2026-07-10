# Import & Assign — importer gaps (to-do)
see also: /home/jakobj/Data/Sammlung/DarwinCoreTransfer
Context: preparing a real ~1,400-specimen beetle spreadsheet (`Käfersammlung.ods`) for the
Import & Assign workflow surfaced a set of importer limitations. This is the fix list, ordered
by severity. File:line anchors were current at time of writing — re-grep before editing.

Key files:
- `app/services/dwc_import.py` — CSV parse, `_DWC_TERMS`, `row_to_*` mappers
- `app/ui/import_assign.py` — the tab: `_resolve_taxon`, `_validate`, `_on_assign`
- `app/services/events.py` — `create_collecting_event`
- `app/services/specimens.py` — `save_specimen_entry`, `finalize_specimen`
- `app/services/taxa.py` — `find_taxon_by_name`
- `app/services/dates.py` — `parse_dwc_date` (exists, unused by this path)

---

## P0 — silent data corruption

### 1. eventDate is stored verbatim, never normalised — DONE
`_on_assign` passes `event_date` straight into `create_collecting_event`, which stores strings
as-is (`events.py:247-249`). `parse_dwc_date` is only wired into `ui/date_input.py` and
`ui/mounting_session.py`, never this path. Result: `15.07.2005` lands in `dwc:eventDate`
literally (1,226 of 1,412 real-world rows), no warning.

**Fixed.** `dwc_import.normalise_row_dates(row)` parses eventDate + dateIdentified via
`parse_dwc_date` (now also month names + roman numerals, #95). `_validate` refuses a bad date
(like a bad latitude); the save path stores the ISO result and keeps the raw eventDate in
`verbatimEventDate`. Ambiguity (2-digit year, slash day/month) is refused, not guessed; the
DD.MM European order is assumed to match the data, with the raw preserved so a misread is
auditable. Covered by test_import_dates.py (incl. an integration test through the real events
service) + 50 date tests. Pre-flight batch validation (#8) can reuse `normalise_row_dates`.

### 2. Two-token fallback silently downgrades a subspecies to its species — DONE
`find_taxon_by_name` (`taxa.py:320-331`) retries a failed exact match on the first two tokens to
strip trailing authorship. It cannot distinguish authorship from a trinomial epithet, so
`Carabus baudii fenestrellanus` resolves to the **species** `Carabus baudii` and the UI reports
"resolved locally" with a green check (`import_assign.py:271`). ~96 trinomials in the real data.

**Fixed** by the data contract the user chose: authorship is separate (DwC `scientificName` is
the name only; `scientificNameAuthorship` holds the author — which our DB already models).
`find_taxon_by_name` now does an **exact match only**, no token-stripping heuristic; the crutch
is gone, so no name can be silently downgraded. `taxa.scientific_name_has_authorship()` detects a
name that still carries authorship (a token past the epithets that isn't a lowercase epithet), and
the import resolve shows an actionable "move it to scientificNameAuthorship" hint instead of a
mysterious local miss. Clean trinomials still resolve exactly; `Sitona lineatus Linnaeus` no
longer auto-matches locally (it goes to TW / manual, which is the point of the contract).

---

## P1 — blocks or breaks visible rows

### 3. No identificationQualifier column
`identificationQualifier` is not in `_DWC_TERMS`; `_on_assign` hardcodes
`"identification_qualifier": None` (`import_assign.py:519`). Model column exists
(`models/taxon_determination.py:30`) and `render_identification()` already inserts the qualifier
after the genus group. Pure plumbing.

- Add `identificationQualifier` to `_DWC_TERMS`, read it in `row_to_determination_fields`, pass
  it through `_on_assign`.
- Constrain to the open-nomenclature set (`cf.`, `aff.`, `sp.`, `?`, …) or validate softly.
- ~23 rows depend on this (`Trechus cf. quadristriatus`, `Orinocarabus indet.`).

### 4. individualCount not hardened against non-numeric / zero
`_select_row` runs `int(sp["individual_count"] or 1)` (`import_assign.py:242`). A value like `F`
raises inside the value-change callback → the form silently fails to populate. And
`int(count_in.value or 1)` in `_on_assign` turns an explicit `0` into `1`.

- Parse defensively in `row_to_specimen_prefill` (non-int → `1` or flag as a bad row).
- Distinguish `0`/empty intentionally.

### 5. Coordinate pair validated per-axis
`_validate` (`import_assign.py:437-447`) checks latitude and longitude independently, so a
latitude with an empty longitude saves half a georeference.

- Reject one-without-the-other.
- Warn when a coordinate has no `coordinateUncertaintyInMeters` (currently blank passes silently;
  it's the one thing point-radius exists to prevent).

### 6. Host plants / biological associations can't be imported
No `associatedTaxa` term; `_on_assign` never passes the `associations=` arg that
`finalize_specimen` already accepts (`specimens.py`). ~82 real rows carry host plants.

- Add an `associatedTaxa` (or similar) term, resolve to taxon + relationship, pass through
  `finalize_specimen(..., associations=...)`.
- Depends on the biological-relationship vocab being loaded.

---

## P2 — scale / correctness at volume

### 7. Every row creates a new collecting event
`_on_assign` always passes `event_id=None` (`import_assign.py:503`), so `save_specimen_entry`
calls `create_collecting_event` every time. N specimens sharing one collecting event → N
duplicate `collecting_event` rows.

- Dedup on identical event fields within the loaded file, or key reuse on `fieldNumber`/`eventID`.
- `save_specimen_entry` already accepts `event_id`; `ui/event_reuse.py` exists for the
  interactive flows — reuse that machinery.

### 8. No dry run / pre-flight validation
Bad rows are discovered one at a time, mid-loop. `collect_import_preview`
(`services/import_preview.py:77`) already does a savepoint-rollback dry run for taxonomy.

- Add a "validate all rows" pass after upload: dates, coordinates, individualCount,
  countryCode length, unresolved taxa — report as a list before the assign loop starts.

### 9. Taxon resolution is per-row, not per-name
`find_taxon_by_name` compares strings with no cache, and only matches accepted names
(`taxa.py:312`). A species whose stored name carries a subgenus, or any locally-present synonym,
hits the TaxonWorks API once **per specimen**.

- Pre-resolve the distinct `scientificName` set once after upload; cache name→taxon_id for the
  session.

---

## P3 — usability / cleanup

### 10. `family` advertised but unread
In the downloadable example CSV (`import_assign.py:45`) but not in `_DWC_TERMS`.

- Either read it (helps homonym disambiguation and pre-creating parents of non-Curculionoidea
  taxa), or drop it from the example to avoid implying support.

### 11. No taxon source for beetles outside Curculionoidea
TaxonWorks weevil catalogue covers ~456 of 946 real names; the other ~490 fall through to the
3-dialog manual-add path. Not a bug, but the single biggest friction in a mixed-family import.

- Consider a GBIF / Catalogue of Life fallback in `_resolve_taxon` after the TaxonWorks miss,
  before manual add.

### 12. Date parsing assumes European order — no American support (deferred)
`parse_dwc_date` commits to **European DD.MM** (correct: the current data is all European, and
`04.07.2005` is genuinely ambiguous — 7 Apr vs 4 Jul — so a value alone cannot decide). Today:
American slash-dates (`07/15/2005`) are **refused** (safe, caught loudly), but a dot-separated
American date with both parts ≤ 12 (`07.04.2005` meaning 4 July) is **silently read as European**
(7 Apr). The raw is kept in `verbatimEventDate`, so a misread is auditable.

**We will need to deal with this later** when non-European data appears. The fix is not
auto-detection (impossible for the ambiguous cases) but an **explicit per-file format toggle** on
the import — "dates are: European / American / ISO" — that the user declares and the parser obeys.
Until then, any American-sourced file must be pre-converted to European or ISO.

---

## Suggested order
1 and 2 first (silent corruption). Then 3, 4, 5, 6 (visible breakage). Then 7–9 (volume).
10–11 last. #8 (pre-flight) naturally absorbs the validation added in 1/4/5 — consider building
it early and hanging the row-level checks off it.

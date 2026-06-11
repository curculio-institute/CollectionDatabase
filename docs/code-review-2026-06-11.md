# Code review — 2026-06-11

Scope: `git diff origin/main...HEAD` (merge-base `627fcfa`) — the specimen-form
extraction, Visiting Collection mode, vocab centralisation, gifting (editable
`collectionCode`), mounting session, and the segmented mode toggle.

Method: 7 finder angles (3 correctness, 3 cleanup, 1 altitude) → dedup → per-candidate
verification against the code. Findings ranked most-severe first. Tick as fixed.

Refuted during verification (not listed below): visiting-mode "silent duplicate
breaks TaxonWorks sync" — a `UNIQUE(collection_code, catalog_number)` constraint
(migration 0005) makes collisions a caught `IntegrityError`, not silent duplication;
`labels.py` blank-number split — the no-hyphen case is handled and codes cannot end
in `-`.

---

## Correctness

- [ ] **CR-1 — `app/ui/mounting_session.py:298` — mounting `_validate()` skips
  countryCode/coordinate checks.**
  It validates only the config codes and that each row has an identification. The
  standard Digitize `_validate()` also checks `len(countryCode) == 2`, latitude/longitude
  bounds, and `uncertainty >= 0`.
  *Failure:* a malformed shared collecting event (e.g. 3-char countryCode, out-of-range
  latitude) passes validation; `_do_save` reserves codes and calls `save_specimen_entry`
  in one transaction; a DB CHECK then fails with a cryptic "Save failed: CHECK constraint
  failed" and rolls back the whole batch, instead of a friendly up-front message.
  *Fix:* factor the standard path's event/coordinate validation into a shared helper and
  call it from mounting `_validate()` before reserving codes.

- [ ] **CR-2 — `app/ui/mounting_session.py:139` — `dateIdentified` persisted without
  save-time normalisation.**
  `attach_date_validation` normalises only on the input's `blur` event (an async
  round-trip); `_do_apply` reads `date_in.value` and writes it verbatim to
  `TaxonDetermination.date_identified`.
  *Failure:* user types `2024-6-5` or `15.06.2024` and clicks Apply before blur completes;
  a non-ISO / unpadded date lands in a DwC date column, unlike every other date path which
  is guaranteed ISO via `parse_dwc_date`.
  *Fix:* run `parse_dwc_date(date_in.value, no_future=True)` inside `_do_apply` and reject /
  normalise before storing.

- [ ] **CR-3 — `app/services/identifiers.py:174` — sequential-number overflow past 99999.**
  `_next_sequential_number` only counts suffixes where `len == 5`, but
  `reserve_sequential_codes` formats with `:05d` (no truncation), so code `100000` has a
  6-digit suffix the scan ignores.
  *Failure:* after 99,999 codes for a `collection_code`, `max_num` falls back to 99999 and
  returns 100000 again on the next reservation; assigning the duplicate to a second specimen
  fails the `UNIQUE(collection_code, catalog_number)` constraint. Deterministic but distant
  (~100k specimens).
  *Fix:* accept suffixes `>= 5` digits (and parse the longest numeric run), or store the
  sequence width explicitly; consider a `SELECT MAX(...)` instead of a Python scan (see EFF-1).

## Efficiency

- [ ] **CR/EFF-1 — `app/ui/specimen_form.py:130` & `:169` — hidden standard form keeps
  polling the DB.**
  The standard policy's two `ui.timer(2.0, ...)` callbacks (a `reserved_codes` SELECT at
  L130 and a `get_config()` read at L169) are not gated on card visibility, so they keep
  firing while the standard card is hidden in Mounting / Visiting mode.
  *Cost:* ~1800 wasted `reserved_codes` queries/hour (growing with the code table) plus a
  config read every 2 s, for a form the user cannot see.
  *Fix:* gate the timers on `card.visible`, or stop them when the card is hidden.

- [ ] **EFF-2 — `app/services/identifiers.py:170` — `_next_sequential_number` is an O(n)
  full scan.**
  Loads every `LabelCode` matching `collection_code-%` into Python and maxes in a loop.
  *Cost:* grows unbounded with code history; a multi-hundred-ms hitch on each mounting save
  once the collection has tens of thousands of codes.
  *Fix:* `ORDER BY code DESC LIMIT 1` (restricted to numeric 5-digit suffixes) or an indexed
  `MAX`. Couple this with the CR-3 fix.

## Cleanup

- [ ] **CL-1 — `app/ui/mounting_session.py:38` — create-defaults duplicated and already
  diverging.**
  `_empty_row` (`preparations='pinned'`, `life_stage='adult'`) and `_do_save`
  (`disposition='in collection'`, `basis='PreservedSpecimen'`) duplicate the seed defaults in
  `specimen_form.build_specimen_form` — and disagree: `specimen_form` seeds
  `preparations=''`, mounting seeds `'pinned'`.
  *Cost:* a new specimen's default `preparations` depends on which tab created it; future
  changes must touch both. Move to one shared seed (e.g. in `vocab.py` or `specimen_form`).

- [ ] **CL-2 — `app/ui/main.py:540` — `_ms_active` is write-only dead state.**
  Assigned at L540 and in `_on_mode_toggle` but never read; mode decisions use local
  `is_ms`/`is_visiting`/`is_standard`.
  *Cost:* a maintainer may treat it as the live "in mounting mode" flag and read a stale
  `False`. Delete it.

- [ ] **CL-3 — `app/services/specimens.py:135` — silent skip of a blank `collection_code`.**
  `update_collection_object` skips an empty `collection_code` (NOT NULL guard) with no
  signal.
  *Cost:* a caller intending to clear it gets a silent no-op edit (mild — `records_tab._save`
  also guards it today). Consider raising instead of silently skipping.

## Altitude

- [ ] **ALT-1 — `app/ui/main.py:1598` (and `:1474`) — hardcoded wipe tuple drifts.**
  `_on_mode_toggle` (and `_clear_after_save`) wipe collecting-event state via a ~20-widget
  tuple that parallels the field declarations with no link.
  *Cost:* every new collecting-event field must be added to both tuples or it silently leaks
  across a mode switch into the next saved record — the exact bug the "full wipe" comment
  guards against. Drive the clear off the field list or a form-level clear.

- [ ] **ALT-2 — `app/ui/mounting_session.py:313` — third independent save orchestration.**
  `_do_save` re-implements reserve → `save_specimen_entry` → `assign_code` → enqueue
  (data/determination/identifier) → bio-association, overlapping Digitize `_on_save` and the
  Records edit path; no shared "save specimen" seam.
  *Cost:* any change to the create contract must be hand-applied in three places. They already
  diverge (mounting enqueues identifier labels, Digitize standard does not — intentional, but
  undocumented). Factor the assign+enqueue+bio block into one service helper.

- [ ] **ALT-3 — `app/ui/specimen_form.py:181` — the `identifier_policy` abstraction leaks.**
  `get_identity()` returns `institution_code=''` as a not-applicable sentinel for edit mode;
  `cat_num` is `None`/select/input by policy; `reset()`/`refresh_codes()`/`get_identity` each
  carry per-policy guards.
  *Cost:* a caller trusting `get_identity()['institution_code']`, if pointed at an edit form,
  would validate/save an empty value with no error (`''` indistinguishable from a real empty).
  Adding a policy means editing `get_identity`, `reset`, `refresh_codes`, and the handle dict
  in lockstep.

---

### Suggested order

Start with **CR-1, CR-2, CL-1** — small, high-value corrections in the new
`mounting_session.py`. **CR/EFF-1** is a one-line visibility gate. The altitude items
(ALT-1/2/3) are larger refactors worth doing before more modes/tabs are added.

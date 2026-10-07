# Controlled vocabularies, geography identity and geocoding

> Moved verbatim out of `CLAUDE.md` on 2026-10-07 to keep the always-loaded file compact.
> This file is now the **one home** for the detail below; `CLAUDE.md` keeps only the binding
> rule and a link here. `§n` references are `CLAUDE.md` section numbers: §2 principles and §8
> conventions are in `CLAUDE.md`; §4 → `data_model.md`, `vocabularies_geography.md`,
> `taxonomy.md`; §5 / §5b / §5c → `taxonworks.md`; §6 → `app_structure.md`.

### Controlled vocabularies (single-name; the generic pattern, decided)

Some fields are **controlled vocabularies referenced by FK**, not free text, so they can be
**edited and merged like persons** (rename once, re-point everywhere; fold a typo into the
canonical value). Current single-name vocabularies (more may follow — built once, reused):

| Field | Column | Table | Migration | Note |
|-------|--------|-------|-----------|------|
| preparations | `collection_object.preparation_id` | `preparation` | 0039 | was `dwc:preparations` TEXT |
| disposition | `collection_object.disposition_id` | `disposition` | 0048 | was `dwc:disposition` TEXT + a **closed** CHECK; opened up (#76) so holdings like "loaned to Jeffrey" are recordable. **Seeded** with the former six values. DwC `[Not mapped]` by TW |
| habitat | `collecting_event.habitat_id` | `habitat` | 0040 | was `dwc:habitat` TEXT |
| samplingProtocol | `collecting_event.sampling_protocol_id` | `sampling_protocol` | 0040 | was a hardcoded UI list; table **seeded** with the curated set |
| country | `collecting_event.country_id` | `country` | 0041, **0056/0057** | English name (OSM `name:en`); **`iso_code`** = ISO 3166-1 (`DE`). `dwc:countryCode` is **derived from it at export/label time**, not stored (0057) |
| stateProvince | `collecting_event.state_province_id` | `state_province` | 0041, **0055/0056** | English name (OSM `name:en`); **`iso_code`** = ISO 3166-2 (`DE-BY`) |
| administrative region | `collecting_event.administrative_region_id` | `administrative_region` | 0041 | Regierungsbezirk tier; **no DwC term** (local field); local name |
| county | `collecting_event.county_id` | `county` | 0041 | local name (Landkreis) |
| island | `collecting_event.island_id` | `island` | 0041 | local name |

**Which fields qualify:** *open, user-coined* vocabularies where consistency tooling (merge)
helps. **Closed standard vocabularies stay fixed CHECK-constrained lists, NOT editable tables**
— `sex`, `basisOfRecord`, `identificationQualifier`, `license` (editability would create
`Male`/`male` duplicates or values TW rejects). **`disposition` was on this closed list but
moved to an editable vocab (#76, migration 0048):** it is genuinely user-coined (the user needs
arbitrary holdings like "loaned to Jeffrey"), and DwC `disposition` is `[Not mapped]` by TW, so
freeform values never reach TaxonWorks — the duplicate/reject risk that keeps the others closed
doesn't apply.

**A geography vocab row is identified by `(name, iso_code)`, never by name (decided 2026-07-10;
migration 0056).** A subdivision *name* does not identify a subdivision: of the 5,420 ISO 3166-2
subdivisions, **40 names are shared across different countries** (checked against Wikidata `P300`)
— `Limburg` = `BE-VLI` + `NL-LI`, `Punjab` = `IN-PB` + `PK-PB`, `Central Province` = six countries.
Under `UNIQUE(name)` a Dutch-Limburg specimen either silently reused the Belgian row or, with the
short-lived fill-once ISO stamp, **refused to save**. Both are wrong.

The rule, applied identically to `country` (ISO 3166-1) and `state_province` (ISO 3166-2):

> **exact match on `(name, iso_code)` → reuse; anything else → create a new row.**

**No existing row is ever mutated to carry a code it did not have, and no save is ever refused.**
A hand-typed `Limburg` must not be silently declared Dutch by a later geocode; it stays an uncoded
row, and the user folds it with the **merge tool** if the two really are the same place. Duplicates
are the *expected*, cheap outcome — merge exists precisely for this.

**Two asymmetric inputs refine "anything else → create" — the strict rule applies only where the
name is actually ambiguous:**

- **Name, no code.** A CSV `country` column with no `countryCode` said "Austria"; the strict
  `(name, code)` rule answered with a *second*, uncoded Austria beside the geocoded `Austria (AT)`
  — a duplicate on every import, for a name nothing was ambiguous about. So an uncoded input
  **reuses the single row bearing that name when there is exactly one** (whether that row is coded
  or not); it creates its own row only when two or more rows already share the name (`Limburg`
  BE-VLI / NL-LI), where "which one?" is a real question and guessing would be the silent wrong
  value. Nothing is mutated either way. (`Vocabulary.get_or_create`.)
- **Code, no name.** A `countryCode` on a row whose `country` column is blank must not be dropped.
  ISO 3166-1 alpha-2 is unambiguous, so the country name is **derived from the code** (pycountry —
  `events._country_name_from_iso`) and then resolved by `(name, code)`, materialising a properly
  named, coded row rather than a nameless one (`country.name` is NOT NULL — a code-only row is not
  representable). **Country only:** ISO 3166-2 subdivision names are never derived, because the 40
  shared names are the reason the strict identity exists in the first place.

`UNIQUE(name)` is replaced by a unique index on **`(name, IFNULL(iso_code, ''))`**. The `IFNULL` is
load-bearing: SQLite treats `NULL != NULL`, so a plain `UNIQUE(name, iso_code)` would let every
hand-typed save create yet another uncoded duplicate. Result: exactly one uncoded row per name,
plus one row per distinct code. `Vocabulary(code_attr=…)` implements the match; `display_label()`
renders `Limburg (NL-LI)` where a bare name would be ambiguous. Existing rows keep `iso_code =
NULL` and are **not** back-filled — 40 names are ambiguous, so a name→code backfill would be a guess.

**The label's "Country, State:" prefix collapses exactly one side (decided 2026-07-10).** An
18x7 mm label has no room for `Germany, Baden-Württemberg`, but `DE, BW` is a cipher — so **one
of the two always stays written out**, and the *longer* name gives way
(`label_text.format_geo_prefix`, used by both the PDF and the previews):

| country / state | prints | why |
|---|---|---|
| Germany / Bavaria | `Germany, Bavaria:` | both short |
| Germany / Baden-Württemberg | `Germany, BW:` | the state is longer → its subdivision suffix |
| Greece / Peloponnese Region | `GR, Peloponnese Region:` | `GR-J` → suffix `J` is useless → the **country** collapses |
| Sri Lanka / Central Province | `LK, Central Province:` | `LK-2` → a bare digit is useless |
| France / Corsica Region | `FR, Corsica Region:` | `FR-2A` → a letter-digit code is not an abbreviation |
| Germany / Baden-Württemberg (uncoded row) | `DE, Baden-Württemberg:` | no state code to collapse to |

The state collapses to the **suffix** (`BY`), unambiguous precisely because the country stands
beside it — but only when that suffix reads as an abbreviation: **≥2 characters, all letters**.
Measured against all 5,351 ISO 3166-2 codes, that refuses 18 mixed letter-digit codes (`FR-2A`,
`NP-P2`, `CZ-20A`, `GR-A1`, `NL-BQ1`) on top of the 353 single-character and ~49% numeric ones. A row with **no ISO code**
cannot collapse: its name stays and the label grows rather than losing the locality. With no state,
the old long-country rule (`format_country`) still applies, so a lone `United Kingdom` prints `GB`.

**`dwc:countryCode` is not stored (0057).** Once the country row carries `iso_code`, an event
column holding the same fact is a second source that drifts — nothing tied them, so `country =
Germany` with `countryCode = FR` saved happily. Same rule, same reason as `dwc:taxonomicStatus`
(0030): **derive it at export, never store it.** `label_text.format_country()` reads
`country_obj.iso_code`; the DwC-CSV importer's `countryCode` now *resolves the country row* by
`(name, code)` instead of populating a column. The migration first folds each event's code onto
its country row (only where the events agree on one code) so nothing is lost.

Below the first-order subdivision **there is no ISO code**, so `county` / `island` /
`administrative_region` keep `UNIQUE(name)` and remain wrong in the same way, with no honest fix.
`municipality` is free text and stays so — *decided*: a vocab there would invite merging
`Biberbach` into `Biberach`, two real Bavarian places, and the geocoder's `Municipality of Tripoli`
vs `Tripoli` is a naming problem, not a merge problem.

**Geography is the exception that proves the rule (revised, #40):** the administrative levels
*are* controlled vocabs — even though they come from geocoding, the geocoder yields
language/spelling variants (`Deutschland` vs `Germany`) and the faceted Explore search demands
consistency, so they're FK vocabs with merge. `municipality` + `locality` stay free text
(too specific; municipality search is "the map's job"). **Resolution lives in the events
service** (`_resolve_geo_fields` in `events.py` maps the name keys → `*_id` via get_or_create
in `create`/`update_collecting_event`), so the event form keeps its geocode-input widgets +
boundary-warning UI **unchanged** — only the service + the model-read sites resolve by FK.
Language policy: country + stateProvince in English (Photon `lang=en`), the rest local.

**Geocoding: containment vs proximity (decided 2026-07-10; `collecting_event_form._reverse_geocode`, #40).**
The two services answer **different questions**, and the administrative hierarchy must come from
the one that answers *containment*:

- **Overpass `is_in` owns the hierarchy.** It returns the polygons that actually *contain* the
  point. The **state is identified by its `ISO3166-2` tag**, never by `admin_level` — the level
  differs by country (`DE-BY` sits at L4, `GR-J` at L5), so a positional rule is wrong. The
  country relation carries `ISO3166-1`, which supplies `dwc:countryCode` from the data (see
  "No hardcoded country codes"). English names come from `name:en`, present on country/state
  everywhere tested; the lower tiers have none and are stored with their local `name`, which is
  the language policy anyway. `administrative_region` (Regierungsbezirk) is an L5 that is *not*
  itself the ISO state.
- **Photon owns locality candidates only.** `/reverse` is a **proximity search** with an implicit
  radius (~1 km; the `radius` param has a low ceiling — 3 km and 10 km returned the same 1.15 km
  of results). Its `city`/`county`/`state` describe the **nearest feature**, not the query point,
  so they are wrong whenever that feature lies across a boundary (measured: at 47.68,11.12 Photon
  reports `Seehausen am Staffelsee`; the point is inside `Uffing am Staffelsee`). Never read the
  hierarchy from Photon. Candidates must be **distance-filtered against the uncertainty circle**
  using the geometry Photon returns (it carries no distance field).
- Photon *does* return wetlands, streams, peaks and `place=locality` — the earlier claim that it
  yields "only buildings/streets" was false — so the Overpass `around:` block that re-fetched them
  is redundant. (It uniquely supplies a peak's `ele`.) Where nothing is within its radius it
  returns **zero features** (open sea, steppe), which must not blank the form: Overpass still
  answers, and Photon's hierarchy stays only as an explicit degraded fallback when Overpass fails.

**What counts as a locality — allow by KEY, exclude by VALUE (decided 2026-07-12).** Two
questions, deliberately answered by two different mechanisms in `collecting_event_form.py`:

1. **May a named feature be *shown* at all?** → `_locality_eligible()` — an **allowlist of keys**
   (`natural` `place` `water` `waterway` `leisure` `boundary` `landuse` `geological`) with a
   **blocklist of values inside them**. Every value under an allowed key is eligible unless
   explicitly excluded, so OSM's long tail (`natural=blowhole`, `arch`, `place=archipelago`) and
   anything OSM adds later come along **for free** — an allowlist of *values* drops them silently.
   Every other key (`shop` `amenity` `building` `highway` `railway` `tourism` `man_made` …) is not
   a place at all, so a **new key can never surprise us with a supermarket**. The exclusions inside
   the allowed keys are short and stable: individual **plants** (`natural=tree`/`tree_row`/
   `tree_group`/`tree_stump`/`shrub` — `natural=tree` alone is ~34 M objects, the biggest single
   noise source), `natural=coastline` (a line, not a place), the **administrative** `place=*`
   values (they already have their own fields), and `landuse` outside `forest`/`wood`/`meadow`
   (a named landuse polygon carries the toponym of the wood or meadow itself — *Bodener Wald*,
   *Neuhüttenwiese* — but the rest of the key is settlement and sports).
2. **May it be filled in *automatically*?** → `_LOCALITY_KV`, an allowlist of `(key, value)` with a
   **priority**. Deciding which name silently lands in the record is exactly the *silent wrong
   value* of §2, so an **eligible-but-unranked** tag is **offered in the menu and never
   auto-filled** — the field stays empty until the user picks it. Nothing useful is dropped;
   nothing unvetted is written.

**Selection: highest priority wins; distance only breaks ties** — then the enclosing Overpass area
as a fallback (`_apply_locality`). And **filter before capping**: the code cut to the nearest
`_MAX_LOCALITY_POINTS` *before* applying the allowlist, so noise could evict the real locality from
the candidate budget entirely. Measured at the **Flugplatzheide** (48.3255, 10.8995) — a protected
heath tagged `natural=heath` **and** `boundary=protected_area` — Photon returns **29 named
features**, of which **ten are `building=dormitory`** (C8, C9, B3 …) and four are
`information=board` panels repeating the heath's own name. A named heath 200 m away with a dorm
between it and the point would have vanished from *both* the menu and the auto-fill, in silence.
After the fix: 29 features → **1 eligible** → auto-fills *Flugplatzheide*. (A dual-tagged object is
why both of its tags must be eligible: Photon indexes it under only one of them.)

**Language policy is per tier, and `name:en` is not the default (fixed 2026-07-12).** `_admin_name`
preferred `name:en` for *every* tier, so an Augsburg lookup filed the Regierungsbezirk as **Swabia**
instead of **Schwaben** — a tier with no English exonym any German label would use. Only **country
and stateProvince** are English (`english=True`); admin. region / county / municipality / island are
the **local** name, by policy and not merely because OSM often lacks a `name:en` for them.

**One Overpass request per lookup — never two in parallel (measured 2026-07-10; dead end, do
not retry).** The hierarchy and the enclosing-areas blocks look separable (`is_in` admin ≈ 2–24 s,
areas ≈ 1.2 s), and splitting them into two concurrent requests to fill the island/locality fields
sooner is the obvious move. It is **8× slower and fails outright** — the public instance grants an
IP only a couple of concurrent slots, so the second request queues behind the first, times out, and
the retry backoff compounds. Six runs, alternating, 20 s apart:

| shape | median | max | succeeded |
|---|---|---|---|
| one combined query | **3.72 s** | 13.75 s | 6/6 |
| two parallel queries | 29.19 s | 37.18 s | **0/6** |

`is_in` is evaluated once and both blocks pivot off it, so bundling is cheaper server-side too.
The failure is *silent-ish* in the UI: a failed admin query drops into the degraded Photon
fallback, which sets `administrative_region = ""` (Photon has no Regierungsbezirk), so the symptom
is a mysteriously empty admin-region field, not an error.

**Progressive fill (decided).** Photon (~0.15 s) and Overpass (~3.7 s median) each write **their
own** fields the moment they land — never one `gather` applied after the slowest returns, which
made a locality known at 150 ms wait on a 24 s containment query. Each geocode-owned field carries
its own spinner; the button reads "Detecting…" and disables. **All geocode-owned fields are blanked
before a lookup starts**: with sources landing separately the previous point's values would
otherwise sit in the form during the new lookup, and a source that finds nothing (Photon returns
zero features at 26.015/101.883) would leave them there permanently. Empty + spinner = being looked
up; empty after = nothing there. A locality carried over from another specimen's coordinates is the
"silent wrong value" of §2.

**Nominatim is not used** (rejected for per-IP rate limiting after 429s from firing on every pin
drag; the manual Lookup button removed that condition, but the `ISO3166-2` tag makes its only
remaining advantage — per-country level→field mapping — unnecessary). Its 1 req/s policy also
rules it out for the 4-point perimeter boundary check.

**Overpass retries** (`_overpass_post`): 429/502/503/504 + transport errors, backoff 1 s then 3 s,
three attempts, under a **hard 40 s deadline** (each attempt's timeout is shrunk to the time
remaining, or a fresh 25 s attempt started at t=26 s would run to 51 s). A 4xx is **not** retried —
that is a bug in our query, not a busy server. Measured: `is_in` at 26.015/101.883 returned 504
once, then answered on retry.

**No mirror failover (measured 2026-07-10; dead end, do not add).** `overpass-api.de` allows only
**2 concurrent queries per IP** (`/api/status`), and it load-balances between two backends
(`gall.` / `lambert.openstreetmap.de`) — which is why one `is_in` costs 1.2 s or 24 s. Adding
mirrors looks like the obvious hedge. It is not, on the Augsburg point:

| endpoint | result |
|---|---|
| `overpass-api.de` | 1.13 s, 6 admin rows ✓ |
| `overpass.kumi.systems` | ReadTimeout at 35 s |
| `overpass.private.coffee` | ReadTimeout at 35 s |
| `overpass.osm.jp` | ConnectError |
| `overpass.osm.ch` | **HTTP 200, zero admin rows** — regional (Swiss) data only |

`osm.ch` is the trap: it *succeeds* with an empty result for a German point, so a failover would
silently report "this point lies in no administrative area" and blank the hierarchy — a silent
wrong value (§2), which is worse than the honest failure. One endpoint, honest errors.

**Errors must say why** (`_overpass_status` / `_overpass_failure_message`). "Overpass unavailable"
gives the user nothing to act on. On failure, read `/api/status` (plain text; needs a `User-Agent`
or Apache answers 406) and report the slot budget: *0 of 2 query slots free for this computer*
(rate-limited — wait), or *2 of 2 free, so this looks like server load* (retry now). If the status
page is unreachable too, **suggest** a rate limit, never assert one. The degraded Photon fallback
also states that the fields came from the nearest feature rather than the containing areas, so the
user knows to check them.

**Boundary-crossing check — one combined `is_in` (measured 2026-07-10).** Whether the uncertainty
*circle* crosses a boundary cannot be answered by any single query at the centre. Two dead ends are
already in the history, do not retry them: `relation(around:r)` matches boundary relations by **way
node** proximity, so a small circle inside an admin area returns 0 results even when it visibly
crosses a border (`e4c27bb`); and 8 parallel Photon calls trip 503s, silently dropping points
(`cd2b6c9`, the Basel tripoint lost France). Sampling perimeter points is therefore still required,
and it is a **warning heuristic, not a guarantee** — 4 bearings cannot prove a circle is
boundary-free (`74713ce`).

Overpass takes **several `is_in` in one request**: `is_in(lat,lon)->.pN;
relation(pivot.pN)[boundary=administrative][name]; convert pt ::id=id(), i="N", …; out;` per point.
`convert` stamps the sample index onto each relation so results can be attributed back (absent tags
come back as `""`, not null). Verified at the Basel tripoint: one request, centre + 4 perimeter →
DE / FR / CH all detected; an interior point yields one hierarchy, no false positive. This also
returns the **centre's** full hierarchy, so it subsumes the separate hierarchy lookup.

**Cost: ~9–12 s** (a single `is_in` is ~0.9 s; five is roughly linear), and that shape has returned
504. So it **must not block the form.** Fill from the fast calls first and let this land later —
`is_in` is *areas*, so `relation(pivot.…)` is required (`rel.pN[…]` silently matches nothing).

**REVERTED (2026-07-12). There is currently NO boundary-crossing check at all**, and that is a
deliberate state: no warning is better than a wrong one that rewrites a good value. Both shipped
implementations were unacceptable, in opposite ways — a third design is needed, and it must clear
*both* bars:

| implementation | correct? | affordable? |
|---|---|---|
| **Photon perimeter samples** (original) | **No.** `/reverse` answers *proximity*, not containment, and reports a different admin tier: at a Peloponnese point it names the L4 Decentralized Administration where containment gives the ISO region (`GR-J`, L5). Centre and perimeter could never agree → a **false crossing on every Greek lookup**, and clicking the warning **overwrote a correct `stateProvince`** (#110). It also *missed* real crossings. | Yes — a steady **3.2 s**, on a separate service costing no Overpass slot. |
| **Overpass containment samples** (#110, PR #117) | **Yes.** Same service and same `_resolve_hierarchy` tiers as the centre; verified at the Basel tripoint (DE/CH/FR all detected) and at the Peloponnese point (no false warning). | **No.** It moved the check onto the **same 2-slot Overpass budget** as the hierarchy query: measured **0.9–12 s and wildly variable** (Overpass load-balances; the same query costs 1 s or 24 s), so a single lookup could saturate the IP's whole allowance and every geocode became slow and unpredictable. |

Constraints for the next attempt (all measured; **do not re-derive**): the dead ends in the
paragraphs above (`relation(around:r)`, 8 parallel Photon calls, two parallel Overpass queries)
still stand; four bearings remain a **heuristic, not a guarantee**; and whatever samples the
perimeter must answer *containment* with the **same tier rules as the centre**, or the comparison
is meaningless. The bar the Overpass version failed is **cost**, not correctness — an offline
boundary source (a local admin-polygon layer, cf. the planned GeoPandas habitat join) would clear
both, since it costs no API slot at all.

Two fixes from #110 are worth keeping whenever the check returns, and were lost with it:
**a warning must set only the field it belongs to** (`_apply_snap` wrote the *whole* snapshot, so
picking a *municipality* alternative silently overwrote `stateProvince`), and **an offered state
should carry its ISO 3166-2 code** (containment identifies the state *by* that tag; Photon had
none, so a state picked from a warning was stored uncoded — and `(name, iso_code)` is the vocab's
identity).

**Open (do not guess):** `county` / `municipality` have **no ISO tag** below the first-order
subdivision. `L6`=county and lowest `L7+`=municipality held across DE/GR/KZ, but that is a
three-country regularity, not a standard — validate it before relying on it. France already
strains it (`L5 European Collectivity of Alsace` is not a Regierungsbezirk tier). Germany strains
it too: a **kreisfreie Stadt** is both Kreis and Gemeinde and appears **once**, at L6 — so at
Augsburg (48.3324519, 10.9251308) `county=Augsburg` and `municipality` is **empty**. Copying county
→ municipality would be a guess; leave it empty until the tier can be identified from the data
(e.g. `de:place=city` / `admin_level` + `place` tags), not from its position.

The shared mechanism:

- **`app/services/vocab.py` → `Vocabulary`** — generic CRUD + merge for any single-name table
  (`id`, `name` UNIQUE), parameterised by the model. `list` / `options` / `get_or_create` /
  `create` / `update` / `delete` / `merge_preview` / `merge`. Merge and delete-safety
  **re-discover referencing FK columns dynamically** via `PRAGMA foreign_key_list` (same
  mechanism as `merge_persons`), so any FK at the vocab table — present or future — is handled
  with no hardcoded list. `delete` is blocked while referenced (and the FK is `ON DELETE
  RESTRICT` as a DB backstop). **Optional default flag:** a vocab constructed with
  `has_default=True` (its table needs an `is_default` column + partial-unique index) gets
  `get_default` / `get_default_name` / `set_default` — a single flaggable **Tier-1 autofill
  default** (mirrors repository `is_default`, #83). `preparation` uses it (migration 0052);
  the Controlled-Vocab card shows a ★ toggle when `VocabSpec.supports_default`.
- **`app/services/vocabularies.py`** — instances + a `VOCAB_REGISTRY` of `VocabSpec`s (display
  metadata; `supports_default` opts a vocab into the ★ default affordance). **Adding a vocab =
  a model + a migration + one registry entry**; it then appears in the Controlled Vocabularies
  tab and gets a dropdown field automatically.
- **`app/ui/vocab_field.py` → `build_vocab_field`** — the data-entry widget, the *same*
  custom-dropdown UX as the person field (`✚ add <typed>` + existing matches, no free-text
  escape; reuses person_field's CSS/nav). `get_value()` is the name; **`commit(session)`
  resolves name → id (get_or_create) inside the save transaction and returns the FK** (exactly
  like person `commit`). Used in Digitize/Records (shared `specimen_form`), Import & Assign,
  Mounting, and — since the geography levels are vocabs too — the **collecting-event form**
  (`country` / `stateProvince` / `admin. region` / `county` / `island`; `_VocabInput` adapts
  the handle to the `.value` interface the form's field registry drives).
  - **Code-bearing vocabs** (`code_attr`) additionally carry `get_code()` / `set_value(name,
    code)`, and render the ISO code as a **pill** — on existing rows *and* on `✚ add Greece
    GR`, because a geocoded name can be new to the vocab yet already carry a code. The widget
    lists **`vocab.entries()`** (one row per DB row), never `options()` (a `{name: name}` dict
    that silently collapses `Limburg` BE-VLI / NL-LI into one).
  - The event form does **not** use `commit()` for country/state: `events._resolve_geo_fields`
    stays the single owner of `(name, iso_code)` resolution, so every write path (Digitize,
    Records, Import) resolves identically.
- **DwC export** resolves `preparation_id` → `preparation.name` → `dwc:preparations` at export
  time (mirrors `recordedBy`/`identifiedBy`; nothing denormalised on `collection_object`).
- **Controlled Vocabularies tab** renders one card per registry entry (`_build_vocab_section`)
  with edit / merge / delete / add — the generic mirror of the People card. **One section per
  tab** (People, Collections/Institutions, then the registry), always on — these lists grow
  without bound. Code-bearing vocabs show an **`ISO code` column**, editable in the add + edit
  dialogs (the user must be able to supply a code the geocoder never found), and the **merge
  dialog labels rows with `display_label()`** — without the code, the two `Limburg` rows are
  indistinguishable in the very dialog that permanently deletes one. Build detail (why NiceGUI
  tabs and not the Digitize chip bar) → design.md.
- **Person stays separate** (not folded into `Vocabulary`): it carries extra columns
  (`abbreviated_name`, `orcid`) and label-printing logic. `Vocabulary` is for the *single-name*
  case only.
- **Collections/institutions (`repository`) stay separate too** (multi-column, #56): identified
  by `collection_full_name` (UNIQUE; `dwc:collectionCode` is optional — migration 0072, see
  the namespace section), with `institution_full_name` and the two
  TaxonWorks ids (institution=Repository, collection=Namespace). DwC-mapping columns carry the
  `dwc:` prefix; the rest are local. It is the source for the identifier label's full
  collection name (`repositories.name_map`, resolved by the code prefix `JJPC-00304`→`JJPC`)
  and for the **default collection** (`is_default` flag, migration 0050, #83 — the home
  collection that stamps new specimens' `repository_id` + catalog-number prefix; see the
  namespace section). Carries an optional **contact/owner `person_id`** (FK → `person`, ON
  DELETE RESTRICT, nullable — migration 0051, #79; a single person per collection, no roles;
  `merge_persons`/delete re-point/block it via the dynamic PRAGMA FK discovery). Its own
  Controlled-Vocabularies card (not the generic single-name one). Migration 0045.

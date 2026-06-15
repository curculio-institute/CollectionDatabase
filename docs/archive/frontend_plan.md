# Frontend Implementation Plan (Phase 2)

> **Purpose of this document.** A complete, decision-locked spec for the NiceGUI
> data-entry frontend so that a follow-up session (on a smaller model) can
> implement it without re-deriving architecture or UX choices. Read this top to
> bottom, then work the **Implementation order** checklist (§14).
>
> **Status:** not started. NiceGUI is *not yet installed*. No `app/services/` or
> `app/ui/` packages exist yet.

---

## 1. Goals & non-goals

### Goal
A single-screen, keyboard-friendly data-entry app modelled on the user's existing
**BeetleLog** tool: **entry fields on top, a table of recent records below.**
Clicking a table row loads that record into the form. Built on the existing
SQLAlchemy ORM (Phase 1), served in the browser via NiceGUI.

The user explicitly approved this layout: *"entry fields above and a table below."*

### Two confirmed UX decisions (do not redesign these)
1. **Collecting event = searchable picker.** A search box where the user can type
   *any* text (locality, date, collector, country…). Matches are shown as
   **concatenated one-line summaries** in a dropdown. Selecting one links the new
   specimen to that existing `collecting_event`. If nothing is selected, the event
   fields the user filled in create a **new** event on save.
   (User: *"searchable event picker. Every text can be entered … and it will show
   concatenated data for the collecting event in a dropdown so I can choose from it."*)
2. **Taxon = autocomplete dropdown of names already in the collection.** No
   on-the-fly taxon creation in the main entry flow. (User: *"autocomplete (dropdown)
   of names in collection."*)

### Non-goals (explicitly deferred — see §13)
- Catalog-number auto-suggest / namespace logic (*"this is backend stuff, deal with
  it later"* — user).
- Biological associations UI.
- Leaflet map view.
- Editing/updating existing records (start **insert-only**; row-click only *loads*
  values into the form to use as a template for a new insert).
- Synonymisation UI, taxon CRUD, determination history viewer.

---

## 2. Tech & dependencies

| Item | Decision |
|------|----------|
| UI framework | **NiceGUI** (per CLAUDE.md §3). Pure-Python, browser-served. |
| Run mode | **Browser** by default: `ui.run(native=False, reload=False, port=8080)`. pywebview wrapper optional, do **not** build it now. |
| Version pin | Add `nicegui` (2.x) to `environment.yml`. |

**Add to `environment.yml`** (NiceGUI is reliably available via pip; keep it in a pip
sub-section to avoid disturbing the conda solve):

```yaml
  - pip
  - pip:
      - nicegui==2.*
```

Then: `conda env update -n collection -f environment.yml`.
Do **not** install into `phylogeny` or `catalogue` envs (CLAUDE.md §3).

---

## 3. Architecture & layers

CLAUDE.md §7 is binding: **"Keep the UI layer thin; logic lives in service/repository
functions callable from scripts."**

```
┌─────────────────────────────────────────────┐
│  app/ui/         NiceGUI pages & widgets      │  ← no SQLAlchemy queries here
│                  (event handlers call services)│
├─────────────────────────────────────────────┤
│  app/services/   plain functions:             │  ← all query/save logic
│                  (session, values) -> result   │     testable without a UI
├─────────────────────────────────────────────┤
│  app/models/     SQLAlchemy ORM (exists)       │
│  app/database.py engine + sessionmaker (exists)│
└─────────────────────────────────────────────┘
```

**Rules for the implementer**
- UI handlers receive user input, open a DB session, call **one** service function,
  show the result/toast. No `select()` / `session.query` inside `app/ui/`.
- Service functions take an explicit `Session` as their first argument and plain
  Python values otherwise (so the same functions are reusable from the Phase-3
  import/validation scripts and from tests).
- Service functions **do not** open or close sessions and **do not** commit — the
  caller owns the transaction boundary. (Exception: a thin `save_specimen_entry`
  orchestrator may flush to obtain IDs but still leaves commit to the caller.)

---

## 4. Directory structure to create

```
app/
  services/
    __init__.py          # re-export public service functions
    taxa.py              # taxon search + name formatting
    events.py            # collecting-event search + summary formatting + create
    specimens.py         # collection_object + determination create; recent-rows query
  ui/
    __init__.py
    main.py              # ui.run entrypoint; builds the page
    entry_form.py        # the top entry-form section (taxon / event / specimen)
    recent_table.py      # the bottom table + row-click -> form loading
    formatting.py        # shared display helpers (sex symbols, etc.) if needed
run.py                   # convenience launcher at repo root: `python run.py`
```

Keep modules small. `main.py` wires sections together exactly like BeetleLog's
`MainWindow.__init__` stacks `_art_section / _fundort_section / _beob_section /
_button_row / _table_section`.

---

## 5. Service-layer API (implement these signatures)

All functions live under `app/services/`. Types use a lightweight dataclass for
options so the UI never touches ORM internals.

```python
# app/services/taxa.py
from dataclasses import dataclass

@dataclass(frozen=True)
class TaxonOption:
    id: int
    label: str        # e.g. "Carabus coriaceus Linnaeus, 1758"

def format_scientific_name(taxon) -> str:
    """genus (+ subgenus) + specificEpithet (+ infraspecific) + authorship.
    Mirror BeetleLog's Arten_mit_Name view logic. None-safe; collapse blanks."""

def search_taxa(session, query: str, limit: int = 20) -> list[TaxonOption]:
    """Case-insensitive LIKE across genus + specificEpithet (and the formatted
    name). Empty query -> first `limit` taxa alphabetically. Used by the taxon
    autocomplete. Only returns taxa that exist in the DB (no creation)."""
```

```python
# app/services/events.py
from dataclasses import dataclass

@dataclass(frozen=True)
class EventOption:
    id: int
    summary: str      # concatenated one-liner (see format_event_summary)

def format_event_summary(event) -> str:
    """One-line concatenation for the picker dropdown. Suggested order, skipping
    blanks, joined by ' · ':
        country/countryCode · stateProvince · locality (or verbatimLocality)
        · eventDate · recordedBy · 'lat,lon'
    Keep it short but uniquely recognisable."""

def search_collecting_events(session, query: str, limit: int = 20) -> list[EventOption]:
    """Case-insensitive LIKE of `query` against ANY of: country, stateProvince,
    county, municipality, locality, verbatimLocality, eventDate,
    verbatimEventDate, recordedBy, habitat. Empty query -> most-recent `limit`
    events (ORDER BY id DESC). This backs the 'type anything' picker."""

def get_event(session, event_id: int):
    """Return the CollectingEvent or None — used to repopulate fields when a
    picker option is chosen."""

def create_collecting_event(session, **fields) -> "CollectingEvent":
    """Insert a new collecting_event from the entry-form fields. Coerce '' -> None,
    parse lat/lon/elevation/uncertainty to float, leave eventDate as the raw
    ISO-8601 string (intervals like '2024-06-15/2024-06-20' are valid — see
    memory: feedback_date_format). flush() so .id is available; no commit."""
```

```python
# app/services/specimens.py
def create_collection_object(session, *, collecting_event_id: int,
                             catalog_number: str, catalog_namespace: str,
                             **fields) -> "CollectionObject":
    """Insert a collection_object. catalog_number/namespace are NOT NULL in the
    schema; for now the UI may pass a placeholder namespace (see §13 deferred)."""

def create_determination(session, *, collection_object_id: int, taxon_id: int,
                         identified_by: str | None, date_identified: str | None,
                         identification_qualifier: str | None = None,
                         identification_remarks: str | None = None,
                         verbatim_identification: str | None = None,
                         is_current: int = 1) -> "TaxonDetermination":
    ...

def save_specimen_entry(session, *, taxon_id: int,
                        event_id: int | None, event_fields: dict,
                        specimen_fields: dict, determination_fields: dict):
    """Orchestrator used by the Save button:
       1. If event_id is None -> create_collecting_event(**event_fields); else reuse.
       2. create_collection_object(collecting_event_id=..., **specimen_fields)
       3. create_determination(collection_object_id=..., taxon_id=..., **determination_fields)
       Caller wraps this in `with session.begin():` so all three are one transaction."""

@dataclass(frozen=True)
class RecentRow:
    collection_object_id: int
    catalog_number: str
    scientific_name: str      # via current determination -> taxon
    sex: str | None
    individual_count: int | None
    country: str | None
    locality: str | None
    event_date: str | None
    recorded_by: str | None
    identified_by: str | None

def recent_specimens(session, limit: int = 200) -> list[RecentRow]:
    """JOIN collection_object → collecting_event (LEFT) and the *current*
    taxon_determination (is_current=1) → taxon. ORDER BY collection_object.id DESC.
    Backs the bottom table."""
```

> **Date handling:** every date field stores ISO-8601 text; `eventDate` supports
> intervals with `/` (verified against TaxonWorks `occurrence.rb:935`). Do not
> reformat or validate beyond trimming — keep the raw string. (memory:
> `feedback_date_format`.)

> **dwc: columns:** the DB column names carry the `dwc:` prefix, but you address
> them through the **snake_case Python attributes** (e.g. `event.decimal_latitude`,
> not `event["dwc:decimalLatitude"]`). Never hand-write the prefixed name in
> Python. (memory: `feedback_dwc_column_names`.)

---

## 6. Screen layout

Single page, vertical stack. ASCII mock (mirrors BeetleLog's section order):

```
┌───────────────────────────────────────────────────────────────────────────┐
│  TAXON                                                                       │
│  [ search taxon… ▼ ]   identifiedBy [______]  dateIdentified [______]        │
│                        qualifier [__]  det.remarks [__________]              │
├───────────────────────────────────────────────────────────────────────────┤
│  COLLECTING EVENT                                                            │
│  Find existing: [ type locality / date / collector … ▼ ]   [Clear event]    │
│  country[__] code[__] stateProvince[____] county[____] municipality[____]    │
│  locality[__________________]  verbatimLocality[__________]                  │
│  lat[____] lon[____] uncertainty(m)[__] elevation min[__] max[__]            │
│  eventDate[__________] verbatimEventDate[____] recordedBy[______]            │
│  habitat[______] samplingProtocol[______] fieldNumber[__]                    │
│  verbatimLabel[____________________________]                                 │
├───────────────────────────────────────────────────────────────────────────┤
│  SPECIMEN                                                                    │
│  catalogNumber[____]* basisOfRecord[PreservedSpecimen▼] count[1]             │
│  sex[▼] lifeStage[▼] preparations[____] typeStatus[____]                     │
│  disposition[▼] occurrenceRemarks[__________]                                │
├───────────────────────────────────────────────────────────────────────────┤
│                         [ 💾 Save specimen ]   <status text>                 │
├───────────────────────────────────────────────────────────────────────────┤
│  RECENT SPECIMENS                                            [🔄 Refresh]    │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ id │ catalog │ scientificName │ sex │ n │ country │ locality │ date │…│  │
│  │ …rows, newest first; click a row to load into the form…              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
```

Use `ui.card()` per section with a bold section header, like BeetleLog's `QGroupBox`.

---

## 7. Widget-by-widget spec

Map each form field to the ORM attribute it feeds. `*` = required.

### Taxon section → drives `taxon_determination` (+ taxon link)
| Widget | NiceGUI | Feeds | Notes |
|--------|---------|-------|-------|
| Taxon search* | `ui.select(..., with_input=True)` or input+menu | `taxon_id` | §9. Sets `selected_taxon_id`. Required to save. |
| identifiedBy | `ui.input` | `determination.identified_by` | default to a configurable person, like BeetleLog's `DEFAULT_PERSON`. |
| dateIdentified | `ui.input` | `determination.date_identified` | ISO-8601 text. |
| qualifier | `ui.input` | `determination.identification_qualifier` | e.g. `cf.`, `aff.` |
| det. remarks | `ui.input` | `determination.identification_remarks` | |

`verbatim_identification` may be auto-filled from the selected taxon label.

### Collecting-event section → drives `collecting_event`
| Widget | Feeds (attr) | Notes |
|--------|--------------|-------|
| Event search picker | (selects existing `event_id`) | §8 |
| Clear event button | resets `selected_event_id=None` and clears event fields |
| country | `country` | |
| code | `country_code` | exactly 2 chars or empty (DB CHECK `ck_ce_country_code_len`). Validate client-side before save to avoid IntegrityError. |
| stateProvince | `state_province` | |
| county | `county` | |
| municipality | `municipality` | |
| locality | `locality` | |
| verbatimLocality | `verbatim_locality` | |
| lat | `decimal_latitude` | float; range [-90,90] (CHECK). |
| lon | `decimal_longitude` | float; range [-180,180] (CHECK). |
| uncertainty(m) | `coordinate_uncertainty_in_meters` | float ≥ 0 (CHECK). |
| elevation min/max | `minimum/maximum_elevation_in_meters` | float. |
| eventDate | `event_date` | ISO-8601; intervals allowed. |
| verbatimEventDate | `verbatim_event_date` | |
| recordedBy | `recorded_by` | collector. |
| habitat | `habitat` | verbatim habitat (GIS enrichment is Phase 3, separate column). |
| samplingProtocol | `sampling_protocol` | could be a `ui.select` with the BeetleLog `METHODEN` list. |
| fieldNumber | `field_number` | |
| verbatimLabel | `verbatim_label` | full label transcription. |

### Specimen section → drives `collection_object`
| Widget | Feeds (attr) | Notes |
|--------|--------------|-------|
| catalogNumber* | `catalog_number` | NOT NULL. See §13 for namespace/auto-number deferral; for now require manual entry. |
| basisOfRecord | `basis_of_record` | default `PreservedSpecimen`. |
| count | `individual_count` | int ≥ 0 (CHECK); default 1. |
| sex | `sex` | `ui.select`; reuse a sex list (♂ ♀ etc.). |
| lifeStage | `life_stage` | `ui.select`: adult/larva/pupa/egg. |
| preparations | `preparations` | e.g. pinned, in ethanol. |
| typeStatus | `type_status` | usually blank. |
| disposition | `disposition` | in collection / on loan / donated / … |
| occurrenceRemarks | `occurrence_remarks` | |

---

## 8. Collecting-event picker behaviour (the key interaction)

State: a module/page variable `selected_event_id: int | None = None`.

1. User types in the **event search** box.
2. Debounced `on_change` handler (≈250 ms) calls
   `search_collecting_events(session, text)` and refreshes the dropdown options to
   the returned `EventOption.summary` strings (keep the parallel list of ids).
3. **User selects an option** → set `selected_event_id = option.id`, fetch the
   event via `get_event`, and **populate all event fields** from it (block change
   handlers while setting, like BeetleLog blocks signals in `on_tree_select`).
   Optionally visually mark the event section as "linked to existing #id".
4. **User edits any event field after selecting** → treat as *diverging from the
   chosen event*: clear `selected_event_id` back to `None` so save creates a NEW
   event (do not silently mutate the existing shared event — events are shared by
   many specimens). Show a subtle hint ("editing will create a new event").
5. **Clear event** button → `selected_event_id=None` + clear all event fields.

On **Save**: if `selected_event_id` is set → reuse it; else → create a new event
from the current field values.

> This is the explicit-picker variant the user asked for, as opposed to BeetleLog's
> silent `get_or_create_fundort` exact-match dedup. We never auto-dedup; the user
> chooses.

---

## 9. Taxon autocomplete behaviour

State: `selected_taxon_id: int | None = None`.

- Backed by `search_taxa(session, text)`.
- Recommended widget: `ui.select(options={id: label}, with_input=True,
  on_change=...)`. If server-side search-as-you-type is needed (large taxon list),
  use an `ui.input` with a debounced handler that rewrites the select's `options`.
- Selecting an option sets `selected_taxon_id`.
- **Saving requires a selected taxon.** If `selected_taxon_id is None`, block save
  with a red toast ("Select a taxon"). No taxon creation here.
- (A separate "add taxon" workflow is a later task; spreadsheet import will seed
  the taxon table first anyway.)

---

## 10. Save flow (Save button handler)

```python
def on_save():
    if selected_taxon_id is None:
        ui.notify("Select a taxon first", type="negative"); return
    cat = catalog_number_input.value.strip()
    if not cat:
        ui.notify("catalogNumber is required", type="negative"); return
    # client-side guards mirroring DB CHECKs (fail loud BEFORE the DB does):
    #  - country_code: '' or exactly 2 chars
    #  - lat in [-90,90], lon in [-180,180], uncertainty >= 0, count >= 0
    #  - numeric fields parse as float/int
    try:
        with session_factory() as session:
            with session.begin():                      # one transaction
                save_specimen_entry(
                    session,
                    taxon_id=selected_taxon_id,
                    event_id=selected_event_id,         # None -> new event
                    event_fields=collect_event_fields(),
                    specimen_fields=collect_specimen_fields(),
                    determination_fields=collect_determination_fields(),
                )
        ui.notify("Saved", type="positive")
        refresh_table()
        clear_for_next_entry()      # see below
    except Exception as e:
        ui.notify(f"Save failed: {e}", type="negative")
```

**Clear-for-next-entry** — copy BeetleLog's "keep" checkboxes so repeated specimens
from the same event/collector are fast:
- `Keep event` checkbox: when ticked, leave the event fields + `selected_event_id`
  intact after save (next specimen reuses the same event).
- `Keep determination` checkbox: leave identifiedBy/date.
- Always clear: catalogNumber, occurrenceRemarks, and (optionally) the taxon search.

---

## 11. Recent-specimens table

- Widget: `ui.table` with columns from `RecentRow` (§5).
- Populated by `recent_specimens(session)`; newest first.
- **Row click** → load that row's values into the matching form fields (block change
  handlers while setting). This is a *template for a new insert*, NOT an edit of the
  clicked record (editing is deferred, §13). Make this clear in a tooltip/status.
- **Refresh** button re-runs the query (also called automatically after each save).
- Column widths/labels: model on BeetleLog's `COLUMNS` / `COL_WIDTHS` for familiarity.

---

## 12. Session & engine management (NiceGUI footgun — read this)

NiceGUI runs on FastAPI/asyncio. **Do not** create one long-lived `Session` for the
app. Instead:

```python
# app/ui/main.py
from app.database import get_engine, get_session_factory
engine = get_engine("sqlite:///collection.db")     # once, at import/startup
session_factory = get_session_factory(engine)
```

In **every** handler open a short-lived session:

```python
with session_factory() as session:
    ... call services ...
    session.commit()      # or use `with session.begin():`
```

- The engine's connect-event already sets `PRAGMA foreign_keys = ON` and WAL
  (`app/database.py`) — so FK/CHECK constraints are enforced. Good.
- Keep DB work inside the handler; pass plain values / dataclasses out to the UI,
  not detached ORM objects (avoids `DetachedInstanceError`).
- SQLite + WAL tolerates the single-user concurrency here; no pooling tuning needed.

---

## 13. Deferred features (document, do not build now)

| Feature | Why deferred / note |
|---------|--------------------|
| catalogNumber auto-suggest & namespace | User: *"backend stuff, deal with it later."* For now: manual `catalogNumber`; pick a single default `catalog_namespace` constant (e.g. `"Jilg"`) in the UI and revisit. |
| Editing existing records | Start insert-only. Row-click loads a template only. Edit/PATCH is a later phase (and TW sync is insert-only anyway, CLAUDE.md §5). |
| Biological associations UI | Separate page later; exclusive-arc form (subject/object each specimen-or-taxon). Local-master, no DwC push. |
| Leaflet map of localities | Phase 2 stretch; NiceGUI `ui.leaflet`. |
| Taxon CRUD / add-taxon | Spreadsheet import seeds taxa first. |
| Synonymisation UI | Backend supports it (`accepted_name_usage_id`); no UI yet. |
| pywebview native window | Browser mode only for now. |

---

## 14. Implementation order (work this checklist)

1. **Env:** add `nicegui==2.*` to `environment.yml`; `conda env update`. Verify
   `python -c "import nicegui"`.
2. **Services first (testable without UI):**
   - `app/services/taxa.py` — `format_scientific_name`, `search_taxa`.
   - `app/services/events.py` — `format_event_summary`, `search_collecting_events`,
     `get_event`, `create_collecting_event`.
   - `app/services/specimens.py` — `create_collection_object`,
     `create_determination`, `save_specimen_entry`, `recent_specimens`.
   - Re-export from `app/services/__init__.py`.
3. **Service tests** (`tests/test_services.py`): reuse the `session` fixture from
   `tests/conftest.py` (function-scoped, rolls back after each test; built on a
   session-scoped temp-file engine with `alembic upgrade head` applied). Cover:
   name formatting (None-safe),
   taxon search LIKE, event search across multiple fields, save orchestrator creates
   3 rows in one transaction, recent_specimens join returns the current determination
   only.
4. **UI skeleton:** `app/ui/main.py` with `ui.run`, plus empty section cards. Launch,
   confirm it serves at localhost:8080.
5. **Entry form** (`entry_form.py`): build the three sections (§7) with field→attr
   wiring; collect_*_fields() helpers returning dicts.
6. **Taxon autocomplete** (§9) wired to `search_taxa`.
7. **Event picker** (§8) wired to `search_collecting_events` / `get_event`, including
   the "edit clears selection" rule.
8. **Save handler** (§10) + keep-checkboxes + client-side CHECK guards.
9. **Recent table** (`recent_table.py`, §11) + row-click loads template + refresh.
10. **`run.py`** launcher at repo root.
11. Manual smoke test: seed a couple taxa + events (or reuse `collection.db`), enter a
    specimen, confirm it appears in the table and in the DB.

---

## 15. Testing

- **Services are the test surface** (UI stays thin). Mirror the style of
  `tests/test_constraints.py` and reuse `tests/conftest.py`'s `session` fixture
  (note: it `rollback()`s after each test, so any commit you make is undone on
  teardown — assert within the test body before yield/teardown).
- Assert the save orchestrator is atomic: if the determination insert fails, the
  collection_object + event must roll back (single `session.begin()`).
- Don't try to unit-test NiceGUI rendering; a manual smoke test (step 11) is enough
  for Phase 2.

---

## 16. Open questions to confirm with the user when reached

1. Default person string for `identifiedBy` / `recordedBy` (BeetleLog used
   `"Tristan Schirok"`). Ask for the user's default.
2. Controlled vocab lists: reuse BeetleLog's `METHODEN` (samplingProtocol) and a sex
   list? Confirm the exact options and language (English vs German labels).
3. Default `catalog_namespace` constant to use until the backend numbering is built.
4. Should `verbatim_identification` auto-fill from the selected taxon label, or stay
   a separate manual field?

---

### Quick reference — file locations
- ORM models: `app/models/` (`Taxon`, `CollectingEvent`, `CollectionObject`,
  `TaxonDetermination`, `BiologicalRelationship`, `BiologicalAssociation`).
- Engine/session: `app/database.py` (`get_engine`, `get_session_factory`).
- Schema diagram: `docs/schema.html` / `docs/schema.mmd`.
- Live DB: `collection.db` (repo root).
- Test fixtures/pattern: `tests/conftest.py`, `tests/test_constraints.py`.

# Application Design

Working design document. Each section is written and agreed before implementation.
Overrides or refines anything in CLAUDE.md where they conflict.

---

## 1. Program overview

A local-first, single-user desktop application for maintaining an entomological specimen
collection (primary focus: Coleoptera). Runs in the browser at localhost; the SQLite
database on disk is the sole source of truth.

The application serves three practical purposes:

1. **Digitising specimens** — recording where and when a specimen was collected, what it
   is (identification), and assigning it a permanent physical identifier label.
2. **Managing the taxonomy** — maintaining a local taxon tree, sourced from TaxonWorks,
   against which identifications are made.
3. **Producing labels** — printing identifier labels and locality/identification labels
   that are physically pinned with each specimen.

A secondary purpose, not yet built, is **synchronising** the local records one-way to
TaxonWorks as a published mirror.

The data structure follows the Darwin Core standard and mirrors TaxonWorks' data model as
closely as practical, so that records can be exported, imported, and verified against
TaxonWorks without lossy transformation.

---

## 2. Startup sequence

Understanding startup order matters when code has dependencies (engine, config, DB
tables) that must exist before other code runs.

```
python run.py
  └─ import app.ui.main           ← module-level code runs immediately
       ├─ get_engine()             ← SQLAlchemy engine created and cached
       ├─ get_session_factory()    ← session factory bound to that engine
       ├─ ensure_higher_taxa()     ← idempotent DB backfill, runs inside a session
       └─ seed_root_taxa()         ← idempotent seed, same session
  └─ ui.run()                     ← NiceGUI starts; browser connects
       └─ index()                  ← @ui.page('/') handler, runs per browser tab
            └─ get_config()        ← first config access, reads config.json
```

**Key rule:** anything that needs the engine or the DB tables must not run at Python
import time (module level outside a function). The engine is available from the moment
`main.py` is imported, but table existence is only guaranteed after `alembic upgrade head`
has been run manually before starting the app.

**Engine caching:** `get_engine()` in `app/database.py` caches by URL. Every call with
the same URL returns the same SQLAlchemy engine object and the same connection pool.
This matters because multiple modules call `get_engine()` independently; without caching
each call would create a separate pool to the same SQLite file.

**Config:** `get_config()` is called only inside the page handler and callbacks, never
at module level, so the engine and DB are always ready when config is first accessed.
Config is persisted to `data/config.json` (flat JSON, one key per field).
It survives DB resets, which is intentional.

### Data directory

All mutable data lives in `data/` at the project root. This directory is never
committed to git.

| File | Contents |
|---|---|
| `data/collection.db` | SQLite database (WAL mode) |
| `data/config.json` | Application settings (TW token, default names, etc.) |

Both paths are computed as absolute paths derived from `__file__` in their respective
modules (`app/database.py`, `app/config.py`), so the app works correctly regardless of
the working directory it is launched from. The `data/` directory is created automatically
on first import if it does not exist.

`alembic/env.py` overrides the URL from `alembic.ini` with the same absolute path, so
`alembic upgrade head` always targets the right file even when run from a subdirectory.

**After a fresh clone or a DB wipe**, run `alembic upgrade head` once before starting
the app. `data/config.json` is preserved across DB resets.

---

## 3. Layer architecture

The application is structured in three layers:

**Data** — SQLite database, SQLAlchemy ORM models, and Alembic migrations. The schema is
the authoritative definition of what data exists and what constraints it must satisfy.
Business logic does not belong here; the data layer only defines structure and enforces
integrity (foreign keys, CHECK constraints, NOT NULL).

**Services** — Python modules in `app/services/` that contain all business logic:
querying, creating, and updating records; communicating with the TaxonWorks API;
generating label PDFs. Services are the only layer that touches the database. They have
no knowledge of the UI. Services are shared — the same function is called from any tab
or widget that needs it.

**UI** — NiceGUI pages and widgets in `app/ui/`, split into two sub-levels:

- *Tabs* or *Tasks* — the actual tab screens. They assemble widgets, wire up callbacks, and call
  services on save. Tab code should be thin: layout and coordination only, no business
  logic.
- *Forms* - Sections, don't have their own code, design only — e.g. "Identification", "Collecting Event", "Specimen"
- *Widgets* — self-contained, reusable UI components (taxon search, collecting event
  picker, map picker, etc.). A widget knows how to display itself and fire a callback
  when something is selected or changed. It has no knowledge of which tab it lives in.
  Minor variations between tabs are handled through parameters and callbacks, not by
  duplicating the widget.


---

## 3. UI conventions

### Auto-fill tiers

Every field in every form falls into exactly one of three tiers. The tier determines how
the field is pre-populated and what the user must do to change it.

**Tier 1 — Auto-filled and editable**
Pre-populated with a sensible constant when a new record is created. The user sees the
value and can change it before saving. Used for values that are almost always the same.

| Field | Pre-filled value |
|---|---|
| `basisOfRecord` | `PreservedSpecimen` |
| `disposition` | `in collection` |

**Tier 2 — One-click configurable default**
Field starts empty. A small `📌` (pin) icon button sits next to the field. Clicking it
inserts the configured default. Nothing is filled silently — the user must actively click.
This prevents stale values appearing during rapid digitising.

The pin icon is always `push_pin` (Material icon), placed adjacent to the field (not
inside it), for every Tier 2 field without exception.

| Field | Configured default |
|---|---|
| `identifiedBy` | user's full name |
| `recordedBy` | user's full name |
| `dateIdentified` | current year |

**Tier 3 — Background invisible default**
Written silently into every saved record. Never shown as an editable form field.
Configured once in Settings and then forgotten.

| Field | Source | Stored |
|---|---|---|
| `institutionCode` | Settings config | per row on `collection_object` |
| `collectionCode` | Settings config | per row on `collection_object` |

Both fields are required. If either is not configured, saving any record is blocked with
a warning. Both are also enforced NOT NULL at the database level.

In the Digitize tab (and any other tab that creates records), both values are shown as
read-only display fields in the "more fields" section so the user can see what will be
recorded. They are not editable from the specimen form — to change them the user goes to
Settings.

#### Tier 3 display field — UI template

Every tier-3 field that is shown read-only in a form must follow this pattern:

```python
field_disp = (
    ui.input("fieldName", value=get_config().field_name)
    .props("readonly outlined dense")
    .classes("col-span-1")          # or whatever grid span applies
    .tooltip("Set in Settings — applies to every new record")
)

def _refresh_field_display():
    field_disp.value = get_config().field_name

ui.timer(2.0, _refresh_field_display)
```

The `ui.timer(2.0, ...)` is mandatory. NiceGUI renders each page once at load time;
any field whose value comes from mutable state (config, database) will be stale without
a periodic refresh. The timer fires every 2 seconds, re-reads the source, and updates
the display. This is the same mechanism used for DB-backed `ui.select` options.

The timer is created in the synchronous page-handler context and is per-client — it
stops automatically when the browser tab closes.

### Collection identity configuration

`institutionCode` and `collectionCode` are configured separately in the Settings tab.
They are independent fields to allow future support for specimens from guest collections,
where the two values may differ from the home collection defaults.

---

## 4. Taxon search widget (taxon_search.py)

A reusable widget used in the Digitize tab and the Records tab (and any future tab that
requires an identification). It renders a search field, handles the search interaction,
and calls a callback with the selected taxon ID when the user makes a pick. What the tab
does with that ID — create a new determination, update an existing one — is the tab's
responsibility. The widget has no knowledge of which tab it lives in.

### States

- **Empty** — no value entered; field shows placeholder text: "Enter genus or species name...".
- **Searching** — user is typing; dropdown is open showing results.
- **Selected** — a taxon has been picked; dropdown is closed; selection is displayed in
  the field.

### Searching

(works very well as is)
- Results come from the local database first, TaxonWorks second.
- Local and TaxonWorks results are shown in separate labelled sections in the dropdown.
- Synonymy is indicated visually in both sections.
- TaxonWorks results carry an import badge (✚ add) to distinguish them from local records.
- The user may only select from the dropdown. Free text without a matching selection is
  not accepted; clicking away without selecting returns the field to its previous state.

### Selected state

(currently broken)
- The exact visual appearance of the chosen dropdown entry in searching state — including synonymy styling,
  import badge, and accepted name — is placed directly into the search field as-is without delay.
- An × button appears alongside it to clear the selection and return to Empty.
- **No delay:** the dropdown entry is already rendered client-side at the moment the user
  clicks it, so it is placed in the field immediately — before any TaxonWorks import
  completes. The async import runs in the background; nothing visible changes when it
  finishes because the visual is already correct.
- The widget calls its callback with the taxon ID once the import is complete and the
  record exists in the local database.

### Callback

```python
on_select(taxon_id: int)
```

Called once, after the local DB record is confirmed to exist. The tab receives the ID and
decides what to do with it.

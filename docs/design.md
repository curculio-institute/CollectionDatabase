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

## 3a. Layer architecture

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
  logic. VERY IMPORTANT: The tab needs to make sure that all widgets and fields live-read existing values from the place where they also store entries. Stale values are a menace.
- *Forms* - Sections, don't have their own code, design only — e.g. "Identification", "Collecting Event", "Specimen"
- *Widgets* — self-contained, reusable UI components (taxon search, collecting event
  picker, map picker, etc.). A widget knows how to display itself and fire a callback
  when something is selected or changed. It has no knowledge of which tab it lives in.
  Minor variations between tabs are handled through parameters and callbacks, not by
  duplicating the widget.


---

## 3. UI conventions

### Automatic change indicator

Any time the application changes a value without the user explicitly typing the result —
whether correcting a format, auto-selecting a default, or normalising input — it must
signal this consistently.

**Symbol:** `auto_fix_high` (Material icon, the magic wand). Used everywhere, without
exception. Never a different icon for "auto-corrected" or "auto-selected".

**Two forms depending on whether the change is persistent or one-shot:**

#### One-shot correction (value was changed and the change is done)

The field value updates in place. A `ui.notify` appears immediately:

```
icon: auto_fix_high   type: info   timeout: 4 s
message: "Normalised: <old value> → <new value>"
```

Example: user types `15.06.2026`, tabs away → field becomes `2026-06-15`,
notification reads *"Normalised: 15.06.2026 → 2026-06-15"*.

If the input cannot be parsed at all, the field is **wiped** (`inp.value = ""`) and a
warning appears explaining what formats are accepted:

```
icon: auto_fix_high   type: warning   timeout: 8 s
message: "Invalid date removed — <error>.  Expected: <format hint>"
```

Format hints by field type:
- Single-date field: `"Expected: YYYY, YYYY-MM, YYYY-MM-DD, or European DD.MM.YYYY / MM.YYYY."`
- eventDate field (allow_interval=True): `"Expected: YYYY-MM-DD, YYYY-MM-DD/YYYY-MM-DD, or European equivalents."`

The field is wiped so no invalid value can slip through to the save path. The user
sees a blank field and can re-enter correctly.

#### Overridable auto-selection (system made a choice the user may want to change)

A pulsing `auto_fix_high` icon appears next to the auto-selected item alongside the
normal state icon. The animation uses the `.auto-changed` CSS class (defined in
`app/ui/date_input.py` as `AUTO_CHANGED_CSS`; inject with `ui.add_head_html`).

Tooltip on the pulsing icon:
```
"Automatically selected — <reason>. Click '<action>' to override."
```

Example: *"Automatically selected — most recent dateIdentified.
Click 'Set current' on another row to override."*

The pulse stops as soon as the user makes a manual choice (the `.auto-changed` class
is removed on the next render after the manual action clears the auto-state flag).

#### Implementation pattern

```python
# One-shot correction (date fields, format normalisation, etc.)
from app.ui.date_input import attach_date_validation
attach_date_validation(inp)                        # single date
attach_date_validation(inp, allow_interval=True)   # eventDate (range allowed)

# Overridable auto-selection (identification list, future pickers)
# 1. Track which item was auto-selected in a list[int | None] state variable.
# 2. Inject AUTO_CHANGED_CSS once via ui.add_head_html(AUTO_CHANGED_CSS).
# 3. In _render_row: if is_auto, add a pulsing auto_fix_high icon alongside the
#    normal state icon.
# 4. Clear the auto state when the user makes a manual choice.
```

---

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

### New-entry badge standard

Whenever a widget presents the user with an item or field value that **does not yet exist
in the database and will be created on save**, it must signal this with a `✚ add` badge.
This is the single, app-wide convention for "will be created". No other icon, colour, or
label is used for this purpose.

**Visual spec (`.pf-new-badge` / `.tw-import-badge` family):**

```
✚ add
```

- Background: `rgba(3,105,161,.12)`, text: `rgb(3,105,161)` (light mode)
- Background: `rgba(14,165,233,.15)`, text: `rgb(14,165,233)` (dark mode)
- Border-radius: 4px; padding: 1px 6px; font-size: .72rem; font-weight: 600

All widgets that need this badge inject their own CSS class with these rules via
`ui.add_head_html`. The class names may differ per widget (`.tw-import-badge`,
`.powo-import-badge`, `.pf-new-badge`) but they must produce visually identical output.

**Where it is used:**

| Widget | Trigger | Location |
|---|---|---|
| Taxon search — TaxonWorks results | TW name not yet in local DB | Dropdown item (right side) |
| Taxon search — POWO results | POWO name not yet in local DB | Dropdown item (right side) |
| Person field | Typed name not in person table | Dropdown item (top of list) |

**Implementation note — person field:** the badge is a selectable dropdown option with key
`"✚ add <text>"`.  It is injected dynamically as the user types via Quasar's `input-value`
event (see §6).  When selected it stays in the field as-is (`"✚ add <text>"`), signalling
that the person is a pending new entry.  `get_value()` and `commit()` strip the prefix at
save time.

---

## 4. Date input widget (date_input.py)

`attach_date_validation(inp, *, allow_interval=False, no_future=False)` — call once on any
`ui.input` that holds a DwC date field. The widget adds a single `blur` listener; it has no
other state and does not touch layout.

**Behaviour on blur:**

| Input | Result |
|---|---|
| Empty | No action |
| Valid ISO 8601 (YYYY, YYYY-MM, YYYY-MM-DD) | Silent, no change |
| Parseable non-ISO (European DD.MM.YYYY, MM.YYYY, missing zero-padding) | Normalised in place; "Normalised: old → new" info notification |
| Invalid (unrecognised pattern, bad calendar date) | Field wiped; warning notification with format hint |
| Future date when `no_future=True` | Treated as invalid: field wiped, warning notification |
| Interval when `allow_interval=False` | Treated as invalid |

**Parameters:**
- `allow_interval=True` — for `eventDate` only (accepts `YYYY-MM-DD/YYYY-MM-DD`)
- `no_future=True` — for `dateIdentified` fields (rejects dates after today)

**CSS:** `AUTO_CHANGED_CSS` (also in this module) defines the `.auto-changed` keyframe
animation used by the overridable-auto-selection pattern. Import and inject it with
`ui.add_head_html(AUTO_CHANGED_CSS)` in any widget that uses pulsing indicators.

**Where it is used:**

| Tab / widget | Field | Flags |
|---|---|---|
| Digitize (`main.py`) | `eventDate` | `allow_interval=True` |
| Records (`records_tab.py`) | `eventDate` (×2, edit and create) | `allow_interval=True` |
| Import & Assign (`import_assign.py`) | `dateIdentified` | `no_future=True` |
| Identification list (`identification_list.py`) | `dateIdentified` (×2, add and edit) | `no_future=True` |

**Tab responsibility:** the widget only validates on blur. Loading an existing record's date
value into the field, and keeping it live if the source can change, are the tab's
responsibility — not the widget's.

---

## 5. Taxon search widget (taxon_search.py)

A single reusable widget used wherever a taxon needs to be searched and selected:
specimen identification (Digitize, Records, Import & Assign), biological associations,
and any future tab. The caller controls which data sources are queried and whether results
are filtered by nomenclatural code. The widget has no knowledge of which tab it lives in.

### Signature

```python
build_taxon_search(
    session_factory,
    on_select=None,
    *,
    nomenclatural_codes: list[str] | None = None,
    sources: tuple | list = ("local", "taxonworks"),
    placeholder: str = "Enter genus or species name…",
) -> dict
```

**`sources`** — which APIs to query, in order. Each listed source always runs; there is no
conditional fallback. Valid values: `"local"`, `"taxonworks"`, `"powo"`.

**`nomenclatural_codes`** — if set, filters the local DB section to only those codes
(e.g. `["ICN"]` for plants/fungi). Does not filter TW or POWO — those are authoritative
for their own nomenclatural domain.

**`on_select(taxon_id: int)`** — optional callback, called once after the local DB record
is confirmed. Tabs that need to react immediately (Digitize, Records) use this. Tabs that
read the selection later (bio associations) poll the state dict instead.

### State dict

```python
{"taxon_id": int | None, "label": str, "clear": callable}
```

| `taxon_id` | Meaning |
|---|---|
| `None` | Nothing selected |
| `-1` | TW or POWO import is in progress — do not read yet |
| `N > 0` | Confirmed local DB id |

`label` — plain-text name of the selected taxon (e.g. `"Achillea millefolium"`). Always
present; callers that don't need it ignore it.

### States

- **Empty** — no value entered; field shows placeholder text.
- **Searching** — user is typing; dropdown is open showing results from each source in order.
- **Selected** — a taxon has been picked; the input is hidden and replaced by a styled
  display showing the exact dropdown item HTML (including any import badge). An ✕ button
  clears the selection and returns to Empty.

### Dropdown sections

Sections appear in `sources` order. Each always runs if listed.

**Local ("In database")** — searched first; 150 ms debounce; filtered by
`nomenclatural_codes` if set. ICN taxa are prefixed with 🌿 in both the dropdown and the
selected display. Synonyms shown as: *name* ✗ = *Accepted name* ✓.

**TaxonWorks** — names already in the local DB are filtered out (deduplication). Remaining
results carry the **✚ add** badge (blue). Hovering the badge shows: *"This taxon and its
parent taxa were imported from TaxonWorks"*. Clicking imports the taxon (and all parent
ranks) into the local DB in the background; `taxon_id` is set to `-1` until import
completes, then to the confirmed DB id.

**POWO (Plants of the World Online)** — only included when `"powo"` is in `sources`.
Results carry the **🌿 add** badge (green). Hovering shows: *"This taxon was imported from
Plants of the World Online (POWO)"*. Clicking imports via the IPNI/POWO API chain.
POWO always runs alongside TW — it is not a fallback.

### Import badge behaviour

The badge rendered in the dropdown item is reused verbatim in the Selected state (the
selected display shows the exact same HTML). This means the badge and its tooltip are
always visible when a TW or POWO taxon is selected, reminding the user that the taxon was
externally sourced.

### Where it is used

| Tab / widget | `sources` | `nomenclatural_codes` | Notes |
|---|---|---|---|
| Digitize (`main.py`) | `("local", "taxonworks")` | None | via `on_select` callback |
| Records (`records_tab.py`) | `("local", "taxonworks")` | None | via `on_select` callback |
| Import & Assign (`import_assign.py`) | `("local", "taxonworks")` | None | state dict polled |
| Identification list (`identification_list.py`) | `("local", "taxonworks")` | None | state dict polled |
| Bio associations — Digitize (`main.py`) | `("local", "taxonworks", "powo")` | `bio_codes` (mutable list) | state dict polled; `label` used |
| Bio associations — Records (`records_tab.py`) | `("local", "taxonworks", "powo")` | `bio_codes_local` | state dict polled |

`bio_codes` is a mutable list mutated in-place by the "Show animals too" toggle and the
Settings dialog. The widget reads it on every keystroke, so toggling takes effect
immediately without rebuilding the widget.

---

## 6. Person field widget (person_field.py)

`build_person_field(session_factory, label, ...)` — a reusable person-select field backed
by the `person` table. Renders directly into the caller's NiceGUI context (no wrapper
element is created). The caller owns the container.

### Signature

```python
build_person_field(
    session_factory,
    label: str,
    *,
    default_fn=None,
    initial_value: str | None = None,
    on_change=None,
    classes: str = "flex-1",
) -> dict
```

**`default_fn`** — zero-argument callable returning the push_pin default string. Called at
click time, never at render time (so the default is always current even if config changes
after the page loads). If `None`, no push_pin button is rendered.

**`initial_value`** — pre-populate the field (e.g. a value loaded from a DB record). If
the value is not in the current person options it is added as a freestanding entry so the
field shows the right value immediately.

**`on_change`** — optional callback fired on every value change, after the indicator
updates. Used by tabs that react to event-field edits (e.g. Digitize tab marks the event
as dirty on every field change).

**`classes`** — CSS classes on the `ui.select` element. Default `"flex-1"` fills the
available width inside the caller's flex container.

### Returned state dict

```python
{
    "get_value": callable,   # → str | None; strips whitespace
    "set_value": callable,   # set_value(val: str | None)
    "commit":    callable,   # commit(session) — see below
    "refresh":   callable,   # force-refresh options from DB immediately
}
```

### Visual: new-entry indicator

When the user types a name that is not in the person table, a **`✚ add <typed text>`**
option appears at the **top of the dropdown** alongside matching existing persons.
Selecting it sets the field value to `"✚ add <name>"`, keeping the prefix visible so the
user can see it is a pending new entry and still correct or clear it before saving.

This is the app-wide convention (see §3 — new-entry badge standard). No separate badge
element or CSS injection is needed — the prefix in the option label is the indicator.

The indicator disappears automatically if:
- the user types a name already in `_known` (the option is not added)
- `commit` is called (the name is added to `_known`; the next `input-value` event omits it)

**How it works technically** (mirrors `taxon_search.py`):

- A `ui.input` is used for typing.  An absolutely-positioned `div` (`.pf-dropdown`) below
  it is shown/hidden as the user types.
- On each `value_change` event the dropdown is rebuilt: matching persons as plain
  `.pf-item` rows, plus a `<span class="pf-new-badge">✚ add</span> <name>` row at the top
  when the typed text is not an exact match in `_known`.
- Clicking a row calls `_enter_selected()`, which hides the input, shows the
  `.pf-selected-display` div (styled like an outlined input), and fires `on_change`.
- A `✕` button in the selected display calls `_clear()` to return to the search state.
- On input `blur`, the dropdown hides after a 0.2 s delay so that dropdown-item clicks
  register first — the same pattern as `taxon_search.py`.
- There is no free-text entry; the user must select from the dropdown.
- The 2-second refresh timer only updates `_known`.  The dropdown is rebuilt fresh on every
  keystroke, so new persons added elsewhere are visible immediately after the user starts
  typing again.

#### Flushing orphaned input text on blur

When the user types in the field and then tabs away without selecting a dropdown item, the
`ui.input` retains the typed text visually, but `_value[0]` was never set — so
`get_value()` returns `None` and the text is logically invisible to the save path.

The fix is to clear the input at the end of the blur handler, *after* the selection window:

```python
async def _on_blur(_) -> None:
    await asyncio.sleep(0.2)   # let dropdown item click register first
    dropdown.style("display:none")
    # If nothing was selected, wipe orphaned text so the field is
    # visibly and logically empty.
    if _value[0] is None and inp.value:
        inp.value = ""
```

The 0.2 s sleep is already there to give NiceGUI time to deliver a dropdown-item click to
Python before the blur handler runs.  If a click did arrive, `_enter_selected` will have
set `_value[0]`, so the guard `_value[0] is None` is False and the text is preserved.

**This pattern applies to any custom `ui.input` + dropdown widget** where selection is
mandatory and free-text entry is not allowed.  Always pair the 0.2 s blur delay with the
orphaned-text guard.

### commit(session)

Call `commit(session)` inside the tab's save transaction, **before** writing the main
record:

```python
with session_factory() as s:
    with s.begin():
        person_state["commit"](s)          # ensures Person row exists
        ev_svc.update_collecting_event(s, ev_id, recorded_by=person_state["get_value"](), ...)
```

`commit` is a no-op if the field is empty or the value is already in `_known`.

Internally `commit` calls `persons_svc.get_or_create_person` (not `create_person` directly)
— see **§6a** below for why this matters when multiple person fields share one transaction.

`recorded_by` and `identified_by` on the DB models are plain text fields with no FK to
the person table. `commit` creates the person row purely to keep the autocomplete table
populated — it does not satisfy any constraint.

---

## 6a. get_or_create pattern for lookup-table rows

**Problem:** A page may contain multiple widgets that each reference the same lookup table
(e.g. two `build_person_field` instances for `identifiedBy` and `recordedBy`).  If the
user types the same new name in both fields and the tab calls `commit` on each widget
inside a single `with s.begin()` block, the second `commit` would try to `INSERT` a row
that the first `commit` has already `flush()`-ed — hitting the `UNIQUE` constraint and
rolling back the entire transaction.

**Why the query finds the row:** SQLAlchemy's `session.flush()` writes the `INSERT` to the
database within the open transaction.  A subsequent `session.query()` on the *same session*
(same connection, same transaction) sees the flushed row before `commit()` is called.
This is standard SQLAlchemy behaviour — the session's identity map and the underlying
database connection share the same transaction, so flushed writes are immediately visible
to later reads in that session.

**Solution:** use `get_or_create` instead of a bare `create`:

```python
# app/services/persons.py
def get_or_create_person(session, *, full_name: str) -> Person:
    existing = session.query(Person).filter_by(full_name=full_name.strip()).first()
    if existing:
        return existing
    return create_person(session, full_name=full_name)
```

**Rule:** any service function that inserts into a table with a `UNIQUE` constraint and
may be called more than once per transaction (from different widgets or commit paths)
**must** use a `get_or_create` variant.  A bare `create` is only safe when the caller
guarantees at most one insert per unique key per transaction.

**Applies to:** `person`, and any future lookup table (locality, institution, etc.) that
follows the same "free-text entry → autocomplete table" pattern.

### Live options refresh

The widget creates a `ui.timer(2.0, refresh)` internally. No external timer is needed.
Tabs that need to trigger an immediate refresh when the person table changes elsewhere
(e.g. after a person is added via the Controlled Vocabularies tab) should call
`person_state["refresh"]()` directly.

### Placement convention

The widget renders a `ui.select` plus (optionally) a push_pin button directly into the
current context. The caller must wrap both in a flex row/div:

```python
with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
    person_state = build_person_field(
        session_factory, "identifiedBy",
        default_fn=lambda: get_config().default_identified_by or None,
        initial_value=loaded_value,
    )
```

The push_pin button uses `bind_visibility_from(sel, "value", lambda v: not v)` — it is
only visible when the field is empty, consistent with Tier 2 field behaviour (see §3).

### Where it is used

| Tab / widget | Field | `default_fn` | `on_change` |
|---|---|---|---|
| Digitize (`main.py`) | `recordedBy` | `default_recorded_by` | `_on_event_field_edit` |
| Records — specimen form (`records_tab.py`) | `recordedBy` | `default_recorded_by` | — |
| Records — event form (`records_tab.py`) | `recordedBy` | `default_recorded_by` | — |
| Identification list — edit panel (`identification_list.py`) | `identifiedBy` | `default_identified_by` | — |
| Identification list — add panel (`identification_list.py`) | `identifiedBy` | `default_identified_by` | — |
| Import & Assign (`import_assign.py`) | `identifiedBy` | `default_identified_by` | — |

### In-memory mode (Digitize)

In the Digitize tab, identification data is held in an in-memory list (`_dets`) until the
whole specimen record is saved. At the point of adding an identification to the list
(`_do_add`), `commit` is called in a short separate transaction:

```python
with session_factory() as s:
    with s.begin():
        add_idby_state["commit"](s)   # creates person if new
_dets.append({..., "identified_by": add_idby_state["get_value"](), ...})
```

Person creation is independent of the specimen record — creating the person row early is
always safe and means the name is available in autocomplete from that point forward.

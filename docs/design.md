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
| `dateIdentified` | current 4-digit year (e.g. `"2026"`) — insert the year only; the user completes month/day |

**Placement (mandatory):** the button must be a **sibling adjacent** to the field (in a flex
row), **not** inside the field's `add_slot("append")`. Quasar QSelect intercepts all events
inside its append slot and opens the dropdown, so `on_click` never fires independently. For
`ui.input` (QInput) the append slot does work, but `push_pin` is still placed adjacent for
visual consistency across all Tier 2 fields.

**Tier 2 implementation pattern** (a `ui.select` person field; adapt for inputs):
```python
with ui.row().classes("flex-1 min-w-40 items-center gap-1"):
    sel = ui.select(opts, label="identifiedBy", with_input=True, clearable=True).classes("flex-1")
    (
        ui.button("", icon="push_pin")
        .props("flat dense round size=xs")
        .tooltip("Insert default name")
        .on_click(lambda: sel.set_value(get_config().default_identified_by) if get_config().default_identified_by else None)
        .bind_visibility_from(sel, "value", lambda v: not v)   # hide once the field has a value
    )
```

Always call `get_config()` **inside the lambda at click time** — never capture the value at
render time, or the button freezes to whatever was configured when the page loaded.

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

### "det." display convention

Whenever an identifying person's name is shown in **any UI display context** — a list row,
a table cell, a summary line — it must be prefixed with `det.`:

```
det. Firstname Lastname
```

This applies to:
- The meta line in the identification list (`identification_list.py`)
- The "det" column in the recent specimens table (`main.py`)
- The secondary info line in mounting session specimen rows (`mounting_session.py`)
- Label PDFs (`labels.py`) — already uses this prefix

It does **not** apply to form field labels, field placeholders, or the field value inside
an `identifiedBy` input — only to read-only display of a resolved person name.

---

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

### Digitize layout modes (normal / single-card)

The Digitize tab has two layouts, chosen in Settings (`AppConfig.digitize_layout`,
`"normal"` | `"single_card"`). The *policy/decision* lives in CLAUDE.md → "Digitize
layout modes"; this is the build detail.

Both layouts use the **same cards** (`specimen_card` / `spec_visiting["card"]`,
`identification_card`, `event_card`, `bio_card`) built once — the layout only changes the
container width and which cards are visible. There is no duplicate card tree.

- **Normal:** container is `max-w-7xl`; Specimen and Identifications sit in one
  `flex flex-wrap` row (each card `flex-1 min-w-[360px]`, so they pair on wide screens and
  stack when narrow). Collecting Event and Biological Associations are full-width below.
- **Single-card (stepper):** container is `max-w-4xl`; one card shows at a time. A chip bar
  (`.tp-stepper-bar` → `.tp-step-chip`, current chip `.active`) and a Back/Next row
  (`step_nav_row`) appear; on the **last** step the Next row is hidden and the normal Save
  bar (`std_save_row`) shows instead — the single real Save is unchanged. Mounting mode
  ignores the stepper (keeps its own staging layout).

**Single source of truth for visibility:** `_refresh_card_visibility()` computes each card's
visibility as *create-mode-visible* AND (*normal* OR *is current step*). `_on_mode_toggle`,
the step nav, and the Settings save all funnel through `_apply_digitize_layout()` →
`_refresh_card_visibility()`. The step list comes from `_step_cards()` (the first card swaps
standard↔visiting). The stepper **never commits per card**; it only toggles visibility, so
value-based unsaved-change detection (which polls field *values*, not visibility) keeps
working for off-screen steps.

**Arrow keys:** a global `keydown` head-script emits a `tp-step-nav` event (handled by
`ui.on("tp-step-nav", …)` → `_step_nav`) on ←/→, but only when the stepper bar is visible
and focus is **not** in an input/textarea/select/`.q-field`/`.q-menu` (so arrows still work
inside fields and dropdowns).

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
    def _default_idby() -> str | None:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[0]

    person_state = build_person_field(
        session_factory, "identifiedBy",
        default_fn=_default_idby,
        initial_value=loaded_value,
    )
```

**Important:** person defaults live in the `person_defaults` DB table, not in
`AppConfig`/`config.json`. Always retrieve them via `pd_svc.get_defaults(session)` —
`[0]` for `identified_by`, `[1]` for `recorded_by`. Never use `get_config()` for this.

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

## 6b. Custom dropdown widget pattern

Use this pattern for any field that needs a styled dropdown with keyboard navigation but
is not backed by the `person` table. `type_status_field.py` is the canonical example.
Do **not** use `ui.select` with `new_value_mode` for this purpose — Quasar's QSelect
requires Enter to commit a typed value and does not let you navigate with arrow keys.

### Anatomy

Every custom dropdown widget is built from five elements inside a positionally-anchored
wrapper div:

```
wrap (position:relative, class="custom-dropdown-field ...")
├── inp          ui.input — the text field the user types into
├── sel_display  div.pf-selected-display — shown instead of inp once a value is picked
│   ├── sel_content  ui.html — the selected value (may contain badge HTML)
│   └── ✕ span  — clears the selection and returns to the search state
└── dropdown     div.pf-dropdown — absolutely positioned below inp; hidden by default
    └── (rebuilt on every keystroke as .pf-item divs)
```

The `pf-*` CSS classes are defined in `person_field.py` and shared by all widgets that
follow this pattern. Inject them via `ui.add_head_html(_CSS)` (import from
`person_field.py` or copy verbatim).

### Keyboard navigation

All custom dropdowns get arrow-key and Enter navigation automatically via a single
client-side JS event-delegation handler defined in `person_field._NAV_SCRIPT`.

**Rules to follow so a new widget participates:**

1. The outer wrapper div **must** carry the class `custom-dropdown-field`:

   ```python
   wrap = ui.element("div").style("position:relative").classes(f"custom-dropdown-field {classes}")
   ```

2. The dropdown container **must** use the class `pf-dropdown` (or `tw-dropdown` for
   taxon-search family widgets). Items inside it **must** use `pf-item` (or
   `tw-dropdown-item`).

3. Inject the script **once per page** — it is idempotent via a `window` guard:

   ```python
   from app.ui.person_field import _NAV_SCRIPT
   ui.add_head_html(_NAV_SCRIPT)
   ```

4. Add the active-highlight CSS to your widget's `_CSS` block:

   ```css
   .pf-item.dropdown-item--active { background: rgb(219,234,254) !important; }
   .dark .pf-item.dropdown-item--active { background: rgb(30,41,59) !important; }
   ```

The JS handler listens on the document in capture phase. On ArrowDown/ArrowUp it moves
the `dropdown-item--active` class through the visible `.pf-item` children (wrapping at
both ends). On Enter it clicks the active item; if no item is active but exactly one item
is visible, it clicks that item automatically (useful when the user has typed enough to
narrow to a single match).

### Focus → show behaviour

To make the dropdown open as soon as the user tabs into the field (so arrow keys work
immediately without typing first), add a focus handler:

```python
inp.on("focus", lambda _: _update_dropdown(inp.value or ""))
```

This is present in `person_field.py` and `type_status_field.py`. Omit it for async
widgets (e.g. taxon search) where showing results on every focus would trigger unwanted
API calls.

### Blur guard — orphaned input text

After the dropdown closes, the user may have typed text that was never selected. Always
pair the 0.2 s blur delay with an orphaned-text guard (see §6 for the full explanation):

```python
async def _on_blur(_) -> None:
    await asyncio.sleep(0.2)          # let dropdown click register first
    dropdown.style("display:none")
    if _value[0] is None and inp.value:
        inp.value = ""                # wipe text that was never committed
```

### Free-text vs. selection-only

| Mode | Behaviour |
|---|---|
| **Selection-only** (person field) | User must pick from dropdown; blur with no pick wipes text |
| **Free-text allowed** (type-status field) | Custom entry appears as a `✎`-badged item at the top of the dropdown; picking it commits the typed text |

For free-text widgets, the custom item is built in `_update_dropdown` whenever the typed
text is not in the predefined list:

```python
if term.strip() and term.strip() not in PREDEFINED:
    custom_html = f'<span class="ts-custom-badge">✎</span>{escape(term.strip())}'
    item = ui.element("div").classes("pf-item pf-item--new")
    with item:
        ui.html(custom_html)
    item.on("click", lambda _, t=term.strip(), h=custom_html: _enter_selected(h, t))
```

The `✎` badge (amber, `.ts-custom-badge`) is defined in `type_status_field.py` and
signals "custom / non-standard value". Do not reuse the blue `✚ add` badge for this
purpose — that badge is reserved for "will create a new DB row" (§3).

### State dict

Every custom dropdown widget must return at minimum:

```python
{"get_value": callable, "set_value": callable}
```

`get_value()` returns the clean string value (no badge prefix) or `None`.
`set_value(val)` sets the field programmatically — used when loading an existing record.
Add `"commit": callable` only if the widget creates DB rows on save (person field pattern).

### Checklist for a new custom dropdown widget

- [ ] Outer wrapper has `custom-dropdown-field` class
- [ ] Dropdown container uses `pf-dropdown` class; items use `pf-item`
- [ ] `ui.add_head_html(_CSS)` and `ui.add_head_html(_NAV_SCRIPT)` called in the builder
- [ ] Active-highlight CSS in `_CSS` block
- [ ] Blur handler has 0.2 s delay + orphaned-text guard
- [ ] Focus handler added if the field should open on tab-focus (static options only)
- [ ] Returns `{"get_value", "set_value"}` state dict

---

## Media button + popup (media_panel.py)

`build_media_button(session_factory, *, target_kind, target_id_getter=None, staged=False,
on_change=None, icon, tooltip)` renders a compact **icon button with a count badge** that
opens a popup gallery — the progressive-disclosure entry point for the infrequently-used
media feature (the gallery is one click away; the badge shows the attachment count).
`target_kind` ∈ `{"collection_object", "collecting_event", "biological_association"}`.
Returns `{button, refresh, has_content, commit, clear, staged_items}`.

**Two modes:**
- **Bound** (`staged=False`, Records): every mutation writes straight to the DB via
  `media_svc` (add/update/set_primary/delete); the count comes from
  `media_svc.count_attachments`.
- **Staged** (`staged=True`, Specimen Digitization): the record doesn't exist yet, so files
  are written to the content-addressed store immediately (`store_bytes`) and held in an
  in-memory list; thumbnails still render via `/media/<rel>`. On Save the host calls
  `commit(session, target_id)` → `media_svc.attach_stored(...)` per item **inside the save
  transaction** (atomic). The host also wires `has_content()` into its unsaved-changes check
  and `clear()` into save / mode-switch resets.

**Popup contents:**
- **Batch upload** — `ui.upload(multiple, auto_upload, on_upload=…)`; `on_upload` fires once
  per file, covering single and multi-file selection in one action. (Do **not** also wire
  `on_multi_upload` — both firing double-processes each file.)
- **Gallery** — flex-wrap of fixed-width cards: images show a `/media/<rel>` thumbnail
  (click → open full), other kinds an Audubon-category icon + download link. Each card has a
  category `ui.select`, a primary star, an edit (pencil) button, delete, and caption/licence
  lines. A category **filter** select narrows the gallery.
- **Details (pencil → dialog):** **rightsHolder** (a `person_field`, Tier-2 — push_pin
  *inside* the field inserts `person_defaults.default_rights_holder_id`), **licence**
  (`vocab.LICENSE_OPTIONS`, Tier-2 — push_pin inserts `config.default_license`), and the
  caption. No title/creator (deliberately trimmed — rightsHolder suffices). Save commits the
  person (`commit()` → id) then, bound, `update_media(...)` + `update_attachment(caption)`;
  staged, it updates the in-memory entry.

**Snapshot-before-render:** attachments + their `media` are read into plain dicts inside a
session so the gallery renders after the session closes (no DetachedInstanceError).

**Placement:** Records — a specimen-media button (specimen form), an event-media button (in
the Collecting Event card header, both the specimen view and the standalone Event form), and
a per-association button on each association row. Digitize — staged specimen, event, and
per-association media buttons (hidden in mounting). Files are served via
`app.add_media_files("/media", media_dir())` (registered in `main.py`), range-request aware
for audio/video.

## External resource identifier button + popup (external_id_panel.py)

`build_external_id_button(session_factory, *, target_kind, target_id_getter=None,
staged=False, staged_store=None, on_change=None, tooltip)` mirrors the media button: a
link-icon button with a count badge opens a popup. `target_kind` ∈ `{"collection_object",
"biological_association"}`. Returns `{button, refresh, has_content, commit, clear,
staged_items}`; bound + staged modes work exactly as the media button.

- **Deliberately minimal:** the popup lists existing identifiers (each the URI as a
  clickable link + delete) and a **single "Resource identifier (URI)" input**. No source
  dropdown, no label field — the user pastes only the URI. `source` is left unset (nullable
  column kept for future flexibility).
- **Modal buttons** follow the app convention: **Abort** (flat) + **Save & close**
  (secondary, adds the entered URI then closes). Deletes apply immediately.
- **Placement:** Records — a button at the lower-right of the specimen card (beside the
  media button) and on each association row. Digitize — staged specimen + per-association
  buttons, committed on Save (per-association via `finalize_specimen`'s returned ids).

## CatalogNumber and printing workflow

CatalogNumber format: `"collectionCode" + "-" + 5-digit zero-padded ascending number`
(e.g. `JJPC-00001`, `JJPC-03963`). Generated via `id_svc.reserve_sequential_codes()`.

Code label layout: one QR-code; collection code prefix on one line; the 5-digit number on
the next line in a larger size.

### CatalogNumber lifecycle

Codes are created in two ways:
1. **Plain generation** — Labels tab, reserve a batch, print, pin physically.
2. **Mounting Session** — codes generated atomically at save time alongside the DB records.

Normal Specimen Digitization does **not** send anything to the print queue.

### Mounting Session mode

A special mode of the Digitize tab. The collecting event section, `recordedBy`, and
biological associations are shared with normal mode (same widgets, same DOM). The mode
toggle swaps visibility between:

- **Standard mode**: taxon/sex/count/etc. fields + "Save specimen" button.
- **Mounting Session**: "Specimens to be labeled" card + "Save Specimens and Print labels"
  button (`mounting_session.py`).

Toggling wipes all unsaved fields (both modes) for consistency.

#### Specimens to be labeled card

Each row represents one specimen. Rows are added/removed dynamically. Per-row fields:

| Field | Tier | Default |
|---|---|---|
| `n` (individual count) | 1 | `1` |
| `preparations` | 1 | `pinned` |
| `lifeStage` | 1 | `adult` |
| `disposition` | 1 | `in collection` (hardcoded at save) |
| `basisOfRecord` | 1 | `PreservedSpecimen` (hardcoded at save) |
| `institutionCode` | 3 | from config |
| `collectionCode` | 3 | from config |
| identification | per-row modal | none (required before save) |

The catalog number is shown as `[auto]` and assigned at save time.

#### Row identification display

When an identification is set, the row shows:

- A button with the taxon name and a ✓ icon (click to re-open the modal).
- A secondary line in muted text with all non-empty metadata, dot-separated:
  `sex · typeStatus · qualifier · det. Firstname Lastname · dateIdentified`

#### Set identification modal

Contains all fields of the identification form:

| Field | Widget |
|---|---|
| taxon | `build_taxon_search` (local + TaxonWorks) |
| `identifiedBy` | `build_person_field` with push_pin default |
| `dateIdentified` | `ui.input` + `attach_date_validation(no_future=True)` |
| `sex` | `ui.select` (`_SEX_OPTIONS`) |
| `typeStatus` | `build_type_status_field` |
| `identificationQualifier` | `ui.input` |
| `remarks` | `ui.input` |

Buttons: **Cancel** / **Apply to all below** / **Apply**. "Apply to all below" copies the
identification to all rows from the current one downward — the fast path for a vial where
all specimens are the same species. A copy-from-previous (↑) shortcut and copy-to-all-below
(↓) shortcut also appear inline on rows.

#### Save behaviour

One transaction. `id_svc.reserve_sequential_codes()` generates all codes at once. The loop
creates one `collecting_event` on the first iteration and reuses its ID for all subsequent
specimens. Each specimen is finalised through the shared seam
`services.specimens.finalize_specimen(..., queue_labels=True, source=SOURCE_MOUNTING)`, which
binds the reserved code and queues **data + identifier + determination** labels for that
specimen under one shared print group. Biological associations (shared from the bio section)
are applied to every specimen row. Mounting is the only create mode that queues labels — see
CLAUDE.md → "Print-queue policy by create mode" for the authoritative per-mode policy.

### Grouped print sheet layout

The print queue (all sources, not just Mounting) renders as a **grouped, column-aligned
sheet** (`labels.py::grouped_sheet`, fed by `print_queue.py::queued_groups`):

- **Groups = queue additions.** Rows enqueued in one operation share a `print_group_id` +
  a `source` header (`SOURCE_MOUNTING` "Mounting Session", `SOURCE_IDENTIFIERS`
  "New identifiers", `SOURCE_REPRINT` "Reprint"). Allocate the id once per batch with
  `next_print_group_id(session)` (columns added in migration 0028). Groups flow as
  inline-block boxes that wrap, separated by a large gap.
- **Within a group, one column per specimen**, with bands stacked top→bottom: **data /
  identifier / determination**. A specimen's own labels touch (no gap) so they stay
  associated while cutting; specimens are separated by a small gap. Built as an HTML
  `<table>` per chunk (WeasyPrint's grid/inline-block sizing is unreliable; tables are not).
  Wide groups wrap at `_LABELS_PER_ROW` columns. Gap/border metrics are named constants in
  `labels.py` — tune by eye against a real PDF.
- **Column reconstruction:** a data/determination row joins its column by
  `collection_object_id`; an identifier row joins by its label code's `collection_object_id`
  (set at assign time), or stands alone if the code is reserved-but-unassigned (a pre-print
  identifier batch → an identifier-only group).
- **Timestamp + archival:** the sheet prints a small "Printed: …" timestamp; on print, the
  PDF is saved to `config.printed_pdf_dir` (default `data/printed_labels/`,
  `config.printed_pdf_dir()` resolves + creates it) as `labels_<YYYYMMDD-HHMMSS>.pdf` before
  the queue is cleared, for reprint/audit.

**Planned (not built):** re-printing existing records by sending them to the print queue.
The `enqueue_*` services are standalone, so this is an "enqueue for an existing
`collection_object`, no `assign_code`" path (its own group, `SOURCE_REPRINT`);
`finalize_specimen` is create-time only and need not change.

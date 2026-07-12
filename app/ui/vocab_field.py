"""Generic single-name controlled-vocabulary field widget.

build_vocab_field(session_factory, vocab, label, ...) -> dict

The exact same dropdown UX as the person field (build_person_field) — a ui.input
with a custom dropdown that offers "✚ add <typed text>" plus existing matches, and
no free-text escape — but driven by any ``Vocabulary`` (app/services/vocab.py)
instead of the person table. It reuses person_field's shared CSS + keyboard-nav
script (the `.custom-dropdown-field` / `.pf-*` classes), so styling stays in one
place.

get_value() returns the clean name (or None). commit(session) ensures the name
exists in the vocab table and returns its **id** (the FK value to store). Call it
inside the save transaction, before the main write — exactly like person commit.
"""
from __future__ import annotations

import asyncio
import html as _html

from nicegui import ui

# Reuse the person field's dropdown CSS + keyboard-nav script (single source).
from app.ui.person_field import _CSS, _NAV_SCRIPT

# The ISO-code chip on a code-bearing vocab row ("Limburg  NL-LI"). It lives here, not in
# person_field's shared _CSS: a person has no ISO code, and only this widget renders one.
_CODE_CSS = """
<style>
.pf-code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .70rem; letter-spacing: .03em;
    color: rgba(0,0,0,.45); background: rgba(0,0,0,.05);
    border-radius: 3px; padding: 0 4px; margin-left: 6px; vertical-align: middle;
}
.dark .pf-code { color: rgba(255,255,255,.55); background: rgba(255,255,255,.08); }
</style>
"""


def build_vocab_field(
    session_factory,
    vocab,
    label: str,
    *,
    default_fn=None,
    initial_value: str | None = None,
    on_change=None,
    classes: str = "flex-1",
) -> dict:
    """Render a controlled-vocabulary dropdown field into the current context.

    Parameters mirror build_person_field; ``vocab`` is a ``Vocabulary`` instance
    (provides .options(session) and .get_or_create(session, name))."""
    ui.add_head_html(_CSS)
    ui.add_head_html(_NAV_SCRIPT)
    ui.add_head_html(_CODE_CSS)

    with session_factory() as s:
        _entries: list[tuple[str, str | None]] = vocab.entries(s)
    # Names only — what the user types is matched against these, and the "✚ add" badge
    # appears when the typed text is not among them.
    # Read-only state. Clearing a value IS an edit, so it must be refused while the field
    # is read-only — see set_readonly().
    _ro = [False]
    _known: set[str] = {n for n, _ in _entries}
    _value: list[str | None] = [None]
    # The ISO code of the *picked row*. Two rows can share a name (Limburg BE-VLI /
    # NL-LI), so the name alone does not identify the pick — the code disambiguates it
    # at save time. None for an uncoded row, a free-typed new name, or a plain vocab.
    _code: list[str | None] = [None]

    wrap = ui.element("div").style("position:relative").classes(f"custom-dropdown-field {classes}")

    with wrap:
        inp = (
            ui.input(label, value="")
            .props("outlined dense clearable")
            .classes("w-full")
        )
        sel_display = ui.element("div").classes("pf-selected-display").style("display:none")
        with sel_display:
            sel_content = ui.html("").classes("pf-selected-content")
            clear_btn = ui.html('<span class="pf-clear-btn" title="Clear">✕</span>')
            clear_btn.on("click", lambda _: _clear())
        dropdown = ui.element("div").classes("pf-dropdown").style("display:none")

        pin_btn = None
        if default_fn is not None:
            pin_btn = (
                ui.button("", icon="push_pin")
                .props("flat dense round size=xs")
                .tooltip("Insert default")
                .on_click(lambda: _do_default())
                .style("position:absolute; right:6px; top:50%; "
                       "transform:translateY(-50%); z-index:1")
            )

    # ── state transitions ─────────────────────────────────────────────────────

    def _enter_selected(display_html: str, clean: str, *, notify: bool = True,
                        code: str | None = None) -> None:
        _value[0] = clean
        _code[0] = code
        sel_content.set_content(display_html)
        sel_display.style("display:flex")
        inp.set_visibility(False)
        dropdown.style("display:none")
        if pin_btn:
            pin_btn.set_visibility(False)
        if notify and on_change:
            on_change()

    def _clear(notify: bool = True) -> None:
        if _ro[0]:
            return                      # read-only: clearing is an edit (see set_readonly)
        _value[0] = None
        _code[0] = None
        sel_content.set_content("")
        sel_display.style("display:none")
        inp.set_visibility(True)
        inp.value = ""
        dropdown.style("display:none")
        if pin_btn:
            pin_btn.set_visibility(True)
        if notify and on_change:
            on_change()

    def _do_default() -> None:
        if default_fn:
            val = default_fn()
            if val:
                set_value(val)
                if on_change:
                    on_change()

    # ── dropdown ──────────────────────────────────────────────────────────────

    def _sel_html(name: str, code: str | None) -> str:
        """Selected/row markup: the name, with its ISO code as a muted suffix."""
        if not code:
            return _html.escape(name)
        return (f'{_html.escape(name)} '
                f'<span class="pf-code">{_html.escape(code)}</span>')

    def _add_html(name: str, code: str | None = None) -> str:
        """"✚ add <name>" markup — carrying the code when we know it.

        A geocoded value can be new to the vocab *and* carry an ISO code (Overpass tags the
        containing relation with ISO3166-1 / ISO3166-2), and that code is what the new row
        will be created with. Hiding it would show "✚ add Greece" while silently creating
        ("Greece", "GR"). The typed-text row has no code, so it renders unchanged.
        """
        return (f'<span class="pf-new-badge">✚ add</span> {_sel_html(name, code)}')

    def _update_dropdown(term: str) -> None:
        dropdown.clear()
        real = term.strip()
        f = real.lower()
        items: list = []
        with dropdown:
            # Existing matches first, so the auto-highlighted top row is the best
            # match — Enter takes it directly (no ArrowDown needed). One row per vocab
            # ROW, not per name: "Limburg (BE-VLI)" and "Limburg (NL-LI)" are separate
            # picks, and choosing one must be unambiguous at save time.
            for name, code in sorted(_entries, key=lambda e: (e[0], e[1] or "")):
                if not f or f in name.lower():
                    item = ui.element("div").classes("pf-item")
                    with item:
                        ui.html(_sel_html(name, code))
                    item.on("click", lambda _, n=name, c=code:
                            _enter_selected(_sel_html(n, c), n, code=c))
                    items.append(item)
            # "✚ add <typed>" LAST — only when the text isn't already a known name;
            # it becomes the sole (highlighted) row when nothing matches.
            if real and real not in _known:
                add_html = _add_html(real)
                item = ui.element("div").classes("pf-item pf-item--new")
                with item:
                    ui.html(add_html)
                item.on("click", lambda _, r=real, h=add_html: _enter_selected(h, r))
                items.append(item)
        # Highlight the first row so Enter selects it without pressing ArrowDown.
        if items:
            items[0].classes("dropdown-item--active")
        dropdown.style("display:block" if items else "display:none")

    def _on_input_change(e) -> None:
        term = (e.value or "").strip()
        if not term:
            dropdown.style("display:none")
            return
        _update_dropdown(term)

    inp.on_value_change(_on_input_change)
    inp.on("focus", lambda _: _update_dropdown(inp.value or ""))

    async def _on_blur(_) -> None:
        await asyncio.sleep(0.2)
        dropdown.style("display:none")
        if _value[0] is None and inp.value:
            inp.value = ""

    inp.on("blur", _on_blur)

    # ── initial state ─────────────────────────────────────────────────────────

    if initial_value:
        v = initial_value.strip()
        if v:
            _seed_code = next((c for n, c in _entries if n == v), None)
            disp = _sel_html(v, _seed_code) if v in _known else _add_html(v, _seed_code)
            _enter_selected(disp, clean=v, notify=False, code=_seed_code)

    # ── timer refresh ─────────────────────────────────────────────────────────

    def refresh() -> None:
        with session_factory() as s:
            new_entries = vocab.entries(s)
        _entries[:] = new_entries
        _known.clear()
        _known.update(n for n, _ in new_entries)

    ui.timer(2.0, refresh)

    # ── state dict ────────────────────────────────────────────────────────────

    def get_value() -> str | None:
        return _value[0]

    def get_code() -> str | None:
        """ISO code of the picked row (None for uncoded rows / plain vocabs)."""
        return _code[0]

    def set_value(val: str | None, code: str | None = None) -> None:
        """Set the field programmatically (geocode fill, record load, push-pin default).

        When no *code* is given and the name matches exactly one existing row, that row's
        code is adopted; an ambiguous name (two Limburgs) adopts none rather than guessing
        which country the user meant.
        """
        v = (val or "").strip() or None
        if not v:
            _clear(notify=False)
            return
        c = (code or "").strip().upper() or None
        if c is None:
            matches = {mc for mn, mc in _entries if mn == v}
            c = matches.pop() if len(matches) == 1 else None
        disp = _sel_html(v, c) if v in _known else _add_html(v, c)
        _enter_selected(disp, clean=v, notify=False, code=c)

    def commit(session) -> int | None:
        """Ensure the current entry exists in the vocab table; return its id (the
        FK value to store), or None if nothing is selected.

        Passes the picked row's code, so a code-bearing vocab resolves to the exact
        (name, code) row instead of collapsing onto the uncoded one.
        """
        val = _value[0]
        if not val:
            return None
        obj = vocab.get_or_create(session, val, code=_code[0])
        _known.add(val)
        return obj.id

    # Read-only must mean read-only. Quasar's `readonly` does NOT disable the `clearable` ✕,
    # and our own ✕ button has its own click handler, so a reused (read-only) collecting event
    # could still have its fields CLEARED — which silently detached it into a new event with
    # fewer fields filled in. The state is guarded in three places: the clearable prop is
    # removed, the ✕ is hidden, and _clear() refuses outright (a stale click cannot mutate).
    def set_readonly(ro: bool) -> None:
        _ro[0] = ro
        if ro:
            inp.props("readonly")
            inp.props(remove="clearable")
        else:
            inp.props(remove="readonly")
            inp.props("clearable")
        clear_btn.set_visibility(not ro)
        if pin_btn is not None:
            pin_btn.set_enabled(not ro)

    return {
        "get_value":    get_value,
        "get_code":     get_code,
        "set_value":    set_value,
        "commit":       commit,
        "refresh":      refresh,
        "set_readonly": set_readonly,
    }

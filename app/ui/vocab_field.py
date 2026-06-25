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

    with session_factory() as s:
        initial_opts = vocab.options(s)
    _known: set[str] = set(initial_opts.keys())
    _value: list[str | None] = [None]

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
            ui.html('<span class="pf-clear-btn" title="Clear">✕</span>').on(
                "click", lambda _: _clear()
            )
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

    def _enter_selected(display_html: str, clean: str, *, notify: bool = True) -> None:
        _value[0] = clean
        sel_content.set_content(display_html)
        sel_display.style("display:flex")
        inp.set_visibility(False)
        dropdown.style("display:none")
        if pin_btn:
            pin_btn.set_visibility(False)
        if notify and on_change:
            on_change()

    def _clear(notify: bool = True) -> None:
        _value[0] = None
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

    def _update_dropdown(term: str) -> None:
        dropdown.clear()
        real = term.strip()
        f = real.lower()
        has_items = False
        with dropdown:
            if real and real not in _known:
                has_items = True
                add_html = (
                    f'<span class="pf-new-badge">✚ add</span> {_html.escape(real)}'
                )
                item = ui.element("div").classes("pf-item pf-item--new")
                with item:
                    ui.html(add_html)
                item.on("click", lambda _, r=real, h=add_html: _enter_selected(h, r))
            for name in sorted(_known):
                if not f or f in name.lower():
                    has_items = True
                    item = ui.element("div").classes("pf-item")
                    with item:
                        ui.label(name)
                    item.on("click", lambda _, n=name: _enter_selected(_html.escape(n), n))
        dropdown.style("display:block" if has_items else "display:none")

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
            disp = (_html.escape(v) if v in _known
                    else f'<span class="pf-new-badge">✚ add</span> {_html.escape(v)}')
            _enter_selected(disp, clean=v, notify=False)

    # ── timer refresh ─────────────────────────────────────────────────────────

    def refresh() -> None:
        with session_factory() as s:
            new_opts = vocab.options(s)
        _known.clear()
        _known.update(new_opts.keys())

    ui.timer(2.0, refresh)

    # ── state dict ────────────────────────────────────────────────────────────

    def get_value() -> str | None:
        return _value[0]

    def set_value(val: str | None) -> None:
        v = (val or "").strip() or None
        if v:
            disp = (_html.escape(v) if v in _known
                    else f'<span class="pf-new-badge">✚ add</span> {_html.escape(v)}')
            _enter_selected(disp, clean=v, notify=False)
        else:
            _clear(notify=False)

    def commit(session) -> int | None:
        """Ensure the current name exists in the vocab table; return its id (the
        FK value to store), or None if nothing is selected."""
        val = _value[0]
        if not val:
            return None
        obj = vocab.get_or_create(session, val)
        _known.add(val)
        return obj.id

    def set_readonly(ro: bool) -> None:
        if ro:
            inp.props("readonly")
        else:
            inp.props(remove="readonly")
        if pin_btn is not None:
            pin_btn.set_enabled(not ro)

    return {
        "get_value":    get_value,
        "set_value":    set_value,
        "commit":       commit,
        "refresh":      refresh,
        "set_readonly": set_readonly,
    }

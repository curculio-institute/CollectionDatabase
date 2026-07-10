"""Fixed-list custom-dropdown field — the person-field keyboard UX for a CLOSED list.

build_choice_field(options, label, ...) -> dict

Same widget as build_person_field / build_vocab_field (a ui.input with a custom dropdown that
**opens on focus with the first item highlighted**, filters as you type, and lets Enter pick the
highlighted row), but over a hardcoded list of strings — no DB, no FK, no "✚ add" escape (the
list is closed). The selected string IS the value.

Built for identificationQualifier (cf. first → one keystroke), and reusable for any closed
option set that wants the same snap-to-first keyboard flow the native ui.select does not give.
"""
from __future__ import annotations

import asyncio
import html as _html

from nicegui import ui

# Reuse the person field's dropdown CSS + keyboard-nav script (single source).
from app.ui.person_field import _CSS, _NAV_SCRIPT


def build_choice_field(
    options: list[str],
    label: str,
    *,
    initial_value: str | None = None,
    on_change=None,
    classes: str = "",
) -> dict:
    """Render a closed-list dropdown. Returns {get_value, set_value, set_readonly}.

    ``get_value()`` is the chosen string, or None when cleared (a definite / empty choice).
    Blank entries in *options* are ignored — clearing the field (the ✕) is the "none" choice.
    """
    ui.add_head_html(_CSS)
    ui.add_head_html(_NAV_SCRIPT)

    _opts = [o for o in options if o]          # a blank is expressed by clearing, not a row
    _value: list[str | None] = [None]

    wrap = ui.element("div").style("position:relative").classes(
        f"custom-dropdown-field {classes}")
    with wrap:
        inp = ui.input(label, value="").props("outlined dense clearable").classes("w-full")
        sel_display = ui.element("div").classes("pf-selected-display").style("display:none")
        with sel_display:
            sel_content = ui.html("").classes("pf-selected-content")
            ui.html('<span class="pf-clear-btn" title="Clear">✕</span>').on(
                "click", lambda _: _clear())
        dropdown = ui.element("div").classes("pf-dropdown").style("display:none")

    def _enter_selected(clean: str, *, notify: bool = True) -> None:
        _value[0] = clean
        sel_content.set_content(_html.escape(clean))
        sel_display.style("display:flex")
        inp.set_visibility(False)
        dropdown.style("display:none")
        if notify and on_change:
            on_change()

    def _clear(notify: bool = True) -> None:
        _value[0] = None
        sel_content.set_content("")
        sel_display.style("display:none")
        inp.set_visibility(True)
        inp.value = ""
        dropdown.style("display:none")
        if notify and on_change:
            on_change()

    def _update_dropdown(term: str) -> None:
        dropdown.clear()
        f = term.strip().lower()
        items: list = []
        with dropdown:
            for opt in _opts:                  # list order preserved (cf. first)
                if not f or f in opt.lower():
                    item = ui.element("div").classes("pf-item")
                    with item:
                        ui.label(opt)
                    item.on("click", lambda _, o=opt: _enter_selected(o))
                    items.append(item)
        # Highlight the first row so Enter takes it with no ArrowDown — the snap the user wants.
        if items:
            items[0].classes("dropdown-item--active")
        dropdown.style("display:block" if items else "display:none")

    inp.on_value_change(lambda e: _update_dropdown(e.value or ""))
    inp.on("focus", lambda _: _update_dropdown(inp.value or ""))   # open on focus

    async def _on_blur(_) -> None:
        await asyncio.sleep(0.2)               # let a click on a row register first
        dropdown.style("display:none")
        if _value[0] is None and inp.value:
            inp.value = ""

    inp.on("blur", _on_blur)

    if initial_value and initial_value.strip():
        _enter_selected(initial_value.strip(), notify=False)

    def set_readonly(ro: bool) -> None:
        inp.props("readonly") if ro else inp.props(remove="readonly")

    return {
        "get_value":    lambda: _value[0],
        "set_value":    lambda v: (_enter_selected(v.strip(), notify=False)
                                   if v and v.strip() else _clear(notify=False)),
        "set_readonly": set_readonly,
    }

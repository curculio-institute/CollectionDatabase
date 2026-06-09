"""Type-status field widget.

build_type_status_field(label, ...) -> dict

Same visual pattern as person_field: a ui.input with a custom absolutely-
positioned dropdown. Options come from a static predefined list. Free-text
entries are accepted and shown with a ✎ badge in the dropdown and selected
display to distinguish them from standard ICZN terms.

State interface:
    get_value()          -> str | None
    set_value(v)         -> None   (accepts str | None)
"""
from __future__ import annotations

import asyncio
import html as _html

from nicegui import ui

from app.ui.person_field import _NAV_SCRIPT

PREDEFINED = [
    "Holotype", "Paratype", "Lectotype", "Paralectotype", "Neotype", "Syntype",
]

# Reuses .pf-* classes from person_field.py; adds .ts-custom-badge only.
_CSS = """<style>
.ts-custom-badge {
    display: inline-flex; align-items: center; gap: 2px;
    background: rgba(161,98,7,.10); color: rgb(161,98,7);
    border-radius: 4px; padding: 1px 6px; font-size: .72rem;
    font-weight: 600; margin-right: 6px; vertical-align: middle;
    letter-spacing: .02em;
}
.dark .ts-custom-badge { background: rgba(251,191,36,.12); color: rgb(251,191,36); }
</style>"""


def build_type_status_field(
    label: str = "typeStatus",
    *,
    initial_value: str | None = None,
    classes: str = "flex-1",
) -> dict:
    ui.add_head_html(_CSS)
    ui.add_head_html(_NAV_SCRIPT)

    _value: list[str | None] = [None]

    wrap = ui.element("div").style("position:relative").classes(f"custom-dropdown-field {classes}")

    with wrap:
        inp = (
            ui.input(label, value="")
            .props("outlined dense clearable")
            .classes("w-full")
        )

        sel_display = (
            ui.element("div")
            .classes("pf-selected-display")
            .style("display:none")
        )
        with sel_display:
            sel_content = ui.html("").classes("pf-selected-content")
            ui.html('<span class="pf-clear-btn" title="Clear">✕</span>').on(
                "click", lambda _: _clear()
            )

        dropdown = (
            ui.element("div")
            .classes("pf-dropdown")
            .style("display:none")
        )

    # ── state transitions ─────────────────────────────────────────────────────

    def _enter_selected(display_html: str, clean: str, *, notify: bool = True) -> None:
        _value[0] = clean
        sel_content.set_content(display_html)
        sel_display.style("display:flex")
        inp.set_visibility(False)
        dropdown.style("display:none")

    def _clear(notify: bool = True) -> None:
        _value[0] = None
        sel_content.set_content("")
        sel_display.style("display:none")
        inp.set_visibility(True)
        inp.value = ""
        dropdown.style("display:none")

    # ── dropdown ──────────────────────────────────────────────────────────────

    def _display_html(val: str) -> str:
        esc = _html.escape(val)
        if val in PREDEFINED:
            return esc
        return f'<span class="ts-custom-badge">✎</span>{esc}'

    def _update_dropdown(term: str) -> None:
        dropdown.clear()
        f = term.strip().lower()
        has_items = False

        with dropdown:
            matches = [v for v in PREDEFINED if not f or f in v.lower()]
            # Custom entry row (shown when typed text is not an exact predefined match)
            if term.strip() and term.strip() not in PREDEFINED:
                has_items = True
                custom_html = f'<span class="ts-custom-badge">✎</span>{_html.escape(term.strip())}'
                item = ui.element("div").classes("pf-item pf-item--new")
                with item:
                    ui.html(custom_html)
                item.on("click", lambda _, t=term.strip(), h=custom_html: _enter_selected(h, t))
            for val in matches:
                has_items = True
                item = ui.element("div").classes("pf-item")
                with item:
                    ui.label(val)
                item.on("click", lambda _, v=val: _enter_selected(_html.escape(v), v))

        dropdown.style("display:block" if has_items else "display:none")

    def _on_input_change(e) -> None:
        term = e.value or ""
        if not term.strip():
            # Show full list when input is empty but focused
            _update_dropdown("")
            return
        _update_dropdown(term)

    inp.on_value_change(_on_input_change)

    async def _on_blur(_) -> None:
        await asyncio.sleep(0.2)
        dropdown.style("display:none")
        # If user typed but didn't pick from dropdown, discard the text.
        if _value[0] is None and inp.value:
            inp.value = ""

    inp.on("blur", _on_blur)
    inp.on("focus", lambda _: _update_dropdown(inp.value or ""))

    # ── initial state ─────────────────────────────────────────────────────────

    if initial_value:
        v = initial_value.strip()
        if v:
            _enter_selected(_display_html(v), v, notify=False)

    # ── state dict ────────────────────────────────────────────────────────────

    def get_value() -> str | None:
        return _value[0]

    def set_value(val: str | None) -> None:
        v = (val or "").strip() or None
        if v:
            _enter_selected(_display_html(v), v, notify=False)
        else:
            _clear(notify=False)

    return {"get_value": get_value, "set_value": set_value}

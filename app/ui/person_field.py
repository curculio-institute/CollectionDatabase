"""Person field widget.

build_person_field(session_factory, label, ...) -> dict

Mirrors the taxon_search widget pattern: a ui.input with a custom
absolutely-positioned dropdown div.  Results come from the local person table.

Dropdown shows:
  1. "✚ add <typed text>"  — styled with .pf-new-badge — when the text is not
     an exact match in the person table.
  2. Matching existing persons (plain text rows).

Selection is only possible from the dropdown; there is no free-text entry.
After selection the input is hidden and a styled selected-display div takes its
place (same pattern as taxon_search).  A ✕ button returns to the search state.

get_value() always returns the clean name (no badge prefix).
commit(session) creates a new Person row if the current value is not yet in
the person table.  Call inside the tab's save transaction before the main write.
"""
from __future__ import annotations

import asyncio
import html as _html

from nicegui import ui

import app.services.persons as persons_svc

_CSS = """<style>
/* ── person field ── */
.pf-dropdown {
    position: absolute; left: 0; right: 0; top: calc(100% + 2px); z-index: 9999;
    background: rgb(255,255,255); border: 1px solid rgb(203,213,225);
    border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.10);
    max-height: 240px; overflow-y: auto;
}
.dark .pf-dropdown {
    background: rgb(38,38,38); border-color: rgb(55,55,55);
    box-shadow: 0 8px 24px rgba(0,0,0,.4);
}
.pf-item {
    padding: 8px 16px; cursor: pointer; font-size: .9rem;
    border-bottom: 1px solid rgb(243,244,246);
}
.dark .pf-item { border-color: rgb(48,48,48); }
.pf-item:last-child { border-bottom: none; }
.pf-item:hover { background: rgb(245,247,251); }
.dark .pf-item:hover { background: rgb(48,48,48); }
.pf-item--new { background: rgba(3,105,161,.04); }
.dark .pf-item--new { background: rgba(14,165,233,.06); }
.pf-item--new:hover { background: rgba(3,105,161,.10) !important; }
.dark .pf-item--new:hover { background: rgba(14,165,233,.13) !important; }
.pf-new-badge {
    display: inline-flex; align-items: center; gap: 2px;
    background: rgba(3,105,161,.12); color: rgb(3,105,161);
    border-radius: 4px; padding: 1px 6px; font-size: .72rem;
    font-weight: 600; margin-right: 6px; vertical-align: middle;
    letter-spacing: .02em;
}
.dark .pf-new-badge { background: rgba(14,165,233,.15); color: rgb(14,165,233); }
.pf-selected-display {
    align-items: center; gap: 8px;
    border: 1px solid rgba(0,0,0,0.24); border-radius: 4px;
    min-height: 40px; padding: 4px 8px 4px 12px;
    box-sizing: border-box; width: 100%; cursor: default;
}
.dark .pf-selected-display { border-color: rgba(255,255,255,0.24); }
.pf-selected-display:hover { border-color: rgba(0,0,0,0.38); }
.dark .pf-selected-display:hover { border-color: rgba(255,255,255,0.38); }
.pf-selected-content { flex: 1; min-width: 0; font-size: .9rem; }
.pf-clear-btn {
    cursor: pointer; color: rgb(156,163,175);
    padding: 2px 4px; flex-shrink: 0; line-height: 1;
}
.pf-clear-btn:hover { color: rgb(220,38,38); }
</style>"""


def build_person_field(
    session_factory,
    label: str,
    *,
    default_fn=None,
    initial_value: str | None = None,
    on_change=None,
    classes: str = "flex-1",
) -> dict:
    """Render a person-search field into the current NiceGUI context.

    Parameters
    ----------
    default_fn:
        Zero-argument callable returning the push_pin default string. Called
        at click time.  If None, no push_pin button is rendered.
    initial_value:
        Pre-populate the field in selected state (e.g. loaded from DB).
    on_change:
        Called whenever the selected value changes (including clear).
    classes:
        CSS classes applied to the outer wrapper div.  Default "flex-1".
    """
    ui.add_head_html(_CSS)

    with session_factory() as s:
        initial_opts = persons_svc.person_options(s)
    _known: set[str] = set(initial_opts.keys())
    _value: list[str | None] = [None]

    # ── layout ────────────────────────────────────────────────────────────────
    wrap = ui.element("div").style("position:relative").classes(classes)

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

    pin_btn = None
    if default_fn is not None:
        pin_btn = (
            ui.button("", icon="push_pin")
            .props("flat dense round size=xs")
            .tooltip("Insert default name")
            .on_click(lambda: _do_default())
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
                    f'<span class="pf-new-badge">✚ add</span>'
                    f" {_html.escape(real)}"
                )
                item = ui.element("div").classes("pf-item pf-item--new")
                with item:
                    ui.html(add_html)
                item.on("click", lambda _, r=real, h=add_html: _select_new(r, h))

            for name in sorted(_known):
                if not f or f in name.lower():
                    has_items = True
                    item = ui.element("div").classes("pf-item")
                    with item:
                        ui.label(name)
                    item.on("click", lambda _, n=name: _select_existing(n))

        dropdown.style("display:block" if has_items else "display:none")

    def _select_existing(name: str) -> None:
        _enter_selected(_html.escape(name), clean=name)

    def _select_new(name: str, display_html: str) -> None:
        _enter_selected(display_html, clean=name)

    def _on_input_change(e) -> None:
        term = (e.value or "").strip()
        if not term:
            dropdown.style("display:none")
            return
        _update_dropdown(term)

    inp.on_value_change(_on_input_change)

    async def _on_blur(_) -> None:
        await asyncio.sleep(0.2)   # let dropdown item click register first
        dropdown.style("display:none")
        # If the user tabbed out without selecting from the dropdown, the
        # input may still contain typed text but _value[0] was never set.
        # Clear it so the field is visibly and logically empty.
        if _value[0] is None and inp.value:
            inp.value = ""

    inp.on("blur", _on_blur)

    # ── initial state ─────────────────────────────────────────────────────────

    if initial_value:
        v = initial_value.strip()
        if v:
            if v in _known:
                disp = _html.escape(v)
            else:
                disp = f'<span class="pf-new-badge">✚ add</span> {_html.escape(v)}'
            _enter_selected(disp, clean=v, notify=False)

    # ── timer refresh ─────────────────────────────────────────────────────────

    def refresh() -> None:
        with session_factory() as s:
            new_opts = persons_svc.person_options(s)
        _known.clear()
        _known.update(new_opts.keys())
        # sel.options no longer exists; dropdown is rebuilt on next keystroke.

    ui.timer(2.0, refresh)

    # ── state dict ────────────────────────────────────────────────────────────

    def get_value() -> str | None:
        return _value[0]

    def set_value(val: str | None) -> None:
        """Set to a clean name (no badge prefix) from outside, e.g. DB load."""
        v = (val or "").strip() or None
        if v:
            if v in _known:
                _enter_selected(_html.escape(v), clean=v, notify=False)
            else:
                disp = f'<span class="pf-new-badge">✚ add</span> {_html.escape(v)}'
                _enter_selected(disp, clean=v, notify=False)
        else:
            _clear(notify=False)

    def commit(session) -> None:
        """Ensure the current name exists in the person table.

        Uses get_or_create_person so it is safe to call from multiple widgets
        in the same transaction with the same name — the second call finds the
        row flushed by the first instead of hitting the unique constraint.
        Call inside the tab's save transaction before writing the main record.
        """
        val = _value[0]
        if not val or val in _known:
            return
        persons_svc.get_or_create_person(session, full_name=val)
        _known.add(val)

    return {
        "get_value": get_value,
        "set_value": set_value,
        "commit":    commit,
        "refresh":   refresh,
    }

"""Check panel: the box beside a form that says what is still missing or about to be lost.

Modelled on TaxonWorks' "Soft Validation" box — a warning-edged card to the right of the
form, one row per finding, with an optional one-click fix — but in this app's own layout
and colour tokens. It never blocks anything: it only makes the state visible *before*
Save, so the Save-time dialogs are a second line, not the first.

The panel knows nothing about the form it sits beside. The caller hands it one function
returning the current findings; a ``ui.timer`` polls that function and the card is
re-rendered only when the findings actually change (value-based, like the unsaved-changes
guard — a map pick, a push-pin or a geocode fill fires no DOM event Python would see).

A finding is a dict::

    {"group": "Collecting Event",          # heading the row is listed under
     "text":  "Date is missing",           # the row itself
     "key":   "ev-missing-Date",           # stable identity (re-render trigger)
     "fix":   ("Add", callable) | None,    # small solid button, like TW's "Fix"
     "on_click": callable | None,          # makes the text itself a link
     "tooltip": str | None}                # for the link
"""
from __future__ import annotations

from typing import Callable

from nicegui import ui

# Sticks below the fixed app header (--tp-header-h is measured by main.py). Sticky only
# works here because main.py turns the Digitize tab panel into a non-scroll-container —
# see ".tp-main-panels" there. Below 1100px there is no room beside the form, so the
# panel floats over its bottom-right corner instead of being pushed under it, where
# nobody would see it.
_CSS = """
<style>
  .tp-check-col   { flex:0 0 300px; width:300px; align-self:flex-start;
                    position:sticky; top:calc(var(--tp-header-h, 0px) + 16px);
                    margin-top:1.5rem;
                    max-height:calc(100vh - var(--tp-header-h, 0px) - 32px);
                    overflow-y:auto; }
  .tp-check       { width:100%; padding:14px 16px 16px !important;
                    border-left:4px solid var(--tp-warning, #f59e0b) !important; }
  .tp-check.tp-check-empty { visibility:hidden; }
  .tp-check-title { font-size:1rem; font-weight:600; }
  .tp-check-group { font-size:.75rem; font-weight:700; letter-spacing:.08em;
                    text-transform:uppercase; color:var(--tp-base-soft);
                    margin-top:12px; }
  .tp-check-row   { display:flex; align-items:flex-start; gap:8px; width:100%;
                    font-size:.86rem; line-height:1.35; margin-top:7px; }
  .tp-check-row .q-icon { color:var(--tp-warning, #f59e0b); margin-top:1px; }
  .tp-check-text  { flex:1; min-width:0; overflow-wrap:anywhere; }
  .tp-check-link  { color:var(--tp-secondary); cursor:pointer; }
  .tp-check-link:hover { text-decoration:underline; }
  @media (max-width: 1100px) {
    .tp-check-col { position:fixed; top:auto; right:12px; bottom:72px; z-index:5000;
                    margin-top:0; max-height:45vh;
                    box-shadow:0 4px 18px rgba(0,0,0,.28); border-radius:8px; }
    .tp-check-col:has(.tp-check-empty) { display:none; }
  }
</style>
"""


def build_check_panel(*, findings_fn: Callable[[], list[dict]],
                      title: str = "Checks", interval: float = 1.0) -> dict:
    """Build the panel in the current container and start polling ``findings_fn``.

    Returns a handle: ``refresh()`` re-evaluates now (call it from a write path that
    changes what the findings depend on, instead of waiting for the next tick).
    """
    ui.add_head_html(_CSS)

    last_sig: list = [None]

    with ui.element("div").classes("tp-check-col"):
        card = ui.card().classes("tp-check tp-check-empty shadow-sm")

    def _render(findings: list[dict]) -> None:
        card.clear()
        card.classes(remove="tp-check-empty") if findings \
            else card.classes(add="tp-check-empty")
        with card:
            ui.label(title).classes("tp-check-title")
            group = None
            for f in findings:
                if f["group"] != group:
                    group = f["group"]
                    ui.label(group).classes("tp-check-group")
                with ui.element("div").classes("tp-check-row"):
                    ui.icon("warning", size="xs")
                    text = ui.label(f["text"]).classes("tp-check-text")
                    if f.get("on_click"):
                        text.classes(add="tp-check-link")
                        text.on("click", lambda _e, fn=f["on_click"]: fn())
                        if f.get("tooltip"):
                            text.tooltip(f["tooltip"])
                    if f.get("fix"):
                        label, fn = f["fix"]
                        ui.button(label, on_click=lambda _e, fn=fn: _run_fix(fn)) \
                            .props("dense unelevated no-caps size=sm color=positive")

    def _run_fix(fn) -> None:
        fn()
        _refresh()      # the fixed row disappears now, not on the next tick

    def _refresh() -> None:
        findings = findings_fn()
        sig = tuple((f["group"], f["key"], f["text"]) for f in findings)
        if sig != last_sig[0]:
            last_sig[0] = sig
            _render(findings)

    _refresh()
    ui.timer(interval, _refresh)
    return {"refresh": _refresh}

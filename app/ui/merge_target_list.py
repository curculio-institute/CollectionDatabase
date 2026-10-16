"""The "entry to keep" picker of a merge dialog — search box + scrolling radio list.

Defined once for every merge dialog (persons, each vocabulary, annotation terms,
biological relationships): any of these lists can run to hundreds of rows, so all of them
filter by typing, scroll inside the dialog and render at most ``_MERGE_LIST_CAP`` rows.
"""
from __future__ import annotations

from nicegui import ui

_MERGE_LIST_CAP = 100   # rows rendered at once; the search narrows the rest


def build_merge_target_list(on_pick) -> dict:
    """The "entry to keep" picker of a merge dialog: search box + scrolling radio list.

    Defined once for every merge dialog in this tab (persons, each vocabulary) — any of
    these lists can run to hundreds of rows, so all of them filter by typing, scroll
    inside the dialog and render at most ``_MERGE_LIST_CAP`` rows at a time.

    Build it inside the dialog card. ``on_pick(id)`` is called on a row click. Returns
    ``show(others, selected_id, reset_search=False)`` — ``others`` is ``{id: label}``.
    """
    shown: dict = {"others": {}, "selected": None}

    search = ui.input(placeholder="Search the entry to keep…") \
        .props("dense outlined clearable debounce=150").classes("w-full mt-3")
    with search.add_slot("prepend"):
        ui.icon("search")
    box = ui.element("div").classes("w-full mt-1 overflow-y-auto") \
        .style("max-height:16rem")

    def _render() -> None:
        needle = (search.value or "").strip().casefold()
        hits = [(oid, lbl) for oid, lbl in shown["others"].items()
                if needle in lbl.casefold()]
        # The selected entry must always be visible: one that sorts beyond the cap is
        # moved to the top instead of being armed for a merge nobody can see.
        sel = shown["selected"]
        if sel in [oid for oid, _ in hits[_MERGE_LIST_CAP:]]:
            hits = ([(sel, shown["others"][sel])]
                    + [(oid, lbl) for oid, lbl in hits if oid != sel])
        box.clear()
        with box:
            if not hits:
                ui.label("No entry matches.").classes("text-sm px-3 py-2") \
                    .style("color:var(--tp-base-soft)")
            if len(hits) > _MERGE_LIST_CAP:
                ui.label(f"Showing {_MERGE_LIST_CAP} of {len(hits)} — type to narrow.") \
                    .classes("text-xs px-3 py-1").style("color:var(--tp-base-soft)")
            for oid, lbl in hits[:_MERGE_LIST_CAP]:
                selected = oid == shown["selected"]
                row = ui.element("div").classes(
                    "w-full flex items-center gap-2 px-3 py-2 rounded cursor-pointer "
                    + ("outline outline-1 outline-secondary bg-secondary/5" if selected
                       else "hover:bg-slate-50 dark:hover:bg-white/5")
                )
                with row:
                    ui.icon("radio_button_checked" if selected
                            else "radio_button_unchecked") \
                        .style("color:var(--tp-secondary); font-size:1.1rem; flex-shrink:0")
                    ui.label(lbl).classes("text-sm truncate")
                row.on("click", lambda _, o=oid: on_pick(o))

    search.on_value_change(lambda _: _render())

    def show(others: dict, selected_id, *, reset_search: bool = False) -> None:
        shown.update(others=others, selected=selected_id)
        if reset_search and search.value:
            search.value = ""       # its change handler renders
        else:
            _render()

    return {"show": show}

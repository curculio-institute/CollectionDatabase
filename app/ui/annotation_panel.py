"""Annotation button + popup (spec "Entering annotations").

``build_annotation_button(...)`` renders a compact ``sell`` icon button with a count
badge that opens a popup: allowed tags as checkboxes, data attributes as pick + value.
Same three modes as ``external_id_panel.py``:

- **staged** (Digitize / Mounting): held in ``staged_store``; ``commit`` ADDS them on Save
  (``annotations.add_annotations`` — a reused event keeps what it has; a contradicting
  value refuses the Save).
- **deferred** (Records cards): edits held until the card's Save; ``commit`` REPLACES the
  record's set (``annotations.set_annotations``).
- **bound** (Records association rows, like the media / identifier buttons beside them):
  "Save & close" writes at once.

A term no longer offered for this target still shows when attached (marked) and can be
removed, but not newly added. Local-only terms carry a "local" badge.
"""
from __future__ import annotations

from typing import Callable, Optional

from nicegui import ui

import app.services.annotations as ann_svc
from app.services.annotations import AnnotationItem
from app.ui.annotation_chips import CSS as CHIP_CSS, chip_html


def collect_items(work: dict, terms: dict) -> list[dict]:
    """Working copy ``{term_id: value}`` -> items, in a stable order (tags first)."""
    items = []
    for term_id in sorted(work, key=lambda i: (terms.get(i, {}).get("kind") != "tag", i)):
        t = terms.get(term_id)
        if t is None:
            raise ValueError(f"An annotation refers to term {term_id}, which no longer "
                             f"exists — remove it and add it again.")
        value = work[term_id]
        if t["kind"] == "data_attribute":
            try:
                value = ann_svc.clean_value(value)
            except ValueError as exc:
                raise ValueError(f"{t['name']}: {exc}") from exc
        else:
            value = None
        items.append({"term_id": term_id, "value": value})
    return items


def _as_items(items: list[dict]) -> list[AnnotationItem]:
    return [AnnotationItem(i["term_id"], i["value"]) for i in items]


def build_annotation_button(
    session_factory,
    *,
    target: str,
    target_id_getter: Optional[Callable[[], Optional[int]]] = None,
    staged: bool = False,
    staged_store: Optional[list] = None,
    deferred: bool = False,
    on_change: Optional[Callable[[], None]] = None,
    tooltip: str = "Annotations",
) -> dict:
    ui.add_head_html(CHIP_CSS)
    staged_items: list[dict] = staged_store if staged_store is not None else []
    pending: dict = {"items": None}          # deferred: None = untouched since load

    def _tid() -> Optional[int]:
        return target_id_getter() if target_id_getter else None

    def _db_items() -> list[dict]:
        tid = _tid()
        if tid is None:
            return []
        with session_factory() as s:
            return [{"term_id": a.term_id, "value": a.value}
                    for a in ann_svc.annotations_for(s, target, tid)]

    def _current() -> list[dict]:
        if staged:
            return list(staged_items)
        if deferred and pending["items"] is not None:
            return list(pending["items"])
        return _db_items()

    btn = ui.button(icon="sell", on_click=lambda: _open()).props("flat dense round") \
        .tooltip(tooltip)
    with btn:
        badge = ui.badge("0", color="secondary").props("floating")
    badge.set_visibility(False)

    def refresh() -> None:
        n = len(_current())
        badge.set_text(str(n))
        badge.set_visibility(n > 0)
        btn.props(f'color={"secondary" if n else "grey"}')

    def _apply(items: list[dict]) -> bool:
        if staged:
            staged_items[:] = items
        elif deferred:
            pending["items"] = items
        else:
            tid = _tid()
            if tid is None:
                ui.notify("Save the record first.", type="warning")
                return False
            try:
                with session_factory() as s:
                    with s.begin():
                        ann_svc.set_annotations(s, target, tid, _as_items(items))
            except ValueError as exc:
                ui.notify(str(exc), type="warning", multi_line=True, timeout=8000)
                return False
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return False
        refresh()
        if on_change:
            on_change()
        return True

    def _open() -> None:
        current = _current()
        with session_factory() as s:
            allowed = {t.id for t in ann_svc.allowed_terms(s, target)}
            wanted = allowed | {i["term_id"] for i in current}
            terms = {t.id: {"kind": t.kind, "name": t.name, "css_color": t.css_color,
                            "local": t.tw_id is None, "allowed": t.id in allowed}
                     for t in ann_svc.list_terms(s) if t.id in wanted}
        work = {i["term_id"]: i["value"] for i in current}

        with ui.dialog() as dlg, ui.card().classes("min-w-[480px] gap-2"):
            ui.label(tooltip).classes("text-base font-medium")
            if target == "association":
                ui.label("Association annotations stay local — TaxonWorks cannot import "
                         "biological associations.").classes("text-xs") \
                    .style("color:var(--tp-base-soft)")
            ui.label("Tags").classes("text-sm font-medium")
            tags = sorted((i for i, t in terms.items() if t["kind"] == "tag"),
                          key=lambda i: terms[i]["name"].casefold())
            if not tags:
                ui.label("No tags are offered here — add or allow them in Controlled "
                         "Vocabularies.").classes("text-xs italic") \
                    .style("color:var(--tp-base-soft)")
            for tid in tags:
                t = terms[tid]
                with ui.row().classes("items-center gap-2"):
                    cb = ui.checkbox(value=tid in work)
                    if not t["allowed"] and tid not in work:
                        cb.disable()
                    cb.on_value_change(lambda e, i=tid: work.__setitem__(i, None)
                                       if e.value else work.pop(i, None))
                    ui.html(chip_html(t["name"], t["css_color"], local_only=t["local"]))
                    if not t["allowed"]:
                        ui.label("no longer offered").classes("text-xs") \
                            .style("color:var(--tp-base-soft)")

            ui.separator().classes("my-1")
            ui.label("Data attributes").classes("text-sm font-medium")
            da_box = ui.column().classes("w-full gap-1")

            def _render_da() -> None:
                da_box.clear()
                with da_box:
                    for tid in [i for i in work if terms.get(i, {}).get("kind") == "data_attribute"]:
                        t = terms[tid]
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.html(chip_html(t["name"], t["css_color"], local_only=t["local"]))
                            if not t["allowed"]:
                                ui.label("no longer offered").classes("text-xs") \
                                    .style("color:var(--tp-base-soft)")
                            v = ui.input(value=work[tid] or "").props("dense").classes("flex-1")
                            v.on_value_change(lambda e, i=tid: work.__setitem__(i, e.value))
                            ui.button(icon="delete",
                                      on_click=lambda _, i=tid: (work.pop(i, None), _render_da())) \
                                .props("flat dense round size=sm color=grey").tooltip("Remove")
                    free = {i: t["name"] for i, t in terms.items()
                            if t["kind"] == "data_attribute" and t["allowed"] and i not in work}
                    if free:
                        with ui.row().classes("items-end gap-2 w-full no-wrap"):
                            pick = ui.select(free, label="Add data attribute") \
                                .props("dense clearable").classes("w-48")
                            val = ui.input("Value").props("dense").classes("flex-1")

                            def _add_da() -> None:
                                if pick.value is None:
                                    ui.notify("Pick a data attribute first.", type="warning")
                                    return
                                work[pick.value] = val.value
                                _render_da()
                            ui.button(icon="add", on_click=_add_da) \
                                .props("flat dense round color=secondary").tooltip("Add")

            _render_da()

            def _save_close() -> None:
                try:
                    items = collect_items(work, terms)
                except ValueError as exc:
                    ui.notify(str(exc), type="warning", multi_line=True)
                    return
                if _apply(items):
                    dlg.close()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abort", on_click=dlg.close).props("flat")
                ui.button("Save & close", on_click=_save_close).props("color=secondary")
        # #65: rebuilt per open, so delete on close or its timers leak.
        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
        dlg.open()

    def commit(session, target_id: int) -> None:
        """staged: ADD on the new / reused record. deferred: REPLACE the set."""
        if staged:
            if staged_items:
                ann_svc.add_annotations(session, target, target_id, _as_items(staged_items))
            return
        if deferred and pending["items"] is not None:
            ann_svc.set_annotations(session, target, target_id, _as_items(pending["items"]))
            pending["items"] = None

    def has_changes() -> bool:
        if not deferred or pending["items"] is None:
            return False
        key = lambda items: sorted((i["term_id"], i["value"] or "") for i in items)
        return key(pending["items"]) != key(_db_items())

    def clear() -> None:
        staged_items.clear()
        pending["items"] = None
        refresh()

    refresh()
    return {
        "button": btn, "refresh": refresh, "commit": commit, "clear": clear,
        "has_changes": has_changes,
        "has_content": (lambda: bool(staged_items)) if staged else (lambda: bool(_current())),
        "staged_items": staged_items,
    }

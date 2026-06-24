"""Reusable media-attachment panel (#48).

``build_media_panel(session_factory, *, target_kind, target_id_getter)`` renders an upload
control, a category filter, and the list of files attached to one record (a specimen,
collecting event, or biological association) with per-item caption / primary / delete.

Bytes are stored content-addressed by app/services/media.py; this is pure UI. Designed to
sit inside a collapsed ``ui.expansion`` (progressive disclosure) — the caller wraps it and
can use the returned ``count`` to auto-open when attachments already exist.
"""
from __future__ import annotations

from typing import Callable, Optional

from nicegui import ui

import app.services.media as media_svc

# Category → Material icon (for non-image previews) and filter order.
_CAT_ICON = {
    "Image": "image", "Sound": "volume_up", "Video": "movie",
    "Document": "description", "Sequence": "biotech", "Other": "insert_drive_file",
}


def build_media_panel(
    session_factory,
    *,
    target_kind: str,
    target_id_getter: Callable[[], Optional[int]],
    on_change: Optional[Callable[[], None]] = None,
) -> dict:
    state = {"filter": None}  # category filter; None = all

    root = ui.column().classes("w-full gap-2")

    with root:
        with ui.row().classes("items-center gap-3 w-full"):
            upload = ui.upload(
                multiple=True, auto_upload=True, max_file_size=200_000_000,
                on_upload=lambda e: _on_upload(e),
            ).props('flat dense accept="*/*"').classes("max-w-xs")
            ui.space()
            filter_sel = ui.select(
                {None: "All kinds"}, value=None, label="Filter",
            ).props("dense outlined").classes("w-40")
        gallery = ui.row().classes("w-full flex-wrap gap-3")

    def _target_id() -> Optional[int]:
        return target_id_getter()

    def _on_upload(e):
        tid = _target_id()
        if tid is None:
            ui.notify("Save the record before attaching media.", type="warning")
            return
        data = e.content.read()
        with session_factory() as s:
            with s.begin():
                media_svc.add_attachment(
                    s, target_kind=target_kind, target_id=tid,
                    data=data, filename=e.name,
                )
        upload.reset()
        refresh()
        if on_change:
            on_change()

    def _set_caption(att_id: int, value: str):
        with session_factory() as s:
            with s.begin():
                media_svc.update_attachment(s, att_id, caption=value)

    def _set_category(media_id: int, value: str):
        with session_factory() as s:
            with s.begin():
                media_svc.update_media(s, media_id, category=value)
        refresh()

    def _make_primary(att_id: int):
        tid = _target_id()
        with session_factory() as s:
            with s.begin():
                media_svc.set_primary(s, target_kind=target_kind, target_id=tid, attachment_id=att_id)
        refresh()

    def _delete(att_id: int):
        with session_factory() as s:
            with s.begin():
                media_svc.delete_attachment(s, att_id)
        refresh()
        if on_change:
            on_change()

    def _snapshot() -> list[dict]:
        """Read attachments + their media into plain dicts inside a session, so the UI can
        render after the session closes (no DetachedInstanceError)."""
        tid = _target_id()
        if tid is None:
            return []
        out = []
        with session_factory() as s:
            for a in media_svc.list_attachments(s, target_kind=target_kind, target_id=tid):
                m = a.media
                out.append({
                    "att_id": a.id, "caption": a.caption or "", "is_primary": bool(a.is_primary),
                    "media_id": m.id, "category": m.category, "rel": m.relative_path,
                    "name": m.original_filename or m.relative_path, "format": m.format,
                })
        return out

    def refresh() -> int:
        items = _snapshot()
        cats = sorted({it["category"] for it in items})
        filter_sel.set_options({None: "All kinds", **{c: c for c in cats}})
        if state["filter"] not in (None, *cats):
            state["filter"] = None
            filter_sel.value = None
        shown = [it for it in items if state["filter"] in (None, it["category"])]

        gallery.clear()
        with gallery:
            if _target_id() is None:
                ui.label("Save the record to attach media.") \
                    .classes("text-sm italic").style("color:var(--tp-base-soft)")
            elif not items:
                ui.label("No media attached.") \
                    .classes("text-sm italic").style("color:var(--tp-base-soft)")
            else:
                for it in shown:
                    _render_item(it)
        return len(items)

    def _render_item(it: dict):
        with ui.card().classes("p-2 gap-1").style("width:172px"):
            url = f"/media/{it['rel']}"
            if it["category"] == "Image":
                ui.image(url).classes("w-full h-32 object-cover rounded") \
                    .style("cursor:pointer").on("click", lambda u=url: ui.navigate.to(u, new_tab=True))
            else:
                with ui.element("div").classes(
                    "w-full h-32 flex flex-col items-center justify-center rounded"
                ).style("background:var(--tp-base-foreground)"):
                    ui.icon(_CAT_ICON.get(it["category"], "insert_drive_file"), size="lg")
                    ui.link(it["name"], url, new_tab=True).classes("text-xs text-center px-1 truncate")
            with ui.row().classes("items-center gap-1 w-full"):
                cat = ui.select(list(_CAT_ICON.keys()), value=it["category"]) \
                    .props("dense borderless").classes("text-xs flex-1")
                cat.on_value_change(lambda e, mid=it["media_id"]: _set_category(mid, e.value))
                star = ui.button(
                    icon="star" if it["is_primary"] else "star_border",
                    on_click=lambda a=it["att_id"]: _make_primary(a),
                ).props("flat dense round size=sm").tooltip("Mark as primary")
                if it["is_primary"]:
                    star.props("color=amber")
                ui.button(icon="delete", on_click=lambda a=it["att_id"]: _delete(a)) \
                    .props("flat dense round size=sm color=grey").tooltip("Remove")
            cap = ui.input(placeholder="caption", value=it["caption"]) \
                .props("dense borderless").classes("w-full text-xs")
            cap.on("blur", lambda e, a=it["att_id"], el=cap: _set_caption(a, el.value))

    filter_sel.on_value_change(lambda e: (state.update(filter=e.value), refresh()))

    refresh()
    return {"container": root, "refresh": refresh}

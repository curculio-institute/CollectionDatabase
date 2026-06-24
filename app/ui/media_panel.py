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
import app.services.person_defaults as pd_svc
from app.config import get_config
from app.vocab import LICENSE_OPTIONS
from app.ui.person_field import build_person_field
from app.models import Person

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

    def _default_rights_holder() -> Optional[str]:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[2]

    def _open_metadata(it: dict):
        """Per-asset metadata editor — title, creator, rightsHolder (person, Tier-2),
        licence (Tier-2), and the per-attachment caption."""
        with ui.dialog() as dlg, ui.card().classes("min-w-[460px] gap-2"):
            ui.label(f"Media details — {it['name']}").classes("text-base font-medium")
            title_in = ui.input("Title", value=it["title"]).classes("w-full")
            creator_in = ui.input("Creator", value=it["creator"]).classes("w-full")
            # rightsHolder — person field with a Tier-2 push_pin (default person).
            rights_field = build_person_field(
                session_factory, "rightsHolder",
                default_fn=_default_rights_holder,
                initial_value=it["rights_name"] or None,
                classes="w-full",
            )
            # licence — select + Tier-2 push_pin inserting the configured default.
            with ui.row().classes("items-center gap-1 w-full"):
                lic_sel = ui.select(LICENSE_OPTIONS, value=it["license"], label="Licence") \
                    .classes("flex-1")
                ui.button(icon="push_pin",
                          on_click=lambda: lic_sel.set_value(get_config().default_license or "")) \
                    .props("flat dense round size=sm").tooltip("Insert default licence")
            cap_in = ui.input("Caption", value=it["caption"]).classes("w-full")

            def _save_meta():
                with session_factory() as s:
                    with s.begin():
                        rid = rights_field["commit"](s)
                        media_svc.update_media(
                            s, it["media_id"],
                            title=title_in.value or None,
                            creator=creator_in.value or None,
                            license=lic_sel.value or None,
                            rights_holder_id=rid,
                        )
                        media_svc.update_attachment(s, it["att_id"], caption=cap_in.value or "")
                dlg.close()
                refresh()

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Save", on_click=_save_meta).props("color=secondary")
        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
        dlg.open()

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
                rights_name = None
                if m.rights_holder_id is not None:
                    p = s.get(Person, m.rights_holder_id)
                    rights_name = p.full_name if p else None
                out.append({
                    "att_id": a.id, "caption": a.caption or "", "is_primary": bool(a.is_primary),
                    "media_id": m.id, "category": m.category, "rel": m.relative_path,
                    "name": m.original_filename or m.relative_path, "format": m.format,
                    "title": m.title or "", "creator": m.creator or "",
                    "license": m.license or "", "rights_name": rights_name or "",
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
                ui.button(icon="edit", on_click=lambda d=it: _open_metadata(d)) \
                    .props("flat dense round size=sm color=grey") \
                    .tooltip("Details — title, creator, rightsHolder, licence")
                ui.button(icon="delete", on_click=lambda a=it["att_id"]: _delete(a)) \
                    .props("flat dense round size=sm color=grey").tooltip("Remove")
            cap = ui.input(placeholder="caption", value=it["caption"]) \
                .props("dense borderless").classes("w-full text-xs")
            cap.on("blur", lambda e, a=it["att_id"], el=cap: _set_caption(a, el.value))
            if it["license"] or it["rights_name"]:
                meta = " · ".join(x for x in (it["license"], it["rights_name"]) if x)
                ui.label(meta).classes("text-xs truncate w-full") \
                    .style("color:var(--tp-base-soft)")

    filter_sel.on_value_change(lambda e: (state.update(filter=e.value), refresh()))

    refresh()
    return {"container": root, "refresh": refresh}

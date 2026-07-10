"""Reusable media button + popup (#48).

``build_media_button(...)`` renders a compact icon button (with a count badge when media
is attached) that opens a popup gallery: batch upload, per-item category / primary /
delete / details. Used wherever a specimen, collecting event, or biological association is
shown — in **Records** (bound mode: writes straight to the DB) and in **Specimen
Digitization** (staged mode: the record doesn't exist yet, so files are stored and
committed on Save).

Bytes are stored content-addressed by app/services/media.py; this is pure UI.
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

_CAT_ICON = {
    "Image": "image", "Sound": "volume_up", "Video": "movie",
    "Document": "description", "Sequence": "biotech", "Other": "insert_drive_file",
}
_CATEGORIES = list(_CAT_ICON.keys())


def build_media_button(
    session_factory,
    *,
    target_kind: str,
    target_id_getter: Optional[Callable[[], Optional[int]]] = None,
    staged: bool = False,
    staged_store: Optional[list] = None,
    on_change: Optional[Callable[[], None]] = None,
    icon: str = "collections",
    tooltip: str = "Media",
) -> dict:
    """Returns a dict with:
      - ``button``      the ui.button (with count badge)
      - ``refresh``     re-read the count + badge
      - ``has_content`` () -> bool   (staged: any staged files; bound: any attachments)
      - ``commit``      (session, target_id) -> None   (staged mode only)
      - ``clear``       () -> None   (staged mode: drop staged files)
      - ``staged_items`` the staged list (so callers can persist it across re-renders)

    ``staged_store`` lets the caller pass in a persistent list to back the staged items —
    needed when the button is re-created on each render (e.g. a per-association button in a
    list that rebuilds), so staged files aren't lost. The bytes are already on disk
    (store_bytes), so thumbnails render via /media/<rel> before the record is saved.
    """
    staged_items: list[dict] = staged_store if staged_store is not None else []
    state = {"filter": None}

    def _target_id() -> Optional[int]:
        return target_id_getter() if target_id_getter else None

    # ── count + button ───────────────────────────────────────────────────────────
    def _count() -> int:
        if staged:
            return len(staged_items)
        tid = _target_id()
        if tid is None:
            return 0
        with session_factory() as s:
            return media_svc.count_attachments(s, target_kind=target_kind, target_id=tid)

    btn = ui.button(icon=icon, on_click=lambda: _open()).props("flat dense round") \
        .tooltip(tooltip)
    with btn:
        badge = ui.badge("0", color="secondary").props("floating")
    badge.set_visibility(False)

    def refresh():
        n = _count()
        badge.set_text(str(n))
        badge.set_visibility(n > 0)
        # filled icon + accent colour signals "holds media"
        btn.props(f'color={"secondary" if n else "grey"}')

    # ── snapshot of current items (uniform shape for both modes) ──────────────────
    def _entries() -> list[dict]:
        if staged:
            out = []
            for i, it in enumerate(staged_items):
                m = it["meta"]
                out.append({
                    "key": i, "caption": it["caption"], "is_primary": it["is_primary"],
                    "category": it["category"], "rel": m["relative_path"],
                    "name": m.get("original_filename") or m["relative_path"],
                    "license": it["license"], "rights_name": it["rights_name"],
                })
            return out
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
                    "key": a.id, "att_id": a.id, "media_id": m.id,
                    "caption": a.caption or "", "is_primary": bool(a.is_primary),
                    "category": m.category, "rel": m.relative_path,
                    "name": m.original_filename or m.relative_path,
                    "license": m.license or "", "rights_name": rights_name or "",
                })
        return out

    # ── mutations ─────────────────────────────────────────────────────────────────
    def _on_files(files: list[tuple[str, bytes]]):
        """files = list of (filename, bytes) — supports batch (one→many) upload."""
        if staged:
            for name, data in files:
                meta = media_svc.store_bytes(data, name)
                staged_items.append({
                    "meta": meta, "category": meta["category"], "license": "",
                    "rights_holder_id": None, "rights_name": "", "caption": "",
                    "is_primary": 0,
                })
        else:
            tid = _target_id()
            if tid is None:
                ui.notify("Save the record before attaching media.", type="warning")
                return
            with session_factory() as s:
                with s.begin():
                    for name, data in files:
                        media_svc.add_attachment(
                            s, target_kind=target_kind, target_id=tid,
                            data=data, filename=name,
                        )
        _rebuild()
        refresh()
        if on_change:
            on_change()

    def _set_category(e, value):
        if staged:
            staged_items[e["key"]]["category"] = value
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_media(s, e["media_id"], category=value)
        _rebuild()

    def _make_primary(e):
        if staged:
            for i, it in enumerate(staged_items):
                it["is_primary"] = 1 if i == e["key"] else 0
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.set_primary(s, target_kind=target_kind,
                                          target_id=_target_id(), attachment_id=e["att_id"])
        _rebuild()

    def _delete(e):
        if staged:
            staged_items.pop(e["key"])
        else:
            # Commits the row deletion, then unlinks the orphaned bytes — never the other
            # way round, or a failed commit leaves a media row pointing at nothing (#63).
            media_svc.delete_attachment_and_file(session_factory, e["att_id"])
        _rebuild()
        refresh()
        if on_change:
            on_change()

    def _default_rights_holder() -> Optional[str]:
        with session_factory() as s:
            return pd_svc.get_defaults(s)[2]

    def _set_license(e, value):
        if staged:
            staged_items[e["key"]]["license"] = value or ""
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_media(s, e["media_id"], license=value or None)

    def _set_caption_entry(e, value):
        if staged:
            staged_items[e["key"]]["caption"] = value or ""
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_attachment(s, e["att_id"], caption=value or "")

    def _set_rights(e, rights_field):
        """Commit the chosen rightsHolder person and store its id (works in both modes)."""
        with session_factory() as s:
            with s.begin():
                rid = rights_field["commit"](s)
                rname = s.get(Person, rid).full_name if rid is not None else ""
        if staged:
            staged_items[e["key"]].update(rights_holder_id=rid, rights_name=rname)
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_media(s, e["media_id"], rights_holder_id=rid)

    # ── gallery (inside the popup) ────────────────────────────────────────────────
    gallery = None
    filter_sel = None

    def _rebuild():
        if gallery is None:
            return
        items = _entries()
        cats = sorted({it["category"] for it in items})
        filter_sel.set_options({None: "All kinds", **{c: c for c in cats}})
        if state["filter"] not in (None, *cats):
            state["filter"] = None
            filter_sel.value = None
        shown = [it for it in items if state["filter"] in (None, it["category"])]
        gallery.clear()
        with gallery:
            if not items:
                ui.label("No media yet — drop or choose files above.") \
                    .classes("text-sm italic").style("color:var(--tp-base-soft)")
            for it in shown:
                _render_card(it)

    def _render_card(it: dict):
        # Metadata (caption / licence / rightsHolder) lives inline beneath the file so
        # the important fields are visible and editable without an extra click. Each
        # commits on change/blur — no Save button.
        with ui.card().classes("p-2 gap-1").style("width:230px"):
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

            # caption
            cap = ui.input(placeholder="caption", value=it["caption"]) \
                .props("dense").classes("w-full text-xs")
            cap.on("blur", lambda e, it=it, el=cap: _set_caption_entry(it, el.value))

            # licence (Tier-2: push_pin inserts the configured default)
            with ui.row().classes("items-center gap-1 w-full"):
                lic = ui.select(LICENSE_OPTIONS, value=it["license"], label="licence") \
                    .props("dense").classes("flex-1 text-xs")
                lic.on_value_change(lambda ev, e=it: _set_license(e, ev.value))
                ui.button(icon="push_pin",
                          on_click=lambda el=lic: el.set_value(get_config().default_license or "")) \
                    .props("flat dense round size=xs").tooltip("Insert default licence")

            # rightsHolder (person field, Tier-2 push_pin inside the field)
            _rh = {}
            _rh["field"] = build_person_field(
                session_factory, "rightsHolder",
                default_fn=_default_rights_holder,
                initial_value=it["rights_name"] or None,
                on_change=lambda e=it: _set_rights(e, _rh["field"]),
                classes="w-full",
            )

            # bottom controls: category / primary / delete
            with ui.row().classes("items-center gap-1 w-full"):
                cat = ui.select(_CATEGORIES, value=it["category"]) \
                    .props("dense borderless").classes("text-xs flex-1")
                cat.on_value_change(lambda ev, e=it: _set_category(e, ev.value))
                star = ui.button(icon="star" if it["is_primary"] else "star_border",
                                 on_click=lambda e=it: _make_primary(e)) \
                    .props("flat dense round size=sm").tooltip("Primary")
                if it["is_primary"]:
                    star.props("color=amber")
                ui.button(icon="delete", on_click=lambda e=it: _delete(e)) \
                    .props("flat dense round size=sm color=grey").tooltip("Remove")

    def _open():
        nonlocal gallery, filter_sel
        with ui.dialog() as dlg, ui.card().classes("min-w-[560px] gap-2"):
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label(tooltip).classes("text-base font-medium")
                ui.space()
                filter_sel = ui.select({None: "All kinds"}, value=state["filter"], label="Filter") \
                    .props("dense outlined").classes("w-40")
                filter_sel.on_value_change(lambda e: (state.update(filter=e.value), _rebuild()))
            # batch upload — multiple files selected in one action. on_upload fires
            # once per file (covers single + batch); do NOT also wire on_multi_upload
            # or every file is processed twice.
            ui.upload(
                multiple=True, auto_upload=True, max_file_size=200_000_000,
                on_upload=lambda e: _on_files([(e.name, e.content.read())]),
            ).props('flat accept="*/*"').classes("w-full")
            gallery = ui.row().classes("w-full flex-wrap gap-3")
            _rebuild()
            with ui.row().classes("w-full justify-end mt-1"):
                ui.button("Close", on_click=dlg.close).props("flat")
        dlg.on_value_change(lambda ev: (_sync_after_close() if not ev.value else None))
        dlg.open()

    def _sync_after_close():
        nonlocal gallery, filter_sel
        gallery = None
        filter_sel = None
        refresh()

    # ── staged commit / helpers ───────────────────────────────────────────────────
    def commit(session, target_id: int):
        """Flush staged files onto the now-saved record (staged mode only)."""
        for it in staged_items:
            media_svc.attach_stored(
                session, target_kind=target_kind, target_id=target_id,
                meta=it["meta"], caption=it["caption"] or None,
                category=it["category"], license=it["license"] or None,
                rights_holder_id=it["rights_holder_id"], is_primary=it["is_primary"],
            )

    def clear():
        staged_items.clear()
        refresh()

    refresh()
    return {
        "button": btn, "refresh": refresh,
        "has_content": (lambda: len(staged_items) > 0) if staged else (lambda: _count() > 0),
        "commit": commit, "clear": clear, "staged_items": staged_items,
    }

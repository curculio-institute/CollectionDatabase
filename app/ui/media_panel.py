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


def _data_url(data: bytes, mime: str | None) -> str:
    """A base64 data: URL, so a not-yet-saved upload's thumbnail renders straight from
    memory — no file on disk (#63). Only images are shown as thumbnails; the rest fall back
    to a category icon, so a generic application/octet-stream mime is fine here."""
    import base64
    return f"data:{mime or 'application/octet-stream'};base64,{base64.b64encode(data).decode()}"


def _license_options(current: str | None) -> list[str]:
    """The licence dropdown's options, always able to display *current* (#64).

    `media.license` is free TEXT — an imported record, or a `config.default_license` typed in
    Settings, can hold a value outside `LICENSE_OPTIONS` (a CC URL, say). NiceGUI's
    `ChoiceElement.__init__` **raises** `ValueError: Invalid value` when the value is not
    among the options, which killed the popup's rebuild mid-render and left the gallery blank.

    So an unknown stored value is appended to the list rather than dropped: the popup opens,
    the real licence is shown, and the user can switch to a standard one. Silently coercing it
    to "" would discard a rights statement — the one thing a licence field must not do.
    """
    cur = (current or "").strip()
    if cur in LICENSE_OPTIONS:
        return list(LICENSE_OPTIONS)
    return [*LICENSE_OPTIONS, cur]


def _apply_default_license(select) -> None:
    """Push-pin: insert `config.default_license`, widening the options if it is non-standard.

    The default is a free string in config.json, so it can be outside LICENSE_OPTIONS too —
    `set_value` on a value the select does not know would be dropped silently.
    """
    default = (get_config().default_license or "").strip()
    if default and default not in select.options:
        select.set_options([*select.options, default])
    select.set_value(default)


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
    deferred: bool = False,
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

    # Deferred (Records): the attachments of an existing record are loaded into an in-memory
    # working list; every add / delete / metadata edit stays there until the card's Save
    # calls commit(session, target_id). Bound and staged modes are unchanged.
    #   existing item: {"kind":"db",  att_id, media_id, deleted, caption, is_primary,
    #                   category, rel, name, license, rights_holder_id, rights_name, _orig}
    #   new item:      {"kind":"new", meta, caption, is_primary, category, license,
    #                   rights_holder_id, rights_name}   (bytes already in the store)
    _def: list[dict] = []

    def _seed_deferred() -> None:
        tid = _target_id()
        _def.clear()
        if tid is None:
            return
        with session_factory() as s:
            for a in media_svc.list_attachments(s, target_kind=target_kind, target_id=tid):
                m = a.media
                rname = None
                if m.rights_holder_id is not None:
                    pr = s.get(Person, m.rights_holder_id)
                    rname = pr.full_name if pr else None
                _def.append({
                    "kind": "db", "att_id": a.id, "media_id": m.id, "deleted": False,
                    "caption": a.caption or "", "is_primary": bool(a.is_primary),
                    "category": m.category, "rel": m.relative_path,
                    "name": m.original_filename or m.relative_path,
                    "license": m.license or "", "rights_holder_id": m.rights_holder_id,
                    "rights_name": rname or "",
                })
        for it in _def:
            it["_orig"] = _def_sig(it)

    def _def_sig(it: dict) -> tuple:
        return (it.get("deleted", False), it["caption"], it["is_primary"],
                it["category"], it["license"], it.get("rights_holder_id"), it["rights_name"])

    def _target_id() -> Optional[int]:
        return target_id_getter() if target_id_getter else None

    # ── count + button ───────────────────────────────────────────────────────────
    def _count() -> int:
        if staged:
            return len(staged_items)
        if deferred:
            return sum(1 for it in _def if not it.get("deleted"))
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
        if deferred:
            out = []
            for i, it in enumerate(_def):
                if it.get("deleted"):
                    continue
                if it["kind"] == "db":
                    rel, name, data_url = it["rel"], it["name"], None
                else:
                    rel, data_url = None, it["data_url"]
                    name = it["probe"].get("original_filename") or "upload"
                out.append({
                    "key": i, "caption": it["caption"], "is_primary": it["is_primary"],
                    "category": it["category"], "rel": rel, "data_url": data_url, "name": name,
                    "license": it["license"], "rights_name": it["rights_name"],
                })
            return out
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
        elif deferred:
            # Held in memory only: probe for metadata WITHOUT writing to disk, and keep the
            # raw bytes for both the thumbnail (a data: URL) and store_bytes at commit. So
            # abandoning an upload writes nothing to disk — no orphan file at all (#63).
            for name, data in files:
                probe = media_svc.probe_bytes(data, name)
                _def.append({
                    "kind": "new", "data": data, "probe": probe,
                    "data_url": _data_url(data, probe.get("format")),
                    "category": probe["category"], "license": "",
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
        elif deferred:
            _def[e["key"]]["category"] = value
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_media(s, e["media_id"], category=value)
        _rebuild()

    def _make_primary(e):
        if staged:
            for i, it in enumerate(staged_items):
                it["is_primary"] = 1 if i == e["key"] else 0
        elif deferred:
            for i, it in enumerate(_def):
                it["is_primary"] = (i == e["key"])
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.set_primary(s, target_kind=target_kind,
                                          target_id=_target_id(), attachment_id=e["att_id"])
        _rebuild()

    def _delete(e):
        if staged:
            staged_items.pop(e["key"])
        elif deferred:
            it = _def[e["key"]]
            if it["kind"] == "new":
                _def.pop(e["key"])          # never persisted — just drop it
            else:
                it["deleted"] = True        # existing row: deleted by commit(), reversible
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
        elif deferred:
            _def[e["key"]]["license"] = value or ""
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_media(s, e["media_id"], license=value or None)

    def _set_caption_entry(e, value):
        if staged:
            staged_items[e["key"]]["caption"] = value or ""
        elif deferred:
            _def[e["key"]]["caption"] = value or ""
        else:
            with session_factory() as s:
                with s.begin():
                    media_svc.update_attachment(s, e["att_id"], caption=value or "")

    def _set_rights(e, rights_field):
        """Store the chosen rightsHolder. Deferred holds the NAME and resolves it to a
        person only on commit (#60 — no stray person if the card is abandoned)."""
        if deferred:
            name = rights_field["get_value"]() or ""
            _def[e["key"]].update(rights_holder_id=None, rights_name=name)
            return
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
            # A memory-held upload (deferred, not yet written) shows from its data: URL;
            # everything on disk shows from /media/<rel>.
            url = it.get("data_url") or f"/media/{it['rel']}"
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
                # One normalised string for BOTH the options and the value: _license_options
                # strips, so passing the raw value here would re-introduce the mismatch that
                # makes ui.select raise (#64).
                _lic_val = (it["license"] or "").strip()
                lic = ui.select(_license_options(_lic_val),
                                value=_lic_val, label="licence") \
                    .props("dense").classes("flex-1 text-xs")
                lic.on_value_change(lambda ev, e=it: _set_license(e, ev.value))
                ui.button(icon="push_pin",
                          on_click=lambda el=lic: _apply_default_license(el)) \
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
        """staged (Digitize): attach the held files to the new record.
        deferred (Records): reconcile the working list against the DB — deletes, then
        creates, then metadata edits, then the single primary. Returns the relative paths
        of any now-orphaned bytes; the caller unlinks them AFTER the transaction commits
        (never inside it — a rolled-back save must not destroy bytes, #63)."""
        if deferred:
            return _commit_deferred(session, target_id)
        for it in staged_items:
            media_svc.attach_stored(
                session, target_kind=target_kind, target_id=target_id,
                meta=it["meta"], caption=it["caption"] or None,
                category=it["category"], license=it["license"] or None,
                rights_holder_id=it["rights_holder_id"], is_primary=it["is_primary"],
            )
        return []

    def _commit_deferred(session, target_id: int) -> list[str]:
        import app.services.persons as persons_svc
        orphans: list[str] = []

        def _rid(it) -> Optional[int]:
            if it.get("rights_holder_id"):
                return it["rights_holder_id"]
            name = (it.get("rights_name") or "").strip()
            return persons_svc.get_or_create_person(session, full_name=name).id if name else None

        for it in _def:
            if it["kind"] == "db" and it.get("deleted"):
                rel = media_svc.delete_attachment(session, it["att_id"])
                if rel:
                    orphans.append(rel)
        for it in _def:
            if it["kind"] == "new":
                _fname = it["probe"].get("original_filename") or "upload"
                meta = media_svc.store_bytes(it["data"], _fname)        # writes to disk now
                att = media_svc.attach_stored(
                    session, target_kind=target_kind, target_id=target_id,
                    meta=meta, caption=it["caption"] or None,
                    category=it["category"], license=it["license"] or None,
                    rights_holder_id=_rid(it), is_primary=1 if it["is_primary"] else 0,
                )
                it.update(kind="db", att_id=att.id, media_id=att.media_id,
                          rel=meta["relative_path"], name=_fname, deleted=False)
                it.pop("data", None); it.pop("data_url", None); it.pop("probe", None)
            elif not it.get("deleted"):
                media_svc.update_media(session, it["media_id"], category=it["category"],
                                       license=it["license"] or None,
                                       rights_holder_id=_rid(it))
                media_svc.update_attachment(session, it["att_id"], caption=it["caption"] or "")

        primary = next((it for it in _def if not it.get("deleted") and it["is_primary"]), None)
        if primary is not None and primary.get("att_id"):
            media_svc.set_primary(session, target_kind=target_kind, target_id=target_id,
                                  attachment_id=primary["att_id"])
        # Drop committed deletions and re-baseline so a second save is a no-op.
        _def[:] = [it for it in _def if not it.get("deleted")]
        for it in _def:
            it["_orig"] = _def_sig(it)
        return orphans

    def has_changes() -> bool:
        return any(it["kind"] == "new" or it["_orig"] != _def_sig(it) for it in _def)

    def clear():
        staged_items.clear()
        refresh()

    if deferred:
        _seed_deferred()
    refresh()
    return {
        "button": btn, "refresh": refresh, "has_changes": has_changes,
        "has_content": (lambda: len(staged_items) > 0) if staged else (lambda: _count() > 0),
        "commit": commit, "clear": clear, "staged_items": staged_items,
    }

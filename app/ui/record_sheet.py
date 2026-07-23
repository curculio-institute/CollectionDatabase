"""Condensed, read-only record "sheet" for the Records tab (#137).

A specimen (or event) shown as a museum-style sheet: identity + media + map +
grouped details on one wide page, composed only from what is present — empty
groups don't render, so a rich record looks rich and a bare one looks clean. An
**Edit** button hands off to the existing editable form (`on_edit`), unchanged.

Everything is snapshotted inside one session and rendered from plain values, so no
lazy-load touches a detached instance after the session closes.
"""
from __future__ import annotations

import html as _html
import json

from nicegui import ui

import app.ui.record_summary as rs
from app.ui.map_picker import build_map_picker, add_map_assets
from app.config import get_config

from app.models import CollectionObject, CollectingEvent
import app.services.specimens as sp_svc
import app.services.biological as bio_svc
import app.services.media as media_svc
import app.services.external_ids as extid_svc
import app.services.life_stage as ls_svc
import app.services.print_queue as pq_svc
from app.services.taxa import format_scientific_name
from app.services.label_text import format_place

_CAT_ICON = {"Image": "image", "Sound": "volume_up", "Video": "movie",
             "Document": "description", "Sequence": "biotech", "Other": "insert_drive_file"}

CSS = """<style>
.rsheet-hero  { font-size: 1.5rem; line-height: 1.2; }
.rsheet-hd    { font-size: .66rem; font-weight: 700; text-transform: uppercase;
                letter-spacing: .06em; color: var(--tp-base-soft, #9ca3af); margin: 2px 0 6px; }
.rsheet-grid  { display: grid; grid-template-columns: max-content 1fr; gap: 3px 14px;
                font-size: .86rem; }
.rsheet-grid dt { color: var(--tp-base-soft, #9ca3af); }
.rsheet-grid dd { margin: 0; }
.rsheet-src-hd { font-size: .74rem; font-weight: 700; color: var(--tp-secondary, #0369a1);
                 margin: 10px 0 4px; }
.rsheet-src-hd:first-child { margin-top: 0; }
.rsheet-mcard  { width: 200px; }
.rsheet-thumb  { width: 200px; max-height: 200px; object-fit: cover; border-radius: 8px;
                 border: 1px solid var(--tp-base-border, #e2e8f0); cursor: zoom-in; background:#f1f5f9; }
.rsheet-mcap   { font-size: .78rem; margin-top: 3px; }
.rsheet-mmeta  { font-size: .7rem; color: var(--tp-base-soft, #9ca3af); line-height: 1.4; }
.rsheet-file   { display: inline-flex; align-items: center; gap: 6px; font-size: .82rem;
                 padding: 5px 10px; border-radius: 8px; border: 1px solid var(--tp-base-border,#e2e8f0);
                 text-decoration: none; color: inherit; }
.rsheet-file:hover { background: rgba(3,105,161,.07); }
.rsheet-src    { font-size: .62rem; text-transform: uppercase; letter-spacing: .04em;
                 color: var(--tp-secondary, #0369a1); background: rgba(3,105,161,.10);
                 border-radius: 4px; padding: 0 5px; }
.rsheet-type   { font-size: .68rem; font-weight: 800; letter-spacing: .05em; color: #fff;
                 background: #b91c1c; border-radius: 4px; padding: 1px 7px; text-transform: uppercase; }
.rsheet-det    { border-bottom: 1px solid var(--tp-base-border,#eef2f6); padding: 5px 0; font-size: .88rem; }
.rsheet-det:last-child { border-bottom: none; }
.rsheet-det .cur { font-weight: 600; }
.rsheet-muted  { color: var(--tp-base-soft, #9ca3af); font-size: .8rem; }
.rsheet-sublink { font-size: .8rem; margin: 2px 0 0 14px; }
</style>"""


def _media_items(session, kind: str, target_id: int, source: str) -> list[dict]:
    from app.models import Person
    out = []
    for a in media_svc.list_attachments(session, target_kind=kind, target_id=target_id):
        m = a.media
        rh = session.get(Person, m.rights_holder_id) if m.rights_holder_id else None
        dims = f"{m.width}×{m.height}" if (m.width and m.height) else None
        out.append({
            "url": f"/media/{m.relative_path}",
            "category": m.category,
            "name": m.original_filename or m.relative_path.rsplit("/", 1)[-1],
            "caption": a.caption or "",
            "is_primary": bool(a.is_primary),
            "source": source,          # what it depicts: Specimen / Event / Biological association
            "license": m.license,
            "rights_holder": rh.full_name if rh else None,
            "capture_date": m.capture_date,
            "title": m.title,
            "dims": dims,
        })
    return out


def build_specimen_sheet(session_factory, co_id: int, *, on_edit, on_open_event=None) -> None:
    """Render the read-only specimen sheet into the current container."""
    ui.add_head_html(rs.CSS)
    ui.add_head_html(CSS)
    add_map_assets()

    with session_factory() as s:
        co = s.get(CollectionObject, co_id)
        if co is None:
            ui.label("Specimen not found.").classes("text-sm text-negative")
            return
        ev = co.collecting_event
        dets = sp_svc.get_determination_history(s, co_id)
        cur = next((d for d in dets if d.is_current), None) or (dets[0] if dets else None)
        cur_t = cur.taxon if cur else None

        ident = {
            "catalog": co.catalog_number,   # already carries its collection prefix (JJPC-00042)
            "collection": co.repository.collection_full_name if co.repository else "",
            "name": (cur_t.scientific_name or "") if cur_t else "",
            "rank": cur_t.taxon_rank if cur_t else None,
            "authorship": cur_t.scientific_name_authorship if cur_t else None,
            "sex": cur.sex if cur else None,
            "count": co.individual_count,
            "type_status": cur.type_status if cur else None,
            "identified_by": cur.identified_by_person.full_name
                             if (cur and cur.identified_by_person) else None,
            "date_identified": cur.date_identified if cur else None,
            "qualifier": cur.identification_qualifier if cur else None,
            "confidential": bool(co.confidential),
            "event_confidential": bool(ev.confidential) if ev else False,
        }
        curatorial = {
            "Preparation": co.preparation.name if co.preparation else None,
            "Life stage": co.life_stage,
            "Disposition": co.disposition.name if co.disposition else None,
            "Basis of record": co.basis_of_record,
            "Other catalog #s": co.other_catalog_numbers,
            "Collection": ident["collection"],
            "Remarks": co.occurrence_remarks,
        }
        det_hist = [{
            "name": (d.taxon.scientific_name or "") if d.taxon else (d.verbatim_identification or ""),
            "rank": d.taxon.taxon_rank if d.taxon else None,
            "authorship": d.taxon.scientific_name_authorship if d.taxon else None,
            "by": d.identified_by_person.full_name if d.identified_by_person else None,
            "date": d.date_identified,
            "qualifier": d.identification_qualifier,
            "type_status": d.type_status,
            "current": bool(d.is_current),
        } for d in dets]
        # Everything hangs off the specimen (#137): media + external ids attach to the
        # specimen, its event, AND each biological association — so gather all three arcs,
        # labelling media by WHAT IT DEPICTS.
        media = _media_items(s, "collection_object", co_id, "Specimen")
        if ev:
            media += _media_items(s, "collecting_event", ev.id, "Event")
        assocs = []
        for a in bio_svc.get_associations_for_specimen(s, co_id):
            # External identifiers can hang off the association itself OR off its field
            # occurrence (the host observation — where an iNaturalist URL naturally lives,
            # since the iNat observation *is* the host observation). Gather both arcs.
            ext_rows = extid_svc.list_identifiers(
                s, target_kind="biological_association", target_id=a.id)
            if a.object_field_occurrence_id:
                ext_rows += extid_svc.list_identifiers(
                    s, target_kind="field_occurrence", target_id=a.object_field_occurrence_id)
            a_ext = [{"value": e.value, "label": e.label} for e in ext_rows]
            assocs.append({"rel": a.rel_name, "label": a.object_label,
                           "qualifier": a.identification_qualifier, "ext_ids": a_ext})
            media += _media_items(s, "biological_association", a.id, "Biological association")
        life_stages = [{"stage": r.life_stage, "basis": r.basis_of_record, "date": r.event_date}
                       for r in ls_svc.list_life_stages(s, co_id)]
        ext_ids = [{"value": e.value, "label": e.label} for e in
                   extid_svc.list_identifiers(s, target_kind="collection_object", target_id=co_id)]

        place = format_place(ev) if ev else ""
        ev_data = {
            "event_id": ev.id if ev else None,
            "date": ev.event_date if ev else None,
            "recorded_by": ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None,
            "habitat": ev.habitat_obj.name if (ev and ev.habitat_obj) else None,
            "protocol": ev.sampling_protocol_obj.name if (ev and ev.sampling_protocol_obj) else None,
            "lat": ev.decimal_latitude if ev else None,
            "lon": ev.decimal_longitude if ev else None,
            "unc": ev.coordinate_uncertainty_in_meters if ev else None,
            "n_here": sp_svc_count(s, ev.id) if ev else 0,
        }

    with session_factory() as s:
        already_queued = pq_svc.specimen_in_queue(s, co_id)

    def _reprint() -> bool:
        # Queue a full reprint of this saved specimen (#38): locality + identifier +
        # one label per identification. Grouped under "Reprint" in the queue, where the
        # user reviews/prunes/prints. Does not touch the record — only stages labels.
        # Returns True on success so the button can disable itself (now already queued).
        try:
            with session_factory() as s:
                with s.begin():
                    summ = pq_svc.reprint_specimen(s, co_id)
        except Exception as exc:
            ui.notify(f"Reprint failed: {exc}", type="negative")
            return False
        if summ.total == 0:
            ui.notify("Nothing to reprint for this specimen.", type="warning")
            return False
        ui.notify(
            f"Queued {summ.total} label{'s' if summ.total != 1 else ''} for reprint — "
            "open the Print queue tab to review and print.",
            type="positive")
        return True

    _render_specimen(ident, curatorial, det_hist, assocs, life_stages, ext_ids, media,
                     place, ev_data, on_edit=on_edit, on_open_event=on_open_event,
                     on_reprint=_reprint, already_queued=already_queued)


def sp_svc_count(session, ev_id: int) -> int:
    import app.services.events as ev_svc
    return ev_svc.count_co_at_event(session, ev_id)


def _co_summary(session, co) -> dict:
    """rs.specimen_html kwargs for one specimen under its event (locality omitted — the
    event IS the locality above)."""
    dets = sp_svc.get_determination_history(session, co.id)
    cur = next((d for d in dets if d.is_current), None) or (dets[0] if dets else None)
    t = cur.taxon if cur else None
    return {
        "co_id": co.id,
        "catalog": co.catalog_number,
        "name": (t.scientific_name or "") if t else "",
        "rank": t.taxon_rank if t else None,
        "authorship": t.scientific_name_authorship if t else None,
        "hosts": [h for a in co.subject_associations if (h := bio_svc.association_host(session, a))],
        "sex": cur.sex if cur else None,
        "count": co.individual_count,
        "identified_by": cur.identified_by_person.full_name
                         if (cur and cur.identified_by_person) else None,
        "date_identified": cur.date_identified if cur else None,
        "confidential": bool(co.confidential),
        "event_confidential": bool(co.collecting_event.confidential) if co.collecting_event else False,
    }


def build_event_sheet(session_factory, ev_id: int, *, on_edit, on_open_specimen=None,
                      on_open_event=None) -> None:
    """Read-only collecting-event sheet: the place (map + habitat media) and the specimens
    collected there, each linking to its own record."""
    import app.services.events as ev_svc
    ui.add_head_html(rs.CSS)
    ui.add_head_html(CSS)
    add_map_assets()

    with session_factory() as s:
        ev = s.get(CollectingEvent, ev_id)
        if ev is None:
            ui.label("Event not found.").classes("text-sm text-negative")
            return
        place = format_place(ev)
        ev_data = {
            "date": ev.event_date, "verbatim_date": ev.verbatim_event_date,
            "recorded_by": ev.recorded_by_person.full_name if ev.recorded_by_person else None,
            "habitat": ev.habitat_obj.name if ev.habitat_obj else None,
            "protocol": ev.sampling_protocol_obj.name if ev.sampling_protocol_obj else None,
            "lat": ev.decimal_latitude, "lon": ev.decimal_longitude,
            "unc": ev.coordinate_uncertainty_in_meters,
            "confidential": bool(ev.confidential),
            "verbatim_locality": ev.verbatim_locality,
        }
        detail = {
            "Country": ev.country_obj.name if ev.country_obj else None,
            "State / province": ev.state_province_obj.name if ev.state_province_obj else None,
            "Region": ev.administrative_region_obj.name if ev.administrative_region_obj else None,
            "County": ev.county_obj.name if ev.county_obj else None,
            "Municipality": ev.municipality,
            "Island": ev.island_obj.name if ev.island_obj else None,
            "Verbatim locality": ev.verbatim_locality,
            "Verbatim date": ev.verbatim_event_date,
        }
        media = _media_items(s, "collecting_event", ev_id, "Event")
        specimens = [_co_summary(s, co) for co in ev.collection_objects]
        nearby = ev_svc.nearest_events(s, ev_id, n=5)

    _render_event(place, ev_data, detail, media, specimens, nearby,
                  on_edit=on_edit, on_open_specimen=on_open_specimen, on_open_event=on_open_event)


def _render_event(place, ev, detail, media, specimens, nearby, *,
                  on_edit, on_open_specimen, on_open_event) -> None:
    # ── locality banner ──
    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-start gap-3 w-full no-wrap"):
            with ui.column().classes("flex-1 min-w-0 gap-1"):
                lock = rs.lock_html(own=ev["confidential"])
                ui.html(f'<div class="rsheet-hero" style="font-size:1.25rem">'
                        f'{_html.escape(place or "— no locality —")} {lock}</div>')
                meta = "  ·  ".join(x for x in (
                    ev["date"], f'leg. {ev["recorded_by"]}' if ev["recorded_by"] else "",
                    ev["habitat"], ev["protocol"]) if x)
                if meta:
                    ui.html(f'<div class="rsheet-muted">{_html.escape(meta)}</div>')
            ui.button("Edit", icon="edit", on_click=on_edit).props("no-caps unelevated")

    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("flex-1 min-w-0 gap-4"):
            with ui.card().classes("w-full shadow-sm"):
                ui.html(f'<div class="rsheet-hd">Specimens ({len(specimens)})</div>')
                if specimens:
                    for sp in specimens:
                        row = ui.html(rs.specimen_html(
                            catalog=sp["catalog"], name=sp["name"], rank=sp["rank"],
                            authorship=sp["authorship"], hosts=sp["hosts"], sex=sp["sex"],
                            count=sp["count"], locality="", identified_by=sp["identified_by"],
                            date_identified=sp["date_identified"],
                            confidential=sp["confidential"],
                            event_confidential=sp["event_confidential"],
                        )).classes("ex-spec-row w-full")
                        if on_open_specimen:
                            row.on("click", lambda _, c=sp["co_id"]: on_open_specimen(c))
                else:
                    ui.html('<div class="rsheet-muted">No specimens.</div>')
            if media:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Habitat media</div>')
                    _media_block(media)

        with ui.column().classes("w-full lg:w-96 shrink-0 gap-4"):
            if ev["lat"] is not None and ev["lon"] is not None:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Location</div>')
                    _coord_map(ev["lat"], ev["lon"], ev["unc"])
            if any(detail.values()):
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Details</div>')
                    _grid(detail)
            if nearby:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Nearby events</div>')
                    for e in nearby:
                        km = e["distance_m"] / 1000
                        dist = f"{e['distance_m']:.0f} m" if km < 1 else f"{km:.1f} km"
                        row = ui.element("div").classes("ex-spec-row w-full")
                        with row:
                            ui.html(f'<div style="font-size:.85rem">{_html.escape(e["label"])}'
                                    f'</div><div class="rsheet-muted">{dist} away · '
                                    f'{e["n_specimens"]} specimen(s)</div>')
                        if on_open_event:
                            row.on("click", lambda _, i=e["id"]: on_open_event(i))


def _render_specimen(ident, curatorial, det_hist, assocs, life_stages, ext_ids, media,
                     place, ev, *, on_edit, on_open_event, on_reprint=None,
                     already_queued=False) -> None:
    # ── identity banner ──
    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-start gap-3 w-full no-wrap"):
            with ui.column().classes("flex-1 min-w-0 gap-1"):
                name = (rs.name_html(ident["name"], ident["rank"], ident["authorship"])
                        if ident["name"] else '<span class="rs-none">— no identification —</span>')
                bits = rs._bits(ident["sex"], ident["count"])
                qual = f'<span class="rsheet-muted">{_html.escape(ident["qualifier"])}</span>' \
                    if ident["qualifier"] else ""
                typ = f'<span class="rsheet-type">{_html.escape(ident["type_status"])}</span>' \
                    if ident["type_status"] else ""
                ui.html(f'<div class="rsheet-hero">{name} {bits} {qual} {typ}</div>')
                det = rs._det_html(ident["identified_by"], ident["date_identified"])
                lock = rs.lock_html(own=ident["confidential"], from_event=ident["event_confidential"])
                ui.html(f'<span class="rs-cat">{_html.escape(ident["catalog"])}</span>'
                        f'  ·  <span class="rsheet-muted">{_html.escape(ident["collection"] or "")}</span>'
                        f'  {det}  {lock}')
            with ui.row().classes("items-center gap-2 shrink-0"):
                ui.button("Edit", icon="edit", on_click=on_edit).props("no-caps unelevated")
                if on_reprint:
                    def _do_reprint():
                        if on_reprint():           # queued OK → now already in the queue
                            _disable_reprint()

                    reprint_btn = ui.button("Reprint", icon="print", on_click=_do_reprint) \
                        .props("no-caps outline")

                    def _disable_reprint():
                        reprint_btn.props("disable")
                        reprint_btn.tooltip("Already in print queue")

                    if already_queued:
                        _disable_reprint()
                    else:
                        reprint_btn.tooltip(
                            "Add this specimen's labels — locality, identifier and "
                            "every identification — to the print queue")

    # ── two zones: left (media / determinations / ecology) · right (where·when / curatorial) ──
    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("flex-1 min-w-0 gap-4"):
            if media:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Media</div>')
                    _media_block(media)
            if det_hist:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Identifications</div>')
                    _det_block(det_hist)
            if assocs or life_stages or ext_ids:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Life &amp; ecology</div>')
                    _ecology_block(assocs, life_stages, ext_ids)

        with ui.column().classes("w-full lg:w-96 shrink-0 gap-4"):
            with ui.card().classes("w-full shadow-sm"):
                with ui.row().classes("items-center gap-2"):
                    ui.html('<div class="rsheet-hd" style="margin-bottom:0">Collecting event</div>')
                    if ev["event_id"] and on_open_event:
                        ui.button("open", icon="open_in_new",
                                  on_click=lambda: on_open_event(ev["event_id"])) \
                            .props("flat dense no-caps size=sm")
                _where_block(place, ev)
            if any(curatorial.values()):
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">In the collection</div>')
                    _grid(curatorial)


def _grid(fields: dict) -> None:
    rows = "".join(
        f'<dt>{_html.escape(k)}</dt><dd>{_html.escape(str(v))}</dd>'
        for k, v in fields.items() if v)
    if rows:
        ui.html(f'<dl class="rsheet-grid">{rows}</dl>')


def _det_block(dets) -> None:
    for d in dets:
        name = rs.name_html(d["name"], d["rank"], d["authorship"]) if d["name"] else "—"
        q = f' <span class="rsheet-muted">{_html.escape(d["qualifier"])}</span>' if d["qualifier"] else ""
        ty = f' <span class="rsheet-type">{_html.escape(d["type_status"])}</span>' if d["type_status"] else ""
        cur = ' <span class="rs-badge">current</span>' if d["current"] else ""
        meta = "  ·  ".join(x for x in (
            f'det. {d["by"]}' if d["by"] else "", d["date"] or "") if x)
        meta_html = f'<div class="rsheet-muted">{_html.escape(meta)}</div>' if meta else ""
        cls = "rsheet-det cur" if d["current"] else "rsheet-det"
        ui.html(f'<div class="{cls}">{name}{q}{ty}{cur}{meta_html}</div>')


def _ecology_block(assocs, life_stages, ext_ids) -> None:
    for a in assocs:
        q = f' {_html.escape(a["qualifier"])}' if a["qualifier"] else ""
        # the association's own external identifiers (e.g. the iNaturalist observation of the
        # host) — indented under the association so nothing that was recorded is dropped.
        links = "".join(
            f'<div class="rsheet-sublink">🔗 <a href="{_html.escape(e["value"])}" target="_blank" '
            f'rel="noopener">{_html.escape(e["label"] or e["value"])}</a></div>'
            for e in a["ext_ids"])
        ui.html(f'<div class="rsheet-det">{_html.escape(a["rel"])}{q} '
                f'<em>{_html.escape(a["label"])}</em>{links}</div>')
    if life_stages:
        chain = " → ".join(
            _html.escape(" ".join(x for x in (r["stage"], r["date"]) if x)) for r in life_stages)
        ui.html(f'<div class="rsheet-det">life stages: {chain}</div>')
    for e in ext_ids:
        lbl = _html.escape(e["label"] or e["value"])
        url = _html.escape(e["value"])
        ui.html(f'<div class="rsheet-det">🔗 <a href="{url}" target="_blank" '
                f'rel="noopener">{lbl}</a></div>')


def _coord_map(lat, lon, unc) -> None:
    """Coordinates + copy button + a read-only 'View on map'. Shared by the specimen and
    event sheets so the map/copy behaviour is defined once."""
    if lat is None or lon is None:
        return
    unc_txt = f' ±{int(unc)} m' if unc else ""
    # Copy exactly like Specimen Digitization: lat, lon, radius — tab-separated.
    copy_text = f'{lat}\t{lon}\t{"" if unc is None else int(unc)}'

    def _copy(t=copy_text):
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(t)})")
        ui.notify("Coordinates copied", type="positive")

    with ui.row().classes("items-center gap-1 mt-1"):
        ui.html(f'<span class="rsheet-muted">{lat:.5f}, {lon:.5f}{unc_txt}</span>')
        ui.button(icon="content_copy", on_click=_copy).props("flat dense round size=sm") \
            .tooltip("Copy latitude, longitude and radius (tab-separated)")
    # read_only baked in at build time → the dot is locked from init (can't be dragged),
    # regardless of the async open (set_readonly around open() races init and loses).
    _map = build_map_picker(lambda *_: None,
                            default_layer=get_config().map_default_layer, read_only=True)

    def _view_map(m=_map):
        m["fly_to"](lat, lon, unc)   # opens + places the point (init-safe)
    ui.button("View on map", icon="place", on_click=_view_map) \
        .props("flat dense no-caps size=sm").classes("mt-1")


def _where_block(place, ev) -> None:
    # place is geography only (no date/collector) — those follow on the meta line, so the
    # block never repeats itself. The "open event" link lives beside the card header.
    if place:
        ui.html(f'<div style="font-size:.9rem">{_html.escape(place)}</div>')
    meta = "  ·  ".join(x for x in (
        ev["date"], f'leg. {ev["recorded_by"]}' if ev["recorded_by"] else "",
        ev["habitat"], ev["protocol"]) if x)
    if meta:
        ui.html(f'<div class="rsheet-muted mt-1">{_html.escape(meta)}</div>')
    _coord_map(ev["lat"], ev["lon"], ev["unc"])


def _media_item_meta(m) -> str:
    """The metadata lines shown under a media item: caption, then category · dims,
    licence, © rights holder, capture date — each only when present."""
    lines = []
    if m["caption"]:
        lines.append(f'<div class="rsheet-mcap">{_html.escape(m["caption"])}</div>')
    tech = "  ·  ".join(x for x in (m["category"], m["dims"], m["capture_date"]) if x)
    if tech:
        lines.append(f'<div class="rsheet-mmeta">{_html.escape(tech)}</div>')
    if m["license"]:
        lines.append(f'<div class="rsheet-mmeta">{_html.escape(m["license"])}</div>')
    if m["rights_holder"]:
        lines.append(f'<div class="rsheet-mmeta">© {_html.escape(m["rights_holder"])}</div>')
    return "".join(lines)


def _media_block(media) -> None:
    from collections import OrderedDict
    groups: "OrderedDict[str, list]" = OrderedDict()
    for m in media:
        groups.setdefault(m["source"], []).append(m)
    for source, items in groups.items():
        # the source label says WHAT THE MEDIA DEPICTS (Specimen / Event / Biological assoc.)
        ui.html(f'<div class="rsheet-src-hd">{_html.escape(source)}</div>')
        with ui.row().classes("gap-3 flex-wrap items-start"):
            for m in items:
                with ui.element("div").classes("rsheet-mcard"):
                    if m["category"] == "Image":
                        ui.html(f'<a href="{_html.escape(m["url"])}" target="_blank" rel="noopener">'
                                f'<img class="rsheet-thumb" src="{_html.escape(m["url"])}"></a>')
                    else:
                        icon = _CAT_ICON.get(m["category"], "insert_drive_file")
                        ui.html(f'<a class="rsheet-file" href="{_html.escape(m["url"])}" '
                                f'target="_blank" rel="noopener"><span class="material-icons" '
                                f'style="font-size:18px">{icon}</span>{_html.escape(m["name"])}</a>')
                    ui.html(_media_item_meta(m))

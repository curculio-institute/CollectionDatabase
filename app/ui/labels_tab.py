"""Labels tab: the print queue (WYSIWYG sheet preview, label editing, print / clear),
the two "queue labels" cards (identifier labels with their batch statistics, plain
identification labels) and the reserved-codes viewer.

Moved verbatim out of main.py. The print queue's client-side JavaScript (hover
toolbar, capture-phase blur → 'pq_edit') still lives in main.py's head script and
talks to this code by event name only.
"""
from __future__ import annotations

import json
from datetime import datetime
from nicegui import ui
import app.services.identifiers as id_svc
import app.services.labels as lbl_svc
import app.services.repositories as repo_svc
import app.services.print_queue as pq_svc
from app.config import get_config, printed_pdf_dir
from app.ui.plain_det_labels import build_plain_det_labels_card


def build_labels_tab(session_factory, *, refreshers, records_handle, main_tabs):
    # Same names the code below used while it lived inline in main.py.
    _sf = session_factory
    _refreshers = refreshers
    _records_handle = records_handle

    def _with_session(fn):
        with _sf() as s:
            return fn(s)

    with ui.column().classes("w-full max-w-[88rem] mx-auto px-4 pt-6 pb-16 gap-6"):

        # ── Print queue ──────────────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-2 mb-2"):
                ui.label("Print queue").classes("section-label")
                ui.space()
                queue_count_lbl = ui.label("").classes("text-sm") \
                    .style("color:var(--tp-base-soft)")
                # Zoom: transform:scale on the sheet (post-layout, never re-wraps).
                with ui.element("div").classes("pq-zoombar"):
                    ui.icon("zoom_out").classes("text-base") \
                        .style("color:var(--tp-base-soft)")
                    zoom_slider = ui.slider(min=0.4, max=6.0, step=0.1, value=1.5) \
                        .props("dense").classes("w-28")
                    ui.button(icon="fit_screen").props("flat dense round size=sm") \
                        .tooltip("Fit page to width") \
                        .on_click(lambda: ui.run_javascript("window._pqFit && window._pqFit()"))
                    ui.icon("zoom_in").classes("text-base") \
                        .style("color:var(--tp-base-soft)")
                clear_btn  = ui.button("Clear", icon="delete_sweep").props("flat dense")
                print_btn  = ui.button("Print all", icon="print").props("color=secondary")

            # WYSIWYG sheet preview (#37): the REAL label markup + the scoped label
            # CSS (injected below), so what is shown is EXACTLY what prints — same
            # builder as build_pdf. Data & determination labels are contenteditable
            # (print-only override; editing one edits all identical labels); a hover
            # toolbar gives the larger editor / open-in-Records / remove. The whole
            # sheet is zoomed via transform:scale, which scales pixels after layout so
            # lines never re-break (measured: browsers agree on the layout to <0.5 mm).
            _pqcfg = get_config()
            ui.add_head_html("<style>" + lbl_svc.preview_css({
                "data":          _pqcfg.label_border_data,
                "determination": _pqcfg.label_border_determination,
                "identifier":    _pqcfg.label_border_identifier,
            }, paper=_pqcfg.paper_format) + "</style>")
            with ui.element("div").classes("pq-sheet-wrap mt-1"):
                with ui.element("div").classes("pq-sheet-scale"):
                    preview_box = ui.html("").classes("w-full")
            zoom_slider.on_value_change(
                lambda e: ui.run_javascript(f"window._pqZoom && window._pqZoom({e.value})"))

            def _open_in_records(co_id):
                if _records_handle and co_id:
                    _records_handle["open_specimen"](co_id)
                main_tabs.set_value("records")

            def _edit_label(qid, raw_html):
                # The WYSIWYG box (or source field) hands back HTML; sanitize
                # to the safe label subset (italics/bold survive, #45/#46).
                # Empty or == the row's auto text clears the override (→ auto);
                # else apply to every identical label (same auto text).
                clean = lbl_svc.sanitize_override_html(raw_html or "")
                with _sf() as session:
                    with session.begin():
                        auto_clean = lbl_svc.sanitize_override_html(
                            pq_svc.row_auto_html(session, qid))
                        new = None if (not clean or clean == auto_clean) else clean
                        n = pq_svc.set_override_for_identical(session, qid, new)
                verb = "Reset to auto" if new is None else "Applied edit"
                ui.notify(f"{verb} on {n} identical label{'s' if n != 1 else ''}.", type="info")
                _refresh_queue()

            # The contenteditable box's innerHTML cannot ride NiceGUI's event
            # args (the DOM node is stripped before serialisation), so a global
            # capture-phase blur listener (added once in head) reads innerHTML
            # client-side and emits 'pq_edit' with the row id + html.
            ui.on("pq_edit", lambda e: _edit_label(int(e.args["qid"]), e.args["html"]))

            # Rebuilding the whole preview on every single delete floods the socket on a
            # large queue — a rapid burst used to crash the connection / restart the
            # server (#132). Coalesce a burst into ONE rebuild with a short trailing
            # timer; the DB deletes still happen per click.
            _pending_refresh: dict = {"timer": None}

            def _run_pending_refresh():
                _pending_refresh["timer"] = None
                _refresh_queue()

            def _schedule_refresh():
                if _pending_refresh["timer"] is not None:
                    return
                _pending_refresh["timer"] = ui.timer(0.06, _run_pending_refresh, once=True)

            # Hover-toolbar actions on the WYSIWYG sheet (edit / open-in-Records /
            # remove). The floating toolbar (head JS) emits pq_tool with the hovered
            # label's queue id + specimen id.
            def _on_pq_tool(e):
                action = e.args.get("action")
                qid = e.args.get("qid") or ""
                co  = e.args.get("co") or ""
                if action == "records":
                    _open_in_records(int(co) if co else None)
                elif action == "edit" and qid:
                    seed = _with_session(lambda s: pq_svc.row_current_html(s, int(qid)))
                    _open_label_dialog(int(qid), seed)
                elif action == "remove_one" and qid:
                    # Remove just this one label (row), leaving the specimen's
                    # other labels queued.
                    with _sf() as session:
                        with session.begin():
                            pq_svc.remove_item(session, int(qid))
                    _schedule_refresh()
                elif action == "remove_co" and co:
                    # Remove every label for this specimen.
                    with _sf() as session:
                        with session.begin():
                            pq_svc.remove_specimen(session, int(co))
                    _schedule_refresh()
            ui.on("pq_tool", _on_pq_tool)

            # Stable DOM ids for the dialog editor (only one open at a time).
            _DLG_ED, _DLG_SRC = "pq-dlg-editor", "pq-dlg-source"

            def _open_label_dialog(qid, seed_html):
                """Larger editor for a queued label — a readable WYSIWYG area
                        with a Bold/Italic toolbar (select text → click), plus a
                        raw-HTML source toggle (#45). The inline box on the sheet is
                        fine for quick tweaks; this window is for longer text and
                        explicit formatting without hand-editing tags."""
                mode = {"src": False}
                with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl gap-2"):
                    ui.label("Edit label for print").classes("text-base font-semibold")
                    ui.label("Select text and click B / I to format, or switch to "
                             "HTML source. Applies to all identical labels; does not "
                             "change the record.").classes("text-xs") \
                        .style("color:var(--tp-base-soft)")
                    with ui.row().classes("items-center gap-1"):
                        # mousedown.preventDefault keeps the editor's selection
                        # alive (a focused toolbar button would otherwise collapse
                        # it); styleWithCSS=false forces <b>/<i> tags, which the
                        # sanitizer maps to <strong>/<em>.
                        b_btn = ui.button(icon="format_bold").props("flat dense").tooltip("Bold")
                        i_btn = ui.button(icon="format_italic").props("flat dense").tooltip("Italic")
                        b_btn.on("mousedown", js_handler="(e)=>{e.preventDefault();"
                                 "document.execCommand('styleWithCSS',false,false);"
                                 "document.execCommand('bold',false,null);}")
                        i_btn.on("mousedown", js_handler="(e)=>{e.preventDefault();"
                                 "document.execCommand('styleWithCSS',false,false);"
                                 "document.execCommand('italic',false,null);}")
                        ui.space()
                        src_btn = ui.button(icon="code").props("flat dense") \
                            .tooltip("Toggle HTML source")
                    editor = (ui.element("div")
                              .props(f'contenteditable=true id={_DLG_ED}')
                              .classes("pq-dlg-editor"))
                    source = (ui.textarea()
                              .props(f"id={_DLG_SRC} outlined")
                              .classes("pq-dlg-source w-full"))
                    source.set_visibility(False)
                    with ui.row().classes("justify-end w-full gap-2 mt-1"):
                        ui.button("Abort").props("flat").on_click(dlg.close)
                        save_btn = ui.button("Save & close").props("color=primary")

                # Seed both surfaces (editor innerHTML set imperatively so Vue
                # never re-binds/clobbers it; the textarea via its value).
                ui.run_javascript(
                    f"document.getElementById('{_DLG_ED}').innerHTML = {json.dumps(seed_html or '')};")
                source.value = seed_html or ""

                async def _toggle_src():
                    if not mode["src"]:
                        html = await ui.run_javascript(
                            f"document.getElementById('{_DLG_ED}').innerHTML")
                        source.value = html or ""
                        editor.set_visibility(False); source.set_visibility(True)
                        mode["src"] = True
                    else:
                        ui.run_javascript(
                            f"document.getElementById('{_DLG_ED}').innerHTML = "
                            f"{json.dumps(source.value or '')};")
                        source.set_visibility(False); editor.set_visibility(True)
                        mode["src"] = False
                src_btn.on_click(_toggle_src)

                async def _save():
                    html = (source.value if mode["src"]
                            else await ui.run_javascript(
                                f"document.getElementById('{_DLG_ED}').innerHTML"))
                    dlg.close()
                    _edit_label(qid, html)
                save_btn.on_click(_save)

                # Per-action dialog: delete on close so its timers don't leak.
                dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                dlg.open()

            def _refresh_queue():
                summary = _with_session(pq_svc.queue_summary)
                queue_count_lbl.set_text(
                    f"{summary.total} queued  "
                    f"({summary.n_data} data · "
                    f"{summary.n_determination} det · "
                    f"{summary.n_identifier} id)"
                    if summary.total else "empty"
                )
                if not summary.total:
                    preview_box.set_content(
                        '<div class="pq-prev-empty">Nothing queued yet — labels are '
                        'added automatically when you save specimens or generate '
                        'identifier codes.</div>')
                    return
                # The REAL label sheet (same builder as the print PDF), editable and
                # scoped. What is shown is exactly what prints.
                preview_box.set_content(_with_session(pq_svc.preview_sheet))
                # Fit the A4 page to the preview width once it's in the DOM.
                ui.timer(0.05, lambda: ui.run_javascript(
                    "window._pqFit && window._pqFit()"), once=True)

            def _print_all():
                summary = _with_session(pq_svc.queue_summary)
                if summary.total == 0:
                    ui.notify("Queue is empty.", type="warning")
                    return
                now = datetime.now()
                stamp_human = now.strftime("%Y-%m-%d %H:%M")
                stamp_file  = now.strftime("%Y%m%d-%H%M%S")
                pdf = _with_session(
                    lambda s: pq_svc.build_pdf(s, stamp_human)
                )
                # Archive every printed sheet to disk for reprint/audit
                # before clearing the queue.
                archive = printed_pdf_dir() / f"labels_{stamp_file}.pdf"
                archive.write_bytes(pdf)
                ui.download(pdf, filename=archive.name,
                            media_type="application/pdf")
                with _sf() as session:
                    with session.begin():
                        pq_svc.clear_queue(session)
                _refresh_queue()
                ui.notify(f"Labels downloaded — queue cleared. Saved {archive.name}",
                          type="positive")

            def _do_clear_queue():
                with _sf() as session:
                    with session.begin():
                        pq_svc.clear_queue(session)
                _refresh_queue()

            def _clear_queue():
                # Clearing removes queued labels WITHOUT printing. The reserved
                # identifier codes are NOT deleted — they stay in their batch
                # (the "Staged" count) and can be re-added to the queue / printed
                # from the Labels tab. Confirm first so codes are never lost by a
                # stray click (user request).
                summary = _with_session(pq_svc.queue_summary)
                if summary.total == 0:
                    ui.notify("Queue is already empty.", type="info")
                    return
                dlg = ui.dialog()
                with dlg, ui.card().classes("min-w-[420px]"):
                    ui.label("Clear the print queue?").classes("section-label mb-1")
                    ui.label(
                        f"{summary.total} queued label(s) will be removed "
                        "without printing. Reserved identifier codes are NOT "
                        "lost — they stay in their batch and can be re-added any "
                        "time from the Labels tab → Reserved codes → "
                        "“Add to print queue”."
                    ).classes("text-sm").style("color:var(--tp-base-soft)")
                    with ui.row().classes("justify-end w-full gap-2 mt-3"):
                        ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
                        def _confirm():
                            dlg.close()
                            _do_clear_queue()
                        ui.button("Clear without printing", on_click=_confirm) \
                            .props("no-caps color=negative")
                dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
                dlg.open()

            print_btn.on_click(_print_all)
            clear_btn.on_click(_clear_queue)
            _refresh_queue()
            _refreshers["queue"] = _refresh_queue

        # ── Batch dashboard ──────────────────────────────────────
        stats = _with_session(id_svc.batch_stats)
        _batch_stat_labels: dict[str, object] = {}
        _reserved_count_ref = [None]   # filled in by the reserved-codes card below

        def _refresh_batch_stats():
            s = _with_session(id_svc.batch_stats)
            _batch_stat_labels["Batches"].set_text(str(s.total_batches))
            _batch_stat_labels["Total codes"].set_text(str(s.total_codes))
            _batch_stat_labels["Assigned"].set_text(str(s.total_assigned))
            _batch_stat_labels["Staged"].set_text(str(s.total_reserved))
            if _reserved_count_ref[0]:
                n = sum(b.n_reserved for b in _with_session(id_svc.all_batches_with_reserved))
                _reserved_count_ref[0].set_text(f"{n} staged")
        _refreshers["batch_stats"] = _refresh_batch_stats

        # The two "queue labels" cards sit side by side on a wide window (each form is
        # narrow; full width stretched them) and stack on a narrow one.
        with ui.element("div").classes("w-full grid grid-cols-1 lg:grid-cols-2 gap-4"):
            # ── Mode A: identifier-only labels ───────────────────────
            with ui.card().classes("w-full shadow-sm"):
                ui.label("Identifier labels").classes("section-label mb-2")
                ui.label(
                    "Pre-print blank identifier labels to pin onto undigitised "
                    "specimens. Each label carries a unique sequential code "
                    "(e.g. JJPC-00001) and QR code. Codes are reserved in the "
                    "database immediately."
                ).classes("text-sm mb-4").style("color:var(--tp-base-soft)")

                # Batch statistics — folded into this card (they describe
                # identifier codes only), refreshed by _refresh_batch_stats.
                with ui.row().classes("w-full gap-6 mb-4"):
                    for label, value in [
                        ("Batches",     stats.total_batches),
                        ("Total codes", stats.total_codes),
                        ("Assigned",    stats.total_assigned),
                        ("Staged",      stats.total_reserved),
                    ]:
                        with ui.column().classes("gap-0 items-start"):
                            _batch_stat_labels[label] = ui.label(str(value)).style(
                                "font-size:1.3rem; font-weight:300; "
                                "line-height:1.2; color:var(--tp-secondary);"
                            )
                            ui.label(label).classes("section-label")

                with ui.row().classes("items-center gap-4"):
                    count_input = (
                        ui.number("Number of labels; 400 are one page", value=20, min=1, max=500, step=1)
                        .classes("w-60")
                    )
                    id_status = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")

                with ui.row().classes("mt-4 gap-3 items-end"):
                    gen_btn = ui.button("Generate", icon="queue")

                def _generate_id_labels():
                    n = int(count_input.value or 1)
                    with _sf() as session:
                        with session.begin():
                            # The catalog-number prefix comes from the default
                            # collection (#83), not a config string.
                            _default_repo = repo_svc.get_default(session)
                            if _default_repo is None:
                                ui.notify(
                                    "No default collection set — open Settings "
                                    "to choose one.", type="negative")
                                return
                            coll_code = _default_repo.collection_code
                            batch_id, _ = id_svc.reserve_sequential_codes(session, coll_code, n)
                            # Enqueue the freshly reserved batch through the one
                            # shared seam (dedup + "New identifiers" group) — the
                            # same path as "Add to print queue" for an existing
                            # batch. Right after reserve the dedup is a no-op.
                            pq_svc.requeue_batch_identifiers(session, batch_id)
                    # Queue-only: printing happens solely via the Print queue
                    # tab. Emitting a PDF here too risked a double print (print
                    # now + print the queue later = duplicate identifier labels).
                    id_status.set_text(f"✓ {n} codes reserved and added to the print queue")
                    _refresh_batch_stats()
                    _refresh_queue()

                gen_btn.on_click(_generate_id_labels)

            # ── Mode B: plain identification labels (no specimen) ─────
            build_plain_det_labels_card(_sf, on_queued=lambda: _refresh_queue())

        # ── Reserved codes viewer ────────────────────────────────
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("items-center gap-2"):
                ui.label("Reserved codes").classes("section-label")
                ui.space()
                reserved_count = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")
                _reserved_count_ref[0] = reserved_count
                show_btn = ui.button("Show", icon="visibility").props("flat dense")

            codes_container = ui.element("div").classes("w-full")
            codes_visible = {"open": False}

            def _load_reserved():
                batches = _with_session(id_svc.all_batches_with_reserved)
                total = sum(b.n_reserved for b in batches)
                reserved_count.set_text(f"{total} staged")
                codes_container.clear()
                if not batches:
                    with codes_container:
                        ui.label("No reserved codes.") \
                          .classes("text-sm italic mt-2") \
                          .style("color:var(--tp-base-soft)")
                    return
                with codes_container:
                    for b in batches:
                        ts  = b.created_at[:16].replace("T", "  ")
                        note = "" if b.n_reserved == b.n_total \
                               else f"  · {b.n_total - b.n_reserved} assigned"
                        with ui.column().classes("w-full mt-3 gap-1"):
                            with ui.row().classes("items-center gap-2 w-full"):
                                ui.label(f"{ts}  —  {b.n_reserved} staged{note}") \
                                  .classes("text-xs font-medium") \
                                  .style("color:var(--tp-base-soft)")
                                ui.space()
                                def _requeue(bid=b.batch_id):
                                    with _sf() as s:
                                        with s.begin():
                                            n = pq_svc.requeue_batch_identifiers(s, bid)
                                    if n:
                                        ui.notify(f"Added {n} code(s) to the print queue.",
                                                  type="positive")
                                    else:
                                        ui.notify("All reserved codes from this batch "
                                                  "are already in the queue.", type="info")
                                    _refresh_queue()
                                ui.button("Add to print queue", icon="playlist_add",
                                          on_click=_requeue) \
                                    .props("flat dense no-caps size=sm color=secondary")
                            codes = _with_session(
                                lambda s, bid=b.batch_id: id_svc.codes_for_batch(s, bid)
                            )
                            with ui.row().classes("flex-wrap gap-1"):
                                for c in codes:
                                    ui.badge(c).props("outline color=secondary")

            def _toggle_reserved():
                codes_visible["open"] = not codes_visible["open"]
                if codes_visible["open"]:
                    _load_reserved()
                    show_btn.props("flat dense icon=visibility_off")
                    show_btn.set_text("Hide")
                else:
                    codes_container.clear()
                    show_btn.props("flat dense icon=visibility")
                    show_btn.set_text("Show")

            show_btn.on_click(_toggle_reserved)
            # Show total count on load without revealing codes
            def _init_count(s):
                n = sum(b.n_reserved for b in id_svc.all_batches_with_reserved(s))
                reserved_count.set_text(f"{n} staged")
            _with_session(_init_count)

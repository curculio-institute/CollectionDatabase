"""Controlled Vocabularies → "Tags & data attributes" and "Biological relationships"
(spec 2026-10-14 "Controlled Vocabularies tab — two new sub-tabs").

Both sub-tabs are one builder: a table with a link chip per row, a Verify button (one
fetch per kind, user-triggered only), a link dialog (searchable TaxonWorks list; a TW term
already linked to another local entry is shown disabled with that entry's name), and the
usual edit / merge / delete / add. Nothing is decided silently: a renamed TW term opens a
dialog with use TW name / keep local name / unlink per entry.
"""
from __future__ import annotations

from nicegui import ui

import app.services.annotations as ann
import app.services.biological as bio
import app.services.taxonworks as tw_svc
import app.services.tw_links as links
from app.models import AnnotationTerm, BiologicalRelationship
from app.ui.confirm_dialog import choose, confirm
from app.ui.annotation_chips import CSS as CHIP_CSS, chip_html, swatch_html
from app.ui.merge_target_list import build_merge_target_list

_KIND_LABEL = {"tag": "Tag", "data_attribute": "Data attribute"}
_LINK_KIND = {"tag": links.KEYWORD, "data_attribute": links.PREDICATE}
_TERM_KIND = {links.KEYWORD: "tag", links.PREDICATE: "data_attribute"}
_STATE_CHIP = {"local_only": ("local only", "grey-6"),
               "verified": ("TaxonWorks ✓", "positive"),
               "not_verified": ("not verified", "warning")}
_ALLOW_COLS = (("allow_specimen", "specimens"), ("allow_event", "events"),
               ("allow_association", "associations"))


def build_annotation_terms_section(session_factory) -> None:
    _build_section(session_factory, terms=True)


def build_relationships_section(session_factory) -> None:
    _build_section(session_factory, terms=False)


def chip_label(state_label: str, local_name: str, tw_name: str | None,
               linked: bool) -> str:
    """A linked entry whose TaxonWorks name differs from the local one says so on the
    chip: the export uses the TW name, so the mismatch must not be invisible."""
    if linked and tw_name is not None and tw_name != local_name:
        return f"{state_label} as '{tw_name}'"
    return state_label


def chip_tooltip(linked: bool, tw_name: str | None) -> str:
    if linked:
        return f"Linked to TaxonWorks '{tw_name}' — unlink first to change"
    return "Click to link to a TaxonWorks term"


def needs_name_choice(local_name: str, picked_name: str) -> bool:
    """Ask Keep local / Use TaxonWorks name when the picked TW name differs
    (case-sensitive, after strip)."""
    return (local_name or "").strip() != (picked_name or "").strip()


def _chip(row, host: str, detail: dict) -> tuple[str, str]:
    state = links.link_state(row, host)
    outcome = detail.get(row.id)
    if state == "not_verified" and outcome is not None:
        if outcome.state == "name_differs":
            return f'name differs: "{outcome.tw_name}"', "negative"
        if outcome.state == "not_found":
            return "not found on this instance", "negative"
    label, color = _STATE_CHIP[state]
    return chip_label(label, row.name, row.tw_name, row.tw_id is not None), color


_GONE = "This entry no longer exists — it was deleted elsewhere. The list will refresh."


def _build_section(session_factory, *, terms: bool) -> None:
    ui.add_head_html(CHIP_CSS)
    model = AnnotationTerm if terms else BiologicalRelationship
    kinds = (links.KEYWORD, links.PREDICATE) if terms else (links.RELATIONSHIP,)
    detail: dict[int, links.VerifyOutcome] = {}   # latest Verify, this session only

    def _get(s, row_id: int):
        row = s.get(model, row_id)
        if row is None:
            raise ValueError(_GONE)
        return row

    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-2 mb-1 w-full"):
            ui.label("Tags & data attributes" if terms else "Biological relationships") \
                .classes("section-label")
            ui.label(
                "Flags and name → value facts for specimens, events and associations. "
                "Link a term to TaxonWorks to export it; an unlinked term stays local."
                if terms else
                "The relationship vocabulary of biological associations (e.g. feeds on). "
                "An optional TaxonWorks link is set here only — never on launch."
            ).classes("text-sm").style("color:var(--tp-base-soft)")
            ui.space()
            verify_btn = ui.button("Verify", icon="cloud_sync") \
                .props("flat no-caps color=secondary") \
                .tooltip("Fetch the TaxonWorks list once and re-check every link")
        ui.separator().classes("mb-3")

        def _rows() -> list[dict]:
            host = tw_svc.configured_host()
            out = []
            with session_factory() as s:
                for kind in kinds:
                    for r in links.rows_of_kind(s, kind):
                        label, color = _chip(r, host, detail)
                        row = {"id": r.id, "name": r.name, "chip": label,
                               "chip_color": color, "linked": r.tw_id is not None,
                               "tw_name": r.tw_name or "",
                               "chip_tip": chip_tooltip(r.tw_id is not None, r.tw_name),
                               "link_kind": kind}
                        if terms:
                            row.update(kind=_KIND_LABEL[r.kind], css_color=r.css_color or "",
                                       **{c: bool(getattr(r, c)) for c, _ in _ALLOW_COLS})
                        out.append(row)
            return out

        cols = [{"name": "name", "label": "Name", "field": "name", "align": "left",
                 "sortable": True}]
        if terms:
            cols += [{"name": "kind", "label": "Type", "field": "kind", "align": "left",
                      "sortable": True},
                     {"name": "allow", "label": "Offered for", "field": "id", "align": "left"},
                     {"name": "colour", "label": "Colour", "field": "css_color",
                      "align": "left"}]
        cols += [{"name": "chip", "label": "TaxonWorks", "field": "chip", "align": "left"},
                 {"name": "actions", "label": "", "field": "id", "align": "right"}]
        table = ui.table(columns=cols, rows=_rows(), row_key="id") \
            .classes("w-full").props("flat dense")
        if terms:
            toggles = "".join(
                f'<q-toggle dense size="xs" label="{label}" '
                f':model-value="props.row.{col}" '
                f"@update:model-value=\"v => $parent.$emit('allow', "
                f"{{id: props.row.id, field: '{col}', value: v}})\" />"
                for col, label in _ALLOW_COLS)
            table.add_slot("body-cell-allow", f'<q-td :props="props">{toggles}</q-td>')
            table.add_slot("body-cell-colour", """
                <q-td :props="props">
                  <span v-if="props.row.css_color" class="ann-swatch"
                        :style="{background: props.row.css_color,
                                 border: '1px solid rgba(0,0,0,.2)'}"></span>
                </q-td>""")
        table.add_slot("body-cell-chip", """
            <q-td :props="props">
              <q-badge :color="props.row.chip_color" :label="props.row.chip"
                       class="cursor-pointer" @click="$parent.$emit('link', props.row)">
                <q-tooltip>{{ props.row.chip_tip }}</q-tooltip>
              </q-badge>
            </q-td>""")
        table.add_slot("body-cell-actions", """
            <q-td :props="props">
              <q-btn v-if="!props.row.linked" flat dense round icon="link" size="xs"
                     @click="$parent.$emit('link', props.row)">
                <q-tooltip>Link to a TaxonWorks term</q-tooltip></q-btn>
              <q-btn v-else flat dense round icon="link_off" size="xs"
                     @click="$parent.$emit('unlink', props.row)">
                <q-tooltip>Unlink — becomes local only (asks first)</q-tooltip></q-btn>
              <q-btn flat dense round icon="edit" size="xs"
                     @click="$parent.$emit('edit', props.row)">
                <q-tooltip>Edit</q-tooltip></q-btn>
              <q-btn flat dense round icon="merge_type" size="sm" style="color:#f97316"
                     @click="$parent.$emit('merge', props.row)">
                <q-tooltip>Merge into another entry</q-tooltip></q-btn>
              <q-btn flat dense round icon="delete" size="xs" color="negative"
                     @click="$parent.$emit('delete', props.row)">
                <q-tooltip>Delete (asks first; refused while in use)</q-tooltip></q-btn>
            </q-td>""")

        def _refresh() -> None:
            table.rows = _rows()
            table.update()

        ui.timer(2.0, _refresh)

        # ── writes ──────────────────────────────────────────────────────────
        def _write(fn, ok_msg: str | None = None) -> bool:
            try:
                with session_factory() as s:
                    with s.begin():
                        fn(s)
            except ValueError as exc:
                ui.notify(str(exc), type="warning", multi_line=True, timeout=8000)
                return False
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative", multi_line=True)
                return False
            _refresh()
            if ok_msg:
                ui.notify(ok_msg, type="positive")
            return True

        def _allow(args: dict) -> None:
            _write(lambda s: ann.update_term(s, int(args["id"]),
                                             **{args["field"]: bool(args["value"])}))

        def _usage(rid: int) -> str:
            with session_factory() as s:
                if terms:
                    return f"{ann.term_usage(s, rid)} record(s)"
                return (f"{bio.relationship_vocab._count_references(s, rid)} "
                        f"association(s)")

        async def _unlink(row: dict) -> None:
            tail = ("is no longer exported" if terms else "is no longer linked")
            if not await confirm(
                    title="Unlink from TaxonWorks?",
                    body=(f"'{row['name']}' is linked to TaxonWorks '{row['tw_name']}' and "
                          f"used on {_usage(int(row['id']))}. Unlinked, it stays here but "
                          f"{tail}, and re-linking needs a fresh TaxonWorks fetch."),
                    action_label="Unlink", action_color="warning"):
                return
            detail.pop(int(row["id"]), None)
            _write(lambda s: links.unlink(s, _get(s, int(row["id"]))),
                   f"'{row['name']}' is local only now.")

        async def _delete(row: dict) -> None:
            if not await confirm(title="Delete?",
                                 body=f"Delete '{row['name']}'? This cannot be undone.",
                                 action_label="Delete"):
                return
            if terms:
                _write(lambda s: ann.delete_term(s, int(row["id"])), "Deleted.")
            else:
                _write(lambda s: bio.relationship_vocab.delete(s, int(row["id"])), "Deleted.")

        # ── link dialog ─────────────────────────────────────────────────────
        def _open_link(kinds_shown: tuple[str, ...], row_id: int | None,
                       create: dict | None = None) -> None:
            """Link an existing row (``row_id``; one list of its own kind) or create one
            from a TW term picked in the Tags / Data attributes tab (``create`` holds the
            new term's allow flags). Each kind's candidates are fetched lazily, the first
            time its panel is shown."""
            host = tw_svc.configured_host()
            tab_label = {links.KEYWORD: "Tags", links.PREDICATE: "Data attributes"}

            with ui.dialog() as dlg, ui.card().classes("w-[540px]"):
                ui.label("Link to a TaxonWorks term" if len(kinds_shown) > 1 else
                         f"Link to a TaxonWorks {kinds_shown[0]}").classes("section-label mb-1")
                ui.label(f"From {host}. Picking a term links it and confirms it at once.") \
                    .classes("text-xs").style("color:var(--tp-base-soft)")

                async def _pick(c: links.TwTerm, kind: str) -> None:
                    warning: list[str | None] = [None]
                    rename_to: str | None = None
                    if row_id is not None:
                        with session_factory() as s:
                            cur = s.get(model, row_id)
                            local = cur.name if cur is not None else None
                        if local is not None and needs_name_choice(local, c.name):
                            ans = await choose(
                                title="Names differ",
                                body=f"Local name '{local}', TaxonWorks name '{c.name}'.",
                                options=[("keep", "Keep local name", "secondary"),
                                         ("use", "Use TaxonWorks name", "primary")])
                            if ans is None:
                                return
                            if ans == "use":
                                rename_to = c.name

                    def fn(s):
                        if row_id is None:
                            row = (ann.create_term(s, kind=_TERM_KIND[kind], name=c.name,
                                                   **(create or {}))
                                   if terms else bio.create_relationship(s, c.name))
                        else:
                            row = _get(s, row_id)
                            if rename_to is not None:
                                if terms:
                                    ann.update_term(s, row_id, name=rename_to)
                                else:
                                    bio.rename_relationship(s, row_id, rename_to)
                        warning[0] = links.link(s, row, c)   # host comes from the fetch (c.host)
                    if not _write(fn, f"Linked to TaxonWorks '{c.name}'."):
                        return
                    if row_id is not None:
                        detail.pop(row_id, None)
                    if warning[0]:
                        ui.notify(warning[0], type="warning", multi_line=True, timeout=8000)
                    dlg.close()

                def _panel(kind: str) -> callable:
                    """Build one kind's search + list; return its async ``load``."""
                    search = ui.input(placeholder="Search TaxonWorks…") \
                        .props("dense outlined clearable debounce=150").classes("w-full mt-2")
                    box = ui.element("div").classes("w-full mt-1 overflow-y-auto") \
                        .style("max-height:20rem")
                    state: dict = {"cands": None, "held": {}, "loading": False}

                    def _note(text: str, color: str = "var(--tp-base-soft)") -> None:
                        box.clear()
                        with box:
                            ui.label(text).classes("text-sm px-3 py-2").style(f"color:{color}")

                    def _render() -> None:
                        if state["cands"] is None:
                            return
                        needle = (search.value or "").strip().casefold()
                        hits = [c for c in state["cands"] if needle in c.name.casefold()]
                        if not hits:
                            _note("No TaxonWorks term matches.")
                            return
                        box.clear()
                        with box:
                            for c in hits[:200]:
                                holder = state["held"].get(c.id)
                                el = ui.element("div").classes(
                                    "w-full flex items-center gap-2 px-3 py-2 rounded "
                                    + ("opacity-50" if holder else
                                       "cursor-pointer hover:bg-slate-50 dark:hover:bg-white/5"))
                                with el:
                                    colour, _w = ann.tw_css_color(c.css_color)
                                    ui.html(chip_html(c.name, colour))
                                    ui.label(f"id {c.id}").classes("text-xs") \
                                        .style("color:var(--tp-base-soft)")
                                    if holder:
                                        ui.label(f"linked to '{holder}'") \
                                            .classes("text-xs ml-auto")
                                if not holder:
                                    el.on("click", lambda _, c=c: _pick(c, kind))

                    async def load() -> None:
                        if state["cands"] is not None or state["loading"]:
                            return
                        state["loading"] = True
                        _note("Loading from TaxonWorks…")
                        try:
                            cands = await links.fetch_candidates(kind)
                        except tw_svc.TaxonWorksUnreachable as exc:
                            _note(str(exc), "var(--q-negative)")
                            return
                        except Exception as exc:
                            _note(f"Could not load: {exc}", "var(--q-negative)")
                            return
                        finally:
                            state["loading"] = False
                        with session_factory() as s:
                            state["held"] = {r.tw_id: r.name
                                             for r in links.rows_of_kind(s, kind)
                                             if r.tw_id is not None and r.id != row_id}
                        state["cands"] = cands
                        _render()

                    search.on_value_change(lambda _: _render())
                    return load

                loaders: dict[str, callable] = {}
                if len(kinds_shown) > 1:
                    with ui.tabs(value=kinds_shown[0]).props("dense align=left") as tabs:
                        for k in kinds_shown:
                            ui.tab(k, label=tab_label[k]).props("no-caps")
                    with ui.tab_panels(tabs, value=kinds_shown[0]).classes("w-full"):
                        for k in kinds_shown:
                            with ui.tab_panel(k).classes("p-0"):
                                loaders[k] = _panel(k)

                    async def _on_tab(e) -> None:
                        await loaders[e.value]()
                    tabs.on_value_change(_on_tab)
                else:
                    loaders[kinds_shown[0]] = _panel(kinds_shown[0])
                with ui.row().classes("w-full justify-end mt-2"):
                    ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()
            ui.timer(0.05, loaders[kinds_shown[0]], once=True)

        # ── verify + name-differs resolution ────────────────────────────────
        def _open_name_differs(outcomes: list[links.VerifyOutcome]) -> None:
            with ui.dialog() as dlg, ui.card().classes("w-[580px]"):
                ui.label("Renamed on TaxonWorks").classes("section-label")
                ui.label("Choose per entry — nothing is decided for you.") \
                    .classes("text-sm").style("color:var(--tp-base-soft)")
                for o in outcomes:
                    with ui.column().classes("w-full gap-1 mt-2") as rbox:
                        ui.label(f"'{o.name}' — TaxonWorks now calls it '{o.tw_name}'") \
                            .classes("text-sm font-medium")
                        with ui.row().classes("gap-2"):
                            for choice, label in (("use_tw", "Use TW name"),
                                                  ("keep_local", "Keep local name"),
                                                  ("unlink", "Unlink")):
                                def _resolve(_, o=o, choice=choice, rbox=rbox):
                                    # The outcome carries the observed host + TW name.
                                    if _write(lambda s: links.resolve_name_difference(
                                            s, _get(s, o.row_id), o, choice)):
                                        detail.pop(o.row_id, None)
                                        rbox.clear()
                                        with rbox:
                                            ui.label(f"'{o.name}': done.").classes("text-sm")
                                ui.button(label, on_click=_resolve) \
                                    .props("flat dense no-caps color=secondary")
                with ui.row().classes("w-full justify-end mt-3"):
                    ui.button("Close", on_click=dlg.close).props("flat no-caps")
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        async def _verify() -> None:
            verify_btn.props("loading")
            outcomes: list[links.VerifyOutcome] = []
            try:
                host = tw_svc.configured_host()
                if not host:
                    ui.notify("No TaxonWorks API base URL set — Settings → TaxonWorks "
                              "connection.", type="negative", multi_line=True)
                    return
                # Every network fetch first, with NO session open: an uncommitted write
                # held across an await would lock the database for the whole app.
                fetched = {}
                for kind in kinds:
                    fetched[kind] = await links.fetch_candidates(kind)
                if any(c.host != host for cs in fetched.values() for c in cs):
                    ui.notify("The configured TaxonWorks host changed during the fetch — "
                              "try again.", type="negative", multi_line=True)
                    return
                with session_factory() as s:
                    for kind in kinds:
                        outcomes += links.apply_verification(s, kind, fetched[kind],
                                                             host=host)
                    s.commit()
            except tw_svc.TaxonWorksUnreachable as exc:
                ui.notify(str(exc), type="negative", multi_line=True, timeout=8000)
                return
            except Exception as exc:
                ui.notify(f"Verify failed: {exc}", type="negative", multi_line=True)
                return
            finally:
                verify_btn.props(remove="loading")
            detail.clear()
            detail.update({o.row_id: o for o in outcomes})
            _refresh()
            for o in outcomes:
                if o.color_warning:
                    ui.notify(f"{o.name}: {o.color_warning}", type="warning", multi_line=True)
            ok = sum(o.state == "verified" for o in outcomes)
            ui.notify(f"{ok} of {len(outcomes)} link(s) confirmed on "
                      f"{tw_svc.configured_host()}.",
                      type="positive" if ok == len(outcomes) else "warning")
            differs = [o for o in outcomes if o.state == "name_differs"]
            if differs:
                _open_name_differs(differs)

        verify_btn.on_click(_verify)

        # ── edit dialog ─────────────────────────────────────────────────────
        def _open_edit(row: dict) -> None:
            rid = int(row["id"])
            try:
                with session_factory() as s:
                    obj = _get(s, rid)
                    name0, linked = obj.name, obj.tw_id is not None
                    colour0 = (obj.css_color or "") if terms else ""
            except ValueError as exc:
                ui.notify(str(exc), type="warning", multi_line=True)
                _refresh()
                return
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label("Edit term" if terms else "Edit relationship") \
                    .classes("section-label mb-2")
                name_in = ui.input("Name *", value=name0).classes("w-full")
                colour_in = None
                if terms:
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        colour_in = ui.input("Colour", value=colour0,
                                             placeholder="#254d0d").classes("flex-1")
                        sw = ui.html(swatch_html(colour0 or None))

                    def _live(_):
                        try:
                            sw.set_content(swatch_html(ann.normalize_css_color(colour_in.value)))
                        except ValueError:
                            sw.set_content(swatch_html(None))
                    colour_in.on_value_change(_live)
                    if linked:
                        colour_in.props("readonly")
                        ui.label("Colour from TaxonWorks while linked.").classes("text-xs") \
                            .style("color:var(--tp-base-soft)")

                def _save() -> None:
                    if terms:
                        kw = {"name": name_in.value}
                        if not linked:
                            kw["css_color"] = colour_in.value
                        fn = lambda s: ann.update_term(s, rid, **kw)
                    else:
                        fn = lambda s: bio.rename_relationship(s, rid, name_in.value)
                    if _write(fn, "Saved."):
                        dlg.close()

                with ui.row().classes("w-full justify-end gap-2 mt-3"):
                    ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
                    ui.button("Save", on_click=_save).props("no-caps color=secondary")
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        # ── merge dialog ────────────────────────────────────────────────────
        def _open_merge(row: dict) -> None:
            absorb_id = int(row["id"])
            try:
                with session_factory() as s:
                    absorb = _get(s, absorb_id)
                    absorb_name, absorb_linked = absorb.name, absorb.tw_id is not None
                    others = {r.id: r.name
                              for r in links.rows_of_kind(s, links.kind_of(absorb))
                              if r.id != absorb_id}
            except ValueError as exc:
                ui.notify(str(exc), type="warning", multi_line=True)
                _refresh()
                return
            chosen: dict = {"keep": None}
            with ui.dialog() as dlg, ui.card().classes("w-[500px]"):
                ui.label("Merge").classes("section-label mb-2")
                ui.label(f"Absorbed — deleted after merge: {absorb_name}") \
                    .classes("text-sm font-semibold").style("color:#f97316")
                keep_lbl = ui.label("— pick the entry to keep below —").classes("text-sm")
                targets = build_merge_target_list(lambda oid: _pick(oid))
                info = ui.label("").classes("text-sm mt-2").style("color:var(--tp-base-soft)")
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
                    confirm = ui.button("Merge", icon="merge_type").props("no-caps") \
                        .style("background:#f97316; color:white")
                confirm.set_enabled(False)

            def _pick(oid: int) -> None:
                chosen["keep"] = oid
                targets["show"](others, oid)
                keep_lbl.set_text(f"Kept — receives all references: {others[oid]}")
                ok, msg = True, ""
                if absorb_linked:
                    ok, msg = False, f"Unlink '{absorb_name}' first — a merge never moves a link."
                else:
                    with session_factory() as s:
                        if terms:
                            try:
                                p = ann.merge_preview(s, keep_id=oid, absorb_id=absorb_id)
                            except ValueError as exc:
                                ok, msg = False, str(exc)
                            else:
                                ok = not p.conflicts
                                msg = (f"{p.repoint} annotation(s) re-pointed, {p.fold} "
                                       f"duplicate(s) folded." if ok else
                                       "Refused — different values on: "
                                       + "; ".join(p.conflicts[:5]))
                        else:
                            try:
                                p = bio.relationship_vocab.merge_preview(
                                    s, keep_id=oid, absorb_id=absorb_id)
                            except ValueError as exc:
                                ok, msg = False, str(exc)
                            else:
                                msg = f"{p.reference_count} association(s) re-pointed."
                info.set_text(msg)
                confirm.set_enabled(ok)

            def _confirm() -> None:
                keep = chosen["keep"]
                fn = ((lambda s: ann.merge_terms(s, keep_id=keep, absorb_id=absorb_id))
                      if terms else
                      (lambda s: bio.merge_relationships(s, keep_id=keep, absorb_id=absorb_id)))
                if _write(fn, "Merged."):
                    dlg.close()

            confirm.on_click(_confirm)
            targets["show"](others, None, reset_search=True)
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        async def _link_clicked(row: dict) -> None:
            if row["linked"]:
                ui.notify(f"Linked to TaxonWorks '{row['tw_name']}'. Unlink it first to "
                          f"link another term.", type="info")
                return
            _open_link((row["link_kind"],), int(row["id"]))

        table.on("link", lambda e: _link_clicked(e.args))
        table.on("unlink", lambda e: _unlink(e.args))
        table.on("edit", lambda e: _open_edit(e.args))
        table.on("merge", lambda e: _open_merge(e.args))
        table.on("delete", lambda e: _delete(e.args))
        if terms:
            table.on("allow", lambda e: _allow(e.args))

        # ── add ─────────────────────────────────────────────────────────────
        ui.separator().classes("my-3")
        ui.label("Add a term" if terms else "Add a relationship") \
            .classes("text-sm font-semibold mb-2")
        with ui.row().classes("w-full gap-3 items-end flex-wrap"):
            add_name = ui.input("Name *").classes("flex-1 min-w-[12rem]")
            add_kind = None
            allow_boxes: dict = {}
            if terms:
                add_kind = ui.select({"tag": "Tag", "data_attribute": "Data attribute"},
                                     value="tag", label="Type").classes("w-40")
                for col, label in _ALLOW_COLS:
                    allow_boxes[col] = ui.checkbox(label, value=(col == "allow_specimen"))

            def _flags() -> dict:
                return {c: bool(cb.value) for c, cb in allow_boxes.items()}

            def _add_local() -> None:
                if terms:
                    fn = lambda s: ann.create_term(s, kind=add_kind.value, name=add_name.value,
                                                   **_flags())
                else:
                    fn = lambda s: bio.create_relationship(s, add_name.value)
                if _write(fn, "Added."):
                    add_name.value = ""

            def _add_from_tw() -> None:
                # Terms: the dialog has a Tags and a Data attributes tab; the kind comes
                # from the tab a term is picked in, not from the Type select.
                _open_link((links.KEYWORD, links.PREDICATE) if terms
                           else (links.RELATIONSHIP,), None,
                           create=_flags() if terms else None)

            ui.button("Add local", icon="add", on_click=_add_local) \
                .props("flat no-caps color=secondary")
        with ui.row().classes("w-full items-center gap-2"):
            ui.button("Add from TaxonWorks", icon="cloud_download", on_click=_add_from_tw) \
                .props("flat no-caps color=secondary")
            if terms:
                ui.label("Pick tags or data attributes in the dialog.").classes("text-xs") \
                    .style("color:var(--tp-base-soft)")

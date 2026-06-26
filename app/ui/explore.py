"""Explore panel — the Records tab's browse/query front-end (#40).

One faceted search bar drives two views over the same filtered set:
  * Taxa   — a drawer-order checklist (family → genus headers, species rows with a
             material count + needs-attention flag), each species expands to its lots;
  * Events — collecting events, each expands to the specimens collected there.

Clicking a specimen / event drills into the existing Records edit detail (callbacks),
so nothing about editing is rebuilt. A CSV export dumps the current filtered set.
"""
from __future__ import annotations

import html as _html

from nicegui import ui

import app.services.explore as ex_svc

_CSS = """<style>
.ex-bar { position: relative; }
.ex-drop {
    position: absolute; left: 0; right: 0; top: calc(100% + 2px); z-index: 9999;
    background: var(--tp-base-foreground, #fff); border: 1px solid var(--tp-base-border, #cbd5e1);
    border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.12); max-height: 320px; overflow-y: auto;
}
.ex-item { padding: 7px 14px; cursor: pointer; font-size: .9rem; display: flex; align-items: center; gap: 8px;
           border-bottom: 1px solid var(--tp-base-border, #eee); }
.ex-item:last-child { border-bottom: none; }
.ex-item:hover { background: rgba(3,105,161,.08); }
.ex-tag { font-size: .66rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
          color: var(--tp-secondary, #0369a1); background: rgba(3,105,161,.10);
          border-radius: 4px; padding: 1px 6px; flex-shrink: 0; }
.ex-chip { display: inline-flex; align-items: center; gap: 6px; background: rgba(3,105,161,.10);
           border: 1px solid var(--tp-base-border, #cbd5e1); border-radius: 14px;
           padding: 2px 6px 2px 10px; font-size: .82rem; }
.ex-chip .ex-x { cursor: pointer; color: #9ca3af; font-weight: 700; }
.ex-chip .ex-x:hover { color: #dc2626; }
/* checklist — published-catalogue look: flush-left ranked headers distinguished by
   SIZE (higher taxa bigger), not deep indentation; subgenus is its own header. */
.ex-hdr   { display: flex; align-items: baseline; gap: 8px; line-height: 1.25; }
/* fixed-width right-aligned rank column → every name starts at the same x,
   regardless of the rank word's length (Superfamily vs Family) */
.ex-rank  { display: inline-block; width: 5.6rem; text-align: right; flex-shrink: 0;
            align-self: center; font-size: .58rem; font-weight: 600;
            text-transform: uppercase; letter-spacing: .07em; color: var(--tp-base-soft, #9ca3af); }
.ex-name  { line-height: 1.2; }
.ex-h-superfamily { margin-top: 20px; }
.ex-h-superfamily .ex-name { font-size: 1.45rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.ex-h-family { margin-top: 18px; }
.ex-h-family .ex-name { font-size: 1.35rem; font-weight: 800; text-transform: uppercase; letter-spacing: .02em; }
.ex-h-subfamily { margin-top: 9px; }
.ex-h-subfamily .ex-name { font-size: 1.12rem; font-weight: 700; }
.ex-h-tribe { margin-top: 5px; }
.ex-h-tribe .ex-name { font-size: 1.0rem; font-weight: 600; }
.ex-h-subtribe .ex-name { font-size: .92rem; font-weight: 600; }
.ex-h-genus { margin-top: 11px; }
.ex-h-genus .ex-name { font-size: 1.08rem; font-weight: 700; font-style: italic; }
.ex-h-subgenus { margin-top: 3px; }
.ex-h-subgenus .ex-name { font-size: .96rem; font-weight: 600; font-style: italic; }
.ex-auth { font-weight: 400; font-style: normal; color: var(--tp-base-soft, #888); font-size: .78em; }
.ex-sp-row { display: flex; align-items: center; gap: 8px; }
.ex-sp   { font-style: italic; font-size: .9rem; }
.ex-count { font-size: .72rem; color: var(--tp-base-soft, #888); }
/* specimen-count pill — distinct from the (italic) name */
.ex-pill { display: inline-block; font-size: .68rem; font-weight: 700; line-height: 1.45;
           padding: 0 7px; border-radius: 10px; margin-left: 6px; font-style: normal;
           background: rgba(3,105,161,.12); color: var(--tp-secondary, #0369a1); }
.ex-warn { color: #d97706; }
.ex-lot  { padding: 3px 0 3px 4px; font-size: .82rem; cursor: pointer; border-radius: 4px;
           overflow-wrap: anywhere; }
.ex-lot:hover { background: rgba(3,105,161,.07); }
.ex-cat  { font-family: monospace; color: var(--tp-base-soft, #888); font-size: .76rem; }
/* species line up under the (sub)genus NAME column (past the rank column), so the
   whole list shares one left edge for names instead of the epithets outdenting */
.ex-species-block { padding-left: calc(5.6rem + 8px); }
/* trim the expansion's own chrome so the epithet sits at the name column */
.ex-species-block .q-expansion-item .q-item { padding: 0; min-height: 0; }
.ex-species-block .q-expansion-item .q-item__section--avatar { min-width: 0; padding: 0; }
</style>"""


def _name_auth(name: str, auth: str) -> str:
    """Render a name with its authorship in a muted span (authorship kept separate by
    the service, so no fragile guessing about where the name ends)."""
    out = _html.escape(name)
    if auth:
        out += f' <span class="ex-auth">{_html.escape(auth)}</span>'
    return out


def build_explore_panel(session_factory, *, on_open_specimen, on_open_event) -> dict:
    ui.add_head_html(_CSS)

    def _with(fn):
        with session_factory() as s:
            return fn(s)

    state = {"filters": [], "view": "taxa"}   # filters: list of {kind,label,key,tag}

    # ── search bar + chips ────────────────────────────────────────────────
    with ui.card().classes("w-full shadow-sm"):
        with ui.element("div").classes("ex-bar w-full"):
            search_in = (ui.input(placeholder="Search taxa, localities, collectors…")
                         .props("outlined dense clearable").classes("w-full"))
            dropdown = ui.element("div").classes("ex-drop").style("display:none")
        chips_row = ui.row().classes("items-center gap-2 mt-2")
        with ui.row().classes("items-center gap-3 mt-2 w-full"):
            count_lbl = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")
            ui.space()
            taxa_btn = ui.button("Taxa", icon="account_tree").props("dense no-caps")
            events_btn = ui.button("Events", icon="place").props("dense no-caps flat")
            csv_btn = ui.button("CSV", icon="download").props("flat dense no-caps") \
                .tooltip("Export the filtered set as CSV")

    results = ui.column().classes("w-full gap-0 mt-2")

    # ── facet dropdown ────────────────────────────────────────────────────
    def _refresh_dropdown(term: str):
        dropdown.clear()
        term = (term or "").strip()
        if not term:
            dropdown.style("display:none")
            return
        facets = _with(lambda s: ex_svc.search_facets(s, term, limit=8))
        # hide facets already active
        active = {(f["kind"], str(f["key"])) for f in state["filters"]}
        facets = [f for f in facets if (f.kind, str(f.key)) not in active]
        if not facets:
            dropdown.style("display:none")
            return
        with dropdown:
            for f in facets:
                item = ui.element("div").classes("ex-item")
                with item:
                    ui.html(f'<span class="ex-tag">{_html.escape(f.tag)}</span>'
                            f'<span>{_html.escape(f.label)}</span>')
                item.on("click", lambda _, fc=f: _add_chip(fc))
        dropdown.style("display:block")

    def _add_chip(f):
        state["filters"].append({"kind": f.kind, "label": f.label, "key": f.key, "tag": f.tag})
        search_in.value = ""
        dropdown.style("display:none")
        _render_chips()
        _refresh()

    def _remove_chip(i):
        del state["filters"][i]
        _render_chips()
        _refresh()

    def _render_chips():
        chips_row.clear()
        with chips_row:
            for i, f in enumerate(state["filters"]):
                chip = ui.element("div").classes("ex-chip")
                with chip:
                    ui.html(f'<span class="ex-tag">{_html.escape(f["tag"])}</span>'
                            f'<span>{_html.escape(f["label"])}</span>')
                    ui.html('<span class="ex-x" title="Remove">✕</span>').on(
                        "click", lambda _, idx=i: _remove_chip(idx))
            if state["filters"]:
                ui.button("Clear all", on_click=_clear_all).props("flat dense no-caps size=sm")

    def _clear_all():
        state["filters"] = []
        _render_chips()
        _refresh()

    search_in.on_value_change(lambda e: _refresh_dropdown(e.value or ""))

    # ── results ───────────────────────────────────────────────────────────
    def _lot_line(lot) -> str:
        bits = []
        if lot.sex:
            bits.append(_html.escape(lot.sex))
        if lot.count and lot.count != 1:
            bits.append(f"×{lot.count}")
        meta = ("  ·  ".join(bits) + "  ·  ") if bits else ""
        loc = _html.escape(lot.locality or "—")
        return (f'<span class="ex-cat">{_html.escape(lot.catalog)}</span>  {meta}{loc}')

    def _render_taxa(groups):
        if not groups:
            ui.label("No specimens match.").classes("text-sm italic mt-3") \
                .style("color:var(--tp-base-soft)")
            return
        prev: list[str] = []   # header names already printed (catalogue: print on change)
        for g in groups:
            names = [nm for _r, nm, _a in g.headers]
            i = 0
            while i < len(prev) and i < len(names) and prev[i] == names[i]:
                i += 1
            for rank, name, auth in g.headers[i:]:
                ui.html(f'<div class="ex-hdr ex-h-{rank}">'
                        f'<span class="ex-rank">{_html.escape(rank)}</span>'
                        f'<span class="ex-name">{_name_auth(name, auth)}</span></div>')
            prev = names
            with ui.element("div").classes("ex-species-block w-full"):
                for sp in g.species:
                    exp = ui.expansion().classes("w-full").props("dense")
                    with exp.add_slot("header"):
                        with ui.element("div").classes("ex-sp-row"):
                            if sp.needs_attention:
                                ui.html('<span class="ex-warn" title="needs attention">⚠</span>')
                            ui.html(f'<span class="ex-sp">{_name_auth(sp.short_label, sp.short_auth)}</span>'
                                    f'<span class="ex-pill" title="specimens / lots">{sp.count}</span>')
                    with exp:
                        for lg in sp.lot_groups:
                            if lg.count == 1:
                                lot = lg.specimens[0]
                                row = ui.html('<div class="ex-lot">' + _lot_line(lot) + "</div>")
                                row.on("click", lambda _, c=lot.co_id: on_open_specimen(c))
                            else:
                                # identical event + associations → collapsed, expand for each
                                lexp = ui.expansion().classes("w-full").props("dense")
                                with lexp.add_slot("header"):
                                    ui.html('<div class="ex-lot">'
                                            f'{_html.escape(lg.locality or "—")}'
                                            f'<span class="ex-pill" title="identical specimens">{lg.count}</span>'
                                            "</div>")
                                with lexp:
                                    for lot in lg.specimens:
                                        meta = []
                                        if lot.sex:
                                            meta.append(_html.escape(lot.sex))
                                        m = ("  ·  " + "  ·  ".join(meta)) if meta else ""
                                        row = ui.html('<div class="ex-lot" style="padding-left:18px">'
                                                      f'<span class="ex-cat">{_html.escape(lot.catalog)}</span>{m}</div>')
                                        row.on("click", lambda _, c=lot.co_id: on_open_specimen(c))

    def _render_events(evs):
        if not evs:
            ui.label("No events match.").classes("text-sm italic mt-3") \
                .style("color:var(--tp-base-soft)")
            return
        for g in evs:
            exp = ui.expansion().classes("w-full").props("dense")
            with exp.add_slot("header"):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.button(icon="edit", on_click=lambda _, e=g.event_id: on_open_event(e)) \
                        .props("flat dense round size=xs").tooltip("Open event")
                    ui.html(f'<span>{_html.escape(g.summary or "event")}</span>'
                            f'<span class="ex-count">· {g.n_specimens} spec.</span>')
            with exp:
                for lot in g.lots:
                    row = ui.html('<div class="ex-lot">'
                                  f'<span>{_html.escape(lot.taxon_label)}</span>  '
                                  f'<span class="ex-cat">{_html.escape(lot.catalog)}</span></div>')
                    row.on("click", lambda _, c=lot.co_id: on_open_specimen(c))

    def _refresh():
        flt = state["filters"]
        c = _with(lambda s: ex_svc.counts(s, flt))
        count_lbl.set_text(
            f"{c['specimens']} specimens · {c['taxa']} taxa · {c['events']} events"
            f" · {c['georeferenced']} mapped")
        results.clear()
        with results:
            if state["view"] == "taxa":
                _render_taxa(_with(lambda s: ex_svc.checklist(s, flt)))
            else:
                _render_events(_with(lambda s: ex_svc.events(s, flt)))

    def _set_view(v):
        state["view"] = v
        taxa_btn.props(f'{"" if v=="taxa" else "flat"}')
        events_btn.props(f'{"" if v=="events" else "flat"}')
        _refresh()

    taxa_btn.on_click(lambda: _set_view("taxa"))
    events_btn.on_click(lambda: _set_view("events"))

    def _export():
        rows = _with(lambda s: ex_svc.query_specimens(s, state["filters"]))
        ui.download(ex_svc.to_csv(rows), filename="collection_export.csv", media_type="text/csv")
    csv_btn.on_click(_export)

    _refresh()
    return {"refresh": _refresh}

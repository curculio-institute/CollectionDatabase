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

import app.ui.record_summary as rs

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
/* stacked search groups (#135): each group is a bordered block; groups joined by AND */
.ex-group { border: 1px solid var(--tp-base-border, #e2e8f0); border-radius: 10px;
            padding: 10px 12px; background: var(--tp-base-foreground, #fff); }
.ex-and { align-self: center; font-size: .66rem; font-weight: 700; letter-spacing: .08em;
          color: var(--tp-base-soft, #9ca3af); padding: 4px 0; }
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
.ex-h-subgenus .ex-name { font-size: .9rem; font-weight: 600; font-style: italic; }
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


# Colour-blind-safe two-series palette (blue / orange), reused across the dashboard.
_SERIES_COLORS = ("#0369a1", "#ea7317")


def _line_chart(categories: list[str], series: list[tuple], *, show_legend: bool = True) -> dict:
    """ECharts option for a category-axis chart. `series` = [(name, values, type)],
    where type is 'line' or 'bar'. Integer y-axis (specimen/species counts)."""
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"show": show_legend, "top": 0,
                   "data": [name for name, _v, _t in series]},
        "grid": {"left": 8, "right": 16, "top": 34 if show_legend else 12,
                 "bottom": 8, "containLabel": True},
        "xAxis": {"type": "category", "data": categories,
                  "axisLabel": {"hideOverlap": True}},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": [
            {"name": name, "type": typ, "data": values, "smooth": typ == "line",
             "showSymbol": typ == "line",
             "itemStyle": {"color": _SERIES_COLORS[i % len(_SERIES_COLORS)]}}
            for i, (name, values, typ) in enumerate(series)
        ],
    }


def _carry(accum: list[tuple[int, int]], years: list[int]) -> list[int]:
    """Expand a cumulative (year → running total) curve onto `years`, carrying the
    last value forward across gap years (a saturation curve never dips)."""
    d = dict(accum)
    out, last = [], 0
    for y in years:
        if y in d:
            last = d[y]
        out.append(last)
    return out


def build_explore_panel(session_factory, *, on_open_specimen, on_open_event) -> dict:
    ui.add_head_html(_CSS)
    ui.add_head_html(rs.CSS)

    def _with(fn):
        with session_factory() as s:
            return fn(s)

    # Stacked search groups (#135): each group = {op, facets:[{kind,label,key,tag}]}.
    # Facets within a group combine by its op (AND/OR); the groups combine by AND.
    state = {"groups": [{"op": "and", "facets": []}], "view": "taxa"}
    _PLACEHOLDER = "Search taxa, localities, collectors, collections…"

    def _all_facets():
        return [f for g in state["groups"] for f in g["facets"]]

    # ── search groups + toolbar ───────────────────────────────────────────
    with ui.card().classes("w-full shadow-sm"):
        groups_box = ui.column().classes("w-full gap-0")
        with ui.row().classes("items-center gap-3 mt-2 w-full"):
            count_lbl = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")
            ui.space()
            taxa_btn = ui.button("Taxa", icon="account_tree").props("dense no-caps")
            events_btn = ui.button("Events", icon="place").props("dense no-caps flat")
            dashboard_btn = ui.button("Dashboard", icon="insights").props("dense no-caps flat") \
                .tooltip("Charts for the filtered set")
            csv_btn = ui.button("CSV", icon="download").props("flat dense no-caps") \
                .tooltip("Export the filtered set as CSV")

    results = ui.column().classes("w-full gap-0 mt-2")

    # ── facet dropdown (per group input) ──────────────────────────────────
    def _refresh_dropdown(g, drop, term: str):
        drop.clear()
        term = (term or "").strip()
        if not term:
            drop.style("display:none")
            return
        facets = _with(lambda s: ex_svc.search_facets(s, term, limit=8))
        active = {(f["kind"], str(f["key"])) for f in g["facets"]}   # already in THIS group
        facets = [f for f in facets if (f.kind, str(f.key)) not in active]
        if not facets:
            drop.style("display:none")
            return
        with drop:
            for f in facets:
                item = ui.element("div").classes("ex-item")
                with item:
                    ui.html(f'<span class="ex-tag">{_html.escape(f.tag)}</span>'
                            f'<span>{_html.escape(f.label)}</span>')
                item.on("click", lambda _, fc=f, gg=g: _add_chip(gg, fc))
        drop.style("display:block")

    def _add_chip(g, f):
        g["facets"].append({"kind": f.kind, "label": f.label, "key": f.key, "tag": f.tag})
        _render_groups()
        _refresh()

    def _remove_chip(g, i):
        del g["facets"][i]
        if not g["facets"] and len(state["groups"]) > 1:
            state["groups"].remove(g)          # an emptied group disappears (never the last)
        _render_groups()
        _refresh()

    def _set_op(g, v):
        g["op"] = v or "and"
        _refresh()

    def _add_group():
        state["groups"].append({"op": "and", "facets": []})
        _render_groups()                        # empty group changes nothing → no _refresh

    def _remove_group(g):
        state["groups"].remove(g)
        if not state["groups"]:
            state["groups"].append({"op": "and", "facets": []})
        _render_groups()
        _refresh()

    def _clear_all():
        state["groups"] = [{"op": "and", "facets": []}]
        _render_groups()
        _refresh()

    def _build_group(g):
        with ui.element("div").classes("ex-group w-full"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                with ui.element("div").classes("ex-bar w-full"):
                    inp = ui.input(placeholder=_PLACEHOLDER) \
                        .props("outlined dense clearable").classes("w-full")
                    drop = ui.element("div").classes("ex-drop").style("display:none")
                inp.on_value_change(lambda e, gg=g, dd=drop: _refresh_dropdown(gg, dd, e.value or ""))
                if len(state["groups"]) > 1:
                    ui.button(icon="close", on_click=lambda _, gg=g: _remove_group(gg)) \
                        .props("flat dense round size=sm").tooltip("Remove this search")
            if g["facets"]:
                with ui.row().classes("items-center gap-2 mt-2"):
                    for i, f in enumerate(g["facets"]):
                        chip = ui.element("div").classes("ex-chip")
                        with chip:
                            ui.html(f'<span class="ex-tag">{_html.escape(f["tag"])}</span>'
                                    f'<span>{_html.escape(f["label"])}</span>')
                            ui.html('<span class="ex-x" title="Remove">✕</span>').on(
                                "click", lambda _, gg=g, idx=i: _remove_chip(gg, idx))
                    if len(g["facets"]) >= 2:
                        tog = ui.toggle({"and": "AND", "or": "OR"}, value=g["op"]) \
                            .props("dense no-caps unelevated size=sm") \
                            .tooltip("How the filters in this search combine — "
                                     "AND: match every filter · OR: match any filter")
                        tog.on_value_change(lambda e, gg=g: _set_op(gg, e.value))

    def _render_groups():
        groups_box.clear()
        with groups_box:
            for gi, g in enumerate(state["groups"]):
                if gi > 0:
                    ui.html('<div class="ex-and">AND</div>')
                _build_group(g)
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.button("Add another search", icon="add", on_click=_add_group) \
                    .props("flat dense no-caps size=sm") \
                    .tooltip("Stack another search — combined with AND")
                if _all_facets():
                    ui.button("Clear all", on_click=_clear_all).props("flat dense no-caps size=sm")

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
                    ui.html(rs.event_html(summary=g.summary, n_specimens=g.n_specimens,
                                          confidential=g.confidential))
            with exp:
                for lot in g.lots:
                    # Routed through the shared renderer: the names under an event used to be
                    # escaped plain text, so no species was italicised here at all.
                    row = ui.html(rs.specimen_html(
                        catalog=lot.catalog,
                        name=lot.taxon_name or lot.taxon_label,
                        rank=lot.taxon_rank,
                        authorship=lot.authorship,
                        hosts=lot.hosts,
                        sex=lot.sex,
                        count=lot.count,
                        locality="",                    # the event IS the locality here
                        confidential=lot.confidential,
                        event_confidential=lot.event_confidential,
                    ))
                    row.on("click", lambda _, c=lot.co_id: on_open_specimen(c))

    def _undated_note(d, *, collecting, identification):
        bits = []
        if collecting and d.undated_collected:
            bits.append(f"{d.undated_collected} without a collecting date")
        if identification and d.undated_identified:
            bits.append(f"{d.undated_identified} without an identification date")
        if bits:
            ui.html('<span class="text-xs" style="color:var(--tp-base-soft)">'
                    f'Not shown: {"; ".join(_html.escape(b) for b in bits)}.</span>')

    def _render_dashboard(d):
        if d.total == 0:
            ui.label("No specimens match.").classes("text-sm italic mt-3") \
                .style("color:var(--tp-base-soft)")
            return

        # A person filter reveals *its own* date axis (#135): filtering by Collector
        # shows the collecting-date views (and hides identification, which isn't what
        # you filtered for); filtering by "identified by" shows the identification-date
        # views. With both, or neither (the overview), everything is shown.
        kinds = {f["kind"] for f in _all_facets()}
        has_coll, has_ident = "collector" in kinds, "identified_by" in kinds
        show_collecting = has_coll or not has_ident
        show_identification = has_ident or not has_coll

        # ── timelines: specimens collected / identified, per year ──
        t_series = []
        if show_collecting:
            t_series.append(("Collected", d.collected_by_year, "bar"))
        if show_identification:
            t_series.append(("Identified", d.identified_by_year, "bar"))
        t_years = sorted({y for _n, data, _t in t_series for y, _c in data})
        with ui.card().classes("w-full shadow-sm mt-2"):
            ui.label("Specimens over time").classes("text-sm font-medium")
            ui.echart(_line_chart(
                [str(y) for y in t_years],
                [(name, [dict(data).get(y, 0) for y in t_years], typ)
                 for name, data, typ in t_series],
                show_legend=len(t_series) > 1,
            )).classes("w-full").style("height:300px")
            _undated_note(d, collecting=show_collecting, identification=show_identification)

        # ── species-accumulation (saturation) curves ──
        a_series = []
        if show_collecting:
            a_series.append(("by collecting date", d.accum_collected))
        if show_identification:
            a_series.append(("by identification date", d.accum_identified))
        a_years = sorted({y for _n, data in a_series for y, _c in data})
        if a_years:
            with ui.card().classes("w-full shadow-sm mt-2"):
                ui.label("Species accumulation").classes("text-sm font-medium")
                ui.html('<span class="text-xs" style="color:var(--tp-base-soft)">'
                        'cumulative distinct species-group names</span>')
                ui.echart(_line_chart(
                    [str(y) for y in a_years],
                    [(name, _carry(data, a_years), "line") for name, data in a_series],
                    show_legend=len(a_series) > 1,
                )).classes("w-full").style("height:300px")

        # ── phenology (collecting month) — a collecting-date view ──
        if show_collecting:
            with ui.card().classes("w-full shadow-sm mt-2"):
                ui.label("Phenology").classes("text-sm font-medium")
                ui.html('<span class="text-xs" style="color:var(--tp-base-soft)">'
                        'specimens by month of collection</span>')
                ui.echart(_line_chart(
                    list(ex_svc._MONTHS),
                    [("Specimens", list(d.phenology), "bar")],
                    show_legend=False,
                )).classes("w-full").style("height:280px")

        # ── host associations ──
        if d.hosts:
            with ui.card().classes("w-full shadow-sm mt-2"):
                ui.label("Host associations").classes("text-sm font-medium")
                names = [n for n, _ in d.hosts][::-1]      # bottom-up for horizontal bars
                vals = [c for _, c in d.hosts][::-1]
                ui.echart({
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "grid": {"left": 8, "right": 24, "top": 12, "bottom": 8,
                             "containLabel": True},
                    "xAxis": {"type": "value", "minInterval": 1},
                    "yAxis": {"type": "category", "data": names},
                    "series": [{"type": "bar", "data": vals,
                                "itemStyle": {"color": "#0369a1"}}],
                }).classes("w-full").style(f"height:{max(200, 26 * len(names) + 60)}px")

    def _refresh():
        flt = state["groups"]
        c = _with(lambda s: ex_svc.counts(s, flt))
        count_lbl.set_text(
            f"{c['specimens']} specimens · {c['species_group']} species-group names"
            f" · {c['events']} events · {c['georeferenced']} specimens georeferenced")
        results.clear()
        with results:
            if state["view"] == "taxa":
                _render_taxa(_with(lambda s: ex_svc.checklist(s, flt)))
            elif state["view"] == "events":
                _render_events(_with(lambda s: ex_svc.events(s, flt)))
            else:
                _render_dashboard(_with(lambda s: ex_svc.dashboard(s, flt)))

    _view_btns = {"taxa": taxa_btn, "events": events_btn, "dashboard": dashboard_btn}

    def _set_view(v):
        state["view"] = v
        for name, btn in _view_btns.items():
            btn.props(f'{"" if name == v else "flat"}')
        _refresh()

    taxa_btn.on_click(lambda: _set_view("taxa"))
    events_btn.on_click(lambda: _set_view("events"))
    dashboard_btn.on_click(lambda: _set_view("dashboard"))

    def _export():
        rows = _with(lambda s: ex_svc.query_specimens(s, state["groups"]))
        ui.download(ex_svc.to_csv(rows), filename="collection_export.csv", media_type="text/csv")
    csv_btn.on_click(_export)

    _render_groups()   # renders the first (empty) search group + Add-another button
    _refresh()
    return {"refresh": _refresh}

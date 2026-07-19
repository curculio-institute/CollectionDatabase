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
import app.services.saved_searches as fav_svc

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
/* favorites rail (#137): quick-buttons for saved searches, in the left margin */
.ex-fav-rail { position: sticky; top: 8px; }
.ex-fav-hd { font-size: .66rem; font-weight: 700; text-transform: uppercase;
             letter-spacing: .06em; color: var(--tp-base-soft, #9ca3af); padding: 2px 4px; }
.ex-fav { display: flex; align-items: center; gap: 4px; width: 100%; border-radius: 8px;
          padding: 5px 6px 5px 9px; cursor: pointer; font-size: .86rem; line-height: 1.2;
          border: 1px solid transparent; }
.ex-fav:hover { background: rgba(3,105,161,.08); border-color: var(--tp-base-border,#e2e8f0); }
.dark .ex-fav:hover { background: rgba(56,189,248,.12); }
.ex-fav-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ex-fav-star { color: #f59e0b; flex-shrink: 0; font-size: 1rem; }
.ex-fav-empty { font-size: .78rem; color: var(--tp-base-soft, #9ca3af); padding: 2px 6px; line-height: 1.4; }
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


# Colour-blind-safe categorical palette, by cohort/series index. These are the
# LIGHT-background (darker) shades used when the option is built; the client-side
# themer swaps each to the index-aligned brighter shade in dark mode (_ECHART_DARK
# below must stay in the same order). A dark-blue line is fine on white but too dark
# on near-black, so the palette itself has to follow the theme, not just the text.
_SERIES_COLORS = ("#0369a1", "#ea7317", "#059669", "#be185d", "#6d28d9",
                  "#b45309", "#0e7490", "#4d7c0f")

# Re-theme ECharts (canvas → can't read the app's `.dark` CSS) from the current theme,
# and again whenever the theme toggles. `_tpThemeECharts` is also called by the server
# after (re)building the dashboard charts. Series colours are left untouched — only the
# chrome text/lines follow the theme. Retries because a chart's instance may not exist
# yet the instant its div is added.
_ECHART_THEME_JS = """
<script>
(function () {
  // Index-aligned with Python _SERIES_COLORS (light) — brighter shades for dark bg.
  var LP = ["#0369a1","#ea7317","#059669","#be185d","#6d28d9","#b45309","#0e7490","#4d7c0f"];
  var DP = ["#38bdf8","#fb923c","#34d399","#f472b6","#a78bfa","#fbbf24","#22d3ee","#a3e635"];
  function swap(c, dark) {              // map a palette colour to the current theme
    if (!c) return null;
    var i = LP.indexOf(c);
    if (i < 0) i = DP.indexOf(c);       // already swapped on an earlier pass → re-map
    if (i < 0) return null;             // not one of ours (e.g. gridline) → leave it
    return dark ? DP[i] : LP[i];
  }
  function themeOne(el) {
    var inst = window.echarts && window.echarts.getInstanceByDom(el);
    if (!inst) return false;
    var dark = document.documentElement.classList.contains('dark');
    var text = dark ? '#cbd5e1' : '#334155';
    var axis = dark ? '#94a3b8' : '#475569';
    var line = dark ? 'rgba(255,255,255,0.16)' : 'rgba(0,0,0,0.15)';
    var split = dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
    // Series colours: swap each between the light/dark palette by index; only
    // itemStyle.color is touched, so the solid/dashed lineStyle survives the merge.
    var series = [];
    (inst.getOption().series || []).forEach(function (s) {
      var cur = (s.itemStyle && s.itemStyle.color) || s.color;
      var mapped = swap(cur, dark);
      series.push(mapped ? { itemStyle: { color: mapped } } : {});
    });
    inst.setOption({
      textStyle: { color: text },
      legend: { textStyle: { color: text } },
      xAxis: { axisLabel: { color: axis }, axisLine: { lineStyle: { color: line } } },
      yAxis: { axisLabel: { color: axis }, axisLine: { lineStyle: { color: line } },
               splitLine: { lineStyle: { color: split } } },
      series: series
    });
    return true;
  }
  // Theme ONE chart, retrying until its instance exists — charts in a multi-chart
  // dashboard mount at slightly different times, so each retries independently (a
  // single shared retry that stopped at the first success left the rest dark).
  function themeEl(el, tries) {
    tries = tries || 0;
    if (themeOne(el)) return;
    if (tries < 25) setTimeout(function () { themeEl(el, tries + 1); }, 80);
  }
  function themeAll() {
    document.querySelectorAll('.nicegui-echart').forEach(function (el) { themeEl(el, 0); });
  }
  window._tpThemeECharts = themeAll;
  // Re-theme on dark-mode toggle (class flips on <html>).
  new MutationObserver(themeAll)
    .observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  // Theme every chart the moment it is added to the DOM, independent of any server
  // call — so a freshly (re)built dashboard is never left with default dark text.
  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.classList && n.classList.contains('nicegui-echart')) themeEl(n, 0);
        else if (n.querySelectorAll)
          n.querySelectorAll('.nicegui-echart').forEach(function (el) { themeEl(el, 0); });
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
})();
</script>
"""


def _line_chart(categories: list[str], series: list[dict], *, show_legend: bool = True) -> dict:
    """ECharts option for a category-axis chart. Each series is a dict:
    ``{"name", "values", "type" ('line'|'bar'), "color"?, "dashed"?}``. A missing
    colour falls back to the palette by position. Integer y-axis (counts)."""
    out_series = []
    for i, s in enumerate(series):
        typ = s["type"]
        color = s.get("color") or _SERIES_COLORS[i % len(_SERIES_COLORS)]
        spec = {"name": s["name"], "type": typ, "data": s["values"],
                "smooth": typ == "line", "showSymbol": typ == "line",
                "itemStyle": {"color": color}}
        if typ == "line" and s.get("dashed"):
            spec["lineStyle"] = {"type": "dashed"}
        out_series.append(spec)
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"show": show_legend, "top": 0,
                   "data": [s["name"] for s in series]},
        "grid": {"left": 8, "right": 16, "top": 34 if show_legend else 12,
                 "bottom": 8, "containLabel": True},
        "xAxis": {"type": "category", "data": categories,
                  "axisLabel": {"hideOverlap": True}},
        "yAxis": {"type": "value", "minInterval": 1},
        "series": out_series,
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
    # ECharts renders to canvas, so it can't inherit the app's `.dark` CSS theme — its
    # legend/axis text stays dark and is unreadable in dark mode. Re-theme every chart
    # instance from the current theme, and again whenever the theme toggles.
    ui.add_head_html(_ECHART_THEME_JS)

    def _with(fn):
        with session_factory() as s:
            return fn(s)

    def _write(fn):
        """Like _with but commits — for favorite create/rename/delete/default writes."""
        with session_factory() as s:
            r = fn(s)
            s.commit()
            return r

    # Stacked search groups (#135): each group = {op, facets:[{kind,label,key,tag}]}.
    # Facets within a group combine by its op (AND/OR); the groups combine by AND.
    # dash_compare: plot each group as its own cohort/series (needs ≥2 groups).
    # dash_dates: which date axes to plot; None = the role-based auto default.
    state = {"groups": [{"op": "and", "facets": []}], "view": "taxa",
             "dash_compare": False, "dash_dates": None}
    _PLACEHOLDER = "Search taxa, localities, collectors, collections…"

    def _all_facets():
        return [f for g in state["groups"] for f in g["facets"]]

    # ── layout: the search bar shares a row with the favorites rail (right on wide,
    # below on narrow); the RESULTS span the full container width underneath, so the
    # dashboard charts / checklist use the whole reclaimed wide-screen space (#137). ──
    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("flex-1 min-w-0 gap-2"):
            # ── search groups + toolbar ───────────────────────────────────
            with ui.card().classes("w-full shadow-sm"):
                groups_box = ui.column().classes("w-full gap-0")
                with ui.row().classes("items-center gap-3 mt-2 w-full"):
                    count_lbl = ui.label("").classes("text-sm") \
                        .style("color:var(--tp-base-soft)")
                    ui.space()
                    taxa_btn = ui.button("Taxa", icon="account_tree").props("dense no-caps")
                    events_btn = ui.button("Events", icon="place").props("dense no-caps flat")
                    dashboard_btn = ui.button("Dashboard", icon="insights") \
                        .props("dense no-caps flat").tooltip("Charts for the filtered set")
                    csv_btn = ui.button("CSV", icon="download").props("flat dense no-caps") \
                        .tooltip("Export the filtered set as CSV")

        fav_rail = ui.column().classes("ex-fav-rail w-full lg:w-56 shrink-0 gap-1 order-last")

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
        state["dash_dates"] = None       # a changed filter re-applies the smart date default
        _render_groups()
        _refresh()

    def _remove_chip(g, i):
        del g["facets"][i]
        if not g["facets"] and len(state["groups"]) > 1:
            state["groups"].remove(g)          # an emptied group disappears (never the last)
        state["dash_dates"] = None
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
        state["dash_dates"] = None
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

    def _effective_dates():
        """(show_collecting, show_identification) — the explicit Dates selection if the
        user set one, else the role-based smart default (#135): filtering by Collector
        reveals the collecting-date axis and hides identification (not what you filtered
        for); filtering by 'identified by' does the reverse; both / neither → both."""
        if state["dash_dates"] is not None:
            return ("collected" in state["dash_dates"], "identified" in state["dash_dates"])
        kinds = {f["kind"] for f in _all_facets()}
        has_coll, has_ident = "collector" in kinds, "identified_by" in kinds
        return (has_coll or not has_ident, has_ident or not has_coll)

    def _toggle_date(key):
        show_c, show_i = _effective_dates()
        cur = {k for k, on in (("collected", show_c), ("identified", show_i)) if on}
        cur.symmetric_difference_update({key})
        if cur:                              # never let both switch off (nothing to plot)
            state["dash_dates"] = cur
            _refresh()

    def _set_dash_compare(v):
        state["dash_compare"] = bool(v)
        _refresh()

    def _group_label(g):
        labels = [f["label"] for f in g["facets"]]
        return (" OR " if g.get("op") == "or" else " + ").join(labels) or "All"

    def _undated_note(d, *, collecting, identification):
        bits = []
        if collecting and d.undated_collected:
            bits.append(f"{d.undated_collected} without a collecting date")
        if identification and d.undated_identified:
            bits.append(f"{d.undated_identified} without an identification date")
        if bits:
            ui.html('<span class="text-xs" style="color:var(--tp-base-soft)">'
                    f'Not shown: {"; ".join(_html.escape(b) for b in bits)}.</span>')

    def _render_dashboard():
        groups_with_facets = [g for g in state["groups"] if g["facets"]]
        can_compare = len(groups_with_facets) >= 2
        compare = state["dash_compare"] and can_compare
        show_c, show_i = _effective_dates()

        # cohorts: in compare mode, one per search group (each evaluated on its own);
        # otherwise the single combined set.
        if compare:
            cohorts = [(_group_label(g),
                        _with(lambda s, gg=g: ex_svc.dashboard(s, [gg])))
                       for g in groups_with_facets]
        else:
            cohorts = [(None, _with(lambda s: ex_svc.dashboard(s, state["groups"])))]

        # ── controls ──
        with ui.row().classes("items-center gap-4 mt-2 w-full"):
            if can_compare:
                ui.toggle({False: "Combined", True: "Compare searches"}, value=compare) \
                    .props("dense no-caps unelevated size=sm") \
                    .on_value_change(lambda e: _set_dash_compare(e.value)) \
                    .tooltip("Combined: the searches AND into one set · "
                             "Compare: one series per search")
            with ui.row().classes("items-center gap-1"):
                ui.label("Dates:").classes("text-xs").style("color:var(--tp-base-soft)")
                for lbl, key, on in (("Collected", "collected", show_c),
                                     ("Identified", "identified", show_i)):
                    ui.button(lbl, on_click=lambda k=key: _toggle_date(k)) \
                        .props(f'dense no-caps size=sm {"unelevated" if on else "outline"}')

        if all(d.total == 0 for _l, d in cohorts):
            with ui.column().classes("gap-1 mt-3"):
                ui.label("No specimens match.").classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
                if can_compare and not compare:
                    ui.label("The searches are combined with AND. To chart each search "
                             "separately, switch to “Compare searches”.") \
                        .classes("text-xs").style("color:var(--tp-base-soft)")
            return

        # colour: by cohort in compare mode; by date-role in combined mode.
        def _color(ci, role):
            return _SERIES_COLORS[ci % len(_SERIES_COLORS)] if compare \
                else _SERIES_COLORS[0 if role == "collected" else 1]

        def _series_name(label, role):
            role_txt = "collected" if role == "collected" else "identified"
            if compare:
                return f"{label} — {role_txt}" if (show_c and show_i) else label
            return "Collected" if role == "collected" else "Identified"

        # ── timelines: specimens collected / identified, per year ──
        raw = []   # (name, [(year,count)], type, color, dashed)
        typ = "line" if compare else "bar"
        for ci, (label, d) in enumerate(cohorts):
            if show_c:
                raw.append((_series_name(label, "collected"), d.collected_by_year, typ,
                            _color(ci, "collected"), False))
            if show_i:
                raw.append((_series_name(label, "identified"), d.identified_by_year, typ,
                            _color(ci, "identified"), compare and show_c))
        t_years = sorted({y for _n, data, *_ in raw for y, _c in data})
        with ui.card().classes("w-full shadow-sm mt-2"):
            ui.label("Specimens over time").classes("text-sm font-medium")
            ui.echart(_line_chart(
                [str(y) for y in t_years],
                [{"name": n, "values": [dict(data).get(y, 0) for y in t_years],
                  "type": tp, "color": col, "dashed": dsh}
                 for n, data, tp, col, dsh in raw],
                show_legend=len(raw) > 1,
            )).classes("w-full").style("height:300px")
            if not compare:
                _undated_note(cohorts[0][1], collecting=show_c, identification=show_i)

        # ── species-accumulation (saturation) curves ──
        araw = []
        for ci, (label, d) in enumerate(cohorts):
            if show_c:
                araw.append((_series_name(label, "collected"), d.accum_collected,
                             _color(ci, "collected"), False))
            if show_i:
                araw.append((_series_name(label, "identified"), d.accum_identified,
                             _color(ci, "identified"), compare and show_c))
        a_years = sorted({y for _n, data, *_ in araw for y, _c in data})
        if a_years:
            with ui.card().classes("w-full shadow-sm mt-2"):
                ui.label("Species accumulation").classes("text-sm font-medium")
                ui.html('<span class="text-xs" style="color:var(--tp-base-soft)">'
                        'cumulative distinct species-group names</span>')
                ui.echart(_line_chart(
                    [str(y) for y in a_years],
                    [{"name": n, "values": _carry(data, a_years), "type": "line",
                      "color": col, "dashed": dsh} for n, data, col, dsh in araw],
                    show_legend=len(araw) > 1,
                )).classes("w-full").style("height:300px")

        # ── phenology (collecting month) — a collecting-date view, one series/cohort ──
        if show_c:
            with ui.card().classes("w-full shadow-sm mt-2"):
                ui.label("Phenology").classes("text-sm font-medium")
                ui.html('<span class="text-xs" style="color:var(--tp-base-soft)">'
                        'specimens by month of collection</span>')
                ui.echart(_line_chart(
                    list(ex_svc._MONTHS),
                    [{"name": (label or "Specimens"), "values": list(d.phenology),
                      "type": "bar", "color": _color(ci, "collected")}
                     for ci, (label, d) in enumerate(cohorts)],
                    show_legend=compare,
                )).classes("w-full").style("height:280px")

        # ── host associations — one chart per cohort in compare mode ──
        for ci, (label, d) in enumerate(cohorts):
            if not d.hosts:
                continue
            with ui.card().classes("w-full shadow-sm mt-2"):
                title = f"Host associations — {label}" if compare else "Host associations"
                ui.label(title).classes("text-sm font-medium")
                names = [n for n, _ in d.hosts][::-1]      # bottom-up for horizontal bars
                vals = [c for _, c in d.hosts][::-1]
                ui.echart({
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "grid": {"left": 8, "right": 24, "top": 12, "bottom": 8,
                             "containLabel": True},
                    "xAxis": {"type": "value", "minInterval": 1},
                    "yAxis": {"type": "category", "data": names},
                    "series": [{"type": "bar", "data": vals,
                                "itemStyle": {"color": _color(ci, "collected")}}],
                }).classes("w-full").style(f"height:{max(200, 26 * len(names) + 60)}px")

        # newly-built charts render dark text by default — theme them to the app's mode.
        ui.run_javascript("window._tpThemeECharts && window._tpThemeECharts()")

    def _refresh():
        flt = state["groups"]
        groups_with_facets = [g for g in state["groups"] if g["facets"]]
        comparing = (state["view"] == "dashboard" and state["dash_compare"]
                     and len(groups_with_facets) >= 2)
        if comparing:
            # each search is its own cohort; the AND-combined total would read 0 for
            # disjoint searches, so show per-search specimen counts instead.
            parts = [f"{_group_label(g)}: "
                     f"{_with(lambda s, gg=g: ex_svc.counts(s, [gg]))['specimens']}"
                     for g in groups_with_facets]
            count_lbl.set_text("Comparing — " + "  ·  ".join(parts))
        else:
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
                _render_dashboard()   # fetches its own cohorts (combined or per-search)

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

    # ── favorites (saved searches) ────────────────────────────────────────
    def _load_search(groups):
        """Replace the current search with `groups` (used by apply-favorite)."""
        state["groups"] = groups or [{"op": "and", "facets": []}]
        state["dash_dates"] = None
        _render_groups()
        _refresh()

    def _apply_favorite(fav_id):
        res = _with(lambda s: fav_svc.resolve_by_id(s, fav_id))
        if res is None:
            _refresh_favorites()          # vanished under us
            return
        applied = fav_svc.apply_groups(res["groups"])
        _load_search(applied)
        if res["stale"]:
            ui.notify(f"{res['stale']} filter(s) in this favorite no longer exist and "
                      "were skipped.", type="warning")

    def _save_current():
        if not _all_facets():
            ui.notify("Add at least one filter before saving a favorite.", type="warning")
            return
        dlg = ui.dialog()
        with dlg, ui.card().classes("gap-2 min-w-[320px]"):
            ui.label("Save this search as a favorite").classes("text-sm font-medium")
            name_in = ui.input("Name").props("outlined dense autofocus").classes("w-full")

            def do_save():
                try:
                    _write(lambda s: fav_svc.create(s, name_in.value, state["groups"]))
                except ValueError as e:
                    ui.notify(str(e), type="warning"); return
                dlg.close(); _refresh_favorites()
                ui.notify("Favorite saved.", type="positive")

            name_in.on("keydown.enter", lambda _: do_save())
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat dense no-caps")
                ui.button("Save", on_click=do_save).props("dense no-caps unelevated")
        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
        dlg.open()

    def _rename_favorite(fav_id, current):
        dlg = ui.dialog()
        with dlg, ui.card().classes("gap-2 min-w-[320px]"):
            ui.label("Rename favorite").classes("text-sm font-medium")
            name_in = ui.input("Name", value=current) \
                .props("outlined dense autofocus").classes("w-full")

            def do_rename():
                try:
                    _write(lambda s: fav_svc.rename(s, fav_id, name_in.value))
                except ValueError as e:
                    ui.notify(str(e), type="warning"); return
                dlg.close(); _refresh_favorites()

            name_in.on("keydown.enter", lambda _: do_rename())
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat dense no-caps")
                ui.button("Save", on_click=do_rename).props("dense no-caps unelevated")
        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
        dlg.open()

    def _delete_favorite(fav_id):
        _write(lambda s: fav_svc.delete(s, fav_id))
        _refresh_favorites()

    def _toggle_default(fav_id, make_default):
        _write(lambda s: fav_svc.set_default(s, fav_id if make_default else None))
        _refresh_favorites()

    def _refresh_favorites():
        fav_rail.clear()
        favs = _with(lambda s: fav_svc.list_searches(s))
        with fav_rail:
            ui.html('<div class="ex-fav-hd">★ Favorites</div>')
            if not favs:
                ui.html('<div class="ex-fav-empty">No favorites yet. Build a search, then '
                        '“Save current search”.</div>')
            for fav in favs:
                is_def = bool(fav.is_default)
                row = ui.element("div").classes("ex-fav")
                with row:
                    if is_def:
                        ui.html('<span class="ex-fav-star material-icons" '
                                'title="Applied when Explore opens">star</span>')
                    ui.html(f'<span class="ex-fav-name" title="{_html.escape(fav.name)}">'
                            f'{_html.escape(fav.name)}</span>')
                    # the row (minus the ⋮) applies the favorite
                    row.on("click", lambda _, fid=fav.id: _apply_favorite(fid))
                    menu_btn = ui.button(icon="more_vert") \
                        .props("flat dense round size=xs").on("click.stop", lambda: None)
                    with menu_btn, ui.menu().props("auto-close"):
                        ui.menu_item("Set as default" if not is_def else "Unset default",
                                     lambda fid=fav.id, d=is_def: _toggle_default(fid, not d))
                        ui.menu_item("Rename",
                                     lambda fid=fav.id, nm=fav.name: _rename_favorite(fid, nm))
                        ui.menu_item("Delete", lambda fid=fav.id: _delete_favorite(fid))
            ui.button("Save current search", icon="star_border", on_click=_save_current) \
                .props("flat dense no-caps size=sm").classes("mt-1 self-start")

    _render_groups()   # renders the first (empty) search group + Add-another button
    # Apply the default favorite on open, if one is set (otherwise the empty search).
    _default = _with(lambda s: fav_svc.get_default(s))
    if _default is not None:
        _apply_favorite(_default.id)
    else:
        _refresh()
    _refresh_favorites()
    return {"refresh": _refresh}

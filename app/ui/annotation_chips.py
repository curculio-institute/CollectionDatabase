"""Annotation chip rendering — pure HTML helpers shared by the record sheets, the
annotation popup and the vocabulary table (spec "Entering annotations" → Colour).

A term's ``css_color`` reaches here only as ``#rgb`` / ``#rrggbb`` — the DB CHECK
``ck_annotation_term_css_color`` guarantees it — so it is safe to inline in a style
attribute. Names and values are HTML-escaped. Inspired by the Curculionoidea TaxonWorks
contributor docs, which colour keyword chips from TW's ``css_color``.
"""
from __future__ import annotations

import html as _html

CSS = """<style>
.ann-chip  { display:inline-flex; align-items:center; gap:4px; font-size:.75rem;
             line-height:1.25; padding:1px 8px; border-radius:999px;
             border:1px solid var(--tp-base-border,#e2e8f0);
             background:rgba(148,163,184,.15); color:inherit; white-space:nowrap; }
.ann-local { font-size:.58rem; text-transform:uppercase; letter-spacing:.04em; opacity:.7; }
.ann-da    { display:inline-flex; align-items:center; gap:4px; font-size:.82rem; }
.ann-line  { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
.ann-swatch { display:inline-block; width:14px; height:14px; border-radius:3px;
              vertical-align:middle; }
</style>"""


def text_color(css_color: str) -> str:
    """``#000`` or ``#fff``, whichever reads on this background (WCAG relative luminance,
    threshold 0.179 — the point where black and white give equal contrast)."""
    h = css_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return "#000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.179 else "#fff"


def chip_html(name: str, css_color: str | None = None, *, local_only: bool = False) -> str:
    style = (f' style="background:{css_color};border-color:{css_color};'
             f'color:{text_color(css_color)}"' if css_color else "")
    badge = '<span class="ann-local">local</span>' if local_only else ""
    return f'<span class="ann-chip"{style}>{_html.escape(name)}{badge}</span>'


def swatch_html(css_color: str | None) -> str:
    if not css_color:
        return ('<span class="ann-swatch" style="border:1px dashed '
                'var(--tp-base-soft,#9ca3af)"></span>')
    return (f'<span class="ann-swatch" style="background:{css_color};'
            f'border:1px solid rgba(0,0,0,.2)"></span>')


def annotations_html(views) -> str:
    """Tags as chips, then data attributes as ``chip: value``; "" when there are none."""
    if not views:
        return ""
    parts = [chip_html(v.name, v.css_color, local_only=not v.linked)
             for v in views if v.kind == "tag"]
    parts += [f'<span class="ann-da">{chip_html(v.name, v.css_color, local_only=not v.linked)}'
              f'{_html.escape(v.value or "")}</span>'
              for v in views if v.kind == "data_attribute"]
    return f'<div class="ann-line">{"".join(parts)}</div>'

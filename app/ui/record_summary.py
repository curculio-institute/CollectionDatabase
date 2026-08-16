"""How a record is summarised — ONE renderer, used by every browse surface.

Every surface used to invent its own f-string ("#1  JJPC-00021  Otiorhynchus armadillo …",
Explore's escaped lot line, the event pickers), and they disagreed about what matters and how a
name is written. Browsing felt clunky because nothing looked the same twice. This module is the
single owner of the answer.

The shape, taken from the Records specimen picker:

    JJPC-00021  Otiorhynchus (Otiorhynchus) armadillo (Rossi, 1792)  ♂  collected from Quercus robur  🔒
    Bodenmöser, Germany · 2026-06-13 · leg. J. Jilg · det. J. Jilg

  * IDENTITY FIRST — the catalog number and the name, because that is what a specimen *is*.
  * The name is italicised BY RANK and the authorship stays roman
    (taxa.render_full_name is the single owner of that convention).
  * The host plant rides on the identity line WITH ITS RELATIONSHIP ("collected from Quercus
    robur") — the relationship is what the association means.
  * The collecting event sits beneath, in the soft colour: it is what tells two specimens of
    the same species apart.
  * CONFIDENTIALITY is a closed amber padlock at the END of the line, and NOTHING when the
    record is public — an "open padlock everywhere" would be clutter on the 99% case. The
    padlock is the closed-access convention (an emoji 🔒 renders as a colour glyph, differently
    per platform, and cannot be tinted — the same reason 🪲 was rejected for `pest_control`).
    A specimen is withheld either by its OWN flag or by INHERITING a confidential event (which
    drops all of its specimens); one glyph means "this will be withheld", and the tooltip says
    which.

Print queue is deliberately NOT routed through here — a label is a physical artifact with its
own composition rules (labels.py).
"""
from __future__ import annotations

import html as _html

import app.services.taxa as taxa_svc

# Amber, not red: a confidential record is restricted, not wrong. Red reads as an error, and the
# flag is a normal curatorial state. Amber is also how closed-access padlocks are rendered.
_LOCK_AMBER = "#b45309"
_CONSENT_GREEN = "#15803d"
# Red is reserved for the ONE state that is unconditional and has no setting — a confidential
# collector is never exported, in any form (CLAUDE.md §5c rule 3). Every other withholding
# reason in this file is amber ("restricted, not wrong"); this is the one exception, because
# unlike the rest it cannot be resolved by a policy choice, only by the person's own flag.
_PERSON_RED = "#dc2626"


def confidential_reason(*, own: bool, from_event: bool) -> str:
    """Why this record will be withheld — the tooltip behind the padlock."""
    if own and from_event:
        return ("Withheld from export: this specimen is flagged confidential, and its "
                "collecting event is too (a confidential event withholds all of its specimens).")
    if own:
        return "Withheld from export: this specimen is flagged confidential."
    if from_event:
        return ("Withheld from export: its collecting event is confidential, which withholds "
                "every specimen collected at it.")
    return ""


def lock_html(*, own: bool = False, from_event: bool = False) -> str:
    """The closed amber padlock — or nothing at all when the record is public."""
    if not (own or from_event):
        return ""
    tip = _html.escape(confidential_reason(own=own, from_event=from_event))
    # A <span>, never an <i>: `i` means EMPHASIS, so every "italicise names in this row" rule
    # reaches it and skews the padlock glyph. An icon is not text and is never italic.
    return (f'<span class="material-icons rs-lock" style="color:{_LOCK_AMBER}" '
            f'title="{tip}">lock</span>')


# (design pass, #170) — the sync tab's "Why some are not eligible" list is gone; the same
# information now rides on the specimen row itself, wherever it's browsed (Explore first),
# as two more badges beside the padlock. The padlock stays scoped to the specimen's/event's
# OWN `confidential` flag; these two cover the other two grounds `export_decision` withholds
# on (CLAUDE.md §5c: privacy — the recordedBy person's own state — and certainty — the
# determination itself).
_CONSENT_TOOLTIP = {
    # Every one of the three states names its own consequence, in the same voice as
    # `confidential_reason` above — the padlock's tooltip is a sentence, so these are too.
    # The confidential case especially: it is the most severe of the three and had the
    # least informative tooltip (a bare "Confidential", which never said what follows
    # from it or that the collector, not the specimen, is the reason).
    "confidential": ("Withheld from export: the collector (recordedBy) is flagged "
                     "confidential, which withholds the whole record."),
    "blocked": ("Withheld from export: the collector (recordedBy) has not consented, and "
                "“Export only data where the collector has consented” is active."),
    "redacted": "Will be exported with the collector’s name removed.",
}
_CONSENT_COLOR = {
    "confidential": _PERSON_RED,
    "blocked": _LOCK_AMBER,
    "redacted": "var(--tp-base-soft)",   # informational, not a restriction — still exported
}


def consent_badge_html(state: str = "") -> str:
    """The recordedBy person's export handling — `export_decision(...).recorded_by_state`,
    one of "" / "confidential" / "blocked" / "redacted". A `person_off` glyph, not another
    padlock (live review): the padlock already means "this specimen/event's own flag
    withholds it"; this is about the COLLECTOR's state, a different question with its own
    three-way answer, so it gets its own icon rather than overloading the lock with a third
    colour.

    An unrecognised `state` renders nothing, the same as `""` (code review fix — a bare
    `if not state` guard let anything else through to a direct dict index, so one bad
    value raised `KeyError` and took down every OTHER row's render with it, since
    `_render_specimens`/`_render_events` build the whole results panel's HTML in one
    loop). `state` is always sourced from `export_decision`'s own closed set in every
    current caller, so this is a last-resort guard against a future caller passing
    something else — a missing badge is a survivable display gap; an unhandled
    exception killing unrelated specimens' rows is not."""
    if state not in _CONSENT_TOOLTIP:
        return ""
    tip = _html.escape(_CONSENT_TOOLTIP[state])
    return (f'<span class="material-icons rs-lock" style="color:{_CONSENT_COLOR[state]}" '
            f'title="{tip}">person_off</span>')


def identification_doubt_badge_html(reasons: tuple[str, ...] = ()) -> str:
    """Why the CURRENT identification itself isn't publishable — `export_decision(...)
    .determination_reasons` (qualified 'cf./aff./…', below species rank, or no current ID
    at all). An orange question mark (live review): these are all forms of "the ID isn't
    certain enough", so — like the padlock's own two sub-reasons — they collapse into ONE
    badge whose tooltip lists whichever apply, rather than a separate icon per sub-reason."""
    reasons = tuple(r for r in reasons if r)
    if not reasons:
        return ""
    tip = _html.escape("Not eligible for TaxonWorks export: " + "; ".join(reasons))
    return (f'<span class="material-icons rs-lock" style="color:{_LOCK_AMBER}" '
            f'title="{tip}">question_mark</span>')


def name_html(name: str, rank: str | None = None, authorship: str | None = None) -> str:
    """A scientific name: italic only for the genus group and below, authorship roman.

    Thin pass-through to the single renderer, :func:`taxa.render_full_name`."""
    return taxa_svc.render_full_name(name, authorship=authorship, taxon_rank=rank)


def hosts_html(hosts) -> str:
    """"collected from <i>Quercus robur</i>" — the associations, on the identity line.

    Each host is ``(relationship, name, rank)``. The RELATIONSHIP is what the association means:
    without it a plant name beside a beetle says nothing about how they met. Several
    associations collapse to the first + a count — the summary stays one line.
    """
    hosts = [h for h in (hosts or []) if h and len(h) >= 2 and h[1]]
    if not hosts:
        return ""
    rel, name, rank = (list(hosts[0]) + [None])[:3]
    verb = _html.escape(rel) if rel else "on"
    more = f' <span class="rs-more">+{len(hosts) - 1}</span>' if len(hosts) > 1 else ""
    return f'<span class="rs-host">{verb} {name_html(name, rank)}{more}</span>'


def _bits(sex: str | None, count: int | None) -> str:
    out = []
    if count and count > 1:
        out.append(f"{count}×")
    if sex:
        out.append(sex)
    return (f'<span class="rs-badge">{_html.escape(" ".join(out))}</span>') if out else ""


def _det_html(identified_by: str | None, date_identified: str | None) -> str:
    """"det. J. Jilg 2026" — the determiner + identification year, muted, on the name line
    (it's part of the *identity*, parallel to the taxon authorship). Year shown only when a
    4-digit year can be read from the ISO dateIdentified."""
    if not identified_by:
        return ""
    yr = (date_identified or "")[:4]
    txt = f"det. {identified_by}" + (f" {yr}" if yr.isdigit() and len(yr) == 4 else "")
    return f'<span class="rs-det">{_html.escape(txt)}</span>'


def specimen_html(
    *,
    catalog: str,
    name: str,
    rank: str | None = None,
    authorship: str | None = None,
    hosts=None,
    sex: str | None = None,
    count: int | None = None,
    locality: str = "",
    event_date: str | None = None,
    recorded_by: str | None = None,
    identified_by: str | None = None,
    date_identified: str | None = None,
    confidential: bool = False,
    event_confidential: bool = False,
    recorded_by_state: str = "",
    determination_reasons: tuple[str, ...] = (),
    undetermined_note: str = "— no identification —",
) -> str:
    """The two-line specimen row used by every browse surface.

    Name line: catalog · name (+authorship) · sex/count · **det. Determiner Year** · lock ·
    person_off · question mark. Meta line: place · **collected from Host** · eventDate ·
    leg. Collector. The determination lives with the name (identity); the host sits between
    place and date on the collection line.

    The three trailing badges are `export_decision`'s three independent grounds, in its own
    rule order (CLAUDE.md §5c) — the padlock (rules 1–2, this record's own confidential
    flag), `person_off` (rules 3–4, the recordedBy person's export state), and the question
    mark (rule 5, the determination's own certainty). #170: this replaced the TaxonWorks
    sync tab's "Why some are not eligible" list, which could get arbitrarily long — the same
    information now travels with the specimen everywhere it's browsed instead of living in
    one dedicated, unbounded list."""
    ident = name_html(name, rank, authorship) if name else \
        f'<span class="rs-none">{_html.escape(undetermined_note)}</span>'
    # Meta line — plain-text bits are escaped; the host bit is trusted HTML (italic name).
    meta: list[str] = []
    if locality:
        meta.append(_html.escape(locality))
    host = hosts_html(hosts)
    if host:
        meta.append(host)
    if event_date:
        meta.append(_html.escape(event_date))
    if recorded_by:
        meta.append(_html.escape(f"leg. {recorded_by}"))
    sub = "  ·  ".join(meta)
    return (
        '<div class="rs-row">'
        '<div class="rs-top">'
        f'<span class="rs-cat">{_html.escape(catalog or "—")}</span>'
        f'{ident}{_bits(sex, count)}{_det_html(identified_by, date_identified)}'
        f'<span class="rs-spacer"></span>'
        f'{lock_html(own=confidential, from_event=event_confidential)}'
        f'{consent_badge_html(recorded_by_state)}'
        f'{identification_doubt_badge_html(determination_reasons)}'
        '</div>'
        # No event line at all when there is nothing to say — under an Explore event the
        # locality IS the event above, so a bare "—" would be noise.
        + (f'<div class="rs-sub">{sub}</div>' if sub else '')
        + '</div>'
    )


def specimen_plain(
    *, catalog: str, name: str, authorship: str | None = None, hosts=None,
    sex: str | None = None, count: int | None = None, locality: str = "",
    event_date: str | None = None, recorded_by: str | None = None,
    identified_by: str | None = None, **_ignored,
) -> str:
    """The same content as PLAIN text — what a q-select filters against and echoes into its
    input once selected (it cannot hold markup). Carries every searchable datum, so the search
    box is a search over all of them and not just the name."""
    host = ", ".join(f"{h[0]} {h[1]}".strip() for h in (hosts or [])
                     if h and len(h) >= 2 and h[1])
    parts = [
        catalog or "",
        f"{name} {authorship or ''}".strip() or "—",
        f"{count}×" if (count or 1) > 1 else "",
        sex or "",
        host,
        locality, event_date or "",
        f"leg. {recorded_by}" if recorded_by else "",
        f"det. {identified_by}" if identified_by else "",
    ]
    return "  ".join(p for p in parts if p)


def event_html(
    *, summary: str, n_specimens: int | None = None, confidential: bool = False,
) -> str:
    """One collecting event: its locality line, its specimen count, and the padlock."""
    count = (f'<span class="rs-badge">{n_specimens} spec.</span>'
             if n_specimens else "")
    return (
        '<div class="rs-row">'
        '<div class="rs-top">'
        f'<span class="rs-ev">{_html.escape(summary or "event")}</span>{count}'
        f'<span class="rs-spacer"></span>{lock_html(own=confidential)}'
        '</div></div>'
    )


CSS = f"""
<style>
.rs-row     {{ width:100%; padding:1px 0; }}
.rs-top     {{ display:flex; align-items:baseline; gap:7px; flex-wrap:nowrap; width:100%; }}
.rs-sub     {{ font-size:.74rem; color:var(--tp-base-soft); margin-top:1px; }}
.rs-cat     {{ font-family:ui-monospace,monospace; font-size:.78rem; font-weight:600;
               color:var(--tp-secondary); flex-shrink:0; }}
.rs-ev      {{ font-size:.9rem; }}
.rs-det     {{ font-size:.76rem; color:var(--tp-base-soft); white-space:nowrap; flex-shrink:0; }}
.rs-host    {{ font-size:.82rem; color:var(--tp-base-soft); white-space:nowrap; }}
.rs-host i  {{ font-style:italic; }}
.rs-more    {{ font-size:.7rem; opacity:.75; }}
.rs-badge   {{ font-size:.7rem; padding:0 5px; border-radius:8px; flex-shrink:0;
               background:var(--tp-base-border); color:var(--tp-base-soft); }}
.rs-none    {{ font-style:italic; color:var(--tp-base-soft); }}
/* the padlock sits at the END of the line — absence of a lock means "not withheld" */
.rs-spacer  {{ flex:1 1 auto; }}
.rs-lock    {{ font-size:17px; flex-shrink:0; align-self:center; }}
.rs-row i   {{ font-style:italic; }}
/* An ICON IS NOT TEXT. The italics rules above target the name; a Material ligature caught by
   them renders as a skewed glyph. Belt and braces, since an icon may land in any of them. */
.rs-row .material-icons,
.rs-host .material-icons,
.material-icons {{ font-style:normal !important; }}
/* People table: one column — open padlock (green) = consented, closed (amber) = confidential */
.rs-consent {{ color:{_CONSENT_GREEN}; font-size:18px; }}
.rs-conf    {{ color:{_LOCK_AMBER}; font-size:18px; }}
</style>
"""

"""Annotations — the local tag / data-attribute vocabulary and the annotations on records.

Terms are LOCAL (``annotation_term``); a term may carry an optional link to one
TaxonWorks keyword / predicate, which ``tw_links.py`` owns — this module never talks to
TaxonWorks. Everything here refuses loudly instead of guessing (CLAUDE.md §2): a term not
offered for a record type, a tag with a value, a merge that would have to pick between two
values, a staged value that contradicts the stored one.

Two write modes, matching the two UIs (spec "Entering annotations"):
* ``set_annotations`` — REPLACE a record's set (Records' deferred Save).
* ``add_annotations`` — ADD to it (Digitize's staged Save, which may land on a reused
  event). The same value is a no-op; a different value refuses.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Annotation, AnnotationTerm
from app.models.base import _utcnow

KINDS = ("tag", "data_attribute")
TARGETS = {
    "specimen": "collection_object_id",
    "event": "collecting_event_id",
    "association": "biological_association_id",
}
_ALLOW = {"specimen": "allow_specimen", "event": "allow_event",
          "association": "allow_association"}
_NOUN = {"specimen": "specimen", "event": "collecting event",
         "association": "biological association"}
_KIND_NOUN = {"tag": "tag", "data_attribute": "data attribute"}
_HEX_RE = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")
_FORBIDDEN = ("\t", "\n", "\r")
_UNSET = object()


@dataclass(frozen=True)
class AnnotationItem:
    term_id: int
    value: str | None = None


@dataclass(frozen=True)
class AnnotationView:
    term_id: int
    kind: str
    name: str
    value: str | None
    css_color: str | None
    linked: bool


@dataclass(frozen=True)
class MergePlan:
    keep_name: str
    absorb_name: str
    repoint: int                 # annotations moved onto the kept term
    fold: int                    # duplicates on the same record, dropped (same meaning)
    conflicts: tuple[str, ...]   # same record, different values — the merge refuses


# ── validation ────────────────────────────────────────────────────────────────

def normalize_css_color(value: str | None) -> str | None:
    """A colour the user typed: blank -> None, ``#rgb`` / ``#rrggbb`` kept as typed,
    anything else refused with the value named. No colour picker (spec "Term colour")."""
    clean = (value or "").strip()
    if not clean:
        return None
    if not _HEX_RE.fullmatch(clean):
        raise ValueError(f"Colour '{clean}' is not #rgb or #rrggbb (e.g. #254d0d).")
    return clean


def tw_css_color(value: object) -> tuple[str | None, str | None]:
    """A colour TaxonWorks returned: ``(colour, warning)``. TW does not validate its
    ``css_color`` column (``app/models/controlled_vocabulary_term.rb``, no validation on
    it @ 897f385), so it may hold ``red`` or garbage — never converted, never guessed:
    the colour becomes None and the caller shows the warning."""
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f"TaxonWorks colour {value!r} not usable (only #rgb / #rrggbb)."
    clean = value.strip()
    if not clean:
        return None, None
    if _HEX_RE.fullmatch(clean):
        return clean, None
    return None, f"TaxonWorks colour '{clean}' not usable (only #rgb / #rrggbb)."


def clean_value(value: str | None) -> str:
    """A data-attribute value: trimmed, non-empty, no tab / line break (it becomes a TSV
    cell — the DB CHECK refuses the same)."""
    clean = (value or "").strip()
    if not clean:
        raise ValueError("A data attribute needs a value.")
    if any(ch in clean for ch in _FORBIDDEN):
        raise ValueError(f"Value {clean!r} contains a tab or line break.")
    # TaxonWorks' DwC importer rewrites every cell: ``normalize_value!`` strips leading /
    # trailing whitespace (incl. NBSP) and squeezes repeated spaces
    # (SpeciesFileGroup/taxonworks@897f385 app/models/dataset_record/darwin_core.rb:91-94).
    # A value it would rewrite would show as a permanent value_differs in Compare, so it is
    # refused here rather than normalised silently (CLAUDE.md §2).
    odd = sorted({ch for ch in clean if ch.isspace() and ch != " "})
    if odd:
        names = ", ".join("non-breaking space (U+00A0)" if ch == "\u00a0"
                          else f"U+{ord(ch):04X}" for ch in odd)
        raise ValueError(f"Value {clean!r} contains a {names} — use a plain space; "
                         f"TaxonWorks would rewrite it.")
    if "  " in clean:
        raise ValueError(f"Value {clean!r} contains repeated spaces — TaxonWorks would "
                         f"squeeze them to one, so the published value would differ.")
    return clean


def _clean_name(name: str | None) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("A term needs a name.")
    return clean


def _col(target: str) -> str:
    if target not in TARGETS:
        raise ValueError(f"unknown annotation target {target!r}")
    return TARGETS[target]


# ── terms ─────────────────────────────────────────────────────────────────────

def _term(session: Session, term_id: int) -> AnnotationTerm:
    t = session.get(AnnotationTerm, term_id)
    if t is None:
        raise ValueError(f"annotation term {term_id} not found")
    return t


def name_taken(session: Session, kind: str, name: str,
               exclude_id: int | None = None) -> bool:
    q = session.query(AnnotationTerm).filter(
        AnnotationTerm.kind == kind,
        func.lower(AnnotationTerm.name) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(AnnotationTerm.id != exclude_id)
    return session.query(q.exists()).scalar()


def create_term(session: Session, *, kind: str, name: str, allow_specimen: bool = False,
                allow_event: bool = False, allow_association: bool = False,
                css_color: str | None = None) -> AnnotationTerm:
    if kind not in KINDS:
        raise ValueError(f"unknown term kind {kind!r}")
    clean = _clean_name(name)
    if name_taken(session, kind, clean):
        raise ValueError(f"A {_KIND_NOUN[kind]} named '{clean}' already exists.")
    t = AnnotationTerm(kind=kind, name=clean, allow_specimen=int(allow_specimen),
                       allow_event=int(allow_event),
                       allow_association=int(allow_association),
                       css_color=normalize_css_color(css_color),
                       created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def term_usage(session: Session, term_id: int) -> int:
    return session.query(Annotation).filter(Annotation.term_id == term_id).count()


def update_term(session: Session, term_id: int, *, name=_UNSET, kind=_UNSET,
                allow_specimen=_UNSET, allow_event=_UNSET, allow_association=_UNSET,
                css_color=_UNSET) -> AnnotationTerm:
    t = _term(session, term_id)
    if kind is not _UNSET and kind != t.kind:
        if kind not in KINDS:
            raise ValueError(f"unknown term kind {kind!r}")
        if t.tw_id is not None:
            raise ValueError(f"Unlink '{t.name}' from TaxonWorks before changing its kind — "
                             f"its link points at a {_KIND_NOUN[t.kind]} term.")
        n = term_usage(session, t.id)
        if n:
            raise ValueError(f"'{t.name}' is used by {n} record(s); its kind cannot change.")
        if name is _UNSET and name_taken(session, kind, t.name, exclude_id=t.id):
            raise ValueError(f"A {_KIND_NOUN[kind]} named '{t.name}' already exists — "
                             f"rename '{t.name}' before changing its kind.")
        t.kind = kind
    if name is not _UNSET:
        clean = _clean_name(name)
        if name_taken(session, t.kind, clean, exclude_id=t.id):
            raise ValueError(f"A {_KIND_NOUN[t.kind]} named '{clean}' already exists.")
        t.name = clean
    for attr, val in (("allow_specimen", allow_specimen), ("allow_event", allow_event),
                      ("allow_association", allow_association)):
        if val is not _UNSET:
            setattr(t, attr, int(bool(val)))
    if css_color is not _UNSET:
        new = normalize_css_color(css_color)
        if new != t.css_color:
            if t.tw_id is not None:
                raise ValueError(f"'{t.name}' takes its colour from TaxonWorks while "
                                 f"linked — unlink it to set a local colour.")
            t.css_color = new
    t.updated_at = _utcnow()
    session.flush()
    return t


def delete_term(session: Session, term_id: int) -> None:
    t = _term(session, term_id)
    n = term_usage(session, t.id)
    if n:
        raise ValueError(f"Cannot delete '{t.name}': still used by {n} record(s).")
    session.delete(t)
    session.flush()


def list_terms(session: Session, kind: str | None = None) -> list[AnnotationTerm]:
    q = session.query(AnnotationTerm)
    if kind is not None:
        q = q.filter(AnnotationTerm.kind == kind)
    return q.order_by(AnnotationTerm.kind, func.lower(AnnotationTerm.name)).all()


def allowed_terms(session: Session, target: str) -> list[AnnotationTerm]:
    _col(target)                                   # refuses an unknown target
    flag = getattr(AnnotationTerm, _ALLOW[target])
    return (session.query(AnnotationTerm).filter(flag == 1)
            .order_by(AnnotationTerm.kind, func.lower(AnnotationTerm.name)).all())


# ── merge ─────────────────────────────────────────────────────────────────────

def _record_label(a: Annotation) -> str:
    for target, col in TARGETS.items():
        rid = getattr(a, col)
        if rid is not None:
            return f"{target} #{rid}"
    return f"annotation #{a.id}"


def _plan(session: Session, keep_id: int, absorb_id: int):
    if keep_id == absorb_id:
        raise ValueError("Cannot merge a term with itself.")
    keep, absorb = _term(session, keep_id), _term(session, absorb_id)
    if keep.kind != absorb.kind:
        raise ValueError(f"'{absorb.name}' is a {_KIND_NOUN[absorb.kind]} and '{keep.name}' "
                         f"a {_KIND_NOUN[keep.kind]} — only terms of the same kind merge.")
    if absorb.tw_id is not None:
        raise ValueError(f"Unlink '{absorb.name}' from TaxonWorks first — a merge never "
                         f"discards or moves a link.")
    kept_by_record = {}
    for a in session.query(Annotation).filter(Annotation.term_id == keep.id):
        kept_by_record[_record_label(a)] = a
    fold, move, conflicts = [], [], []
    for a in session.query(Annotation).filter(Annotation.term_id == absorb.id):
        other = kept_by_record.get(_record_label(a))
        if other is None:
            move.append(a)
        elif other.value == a.value:
            fold.append(a)
        else:
            conflicts.append(f"{_record_label(a)}: {other.value!r} vs {a.value!r}")
    plan = MergePlan(keep_name=keep.name, absorb_name=absorb.name, repoint=len(move),
                     fold=len(fold), conflicts=tuple(conflicts))
    return plan, absorb, fold, move


def merge_preview(session: Session, keep_id: int, absorb_id: int) -> MergePlan:
    return _plan(session, keep_id, absorb_id)[0]


def merge_terms(session: Session, keep_id: int, absorb_id: int) -> MergePlan:
    """De-duplicate two terms. Refuses (spec "Merge rules") on a kind mismatch, a linked
    absorbed term, or any record holding both with different values."""
    plan, absorb, fold, move = _plan(session, keep_id, absorb_id)
    if plan.conflicts:
        shown = "; ".join(plan.conflicts[:5])
        more = f" (+{len(plan.conflicts) - 5} more)" if len(plan.conflicts) > 5 else ""
        raise ValueError(f"Cannot merge '{plan.absorb_name}' into '{plan.keep_name}': "
                         f"records hold different values — {shown}{more}. "
                         f"Make them agree first.")
    for a in fold:
        session.delete(a)
    session.flush()
    now = _utcnow()
    keep = _term(session, keep_id)
    for a in move:
        # Through the relationship, not term_id alone: `a.term` is loaded (lazy="joined")
        # and still points at the absorbed row, which is deleted next.
        a.term = keep
        a.updated_at = now
    session.flush()
    session.delete(absorb)
    session.flush()
    return plan


# ── annotations on records ────────────────────────────────────────────────────

def annotations_for(session: Session, target: str, target_id: int) -> list[Annotation]:
    col = getattr(Annotation, _col(target))
    return (session.query(Annotation).join(AnnotationTerm, Annotation.term_id == AnnotationTerm.id)
            .filter(col == target_id)
            # tags first ('tag' sorts after 'data_attribute', hence desc), then by name
            .order_by(AnnotationTerm.kind.desc(), func.lower(AnnotationTerm.name)).all())


def views_for(session: Session, target: str, target_id: int) -> list[AnnotationView]:
    return [AnnotationView(term_id=a.term.id, kind=a.term.kind, name=a.term.name,
                           value=a.value, css_color=a.term.css_color,
                           linked=a.term.tw_id is not None)
            for a in annotations_for(session, target, target_id)]


def _value_for(term: AnnotationTerm, raw: str | None) -> str | None:
    if term.kind == "tag":
        if raw not in (None, ""):
            raise ValueError(f"'{term.name}' is a tag and carries no value.")
        return None
    return clean_value(raw)


def _require_allowed(term: AnnotationTerm, target: str) -> None:
    if not getattr(term, _ALLOW[target]):
        raise ValueError(f"'{term.name}' is not offered for {_NOUN[target]}s — allow it in "
                         f"Controlled Vocabularies first.")


def _unique_items(items: Iterable[AnnotationItem]) -> list[AnnotationItem]:
    out, seen = [], set()
    for it in items:
        if it.term_id in seen:
            raise ValueError(f"term {it.term_id} appears twice in one annotation set")
        seen.add(it.term_id)
        out.append(it)
    return out


def _insert(session: Session, target: str, target_id: int, term: AnnotationTerm,
            value: str | None) -> None:
    a = Annotation(term_id=term.id, term_kind=term.kind, value=value,
                   created_at=_utcnow(), updated_at=_utcnow())
    setattr(a, _col(target), target_id)
    session.add(a)


def set_annotations(session: Session, target: str, target_id: int,
                    items: Iterable[AnnotationItem]) -> None:
    """Replace the record's annotation set (inside the caller's transaction). A term
    already on the record stays even if it is no longer offered for this target; a NEW
    term must be offered (spec: "allow" switches filter the picker only)."""
    items = _unique_items(items)
    existing = {a.term_id: a for a in annotations_for(session, target, target_id)}
    # Validate EVERY item before touching anything: a refusal leaves the set unchanged.
    plan = []
    for it in items:
        term = _term(session, it.term_id)
        value = _value_for(term, it.value)
        if term.id not in existing:
            _require_allowed(term, target)
        plan.append((term, value))
    wanted = {it.term_id for it in items}
    for term_id, a in existing.items():
        if term_id not in wanted:
            session.delete(a)
    session.flush()
    for term, value in plan:
        a = existing.get(term.id)
        if a is not None:
            if a.value != value:
                a.value = value
                a.updated_at = _utcnow()
            continue
        _insert(session, target, target_id, term, value)
    session.flush()


def add_annotations(session: Session, target: str, target_id: int,
                    items: Iterable[AnnotationItem]) -> None:
    """Add staged annotations (Digitize Save). Same term + same value: no-op. Same data
    attribute with another value: refused, both values named — the Save fails whole."""
    items = _unique_items(items)
    existing = {a.term_id: a for a in annotations_for(session, target, target_id)}
    for it in items:
        term = _term(session, it.term_id)
        value = _value_for(term, it.value)
        a = existing.get(term.id)
        if a is not None:
            if a.value != value:
                raise ValueError(
                    f"{_NOUN[target].capitalize()} #{target_id} already has "
                    f"'{term.name}' = '{a.value}'; the value entered here is '{value}'. "
                    f"Change it in Records instead.")
            continue
        _require_allowed(term, target)
        _insert(session, target, target_id, term, value)
    session.flush()

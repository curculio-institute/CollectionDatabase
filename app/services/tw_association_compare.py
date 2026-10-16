"""Biological-association compare (spec docs/superpowers/specs/2026-10-17-association-
compare-design.md) — read-only, diagnostic.

TaxonWorks cannot import associations (``associatedTaxa`` is [Not mapped],
``docs/taxonworks.md`` §5b), so they are entered there by hand; this lists what diverges.
Built like ``tw_annotation_compare.py``: fetch functions (I/O, every response re-verified),
a pure ``compare_associations`` and ``run_association_compare``.

Verified against SpeciesFileGroup/taxonworks @ 897f385 (live probes on sfg, 2026-10-17):
* ``GET /api/v1/biological_associations/basic`` (``config/routes/api_v1.rb:127``,
  ``api/v1/basic.json.jbuilder``): per association ``subject``/``object`` ``{id, type,
  label}``, ``subject_otu_id``, ``object_otu_id`` and the relationship **name only**.
  For an anatomical-part side the ``*_otu_id`` is the OTU at the top of its origin chain.
* ``GET /api/v1/biological_associations`` (``api_v1.rb:128``,
  ``api/v1/_base_attributes.json.jbuilder``): ``biological_relationship_id`` and both
  sides' type and id. The two lists are joined on the association id.
* ``collection_object_id[]`` matches the specimen as subject OR object
  (``lib/queries/biological_association/filter.rb``, ``subject_object_facet``).
* ``GET /api/v1/otus?otu_id[]=`` rows carry ``taxon_name_id``; ``GET
  /api/v1/taxon_names?taxon_name_id[]=`` rows carry ``cached`` (no authorship),
  ``cached_author_year``, ``rank``.
* There is no ``anatomical_parts`` endpoint (``docs/taxonworks.md`` §5b) — the part is
  shown by its label, never compared; only the host's OTU name is.

OTU ids are never compared (§5c): the other side is compared by taxon name.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.services.taxonworks as tw
from app.models import (Annotation, AnnotationTerm, BiologicalAssociation, CollectionObject,
                        Taxon)
from app.services import annotations as ann
from app.services import field_occurrence as fo_svc
from app.services import taxa
from app.services import tw_annotation_compare as ann_cmp
from app.services import tw_media_compare
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_annotation_compare import AnnotationFinding, _batches, _foreign, _gather
from app.services.tw_compare import CatalogIndex

_TIMEOUT = httpx.Timeout(25.0)
_PER_PAGE = 500
_BATCH = 100        # collection-object ids per request — keeps the GET URL short


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass(frozen=True)
class TwSide:
    type: str               # CollectionObject | Otu | FieldOccurrence | AnatomicalPart | …
    id: int
    otu_id: int | None      # the OTU TaxonWorks indexes for this side (None: none)
    label: str              # TaxonWorks' display label, e.g. "rhizome: Typha L."


@dataclass(frozen=True)
class TwAssociation:
    id: int
    relationship_id: int
    relationship_name: str
    subject: TwSide
    object: TwSide


@dataclass(frozen=True)
class TwName:
    taxon_name_id: int
    cached: str             # TaxonWorks' composed name, no authorship
    authorship: str | None  # cached_author_year
    rank: str | None


async def fetch_associations(co_ids: list[int]) -> dict[int, TwAssociation]:
    """Every TaxonWorks association on these collection objects (as subject or object):
    ``/basic`` (sides, OTU ids, labels) joined on id with the plain index (relationship
    id). Both are re-verified; any disagreement between them is a lookup failure, never a
    finding."""
    out: dict[int, TwAssociation] = {}
    if not co_ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(co_ids))):
            wanted = set(batch)
            what_b = f"the TaxonWorks associations (basic) of {len(batch)} collection object(s)"
            what_p = f"the TaxonWorks associations of {len(batch)} collection object(s)"

            def involves(st, sid, ot, oid, wanted=wanted) -> bool:
                return ((st == "CollectionObject" and sid in wanted)
                        or (ot == "CollectionObject" and oid in wanted))

            def check_basic(row: dict, what=what_b) -> None:
                s, o = row.get("subject"), row.get("object")
                if not isinstance(s, dict) or not isinstance(o, dict):
                    raise TaxonWorksUnreachable(
                        f"{what} returned association #{row.get('id')} without its sides.")
                for side in (s, o):
                    if not isinstance(side.get("type"), str) or not _is_int(side.get("id")):
                        raise TaxonWorksUnreachable(
                            f"{what} returned association #{row.get('id')} with an "
                            f"incomplete side.")
                for key in ("subject_otu_id", "object_otu_id"):
                    if key not in row or (row[key] is not None and not _is_int(row[key])):
                        raise TaxonWorksUnreachable(
                            f"{what} returned association #{row.get('id')} without a "
                            f"usable {key}.")
                if not involves(s["type"], s["id"], o["type"], o["id"]):
                    raise _foreign(what, "BiologicalAssociation", row.get("id"))

            def check_plain(row: dict, what=what_p) -> None:
                if not _is_int(row.get("biological_relationship_id")):
                    raise TaxonWorksUnreachable(
                        f"{what} returned association #{row.get('id')} without a "
                        f"relationship id.")
                if not involves(row.get("biological_association_subject_type"),
                                row.get("biological_association_subject_id"),
                                row.get("biological_association_object_type"),
                                row.get("biological_association_object_id")):
                    raise _foreign(what, "BiologicalAssociation", row.get("id"))

            params = {"collection_object_id[]": batch}
            basic, plain = await _gather(
                tw.fetch_all_pages(client, "/biological_associations/basic", params,
                                   what=what_b, check_row=check_basic, per_page=_PER_PAGE),
                tw.fetch_all_pages(client, "/biological_associations", params,
                                   what=what_p, check_row=check_plain, per_page=_PER_PAGE))
            by_id = {r["id"]: r for r in plain}
            basic_ids = {r["id"] for r in basic}
            if basic_ids != set(by_id):
                raise TaxonWorksUnreachable(
                    f"{what_p}: the two association lists disagree (ids "
                    f"{sorted(basic_ids ^ set(by_id))}) — treated as a lookup failure, "
                    f"never as a difference.")
            for b in basic:
                p = by_id[b["id"]]
                sides_b = (b["subject"]["type"], b["subject"]["id"],
                           b["object"]["type"], b["object"]["id"])
                sides_p = (p.get("biological_association_subject_type"),
                           p.get("biological_association_subject_id"),
                           p.get("biological_association_object_type"),
                           p.get("biological_association_object_id"))
                if sides_b != sides_p:
                    raise TaxonWorksUnreachable(
                        f"{what_p}: association #{b['id']} has different sides in the two "
                        f"lists — treated as a lookup failure.")
                out[b["id"]] = TwAssociation(
                    id=b["id"], relationship_id=p["biological_relationship_id"],
                    relationship_name=str(b.get("relationship") or "").strip(),
                    subject=TwSide(b["subject"]["type"], b["subject"]["id"],
                                   b["subject_otu_id"],
                                   str(b["subject"].get("label") or "").strip()),
                    object=TwSide(b["object"]["type"], b["object"]["id"],
                                  b["object_otu_id"],
                                  str(b["object"].get("label") or "").strip()))
    return out


async def fetch_otu_names(otu_ids: list[int]) -> dict[int, TwName | None]:
    """OTU id → its TaxonWorks taxon name (None: the OTU has no taxon name). Every OTU and
    every taxon name asked for must come back."""
    if not otu_ids:
        return {}
    otu_tn: dict[int, int | None] = {}
    names: dict[int, TwName] = {}
    async with _client() as client:
        for batch in _batches(sorted(set(otu_ids))):
            wanted = set(batch)
            what = f"the TaxonWorks OTUs ({len(batch)} requested)"

            def check_otu(row: dict, wanted=wanted, what=what) -> None:
                if row.get("id") not in wanted:
                    raise _foreign(what, "Otu", row.get("id"))
                tn = row.get("taxon_name_id", "absent")
                if tn == "absent" or (tn is not None and not _is_int(tn)):
                    raise TaxonWorksUnreachable(
                        f"{what} returned OTU #{row.get('id')} without a usable taxon_name_id.")

            rows = await tw.fetch_all_pages(client, "/otus", {"otu_id[]": batch}, what=what,
                                            check_row=check_otu, per_page=_PER_PAGE)
            for r in rows:
                otu_tn[r["id"]] = r["taxon_name_id"]
            missing = sorted(wanted - {r["id"] for r in rows})
            if missing:
                raise TaxonWorksUnreachable(
                    f"{what} did not return OTU(s) {missing} — treated as a lookup failure.")
        for batch in _batches(sorted({t for t in otu_tn.values() if t is not None})):
            wanted = set(batch)
            what = f"the TaxonWorks taxon names ({len(batch)} requested)"

            def check_name(row: dict, wanted=wanted, what=what) -> None:
                if row.get("id") not in wanted:
                    raise _foreign(what, "TaxonName", row.get("id"))
                if not str(row.get("cached") or "").strip():
                    raise TaxonWorksUnreachable(
                        f"{what} returned taxon name #{row.get('id')} without a name.")

            rows = await tw.fetch_all_pages(client, "/taxon_names",
                                            {"taxon_name_id[]": batch}, what=what,
                                            check_row=check_name, per_page=_PER_PAGE)
            for r in rows:
                names[r["id"]] = TwName(
                    r["id"], " ".join(str(r["cached"]).split()),
                    str(r.get("cached_author_year") or "").strip() or None, r.get("rank"))
            missing = sorted(wanted - {r["id"] for r in rows})
            if missing:
                raise TaxonWorksUnreachable(
                    f"{what} did not return taxon name(s) {missing} — treated as a lookup "
                    f"failure.")
    return {otu: (names[tn] if tn is not None else None) for otu, tn in otu_tn.items()}


# ── The host-name rule (mirrors tw_sync.check_names, docs/taxonworks.md §5c) ───────

# A difference that disappears after this folding is a transliteration, not a different
# author (§5c: 'Schönherr' vs 'Schoenherr' is expected).
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                       "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def _ws(s: str | None) -> str:
    return " ".join((s or "").split())


def host_verdict(local_name: str, local_authorship: str | None,
                 tw_name: TwName) -> tuple[str, tuple[str, ...]]:
    """``("match" | "subgenus" | "differs", notes)`` for a local host name (bare, no
    authorship) against a TaxonWorks taxon name. A subgenus-only difference matches with a
    note (placement is unstable); two authorships that differ make the host differ (same
    spelling may be a homonym); a missing authorship on either side is a note."""
    ln, tn = _ws(local_name), _ws(tw_name.cached)
    notes: list[str] = []
    if ln == tn:
        verdict = "match"
    elif taxa.binomial_key(ln) == taxa.binomial_key(tn):
        verdict = "subgenus"
        notes.append(f"subgenus placement differs: TaxonWorks has '{tn}'")
    else:
        return "differs", (f"name: here '{ln}', TaxonWorks '{tn}'",)
    la, ta = _ws(local_authorship), _ws(tw_name.authorship)
    if not la or not ta:
        notes.append("authorship not compared (missing on one side)")
    elif taxa.authorship_matches(la, ta):
        pass
    elif taxa.authorship_matches(la.translate(_FOLD), ta.translate(_FOLD)):
        notes.append(f"authorship spelled differently: here '{la}', TaxonWorks '{ta}'")
    else:
        return "differs", (f"authorship: here '{la}', TaxonWorks '{ta}'",)
    return verdict, tuple(notes)


# ── Result shapes ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssociationRow:
    catalog_number: str
    tw_specimen_id: int                 # for the "open in TaxonWorks" specimen link
    relationship: str
    host: str                           # the other side, as recorded on its side
    host_kind: str                      # taxon | field occurrence | specimen | TW type
    local_id: int | None = None
    tw_id: int | None = None
    tw_host: str | None = None          # Host differs: TaxonWorks' side
    reason: str = ""
    notes: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()   # display lines (To add / Only on TaxonWorks)


@dataclass(frozen=True)
class AssociationAnnotationFinding:
    catalog_number: str
    tw_specimen_id: int
    association: str                    # "collected from → Typha L."
    finding: AnnotationFinding          # target "association", tw_object_id = TW association


@dataclass(frozen=True)
class AssociationCompareResult:
    checked_specimens: int
    confirmed_count: int
    to_add: tuple[AssociationRow, ...]
    only_on_tw: tuple[AssociationRow, ...]
    host_differs: tuple[AssociationRow, ...]
    cant_compare: tuple[AssociationRow, ...]
    annotation_findings: tuple[AssociationAnnotationFinding, ...]
    unverified_terms: tuple[str, ...]
    not_on_tw_count: int
    numberless_count: int
    taxon_subject_count: int
    ambiguous_catalog_numbers: tuple[str, ...]


# ── Local side ───────────────────────────────────────────────────────────────────

def _current_taxon(co: CollectionObject) -> Taxon | None:
    # Same one-line `is_current == 1` scan tw_compare / tw_sync each keep privately.
    for det in co.determinations:
        if det.is_current == 1:
            return det.taxon
    return None


def _display(session: Session, t: Taxon) -> str:
    return " ".join(x for x in (taxa.compose_scientific_name(session, t),
                                (t.scientific_name_authorship or "").strip()) if x)


@dataclass(frozen=True)
class _Local:
    a: BiologicalAssociation
    anchor_co_id: int
    anchor_is_subject: bool
    taxon: Taxon | None
    host: str
    host_kind: str
    notes: tuple[str, ...]


def _local_entry(session: Session, a: BiologicalAssociation, anchor_co_id: int,
                 anchor_is_subject: bool) -> _Local:
    notes: tuple[str, ...] = ()
    if anchor_is_subject and a.object_taxon is not None:
        t = a.object_taxon
        return _Local(a, anchor_co_id, True, t, _display(session, t), "taxon", notes)
    if anchor_is_subject and a.object_field_occurrence is not None:
        det = fo_svc.current_determination(session, a.object_field_occurrence)
        t = det.taxon if det is not None else None
        if det is not None and det.identification_qualifier:
            notes = (f"identified with qualifier '{det.identification_qualifier}'",)
        return _Local(a, anchor_co_id, True, t,
                      _display(session, t) if t else "no current identification",
                      "field occurrence", notes)
    co = a.object_collection_object if anchor_is_subject else a.subject_collection_object
    t = _current_taxon(co)
    label = f"specimen {co.catalog_number or '(no catalog number)'}"
    return _Local(a, anchor_co_id, anchor_is_subject, t,
                  f"{label} ({_display(session, t)})" if t else label, "specimen", notes)


def _verified(rel, host: str) -> bool:
    return rel.tw_id is not None and rel.tw_host == host


# ── TaxonWorks side ──────────────────────────────────────────────────────────────

def _tw_anchor_is_subject(t: TwAssociation, tw_co_ids: set[int]) -> bool:
    """The anchor is the subject when the subject is one of our specimens, else the
    object (fetch_associations verified one of them is)."""
    return t.subject.type == "CollectionObject" and t.subject.id in tw_co_ids


def _other(t: TwAssociation, anchor_is_subject: bool) -> TwSide:
    return t.object if anchor_is_subject else t.subject


# ── Annotation display lines ─────────────────────────────────────────────────────

def _local_annotation_lines(anns: list, host: str) -> tuple[str, ...]:
    out = []
    for a in anns:
        t = a.term
        noun = "tag" if t.kind == "tag" else "data attribute"
        if t.tw_id is not None and t.tw_host == host:
            name, state = t.tw_name, ""
        elif t.tw_id is None:
            name, state = t.name, " (local only — never sent)"
        else:
            name, state = t.name, " (link not verified here)"
        out.append(f"{noun} {name}" + (f" = {a.value}" if a.value else "") + state)
    return tuple(out)


def _tw_annotation_lines(tags: list, data: list) -> tuple[str, ...]:
    return tuple([f"tag {t.name}" for t in tags]
                 + [f"data attribute {d.name} = {d.value}" for d in data])


# ── The comparison ───────────────────────────────────────────────────────────────

_DEMOTED = "another association on this specimen could not be compared"


def compare_associations(session: Session, *, repository_id: int,
                         specimens: list[tuple[str, int, int]], ambiguous: tuple[str, ...],
                         tw_assocs: dict[int, TwAssociation],
                         names: dict[int, TwName | None],
                         tw_tags: dict, tw_data: dict, host: str) -> AssociationCompareResult:
    """Pure computation, no I/O (spec "What is compared"). ``specimens`` is
    ``(catalog_number, local co id, TW co id)`` from ``tw_media_compare.specimens_on_tw``."""
    on_tw = {co_id: (cat, tw_id) for cat, co_id, tw_id in specimens}
    tw_co_ids = {tw_id for _cat, _co, tw_id in specimens}
    tw_to_co = {tw_id: co_id for _cat, co_id, tw_id in specimens}
    ambiguous_set = set(ambiguous)
    repo_ids = select(CollectionObject.id).where(CollectionObject.repository_id == repository_id)

    terms = ann.list_terms(session)
    verified_terms = {t.id: t for t in terms if t.tw_id is not None and t.tw_host == host}
    unverified_terms = tuple(sorted({t.name for t in terms
                                     if t.tw_id is not None and t.tw_host != host}))

    # ── local associations touching this collection (sub-selects: no IN-list limit)
    local_rows = (session.query(BiologicalAssociation)
                  .filter((BiologicalAssociation.subject_collection_object_id.in_(repo_ids))
                          | (BiologicalAssociation.object_collection_object_id.in_(repo_ids))
                          | ((BiologicalAssociation.subject_taxon_id.isnot(None))
                             & BiologicalAssociation.object_collection_object_id.is_(None)))
                  .order_by(BiologicalAssociation.id).all())
    not_on_tw = numberless = taxon_subject = 0
    locals_by_anchor: dict[int, list[_Local]] = defaultdict(list)
    for a in local_rows:
        if a.subject_taxon_id is not None:
            taxon_subject += 1
            continue
        s, o = a.subject_collection_object_id, a.object_collection_object_id
        if s in on_tw:
            locals_by_anchor[s].append(_local_entry(session, a, s, True))
        elif o is not None and o in on_tw:
            locals_by_anchor[o].append(_local_entry(session, a, o, False))
        else:
            side = a.subject_collection_object if a.subject_collection_object.repository_id \
                == repository_id else a.object_collection_object
            if side.catalog_number is None:
                numberless += 1
            elif side.catalog_number not in ambiguous_set:
                not_on_tw += 1

    tw_by_anchor: dict[int, list[TwAssociation]] = defaultdict(list)
    for t in sorted(tw_assocs.values(), key=lambda x: x.id):
        anchor_subject = _tw_anchor_is_subject(t, tw_co_ids)
        anchor_tw = t.subject.id if anchor_subject else t.object.id
        tw_by_anchor[tw_to_co[anchor_tw]].append(t)

    # local annotations of every association involved, batch-loaded, ordered like
    # annotations_for (tags first, then by name)
    local_ids = sorted({e.a.id for es in locals_by_anchor.values() for e in es})
    anns_by_ba: dict[int, list] = defaultdict(list)
    if local_ids:
        for an in (session.query(Annotation)
                   .join(AnnotationTerm, Annotation.term_id == AnnotationTerm.id)
                   .filter(Annotation.biological_association_id.in_(local_ids))
                   .order_by(AnnotationTerm.kind.desc(), func.lower(AnnotationTerm.name))):
            anns_by_ba[an.biological_association_id].append(an)

    confirmed = 0
    to_add: list[AssociationRow] = []
    only: list[AssociationRow] = []
    differs: list[AssociationRow] = []
    cant: list[AssociationRow] = []
    ann_findings: list[AssociationAnnotationFinding] = []

    for co_id in sorted(set(locals_by_anchor) | set(tw_by_anchor),
                        key=lambda c: on_tw[c][0]):
        cat, tw_specimen = on_tw[co_id]

        def local_row(e: _Local, **kw) -> AssociationRow:
            return AssociationRow(cat, tw_specimen, e.a.biological_relationship.name, e.host,
                                  e.host_kind, local_id=e.a.id, notes=e.notes,
                                  annotations=_local_annotation_lines(
                                      anns_by_ba.get(e.a.id, []), host), **kw)

        def tw_row(t: TwAssociation, anchor_subject: bool, **kw) -> AssociationRow:
            other = _other(t, anchor_subject)
            return AssociationRow(cat, tw_specimen, t.relationship_name, other.label,
                                  other.type, tw_id=t.id,
                                  annotations=_tw_annotation_lines(
                                      tw_tags.get(t.id, []), tw_data.get(t.id, [])), **kw)

        # comparable vs can't-compare, both sides
        ls, ts = [], []
        blocked = False
        for e in locals_by_anchor.get(co_id, []):
            rel = e.a.biological_relationship
            if not _verified(rel, host):
                why = (f"relationship '{rel.name}' is not linked to TaxonWorks"
                       if rel.tw_id is None else
                       f"relationship '{rel.name}' is linked but not verified on {host}")
                cant.append(local_row(e, reason=why))
                blocked = True
            elif e.taxon is None:
                cant.append(local_row(e, reason="the other side has no current identification"))
                blocked = True
            else:
                ls.append(e)
        for t in tw_by_anchor.get(co_id, []):
            anchor_subject = _tw_anchor_is_subject(t, tw_co_ids)
            other = _other(t, anchor_subject)
            if other.otu_id is None:
                cant.append(tw_row(t, anchor_subject,
                                   reason="TaxonWorks gives no OTU for the other side"))
                blocked = True
            elif names[other.otu_id] is None:
                cant.append(tw_row(t, anchor_subject,
                                   reason=f"TaxonWorks OTU {other.otu_id} has no taxon name"))
                blocked = True
            else:
                ts.append((t, anchor_subject, names[other.otu_id]))

        # pair: exact names first, then subgenus-only; ascending ids within a tier
        paired_l: set[int] = set()
        paired_t: set[int] = set()
        for tier in ("match", "subgenus"):
            for e in ls:
                if e.a.id in paired_l:
                    continue
                name = taxa.compose_scientific_name(session, e.taxon)
                for t, anchor_subject, tw_name in ts:
                    if t.id in paired_t or anchor_subject != e.anchor_is_subject:
                        continue
                    if t.relationship_id != e.a.biological_relationship.tw_id:
                        continue
                    verdict, _notes = host_verdict(name, e.taxon.scientific_name_authorship,
                                                   tw_name)
                    if verdict != tier:
                        continue
                    paired_l.add(e.a.id)
                    paired_t.add(t.id)
                    confirmed += 1
                    before = []
                    ann_cmp._diff(before, cat, t.id, "association", anns_by_ba.get(e.a.id, []),
                                  tw_tags.get(t.id, []), tw_data.get(t.id, []), verified_terms)
                    label = f"{e.a.biological_relationship.name} → {e.host}"
                    ann_findings.extend(AssociationAnnotationFinding(cat, tw_specimen, label, f)
                                        for f in before)
                    break

        left_l = [e for e in ls if e.a.id not in paired_l]
        left_t = [x for x in ts if x[0].id not in paired_t]
        if blocked:
            cant.extend(local_row(e, reason=_DEMOTED) for e in left_l)
            cant.extend(tw_row(t, s, reason=_DEMOTED) for t, s, _n in left_t)
            continue
        groups: dict[tuple[bool, int], tuple[list, list]] = defaultdict(lambda: ([], []))
        for e in left_l:
            groups[(e.anchor_is_subject, e.a.biological_relationship.tw_id)][0].append(e)
        for x in left_t:
            groups[(x[1], x[0].relationship_id)][1].append(x)
        for (_s, _rel), (gl, gt) in groups.items():
            if len(gl) == 1 and len(gt) == 1:
                e, (t, anchor_subject, tw_name) = gl[0], gt[0]
                _v, why = host_verdict(taxa.compose_scientific_name(session, e.taxon),
                                       e.taxon.scientific_name_authorship, tw_name)
                differs.append(local_row(e, tw_id=t.id,
                                         tw_host=_other(t, anchor_subject).label,
                                         reason="; ".join(why)))
                continue
            to_add.extend(local_row(e) for e in gl)
            only.extend(tw_row(t, s) for t, s, _n in gt)

    return AssociationCompareResult(
        checked_specimens=len(specimens), confirmed_count=confirmed,
        to_add=tuple(to_add), only_on_tw=tuple(only), host_differs=tuple(differs),
        cant_compare=tuple(cant), annotation_findings=tuple(ann_findings),
        unverified_terms=unverified_terms, not_on_tw_count=not_on_tw,
        numberless_count=numberless, taxon_subject_count=taxon_subject,
        ambiguous_catalog_numbers=tuple(ambiguous))


async def run_association_compare(session: Session, *, repository_id: int,
                                  index: CatalogIndex,
                                  host: str | None = None) -> AssociationCompareResult:
    """I/O + computation — the entry point the TaxonWorks tab calls."""
    host = tw.configured_host() if host is None else host
    specimens, ambiguous = tw_media_compare.specimens_on_tw(
        session, repository_id=repository_id, index=index)
    tw_co_ids = sorted({tw_id for _cat, _co, tw_id in specimens})
    tw_assocs = await fetch_associations(tw_co_ids)
    co_set = set(tw_co_ids)
    otu_ids = sorted({_other(t, _tw_anchor_is_subject(t, co_set)).otu_id
                      for t in tw_assocs.values()} - {None})
    ba_ids = sorted(tw_assocs)
    names, tags, data = await _gather(
        fetch_otu_names(otu_ids),
        ann_cmp.fetch_tags("BiologicalAssociation", ba_ids),
        ann_cmp.fetch_data_attributes("BiologicalAssociation", ba_ids))
    return compare_associations(session, repository_id=repository_id, specimens=specimens,
                                ambiguous=ambiguous, tw_assocs=tw_assocs, names=names,
                                tw_tags=tags, tw_data=data, host=host)

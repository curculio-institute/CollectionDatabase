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

from dataclasses import dataclass

import httpx

import app.services.taxonworks as tw
from app.services import taxa
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_annotation_compare import _batches, _foreign, _gather

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

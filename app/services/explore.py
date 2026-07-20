"""Explore service — dataset querying for the reworked Records tab (#40).

Two browse axes over one filtered set:
  * by taxon  — a drawer-order checklist (family → genus → species) with the
                material (specimen lots) under each species, mirroring the user's
                Käfersammlung spreadsheet;
  * by event  — collecting events, each expandable to the specimens collected there.

Both are driven by one faceted search bar: `search_facets` returns suggestions
tagged by source (taxon / country / state / region / county / island / collector);
the chosen facets become AND-combined filters consumed by every query here.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, and_, or_, false, exists
from sqlalchemy.orm import Session, joinedload, aliased

from app.models import (
    CollectionObject, CollectingEvent, TaxonDetermination, Taxon, Person,
    Country, StateProvince, County, Island, AdministrativeRegion, Repository,
)
from app.services.taxa import format_scientific_name, parse_scientific_name
from app.services.label_text import format_locality_label, format_place
from app.services.biological import association_host as _assoc_host

# Geography facet kind → (vocab model, collecting_event FK attr).
_GEO_FACETS = {
    "country":               (Country, "country_id"),
    "state_province":        (StateProvince, "state_province_id"),
    "administrative_region": (AdministrativeRegion, "administrative_region_id"),
    "county":                (County, "county_id"),
    "island":                (Island, "island_id"),
}
_GEO_LABEL = {
    "country": "Country", "state_province": "State/province",
    "administrative_region": "Region", "county": "County", "island": "Island",
}

# Ranks shown as their own stacked headers above the species rows (like a published
# catalogue: superfamily / family / subfamily / tribe / subtribe / genus / subgenus).
# Subgenus IS its own header level; the species below it then show the bare epithet.
_HEADER_RANKS = ("superfamily", "family", "subfamily", "tribe", "subtribe",
                 "genus", "subgenus")


def _header_label(t: Taxon) -> tuple[str, str]:
    """(name, authorship) for a rank header — its own epithet (not the composed path).
    Subgenus → just the subgenus name, not 'Genus (Subgenus)'. Author kept separate so
    the UI can mute it without guessing where the name ends."""
    if t.taxon_rank == "subgenus":
        _g, sg, _sp, _i = parse_scientific_name(t.scientific_name or "")
        name = sg or t.name_element or (t.scientific_name or "")
    else:
        name = t.scientific_name or t.name_element or ""
    return name, (t.scientific_name_authorship or "")


def _species_epithet(t: Taxon) -> tuple[str, str]:
    """(name, authorship) for a species row: **Genus species** — the genus is printed
    before the epithet for readability (#139); the SUBGENUS is dropped (it's a header in
    the checklist). Falls back to the full name for genus-or-higher determinations."""
    g, _sg, sp, infra = parse_scientific_name(t.scientific_name or "")
    base = " ".join(p for p in (g, sp, infra) if p)
    if not base:
        return format_scientific_name(t), ""
    return base, (t.scientific_name_authorship or "")


# ── facet search ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Facet:
    kind: str          # taxon | country | state_province | … | collector
    label: str         # display text (the value)
    key: object        # taxon_id / vocab_id / person full_name
    tag: str           # category tag shown in the dropdown, e.g. "Genus", "Country"


def search_facets(session: Session, term: str, limit: int = 25) -> list[Facet]:
    """Suggestions across taxa + geography vocabs + collectors for the search bar.
    Each multi-token term must match (so 'ber bav' matches 'Bavaria … Bergen')."""
    term = (term or "").strip()
    if not term:
        return []
    toks = term.split()
    out: list[Facet] = []

    # Taxa (accepted + synonyms) — tag with the rank.
    tq = session.query(Taxon)
    for tok in toks:
        tq = tq.filter(Taxon.scientific_name.ilike(f"%{tok}%"))
    for t in tq.order_by(Taxon.scientific_name).limit(limit).all():
        out.append(Facet("taxon", format_scientific_name(t), t.id,
                         (t.taxon_rank or "taxon").capitalize()))

    # Geography vocabs.
    for kind, (model, _attr) in _GEO_FACETS.items():
        gq = session.query(model)
        for tok in toks:
            gq = gq.filter(model.name.ilike(f"%{tok}%"))
        for v in gq.order_by(model.name).limit(limit).all():
            out.append(Facet(kind, v.name, v.id, _GEO_LABEL[kind]))

    # People, in BOTH roles (#135): a person can be filtered as the Collector
    # (recordedBy on the event) or as who "identified by" (identifiedBy on the
    # determination). The identifier role is deliberately labelled "identified by",
    # not "Identifier" — the latter collides with the specimen's catalog identifier.
    pq = session.query(Person)
    for tok in toks:
        pq = pq.filter(Person.full_name.ilike(f"%{tok}%"))
    for p in pq.order_by(Person.full_name).limit(limit).all():
        out.append(Facet("collector", p.full_name, p.full_name, "Collector"))
        out.append(Facet("identified_by", p.full_name, p.full_name, "identified by"))

    # Collections (repositories) — match on the code or either full name, so typing
    # "JJPC" brings up the collection (#135). Label shows the code + full name.
    rq = session.query(Repository)
    for tok in toks:
        rq = rq.filter(
            Repository.collection_code.ilike(f"%{tok}%")
            | Repository.collection_full_name.ilike(f"%{tok}%")
            | Repository.institution_code.ilike(f"%{tok}%")
            | Repository.institution_full_name.ilike(f"%{tok}%"))
    for r in rq.order_by(Repository.collection_code).limit(limit).all():
        label = (f"{r.collection_code} — {r.collection_full_name}"
                 if r.collection_full_name else r.collection_code)
        out.append(Facet("collection", label, r.id, "Collection"))

    return out


# ── taxon subtree ─────────────────────────────────────────────────────────────

def _taxon_index(session: Session) -> dict[int, Taxon]:
    return {t.id: t for t in session.query(Taxon).all()}


def _descendant_ids(taxon_id: int, children: dict[int, list[int]]) -> set[int]:
    out, stack = set(), [taxon_id]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, ()))
    return out


# ── specimen query ────────────────────────────────────────────────────────────

@dataclass
class SpecimenRow:
    co_id: int
    catalog: str
    collection_code: str
    taxon_id: int | None
    taxon_label: str           # composed name WITH authorship (legacy plain-text callers)
    # The parts a renderer needs to italicise correctly (only the genus group and below) and to
    # keep the authorship roman — see taxa.scientific_name_html.
    taxon_name: str = ""       # composed name WITHOUT authorship
    taxon_rank: str | None = None
    authorship: str | None = None
    # Biological associations (the host plant, usually) as (relationship, name, rank) —
    # "collected from Quercus robur". The RELATIONSHIP is what the association means; a plant
    # name beside a beetle says nothing about how they met.
    hosts: list[tuple[str, str, str | None]] = field(default_factory=list)
    # Withheld from public export: the specimen's own flag, or INHERITED from a confidential
    # event (which drops all of its specimens). Shown as one amber padlock; the reason is the
    # tooltip. See CLAUDE.md "Confidential / privacy flag".
    confidential: bool = False
    event_confidential: bool = False
    needs_attention: bool = False   # not determined to species (indet.)
    sex: str | None = None
    count: int = 1
    type_status: str | None = None
    event_id: int | None = None
    locality: str = ""         # composed one-line label (incl. associations, date, collector)
    locality_place: str = ""   # place only (locality + country) — for the specimen summary
    event_date: str | None = None
    date_identified: str | None = None   # dwc:dateIdentified of the current determination
    identified_by: str | None = None     # the determiner (person) of the current determination
    recorded_by: str | None = None
    lat: float | None = None
    lon: float | None = None
    # The composed data-label content (locality + date + collector + associated
    # species). Specimens with the same content are the same "data" and collapse into
    # one checklist row by default — the print-queue "identical labels" notion (#37),
    # by CONTENT not by event-row id (duplicate event rows still collapse).
    data_key: str = ""


def _as_groups(filters: list[dict] | None, combine: str) -> list[dict]:
    """Normalise the two accepted ``filters`` shapes to a list of groups (#135):

    * **Grouped** — ``[{"op": "and"|"or", "facets": [facet, …]}, …]`` (the UI's stacked
      searches). Passed through unchanged.
    * **Flat** — ``[facet, …]`` (a single implicit group). Wrapped into one group whose
      op is ``combine``. This is what the tests and any legacy caller pass.

    A facet is ``{"kind", "key", …}``.
    """
    filters = filters or []
    if filters and isinstance(filters[0], dict) and "facets" in filters[0]:
        return filters
    return [{"op": combine, "facets": list(filters)}]


def _apply_filters(session: Session, q, filters: list[dict], idx: dict[int, Taxon],
                   *, combine: str = "and", id_scope=("current",)):
    """Apply facet filters to a query over (CollectionObject join det/event).

    Filters are a list of **groups**; each facet is its own clause. Within a group the
    facets combine by the group's ``op`` (AND/OR); the groups themselves combine by AND
    — the stacked-search model (#135). A flat facet list is treated as one group whose
    op is ``combine`` (see ``_as_groups``).

    * within-group ``"and"`` — a specimen must match every facet. Two families →
      ``taxon_id in Carabidae`` AND ``taxon_id in Curculionidae`` → **0** (a
      determination has one taxon, so disjoint subtrees can't both hold).
    * within-group ``"or"``  — a specimen matching *any* facet: all Carabidae *plus*
      all Curculionidae.
    * across groups (always AND) — ``(Carabidae OR Curculionidae) AND (Jakob AND JJPC)``.

    (Historically same-kind facets were hard-wired to OR — "a specimen has one
    country" (#66) — but with an explicit AND/OR toggle the toggle must govern the
    same-kind case too, or "Carabidae AND Curculionidae" wrongly returns the union.)
    """
    children: dict[int, list[int]] = defaultdict(list)
    for t in idx.values():
        if t.parent_name_usage_id:
            children[t.parent_name_usage_id].append(t.id)

    def _clause(f):
        kind = f["kind"]
        key = f.get("key")
        if kind == "taxon":
            ids = _descendant_ids(int(key), children)
            # Which determinations the taxon filter searches (#137): the CURRENT one
            # (default), ANY past determination, and/or the frozen verbatim text.
            conds = []
            if "current" in id_scope:                 # the joined current determination
                conds.append(TaxonDetermination.taxon_id.in_(ids))
            if "past" in id_scope:                    # any determination, not just current
                td = aliased(TaxonDetermination)
                conds.append(exists().where(and_(
                    td.collection_object_id == CollectionObject.id, td.taxon_id.in_(ids))))
            if "verbatim" in id_scope:                # the frozen verbatimIdentification text
                t = idx.get(int(key))
                name = t.scientific_name if t else None
                if name:
                    tdv = aliased(TaxonDetermination)
                    conds.append(exists().where(and_(
                        tdv.collection_object_id == CollectionObject.id,
                        tdv.verbatim_identification.ilike(f"%{name}%"))))
            return or_(*conds) if conds else false()
        if kind in _GEO_FACETS:
            _model, attr = _GEO_FACETS[kind]
            return getattr(CollectingEvent, attr) == int(key)
        if kind == "collector":
            return Person.full_name == key
        if kind == "identified_by":
            # key is a person full_name (UNIQUE); resolve to the id and match the
            # determination's identifiedBy. A name that resolves to nothing is an
            # impossible clause, never "IS NULL" (which would match un-identified rows).
            pid = session.query(Person.id).filter(Person.full_name == key).scalar()
            return TaxonDetermination.identified_by_id == pid if pid is not None else false()
        if kind == "collection":
            return CollectionObject.repository_id == int(key)
        if kind == "disposition":
            col = CollectionObject.disposition_id
            # exclude also keeps specimens with NO disposition (they aren't "loaned" either).
            return or_(col.is_(None), col != int(key)) if f.get("exclude") else col == int(key)
        if kind == "date":
            # A date range on the collecting date (event_date) or the identification date.
            # ISO date strings sort lexicographically, so >= from / <= to work directly (the
            # eventDate interval "start/end" sorts by its start — "collected on/after"). A
            # blank bound is open-ended.
            col = (CollectingEvent.event_date if f.get("field") == "collected"
                   else TaxonDetermination.date_identified)
            conds = []
            if f.get("from"):
                conds.append(col >= f["from"])
            if f.get("to"):
                conds.append(col <= f["to"])
            return and_(*conds) if conds else None
        return None

    def _neg_clause(f):
        """NULL-safe negation of a facet, for a NOT group (#140). A specimen matches iff it
        does NOT positively match — a record MISSING the field counts as "not it" (no
        collector *is* "not collected by Jakob"; an undetermined specimen *is* "not
        Carabidae"). SQLite ``IS NOT`` (``is_distinct_from``) delivers that for the plain
        column facets; the EXISTS-based taxon/verbatim clauses negate NULL-safely via
        ``NOT EXISTS``."""
        kind = f["kind"]
        key = f.get("key")
        if kind == "taxon":
            ids = _descendant_ids(int(key), children)
            negs = []                              # NOT(current OR past OR verbatim)
            if "current" in id_scope:
                col = TaxonDetermination.taxon_id
                negs.append(or_(col.is_(None), col.notin_(ids)))
            if "past" in id_scope:
                td = aliased(TaxonDetermination)
                negs.append(~exists().where(and_(
                    td.collection_object_id == CollectionObject.id, td.taxon_id.in_(ids))))
            if "verbatim" in id_scope:
                t = idx.get(int(key))
                name = t.scientific_name if t else None
                if name:
                    tdv = aliased(TaxonDetermination)
                    negs.append(~exists().where(and_(
                        tdv.collection_object_id == CollectionObject.id,
                        tdv.verbatim_identification.ilike(f"%{name}%"))))
            return and_(*negs) if negs else None
        if kind in _GEO_FACETS:
            _model, attr = _GEO_FACETS[kind]
            return getattr(CollectingEvent, attr).is_distinct_from(int(key))
        if kind == "collector":
            return Person.full_name.is_distinct_from(key)
        if kind == "identified_by":
            pid = session.query(Person.id).filter(Person.full_name == key).scalar()
            # negation of an impossible clause (unresolvable name) is "everything" → no restriction
            return (TaxonDetermination.identified_by_id.is_distinct_from(pid)
                    if pid is not None else None)
        if kind == "collection":
            return CollectionObject.repository_id.is_distinct_from(int(key))
        if kind == "disposition":
            return CollectionObject.disposition_id.is_distinct_from(int(key))
        if kind == "date":
            col = (CollectingEvent.event_date if f.get("field") == "collected"
                   else TaxonDetermination.date_identified)
            conds = []                             # outside the range …
            if f.get("from"):
                conds.append(col < f["from"])
            if f.get("to"):
                conds.append(col > f["to"])
            return or_(col.is_(None), *conds) if conds else None   # … or no date at all
        return None

    group_clauses = []
    for g in _as_groups(filters, combine):
        op = g.get("op", "and")
        facets = g.get("facets", [])
        if op == "not":                            # match NONE of the group's facets
            negs = [c for f in facets if (c := _neg_clause(f)) is not None]
            if negs:
                group_clauses.append(and_(*negs))  # NOT A AND NOT B  (= NOT(A OR B))
            continue
        facet_clauses = [c for f in facets if (c := _clause(f)) is not None]
        if not facet_clauses:
            continue
        group_clauses.append(or_(*facet_clauses) if op == "or"
                             else and_(*facet_clauses))
    if not group_clauses:
        return q
    return q.filter(and_(*group_clauses))


def query_specimens(session: Session, filters: list[dict] | None = None,
                    *, combine: str = "and", id_scope=("current",)) -> list[SpecimenRow]:
    """Specimens matching the facet filters, with display fields for list/map/CSV.
    ``combine`` ("and"/"or") is how facets of different kinds combine (#135); ``id_scope``
    is which determinations the taxon filter searches (current / past / verbatim, #137).
    The DISPLAYED determination is always the current one regardless of scope."""
    filters = filters or []
    idx = _taxon_index(session)
    q = (
        session.query(CollectionObject, TaxonDetermination, CollectingEvent)
        .options(joinedload(CollectionObject.repository))
        .outerjoin(TaxonDetermination,
                   (TaxonDetermination.collection_object_id == CollectionObject.id)
                   & (TaxonDetermination.is_current == 1))
        .outerjoin(CollectingEvent, CollectingEvent.id == CollectionObject.collecting_event_id)
        .outerjoin(Person, Person.id == CollectingEvent.recorded_by_id)
    )
    q = _apply_filters(session, q, filters, idx, combine=combine, id_scope=id_scope)
    # Identification-scope requirement (#137): with 'current' unselected, restrict to records
    # that actually HAVE an identification of the chosen kind(s) — "only past" → only
    # re-identified records. 'current' is permissive (imposes nothing), so the default still
    # shows every specimen, undetermined ones included.
    if "current" not in id_scope:
        reqs = []
        if "past" in id_scope:
            tdp = aliased(TaxonDetermination)
            reqs.append(exists().where(and_(
                tdp.collection_object_id == CollectionObject.id, tdp.is_current == 0)))
        if "verbatim" in id_scope:
            tdv = aliased(TaxonDetermination)
            reqs.append(exists().where(and_(
                tdv.collection_object_id == CollectionObject.id,
                tdv.verbatim_identification.isnot(None))))
        if reqs:
            q = q.filter(or_(*reqs))
    q = q.order_by(CollectionObject.id.desc())

    rows: list[SpecimenRow] = []
    seen: set[int] = set()
    for co, td, ev in q.all():
        # A specimen should have one current determination, but is_current
        # uniqueness isn't DB-enforced — guard against a stray second current row
        # joining the specimen twice (double-counting it across two taxa) (#66).
        if co.id in seen:
            continue
        seen.add(co.id)
        t = idx.get(td.taxon_id) if td else None
        rank = (t.taxon_rank if t else None)
        # Biological associations of this specimen (subject role). `hosts` (relationship +
        # host taxon) drives the summary's "collected from …"; `assoc_names` feeds the
        # composed locality label + collapse key. A host may be a taxon or a field
        # occurrence — _assoc_host resolves both; an association to another *specimen*
        # contributes its catalog id, and one that resolves to nothing is skipped (never
        # the old "specimen #None").
        assocs = co.subject_associations
        hosts = [h for a in assocs if (h := _assoc_host(session, a))]
        assoc_names = [h[1] for h in hosts] + [
            f"specimen #{a.object_collection_object_id}"
            for a in assocs if a.object_collection_object_id]
        loc = format_locality_label(ev, assoc_names or None) if ev else ""
        # A place-only locality for the specimen summary — the composed `loc` above bundles
        # associations/coords/habitat/date/collector, which the summary already shows
        # separately, so reusing it would duplicate them (and print "specimen #None").
        place = format_place(ev) if ev else ""
        rows.append(SpecimenRow(
            co_id=co.id,
            catalog=co.catalog_number,
            collection_code=co.repository.collection_code,
            taxon_id=(t.id if t else None),
            taxon_label=(format_scientific_name(t) if t else "— undetermined —"),
            taxon_name=((t.scientific_name or "") if t else ""),
            taxon_rank=rank,
            authorship=((t.scientific_name_authorship or None) if t else None),
            hosts=hosts,
            confidential=bool(co.confidential),
            event_confidential=bool(ev.confidential) if ev else False,
            needs_attention=(t is None or rank not in ("species", "subspecies", "variety", "form")),
            sex=(td.sex if td else None),
            count=co.individual_count,
            type_status=(td.type_status if td else None),
            event_id=co.collecting_event_id,
            locality=loc,
            locality_place=place,
            event_date=(ev.event_date if ev else None),
            date_identified=(td.date_identified if td else None),
            identified_by=(td.identified_by_person.full_name
                           if (td and td.identified_by_person) else None),
            recorded_by=(ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None),
            lat=(ev.decimal_latitude if ev else None),
            lon=(ev.decimal_longitude if ev else None),
            data_key=(loc or f"co-{co.id}"),
        ))
    return rows


# ── drawer-order checklist (taxa axis) ────────────────────────────────────────

@dataclass
class LotGroup:
    """Specimens of one species sharing the same collecting event + biological
    associations — the same 'data'. Collapsed to one row by default; expand for the
    individual specimens."""
    count: int                 # number of specimens in the group
    locality: str              # shared one-line locality (incl. associated species)
    specimens: list[SpecimenRow] = field(default_factory=list)


@dataclass
class ChecklistSpecies:
    taxon_id: int | None
    label: str                 # composed species name (full)
    short_label: str           # bare epithet (genus/subgenus are headers)
    short_auth: str            # authorship (rendered muted, separate from the name)
    count: int                 # total specimens (matching)
    needs_attention: bool
    lot_groups: list[LotGroup] = field(default_factory=list)


@dataclass
class ChecklistGroup:
    """One (sub)genus block, under family/subfamily/tribe headers, in drawer order."""
    headers: list[tuple[str, str, str]]   # [(rank, name, authorship), …] family … (sub)genus
                                          # — stacked rank headers, printed only when changed
    species: list[ChecklistSpecies] = field(default_factory=list)


def _ancestor_chain(t: Taxon, idx: dict[int, Taxon]) -> list[Taxon]:
    """[root … t] following parent links (excludes kingdom scaffolding)."""
    chain, cur = [], t
    seen = set()
    while cur and cur.id not in seen:
        seen.add(cur.id)
        if cur.taxon_rank != "kingdom":
            chain.append(cur)
        cur = idx.get(cur.parent_name_usage_id) if cur.parent_name_usage_id else None
    return list(reversed(chain))


def _group_lots(lots: list[SpecimenRow]) -> list[LotGroup]:
    """Collapse lots sharing the same collecting event + biological associations."""
    by_key: dict[tuple, list[SpecimenRow]] = {}
    for l in lots:
        by_key.setdefault(l.data_key, []).append(l)
    groups = [LotGroup(count=len(v), locality=v[0].locality, specimens=v)
              for v in by_key.values()]
    groups.sort(key=lambda g: g.locality or "")
    return groups


def checklist(session: Session, filters: list[dict] | None = None,
              *, combine: str = "and", id_scope=("current",)) -> list[ChecklistGroup]:
    """Matching specimens grouped under their taxonomy in drawer order: a list of
    genus groups (each carrying the family→genus header path) with species rows +
    their lots. Mirrors the Käfersammlung layout."""
    rows = query_specimens(session, filters, combine=combine, id_scope=id_scope)
    idx = _taxon_index(session)

    # Group lots by the taxon they were determined as.
    by_taxon: dict[int | None, list[SpecimenRow]] = defaultdict(list)
    for r in rows:
        by_taxon[r.taxon_id].append(r)

    # For each determined taxon, find its species-or-higher node + ancestor chain.
    # Build a sort key from the ancestor scientific names so siblings sort A→Z and
    # the whole list follows the taxonomic (drawer) order.
    species_entries: list[tuple[tuple, list[str], ChecklistSpecies]] = []
    undetermined: list[SpecimenRow] = by_taxon.pop(None, [])
    for taxon_id, lots in by_taxon.items():
        t = idx.get(taxon_id)
        if t is None:
            undetermined.extend(lots)
            continue
        chain = _ancestor_chain(t, idx)
        # Drawer order: each ancestor sorts by its manual sort_order first (the
        # arranged taxonomic sequence at family-and-above), then alphabetically.
        names = [(c.sort_order if c.sort_order is not None else 10 ** 9,
                  c.scientific_name or "") for c in chain]
        # header path = the drawer-divider ranks above this taxon (family … genus);
        # subgenus is excluded so all of a genus's species stay in one group.
        # When the determination is at a header rank itself (genus/family/super…/),
        # that taxon becomes its OWN header and the row is an indet. placeholder under
        # it — so e.g. material determined only to 'Curculionoidea' sits UNDER the
        # superfamily header, not floating above it. Otherwise (species/subspecies)
        # the header path is the ancestors and the row is the bare epithet.
        header_taxa = [c for c in chain if c.taxon_rank in _HEADER_RANKS]
        headers = [(c.taxon_rank, *_header_label(c)) for c in header_taxa]
        full = format_scientific_name(t)
        if t.taxon_rank in _HEADER_RANKS:
            epithet = "sp." if t.taxon_rank in ("genus", "subgenus") else "indet."
            ep_auth = ""
        else:
            epithet, ep_auth = _species_epithet(t)
        sp = ChecklistSpecies(
            taxon_id=taxon_id, label=full, short_label=epithet, short_auth=ep_auth,
            count=len(lots), needs_attention=any(l.needs_attention for l in lots),
            lot_groups=_group_lots(lots),
        )
        species_entries.append((tuple(names), headers, sp))

    species_entries.sort(key=lambda e: e[0])

    # Fold consecutive species sharing the same header path into one group.
    groups: list[ChecklistGroup] = []
    for _key, headers, sp in species_entries:
        if groups and groups[-1].headers == headers:
            groups[-1].species.append(sp)
        else:
            groups.append(ChecklistGroup(headers=headers, species=[sp]))

    if undetermined:
        groups.append(ChecklistGroup(
            headers=[("genus", "— undetermined —", "")],
            species=[ChecklistSpecies(
                taxon_id=None, label="— undetermined —", short_label="(no determination)",
                short_auth="", count=len(undetermined), needs_attention=True,
                lot_groups=_group_lots(undetermined),
            )],
        ))
    return groups


# ── events axis ───────────────────────────────────────────────────────────────

@dataclass
class EventGroup:
    event_id: int
    summary: str
    n_specimens: int
    confidential: bool = False   # withheld from export — withholds ALL of its specimens
    lots: list[SpecimenRow] = field(default_factory=list)


def events(session: Session, filters: list[dict] | None = None,
           *, combine: str = "and", id_scope=("current",)) -> list[EventGroup]:
    """Collecting events whose specimens match the filters, each with its lots."""
    rows = query_specimens(session, filters, combine=combine, id_scope=id_scope)
    by_event: dict[int | None, list[SpecimenRow]] = defaultdict(list)
    for r in rows:
        by_event[r.event_id].append(r)
    out: list[EventGroup] = []
    for event_id, lots in by_event.items():
        if event_id is None:
            continue
        ev = session.get(CollectingEvent, event_id)
        out.append(EventGroup(
            event_id=event_id,
            summary=(format_locality_label(ev) if ev else f"Event #{event_id}") or f"Event #{event_id}",
            n_specimens=len(lots),
            confidential=bool(ev.confidential) if ev else False,
            lots=sorted(lots, key=lambda l: l.taxon_label),
        ))
    out.sort(key=lambda g: g.summary)
    return out


# ── CSV export ────────────────────────────────────────────────────────────────

def to_csv(rows: list[SpecimenRow]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "catalogNumber", "collectionCode", "scientificName", "sex",
                "individualCount", "typeStatus", "locality", "eventDate",
                "recordedBy", "decimalLatitude", "decimalLongitude"])
    for r in rows:
        w.writerow([r.co_id, r.catalog, r.collection_code, r.taxon_label, r.sex or "",
                    r.count, r.type_status or "", r.locality, r.event_date or "",
                    r.recorded_by or "", r.lat if r.lat is not None else "",
                    r.lon if r.lon is not None else ""])
    return buf.getvalue().encode("utf-8")


# A "species-group name" (zoology) is a species or subspecies. The headline taxon count is
# these only — a specimen determined merely to genus/subgenus is NOT a species-group name and
# must not inflate the figure (#135). Distinct species and distinct subspecies both count.
_SPECIES_GROUP_RANKS = frozenset({"species", "subspecies"})


def counts(session: Session, filters: list[dict] | None = None,
           *, combine: str = "and", id_scope=("current",)) -> dict:
    """Headline counts for the current filter set.

    ``species_group`` = distinct species/subspecies determinations (genus/subgenus-level
    determinations are excluded); ``georeferenced`` = specimens carrying coordinates."""
    rows = query_specimens(session, filters, combine=combine, id_scope=id_scope)
    return {
        "specimens": len(rows),
        "species_group": len({
            r.taxon_id for r in rows
            if r.taxon_id and (r.taxon_rank or "").lower() in _SPECIES_GROUP_RANKS}),
        "events": len({r.event_id for r in rows if r.event_id}),
        "georeferenced": sum(1 for r in rows if r.lat is not None and r.lon is not None),
    }


# ── dashboard (data visualisations, #135) ─────────────────────────────────────

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _iso_year(s: str | None) -> int | None:
    """Leading year of an ISO 8601 date or interval (`2024-06/…` → 2024). None if absent."""
    head = (s or "").split("/")[0].strip()
    return int(head[:4]) if len(head) >= 4 and head[:4].isdigit() else None


def _iso_month(s: str | None) -> int | None:
    """1–12 month of an ISO 8601 date, or None. Used for phenology (collecting season)."""
    head = (s or "").split("/")[0].strip()
    if len(head) >= 7 and head[4] == "-" and head[5:7].isdigit():
        m = int(head[5:7])
        return m if 1 <= m <= 12 else None
    return None


@dataclass
class Dashboard:
    """Aggregated series for the Explore dashboard over the current filter set.

    All counts are of specimen *records* (matching the headline `specimens` figure),
    not summed individualCount. Year series are gap-free over [min, max] so the line
    charts don't imply activity in years that simply have no bin."""
    total: int
    collected_by_year: list[tuple[int, int]]      # (year, #specimens) by collecting date
    identified_by_year: list[tuple[int, int]]     # (year, #specimens) by identification date
    accum_collected: list[tuple[int, int]]        # (year, cumulative distinct species) collecting
    accum_identified: list[tuple[int, int]]       # (year, cumulative distinct species) identifying
    phenology: list[int]                          # 12 months, #specimens by collecting month
    hosts: list[tuple[str, int]]                  # (host taxon, #specimens total), most-associated first
    # Per-host breakdown by biological relationship, for the stacked host bars: host name →
    # {relationship: #specimens}. `host_relationships` is the distinct relationship set
    # (most-frequent first) → one coloured, stacked series each.
    host_breakdown: dict[str, dict[str, int]]
    host_relationships: list[str]
    undated_collected: int                        # specimens with no parseable collecting year
    undated_identified: int                       # specimens with no parseable identification year


def _fill_years(counts_by_year: dict[int, int]) -> list[tuple[int, int]]:
    """Sorted (year, count), with every intervening year present at 0."""
    if not counts_by_year:
        return []
    lo, hi = min(counts_by_year), max(counts_by_year)
    return [(y, counts_by_year.get(y, 0)) for y in range(lo, hi + 1)]


def _accumulate(first_seen: dict[int, int]) -> list[tuple[int, int]]:
    """Cumulative distinct-species curve from {taxon_id: first-seen year}."""
    per_year: dict[int, int] = defaultdict(int)
    for y in first_seen.values():
        per_year[y] += 1
    out, run = [], 0
    for y in sorted(per_year):
        run += per_year[y]
        out.append((y, run))
    return out


def dashboard(session: Session, filters: list[dict] | None = None,
              *, combine: str = "and", id_scope=("current",), host_limit: int = 15) -> Dashboard:
    """Build the dashboard series for the specimens matching ``filters``."""
    rows = query_specimens(session, filters, combine=combine, id_scope=id_scope)
    coll_year: dict[int, int] = defaultdict(int)
    ident_year: dict[int, int] = defaultdict(int)
    phenology = [0] * 12
    first_coll: dict[int, int] = {}     # taxon_id → earliest collecting year
    first_ident: dict[int, int] = {}    # taxon_id → earliest identification year
    hosts: dict[str, int] = defaultdict(int)
    host_by_rel: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    undated_c = undated_i = 0

    for r in rows:
        cy = _iso_year(r.event_date)
        if cy is not None:
            coll_year[cy] += 1
        else:
            undated_c += 1
        iy = _iso_year(r.date_identified)
        if iy is not None:
            ident_year[iy] += 1
        else:
            undated_i += 1
        m = _iso_month(r.event_date)
        if m is not None:
            phenology[m - 1] += 1
        if r.taxon_id and (r.taxon_rank or "").lower() in _SPECIES_GROUP_RANKS:
            if cy is not None:
                first_coll[r.taxon_id] = min(cy, first_coll.get(r.taxon_id, cy))
            if iy is not None:
                first_ident[r.taxon_id] = min(iy, first_ident.get(r.taxon_id, iy))
        for rel, name, _rank in r.hosts:
            if name:
                hosts[name] += 1
                host_by_rel[name][rel or "association"] += 1

    top_hosts = sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))[:host_limit]
    top_names = [n for n, _ in top_hosts]
    # distinct relationships across the shown hosts, most-frequent first (stable colours).
    rel_totals: dict[str, int] = defaultdict(int)
    for n in top_names:
        for rel, c in host_by_rel[n].items():
            rel_totals[rel] += c
    host_relationships = [r for r, _ in
                          sorted(rel_totals.items(), key=lambda kv: (-kv[1], kv[0]))]
    return Dashboard(
        total=len(rows),
        collected_by_year=_fill_years(coll_year),
        identified_by_year=_fill_years(ident_year),
        accum_collected=_accumulate(first_coll),
        accum_identified=_accumulate(first_ident),
        phenology=phenology,
        hosts=top_hosts,
        host_breakdown={n: dict(host_by_rel[n]) for n in top_names},
        host_relationships=host_relationships,
        undated_collected=undated_c,
        undated_identified=undated_i,
    )

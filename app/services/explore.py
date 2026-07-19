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

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import (
    CollectionObject, CollectingEvent, TaxonDetermination, Taxon, Person,
    Country, StateProvince, County, Island, AdministrativeRegion, Repository,
)
from app.services.taxa import format_scientific_name, parse_scientific_name
from app.services.label_text import format_locality_label

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
    """(epithet, authorship) — the species without genus/subgenus (those are headers).
    Falls back to the full name for genus-or-higher determinations."""
    _g, _sg, sp, infra = parse_scientific_name(t.scientific_name or "")
    base = " ".join(p for p in (sp, infra) if p)
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

    # Collectors (recordedBy).
    pq = session.query(Person)
    for tok in toks:
        pq = pq.filter(Person.full_name.ilike(f"%{tok}%"))
    for p in pq.order_by(Person.full_name).limit(limit).all():
        out.append(Facet("collector", p.full_name, p.full_name, "Collector"))

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
    locality: str = ""         # one-line locality label (incl. associated species)
    event_date: str | None = None
    recorded_by: str | None = None
    lat: float | None = None
    lon: float | None = None
    # The composed data-label content (locality + date + collector + associated
    # species). Specimens with the same content are the same "data" and collapse into
    # one checklist row by default — the print-queue "identical labels" notion (#37),
    # by CONTENT not by event-row id (duplicate event rows still collapse).
    data_key: str = ""


def _apply_filters(session: Session, q, filters: list[dict], idx: dict[int, Taxon]):
    """Apply facet filters to a query over (CollectionObject join det/event).

    Facets of the **same kind** are OR-combined (pick two countries → specimens
    from *either*), facets of **different kinds** are AND-combined (country +
    collector → specimens matching both). A specimen has only one
    country/collector, so AND-combining same-kind facets could never match (#66).
    """
    children: dict[int, list[int]] = defaultdict(list)
    for t in idx.values():
        if t.parent_name_usage_id:
            children[t.parent_name_usage_id].append(t.id)
    by_kind: dict[str, list] = defaultdict(list)
    for f in filters:
        by_kind[f["kind"]].append(f["key"])
    for kind, keys in by_kind.items():
        if kind == "taxon":
            ids: list[int] = []
            for k in keys:
                ids.extend(_descendant_ids(int(k), children))
            q = q.filter(TaxonDetermination.taxon_id.in_(ids))
        elif kind in _GEO_FACETS:
            _model, attr = _GEO_FACETS[kind]
            q = q.filter(getattr(CollectingEvent, attr).in_([int(k) for k in keys]))
        elif kind == "collector":
            q = q.filter(Person.full_name.in_(keys))
        elif kind == "collection":
            q = q.filter(CollectionObject.repository_id.in_([int(k) for k in keys]))
    return q


def query_specimens(session: Session, filters: list[dict] | None = None) -> list[SpecimenRow]:
    """Specimens matching the facet filters, with display fields for list/map/CSV."""
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
    q = _apply_filters(session, q, filters, idx)
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
        # Biological associations of this specimen (subject role): identity for the
        # collapse key + the object names for the locality line.
        assocs = co.subject_associations
        assoc_taxa = [format_scientific_name(a.object_taxon) if a.object_taxon
                      else f"specimen #{a.object_collection_object_id}" for a in assocs]
        loc = format_locality_label(ev, assoc_taxa or None) if ev else ""
        rows.append(SpecimenRow(
            co_id=co.id,
            catalog=co.catalog_number,
            collection_code=co.repository.collection_code,
            taxon_id=(t.id if t else None),
            taxon_label=(format_scientific_name(t) if t else "— undetermined —"),
            taxon_name=((t.scientific_name or "") if t else ""),
            taxon_rank=rank,
            authorship=((t.scientific_name_authorship or None) if t else None),
            hosts=[(a.biological_relationship.name if a.biological_relationship else "",
                    a.object_taxon.scientific_name or "",
                    a.object_taxon.taxon_rank)
                   for a in assocs if a.object_taxon],
            confidential=bool(co.confidential),
            event_confidential=bool(ev.confidential) if ev else False,
            needs_attention=(t is None or rank not in ("species", "subspecies", "variety", "form")),
            sex=(td.sex if td else None),
            count=co.individual_count,
            type_status=(td.type_status if td else None),
            event_id=co.collecting_event_id,
            locality=loc,
            event_date=(ev.event_date if ev else None),
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


def checklist(session: Session, filters: list[dict] | None = None) -> list[ChecklistGroup]:
    """Matching specimens grouped under their taxonomy in drawer order: a list of
    genus groups (each carrying the family→genus header path) with species rows +
    their lots. Mirrors the Käfersammlung layout."""
    rows = query_specimens(session, filters)
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


def events(session: Session, filters: list[dict] | None = None) -> list[EventGroup]:
    """Collecting events whose specimens match the filters, each with its lots."""
    rows = query_specimens(session, filters)
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


def counts(session: Session, filters: list[dict] | None = None) -> dict:
    """Headline counts for the current filter set.

    ``species_group`` = distinct species/subspecies determinations (genus/subgenus-level
    determinations are excluded); ``georeferenced`` = specimens carrying coordinates."""
    rows = query_specimens(session, filters)
    return {
        "specimens": len(rows),
        "species_group": len({
            r.taxon_id for r in rows
            if r.taxon_id and (r.taxon_rank or "").lower() in _SPECIES_GROUP_RANKS}),
        "events": len({r.event_id for r in rows if r.event_id}),
        "georeferenced": sum(1 for r in rows if r.lat is not None and r.lon is not None),
    }

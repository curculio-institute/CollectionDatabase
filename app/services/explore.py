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
from sqlalchemy.orm import Session

from app.models import (
    CollectionObject, CollectingEvent, TaxonDetermination, Taxon, Person,
    Country, StateProvince, County, Island, AdministrativeRegion,
)
from app.services.taxa import format_scientific_name
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

# Ranks shown as the drawer-divider headers above the species rows (like the
# Käfersammlung sheet: family / subfamily / tribe / genus). Finer ranks (subgenus)
# are NOT headers — they appear inside the species name — so a genus stays one group.
_HEADER_RANKS = ("superfamily", "family", "subfamily", "tribe", "subtribe", "genus")


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
    taxon_label: str
    needs_attention: bool      # not determined to species (indet.)
    sex: str | None
    count: int
    type_status: str | None
    event_id: int | None
    locality: str              # one-line locality label
    event_date: str | None
    recorded_by: str | None
    lat: float | None
    lon: float | None


def _apply_filters(session: Session, q, filters: list[dict], idx: dict[int, Taxon]):
    """Apply AND facet filters to a query over (CollectionObject join det/event)."""
    children: dict[int, list[int]] = defaultdict(list)
    for t in idx.values():
        if t.parent_name_usage_id:
            children[t.parent_name_usage_id].append(t.id)
    for f in filters:
        kind = f["kind"]
        if kind == "taxon":
            ids = _descendant_ids(int(f["key"]), children)
            q = q.filter(TaxonDetermination.taxon_id.in_(ids))
        elif kind in _GEO_FACETS:
            _model, attr = _GEO_FACETS[kind]
            q = q.filter(getattr(CollectingEvent, attr) == int(f["key"]))
        elif kind == "collector":
            q = q.filter(Person.full_name == f["key"])
    return q


def query_specimens(session: Session, filters: list[dict] | None = None) -> list[SpecimenRow]:
    """Specimens matching the facet filters, with display fields for list/map/CSV."""
    filters = filters or []
    idx = _taxon_index(session)
    q = (
        session.query(CollectionObject, TaxonDetermination, CollectingEvent)
        .outerjoin(TaxonDetermination,
                   (TaxonDetermination.collection_object_id == CollectionObject.id)
                   & (TaxonDetermination.is_current == 1))
        .outerjoin(CollectingEvent, CollectingEvent.id == CollectionObject.collecting_event_id)
        .outerjoin(Person, Person.id == CollectingEvent.recorded_by_id)
    )
    q = _apply_filters(session, q, filters, idx)
    q = q.order_by(CollectionObject.id.desc())

    rows: list[SpecimenRow] = []
    for co, td, ev in q.all():
        t = idx.get(td.taxon_id) if td else None
        rank = (t.taxon_rank if t else None)
        rows.append(SpecimenRow(
            co_id=co.id,
            catalog=co.catalog_number,
            collection_code=co.collection_code,
            taxon_id=(t.id if t else None),
            taxon_label=(format_scientific_name(t) if t else "— undetermined —"),
            needs_attention=(t is None or rank not in ("species", "subspecies", "variety", "form")),
            sex=(td.sex if td else None),
            count=co.individual_count,
            type_status=(td.type_status if td else None),
            event_id=co.collecting_event_id,
            locality=(format_locality_label(ev) if ev else ""),
            event_date=(ev.event_date if ev else None),
            recorded_by=(ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None),
            lat=(ev.decimal_latitude if ev else None),
            lon=(ev.decimal_longitude if ev else None),
        ))
    return rows


# ── drawer-order checklist (taxa axis) ────────────────────────────────────────

@dataclass
class ChecklistSpecies:
    taxon_id: int | None
    label: str                 # composed species name
    count: int                 # number of lots (matching)
    needs_attention: bool
    lots: list[SpecimenRow] = field(default_factory=list)


@dataclass
class ChecklistGroup:
    """One genus block, under family/subfamily headers, in drawer order."""
    headers: list[str]         # ancestor labels shown above (family … genus)
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
        names = [c.scientific_name or "" for c in chain]
        # header path = the drawer-divider ranks above this taxon (family … genus);
        # subgenus is excluded so all of a genus's species stay in one group.
        headers = [format_scientific_name(c) for c in chain
                   if c.taxon_rank in _HEADER_RANKS and c.id != t.id]
        sp = ChecklistSpecies(
            taxon_id=taxon_id, label=format_scientific_name(t),
            count=len(lots), needs_attention=any(l.needs_attention for l in lots),
            lots=sorted(lots, key=lambda l: (l.locality or "", l.event_date or "")),
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
            headers=["— undetermined —"],
            species=[ChecklistSpecies(
                taxon_id=None, label="— undetermined —", count=len(undetermined),
                needs_attention=True,
                lots=sorted(undetermined, key=lambda l: (l.locality or "")),
            )],
        ))
    return groups


# ── events axis ───────────────────────────────────────────────────────────────

@dataclass
class EventGroup:
    event_id: int
    summary: str
    n_specimens: int
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


def counts(session: Session, filters: list[dict] | None = None) -> dict:
    """Headline counts for the current filter set."""
    rows = query_specimens(session, filters)
    return {
        "specimens": len(rows),
        "taxa": len({r.taxon_id for r in rows if r.taxon_id}),
        "events": len({r.event_id for r in rows if r.event_id}),
        "georeferenced": sum(1 for r in rows if r.lat is not None and r.lon is not None),
    }

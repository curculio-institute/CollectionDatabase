"""Taxonomy service: checklist tree + summary stats.

Tree structure mirrors a scientific paper checklist:
  FAMILY
    Subfamily
      Tribe
        Subtribe
          Genus
            Genus species Author, Year          [↗ TaxonPages]
              = Synonym species Author, Year

The tree is built by walking the dwc:parentNameUsageID parent-child links
stored in the taxon table (DwC parent-link model).  All accepted taxa are
loaded into memory; children are grouped by their parent taxon id.
Synonyms (accepted_name_usage_id IS NOT NULL) appear as leaf children of
their accepted name, prefixed with '='.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CollectionObject, Taxon, TaxonDetermination
from app.services.taxa import format_scientific_name
from app.services.taxonworks import taxonpages_url

# Ranks the user may manually re-order (the collection's taxonomic sequence):
# family and everything above it. Below family stays alphabetical (#40).
ORDERABLE_RANKS = frozenset({
    "kingdom", "phylum", "subphylum", "class", "subclass", "infraclass",
    "superorder", "order", "suborder", "infraorder", "series", "superfamily", "family",
})

_NO_ORDER = 10 ** 9   # sort key for an unset sort_order → after the arranged ones


def order_key(t: Taxon):
    """Sibling display key: manual sort_order first (the arranged taxonomic
    sequence), then alphabetical. Unset sort_order ⇒ pure alphabetical."""
    return (t.sort_order if t.sort_order is not None else _NO_ORDER,
            t.scientific_name or "")


def move_taxon(session: Session, taxon_id: int, direction: int) -> None:
    """Move a family-or-above taxon up (-1) / down (+1) among its siblings, by
    swapping manual sort_order values. The first move materialises the siblings'
    current (alphabetical) order into sort_order so subsequent moves are stable.
    No-op for ranks below family."""
    t = session.get(Taxon, taxon_id)
    if t is None or t.taxon_rank not in ORDERABLE_RANKS:
        return
    sibs = (
        session.query(Taxon)
        .filter(Taxon.parent_name_usage_id == t.parent_name_usage_id,
                Taxon.accepted_name_usage_id.is_(None))
        .all()
    )
    sibs.sort(key=order_key)
    if any(s.sort_order is None for s in sibs):       # materialise current order
        for i, s in enumerate(sibs):
            s.sort_order = i
    idx = next(i for i, s in enumerate(sibs) if s.id == t.id)
    j = idx + direction
    if 0 <= j < len(sibs):
        sibs[idx].sort_order, sibs[j].sort_order = sibs[j].sort_order, sibs[idx].sort_order
    session.flush()


@dataclass
class TaxonomyStats:
    total_accepted: int
    total_species: int
    total_specimens: int


def get_stats(session: Session) -> TaxonomyStats:
    accepted_base = session.query(func.count(Taxon.id)).filter(
        Taxon.accepted_name_usage_id.is_(None),
        Taxon.parent_name_usage_id.is_not(None),
    )
    return TaxonomyStats(
        total_accepted=accepted_base.scalar() or 0,
        total_species=accepted_base.filter(Taxon.taxon_rank == "species").scalar() or 0,
        total_specimens=session.query(func.count(CollectionObject.id)).scalar() or 0,
    )


# ---------------------------------------------------------------------------
# Checklist filter options
# ---------------------------------------------------------------------------

def checklist_options(session: Session) -> dict[str, str]:
    """Return {key: label} for the taxonomy filter select widget.

    Keys use format 'taxon:{id}' for all entries; the UI splits on ':' and
    routes to build_taxonomy_tree(filter_id=id).
    Labels: '{scientificName + authorship}  — {Rank}'.
    """
    taxa = (
        session.query(Taxon)
        .filter(Taxon.accepted_name_usage_id.is_(None))
        .order_by(Taxon.scientific_name)
        .all()
    )
    opts: dict[str, str] = {}
    for t in taxa:
        rank_label = (t.taxon_rank or "unknown").title()
        opts[f"taxon:{t.id}"] = f"{format_scientific_name(t)}  — {rank_label}"
    return opts


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------

def build_taxonomy_tree(
    session: Session,
    filter_rank: str | None = None,
    filter_value: str | None = None,
    filter_id: int | None = None,
    nomenclatural_code: str | None = None,
) -> list[dict]:
    """Build the full checklist tree as a list of NiceGUI tree-node dicts.

    filter_id: show only the subtree rooted at this taxon id.
    filter_rank + filter_value: show only subtrees for taxa of that rank and name.
    nomenclatural_code: if set, restrict to taxa with that code (or NULL).
    No filter: show the full tree from all root taxa (parentNameUsageID IS NULL).
    """
    q = session.query(Taxon).filter(Taxon.accepted_name_usage_id.is_(None))
    if nomenclatural_code:
        from sqlalchemy import or_
        q = q.filter(
            or_(
                Taxon.nomenclatural_code == nomenclatural_code,
                Taxon.nomenclatural_code.is_(None),
            )
        )
    all_accepted = q.all()
    taxa_by_id: dict[int, Taxon] = {t.id: t for t in all_accepted}

    # Kingdom-rank taxa are navigation scaffolding only; exclude them from the
    # checklist display and promote their children as top-level display roots.
    kingdom_ids: set[int] = {t.id for t in all_accepted if t.taxon_rank == "kingdom"}
    display_taxa = [t for t in all_accepted if t.taxon_rank != "kingdom"]

    children_map: dict[int | None, list[Taxon]] = defaultdict(list)
    for t in display_taxa:
        # Remap children of kingdoms to the display root (None).
        parent = t.parent_name_usage_id
        children_map[None if parent in kingdom_ids else parent].append(t)

    # Specimen counts (current determinations only).
    spec_counts: dict[int, int] = {}
    for taxon_id, cnt in (
        session.query(TaxonDetermination.taxon_id, func.count(CollectionObject.id))
        .join(CollectionObject, CollectionObject.id == TaxonDetermination.collection_object_id)
        .filter(TaxonDetermination.is_current == 1)
        .group_by(TaxonDetermination.taxon_id)
        .all()
    ):
        spec_counts[taxon_id] = cnt

    # Synonyms by accepted_name_usage_id.
    syn_map: dict[int, list[Taxon]] = defaultdict(list)
    for syn in session.query(Taxon).filter(Taxon.accepted_name_usage_id.isnot(None)):
        syn_map[syn.accepted_name_usage_id].append(syn)

    # Determine root taxa to display.
    if filter_id is not None:
        root_taxon = taxa_by_id.get(filter_id)
        roots = [root_taxon] if root_taxon and root_taxon.taxon_rank != "kingdom" else []
    elif filter_rank and filter_value:
        roots = [
            t for t in display_taxa
            if t.taxon_rank == filter_rank and t.scientific_name == filter_value
        ]
    else:
        roots = children_map.get(None, [])

    return [
        _build_node(t, children_map, spec_counts, syn_map)
        for t in sorted(roots, key=order_key)
    ]


def _build_node(
    taxon: Taxon,
    children_map: dict,
    spec_counts: dict,
    syn_map: dict,
) -> dict:
    """Recursively build a tree node dict for `taxon`."""
    child_taxa = sorted(children_map.get(taxon.id, []), key=order_key)
    child_nodes = [
        _build_node(c, children_map, spec_counts, syn_map) for c in child_taxa
    ]

    syns = sorted(syn_map.get(taxon.id, []), key=format_scientific_name)
    syn_nodes = [
        {
            "id":    f"syn-{s.id}",
            "label": format_scientific_name(s),
            "name":  s.scientific_name or f"taxon #{s.id}",
            "auth":  s.scientific_name_authorship or "",
            "rank":  "synonym",
            "synonym": True,
        }
        for s in syns
    ]

    # Aggregate counts bottom-up.
    own_spec = spec_counts.get(taxon.id, 0)
    total_spec = own_spec + sum(n.get("spec_count", 0) for n in child_nodes)
    total_spp = (
        (1 if taxon.taxon_rank == "species" else 0)
        + sum(n.get("spp_count", 0) for n in child_nodes)
    )

    node: dict = {
        "id":         f"taxon-{taxon.id}",
        "tid":        taxon.id,            # raw id for the reorder controls
        "label":      format_scientific_name(taxon),
        "name":       taxon.scientific_name or f"taxon #{taxon.id}",
        "auth":       taxon.scientific_name_authorship or "",
        "rank":       taxon.taxon_rank or "unknown",
        "orderable":  (taxon.taxon_rank in ORDERABLE_RANKS),
        "spec_count": total_spec,
        "spp_count":  total_spp,
    }
    if taxon.taxonworks_otu_id:
        node["tw_url"] = taxonpages_url(taxon.taxonworks_otu_id)

    all_children = child_nodes + syn_nodes
    if all_children:
        node["children"] = all_children

    return node

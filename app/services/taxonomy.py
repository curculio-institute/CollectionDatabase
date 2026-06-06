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


@dataclass
class TaxonomyStats:
    total_accepted: int
    total_species: int
    total_specimens: int


def get_stats(session: Session) -> TaxonomyStats:
    accepted_base = session.query(func.count(Taxon.id)).filter(
        Taxon.accepted_name_usage_id.is_(None)
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
) -> list[dict]:
    """Build the full checklist tree as a list of NiceGUI tree-node dicts.

    filter_id: show only the subtree rooted at this taxon id.
    filter_rank + filter_value: show only subtrees for taxa of that rank and name.
    No filter: show the full tree from all root taxa (parentNameUsageID IS NULL).
    """
    all_accepted = (
        session.query(Taxon)
        .filter(Taxon.accepted_name_usage_id.is_(None))
        .all()
    )
    taxa_by_id: dict[int, Taxon] = {t.id: t for t in all_accepted}

    children_map: dict[int | None, list[Taxon]] = defaultdict(list)
    for t in all_accepted:
        children_map[t.parent_name_usage_id].append(t)

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
        roots = [root_taxon] if root_taxon else []
    elif filter_rank and filter_value:
        roots = [
            t for t in all_accepted
            if t.taxon_rank == filter_rank and t.scientific_name == filter_value
        ]
    else:
        roots = children_map.get(None, [])

    return [
        _build_node(t, children_map, spec_counts, syn_map)
        for t in sorted(roots, key=lambda t: t.scientific_name or "")
    ]


def _build_node(
    taxon: Taxon,
    children_map: dict,
    spec_counts: dict,
    syn_map: dict,
) -> dict:
    """Recursively build a tree node dict for `taxon`."""
    child_taxa = sorted(
        children_map.get(taxon.id, []), key=lambda t: t.scientific_name or ""
    )
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
        "label":      format_scientific_name(taxon),
        "name":       taxon.scientific_name or f"taxon #{taxon.id}",
        "auth":       taxon.scientific_name_authorship or "",
        "rank":       taxon.taxon_rank or "unknown",
        "spec_count": total_spec,
        "spp_count":  total_spp,
    }
    if taxon.taxonworks_otu_id:
        node["tw_url"] = taxonpages_url(taxon.taxonworks_otu_id)

    all_children = child_nodes + syn_nodes
    if all_children:
        node["children"] = all_children

    return node

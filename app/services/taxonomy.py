"""Taxonomy service: checklist tree + summary stats.

Tree structure mirrors a scientific paper checklist:
  FAMILY
    Subfamily
      Tribe
        Subtribe
          Genus
            Genus species Author, Year          [↗ TaxonPages]
              = Synonym species Author, Year
              = Synonym species Author, Year

Ranks are skipped when no taxon in the current group has a value for them.
Synonyms (accepted_name_usage_id IS NOT NULL) appear as leaf children of
their valid name, prefixed with '='.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import CollectionObject, Taxon, TaxonDetermination
from app.services.taxa import format_scientific_name
from app.services.taxonworks import taxonpages_url


# Ordered rank levels used to build the hierarchy.
# Each entry: (rank_key used in node id/rank field, Taxon attribute name)
_RANK_LEVELS: list[tuple[str, str]] = [
    ("family",    "family"),
    ("subfamily", "subfamily"),
    ("tribe",     "tribe"),
    ("subtribe",  "subtribe"),
    ("genus",     "genus"),
    ("subgenus",  "subgenus"),
]


@dataclass
class TaxonomyStats:
    total_accepted: int      # accepted taxa (synonyms excluded)
    total_species: int       # accepted species-rank taxa
    total_specimens: int


def get_stats(session: Session) -> TaxonomyStats:
    accepted_base = session.query(func.count(Taxon.id)).filter(
        Taxon.accepted_name_usage_id.is_(None)
    )
    return TaxonomyStats(
        total_accepted  = accepted_base.scalar() or 0,
        total_species   = accepted_base.filter(Taxon.specific_epithet.isnot(None)).scalar() or 0,
        total_specimens = session.query(func.count(CollectionObject.id)).scalar() or 0,
    )


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------

def checklist_options(session: Session) -> dict[str, str]:
    """Return {key: label} for the taxonomy filter select widget.

    Keys encode rank and value: 'genus:Otiorhynchus' or 'species:42'.
    Labels are searchable: 'Otiorhynchus — Genus'.
    """
    opts: dict[str, str] = {}

    rank_meta = [
        ("family",    "Family"),
        ("subfamily", "Subfamily"),
        ("tribe",     "Tribe"),
        ("subtribe",  "Subtribe"),
        ("genus",     "Genus"),
        ("subgenus",  "Subgenus"),
    ]
    for rank_attr, rank_label in rank_meta:
        col = getattr(Taxon, rank_attr)
        rows = (
            session.query(col).distinct()
            .filter(col.isnot(None), Taxon.accepted_name_usage_id.is_(None))
            .order_by(col)
            .all()
        )
        for (val,) in rows:
            opts[f"{rank_attr}:{val}"] = f"{val}  — {rank_label}"

    taxa = (
        session.query(Taxon)
        .filter(Taxon.specific_epithet.isnot(None), Taxon.accepted_name_usage_id.is_(None))
        .order_by(Taxon.genus, Taxon.specific_epithet)
        .all()
    )
    for t in taxa:
        opts[f"species:{t.id}"] = f"{format_scientific_name(t)}  — Species"

    return opts


def build_taxonomy_tree(
    session: Session,
    filter_rank: str | None = None,
    filter_value: str | None = None,
    filter_id: int | None = None,
) -> list[dict]:
    """Build the full checklist tree as a list of NiceGUI tree node dicts."""

    # Accepted taxa — optionally pre-filtered by a rank value or species id.
    # Exclude parent-rank rows (specific_epithet IS NULL) unless at least one
    # determination to that taxon exists.  Those rows exist only as determination
    # targets; they must not appear as leaf nodes or "(unplaced)" entries in the tree.
    # Taxon IDs that have at least one determination — used to include higher-rank
    # taxa (genus, tribe…) only when a specimen is actually determined to them.
    # Non-correlated subquery avoids the auto-correlation problem that arises
    # because the outer query already joins taxon_determination.
    from sqlalchemy import select as sa_select
    taxa_with_dets = sa_select(TaxonDetermination.taxon_id).distinct()
    q = (
        session.query(Taxon, func.count(CollectionObject.id).label("spec_count"))
        .filter(
            Taxon.accepted_name_usage_id.is_(None),
            (Taxon.specific_epithet.isnot(None)) | Taxon.id.in_(taxa_with_dets),
        )
    )
    if filter_id is not None:
        q = q.filter(Taxon.id == filter_id)
    elif filter_rank and filter_value:
        _VALID_RANKS = {"family", "subfamily", "tribe", "subtribe", "genus", "subgenus"}
        if filter_rank in _VALID_RANKS:
            q = q.filter(getattr(Taxon, filter_rank) == filter_value)

    rows = (
        q
        .outerjoin(
            TaxonDetermination,
            and_(
                TaxonDetermination.taxon_id == Taxon.id,
                TaxonDetermination.is_current == 1,
            ),
        )
        .outerjoin(CollectionObject, CollectionObject.id == TaxonDetermination.collection_object_id)
        .group_by(Taxon.id)
        .all()
    )


    # Synonyms keyed by accepted taxon id
    syn_map: dict[int, list[Taxon]] = defaultdict(list)
    for syn in session.query(Taxon).filter(Taxon.accepted_name_usage_id.isnot(None)):
        syn_map[syn.accepted_name_usage_id].append(syn)

    return _build_level(rows, 0, syn_map, path="")


# ---------------------------------------------------------------------------
# Recursive helpers
# ---------------------------------------------------------------------------

def _build_level(
    rows: list,
    rank_index: int,
    syn_map: dict,
    path: str,
) -> list[dict]:
    """Recursively partition `rows` into tree nodes for `_RANK_LEVELS[rank_index]`."""

    # All ranks consumed → emit species leaf nodes
    if rank_index >= len(_RANK_LEVELS):
        return _species_nodes(rows, syn_map)

    rank_key, rank_attr = _RANK_LEVELS[rank_index]

    # Skip this rank when none of the taxa have a value for it
    if all(getattr(t, rank_attr) is None for t, _ in rows):
        return _build_level(rows, rank_index + 1, syn_map, path)

    # Group by rank value
    groups: dict[str, dict] = {}
    for taxon, spec_count in rows:
        val = getattr(taxon, rank_attr) or "(unplaced)"
        if val not in groups:
            groups[val] = {"rows": [], "spp_count": 0, "spec_count": 0}
        g = groups[val]
        g["rows"].append((taxon, spec_count))
        g["spec_count"] += spec_count
        if taxon.specific_epithet:   # species-rank taxa count as spp
            g["spp_count"] += 1

    nodes = []
    for val in sorted(groups):
        g = groups[val]
        node_id = f"{path}{rank_key}-{val}".replace(" ", "_")
        children = _build_level(g["rows"], rank_index + 1, syn_map, path=node_id + "/")

        # Authorship: look it up from the first taxon in this group.
        # All taxa sharing the same rank value should share the same authorship.
        first_taxon: Taxon = g["rows"][0][0]
        auth = getattr(first_taxon, f"{rank_attr}_authorship", None)
        if rank_key == "subgenus":
            label = f"({val}) {auth}" if auth else f"({val})"
        else:
            label = f"{val} {auth}" if auth else val

        node: dict = {
            "id":         node_id,
            "label":      label,
            "rank":       rank_key,
            "spp_count":  g["spp_count"],
            "spec_count": g["spec_count"],
        }
        if children:
            node["children"] = children
        nodes.append(node)

    return nodes


def _species_nodes(rows: list, syn_map: dict) -> list[dict]:
    """Build leaf nodes for species-rank taxa, with synonyms as their children."""
    nodes = []
    for taxon, spec_count in rows:
        node: dict = {
            "id":         f"sp-{taxon.id}",
            "label":      format_scientific_name(taxon),
            "rank":       "species",
            "spec_count": spec_count,
        }
        if taxon.taxonworks_otu_id:
            node["tw_url"] = taxonpages_url(taxon.taxonworks_otu_id)

        syns = sorted(syn_map.get(taxon.id, []), key=format_scientific_name)
        if syns:
            node["children"] = [
                {
                    "id":      f"syn-{s.id}",
                    "label":   format_scientific_name(s),
                    "rank":    "synonym",
                    "synonym": True,
                }
                for s in syns
            ]
        nodes.append(node)

    return sorted(nodes, key=lambda n: n["label"])

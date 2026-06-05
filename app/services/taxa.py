from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Taxon
from app.models.base import _utcnow


@dataclass(frozen=True)
class TaxonOption:
    id: int
    label: str


def format_scientific_name(taxon: Taxon) -> str:
    """Build the display name from component parts. None-safe.

    For species and below uses genus + subgenus + epithets.
    For higher taxa (no genus) falls back through subtribe → tribe →
    subfamily → family → order.
    """
    parts = [
        taxon.genus,
        f"({taxon.subgenus})" if taxon.subgenus else None,
        taxon.specific_epithet,
        taxon.infraspecific_epithet,
    ]
    name = " ".join(p for p in parts if p)
    if not name:
        name = (taxon.subtribe or taxon.tribe or taxon.subfamily
                or taxon.family or taxon.taxon_order or "")
    if name and taxon.scientific_name_authorship:
        name = f"{name} {taxon.scientific_name_authorship}"
    return name.strip() or f"taxon #{taxon.id}"


def find_taxon_by_name(session: Session, scientific_name: str) -> "Taxon | None":
    """Find an accepted local taxon matching a DwC scientificName string.

    Parses 'Genus [(Subgenus)] epithet [authorship]' and matches on genus +
    specific_epithet.  Returns None when the name is empty, unparseable, or
    matches more than one accepted taxon (ambiguous genus-only query).
    """
    parts = scientific_name.strip().split()
    if not parts:
        return None
    genus = parts[0]
    # Skip a subgenus token in parentheses
    idx = 1
    if len(parts) > 1 and parts[1].startswith("("):
        idx = 2
    # Next token is the epithet only if it starts with a lowercase letter
    epithet: str | None = None
    if idx < len(parts) and parts[idx][0].islower():
        epithet = parts[idx]

    q = (session.query(Taxon)
         .filter(Taxon.accepted_name_usage_id.is_(None), Taxon.genus == genus))
    if epithet:
        q = q.filter(Taxon.specific_epithet == epithet)

    results = q.all()
    if len(results) == 1:
        return results[0]
    # Exact authorship tie-break when multiple species share the same epithet
    if len(results) > 1 and epithet:
        for t in results:
            if t.specific_epithet == epithet:
                return t
    return None


def create_taxon_manual(
    session: Session,
    *,
    genus: str,
    specific_epithet: str | None = None,
    infraspecific_epithet: str | None = None,
    scientific_name_authorship: str | None = None,
    family: str | None = None,
    subfamily: str | None = None,
    tribe: str | None = None,
    subtribe: str | None = None,
    subgenus: str | None = None,
) -> "Taxon":
    """Create a new accepted taxon from manually entered fields.

    Used when a DwC scientificName is not found locally or in TaxonWorks.
    The taxon is created without a TW link (taxonworks_otu_id = NULL).
    """
    t = Taxon(
        genus=genus or None,
        specific_epithet=specific_epithet or None,
        infraspecific_epithet=infraspecific_epithet or None,
        scientific_name_authorship=scientific_name_authorship or None,
        family=family or None,
        subfamily=subfamily or None,
        tribe=tribe or None,
        subtribe=subtribe or None,
        subgenus=subgenus or None,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


@dataclass(frozen=True)
class TaxonSearchResult:
    id: int
    label: str
    is_synonym: bool
    accepted_label: str | None   # label of the accepted taxon when is_synonym


def search_taxa_for_display(
    session: Session, query: str, limit: int = 10
) -> list[TaxonSearchResult]:
    """Search taxa for the search widget: valid names first, synonyms flagged.

    Returns up to `limit` results ordered: accepted taxa → synonyms,
    then alphabetically within each group.
    """
    from sqlalchemy import case as sa_case
    from sqlalchemy.orm import joinedload

    q = session.query(Taxon).options(joinedload(Taxon.accepted_name_usage))
    if query.strip():
        pat = f"%{query.strip()}%"
        q = q.filter(
            Taxon.genus.ilike(pat)
            | Taxon.subgenus.ilike(pat)
            | Taxon.specific_epithet.ilike(pat)
            | Taxon.infraspecific_epithet.ilike(pat)
            | Taxon.subtribe.ilike(pat)
            | Taxon.tribe.ilike(pat)
            | Taxon.subfamily.ilike(pat)
            | Taxon.family.ilike(pat)
        )
    order_valid_first = sa_case(
        (Taxon.accepted_name_usage_id.is_(None), 0), else_=1
    )
    q = q.order_by(order_valid_first, Taxon.genus, Taxon.specific_epithet).limit(limit)

    out = []
    for t in q:
        is_syn = t.accepted_name_usage_id is not None
        accepted_label = (
            format_scientific_name(t.accepted_name_usage)
            if is_syn and t.accepted_name_usage
            else None
        )
        out.append(TaxonSearchResult(
            id=t.id,
            label=format_scientific_name(t),
            is_synonym=is_syn,
            accepted_label=accepted_label,
        ))
    return out


def search_taxa(session: Session, query: str, limit: int = 1000) -> list[TaxonOption]:
    """Return taxa matching query (case-insensitive across genus + epithets).
    Empty query returns first `limit` taxa alphabetically."""
    q = session.query(Taxon)
    if query.strip():
        pat = f"%{query.strip()}%"
        q = q.filter(
            Taxon.genus.ilike(pat)
            | Taxon.subgenus.ilike(pat)
            | Taxon.specific_epithet.ilike(pat)
            | Taxon.infraspecific_epithet.ilike(pat)
            | Taxon.subtribe.ilike(pat)
            | Taxon.tribe.ilike(pat)
            | Taxon.subfamily.ilike(pat)
            | Taxon.family.ilike(pat)
        )
    q = q.order_by(Taxon.genus, Taxon.specific_epithet).limit(limit)
    return [TaxonOption(id=t.id, label=format_scientific_name(t)) for t in q]


# ---------------------------------------------------------------------------
# TaxonWorks integration
# ---------------------------------------------------------------------------

# Maps TW rank strings to the ORM attribute they fill.
_RANK_TO_ATTR: dict[str, str] = {
    "order":       "taxon_order",
    "suborder":    "taxon_order",
    "family":      "family",
    "subfamily":   "subfamily",
    "tribe":       "tribe",
    "subtribe":    "subtribe",
    "genus":       "genus",
    "subgenus":    "subgenus",
    "species":     "specific_epithet",
    "subspecies":  "infraspecific_epithet",
    "variety":     "infraspecific_epithet",
    "form":        "infraspecific_epithet",
}


def _fields_from_tw(tw: dict) -> dict:
    """Extract local Taxon field values from a TW taxon_names record.

    `tw` may be a plain record or an augmented one from fetch_full_classification,
    which adds ancestor fields (family, subfamily, tribe, subtribe, genus, subgenus,
    taxon_order) directly as top-level keys.
    """
    name    = tw.get("name") or ""
    rank    = (tw.get("rank") or "").lower()
    cached  = (tw.get("cached") or "").strip()
    auth    = tw.get("cached_author_year") or tw.get("cached_author") or None

    fields: dict = {}
    if auth:
        fields["scientific_name_authorship"] = auth

    # Ancestor fields injected by fetch_full_classification
    for attr in ("taxon_order", "family", "subfamily", "tribe", "subtribe", "genus", "subgenus"):
        val = tw.get(attr)
        if val:
            fields[attr] = val

    # Authorship for each rank (also injected by fetch_full_classification)
    for rank_key in ("family", "subfamily", "tribe", "subtribe", "genus", "subgenus"):
        val = tw.get(f"{rank_key}_authorship")
        if val:
            fields[f"{rank_key}_authorship"] = val

    # Target taxon's own rank
    attr = _RANK_TO_ATTR.get(rank)
    if attr:
        fields[attr] = name

    # Fallback: genus from first word of cached when not supplied by ancestor walk
    if rank in ("species", "subspecies", "variety", "form") and "genus" not in fields:
        parts = cached.split()
        if parts:
            fields["genus"] = parts[0]
    if rank in ("subspecies", "variety", "form") and "specific_epithet" not in fields:
        parts = cached.split()
        if len(parts) > 1:
            fields["specific_epithet"] = parts[1]

    return fields


# ---------------------------------------------------------------------------
# Parent-row helpers
# ---------------------------------------------------------------------------

# Supra-generic rank levels in order highest → lowest.
# Each entry: (rank_attr, auth_attr, next_lower_attr | None)
# A row at rank X has: ranks [family..X] set, everything below X null, genus null.
_SUPRA_GENERIC: list[tuple[str, str, str | None]] = [
    ("family",    "family_authorship",    "subfamily"),
    ("subfamily", "subfamily_authorship", "tribe"),
    ("tribe",     "tribe_authorship",     "subtribe"),
    ("subtribe",  "subtribe_authorship",  None),
]


def _ensure_parent_rows(session: Session, fields: dict) -> None:
    """Create all missing parent-rank Taxon rows for a species being imported.

    Covers family → subfamily → tribe → subtribe → genus → subgenus.
    Each rank row sets ONLY ancestor fields + its own rank; never lower ranks.
    """
    # Supra-generic ranks (genus IS NULL)
    for i, (rank_attr, auth_attr, next_lower) in enumerate(_SUPRA_GENERIC):
        val = fields.get(rank_attr)
        if not val:
            continue

        # Uniqueness: rank column set, next-lower column null, genus null
        q = session.query(Taxon).filter(
            getattr(Taxon, rank_attr) == val,
            Taxon.genus.is_(None),
        )
        if next_lower:
            q = q.filter(getattr(Taxon, next_lower).is_(None))
        if q.first():
            continue

        # Build row: set only ranks up to and including i (ancestors + self)
        row = Taxon(created_at=_utcnow(), updated_at=_utcnow())
        for j, (a, aa, _) in enumerate(_SUPRA_GENERIC[:i + 1]):
            v = fields.get(a)
            if v:
                setattr(row, a, v)
            av = fields.get(aa)
            if av:
                setattr(row, aa, av)
        session.add(row)

    # Genus row: genus set, subgenus null, specific_epithet null
    genus    = fields.get("genus")
    subgenus = fields.get("subgenus")
    if genus:
        if not session.query(Taxon).filter(
            Taxon.genus == genus,
            Taxon.subgenus.is_(None),
            Taxon.specific_epithet.is_(None),
        ).first():
            session.add(Taxon(
                genus=genus,
                family=fields.get("family"),
                subfamily=fields.get("subfamily"),
                tribe=fields.get("tribe"),
                subtribe=fields.get("subtribe"),
                genus_authorship=fields.get("genus_authorship"),
                family_authorship=fields.get("family_authorship"),
                subfamily_authorship=fields.get("subfamily_authorship"),
                tribe_authorship=fields.get("tribe_authorship"),
                subtribe_authorship=fields.get("subtribe_authorship"),
                created_at=_utcnow(), updated_at=_utcnow(),
            ))

        # Subgenus row: genus set, subgenus set, specific_epithet null
        if subgenus and not session.query(Taxon).filter(
            Taxon.genus == genus,
            Taxon.subgenus == subgenus,
            Taxon.specific_epithet.is_(None),
        ).first():
            session.add(Taxon(
                genus=genus,
                subgenus=subgenus,
                family=fields.get("family"),
                subfamily=fields.get("subfamily"),
                tribe=fields.get("tribe"),
                subtribe=fields.get("subtribe"),
                family_authorship=fields.get("family_authorship"),
                subfamily_authorship=fields.get("subfamily_authorship"),
                tribe_authorship=fields.get("tribe_authorship"),
                subtribe_authorship=fields.get("subtribe_authorship"),
                created_at=_utcnow(), updated_at=_utcnow(),
            ))

    session.flush()


def ensure_higher_taxa(session: Session) -> int:
    """Backfill all parent-rank rows derived from existing species rows.

    Cleans up any incorrectly-structured rows from previous runs first,
    then recreates them with the correct logic.  Safe to call at every startup.
    Returns the number of rows created.
    """
    from app.models import TaxonDetermination

    # Remove auto-generated parent rows that have no TW link and no determinations.
    # These may be structurally wrong from an earlier buggy run; recreate correctly.
    orphan_ids = [
        row.id for row in
        session.query(Taxon.id)
        .filter(
            Taxon.specific_epithet.is_(None),
            Taxon.taxonworks_otu_id.is_(None),
            Taxon.accepted_name_usage_id.is_(None),
            ~session.query(TaxonDetermination.id)
              .filter(TaxonDetermination.taxon_id == Taxon.id)
              .exists(),
        )
        .all()
    ]
    if orphan_ids:
        session.query(Taxon).filter(Taxon.id.in_(orphan_ids)).delete(
            synchronize_session=False
        )
        session.flush()

    species = session.query(Taxon).filter(Taxon.specific_epithet.isnot(None)).all()
    before  = session.query(Taxon).count()

    for sp in species:
        _ensure_parent_rows(session, {
            "family":    sp.family,    "subfamily":  sp.subfamily,
            "tribe":     sp.tribe,     "subtribe":   sp.subtribe,
            "genus":     sp.genus,     "subgenus":   sp.subgenus,
            "family_authorship":    sp.family_authorship,
            "subfamily_authorship": sp.subfamily_authorship,
            "tribe_authorship":     sp.tribe_authorship,
            "subtribe_authorship":  sp.subtribe_authorship,
            "genus_authorship":     sp.genus_authorship,
        })

    after = session.query(Taxon).count()
    return after - before


def get_or_create_from_tw_data(
    session: Session, tw: dict, otu_id: int | None = None
) -> Taxon:
    """Find the matching local Taxon or create it from a TW taxon_names record.

    Matching key: genus + specific_epithet (or just genus for genus-rank names).
    Creates the row if absent; never updates existing rows.

    Synonym handling: if fetch_full_classification detected the name is invalid
    (cached_is_valid=False), tw contains '_valid_tw_data' and '_valid_otu_id'.
    The valid taxon is imported first; the synonym row gets accepted_name_usage_id
    set to point at it.
    """
    # If this is a synonym, ensure the valid taxon exists first.
    accepted_id: int | None = None
    valid_tw = tw.get("_valid_tw_data")
    if valid_tw:
        valid_taxon = get_or_create_from_tw_data(
            session, valid_tw, otu_id=tw.get("_valid_otu_id")
        )
        accepted_id = valid_taxon.id

    fields = _fields_from_tw(tw)
    rank    = (tw.get("rank") or "").lower()
    genus   = fields.get("genus")
    species = fields.get("specific_epithet")
    infra   = fields.get("infraspecific_epithet")

    # Higher-rank taxa (no genus): match on the specific rank column only.
    # Matching on genus+epithet when both are NULL would be dangerously broad.
    _HIGHER_RANKS = {
        "subtribe": "subtribe", "tribe": "tribe",
        "subfamily": "subfamily", "family": "family",
        "order": "taxon_order", "suborder": "taxon_order",
    }
    if rank in _HIGHER_RANKS and not genus:
        rank_attr = _HIGHER_RANKS[rank]
        rank_val  = fields.get(rank_attr)
        existing  = (
            session.query(Taxon)
            .filter(
                getattr(Taxon, rank_attr) == rank_val,
                Taxon.genus.is_(None),
                Taxon.specific_epithet.is_(None),
            )
            .first()
        ) if rank_val else None
    else:
        # Species / genus / subgenus: match on genus + epithet
        q = session.query(Taxon)
        if genus:
            q = q.filter(Taxon.genus == genus)
        if species:
            q = q.filter(Taxon.specific_epithet == species)
        else:
            q = q.filter(Taxon.specific_epithet.is_(None))
        if infra:
            q = q.filter(Taxon.infraspecific_epithet == infra)
        existing = q.first()
    if existing:
        dirty = False
        if otu_id and not existing.taxonworks_otu_id:
            existing.taxonworks_otu_id = otu_id
            dirty = True
        if accepted_id and not existing.accepted_name_usage_id:
            existing.accepted_name_usage_id = accepted_id
            dirty = True
        for attr in ("family_authorship", "subfamily_authorship", "tribe_authorship",
                     "subtribe_authorship", "genus_authorship", "subgenus_authorship"):
            if fields.get(attr) and not getattr(existing, attr):
                setattr(existing, attr, fields[attr])
                dirty = True
        if dirty:
            existing.updated_at = _utcnow()
            session.flush()
        return existing

    # Create new
    t = Taxon(created_at=_utcnow(), updated_at=_utcnow())
    for attr, val in fields.items():
        if val:
            setattr(t, attr, val)
    if otu_id:
        t.taxonworks_otu_id = otu_id
    if accepted_id:
        t.accepted_name_usage_id = accepted_id
    session.add(t)
    session.flush()
    # Ensure all parent-rank rows exist (genus, subgenus, tribe, etc.)
    if species or (genus and not species):
        _ensure_parent_rows(session, fields)
    return t

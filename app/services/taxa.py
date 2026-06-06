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
    """Return display name: scientificName + authorship (if present)."""
    name = taxon.scientific_name or ""
    auth = taxon.scientific_name_authorship or ""
    if name and auth:
        return f"{name} {auth}"
    return name or f"taxon #{taxon.id}"


def find_taxon_by_name(session: Session, scientific_name: str) -> "Taxon | None":
    """Find an accepted taxon matching a name string.

    Tries exact match on dwc:scientificName first; then falls back to a
    two-token match (strips trailing authorship tokens) to handle spreadsheets
    that include authorship in the scientificName column.
    Returns None when the name is empty or matches more than one accepted row.
    """
    name = scientific_name.strip()
    if not name:
        return None

    base_q = session.query(Taxon).filter(Taxon.accepted_name_usage_id.is_(None))

    results = base_q.filter(Taxon.scientific_name == name).all()
    if len(results) == 1:
        return results[0]
    if len(results) > 1:
        return None

    # Strip potential authorship: take the first 2 tokens (or 3 for subgenus form).
    parts = name.split()
    if len(parts) >= 2:
        if len(parts) >= 3 and parts[1].startswith("("):
            short = " ".join(parts[:3])
        else:
            short = " ".join(parts[:2])
        if short != name:
            results = base_q.filter(Taxon.scientific_name == short).all()
            if len(results) == 1:
                return results[0]

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

    Builds scientificName and taxonRank from components, creates any missing
    parent-rank rows, and links the new row via parentNameUsageID.
    """
    if specific_epithet:
        if subgenus:
            sci_name = f"{genus} ({subgenus}) {specific_epithet}"
        else:
            sci_name = f"{genus} {specific_epithet}"
        if infraspecific_epithet:
            sci_name = f"{sci_name} {infraspecific_epithet}"
        rank = "subspecies" if infraspecific_epithet else "species"
    else:
        sci_name = genus
        rank = "genus"

    fields = {
        "taxon_rank": rank,
        "genus": genus or None,
        "subgenus": subgenus or None,
        "subtribe": subtribe or None,
        "tribe": tribe or None,
        "subfamily": subfamily or None,
        "family": family or None,
    }
    parent_id = _ensure_parent_rows(session, fields)

    t = Taxon(
        scientific_name=sci_name,
        taxon_rank=rank,
        taxonomic_status="accepted",
        scientific_name_authorship=scientific_name_authorship or None,
        parent_name_usage_id=parent_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


@dataclass(frozen=True)
class TaxonSearchResult:
    id: int
    label: str             # full display name: "Name Authorship"
    is_synonym: bool
    accepted_label: str | None  # full display name of accepted taxon (if synonym)
    scientific_name: str = ""
    authorship: str | None = None
    family: str | None = None


def _get_family(taxon: Taxon) -> str | None:
    """Walk parent chain (max 10 hops) to find the family-rank ancestor."""
    t = taxon
    seen: set[int] = set()
    for _ in range(10):
        if t is None or t.id in seen:
            break
        if t.taxon_rank == "family":
            return t.scientific_name
        seen.add(t.id)
        t = t.parent
    return None


def search_taxa_for_display(
    session: Session, query: str, limit: int = 10,
    nomenclatural_codes: list[str] | None = None,
) -> list[TaxonSearchResult]:
    """Search taxa for the search widget: accepted names first, synonyms flagged.

    Splits query on whitespace so multi-token input like "Sit lin" matches
    "Sitona lineatus" (each token must appear somewhere in the name).
    """
    from sqlalchemy import case as sa_case
    from sqlalchemy.orm import joinedload

    q = session.query(Taxon).options(joinedload(Taxon.accepted_name_usage))
    for token in query.split():
        q = q.filter(Taxon.scientific_name.ilike(f"%{token}%"))
    if nomenclatural_codes:
        q = q.filter(Taxon.nomenclatural_code.in_(nomenclatural_codes))

    order_valid_first = sa_case(
        (Taxon.accepted_name_usage_id.is_(None), 0), else_=1
    )
    q = q.order_by(order_valid_first, Taxon.scientific_name).limit(limit)

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
            scientific_name=t.scientific_name or "",
            authorship=t.scientific_name_authorship or None,
            family=_get_family(t),
        ))
    return out


def search_taxa(session: Session, query: str, limit: int = 1000) -> list[TaxonOption]:
    """Return taxa matching query (case-insensitive, multi-token).
    Empty query returns first `limit` taxa alphabetically."""
    q = session.query(Taxon)
    for token in query.split():
        q = q.filter(Taxon.scientific_name.ilike(f"%{token}%"))
    q = q.order_by(Taxon.scientific_name).limit(limit)
    return [TaxonOption(id=t.id, label=format_scientific_name(t)) for t in q]


# ---------------------------------------------------------------------------
# TaxonWorks integration
# ---------------------------------------------------------------------------

def _scientific_name_from_tw(tw: dict, ancestor_fields: dict) -> str:
    """Build the bare scientificName (without authorship) from a TW record."""
    rank = (tw.get("rank") or "").lower()
    name = tw.get("name") or ""
    genus = ancestor_fields.get("genus", "")
    subgenus = ancestor_fields.get("subgenus", "")

    if rank == "species":
        if subgenus:
            return f"{genus} ({subgenus}) {name}".strip()
        return f"{genus} {name}".strip()

    if rank in ("subspecies", "variety", "form"):
        specific_epithet = ancestor_fields.get("specific_epithet", "")
        if specific_epithet:
            if subgenus:
                return f"{genus} ({subgenus}) {specific_epithet} {name}".strip()
            return f"{genus} {specific_epithet} {name}".strip()
        # Fallback: strip authorship from TW's cached full name.
        cached = (tw.get("cached") or "").strip()
        auth = tw.get("cached_author_year") or tw.get("cached_author") or ""
        if auth and cached.endswith(auth):
            return cached[: -len(auth)].strip()
        return f"{genus} {name}".strip() if genus else name

    # Uninomials: genus, subgenus, family, subfamily, tribe, subtribe, order …
    return name


def _fields_from_tw(tw: dict) -> dict:
    """Extract Taxon field values from an augmented TW taxon_names record.

    Returns a dict containing:
      scientific_name, taxon_rank, scientific_name_authorship
      + ancestor keys used by _ensure_parent_rows:
        taxon_order, family, family_authorship, subfamily, subfamily_authorship,
        tribe, tribe_authorship, subtribe, subtribe_authorship,
        genus, genus_authorship, subgenus, subgenus_authorship
    """
    rank = (tw.get("rank") or "").lower()
    auth = tw.get("cached_author_year") or tw.get("cached_author") or None

    # Collect ancestor info injected by fetch_full_classification.
    anc: dict = {}
    for key in (
        "taxon_order", "taxon_order_authorship", "taxon_order_otu_id",
        "family", "family_authorship", "family_otu_id",
        "subfamily", "subfamily_authorship", "subfamily_otu_id",
        "tribe", "tribe_authorship", "tribe_otu_id",
        "subtribe", "subtribe_authorship", "subtribe_otu_id",
        "genus", "genus_authorship", "genus_otu_id",
        "subgenus", "subgenus_authorship", "subgenus_otu_id",
        "specific_epithet", "specific_epithet_authorship",
        "species_name", "species_name_otu_id",
    ):
        val = tw.get(key)
        if val:
            anc[key] = val

    sci_name = _scientific_name_from_tw(tw, anc)

    # nomenclatural_code: TW returns lowercase ("iczn", "icn", …); store uppercase.
    raw_code = tw.get("nomenclatural_code") or ""
    nomen_code = raw_code.upper() or None

    return {
        "scientific_name": sci_name,
        "taxon_rank": rank,
        "scientific_name_authorship": auth,
        "nomenclatural_code": nomen_code,
        **anc,
    }


# ---------------------------------------------------------------------------
# Parent-row helpers
# ---------------------------------------------------------------------------

# Ordered rank chain highest → lowest (excludes species-rank and below).
# Each entry: (rank_name_stored, ancestor_dict_key, authorship_dict_key)
_RANK_CHAIN: list[tuple[str, str, str]] = [
    ("order",     "taxon_order",  "taxon_order_authorship"),
    ("family",    "family",       "family_authorship"),
    ("subfamily", "subfamily",    "subfamily_authorship"),
    ("tribe",     "tribe",        "tribe_authorship"),
    ("subtribe",  "subtribe",     "subtribe_authorship"),
    ("genus",     "genus",        "genus_authorship"),
    ("subgenus",  "subgenus",     "subgenus_authorship"),
    ("species",   "species_name", "specific_epithet_authorship"),
]


def _ensure_parent_rows(
    session: Session, fields: dict, nomenclatural_code: str | None = None
) -> int | None:
    """Create all missing ancestor rows and return the immediate parent taxon ID.

    Walks _RANK_CHAIN from highest to lowest rank, stopping when it reaches
    the target rank so the caller can create that row itself.  Each row is
    created only once (matched by scientific_name + taxon_rank); existing rows
    get their parent_name_usage_id and nomenclatural_code filled in if not set.
    """
    target_rank = fields.get("taxon_rank", "")
    # Prefer explicit field value; fall back to the passed-in code.
    nomen_code = fields.get("nomenclatural_code") or nomenclatural_code
    parent_id: int | None = None

    for rank_name, field_key, auth_key in _RANK_CHAIN:
        if rank_name == target_rank:
            break

        name = fields.get(field_key)
        if not name:
            continue

        auth = fields.get(auth_key)
        otu_id = fields.get(f"{field_key}_otu_id")

        existing = (
            session.query(Taxon)
            .filter(Taxon.scientific_name == name, Taxon.taxon_rank == rank_name)
            .first()
        )
        if not existing:
            existing = Taxon(
                scientific_name=name,
                taxon_rank=rank_name,
                taxonomic_status="accepted",
                scientific_name_authorship=auth or None,
                parent_name_usage_id=parent_id,
                taxonworks_otu_id=otu_id,
                nomenclatural_code=nomen_code,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(existing)
            session.flush()
        else:
            dirty = False
            if parent_id and not existing.parent_name_usage_id:
                existing.parent_name_usage_id = parent_id
                dirty = True
            if otu_id and not existing.taxonworks_otu_id:
                existing.taxonworks_otu_id = otu_id
                dirty = True
            if nomen_code and not existing.nomenclatural_code:
                existing.nomenclatural_code = nomen_code
                dirty = True
            if dirty:
                existing.updated_at = _utcnow()
                session.flush()

        parent_id = existing.id

    return parent_id


def ensure_higher_taxa(session: Session) -> int:
    """No-op in the DwC parent-link model.

    Parent rows are created during TW import via _ensure_parent_rows; no
    startup backfill is needed.  Kept for API compatibility with main.py.
    """
    return 0


def get_or_create_from_tw_data(
    session: Session, tw: dict, otu_id: int | None = None
) -> Taxon:
    """Find the matching local Taxon or create it from an augmented TW record.

    Matching key: (scientific_name, taxon_rank).
    All ancestor rows are created first via _ensure_parent_rows.

    Synonym handling: if fetch_full_classification detected the name is invalid
    (cached_is_valid=False), tw contains '_valid_tw_data' and '_valid_otu_id'.
    The valid taxon is imported first; the synonym row gets accepted_name_usage_id
    set to point at it.
    """
    # If this is a synonym, ensure the valid (accepted) taxon exists first.
    accepted_id: int | None = None
    valid_tw = tw.get("_valid_tw_data")
    if valid_tw:
        valid_taxon = get_or_create_from_tw_data(
            session, valid_tw, otu_id=tw.get("_valid_otu_id")
        )
        accepted_id = valid_taxon.id

    fields = _fields_from_tw(tw)
    sci_name = fields["scientific_name"]
    rank = fields["taxon_rank"]
    nomen_code = fields.get("nomenclatural_code")

    # Ensure all ancestor rows exist; get the immediate parent ID.
    parent_id = _ensure_parent_rows(session, fields, nomenclatural_code=nomen_code)

    existing = (
        session.query(Taxon)
        .filter(Taxon.scientific_name == sci_name, Taxon.taxon_rank == rank)
        .first()
    )
    if existing:
        dirty = False
        if otu_id and not existing.taxonworks_otu_id:
            existing.taxonworks_otu_id = otu_id
            dirty = True
        if accepted_id and not existing.accepted_name_usage_id:
            existing.accepted_name_usage_id = accepted_id
            existing.taxonomic_status = "synonym"
            dirty = True
        if parent_id and not existing.parent_name_usage_id:
            existing.parent_name_usage_id = parent_id
            dirty = True
        if nomen_code and not existing.nomenclatural_code:
            existing.nomenclatural_code = nomen_code
            dirty = True
        if dirty:
            existing.updated_at = _utcnow()
            session.flush()
        return existing

    t = Taxon(
        scientific_name=sci_name,
        taxon_rank=rank,
        taxonomic_status="synonym" if accepted_id else "accepted",
        scientific_name_authorship=fields.get("scientific_name_authorship"),
        parent_name_usage_id=parent_id,
        accepted_name_usage_id=accepted_id,
        taxonworks_otu_id=otu_id,
        nomenclatural_code=nomen_code,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


# ---------------------------------------------------------------------------
# POWO integration
# ---------------------------------------------------------------------------

def get_or_create_from_powo_data(
    session: Session,
    powo_fields: dict,
    *,
    accepted_fields: dict | None = None,
) -> Taxon:
    """Find or create a local Taxon from a POWO-derived field dict.

    powo_fields comes from powo.fields_from_powo(powo_record).
    Creates family and genus ancestor rows if missing, then the species row.
    nomenclatural_code is propagated to all rows from the POWO record.

    Synonym handling: if powo_fields["is_synonym"] is True and accepted_fields
    is provided, the accepted name is created first and the synonym row is
    linked to it via accepted_name_usage_id.

    Matching key: (scientific_name, taxon_rank) — same as TW imports.
    """
    sci_name   = powo_fields["scientific_name"]
    rank       = (powo_fields.get("taxon_rank") or "species").lower()
    auth       = powo_fields.get("scientific_name_authorship")
    nomen_code = powo_fields.get("nomenclatural_code")
    family     = powo_fields.get("family")
    genus      = powo_fields.get("genus")
    is_synonym = powo_fields.get("is_synonym", False)

    # Create the accepted name first so the synonym can link to it.
    accepted_taxon: Taxon | None = None
    if is_synonym and accepted_fields:
        accepted_taxon = get_or_create_from_powo_data(session, accepted_fields)

    # POWO gives family → genus → species (no subfamily/tribe available).
    ancestor_fields: dict = {"taxon_rank": rank, "nomenclatural_code": nomen_code}
    if family:
        ancestor_fields["family"] = family
    if genus and rank != "genus":
        ancestor_fields["genus"] = genus

    parent_id = _ensure_parent_rows(session, ancestor_fields, nomenclatural_code=nomen_code)

    existing = (
        session.query(Taxon)
        .filter(Taxon.scientific_name == sci_name, Taxon.taxon_rank == rank)
        .first()
    )
    if existing:
        dirty = False
        if parent_id and not existing.parent_name_usage_id:
            existing.parent_name_usage_id = parent_id
            dirty = True
        if nomen_code and not existing.nomenclatural_code:
            existing.nomenclatural_code = nomen_code
            dirty = True
        if accepted_taxon and not existing.accepted_name_usage_id:
            existing.accepted_name_usage_id = accepted_taxon.id
            existing.taxonomic_status = "synonym"
            dirty = True
        if dirty:
            existing.updated_at = _utcnow()
            session.flush()
        return existing

    taxonomic_status = "synonym" if (is_synonym and accepted_taxon) else "accepted"
    t = Taxon(
        scientific_name=sci_name,
        taxon_rank=rank,
        taxonomic_status=taxonomic_status,
        scientific_name_authorship=auth,
        parent_name_usage_id=parent_id,
        accepted_name_usage_id=accepted_taxon.id if accepted_taxon else None,
        nomenclatural_code=nomen_code,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t

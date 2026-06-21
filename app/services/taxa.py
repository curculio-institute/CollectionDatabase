from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Taxon
from app.models.base import _utcnow


TAXON_RANKS: list[str] = [
    "kingdom", "phylum", "subphylum", "class", "subclass",
    "superorder", "order", "suborder", "superfamily",
    "family", "subfamily", "supertribe", "tribe", "subtribe",
    "genus", "subgenus", "species", "subspecies", "variety", "form",
]

# Display order for rank-selection dropdowns: the ranks used daily for beetle
# work go on top (finest first), then the rarer higher categories descend the
# tree. This is a UI-ordering concern only — TAXON_RANKS stays in semantic
# high→low order because hierarchy validation relies on TAXON_RANKS.index().
TAXON_RANKS_BY_USE: list[str] = [
    "subspecies", "species", "subgenus", "genus",
    "variety", "form",
    "subtribe", "tribe", "supertribe",
    "subfamily", "family", "superfamily",
    "suborder", "order", "superorder",
    "subclass", "class", "subphylum", "phylum", "kingdom",
]
# Guard against drift: both lists must cover exactly the same ranks.
assert set(TAXON_RANKS_BY_USE) == set(TAXON_RANKS)


@dataclass(frozen=True)
class TaxonOption:
    id: int
    label: str
    taxon_rank: str = ""
    nomenclatural_code: str | None = None


def format_scientific_name(taxon: Taxon) -> str:
    """Return display name: scientificName + authorship (if present).

    A standalone subgenus row stores only the bare subgenus name (e.g.
    "Otiorhynchus"), which is indistinguishable from the genus of the same
    name. Render it as ``Genus (Subgenus) Authorship`` instead, deriving the
    genus from the parent so it adjusts automatically if the parent changes.
    Species/subspecies already carry the subgenus in their stored name and are
    left untouched.
    """
    name = taxon.scientific_name or ""
    auth = taxon.scientific_name_authorship or ""
    if taxon.taxon_rank == "subgenus" and name and "(" not in name:
        try:
            parent = taxon.parent
        except Exception:
            parent = None  # detached instance — degrade to the bare name
        if parent is not None and parent.scientific_name:
            name = f"{parent.scientific_name} ({name})"
    if name and auth:
        return f"{name} {auth}"
    return name or f"taxon #{taxon.id}"


# ---------------------------------------------------------------------------
# Name composition (atomic model — Epic #30)
# ---------------------------------------------------------------------------
# name_element is the atomic source of truth (this rank's own epithet/uninomial).
# compose_scientific_name() builds the bare dwc:scientificName (no authorship)
# from name_element + the parent chain, uniformly for valid names AND synonyms
# (parentNameUsageID is the name's own lineage, so a synonym composes under its
# own genus). recompose_subtree() re-runs it down the tree after a rename or
# reparent. Wiring into the write paths + retiring format_scientific_name's
# subgenus fallback happens in Phase 3.

# Infraspecific connectors: ICN (botany/mycology) uses connecting terms; ICZN
# (zoology) writes a bare trinomial with no connector.
_ICN_INFRA_CONNECTOR = {"subspecies": "subsp.", "variety": "var.", "form": "f."}


def _infra_connector(rank: str, nomenclatural_code: str | None) -> str:
    if (nomenclatural_code or "").upper() == "ICN":
        return _ICN_INFRA_CONNECTOR.get(rank, "")
    return ""  # ICZN: bare trinomial


def compose_scientific_name(session: Session, taxon: Taxon) -> str:
    """Compose the bare full name (no authorship) from name_element + the parent
    chain. Uniform for valid names and synonyms.

    Falls back to the already-stored scientific_name when name_element is not yet
    populated, so it is safe to call on rows imported before the atomic backfill.
    """
    element = (taxon.name_element or "").strip()
    if not element:
        return taxon.scientific_name or ""

    rank = (taxon.taxon_rank or "").lower()

    # Walk ancestors (by FK, via the passed session) collecting the nearest
    # genus / subgenus / species elements needed to build the name.
    genus = subgenus = species_epithet = None
    seen: set[int] = {taxon.id}
    cur_id = taxon.parent_name_usage_id
    while cur_id and cur_id not in seen:
        seen.add(cur_id)
        cur = session.get(Taxon, cur_id)
        if cur is None:
            break
        crank = (cur.taxon_rank or "").lower()
        cur_el = cur.name_element or cur.scientific_name or ""
        if crank == "genus" and genus is None:
            genus = cur_el
        elif crank == "subgenus" and subgenus is None:
            subgenus = cur_el
        elif crank == "species" and species_epithet is None:
            species_epithet = cur_el
        cur_id = cur.parent_name_usage_id

    sub = f" ({subgenus})" if subgenus else ""

    if rank == "subgenus":
        return f"{genus} ({element})".strip() if genus else f"({element})"

    if rank == "species":
        return f"{genus}{sub} {element}".strip() if genus else element

    if rank in ("subspecies", "variety", "form"):
        head = f"{genus}{sub} {species_epithet}".strip() if genus else (species_epithet or "")
        connector = _infra_connector(rank, taxon.nomenclatural_code)
        parts = [p for p in (head, connector, element) if p]
        return " ".join(parts)

    # Uninomial ranks (kingdom … family … genus): the element is the name.
    return element


def recompose_subtree(session: Session, taxon: Taxon) -> None:
    """Recompute scientific_name for `taxon` and all descendants (cascade after a
    rename or reparent)."""
    taxon.scientific_name = compose_scientific_name(session, taxon)
    taxon.updated_at = _utcnow()
    session.flush()
    for child in session.query(Taxon).filter(
        Taxon.parent_name_usage_id == taxon.id
    ).all():
        recompose_subtree(session, child)


def parse_scientific_name(
    name: str,
) -> tuple[str, str | None, str | None, str | None]:
    """Split a bare scientific name into (genus, subgenus, specific_epithet, infraspecific).

    Operates on the *bare* name only (no authorship). The subgenus is the token
    wrapped in parentheses, if present.

        'Sitona'                         → ('Sitona', None, None, None)
        'Sitona lineatus'                → ('Sitona', None, 'lineatus', None)
        'Sitona (Sitona) lineatus'       → ('Sitona', 'Sitona', 'lineatus', None)
        'Sitona lineatus lineatus'       → ('Sitona', None, 'lineatus', 'lineatus')
        'Sitona (Sitona) lineatus allii' → ('Sitona', 'Sitona', 'lineatus', 'allii')
    """
    parts = name.split()
    if not parts:
        return "", None, None, None
    if len(parts) == 1:
        return parts[0], None, None, None
    genus = parts[0]
    if len(parts) >= 3 and parts[1].startswith("("):
        subgenus = parts[1].strip("()")
        specific = parts[2]
        infra = parts[3] if len(parts) > 3 else None
        return genus, subgenus, specific, infra
    specific = parts[1]
    infra = parts[2] if len(parts) > 2 else None
    return genus, None, specific, infra


def rank_from_parse(specific: str | None, infraspecific: str | None) -> str:
    """Rank implied by a parsed binomial: subspecies / species / genus."""
    if infraspecific:
        return "subspecies"
    if specific:
        return "species"
    return "genus"


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


_EPITHET_RE = re.compile(r"^[a-z][a-z-]*$")


def build_manual_taxon_prefill(session: Session, row: dict) -> dict:
    """Starting values for the manual 'add taxon' form, parsed from a DwC row.

    Parses the row's ``scientificName`` into a bare name + rank, takes authorship
    from the ``scientificNameAuthorship`` column, and — the point of parsing —
    resolves ``parent_name_usage_id`` by looking up the parsed subgenus then
    genus (most specific that already exists) in the local DB, so the parent and
    its inherited code are pre-selected with no gap. ``accepted_name_usage_id`` is
    resolved from the row's ``acceptedNameUsage`` name when it matches a local
    taxon. Every value is a starting point the user can adjust before saving.
    """
    raw = (row.get("scientificName") or "").strip()
    genus, subgenus, specific, infra = parse_scientific_name(raw)
    # Drop tokens that aren't valid lowercase epithets (e.g. leaked authorship).
    if specific and not _EPITHET_RE.match(specific):
        specific = infra = None
    if infra and not _EPITHET_RE.match(infra):
        infra = None

    bare = genus
    if subgenus:
        bare += f" ({subgenus})"
    if specific:
        bare += f" {specific}"
    if infra:
        bare += f" {infra}"

    # Parent: only species/subspecies have a parent derivable from the binomial
    # (a new genus's parent is a family the user must pick). Prefer subspecies →
    # species, species → subgenus → genus; first that exists locally wins.
    parent_id = None
    if specific:
        candidates: list[tuple[str, str]] = []
        if infra:
            sp_name = f"{genus} ({subgenus}) {specific}" if subgenus else f"{genus} {specific}"
            candidates.append((sp_name, "species"))
        if subgenus:
            candidates.append((subgenus, "subgenus"))
        candidates.append((genus, "genus"))
        for cname, crank in candidates:
            match = (
                session.query(Taxon)
                .filter(Taxon.scientific_name == cname, Taxon.taxon_rank == crank)
                .first()
            )
            if match:
                parent_id = match.id
                break

    accepted_id = None
    acc_name = (row.get("acceptedNameUsage") or "").strip()
    if acc_name:
        acc = find_taxon_by_name(session, acc_name)
        if acc:
            accepted_id = acc.id

    return {
        "scientific_name": bare,
        "taxon_rank": rank_from_parse(specific, infra),
        "scientific_name_authorship": (row.get("scientificNameAuthorship") or "").strip() or None,
        "parent_name_usage_id": parent_id,
        "accepted_name_usage_id": accepted_id,
    }


@dataclass(frozen=True)
class TaxonSearchResult:
    id: int
    label: str             # full display name: "Name Authorship"
    is_synonym: bool
    accepted_label: str | None  # full display name of accepted taxon (if synonym)
    scientific_name: str = ""
    authorship: str | None = None
    family: str | None = None
    nomenclatural_code: str | None = None


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
            nomenclatural_code=t.nomenclatural_code,
        ))
    return out


def search_taxa(session: Session, query: str, limit: int = 1000) -> list[TaxonOption]:
    """Return taxa matching query (case-insensitive, multi-token).
    Empty query returns first `limit` taxa alphabetically."""
    q = session.query(Taxon)
    for token in query.split():
        q = q.filter(Taxon.scientific_name.ilike(f"%{token}%"))
    q = q.order_by(Taxon.scientific_name).limit(limit)
    return [
        TaxonOption(
            id=t.id,
            label=format_scientific_name(t),
            taxon_rank=t.taxon_rank or "",
            nomenclatural_code=t.nomenclatural_code,
        )
        for t in q
    ]


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
        taxon_order, suborder, superfamily, family, subfamily, tribe, subtribe,
        genus, subgenus (each optionally with _authorship and _otu_id suffixes)
    """
    rank = (tw.get("rank") or "").lower()
    auth = tw.get("cached_author_year") or tw.get("cached_author") or None

    # Collect ancestor info injected by fetch_full_classification.
    anc: dict = {}
    for key in (
        "taxon_order", "taxon_order_authorship", "taxon_order_otu_id",
        "suborder", "suborder_authorship", "suborder_otu_id",
        "superfamily", "superfamily_authorship", "superfamily_otu_id",
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

# Ordered rank chain highest → lowest.
# Each entry: (rank_name_stored_in_db, ancestor_dict_key, authorship_dict_key).
# "taxon_order" keeps the taxon_ prefix to avoid confusion with SQL's ORDER keyword.
_RANK_CHAIN: list[tuple[str, str, str]] = [
    ("order",       "taxon_order",  "taxon_order_authorship"),
    ("suborder",    "suborder",     "suborder_authorship"),
    ("superfamily", "superfamily",  "superfamily_authorship"),
    ("family",      "family",       "family_authorship"),
    ("subfamily",   "subfamily",    "subfamily_authorship"),
    ("tribe",       "tribe",        "tribe_authorship"),
    ("subtribe",    "subtribe",     "subtribe_authorship"),
    ("genus",       "genus",        "genus_authorship"),
    ("subgenus",    "subgenus",     "subgenus_authorship"),
    ("species",     "species_name", "specific_epithet_authorship"),
]


def _ensure_parent_rows(
    session: Session,
    fields: dict,
    nomenclatural_code: str | None = None,
    mismatches: list[str] | None = None,
) -> int | None:
    """Create all missing ancestor rows and return the immediate parent taxon ID.

    Walks _RANK_CHAIN from highest to lowest rank, stopping when it reaches
    the target rank so the caller can create that row itself.

    Lookup priority to avoid creating duplicates when TW's rank for an ancestor
    differs from what is already in the DB:
      1. OTU ID (authoritative, rank-independent)
      2. (scientific_name, taxon_rank) exact match
      3. scientific_name alone — only for order/suborder/superfamily, where
         homonyms across ranks are essentially impossible.

    Import policy: only fills NULL fields on existing rows.  If a non-NULL
    field differs from the import value, a message is appended to mismatches
    (if provided) but the local value is left unchanged.
    """
    target_rank = fields.get("taxon_rank", "")
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

        existing = None
        if otu_id:
            existing = session.query(Taxon).filter(Taxon.taxonworks_otu_id == otu_id).first()
        if existing is None:
            existing = (
                session.query(Taxon)
                .filter(Taxon.scientific_name == name, Taxon.taxon_rank == rank_name)
                .first()
            )
        if existing is None and rank_name in ("order", "suborder", "superfamily"):
            existing = session.query(Taxon).filter(Taxon.scientific_name == name).first()

        if not existing:
            existing = Taxon(
                scientific_name=name,
                taxon_rank=rank_name,
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
            if existing.taxon_rank != rank_name and mismatches is not None:
                mismatches.append(
                    f"{name}: rank is {existing.taxon_rank!r} locally, "
                    f"import says {rank_name!r}"
                )
            if parent_id is not None:
                if existing.parent_name_usage_id is None:
                    existing.parent_name_usage_id = parent_id
                    dirty = True
                elif existing.parent_name_usage_id != parent_id and mismatches is not None:
                    local_p = session.get(Taxon, existing.parent_name_usage_id)
                    import_p = session.get(Taxon, parent_id)
                    lname = local_p.scientific_name if local_p else f"id:{existing.parent_name_usage_id}"
                    iname = import_p.scientific_name if import_p else f"id:{parent_id}"
                    mismatches.append(
                        f"{name}: parent is {lname!r} locally, import says {iname!r}"
                    )
            if auth:
                if not existing.scientific_name_authorship:
                    existing.scientific_name_authorship = auth
                    dirty = True
                elif existing.scientific_name_authorship != auth and mismatches is not None:
                    mismatches.append(
                        f"{name}: authorship is {existing.scientific_name_authorship!r} locally, "
                        f"import says {auth!r}"
                    )
            if otu_id:
                if not existing.taxonworks_otu_id:
                    existing.taxonworks_otu_id = otu_id
                    dirty = True
                elif existing.taxonworks_otu_id != otu_id and mismatches is not None:
                    mismatches.append(
                        f"{name}: TaxonWorks OTU ID is {existing.taxonworks_otu_id!r} locally, "
                        f"import says {otu_id!r}"
                    )
            if nomen_code:
                if not existing.nomenclatural_code:
                    existing.nomenclatural_code = nomen_code
                    dirty = True
                elif existing.nomenclatural_code != nomen_code and mismatches is not None:
                    mismatches.append(
                        f"{name}: nomenclatural code is {existing.nomenclatural_code!r} locally, "
                        f"import says {nomen_code!r}"
                    )
            if dirty:
                existing.updated_at = _utcnow()
                session.flush()

        parent_id = existing.id

    return parent_id


def update_taxon(
    session: Session,
    taxon_id: int,
    *,
    scientific_name: str,
    taxon_rank: str,
    scientific_name_authorship: str | None,
    parent_name_usage_id: int | None,
    accepted_name_usage_id: int | None,
    nomenclatural_code: str | None,
    taxonworks_otu_id: int | None,
) -> "Taxon":
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    t.scientific_name = scientific_name
    t.taxon_rank = taxon_rank
    t.scientific_name_authorship = scientific_name_authorship or None
    t.nomenclatural_code = nomenclatural_code or None
    t.taxonworks_otu_id = taxonworks_otu_id
    t.updated_at = _utcnow()
    session.flush()
    # Synonymy + parent are derived/cascaded, never set independently here:
    # making it a synonym copies the accepted name's parent (and flattens any
    # chain); making it accepted re-parents it and cascades to its synonyms.
    if accepted_name_usage_id is not None:
        synonymize(session, name_id=taxon_id, accepted_id=accepted_name_usage_id)
    else:
        make_accepted(session, taxon_id)
        reparent(session, taxon_id=taxon_id, new_parent_id=parent_name_usage_id)
    return t


def delete_taxon(session: Session, taxon_id: int) -> None:
    """Delete a taxon. Raises ValueError if it has children, synonyms, or determinations."""
    from app.models import TaxonDetermination
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    child_count = session.query(Taxon).filter(Taxon.parent_name_usage_id == taxon_id).count()
    if child_count:
        raise ValueError(f"Cannot delete: taxon has {child_count} child taxon(s)")
    syn_count = session.query(Taxon).filter(Taxon.accepted_name_usage_id == taxon_id).count()
    if syn_count:
        raise ValueError(f"Cannot delete: taxon has {syn_count} synonym(s)")
    det_count = session.query(TaxonDetermination).filter(TaxonDetermination.taxon_id == taxon_id).count()
    if det_count:
        raise ValueError(f"Cannot delete: taxon is used in {det_count} determination(s)")
    session.delete(t)
    session.flush()


# ---------------------------------------------------------------------------
# Synonym integrity
#
# Synonymy is encoded solely by acceptedNameUsageID; a synonym shares its
# accepted name's parent (the classification lives on the concept). Two BEFORE
# triggers (migration 0031) make the synonym-side bad state unreachable from any
# write path. These service ops are the single writers that do the multi-row
# work keeping it true across edits — every parent / accepted-link mutation on an
# existing taxon goes through synonymize / make_accepted / reparent.
# ---------------------------------------------------------------------------

def _terminal_accepted(session: Session, taxon: "Taxon") -> "Taxon":
    """Follow acceptedNameUsageID to the terminal accepted name (no chains)."""
    cur, seen = taxon, {taxon.id}
    while cur.accepted_name_usage_id is not None:
        nxt = session.get(Taxon, cur.accepted_name_usage_id)
        if nxt is None or nxt.id in seen:
            break
        cur, _ = nxt, seen.add(nxt.id)
    return cur


def synonymize(session: Session, *, name_id: int, accepted_id: int) -> "Taxon":
    """Make ``name_id`` a synonym of ``accepted_id``.

    Resolves the target to its terminal accepted name (GBIF "chained synonym"
    rule — never a synonym of a synonym), copies that name's parent onto the
    synonym, and re-points the name's own existing synonyms onto the same
    accepted name so no chain forms. Atomic; caller wraps in a transaction.
    """
    name = session.get(Taxon, name_id)
    target = session.get(Taxon, accepted_id)
    if name is None or target is None:
        raise ValueError("name or accepted taxon not found")
    terminal = _terminal_accepted(session, target)
    if terminal.id == name.id:
        raise ValueError("a name cannot be a synonym of itself")
    if name.nomenclatural_code != terminal.nomenclatural_code:
        raise ValueError(
            "synonym and accepted name must share the nomenclatural code "
            f"({name.nomenclatural_code} vs {terminal.nomenclatural_code})"
        )
    has_children = (
        session.query(Taxon)
        .filter(Taxon.parent_name_usage_id == name.id,
                Taxon.accepted_name_usage_id.is_(None))
        .count()
    )
    if has_children:
        raise ValueError("cannot synonymize a name that has subordinate taxa")
    # The name itself plus its current synonyms all move onto `terminal`.
    movers = [name] + (
        session.query(Taxon).filter(Taxon.accepted_name_usage_id == name.id).all()
    )
    for m in movers:
        m.accepted_name_usage_id = terminal.id
        m.parent_name_usage_id = terminal.parent_name_usage_id
        m.updated_at = _utcnow()
    session.flush()
    return name


def make_accepted(session: Session, taxon_id: int) -> "Taxon":
    """Clear a taxon's synonym link, making it an accepted name (keeps its parent)."""
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    if t.accepted_name_usage_id is not None:
        t.accepted_name_usage_id = None
        t.updated_at = _utcnow()
        session.flush()
    return t


def reparent(session: Session, *, taxon_id: int, new_parent_id: int | None) -> "Taxon":
    """Re-parent an accepted name, cascading the new parent to its synonyms.

    Synonyms share their accepted name's parent, so re-homing an accepted name
    must move its synonyms too — the one drift vector the write-time triggers
    cannot catch (re-parenting never touches the synonym rows).
    """
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    if t.accepted_name_usage_id is not None:
        raise ValueError("cannot reparent a synonym directly — reparent its accepted name")
    t.parent_name_usage_id = new_parent_id
    t.updated_at = _utcnow()
    session.flush()  # accepted row first, so the synonym writes satisfy the trigger
    for syn in session.query(Taxon).filter(Taxon.accepted_name_usage_id == taxon_id).all():
        syn.parent_name_usage_id = new_parent_id
        syn.updated_at = _utcnow()
    session.flush()
    return t


def verify_taxon_consistency(session: Session) -> list[dict]:
    """Audit taxon hierarchy/synonymy invariants; return a list of violations.

    Read-only — run manually (Taxonomy-tab button / tests), not at startup. It
    catches the drift the write-time triggers structurally cannot (chiefly a
    synonym whose stored parent no longer matches its accepted name's, e.g. from
    a raw-SQL re-parent). Issue names follow GBIF's NameUsageIssue vocabulary
    where they map; SYNONYM_PARENT_MISMATCH is this project's stricter rule.
    """
    issues: list[dict] = []
    taxa = session.query(Taxon).all()
    by_id = {t.id: t for t in taxa}
    for t in taxa:
        if t.parent_name_usage_id is not None and t.parent_name_usage_id not in by_id:
            issues.append({"issue": "PARENT_NAME_USAGE_ID_INVALID", "taxon_id": t.id,
                           "name": t.scientific_name,
                           "detail": f"parentNameUsageID {t.parent_name_usage_id} does not resolve"})
        if t.accepted_name_usage_id is None:
            continue
        acc = by_id.get(t.accepted_name_usage_id)
        if acc is None:
            issues.append({"issue": "ACCEPTED_NAME_USAGE_ID_INVALID", "taxon_id": t.id,
                           "name": t.scientific_name,
                           "detail": f"acceptedNameUsageID {t.accepted_name_usage_id} does not resolve"})
            continue
        if acc.accepted_name_usage_id is not None:
            issues.append({"issue": "CHAINED_SYNONYM", "taxon_id": t.id,
                           "name": t.scientific_name,
                           "detail": f"accepted name '{acc.scientific_name}' is itself a synonym"})
        if t.parent_name_usage_id != acc.parent_name_usage_id:
            issues.append({"issue": "SYNONYM_PARENT_MISMATCH", "taxon_id": t.id,
                           "name": t.scientific_name,
                           "detail": f"parent differs from accepted name '{acc.scientific_name}'"})
    return issues


def create_taxon_direct(
    session: Session,
    *,
    scientific_name: str,
    taxon_rank: str,
    scientific_name_authorship: str | None = None,
    parent_name_usage_id: int | None = None,
    accepted_name_usage_id: int | None = None,
    nomenclatural_code: str | None = None,
    taxonworks_otu_id: int | None = None,
) -> "Taxon":
    """Create a taxon row directly from fully specified fields (no parent inference).

    When an accepted name is given the row is a synonym, so its parent is taken
    from the (terminal) accepted name rather than the passed value — synonyms
    share their accepted name's parent.
    """
    if accepted_name_usage_id is not None:
        acc = session.get(Taxon, accepted_name_usage_id)
        if acc is None:
            raise ValueError("accepted taxon not found")
        terminal = _terminal_accepted(session, acc)
        accepted_name_usage_id = terminal.id
        parent_name_usage_id = terminal.parent_name_usage_id
    t = Taxon(
        scientific_name=scientific_name,
        taxon_rank=taxon_rank,
        scientific_name_authorship=scientific_name_authorship or None,
        parent_name_usage_id=parent_name_usage_id,
        accepted_name_usage_id=accepted_name_usage_id,
        nomenclatural_code=nomenclatural_code or None,
        taxonworks_otu_id=taxonworks_otu_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


def seed_root_taxa(session: Session) -> None:
    """Ensure one root taxon per nomenclatural code exists.

    These are the only taxa allowed to have parentNameUsageID = NULL.
    Called once at startup; idempotent.
    """
    _ROOTS = [
        ("Animalia", "kingdom", "ICZN"),
        ("Plantae",  "kingdom", "ICN"),
        ("Fungi",    "kingdom", "ICN"),
        ("Bacteria", "kingdom", "ICNP"),
        ("Viruses",  "kingdom", "ICVCN"),
    ]
    for sci_name, rank, code in _ROOTS:
        exists = (
            session.query(Taxon)
            .filter(Taxon.scientific_name == sci_name, Taxon.taxon_rank == rank)
            .first()
        )
        if not exists:
            session.add(Taxon(
                scientific_name=sci_name,
                taxon_rank=rank,
                nomenclatural_code=code,
                parent_name_usage_id=None,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            ))
    session.flush()


def ensure_higher_taxa(session: Session) -> int:
    """No-op in the DwC parent-link model.

    Parent rows are created during TW import via _ensure_parent_rows; no
    startup backfill is needed.  Kept for API compatibility with main.py.
    """
    return 0


def get_or_create_from_tw_data(
    session: Session,
    tw: dict,
    otu_id: int | None = None,
    mismatches: list[str] | None = None,
) -> Taxon:
    """Find the matching local Taxon or create it from an augmented TW record.

    Lookup priority: OTU ID first, then (scientific_name, taxon_rank).
    All ancestor rows are created first via _ensure_parent_rows.

    Synonym handling: if fetch_full_classification detected the name is invalid
    (cached_is_valid=False), tw contains '_valid_tw_data' and '_valid_otu_id'.
    The valid taxon is imported first; the synonym row gets accepted_name_usage_id
    set to point at it.

    Import policy: only fills NULL fields on existing rows.  Conflicts with
    non-NULL local values are appended to mismatches (if provided).
    """
    # If this is a synonym, ensure the valid (accepted) taxon exists first.
    accepted_id: int | None = None
    acc_parent_id: int | None = None   # a synonym shares its accepted name's parent
    valid_tw = tw.get("_valid_tw_data")
    if valid_tw:
        valid_taxon = get_or_create_from_tw_data(
            session, valid_tw, otu_id=tw.get("_valid_otu_id"), mismatches=mismatches
        )
        accepted_id = valid_taxon.id
        acc_parent_id = valid_taxon.parent_name_usage_id

    fields = _fields_from_tw(tw)
    sci_name = fields["scientific_name"]
    rank = fields["taxon_rank"]
    nomen_code = fields.get("nomenclatural_code")

    # Ensure all ancestor rows exist; get the immediate parent ID.
    parent_id = _ensure_parent_rows(
        session, fields, nomenclatural_code=nomen_code, mismatches=mismatches
    )

    existing = None
    if otu_id:
        existing = session.query(Taxon).filter(Taxon.taxonworks_otu_id == otu_id).first()
    if existing is None:
        existing = (
            session.query(Taxon)
            .filter(Taxon.scientific_name == sci_name, Taxon.taxon_rank == rank)
            .first()
        )
    if existing:
        dirty = False
        if existing.taxon_rank != rank and mismatches is not None:
            mismatches.append(
                f"{sci_name}: rank is {existing.taxon_rank!r} locally, "
                f"import says {rank!r}"
            )
        if otu_id and not existing.taxonworks_otu_id:
            existing.taxonworks_otu_id = otu_id
            dirty = True
        if accepted_id:
            if not existing.accepted_name_usage_id:
                existing.accepted_name_usage_id = accepted_id
                existing.parent_name_usage_id = acc_parent_id   # Inv1: share accepted's parent
                dirty = True
            elif existing.accepted_name_usage_id != accepted_id and mismatches is not None:
                local_acc = session.get(Taxon, existing.accepted_name_usage_id)
                import_acc = session.get(Taxon, accepted_id)
                lname = local_acc.scientific_name if local_acc else f"id:{existing.accepted_name_usage_id}"
                iname = import_acc.scientific_name if import_acc else f"id:{accepted_id}"
                mismatches.append(
                    f"{sci_name}: accepted name is {lname!r} locally, import says {iname!r}"
                )
        # Parent backfill applies to accepted names only; a synonym's parent is
        # taken from its accepted name above.
        if parent_id is not None and not accepted_id:
            if existing.parent_name_usage_id is None:
                existing.parent_name_usage_id = parent_id
                dirty = True
            elif existing.parent_name_usage_id != parent_id and mismatches is not None:
                local_p = session.get(Taxon, existing.parent_name_usage_id)
                import_p = session.get(Taxon, parent_id)
                lname = local_p.scientific_name if local_p else f"id:{existing.parent_name_usage_id}"
                iname = import_p.scientific_name if import_p else f"id:{parent_id}"
                mismatches.append(
                    f"{sci_name}: parent is {lname!r} locally, import says {iname!r}"
                )
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
        scientific_name_authorship=fields.get("scientific_name_authorship"),
        parent_name_usage_id=(acc_parent_id if accepted_id else parent_id),
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
    mismatches: list[str] | None = None,
) -> Taxon:
    """Find or create a local Taxon from a POWO-derived field dict.

    powo_fields comes from powo.fields_from_powo(powo_record).
    Creates family and genus ancestor rows if missing, then the species row.
    nomenclatural_code is propagated to all rows from the POWO record.

    Synonym handling: if powo_fields["is_synonym"] is True and accepted_fields
    is provided, the accepted name is created first and the synonym row is
    linked to it via accepted_name_usage_id.

    Matching key: (scientific_name, taxon_rank) — same as TW imports.

    Import policy: only fills NULL fields on existing rows.  Conflicts with
    non-NULL local values are appended to mismatches (if provided).
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
    acc_parent_id: int | None = None   # a synonym shares its accepted name's parent
    if is_synonym and accepted_fields:
        accepted_taxon = get_or_create_from_powo_data(
            session, accepted_fields, mismatches=mismatches
        )
        acc_parent_id = accepted_taxon.parent_name_usage_id

    # POWO gives family → genus → species (no subfamily/tribe available).
    ancestor_fields: dict = {"taxon_rank": rank, "nomenclatural_code": nomen_code}
    if family:
        ancestor_fields["family"] = family
    if genus and rank != "genus":
        ancestor_fields["genus"] = genus

    # Populate authorship for each ancestor rank using the classification-derived
    # rank → author map from the POWO record. Use a separate variable: reusing
    # `auth` here would clobber the *target* taxon's authorship captured above,
    # leaving directly-imported genera (and mis-attributing infraspecific taxa)
    # with the last-iterated ancestor's value instead of their own.
    ancestor_authorships: dict[str, str] = powo_fields.get("ancestor_authorships") or {}
    for rank_name, _field_key, auth_key in _RANK_CHAIN:
        anc_auth = ancestor_authorships.get(rank_name)
        if anc_auth:
            ancestor_fields[auth_key] = anc_auth
    # For infraspecific taxa, extract the parent species name so _ensure_parent_rows
    # creates the species row as the immediate parent instead of stopping at genus.
    if rank in ("subspecies", "variety", "subvariety", "form", "subform") and genus:
        # sci_name format: "Genus epithet subsp./var./f. infraepithet"
        # (or "Genus (Subgenus) epithet …"); grab the first lowercase word after the genus.
        rest = sci_name[len(genus):].strip()
        m = re.match(r"(?:\([^)]+\)\s+)?([a-z×][a-z\-]*)", rest)
        if m:
            ancestor_fields["species_name"] = f"{genus} {m.group(1)}"

    parent_id = _ensure_parent_rows(
        session, ancestor_fields, nomenclatural_code=nomen_code, mismatches=mismatches
    )

    existing = (
        session.query(Taxon)
        .filter(Taxon.scientific_name == sci_name, Taxon.taxon_rank == rank)
        .first()
    )
    if existing:
        dirty = False
        if auth:
            if not existing.scientific_name_authorship:
                existing.scientific_name_authorship = auth
                dirty = True
            elif existing.scientific_name_authorship != auth and mismatches is not None:
                mismatches.append(
                    f"{sci_name}: authorship is {existing.scientific_name_authorship!r} locally, "
                    f"import says {auth!r}"
                )
        # Parent backfill applies to accepted names only; a synonym's parent is
        # taken from its accepted name below.
        if parent_id is not None and not accepted_taxon:
            if existing.parent_name_usage_id is None:
                existing.parent_name_usage_id = parent_id
                dirty = True
            elif existing.parent_name_usage_id != parent_id and mismatches is not None:
                local_p = session.get(Taxon, existing.parent_name_usage_id)
                import_p = session.get(Taxon, parent_id)
                lname = local_p.scientific_name if local_p else f"id:{existing.parent_name_usage_id}"
                iname = import_p.scientific_name if import_p else f"id:{parent_id}"
                mismatches.append(
                    f"{sci_name}: parent is {lname!r} locally, import says {iname!r}"
                )
        if nomen_code and not existing.nomenclatural_code:
            existing.nomenclatural_code = nomen_code
            dirty = True
        if accepted_taxon:
            if not existing.accepted_name_usage_id:
                existing.accepted_name_usage_id = accepted_taxon.id
                existing.parent_name_usage_id = acc_parent_id   # Inv1: share accepted's parent
                dirty = True
            elif existing.accepted_name_usage_id != accepted_taxon.id and mismatches is not None:
                local_acc = session.get(Taxon, existing.accepted_name_usage_id)
                lname = local_acc.scientific_name if local_acc else f"id:{existing.accepted_name_usage_id}"
                mismatches.append(
                    f"{sci_name}: accepted name is {lname!r} locally, "
                    f"import says {accepted_taxon.scientific_name!r}"
                )
        if dirty:
            existing.updated_at = _utcnow()
            session.flush()
        return existing

    t = Taxon(
        scientific_name=sci_name,
        taxon_rank=rank,
        scientific_name_authorship=auth,
        parent_name_usage_id=(acc_parent_id if accepted_taxon else parent_id),
        accepted_name_usage_id=accepted_taxon.id if accepted_taxon else None,
        nomenclatural_code=nomen_code,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t

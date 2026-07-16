from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Taxon
from app.models.base import _utcnow


from app.vocab import IDENTIFICATION_QUALIFIERS, NOMENCLATURAL_CODES

TAXON_RANKS: list[str] = [
    "kingdom", "phylum", "subphylum", "class", "subclass",
    "superorder", "order", "suborder", "superfamily",
    "family", "subfamily", "supertribe", "tribe", "subtribe",
    "genus", "subgenus", "section", "subsection", "species", "subspecies",
    "variety", "subvariety", "form", "subform",
]

# A rank belongs to a NOMENCLATURAL CODE, not to a global vocabulary — TaxonWorks models
# the four codes as four separate hierarchies (app/models/nomenclatural_rank/{iczn,icn,
# icnp,icvcn}/, TW @ 897f385), and the same rank NAME can be a different rank in each.
# TAXON_RANKS above stays the one high→low ordering (hierarchy validation indexes into it);
# these are the subsets that may be SELECTED for a taxon governed by each code. Each list is
# a subsequence of TAXON_RANKS, so ordering comparisons remain valid across codes.
#
# This is a curated subset of TW's ranks, not a mirror: we model the ranks this collection
# actually uses. What it must get right is the code SPLIT — offering 'variety' for a beetle
# (ICZN has no rank below subspecies) or 'superfamily' for a plant (ICN's family group is
# only family/subfamily/tribe/subtribe) invites a silently wrong name.
RANKS_BY_CODE: dict[str, list[str]] = {
    # Zoology: no infraspecific ranks below subspecies, no botanical genus-group sections.
    "ICZN": [
        "kingdom", "phylum", "subphylum", "class", "subclass",
        "superorder", "order", "suborder", "superfamily",
        "family", "subfamily", "supertribe", "tribe", "subtribe",
        "genus", "subgenus", "species", "subspecies",
    ],
    # Botany/mycology: no super-ranks in the family group, but sections (Taraxacum sect.
    # Ruderalia) and the infraspecific series var./f. that zoology lacks.
    "ICN": [
        "kingdom", "phylum", "subphylum", "class", "subclass",
        "order", "suborder",
        "family", "subfamily", "tribe", "subtribe",
        "genus", "subgenus", "section", "subsection", "species", "subspecies",
        "variety", "subvariety", "form", "subform",
    ],
    "ICNP": [
        "kingdom", "phylum", "class", "subclass", "order", "suborder",
        "family", "subfamily", "tribe", "subtribe",
        "genus", "subgenus", "species", "subspecies",
    ],
    "ICVCN": [
        "kingdom", "phylum", "class", "order",
        "family", "subfamily", "genus", "species",
    ],
}

# Display order for rank-selection dropdowns: the ranks used daily for beetle
# work go on top (finest first), then the rarer higher categories descend the
# tree. This is a UI-ordering concern only — TAXON_RANKS stays in semantic
# high→low order because hierarchy validation relies on TAXON_RANKS.index().
TAXON_RANKS_BY_USE: list[str] = [
    "subspecies", "species", "subgenus", "genus",
    "variety", "subvariety", "form", "subform",
    "section", "subsection",
    "subtribe", "tribe", "supertribe",
    "subfamily", "family", "superfamily",
    "suborder", "order", "superorder",
    "subclass", "class", "subphylum", "phylum", "kingdom",
]
# Guard against drift: both lists must cover exactly the same ranks, and every per-code
# subset must draw only from them.
assert set(TAXON_RANKS_BY_USE) == set(TAXON_RANKS)
assert all(set(rs) <= set(TAXON_RANKS) for rs in RANKS_BY_CODE.values())


def ranks_for(nomenclatural_code: str | None) -> list[str]:
    """The ranks selectable under *nomenclatural_code*, in TAXON_RANKS_BY_USE display order.

    An unknown/absent code yields every rank — the caller does not yet know the code (a new
    taxon before its parent is chosen), and refusing to offer anything would be worse than
    offering too much. The editor re-filters as soon as a parent supplies the code, and
    validate() refuses a rank the code does not have.
    """
    allowed = RANKS_BY_CODE.get((nomenclatural_code or "").strip().upper())
    if not allowed:
        return list(TAXON_RANKS_BY_USE)
    return [r for r in TAXON_RANKS_BY_USE if r in set(allowed)]


@dataclass(frozen=True)
class TaxonOption:
    id: int
    label: str
    taxon_rank: str = ""
    nomenclatural_code: str | None = None



def _require_code(nomenclatural_code: str | None, context: str) -> str:
    """Every taxon row must carry a nomenclatural code (#96; DB CHECK + NOT NULL, mig 0054).

    Raised — never defaulted. The code is a property of the source (WCVP indexes only
    ICN-governed names; TaxonWorks reports its own) or inherited from the parent chain, so a
    missing one means an importer failed to supply it, and guessing would silently mislabel a
    name's governing code. Fails here, loudly, rather than as an opaque IntegrityError.
    """
    code = (nomenclatural_code or "").strip().upper()
    if not code:
        raise ValueError(
            f"{context}: no nomenclatural code. It is inherited from the parent or supplied "
            "by the source; it is never guessed."
        )
    if code not in NOMENCLATURAL_CODES:
        raise ValueError(
            f"{context}: nomenclatural code {code!r} is not one of "
            f"{', '.join(NOMENCLATURAL_CODES)}."
        )
    return code


def format_scientific_name(taxon: Taxon) -> str:
    """Return display name: scientificName + authorship (if present).

    In the atomic-name model (Epic #30) ``scientific_name`` is the fully composed
    name maintained by compose_scientific_name() — a subgenus row already stores
    ``Genus (Subgenus)``, so no rank-specific reconstruction is needed here.
    """
    name = taxon.scientific_name or ""
    auth = taxon.scientific_name_authorship or ""
    if name and auth:
        return f"{name} {auth}"
    return name or f"taxon #{taxon.id}"


# ---------------------------------------------------------------------------
# Determination rendering (Epic #30, Phase 5)
# ---------------------------------------------------------------------------
# A determination freezes dwc:verbatimIdentification = the composed name at save
# time (qualifier-free); the open-nomenclature qualifier (cf./aff./sp./…) lives
# separately in dwc:identificationQualifier. Rendering follows ONE rule — the
# qualifier always sits right after the genus-group — so there is no per-qualifier
# logic and no `sp.` special case (an "sp." determination simply points at a genus
# row, whose composed name is the bare genus, leaving an empty rest).

def split_genus_group(name: str) -> tuple[str, str]:
    """Split a composed bare name into ``(genus_group, rest)`` where genus_group
    is the genus plus its ``(Subgenus)`` if present.

        'Otiorhynchus forticollis'         → ('Otiorhynchus', 'forticollis')
        'Otiorhynchus (Nihus) forticollis' → ('Otiorhynchus (Nihus)', 'forticollis')
        'Otiorhynchus'                     → ('Otiorhynchus', '')
        'Otiorhynchus (Nihus)'             → ('Otiorhynchus (Nihus)', '')
    """
    parts = (name or "").split()
    if not parts:
        return "", ""
    if len(parts) >= 2 and parts[1].startswith("("):
        return f"{parts[0]} {parts[1]}", " ".join(parts[2:])
    return parts[0], " ".join(parts[1:])


# ---------------------------------------------------------------------------
# Rendering a scientific name as HTML (the single owner of italics)
# ---------------------------------------------------------------------------
# Every surface that shows a name goes through here, so the convention is applied once:
#
#   * ONLY the genus group and below is italic. A family, tribe or order is NOT
#     (Curculionidae, Otiorhynchini, Coleoptera are roman).
#   * The AUTHORSHIP is never italic — "Otiorhynchus armadillo (Rossi, 1792)".
#   * Connecting terms and open-nomenclature qualifiers are never italic:
#     "Taraxacum sect. Ruderalia", "Achillea millefolium var. alpina",
#     "Otiorhynchus cf. forticollis", "Otiorhynchus sp."
#
# Previously each UI site rolled its own: taxon_search wrapped the WHOLE label — authorship
# included — in <i>, and did so regardless of rank, so every family and tribe in the dropdown
# was italicised and every author with it.

# Tokens that sit inside a name but are not part of it, so they stay roman.
_ROMAN_TOKENS = frozenset(
    {"subg.", "sect.", "subsect.", "ser.", "subser.",
     "subsp.", "ssp.", "var.", "subvar.", "f.", "subf.", "forma", "nothosubsp.",
     "x", "×"}                                   # hybrid marker
    | set(IDENTIFICATION_QUALIFIERS)             # cf. aff. nr. agg. gr. ? sp. spp. indet.
)


def rank_is_italic(taxon_rank: str | None) -> bool:
    """True for the genus group and below — the only ranks written in italics."""
    r = (taxon_rank or "").strip().lower()
    if r not in TAXON_RANKS:
        return False                              # unknown rank: do not assert a convention
    return TAXON_RANKS.index(r) >= TAXON_RANKS.index("genus")


def scientific_name_html(
    name: str,
    taxon_rank: str | None = None,
    authorship: str | None = None,
) -> str:
    """A composed name as display HTML: italic where the code says italic, and nowhere else.

    `name` is the composed bare name (or a rendered determination, qualifier included).
    Escaping is done here — callers pass raw text, never markup.
    """
    from html import escape as _esc

    text = (name or "").strip()
    if not text:
        return ""

    if not rank_is_italic(taxon_rank):
        out = _esc(text)                          # family, tribe, order … all roman
    else:
        # Italicise runs of name tokens; leave connectors/qualifiers roman between them.
        parts: list[str] = []
        run: list[str] = []

        def _flush() -> None:
            if run:
                parts.append(f"<i>{_esc(' '.join(run))}</i>")
                run.clear()

        for tok in text.split():
            if tok.lower() in _ROMAN_TOKENS:
                _flush()
                parts.append(_esc(tok))
            else:
                run.append(tok)
        _flush()
        out = " ".join(parts)

    auth = (authorship or "").strip()
    return f"{out} {_esc(auth)}" if auth else out


def render_identification(name: str, qualifier: str | None = None) -> str:
    """Render a frozen determination name with its qualifier inserted right after
    the genus-group: ``Otiorhynchus cf. forticollis``, ``Otiorhynchus (Nihus) aff.
    forticollis``, ``Otiorhynchus sp.`` (genus row → empty rest), ``Otiorhynchus
    cf.`` (rare, no rest). Empty parts are dropped; no per-qualifier special-casing.
    """
    genus_group, rest = split_genus_group(name or "")
    parts = [p for p in (genus_group, (qualifier or "").strip(), rest) if p]
    return " ".join(parts)


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
_ICN_INFRA_CONNECTOR = {
    "subspecies": "subsp.", "variety": "var.", "subvariety": "subvar.",
    "form": "f.", "subform": "subf.",
}

# Genus-group connectors, the same idea one rank-group up. The two codes write the genus
# group differently and BOTH halves matter:
#   ICZN  brackets the subgenus and carries it into the binomial —
#         'Otiorhynchus (Nihus)', 'Otiorhynchus (Nihus) armadillo'
#   ICN   spells the connector out and does NOT carry it into the binomial —
#         'Taraxacum subg. Palustria', 'Taraxacum sect. Ruderalia', but the species under
#         either is plain 'Taraxacum officinale' (a botanical binomial is genus + epithet).
_ICN_GENUS_CONNECTOR = {
    "subgenus": "subg.", "section": "sect.", "subsection": "subsect.",
}


def _is_zoological(nomenclatural_code: str | None) -> bool:
    return (nomenclatural_code or "").upper() == "ICZN"


def _infra_connector(rank: str, nomenclatural_code: str | None) -> str:
    if _is_zoological(nomenclatural_code):
        return ""  # ICZN: bare trinomial
    return _ICN_INFRA_CONNECTOR.get(rank, "")


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

    # The subgenus rides along inside the binomial in ZOOLOGY only: 'Otiorhynchus (Nihus)
    # armadillo'. A botanical binomial is genus + epithet — the subgenus/section is a
    # classificatory rank, not part of the name — so `sub` stays empty under ICN.
    zoological = _is_zoological(taxon.nomenclatural_code)
    sub = f" ({subgenus})" if (subgenus and zoological) else ""

    if rank == "subgenus" and zoological:
        return f"{genus} ({element})".strip() if genus else f"({element})"

    if rank in _ICN_GENUS_CONNECTOR:   # subgenus / section / subsection, botanical form
        connector = _ICN_GENUS_CONNECTOR[rank]
        parts = [p for p in (genus, connector, element) if p]
        return " ".join(parts)

    if rank == "species":
        return f"{genus}{sub} {element}".strip() if genus else element

    if rank in ("subspecies", "variety", "subvariety", "form", "subform"):
        # An infraspecific name is built from its SPECIES ancestor. If the chain has no species
        # row the name cannot be composed — and the one thing we must not do is interpolate the
        # missing part, which silently produced names like "Carabus (Megodontus) None germarii"
        # (a source that parents subspecies straight under a subgenus). Drop the missing piece;
        # the caller is responsible for supplying the species parent (name_source.chain_for
        # inserts it), and a bare-epithet name is a visible fault, not a plausible-looking lie.
        head_parts = [p for p in (genus, sub.strip() or None, species_epithet) if p]
        head = " ".join(head_parts)
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


def _compose_transient(
    session: Session,
    *,
    name_element: str,
    taxon_rank: str,
    parent_id: int | None,
    nomenclatural_code: str | None = None,
) -> str:
    """Compose the full bare name for a row that does not exist yet.

    Used by the import/create paths to derive (and match on) the composed
    scientific_name *before* the row is inserted: build a throw-away Taxon with
    the element + parent and run the normal composer. The probe is never added
    to the session — compose only reads ancestors by FK id.
    """
    probe = Taxon(
        name_element=name_element,
        taxon_rank=taxon_rank,
        parent_name_usage_id=parent_id,
        nomenclatural_code=nomenclatural_code,
    )
    return compose_scientific_name(session, probe)


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


def element_from_name(scientific_name: str, taxon_rank: str) -> str:
    """Extract the atomic name element (this rank's own epithet/uninomial) from a
    full bare scientific name — the inverse of compose_scientific_name for
    well-formed input. Used when a writer is handed a full name but no explicit
    name_element (manual create with a typed full name, POWO's full `name`).

        'Achillea millefolium' / species     → 'millefolium'
        'Otiorhynchus (Nihus) crypticus' / species → 'crypticus'
        'Achillea millefolium alpina' / subspecies → 'alpina'
        'Otiorhynchus (Nihus)' / subgenus    → 'Nihus'
        'Asteraceae' / family                → 'Asteraceae'
    """
    name = (scientific_name or "").strip()
    rank = (taxon_rank or "").lower()
    if not name:
        return ""
    if rank in ("species", "subspecies", "variety", "subvariety", "form", "subform"):
        return name.split()[-1]
    if rank == "subgenus":
        # Stored as "Genus (Subgenus)" or bare "Subgenus"; the element is the
        # parenthetical when present (parse_scientific_name only sees a subgenus
        # token alongside a following epithet, so handle the bare form here).
        if "(" in name and ")" in name:
            return name[name.index("(") + 1: name.index(")")].strip()
        return name
    return name  # uninomial (genus, family, tribe, order, …): the whole name


def rank_from_parse(specific: str | None, infraspecific: str | None) -> str:
    """Rank implied by a parsed binomial: subspecies / species / genus."""
    if infraspecific:
        return "subspecies"
    if specific:
        return "species"
    return "genus"


def find_taxon_by_name(session: Session, scientific_name: str) -> "Taxon | None":
    """Find an accepted taxon by an **exact** match on dwc:scientificName.

    The name is expected authorship-free — `dwc:scientificName` is the *composed* name
    without authorship (CLAUDE.md §4), and authorship rides in `scientificNameAuthorship`.
    So there is deliberately **no** authorship-stripping fallback: the old "take the first
    two tokens" heuristic could not tell a trailing author from a trinomial epithet, and
    silently downgraded `Carabus baudii fenestrellanus` to the species `Carabus baudii` (#2).
    A caller with a dirty, authorship-laden name should be told to separate it (see
    `scientific_name_has_authorship`), not quietly mis-resolved.

    Returns None when the name is empty or matches more than one accepted row.
    """
    name = scientific_name.strip()
    if not name:
        return None

    results = (session.query(Taxon)
               .filter(Taxon.accepted_name_usage_id.is_(None))
               .filter(Taxon.scientific_name == name)
               .all())
    return results[0] if len(results) == 1 else None


_EPITHET_RE = re.compile(r"^[a-z][a-z-]*$")


def scientific_name_has_authorship(scientific_name: str) -> bool:
    """True if a ``scientificName`` string appears to carry authorship it should not.

    A clean DwC name (our stored form) is ``Genus`` + optional ``(Subgenus)`` + one or more
    lowercase epithets — nothing else. Any other token — a capitalised author, a year, ``&``,
    ``d'Orbigny`` — means authorship was left in the name column and belongs in
    ``scientificNameAuthorship``. Used to turn a resolution miss into an actionable message
    ("move the authorship out") instead of a silent failure (#2).

    Note `Carabus baudii fenestrellanus` (a clean trinomial, all lowercase epithets) is NOT
    flagged, while `Carabus violaceus de Geer` IS (``Geer`` is capitalised).
    """
    parts = scientific_name.split()
    if not parts:
        return False
    i = 1                                   # genus (capitalised) is fine
    if i < len(parts) and parts[i].startswith("("):
        i += 1                              # skip a (Subgenus) token
    return any(not _EPITHET_RE.match(tok) for tok in parts[i:])


# Lowercase nobiliary particles that open a surname ("de Geer", "van der Poll"). They look
# exactly like an epithet, so a name split on "first non-epithet token" cuts *after* them and
# leaves `Carabus violaceus de`. Trailing particles are therefore handed back to the author.
_AUTHOR_PARTICLES = {
    "de", "den", "der", "des", "di", "da", "del", "della", "dos", "du",
    "van", "von", "le", "la", "el", "ter", "ten", "af", "av",
}


def split_scientific_name_authorship(scientific_name: str) -> tuple[str, str]:
    """Split a ``scientificName`` that carries its authorship inline into ``(name, author)``.

    ``Bembidion minimum (Fabricius, 1792)`` → ``("Bembidion minimum", "(Fabricius, 1792)")``;
    a clean name returns ``(name, "")``. Same rule as ``scientific_name_has_authorship``, read
    forwards: genus, an optional ``(Subgenus)``, then lowercase epithets — the name **stops at
    the first token that is none of those**. A subgenus and an author's parenthesis look alike,
    so position decides: only the token straight after the genus can be a subgenus;
    ``(Fabricius, 1792)`` in third place is an author.

    The author is handed back rather than discarded because it is *evidence*: matched against
    the authorship of the taxon we resolve to, it confirms the identification — or, when it
    disagrees, says the row means a different beetle than the name alone suggests. Neither the
    name nor the author is ever *stored* from this string; the taxon the user picks supplies
    both.
    """
    parts = scientific_name.split()
    if not parts:
        return ("", "")
    kept = [parts[0]]
    i = 1
    if i < len(parts) and parts[i].startswith("(") and parts[i].endswith(")") \
            and _EPITHET_RE.match(parts[i][1:-1].lower() or "x"):
        kept.append(parts[i])                # a (Subgenus) directly after the genus
        i += 1
    for tok in parts[i:]:
        if not _EPITHET_RE.match(tok):
            break                            # first author token — the name ends here
        kept.append(tok)
    while len(kept) > 2 and kept[-1].lower() in _AUTHOR_PARTICLES:
        kept.pop()                           # "… violaceus de" → the "de" opens "de Geer"
    author = " ".join(parts[len(kept):])
    return (" ".join(kept), author)


def scientific_name_without_authorship(scientific_name: str) -> str:
    """The name half of :func:`split_scientific_name_authorship`."""
    return split_scientific_name_authorship(scientific_name)[0]


def authorship_matches(a: str, b: str) -> bool:
    """Do two authorship strings name the same author and year?

    Compared on their *words*, so the punctuation that varies between sources does not
    decide: ``(Fabricius, 1792)`` == ``Fabricius, 1792``. The brackets are dropped
    deliberately — they record that the species has since moved genus, which is a statement
    about the *combination*, not about who described it, and the two sources routinely
    disagree on them. An empty string on either side is not a match (nothing to compare):
    the caller decides what to do with "unknown", and it must not be "assume yes".
    """
    def _words(s: str) -> list[str]:
        return re.findall(r"[\w']+", (s or "").lower())
    wa, wb = _words(a), _words(b)
    return bool(wa) and wa == wb


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
            # Subgenus rows store the composed "Genus (Subgenus)" form.
            candidates.append((f"{genus} ({subgenus})", "subgenus"))
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

    rank = rank_from_parse(specific, infra)
    return {
        "name_element": element_from_name(bare, rank),
        "taxon_rank": rank,
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
    # Italics are a function of RANK (only the genus group and below), so the renderer needs it
    # — for this name and for the accepted name it may be shown beside.
    taxon_rank: str | None = None
    accepted_name: str | None = None          # bare, without authorship (for the HTML renderer)
    accepted_rank: str | None = None
    accepted_authorship: str | None = None


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
            taxon_rank=t.taxon_rank,
            accepted_name=(acc.scientific_name
                           if (is_syn and (acc := t.accepted_name_usage)) else None),
            accepted_rank=acc.taxon_rank if (is_syn and (acc := t.accepted_name_usage)) else None,
            accepted_authorship=(acc.scientific_name_authorship
                                 if (is_syn and (acc := t.accepted_name_usage)) else None),
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

def _fields_from_tw(tw: dict) -> dict:
    """Extract Taxon field values from an augmented TW taxon_names record.

    In the atomic-name model TW's ``name`` is already the rank's own element
    (epithet for species/infraspecific, uninomial otherwise), so it maps
    directly to ``name_element``; the composed ``scientific_name`` is derived
    later (once the parent chain is known) by the caller. Returns:
      name_element, taxon_rank, scientific_name_authorship, nomenclatural_code
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

    # nomenclatural_code: TW returns lowercase ("iczn", "icn", …); store uppercase.
    raw_code = tw.get("nomenclatural_code") or ""
    nomen_code = raw_code.upper() or None

    return {
        "name_element": tw.get("name") or "",
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
      2. (composed scientific_name, taxon_rank) exact match
      3. composed scientific_name alone — only for order/suborder/superfamily,
         where homonyms across ranks are essentially impossible.

    Each created/matched row carries its atomic ``name_element``; the row's
    ``scientific_name`` is composed from that element + its parent chain, so a
    subgenus ancestor is stored as ``Genus (Subgenus)`` and a species ancestor
    as ``Genus epithet``.

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

        # The atomic element: for the species ancestor it is the bare epithet
        # (TW supplies specific_epithet; otherwise the last token of the
        # "Genus epithet" field). Every other rank in the chain is a uninomial,
        # so the field value is itself the element.
        if rank_name == "species":
            element = fields.get("specific_epithet") or name.split()[-1]
        else:
            element = name
        sci = _compose_transient(
            session, name_element=element, taxon_rank=rank_name,
            parent_id=parent_id, nomenclatural_code=nomen_code,
        )

        auth = fields.get(auth_key)
        otu_id = fields.get(f"{field_key}_otu_id")
        # Same threading as otu_id, for the other external identity we may know about an
        # ancestor: WCVP resolves the genus row, so its IPNI id should not be dropped.
        anc_ipni = fields.get(f"{field_key}_ipni_id")

        existing = None
        if otu_id:
            existing = session.query(Taxon).filter(Taxon.taxonworks_otu_id == otu_id).first()
        if existing is None:
            existing = (
                session.query(Taxon)
                .filter(Taxon.scientific_name == sci, Taxon.taxon_rank == rank_name)
                .first()
            )
        if existing is None and rank_name in ("order", "suborder", "superfamily"):
            existing = session.query(Taxon).filter(Taxon.scientific_name == sci).first()

        if not existing:
            _require_code(nomen_code, f"ancestor {sci!r} [{rank_name}]")
            existing = Taxon(
                name_element=element,
                scientific_name=sci,
                taxon_rank=rank_name,
                scientific_name_authorship=auth or None,
                parent_name_usage_id=parent_id,
                taxonworks_otu_id=otu_id,
                ipni_id=anc_ipni,
                nomenclatural_code=nomen_code,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(existing)
            session.flush()
        else:
            dirty = False
            if not existing.name_element:
                existing.name_element = element
                dirty = True
            if existing.taxon_rank != rank_name and mismatches is not None:
                mismatches.append(
                    f"{sci}: rank is {existing.taxon_rank!r} locally, "
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
            if anc_ipni and not existing.ipni_id:
                existing.ipni_id = anc_ipni
                dirty = True
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
    taxon_rank: str,
    scientific_name_authorship: str | None,
    parent_name_usage_id: int | None,
    accepted_name_usage_id: int | None,
    nomenclatural_code: str | None,
    taxonworks_otu_id: int | None,
    name_element: str | None = None,
    scientific_name: str | None = None,
) -> "Taxon":
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    # Validate before mutating: an edit that clears the code must fail with a message naming
    # the taxon, not as an opaque IntegrityError with a half-updated object in the session.
    code = _require_code(nomenclatural_code, scientific_name or t.scientific_name or "taxon")
    if name_element is None:
        name_element = element_from_name(scientific_name or "", taxon_rank)
    t.name_element = name_element
    t.taxon_rank = taxon_rank
    t.scientific_name_authorship = scientific_name_authorship or None
    t.nomenclatural_code = code
    t.taxonworks_otu_id = taxonworks_otu_id
    t.updated_at = _utcnow()
    session.flush()
    # Synonymy and parent are routed through the chokepoint ops. Set the
    # synonym/accepted status first, then apply the (own-lineage) parent for BOTH
    # cases — a synonym carries its own parent, so its parent edit must be applied
    # too, not silently dropped (#71). reparent recomposes the subtree.
    if accepted_name_usage_id is not None:
        synonymize(session, name_id=taxon_id, accepted_id=accepted_name_usage_id)
    else:
        make_accepted(session, taxon_id)
    reparent(session, taxon_id=taxon_id, new_parent_id=parent_name_usage_id)
    return t


def delete_taxon(session: Session, taxon_id: int) -> None:
    """Delete a taxon. Raises ValueError if anything still references it."""
    from sqlalchemy import or_

    from app.models import BiologicalAssociation, TaxonDetermination
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
    # A host plant is referenced by biological_association. SQLAlchemy's default relationship
    # behaviour NULLs the child's FK before the FK's ON DELETE RESTRICT can fire, which would
    # orphan the association; only the exclusive-arc CHECK stops it, and it reports itself
    # rather than the real cause. Refuse here, with a message about the association (#101).
    assoc_count = session.query(BiologicalAssociation).filter(
        or_(BiologicalAssociation.subject_taxon_id == taxon_id,
            BiologicalAssociation.object_taxon_id == taxon_id)
    ).count()
    if assoc_count:
        raise ValueError(
            f"Cannot delete: taxon is used in {assoc_count} biological association(s)")
    session.delete(t)
    session.flush()


# ---------------------------------------------------------------------------
# Synonym integrity
#
# Synonymy is encoded solely by acceptedNameUsageID. In the atomic-name model
# (Epic #30) a name is parented under its OWN lineage — a synonym sits under its
# own genus (e.g. Curculio forticollis under Curculio), independent of its
# accepted name. So status is a one-field toggle: synonymize / make_accepted
# never touch parentNameUsageID and never rewrite the name. The only surviving
# write-time guard is trg_taxon_accepted_is_terminal (no chained synonyms,
# GBIF's rule); the strict synonym-parent-match trigger was retired in migration
# 0033. Every parent / accepted-link mutation on an existing taxon still routes
# through synonymize / make_accepted / reparent (chokepoint discipline).
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
    rule — never a synonym of a synonym) and re-points the name's own existing
    synonyms onto the same accepted name so no chain forms. The name keeps its
    OWN parentNameUsageID and its own scientific_name — in the atomic model a
    synonym is parented under its own lineage, so status is a pure one-field
    toggle with no name rewrite. Atomic; caller wraps in a transaction.
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
    # The name itself plus its current synonyms all re-point onto `terminal`.
    # Each keeps its own parentNameUsageID (own lineage) — only the link moves.
    movers = [name] + (
        session.query(Taxon).filter(Taxon.accepted_name_usage_id == name.id).all()
    )
    for m in movers:
        m.accepted_name_usage_id = terminal.id
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


def _rank_requires_parent(rank: str | None) -> bool:
    """True for ranks whose composed name is built from an ancestor (subgenus and
    everything below genus). For these a missing parent collapses the name to a
    bare epithet, so they may never be roots. Genus and higher are uninomials and
    may legitimately have no parent."""
    r = (rank or "").lower()
    if r not in TAXON_RANKS:
        return False
    return TAXON_RANKS.index(r) > TAXON_RANKS.index("genus")


def reparent(session: Session, *, taxon_id: int, new_parent_id: int | None) -> "Taxon":
    """Re-home a name under a new parent *within its own lineage*, then recompose
    its subtree.

    Works for accepted names **and synonyms** (#71): in the atomic model every
    name carries its own lineage, so a synonym's parent is independent of its
    accepted name and is legitimately editable — e.g. moving the synonym
    ``Curculio forticollis`` under its correct genus. Re-homing an *accepted*
    name still leaves its synonyms exactly where they are (they carry their own
    parent); this only ever moves the one row passed in.
    """
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    # No-fallback rule (#71): blanking the parent of a rank that composes from an
    # ancestor (e.g. a species) would collapse its name to a bare epithet and leave
    # a rootless non-root taxon. Refuse it loudly rather than silently corrupt.
    if new_parent_id is None and _rank_requires_parent(t.taxon_rank):
        raise ValueError(
            f"a {t.taxon_rank} ('{t.scientific_name}') requires a parent — clearing it "
            f"would collapse the name to a bare epithet. Set the correct parent instead."
        )
    t.parent_name_usage_id = new_parent_id
    t.updated_at = _utcnow()
    session.flush()
    # The new parent changes this row's composed name and every descendant's.
    recompose_subtree(session, t)
    return t


def verify_taxon_consistency(session: Session) -> list[dict]:
    """Audit taxon hierarchy/synonymy invariants; return a list of violations.

    Read-only — run manually (Taxonomy-tab button / tests), not at startup. It
    catches drift the write-time trigger structurally cannot, chiefly a dangling
    parentNameUsageID / acceptedNameUsageID or a chained synonym (an accepted
    name that is itself a synonym). Issue names follow GBIF's NameUsageIssue
    vocabulary. The atomic model parents synonyms under their own lineage, so
    there is no synonym-parent-match rule to audit (retired in migration 0033).
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
    return issues


def create_taxon_direct(
    session: Session,
    *,
    name_element: str | None = None,
    scientific_name: str | None = None,
    taxon_rank: str,
    scientific_name_authorship: str | None = None,
    parent_name_usage_id: int | None = None,
    accepted_name_usage_id: int | None = None,
    nomenclatural_code: str | None = None,
    taxonworks_otu_id: int | None = None,
) -> "Taxon":
    """Create a taxon row from fully specified fields (no parent inference).

    Provide ``name_element`` (the atomic epithet/uninomial — preferred); the
    composed ``scientific_name`` is derived from it + the parent chain. For
    backward-compatibility a full ``scientific_name`` may be passed instead, from
    which the element is extracted. In the atomic model a synonym keeps its OWN
    parent (it is parented under its own lineage), so the passed parent is used
    as-is; only the accepted link is resolved to its terminal name.
    """
    if name_element is None:
        name_element = element_from_name(scientific_name or "", taxon_rank)
    if accepted_name_usage_id is not None:
        acc = session.get(Taxon, accepted_name_usage_id)
        if acc is None:
            raise ValueError("accepted taxon not found")
        accepted_name_usage_id = _terminal_accepted(session, acc).id
    nomenclatural_code = _require_code(
        nomenclatural_code, f"{scientific_name or name_element!r} [{taxon_rank}]")
    t = Taxon(
        name_element=name_element,
        scientific_name=scientific_name or name_element,  # placeholder; recomposed below
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
    t.scientific_name = compose_scientific_name(session, t)
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
                name_element=sci_name,   # uninomial root: element == scientific_name
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
    set to point at it. In the atomic model the synonym keeps its OWN-lineage
    parent (TW supplies its original genus), independent of its accepted name.

    Import policy: only fills NULL fields on existing rows.  Conflicts with
    non-NULL local values are appended to mismatches (if provided).
    """
    # If this is a synonym, ensure the valid (accepted) taxon exists first.
    accepted_id: int | None = None
    valid_tw = tw.get("_valid_tw_data")
    if valid_tw:
        valid_taxon = get_or_create_from_tw_data(
            session, valid_tw, otu_id=tw.get("_valid_otu_id"), mismatches=mismatches
        )
        accepted_id = valid_taxon.id

    fields = _fields_from_tw(tw)
    element = fields["name_element"]
    rank = fields["taxon_rank"]
    nomen_code = fields.get("nomenclatural_code")

    # Ensure all ancestor rows exist; get the immediate (own-lineage) parent ID.
    parent_id = _ensure_parent_rows(
        session, fields, nomenclatural_code=nomen_code, mismatches=mismatches
    )

    # Compose the full name now that the parent chain exists.
    sci_name = _compose_transient(
        session, name_element=element, taxon_rank=rank,
        parent_id=parent_id, nomenclatural_code=nomen_code,
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
        if not existing.name_element:
            existing.name_element = element
            dirty = True
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
                dirty = True
            elif existing.accepted_name_usage_id != accepted_id and mismatches is not None:
                local_acc = session.get(Taxon, existing.accepted_name_usage_id)
                import_acc = session.get(Taxon, accepted_id)
                lname = local_acc.scientific_name if local_acc else f"id:{existing.accepted_name_usage_id}"
                iname = import_acc.scientific_name if import_acc else f"id:{accepted_id}"
                mismatches.append(
                    f"{sci_name}: accepted name is {lname!r} locally, import says {iname!r}"
                )
        # Own-lineage parent backfill (applies to accepted names and synonyms
        # alike — a synonym carries its own parent now).
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
                    f"{sci_name}: parent is {lname!r} locally, import says {iname!r}"
                )
        if nomen_code and not existing.nomenclatural_code:
            existing.nomenclatural_code = nomen_code
            dirty = True
        if dirty:
            existing.updated_at = _utcnow()
            session.flush()
        return existing

    _require_code(nomen_code, f"{sci_name!r} [{rank}]")
    t = Taxon(
        name_element=element,
        scientific_name=sci_name,
        taxon_rank=rank,
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
# WCVP integration
# ---------------------------------------------------------------------------

def get_or_create_from_wcvp_data(
    session: Session,
    fields: dict,
    *,
    mismatches: list[str] | None = None,
) -> Taxon:
    """Find or create a local Taxon from a WCVP-derived field dict.

    `fields` comes from `wcvp.fields_from_wcvp()`, which has already refused anything the
    model cannot hold (Unplaced / Misapplied statuses, unmodelled ranks, dangling accepted
    links). Creates the family and genus ancestor rows if missing, then the name itself.

    Lineage is own-lineage (Epic #30): the genus comes from the row's OWN genus, never from
    its accepted name's. Parenting a synonym under the accepted name's genus would *rename*
    it — `scientific_name` is composed from the parent chain — merging 143 738 synonyms into
    their own accepted name and fabricating 216 006 combinations nobody published. See
    docs/plant_names.md §5.

    Import policy matches the TW/POWO importers: fill NULL fields on an existing row, and
    report a conflict with a non-NULL local value into `mismatches` rather than overwriting.
    The local DB is the source of truth.
    """
    sci_name   = fields["scientific_name"]
    rank       = (fields.get("taxon_rank") or "species").lower()
    auth       = fields.get("scientific_name_authorship")
    nomen_code = fields["nomenclatural_code"]
    name_id    = fields.get("ipni_id")
    element    = element_from_name(sci_name, rank)

    # The accepted name first, so the synonym can link to it. Its own lineage is built by
    # the recursive call; it is NOT this name's lineage.
    accepted_taxon: Taxon | None = None
    accepted_fields = fields.get("accepted")
    if accepted_fields:
        accepted_taxon = get_or_create_from_wcvp_data(
            session, accepted_fields, mismatches=mismatches
        )

    # WCVP has no rank above Genus: `family` is a text column with no authorship, so the
    # family row is created from its name alone. The genus authorship is present only when
    # resolve_genus() found it unambiguously; NULL otherwise (silence, never a guess).
    ancestor_fields: dict = {"taxon_rank": rank, "nomenclatural_code": nomen_code}
    if fields.get("family"):
        ancestor_fields["family"] = fields["family"]
    if fields.get("genus"):
        ancestor_fields["genus"] = fields["genus"]
    if fields.get("genus_authorship"):
        ancestor_fields["genus_authorship"] = fields["genus_authorship"]
    if fields.get("genus_ipni_id"):
        ancestor_fields["genus_ipni_id"] = fields["genus_ipni_id"]
    if fields.get("species_name"):
        ancestor_fields["species_name"] = fields["species_name"]

    parent_id = _ensure_parent_rows(
        session, ancestor_fields, nomenclatural_code=nomen_code, mismatches=mismatches
    )

    composed_sci = _compose_transient(
        session, name_element=element, taxon_rank=rank,
        parent_id=parent_id, nomenclatural_code=nomen_code,
    )

    existing = (
        session.query(Taxon)
        .filter(Taxon.scientific_name == composed_sci, Taxon.taxon_rank == rank)
        .first()
    )

    # A name can never be its own accepted name. This fires when a synonym composes to the
    # same name as its accepted name — the signature of a lineage bug (parenting the synonym
    # under the accepted name's genus), which would otherwise merge the two rows silently and
    # take the synonym's determinations with it. The accepted-is-terminal triggers do NOT
    # catch a self-link: the target is itself accepted, so their check passes.
    if accepted_taxon is not None and existing is not None and existing.id == accepted_taxon.id:
        raise ValueError(
            f"{sci_name!r} composes to {composed_sci!r}, which is its own accepted name — "
            "refusing to merge a synonym into the name it is a synonym of"
        )
    if existing:
        dirty = False
        if not existing.name_element:
            existing.name_element = element
            dirty = True
        if auth:
            if not existing.scientific_name_authorship:
                existing.scientific_name_authorship = auth
                dirty = True
            elif existing.scientific_name_authorship != auth and mismatches is not None:
                mismatches.append(
                    f"{sci_name}: authorship is {existing.scientific_name_authorship!r} "
                    f"locally, import says {auth!r}"
                )
        if name_id and not existing.ipni_id:
            existing.ipni_id = name_id
            dirty = True
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
                    f"{sci_name}: parent is {lname!r} locally, import says {iname!r}"
                )
        if not existing.nomenclatural_code:
            existing.nomenclatural_code = nomen_code
            dirty = True
        if accepted_taxon:
            if not existing.accepted_name_usage_id:
                existing.accepted_name_usage_id = _terminal_accepted(session, accepted_taxon).id
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

    _require_code(nomen_code, f"{sci_name!r} [{rank}]")
    t = Taxon(
        name_element=element,
        scientific_name=composed_sci,
        taxon_rank=rank,
        scientific_name_authorship=auth,
        parent_name_usage_id=parent_id,
        # Resolve to the terminal accepted name: the trg_taxon_accepted_is_terminal triggers
        # RAISE on a chained synonym, and WCVP's own links are not guaranteed terminal.
        accepted_name_usage_id=_terminal_accepted(session, accepted_taxon).id if accepted_taxon else None,
        nomenclatural_code=nomen_code,
        ipni_id=name_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


def get_or_create_from_chain(
    session: Session,
    chain: list[dict],
    *,
    accepted_chain: list[dict] | None = None,
    mismatches: list[str] | None = None,
) -> Taxon:
    """Find or create a Taxon from an explicit ROOT→LEAF lineage chain, returning the leaf.

    The chain is the *source's own* statement of where a name sits — `name_source.chain_for()`
    builds it by walking the archive's parentNameUsageID links. This is deliberately unlike
    `get_or_create_from_wcvp_data`, which reconstructs a family/genus lineage from denormalised
    columns because WCVP models no rank above genus. A chain can express any lineage the source
    has — notably a species under its **subgenus** — without a column per rank.

    Own-lineage (Epic #30): `accepted_chain` is the accepted name's OWN chain, built and created
    independently. A synonym is never parented under its accepted name's lineage — doing so
    would *rename* it, since scientific_name is composed from the parent chain.

    Import policy matches the other importers: fill NULL fields on an existing row, report a
    conflict with a non-NULL local value into `mismatches` rather than overwriting. The local
    DB is the source of truth.
    """
    if not chain:
        raise ValueError("empty lineage chain — nothing to import")

    accepted_taxon: Taxon | None = None
    if accepted_chain:
        accepted_taxon = get_or_create_from_chain(
            session, accepted_chain, mismatches=mismatches)

    node: Taxon | None = None
    parent_id: int | None = None
    for i, entry in enumerate(chain):
        is_leaf = i == len(chain) - 1
        rank = (entry["rank"] or "").lower()
        code = entry["code"]
        element = element_from_name(entry["name"], rank)
        auth = entry.get("authorship")
        _require_code(code, f"{entry['name']!r} [{rank}]")

        composed = _compose_transient(
            session, name_element=element, taxon_rank=rank,
            parent_id=parent_id, nomenclatural_code=code,
        )
        existing = (
            session.query(Taxon)
            .filter(Taxon.scientific_name == composed, Taxon.taxon_rank == rank)
            .first()
        )

        # A name can never be its own accepted name. This fires when a synonym composes to the
        # same name as its accepted name — the signature of a lineage bug — which would
        # otherwise merge the two rows silently, taking the synonym's determinations with it.
        if (is_leaf and accepted_taxon is not None and existing is not None
                and existing.id == accepted_taxon.id):
            raise ValueError(
                f"{entry['name']!r} composes to {composed!r}, which is its own accepted "
                "name — refusing to merge a synonym into the name it is a synonym of"
            )

        if existing is not None:
            dirty = False
            if not existing.name_element:
                existing.name_element = element
                dirty = True
            if auth:
                if not existing.scientific_name_authorship:
                    existing.scientific_name_authorship = auth
                    dirty = True
                elif existing.scientific_name_authorship != auth and mismatches is not None:
                    mismatches.append(
                        f"{composed}: authorship is "
                        f"{existing.scientific_name_authorship!r} locally, "
                        f"import says {auth!r}"
                    )
            if parent_id is not None and existing.parent_name_usage_id is None:
                existing.parent_name_usage_id = parent_id
                dirty = True
            if is_leaf and accepted_taxon is not None and existing.accepted_name_usage_id is None:
                existing.accepted_name_usage_id = _terminal_accepted(
                    session, accepted_taxon).id
                dirty = True
            if dirty:
                existing.updated_at = _utcnow()
                session.flush()
            node = existing
        else:
            node = Taxon(
                name_element=element,
                scientific_name=composed,
                taxon_rank=rank,
                scientific_name_authorship=auth,
                parent_name_usage_id=parent_id,
                # Resolve to the TERMINAL accepted name: trg_taxon_accepted_is_terminal
                # RAISEs on a chained synonym, and a source's links are not guaranteed
                # terminal.
                accepted_name_usage_id=(
                    _terminal_accepted(session, accepted_taxon).id
                    if (is_leaf and accepted_taxon is not None) else None
                ),
                nomenclatural_code=code,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(node)
            session.flush()
        parent_id = node.id

    return node

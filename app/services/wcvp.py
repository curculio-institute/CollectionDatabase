"""WCVP — the offline plant-name backbone.

The World Checklist of Vascular Plants is the taxonomic backbone that POWO serves.
It is distributed as a static Darwin Core Archive; there is **no usable API** (both
`powo.science.kew.org` and `wcvp.science.kew.org` sit behind a Cloudflare bot challenge
that answers a plain HTTP client with 403 on ~17 of 20 requests — verified 2026-07-09,
issue #98). The archive is the access route the literature itself uses: Schellenberger
Costa et al. 2023, New Phytologist 240:1687-1702, doi:10.1111/nph.18961, Data Availability.

That paper is also why the backbone choice is recorded rather than assumed: the four global
checklists disagree on ~300 000 names, and its authors — curators of all four — decline to
recommend one over another. WCVP is chosen because it is what POWO serves (so plant names
already in the DB keep their treatment), and because it carries the IPNI links the paper
endorses for joining across resources.

This module owns the archive → SQLite index build. The index is a *read-only lookup table*,
rebuilt from Kew's archive and never edited; it is not the specimen DB.
"""
from __future__ import annotations

import csv
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree

WCVP_DWCA_URL = "http://sftp.kew.org/pub/data-repositories/WCVP/wcvp_dwca.zip"

_TAXON_CSV = "wcvp_taxon.csv"
_EML_XML = "eml.xml"

# From meta.xml: fieldsTerminatedBy="|" fieldsEnclosedBy='' — the CSV is UNQUOTED.
# Reading it with Python's default QUOTE_MINIMAL silently reinterprets embedded double
# quotes: 24 rows parse differently (all in taxonremarks). Honour the archive's own
# declaration rather than depend on that column being one we ignore.
_DELIMITER = "|"
_QUOTING = csv.QUOTE_NONE

# Kew misspells two headers in the archive ("scientfiic"). They are the source of truth
# for parsing; do not "correct" them or the loader breaks on the real file.
_COL_ID = "taxonid"
_COL_NAME = "scientfiicname"
_COL_AUTHORSHIP = "scientfiicnameauthorship"
_COL_RANK = "taxonrank"
_COL_STATUS = "taxonomicstatus"
_COL_ACCEPTED = "acceptednameusageid"
_COL_PARENT = "parentnameusageid"
_COL_NAME_ID = "scientificnameid"   # holds the IPNI id as "ipni:304293-2"
_COL_FAMILY = "family"
_COL_GENUS = "genus"

_IPNI_PREFIX = "ipni:"

# Every name in WCVP is governed by the ICN — a property of the source, not of the row.
NOMENCLATURAL_CODE = "ICN"

# The archive is CC BY 3.0 (declared in its eml.xml <intellectualRights>). Attribution is
# required wherever the data is redistributed, including a DwC export derived from it, so the
# licence and the versioned citation are recorded in the index's meta table.
WCVP_LICENSE = "CC BY 3.0"

# WCVP's taxonomicStatus vocabulary, partitioned by what our two-state taxon model can
# represent. A name is a synonym iff acceptedNameUsageID is set, otherwise accepted; there
# is no third state, and `taxonomicStatus` is *derived from that column at export*
# (migration 0030). So a status that is neither "accepted" nor "replaced by X" cannot be
# stored without publishing a false claim — it is refused at import, never coerced.
STATUS_ACCEPTED = frozenset({
    "Accepted",
    # WCVP does accept these, tentatively; no accepted link exists to point at.
    "Provisionally Accepted",
})
STATUS_REPLACED = frozenset({
    "Synonym",
    # All of these mean "use that name instead of this one" — representable as a synonym
    # link. The *reason* is lost, but nothing false is asserted. (TaxonWorks models them
    # as Homonym / OriginallyInvalid / Usage::Misspelling relationships.)
    "Illegitimate",
    "Invalid",
    "Orthographic",
    "Local Biotype",
    "Artificial Hybrid",
})
STATUS_REFUSED = frozenset({
    # WCVP explicitly declines to say whether the name is accepted or a synonym. A NULL
    # link would assert "accepted" — a claim the source refuses to make.
    "Unplaced",
    # A misapplication is not a synonymy. TaxonWorks declares
    # TaxonNameRelationship::Icn::Unaccepting::Misapplication disjoint from Synonym, and
    # DwC/GBIF give `misapplied` as a taxonomicStatus distinct from `synonym`.
    "Misapplied",
})
KNOWN_STATUSES = STATUS_ACCEPTED | STATUS_REPLACED | STATUS_REFUSED

# WCVP ranks our taxon model can hold (TAXON_RANKS, lowercased). WCVP also carries 7 137
# importable names at ranks we do not model — `proles`, `lusus`, `nothosubsp.`, `monstr.`,
# and 2 707 with no rank at all. Same rule as an unrepresentable status: refuse the import,
# but show the name so the user learns it exists rather than inventing it by hand.
SUPPORTED_RANKS = frozenset({
    "genus", "species", "subspecies", "variety", "subvariety", "form", "subform",
})

_SCHEMA = """
CREATE TABLE name (
    taxonid    TEXT NOT NULL PRIMARY KEY,
    ipni_id    TEXT,
    name       TEXT NOT NULL,
    authorship TEXT,
    rank       TEXT NOT NULL,
    status     TEXT NOT NULL,
    accepted_id TEXT,
    parent_id  TEXT,
    family     TEXT,
    genus      TEXT
) STRICT;

CREATE TABLE meta (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
"""

# A BINARY index yields `SCAN` for a case-insensitive prefix LIKE; only a NOCASE index
# lets SQLite turn it into `SEARCH … (name>? AND name<?)`. That is the difference between
# ~100 ms and ~0.2 ms per keystroke over 1.45 M rows.
_INDEXES = [
    "CREATE INDEX ix_name_nocase ON name(name COLLATE NOCASE)",
    "CREATE INDEX ix_accepted ON name(accepted_id)",
    "CREATE INDEX ix_ipni ON name(ipni_id)",
]


@dataclass(frozen=True)
class ArchiveMeta:
    """Provenance of the archive, read from its own eml.xml — never hardcoded."""
    version: str
    pub_date: str
    citation: str

    @property
    def label(self) -> str:
        """Human-readable backbone identity, e.g. 'WCVP v16.0 (2026-06-04)'."""
        return f"WCVP v{self.version} ({self.pub_date})"


@dataclass(frozen=True)
class BuildReport:
    meta: ArchiveMeta
    rows: int
    accepted: int
    replaced: int
    refused: int
    self_referencing_accepted: int
    dangling_accepted_ids: int
    dangling_parent_ids: int


class WcvpError(RuntimeError):
    """The archive is not what this loader was written against."""


def read_archive_meta(zf: zipfile.ZipFile) -> ArchiveMeta:
    """Parse version / pubDate / citation out of the archive's eml.xml."""
    root = ElementTree.fromstring(zf.read(_EML_XML))
    def _first(tag: str) -> str:
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag and (el.text or "").strip():
                return el.text.strip()
        raise WcvpError(f"eml.xml has no <{tag}> — archive layout changed")
    return ArchiveMeta(
        version=_first("version"),
        pub_date=_first("pubDate"),
        citation=_first("citation"),
    )


def _rows(zf: zipfile.ZipFile) -> Iterator[tuple]:
    """Stream the taxon CSV as insert tuples, validating as we go.

    Two source quirks are normalised here, once, so no consumer can get them wrong:

    * `acceptednameusageid` on an Accepted row **points at itself** (all 434 691 of them).
      Stored verbatim it would read as "synonym of itself". Normalised to NULL.
    * `scientificnameid` carries the IPNI id as "ipni:304293-2"; the bare id is stored.

    An unrecognised taxonomicStatus raises: it means Kew changed the vocabulary and the
    accepted/replaced/refused partition above needs re-deciding, not guessing.
    """
    with zf.open(_TAXON_CSV) as raw:
        text = (line.decode("utf-8") for line in raw)
        for row in csv.DictReader(text, delimiter=_DELIMITER, quoting=_QUOTING):
            status = row[_COL_STATUS]
            if status not in KNOWN_STATUSES:
                raise WcvpError(
                    f"unknown taxonomicStatus {status!r} (taxonid {row[_COL_ID]!r}). "
                    "The WCVP status vocabulary changed; decide whether it is accepted, "
                    "replaced-by-X, or unrepresentable before importing it."
                )
            taxonid = row[_COL_ID]
            accepted = row[_COL_ACCEPTED] or None
            if accepted == taxonid:
                accepted = None
            name_id = row[_COL_NAME_ID] or ""
            ipni = name_id[len(_IPNI_PREFIX):] if name_id.startswith(_IPNI_PREFIX) else None
            yield (
                taxonid,
                ipni,
                row[_COL_NAME],
                row[_COL_AUTHORSHIP] or None,
                row[_COL_RANK],
                status,
                accepted,
                row[_COL_PARENT] or None,
                row[_COL_FAMILY] or None,
                row[_COL_GENUS] or None,
            )


def build_index(archive: Path, db_path: Path, *, batch: int = 50_000) -> BuildReport:
    """Build the SQLite lookup index from a WCVP Darwin Core Archive.

    Deterministic and rebuildable: the target is replaced wholesale. Journalling is off
    because a half-built index is thrown away, not recovered.
    """
    with zipfile.ZipFile(archive) as zf:
        if _TAXON_CSV not in zf.namelist():
            raise WcvpError(f"{archive} contains no {_TAXON_CSV}")
        meta = read_archive_meta(zf)

        tmp = db_path.with_suffix(".building")
        tmp.unlink(missing_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(tmp)
        try:
            db.execute("PRAGMA journal_mode = OFF")
            db.execute("PRAGMA synchronous = OFF")
            db.executescript(_SCHEMA)

            n = 0
            pending: list[tuple] = []
            for rec in _rows(zf):
                n += 1
                pending.append(rec)
                if len(pending) >= batch:
                    db.executemany("INSERT INTO name VALUES (?,?,?,?,?,?,?,?,?,?)", pending)
                    pending.clear()
            if pending:
                db.executemany("INSERT INTO name VALUES (?,?,?,?,?,?,?,?,?,?)", pending)

            for stmt in _INDEXES:
                db.execute(stmt)

            counts = dict(db.execute(
                "SELECT status, count(*) FROM name GROUP BY status"))
            accepted = sum(counts.get(s, 0) for s in STATUS_ACCEPTED)
            replaced = sum(counts.get(s, 0) for s in STATUS_REPLACED)
            refused = sum(counts.get(s, 0) for s in STATUS_REFUSED)
            self_ref = counts.get("Accepted", 0)

            # Referential health of Kew's own data. Reported, not enforced: a dangling
            # link is Kew's error and must not silently vanish, but neither should it
            # block a build. The import path refuses such a row individually.
            dangling_acc = db.execute(
                "SELECT count(*) FROM name n WHERE n.accepted_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM name a WHERE a.taxonid = n.accepted_id)"
            ).fetchone()[0]
            dangling_par = db.execute(
                "SELECT count(*) FROM name n WHERE n.parent_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM name p WHERE p.taxonid = n.parent_id)"
            ).fetchone()[0]

            db.executemany("INSERT INTO meta VALUES (?,?)", [
                ("version", meta.version),
                ("pub_date", meta.pub_date),
                ("citation", meta.citation),
                ("label", meta.label),
                ("source_url", WCVP_DWCA_URL),
                ("license", WCVP_LICENSE),
                ("nomenclatural_code", NOMENCLATURAL_CODE),
                ("built_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ("rows", str(n)),
            ])
            db.commit()
        except BaseException:
            db.close()
            tmp.unlink(missing_ok=True)   # never leave a half-built index on disk
            raise
        finally:
            db.close()

        tmp.replace(db_path)

    return BuildReport(
        meta=meta, rows=n, accepted=accepted, replaced=replaced, refused=refused,
        self_referencing_accepted=self_ref,
        dangling_accepted_ids=dangling_acc, dangling_parent_ids=dangling_par,
    )


# ---------------------------------------------------------------------------
# Query layer — read-only access to the built index
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WcvpName:
    """One row of the checklist."""
    taxonid: str
    ipni_id: str | None
    name: str
    authorship: str | None
    rank: str
    status: str
    accepted_id: str | None
    parent_id: str | None
    family: str | None
    genus: str | None

    @property
    def is_accepted(self) -> bool:
        return self.status in STATUS_ACCEPTED and not self.rank_unsupported

    @property
    def is_replaced(self) -> bool:
        return self.status in STATUS_REPLACED and not self.rank_unsupported

    @property
    def rank_unsupported(self) -> bool:
        return self.rank.lower() not in SUPPORTED_RANKS

    @property
    def is_refused(self) -> bool:
        """True when our model cannot represent this name — an unrepresentable status
        (see STATUS_REFUSED) or a rank we do not model (see SUPPORTED_RANKS)."""
        return self.status in STATUS_REFUSED or self.rank_unsupported

    @property
    def label(self) -> str:
        return f"{self.name} {self.authorship}".strip() if self.authorship else self.name


class IndexMissing(WcvpError):
    """The WCVP index has not been built. Plant search is unavailable until it is."""


def open_index(path: Path | None = None) -> sqlite3.Connection:
    """Open the index read-only. Raises IndexMissing if it has not been built.

    Read-only by URI: the index is derived data, and nothing in the app may write to it.
    """
    from app import config
    path = path or config.wcvp_db_path()
    if not path.exists():
        raise IndexMissing(
            f"no WCVP index at {path} — build it with scripts/build_wcvp_index.py"
        )
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def index_meta(db: sqlite3.Connection) -> dict[str, str]:
    """Provenance of the installed index — read from the file, never the network."""
    return dict(db.execute("SELECT key, value FROM meta"))


def _name(row: sqlite3.Row) -> WcvpName:
    return WcvpName(**{k: row[k] for k in WcvpName.__dataclass_fields__})


_COLS = "taxonid, ipni_id, name, authorship, rank, status, accepted_id, parent_id, family, genus"

# Accepted names first, then replaced-by-X. Within a group, the shortest name first, so an
# exact hit outranks its own infraspecifics ("Quercus robur" before "Quercus robur var. …").
_ORDER = "CASE WHEN status IN ('Accepted','Provisionally Accepted') THEN 0 ELSE 1 END, length(name), name"


def _escape(token: str) -> str:
    """Neutralise LIKE wildcards typed by the user — a bare '%' would match every name."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(term: str) -> tuple[str, list[str]]:
    """Anchor the first token so SQLite can seek ix_name_nocase instead of scanning 1.45M rows.

    A leading wildcard on the first token turns SEARCH into SCAN (~100 ms/keystroke).
    Later tokens keep their leading wildcard: "Quer rob" must match "Quercus robur".
    """
    tokens = term.split()
    clauses = [r"name LIKE ? ESCAPE '\'"]
    args = [f"{_escape(tokens[0])}%"]
    for tok in tokens[1:]:
        clauses.append(r"name LIKE ? ESCAPE '\'")
        args.append(f"%{_escape(tok)}%")
    return " AND ".join(clauses), args


def search(db: sqlite3.Connection, term: str, *, limit: int = 8,
           refused_limit: int = 3) -> list[WcvpName]:
    """Multi-token prefix search, importable names first.

    Refused names (Unplaced / Misapplied) are *shown* — hiding them invites the user to
    conclude the name does not exist and hand-create it, a silent invention. They are ranked
    last and capped, so they can never displace a usable suggestion: in genera like Rubus
    (23.1% refused) they would otherwise flood the list.
    """
    term = term.strip()
    if len(term) < 2:
        return []
    where, args = _where(term)

    # Must mirror WcvpName.is_refused exactly, or the ranking and the importer disagree.
    st = ",".join("?" * len(STATUS_REFUSED))
    rk = ",".join("?" * len(SUPPORTED_RANKS))
    refused_sql = f"(status IN ({st}) OR lower(rank) NOT IN ({rk}))"
    refused_args = [*sorted(STATUS_REFUSED), *sorted(SUPPORTED_RANKS)]

    importable = db.execute(
        f"SELECT {_COLS} FROM name WHERE {where} AND NOT {refused_sql} "
        f"ORDER BY {_ORDER} LIMIT ?",
        (*args, *refused_args, limit),
    ).fetchall()
    blocked = db.execute(
        f"SELECT {_COLS} FROM name WHERE {where} AND {refused_sql} "
        f"ORDER BY length(name), name LIMIT ?",
        (*args, *refused_args, refused_limit),
    ).fetchall()
    return [_name(r) for r in importable] + [_name(r) for r in blocked]


def get(db: sqlite3.Connection, taxonid: str) -> WcvpName | None:
    row = db.execute(f"SELECT {_COLS} FROM name WHERE taxonid = ?", (taxonid,)).fetchone()
    return _name(row) if row else None


def accepted_name(db: sqlite3.Connection, row: WcvpName) -> WcvpName | None:
    """The name WCVP says to use instead. None for an accepted name.

    Kew's data contains dangling accepted_id values; a missing target returns None rather
    than a fabricated one, and the importer refuses such a row.
    """
    if not row.accepted_id:
        return None
    return get(db, row.accepted_id)


def refusal_reason(db: sqlite3.Connection, row: WcvpName) -> str:
    """Why this name cannot be imported, phrased without asserting a synonymy it is not."""
    if row.status == "Misapplied":
        target = accepted_name(db, row)
        if target:
            return f"in WCVP this name is applied to {target.name}"
        return "in WCVP this name is a misapplication of another name"
    if row.status == "Unplaced":
        return "WCVP records no accepted placement for this name"
    if row.rank_unsupported:
        rank = row.rank or "no rank"
        return f"this database does not model the rank “{rank}”"
    return ""


def _bare(genus: str) -> str:
    """Strip the hybrid/graft marker a nothogenus row carries ('× Epicattleya', '+ Pirocydonia')."""
    return genus.lstrip("×+ ").strip()


def resolve_genus(db: sqlite3.Connection, genus: str, family: str | None) -> WcvpName | None:
    """The genus row a name belongs under, by its OWN genus — never its accepted name's.

    1 894 genus names are homonyms (Torreya occurs in six families), so name alone is unsafe.
    Matching on name + family is unique for 96.6% of synonyms; preferring the single Accepted
    candidate resolves a further 2.2%.

    Returns None when the genus is ambiguous among non-accepted rows (8 179 synonyms, e.g.
    Ascyrum L. vs Ascyrum Mill.) or absent (810 nothogenera). The caller then creates the
    genus from its name with NO authorship: the name is certain, the author is not, and
    composition uses the parent's name_element rather than its authorship — so the composed
    name is identical either way, and silence about the author asserts nothing false.
    """
    target = _bare(genus)
    rows = [
        _name(r) for r in db.execute(
            f"SELECT {_COLS} FROM name WHERE rank = 'Genus' AND family IS ?"
            " AND (name = ? OR name = '× ' || ? OR name = '+ ' || ?)",
            (family, target, target, target),
        )
    ]
    if len(rows) == 1:
        return rows[0]
    accepted = [r for r in rows if r.status == "Accepted"]
    if len(accepted) == 1:
        return accepted[0]
    return None


# Infraspecific connectors WCVP writes into the name string ("Quercus robur subsp. robur").
# Used only to cut the parent species name off an infraspecific synonym, which WCVP gives
# no parent_id. Mirrors taxa._ICN_INFRA_CONNECTOR.
_INFRA_CONNECTORS = ("subsp.", "var.", "subvar.", "f.", "subf.")


def _species_name_of(name: str) -> str | None:
    """The parent species name embedded in an infraspecific name, or None.

        'Sarothamnus scoparius var. bicolor' → 'Sarothamnus scoparius'
    """
    tokens = name.split()
    for i, tok in enumerate(tokens):
        if tok in _INFRA_CONNECTORS:
            return " ".join(tokens[:i]) or None
    return None


class NotImportable(WcvpError):
    """This name cannot be represented in the local taxon model. Never coerce it."""


def fields_from_wcvp(db: sqlite3.Connection, row: WcvpName) -> dict:
    """Flatten a chosen WCVP name into the field dict `taxa.get_or_create_from_wcvp_data`
    consumes — the extraction seam, mirroring the old `powo.fields_from_powo`.

    Raises NotImportable rather than coercing a name the model cannot hold:
    `Unplaced` / `Misapplied` statuses, ranks we do not model, and a synonym whose accepted
    target is missing from Kew's data (a dangling link — the caller must not invent one).

    Lineage is the synonym-safe one (Epic #30): the genus comes from the row's OWN `genus`
    column, never from its accepted name's genus. See docs/plant_names.md §5.
    """
    if row.is_refused:
        raise NotImportable(f"{row.label}: {refusal_reason(db, row)}")

    accepted: dict | None = None
    if row.accepted_id:
        target = accepted_name(db, row)
        if target is None:
            raise NotImportable(
                f"{row.label}: WCVP links it to accepted name id {row.accepted_id!r}, "
                "which is not in the archive (an error in Kew's data)"
            )
        accepted = fields_from_wcvp(db, target)

    rank = row.rank.lower()
    genus_row = resolve_genus(db, row.genus, row.family) if row.genus else None

    return {
        "scientific_name": row.name,
        "taxon_rank": rank,
        "scientific_name_authorship": row.authorship,
        "nomenclatural_code": NOMENCLATURAL_CODE,
        "ipni_id": row.ipni_id,
        "family": row.family,
        # The genus row supplies authorship only when unambiguously resolved; otherwise the
        # name is certain and the author is not, so we stay silent rather than pick one.
        "genus": row.genus if rank != "genus" else None,
        "genus_authorship": genus_row.authorship if genus_row else None,
        "genus_ipni_id": genus_row.ipni_id if genus_row else None,
        # Infraspecific names need their species parent. Accepted rows carry parent_id;
        # synonyms carry none, so cut it out of the name string.
        "species_name": _species_name_of(row.name) if rank in _INFRA_RANKS else None,
        "is_synonym": bool(row.accepted_id),
        "accepted": accepted,
    }


_INFRA_RANKS = frozenset({"subspecies", "variety", "subvariety", "form", "subform"})


# ---------------------------------------------------------------------------
# Update check — 16 KB, not 84 MB
# ---------------------------------------------------------------------------
#
# Never called at startup. This is a local-first app: it must launch offline, and
# db_safety runs its checkpoint/integrity/snapshot before the UI serves, so a hanging
# HTTP request there would block the app on a bad connection. The Settings card calls
# this only when the user presses the button.

_ZIP_LOCAL_HEADER = b"PK\x03\x04"
_UPDATE_CHECK_BYTES = 32_768


def meta_from_zip_prefix(prefix: bytes) -> ArchiveMeta:
    """Read eml.xml out of the first bytes of the archive, without the other 84 MB.

    eml.xml is the archive's first zip entry (~4.9 KB deflated), so its local file header
    sits at offset 0 and a ranged request for the first few KB contains the whole entry.
    Raises WcvpError if the archive no longer starts with eml.xml, rather than reporting a
    version read out of the wrong member.
    """
    import struct
    import zlib

    if prefix[:4] != _ZIP_LOCAL_HEADER:
        raise WcvpError("not a zip archive (no local file header)")
    method, = struct.unpack("<H", prefix[8:10])
    csize, _usize = struct.unpack("<II", prefix[18:26])
    nlen, elen = struct.unpack("<HH", prefix[26:30])
    name = prefix[30:30 + nlen].decode("utf-8", "replace")
    if name != _EML_XML:
        raise WcvpError(f"first archive entry is {name!r}, expected {_EML_XML!r}")

    start = 30 + nlen + elen
    blob = prefix[start:start + csize]
    if len(blob) < csize:
        raise WcvpError("archive prefix too short to contain eml.xml")
    data = zlib.decompress(blob, -15) if method == 8 else blob

    root = ElementTree.fromstring(data)
    def _first(tag: str) -> str:
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag and (el.text or "").strip():
                return el.text.strip()
        raise WcvpError(f"eml.xml has no <{tag}>")
    return ArchiveMeta(version=_first("version"), pub_date=_first("pubDate"),
                       citation=_first("citation"))


def latest_release(url: str = WCVP_DWCA_URL, *, timeout: float = 20.0) -> ArchiveMeta:
    """What release Kew is currently serving. Costs ~32 KB; requires the network."""
    import httpx

    r = httpx.get(url, headers={"Range": f"bytes=0-{_UPDATE_CHECK_BYTES - 1}"},
                  follow_redirects=True, timeout=timeout)
    r.raise_for_status()
    return meta_from_zip_prefix(r.content)

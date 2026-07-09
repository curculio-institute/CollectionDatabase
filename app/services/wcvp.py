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

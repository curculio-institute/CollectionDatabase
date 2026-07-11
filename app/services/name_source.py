"""Offline name sources — the generic engine behind WCVP and user-added checklists.

A *name source* is a static Darwin Core Archive (a Taxon core) indexed into a read-only
SQLite lookup table, searched from the taxon widget, and imported name-by-name (or in bulk)
into the local DB. WCVP is one instance of it (plants, ICN, downloaded from Kew); a user may
add others — e.g. a Coleoptera checklist (ICZN) — from a file on their computer.

The index is a *read-only lookup table*, rebuilt from its archive and never edited. It is not
the specimen DB.

**The archive describes itself; we do not configure it.** `meta.xml` declares the core file,
its delimiter, and every field by **DwC term URI and column index** — so a field is located by
term, never by the spelling of the CSV header. That matters concretely: Kew misspells two
headers (`scientfiicname`, `scientfiicnameauthorship`) and a correctly-spelled archive writes
`scientificName`. Reading by term, both work and neither is special-cased. `meta.xml` may also
declare a `default` value for a field the CSV omits — which is how an archive states its own
`nomenclaturalCode` (ICZN / ICN) rather than us guessing it from the taxa inside.
"""
from __future__ import annotations

import csv
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree

_DWC = "http://rs.tdwg.org/dwc/terms/"


class NameSourceError(RuntimeError):
    """The archive is not what this loader was written against."""


class IndexMissing(NameSourceError):
    """The index has not been built. This source is unavailable until it is."""


# ---------------------------------------------------------------------------
# The columns we keep, addressed by DwC term
# ---------------------------------------------------------------------------
# Every field is located by its term URI (from meta.xml). The header-name aliases are only a
# fallback for an archive with no meta.xml, and they deliberately include Kew's misspellings
# alongside the correct DwC spellings — both are real files in the wild.
_TERM_TAXON_ID = _DWC + "taxonID"
_TERM_NAME = _DWC + "scientificName"
_TERM_AUTHORSHIP = _DWC + "scientificNameAuthorship"
_TERM_RANK = _DWC + "taxonRank"
_TERM_STATUS = _DWC + "taxonomicStatus"
_TERM_ACCEPTED = _DWC + "acceptedNameUsageID"
_TERM_PARENT = _DWC + "parentNameUsageID"
_TERM_NAME_ID = _DWC + "scientificNameID"
_TERM_FAMILY = _DWC + "family"
_TERM_GENUS = _DWC + "genus"
_TERM_NOMEN_CODE = _DWC + "nomenclaturalCode"

# Our field → every spelling that may denote it, as a NORMALISED key (see _key): the DwC term,
# the camelCase header, the all-lowercase header, and Kew's misspellings. One table serves both
# the meta.xml path (term URIs) and the header fallback, so `taxonID`, `taxonid` and the full
# term URI can never diverge in how they resolve.
_FIELDS: dict[str, tuple[str, ...]] = {
    "taxonid":     ("taxonid",),
    "name":        ("scientificname", "scientfiicname"),          # Kew misspells it
    "authorship":  ("scientificnameauthorship", "scientfiicnameauthorship"),
    "rank":        ("taxonrank",),
    "status":      ("taxonomicstatus",),
    "accepted_id": ("acceptednameusageid",),
    "parent_id":   ("parentnameusageid",),
    "name_id":     ("scientificnameid",),
    "family":      ("family",),
    "genus":       ("genus",),
}
_REQUIRED = ("taxonid", "name", "rank", "status")

# alias → our field name
_BY_ALIAS: dict[str, str] = {
    alias: fname for fname, aliases in _FIELDS.items() for alias in aliases
}


# Namespace prefixes a header may carry. Stripped only when they are an actual prefix (`dwc:`,
# `dwc_`, `dwc.`) — never by blindly splitting on the separator, which would turn a snake_case
# `scientific_name` into `name`.
_NS_PREFIXES = ("dwc", "dcterms", "dc", "gbif", "tw")


def _key(raw: str) -> str:
    """Normalise a DwC term URI or a CSV header to one lookup key.

    All of these collapse to `taxonid`:

        http://rs.tdwg.org/dwc/terms/taxonID · taxonID · taxonid · dwc:taxonID
        dwc_taxonid · dwc.taxonID · taxon_id · Taxon ID

    Case, the URI prefix, a namespace prefix and word separators are all *presentation*, not
    identity — archives in the wild write every one of these — so the reader must not care.
    What it must never do is guess at a name it does not recognise.
    """
    s = (raw or "").strip().rsplit("/", 1)[-1].lower()
    for ns in _NS_PREFIXES:
        for sep in (":", "_", "."):
            if s.startswith(ns + sep):
                s = s[len(ns) + len(sep):]
                break
    for sep in ("_", "-", " ", "."):
        s = s.replace(sep, "")
    return s


@dataclass(frozen=True)
class ArchiveLayout:
    """How to read one archive's Taxon core — parsed from its own meta.xml."""
    core_file: str
    delimiter: str
    quoting: int
    header_lines: int
    columns: dict[str, int]          # our field name → column index
    defaults: dict[str, str]         # our field name → constant value (no column)
    nomenclatural_code: str | None   # from a `default` on the nomenclaturalCode field


def _unescape(raw: str | None, fallback: str) -> str:
    """meta.xml writes control characters escaped ('\\t', '\\n')."""
    if raw is None or raw == "":
        return fallback
    return raw.encode().decode("unicode_escape")


def read_layout(zf: zipfile.ZipFile) -> ArchiveLayout:
    """Parse meta.xml → how to read the Taxon core. The archive is the authority."""
    if "meta.xml" not in zf.namelist():
        raise NameSourceError(
            "the archive has no meta.xml, so it does not declare its own columns. "
            "Only a standard Darwin Core Archive can be indexed."
        )
    root = ElementTree.fromstring(zf.read("meta.xml"))

    def _local(el) -> str:
        return el.tag.rsplit("}", 1)[-1]

    core = next((el for el in root if _local(el) == "core"), None)
    if core is None:
        raise NameSourceError("meta.xml declares no <core> — this is not a Taxon archive")

    loc = next((el.text.strip() for el in core.iter()
                if _local(el) == "location" and (el.text or "").strip()), None)
    if not loc:
        raise NameSourceError("meta.xml <core> declares no <location> (the CSV to read)")

    # A field is either at a column index, or a constant `default` with no column at all — the
    # latter is how an archive states its own nomenclaturalCode.
    columns: dict[str, int] = {}
    defaults: dict[str, str] = {}
    nomen_code: str | None = None
    for el in core:
        if _local(el) != "field":
            continue
        key = _key(el.get("term") or "")
        idx = el.get("index")
        default = el.get("default")
        if key == _key(_TERM_NOMEN_CODE) and default is not None:
            nomen_code = default.strip() or None
            continue
        fname = _BY_ALIAS.get(key)
        if fname is None:
            continue                      # a term we do not keep (taxonRemarks, references…)
        if idx is not None:
            columns[fname] = int(idx)
        elif default is not None:
            defaults[fname] = default

    delimiter = _unescape(core.get("fieldsTerminatedBy"), ",")
    header_lines = int(core.get("ignoreHeaderLines") or 0)

    # Fallback: an archive whose meta.xml declares no usable fields (or omits the ones we
    # need) may still carry a header row that names them. Read it and match by the SAME
    # normalised key, so `taxonID` / `taxonid` / `dwc:taxonID` all resolve identically.
    if not all(f in columns or f in defaults for f in _REQUIRED) and header_lines:
        with zf.open(loc) as raw:
            first = raw.readline().decode("utf-8")
        header = next(csv.reader([first], delimiter=delimiter, quoting=csv.QUOTE_NONE), [])
        for i, cell in enumerate(header):
            fname = _BY_ALIAS.get(_key(cell))
            if fname and fname not in columns:
                columns[fname] = i

    missing = [f for f in _REQUIRED if f not in columns and f not in defaults]
    if missing:
        raise NameSourceError(
            "the archive's Taxon core is missing required Darwin Core fields: "
            + ", ".join(missing)
            + ". Every name needs at least a taxonID, scientificName, taxonRank and "
            "taxonomicStatus."
        )

    return ArchiveLayout(
        core_file=loc,
        delimiter=delimiter,
        # fieldsEnclosedBy='' means the CSV is UNQUOTED. Reading it with Python's default
        # QUOTE_MINIMAL silently reinterprets embedded double quotes (24 WCVP rows parse
        # differently, all in taxonRemarks). Honour the archive's declaration.
        quoting=csv.QUOTE_NONE if not core.get("fieldsEnclosedBy") else csv.QUOTE_MINIMAL,
        header_lines=header_lines,
        columns=columns,
        defaults=defaults,
        nomenclatural_code=nomen_code,
    )


# ---------------------------------------------------------------------------
# The spec: what an archive's own data cannot tell us
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NameSourceSpec:
    """A source's identity and the policy for reading its rows.

    `supported_ranks` and the status partition are the *representability* rules (CLAUDE.md §2):
    our taxon model has exactly two states (accepted / synonym-link), so a status that means
    neither — WCVP's `Unplaced`, `Misapplied` — is refused at import rather than coerced into a
    false claim. A rank we do not model is refused the same way. Refused names are still SHOWN
    in search, so the user learns the name exists instead of hand-inventing it.
    """
    slug: str
    label: str
    nomenclatural_code: str
    supported_ranks: frozenset[str]
    status_accepted: frozenset[str] = frozenset({"Accepted"})
    status_replaced: frozenset[str] = frozenset({"Synonym"})
    status_refused: frozenset[str] = frozenset()
    name_id_prefix: str | None = None     # "ipni:" — strip it, store the bare id
    license: str = ""
    source_url: str = ""
    citation: str = ""
    builtin: bool = False                 # WCVP; user datasets are not
    experimental: bool = False

    @property
    def known_statuses(self) -> frozenset[str]:
        return self.status_accepted | self.status_replaced | self.status_refused


@dataclass(frozen=True)
class Name:
    """One row of a checklist. Spec-free: representability is asked of the spec, not the row."""
    taxonid: str
    name_id: str | None
    name: str
    authorship: str | None
    rank: str
    status: str
    accepted_id: str | None
    parent_id: str | None
    family: str | None
    genus: str | None

    @property
    def label(self) -> str:
        return f"{self.name} {self.authorship}".strip() if self.authorship else self.name

    def rank_unsupported(self, spec: NameSourceSpec) -> bool:
        return self.rank.lower() not in spec.supported_ranks

    def is_refused(self, spec: NameSourceSpec) -> bool:
        return self.status in spec.status_refused or self.rank_unsupported(spec)

    def is_accepted(self, spec: NameSourceSpec) -> bool:
        return self.status in spec.status_accepted and not self.rank_unsupported(spec)


@dataclass(frozen=True)
class ArchiveMeta:
    version: str = ""
    pub_date: str = ""
    citation: str = ""

    @property
    def label(self) -> str:
        return f"{self.version} ({self.pub_date})".strip() if self.version else ""


@dataclass
class BuildReport:
    meta: ArchiveMeta
    rows: int = 0
    accepted: int = 0
    replaced: int = 0
    refused: int = 0
    dangling_accepted_ids: int = 0
    dangling_parent_ids: int = 0
    ranks: dict[str, int] = field(default_factory=dict)


def read_archive_meta(zf: zipfile.ZipFile) -> ArchiveMeta:
    """Version / pubDate / citation from eml.xml. Absent is fine — a hand-built archive
    need not carry provenance, and refusing it over a missing <version> would be pedantry."""
    if "eml.xml" not in zf.namelist():
        return ArchiveMeta()
    root = ElementTree.fromstring(zf.read("eml.xml"))

    def _first(tag: str) -> str:
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag and (el.text or "").strip():
                return el.text.strip()
        return ""

    return ArchiveMeta(version=_first("version"), pub_date=_first("pubDate"),
                       citation=_first("citation"))


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE name (
    taxonid    TEXT NOT NULL PRIMARY KEY,
    name_id    TEXT,
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

# A BINARY index yields `SCAN` for a case-insensitive prefix LIKE; only a NOCASE index lets
# SQLite turn it into `SEARCH … (name>? AND name<?)`. Over WCVP's 1.45 M rows that is the
# difference between ~100 ms and ~0.2 ms per keystroke.
INDEXES = [
    "CREATE INDEX ix_name_nocase ON name(name COLLATE NOCASE)",
    "CREATE INDEX ix_accepted ON name(accepted_id)",
    "CREATE INDEX ix_name_id ON name(name_id)",
]

_COLS = ("taxonid, name_id, name, authorship, rank, status, accepted_id, parent_id, "
         "family, genus")


def _rows(zf: zipfile.ZipFile, layout: ArchiveLayout, spec: NameSourceSpec) -> Iterator[tuple]:
    """Stream the Taxon core as insert tuples, validating as we go.

    Two source quirks are normalised here, once, so no consumer can get them wrong:

    * `acceptedNameUsageID` on an accepted row may **point at itself** (all 434 691 of WCVP's).
      Stored verbatim it reads as "synonym of itself". Normalised to NULL.
    * `scientificNameID` may carry a prefixed id ("ipni:304293-2"); the bare id is stored.

    An unrecognised taxonomicStatus raises: it means the source's vocabulary is not the one
    the spec partitions, and whether it is accepted / replaced / unrepresentable is a decision,
    not a guess (§2).
    """
    def cell(row: list[str], f: str) -> str:
        idx = layout.columns.get(f)
        if idx is None:
            return layout.defaults.get(f, "")
        return row[idx].strip() if idx < len(row) else ""

    with zf.open(layout.core_file) as raw:
        text = (line.decode("utf-8") for line in raw)
        reader = csv.reader(text, delimiter=layout.delimiter, quoting=layout.quoting)
        for _ in range(layout.header_lines):
            next(reader, None)
        for row in reader:
            if not row:
                continue
            status = cell(row, "status")
            if status not in spec.known_statuses:
                raise NameSourceError(
                    f"unknown taxonomicStatus {status!r} (taxonID {cell(row, 'taxonid')!r}). "
                    f"Decide whether it means accepted, replaced-by-another-name, or "
                    f"unrepresentable before importing this source — it is not guessed."
                )
            taxonid = cell(row, "taxonid")
            accepted = cell(row, "accepted_id") or None
            if accepted == taxonid:
                accepted = None
            name_id = cell(row, "name_id")
            if spec.name_id_prefix and name_id.startswith(spec.name_id_prefix):
                name_id = name_id[len(spec.name_id_prefix):]
            yield (
                taxonid,
                name_id or None,
                cell(row, "name"),
                cell(row, "authorship") or None,
                cell(row, "rank"),
                status,
                accepted,
                cell(row, "parent_id") or None,
                cell(row, "family") or None,
                cell(row, "genus") or None,
            )


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 10, delay: float = 0.2) -> None:
    """Atomically move `src` onto `dst`, tolerating a reader that has `dst` briefly open.

    POSIX replaces an open file happily. Windows raises PermissionError while any handle is
    open, and readers here are short-lived (open → query → close), so a short retry closes the
    race rather than failing a whole rebuild (#104).
    """
    import time

    for attempt in range(attempts):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise NameSourceError(
                    f"could not replace {dst}: it is open in another program. Close any other "
                    "window using this collection and try again."
                ) from None
            time.sleep(delay)


def build_index(archive: Path, db_path: Path, spec: NameSourceSpec,
                *, batch: int = 50_000) -> BuildReport:
    """Build the SQLite lookup index from a Darwin Core Archive.

    Deterministic and rebuildable: the target is replaced wholesale. Journalling is off
    because a half-built index is thrown away, not recovered.
    """
    with zipfile.ZipFile(archive) as zf:
        layout = read_layout(zf)
        if layout.core_file not in zf.namelist():
            raise NameSourceError(
                f"meta.xml points at {layout.core_file!r}, which is not in the archive")
        meta = read_archive_meta(zf)

        tmp = db_path.with_suffix(".building")
        tmp.unlink(missing_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(tmp)
        try:
            db.execute("PRAGMA journal_mode = OFF")
            db.execute("PRAGMA synchronous = OFF")
            db.executescript(SCHEMA)

            n = 0
            pending: list[tuple] = []
            for rec in _rows(zf, layout, spec):
                n += 1
                pending.append(rec)
                if len(pending) >= batch:
                    db.executemany("INSERT INTO name VALUES (?,?,?,?,?,?,?,?,?,?)", pending)
                    pending.clear()
            if pending:
                db.executemany("INSERT INTO name VALUES (?,?,?,?,?,?,?,?,?,?)", pending)

            for stmt in INDEXES:
                db.execute(stmt)

            counts = dict(db.execute("SELECT status, count(*) FROM name GROUP BY status"))
            ranks = dict(db.execute(
                "SELECT lower(rank), count(*) FROM name GROUP BY lower(rank) "
                "ORDER BY count(*) DESC"))

            # Referential health of the source's own data. Reported, not enforced: a dangling
            # link is the source's error and must not silently vanish, but neither should it
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
                ("slug", spec.slug),
                ("label", spec.label),
                ("version", meta.version),
                ("pub_date", meta.pub_date),
                ("citation", meta.citation or spec.citation),
                ("source_url", spec.source_url),
                ("license", spec.license),
                ("nomenclatural_code", spec.nomenclatural_code),
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

        _replace_with_retry(tmp, db_path)

    return BuildReport(
        meta=meta, rows=n,
        accepted=sum(counts.get(s, 0) for s in spec.status_accepted),
        replaced=sum(counts.get(s, 0) for s in spec.status_replaced),
        refused=sum(counts.get(s, 0) for s in spec.status_refused),
        dangling_accepted_ids=dangling_acc, dangling_parent_ids=dangling_par,
        ranks=ranks,
    )


# ---------------------------------------------------------------------------
# Query layer — read-only access to a built index
# ---------------------------------------------------------------------------

def _read_only_uri(path: Path) -> str:
    """A SQLite read-only URI for `path`, safe on every platform.

    Interpolating the path straight into `f"file:{path}?mode=ro"` is wrong twice over: a `%`
    in the path fails to open, and a `?` silently opens a *different*, empty database (the rest
    is parsed as query parameters). On Windows it also emits raw backslashes and an unescaped
    drive colon. pathname2url percent-escapes and yields `///C:/…` on Windows.
    """
    import urllib.request

    return "file:" + urllib.request.pathname2url(str(path)) + "?mode=ro"


def open_index(path: Path) -> sqlite3.Connection:
    """Open an index read-only. Raises IndexMissing if it has not been built.

    Cheap (~1.5 ms open+query+close). Callers open per query rather than holding a handle: a
    held handle locks the file on Windows (a rebuild then fails with PermissionError) and pins
    a stale inode on POSIX (the app keeps serving the old index after a rebuild).
    """
    if not path.exists():
        raise IndexMissing(f"no index at {path} — install it in Settings → Name datasets")
    db = sqlite3.connect(_read_only_uri(path), uri=True)
    db.row_factory = sqlite3.Row
    return db


def index_meta(db: sqlite3.Connection) -> dict[str, str]:
    """Provenance of the installed index — read from the file, never the network."""
    return dict(db.execute("SELECT key, value FROM meta"))


def _to_name(row: sqlite3.Row) -> Name:
    return Name(**{k: row[k] for k in Name.__dataclass_fields__})


def _escape(token: str) -> str:
    """Neutralise LIKE wildcards typed by the user — a bare '%' would match every name."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(term: str) -> tuple[str, list[str]]:
    """Anchor the first token so SQLite can seek ix_name_nocase instead of scanning.

    A leading wildcard on the first token turns SEARCH into SCAN (~100 ms/keystroke on WCVP).
    Later tokens keep their leading wildcard: "Quer rob" must match "Quercus robur".
    """
    tokens = term.split()
    clauses = [r"name LIKE ? ESCAPE '\'"]
    args = [f"{_escape(tokens[0])}%"]
    for tok in tokens[1:]:
        clauses.append(r"name LIKE ? ESCAPE '\'")
        args.append(f"%{_escape(tok)}%")
    return " AND ".join(clauses), args


def search(db: sqlite3.Connection, term: str, spec: NameSourceSpec, *,
           limit: int = 8, refused_limit: int = 3) -> list[Name]:
    """Multi-token prefix search, importable names first.

    Refused names are *shown* — hiding them invites the user to conclude the name does not
    exist and hand-create it, a silent invention. They rank last and are capped, so they can
    never displace a usable suggestion.
    """
    term = term.strip()
    if len(term) < 2:
        return []
    where, args = _where(term)

    accepted_sql = ",".join("?" * len(spec.status_accepted)) or "NULL"
    order = (f"CASE WHEN status IN ({accepted_sql}) THEN 0 ELSE 1 END, length(name), name")
    order_args = [*sorted(spec.status_accepted)]

    # Must mirror Name.is_refused exactly, or the ranking and the importer disagree.
    refused_clauses, refused_args = [], []
    if spec.status_refused:
        refused_clauses.append(f"status IN ({','.join('?' * len(spec.status_refused))})")
        refused_args += sorted(spec.status_refused)
    rk = ",".join("?" * len(spec.supported_ranks))
    refused_clauses.append(f"lower(rank) NOT IN ({rk})")
    refused_args += sorted(spec.supported_ranks)
    refused_sql = "(" + " OR ".join(refused_clauses) + ")"

    importable = db.execute(
        f"SELECT {_COLS} FROM name WHERE {where} AND NOT {refused_sql} "
        f"ORDER BY {order} LIMIT ?",
        (*args, *refused_args, *order_args, limit),
    ).fetchall()
    blocked = db.execute(
        f"SELECT {_COLS} FROM name WHERE {where} AND {refused_sql} "
        f"ORDER BY length(name), name LIMIT ?",
        (*args, *refused_args, refused_limit),
    ).fetchall()
    return [_to_name(r) for r in importable] + [_to_name(r) for r in blocked]


def get(db: sqlite3.Connection, taxonid: str) -> Name | None:
    row = db.execute(f"SELECT {_COLS} FROM name WHERE taxonid = ?", (taxonid,)).fetchone()
    return _to_name(row) if row else None


def accepted_name(db: sqlite3.Connection, row: Name) -> Name | None:
    """The name this source says to use instead. None for an accepted name.

    Real archives contain dangling accepted ids; a missing target returns None rather than a
    fabricated one, and the importer refuses such a row.
    """
    if not row.accepted_id:
        return None
    return get(db, row.accepted_id)


class NotImportable(NameSourceError):
    """This name cannot be represented by the local taxon model."""


def refusal_reason(db: sqlite3.Connection, row: Name, spec: NameSourceSpec) -> str:
    """Why this name cannot be imported, phrased without asserting a synonymy it is not."""
    if row.status in spec.status_refused:
        target = accepted_name(db, row)
        if row.status == "Misapplied":
            if target:
                return f"in {spec.label} this name is applied to {target.name}"
            return "this name is a misapplication of another name"
        if row.status == "Unplaced":
            return f"{spec.label} records no accepted placement for this name"
        return f"{spec.label} gives its status as “{row.status}”, which this database "
    if row.rank_unsupported(spec):
        return f"this database does not model the rank “{row.rank or 'no rank'}”"
    return ""


def lineage(db: sqlite3.Connection, row: Name, spec: NameSourceSpec) -> list[Name]:
    """The archive's own parent chain for `row`, ROOT FIRST.

    Follows `parentNameUsageID` rather than rebuilding lineage from the denormalised
    family/genus columns. That is what lets a source carry ranks the columns cannot express —
    this is how a species lands under its *subgenus* — and it is the source's own statement of
    where the name sits, not our reconstruction of it.

    Own-lineage (Epic #30): a synonym's chain is ITS OWN, never its accepted name's. Parenting
    a synonym under the accepted name's genus would *rename* it, because scientific_name is
    composed from the parent chain.

    A cycle or a dangling parent stops the walk rather than looping forever; the chain built so
    far is still valid (the source's error is reported at build time, not fabricated here).
    """
    chain = [row]
    seen = {row.taxonid}
    cur = row
    while cur.parent_id and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        parent = get(db, cur.parent_id)
        if parent is None:
            break
        chain.append(parent)
        cur = parent
    chain.reverse()
    return chain


def _entry(row: Name, spec: NameSourceSpec) -> dict:
    return {
        "name": row.name,
        "rank": row.rank.lower(),
        "authorship": row.authorship,
        "code": spec.nomenclatural_code,
        "source_id": row.taxonid,
        "name_id": row.name_id,
    }


_INFRA_RANKS = frozenset({"subspecies", "variety", "subvariety", "form", "subform"})


def _species_name_of(name: str) -> str | None:
    """The species binomial inside an infraspecific name: 'Carabus germarii germarii' →
    'Carabus germarii'. None when the name is not a trinomial we can split."""
    parts = (name or "").split()
    return " ".join(parts[:2]) if len(parts) >= 3 else None


def _species_ancestor(db: sqlite3.Connection, row: Name, spec: NameSourceSpec) -> dict | None:
    """The species a subspecies belongs to, when the archive's parent chain SKIPS it.

    Real checklists do this: every one of the 692 subspecies in the Coleoptera archive is
    parented straight under a *subgenus*, so the chain contains no species row and the
    infraspecific name has nothing to compose from.

    The trinomial itself names the species unambiguously, so the species is recovered from it —
    but the archive's OWN species row is preferred when it exists (it carries the authorship),
    and only a name is synthesised otherwise. Authorship is left NULL rather than guessed:
    the name is certain, the author is not.
    """
    sp_name = _species_name_of(row.name)
    if not sp_name:
        return None
    hit = db.execute(
        "SELECT " + _COLS + " FROM name WHERE name = ? AND lower(rank) = 'species' "
        "ORDER BY CASE WHEN accepted_id IS NULL THEN 0 ELSE 1 END LIMIT 1",
        (sp_name,),
    ).fetchone()
    if hit is not None:
        return _entry(_to_name(hit), spec)
    return {"name": sp_name, "rank": "species", "authorship": None,
            "code": spec.nomenclatural_code, "source_id": None, "name_id": None}


def chain_for(db: sqlite3.Connection, row: Name, spec: NameSourceSpec) -> dict:
    """Flatten a chosen name into what `taxa.get_or_create_from_chain` consumes.

    Raises NotImportable rather than coercing a name the model cannot hold: a refused status,
    a rank we do not model, or a synonym whose accepted target is missing from the archive (a
    dangling link — the caller must not invent one).

    Every ancestor in the chain must itself be a rank we model; an unmodelled rank in the
    middle of the lineage is skipped, not refused, because it is not the name being imported
    and its absence does not falsify the name's own placement.
    """
    if row.is_refused(spec):
        raise NotImportable(f"{row.label}: {refusal_reason(db, row, spec)}")

    accepted_chain = None
    if row.accepted_id:
        target = accepted_name(db, row)
        if target is None:
            raise NotImportable(
                f"{row.label}: {spec.label} links it to accepted name id "
                f"{row.accepted_id!r}, which is not in the archive (an error in the source)"
            )
        accepted_chain = chain_for(db, target, spec)["chain"]

    chain = [_entry(r, spec) for r in lineage(db, row, spec)
             if not r.rank_unsupported(spec)]

    # An infraspecific name composes from its SPECIES; if the archive's chain skips that rank,
    # insert it (see _species_ancestor) rather than let the name compose without it.
    if row.rank.lower() in _INFRA_RANKS and not any(e["rank"] == "species" for e in chain):
        sp = _species_ancestor(db, row, spec)
        if sp is None:
            raise NotImportable(
                f"{row.label}: a {row.rank.lower()} needs a species to belong to, and neither "
                f"the archive's parent chain nor the name itself supplies one"
            )
        chain.insert(len(chain) - 1, sp)   # directly above the leaf

    return {"chain": chain, "accepted_chain": accepted_chain}


def count(db: sqlite3.Connection, spec: NameSourceSpec) -> tuple[int, int]:
    """(importable, total) — what a bulk import would actually create, and the file's size."""
    total = db.execute("SELECT count(*) FROM name").fetchone()[0]
    rk = ",".join("?" * len(spec.supported_ranks))
    args: list[str] = [*sorted(spec.supported_ranks)]
    sql = f"SELECT count(*) FROM name WHERE lower(rank) IN ({rk})"
    # `status NOT IN (NULL)` is NULL, never true — a source with nothing refused (every status
    # representable) would report ZERO importable names. Omit the clause instead of emitting a
    # placeholder that silently matches nothing.
    if spec.status_refused:
        sql += f" AND status NOT IN ({','.join('?' * len(spec.status_refused))})"
        args += sorted(spec.status_refused)
    importable = db.execute(sql, args).fetchone()[0]
    return importable, total

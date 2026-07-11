"""User-added name datasets — the registry around `name_source` (EXPERIMENTAL).

A dataset is a Darwin Core Archive the user picks from their computer. It is **copied into**
`data/name_sources/<slug>/` (never referenced in place — the original may move or be deleted,
exactly as with the media store), indexed there, and registered in `config.json`.

Registered datasets are searched **after** local / TaxonWorks / WCVP: they are the last resort
in the name chain, not a competitor to the local DB.

The archive states its own `nomenclaturalCode` (a `default` field in meta.xml) and its own
columns, so nothing about a dataset is configured by hand — see `name_source.read_layout`.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app import config
from app.services import name_source as ns
from app.services.taxa import RANKS_BY_CODE

# Marked experimental in the UI: the shape of a third-party checklist is far less predictable
# than Kew's, and a name imported from one lands in the same taxon table as everything else.
EXPERIMENTAL = True

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "dataset"


@dataclass(frozen=True)
class Dataset:
    """A registered dataset: its config entry, resolved to paths + a NameSourceSpec."""
    slug: str
    label: str
    code: str
    archive_name: str

    @property
    def dir(self) -> Path:
        return config.name_sources_dir() / self.slug

    @property
    def db_path(self) -> Path:
        return self.dir / "index.sqlite"

    @property
    def archive_path(self) -> Path:
        return self.dir / self.archive_name

    @property
    def installed(self) -> bool:
        return self.db_path.exists()

    @property
    def spec(self) -> ns.NameSourceSpec:
        ranks = RANKS_BY_CODE.get(self.code)
        if not ranks:
            raise ns.NameSourceError(
                f"{self.label}: nomenclatural code {self.code!r} is not one this database "
                f"models ({', '.join(sorted(RANKS_BY_CODE))})."
            )
        return ns.NameSourceSpec(
            slug=self.slug,
            label=self.label,
            nomenclatural_code=self.code,
            # A dataset may only offer ranks its CODE has (RANKS_BY_CODE). A checklist that
            # carries a rank we do not model has that name refused at import and shown as
            # blocked in search — never coerced to a neighbouring rank.
            supported_ranks=frozenset(ranks),
            experimental=EXPERIMENTAL,
        )

    def open(self):
        return ns.open_index(self.db_path)


def list_datasets() -> list[Dataset]:
    return [
        Dataset(slug=e["slug"], label=e["label"], code=e["code"],
                archive_name=e["archive"])
        for e in config.get_config().name_sources
    ]


def get_dataset(slug: str) -> Dataset | None:
    return next((d for d in list_datasets() if d.slug == slug), None)


def _register(ds: Dataset) -> None:
    cfg = config.get_config()
    entry = {"slug": ds.slug, "label": ds.label, "code": ds.code,
             "archive": ds.archive_name, "experimental": EXPERIMENTAL}
    cfg.name_sources = [e for e in cfg.name_sources if e["slug"] != ds.slug] + [entry]
    config.save_config(cfg)


def install(archive_bytes: bytes, filename: str, *, label: str | None = None) -> tuple[Dataset, ns.BuildReport]:
    """Copy an archive into data/name_sources/<slug>/ and build its index.

    The label defaults to the file's own name; the nomenclatural code is read from the
    archive's meta.xml — **never** asked for, because the archive already states it and a
    hand-typed answer could contradict the data (§2).

    Nothing is registered until the index builds: a dataset that cannot be read must not
    appear installed. The half-written folder is removed on failure.
    """
    stem = Path(filename).stem
    label = (label or stem).strip() or stem
    slug = _slugify(stem)

    target_dir = config.name_sources_dir() / slug
    pre_existing = target_dir.exists()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / Path(filename).name

    try:
        archive_path.write_bytes(archive_bytes)

        # The archive declares its own code. Read it before committing to anything.
        import zipfile

        with zipfile.ZipFile(archive_path) as zf:
            layout = ns.read_layout(zf)
        code = (layout.nomenclatural_code or "").strip().upper()
        if not code:
            raise ns.NameSourceError(
                "the archive does not declare a nomenclaturalCode. Add it to meta.xml as a "
                "field default — the code is a property of the source and is never guessed."
            )
        if code not in RANKS_BY_CODE:
            raise ns.NameSourceError(
                f"the archive declares nomenclaturalCode {code!r}, which this database does "
                f"not model ({', '.join(sorted(RANKS_BY_CODE))})."
            )

        ds = Dataset(slug=slug, label=label, code=code, archive_name=archive_path.name)
        report = ns.build_index(archive_path, ds.db_path, ds.spec)
    except BaseException:
        if not pre_existing:
            shutil.rmtree(target_dir, ignore_errors=True)
        raise

    _register(ds)
    return ds, report


def rebuild(ds: Dataset) -> ns.BuildReport:
    """Re-index from the archive already copied into the dataset's folder."""
    if not ds.archive_path.exists():
        raise ns.NameSourceError(
            f"{ds.label}: the archive {ds.archive_name} is no longer in {ds.dir} — "
            "re-add the dataset from its file."
        )
    return ns.build_index(ds.archive_path, ds.db_path, ds.spec)


@dataclass
class ImportReport:
    """What a bulk import actually did — counted, never assumed."""
    imported: int = 0
    refused: int = 0
    failed: int = 0
    created: int = 0            # net new taxon rows
    mismatches: list[str] = None
    refusals: list[str] = None

    def __post_init__(self):
        self.mismatches = self.mismatches if self.mismatches is not None else []
        self.refusals = self.refusals if self.refusals is not None else []


def import_all(session, ds: Dataset, *, progress=None, batch: int = 200) -> ImportReport:
    """Import EVERY importable name from a dataset into the local taxon table.

    This is a bulk operation over thousands of names and it is not undoable from the UI — the
    caller must warn first. It is, however, **idempotent**: every name goes through the same
    `get_or_create_from_chain` seam as a single pick, so re-running it creates nothing new and
    only fills NULL fields on rows that already exist.

    A name the model cannot hold is **refused and counted**, never coerced (§2), and a row that
    fails for any other reason is counted and named rather than aborting the whole run — one
    bad row in a 10 000-name checklist must not lose the other 9 999. Progress is reported as
    (done, total) so a long run can show a bar.
    """
    from app.models import Taxon
    from app.services.taxa import get_or_create_from_chain

    spec = ds.spec
    report = ImportReport()
    before = session.query(Taxon).count()

    db = ds.open()
    try:
        total = db.execute("SELECT count(*) FROM name").fetchone()[0]
        rows = db.execute(f"SELECT {ns._COLS} FROM name").fetchall()
        for i, raw in enumerate(rows, start=1):
            row = ns._to_name(raw)
            try:
                chain = ns.chain_for(db, row, spec)
                get_or_create_from_chain(
                    session, chain["chain"],
                    accepted_chain=chain["accepted_chain"],
                    mismatches=report.mismatches,
                )
                report.imported += 1
            except ns.NotImportable as exc:
                report.refused += 1
                if len(report.refusals) < 50:      # enough to diagnose, not a second dataset
                    report.refusals.append(str(exc))
            except Exception as exc:               # noqa: BLE001 — one bad row must not abort
                report.failed += 1
                if len(report.refusals) < 50:
                    report.refusals.append(f"{row.label}: {exc}")
            if i % batch == 0:
                session.flush()
                if progress:
                    progress(i, total)
        session.flush()
        if progress:
            progress(total, total)
    finally:
        db.close()

    report.created = session.query(Taxon).count() - before
    return report


def remove(ds: Dataset) -> None:
    """Unregister a dataset and delete its folder (archive + index).

    Names already imported from it are NOT touched: they are local taxon rows now, exactly
    like an imported WCVP name (docs/plant_names.md §5 — an imported name is thereafter local
    and never rewritten). Removing the dataset removes the lookup, not the data.
    """
    cfg = config.get_config()
    cfg.name_sources = [e for e in cfg.name_sources if e["slug"] != ds.slug]
    config.save_config(cfg)
    shutil.rmtree(ds.dir, ignore_errors=True)

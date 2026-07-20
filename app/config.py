"""Application configuration — persisted to data/config.json.

Load once at startup via get_config().  Mutate and persist via save_config().
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_CONFIG_PATH = _DATA_DIR / "config.json"


@dataclass
class AppConfig:
    # TaxonWorks connection
    tw_base: str = "https://sfg.taxonworks.org/api/v1"
    tw_token: str = ""
    taxonpages_base: str = "https://catalog.curculionoidea.org"

    # NOTE: the collection identity (collectionCode / institutionCode) is NOT stored here.
    # It is a property of the repositories vocab — the repository flagged is_default
    # (migration 0050, #83) is the user's own collection, and both the catalog-number
    # prefix and a new specimen's repository_id derive from it. A configurable default that
    # references a DB entity belongs in the DB, never as a flat string here (same rule as
    # person defaults; see CLAUDE.md "Why person defaults live in the DB").

    # Nomenclatural codes shown in the biological-association object search by default.
    # DwC values: "ICN" (plants/fungi), "ICZN" (animals).
    bio_assoc_default_codes: list[str] = field(default_factory=lambda: ["ICN"])
    # Default tile layer shown when the map picker opens.
    # Values: "street" | "satellite" | "satellite_labels"
    map_default_layer: str = "street"

    # Folder where every printed label-queue PDF is archived (for reprint/audit).
    # Relative paths resolve against the project data/ dir; "" → data/printed_labels.
    printed_pdf_dir: str = ""

    # Digitize-tab layout. "normal" → wide multi-card page (Specimen | Identifications
    # paired, Event + Bio full-width below). "single_card" → guided stepper showing one
    # card at a time, advancing card-to-card with the real Save on the last step.
    digitize_layout: str = "normal"

    # Managed media store: every attached file is copied here, content-addressed by
    # SHA-256. Relative paths resolve against the project data/ dir; "" → data/media.
    media_dir: str = ""

    # Tier-2 one-click default for a media file's licence (the push_pin in the media
    # metadata editor inserts this). "" → no default. The rightsHolder default is a
    # person and lives in the DB (person_defaults.default_rights_holder_id), not here.
    default_license: str = ""

    # Privacy: the generic string substituted for a confidential person's name in
    # the DwC export (recordedBy / identifiedBy). The record is still exported; only
    # the name is obscured. Confidential specimens/events are dropped entirely.
    confidential_person_label: str = "Collector obscured (Privacy Policy)"

    # Printed-label borders, per label type. "black" → a thin solid cut-guide line
    # around each label; "none" → no border. Independent per type so the user can,
    # e.g., border identifier labels but not data labels. See labels._border_decl.
    label_border_data: str = "black"
    label_border_determination: str = "black"
    label_border_identifier: str = "black"

    # Folder holding the offline plant-name backbone: Kew's downloaded Darwin Core Archive,
    # the SQLite index built from it, and a README recording where both came from. Relative
    # paths resolve against data/; "" → data/wcvp. Neither file is the specimen DB — the index
    # is a read-only lookup table, rebuilt from the archive, never edited.
    wcvp_dir: str = ""

    # User-added offline name datasets (EXPERIMENTAL, see services/name_source.py). Each entry
    # is {"slug", "label", "code", "archive", "experimental"} — the archive filename inside
    # data/name_sources/<slug>/, beside the index built from it. Registered here rather than in
    # the DB because these are FILES and settings, not DB entities: nothing references them by
    # FK, so the person-defaults rule ("a default that references a DB entity belongs in the
    # DB") does not apply. Searched AFTER local / TaxonWorks / WCVP.
    name_sources: list[dict] = field(default_factory=list)


def name_sources_dir() -> Path:
    """Root folder holding every user-added name dataset, one sub-folder per slug.

    Not created here: its absence simply means no dataset is installed.
    """
    return _DATA_DIR / "name_sources"


def printed_pdf_dir() -> Path:
    """Resolved archival folder for printed label PDFs (created if missing)."""
    raw = get_config().printed_pdf_dir
    path = Path(raw) if raw else (_DATA_DIR / "printed_labels")
    if not path.is_absolute():
        path = _DATA_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_dir() -> Path:
    """Resolved managed media store (created if missing). Files live content-addressed
    under here as <xx>/<sha256>.<ext>."""
    raw = get_config().media_dir
    path = Path(raw) if raw else (_DATA_DIR / "media")
    if not path.is_absolute():
        path = _DATA_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def geo_dir() -> Path:
    """Resolved folder for the QGIS geo mirror (created if missing): the GeoPackage the
    map is written to (collection.gpkg) + a starter QGIS project (collection.qgz)."""
    path = _DATA_DIR / "geo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def wcvp_dir() -> Path:
    """Resolved folder holding the WCVP archive, the index built from it, and its README.

    WCVP is a *name source* like any other (services/name_source.py), so it lives beside the
    user-added ones under `data/name_sources/wcvp` — one place to look for every offline
    checklist, one place to back up. `migrate_legacy_dirs()` moves an older `data/wcvp` there.

    Not created here: its absence means "no plant backbone installed", which the caller
    reports and the Settings card offers to fix.
    """
    raw = get_config().wcvp_dir
    path = Path(raw) if raw else (name_sources_dir() / "wcvp")
    if not path.is_absolute():
        path = _DATA_DIR / path
    return path


def _legacy_wcvp_dir() -> Path:
    """Where WCVP lived before it became a name source (data/wcvp)."""
    return _DATA_DIR / "wcvp"


def migrate_legacy_dirs() -> str | None:
    """Move an existing `data/wcvp` into `data/name_sources/wcvp`. Idempotent.

    Called once at startup (run.py), before anything reads the index. A rename within the same
    data folder — never a re-download: the archive is ~88 MB and the index ~270 MB, and a
    "missing" index would otherwise send the user to fetch both again. Only moves when the
    destination does not exist, so a half-migrated state can never overwrite a good install.
    Returns a message for the log, or None when there was nothing to do.
    """
    if get_config().wcvp_dir:
        return None                       # the user configured an explicit path; leave it alone
    legacy, target = _legacy_wcvp_dir(), name_sources_dir() / "wcvp"
    if not legacy.is_dir() or target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(target)
    return f"moved {legacy} → {target}"


def wcvp_db_path() -> Path:
    """Resolved path of the offline WCVP index, inside wcvp_dir()."""
    return wcvp_dir() / "wcvp.sqlite"


def wcvp_archive_path() -> Path:
    """Resolved path of the downloaded archive the index was built from."""
    return wcvp_dir() / "wcvp_dwca.zip"


_instance: AppConfig | None = None


def get_config() -> AppConfig:
    global _instance
    if _instance is None:
        _instance = _load()
    return _instance


def save_config(cfg: AppConfig) -> None:
    global _instance
    _instance = cfg
    _CONFIG_PATH.write_text(json.dumps(asdict(cfg), indent=2))


def _load() -> AppConfig:
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            return AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
        except Exception:
            pass
    return AppConfig()

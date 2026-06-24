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

    # Collection identity — injected as background defaults into every DwC export row.
    # institution_code: maps to dwc:institutionCode (TW Repository lookup key).
    # collection_code:  maps to dwc:collectionCode (TW catalog-number namespace lookup key).
    # TW uses (institutionCode, collectionCode) together to find the namespace that
    # prefixes the catalogNumber in TW's internal identifier, e.g. "Jilg ab12".
    institution_code: str = "Jilg"
    collection_code: str = "Jilg"

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


def printed_pdf_dir() -> Path:
    """Resolved archival folder for printed label PDFs (created if missing)."""
    raw = get_config().printed_pdf_dir
    path = Path(raw) if raw else (_DATA_DIR / "printed_labels")
    if not path.is_absolute():
        path = _DATA_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


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

"""Application configuration — persisted to config.json at the project root.

Load once at startup via get_config().  Mutate and persist via save_config().
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"


@dataclass
class AppConfig:
    # TaxonWorks connection
    tw_base: str = "https://sfg.taxonworks.org/api/v1"
    tw_token: str = ""
    taxonpages_base: str = "https://catalog.curculionoidea.org"
    # Nomenclatural codes shown in the biological-association object search by default.
    # DwC values: "ICN" (plants/fungi), "ICZN" (animals).
    bio_assoc_default_codes: list[str] = field(default_factory=lambda: ["ICN"])
    # Default tile layer shown when the map picker opens.
    # Values: "street" | "satellite" | "satellite_labels"
    map_default_layer: str = "street"


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

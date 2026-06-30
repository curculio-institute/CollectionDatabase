"""Registry of single-name controlled vocabularies.

Each entry is a ``Vocabulary`` (see app/services/vocab.py) plus the display
metadata the Controlled Vocabularies tab needs to render an edit/merge section.
Add a future single-name vocabulary by: creating its model + migration, then
appending one entry here — it appears in the tab and gets a reusable dropdown
field automatically.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.preparation import Preparation
from app.models.disposition import Disposition
from app.models.habitat import Habitat
from app.models.sampling_protocol import SamplingProtocol
from app.models.geography import (
    Country, StateProvince, County, Island, AdministrativeRegion,
)
from app.services.vocab import Vocabulary


@dataclass(frozen=True)
class VocabSpec:
    key: str            # stable id (used for refresh wiring)
    vocab: Vocabulary
    title: str          # section heading in the Controlled Vocabularies tab
    help: str           # one-line description under the heading
    add_label: str      # the "Add …" button label
    field_label: str    # the data-entry field label in forms


preparation_vocab = Vocabulary(Preparation, ref_table="preparation", noun="preparation")
disposition_vocab = Vocabulary(Disposition, ref_table="disposition", noun="disposition")
habitat_vocab = Vocabulary(Habitat, ref_table="habitat", noun="habitat")
sampling_protocol_vocab = Vocabulary(
    SamplingProtocol, ref_table="sampling_protocol", noun="sampling protocol")

PREPARATION = VocabSpec(
    key="preparations",
    vocab=preparation_vocab,
    title="Preparations",
    help="Values used in the specimen preparations field (e.g. pinned, in ethanol).",
    add_label="Add preparation",
    field_label="preparations",
)

DISPOSITION = VocabSpec(
    key="disposition",
    vocab=disposition_vocab,
    title="Dispositions",
    help="Holding status of a specimen (e.g. in collection, on loan, loaned to Jeffrey).",
    add_label="Add disposition",
    field_label="disposition",
)

HABITAT = VocabSpec(
    key="habitat",
    vocab=habitat_vocab,
    title="Habitats",
    help="Values used in the collecting-event habitat field (e.g. broadleaf forest edge).",
    add_label="Add habitat",
    field_label="habitat",
)

SAMPLING_PROTOCOL = VocabSpec(
    key="sampling_protocol",
    vocab=sampling_protocol_vocab,
    title="Sampling protocols",
    help="Collecting methods used in the samplingProtocol field (e.g. beating, pitfall trap).",
    add_label="Add sampling protocol",
    field_label="samplingProtocol",
)

# ── Geography facets (administrative hierarchy) ───────────────────────────────
# Single-name vocabularies on collecting_event so locality values are consistent
# enough for the faceted Explore search + mergeable (Deutschland → Germany). #40.
country_vocab = Vocabulary(Country, ref_table="country", noun="country")
state_province_vocab = Vocabulary(StateProvince, ref_table="state_province", noun="state / province")
county_vocab = Vocabulary(County, ref_table="county", noun="county")
island_vocab = Vocabulary(Island, ref_table="island", noun="island")
administrative_region_vocab = Vocabulary(
    AdministrativeRegion, ref_table="administrative_region", noun="administrative region")

COUNTRY = VocabSpec(
    key="country", vocab=country_vocab, title="Countries",
    help="Country names (English). Merge variants like Deutschland → Germany.",
    add_label="Add country", field_label="country",
)
STATE_PROVINCE = VocabSpec(
    key="state_province", vocab=state_province_vocab, title="States / provinces",
    help="State / province (English), e.g. Bavaria — the Bundesland level.",
    add_label="Add state / province", field_label="stateProvince",
)
ADMINISTRATIVE_REGION = VocabSpec(
    key="administrative_region", vocab=administrative_region_vocab, title="Administrative regions",
    help="Sub-state region between state and county, e.g. Oberbayern (Regierungsbezirk). Local field, no DwC term.",
    add_label="Add administrative region", field_label="administrative region",
)
COUNTY = VocabSpec(
    key="county", vocab=county_vocab, title="Counties",
    help="County / district (local name), e.g. Landkreis Berchtesgadener Land.",
    add_label="Add county", field_label="county",
)
ISLAND = VocabSpec(
    key="island", vocab=island_vocab, title="Islands",
    help="Island names (local).",
    add_label="Add island", field_label="island",
)

# Ordered list consumed by the Controlled Vocabularies tab.
VOCAB_REGISTRY: list[VocabSpec] = [
    PREPARATION, DISPOSITION, HABITAT, SAMPLING_PROTOCOL,
    COUNTRY, STATE_PROVINCE, ADMINISTRATIVE_REGION, COUNTY, ISLAND,
]

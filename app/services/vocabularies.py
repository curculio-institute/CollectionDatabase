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
from app.models.habitat import Habitat
from app.models.sampling_protocol import SamplingProtocol
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

# Ordered list consumed by the Controlled Vocabularies tab.
VOCAB_REGISTRY: list[VocabSpec] = [PREPARATION, HABITAT, SAMPLING_PROTOCOL]

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

PREPARATION = VocabSpec(
    key="preparations",
    vocab=preparation_vocab,
    title="Preparations",
    help="Values used in the specimen preparations field (e.g. pinned, in ethanol).",
    add_label="Add preparation",
    field_label="preparations",
)

# Ordered list consumed by the Controlled Vocabularies tab.
VOCAB_REGISTRY: list[VocabSpec] = [PREPARATION]

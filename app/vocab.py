"""Controlled vocabulary — single source of truth for both UI and services.

Lives at app/vocab.py (not under app/ui/) on purpose: these lists and their
display mappings are shared by UI dropdowns AND service-layer rendering
(labels.py builds determination-label PDFs), and services must never import the
UI layer. A layer-neutral home lets both sides import the same constants.

These were previously duplicated across main.py, records_tab.py, import_assign.py,
mounting_session.py, identification_list.py, specimen_form.py and labels.py
(audit bug m-5). Edit a list here and every consumer picks it up.

Convention: the empty-string sentinel ("") is always **last** so the blank option
renders at the bottom of every ui.select (see CLAUDE.md → UI conventions).

Note: BASIS_OPTIONS and DISPOSITION_OPTIONS mirror values constrained by DB CHECKs
(migration 0019). They MUST stay a subset of those constraints or saving raises an
IntegrityError. Keep them in sync if a future migration changes the allowed values.
"""
from __future__ import annotations

# sex is free-text in the DB (no CHECK), so this list can grow without a
# migration. Keep SEX_OPTIONS and SEX_SYMBOLS in step: every value that has a
# typographic glyph belongs in both. "undetermined"/"" have no glyph by design.
SEX_OPTIONS = ["male", "female", "gynandromorph", "undetermined", ""]

# Stored sex value → typographic symbol, for compact display on labels (PDF) and
# in the determination list. A value absent here simply renders no glyph.
SEX_SYMBOLS = {"male": "♂", "female": "♀", "gynandromorph": "⚥"}

LIFE_STAGE_OPTIONS = ["adult", "larva", "pupa", "egg", ""]

# Must match ck_co_basis_of_record (migration 0019): exactly these three values.
# basisOfRecord is NOT NULL, so no blank option. (TaxonWorks' DwC import accepts
# only PreservedSpecimen/FossilSpecimen — HumanObservation is local-only, see
# CLAUDE.md §5b — but it is a valid DB value and may be selected here.)
BASIS_OPTIONS = ["PreservedSpecimen", "FossilSpecimen", "HumanObservation"]

# Must match ck_co_disposition (migration 0019): these six, or NULL. The trailing
# "" maps to NULL on save.
DISPOSITION_OPTIONS = [
    "in collection", "on loan", "donated",
    "exchanged", "missing", "destroyed", "",
]

# SUPERSEDED: samplingProtocol is now a DB-backed controlled vocabulary
# (sampling_protocol table, migration 0040 seeded it with this set). This constant
# is retained only as the historical seed source; it is NOT used at runtime — the
# form reads the table via sampling_protocol_vocab.
SAMPLING_PROTOCOLS = [
    "hand collecting", "sweep net", "beating", "pitfall trap",
    "light trap", "sifting", "bark peeling", "rearing", "Berlese funnel",
    "yellow pan trap", "window trap", "observation", "",
]

# Media licences (Creative Commons family used by iNaturalist/GBIF, plus the two
# non-CC extremes). media.license is plain TEXT (no CHECK), so this is a convenience
# dropdown, not a constrained vocabulary. Blank sentinel last.
LICENSE_OPTIONS = [
    "CC0", "CC BY", "CC BY-SA", "CC BY-NC", "CC BY-NC-SA",
    "CC BY-ND", "CC BY-NC-ND", "All rights reserved", "Public domain", "",
]

# Seed values for a brand-new specimen (create mode). Single source of truth for
# the Digitize standard form (specimen_form) and the Mounting Session, so the
# create contract lives in one place rather than being duplicated as literals.
# NOTE: `preparations` is intentionally NOT here — it defaults to "" in standard
# digitizing but to "pinned" in a Mounting Session (where specimens are, by
# definition, being pinned). That divergence is per-workflow, not an oversight.
NEW_SPECIMEN_DEFAULTS = {
    "individual_count": 1,
    "life_stage":       "adult",
    "disposition":      "in collection",
    "basis_of_record":  "PreservedSpecimen",
}

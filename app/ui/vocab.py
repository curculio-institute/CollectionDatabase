"""Controlled-vocabulary option lists for UI dropdowns — single source of truth.

These were previously duplicated across main.py, records_tab.py, import_assign.py,
mounting_session.py, identification_list.py and specimen_form.py (audit bug m-5).
Edit a list here and every dropdown picks it up.

Convention: the empty-string sentinel ("") is always **last** so the blank option
renders at the bottom of every ui.select (see CLAUDE.md → UI conventions).

Note: BASIS_OPTIONS and DISPOSITION_OPTIONS mirror values constrained by DB CHECKs
(migration 0019). They MUST stay a subset of those constraints or saving raises an
IntegrityError. Keep them in sync if a future migration changes the allowed values.
"""
from __future__ import annotations

SEX_OPTIONS = ["male", "female", "undetermined", ""]

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

SAMPLING_PROTOCOLS = [
    "hand collecting", "sweep net", "beating", "pitfall trap",
    "light trap", "sifting", "bark peeling", "rearing", "Berlese funnel",
    "yellow pan trap", "window trap", "observation", "",
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

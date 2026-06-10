"""Controlled-vocabulary option lists for UI dropdowns — single source of truth.

These were previously duplicated across main.py, records_tab.py, import_assign.py,
mounting_session.py, identification_list.py and specimen_form.py (audit bug m-5).
Edit a list here and every dropdown picks it up.

Convention: the empty-string sentinel ("") is always **last** so the blank option
renders at the bottom of every ui.select (see CLAUDE.md → UI conventions).

Note: BASIS_OPTIONS and DISPOSITION_OPTIONS mirror values that also have DB CHECK
constraints — keep them in sync with the corresponding migrations if either set
of allowed values changes.
"""
from __future__ import annotations

SEX_OPTIONS = ["male", "female", "undetermined", ""]

LIFE_STAGE_OPTIONS = ["adult", "larva", "pupa", "egg", ""]

BASIS_OPTIONS = [
    "PreservedSpecimen", "FossilSpecimen", "LivingSpecimen",
    "HumanObservation", "MachineObservation",
]

DISPOSITION_OPTIONS = [
    "in collection", "on loan", "donated",
    "exchanged", "missing", "destroyed", "",
]

SAMPLING_PROTOCOLS = [
    "hand collecting", "sweep net", "beating", "pitfall trap",
    "light trap", "sifting", "bark peeling", "rearing", "Berlese funnel",
    "yellow pan trap", "window trap", "observation", "",
]

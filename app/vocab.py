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

Note: BASIS_OPTIONS mirrors values constrained by a DB CHECK (migration 0019). It
MUST stay a subset of that constraint or saving raises an IntegrityError. Keep it in
sync if a future migration changes the allowed values. (disposition was a similar
fixed list until migration 0048 (#76) turned it into an editable controlled
vocabulary — see vocabularies.disposition_vocab.)
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

# Open-nomenclature qualifiers for a determination (dwc:identificationQualifier). A CLOSED
# standard set — hard-enforced by a DB CHECK (migration 0058), not an editable vocab — because
# each carries a specific taxonomic meaning that must not drift:
#   cf.    confer — "compare with"; tentative, resembles the named species
#   aff.   affinis — has affinity to but is distinct from it
#   nr.    "near" — used like aff.
#   agg.   aggregate — a named species-aggregate (e.g. "Rubus fruticosus agg.")
#   gr.    of the species group
#   ?      identification uncertain
#   sp.    species undetermined (a genus-level determination)
#   spp.   several/multiple species
#   indet. indeterminate — cannot be determined further
# "" (blank) = a definite identification, stored as NULL. cf. is first (fast one-key add);
# the blank stays last per the UI convention. render_identification inserts the value verbatim
# after the genus-group, so this list is the ONLY place the semantics live.
IDENTIFICATION_QUALIFIER_OPTIONS = [
    "cf.", "aff.", "nr.", "agg.", "gr.", "?", "sp.", "spp.", "indet.", "",
]
# The non-blank values, for the DB CHECK + import validation (blank is stored as NULL).
IDENTIFICATION_QUALIFIERS = tuple(q for q in IDENTIFICATION_QUALIFIER_OPTIONS if q)

# Must match ck_co_basis_of_record (migration 0019): exactly these three values.
# basisOfRecord is NOT NULL, so no blank option. (TaxonWorks' DwC import accepts
# only PreservedSpecimen/FossilSpecimen — HumanObservation is local-only, see
# CLAUDE.md §5b — but it is a valid DB value and may be selected here.)
BASIS_OPTIONS = ["PreservedSpecimen", "FossilSpecimen", "HumanObservation"]

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
# NOTE: `preparations` is intentionally NOT here — in standard digitizing it pre-fills
# with the flagged default preparation (preparation.is_default, migration 0052; empty if
# none is flagged), while a Mounting Session forces "pinned" (specimens are, by definition,
# being pinned). That divergence is per-workflow, not an oversight.
# NOTE: `disposition` is intentionally NOT here either — a new specimen's disposition
# starts EMPTY and is set manually (or in bulk via the Batch tools tab). The former
# hardcoded "in collection" default was dropped.
NEW_SPECIMEN_DEFAULTS = {
    "individual_count": 1,
    "life_stage":       "adult",
    "basis_of_record":  "PreservedSpecimen",
}

# Nomenclatural codes. A closed standard vocabulary — these are the codes themselves, not
# user-coined terms, so they stay a fixed list and never become an editable vocab table.
# Mirrored by the CHECK on taxon."dwc:nomenclaturalCode" (migration 0054); keep in step.
NOMENCLATURAL_CODES = ["ICZN", "ICN", "ICNP", "ICVCN"]

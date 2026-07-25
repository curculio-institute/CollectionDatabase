"""Darwin Core export — eligibility policy and the occurrence projection (#149).

Two things live here, and the split matters:

* **`export_decision()`** — *may* this specimen leave the building, and with which fields
  blanked. Pure policy, derived from the three `confidential` flags and person consent.
* **the occurrence projection** — *what* a specimen looks like as a DwC row.

Both the TaxonWorks sync comparison and the emitted CSV read the same two functions, so a
record the comparison calls "withheld" can never be the record the CSV writes, and a field
the CSV emits can never be a field the comparison forgot to diff.

Withholding is deliberately **loud in the report and silent in the file**: an ineligible
specimen is listed, with its reason, in the sync tab — it simply never reaches the CSV. A
blanked name is written as *no value at all*, never a placeholder (see AppConfig for why).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_config
from app.models import CollectingEvent, CollectionObject, Person


# ── Eligibility ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExportDecision:
    """Whether a specimen may be exported, and what must be withheld from its row.

    `reasons` is populated only when `eligible` is False; `blank_fields` only when the
    record IS exported but a field must be emitted empty. The two are never both set —
    a record that is not exported has no fields to blank.
    """
    eligible: bool
    reasons: tuple[str, ...] = ()
    blank_fields: tuple[str, ...] = ()
    # Why each blanked field was blanked, for the report ("recordedBy: has not consented").
    blank_notes: tuple[str, ...] = ()

    @property
    def withheld(self) -> bool:
        return not self.eligible


def _recorded_by(event: CollectingEvent | None) -> Person | None:
    return event.recorded_by_person if event is not None else None


def export_decision(
    co: CollectionObject,
    *,
    event: CollectingEvent | None = None,
) -> ExportDecision:
    """Decide whether `co` may be exported to TaxonWorks, and what must be blanked.

    `event` defaults to the specimen's own collecting event; pass it explicitly only to
    avoid a lazy load when the caller already holds it.

    The rules, in the order the issue states them (#149 Step 1.2 / Step 3.2):

    1. the specimen is flagged confidential           → withheld
    2. its collecting event is flagged confidential    → withheld (the event withholds
       *all* of its specimens — you cannot keep the occurrence but blank the locality,
       that breaks the record)
    3. the event's recordedBy person is confidential   → **withheld, always**
    4. the recordedBy person is neither confidential
       nor consent_approved                           → recordedBy blanked, or withheld
       (`AppConfig.tw_export_nonconsent`)

    Rule 3 has **no setting**: a confidential collector is never exported, in any form.
    The flag exists to be obeyed, not weighed — so there is deliberately no configuration
    that turns it into "export the record without the name". Rule 4 covers the genuinely
    undecided case (nobody asked the person yet), and that is the only choice offered.

    The two rules cannot overlap: `confidential` and `consent_approved` are mutually
    exclusive at the DB level, so a confidential person also reads as "not consented" —
    rule 4 therefore tests `not person.confidential` and rule 3 owns that case alone.

    `identifiedBy` is never blanked and never withholds a record.
    """
    cfg = get_config()
    event = event if event is not None else co.collecting_event

    reasons: list[str] = []
    blank_fields: list[str] = []
    blank_notes: list[str] = []

    if co.confidential:
        reasons.append("specimen is flagged confidential")
    if event is not None and event.confidential:
        reasons.append("collecting event is flagged confidential")

    person = _recorded_by(event)
    if person is not None:
        if person.confidential:
            # No setting here, by design — a confidential collector is never exported.
            reasons.append(f"recordedBy {person.full_name} is confidential")
        elif not person.consent_approved:
            if cfg.tw_export_nonconsent == "consented_only":
                reasons.append(f"recordedBy {person.full_name} has not consented")
            else:                                    # "name_removed" — the default
                blank_fields.append("recordedBy")
                blank_notes.append(f"recordedBy: {person.full_name} has not consented")

    if reasons:
        # A withheld record has no fields to blank — drop them so the report cannot show
        # a redaction for a row that is never written.
        return ExportDecision(eligible=False, reasons=tuple(reasons))
    return ExportDecision(
        eligible=True,
        blank_fields=tuple(blank_fields),
        blank_notes=tuple(blank_notes),
    )

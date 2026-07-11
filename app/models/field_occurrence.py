from __future__ import annotations
import uuid
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


def _new_occurrence_id() -> str:
    """A fresh globally-unique DwC occurrenceID for an observation."""
    return str(uuid.uuid4())


class FieldOccurrence(Base, TimestampMixin):
    """A HumanObservation — something recorded but NOT physically collected.

    The general home for observations (a host plant a beetle was collected on; a
    beetle seen but not taken), mirroring TaxonWorks' FieldOccurrence (the sibling
    of CollectionObject). See docs/field_occurrence.md.

    Deliberately unlike CollectionObject: **no catalog_number** (there is no pinned
    physical label) and **no repository / preparation / disposition** (nothing is
    held). Identity is its own ``occurrence_id`` (a UUID, DwC occurrenceID) — so the
    specimen catalog-number invariant is untouched: specimens are keyed by
    catalogNumber, observations by occurrenceID.

    Its taxon determination is a ``taxon_determination`` row via that table's subject
    exclusive arc (collection_object XOR field_occurrence), so the identification —
    including the open-nomenclature qualifier — reuses the specimen machinery.
    """

    __tablename__ = "field_occurrence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable, globally-unique observation identity (DwC occurrenceID). Generated,
    # since an observation carries no human-facing physical code.
    occurrence_id: Mapped[str] = mapped_column(
        "dwc:occurrenceID", String, nullable=False, default=_new_occurrence_id)

    collecting_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("collecting_event.id", ondelete="RESTRICT"), nullable=False)

    basis_of_record: Mapped[str] = mapped_column(
        "dwc:basisOfRecord", String, nullable=False, default="HumanObservation")
    individual_count: Mapped[int] = mapped_column(
        "dwc:individualCount", Integer, nullable=False, default=1)
    sex: Mapped[Optional[str]] = mapped_column("dwc:sex", String, nullable=True)
    life_stage: Mapped[Optional[str]] = mapped_column("dwc:lifeStage", String, nullable=True)
    occurrence_remarks: Mapped[Optional[str]] = mapped_column(
        "dwc:occurrenceRemarks", String, nullable=True)
    # Local-only privacy flag (mirrors collection_object): a confidential occurrence is
    # dropped from the DwC export entirely. Never pushed to TaxonWorks.
    confidential: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("dwc:occurrenceID", name="uq_fo_occurrence_id"),
        CheckConstraint('"dwc:individualCount" >= 0', name="ck_fo_individual_count_non_negative"),
        CheckConstraint(
            "\"dwc:basisOfRecord\" IN ('HumanObservation', 'MachineObservation')",
            name="ck_fo_basis_of_record",
        ),
        CheckConstraint("confidential IN (0, 1)", name="ck_fo_confidential"),
    )

    collecting_event: Mapped["CollectingEvent"] = relationship("CollectingEvent")
    determinations: Mapped[List["TaxonDetermination"]] = relationship(
        "TaxonDetermination", back_populates="field_occurrence",
        cascade="all, delete-orphan")
    object_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation",
        foreign_keys="BiologicalAssociation.object_field_occurrence_id",
        back_populates="object_field_occurrence")

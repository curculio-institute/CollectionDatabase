from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class CollectionObject(Base, TimestampMixin):
    """One physical specimen or lot. DwC columns carry dwc: prefix.

    dwc:catalogNumber is NOT NULL — the stable sync join key with TaxonWorks.
    dwc:collectionCode is NOT NULL — the TW catalog-number namespace (e.g. "Jilg").
    dwc:institutionCode is NOT NULL — stored per row; configured in Settings.
      Together institutionCode + collectionCode identify the TW namespace: TW looks up
      (institutionCode, collectionCode) → Namespace → stores identifier as
      "[namespace.short_name] [catalogNumber]" (e.g. "Jilg ab12").
    """

    __tablename__ = "collection_object"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collecting_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collecting_event.id", ondelete="RESTRICT"), nullable=True)

    catalog_number: Mapped[str] = mapped_column("dwc:catalogNumber", String, nullable=False)
    collection_code: Mapped[str] = mapped_column("dwc:collectionCode", String, nullable=False)
    institution_code: Mapped[str] = mapped_column("dwc:institutionCode", String, nullable=False, server_default="")

    basis_of_record: Mapped[str] = mapped_column("dwc:basisOfRecord", String, nullable=False, default="PreservedSpecimen")
    individual_count: Mapped[int] = mapped_column("dwc:individualCount", Integer, nullable=False, default=1)
    life_stage: Mapped[Optional[str]] = mapped_column("dwc:lifeStage", String, nullable=True)
    sex: Mapped[Optional[str]] = mapped_column("dwc:sex", String, nullable=True)
    # "in collection" | "on loan" | "donated" | "exchanged" | "missing" | "destroyed"
    disposition: Mapped[Optional[str]] = mapped_column("dwc:disposition", String, nullable=True)
    preparations: Mapped[Optional[str]] = mapped_column("dwc:preparations", String, nullable=True)
    type_status: Mapped[Optional[str]] = mapped_column("dwc:typeStatus", String, nullable=True)
    occurrence_remarks: Mapped[Optional[str]] = mapped_column("dwc:occurrenceRemarks", String, nullable=True)

    __table_args__ = (
        CheckConstraint('"dwc:individualCount" >= 0', name="ck_co_individual_count_non_negative"),
        CheckConstraint(
            "\"dwc:basisOfRecord\" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')",
            name="ck_co_basis_of_record",
        ),
        CheckConstraint(
            "\"dwc:disposition\" IS NULL OR \"dwc:disposition\" IN "
            "('in collection', 'on loan', 'donated', 'exchanged', 'missing', 'destroyed')",
            name="ck_co_disposition",
        ),
    )

    collecting_event: Mapped[Optional["CollectingEvent"]] = relationship("CollectingEvent", back_populates="collection_objects")
    determinations: Mapped[List["TaxonDetermination"]] = relationship(
        "TaxonDetermination", back_populates="collection_object", cascade="all, delete-orphan")
    subject_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation", foreign_keys="BiologicalAssociation.subject_collection_object_id",
        back_populates="subject_collection_object")
    object_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation", foreign_keys="BiologicalAssociation.object_collection_object_id",
        back_populates="object_collection_object")
    label_codes: Mapped[List["LabelCode"]] = relationship(
        "LabelCode", back_populates="collection_object")

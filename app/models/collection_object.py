from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint, UniqueConstraint
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
    # "in collection" | "on loan" | "donated" | "exchanged" | "missing" | "destroyed"
    disposition: Mapped[Optional[str]] = mapped_column("dwc:disposition", String, nullable=True)
    # preparations is a controlled vocabulary (FK → preparation), not free text —
    # so it can be edited/merged like persons. The DwC `preparations` string is
    # resolved from preparation.name at export time (mirrors recordedBy/identifiedBy).
    preparation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("preparation.id", ondelete="RESTRICT"), nullable=True)
    occurrence_remarks: Mapped[Optional[str]] = mapped_column("dwc:materialEntityRemarks", String, nullable=True)

    __table_args__ = (
        # Catalog number is unique per collection, not globally — foreign datasets
        # may reuse numbers under their own collectionCode. (Was an unnamed UNIQUE in
        # the live schema, undeclared in the model — that gap dropped it in migration
        # 0029 until this was added. See CLAUDE.md migration discipline.)
        UniqueConstraint("dwc:collectionCode", "dwc:catalogNumber", name="uq_co_collection_catalog"),
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
    preparation: Mapped[Optional["Preparation"]] = relationship("Preparation")
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

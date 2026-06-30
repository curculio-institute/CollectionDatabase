from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class CollectionObject(Base, TimestampMixin):
    """One physical specimen or lot. DwC columns carry dwc: prefix.

    dwc:catalogNumber is NOT NULL — the stable, immutable sync join key with TaxonWorks.
    repository_id is NOT NULL — the FK to the owning collection/institution (migration
      0047, #75). It is the single source of truth for collectionCode / institutionCode /
      ownerInstitutionCode (resolved through the repository at DwC export time); the old
      denormalised dwc:collectionCode + dwc:institutionCode text columns were dropped.
      Re-homing a specimen to another collection (gift/exchange) re-points this FK; the
      catalog number never changes, so its code prefix may then differ from the repository.
    """

    __tablename__ = "collection_object"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collecting_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collecting_event.id", ondelete="RESTRICT"), nullable=True)

    catalog_number: Mapped[str] = mapped_column("dwc:catalogNumber", String, nullable=False)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repository.id", ondelete="RESTRICT"), nullable=False)

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
    # Local-only privacy flag (migration 0043). A confidential specimen is dropped
    # from the DwC export entirely. Never pushed to TaxonWorks.
    confidential: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        # Catalog number is unique per owning collection, not globally — foreign datasets
        # may reuse numbers under their own repository (migration 0047, #75; replaced the
        # former UNIQUE(collectionCode, catalogNumber) when membership became an FK).
        UniqueConstraint("repository_id", "dwc:catalogNumber", name="uq_co_repository_catalog"),
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
        CheckConstraint("confidential IN (0, 1)", name="ck_co_confidential"),
    )

    collecting_event: Mapped[Optional["CollectingEvent"]] = relationship("CollectingEvent", back_populates="collection_objects")
    repository: Mapped["Repository"] = relationship("Repository")
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

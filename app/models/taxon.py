from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class Taxon(Base, TimestampMixin):
    """Local taxon / OTU record. DwC columns carry dwc: prefix.

    Stores rank-level fields from order down. dwc:scientificName is derived
    at export time (genus + specificEpithet + authorship).

    parent_id provides a navigable tree alongside the denormalised rank
    columns — both coexist: the columns make DwC export a flat read,
    parent_id supports taxonomy browsing and tree queries.

    dwc:taxonomicStatus: "accepted" | "synonym" | "invalid".
    When a name is synonymised, update this field; add the accepted name as a
    new row. Determinations keep pointing to whichever row was used —
    verbatimIdentification preserves the label text.
    """

    __tablename__ = "taxon"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    taxon_order: Mapped[Optional[str]] = mapped_column("dwc:order", String, nullable=True)
    family: Mapped[Optional[str]] = mapped_column("dwc:family", String, nullable=True)
    subfamily: Mapped[Optional[str]] = mapped_column("dwc:subfamily", String, nullable=True)
    tribe: Mapped[Optional[str]] = mapped_column("dwc:tribe", String, nullable=True)
    subtribe: Mapped[Optional[str]] = mapped_column("dwc:subtribe", String, nullable=True)
    genus: Mapped[Optional[str]] = mapped_column("dwc:genus", String, nullable=True)
    subgenus: Mapped[Optional[str]] = mapped_column("dwc:subgenus", String, nullable=True)
    specific_epithet: Mapped[Optional[str]] = mapped_column("dwc:specificEpithet", String, nullable=True)
    infraspecific_epithet: Mapped[Optional[str]] = mapped_column("dwc:infraspecificEpithet", String, nullable=True)
    scientific_name_authorship: Mapped[Optional[str]] = mapped_column("dwc:scientificNameAuthorship", String, nullable=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("taxon.id", ondelete="RESTRICT"), nullable=True
    )

    # TaxonWorks OTU id — set on first TW selection; used for TaxonPages deep-links.
    taxonworks_otu_id: Mapped[Optional[int]] = mapped_column(
        "taxonworksOtuID", Integer, nullable=True
    )

    # Authorship strings for higher-rank names (captured during TW parent-chain walk).
    family_authorship:    Mapped[Optional[str]] = mapped_column("familyAuthorship",    String, nullable=True)
    subfamily_authorship: Mapped[Optional[str]] = mapped_column("subfamilyAuthorship", String, nullable=True)
    tribe_authorship:     Mapped[Optional[str]] = mapped_column("tribeAuthorship",     String, nullable=True)
    subtribe_authorship:  Mapped[Optional[str]] = mapped_column("subtribeAuthorship",  String, nullable=True)
    genus_authorship:     Mapped[Optional[str]] = mapped_column("genusAuthorship",     String, nullable=True)
    subgenus_authorship:  Mapped[Optional[str]] = mapped_column("subgenusAuthorship",  String, nullable=True)

    # Set when taxonomicStatus = "synonym" or "invalid"; NULL on accepted taxa.
    # dwc:acceptedNameUsage (the name string) is derived at export time — not stored.
    accepted_name_usage_id: Mapped[Optional[int]] = mapped_column(
        "dwc:acceptedNameUsageID",
        Integer,
        ForeignKey("taxon.id", ondelete="RESTRICT"),
        nullable=True,
    )

    parent: Mapped[Optional[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.parent_id",
        remote_side="Taxon.id", back_populates="children",
    )
    children: Mapped[List[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.parent_id", back_populates="parent",
    )
    accepted_name_usage: Mapped[Optional[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.accepted_name_usage_id",
        remote_side="Taxon.id", back_populates="synonyms",
    )
    synonyms: Mapped[List[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.accepted_name_usage_id",
        back_populates="accepted_name_usage",
        passive_deletes=True,  # let the DB RESTRICT fire, don't auto-null
    )
    determinations: Mapped[List["TaxonDetermination"]] = relationship("TaxonDetermination", back_populates="taxon")
    subject_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation", foreign_keys="BiologicalAssociation.subject_taxon_id", back_populates="subject_taxon")
    object_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation", foreign_keys="BiologicalAssociation.object_taxon_id", back_populates="object_taxon")

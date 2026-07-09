from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class Taxon(Base, TimestampMixin):
    """Local taxon row — DwC parent-link model (GBIF checklist best practices).

    Each row carries only the core DwC Taxon fields; hierarchy is encoded via
    dwc:parentNameUsageID (FK to self) rather than denormalised rank columns.

    scientificName stores the bare name without authorship (e.g. "Otiorhynchus
    sulcatus"). format_scientific_name() appends scientificNameAuthorship for
    display.  DwC export concatenates them at export time.

    Synonymy is encoded solely by acceptedNameUsageID: a row is a synonym iff it
    links to an accepted taxon, otherwise it is accepted. The DwC Taxon-core
    `taxonomicStatus` term is *derived* from that link at export time and is not
    stored (it was dropped in migration 0030 to remove a redundant column that
    could — and did — drift out of sync with acceptedNameUsageID).
    """

    __tablename__ = "taxon"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Atomic source of truth: this rank's own epithet/uninomial (TaxonWorks'
    # `name`). dwc:scientificName below is the *composed* full name (without
    # authorship), maintained from name_element + the parent chain by
    # compose_scientific_name() in app/services/taxa.py. Nullable at the DB
    # level only because it is backfilled by import; the service layer treats
    # it as required (a row's name cannot be rendered without it).
    name_element: Mapped[Optional[str]] = mapped_column(
        "name_element", String, nullable=True
    )

    scientific_name: Mapped[str] = mapped_column(
        "dwc:scientificName", String, nullable=False
    )
    taxon_rank: Mapped[str] = mapped_column(
        "dwc:taxonRank", String, nullable=False
    )
    scientific_name_authorship: Mapped[Optional[str]] = mapped_column(
        "dwc:scientificNameAuthorship", String, nullable=True
    )

    # Parent link — encodes the hierarchy (replaces denormalised rank columns).
    parent_name_usage_id: Mapped[Optional[int]] = mapped_column(
        "dwc:parentNameUsageID",
        Integer,
        ForeignKey("taxon.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Synonym link — NULL for accepted names.
    accepted_name_usage_id: Mapped[Optional[int]] = mapped_column(
        "dwc:acceptedNameUsageID",
        Integer,
        ForeignKey("taxon.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # TaxonWorks OTU id — set on first TW selection; used for TaxonPages links.
    taxonworks_otu_id: Mapped[Optional[int]] = mapped_column(
        "taxonworksOtuID", Integer, nullable=True
    )

    # IPNI id ("304293-2") of the name this row was imported from (WCVP's scientificnameid).
    # Named for its source, like taxonworksOtuID above — a source-specific external id is a
    # plain local column, not a dwc: term (dwc:scientificNameID is generic: it could hold a
    # WFO id just as well). Identity, not provenance: it says which name this is, not whose
    # opinion the row reflects, so a manual re-parent leaves it true.
    # NULL for TaxonWorks imports, manual creations, and WCVP names with no IPNI id.
    ipni_id: Mapped[Optional[str]] = mapped_column("ipniID", String, nullable=True)

    # Nomenclatural code governing this name, uppercased DwC value.
    # Set from source API: TW returns "iczn"/"icn"/… → stored as "ICZN"/"ICN"/…
    # POWO returns "Botanical" → stored as "ICN".
    nomenclatural_code: Mapped[Optional[str]] = mapped_column(
        "dwc:nomenclaturalCode", String, nullable=True
    )

    # Display-only manual ordering among siblings (the collection's taxonomic
    # sequence). Used ONLY by the checklist UI for family-and-above ranks; NULL ⇒
    # fall back to alphabetical. Never exported (not a DwC term, #40).
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    parent: Mapped[Optional[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.parent_name_usage_id",
        remote_side="Taxon.id", back_populates="children",
    )
    children: Mapped[List[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.parent_name_usage_id",
        back_populates="parent",
    )
    accepted_name_usage: Mapped[Optional[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.accepted_name_usage_id",
        remote_side="Taxon.id", back_populates="synonyms",
    )
    synonyms: Mapped[List[Taxon]] = relationship(
        "Taxon", foreign_keys="Taxon.accepted_name_usage_id",
        back_populates="accepted_name_usage",
        passive_deletes=True,
    )
    determinations: Mapped[List["TaxonDetermination"]] = relationship(
        "TaxonDetermination", back_populates="taxon"
    )
    subject_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation",
        foreign_keys="BiologicalAssociation.subject_taxon_id",
        back_populates="subject_taxon",
    )
    object_associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation",
        foreign_keys="BiologicalAssociation.object_taxon_id",
        back_populates="object_taxon",
    )

from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint, Index, text  # CheckConstraint used by BiologicalAssociation
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym
from .base import Base, TimestampMixin


class BiologicalRelationship(Base, TimestampMixin):
    """The kind of association (e.g. 'collected_on', 'feeds_on').

    Seed rows are inserted by the initial Alembic migration.
    taxonworksID is the optional TaxonWorks link id (see tw_links.py); it is set only by an
    explicit link in the Controlled Vocabularies tab.
    """

    __tablename__ = "biological_relationship"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    taxonworks_id: Mapped[Optional[int]] = mapped_column(
        "taxonworksID", Integer, nullable=True
    )
    # The optional TaxonWorks link (migration 0073), same rules as annotation_term:
    # tw_name = TW's name as last fetched; tw_host = confirmed on that host (NULL = not
    # verified). The tw_name <-> taxonworksID coupling is two triggers (see 0073).
    tw_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tw_host: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # One name for the link id across both linkable vocabularies (tw_links.py).
    tw_id = synonym("taxonworks_id")

    associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation", back_populates="biological_relationship"
    )

    __table_args__ = (
        CheckConstraint(
            "tw_name IS NULL OR (length(trim(tw_name)) > 0 AND instr(tw_name, char(9)) = 0 "
            "AND instr(tw_name, char(10)) = 0 AND instr(tw_name, char(13)) = 0)",
            name="ck_br_tw_name_clean"),
        CheckConstraint('tw_host IS NULL OR "taxonworksID" IS NOT NULL',
                        name="ck_br_tw_host_needs_id"),
        Index("uq_br_taxonworks_id", "taxonworksID", unique=True,
              sqlite_where=text('"taxonworksID" IS NOT NULL')),
    )


class BiologicalAssociation(Base, TimestampMixin):
    """Joins a subject to an object via a BiologicalRelationship.

    Subject and object are each either a CollectionObject or a Taxon.
    The exclusive-arc pattern (two nullable FKs per role, exactly one
    non-null per role) gives DB-enforced referential integrity without
    a polymorphic type column.

    Mirrors TaxonWorks BiologicalAssociation (subject/object polymorphic).
    Cannot be imported via DwC; local-master with no automated push.
    Verified: occurrence.rb:948 (@897f385) — associatedTaxa is [Not mapped].
    """

    __tablename__ = "biological_association"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    biological_relationship_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("biological_relationship.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Subject exclusive arc
    subject_collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("collection_object.id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject_taxon_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("taxon.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Object exclusive arc — collection_object XOR taxon XOR field_occurrence.
    # A field_occurrence object is a host/associated organism recorded as its own
    # HumanObservation (migration 0061); object_taxon stays for the lightweight
    # "collected on <taxon>, no observation record" case.
    object_collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("collection_object.id", ondelete="RESTRICT"),
        nullable=True,
    )
    object_taxon_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("taxon.id", ondelete="RESTRICT"),
        nullable=True,
    )
    object_field_occurrence_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("field_occurrence.id", ondelete="RESTRICT"),
        nullable=True,
    )

    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(subject_collection_object_id IS NOT NULL AND subject_taxon_id IS NULL) OR "
            "(subject_collection_object_id IS NULL AND subject_taxon_id IS NOT NULL)",
            name="ck_ba_subject_exclusive_arc",
        ),
        CheckConstraint(
            "((object_collection_object_id IS NOT NULL) + (object_taxon_id IS NOT NULL) + "
            "(object_field_occurrence_id IS NOT NULL)) = 1",
            name="ck_ba_object_exclusive_arc",
        ),
    )

    biological_relationship: Mapped["BiologicalRelationship"] = relationship(
        "BiologicalRelationship", back_populates="associations"
    )
    subject_collection_object: Mapped[Optional["CollectionObject"]] = relationship(
        "CollectionObject",
        foreign_keys=[subject_collection_object_id],
        back_populates="subject_associations",
    )
    subject_taxon: Mapped[Optional["Taxon"]] = relationship(
        "Taxon",
        foreign_keys=[subject_taxon_id],
        back_populates="subject_associations",
    )
    object_collection_object: Mapped[Optional["CollectionObject"]] = relationship(
        "CollectionObject",
        foreign_keys=[object_collection_object_id],
        back_populates="object_associations",
    )
    object_taxon: Mapped[Optional["Taxon"]] = relationship(
        "Taxon",
        foreign_keys=[object_taxon_id],
        back_populates="object_associations",
    )
    object_field_occurrence: Mapped[Optional["FieldOccurrence"]] = relationship(
        "FieldOccurrence",
        foreign_keys=[object_field_occurrence_id],
        back_populates="object_associations",
    )

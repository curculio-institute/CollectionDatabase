from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from app.vocab import IDENTIFICATION_QUALIFIERS

# NULL (definite ID) or one of the closed open-nomenclature set. Migration 0058.
_QUAL_CHECK_SQL = (
    '"dwc:identificationQualifier" IS NULL OR "dwc:identificationQualifier" IN ('
    + ", ".join(f"'{q}'" for q in IDENTIFICATION_QUALIFIERS) + ")"
)

if TYPE_CHECKING:
    from .person import Person


class TaxonDetermination(Base, TimestampMixin):
    """Links a CollectionObject **or a FieldOccurrence** to a Taxon. DwC columns
    carry dwc: prefix.

    The subject is an exclusive arc (migration 0060): exactly one of
    ``collection_object_id`` (a held specimen) / ``field_occurrence_id`` (a
    HumanObservation) is set. A field occurrence thus reuses the whole
    determination machinery, including the open-nomenclature qualifier.

    is_current=1 marks the accepted determination; history is preserved
    by keeping older rows with is_current=0.
    """

    __tablename__ = "taxon_determination"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_object_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True)
    field_occurrence_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("field_occurrence.id", ondelete="CASCADE"), nullable=True)
    taxon_id: Mapped[int] = mapped_column(Integer, ForeignKey("taxon.id", ondelete="RESTRICT"), nullable=False)

    verbatim_identification: Mapped[Optional[str]] = mapped_column("dwc:verbatimIdentification", String, nullable=True)
    sex: Mapped[Optional[str]] = mapped_column("dwc:sex", String, nullable=True)
    type_status: Mapped[Optional[str]] = mapped_column("dwc:typeStatus", String, nullable=True)
    identified_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("person.id", ondelete="RESTRICT"), nullable=True)
    identified_by_person: Mapped[Optional["Person"]] = relationship("Person", lazy="select", foreign_keys="[TaxonDetermination.identified_by_id]")
    date_identified: Mapped[Optional[str]] = mapped_column("dwc:dateIdentified", String, nullable=True)
    identification_qualifier: Mapped[Optional[str]] = mapped_column("dwc:identificationQualifier", String, nullable=True)
    identification_remarks: Mapped[Optional[str]] = mapped_column("dwc:identificationRemarks", String, nullable=True)
    is_current: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("is_current IN (0, 1)", name="ck_td_is_current_bool"),
        CheckConstraint(_QUAL_CHECK_SQL, name="ck_td_identification_qualifier"),
        # Subject exclusive arc: exactly one of collection_object / field_occurrence.
        CheckConstraint(
            "(collection_object_id IS NOT NULL AND field_occurrence_id IS NULL) OR "
            "(collection_object_id IS NULL AND field_occurrence_id IS NOT NULL)",
            name="ck_td_subject_exclusive_arc",
        ),
        # At most one current determination per subject (#74). Partial unique
        # indexes so the is_current=0 history rows stay unconstrained; one per arc
        # side (SQLite's distinct-NULL semantics scope each to its own subject).
        Index(
            "uq_td_one_current_per_co",
            "collection_object_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
        Index(
            "uq_td_one_current_per_fo",
            "field_occurrence_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    collection_object: Mapped[Optional["CollectionObject"]] = relationship("CollectionObject", back_populates="determinations")
    field_occurrence: Mapped[Optional["FieldOccurrence"]] = relationship("FieldOccurrence", back_populates="determinations")
    taxon: Mapped["Taxon"] = relationship("Taxon", back_populates="determinations")

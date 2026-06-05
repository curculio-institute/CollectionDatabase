from __future__ import annotations
from typing import Optional
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class TaxonDetermination(Base, TimestampMixin):
    """Links a CollectionObject to a Taxon. DwC columns carry dwc: prefix.

    is_current=1 marks the accepted determination; history is preserved
    by keeping older rows with is_current=0.
    """

    __tablename__ = "taxon_determination"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_object_id: Mapped[int] = mapped_column(Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=False)
    taxon_id: Mapped[int] = mapped_column(Integer, ForeignKey("taxon.id", ondelete="RESTRICT"), nullable=False)

    verbatim_identification: Mapped[Optional[str]] = mapped_column("dwc:verbatimIdentification", String, nullable=True)
    identified_by: Mapped[Optional[str]] = mapped_column("dwc:identifiedBy", String, nullable=True)
    date_identified: Mapped[Optional[str]] = mapped_column("dwc:dateIdentified", String, nullable=True)
    identification_qualifier: Mapped[Optional[str]] = mapped_column("dwc:identificationQualifier", String, nullable=True)
    identification_remarks: Mapped[Optional[str]] = mapped_column("dwc:identificationRemarks", String, nullable=True)
    is_current: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("is_current IN (0, 1)", name="ck_td_is_current_bool"),
    )

    collection_object: Mapped["CollectionObject"] = relationship("CollectionObject", back_populates="determinations")
    taxon: Mapped["Taxon"] = relationship("Taxon", back_populates="determinations")

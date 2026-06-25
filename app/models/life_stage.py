from __future__ import annotations
from typing import Optional
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class LifeStageRecord(Base, TimestampMixin):
    """One additional life-stage occurrence facet of a *reared* specimen.

    A reared specimen is preserved as (say) the adult — that stage lives on
    `collection_object` (`dwc:lifeStage`, `dwc:basisOfRecord`) and its
    `collecting_event` carries the original wild date + locality. Each row here records an
    *earlier* stage of the **same individual** — e.g. the larva collected in the wild — as
    its own `(dwc:lifeStage, dwc:basisOfRecord, dwc:eventDate)` tuple, **without
    duplicating the specimen or the event** (the design decision: store the history linked
    to the specimen, not as extra specimen/event rows).

    At DwC export the preserved specimen becomes a PreservedSpecimen record and each
    life-stage row a separate record (e.g. the wild larva as a HumanObservation, sharing
    the specimen's locality, with its own eventDate), the two linked via a derived
    resource relationship — see app/services/life_stage.py.
    """

    __tablename__ = "life_stage_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_object_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=False)

    life_stage: Mapped[str] = mapped_column("dwc:lifeStage", String, nullable=False)
    basis_of_record: Mapped[str] = mapped_column(
        "dwc:basisOfRecord", String, nullable=False, default="HumanObservation")
    event_date: Mapped[Optional[str]] = mapped_column("dwc:eventDate", String, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remarks: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "\"dwc:basisOfRecord\" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')",
            name="ck_life_stage_basis_of_record",
        ),
    )

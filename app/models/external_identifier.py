from __future__ import annotations
from typing import Optional
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class ExternalIdentifier(Base, TimestampMixin):
    """An external resource identifier/link attached to one record.

    For a **collection_object** it points at an external page about the specimen (e.g. an
    iNaturalist observation URL). For a **field_occurrence** (a HumanObservation — often
    *born* from an iNaturalist observation) it is the observation's own resolvable URI. For
    a **biological_association** it denotes the *other party* — the non-collection-object
    side — as an external resource (e.g. the host plant's iNaturalist observation). It is an
    **optional addition**: the association's object arc is unchanged.

    Exclusive-arc: exactly one of (collection_object_id, biological_association_id,
    field_occurrence_id) is set — the project's FK-safe alternative to a polymorphic
    association.
    """

    __tablename__ = "external_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True)
    biological_association_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("biological_association.id", ondelete="CASCADE"), nullable=True)
    field_occurrence_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("field_occurrence.id", ondelete="CASCADE"), nullable=True)

    # The URI is the identifier; source is an optional, currently-unpopulated label kept
    # for future flexibility (can be derived from the URI at export/query time).
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    value: Mapped[str] = mapped_column(String, nullable=False)    # the URI / identifier
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "((collection_object_id IS NOT NULL) + (biological_association_id IS NOT NULL) + "
            "(field_occurrence_id IS NOT NULL)) = 1",
            name="ck_external_identifier_exclusive_arc",
        ),
    )

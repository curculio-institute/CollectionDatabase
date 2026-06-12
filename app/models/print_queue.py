from __future__ import annotations
from typing import Optional
from sqlalchemy import CheckConstraint, Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class PrintQueue(Base, TimestampMixin):
    __tablename__ = "print_queue"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    label_type: Mapped[str] = mapped_column(String, nullable=False)

    # Grouping for the printed sheet: rows enqueued in one operation share a
    # print_group_id and a `source` header (e.g. "Mounting Session"). Both
    # nullable — legacy rows render as one fallback group. See migration 0028.
    print_group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source:         Mapped[Optional[str]] = mapped_column(String, nullable=True)

    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True
    )
    label_code_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("label_code.id", ondelete="CASCADE"), nullable=True
    )

    collection_object = relationship("CollectionObject")
    label_code        = relationship("LabelCode")

    __table_args__ = (
        CheckConstraint(
            "label_type IN ('data', 'determination', 'identifier')",
            name="ck_print_queue_label_type",
        ),
        CheckConstraint(
            "(label_type IN ('data', 'determination')"
            "  AND collection_object_id IS NOT NULL"
            "  AND label_code_id IS NULL)"
            " OR "
            "(label_type = 'identifier'"
            "  AND label_code_id IS NOT NULL"
            "  AND collection_object_id IS NULL)",
            name="ck_print_queue_exclusive_arc",
        ),
    )

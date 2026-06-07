from __future__ import annotations
from typing import Optional
from sqlalchemy import CheckConstraint, Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class LabelCode(Base, TimestampMixin):
    __tablename__ = "label_code"

    id:     Mapped[int]          = mapped_column(Integer, primary_key=True)
    code:   Mapped[str]          = mapped_column(String,  nullable=False, unique=True)
    status: Mapped[str]          = mapped_column(String,  nullable=False, default="reserved")
    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="SET NULL"), nullable=True
    )
    batch_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("label_batch.id"), nullable=True
    )

    collection_object = relationship("CollectionObject", back_populates="label_codes")
    batch = relationship("LabelBatch", back_populates="codes")

    __table_args__ = (
        CheckConstraint("status IN ('reserved', 'assigned')", name="ck_label_code_status"),
    )

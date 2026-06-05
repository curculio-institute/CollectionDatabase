from __future__ import annotations
from typing import Optional
from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class PrintQueue(Base, TimestampMixin):
    __tablename__ = "print_queue"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    label_type: Mapped[str] = mapped_column(String, nullable=False)

    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True
    )
    label_code_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("label_code.id", ondelete="CASCADE"), nullable=True
    )

    collection_object = relationship("CollectionObject")
    label_code        = relationship("LabelCode")

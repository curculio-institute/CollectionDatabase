from __future__ import annotations
from typing import List
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class LabelBatch(Base, TimestampMixin):
    __tablename__ = "label_batch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    codes: Mapped[List["LabelCode"]] = relationship("LabelCode", back_populates="batch")

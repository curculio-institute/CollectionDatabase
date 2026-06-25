from __future__ import annotations
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Preparation(Base, TimestampMixin):
    """Controlled-vocabulary entry for a specimen's preparation (e.g. 'pinned',
    'in ethanol'). A single-name vocab referenced by collection_object.preparation_id;
    managed via app/services/vocab.py (edit / merge / delete like persons)."""

    __tablename__ = "preparation"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_preparation_name"),
    )

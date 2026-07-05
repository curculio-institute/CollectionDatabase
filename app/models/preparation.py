from __future__ import annotations
from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Preparation(Base, TimestampMixin):
    """Controlled-vocabulary entry for a specimen's preparation (e.g. 'pinned',
    'in ethanol'). A single-name vocab referenced by collection_object.preparation_id;
    managed via app/services/vocab.py (edit / merge / delete like persons)."""

    __tablename__ = "preparation"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # One preparation can be flagged as the create-time Tier-1 autofill default
    # (migration 0052; mirrors repository.is_default). At most one at a time.
    is_default: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("name", name="uq_preparation_name"),
        CheckConstraint("is_default IN (0, 1)", name="ck_preparation_is_default"),
        Index("uq_preparation_one_default", "is_default",
              unique=True, sqlite_where=text("is_default = 1")),
    )

from __future__ import annotations
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Habitat(Base, TimestampMixin):
    """Controlled-vocabulary entry for a collecting event's habitat (e.g.
    'broadleaf forest edge', 'alpine meadow'). Single-name vocab referenced by
    collecting_event.habitat_id; managed via app/services/vocab.py."""

    __tablename__ = "habitat"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_habitat_name"),
    )

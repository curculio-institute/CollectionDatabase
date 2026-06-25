from __future__ import annotations
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class SamplingProtocol(Base, TimestampMixin):
    """Controlled-vocabulary entry for a collecting event's sampling protocol /
    collecting method (e.g. 'beating', 'pitfall trap', 'light trap'). Single-name
    vocab referenced by collecting_event.sampling_protocol_id; managed via
    app/services/vocab.py. Seeded with a curated starting set (migration 0040)."""

    __tablename__ = "sampling_protocol"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_sampling_protocol_name"),
    )

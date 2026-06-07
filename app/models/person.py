from __future__ import annotations
from typing import Optional
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Person(Base, TimestampMixin):
    """Controlled-vocabulary entry for a collector / identifier.

    abbreviated_name is the string written into DwC fields (identifiedBy, recordedBy).
    If absent, full_name is used instead.
    """

    __tablename__ = "person"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True)
    full_name:        Mapped[str]           = mapped_column(String, nullable=False)
    abbreviated_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    orcid:            Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("full_name", name="uq_person_full_name"),
    )

    @property
    def dwc_name(self) -> str:
        """The string stored in DwC identifiedBy / recordedBy fields: always the full name."""
        return self.full_name

    @property
    def label_name(self) -> str:
        """Short form for printed specimen labels: abbreviated name if set, else full name."""
        return self.abbreviated_name or self.full_name

    @property
    def display_label(self) -> str:
        """Dropdown display: always the full name."""
        return self.full_name

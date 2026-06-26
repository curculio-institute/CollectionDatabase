from __future__ import annotations
from typing import Optional
from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Repository(Base, TimestampMixin):
    """An institution / collection the specimens belong to (migration 0045, #56).

    Keyed by ``dwc:collectionCode`` (the prefix embedded in every catalog number,
    e.g. ``JJPC`` in ``JJPC-00304``). The identifier label resolves that prefix to
    ``collection_full_name``. DwC-mapping columns carry the ``dwc:`` prefix so the
    export is a passthrough; full names + TW ids are local. TaxonWorks stores the
    institution (Repository) and collection (Namespace) under separate ids.
    """

    __tablename__ = "repository"

    id:                        Mapped[int]           = mapped_column(Integer, primary_key=True)
    institution_code:          Mapped[Optional[str]] = mapped_column("dwc:institutionCode", String, nullable=True)
    institution_full_name:     Mapped[Optional[str]] = mapped_column(String, nullable=True)
    collection_code:           Mapped[str]           = mapped_column("dwc:collectionCode", String, nullable=False)
    collection_full_name:      Mapped[str]           = mapped_column(String, nullable=False)
    taxonworks_institution_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    taxonworks_collection_id:  Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("dwc:collectionCode", name="uq_repository_collection_code"),
    )

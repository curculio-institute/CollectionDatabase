from __future__ import annotations
from typing import Optional
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Index, text
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
    # The user's own/home collection — used to stamp new specimens and generate
    # catalog numbers. At most one repository is the default (migration 0050, #83);
    # the default is held *here*, in the vocab, not as a code string in config.json.
    is_default:                Mapped[int]           = mapped_column(Integer, nullable=False, server_default="0")
    # Optional contact/owner person for the collection (migration 0051, #79). No roles —
    # a single person per repository. merge_persons/delete re-point this FK dynamically.
    person_id:                 Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="RESTRICT"), nullable=True)

    __table_args__ = (
        UniqueConstraint("dwc:collectionCode", name="uq_repository_collection_code"),
        CheckConstraint("is_default IN (0, 1)", name="ck_repository_is_default"),
        # At most one default collection at a time (partial unique index, #83).
        Index("uq_repository_one_default", "is_default",
              unique=True, sqlite_where=text("is_default = 1")),
    )

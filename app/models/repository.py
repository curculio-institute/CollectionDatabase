from __future__ import annotations
from typing import Optional
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Repository(Base, TimestampMixin):
    """An institution / collection the specimens belong to (migration 0045, #56).

    Identified by ``collection_full_name`` (UNIQUE, migration 0072) — a private
    collection ("Frank Lange collection") often has no code at all. ``dwc:collectionCode``
    is optional and unique where present; it is the prefix embedded in the own
    collection's catalog numbers (``JJPC`` in ``JJPC-00304``), which the identifier label
    resolves back to ``collection_full_name``. The default collection must have one. DwC-mapping columns carry the ``dwc:`` prefix so the
    export is a passthrough; full names + TW ids are local. TaxonWorks stores the
    institution (Repository) and collection (Namespace) under separate ids.
    """

    __tablename__ = "repository"

    id:                        Mapped[int]           = mapped_column(Integer, primary_key=True)
    institution_code:          Mapped[Optional[str]] = mapped_column("dwc:institutionCode", String, nullable=True)
    institution_full_name:     Mapped[Optional[str]] = mapped_column(String, nullable=True)
    collection_code:           Mapped[Optional[str]] = mapped_column("dwc:collectionCode", String, nullable=True)
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
        UniqueConstraint("collection_full_name", name="uq_repository_collection_full_name"),
        # The code is unique only where there is one (partial unique index).
        Index("uq_repository_collection_code", "dwc:collectionCode",
              unique=True, sqlite_where=text('"dwc:collectionCode" IS NOT NULL')),
        CheckConstraint("is_default IN (0, 1)", name="ck_repository_is_default"),
        CheckConstraint(
            '"dwc:collectionCode" IS NULL OR length(trim("dwc:collectionCode")) > 0',
            name="ck_repository_collection_code_not_blank"),
        CheckConstraint("length(trim(collection_full_name)) > 0",
                        name="ck_repository_name_not_blank"),
        # The default collection's code is the catalog-number prefix.
        CheckConstraint('is_default = 0 OR "dwc:collectionCode" IS NOT NULL',
                        name="ck_repository_default_has_code"),
        # At most one default collection at a time (partial unique index, #83).
        Index("uq_repository_one_default", "is_default",
              unique=True, sqlite_where=text("is_default = 1")),
    )

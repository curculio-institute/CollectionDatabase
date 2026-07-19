from __future__ import annotations
from sqlalchemy import CheckConstraint, Integer, String, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class SavedSearch(Base, TimestampMixin):
    """An Explore favorite — a named snapshot of the search state (migration 0065, #137).

    ``payload`` is the stacked-group structure ``[{op, facets:[{kind,key,…}]}]`` as JSON
    TEXT. It references DB entities by key (taxon_id, repository_id, geo-vocab id, person),
    so the favorite lives in the DB, not config.json (the DB-entity-reference rule). Keys
    are re-resolved to labels on load; a key that no longer resolves is shown stale, never
    silently applied.

    ``is_default`` marks the search auto-applied when Explore opens; a partial-unique index
    keeps at most one, mirroring ``repository.is_default`` (#83).
    """

    __tablename__ = "saved_search"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    name:       Mapped[str] = mapped_column(String, nullable=False)
    payload:    Mapped[str] = mapped_column(String, nullable=False)   # groups JSON
    is_default: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("name", name="uq_saved_search_name"),
        CheckConstraint("is_default IN (0, 1)", name="ck_saved_search_is_default"),
        # At most one default search at a time (partial unique index, #137).
        Index("uq_saved_search_one_default", "is_default",
              unique=True, sqlite_where=text("is_default = 1")),
    )

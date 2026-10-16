from __future__ import annotations

from typing import Optional

from sqlalchemy import (CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

_CLEAN = ("instr({c}, char(9)) = 0 AND instr({c}, char(10)) = 0 "
          "AND instr({c}, char(13)) = 0")
_HEX = "[0-9A-Fa-f]"


class AnnotationTerm(Base, TimestampMixin):
    """A tag or data-attribute term of the LOCAL controlled vocabulary (migration 0073).

    Optional link to ONE TaxonWorks keyword (tag) / predicate (data attribute):
    ``tw_id`` + ``tw_name`` (TW's name as last fetched; the export header) + ``tw_host``
    (confirmed on that host; NULL = not verified). ``tw_id`` NULL = local only, never
    exported. ``css_color`` is mirrored from TW while linked (``tw_links``)."""

    __tablename__ = "annotation_term"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    allow_specimen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_event: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_association: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tw_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tw_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tw_host: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    css_color: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_annotation_term_kind_name"),
        UniqueConstraint("kind", "tw_id", name="uq_annotation_term_kind_tw_id"),
        UniqueConstraint("id", "kind", name="uq_annotation_term_id_kind"),
        CheckConstraint("kind IN ('tag', 'data_attribute')", name="ck_annotation_term_kind"),
        CheckConstraint("length(trim(name)) > 0", name="ck_annotation_term_name_not_blank"),
        CheckConstraint("allow_specimen IN (0, 1)", name="ck_annotation_term_allow_specimen"),
        CheckConstraint("allow_event IN (0, 1)", name="ck_annotation_term_allow_event"),
        CheckConstraint("allow_association IN (0, 1)",
                        name="ck_annotation_term_allow_association"),
        CheckConstraint("(tw_id IS NULL) = (tw_name IS NULL)",
                        name="ck_annotation_term_tw_name_iff_id"),
        CheckConstraint("tw_name IS NULL OR (length(trim(tw_name)) > 0 AND "
                        + _CLEAN.format(c="tw_name") + ")",
                        name="ck_annotation_term_tw_name_clean"),
        CheckConstraint("tw_host IS NULL OR tw_id IS NOT NULL",
                        name="ck_annotation_term_tw_host_needs_id"),
        CheckConstraint(f"css_color IS NULL OR css_color GLOB '#{_HEX * 3}' "
                        f"OR css_color GLOB '#{_HEX * 6}'",
                        name="ck_annotation_term_css_color"),
    )


class Annotation(Base, TimestampMixin):
    """One tag / data attribute on exactly one specimen, event or biological association.

    ``(term_id, term_kind)`` is a composite FK onto ``annotation_term(id, kind)`` — the
    kind is repeated here so the value CHECK can see it (a tag has no value, a data
    attribute must have one)."""

    __tablename__ = "annotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True)
    collecting_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collecting_event.id", ondelete="CASCADE"), nullable=True)
    biological_association_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("biological_association.id", ondelete="CASCADE"), nullable=True)
    term_id: Mapped[int] = mapped_column(Integer, nullable=False)
    term_kind: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    term: Mapped[AnnotationTerm] = relationship(AnnotationTerm, lazy="joined")

    __table_args__ = (
        ForeignKeyConstraint(["term_id", "term_kind"],
                             ["annotation_term.id", "annotation_term.kind"],
                             ondelete="RESTRICT", name="fk_annotation_term"),
        CheckConstraint("((collection_object_id IS NOT NULL) + (collecting_event_id IS NOT "
                        "NULL) + (biological_association_id IS NOT NULL)) = 1",
                        name="ck_annotation_exclusive_arc"),
        CheckConstraint("term_kind IN ('tag', 'data_attribute')", name="ck_annotation_kind"),
        CheckConstraint("(term_kind = 'tag' AND value IS NULL) OR (term_kind = "
                        "'data_attribute' AND value IS NOT NULL AND length(trim(value)) > 0 "
                        "AND " + _CLEAN.format(c="value") + ")",
                        name="ck_annotation_value_by_kind"),
        Index("uq_annotation_specimen_term", "collection_object_id", "term_id", unique=True,
              sqlite_where=text("collection_object_id IS NOT NULL")),
        Index("uq_annotation_event_term", "collecting_event_id", "term_id", unique=True,
              sqlite_where=text("collecting_event_id IS NOT NULL")),
        Index("uq_annotation_association_term", "biological_association_id", "term_id",
              unique=True, sqlite_where=text("biological_association_id IS NOT NULL")),
    )

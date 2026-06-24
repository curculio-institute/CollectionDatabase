from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Integer, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class Media(Base, TimestampMixin):
    """A stored media asset — the bytes live content-addressed on disk (see
    app/services/media.py); this row holds the metadata + checksum.

    One row per distinct file content (sha256 is UNIQUE → de-duplicated). Metadata
    follows Audubon Core where it maps cleanly. ``category`` is the user-facing filter
    key (Image / Sound / Video / Document / Sequence / Other) — "Sequence" covers genetic
    data such as FASTA, which Audubon Core has no native category for.

    Inspired by TaxonWorks' Image asset; attachment to a record is a separate row
    (MediaAttachment), mirroring TW's Image↔Depiction split, but using the project's
    exclusive-arc pattern instead of a polymorphic association for FK-safe integrity.
    """

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # mime type
    original_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    byte_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Audubon-Core-style descriptive metadata
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    creator: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    capture_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    license: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rights_holder: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "category IN ('Image', 'Sound', 'Video', 'Document', 'Sequence', 'Other')",
            name="ck_media_category",
        ),
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_media_byte_size_non_negative"),
    )

    attachments: Mapped[List["MediaAttachment"]] = relationship(
        "MediaAttachment", back_populates="media", cascade="all, delete-orphan"
    )


class MediaAttachment(Base, TimestampMixin):
    """Links a Media asset to exactly one record — a collection_object, a
    collecting_event, or a biological_association (exclusive-arc, exactly one non-null).

    Per-attachment fields (caption, is_primary, sort_order) live here, not on Media, so
    the same asset can be attached to several records with different captions — mirroring
    TaxonWorks' Depiction.
    """

    __tablename__ = "media_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id", ondelete="CASCADE"), nullable=False)

    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True)
    collecting_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collecting_event.id", ondelete="CASCADE"), nullable=True)
    biological_association_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("biological_association.id", ondelete="CASCADE"), nullable=True)

    caption: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_primary: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "(collection_object_id IS NOT NULL AND collecting_event_id IS NULL AND biological_association_id IS NULL) OR "
            "(collection_object_id IS NULL AND collecting_event_id IS NOT NULL AND biological_association_id IS NULL) OR "
            "(collection_object_id IS NULL AND collecting_event_id IS NULL AND biological_association_id IS NOT NULL)",
            name="ck_media_attachment_exclusive_arc",
        ),
        CheckConstraint("is_primary IN (0, 1)", name="ck_media_attachment_is_primary"),
    )

    media: Mapped["Media"] = relationship("Media", back_populates="attachments")

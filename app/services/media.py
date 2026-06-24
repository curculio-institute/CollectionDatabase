"""Managed, content-addressed media store.

Every attached file is copied into ``data/media/`` (see ``config.media_dir``) and named
by its SHA-256 digest, sharded by the first two hex chars:
``data/media/<ab>/<abcdef…>.<ext>``. Content-addressing gives us free de-duplication
(identical bytes → one stored file) and a built-in integrity check (re-hash the file and
compare). The DB (``media`` / ``media_attachment``) holds the metadata + the link to a
specimen / event / association; the bytes live on disk. This keeps the store *safe and
persistent*: originals can move or be deleted without affecting us, and corruption is
detectable.

The DB row is the source of truth for *which* files exist; this module only manages the
bytes and derives metadata (category, mime, size, image dimensions).
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import media_dir
from app.models import Media, MediaAttachment

# Target kinds an attachment can point at → the FK column on media_attachment.
TARGET_FK = {
    "collection_object": "collection_object_id",
    "collecting_event": "collecting_event_id",
    "biological_association": "biological_association_id",
}

# ── Category vocabulary (the per-file filter key, stored on media.category) ──────────
# Audubon-Core-style top-level kinds, plus "Sequence" for genetic data (FASTA etc.),
# which AC has no native category for. Mirrored by the ck_media_category CHECK.
CATEGORIES = ("Image", "Sound", "Video", "Document", "Sequence", "Other")

# Genetic / sequence file extensions → "Sequence" (no reliable mime type for these).
_SEQUENCE_EXTS = {
    ".fasta", ".fa", ".fna", ".faa", ".ffn", ".frn",
    ".fastq", ".fq", ".gb", ".gbk", ".genbank", ".sam", ".vcf", ".nexus", ".nex",
}
_DOCUMENT_EXTS = {".pdf", ".txt", ".csv", ".tsv", ".doc", ".docx", ".odt", ".rtf", ".md"}


def detect_category(mime: Optional[str], ext: str) -> str:
    """Classify a file into one of CATEGORIES from its mime type + extension.

    Extension wins for sequence files (no mime), otherwise the mime top-level type
    drives it. Unknown → "Other"."""
    ext = ext.lower()
    if ext in _SEQUENCE_EXTS:
        return "Sequence"
    if mime:
        top = mime.split("/", 1)[0]
        if top == "image":
            return "Image"
        if top == "audio":
            return "Sound"
        if top == "video":
            return "Video"
        if mime == "application/pdf" or top == "text":
            return "Document"
    if ext in _DOCUMENT_EXTS:
        return "Document"
    return "Other"


def _image_dimensions(path: Path) -> tuple[Optional[int], Optional[int]]:
    """Best-effort (width, height) for an image; (None, None) if Pillow is unavailable
    or the file is not a readable image. Pillow ships transitively via qrcode[pil]."""
    try:
        from PIL import Image  # local import: optional, best-effort
        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None, None


def _store_path(sha256: str, ext: str) -> Path:
    shard = media_dir() / sha256[:2]
    return shard / f"{sha256}{ext.lower()}"


def store_bytes(data: bytes, original_filename: str) -> dict:
    """Copy ``data`` into the content-addressed store and return its metadata dict.

    Idempotent: identical bytes resolve to the same path and are written only once.
    Returns the fields needed to create a ``media`` row (the caller fills user metadata
    like title/creator/license)."""
    sha256 = hashlib.sha256(data).hexdigest()
    ext = Path(original_filename).suffix
    dest = _store_path(sha256, ext)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp sibling then atomically rename, so a crash mid-write never
        # leaves a partial file masquerading as a valid content-addressed object.
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
    mime, _ = mimetypes.guess_type(original_filename)
    width, height = _image_dimensions(dest)
    return {
        "sha256": sha256,
        "relative_path": str(dest.relative_to(media_dir())),
        "byte_size": len(data),
        "format": mime,
        "category": detect_category(mime, ext),
        "original_filename": original_filename,
        "width": width,
        "height": height,
    }


def store_file(src: str | Path | BinaryIO, original_filename: Optional[str] = None) -> dict:
    """Store a file given a path or an open binary stream (e.g. NiceGUI's upload)."""
    if isinstance(src, (str, Path)):
        p = Path(src)
        return store_bytes(p.read_bytes(), original_filename or p.name)
    data = src.read()
    if original_filename is None:
        raise ValueError("original_filename is required when storing from a stream")
    return store_bytes(data, original_filename)


def abs_path(relative_path: str) -> Path:
    """Absolute on-disk path for a media row's relative_path."""
    return media_dir() / relative_path


def verify_integrity(relative_path: str, expected_sha256: str) -> bool:
    """True iff the stored file exists and its bytes still hash to expected_sha256."""
    p = abs_path(relative_path)
    if not p.is_file():
        return False
    return hashlib.sha256(p.read_bytes()).hexdigest() == expected_sha256


def delete_stored_file(relative_path: str) -> None:
    """Remove the on-disk bytes for a media row. Call only after confirming no other
    media_attachment / media row references the same content (content may be shared)."""
    p = abs_path(relative_path)
    if p.is_file():
        p.unlink()
        # Tidy the (now possibly empty) shard directory; ignore if not empty.
        try:
            p.parent.rmdir()
        except OSError:
            pass


# ── Repository layer (DB rows + bytes together) ──────────────────────────────────────

def _get_or_create_media(session: Session, meta: dict) -> Media:
    """Find an existing Media row by sha256 (content already de-duped on disk) or create
    one from a metadata dict produced by store_bytes/store_file."""
    existing = session.scalar(select(Media).where(Media.sha256 == meta["sha256"]))
    if existing is not None:
        return existing
    media = Media(
        sha256=meta["sha256"],
        relative_path=meta["relative_path"],
        category=meta["category"],
        format=meta.get("format"),
        original_filename=meta.get("original_filename"),
        byte_size=meta.get("byte_size"),
        width=meta.get("width"),
        height=meta.get("height"),
    )
    session.add(media)
    session.flush()
    return media


def add_attachment(
    session: Session,
    *,
    target_kind: str,
    target_id: int,
    data: bytes,
    filename: str,
    caption: Optional[str] = None,
) -> MediaAttachment:
    """Store ``data`` in the content-addressed store and attach it to one record.

    target_kind ∈ TARGET_FK. The bytes are de-duplicated; a new media_attachment row is
    always created (the same asset may be attached to several records)."""
    if target_kind not in TARGET_FK:
        raise ValueError(f"unknown target_kind {target_kind!r}")
    meta = store_bytes(data, filename)
    media = _get_or_create_media(session, meta)
    att = MediaAttachment(media_id=media.id, caption=caption)
    setattr(att, TARGET_FK[target_kind], target_id)
    session.add(att)
    session.flush()
    return att


def list_attachments(session: Session, *, target_kind: str, target_id: int) -> list[MediaAttachment]:
    """All media_attachment rows (with their Media loaded) for one record, primary first."""
    col = getattr(MediaAttachment, TARGET_FK[target_kind])
    rows = session.scalars(
        select(MediaAttachment).where(col == target_id)
        .order_by(MediaAttachment.is_primary.desc(), MediaAttachment.sort_order, MediaAttachment.id)
    ).all()
    for r in rows:
        _ = r.media  # eager-touch inside the session so the UI can read it after detach
    return list(rows)


def set_primary(session: Session, *, target_kind: str, target_id: int, attachment_id: int) -> None:
    """Mark one attachment primary for its record, clearing the flag on the others."""
    col = getattr(MediaAttachment, TARGET_FK[target_kind])
    for att in session.scalars(select(MediaAttachment).where(col == target_id)):
        att.is_primary = 1 if att.id == attachment_id else 0


def update_media(session: Session, media_id: int, **fields) -> None:
    """Patch descriptive metadata on a Media row (title/creator/license/category/…)."""
    media = session.get(Media, media_id)
    if media is None:
        return
    allowed = {"category", "title", "creator", "capture_date",
               "license", "rights_holder_id", "source", "remarks"}
    for k, v in fields.items():
        if k in allowed:
            setattr(media, k, v)


def update_attachment(session: Session, attachment_id: int, *, caption: Optional[str] = None) -> None:
    att = session.get(MediaAttachment, attachment_id)
    if att is not None and caption is not None:
        att.caption = caption


def delete_attachment(session: Session, attachment_id: int) -> None:
    """Remove an attachment. If its Media is left with no attachments, delete the Media
    row and its on-disk bytes too (content no longer referenced)."""
    att = session.get(MediaAttachment, attachment_id)
    if att is None:
        return
    media = att.media
    session.delete(att)
    session.flush()
    remaining = session.scalar(
        select(MediaAttachment.id).where(MediaAttachment.media_id == media.id)
    )
    if remaining is None:
        rel = media.relative_path
        session.delete(media)
        session.flush()
        delete_stored_file(rel)

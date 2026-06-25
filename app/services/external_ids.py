"""External resource identifiers (#49).

CRUD for ``external_identifier`` rows attached to a collection_object or a
biological_association (exclusive arc). The user supplies just the URI; ``source`` is an
optional label left unpopulated for now (kept on the row for future flexibility — it can be
derived from the URI at export/query time).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalIdentifier

# Target kinds → the FK column on external_identifier.
TARGET_FK = {
    "collection_object": "collection_object_id",
    "biological_association": "biological_association_id",
}


def add_identifier(
    session: Session,
    *,
    target_kind: str,
    target_id: int,
    value: str,
    source: Optional[str] = None,
    label: Optional[str] = None,
) -> ExternalIdentifier:
    if target_kind not in TARGET_FK:
        raise ValueError(f"unknown target_kind {target_kind!r}")
    if not (value or "").strip():
        raise ValueError("external identifier value cannot be blank")
    ext = ExternalIdentifier(source=source or None, value=value.strip(), label=label or None)
    setattr(ext, TARGET_FK[target_kind], target_id)
    session.add(ext)
    session.flush()
    return ext


def list_identifiers(session: Session, *, target_kind: str, target_id: int) -> list[ExternalIdentifier]:
    col = getattr(ExternalIdentifier, TARGET_FK[target_kind])
    return list(session.scalars(
        select(ExternalIdentifier).where(col == target_id).order_by(ExternalIdentifier.id)
    ).all())


def count_identifiers(session: Session, *, target_kind: str, target_id: int) -> int:
    col = getattr(ExternalIdentifier, TARGET_FK[target_kind])
    return session.query(ExternalIdentifier).filter(col == target_id).count()


def delete_identifier(session: Session, ext_id: int) -> None:
    ext = session.get(ExternalIdentifier, ext_id)
    if ext is not None:
        session.delete(ext)
        session.flush()

"""Identifier (label code) management.

Codes are sequential per collection, e.g. "JJPC-03963" (reserve_sequential_codes).
Legacy random 4-char codes (e.g. "AB3C") may still exist in the table from before
the format change, but are no longer generated.

Workflow:
  reserve_sequential_codes(session, coll_code, n) → create a batch of n sequential codes
  all_batches_with_reserved(session)→ batches with ≥1 reserved code, newest first (viewer)
  codes_for_batch(session, batch_id)→ reserved codes in a specific batch
  assign_code(session, code, co_id) → link a reserved code to a CollectionObject
  reserved_codes(session)           → flat list of all reserved codes (for digitize dropdown)
  codes_for_object(session, co_id)  → all codes assigned to a given specimen
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import LabelBatch, LabelCode, CollectionObject
from app.models.base import _utcnow


@dataclass
class BatchInfo:
    batch_id: int
    created_at: str
    n_reserved: int
    n_total: int


@dataclass
class BatchStats:
    total_batches: int
    total_codes: int
    total_assigned: int
    total_reserved: int


def batch_stats(session: Session) -> BatchStats:
    from sqlalchemy import func
    total   = session.query(func.count(LabelCode.id)).scalar() or 0
    assigned = session.query(func.count(LabelCode.id)).filter(LabelCode.status == "assigned").scalar() or 0
    batches = session.query(func.count(LabelBatch.id)).scalar() or 0
    return BatchStats(
        total_batches=batches,
        total_codes=total,
        total_assigned=assigned,
        total_reserved=total - assigned,
    )


def all_batches_with_reserved(session: Session) -> list[BatchInfo]:
    """All batches that still have at least one reserved code, newest first —
    including batches where some codes are already assigned. Drives the reserved-
    codes viewer (each batch offers "Add to print queue")."""
    from sqlalchemy import func
    n_reserved = func.count(LabelCode.id).filter(LabelCode.status == "reserved")
    n_total    = func.count(LabelCode.id)
    rows = (
        session.query(
            LabelBatch.id,
            LabelBatch.created_at,
            n_reserved.label("n_reserved"),
            n_total.label("n_total"),
        )
        .join(LabelCode, LabelCode.batch_id == LabelBatch.id)
        .group_by(LabelBatch.id)
        .having(n_reserved > 0)
        .order_by(LabelBatch.created_at.desc())
        .all()
    )
    return [BatchInfo(r.id, r.created_at, r.n_reserved, r.n_total) for r in rows]


def codes_for_batch(session: Session, batch_id: int) -> list[str]:
    """Reserved codes belonging to a specific batch."""
    return [
        lc.code
        for lc in session.query(LabelCode)
        .filter(LabelCode.batch_id == batch_id, LabelCode.status == "reserved")
        .order_by(LabelCode.created_at)
        .all()
    ]


def assign_code(session: Session, code: str, collection_object_id: int) -> LabelCode:
    """Mark a reserved code as assigned and link it to a CollectionObject."""
    lc = session.query(LabelCode).filter(LabelCode.code == code).one()
    if lc.status == "assigned":
        raise ValueError(f"Code {code!r} is already assigned.")
    lc.collection_object_id = collection_object_id
    lc.status = "assigned"
    lc.updated_at = _utcnow()

    co = session.get(CollectionObject, collection_object_id)
    if co and not co.catalog_number:
        co.catalog_number = code
        co.updated_at = _utcnow()

    session.flush()
    return lc


def _next_sequential_number(session: Session, collection_code: str) -> int:
    """Return the next 1-based sequence number for the given collection_code prefix.

    The max is computed DB-side via a single aggregate (a prefix range-scan over
    the indexed ``code`` column) rather than loading every matching row into Python.

    Suffixes are zero-padded to 5 digits but may overflow to 6+ digits past 99999,
    so we parse the numeric value (CAST) rather than relying on lexicographic order
    or a fixed length — ``"99999"`` sorts *after* ``"100000"`` as text, so a plain
    ``ORDER BY code DESC`` or a ``len == 5`` filter would miss the real maximum.
    """
    from sqlalchemy import func, cast, Integer
    prefix = f"{collection_code}-"
    suffix = func.substr(LabelCode.code, len(prefix) + 1)
    max_num = (
        session.query(func.max(cast(suffix, Integer)))
        .filter(
            LabelCode.code.like(f"{prefix}%"),
            suffix != "",
            suffix.op("NOT GLOB")("*[^0-9]*"),  # suffix is entirely digits
        )
        .scalar()
    )
    return (max_num or 0) + 1


def format_catalog_display(collection_code: str | None, catalog_number: str | None) -> str:
    """Render a specimen identifier for display without doubling the collection code.

    ``catalog_number`` embeds the collection-code prefix (``JJPRC-00001``) and is
    immutable — it travels with the specimen, so after a transfer the current
    holder (``collection_code``) can differ from that prefix. Display rule:

      * still in its home collection (catalog starts with collection_code) →
        show the catalog number alone (``JJPRC-00001``); the prefix already names
        the origin, so repeating it as ``JJPRC JJPRC-00001`` is redundant.
      * transferred (foreign collection_code) → show both, current holder first
        (``ABC  JJPRC-00001``), which is genuinely informative.

    Display-only; never mutates the stored fields (catalog_number must keep its
    prefix for TaxonWorks matching).
    """
    cc = (collection_code or "").strip()
    cn = (catalog_number or "").strip()
    if not cn:
        return cc
    if cc and (cn == cc or cn.startswith(f"{cc}-") or cn.startswith(f"{cc} ")):
        return cn
    return f"{cc} {cn}".strip()


def reserve_sequential_codes(
    session: Session, collection_code: str, count: int
) -> tuple[int, list[str]]:
    """Generate `count` sequential codes like 'JJPC-03963' and reserve them in a new batch.

    Returns (batch_id, codes).
    Codes are reserved; call assign_code() per specimen after creating CollectionObjects.
    """
    start = _next_sequential_number(session, collection_code)
    codes = [f"{collection_code}-{start + i:05d}" for i in range(count)]

    now = _utcnow()
    batch = LabelBatch(created_at=now, updated_at=now)
    session.add(batch)
    session.flush()

    for code in codes:
        session.add(LabelCode(
            code=code, status="reserved",
            batch_id=batch.id,
            created_at=now, updated_at=now,
        ))
    session.flush()
    return batch.id, codes


def reserved_codes(session: Session) -> list[str]:
    """Flat list of all reserved codes across all batches (for the digitize dropdown)."""
    return [
        lc.code
        for lc in session.query(LabelCode)
        .filter(LabelCode.status == "reserved")
        .order_by(LabelCode.created_at)
        .all()
    ]


def codes_for_object(session: Session, collection_object_id: int) -> list[str]:
    return [
        lc.code
        for lc in session.query(LabelCode)
        .filter(LabelCode.collection_object_id == collection_object_id)
        .all()
    ]

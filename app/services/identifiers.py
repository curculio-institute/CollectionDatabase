"""Identifier (label code) management.

Two code formats are supported and coexist in the same table:
  - Legacy random 4-char codes, e.g. "AB3C"  (reserve_codes)
  - Sequential codes,           e.g. "JJPC-03963"  (reserve_sequential_codes)

Workflow:
  reserve_codes(session, n)                       → create a batch, generate n unique random codes
  reserve_sequential_codes(session, coll_code, n) → create a batch of n sequential codes
  batches_with_reserved(session)    → list of (batch_id, created_at, n_reserved) for UI
  codes_for_batch(session, batch_id)→ reserved codes in a specific batch
  assign_code(session, code, co_id) → link a reserved code to a CollectionObject
  reserved_codes(session)           → flat list of all reserved codes (for digitize dropdown)
  codes_for_object(session, co_id)  → all codes assigned to a given specimen
"""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import LabelBatch, LabelCode, CollectionObject
from app.models.base import _utcnow

_CHARS = string.ascii_uppercase + string.digits  # 36 characters


def _random_code() -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(4))


def reserve_codes(session: Session, count: int) -> tuple[int, list[str]]:
    """Create a batch, generate `count` unique codes, return (batch_id, codes)."""
    existing = {row.code for row in session.query(LabelCode.code).all()}
    codes: list[str] = []
    while len(codes) < count:
        code = _random_code()
        if code not in existing and code not in codes:
            codes.append(code)

    now = _utcnow()
    batch = LabelBatch(created_at=now, updated_at=now)
    session.add(batch)
    session.flush()  # get batch.id

    for code in codes:
        session.add(LabelCode(
            code=code, status="reserved",
            batch_id=batch.id,
            created_at=now, updated_at=now,
        ))
    session.flush()
    return batch.id, codes


@dataclass
class BatchInfo:
    batch_id: int
    created_at: str
    n_reserved: int
    n_total: int


def batches_with_reserved(session: Session) -> list[BatchInfo]:
    """Return batches where every code is still reserved (none assigned), newest first.

    A batch becomes ineligible for reprint the moment any of its codes is assigned
    to a specimen — reprinting at that point risks producing duplicate labels.
    """
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
        .having(n_reserved == n_total)   # all codes still unused
        .order_by(LabelBatch.created_at.desc())
        .all()
    )
    return [BatchInfo(r.id, r.created_at, r.n_reserved, r.n_total) for r in rows]


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
    """All batches that still have at least one reserved code, newest first.

    Unlike batches_with_reserved, this includes batches where some codes are
    already assigned — used for the viewer, not for reprinting.
    """
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


def reserve_sequential_codes(
    session: Session, collection_code: str, count: int
) -> tuple[int, list[str]]:
    """Generate `count` sequential codes like 'JJPC-03963' and reserve them in a new batch.

    Same return signature as reserve_codes: (batch_id, codes).
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

"""Generic single-name controlled-vocabulary service.

A controlled vocabulary here is a small reference table with a stable ``id`` and a
single, unique human-readable ``name`` column, referenced from data rows by FK —
exactly the pattern the ``person`` table uses for recordedBy / identifiedBy, but
without person's extra columns (abbreviated_name / orcid). The first such vocab is
``preparation`` (#preparations); several more are planned, so this is built once and
reused.

``Vocabulary`` wraps one such model. It mirrors ``app/services/persons.py``:

  list / options / get_or_create / create / update / delete / merge_preview / merge

Merge and delete-safety re-discover the referencing FK columns dynamically via
``PRAGMA foreign_key_list`` (no hardcoded table list), so any FK pointing at the
vocab table — present or future — is handled automatically.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.base import _utcnow


@dataclass(frozen=True)
class MergePreview:
    keep_id: int
    keep_name: str
    absorb_id: int
    absorb_name: str
    reference_count: int   # total rows that will be re-pointed


class Vocabulary:
    """CRUD + merge for one single-name controlled-vocabulary table.

    Parameters
    ----------
    model:     the SQLAlchemy model (must have ``id`` and a single name column).
    ref_table: the model's __tablename__ (the FK target table name).
    name_attr: the model's name attribute (default ``"name"``).
    noun:      singular human label for messages (e.g. ``"preparation"``).
    """

    def __init__(self, model, *, ref_table: str, name_attr: str = "name",
                 noun: str = "entry"):
        self.model = model
        self.ref_table = ref_table
        self.name_attr = name_attr
        self.noun = noun

    # ── name helpers ──────────────────────────────────────────────────────────

    def _name(self, obj) -> str:
        return getattr(obj, self.name_attr)

    def display(self, obj) -> str:
        """Public accessor for an entry's name (for UI rendering)."""
        return self._name(obj)

    def _name_col(self):
        return getattr(self.model, self.name_attr)

    # ── reads ─────────────────────────────────────────────────────────────────

    def list(self, session: Session) -> list:
        return session.query(self.model).order_by(self._name_col()).all()

    def options(self, session: Session) -> dict[str, str]:
        """{name: name} for ui.select / the vocab dropdown widget."""
        return {self._name(o): self._name(o) for o in self.list(session)}

    # ── writes ────────────────────────────────────────────────────────────────

    def get_or_create(self, session: Session, name: str):
        """Return the existing row with this name, or create one.

        Safe to call repeatedly in one transaction — the flush in ``create``
        makes the new row visible so a second call finds it instead of hitting
        the unique constraint.

        Matching is **case-insensitive** so a geocoder's ``"Germany"`` and a
        hand-typed ``"germany"`` resolve to the same canonical row instead of
        creating a case-variant duplicate (#73). The first-created spelling
        stays canonical; later differently-cased inputs reuse it."""
        clean = (name or "").strip()
        existing = (
            session.query(self.model)
            .filter(func.lower(self._name_col()) == clean.lower())
            .first()
        )
        if existing:
            return existing
        return self.create(session, name=clean)

    def create(self, session: Session, *, name: str):
        obj = self.model(created_at=_utcnow(), updated_at=_utcnow())
        setattr(obj, self.name_attr, (name or "").strip())
        session.add(obj)
        session.flush()
        return obj

    def update(self, session: Session, obj_id: int, *, name: str):
        obj = session.get(self.model, obj_id)
        if obj is None:
            raise ValueError(f"{self.noun} {obj_id} not found")
        setattr(obj, self.name_attr, (name or "").strip())
        obj.updated_at = _utcnow()
        session.flush()
        return obj

    def delete(self, session: Session, obj_id: int) -> None:
        obj = session.get(self.model, obj_id)
        if obj is None:
            return
        count = self._count_references(session, obj_id)
        if count:
            noun = "record" if count == 1 else "records"
            raise ValueError(
                f"Cannot delete '{self._name(obj)}': still used by {count} {noun}."
            )
        session.delete(obj)
        session.flush()

    # ── merge ─────────────────────────────────────────────────────────────────

    def merge_preview(self, session: Session, keep_id: int, absorb_id: int) -> MergePreview:
        keep = session.get(self.model, keep_id)
        absorb = session.get(self.model, absorb_id)
        if keep is None or absorb is None:
            raise ValueError("One or both entries not found")
        return MergePreview(
            keep_id=keep_id,
            keep_name=self._name(keep),
            absorb_id=absorb_id,
            absorb_name=self._name(absorb),
            reference_count=self._count_references(session, absorb_id),
        )

    def merge(self, session: Session, keep_id: int, absorb_id: int) -> None:
        """Re-point every FK reference from absorb → keep, then delete absorb."""
        if keep_id == absorb_id:
            raise ValueError("Cannot merge an entry with itself")
        keep = session.get(self.model, keep_id)
        absorb = session.get(self.model, absorb_id)
        if keep is None or absorb is None:
            raise ValueError("One or both entries not found")
        for table, col in self._fk_references(session):
            session.execute(
                text(f'UPDATE "{table}" SET "{col}" = :keep WHERE "{col}" = :absorb'),
                {"keep": keep_id, "absorb": absorb_id},
            )
        session.delete(absorb)
        session.flush()

    # ── internal: dynamic FK discovery ────────────────────────────────────────

    def _all_user_tables(self, session: Session) -> list[str]:
        rows = session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        )).fetchall()
        return [r[0] for r in rows]

    def _fk_references(self, session: Session) -> list[tuple[str, str]]:
        """[(table, column), …] for every FK column referencing this vocab's id."""
        refs: list[tuple[str, str]] = []
        for table in self._all_user_tables(session):
            for fk in session.execute(
                text(f'PRAGMA foreign_key_list("{table}")')
            ).fetchall():
                # PRAGMA cols: id, seq, table, from, to, on_update, on_delete, match
                if fk[2] == self.ref_table and fk[4] == "id":
                    refs.append((table, fk[3]))
        return refs

    def _count_references(self, session: Session, obj_id: int) -> int:
        total = 0
        for table, col in self._fk_references(session):
            total += session.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" = :id'),
                {"id": obj_id},
            ).scalar() or 0
        return total

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

# Sentinel: "the caller said nothing about the code" — distinct from an explicit None,
# which means "clear it". Only meaningful for a code-bearing vocab (see ``code_attr``).
_UNSET: str = "\x00__unset__"


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
    model:      the SQLAlchemy model (must have ``id`` and a single name column).
    ref_table:  the model's __tablename__ (the FK target table name).
    name_attr:  the model's name attribute (default ``"name"``).
    noun:       singular human label for messages (e.g. ``"preparation"``).
    has_default: whether the model carries an ``is_default`` flag column (at most one
                row flagged; a partial-unique index enforces it). Enables ``get_default``
                / ``set_default`` for a Tier-1 autofill default (mirrors repository #83).
    code_attr:  name of an ISO-code column, if the vocab has one (``country`` /
                ``state_province``). When set, a row's identity is **(name, code)**, not
                the name — because 40 ISO 3166-2 subdivision names are shared across
                countries (Limburg = BE-VLI + NL-LI). ``get_or_create`` then matches on
                both, and creates rather than reusing a row with a different code.
                Migration 0056.
    """

    def __init__(self, model, *, ref_table: str, name_attr: str = "name",
                 noun: str = "entry", has_default: bool = False,
                 code_attr: str | None = None):
        self.model = model
        self.ref_table = ref_table
        self.name_attr = name_attr
        self.noun = noun
        self.has_default = has_default
        self.code_attr = code_attr

    # ── name helpers ──────────────────────────────────────────────────────────

    def _name(self, obj) -> str:
        return getattr(obj, self.name_attr)

    def display(self, obj) -> str:
        """Public accessor for an entry's name (for UI rendering)."""
        return self._name(obj)

    def _name_col(self):
        return getattr(self.model, self.name_attr)

    # ── default flag (Tier-1 autofill) ────────────────────────────────────────

    def get_default(self, session: Session):
        """The flagged default entry, or None. Returns None if the vocab has no
        default flag (``has_default`` is False)."""
        if not self.has_default:
            return None
        return (session.query(self.model)
                .filter(self.model.is_default == 1).one_or_none())

    def get_default_name(self, session: Session) -> str | None:
        """The flagged default entry's name, or None."""
        obj = self.get_default(session)
        return self._name(obj) if obj else None

    def set_default(self, session: Session, obj_id: int | None) -> None:
        """Make ``obj_id`` the sole default (or clear the default when None).

        Clears the old default first so the partial-unique index never trips
        mid-statement (same guard as repositories.set_default)."""
        if not self.has_default:
            raise ValueError(f"{self.noun} vocabulary has no default flag")
        now = _utcnow()
        session.query(self.model).filter(self.model.is_default == 1).update(
            {"is_default": 0, "updated_at": now})
        if obj_id is not None:
            if session.get(self.model, obj_id) is None:
                raise ValueError(f"{self.noun} {obj_id} not found")
            session.query(self.model).filter(self.model.id == obj_id).update(
                {"is_default": 1, "updated_at": now})
        session.flush()

    # ── reads ─────────────────────────────────────────────────────────────────

    def list(self, session: Session) -> list:
        return session.query(self.model).order_by(self._name_col()).all()

    def options(self, session: Session) -> dict[str, str]:
        """{name: name} for ui.select / the vocab dropdown widget.

        Keys are the plain names — `vocab_field` matches typed text against them, so they
        must not be decorated. A code-bearing vocab can hold two rows with the same name
        (Limburg BE-VLI / NL-LI); they collapse to one key here, which is why such vocabs
        are rendered from ``list()`` + ``display_label()`` in the Controlled Vocabularies
        card, not from this dict.
        """
        return {self._name(o): self._name(o) for o in self.list(session)}

    def display_label(self, obj) -> str:
        """Name, suffixed with the ISO code when the vocab has one — "Limburg (NL-LI)".

        Two rows may legitimately share a name (see ``code_attr``); the code is the only
        thing that tells them apart in a list.
        """
        name = self._name(obj)
        code = getattr(obj, self.code_attr, None) if self.code_attr else None
        return f"{name} ({code})" if code else name

    def entries(self, session: Session) -> list[tuple[str, str | None]]:
        """[(name, code)] for the dropdown widget — one tuple per *row*, not per name.

        Unlike ``options()`` this never collapses two same-named rows: the widget must be
        able to offer "Limburg (BE-VLI)" and "Limburg (NL-LI)" as distinct picks, because
        choosing one has to be unambiguous at save time.
        """
        return [(self._name(o),
                 getattr(o, self.code_attr) if self.code_attr else None)
                for o in self.list(session)]

    # ── writes ────────────────────────────────────────────────────────────────

    def get_or_create(self, session: Session, name: str, *, code: str | None = None):
        """Return the existing row matching this entry, or create one.

        Safe to call repeatedly in one transaction — the flush in ``create``
        makes the new row visible so a second call finds it instead of hitting
        the unique constraint.

        Matching is **case-insensitive** so a geocoder's ``"Germany"`` and a
        hand-typed ``"germany"`` resolve to the same canonical row instead of
        creating a case-variant duplicate (#73). The first-created spelling
        stays canonical; later differently-cased inputs reuse it.

        For a code-bearing vocab (``code_attr``), identity is **(name, code)**:
        an exact match is reused, anything else creates a new row. An existing
        row is **never mutated** to carry a code it did not have — a hand-typed
        ``Limburg`` must not be silently declared Dutch. Two rows that do turn
        out to mean the same place are folded with ``merge``.

        **A name with no code reuses the one row that bears it, when there is
        exactly one.** A CSV with a `country` column and no `countryCode` said
        "Austria", and the strict (name, code) rule answered with a *second*,
        uncoded Austria beside the geocoded `Austria (AT)` — a duplicate on
        every import, for a name nothing was ambiguous about. Ambiguity is what
        the strict rule exists for, so it applies only where ambiguity actually
        exists: when two or more rows already share the name (`Limburg` BE-VLI /
        NL-LI), an uncoded input still creates its own row rather than guess
        which country was meant. Nothing is mutated in either case.
        """
        clean = (name or "").strip()
        q = session.query(self.model).filter(func.lower(self._name_col()) == clean.lower())
        if self.code_attr:
            clean_code = (code or "").strip().upper() or None
            col = getattr(self.model, self.code_attr)
            if clean_code is not None:
                existing = q.filter(col == clean_code).first()
                return existing or self.create(session, name=clean, code=clean_code)
            same_name = q.all()
            exact = [o for o in same_name if getattr(o, self.code_attr) is None]
            if exact:
                return exact[0]
            if len(same_name) == 1:
                return same_name[0]          # the only row of that name — no ambiguity
            return self.create(session, name=clean, code=None)
        existing = q.first()
        if existing:
            return existing
        return self.create(session, name=clean)

    def create(self, session: Session, *, name: str, code: str | None = None):
        obj = self.model(created_at=_utcnow(), updated_at=_utcnow())
        setattr(obj, self.name_attr, (name or "").strip())
        if self.code_attr:
            setattr(obj, self.code_attr, (code or "").strip().upper() or None)
        session.add(obj)
        session.flush()
        return obj

    def update(self, session: Session, obj_id: int, *, name: str, code: str | None = _UNSET):
        """Rename an entry, and (for a code-bearing vocab) set or clear its ISO code.

        ``code`` defaults to ``_UNSET`` so a caller that does not mention it leaves the
        existing code alone — passing ``None`` explicitly *clears* it. Editing the code is
        how a user corrects or supplies a code the geocoder never provided.
        """
        obj = session.get(self.model, obj_id)
        if obj is None:
            raise ValueError(f"{self.noun} {obj_id} not found")
        setattr(obj, self.name_attr, (name or "").strip())
        if self.code_attr and code is not _UNSET:
            setattr(obj, self.code_attr, (code or "").strip().upper() or None)
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

"""Service for the ``export_settings`` table (#149).

Single-row table remembering the TaxonWorks export's taxonomy restriction (e.g. "only
export Curculionoidea") across restarts. Same "remember my last choice" spirit as
``saved_search``, but a single current value rather than a named, reusable list, so it is
its own tiny table rather than a saved-search row. No ORM model — the same choice
``person_defaults.py`` makes for the same reason: one column is not worth a mapped class.

FK is ``ON DELETE SET NULL`` (migration 0067): if the scoping taxon is deleted or merged
away, the restriction silently clears rather than leaving a dangling id or blocking the
taxon operation — see the migration's docstring for why that is the safe choice here,
unlike ``person_defaults``' ``RESTRICT``.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_taxon_scope_id(session: Session) -> int | None:
    """The remembered taxon_id export restriction, or None (export everything)."""
    row = session.execute(
        text("SELECT tw_export_taxon_id FROM export_settings WHERE id = 1")
    ).fetchone()
    return row[0] if row else None


def set_taxon_scope_id(session: Session, taxon_id: int | None) -> None:
    """Remember (or clear, when ``taxon_id`` is None) the export's taxonomy restriction.

    Call inside an open transaction (mirrors ``person_defaults.set_defaults``).
    """
    session.execute(
        text("UPDATE export_settings SET tw_export_taxon_id = :t WHERE id = 1"),
        {"t": taxon_id},
    )

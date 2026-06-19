"""Collect a before/after diff of taxon rows produced by an import function.

Usage (preview only — nothing is committed):
    with session_factory() as session:
        changes = collect_import_preview(
            session,
            lambda: get_or_create_from_tw_data(session, tw_data, otu_id=otu_id),
        )
    # session closed; DB unchanged

The import function is run inside a savepoint that is always rolled back, so the
caller can call it again in a proper committed transaction to actually apply the
changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.models import Taxon


# Fields shown in the preview dialog, in display order.
# Tuple: (snapshot_key, display_label).
# scientific_name and taxon_rank are omitted — they appear in the section header.
PREVIEW_FIELDS: list[tuple[str, str]] = [
    ("taxonomic_status",           "taxonomicStatus"),
    ("scientific_name_authorship", "authorship"),
    ("parent_name",                "parentNameUsage"),
    ("accepted_name",              "acceptedNameUsage"),
    ("nomenclatural_code",         "nomenclaturalCode"),
    ("taxonworks_otu_id",          "taxonworksOtuID"),
]


@dataclass
class TaxonChangeRecord:
    scientific_name: str
    taxon_rank: str
    is_new: bool
    before: dict | None   # None for newly created rows
    after: dict


def _snapshot(t: Taxon, session: Session) -> dict:
    """Return a plain-dict snapshot of a Taxon row.

    parentNameUsageID and acceptedNameUsageID are resolved to scientific names
    so the UI can display them without an extra DB lookup.
    """
    parent_name: str | None = None
    if t.parent_name_usage_id is not None:
        p = session.get(Taxon, t.parent_name_usage_id)
        parent_name = (p.scientific_name if p else None) or f"id:{t.parent_name_usage_id}"

    accepted_name: str | None = None
    if t.accepted_name_usage_id is not None:
        a = session.get(Taxon, t.accepted_name_usage_id)
        accepted_name = (a.scientific_name if a else None) or f"id:{t.accepted_name_usage_id}"

    return {
        "scientific_name":            t.scientific_name,
        "taxon_rank":                 t.taxon_rank,
        # Derived (not stored): a taxon is a synonym iff it links to an accepted
        # name. Shown here because this is the value the DwC export emits.
        "taxonomic_status":           "synonym" if t.accepted_name_usage_id is not None else "accepted",
        "scientific_name_authorship": t.scientific_name_authorship,
        "parent_name":                parent_name,
        "accepted_name":              accepted_name,
        "nomenclatural_code":         t.nomenclatural_code,
        "taxonworks_otu_id":          t.taxonworks_otu_id,
    }


def collect_import_preview(
    session: Session,
    import_fn: Callable,
) -> list[TaxonChangeRecord]:
    """Run import_fn inside a savepoint, collect row changes, roll back.

    import_fn() is a zero-argument callable that uses session internally and
    returns the primary Taxon.  All rows created or modified during the call
    are recorded.  The savepoint is always rolled back — nothing is persisted.

    Returns new rows first (ordered by id), then modified rows (ordered by
    scientific_name).  Rows whose snapshot is identical before and after are
    excluded.
    """
    existing: dict[int, dict] = {
        t.id: _snapshot(t, session)
        for t in session.query(Taxon).all()
    }
    max_id_before = max(existing.keys()) if existing else 0

    sp = session.begin_nested()
    changes: list[TaxonChangeRecord] = []
    try:
        import_fn()
        session.flush()

        for t in (
            session.query(Taxon)
            .filter(Taxon.id > max_id_before)
            .order_by(Taxon.id)
            .all()
        ):
            changes.append(TaxonChangeRecord(
                scientific_name=t.scientific_name or "",
                taxon_rank=t.taxon_rank or "",
                is_new=True,
                before=None,
                after=_snapshot(t, session),
            ))

        modified: list[TaxonChangeRecord] = []
        for old_id, old_snap in existing.items():
            t = session.get(Taxon, old_id)
            if t is None:
                continue
            new_snap = _snapshot(t, session)
            if new_snap != old_snap:
                modified.append(TaxonChangeRecord(
                    scientific_name=t.scientific_name or "",
                    taxon_rank=t.taxon_rank or "",
                    is_new=False,
                    before=old_snap,
                    after=new_snap,
                ))
        modified.sort(key=lambda r: r.scientific_name)
        changes.extend(modified)

    finally:
        sp.rollback()
        session.expire_all()

    return changes

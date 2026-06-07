"""Biological association CRUD and TaxonWorks relationship sync.

Session-start workflow:
  Call sync_biological_relationships(session) once per session.
  It fetches the TW relationship list and upserts into the local table by
  taxonworksID.  If TW is unreachable the function returns silently and the
  UI falls back to whatever rows are already in the local table.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.base import _utcnow
from app.models.biological import BiologicalAssociation, BiologicalRelationship


# ---------------------------------------------------------------------------
# Relationship sync
# ---------------------------------------------------------------------------

async def sync_biological_relationships(session: Session) -> None:
    """Fetch TW biological_relationships and upsert into local table by taxonworksID.

    Safe to call multiple times (idempotent).  Never deletes local rows so
    existing BiologicalAssociation FK references are always valid.
    """
    try:
        import app.services.taxonworks as tw_svc
        tw_rows = await tw_svc.fetch_biological_relationships()
    except Exception:
        return  # TW unreachable — use cached local rows

    now = _utcnow()
    for tw in tw_rows:
        tw_id   = tw.get("id")
        tw_name = (tw.get("name") or "").strip()
        if not tw_id or not tw_name:
            continue

        existing = (
            session.query(BiologicalRelationship)
            .filter(BiologicalRelationship.taxonworks_id == tw_id)
            .first()
        )
        if existing:
            if existing.name != tw_name:
                existing.name       = tw_name
                existing.updated_at = now
        else:
            session.add(BiologicalRelationship(
                name=tw_name,
                taxonworks_id=tw_id,
                created_at=now,
                updated_at=now,
            ))

    session.flush()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelationshipOption:
    id: int
    name: str
    taxonworks_id: int | None


def get_relationship_options(session: Session) -> list[RelationshipOption]:
    """Return all local biological relationships ordered: active first, legacy last."""
    rows = (
        session.query(BiologicalRelationship)
        .order_by(
            BiologicalRelationship.name.contains("[legacy]"),
            BiologicalRelationship.name,
        )
        .all()
    )
    return [RelationshipOption(r.id, r.name, r.taxonworks_id) for r in rows]


@dataclass(frozen=True)
class AssociationRow:
    id: int
    rel_name: str
    object_label: str
    object_taxon_id: int | None


def get_associations_for_specimen(
    session: Session, collection_object_id: int
) -> list[AssociationRow]:
    """Return all biological associations where the specimen is the subject."""
    from app.models import Taxon
    from app.services.taxa import format_scientific_name

    rows = (
        session.query(BiologicalAssociation)
        .filter(
            BiologicalAssociation.subject_collection_object_id == collection_object_id
        )
        .all()
    )
    out = []
    for r in rows:
        rel_name = r.biological_relationship.name if r.biological_relationship else "?"
        if r.object_taxon:
            obj_label = format_scientific_name(r.object_taxon)
        else:
            obj_label = f"specimen #{r.object_collection_object_id}"
        out.append(AssociationRow(r.id, rel_name, obj_label, r.object_taxon_id))
    return out


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def save_biological_association(
    session: Session,
    *,
    collection_object_id: int,
    biological_relationship_id: int,
    object_taxon_id: int,
) -> BiologicalAssociation:
    """Create one BiologicalAssociation with the specimen as subject and a taxon as object."""
    now = _utcnow()
    assoc = BiologicalAssociation(
        biological_relationship_id=biological_relationship_id,
        subject_collection_object_id=collection_object_id,
        object_taxon_id=object_taxon_id,
        created_at=now,
        updated_at=now,
    )
    session.add(assoc)
    session.flush()
    return assoc


def remove_biological_association(session: Session, ba_id: int) -> None:
    """Delete a BiologicalAssociation by id. No-op if not found."""
    ba = session.get(BiologicalAssociation, ba_id)
    if ba:
        session.delete(ba)
        session.flush()

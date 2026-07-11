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
        .filter(~BiologicalRelationship.name.contains("[legacy]"))
        .order_by(BiologicalRelationship.name)
        .all()
    )
    return [RelationshipOption(r.id, r.name, r.taxonworks_id) for r in rows]


@dataclass(frozen=True)
class AssociationRow:
    id: int
    rel_name: str
    object_label: str
    object_taxon_id: int | None
    # The relationship's FK, needed to re-create a staged association on save (Records
    # stages adds/removes until "Save changes"); rel_name alone is a display label.
    rel_id: int | None = None
    # When the object is a field_occurrence (the normal case now), its id + the current
    # determination's qualifier — so the UI can show the qualifier and open the full editor.
    object_field_occurrence_id: int | None = None
    identification_qualifier: str | None = None


def get_associations_for_specimen(
    session: Session, collection_object_id: int
) -> list[AssociationRow]:
    """Return all biological associations where the specimen is the subject."""
    from app.models import Taxon
    from app.services.taxa import format_scientific_name

    from app.services import field_occurrence as fo_svc

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
        obj_taxon_id = None
        fo_id = None
        qualifier = None
        if r.object_field_occurrence:
            # The normal case: object is a HumanObservation. Resolve its current
            # determination for the label + taxon + qualifier the UI needs.
            det = fo_svc.current_determination(session, r.object_field_occurrence)
            obj_label = fo_svc.object_label(session, r.object_field_occurrence)
            fo_id = r.object_field_occurrence_id
            if det:
                obj_taxon_id = det.taxon_id
                qualifier = det.identification_qualifier
        elif r.object_taxon:
            obj_label = format_scientific_name(r.object_taxon)
            obj_taxon_id = r.object_taxon_id
        else:
            obj_label = f"specimen #{r.object_collection_object_id}"
        out.append(AssociationRow(
            r.id, rel_name, obj_label, obj_taxon_id, r.biological_relationship_id,
            object_field_occurrence_id=fo_id, identification_qualifier=qualifier))
    return out


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def save_biological_association(
    session: Session,
    *,
    collection_object_id: int,
    biological_relationship_id: int,
    object_taxon_id: int | None = None,
    object_field_occurrence_id: int | None = None,
) -> BiologicalAssociation:
    """Create one BiologicalAssociation with the specimen as subject. The object is a
    field_occurrence (the normal case) or, for a lightweight legacy write, a bare taxon —
    the exclusive-arc CHECK enforces exactly one."""
    now = _utcnow()
    assoc = BiologicalAssociation(
        biological_relationship_id=biological_relationship_id,
        subject_collection_object_id=collection_object_id,
        object_taxon_id=object_taxon_id,
        object_field_occurrence_id=object_field_occurrence_id,
        created_at=now,
        updated_at=now,
    )
    session.add(assoc)
    session.flush()
    return assoc


def save_association_as_field_occurrence(
    session: Session,
    *,
    collection_object_id: int,
    biological_relationship_id: int,
    taxon_id: int,
    identification_qualifier: str | None = None,
) -> BiologicalAssociation:
    """The standard association write (decided 2026-07-11): the object taxon is recorded as
    its own HumanObservation ``field_occurrence`` sharing the specimen's collecting event,
    with identifiedBy defaulting to the event's recordedBy and only the qualifier surfaced at
    data entry. Returns the created association (its object_field_occurrence_id is set)."""
    from app.models import CollectionObject
    from app.services import field_occurrence as fo_svc

    co = session.get(CollectionObject, collection_object_id)
    if co is None:
        raise ValueError(f"collection_object {collection_object_id} not found")
    event_id = co.collecting_event_id
    if event_id is None:
        raise ValueError(
            "cannot record a host observation for a specimen with no collecting event")

    fo = fo_svc.create_field_occurrence(
        session,
        collecting_event_id=event_id,
        taxon_id=taxon_id,
        identification_qualifier=identification_qualifier,
        identified_by_id=fo_svc.event_recorded_by_id(session, event_id),
    )
    return save_biological_association(
        session,
        collection_object_id=collection_object_id,
        biological_relationship_id=biological_relationship_id,
        object_field_occurrence_id=fo.id,
    )


def remove_biological_association(session: Session, ba_id: int) -> None:
    """Delete a BiologicalAssociation by id. No-op if not found."""
    ba = session.get(BiologicalAssociation, ba_id)
    if ba:
        session.delete(ba)
        session.flush()

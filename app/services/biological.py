"""Biological association CRUD and the biological-relationship vocabulary.

Relationships are a local controlled vocabulary with an optional TaxonWorks link
(``tw_links.py``), managed in Controlled Vocabularies → Biological relationships. The old
launch sync is gone (spec 2026-10-14 "Removed: the launch sync"): it renamed local rows to
the TW name on every launch — after a host change that could re-label a relationship with
a *different* TW relationship's name, changing what existing associations say — and it
swallowed every error.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.base import _utcnow
from app.models.biological import BiologicalAssociation, BiologicalRelationship
from app.services.vocab import Vocabulary


relationship_vocab = Vocabulary(
    BiologicalRelationship, ref_table="biological_relationship", noun="relationship")


def _clean_relationship_name(session: Session, name: str,
                             exclude_id: int | None = None) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("A relationship needs a name.")
    q = session.query(BiologicalRelationship).filter(
        func.lower(BiologicalRelationship.name) == clean.lower())
    if exclude_id is not None:
        q = q.filter(BiologicalRelationship.id != exclude_id)
    if session.query(q.exists()).scalar():
        raise ValueError(f"A relationship named '{clean}' already exists.")
    return clean


def create_relationship(session: Session, name: str) -> BiologicalRelationship:
    return relationship_vocab.create(session, name=_clean_relationship_name(session, name))


def rename_relationship(session: Session, rel_id: int, name: str) -> BiologicalRelationship:
    """Rename the LOCAL name. A link's ``tw_name`` is TaxonWorks' and is not touched — the
    two may differ by design (spec "keep local name")."""
    return relationship_vocab.update(
        session, rel_id, name=_clean_relationship_name(session, name, exclude_id=rel_id))


def relationship_usage(session: Session, rel_id: int) -> int:
    """How many records use this relationship — deleting it is refused while > 0."""
    return relationship_vocab.usage(session, rel_id)


def merge_relationships(session: Session, keep_id: int, absorb_id: int) -> None:
    """De-duplicate two relationships (spec "Merge rules"): the absorbed one must be
    local only — a merge never discards or moves a TaxonWorks link."""
    absorb = session.get(BiologicalRelationship, absorb_id)
    if absorb is None:
        raise ValueError("relationship not found")
    if absorb.tw_id is not None:
        raise ValueError(f"Unlink '{absorb.name}' from TaxonWorks first — a merge never "
                         f"discards or moves a link.")
    relationship_vocab.merge(session, keep_id=keep_id, absorb_id=absorb_id)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelationshipOption:
    id: int
    name: str
    taxonworks_id: int | None


def association_host(
    session: Session, a: BiologicalAssociation
) -> tuple[str, str, str | None, str | None] | None:
    """(relationship, host taxon name, rank, qualifier) for an association whose object
    resolves to a taxon — the host, usually a plant. The object may be a taxon directly
    *or* a field occurrence (the current model records a host as a HumanObservation), so
    resolve through the field occurrence's current determination too — which also carries
    the host's open-nomenclature ``qualifier`` (``None`` for a bare object_taxon, which has
    no determination). An object that is another specimen (or resolves to no taxon) returns
    None. One owner so every browse surface renders hosts the same way (Explore + the
    Records picker)."""
    from app.services import field_occurrence as fo_svc
    t = a.object_taxon
    qualifier: str | None = None
    if t is None and a.object_field_occurrence is not None:
        det = fo_svc.current_determination(session, a.object_field_occurrence)
        if det is not None:
            t = det.taxon
            qualifier = det.identification_qualifier
    if t is None:
        return None
    rel = a.biological_relationship.name if a.biological_relationship else ""
    return (rel, t.scientific_name or "", t.taxon_rank, qualifier)


def get_relationship_options(session: Session) -> list[RelationshipOption]:
    """Return all local biological relationships ordered: active first, legacy last."""
    rows = (
        session.query(BiologicalRelationship)
        .filter(~BiologicalRelationship.name.contains("[legacy]"))
        .order_by(BiologicalRelationship.name)
        .all()
    )
    return [RelationshipOption(r.id, r.name, r.taxonworks_id) for r in rows]


def check_staged_relationships(session: Session, associations: list[dict]) -> None:
    """Refuse a Save whose staged association names a relationship that was merged or
    deleted since it was staged (the live set is ``get_relationship_options``). Shared by
    Digitize and Mounting so both stop with the same named message."""
    live = {r.id for r in get_relationship_options(session)}
    for a in associations:
        if a["rel_id"] not in live:
            raise ValueError(f"The relationship '{a['rel_name']}' was merged or deleted "
                             f"— pick it again.")


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

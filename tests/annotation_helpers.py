"""Fixtures shared by the annotation tests (service, export, compare)."""
from app.models import AnnotationTerm, CollectingEvent, Taxon
from app.models.base import _utcnow
from app.services import specimens as spec_svc
from app.services.taxa import compose_scientific_name
from tests.helpers import ensure_repo

SFG = "sfg.taxonworks.org"
SANDBOX = "sandbox.taxonworks.org"


def make_term(session, kind: str = "tag", name: str = "Adventive", *,
              specimen: bool = True, event: bool = False, association: bool = False,
              tw_id: int | None = None, tw_name: str | None = None,
              tw_host: str | None = None, css_color: str | None = None) -> AnnotationTerm:
    """Direct ORM insert, so a test can put a term in any link state (linked, verified,
    verified elsewhere) without going through tw_links."""
    t = AnnotationTerm(kind=kind, name=name, allow_specimen=int(specimen),
                       allow_event=int(event), allow_association=int(association),
                       tw_id=tw_id, tw_name=tw_name if tw_id is not None else None,
                       tw_host=tw_host, css_color=css_color,
                       created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def species(session) -> Taxon:
    sp = session.query(Taxon).filter_by(scientific_name="Otiorhynchus crypticus").first()
    if sp is not None:
        return sp
    genus = Taxon(name_element="Otiorhynchus", scientific_name="Otiorhynchus",
                  taxon_rank="genus", nomenclatural_code="ICZN",
                  created_at=_utcnow(), updated_at=_utcnow())
    session.add(genus)
    session.flush()
    sp = Taxon(name_element="crypticus", scientific_name="crypticus", taxon_rank="species",
               parent_name_usage_id=genus.id, nomenclatural_code="ICZN",
               created_at=_utcnow(), updated_at=_utcnow())
    session.add(sp)
    session.flush()
    sp.scientific_name = compose_scientific_name(session, sp)
    session.flush()
    return sp


def make_event(session) -> CollectingEvent:
    ev = CollectingEvent(created_at=_utcnow(), updated_at=_utcnow())
    session.add(ev)
    session.flush()
    return ev


def make_specimen(session, catalog_number: str, *, event_id: int | None = None,
                  code: str = "ANNX"):
    """An export-ELIGIBLE specimen: a definite species determination and a collection
    with both codes, so the occurrenceID triplet forms."""
    repo_id = ensure_repo(session, code, institution="JJ")
    co = spec_svc.create_collection_object(
        session, collecting_event_id=event_id, catalog_number=catalog_number,
        repository_id=repo_id)
    session.flush()
    spec_svc.create_determination(session, collection_object_id=co.id,
                                  taxon_id=species(session).id, is_current=1)
    session.flush()
    session.refresh(co)
    return co

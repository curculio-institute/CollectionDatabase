"""Explore service — faceted search, drawer-order checklist, filtering, CSV (#40)."""
from app.models import Taxon, CollectingEvent, CollectionObject, TaxonDetermination
from app.models.base import _utcnow
import app.services.events as ev_svc
import app.services.explore as ex
from tests.helpers import ensure_repo


def _taxon(session, name, rank, parent=None, auth=""):
    t = Taxon(scientific_name=name, taxon_rank=rank, scientific_name_authorship=auth,
              parent_name_usage_id=(parent.id if parent else None),
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    return t


def _specimen(session, taxon, event, catalog):
    co = CollectionObject(catalog_number=catalog, repository_id=ensure_repo(session, "Doe"),
                          individual_count=1,
                          collecting_event_id=event.id, created_at=_utcnow(), updated_at=_utcnow())
    session.add(co); session.flush()
    session.add(TaxonDetermination(collection_object_id=co.id, taxon_id=taxon.id,
                                   is_current=1, created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    return co


def _fixture(session):
    fam = _taxon(session, "Curculionidae", "family")
    gen = _taxon(session, "Otiorhynchus", "genus", parent=fam)
    sp1 = _taxon(session, "Otiorhynchus sulcatus", "species", parent=gen)
    sp2 = _taxon(session, "Otiorhynchus iratus", "species", parent=gen)
    de = ev_svc.create_collecting_event(session, country="Germany", state_province="Bavaria", locality="Watzmann")
    at = ev_svc.create_collecting_event(session, country="Austria", state_province="Tyrol", locality="Innsbruck")
    session.flush()
    _specimen(session, sp1, de, "A1")
    _specimen(session, sp1, de, "A2")
    _specimen(session, sp2, at, "A3")
    return locals()


def test_search_facets_tags_by_source(session):
    _fixture(session)
    by_kind = {(f.kind, f.label): f for f in ex.search_facets(session, "Otio")}
    assert any(k[0] == "taxon" for k in by_kind)
    g = next(f for f in ex.search_facets(session, "Germany") if f.kind == "country")
    assert g.tag == "Country"


def test_checklist_groups_species_under_genus_in_order(session):
    _fixture(session)
    groups = ex.checklist(session)
    assert len(groups) == 1                       # one genus group
    g = groups[0]
    assert g.headers[-1][0] == "genus"            # last header is the genus
    assert g.headers[-1][1].startswith("Otiorhynchus")
    labels = [sp.label for sp in g.species]
    assert labels == sorted(labels)               # species A→Z within the genus
    counts = {sp.label.split()[1]: sp.count for sp in g.species}
    assert counts["sulcatus"] == 2 and counts["iratus"] == 1


def test_country_facet_filters_checklist_and_counts(session):
    f = _fixture(session)
    at_id = ex.search_facets(session, "Austria")[0].key   # the Austria country facet
    flt = [{"kind": "country", "key": at_id}]
    c = ex.counts(session, flt)
    assert c["specimens"] == 1 and c["events"] == 1
    groups = ex.checklist(session, flt)
    labels = [sp.label for g in groups for sp in g.species]
    assert any("iratus" in l for l in labels) and not any("sulcatus" in l for l in labels)


def test_events_axis_groups_specimens(session):
    _fixture(session)
    evs = ex.events(session)
    assert len(evs) == 2
    assert {g.n_specimens for g in evs} == {1, 2}


def test_identical_event_and_assoc_lots_collapse(session):
    from app.models import BiologicalRelationship, BiologicalAssociation
    f = _fixture(session)
    sp1, de = f["sp1"], f["de"]
    # sp1 already has 2 specimens at event `de`, no associations → one collapsed group.
    groups = ex.checklist(session)
    sulc = next(s for g in groups for s in g.species if "sulcatus" in s.label)
    assert sulc.count == 2
    assert len(sulc.lot_groups) == 1 and sulc.lot_groups[0].count == 2

    # Add a third specimen at the SAME event but with a host association → its own group.
    co = _specimen(session, sp1, de, "A4")
    rel = BiologicalRelationship(name="collected on", created_at=_utcnow(), updated_at=_utcnow())
    session.add(rel); session.flush()
    host = _taxon(session, "Quercus robur", "species")
    session.add(BiologicalAssociation(
        subject_collection_object_id=co.id, biological_relationship_id=rel.id,
        object_taxon_id=host.id, created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    groups = ex.checklist(session)
    sulc = next(s for g in groups for s in g.species if "sulcatus" in s.label)
    assert sulc.count == 3                       # 3 specimens total
    assert len(sulc.lot_groups) == 2             # but two distinct (event+assoc) lots


def test_csv_has_header_and_rows(session):
    _fixture(session)
    text = ex.to_csv(ex.query_specimens(session)).decode()
    assert text.splitlines()[0].startswith("id,catalogNumber")
    assert len(text.strip().splitlines()) == 1 + 3   # header + 3 specimens

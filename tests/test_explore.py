"""Explore service — faceted search, drawer-order checklist, filtering, CSV (#40)."""
from app.models import Taxon, CollectingEvent, CollectionObject, TaxonDetermination, Person
from app.models.base import _utcnow
import app.services.events as ev_svc
import app.services.explore as ex
from tests.helpers import ensure_repo


def _person(session, name):
    p = Person(full_name=name, created_at=_utcnow(), updated_at=_utcnow())
    session.add(p); session.flush()
    return p


def _taxon(session, name, rank, parent=None, auth=""):
    t = Taxon(scientific_name=name, taxon_rank=rank, scientific_name_authorship=auth,
              nomenclatural_code="ICZN",
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


def test_species_group_count_excludes_genus_level(session):
    """#135: the headline taxon figure counts species-group names (species/subspecies)
    only — a specimen determined merely to genus must not inflate it."""
    f = _fixture(session)                             # 3 specimens, 2 species
    gen = f["gen"]
    de = f["de"]
    _specimen(session, gen, de, "A4")                 # a genus-level determination
    c = ex.counts(session)
    assert c["specimens"] == 4                         # all four specimens counted
    assert c["species_group"] == 2                     # but only the two species


def test_collection_facet_filters_by_repository(session):
    """#135: typing a collection code surfaces it as a facet and filters to its
    specimens only."""
    f = _fixture(session)
    other = ensure_repo(session, "ZMB")                 # a second collection
    co = CollectionObject(catalog_number="Z1", repository_id=other, individual_count=1,
                          collecting_event_id=f["de"].id, created_at=_utcnow(), updated_at=_utcnow())
    session.add(co); session.flush()
    session.add(TaxonDetermination(collection_object_id=co.id, taxon_id=f["sp1"].id,
                                   is_current=1, created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()

    facets = ex.search_facets(session, "ZMB")
    coll = next(f for f in facets if f.kind == "collection")
    assert coll.tag == "Collection"
    flt = [{"kind": "collection", "key": coll.key}]
    c = ex.counts(session, flt)
    assert c["specimens"] == 1                          # only the ZMB specimen
    cats = {r.catalog for r in ex.query_specimens(session, flt)}
    assert cats == {"Z1"}


def _dated_specimen(session, taxon, event, catalog, date_identified):
    co = _specimen(session, taxon, event, catalog)
    det = session.query(TaxonDetermination).filter_by(collection_object_id=co.id).one()
    det.date_identified = date_identified
    session.flush()
    return co


def test_dashboard_timelines_accumulation_and_phenology(session):
    """#135: collecting/identification timelines, species-accumulation curves, and
    a month-of-year phenology histogram over the current filter set."""
    fam = _taxon(session, "Curculionidae", "family")
    gen = _taxon(session, "Otiorhynchus", "genus", parent=fam)
    sp1 = _taxon(session, "Otiorhynchus sulcatus", "species", parent=gen)
    sp2 = _taxon(session, "Otiorhynchus iratus", "species", parent=gen)
    e2019 = ev_svc.create_collecting_event(session, country="Germany", locality="A", event_date="2019-06-15")
    e2021 = ev_svc.create_collecting_event(session, country="Germany", locality="B", event_date="2021-08-02")
    session.flush()
    _dated_specimen(session, sp1, e2019, "A1", "2020-01-10")
    _dated_specimen(session, sp1, e2021, "A2", "2020-01-11")   # same species, later collect
    _dated_specimen(session, sp2, e2021, "A3", "2022-03-04")   # new species in 2021/2022

    d = ex.dashboard(session)
    assert d.total == 3
    # collecting timeline: 2019→1, 2020→0 (gap filled), 2021→2
    assert d.collected_by_year == [(2019, 1), (2020, 0), (2021, 2)]
    # identification timeline: 2020→2, 2021→0, 2022→1
    assert d.identified_by_year == [(2020, 2), (2021, 0), (2022, 1)]
    # accumulation by collecting date: sp1 first in 2019, sp2 first in 2021
    assert d.accum_collected == [(2019, 1), (2021, 2)]
    # accumulation by identification date: sp1 first in 2020, sp2 first in 2022
    assert d.accum_identified == [(2020, 1), (2022, 2)]
    # phenology: June (idx 5) has 1, August (idx 7) has 2
    assert d.phenology[5] == 1 and d.phenology[7] == 2 and sum(d.phenology) == 3
    assert d.undated_collected == 0 and d.undated_identified == 0


def test_dashboard_counts_undated_and_top_hosts(session):
    """Undated specimens are tallied separately (not silently dropped); genus-level
    determinations never enter the accumulation curve."""
    fam = _taxon(session, "Curculionidae", "family")
    gen = _taxon(session, "Otiorhynchus", "genus", parent=fam)
    e = ev_svc.create_collecting_event(session, country="Germany", locality="X")   # no date
    session.flush()
    _specimen(session, gen, e, "A1")            # genus-level, undated
    d = ex.dashboard(session)
    assert d.total == 1
    assert d.undated_collected == 1 and d.undated_identified == 1
    assert d.accum_collected == [] and d.accum_identified == []   # genus excluded


def test_person_appears_in_both_roles_and_each_filters(session):
    """#135: a person searched shows up as both 'Collector' and 'identified by';
    each role filters its own column."""
    fam = _taxon(session, "Curculionidae", "family")
    gen = _taxon(session, "Otiorhynchus", "genus", parent=fam)
    sp = _taxon(session, "Otiorhynchus sulcatus", "species", parent=gen)
    jakob = _person(session, "Jakob Jilg")
    ludger = _person(session, "Ludger Schmidt")
    # collected by Jakob, identified by Ludger
    ev = ev_svc.create_collecting_event(session, country="Germany", locality="X",
                                        recorded_by_id=jakob.id)
    session.flush()
    co = _specimen(session, sp, ev, "A1")
    det = session.query(TaxonDetermination).filter_by(collection_object_id=co.id).one()
    det.identified_by_id = ludger.id
    session.flush()

    facets = ex.search_facets(session, "Jakob")
    tags = {f.tag for f in facets if f.kind in ("collector", "identified_by")}
    assert tags == {"Collector", "identified by"}

    # Jakob as Collector → matches; Jakob as identified-by → does not (Ludger did)
    assert ex.counts(session, [{"kind": "collector", "key": "Jakob Jilg"}])["specimens"] == 1
    assert ex.counts(session, [{"kind": "identified_by", "key": "Jakob Jilg"}])["specimens"] == 0
    assert ex.counts(session, [{"kind": "identified_by", "key": "Ludger Schmidt"}])["specimens"] == 1


def test_and_vs_or_combine(session):
    """#135: different-kind facets combine by AND (default) or OR (union)."""
    fam = _taxon(session, "Curculionidae", "family")
    gen = _taxon(session, "Otiorhynchus", "genus", parent=fam)
    sp = _taxon(session, "Otiorhynchus sulcatus", "species", parent=gen)
    jakob = _person(session, "Jakob Jilg")
    de = ev_svc.create_collecting_event(session, country="Germany", locality="X",
                                        recorded_by_id=jakob.id)
    at = ev_svc.create_collecting_event(session, country="Austria", locality="Y")   # no collector
    session.flush()
    _specimen(session, sp, de, "A1")   # Germany + Jakob
    _specimen(session, sp, at, "A2")   # Austria, no collector

    at_id = ex.search_facets(session, "Austria")[0].key
    flt = [{"kind": "collector", "key": "Jakob Jilg"}, {"kind": "country", "key": at_id}]
    # AND: collected by Jakob AND in Austria → none
    assert ex.counts(session, flt, combine="and")["specimens"] == 0
    # OR: collected by Jakob OR in Austria → both specimens
    assert ex.counts(session, flt, combine="or")["specimens"] == 2


def test_and_across_two_taxa_is_intersection(session):
    """#135: two taxa of disjoint subtrees under AND → 0 (a determination has one
    taxon, so it can't be in both). Under OR → the union."""
    cara = _taxon(session, "Carabidae", "family")
    curc = _taxon(session, "Curculionidae", "family")
    cg = _taxon(session, "Carabus", "genus", parent=cara)
    og = _taxon(session, "Otiorhynchus", "genus", parent=curc)
    csp = _taxon(session, "Carabus granulatus", "species", parent=cg)
    osp = _taxon(session, "Otiorhynchus sulcatus", "species", parent=og)
    ev = ev_svc.create_collecting_event(session, country="Germany", locality="X")
    session.flush()
    _specimen(session, csp, ev, "A1")   # a carabid
    _specimen(session, osp, ev, "A2")   # a curculionid

    flt = [{"kind": "taxon", "key": cara.id}, {"kind": "taxon", "key": curc.id}]
    assert ex.counts(session, flt, combine="and")["specimens"] == 0
    assert ex.counts(session, flt, combine="or")["specimens"] == 2


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

"""Batch tools: collection-scoped fetch/match + bulk apply (#78).

The load-bearing property is the **collection scope**: a taxon fetch or a bulk apply
must never reach a specimen in another collection. These tests pin that down.
"""
import pytest

from app.models import CollectionObject, Taxon, TaxonDetermination
from app.models.disposition import Disposition
from app.models.base import _utcnow
import app.services.batch_ops as batch
from tests.helpers import ensure_repo


def _taxon(session, name, rank, parent=None):
    t = Taxon(scientific_name=name, taxon_rank=rank, nomenclatural_code="ICZN",
              parent_name_usage_id=(parent.id if parent else None),
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    return t


def _specimen(session, repo_id, catalog, taxon):
    co = CollectionObject(catalog_number=catalog, repository_id=repo_id)
    session.add(co); session.flush()
    d = TaxonDetermination(collection_object_id=co.id, taxon_id=taxon.id,
                           is_current=1, created_at=_utcnow(), updated_at=_utcnow())
    session.add(d); session.flush()
    return co


@pytest.fixture
def world(session):
    home = ensure_repo(session, "HOME")
    other = ensure_repo(session, "OTHER")
    genus = _taxon(session, "Sitona", "genus")
    sp = _taxon(session, "Sitona oblongulus", "species", parent=genus)
    carabus = _taxon(session, "Carabus", "genus")
    s1 = _specimen(session, home, "HOME-00001", sp)
    s2 = _specimen(session, home, "HOME-00002", sp)
    s3 = _specimen(session, other, "OTHER-00001", sp)   # foreign: same taxon, other collection
    s4 = _specimen(session, home, "HOME-00003", carabus)
    return dict(home=home, other=other, genus=genus, sp=sp,
                s1=s1, s2=s2, s3=s3, s4=s4)


def test_fetch_by_taxon_is_collection_scoped(session, world):
    got = batch.fetch_by_taxon(session, repository_id=world["home"], taxon_id=world["sp"].id)
    cats = {m.catalog for m in got}
    assert cats == {"HOME-00001", "HOME-00002"}      # the OTHER collection specimen excluded
    assert "OTHER-00001" not in cats


def test_fetch_by_taxon_includes_descendants(session, world):
    # Fetching the genus pulls its species' specimens, still scoped to HOME.
    got = batch.fetch_by_taxon(session, repository_id=world["home"], taxon_id=world["genus"].id)
    assert {m.catalog for m in got} == {"HOME-00001", "HOME-00002"}


def test_match_catalog_numbers_classifies(session, world):
    res = batch.match_catalog_numbers(
        session, repository_id=world["home"],
        numbers=["HOME-00001", "OTHER-00001", "NOPE-99"])
    assert {m.catalog for m in res.matched} == {"HOME-00001"}
    assert [f.catalog for f in res.foreign] == ["OTHER-00001"]
    assert res.foreign[0].collection_code == "OTHER"
    assert res.not_found == ["NOPE-99"]


def test_parse_catalog_numbers_dedupes_and_splits():
    assert batch.parse_catalog_numbers("a-1, a-2\n a-1;a-3  a-2") == ["a-1", "a-2", "a-3"]


def test_apply_disposition(session, world):
    disp = Disposition(name="box 12", created_at=_utcnow(), updated_at=_utcnow())
    session.add(disp); session.flush()
    n = batch.apply_disposition(
        session, source_repository_id=world["home"],
        co_ids=[world["s1"].id, world["s2"].id], disposition_id=disp.id)
    assert n == 2
    assert session.get(CollectionObject, world["s1"].id).disposition_id == disp.id


def test_apply_repository_moves_and_keeps_catalog(session, world):
    n = batch.apply_repository(
        session, source_repository_id=world["home"],
        co_ids=[world["s1"].id], target_repository_id=world["other"])
    assert n == 1
    moved = session.get(CollectionObject, world["s1"].id)
    assert moved.repository_id == world["other"]
    assert moved.catalog_number == "HOME-00001"        # immutable — prefix stays


def test_apply_refuses_cross_collection_specimen(session, world):
    # s3 belongs to OTHER; a bulk op scoped to HOME must refuse it.
    with pytest.raises(ValueError, match="cross-collection"):
        batch.apply_disposition(
            session, source_repository_id=world["home"],
            co_ids=[world["s1"].id, world["s3"].id], disposition_id=None)


def test_apply_repository_refuses_same_target(session, world):
    with pytest.raises(ValueError, match="same"):
        batch.apply_repository(
            session, source_repository_id=world["home"],
            co_ids=[world["s1"].id], target_repository_id=world["home"])

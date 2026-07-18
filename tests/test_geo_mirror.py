"""geo_mirror — the QGIS GeoPackage mirror (data/geo/collection.gpkg + starter .qgz).

The mirror is a separate GeoPackage QGIS reads (never the live DB). The specimens layer is
overwritten in place on each rebuild; the starter project is written once and never clobbered.
"""
import zipfile

import geopandas as gpd
import pytest

from app.models import Taxon
from app.models.base import _utcnow
from app.services import geo_mirror
from app.services.events import create_collecting_event
from app.services.specimens import create_collection_object, create_determination
from app.services.taxa import compose_scientific_name
from tests.helpers import ensure_repo


@pytest.fixture(autouse=True)
def _geo_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(geo_mirror, "geo_dir", lambda: tmp_path)
    return tmp_path


def _taxon(session, genus_el, sp_el, auth=None):
    g = Taxon(name_element=genus_el, scientific_name=genus_el, taxon_rank="genus",
              nomenclatural_code="ICZN", created_at=_utcnow(), updated_at=_utcnow())
    session.add(g); session.flush()
    t = Taxon(name_element=sp_el, scientific_name=sp_el, taxon_rank="species",
              parent_name_usage_id=g.id, scientific_name_authorship=auth,
              nomenclatural_code="ICZN", created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    t.scientific_name = compose_scientific_name(session, t); session.flush()
    return t


def test_build_gpkg_writes_only_coord_specimens(session):
    repo = ensure_repo(session, "Doe")
    t = _taxon(session, "Otiorhynchus", "armadillo", "(Rossi, 1792)")
    ev = create_collecting_event(session, locality="Bodenmoos", event_date="2026-07-16",
                                 decimal_latitude="50.44", decimal_longitude="9.98",
                                 coordinate_uncertainty_in_meters="50")
    co = create_collection_object(session, collecting_event_id=ev.id,
                                  catalog_number="aa01", repository_id=repo)
    create_determination(session, collection_object_id=co.id, taxon_id=t.id,
                         verbatim_identification="Otiorhynchus armadillo")
    # an event WITHOUT coordinates → its specimen is skipped
    ev2 = create_collecting_event(session, locality="no coords", event_date="2026-07-17")
    create_collection_object(session, collecting_event_id=ev2.id,
                             catalog_number="aa02", repository_id=repo)
    session.flush()

    p = geo_mirror.build_gpkg(session)
    gdf = gpd.read_file(p, layer="specimens")
    assert len(gdf) == 1
    row = gdf.iloc[0]
    assert row["catalog_number"] == "aa01"
    assert row["taxon"] == "Otiorhynchus armadillo (Rossi, 1792)"
    assert abs(row.geometry.x - 9.98) < 1e-6 and abs(row.geometry.y - 50.44) < 1e-6
    assert str(gdf.crs).upper().endswith("4326")


def test_build_gpkg_overwrites_in_place(session):
    repo = ensure_repo(session, "Doe")
    ev = create_collecting_event(session, decimal_latitude="50.0", decimal_longitude="9.0")
    create_collection_object(session, collecting_event_id=ev.id, catalog_number="aa01",
                             repository_id=repo)
    session.flush()
    p = geo_mirror.build_gpkg(session); ino1 = p.stat().st_ino
    ev2 = create_collecting_event(session, decimal_latitude="51.0", decimal_longitude="10.0")
    create_collection_object(session, collecting_event_id=ev2.id, catalog_number="aa02",
                             repository_id=repo)
    session.flush()
    geo_mirror.build_gpkg(session); ino2 = p.stat().st_ino
    gdf = gpd.read_file(p, layer="specimens")
    assert len(gdf) == 2 and ino1 == ino2       # grew, same file (QGIS auto-refresh works)


def test_empty_db_writes_valid_empty_layer(session):
    p = geo_mirror.build_gpkg(session)
    gdf = gpd.read_file(p, layer="specimens")
    assert len(gdf) == 0


def test_starter_qgz_written_once_never_overwritten(session):
    p = geo_mirror.ensure_starter_qgz()
    assert p.exists()
    with zipfile.ZipFile(p) as z:
        assert "collection.qgs" in z.namelist()
        xml = z.read("collection.qgs").decode()
    assert "collection.gpkg" in xml and "EPSG:4326" in xml and 'autoRefreshMode="ReloadData"' in xml
    # a user edit must survive a later ensure_starter_qgz()
    p.write_bytes(b"USER EDIT")
    geo_mirror.ensure_starter_qgz()
    assert p.read_bytes() == b"USER EDIT"

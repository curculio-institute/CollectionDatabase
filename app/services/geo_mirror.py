"""Geo mirror — a GeoPackage of the collection's specimens, kept in sync for QGIS.

The live DB is SQLite; a GeoPackage is *also* SQLite plus a spatial schema, so this is a
mirror in the same family. It lives in ``data/geo/`` (its own subdir, alongside
``data/media/`` and ``data/snapshots/``). QGIS reads the mirror (``collection.gpkg``) —
never the live DB — so the source is never at risk and a read-only auto-refresh in QGIS just
repaints.

- **``collection.gpkg``** is rewritten on every save (coalesced by the UI). The specimens
  layer is overwritten **in place** (same file, same inode — verified), so a QGIS layer with
  auto-refresh on picks up new points without re-opening the file.
- **``collection.qgz``** — a starter QGIS project — is written **once** and then never
  overwritten, so the user can restyle it in QGIS and keep their version. (If the generated
  starter doesn't suit, deleting it regenerates a fresh one; editing it in QGIS is preserved.)

Only specimens whose collecting event has coordinates are plotted; specimens sharing an event
land on the same point (QGIS styles the overlap).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import geo_dir

LAYER = "specimens"


def gpkg_path() -> Path:
    return geo_dir() / "collection.gpkg"


def qgz_path() -> Path:
    return geo_dir() / "collection.qgz"


# ── the specimen point projection ───────────────────────────────────────────────────

# Attribute columns written to the layer (order fixed so the schema is stable across rebuilds).
_COLS = ["co_id", "catalog_number", "scientific_name", "authorship", "taxon",
         "event_date", "locality", "recorded_by", "uncertainty_m"]


def _specimen_rows(session: Session) -> list[dict]:
    """One row per specimen that has coordinates (via its collecting event)."""
    from app.models import CollectionObject
    from app.services.taxa import format_scientific_name
    rows: list[dict] = []
    for co in session.query(CollectionObject).all():
        ev = co.collecting_event
        if ev is None or ev.decimal_latitude is None or ev.decimal_longitude is None:
            continue
        det = next((d for d in co.determinations if d.is_current), None)
        taxon = det.taxon if det else None
        rows.append({
            "co_id":           co.id,
            "catalog_number":  co.catalog_number,
            "scientific_name": (taxon.scientific_name if taxon else None),
            "authorship":      (taxon.scientific_name_authorship if taxon else None),
            "taxon":           (format_scientific_name(taxon) if taxon else None),
            "event_date":      ev.event_date,
            "locality":        ev.locality,
            "recorded_by":     (ev.recorded_by_person.full_name if ev.recorded_by_person else None),
            "uncertainty_m":   ev.coordinate_uncertainty_in_meters,
            "_lon":            ev.decimal_longitude,
            "_lat":            ev.decimal_latitude,
        })
    return rows


def build_gpkg(session: Session) -> Path:
    """(Re)write the specimens layer of ``data/geo/collection.gpkg`` from the DB (EPSG:4326).

    Overwrites the layer **in place** — same file, so a QGIS layer already pointed at it and
    set to auto-refresh repaints without re-opening. Returns the gpkg path."""
    import geopandas as gpd
    from shapely.geometry import Point

    rows = _specimen_rows(session)
    data = {c: [r[c] for r in rows] for c in _COLS}
    geom = [Point(r["_lon"], r["_lat"]) for r in rows]      # empty list → empty layer (still valid)
    gdf = gpd.GeoDataFrame(data, geometry=geom, crs="EPSG:4326")
    path = gpkg_path()
    gdf.to_file(path, layer=LAYER, driver="GPKG")
    return path


def ensure_starter_qgz() -> Path:
    """Write a starter QGIS project (``collection.qgz``) **only if it does not exist**.

    Never overwrites an existing one — the user may have restyled it in QGIS. The project
    points at ``./collection.gpkg`` (relative) with the specimens layer set to auto-refresh,
    so opening it shows the collection and repaints as you digitize. Delete the file to
    regenerate a fresh starter."""
    path = qgz_path()
    if path.exists():
        return path
    qgs = _starter_qgs_xml()
    # A .qgz is a zip containing "<projectname>.qgs".
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("collection.qgs", qgs)
    return path


def refresh(session_factory) -> Path:
    """Rebuild the gpkg mirror and make sure the starter project exists. The UI calls this
    (coalesced) after a save. Never raises into the caller's save path — mirror trouble must
    not fail a specimen save; it logs and returns the path."""
    import logging
    with session_factory() as s:
        p = build_gpkg(s)
    try:
        ensure_starter_qgz()
    except Exception:                                        # noqa: BLE001
        logging.getLogger(__name__).exception("geo mirror: starter .qgz write failed")
    return p


# ── the starter QGIS project XML ─────────────────────────────────────────────────────
# A deliberately minimal QGIS 3.x project: one OGR vector layer on the relative gpkg, a
# simple green marker, WGS84, and per-layer auto-refresh (ReloadData every 10 s). Kept small
# on purpose — it is a *starter* the user restyles and re-saves (and we never overwrite it).

_WGS84_SRS = (
    "<spatialrefsys>"
    "<proj4>+proj=longlat +datum=WGS84 +no_defs</proj4>"
    "<srsid>3452</srsid><srid>4326</srid><authid>EPSG:4326</authid>"
    "<description>WGS 84</description>"
    "<projectionacronym>longlat</projectionacronym>"
    "<ellipsoidacronym>EPSG:7030</ellipsoidacronym>"
    "<geographicflag>true</geographicflag>"
    "</spatialrefsys>"
)


def _starter_qgs_xml(layer_id: str = "specimens_layer") -> str:
    src = "./collection.gpkg|layername=specimens"
    return (
        "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
        '<qgis projectname="Collection map" version="3.34.0">'
        "<homePath path=\"\"/>"
        "<title>Collection map</title>"
        f"<projectCrs>{_WGS84_SRS}</projectCrs>"
        "<layer-tree-group>"
        f'<layer-tree-layer id="{layer_id}" name="specimens" source="{src}" '
        'providerKey="ogr" checked="Qt::Checked" expanded="1"><customproperties/>'
        "</layer-tree-layer>"
        "</layer-tree-group>"
        '<mapcanvas name="theMapCanvas">'
        f"<destinationsrs>{_WGS84_SRS}</destinationsrs>"
        "</mapcanvas>"
        "<projectlayers>"
        '<maplayer type="vector" geometry="Point" wkbType="Point" '
        'autoRefreshTime="10000" autoRefreshMode="ReloadData" '
        'hasScaleBasedVisibilityFlag="0">'
        f"<id>{layer_id}</id>"
        f"<datasource>{src}</datasource>"
        "<layername>specimens</layername>"
        f"<srs>{_WGS84_SRS}</srs>"
        "<provider>ogr</provider>"
        '<renderer-v2 type="singleSymbol">'
        '<symbols><symbol type="marker" name="0">'
        '<layer class="SimpleMarker">'
        '<Option type="Map">'
        '<Option type="QString" name="color" value="34,139,34,255"/>'
        '<Option type="QString" name="outline_color" value="0,60,0,255"/>'
        '<Option type="QString" name="size" value="2.6"/>'
        '<Option type="QString" name="name" value="circle"/>'
        "</Option>"
        "</layer>"
        "</symbol></symbols>"
        "</renderer-v2>"
        "</maplayer>"
        "</projectlayers>"
        "</qgis>"
    )

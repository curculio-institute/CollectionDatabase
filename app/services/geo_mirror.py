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


# A marker in the generated project's XML. While it is present the file is still *our*
# untouched starter and we may regenerate it (e.g. to add basemaps). The moment the user
# opens it in QGIS and **saves**, QGIS rewrites the whole document and the marker is gone —
# from then on it is the user's project and we never touch it again.
_STARTER_MARKER = "<!--collection-geo-starter-->"


def _is_unmodified_starter(path: Path) -> bool:
    """True if `path` is a QGIS project we generated and the user has not saved over."""
    try:
        with zipfile.ZipFile(path) as z:
            qgs = next((n for n in z.namelist() if n.endswith(".qgs")), None)
            if qgs is None:
                return False
            return _STARTER_MARKER in z.read(qgs).decode("utf-8", "replace")
    except Exception:                                        # noqa: BLE001 — not a readable zip
        return False


def ensure_starter_qgz() -> Path:
    """Write / update the starter QGIS project (``collection.qgz``).

    Written if absent; **regenerated** while it is still our unmodified starter (so template
    improvements like basemaps reach it); and **never overwritten** once the user has saved it
    in QGIS (the marker is then gone). Delete the file to force a fresh starter."""
    path = qgz_path()
    if path.exists() and not _is_unmodified_starter(path):
        return path                                          # user's project — leave it
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
# A minimal QGIS 3.x project: the OGR vector layer on the relative gpkg (green markers,
# auto-refresh ReloadData every 10 s) over XYZ basemaps — OpenStreetMap visible, OpenTopoMap
# and Esri World Imagery available but off. The canvas is Web Mercator (EPSG:3857) so the
# tiles render natively; the points are stored in 4326 and QGIS reprojects them on the fly.
# Kept small on purpose — it is a *starter* the user restyles and re-saves (never overwritten).

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

_MERC_SRS = (
    "<spatialrefsys>"
    "<proj4>+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 "
    "+units=m +nadgrids=@null +wktext +no_defs</proj4>"
    "<srsid>3857</srsid><srid>3857</srid><authid>EPSG:3857</authid>"
    "<description>WGS 84 / Pseudo-Mercator</description>"
    "<projectionacronym>merc</projectionacronym>"
    "<ellipsoidacronym>WGS84</ellipsoidacronym>"
    "<geographicflag>false</geographicflag>"
    "</spatialrefsys>"
)

# (layer id, display name, XYZ tile URL, visible-by-default). No API keys required.
# Chosen for a naturalist's context — terrain, relief and land cover over street reference —
# since substrate/elevation drive beetle distribution. OpenTopoMap (contours + relief) is the
# default backdrop; true bedrock geology needs a WMS (see the module note / follow-up).
_BASEMAPS = [
    ("opentopo_basemap", "OpenTopoMap",
     "https://a.tile.opentopomap.org/{z}/{x}/{y}.png", True),
    ("osm_basemap", "OpenStreetMap",
     "https://tile.openstreetmap.org/{z}/{x}/{y}.png", False),
    ("esri_hillshade_basemap", "Esri World Hillshade (relief)",
     "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/"
     "MapServer/tile/{z}/{y}/{x}", False),
    ("esri_imagery_basemap", "Esri World Imagery",
     "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
     "tile/{z}/{y}/{x}", False),
]

_SPECIMENS_ID = "specimens_layer"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _xyz_datasource(url: str) -> str:
    enc = url.replace("{", "%7B").replace("}", "%7D")
    return f"type=xyz&url={enc}&zmax=19&zmin=0"


def _tree_layer(lid: str, name: str, source: str, provider: str, checked: bool) -> str:
    chk = "Qt::Checked" if checked else "Qt::Unchecked"
    return (f'<layer-tree-layer id="{lid}" name="{_esc(name)}" source="{_esc(source)}" '
            f'providerKey="{provider}" checked="{chk}" expanded="0"><customproperties/>'
            "</layer-tree-layer>")


def _xyz_maplayer(lid: str, name: str, url: str) -> str:
    return (
        '<maplayer type="raster" hasScaleBasedVisibilityFlag="0">'
        f"<id>{lid}</id>"
        f"<datasource>{_esc(_xyz_datasource(url))}</datasource>"
        f"<layername>{_esc(name)}</layername>"
        f"<srs>{_MERC_SRS}</srs>"
        "<provider>wms</provider>"
        "<pipe>"
        '<rasterrenderer type="singlebandcolordata" band="1" opacity="1" '
        'alphaBand="-1" nodataColor=""/>'
        "</pipe>"
        "<blendMode>0</blendMode>"
        "</maplayer>"
    )


def _starter_qgs_xml() -> str:
    src = "./collection.gpkg|layername=specimens"
    # Layer tree: specimens on top, basemaps beneath (drawn under the points).
    tree = _tree_layer(_SPECIMENS_ID, "specimens", src, "ogr", True)
    tree += "".join(
        _tree_layer(lid, name, _xyz_datasource(url), "wms", vis)
        for lid, name, url, vis in _BASEMAPS)
    basemap_layers = "".join(
        _xyz_maplayer(lid, name, url) for lid, name, url, _ in _BASEMAPS)
    return (
        "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
        '<qgis projectname="Collection map" version="3.34.0">'
        f"{_STARTER_MARKER}"
        "<homePath path=\"\"/>"
        "<title>Collection map</title>"
        f"<projectCrs>{_MERC_SRS}</projectCrs>"
        f"<layer-tree-group>{tree}</layer-tree-group>"
        '<mapcanvas name="theMapCanvas">'
        f"<destinationsrs>{_MERC_SRS}</destinationsrs>"
        "</mapcanvas>"
        "<projectlayers>"
        '<maplayer type="vector" geometry="Point" wkbType="Point" '
        'autoRefreshTime="10000" autoRefreshMode="ReloadData" '
        'hasScaleBasedVisibilityFlag="0">'
        f"<id>{_SPECIMENS_ID}</id>"
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
        f"{basemap_layers}"
        "</projectlayers>"
        "</qgis>"
    )

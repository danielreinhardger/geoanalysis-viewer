"""Client for the LUBW ArcGIS REST services at rips-gdi (folder `wfs`).

All geometry is requested in EPSG:25832 (ETRS89 / UTM 32N, metres), so no
projection code is needed. Results are returned as GeoJSON-like dicts.

Known server quirks (seen 13.11.2026):
  * `distance=0` produces an error -> omit the parameter when 0.
  * Large layers can return `exceededTransferLimit` -> paginate with resultOffset.
  * Field names differ per layer (OBJEKT, LANGNAME, BIOTOP, ...) -> keep all attributes.
"""
from __future__ import annotations

from .cache import HttpCache

BASE = "https://rips-gdi.lubw.baden-wuerttemberg.de/arcgis/rest/services/wfs/"
SRID = 25832

# Layers used so far. Add more from `list_services()`.
LAYERS = {
    "spa": "Vogelschutzgebiet_SPA",
    "ffh": "FFH_Gebiet",
    "nsg": "Naturschutzgebiet",
    "lsg": "Landschaftsschutzgebiet",
    "wsg": "Wasserschutzgebiet",
    "qsg": "Quellenschutzgebiet",
    "usg": "Ueberschwemmungsgebiet",
    "biotop": "Biotopkartierung",
    "naturpark": "Naturpark",
    "deponie": "Abfalldeponie",
}

# Most services expose their features as layer 0; exceptions are listed here.
LAYER_IDS = {"FFH_Maehwiese": 1}

NAME_FIELDS = ("OBJEKT", "LANGNAME", "BIOTOP", "NAME", "GEBIETSNAME", "BESCHREIBUNG", "FIRMA")


def list_services(cache: HttpCache) -> list[str]:
    r = cache.request(BASE, {"f": "json"}, layer="_services").json()
    return [s["name"].split("/", 1)[-1] for s in r.get("services", [])]


def _esri_to_geojson(geom: dict | None) -> dict | None:
    if not geom:
        return None
    if "rings" in geom:
        return {"type": "Polygon", "coordinates": geom["rings"]}  # even-odd: extra rings may be holes
    if "paths" in geom:
        return {"type": "MultiLineString", "coordinates": geom["paths"]}
    if "x" in geom:
        return {"type": "Point", "coordinates": [geom["x"], geom["y"]]}
    return None


def feature_name(props: dict) -> str:
    for f in NAME_FIELDS:
        if props.get(f):
            return str(props[f]).strip()
    return ""


def query(cache: HttpCache, layer: str, *, bbox: tuple[float, float, float, float] | None = None,
          polygon: list | None = None, distance_m: float = 0, where: str = "1=1",
          return_geometry: bool = True, page_size: int = 1000) -> dict:
    """Query one LUBW layer. Give either bbox (xmin,ymin,xmax,ymax in 25832) or polygon (ring list).

    Returns a FeatureCollection with `meta` (layer, retrieval times, from_cache, request count).
    """
    service = LAYERS.get(layer, layer)
    url = f"{BASE}{service}/MapServer/{LAYER_IDS.get(service, 0)}/query"
    params = {"f": "json", "where": where, "outFields": "*", "returnGeometry": str(return_geometry).lower(),
              "outSR": SRID, "inSR": SRID, "spatialRel": "esriSpatialRelIntersects"}
    if bbox:
        params.update(geometryType="esriGeometryEnvelope",
                      geometry={"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
                                "spatialReference": {"wkid": SRID}})
    elif polygon:
        params.update(geometryType="esriGeometryPolygon",
                      geometry={"rings": polygon, "spatialReference": {"wkid": SRID}})
    if distance_m and distance_m > 0:  # distance=0 breaks the server
        params.update(distance=distance_m, units="esriSRUnit_Meter")

    features, offset, meta_calls = [], 0, []
    while True:
        p = dict(params, resultOffset=offset, resultRecordCount=page_size)
        resp = cache.request(url, p, method="POST", layer=service)
        data = resp.json()
        meta_calls.append({"fetched_at": resp.fetched_at, "from_cache": resp.from_cache, "key": resp.key})
        if "error" in data:
            raise RuntimeError(f"{service}: {data['error']}")
        for f in data.get("features", []):
            props = f.get("attributes", {})
            features.append({"type": "Feature", "properties": dict(props, layer=service, _name=feature_name(props)),
                             "geometry": _esri_to_geojson(f.get("geometry"))})
        if not data.get("exceededTransferLimit"):
            break
        offset += page_size
    return {"type": "FeatureCollection", "crs": f"EPSG:{SRID}", "features": features,
            "meta": {"layer": service, "source": url, "requests": meta_calls}}

"""Client for the LGL-BW ALKIS WFS (official cadastre, open data) and the settlement distance.

Service: WFS 2.0 "WFS LGL-BW ALKIS", native EPSG:25832, GeoJSON output.
Licence (from the service's GetCapabilities, read 13.11.2026): Datenlizenz Deutschland -
Namensnennung - Version 2.0 (dl-de/by-2-0). Required attribution:
"LGL-BW (<year of retrieval>) Datenlizenz Deutschland - Namensnennung - Version 2.0, www.lgl-bw.de".

The server answers some bad requests with HTTP 200 and an HTML "Request Rejected" page, so
every response is validated as a GeoJSON FeatureCollection before it is cached.
"""
from __future__ import annotations

from . import geom, lgl_wfs
from .cache import HttpCache
from .lgl_wfs import CRS, LICENCE, attribution  # noqa: F401  (re-exported: callers use alkis.LICENCE etc.)

URL = "https://owsproxy.lgl-bw.de/owsproxy/wfs/WFS_LGL-BW_ALKIS"
LAND_USE = "nora:v_al_tatsaechliche_nutzung"
# ALKIS "Tatsächliche Nutzung" object types that count as settlement where people live.
# The names are matched against the `objektname` attribute the service delivers.
RESIDENTIAL = ("Wohnbaufläche",)
MIXED = ("Fläche gemischter Nutzung",)


BUILDINGS = "nora:v_al_gebaeude"
OTHER_BUILDINGS_WITHIN_M = 300.0  # non-residential buildings this close are listed for information


def is_residential_building(funktion: str | None) -> bool:
    """Rule on the delivered `gebaeudefunktion_name`: contains "wohn" (Wohnhaus, Wohnheim,
    Wohn- und Geschäftsgebäude, ...) or is a Bauernhaus or a care/children's home.
    Wochenendhaus does not count; such buildings are listed separately when close."""
    f = (funktion or "").lower()
    return "wohn" in f or f in ("bauernhaus", "seniorenheim", "altenheim", "pflegeheim", "kinderheim")


def query(cache: HttpCache, typename: str, bbox: tuple[float, float, float, float],
          page_size: int = 2000) -> dict:
    """All features of one ALKIS feature type that intersect the bbox (EPSG:25832), paged."""
    return lgl_wfs.query(cache, URL, typename, bbox, page_size)


def _polygons(g: dict | None) -> list[list]:
    """Ring lists of a GeoJSON Polygon or MultiPolygon."""
    if not g:
        return []
    if g["type"] == "Polygon":
        return [g["coordinates"]]
    if g["type"] == "MultiPolygon":
        return list(g["coordinates"])
    return []


def settlement_distance(site: dict, cache: HttpCache, radius_m: float = 1500.0) -> dict:
    """Distance from the site polygon to the nearest residential and mixed-use ALKIS areas.

    Every figure is polygon to polygon and exact for the given geometries; the site's
    tolerance_m still applies to it.
    """
    rings = site["geometry"]["coordinates"]
    tol = float(site["properties"].get("tolerance_m", 0))
    sb = geom.bbox(rings)
    box = (sb[0] - radius_m, sb[1] - radius_m, sb[2] + radius_m, sb[3] + radius_m)
    fc = query(cache, LAND_USE, box)
    rows = []
    for f in fc["features"]:
        p = f["properties"]
        kind = "wohnen" if p.get("objektname") in RESIDENTIAL else "gemischt" if p.get("objektname") in MIXED else None
        if not kind:
            continue
        polys = _polygons(f.get("geometry"))
        if not polys:
            continue
        d = min(geom.boundary_distance(rings, poly) for poly in polys)
        if d > radius_m:
            continue
        b = geom.bbox([r for poly in polys for r in poly])
        rows.append({"kind": kind, "objektname": p.get("objektname"), "unterart": p.get("unterart_name"),
                     "gml_id": p.get("gml_id"), "distance_m": round(d, 1),
                     "area_m2": round(sum(geom.polygon_area(poly) for poly in polys)),
                     "centre_utm32": [round((b[0] + b[2]) / 2), round((b[1] + b[3]) / 2)]})
    rows.sort(key=lambda r: r["distance_m"])
    nearest = {k: next((r for r in rows if r["kind"] == k), None) for k in ("wohnen", "gemischt")}

    bfc = query(cache, BUILDINGS, box)
    buildings = []
    other_near = []
    for f in bfc["features"]:
        p = f["properties"]
        polys = _polygons(f.get("geometry"))
        if not polys:
            continue
        d = min(geom.boundary_distance(rings, poly) for poly in polys)
        if d > radius_m:
            continue
        b = geom.bbox([r for poly in polys for r in poly])
        row = {"funktion": p.get("gebaeudefunktion_name"), "gml_id": p.get("gml_id"),
               "distance_m": round(d, 1), "adressen": p.get("adressen"),
               "centre_utm32": [round((b[0] + b[2]) / 2), round((b[1] + b[3]) / 2)]}
        if is_residential_building(p.get("gebaeudefunktion_name")):
            buildings.append(row)
        elif d <= OTHER_BUILDINGS_WITHIN_M:
            other_near.append(row)
    buildings.sort(key=lambda r: r["distance_m"])
    other_near.sort(key=lambda r: r["distance_m"])
    return {"site": site["properties"], "tolerance_m": tol, "radius_m": radius_m, "nearest": nearest,
            "results": rows, "features_in_box": len(fc["features"]), "source": fc["meta"],
            "buildings": buildings, "other_buildings_near": other_near, "buildings_in_box": len(bfc["features"]), "buildings_source": bfc["meta"]}


def to_markdown(rep: dict) -> str:
    s, m = rep["site"], rep["source"]
    out = [f"# Siedlungsabstand {s.get('name', s.get('id'))}", "",
           f"Abstand Fläche zu Fläche (Polygon zu Polygon, exakt gerechnet), EPSG:25832. "
           f"Toleranz der Standortfläche ±{rep['tolerance_m']} m ({s.get('tolerance_note', '')}). "
           f"Umkreis {rep['radius_m']:g} m.", ""]
    for kind, title in (("wohnen", "Nächste Wohnbaufläche"), ("gemischt", "Nächste Fläche gemischter Nutzung")):
        r = rep["nearest"][kind]
        out.append(f"- **{title}:** " + (f"{r['distance_m']} m ±{rep['tolerance_m']} m (ALKIS-Objekt {r['gml_id']})"
                                         if r else f"keine im Umkreis von {rep['radius_m']:g} m"))
    out += ["", "| Nutzung | Unterart | Abstand m | Fläche m² | Lage (UTM32 E, N) | ALKIS-Objekt |",
            "|---|---|---:|---:|---|---|"]
    for r in rep["results"][:25]:
        out.append(f"| {r['objektname']} | {r['unterart'] or ''} | {r['distance_m']} | {r['area_m2']} | "
                   f"{r['centre_utm32'][0]}, {r['centre_utm32'][1]} | {r['gml_id']} |")
    if len(rep["results"]) > 25:
        out.append(f"\n{len(rep['results']) - 25} weitere Flächen stehen in der JSON-Datei.")
    bm = rep["buildings_source"]
    out += ["", "## Nächste Wohngebäude", "",
            "Gebäude, deren ALKIS-Gebäudefunktion „wohn“ enthält (Wohnhaus, Wohnheim, Wohn- und "
            "Geschäftsgebäude usw.) oder Bauernhaus, Senioren-, Alten-, Pflege- oder Kinderheim lautet. "
            "Abstand Gebäudeumriss zu Standortfläche, jeweils ±" + f"{rep['tolerance_m']} m. "
            "Ob ein Gebäude tatsächlich bewohnt ist, steht nicht im Kataster.", "",
            "| Gebäudefunktion | Abstand m | Lage (UTM32 E, N) | ALKIS-Objekt |", "|---|---:|---|---|"]
    for r in rep["buildings"][:15]:
        out.append(f"| {r['funktion']} | {r['distance_m']} | {r['centre_utm32'][0]}, {r['centre_utm32'][1]} | "
                   f"{r['gml_id']} |")
    if not rep["buildings"]:
        out.append(f"| keine im Umkreis von {rep['radius_m']:g} m | | | |")
    elif len(rep["buildings"]) > 15:
        out.append(f"\n{len(rep['buildings']) - 15} weitere Wohngebäude stehen in der JSON-Datei.")
    out += ["", f"## Sonstige Gebäude bis {OTHER_BUILDINGS_WITHIN_M:g} m", "",
            "Gebäude ohne Wohnfunktion nach obiger Regel (z. B. Wochenendhaus, Wirtschaftsgebäude), zur Information.",
            "", "| Gebäudefunktion | Abstand m | Lage (UTM32 E, N) | ALKIS-Objekt |", "|---|---:|---|---|"]
    for r in rep["other_buildings_near"]:
        out.append(f"| {r['funktion']} | {r['distance_m']} | {r['centre_utm32'][0]}, {r['centre_utm32'][1]} | "
                   f"{r['gml_id']} |")
    if not rep["other_buildings_near"]:
        out.append("| keine | | | |")
    times = sorted({c["fetched_at"] for c in m["requests"]})
    btimes = sorted({c["fetched_at"] for c in bm["requests"]})
    cached = " (aus Cache)" if all(c["from_cache"] for c in m["requests"]) else ""
    bcached = " (aus Cache)" if all(c["from_cache"] for c in bm["requests"]) else ""
    out += ["", "## Quellen", "",
            f"- ALKIS Tatsächliche Nutzung, WFS `{m['layer']}` ({m['source']}), Abruf {', '.join(times)}{cached}",
            f"- ALKIS Gebäude, WFS `{bm['layer']}` ({bm['source']}), Abruf {', '.join(btimes)}{bcached}",
            f"- Quellenvermerk: {m['attribution']}",
            f"- Fläche: {s.get('source', '')}"]
    return "\n".join(out) + "\n"

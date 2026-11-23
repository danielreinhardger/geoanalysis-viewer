"""Site analysis: how does a site polygon relate to protected areas / features?

Rules (from experience, see CLAUDE.md):
  * Always use the site polygon, never a centre point.
  * Every overlap/distance is reported with the site's tolerance; values within
    tolerance are classified as "beruehrt (im Toleranzbereich)".
  * Every output carries source URLs and retrieval timestamps.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import geom, ripsgdi
from .cache import HttpCache


def load_site(path: str | Path) -> dict:
    f = json.loads(Path(path).read_text(encoding="utf-8"))
    if f.get("type") == "FeatureCollection":
        f = f["features"][0]
    return f


def classify(overlap_m2: float, depth: float, dist: float, tol: float) -> str:
    if overlap_m2 > 0 and depth > tol:
        return "ueberlappt"
    if overlap_m2 > 0 or dist <= tol:
        return "beruehrt (im Toleranzbereich)"
    return "getrennt"


def analyze_site(site: dict, layers: list[str], cache: HttpCache, radius_m: float = 1500,
                 res_m: float = 0.5) -> dict:
    rings = site["geometry"]["coordinates"]
    tol = float(site["properties"].get("tolerance_m", 0))
    sb = geom.bbox(rings)
    # query box = site bbox + radius; clip box = + radius + margin so artificial clip edges stay far away
    qbox = (sb[0] - radius_m, sb[1] - radius_m, sb[2] + radius_m, sb[3] + radius_m)
    margin = radius_m + 500
    cbox = (sb[0] - margin, sb[1] - margin, sb[2] + margin, sb[3] + margin)

    rows, sources = [], []
    for layer in layers:
        fc = ripsgdi.query(cache, layer, bbox=qbox)
        sources.append(fc["meta"])
        for f in fc["features"]:
            g = f["geometry"]
            if not g or g["type"] != "Polygon":
                continue
            clipped = [c for c in (geom.clip_ring_to_bbox(r, cbox) for r in g["coordinates"]) if c]
            if not clipped:
                continue
            ov = geom.overlap_stats(rings, clipped, res=res_m)
            dist = 0.0 if ov["overlap_m2"] > 0 else geom.boundary_distance(rings, clipped)
            if dist > radius_m:
                continue
            p = f["properties"]
            rows.append({
                "layer": p["layer"], "name": p.get("_name", ""),
                "typ": p.get("BIOTOPTYP_NATSCHG_GRUPPE") or p.get("ZONE") or p.get("SCHUTZZONE") or "",
                "overlap_m2": round(ov["overlap_m2"]), "depth_m": round(ov["depth_m"], 1),
                "distance_m": round(dist, 1),
                "status": classify(ov["overlap_m2"], ov["depth_m"], dist, tol),
            })
    rows.sort(key=lambda r: (r["distance_m"], -r["overlap_m2"]))
    return {
        "site": site["properties"], "site_area_ha": round(geom.polygon_area(rings) / 1e4, 2),
        "radius_m": radius_m, "sampling_res_m": res_m, "tolerance_m": tol,
        "overlap_method": "scanline (Zeilen exakt geschnitten, Zeilenabstand = sampling_res_m)",
        "distance_method": "exakt (Eckpunkt-Segment-Abstand)",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": rows, "sources": sources,
    }


def to_markdown(rep: dict) -> str:
    s = rep["site"]
    out = [f"# Analyse {s.get('name', s.get('id'))}", "",
           f"Fläche {rep['site_area_ha']} ha · Toleranz ±{rep['tolerance_m']} m ({s.get('tolerance_note', '')}) · "
           f"Flächenverschnitt zeilenweise, Zeilenabstand {rep['sampling_res_m']} m · Abstände exakt · Umkreis {rep['radius_m']} m · erstellt {rep['generated']}", "",
           "| Ebene | Name | Typ | Überlappung m² | Tiefe m | Abstand m | Einstufung |",
           "|---|---|---|---:|---:|---:|---|"]
    for r in rep["results"]:
        out.append(f"| {r['layer']} | {r['name']} | {r['typ']} | {r['overlap_m2']} | {r['depth_m']} | "
                   f"{r['distance_m']} | {r['status']} |")
    out += ["", "## Quellen", ""]
    for m in rep["sources"]:
        times = sorted({c["fetched_at"] for c in m["requests"]})
        cached = all(c["from_cache"] for c in m["requests"])
        out.append(f"- LUBW `{m['layer']}` ({m['source']}), Abruf {', '.join(times)}{' (aus Cache)' if cached else ''}")
    out.append(f"- Fläche: {s.get('source', '')}")
    return "\n".join(out) + "\n"

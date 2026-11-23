"""Web bundle for the three.js site viewer: terrain, aerial image, layers, analysis results.

`build()` writes a self-contained folder (default exports/<ID>_web/) that any static web
server can host:

  scene.json      site outline, layers (polygons, lines, points), analysis status per feature,
                  viewpoints, sources with retrieval dates, description of the other files
  terrain.bin     uint16 little-endian, cm above `terrain.z_min`, row 0 = southern edge
  ortho_<r>_<c>.jpg   DOP20 aerial image tiles (row 0 = northern row)
  index.html, viewer.js, viewer.css, vendor/   copied from viewer/ in the repo (three.js is bundled)

Coordinates in scene.json are local metres: x = East - origin[0], y = North - origin[1].
In three.js a point is (x, height, -y).

Nothing here is computed twice: overlap/distance figures come from reports/<ID>_analyse.json
and reports/<ID>_siedlungsabstand.json (run `analyze` and `settlement` first). The viewer
only draws them.

Pure standard library.
"""
from __future__ import annotations

import array
import json
import math
import shutil
import sys
import time
from pathlib import Path

from . import alkis, dgm, geom
from .cache import CacheMiss, HttpCache
from .singlefile import embedded, single_html  # noqa: F401  (moved; kept importable from here)

DOP_URL = "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C"
DOP_LAYER = "IMAGES_DOP_20_RGB"
DOP_TILE_PX = 2048  # largest tile edge requested from the WMS
RING_MPP = 15.0     # aerial image of the horizon ring (30 km), as requested from the WMS (cached)
RING_STORE_MPP = 20.0   # ... and as stored: seen at 1.5-15 km, where a 20-m pixel is below one screen pixel;
                        # keeps the colours, which AVIF at lower quality loses (docs/RENDERING.md, 21.11.2026)

# How each data/<ID>/<stem>.geojson is shown. Layers not listed here are left out
# (sample points of the geology queries, for instance). `on` = visible at start.
LAYER_STYLE = {
    "Naturschutzgebiet": {"label": "Naturschutzgebiet", "color": "#d7263d", "group": "Schutzgebiete", "on": True},
    "FFH_Gebiet": {"label": "FFH-Gebiet", "color": "#2e9e4f", "group": "Schutzgebiete", "on": True},
    "Vogelschutzgebiet_SPA": {"label": "Vogelschutzgebiet (SPA)", "color": "#f2a900", "group": "Schutzgebiete",
                              "on": True},
    "Landschaftsschutzgebiet": {"label": "Landschaftsschutzgebiet", "color": "#7cb518", "group": "Schutzgebiete",
                                "on": True},
    "Biotopkartierung": {"label": "Kartierte Biotope", "color": "#00a896", "group": "Schutzgebiete", "on": True},
    "Naturdenkmal_Flaechenhaft": {"label": "Naturdenkmal (flächenhaft)", "color": "#8e5572",
                                  "group": "Schutzgebiete", "on": True},
    "Wasserschutzgebiet": {"label": "Wasserschutzgebiet", "color": "#1f78d1", "group": "Wasser", "on": True},
    "Quellenschutzgebiet": {"label": "Quellenschutzgebiet", "color": "#5bc0eb", "group": "Wasser", "on": True},
    "Ueberschwemmungsgebiet": {"label": "Überschwemmungsgebiet", "color": "#3a0ca3", "group": "Wasser", "on": True},
    "Gewaessernetz_AWGN": {"label": "Gewässernetz (AWGN)", "color": "#4cc9f0", "group": "Wasser", "on": True},
    "Stehendes_Gewaesser_AWGN": {"label": "Stehende Gewässer", "color": "#4895ef", "group": "Wasser", "on": True},
    "Gewaessereinzugsgebiet_AWGN": {"label": "Gewässereinzugsgebiet", "color": "#90e0ef", "group": "Wasser",
                                    "on": False},
    "Oberflaechenwasserkoerper": {"label": "Oberflächenwasserkörper", "color": "#ade8f4", "group": "Wasser",
                                  "on": False},
}
# Left out on purpose: existing landfills (Abfalldeponie), which are not what this view is about, and the
# access-road model lines (zufahrt_*), which have their own reports and are no planning.
NAME_KEYS = ("_name", "titel", "OBJEKT", "LANGNAME", "BIOTOP", "GEW_NAME", "AS_NAME", "NAME", "name")
TYPE_KEYS = ("BIOTOPTYP_NATSCHG_GRUPPE", "SCHUTZSTATUS", "STATUS", "BESCHREIBUNG", "hinweis")


def _is_jpeg(body: bytes) -> bool:
    return body[:2] == b"\xff\xd8"


def encode_heights(grid: dgm.Grid) -> tuple[bytes, float, float]:
    """uint16 LE cm above the minimum; cells without data get the minimum. -> (bytes, z_min, z_max)"""
    vals = [v for v in grid.z if not math.isnan(v)]
    if not vals:
        raise ValueError("terrain grid holds no heights")
    z_min, z_max = min(vals), max(vals)
    if (z_max - z_min) * 100 > 65535:
        raise ValueError("height range exceeds 655 m; raise the quantisation step")
    out = array.array("H", (0 if math.isnan(v) else int(round((v - z_min) * 100)) for v in grid.z))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes(), z_min, z_max


CLIP_PAD_M = 20.0     # polygons are cut this far outside the bundle, so the cut edge is never drawn
SIMPLIFY_M = 0.5      # display only: far below the accuracy of the layers and the 1 m aerial image


def geometry_parts(g: dict | None, ox: float, oy: float, box) -> dict:
    """GeoJSON geometry -> {"polygons": [[ring, ...], ...], "lines": [[pt, ...], ...], "points": [pt, ...]}
    in local metres, rounded to 0.1 m. Parts whose bbox misses `box` (absolute coordinates) are dropped.
    For the view only: polygon rings are clipped to `box` plus CLIP_PAD_M, and rings and lines are
    simplified by SIMPLIFY_M. Overlap figures never come from these shapes (they come from the
    analysis report)."""
    clip = (box[0] - CLIP_PAD_M, box[1] - CLIP_PAD_M, box[2] + CLIP_PAD_M, box[3] + CLIP_PAD_M)
    out = {"polygons": [], "lines": [], "points": []}
    if not g:
        return out

    def loc(p):
        return [round(p[0] - ox, 1), round(p[1] - oy, 1)]

    def hits(pts):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return geom.bbox_intersects((min(xs), min(ys), max(xs), max(ys)), box)

    t, c = g["type"], g["coordinates"]
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    lines = [c] if t == "LineString" else c if t == "MultiLineString" else []
    points = [c] if t == "Point" else c if t == "MultiPoint" else []
    for rings in polys:
        if not rings or not hits(rings[0]):
            continue
        cut = []
        for i, r in enumerate(rings):
            c = geom.clip_ring_to_bbox(r, clip)
            if c is None:
                if i == 0:
                    break           # outer ring gone: nothing of this polygon is in view
                continue
            if c[-1] == c[0]:
                c = c[:-1]
            c = geom.simplify(c + [c[0]], SIMPLIFY_M)      # closed again: last point = first point
            if len(c) >= 4:
                cut.append([loc(p) for p in c])
            elif i == 0:
                break
        if cut and len(cut[0]) >= 4:
            out["polygons"].append(cut)
    for ln in lines:
        if len(ln) > 1 and hits(ln):
            out["lines"].append([loc(p) for p in geom.simplify(ln, SIMPLIFY_M)])
    for p in points:
        if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]:
            out["points"].append(loc(p))
    return out


def _first(props: dict, keys) -> str:
    for k in keys:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _status_index(analysis: dict | None) -> dict:
    """(layer, name) -> fields to attach to a feature.

    The analysis report identifies features by layer and name only. A name that occurs once
    gets status, overlap and distance. A name shared by several rows gets the status alone,
    and only if all rows agree; their figures cannot be assigned to one geometry."""
    rows: dict = {}
    for r in (analysis or {}).get("results", []):
        rows.setdefault((r["layer"], r.get("name", "")), []).append(r)
    idx = {}
    for k, rs in rows.items():
        if len(rs) == 1:
            idx[k] = {"status": rs[0]["status"], "overlap_m2": rs[0]["overlap_m2"], "distance_m": rs[0]["distance_m"]}
        elif len({r["status"] for r in rs}) == 1:
            idx[k] = {"status": rs[0]["status"]}
    return idx


def _layer_credit(stem: str, fc: dict) -> tuple[str, str | None, str]:
    """(attribution, date, kind of date) for the sources list. A file without a service URL in its
    meta block is the framework's own product and must not be credited to an authority."""
    meta = fc.get("meta", {})
    src = meta.get("source", "")
    reqs = meta.get("requests", [])
    if src.startswith("http"):
        who = "LUBW, Kartendienst rips-gdi" if "lubw" in src else src.split("/")[2]
        return who, (reqs[0]["fetched_at"] if reqs else None), "abgerufen"
    made = next((f["properties"].get("erstellt") for f in fc["features"]
                 if (f.get("properties") or {}).get("erstellt")), None)
    return "eigene Auswertung, keine amtlichen Daten", made, "erstellt"


def collect_layers(data_dir: Path, ox: float, oy: float, box, analysis: dict | None) -> list[dict]:
    status = _status_index(analysis)
    layers = []
    for stem, style in LAYER_STYLE.items():
        fp = data_dir / f"{stem}.geojson"
        if not fp.exists():
            continue
        fc = json.loads(fp.read_text(encoding="utf-8"))
        meta = fc.get("meta", {})
        feats = []
        for f in fc["features"]:
            parts = geometry_parts(f.get("geometry"), ox, oy, box)
            if not (parts["polygons"] or parts["lines"] or parts["points"]):
                continue
            p = f.get("properties") or {}
            name = _first(p, NAME_KEYS)
            row = status.get((stem, p.get("_name", name)))
            feat = {"name": name, "typ": _first(p, TYPE_KEYS), **{k: v for k, v in parts.items() if v}}
            if row:
                feat.update(row)
            feats.append(feat)
        credit, when, when_kind = _layer_credit(stem, fc)
        layers.append({"id": stem, **style, "features": feats, "total_in_file": len(fc["features"]),
                       "source": meta.get("source", f"data/{data_dir.name}/{stem}.geojson"),
                       "attribution": credit, "date": when, "date_kind": when_kind})
    return layers


def settlement_layer(site: dict, cache: HttpCache, report: dict, ox: float, oy: float, box) -> dict | None:
    """ALKIS residential and mixed-use land as a layer; distances are taken from the settlement report."""
    rad = float(report.get("radius_m", 1500))
    sb = geom.bbox(site["geometry"]["coordinates"])
    qbox = (sb[0] - rad, sb[1] - rad, sb[2] + rad, sb[3] + rad)  # same request as `settlement` -> cache hit
    try:
        fc = alkis.query(cache, alkis.LAND_USE, qbox)
    except CacheMiss:
        return None
    rows = {r["gml_id"]: r for r in report.get("results", [])}
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        row = rows.get(p.get("gml_id"))
        if not row:
            continue
        parts = geometry_parts(f.get("geometry"), ox, oy, box)
        if parts["polygons"]:
            feats.append({"name": row["objektname"], "typ": row.get("unterart") or "", "kind": row["kind"],
                          "distance_m": row["distance_m"], "polygons": parts["polygons"]})
    return {"id": "ALKIS_Wohnen", "label": "Wohn- und Mischflächen (ALKIS)", "color": "#ff4d6d", "group": "Umfeld",
            "on": True, "features": feats, "total_in_file": len(fc["features"]), "source": fc["meta"]["source"],
            "date": fc["meta"]["requests"][0]["fetched_at"], "date_kind": "abgerufen",
            "attribution": fc["meta"]["attribution"]}


VIEWPOINT_LABELS = {"wohnen": "Nächste Wohnbaufläche", "house": "Nächstes Wohnhaus", "far_house": "Wohnhaus, andere Seite"}


def viewpoints(report: dict | None, ox: float, oy: float, box, labels: dict | None = None) -> list[dict]:
    """Eye-level viewpoints taken from the settlement report (no guessed coordinates).
    `labels` (site property "viewpoint_labels") renames a viewpoint or, with null, leaves it out."""
    out = []
    if not report:
        return out
    names = {**VIEWPOINT_LABELS, **(labels or {})}

    def add(key, row):
        if names.get(key) and row and box[0] <= row["centre_utm32"][0] <= box[2] and box[1] <= row["centre_utm32"][1] <= box[3]:
            out.append({"label": names[key], "x": round(row["centre_utm32"][0] - ox, 1),
                        "y": round(row["centre_utm32"][1] - oy, 1), "distance_m": row["distance_m"]})

    add("wohnen", (report.get("nearest") or {}).get("wohnen"))
    blds = report.get("buildings") or []
    if blds:
        add("house", blds[0])
        # one more house from a clearly different direction, if the report has one
        bx, by = blds[0]["centre_utm32"]
        far = next((b for b in blds if math.hypot(b["centre_utm32"][0] - bx, b["centre_utm32"][1] - by) > 500), None)
        add("far_house", far)
    return out


def fetch_ortho(cache: HttpCache, box, mpp: float, out: Path, prefix: str = "ortho") -> dict:
    """DOP20 through the WMS in tiles of at most DOP_TILE_PX; returns the description for scene.json."""
    w_m, h_m = box[2] - box[0], box[3] - box[1]
    cols = max(1, math.ceil(w_m / mpp / DOP_TILE_PX))
    rows = max(1, math.ceil(h_m / mpp / DOP_TILE_PX))
    tiles, fetched = [], []
    for r in range(rows):  # row 0 = northern row
        y1 = box[3] - r * h_m / rows
        y0 = box[3] - (r + 1) * h_m / rows
        for c in range(cols):
            x0 = box[0] + c * w_m / cols
            x1 = box[0] + (c + 1) * w_m / cols
            px_w, px_h = round((x1 - x0) / mpp), round((y1 - y0) / mpp)
            params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": DOP_LAYER, "STYLES": "",
                      "CRS": "EPSG:25832", "BBOX": f"{x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}",
                      "WIDTH": px_w, "HEIGHT": px_h, "FORMAT": "image/jpeg"}
            resp = cache.request(DOP_URL, params, layer="DOP20", validate=_is_jpeg, retries=2)
            name = f"{prefix}_{r}_{c}.jpg"
            (out / name).write_bytes(resp.body)
            tiles.append({"file": name, "row": r, "col": c, "width": px_w, "height": px_h})
            fetched.append(resp.fetched_at)
    return {"rows": rows, "cols": cols, "mpp": mpp, "tiles": tiles, "source": DOP_URL, "layer": DOP_LAYER,
            "fetched_at": min(fetched), "attribution": alkis.attribution(min(fetched))}


def _coarser(img: dict, mpp: float, out: Path) -> dict:
    """Resample the tiles of a fetch_ortho result on disk to `mpp` (Lanczos, JPEG q90); unchanged without
    Pillow. Saves a second WMS request at the coarser resolution."""
    try:
        from PIL import Image
    except ImportError:
        return img
    f = img["mpp"] / mpp
    for t in img["tiles"]:
        fp = out / t["file"]
        im = Image.open(fp).convert("RGB")
        t["width"], t["height"] = max(1, round(t["width"] * f)), max(1, round(t["height"] * f))
        im.resize((t["width"], t["height"]), Image.LANCZOS).save(fp, "JPEG", quality=90)
    return {**img, "mpp": mpp, "requested_mpp": img["mpp"]}


def surface_heights(root: Path, grid: dgm.Grid, z_min: float, box, ox: float, oy: float) -> dict | None:
    """Surface with trees and buildings (DOM1) on the terrain grid, from the tiles already on
    disk; cells outside them keep the terrain height. Same encoding as terrain.bin."""
    import numpy as np  # raster work needs numpy; the rest of the bundle does not

    from . import raster
    tile_dir = root / "data" / "sources" / "dom" / "opendata"
    blocks = [tuple(int(v) for v in p.stem.split("_")[2:4]) for p in tile_dir.glob("dom1_32_*_2_bw.zip")]
    blocks = [(e, n) for e, n in blocks if e * 1000 < box[2] and (e + 2) * 1000 > box[0] and n * 1000 < box[3] and (n + 2) * 1000 > box[1]]
    if not blocks:
        return None
    # the largest rectangle of complete 2 km blocks inside the box (raster.load needs every tile)
    es, ns = sorted({e for e, _ in blocks}), sorted({n for _, n in blocks})
    have = set(blocks)
    best = None
    for i in range(len(es)):
        for j in range(i, len(es)):
            for k in range(len(ns)):
                for m in range(k, len(ns)):
                    cells = [(e, n) for e in es[i:j + 1] for n in ns[k:m + 1]]
                    if all(c in have for c in cells) and (best is None or len(cells) > best[0]):
                        best = (len(cells), es[i], es[j], ns[k], ns[m])
    _, e0, e1, n0, n1 = best
    clip = (max(box[0], e0 * 1000), max(box[1], n0 * 1000), min(box[2], (e1 + 2) * 1000), min(box[3], (n1 + 2) * 1000))
    s = raster.load("dom", clip, grid.res, root / "data" / "sources")
    xs = grid.x0 + (np.arange(grid.ncols) + 0.5) * grid.res
    ys = grid.y0 + (np.arange(grid.nrows) + 0.5) * grid.res
    X, Y = np.meshgrid(xs, ys)
    g = np.array(grid.z, dtype=np.float64).reshape(grid.nrows, grid.ncols)
    inside = (X >= clip[0]) & (X < clip[2]) & (Y >= clip[1]) & (Y < clip[3])
    z = g.copy()
    z[inside] = np.fmax(s.sample(X[inside], Y[inside]), g[inside])
    # known errors of the DOM1 (data/reference/dom_corrections.json), e.g. a steam plume matched as surface
    from . import domfix
    corrections = domfix.fix_grid(root, z, g, grid, domfix.load(root))
    z = np.where(np.isnan(z), z_min, z)
    q = np.clip(np.rint((z - z_min) * 100), 0, 65535).astype("<u2")
    return {"body": q.tobytes(), "file": "surface.bin", "encoding": "uint16-le, cm above terrain.z_min, same grid as terrain.bin",
            "extent": [clip[0] - ox, clip[1] - oy, clip[2] - ox, clip[3] - oy],
            "flight": raster.flight_dates(s), "sources": s.sources, "corrections": corrections,
            "note": "DOM1 (Oberfläche mit Bäumen und Gebäuden), je Zelle der höchste Wert; außerhalb von extent Gelände",
            "attribution": f"LGL-BW, {raster.LICENCE}, www.lgl-bw.de"}


DETAIL_JPEG_QUALITY = 90


def fetch_detail(cache: HttpCache, box, mpp: float, out: Path, ox: float, oy: float) -> dict:
    """Near-area DOP20 at mpp (e.g. 0.2 m, the native resolution) through the same WMS and cache as
    the main image, composed into one ortho_detail.jpg. Needs Pillow."""
    from PIL import Image
    d = fetch_ortho(cache, box, mpp, out, prefix="detail")
    W = sum(t["width"] for t in d["tiles"] if t["row"] == 0)
    H = sum(t["height"] for t in d["tiles"] if t["col"] == 0)
    img = Image.new("RGB", (W, H))
    for t in d["tiles"]:
        x = sum(u["width"] for u in d["tiles"] if u["row"] == t["row"] and u["col"] < t["col"])
        y = sum(u["height"] for u in d["tiles"] if u["col"] == t["col"] and u["row"] < t["row"])
        with Image.open(out / t["file"]) as im:
            img.paste(im.convert("RGB"), (x, y))
        (out / t["file"]).unlink()
    img.save(out / "ortho_detail.jpg", "JPEG", quality=DETAIL_JPEG_QUALITY)
    return {"file": "ortho_detail.jpg", "bbox": [box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy], "mpp": mpp,
            "width": W, "height": H, "source": d["source"], "layer": d["layer"], "fetched_at": d["fetched_at"],
            "attribution": d["attribution"]}


def detail_ortho(root: Path, ox: float, oy: float, out: Path) -> dict | None:
    """A sharper aerial image of the near area, if one is already on disk (data/sources/dop, logged in
    abruf.json with its WMS request). The viewer blends it over the 1-m image inside its box; nothing
    is downloaded here."""
    src = root / "data" / "sources" / "dop"
    log = src / "abruf.json"
    if not log.exists():
        return None
    rec = json.loads(log.read_text(encoding="utf-8"))
    q = dict(kv.split("=", 1) for kv in rec["url"].split("?", 1)[1].split("&"))
    x0, y0, x1, y1 = (float(v) for v in q["BBOX"].split(","))
    imgs = sorted(src.glob("*.jpg"))
    if not imgs:
        return None
    shutil.copyfile(imgs[0], out / "ortho_detail.jpg")
    return {"file": "ortho_detail.jpg", "bbox": [x0 - ox, y0 - oy, x1 - ox, y1 - oy],
            "mpp": round((x1 - x0) / int(q["WIDTH"]), 3), "source": rec["url"].split("?", 1)[0],
            "layer": q.get("LAYERS"), "fetched_at": rec.get("fetched_at"), "attribution": rec.get("attribution")}


def _mean_linear(fp: Path) -> list | None:
    """Mean colour of an image in linear RGB (as the GPU sees an sRGB texture); None without numpy/Pillow."""
    try:
        import numpy as np
        from PIL import Image
        c = np.asarray(Image.open(fp).convert("RGB"), dtype=np.float64).reshape(-1, 3) / 255.0
    except Exception:  # noqa: BLE001
        return None
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return [round(float(v), 4) for v in lin.mean(axis=0)]


def heap_textures(root: Path, out: Path, include: bool = True) -> dict:
    """Texture images for the heap made by tools/textures/make_tileable.py (synthetic, e.g. ComfyUI),
    copied next to scene.json so the single file can embed them. Since 19.11.2026 the viewer draws the
    heap from a material map of its own and does not use them (include=False only clears old copies)."""
    log = root / "viewer" / "textures" / "textures.json"
    for old in out.glob("tex_heap_*.webp"):
        old.unlink()
    if not include or not log.exists():
        return {}
    res = {}
    for kind, rec in json.loads(log.read_text(encoding="utf-8")).items():
        src = log.parent / rec["file"]
        if src.exists():
            shutil.copyfile(src, out / f"tex_{rec['file']}")
            res[kind] = {"file": f"tex_{rec['file']}", "source": rec["source"], "created": rec.get("created")}
            if rec.get("data"):   # derived relief map (make_pbr.py): data, not colour
                res[kind]["data"] = True
                continue
            mean = rec.get("mean_linear") or _mean_linear(src)   # stored for the large-scale companions
            if mean:
                res[kind]["mean_linear"] = mean
    return res


def build(site: dict, root: Path, cache: HttpCache, out: Path | None = None, pad_m: float = 1500.0,
          terrain_res: float = 5.0, ortho_mpp: float = 1.0, download: bool = True, progress=None,
          scenario: dict | None = None, surface: bool = False, detail: bool = False,
          detail_mpp: float | None = None, detail_pad_m: float = 200.0) -> Path:
    props = site["properties"]
    sid = props["id"]
    rings = site["geometry"]["coordinates"]
    sb = geom.bbox(rings)
    ox, oy = round((sb[0] + sb[2]) / 2), round((sb[1] + sb[3]) / 2)
    snap = terrain_res
    box = (math.floor((sb[0] - pad_m) / snap) * snap, math.floor((sb[1] - pad_m) / snap) * snap,
           math.ceil((sb[2] + pad_m) / snap) * snap, math.ceil((sb[3] + pad_m) / snap) * snap)
    out = out or root / "exports" / f"{sid}_web"
    out.mkdir(parents=True, exist_ok=True)

    tile_dir = root / "data" / "sources" / "dgm" / "opendata"
    grid = dgm.load_grid(box, terrain_res, tile_dir, download=download, progress=progress)
    # download dates of the tiles used, from the log dgm.ensure_zip keeps
    log = tile_dir / "abruf.json"
    stamps = {f["url"]: f["fetched_at"] for f in json.loads(log.read_text(encoding="utf-8"))["files"]} \
        if log.exists() else {}
    tile_dates = sorted(stamps[s["url"]] for s in grid.sources if s["url"] in stamps)
    body, z_min, z_max = encode_heights(grid)
    (out / "terrain.bin").write_bytes(body)
    try:   # coarse horizon ring (Copernicus DEM on disk, numpy/Pillow); skipped without them
        from . import horizon
        ring = horizon.build_ring(root, grid, box, ox, oy, out)
    except ImportError:
        ring = None
    surf = surface_heights(root, grid, z_min, box, ox, oy) if surface else None
    if surf:
        (out / "surface.bin").write_bytes(surf.pop("body"))

    def report(name):
        fp = root / "reports" / f"{sid}_{name}.json"
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None

    analysis, settle = report("analyse"), report("siedlungsabstand")
    tree_info = None
    for old in ("trees.bin", "canopy_class.bin"):
        if (out / old).exists():
            (out / old).unlink()
    if surf:   # stand-in trees from DOM1/DGM1 at 1 m; buildings masked with the cached ALKIS query
        rad = float((settle or {}).get("radius_m", 1500))
        bbox_b = (sb[0] - rad, sb[1] - rad, sb[2] + rad, sb[3] + rad)   # same request as `settlement`
        ext = surf["extent"]
        try:
            from . import trees
            tree_info = trees.build(root, cache, grid, (ext[0] + ox, ext[1] + oy, ext[2] + ox, ext[3] + oy),
                                    bbox_b, ox, oy, out)
        except ImportError:   # scipy missing
            tree_info = None
    layers = collect_layers(root / "data" / sid, ox, oy, box, analysis)
    if settle:
        lay = settlement_layer(site, cache, settle, ox, oy, box)
        if lay:
            layers.append(lay)
    ortho = fetch_ortho(cache, box, ortho_mpp, out)
    for old in out.glob("ringimg_*.jpg"):
        old.unlink()
    if ring:   # coarse aerial image for the horizon ring, same WMS (one request of about 2000 x 2000 px)
        rx0, ry0, span = ring["x0"] + ox, ring["y0"] + oy, (ring["n"] - 1) * ring["res"]
        ring["image"] = _coarser(fetch_ortho(cache, (rx0, ry0, rx0 + span, ry0 + span), RING_MPP, out, prefix="ringimg"),
                                 RING_STORE_MPP, out)
    stale = out / "ortho_detail.jpg"
    if stale.exists():
        stale.unlink()
    if detail_mpp:   # fetched (or taken from the cache) at detail_mpp around the site bbox
        dbox = (math.floor(sb[0] - detail_pad_m), math.floor(sb[1] - detail_pad_m),
                math.ceil(sb[2] + detail_pad_m), math.ceil(sb[3] + detail_pad_m))
        ortho_detail = fetch_detail(cache, dbox, detail_mpp, out, ox, oy)
    else:            # an image already on disk (data/sources/dop)
        ortho_detail = detail_ortho(root, ox, oy, out) if detail else None
    textures = heap_textures(root, out, include=False)   # the viewer draws its own material map
    # grayscale detail tiles for that map (tools/textures/make_detail.py), embedded: small, read at once
    det_fp = root / "viewer" / "textures" / "heap_detail.json"
    heap_detail = json.loads(det_fp.read_text(encoding="utf-8")) if det_fp.exists() else None

    doc = {
        "format": "geoanalysis-viewer/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "crs": "EPSG:25832",
        "origin": [ox, oy],
        "axes": "x = East - origin[0], y = North - origin[1] (metres); three.js: (x, height, -y)",
        "bbox": [box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy],
        "site": {"id": sid, "name": props.get("name", sid), "tolerance_m": props.get("tolerance_m"),
                 "tolerance_note": props.get("tolerance_note"), "source": props.get("source"),
                 "retrieved": props.get("retrieved"), "area_ha": round(geom.polygon_area(rings) / 1e4, 2),
                 "rings": [[[round(x - ox, 2), round(y - oy, 2)] for x, y in r] for r in rings]},
        "terrain": {"file": "terrain.bin", "encoding": "uint16-le, cm above z_min, row 0 = south, values = cell means",
                    "x0": grid.x0 - ox, "y0": grid.y0 - oy, "res": grid.res, "ncols": grid.ncols,
                    "nrows": grid.nrows, "z_min": round(z_min, 2), "z_max": round(z_max, 2),
                    "accuracy_m": dgm.HEIGHT_ACCURACY_M,
                    "note": "DGM1 (Geländemodell ohne Gebäude und Bewuchs), Befliegung laut Kacheldateiname",
                    "sources": grid.sources, "licence": dgm.LICENCE,
                    "fetched_at": tile_dates[-1] if tile_dates else None,
                    "attribution": f"LGL-BW, {dgm.LICENCE}, www.lgl-bw.de"},
        "surface": surf,
        "trees": tree_info,
        "ring": ring,
        "ortho": ortho,
        "ortho_detail": ortho_detail,
        "textures": textures or None,
        "heap_detail": heap_detail,
        "layers": layers,
        "viewpoints": viewpoints(settle, ox, oy, box, props.get("viewpoint_labels")),
        # start values of the heap scenario (sites/<id>_szenario.json); the viewer labels them as assumptions
        "scenario": {k: v for k, v in (scenario or {}).items() if k != "comment"} or None,
        "analysis": ({"generated": analysis.get("generated"), "radius_m": analysis.get("radius_m"),
                      "method": analysis.get("overlap_method")} if analysis else None),
    }
    (out / "scene.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    viewer = root / "viewer"
    if viewer.is_dir():  # includes vendor/three, so the page needs no third-party server
        shutil.copytree(viewer, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("standalone"))
    return out

"""Coarse terrain ring for the horizon of the 3D viewer.

The detailed terrain (LGL DGM1) covers only about 1.5 km around the site; behind it the view ended
in empty sky. The ring adds the land out to RING_RADIUS_M on a RING_RES_M grid from the Copernicus
DEM GLO-30, so the skyline (Albtrauf, Hohenstaufen) is real.

Source: Copernicus DEM GLO-30, tile Copernicus_DSM_COG_10_N48_00_E009_00_DEM.tif from AWS Open
Data (https://registry.opendata.aws/copernicus-dem/), stored with abruf.json in
data/sources/copdem. It is a *surface* model (forests and buildings included, heights on the
EGM2008 geoid), so it is used only for the distant ring: inside the DGM1 area the ring sits 2 m
below the DGM1, and over the first BLEND_M outside it the heights blend from the DGM1 edge into
the Copernicus heights, so there is no step at the seam. Needs numpy and Pillow.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

RING_RADIUS_M = 15000.0
RING_RES_M = 60.0
BLEND_M = 1000.0
COP_FILE = "Copernicus_DSM_COG_10_N48_00_E009_00_DEM.tif"
ATTRIBUTION = ("produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and "
               "Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved")

# GRS80 (ETRS89), UTM zone 32
_A, _F, _K0, _LON0 = 6378137.0, 1 / 298.257222101, 0.9996, math.radians(9.0)


def utm32_to_lonlat(e, n):
    """EPSG:25832 easting/northing (scalars or numpy arrays) to longitude/latitude in degrees.
    Inverse transverse Mercator series (Snyder, USGS PP 1395, eq. 8-12 ff.); error well below 1 m
    within the zone."""
    import numpy as np
    e2 = _F * (2 - _F)
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x = np.asarray(e, dtype=np.float64) - 500000.0
    mu = np.asarray(n, dtype=np.float64) / _K0 / (_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * np.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * np.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * np.sin(6 * mu) + (1097 * e1 ** 4 / 512) * np.sin(8 * mu))
    s, c, t = np.sin(p1), np.cos(p1), np.tan(p1)
    c1, t1 = ep2 * c ** 2, t ** 2
    n1 = _A / np.sqrt(1 - e2 * s ** 2)
    r1 = _A * (1 - e2) / (1 - e2 * s ** 2) ** 1.5
    d = x / (n1 * _K0)
    lat = p1 - (n1 * t / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / c
    return np.degrees(lon), np.degrees(lat)


def _load_cop(fp: Path):
    """Heights and georeference of a Copernicus GeoTIFF: (array, lon of the left edge, lat of the top
    edge, degrees per pixel in x and y)."""
    import numpy as np
    from PIL import Image
    im = Image.open(fp)
    sx, sy = im.tag_v2[33550][:2]
    tp = im.tag_v2[33922]
    return np.array(im, dtype=np.float32), tp[3], tp[4], sx, sy


def _sample(img, lon0, lat0, sx, sy, lon, lat):
    """Bilinear sample at pixel centres (PixelIsArea: the tie point is the corner of pixel 0)."""
    import numpy as np
    h, w = img.shape
    fx = np.clip((lon - lon0) / sx - 0.5, 0, w - 1.001)
    fy = np.clip((lat0 - lat) / sy - 0.5, 0, h - 1.001)
    c0, r0 = fx.astype(int), fy.astype(int)
    tx, ty = fx - c0, fy - r0
    a, b = img[r0, c0], img[r0, c0 + 1]
    c, d = img[r0 + 1, c0], img[r0 + 1, c0 + 1]
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def build_ring(root: Path, grid, inner_box, ox: float, oy: float, out: Path) -> dict | None:
    """Write ring.bin (uint16 little-endian, decimetres above z_min, row 0 = south) and return its
    scene entry, or None when the Copernicus tile is not on disk. `grid` is the DGM1 grid of the
    detailed terrain and `inner_box` its extent (EPSG:25832)."""
    import numpy as np
    src = root / "data" / "sources" / "copdem"
    fp = src / COP_FILE
    for old in out.glob("ring.bin"):
        old.unlink()
    if not fp.exists():
        return None
    img, lon0, lat0, sx, sy = _load_cop(fp)
    r = RING_RES_M
    x0 = math.floor((ox - RING_RADIUS_M) / r) * r
    y0 = math.floor((oy - RING_RADIUS_M) / r) * r
    n = int(round(2 * RING_RADIUS_M / r)) + 1
    xs = x0 + np.arange(n) * r
    ys = y0 + np.arange(n) * r
    X, Y = np.meshgrid(xs, ys)                      # row 0 = south
    lon, lat = utm32_to_lonlat(X, Y)
    z = _sample(img, lon0, lat0, sx, sy, lon, lat).astype(np.float64)
    # DGM1 heights at the nearest point of the inner box (clamped), for the seam
    bx0, by0, bx1, by1 = inner_box
    cx, cy = np.clip(X, bx0, bx1), np.clip(Y, by0, by1)
    edge = np.array([grid.height(a, b) for a, b in zip(cx.ravel(), cy.ravel())]).reshape(X.shape)
    dist = np.maximum(np.abs(X - cx), np.abs(Y - cy))           # metres outside the box, 0 inside
    t = np.clip(dist / BLEND_M, 0, 1)
    t = t * t * (3 - 2 * t)
    z = np.where(dist > 0, edge * (1 - t) + z * t, edge - 2.0)
    z_min = float(np.floor(z.min()))
    q = np.round((z - z_min) * 10).astype(np.uint16)
    (out / "ring.bin").write_bytes(q.astype("<u2").tobytes())
    log = src / "abruf.json"
    fetched = None
    if log.exists():
        files = json.loads(log.read_text(encoding="utf-8-sig")).get("files", [])
        fetched = files[0].get("fetched_at") if files else None
    return {"file": "ring.bin", "encoding": "uint16-le, decimetres above z_min, row 0 = south, values at grid points",
            "x0": float(x0 - ox), "y0": float(y0 - oy), "res": r, "n": n, "z_min": z_min,
            "inner": [bx0 - ox, by0 - oy, bx1 - ox, by1 - oy], "blend_m": BLEND_M,
            "source": "Copernicus DEM GLO-30 (Oberflächenmodell mit Wald und Gebäuden), AWS Open Data",
            "url": "https://registry.opendata.aws/copernicus-dem/", "fetched_at": fetched,
            "attribution": ATTRIBUTION,
            "note": "Nur für den Horizont außerhalb des genauen Geländemodells; 60-m-Raster, grob."}

"""Tree positions for stand-in trees in the 3D viewer, measured from the surface model.

The 5-m blocks of the viewer show where something stands, but a wood reads as a wall of boxes. Here the
single tree tops are taken from the 1-m surface model (DOM1) minus the 1-m terrain model (DGM1): every
local maximum of the height above ground that is at least MIN_TREE_M high and not on a building is one
tree. The viewer draws a simple stand-in (trunk and round crown) there, at the measured height. Nothing
is added, moved or jittered: no maximum, no tree.

Buildings are masked with the ALKIS building footprints (LGL-BW, the same WFS and cache as the settlement
report, so no new request), widened by BUILDING_PAD_M. Without them the step is skipped, because roof
ridges would become trees.

What is measured and what is assumed:
- position and height of each top: measured (DOM1/DGM1, 1 m). The flights were in March, leaf-off, so
  deciduous crowns are partly seen through and some tops may be lower or split into several maxima.
- crown radius: the distance from the top to where the height falls below CROWN_DROP of the top height
  (or rises again towards the next crown), mean of 8 directions; a rough measure, clamped to 1-8 m.
- crown shape, trunk and the vertical size of the crown: an assumption of the viewer, not measured.

Also writes a class per 5-m terrain cell (0 nothing tall, 1 tall and under a detected crown, 2 any other
tall cell), so the viewer replaces only cells a stand-in covers and keeps blocks for buildings, hedges
below MIN_TREE_M, pylons and anything else without a detected top. Needs numpy and scipy.
"""
from __future__ import annotations

from pathlib import Path

MIN_TREE_M = 3.0        # lower tops are shrubs, hedges, parked cars
TALL_M = 2.0            # same threshold as the viewer's blocks (TALL_CM)
BUILDING_PAD_M = 2      # footprints widened by this much (roof overhang, registration)
CROWN_DROP = 0.6        # crown edge: height below this share of the top
CROWN_R_MIN, CROWN_R_MAX = 1.0, 8.0
# minimum distance between two tops grows with the height (window edge in cells of 1 m)
WINDOWS = ((0.0, 5), (10.0, 7), (20.0, 9))


def find_tops(ndsm, building, res: float = 1.0):
    """Tree tops in a height-above-ground array (row 0 = south). Returns (rows, cols, heights, radii)
    as numpy arrays; `building` is a boolean mask of the same shape (no tops there)."""
    import numpy as np
    from scipy import ndimage
    h = np.nan_to_num(ndsm.astype(np.float32), nan=0.0)
    s = ndimage.gaussian_filter(h, 1.0)            # one top per crown, not per branch
    # each cell is tested in the window of its own height band: a top must be the highest point within it
    peak = np.zeros(h.shape, dtype=bool)
    bounds = [lo for lo, _ in WINDOWS[1:]] + [float("inf")]
    for (lo, size), hi in zip(WINDOWS, bounds):
        peak |= (s >= lo) & (s < hi) & (s == ndimage.maximum_filter(s, size=size, mode="nearest"))
    peak &= (s >= MIN_TREE_M) & ~building
    rows, cols = np.nonzero(peak)
    # height: the highest raw value within 1 m of the smoothed top
    hmax = ndimage.maximum_filter(h, size=3, mode="nearest")
    heights = hmax[rows, cols]
    # crown radius from 8 directions, up to CROWN_R_MAX
    steps = int(CROWN_R_MAX / res)
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    radii = np.zeros(len(rows), dtype=np.float32)
    H, W = s.shape
    top = s[rows, cols]
    for dy, dx in dirs:
        unit = res * (2 ** 0.5 if dx and dy else 1.0)
        reach = np.full(len(rows), steps * unit, dtype=np.float32)
        open_ = np.ones(len(rows), dtype=bool)
        prev = top.copy()
        for k in range(1, steps + 1):
            r = np.clip(rows + dy * k, 0, H - 1)
            c = np.clip(cols + dx * k, 0, W - 1)
            v = s[r, c]
            stop = open_ & ((v < CROWN_DROP * top) | (v > prev + 0.05))
            reach[stop] = (k - 0.5) * unit
            open_ &= ~stop
            prev = v
        radii += reach
    radii = np.clip(radii / len(dirs), CROWN_R_MIN, CROWN_R_MAX)
    return rows, cols, heights, radii


def building_mask(r, cache, box) -> "object | None":
    """ALKIS building footprints on raster r, widened by BUILDING_PAD_M; None when not in the cache."""
    import numpy as np
    from scipy import ndimage

    from . import alkis, raster
    from .cache import CacheMiss
    try:
        fc = alkis.query(cache, alkis.BUILDINGS, box)
    except CacheMiss:
        return None
    polys = [p for f in fc["features"] for p in raster.polygons_of(f.get("geometry"))]
    m = raster.polygon_mask(r, polys) if polys else np.zeros(r.z.shape, dtype=bool)
    if BUILDING_PAD_M:
        m = ndimage.binary_dilation(m, iterations=int(round(BUILDING_PAD_M / r.res)))
    return m, fc["meta"]


def build(root: Path, cache, grid, extent, building_box, ox: float, oy: float, out: Path) -> dict | None:
    """Write trees.bin and canopy_class.bin for the bundle and return the scene entry, or None.

    extent: the area covered by the surface model (absolute EPSG:25832), grid: the 5-m terrain grid,
    building_box: bbox of the cached ALKIS building query (the one the settlement report made)."""
    import numpy as np

    from . import raster
    for old in ("trees.bin", "canopy_class.bin"):
        if (out / old).exists():
            (out / old).unlink()
    src = root / "data" / "sources"
    dom = raster.load("dom", extent, 1.0, src)
    dgm = raster.load("dgm", extent, 1.0, src)
    if dom.z.shape != dgm.z.shape or (dom.x0, dom.y0) != (dgm.x0, dgm.y0):
        raise RuntimeError("DOM1 and DGM1 grids differ")
    bm = building_mask(dom, cache, building_box)
    if bm is None:
        return None
    building, meta = bm
    ndsm = dom.z - dgm.z
    # known errors of the DOM1 (a steam plume matched as surface) are ground, not tree tops
    from . import domfix
    err, _ = domfix.error_mask(dom.x0, dom.y0, dom.res, ndsm, domfix.load(root))
    ndsm = np.where(err, 0.0, ndsm)
    rows, cols, heights, radii = find_tops(ndsm, building, dom.res)
    xs = dom.x0 + (cols + 0.5) * dom.res - ox
    ys = dom.y0 + (rows + 0.5) * dom.res - oy
    order = np.lexsort((xs, ys))
    rec = np.zeros(len(order), dtype=[("x", "<i2"), ("y", "<i2"), ("h", "u1"), ("r", "u1")])
    rec["x"] = np.round(xs[order] * 10)
    rec["y"] = np.round(ys[order] * 10)
    rec["h"] = np.clip(np.round(heights[order] * 4), 0, 255)       # 0.25 m steps, up to 63 m
    rec["r"] = np.clip(np.round(radii[order] * 10), 0, 255)        # 0.1 m steps
    # struct of arrays: each column compresses on its own
    (out / "trees.bin").write_bytes(b"".join(rec[k].tobytes() for k in ("x", "y", "h", "r")))
    # class per 5-m terrain cell
    tall = np.nan_to_num(ndsm, nan=0.0) > TALL_M
    k = int(round(grid.res / dom.res))
    cls = np.zeros((grid.nrows, grid.ncols), dtype=np.uint8)
    c0 = int(round((dom.x0 - grid.x0) / grid.res))
    r0 = int(round((dom.y0 - grid.y0) / grid.res))
    nr, nc = dom.z.shape[0] // k, dom.z.shape[1] // k
    if (dom.x0 - grid.x0) % grid.res or (dom.y0 - grid.y0) % grid.res:
        raise RuntimeError("surface extent is not aligned to the terrain grid")
    t = tall[:nr * k, :nc * k].reshape(nr, k, nc, k).any(axis=(1, 3))
    b = (tall & building)[:nr * k, :nc * k].reshape(nr, k, nc, k).any(axis=(1, 3))
    # a cell is replaced by stand-ins only where a detected crown covers its centre; everything else that
    # stands (buildings, hedges below MIN_TREE_M, pylons, anything without a top) keeps its block
    cover = np.zeros((nr, nc), dtype=bool)
    cy = (np.arange(nr) + 0.5) * k * dom.res          # cell centres in metres from the raster origin
    cx = (np.arange(nc) + 0.5) * k * dom.res
    for tr, tc, rad in zip(rows, cols, radii):
        py, px = (tr + 0.5) * dom.res, (tc + 0.5) * dom.res
        i0, i1 = np.searchsorted(cy, py - rad), np.searchsorted(cy, py + rad)
        j0, j1 = np.searchsorted(cx, px - rad), np.searchsorted(cx, px + rad)
        if i0 < i1 and j0 < j1:
            cover[i0:i1, j0:j1] |= ((cy[i0:i1, None] - py) ** 2 + (cx[None, j0:j1] - px) ** 2) <= rad * rad
        ti, tj = min(int(tr // k), nr - 1), min(int(tc // k), nc - 1)
        cover[ti, tj] = True                          # the cell holding the top
    sub = np.where(t & cover & ~b, 1, np.where(t, 2, 0)).astype(np.uint8)
    crown_m2 = float(np.sum(np.pi * radii ** 2))
    tree_cells_m2 = float((sub == 1).sum() * grid.res ** 2)
    tall_m2 = float(t.sum() * grid.res ** 2)
    rr0, cc0 = max(r0, 0), max(c0, 0)
    rr1, cc1 = min(r0 + nr, grid.nrows), min(c0 + nc, grid.ncols)
    cls[rr0:rr1, cc0:cc1] = sub[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]
    (out / "canopy_class.bin").write_bytes(cls.tobytes())
    return {"file": "trees.bin", "count": int(len(order)), "class_file": "canopy_class.bin",
            "encoding": "4 columns one after the other, count values each: x int16 dm, y int16 dm (local), "
                        "h uint8 height above ground in 0.25 m, r uint8 crown radius in 0.1 m; class file: "
                        "uint8 per terrain cell (0 nothing tall, 1 tall and under a detected crown: drawn as "
                        "stand-ins, 2 every other tall cell: stays a block)",
            "crown_area_m2": round(crown_m2), "tree_cells_m2": round(tree_cells_m2), "tall_cells_m2": round(tall_m2),
            "min_height_m": MIN_TREE_M, "crown_drop": CROWN_DROP, "building_pad_m": BUILDING_PAD_M,
            "buildings": {"source": meta["source"], "attribution": meta["attribution"],
                          "fetched_at": meta["requests"][0]["fetched_at"]},
            "note": "Stellvertreter: Lage und Höhe je Baumspitze aus DOM1 minus DGM1 (1 m, Befliegung im März, "
                    "unbelaubt); Kronenbreite grob gemessen; Form von Krone und Stamm angenommen."}

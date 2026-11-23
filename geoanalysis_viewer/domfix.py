"""Known errors in the surface model (DOM1) and their correction.

The DOM1 of LGL-BW comes from image matching of the flight photos. Steam over a chimney can then be
matched as if it were a solid surface: at the waste power plant in Göppingen the 2016 flight shows a
body about 30 x 28 m wide and up to 154 m high next to the boiler house, while the stack itself is a
few metres wide and about 100 m high. Such errors are listed with their evidence in
data/reference/dom_corrections.json; here every 1-m cell inside a listed box that is higher above the
ground than `above_ground_m` is set to the ground, except the cells within `keep` (the measured stack
top). Nothing is added: the stack keeps its own DOM1 values.
"""
from __future__ import annotations

import json
from pathlib import Path


def load(root: Path) -> list[dict]:
    fp = Path(root) / "data" / "reference" / "dom_corrections.json"
    return json.loads(fp.read_text(encoding="utf-8"))["corrections"] if fp.exists() else []


def error_mask(x0: float, y0: float, res: float, ndsm, corrections: list[dict]):
    """Boolean mask of the cells of a 1-m height-above-ground array (row 0 = south, lower left corner
    x0, y0) that belong to a listed error. Returns (mask, [ids of the corrections that hit])."""
    import numpy as np
    mask = np.zeros(ndsm.shape, dtype=bool)
    hit = []
    xs = x0 + (np.arange(ndsm.shape[1]) + 0.5) * res
    ys = y0 + (np.arange(ndsm.shape[0]) + 0.5) * res
    X, Y = np.meshgrid(xs, ys)
    for c in corrections:
        bx0, by0, bx1, by1 = c["bbox"]
        m = (X >= bx0) & (X < bx1) & (Y >= by0) & (Y < by1) & (np.nan_to_num(ndsm, nan=0.0) > c["above_ground_m"])
        k = c.get("keep")
        if k:
            m &= (X - k["x"]) ** 2 + (Y - k["y"]) ** 2 > k["radius_m"] ** 2
        if m.any():
            mask |= m
            hit.append(c["id"])
    return mask, hit


def fix_grid(root: Path, z, ground, grid, corrections: list[dict]) -> list[dict]:
    """Correct a surface grid `z` (absolute heights, rows south to north, same layout as `grid`, cell
    size a whole number of metres) in place: the cells touching a listed box are recomputed from the
    1-m DOM1 and DGM1 with the error cells set to the ground, as the highest value per cell and never
    below `ground` (the terrain grid). Returns what was changed."""
    import numpy as np

    from . import raster
    done = []
    src = Path(root) / "data" / "sources"
    k = int(round(grid.res))
    for c in corrections:
        bx0, by0, bx1, by1 = c["bbox"]
        c0 = max(int(np.floor((bx0 - grid.x0) / grid.res)), 0)
        r0 = max(int(np.floor((by0 - grid.y0) / grid.res)), 0)
        c1 = min(int(np.ceil((bx1 - grid.x0) / grid.res)), grid.ncols)
        r1 = min(int(np.ceil((by1 - grid.y0) / grid.res)), grid.nrows)
        if c0 >= c1 or r0 >= r1:
            continue
        box = (grid.x0 + c0 * grid.res, grid.y0 + r0 * grid.res, grid.x0 + c1 * grid.res, grid.y0 + r1 * grid.res)
        dom = raster.load("dom", box, 1.0, src)
        dgm = raster.load("dgm", box, 1.0, src)
        if dom.z.shape != ((r1 - r0) * k, (c1 - c0) * k) or (dom.x0, dom.y0) != (box[0], box[1]):
            raise RuntimeError("DOM1 grid is not aligned to the terrain grid")
        ndsm = dom.z - dgm.z
        mask, hit = error_mask(dom.x0, dom.y0, 1.0, ndsm, [c])
        if not hit:
            continue
        fixed = np.where(mask, dgm.z, dom.z)
        with np.errstate(all="ignore"):
            agg = np.nanmax(fixed.reshape(r1 - r0, k, c1 - c0, k), axis=(1, 3))
        z[r0:r1, c0:c1] = np.fmax(agg, ground[r0:r1, c0:c1])
        kept = np.where(mask, -np.inf, np.nan_to_num(ndsm, nan=-np.inf))
        kc = c.get("keep")
        if kc:
            xs = dom.x0 + (np.arange(dom.z.shape[1]) + 0.5)
            ys = dom.y0 + (np.arange(dom.z.shape[0]) + 0.5)
            X, Y = np.meshgrid(xs, ys)
            kept = np.where((X - kc["x"]) ** 2 + (Y - kc["y"]) ** 2 <= kc["radius_m"] ** 2, kept, -np.inf)
        done.append({"id": c["id"], "name": c["name"], "cells_1m": int(mask.sum()),
                     "max_removed_m": round(float(np.nanmax(ndsm[mask])), 1),
                     "kept": ({"what": kc["what"], "top_m": round(float(kept.max()), 1)} if kc else None),
                     "evidence": c["evidence"], "height_source": c.get("height_source"), "checked": c.get("checked")})
    return done

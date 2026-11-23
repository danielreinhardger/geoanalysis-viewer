"""Terrain grid from the LGL-BW DGM1 open-data tiles (1 m grid, heights in cm resolution).

Source: https://opengeodata.lgl-bw.de/data/dgm/dgm1_32_<E>_<N>_2_bw.zip , one zip per
2 km x 2 km block (E odd, N even, in km), each holding four 1 km tiles as "x y z" text with
points at the centres of the 1 m cells. Licence: Datenlizenz Deutschland - Namensnennung -
Version 2.0 ("LGL-BW (<year>) ..., www.lgl-bw.de"). Height accuracy per tile metadata: 0.15 m.

Pure standard library. Reading a 1 km tile takes a few seconds, so the resampled grid is
kept in a small binary cache next to the tiles.
"""
from __future__ import annotations

import array
import json
import math
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

# the LGL height products share one tiling: one zip per 2 km x 2 km block
TILE_URLS = {"dgm": "https://opengeodata.lgl-bw.de/data/dgm/dgm1_32_{e}_{n}_2_bw.zip",
             "dom": "https://opengeodata.lgl-bw.de/data/dom1/dom1_32_{e}_{n}_2_bw.zip"}
TILE_URL = TILE_URLS["dgm"]
LICENCE = "Datenlizenz Deutschland - Namensnennung - Version 2.0"
HEIGHT_ACCURACY_M = 0.15  # from the tile metadata (column "Genauigkeit")


class Grid:
    """Regular grid, row 0 at the southern edge. `z` holds cell means; NaN where no data."""

    def __init__(self, x0: float, y0: float, res: float, ncols: int, nrows: int, z=None, sources=None):
        self.x0, self.y0, self.res, self.ncols, self.nrows = x0, y0, res, ncols, nrows
        self.z = z if z is not None else array.array("f", [math.nan]) * (ncols * nrows)
        self.sources = sources or []

    def idx(self, col: int, row: int) -> int:
        return row * self.ncols + col

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return int((x - self.x0) // self.res), int((y - self.y0) // self.res)

    def inside(self, col: int, row: int) -> bool:
        return 0 <= col < self.ncols and 0 <= row < self.nrows

    def centre(self, col: int, row: int) -> tuple[float, float]:
        return self.x0 + (col + 0.5) * self.res, self.y0 + (row + 0.5) * self.res

    def height(self, x: float, y: float) -> float:
        """Bilinear interpolation between cell centres (clamped at the edge)."""
        fx = (x - self.x0) / self.res - 0.5
        fy = (y - self.y0) / self.res - 0.5
        c0 = min(max(int(math.floor(fx)), 0), self.ncols - 2)
        r0 = min(max(int(math.floor(fy)), 0), self.nrows - 2)
        tx = min(max(fx - c0, 0.0), 1.0)
        ty = min(max(fy - r0, 0.0), 1.0)
        z = self.z
        a, b = z[self.idx(c0, r0)], z[self.idx(c0 + 1, r0)]
        c, d = z[self.idx(c0, r0 + 1)], z[self.idx(c0 + 1, r0 + 1)]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    def smoothed(self, k: int) -> "Grid":
        """Box mean over k x k cells (k odd), edges clamped. Used to judge gradients over a
        length a road can actually follow instead of every 5 m bump."""
        if k <= 1:
            return self
        h, n, m, z = k // 2, self.ncols, self.nrows, self.z
        tmp = array.array("f", bytes(4 * n * m))
        for r in range(m):
            base = r * n
            row = z[base:base + n]
            pre = [0.0]
            for v in row:
                pre.append(pre[-1] + v)
            for c in range(n):
                a, b = max(0, c - h), min(n, c + h + 1)
                tmp[base + c] = (pre[b] - pre[a]) / (b - a)
        out = array.array("f", bytes(4 * n * m))
        for c in range(n):
            pre = [0.0]
            for r in range(m):
                pre.append(pre[-1] + tmp[r * n + c])
            for r in range(m):
                a, b = max(0, r - h), min(m, r + h + 1)
                out[r * n + c] = (pre[b] - pre[a]) / (b - a)
        return Grid(self.x0, self.y0, self.res, n, m, out, self.sources)


def plane(x0, y0, res, ncols, nrows, f) -> Grid:
    """Synthetic grid from a function f(x, y) of the cell centre; for tests."""
    g = Grid(x0, y0, res, ncols, nrows)
    for r in range(nrows):
        for c in range(ncols):
            g.z[g.idx(c, r)] = f(*g.centre(c, r))
    return g


def zip_block(km_e: int, km_n: int) -> tuple[int, int]:
    """Lower-left corner (km) of the 2 km zip block that holds the 1 km tile."""
    return (km_e if km_e % 2 == 1 else km_e - 1), (km_n if km_n % 2 == 0 else km_n - 1)


def tiles_for(bbox) -> list[tuple[int, int]]:
    """1 km tiles (km east, km north of the lower-left corner) that cover the bbox."""
    return [(e, n) for e in range(int(bbox[0] // 1000), int(math.ceil(bbox[2] / 1000)))
            for n in range(int(bbox[1] // 1000), int(math.ceil(bbox[3] / 1000)))]


def ensure_zip(tile_dir: Path, km_e: int, km_n: int, download: bool, kind: str = "dgm") -> Path:
    """Path of the zip that holds the 1 km tile; downloads it when allowed and logs the fetch in
    <tile_dir>/abruf.json. kind: "dgm" (bare ground) or "dom" (surface)."""
    e, n = zip_block(km_e, km_n)
    fp = tile_dir / f"{kind}1_32_{e}_{n}_2_bw.zip"
    if fp.exists():
        return fp
    url = TILE_URLS[kind].format(e=e, n=n)
    if not download:
        raise FileNotFoundError(f"{kind.upper()}1 tile missing: {fp.name}. Download {url} into {tile_dir} "
                                f"(or run without --offline).")
    tile_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as r:
        body = r.read()
    if body[:2] != b"PK":
        raise RuntimeError(f"{url} did not return a zip file")
    fp.write_bytes(body)
    log = tile_dir / "abruf.json"
    doc = json.loads(log.read_text(encoding="utf-8")) if log.exists() else {
        "attribution": f"LGL-BW, {LICENCE}, www.lgl-bw.de", "files": []}
    doc["files"].append({"url": url, "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    log.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def load_grid(bbox, res: float, tile_dir: Path, download: bool = False, progress=None) -> Grid:
    """Mean height per res x res cell for the bbox (snapped outward to whole metres)."""
    x0, y0 = math.floor(bbox[0] / res) * res, math.floor(bbox[1] / res) * res
    ncols = int(math.ceil((bbox[2] - x0) / res))
    nrows = int(math.ceil((bbox[3] - y0) / res))
    tile_dir = Path(tile_dir)
    cache_dir = tile_dir.parent / "grid_cache"
    stem = f"dgm_{x0:.0f}_{y0:.0f}_{ncols}x{nrows}_{res:g}m"
    meta_fp, bin_fp = cache_dir / f"{stem}.json", cache_dir / f"{stem}.bin"
    if meta_fp.exists() and bin_fp.exists():
        z = array.array("f")
        z.frombytes(bin_fp.read_bytes())
        return Grid(x0, y0, res, ncols, nrows, z, json.loads(meta_fp.read_text(encoding="utf-8"))["sources"])
    n = ncols * nrows
    total, count, sources = array.array("d", bytes(8 * n)), array.array("l", bytes(array.array("l").itemsize * n)), []
    x1, y1 = x0 + ncols * res, y0 + nrows * res
    for km_e, km_n in tiles_for((x0, y0, x1, y1)):
        zp = ensure_zip(tile_dir, km_e, km_n, download)
        with zipfile.ZipFile(zp) as zf:
            names = [m for m in zf.namelist() if m.endswith(".xyz") and f"_{km_e}_{km_n}_1_" in m]
            if not names:
                raise RuntimeError(f"{zp.name} holds no tile for {km_e}/{km_n}")
            if progress:
                progress(names[0])
            sources.append({"zip": zp.name, "tile": names[0].rsplit("/", 1)[-1],
                            "url": TILE_URL.format(e=zip_block(km_e, km_n)[0], n=zip_block(km_e, km_n)[1])})
            with zf.open(names[0]) as fh:
                for line in fh:
                    p = line.split()
                    x, y = float(p[0]), float(p[1])
                    if x0 <= x < x1 and y0 <= y < y1:
                        i = int((y - y0) // res) * ncols + int((x - x0) // res)
                        total[i] += float(p[2])
                        count[i] += 1
    z = array.array("f", (total[i] / count[i] if count[i] else math.nan for i in range(n)))
    cache_dir.mkdir(parents=True, exist_ok=True)
    bin_fp.write_bytes(z.tobytes())
    meta_fp.write_text(json.dumps({"bbox": [x0, y0, x1, y1], "res": res, "sources": sources}, indent=1),
                       encoding="utf-8")
    return Grid(x0, y0, res, ncols, nrows, z, sources)

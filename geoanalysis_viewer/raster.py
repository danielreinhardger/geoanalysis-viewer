"""Georeferenced height rasters from the LGL-BW open-data tiles, as numpy arrays.

Two products, same tiling (one zip per 2 km x 2 km block, four 1 km tiles of 1 m cells):

  dgm  bare ground          https://opengeodata.lgl-bw.de/data/dgm/dgm1_32_<E>_<N>_2_bw.zip   ("x y z" text)
  dom  surface with trees   https://opengeodata.lgl-bw.de/data/dom1/dom1_32_<E>_<N>_2_bw.zip  (GeoTIFF + .tfw)
       and buildings

Licence: Datenlizenz Deutschland - Namensnennung - Version 2.0, "LGL-BW, www.lgl-bw.de".
Each tile comes with a .csv that states the flight date (Aktualitaet) and the last update
(Fortfuehrung); both are kept in `Raster.sources`, because a surface model is only as current
as its flight.

This is the one part of the package that needs numpy (and pillow for the GeoTIFF tiles). It is
imported lazily by the commands that use it, so the vector commands run without numpy.
`geoanalysis_viewer.dgm` is the pure-Python reader the terrain grid of the viewer uses.
"""
from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np

from . import dgm as _dgm

LICENCE = _dgm.LICENCE
KINDS = {
    "dgm": {"url": _dgm.TILE_URLS["dgm"], "agg": "mean",
            "label": "Digitales Geländemodell DGM1"},
    "dom": {"url": _dgm.TILE_URLS["dom"], "agg": "max",
            "label": "Digitales Oberflächenmodell DOM1"},
}


class Raster:
    """Regular grid in EPSG:25832. `z[row, col]`, row 0 at the southern edge, values are cell
    means (dgm) or cell maxima (dom); NaN where no data."""

    def __init__(self, x0: float, y0: float, res: float, z: np.ndarray, kind: str = "", sources=None):
        self.x0, self.y0, self.res, self.z, self.kind = float(x0), float(y0), float(res), z, kind
        self.sources = sources or []

    @property
    def nrows(self) -> int:
        return self.z.shape[0]

    @property
    def ncols(self) -> int:
        return self.z.shape[1]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x0 + self.ncols * self.res, self.y0 + self.nrows * self.res

    def like(self, z: np.ndarray, kind: str = "") -> "Raster":
        return Raster(self.x0, self.y0, self.res, z, kind or self.kind, self.sources)

    def index(self, xs, ys):
        """Row and column of the cells that hold the points, clamped to the grid."""
        c = np.clip(np.floor((np.asarray(xs) - self.x0) / self.res).astype(np.intp), 0, self.ncols - 1)
        r = np.clip(np.floor((np.asarray(ys) - self.y0) / self.res).astype(np.intp), 0, self.nrows - 1)
        return r, c

    def sample(self, xs, ys) -> np.ndarray:
        """Value of the cell that holds each point (no interpolation)."""
        r, c = self.index(xs, ys)
        return self.z[r, c]

    def height(self, xs, ys) -> np.ndarray:
        """Bilinear interpolation between cell centres, clamped at the edge."""
        fx = (np.asarray(xs, dtype=np.float64) - self.x0) / self.res - 0.5
        fy = (np.asarray(ys, dtype=np.float64) - self.y0) / self.res - 0.5
        c0 = np.clip(np.floor(fx).astype(np.intp), 0, self.ncols - 2)
        r0 = np.clip(np.floor(fy).astype(np.intp), 0, self.nrows - 2)
        tx, ty = np.clip(fx - c0, 0, 1), np.clip(fy - r0, 0, 1)
        z = self.z
        return ((z[r0, c0] * (1 - tx) + z[r0, c0 + 1] * tx) * (1 - ty)
                + (z[r0 + 1, c0] * (1 - tx) + z[r0 + 1, c0 + 1] * tx) * ty)

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        """x of every column centre and y of every row centre."""
        return (self.x0 + (np.arange(self.ncols) + 0.5) * self.res,
                self.y0 + (np.arange(self.nrows) + 0.5) * self.res)

    def window(self, bbox) -> "Raster":
        """Sub-raster that covers the bbox (snapped outward to cells, clipped to the grid)."""
        c0 = max(0, int(math.floor((bbox[0] - self.x0) / self.res)))
        r0 = max(0, int(math.floor((bbox[1] - self.y0) / self.res)))
        c1 = min(self.ncols, int(math.ceil((bbox[2] - self.x0) / self.res)))
        r1 = min(self.nrows, int(math.ceil((bbox[3] - self.y0) / self.res)))
        return Raster(self.x0 + c0 * self.res, self.y0 + r0 * self.res, self.res, self.z[r0:r1, c0:c1],
                      self.kind, self.sources)

    def to_grid(self) -> _dgm.Grid:
        """The same data as the pure-Python `dgm.Grid`."""
        import array
        z = array.array("f")
        z.frombytes(np.ascontiguousarray(self.z, dtype=np.float32).tobytes())
        return _dgm.Grid(self.x0, self.y0, self.res, self.ncols, self.nrows, z, self.sources)


def polygon_mask(r: Raster, polygons: list[list]) -> np.ndarray:
    """True for cells whose centre lies inside any polygon (even-odd rule, holes respected).
    `polygons` is a list of ring lists, i.e. the coordinates of a GeoJSON MultiPolygon."""
    xs, ys = r.centres()
    out = np.zeros(r.z.shape, dtype=bool)
    for rings in polygons:
        pts = [p for ring in rings for p in ring]
        c0 = max(0, int((min(p[0] for p in pts) - r.x0) / r.res) - 1)
        c1 = min(r.ncols, int((max(p[0] for p in pts) - r.x0) / r.res) + 2)
        r0 = max(0, int((min(p[1] for p in pts) - r.y0) / r.res) - 1)
        r1 = min(r.nrows, int((max(p[1] for p in pts) - r.y0) / r.res) + 2)
        if c0 >= c1 or r0 >= r1:
            continue
        # scanline: per row, the x of every edge crossing; cells between crossing pairs are inside
        a = np.array([p for ring in rings for p in zip(ring, ring[1:] + ring[:1])], dtype=np.float64)
        ax, ay, bx, by = a[:, 0, 0], a[:, 0, 1], a[:, 1, 0], a[:, 1, 1]
        keep = ay != by
        ax, ay, bx, by = ax[keep], ay[keep], bx[keep], by[keep]
        cx = xs[c0:c1]
        for row in range(r0, r1):
            y = ys[row]
            hit = (ay > y) != (by > y)
            if not hit.any():
                continue
            xc = np.sort((bx[hit] - ax[hit]) * (y - ay[hit]) / (by[hit] - ay[hit]) + ax[hit])
            # a centre is inside when an odd number of crossings lies to its right
            out[row, c0:c1] |= (len(xc) - np.searchsorted(xc, cx, side="right")) % 2 == 1
    return out


def polygons_of(geometry: dict | None) -> list[list]:
    """Ring lists of a GeoJSON Polygon or MultiPolygon (empty for anything else)."""
    if not geometry:
        return []
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return list(geometry["coordinates"])
    return []


# ---------------------------------------------------------------- tiles


def _tile_meta(zf: zipfile.ZipFile, stem: str) -> dict:
    """Flight date and last update from the tile's .csv (semicolon separated, one data row)."""
    try:
        head, row = zf.read(stem + ".csv").decode("utf-8", "replace").splitlines()[:2]
        d = dict(zip(head.split(";"), row.split(";")))
        return {"befliegung": d.get("Aktualitaet"), "fortfuehrung": d.get("Fortfuehrung"),
                "genauigkeit_m": float(d["Genauigkeit"]) if d.get("Genauigkeit") else None}
    except (KeyError, ValueError):
        return {}


def read_tile(zf: zipfile.ZipFile, km_e: int, km_n: int) -> tuple[np.ndarray, dict]:
    """One 1 km tile as a 1000 x 1000 array (row 0 south, 1 m cells) plus its metadata."""
    tag = f"_{km_e}_{km_n}_1_"
    names = [m for m in zf.namelist() if tag in m and m.endswith((".xyz", ".tif"))]
    if not names:
        raise RuntimeError(f"{zf.filename} holds no tile for {km_e}/{km_n}")
    name = names[0]
    if name.endswith(".xyz"):
        a = np.loadtxt(zf.open(name), dtype=np.float64)
        tile = np.full((1000, 1000), np.nan, dtype=np.float32)
        tile[(a[:, 1] - km_n * 1000).astype(np.intp), (a[:, 0] - km_e * 1000).astype(np.intp)] = a[:, 2]
    else:
        from PIL import Image  # pillow; only the surface model needs it
        t = [float(v) for v in zf.read(name[:-4] + ".tfw").split()]
        if abs(t[0] - 1.0) > 1e-9 or abs(t[4] - (km_e * 1000 + 0.5)) > 1e-6:
            raise RuntimeError(f"{name}: unexpected georeference {t}")
        tile = np.array(Image.open(io.BytesIO(zf.read(name))), dtype=np.float32)[::-1].copy()
        tile[tile < -100] = np.nan
    return tile, {"tile": name.rsplit("/", 1)[-1], **_tile_meta(zf, name[:-4])}


def load(kind: str, bbox, res: float, source_dir: Path, download: bool = False, progress=None) -> Raster:
    """Raster of `kind` ("dgm" or "dom") for the bbox, snapped outward to multiples of `res`
    (whole metres). Tiles live in <source_dir>/<kind>/opendata, the resampled grid is cached
    in <source_dir>/<kind>/grid_cache."""
    if kind not in KINDS:
        raise ValueError(f"unknown raster kind {kind!r}")
    k = int(round(res))
    if k < 1 or abs(res - k) > 1e-9:
        raise ValueError("res must be a whole number of metres (the tiles have 1 m cells)")
    x0, y0 = math.floor(bbox[0] / k) * k, math.floor(bbox[1] / k) * k
    ncols, nrows = math.ceil((bbox[2] - x0) / k), math.ceil((bbox[3] - y0) / k)
    tile_dir, cache_dir = Path(source_dir) / kind / "opendata", Path(source_dir) / kind / "grid_cache"
    stem = f"r_{kind}_{x0}_{y0}_{ncols}x{nrows}_{k}m"
    meta_fp, npy_fp = cache_dir / f"{stem}.json", cache_dir / f"{stem}.npy"
    if meta_fp.exists() and npy_fp.exists():
        return Raster(x0, y0, k, np.load(npy_fp), kind, json.loads(meta_fp.read_text(encoding="utf-8"))["sources"])
    x1, y1 = x0 + ncols * k, y0 + nrows * k
    fine = np.full((nrows * k, ncols * k), np.nan, dtype=np.float32)
    sources = []
    for km_e in range(x0 // 1000, math.ceil(x1 / 1000)):
        for km_n in range(y0 // 1000, math.ceil(y1 / 1000)):
            zp = _dgm.ensure_zip(tile_dir, km_e, km_n, download, kind)
            with zipfile.ZipFile(zp) as zf:
                tile, meta = read_tile(zf, km_e, km_n)
            if progress:
                progress(meta["tile"])
            e, n = _dgm.zip_block(km_e, km_n)
            sources.append({"zip": zp.name, "url": KINDS[kind]["url"].format(e=e, n=n), **meta})
            cx0, cy0 = max(x0, km_e * 1000), max(y0, km_n * 1000)
            cx1, cy1 = min(x1, km_e * 1000 + 1000), min(y1, km_n * 1000 + 1000)
            fine[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = tile[cy0 - km_n * 1000:cy1 - km_n * 1000,
                                                              cx0 - km_e * 1000:cx1 - km_e * 1000]
    if k == 1:
        z = fine
    else:
        blocks = fine.reshape(nrows, k, ncols, k)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                z = (np.nanmean if KINDS[kind]["agg"] == "mean" else np.nanmax)(blocks, axis=(1, 3))
        z = z.astype(np.float32)
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(npy_fp, z)
    meta_fp.write_text(json.dumps({"bbox": [x0, y0, x1, y1], "res": k, "kind": kind, "sources": sources}, indent=1),
                       encoding="utf-8")
    return Raster(x0, y0, k, z, kind, sources)


def flight_dates(r: Raster) -> list[str]:
    """Distinct flight dates of the tiles behind a raster, sorted."""
    return sorted({s["befliegung"] for s in r.sources if s.get("befliegung")})

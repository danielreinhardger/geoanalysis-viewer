"""Derive relief maps (normal + ambient occlusion) for the heap textures of the 3D viewer from their
colour images, so the scenario heap shows small-scale relief under the sun.

    python tools/textures/make_pbr.py [--kind betrieb|rekultiviert ...] [--relief-cm 4]

Reads viewer/textures/heap_<kind>.webp (already seamless, made by make_tileable.py) and writes
viewer/textures/heap_<kind>_nao.webp: R, G = tangent-space normal x, y (z is rebuilt in the shader),
B = ambient occlusion. The entry "<kind>_nao" in textures.json records it as derived, not as a new image.

How: the height is guessed from the brightness of the colour image (bright = raised; an assumption that
holds roughly for rubble pieces and grass tufts, not a measurement), band-passed so only detail of a few
centimetres to about half a metre is kept. All filters wrap around the tile edges (FFT), so the maps stay
seamless. Slopes of that height give the normal; how far a pixel lies below its surroundings gives the
occlusion. `--relief-cm` is the assumed standard deviation of that relief.

The texture covers TEX_M = 24 m (viewer.js), so one pixel of a 1024 image is 2.3 cm. Needs numpy, Pillow.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "viewer" / "textures"
TEX_M = 24.0                     # metres per texture tile, as TEX_M in viewer/viewer.js
RELIEF_CM = {"betrieb": 4.0, "rekultiviert": 2.0}   # assumed relief (std. dev.), see the docstring


def blur(a, sigma_px):
    """Gaussian blur with wrap-around (FFT), so a seamless tile stays seamless."""
    import numpy as np
    h, w = a.shape
    fy, fx = np.fft.fftfreq(h)[:, None], np.fft.fftfreq(w)[None, :]
    g = np.exp(-2 * (np.pi * sigma_px) ** 2 * (fx * fx + fy * fy))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * g))


def relief_maps(rgb, relief_m, px_m):
    """rgb: H x W x 3 floats 0..1 (sRGB). Returns (nx, ny, ao) arrays: normal x, y in -1..1 in texture
    space (x east = +u, y = +v = up in the image as the viewer loads it), ao 0..1."""
    import numpy as np
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    lum = lin @ np.array([0.2126, 0.7152, 0.0722])
    # band-pass: drop pixel noise (< 1.5 px) and broad brightness zones (> about 0.5 m)
    h = blur(lum, 1.2) - blur(lum, 0.25 / px_m)
    h = h / (h.std() + 1e-9) * relief_m                       # metres
    dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) / (2 * px_m)
    drow = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) / (2 * px_m)
    # image rows run downwards, v upwards: dh/dv = -dh/drow
    nx, ny, nz = -dx, drow, np.ones_like(h)
    n = np.sqrt(nx * nx + ny * ny + nz * nz)
    # occlusion: depth below the mean of the surroundings (about 10 cm and 40 cm), scaled to the relief
    cav = 0.6 * (blur(h, 0.1 / px_m) - h) + 0.4 * (blur(h, 0.4 / px_m) - h)
    ao = np.clip(1 - np.maximum(cav, 0) / (2.5 * relief_m), 0.35, 1)
    return nx / n, ny / n, ao


def fold(a, pad, n):
    """A tile of n pixels from an estimate made on the tile padded by `pad` pixels with wrap-around:
    where the padding repeats a part of the tile, both estimates are averaged, each weighted by its
    distance from the border of the padded image, so the result is seamless. One axis at a time."""
    import numpy as np
    for axis in (0, 1):
        m = a.shape[axis]
        idx = np.arange(m)
        wgt = np.clip(np.minimum(idx + 0.5, m - idx - 0.5) / pad, 1e-3, 1)   # 0 at the border, 1 from pad inwards
        acc = np.zeros((n,) + a.shape[1:] if axis == 0 else (a.shape[0], n) + a.shape[2:])
        wsum = np.zeros(n)
        for i in range(m):
            t = (i - pad) % n
            sl = a[i] if axis == 0 else a[:, i]
            if axis == 0:
                acc[t] += wgt[i] * sl
            else:
                acc[:, t] += wgt[i] * sl
            wsum[t] += wgt[i]
        a = acc / (wsum[:, None, None] if axis == 0 else wsum[None, :, None])
    return a


def from_normals(img, pad, n, flip_x, flip_y, px_m):
    """Normals estimated by a model (RGB image, 0.5 = flat) to (nx, ny, ao): folded into a seamless tile,
    the tilt of the whole picture removed (the shape of the heap comes from the mesh), height integrated
    with the periodic Frankot-Chellappa method, occlusion from the depth below the surroundings."""
    import numpy as np
    nrm = img * 2 - 1
    nrm = fold(nrm, pad, n)
    nx, ny, nz = nrm[..., 0] * (-1 if flip_x else 1), nrm[..., 1] * (-1 if flip_y else 1), np.clip(nrm[..., 2], 0.2, 1)
    p, q = -nx / nz, -ny / nz                      # slopes dh/du, dh/dv (v up)
    p -= blur(p, 0.5 / px_m); q -= blur(q, 0.5 / px_m)   # keep detail below about half a metre
    fy, fx = np.fft.fftfreq(n)[:, None], np.fft.fftfreq(n)[None, :]
    den = (2 * np.pi) ** 2 * (fx * fx + fy * fy); den[0, 0] = 1
    # rows run downwards, v upwards: d/drow = -d/dv
    H = (-1j * 2 * np.pi * fx * np.fft.fft2(p) + 1j * 2 * np.pi * fy * np.fft.fft2(q)) / den
    H[0, 0] = 0
    h = np.real(np.fft.ifft2(H)) * px_m             # metres
    cav = 0.6 * (blur(h, 0.1 / px_m) - h) + 0.4 * (blur(h, 0.4 / px_m) - h)
    ao = np.clip(1 - np.maximum(cav, 0) / (2.5 * max(h.std(), 1e-4)), 0.35, 1)
    l = np.sqrt(p * p + q * q + 1)
    return -p / l, -q / l, ao, h


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", action="append", choices=sorted(RELIEF_CM))
    ap.add_argument("--relief-cm", type=float, help="override the assumed relief for all kinds")
    ap.add_argument("--normals", help="normal image from a model (e.g. ComfyUI, Marigold V2) for the one --kind, "
                                      "made from the tile padded with wrap-around")
    ap.add_argument("--pad", type=int, default=256, help="padding of the --normals input, pixels")
    ap.add_argument("--flip-x", action="store_true", help="the model's red channel points west")
    ap.add_argument("--flip-y", action="store_true", help="the model's green channel points down in the image")
    ap.add_argument("--source", help="model, workflow and date for --normals, shown in the viewer")
    a = ap.parse_args(argv)
    import numpy as np
    from PIL import Image
    log = OUT / "textures.json"
    rec = json.loads(log.read_text(encoding="utf-8"))
    if a.normals and (not a.kind or len(a.kind) != 1 or not a.source):
        ap.error("--normals needs exactly one --kind and --source")
    for kind in a.kind or [k for k in RELIEF_CM if k in rec]:
        src = OUT / rec[kind]["file"]
        rgb = np.asarray(Image.open(src).convert("RGB"), dtype=np.float64) / 255
        n = rgb.shape[1]
        if a.normals:
            mi = Image.open(a.normals).convert("RGB")
            if mi.size != (n + 2 * a.pad, n + 2 * a.pad):
                mi = mi.resize((n + 2 * a.pad, n + 2 * a.pad), Image.LANCZOS)
            nx, ny, ao, h = from_normals(np.asarray(mi, dtype=np.float64) / 255, a.pad, n, a.flip_x, a.flip_y, TEX_M / n)
            print(f"{kind}: relief std {h.std() * 100:.1f} cm (from the model, scale unchecked)")
            source = f"{a.source}; Höhe daraus integriert, tools/textures/make_pbr.py"
        else:
            relief = (a.relief_cm or RELIEF_CM[kind]) / 100
            nx, ny, ao = relief_maps(rgb, relief, TEX_M / n)
            source = (f"abgeleitet aus {rec[kind]['file']} (Helligkeit als Höhe, Annahme: hell = erhaben; "
                      f"angenommene Rauheit {relief * 100:g} cm), tools/textures/make_pbr.py")
        img = np.stack([(nx + 1) / 2, (ny + 1) / 2, ao], axis=-1)
        name = f"heap_{kind}_nao.webp"
        Image.fromarray(np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)).save(OUT / name, "WEBP", quality=90, method=6)
        rec[f"{kind}_nao"] = {
            "file": name, "data": True, "derived_from": rec[kind]["file"], "source": source,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "synthetic": True,
        }
        if a.normals:
            rec[f"{kind}_nao"]["input"] = Path(a.normals).name
        print(OUT / name)
    log.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

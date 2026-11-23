"""Grayscale detail tiles for the heap's material map, from generated images (ComfyUI, FLUX 3 klein).

The viewer draws the heap from a material map it bakes itself: colours measured on aerial images,
layout assumed. This tool adds the light/dark structure of a few metres (tyre lanes, dumped heaps, rills)
that the default view can still resolve (about 1-2.5 m per screen pixel): each image is cropped,
made seamless, reduced to its local contrast (luminance divided by a blurred copy, so the large-scale
tone stays with the map) and stored at about the map's own resolution, so nothing finer than the view
can show is kept.

    python tools/textures/make_detail.py --tile top IMAGE --metres 160 --crop 0.06,0.06,0.94,0.94 \
        --source "..." [--tile loose ...] [--tile slope ... --height 1 --wrap x]

Writes viewer/textures/heap_detail.json: {"mpp", "tiles": {name: {"w", "h", "mw", "mh", "wrap",
"data" (base64 bytes, 128 = no change), "source", "created"}}}. `export-web` puts it into scene.json.
The heap is a scenario, not a measured object, so synthetic structure is allowed there and labelled;
never on the terrain, trees or buildings. Needs numpy and Pillow.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "viewer" / "textures" / "heap_detail.json"
MPP = 0.8   # metres per stored pixel: about the map's resolution (1.67 m grid, 2 map pixels per cell)


def seamless_x(a, band=0.2):
    """Cross-fade with a copy shifted by half along x (as make_tileable.tileable, one axis)."""
    import numpy as np
    n = a.shape[1]
    d = np.minimum(np.arange(n) + 0.5, n - np.arange(n) - 0.5) / n
    t = np.clip(d / band, 0, 1)
    w = (t * t * (3 - 2 * t))[None, :]
    return a * w + np.roll(a, n // 2, axis=1) * (1 - w)


def detail(img, w, h, wrap, blur_frac=0.12, spread=0.22):
    """Local contrast of the luminance, resized to w x h, as bytes around 128."""
    import numpy as np
    from PIL import Image
    lum = np.asarray(img.convert("L"), dtype=np.float64) / 255.0
    if "x" in wrap:
        lum = seamless_x(lum)
    if "y" in wrap:
        lum = seamless_x(lum.T).T
    small = Image.fromarray(np.clip(lum * 255, 0, 255).astype(np.uint8)).resize((w, h), Image.LANCZOS)
    a = np.asarray(small, dtype=np.float64) + 1.0
    rad = max(1.0, blur_frac * max(w, h))
    big = np.pad(a, ((0, 0), (w, w)), mode="wrap" if "x" in wrap else "reflect")   # blur across the seams
    big = np.pad(big, ((h, h), (0, 0)), mode="wrap" if "y" in wrap else "reflect")
    x = np.arange(-int(3 * rad), int(3 * rad) + 1)
    k = np.exp(-0.5 * (x / rad) ** 2); k /= k.sum()
    big = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, big)
    big = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, big)
    blurred = big[h:2 * h, w:2 * w]
    r = np.log(a / blurred)
    r = r / (r.std() + 1e-9) * spread            # same contrast for every tile; the viewer scales it
    return np.clip(np.round(128 * np.exp(r)), 1, 255).astype(np.uint8)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tile", nargs=2, action="append", metavar=("NAME", "IMAGE"), required=True)
    ap.add_argument("--metres", type=float, action="append", required=True, help="width of the tile on the ground")
    ap.add_argument("--height", type=float, action="append", help="height in metres; 1 = one slope face (no metres)")
    ap.add_argument("--crop", action="append", help="x0,y0,x1,y1 as fractions")
    ap.add_argument("--wrap", action="append", help="xy (default) or x")
    ap.add_argument("--source", action="append", required=True)
    a = ap.parse_args(argv)
    from PIL import Image
    doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"mpp": MPP, "tiles": {}}
    for i, (name, path) in enumerate(a.tile):
        img = Image.open(path).convert("RGB")
        if a.crop and i < len(a.crop) and a.crop[i]:
            x0, y0, x1, y1 = (float(v) for v in a.crop[i].split(","))
            img = img.crop((round(x0 * img.width), round(y0 * img.height), round(x1 * img.width), round(y1 * img.height)))
        mw = a.metres[i]
        hgt = a.height[i] if a.height and i < len(a.height) else None
        mh = hgt if hgt else mw * img.height / img.width
        wrap = a.wrap[i] if a.wrap and i < len(a.wrap) else "xy"
        w = max(8, round(mw / MPP))
        h = max(8, round((mh if hgt != 1 else 32) / MPP))   # a slope face: about 32 m along the slope
        data = detail(img, w, h, wrap)
        doc["tiles"][name] = {"w": w, "h": h, "mw": mw, "mh": mh, "wrap": wrap,
                              "data": base64.b64encode(data.tobytes()).decode("ascii"),
                              "source": a.source[i], "created": time.strftime("%Y-%m-%d")}
        print(name, w, h, mw, mh, wrap)
    doc["mpp"] = MPP
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

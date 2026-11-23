"""Turn a generated image (e.g. from ComfyUI through the Comfy MCP) into a seamless surface texture
for the heap of the 3D viewer, and record where it came from.

The heap is a scenario, not a measured object, so a synthetic texture is fine there. It must never be
used for the terrain, trees or buildings: those show the aerial image of what is really there.

    python tools/textures/make_tileable.py IMAGE --kind betrieb|rekultiviert \
        --source "ComfyUI, model X, prompt '...', seed N" [--size 1024]

Writes viewer/textures/heap_<kind>.webp and adds an entry to viewer/textures/textures.json.
With --macro the image is the large-scale companion (material zones tens of metres wide, repeating every
MACRO_TEX_M m in the viewer): heap_<kind>_macro.webp, entry "<kind>_macro", softened a little and stored
with its mean colour (linear RGB), which the viewer divides by, so it shifts the colour of the fine
texture zone by zone instead of replacing it.
`export-web` copies them into the bundle; the viewer uses them instead of its procedural texture and
names the source under "Quellen". Needs numpy and Pillow.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "viewer" / "textures"
KINDS = ("betrieb", "rekultiviert")   # surface kinds of the viewer's heap ("im Betrieb", "rekultiviert")


def tileable(a, band=0.2):
    """Seamless version of an H x W x C float array. One axis at a time: cross-fade with a copy shifted
    by half along that axis. Near the borders (outer `band` of the size) the shifted copy shows, whose
    rows continue across the wrap; its own seam lies in the middle, where only the original shows.
    Doing the axes one after the other (not as one 2-D product) leaves no seam line, and the narrow
    band keeps the ghosting of the cross-fade to the edges."""
    import numpy as np
    for axis in (1, 0):
        n = a.shape[axis]
        d = np.minimum(np.arange(n) + 0.5, n - np.arange(n) - 0.5) / n    # distance to the edge, 0..0.5
        t = np.clip(d / band, 0, 1)
        w = t * t * (3 - 2 * t)
        w = w.reshape((1, n, 1) if axis == 1 else (n, 1, 1))
        a = a * w + np.roll(a, n // 2, axis=axis) * (1 - w)
    return a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image")
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--source", required=True, help="model, prompt and seed, shown in the viewer")
    ap.add_argument("--size", type=int)
    ap.add_argument("--macro", action="store_true", help="large-scale companion texture (default size 512)")
    a = ap.parse_args(argv)
    size = a.size or (512 if a.macro else 1024)
    import numpy as np
    from PIL import Image
    img = Image.open(a.image).convert("RGB")
    s = min(img.size)
    img = img.crop(((img.width - s) // 2, (img.height - s) // 2, (img.width + s) // 2, (img.height + s) // 2))
    img = img.resize((size, size), Image.LANCZOS)
    if a.macro:   # soften the hard zone borders a generated "camouflage" pattern tends to have
        from PIL import ImageFilter
        img = img.filter(ImageFilter.GaussianBlur(size / 170))
    arr = np.clip(tileable(np.asarray(img, dtype=np.float32)) + 0.5, 0, 255).astype(np.uint8)
    OUT.mkdir(parents=True, exist_ok=True)
    key = f"{a.kind}_macro" if a.macro else a.kind
    name = f"heap_{key}.webp"
    Image.fromarray(arr).save(OUT / name, "WEBP", quality=85, method=6)
    log = OUT / "textures.json"
    rec = json.loads(log.read_text(encoding="utf-8")) if log.exists() else {}
    rec[key] = {"file": name, "source": a.source, "input": Path(a.image).name,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "synthetic": True}
    if a.macro:
        c = arr.reshape(-1, 3) / 255.0
        lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)   # sRGB -> linear, as the GPU decodes
        rec[key]["mean_linear"] = [round(float(v), 4) for v in lin.mean(axis=0)]
    log.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(OUT / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

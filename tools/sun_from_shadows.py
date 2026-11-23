"""Sun direction on the flight day of the aerial image, from the shadows of isolated trees.

The 0.2-m DOP20 is a TrueDOP (no lean of tall objects), so a tree's shadow lies on the ground on the side
away from the sun, about h / tan(elevation) long. The image is averaged around isolated tree tops (stand-in
trees of the export: position and height from DOM1 minus DGM1), in coordinates scaled by each tree's
height, so all shadows add up in one dark lobe; its bearing gives the sun azimuth, its length roughly the
elevation. Rough by design: tree heights are from 2016, the crowns hide the start of the shadow.

    python tools/sun_from_shadows.py [exports/G2_web] [--iso 10]

Result for G2 (21.11.2026): shadows to bearing 15-35° (139 trees at 10 m isolation, 368 at 6 m), so sun
azimuth about 195-215°, elevation about 60° (59-61°): a summer flight around midday. Used for the
viewer's sun (SUN_AZ, SUN_EL in viewer/viewer.js). Needs numpy and Pillow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    import numpy as np
    from PIL import Image
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", nargs="?", default=str(ROOT / "exports" / "G2_web"))
    ap.add_argument("--iso", type=float, default=10.0, help="no other tree top within this many metres")
    ap.add_argument("--min-height", type=float, default=8.0)
    a = ap.parse_args(argv)
    b = Path(a.bundle)
    s = json.loads((b / "scene.json").read_text(encoding="utf-8"))
    det, tr = s.get("ortho_detail"), s.get("trees")
    if not det or not tr:
        raise SystemExit("needs a bundle exported with --surface and --detail-mpp")
    bb, mpp = det["bbox"], det["mpp"]
    img = np.asarray(Image.open(b / det["file"]).convert("L"), dtype=np.float32)
    H, W = img.shape
    raw, n = (b / tr["file"]).read_bytes(), tr["count"]
    x = np.frombuffer(raw, "<i2", n, 0) / 10.0
    y = np.frombuffer(raw, "<i2", n, 2 * n) / 10.0
    h = np.frombuffer(raw, "u1", n, 4 * n) * 0.25
    r = np.frombuffer(raw, "u1", n, 5 * n) * 0.1
    cand = np.where((x > bb[0] + 60) & (x < bb[2] - 60) & (y > bb[1] + 60) & (y < bb[3] - 60) & (h >= a.min_height))[0]
    keep = []
    for i in cand:
        d = np.hypot(x - x[i], y - y[i])
        d[i] = 1e9
        if d.min() > a.iso:
            keep.append(i)
    U = np.arange(-3, 3.001, 0.05)
    gu, gv = np.meshgrid(U, U)   # east, north, in units of tree height
    acc, cnt = np.zeros_like(gu), 0
    rad = np.hypot(gu, gv)
    for i in keep:
        c = ((x[i] + gu * h[i] - bb[0]) / mpp).astype(int)
        rr = ((bb[3] - (y[i] + gv * h[i])) / mpp).astype(int)
        if c.min() < 0 or rr.min() < 0 or c.max() >= W or rr.max() >= H:
            continue
        p = img[rr, c]
        acc += p / p[rad > 2.5].mean()
        cnt += 1
    if not cnt:
        raise SystemExit("no isolated trees inside the near-area image")
    dark = np.clip(1 - acc / cnt, 0, None)
    w = dark * (rad > 0.25)   # leave out the crown centre
    bearing = float(np.degrees(np.arctan2((w * gu).sum(), (w * gv).sum())) % 360)
    ux, uy = np.sin(np.radians(bearing)), np.cos(np.radians(bearing))
    prof = np.array([dark[int(round((t * uy + 3) / 0.05)), int(round((t * ux + 3) / 0.05))] for t in np.arange(0, 3, 0.05)])
    tip = float(np.arange(0, 3, 0.05)[prof > prof.max() / 2].max())
    crown = float(np.median(r[keep] / h[keep]))
    elev = float(np.degrees(np.arctan(1 / max(1e-3, tip - crown))))
    print(f"isolated trees: {len(keep)} of {len(cand)}, used {cnt}")
    print(f"shadow bearing {bearing:.0f}° -> sun azimuth {(bearing + 180) % 360:.0f}°; "
          f"shadow tip {tip:.2f} h, crown {crown:.2f} h -> sun elevation about {elev:.0f}° (rough)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

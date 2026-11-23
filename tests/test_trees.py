import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import numpy as np
    import scipy  # noqa: F401
    from geoanalysis_viewer import trees
except ImportError:   # numpy/scipy are optional for the package
    trees = None


def bump(shape, r, c, h, sigma):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return h * np.exp(-((yy - r) ** 2 + (xx - c) ** 2) / (2 * sigma ** 2))


@unittest.skipIf(trees is None, "numpy/scipy not installed")
class Tops(unittest.TestCase):
    def test_one_top_per_crown_at_measured_height(self):
        a = np.maximum(bump((60, 60), 15, 15, 12.0, 2.5), bump((60, 60), 40, 42, 22.0, 3.5)).astype(np.float32)
        rows, cols, h, rad = trees.find_tops(a, np.zeros(a.shape, bool))
        got = sorted(zip(rows.tolist(), cols.tolist()))
        self.assertEqual(got, [(15, 15), (40, 42)])
        self.assertAlmostEqual(float(h[list(rows).index(40)]), 22.0, places=3)   # raw height, not smoothed
        self.assertTrue(all(trees.CROWN_R_MIN <= r <= trees.CROWN_R_MAX for r in rad))
        # a wider crown gets the larger radius
        self.assertGreater(float(rad[list(rows).index(40)]), float(rad[list(rows).index(15)]))

    def test_buildings_and_low_objects_give_no_tree(self):
        a = np.maximum(bump((50, 50), 10, 10, 9.0, 2.0), bump((50, 50), 35, 35, 2.0, 2.0)).astype(np.float32)
        bld = np.zeros(a.shape, bool)
        bld[5:16, 5:16] = True
        rows, _, _, _ = trees.find_tops(a, bld)
        self.assertEqual(len(rows), 0)   # one on a roof, one below MIN_TREE_M

    def test_flat_ground_has_no_tops(self):
        rows, _, _, _ = trees.find_tops(np.zeros((20, 20), np.float32), np.zeros((20, 20), bool))
        self.assertEqual(len(rows), 0)


class Avif(unittest.TestCase):
    """The single file embeds the optional late images as AVIF, the main aerial image as WebP."""

    def test_late_images_avif_main_webp(self):
        try:
            from PIL import Image, features
        except ImportError:
            self.skipTest("Pillow not installed")
        if not (features.check("avif") and features.check("webp")):
            self.skipTest("Pillow without AVIF/WebP")
        from geoanalysis_viewer import singlefile
        with tempfile.TemporaryDirectory() as d:
            b = Path(d)
            shutil.copytree(ROOT / "viewer", b, dirs_exist_ok=True, ignore=shutil.ignore_patterns("standalone"))
            (b / "scene.json").write_text(json.dumps({"terrain": {"ncols": 2, "nrows": 2}}), encoding="utf-8")
            jpg = io.BytesIO()
            Image.new("RGB", (64, 64), (90, 120, 60)).save(jpg, "JPEG")
            for name in ("ortho_0_0.jpg", "ortho_detail.jpg", "ringimg_0_0.jpg"):
                (b / name).write_bytes(jpg.getvalue())
            html = singlefile.single_html(b)
        files = json.loads(re.search(r"const __files = (.*?);\n", html).group(1))
        self.assertEqual(files["ortho_0_0.jpg"]["t"], "image/webp")
        self.assertEqual(files["ortho_detail.jpg"]["t"], "image/avif")
        self.assertEqual(files["ringimg_0_0.jpg"]["t"], "image/avif")
        im = Image.open(io.BytesIO(singlefile.embedded(html, "ortho_detail.jpg")))
        self.assertEqual(im.size, (64, 64))


class HeapProfile(unittest.TestCase):
    """heapProfile in viewer/viewer.js (run with node, if installed)."""

    def test_berms(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        src = (ROOT / "viewer" / "viewer.js").read_text(encoding="utf-8")
        fn = re.search(r"function heapProfile\(.*?\n}\n", src, re.S).group(0)
        cases = [[30, 30, 3, 10, 5], [35, 30, 3, 10, 5], [36, 30, 3, 10, 5], [1000, 30, 3, 10, 5],
                 [60, 30, 3, 0, 0], [-1, 30, 3, 10, 5], [200, 30, 3, 0, 0]]
        js = fn + f"console.log(JSON.stringify({json.dumps(cases)}.map(a => heapProfile(...a))));"
        out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
        # rise 10 m over 30 m, flat 5 m berm, rise again; capped at H; no berms: plain 1:3; outside: 0
        for got, want in zip(out, [10, 10, 10 + 1 / 3, 30, 20, 0, 30]):
            self.assertAlmostEqual(got, want, places=9)


if __name__ == "__main__":
    unittest.main()

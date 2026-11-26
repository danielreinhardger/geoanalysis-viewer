import array
import math
import struct
import unittest
from pathlib import Path

from geoanalysis_viewer import dgm, singlefile, webscene


class EncodeHeights(unittest.TestCase):
    def test_cm_above_minimum_row0_south(self):
        g = dgm.plane(0, 0, 5, 3, 2, lambda x, y: 300 + y)  # rises to the north
        body, z_min, z_max = webscene.encode_heights(g)
        vals = struct.unpack("<6H", body)
        self.assertEqual(z_min, 302.5)
        self.assertEqual(z_max, 307.5)
        self.assertEqual(vals, (0, 0, 0, 500, 500, 500))  # first row = southern row

    def test_nodata_becomes_minimum(self):
        g = dgm.Grid(0, 0, 1, 2, 1, array.array("f", [math.nan, 10.0]))
        body, z_min, _ = webscene.encode_heights(g)
        self.assertEqual(struct.unpack("<2H", body), (0, 0))
        self.assertEqual(z_min, 10.0)

    def test_range_too_large(self):
        g = dgm.Grid(0, 0, 1, 2, 1, array.array("f", [0.0, 700.0]))
        with self.assertRaises(ValueError):
            webscene.encode_heights(g)


class GeometryParts(unittest.TestCase):
    BOX = (0, 0, 100, 100)

    def test_polygon_local_coordinates(self):
        g = {"type": "Polygon", "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 10]]]}
        p = webscene.geometry_parts(g, 5, 5, self.BOX)
        self.assertEqual(p["polygons"], [[[[5, 5], [15, 5], [15, 15], [5, 5]]]])

    def test_lines_points_and_multi(self):
        ln = webscene.geometry_parts({"type": "MultiLineString", "coordinates": [[[1, 1], [2, 2]], [[500, 500], [600, 600]]]},
                                     0, 0, self.BOX)
        self.assertEqual(len(ln["lines"]), 1)  # the far part is dropped
        pt = webscene.geometry_parts({"type": "Point", "coordinates": [50, 50]}, 0, 0, self.BOX)
        self.assertEqual(pt["points"], [[50, 50]])
        mp = webscene.geometry_parts({"type": "MultiPolygon", "coordinates": [
            [[[1, 1], [2, 1], [2, 2], [1, 1]]], [[[900, 900], [901, 900], [901, 901], [900, 900]]]]}, 0, 0, self.BOX)
        self.assertEqual(len(mp["polygons"]), 1)

    def test_missing_geometry(self):
        self.assertEqual(webscene.geometry_parts(None, 0, 0, self.BOX), {"polygons": [], "lines": [], "points": []})


class StatusIndex(unittest.TestCase):
    def test_ambiguous_names_are_left_out(self):
        rep = {"results": [
            {"layer": "A", "name": "x", "status": "ueberlappt", "overlap_m2": 3, "distance_m": 0.0},
            {"layer": "A", "name": "y", "status": "getrennt"},
            {"layer": "A", "name": "y", "status": "ueberlappt"},
        ]}
        idx = webscene._status_index(rep)
        self.assertIn(("A", "x"), idx)
        self.assertNotIn(("A", "y"), idx)

    def test_shared_name_same_status_keeps_status_without_figures(self):
        rep = {"results": [
            {"layer": "A", "name": "z", "status": "getrennt", "overlap_m2": 0, "distance_m": 10.0},
            {"layer": "A", "name": "z", "status": "getrennt", "overlap_m2": 0, "distance_m": 900.0},
            {"layer": "A", "name": "u", "status": "ueberlappt", "overlap_m2": 5, "distance_m": 0.0},
        ]}
        idx = webscene._status_index(rep)
        self.assertEqual(idx[("A", "z")], {"status": "getrennt"})
        self.assertEqual(idx[("A", "u")], {"status": "ueberlappt", "overlap_m2": 5, "distance_m": 0.0})


class LayerCredit(unittest.TestCase):
    def test_service_layer(self):
        fc = {"features": [], "meta": {"source": "https://rips-gdi.lubw.baden-wuerttemberg.de/x/query",
                                       "requests": [{"fetched_at": "2026-11-13T18:29:49+0100"}]}}
        self.assertEqual(webscene._layer_credit("X", fc),
                         ("LUBW, Kartendienst rips-gdi", "2026-11-13T18:29:49+0100", "abgerufen"))

    def test_own_product_is_not_credited_to_an_authority(self):
        fc = {"features": [{"properties": {"erstellt": "2026-11-14T18:55:47+01:00"}}]}
        who, when, kind = webscene._layer_credit("zufahrt_routen", fc)
        self.assertIn("eigene Auswertung", who)
        self.assertEqual((when, kind), ("2026-11-14T18:55:47+01:00", "erstellt"))


if __name__ == "__main__":
    unittest.main()


class SingleFile(unittest.TestCase):
    """`singlefile.single_html` packs a bundle into one page that needs no server."""

    def test_pack(self):
        import gzip
        import json
        import re
        import shutil
        import tempfile
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        from geoanalysis_viewer import webscene
        root = Path(__file__).resolve().parents[1]
        nc, nr = 30, 20
        cm = (np.add.outer(np.arange(nr) * 37, np.arange(nc) * 13) + 1234).astype("<u2")
        with tempfile.TemporaryDirectory() as d:
            b = Path(d)
            shutil.copytree(root / "viewer", b, dirs_exist_ok=True, ignore=shutil.ignore_patterns("standalone"))
            (b / "scene.json").write_text(json.dumps({"terrain": {"ncols": nc, "nrows": nr}}), encoding="utf-8")
            (b / "terrain.bin").write_bytes(cm.tobytes())
            (b / "ortho_0_0.jpg").write_bytes(b"\xff\xd8\xff not a real jpeg")   # unreadable: stays JPEG
            html = singlefile.single_html(b)
        for ref in ('src="viewer.js"', 'href="viewer.css"', "./vendor/", "'./three.core.js'", "importmap"):
            self.assertNotIn(ref, html)
        files = json.loads(re.search(r"const __files = (.*?);\n", html).group(1))
        self.assertEqual(set(files), {"three.core.js", "three.module.js", "OrbitControls.js", "viewer.js",
                                      "scene.json", "terrain.bin", "ortho_0_0.jpg"})
        viewer = gzip.decompress(singlefile.embedded(html, "viewer.js")).decode("utf-8")
        self.assertIn('from "__THREE__"', viewer)
        self.assertIn('from "__ORBIT__"', viewer)
        self.assertEqual(files["ortho_0_0.jpg"]["t"], "image/jpeg")
        # heights: whole decimetres, predicted from the neighbours; decoded within 5 cm of the original
        h = files["terrain.bin"]["h"]
        self.assertEqual((h["w"], h["n"], h["p"], h["k"]), (nc, nc * nr, 1, 10))
        back = singlefile.unheights(gzip.decompress(singlefile.embedded(html, "terrain.bin")), nc, True) * 10
        self.assertLessEqual(int(np.abs(back - cm.astype(np.int64)).max()), 5)
        self.assertIn("MIT", html[:3000])
        # the embedded text holds no character the HTML parser would change or that could end the element
        for m in re.finditer(r'<script type="application/octet-stream" id="[^"]+">(.*?)</script>', html, re.S):
            self.assertFalse(set(m.group(1)) & set("\0\n\r<"))
        self.assertEqual(singlefile.embedded(html, "ortho_0_0.jpg"), b"\xff\xd8\xff not a real jpeg")

    def test_heights_roundtrip(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        rng = np.random.default_rng(1)
        a = np.cumsum(rng.integers(-300, 300, (7, 9)), axis=1) + 4000   # some residuals need the 3-byte escape
        for predict in (True, False):
            self.assertTrue(np.array_equal(singlefile.unheights(singlefile._heights(a, predict), 9, predict), a))

    def test_surface_only_where_read(self):
        """The surface is kept only where the viewer reads it; elsewhere it decodes to the terrain."""
        import gzip
        import re
        import tempfile
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        root = Path(__file__).resolve().parents[1]
        tall = re.search(r"const TALL_CM = (\d+);", (root / "viewer" / "viewer.js").read_text(encoding="utf-8"))
        self.assertEqual(int(tall.group(1)), singlefile.SURFACE_MIN_CM)   # the two must stay equal
        nc, nr = 4, 2
        cm = np.full((nr, nc), 30000, "<u2")
        sm = cm + np.array([[0, 150, 200, 210], [1500, 30, 0, 9000]], "<u2")
        with tempfile.TemporaryDirectory() as d:
            b = Path(d)
            (b / "terrain.bin").write_bytes(cm.tobytes())
            (b / "surface.bin").write_bytes(sm.tobytes())
            f = singlefile._height_files(b, {"terrain": {"ncols": nc, "nrows": nr}, "surface": {"file": "surface.bin"}})
        h = f["surface.bin"]["h"]
        self.assertEqual(h["base"], "terrain.bin")
        above = singlefile.unheights(gzip.decompress(f["surface.bin"]["d"]), nc, False) * h["k"]
        self.assertEqual(above.tolist(), [[0, 0, 0, 210], [1500, 0, 0, 9000]])

    def test_ortho_core(self):
        """The 1-m image: full resolution only round the site and viewpoints, the tiles at half size."""
        import io
        import tempfile
        from unittest import mock
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("pillow not installed")
        # 2 x 2 tiles of 40 x 30 px at 1 m over bbox (0, 0)-(80, 60); row 0 is north: red, row 1 blue
        tiles = [{"file": f"ortho_{r}_{c}.jpg", "row": r, "col": c, "width": 40, "height": 30} for r in (0, 1) for c in (0, 1)]
        scene = {"bbox": [0, 0, 80, 60], "ortho": {"mpp": 1.0, "tiles": tiles},
                 "site": {"rings": [[[60, 40], [65, 40], [65, 45], [60, 45]]]}, "viewpoints": [{"x": 62, "y": 47}]}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(singlefile, "ORTHO_CORE_PAD_M", 5.0):
            b = Path(d)
            for t in tiles:
                Image.new("RGB", (40, 30), (220, 30, 30) if t["row"] == 0 else (30, 30, 220)).save(b / t["file"], "JPEG")
            f = singlefile._ortho_files(b, scene)
        if not f:
            self.skipTest("no WebP support in pillow")
        c = scene["ortho"]["core"]
        # x 60-5 .. 65+5 m, y (north at the top) 60-(47+5) .. 60-(40-5) m
        self.assertEqual((c["x"], c["y"], c["width"], c["height"]), (55, 8, 16, 18))
        self.assertEqual((c["mpp"], c["outer_mpp"]), (1.0, 2.0))
        core = Image.open(io.BytesIO(f["ortho_core.jpg"]["d"])).convert("RGB")
        self.assertEqual(core.size, (16, 18))
        r, g, bl = core.getpixel((8, 9))
        self.assertGreater(r, bl)   # the core lies in the northern (red) row
        for t in tiles:
            self.assertEqual(Image.open(io.BytesIO(f[t["file"]]["d"])).size, (20, 15))




class Tileable(unittest.TestCase):
    def test_edges_meet(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        import importlib.util
        spec = importlib.util.spec_from_file_location("mt", Path(__file__).resolve().parents[1] / "tools" / "textures" / "make_tileable.py")
        mt = importlib.util.module_from_spec(spec); spec.loader.exec_module(mt)
        rng = np.random.default_rng(1)
        a = np.cumsum(np.cumsum(rng.normal(size=(64, 64, 3)), 0), 1).astype(np.float32)   # smooth, not tileable
        t = mt.tileable(a)
        # the jump across the wrap is no larger than an ordinary step inside the image
        inner = np.abs(np.diff(t, axis=1)).mean()
        self.assertLess(np.abs(t[:, 0] - t[:, -1]).mean(), 3 * inner)
        self.assertLess(np.abs(t[0] - t[-1]).mean(), 3 * np.abs(np.diff(t, axis=0)).mean())
        self.assertGreater(np.abs(a[:, 0] - a[:, -1]).mean(), 3 * np.abs(np.diff(a, axis=1)).mean())

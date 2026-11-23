"""Height rasters on synthetic terrain (offline, no tiles needed), and the CLI without numpy."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:  # the vector commands must keep working without numpy
    np = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(np is None, "numpy not installed")
class RasterTest(unittest.TestCase):
    def setUp(self):
        from geoanalysis_viewer import raster
        self.raster = raster
        z = np.add.outer(np.arange(50, dtype=np.float32), np.zeros(80, dtype=np.float32))   # rises to the north
        self.r = raster.Raster(1000.0, 2000.0, 2.0, z)

    def test_sample_and_height(self):
        self.assertEqual(float(self.r.sample(1001.0, 2001.0)), 0.0)
        self.assertEqual(float(self.r.sample(1001.0, 2007.0)), 3.0)
        # between the centres of rows 3 and 4 (y = 2007 and 2009)
        self.assertAlmostEqual(float(self.r.height(1050.0, 2008.0)), 3.5, places=5)
        self.assertEqual(self.r.bbox, (1000.0, 2000.0, 1160.0, 2100.0))

    def test_window_keeps_georeference(self):
        w = self.r.window((1011.0, 2021.0, 1029.0, 2039.0))
        self.assertEqual((w.x0, w.y0, w.ncols, w.nrows), (1010.0, 2020.0, 10, 10))
        self.assertEqual(float(w.sample(1011.0, 2021.0)), float(self.r.sample(1011.0, 2021.0)))

    def test_polygon_mask_area_and_hole(self):
        outer = [[1020, 2020], [1100, 2020], [1100, 2080], [1020, 2080]]
        hole = [[1040, 2040], [1060, 2040], [1060, 2060], [1040, 2060]]
        m = self.raster.polygon_mask(self.r, [[outer, hole]])
        self.assertEqual(int(m.sum()) * 4, 80 * 60 - 20 * 20)
        self.assertFalse(m[self.r.index(1050, 2050)])
        self.assertTrue(m[self.r.index(1030, 2030)])

    def test_to_grid_matches_pure_python_reader(self):
        g = self.r.to_grid()
        self.assertEqual((g.ncols, g.nrows, g.res), (80, 50, 2.0))
        self.assertAlmostEqual(g.height(1050.0, 2008.0), 3.5, places=5)

    def test_polygons_of(self):
        ring = [[0, 0], [1, 0], [1, 1]]
        self.assertEqual(self.raster.polygons_of({"type": "Polygon", "coordinates": [ring]}), [[ring]])
        self.assertEqual(self.raster.polygons_of({"type": "MultiPolygon", "coordinates": [[ring], [ring]]}), [[ring], [ring]])
        self.assertEqual(self.raster.polygons_of(None), [])


class ImportTest(unittest.TestCase):
    def test_cli_loads_without_numpy(self):
        code = ("import sys; sys.modules['numpy'] = None\n"
                "import geoanalysis_viewer.cli, json\n"
                "print(json.dumps(sorted(m for m in sys.modules if m.startswith('geoanalysis_viewer.'))))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        loaded = json.loads(out.stdout)
        self.assertNotIn("geoanalysis_viewer.raster", loaded)
        self.assertNotIn("geoanalysis_viewer.trees", loaded)


if __name__ == "__main__":
    unittest.main()

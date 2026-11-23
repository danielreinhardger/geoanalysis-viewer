import json
import unittest
from pathlib import Path

from geoanalysis_viewer import geom

ROOT = Path(__file__).resolve().parent.parent
SQ = [[0, 0], [100, 0], [100, 100], [0, 100]]


class GeomTest(unittest.TestCase):
    def test_area(self):
        self.assertAlmostEqual(geom.polygon_area([SQ]), 10000)

    def test_pip_hole(self):
        hole = [[40, 40], [60, 40], [60, 60], [40, 60]]
        self.assertTrue(geom.point_in_polygon(10, 10, [SQ, hole]))
        self.assertFalse(geom.point_in_polygon(50, 50, [SQ, hole]))

    def test_distance_exact(self):
        other = [[[110, 0], [120, 0], [120, 10], [110, 10]]]
        self.assertAlmostEqual(geom.boundary_distance([SQ], other), 10)
        # nearest point lies in the middle of a long edge, far from any vertex of the site
        tri = [[[50, 103.3], [40, 200], [60, 200]]]
        self.assertAlmostEqual(geom.boundary_distance([SQ], tri), 3.3)
        self.assertAlmostEqual(geom.boundary_distance(tri, [SQ]), 3.3)

    def test_distance_zero_cases(self):
        inside = [[[40, 40], [60, 40], [60, 60], [40, 60]]]
        self.assertEqual(geom.boundary_distance([SQ], inside), 0.0)
        self.assertEqual(geom.boundary_distance(inside, [SQ]), 0.0)
        # edges cross, but neither polygon has a vertex inside the other
        bar = [[[-10, 40], [110, 40], [110, 60], [-10, 60]]]
        tall = [[[40, -10], [60, -10], [60, 110], [40, 110]]]
        self.assertEqual(geom.boundary_distance(bar, tall), 0.0)
        # two rings: the first far outside, the second wholly inside the square
        two = [[[300, 300], [310, 300], [310, 310]], [[40, 40], [60, 40], [60, 60], [40, 60]]]
        self.assertEqual(geom.boundary_distance([SQ], two), 0.0)
        self.assertEqual(geom.boundary_distance(two, [SQ]), 0.0)
        # shared edge
        touch = [[[100, 20], [150, 20], [150, 80], [100, 80]]]
        self.assertEqual(geom.boundary_distance([SQ], touch), 0.0)

    def test_overlap_rectangle(self):
        other = [[[90, 0], [130, 0], [130, 100], [90, 100]]]
        ov = geom.overlap_stats([SQ], other, res=0.5)
        self.assertAlmostEqual(ov["overlap_m2"], 1000, places=6)
        self.assertAlmostEqual(ov["depth_m"], 10, delta=0.01)

    def test_overlap_triangle(self):
        # triangle with apex inside the square: base outside on the right
        tri = [[[80, 50], [120, 30], [120, 70]]]
        ov = geom.overlap_stats([SQ], tri, res=0.25)
        # part inside x <= 100: triangle from apex (80,50) to the chord x=100 (y 40..60) -> 0.5*20*20
        self.assertAlmostEqual(ov["overlap_m2"], 200, delta=0.5)
        self.assertAlmostEqual(ov["depth_m"], 20, delta=0.3)

    def test_overlap_hole_and_two_rings(self):
        hole = [[40, 40], [60, 40], [60, 60], [40, 60]]
        other = [[[30, 30], [70, 30], [70, 70], [30, 70]]]
        ov = geom.overlap_stats([SQ, hole], other, res=0.5)
        self.assertAlmostEqual(ov["overlap_m2"], 1600 - 400, places=6)
        two = [[[-10, 10], [10, 10], [10, 20], [-10, 20]], [[90, 80], [110, 80], [110, 90], [90, 90]]]
        self.assertAlmostEqual(geom.overlap_stats([SQ], two, res=0.5)["overlap_m2"], 200, places=6)

    def test_overlap_none(self):
        far = [[[200, 0], [300, 0], [300, 100], [200, 100]]]
        self.assertEqual(geom.overlap_stats([SQ], far)["overlap_m2"], 0.0)
        near = [[[100.5, 0], [130, 0], [130, 100], [100.5, 100]]]
        self.assertEqual(geom.overlap_stats([SQ], near)["overlap_m2"], 0.0)

    def test_overlap_matches_grid_oracle(self):
        site = [[[0, 0], [80, 10], [120, 70], [60, 110], [-10, 60]]]
        other = [[[50, -20], [140, 20], [110, 130], [70, 60]], [[80, 20], [100, 30], [90, 50]]]
        new = geom.overlap_stats(site, other, res=0.25)
        old = geom.overlap_stats_grid(site, other, res=0.5)
        self.assertAlmostEqual(new["overlap_m2"], old["overlap_m2"], delta=0.01 * old["overlap_m2"])
        self.assertAlmostEqual(new["depth_m"], old["depth_m"], delta=0.75)
        # row spacing barely matters: the scanline is exact in x
        self.assertAlmostEqual(new["overlap_m2"], geom.overlap_stats(site, other, res=0.05)["overlap_m2"], delta=1.0)

    def test_overlap_large_caps_depth_samples(self):
        big = [[[-1000, -1000], [1000, -1000], [1000, 1000], [-1000, 1000]]]
        ov = geom.overlap_stats([SQ], big, res=0.5, max_depth_samples=400)
        self.assertAlmostEqual(ov["overlap_m2"], 10000, places=6)
        self.assertGreater(ov["depth_res_m"], 0.5)
        self.assertAlmostEqual(ov["depth_m"], 50, delta=ov["depth_res_m"])

    def test_clip(self):
        r = geom.clip_ring_to_bbox(SQ, (50, -10, 200, 200))
        self.assertAlmostEqual(abs(geom.ring_area(r)), 5000)

    @unittest.skipUnless((ROOT / "data" / "G2" / "Biotopkartierung.geojson").exists(), "needs fetched G2 data")
    def test_g2_regression(self):
        """G2 overlaps of 13.11.2026. The old 2 m point grid gave 828 / 444 / 272 m² (error of
        several 10 m² on these narrow strips); the scanline values are stable from 0.5 m down to 0.1 m."""
        site = json.loads((ROOT / "sites" / "g2.geojson").read_text(encoding="utf-8"))
        rings = site["geometry"]["coordinates"]
        fc = json.loads((ROOT / "data" / "G2" / "Biotopkartierung.geojson").read_text(encoding="utf-8"))
        want = {"Bettlenswiesen": (811, 41.8, 828), "Hintere Buchwiesen": (265, 67.5, 272),
                "Eschenbach N Eschenbach": (388, 8.2, 444)}
        found = 0
        for f in fc["features"]:
            for key, (area, depth, old_grid) in want.items():
                if key in f["properties"]["_name"]:
                    ov = geom.overlap_stats(rings, f["geometry"]["coordinates"], res=0.5)
                    if ov["overlap_m2"] == 0:
                        continue
                    found += 1
                    self.assertAlmostEqual(ov["overlap_m2"], area, delta=3)
                    self.assertAlmostEqual(ov["depth_m"], depth, delta=0.5)
                    self.assertAlmostEqual(ov["overlap_m2"], old_grid, delta=60)
        self.assertEqual(found, 3)


if __name__ == "__main__":
    unittest.main()

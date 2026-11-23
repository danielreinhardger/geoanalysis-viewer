"""Offline tests for the pure-Python DGM1 grid: synthetic terrain, no network, no data files."""
import unittest

from geoanalysis_viewer import dgm


class GridTests(unittest.TestCase):
    def test_bilinear_height_on_plane(self):
        g = dgm.plane(0, 0, 5, 40, 40, lambda x, y: 100 + 0.05 * x - 0.02 * y)
        self.assertAlmostEqual(g.height(52.0, 77.0), 100 + 0.05 * 52 - 0.02 * 77, places=3)

    def test_smoothing_keeps_a_plane(self):
        g = dgm.plane(0, 0, 5, 30, 30, lambda x, y: 0.1 * x)
        s = g.smoothed(5)
        self.assertAlmostEqual(s.z[s.idx(15, 15)], g.z[g.idx(15, 15)], places=3)

    def test_tiles_and_zip_block(self):
        self.assertEqual(dgm.tiles_for((548400, 5390250, 549650, 5391350)),
                         [(548, 5390), (548, 5391), (549, 5390), (549, 5391)])
        self.assertEqual(dgm.zip_block(548, 5391), (547, 5390))
        self.assertEqual(dgm.zip_block(549, 5392), (549, 5392))


if __name__ == "__main__":
    unittest.main()

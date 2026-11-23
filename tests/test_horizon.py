import tempfile
import unittest
from pathlib import Path

try:
    import numpy  # noqa: F401
    from geoanalysis_viewer import horizon
except ImportError:   # numpy is optional for the package
    horizon = None


@unittest.skipIf(horizon is None, "numpy not installed")
class Utm32(unittest.TestCase):
    def test_central_meridian(self):
        lon, lat = horizon.utm32_to_lonlat(500000.0, 5400000.0)
        self.assertAlmostEqual(float(lon), 9.0, places=9)
        self.assertTrue(48.7 < float(lat) < 48.8)

    def test_equator(self):
        lon, lat = horizon.utm32_to_lonlat(500000.0, 0.0)
        self.assertAlmostEqual(float(lat), 0.0, places=9)

    def test_symmetric_about_meridian(self):
        a, la = horizon.utm32_to_lonlat(550000.0, 5390000.0)
        b, lb = horizon.utm32_to_lonlat(450000.0, 5390000.0)
        self.assertAlmostEqual(float(a) - 9.0, 9.0 - float(b), places=9)
        self.assertAlmostEqual(float(la), float(lb), places=9)

    def test_g2_region(self):
        # G2 lies near Eschenbach/Heiningen, about 9.67 E, 48.68 N
        lon, lat = horizon.utm32_to_lonlat(549442.0, 5392333.0)
        self.assertAlmostEqual(float(lon), 9.67, delta=0.01)
        self.assertAlmostEqual(float(lat), 48.68, delta=0.01)


@unittest.skipIf(horizon is None, "numpy not installed")
class Ring(unittest.TestCase):
    def test_without_tile_no_ring(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.mkdir()
            (out / "ring.bin").write_bytes(b"old")
            self.assertIsNone(horizon.build_ring(Path(d), None, (0, 0, 1, 1), 0, 0, out))
            self.assertFalse((out / "ring.bin").exists())


if __name__ == "__main__":
    unittest.main()

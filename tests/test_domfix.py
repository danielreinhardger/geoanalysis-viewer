"""Known DOM1 errors: the mask takes only high cells inside the box and spares the kept stack top."""
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from geoanalysis_viewer import domfix


@unittest.skipIf(np is None, "needs numpy")
class ErrorMask(unittest.TestCase):
    def setUp(self):
        self.h = np.full((20, 20), 10.0, dtype=np.float32)    # 1-m cells, lower left corner (100, 200)
        self.h[5:15, 5:15] = 150.0                              # the plume
        self.h[9:11, 9:11] = 100.0                              # stack top inside it
        self.h[0:2, 18:20] = 150.0                              # something high outside the box
        self.c = {"id": "t", "bbox": [104, 204, 116, 216], "above_ground_m": 25,
                  "keep": {"x": 110.0, "y": 210.0, "radius_m": 1.5}}

    def test_plume_cells_inside_box(self):
        m, hit = domfix.error_mask(100, 200, 1.0, self.h, [self.c])
        self.assertEqual(hit, ["t"])
        self.assertTrue(m[5, 5] and m[14, 14])
        self.assertFalse(m[9, 9] or m[10, 10])                  # kept: the stack top
        self.assertFalse(m[0, 18])                              # outside the box
        self.assertFalse(m[3, 3])                               # low cell

    def test_no_hit(self):
        m, hit = domfix.error_mask(100, 200, 1.0, np.zeros((20, 20)), [self.c])
        self.assertEqual(hit, [])
        self.assertFalse(m.any())


if __name__ == "__main__":
    unittest.main()

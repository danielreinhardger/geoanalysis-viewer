import json
import unittest
from pathlib import Path

from geoanalysis_viewer import alkis, lgl_wfs

SQ = [[0, 0], [100, 0], [100, 100], [0, 100]]


class FakeResp:
    def __init__(self, body):
        self.body, self.fetched_at, self.from_cache, self.key = body, "2026-11-13T18:00:00+0100", True, "k"

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class FakeCache:
    """Answers by feature type; records calls."""

    def __init__(self, by_type):
        self.by_type, self.calls = by_type, []

    def request(self, url, params=None, method="GET", layer=None, validate=None, retries=0):
        self.calls.append(params)
        feats = self.by_type.get(params["TYPENAMES"], [])
        page = feats[params["STARTINDEX"]:params["STARTINDEX"] + params["COUNT"]]
        body = json.dumps({"type": "FeatureCollection", "features": page}).encode()
        assert validate is None or validate(body)
        return FakeResp(body)


def _feat(props, ring):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


class AlkisTest(unittest.TestCase):
    def test_residential_rule(self):
        for name in ("Wohnhaus", "Wohnheim", "Wohn- und Geschäftsgebäude", "Bauernhaus", "Seniorenheim"):
            self.assertTrue(alkis.is_residential_building(name), name)
        for name in ("Garage", "Wochenendhaus", "Stall", None):
            self.assertFalse(alkis.is_residential_building(name), name)

    def test_validator(self):
        self.assertTrue(lgl_wfs.is_feature_collection(b'{"type":"FeatureCollection","features":[]}'))
        self.assertFalse(lgl_wfs.is_feature_collection(b"<html>Request Rejected</html>"))

    def test_paging(self):
        feats = [_feat({"objektname": "Weg"}, _box(0, 0, 1, 1)) for _ in range(5)]
        c = FakeCache({alkis.LAND_USE: feats})
        fc = alkis.query(c, alkis.LAND_USE, (0, 0, 10, 10), page_size=2)
        self.assertEqual(len(fc["features"]), 5)
        self.assertEqual([p["STARTINDEX"] for p in c.calls], [0, 2, 4])
        self.assertIn("LGL-BW (2026)", fc["meta"]["attribution"])

    def test_settlement_distance(self):
        site = {"properties": {"id": "T", "tolerance_m": 4.0}, "geometry": {"type": "Polygon", "coordinates": [SQ]}}
        c = FakeCache({
            alkis.LAND_USE: [
                _feat({"objektname": "Wohnbaufläche", "gml_id": "far"}, _box(400, 0, 450, 50)),
                _feat({"objektname": "Wohnbaufläche", "gml_id": "near"}, _box(250, 0, 300, 50)),
                _feat({"objektname": "Fläche gemischter Nutzung", "gml_id": "mix"}, _box(140, 0, 160, 50)),
                _feat({"objektname": "Landwirtschaft", "gml_id": "field"}, _box(101, 0, 120, 50))],
            alkis.BUILDINGS: [
                _feat({"gebaeudefunktion_name": "Garage", "gml_id": "g"}, _box(110, 0, 115, 5)),
                _feat({"gebaeudefunktion_name": "Wohnhaus", "gml_id": "w"}, _box(260, 10, 270, 20))]})
        rep = alkis.settlement_distance(site, c, radius_m=500)
        self.assertEqual(rep["nearest"]["wohnen"]["gml_id"], "near")
        self.assertAlmostEqual(rep["nearest"]["wohnen"]["distance_m"], 150.0)
        self.assertAlmostEqual(rep["nearest"]["gemischt"]["distance_m"], 40.0)
        self.assertEqual([r["gml_id"] for r in rep["results"]], ["mix", "near", "far"])
        self.assertEqual([b["gml_id"] for b in rep["buildings"]], ["w"])
        self.assertEqual([(b["gml_id"], b["distance_m"]) for b in rep["other_buildings_near"]], [("g", 10.0)])
        md = alkis.to_markdown(rep)
        self.assertIn("150.0 m ±4.0 m", md)
        self.assertIn("Quellenvermerk: LGL-BW (2026)", md)


if __name__ == "__main__":
    unittest.main()

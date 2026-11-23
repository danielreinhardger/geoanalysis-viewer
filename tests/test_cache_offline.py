import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from geoanalysis_viewer.cache import CacheMiss, HttpCache, InvalidResponse


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CacheTest(unittest.TestCase):
    def test_offline_miss_raises(self):
        with tempfile.TemporaryDirectory() as d:
            c = HttpCache(Path(d) / "c.sqlite", mode="offline")
            with self.assertRaises(CacheMiss):
                c.request("https://example.invalid/x", {"a": 1})
            c.close()

    def test_key_is_order_independent(self):
        from geoanalysis_viewer.cache import _normalise
        self.assertEqual(_normalise("u", {"a": 1, "b": {"y": 2, "x": 1}}, "get"),
                         _normalise("u", {"b": {"x": 1, "y": 2}, "a": 1}, "GET"))

    def test_invalid_body_is_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            c = HttpCache(Path(d) / "c.sqlite")
            ok = lambda b: b.startswith(b"{")  # noqa: E731
            with mock.patch("urllib.request.urlopen", return_value=_Resp(b"<html>Request Rejected</html>")):
                with self.assertRaises(InvalidResponse):
                    c.request("https://example.invalid/x", {"a": 1}, validate=ok)
            self.assertEqual(c.list(), [])
            with mock.patch("urllib.request.urlopen", return_value=_Resp(b'{"ok": 1}')):
                r = c.request("https://example.invalid/x", {"a": 1}, validate=ok)
            self.assertFalse(r.from_cache)
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                self.assertTrue(c.request("https://example.invalid/x", {"a": 1}, validate=ok).from_cache)
            c.close()

    def test_stored_invalid_body_counts_as_miss(self):
        with tempfile.TemporaryDirectory() as d:
            c = HttpCache(Path(d) / "c.sqlite")
            with mock.patch("urllib.request.urlopen", return_value=_Resp(b"<html>error</html>")):
                c.request("https://example.invalid/x")  # stored: no validator given
            c.mode = "offline"
            with self.assertRaises(CacheMiss):
                c.request("https://example.invalid/x", validate=lambda b: b.startswith(b"{"))
            c.close()

    def test_retry_after_server_error(self):
        with tempfile.TemporaryDirectory() as d:
            c = HttpCache(Path(d) / "c.sqlite")
            err = urllib.error.HTTPError("u", 502, "Bad Gateway", None, io.BytesIO())
            self.addCleanup(err.close)
            with mock.patch("urllib.request.urlopen", side_effect=[err, _Resp(b"fine")]) as u, \
                    mock.patch("time.sleep"):
                self.assertEqual(c.request("https://example.invalid/x", retries=2).body, b"fine")
                self.assertEqual(u.call_count, 2)
            err404 = urllib.error.HTTPError("u", 404, "Not Found", None, io.BytesIO())
            self.addCleanup(err404.close)
            with mock.patch("urllib.request.urlopen", side_effect=err404) as u, \
                    mock.patch("time.sleep"):
                with self.assertRaises(urllib.error.HTTPError):
                    c.request("https://example.invalid/y", retries=2)
                self.assertEqual(u.call_count, 1)  # client errors are not retried
            c.close()


if __name__ == "__main__":
    unittest.main()

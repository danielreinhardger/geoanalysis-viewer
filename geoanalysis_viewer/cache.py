"""Local SQLite cache for HTTP responses (rips-gdi, LGRB, Overpass ...).

Every response is stored with URL, normalised parameters, layer tag,
retrieval timestamp and HTTP status, keyed by sha256 of the normalised request.

Modes:
  default  - use cache if present, otherwise fetch and store
  refresh  - always fetch and overwrite
  offline  - never touch the network; raise CacheMiss if not cached
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "cache" / "http_cache.sqlite"
USER_AGENT = "geoanalysis-viewer/0.1 (+local research)"


class CacheMiss(RuntimeError):
    pass


class InvalidResponse(RuntimeError):
    """HTTP 200 whose body failed the caller's validation; nothing was cached."""


@dataclass
class CachedResponse:
    key: str
    url: str
    params: dict
    layer: str | None
    fetched_at: str
    status: int
    body: bytes
    from_cache: bool

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _normalise(url: str, params: dict | None, method: str) -> str:
    p = {k: (json.dumps(v, sort_keys=True, separators=(",", ":")) if isinstance(v, (dict, list)) else str(v))
         for k, v in (params or {}).items() if v is not None}
    return json.dumps({"m": method.upper(), "u": url, "p": dict(sorted(p.items()))}, sort_keys=True)


class HttpCache:
    def __init__(self, db_path: Path | str = DEFAULT_DB, mode: str = "default", timeout: int = 60,
                 min_interval_s: float = 0.0):
        assert mode in ("default", "refresh", "offline"), mode
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.timeout = timeout
        self.min_interval_s = min_interval_s
        self._last_call = 0.0
        self.stats = {"hits": 0, "network": 0}
        self.con = sqlite3.connect(self.db_path)
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS responses(
                   key TEXT PRIMARY KEY, url TEXT, method TEXT, params TEXT, layer TEXT,
                   fetched_at TEXT, status INTEGER, body BLOB)""")
        self.con.commit()

    def request(self, url: str, params: dict | None = None, method: str = "GET",
                layer: str | None = None, validate=None, retries: int = 0) -> CachedResponse:
        """Fetch through the cache.

        validate: optional callable(body: bytes) -> bool. Some servers answer HTTP 200 with an
            error page (WMS ServiceException, WAF "Request Rejected", Overpass timeout). A body
            that fails validation is never stored, and a stored one is treated as a miss.
        retries: extra attempts with backoff (2 s, 4 s, ...) on network errors, HTTP 429/5xx
            and bodies that fail validation.
        """
        norm = _normalise(url, params, method)
        key = hashlib.sha256(norm.encode()).hexdigest()
        if self.mode != "refresh":
            row = self.con.execute(
                "SELECT url, params, layer, fetched_at, status, body FROM responses WHERE key=?", (key,)).fetchone()
            if row:
                body = gzip.decompress(row[5])
                if validate is None or validate(body):
                    self.stats["hits"] += 1
                    return CachedResponse(key, row[0], json.loads(row[1]), row[2], row[3], row[4], body, True)
        if self.mode == "offline":
            raise CacheMiss(f"offline and not cached: {layer or ''} {url}")

        p = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (params or {}).items()
             if v is not None}
        data = urllib.parse.urlencode(p).encode()
        for attempt in range(retries + 1):
            wait = self.min_interval_s - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            if method.upper() == "GET":
                req = urllib.request.Request(url + ("?" + data.decode() if p else ""),
                                             headers={"User-Agent": USER_AGENT})
            else:
                req = urllib.request.Request(url, data=data, headers={
                    "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body, status = r.read(), r.status
                self.stats["network"] += 1
                if validate is None or validate(body):
                    break
                err: Exception = InvalidResponse(f"{layer or ''} {url}: {body[:200]!r}")
            except urllib.error.HTTPError as e:
                if e.code != 429 and e.code < 500:
                    raise
                err = e
            except (urllib.error.URLError, TimeoutError) as e:
                err = e
            finally:
                self._last_call = time.time()
            if attempt == retries:
                raise err
            time.sleep(2 ** (attempt + 1))
        fetched = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.con.execute("INSERT OR REPLACE INTO responses VALUES(?,?,?,?,?,?,?,?)",
                         (key, url, method.upper(), json.dumps(p, sort_keys=True), layer, fetched, status,
                          gzip.compress(body)))
        self.con.commit()
        return CachedResponse(key, url, p, layer, fetched, status, body, False)

    def list(self, layer: str | None = None):
        q = "SELECT key, layer, url, fetched_at, status, length(body) FROM responses"
        args = ()
        if layer:
            q += " WHERE layer=?"
            args = (layer,)
        return self.con.execute(q + " ORDER BY fetched_at", args).fetchall()

    def close(self):
        self.con.close()

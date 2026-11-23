"""Paged GeoJSON client for the WFS 2.0 services of the LGL-BW (ALKIS, ATKIS Basis-DLM).

Licence (from the services' GetCapabilities, read 13./14.11.2026): Datenlizenz Deutschland -
Namensnennung - Version 2.0 (dl-de/by-2-0). Required attribution:
"LGL-BW (<year of retrieval>) Datenlizenz Deutschland - Namensnennung - Version 2.0, www.lgl-bw.de".

The servers answer some bad requests with HTTP 200 and an HTML "Request Rejected" page, so every
response is validated as a GeoJSON FeatureCollection before it is cached.
"""
from __future__ import annotations

import json

from .cache import HttpCache

CRS = "urn:ogc:def:crs:EPSG::25832"
LICENCE = "Datenlizenz Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0)"


def attribution(fetched_at: str) -> str:
    return f"LGL-BW ({fetched_at[:4]}) {LICENCE.split(' (')[0]}, www.lgl-bw.de"


def is_feature_collection(body: bytes) -> bool:
    try:
        return json.loads(body.decode("utf-8")).get("type") == "FeatureCollection"
    except (ValueError, AttributeError):
        return False


def query(cache: HttpCache, url: str, typename: str, bbox: tuple[float, float, float, float],
          page_size: int = 2000) -> dict:
    """All features of one feature type that intersect the bbox (EPSG:25832), paged."""
    features, calls, start = [], [], 0
    while True:
        params = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": typename,
                  "SRSNAME": CRS, "BBOX": ",".join(f"{v:.2f}" for v in bbox) + "," + CRS,
                  "OUTPUTFORMAT": "application/json", "COUNT": page_size, "STARTINDEX": start}
        resp = cache.request(url, params, layer=typename, validate=is_feature_collection, retries=3)
        page = resp.json()["features"]
        calls.append({"fetched_at": resp.fetched_at, "from_cache": resp.from_cache, "key": resp.key})
        features.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return {"type": "FeatureCollection", "crs": "EPSG:25832", "features": features,
            "meta": {"layer": typename, "source": url, "licence": LICENCE,
                     "attribution": attribution(calls[0]["fetched_at"]), "requests": calls}}

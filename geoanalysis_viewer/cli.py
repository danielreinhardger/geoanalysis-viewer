"""Command line: python -m geoanalysis_viewer <command> ...

  services                      list LUBW services (cached)
  fetch   --site S --layers ..  fetch & cache layers around a site, write GeoJSON to data/
  analyze --site S --layers ..  overlap/distance report (JSON + Markdown) in reports/; the viewer shows its status per feature
  settlement --site S           distance to ALKIS residential land (reports/<ID>_siedlungsabstand.md); the viewer takes its viewpoints from it
  cache   [--layer L]           list cached responses
  export-web --site S           web bundle for the three.js viewer: terrain, aerial image, layers,
                                analysis status, viewer files (exports/<ID>_web/)

Global flags: --offline (cache only), --refresh (refetch), --db PATH

Each command is one cmd_<name>(args, cache) function; _parser() wires the arguments to it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import alkis, analyze, geom, ripsgdi, singlefile, webscene
from .cache import DEFAULT_DB, HttpCache

ROOT = Path(__file__).resolve().parent.parent
REPORTS, EXPORTS = ROOT / "reports", ROOT / "exports"
DEFAULT_SITE = str(ROOT / "sites" / "g2.geojson")
DEFAULT_LAYERS = ["spa", "ffh", "nsg", "lsg", "wsg", "qsg", "usg", "biotop"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(obj, **kw) -> str:
    return json.dumps(obj, ensure_ascii=False, **kw)


def _site(a) -> tuple[dict, str]:
    site = analyze.load_site(a.site)
    return site, site["properties"]["id"]


def _padded_bbox(site: dict, pad: float) -> tuple[float, float, float, float]:
    sb = geom.bbox(site["geometry"]["coordinates"])
    return sb[0] - pad, sb[1] - pad, sb[2] + pad, sb[3] + pad


def _report(stem: str, rep: dict | None, md: str) -> None:
    """reports/<stem>.json (if rep is given) and reports/<stem>.md; the Markdown also goes to stdout."""
    if rep is not None:
        _write(REPORTS / f"{stem}.json", _json(rep, indent=1))
    _write(REPORTS / f"{stem}.md", md)
    print(md)


def _say(prefix: str = "  "):
    return lambda m: print(f"{prefix}{m}", file=sys.stderr)


# ---------------------------------------------------------------- commands


def cmd_services(a, cache):
    print("\n".join(ripsgdi.list_services(cache)))


def cmd_cache(a, cache):
    for row in cache.list(a.layer):
        print(" | ".join(map(str, row)))


def cmd_fetch(a, cache):
    site, sid = _site(a)
    for layer in a.layers:
        fc = ripsgdi.query(cache, layer, bbox=_padded_bbox(site, a.radius))
        _write(ROOT / "data" / sid / f"{fc['meta']['layer']}.geojson", _json(fc))
        print(f"{fc['meta']['layer']}: {len(fc['features'])} features")


def cmd_analyze(a, cache):
    site, sid = _site(a)
    rep = analyze.analyze_site(site, a.layers, cache, radius_m=a.radius, res_m=a.res)
    _report(f"{sid}_analyse", rep, analyze.to_markdown(rep))


def cmd_settlement(a, cache):
    site, sid = _site(a)
    rep = alkis.settlement_distance(site, cache, radius_m=a.radius)
    _report(f"{sid}_siedlungsabstand", rep, alkis.to_markdown(rep))


def cmd_export_web(a, cache):
    site, sid = _site(a)
    sc = Path(a.scenario) if a.scenario else Path(a.site).with_name(Path(a.site).stem + "_szenario.json")
    scenario = json.loads(sc.read_text(encoding="utf-8")) if sc.exists() else None
    out = webscene.build(site, ROOT, cache, out=Path(a.out) if a.out else None, pad_m=a.pad,
                         terrain_res=a.terrain_res, ortho_mpp=a.ortho_mpp, download=not a.offline,
                         progress=_say("[dgm] "), scenario=scenario, surface=a.surface, detail=a.detail,
                         detail_mpp=a.detail_mpp, detail_pad_m=a.detail_pad)
    print(out)
    if a.single:
        one = EXPORTS / f"{sid}_3d.html"
        _write(one, singlefile.single_html(out))
        print(one, f"{one.stat().st_size / 1e6:.1f} MB")


# ---------------------------------------------------------------- arguments


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="geoanalysis_viewer")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def command(name, fn, site=True):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if site:
            p.add_argument("--site", default=DEFAULT_SITE)
        return p

    command("services", cmd_services, site=False)
    command("cache", cmd_cache, site=False).add_argument("--layer")
    for name, fn in (("fetch", cmd_fetch), ("analyze", cmd_analyze)):
        p = command(name, fn)
        p.add_argument("--layers", nargs="*", default=DEFAULT_LAYERS)
        p.add_argument("--radius", type=float, default=1500)
        p.add_argument("--res", type=float, default=0.5)

    w = command("export-web", cmd_export_web)
    w.add_argument("--pad", type=float, default=1500, help="terrain and image margin around the site bbox (m)")
    w.add_argument("--terrain-res", type=float, default=5.0, help="terrain grid spacing (m)")
    w.add_argument("--ortho-mpp", type=float, default=1.0, help="aerial image resolution (m per pixel)")
    w.add_argument("--out", help="output folder (default exports/<ID>_web)")
    w.add_argument("--single", action="store_true", help="also write exports/<ID>_3d.html: one file, opens offline without a server")
    w.add_argument("--surface", action="store_true", help="add trees and buildings (DOM1 tiles already on disk; needs numpy)")
    w.add_argument("--detail", action="store_true", help="blend the sharper near-area aerial image in data/sources/dop (already on disk)")
    w.add_argument("--detail-mpp", type=float, help="fetch the near-area aerial image at this resolution through the WMS (e.g. 0.2)")
    w.add_argument("--detail-pad", type=float, default=200, help="margin of the near-area image around the site bbox (m)")
    w.add_argument("--scenario", help="JSON with start values for the heap scenario "
                                      "(default: <site file stem>_szenario.json next to the site, if present)")

    command("settlement", cmd_settlement).add_argument("--radius", type=float, default=1500)

    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # the reports are German: a cp1252 console must not crash on them
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = _parser().parse_args(argv)
    mode = "offline" if a.offline else "refresh" if a.refresh else "default"
    cache = HttpCache(a.db, mode=mode, min_interval_s=0.3)
    a.fn(a, cache)
    print(f"[cache] hits={cache.stats['hits']} network={cache.stats['network']}", file=sys.stderr)


if __name__ == "__main__":
    main()

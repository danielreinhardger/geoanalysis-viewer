"""Build the public demo page docs/index.html (served by GitHub Pages) from an export-web bundle.

  python -m geoanalysis_viewer --offline export-web --site sites/g2.geojson --surface --detail-mpp 0.2
  python tools/build_demo.py exports/G2_web

The demo leaves out the LUBW layers (protected areas, biotopes, water layers) and the analysis status
that refers to them: their terms for redistribution were not checked. Everything else is the bundle as
exported: terrain, aerial image, surface model, trees, residential areas (ALKIS), viewpoints, the heap
scenario. The bundle folder itself is not changed; the page is packed from a temporary copy.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geoanalysis_viewer import singlefile  # noqa: E402


def is_lubw(layer: dict) -> bool:
    return "lubw" in f"{layer.get('source', '')} {layer.get('attribution', '')}".lower()


def main(bundle: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "bundle"
        shutil.copytree(bundle, work)
        fp = work / "scene.json"
        scene = json.loads(fp.read_text(encoding="utf-8"))
        dropped = [la["id"] for la in scene["layers"] if is_lubw(la)]
        scene["layers"] = [la for la in scene["layers"] if not is_lubw(la)]
        scene["analysis"] = None   # overlap status of the dropped layers
        fp.write_text(json.dumps(scene, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        html = singlefile.single_html(work)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"{out}: {out.stat().st_size / 1e6:.2f} MB, left out {len(dropped)} LUBW layers")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "index.html")

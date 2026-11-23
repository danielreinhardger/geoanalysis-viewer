# geoanalysis-viewer: G2 in 3D

A three.js viewer that shows a site in Baden-Württemberg in 3D from public geodata: terrain (DGM1), aerial image (DOP20 down to 0.2 m), buildings and trees from the surface model (DOM1), protected areas and water layers (LUBW), and an assumed landfill heap as a scenario. It is built for the landfill search area **G2** (Eschenbach and Heiningen, Landkreis Göppingen) of the Verband Region Stuttgart, and asks one question: how would a heap there look from where people live?

The export writes one HTML file (about 5.5 MB for G2) that opens offline from disk, without a server and without network requests.

## What is real and what is assumed

- **Real data:** terrain, aerial image, surface heights, tree positions and heights, protected areas, viewpoints at the nearest houses. Each is named with source and retrieval date under "Quellen" in the viewer.
- **Assumed:** the heap. Its footprint and slope come from `sites/g2_szenario.json`, where every value is sourced or marked as an assumption; its surface and fine detail are labelled as illustration (part of it from AI-generated images).
- Nothing invented appears where something real exists: the aerial image is not upscaled, no houses or trees are made up.
- At eye level the view is shown at true angular size for an assumed screen (50 cm away, 0.26 mm per pixel; the viewer states this).

`docs/RENDERING.md` explains how the view is made, which parts are measured and which are assumed, and what it costs.

## Build the viewer

Developed and tested only with Python 3.14 under Windows; `pyproject.toml` asks for 3.12 or newer, which is untested. Building the 3D page needs numpy, pillow and scipy (`pip install -e .[raster]`; without scipy the trees are left out without an error); the data clients are standard library only.

Run from the repository root:

```powershell
python -m geoanalysis_viewer fetch      --site sites/g2.geojson   # LUBW layers around the site (cached in cache/)
python -m geoanalysis_viewer analyze    --site sites/g2.geojson   # overlap and distance per layer: the viewer shows this status
python -m geoanalysis_viewer settlement --site sites/g2.geojson   # nearest houses: the viewer's eye-level viewpoints
python -m geoanalysis_viewer export-web --site sites/g2.geojson --surface --detail-mpp 0.2 --single
```

The last command downloads the DGM1 terrain tiles and the aerial images it needs (into `data/sources/`), writes the folder version to `exports/G2_web/` and the single file to `exports/G2_3d.html`. `--offline` uses the cache only; the last stderr line, `[cache] hits=… network=…`, shows whether the network was used. The horizon ring beyond the terrain needs the Copernicus DEM tile named in `geoanalysis_viewer/horizon.py` in `data/sources/copdem/`; without it the ring is left out. `--surface` (buildings and trees) reads the DOM1 tiles `dom1_32_<e>_<n>_2_bw.zip` from `data/sources/dom/opendata/`; no command downloads them yet (the URL is in `geoanalysis_viewer/dgm.py`, `TILE_URLS`), and without them the page shows bare terrain.

Any other site works the same way: a GeoJSON polygon in EPSG:25832 with `tolerance_m` and `source` in its properties, and optionally `<site>_szenario.json` next to it.

```powershell
python -m unittest discover -s tests -q        # offline tests
python -I tools/check_page.py file:///<repo>/exports/G2_3d.html   # open the page in Chrome without network, print state and errors
```

## Layout

| Path | Contents |
|---|---|
| `viewer/` | the viewer: `index.html`, `viewer.js`, `viewer.css`; three.js r186 in `vendor/three/`; heap textures in `textures/` |
| `geoanalysis_viewer/webscene.py` | writes the bundle: terrain, aerial tiles, layers with analysis status, viewpoints, scenario |
| `geoanalysis_viewer/singlefile.py` | packs the bundle into one HTML file |
| `geoanalysis_viewer/trees.py`, `domfix.py`, `horizon.py`, `raster.py`, `dgm.py` | tree tops from the surface model, the surface-error correction, the horizon ring, height tiles |
| `geoanalysis_viewer/ripsgdi.py`, `alkis.py`, `lgl_wfs.py`, `analyze.py`, `cache.py`, `geom.py`, `cli.py` | data clients (LUBW, LGL), the layer analysis and settlement distance the viewer shows, HTTP cache, geometry, commands |
| `tools/` | heap texture tools (`textures/`), sun direction from tree shadows, headless page check |
| `sites/` | the G2 outline (`g2.geojson`, traced from the site plan, ±4 m) and the heap scenario |
| `data/reference/dom_corrections.json` | a documented error in the surface model (a steam plume) that the export removes |
| `docs/RENDERING.md` | how the view is made and where it stops being real; `docs/retrace_lageplan.py` shows how the G2 outline was traced |

## Data and licences

The page fetches nothing; the export fetches public data and embeds it with attribution:

- **LGL-BW** (DGM1, DOM1, DOP20, ALKIS): dl-de/by-2-0, "LGL-BW (year of retrieval) Datenlizenz Deutschland - Namensnennung - Version 2.0, www.lgl-bw.de".
- **LUBW** (rips-gdi, protected areas and water layers): terms of use at lubw.baden-wuerttemberg.de.
- **Copernicus DEM GLO-30** (horizon ring): notice in `THIRD_PARTY.md`.

Third-party code (three.js, MIT; martini, ISC) and the AI-generated textures: `THIRD_PARTY.md`.

## Licence

Copyright (C) 2026 Daniel Reinhard.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 3 (`LICENSE`). It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

Bundled third-party code and the data notices are listed in `THIRD_PARTY.md`; they keep their own licences.

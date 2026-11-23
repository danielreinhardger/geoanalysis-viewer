# Third-party code and content

## three.js r186

`viewer/vendor/three/` (three.core.js, three.module.js, OrbitControls.js), MIT licence, full text in `viewer/vendor/three/LICENSE`. The single-file export copies that licence into every page.

## martini

The terrain simplification `rtinIndex()` in `viewer/viewer.js` (right-triangulated irregular network) follows mapbox/martini, https://github.com/mapbox/martini. The code is a rewrite, not a copy; the notice is kept anyway:

```
ISC License

Copyright (c) 2019, Mapbox

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
```

Source: https://raw.githubusercontent.com/mapbox/martini/main/LICENSE, read 23.11.2026.

## Heap textures

`viewer/textures/heap_*.webp` were generated with FLUX 3 Image Klein (Black Forest Labs) in ComfyUI. Model, prompt and seed of each image are in `viewer/textures/textures.json` and `viewer/textures/heap_detail.json`. Under the FLUX Kommunity License the provider claims no ownership of outputs; they may not be used to train other image models, and their AI origin must be disclosed (the viewer labels them as generated). This is a reading of the licence, not a legal review.

## Geodata

The repository holds small derived files in `data/reference/` and `sites/` (each states its source and retrieval date) and the demo page `docs/g2.html`. The demo page embeds LGL-BW data (DGM1, DOM1, DOP20, ALKIS residential areas) and the Copernicus DEM horizon ring with their attributions, and three.js with its licence; it leaves out the LUBW layers. A local export embeds data from:

- LUBW (rips-gdi): terms of use at lubw.baden-wuerttemberg.de.
- LGL-BW (ALKIS, ATKIS, DGM1, DOM1, DOP20): dl-de/by-2-0, "LGL-BW (year) Datenlizenz Deutschland - Namensnennung - Version 2.0, www.lgl-bw.de".

LUBW data are not stored in the repository, also not in the demo page; they are fetched at run time.

## Data fetched at run time (not in the repository)

Notices as the code records them; the licence texts themselves were not all read in full.

- Copernicus DEM GLO-30 (horizon ring, `geoanalysis_viewer/horizon.py`, from AWS Open Data): "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved". The viewer shows it under "Quellen".

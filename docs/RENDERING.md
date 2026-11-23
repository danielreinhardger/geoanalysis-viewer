# How the 3D view is made, and where it stops being real

`python -m geoanalysis_viewer export-web --site S --surface --detail-mpp 0.2 --single` writes the three.js viewer for a site as a folder (`exports/<ID>_web/`) and as one HTML file that opens from disk without a server or network (`exports/<ID>_3d.html`). This document says what the view is built from, which parts are measured and which are assumed, and what it costs. Figures are for G2 and were measured in Chrome on one desktop GPU (RTX 5090); weaker devices were not measured.

## First principles

How true the picture is depends on four things, and the weakest limits the whole:

1. **Image detail on screen.** A screen pixel should not cover more ground than one image pixel.
2. **Shape of the ground and of what stands on it.** A 5-m terrain grid is fine for hills, not for houses and trees.
3. **Light.** The aerial image already holds the sunlight of its flight day. Extra lighting must not cast a second, different shadow across it; only a soft slope shading is right.
4. **Lines and labels.** They are vectors and are drawn as geometry, so they stay sharp.

One rule holds throughout: **nothing invented appears where something real exists.** The aerial image is never upscaled, and no houses or trees are made up. Synthetic or AI-generated detail is used only on the scenario heap, which is itself an assumption, and the viewer labels it there.

## What is real data

| Part | Source | Notes |
|---|---|---|
| Terrain | LGL DGM1 (1 m), averaged to 5 m | stated height accuracy ±0.15 m |
| Aerial image | LGL DOP20 through the WMS: 1 m overall, 0.2 m in the near area (site + 200 m) | the near-area image loads after the first frame; if the GPU cannot hold it, the 1-m image stays |
| Houses, hedges, woods | LGL DOM1 (laser flight, March 2016), highest value per 5-m cell, drawn as blocks | narrow tall structures appear 5 m wide |
| Trees | tops found in DOM1 minus DGM1 (local maxima, at least 3 m high, not on ALKIS buildings), drawn as stand-ins at the measured position, height and crown radius | crowns are drawn closed, as if in leaf; the heights are from the leaf-off flight. Dense woods have fewer detected tops than trees |
| Layers | protected areas, biotopes etc. from the analysis, mixed into the terrain shader | zone fills fade where the view grazes the ground, so the land shows through at eye level |
| Horizon | Copernicus DEM GLO-30 (a surface model) for a 30 × 30 km ring beyond the DGM1, with a coarse DOP20 image | blended into the DGM1 over the first 1,000 m; off by default |
| Viewpoints | nearest residential buildings from the `settlement` report | walk mode at a set eye height (1.7–15 m) |

**Corrected surface error.** The DOM1 tile north of G2 holds a body 46–154 m above ground beside a waste-to-energy plant, very likely the steam plume matched as a surface. `data/reference/dom_corrections.json` records the box, the threshold, the kept stack top and the evidence; `geoanalysis_viewer/domfix.py` sets those cells to the ground before the surface blocks and tree tops are built. Only the 3D export reads the correction.

## What is assumed: the scenario heap

The heap is a scenario, not a plan. Its footprint and slope come from `sites/<ID>_szenario.json`, where every value is either sourced or marked as an assumption.

- **Forms:** "einfach (Rechenform)" is the simple profile used for line-of-sight and wind calculations elsewhere. The default "Deponie mit Bermen und Kuppe (nach Vorbildern)" straightens the foot line, adds a ring road, berms with tracks and a top falling 6 % to its edge; its highest point is held at that of the simple form, so it never shows less height than the reports. The volumes of both forms are shown.
- **Surface:** one material map over the heap, rebuilt on each slider change from what each spot is (tracks, slopes, top, a working area with tipping piles). In the operating state only mineral surfaces are shown; "nach Abschluss" shows grass. Shares and layout are assumptions. Brightness comes from DOP20 images of operating landfills for mineral waste, so the heap matches the aerial image around it.
- **Generated detail:** brightness detail of a few metres from AI-generated aerial-scale tiles (FLUX 3 Image Klein; model, prompt and seed in `viewer/textures/heap_detail.json`) is baked into the map. The viewer says so under "Oberfläche" and "Quellen".
- **Vehicles:** boxes for size only. Nothing leaves the footprint, so no access point is implied: no haul ramp, no access road, no dust plumes.
- Berm spacing and width have no primary source and are labelled as free assumptions.

## Techniques

- **Material map on the GPU.** The map (about 0.9 M pixels at 0.83 m) is rendered by a fragment shader (`BAKE_FS`, `gpuBake`) into a render target once per change, not per frame. The CPU version stays as reference and fallback: `?bake=cpu` uses it, `viewer.bakeCompare()` compares both (at most 1/255 per channel). If the shader fails to compile, the CPU bake takes over. The top tile is hex-tiled (Mikkelsen 2022, JCGT 11(3)) to hide its repeat.
- **Light.** "Photo-preserving" shading keeps the aerial image's brightness on flat ground and only lightens or darkens slopes. The sun direction is the one on the image's flight day, measured from tree shadows (`tools/sun_from_shadows.py`: azimuth about 205°, elevation about 60°). Sky gradient and haze are illustrations, not a measured visibility.
- **True scale at eye level.** On the eye-level views the vertical field of view is set so that one CSS pixel covers what it would at 50 cm viewing distance and 0.26 mm per CSS pixel (touch: 30 cm, 0.17 mm). That gives 22° vertical on a 1384 × 749 window instead of 45°, so the heap appears at its real angular size. The browser cannot know the real pixel size, so the viewer states the assumption (zoom 100 %). Overviews keep 45°.
- **Fewer triangles.** The terrain index is a right-triangulated irregular network over the same grid points (after mapbox/martini, see `THIRD_PARTY.md`): a triangle stays large where the grid deviates from it by at most 0.15 m, the DGM1's stated accuracy. Positions, uv and normals (from the full grid) are unchanged. 1.16 M → 0.35 M triangles, computed in about 75 ms right after the first frame. Tree crowns use 36 and trunks 6 triangles. Per frame: 5.06 M → 2.50 M triangles. Heights read elsewhere (heap foot, trees, eye height) come from the grid and can differ from the drawn surface by up to 0.15 m.

## The single file

`geoanalysis_viewer/singlefile.py` packs the bundle into one page:

- text files are gzipped; heights become decimetre row differences (rounding ≤ 5 cm, below the DGM1 accuracy), which gzip about three times smaller;
- the 1-m aerial tiles become WebP q70; the near-area image and the horizon image become AVIF (q40 and q50). Both AVIF images load after the first frame, and the viewer works without them if a browser cannot decode AVIF;
- everything is carried as 7-bit text (after base-122, K. Albertson 2016) in `<script type="application/octet-stream">` elements and decoded once at start into blob: URLs, so no import map and no request is needed.

For G2 the file is 5.47 MB. Opened from disk or sent as an attachment it is 14 % smaller than with base64; served over HTTP with gzip it is about 4 % larger (4.98 against 4.79 MB on the wire), because gzip had already removed most of base64's overhead.

## Not done

- LoD2 building models: the licence and download route were not verified.
- A summer surface model (bDOM from aerial photos): not available for download without a request to the LGL.
- Ambient occlusion and slope-dependent heap texturing.
- GPU texture compression (KTX2): it would cut graphics memory, not file size, and the transcoder would add to the page.
- Measurements on laptop GPUs and phones.
- Google Photorealistic 3D Tiles are not usable: their terms forbid offline storage and derived content.

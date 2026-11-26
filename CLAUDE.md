# Notes for coding agents and contributors

Read `README.md` (what it does, how to build) and `docs/RENDERING.md` (how the view is made) first. `AGENTS.md` lists the invariants, where to change what, how the export degrades when something is missing, and what is open; `docs/DESIGN.md` explains the decisions for people.

## Rules

- **Nothing invented where something real exists.** Never upscale the aerial image to add detail (AI or super-resolution; showing it coarser is fine), never invent houses or trees, never drape map layers on the tree or building surface. Synthetic or AI detail only on the scenario heap, and labelled in the viewer.
- **The heap is a scenario.** Its inputs live in `sites/<id>_szenario.json`, each value sourced or marked as an assumption. No haul ramp or access road (it would imply an access point), no dust plumes.
- **Cite sources and dates.** Everything the export embeds carries source and retrieval date; the viewer lists them under "Quellen".
- **No guessed coordinates.** Site outlines come from a plan or an official service, with `tolerance_m` and `source`.
- **Dependencies:** the data clients, cache and geometry are standard library only; numpy and pillow are imported inside the export (a test checks that the CLI loads without numpy). CRS is EPSG:25832 everywhere.
- **Every service client** goes through `HttpCache.request(..., validate=...)`: a body that fails validation is never cached.
- **Language:** code, comments and docs in English; text shown in the viewer in German.

## Where things are

- Edit the viewer in `viewer/`, never in `exports/`. `webscene.py` writes the bundle, `singlefile.py` packs it into one page (it fills exactly the two import specifiers of `viewer.js`).
- One command = one `cmd_<name>(args, cache)` in `geoanalysis_viewer/cli.py`.

## Checks

```powershell
python -m unittest discover -s tests -q
python -m geoanalysis_viewer --offline export-web --site sites/g2.geojson --surface --detail-mpp 0.2 --single
python -I tools/check_page.py file:///<repo>/exports/G2_3d.html     # STATE ready, REQUESTS [] , no errors
```

## Known server quirks

- rips-gdi returns an error for `distance=0`; omit the parameter. Field names differ per layer; keep all attributes. `FFH_Maehwiese` uses layer id 1 (`LAYER_IDS`).
- LGL ALKIS WFS answers a bad request with HTTP 200 and an HTML "Request Rejected" page; page with `COUNT`/`STARTINDEX`.

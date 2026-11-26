"""One HTML file from a viewer bundle: opens from disk without a server or network.

`single_html(bundle)` packs every file of an export-web folder into the page: text is gzipped,
aerial tiles become WebP/AVIF (the 1-m image at full resolution only near the site), heights are
predicted from their neighbours (_heights), and all of it is carried
as 7-bit text (`_b122`) that a small boot script decodes once into blob: URLs.
`embedded(html, name)` reads one file back (tests).

Pure standard library, except numpy for the height deltas and pillow for WebP/AVIF (both optional:
without pillow the JPEGs are embedded as they are).
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path


_MIME = {".json": "application/json", ".bin": "application/octet-stream", ".jpg": "image/jpeg", ".png": "image/png",
         ".js": "text/javascript", ".webp": "image/webp"}
WEBP_QUALITY = 70   # 1-m aerial tiles in the single file; q70 looks like q80 at about 24 % fewer bytes (21.11.2026)


# Height grids (terrain.bin, surface.bin, ring.bin) in the single file. Terrain and surface are rounded to
# whole decimetres (<= 5 cm, below the 15 cm accuracy of DGM1/DOM1); the ring is in decimetres already.
# Each value is predicted from its neighbours left, below and below-left (left + below - below-left: a
# plane through them); the residual is zigzagged (0, -1, 1, -2 ... -> 0, 1, 2, 3 ...) and written as one
# byte, or as 255 plus three bytes when it is 255 or more; then gzipped. The surface is stored as height
# above the terrain, and only where the viewer reads it (SURFACE_MIN_CM): elsewhere it decodes to the
# terrain itself, which draws the same picture. Measured on G2 (26.11.2026), against the earlier row
# differences or plain gzip: terrain 292 -> 203 kB, surface 479 -> 249 kB, ring 388 -> 249 kB.
# Decoded by __unh in _BOOT; unheights below is the same in Python, for tests.
SURFACE_MIN_CM = 200   # = TALL_CM in viewer/viewer.js: surface cells lower than this above ground are not read


def _zz_bytes(r) -> bytes:
    import numpy as np  # only for the single file
    r = np.asarray(r, np.int64).ravel()
    z = (r << 1) ^ (r >> 63)
    if int(z.max(initial=0)) >= 1 << 24:
        raise ValueError("height residual too large for 3 bytes")
    big = z >= 255
    pos = np.arange(len(z)) + 3 * np.concatenate([[0], np.cumsum(big)[:-1]]).astype(np.int64)
    out = np.zeros(len(z) + 3 * int(big.sum()), np.uint8)
    out[pos] = np.where(big, 255, z)
    for k in range(3):
        out[pos[big] + 1 + k] = (z[big] >> (8 * k)) & 255
    return out.tobytes()


def _heights(a, predict: bool) -> bytes:
    """int grid (rows x cols) -> residual bytes as described above; predict=False stores the values themselves."""
    import numpy as np
    a = np.asarray(a, np.int64)
    if not predict:
        return _zz_bytes(a)
    p = np.zeros_like(a)
    p[1:, 1:] = a[1:, :-1] + a[:-1, 1:] - a[:-1, :-1]
    p[0, 1:] = a[0, :-1]
    p[1:, 0] = a[:-1, 0]
    return _zz_bytes(a - p)


def unheights(b: bytes, w: int, predict: bool):
    """Inverse of _heights: residual bytes -> int64 grid with w columns (for tests; the page uses __unh)."""
    import numpy as np
    v = np.frombuffer(b, np.uint8)
    starts, i = [], 0
    while i < len(v):   # slow but plain; only tests call it
        starts.append(i)
        i += 4 if v[i] == 255 else 1
    s = np.array(starts, np.int64)
    z = v[s].astype(np.int64)
    big = z == 255
    z[big] = v[s[big] + 1].astype(np.int64) | v[s[big] + 2].astype(np.int64) << 8 | v[s[big] + 3].astype(np.int64) << 16
    r = ((z >> 1) ^ -(z & 1)).reshape(-1, w)
    if not predict:
        return r
    a = np.zeros_like(r)
    a[0] = np.cumsum(r[0])
    for y in range(1, r.shape[0]):   # a[y,x] = r + a[y,x-1] + a[y-1,x] - a[y-1,x-1]: a cumulative sum of r + (a[y-1] differences)
        a[y] = a[y - 1] + np.cumsum(r[y])
    return a


def _webp(raw: bytes) -> bytes | None:
    try:
        import io
        from PIL import Image
        out = io.BytesIO()
        Image.open(io.BytesIO(raw)).save(out, "WEBP", quality=WEBP_QUALITY, method=6)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - no pillow or no WebP support: keep the JPEG
        return None


# Images the viewer loads after the first frame and can do without (near-area detail, horizon ring) are
# AVIF in the single file: measured 15.11.2026 (docs/RENDERING.md, "Encoding"), AVIF q50 has the error of
# WebP q80 on the 0.2-m detail image at a third fewer bytes and decodes faster in Chrome. A browser without
# AVIF (Safari before 16.4) keeps the 1-m image and the plain ring, so the main aerial image stays WebP.
AVIF_QUALITY = {"ortho_detail": 40, "ringimg_": 50}   # 0.2-m image q40: almost like q50 at 44 % fewer bytes
AVIF_FILES = tuple(AVIF_QUALITY)


def _avif(raw: bytes, quality: int) -> bytes | None:
    try:
        import io
        from PIL import Image
        out = io.BytesIO()
        Image.open(io.BytesIO(raw)).convert("RGB").save(out, "AVIF", quality=quality, speed=4)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - no AVIF encoder in this Pillow: fall back to WebP
        return None


# Runs before anything else in the single file. Every embedded file is gzip (or WebP/AVIF/JPEG) as text;
# fetch() of its name is answered from memory. three.js, OrbitControls and the viewer are then loaded
# as blob: modules whose import specifiers are filled in here, so no import map and no request is needed.
# Embedded files as text, 7 bits per character (after base-122, K. Albertson 2016): each character below
# 128 carries 7 bits of the file and takes one byte in UTF-8; a group of 7 bits that would be unsafe as a
# character (_B122_UNSAFE: NUL and CR, which the HTML parser changes, "<", which could end the script
# element, and LF, '"' and backslash for good measure) is sent together with the next group in one
# two-byte character U+0080-U+037F, a last unsafe group alone in U+0380-U+0385. 8/7 of the file instead of
# base64's 4/3: about 14 % smaller (measured on G2: docs/RENDERING.md). The text sits in
# <script type="application/octet-stream"> elements, which the browser keeps as they are.
_B122_UNSAFE = (0, 10, 13, 34, 92, 60)


def _b122(raw: bytes) -> str:
    import numpy as np  # only for the single file
    bits = np.unpackbits(np.frombuffer(raw, np.uint8))
    pad = (-len(bits)) % 7
    g = np.packbits(np.concatenate([bits, np.zeros(pad, np.uint8)]).reshape(-1, 7), axis=1, bitorder="big")[:, 0] >> 1
    unsafe = {v: i for i, v in enumerate(_B122_UNSAFE)}
    cps = g.astype(np.int32)
    keep = np.ones(len(g), bool)
    i, n = 0, len(g)
    for j in np.flatnonzero(np.isin(g, _B122_UNSAFE)):
        if j < i:   # already taken as the second group of a pair
            continue
        k = unsafe[int(g[j])]
        if j + 1 < n:
            cps[j] = 0x80 + k * 128 + int(g[j + 1])
            keep[j + 1] = False
            i = j + 2
        else:
            cps[j] = 0x380 + k
            i = j + 1
    return "".join(map(chr, cps[keep].tolist()))


def _unb122(text: str, n: int) -> bytes:
    """Inverse of _b122 (for tests); n = length of the file in bytes."""
    groups = []
    for ch in text:
        c = ord(ch)
        if c < 0x80:
            groups.append(c)
        elif c < 0x380:
            groups += [_B122_UNSAFE[(c - 0x80) >> 7], (c - 0x80) & 127]
        else:
            groups.append(_B122_UNSAFE[c - 0x380])
    bits = "".join(f"{v:07b}" for v in groups)
    return int(bits[:n * 8] or "0", 2).to_bytes(n, "big") if n else b""


def embedded(html: str, name: str) -> bytes:
    """Bytes of one embedded file of a single-file page, as the page decodes them (before gunzip)."""
    import re
    files = json.loads(re.search(r"const __files = (.*?);\n", html).group(1))
    f = files[name]
    m = re.search(r'<script type="application/octet-stream" id="%s">(.*?)</script>' % f["e"], html, re.S)
    return _unb122(m.group(1), f["n"])


_BOOT = r"""
const __UNSAFE = [0, 10, 13, 34, 92, 60];
function __b122(f) {   // see _b122 in webscene.py
  const s = document.getElementById(f.e).textContent, out = new Uint8Array(f.n), U = __UNSAFE;
  let acc = 0, bits = 0, o = 0;
  for (let i = 0, L = s.length; i < L; i++) {
    const c = s.charCodeAt(i);
    let g = c < 128 ? c : c < 0x380 ? U[(c - 128) >> 7] : U[c - 0x380];
    acc = (acc << 7) | g; bits += 7;
    if (bits >= 8) { bits -= 8; if (o < out.length) out[o++] = acc >> bits; acc &= (1 << bits) - 1; }
    if (c >= 128 && c < 0x380) {
      acc = (acc << 7) | ((c - 128) & 127); bits += 7;
      if (bits >= 8) { bits -= 8; if (o < out.length) out[o++] = acc >> bits; acc &= (1 << bits) - 1; }
    }
  }
  return out;
}
const __fetch = window.fetch.bind(window);
async function __bytes(f) {
  let b = __b122(f);
  if (f.z) b = new Uint8Array(await new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());
  if (f.h) b = new Uint8Array(__unh(b, f.h, f.h.base ? new Uint16Array((await __bytes(__files[f.h.base])).buffer) : null).buffer);
  return b;
}
// height grid (_heights in singlefile.py) -> uint16 values as in the .bin file: decoded value * h.k, plus
// the base grid where the value is not 0 (surface above terrain); where it is 0, the base grid itself
function __unh(b, h, base) {
  const w = h.w, n = h.n, a = new Int32Array(n), o = new Uint16Array(n);
  for (let i = 0, j = 0; j < n; j++) {
    let z = b[i++];
    if (z === 255) { z = b[i] | (b[i + 1] << 8) | (b[i + 2] << 16); i += 3; }
    const r = (z >>> 1) ^ -(z & 1), x = j % w;
    a[j] = !h.p ? r : r + (j >= w ? (x ? a[j - 1] + a[j - w] - a[j - w - 1] : a[j - w]) : x ? a[j - 1] : 0);
    o[j] = (base ? base[j] : 0) + a[j] * h.k;
  }
  return o;
}
window.fetch = async (u, o) => {
  const f = __files[String(u).split('?')[0].replace(/^(\.\/)+/, '')];
  if (!f) return __fetch(u, o);
  return new Response(await __bytes(f), {headers: {'Content-Type': f.t}});
};
const __fail = (m) => { const l = document.getElementById('loading'); if (l) l.textContent = 'Fehler beim Laden: ' + m; };
addEventListener('error', (e) => __fail(e.message));
addEventListener('unhandledrejection', (e) => __fail(e.reason && e.reason.message || e.reason));
(async () => {
  try {
    const text = async (n) => new TextDecoder().decode(await __bytes(__files[n]));
    const mod = (t) => URL.createObjectURL(new Blob([t], {type: 'text/javascript'}));
    const core = mod(await text('three.core.js'));
    const three = mod((await text('three.module.js')).replaceAll('__THREE_CORE__', core));
    const orbit = mod((await text('OrbitControls.js')).replaceAll('__THREE__', three));
    const viewer = (await text('viewer.js')).replaceAll('__THREE__', three).replaceAll('__ORBIT__', orbit);
    await import(mod(viewer));
  } catch (e) { __fail(e && e.message || e); }
})();
"""


def _height_files(bundle: Path, scene: dict) -> dict:
    """terrain.bin, surface.bin and ring.bin of the bundle, encoded with _heights (see there)."""
    import numpy as np  # only for the single file
    out = {}

    def add(name, grid, w, predict, k, base=None):
        h = {"w": w, "n": int(grid.size), "p": int(predict), "k": k}
        if base:
            h["base"] = base
        out[name] = {"t": _MIME[".bin"], "z": True, "h": h, "d": gzip.compress(_heights(grid, predict), 9)}

    t = scene.get("terrain")
    tfp = bundle / (t or {}).get("file", "terrain.bin")
    if t and tfp.exists():
        w = t["ncols"]
        td = (np.frombuffer(tfp.read_bytes(), "<u2").astype(np.int64).reshape(-1, w) + 5) // 10
        add(tfp.name, td, w, True, 10)
        s = scene.get("surface")
        sfp = bundle / (s or {}).get("file", "surface.bin")
        if s and sfp.exists():
            sd = (np.frombuffer(sfp.read_bytes(), "<u2").astype(np.int64).reshape(-1, w) + 5) // 10
            add(sfp.name, np.where((sd - td) * 10 > SURFACE_MIN_CM, sd - td, 0), w, False, 10, tfp.name)
    r = scene.get("ring")
    rfp = bundle / (r or {}).get("file", "ring.bin")
    if r and rfp.exists():
        add(rfp.name, np.frombuffer(rfp.read_bytes(), "<u2").astype(np.int64).reshape(-1, r["n"]), r["n"], True, 1)
    return out


# The 1-m aerial image (scene.ortho) in the single file: full resolution only in a core box around the
# site and the house viewpoints (plus ORTHO_CORE_PAD_M), embedded as one extra image; the tiles themselves
# at half resolution, which the viewer scales up to their place (drawImage at the tile's nominal size) before
# it draws the core over them. Nothing is added: outside the core the same image is coarser. Measured on G2
# (26.11.2026): 1-m tiles 1904 kB -> core and 2-m tiles about 900 kB (see docs/RENDERING.md). The folder
# version keeps the full tiles.
ORTHO_CORE_PAD_M = 300.0
ORTHO_OUTER = 2   # outside the core: this many 1-m pixels per embedded pixel


def _ortho_files(bundle: Path, scene: dict) -> dict:
    """Aerial tiles at 1/ORTHO_OUTER resolution plus the full-resolution core (see above); sets
    scene["ortho"]["core"]. Without pillow, or without a site box, nothing (the tiles go in as they are)."""
    O, bb, site = scene.get("ortho"), scene.get("bbox"), scene.get("site")
    if not O or not bb or not site or not O.get("tiles"):
        return {}
    try:
        import io
        from PIL import Image
    except ImportError:
        return {}
    tiles = O["tiles"]
    fw = sum(t["width"] for t in tiles if t["row"] == 0)
    fh = sum(t["height"] for t in tiles if t["col"] == 0)
    pos = {(t["row"], t["col"]): (sum(u["width"] for u in tiles if u["row"] == t["row"] and u["col"] < t["col"]),
                                  sum(u["height"] for u in tiles if u["col"] == t["col"] and u["row"] < t["row"])) for t in tiles}
    full = Image.new("RGB", (fw, fh))
    imgs = {}
    for t in tiles:
        imgs[t["file"]] = im = Image.open(bundle / t["file"]).convert("RGB")
        full.paste(im.resize((t["width"], t["height"])) if im.size != (t["width"], t["height"]) else im, pos[(t["row"], t["col"])])
    pts = [p for r in site["rings"] for p in r] + [(v["x"], v["y"]) for v in scene.get("viewpoints") or []]
    pad = ORTHO_CORE_PAD_M
    x0 = max(0, int((min(p[0] for p in pts) - pad - bb[0]) / (bb[2] - bb[0]) * fw))
    x1 = min(fw, int((max(p[0] for p in pts) + pad - bb[0]) / (bb[2] - bb[0]) * fw + 1))
    y0 = max(0, int((bb[3] - max(p[1] for p in pts) - pad) / (bb[3] - bb[1]) * fh))   # image row 0 = north
    y1 = min(fh, int((bb[3] - min(p[1] for p in pts) + pad) / (bb[3] - bb[1]) * fh + 1))

    def webp(im):
        o = io.BytesIO()
        im.save(o, "WEBP", quality=WEBP_QUALITY, method=6)
        return o.getvalue()

    try:
        out = {"ortho_core.jpg": {"t": "image/webp", "z": False, "d": webp(full.crop((x0, y0, x1, y1)))}}
        for t in tiles:
            im = imgs[t["file"]]
            small = im.resize((max(1, im.width // ORTHO_OUTER), max(1, im.height // ORTHO_OUTER)), Image.BOX)
            out[t["file"]] = {"t": "image/webp", "z": False, "d": webp(small)}
    except Exception:  # noqa: BLE001 - no WebP support in this pillow: tiles as they are
        return {}
    O["core"] = {"file": "ortho_core.jpg", "x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0, "mpp": O.get("mpp"),
                 "outer_mpp": (O.get("mpp") or 1) * ORTHO_OUTER}
    return out


def single_html(bundle: Path) -> str:
    """The bundle as one HTML file that opens from disk without a server or network.

    Load time and size, from first principles: the file is read once and every byte is text, so
    (1) everything is compressed before encoding (three.js and the viewer gzip to a fifth; heights
    are predicted from their neighbours, _heights; the aerial images become WebP/AVIF, the 1-m image
    at full resolution only near the site, _ortho_files); (2) the text carries 7 bits
    per byte (_b122; base64 carried 6), decoded once at start; (3) modules are created from memory
    (blob: URLs), so no import map is needed. viewer.js is the
    same file as in the folder version; only its two import specifiers are filled in at start."""
    html = (bundle / "index.html").read_text(encoding="utf-8")
    vendor = bundle / "vendor" / "three"
    scene = json.loads((bundle / "scene.json").read_text(encoding="utf-8"))

    def swap(text: str, old: str, new: str, name: str) -> str:
        if old not in text:
            raise RuntimeError(f"{name}: '{old}' not found; adapt single_html to the new file")
        return text.replace(old, new)

    sources = {
        "three.core.js": (vendor / "three.core.js").read_text(encoding="utf-8"),
        "three.module.js": swap((vendor / "three.module.js").read_text(encoding="utf-8"),
                                "'./three.core.js'", "'__THREE_CORE__'", "three.module.js"),
        "OrbitControls.js": swap((vendor / "OrbitControls.js").read_text(encoding="utf-8"),
                                 "from 'three'", "from '__THREE__'", "OrbitControls.js"),
        "viewer.js": swap(swap((bundle / "viewer.js").read_text(encoding="utf-8"), 'from "three"', 'from "__THREE__"', "viewer.js"),
                          'from "three/addons/controls/OrbitControls.js"', 'from "__ORBIT__"', "viewer.js"),
    }
    files = {n: {"t": _MIME[".js"], "z": True, "d": gzip.compress(t.encode("utf-8"), 9)} for n, t in sources.items()}
    files.update(_height_files(bundle, scene))
    files.update(_ortho_files(bundle, scene))   # also sets scene["ortho"]["core"], so scene.json goes in after it
    if "ortho_core.jpg" in files:
        files["scene.json"] = {"t": _MIME[".json"], "z": True,
                               "d": gzip.compress(json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)}
    for p in sorted(bundle.iterdir()):
        if p.suffix not in _MIME or p.suffix == ".js" or p.name == "index.html" or p.name in files:
            continue
        raw = p.read_bytes()
        if p.suffix == ".jpg":
            a = _avif(raw, next(q for k, q in AVIF_QUALITY.items() if p.name.startswith(k))) if p.name.startswith(AVIF_FILES) else None
            w = None if a else _webp(raw)
            mime = "image/avif" if a else "image/webp" if w else "image/jpeg"
            files[p.name] = {"t": mime, "z": False, "d": a or w or raw}
        elif p.suffix in (".webp", ".png"):   # already compressed
            files[p.name] = {"t": _MIME[p.suffix], "z": False, "d": raw}
        else:
            files[p.name] = {"t": _MIME[p.suffix], "z": True, "d": gzip.compress(raw, 9)}
    data = []
    for i, (name, f) in enumerate(files.items()):
        raw = f.pop("d")
        f.update(e=f"__f{i}", n=len(raw))
        data.append(f'<script type="application/octet-stream" id="__f{i}">{_b122(raw)}</script>')
    boot = "\n".join(data) + "\n<script>\nconst __files = " + json.dumps(files, separators=(",", ":")) + ";\n" + _BOOT + "</script>"
    css = (bundle / "viewer.css").read_text(encoding="utf-8")
    licence = (vendor / "LICENSE").read_text(encoding="utf-8").replace("--", "- -")
    start = html.index('<script type="importmap">')
    html = html[:start] + html[html.index("</script>", start) + len("</script>"):]
    html = swap(html, '<link rel="stylesheet" href="viewer.css">', "<style>\n" + css + "\n</style>", "index.html")
    html = swap(html, '<script type="module" src="viewer.js"></script>', boot, "index.html")
    # the licence goes after <meta charset>: browsers look for the charset only in the first 1024 bytes,
    # and the embedded text needs UTF-8
    meta = '<meta charset="utf-8">'
    return swap(html, meta, f"{meta}\n<!-- three.js r186, bundled under its MIT licence:\n{licence}\n-->", "index.html")

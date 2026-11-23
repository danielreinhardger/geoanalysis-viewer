"""One HTML file from a viewer bundle: opens from disk without a server or network.

`single_html(bundle)` packs every file of an export-web folder into the page: text is gzipped,
aerial tiles become WebP/AVIF, heights become decimetre row differences, and all of it is carried
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


def _height_delta_dm(raw: bytes, ncols: int) -> bytes:
    """uint16 cm -> int16 row differences of whole decimetres (rounding <= 5 cm, below the 15 cm
    accuracy of DGM1/DOM1). Smooth terrain then gzips about three times smaller."""
    import numpy as np  # only for the single file
    a = np.frombuffer(raw, "<u2").astype(np.int32).reshape(-1, ncols)
    return np.diff((a + 5) // 10, axis=1, prepend=0).astype("<i2").tobytes()


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
  if (f.delta) {           // int16 row differences of decimetres -> uint16 centimetres, as terrain.bin
    const d = new Int16Array(b.buffer, b.byteOffset, b.byteLength / 2), o = new Uint16Array(d.length), n = f.delta;
    for (let i = 0; i < d.length; i += n) { let v = 0; for (let j = 0; j < n; j++) { v += d[i + j]; o[i + j] = v * 10; } }
    b = new Uint8Array(o.buffer);
  }
  return b;
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


def single_html(bundle: Path) -> str:
    """The bundle as one HTML file that opens from disk without a server or network.

    Load time and size, from first principles: the file is read once and every byte is text, so
    (1) everything is compressed before encoding (three.js and the viewer gzip to a fifth; heights
    become decimetre row differences; the aerial images become WebP/AVIF); (2) the text carries 7 bits
    per byte (_b122; base64 carried 6), decoded once at start; (3) modules are created from memory
    (blob: URLs), so no import map is needed. viewer.js is the
    same file as in the folder version; only its two import specifiers are filled in at start."""
    html = (bundle / "index.html").read_text(encoding="utf-8")
    vendor = bundle / "vendor" / "three"
    scene = json.loads((bundle / "scene.json").read_text(encoding="utf-8"))
    ncols, nrows = scene["terrain"]["ncols"], scene["terrain"]["nrows"]

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
    for p in sorted(bundle.iterdir()):
        if p.suffix not in _MIME or p.suffix == ".js" or p.name == "index.html":
            continue
        raw = p.read_bytes()
        if p.suffix == ".jpg":
            a = _avif(raw, next(q for k, q in AVIF_QUALITY.items() if p.name.startswith(k))) if p.name.startswith(AVIF_FILES) else None
            w = None if a else _webp(raw)
            mime = "image/avif" if a else "image/webp" if w else "image/jpeg"
            files[p.name] = {"t": mime, "z": False, "d": a or w or raw}
        elif p.suffix == ".bin" and len(raw) == 2 * ncols * nrows:
            files[p.name] = {"t": _MIME[".bin"], "z": True, "delta": ncols,
                             "d": gzip.compress(_height_delta_dm(raw, ncols), 9)}
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

// three.js viewer for a geoanalysis_viewer web bundle (scene.json, terrain.bin, ortho_*.jpg).
// Coordinates: scene.json uses local metres (x east, y north). three.js: (x, height, -y).
// The viewer draws what the bundle states; overlap and distance figures are never recomputed here.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 0) => v.toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const base = new URLSearchParams(location.search).get("bundle") || ".";

$("panelToggle").addEventListener("click", () => {
  const closed = $("panel").classList.toggle("closed");
  document.body.classList.toggle("panel-closed", closed);
  $("panelToggle").setAttribute("aria-expanded", String(!closed));
});
if (matchMedia("(max-width: 760px)").matches) $("panelToggle").click(); // small screens start with the map

const [scene, heightsRaw] = await Promise.all([
  fetch(`${base}/scene.json`).then((r) => r.json()),
  fetch(`${base}/terrain.bin`).then((r) => r.arrayBuffer()),
]);
// optional: surface with trees and buildings on the same grid (heights in cm above T.z_min)
const sm = scene.surface ? new Uint16Array(await (await fetch(`${base}/${scene.surface.file}`)).arrayBuffer()) : null;
// optional: coarse ring of land out to the horizon (decimetres above scene.ring.z_min)
const ringRaw = scene.ring ? new Uint16Array(await (await fetch(`${base}/${scene.ring.file}`)).arrayBuffer()) : null;
const marks = { fetched: Math.round(performance.now()) };
const T = scene.terrain;
const [bx0, by0, bx1, by1] = scene.bbox;
const cm = new Uint16Array(heightsRaw);
if (cm.length !== T.ncols * T.nrows) throw new Error("terrain.bin does not match scene.json");

// ---------- terrain sampling (heights in metres above z_min) ----------
const hAt = (c, r) => cm[r * T.ncols + c] / 100;
function terrainHeight(x, y) {
  const fx = (x - T.x0) / T.res - 0.5, fy = (y - T.y0) / T.res - 0.5;
  const c = Math.min(Math.max(Math.floor(fx), 0), T.ncols - 2), r = Math.min(Math.max(Math.floor(fy), 0), T.nrows - 2);
  const tx = Math.min(Math.max(fx - c, 0), 1), ty = Math.min(Math.max(fy - r, 0), 1);
  return (hAt(c, r) * (1 - tx) + hAt(c + 1, r) * tx) * (1 - ty) + (hAt(c, r + 1) * (1 - tx) + hAt(c + 1, r + 1) * tx) * ty;
}

// ---------- geometry helpers (same even-odd rule as geoanalysis_viewer/geom.py) ----------
function inRings(x, y, rings) {
  let inside = false;
  for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i], [xj, yj] = r[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}
function distToRings(x, y, rings) {
  let best = Infinity;
  for (const r of rings) {
    for (let i = 0; i < r.length - 1; i++) {
      const [ax, ay] = r[i], [bx, by] = r[i + 1];
      const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
      const t = l2 ? Math.min(1, Math.max(0, ((x - ax) * dx + (y - ay) * dy) / l2)) : 0;
      const ex = x - ax - t * dx, ey = y - ay - t * dy, d2 = ex * ex + ey * ey;   // squared: one sqrt at the end
      if (d2 < best) best = d2;
    }
  }
  return Math.sqrt(best);
}
function ringsBox(polys) {
  let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
  for (const rings of polys) for (const [x, y] of rings[0]) { a = Math.min(a, x); b = Math.min(b, y); c = Math.max(c, x); d = Math.max(d, y); }
  return [a, b, c, d];
}
for (const L of scene.layers) for (const f of L.features) if (f.polygons) f.box = ringsBox(f.polygons);

// ---------- renderer, scene, camera ----------
const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const three = new THREE.Scene();
// Sky and haze: a dome from a pale, hazy horizon to a blue zenith, and exponential haze in the horizon
// colour, so distance reads as depth and the edge of the terrain melts into the sky. HAZE is the density
// for a level view (about 15 % at 5 km, 47 % at 10 km); looking down steeply there is less air in the
// way, so the render loop thins it (overviews stay clear). An illustration, not measured visibility.
const HORIZON = new THREE.Color(0xdce4ea), ZENITH = new THREE.Color(0x7ea7d4), HAZE = 0.8e-4;
three.background = HORIZON.clone();
three.fog = new THREE.FogExp2(HORIZON.getHex(), HAZE);
const sky = new THREE.Mesh(new THREE.SphereGeometry(20000, 32, 16), new THREE.ShaderMaterial({
  side: THREE.BackSide, depthWrite: false, fog: false,
  uniforms: { horizon: { value: HORIZON }, zenith: { value: ZENITH } },
  vertexShader: "varying vec3 vDir; void main() { vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: `uniform vec3 horizon; uniform vec3 zenith; varying vec3 vDir;
    void main() { float h = max(normalize(vDir).y, 0.0); gl_FragColor = vec4(mix(horizon, zenith, pow(h, 0.45)), 1.0);
    #include <colorspace_fragment>
    }`,
}));
sky.renderOrder = -1;
sky.frustumCulled = false;
three.add(sky);
const camera = new THREE.PerspectiveCamera(45, 1, 2, 30000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI / 2 - 0.01;
controls.minDistance = 20;
controls.maxDistance = 9000;

three.add(new THREE.HemisphereLight(0xffffff, 0x8a8f7a, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.7);
// Sun as on the flight day of the aerial image, so slopes are lit as the photo's own shadows are: measured
// from the shadows of isolated trees in the 0.2-m image, scaled by their height from DOM1
// (tools/sun_from_shadows.py, 21.11.2026): azimuth about 205° (195-215°), elevation about 60° (rough).
const SUN_AZ = 205 * Math.PI / 180, SUN_EL = 60 * Math.PI / 180;
sun.position.set(Math.sin(SUN_AZ) * Math.cos(SUN_EL), Math.sin(SUN_EL), -Math.cos(SUN_AZ) * Math.cos(SUN_EL)).multiplyScalar(3000);
three.add(sun);

// Photo-preserving light for surfaces that carry the aerial image: the photo already holds the light of
// the flight day, so flat ground keeps its brightness and a slope only gets lighter or darker by how
// much more or less it faces the sun than flat ground (k). The heap texture has no light of its own and
// gets a stronger k. Chains onto an existing onBeforeCompile.
function photoLight(mat, k = 0.6, key = "") {
  const prev = mat.onBeforeCompile, prevKey = mat.customProgramCacheKey;
  mat.onBeforeCompile = (sh, r) => {
    if (prev) prev(sh, r);
    sh.uniforms.shadeK = { value: k };
    sh.fragmentShader = "uniform float shadeK;\n" + sh.fragmentShader.replace("#include <opaque_fragment>", `
      #if NUM_DIR_LIGHTS > 0
      {
        vec3 sunV = directionalLights[0].direction;
        vec3 upV = normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
        float shade = clamp(1.0 + shadeK * (dot(normal, sunV) - dot(upV, sunV)), 0.5, 1.3);
        outgoingLight = diffuseColor.rgb * shade + totalEmissiveRadiance;
      }
      #endif
      #include <opaque_fragment>`);
  };
  mat.customProgramCacheKey = () => (prevKey ? prevKey.call(mat) : "") + "photo" + k + key;
  return mat;
}

const world = new THREE.Group(); // scale.y = vertical exaggeration
three.add(world);

// ---------- terrain mesh ----------
const n = T.ncols * T.nrows;
const pos = new Float32Array(n * 3), uv = new Float32Array(n * 2);
for (let r = 0; r < T.nrows; r++) {
  for (let c = 0; c < T.ncols; c++) {
    const i = r * T.ncols + c, x = T.x0 + (c + 0.5) * T.res, y = T.y0 + (r + 0.5) * T.res;
    pos[i * 3] = x; pos[i * 3 + 1] = cm[i] / 100; pos[i * 3 + 2] = -y;
    uv[i * 2] = (x - bx0) / (bx1 - bx0); uv[i * 2 + 1] = (y - by0) / (by1 - by0);
  }
}
const idx = new Uint32Array((T.ncols - 1) * (T.nrows - 1) * 6);
for (let r = 0, k = 0; r < T.nrows - 1; r++) {
  for (let c = 0; c < T.ncols - 1; c++) {
    const a = r * T.ncols + c, b = a + 1, d = a + T.ncols, e = d + 1;
    idx[k++] = a; idx[k++] = b; idx[k++] = d; idx[k++] = b; idx[k++] = e; idx[k++] = d;
  }
}
const terrainGeo = new THREE.BufferGeometry();
terrainGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
terrainGeo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
terrainGeo.setIndex(new THREE.BufferAttribute(idx, 1));
terrainGeo.computeVertexNormals();   // from the full grid: the shading keeps the 5-m detail
// Then the triangles are thinned: a right-triangulated irregular network (after mapbox/martini, ISC licence)
// keeps a triangle large wherever the 5-m grid deviates from it by at most TERRAIN_ERR_M, so flat fields get
// big triangles and slopes and edges small ones. Same vertices (positions, normals, uv), only fewer
// triangles: 1.16 M -> about 0.35 M at 0.15 m, the stated height accuracy of the DGM1 (docs/RENDERING.md).
// Heights read elsewhere (heap foot, trees, blocks, eye height) come from the grid and so differ from the
// drawn surface by at most that much.
const TERRAIN_ERR_M = 0.15;
function rtinIndex(maxErr) {
  const W = T.ncols, H = T.nrows;
  let tile = 1;
  while (tile < Math.max(W, H) - 1) tile *= 2;
  const size = tile + 1, err = new Float64Array(size * size);   // 64 bit: decimetre heights give many errors of exactly 0.1 m
  const Z = new Float64Array(size * size);   // heights in metres, padded with the last row and column
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) Z[y * size + x] = cm[Math.min(y, H - 1) * W + Math.min(x, W - 1)] / 100;
  // all triangles of the hierarchy, level by level (corners a, b, c; hypotenuse a-b), each level from the
  // one above: O(1) per triangle
  const levels = [new Uint16Array([0, 0, tile, tile, tile, 0, tile, tile, 0, 0, 0, tile])];
  for (;;) {
    const L = levels[levels.length - 1];
    // stop when the children would have legs of one cell: their midpoints are not grid points
    if (Math.abs(L[4] - ((L[0] + L[2]) >> 1)) + Math.abs(L[5] - ((L[1] + L[3]) >> 1)) <= 1) break;
    const C = new Uint16Array(L.length * 2);
    for (let t = 0, o = 0; t < L.length; t += 6) {
      const ax = L[t], ay = L[t + 1], bx = L[t + 2], by = L[t + 3], cx = L[t + 4], cy = L[t + 5];
      const mx = (ax + bx) >> 1, my = (ay + by) >> 1;
      C[o++] = cx; C[o++] = cy; C[o++] = ax; C[o++] = ay; C[o++] = mx; C[o++] = my;
      C[o++] = bx; C[o++] = by; C[o++] = cx; C[o++] = cy; C[o++] = mx; C[o++] = my;
    }
    levels.push(C);
  }
  // errors from the smallest triangles up; a midpoint carries the largest error of every triangle below
  // both triangles that share it, so the kept mesh has no cracks
  for (let l = levels.length - 1; l >= 0; l--) {
    const L = levels[l], leaf = l === levels.length - 1;
    for (let t = 0; t < L.length; t += 6) {
      const ax = L[t], ay = L[t + 1], bx = L[t + 2], by = L[t + 3], cx = L[t + 4], cy = L[t + 5];
      const mx = (ax + bx) >> 1, my = (ay + by) >> 1, mid = my * size + mx;
      // a long side reaching outside the grid (padding to 2^k + 1): always split, so no kept triangle
      // reaches out of the grid (with only the midpoint tested, the odd last row left half-cell holes)
      const out = mx >= W || my >= H || ax >= W || ay >= H || bx >= W || by >= H;
      let e = out ? Infinity : Math.abs((Z[ay * size + ax] + Z[by * size + bx]) / 2 - Z[mid]);
      if (!leaf) e = Math.max(e, err[((ay + cy) >> 1) * size + ((ax + cx) >> 1)], err[((by + cy) >> 1) * size + ((bx + cx) >> 1)]);
      if (e > err[mid]) err[mid] = e;
    }
  }
  const out = [];
  const emit = (ax, ay, bx, by, cx, cy) => {
    if (ax >= W || bx >= W || cx >= W || ay >= H || by >= H || cy >= H) return;   // outside the grid
    const a = ay * W + ax, b = by * W + bx, c = cy * W + cx;
    // counter-clockwise in (column, row), as the full grid above
    if ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0) out.push(a, b, c); else out.push(a, c, b);
  };
  const stack = [[0, 0, tile, tile, tile, 0], [tile, tile, 0, 0, 0, tile]];
  while (stack.length) {
    const [ax, ay, bx, by, cx, cy] = stack.pop();
    const mx = (ax + bx) >> 1, my = (ay + by) >> 1;
    if (Math.abs(ax - cx) + Math.abs(ay - cy) > 1 && err[my * size + mx] > maxErr) {
      stack.push([cx, cy, ax, ay, mx, my], [bx, by, cx, cy, mx, my]);
    } else emit(ax, ay, bx, by, cx, cy);
  }
  return new Uint32Array(out);
}
// done right after the first frame (see the loop), so the start is not delayed; until then the full grid
function thinTerrain() {
  const t0 = performance.now();
  terrainGeo.setIndex(new THREE.BufferAttribute(rtinIndex(TERRAIN_ERR_M), 1));
  marks.rtin = Math.round(performance.now() - t0);
}
marks.mesh = Math.round(performance.now());

// aerial image: tiles composed onto one canvas (north at the top)
const maxTex = Math.min(renderer.capabilities.maxTextureSize, 8192);
const O = scene.ortho;
const fullW = O.tiles.filter((t) => t.row === 0).reduce((s, t) => s + t.width, 0);
const fullH = O.tiles.filter((t) => t.col === 0).reduce((s, t) => s + t.height, 0);
const k = Math.min(1, maxTex / Math.max(fullW, fullH));
const orthoCanvas = Object.assign(document.createElement("canvas"), { width: Math.round(fullW * k), height: Math.round(fullH * k) });
const octx = orthoCanvas.getContext("2d");
octx.fillStyle = "#6f7f5f"; octx.fillRect(0, 0, orthoCanvas.width, orthoCanvas.height);
const orthoTex = new THREE.CanvasTexture(orthoCanvas);
orthoTex.colorSpace = THREE.SRGBColorSpace;
orthoTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
await Promise.all(O.tiles.map(async (t) => {
  // createImageBitmap decodes off the render path, so loading does not stall in a background tab
  const img = await createImageBitmap(await (await fetch(`${base}/${t.file}`)).blob());
  const x = O.tiles.filter((u) => u.row === t.row && u.col < t.col).reduce((s, u) => s + u.width, 0);
  const y = O.tiles.filter((u) => u.col === t.col && u.row < t.row).reduce((s, u) => s + u.height, 0);
  octx.drawImage(img, x * k, y * k, t.width * k, t.height * k);
}));
marks.images = Math.round(performance.now());
orthoTex.needsUpdate = true;
// Optional sharper aerial image of the near area (e.g. 0.5 m instead of 1 m per pixel). It is blended
// over the main image in the shader inside its own box, with a 40-m soft edge, so no seam shows and the
// main texture need not grow. Same uv as the main image; the box is given in that uv space.
// It is loaded after the first frame (loadDetail at the end), so a large 0.2-m image does not delay the
// start; until then the shader's weight detailOn is 0 and only the main image shows.
const D = scene.ortho_detail;
const detailU = { detailMap: { value: null }, detailOn: { value: 0 } };
async function loadDetail() {
  // a 0.2-m image can be larger than the graphics card allows; then it is scaled down while decoding
  const ks = D.width ? Math.min(1, maxTex / Math.max(D.width, D.height)) : 1;
  const img = await createImageBitmap(await (await fetch(`${base}/${D.file}`)).blob(), ks < 1
    ? { imageOrientation: "flipY", resizeWidth: Math.floor(D.width * ks), resizeHeight: Math.floor(D.height * ks), resizeQuality: "high" }
    : { imageOrientation: "flipY" });
  const tx = new THREE.Texture(img);
  tx.flipY = false;   // flipped while decoding, which ImageBitmap uploads need
  tx.colorSpace = THREE.SRGBColorSpace;
  tx.anisotropy = orthoTex.anisotropy;
  tx.needsUpdate = true;
  detailU.detailMap.value = tx;
  detailU.detailOn.value = 1;
  marks.detail = Math.round(performance.now());
}
const detailBox = D ? new THREE.Vector4((D.bbox[0] - bx0) / (bx1 - bx0), (D.bbox[1] - by0) / (by1 - by0),
  (D.bbox[2] - bx0) / (bx1 - bx0), (D.bbox[3] - by0) / (by1 - by0)) : null;
const detailFeather = D ? new THREE.Vector2(40 / (D.bbox[2] - D.bbox[0]), 40 / (D.bbox[3] - D.bbox[1])) : null;
function withDetail(mat) {
  if (!D) return mat;
  mat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, detailU, { detailBox: { value: detailBox }, detailFeather: { value: detailFeather } });
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <map_pars_fragment>", "#include <map_pars_fragment>\nuniform sampler2D detailMap; uniform float detailOn; uniform vec4 detailBox; uniform vec2 detailFeather;")
      .replace("#include <map_fragment>", `#include <map_fragment>
      {
        vec2 du = (vMapUv - detailBox.xy) / (detailBox.zw - detailBox.xy);
        vec2 edge = min(du, 1.0 - du) / detailFeather;
        float w = clamp(min(edge.x, edge.y), 0.0, 1.0) * detailOn;
        if (w > 0.0) diffuseColor.rgb = mix(diffuseColor.rgb, texture2D(detailMap, du).rgb, w);
      }`);
  };
  mat.customProgramCacheKey = () => "detail";
  return mat;
}
const terrain = new THREE.Mesh(terrainGeo, photoLight(withDetail(new THREE.MeshLambertMaterial({ map: orthoTex }))));
world.add(terrain);

// ---------- horizon ring: coarse land (Copernicus DEM, 60 m) around the detailed terrain ----------
let ringMat = null, ringMesh = null, ringImageState = "";   // "", "loading", "done"
// Only so the skyline is real; it has no aerial image and is drawn in a neutral land colour with the
// same slope shading. Inside the detailed area it lies 2 m below the DGM1 and its cells are skipped,
// except one row of overlap at the seam, where the heights blend into the DGM1 edge.
if (ringRaw) {
  const R = scene.ring, nR = R.n, rp = new Float32Array(nR * nR * 3);
  for (let r = 0; r < nR; r++) for (let c = 0; c < nR; c++) {
    const i = r * nR + c;
    rp[i * 3] = R.x0 + c * R.res; rp[i * 3 + 1] = R.z_min + ringRaw[i] / 10 - T.z_min; rp[i * 3 + 2] = -(R.y0 + r * R.res);
  }
  const [ix0, iy0, ix1, iy1] = R.inner, inside = (x, y) => x > ix0 + R.res && x < ix1 - R.res && y > iy0 + R.res && y < iy1 - R.res;
  const idx = [];
  for (let r = 0; r < nR - 1; r++) for (let c = 0; c < nR - 1; c++) {
    const x = R.x0 + c * R.res, y = R.y0 + r * R.res;
    if (inside(x, y) && inside(x + R.res, y) && inside(x, y + R.res) && inside(x + R.res, y + R.res)) continue;
    const a = r * nR + c, b = a + 1, d = a + nR, e = d + 1;
    idx.push(a, b, d, b, e, d);   // counter-clockwise seen from above (z = -north)
  }
  const ruv = new Float32Array(nR * nR * 2), span = (nR - 1) * R.res;
  for (let r = 0; r < nR; r++) for (let c = 0; c < nR; c++) { ruv[(r * nR + c) * 2] = c * R.res / span; ruv[(r * nR + c) * 2 + 1] = r * R.res / span; }
  const rg = new THREE.BufferGeometry();
  rg.setAttribute("position", new THREE.BufferAttribute(rp, 3));
  rg.setAttribute("uv", new THREE.BufferAttribute(ruv, 2));
  rg.setIndex(idx);
  rg.computeVertexNormals();
  ringMat = photoLight(new THREE.MeshLambertMaterial({ color: 0x8c976b }), 0.6, "ring");
  ringMesh = new THREE.Mesh(rg, ringMat);
  ringMesh.renderOrder = -0.5;
  ringMesh.visible = $("ringOn").checked;
  world.add(ringMesh);
}
// The ring's coarse aerial image (one tile) is loaded after the first frame, and only while the ring is
// shown; until then neutral colour.
function showRing(on) {
  if (!ringMesh) return;
  ringMesh.visible = on;
  if (on && scene.ring.image && !ringImageState && marks.firstFrame) {
    ringImageState = "loading";
    loadRingImage().then(() => (ringImageState = "done")).catch((e) => console.warn("Horizont-Luftbild:", e));
  }
}
async function loadRingImage() {
  const t = scene.ring.image.tiles[0];
  const tx = new THREE.Texture(await createImageBitmap(await (await fetch(`${base}/${t.file}`)).blob(), { imageOrientation: "flipY" }));
  tx.flipY = false; tx.colorSpace = THREE.SRGBColorSpace; tx.anisotropy = orthoTex.anisotropy; tx.needsUpdate = true;
  ringMat.map = tx; ringMat.color.set(0xffffff); ringMat.needsUpdate = true;
  marks.ringImage = Math.round(performance.now());
}

// ---------- overlay: zones drawn onto a second texture, mixed into the terrain shader (withOverlay) ----------
// half the resolution of the aerial image (about 2 m per pixel): zones and lines need no more, and the
// texture is a quarter of the size to upload on every change
const ovCanvas = Object.assign(document.createElement("canvas"), { width: Math.ceil(orthoCanvas.width / 2), height: Math.ceil(orthoCanvas.height / 2) });
let ovx = ovCanvas.getContext("2d");
const ovTex = new THREE.CanvasTexture(ovCanvas);
ovTex.colorSpace = THREE.SRGBColorSpace;
ovTex.anisotropy = orthoTex.anisotropy;
// Near the site the same layers are drawn a second time at NEAR_MPP onto a smaller canvas, which the
// shader uses inside its box: outlines and fills there are as sharp as the 0.2-m aerial image needs,
// while the whole map stays at 2 m. Box: that of the near-area aerial image, else the site + 200 m.
const NEAR_MPP = 0.4;
const nearOn = { value: 0 };   // set after the first frame, so its upload does not delay the start
const sbox0 = ringsBox([scene.site.rings]);
const nearBox = D ? D.bbox : [sbox0[0] - 200, sbox0[1] - 200, sbox0[2] + 200, sbox0[3] + 200];
const nearCanvas = Object.assign(document.createElement("canvas"), {
  width: Math.min(maxTex, Math.ceil((nearBox[2] - nearBox[0]) / NEAR_MPP)), height: Math.min(maxTex, Math.ceil((nearBox[3] - nearBox[1]) / NEAR_MPP)) });
const nearTex = new THREE.CanvasTexture(nearCanvas);
nearTex.colorSpace = THREE.SRGBColorSpace;
nearTex.anisotropy = orthoTex.anisotropy;
const nearUv = new THREE.Vector4((nearBox[0] - bx0) / (bx1 - bx0), (nearBox[1] - by0) / (by1 - by0),
  (nearBox[2] - bx0) / (bx1 - bx0), (nearBox[3] - by0) / (by1 - by0));
// The layers are mixed into the terrain's own shader, not drawn as a second, transparent copy of the
// terrain mesh (1.16 M triangles once instead of twice). Same picture: they are mixed after the light and
// the conversion to sRGB, where the blending of the second pass happened, and before the haze, which is
// the same for both. Inside the near box the sharper near canvas is used.
// Fills of the zones fade where the view grazes the ground (eye-level views): there many fills stack up
// on screen and cover the land. Outlines, lines and points (texture alpha near 1) stay; the fills are
// drawn with alpha 0.38, so the shader tells them apart by alpha. Top and oblique views are unchanged.
const ovU = { ovMap: { value: ovTex }, ovOpacity: { value: 0.7 }, nearMap: { value: nearTex }, nearBox: { value: nearUv }, nearOn };
function withOverlay(mat) {
  const prev = mat.onBeforeCompile, prevKey = mat.customProgramCacheKey;
  mat.onBeforeCompile = (sh, r) => {
    if (prev) prev(sh, r);
    Object.assign(sh.uniforms, ovU);
    sh.vertexShader = "varying float vGraze;\n" + sh.vertexShader.replace("#include <begin_vertex>", `#include <begin_vertex>
    vGraze = abs(normalize((modelMatrix * vec4(transformed, 1.0)).xyz - cameraPosition).y);`);
    sh.fragmentShader = "varying float vGraze;\nuniform sampler2D ovMap; uniform float ovOpacity; uniform sampler2D nearMap; uniform vec4 nearBox; uniform float nearOn;\n" +
      sh.fragmentShader.replace("#include <colorspace_fragment>", `#include <colorspace_fragment>
    {
      vec4 ov = texture2D(ovMap, vMapUv);
      vec2 nu = (vMapUv - nearBox.xy) / (nearBox.zw - nearBox.xy);
      if (nearOn > 0.5 && all(greaterThan(nu, vec2(0.002))) && all(lessThan(nu, vec2(0.998)))) ov = texture2D(nearMap, nu);
      float a = ov.a * ovOpacity;
      if (a < 0.9 * ovOpacity) a *= mix(0.2, 1.0, smoothstep(0.04, 0.3, vGraze));
      gl_FragColor.rgb = mix(gl_FragColor.rgb, linearToOutputTexel(vec4(ov.rgb, 1.0)).rgb, a);
    }`);
  };
  mat.customProgramCacheKey = () => (prevKey ? prevKey.call(mat) : "") + "ov";
  return mat;
}
withOverlay(terrain.material);
terrain.material.needsUpdate = true;

// The red line of the search area as geometry, not as pixels of the overlay: a band on the ground
// (points every 2 m, lifted 0.4 m), sharp at any distance. The paler band is the tolerance of the
// traced line (± tolerance_m).
function ribbon(rings, width, color, opacity, lift, order) {
  const P = [], I = [], OFF = [];
  for (const ring0 of rings) {
    const ring = [];
    for (let k = 0; k < ring0.length - 1; k++) {
      const [ax, ay] = ring0[k], [bx, by] = ring0[k + 1], m = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / 2));
      for (let j = 0; j < m; j++) ring.push([ax + ((bx - ax) * j) / m, ay + ((by - ay) * j) / m]);
    }
    const nr = ring.length, base = P.length / 3;
    for (let k = 0; k < nr; k++) {
      const [px, py] = ring[(k - 1 + nr) % nr], [x, y] = ring[k], [qx, qy] = ring[(k + 1) % nr];
      let dx = qx - px, dy = qy - py; const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
      const ox = -dy * width / 2, oy = dx * width / 2;
      for (const s of [1, -1]) { const X = x + s * ox, Y = y + s * oy; P.push(X, terrainHeight(X, Y) + lift, -Y); OFF.push(-s * dy, -s * dx); }
    }
    for (let k = 0; k < nr; k++) {
      const a = base + 2 * k, b = base + 2 * ((k + 1) % nr);
      I.push(a, a + 1, b, a + 1, b + 1, b);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(P, 3));
  g.setAttribute("off", new THREE.Float32BufferAttribute(OFF, 2));
  g.setIndex(I);
  const mat = new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: false,
    side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -8, polygonOffsetUnits: -8 });
  // far away the band widens so it never gets thinner than minPx screen pixels (vertex shader)
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.pxRad = ribbonPx; sh.uniforms.halfW = { value: width / 2 }; sh.uniforms.minPx = { value: width > 3 ? 4 : 2.2 };
    sh.vertexShader = "attribute vec2 off; uniform float pxRad; uniform float halfW; uniform float minPx;\n" + sh.vertexShader.replace("#include <begin_vertex>",
      `#include <begin_vertex>
      float dist = length((modelViewMatrix * vec4(position, 1.0)).xyz);
      float extra = max(0.0, dist * pxRad * minPx / 2.0 - halfW);
      transformed.x += off.x * extra; transformed.z += off.y * extra;`);
  };
  mat.customProgramCacheKey = () => "ribbon" + width;
  const m = new THREE.Mesh(g, mat);
  m.renderOrder = order;
  world.add(m);
  return m;
}
const ribbonPx = { value: 0.001 };   // radians per screen pixel, set on resize
const siteTol = scene.site.tolerance_m || 0;
const siteBand = siteTol ? ribbon(scene.site.rings, 2 * siteTol, 0xe10600, 0.3, 0.4, 4) : null;
const siteLine = ribbon(scene.site.rings, 2, 0xe10600, 1, 0.45, 5);
const state = { layers:Object.fromEntries(scene.layers.map((L) => [L.id, L.on && L.features.length > 0])), site: true };
function tracePolys(polys) {
  ovx.beginPath();
  for (const rings of polys) for (const r of rings) { r.forEach(([x, y], i) => (i ? ovx.lineTo(x, y) : ovx.moveTo(x, y))); ovx.closePath(); }
}
function traceLines(lines) {
  ovx.beginPath();
  for (const l of lines) l.forEach(([x, y], i) => (i ? ovx.lineTo(x, y) : ovx.moveTo(x, y)));
}
function drawOverlay() {
  drawLayers(ovCanvas, [bx0, by0, bx1, by1]);
  ovTex.needsUpdate = true;
  if (nearOn.value) { drawLayers(nearCanvas, nearBox); nearTex.needsUpdate = true; }
  siteLine.visible = state.site;   // the red line is a band of its own (ribbon above)
  if (siteBand) siteBand.visible = state.site;
}
function drawLayers(cv, [x0, y0, x1, y1]) {
  ovx = cv.getContext("2d");
  const sx = cv.width / (x1 - x0), sy = cv.height / (y1 - y0);
  ovx.setTransform(1, 0, 0, 1, 0, 0);
  ovx.clearRect(0, 0, cv.width, cv.height);
  ovx.setTransform(sx, 0, 0, -sy, -x0 * sx, y1 * sy); // draw in local metres, north up
  ovx.lineJoin = "round"; ovx.lineCap = "round";
  for (const L of scene.layers) {
    if (!state.layers[L.id]) continue;
    for (const f of L.features) {
      if (f.polygons) {
        tracePolys(f.polygons);
        ovx.globalAlpha = 0.38; ovx.fillStyle = L.color; ovx.fill("evenodd");
        // outline 2.5 m on the 2-m map (so it shows at all), 1.2 m on the sharp near canvas
        ovx.globalAlpha = 1; ovx.strokeStyle = L.color; ovx.lineWidth = sx > 1 ? 1.2 : 2.5; ovx.stroke();
      }
      if (f.lines) { traceLines(f.lines); ovx.globalAlpha = 1; ovx.strokeStyle = L.color; ovx.lineWidth = 5; ovx.stroke(); }
      for (const [x, y] of f.points || []) {
        ovx.beginPath(); ovx.arc(x, y, 14, 0, 2 * Math.PI);
        ovx.globalAlpha = 1; ovx.fillStyle = L.color; ovx.fill(); ovx.strokeStyle = "#fff"; ovx.lineWidth = 3; ovx.stroke();
      }
    }
  }
  for (const v of scene.viewpoints) {
    ovx.beginPath(); ovx.arc(v.x, v.y, 10, 0, 2 * Math.PI);
    ovx.globalAlpha = 1; ovx.fillStyle = "#fff"; ovx.fill(); ovx.strokeStyle = "#111"; ovx.lineWidth = 3; ovx.stroke();
  }
}

// ---------- heap scenario ----------
// Signed distance of each terrain cell centre to the site outline (positive inside, negative outside),
// site bbox plus one cell. The heap itself is drawn on a finer grid (HEAP_SUB points per terrain cell),
// so steps in the slope (berms) a few metres wide are not lost on the 5-m grid; the distance there is
// interpolated from these cells.
marks.heapDist0 = Math.round(performance.now());
const siteRings = scene.site.rings;
const sbox = ringsBox([siteRings]);
const c0 = Math.max(0, Math.floor((sbox[0] - T.x0) / T.res) - 1), c1 = Math.min(T.ncols - 1, Math.ceil((sbox[2] - T.x0) / T.res) + 1);
const r0 = Math.max(0, Math.floor((sbox[1] - T.y0) / T.res) - 1), r1 = Math.min(T.nrows - 1, Math.ceil((sbox[3] - T.y0) / T.res) + 1);
const hc = c1 - c0 + 1, hr = r1 - r0 + 1;
const inner = new Float32Array(hc * hr);
for (let r = 0; r < hr; r++) {
  for (let c = 0; c < hc; c++) {
    const x = T.x0 + (c0 + c + 0.5) * T.res, y = T.y0 + (r0 + r + 0.5) * T.res, d = distToRings(x, y, siteRings);
    inner[r * hc + c] = inRings(x, y, siteRings) ? d : -d;
  }
}
const HEAP_SUB = 3, HR = T.res / HEAP_SUB;          // 1.67 m on the 5-m terrain
const fcN = (hc - 1) * HEAP_SUB + 1, frN = (hr - 1) * HEAP_SUB + 1, fN = fcN * frN;
const fDist = new Float32Array(fN), fGround = new Float32Array(fN);
for (let j = 0; j < frN; j++) {
  for (let i = 0; i < fcN; i++) {
    const cc = Math.min(Math.floor(i / HEAP_SUB), hc - 2), rr = Math.min(Math.floor(j / HEAP_SUB), hr - 2);
    const tx = i / HEAP_SUB - cc, ty = j / HEAP_SUB - rr, k = j * fcN + i;
    const q00 = inner[rr * hc + cc], q10 = inner[rr * hc + cc + 1], q01 = inner[(rr + 1) * hc + cc], q11 = inner[(rr + 1) * hc + cc + 1];
    fDist[k] = (q00 * (1 - tx) + q10 * tx) * (1 - ty) + (q01 * (1 - tx) + q11 * tx) * ty;
    // ground on the same two triangles per cell as the terrain mesh (a,b,d and b,e,d), so the heap's
    // foot lies exactly on the drawn ground
    const ga = hAt(c0 + cc, r0 + rr), gb = hAt(c0 + cc + 1, r0 + rr), gd = hAt(c0 + cc, r0 + rr + 1), ge = hAt(c0 + cc + 1, r0 + rr + 1);
    fGround[k] = tx + ty <= 1 ? ga + (gb - ga) * tx + (gd - ga) * ty : ge + (gd - ge) * (1 - tx) + (gb - ge) * (1 - ty);
  }
}
const heapGeo = new THREE.BufferGeometry();
const heapPos = new Float32Array(fN * 3);
heapGeo.setAttribute("position", new THREE.BufferAttribute(heapPos, 3));
// uv  = ground position / SURF_TILE: the surface texture repeats every SURF_TILE metres
// uv1 = (0, fill height / 5): one contour line per 5 m on a second, transparent mesh
const SURF_TILE = 450; // larger than half the site, so the repeat is not noticeable
heapGeo.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(fN * 2), 2));
heapGeo.setAttribute("uv1", new THREE.BufferAttribute(new Float32Array(fN * 2), 2));
// foot = metres past the foot of the heap (negative outside). The fragment shader drops everything
// with foot < 0, so the edge follows the interpolated foot line instead of the grid.
heapGeo.setAttribute("foot", new THREE.BufferAttribute(new Float32Array(fN), 1));
for (let j = 0; j < frN; j++) for (let i = 0; i < fcN; i++) {
  const k = j * fcN + i, x = T.x0 + (c0 + 0.5) * T.res + i * HR, y = T.y0 + (r0 + 0.5) * T.res + j * HR;
  heapPos[k * 3] = x; heapPos[k * 3 + 2] = -y;
  heapGeo.attributes.uv.array[k * 2] = x / SURF_TILE; heapGeo.attributes.uv.array[k * 2 + 1] = y / SURF_TILE;
}
marks.heapGrid = Math.round(performance.now());
// grain: a fine tileable noise multiplied into the heap surface every GRAIN_M metres, for some texture
// close by; it fades out from about 1 m per screen pixel, where the material map carries the look
const GRAIN_M = 6;
let grainTex = null;
function makeGrain() {
  const size = 256, cv = Object.assign(document.createElement("canvas"), { width: size, height: size });
  const ctx = cv.getContext("2d"), img = ctx.createImageData(size, size);
  const a = makeNoise(size, 71), b = makeNoise(size, 89);
  let s = 7;
  const rnd = () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
  for (let i = 0; i < size * size; i++) {
    const v = 128 + (a[i] - 0.5) * 70 + (b[i] - 0.5) * 50 + (rnd() - 0.5) * 60;
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = Math.max(0, Math.min(255, v)); img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const tx = new THREE.CanvasTexture(cv);
  tx.wrapS = tx.wrapT = THREE.RepeatWrapping;
  tx.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return tx;
}
// uniforms of the heap shader: grain repeats per uv unit of the material map (set in buildHeap)
const heapGrainScale = { value: 1 }, heapGrainAmt = { value: 0.5 };
function footClip(mat, grain = false) {
  mat.onBeforeCompile = (sh) => {
    sh.vertexShader = "attribute float foot;\nvarying float vFoot;\n" +
      sh.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\nvFoot = foot;");
    sh.fragmentShader = "varying float vFoot;\n" + sh.fragmentShader.replace("#include <clipping_planes_fragment>", "#include <clipping_planes_fragment>\nif (vFoot < 0.0) discard;");
    if (grain) {
      Object.assign(sh.uniforms, { grainMap: { value: (grainTex ||= makeGrain()) }, grainScale: heapGrainScale, grainAmt: heapGrainAmt });
      sh.fragmentShader = sh.fragmentShader
        .replace("#include <map_pars_fragment>", "#include <map_pars_fragment>\nuniform sampler2D grainMap; uniform float grainScale; uniform float grainAmt;")
        .replace("#include <map_fragment>", `#include <map_fragment>
#ifdef USE_MAP
{
  // grain tiles per screen pixel; one tile is GRAIN_M metres, so 0.15 is about 1 m per pixel
  // (no read at all once it has faded, as in the default view)
  vec2 gu = vMapUv * grainScale, gx = dFdx(gu), gy = dFdy(gu);
  float far = smoothstep(0.08, 0.2, max(length(gx), length(gy)));
  if (far < 1.0) diffuseColor.rgb *= mix(1.0, 0.7 + 0.6 * textureGrad(grainMap, gu, gx, gy).r, grainAmt * (1.0 - far));
}
#endif`);
    }
  };
  mat.customProgramCacheKey = () => (grain ? "foot-grain5" : "foot3");
  return mat;
}

// Tileable value noise (seeded, so the picture is the same on every load).
function makeNoise(size, seed) {
  let s = seed >>> 0;
  const rnd = () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
  const out = new Float32Array(size * size);
  let total = 0;
  for (let cells = 8, amp = 1; cells <= size / 2; cells *= 2, amp *= 0.55) {
    const g = new Float32Array(cells * cells).map(rnd), step = size / cells;
    for (let y = 0; y < size; y++) {
      const fy = y / step, y0 = Math.floor(fy), ty = fy - y0, sy = ty * ty * (3 - 2 * ty);
      for (let x = 0; x < size; x++) {
        const fx = x / step, x0 = Math.floor(fx), tx = fx - x0, sx = tx * tx * (3 - 2 * tx);
        const a = g[(y0 % cells) * cells + (x0 % cells)], b = g[(y0 % cells) * cells + ((x0 + 1) % cells)];
        const c = g[((y0 + 1) % cells) * cells + (x0 % cells)], d = g[((y0 + 1) % cells) * cells + ((x0 + 1) % cells)];
        out[y * size + x] += amp * ((a * (1 - sx) + b * sx) * (1 - sy) + (c * (1 - sx) + d * sx) * sy);
      }
    }
    total += amp;
  }
  for (let i = 0; i < out.length; i++) out[i] /= total;
  return out;
}
const lerp3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const smooth = (lo, hi, v) => { const t = Math.min(1, Math.max(0, (v - lo) / (hi - lo))); return t * t * (3 - 2 * t); };
// ---------- material map of the heap ----------
// From the start view (about 1.5 m per screen pixel) only patches several metres wide can be seen, so the
// surface is one map over the whole heap (one pixel per grid point, 1.67 m; finer cost 4 times as much), drawn
// from what each spot is: ring road, track on a berm, finished slope (grass), cells on the top (compacted
// fill, soil cover, temporary sheets, grass, the area being filled). Colours measured from DOP20 aerial
// images (LGL) of three operating landfills for mineral waste in Baden-Württemberg (19.11.2026, k-means
// over the landfill areas; docs/RENDERING.md); the layout and the shares are assumptions. The map is drawn
// again with the heap, so it follows berms and outline. One texture read (plus the grain close by).
const MAT_PX = 2, matW = (fcN - 1) * MAT_PX + 1, matH = (frN - 1) * MAT_PX + 1;
const matCanvas = Object.assign(document.createElement("canvas"), { width: matW, height: matH });
const matCtx = matCanvas.getContext("2d"), matImg = matCtx.createImageData(matW, matH);
const matTex = new THREE.CanvasTexture(matCanvas);
matTex.colorSpace = THREE.SRGBColorSpace;
matTex.anisotropy = orthoTex.anisotropy;
// the uv of the heap mesh is position / SURF_TILE; map it onto the grid (canvas top row = northern edge)
const gx0 = T.x0 + (c0 + 0.5) * T.res, gy0 = T.y0 + (r0 + 0.5) * T.res, gW = (fcN - 1) * HR, gH = (frN - 1) * HR;
matTex.repeat.set(SURF_TILE / gW, SURF_TILE / gH);
matTex.offset.set(-gx0 / gW, -gy0 / gH);
heapGrainScale.value = gW / GRAIN_M;
// sRGB. Brightness from cluster means of DOP20 images of operating landfills (the aerial image of the
// surroundings is DOP20 too, so the heap must not be darker than its neighbours); the warm hue of the
// operating surface (red about 30 above blue) from photos of operating landfills (docs/RENDERING.md).
const PAL = {
  track: [218, 208, 190], ditch: [146, 134, 116], plat: [194, 181, 160], plat2: [170, 156, 135],
  slope: [160, 146, 128], slopeL: [180, 166, 147], slopeD: [124, 112, 97],
  loose: [156, 140, 118], loose2: [138, 124, 106], pile: [150, 145, 136],
  grass: [108, 118, 100], grass2: [92, 105, 86], grass3: [126, 131, 118], dry: [160, 154, 124],
};
const matNoise = [makeNoise(256, 301), makeNoise(256, 307), makeNoise(256, 311)];
// tileable noise at world position (metres), period `per` metres
function wnoise(a, x, y, per) {
  const N = 256, u = ((x / per) % 1 + 1) % 1 * N, v = ((y / per) % 1 + 1) % 1 * N;
  const i0 = Math.floor(u), j0 = Math.floor(v), tu = u - i0, tv = v - j0, i1 = (i0 + 1) % N, j1 = (j0 + 1) % N;
  return (a[j0 * N + i0] * (1 - tu) + a[j0 * N + i1] * tu) * (1 - tv) + (a[j1 * N + i0] * (1 - tu) + a[j1 * N + i1] * tu) * tv;
}
const hash2 = (i, j, s = 0) => { let h = Math.imul(i, 374761393) ^ Math.imul(j, 668265263) ^ Math.imul(s, 2246822519); h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };
// per pixel and fixed for the page: three noises and the position along / across the main axis, bent by a
// smooth noise so the cell borders are not ruled lines. Made on the first bake.
let bakeStatic = null;
function bakeFields(m) {
  if (bakeStatic) return bakeStatic;
  const n = matW * matH, n1 = new Float32Array(n), n2 = new Float32Array(n), n3 = new Float32Array(n), uu = new Float32Array(n), vv = new Float32Array(n);
  for (let r = 0; r < matH; r++) {
    const gj = (matH - 1 - r) / MAT_PX, y = gy0 + gj * HR;
    for (let c = 0; c < matW; c++) {
      const gi = c / MAT_PX, x = gx0 + gi * HR, o = r * matW + c;
      n1[o] = wnoise(matNoise[0], x, y, 420); n2[o] = wnoise(matNoise[1], x, y, 130); n3[o] = wnoise(matNoise[2], x, y, 45);
      const i0 = Math.min(Math.floor(gi), fcN - 2), j0 = Math.min(Math.floor(gj), frN - 2), ti = gi - i0, tj = gj - j0, k = j0 * fcN + i0;
      const bl = (a) => (a[k] * (1 - ti) + a[k + 1] * ti) * (1 - tj) + (a[k + fcN] * (1 - ti) + a[k + fcN + 1] * ti) * tj;
      uu[o] = bl(m.u) + 28 * (wnoise(matNoise[1], x + 517, y, 260) - 0.5);
      vv[o] = bl(m.v) + 28 * (wnoise(matNoise[2], x, y + 733, 260) - 0.5);
    }
  }
  return (bakeStatic = { n1, n2, n3, uu, vv });
}
// Grayscale detail tiles (tools/textures/make_detail.py): the light/dark structure of a few metres (tyre
// lanes, dumped heaps, rills) from generated images, stored at about the map's resolution; 128 = no
// change. Only the brightness is taken from them; the colours stay the measured ones.
const DET = {};
for (const [name, t] of Object.entries(scene.heap_detail?.tiles || {})) {
  const bin = atob(t.data), a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  DET[name] = { ...t, a };
}
const DET_K = { top: 0.75, loose: 0.7, slope: 0.65 };   // strength of each tile (1 = as stored)
// factor at (x, y) in tile pixels, bilinear, wrapped across x (and y where the tile wraps)
function detAt(t, x, y) {
  const w = t.w, h = t.h, wy = t.wrap.includes("y");
  x = ((x % w) + w) % w;
  y = wy ? ((y % h) + h) % h : Math.min(h - 1, Math.max(0, y));
  const i0 = Math.floor(x), j0 = Math.min(Math.floor(y), h - 1), tx = x - i0, ty = y - j0;
  const i1 = (i0 + 1) % w, j1 = wy ? (j0 + 1) % h : Math.min(j0 + 1, h - 1), a = t.a;
  return ((a[j0 * w + i0] * (1 - tx) + a[j0 * w + i1] * tx) * (1 - ty) + (a[j1 * w + i0] * (1 - tx) + a[j1 * w + i1] * tx) * ty) / 128;
}
// position along the straightened outline (metres, 0 ... its length) of the nearest point on it, per grid
// point: the slope tile runs along it, so its rills stand in the fall line. Fixed for the page.
let alongStatic = null;
function alongOutline() {
  if (alongStatic) return alongStatic;
  const S = formHelpers().S, n = S.length, cum = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) { const [ax, ay] = S[i], [bx, by] = S[(i + 1) % n]; cum[i + 1] = cum[i] + Math.hypot(bx - ax, by - ay); }
  const L = cum[n], A = new Float32Array(fN);
  for (let j = 0; j < frN; j++) for (let i = 0; i < fcN; i++) {
    const x = gx0 + i * HR, y = gy0 + j * HR;
    let best = Infinity, s = 0;
    for (let e = 0; e < n; e++) {
      const [ax, ay] = S[e], [bx, by] = S[(e + 1) % n], dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1;
      const t = Math.min(1, Math.max(0, ((x - ax) * dx + (y - ay) * dy) / l2)), ex = ax + t * dx - x, ey = ay + t * dy - y, d2 = ex * ex + ey * ey;
      if (d2 < best) { best = d2; s = cum[e] + t * Math.sqrt(l2); }
    }
    A[j * fcN + i] = s;
  }
  return (alongStatic = { A, L });
}
// m: per grid point inset p (metres inside the foot) and td (profile without top minus drawn height: > 0 on
// the top), u, v, pile (height of the tipping piles); geo: road width, berm step and width, slope, index of
// the uppermost terrace, centre of the working area (cu, cv)
function bakeMaterial(kind, m, geo) {
  const d = matImg.data, S = bakeFields(m), op = kind === "betrieb";
  const AL = op && DET.slope ? alongOutline() : null;
  // the slope tile repeats a whole number of times round the outline, so it has no seam
  const slopeW = AL ? AL.L / Math.max(1, Math.round(AL.L / DET.slope.mw)) : 1;
  const lift = (f, k) => 1 + k * (f - 1);
  const per = geo.bermH ? geo.bermH * geo.slopeN + geo.bermW : Infinity, rise = geo.bermH * geo.slopeN;
  // foot point of each grid point: where the fall line through it meets the foot line. A noise read there
  // is the same all the way down the slope and changes along it, so it draws erosion rills.
  const P = m.p, FX = new Float32Array(fN), FY = new Float32Array(fN);
  for (let j = 0; j < frN; j++) for (let i = 0; i < fcN; i++) {
    const k = j * fcN + i;
    const gx = P[j * fcN + Math.min(i + 1, fcN - 1)] - P[j * fcN + Math.max(i - 1, 0)];
    const gy = P[Math.min(j + 1, frN - 1) * fcN + i] - P[Math.max(j - 1, 0) * fcN + i];
    const l = Math.hypot(gx, gy) || 1;
    FX[k] = gx0 + i * HR - (P[k] * gx) / l; FY[k] = gy0 + j * HR - (P[k] * gy) / l;
  }
  // driving tracks on the top, about 6 m wide, in smooth bends: one along the main axis through the working
  // area, and cross tracks every 150 m or so (some left out)
  const trackAt = (u, v) => {
    const cv = geo.cv ?? 1e4, along = Math.abs(v - (cv + 28 * Math.sin(u / 170) + 12 * Math.sin(u / 61 + 1)));
    const ic = Math.round((u - 1e4) / 150), uc = 1e4 + ic * 150 + 35 * Math.sin(v / 130 + ic);
    const cross = hash2(ic, 0, 41) < 0.7 ? Math.abs(u - uc) : 99;
    return 1 - smooth(2.4, 3.8, Math.min(along, cross));
  };
  for (let r = 0; r < matH; r++) {
    const gj = (matH - 1 - r) / MAT_PX, j0 = Math.min(Math.floor(gj), frN - 2), tj = gj - j0;
    for (let c = 0; c < matW; c++) {
      const gi = c / MAT_PX, i0 = Math.min(Math.floor(gi), fcN - 2), ti = gi - i0, k = j0 * fcN + i0, o = r * matW + c, o4 = o * 4;
      const bl = (a) => (a[k] * (1 - ti) + a[k + 1] * ti) * (1 - tj) + (a[k + fcN] * (1 - ti) + a[k + fcN + 1] * ti) * tj;
      const p = bl(m.p);
      if (p < -2) { d[o4] = d[o4 + 1] = d[o4 + 2] = 128; d[o4 + 3] = 255; continue; }
      const n1 = S.n1[o], n2 = S.n2[o], n3 = S.n3[o], q = p - geo.road, onTop = bl(m.td) > 0.6;
      let col;
      const qm = geo.bermH ? q % per : 0, px = HR / MAT_PX;
      // share of this pixel on a berm track (soft over one pixel, so the edges do not step)
      const onBerm = !onTop && geo.bermH && q > 0 ? smooth(rise - px / 2, rise + px / 2, qm) * (1 - smooth(per - 1.3 - px / 2, per - 1.3 + px / 2, qm) * 0.5) : 0;
      if (q < px / 2) {   // ring road at the foot, with a darker verge on both sides
        col = lerp3(PAL.track, PAL.ditch, Math.max(smooth(-1.2 - px / 2, -1.2 + px / 2, q), 1 - smooth(1.2 - px / 2, 1.2 + px / 2, p)));
      } else if (onBerm > 0.98) {   // berm with a track; ditch on the inner side
        col = per - qm < 1.3 ? PAL.ditch : PAL.track;
      } else if (!onTop) {
        if (op) {
          // slopes in the operating state: no grass (only mineral surfaces are shown while it is operated);
          // open fill, darker than the berm tracks, with rills down the fall line, fines collected at the
          // toe and a lighter crest. This light/dark banding is what makes the terraces visible from afar.
          const qq = geo.bermH ? qm : q;
          col = lerp3(PAL.slope, PAL.slopeL, 0.4 * n2);
          if (AL) {
            // generated rills: the tile runs along the outline and spans one slope face from toe to crest
            // (without berms: every 30 m)
            const a0 = AL.A[k], un = (a) => (a - a0 > AL.L / 2 ? a - AL.L : a0 - a > AL.L / 2 ? a + AL.L : a);
            const al = (un(AL.A[k]) * (1 - ti) + un(AL.A[k + 1]) * ti) * (1 - tj) + (un(AL.A[k + fcN]) * (1 - ti) + un(AL.A[k + fcN + 1]) * ti) * tj;
            // each terrace shifted along by its own amount, so the rills of two faces do not line up
            const tl = DET.slope, fv = geo.bermH ? qm / rise : (q % 30) / 30, sh = hash2(geo.bermH ? Math.floor(q / per) : Math.floor(q / 30), 0, 51) * tl.w;
            // two reads at different scales along the slope (no even hatching that could beat with the
            // screen pixels), and a slow change of strength along the outline: rilled and smoother stretches
            const yy = (1 - fv) * (tl.h - 1), xa = (al / slopeW) * tl.w + sh;
            const f0 = 0.55 * detAt(tl, xa, yy) + 0.45 * detAt(tl, xa * 0.61 + 17, yy);
            const f = lift(f0, DET_K.slope * (0.35 + 0.9 * smooth(0.35, 0.65, wnoise(matNoise[0], al, sh * 7, 1600))));
            col = [col[0] * f, col[1] * f, col[2] * f];
          } else {
            // noise features of about 3 and 8 m along the contour, contrast stretched; nothing coarser, so
            // the slopes read as even bands from the start view instead of dashes
            const fx = bl(FX), fy = bl(FY);
            const rill = 0.5 + 1.5 * (0.55 * wnoise(matNoise[2], fx, fy, 24) + 0.45 * wnoise(matNoise[1], fx + 91, fy - 37, 60) - 0.5);
            col = lerp3(col, PAL.slopeD, 0.75 * smooth(0.52, 0.28, rill));
            col = lerp3(col, PAL.slopeL, 0.45 * smooth(0.55, 0.75, rill));
          }
          col = lerp3(col, PAL.slopeD, 0.35 * (1 - smooth(0, 6, qq)));
          if (geo.bermH) col = lerp3(col, PAL.slopeL, 0.4 * smooth(rise - 3, rise, qm));
        } else {
          col = lerp3(lerp3(PAL.grass, PAL.grass2, smooth(0.35, 0.65, n2)), PAL.grass3, smooth(0.5, 0.8, n1));
          col = lerp3(col, PAL.dry, 0.5 * smooth(0.62, 0.8, n3));
        }
      } else if (op) {
        // top: a broad, light, compacted surface with driving tracks, and one working area round the cell
        // being filled, with darker loose material and the grey tipping piles
        col = lerp3(PAL.plat, PAL.plat2, smooth(0.38, 0.62, 0.5 * n1 + 0.5 * n2));   // older and newer fill
        col = lerp3(col, PAL.loose, 0.35 * smooth(0.55, 0.75, n3));
        const u = bl(m.u), v = bl(m.v), w = geo.cu != null ? workAt(u, v, geo.cu, geo.cv) : -99;
        const trw = trackAt(u, v) * (1 - smooth(-6, 2, w));
        col = lerp3(col, PAL.track, 0.75 * trw);
        const ww = smooth(-4, 4, w);
        if (w > -4) {
          col = lerp3(col, lerp3(PAL.loose, PAL.loose2, smooth(0.3, 0.7, n3)), ww * (0.55 + 0.45 * smooth(0.4, 0.6, n2)));
          if (m.pile) col = lerp3(col, PAL.pile, smooth(0.2, 1.4, bl(m.pile)));
        }
        if (DET.top) {
          // two reads of the top tile, the second turned and scaled, so its 160-m repeat is not seen
          const tl = DET.top, x = gx0 + gi * HR, y = gy0 + gj * HR, sx = tl.w / tl.mw;
          let f = 0.6 * detAt(tl, x * sx, y * sx) + 0.4 * detAt(tl, (0.79 * x - 0.61 * y) * sx * 0.73, (0.61 * x + 0.79 * y) * sx * 0.73);
          // dark side clamped (the tile's damp patches would dominate), weaker on the tracks so they stay light
          f = Math.max(0.82, lift(f, DET_K.top * (1 - 0.7 * trw)));
          // the loose tile only where no tipping piles stand (its heaps would make a second, unrelated grid)
          const wl = ww * (m.pile ? 1 - smooth(0.05, 0.6, bl(m.pile)) : 1);
          if (DET.loose && wl > 0) f = f * (1 - wl) + wl * lift(detAt(DET.loose, (u * DET.loose.w) / DET.loose.mw, (v * DET.loose.h) / DET.loose.mh), DET_K.loose);
          col = [col[0] * f, col[1] * f, col[2] * f];
        }
      } else {   // finished: grass, mown patches of slightly different tone
        const s = hash2(Math.floor(S.uu[o] / 90), Math.floor(S.vv[o] / 70), 9);
        col = lerp3(lerp3(PAL.grass, PAL.grass3, s), PAL.grass2, smooth(0.4, 0.7, n2) * 0.6);
        col = lerp3(col, PAL.dry, 0.4 * smooth(0.6, 0.8, n3));
      }
      if (onBerm > 0.02 && onBerm <= 0.98) col = lerp3(col, PAL.track, onBerm);
      const b = 0.95 + 0.1 * n1 + (hash2(c, r, 23) - 0.5) * 0.06;
      d[o4] = col[0] * b; d[o4 + 1] = col[1] * b; d[o4 + 2] = col[2] * b; d[o4 + 3] = 255;
    }
  }
  matCtx.putImageData(matImg, 0, 0);
  matTex.needsUpdate = true;
}
const heap = new THREE.Mesh(heapGeo, photoLight(footClip(new THREE.MeshLambertMaterial({
  side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4, map: matTex,
}), true), 0.9));
heap.renderOrder = 3;
world.add(heap);

// contour lines (switch, off by default: they make the body read like a drawing): band texture on uv1
const band = Object.assign(document.createElement("canvas"), { width: 4, height: 64 });
const bctx = band.getContext("2d");
bctx.fillStyle = "rgba(40,34,28,0.55)"; bctx.fillRect(0, 0, 4, 4);
const bandTex = new THREE.CanvasTexture(band);
bandTex.colorSpace = THREE.SRGBColorSpace;
bandTex.wrapT = THREE.RepeatWrapping;
bandTex.channel = 1;
const heapLines = new THREE.Mesh(heapGeo, footClip(new THREE.MeshBasicMaterial({
  map: bandTex, transparent: true, depthWrite: false, side: THREE.DoubleSide,
  polygonOffset: true, polygonOffsetFactor: -6, polygonOffsetUnits: -6,
})));
heapLines.renderOrder = 4;
world.add(heapLines);
const heapInfo = {};
// start values from the bundle (sites/<id>_szenario.json); the distance to the outline is chosen so that
// the footprint matches the stated area, because the source gives an area but no shape
const SC = scene.scenario || {};
if (SC.height_m != null) $("heapH").value = SC.height_m;
if (SC.slope_n != null) $("heapS").value = SC.slope_n;
if (SC.footprint_ha) {
  let bestB = 0, bestErr = Infinity;
  for (let b = 0; b <= +$("heapB").max; b++) {
    let cells = 0;
    for (let i = 0; i < inner.length; i++) if (inner[i] >= b) cells++;
    const err = Math.abs((cells * T.res * T.res) / 1e4 - SC.footprint_ha);
    if (err < bestErr) { bestErr = err; bestB = b; }
  }
  $("heapB").value = bestB;
}
// Berms (steps in the slope): no source for spacing and width was found (search 15.11.2026: the GDA
// recommendations of the DGGT are named for the stability proofs, but no figure for berms), so both are
// sliders labelled as free assumptions.
$("heapNote").insertAdjacentHTML("beforeend", [SC.footprint_source ? ` Der Abstand zur roten Linie ist so voreingestellt, dass die Grundfläche der Quelle entspricht (${SC.footprint_source}); die Lage dieser Fläche im Suchbereich ist eine Konstruktion.` : "", SC.note ? " " + SC.note : "",
  " Bermen: Abstand und Breite sind frei gewählte Annahmen, nicht verifiziert (eine Quelle für übliche Maße wurde nicht gefunden); „Böschung 1 : n“ ist die Neigung zwischen zwei Bermen."].join(""));
// Height of the body at `p` metres inside its foot line: rises with 1:slopeN; with berms every bermH metres
// of height a flat step of bermW metres follows, so "Böschung 1:n" is the slope between two berms and
// the body overall is flatter. Capped at H. The foot line does not depend on the berms.
function heapProfile(p, H, slopeN, bermH = 0, bermW = 0) {
  if (p <= 0) return 0;
  if (!bermH) return Math.min(H, p / slopeN);
  const per = bermH * slopeN + bermW, k = Math.floor(p / per), rem = p - k * per;
  return Math.min(H, k * bermH + Math.min(bermH, rem / slopeN));
}
// ---------- forms ----------
// "einfach": the profile of the visibility and wind calculations (heapProfile without berms, top following
// the ground). "realistisch" (start form): built like the operating mineral landfills on the reference
// images (docs/RENDERING.md), with its highest point held at that of the simple form, so it never
// shows less height than the reports: foot line straightened, a ring road
// at the foot, berms with tracks, a top in fixed height falling 6 % to its edge (DepV Anhang 1, Tabelle 2:
// drainage layer "Gefälle > 5 %"), rounded edges, tipping piles where the fill goes on. All other figures
// are assumptions.
const FORM = {
  CROWN: 0.06, ROUND: 6, ROUND_TOE: 4, ROAD_W: 8, SIMPLIFY_M: 30, MARGIN_M: 10,
  CELL_U: 75, CELL_V: 55, WORK_R: 55, PILE_DU: 12, PILE_DV: 5.5, PILE_H: 2.5, PILE_R: 4.5,
};
const FORM_NOTES = {
  realistisch: `Form nach dem Vorbild laufender Deponien für mineralische Abfälle in Baden-Württemberg (Luftbilder): Fußlinie begradigt, Ringweg am Fuß (${FORM.ROAD_W} m), Bermen mit Fahrweg, oben eine Fläche in fester Höhe, die zum Rand mit ${FORM.CROWN * 100} % fällt (die Deponieverordnung verlangt für die Entwässerungsschicht der Abdeckung mehr als 5 % Gefälle), Kanten gerundet. Der höchste Punkt liegt so hoch wie bei der einfachen Form, mit der die Sicht- und Windberechnungen rechnen; das Volumen ist dadurch kleiner (siehe unten). Lage der Abschnitte, Wege und Maße ohne Quelle sind Annahmen, keine Planung für G2.`,
  einfach: "Einfache Form (Rechenform): Böschung vom Fuß bis zur gewählten Höhe, oben folgt der Körper dem Gelände, ohne Bermen. So rechnen die Sicht- und Windberechnungen.",
};
const SURF_NOTES = {
  betrieb: "Oberfläche im Betrieb, ohne Bewuchs: oben eine helle, verdichtete Einbaufläche mit Fahrspuren und ein Arbeitsbereich mit lockerem Material und frisch abgekippten Haufen in Reihen; die Böschungen dunkler, mit Rinnen in Gefällerichtung; helle Fahrwege auf den Bermen und am Fuß. Die Fahrzeuge (Lkw, Raupe, Bagger, Walze) zeigen nur die Größenverhältnisse; Zahl und Lage sind angenommen. Die Feinstruktur (Fahrspuren, Haufen, Rinnen) stammt aus KI-erzeugten Bildern (siehe Quellen), kein Foto; übernommen ist nur hell und dunkel, die Farben bleiben die gemessenen. Helligkeit gemessen an Luftbildern laufender Deponien für mineralische Abfälle in Baden-Württemberg (DOP20, LGL), Farbton und Aufbau angelehnt an Fotos laufender Deponien (Herkunft der Fotos nicht geprüft); Anordnung angenommen. Bei den Vorbildern ist ein Teil der Böschungen schon begrünt; das ist hier nicht gezeichnet.",
  rekultiviert: "Oberfläche nach Abschluss: begrünt, Wege auf den Bermen und am Fuß bleiben. Wald (wie bei manchen Deponien vorgesehen) ist nicht gezeichnet.",
  neutral: "Neutrale Oberfläche: nur die Form, ohne Material.",
};
let formAux = null;
// straightened outline (Douglas-Peucker on the red line), signed distance to it, and position along and
// across the main axis of the site; computed on first use
function formHelpers() {
  if (formAux) return formAux;
  const ring = siteRings[0];
  const dp = (pts, tol) => {
    if (pts.length < 3) return pts;
    const [ax, ay] = pts[0], [bx, by] = pts[pts.length - 1], dx = bx - ax, dy = by - ay, l = Math.hypot(dx, dy) || 1;
    let imax = 0, dmax = 0;
    for (let i = 1; i < pts.length - 1; i++) { const dd = Math.abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / l; if (dd > dmax) { dmax = dd; imax = i; } }
    if (dmax <= tol) return [pts[0], pts[pts.length - 1]];
    return dp(pts.slice(0, imax + 1), tol).slice(0, -1).concat(dp(pts.slice(imax), tol));
  };
  const half = Math.floor(ring.length / 2);   // split the closed ring in two, so the first and last point differ
  const S = dp(ring.slice(0, half + 1), FORM.SIMPLIFY_M).slice(0, -1).concat(dp(ring.slice(half), FORM.SIMPLIFY_M));
  const dS = new Float32Array(fN), u = new Float32Array(fN), v = new Float32Array(fN);
  let mx = 0, my = 0, sxx = 0, syy = 0, sxy = 0, cnt = 0;
  const dC = new Float32Array(hc * hr);   // on the 5-m cells, interpolated like fDist (9 times fewer points)
  for (let r = 0; r < hr; r++) for (let c = 0; c < hc; c++) {
    const x = T.x0 + (c0 + c + 0.5) * T.res, y = T.y0 + (r0 + r + 0.5) * T.res, dd = distToRings(x, y, [S]);
    dC[r * hc + c] = inRings(x, y, [S]) ? dd : -dd;
  }
  for (let j = 0; j < frN; j++) for (let i = 0; i < fcN; i++) {
    const cc = Math.min(Math.floor(i / HEAP_SUB), hc - 2), rr = Math.min(Math.floor(j / HEAP_SUB), hr - 2);
    const tx = i / HEAP_SUB - cc, ty = j / HEAP_SUB - rr, k = j * fcN + i;
    dS[k] = (dC[rr * hc + cc] * (1 - tx) + dC[rr * hc + cc + 1] * tx) * (1 - ty) + (dC[(rr + 1) * hc + cc] * (1 - tx) + dC[(rr + 1) * hc + cc + 1] * tx) * ty;
    if (fDist[k] > 0) { mx += heapPos[k * 3]; my -= heapPos[k * 3 + 2]; cnt++; }
  }
  mx /= cnt; my /= cnt;
  for (let k = 0; k < fN; k++) if (fDist[k] > 0) {
    const dx = heapPos[k * 3] - mx, dy = -heapPos[k * 3 + 2] - my;
    sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
  }
  const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy), ax = Math.cos(ang), ay = Math.sin(ang);
  for (let k = 0; k < fN; k++) {
    const dx = heapPos[k * 3] - mx, dy = -heapPos[k * 3 + 2] - my;
    u[k] = dx * ax + dy * ay + 1e4; v[k] = -dx * ay + dy * ax + 1e4;   // offset: keeps the cell indices positive
  }
  return (formAux = { S, dS, u, v, mx, my, ax, ay, setback: null, pR: new Float32Array(fN) });
}
// smooth minimum: like Math.min, but rounded over a band of r metres where a and b meet
const smin = (a, b, r) => { const h = Math.max(r - Math.abs(a - b), 0) / r; return Math.min(a, b) - (h * h * r) / 4; };
// grid point nearest to a position along / across the main axis (-1 outside the grid)
function kAtUV(u, v) {
  const F = formHelpers(), du = u - 1e4, dv = v - 1e4;
  const i = Math.round((F.mx + du * F.ax - dv * F.ay - gx0) / HR), j = Math.round((F.my + du * F.ay + dv * F.ax - gy0) / HR);
  return i < 0 || j < 0 || i >= fcN || j >= frN ? -1 : j * fcN + i;
}
// working area on the top: an irregular patch round the cell being filled, longer along the main axis;
// > 0 inside (roughly metres)
function workAt(u, v, cu, cv) {
  return FORM.WORK_R * (0.5 + 1.0 * wnoise(matNoise[0], u, v, 600)) - Math.hypot((u - cu) / 1.8, v - cv);
}
// realistic form: heights above ground into hOut; returns the fields the material map needs
function formBody(H, slopeN, setback, bermH, bermW, surf, hOut) {
  const F = formHelpers(), RT = FORM.ROUND_TOE;
  const toe = (q) => (q < RT ? (q * q) / (2 * RT) : q - RT / 2);
  // foot line: the straightened outline moved in so far that the footprint is as large as that of the
  // simple form, but never closer than MARGIN_M to the red line
  if (F.setback !== setback) {
    let target = 0;
    for (let k = 0; k < fN; k++) if (fDist[k] - setback > 0) target++;
    let lo = -50, hi = 400;
    for (let it = 0; it < 24; it++) {
      const mid = (lo + hi) / 2;
      let n = 0;
      for (let k = 0; k < fN; k++) if (Math.min(F.dS[k] - mid, fDist[k] - FORM.MARGIN_M) > 0) n++;
      if (n > target) lo = mid; else hi = mid;
    }
    for (let k = 0; k < fN; k++) F.pR[k] = Math.min(F.dS[k] - lo, fDist[k] - FORM.MARGIN_M);
    F.setback = setback;
  }
  const pR = F.pR, inside = [];
  let pmax = 0, top0 = -Infinity, gmax = -Infinity;
  for (let k = 0; k < fN; k++) {
    if (pR[k] > 0) { inside.push(k); pmax = Math.max(pmax, pR[k]); gmax = Math.max(gmax, fGround[k]); }
    const p0 = fDist[k] - setback;
    if (p0 > 0) top0 = Math.max(top0, fGround[k] + heapProfile(p0, H, slopeN));   // highest point of the simple form
  }
  const prof = new Float32Array(fN), tz = new Float32Array(fN), td = new Float32Array(fN);
  for (const k of inside) {
    const q = pR[k] - FORM.ROAD_W;
    prof[k] = q > 0 ? heapProfile(toe(q), 1e6, slopeN, bermH, bermW) : 0;
    tz[k] = -FORM.CROWN * (pmax - pR[k]);
  }
  const body = (k, Zc) => Math.max(0, smin(fGround[k] + prof[k], Zc + tz[k], FORM.ROUND) - fGround[k]);
  const target = top0 - (surf === "betrieb" ? FORM.PILE_H : 0);   // the piles reach the simple form's top
  let lo = gmax - 200, hi = top0 + 50;
  for (let it = 0; it < 20; it++) {
    const mid = (lo + hi) / 2;
    let zt = -Infinity;
    for (const k of inside) { const z = fGround[k] + body(k, mid); if (z > zt) zt = z; }
    if (zt > target) hi = mid; else lo = mid;
  }
  hOut.fill(0);
  for (const k of inside) { hOut[k] = body(k, lo); td[k] = prof[k] - hOut[k]; }
  // the cell being filled: on the top, towards one end of the main axis (85th percentile)
  const tops = inside.filter((k) => td[k] > 1);
  let activeU = -1, activeV = -1;
  if (tops.length) {
    const us = tops.map((k) => F.u[k]).sort((a, b) => a - b), vs = tops.map((k) => F.v[k]).sort((a, b) => a - b);
    activeU = Math.floor(us[Math.floor(us.length * 0.85)] / FORM.CELL_U);
    activeV = Math.floor(vs[Math.floor(vs.length * 0.5)] / FORM.CELL_V);
  }
  const cu = (activeU + 0.5) * FORM.CELL_U, cv = (activeV + 1) * FORM.CELL_V, pile = new Float32Array(fN);
  if (surf === "betrieb" && tops.length) {
    // tipping piles in rows, as the trucks drop them one beside the other, so they merge into broken
    // ridges; on one side of the working area only, the other side is
    // already spread flat. Together no higher than PILE_H, so the top stays where it was set.
    const R = Math.ceil(FORM.PILE_R / HR), RU = FORM.WORK_R * 1.8;
    for (let a = 0, uu = cu - RU; uu < cu + 0.15 * RU; uu += FORM.PILE_DU, a++) {
      for (let b = 0, vv = cv - FORM.WORK_R; vv < cv + FORM.WORK_R; vv += FORM.PILE_DV, b++) {
        if (hash2(a, Math.floor(b / 4), 21) < 0.3 || hash2(a, b, 22) < 0.1) continue;   // rows broken into runs
        const pu = uu + 3 * (hash2(a, b, 23) - 0.5) + 4 * Math.sin(b * 0.4 + a), pv = vv + 2 * (hash2(a, b, 29) - 0.5);
        if (workAt(pu, pv, cu, cv) < FORM.PILE_R) continue;
        const k = kAtUV(pu, pv);
        if (k < 0 || td[k] < 3) continue;
        const i0 = k % fcN, j0 = Math.floor(k / fcN), hp = FORM.PILE_H * (0.55 + 0.45 * hash2(a, b, 31));
        for (let j = Math.max(0, j0 - R); j <= Math.min(frN - 1, j0 + R); j++) for (let i = Math.max(0, i0 - R); i <= Math.min(fcN - 1, i0 + R); i++) {
          const qq = Math.hypot(i - i0, j - j0) * HR / FORM.PILE_R;
          if (qq < 1) pile[j * fcN + i] = Math.max(pile[j * fcN + i], hp * (1 - qq * qq) * (1 - qq * qq));
        }
      }
    }
    for (const k of inside) hOut[k] += pile[k];
  }
  let nBerm = 0;   // index of the uppermost terrace that reaches the slope (the one still being built)
  if (bermH) { const per = bermH * slopeN + bermW; for (const k of inside) if (td[k] <= 0.6 && pR[k] > FORM.ROAD_W) nBerm = Math.max(nBerm, Math.floor((pR[k] - FORM.ROAD_W) / per)); }
  return { p: pR, td, u: F.u, v: F.v, pile, road: FORM.ROAD_W, activeU, activeV, cu, cv, nBerm };
}
// Vehicles on the operating surface, only for a sense of size (as on photos of operating landfills): boxes,
// placed by assumption on the top and on the closed ring road; nothing leaves the foot of the body, so no
// access point is implied. Parts: [along, across, length, height, width, base above ground, colour, kind];
// kind picks a column of the small texture atlas below (0 cab, 1 dump body, 2 machine body, 3 tracks and
// chassis, 4 steel), whose colours are multiplied by the part's colour.
const MACH = {
  truck: [[3.7, 0, 2.2, 3.1, 2.6, 0.2, 0xe6e3dc, 0], [-1.0, 0, 6.6, 2.4, 2.8, 1.0, 0xc9a23a, 1], [-1.0, 0, 7.4, 1.1, 2.6, 0, 0xb0aca4, 3]],
  dozer: [[0, 0, 4.4, 1.6, 3.4, 0.5, 0xd4a020, 2], [-0.6, 0, 1.9, 1.5, 1.9, 2.1, 0xd4a020, 0], [0, 0, 4.6, 0.9, 3.6, 0, 0xb0aca4, 3], [2.7, 0, 0.4, 1.5, 4.0, 0, 0x9a968e, 4]],
  excavator: [[0, 0, 4.8, 1.0, 3.3, 0, 0xb0aca4, 3], [-0.4, 0, 3.6, 1.9, 2.9, 1.0, 0xd4a020, 2], [1.0, -0.8, 1.2, 1.4, 1.0, 2.9, 0xd4a020, 0],
    [3.0, 0.6, 4.2, 0.7, 0.7, 2.3, 0xd4a020, 2]],
  roller: [[-0.6, 0, 3.8, 2.0, 2.3, 0.4, 0xd4a020, 2], [-1.0, 0, 1.4, 1.2, 1.4, 2.4, 0xd4a020, 0], [2.2, 0, 1.6, 1.6, 2.3, 0, 0xb5b1a8, 4]],
};
// Atlas: 5 columns of 64 px, side faces in the lower half, top faces in the upper half; drawn here, no image
const machAtlas = (() => {
  const C = 64, cv = Object.assign(document.createElement("canvas"), { width: 5 * C, height: 2 * C }), g = cv.getContext("2d");
  let s = 7;
  const rnd = () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
  const grey = (v) => `rgb(${v},${v},${v})`;
  const speckle = (x0, y0, w, h, n, lo, hi) => { for (let i = 0; i < n; i++) { g.fillStyle = grey(lo + Math.floor(rnd() * (hi - lo))); g.fillRect(x0 + rnd() * w, y0 + rnd() * h, 1 + rnd() * 2, 1 + rnd() * 2); } };
  const grime = (x0, h) => { const gr = g.createLinearGradient(0, 2 * C, 0, 2 * C - h); gr.addColorStop(0, "rgba(70,60,50,0.75)"); gr.addColorStop(1, "rgba(70,60,50,0)"); g.fillStyle = gr; g.fillRect(x0, 2 * C - h, C, h); };
  const side = C;   // canvas row of the side faces (top faces: row 0)
  // 0 cab: window band with frame, darker sill, grime below; roof light
  g.fillStyle = grey(238); g.fillRect(0, side, C, C);
  g.fillStyle = "rgb(44,50,58)"; g.fillRect(5, side + 8, C - 10, 22);
  g.fillStyle = "rgb(90,104,118)"; g.fillRect(8, side + 10, C - 16, 5);
  g.fillStyle = grey(200); g.fillRect(0, side + 34, C, 3);
  grime(0, 18);
  g.fillStyle = grey(225); g.fillRect(0, 0, C, C); speckle(0, 0, C, C, 60, 190, 240);
  // 1 dump body: vertical ribs, light rim on top, grime; seen from above: a load of soil and rubble
  g.fillStyle = grey(232); g.fillRect(C, side, C, C);
  for (let x = 4; x < C; x += 10) { g.fillStyle = grey(176); g.fillRect(C + x, side + 6, 3, C - 6); }
  g.fillStyle = grey(250); g.fillRect(C, side, C, 5);
  grime(C, 26);
  g.fillStyle = "rgb(150,132,112)"; g.fillRect(C, 0, C, C); speckle(C, 0, C, C, 400, 90, 200);
  g.fillStyle = grey(230); g.fillRect(C, 0, C, 4); g.fillRect(C, C - 4, C, 4); g.fillRect(C, 0, 4, C); g.fillRect(2 * C - 4, 0, 4, C);
  // 2 machine body: grille lines and a dark panel joint, grime below; vents on top
  g.fillStyle = grey(236); g.fillRect(2 * C, side, C, C);
  for (let y = 12; y < 34; y += 5) { g.fillStyle = grey(120); g.fillRect(2 * C + 40, side + y, 18, 2); }
  g.fillStyle = grey(150); g.fillRect(2 * C + 34, side + 6, 2, 40);
  grime(2 * C, 30);
  g.fillStyle = grey(228); g.fillRect(2 * C, 0, C, C);
  for (let y = 20; y < 44; y += 6) { g.fillStyle = grey(110); g.fillRect(2 * C + 10, y, 44, 2); }
  // 3 tracks / chassis: dark, tread blocks along the lower half, wheel hubs; top plain dark
  g.fillStyle = grey(96); g.fillRect(3 * C, side, C, C);
  for (let x = 0; x < C; x += 6) { g.fillStyle = grey(64); g.fillRect(3 * C + x, side + 30, 3, 34); }
  for (const x of [12, 32, 52]) { g.fillStyle = grey(40); g.beginPath(); g.arc(3 * C + x, side + 44, 9, 0, 7); g.fill(); g.fillStyle = grey(140); g.beginPath(); g.arc(3 * C + x, side + 44, 3, 0, 7); g.fill(); }
  g.fillStyle = grey(88); g.fillRect(3 * C, 0, C, C); speckle(3 * C, 0, C, C, 80, 60, 120);
  // 4 steel: worn edge, scratches, dirt
  g.fillStyle = grey(205); g.fillRect(4 * C, side, C, C);
  for (let i = 0; i < 30; i++) { g.fillStyle = grey(150 + Math.floor(rnd() * 90)); g.fillRect(4 * C + rnd() * C, side + rnd() * C, 1, 4 + rnd() * 14); }
  grime(4 * C, 20);
  g.fillStyle = grey(196); g.fillRect(4 * C, 0, C, C); speckle(4 * C, 0, C, C, 80, 150, 230);
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
})();
const machGeo = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
{   // per vertex: 1 on the top face (+y, vertices 8-11 of BoxGeometry), else 0
  const top = new Float32Array(machGeo.attributes.position.count);
  for (let i = 8; i < 12; i++) top[i] = 1;
  machGeo.setAttribute("aTop", new THREE.BufferAttribute(top, 1));
}
const machKind = new THREE.InstancedBufferAttribute(new Float32Array(64), 1);
machGeo.setAttribute("aKind", machKind);
const machMat = new THREE.MeshLambertMaterial({ color: 0xffffff, map: machAtlas });
machMat.onBeforeCompile = (sh) => {
  sh.vertexShader = "attribute float aTop;\nattribute float aKind;\n" + sh.vertexShader.replace("#include <uv_vertex>",
    "#include <uv_vertex>\n  vMapUv = vec2((aKind + uv.x) / 5.0, (aTop + uv.y) / 2.0);");
};
machMat.customProgramCacheKey = () => "machAtlas";
const machines = new THREE.InstancedMesh(machGeo, photoLight(machMat, 0.9, "mach"), 64);
machines.count = 0;
machines.visible = false;
world.add(machines);
function placeMachines(mat, on) {
  machines.count = 0;
  machines.visible = on;
  if (!on) return;
  const F = formHelpers(), dummy = new THREE.Object3D(), colr = new THREE.Color();
  const put = (kind, k, ang) => {   // ang: heading in the map (radians, from east)
    if (k < 0) return;
    const x = heapPos[k * 3], y = -heapPos[k * 3 + 2], z = heapPos[k * 3 + 1], ca = Math.cos(ang), sa = Math.sin(ang);
    for (const [al, ac, L, Hh, W, base, hex, part] of MACH[kind]) {
      if (machines.count >= machines.instanceMatrix.count) return;
      dummy.position.set(x + al * ca - ac * sa, z + base, -(y + al * sa + ac * ca));
      dummy.rotation.set(0, ang, 0);
      dummy.scale.set(L, Hh, W);
      dummy.updateMatrix();
      machines.setMatrixAt(machines.count, dummy.matrix);
      machines.setColorAt(machines.count, colr.setHex(hex));
      machKind.array[machines.count] = part;
      machines.count++;
    }
  };
  const axis = Math.atan2(F.ay, F.ax), RU = FORM.WORK_R * 1.8, at = (du, dv) => kAtUV(mat.cu + du, mat.cv + dv);
  const onTop = (k) => (k >= 0 && mat.td[k] > 3 ? k : -1);
  // in the working area: an excavator at the pile front, a dozer and a roller on the spread side,
  // two trucks tipping and one on its way across the top
  put("excavator", onTop(at(0.2 * RU, -0.3 * FORM.WORK_R)), axis + 2.6);
  put("dozer", onTop(at(0.4 * RU, 0.15 * FORM.WORK_R)), axis + 0.2);
  put("roller", onTop(at(0.6 * RU, -0.45 * FORM.WORK_R)), axis + Math.PI / 2);
  put("truck", onTop(at(-0.15 * RU, 0.5 * FORM.WORK_R)), axis + Math.PI - 0.3);
  put("truck", onTop(at(0.1 * RU, 0.75 * FORM.WORK_R)), axis + Math.PI + 0.4);
  put("truck", onTop(at(-1.9 * RU, 0.2 * FORM.WORK_R)), axis + 0.1);
  // two trucks on the ring road, heading along it
  const ring = [];
  for (let k = 0; k < fN; k++) if (Math.abs(mat.p[k] - FORM.ROAD_W / 2) < 0.6) ring.push(k);
  ring.sort((a, b) => F.u[a] - F.u[b]);
  for (const f of [0.28, 0.73]) {
    const k = ring[Math.floor(ring.length * f)];
    if (k == null) continue;
    const i = k % fcN, j = Math.floor(k / fcN), P = mat.p;
    const gx = P[j * fcN + Math.min(i + 1, fcN - 1)] - P[j * fcN + Math.max(i - 1, 0)], gy = P[Math.min(j + 1, frN - 1) * fcN + i] - P[Math.max(j - 1, 0) * fcN + i];
    put("truck", k, Math.atan2(gy, gx) + Math.PI / 2);
  }
  machines.instanceMatrix.needsUpdate = true;
  machKind.needsUpdate = true;
  if (machines.instanceColor) machines.instanceColor.needsUpdate = true;
  machines.computeBoundingSphere();
}
// ---------- the same map, drawn on the graphics card ----------
// bakeMaterial above, line for line in a fragment shader that renders into a texture once per change of
// a slider (not per frame): about 3-7 ms instead of 70-140 ms on the processor (RTX 5090, docs/RENDERING.md).
// The fields per grid point go up as float textures and are interpolated by hand (as bl() does), the noise
// tables and detail tiles as textures read by texel. The processor version stays as the reference
// (?bake=cpu, viewer.bakeCompare) and takes over if the shader does not compile on a device.
const BAKE_GPU = !/[?&]bake=cpu\b/.test(location.search);
const BAKE_FS = `precision highp float; precision highp int; precision highp sampler2D;
uniform sampler2D fA, fB, nz, dTop, dLoose, dSlope;
uniform int fcN, frN, matH, MATPX, op, hasCu, hasPile, hasAL, hasTop, hasLoose, hex;
uniform float HR, gx0, gy0, road, bermH, bermW, slopeN, cu, cv, slopeW, alL, workR;
uniform vec4 tTop, tLoose, tSlope;   // w, h, metres wide, metres high
uniform vec3 DK;                      // strength of the top, loose and slope tile
uniform vec3 P[14];                   // PAL in the order of PAL_KEYS
out vec4 outColor;
#define TRACK P[0]
#define DITCH P[1]
#define PLAT P[2]
#define PLAT2 P[3]
#define SLOPE P[4]
#define SLOPEL P[5]
#define SLOPED P[6]
#define LOOSE P[7]
#define LOOSE2 P[8]
#define PILE P[9]
#define GRASS P[10]
#define GRASS2 P[11]
#define GRASS3 P[12]
#define DRY P[13]
float sm(float lo, float hi, float v) { float t = clamp((v - lo) / (hi - lo), 0.0, 1.0); return t * t * (3.0 - 2.0 * t); }
float lift(float f, float k) { return 1.0 + k * (f - 1.0); }
float hash2(int i, int j, int s) {
  uint h = (uint(i) * 374761393u) ^ (uint(j) * 668265263u) ^ (uint(s) * 2246822519u);
  h = (h ^ (h >> 13u)) * 1274126177u;
  return float(h ^ (h >> 16u)) / 4294967296.0;
}
// wnoise(): tileable value noise, period per metres, channel ch of the noise texture
float wn(int ch, float x, float y, float per) {
  float u = fract(x / per) * 256.0, v = fract(y / per) * 256.0;
  int i0 = int(floor(u)), j0 = int(floor(v));
  float tu = u - float(i0), tv = v - float(j0);
  int i1 = (i0 + 1) & 255, j1 = (j0 + 1) & 255; i0 &= 255; j0 &= 255;
  float a = texelFetch(nz, ivec2(i0, j0), 0)[ch], b = texelFetch(nz, ivec2(i1, j0), 0)[ch];
  float c = texelFetch(nz, ivec2(i0, j1), 0)[ch], d = texelFetch(nz, ivec2(i1, j1), 0)[ch];
  return mix(mix(a, b, tu), mix(c, d, tu), tv);
}
// detAt(): factor at (x, y) in tile pixels, bilinear, wrapped across x and, where wy, across y
float det(sampler2D s, vec4 t, bool wy, float x, float y) {
  int w = int(t.x), h = int(t.y);
  x -= t.x * floor(x / t.x);
  y = wy ? y - t.y * floor(y / t.y) : clamp(y, 0.0, t.y - 1.0);
  int i0 = min(int(floor(x)), w - 1), j0 = min(int(floor(y)), h - 1);
  float tx = x - float(i0), ty = y - float(j0);
  int i1 = (i0 + 1) % w, j1 = wy ? (j0 + 1) % h : min(j0 + 1, h - 1);
  float a = texelFetch(s, ivec2(i0, j0), 0).r, b = texelFetch(s, ivec2(i1, j0), 0).r;
  float c = texelFetch(s, ivec2(i0, j1), 0).r, d = texelFetch(s, ivec2(i1, j1), 0).r;
  return mix(mix(a, b, tx), mix(c, d, tx), ty) * (255.0 / 128.0);
}
// hex-tiling after Mikkelsen 2022 (JCGT 11(3)): three reads of the tile at the corners of the triangle
// grid cell containing the point, each shifted and turned by a hash of its corner, so no repeat shows.
// Blend: soft (exponent 3), the contrast lost by averaging restored by dividing the deviation from 1 by the
// root of the summed squared weights (variance kept, mean 1 kept). The paper's sharp blend (exponent 7,
// weighted to contrast) looks practically the same here; either is fine.
vec2 hexRot(vec2 p, float a) { float c = cos(a), s = sin(a); return vec2(c * p.x - s * p.y, s * p.x + c * p.y); }
float detHex(sampler2D s, vec4 t, float x, float y) {
  vec2 p = vec2(x, y) / t.x * 3.4641;                         // hexagons of about 0.5 tile
  vec2 st = vec2(p.x - p.y * 0.57735, p.y * 1.1547);           // skewed to a triangle grid
  vec2 b = floor(st), f = fract(st);
  vec3 w; ivec2 v1, v2, v3;
  if (f.x + f.y < 1.0) { w = vec3(1.0 - f.x - f.y, f.x, f.y); v1 = ivec2(b); v2 = ivec2(b) + ivec2(1, 0); v3 = ivec2(b) + ivec2(0, 1); }
  else { w = vec3(f.x + f.y - 1.0, 1.0 - f.x, 1.0 - f.y); v1 = ivec2(b) + ivec2(1, 1); v2 = ivec2(b) + ivec2(0, 1); v3 = ivec2(b) + ivec2(1, 0); }
  float r[3]; ivec2 vs[3] = ivec2[3](v1, v2, v3);
  for (int i = 0; i < 3; i++) {
    vec2 o = vec2(hash2(vs[i].x, vs[i].y, 61), hash2(vs[i].x, vs[i].y, 62)) * t.x;
    vec2 q = hexRot(vec2(x, y), 6.2832 * hash2(vs[i].x, vs[i].y, 63)) + o;
    r[i] = det(s, t, true, q.x, q.y);
  }
  vec3 ww = pow(w, vec3(3.0));
  ww /= dot(ww, vec3(1.0));
  return 1.0 + (dot(ww, vec3(r[0], r[1], r[2])) - 1.0) / sqrt(dot(ww, ww));
}
vec3 lin(vec3 c) { c /= 255.0; return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }
float trackAt(float u, float v) {
  float cvv = hasCu == 1 ? cv : 1e4;
  float along = abs(v - (cvv + 28.0 * sin(u / 170.0) + 12.0 * sin(u / 61.0 + 1.0)));
  int ic = int(floor((u - 1e4) / 150.0 + 0.5));
  float uc = 1e4 + float(ic) * 150.0 + 35.0 * sin(v / 130.0 + float(ic));
  float cross = hash2(ic, 0, 41) < 0.7 ? abs(u - uc) : 99.0;
  return 1.0 - sm(2.4, 3.8, min(along, cross));
}
void main() {
  int c = int(gl_FragCoord.x), yr = int(gl_FragCoord.y), r = matH - 1 - yr;   // r: canvas row (0 = north)
  float gi = float(c) / float(MATPX), gj = float(yr) / float(MATPX);
  int i0 = min(int(floor(gi)), fcN - 2), j0 = min(int(floor(gj)), frN - 2);
  float ti = gi - float(i0), tj = gj - float(j0);
  vec4 a00 = texelFetch(fA, ivec2(i0, j0), 0), a10 = texelFetch(fA, ivec2(i0 + 1, j0), 0);
  vec4 a01 = texelFetch(fA, ivec2(i0, j0 + 1), 0), a11 = texelFetch(fA, ivec2(i0 + 1, j0 + 1), 0);
  vec4 A = mix(mix(a00, a10, ti), mix(a01, a11, ti), tj);            // p, td, u, v
  float p = A.x;
  if (p < -2.0) { outColor = vec4(lin(vec3(128.0)), 1.0); return; }
  vec4 b00 = texelFetch(fB, ivec2(i0, j0), 0), b10 = texelFetch(fB, ivec2(i0 + 1, j0), 0);
  vec4 b01 = texelFetch(fB, ivec2(i0, j0 + 1), 0), b11 = texelFetch(fB, ivec2(i0 + 1, j0 + 1), 0);
  vec4 B = mix(mix(b00, b10, ti), mix(b01, b11, ti), tj);            // pile, along, foot x, foot y
  float x = gx0 + gi * HR, y = gy0 + gj * HR;
  float n1 = wn(0, x, y, 420.0), n2 = wn(1, x, y, 130.0), n3 = wn(2, x, y, 45.0);
  float q = p - road;
  bool onTop = A.y > 0.6, berms = bermH > 0.0;
  float per = berms ? bermH * slopeN + bermW : 1e9, rise = bermH * slopeN;
  float qm = berms ? mod(q, per) : 0.0, px = HR / float(MATPX);
  float onBerm = (!onTop && berms && q > 0.0) ? sm(rise - px / 2.0, rise + px / 2.0, qm) * (1.0 - sm(per - 1.3 - px / 2.0, per - 1.3 + px / 2.0, qm) * 0.5) : 0.0;
  vec3 col;
  if (q < px / 2.0) {
    col = mix(TRACK, DITCH, max(sm(-1.2 - px / 2.0, -1.2 + px / 2.0, q), 1.0 - sm(1.2 - px / 2.0, 1.2 + px / 2.0, p)));
  } else if (onBerm > 0.98) {
    col = per - qm < 1.3 ? DITCH : TRACK;
  } else if (!onTop) {
    if (op == 1) {
      float qq = berms ? qm : q;
      col = mix(SLOPE, SLOPEL, 0.4 * n2);
      if (hasAL == 1) {
        float a0 = b00.y, L = alL;
        vec4 al4 = vec4(b00.y, b10.y, b01.y, b11.y);
        al4 = al4 - L * step(L / 2.0, al4 - a0) + L * step(L / 2.0, a0 - al4);   // un(): across the seam
        float al = mix(mix(al4.x, al4.y, ti), mix(al4.z, al4.w, ti), tj);
        float fv = berms ? qm / rise : mod(q, 30.0) / 30.0;
        float sh = hash2(int(floor(berms ? q / per : q / 30.0)), 0, 51) * tSlope.x;
        float yy = (1.0 - fv) * (tSlope.y - 1.0), xa = (al / slopeW) * tSlope.x + sh;
        float f0 = 0.55 * det(dSlope, tSlope, false, xa, yy) + 0.45 * det(dSlope, tSlope, false, xa * 0.61 + 17.0, yy);
        float f = lift(f0, DK.z * (0.35 + 0.9 * sm(0.35, 0.65, wn(0, al, sh * 7.0, 1600.0))));
        col *= f;
      } else {
        float fx = B.z, fy = B.w;
        float rill = 0.5 + 1.5 * (0.55 * wn(2, fx, fy, 24.0) + 0.45 * wn(1, fx + 91.0, fy - 37.0, 60.0) - 0.5);
        col = mix(col, SLOPED, 0.75 * sm(0.52, 0.28, rill));
        col = mix(col, SLOPEL, 0.45 * sm(0.55, 0.75, rill));
      }
      col = mix(col, SLOPED, 0.35 * (1.0 - sm(0.0, 6.0, qq)));
      if (berms) col = mix(col, SLOPEL, 0.4 * sm(rise - 3.0, rise, qm));
    } else {
      col = mix(mix(GRASS, GRASS2, sm(0.35, 0.65, n2)), GRASS3, sm(0.5, 0.8, n1));
      col = mix(col, DRY, 0.5 * sm(0.62, 0.8, n3));
    }
  } else if (op == 1) {
    col = mix(PLAT, PLAT2, sm(0.38, 0.62, 0.5 * n1 + 0.5 * n2));
    col = mix(col, LOOSE, 0.35 * sm(0.55, 0.75, n3));
    float u = A.z, v = A.w;
    float w = hasCu == 1 ? workR * (0.5 + 1.0 * wn(0, u, v, 600.0)) - length(vec2((u - cu) / 1.8, v - cv)) : -99.0;
    float trw = trackAt(u, v) * (1.0 - sm(-6.0, 2.0, w));
    col = mix(col, TRACK, 0.75 * trw);
    float ww = sm(-4.0, 4.0, w);
    if (w > -4.0) {
      col = mix(col, mix(LOOSE, LOOSE2, sm(0.3, 0.7, n3)), ww * (0.55 + 0.45 * sm(0.4, 0.6, n2)));
      if (hasPile == 1) col = mix(col, PILE, sm(0.2, 1.4, B.x));
    }
    if (hasTop == 1) {
      float sx = tTop.x / tTop.z, f;
      if (hex == 1) f = detHex(dTop, tTop, x * sx, y * sx);
      else f = 0.6 * det(dTop, tTop, true, x * sx, y * sx) + 0.4 * det(dTop, tTop, true, (0.79 * x - 0.61 * y) * sx * 0.73, (0.61 * x + 0.79 * y) * sx * 0.73);
      f = max(0.82, lift(f, DK.x * (1.0 - 0.7 * trw)));
      float wl = ww * (hasPile == 1 ? 1.0 - sm(0.05, 0.6, B.x) : 1.0);
      if (hasLoose == 1 && wl > 0.0) f = f * (1.0 - wl) + wl * lift(det(dLoose, tLoose, true, (u * tLoose.x) / tLoose.z, (v * tLoose.y) / tLoose.w), DK.y);
      col *= f;
    }
  } else {
    float uu = A.z + 28.0 * (wn(1, x + 517.0, y, 260.0) - 0.5), vv = A.w + 28.0 * (wn(2, x, y + 733.0, 260.0) - 0.5);
    float s = hash2(int(floor(uu / 90.0)), int(floor(vv / 70.0)), 9);
    col = mix(mix(GRASS, GRASS3, s), GRASS2, sm(0.4, 0.7, n2) * 0.6);
    col = mix(col, DRY, 0.4 * sm(0.6, 0.8, n3));
  }
  if (onBerm > 0.02 && onBerm <= 0.98) col = mix(col, TRACK, onBerm);
  float bb = 0.95 + 0.1 * n1 + (hash2(c, r, 23) - 0.5) * 0.06;
  outColor = vec4(lin(clamp(floor(col * bb + 0.5), 0.0, 255.0)), 1.0);
}`;
const PAL_KEYS = ["track", "ditch", "plat", "plat2", "slope", "slopeL", "slopeD", "loose", "loose2", "pile", "grass", "grass2", "grass3", "dry"];
const gpuBake = (() => {
  if (!BAKE_GPU) return null;
  const fA = new Float32Array(fN * 4), fB = new Float32Array(fN * 4);
  const tex = (a, w, h, fmt, type, align = 4) => {
    const t = new THREE.DataTexture(a, w, h, fmt, type);
    t.unpackAlignment = align;
    t.needsUpdate = true;
    return t;
  };
  const tA = tex(fA, fcN, frN, THREE.RGBAFormat, THREE.FloatType), tB = tex(fB, fcN, frN, THREE.RGBAFormat, THREE.FloatType);
  const nzA = new Float32Array(256 * 256 * 4);
  for (let i = 0; i < 256 * 256; i++) for (let c = 0; c < 3; c++) nzA[i * 4 + c] = matNoise[c][i];
  const none = tex(new Uint8Array([128]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType, 1);
  const dt = (t) => (t ? tex(t.a, t.w, t.h, THREE.RedFormat, THREE.UnsignedByteType, 1) : none);
  const dv = (t) => new THREE.Vector4(t ? t.w : 1, t ? t.h : 1, t ? t.mw : 1, t ? t.mh : 1);
  const U = {
    fA: { value: tA }, fB: { value: tB }, nz: { value: tex(nzA, 256, 256, THREE.RGBAFormat, THREE.FloatType) },
    dTop: { value: dt(DET.top) }, dLoose: { value: dt(DET.loose) }, dSlope: { value: dt(DET.slope) },
    tTop: { value: dv(DET.top) }, tLoose: { value: dv(DET.loose) }, tSlope: { value: dv(DET.slope) },
    DK: { value: new THREE.Vector3(DET_K.top, DET_K.loose, DET_K.slope) },
    P: { value: PAL_KEYS.map((k) => new THREE.Vector3(...PAL[k])) },
    fcN: { value: fcN }, frN: { value: frN }, matH: { value: matH }, MATPX: { value: MAT_PX },
    HR: { value: HR }, gx0: { value: gx0 }, gy0: { value: gy0 }, workR: { value: FORM.WORK_R },
    hasTop: { value: DET.top ? 1 : 0 }, hasLoose: { value: DET.loose ? 1 : 0 }, hex: { value: 1 },
  };
  for (const k of ["op", "hasCu", "hasPile", "hasAL", "road", "bermH", "bermW", "slopeN", "cu", "cv", "slopeW", "alL"]) U[k] = { value: 0 };
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
  const quad = new THREE.Mesh(geo, new THREE.RawShaderMaterial({
    glslVersion: THREE.GLSL3, uniforms: U, depthTest: false, depthWrite: false,
    vertexShader: "in vec3 position;\nvoid main() { gl_Position = vec4(position.xy, 0.0, 1.0); }",
    fragmentShader: BAKE_FS,
  }));
  quad.frustumCulled = false;
  const bScene = new THREE.Scene(), bCam = new THREE.Camera();
  bScene.add(quad);
  const rt = new THREE.WebGLRenderTarget(matW, matH, { depthBuffer: false, generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter });
  const t = rt.texture;
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = matTex.anisotropy;
  t.repeat.copy(matTex.repeat);
  t.offset.copy(matTex.offset);
  function bake(kind, m, geo) {
    const op = kind === "betrieb", AL = op && DET.slope ? alongOutline() : null, P = m.p;
    for (let j = 0; j < frN; j++) for (let i = 0; i < fcN; i++) {
      const k = j * fcN + i, o = k * 4;
      fA[o] = P[k]; fA[o + 1] = m.td[k]; fA[o + 2] = m.u[k]; fA[o + 3] = m.v[k];
      // foot point (as in bakeMaterial), only needed without the generated slope tile
      let fx = 0, fy = 0;
      if (!AL) {
        const gx = P[j * fcN + Math.min(i + 1, fcN - 1)] - P[j * fcN + Math.max(i - 1, 0)];
        const gy = P[Math.min(j + 1, frN - 1) * fcN + i] - P[Math.max(j - 1, 0) * fcN + i];
        const l = Math.hypot(gx, gy) || 1;
        fx = gx0 + i * HR - (P[k] * gx) / l; fy = gy0 + j * HR - (P[k] * gy) / l;
      }
      fB[o] = m.pile ? m.pile[k] : 0; fB[o + 1] = AL ? AL.A[k] : 0; fB[o + 2] = fx; fB[o + 3] = fy;
    }
    tA.needsUpdate = tB.needsUpdate = true;
    const set = { op: op ? 1 : 0, hasCu: geo.cu != null ? 1 : 0, hasPile: m.pile ? 1 : 0, hasAL: AL ? 1 : 0, road: geo.road, bermH: geo.bermH,
      bermW: geo.bermW, slopeN: geo.slopeN, cu: geo.cu ?? 0, cv: geo.cv ?? 0, alL: AL ? AL.L : 1,
      slopeW: AL ? AL.L / Math.max(1, Math.round(AL.L / DET.slope.mw)) : 1 };
    for (const [k, v] of Object.entries(set)) U[k].value = v;
    const prev = renderer.getRenderTarget();
    renderer.setRenderTarget(rt);
    renderer.render(bScene, bCam);
    renderer.setRenderTarget(prev);
    // false if the shader did not compile or link on this device (three keeps the diagnostics)
    return renderer.properties.get(quad.material).currentProgram?.diagnostics?.runnable !== false;
  }
  return { bake, rt, U, fs: BAKE_FS, failed: false };
})();
// the map the heap shows: the rendered one, else (?bake=cpu, or the shader failed) the canvas
let heapMap = gpuBake ? gpuBake.rt.texture : matTex;
// for checks: both bakes of the current state, compared per pixel (bytes, sRGB)
function bakeCompare(hex = 0) {
  if (!gpuBake || gpuBake.failed || !lastBake) return null;
  const last = lastBake;
  gpuBake.U.hex.value = hex;
  gpuBake.bake(...last);
  bakeMaterial(...last);
  gpuBake.U.hex.value = 1;
  const g = new Uint8Array(matW * matH * 4);
  renderer.readRenderTargetPixels(gpuBake.rt, 0, 0, matW, matH, g);
  const d = matImg.data;
  let max = 0, sum = 0, n = 0, over2 = 0;
  const mg = [0, 0, 0], mc = [0, 0, 0];
  for (let yr = 0; yr < matH; yr++) for (let c = 0; c < matW; c++) {
    const oc = ((matH - 1 - yr) * matW + c) * 4, og = (yr * matW + c) * 4;
    let dm = 0;
    for (let ch = 0; ch < 3; ch++) { const e = Math.abs(g[og + ch] - d[oc + ch]); dm = Math.max(dm, e); mg[ch] += g[og + ch]; mc[ch] += d[oc + ch]; }
    max = Math.max(max, dm); sum += dm; n++; if (dm > 2) over2++;
  }
  gpuBake.bake(...last);
  return { max, mean: +(sum / n).toFixed(3), over2, share_over2: +(over2 / n).toFixed(5), meanGPU: mg.map((v) => +(v / n).toFixed(2)), meanCPU: mc.map((v) => +(v / n).toFixed(2)) };
}
let lastBake = null;
function bakeHeap(kind, m, geo) {
  lastBake = [kind, m, geo];
  if (gpuBake && !gpuBake.failed) {
    if (gpuBake.bake(kind, m, geo)) return;
    gpuBake.failed = true;
    heapMap = matTex;
    console.warn("heap map: shader failed, baking on the processor");
  }
  bakeMaterial(kind, m, geo);
}
// a lost WebGL context empties the rendered map (the canvas survives): draw it again once restored
if (gpuBake) renderer.domElement.addEventListener("webglcontextrestored", () => buildHeap());
let formH = null;
function buildHeap() {
  const H = +$("heapH").value, slopeN = +$("heapS").value, setback = +$("heapB").value;
  const form = $("heapForm").value, surf = $("heapSurf").value;
  const bermH = form === "realistisch" ? +$("bermH").value : 0, bermW = bermH ? +$("bermW").value : 0;
  let mat = null;
  const tB0 = performance.now();
  if (form === "realistisch") mat = formBody(H, slopeN, setback, bermH, bermW, surf, (formH ||= new Float32Array(fN)));
  $("formNote").textContent = FORM_NOTES[form];
  $("surfNote").textContent = SURF_NOTES[surf] + (SC.surface_note ? " " + SC.surface_note + (SC.surface_sources ? " Quellen: " + SC.surface_sources.join("; ") + "." : "") : "");
  const A = heapGeo.attributes, cellA = HR * HR;
  let cells = 0, vol = 0, vol0 = 0, top = -Infinity, hmax = 0;
  const pS = new Float32Array(fN), tdS = new Float32Array(fN);
  for (let k = 0; k < fN; k++) {
    const p0 = fDist[k] - setback, h0 = heapProfile(p0, H, slopeN), p = mat ? mat.p[k] : p0;
    const h = mat ? formH[k] : h0, g = fGround[k];
    if (h0 > 0) vol0 += h0;
    A.uv1.array[k * 2 + 1] = (h - 0.3) / 5;   // line just above each 5 m, so a flat top at 30 m is not tinted
    A.foot.array[k] = p;
    heapPos[k * 3 + 1] = g + h;
    if (p >= 0) cells++;
    if (h > 0) { vol += h; hmax = Math.max(hmax, h); top = Math.max(top, g + h); }
    if (!mat) { pS[k] = p0; tdS[k] = p0 / slopeN - h0; }
  }
  const tB1 = performance.now();
  if (surf === "neutral") {
    if (heap.material.map) { heap.material.map = null; heap.material.needsUpdate = true; }
    heap.material.color.set(0xa89c8e);
  } else {
    bakeHeap(surf, mat || { p: pS, td: tdS, u: formHelpers().u, v: formHelpers().v }, mat
      ? { road: mat.road, bermH, bermW, slopeN, nBerm: mat.nBerm, cu: mat.cu, cv: mat.cv }
      : { road: 0, bermH: 0, bermW: 0, slopeN, nBerm: 0 });
    if (heap.material.map !== heapMap) { heap.material.map = heapMap; heap.material.needsUpdate = true; }
    heap.material.color.set(0xffffff);
  }
  const tB2 = performance.now();
  const ft = A.foot.array;
  if (!heapGeo.index) heapGeo.setIndex(new THREE.BufferAttribute(new Uint32Array((fcN - 1) * (frN - 1) * 6), 1));
  const tri = heapGeo.index.array;
  let t = 0;
  for (let j = 0; j < frN - 1; j++) {
    for (let i = 0; i < fcN - 1; i++) {
      const a = j * fcN + i, b = a + 1, d = a + fcN, e = d + 1;
      if (ft[a] >= 0 || ft[b] >= 0 || ft[d] >= 0) { tri[t++] = a; tri[t++] = b; tri[t++] = d; }
      if (ft[b] >= 0 || ft[e] >= 0 || ft[d] >= 0) { tri[t++] = b; tri[t++] = e; tri[t++] = d; }
    }
  }
  tri.fill(0, t);   // degenerate rest: computeVertexNormals reads the whole index, not the draw range
  heapGeo.setDrawRange(0, t);
  heapGeo.index.needsUpdate = true;
  heapGeo.attributes.foot.needsUpdate = true;
  heapGeo.attributes.position.needsUpdate = true;
  heapGeo.attributes.uv1.needsUpdate = true;
  heapGeo.computeVertexNormals();
  heapGeo.computeBoundingSphere();
  heapInfo.ms = { form: Math.round(tB1 - tB0), bake: Math.round(tB2 - tB1), mesh: Math.round(performance.now() - tB2) };
  Object.assign(heapInfo, { H, slopeN, setback, bermH, bermW, form, area_ha: (cells * cellA) / 1e4, volume_m3: vol * cellA, volume_simple_m3: vol0 * cellA, top_m: hmax ? top + T.z_min : null, hmax });
  heap.visible = $("heapOn").checked && hmax > 0;
  heapLines.visible = heap.visible && $("linesOn").checked;
  placeMachines(mat, heap.visible && !!mat && surf === "betrieb");
  applyTerrain();
  $("hOut").textContent = `${fmt(H)} m`;
  $("sOut").textContent = `1 : ${fmt(slopeN, 1)}`;
  $("bOut").textContent = `${fmt(setback)} m`;
  $("bermRow").hidden = form !== "realistisch";
  $("bermOut").textContent = bermH ? `alle ${fmt(bermH)} m Höhe (Annahme)` : "keine";
  $("bermWOut").textContent = `${fmt(+$("bermW").value)} m (Annahme)`;
  $("bermWRow").hidden = !bermH;
  $("heapStats").innerHTML =
    `<dt>Grundfläche des Körpers</dt><dd>${fmt(heapInfo.area_ha, 1)} ha</dd>` +
    (SC.footprint_ha ? `<dt>Aufstandsfläche laut Quelle</dt><dd>rund ${fmt(SC.footprint_ha)} ha</dd>` : "") +
    `<dt>Fläche Suchbereich (rote Linie)</dt><dd>${fmt(scene.site.area_ha, 1)} ha</dd>` +
    `<dt>Volumen (aus diesem Modell)</dt><dd>${fmt(heapInfo.volume_m3 / 1e6, 2)} Mio. m³</dd>` +
    (mat ? `<dt>Volumen der Rechenform</dt><dd>${fmt(heapInfo.volume_simple_m3 / 1e6, 2)} Mio. m³</dd>` : "") +
    `<dt>Erreichte Höhe über Gelände</dt><dd>${fmt(hmax, 1)} m</dd>` +
    `<dt>Höchster Punkt</dt><dd>${hmax ? fmt(heapInfo.top_m, 0) + " m ü. NHN" : "–"}</dd>`;
}

// Trees and buildings: a separate mesh, only where something stands (surface more than 2 m above
// the ground). The terrain mesh with the aerial image and all layers always stays on the bare
// ground, so water courses, biotopes and the red line lie where they are, not on tree tops.
// Each tall cell is drawn as a block: a flat top over the whole 5-m cell at the highest surface
// value, and walls down to the lower neighbour (or the ground). A grid of points instead would turn
// a narrow tall object (a 100-m chimney) into a needle 10 m wide at the base, with the aerial image
// smeared down its sides. Walls take the colour of their own cell's top, never a stretched image.
// Under the heap nothing is drawn, because whatever stands there today would be gone.
const TALL_CM = 200;
let canopy = null, terrainKey = "";
if (sm) {
  canopy = new THREE.Mesh(new THREE.BufferGeometry(), photoLight(withDetail(new THREE.MeshLambertMaterial({ map: orthoTex }))));
  canopy.visible = false;
  world.add(canopy);
}
function buildBlocks(gone, skip) {
  const W = T.ncols, H = T.nrows, h2 = T.res / 2;
  const tall = (i) => sm[i] - cm[i] > TALL_CM && !gone[i] && !(skip && skip[i] === 1);
  const top = (i) => sm[i] / 100;
  // wall bottom towards neighbour j: its top if it is a block, else the lower of the two ground heights
  const low = (i, j) => (j < 0 ? cm[i] / 100 : tall(j) ? top(j) : Math.min(cm[i], cm[j]) / 100);
  const nbs = (i, r, c) => [c + 1 < W ? i + 1 : -1, c > 0 ? i - 1 : -1, r + 1 < H ? i + W : -1, r > 0 ? i - W : -1];
  let quads = 0;
  for (let r = 0; r < H; r++) for (let c = 0; c < W; c++) {
    const i = r * W + c;
    if (!tall(i)) continue;
    quads++;
    for (const j of nbs(i, r, c)) if (low(i, j) < top(i)) quads++;
  }
  const P = new Float32Array(quads * 12), N = new Float32Array(quads * 12), U = new Float32Array(quads * 8);
  const I = new Uint32Array(quads * 6);
  let q = 0;
  const quad = (pts, nx, ny, nz, uvs) => {
    for (let k = 0; k < 4; k++) {
      P.set(pts[k], (q * 4 + k) * 3); N.set([nx, ny, nz], (q * 4 + k) * 3); U.set(uvs[k], (q * 4 + k) * 2);
    }
    I.set([q * 4, q * 4 + 1, q * 4 + 2, q * 4, q * 4 + 2, q * 4 + 3], q * 6);
    q++;
  };
  const du = h2 / (bx1 - bx0), dv = h2 / (by1 - by0);
  for (let r = 0; r < H; r++) for (let c = 0; c < W; c++) {
    const i = r * W + c;
    if (!tall(i)) continue;
    const x = pos[i * 3], z = pos[i * 3 + 2], y = top(i), u = uv[i * 2], v = uv[i * 2 + 1];
    const xa = x - h2, xb = x + h2, za = z + h2, zb = z - h2;   // za south edge, zb north edge (z = -y)
    quad([[xa, y, za], [xb, y, za], [xb, y, zb], [xa, y, zb]], 0, 1, 0,
      [[u - du, v - dv], [u + du, v - dv], [u + du, v + dv], [u - du, v + dv]]);
    const [e, w, nn, s] = nbs(i, r, c), flat = [[u, v], [u, v], [u, v], [u, v]];
    let b;
    if ((b = low(i, e)) < y) quad([[xb, b, za], [xb, b, zb], [xb, y, zb], [xb, y, za]], 1, 0, 0, flat);
    if ((b = low(i, w)) < y) quad([[xa, b, zb], [xa, b, za], [xa, y, za], [xa, y, zb]], -1, 0, 0, flat);
    if ((b = low(i, nn)) < y) quad([[xb, b, zb], [xa, b, zb], [xa, y, zb], [xb, y, zb]], 0, 0, -1, flat);
    if ((b = low(i, s)) < y) quad([[xa, b, za], [xb, b, za], [xb, y, za], [xa, y, za]], 0, 0, 1, flat);
  }
  const g = canopy.geometry;
  g.setAttribute("position", new THREE.BufferAttribute(P, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(N, 3));
  g.setAttribute("uv", new THREE.BufferAttribute(U, 2));
  g.setIndex(new THREE.BufferAttribute(I, 1));
  g.computeBoundingSphere();
}
// Stand-in trees (scene.trees, geoanalysis_viewer/trees.py): one simple tree per measured tree top of the 1-m
// surface model, at its measured position and height; nothing added, moved or jittered. Crown width is a
// rough measure, crown depth and trunk are assumed; the crown takes the colour of the aerial image at
// that point. Cells classed as trees then lose their block; buildings stay blocks. Loaded only when the
// switch is first turned on, so the start is not slowed down.
const TR = scene.trees;
let treeSet = null, treeLoading = false;
async function loadTrees() {
  const buf = await (await fetch(`${base}/${TR.file}`)).arrayBuffer(), N = TR.count;
  const tx = new Int16Array(buf, 0, N), ty = new Int16Array(buf, 2 * N, N);
  const th = new Uint8Array(buf, 4 * N, N), trad = new Uint8Array(buf, 5 * N, N);
  const cls = new Uint8Array(await (await fetch(`${base}/${TR.class_file}`)).arrayBuffer());
  // crown colour: mean of 3 x 3 pixels of the aerial image at the top
  const W = orthoCanvas.width, Hh = orthoCanvas.height, px = octx.getImageData(0, 0, W, Hh).data;
  const col = new Float32Array(N * 3), c3 = new THREE.Color();
  for (let i = 0; i < N; i++) {
    const u = Math.round(((tx[i] / 10 - bx0) / (bx1 - bx0)) * W), v = Math.round(((by1 - ty[i] / 10) / (by1 - by0)) * Hh);
    let r = 0, g = 0, b = 0, m = 0;
    for (let dv = -1; dv <= 1; dv++) for (let du = -1; du <= 1; du++) {
      const uu = Math.min(W - 1, Math.max(0, u + du)), vv = Math.min(Hh - 1, Math.max(0, v + dv)), o = (vv * W + uu) * 4;
      r += px[o]; g += px[o + 1]; b += px[o + 2]; m++;
    }
    c3.setRGB(r / m / 255, g / m / 255, b / m / 255, THREE.SRGBColorSpace);
    col.set([c3.r, c3.g, c3.b], i * 3);
  }
  // low polygon counts: 22,000 trees, a crown is a few pixels in the overview (36 triangles, smooth normals;
  // trunk an open three-sided prism, 6 triangles); before 100 and 20 (docs/RENDERING.md)
  const crownGeo = new THREE.SphereGeometry(1, 6, 4), trunkGeo = new THREE.CylinderGeometry(1, 1, 1, 3, 1, true);
  trunkGeo.translate(0, 0.5, 0);
  const crowns = new THREE.InstancedMesh(crownGeo, photoLight(new THREE.MeshLambertMaterial({ color: 0xffffff }), 0.6, "crown"), N);
  const trunks = new THREE.InstancedMesh(trunkGeo, new THREE.MeshLambertMaterial({ color: 0x5b4a3a }), N);
  crowns.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(N * 3), 3);
  world.add(crowns, trunks);
  treeSet = { N, tx, ty, th, trad, col, cls, crowns, trunks };
  marks.trees = Math.round(performance.now());
}
function placeTrees(on, gone) {
  if (!treeSet) return;
  const { N, tx, ty, th, trad, col, crowns, trunks } = treeSet, m = new THREE.Matrix4(), q = new THREE.Quaternion();
  const p = new THREE.Vector3(), sc = new THREE.Vector3(), ic = crowns.instanceColor.array;
  let k = 0;
  if (on) for (let i = 0; i < N; i++) {
    const x = tx[i] / 10, y = ty[i] / 10, h = th[i] / 4, r = trad[i] / 10;
    const c = Math.floor((x - T.x0) / T.res), rr = Math.floor((y - T.y0) / T.res);
    if (c >= 0 && rr >= 0 && c < T.ncols && rr < T.nrows && gone[rr * T.ncols + c]) continue;   // under the heap
    const g = terrainHeight(x, y), cd = Math.min(h - 1, Math.max(2 * r, 0.5 * h));   // crown depth: assumed
    crowns.setMatrixAt(k, m.compose(p.set(x, g + h - cd / 2, -y), q, sc.set(r, cd / 2, r)));
    ic[k * 3] = col[i * 3]; ic[k * 3 + 1] = col[i * 3 + 1]; ic[k * 3 + 2] = col[i * 3 + 2];
    const tr = 0.12 + 0.012 * h;
    trunks.setMatrixAt(k, m.compose(p.set(x, g, -y), q, sc.set(tr, h - 0.7 * cd, tr)));
    k++;
  }
  crowns.count = trunks.count = k;
  crowns.instanceMatrix.needsUpdate = trunks.instanceMatrix.needsUpdate = true;
  crowns.instanceColor.needsUpdate = true;
  crowns.computeBoundingSphere(); trunks.computeBoundingSphere();
  crowns.visible = trunks.visible = on && k > 0;
}
function applyTerrain() {
  const on = !!canopy && $("surfOn").checked;
  const treesOn = on && !!TR;   // trees at the measured tops always come with the surface (no separate switch)
  if (treesOn && !treeSet && !treeLoading) {
    treeLoading = true;
    loadTrees().then(() => { terrainKey = ""; applyTerrain(); }).catch((e) => console.warn("Bäume:", e));
  }
  const key = `${on}|${treesOn && !!treeSet}|${on && heap.visible ? heapInfo.setback : "-"}`;
  if (key === terrainKey) return;   // height and slope of the heap do not change the stands
  terrainKey = key;
  if (canopy) {
    canopy.visible = on;
    const gone = new Uint8Array(n);
    if (on && heap.visible) for (let r = 0; r < hr; r++) for (let c = 0; c < hc; c++) if (inner[r * hc + c] >= heapInfo.setback) gone[(r0 + r) * T.ncols + c0 + c] = 1;
    if (on) {
      const t0 = performance.now();
      buildBlocks(gone, treesOn && treeSet ? treeSet.cls : null);
      marks.blocks = Math.round(performance.now() - t0);
    }
    placeTrees(treesOn, gone);
  }
  setVpNote();
}
function setVpNote() {
  const on = !!canopy && $("surfOn").checked, treesOn = on && !!TR;
  const eye = `Augenhöhe ${fmt(eyeM(), 1)} m`;
  const flights = scene.surface ? scene.surface.flight.map(date).join(", ") : "";
  $("vpNote").textContent = !on
    ? eye + ". Das Geländemodell enthält keine Gebäude und keinen Bewuchs; die tatsächliche Sicht ist deshalb oft stärker verdeckt."
    : treesOn
      ? `${eye} über dem Gelände. Bäume als Stellvertreter: je gemessener Baumspitze (Oberflächenmodell 1 m, Befliegung ${flights}, im März, unbelaubt) ein Baum in gemessener Höhe; Kronenbreite grob gemessen, Kronenform und Stamm angenommen, Farbe aus dem Luftbild. In dichtem Wald sind es weniger Spitzen als Bäume. Die Kronen sind geschlossen gezeichnet, wie belaubt im Sommer; ihre Höhe stammt aber aus der Befliegung im März, unbelaubte Kronen sind im Winter teils durchsichtig. Gebäude, Hecken und alles ohne erkannte Spitze bleiben 5-m-Klötze.`
      : eye + " über dem Gelände. Bäume und Gebäude nach dem Oberflächenmodell (Befliegung " + flights + ", 5-m-Raster, je Zelle der höchste Wert): grob, Bäume und Gebäude erscheinen als 5-m-Klötze, schmale hohe Bauten wie ein Schornstein werden breiter dargestellt. Gehölz ist undurchsichtig gezeichnet, wie belaubt im Sommer; die Höhen stammen aus der Befliegung im März.";
}

// ---------- camera presets ----------
const siteC = { x: (sbox[0] + sbox[2]) / 2, y: (sbox[1] + sbox[3]) / 2 };
const siteSpan = Math.max(sbox[2] - sbox[0], sbox[3] - sbox[1]);
const groundY = (x, y) => terrainHeight(x, y) * world.scale.y;
function lookFrom(px, py, pz) {
  controls.target.set(siteC.x, groundY(siteC.x, siteC.y), -siteC.y);
  camera.position.set(px, py, pz);
  controls.update();
}
function eyeM() { return +$("eyeH").value; }
function showEyeUI(on) { for (const id of ["vpNote", "eyeRow", "eyeNote", "scaleRow", "scaleNote"]) $(id).hidden = !on; }
// True scale at eye level: the picture angle is chosen so that things appear as large on the screen as
// they would to the eye from the same spot, for a screen looked at from about 50 cm (phone: 30 cm). The
// browser does not know the physical size of a pixel; assumed: 0.26 mm per CSS pixel (the CSS reference,
// about right for desktop and laptop screens at their usual scaling), 0.17 mm on touch devices. The fixed
// 45° vertical angle of the overview shows things about half as large as that on a laptop (docs/RENDERING.md).
const FOV_OVERVIEW = 45;
function eyeFov(h) {
  const phone = matchMedia("(pointer: coarse)").matches, mm = phone ? 0.17 : 0.26, dist = phone ? 300 : 500;
  return Math.min(FOV_OVERVIEW, 2 * Math.atan((h * mm) / 2 / dist) * 180 / Math.PI);
}
function updateFov() {
  const h = Math.max(1, canvas.clientHeight), w = Math.max(1, canvas.clientWidth);
  camera.fov = walk.on && $("scaleOn").checked ? eyeFov(h) : FOV_OVERVIEW;
  camera.updateProjectionMatrix();
  ribbonPx.value = (camera.fov * Math.PI / 180) / h;
  const hz = 2 * Math.atan(Math.tan(camera.fov * Math.PI / 360) * w / h) * 180 / Math.PI;
  const phone = matchMedia("(pointer: coarse)").matches;
  $("scaleNote").textContent = `Bildwinkel jetzt ${fmt(hz)}° waagrecht, ${fmt(camera.fov)}° senkrecht. ` + ($("scaleOn").checked
    ? `Maßstabsgetreu für einen Blick auf den Bildschirm aus etwa ${phone ? "30" : "50"} cm (Annahme: ${phone ? "0,17" : "0,26"} mm je Bildpunkt der Seite bei Zoom 100 %; bei anderen Bildschirmen oder anderem Zoom weicht es ab). Wer weiter weg sitzt, sieht alles kleiner, als es in Wirklichkeit erscheint.`
    : `Weitwinkel wie in der Übersicht: Alles erscheint deutlich kleiner als mit eigenen Augen.`);
}
function setView(v) {
  showEyeUI(false);
  walk.on = false;
  updateFov();
  controls.enabled = true;
  const d = siteSpan * 1.5, up = groundY(siteC.x, siteC.y) + siteSpan * 0.55;
  if (v === "top") lookFrom(siteC.x, groundY(siteC.x, siteC.y) + siteSpan * 2.2, -siteC.y + 1); // north up
  if (v === "N") lookFrom(siteC.x, up, -siteC.y + d); // camera south of the site, looking north
  if (v === "S") lookFrom(siteC.x, up, -siteC.y - d);
  if (v === "E") lookFrom(siteC.x - d, up, -siteC.y);
  if (v === "W") lookFrom(siteC.x + d, up, -siteC.y);
}
// Eye level: the orbit controls are off and the camera walks on the ground at the chosen eye height
// (the orbit controls had lifted it: they keep the camera above the target, and the target, half
// the heap height over the site centre, lies above the eye: +21 m at G2). Drag looks around; the
// wheel, arrow keys or W/A/S/D walk (Shift: faster). The overview buttons switch back.
const walk = { on: false, yaw: 0, pitch: 0, keys: new Set(), drag: null, t: 0 };
const walkDir = () => {
  const cp = Math.cos(walk.pitch);
  return new THREE.Vector3(Math.sin(walk.yaw) * cp, Math.sin(walk.pitch), -Math.cos(walk.yaw) * cp);
};
function walkBy(fwd, side) {
  const s = Math.sin(walk.yaw), c = Math.cos(walk.yaw);
  camera.position.x += s * fwd + c * side;
  camera.position.z += -c * fwd + s * side;
}
function setViewpoint(v) {
  showEyeUI(true);
  // from the houses the land is shown bare first; the switch can turn trees and buildings back on
  if ($("surfOn").checked) { $("surfOn").checked = false; applyTerrain(); }
  walk.on = true;
  updateFov();
  controls.enabled = false;
  camera.position.set(v.x, groundY(v.x, v.y) + eyeM(), -v.y);
  // first look: towards the site centre at half the heap height
  const d = new THREE.Vector3(siteC.x, groundY(siteC.x, siteC.y) + (heap.visible ? heapInfo.hmax / 2 : 0) * world.scale.y, -siteC.y).sub(camera.position);
  walk.yaw = Math.atan2(d.x, -d.z);
  walk.pitch = Math.asin(d.y / d.length());
}
canvas.addEventListener("pointerdown", (ev) => {
  if (!walk.on || ev.button !== 0) return;
  walk.drag = { x: ev.clientX, y: ev.clientY };
  canvas.setPointerCapture(ev.pointerId);
});
canvas.addEventListener("pointermove", (ev) => {
  if (!walk.on || !walk.drag) return;
  // grab the view: the landscape follows the pointer
  walk.yaw -= (ev.clientX - walk.drag.x) * 0.004;
  walk.pitch = Math.min(1.4, Math.max(-1.4, walk.pitch + (ev.clientY - walk.drag.y) * 0.004));
  walk.drag = { x: ev.clientX, y: ev.clientY };
});
const endDrag = () => { walk.drag = null; };
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);
canvas.addEventListener("wheel", (ev) => {
  if (!walk.on) return;
  ev.preventDefault();
  walkBy(-Math.sign(ev.deltaY) * (ev.shiftKey ? 50 : 10), 0);
}, { passive: false });
const WALK_KEYS = { ArrowUp: [1, 0], KeyW: [1, 0], ArrowDown: [-1, 0], KeyS: [-1, 0], ArrowLeft: [0, -1], KeyA: [0, -1], ArrowRight: [0, 1], KeyD: [0, 1] };
const typing = (ev) => /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(ev.target.tagName);
window.addEventListener("keydown", (ev) => {
  if (!walk.on || !WALK_KEYS[ev.code] || typing(ev)) return;
  ev.preventDefault();
  walk.keys.add(ev.code);
  walk.fast = ev.shiftKey;
});
window.addEventListener("keyup", (ev) => { walk.keys.delete(ev.code); walk.fast = ev.shiftKey; });
window.addEventListener("blur", () => walk.keys.clear());
function walkFrame() {
  const now = performance.now(), dt = Math.min(0.1, (now - (walk.t || now)) / 1000);
  walk.t = now;
  let f = 0, s = 0;
  for (const k of walk.keys) { f += WALK_KEYS[k][0]; s += WALK_KEYS[k][1]; }
  if (f || s) walkBy(f * dt * (walk.fast ? 60 : 12), s * dt * (walk.fast ? 60 : 12));   // m/s
  camera.position.y = groundY(camera.position.x, -camera.position.z) + eyeM();
  camera.lookAt(camera.position.clone().add(walkDir()));
}
$("eyeH").addEventListener("input", () => {
  $("eyeOut").textContent = `${fmt(eyeM(), 1)} m`;
  setVpNote();
});

// ---------- UI ----------
const STATUS = {
  ueberlappt: ["hit", "überlappt den Suchbereich"],
  "beruehrt (im Toleranzbereich)": ["touch", `berührt den Suchbereich (innerhalb der Toleranz ±${fmt(scene.site.tolerance_m || 0, 1)} m)`],
};
function statusText(f) {
  if (f.status && STATUS[f.status]) {
    const extra = f.status === "ueberlappt" && f.overlap_m2 ? `, ${fmt(f.overlap_m2)} m²` : "";
    return `<span class="${STATUS[f.status][0]}">${STATUS[f.status][1]}${extra}</span>`;
  }
  return f.distance_m != null ? `Abstand zum Suchbereich ${fmt(f.distance_m, 0)} m` : "";
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
const date = (s) => (s ? new Date(s.replace(/([+-]\d\d)(\d\d)$/, "$1:$2")).toLocaleDateString("de-DE") : "ohne Datum");

$("title").textContent = scene.site.name;
$("subtitle").textContent = `Suchbereich ${scene.site.id} · ${fmt(scene.site.area_ha, 1)} ha · Gelände ${fmt(T.z_min)}–${fmt(T.z_max)} m ü. NHN im Ausschnitt`;
document.title = `${scene.site.id} in 3D: Gelände, Schutzgebiete, Schüttkörper-Szenario`;

const layersEl = $("layers");
let group = null;
// only layers with features in the map area are listed; the others are named once below the list
for (const L of [...scene.layers].filter((L) => L.features.length).sort((a, b) => a.group.localeCompare(b.group))) {
  if (L.group !== group) { group = L.group; layersEl.insertAdjacentHTML("beforeend", `<div class="grp">${esc(group)}</div>`); }
  const hits = L.features.filter((f) => f.status === "ueberlappt").length;
  const touch = L.features.filter((f) => f.status === "beruehrt (im Toleranzbereich)").length;
  const info = !L.features.length ? "kein Treffer im Ausschnitt"
    : `${L.features.length} im Ausschnitt` + (hits ? ` · <span class="hit">${hits} überlappt</span>` : "") + (touch ? ` · <span class="touch">${touch} berührt</span>` : "");
  const el = document.createElement("label");
  el.className = "lay" + (L.features.length ? "" : " empty");
  el.innerHTML = `<input type="checkbox" ${state.layers[L.id] ? "checked" : ""} ${L.features.length ? "" : "disabled"}>` +
    `<span class="sw" style="background:${L.color}"></span><span>${esc(L.label)}</span><small>${info}</small>`;
  el.querySelector("input").addEventListener("change", (e) => { state.layers[L.id] = e.target.checked; drawOverlay(); });
  layersEl.append(el);
}
const noHit = scene.layers.filter((L) => !L.features.length).map((L) => esc(L.label));
if (noHit.length) layersEl.insertAdjacentHTML("beforeend", `<p class="note">Geprüft, ohne Treffer im Ausschnitt: ${noHit.join(", ")}.</p>`);
$("siteOn").addEventListener("change", (e) => { state.site = e.target.checked; drawOverlay(); });
$("opacity").addEventListener("input", (e) => { ovU.ovOpacity.value = e.target.value / 100; $("opOut").textContent = `${e.target.value} %`; });
for (const id of ["heapH", "heapS", "heapB", "bermH", "bermW"]) $(id).addEventListener("input", buildHeap);
$("heapOn").addEventListener("change", buildHeap);
$("heapForm").addEventListener("change", buildHeap);
$("linesOn").addEventListener("change", () => { heapLines.visible = heap.visible && $("linesOn").checked; });
if (sm) { $("surfRow").hidden = false; $("surfOn").addEventListener("change", applyTerrain); }
if (ringMesh) {
  $("ringRow").hidden = false;
  $("ringKm").textContent = fmt((scene.ring.n - 1) * scene.ring.res / 2000);
  $("ringOn").addEventListener("change", (e) => showRing(e.target.checked));
}
$("heapSurf").addEventListener("change", buildHeap);
marks.ui = Math.round(performance.now());
marks.heapTexture = Math.round(performance.now());
$("exag").addEventListener("input", (e) => {
  const old = world.scale.y;
  world.scale.y = +e.target.value;
  $("exagOut").textContent = `${fmt(world.scale.y, 1)}×` + (world.scale.y > 1 ? " (überhöht)" : "");
  if (!walk.on) { camera.position.y *= world.scale.y / old; controls.target.y *= world.scale.y / old; controls.update(); }
});
$("views").addEventListener("click", (e) => e.target.dataset.view && setView(e.target.dataset.view));
for (const v of scene.viewpoints) {
  const b = document.createElement("button");
  b.textContent = `${v.label} (${fmt(v.distance_m)} m)`;
  b.addEventListener("click", () => setViewpoint(v));
  $("viewpoints").append(b);
}

const srcRows = [
  `Rote Linie: ${esc(scene.site.source)}; Lagegenauigkeit ±${fmt(scene.site.tolerance_m || 0, 1)} m (helles Band).`,
  `Gelände: DGM1, ${esc(T.attribution)}; abgerufen ${date(T.fetched_at)}; Höhengenauigkeit ${fmt(T.accuracy_m, 2)} m, hier auf ${fmt(T.res)} m gemittelt.`,
  scene.ring ? `Horizont außerhalb des Geländemodells (bis ${fmt((scene.ring.n - 1) * scene.ring.res / 2000)} km): ${esc(scene.ring.source)}, ${fmt(scene.ring.res)}-m-Raster; ${esc(scene.ring.attribution)}; abgerufen ${date(scene.ring.fetched_at)}.` +
    (scene.ring.image ? ` Luftbild dort: DOP20 grob, ${fmt(scene.ring.image.mpp)} m je Bildpunkt, ${esc(scene.ring.image.attribution)}; abgerufen ${date(scene.ring.image.fetched_at)}.` : "") : "",
  `Farben der Oberfläche des Schüttkörpers: Helligkeit gemessen an DOP20-Luftbildern (LGL, dl-de/by-2-0, abgerufen 19.11.2026) von drei laufenden Deponien für mineralische Abfälle in Baden-Württemberg; Farbton und Aufbau (Bermen, Rinnen, Arbeitsbereich) angelehnt an Fotos laufender Deponien, Herkunft der Fotos nicht geprüft; Anordnung, Anteile und Fahrzeuge angenommen.`,
  ...Object.entries(scene.heap_detail?.tiles || {}).map(([k, x]) => `Feinstruktur der Oberfläche des Schüttkörpers (${{ top: "Einbaufläche", loose: "Arbeitsbereich", slope: "Böschungen" }[k] || esc(k)}, nur hell/dunkel): ${esc(x.source)}; kein Foto, keine gemessene Oberfläche.`),
  ...Object.values(scene.textures || {}).map((x) => x.data
    ? `Relief der Oberfläche des Schüttkörpers (Licht und Schatten im Kleinen): ${esc(x.source)}; keine gemessene Oberfläche.`
    : `Oberfläche des Schüttkörpers: erzeugtes Bild, kein Foto (${esc(x.source)}).`),
  `Luftbild: DOP20, ${esc(O.attribution)}; abgerufen ${date(O.fetched_at)}; hier ${fmt(O.mpp, 1)} m je Bildpunkt` +
    (D ? `, im Nahbereich (${fmt((D.bbox[2] - D.bbox[0]) / 1000, 2)} × ${fmt((D.bbox[3] - D.bbox[1]) / 1000, 2)} km um G2) ${fmt(D.mpp, 1)} m je Bildpunkt, abgerufen ${date(D.fetched_at)}` : "") + ".",
  ...scene.layers.filter((L) => L.features.length).map((L) => `${esc(L.label)}: ${esc(L.attribution)}; ${esc(L.date_kind)} ${date(L.date)}.`),
  scene.analysis ? `Überlappungen und Abstände: Auswertung vom ${date(scene.analysis.generated)}, Umkreis ${fmt(scene.analysis.radius_m)} m. Werte innerhalb der Toleranz gelten als „berührt“.` : "",
  TR ? `Baum-Stellvertreter: ${fmt(TR.count)} Baumspitzen aus DOM1 minus DGM1 (1 m, LGL-BW), mindestens ${fmt(TR.min_height_m)} m hoch, nicht auf Gebäuden (ALKIS-Gebäude, ${esc(TR.buildings.attribution)}, abgerufen ${date(TR.buildings.fetched_at)}, um ${fmt(TR.building_pad_m)} m erweitert). Form von Krone und Stamm angenommen, Farbe aus dem Luftbild. Ersetzt werden nur 5-m-Zellen unter einer erkannten Krone (${fmt(TR.tree_cells_m2 / 1e4)} ha); die übrigen ${fmt((TR.tall_cells_m2 - TR.tree_cells_m2) / 1e4)} ha hoher Zellen bleiben Klötze (Gebäude, Hecken unter ${fmt(TR.min_height_m)} m, Masten, Wald ohne erkannte Spitze). Höchste Spitzen um 40 m (Wald im Südosten; nicht geprüft).` : "",
  scene.surface ? `Bäume und Gebäude: DOM1, ${esc(scene.surface.attribution)}; Befliegung ${scene.surface.flight.map(date).join(", ")}; je Zelle der höchste Wert. Vorhanden von E ${Math.round(scene.surface.extent[0] + scene.origin[0])} bis ${Math.round(scene.surface.extent[2] + scene.origin[0])} und N ${Math.round(scene.surface.extent[1] + scene.origin[1])} bis ${Math.round(scene.surface.extent[3] + scene.origin[1])} (UTM32); außerhalb zeigt die Ansicht nur das Gelände.` +
    (scene.surface.corrections || []).map((c) => ` Korrigiert: ${esc(c.name)} (Hinweis, nicht verifiziert). Im DOM1 als Fläche bis ${fmt(c.max_removed_m)} m über Gelände erfasst; diese ${fmt(c.cells_1m)} m² sind hier auf Geländehöhe gesetzt, niedrigere Gebäude darunter fehlen deshalb.` +
      (c.kept ? ` Erhalten bleibt eine kleine flache Fläche mit ${fmt(c.kept.top_m)} m über Gelände, wahrscheinlich die Schornsteinspitze (DOM1-Wert; im 5-m-Raster deutlich breiter dargestellt als in Wirklichkeit).` : "") +
      ` Begründung: ${c.evidence.map(esc).join(" ")} ${c.height_source ? esc(c.height_source) : ""}`).join("") : "Das Geländemodell zeigt den Boden ohne Gebäude und Bewuchs.",
];
$("sources").innerHTML = srcRows.filter(Boolean).map((s) => `<p>${s}</p>`).join("");

// ---------- picking ----------
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
function featuresAt(x, y) {
  const out = [];
  for (const L of scene.layers) {
    if (!state.layers[L.id]) continue;
    for (const f of L.features) {
      if (f.polygons && x >= f.box[0] && x <= f.box[2] && y >= f.box[1] && y <= f.box[3] && f.polygons.some((p) => inRings(x, y, p))) out.push([L, f]);
      else if (f.lines && f.lines.some((l) => distToRings(x, y, [l]) < 8)) out.push([L, f]);
      else if (f.points && f.points.some(([px, py]) => Math.hypot(px - x, py - y) < 16)) out.push([L, f]);
    }
  }
  return out;
}
function describe(ev) {
  const b = canvas.getBoundingClientRect();
  ndc.set(((ev.clientX - b.left) / b.width) * 2 - 1, -((ev.clientY - b.top) / b.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  const hit = ray.intersectObject(terrain, false)[0];
  if (!hit) return null;
  const x = hit.point.x, y = -hit.point.z, inside = inRings(x, y, siteRings);
  const feats = featuresAt(x, y);
  const head = `<div class="c">E ${Math.round(x + scene.origin[0])} · N ${Math.round(y + scene.origin[1])} · ${fmt(terrainHeight(x, y) + T.z_min, 1)} m ü. NHN${inside ? " · <b>im Suchbereich</b>" : ""}</div>`;
  const rows = feats.map(([L, f]) => `<div class="feat"><span class="sw" style="background:${L.color}"></span><div><b>${esc(L.label)}</b>${f.name ? ": " + esc(f.name) : ""}` +
    `${f.typ && f.typ !== f.name ? `<div class="c">${esc(f.typ)}</div>` : ""}<div>${statusText(f)}</div></div></div>`);
  return { html: head + rows.join(""), count: feats.length };
}
let pending = null, moved = false;
canvas.addEventListener("pointermove", (ev) => {
  moved = true;
  if (ev.buttons) { $("tip").hidden = true; return; }
  pending = ev;
});
canvas.addEventListener("pointerdown", () => { moved = false; });
canvas.addEventListener("pointerup", (ev) => {
  if (moved) return;
  const d = describe(ev);
  $("pick").innerHTML = d ? d.html + (d.count ? "" : "<p>Keine eingeblendete Fläche an diesem Punkt.</p>") : "Kein Geländepunkt getroffen.";
});
canvas.addEventListener("pointerleave", () => { pending = null; $("tip").hidden = true; });

// ---------- loop ----------
function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  updateFov();
}
window.addEventListener("resize", resize);
$("scaleOn").addEventListener("change", updateFov);
const dir = new THREE.Vector3();
renderer.setAnimationLoop(() => {
  if (walk.on) walkFrame();
  else {
    walk.t = 0;
    controls.update();
    // keep the camera above the ground
    const g = groundY(camera.position.x, -camera.position.z) + 1.5;
    if (camera.position.y < g) camera.position.y = g;
  }
  if (pending) {
    const d = describe(pending), tip = $("tip");
    if (d && d.count) {
      tip.innerHTML = d.html; tip.hidden = false;
      tip.style.left = `${Math.min(pending.clientX + 14, innerWidth - 310)}px`; tip.style.top = `${pending.clientY + 14}px`;
    } else tip.hidden = true;
    pending = null;
  }
  camera.getWorldDirection(dir);
  sky.position.copy(camera.position);
  three.fog.density = HAZE * (1 - 0.85 * smooth(0.35, 0.9, -dir.y));   // less haze looking steeply down
  $("needle").style.transform = `rotate(${Math.atan2(dir.x, -dir.z) * -57.2958}deg)`;
  renderer.render(three, camera);
  if (!marks.firstFrame) {
    marks.firstFrame = Math.round(performance.now());
    setTimeout(() => { nearOn.value = 1; drawOverlay(); marks.nearLayers = Math.round(performance.now()); }, 0);
    setTimeout(thinTerrain, 0);
    if (D) setTimeout(() => loadDetail().catch((e) => console.warn("Nahbereichs-Luftbild:", e)), 0);
    if (ringMesh) setTimeout(() => showRing($("ringOn").checked), 0);
  }
});

resize();
drawOverlay();
marks.heapStart = Math.round(performance.now());
buildHeap();
marks.heapBuilt = Math.round(performance.now());
setView("N");
$("loading").remove();

// hook for checks and screenshots
marks.ready = Math.round(performance.now());
window.viewer = { matCanvas, bakeCompare, gpuBake, machines, heapProfile, formHeights: () => formH, formHelpers, heapGeo, fDist, three, heap, applyTerrain, heapCells: { inner, hc, hr, res: T.res }, marks, featuresAt, scene, camera, controls, terrainHeight, heapInfo, setView, setViewpoint, state, drawOverlay, buildHeap, renderer };

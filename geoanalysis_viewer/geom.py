"""Pure-Python planar geometry in metric coordinates (EPSG:25832). No dependencies.

Conventions: a polygon is a list of rings; each ring is a list of [x, y] (closed or not).
Ring roles follow the even-odd rule (works for Esri rings without orientation checks).
"""
from __future__ import annotations

import math

Ring = list  # list[[x, y]]


def bbox(rings: list[Ring]) -> tuple[float, float, float, float]:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(a, b, pad: float = 0.0) -> bool:
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def ring_area(r: Ring) -> float:
    s = 0.0
    for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
        s += x1 * y2 - x2 * y1
    return s / 2.0


def polygon_area(rings: list[Ring]) -> float:
    """Area with even-odd semantics approximated as |outer| - sum|inner| (first ring = outer)."""
    a = abs(ring_area(rings[0]))
    return a - sum(abs(ring_area(r)) for r in rings[1:])


def point_in_ring(x: float, y: float, r: Ring) -> bool:
    inside = False
    n = len(r)
    j = n - 1
    for i in range(n):
        xi, yi = r[i]
        xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygon(x: float, y: float, rings: list[Ring]) -> bool:
    inside = False
    for r in rings:
        if point_in_ring(x, y, r):
            inside = not inside
    return inside


def seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    u = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(ax + u * dx - px, ay + u * dy - py)


def point_to_rings_dist(x, y, rings: list[Ring]) -> float:
    best = math.inf
    for r in rings:
        for (ax, ay), (bx, by) in zip(r, r[1:] + r[:1]):
            d = seg_dist(x, y, ax, ay, bx, by)
            if d < best:
                best = d
    return best


def _segments(rings: list[Ring]) -> list[tuple[float, float, float, float]]:
    return [(ax, ay, bx, by) for r in rings for (ax, ay), (bx, by) in zip(r, r[1:] + r[:1])
            if ax != bx or ay != by]


def _segments_cross(s, t) -> bool:
    """True if two segments intersect (touching counts)."""
    ax, ay, bx, by = s
    cx, cy, dx, dy = t
    if max(ax, bx) < min(cx, dx) or max(cx, dx) < min(ax, bx) or max(ay, by) < min(cy, dy) or max(cy, dy) < min(ay, by):
        return False
    d1 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d2 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    d3 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    d4 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
    if (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0):
        return True
    # collinear / endpoint-on-segment cases: an endpoint lies on the other segment
    return (seg_dist(cx, cy, ax, ay, bx, by) == 0 or seg_dist(dx, dy, ax, ay, bx, by) == 0
            or seg_dist(ax, ay, cx, cy, dx, dy) == 0 or seg_dist(bx, by, cx, cy, dx, dy) == 0)


def boundary_distance(a: list[Ring], b: list[Ring]) -> float:
    """Exact minimum distance between two polygons (0 if they overlap or touch).

    Between boundaries that do not cross, the minimum is always attained at a vertex of
    one of them, so vertex-to-segment distances in both directions are sufficient.
    """
    sa, sb = _segments(a), _segments(b)
    if not sa or not sb:
        return math.inf
    # containment without crossing edges: test one vertex of every ring, in both directions
    if any(r and point_in_polygon(r[0][0], r[0][1], b) for r in a) \
            or any(r and point_in_polygon(r[0][0], r[0][1], a) for r in b):
        return 0.0
    ba, bb = bbox(a), bbox(b)
    if bbox_intersects(ba, bb):
        near_b = [t for t in sb if not (max(t[0], t[2]) < ba[0] or min(t[0], t[2]) > ba[2]
                                        or max(t[1], t[3]) < ba[1] or min(t[1], t[3]) > ba[3])]
        for s in sa:
            for t in near_b:
                if _segments_cross(s, t):
                    return 0.0
    best = math.inf
    for src, dst in ((sa, sb), (sb, sa)):
        for x, y, _, _ in src:
            for ax, ay, bx, by in dst:
                # cheap reject against the current best
                if (ax if ax < bx else bx) - best > x or (ax if ax > bx else bx) + best < x \
                        or (ay if ay < by else by) - best > y or (ay if ay > by else by) + best < y:
                    continue
                d = seg_dist(x, y, ax, ay, bx, by)
                if d < best:
                    best = d
    return best


def overlap_stats_grid(site: list[Ring], other: list[Ring], res: float = 1.0) -> dict:
    """Old point-grid sampler. Slow; kept as an independent oracle for the tests."""
    sb, ob = bbox(site), bbox(other)
    if not bbox_intersects(sb, ob):
        return {"overlap_m2": 0.0, "depth_m": 0.0, "res_m": res}
    x0, y0 = max(sb[0], ob[0]), max(sb[1], ob[1])
    x1, y1 = min(sb[2], ob[2]), min(sb[3], ob[3])
    n, depth = 0, 0.0
    y = y0 + res / 2
    while y < y1:
        x = x0 + res / 2
        while x < x1:
            if point_in_polygon(x, y, site) and point_in_polygon(x, y, other):
                n += 1
                d = point_to_rings_dist(x, y, site)
                if d > depth:
                    depth = d
            x += res
        y += res
    return {"overlap_m2": n * res * res, "depth_m": depth, "res_m": res}


def _row_buckets(rings: list[Ring], y0: float, res: float, nrows: int) -> list[list]:
    """For each scanline row, the edges that cross it (half-open rule, as in point_in_ring)."""
    rows = [[] for _ in range(nrows)]
    for ax, ay, bx, by in _segments(rings):
        if ay == by:
            continue
        lo, hi = (ay, by) if ay < by else (by, ay)
        # row i has y = y0 + (i + 0.5) * res; pad by one row, _spans applies the exact rule
        i0 = max(0, math.floor((lo - y0) / res - 0.5))
        i1 = min(nrows - 1, math.ceil((hi - y0) / res - 0.5))
        for i in range(i0, i1 + 1):
            rows[i].append((ax, ay, bx, by))
    return rows


def _spans(edges: list, y: float) -> list[tuple[float, float]]:
    """Even-odd interior intervals of a polygon along the horizontal line at y."""
    xs = sorted(ax + (y - ay) * (bx - ax) / (by - ay) for ax, ay, bx, by in edges
                if (ay > y) != (by > y))
    return list(zip(xs[0::2], xs[1::2]))


def _intersect_spans(a: list, b: list) -> list[tuple[float, float]]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def overlap_stats(site: list[Ring], other: list[Ring], res: float = 0.5, max_depth_samples: int = 20000) -> dict:
    """Overlap area (m²) and max penetration depth (m) of `other` into `site` by scanline intersection.

    Each row is intersected exactly in x (edge crossings, even-odd); rows are `res` apart
    (midpoint rule in y). Depth is the largest distance from a sample point of the
    intersection to the site boundary; samples are `depth_res_m` apart, which grows above
    `res` only when the intersection is large (cap of about `max_depth_samples` points).
    """
    sb, ob = bbox(site), bbox(other)
    zero = {"overlap_m2": 0.0, "depth_m": 0.0, "res_m": res, "depth_res_m": res, "method": "scanline"}
    if not bbox_intersects(sb, ob):
        return zero
    y0, y1 = max(sb[1], ob[1]), min(sb[3], ob[3])
    nrows = max(1, math.ceil((y1 - y0) / res))
    rs, ro = _row_buckets(site, y0, res, nrows), _row_buckets(other, y0, res, nrows)
    rows, total = [], 0.0
    for i in range(nrows):
        if not rs[i] or not ro[i]:
            continue
        y = y0 + (i + 0.5) * res
        sp = _intersect_spans(_spans(rs[i], y), _spans(ro[i], y))
        if sp:
            rows.append((i, y, sp))
            total += sum(hi - lo for lo, hi in sp)
    area = total * res
    if area <= 0:
        return zero
    ds = max(res, math.sqrt(area / max_depth_samples))
    every = max(1, round(ds / res))
    if len(rows) <= every:
        every = 1
    segs = _segments(site)
    depth = 0.0
    for i, y, sp in rows:
        if i % every:
            continue
        for lo, hi in sp:
            n = max(1, math.ceil((hi - lo) / ds))
            for k in range(n + 1):
                x = lo + (hi - lo) * k / n
                best = math.inf
                for ax, ay, bx, by in segs:
                    d = seg_dist(x, y, ax, ay, bx, by)
                    if d < best:
                        best = d
                        if best <= depth:  # this point cannot raise the maximum
                            break
                if best > depth:
                    depth = best
    return {"overlap_m2": area, "depth_m": depth, "res_m": res, "depth_res_m": every * res, "method": "scanline"}


def clip_ring_to_bbox(r: Ring, b) -> Ring | None:
    """Sutherland-Hodgman clip against an axis-aligned box (area-correct, may leave degenerate edges)."""
    xmin, ymin, xmax, ymax = b
    edges = [
        (lambda p: p[0] >= xmin, lambda a, c: [xmin, a[1] + (xmin - a[0]) * (c[1] - a[1]) / (c[0] - a[0])]),
        (lambda p: p[0] <= xmax, lambda a, c: [xmax, a[1] + (xmax - a[0]) * (c[1] - a[1]) / (c[0] - a[0])]),
        (lambda p: p[1] >= ymin, lambda a, c: [a[0] + (ymin - a[1]) * (c[0] - a[0]) / (c[1] - a[1]), ymin]),
        (lambda p: p[1] <= ymax, lambda a, c: [a[0] + (ymax - a[1]) * (c[0] - a[0]) / (c[1] - a[1]), ymax]),
    ]
    out = list(r)
    for inside, inter in edges:
        inp, out = out, []
        if not inp:
            return None
        s = inp[-1]
        for e in inp:
            if inside(e):
                if not inside(s):
                    out.append(inter(s, e))
                out.append(e)
            elif inside(s):
                out.append(inter(s, e))
            s = e
    return out if len(out) >= 3 else None


def simplify(pts: list, tol: float) -> list:
    """Douglas-Peucker: drop points closer than `tol` to the line between the points kept around
    them. First and last point stay (a closed ring keeps its closing point)."""
    if len(pts) < 3 or tol <= 0:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = pts[i][0], pts[i][1]
        bx, by = pts[j][0], pts[j][1]
        best, k = -1.0, -1
        for m in range(i + 1, j):
            d = seg_dist(pts[m][0], pts[m][1], ax, ay, bx, by)
            if d > best:
                best, k = d, m
        if k > 0 and best > tol:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, f in zip(pts, keep) if f]


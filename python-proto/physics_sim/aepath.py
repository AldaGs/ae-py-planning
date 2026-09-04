"""
AE shape-layer paths -> polygons.

THE FORMAT
----------
An AE Path property's value is a Shape with four fields:

    {"vertices":    [[x, y], ...],
     "inTangents":  [[dx, dy], ...],
     "outTangents": [[dx, dy], ...],
     "closed":      true}

The trap is that **tangents are stored relative to their own vertex**, not as
absolute control points. The cubic running from vertex i to vertex i+1 is

    P0 = v[i]
    P1 = v[i]   + outTangents[i]
    P2 = v[i+1] + inTangents[i+1]
    P3 = v[i+1]

Reading them as absolute points collapses every shape toward the origin, which
is checked explicitly as a broken control in a3_paths.py.

ASSUMPTION TO VERIFY IN B1
--------------------------
This module assumes path coordinates arrive in the same layer space that the
anchor point is expressed in. For a plain shape layer with an untransformed
group that holds, but shape *group* transforms sit between the path and the
layer, and this sandbox has no real AE dump to confirm against. B1 is where
that gets checked against actual `evalScript` output rather than assumed.
"""

from __future__ import annotations

import math

Vec = tuple[float, float]

# Circle constant: a quarter arc of radius r has tangents of length k*r.
KAPPA = 4.0 / 3.0 * (math.sqrt(2.0) - 1.0)


def circle_path(radius: float, center: Vec = (0.0, 0.0)) -> dict:
    """A circle as AE would store it: four cubics. Ground truth for the
    flattening checks, since we know its exact area and radius."""
    cx, cy = center
    r, k = radius, KAPPA * radius
    return {
        "vertices": [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)],
        "outTangents": [(0.0, k), (-k, 0.0), (0.0, -k), (k, 0.0)],
        "inTangents": [(0.0, -k), (k, 0.0), (0.0, k), (-k, 0.0)],
        "closed": True,
    }


def polygon_path(verts: list[Vec]) -> dict:
    """A straight-edged path: all tangents zero, as AE stores corners."""
    z = [(0.0, 0.0)] * len(verts)
    return {"vertices": list(verts), "inTangents": z, "outTangents": list(z),
            "closed": True}


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------

def _flatness(p0: Vec, p1: Vec, p2: Vec, p3: Vec) -> float:
    """Greatest distance of the two control points from the chord P0-P3."""
    ax, ay = p3[0] - p0[0], p3[1] - p0[1]
    n = math.hypot(ax, ay)
    if n < 1e-12:
        return max(math.dist(p1, p0), math.dist(p2, p0))
    d1 = abs((p1[0] - p0[0]) * ay - (p1[1] - p0[1]) * ax) / n
    d2 = abs((p2[0] - p0[0]) * ay - (p2[1] - p0[1]) * ax) / n
    return max(d1, d2)


def _mid(a: Vec, b: Vec) -> Vec:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def flatten_cubic(p0: Vec, p1: Vec, p2: Vec, p3: Vec, tol: float,
                  out: list[Vec], depth: int = 0) -> None:
    """Adaptive de Casteljau subdivision.

    Adaptive rather than a fixed step count: a fixed count over-samples gentle
    curves and under-samples tight ones, and the whole point of `tol` is that
    the error is something we can state in pixels rather than hope about.
    """
    if depth >= 20 or _flatness(p0, p1, p2, p3) <= tol:
        out.append(p3)
        return
    p01, p12, p23 = _mid(p0, p1), _mid(p1, p2), _mid(p2, p3)
    p012, p123 = _mid(p01, p12), _mid(p12, p23)
    mid = _mid(p012, p123)
    flatten_cubic(p0, p01, p012, mid, tol, out, depth + 1)
    flatten_cubic(mid, p123, p23, p3, tol, out, depth + 1)


def flatten_path(shape: dict, tol: float = 0.25,
                 absolute_tangents: bool = False) -> list[Vec]:
    """AE Shape -> polygon. `tol` is the max chord deviation in px.

    `absolute_tangents=True` is the WRONG reading, kept so a3_paths.py can
    show what the mistake costs.
    """
    v = [tuple(map(float, p)) for p in shape["vertices"]]
    it = [tuple(map(float, p)) for p in shape["inTangents"]]
    ot = [tuple(map(float, p)) for p in shape["outTangents"]]
    n = len(v)
    if n < 2:
        raise ValueError("path needs at least two vertices")

    closed = shape.get("closed", True)
    out: list[Vec] = [v[0]]
    last = n if closed else n - 1
    for i in range(last):
        j = (i + 1) % n
        if absolute_tangents:
            p1, p2 = ot[i], it[j]
        else:
            p1 = (v[i][0] + ot[i][0], v[i][1] + ot[i][1])
            p2 = (v[j][0] + it[j][0], v[j][1] + it[j][1])
        flatten_cubic(v[i], p1, p2, v[j], tol, out)

    if closed and math.dist(out[0], out[-1]) < 1e-9:
        out.pop()
    return out


# --------------------------------------------------------------------------
# Simplification
# --------------------------------------------------------------------------

def _perp_distance(p: Vec, a: Vec, b: Vec) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    n = math.hypot(ax, ay)
    if n < 1e-12:
        return math.dist(p, a)
    return abs((p[0] - a[0]) * ay - (p[1] - a[1]) * ax) / n


def rdp(points: list[Vec], tol: float) -> list[Vec]:
    """Ramer-Douglas-Peucker on an open chain."""
    if len(points) < 3:
        return list(points)
    worst, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _perp_distance(points[i], points[0], points[-1])
        if d > worst:
            worst, idx = d, i
    if worst <= tol:
        return [points[0], points[-1]]
    left = rdp(points[:idx + 1], tol)
    right = rdp(points[idx:], tol)
    return left[:-1] + right


def simplify_closed(poly: list[Vec], tol: float) -> list[Vec]:
    """RDP for a ring.

    A ring has no endpoints, so it is split at two far-apart anchors and
    simplified as two chains -- otherwise RDP's fixed first/last vertices
    become arbitrary and the result depends on where the list happens to start.
    """
    n = len(poly)
    if n < 4:
        return list(poly)
    half = n // 2
    a = rdp(poly[:half + 1], tol)
    b = rdp(poly[half:] + [poly[0]], tol)
    return a[:-1] + b[:-1]


def max_deviation(original: list[Vec], simplified: list[Vec]) -> float:
    """Greatest distance from any original vertex to the simplified ring.

    This is the number that says whether a simplification tolerance was
    honoured, and it is worth measuring rather than trusting.
    """
    m = len(simplified)
    worst = 0.0
    for p in original:
        d = min(
            _segment_distance(p, simplified[i], simplified[(i + 1) % m])
            for i in range(m)
        )
        worst = max(worst, d)
    return worst


def _segment_distance(p: Vec, a: Vec, b: Vec) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    L2 = ax * ax + ay * ay
    if L2 < 1e-18:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2))
    return math.dist(p, (a[0] + t * ax, a[1] + t * ay))

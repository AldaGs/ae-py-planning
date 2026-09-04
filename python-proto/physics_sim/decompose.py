"""
Concave polygon -> convex parts (Wall C).

Every 2D engine collides convex shapes only, so a real silhouette has to be
cut up before it can be a body. The route here is the classic practical one:

    ear clipping  ->  triangles  ->  Hertel-Mehlhorn merge  ->  convex parts

Ear clipping is O(n^2) and easy to get right; Hertel-Mehlhorn then deletes any
diagonal whose removal leaves the merged piece convex. HM does not produce the
minimum number of pieces -- that needs Keil-Snoeyink dynamic programming -- but
it is guaranteed within 4x of optimal and it is enough to keep the part count
off the solver's back.

The thing that must not happen is a sliver: a near-degenerate part makes the
solver's contact normals unstable. a3_paths.py measures the worst part's
aspect ratio rather than trusting that they came out reasonable.
"""

from __future__ import annotations

import math

import geom

Vec = tuple[float, float]
Poly = list[Vec]


def _cross3(o: Vec, a: Vec, b: Vec) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def is_convex(poly: Poly, tol: float = 1e-9) -> bool:
    """True if every turn goes the same way (collinear vertices allowed)."""
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        c = _cross3(poly[i], poly[(i + 1) % n], poly[(i + 2) % n])
        if abs(c) <= tol:
            continue
        s = 1 if c > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def _point_in_triangle(p: Vec, a: Vec, b: Vec, c: Vec) -> bool:
    d1 = _cross3(a, b, p)
    d2 = _cross3(b, c, p)
    d3 = _cross3(c, a, p)
    neg = (d1 < -1e-12) or (d2 < -1e-12) or (d3 < -1e-12)
    pos = (d1 > 1e-12) or (d2 > 1e-12) or (d3 > 1e-12)
    return not (neg and pos)


def triangulate(poly: Poly) -> list[Poly]:
    """Ear clipping. Input may be concave; must be simple (non-self-crossing)."""
    v = geom.ensure_ccw(poly)
    n = len(v)
    if n < 3:
        return []
    idx = list(range(n))
    tris: list[Poly] = []
    guard = 0

    while len(idx) > 3:
        guard += 1
        if guard > 4 * n * n:
            raise ValueError(
                "ear clipping stalled -- polygon is probably self-intersecting"
            )
        clipped = False
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[(k - 1) % m], idx[k], idx[(k + 1) % m]
            a, b, c = v[i0], v[i1], v[i2]
            if _cross3(a, b, c) <= 1e-12:      # reflex or collinear: no ear
                continue
            if any(
                _point_in_triangle(v[j], a, b, c)
                for j in idx if j not in (i0, i1, i2)
            ):
                continue
            tris.append([a, b, c])
            idx.pop(k)
            clipped = True
            break
        if not clipped:
            raise ValueError("ear clipping found no ear -- degenerate input")

    tris.append([v[idx[0]], v[idx[1]], v[idx[2]]])
    return tris


def _key(p: Vec, q: float = 1e6) -> tuple[int, int]:
    return (round(p[0] * q), round(p[1] * q))


def _merge_on_shared_edge(p: Poly, q: Poly) -> Poly | None:
    """If p and q share an edge traversed in opposite directions, splice them.

    p runs ... A, B ...   and q runs ... B, A ...
    The merged ring is p up to A, then q's interior, then p from B onward.
    """
    np_, nq = len(p), len(q)
    pk = [_key(x) for x in p]
    qk = [_key(x) for x in q]
    for i in range(np_):
        a, b = pk[i], pk[(i + 1) % np_]
        for j in range(nq):
            if qk[j] == b and qk[(j + 1) % nq] == a:
                interior = [q[(j + 2 + t) % nq] for t in range(nq - 2)]
                return p[:i + 1] + interior + p[i + 1:]
    return None


def convex_parts(poly: Poly, max_verts: int = 8) -> list[Poly]:
    """Concave polygon -> list of convex polygons covering the same area.

    `max_verts` caps part complexity because Chipmunk (and Box2D more so) get
    slower and less stable with very many-sided shapes.
    """
    parts = [list(t) for t in triangulate(poly)]

    merged = True
    while merged:
        merged = False
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                cand = _merge_on_shared_edge(parts[i], parts[j])
                if cand is None or len(cand) > max_verts:
                    continue
                if not is_convex(cand):
                    continue
                parts[i] = cand
                parts.pop(j)
                merged = True
                break
            if merged:
                break
    return [geom.ensure_ccw(p) for p in parts]


def aspect_ratio(poly: Poly) -> float:
    """Longest edge over the shape's thickness (area / longest edge).

    A high number means a sliver, which is what destabilises contacts.
    """
    n = len(poly)
    longest = max(math.dist(poly[i], poly[(i + 1) % n]) for i in range(n))
    area = abs(geom.signed_area(poly))
    if area < 1e-12:
        return float("inf")
    return longest / (area / longest)

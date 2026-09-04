"""
Rendered alpha -> contours -> polygons with holes resolved (Wall B).

This is the pipeline the Phase D AEGP will have to run in C++, so it is written
as an algorithm rather than as a call into a library: marching squares with
sub-pixel interpolation, loop stitching, nesting analysis, then hole bridging.

WHY INTERPOLATED MARCHING SQUARES
---------------------------------
AE hands us antialiased edges -- about 1.4% of the pixels in these exports are
partial alpha. Tracing the pixel staircase at a hard threshold throws that
information away and leaves a jagged contour that RDP then has to smooth back
out. Interpolating each crossing to the alpha=threshold iso-line uses the AA
band for what it is: sub-pixel edge position.

ORIENTATION
-----------
The segment table below is self-consistent, which is all that loop stitching
needs. Which loops are outers and which are holes is decided afterwards by
nesting depth, not by trusting a winding convention -- and winding is then
normalised so outers are CCW and holes CW, which is what the bridging step
requires.
"""

from __future__ import annotations

import math

import numpy as np

import geom

Vec = tuple[float, float]

# case -> list of (from_edge, to_edge), edges named T R B L.
# case bits: TL=8, TR=4, BR=2, BL=1  (set == inside)
_TABLE = {
    1:  [("L", "B")],
    2:  [("B", "R")],
    3:  [("L", "R")],
    4:  [("R", "T")],
    6:  [("B", "T")],
    7:  [("L", "T")],
    8:  [("T", "L")],
    9:  [("T", "B")],
    11: [("T", "R")],
    12: [("R", "L")],
    13: [("R", "B")],
    14: [("B", "L")],
}
# Saddles resolved by the cell's mean value.
_SADDLE = {
    (5, True):  [("L", "T"), ("R", "B")],
    (5, False): [("L", "B"), ("R", "T")],
    (10, True): [("T", "R"), ("B", "L")],
    (10, False): [("T", "L"), ("B", "R")],
}


def _interp(v0: float, v1: float, t: float) -> float:
    d = v1 - v0
    return 0.5 if abs(d) < 1e-12 else (t - v0) / d


def marching_squares(field: np.ndarray, threshold: float,
                     nudge: bool = True):
    """Iso-contours of `field` at `threshold`, as closed loops in pixel coords.

    Returns (loops, stats). The stats matter: this routine can fail SILENTLY,
    and did. See the degeneracy note below.

    The field is zero-padded so shapes running off the edge still close.

    DEGENERATE SAMPLES
    ------------------
    If a corner sample equals the threshold exactly, the crossings on both of
    its edges interpolate to t=0 -- the corner itself -- so two different edge
    points become the same point. Keyed by start point, one segment then
    overwrites the other and the loop silently shatters.

    Eight pixels of alpha==128 in a 300x300 export were enough to turn one
    contour into 76 fragments, with no error raised anywhere. Nudging any
    exactly-on-threshold sample off the iso-value costs nothing and removes
    the whole class of failure.
    """
    g = np.pad(field.astype(np.float64), 1, constant_values=0.0)
    if nudge:
        g = np.where(g == threshold, g + 1e-3, g)
    inside = g >= threshold

    tl, tr = inside[:-1, :-1], inside[:-1, 1:]
    br, bl = inside[1:, 1:], inside[1:, :-1]
    case = (tl.astype(np.uint8) << 3 | tr.astype(np.uint8) << 2
            | br.astype(np.uint8) << 1 | bl.astype(np.uint8))

    segs: dict[tuple[int, int], tuple[Vec, Vec]] = {}
    collisions = 0
    key = lambda p: (round(p[0] * 1e6), round(p[1] * 1e6))  # noqa: E731

    for y, x in np.argwhere((case != 0) & (case != 15)):
        y, x = int(y), int(x)
        v_tl, v_tr = g[y, x], g[y, x + 1]
        v_br, v_bl = g[y + 1, x + 1], g[y + 1, x]
        pts = {
            "T": (x + _interp(v_tl, v_tr, threshold), float(y)),
            "R": (float(x + 1), y + _interp(v_tr, v_br, threshold)),
            "B": (x + _interp(v_bl, v_br, threshold), float(y + 1)),
            "L": (float(x), y + _interp(v_tl, v_bl, threshold)),
        }
        c = int(case[y, x])
        if c in (5, 10):
            mean_inside = (v_tl + v_tr + v_br + v_bl) / 4.0 >= threshold
            pairs = _SADDLE[(c, mean_inside)]
        else:
            pairs = _TABLE[c]
        for a, b in pairs:
            k = key(pts[a])
            if k in segs:
                collisions += 1
            segs[k] = (pts[a], pts[b])

    total = len(segs)
    loops: list[list[Vec]] = []
    open_chains = 0
    while segs:
        start_k = next(iter(segs))
        p0, p1 = segs.pop(start_k)
        loop = [p0, p1]
        closed = False
        while True:
            k = key(loop[-1])
            if k not in segs:
                break
            _a, b = segs.pop(k)
            if key(b) == key(loop[0]):
                closed = True
                break
            loop.append(b)
        if not closed:
            open_chains += 1
        if len(loop) >= 3:
            loops.append([(px - 1.0, py - 1.0) for px, py in loop])

    stats = {"segments": total, "collisions": collisions,
             "open_chains": open_chains, "loops": len(loops)}
    return loops, stats


# --------------------------------------------------------------------------
# Nesting
# --------------------------------------------------------------------------

def _point_in_poly(p: Vec, poly: list[Vec]) -> bool:
    x, y = p
    n, inside = len(poly), False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xi = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xi:
                inside = not inside
    return inside


def classify(loops: list[list[Vec]]) -> list[tuple[list[Vec], list[list[Vec]]]]:
    """Group loops into (outer, [holes]).

    Depth is counted by containment rather than read off the winding, so a
    wrong sign anywhere in the segment table cannot mis-label a hole as an
    island. Even depth is solid, odd depth is a hole, and a hole belongs to the
    smallest loop that contains it.
    """
    depth = []
    for i, a in enumerate(loops):
        d = sum(1 for j, b in enumerate(loops)
                if i != j and _point_in_poly(a[0], b))
        depth.append(d)

    groups = []
    for i, a in enumerate(loops):
        if depth[i] % 2 != 0:
            continue
        holes = []
        for j, b in enumerate(loops):
            if i == j or depth[j] != depth[i] + 1:
                continue
            if not _point_in_poly(b[0], a):
                continue
            # nearest containing outer wins, so nested rings behave
            better = any(
                k not in (i, j) and depth[k] == depth[i]
                and _point_in_poly(b[0], loops[k])
                and abs(geom.signed_area(loops[k])) < abs(geom.signed_area(a))
                for k in range(len(loops))
            )
            if not better:
                holes.append(b)
        groups.append((geom.ensure_ccw(a),
                       [list(reversed(geom.ensure_ccw(h))) for h in holes]))
    return groups


# --------------------------------------------------------------------------
# Hole bridging
# --------------------------------------------------------------------------

def _segments_cross(a: Vec, b: Vec, c: Vec, d: Vec) -> bool:
    def side(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    d1, d2 = side(a, b, c), side(a, b, d)
    d3, d4 = side(c, d, a), side(c, d, b)
    return d1 * d2 < 0 and d3 * d4 < 0


def bridge_holes(outer: list[Vec], holes: list[list[Vec]]) -> list[Vec]:
    """Splice each hole into the outer ring along a non-crossing bridge.

    The result is a simple (if degenerate-looking) polygon: the bridge is
    traversed once in each direction, which ear clipping tolerates and which
    keeps the hole's area correctly excluded.

    Candidate bridges are tried nearest-first and rejected if they cross any
    edge of any contour still in play.
    """
    ring = list(outer)
    for hole in sorted(holes, key=lambda h: -abs(geom.signed_area(h))):
        others = [h for h in holes if h is not hole]
        cands = sorted(
            ((math.dist(ring[i], hole[j]), i, j)
             for i in range(len(ring)) for j in range(len(hole))),
            key=lambda t: t[0],
        )
        spliced = None
        for _d, i, j in cands:
            a, b = ring[i], hole[j]
            blocked = False
            for poly in [ring, hole, *others]:
                n = len(poly)
                for k in range(n):
                    p, q = poly[k], poly[(k + 1) % n]
                    if _segments_cross(a, b, p, q):
                        blocked = True
                        break
                if blocked:
                    break
            if not blocked:
                spliced = (ring[:i + 1] + hole[j:] + hole[:j + 1] + ring[i:])
                break
        if spliced is None:
            raise ValueError("no visible bridge found for hole")
        ring = spliced
    return ring


# --------------------------------------------------------------------------
# Top level
# --------------------------------------------------------------------------

def contours_from_alpha(alpha: np.ndarray, threshold: float = 128.0,
                        min_area: float = 12.0, with_stats: bool = False,
                        nudge: bool = True):
    """alpha image -> [(outer, [holes])], specks dropped.

    `min_area` exists because a stray antialiased pixel becomes a valid tiny
    contour, and a body made of one is a body the solver has to think about
    forever.
    """
    loops, stats = marching_squares(alpha, threshold, nudge=nudge)
    groups = classify(loops)
    groups = [
        (o, [h for h in hs if abs(geom.signed_area(h)) >= min_area])
        for o, hs in groups
        if abs(geom.signed_area(o)) >= min_area
    ]
    return (groups, stats) if with_stats else groups


def net_area(groups) -> float:
    """Solid area: outers minus their holes."""
    return sum(
        abs(geom.signed_area(o)) - sum(abs(geom.signed_area(h)) for h in hs)
        for o, hs in groups
    )

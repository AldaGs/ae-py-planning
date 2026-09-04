"""
Polygon mass properties, done by hand.

A2 needs a centre of mass that is NOT the obvious centre of the shape. For a
box or a circle the COM is the middle and Wall E hides; for a triangle it sits
at one third, and for a compound shape it sits wherever the areas put it.

Everything here is cross-checked against pymunk's own implementation in
a2_anchor_com.py -- two independent computations agreeing is the point.

All functions take vertices as a sequence of (x, y) in ANY consistent unit and
assume uniform density.
"""

from __future__ import annotations

import math

Vec = tuple[float, float]
Poly = list[Vec]


def _cross(a: Vec, b: Vec) -> float:
    return a[0] * b[1] - b[0] * a[1]


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1]


def signed_area(verts: Poly) -> float:
    """Shoelace. Sign carries the winding, so callers can normalise it."""
    n = len(verts)
    return 0.5 * sum(_cross(verts[i], verts[(i + 1) % n]) for i in range(n))


def ensure_ccw(verts: Poly) -> Poly:
    """Normalise winding to positive signed area.

    AE paths arrive in either winding, and a negative-area polygon silently
    flips the sign of every moment computed from it.
    """
    return list(verts) if signed_area(verts) > 0.0 else list(reversed(verts))


def centroid(verts: Poly) -> Vec:
    """Area centroid:  C = 1/(6A) * sum (Pi + Pj) * cross(Pi, Pj)"""
    a = signed_area(verts)
    if abs(a) < 1e-15:
        raise ValueError("degenerate polygon: zero area")
    n = len(verts)
    cx = cy = 0.0
    for i in range(n):
        p, q = verts[i], verts[(i + 1) % n]
        w = _cross(p, q)
        cx += (p[0] + q[0]) * w
        cy += (p[1] + q[1]) * w
    return (cx / (6.0 * a), cy / (6.0 * a))


def moment_about_origin(verts: Poly, density: float) -> float:
    """Second moment of mass about the origin, uniform density.

        I = (rho/12) * sum cross(Pi,Pj) * (Pi.Pi + Pi.Pj + Pj.Pj)
    """
    n = len(verts)
    total = 0.0
    for i in range(n):
        p, q = verts[i], verts[(i + 1) % n]
        total += _cross(p, q) * (_dot(p, p) + _dot(p, q) + _dot(q, q))
    return abs(density * total / 12.0)


def mass_properties(verts: Poly, density: float) -> tuple[float, Vec, float]:
    """(mass, centroid, moment about the centroid) for one convex polygon."""
    v = ensure_ccw(verts)
    area = abs(signed_area(v))
    mass = density * area
    c = centroid(v)
    # Parallel axis, backwards: I_c = I_o - m*d^2
    i_c = moment_about_origin(v, density) - mass * _dot(c, c)
    return mass, c, i_c


def compound_mass_properties(
    parts: list[Poly], density: float
) -> tuple[float, Vec, float]:
    """(mass, COM, moment about the COM) for several convex parts.

    This is the machinery A3's convex decomposition will need: several convex
    pieces standing in for one concave layer, sharing a single rigid body.
    """
    if not parts:
        raise ValueError("compound body with no parts")

    per = [mass_properties(p, density) for p in parts]
    total = sum(m for m, _c, _i in per)
    com = (
        sum(m * c[0] for m, c, _i in per) / total,
        sum(m * c[1] for m, c, _i in per) / total,
    )
    # Parallel axis, forwards: shift each part's moment to the shared COM.
    moment = sum(
        i_c + m * ((c[0] - com[0]) ** 2 + (c[1] - com[1]) ** 2)
        for m, c, i_c in per
    )
    return total, com, moment


def bbox_center(parts: list[Poly]) -> Vec:
    """The WRONG centre -- kept so A2 can show how far off it is."""
    xs = [v[0] for p in parts for v in p]
    ys = [v[1] for p in parts for v in p]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def rotate(v: Vec, rad: float) -> Vec:
    c, s = math.cos(rad), math.sin(rad)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])

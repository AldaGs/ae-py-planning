"""
B1 -- the INPUT schema, `ae-physics-scene/1`.

Phase A had no input format. Its scenes were Python dataclasses written by
hand in a1..a5, which is fine for a sandbox and useless the moment something
outside Python has to produce one. B1 is where that gets fixed, because
ExtendScript has to hand us a comp.

    ae-physics-scene/1   ExtendScript writes it, this module reads it
    ae-physics-bake/2    this module's Scene produces it, B2 applies it

THE CONTRACT WITH THE SCRIPT
----------------------------
The single most important line in this file:

    **path vertices arrive in LAYER space.**

They do not start there. An AE shape layer nests paths inside groups, and every
group has its own transform (anchor, position, scale, rotation, skew). A3 wrote
down that it was *assuming* layer space and flagged it for B1; the answer is
that the assumption is false in AE and true in this schema, because composing
the group transforms down is the SCRIPT's job. Complexity belongs at the
boundary, not in the solver. `compose_point` is that math, kept here so it can
be tested offline against the jsx that mirrors it.

WHY LAYERS CARRY AN `id`
------------------------
AE lets two layers share a name. "Shape Layer 1" twice in one comp is the
default, not an edge case. So name is for humans and `id` -- the AE layer index,
unique within a comp -- is the join key between a scene and its bake. Phase A
keyed on name because Phase A invented its own names; that would have collided
on the first real comp.

THERE IS NO scene_to_doc()
--------------------------
The obvious round-trip check -- Scene -> document -> Scene -- cannot be written,
and finding that out is worth more than the check would have been. A `Scene`
holds CONVEX PARTS: it sits downstream of flattening, simplification and ear
clipping, and the contours it came from are gone. Writing the parts back out as
paths does not reproduce the document, it produces a different one that happens
to draw the same picture -- and re-reading it re-runs containment nesting over
polygons that share edges, which lands in `triangulate` with no ear to clip.

So the round trip that means anything is at the DOCUMENT level: text -> doc ->
Scene -> bake, twice, compared byte for byte. Phase C will want a real writer
for a panel that edits scenes; that writer needs bodies to keep their source
contours, which is a change to `PolyBody` and not a detail of this module.

SCALE
-----
A rigid body has no scale, so a layer at 200% cannot be modelled as-is. Scale is
BAKED INTO the geometry at read time -- vertices and anchor both multiplied --
which is exact, because AE applies scale in layer space before rotation:

    comp = position + R(theta) * (S * (p - anchor))
         = position + R(theta) * ((S*p) - (S*anchor))

The layer's own Scale property is left alone in AE; we only ever write Position
and Rotation back. An ANIMATED scale is a different matter -- that is not a
rigid body at all -- and gets a warning.
"""

from __future__ import annotations

import math

import aepath
import alpha_contours as ac
import decompose
import sim

SCENE_SCHEMA = "ae-physics-scene/1"

FLATTEN_TOL = 0.25
SIMPLIFY_TOL = 1.0


class SceneError(ValueError):
    """Raised with every problem found, not just the first.

    The producer is a hand-rolled ES3 serialiser in a host application we
    cannot debug from here. One error per run would be a miserable loop.
    """


# --------------------------------------------------------------------------
# The transform maths the script mirrors
# --------------------------------------------------------------------------

def compose_point(p, anchor, position, scale, rotation_deg, skew=0.0,
                  skew_axis_deg=0.0):
    """One AE transform applied to one point, group or layer -- same form.

        parent = position + R(rot) * Skew * S(scale) * (p - anchor)

    Scale arrives as AE stores it, in percent.
    """
    x = (p[0] - anchor[0]) * scale[0] / 100.0
    y = (p[1] - anchor[1]) * scale[1] / 100.0
    if skew:
        a = math.radians(skew_axis_deg)
        ca, sa = math.cos(a), math.sin(a)
        # Rotate into the skew axis, shear along x, rotate back.
        u, v = ca * x + sa * y, -sa * x + ca * y
        u += math.tan(math.radians(skew)) * v
        x, y = ca * u - sa * v, sa * u + ca * v
    r = math.radians(rotation_deg)
    c, s = math.cos(r), math.sin(r)
    return (position[0] + x * c - y * s, position[1] + x * s + y * c)


def compose_path(shape: dict, xform: dict) -> dict:
    """Apply a transform to a whole AE Shape, tangents included.

    Tangents are stored RELATIVE to their vertex (the trap `aepath` documents),
    so they transform as VECTORS: same matrix, no translation. Doing it by
    transforming the absolute control point and subtracting the transformed
    vertex gives the same answer and is what the jsx does, since it avoids
    writing a second matrix path that could drift from this one.
    """
    def pt(p):
        return compose_point(p, xform["anchor"], xform["position"],
                             xform["scale"], xform["rotation_deg"],
                             xform.get("skew", 0.0),
                             xform.get("skew_axis_deg", 0.0))

    v = [pt(p) for p in shape["vertices"]]
    out = []
    for chan in ("inTangents", "outTangents"):
        moved = []
        for i, t in enumerate(shape[chan]):
            av = shape["vertices"][i]
            abs_ctrl = pt((av[0] + t[0], av[1] + t[1]))
            moved.append((abs_ctrl[0] - v[i][0], abs_ctrl[1] - v[i][1]))
        out.append(moved)
    return {"vertices": v, "inTangents": out[0], "outTangents": out[1],
            "closed": shape.get("closed", True)}


IDENTITY = {"anchor": (0.0, 0.0), "position": (0.0, 0.0),
            "scale": (100.0, 100.0), "rotation_deg": 0.0}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _vec(v):
    return isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(c) for c in v)


def validate(doc) -> list[str]:
    """Every problem in the document, as sentences. Empty list means good."""
    p: list[str] = []
    if not isinstance(doc, dict):
        return ["document is not an object"]
    if doc.get("schema") != SCENE_SCHEMA:
        p.append(f"schema is {doc.get('schema')!r}, expected {SCENE_SCHEMA!r}")

    comp = doc.get("comp")
    if not isinstance(comp, dict):
        p.append("missing 'comp'")
    else:
        for k in ("width", "height", "fps", "duration_frames"):
            if not _num(comp.get(k)) or comp[k] <= 0:
                p.append(f"comp.{k} is {comp.get(k)!r}, want a positive number")

    layers = doc.get("layers")
    if not isinstance(layers, list) or not layers:
        p.append("'layers' must be a non-empty list")
        return p

    seen_ids: dict = {}
    for n, lay in enumerate(layers):
        tag = f"layer[{n}]"
        if not isinstance(lay, dict):
            p.append(f"{tag} is not an object")
            continue
        tag = f"layer {lay.get('id', '?')} ({lay.get('name', '?')!r})"

        lid = lay.get("id")
        if not isinstance(lid, int) or isinstance(lid, bool):
            p.append(f"{tag}: 'id' must be an integer AE layer index")
        elif lid in seen_ids:
            p.append(f"{tag}: duplicate id {lid}, already used by "
                     f"{seen_ids[lid]!r} -- ids are the join key with the bake")
        else:
            seen_ids[lid] = lay.get("name")

        for k in ("anchor", "position"):
            if not _vec(lay.get(k)):
                p.append(f"{tag}: '{k}' must be two finite numbers, got "
                         f"{lay.get(k)!r}")
        if not _num(lay.get("rotation_deg", 0.0)):
            p.append(f"{tag}: 'rotation_deg' is not a finite number")
        sc = lay.get("scale", [100.0, 100.0])
        if not _vec(sc):
            p.append(f"{tag}: 'scale' must be two finite numbers")
        elif sc[0] == 0 or sc[1] == 0:
            p.append(f"{tag}: scale {sc} collapses the layer to nothing")

        paths = lay.get("paths")
        if not isinstance(paths, list) or not paths:
            p.append(f"{tag}: 'paths' must be a non-empty list -- a layer with "
                     "no path has no collision shape")
            continue
        for j, sh in enumerate(paths):
            st = f"{tag} path[{j}]"
            if not isinstance(sh, dict):
                p.append(f"{st} is not an object")
                continue
            v = sh.get("vertices")
            if not isinstance(v, list) or len(v) < 2:
                p.append(f"{st}: needs at least 2 vertices, has "
                         f"{len(v) if isinstance(v, list) else '?'}")
                continue
            if not all(_vec(q) for q in v):
                p.append(f"{st}: a vertex is not two finite numbers "
                         "(NaN and null both land here)")
            for chan in ("inTangents", "outTangents"):
                t = sh.get(chan)
                if not isinstance(t, list) or len(t) != len(v):
                    p.append(f"{st}: {chan} has "
                             f"{len(t) if isinstance(t, list) else '?'} entries "
                             f"for {len(v)} vertices -- they must match")
                elif not all(_vec(q) for q in t):
                    p.append(f"{st}: a {chan} entry is not two finite numbers")
            if not sh.get("closed", True):
                p.append(f"{st}: path is open; a collision shape must be "
                         "closed (AE calls it 'Closed' on the path)")
    return p


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _polygons(lay, flatten_tol, simplify_tol):
    """A layer's paths -> convex parts, in scaled layer space.

    After flattening, this is byte-for-byte A4's pipeline: nest by containment,
    bridge holes, ear-clip. Two paths drawn as a ring nest exactly the way two
    alpha contours do, so the shape-layer and alpha routes converge here rather
    than each growing their own hole logic.
    """
    sx, sy = lay.get("scale", [100.0, 100.0])
    ax, ay = lay["anchor"]
    loops = []
    for sh in lay["paths"]:
        poly = aepath.flatten_path(sh, flatten_tol)
        poly = [((x - ax) * sx / 100.0, (y - ay) * sy / 100.0) for x, y in poly]
        loops.append(aepath.simplify_closed(poly, simplify_tol))

    parts = []
    for outer, holes in ac.classify(loops):
        ring = ac.bridge_holes(outer, holes) if holes else outer
        try:
            parts.extend(decompose.convex_parts(ring))
        except ValueError as e:
            raise SceneError(
                f"layer {lay['id']} ({lay['name']!r}): could not decompose a "
                f"contour of {len(ring)} vertices -- {e}. Self-intersecting or "
                "duplicated paths are the usual cause."
            ) from e
    return parts


def load_scene(doc, flatten_tol=FLATTEN_TOL, simplify_tol=SIMPLIFY_TOL):
    """Document -> (sim.Scene, meta).

    `meta` carries what the solver has no use for but a UI does: the AE layer
    id and name per body, in scene order, plus every warning raised while
    reading. Scale is baked into the geometry here, so the anchor becomes the
    origin and every body's anchor is (0,0) in its own scaled layer space.
    """
    problems = validate(doc)
    if problems:
        raise SceneError("scene document is not usable:\n  - "
                         + "\n  - ".join(problems))

    comp, s = doc["comp"], doc.get("sim", {})
    warnings = list(doc.get("warnings", []))
    bodies, meta = [], []

    for lay in doc["layers"]:
        sc = lay.get("scale", [100.0, 100.0])
        if abs(sc[0]) != abs(sc[1]):
            warnings.append(
                f"layer {lay['id']} ({lay['name']!r}) has non-uniform scale "
                f"{sc}; it is baked into the geometry, which is exact, but the "
                "body will not look square under rotation")
        if sc[0] < 0 or sc[1] < 0:
            warnings.append(
                f"layer {lay['id']} ({lay['name']!r}) has negative scale "
                f"{sc}; the contour winding flips and the decomposition may "
                "produce inverted parts")
        if lay.get("scale_animated"):
            warnings.append(
                f"layer {lay['id']} ({lay['name']!r}) has an ANIMATED scale. "
                "A rigid body has no scale: the value at the first frame is "
                "baked in and the animation is ignored")

        parts = _polygons(lay, flatten_tol, simplify_tol)
        if not parts:
            raise SceneError(f"layer {lay['id']} ({lay['name']!r}) produced no "
                             "convex parts from its paths")

        bodies.append(sim.PolyBody(
            name=lay["name"],
            parts=parts,
            anchor=(0.0, 0.0),          # scale-baked space is anchor-centred
            position=tuple(lay["position"]),
            angle_deg=float(lay.get("rotation_deg", 0.0)),
            velocity=tuple(lay.get("velocity", (0.0, 0.0))),
            angular_velocity_deg=float(lay.get("angular_velocity_deg", 0.0)),
            density=float(lay.get("density", 1.0)),
            friction=float(lay.get("friction", 0.6)),
            elasticity=float(lay.get("elasticity", 0.2)),
            static=bool(lay.get("static", False)),
        ))
        meta.append({"id": lay["id"], "name": lay["name"],
                     "parts": len(parts),
                     "static": bool(lay.get("static", False))})

    scene = sim.Scene(
        width=int(comp["width"]), height=int(comp["height"]),
        fps=float(comp["fps"]),
        duration_frames=int(comp["duration_frames"]),
        gravity_m_s2=float(s.get("gravity_m_s2", 9.8)),
        pixels_per_meter=float(s.get("pixels_per_meter", 100.0)),
        substeps=int(s.get("substeps", 8)),
        bodies=bodies,
        statics=[tuple(tuple(p) for p in seg) for seg in doc.get("statics", [])],
    )
    return scene, {"layers": meta, "warnings": warnings,
                   "comp_name": comp.get("name", "")}

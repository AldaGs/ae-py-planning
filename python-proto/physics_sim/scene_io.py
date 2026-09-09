"""
B1 -- the INPUT schema, `ae-physics-scene/1`.

Phase A had no input format. Its scenes were Python dataclasses written by
hand in a1..a5, which is fine for a sandbox and useless the moment something
outside Python has to produce one. B1 is where that gets fixed, because
ExtendScript has to hand us a comp.

    ae-physics-scene/2   ExtendScript writes it, this module reads it
    ae-physics-bake/3    this module's Scene produces it, B2 applies it

    ae-physics-scene/1   still read, upgraded on the way in. See below.

WHY /2 EXISTS, AND WHY IT IS MOSTLY EMPTY
-----------------------------------------
C1 ships an application, and an application writes documents that have to
survive it. The plan's Tier 0 is the list of features that RESHAPE this
document rather than extend it -- joints, zones, kinematic input tracks,
collision groups, N outputs per layer, a bake target other than in place. Every
one of them is a year away and every one of them, retrofitted, invalidates every
document written before it. So /2 carries the SHAPE of all seven now, with the
arrays empty and the scalars at their defaults, and adding the implementations
later is then an additive change like any other.

The slots, and what each is for:

    joints[]           relations BETWEEN bodies, so top-level, not per layer
    zones[]            force fields -- wind, magnets, explosions
    layer.motion       "dynamic" | "static" | "kinematic"; /1's boolean
                       `static` is the two-valued version of this
    layer.input_keyframes   a kinematic layer is driven by its OWN AE
                       keyframes, so it arrives carrying them
    layer.collision_group / collide_with    a group index and a bitmask
    layer.outputs      how many AE layers this input becomes (Wall J's island
                       split and pre-fracture are the same capability)
    output             where the bake lands: in place, or a new comp

READING /1
----------
`upgrade()` turns a /1 document into a /2 one and is the only place that knows
the difference; `validate()` and `load_scene()` both run it first, so nothing
downstream carries a version branch. It is pure -- the caller's document is not
touched -- and it is idempotent, which is checked rather than asserted.

This matters more than backwards compatibility usually does here: `b1_ae_export.json`
and `b1_fixture_scene.json` are /1 documents captured from a real comp and
hand-authored respectively, and they are the evidence behind B1's 22 checks. A
hard cut to /2 would have re-baselined all of it against a document no run in AE
had ever produced.

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

SCENE_SCHEMA = "ae-physics-scene/2"
SCENE_SCHEMA_V1 = "ae-physics-scene/1"
SCENE_SCHEMAS_READ = (SCENE_SCHEMA_V1, SCENE_SCHEMA)

FLATTEN_TOL = 0.25
SIMPLIFY_TOL = 1.0

# Tier 0 vocabularies. Kept as tuples so a typo in a document is a sentence
# naming the alternatives rather than a silent fall-through to the default.
MOTION_KINDS = ("dynamic", "static", "kinematic")
JOINT_TYPES = ("pivot", "spring", "weld", "distance")
ZONE_TYPES = ("wind", "magnet", "explosion")
BAKE_TARGETS = ("in_place", "new_comp")

# Every bit set: collide with every group. The mask is 16 bits because that is
# what both Chipmunk and Rapier give a category mask, and picking the smaller
# of the two now costs nothing.
COLLIDE_WITH_ALL = 0xFFFF


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
# Versioning
# --------------------------------------------------------------------------

def default_output() -> dict:
    """Where a bake lands. /1 had no choice and always overwrote the source
    layer's Position and Rotation, which is destructive and not what a careful
    user expects -- so the slot exists now and its default is the old
    behaviour, spelled out."""
    return {"target": "in_place", "comp_name": None}


def _layer_defaults(lay: dict) -> dict:
    """The Tier 0 fields a /2 layer carries, with /1's answers where /1 had one.

    `static` is the only real translation: /1 said a layer either was pinned or
    was not, and /2 says which of three kinds of motion it has. The boolean is
    KEPT alongside, because the solver, the bake and both jsx files read it and
    rewriting all of them to chase a rename is churn with no result. `validate`
    refuses a document where the two disagree, which is the part that matters.
    """
    static = bool(lay.get("static", False))
    return {
        "motion": lay.get("motion", "static" if static else "dynamic"),
        "static": static,
        # A kinematic layer is driven by its own AE keyframes rather than by
        # the solver. None means "not read"; an empty track would mean "read,
        # and there were none", and those are different facts.
        "input_keyframes": lay.get("input_keyframes"),
        "collision_group": lay.get("collision_group", 0),
        "collide_with": lay.get("collide_with", COLLIDE_WITH_ALL),
        # One input layer may become N output layers -- Wall J's island split
        # and pre-fracture are the same capability. 1 is today's answer.
        "outputs": lay.get("outputs", 1),
    }


def upgrade(doc):
    """Any accepted scene version -> /2. Pure, and idempotent.

    Returns the document unchanged in shape if it is already /2, and does not
    mutate the input in either case. Anything that is not a dict, or carries a
    version this module does not read, comes back untouched so that
    `validate()` is the thing that reports it -- an upgrader that raised would
    turn one legible sentence into a traceback.
    """
    if not isinstance(doc, dict) or doc.get("schema") not in SCENE_SCHEMAS_READ:
        return doc

    out = dict(doc)
    out["schema"] = SCENE_SCHEMA
    out.setdefault("joints", [])
    out.setdefault("zones", [])
    out["output"] = {**default_output(), **(doc.get("output") or {})}

    layers = doc.get("layers")
    if isinstance(layers, list):
        out["layers"] = [
            {**lay, **_layer_defaults(lay)} if isinstance(lay, dict) else lay
            for lay in layers
        ]
    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _vec(v):
    return isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(c) for c in v)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_joints(doc, p: list[str]) -> None:
    """A joint is a relation between bodies, so it validates against the layer
    ids -- which is exactly why it is a top-level array and not a layer field.

    Empty in every document C1 writes. The checks exist anyway: the point of
    shipping the slot early is that the first document to carry a real joint is
    read by code that already knows what one looks like.
    """
    joints = doc.get("joints")
    if not isinstance(joints, list):
        p.append(f"'joints' must be a list (empty is fine), got "
                 f"{type(joints).__name__}")
        return
    ids = {lay.get("id") for lay in doc.get("layers", [])
           if isinstance(lay, dict)}
    seen = set()
    for n, j in enumerate(joints):
        tag = f"joint[{n}]"
        if not isinstance(j, dict):
            p.append(f"{tag} is not an object")
            continue
        jid = j.get("id")
        if not _int(jid):
            p.append(f"{tag}: 'id' must be an integer")
        elif jid in seen:
            p.append(f"{tag}: duplicate joint id {jid}")
        else:
            seen.add(jid)
        if j.get("type") not in JOINT_TYPES:
            p.append(f"{tag}: 'type' is {j.get('type')!r}, want one of "
                     f"{', '.join(JOINT_TYPES)}")
        bodies = j.get("bodies")
        if not isinstance(bodies, list) or len(bodies) != 2:
            p.append(f"{tag}: 'bodies' must be two layer ids; null in the "
                     "second slot pins the joint to the world")
        else:
            for k, b in enumerate(bodies):
                # None is legal and meaningful: a joint to the world.
                if b is not None and b not in ids:
                    p.append(f"{tag}: bodies[{k}] is layer id {b!r}, which is "
                             "not in this scene")
        anchors = j.get("anchors")
        if not isinstance(anchors, list) or len(anchors) != 2 \
                or not all(_vec(a) for a in anchors):
            p.append(f"{tag}: 'anchors' must be two points in COMP space, one "
                     "per body")


def _validate_zones(doc, p: list[str]) -> None:
    zones = doc.get("zones")
    if not isinstance(zones, list):
        p.append(f"'zones' must be a list (empty is fine), got "
                 f"{type(zones).__name__}")
        return
    seen = set()
    for n, z in enumerate(zones):
        tag = f"zone[{n}]"
        if not isinstance(z, dict):
            p.append(f"{tag} is not an object")
            continue
        zid = z.get("id")
        if not _int(zid):
            p.append(f"{tag}: 'id' must be an integer")
        elif zid in seen:
            p.append(f"{tag}: duplicate zone id {zid}")
        else:
            seen.add(zid)
        if z.get("type") not in ZONE_TYPES:
            p.append(f"{tag}: 'type' is {z.get('type')!r}, want one of "
                     f"{', '.join(ZONE_TYPES)}")
        # `region` null means the whole comp, which is what a plain gravity-like
        # wind wants and is the reason it is not required.
        region = z.get("region")
        if region is not None and not isinstance(region, dict):
            p.append(f"{tag}: 'region' must be an object or null (null is the "
                     "whole comp)")
        frames = z.get("frames")
        if frames is not None and (not isinstance(frames, list)
                                   or len(frames) != 2
                                   or not all(_int(f) for f in frames)):
            p.append(f"{tag}: 'frames' must be two integers or null (null is "
                     "the whole simulation)")


def _validate_track(track, tag: str, arity: int, p: list[str]) -> None:
    """One kinematic input channel: [[frame, value], ...].

    Same shape the bake writes back out, deliberately -- a kinematic layer's
    input and a dynamic layer's output are the same kind of thing, and having
    two spellings of it would be a trap the first time someone feeds a bake
    back in as input.
    """
    if not isinstance(track, list):
        p.append(f"{tag} must be a list of [frame, value] pairs")
        return
    for i, entry in enumerate(track):
        if not isinstance(entry, list) or len(entry) != 2:
            p.append(f"{tag}[{i}] is not a [frame, value] pair")
            continue
        f, v = entry
        if not _int(f):
            p.append(f"{tag}[{i}]: frame {f!r} is not an integer")
        ok = _vec(v) if arity == 2 else _num(v)
        if not ok:
            p.append(f"{tag}[{i}]: value {v!r} is not "
                     + ("two finite numbers" if arity == 2
                        else "a finite number"))


def _validate_layer_slots(lay: dict, tag: str, p: list[str]) -> None:
    motion = lay.get("motion")
    if motion not in MOTION_KINDS:
        p.append(f"{tag}: 'motion' is {motion!r}, want one of "
                 f"{', '.join(MOTION_KINDS)}")
    elif bool(lay.get("static", False)) != (motion == "static"):
        # Both spellings are carried, so they can drift. This is the tripwire.
        p.append(f"{tag}: motion is {motion!r} but 'static' is "
                 f"{lay.get('static')!r} -- they must agree")

    g = lay.get("collision_group")
    if not _int(g) or g < 0:
        p.append(f"{tag}: 'collision_group' must be a non-negative integer, "
                 f"got {g!r}")
    m = lay.get("collide_with")
    if not _int(m) or not 0 <= m <= COLLIDE_WITH_ALL:
        p.append(f"{tag}: 'collide_with' must be a 16-bit mask "
                 f"(0..{COLLIDE_WITH_ALL}), got {m!r}")

    n = lay.get("outputs")
    if not _int(n) or n < 1:
        p.append(f"{tag}: 'outputs' must be at least 1 -- a layer that "
                 f"produces no output layer has nothing to bake, got {n!r}")

    kin = lay.get("input_keyframes")
    if kin is not None:
        if not isinstance(kin, dict):
            p.append(f"{tag}: 'input_keyframes' must be an object or null")
        else:
            _validate_track(kin.get("position", []),
                            f"{tag}: input_keyframes.position", 2, p)
            _validate_track(kin.get("rotation", []),
                            f"{tag}: input_keyframes.rotation", 1, p)
    elif motion == "kinematic":
        p.append(f"{tag}: motion is 'kinematic' but there are no "
                 "'input_keyframes' -- a kinematic layer is driven by its own "
                 "AE keyframes, so with none it cannot move")


def validate(doc) -> list[str]:
    """Every problem in the document, as sentences. Empty list means good.

    Accepts any version in `SCENE_SCHEMAS_READ` and judges the /2 shape,
    because `upgrade()` runs first. So a /1 document is judged on the fields it
    actually has: every Tier 0 check below is satisfied by the defaults the
    upgrade fills in, and none of them can fail for a document written before
    the slots existed.
    """
    p: list[str] = []
    if not isinstance(doc, dict):
        return ["document is not an object"]
    if doc.get("schema") not in SCENE_SCHEMAS_READ:
        p.append(f"schema is {doc.get('schema')!r}, expected "
                 f"{SCENE_SCHEMA!r} (or {SCENE_SCHEMA_V1!r}, which is "
                 "upgraded on read)")
    doc = upgrade(doc)

    out = doc.get("output")
    if not isinstance(out, dict):
        p.append("missing 'output' -- it says where the bake lands")
    else:
        if out.get("target") not in BAKE_TARGETS:
            p.append(f"output.target is {out.get('target')!r}, want one of "
                     f"{', '.join(BAKE_TARGETS)}")
        elif out["target"] == "new_comp" and not out.get("comp_name"):
            p.append("output.target is 'new_comp' but 'comp_name' is empty")

    _validate_joints(doc, p)
    _validate_zones(doc, p)

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

        _validate_layer_slots(lay, tag, p)

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

    A /1 document is upgraded on the way in, so the Scene this returns for one
    is bit-for-bit what /1 always produced -- the Tier 0 slots are shape, and
    none of them reaches the solver yet.
    """
    doc = upgrade(doc)
    problems = validate(doc)
    if problems:
        raise SceneError("scene document is not usable:\n  - "
                         + "\n  - ".join(problems))

    # The slots are shape, not behaviour, and saying so out loud is the whole
    # difference between a slot and a lie. A document that fills one gets a
    # refusal naming the phase that implements it, never a silent drop -- a
    # kinematic layer quietly simulated as dynamic would knock over exactly the
    # stack it was authored to knock over, only wrongly.
    unimplemented = []
    for lay in doc["layers"]:
        if lay.get("motion") == "kinematic":
            unimplemented.append(
                f"layer {lay['id']} ({lay['name']!r}) is kinematic; the schema "
                "carries the slot but the solver does not read it yet")
        if lay.get("outputs", 1) != 1:
            unimplemented.append(
                f"layer {lay['id']} ({lay['name']!r}) asks for "
                f"{lay['outputs']} output layers; one input is still one "
                "output until C5")
        if lay.get("collision_group", 0) != 0 \
                or lay.get("collide_with", COLLIDE_WITH_ALL) != COLLIDE_WITH_ALL:
            unimplemented.append(
                f"layer {lay['id']} ({lay['name']!r}) sets a collision group "
                "or mask; the solver collides everything with everything")
    for name, arr in (("joints", doc["joints"]), ("zones", doc["zones"])):
        if arr:
            unimplemented.append(
                f"the scene carries {len(arr)} {name[:-1]}(s); the schema has "
                "the slot but the solver does not build them yet")
    if doc["output"]["target"] != "in_place":
        unimplemented.append(
            f"output.target is {doc['output']['target']!r}; the bake is still "
            "applied in place")
    if unimplemented:
        raise SceneError("scene uses schema slots that are not implemented "
                         "yet:\n  - " + "\n  - ".join(unimplemented))

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
                     "static": bool(lay.get("static", False)),
                     # Carried through for the UI, which shows the document's
                     # own answers rather than re-deriving them.
                     "motion": lay["motion"],
                     "collision_group": lay["collision_group"],
                     "collide_with": lay["collide_with"],
                     "outputs": lay["outputs"]})

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
                   "comp_name": comp.get("name", ""),
                   "schema": doc["schema"],
                   "output": dict(doc["output"]),
                   "joints": list(doc["joints"]),
                   "zones": list(doc["zones"])}

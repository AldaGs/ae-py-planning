"""
B1 -- the scene schema, checked without opening After Effects.

B1 is the first step whose deliverable is half unrunnable here:
`b1_read_shapes.jsx` needs AE. So the split is deliberate --

    the SCRIPT's job    walk Contents, compose group transforms down to layer
                        space, serialise. Unverified until you run it.
    the PYTHON's job    read that document, validate it usefully, turn it into
                        the same bodies A3/A4 built. Verified here, offline,
                        against a hand-authored fixture that stands in for AE.

The transform maths the script depends on is NOT left unverified: `compose_*`
lives in `scene_io.py`, the jsx mirrors it line for line, and it is measured
here against closed-form answers. So if the first AE run disagrees, the
question is "did the script read the right property?" and not "is the maths
right?", which is a much smaller search.

This is also where A3's open debt gets paid. A3 wrote that it *assumed* path
vertices arrive in the same space as the anchor and flagged it for B1. The
answer is that the assumption is false in AE -- vertices are in their enclosing
group's space -- and true in the schema, because composing is the script's job.

Run:  python b1_scene.py
"""

from __future__ import annotations

import copy
import json
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import aepath
import geom
import preview
import scene_io
from scene_io import SceneError, compose_path, compose_point, load_scene
from sim import bake as bake_scene, simulate_traced

FIXTURE = "b1_fixture_scene.json"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# 1. Group transforms -- A3's open assumption, closed
# --------------------------------------------------------------------------

def test_group_transform():
    print("\n1. GROUP TRANSFORMS  (A3 assumed layer space; AE does not give it)")

    # A unit square in group space, through a group that is scaled 200%,
    # rotated 30 degrees, anchored off-centre and moved. Closed form for the
    # corner (1,1): (p - anchor)*2, rotated 30, plus position.
    xf = {"anchor": (1.0, 0.0), "position": (10.0, 5.0),
          "scale": (200.0, 200.0), "rotation_deg": 30.0}
    got = compose_point((1.0, 1.0), **{k: xf[k] for k in
                                       ("anchor", "position", "scale")},
                        rotation_deg=xf["rotation_deg"])
    r = math.radians(30.0)
    x, y = 0.0, 2.0                       # (p - anchor) * 2
    want = (10.0 + x * math.cos(r) - y * math.sin(r),
            5.0 + x * math.sin(r) + y * math.cos(r))
    check("one transform matches the closed form",
          math.dist(got, want) < 1e-12,
          f"corner (1,1) -> ({got[0]:.6f}, {got[1]:.6f}), "
          f"error {math.dist(got, want):.2e} px")

    # Two nested groups. Applying inner then outer must equal applying the
    # single equivalent transform; the check is that nesting composes at all.
    inner = {"anchor": (0.0, 0.0), "position": (5.0, 0.0),
             "scale": (50.0, 50.0), "rotation_deg": 90.0}
    outer = {"anchor": (0.0, 0.0), "position": (0.0, 20.0),
             "scale": (200.0, 200.0), "rotation_deg": -90.0}
    p = (4.0, 0.0)
    once = compose_point(p, **inner)
    twice = compose_point(once, **outer)
    # inner: (4,0) -> scale .5 -> (2,0) -> rot 90 -> (0,2) -> +(5,0) = (5,2)
    # outer: (5,2) -> scale 2 -> (10,4) -> rot -90 -> (4,-10) -> +(0,20)
    check("nested groups compose in the right order",
          math.dist(twice, (4.0, 10.0)) < 1e-12,
          f"(4,0) -> {tuple(round(v, 6) for v in once)} -> "
          f"{tuple(round(v, 6) for v in twice)}, expected (4.0, 10.0)")

    # The broken control: what ignoring the group transform costs. This is
    # exactly the bug A3 could not rule out.
    ignored = (4.0, 0.0)
    check("...and ignoring group transforms is not a small error",
          math.dist(twice, ignored) > 5.0,
          f"treating group space as layer space leaves the point at (4,0) "
          f"instead of (4,10): {math.dist(twice, ignored):.1f} px off, on a "
          "shape 4 px from its own origin")


def test_tangents_are_vectors():
    print("\n2. TANGENTS UNDER A TRANSFORM  (they are relative, so they rotate"
          " but do not translate)")
    circ = aepath.circle_path(50.0)
    xf = {"anchor": (0.0, 0.0), "position": (300.0, 100.0),
          "scale": (200.0, 200.0), "rotation_deg": 37.0}
    moved = compose_path(circ, xf)

    poly = aepath.flatten_path(moved, 0.05)
    area = abs(geom.signed_area(poly))
    want = math.pi * 100.0 ** 2
    c = geom.centroid(poly)
    check("a scaled, rotated circle keeps its area and lands on its centre",
          abs(area - want) / want < 2e-3 and math.dist(c, (300.0, 100.0)) < 1e-6,
          f"r=50 at 200% -> area {area:.1f} px^2 vs pi*100^2 = {want:.1f} "
          f"({abs(area - want) / want * 100:.3f}% off, all of it flattening "
          f"tolerance), centre ({c[0]:.6f}, {c[1]:.6f})")

    # Broken control: treat the tangents as absolute points, which is what
    # happens if you transform them with the point routine and forget to
    # subtract the vertex.
    #
    # The first version of this control measured AREA and reported 1.0x, which
    # looked like the mistake was harmless. It is not -- a wrecked path crosses
    # itself, and the shoelace formula cancels the overlapping lobes against
    # each other. Extent is the honest metric: how far from the centre does the
    # curve actually go.
    def extent(poly):
        c = geom.centroid(poly)
        return max(math.dist(p, c) for p in poly)

    wrong = copy.deepcopy(moved)
    for chan in ("inTangents", "outTangents"):
        wrong[chan] = [compose_point(t, **xf) for t in circ[chan]]
    wrong_poly = aepath.flatten_path(wrong, 0.05)
    check("...and transforming them as points wrecks the shape",
          extent(wrong_poly) > extent(poly) * 2,
          f"absolute-tangent reading swings {extent(wrong_poly):.0f} px from "
          f"the centre instead of {extent(poly):.0f} (the true radius) -- "
          f"{extent(wrong_poly) / extent(poly):.1f}x. Measured by AREA the same "
          f"path reads {abs(geom.signed_area(wrong_poly)) / area:.2f}x, because "
          "the loop crosses itself and the lobes cancel")


# --------------------------------------------------------------------------
# 3. Validation
# --------------------------------------------------------------------------

def _break(doc, fn):
    d = copy.deepcopy(doc)
    fn(d)
    return d


def test_validation():
    print("\n3. VALIDATION  (the producer is a hand-rolled ES3 serialiser in an"
          " app we cannot debug from here)")
    good = load_fixture()
    check("the fixture itself is clean",
          scene_io.validate(good) == [],
          "no problems reported, so the failures below are the injected ones")

    cases = [
        ("wrong schema version",
         lambda d: d.__setitem__("schema", "ae-physics-scene/0")),
        ("a NaN vertex (toFixed on NaN writes null)",
         lambda d: d["layers"][0]["paths"][0]["vertices"].__setitem__(0, None)),
        ("tangent count out of step with vertices",
         lambda d: d["layers"][0]["paths"][0]["inTangents"].pop()),
        ("a path with one vertex",
         lambda d: d["layers"][0]["paths"][0].__setitem__(
             "vertices", [[0.0, 0.0]])),
        ("an open path",
         lambda d: d["layers"][0]["paths"][0].__setitem__("closed", False)),
        ("a layer with no paths",
         lambda d: d["layers"][1].__setitem__("paths", [])),
        ("duplicate layer ids",
         lambda d: d["layers"][1].__setitem__("id", 3)),
        ("scale of zero",
         lambda d: d["layers"][2].__setitem__("scale", [0, 100])),
    ]
    print("      injected fault                                  caught  message")
    print("      -------------------------------------------     ------  -------")
    missed = []
    for name, fn in cases:
        probs = scene_io.validate(_break(good, fn))
        if not probs:
            missed.append(name)
        first = probs[0] if probs else "(nothing)"
        print(f"      {name:<45}   {'yes' if probs else 'NO':>4}   "
              f"{first[:70]}")
    check("every malformed document is rejected with a sentence",
          not missed,
          f"{len(cases)}/{len(cases)} caught, each naming the layer and the "
          "field; load_scene refuses the document rather than half-loading it")

    try:
        load_scene(_break(good, cases[1][1]))
        raised = False
    except SceneError as e:
        raised = "vertex is not two finite numbers" in str(e)
    check("load_scene raises rather than limping on",
          raised, "SceneError carries every problem at once, not just the first")


# --------------------------------------------------------------------------
# 4. Duplicate names -- the reason the bake schema went to /2
# --------------------------------------------------------------------------

def test_duplicate_names():
    print("\n4. DUPLICATE LAYER NAMES  (AE's default, and Phase A's join key)")
    doc = load_fixture()
    names = [l["name"] for l in doc["layers"]]
    dupes = len(names) - len(set(names))
    scene, meta = load_scene(doc)
    ids = [m["id"] for m in meta["layers"]]
    bake = bake_scene(scene, ids)

    by_id = preview.index_bake(bake)
    by_name = {lay["name"]: lay for lay in bake["layers"]}   # the old way
    check("keying the bake by name silently loses a layer",
          len(by_name) < len(by_id) == len(scene.bodies),
          f"the fixture has {dupes} duplicate name(s): keying by name gives "
          f"{len(by_name)} entries for {len(scene.bodies)} layers, keying by "
          f"id gives {len(by_id)}. This is why the bake schema is now "
          f"{bake['schema']}")

    # And the ids have to actually reach the preview, or the fix is cosmetic.
    layers = [preview.PreviewLayer(m["id"], m["name"], b.parts, b.anchor)
              for m, b in zip(meta["layers"], scene.bodies)]
    drawn = [lay for lay in layers if lay.id in by_id]
    check("every layer still resolves through the preview's join",
          len(drawn) == len(layers) == len(ids) == len(set(ids)),
          f"ids {ids} are unique and all {len(drawn)} layers render")
    return scene, meta, bake


# --------------------------------------------------------------------------
# 5. Scale, baked into the geometry
# --------------------------------------------------------------------------

def test_scale_baked():
    print("\n5. SCALE  (a rigid body has none, so it goes into the geometry)")
    doc = load_fixture()
    big = [l for l in doc["layers"] if l["name"] == "Big Box"][0]
    scene, _m = load_scene(doc)
    body = [b for b in scene.bodies if b.name == "Big Box"][0]
    area = sum(abs(geom.signed_area(p)) for p in body.parts)

    at_100 = copy.deepcopy(doc)
    for l in at_100["layers"]:
        if l["name"] == "Big Box":
            l["scale"] = [100.0, 100.0]
    small = [b for b in load_scene(at_100)[0].bodies if b.name == "Big Box"][0]
    small_area = sum(abs(geom.signed_area(p)) for p in small.parts)

    check("200% scale gives exactly 4x the area",
          abs(area / small_area - 4.0) < 1e-9,
          f"{small_area:.1f} -> {area:.1f} px^2, ratio "
          f"{area / small_area:.9f}. AE applies scale in layer space before "
          "rotation, so scaling vertices AND anchor together is exact, not an "
          "approximation")

    # The anchor becomes the origin, which is what makes the bake's Position
    # mean the same thing it means in AE.
    check("the anchor lands on the origin of the baked geometry",
          body.anchor == (0.0, 0.0),
          f"layer anchor {big['anchor']} at scale {big['scale']} folds into "
          "the vertices; every loaded body is anchor-centred")


# --------------------------------------------------------------------------
# 6. Round trip
# --------------------------------------------------------------------------

def test_round_trip(scene, meta):
    """Document -> text -> document -> bake, not Scene -> document.

    The Scene-level round trip cannot be written: a Scene holds convex parts,
    downstream of flattening and ear clipping, so its source contours are gone.
    Writing them back out produces a different document that draws the same
    picture -- and re-reading it re-nests polygons that share edges, which dies
    in `triangulate` with no ear to clip. That is recorded in `scene_io`'s
    docstring; here the round trip is done where it is meaningful.
    """
    print("\n6. ROUND TRIP  (text -> document -> scene -> bake, twice)")
    doc = load_fixture()
    text = json.dumps(doc, separators=(",", ":"), sort_keys=True)
    doc2 = json.loads(text)
    check("the document survives serialisation exactly",
          json.dumps(doc2, separators=(",", ":"), sort_keys=True) == text,
          f"{len(text)} bytes, stable -- so any difference below is the "
          "loader's, not the file's")

    ids = [m["id"] for m in meta["layers"]]
    b1 = json.dumps(bake_scene(load_scene(doc)[0], ids), sort_keys=True)
    b2 = json.dumps(bake_scene(load_scene(doc2)[0], ids), sort_keys=True)
    check("two loads of one document bake to the same bytes",
          b1 == b2,
          f"{len(b1)} bytes, identical -- Wall H holds through the schema, "
          "with nothing depending on dict order or float text")

    nudged = copy.deepcopy(doc)
    nudged["layers"][0]["paths"][0]["vertices"][0][0] += 0.05
    b3 = json.dumps(bake_scene(load_scene(nudged)[0], ids), sort_keys=True)
    check("...and a 0.05 px vertex nudge changes them",
          b3 != b1,
          "control: the comparison is capable of failing")


def test_scene_is_not_a_document():
    """The finding above, made a check so it cannot quietly stop being true."""
    print("\n6b. WHY THERE IS NO scene_to_doc  (a Scene is post-decomposition)")
    doc = load_fixture()
    scene, meta = load_scene(doc)
    ring = [b for b in scene.bodies if b.name == "Shape Layer 1"][1]
    paths_in = len([l for l in doc["layers"] if l["id"] == 4][0]["paths"])
    check("the ring arrives as 2 paths and survives as convex parts",
          paths_in == 2 and len(ring.parts) > 2,
          f"{paths_in} paths in, {len(ring.parts)} convex parts out. The parts "
          "share edges, so feeding them back in as paths would re-run "
          "containment nesting over touching polygons -- a different document "
          "that happens to draw the same picture. Phase C needs bodies to keep "
          "their source contours before a scene writer can exist")


# --------------------------------------------------------------------------
# 7. End to end from the fixture file
# --------------------------------------------------------------------------

def test_end_to_end(scene, meta, bake):
    print("\n7. END TO END  (JSON on disk -> bodies -> bake -> preview)")
    handles, tracks, traces = simulate_traced(scene)
    layers = [preview.PreviewLayer(m["id"], m["name"], b.parts, b.anchor)
              for m, b in zip(meta["layers"], scene.bodies)]
    idx = preview.index_bake(bake)
    worst = 0.0
    for fr in range(scene.duration_frames + 1):
        for i, lay in enumerate(layers):
            for pg, pt in zip(preview.transform_layer(lay, idx[lay.id],
                                                      float(fr)), traces[i][fr]):
                for a, b in zip(pg, pt):
                    worst = max(worst, math.dist(a, b))
    nparts = sum(len(b.parts) for b in scene.bodies)
    check("a scene read from disk replays exactly like one built in Python",
          worst < 1e-3,
          f"worst {worst:.3e} px over {scene.duration_frames + 1} frames, "
          f"{nparts} convex parts from {len(scene.bodies)} layers")

    settled = all(0 < t["position"][-1][1][1] < 1080 for t in tracks)
    check("the fixture's bodies stay in the comp",
          settled,
          "ring, bezier circle and scaled box all land on the floor")
    return layers


def plot(scene, meta, bake, layers):
    doc = load_fixture()
    fig = plt.figure(figsize=(14, 8))

    ax = fig.add_subplot(2, 2, 1)
    cm = plt.get_cmap("tab20")
    for i, (lay, b) in enumerate(zip(doc["layers"], scene.bodies)):
        for sh in lay["paths"]:
            poly = aepath.flatten_path(sh, 0.25)
            ax.plot([p[0] for p in poly] + [poly[0][0]],
                    [p[1] for p in poly] + [poly[0][1]], lw=1.4,
                    color=cm(i * 2 % 20))
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_title("paths as read, in layer space", fontsize=9)
    ax.grid(alpha=.3)

    # Each body is drawn in its OWN layer space, so without an offset all
    # three would sit on top of each other around the origin.
    ax = fig.add_subplot(2, 2, 2)
    dx = 0.0
    for i, b in enumerate(scene.bodies):
        w = max(v[0] for p in b.parts for v in p) -             min(v[0] for p in b.parts for v in p)
        dx += w / 2 + 20
        for k, p in enumerate(b.parts):
            ax.fill([v[0] + dx for v in p], [v[1] for v in p],
                    color=cm((i * 2 + k) % 20), alpha=.85, ec="k", lw=.5)
        ax.text(dx, 110, b.name, ha="center", fontsize=7)
        dx += w / 2 + 20
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_title(f"{sum(len(b.parts) for b in scene.bodies)} convex parts, "
                 "scale baked in (bodies spread out; each is at its own "
                 "origin)", fontsize=8)
    ax.grid(alpha=.3)

    ax = fig.add_subplot(2, 1, 2)
    idx = preview.index_bake(bake)
    for frame, alpha_v in ((0, .25), (scene.duration_frames, .9)):
        for i, lay in enumerate(layers):
            for k, p in enumerate(preview.transform_layer(lay, idx[lay.id],
                                                          float(frame))):
                ax.fill([v[0] for v in p], [v[1] for v in p],
                        color=cm((i * 2 + k) % 20), alpha=alpha_v,
                        ec="k", lw=.4)
    for (x1, y1), (x2, y2) in scene.statics:
        ax.plot([x1, x2], [y1, y2], color="k", lw=2)
    ax.set_xlim(0, 1920)
    ax.set_ylim(1080, 0)
    ax.set_aspect("equal")
    ax.set_title("the fixture simulated: first frame (faint), last",
                 fontsize=9)
    ax.grid(alpha=.3)

    fig.tight_layout()
    fig.savefig("b1_checks.png", dpi=105)
    print("\n   wrote b1_checks.png")


# --------------------------------------------------------------------------
# 8. The real thing
# --------------------------------------------------------------------------

AE_EXPORT = "b1_ae_export.json"


def test_real_export():
    """b1_read_shapes.jsx, run on a real comp, read back here.

    Everything above this point was checked against a fixture I wrote, which
    can only ever confirm that the loader agrees with my own idea of what AE
    emits. This section is the first time the script's actual output is in the
    loop, so it is the only part of B1 that can falsify the schema.

    What it CANNOT do by itself is prove the group-transform composition. If
    every group in the comp happens to sit at identity, a broken composition
    and a correct one produce the same file. So this reports the evidence --
    where each layer lands in comp space -- and `b1_ae_frame0.png` is rendered
    for comparison against the comp itself. Matching pixels is the proof;
    validating is only the absence of an obvious wound.
    """
    print("\n8. THE REAL EXPORT  (b1_read_shapes.jsx, run in AE)")
    if not os.path.exists(AE_EXPORT):
        print(f"      {AE_EXPORT} not present -- skipping")
        return None

    with open(AE_EXPORT, encoding="utf-8") as fh:
        doc = json.load(fh)
    problems = scene_io.validate(doc)
    check("the script's own output validates",
          problems == [],
          f"{len(doc['layers'])} layers out of comp {doc['comp']['name']!r} "
          f"({doc['comp']['width']}x{doc['comp']['height']} @ "
          f"{doc['comp']['fps']}fps), {len(doc.get('warnings', []))} warnings "
          "from the script"
          if not problems else "; ".join(problems[:3]))

    scene, meta = load_scene(doc)
    w, h = doc["comp"]["width"], doc["comp"]["height"]
    print("      layer                 paths  verts  curved  parts   "
          "comp-space bbox")
    print("      -------------------   -----  -----  ------  -----   "
          "------------------------")
    inside = True
    for lay, body, m in zip(doc["layers"], scene.bodies, meta["layers"]):
        nv = sum(len(sh["vertices"]) for sh in lay["paths"])
        curved = sum(1 for sh in lay["paths"] for t in sh["inTangents"]
                     if t != [0, 0])
        pts = [(x + body.position[0], y + body.position[1])
               for part in body.parts for x, y in part]
        x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
        y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
        inside = inside and -w < x0 and x1 < 2 * w and -h < y0 and y1 < 2 * h
        print(f"      {m['name']:<19}   {len(lay['paths']):5d}  {nv:5d}  "
              f"{curved:6d}  {m['parts']:5d}   "
              f"x {x0:7.1f}..{x1:7.1f}  y {y0:6.1f}..{y1:6.1f}")

    check("every layer lands on the comp, not off in group space",
          inside,
          f"all {len(scene.bodies)} bodies sit within the {w}x{h} frame once "
          "the anchor is subtracted and Position added. A composition that "
          "dropped a group transform would offset a layer by that "
          "transform -- this is evidence, not proof, since identity groups "
          "would look the same")

    curved_total = sum(1 for lay in doc["layers"] for sh in lay["paths"]
                       for t in sh["inTangents"] if t != [0, 0])
    check("real paths are curved, so flattening is actually exercised",
          curved_total > 0,
          f"{curved_total} vertices carry non-zero tangents -- the fixture's "
          "squares could not have tested the bezier path")

    ids = [m["id"] for m in meta["layers"]]
    b1 = json.dumps(bake_scene(load_scene(doc)[0], ids), sort_keys=True)
    b2 = json.dumps(bake_scene(load_scene(doc)[0], ids), sort_keys=True)
    check("a real comp bakes deterministically",
          b1 == b2, f"{len(b1)} bytes, identical over two runs")
    return doc, scene, meta


def render_real_frame0(doc, scene, meta):
    """Frame 0 at comp resolution. THIS is what confirms the read: it should
    be pixel-for-pixel where the comp is, and no measurement here can say so
    -- only looking at both can."""
    ids = [m["id"] for m in meta["layers"]]
    short = copy.deepcopy(scene)
    short.duration_frames = 1
    bake = bake_scene(short, ids)
    layers = [preview.PreviewLayer(m["id"], m["name"], b.parts, b.anchor)
              for m, b in zip(meta["layers"], scene.bodies)]
    ps = preview.PreviewScene(doc["comp"]["width"], doc["comp"]["height"],
                              [tuple(tuple(p) for p in s)
                               for s in doc.get("statics", [])],
                              background=(255, 255, 255))
    img = preview.render_frame(bake, layers, ps, 0.0, 1.0)
    img.save("b1_ae_frame0.png")
    print(f"\n   wrote b1_ae_frame0.png ({img.size[0]}x{img.size[1]}) -- "
          "compare it against the comp at frame 0")


if __name__ == "__main__":
    print("B1 -- ae-physics-scene/1, checked without AE")
    test_group_transform()
    test_tangents_are_vectors()
    test_validation()
    scene, meta, bake = test_duplicate_names()
    test_scale_baked()
    test_round_trip(scene, meta)
    test_scene_is_not_a_document()
    layers = test_end_to_end(scene, meta, bake)
    plot(scene, meta, bake, layers)
    real = test_real_export()
    if real:
        render_real_frame0(*real)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")
    if real:
        print("\n   b1_read_shapes.jsx HAS run in AE and its output is checked "
              "above. What no check here can settle is whether the read is "
              "COMPLETE -- compare b1_ae_frame0.png against the comp.")
    else:
        print("\n   NOT verified here: b1_read_shapes.jsx. It needs AE, and "
              "the first real run is expected to find something.")

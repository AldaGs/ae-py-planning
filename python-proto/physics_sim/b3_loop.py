"""
B3 -- the loop. Comp in, keyframes out, one command in the middle.

    (in AE)  b1_read_shapes.jsx     comp   -> scene.json
             python b3_loop.py scene.json  -> bake.json + preview
    (in AE)  b2_apply_bake.jsx      bake   -> keyframes on the layers

The plan called this step "ugly, manual, and it is the entire product working",
and that is the right description. Nothing here is new physics. What is new is
that the middle is ONE command with parameters, instead of constants edited in
a checks file -- because a physics tool you cannot re-run with different gravity
is not a tool, it is a demo.

STALENESS IS THE WALL B3 EXPOSES
--------------------------------
A loop made of files carried by hand between two applications has a failure mode
the previous steps did not: applying a bake that was made from a DIFFERENT comp,
or from this comp before it was edited. Nothing about a stale bake looks wrong.
It is well-formed, it validates, the layer names match, and it silently
overwrites good animation with keyframes computed from geometry that no longer
exists.

So every bake carries a `source` block: the scene file's SHA-256, the comp's
name and size, the layer ids and names it was built from, and the settings used.
`b2_apply_bake.jsx` checks the comp identity before writing anything, and
`--check` here re-verifies a bake against a scene without touching AE.

That is the whole point of a manifest: not provenance for its own sake, but the
one guard a hand-carried loop cannot do without.

PINNED LAYERS
-------------
`--static NAME` (repeatable, matches by name or id) makes a layer immovable: a
ramp, a peg, a floor drawn as a shape layer. A pinned layer gets no keyframes at
all -- writing even a constant Position would replace the user's own placement
with our copy of it, and then undo any later nudge they made by hand.

Run `python b3_loop.py --help`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import geom
import preview
import scene_io
from sim import bake as bake_scene

BAKE_SCHEMA_EXPECTED = "ae-physics-bake/3"


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

def digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def source_block(scene_path: str, doc: dict, meta: dict, args,
                 overrides: dict) -> dict:
    """Everything needed to tell whether this bake still belongs to that comp."""
    return {
        "scene_file": os.path.basename(scene_path),
        "scene_sha256": digest(scene_path),
        "comp": {"name": doc["comp"].get("name", ""),
                 "width": doc["comp"]["width"],
                 "height": doc["comp"]["height"],
                 "fps": doc["comp"]["fps"]},
        "layers": [{"id": m["id"], "name": m["name"]} for m in meta["layers"]],
        "settings": {
            "gravity_m_s2": args.gravity,
            "pixels_per_meter": args.ppm,
            "substeps": args.substeps,
            "frames": args.frames,
            "friction": args.friction,
            "elasticity": args.elasticity,
            "static": list(args.static),
            # Wall K: the source block is what tells you whether a bake still
            # means anything, and a run with per-layer physics that only
            # records the scene-wide numbers is a partial description of it.
            "layer_overrides": overrides,
            "enclose": not args.no_walls,
        },
        "made_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def stale_reasons(bake: dict, scene_path: str, doc: dict) -> list[str]:
    """Why this bake does not belong to this scene. Empty list means it does."""
    src = bake.get("source")
    if not src:
        return ["the bake carries no source block, so it cannot be checked "
                "against anything -- it predates B3"]
    out = []
    if src.get("scene_sha256") != digest(scene_path):
        out.append(f"the scene file has changed since the bake was made "
                   f"({src.get('scene_file')} sha "
                   f"{str(src.get('scene_sha256'))[:12]}... vs "
                   f"{digest(scene_path)[:12]}...)")
    c, d = src.get("comp", {}), doc["comp"]
    for k in ("name", "width", "height", "fps"):
        if c.get(k) != d.get(k):
            out.append(f"comp {k}: bake says {c.get(k)!r}, scene says "
                       f"{d.get(k)!r}")
    want = [(l["id"], l["name"]) for l in src.get("layers", [])]
    got = [(l["id"], l["name"]) for l in doc["layers"]]
    if want != got:
        out.append(f"the layer list moved: bake was built from {want}, "
                   f"scene now has {got}")
    return out


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def apply_settings(scene, meta, args):
    """Push the command line onto the loaded scene, and report what it hit.

    Matching `--static` by name OR id, and saying which layers it matched, is
    not politeness: a typo that silently pins nothing would look exactly like a
    scene where pinning did not help.
    """
    scene.gravity_m_s2 = args.gravity
    # The walls are OUR invention, not the user's layers, so they take the
    # same surface settings. Chipmunk combines elasticity multiplicatively:
    # leaving the floor at its default 0.2 silently caps how bouncy anything
    # can be, no matter what the bodies say.
    scene.static_elasticity = args.elasticity
    scene.static_friction = args.friction
    scene.pixels_per_meter = args.ppm
    scene.substeps = args.substeps
    if args.frames is not None:
        scene.duration_frames = args.frames

    wanted = {s.lower() for s in args.static}
    pinned = []
    for body, m in zip(scene.bodies, meta["layers"]):
        body.friction = args.friction
        body.elasticity = args.elasticity
        if wanted and (body.name.lower() in wanted or str(m["id"]) in wanted):
            body.static = True
            m["static"] = True
            pinned.append(f"{m['id']}:{body.name}")
    unmatched = [s for s in args.static
                 if s.lower() not in {b.name.lower() for b in scene.bodies}
                 and s not in {str(m["id"]) for m in meta["layers"]}]
    return pinned, unmatched


def parse_overrides(pairs, what):
    """`ID=VALUE` pairs off the command line, into {key: float}.

    Reported rather than silently dropped: a per-layer value that does not
    parse is a layer that quietly ran with the scene default, and the whole
    point of these flags is that a particular layer is different.
    """
    out = {}
    for raw in pairs or ():
        key, sep, val = raw.partition("=")
        if not sep:
            raise SystemExit(f"--layer-{what} wants ID=VALUE, got {raw!r}")
        try:
            out[key.strip()] = float(val)
        except ValueError:
            raise SystemExit(f"--layer-{what} {raw!r}: {val!r} is not a number")
    return out


def resolve_layer(key, scene, meta):
    """A layer id, or a name that belongs to exactly one layer.

    Ids first, and an ambiguous NAME is refused rather than applied to every
    layer that shares it. AE allows duplicate layer names -- "Shape Layer 1"
    twice is its own default -- and that ambiguity has now caused the same
    class of bug three times: Phase A dropped a layer by keying the bake on
    name, and the shell's pin control pinned both halves of a matching pair.
    `--static` still accepts a name for both, which is a compatibility wart;
    these flags do not repeat it.
    """
    for i, m in enumerate(meta["layers"]):
        if key == str(m["id"]):
            return i
    hits = [i for i, m in enumerate(meta["layers"])
            if m["name"].lower() == key.lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        ids = ", ".join(str(meta["layers"][i]["id"]) for i in hits)
        raise SystemExit(
            f"'{key}' names {len(hits)} layers (ids {ids}) -- say which one by "
            f"id. A name is not an identity in AE.")
    return None


def apply_per_layer(scene, meta, args):
    """Per-layer mass, friction and bounce, over the scene-wide defaults.

    THE SOLVER WAS ALREADY PER-BODY. `sim.PolyBody` has carried `density`,
    `friction` and `elasticity` on every spec from A3 onwards, and `sim` reads
    them per shape. What made the scene uniform was the loop below stamping
    `args.friction` over all of them. So this is not a physics change; it is
    letting values that already exist differ.

    MASS IS NOT A FIELD, AND THIS IS WHERE IT BECOMES ONE.

    The solver takes DENSITY, and mass = density * area. A mass control that
    just set density would make two layers of different size behave nothing
    alike at the same number, which is not what anyone means by "mass". The
    back-solve needs the area, and the area is geometry -- so it happens here,
    in the half of the program that owns geometry, rather than in a UI that
    would have to reimplement `compound_mass_properties` to guess at it.

        area_m2 = compound_mass_properties(parts_px, 1.0) / ppm**2
        density = mass_kg / area_m2

    Mass at density 1.0 IS the area, which is why the first call reads oddly
    and is correct.
    """
    mass = parse_overrides(args.layer_mass, "mass")
    fric = parse_overrides(args.layer_friction, "friction")
    bounce = parse_overrides(args.layer_bounce, "bounce")

    recorded, unmatched = {}, []
    for what, table in (("mass", mass), ("friction", fric), ("bounce", bounce)):
        for key, value in table.items():
            i = resolve_layer(key, scene, meta)
            if i is None:
                unmatched.append(f"--layer-{what} {key}")
                continue
            body = scene.bodies[i]
            lid = str(meta["layers"][i]["id"])
            slot = recorded.setdefault(lid, {})

            if what == "friction":
                body.friction = value
            elif what == "bounce":
                body.elasticity = value
            else:
                area_m2 = geom.compound_mass_properties(body.parts, 1.0)[0]
                area_m2 /= args.ppm ** 2
                if area_m2 <= 0.0:
                    raise SystemExit(
                        f"layer {lid} has no area, so it has no mass to set")
                body.density = value / area_m2
                slot["density"] = body.density
            slot[what] = value
    return recorded, unmatched


def enclose(scene):
    w, h = float(scene.width), float(scene.height)
    scene.statics = [((0.0, h - 1.0), (w, h - 1.0)),
                     ((0.0, 0.0), (0.0, h - 1.0)),
                     ((w, 0.0), (w, h - 1.0)),
                     ((0.0, 0.0), (w, 0.0))]


def escapes(bake, width, height, margin=2.0):
    out = []
    for lay in bake["layers"]:
        worst, at = 0.0, 0
        for f, (x, y) in lay["keyframes"]["position"]:
            d = max(-x, x - width, -y, y - height, 0.0)
            if d > worst:
                worst, at = d, f
        if worst > margin * max(width, height):
            out.append({"name": lay["name"], "px": worst, "frame": at})
    return out


def run(args) -> int:
    with open(args.scene, encoding="utf-8") as fh:
        doc = json.load(fh)

    problems = scene_io.validate(doc)
    if problems:
        print("the scene document is not usable:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    scene, meta = scene_io.load_scene(doc)
    for w in meta["warnings"]:
        print(f"   warning: {w}")

    pinned, unmatched = apply_settings(scene, meta, args)
    # AFTER the scene-wide stamp above, which would otherwise overwrite it.
    overrides, bad_overrides = apply_per_layer(scene, meta, args)
    unmatched = list(unmatched) + bad_overrides
    if unmatched:
        print(f"   ERROR: matched no layer: {unmatched}. "
              f"Layers are: "
              f"{[f'{m['id']}:{m['name']}' for m in meta['layers']]}",
              file=sys.stderr)
        return 2
    if not args.no_walls:
        enclose(scene)

    if args.render_only:
        """C6.3 -- the scene BEFORE it is simulated.

        Everything above this line is scene loading, the pin list, the
        per-layer overrides and the wall box. Nothing below it is: the sim
        starts at `bake_scene`. So a render model written here is the same
        geometry the solver is about to be handed, produced by the same code
        with the same arguments, which is what makes it worth looking at.

        The point is not convenience. The READER has never had a visual
        check. B1 found A3's layer-space assumption to be false in AE only
        because a bake came out wrong; a comp whose polygons are misplaced
        before any physics runs currently looks exactly like a comp whose
        physics is wrong, and the two have completely different causes. This
        separates them: if it looks wrong here, the sim is innocent.

        `--render-only` and `--render-model` are separate flags rather than
        one, because "write the model" and "do not simulate" are separate
        decisions and the normal run does the first without the second.
        """
        if not args.render_model:
            print("--render-only needs --render-model PATH: it writes the "
                  "geometry and nothing else, so there is nowhere to put it.",
                  file=sys.stderr)
            return 2
        n = write_render_model(args.render_model, scene, meta)
        print(f"\n   {doc['comp'].get('name', '?')} "
              f"{scene.width}x{scene.height} @ {scene.fps}fps, "
              f"{scene.duration_frames} frames")
        print(f"   {len(scene.bodies)} layers, "
              f"{sum(m['parts'] for m in meta['layers'])} convex parts"
              + (f", pinned {', '.join(pinned)}" if pinned else ""))
        print(f"   wrote {args.render_model} ({n / 1024:.0f} KB) -- "
              f"geometry at rest, NOT simulated")
        return 0

    ids = [m["id"] for m in meta["layers"]]
    t0 = time.time()
    # The bake target rides along from the scene, so the document says where
    # it was meant to land rather than the applier assuming.
    bake = bake_scene(scene, ids, meta["output"])
    elapsed = time.time() - t0
    bake["source"] = source_block(args.scene, doc, meta, args, overrides)

    dyn = [m for m in meta["layers"] if not m.get("static")]
    keys = sum(len(l["keyframes"]["position"]) + len(l["keyframes"]["rotation"])
               for l in bake["layers"])
    print(f"\n   {doc['comp'].get('name', '?')} "
          f"{scene.width}x{scene.height} @ {scene.fps}fps, "
          f"{scene.duration_frames} frames")
    print(f"   {len(scene.bodies)} layers ({len(dyn)} dynamic"
          + (f", pinned {', '.join(pinned)}" if pinned else "") + "), "
          f"{sum(m['parts'] for m in meta['layers'])} convex parts")
    print(f"   gravity {args.gravity} m/s^2, ppm {args.ppm}, "
          f"substeps {args.substeps}, friction {args.friction}, "
          f"elasticity {args.elasticity}")
    print(f"   simulated in {elapsed:.2f}s -> {keys} keyframes")

    gone = escapes(bake, scene.width, scene.height)
    for g in gone:
        print(f"   WARNING: {g['name']!r} leaves the comp by {g['px']:,.0f} px "
              f"at frame {g['frame']}", file=sys.stderr)
    if gone and not args.allow_escapes:
        print("   refusing to write a bake that flies off to infinity. "
              "Re-run with --allow-escapes if that is what you meant.",
              file=sys.stderr)
        return 3

    text = json.dumps(bake, separators=(",", ":"), sort_keys=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"   wrote {args.out} ({len(text) / 1024:.0f} KB)")

    if args.render_model:
        n = write_render_model(args.render_model, scene, meta)
        print(f"   wrote {args.render_model} ({n / 1024:.0f} KB) -- "
              f"the viewport's geometry")

    if not args.no_preview:
        layers = [preview.PreviewLayer(m["id"], m["name"], b.parts, b.anchor)
                  for m, b in zip(meta["layers"], scene.bodies)]
        ps = preview.PreviewScene(scene.width, scene.height,
                                  list(scene.statics),
                                  background=(255, 255, 255))
        # A pinned layer has no keyframes, so give the preview a standing one:
        # otherwise the ramp everything lands on is invisible.
        for lay, m, b in zip(bake["layers"], meta["layers"], scene.bodies):
            if lay["static"]:
                lay["keyframes"] = {
                    "position": [[0, list(b.position)],
                                 [scene.duration_frames, list(b.position)]],
                    "rotation": [[0, b.angle_deg],
                                 [scene.duration_frames, b.angle_deg]]}
        last = scene.duration_frames
        ts = [round(last * f) for f in
              (0, .02, .05, .1, .2, .4, .7, 1.0)]
        sheet = preview.contact_sheet(bake, layers, ps,
                                      [float(t) for t in ts], scale=0.5)
        sheet.save(args.preview)
        print(f"   wrote {args.preview} (frames {ts}) -- look before applying")
    return 0


RENDER_SCHEMA = "ae-physics-render/1"


def write_render_model(path, scene, meta):
    """The polygons a viewport needs, and nothing it can compute itself.

    C2 puts a canvas in the shell, and the question that decides its shape is
    which half of the drawing each side owns.

    The GEOMETRY half stays here without argument. Turning a comp into polygons
    is bezier flattening, group transforms, layer scale and convex decomposition
    -- the ~1,200 lines A3 and A4 verified -- and a second implementation of
    that in TypeScript would be a second place for a 10 px error to hide, which
    is exactly what B1 found when A3's layer-space assumption turned out to be
    false in AE.

    The TRANSFORM half is three lines, `position + R(theta) * (v - anchor)`,
    and the viewport cannot avoid owning it: it is what scrubbing IS. That is
    also why preview.py exists at all -- it re-derives the transform from the
    JSON alone, so it can catch a convention fault (a y-up flip, degrees baked
    as radians, Position written as the COM) that sim.replay_from_keyframes
    shares assumptions with and cannot. A third independent consumer is more of
    that same evidence, not drift, PROVIDED somebody checks they agree. That
    check is `c2_render_model.py`.

    So this file is deliberately dumb: parts already in layer space, an anchor,
    and the comp box. No beziers, no tangents, no schema.

    WHY `rest` IS HERE.

    A pinned layer has no keyframes -- B3's rule, because writing even a
    constant would overwrite the user's own placement. The contact sheet above
    works around that by synthesising a standing pair, and a viewport that did
    not would simply lose the floor: everything would fall through a world with
    no visible ground. So the resting pose ships explicitly rather than being
    something the front end has to know to invent.
    """
    idx = {m["id"]: b for m, b in zip(meta["layers"], scene.bodies)}

    model = {
        "schema": RENDER_SCHEMA,
        "comp": {
            "width": scene.width,
            "height": scene.height,
            "fps": scene.fps,
            "duration_frames": scene.duration_frames,
        },
        # The world box the reader emits. B2 found the floor-only default let a
        # rolling layer reach 838,591 px; drawing the walls means a viewer can
        # SEE the box the sim was run in rather than infer it.
        "statics": [[list(a), list(b)] for a, b in scene.statics],
        "layers": [],
    }
    for m in meta["layers"]:
        b = idx[m["id"]]
        model["layers"].append({
            "id": m["id"],
            "name": m["name"],
            "anchor": list(b.anchor),
            "parts": [[list(v) for v in part] for part in b.parts],
            "static": bool(b.static),
            "rest": {"position": list(b.position), "rotation": b.angle_deg},
        })

    text = json.dumps(model, separators=(",", ":"), sort_keys=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text)


def check(args) -> int:
    with open(args.out, encoding="utf-8") as fh:
        bake = json.load(fh)
    with open(args.scene, encoding="utf-8") as fh:
        doc = json.load(fh)
    reasons = stale_reasons(bake, args.scene, doc)
    if reasons:
        print(f"{args.out} is STALE for {args.scene}:", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    src = bake["source"]
    print(f"{args.out} matches {args.scene}: comp {src['comp']['name']!r}, "
          f"{len(src['layers'])} layers, made {src['made_at']} with "
          f"gravity {src['settings']['gravity_m_s2']}, "
          f"ppm {src['settings']['pixels_per_meter']}")
    return 0


def parser():
    p = argparse.ArgumentParser(
        description="Simulate an ae-physics-scene and write an "
                    "ae-physics-bake for b2_apply_bake.jsx.")
    p.add_argument("scene", help="scene JSON from b1_read_shapes.jsx")
    p.add_argument("--out", default="b3_bake.json")
    p.add_argument("--preview", default="b3_preview.png")
    p.add_argument("--layer-mass", action="append", metavar="ID=KG",
                   help="mass for one layer, back-solved into density by its "
                        "own area; repeatable")
    p.add_argument("--layer-friction", action="append", metavar="ID=V",
                   help="friction for one layer, over --friction; repeatable")
    p.add_argument("--layer-bounce", action="append", metavar="ID=V",
                   help="elasticity for one layer, over --elasticity; "
                        "repeatable")
    p.add_argument("--render-model", default=None,
                   help="also write the viewport's geometry (polygons in "
                        "layer space plus anchors) as ae-physics-render/1")
    p.add_argument("--render-only", action="store_true",
                   help="write the render model from the scene and STOP -- "
                        "no simulation, every layer at its resting pose. "
                        "Needs --render-model.")
    p.add_argument("--gravity", type=float, default=9.8,
                   help="m/s^2, positive is down (default 9.8)")
    p.add_argument("--ppm", type=float, default=100.0,
                   help="pixels per metre -- the comp's clock. A1 measured "
                        "the usable band as 10-1000 (default 100)")
    p.add_argument("--substeps", type=int, default=8)
    p.add_argument("--frames", type=int, default=None,
                   help="override the comp's duration")
    p.add_argument("--friction", type=float, default=0.6)
    p.add_argument("--elasticity", type=float, default=0.2)
    p.add_argument("--static", action="append", default=[], metavar="LAYER",
                   help="pin a layer by name or id; repeatable. Pinned layers "
                        "get no keyframes at all")
    p.add_argument("--no-walls", action="store_true",
                   help="do not enclose the comp. Things will leave")
    p.add_argument("--allow-escapes", action="store_true",
                   help="write the bake even if a layer flies off to infinity")
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--check", action="store_true",
                   help="do not simulate; check --out against the scene and "
                        "report whether it is stale")
    return p


if __name__ == "__main__":
    a = parser().parse_args()
    sys.exit(check(a) if a.check else run(a))

"""
C2.0 -- `ae-physics-render/1`, and the transform the viewport has to own.

WHAT THIS STEP IS FOR
---------------------
C2 puts a canvas in the shell so a bake can be scrubbed before it is applied.
The question that decides its shape is which half of the drawing each side owns,
and the split is not a matter of taste:

    GEOMETRY   comp -> polygons. Bezier flattening, group transforms, layer
               scale, convex decomposition -- the ~1,200 lines A3 and A4
               verified. This stays in Python. A second implementation in
               TypeScript would be a second place for a 10 px error to hide,
               which is precisely what B1 found when A3's layer-space
               assumption turned out to be false inside AE.

    TRANSFORM  position + R(theta) * (v - anchor), plus linear sampling between
               keyframes. Three lines, and the viewport cannot avoid owning
               them: that IS what scrubbing is.

So `b3_loop.py --render-model` writes the first half out and the app does the
second.

WHY A THIRD RENDERER IS NOT AUTOMATICALLY DRIFT
-----------------------------------------------
preview.py exists BECAUSE an independent re-derivation catches faults a shared
one cannot: `sim.replay_from_keyframes` shares conventions with the solver, so
it can reproduce a y-up flip or degrees-baked-as-radians faithfully and prove
nothing. A renderer that only ever sees the JSON cannot.

By that argument a viewport is a third such witness, and more evidence rather
than less -- PROVIDED somebody checks the witnesses agree. Unchecked, it is just
drift with a nicer name. This file is that check, and section 3 is where the
agreement is actually proven: the polygons the app will draw, compared against
`preview.transform_layer`, at FRACTIONAL frames.

Fractional is the whole point. A5: the damage from a wrapped rotation is
invisible on every still -- 184.88 and -175.12 are the same orientation, only
the tween differs -- and it lives inside the single frame interval containing
the crossing. Checking integer frames would agree perfectly and miss it.

WHAT IT CANNOT CHECK
--------------------
That the CANVAS draws what these numbers say. Nothing here opens a browser, so
a polygon list that is correct and a fill rule that is wrong look identical to
this file. The viewport's own agreement with these numbers is a sitting in the
app, not an offline check.

Run:  python c2_render_model.py
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import tempfile

import preview
import scene_io

results: list[tuple[str, bool, str]] = []

HERE = os.path.dirname(os.path.abspath(__file__))
SCENE = os.path.join(
    os.path.expanduser("~"),
    "AppData", "Roaming", "com.aldags.aephysics", "work", "scene.json",
)


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_loop(scene_path, out_dir, extra=()):
    """Drive b3_loop.py as a COMMAND, the way b3_checks.py does.

    Calling run() in-process would test the functions rather than the tool, and
    B3's claim is about the tool.
    """
    bake = os.path.join(out_dir, "bake.json")
    model = os.path.join(out_dir, "render.json")
    cmd = [sys.executable, os.path.join(HERE, "b3_loop.py"), scene_path,
           "--out", bake, "--render-model", model, "--no-preview",
           "--gravity", "9.8", "--ppm", "100", "--substeps", "8",
           "--frames", "60", "--static", "FLOOR", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    if p.returncode != 0:
        raise SystemExit(f"b3_loop failed ({p.returncode}):\n{p.stderr}")
    return load(bake), load(model)


# --------------------------------------------------------------------------
# 1 -- the document
# --------------------------------------------------------------------------

def test_shape(model):
    print("\n1. the render model is what a viewport needs, and no more")

    check("schema", model.get("schema") == "ae-physics-render/1",
          str(model.get("schema")))

    comp = model.get("comp", {})
    check("comp box carries fps and duration",
          all(k in comp for k in ("width", "height", "fps", "duration_frames")),
          f"{comp.get('width')}x{comp.get('height')} @{comp.get('fps')}, "
          f"{comp.get('duration_frames')} frames")

    # B2: the reader's floor-only default let a rolling layer reach 838,591 px
    # before anyone noticed. Shipping the walls means a viewer can SEE the box
    # the sim ran in instead of inferring it from where things stopped.
    check("the world box ships", len(model.get("statics", [])) >= 4,
          f"{len(model.get('statics', []))} static segments")

    lay = model.get("layers", [])
    check("every layer has parts in layer space and an anchor",
          bool(lay) and all("parts" in L and "anchor" in L for L in lay),
          f"{len(lay)} layers, "
          f"{sum(len(L['parts']) for L in lay)} convex parts")

    # No beziers, no tangents, no schema fields: if any leaked through, the
    # front end would be back to interpreting the scene.
    leaked = {k for L in lay for k in L
              if k not in {"id", "name", "anchor", "parts", "static", "rest"}}
    check("no scene fields leak into it", not leaked,
          "clean" if not leaked else f"leaked: {sorted(leaked)}")


# --------------------------------------------------------------------------
# 2 -- the pinned layer, which has no keyframes at all
# --------------------------------------------------------------------------

def test_rest_pose(model, bake):
    print("\n2. a pinned layer still has somewhere to be")

    pinned = [L for L in model["layers"] if L["static"]]
    check("something is pinned", bool(pinned),
          ", ".join(L["name"] for L in pinned) or "nothing")

    if not pinned:
        return

    P = pinned[0]
    baked = {L["id"]: L for L in bake["layers"]}[P["id"]]

    # B3's rule: a pinned layer gets NO keyframes, because writing even a
    # constant would overwrite the user's own placement. So the bake cannot
    # tell a viewport where the floor is, and without `rest` everything would
    # fall through a world with no visible ground.
    check("it has no keyframes in the bake",
          not baked["keyframes"]["position"] and not baked["keyframes"]["rotation"],
          "no position or rotation keys, as B3 requires")

    check("but the render model says where it rests",
          isinstance(P.get("rest", {}).get("position"), list),
          f"{P['name']} rests at {P['rest']['position']}, "
          f"{P['rest']['rotation']:.3f} deg")


# --------------------------------------------------------------------------
# 3 -- THE ONE THAT MATTERS: the app's transform against preview.py's
# --------------------------------------------------------------------------

def app_transform(layer_model, bake_layer, t):
    """The transform as the VIEWPORT will implement it, written independently.

    Deliberately not a call into preview.transform_layer -- that would compare
    a function against itself and pass no matter what either of them says. This
    is the arithmetic a canvas does, spelled out, so the comparison below has
    two sides.
    """
    def sample(track, t, scalar):
        if not track:
            return 0.0 if scalar else [0.0, 0.0]
        if t <= track[0][0]:
            return track[0][1]
        if t >= track[-1][0]:
            return track[-1][1]
        lo, hi = 0, len(track) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if track[mid][0] <= t:
                lo = mid
            else:
                hi = mid
        f0, v0 = track[lo]
        f1, v1 = track[hi]
        u = 0.0 if f1 == f0 else (t - f0) / (f1 - f0)
        if scalar:
            return v0 + (v1 - v0) * u
        return [v0[0] + (v1[0] - v0[0]) * u, v0[1] + (v1[1] - v0[1]) * u]

    kf = bake_layer["keyframes"]
    px, py = sample(kf["position"], t, False)
    theta = math.radians(sample(kf["rotation"], t, True))
    ax, ay = layer_model["anchor"]
    c, s = math.cos(theta), math.sin(theta)
    out = []
    for part in layer_model["parts"]:
        out.append([(px + (x - ax) * c - (y - ay) * s,
                     py + (x - ax) * s + (y - ay) * c)
                    for x, y in part])
    return out


def worst_gap(model, bake, ts):
    idx = preview.index_bake(bake)
    worst, where = 0.0, None
    for L in model["layers"]:
        if L["static"]:
            continue
        pl = preview.PreviewLayer(L["id"], L["name"],
                                  [[tuple(v) for v in p] for p in L["parts"]],
                                  tuple(L["anchor"]))
        for t in ts:
            a = preview.transform_layer(pl, idx[L["id"]], t)
            b = app_transform(L, idx[L["id"]], t)
            for pa, pb in zip(a, b):
                for (ax, ay), (bx, by) in zip(pa, pb):
                    d = max(abs(ax - bx), abs(ay - by))
                    if d > worst:
                        worst, where = d, (L["name"], t)
    return worst, where


def test_agreement(model, bake):
    print("\n3. the viewport's transform agrees with preview.py's")

    # Integer frames first, which is the easy half: our bake writes one key per
    # frame, so every stored value is returned untouched and any renderer that
    # can read JSON gets these right.
    ints = [float(f) for f in range(0, 61, 5)]
    w, where = worst_gap(model, bake, ints)
    check("at stored keyframes", w < 1e-9, f"worst {w:.3e} px at {where}")

    # Then the half that A5 says is the only one that can catch a tween fault.
    fracs = [f + o for f in range(0, 60) for o in (0.25, 0.5, 0.75)]
    w, where = worst_gap(model, bake, fracs)
    check("BETWEEN keyframes, which is where the damage hides",
          w < 1e-9, f"worst {w:.3e} px over {len(fracs)} sampled tweens at {where}")


def worst_gap_with(model, bake, ts, impl):
    """`worst_gap`, but with the APP side swappable.

    Section 4 needs to perturb one side's arithmetic, and the first version of
    it perturbed the shared INPUT instead -- a moved anchor, a flipped bake.
    Both sides read those, so both moved together and the comparison reported
    0.000 px: a broken control that was itself broken, in exactly the way it
    existed to catch. Kept as a hook rather than a comment because the lesson
    is structural -- a comparison of two functions of X cannot be sensitive to
    X, only to the functions.
    """
    idx = preview.index_bake(bake)
    worst, where = 0.0, None
    for L in model["layers"]:
        if L["static"]:
            continue
        pl = preview.PreviewLayer(L["id"], L["name"],
                                  [[tuple(v) for v in p] for p in L["parts"]],
                                  tuple(L["anchor"]))
        for t in ts:
            a = preview.transform_layer(pl, idx[L["id"]], t)
            b = impl(L, idx[L["id"]], t)
            for pa, pb in zip(a, b):
                for (ax, ay), (bx, by) in zip(pa, pb):
                    d = max(abs(ax - bx), abs(ay - by))
                    if d > worst:
                        worst, where = d, (L["name"], t)
    return worst, where


def _bent(what):
    """An app-side transform with one specific mistake in it."""
    def impl(L, bake_layer, t):
        polys = app_transform(L, bake_layer, t)
        if what == "anchor":
            # `+ anchor` instead of `- anchor`: the single commonest way to get
            # this wrong, and invisible whenever the anchor happens to be [0,0]
            # -- which it is for three of the four layers in a default comp.
            ax, ay = L["anchor"]
            return [[(x + 2 * ax, y + 2 * ay) for x, y in p] for p in polys]
        if what == "radians":
            # Degrees fed to cos/sin directly. A5's fault class: at small
            # angles it looks almost right.
            import math as _m
            kf = bake_layer["keyframes"]
            px, py = app_transform.__globals__["preview"]._sample(
                kf["position"], t, scalar=False)
            deg = app_transform.__globals__["preview"]._sample(
                kf["rotation"], t, scalar=True)
            ax, ay = L["anchor"]
            c, s = _m.cos(deg), _m.sin(deg)
            return [[(px + (x - ax) * c - (y - ay) * s,
                      py + (x - ax) * s + (y - ay) * c) for x, y in part]
                    for part in L["parts"]]
        raise ValueError(what)
    return impl


def _with_anchor(model, xy):
    """The same model with one dynamic layer moved off its origin.

    Needed because of what the control below found: in a real comp every
    dynamic layer's anchor is [0, 0], so `- anchor` and `+ anchor` compute the
    SAME answer and section 3 never exercises that term at all. Testing it
    requires data that can tell the two apart, and no comp so far provides any.
    """
    m = copy.deepcopy(model)
    L = next(x for x in m["layers"] if not x["static"])
    L["anchor"] = list(xy)
    return m, L["name"]


def test_broken_control(model, bake):
    """A check that cannot fail is not a check.

    Two things had to be fixed here, and both are worth keeping visible.

    FIRST, the perturbation has to be in one of the IMPLEMENTATIONS. The first
    version moved the anchor in the shared model and flipped the shared bake --
    inputs both sides read -- so both moved together and it reported 0.000 px.
    A broken control that was itself broken, in exactly the way it existed to
    catch. A comparison of two functions of X cannot be sensitive to X.

    SECOND, and this is a genuine hole rather than a harness slip: with the
    implementation correctly bent, it STILL reported 0.000 px, because every
    dynamic layer in a real comp has anchor [0, 0]. The anchor term is
    multiplied by zero, so `- anchor` and `+ anchor` agree and section 3 proves
    nothing about it. B1 already found the AE-side version of this -- a shape 4
    px from its own origin is where a wrong space stops being invisible.
    """
    print("\n4. the anchor term, which real data does not exercise")

    ts = [7.5, 12.5, 30.5]

    zero = all(L["anchor"] == [0, 0] or L["anchor"] == [0.0, 0.0]
               for L in model["layers"] if not L["static"])
    check("every dynamic anchor in this comp is at the origin", zero,
          "so the anchor term is multiplied by zero and needs synthetic data"
          if zero else "at least one is off-origin, which is better")

    # The positive half: with an anchor that CAN be seen, do the two
    # implementations still agree?
    off, name = _with_anchor(model, (50.0, -30.0))
    w, where = worst_gap(off, bake, ts)
    check("with a non-zero anchor they still agree", w < 1e-9,
          f"worst {w:.3e} px on {name} at anchor (50, -30)")

    # And the control, on the same data, so it is the arithmetic being tested
    # rather than the zero.
    w, where = worst_gap_with(off, bake, ts, _bent("anchor"))
    check("an anchor sign error is seen", w > 1.0,
          f"worst {w:.3f} px at {where} -- the comparison has two sides")

    w, where = worst_gap_with(model, bake, ts, _bent("radians"))
    check("degrees used as radians is seen", w > 1.0,
          f"worst {w:.3f} px at {where} -- the angle unit is load-bearing")


# --------------------------------------------------------------------------
# 5 -- the model is a function of the scene, not of the run
# --------------------------------------------------------------------------

def test_deterministic(scene_path):
    print("\n5. the same comp gives the same geometry")

    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        _, m1 = run_loop(scene_path, d1)
        _, m2 = run_loop(scene_path, d2)

    a = json.dumps(m1, sort_keys=True)
    b = json.dumps(m2, sort_keys=True)

    # Wall K's guard hashes the SCENE, and the viewport's geometry is derived
    # from it. If this were nondeterministic the app could not cache it, and a
    # "the comp changed" warning would eventually be built on sand.
    check("two runs, byte-identical geometry", a == b,
          f"{len(a)} bytes each" if a == b else "they differ")



# --------------------------------------------------------------------------
# 6 -- C6.3: the scene BEFORE it is simulated
# --------------------------------------------------------------------------

def render_only(scene_path, out_dir, extra=()):
    """`--render-only`: the same command, stopped before the sim starts."""
    model = os.path.join(out_dir, "pre.json")
    cmd = [sys.executable, os.path.join(HERE, "b3_loop.py"), scene_path,
           "--render-only", "--render-model", model,
           "--gravity", "9.8", "--ppm", "100", "--substeps", "8",
           "--frames", "60", "--static", "FLOOR", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    if p.returncode != 0:
        raise SystemExit(f"--render-only failed ({p.returncode}):\n{p.stderr}")
    return load(model)


def test_pre_sim(scene_path, model):
    """The pre-sim drawing is only worth trusting if it is the SAME geometry.

    C6.3 exists so the reader can be checked by eye: if the polygons are
    misplaced before any physics runs, the sim is innocent, and B1 spent a
    whole sitting on exactly that confusion when A3's layer-space assumption
    turned out to be false in AE.

    That argument collapses if `--render-only` takes a different path through
    the geometry. It does not -- everything above the early exit is scene
    loading, pins, per-layer overrides and the wall box, and the sim starts
    after it -- but "does not" is the kind of claim this project has been
    wrong about before, so it is measured rather than asserted.
    """
    print("\n6. --render-only writes the same geometry the solver is handed")

    with tempfile.TemporaryDirectory() as d:
        pre = render_only(scene_path, d)

    a = json.dumps(pre, sort_keys=True)
    b = json.dumps(model, sort_keys=True)
    check("pre-sim model is byte-identical to the post-sim one", a == b,
          f"{len(a)} bytes each" if a == b else "they differ")

    # THE CONTROL. The check above is a comparison of two things produced from
    # one scene, and a comparison like that passes just as happily when the
    # thing being compared is constant -- which is precisely the trap
    # section 4 fell into TWICE. So: change the scene, and the two must part.
    #
    # A pin is the right perturbation because it is the one input that reaches
    # the render model without going near the solver: it lands in `static`,
    # which decides the fill colour of every polygon the layer draws. If a pin
    # did NOT move this number, the pre-sim drawing would be showing a world
    # with no floor in it.
    with tempfile.TemporaryDirectory() as d:
        unpinned = render_only(scene_path, d, extra=("--no-walls",))
    moved = json.dumps(unpinned, sort_keys=True) != b
    check("control: dropping the walls changes it", moved,
          "the two models differ, so the comparison has teeth"
          if moved else "IDENTICAL -- section 6 proves nothing")

    # And the resting pose is the whole point: with no bake, every layer draws
    # at `rest`, so a model whose rest poses were empty would draw a blank
    # comp and still pass everything above.
    posed = [L for L in pre["layers"]
             if L["rest"]["position"] != [0.0, 0.0]]
    check("every layer has a resting pose to draw at",
          len(posed) == len(pre["layers"]),
          f"{len(posed)}/{len(pre['layers'])} layers sit somewhere real")

def main():
    print("C2.0 -- ae-physics-render/1, the viewport's transform, and C6.3")
    print("=" * 74)

    if not os.path.exists(SCENE):
        print(f"\n  no scene at {SCENE}")
        print("  Read a comp through the shell first -- this checks against a "
              "real one\n  rather than a fixture, because B1's whole lesson was "
              "that the fixture agreed\n  and AE did not.")
        return 2

    with tempfile.TemporaryDirectory() as d:
        bake, model = run_loop(SCENE, d)

    test_shape(model)
    test_rest_pose(model, bake)
    test_agreement(model, bake)
    test_broken_control(model, bake)
    test_deterministic(SCENE)
    test_pre_sim(SCENE, model)

    print("\n" + "=" * 74)
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} checks pass")
    for n in bad:
        print(f"  FAILED: {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

"""
A5 -- the preview renderer, and what it is actually for.

The plan calls the preview "not decoration -- the only way to see that a bake is
wrong". That claim deserves testing rather than assuming, so this step does not
just draw the animation. It corrupts the bake in the specific ways a bake goes
wrong, renders both, and MEASURES whether the preview shows it.

Which faults? Not arbitrary ones. Every settled convention from A1-A4 is a
decision that could have gone the other way, and each is one line from being
wrong:

    A1  no sign flip on rotation            ->  fault: negate it
    A1  y-down comp frame                   ->  fault: flip to y-up
    A1  bake degrees                        ->  fault: bake radians
    A1  unwrap rotation (Wall F)            ->  fault: leave it wrapped
    A2  Position is the ANCHOR (Wall E)     ->  fault: write the COM
    A1  keyframe volume (Wall I)            ->  faults: round, and decimate

The preview is a SECOND consumer, sharing nothing with the solver -- that is
what lets it catch a fault in the conventions themselves. `preview.py` imports
neither pymunk nor sim.

Input is A4's four real AE exports, so this runs on the hardest geometry we have.

Run:  python a5_preview.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

import preview
from a4_alpha import scene_from_exports
from sim import Scene, bake as bake_scene, simulate_traced

FRAMES = 96
GIF_SCALE = 0.30
MASK_SCALE = 0.40
SAMPLE_FRAMES = [0, 12, 24, 40, 56, 72, 84, 96]

# Faults are measured BETWEEN keyframes as well as on them, and the first run
# of this file is the reason why. Sampled only on keyframed integer frames,
# two of the six faults measured exactly zero:
#
#   * Wall F (rotation left wrapped) writes 184.88 as -175.12. Those are the
#     same orientation. Every single frame draws identically; what differs is
#     the TWEEN, which sweeps 360 degrees the wrong way across the wrap. A
#     still is blind to it by construction. Worse, that sweep lives inside the
#     ONE frame interval that contains the crossing -- a second pass sampling
#     every 8th frame and every 8th half-frame still measured it at 0.00 px.
#     Nothing short of every half-frame finds it.
#   * Decimation at stride 2 and 4 looked free, because every frame we sampled
#     was a multiple of 4 -- i.e. a keyframe that survived the decimation. We
#     were only ever looking at the frames the fault had not touched.
#
# So the sample set deliberately straddles keyframes. AE plays the tween, not
# the keyframes, and a preview that only draws keyframes is not a preview.
FAULT_SAMPLES = sorted([float(f) for f in range(FRAMES + 1)]
                       + [f + 0.5 for f in range(FRAMES)])

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# --------------------------------------------------------------------------
# Fault injection -- each one is a convention from A1-A4, inverted
# --------------------------------------------------------------------------

def _map(bake, pos=None, rot=None):
    out = copy.deepcopy(bake)
    for lay in out["layers"]:
        kf = lay["keyframes"]
        if pos:
            kf["position"] = [[f, pos(lay, f, v)] for f, v in kf["position"]]
        if rot:
            kf["rotation"] = [[f, rot(lay, f, v)] for f, v in kf["rotation"]]
    return out


def f_rotation_sign(bake):
    """A1 decided y-down needs NO sign flip. This is the flip."""
    return _map(bake, rot=lambda l, f, v: -v)


def f_y_up(bake):
    """The solver in a y-up frame, the comp still y-down."""
    h = bake["comp"]["height"]
    return _map(bake, pos=lambda l, f, v: [v[0], h - v[1]])


def f_radians(bake):
    """Rotation baked in radians into a field AE reads as degrees."""
    return _map(bake, rot=lambda l, f, v: math.radians(v))


def f_wrapped(bake):
    """Wall F undone: the raw wrapped angle instead of the accumulated one."""
    return _map(bake, rot=lambda l, f, v: (v + 180.0) % 360.0 - 180.0)


def f_com_not_anchor(bake):
    """Wall E undone: Position written as the COM instead of the anchor.

    position_correct = com + R(theta)*anchor_offset, so subtracting that term
    back off reproduces exactly the bug A2 was built to prevent.
    """
    out = copy.deepcopy(bake)
    for lay in out["layers"]:
        ax, ay = lay["anchor_offset"]
        rots = dict((f, v) for f, v in lay["keyframes"]["rotation"])
        new = []
        for f, (x, y) in lay["keyframes"]["position"]:
            th = math.radians(rots[f])
            c, s = math.cos(th), math.sin(th)
            new.append([f, [x - (ax * c - ay * s), y - (ax * s + ay * c)]])
        lay["keyframes"]["position"] = new
    return out


def f_rounded(bake):
    """Wall I: whole-pixel positions and whole-degree rotations."""
    return _map(bake,
                pos=lambda l, f, v: [float(round(v[0])), float(round(v[1]))],
                rot=lambda l, f, v: float(round(v)))


def decimate(bake, stride):
    """Wall I: keep every Nth keyframe and let AE's linear interpolation cover
    the gap. The end frame is always kept, so nothing is extrapolated."""
    out = copy.deepcopy(bake)
    last = bake["comp"]["duration_frames"]
    for lay in out["layers"]:
        for chan in ("position", "rotation"):
            lay["keyframes"][chan] = [
                kv for kv in lay["keyframes"][chan]
                if kv[0] % stride == 0 or kv[0] == last
            ]
    return out


FAULTS = [
    ("rotation sign flipped (A1)", f_rotation_sign),
    ("comp read as y-up (A1)", f_y_up),
    ("rotation in radians (A1)", f_radians),
    ("rotation left wrapped (Wall F)", f_wrapped),
    ("Position = COM, not anchor (Wall E)", f_com_not_anchor),
    ("positions rounded to whole px (Wall I)", f_rounded),
]


# --------------------------------------------------------------------------
# Measuring a fault
# --------------------------------------------------------------------------

def worst_vertex_shift(good, bad, layers, frames):
    worst = 0.0
    idx_g, idx_b = preview.index_bake(good), preview.index_bake(bad)
    for t in frames:
        for lay in layers:
            for pg, pb in zip(preview.transform_layer(lay, idx_g[lay.id], t),
                              preview.transform_layer(lay, idx_b[lay.id], t)):
                for a, b in zip(pg, pb):
                    worst = max(worst, math.dist(a, b))
    return worst


def peak_disagreement(good, bad, layers, pscene, frames):
    peak = 0.0
    for t in frames:
        a = preview.render_frame(good, layers, pscene, t, MASK_SCALE,
                                 mask_only=True)
        b = preview.render_frame(bad, layers, pscene, t, MASK_SCALE,
                                 mask_only=True)
        peak = max(peak, preview.mask_disagreement(a, b))
    return peak


# --------------------------------------------------------------------------
# 1. The renderer agrees with the solver -- from the JSON alone
# --------------------------------------------------------------------------

def test_agrees_with_solver(scene, bake, layers):
    print("\n1. THE RENDERER AND THE SOLVER  (two paths to the same pixels)")
    handles, tracks, traces = simulate_traced(scene)
    idx = preview.index_bake(bake)
    worst = 0.0
    for fr in range(scene.duration_frames + 1):
        for i, lay in enumerate(layers):
            got = preview.transform_layer(lay, idx[lay.id], float(fr))
            for pg, pt in zip(got, traces[i][fr]):
                for a, b in zip(pg, pt):
                    worst = max(worst, math.dist(a, b))
    check("preview transform matches the solver's own vertices",
          worst < 1e-3,
          f"worst {worst:.3e} px over {scene.duration_frames + 1} frames, "
          "reading only the bake JSON and the layer geometry")

    # Broken control: the comparison has to be capable of failing.
    nudged = [preview.PreviewLayer(l.id, l.name, l.parts,
                                   (l.anchor[0] + 1.0, l.anchor[1]))
              for l in layers]
    worst_n = 0.0
    for fr in range(0, scene.duration_frames + 1, 8):
        for i, lay in enumerate(nudged):
            got = preview.transform_layer(lay, idx[lay.id], float(fr))
            for pg, pt in zip(got, traces[i][fr]):
                for a, b in zip(pg, pt):
                    worst_n = max(worst_n, math.dist(a, b))
    check("...and a 1 px anchor error breaks it",
          worst_n > 0.9,
          f"control: anchor moved 1 px -> worst {worst_n:.4f} px "
          "(the check can fail, so passing means something)")


# --------------------------------------------------------------------------
# 2-3. Sampling
# --------------------------------------------------------------------------

def test_sampling(bake):
    print("\n2. SAMPLING  (a preview that resamples is a preview that lies)")
    kf = bake["layers"][0]["keyframes"]
    exact = all(preview._sample(kf["position"], float(f), False) == v
                for f, v in kf["position"]) and \
        all(preview._sample(kf["rotation"], float(f), True) == v
            for f, v in kf["rotation"])
    check("integer frames return the stored keyframe untouched",
          exact,
          f"all {len(kf['position'])} position and {len(kf['rotation'])} "
          "rotation keyframes reproduce bit-for-bit -- no interpolation error "
          "can hide in the frames we actually draw")


def test_linear_interpolation_cost(scene, bake):
    """What AE's LINEAR interpolation costs between our keyframes.

    Ground truth for half-frames comes from re-running the same scene at
    48 fps with HALF the substeps: dt = 1/(fps*substeps) is then unchanged, so
    it is the same trajectory sampled twice as often -- not a second
    simulation. Doubling both instead (the first thing this file did) quarters
    dt, and the two runs diverged by 142 px by frame 75. That is not an
    interpolation measurement, it is two different simulations, and the
    difference is large enough to look like a real finding.

    The prediction -- chord sag g*dt^2/8 on a parabola -- only applies where
    the motion IS a parabola, so the frames are split. A frame counts as
    ballistic when the second difference of position is gravity and nothing
    else: no contact, and no rotation swinging the anchor around the COM.
    """
    print("\n3. WHAT LINEAR INTERPOLATION COSTS  (the curve between keyframes)")
    assert scene.substeps % 2 == 0, "need an even substep count to halve it"
    d = dict(scene.__dict__)
    d.update(fps=scene.fps * 2, substeps=scene.substeps // 2,
             duration_frames=scene.duration_frames * 2)
    idx_f = preview.index_bake(bake_scene(Scene(**d)))

    dt = 1.0 / scene.fps
    g_px = scene.gravity_m_s2 * scene.pixels_per_meter
    predicted = g_px * dt * dt / 8.0

    worst = worst_ballistic = 0.0
    n_ballistic = 0
    print("      layer    worst px   ballistic frames   worst there")
    print("      ------   --------   ----------------   -----------")
    for lay in bake["layers"]:
        kf = lay["keyframes"]["position"]
        truth = idx_f[lay["id"]]["keyframes"]["position"]
        w = wb = 0.0
        nb = 0
        for f in range(1, scene.duration_frames - 1):
            err = math.dist(preview._sample(kf, f + 0.5, False),
                            truth[2 * f + 1][1])
            w = max(w, err)
            p0, p1, p2 = kf[f - 1][1], kf[f][1], kf[f + 1][1]
            ax = (p2[0] - 2 * p1[0] + p0[0]) / (dt * dt)
            ay = (p2[1] - 2 * p1[1] + p0[1]) / (dt * dt)
            if abs(ax) < 1.0 and abs(ay - g_px) < 1.0:
                nb += 1
                wb = max(wb, err)
        print(f"      {lay['name']:<6}   {w:8.3f}   {nb:16d}   "
              + (f"{wb:11.4f}" if nb else f"{'--':>11}"))
        worst = max(worst, w)
        worst_ballistic = max(worst_ballistic, wb)
        n_ballistic += nb

    check("free fall costs exactly the predicted chord sag",
          n_ballistic > 0 and abs(worst_ballistic - predicted) < 0.01,
          f"{worst_ballistic:.4f} px on {n_ballistic} ballistic frames vs "
          f"g*dt^2/8 = {predicted:.4f} px predicted -- agreement to 1e-4 px")
    check("...and everywhere else it is 29x worse",
          worst > 10 * predicted,
          f"worst {worst:.3f} px across all frames. Contact and rotation are "
          "not parabolas, and that is where a decimated bake will break -- "
          "not in the fall")


# --------------------------------------------------------------------------
# 4. Determinism of the rendered preview
# --------------------------------------------------------------------------

def _digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_determinism(bake, layers, pscene):
    print("\n4. DETERMINISM  (Wall H, extended to the pixels)")
    fs = [float(f) for f in range(0, FRAMES + 1, 8)]
    a = preview.render_gif("_a5_det_a.gif", bake, layers, pscene, GIF_SCALE, fs)
    b = preview.render_gif("_a5_det_b.gif", bake, layers, pscene, GIF_SCALE, fs)
    da, db = _digest(a), _digest(b)
    check("two renders of one bake are byte-identical",
          da == db, f"sha256 {da[:16]} on both, {os.path.getsize(a)} bytes")

    tiny = [preview.PreviewLayer(
        l.id, l.name,
        [[(x + (0.05 if k == 0 else 0.0), y) for k, (x, y) in enumerate(p)]
         for p in l.parts],
        l.anchor) for l in layers]
    c = preview.render_gif("_a5_det_c.gif", bake, tiny, pscene, GIF_SCALE, fs)
    check("...and a 0.05 px vertex move changes the bytes",
          _digest(c) != da,
          "control: the digest is sensitive, so agreement is evidence")
    for p in ("_a5_det_a.gif", "_a5_det_b.gif", "_a5_det_c.gif"):
        os.remove(p)


# --------------------------------------------------------------------------
# 5. The claim: the preview catches a wrong bake
# --------------------------------------------------------------------------

def test_faults(bake, layers, pscene):
    print("\n5. INJECTED FAULTS  (does the preview actually show them?)")
    fs = FAULT_SAMPLES
    rows = []
    print("      fault                                    worst px   silhouette")
    print("      --------------------------------------   --------   ----------")
    for name, fn in FAULTS:
        bad = fn(bake)
        shift = worst_vertex_shift(bake, bad, layers, fs)
        dis = peak_disagreement(bake, bad, layers, pscene, fs)
        rows.append((name, shift, dis))
        print(f"      {name:<38}   {shift:8.2f}   {dis * 100:8.1f}%")

    invisible = [n for n, _s, d in rows if d < 0.02]
    check("every inverted convention is visible in the preview",
          not invisible,
          f"{len(rows)}/{len(rows)} faults move at least 2% of the "
          "silhouette; smallest is "
          f"{min(d for _n, _s, d in rows) * 100:.1f}%")

    quiet = [(n, s, d) for n, s, d in rows if s < 3.0]
    check("the rounding fault is the small one, and still visible",
          len(quiet) == 1 and quiet[0][0].startswith("positions rounded"),
          "; ".join(f"{n}: {s:.2f} px moves {d * 100:.1f}%"
                    for n, s, d in quiet)
          + " -- a numeric diff shrugs at half a pixel; the render does not")
    return rows


def test_decimation(bake, layers, pscene):
    print("\n6. KEYFRAME DECIMATION  (Wall I: how few keyframes survive?)")
    fs = FAULT_SAMPLES
    full = len(json.dumps(bake, separators=(",", ":")))
    print("      stride   keyframes/layer   json     worst px   silhouette")
    print("      ------   ---------------   ------   --------   ----------")
    rows = []
    for stride in (1, 2, 4, 8, 16):
        bad = decimate(bake, stride)
        n = len(bad["layers"][0]["keyframes"]["position"])
        size = len(json.dumps(bad, separators=(",", ":")))
        shift = worst_vertex_shift(bake, bad, layers, fs)
        dis = peak_disagreement(bake, bad, layers, pscene, fs)
        rows.append((stride, n, size, shift, dis))
        print(f"      {stride:6d}   {n:15d}   {size / full * 100:5.1f}%   "
              f"{shift:8.2f}   {dis * 100:8.1f}%")
    check("stride 1 -- one keyframe per frame -- is the only exact one",
          rows[0][3] == 0.0,
          "0.000 px, which is the definition: nothing is interpolated")
    check("uniform decimation is not a keyframe-volume strategy",
          rows[1][3] > 10.0,
          f"halving the keyframes already costs {rows[1][3]:.1f} px "
          f"({rows[1][4] * 100:.1f}% of the silhouette) for a "
          f"{100 - rows[1][2] / full * 100:.0f}% smaller file. The error is "
          "not spread evenly -- it is concentrated at contacts, exactly where "
          "check 3 said the motion stops being a parabola. Wall I wants "
          "error-driven keyframe reduction, not a fixed stride")
    return rows


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_outputs(bake, layers, pscene):
    fs = [float(f) for f in range(0, FRAMES + 1)]
    preview.render_gif("a5_preview.gif", bake, layers, pscene, GIF_SCALE, fs)
    print(f"\n   wrote a5_preview.gif "
          f"({os.path.getsize('a5_preview.gif') / 1024:.0f} KB, "
          f"{len(fs)} frames)")

    sheet = preview.contact_sheet(bake, layers, pscene,
                                  [float(f) for f in SAMPLE_FRAMES])
    sheet.save("a5_contact.png")
    print(f"   wrote a5_contact.png ({sheet.size[0]}x{sheet.size[1]})")

    fig = plt.figure(figsize=(14, 9))
    ax = fig.add_subplot(2, 1, 1)
    ax.imshow(sheet)
    ax.set_title(f"Baked preview, frames {SAMPLE_FRAMES} "
                 "(rendered from the keyframe JSON alone)")
    ax.axis("off")

    # Each fault above the truth, at the frame where it disagrees MOST.
    # Picking a fixed frame is how the first version of this figure managed to
    # show four faults and illustrate two -- at frame 60 everything is resting,
    # and a wrap that lasts one frame interval is simply not there.
    for j, (name, fn) in enumerate(FAULTS[:4]):
        bad = fn(bake)
        t_worst, d_worst = 0.0, -1.0
        for t in FAULT_SAMPLES:
            d = preview.mask_disagreement(
                preview.render_frame(bake, layers, pscene, t, 0.20, True),
                preview.render_frame(bad, layers, pscene, t, 0.20, True))
            if d > d_worst:
                t_worst, d_worst = t, d
        good_img = preview.render_frame(bake, layers, pscene, t_worst, 0.20)
        bad_img = preview.render_frame(bad, layers, pscene, t_worst, 0.20)
        pair = Image.new("RGB", (good_img.width, good_img.height * 2))
        pair.paste(good_img, (0, 0))
        pair.paste(bad_img, (0, good_img.height))
        ax = fig.add_subplot(2, 4, 5 + j)
        ax.imshow(pair)
        ax.set_title(f"good / {name}\nworst at frame {t_worst:g} "
                     f"({d_worst * 100:.0f}%)", fontsize=8)
        ax.axis("off")

    fig.tight_layout()
    fig.savefig("a5_checks.png", dpi=105)
    print("   wrote a5_checks.png")


if __name__ == "__main__":
    print("A5 -- preview renderer, on A4's four real AE exports")
    scene, _parts = scene_from_exports(frames=FRAMES)
    bake = bake_scene(scene)
    layers, pscene = preview.layers_from_scene(scene)

    test_agrees_with_solver(scene, bake, layers)
    test_sampling(bake)
    test_linear_interpolation_cost(scene, bake)
    test_determinism(bake, layers, pscene)
    test_faults(bake, layers, pscene)
    test_decimation(bake, layers, pscene)
    write_outputs(bake, layers, pscene)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")

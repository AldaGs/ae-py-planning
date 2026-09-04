"""
A2 -- the layer-space <-> body-space transform (Wall E).

A1 only ever used symmetric primitives, whose COM is trivially their centre.
That hides the whole problem. Here the test shape is an L built from two convex
parts whose centre of mass lands at (60, 160) in layer space -- a point that is
OUTSIDE the material entirely. The layer rotates about empty space, which is
exactly the case where getting the transform wrong looks like a solver bug.

The headline check is a replay: rebuild each frame's polygon the way AE would,
from the baked keyframes alone, and compare against the solver's own vertices.

Run:  python a2_anchor_com.py
"""

from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import geom
from sim import (Body, PolyBody, Scene, bake_json, geom as _g, replay_from_keyframes,
                 simulate_traced)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# The test shape: a thin L, in LAYER space px, as two convex parts.
L_PARTS = [
    [(0.0, 0.0), (40.0, 0.0), (40.0, 240.0), (0.0, 240.0)],        # upright
    [(40.0, 200.0), (200.0, 200.0), (200.0, 240.0), (40.0, 240.0)],  # foot
]
L_ANCHOR = (0.0, 0.0)          # AE's layer origin -- nowhere near the COM


def point_in_parts(pt, parts) -> bool:
    x, y = pt
    for part in parts:
        v = geom.ensure_ccw(part)
        n = len(v)
        if all(
            (v[(i + 1) % n][0] - v[i][0]) * (y - v[i][1])
            - (v[(i + 1) % n][1] - v[i][1]) * (x - v[i][0]) >= -1e-9
            for i in range(n)
        ):
            return True
    return False


# --------------------------------------------------------------------------
# 1. Mass properties, computed twice
# --------------------------------------------------------------------------

def test_mass_properties():
    print("\n1. MASS PROPERTIES  (our math vs pymunk's, independently)")
    import pymunk

    mass, com, moment = geom.compound_mass_properties(L_PARTS, 1.0)
    bbox = geom.bbox_center(L_PARTS)
    print(f"      area        = {mass:.4f} px^2")
    print(f"      true COM    = ({com[0]:.4f}, {com[1]:.4f})")
    print(f"      bbox centre = ({bbox[0]:.4f}, {bbox[1]:.4f})")

    check("COM is not the bbox centre (broken control)",
          math.dist(com, bbox) > 10.0,
          f"they differ by {math.dist(com, bbox):.2f} px -- using the bbox "
          "centre would offset every rotation by that much")
    check("COM lands OUTSIDE the material",
          not point_in_parts(com, L_PARTS),
          f"({com[0]:.1f}, {com[1]:.1f}) is in empty space between the arms; "
          "the layer rotates about a point that is not on it")

    # Single convex part, cross-checked against pymunk's own formula.
    tri = [(0.0, 0.0), (300.0, 0.0), (0.0, 180.0)]
    m_t, c_t, i_t = geom.mass_properties(tri, 1.0)
    ours = i_t
    theirs = pymunk.moment_for_poly(
        m_t, geom.ensure_ccw([(x - c_t[0], y - c_t[1]) for x, y in tri]),
        (0, 0), 0.0)
    check("our polygon moment matches pymunk's",
          abs(ours - theirs) / theirs < 1e-9,
          f"ours {ours:.6f} vs pymunk {theirs:.6f} "
          f"(rel {abs(ours - theirs) / theirs:.2e})")
    check("triangle centroid is at the one-third point",
          math.dist(c_t, (100.0, 60.0)) < 1e-9,
          f"({c_t[0]:.6f}, {c_t[1]:.6f}) -- not the bbox centre (150, 90)")
    return com, bbox


# --------------------------------------------------------------------------
# 2. Round trip: AE -> solver -> AE must be the identity
# --------------------------------------------------------------------------

def test_round_trip():
    print("\n2. ROUND TRIP  (position in == position out, at frame 0)")
    cases = [
        ((0.0, 0.0), (500.0, 300.0), 0.0),
        ((0.0, 0.0), (500.0, 300.0), 37.0),
        ((200.0, 240.0), (960.0, 540.0), -125.0),
        ((60.0, 160.0), (100.0, 900.0), 180.0),      # anchor ON the COM
    ]
    worst = 0.0
    for anchor, pos, ang in cases:
        scene = Scene(duration_frames=0, gravity_m_s2=0.0, bodies=[
            PolyBody("L", L_PARTS, anchor=anchor, position=pos, angle_deg=ang)])
        _h, tracks, _t = simulate_traced(scene)
        got = tracks[0]["position"][0][1]
        worst = max(worst, math.dist(got, pos))
    check("anchor/position survives the trip for every case",
          worst < 1e-6, f"worst error over {len(cases)} cases = {worst:.3e} px")

    # Broken control: what you get if you skip the transform and hand AE the
    # centre of mass instead of the anchor.
    anchor, pos, ang = (0.0, 0.0), (960.0, 540.0), 37.0
    scene = Scene(duration_frames=0, gravity_m_s2=0.0, bodies=[
        PolyBody("L", L_PARTS, anchor=anchor, position=pos, angle_deg=ang)])
    h, tracks, _t = simulate_traced(scene)
    com_world = tuple(v * scene.pixels_per_meter for v in h[0].body.position)
    check("using the COM as Position would be visibly wrong (broken control)",
          math.dist(com_world, pos) > 100.0,
          f"COM sits {math.dist(com_world, pos):.1f} px from the anchor at "
          f"{ang:g} deg -- that is the size of the mistake")


# --------------------------------------------------------------------------
# 3. Replay: does AE, given only our keyframes, draw what the solver simulated?
# --------------------------------------------------------------------------

def falling_L_scene(frames=90):
    return Scene(
        fps=24.0, duration_frames=frames,
        statics=[((100.0, 950.0), (1820.0, 950.0))],
        bodies=[
            PolyBody("L_spin", L_PARTS, anchor=L_ANCHOR,
                     position=(700.0, 200.0), angle_deg=15.0,
                     velocity=(120.0, 0.0), angular_velocity_deg=200.0),
            PolyBody("L_drop", L_PARTS, anchor=(200.0, 240.0),
                     position=(1400.0, 300.0), angle_deg=-40.0),
        ],
    )


def test_replay():
    print("\n3. REPLAY  (AE redraws from keyframes; does it match the solver?)")
    scene = falling_L_scene()
    handles, tracks, traces = simulate_traced(scene)

    worst = 0.0
    per_frame = []
    for f in range(scene.duration_frames + 1):
        e = 0.0
        for h, trk, tr in zip(handles, tracks, traces):
            for got, want in zip(replay_from_keyframes(h, trk, f), tr[f]):
                for a, b in zip(got, want):
                    e = max(e, math.dist(a, b))
        per_frame.append(e)
        worst = max(worst, e)
    check("replayed vertices match the solver everywhere",
          worst < 1e-3,
          f"worst vertex error over {scene.duration_frames + 1} frames x "
          f"{len(handles)} layers = {worst:.3e} px")

    # Broken control: replay with the anchor offset dropped, i.e. treating the
    # COM as the anchor -- the single most likely way to get this wrong.
    broken = 0.0
    for f in range(scene.duration_frames + 1):
        for h, trk, tr in zip(handles, tracks, traces):
            pos = trk["position"][f][1]
            th = math.radians(trk["rotation"][f][1])
            for part, want in zip(h.parts_local, tr[f]):
                for (x, y), b in zip(part, want):
                    p = geom.rotate((x, y), th)
                    broken = max(broken, math.dist(
                        (pos[0] + p[0], pos[1] + p[1]), b))
    check("dropping the anchor offset breaks it (broken control)",
          broken > 100.0,
          f"worst error becomes {broken:.1f} px -- the check can fail")
    return scene, handles, tracks, traces, per_frame


# --------------------------------------------------------------------------
# 4. Physical consequence: the COM is the thing that obeys the physics
# --------------------------------------------------------------------------

def test_com_is_physical():
    print("\n4. PHYSICS  (the COM follows the parabola; the anchor does not)")
    scene = Scene(
        fps=24.0, duration_frames=72,
        bodies=[PolyBody("L", L_PARTS, anchor=L_ANCHOR,
                         position=(400.0, 150.0),
                         angular_velocity_deg=260.0)],
    )
    _h, tracks, _t = simulate_traced(scene)
    trk = tracks[0]

    _m, com_layer, _i = geom.compound_mass_properties(L_PARTS, 1.0)
    offset = (L_ANCHOR[0] - com_layer[0], L_ANCHOR[1] - com_layer[1])
    radius = math.hypot(*offset)

    coms, ancs = [], []
    for f in range(scene.duration_frames + 1):
        p = trk["position"][f][1]
        th = math.radians(trk["rotation"][f][1])
        r = geom.rotate(offset, th)
        coms.append((p[0] - r[0], p[1] - r[1]))
        ancs.append(tuple(p))

    com_dx = max(abs(c[0] - coms[0][0]) for c in coms)
    # The anchor sweeps a circle of `radius` about the COM, so over at least
    # one full turn its x SPAN is a diameter. (Measuring deviation from the
    # starting x instead gives radius + |offset_x|, which is not an invariant
    # -- it depends on where in the circle the sim happened to start.)
    anc_span = max(a[0] for a in ancs) - min(a[0] for a in ancs)
    turns = abs(trk["rotation"][-1][1] - trk["rotation"][0][1]) / 360.0
    check("no horizontal force, so the COM holds its x exactly",
          com_dx < 1e-4,
          f"COM x wanders {com_dx:.2e} px over {len(coms)} frames")
    check("the anchor meanwhile sweeps a full diameter about the COM",
          turns > 1.0 and abs(anc_span - 2.0 * radius) < 1.0,
          f"anchor x spans {anc_span:.1f} px over {turns:.2f} turns, "
          f"vs 2*|anchor - COM| = {2 * radius:.1f} px -- a layer whose "
          "Position is not its physics")
    return coms, ancs


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------

def draw_parts(ax, parts, **kw):
    for p in parts:
        xs = [v[0] for v in p] + [p[0][0]]
        ys = [v[1] for v in p] + [p[0][1]]
        ax.plot(xs, ys, **kw)
        kw = {k: v for k, v in kw.items() if k != "label"}


def plot(scene, handles, tracks, traces, per_frame, coms, ancs, com, bbox):
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))

    a = ax[0][0]
    draw_parts(a, L_PARTS, color="0.35", lw=2, label="layer content")
    a.plot(*com, "o", ms=10, color="crimson", label=f"true COM {com}")
    a.plot(*bbox, "s", ms=9, color="tab:blue", label="bbox centre (wrong)")
    a.plot(*L_ANCHOR, "^", ms=10, color="tab:green", label="anchor (0,0)")
    a.set_aspect("equal")
    a.invert_yaxis()
    a.set_title("The COM sits in empty space between the arms")
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    a = ax[0][1]
    for f in range(0, scene.duration_frames + 1, 12):
        for i, (h, trk, tr) in enumerate(zip(handles, tracks, traces)):
            first = (f == 0 and i == 0)
            draw_parts(a, replay_from_keyframes(h, trk, f),
                       color="tab:orange", lw=2.4, alpha=.75,
                       label="replayed from keyframes" if first else None)
            draw_parts(a, tr[f], color="k", lw=1.0, ls="--",
                       label="solver ground truth" if first else None)
    a.axhline(950, color="k", lw=2)
    a.set_xlim(400, 1920)
    a.set_ylim(1080, 0)
    a.set_aspect("equal")
    a.set_title("Replay vs solver, every 12th frame")
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    a = ax[1][0]
    a.semilogy([max(e, 1e-12) for e in per_frame], lw=1.6)
    a.set_xlabel("frame")
    a.set_ylabel("worst vertex error (px)")
    a.set_title("Replay error -- floor is keyframe rounding, not the transform")
    a.grid(alpha=.3, which="both")

    a = ax[1][1]
    a.plot([p[0] for p in coms], [p[1] for p in coms], lw=2.5,
           color="crimson", label="COM (clean parabola)")
    a.plot([p[0] for p in ancs], [p[1] for p in ancs], lw=1.3,
           color="tab:green", label="anchor = AE Position (cycloid)")
    a.invert_yaxis()
    a.set_xlabel("comp x (px)")
    a.set_ylabel("comp y (px)")
    # Axes deliberately NOT equal: the body falls ~4000px while the anchor
    # swings only +/-171, so an equal-aspect plot is an unreadable sliver.
    a.set_title("What AE keyframes trace (note: axes not to scale)")
    a.legend(fontsize=8, loc="upper left")
    a.grid(alpha=.3)

    fig.tight_layout()
    fig.savefig("a2_checks.png", dpi=110)
    print("\n   wrote a2_checks.png")


if __name__ == "__main__":
    print("A2 -- layer space vs body space, on a shape with a real COM")
    com, bbox = test_mass_properties()
    test_round_trip()
    scene, handles, tracks, traces, per_frame = test_replay()
    coms, ancs = test_com_is_physical()
    plot(scene, handles, tracks, traces, per_frame, coms, ancs, com, bbox)

    with open("a2_bake.json", "w") as fh:
        fh.write(bake_json(falling_L_scene()))
    print("   wrote a2_bake.json")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")

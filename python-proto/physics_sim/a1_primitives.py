"""
A1 -- the spine, on primitives.

Everything here is a MEASUREMENT with a BROKEN CONTROL beside it, never a bare
assertion: each check is paired with a deliberately wrong run, so we can see the
check is actually capable of failing.

Run:  python a1_primitives.py
"""

from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sim import Body, Scene, bake_json, simulate

FLOOR_Y = 900.0
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# --------------------------------------------------------------------------
# 1. Free fall vs the integrator we actually have
# --------------------------------------------------------------------------

def free_fall_scene(ppm=100.0, substeps=8, frames=24):
    return Scene(
        fps=24.0, duration_frames=frames, pixels_per_meter=ppm,
        substeps=substeps,
        bodies=[Body("faller", "box", (100, 100), (960.0, 100.0))],
    )


def test_free_fall():
    print("\n1. FREE FALL  (do we understand the integrator?)")
    scene = free_fall_scene()
    _, tracks = simulate(scene)
    y0 = tracks[0]["position"][0][1][1]
    y1 = tracks[0]["position"][-1][1][1]
    drop = y1 - y0

    g_px = scene.gravity_m_s2 * scene.pixels_per_meter
    t = scene.duration_frames / scene.fps
    dt = 1.0 / (scene.fps * scene.substeps)
    n = scene.duration_frames * scene.substeps

    analytic = 0.5 * g_px * t * t
    # MEASURED, not assumed. Chipmunk advances position with the velocity from
    # BEFORE the gravity update (x += v*dt, then v += g*dt), so it accumulates
    # g*dt^2 * n(n-1)/2 and UNDERSHOOTS the closed form by 0.5*g*dt*t.
    # (First guess here was semi-implicit -- n(n+1)/2, overshooting. The run
    # disagreed by exactly twice the bias, which is what pinned the ordering.)
    euler = g_px * dt * dt * n * (n - 1) / 2.0

    print(f"      analytic 0.5*g*t^2 = {analytic:9.4f} px")
    print(f"      euler prediction   = {euler:9.4f} px "
          f"(bias {euler - analytic:+.4f} = -0.5*g*dt*t)")
    print(f"      measured           = {drop:9.4f} px")
    check("free fall matches explicit-Euler prediction",
          abs(drop - euler) < 1e-6,
          f"|measured - euler| = {abs(drop - euler):.3e} px")
    check("free fall does NOT match the closed form (broken control)",
          abs(drop - analytic) > 1.0,
          f"|measured - analytic| = {abs(drop - analytic):.4f} px "
          f"-- the {abs(euler - analytic):.2f}px gap is the integrator, not a bug")


# --------------------------------------------------------------------------
# 2. Determinism  (Wall H)
# --------------------------------------------------------------------------

def stack_scene(ppm=100.0, frames=120):
    return Scene(
        fps=24.0, duration_frames=frames, pixels_per_meter=ppm,
        statics=[((200.0, FLOOR_Y), (1720.0, FLOOR_Y))],
        bodies=[
            Body("box_a", "box", (140, 140), (900.0, 300.0), angle_deg=8.0),
            Body("box_b", "box", (140, 140), (960.0, 120.0), angle_deg=-5.0),
            Body("ball", "circle", (70, 70), (1120.0, 200.0),
                 velocity=(-180.0, 0.0)),
        ],
    )


def test_determinism():
    print("\n2. DETERMINISM  (a bake that differs between runs is unusable)")
    a = bake_json(stack_scene())
    b = bake_json(stack_scene())
    check("two runs are byte-identical",
          a == b, f"{len(a)} bytes, identical")

    perturbed = stack_scene()
    x, y = perturbed.bodies[0].position
    perturbed.bodies[0].position = (x + 1e-9, y)   # one nanopixel
    c = bake_json(perturbed)
    check("a 1e-9 px nudge IS detected (broken control)",
          c != a, "output differs, so the comparison can fail")


# --------------------------------------------------------------------------
# 3. Units  (Wall D)  -- the wall, measured rather than asserted
# --------------------------------------------------------------------------

def test_units():
    print("\n3. UNITS  (pixels_per_meter is a tuning knob, not bookkeeping)")
    print("      ppm     fall in 1s     rest sink     residual speed")
    print("      ----    -----------    ----------    --------------")
    rows = []
    for ppm in (1.0, 10.0, 100.0, 1000.0, 10000.0):
        ff = free_fall_scene(ppm=ppm, frames=24)
        _, t = simulate(ff)
        fall = t[0]["position"][-1][1][1] - t[0]["position"][0][1][1]

        sc = Scene(
            fps=24.0, duration_frames=200, pixels_per_meter=ppm,
            statics=[((200.0, FLOOR_Y), (1720.0, FLOOR_Y))],
            bodies=[Body("resting", "box", (140, 140), (960.0, 700.0))],
        )
        _, rt = simulate(sc)
        ys = [k[1][1] for k in rt[0]["position"]]
        # Ideal contact = floor - h/2 - segment radius (which is 0.5px by
        # construction, so it stays 0.5px at every ppm).
        sink = ys[-1] - (FLOOR_Y - 70.0 - 0.5)
        speed = abs(ys[-1] - ys[-2]) * sc.fps      # px/s over the last frame
        rows.append((ppm, fall, sink, speed))
        print(f"      {ppm:6.0f}  {fall:11.2f}    {sink:10.3f}    {speed:14.4f}")

    by_ppm = {r[0]: r for r in rows}

    # ppm sets the pace of the whole comp: gravity in px/s^2 IS 9.8*ppm, so
    # the time for anything to cross the frame scales as 1/sqrt(ppm).
    cross = {p: math.sqrt(2 * 1080.0 / (9.8 * p)) for p in by_ppm}
    print(f"      -> time to fall the height of a 1080px comp: "
          f"{cross[1.0]:.1f}s at ppm=1, {cross[100.0]:.2f}s at ppm=100")
    check("fall distance scales linearly with ppm",
          abs(by_ppm[100.0][1] / by_ppm[10.0][1] - 10.0) < 0.01,
          f"fall(ppm=100)/fall(ppm=10) = "
          f"{by_ppm[100.0][1] / by_ppm[10.0][1]:.4f} -- ppm is the clock, and "
          f"at ppm=1 a drop takes {cross[1.0]:.1f}s, which reads as underwater")

    # NOT what was predicted. collision_slop is 0.1 WORLD units, so the guess
    # was pixel sink growing linearly with ppm all the way up. It does not --
    # the solver resolves far tighter than slop, and sink is flat (~2px) from
    # ppm=100 to ppm=1000. The upper wall is real but sits two decades higher
    # than predicted, and it is precision, not slop.
    check("contact error stays within ~2px from ppm=1 to ppm=1000",
          all(abs(by_ppm[p][2]) < 2.5 for p in (1.0, 10.0, 100.0, 1000.0)),
          "sink " + ", ".join(f"{by_ppm[p][2]:+.2f}px@{p:.0f}"
                              for p in (1.0, 10.0, 100.0, 1000.0)))
    check("an upper wall does exist, but at ppm=10000",
          abs(by_ppm[10000.0][2]) > 5.0 * abs(by_ppm[1000.0][2]),
          f"sink jumps to {by_ppm[10000.0][2]:+.2f}px "
          f"({abs(by_ppm[10000.0][2] / by_ppm[1000.0][2]):.1f}x the ppm=1000 "
          "figure) -- visible interpenetration at last")
    return rows


# --------------------------------------------------------------------------
# 4. Rotation unwrapping  (Wall F)
# --------------------------------------------------------------------------

def test_rotation():
    print("\n4. ROTATION UNWRAP  (a double spin must bake as 720, not 0)")
    scene = Scene(
        fps=24.0, duration_frames=48, gravity_m_s2=0.0,
        bodies=[Body("spinner", "box", (200, 60), (960.0, 540.0),
                     angular_velocity_deg=360.0)],
    )
    _, tracks = simulate(scene)
    degs = [k[1] for k in tracks[0]["rotation"]]
    final = degs[-1]
    check("two full turns bake as ~720 deg",
          abs(final - 720.0) < 1.0, f"final rotation = {final:.3f} deg")
    check("the raw wrapped angle would NOT (broken control)",
          abs(((final + 180.0) % 360.0) - 180.0) < 1.0,
          f"wrapped equivalent = {((final + 180.0) % 360.0) - 180.0:.3f} deg")
    check("rotation is monotonic (no snap-backs)",
          all(b >= a for a, b in zip(degs, degs[1:])),
          f"{len(degs)} samples, strictly non-decreasing")
    return degs


# --------------------------------------------------------------------------
# 5. Anchor offset  (Wall E, previewing A2)
# --------------------------------------------------------------------------

def test_anchor():
    print("\n5. ANCHOR OFFSET  (AE Position is the ANCHOR, not the COM)")
    off = (100.0, 0.0)
    scene = Scene(
        fps=24.0, duration_frames=48, gravity_m_s2=0.0,
        bodies=[
            Body("com_anchor", "box", (200, 60), (960.0, 540.0),
                 angular_velocity_deg=360.0),
            Body("off_anchor", "box", (200, 60), (960.0, 540.0),
                 angular_velocity_deg=360.0, anchor_offset=off),
        ],
    )
    _, tracks = simulate(scene)
    com = [k[1] for k in tracks[0]["position"]]
    anc = [k[1] for k in tracks[1]["position"]]

    radii = [math.dist(a, c) for a, c in zip(anc, com)]
    check("offset anchor orbits the COM at |offset|",
          max(abs(r - 100.0) for r in radii) < 1e-6,
          f"radius stays {min(radii):.6f}..{max(radii):.6f} px (want 100)")
    check("a COM anchor does not orbit (broken control)",
          max(math.dist(c, com[0]) for c in com) < 1e-6,
          "zero-offset body holds still while spinning")

    # y-down means a positive angle reads CLOCKWISE, which is AE's sense.
    quarter = anc[6]           # 6 frames at 360 deg/s = 90 deg
    check("+90 deg sends a +x anchor to screen-DOWN (AE rotation sense)",
          abs(quarter[0] - 960.0) < 1.0 and quarter[1] - 540.0 > 99.0,
          f"anchor at {quarter[0]:.2f}, {quarter[1]:.2f} "
          "-- started 100px right, now 100px below")
    return com, anc


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------

def plot(unit_rows, degs, com, anc):
    scene = stack_scene()
    _, tracks = simulate(scene)

    fig, ax = plt.subplots(2, 2, figsize=(13, 9))

    a = ax[0][0]
    for layer, trk in zip(scene.bodies, tracks):
        xs = [k[1][0] for k in trk["position"]]
        ys = [k[1][1] for k in trk["position"]]
        a.plot(xs, ys, lw=1.6, label=layer.name)
        a.plot(xs[0], ys[0], "o", ms=5)
    a.axhline(FLOOR_Y, color="k", lw=2)
    a.set_xlim(0, 1920)
    a.set_ylim(1080, 0)
    a.set_title("A1: baked trajectories (comp space, y down)")
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    a = ax[0][1]
    ppms = [r[0] for r in unit_rows]
    a.plot(ppms, [abs(r[2]) for r in unit_rows], "o-", label="rest sink (px)")
    a.plot(ppms, [r[1] for r in unit_rows], "s--", label="fall in 1s (px)")
    a.set_xscale("log")
    a.set_yscale("log")
    a.set_xlabel("pixels per meter")
    a.set_title("Wall D: too low is sluggish, too high sinks")
    a.legend(fontsize=8)
    a.grid(alpha=.3, which="both")

    a = ax[1][0]
    a.plot(degs, lw=1.8, label="unwrapped")
    a.plot([((d + 180) % 360) - 180 for d in degs], lw=1.2, ls="--",
           label="raw wrapped (what AE would snap to)")
    a.set_xlabel("frame")
    a.set_ylabel("degrees")
    a.set_title("Wall F: rotation must accumulate")
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    a = ax[1][1]
    a.plot([p[0] for p in com], [p[1] for p in com], "o", ms=7, label="COM")
    a.plot([p[0] for p in anc], [p[1] for p in anc], lw=1.6,
           label="anchor (offset 100px)")
    a.set_aspect("equal")
    a.invert_yaxis()
    a.set_title("Wall E: AE Position tracks the anchor")
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    fig.tight_layout()
    fig.savefig("a1_checks.png", dpi=110)
    print("\n   wrote a1_checks.png")


if __name__ == "__main__":
    print("A1 -- world + bake spine, on primitives")
    test_free_fall()
    test_determinism()
    rows = test_units()
    degs = test_rotation()
    com, anc = test_anchor()
    plot(rows, degs, com, anc)

    with open("a1_bake.json", "w") as fh:
        fh.write(bake_json(stack_scene()))
    print("   wrote a1_bake.json")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")

"""
A3 -- bezier paths to bodies (Walls B and C, on the easy input).

Pipeline:  AE Shape  ->  flatten  ->  simplify  ->  convex parts  ->  PolyBody

The strongest check here is an invariance one. A2 built its L by hand as two
convex rectangles and measured a COM of (60, 160). A3 builds the same L as a
single concave path, flattens it, and lets ear clipping plus Hertel-Mehlhorn
cut it up however they like. Two completely different routes, and the mass
properties have to agree -- decomposition is not allowed to move the COM.

Holes are deliberately NOT handled here. A ring needs its inner contour bridged
into the outer one before it can be triangulated, and the plan puts that in A4
alongside alpha contours, where holes and islands arrive together.

Run:  python a3_paths.py
"""

from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import aepath
import decompose
import geom
from sim import PolyBody, Scene, replay_from_keyframes, simulate_traced

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# The A2 L, this time as ONE concave path in layer space.
L_PATH = aepath.polygon_path([
    (0.0, 0.0), (40.0, 0.0), (40.0, 200.0),
    (200.0, 200.0), (200.0, 240.0), (0.0, 240.0),
])
L_COM_FROM_A2 = (60.0, 160.0)
L_AREA_FROM_A2 = 16000.0


def star_path(outer=160.0, inner=64.0, points=5, center=(0.0, 0.0)) -> dict:
    v = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        a = math.pi * i / points - math.pi / 2
        v.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
    return aepath.polygon_path(v)


# --------------------------------------------------------------------------
# 1. Flattening
# --------------------------------------------------------------------------

def test_flatten():
    print("\n1. FLATTENING  (a circle, whose answer we know exactly)")
    r = 200.0
    path = aepath.circle_path(r)
    true_area = math.pi * r * r

    print("       tol     verts    area error    worst vertex radial error")
    print("      -----    -----    ----------    -------------------------")
    rows = []
    for tol in (4.0, 1.0, 0.25, 0.05, 0.01):
        poly = aepath.flatten_path(path, tol=tol)
        area = abs(geom.signed_area(poly))
        radial = max(abs(math.hypot(*p) - r) for p in poly)
        rows.append((tol, len(poly), area, radial))
        print(f"      {tol:5.2f}    {len(poly):5d}    {true_area - area:10.4f}"
              f"    {radial:25.6f}")

    # Two different errors, and they behave differently -- worth separating.
    # Flattening vertices lie exactly ON the bezier, so their radial error is
    # the four-arc bezier's own circle approximation and does not improve with
    # tol. What tol buys is chord sagitta, i.e. AREA.
    #
    # MEASURED: 0.0545px at r=200, i.e. 2.72e-4 of the radius. That is the
    # known kappa-circle figure of 0.027% -- the first guess here was 0.02%,
    # which is simply the wrong constant.
    bezier_limit = 3.0e-4 * r
    check("vertex radial error is the bezier's, not the flattener's",
          all(abs(rw[3]) < bezier_limit for rw in rows)
          and rows[0][3] / rows[-1][3] < 2.0,
          f"radial error {rows[0][3]:.4f}px at tol=4 -> {rows[-1][3]:.4f}px at "
          f"tol=0.01, i.e. {rows[-1][3] / r:.2e} of r -- the kappa circle's own "
          "0.027%, and 400x finer flattening does not touch it")
    # Measure convergence of the FLATTENER, against the finest run rather than
    # against pi*r^2 -- the bezier's own constant offset (it converges to
    # pi*r^2 + 32px^2 here) would otherwise sit under everything as a floor,
    # and the deficit even changes sign at tol=0.25.
    conv = rows[-1][2]
    errs = [(rw[0], abs(rw[2] - conv)) for rw in rows[:-1]]
    ratio = errs[0][1] / errs[-1][1]          # tol 4.0 vs 0.05, an 80x span
    check("flattening area error is linear in tol",
          20.0 < ratio < 320.0,
          f"error {errs[0][1]:.1f}px^2 at tol={errs[0][0]:g} -> "
          f"{errs[-1][1]:.2f}px^2 at tol={errs[-1][0]:g}, a {ratio:.0f}x drop "
          "across an 80x tol range")
    check("a flattened circle is convex (broken control is the star)",
          decompose.is_convex(aepath.flatten_path(path, tol=0.25))
          and not decompose.is_convex(
              aepath.flatten_path(star_path(), tol=0.25)),
          "circle convex, star not -- the convexity test can tell them apart")
    return rows


def test_tangent_convention():
    print("\n2. TANGENTS  (AE stores them RELATIVE to their vertex)")
    r = 200.0
    path = aepath.circle_path(r)
    good = abs(geom.signed_area(aepath.flatten_path(path, tol=0.25)))
    bad = abs(geom.signed_area(
        aepath.flatten_path(path, tol=0.25, absolute_tangents=True)))
    true_area = math.pi * r * r
    # The residual is the kappa circle's own area error (it converges to
    # pi*r^2 + 32px^2 at r=200, about 2.5e-4 relative), not a flattening bug.
    check("relative tangents reproduce the circle's area",
          abs(good - true_area) / true_area < 1e-3,
          f"{good:.2f} vs pi*r^2 = {true_area:.2f} "
          f"(rel {abs(good - true_area) / true_area:.2e} -- the bezier's own "
          "approximation, not the flattener's)")
    check("reading tangents as absolute wrecks it (broken control)",
          abs(bad - true_area) / true_area > 0.5,
          f"area collapses to {bad:.2f}, {bad / true_area * 100:.1f}% of "
          "the truth -- the shape implodes toward the origin")


# --------------------------------------------------------------------------
# 3. Simplification
# --------------------------------------------------------------------------

def test_simplify():
    print("\n3. SIMPLIFY  (does RDP honour the tolerance it was given?)")
    poly = aepath.flatten_path(aepath.circle_path(200.0), tol=0.01)
    print("       tol     verts    measured max deviation")
    print("      -----    -----    ----------------------")
    ok = True
    for tol in (0.5, 2.0, 8.0):
        s = aepath.simplify_closed(poly, tol)
        dev = aepath.max_deviation(poly, s)
        ok = ok and dev <= tol + 1e-9
        print(f"      {tol:5.2f}    {len(s):5d}    {dev:22.6f}")
    s2 = aepath.simplify_closed(poly, 2.0)
    check("measured deviation never exceeds the tolerance",
          ok, "checked at tol = 0.5, 2.0, 8.0")
    check("simplification actually removes vertices",
          len(s2) < len(poly) / 2,
          f"{len(poly)} -> {len(s2)} vertices at tol=2.0")


# --------------------------------------------------------------------------
# 4. Decomposition, and the A2 cross-check
# --------------------------------------------------------------------------

def test_decompose():
    print("\n4. DECOMPOSE  (ear clip + Hertel-Mehlhorn)")
    shapes = {
        "L": aepath.flatten_path(L_PATH),
        "star": aepath.flatten_path(star_path()),
        "circle": aepath.simplify_closed(
            aepath.flatten_path(aepath.circle_path(200.0)), 1.0),
    }
    worst_area, worst_aspect, counts = 0.0, 0.0, {}
    for name, poly in shapes.items():
        tris = decompose.triangulate(poly)
        parts = decompose.convex_parts(poly)
        counts[name] = (len(poly), len(tris), len(parts))

        a_poly = abs(geom.signed_area(poly))
        a_tris = sum(abs(geom.signed_area(t)) for t in tris)
        a_part = sum(abs(geom.signed_area(p)) for p in parts)
        worst_area = max(worst_area,
                         abs(a_tris - a_poly) / a_poly,
                         abs(a_part - a_poly) / a_poly)
        worst_aspect = max(worst_aspect,
                           max(decompose.aspect_ratio(p) for p in parts))
        print(f"      {name:7} {len(poly):4d} verts -> {len(tris):3d} tris "
              f"-> {len(parts):3d} convex parts")

    check("triangles and parts both preserve area exactly",
          worst_area < 1e-12,
          f"worst relative area error = {worst_area:.2e}")
    check("every output part is convex",
          all(decompose.is_convex(p)
              for poly in shapes.values()
              for p in decompose.convex_parts(poly)),
          "checked for all three shapes")
    check("merging beats raw triangulation on part count",
          all(c[2] < c[1] for c in counts.values()),
          ", ".join(f"{k}: {v[1]}->{v[2]}" for k, v in counts.items()))
    check("no slivers among the parts",
          worst_aspect < 60.0,
          f"worst aspect ratio = {worst_aspect:.1f} "
          "(a sliver would destabilise contact normals)")

    # The invariance check: same L, two unrelated routes to its mass.
    parts = decompose.convex_parts(shapes["L"])
    mass, com, moment = geom.compound_mass_properties(parts, 1.0)
    direct = geom.mass_properties(shapes["L"], 1.0)
    check("decomposed L has A2's hand-built COM",
          math.dist(com, L_COM_FROM_A2) < 1e-9 and abs(mass - L_AREA_FROM_A2) < 1e-9,
          f"COM ({com[0]:.9f}, {com[1]:.9f}) and area {mass:.6f} from "
          f"{len(parts)} auto-generated parts, vs (60, 160) / 16000 from A2's "
          "two hand-placed rectangles")
    check("decomposition does not move the moment either",
          abs(moment - direct[2]) / direct[2] < 1e-12,
          f"compound {moment:.6f} vs whole-polygon {direct[2]:.6f} "
          f"(rel {abs(moment - direct[2]) / direct[2]:.2e})")
    return shapes


# --------------------------------------------------------------------------
# 5. End to end: decomposed paths become bodies, and the A2 replay still holds
# --------------------------------------------------------------------------

def pile_scene(shapes, frames=110):
    bodies = []
    for i, (name, poly) in enumerate(shapes.items()):
        parts = decompose.convex_parts(poly)
        bodies.append(PolyBody(
            f"{name}_layer", parts, anchor=(0.0, 0.0),
            position=(560.0 + i * 380.0, 180.0 + i * 60.0),
            angle_deg=17.0 * (i + 1),
            angular_velocity_deg=90.0 * (1 if i % 2 else -1),
        ))
    return Scene(fps=24.0, duration_frames=frames, bodies=bodies,
                 statics=[((100.0, 950.0), (1820.0, 950.0)),
                          ((100.0, 950.0), (100.0, 400.0)),
                          ((1820.0, 950.0), (1820.0, 400.0))])


def test_end_to_end(shapes):
    print("\n5. END TO END  (decomposed paths as bodies; A2's replay must hold)")
    scene = pile_scene(shapes)
    handles, tracks, traces = simulate_traced(scene)

    worst = 0.0
    for f in range(scene.duration_frames + 1):
        for h, trk, tr in zip(handles, tracks, traces):
            for got, want in zip(replay_from_keyframes(h, trk, f), tr[f]):
                for a, b in zip(got, want):
                    worst = max(worst, math.dist(a, b))
    nparts = sum(len(h.parts_local) for h in handles)
    check("replay still matches with multi-part bodies",
          worst < 1e-3,
          f"worst vertex error = {worst:.3e} px across {nparts} convex parts "
          f"on {len(handles)} bodies")

    settled = all(
        abs(trk["position"][-1][1][1] - trk["position"][-3][1][1]) < 2.0
        for trk in tracks)
    check("the pile comes to rest inside the box",
          settled and all(200 < trk["position"][-1][1][1] < 1000
                          for trk in tracks),
          "every body settled and stayed in frame -- no tunnelling, no "
          "explosion from a bad decomposition")
    return scene, handles, tracks, traces


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------

def fill_parts(ax, parts, cmap="tab20", alpha=.75, edge="k"):
    cm = plt.get_cmap(cmap)
    for i, p in enumerate(parts):
        ax.fill([v[0] for v in p], [v[1] for v in p],
                color=cm(i % 20), alpha=alpha, ec=edge, lw=.8)


def plot(flat_rows, shapes, scene, handles, tracks):
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))

    a = ax[0][0]
    conv = flat_rows[-1][2]
    a.loglog([r[0] for r in flat_rows[:-1]],
             [abs(r[2] - conv) for r in flat_rows[:-1]],
             "o-", label="area error vs converged (px^2)")
    a.loglog([r[0] for r in flat_rows], [r[3] for r in flat_rows],
             "s--", label="vertex radial error (px)")
    a.set_xlabel("flatten tolerance (px)")
    a.set_title("Flattening: tol buys area, not vertex accuracy")
    a.legend(fontsize=8)
    a.grid(alpha=.3, which="both")

    a = ax[0][1]
    parts = decompose.convex_parts(shapes["star"])
    fill_parts(a, parts)
    sp = shapes["star"]
    a.plot([v[0] for v in sp] + [sp[0][0]], [v[1] for v in sp] + [sp[0][1]],
           color="k", lw=2)
    a.set_aspect("equal")
    a.invert_yaxis()
    a.set_title(f"Star: {len(sp)} verts into {len(parts)} convex parts")
    a.grid(alpha=.3)

    a = ax[1][0]
    lparts = decompose.convex_parts(shapes["L"])
    fill_parts(a, lparts)
    _m, com, _i = geom.compound_mass_properties(lparts, 1.0)
    a.plot(*com, "o", ms=11, color="crimson",
           label=f"COM ({com[0]:.0f}, {com[1]:.0f}) — matches A2")
    a.set_aspect("equal")
    a.invert_yaxis()
    a.set_title(f"L auto-decomposed into {len(lparts)} parts")
    a.legend(fontsize=8)
    a.grid(alpha=.3)

    a = ax[1][1]
    for f in (0, scene.duration_frames):
        for h, trk in zip(handles, tracks):
            fill_parts(a, replay_from_keyframes(h, trk, f),
                       alpha=.28 if f == 0 else .85)
    a.axhline(950, color="k", lw=2)
    a.axvline(100, color="k", lw=2)
    a.axvline(1820, color="k", lw=2)
    a.set_xlim(60, 1860)
    a.set_ylim(1010, 0)
    a.set_aspect("equal")
    a.set_title("First frame (faint) and last, replayed from keyframes")
    a.grid(alpha=.3)

    fig.tight_layout()
    fig.savefig("a3_checks.png", dpi=110)
    print("\n   wrote a3_checks.png")


if __name__ == "__main__":
    print("A3 -- bezier paths to bodies")
    flat_rows = test_flatten()
    test_tangent_convention()
    test_simplify()
    shapes = test_decompose()
    scene, handles, tracks, traces = test_end_to_end(shapes)
    plot(flat_rows, shapes, scene, handles, tracks)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")

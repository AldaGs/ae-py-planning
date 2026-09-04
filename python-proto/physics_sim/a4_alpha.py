"""
A4 -- rendered alpha to bodies (Wall B, on the hard input).

Input is four real AE render-queue exports in png-ae-exports/, and they are a
better test set than anything synthetic would have been:

    Circ    a disc with an enclosed hole AND a notch open to the boundary,
            plus three separate satellite islands
    Penta   a pentagon ring -- pure hole case
    S       one deeply concave blob, two hooks
    Star    a rounded star, concave

Pipeline: alpha -> marching squares -> nesting -> simplify -> bridge holes
-> convex parts -> one body per layer.

The luxury here is that the image is its own ground truth. Contour area can be
checked against the pixel count and the contour centroid against the mask
centroid, both computed without going near the contour code.

Run:  python a4_alpha.py
"""

from __future__ import annotations

import glob
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from scipy import ndimage

import aepath
import alpha_contours as ac
import decompose
import geom
from sim import PolyBody, Scene, replay_from_keyframes, simulate_traced

THRESHOLD = 128.0
SIMPLIFY_TOL = 1.0

# A layer whose islands raise its moment of inertia by more than this over its
# largest island alone is welded hard enough that the user should be told.
GLUE_WARN = 1.25
EXPORTS = "png-ae-exports"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def load_alpha(path: str) -> np.ndarray:
    return np.array(Image.open(path).convert("RGBA"))[..., 3].astype(float)


def files() -> list[str]:
    return sorted(f for f in glob.glob(os.path.join(EXPORTS, "*.png"))
                  if not os.path.basename(f).startswith("_"))


def truth(alpha: np.ndarray) -> dict:
    """Ground truth straight from the pixels, touching no contour code."""
    solid = alpha >= THRESHOLD
    lab, n_isl = ndimage.label(solid)
    hl, hn = ndimage.label(~solid)
    border = set(np.unique(np.concatenate(
        [hl[0, :], hl[-1, :], hl[:, 0], hl[:, -1]])))
    holes = [c for c in range(1, hn + 1) if c not in border]
    ys, xs = np.where(solid)
    return {
        "islands": n_isl,
        "holes": len(holes),
        "area": float(solid.sum()),
        "com": (float(xs.mean()), float(ys.mean())),
    }


def build(alpha: np.ndarray, tol: float = SIMPLIFY_TOL):
    """alpha -> (convex parts for the whole layer, per-group contours)."""
    groups, stats = ac.contours_from_alpha(alpha, THRESHOLD, with_stats=True)
    parts, rings = [], []
    for outer, holes in groups:
        o = aepath.simplify_closed(outer, tol)
        hs = [aepath.simplify_closed(h, tol) for h in holes]
        ring = ac.bridge_holes(o, hs) if hs else o
        rings.append((o, hs))
        parts.extend(decompose.convex_parts(ring))
    return parts, rings, groups, stats


# --------------------------------------------------------------------------
# 1. Topology, against an independent labelling
# --------------------------------------------------------------------------

def test_topology():
    print("\n1. TOPOLOGY  (contours vs a labelling that never sees them)")
    print("      file         islands  holes      truth       stats")
    print("      --------     -------  -----   -----------   ------------")
    ok = True
    for f in files():
        a = load_alpha(f)
        groups, stats = ac.contours_from_alpha(a, THRESHOLD, with_stats=True)
        t = truth(a)
        nh = sum(len(h) for _o, h in groups)
        match = (len(groups) == t["islands"] and nh == t["holes"])
        ok = ok and match
        print(f"      {os.path.basename(f):12} {len(groups):7d}  {nh:5d}   "
              f"{t['islands']} isl {t['holes']} hole   "
              f"{stats['collisions']} coll {stats['open_chains']} open")
    check("island and hole counts match the labelling exactly",
          ok, "all four exports, including Circ's 4 islands + 1 hole")
    check("no degenerate samples survive",
          all(ac.contours_from_alpha(load_alpha(f), THRESHOLD,
                                     with_stats=True)[1]["collisions"] == 0
              and ac.contours_from_alpha(load_alpha(f), THRESHOLD,
                                         with_stats=True)[1]["open_chains"] == 0
              for f in files()),
          "0 collisions and 0 open chains across all four")


def test_degeneracy_control():
    print("\n2. DEGENERACY  (the bug that fails silently)")
    a = load_alpha(os.path.join(EXPORTS, "Star.png"))
    good = ac.contours_from_alpha(a, THRESHOLD, with_stats=True)[1]
    bad = ac.contours_from_alpha(a, THRESHOLD, with_stats=True, nudge=False)[1]
    exact = int((a == THRESHOLD).sum())
    check("without the nudge, the contour shatters (broken control)",
          bad["collisions"] > 0 and bad["loops"] > 20 * good["loops"],
          f"{exact} pixels of alpha==128 cause {bad['collisions']} key "
          f"collisions, {bad['open_chains']} open chains and "
          f"{bad['loops']} loops instead of {good['loops']} -- and nothing "
          "raises")


# --------------------------------------------------------------------------
# 3. The image as its own ground truth
# --------------------------------------------------------------------------

def test_ground_truth():
    print("\n3. GROUND TRUTH  (the pixels already know the answer)")
    print("      file         contour area   pixel area    rel      COM err")
    print("      --------     ------------   ----------   ------   -------")
    worst_area, worst_com = 0.0, 0.0
    for f in files():
        a = load_alpha(f)
        groups = ac.contours_from_alpha(a, THRESHOLD)
        t = truth(a)
        area = ac.net_area(groups)
        rel = abs(area - t["area"]) / t["area"]

        # Mass properties straight from the contours: outer minus holes, and
        # islands area-weighted. No decomposition -- it is not needed for this
        # and ear clipping on a raw 800-vertex contour is O(n^3).
        per = [geom.with_holes_mass_properties(o, hs, 1.0) for o, hs in groups]
        tot = sum(m for m, _c, _i in per)
        com = (sum(m * c[0] for m, c, _i in per) / tot,
               sum(m * c[1] for m, c, _i in per) / tot)
        cerr = math.dist(com, t["com"])

        worst_area = max(worst_area, rel)
        worst_com = max(worst_com, cerr)
        print(f"      {os.path.basename(f):12} {area:12.1f}   {t['area']:10.0f}"
              f"   {rel:6.4f}   {cerr:7.3f}")

    check("contour area matches the pixel count",
          worst_area < 0.002,
          f"worst relative area error = {worst_area:.5f} across four exports")
    check("contour centroid matches the mask centroid",
          worst_com < 0.5,
          f"worst COM error = {worst_com:.3f} px -- sub-pixel, from the "
          "interpolated iso-line using the AA band")


def test_holes_and_islands_matter():
    print("\n4. WHAT HOLES AND ISLANDS ARE WORTH  (broken controls)")
    a = load_alpha(os.path.join(EXPORTS, "Penta.png"))
    groups = ac.contours_from_alpha(a, THRESHOLD)
    outer_only = sum(abs(geom.signed_area(o)) for o, _h in groups)
    net = ac.net_area(groups)
    check("ignoring the hole inflates a ring's mass (broken control)",
          (outer_only - net) / net > 0.5,
          f"Penta would be {outer_only:.0f} instead of {net:.0f}, "
          f"{(outer_only / net - 1) * 100:.0f}% too heavy, and its COM would "
          "sit in the hole")

    a = load_alpha(os.path.join(EXPORTS, "Circ.png"))
    groups = ac.contours_from_alpha(a, THRESHOLD)
    net = ac.net_area(groups)
    biggest = max(abs(geom.signed_area(o)) - sum(abs(geom.signed_area(h))
                                                 for h in hs)
                  for o, hs in groups)
    check("keeping only the largest island loses real mass (broken control)",
          (net - biggest) / net > 0.1,
          f"Circ's 3 satellites are {(net - biggest) / net * 100:.1f}% of the "
          f"layer -- {len(groups)} islands stay on ONE body, because one layer "
          "is one transform")


# --------------------------------------------------------------------------
# 5. Simplify and decompose
# --------------------------------------------------------------------------

def test_pipeline():
    print("\n5. PIPELINE  (simplify -> bridge -> convex parts)")
    print("      file         verts    ->simp   parts   area kept   worst asp"
          "   sliver mass")
    print("      --------    ------   -------   -----   ---------   ---------"
          "   -----------")
    all_convex, worst_decomp, worst_asp, worst_sliver = True, 0.0, 0.0, 0.0
    for f in files():
        a = load_alpha(f)
        groups = ac.contours_from_alpha(a, THRESHOLD)
        raw_v = sum(len(o) + sum(len(h) for h in hs) for o, hs in groups)

        parts, rings, _g, _s = build(a)
        simp_v = sum(len(o) + sum(len(h) for h in hs) for o, hs in rings)
        ring_net = sum(abs(geom.signed_area(o))
                       - sum(abs(geom.signed_area(h)) for h in hs)
                       for o, hs in rings)
        part_area = sum(abs(geom.signed_area(p)) for p in parts)

        all_convex = all_convex and all(decompose.is_convex(p) for p in parts)
        worst_decomp = max(worst_decomp,
                           abs(part_area - ring_net) / ring_net)
        asp = max(decompose.aspect_ratio(p) for p in parts)
        worst_asp = max(worst_asp, asp)
        sliver_mass = sum(abs(geom.signed_area(p)) for p in parts
                          if decompose.aspect_ratio(p) > 60.0) / part_area
        worst_sliver = max(worst_sliver, sliver_mass)
        kept = part_area / ac.net_area(groups)
        print(f"      {os.path.basename(f):12} {raw_v:6d}   {simp_v:7d}   "
              f"{len(parts):5d}   {kept * 100:8.2f}%   {asp:9.1f}"
              f"   {sliver_mass * 100:8.2f}%")

    check("decomposition preserves the simplified ring's area exactly",
          worst_decomp < 1e-12,
          f"worst relative error = {worst_decomp:.2e} -- the area lost is "
          "RDP's, not the decomposition's")
    check("every part of every shape is convex", all_convex,
          "including both bridged rings")
    # A3's synthetic shapes gave a worst aspect of 27.9; real contours give
    # ~146, and raising the HM merge cap from 8 to 24 does not move it at all
    # (it only cuts part counts). Slivers are inherent to ear clipping on a
    # densely sampled smooth curve -- their neighbours cannot merge with them
    # without going concave. So the honest question is not "are there slivers"
    # but "do they carry any mass", and they do not.
    check("slivers exist on real contours but are mass-negligible",
          worst_asp > 60.0 and worst_sliver < 0.02,
          f"worst aspect {worst_asp:.1f} (vs 27.9 on A3's synthetic shapes), "
          f"but parts above aspect 60 hold only {worst_sliver * 100:.2f}% of "
          "the mass -- flagged as a Phase C stability risk, not solved here")

    # What simplification itself costs, measured rather than assumed.
    a = load_alpha(os.path.join(EXPORTS, "S.png"))
    groups = ac.contours_from_alpha(a, THRESHOLD)
    raw = groups[0][0]
    print("       tol    verts   area kept   max deviation")
    print("      -----   -----   ---------   -------------")
    for tol in (0.25, 1.0, 4.0):
        s = aepath.simplify_closed(raw, tol)
        dev = aepath.max_deviation(raw, s)
        print(f"      {tol:5.2f}   {len(s):5d}   "
              f"{abs(geom.signed_area(s)) / abs(geom.signed_area(raw)) * 100:8.2f}%"
              f"   {dev:13.4f}")
    s1 = aepath.simplify_closed(raw, 1.0)
    check("simplification honours its tolerance on real contours",
          aepath.max_deviation(raw, s1) <= 1.0 + 1e-9,
          f"{len(raw)} -> {len(s1)} verts at tol=1.0, max deviation "
          f"{aepath.max_deviation(raw, s1):.4f} px")


def glue_warning(rings) -> tuple[dict, str | None]:
    """The report, plus the sentence the UI should show -- or None if quiet.

    This is the surface Phase B and C call. Islands are welded because one AE
    layer has one transform, which is a property of the OUTPUT format, not of
    the physics -- so the user gets told when it costs something.
    """
    r = geom.island_glue(rings)
    if r["islands"] < 2 or r["inertia_ratio"] < GLUE_WARN:
        return r, None
    return r, (
        f"{r['islands']} disconnected pieces are welded into one rigid body "
        f"(an AE layer has one Position and one Rotation). That is "
        f"{r['mass_ratio']:.2f}x the mass of the largest piece but "
        f"{r['inertia_ratio']:.2f}x its moment of inertia, and moves the "
        f"centre of mass {r['com_shift']:.1f} px. Split the layer in AE if "
        "the pieces should move independently."
    )


def test_island_glue():
    print("\n7. ISLAND GLUE  (one layer is one transform -- what does that cost?)")
    print("      layer    islands   mass x   inertia x   COM shift   gyration")
    print("      ------   -------   ------   ---------   ---------   --------")
    reports = {}
    for f in files():
        name = os.path.splitext(os.path.basename(f))[0]
        _p, rings, _g, _s = build(load_alpha(f))
        r, warn = glue_warning(rings)
        reports[name] = (r, warn)
        print(f"      {name:<6}   {r['islands']:7d}   {r['mass_ratio']:6.2f}   "
              f"{r['inertia_ratio']:9.2f}   {r['com_shift']:7.1f} px   "
              f"{r['largest_gyration']:.1f} -> {r['gyration']:.1f} px")

    warned = [n for n, (_r, w) in reports.items() if w]
    check("only the multi-island layer warns",
          warned == ["Circ"],
          f"warned: {warned or 'none'}; the three single-island layers report "
          "a ratio of exactly 1.00, so the check is not warning on everything")

    circ = reports["Circ"][0]
    check("mass ratio would have called this a 22% effect; inertia says 115%",
          circ["inertia_ratio"] > 2.0 > circ["mass_ratio"],
          f"Circ's satellites are {circ['mass_ratio']:.2f}x the mass but "
          f"{circ['inertia_ratio']:.2f}x the moment -- parallel axis is "
          "quadratic in distance, so a little mass far out dominates. "
          f"Radius of gyration {circ['largest_gyration']:.1f} -> "
          f"{circ['gyration']:.1f} px: the layer spins visibly slower under "
          "the same torque than the disc alone would")
    print(f"\n      WARNING for Circ: {reports['Circ'][1]}")


def test_specks():
    print("\n6. SPECKS  (a stray pixel is a body the solver thinks about)")
    a = load_alpha(os.path.join(EXPORTS, "Star.png")).copy()
    for (y, x) in ((8, 8), (8, 290), (292, 8)):
        a[y:y + 2, x:x + 2] = 255.0
    kept = ac.contours_from_alpha(a, THRESHOLD, min_area=12.0)
    unfiltered = ac.contours_from_alpha(a, THRESHOLD, min_area=0.0)
    check("the min-area filter drops specks and keeps the shape",
          len(unfiltered) == 4 and len(kept) == 1,
          f"3 injected 2x2 specks give {len(unfiltered)} groups unfiltered, "
          f"{len(kept)} after filtering")


# --------------------------------------------------------------------------
# 8. End to end
# --------------------------------------------------------------------------

def scene_from_exports(frames=120):
    bodies, partsets = [], {}
    for i, f in enumerate(files()):
        name = os.path.splitext(os.path.basename(f))[0]
        parts, _r, _g, _s = build(load_alpha(f))
        partsets[name] = parts
        bodies.append(PolyBody(
            name, parts, anchor=(150.0, 150.0),
            position=(420.0 + i * 360.0, 170.0 + (i % 2) * 90.0),
            angle_deg=20.0 * i,
            angular_velocity_deg=70.0 * (1 if i % 2 else -1),
        ))
    scene = Scene(fps=24.0, duration_frames=frames, bodies=bodies,
                  statics=[((80.0, 980.0), (1840.0, 980.0)),
                           ((80.0, 980.0), (80.0, 300.0)),
                           ((1840.0, 980.0), (1840.0, 300.0))])
    return scene, partsets


def test_end_to_end():
    print("\n8. END TO END  (four real layers, simulated and replayed)")
    scene, _p = scene_from_exports()
    handles, tracks, traces = simulate_traced(scene)
    worst = 0.0
    for fr in range(scene.duration_frames + 1):
        for h, trk, tr in zip(handles, tracks, traces):
            for got, want in zip(replay_from_keyframes(h, trk, fr), tr[fr]):
                for p, q in zip(got, want):
                    worst = max(worst, math.dist(p, q))
    nparts = sum(len(h.parts_local) for h in handles)
    check("replay matches the solver for alpha-derived bodies",
          worst < 1e-3,
          f"worst vertex error = {worst:.3e} px over {nparts} convex parts "
          f"on {len(handles)} bodies")
    settled = all(200 < trk["position"][-1][1][1] < 1050 for trk in tracks)
    check("everything stays in the box",
          settled,
          "no tunnelling and no decomposition blow-ups over "
          f"{scene.duration_frames} frames")
    return scene, handles, tracks


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------

def plot(scene, handles, tracks):
    fig = plt.figure(figsize=(14, 10))
    fs = files()

    for i, f in enumerate(fs):
        a = load_alpha(f)
        groups = ac.contours_from_alpha(a, THRESHOLD)
        ax = fig.add_subplot(3, 4, i + 1)
        ax.imshow(a, cmap="gray", vmin=0, vmax=255)
        for outer, holes in groups:
            ax.plot([p[0] for p in outer] + [outer[0][0]],
                    [p[1] for p in outer] + [outer[0][1]],
                    color="tab:green", lw=1.6)
            for h in holes:
                ax.plot([p[0] for p in h] + [h[0][0]],
                        [p[1] for p in h] + [h[0][1]],
                        color="tab:red", lw=1.6)
        ax.set_title(f"{os.path.basename(f)}: {len(groups)} isl, "
                     f"{sum(len(h) for _o, h in groups)} hole", fontsize=9)
        ax.axis("off")

    cm = plt.get_cmap("tab20")
    for i, f in enumerate(fs):
        parts, _r, _g, _s = build(load_alpha(f))
        ax = fig.add_subplot(3, 4, 5 + i)
        for k, p in enumerate(parts):
            ax.fill([v[0] for v in p], [v[1] for v in p],
                    color=cm(k % 20), alpha=.85, ec="k", lw=.5)
        ax.set_aspect("equal")
        ax.invert_yaxis()
        ax.set_title(f"{len(parts)} convex parts", fontsize=9)
        ax.axis("off")

    ax = fig.add_subplot(3, 1, 3)
    for frame, alpha_v in ((0, .22), (scene.duration_frames, .9)):
        for h, trk in zip(handles, tracks):
            for k, p in enumerate(replay_from_keyframes(h, trk, frame)):
                ax.fill([v[0] for v in p], [v[1] for v in p],
                        color=cm(k % 20), alpha=alpha_v, ec="k", lw=.4)
    ax.axhline(980, color="k", lw=2)
    ax.axvline(80, color="k", lw=2)
    ax.axvline(1840, color="k", lw=2)
    ax.set_xlim(40, 1880)
    ax.set_ylim(1040, 0)
    ax.set_aspect("equal")
    ax.set_title("Four AE exports as rigid bodies: first frame (faint), last")
    ax.grid(alpha=.3)

    fig.tight_layout()
    fig.savefig("a4_checks.png", dpi=105)
    print("\n   wrote a4_checks.png")


if __name__ == "__main__":
    print("A4 -- rendered alpha to bodies, on real AE exports")
    test_topology()
    test_degeneracy_control()
    test_ground_truth()
    test_holes_and_islands_matter()
    test_pipeline()
    test_specks()
    test_island_glue()
    scene, handles, tracks = test_end_to_end()
    plot(scene, handles, tracks)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")

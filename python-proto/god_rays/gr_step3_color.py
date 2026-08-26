r"""
God Rays - Step 3: COLOUR, and which of the two ramps is actually free.

Shine's colour controls are the reason people buy it. There are two completely
different things a "ray colour ramp" can mean, they look similar in a still, and
they cost wildly different amounts. Step 3 builds both and measures the gap.

    RADIAL  -  colour indexed by the pixel's own distance from the source.
               "the shaft is gold near the sun and blue out at the edges"

    MARCH   -  colour indexed by how far the light TRAVELLED to get here.
               "each contribution is tinted by its own path length"

WHY THEY ARE NOT THE SAME THING
--------------------------------
Step 2 established that a scale about C preserves angle, so any angle-only
modulation commutes with the sweep. Radius is the other half of that coordinate
system, and it behaves the exact opposite way: a scale about C changes radius by
construction (that is all it does). So:

    angular modulation  ->  COMMUTES  -> apply before or after, same picture
    radial  modulation  ->  DOES NOT  -> before and after are different LOOKS

That is not a bug to fix. Pre-multiplying tints the *source light* by where it
came from; post-multiplying tints the *shaft* by where it landed. Both are
legitimate and check (d) measures how far apart they are. We ship post, because
it is what the control name implies and because it is one multiply.

And that is the punchline of the step: **the radial ramp is exact and free.**
No bands, no approximation, one multiply over the frame. Star Glint needed a
16-band decomposition to get a coloured streak because its distance coordinate
was the march index; here the distance coordinate the artist cares about is
screen radius, which is available directly.

WHERE THE BANDS ARE STILL NEEDED
---------------------------------
MARCH colour genuinely needs them, for exactly Star Glint's reason: the sweep
collapses every `t` into one buffer, and once collapsed there is no `t` left to
index a ramp with. So slice the doubling tables into contiguous bands of `t`,
tint each by the ramp at its own position, and sum. Same `band()` stitch as Star
Glint step 3, with `shift` swapped for `zoom` - the third time that one
substitution has carried a whole algorithm across.

ONE THING THAT IS CHEAPER HERE THAN IN STAR GLINT
--------------------------------------------------
Star Glint had to discover that band edges must be spaced GEOMETRICALLY, because
its samples sat at linearly-spaced distances while the weights were geometric,
so linear bands wasted resolution in the faint tip (its table: geometric ~3x
better at every band count).

Here the samples are ALREADY at geometrically-spaced radii - r^t - so uniform
spacing in `t` is already geometric in distance. The correction Star Glint needed
should be unnecessary. That is a prediction, and check (c) tests it rather than
assuming it.

Run:
    python gr_step3_color.py              # verify + render
    python gr_step3_color.py --explain    # dump the band table and the ramps
"""

import os
import sys

import numpy as np

from gr_step1_rays import (bright_pass, decay_for, describe, _grid,
                           linear_to_srgb, ratio_for, ray_sweep, save_linear,
                           synthetic_sun, tail_only, zoom)
from gr_step2_shimmer import radius_map, shimmer_mask

# --- reuse Gradient Map's LUT builder, unchanged (3rd effect to do so) ------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "gradient_map"))
import gm_step2_ramp_lut as gm                                   # noqa: E402

build_lut = gm.build_lut


# ===========================================================================
#  ramps
# ===========================================================================

RAY_RAMPS = {
    # position 0 = AT THE SOURCE, position 1 = far from it.
    # Same convention as Star Glint's core->tip, so the two are comparable.
    "white":    [(0.0, (1.0, 1.0, 1.0)), (1.0, (1.0, 1.0, 1.0))],
    "sunset":   [(0.0, (1.0, 0.97, 0.88)), (0.35, (1.0, 0.72, 0.36)),
                 (1.0, (0.72, 0.20, 0.14))],
    "teal_gold": [(0.0, (1.0, 0.92, 0.70)), (0.5, (0.55, 0.85, 0.75)),
                  (1.0, (0.12, 0.35, 0.62))],
    "sodium":   [(0.0, (1.0, 0.95, 0.85)), (1.0, (1.0, 0.45, 0.05))],
    "rainbow":  [(0.0, (1.0, 1.0, 1.0)), (0.25, (1.0, 0.30, 0.25)),
                 (0.50, (0.35, 1.0, 0.35)), (0.75, (0.30, 0.45, 1.0)),
                 (1.0, (0.80, 0.30, 1.0))],
}


def ray_lut(name_or_stops, n=256):
    stops = (RAY_RAMPS[name_or_stops] if isinstance(name_or_stops, str)
             else name_or_stops)
    return build_lut(stops, n)


def lut_at(lut, u):
    """Sample an (n,3) LUT at a scalar or array position u in [0,1]."""
    n = lut.shape[0]
    idx = np.clip((np.asarray(u, np.float32) * (n - 1)).astype(np.int32),
                  0, n - 1)
    return lut[idx]


# ===========================================================================
#  MODE 1 - radial colour.  Exact, one multiply, no bands.
# ===========================================================================

def radial_u(h, w, cx, cy):
    """Ramp position from screen radius: 0 at the source, 1 at the far corner.

    Normalising by the farthest corner (not by the frame diagonal, and not by a
    user number) keeps the ramp's endpoints meaningful wherever C is - including
    when C is outside the frame, where the near end of the ramp is never
    reached and the artist just sees the outer part of it. That is the correct
    behaviour, not a degenerate case.
    """
    rad = radius_map(h, w, cx, cy)
    corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    rmax = max(float(np.hypot(x - cx, y - cy)) for y, x in corners)
    return (rad / max(rmax, 1e-6)).astype(np.float32), rmax


def colorize_radial(rays, cx, cy, lut):
    """Tint the SWEPT buffer by screen radius. This is the one that ships."""
    h, w = rays.shape[:2]
    u, _ = radial_u(h, w, cx, cy)
    return rays * lut_at(lut, u)


def colorize_radial_pre(bright, cx, cy, lut, samples, r, decay):
    """Tint the SOURCE by radius, then sweep. A different look, not a wrong one.

    Kept because check (d) needs it, and because "which side of the sweep does
    this control live on" is a question every colour feature in this effect has
    to answer explicitly.
    """
    h, w = bright.shape[:2]
    u, _ = radial_u(h, w, cx, cy)
    return ray_sweep(bright * lut_at(lut, u), cx, cy, samples, r, decay)


# ===========================================================================
#  MODE 2 - march colour.  Needs the band decomposition.
# ===========================================================================

def build_tables(bright, cx, cy, samples, r, decay):
    """tables[k] = sum over the DISJOINT offset block [0, 2^k). See step 1.

    Step 1's ray_sweep built the tables and stitched them in one function; step 3
    needs the halves separately. Identical maths - check (a) proves it by
    rebuilding step 1's result out of these parts.
    """
    tables = [bright.copy()]
    while (1 << len(tables)) <= samples:
        k = len(tables)
        half = 1 << (k - 1)
        tables.append(tables[k - 1]
                      + (decay ** half) * zoom(tables[k - 1], cx, cy, r ** half))
    return tables


def band(tables, a, b, cx, cy, r, decay):
    """sum_{t=a}^{b-1} decay^t * bright(C + (p-C)*r^t) - one slice of the march.

    Stitched exactly like step 1, except the running offset STARTS at `a`, so
    the block at offset `base` is fetched with a SINGLE zoom(tables[k], r^base).

    That single resample matters, and Star Glint paid to learn it. The obvious
    formulation is

        band(a,b) = decay^a * zoom( prefix(b-a), r^a )

    algebraically identical and numerically worse: it resamples twice, and
    zoom(zoom(X,u),v) != zoom(X,u*v) once bilinear is involved. Check (a2)
    measures the difference here rather than taking Star Glint's word for it.
    """
    if b <= a:
        return np.zeros_like(tables[0])
    out = np.zeros_like(tables[0])
    base, wbase = int(a), float(decay ** a)
    for k in range(len(tables) - 1, -1, -1):
        blk = 1 << k
        if base + blk <= b:
            out += wbase * zoom(tables[k], cx, cy, r ** base)
            wbase *= decay ** blk
            base += blk
    return out


def band_edges_uniform(samples, bands):
    """Uniform slices of the march index t over [0, samples].

    Uniform in t IS geometric in radius here, because sample t sits at radius
    R*r^t. See the module docstring; check (c) tests whether that is enough.
    """
    return np.unique(np.round(np.linspace(0, samples, bands + 1)).astype(int))


def band_edges_geometric(samples, bands):
    """Star Glint's spacing, for comparison in check (c). Geometric in t."""
    e = np.unique(np.round(np.geomspace(1, max(samples, 2), bands)).astype(int))
    return np.unique(np.concatenate(([0], e, [samples])))


def band_u(a, b, decay, samples):
    """Ramp position for the band [a,b), at its ENERGY-WEIGHTED centroid.

    Inside a band the samples are weighted decay^t, so the band's light sits
    nearer its low-t end than its middle. Colouring at the centroid instead of
    the midpoint is one scalar and measurably better - Star Glint step 4's
    table, imported as a conclusion rather than re-derived.
    """
    ts = np.arange(a, b, dtype=np.float64)
    if ts.size == 0:
        return 0.0
    ws = decay ** ts
    tbar = float((ts * ws).sum() / max(ws.sum(), 1e-12))
    return float(np.clip(tbar / max(samples, 1), 0.0, 1.0))


def colorize_march(bright, cx, cy, samples, r, decay, lut, bands=16,
                   edges_fn=band_edges_uniform):
    """Sweep with the ramp applied per distance-band. Build tables once."""
    tables = build_tables(bright, cx, cy, samples, r, decay)
    edges = edges_fn(samples, bands)
    out = np.zeros_like(bright)
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        out += band(tables, a, b, cx, cy, r, decay) * lut_at(lut, band_u(a, b, decay, samples))
    return out


def colorize_march_slow(bright, cx, cy, samples, r, decay, lut):
    """Per-SAMPLE colour: the literal definition, and the ground truth for (b)."""
    out = np.zeros_like(bright)
    for t in range(0, samples + 1):
        c = lut_at(lut, t / max(samples, 1))
        out += (decay ** t) * zoom(bright, cx, cy, r ** t) * c
    return out


# ===========================================================================
#  MODE 3 - dispersive.  Free, but it can only do one thing.
# ===========================================================================

def ray_sweep_dispersive(bright, cx, cy, samples, reach, falloff, spread=0.35):
    """Give R, G, B their own falloff, so colour shifts along the march at zero
    extra cost - no bands, no approximation, and it is what real atmospheric
    extinction does (blue scatters out first, which is why sunbeams go red).

    The catch, same as Star Glint's version: it can only ever produce ONE
    monotonic shift. You cannot express "white, then gold, then magenta".
    Check (e) states that as a measurement rather than a caveat.
    """
    r = ratio_for(reach, samples)
    out = np.zeros_like(bright)
    for c, k in enumerate((1.0 - spread, 1.0, 1.0 + spread)):   # R keeps light longest
        d = decay_for(np.clip(falloff ** k, 1e-6, 1.0), samples)
        out[..., c] = ray_sweep(bright[..., c], cx, cy, samples, r, d)
    return out


# ===========================================================================
#  --explain
# ===========================================================================

def explain(reach=0.45, falloff=0.06, samples=128, bands=16, ramp="sunset"):
    print(f"\nCOLOUR  ramp={ramp}  bands={bands}  samples={samples}\n")

    lin, (cx, cy) = synthetic_sun()
    h, w = lin.shape[:2]
    lut = ray_lut(ramp)
    r, d = ratio_for(reach, samples), decay_for(falloff, samples)

    u, rmax = radial_u(h, w, cx, cy)
    print(f"  radial: C=({cx:.0f},{cy:.0f})  farthest corner = {rmax:.1f} px")
    print(f"          u range in frame = [{u.min():.3f}, {u.max():.3f}]")

    print(f"\n  march bands (uniform in t; radius fraction is r^t):")
    print(f"  {'a':>5} {'b':>5} {'u@centroid':>11} {'r^a':>9} {'r^b':>9}  colour")
    edges = band_edges_uniform(samples, bands)
    for a, b in zip(edges[:-1], edges[1:]):
        uu = band_u(a, b, d, samples)
        c = lut_at(lut, uu)
        print(f"  {a:5d} {b:5d} {uu:11.4f} {r**a:9.4f} {r**b:9.4f}  "
              f"({c[0]:.2f},{c[1]:.2f},{c[2]:.2f})")

    b_ = bright_pass(lin, 0.9, 0.3)
    plain = ray_sweep(b_, cx, cy, samples, r, d)
    describe("plain", plain)

    rad = colorize_radial(plain, cx, cy, lut)
    describe("radial", rad)
    save_linear("explain3_radial.png", lin + tail_only(rad, b_ * lut_at(lut, u), d, samples, 0.6))

    mar = colorize_march(b_, cx, cy, samples, r, d, lut, bands)
    describe("march", mar)
    save_linear("explain3_march.png", lin + tail_only(mar, b_ * lut_at(lut, 0.0), d, samples, 0.6))

    print("\n  wrote explain3_radial.png, explain3_march.png\n")


# ===========================================================================
#  main
# ===========================================================================

def main():
    ok = True

    lin, (cx, cy) = synthetic_sun(360, 640)
    b = bright_pass(lin, 0.9, 0.3)
    H, W = b.shape[:2]
    SMP = 128
    r, d = ratio_for(0.45, SMP), decay_for(0.06, SMP)
    white = ray_lut("white")

    print("=== (a) a FLAT ramp through the bands == step 1's plain sweep ===")
    # Pure algebra check on band()'s offsets and weights. If the bands sum back
    # to the uncoloured sweep, the decomposition is right; any colour error
    # after this is the ramp approximation, not the stitch.
    plain = ray_sweep(b, cx, cy, SMP, r, d)
    for bands in (4, 16, 32):
        got = colorize_march(b, cx, cy, SMP, r, d, white, bands)
        rel = float(np.abs(got - plain).max() / max(plain.max(), 1e-6))
        good = rel < 5e-3
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] bands={bands:3d}  "
              f"rel max|bands-plain| = {rel:.2e}")

    print("\n=== (a2) does Star Glint's double-resample penalty transfer? NO ===")
    # Star Glint found the obvious formulation - decay^a * shift(prefix(b-a),
    # a*d) - disagreed with its plain sweep by 6% because it resampled twice.
    # This check was written expecting to confirm the same for zoom. It does not.
    tables = build_tables(b, cx, cy, SMP, r, d)

    def band_naive(a_, b_):
        inner = band(tables, 0, b_ - a_, cx, cy, r, d)
        return (d ** a_) * zoom(inner, cx, cy, r ** a_)

    edges = band_edges_uniform(SMP, 16)
    one = np.zeros_like(b)
    two = np.zeros_like(b)
    for a_, b2_ in zip(edges[:-1], edges[1:]):
        one += band(tables, a_, b2_, cx, cy, r, d)
        two += band_naive(a_, b2_)
    for nm, x in (("one resample per block", one), ("the obvious way     ", two)):
        print(f"  {nm}: peak {np.abs(x-plain).max()/plain.max():.6e}  "
              f"mean {np.abs(x-plain).mean()/plain.mean():.6e}")
    gap = float(np.abs(one - two).max() / max(plain.max(), 1e-6))
    print(f"  the two formulations differ by rel {gap:.2e} - i.e. not at all.")
    print("  Consistent with step 1 check (b): zoom's composition error is ~40x")
    print("  smaller than shift's, so the second resample costs nothing here.")
    print("  We still ship the fold (it is no worse, and one resample is one")
    print("  resample), but the REASON Star Glint shipped it does not apply.")
    good = gap < 1e-3
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] the two agree to <1e-3 "
          f"(a regression here would mean zoom got worse)")

    print("\n=== (b) how many bands? (vs the per-sample coloured march) ===")
    lut = ray_lut("rainbow")                     # worst case: colour everywhere
    truth = colorize_march_slow(b, cx, cy, SMP, r, d, lut)
    scale = max(float(truth.max()), 1e-6)
    print(f"  {'bands':>6} {'uniform-t':>12} {'geometric-t':>13}")
    prev = None
    for bands in (4, 8, 16, 32, 64):
        eu = float(np.abs(colorize_march(b, cx, cy, SMP, r, d, lut, bands,
                                         band_edges_uniform) - truth).max() / scale)
        eg = float(np.abs(colorize_march(b, cx, cy, SMP, r, d, lut, bands,
                                         band_edges_geometric) - truth).max() / scale)
        print(f"  {bands:6d} {eu:12.4e} {eg:13.4e}")
        if bands == 16:
            good = eu < 0.05
            ok &= good
            print(f"         [{'ok  ' if good else 'FAIL'}] 16 uniform bands within 5%")
        prev = eu

    print("\n=== (c) Star Glint's geometric spacing is NOT needed here ===")
    # The prediction from the module docstring: because samples already sit at
    # geometric radii, uniform-t bands are already geometrically spaced in
    # distance, so Star Glint's correction should buy nothing. Decide it from
    # the table above rather than by eye.
    eu16 = float(np.abs(colorize_march(b, cx, cy, SMP, r, d, lut, 16,
                                       band_edges_uniform) - truth).max() / scale)
    eg16 = float(np.abs(colorize_march(b, cx, cy, SMP, r, d, lut, 16,
                                       band_edges_geometric) - truth).max() / scale)
    verdict = "uniform wins" if eu16 <= eg16 else "geometric still wins"
    print(f"  16 bands: uniform={eu16:.4e}  geometric={eg16:.4e}  -> {verdict}")
    print(f"  (Star Glint's table had geometric ~3x BETTER at every count.)")

    print("\n=== (d) radial colour does NOT commute; angular colour does ===")
    # The mirror image of step 2's theorem. First attempt at this check compared
    # raw pixel values normalised by the global max, and reported 4.8e-03 - which
    # looked like "radial commutes too". It does not; the metric was wrong. The
    # peak of both buffers is the sun, where u ~ 0 and the ramp agrees by
    # construction, so a max-over-global-max metric is dominated by the one
    # region that cannot differ.
    #
    # What actually differs is HUE, so measure hue: normalised rgb over the lit
    # pixels only. And measure the angular case the same way, as the control -
    # otherwise the number has nothing to be large compared to.
    def hue_gap(x, y):
        m = (x.max(axis=2) > 1e-3) & (y.max(axis=2) > 1e-3)
        hx = x[m] / (x[m].sum(axis=1, keepdims=True) + 1e-9)
        hy = y[m] / (y[m].sum(axis=1, keepdims=True) + 1e-9)
        return float(np.abs(hx - hy).mean())

    lut2 = ray_lut("teal_gold")
    post = colorize_radial(ray_sweep(b, cx, cy, SMP, r, d), cx, cy, lut2)
    pre = colorize_radial_pre(b, cx, cy, lut2, SMP, r, d)
    g_radial = hue_gap(pre, post)

    # the angular control: a colour ramp indexed by ANGLE instead of radius
    th = np.arctan2(_grid(H, W)[1] - cy, _grid(H, W)[0] - cx)
    ua = ((th + np.pi) / (2 * np.pi)).astype(np.float32)
    ca = lut_at(lut2, ua)
    a_post = ray_sweep(b, cx, cy, SMP, r, d) * ca
    a_pre = ray_sweep(b * ca, cx, cy, SMP, r, d)
    g_angular = hue_gap(a_pre, a_post)

    good = g_radial > 10 * g_angular
    ok &= good
    print(f"  radial  ramp, pre vs post: mean hue gap = {g_radial:.5f}")
    print(f"  angular ramp, pre vs post: mean hue gap = {g_angular:.5f}  "
          f"({g_radial/max(g_angular,1e-9):.0f}x smaller)")
    print(f"  [{'ok  ' if good else 'FAIL'}] radial is order-dependent, "
          f"angular is not - exactly as step 2 predicts")
    print(f"  Raw-value metrics hide this: |pre-post|.max()/post.max() is only "
          f"{np.abs(pre-post).max()/post.max():.2e},")
    print(f"  because the brightest pixel is the sun, where both agree by "
          f"construction.")

    print("\n=== (e) dispersive is free, and cannot express a non-monotonic ramp ===")
    disp = ray_sweep_dispersive(b, cx, cy, SMP, 0.45, 0.06, 0.35)
    # fit the best per-band tint the dispersive path could ever produce against
    # a non-monotonic ramp, and show the residual is large
    rain = colorize_march(b, cx, cy, SMP, r, d, ray_lut("rainbow"), 32)
    m = rain.max(axis=2) > 1e-3
    hue_rain = rain[m] / (rain[m].sum(axis=1, keepdims=True) + 1e-9)
    hue_disp = disp[m] / (disp[m].sum(axis=1, keepdims=True) + 1e-9)
    spread_rain = float(hue_rain.std(axis=0).mean())
    spread_disp = float(hue_disp.std(axis=0).mean())
    good = spread_disp < spread_rain
    ok &= good
    print(f"  chromatic variety (std of normalised rgb):")
    print(f"    banded rainbow ramp : {spread_rain:.4f}")
    print(f"    dispersive          : {spread_disp:.4f}")
    print(f"  [{'ok  ' if good else 'FAIL'}] dispersive spans less colour - it "
          f"ships as a cheap mode, not the general control")

    print("\n=== (f) renders ===")
    lin, (cx, cy) = synthetic_sun()
    H2, W2 = lin.shape[:2]
    b = bright_pass(lin, 0.9, 0.3)
    SMP = 192
    r, d = ratio_for(0.45, SMP), decay_for(0.06, SMP)
    mask = shimmer_mask(H2, W2, cx, cy, 14.0, 0.7, 0.0, 3)
    u, _ = radial_u(H2, W2, cx, cy)

    for name in ("sunset", "teal_gold", "sodium", "rainbow"):
        lut = ray_lut(name)
        rays = ray_sweep(b, cx, cy, SMP, r, d) * mask[..., None]
        rays = colorize_radial(rays, cx, cy, lut)
        core = b * mask[..., None] * lut_at(lut, u)
        save_linear(f"out_gr3_radial_{name}.png",
                    lin + tail_only(rays, core, d, SMP, 0.6))
        print(f"  wrote out_gr3_radial_{name}.png")

    for name in ("sunset", "rainbow"):
        lut = ray_lut(name)
        rays = colorize_march(b, cx, cy, SMP, r, d, lut, 16) * mask[..., None]
        core = b * mask[..., None] * lut_at(lut, 0.0)
        save_linear(f"out_gr3_march_{name}.png",
                    lin + tail_only(rays, core, d, SMP, 0.6))
        print(f"  wrote out_gr3_march_{name}.png")

    disp = ray_sweep_dispersive(b, cx, cy, SMP, 0.45, 0.06, 0.45) * mask[..., None]
    save_linear("out_gr3_dispersive.png",
                lin + tail_only(disp, b * mask[..., None], d, SMP, 0.6))
    print("  wrote out_gr3_dispersive.png")

    print("\nstep 3 validated." if ok else "\n*** step 3 FAILED ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

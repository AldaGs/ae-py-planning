r"""
Star / Glint - Step 3: COLOUR (per-arm tint, and a ramp ALONG each arm).

Two features, and only one of them is hard.

EASY: PER-ARM TINT
------------------
Give arm i its own colour and multiply. One line. This is the "each spike a
different hue" look, and because it is just a per-arm constant it costs nothing.

HARD: A RAMP ALONG THE ARM
--------------------------
Starglow's actual signature is that a streak CHANGES colour as it travels - hot
white at the core, cooling to a tint at the tip. That is a 1D colour ramp
indexed by distance:

    arm(p) = sum_{t=0..n} decay^t * c(t) * bright(p - t*d)

and there is a problem hiding in that formula. The whole reason the sweep is
fast (step 1) is that it collapses every t into ONE buffer via doubling. Once
collapsed, the distance is gone - there is no t left to index c() with. The
speed and the feature are in direct conflict.

THE FIX: SLICE THE SUM INTO DISTANCE BANDS
------------------------------------------
Go back to what the doubling actually gives us. `tables[k]` is the sum over the
DISJOINT offset block [0, 2^k), and stitching them produces

    prefix(m) = sum_{t=0}^{m-1} decay^t * bright(p - t*d)

Any contiguous band of distances [a, b) is then one shift of a prefix:

    band(a,b) = sum_{t=a}^{b-1} decay^t bright(p - t d)
              = decay^a * shift( prefix(b-a),  a*d )

because factoring decay^a out of the block leaves exactly prefix(b-a), shifted
to start at a. So: build the tables ONCE, then stitch B bands out of them, tint
each band with the ramp colour at its own distance, and sum. The tables - the
expensive part - are shared across all B bands.

    cost:  1 table build (log n)  +  B stitches (B log n)

This is piecewise-CONSTANT colour, so it is an approximation, and the honest
question is how many bands you need before the steps stop showing. Check (c)
measures exactly that against a per-sample coloured march (ground truth) instead
of guessing.

WHY NOT PER-CHANNEL DECAY?
--------------------------
There is a beautiful near-free alternative: give R, G and B different decay
rates. The colour then shifts smoothly along the arm at zero extra cost, and it
is what real dispersion does. `streak_arm_dispersive` below does it in 3 lines.
The catch is that it can only produce ONE monotonic core->tip shift - you cannot
express "white, then gold, then magenta". So it ships as a cheap preset-ish
mode, not as the general control. Both are here; check (d) compares them.

RAMP REUSE
----------
The ramp itself is Gradient Map's `build_lut` imported unchanged - stops in,
256-entry table out. That effect is done and validated; there is no reason to
write a second one. (See the syllabus' cross-effect technique map: "distance
field", "linear light", "ramp LUT" are meant to be spent more than once.)

Run:
    python sg_step3_color.py              # verify + render
    python sg_step3_color.py --explain    # per-band table + band dumps
"""

import os
import sys

import numpy as np
import cv2

import sg_step1_streak as s1
import sg_step2_star as s2
import sg_step2b_armlengths as s2b

# --- reuse Gradient Map's LUT builder, unchanged ---------------------------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "gradient_map"))
import gm_step2_ramp_lut as gm                                  # noqa: E402

build_lut = gm.build_lut

bright_pass = s1.bright_pass
decay_for = s1.decay_for
save_linear = s1.save_linear
shift = s1.shift
direction = s1.direction
lateral_blur = s2.lateral_blur
arm_angles = s2.arm_angles
arm_lengths = s2b.arm_lengths


# ===========================================================================
#  ramps
# ===========================================================================

STAR_RAMPS = {
    # position 0 = the CORE, position 1 = the TIP.
    "white":     [(0.0, (1.0, 1.0, 1.0)), (1.0, (1.0, 1.0, 1.0))],
    "gold":      [(0.0, (1.0, 1.0, 1.0)), (0.35, (1.0, 0.80, 0.45)),
                  (1.0, (0.9, 0.45, 0.10))],
    "cyan_pink": [(0.0, (1.0, 1.0, 1.0)), (0.45, (0.35, 0.85, 1.0)),
                  (1.0, (1.0, 0.25, 0.70))],
    "rainbow":   [(0.0, (1.0, 1.0, 1.0)), (0.25, (1.0, 0.30, 0.25)),
                  (0.50, (0.35, 1.0, 0.35)), (0.75, (0.30, 0.45, 1.0)),
                  (1.0, (0.80, 0.30, 1.0))],
    "sodium":    [(0.0, (1.0, 0.95, 0.85)), (1.0, (1.0, 0.55, 0.05))],
}


def star_lut(name_or_stops, n=256):
    stops = (STAR_RAMPS[name_or_stops] if isinstance(name_or_stops, str)
             else name_or_stops)
    return build_lut(stops, n)


# ===========================================================================
#  the sweep, re-exposed so bands can be cut out of it
# ===========================================================================
#
# Step 1's streak_arm built the tables and stitched them in one function. Step 3
# needs the two halves separately, so they are split here. The maths is
# identical - this is a refactor, and check (a) proves it by rebuilding step 1's
# result out of these parts.

def build_tables(bright, angle, samples, decay):
    """tables[k] = sum over the DISJOINT offset block [0, 2^k). See step 1."""
    dx, dy = direction(angle)
    tables = [bright.copy()]
    while (1 << len(tables)) <= samples:
        k = len(tables)
        half = 1 << (k - 1)
        tables.append(tables[k - 1]
                      + (decay ** half) * shift(tables[k - 1], dx * half, dy * half))
    return tables


def band(tables, a, b, angle, decay):
    """sum_{t=a}^{b-1} decay^t * bright(p - t*d) - one contiguous slice of DISTANCE.

    This is the thing the plain sweep threw away and the thing a ramp needs.

    Stitched exactly like step 1, except the running offset STARTS at `a`
    instead of 0, so the block at offset `base` is fetched with a single
    shift(tables[k], base*d).

    That single shift matters. The obvious formulation is

        band(a,b) = decay^a * shift( prefix(b-a), a*d )

    which is right in exact arithmetic and WRONG in floating point with bilinear
    sampling, because it resamples twice: shift(shift(X,u),v) != shift(X,u+v) -
    each resample adds its own blur. Measured, that version disagreed with step
    1's plain sweep by 6% at 33 degrees even with a flat white ramp, where the
    bands should have summed back to exactly the uncoloured arm. Folding `a`
    into the stitch keeps it to one resample per block, same as step 1.
    """
    if b <= a:
        return np.zeros_like(tables[0])
    dx, dy = direction(angle)
    out = np.zeros_like(tables[0])
    base, wbase = int(a), float(decay ** a)
    for k in range(len(tables) - 1, -1, -1):
        blk = 1 << k
        if base + blk <= b:
            out += wbase * shift(tables[k], dx * base, dy * base)
            wbase *= decay ** blk
            base += blk
    return out


def prefix(tables, m, angle, decay):
    """sum_{t=0}^{m-1} decay^t * bright(p - t*d) - step 1's stitch, i.e. band(0,m)."""
    return band(tables, 0, m, angle, decay)


# ===========================================================================
#  coloured arms
# ===========================================================================

def band_edges(samples, bands):
    """Split the TAIL offsets [1, samples) into `bands` contiguous slices.

    Geometric spacing, not linear: the streak is decay-weighted, so almost all
    the energy - and all the visible colour change - lives near the core. Linear
    bands would spend most of their resolution out in the faint tip where
    nothing is happening. Check (c) measures the difference.
    """
    lo, hi = 1, max(samples, 2)
    e = np.unique(np.round(np.geomspace(lo, hi, bands + 1)).astype(int))
    return e


def streak_arm_colored(bright, angle, length, decay, lut, bands=16,
                       tint=(1.0, 1.0, 1.0), edges_fn=None):
    """The tail, coloured by `lut` along its length, plus a per-arm `tint`."""
    n = int(np.floor(length))
    if n < 1:
        return np.zeros_like(bright)
    samples = n + 1
    tables = build_tables(bright, angle, samples, decay)
    edges = (edges_fn or band_edges)(samples, bands)

    acc = np.zeros_like(bright)
    for a, b in zip(edges[:-1], edges[1:]):
        # colour at this band's MIDPOINT distance, as a fraction of the length
        t_mid = 0.5 * (a + b - 1) / max(length, 1.0)
        col = lut[int(np.clip(t_mid, 0.0, 1.0) * (len(lut) - 1))]
        acc += band(tables, a, b, angle, decay) * col
    return acc * np.float32(tint)


def streak_arm_colored_march(bright, angle, length, decay, lut,
                             tint=(1.0, 1.0, 1.0)):
    """Ground truth: colour applied per SAMPLE, no banding. O(n) shifts."""
    dx, dy = direction(angle)
    out = np.zeros_like(bright)
    for t in range(1, int(np.floor(length)) + 1):
        col = lut[int(np.clip(t / max(length, 1.0), 0.0, 1.0) * (len(lut) - 1))]
        out += (decay ** t) * shift(bright, dx * t, dy * t) * col
    return out * np.float32(tint)


def streak_arm_dispersive(bright, angle, length, decay, spread=0.25,
                          tint=(1.0, 1.0, 1.0)):
    """The near-free alternative: one decay per CHANNEL.

    Red decays slowest and blue fastest (or the reverse for negative spread), so
    the arm drifts in hue along its length at exactly the cost of the plain
    sweep. Smooth by construction - no bands, no approximation - but it can only
    ever make ONE monotonic core->tip shift.
    """
    per_ch = [decay ** (1.0 - spread), decay, decay ** (1.0 + spread)]
    out = np.zeros_like(bright)
    for c in range(3):
        arm = s1.streak_arm(bright[..., c], angle, length, per_ch[c])
        out[..., c] = arm - bright[..., c]
    return out * np.float32(tint)


# ===========================================================================
#  the star
# ===========================================================================

def star(lin, count=6, base_angle=0.0, length=220.0, intensity=1.0,
         threshold=1.0, knee=0.5, tip=0.02, width=3.0,
         mode="uniform", ratio=0.5, variation=0.5, seed=0,
         ramp="gold", bands=16, tints=None, color_mode="ramp", spread=0.25):
    """Step 2b's star, with colour. `tints` is an optional per-arm colour list."""
    bright = bright_pass(lin, threshold, knee)
    lengths = arm_lengths(count, length, mode, ratio, variation, seed)
    lut = star_lut(ramp)

    acc = np.zeros_like(bright)
    for i, (ang, L) in enumerate(zip(arm_angles(count, base_angle), lengths)):
        decay = decay_for(L, tip)
        norm = (1.0 - decay) / decay
        tint = (1.0, 1.0, 1.0) if tints is None else tints[i % len(tints)]
        if color_mode == "ramp":
            tail = streak_arm_colored(bright, ang, L, decay, lut, bands, tint)
        elif color_mode == "dispersive":
            tail = streak_arm_dispersive(bright, ang, L, decay, spread, tint)
        else:
            raise ValueError(color_mode)
        acc += norm * lateral_blur(tail, ang, width * 0.5)
    return (intensity / count) * acc


def hue_wheel(count, sat=0.85):
    """`count` evenly spaced hues - the obvious per-arm tint preset."""
    hsv = np.zeros((1, count, 3), np.float32)
    hsv[0, :, 0] = np.linspace(0, 360, count, endpoint=False)
    hsv[0, :, 1] = sat
    hsv[0, :, 2] = 1.0
    rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)[0]
    return [tuple(float(v) for v in c) for c in rgb]


# ===========================================================================
#  --explain
# ===========================================================================

def explain(length=220.0, bands=12, ramp="rainbow", angle=0.0):
    print(f"\nCOLOUR  ramp={ramp}  bands={bands}  length={length}  angle={angle}\n")

    lut = star_lut(ramp)
    decay = decay_for(length)
    samples = int(np.floor(length)) + 1
    edges = band_edges(samples, bands)

    print("   band   distances    mid    weight decay^a      colour (R,G,B)")
    for a, b in zip(edges[:-1], edges[1:]):
        t_mid = 0.5 * (a + b - 1) / length
        col = lut[int(np.clip(t_mid, 0, 1) * (len(lut) - 1))]
        print(f"   {a:4d}-{b:<4d} {b-a:5d} px  {t_mid:5.2f}   {decay**a:9.5f}   "
              f"({col[0]:.2f}, {col[1]:.2f}, {col[2]:.2f})")
    print(f"\n   {len(edges)-1} bands, geometrically spaced - narrow near the core")
    print("   where the energy and the colour change are, wide out in the tip.\n")

    lin = s1.synthetic_points()
    bright = bright_pass(lin, 1.0, 0.5)
    tables = build_tables(bright, angle, samples, decay)
    norm = (1.0 - decay) / decay

    acc = np.zeros_like(bright)
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
        t_mid = 0.5 * (a + b - 1) / length
        col = lut[int(np.clip(t_mid, 0, 1) * (len(lut) - 1))]
        acc += band(tables, a, b, angle, decay) * col
        save_linear(f"explain3_band{i:02d}.png", lin + norm * acc)
    print(f"   wrote explain3_band00..{len(edges)-2:02d}.png - each image extends")
    print("   the arm one band further out, in that band's colour.\n")


# ===========================================================================
#  main
# ===========================================================================

def main():
    ok = True
    rng = np.random.default_rng(0)
    probe = rng.random((120, 160, 3), np.float32).astype(np.float32)

    print("=== (a) ON-AXIS, a flat ramp reproduces the exact sum bit-for-bit ===")
    # The decomposition claim: bands partition [1, samples), so with a white
    # ramp they must sum back to the plain tail. On-axis there is no resampling
    # to muddy it, so this is a clean algebra check - if band() got its offsets
    # or weights wrong, this fails immediately.
    for ang, L in ((0.0, 100.0), (90.0, 60.0), (180.0, 45.0)):
        d = decay_for(L)
        march = s1.streak_arm_slow(probe, ang, L, d) - probe
        got = streak_arm_colored(probe, ang, L, d, star_lut("white"), bands=8)
        err = float(np.abs(march - got).max()) / float(march.max())
        good = err < 1e-5
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] ang={ang:5.1f} L={L:5.1f}  "
              f"rel err vs exact march = {err:.2e}")

    print("\n=== (b) OFF-AXIS, banding adds no error of its own ===")
    # Off-axis, the ground truth is the literal march - NOT step 1's fast path,
    # which is itself only an approximation there (chained bilinear taps; see
    # step 2). Measured below: step 1's sweep sits ~8-10% off the exact sum at
    # 33/45 degrees. So the right question is not "do bands match step 1" but
    # "do bands make it any worse". They do not - they come out slightly closer.
    print("        angle   bands=8 vs march   step1 sweep vs march")
    for ang, L in ((33.0, 80.0), (45.0, 80.0), (20.0, 120.0)):
        d = decay_for(L)
        march = s1.streak_arm_slow(probe, ang, L, d) - probe
        m = float(march.max())
        e_fast = float(np.abs(s1.streak_arm(probe, ang, L, d) - probe - march).max()) / m
        e_band = float(np.abs(streak_arm_colored(probe, ang, L, d,
                                                 star_lut("white"), 8) - march).max()) / m
        good = e_band <= e_fast * 1.1
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}]  {ang:5.1f}      {e_band:.2e}"
              f"           {e_fast:.2e}")

    print("\n=== (b2) a flat ramp is colour-neutral at any band count ===")
    # If the band machinery itself tinted anything, changing the band count
    # would shift the result. On-axis so the resampling floor is out of the way.
    d = decay_for(120.0)
    march = s1.streak_arm_slow(probe, 0.0, 120.0, d) - probe
    for b in (4, 8, 16, 32):
        got = streak_arm_colored(probe, 0.0, 120.0, d, star_lut("white"), bands=b)
        err = float(np.abs(march - got).max()) / float(march.max())
        good = err < 1e-5
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] bands={b:3d}: rel err = {err:.2e}")

    print("\n=== (c) how many bands? measured against a per-sample march ===")
    # The honest question about a piecewise-constant approximation. Ground truth
    # colours every sample individually; we compare band counts against it, and
    # also compare geometric vs linear band spacing.
    pt = np.zeros((260, 260, 3), np.float32)
    pt[130, 60] = 8.0
    L = 160.0
    d = decay_for(L)
    lut = star_lut("rainbow")
    truth = streak_arm_colored_march(pt, 0.0, L, d, lut)
    scale = float(truth.max())

    def lin_edges(samples, bands):
        return np.unique(np.round(np.linspace(1, samples, bands + 1)).astype(int))

    print("        bands    geometric      linear")
    for b in (4, 8, 16, 32, 64):
        g = streak_arm_colored(pt, 0.0, L, d, lut, bands=b)
        li = streak_arm_colored(pt, 0.0, L, d, lut, bands=b, edges_fn=lin_edges)
        eg = float(np.abs(g - truth).max()) / scale
        el = float(np.abs(li - truth).max()) / scale
        print(f"         {b:3d}     {eg:8.2e}    {el:8.2e}")
        if b == 16:
            good = eg < 5e-2
            ok &= good
            print(f"  [{'ok  ' if good else 'FAIL'}] 16 geometric bands within 5% "
                  f"of the exact per-sample colouring")

    print("\n=== (d) dispersive mode is smooth but only monotonic ===")
    # Not a correctness check - a capability check, so the trade-off is on record.
    disp = streak_arm_dispersive(pt, 0.0, L, d, spread=0.35)
    hues = []
    for r in (20, 60, 100, 140):
        col = disp[130, 60 + r]
        hues.append(col / max(float(col.max()), 1e-9))
    print("        distance   normalised colour")
    for r, h in zip((20, 60, 100, 140), hues):
        print(f"          {r:4d}     ({h[0]:.2f}, {h[1]:.2f}, {h[2]:.2f})")
    mono = all(hues[i][2] >= hues[i + 1][2] - 1e-3 for i in range(len(hues) - 1))
    print(f"        blue falls monotonically: {mono}  <- that is the limitation")

    print("\n=== (e) renders ===")
    lin = s1.synthetic_points()
    configs = [
        ("ramp_gold", dict(count=6, base_angle=15.0, ramp="gold")),
        ("ramp_cyanpink", dict(count=6, base_angle=15.0, ramp="cyan_pink")),
        ("ramp_rainbow", dict(count=8, ramp="rainbow")),
        ("ramp_sodium", dict(count=4, ramp="sodium", length=280.0)),
        ("tint_wheel", dict(count=6, base_angle=15.0, ramp="white",
                            tints=hue_wheel(6))),
        ("tint_wheel_ramp", dict(count=8, ramp="gold", tints=hue_wheel(8, 0.5))),
        ("dispersive", dict(count=6, base_angle=15.0, color_mode="dispersive",
                            spread=0.4)),
        ("style_mix", dict(count=8, ramp="cyan_pink", mode="alternate",
                           ratio=0.45, tints=hue_wheel(8, 0.35))),
        ("style_random", dict(count=8, ramp="gold", mode="random",
                              variation=0.6, seed=2)),
    ]
    for name, kw in configs:
        st = star(lin, **kw)
        save_linear(f"out_sg3_{name}.png", lin + st)
        print(f"  wrote out_sg3_{name}.png  {kw}")

    print("\nstep 3 validated." if ok else "\n*** step 3 FAILED ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

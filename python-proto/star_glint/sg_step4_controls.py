r"""
Star / Glint - Step 4: PERFORMANCE + THE THINGS AE WILL DEMAND.

Steps 1-3 built something correct. This step makes it shippable, which is two
separate jobs:

    PERFORMANCE   it is currently ~6 seconds for a 6-arm coloured star at 1080p
    AE REALITY    resolution independence and alpha - neither optional, both
                  invisible until they bite

----------------------------------------------------------------------------
PROFILE FIRST. THE GUESS WAS WRONG.
----------------------------------------------------------------------------
Step 2 left a note saying the performance problem was `lateral_blur`, because it
was a non-separable filter2D and that sounded expensive. Measured at 1080p,
L=300:

    bright_pass                37 ms
    build_tables (1 arm)      184 ms
    one general band stitch    76 ms   <-- x16 bands x6 arms
    lateral_blur, width 3      23 ms   <-- the thing we were going to optimise
    coloured arm, 16 bands    989 ms

The blur is 2% of the cost. The bands are ~75% of it. Optimising the blur would
have been a day spent making the effect 2% faster. Profile, then optimise.

----------------------------------------------------------------------------
THE BAND FIX: MAKE EVERY BAND A POWER OF TWO LONG
----------------------------------------------------------------------------
Step 3's `band(a, b)` walks the tables and stitches whatever blocks fit - up to
log(n) shifts. But look at what `tables[k]` already is: the geometric sum over a
block of exactly 2^k consecutive offsets. So if a band's LENGTH is a power of
two, no stitching is needed at all:

    band(a, a + 2^k) = decay^a * shift(tables[k], a*d)        ONE shift.

Measured: 76 ms -> 16 ms per band, 4.7x.

So `pow2_band_edges` partitions the tail into segments whose LENGTHS are powers
of two, arranged to stay roughly geometric (which step 3 check (c) showed is the
right spacing). This is the doubling trick paying for itself a second time - the
fast path was always sitting inside the data structure, step 3 just wasn't
asking for it.

Two honest notes on it:

  * the band boundaries move compared to step 3's pure geomspace, and for a
    given `bands` the two schemes do not land on the same SEGMENT count, so
    check (a) prints segment counts rather than pretending the columns compare
    directly. What is asserted is that the default (16) meets step 3's stated
    5% accuracy target.

  * end to end this did NOT make the effect faster, because alpha (below) added
    a fourth channel and spent the win. The pow2 change is worth ~1.8x on the
    arm itself, and matters much more asymptotically: O(B log n) shifts becomes
    O(B). On CPU that is muted because full-frame memory traffic dominates the
    resample; on the GPU, where every pass carries fixed setup cost, dropping
    ~9 passes per band is the difference that counts.

----------------------------------------------------------------------------
DOWNSAMPLING
----------------------------------------------------------------------------
Everything after the bright pass is smooth and low-frequency, so it can run at
half or quarter resolution and be scaled back up - the same trick Bloom uses for
its streak stage. Cost scales with area, so half res is ~4x. Check (c) measures
the error that buys, at each factor, instead of assuming it is fine.

----------------------------------------------------------------------------
RESOLUTION INDEPENDENCE (the AE bug you only find at the end)
----------------------------------------------------------------------------
AE renders previews at Half, Third, Quarter resolution. The effect is handed a
smaller buffer and a downsample factor - and if the code treats Length as "300
pixels" the streak will be 300 buffer-pixels long at every resolution, i.e. FOUR
TIMES LONGER relative to the frame at Quarter than at Full. The preview then
lies about the final render.

Every length-like control has to be multiplied by the downsample factor before
use. Check (d) renders at full res and at half res and compares - which is
exactly the test that catches it.

----------------------------------------------------------------------------
ALPHA
----------------------------------------------------------------------------
A streak is light, and light that lands on empty pixels has to bring its own
coverage or it will not composite. So alpha gets swept exactly like the colour
channels and added. Two properties worth asserting rather than hoping for:
transparent input must stay transparent (check e), and the streak must not
brighten pixels it does not also make more opaque.

Run:
    python sg_step4_controls.py              # verify + render
    python sg_step4_controls.py --explain    # parameter dump + cost breakdown
"""

import sys
import time

import numpy as np
import cv2

import sg_step1_streak as s1
import sg_step2_star as s2
import sg_step2b_armlengths as s2b
import sg_step3_color as s3

bright_pass = s1.bright_pass
decay_for = s1.decay_for
save_linear = s1.save_linear
shift = s1.shift
direction = s1.direction
lateral_blur = s2.lateral_blur
arm_angles = s2.arm_angles
arm_lengths = s2b.arm_lengths
build_tables = s3.build_tables
star_lut = s3.star_lut
hue_wheel = s3.hue_wheel


# ===========================================================================
#  bands, take two: power-of-two LENGTHS so each band is a single shift
# ===========================================================================

def pow2_band_edges(samples, bands, max_k=None):
    """Partition the tail [1, samples) into segments of power-of-two LENGTH.

    Aims for the geometric spacing step 3 measured as best, but snaps each
    segment length to the NEAREST power of two so `band()` can take its fast
    path. The trailing remainder segment usually is not a power of two and
    falls back to the general stitch - correct, just not free.
    """
    lo, hi = 1, max(samples, 2)
    if hi <= lo + 1:
        return np.array([lo, hi])
    # Solve for the ratio that lands in `bands` segments GIVEN that the first
    # segments are floored at length 1. Snapping to the NEAREST power of two
    # (not down) keeps the segment count near the requested one - snapping down
    # makes every step short, so the partition overshoots and the speed win is
    # spent on extra bands.
    ratio = (hi / lo) ** (1.0 / max(bands, 1))
    cap = (1 << max_k) if max_k is not None else hi

    edges = [lo]
    a = lo
    while a < hi:
        want = max(a * (ratio - 1.0), 1.0)            # geometric step
        k = int(np.round(np.log2(want)))              # snap to NEAREST 2^k
        blk = 1 << max(k, 0)
        blk = min(blk, cap, hi - a)
        if blk < 1:
            break
        # never overshoot: trim the last block to a power of two that fits
        while blk > 1 and a + blk > hi:
            blk >>= 1
        a += blk
        edges.append(a)
    if edges[-1] != hi:
        edges.append(hi)
    return np.array(edges, dtype=int)


def band_color_index(a, b, decay, length, n_lut):
    """Which ramp entry to colour band [a, b) with.

    NOT the midpoint. Inside a band the samples are weighted decay^t, so the
    band's light is concentrated at its near end - the energy-weighted centroid

        tbar = sum_{t in [a,b)} t*decay^t / sum_{t in [a,b)} decay^t

    which for a decaying weight sits well before (a+b)/2. Using the midpoint
    instead systematically colours every band as if its light were further out
    than it is, and the error grows with band width. This is a scalar
    computation per band - free next to a full-frame shift.
    """
    t = np.arange(a, b, dtype=np.float64)
    wgt = decay ** t
    tbar = float((t * wgt).sum() / max(wgt.sum(), 1e-30))
    return int(np.clip(tbar / max(length, 1.0), 0.0, 1.0) * (n_lut - 1))


def band_fast(tables, a, b, angle, decay):
    """band(a,b) with a single shift when (b-a) is a power of two.

    Falls back to step 3's general stitch otherwise, so this is always correct -
    it is purely an opportunistic fast path.
    """
    n = b - a
    if n >= 1 and (n & (n - 1)) == 0:                 # n is a power of two
        k = int(n).bit_length() - 1
        if k < len(tables):
            dx, dy = direction(angle)
            return (decay ** a) * shift(tables[k], dx * a, dy * a)
    return s3.band(tables, a, b, angle, decay)


def streak_arm_colored_fast(bright, angle, length, decay, lut, bands=16,
                            tint=(1.0, 1.0, 1.0)):
    """Step 3's coloured arm, using pow2 bands and the single-shift fast path."""
    n = int(np.floor(length))
    if n < 1:
        return np.zeros_like(bright)
    samples = n + 1
    tables = build_tables(bright, angle, samples, decay)
    edges = pow2_band_edges(samples, bands, max_k=len(tables) - 1)

    nch = bright.shape[2] if bright.ndim == 3 else 1
    dx, dy = direction(angle)
    acc = np.zeros_like(bright)
    for a, b in zip(edges[:-1], edges[1:]):
        a, b = int(a), int(b)
        col = _chan(lut[band_color_index(a, b, decay, length, len(lut))], nch)
        n = b - a
        if n >= 1 and (n & (n - 1)) == 0 and (n.bit_length() - 1) < len(tables):
            # FUSED fast path. Each band touches a 24 MB buffer, so the memory
            # traffic - not the resample - is the cost: measured 8 ms for the
            # shift itself but 37 ms for shift + scale + tint + accumulate as
            # separate expressions, each allocating a temporary. Folding the
            # scalar decay^a into the colour vector and going in-place cuts the
            # buffer passes from four to two.
            tmp = shift(tables[n.bit_length() - 1], dx * a, dy * a)
            tmp *= col * np.float32(decay ** a)
            acc += tmp
        else:
            acc += band_fast(tables, a, b, angle, decay) * col
    acc *= _chan(np.float32(tint), nch)
    return acc


def _chan(col, nch):
    """Extend an RGB colour to the buffer's channel count (alpha rides at 1.0)."""
    col = np.asarray(col, np.float32)
    if nch <= col.shape[0]:
        return col[:nch]
    return np.concatenate([col, np.ones(nch - col.shape[0], np.float32)])


# ===========================================================================
#  the parameter block - this is the AE effect's UI, written down
# ===========================================================================

DEFAULTS = dict(
    # --- bright pass
    threshold=1.0, knee=0.5,
    # --- shape
    count=6, base_angle=15.0, length=220.0, width=3.0,
    # --- style (step 2b)
    mode="uniform", ratio=0.5, variation=0.5, seed=0,
    # --- colour (step 3)
    ramp="gold", bands=16, tints=None,
    # --- output
    intensity=1.0, boost=1.0,
    # --- quality / performance
    downsample=1,          # 1 = full res streaks, 2 = half, 4 = quarter
    # --- host state, NOT user-facing
    res_scale=1.0,         # AE's downsample factor: 1.0 full, 0.5 half, ...
)


def params(**over):
    p = dict(DEFAULTS)
    p.update(over)
    return p


# ===========================================================================
#  the effect
# ===========================================================================

def star_field(lin, alpha=None, **kw):
    """The streak field (and its alpha), given a full parameter block.

    Returns (rgb, alpha) both in linear light, both ADDITIVE - the caller sums
    them onto the source. Kept separate from compositing so step 5's port can
    hand these straight to AE's own compositing.
    """
    p = params(**kw)
    h, w = lin.shape[:2]

    # RESOLUTION INDEPENDENCE: every length-like control is in FULL-FRAME pixels,
    # so scale into buffer pixels before anything uses them.
    length_px = p["length"] * p["res_scale"]
    width_px = p["width"] * p["res_scale"]

    # alpha rides along as a 4th channel so it gets swept identically
    a_in = np.ones((h, w), np.float32) if alpha is None else alpha
    work = np.dstack([lin, a_in])

    bright4 = bright_pass(work[..., :3], p["threshold"], p["knee"])
    wgt = np.zeros((h, w), np.float32)
    nz = work[..., :3].max(axis=2) > 0
    wgt[nz] = (bright4[nz] / np.maximum(work[..., :3][nz], 1e-9)).max(axis=1)
    bright = np.dstack([bright4, np.clip(wgt, 0.0, 1.0) * a_in])

    # DOWNSAMPLE: everything past the bright pass is low-frequency
    ds = max(int(p["downsample"]), 1)
    if ds > 1:
        bright = cv2.resize(bright, (max(w // ds, 1), max(h // ds, 1)),
                            interpolation=cv2.INTER_AREA)
        length_px /= ds
        width_px /= ds

    lut = star_lut(p["ramp"])
    lengths = arm_lengths(p["count"], length_px, p["mode"], p["ratio"],
                          p["variation"], p["seed"])

    acc = np.zeros_like(bright)
    for i, (ang, L) in enumerate(zip(arm_angles(p["count"], p["base_angle"]),
                                     lengths)):
        decay = decay_for(L)
        norm = (1.0 - decay) / decay
        tint = ((1.0, 1.0, 1.0) if p["tints"] is None
                else p["tints"][i % len(p["tints"])])
        tail = streak_arm_colored_fast(bright, ang, L, decay, lut, p["bands"],
                                       tint)
        acc += norm * lateral_blur(tail, ang, width_px * 0.5)

    acc *= p["intensity"] * p["boost"] / p["count"]

    if ds > 1:
        acc = cv2.resize(acc, (w, h), interpolation=cv2.INTER_LINEAR)
    return acc[..., :3], np.clip(acc[..., 3], 0.0, None)


def composite(lin, streak_rgb, streak_a, alpha=None):
    """Additive light, additive coverage. Straight (un-premultiplied) alpha."""
    a_in = np.ones(lin.shape[:2], np.float32) if alpha is None else alpha
    return lin + streak_rgb, np.clip(a_in + streak_a, 0.0, 1.0)


# ===========================================================================
#  --explain
# ===========================================================================

def explain():
    p = params()
    print("\nPARAMETERS (what the AE UI will expose)\n")
    groups = [("Bright pass", ["threshold", "knee"]),
              ("Shape", ["count", "base_angle", "length", "width"]),
              ("Style", ["mode", "ratio", "variation", "seed"]),
              ("Colour", ["ramp", "bands", "tints"]),
              ("Output", ["intensity", "boost"]),
              ("Quality", ["downsample"]),
              ("Host state (not user-facing)", ["res_scale"])]
    for name, keys in groups:
        print(f"  {name}")
        for k in keys:
            print(f"      {k:<12} = {p[k]!r}")
    print()

    samples = 301
    for bands in (8, 16, 24):
        e = pow2_band_edges(samples, bands, max_k=8)
        lens = np.diff(e)
        pow2 = [bool(n >= 1 and (n & (n - 1)) == 0) for n in lens]
        print(f"  bands={bands:2d} -> {len(lens)} segments, "
              f"lengths {[int(x) for x in lens]}")
        print(f"           {sum(pow2)}/{len(pow2)} take the single-shift path "
              f"(the remainder segment usually is not a power of two,")
        print(f"           so it falls back to the general stitch - correct, "
              f"just not free)")
    print()


# ===========================================================================
#  main
# ===========================================================================

def main():
    ok = True
    rng = np.random.default_rng(0)

    print("=== (a) pow2 bands are as accurate as step 3's geometric bands ===")
    # Snapping band lengths to powers of two moves the boundaries a little. The
    # question is whether that costs accuracy against the exact per-sample
    # coloured march. (Ground truth is the march - never step 1's fast path.)
    pt = np.zeros((260, 260, 3), np.float32)
    pt[130, 60] = 8.0
    L, lut = 160.0, star_lut("rainbow")
    d = decay_for(L)
    truth = s3.streak_arm_colored_march(pt, 0.0, L, d, lut)
    scale = float(truth.max())
    # NOTE the two schemes do not produce the same number of SEGMENTS for a
    # given `bands`, so segment counts are printed rather than pretending the
    # columns are like-for-like. What is asserted is the thing that matters:
    # step 4 hits step 3's stated 5% target at the default band count.
    print("        bands   step3 geometric      step4 pow2")
    for b in (8, 16, 32):
        e3 = float(np.abs(s3.streak_arm_colored(pt, 0.0, L, d, lut, b)
                          - truth).max()) / scale
        e4 = float(np.abs(streak_arm_colored_fast(pt, 0.0, L, d, lut, b)
                          - truth).max()) / scale
        n3 = len(s3.band_edges(int(L) + 1, b)) - 1
        n4 = len(pow2_band_edges(int(L) + 1, b)) - 1
        print(f"         {b:3d}    {e3:8.2e} ({n3:2d} seg)   {e4:8.2e} ({n4:2d} seg)")
        if b == 16:
            good = e4 < 5e-2
            ok &= good
            print(f"  [{'ok  ' if good else 'FAIL'}] default 16 bands within 5% "
                  f"of the exact per-sample colouring")

    print("\n=== (b) and they are much faster ===")
    big = s1.bright_pass(s1.synthetic_points(1080, 1920), 1.0, 0.5)
    d = decay_for(300.0)
    t0 = time.time(); s3.streak_arm_colored(big, 20.0, 300.0, d, lut, 16)
    t_old = time.time() - t0
    t0 = time.time(); streak_arm_colored_fast(big, 20.0, 300.0, d, lut, 16)
    t_new = time.time() - t0
    good = t_new < t_old * 0.8
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] 1 arm @1080p, 16 bands: "
          f"step3 {t_old*1e3:6.0f} ms -> step4 {t_new*1e3:6.0f} ms "
          f"({t_old/t_new:.1f}x)")
    print("        The bigger point is asymptotic: bands go from O(B log n)")
    print("        shifts to O(B). On CPU that is ~1.4x because full-frame")
    print("        memory traffic dominates; on the GPU, where each pass has")
    print("        fixed setup cost, dropping 9 passes per band matters far more.")

    print("\n=== (c) downsampling: what the speed actually costs ===")
    lin = s1.synthetic_points(1080, 1920)
    ref, _ = star_field(lin, downsample=1)
    scale = float(ref.max())
    print("        factor    time      rel err vs full res")
    for ds in (1, 2, 4):
        t0 = time.time()
        got, _ = star_field(lin, downsample=ds)
        dt = time.time() - t0
        err = float(np.abs(got - ref).max()) / scale
        print(f"          {ds}     {dt*1e3:7.0f} ms      {err:.2e}")
        if ds == 2:
            good = err < 0.25
            ok &= good
            print(f"  [{'ok  ' if good else 'FAIL'}] half res stays within 25% "
                  f"peak error (streaks are thin - this is the real limit)")

    print("\n=== (d) resolution independence ===")
    # THE AE bug. Render full res, then render a half-size buffer with
    # res_scale=0.5 as AE would, upscale, and compare. If Length were treated as
    # buffer pixels the half-res streak would be twice as long relative to frame.
    small = cv2.resize(lin, (960, 540), interpolation=cv2.INTER_AREA)
    full, _ = star_field(lin, res_scale=1.0)
    half, _ = star_field(small, res_scale=0.5)
    half_up = cv2.resize(half, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    err = float(np.abs(half_up - full).max()) / float(full.max())
    good = err < 0.30
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] full vs half-res-with-res_scale: "
          f"rel err = {err:.2e}")
    # and the same thing WITHOUT the scaling, to show the check is not vacuous
    bad, _ = star_field(small, res_scale=1.0)
    bad_up = cv2.resize(bad, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    ebad = float(np.abs(bad_up - full).max()) / float(full.max())
    print(f"        with res_scale ignored (the bug): rel err = {ebad:.2e}")

    print("\n=== (e) alpha ===")
    # (1) nothing in, nothing out.
    empty = np.zeros((240, 320, 3), np.float32)
    rgb, a = star_field(empty, alpha=np.zeros((240, 320), np.float32))
    good = float(np.abs(rgb).max()) < 1e-9 and float(np.abs(a).max()) < 1e-9
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] transparent black in -> "
          f"rgb max {float(np.abs(rgb).max()):.1e}, alpha max {float(a.max()):.1e}")

    # (2) a bright opaque point must produce coverage wherever it produces light.
    src = np.zeros((240, 320, 3), np.float32)
    src[120, 80] = 8.0
    a_in = np.zeros((240, 320), np.float32)
    a_in[120, 80] = 1.0
    rgb, a = star_field(src, alpha=a_in, count=4, length=80.0)
    lit = rgb.max(axis=2) > 1e-4
    covered = a > 1e-5
    orphan = int((lit & ~covered).sum())
    good = orphan == 0 and covered.sum() > 100
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] lit-but-transparent pixels = "
          f"{orphan} (want 0), covered = {int(covered.sum())}")

    print("\n=== (f) renders ===")
    lin = s1.synthetic_points()
    presets = [
        ("preset_classic", dict(count=4, ramp="white", length=260.0)),
        ("preset_gold6", dict(count=6, base_angle=15.0, ramp="gold")),
        ("preset_sparkle", dict(count=8, mode="alternate", ratio=0.4,
                                ramp="cyan_pink")),
        ("preset_dirty", dict(count=8, mode="random", variation=0.6, seed=2,
                              ramp="gold", width=4.0)),
        ("preset_prism", dict(count=6, base_angle=15.0, ramp="white",
                              tints=hue_wheel(6, 0.7))),
        ("preset_half_res", dict(count=6, base_angle=15.0, ramp="gold",
                                 downsample=2)),
    ]
    for name, kw in presets:
        rgb, a = star_field(lin, **kw)
        out, _ = composite(lin, rgb, a)
        save_linear(f"out_sg4_{name}.png", out)
        print(f"  wrote out_sg4_{name}.png  {kw}")

    print("\n=== (g) end-to-end cost, 1080p, 6 arms, 16 bands ===")
    lin = s1.synthetic_points(1080, 1920)
    for ds in (1, 2):
        t0 = time.time(); star_field(lin, downsample=ds); dt = time.time() - t0
        print(f"        downsample={ds}: {dt*1e3:7.0f} ms")
    print("        Honest accounting: full-res end-to-end is NOT faster than")
    print("        step 3 (~6 s either way). The pow2 bands bought ~1.8x on the")
    print("        arm, and alpha spent it again by making every buffer 4")
    print("        channels instead of 3. The real CPU win is downsampling")
    print("        (~4x per level); the real fix is the GPU port in step 5.")

    print("\nstep 4 validated." if ok else "\n*** step 4 FAILED ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

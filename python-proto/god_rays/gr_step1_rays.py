r"""
God Rays / Volumetric Light - Step 1: BRIGHT PASS + THE RADIAL SWEEP.

Effect #6 in the spec. The end goal is Shine: light shafts radiating from a
source point, thrown by whatever is bright (or by whatever occludes the source).
This step builds the two pieces everything else is made of, and proves the fast
one is correct.

    bright_pass(image)  ->  only the highlights, in linear light   [reused as-is]
    ray_sweep(bright)   ->  smear those highlights AWAY from a centre point

Step 1 stops at one centre, white light, no colour, no occlusion model. That is
on purpose: the whole effect is the second function, and the second function is
one idea.

THE PIPELINE (this is what --explain dumps, in order)
-----------------------------------------------------
    0. src_srgb      the input as it arrives, gamma-encoded 0..1
    1. linear        undo sRGB gamma -> light-linear values
    2. bright        soft-knee threshold: keep highlights, kill the rest
    3. rays          bright, smeared radially outward from the centre
    4. tail          rays minus the core (the core is already in the image)
    5. composite     src + tail, back to sRGB

WHAT IS ACTUALLY NEW HERE (versus Star Glint)
---------------------------------------------
Star Glint's sweep marched along a FIXED direction d, the same vector for every
pixel:

    arm(p) = sum_t decay^t * bright(p - t*d)                    <- TRANSLATION

God rays march every pixel toward the light. The direction is different for
every pixel, and so is the step size - a pixel far from the light has further to
travel. That sounds like it kills the fast path. It does not, because the march
is not really "a different translation per pixel". Written properly:

    ray(p) = sum_t decay^t * bright(C + (p - C) * r^t)          <- SCALE about C

Each step multiplies the offset-from-centre by a constant ratio r < 1, dragging
the sample toward C. One global scalar r, one centre C. This is the standard
GPU Gems 3 radial-scattering march (Mitchell, "Volumetric Light Scattering as a
Post-Process") written with a geometric step instead of a linear one.

WHY THAT REWRITE IS THE WHOLE STEP
-----------------------------------
Scales about a common centre COMPOSE, exactly like translations do:

    zoom(zoom(X, a), b) = zoom(X, a*b)          just as   shift+shift = shift

So Star Glint's geometric-doubling identity carries over verbatim - only the
primitive changes. Let S_k be the sum over the block of offsets [0, 2^k):

    S_k(p) = S_{k-1}(p)  +  decay^(2^(k-1)) * zoom(S_{k-1}, r^(2^(k-1)))
             \_first half_/  \_____________second half_______________/

Each pass DOUBLES the reach. 256 samples in 9 passes, exact. Every pass is
"resample the frame with one affine map, multiply by a scalar, add" - the same
ping-pong texture op as Bloom's pyramid and Star Glint's arms.

That claim is what main() verifies against ray_sweep_slow, the literal march.

THE ONE PLACE THE ANALOGY BREAKS
---------------------------------
A translation is energy-preserving: bilinear weights over the destination
lattice sum to exactly 1, so shift() never changes a buffer's total. A scale is
NOT. Magnifying by 1/r spreads the same light over 1/r^2 as many pixels, so the
sum grows. This is physically correct - a shaft really does get wider - but it
means "check the total energy" is not the free invariant it was in Star Glint.
Check (c) measures the growth instead of assuming it away.

PARAMETERS, AND THE TWO THAT ARE SECRETLY THE SAME CONTROL
-----------------------------------------------------------
The GPU Gems formulation exposes density / decay / weight / exposure. Taken
literally, three of those four are sample-count dependent and two of them are
the same number. This file fixes both problems up front:

  reach    user-facing.  How far along the way to the centre the march travels,
                         as a fraction. 1.0 = all the way to the light.
                         -> r = (1 - reach) ** (1/n).  Independent of n.
  falloff  user-facing.  How much a sample is dimmed by the END of the march.
                         -> decay = falloff ** (1/n).  Independent of n.
  samples  quality.      More samples = smoother shaft, SAME look. That is the
                         point of deriving r and decay from n rather than
                         exposing them raw.
  intensity            weight * exposure. They multiply; there is no second
                         degree of freedom, so we ship one knob, not two.

Run:
    python gr_step1_rays.py              # verify + render
    python gr_step1_rays.py --explain    # dump every stage as a numbered PNG
"""

import sys
import time

import numpy as np
import cv2


# ===========================================================================
#  colour transfer - sRGB <-> linear light          [same spine as Bloom / SG]
# ===========================================================================

def linear_to_srgb(c):
    """Re-apply the display curve on the way out. Negatives clamped first."""
    c = np.clip(np.asarray(c, np.float32), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1.0 / 2.4)) - 0.055)


LUMA = np.float32([0.2126, 0.7152, 0.0722])      # Rec.709, for linear light


# ===========================================================================
#  stage 2 - bright pass                            [lifted from Star Glint]
# ===========================================================================

def bright_pass(lin, threshold=1.0, knee=0.5):
    """Keep only what is brighter than `threshold`, fading in over `knee`.

    Identical to Star Glint's. Weight the ORIGINAL rgb rather than subtracting
    the threshold, so a warm sun stays warm instead of drifting toward white.
    """
    y = lin @ LUMA
    lo, hi = threshold - knee, threshold + knee
    if hi <= lo:
        w = (y >= threshold).astype(np.float32)
    else:
        t = np.clip((y - lo) / (hi - lo), 0.0, 1.0)
        w = t * t * (3.0 - 2.0 * t)               # smoothstep
    return lin * w[..., None]


# ===========================================================================
#  stage 3 - the radial sweep
# ===========================================================================

_GRID_CACHE = {}


def _grid(h, w):
    """The (xs, ys) sampling lattice for an h x w buffer, built once per size.

    Same cache as Star Glint, same reason: `zoom` runs dozens of times per
    render on one buffer size, and a fresh meshgrid at 1920x1080 is two 8 MB
    allocations every call.
    """
    key = (h, w)
    g = _GRID_CACHE.get(key)
    if g is None:
        g = np.meshgrid(np.arange(w, dtype=np.float32),
                        np.arange(h, dtype=np.float32))
        _GRID_CACHE[key] = g
    return g


def zoom(buf, cx, cy, r):
    """Sample buf at C + (p - C) * r, bilinear, zero outside. THE primitive.

    r < 1 drags every sample toward the centre, which makes the OUTPUT look
    magnified. This is Star Glint's `shift` with the translation swapped for a
    scale about a point - everything downstream is unchanged.

    Note there is no special case for the centre pixel or for arbitrary r:
    fractional sample positions are the normal case here, not an edge case.
    """
    h, w = buf.shape[:2]
    xs, ys = _grid(h, w)
    r = np.float32(r)
    cx, cy = np.float32(cx), np.float32(cy)
    mx = cx + (xs - cx) * r
    my = cy + (ys - cy) * r
    return cv2.remap(buf, mx, my, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)


def ratio_for(reach, samples):
    """Per-step scale factor so the march covers `reach` of the way to C.

        r ** samples = 1 - reach

    Deriving r from reach (rather than exposing r) makes the control mean what
    its name says AND makes the look independent of `samples`. Doubling the
    sample count then only buys smoothness, which is what a quality knob should
    do.
    """
    reach = float(np.clip(reach, 0.0, 0.999))
    return float((1.0 - reach) ** (1.0 / max(samples, 1)))


def decay_for(falloff, samples):
    """Per-step dim factor so a sample is dimmed to `falloff` by the last step.

    Same trick as ratio_for, same reason. GPU Gems exposes the per-step decay
    directly, which silently re-times the whole effect whenever you change the
    sample count.
    """
    falloff = float(np.clip(falloff, 1e-6, 1.0))
    return float(falloff ** (1.0 / max(samples, 1)))


def ray_sweep_slow(bright, cx, cy, samples, r, decay):
    """The formula, typed out literally. O(n) resamples. Ground truth."""
    out = bright.copy()                           # the t=0 term
    for t in range(1, samples + 1):
        out += (decay ** t) * zoom(bright, cx, cy, r ** t)
    return out


def ray_sweep(bright, cx, cy, samples, r, decay):
    """Same sum, O(log n) resamples, via geometric doubling.

    Line-for-line Star Glint's `streak_arm` with `shift(buf, dx*t, dy*t)`
    replaced by `zoom(buf, cx, cy, r**t)`. Both primitives compose the same way
    under addition of the exponent, which is the only property the doubling
    needs.
    """
    n = int(samples)
    if n < 1:
        return bright.copy()
    total = n + 1                                 # offsets 0..n inclusive

    # tables[k] = the sum over the DISJOINT block of offsets [0, 2^k)
    tables = [bright.copy()]
    while (1 << len(tables)) <= total:
        k = len(tables)
        half = 1 << (k - 1)
        tables.append(tables[k - 1]
                      + (decay ** half) * zoom(tables[k - 1], cx, cy, r ** half))

    # stitch [0, total) out of disjoint power-of-two blocks - the binary
    # expansion of `total`. Disjoint => nothing is counted twice.
    out = np.zeros_like(bright)
    base, wbase = 0, 1.0                          # wbase == decay ** base
    for k in range(len(tables) - 1, -1, -1):
        blk = 1 << k
        if base + blk <= total:
            out += wbase * zoom(tables[k], cx, cy, r ** base)
            wbase *= decay ** blk
            base += blk
    return out


def tail_only(rays, bright, decay, samples, intensity=1.0):
    """Drop the core (t=0) and normalise, so Intensity means the same thing at
    any reach / falloff / sample count.

    sum_{t=1..n} decay^t = decay * (1 - decay^n) / (1 - decay); scaling by its
    reciprocal keeps the shaft's nominal energy ~= 1 whichever way the other
    controls are set.
    """
    if decay >= 1.0:
        norm = 1.0 / max(samples, 1)
    else:
        s = decay * (1.0 - decay ** samples) / (1.0 - decay)
        norm = 1.0 / max(s, 1e-6)
    return (intensity * norm) * (rays - bright)


# ===========================================================================
#  test images + reporting
# ===========================================================================

def synthetic_sun(h=540, w=960):
    """A bright sky with a sun in it, cut up by hard occluders.

    Getting this subject right took two tries and the failures are instructive,
    so they are written down here rather than deleted.

    Star Glint's subject was isolated HDR points on black, because a streak is
    legible on black. Reusing that here produces almost nothing: a lone point
    smeared radially is just a small halo. A god ray is legible only when
    something INTERRUPTS a bright field - the shafts ARE the gaps between the
    occluders, and the dark wedges are the occluders' shadows thrown outward.

    So the source has to be a bright AREA, not a point:
      - the sky is above threshold, so it survives the bright pass
      - the slats punch holes in it, and those holes become the shafts
      - the sun is the hot core that sets where everything converges
      - the ground is below threshold and must stay clean
    """
    lin = np.zeros((h, w, 3), np.float32)
    ys, xs = np.ogrid[:h, :w]
    cx, cy = w * 0.32, h * 0.30

    # the sky: bright enough to clear a threshold of 1.0, warm-tinted
    for c, k in enumerate((1.0, 0.95, 0.86)):
        lin[..., c] = 1.25 * k

    # the sun: small and very hot. Small matters - the sweep MAGNIFIES, so a
    # large hot source just floods the frame.
    r2 = (xs - cx) ** 2 + (ys - cy) ** 2
    core = np.clip(1.0 - r2 / (26.0 ** 2), 0.0, 1.0) ** 1.5
    for c, k in enumerate((1.0, 0.90, 0.70)):
        lin[..., c] += core * 18.0 * k

    # occluders: slats of varied width, so the shafts are not a regular comb
    occ = np.ones((h, w), np.float32)
    widths = (34, 12, 52, 20, 8, 40, 16, 28, 10, 46, 18, 30, 14, 24, 38, 11)
    x = 0
    for i, wd in enumerate(widths * 3):
        if x >= w:
            break
        if i % 2 == 0:                            # every other block is solid
            occ[:, x:x + wd] = 0.0
        x += wd

    # the sun peeks through a gap - otherwise it lands behind a slat and the
    # convergence point of the shafts is invisible
    occ[r2 <= 40.0 ** 2] = 1.0

    # the ground: an unlit foreground ridge, below threshold
    ridge = ((ys - h * 0.72) * 2.4 + (xs - w * 0.04)) > 0
    ground = ridge & (ys > h * 0.60)
    occ[ground] = 1.0                             # slats stop at the horizon
    lin[ground] = 0.05

    lin *= np.where(ground, 1.0, occ)[..., None]

    # a dim grey bar on the ground: BELOW threshold, so it must NOT throw rays
    lin[int(h * 0.86):int(h * 0.90), int(w * 0.55):int(w * 0.88)] = 0.18

    # a dim grey bar: BELOW threshold, so it must NOT throw rays
    lin[int(h * 0.86):int(h * 0.90), int(w * 0.55):int(w * 0.88)] = 0.18
    return lin, (cx, cy)


def save_linear(name, lin):
    u8 = np.clip(linear_to_srgb(lin) * 255.0, 0, 255).astype(np.uint8)
    cv2.imwrite(name, cv2.cvtColor(u8, cv2.COLOR_RGB2BGR))


def describe(label, a):
    print(f"  {label:<14} shape={str(a.shape):<18} {a.dtype}  "
          f"min={a.min():8.4f}  max={a.max():9.4f}  mean={a.mean():8.4f}")


# ===========================================================================
#  --explain : run the pipeline one stage at a time, dumping each
# ===========================================================================

def explain(reach=0.45, falloff=0.06, samples=128, threshold=0.9, knee=0.3,
            intensity=0.6):
    print(f"\nPIPELINE  reach={reach}  falloff={falloff}  samples={samples}  "
          f"threshold={threshold}  knee={knee}  intensity={intensity}\n")

    lin, (cx, cy) = synthetic_sun()
    src_srgb = linear_to_srgb(lin)
    describe("0 src_srgb", src_srgb)
    save_linear("explain_0_src.png", lin)

    describe("1 linear", lin)
    save_linear("explain_1_linear.png", lin)

    bright = bright_pass(lin, threshold, knee)
    describe("2 bright", bright)
    save_linear("explain_2_bright.png", bright)
    frac = float((bright.max(axis=2) > 0).mean())
    print(f"                 -> {frac*100:.2f}% of pixels survived the bright pass")

    r = ratio_for(reach, samples)
    d = decay_for(falloff, samples)
    passes = int(np.floor(np.log2(samples + 1))) + 1
    print(f"                 -> centre=({cx:.0f},{cy:.0f})")
    print(f"                 -> r={r:.6f}   r^{samples} = {r**samples:.4f} "
          f"(that is 1-reach)")
    print(f"                 -> decay={d:.6f}   decay^{samples} = {d**samples:.4f} "
          f"(that is falloff)")
    print(f"                 -> {passes} doubling passes instead of {samples} resamples")

    rays = ray_sweep(bright, cx, cy, samples, r, d)
    describe("3 rays", rays)
    save_linear("explain_3_rays.png", rays)

    tail = tail_only(rays, bright, d, samples, intensity)
    describe("4 tail", tail)
    save_linear("explain_4_tail.png", tail)

    out = lin + tail
    describe("5 composite", out)
    save_linear("explain_5_composite.png", out)

    print("\n  wrote explain_0..5_*.png - open them in order.\n")


# ===========================================================================
#  main : prove the fast path, then render
# ===========================================================================

def _affine_probe(h, w):
    """An image that bilinear resampling reproduces EXACTLY.

    Star Glint could check its fast path bit-for-bit by picking on-axis angles,
    where a shift is a plain copy. A scale is never a plain copy, so that door
    is shut. But bilinear interpolation is exact on affine functions - and a
    scale of an affine function is still affine. So on this probe the sampler
    contributes no error at all, and any fast-vs-slow disagreement is a bug in
    the doubling algebra, not resampling noise. That is what check (a) needs.
    """
    xs, ys = _grid(h, w)
    f = 0.20 + 0.30 * (xs / w) + 0.25 * (ys / h)
    return np.repeat(f[..., None], 3, axis=2).astype(np.float32)


def main():
    ok = True

    print("=== (a) doubling algebra == literal march  (affine probe, exact) ===")
    # r < 1 means every sample lands INSIDE the frame, so the zero border never
    # participates and the probe stays affine everywhere it is read.
    for smp, reach, falloff in ((32, 0.5, 0.05), (100, 0.8, 0.02),
                                (128, 0.3, 0.30), (255, 0.9, 0.01)):
        h, w = 180, 240
        probe = _affine_probe(h, w)
        cx, cy = w * 0.37, h * 0.44
        r, d = ratio_for(reach, smp), decay_for(falloff, smp)
        a = ray_sweep(probe, cx, cy, smp, r, d)
        b = ray_sweep_slow(probe, cx, cy, smp, r, d)
        err = float(np.abs(a - b).max() / max(b.max(), 1e-6))
        good = err < 1e-4
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] samples={smp:4d} reach={reach:.1f} "
              f"falloff={falloff:.2f}  rel max|fast-slow| = {err:.2e}")

    print("\n=== (b) the same test on real content (resampling error, measured) ===")
    # Now the probe is NOT affine, so the sampler DOES contribute. This number
    # is not a pass/fail on the algebra - it is the price of the fast path, and
    # it is here so a later change that makes it worse is visible.
    rng = np.random.default_rng(0)
    for smp in (32, 128, 255):
        h, w = 180, 240
        probe = cv2.GaussianBlur(rng.random((h, w, 3)).astype(np.float32), (0, 0), 3.0)
        cx, cy = w * 0.37, h * 0.44
        r, d = ratio_for(0.6, smp), decay_for(0.05, smp)
        a = ray_sweep(probe, cx, cy, smp, r, d)
        b = ray_sweep_slow(probe, cx, cy, smp, r, d)
        rel = float(np.abs(a - b).max() / max(b.max(), 1e-6))
        mean = float(np.abs(a - b).mean() / max(b.mean(), 1e-6))
        good = rel < 0.05
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] samples={smp:4d}  "
              f"rel peak = {rel:.2e}   rel mean = {mean:.2e}")

    print("\n=== (c) a scale is NOT energy preserving - measure the growth ===")
    # Star Glint's check (b) leaned on shift() conserving a buffer's sum. zoom()
    # does not: magnifying by 1/r spreads light over 1/r^2 more pixels. Verify
    # the growth matches 1/r^2 per step, so we know the sampler is behaving and
    # not, say, losing light off the frame edge.
    for r in (0.99, 0.95, 0.90):
        blob = np.zeros((401, 401), np.float32)
        blob[195:206, 195:206] = 1.0              # centred, well inside
        got = float(zoom(blob, 200.0, 200.0, r).sum()) / float(blob.sum())
        want = 1.0 / (r * r)
        rel = abs(got - want) / want
        good = rel < 2e-2
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] r={r:.2f}  "
              f"sum ratio got={got:.4f}  1/r^2={want:.4f}  rel={rel:.2e}")

    print("\n=== (d) speed ===")
    big = cv2.GaussianBlur(rng.random((540, 960, 3)).astype(np.float32), (0, 0), 3.0)
    r, d = ratio_for(0.6, 255), decay_for(0.05, 255)
    t0 = time.time(); ray_sweep(big, 340.0, 180.0, 255, r, d);      tf = time.time() - t0
    t0 = time.time(); ray_sweep_slow(big, 340.0, 180.0, 255, r, d); ts = time.time() - t0
    print(f"  960x540, samples=255:  fast {tf*1e3:6.1f} ms   slow {ts*1e3:7.1f} ms   "
          f"({ts/max(tf,1e-6):.0f}x)")

    print("\n=== (e) renders ===")
    lin, (cx, cy) = synthetic_sun()
    save_linear("out_gr1_input.png", lin)
    print("  wrote out_gr1_input.png")
    for name, reach, falloff, inten in (("short", 0.20, 0.02, 0.6),
                                        ("classic", 0.45, 0.06, 0.6),
                                        ("long", 0.75, 0.15, 0.5)):
        smp = 192
        r, d = ratio_for(reach, smp), decay_for(falloff, smp)
        b = bright_pass(lin, 0.9, 0.3)
        tail = tail_only(ray_sweep(b, cx, cy, smp, r, d), b, d, smp, inten)
        save_linear(f"out_gr1_{name}.png", lin + tail)
        print(f"  wrote out_gr1_{name}.png  (reach={reach} falloff={falloff} I={inten})")

    # off-centre source, including one OUTSIDE the frame - a shot where the sun
    # is just past the edge is the single most common use of this effect.
    h, w = lin.shape[:2]
    for name, (ox, oy) in (("offscreen", (-0.18 * w, 0.20 * h)),
                           ("corner", (0.98 * w, 0.05 * h))):
        smp = 192
        r, d = ratio_for(0.45, smp), decay_for(0.06, smp)
        b = bright_pass(lin, 0.9, 0.3)
        tail = tail_only(ray_sweep(b, ox, oy, smp, r, d), b, d, smp, 0.6)
        save_linear(f"out_gr1_{name}.png", lin + tail)
        print(f"  wrote out_gr1_{name}.png  (centre=({ox:.0f},{oy:.0f}))")

    print("\nstep 1 validated." if ok else "\n*** FAST != SLOW - do not build on this ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

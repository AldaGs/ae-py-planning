r"""
God Rays - Step 2: SHIMMER, and a theorem that makes it free.

Step 1's shafts are perfectly clean: every ray is a smooth smear of whatever was
bright. Real light shafts are not clean - dust, haze and atmosphere break them
into a rake of brighter and darker rays. Shine calls this Shimmer, and it is the
single control that most separates "a radial blur" from "god rays".

The naive way to add it is to multiply the bright buffer by some noise before
sweeping. That works, and it costs nothing extra, and it is also NOT what this
file ends up doing - because measuring the naive version turned up something
better.

THE OBSERVATION
---------------
A scale about C preserves ANGLE about C. Exactly, not approximately:

    p' = C + (p - C) * r        =>      atan2(p' - C) == atan2(p - C)

for any r > 0. Scaling slides a point along the ray it is already on; it cannot
move it to a different ray. (This is the same fact that made the sweep fast in
step 1, seen from the other side: the sweep never mixes rays.)

THE CONSEQUENCE
---------------
The sweep at pixel p reads only the points C + (p-C)*r^t - every one of which
has the SAME angle theta(p). So if `m` is a function of angle alone:

    sweep(bright * m)(p) = sum_t decay^t * m(theta) * bright(...)
                         = m(theta) * sum_t decay^t * bright(...)
                         = m(theta) * sweep(bright)(p)

**Angular modulation commutes with the sweep.** You can apply the shimmer AFTER
the sweep - one multiply over the frame - instead of before it. Same picture.

That is worth three things:

  1. It is cheaper. One multiply instead of one multiply (a wash), except that
     the post-multiply version never resamples the noise, so it stays sharp.
  2. It is separable in the UI sense: animating Shimmer Phase does not
     invalidate the sweep. In AE that is the difference between a cached render
     and a full recompute on every frame.
  3. It is a *check*. Two independent code paths that must agree - and where
     they disagree tells you exactly how much the bilinear sampler is smearing
     rays into each other. Check (b) measures that.

Check (b) is the whole reason this file exists in the form it does.

THE TWO CONSTRAINTS THE NOISE HAS TO RESPECT
---------------------------------------------
1. **It must be periodic in theta with period 2*pi.** Sample any old 1D noise
   over [-pi, pi] and there is a visible seam along the -pi/+pi ray. A Fourier
   series with INTEGER frequencies is exactly periodic by construction, which is
   why `shimmer_field` is built from cosines rather than from value noise.

2. **It must fade out near C.** Angular frequency measured in PIXELS is k/radius
   - so at radius 3 a 32-cycle shimmer is asking for 10 cycles per pixel. It
   aliases into a hard, crawling starburst right at the source. The Nyquist
   radius is computable, not a taste call, and `radial_fade` uses it.

Run:
    python gr_step2_shimmer.py              # verify + render
    python gr_step2_shimmer.py --explain    # dump the field and the stages
"""

import sys
import time

import numpy as np
import cv2

from gr_step1_rays import (LUMA, bright_pass, decay_for, describe, _grid,
                           linear_to_srgb, ratio_for, ray_sweep, save_linear,
                           synthetic_sun, tail_only, zoom)


# ===========================================================================
#  the angle field
# ===========================================================================

def angle_map(h, w, cx, cy):
    """Per-pixel angle about C, in radians, in [-pi, pi].

    This is the coordinate the whole step lives in. Note what it does NOT
    depend on: radius. That is the point - see the module docstring.
    """
    xs, ys = _grid(h, w)
    return np.arctan2(ys - np.float32(cy), xs - np.float32(cx))


def radius_map(h, w, cx, cy):
    """Per-pixel distance from C, in pixels. Only used for the aliasing fade."""
    xs, ys = _grid(h, w)
    dx, dy = xs - np.float32(cx), ys - np.float32(cy)
    return np.sqrt(dx * dx + dy * dy)


# ===========================================================================
#  the shimmer field
# ===========================================================================

def harmonics(detail, octaves=3):
    """Integer frequencies for the Fourier series, plus 1/f amplitudes.

    INTEGER is the load-bearing word: cos(k*theta) is 2*pi-periodic for integer
    k and only for integer k. Any other frequency puts a seam down the -pi/+pi
    ray, which reads as one permanently wrong shaft.

    Octaves give it a fractal feel - a few broad rays with finer structure
    inside them - which is what dust in air actually looks like.
    """
    ks, amps = [], []
    for o in range(octaves):
        k = int(round(detail * (2 ** o)))
        if k < 1:
            k = 1
        ks.append(k)
        amps.append(0.5 ** o)
    return ks, amps


def shimmer_field(theta, detail=14.0, amount=0.6, phase=0.0, seed=3, octaves=3):
    """Multiplicative field m(theta), mean ~1, min clamped at 0.

    Returns a plain 2D array. `amount` 0 -> exactly 1.0 everywhere (the control
    must be able to turn itself off - same rule as Star Glint's style knobs).
    `phase` rotates the whole pattern and is the animatable one.
    """
    if amount <= 0.0:
        return np.ones_like(theta)

    ks, amps = harmonics(detail, octaves)
    rng = np.random.default_rng(seed)                 # deterministic per seed
    field = np.zeros_like(theta)
    for k, a in zip(ks, amps):
        ph = float(rng.uniform(0.0, 2.0 * np.pi))
        field += a * np.cos(k * theta + ph + phase * k)

    # normalise to unit peak so `amount` means the same thing at any octave
    # count - otherwise adding an octave would secretly deepen the shimmer.
    field /= sum(amps)
    return np.clip(1.0 + amount * field, 0.0, None).astype(np.float32)


def nyquist_radius(detail, octaves=3):
    """Radius inside which the finest harmonic aliases, in pixels.

    The finest frequency is k_max cycles per full turn. At radius R one pixel of
    arc subtends 1/R radians, so the local frequency in cycles-per-pixel is
    k_max / (2*pi*R). Nyquist is 0.5 cycles/pixel:

        k_max / (2*pi*R) = 0.5   ->   R = k_max / pi
    """
    ks, _ = harmonics(detail, octaves)
    return max(ks) / np.pi


def radial_fade(radius, detail, octaves=3, softness=4.0):
    """Ramp the shimmer off to 1.0 inside the aliasing radius.

    Without this, a high Detail puts a hard crawling starburst right at the
    source point - the single worst artefact in this effect, and one that only
    shows up when the centre is inside the frame.
    """
    r0 = nyquist_radius(detail, octaves)
    lo, hi = r0, r0 * softness
    t = np.clip((radius - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)   # smoothstep


def shimmer_mask(h, w, cx, cy, detail=14.0, amount=0.6, phase=0.0, seed=3,
                 octaves=3):
    """The full field, faded near C. This is what gets multiplied in."""
    if amount <= 0.0:
        return np.ones((h, w), np.float32)
    m = shimmer_field(angle_map(h, w, cx, cy), detail, amount, phase, seed,
                      octaves)
    f = radial_fade(radius_map(h, w, cx, cy), detail, octaves)
    return (1.0 + (m - 1.0) * f).astype(np.float32)       # lerp toward 1.0


# ===========================================================================
#  the two ways to apply it
# ===========================================================================

def rays_pre(bright, cx, cy, samples, r, decay, mask):
    """Modulate the source, THEN sweep. The obvious implementation."""
    return ray_sweep(bright * mask[..., None], cx, cy, samples, r, decay)


def rays_post(bright, cx, cy, samples, r, decay, mask):
    """Sweep, THEN modulate. Identical in exact arithmetic - see docstring.

    This is the one that ships: the noise is never resampled, and changing
    Phase does not invalidate the swept buffer.
    """
    return ray_sweep(bright, cx, cy, samples, r, decay) * mask[..., None]


# ===========================================================================
#  --explain
# ===========================================================================

def explain(reach=0.45, falloff=0.06, samples=128, threshold=0.9, knee=0.3,
            intensity=0.6, detail=14.0, amount=0.7, phase=0.0, seed=3):
    print(f"\nSHIMMER  detail={detail}  amount={amount}  phase={phase}  "
          f"seed={seed}\n")

    lin, (cx, cy) = synthetic_sun()
    h, w = lin.shape[:2]

    ks, amps = harmonics(detail)
    print(f"  harmonics (integer, so 2pi-periodic): {ks}")
    print(f"  amplitudes                          : {amps}")
    print(f"  nyquist radius                      : "
          f"{nyquist_radius(detail):.1f} px  (fade ends at "
          f"{nyquist_radius(detail)*4:.1f} px)")

    theta = angle_map(h, w, cx, cy)
    raw = shimmer_field(theta, detail, amount, phase, seed)
    describe("field(theta)", raw)
    save_linear("explain2_0_field.png", np.repeat(raw[..., None] * 0.35, 3, 2))

    mask = shimmer_mask(h, w, cx, cy, detail, amount, phase, seed)
    describe("mask (faded)", mask)
    save_linear("explain2_1_mask.png", np.repeat(mask[..., None] * 0.35, 3, 2))

    b = bright_pass(lin, threshold, knee)
    r, d = ratio_for(reach, samples), decay_for(falloff, samples)

    clean = ray_sweep(b, cx, cy, samples, r, d)
    describe("rays (clean)", clean)
    save_linear("explain2_2_clean.png", lin + tail_only(clean, b, d, samples,
                                                        intensity))

    post = rays_post(b, cx, cy, samples, r, d, mask)
    describe("rays (post)", post)
    save_linear("explain2_3_post.png", lin + tail_only(post, b * mask[..., None],
                                                       d, samples, intensity))

    pre = rays_pre(b, cx, cy, samples, r, d, mask)
    describe("rays (pre)", pre)
    save_linear("explain2_4_pre.png", lin + tail_only(pre, b * mask[..., None],
                                                      d, samples, intensity))

    diff = np.abs(pre - post).max() / max(post.max(), 1e-6)
    print(f"\n  pre vs post, relative peak difference = {diff:.3e}")
    print("  (exact arithmetic says 0; the gap is the bilinear sampler "
          "leaking\n   light between neighbouring rays)")
    print("\n  wrote explain2_0..4_*.png\n")


# ===========================================================================
#  main
# ===========================================================================

def _timed(fn):
    """Wall time of one call, in seconds. Used only by the timing checks."""
    t0 = time.time()
    fn()
    return time.time() - t0


def main():
    ok = True

    print("=== (a) a scale about C preserves ANGLE exactly ===")
    # The premise of the entire step. If this is not exact, nothing below holds.
    h, w = 240, 320
    cx, cy = w * 0.31, h * 0.44
    th = angle_map(h, w, cx, cy)
    xs, ys = _grid(h, w)
    for r in (0.99, 0.7, 0.25, 0.02):
        th2 = np.arctan2((ys - cy) * r, (xs - cx) * r)
        # ignore the singular pixel nearest C, where both angles are undefined
        rad = radius_map(h, w, cx, cy)
        m = rad > 1.5
        err = float(np.abs(np.angle(np.exp(1j * (th2 - th))))[m].max())
        good = err < 1e-5
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] r={r:.2f}  "
              f"max |angle drift| = {err:.2e} rad")

    print("\n=== (b) angular modulation COMMUTES with the sweep ===")
    # sweep(bright * m(theta)) == m(theta) * sweep(bright).  Exact in continuous
    # maths; the gap here is entirely the bilinear sampler leaking light between
    # neighbouring rays. This is a measurement, not a tolerance to tune.
    #
    # The prediction was "error grows with Detail, because finer rays sit closer
    # together and leak into each other more". The MEAN does exactly that
    # (0.9% -> 2.9%). The PEAK does the opposite (9.9% -> 0.8%), because the
    # worst single pixel is not a fine-ray-crosstalk pixel at all: at low Detail
    # the field has a few broad lobes with steep walls, and the peak error sits
    # on one of those walls. Two different mechanisms, and only the mean tracks
    # the one that was predicted. Reported both, rather than picking the one
    # that agreed.
    lin, (scx, scy) = synthetic_sun(360, 640)
    b = bright_pass(lin, 0.9, 0.3)
    H, W = b.shape[:2]
    r, d = ratio_for(0.45, 128), decay_for(0.06, 128)
    for detail in (4.0, 14.0, 40.0):
        mask = shimmer_mask(H, W, scx, scy, detail, 0.7, 0.0, 3)
        a = rays_pre(b, scx, scy, 128, r, d, mask)
        c = rays_post(b, scx, scy, 128, r, d, mask)
        rel = float(np.abs(a - c).max() / max(c.max(), 1e-6))
        mean = float(np.abs(a - c).mean() / max(c.mean(), 1e-6))
        good = rel < 0.25
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] detail={detail:5.1f}  "
              f"rel peak = {rel:.3e}   rel mean = {mean:.3e}")

    print("\n=== (c) the field is seamless across the -pi/+pi ray ===")
    # Integer harmonics make this exact. Verify by evaluating the field either
    # side of the seam and also by comparing against a DELIBERATELY broken
    # non-integer version, so the check cannot pass for the wrong reason.
    t_lo = np.float32([[-np.pi + 1e-6]])
    t_hi = np.float32([[np.pi - 1e-6]])
    for detail in (7.0, 14.0, 33.0):
        a = float(shimmer_field(t_lo, detail, 0.7, 0.0, 3)[0, 0])
        c = float(shimmer_field(t_hi, detail, 0.7, 0.0, 3)[0, 0])
        good = abs(a - c) < 1e-4
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] detail={detail:5.1f}  "
              f"m(-pi)={a:.6f}  m(+pi)={c:.6f}  gap={abs(a-c):.2e}")

    # the broken control: non-integer frequency
    rngp = np.random.default_rng(3)
    ph = float(rngp.uniform(0, 2 * np.pi))
    bad_lo = np.cos(14.5 * (-np.pi) + ph)
    bad_hi = np.cos(14.5 * (np.pi) + ph)
    print(f"  [note] non-integer k=14.5:  gap={abs(bad_lo-bad_hi):.2e}  "
          f"<- this is the seam the integers avoid")
    ok &= abs(bad_lo - bad_hi) > 0.1

    print("\n=== (d) the aliasing fade actually removes the aliasing ===")
    # Inside the Nyquist radius the field must be flat. Measure the worst
    # pixel-to-pixel swing of the mask in a disc around C, faded vs unfaded.
    detail = 40.0
    r0 = nyquist_radius(detail)
    faded = shimmer_mask(H, W, scx, scy, detail, 0.9, 0.0, 3)
    plain = shimmer_field(angle_map(H, W, scx, scy), detail, 0.9, 0.0, 3)
    rad = radius_map(H, W, scx, scy)
    core = rad < r0
    for name, m in (("unfaded", plain), ("faded", faded)):
        swing = float(m[core].max() - m[core].min())
        print(f"  {name:<8} peak-to-peak inside r<{r0:.1f}px = {swing:.4f}")
    swing_f = float(faded[core].max() - faded[core].min())
    swing_p = float(plain[core].max() - plain[core].min())
    good = swing_f < 0.02 and swing_p > 0.5
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] fade suppresses the core starburst")

    print("\n=== (e) amount=0 is EXACTLY step 1, and seeds are deterministic ===")
    off = shimmer_mask(H, W, scx, scy, 14.0, 0.0, 0.0, 3)
    good = float(np.abs(off - 1.0).max()) == 0.0
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] amount=0 -> mask is exactly 1.0")
    m1 = shimmer_mask(H, W, scx, scy, 14.0, 0.7, 0.0, 3)
    m1b = shimmer_mask(H, W, scx, scy, 14.0, 0.7, 0.0, 3)
    m2 = shimmer_mask(H, W, scx, scy, 14.0, 0.7, 0.0, 9)
    same = float(np.abs(m1 - m1b).max()) == 0.0
    diff = float(np.abs(m1 - m2).max()) > 0.1
    ok &= same and diff
    print(f"  [{'ok  ' if same else 'FAIL'}] same seed -> identical")
    print(f"  [{'ok  ' if diff else 'FAIL'}] different seed -> different")

    print("\n=== (f) cost: post-multiply vs re-sweeping for a Phase change ===")
    # WARM UP, then take the best of several runs. The first version of this
    # check timed one cold call each and reported 2977 ms / 438 ms / 6.8x. None
    # of those numbers reproduced: with the grid cache cold and the 1080p
    # buffers being touched for the first time, it was measuring allocation and
    # page faults, not the algorithm. A single timing run is not a measurement.
    big = bright_pass(synthetic_sun(1080, 1920)[0], 0.9, 0.3)
    BH, BW = big.shape[:2]
    bcx, bcy = BW * 0.32, BH * 0.30

    def best(fn, n=3):
        fn()                                   # warm-up, discarded
        return min(_timed(fn) for _ in range(n))

    swept = ray_sweep(big, bcx, bcy, 128, r, d)
    t_sweep = best(lambda: ray_sweep(big, bcx, bcy, 128, r, d))

    def phase_only():
        mk = shimmer_mask(BH, BW, bcx, bcy, 14.0, 0.7, 0.4, 3)
        return swept * mk[..., None]

    t_phase = best(phase_only)
    print(f"  1920x1080 (best of 3, warmed):  full sweep {t_sweep*1e3:7.1f} ms   "
          f"phase-only re-apply {t_phase*1e3:6.1f} ms   "
          f"({t_sweep/max(t_phase,1e-6):.1f}x)")

    print("\n=== (g) renders ===")
    lin, (cx, cy) = synthetic_sun()
    H2, W2 = lin.shape[:2]
    b = bright_pass(lin, 0.9, 0.3)
    r, d = ratio_for(0.45, 192), decay_for(0.06, 192)
    for name, detail, amount, seed in (("clean", 14.0, 0.0, 3),
                                       ("soft", 6.0, 0.45, 3),
                                       ("dusty", 14.0, 0.70, 3),
                                       ("fine", 34.0, 0.70, 3),
                                       ("dusty_s9", 14.0, 0.70, 9)):
        mask = shimmer_mask(H2, W2, cx, cy, detail, amount, 0.0, seed)
        rays = rays_post(b, cx, cy, 192, r, d, mask)
        tail = tail_only(rays, b * mask[..., None], d, 192, 0.6)
        save_linear(f"out_gr2_{name}.png", lin + tail)
        print(f"  wrote out_gr2_{name}.png  (detail={detail} amount={amount} "
              f"seed={seed})")

    # phase sweep - the animatable control, same seed
    for i, ph in enumerate((0.0, 0.05, 0.10)):
        mask = shimmer_mask(H2, W2, cx, cy, 14.0, 0.7, ph, 3)
        rays = rays_post(b, cx, cy, 192, r, d, mask)
        tail = tail_only(rays, b * mask[..., None], d, 192, 0.6)
        save_linear(f"out_gr2_phase{i}.png", lin + tail)
    print("  wrote out_gr2_phase0..2.png  (same seed, animating Phase)")

    print("\nstep 2 validated." if ok else "\n*** step 2 FAILED ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

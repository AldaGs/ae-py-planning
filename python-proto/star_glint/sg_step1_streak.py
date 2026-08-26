r"""
Star / Glint - Step 1: BRIGHT PASS + ONE STREAK ARM.

Effect #7 in the spec. The end goal is Starglow: N streak arms radiating from
every highlight, each with its own colour. This step builds the two pieces that
everything else is made of, and proves the fast one is correct.

    bright_pass(image)  ->  only the highlights, in linear light
    streak_arm(bright)  ->  smear those highlights along ONE direction

Do those two, then repeat the second one at 8 angles, and you have the effect.
Step 1 stops at one arm on purpose.

THE PIPELINE (this is what --explain dumps, in order)
-----------------------------------------------------
    0. src_srgb      the input as it arrives, gamma-encoded 0..1
    1. linear        undo sRGB gamma -> light-linear values
    2. bright        soft-knee threshold: keep highlights, kill the rest
    3. arm           bright, smeared along the streak direction
    4. tail          arm minus the core (the core is already in the image)
    5. composite     src + tail, back to sRGB

WHY LINEAR LIGHT (stage 1)
--------------------------
sRGB values are perceptual, not physical: 0.5 in a PNG is ~0.21 of the actual
light. Adding glow in that space makes bright things add up wrong (two 0.5
lights do not make a 1.0 light). Every additive optical effect - glow, streaks,
god rays - has to happen after the gamma is undone. This is the same spine
Bloom uses.

WHY A SOFT KNEE (stage 2)
-------------------------
A hard threshold (x if x > t else 0) makes a highlight pop into existence the
instant a pixel crosses t. On an animated shot that flickers. The soft knee
ramps the contribution in over a small window around t, so a pixel drifting
across the threshold fades in. Cost: about four lines.

THE STREAK MATH (stage 3) - the one piece of real algorithm here
----------------------------------------------------------------
A streak is a weighted sum of the bright buffer, marched backwards along a
direction d, each step dimmer than the last:

    arm(p) = sum_{t=0..n} decay^t * bright(p - t*d)

Done literally that is n texture reads per pixel - 300 reads for a 300px
streak. Too slow, and much too slow on a GPU.

The trick: because the weight is GEOMETRIC (decay^t), it FACTORS. Define
S_k(p) = the sum over the block of offsets [0, 2^k). Then

    S_k(p) = S_{k-1}(p)  +  decay^(2^(k-1)) * S_{k-1}(p - 2^(k-1) * d)
             \_first half_/  \____________second half_______________/

The second half covers offsets [2^(k-1), 2^k). Shifting it by 2^(k-1)*d slides
it on top of the first half's range, and EVERY term in it is short by exactly
the same factor decay^(2^(k-1)) - one scalar. So each pass DOUBLES the streak
length. log2(300) ~ 9 passes instead of 300. This is exact, not an
approximation, and each pass is a shift-and-add over the whole frame, which is
the shape a GPU likes.

That claim is what main() verifies: the fast version is compared against a
dumb literal march (streak_arm_slow), which is obviously correct because it is
just the formula typed out.

Run:
    python sg_step1_streak.py              # verify + render
    python sg_step1_streak.py --explain    # dump every stage as a numbered PNG
"""

import sys
import time

import numpy as np
import cv2


# ===========================================================================
#  colour transfer - sRGB <-> linear light
# ===========================================================================

#def srgb_to_linear(c):
#    """Undo the sRGB display curve. 0.5 in a PNG -> ~0.21 of actual light."""
#    c = np.asarray(c, np.float32)
#    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    """Re-apply the display curve on the way out. Negatives clamped first."""
    c = np.clip(np.asarray(c, np.float32), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1.0 / 2.4)) - 0.055)


LUMA = np.float32([0.2126, 0.7152, 0.0722])      # Rec.709, for linear light


# ===========================================================================
#  stage 2 - bright pass
# ===========================================================================

def bright_pass(lin, threshold=1.0, knee=0.5):
    """Keep only what is brighter than `threshold`, fading in over `knee`.

    `lin` is linear-light RGB (may exceed 1.0 - that is HDR, and is exactly
    where stars come from). Returns a buffer the same shape, mostly zeros.

    The weight curve on luminance y:
        y <= t-k        ->  0            fully rejected
        t-k < y < t+k   ->  smoothstep   fades in (this is the soft knee)
        y >= t+k        ->  1            fully kept

    We weight the ORIGINAL rgb rather than subtracting the threshold, so a warm
    highlight stays warm instead of drifting toward white.
    """
    y = lin @ LUMA                                # per-pixel luminance
    lo, hi = threshold - knee, threshold + knee
    if hi <= lo:                                  # knee == 0 -> hard threshold
        w = (y >= threshold).astype(np.float32)
    else:
        t = np.clip((y - lo) / (hi - lo), 0.0, 1.0)
        w = t * t * (3.0 - 2.0 * t)               # smoothstep
    return lin * w[..., None]


# ===========================================================================
#  stage 3 - the streak
# ===========================================================================

def direction(angle_deg):
    """Unit vector for an angle. 0 deg = +x (right), 90 deg = +y (down)."""
    a = np.deg2rad(angle_deg)
    return float(np.cos(a)), float(np.sin(a))


_GRID_CACHE = {}


def _grid(h, w):
    """The (xs, ys) sampling lattice for an h x w buffer, built once per size.

    `shift` is called hundreds of times per render on the SAME buffer size, and
    a fresh meshgrid at 1920x1080 is two 8 MB allocations each time. Caching it
    costs nothing and is worth more than most of the clever maths in this file
    (measured in step 4).
    """
    key = (h, w)
    g = _GRID_CACHE.get(key)
    if g is None:
        g = np.meshgrid(np.arange(w, dtype=np.float32),
                        np.arange(h, dtype=np.float32))
        _GRID_CACHE[key] = g
    return g


def shift(buf, ox, oy):
    """Sample buf at (x-ox, y-oy) with bilinear filtering, zero outside.

    This is the single primitive the whole doubling is built from. Fractional
    offsets are fine - that is why arbitrary angles work without special cases.
    """
    h, w = buf.shape[:2]
    xs, ys = _grid(h, w)
    # cast the offsets: a numpy scalar here would promote the maps to float64,
    # which cv2.remap rejects outright.
    ox, oy = np.float32(ox), np.float32(oy)
    return cv2.remap(buf, xs - ox, ys - oy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)


def decay_for(length, tip=.001):
    """Per-step dim factor so the streak has faded to `tip` at `length` px.

        decay ** length = tip   ->   decay = exp(ln(tip) / length)

    Deriving decay from length (instead of exposing both) means the Length
    control does what its name says: the streak really does end there.
    """
    return float(np.exp(np.log(tip) / max(length, 1.0)))


def streak_arm_slow(bright, angle, length, decay, step=1.0):
    """The formula, typed out literally. O(n) shifts. Ground truth."""
    dx, dy = direction(angle)
    out = bright.copy()                           # the t=0 term
    t = step
    while t <= length + 1e-6:
        out += (decay ** t) * shift(bright, dx * t, dy * t)
        t += step
    return out


def streak_arm(bright, angle, length, decay):
    """Same sum, O(log n) shifts, via geometric doubling. See module docstring."""
    dx, dy = direction(angle)
    n = int(np.floor(length))
    if n < 1:
        return bright.copy()
    samples = n + 1                               # offsets 0..n inclusive

    # tables[k] = the sum over the DISJOINT block of offsets [0, 2^k)
    tables = [bright.copy()]                      # k=0: just offset 0
    while (1 << len(tables)) <= samples:
        k = len(tables)
        half = 1 << (k - 1)
        tables.append(tables[k - 1]
                      + (decay ** half) * shift(tables[k - 1], dx * half, dy * half))

    # stitch [0, samples) out of disjoint power-of-two blocks - the binary
    # expansion of `samples`. Disjoint => nothing is counted twice.
    out = np.zeros_like(bright)
    base, wbase = 0, 1.0                          # wbase == decay ** base
    for k in range(len(tables) - 1, -1, -1):
        blk = 1 << k
        if base + blk <= samples:
            out += wbase * shift(tables[k], dx * base, dy * base)
            wbase *= decay ** blk
            base += blk
    return out


def tail_only(arm, bright, decay, intensity=1.0):
    """Drop the core (t=0) and normalise, so Intensity means the same thing at
    any Length.

    sum_{t>=1} decay^t = decay / (1 - decay), so scaling by its reciprocal
    keeps the streak's total energy ~= 1 whether it is 20px or 400px long.
    """
    norm = (1.0 - decay) / decay
    return (intensity * norm) * (arm - bright)


# ===========================================================================
#  test images + reporting
# ===========================================================================

def synthetic_points(h=540, w=960):
    """HDR points on black. The clearest possible subject for a streak."""
    lin = np.zeros((h, w, 3), np.float32)
    ys, xs = np.ogrid[:h, :w]

    def disc(cx, cy, r, rgb, peak):
        m = ((xs - cx) ** 2 + (ys - cy) ** 2) <= r * r
        for c in range(3):
            lin[..., c][m] = rgb[c] * peak

    disc(w * 0.30, h * 0.42, 3, (1.0, 1.0, 1.0), 8.0)     # white point
    disc(w * 0.62, h * 0.40, 2, (1.0, 0.6, 0.3), 12.0)    # warm point
    disc(w * 0.78, h * 0.66, 5, (0.5, 0.7, 1.0), 5.0)     # cool disc
    # a dim mid-grey bar: BELOW threshold, so it must NOT streak
    lin[int(h * 0.80):int(h * 0.84), int(w * 0.15):int(w * 0.45)] = 0.18
    return lin


def save_linear(name, lin):
    u8 = np.clip(linear_to_srgb(lin) * 255.0, 0, 255).astype(np.uint8)
    cv2.imwrite(name, cv2.cvtColor(u8, cv2.COLOR_RGB2BGR))


def describe(label, a):
    print(f"  {label:<14} shape={str(a.shape):<18} {a.dtype}  "
          f"min={a.min():8.4f}  max={a.max():9.4f}  mean={a.mean():8.4f}")


# ===========================================================================
#  --explain : run the pipeline one stage at a time, dumping each
# ===========================================================================

def explain(angle=20.0, length=200.0, threshold=1.0, knee=0.5, intensity=0.9):
    print(f"\nPIPELINE  angle={angle}  length={length}  "
          f"threshold={threshold}  knee={knee}  intensity={intensity}\n")

    lin = synthetic_points()
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

    d = decay_for(length)
    print(f"                 -> decay={d:.5f}  "
          f"({d:.5f}^{int(length)} = {d**length:.4f} at the tip)")

    arm = streak_arm(bright, angle, length, d)
    describe("3 arm", arm)
    save_linear("explain_3_arm.png", arm)

    tail = tail_only(arm, bright, d, intensity)
    describe("4 tail", tail)
    save_linear("explain_4_tail.png", tail)

    out = lin + tail
    describe("5 composite", out)
    save_linear("explain_5_composite.png", out)

    print("\n  wrote explain_0..5_*.png - open them in order.\n")


# ===========================================================================
#  main : prove the fast path, then render
# ===========================================================================

def main():
    ok = True

    print("=== (a) fast doubling == literal march, on-axis (should be exact) ===")
    rng = np.random.default_rng(0)
    probe = rng.random((120, 160, 3), np.float32).astype(np.float32)
    for ang, L in ((0.0, 100.0), (90.0, 60.0), (180.0, 45.0), (270.0, 33.0)):
        d = decay_for(L)
        err = float(np.abs(streak_arm(probe, ang, L, d)
                           - streak_arm_slow(probe, ang, L, d)).max())
        good = err < 2e-4
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] ang={ang:5.1f} L={L:5.1f}  "
              f"max|fast-slow| = {err:.2e}")

    print("\n=== (b) off-axis: energy matches the analytic geometric sum ===")
    # A bilinear shift preserves the total sum of a buffer, so a lone point's
    # streak must carry exactly sum_t decay^t of energy - at ANY angle. If this
    # holds, the streak has the right brightness regardless of direction.
    for ang in (0.0, 15.0, 33.0, 45.0, 200.0):
        L, pt = 90.0, np.zeros((400, 400), np.float32)
        pt[200, 200] = 1.0
        d = decay_for(L)
        analytic = float(sum(d ** t for t in range(int(L) + 1)))
        got = float(streak_arm(pt, ang, L, d).sum())
        rel = abs(got - analytic) / analytic
        good = rel < 2e-3
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] ang={ang:5.1f}  "
              f"analytic={analytic:6.3f}  got={got:6.3f}  rel={rel:.2e}")

    print("\n=== (c) speed ===")
    big = rng.random((540, 960, 3), np.float32).astype(np.float32)
    d = decay_for(300.0)
    t0 = time.time(); streak_arm(big, 20.0, 300.0, d);      tf = time.time() - t0
    t0 = time.time(); streak_arm_slow(big, 20.0, 300.0, d); ts = time.time() - t0
    print(f"  960x540, L=300:  fast {tf*1e3:6.1f} ms   slow {ts*1e3:7.1f} ms   "
          f"({ts/max(tf,1e-6):.0f}x)")

    print("\n=== (d) renders ===")
    lin = synthetic_points()
    save_linear("out_sg1_input.png", lin)
    print("  wrote out_sg1_input.png")
    for name, ang, L, inten in (("arm_right", 0.0, 220.0, 0.9),
                                ("arm_diag", 45.0, 220.0, 0.9),
                                ("arm_up", -90.0, 160.0, 0.9)):
        d = decay_for(L)
        b = bright_pass(lin, 1.0, 0.5)
        tail = tail_only(streak_arm(b, ang, L, d), b, d, inten)
        save_linear(f"out_sg1_{name}.png", lin + tail)
        print(f"  wrote out_sg1_{name}.png  (angle={ang} L={L} I={inten})")

    print("\nstep 1 validated." if ok else "\n*** FAST != SLOW - do not build on this ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

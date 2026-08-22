"""
Deep Glow - Step 3: the PYRAMID. A radius-1000 glow for the price of ~radius-30.

=====================================================================
  LECTURE: why a pyramid, and what a pyramid even is
=====================================================================

THE PROBLEM
-----------
Step 2's glow is one honest Gaussian with sigma ~ radius/3. A separable Gaussian
costs O(N * k) where N = pixels and k = kernel width ~ 6*sigma ~ 2*radius. So:

    radius   30  -> k ~   60 taps/pixel
    radius  200  -> k ~  400 taps/pixel
    radius 1000  -> k ~ 2000 taps/pixel   <- Deep Glow's default. Unusable.

Cost grows LINEARLY with radius. We want a glow whose cost DOESN'T care about
radius. The trick is to stop making the KERNEL bigger and start making the
IMAGE smaller.

THE ONE KEY IDEA: blur is relative to resolution
------------------------------------------------
Shrink the image to half size and run a SMALL blur on it. In the small image the
blur only spans, say, 4 pixels. But each small-image pixel covers 2 original
pixels, so back at full size that same 4-pixel blur now spans 8 ORIGINAL pixels.

    blur radius in ORIGINAL pixels = (small blur radius) x (downsample factor)

Halve again: the same 4-pixel blur now covers 16 original pixels. Halve k times
and a fixed tiny blur reaches 2^k original pixels. So we buy a HUGE reach not by
enlarging the kernel but by shrinking the picture. That's the whole secret.

WHAT A "PYRAMID" IS
-------------------
Take the extract and build a stack of ever-smaller, ever-blurrier copies:

    level 0 : full res              (finest, tightest glow)
    level 1 : 1/2 res, blurred
    level 2 : 1/4 res
    level 3 : 1/8 res
    ...
    level L : 1/2^L res             (coarsest, widest glow)

Drawn to scale the stack looks like a PYRAMID - hence the name (a "Gaussian
pyramid", from Burt & Adelson 1983, the same structure as mipmaps in 3D).

Each level, when blown back up to full size, is the extract blurred by roughly
2^level pixels. So the levels are a READY-MADE set of glows at doubling radii:
tight, medium, wide, huge - and we didn't run a single big kernel.

TURNING THE STACK INTO ONE GLOW
-------------------------------
A real light's falloff is one smooth curve, not four separate rings. We get that
smooth curve by ADDING the levels together (each upsampled back to full res),
with a WEIGHT per level:

    glow = sum_k  w_k * upsample_to_full( level_k )

- The wide levels fill in the far, soft tail.
- The tight levels add the near, bright core.
- Bilinear UPSAMPLING interpolates between the coarse samples, so the far tail
  comes out SMOOTH - no banding, no blocky mips. (This is why pyramid glow does
  not stair-step even though the top level might be 8x8 pixels.)

- The WEIGHTS w_k are the "shape" of the falloff. Equal weights = broad haze;
  weights that DECAY as you go coarser = a bright core with a soft skirt. This
  is literally Deep Glow's "Glow Mode" popup (Exponential vs Gaussian) - it's
  just choosing the w_k curve. RADIUS = how many levels you let in / how the
  weights are spread, NOT a kernel size.

WHY IT'S CHEAP (the punchline)
------------------------------
Level k has N / 4^k pixels. Total work over all levels:

    N * (1 + 1/4 + 1/16 + 1/64 + ...) = N * 4/3

The whole pyramid - ALL levels, up and down - costs about 1.33x a single
full-res pass, no matter how many levels. Radius 30 or radius 1000: same cost.
That geometric series collapsing to 4/3 is the number that makes wide glow
real-time. (Compare step 2's O(N*radius).)

EFFICIENT VARIANT (what plugins/GPUs actually do)
-------------------------------------------------
Instead of upsampling each level all the way to full res separately, do it
PROGRESSIVELY: start at the smallest level, upsample by 2, add the next-finer
level, upsample by 2, add the next... one octave at a time up to full res. Same
result, and every operation is just a 2x resample + add. This "downsample chain
then upsample-add chain" is the dual-filter / Kawase bloom, and it maps 1:1 onto
GPU passes later (each octave = one ping-pong pass, exactly like JFA/log-doubling
in Buildable Stroke).

We implement BOTH here:
  glow_pyramid_simple()      - sum of upsampled levels (clearest to read)
  glow_pyramid_progressive() - the octave-by-octave version (what we'll port)
and show they agree, and that both match step-2's slow Gaussian in look while
being radius-independent in cost.

Run:  python dg_step3_pyramid.py
"""

import time
import numpy as np
import cv2

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


# ----------------------------------------------------------- color / io (from step 2)
def srgb_to_linear(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92,
                    np.power(np.clip((c + 0.055) / 1.055, 0, None), 2.4))


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, np.float32), 0, None)
    return np.where(c <= 0.0031308, c * 12.92,
                    1.055 * np.power(c, 1 / 2.4) - 0.055)


def synthetic_on_black(w=900, h=520):
    rgb = np.zeros((h, w, 3), np.float32)
    cv2.rectangle(rgb, (120, 210), (360, 300), (1.0, 0.85, 0.35), -1)
    cv2.circle(rgb, (560, 180), 26, (3.0, 3.0, 3.0), -1)
    cv2.circle(rgb, (640, 340), 90, (0.2, 0.9, 1.0), 6)
    cv2.line(rgb, (770, 250), (770, 330), (0.3, 1.0, 0.3), 8)
    cv2.line(rgb, (730, 290), (810, 290), (0.3, 1.0, 0.3), 8)
    return rgb


def save(path, lin, is_linear=True):
    disp = linear_to_srgb(lin) if is_linear else lin
    cv2.imwrite(path, (np.clip(disp, 0, 1)[:, :, ::-1] * 255 + 0.5).astype(np.uint8))
    print("  wrote", path)


def side_by_side(a, b):
    gap = np.zeros((a.shape[0], 8, 3), a.dtype)
    return np.concatenate([a, gap, b], axis=1)


def extract(work):  # everything-glows, in linear
    return work * (work @ LUMA)[:, :, None]


# ================================================================= the pyramid
def build_downsample_chain(img, levels):
    """The Gaussian pyramid: [full, 1/2, 1/4, ...]. cv2.pyrDown = blur-then-halve.

    pyrDown applies a 5-tap Gaussian and drops every other pixel. The built-in
    small blur is exactly the 'fixed tiny blur' from the lecture; halving is what
    turns it into a doubling-radius stack.
    """
    chain = [img]
    for _ in range(levels):
        h, w = chain[-1].shape[:2]
        if h < 2 or w < 2:
            break
        chain.append(cv2.pyrDown(chain[-1]))
    return chain


def _up_to(img, shape_hw):
    """Bilinear upsample to an exact (H,W). Bilinear = the smoothing that kills
    banding between coarse samples."""
    return cv2.resize(img, (shape_hw[1], shape_hw[0]), interpolation=cv2.INTER_LINEAR)


def level_weights(levels, mode="exponential", decay=0.75):
    """The w_k falloff shape = Deep Glow's 'Glow Mode'.

      exponential : w_k = decay^k  -> bright tight core, soft wide skirt (default)
      gaussian    : bell over levels -> rounder, more evenly spread
      equal       : flat -> broad flat haze (mostly to show the knob matters)
    Normalized so total weight = 1 (glow energy independent of level count).
    """
    k = np.arange(levels + 1, dtype=np.float32)
    if mode == "exponential":
        w = decay ** k
    elif mode == "gaussian":
        c = levels / 2.0
        w = np.exp(-((k - c) ** 2) / (2 * (levels / 3.0 + 1e-6) ** 2))
    else:  # equal
        w = np.ones_like(k)
    return w / w.sum()


def glow_pyramid_simple(ex, levels, mode="exponential", decay=0.75):
    """CLEAREST version: upsample every level to full res, weight, sum.

    Reads exactly like the lecture's  glow = sum_k w_k * upsample(level_k).
    (Not the cheapest - upsamples each level from scratch - but unmistakable.)
    """
    chain = build_downsample_chain(ex, levels)
    w = level_weights(len(chain) - 1, mode, decay)
    full = ex.shape[:2]
    acc = np.zeros_like(ex)
    for k, lv in enumerate(chain):
        acc += w[k] * _up_to(lv, full)
    return acc


def glow_pyramid_progressive(ex, levels, mode="exponential", decay=0.75):
    """EFFICIENT version we'll port: octave-by-octave upsample+add.

    Start at the coarsest level; repeatedly upsample by one octave and add the
    next-finer level's weighted contribution. Every op is a 2x resize + add ->
    one GPU ping-pong pass per octave later.
    """
    chain = build_downsample_chain(ex, levels)
    w = level_weights(len(chain) - 1, mode, decay)
    acc = w[-1] * chain[-1]                         # start at the top (smallest)
    for k in range(len(chain) - 2, -1, -1):         # walk down to full res
        acc = _up_to(acc, chain[k].shape[:2])       # climb one octave
        acc += w[k] * chain[k]                       # add this level's detail
    return acc


# ----------------------------------------------------------------- reference (step 2)
def glow_single_gaussian(ex, radius):
    sigma = radius / 3.0
    k = max(int(sigma * 6) | 1, 3)
    return cv2.GaussianBlur(ex, (k, k), sigmaX=sigma, sigmaY=sigma,
                            borderType=cv2.BORDER_CONSTANT)


def composite(work_linear, glow_linear, exposure=1.0):
    return work_linear + exposure * glow_linear


# ----------------------------------------------------------------- run / observe
def main():
    print("Deep Glow step 3 - the pyramid\n")
    rgb = synthetic_on_black()
    work = srgb_to_linear(rgb)
    ex = extract(work)

    LEVELS = 7  # 1/128 res at the top - plenty for a huge glow on a 900px image

    # --- the two pyramid implementations must agree ---
    g_simple = glow_pyramid_simple(ex, LEVELS)
    g_prog = glow_pyramid_progressive(ex, LEVELS)
    diff = float(np.max(np.abs(g_simple - g_prog)))
    print(f"  simple vs progressive max|diff| = {diff:.4f}  (same glow, two ways)\n")

    save("out_dg3_pyramid.png", composite(work, g_prog))

    # --- glow-mode (weights) sweep: this is the falloff SHAPE knob ---
    for mode in ("exponential", "gaussian", "equal"):
        g = glow_pyramid_progressive(ex, LEVELS, mode=mode)
        save(f"out_dg3_mode_{mode}.png", composite(work, g))

    # --- 'radius' via number of levels: cost stays flat, reach grows ---
    for L in (2, 4, 7):
        g = glow_pyramid_progressive(ex, L)
        save(f"out_dg3_levels_{L}.png", composite(work, g))

    # --- head-to-head vs the slow single Gaussian, and TIMED ---
    def timeit(fn, n=5):
        fn()  # warmup
        t = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t) / n * 1000.0

    t_pyr = timeit(lambda: glow_pyramid_progressive(ex, LEVELS))
    t_g200 = timeit(lambda: glow_single_gaussian(ex, 200))
    t_g60 = timeit(lambda: glow_single_gaussian(ex, 60))
    print(f"  pyramid (7 levels, huge reach) : {t_pyr:6.2f} ms")
    print(f"  single Gaussian radius 60      : {t_g60:6.2f} ms")
    print(f"  single Gaussian radius 200     : {t_g200:6.2f} ms")
    print("  -> the pyramid's reach rivals the big Gaussian at a fraction of the")
    print("     cost, AND its cost does not grow with radius (4/3 * N, always).")

    g_ref = glow_single_gaussian(ex, 120)
    save("out_dg3_AB_gaussian_vs_pyramid.png",
         side_by_side(composite(work, g_ref), composite(work, g_prog)))
    print("\n  wrote AB: LEFT single Gaussian r120, RIGHT 7-level pyramid.")
    print("  Look: similar smooth falloff; the pyramid's is a touch wider &")
    print("  softer in the tail (sum of octaves) - the Deep Glow signature.")


if __name__ == "__main__":
    main()

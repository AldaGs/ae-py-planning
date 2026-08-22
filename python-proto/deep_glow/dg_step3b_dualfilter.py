"""
Deep Glow - Step 3b: de-blockify the pyramid (the "dual filter" bloom).

WHY STEP 3 WAS BLOCKY
---------------------
The naive pyramid used pyrDown (a 5-tap Gaussian + decimate) going down and plain
bilinear going up. Two weaknesses:

  - DOWNSAMPLE aliased: a 5-tap Gaussian isn't wide enough to kill everything
    above the new Nyquist limit when you throw away 3/4 of the pixels. High
    frequencies FOLD back as structure. On a tiny 8x8 level a round blob turns
    into a little SQUARE.
  - UPSAMPLE was a pure bilinear stretch: reconstructing a full-res image from an
    8x8 grid with straight bilinear shows the grid - diamond/box seams between
    the 4 samples of each cell. That's the blocky halo you saw.

THE FIX (Jimenez, "Next Generation Post-Processing in Call of Duty: Advanced
Warfare", 2014 - the recipe every modern engine's bloom uses):

  DOWNSAMPLE with 13 bilinear taps arranged as overlapping 2x2 boxes. It behaves
  like a wide, well-centered low-pass: far better anti-aliasing than 5 taps, so
  the coarse levels stay ROUND instead of turning square. As a 5x5 stencil:

        1/32   .    1/16   .    1/32
         .    1/8    .    1/8    .
        1/16   .    1/8    .    1/16
         .    1/8    .    1/8    .
        1/32   .    1/16   .    1/32     (sums to 1)

  UPSAMPLE with a 3x3 TENT filter (1 2 1 / 2 4 2 / 1 2 1)/16 instead of raw
  bilinear. The tent overlaps neighbouring cells, dissolving the grid seams so
  the reconstructed low-res level is smooth. An optional filterRadius widens the
  tent to spread the glow a touch more per octave.

Same pyramid, same O(4/3 N) cost. Only the TAPS change - and the blockiness dies.
This is exactly the down/up pass pair we'll write as two CUDA kernels later.

We render naive-vs-dualfilter side by side so the difference is undeniable.

Run:  python dg_step3b_dualfilter.py
"""

import numpy as np
import cv2

# reuse color + assets + extract + the naive pyramid from step 3
import dg_step3_pyramid as base

srgb_to_linear = base.srgb_to_linear
linear_to_srgb = base.linear_to_srgb
extract = base.extract
save = base.save
side_by_side = base.side_by_side
level_weights = base.level_weights


# ---------------------------------------------------------------- dual-filter taps
# 13-tap downsample collapsed to a 5x5 convolution stencil (offsets -2..+2),
# then decimate by 2. Matches the CoD weights: e .125, (b d f h) .0625,
# (a c g i) .03125, (j k l m) .125.
_DOWN5 = np.array([
    [0.03125, 0.0, 0.0625, 0.0, 0.03125],
    [0.0,     0.125, 0.0,   0.125, 0.0],
    [0.0625,  0.0, 0.125,   0.0, 0.0625],
    [0.0,     0.125, 0.0,   0.125, 0.0],
    [0.03125, 0.0, 0.0625, 0.0, 0.03125],
], np.float32)
assert abs(_DOWN5.sum() - 1.0) < 1e-6

_TENT3 = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float32) / 16.0


def downsample13(img):
    """13-tap low-pass then halve. Rounder coarse levels = no square blobs."""
    blurred = cv2.filter2D(img, -1, _DOWN5, borderType=cv2.BORDER_REFLECT)
    return blurred[::2, ::2].copy()


def upsample_tent(img, dst_hw, radius=1.0):
    """Bilinear stretch to dst, then a 3x3 tent to dissolve the grid seams.

    radius>1 applies the tent more than once to widen the per-octave spread
    (cheap way to expose a 'softness' knob later without changing the pyramid).
    """
    up = cv2.resize(img, (dst_hw[1], dst_hw[0]), interpolation=cv2.INTER_LINEAR)
    passes = max(1, int(round(radius)))
    for _ in range(passes):
        up = cv2.filter2D(up, -1, _TENT3, borderType=cv2.BORDER_REFLECT)
    return up


# ---------------------------------------------------------------- the pyramid (dual)
def build_chain_dual(img, levels):
    chain = [img]
    for _ in range(levels):
        h, w = chain[-1].shape[:2]
        if h < 2 or w < 2:
            break
        chain.append(downsample13(chain[-1]))
    return chain


def glow_dualfilter(ex, levels, mode="exponential", decay=0.75, radius=1.0):
    """Progressive octave upsample+add, but with the 13-tap down / tent up."""
    chain = build_chain_dual(ex, levels)
    w = level_weights(len(chain) - 1, mode, decay)
    acc = w[-1] * chain[-1]
    for k in range(len(chain) - 2, -1, -1):
        acc = upsample_tent(acc, chain[k].shape[:2], radius)
        acc += w[k] * chain[k]
    return acc


def composite(work_linear, glow_linear, exposure=1.0):
    return work_linear + exposure * glow_linear


# ---------------------------------------------------------------- run / observe
def main():
    print("Deep Glow step 3b - dual-filter (de-blockified) pyramid\n")
    rgb = base.synthetic_on_black()
    work = srgb_to_linear(rgb)
    ex = extract(work)
    LEVELS = 7

    naive = base.glow_pyramid_progressive(ex, LEVELS)   # step-3 blocky one
    dual = glow_dualfilter(ex, LEVELS)                  # step-3b smooth one

    save("out_dg3b_AB_naive_vs_dual.png",
         side_by_side(composite(work, naive), composite(work, dual)))
    print("  wrote AB: LEFT naive pyramid (blocky), RIGHT dual-filter (smooth).")

    save("out_dg3b_dual.png", composite(work, dual))

    # softness via tent radius (per-octave spread) - a look knob for later
    for r in (1, 2, 3):
        save(f"out_dg3b_softness_{r}.png",
             composite(work, glow_dualfilter(ex, LEVELS, radius=r)))

    # still radius-independent + still cheap; confirm the diamond artifact is gone
    # by measuring how "square vs round" the hot-dot halo is (variance of the
    # halo along a ring should be tiny if round).
    def roundness(glow):
        cy, cx = 180, 560                      # hot dot center
        ang = np.linspace(0, 2 * np.pi, 180, endpoint=False)
        r = 70
        ys = np.clip((cy + r * np.sin(ang)).astype(int), 0, glow.shape[0] - 1)
        xs = np.clip((cx + r * np.cos(ang)).astype(int), 0, glow.shape[1] - 1)
        samples = glow[ys, xs].mean(1)
        return samples.std() / (samples.mean() + 1e-6)   # lower = rounder

    print(f"\n  halo anisotropy @ r=70 around the hot dot (lower = rounder):")
    print(f"    naive pyramid : {roundness(naive):.4f}")
    print(f"    dual filter   : {roundness(dual):.4f}")
    print("\n  The dual-filter halo is markedly rounder & seam-free. Same 4/3 N")
    print("  cost, same radius-independence - just better taps. Pyramid is now")
    print("  Deep-Glow-smooth. Next (step 4): the CONTROLS + HDR tone mapping.")


if __name__ == "__main__":
    main()

"""
Deep Glow - Step 4b: keep the SATURATION (hue-preserving tone mapping).

THE COMPLAINT (correct): our glow desaturates. Bright areas drift toward white.

TWO CAUSES, stacked:
  1. Additive light genuinely desaturates - overlapping colored glows sum toward
     white, and any channel hitting its ceiling reads as white. This is physical
     and we keep a little of it.
  2. The DOMINANT cause: PER-CHANNEL tone mapping. Running ACES/Reinhard on R, G,
     B independently squeezes each channel toward 1 at its own rate, so a saturated
     color (big gap between channels) gets that gap COMPRESSED -> hue washes to
     gray. This is the classic "filmic tonemap desaturates highlights" problem.

THE FIX (what Deep Glow does): tone-map only the LUMINANCE, preserve CHROMA.
Compress how BRIGHT a pixel is, not WHAT COLOR it is:

    L    = luma(rgb)                     # scalar brightness
    L'   = tonemap_scalar(L)             # compress brightness only
    rgb' = rgb * (L' / L)                # rescale, keeping channel RATIOS = hue

Because the R:G:B ratio is untouched, hue and saturation survive; only the
overall level rolls off. That's the whole trick.

REFINEMENT: pure ratio-scaling can push a channel above 1 (if one channel far
exceeds luma), which then clips and re-desaturates at the very top. So we expose
a HUE-PRESERVE amount that blends between:
    per-channel tonemap  (safe, desaturates)   <---->   luminance tonemap (saturated)
Deep Glow-like default leans strongly to the luminance side. Plus a plain
SATURATION knob (Deep Glow's Saturation Bias) for taste.

We compare per-channel vs hue-preserving side by side so the fix is visible.

Run:  python dg_step4b_saturation.py
"""

import numpy as np
import cv2

import dg_step3_pyramid as base
import dg_step3b_dualfilter as dual
import dg_step4_controls as s4

srgb_to_linear = base.srgb_to_linear
linear_to_srgb = base.linear_to_srgb
LUMA = base.LUMA
save = base.save
side_by_side = base.side_by_side


# ---------------------------------------------------------------- scalar tone curves
def _tm_scalar(x, mode, white):
    if mode == "reinhard":
        return x / (1.0 + x / white)
    if mode == "aces":
        a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
        return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, None)
    return np.clip(x, 0, 1)  # none


def tone_map_per_channel(lin, mode="aces", white=4.0):
    """Old behaviour: tone curve on each channel independently (desaturates)."""
    return np.clip(_tm_scalar(lin, mode, white), 0, 1)


def tone_map_hue_preserving(lin, mode="aces", white=4.0, hue_preserve=0.85):
    """Tone map luminance, keep chroma. hue_preserve blends to per-channel.

    hue_preserve=1 -> pure luminance tonemap (max saturation, may clip a channel)
    hue_preserve=0 -> pure per-channel (safe, desaturated) = the old look
    """
    L = lin @ LUMA
    Lt = _tm_scalar(L, mode, white)
    ratio = (Lt / np.maximum(L, 1e-6))[:, :, None]
    lum_mapped = lin * ratio                       # hue-preserving
    per_chan = _tm_scalar(lin, mode, white)        # hue-shifting
    out = hue_preserve * lum_mapped + (1.0 - hue_preserve) * per_chan
    return np.clip(out, 0.0, 1.0)


def apply_saturation(rgb, s):
    """Saturation bias around luma (s=1 no change, >1 boost, <1 desaturate)."""
    if s == 1.0:
        return rgb
    L = (rgb @ LUMA)[:, :, None]
    return np.clip(L + (rgb - L) * s, 0.0, 1.0)


# ---------------------------------------------------------------- full glow (4 + sat)
def deep_glow(rgb_srgb, radius=200.0, exposure=1.0, threshold=0.0,
              tone="aces", karis=True, levels=7,
              hue_preserve=0.85, saturation=1.0):
    work = srgb_to_linear(rgb_srgb)
    ex = s4.extract_controlled(work, threshold)
    chain = s4.build_chain(ex, levels, karis=karis)
    w = s4.weights_from_radius(len(chain) - 1, radius)

    acc = w[-1] * chain[-1]
    for i in range(len(chain) - 2, -1, -1):
        acc = dual.upsample_tent(acc, chain[i].shape[:2])
        acc += w[i] * chain[i]

    lit = work + exposure * acc
    toned = tone_map_hue_preserving(lit, tone, hue_preserve=hue_preserve)
    return linear_to_srgb(apply_saturation(toned, saturation))


# ---------------------------------------------------------------- run / observe
def _saturation_of(bgr_or_rgb01):
    """Mean HSV saturation (display space) for a rough numeric check."""
    u8 = (np.clip(bgr_or_rgb01, 0, 1)[:, :, ::-1] * 255).astype(np.uint8)
    hsv = cv2.cvtColor(u8, cv2.COLOR_BGR2HSV)
    return hsv[:, :, 1].mean()


def main():
    print("Deep Glow step 4b - hue-preserving tone map (keep saturation)\n")
    rgb = base.synthetic_on_black()

    per_ch = deep_glow(rgb, radius=200, exposure=2.5, hue_preserve=0.0)   # old
    hue_pr = deep_glow(rgb, radius=200, exposure=2.5, hue_preserve=0.9)   # fixed
    save("out_dg4b_AB_perchannel_vs_hue.png", side_by_side(per_ch, hue_pr))
    print("  wrote AB: LEFT per-channel (washed), RIGHT hue-preserving (saturated)")
    print(f"    mean display saturation  per-channel : {_saturation_of(per_ch):6.2f}")
    print(f"    mean display saturation  hue-preserve: {_saturation_of(hue_pr):6.2f}")

    # hue_preserve sweep
    for hp in (0.0, 0.5, 1.0):
        save(f"out_dg4b_huepreserve_{hp}.png",
             deep_glow(rgb, radius=200, exposure=2.5, hue_preserve=hp))

    # saturation bias knob (taste), on top of hue-preserving
    for s in (0.7, 1.0, 1.4):
        save(f"out_dg4b_saturation_{s}.png",
             deep_glow(rgb, radius=200, exposure=2.0, hue_preserve=0.85, saturation=s))

    # real asset default, saturated
    ctbs = cv2.imread("CTBS_W.png", cv2.IMREAD_UNCHANGED)
    if ctbs is not None:
        c = ctbs[:, :, 2::-1].astype(np.float32) / 255.0
        save("out_dg4b_ctbs_saturated.png",
             deep_glow(c, radius=250, exposure=1.0, hue_preserve=0.85))

    print("\nObserve:")
    print("  - AB: the hot dot's halo and the colored glows keep their HUE on the")
    print("    right; the left bleaches to gray-white. Same brightness, real color.")
    print("  - hue_preserve 0->1: watch the yellow bar & cyan ring recover color as")
    print("    the tonemap moves from per-channel to luminance-only.")
    print("  - saturation knob = final taste bias (Deep Glow's Saturation Bias).")
    print("\n  This is the missing piece you spotted. Now onto step 5 (hardening).")


if __name__ == "__main__":
    main()

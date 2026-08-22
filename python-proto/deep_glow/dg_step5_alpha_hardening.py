"""
Deep Glow - Step 5: hardening for real footage (ALPHA, UNMULT, EDGES).

Everything so far assumed opaque RGB on a solid frame. Real AE layers are RGBA,
usually PREMULTIPLIED, often mostly TRANSPARENT, and the glow spreads PAST the
layer's pixels. Three things must be right or the effect is unusable:

============================================================ 1) GLOW NEEDS ALPHA
On a transparent comp, if the output alpha stays equal to the SOURCE alpha, the
glow is invisible - it has color but 0 alpha, so AE composites nothing where the
source was transparent. The glow must CARRY ITS OWN ALPHA.

So we build a glow ALPHA from the glow's coverage (its luminance), and combine:

    out_rgb = src_rgb + exposure*glow_rgb        (premultiplied, additive light)
    out_a   = src_a  +  glow_a  - src_a*glow_a   (screen of alphas: union coverage)

Now the glow is visible spilling into the transparent surround. This is exactly
Deep Glow's "Unmult (Required for Alpha)" checkbox: without it you get color with
no alpha and the glow vanishes on transparency.

======================================================== 2) PREMULT vs UNMULT
AE hands us PREMULTIPLIED pixels (rgb already multiplied by alpha), so semi-
transparent edge pixels are DARKER than the true surface color. If we extract
light straight from premultiplied values, edges under-glow (a dark fringe).

UNMULT = divide rgb by alpha to recover the true (straight) color before
extracting light, so edges glow at full strength. We expose it as a toggle
(matches the plugin). Fully-transparent pixels (a==0) contribute no light either
way.

============================================================== 3) EDGES / BOUNDS
A glow reaches RADIUS pixels beyond the shape. If the shape is near the frame
edge, that spill is CLIPPED unless the effect grows its output bounds. In AE that
is the I_EXPAND_BUFFER out-flag + the PreRender max_result_rect / result_rect
contract you already wired for Buildable Stroke and Extended Shadow. In the
prototype we emulate it by PADDING the buffer by the glow reach, doing the glow
in the padded buffer, then keeping the pad in the output. We show clipped vs
padded to prove the buffer-growth is required.

Output here is written as straight-alpha PNG composited over a CHECKERBOARD so
transparency is visible.

Run:  python dg_step5_alpha_hardening.py
"""

import numpy as np
import cv2

import dg_step3_pyramid as base
import dg_step3b_dualfilter as dual
import dg_step4_controls as s4
import dg_step4b_saturation as s4b

srgb_to_linear = base.srgb_to_linear
linear_to_srgb = base.linear_to_srgb
LUMA = base.LUMA


# ---------------------------------------------------------------- RGBA assets
def synthetic_rgba_transparent(w=900, h=520):
    """Bright shapes on a TRANSPARENT background (alpha 0), premultiplied.

    Includes a shape hard against the RIGHT edge to test bounds spill, and a
    semi-transparent soft shape to test the premult/unmult edge behavior.
    """
    rgb = np.zeros((h, w, 3), np.float32)
    a = np.zeros((h, w), np.float32)

    def stamp(mask, color):
        for c in range(3):
            rgb[:, :, c] = np.where(mask > 0, color[c], rgb[:, :, c])
        np.maximum(a, mask, out=a)

    m = np.zeros((h, w), np.float32); cv2.circle(m, (250, 260), 60, 1.0, -1)
    stamp(m, (0.2, 0.9, 1.0))                                   # cyan disc
    m = np.zeros((h, w), np.float32); cv2.rectangle(m, (470, 220), (560, 300), 1.0, -1)
    stamp(m, (3.0, 3.0, 3.0))                                   # hot white (HDR)
    m = np.zeros((h, w), np.float32); cv2.rectangle(m, (860, 180), (900, 340), 1.0, -1)
    stamp(m, (1.0, 0.3, 0.2))                                   # red bar AT right edge
    # a soft semi-transparent green blob (alpha ramp) to expose premult fringing
    yy, xx = np.mgrid[0:h, 0:w]
    soft = np.clip(1.0 - np.hypot(xx - 680, yy - 400) / 55.0, 0.0, 1.0)
    rgb[:, :, 1] = np.where(soft > 0, 1.0, rgb[:, :, 1])
    a = np.maximum(a, soft)

    rgb *= a[:, :, None]          # PREMULTIPLY (AE-style)
    return rgb, a


def checker(h, w, sq=32):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((xx // sq) + (yy // sq)) & 1).astype(np.float32)
    return 0.35 + 0.25 * c        # two grays


def save_over_checker(path, rgb_straight, alpha):
    h, w = alpha.shape
    bg = checker(h, w)[:, :, None] * np.ones(3, np.float32)
    comp = rgb_straight * alpha[:, :, None] + bg * (1.0 - alpha[:, :, None])
    cv2.imwrite(path, (np.clip(comp, 0, 1)[:, :, ::-1] * 255 + 0.5).astype(np.uint8))
    print("  wrote", path)


def side_by_side_rgba(a_rgb, a_a, b_rgb, b_a):
    h = a_a.shape[0]
    gap = np.zeros((h, 8), np.float32)
    return (np.concatenate([a_rgb, np.zeros((h, 8, 3), np.float32), b_rgb], 1),
            np.concatenate([a_a, gap, b_a], 1))


# ---------------------------------------------------------------- glow with alpha
def deep_glow_rgba(rgb_premult, alpha, radius=180.0, exposure=1.0, threshold=0.0,
                   unmult=True, tone="aces", hue_preserve=0.85, saturation=1.0,
                   karis=True, levels=7, pad=0):
    """Full hardened glow. Returns (straight rgb, alpha), both padded if pad>0."""
    if pad > 0:
        rgb_premult = np.pad(rgb_premult, ((pad, pad), (pad, pad), (0, 0)))
        alpha = np.pad(alpha, ((pad, pad), (pad, pad)))

    work = srgb_to_linear(rgb_premult)                 # premult linear light

    # --- light source for the glow (UNMULT recovers true edge color) ---
    if unmult:
        light = work / np.maximum(alpha, 1e-4)[:, :, None]
        light *= (alpha > 1e-4)[:, :, None]            # keep fully-transparent at 0
    else:
        light = work

    ex = s4.extract_controlled(light, threshold)

    # pyramid (reuse 4b machinery)
    chain = s4.build_chain(ex, levels, karis=karis)
    w = s4.weights_from_radius(len(chain) - 1, radius)
    acc = w[-1] * chain[-1]
    for i in range(len(chain) - 2, -1, -1):
        acc = dual.upsample_tent(acc, chain[i].shape[:2])
        acc += w[i] * chain[i]
    glow = exposure * acc                               # linear, premult-scale light

    # --- glow's OWN alpha from its coverage (luminance), so it shows on transparency ---
    glow_a = np.clip(glow @ LUMA, 0.0, 1.0)

    # composite: additive light (premult), union of alphas
    lit = work + glow
    out_a = np.clip(alpha + glow_a - alpha * glow_a, 0.0, 1.0)   # screen of alphas

    # tone map (hue-preserving) in premult, then unpremult to straight for output
    toned = s4b.tone_map_hue_preserving(lit, tone, hue_preserve=hue_preserve)
    toned = s4b.apply_saturation(toned, saturation)
    straight = linear_to_srgb(toned) / np.maximum(out_a, 1e-4)[:, :, None]
    straight = np.clip(straight, 0.0, 1.0)
    return straight, out_a


# ---------------------------------------------------------------- run / observe
def main():
    print("Deep Glow step 5 - alpha / unmult / edges\n")
    rgb, a = synthetic_rgba_transparent()

    # 1) GLOW ALPHA: without vs with a glow alpha, over checker.
    #    Simulate "no glow alpha" by forcing out_a = source alpha.
    s_rgb, s_a = deep_glow_rgba(rgb, a, radius=160, exposure=1.5)
    save_over_checker("out_dg5_glow_with_alpha.png", s_rgb, s_a)
    save_over_checker("out_dg5_glow_no_alpha.png", s_rgb, a)   # keep only source alpha
    print("  with_alpha: glow spills over the checker (visible on transparency)")
    print("  no_alpha  : glow clipped to source shape -> invisible surround\n")

    # 2) UNMULT on/off: the soft green blob & shape edges.
    u_on, ua = deep_glow_rgba(rgb, a, radius=120, exposure=1.5, unmult=True)
    u_off, ub = deep_glow_rgba(rgb, a, radius=120, exposure=1.5, unmult=False)
    save_over_checker("out_dg5_unmult_on.png", u_on, ua)
    save_over_checker("out_dg5_unmult_off.png", u_off, ub)
    print("  unmult_on : soft/edge pixels glow at true color strength")
    print("  unmult_off: premultiplied edges under-glow (darker fringe)\n")

    # 3) EDGES / BOUNDS: red bar at the right edge, clipped vs padded.
    clip_rgb, clip_a = deep_glow_rgba(rgb, a, radius=180, exposure=1.5, pad=0)
    pad = 120
    pad_rgb, pad_a = deep_glow_rgba(rgb, a, radius=180, exposure=1.5, pad=pad)
    save_over_checker("out_dg5_edge_clipped.png", clip_rgb, clip_a)
    save_over_checker("out_dg5_edge_padded.png", pad_rgb, pad_a)
    print(f"  edge_clipped: right-edge red glow cut at the frame boundary")
    print(f"  edge_padded : +{pad}px buffer -> glow spills past the old edge")
    print("    (this is I_EXPAND_BUFFER + max_result_rect in the AE port)\n")

    print("Hardening proven. Locked defaults for the port:")
    print("  radius 150, exposure 1.0, threshold 0 (everything glows),")
    print("  glow mode exponential, tone ACES, hue_preserve 0.85, unmult ON,")
    print("  karis ON. 8/16/32-bit: math is float already; reads/writes scale by")
    print("  white (255 / 32768 / 1.0) like your other effects. Ready for C++.")


if __name__ == "__main__":
    main()

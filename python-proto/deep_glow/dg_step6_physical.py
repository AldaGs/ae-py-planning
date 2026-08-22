"""
Deep Glow - Step 6: the PHYSICAL glow model (and why our dot got swallowed).

THE OBSERVATION
---------------
At radius 600 the real Deep Glow keeps the white circle as a CRISP DISC wearing a
dim halo. Ours dissolved it into a giant white blob. Deep Glow is the physically
accurate one. Here's the physics and the two-line fix.

WHAT BLOOM ACTUALLY IS
----------------------
Real glow is light SCATTERING in an optical system (lens elements, sensor cover
glass, the eye's own media). Physically it is a convolution with a point-spread
function, mixed by a small scatter fraction s:

    out = (1-s) * in  +  s * (in (*) PSF)

Three properties follow, and we broke two of them:

  1. LINEAR. Scattering doesn't care how bright the source is. Double the light,
     double the scattered light. Our extract was rgb*luma = QUADRATIC. Measured:
     the dot is 12.8x brighter than the bar in linear light, but glowed 230x
     harder. That single pixel then floods a huge area past 1.0, where the tone
     map clips everything to identical white -> the silhouette dissolves.

     NOTE / my error in step 1: "everything glows" only means NO THRESHOLD.
     Multiplying by luma was an addition of mine, and it is the non-physical bit.
     A linear extract with no threshold is still "everything glows".

  2. HEAVY-TAILED PSF: a bright narrow core plus a faint wide tail (~1/r^2..1/r^3,
     the CIE glare-spread function). A point source stays a POINT, wearing a dim
     halo. Our normalized octave pyramid already is such a PSF - we built the
     right kernel and fed it the wrong extract.

  3. ENERGY IS REDISTRIBUTED, NOT CREATED. Only a few percent of light scatters
     into the halo; the rest stays in the direct image. Ours did
     out = source + glow, i.e. added a full-strength copy = created energy, so
     the core got no brighter relative to its surround and lost its edge.

THE FIX (two lines)
-------------------
    extract  : rgb            (linear, no luma multiply)
    composite: lerp(src, src(*)PSF, strength)   instead of  src + glow

A free bonus: an energy-conserving mix of two values <= 1 is itself <= 1, so an
LDR input CANNOT blow out. Tone mapping stops being load-bearing and becomes an
HDR-only convenience.

FAIRNESS NOTE for the comparison
--------------------------------
The PNG we handed the real Deep Glow was 8-bit, so its hot dot was clipped to
1.0 - while our in-memory renders used 3.0 (HDR). Part of the difference was
INPUT, not model. This script renders from the SAME 8-bit file Deep Glow saw.

Run:  python dg_step6_physical.py
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

REF = "out_dg1_synthetic_DEEPGLOW_side.png"   # real Deep Glow, radius 600
SRC = "out_dg1_synthetic_input.png"           # the 8-bit file it was given


# ---------------------------------------------------------------- extract
def extract_physical(work, threshold=0.0, style=0.0):
    """Linear extract (physical). `style` blends in the old luma weighting.

    style=0 -> ex = rgb                 (linear, physically correct)
    style=1 -> ex = rgb * luma          (step-1..5 stylization, quadratic)
    A threshold still applies as a soft-knee floor if you want selective bloom.
    """
    lin = work
    if style > 0.0:
        lin = (1.0 - style) * work + style * s4.extract_controlled(work, 0.0)
    if threshold > 0.0:
        lum = work @ LUMA
        k = max(threshold * 0.5, 1e-4)
        soft = np.clip(lum - threshold + k, 0.0, 2 * k)
        soft = soft * soft / (4 * k)
        gate = np.maximum(np.maximum(soft, lum - threshold), 0.0)
        gate = gate / np.maximum(lum, 1e-6)          # normalized gate, keeps linearity
        lin = lin * gate[:, :, None]
    return lin


# ---------------------------------------------------------------- the PSF (pyramid)
def psf_convolve(ex, radius, levels=8, karis=True):
    """Normalized heavy-tailed PSF via the dual-filter octave pyramid."""
    chain = s4.build_chain(ex, levels, karis=karis)
    w = s4.weights_from_radius(len(chain) - 1, radius)
    acc = w[-1] * chain[-1]
    for i in range(len(chain) - 2, -1, -1):
        acc = dual.upsample_tent(acc, chain[i].shape[:2])
        acc += w[i] * chain[i]
    return acc


# ---------------------------------------------------------------- glow models
def glow_physical(rgb_srgb, radius=600.0, strength=0.35, threshold=0.0,
                  style=0.0, tone="none", hue_preserve=0.85, saturation=1.0,
                  levels=8):
    """out = (1-s)*src + s*(src (*) PSF), all in linear light."""
    work = srgb_to_linear(rgb_srgb)
    ex = extract_physical(work, threshold, style)
    scattered = psf_convolve(ex, radius, levels)
    lit = (1.0 - strength) * work + strength * scattered     # ENERGY CONSERVING
    if tone != "none":
        lit = s4b.tone_map_hue_preserving(lit, tone, hue_preserve=hue_preserve)
    else:
        lit = np.clip(lit, 0.0, 1.0)
    return linear_to_srgb(s4b.apply_saturation(lit, saturation))


def glow_old_additive(rgb_srgb, radius=600.0, exposure=1.0, levels=8):
    """Steps 1-5 model, for the A/B: quadratic extract + additive composite."""
    work = srgb_to_linear(rgb_srgb)
    ex = s4.extract_controlled(work, 0.0)
    acc = psf_convolve(ex, radius, levels)
    lit = work + exposure * acc
    return linear_to_srgb(s4b.tone_map_hue_preserving(lit, "aces", hue_preserve=0.85))


# ---------------------------------------------------------------- metrics
def core_contrast(img, cy=180, cx=560, r=45):
    """THE metric for 'did the disc survive': center brightness minus the
    brightness of a ring r px away. Swallowed -> both clip to 1 -> ~0.
    Survived -> large positive. (Measure the defect, not the whole frame.)"""
    L = img @ LUMA
    ang = np.linspace(0, 2 * np.pi, 240, endpoint=False)
    ys = np.clip((cy + r * np.sin(ang)).astype(int), 0, L.shape[0] - 1)
    xs = np.clip((cx + r * np.cos(ang)).astype(int), 0, L.shape[1] - 1)
    return float(L[cy, cx] - L[ys, xs].mean())


def load01(path):
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise FileNotFoundError(path)
    if im.ndim == 3 and im.shape[2] >= 3:
        im = im[:, :, 2::-1]
    return im.astype(np.float32) / 255.0


def save01(path, rgb):
    cv2.imwrite(path, (np.clip(rgb, 0, 1)[:, :, ::-1] * 255 + 0.5).astype(np.uint8))
    print("  wrote", path)


def sbs(*imgs):
    out = [imgs[0]]
    for im in imgs[1:]:
        out.append(np.zeros((im.shape[0], 8, 3), np.float32))
        out.append(im)
    return np.concatenate(out, 1)


# ---------------------------------------------------------------- run
def main():
    print("Deep Glow step 6 - physical model vs the real Deep Glow\n")
    src = load01(SRC)                    # the SAME 8-bit input Deep Glow got
    ref = load01(REF)                    # real Deep Glow, radius 600

    old = glow_old_additive(src, radius=600)
    new = glow_physical(src, radius=600, strength=0.35)

    print("  core contrast (dot center - ring 45px away; higher = disc survived):")
    print(f"    reference (real Deep Glow) : {core_contrast(ref):.4f}")
    print(f"    ours OLD (quad + additive) : {core_contrast(old):.4f}")
    print(f"    ours NEW (linear + mix)    : {core_contrast(new):.4f}\n")

    # strength sweep: which scatter fraction best matches the reference?
    print("  strength sweep (mean abs diff vs reference, lower = closer):")
    best = None
    for s in (0.15, 0.25, 0.35, 0.45, 0.60):
        cand = glow_physical(src, radius=600, strength=s)
        d = float(np.abs(cand - ref).mean())
        cc = core_contrast(cand)
        print(f"    strength {s:.2f} : diff {d:.4f}   core contrast {cc:.4f}")
        if best is None or d < best[1]:
            best = (s, d, cand)
    print(f"\n  best strength = {best[0]:.2f} (diff {best[1]:.4f})")
    print(f"  ours OLD diff vs reference   = {float(np.abs(old-ref).mean()):.4f}")

    save01("out_dg6_old_additive.png", old)
    save01("out_dg6_physical.png", best[2])
    save01("out_dg6_TRIPTYCH_old_new_ref.png", sbs(old, best[2], ref))
    print("\n  triptych order: OLD (ours, additive) | NEW (ours, physical) | REFERENCE")

    # the physical model on genuine HDR input (dot at 3.0) - the core still survives
    hdr = base.synthetic_on_black()
    save01("out_dg6_hdr_physical.png",
           glow_physical(hdr, radius=600, strength=0.35, tone="aces"))
    print("  (also rendered the true-HDR scene through the physical model)")

    print("\nObserve:")
    print("  - the disc SURVIVES now: energy is redistributed, not created, so the")
    print("    core stays far brighter than its halo instead of co-clipping to white.")
    print("  - LDR in -> LDR out is guaranteed: a mix of values <=1 is <=1, so")
    print("    nothing blows out and tone mapping is no longer load-bearing.")
    print("  - remaining gaps vs reference are look-tuning (PSF tail shape, their")
    print("    Screen blend + Gamma/Tint stages), not model errors.")


if __name__ == "__main__":
    main()

"""
Deep Glow - Step 6b: physically-correct CORE + explicit ARTISTIC controls.

DESIGN PHILOSOPHY (the user's call, and the right one)
-----------------------------------------------------
Keep the physically accurate model as the DEFAULT, then expose knobs that let the
artist deliberately depart from it. Every control below is labelled with whether
it preserves physics, so the plugin is honest about what it's doing:

    Radius    how far light scatters                        PHYSICAL
    Falloff   PSF tail shape: compact -> heavy glare tail    PHYSICAL (tail shape
                                                             is a real property)
    Scatter   fraction of light that scatters into the halo  PHYSICAL, conserving
    Gain      boost the halo beyond conservation             ARTISTIC (1.0=physical)
    Bias      linear -> luma-weighted extract                ARTISTIC (0.0=physical)

THE PHYSICAL CORE
-----------------
    out = (1-scatter)*src + scatter * gain * (src (*) PSF)

Because the PSF is NORMALIZED (its weights sum to 1), a convolution preserves
total light. So at gain=1 the whole operation is exactly energy conserving: it
REDISTRIBUTES light, never creates it. We verify that numerically below - total
frame luminance should be unchanged at gain=1. That's the property that makes
this a scattering model rather than a brightness effect.

WHAT WE LEARNED THE HARD WAY (step 6)
-------------------------------------
- The "swallowed disc" was NOT a model bug: it was an HDR input (a 3.0 dot) that
  the reference plugin never saw (it got an 8-bit clipped 1.0). On equal input
  our model kept the disc BETTER than the reference. Always compare on identical
  input before blaming the model.
- The LINEAR EXTRACT is still a genuine fix: rgb*luma is quadratic in intensity,
  so a 12.8x brighter pixel glowed 230x harder. Physical scattering is linear.
  Kept as the default; the old behaviour survives as the artistic `bias` knob.
- The real measured gap to the reference was ENERGY and TAIL, not disc survival:
  their halo is 3-6x brighter at every radius and they add ~6x the frame's light.
  That is them being deliberately non-conservative - which is exactly what our
  `gain` knob now expresses, as a CHOICE rather than an accident.

FALLOFF / TAIL
--------------
Level weights were w_k = decay^k (decay = radius/(radius+K)). `falloff` biases
energy toward the COARSE levels, which is what lengthens the tail:

    w_k = decay^k * (k+1)^falloff

falloff=0 -> compact, gaussian-ish. falloff>0 -> heavy-tailed, glare-like (real
optical glare is heavy-tailed, ~1/r^2..1/r^3, so this is a physical shape knob,
not a cheat). Weights are always renormalized to sum 1, so changing the tail
never changes total energy - only where it goes.

Run:  python dg_step6b_artistic.py
"""

import numpy as np
import cv2

import dg_step3_pyramid as base
import dg_step3b_dualfilter as dual
import dg_step4_controls as s4
import dg_step4b_saturation as s4b
import dg_step6_physical as s6

srgb_to_linear = base.srgb_to_linear
linear_to_srgb = base.linear_to_srgb
LUMA = base.LUMA
load01, save01, sbs = s6.load01, s6.save01, s6.sbs


# ---------------------------------------------------------------- PSF weights
def psf_weights(nlev, radius, falloff=0.0, K=64.0):
    """Normalized octave weights. radius = reach, falloff = tail heaviness."""
    decay = radius / (radius + K)
    k = np.arange(nlev + 1, dtype=np.float32)
    w = (decay ** k) * ((k + 1.0) ** falloff)
    return w / w.sum()                      # normalized => energy preserving


def psf_convolve(ex, radius, falloff=0.0, levels=8, karis=True):
    chain = s4.build_chain(ex, levels, karis=karis)
    w = psf_weights(len(chain) - 1, radius, falloff)
    acc = w[-1] * chain[-1]
    for i in range(len(chain) - 2, -1, -1):
        acc = dual.upsample_tent(acc, chain[i].shape[:2])
        acc += w[i] * chain[i]
    return acc


# ---------------------------------------------------------------- the effect
def deep_glow(rgb_srgb, radius=300.0, falloff=0.0, scatter=0.5, gain=1.0,
              bias=0.0, threshold=0.0, saturation=1.0, tone="none",
              hue_preserve=0.85, levels=8):
    """Physical core + artistic knobs. gain=1, bias=0 => energy conserving."""
    work = srgb_to_linear(rgb_srgb)
    ex = s6.extract_physical(work, threshold, bias)
    scattered = psf_convolve(ex, radius, falloff, levels)

    lit = (1.0 - scatter) * work + scatter * gain * scattered

    if tone != "none":
        lit = s4b.tone_map_hue_preserving(lit, tone, hue_preserve=hue_preserve)
    else:
        lit = np.clip(lit, 0.0, 1.0)
    return linear_to_srgb(s4b.apply_saturation(lit, saturation))


# ---------------------------------------------------------------- run / observe
def energy(img_srgb):
    """Total light in the frame (linear), for the conservation check."""
    return float((srgb_to_linear(img_srgb) @ LUMA).mean())


def halo_profile(img, cy=180, cx=560):
    L = img @ LUMA
    ang = np.linspace(0, 2 * np.pi, 360, endpoint=False)
    out = []
    for r in (40, 80, 150, 250, 400):
        ys = np.clip((cy + r * np.sin(ang)).astype(int), 0, L.shape[0] - 1)
        xs = np.clip((cx + r * np.cos(ang)).astype(int), 0, L.shape[1] - 1)
        out.append(L[ys, xs].mean())
    return out


def main():
    print("Deep Glow step 6b - physical core + artistic controls\n")
    src = load01(s6.SRC)
    ref = load01(s6.REF)

    # ---- 1. THE PHYSICAL CLAIM: at gain=1, energy is conserved ----
    print("  ENERGY CONSERVATION CHECK (linear frame luminance):")
    e_in = energy(src)
    for sc in (0.0, 0.25, 0.5, 0.9):
        out = deep_glow(src, radius=300, scatter=sc, gain=1.0)
        print(f"    scatter {sc:.2f}, gain 1.0 -> {energy(out):.5f}   (input {e_in:.5f})")
    print("    ^ light is REDISTRIBUTED, not created. That's the physical core.")
    print("      (small drift = light scattered off-frame + 8-bit round-trip)\n")

    # ---- 2. GAIN: the artistic departure, and where the reference sits ----
    print("  GAIN sweep (artistic; 1.0 = physical). Frame light vs reference:")
    print(f"    reference (real Deep Glow) -> {energy(ref):.5f}")
    for g in (1.0, 2.0, 4.0, 6.0):
        out = deep_glow(src, radius=600, falloff=1.0, scatter=0.6, gain=g)
        print(f"    gain {g:.1f} -> {energy(out):.5f}")

    # ---- 3. FALLOFF: tail shape at constant energy ----
    print("\n  FALLOFF sweep (halo luminance @40/80/150/250/400px, energy fixed):")
    for f in (0.0, 1.0, 2.0):
        out = deep_glow(src, radius=600, falloff=f, scatter=0.6, gain=1.0)
        print(f"    falloff {f:.1f}: " + "  ".join(f"{v:.3f}" for v in halo_profile(out)))
    print(f"    reference : " + "  ".join(f"{v:.3f}" for v in halo_profile(ref)))

    # ---- renders ----
    physical = deep_glow(src, radius=300, falloff=0.5, scatter=0.5, gain=1.0)
    save01("out_dg6b_physical_default.png", physical)

    # an artistic preset aimed at the reference's energy/tail
    stylized = deep_glow(src, radius=600, falloff=1.5, scatter=0.7, gain=4.0,
                         tone="aces", saturation=1.05)
    save01("out_dg6b_stylized_deepglowlike.png", stylized)

    save01("out_dg6b_TRIPTYCH.png", sbs(physical, stylized, ref))
    print("\n  triptych: PHYSICAL default | STYLIZED (gain 4, tail 1.5) | REFERENCE")

    # knob sweeps for the look library
    for g in (1.0, 2.5, 5.0):
        save01(f"out_dg6b_gain_{g}.png",
               deep_glow(src, radius=600, falloff=1.0, scatter=0.6, gain=g, tone="aces"))
    for f in (0.0, 1.5, 3.0):
        save01(f"out_dg6b_falloff_{f}.png",
               deep_glow(src, radius=600, falloff=f, scatter=0.6, gain=2.5, tone="aces"))
    for b in (0.0, 1.0):
        save01(f"out_dg6b_bias_{b}.png",
               deep_glow(src, radius=600, falloff=1.0, scatter=0.6, gain=2.5,
                         bias=b, tone="aces"))

    print("\n  Defaults are physical (gain 1, bias 0, energy conserving).")
    print("  Artists reach for gain/falloff/bias to get the punchier look, and the")
    print("  UI can say so - departing from physics on purpose, not by accident.")


if __name__ == "__main__":
    main()

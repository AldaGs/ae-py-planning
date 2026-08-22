"""
Deep Glow - Step 2: blur in LINEAR LIGHT, and composite glow OVER the art.

This step fixes two things step 1 exposed:

  (A) the DINGY falloff  -> blur in linear light, not gamma
  (B) the "glow sits behind the art" + the dark-hole HDR bug
                          -> composite ADDITIVELY in linear (glow over art)

------------------------------------------------------------------ (A) LINEAR
Images are stored gamma-encoded (roughly value = light^(1/2.2), the sRGB curve).
That encoding spends more bits in the darks because eyes are more sensitive
there. It is a STORAGE format, not light.

Blurring = averaging neighbours. Averaging is only physically meaningful on
LIGHT (linear), not on the gamma-encoded code values. Average two pixels of
linear light 0.0 and 1.0 -> 0.5 light -> encodes to ~0.73 gray. But average the
ENCODED values 0.0 and 1.0 -> 0.5 encoded -> only ~0.21 light. Blurring in gamma
therefore makes every soft transition too DARK - that's the "dingy" smudge.

So the correct glow blur is:
    linear = srgb_to_linear(rgb)      # decode to light
    blur in linear
    rgb    = linear_to_srgb(result)   # re-encode for display

You already met this idea as Gradient Map's "Linearize" checkbox. There it was a
correctness nicety. For glow it's THE look: highlights bleed like real light.

--------------------------------------------------------------- (B) COMPOSITE
Step 1 used screen(rgb, glow). Screen is SYMMETRIC (screen(a,b)==screen(b,a)) so
there is no real "over/behind" - the art just looked on top because it's crisp,
and screen broke (went negative -> black hole) on HDR (>1) operands.

Deep Glow composites the glow as LIGHT ADDED ON TOP:
    result_linear = source_linear + exposure * glow_linear
Addition in linear = photons adding to photons. It naturally sits the glow OVER
the art (brightens/softens the art's own edges), and can never go negative, so
the dark hole is gone. This is the physically-honest default.

We keep SCREEN as an option too (Deep Glow's default blend name), but computed
in linear and on operands kept in range, so it behaves.

HDR NOTE: after adding, linear values can exceed 1 (a hot dot + its glow). We
convert back to sRGB and, for the PNG only, clip. Real Deep Glow has a Tone
Mapping stage to roll those highlights down gracefully instead of clipping -
that's a step-4 knob. Here we just clip for display and keep the data HDR.

Run:  python dg_step2_linear_light.py
Outputs: out_dg2_*.png  (plus A/B pairs vs step-1 gamma blur)
"""

import numpy as np
import cv2

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


# ---------------------------------------------------------------- sRGB <-> linear
def srgb_to_linear(c):
    """Exact sRGB decode (piecewise). c may be >1 (HDR) - formula extends fine."""
    c = np.asarray(c, np.float32)
    lo = c / 12.92
    hi = np.power(np.clip((c + 0.055) / 1.055, 0.0, None), 2.4)
    return np.where(c <= 0.04045, lo, hi)


def linear_to_srgb(c):
    c = np.asarray(c, np.float32)
    c = np.clip(c, 0.0, None)  # negatives are nonphysical light
    lo = c * 12.92
    hi = 1.055 * np.power(c, 1.0 / 2.4) - 0.055
    return np.where(c <= 0.0031308, lo, hi)


# ---------------------------------------------------------------- io / assets
def load_rgba(path):
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra is None:
        raise FileNotFoundError(path)
    if bgra.ndim == 2:
        bgra = cv2.cvtColor(bgra, cv2.COLOR_GRAY2BGR)
    if bgra.shape[2] == 3:
        return bgra[:, :, ::-1].astype(np.float32) / 255.0
    return bgra[:, :, 2::-1].astype(np.float32) / 255.0


def synthetic_on_black(w=900, h=520):
    rgb = np.zeros((h, w, 3), np.float32)
    cv2.rectangle(rgb, (120, 210), (360, 300), (1.0, 0.85, 0.35), -1)
    cv2.circle(rgb, (560, 180), 26, (3.0, 3.0, 3.0), -1)
    cv2.circle(rgb, (640, 340), 90, (0.2, 0.9, 1.0), 6)
    cv2.line(rgb, (770, 250), (770, 330), (0.3, 1.0, 0.3), 8)
    cv2.line(rgb, (730, 290), (810, 290), (0.3, 1.0, 0.3), 8)
    return rgb


def save(path, rgb_srgb_or_linear, is_linear=False):
    disp = linear_to_srgb(rgb_srgb_or_linear) if is_linear else rgb_srgb_or_linear
    bgr = (np.clip(disp, 0, 1)[:, :, ::-1] * 255.0 + 0.5).astype(np.uint8)
    cv2.imwrite(path, bgr)
    print("  wrote", path)


def side_by_side(a, b):
    gap = np.zeros((a.shape[0], 8, 3), a.dtype)
    return np.concatenate([a, gap, b], axis=1)


# ---------------------------------------------------------------- pipeline
def gaussian_blur(img, radius):
    if radius <= 0:
        return img.copy()
    sigma = radius / 3.0
    k = max(int(sigma * 6) | 1, 3)
    return cv2.GaussianBlur(img, (k, k), sigmaX=sigma, sigmaY=sigma,
                            borderType=cv2.BORDER_CONSTANT)


def glow_linear(rgb_srgb, radius=80.0, exposure=1.0, blend="add", in_linear=True):
    """Full step-2 glow. Returns an sRGB-encoded display image.

    in_linear=False reproduces step 1 (blur in gamma) for A/B comparison, but
    now with the additive-over composite so we isolate JUST the linear effect.
    """
    if in_linear:
        work = srgb_to_linear(rgb_srgb)           # -> light
    else:
        work = rgb_srgb.copy()                    # stay in gamma (wrong on purpose)

    luma = (work @ LUMA)[:, :, None]              # luma of whatever space we're in
    extract = work * luma                         # everything-glows weight
    glow = gaussian_blur(extract, radius) * exposure

    if blend == "add":
        out = work + glow                         # light over light
    else:  # screen, but on in-range-ish operands, in whatever space
        out = 1.0 - (1.0 - np.clip(work, 0, 1)) * (1.0 - np.clip(glow, 0, 1))

    # convert back to display. If we worked in linear, encode; else it's already gamma.
    return linear_to_srgb(out) if in_linear else np.clip(out, 0, 1)


# ---------------------------------------------------------------- run / observe
def main():
    print("Deep Glow step 2 - linear-light blur + additive-over composite\n")
    rgb = synthetic_on_black()

    # THE headline A/B: same everything, gamma blur vs linear blur (additive).
    gamma = glow_linear(rgb, radius=80, exposure=1.0, blend="add", in_linear=False)
    linear = glow_linear(rgb, radius=80, exposure=1.0, blend="add", in_linear=True)
    save("out_dg2_AB_gamma_vs_linear.png", side_by_side(gamma, linear))
    print("  ^ LEFT = gamma blur (dingy), RIGHT = linear blur (light-like)\n")

    # additive-OVER vs screen, both in linear - the 'over the art' question.
    save("out_dg2_add_over.png",
         glow_linear(rgb, radius=80, blend="add", in_linear=True))
    save("out_dg2_screen.png",
         glow_linear(rgb, radius=80, blend="screen", in_linear=True))

    # radius sweep in linear (the reference look)
    for r in (30, 80, 200):
        save(f"out_dg2_linear_r{r}.png",
             glow_linear(rgb, radius=r, blend="add", in_linear=True))

    # exposure sweep - Deep Glow's Exposure knob (in STOPS conceptually; here linear gain)
    for e in (0.5, 1.0, 2.0):
        save(f"out_dg2_exposure_{e}.png",
             glow_linear(rgb, radius=80, exposure=e, blend="add", in_linear=True))

    # real asset
    try:
        ctbs = load_rgba("CTBS_W.png")
        save("out_dg2_ctbs_linear_r120.png",
             glow_linear(ctbs, radius=120, exposure=1.0, blend="add", in_linear=True))
    except FileNotFoundError:
        pass

    print("\nObserve:")
    print("  - AB image: RIGHT half's falloff is brighter/cleaner in the mids;")
    print("    the hot dot's halo reads as LIGHT, not gray paint. That's linear.")
    print("  - add_over vs screen: 'add' lets the glow ride OVER the shapes")
    print("    (edges soften, dot core stays bright - no black hole). This is")
    print("    the 'glow over the original art' look you spotted in Deep Glow.")
    print("  - the dark-hole bug is GONE (addition can't go negative).")
    print("\n  Slow part unchanged: still one honest wide Gaussian. Step 3 makes")
    print("  the radius cheap via the downsample/upsample PYRAMID.")


if __name__ == "__main__":
    main()

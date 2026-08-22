"""
Deep Glow - Step 4: the CONTROLS that turn the pipeline into a plugin.

We now have a smooth, cheap, radius-independent glow. This step adds the knobs a
user actually turns, each mapped to Deep Glow's UI:

  RADIUS      -> how wide the glow reaches
  EXPOSURE    -> glow brightness (gain, in stops)
  THRESHOLD   -> optional floor: from "everything glows" toward selective bloom
  GLOW MODE   -> falloff shape (the per-level weights, from step 3)
  TONE MAP    -> roll HDR highlights down gracefully instead of clipping to white
  KARIS AVG   -> firefly/flicker suppression (the Unreal addition)

------------------------------------------------------------------ RADIUS
The pyramid's reach is set by how much weight the COARSE (wide) levels get. So
Radius is really a WEIGHT-DECAY dial:

    decay = radius / (radius + K)

Small radius -> small decay -> weight collapses onto the fine levels -> tight
glow. Large radius -> decay -> 1 -> coarse levels contribute fully -> huge glow.
Continuous and smooth, no integer "number of levels" stair-stepping. (We keep a
tall pyramid always; the weights decide what's visible. Cost stays 4/3 N.)

------------------------------------------------------------------ THRESHOLD
"Everything glows" = luma weight, no floor. A THRESHOLD subtracts a brightness
floor first, so only pixels above it bloom - classic selective bloom. A hard
step causes popping as things cross the line, so we use a SOFT KNEE (a little
quadratic ramp around the threshold), the same curve Unreal/Jimenez use.

    threshold 0  -> unchanged "everything glows" (our default)
    threshold >0 -> only brighter-than-floor pixels contribute, soft-kneed

------------------------------------------------------------------ TONE MAP
Glow is additive in linear, so a hot spot + its glow can reach values far above 1
(our dot hits 164). Clipping them all to pure white looks flat and loses the
color of the glow. A tone map compresses [0, inf) -> [0, 1] with a gentle
shoulder so highlights stay bright but keep shape and hue. We offer:

    none      : clip (what steps 1-3 did)
    reinhard  : x / (1 + x/whitePoint)   - simple, soft, slightly desaturating
    aces      : filmic ACES approximation - punchier contrast, film-like

------------------------------------------------------------------ KARIS AVG
A single ultra-bright pixel (a specular ping, our 164 dot) dominates a downsample
box and, because of that, FLICKERS as it moves sub-pixel between frames
("fireflies"). Karis's fix: on the FIRST downsample only, weight each tap by
1/(1+luma) so one blazing pixel can't hijack the average. Tames fireflies with
almost no effect on the overall look.

Run:  python dg_step4_controls.py
"""

import numpy as np
import cv2

import dg_step3_pyramid as base
import dg_step3b_dualfilter as dual

srgb_to_linear = base.srgb_to_linear
linear_to_srgb = base.linear_to_srgb
LUMA = base.LUMA
save = base.save
side_by_side = base.side_by_side
_DOWN5 = dual._DOWN5
upsample_tent = dual.upsample_tent
downsample13 = dual.downsample13


# ---------------------------------------------------------------- extract w/ threshold
def extract_controlled(work, threshold=0.0, knee=0.5):
    """Luma-weighted extract, with an optional soft-knee threshold floor.

    threshold=0 -> pure everything-glows (work * luma).
    threshold>0 -> subtract a soft floor so only brighter pixels bloom.
    """
    lum = work @ LUMA                                   # (H,W), linear luma
    if threshold <= 0.0:
        weight = lum
    else:
        k = max(threshold * knee, 1e-4)
        soft = np.clip(lum - threshold + k, 0.0, 2 * k)
        soft = soft * soft / (4 * k)                    # quadratic knee
        weight = np.maximum(soft, lum - threshold)      # hard part above the knee
        weight = np.maximum(weight, 0.0)
    return work * weight[:, :, None]


# ---------------------------------------------------------------- Karis downsample
def downsample13_karis(img):
    """First-octave downsample with Karis weighting: tame the brightest taps."""
    # per-pixel Karis weight w = 1/(1+luma); apply as a weighted low-pass then /norm
    lum = img @ LUMA                                    # (H,W)
    w = 1.0 / (1.0 + lum)                               # (H,W)
    num = cv2.filter2D(img * w[:, :, None], -1, _DOWN5, borderType=cv2.BORDER_REFLECT)
    den = cv2.filter2D(w,                   -1, _DOWN5, borderType=cv2.BORDER_REFLECT)
    return (num / np.maximum(den, 1e-6)[:, :, None])[::2, ::2].copy()


def build_chain(img, levels, karis=True):
    chain = [img]
    for i in range(levels):
        h, wd = chain[-1].shape[:2]
        if h < 2 or wd < 2:
            break
        ds = downsample13_karis(chain[-1]) if (karis and i == 0) else downsample13(chain[-1])
        chain.append(ds)
    return chain


# ---------------------------------------------------------------- radius -> weights
def weights_from_radius(nlev, radius, K=64.0):
    """Continuous Radius dial via weight decay. Returns normalized weights."""
    decay = radius / (radius + K)                        # (0,1), ->1 for big radius
    k = np.arange(nlev + 1, dtype=np.float32)
    w = decay ** k
    return w / w.sum()


# ---------------------------------------------------------------- tone mapping
def tone_map(lin, mode="aces", white=4.0):
    if mode == "none":
        return np.clip(lin, 0, 1)
    if mode == "reinhard":
        return lin / (1.0 + lin / white)
    # ACES filmic approximation (Narkowicz)
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    x = lin
    return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)


# ---------------------------------------------------------------- full glow
def deep_glow(rgb_srgb, radius=200.0, exposure=1.0, threshold=0.0,
              tone="aces", karis=True, levels=7):
    work = srgb_to_linear(rgb_srgb)
    ex = extract_controlled(work, threshold)
    chain = build_chain(ex, levels, karis=karis)
    w = weights_from_radius(len(chain) - 1, radius)

    acc = w[-1] * chain[-1]
    for i in range(len(chain) - 2, -1, -1):
        acc = upsample_tent(acc, chain[i].shape[:2])
        acc += w[i] * chain[i]

    lit = work + exposure * acc                          # additive-over, linear
    return linear_to_srgb(tone_map(lit, tone))


# ---------------------------------------------------------------- run / observe
def main():
    print("Deep Glow step 4 - controls\n")
    rgb = base.synthetic_on_black()

    # RADIUS sweep (continuous, cost flat)
    for r in (40, 150, 600):
        save(f"out_dg4_radius_{r}.png", deep_glow(rgb, radius=r))

    # EXPOSURE sweep
    for e in (0.5, 1.0, 2.5):
        save(f"out_dg4_exposure_{e}.png", deep_glow(rgb, exposure=e))

    # THRESHOLD: everything-glows -> selective
    for t in (0.0, 0.3, 0.8):
        save(f"out_dg4_threshold_{t}.png", deep_glow(rgb, threshold=t, radius=150))

    # TONE MAP comparison on the HDR dot (this is where clipping vs shoulder shows)
    save("out_dg4_tone_AB.png",
         side_by_side(deep_glow(rgb, exposure=2.5, tone="none"),
                      deep_glow(rgb, exposure=2.5, tone="aces")))
    print("  wrote tone AB: LEFT clip (flat white), RIGHT ACES (keeps shape/hue)")

    # KARIS on/off (firefly suppression) - subtle but real on the 164 dot
    save("out_dg4_karis_AB.png",
         side_by_side(deep_glow(rgb, karis=False), deep_glow(rgb, karis=True)))

    # real asset, a tasteful default
    try:
        ctbs = base.__dict__  # noop guard
        ctbs_rgb = cv2.imread("CTBS_W.png", cv2.IMREAD_UNCHANGED)
        if ctbs_rgb is not None:
            c = (ctbs_rgb[:, :, 2::-1] if ctbs_rgb.shape[2] >= 3 else ctbs_rgb)
            c = c.astype(np.float32) / 255.0
            save("out_dg4_ctbs_default.png",
                 deep_glow(c, radius=250, exposure=1.0, threshold=0.0, tone="aces"))
    except Exception as ex:
        print("  (ctbs skipped:", ex, ")")

    print("\nObserve:")
    print("  - radius: reach grows smoothly, no stair-steps, same cost.")
    print("  - threshold 0 vs 0.3 vs 0.8: dim shapes stop glowing as the floor")
    print("    rises; the hot dot survives longest. Soft-kneed, no popping.")
    print("  - tone AB: clipping flattens hot areas to featureless white; ACES")
    print("    keeps the highlight rolloff and the glow's color.")
    print("\n  Pipeline is now a full plugin's worth of controls. Step 5 =")
    print("  hardening (alpha/unmult, edge behavior, defaults) -> then C++ port.")


if __name__ == "__main__":
    main()

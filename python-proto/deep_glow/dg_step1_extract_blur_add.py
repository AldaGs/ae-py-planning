"""
Deep Glow - Step 1: the whole bloom pipeline, end-to-end, on purpose UGLY.

THEORY
------
A glow / bloom is three stages, and every glow plugin ever made is a variation
on them:

    1. EXTRACT   pull out the "glowing" signal from the image
    2. BLUR      spread that signal out spatially (this IS the glow shape)
    3. COMPOSITE add it back over the original

We picked the DEEP-GLOW flavor: "everything glows", LUMINANCE-WEIGHTED. There is
NO hard threshold (Deep Glow's default Threshold = 0%). Instead every pixel
contributes to the glow in proportion to how BRIGHT it is:

    extract(p) = rgb(p) * luma(p)          # bright pixels bloom more, dark ~none

That single multiply is the "everything glows" model. (Later, a Threshold knob
just subtracts a floor before this - classic bloom is the same pipeline with a
hard gate instead of a soft weight. We build the soft one first, per your pick.)

Stage 2 here is ONE Gaussian blur. That's the honest, slow, correct reference:
a real wide Gaussian. It looks right but is expensive at large radius - which is
the entire motivation for the PYRAMID in step 3. We want to SEE the correct
result first so the pyramid has something to match.

Stage 3 is SCREEN (Deep Glow's default blend):  screen(a,b) = 1-(1-a)(1-b).
Screen never clips to >1 and brightens like light adding to light - the right
compositing model for glow. (Plain ADD also works and blows out; screen is
gentler. We expose both so you can compare.)

WHAT'S DELIBERATELY WRONG IN STEP 1
-----------------------------------
We blur in GAMMA space (i.e. straight on the 0..1 sRGB-ish values). This is what
the cheap glows do and it's why they look like gray smudge: blurring gamma-
encoded values averages them too dark, so highlights don't bleed with that
"light spilling" feel. Step 2 moves the blur into LINEAR light and we A/B it -
that's the moment the look clicks. Building it wrong first is the point.

Run:  python dg_step1_extract_blur_add.py
Outputs: out_dg1_*.png
"""

import numpy as np
import cv2

# Rec.709 luma weights - same ones you locked on Gradient Map.
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


# ---------------------------------------------------------------- io / assets
def load_rgba(path):
    """Return (rgb float32 0..1, alpha float32 0..1). White bg -> alpha 1."""
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra is None:
        raise FileNotFoundError(path)
    if bgra.ndim == 2:
        bgra = cv2.cvtColor(bgra, cv2.COLOR_GRAY2BGR)
    if bgra.shape[2] == 3:
        rgb = bgra[:, :, ::-1].astype(np.float32) / 255.0
        alpha = np.ones(bgra.shape[:2], np.float32)
        return rgb, alpha
    alpha = bgra[:, :, 3].astype(np.float32) / 255.0
    rgb = bgra[:, :, 2::-1].astype(np.float32) / 255.0
    return rgb, alpha


def synthetic_on_black(w=900, h=520):
    """A few bright shapes on BLACK so the glow is unmistakable.

    White bg hides glow (everything already bright); black bg makes the bloom
    obvious. We test on both.
    """
    rgb = np.zeros((h, w, 3), np.float32)
    # bright warm bar
    cv2.rectangle(rgb, (120, 210), (360, 300), (1.0, 0.85, 0.35), -1)
    # a hot (HDR-ish) white dot - value >1 to show glow feeds on real intensity
    cv2.circle(rgb, (560, 180), 26, (3.0, 3.0, 3.0), -1)
    # thin cyan ring
    cv2.circle(rgb, (640, 340), 90, (0.2, 0.9, 1.0), 6)
    # small green plus
    cv2.line(rgb, (770, 250), (770, 330), (0.3, 1.0, 0.3), 8)
    cv2.line(rgb, (730, 290), (810, 290), (0.3, 1.0, 0.3), 8)
    alpha = np.ones((h, w), np.float32)
    return rgb, alpha


def save(path, rgb):
    """Write RGB float (may exceed 1 = HDR) to 8-bit PNG, clamped for display."""
    disp = np.clip(rgb, 0.0, 1.0)
    bgr = (disp[:, :, ::-1] * 255.0 + 0.5).astype(np.uint8)
    cv2.imwrite(path, bgr)
    print("  wrote", path)


# ---------------------------------------------------------------- the pipeline
def luma_of(rgb):
    return rgb @ LUMA  # (H,W)


def extract_everything_glows(rgb):
    """Luminance-weighted extract: bright pixels bloom, dark pixels ~nothing.

    extract = rgb * luma. No threshold (Deep Glow default). Returns HDR-safe
    (can exceed 1 where the input is hot - that's wanted, glow feeds on light).
    """
    l = luma_of(rgb)[:, :, None]  # (H,W,1)
    return rgb * l


def gaussian_blur(img, radius):
    """One honest wide Gaussian. sigma ~ radius/3 (3-sigma ~ visible reach).

    cv2 wants an odd ksize; we size it to ~6 sigma. This is the SLOW reference
    the pyramid must reproduce. Cost ~ O(N * ksize) even separably -> hurts a
    lot at radius 1000, which is exactly why step 3 exists.
    """
    if radius <= 0:
        return img.copy()
    sigma = radius / 3.0
    k = int(sigma * 6) | 1  # force odd
    k = max(k, 3)
    return cv2.GaussianBlur(img, (k, k), sigmaX=sigma, sigmaY=sigma,
                            borderType=cv2.BORDER_CONSTANT)


def screen(a, b):
    """Screen blend - light adds to light, soft-clips toward 1."""
    return 1.0 - (1.0 - a) * (1.0 - b)


def glow(rgb, radius=60.0, exposure=1.0, blend="screen"):
    """extract -> gaussian blur -> composite back. exposure scales the glow."""
    glow_src = extract_everything_glows(rgb)
    blurred = gaussian_blur(glow_src, radius) * exposure
    if blend == "screen":
        # screen wants 0..1-ish operands; clamp the glow's soft-clip input only
        return screen(rgb, np.clip(blurred, 0.0, None))
    else:  # add
        return rgb + blurred


# ---------------------------------------------------------------- run / observe
def main():
    print("Deep Glow step 1 - extract, blur (gamma space), composite\n")

    # --- synthetic on black: glow is obvious here ---
    rgb, _ = synthetic_on_black()
    save("out_dg1_synthetic_input.png", rgb)
    save("out_dg1_synthetic_extract.png", extract_everything_glows(rgb))
    for r in (30, 80, 200):
        save(f"out_dg1_synthetic_screen_r{r}.png",
             glow(rgb, radius=r, exposure=1.0, blend="screen"))
    save("out_dg1_synthetic_add_r80.png",
         glow(rgb, radius=80, exposure=1.0, blend="add"))

    # --- your real asset (white bg): everything blooms, washes out ---
    try:
        rgb2, _ = load_rgba("CTBS_W.png")
        save("out_dg1_ctbs_screen_r80.png",
             glow(rgb2, radius=80, exposure=1.0, blend="screen"))
        print("\n  NOTE on CTBS_W: white bg is fully bright, so 'everything")
        print("  glows' blooms the whole frame - that's correct behavior, and")
        print("  it's exactly why the reference default render looks washed.")
    except FileNotFoundError:
        print("  (CTBS_W.png not found, skipped real-asset run)")

    print("\nObserve:")
    print("  - extract.png: dark stays dark, bright shapes kept & weighted.")
    print("  - screen r30/r80/r200: same shape, bloom widens with radius.")
    print("  - add vs screen: add blows out hot spots harder; screen is softer.")
    print("  - it already reads as glow, but the falloff is a touch DINGY -")
    print("    that's the gamma-space blur. Step 2 blurs in LINEAR light.")


if __name__ == "__main__":
    main()

"""
Gradient Map - Step 1: Luminance (reducing a color pixel to one number).

Goal: a gradient map works in two moves:
    (1) collapse each pixel's COLOR down to a single "tone" value t in [0,1],
    (2) use that t to look up a color in a ramp (that's Step 2).
This step is only about move (1): getting t. It looks trivial, but *how* you
compute luminance is the whole reason a gradient map looks right instead of
muddy. We compute it three ways and compare, so you can SEE why Rec.709 wins.

Why three ways?
    - average    = (R+G+B)/3          -> treats all channels as equal. Wrong:
                                         green LOOKS much brighter than blue to
                                         the eye, so pure blue and pure green
                                         come out the same tone. Bad.
    - luminosity = 0.299R+0.587G+0.114B  (Rec.601, the old TV standard)
    - rec709     = 0.2126R+0.7152G+0.0722B  (what HD/AE working space uses)
Rec.709 is the one to port to the plugin: it matches how AE weights light, so
your tones land where a colorist expects.

Run:  python gm_step1_luminance.py
Then open the PNGs and compare the three grays, especially in the color bars.
"""

import numpy as np
import cv2


def make_test_image(size=400):
    """A test image built to EXPOSE luminance differences.

    Top half: a smooth left->right black-to-white ramp. This is the ideal
    gradient-map test bed - every tone from 0 to 1 appears exactly once, so
    after mapping you can read the whole ramp like a color chart.

    Bottom half: pure-color bars (red, green, blue, yellow). These are where
    the three luma formulas DISAGREE the most - watch how green vs blue shift.
    """
    img = np.zeros((size, size, 3), dtype=np.uint8)

    # Top half: horizontal luminance ramp. np.linspace makes 0..255 across width.
    ramp = np.linspace(0, 255, size, dtype=np.uint8)      # shape (size,)
    ramp = np.tile(ramp, (size // 2, 1))                  # stack into (size/2, size)
    # Same gray in all 3 channels -> neutral gray ramp.
    img[: size // 2] = cv2.merge([ramp, ramp, ramp])

    # Bottom half: four solid color bars. OpenCV order is B, G, R.
    bars = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255)]  # R, G, B, Yellow
    bar_w = size // len(bars)
    for i, color in enumerate(bars):
        img[size // 2 :, i * bar_w : (i + 1) * bar_w] = color
    return img


def luma_average(img_f):
    """(R+G+B)/3. Perceptually wrong, shown for contrast."""
    return img_f.mean(axis=2)


def luma_rec601(img_f):
    """0.299R + 0.587G + 0.114B - the SD/TV weights."""
    b, g, r = img_f[..., 0], img_f[..., 1], img_f[..., 2]
    return 0.114 * b + 0.587 * g + 0.299 * r


def luma_rec709(img_f):
    """0.2126R + 0.7152G + 0.0722B - HD weights, matches AE. USE THIS ONE."""
    b, g, r = img_f[..., 0], img_f[..., 1], img_f[..., 2]
    return 0.0722 * b + 0.7152 * g + 0.2126 * r


def main():
    img = make_test_image()

    # Work in float [0,1]. In the plugin, 32-bit float pixels ARE already [0,1],
    # so prototyping this way keeps the math identical to what you'll port.
    img_f = img.astype(np.float32) / 255.0

    t_avg = luma_average(img_f)
    t_601 = luma_rec601(img_f)
    t_709 = luma_rec709(img_f)

    # t is a 2D (H, W) array of tone values in [0,1] - a grayscale image.
    print("input shape: ", img.shape)
    print("luma shape:  ", t_709.shape, " -> one tone value per pixel, no color axis")

    # Sample the color bars (bottom row) to see the disagreement numerically.
    y = int(img.shape[0] * 0.75)
    for name, x_frac in [("red", 0.12), ("green", 0.37), ("blue", 0.62), ("yellow", 0.87)]:
        x = int(img.shape[1] * x_frac)
        print(f"  {name:6s}: avg={t_avg[y,x]:.3f}  rec601={t_601[y,x]:.3f}  "
              f"rec709={t_709[y,x]:.3f}")
    print("Note: pure blue is DARK in rec709 (0.07) but mid-gray in average (0.33).")

    # Save the three tone maps as 8-bit grayscale to eyeball them.
    cv2.imwrite("out_gm1_input.png", img)
    cv2.imwrite("out_gm1_luma_average.png", (t_avg * 255).astype(np.uint8))
    cv2.imwrite("out_gm1_luma_rec601.png",  (t_601 * 255).astype(np.uint8))
    cv2.imwrite("out_gm1_luma_rec709.png",  (t_709 * 255).astype(np.uint8))

    print("\nWrote: out_gm1_input.png and out_gm1_luma_{average,rec601,rec709}.png")
    print("Compare the color bars: 'average' flattens them, rec709 spreads them "
          "the way your eye expects.")


if __name__ == "__main__":
    main()

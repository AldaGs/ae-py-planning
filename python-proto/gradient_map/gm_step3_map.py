"""
Gradient Map - Step 3: The mapping (luma -> LUT -> colored image).

Goal: connect the two halves we built.
    Step 1: image -> t in [0,1] per pixel   (rec709 luma)
    Step 2: stops -> LUT[n] of colors        (build once)
    Step 3 (here): out_pixel = LUT[ t ]      (per pixel, just an index)

This is the entire effect. Everything after this (Step 4/5) is controls and
edge-hardening; the core is these three lines of ideas.

Two things this step also demonstrates, because you asked:
  1. INTERPOLATION MODE lives in build_lut, NOT in the per-pixel loop. We show
     'linear' vs 'constant' (stepped/posterized). The mapping code below is
     byte-for-byte identical for both - proof that the render loop doesn't care
     how the ramp was built.
  2. VECTORIZED lookup. Instead of a Python for-loop over pixels, we quantize t
     to a table index and index the whole image at once with NumPy fancy
     indexing. This mirrors how the C++ inner loop will read table[idx] per
     pixel - same operation, just spelled array-wide here.

Run:  python gm_step3_map.py
Then open out_gm3_*.png and compare linear vs constant, and the presets.
"""

import numpy as np
import cv2


# ---- Reuse Step 1's luma (rec709) and Step 2's stops, kept local so this file
#      runs standalone without imports getting in the way of reading it. ----

def luma_rec709(img_f):
    b, g, r = img_f[..., 0], img_f[..., 1], img_f[..., 2]
    return 0.0722 * b + 0.7152 * g + 0.2126 * r


def make_stops(name):
    presets = {
        "duotone":  [(0.0, (0.05, 0.10, 0.20)), (1.0, (0.99, 0.90, 0.72))],
        "teal_orange": [(0.0, (0.03, 0.14, 0.20)),
                        (0.5, (0.20, 0.30, 0.35)),
                        (1.0, (1.00, 0.62, 0.25))],
        "spectrum": [(0.00, (0.00, 0.00, 0.00)),
                     (0.25, (0.10, 0.20, 0.90)),
                     (0.50, (0.10, 0.80, 0.40)),
                     (0.75, (0.95, 0.80, 0.10)),
                     (1.00, (1.00, 1.00, 1.00))],
    }
    return presets[name]


def build_lut(stops, n=256, mode="linear"):
    """Interpolate stops into an (n,3) RGB table.

    mode = "linear"   -> straight-line blend between neighboring stops.
    mode = "constant" -> hold: each level takes the color of the stop at or
                         below it (hard bands, posterized look).
    The per-pixel mapping later is IDENTICAL for either mode.
    """
    stops = sorted(stops, key=lambda s: s[0])
    positions = [p for p, _ in stops]
    colors = [np.array(c, dtype=np.float32) for _, c in stops]

    lut = np.zeros((n, 3), dtype=np.float32)
    for i in range(n):
        t = i / (n - 1)
        if t <= positions[0]:
            lut[i] = colors[0]; continue
        if t >= positions[-1]:
            lut[i] = colors[-1]; continue

        k = 0
        while k < len(positions) - 1 and positions[k + 1] <= t:
            k += 1

        if mode == "constant":
            lut[i] = colors[k]                      # hold the lower stop - no blend
        else:  # linear
            span = positions[k + 1] - positions[k]
            f = 0.0 if span == 0 else (t - positions[k]) / span
            lut[i] = (1.0 - f) * colors[k] + f * colors[k + 1]
    return lut


def apply_gradient_map(img_f, lut):
    """The whole effect: reduce to tone, then index the LUT with that tone.

    img_f : (H,W,3) float BGR in [0,1]
    lut   : (n,3)   float RGB
    returns (H,W,3) float BGR in [0,1]
    """
    n = lut.shape[0]
    t = luma_rec709(img_f)                          # (H,W) tone in [0,1]

    # Quantize tone -> integer table index in [0, n-1]. This IS what the C++
    # loop does: idx = (int)(t * (n-1) + 0.5). np.clip guards HDR/out-of-range t.
    idx = np.clip(t * (n - 1) + 0.5, 0, n - 1).astype(np.int32)   # (H,W)

    mapped_rgb = lut[idx]                            # fancy index -> (H,W,3) RGB
    mapped_bgr = mapped_rgb[..., ::-1]              # RGB -> BGR for OpenCV write
    return mapped_bgr


def make_photo_like(size=400):
    """A grayscale-ish test image with a full tonal range and real structure.

    Radial vignette (dark corners -> bright center) plus a few shapes, so the
    gradient map has shadows, mids, and highlights to reach into.
    """
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = size / 2
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    v = 1.0 - (d / d.max())                          # 1 center -> 0 corner
    v = np.clip(v * 1.2, 0, 1)
    img = cv2.merge([v, v, v]).astype(np.float32)    # neutral gray base
    # A couple of tonal blocks so bands are visible.
    cv2.circle(img, (size // 2, size // 2), size // 6, (0.9, 0.9, 0.9), -1)
    cv2.rectangle(img, (30, 30), (120, 120), (0.15, 0.15, 0.15), -1)
    return img


def main():
    img_f = make_photo_like()
    cv2.imwrite("out_gm3_input.png", (img_f * 255).astype(np.uint8))

    # Same preset, two interpolation modes -> shows mode lives in the LUT only.
    for mode in ("linear", "constant"):
        lut = build_lut(make_stops("teal_orange"), n=256, mode=mode)
        out = apply_gradient_map(img_f, lut)
        cv2.imwrite(f"out_gm3_teal_orange_{mode}.png",
                    (np.clip(out, 0, 1) * 255).astype(np.uint8))
        print(f"teal_orange / {mode:8s}: mapped {img_f.shape} through LUT {lut.shape}")

    # A couple more presets in linear so you can see the range.
    for name in ("duotone", "spectrum"):
        lut = build_lut(make_stops(name), n=256, mode="linear")
        out = apply_gradient_map(img_f, lut)
        cv2.imwrite(f"out_gm3_{name}.png", (np.clip(out, 0, 1) * 255).astype(np.uint8))
        print(f"{name:11s}/ linear  : done")

    print("\nWrote: out_gm3_input.png, out_gm3_teal_orange_{linear,constant}.png, "
          "out_gm3_{duotone,spectrum}.png")
    print("Compare linear vs constant: same stops, but 'constant' shows hard "
          "posterized bands because only the LUT build changed.")


if __name__ == "__main__":
    main()

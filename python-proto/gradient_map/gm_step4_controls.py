"""
Gradient Map - Step 4: Controls (the knobs that make it a real AE effect).

Goal: Step 3 mapped luma->LUT with a fixed pipeline. A shippable effect exposes
parameters. This step adds the controls an AE user expects, each mapped to the
PF param it will become in the C++ port:

  - BLEND AMOUNT (0..1)   -> PF_ADD_FLOAT_SLIDER. Mix mapped result back over the
                             original. 0 = untouched, 1 = full gradient map.
                             out = (1-a)*orig + a*mapped.  (This is Colorama's
                             "Blend With Original", and the standard opacity knob.)
  - PRESERVE ALPHA        -> PF_ADD_CHECKBOX. Gradient map recolors RGB only; the
                             layer's original alpha must pass through untouched.
                             Getting this right matters for premultiplied edges.
  - REVERSE RAMP          -> PF_ADD_CHECKBOX. Flip the LUT (t -> 1-t). One of
                             Colorama's two headline moves. Free: reverse the
                             table at build time, loop unchanged.
  - INTERPOLATION MODE    -> PF_ADD_POPUP. constant / linear / smooth(spline).
                             Lives ONLY in build_lut - render loop never changes.

The spline mode answers the earlier question concretely: we use a monotone-ish
Catmull-Rom through the stops, then CLAMP, since a cubic can overshoot [0,1].

Run:  python gm_step4_controls.py
Then open out_gm4_*.png and compare interp modes and blend amounts.
"""

import numpy as np
import cv2


def luma_rec709(img_f):
    b, g, r = img_f[..., 0], img_f[..., 1], img_f[..., 2]
    return 0.0722 * b + 0.7152 * g + 0.2126 * r


def make_stops(name):
    presets = {
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


def _catmull_rom(p0, p1, p2, p3, f):
    """Catmull-Rom cubic between p1 and p2 (p0,p3 are the neighbors), f in [0,1].

    Passes through p1 and p2 exactly; slope is continuous across stops (no crease
    like linear). Can overshoot, so callers CLAMP the result to [0,1].
    """
    f2 = f * f
    f3 = f2 * f
    return 0.5 * (
        (2 * p1)
        + (-p0 + p2) * f
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f2
        + (-p0 + 3 * p1 - 3 * p2 + p3) * f3
    )


def build_lut(stops, n=256, mode="linear", reverse=False):
    """Interpolate stops into an (n,3) RGB table.

    mode: "constant" (hold), "linear" (straight blend), "smooth" (Catmull-Rom).
    reverse: flip the ramp so highlights take the shadow color and vice versa.
    """
    stops = sorted(stops, key=lambda s: s[0])
    positions = [p for p, _ in stops]
    colors = [np.array(c, dtype=np.float32) for _, c in stops]
    m = len(stops)

    lut = np.zeros((n, 3), dtype=np.float32)
    for i in range(n):
        t = i / (n - 1)
        if t <= positions[0]:
            lut[i] = colors[0]; continue
        if t >= positions[-1]:
            lut[i] = colors[-1]; continue

        k = 0
        while k < m - 1 and positions[k + 1] <= t:
            k += 1
        span = positions[k + 1] - positions[k]
        f = 0.0 if span == 0 else (t - positions[k]) / span

        if mode == "constant":
            lut[i] = colors[k]
        elif mode == "smooth":
            # Neighbors for the cubic; clamp indices at the ends (Catmull-Rom
            # needs one stop on each side - reuse endpoints where missing).
            p0 = colors[max(k - 1, 0)]
            p1 = colors[k]
            p2 = colors[k + 1]
            p3 = colors[min(k + 2, m - 1)]
            lut[i] = np.clip(_catmull_rom(p0, p1, p2, p3, f), 0.0, 1.0)
        else:  # linear
            lut[i] = (1.0 - f) * colors[k] + f * colors[k + 1]

    if reverse:
        lut = lut[::-1].copy()          # flip table: t -> 1-t, loop stays the same
    return lut


def apply_gradient_map(img_f, lut, blend=1.0, preserve_alpha=True, alpha=None):
    """Map, then blend back over the original by `blend` amount.

    img_f : (H,W,3) float BGR in [0,1]
    blend : 0 = original, 1 = full gradient map (PF_ADD_FLOAT_SLIDER 0..1)
    preserve_alpha / alpha : if given, alpha rides through untouched (RGB-only).
    """
    n = lut.shape[0]
    t = luma_rec709(img_f)
    idx = np.clip(t * (n - 1) + 0.5, 0, n - 1).astype(np.int32)
    mapped = lut[idx][..., ::-1]                     # (H,W,3) BGR

    out = (1.0 - blend) * img_f + blend * mapped     # blend-with-original
    out = np.clip(out, 0.0, 1.0)
    # preserve_alpha is conceptual here (our test images are opaque); in the
    # plugin this is where you'd copy the source alpha into the output, NOT the
    # mapped luma. Kept explicit so the port remembers to do it.
    return out


def make_photo_like(size=400):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = size / 2
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    v = np.clip((1.0 - d / d.max()) * 1.2, 0, 1)
    img = cv2.merge([v, v, v]).astype(np.float32)
    cv2.circle(img, (size // 2, size // 2), size // 6, (0.9, 0.9, 0.9), -1)
    cv2.rectangle(img, (30, 30), (120, 120), (0.15, 0.15, 0.15), -1)
    return img


def lut_to_strip(lut, height=40):
    row = (np.clip(lut[:, ::-1], 0, 1) * 255).astype(np.uint8)
    return np.tile(row[np.newaxis, :, :], (height, 1, 1))


def main():
    img_f = make_photo_like()
    cv2.imwrite("out_gm4_input.png", (img_f * 255).astype(np.uint8))

    # 1) Interp modes side by side (same stops) - and dump the LUT strips too.
    for mode in ("constant", "linear", "smooth"):
        lut = build_lut(make_stops("spectrum"), mode=mode)
        out = apply_gradient_map(img_f, lut, blend=1.0)
        cv2.imwrite(f"out_gm4_interp_{mode}.png", (out * 255).astype(np.uint8))
        cv2.imwrite(f"out_gm4_ramp_{mode}.png", lut_to_strip(lut))
        print(f"interp {mode:8s}: image + ramp strip written")

    # 2) Blend-amount sweep on teal_orange.
    lut = build_lut(make_stops("teal_orange"), mode="smooth")
    for a in (0.0, 0.5, 1.0):
        out = apply_gradient_map(img_f, lut, blend=a)
        cv2.imwrite(f"out_gm4_blend_{a:.1f}.png", (out * 255).astype(np.uint8))
        print(f"blend {a:.1f}: written")

    # 3) Reverse toggle.
    lut_rev = build_lut(make_stops("teal_orange"), mode="smooth", reverse=True)
    out = apply_gradient_map(img_f, lut_rev, blend=1.0)
    cv2.imwrite("out_gm4_reverse.png", (out * 255).astype(np.uint8))
    print("reverse: written")

    print("\nWrote out_gm4_input, out_gm4_interp_{constant,linear,smooth}, "
          "matching out_gm4_ramp_* strips, out_gm4_blend_{0.0,0.5,1.0}, "
          "out_gm4_reverse.")
    print("Look at interp_smooth vs interp_linear: smooth removes the tone-band "
          "creases where segments meet.")


if __name__ == "__main__":
    main()

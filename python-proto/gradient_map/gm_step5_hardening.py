"""
Gradient Map - Step 5: Hardening (the edge cases that bite during the C++ port).

Goal: no new look. This step stress-tests the FOUR things that break between a
pretty Python prototype and a correct AE plugin that renders at 8/16/32-bit on
real footage. Each section maps to a decision you must bake into the port.

  A. BIT-DEPTH PARITY
     AE calls your effect with 8-bit (0..255), 16-bit (0..32768, note: AE's
     16-bit max is 0x8000 = 32768, NOT 65535), and 32-bit float pixels. The LUT
     math must give the SAME look at every depth. We prove parity by mapping the
     same image at all three and measuring the difference.

  B. HDR / OUT-OF-RANGE TONES (32-bit only)
     Float pixels can exceed 1.0 (super-whites) or go below 0.0. luma can land
     outside [0,1]. The index MUST clamp or you read out of bounds -> crash in
     C++. We test t<0 and t>1 and confirm they pin to the end colors.

  C. LUT RESOLUTION + INTERPOLATE-ON-LOOKUP
     Nearest-index lookup is fine at 8-bit (256 tones, 256 slots). At 16/32-bit
     there are far more tones than a 256 LUT has slots -> visible banding on
     smooth gradients. Fix: either grow the LUT, or LINEARLY INTERPOLATE between
     the two nearest LUT entries. We compare nearest vs interpolated banding.

  D. LINEAR-LIGHT LUMA (the caveat from Step 1)
     Strictly, luma should be measured on LINEAR-light RGB. AE's working space is
     usually gamma-encoded (sRGB-ish). Computing luma on gamma values is the fast
     approximation everyone ships; doing it in linear shifts midtones. We show
     the difference so you can DECIDE (and expose it as a checkbox if you want).

Run:  python gm_step5_hardening.py
Read the printed PASS/FAIL checks; open the banding + linear-vs-gamma PNGs.
"""

import numpy as np
import cv2


# ---------- shared core (frozen from step 4) ----------

def luma_rec709(img_f, linearize=False):
    """Rec.709 luma. If linearize, convert sRGB->linear first (proper light)."""
    x = img_f
    if linearize:
        x = srgb_to_linear(img_f)
    b, g, r = x[..., 0], x[..., 1], x[..., 2]
    return 0.0722 * b + 0.7152 * g + 0.2126 * r


def srgb_to_linear(c):
    """Standard sRGB EOTF. Vectorized, handles the small linear toe below 0.04045."""
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def build_lut(stops, n=256):
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
        lut[i] = (1.0 - f) * colors[k] + f * colors[k + 1]
    return lut


def map_nearest(t, lut):
    """Nearest-slot lookup: idx = round(t*(n-1)). Clamped for HDR safety."""
    n = lut.shape[0]
    idx = np.clip(t * (n - 1) + 0.5, 0, n - 1).astype(np.int32)
    return lut[idx]


def map_interpolated(t, lut):
    """Interpolate between the two nearest LUT slots - kills banding at hi depth.

    pos = t*(n-1); i0 = floor(pos); frac = pos-i0; out = lerp(lut[i0], lut[i1]).
    This is the per-pixel version the C++ float path should use.
    """
    n = lut.shape[0]
    pos = np.clip(t * (n - 1), 0, n - 1)
    i0 = np.floor(pos).astype(np.int32)
    i1 = np.minimum(i0 + 1, n - 1)
    frac = (pos - i0)[..., np.newaxis]               # (H,W,1) for broadcast
    return (1.0 - frac) * lut[i0] + frac * lut[i1]


STOPS = [(0.0, (0.03, 0.14, 0.20)), (0.5, (0.20, 0.30, 0.35)),
         (1.0, (1.00, 0.62, 0.25))]


# ---------- A. bit-depth parity ----------

def check_bit_depth_parity():
    """Render a ramp at 8/16/32-bit; all should match within quantization."""
    lut = build_lut(STOPS, n=256)
    W = 1024
    ramp01 = np.linspace(0, 1, W, dtype=np.float32)
    ramp01 = np.tile(ramp01, (64, 1))
    img32 = cv2.merge([ramp01, ramp01, ramp01])       # float [0,1]

    # Simulate AE's integer depths by round-tripping through their max values.
    img8 = np.round(img32 * 255.0) / 255.0            # 8-bit grid
    img16 = np.round(img32 * 32768.0) / 32768.0       # AE 16-bit grid (0x8000)

    # Measure parity on the INTERPOLATED path - nearest lookup jumps a whole LUT
    # slot at grid boundaries (~1/256), which is the banding Section C fixes, not
    # a real depth mismatch. Interpolated lookup isolates true bit-depth error.
    out32 = map_interpolated(luma_rec709(img32), lut)
    out8 = map_interpolated(luma_rec709(img8), lut)
    out16 = map_interpolated(luma_rec709(img16), lut)

    d8 = np.abs(out32 - out8).max()
    d16 = np.abs(out32 - out16).max()
    print("A. BIT-DEPTH PARITY (interpolated lookup)")
    print(f"   max |32bit - 8bit|  = {d8:.5f}  (~1/256 input grid, expected)")
    print(f"   max |32bit - 16bit| = {d16:.6f} (~1/32768 grid, tiny)")
    print(f"   -> {'PASS' if d16 < 1e-3 and d8 < 0.01 else 'FAIL'}: same look "
          "across depths; only input quantization differs.\n")


# ---------- B. HDR / out-of-range clamp ----------

def check_hdr_clamp():
    lut = build_lut(STOPS, n=256)
    # Craft tones deliberately outside [0,1].
    t = np.array([[-0.5, 0.0, 0.5, 1.0, 2.5]], dtype=np.float32)
    near = map_nearest(t, lut)
    interp = map_interpolated(t, lut)
    lo, hi = lut[0], lut[-1]
    ok = (np.allclose(near[0, 0], lo) and np.allclose(near[0, -1], hi)
          and np.allclose(interp[0, 0], lo) and np.allclose(interp[0, -1], hi))
    print("B. HDR / OUT-OF-RANGE CLAMP")
    print(f"   t=-0.5 -> {near[0,0].round(3)} (want shadow {lo.round(3)})")
    print(f"   t=+2.5 -> {near[0,-1].round(3)} (want highlight {hi.round(3)})")
    print(f"   -> {'PASS' if ok else 'FAIL'}: no out-of-bounds index; extremes "
          "pin to end colors.\n")


# ---------- C. LUT resolution: nearest vs interpolated banding ----------

def check_banding():
    """Tiny 8-slot LUT exaggerates banding so the fix is obvious."""
    lut_small = build_lut(STOPS, n=8)                 # deliberately coarse
    W = 1024
    ramp = np.linspace(0, 1, W, dtype=np.float32)
    t = np.tile(ramp, (80, 1))

    near = np.clip(map_nearest(t, lut_small)[..., ::-1], 0, 1)
    interp = np.clip(map_interpolated(t, lut_small)[..., ::-1], 0, 1)
    cv2.imwrite("out_gm5_banding_nearest.png", (near * 255).astype(np.uint8))
    cv2.imwrite("out_gm5_banding_interp.png", (interp * 255).astype(np.uint8))

    # Quantify: count distinct color steps across the row (more = smoother).
    steps_near = len(np.unique(np.round(near[0] * 255).astype(np.uint8), axis=0))
    steps_interp = len(np.unique(np.round(interp[0] * 255).astype(np.uint8), axis=0))
    print("C. LUT RESOLUTION (8-slot LUT, exaggerated)")
    print(f"   distinct steps  nearest={steps_near}  interpolated={steps_interp}")
    print(f"   -> interpolate-on-lookup turns {steps_near} hard bands into a "
          f"{steps_interp}-step smooth ramp. Use it on the float path.\n")


# ---------- D. linear-light vs gamma luma ----------

def check_linear_vs_gamma():
    lut = build_lut(STOPS, n=256)
    size = 400
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    v = np.clip((1 - np.sqrt((xx-size/2)**2+(yy-size/2)**2)/(size/2))*1.2, 0, 1)
    img = cv2.merge([v, v, v]).astype(np.float32)

    out_gamma = np.clip(map_nearest(luma_rec709(img, linearize=False), lut)[..., ::-1], 0, 1)
    out_linear = np.clip(map_nearest(luma_rec709(img, linearize=True), lut)[..., ::-1], 0, 1)
    cv2.imwrite("out_gm5_luma_gamma.png", (out_gamma * 255).astype(np.uint8))
    cv2.imwrite("out_gm5_luma_linear.png", (out_linear * 255).astype(np.uint8))

    # Sample a genuine MIDTONE (a flat 0.5 gray patch), not the clipped center.
    patch = np.full((1, 1, 3), 0.5, dtype=np.float32)
    mid = float(luma_rec709(patch, linearize=False)[0, 0])
    mid_lin = float(luma_rec709(patch, linearize=True)[0, 0])
    print("D. LINEAR-LIGHT vs GAMMA LUMA")
    print(f"   0.5 gray tone: gamma={mid:.3f}  linear={mid_lin:.3f} "
          "(linear reads mid-gray much darker)")
    print("   -> both are 'valid'; ship gamma by default (matches legacy AE), "
          "expose a 'Linearize' checkbox for correctness.\n")


def main():
    check_bit_depth_parity()
    check_hdr_clamp()
    check_banding()
    check_linear_vs_gamma()
    print("Wrote: out_gm5_banding_{nearest,interp}.png, "
          "out_gm5_luma_{gamma,linear}.png")
    print("Prototype complete. Port decisions locked: rec709 luma, 256+ LUT with "
          "interpolate-on-lookup for float, clamp index, gamma luma default.")


if __name__ == "__main__":
    main()

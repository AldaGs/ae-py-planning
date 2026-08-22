"""
Step 4 - Real controls + a vectorized (no-Python-loop) implementation.

Parameters exposed (this is the artist-facing API of the plugin):
  center   : (cx, cy) the clean-zone location        -> AE point control
  amount   : friendly 0..100 strength                -> AE slider (remapped internally)
  falloff  : 0 = linear growth, 1 = distance-squared  -> AE slider
  invert   : swap red-out/blue-in for the reverse look -> AE checkbox

Two implementations are given:
  chromatic_loop  - the readable per-pixel version (mirrors the C++ render loop)
  chromatic_fast  - the vectorized NumPy/cv2.remap version (whole image at once)
They should produce visually identical output; we time both.

Run:  python step4_controls.py
"""

import time
import numpy as np
import cv2


def make_test_image(size=400):
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 3, (0, 0, 0), 6)
    for gx in range(0, size, 40):
        cv2.line(img, (gx, 0), (gx, size), (0, 0, 0), 1)
        cv2.line(img, (0, gx), (size, gx), (0, 0, 0), 1)
    return img


def _amount_to_k(amount):
    """Remap a friendly 0..100 slider into the internal strength constant.
    100 -> k = 0.02, a strong-but-sane maximum. Linear remap; tune to taste."""
    return (amount / 100.0) * 0.02


def chromatic_fast(img, center=None, amount=30.0, falloff=0.0, invert=False):
    """Vectorized chromatic aberration. Operates on the whole array at once."""
    img = img.astype(np.float32)
    h, w = img.shape[:2]
    cx, cy = (w * 0.5, h * 0.5) if center is None else center
    k = _amount_to_k(amount)
    if invert:
        k = -k

    # Coordinate grids: every pixel's (x, y), then its vector from center.
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    dx = gx - cx
    dy = gy - cy
    dist = np.hypot(dx, dy)
    max_dist = np.hypot(max(cx, w - cx), max(cy, h - cy)) + 1e-6

    # shift = k * dist * (dist/max_dist)^falloff   (falloff=0 -> plain linear)
    curve = (dist / max_dist) ** falloff
    shift = k * dist * curve

    # Unit direction outward (safe at center: dist->0 handled by np.where).
    safe = np.where(dist < 1e-6, 1.0, dist)
    nx = dx / safe
    ny = dy / safe

    # For each channel, build the (H,W) map of WHERE to read from, then remap.
    def sample(channel, sgn):
        # cv2.remap requires float32 maps (CV_32FC1); force the dtype.
        map_x = (gx + sgn * nx * shift).astype(np.float32)
        map_y = (gy + sgn * ny * shift).astype(np.float32)
        return cv2.remap(channel, map_x, map_y,
                         interpolation=cv2.INTER_LINEAR,   # <- bilinear, Step 2
                         borderMode=cv2.BORDER_REPLICATE)  # <- clamp, Step 2/5

    b, g, r = cv2.split(img)
    r_out = sample(r, +1.0)   # red pulled outward
    g_out = g                 # green unchanged
    b_out = sample(b, -1.0)   # blue pulled inward
    out = cv2.merge([b_out, g_out, r_out])
    return np.clip(out, 0, 255).astype(np.uint8)


def chromatic_loop(img, center=None, amount=30.0, falloff=0.0, invert=False):
    """Readable per-pixel version - this is the shape of the C++ render loop."""
    from step2_bilinear import sample_bilinear
    img = img.astype(np.float32)
    h, w = img.shape[:2]
    cx, cy = (w * 0.5, h * 0.5) if center is None else center
    k = _amount_to_k(amount) * (-1.0 if invert else 1.0)
    max_dist = np.hypot(max(cx, w - cx), max(cy, h - cy)) + 1e-6
    b, g, r = cv2.split(img)
    out = np.zeros_like(img)
    for y in range(h):
        for x in range(w):
            dx, dy = x - cx, y - cy
            dist = np.hypot(dx, dy)
            if dist < 1e-6:
                out[y, x] = img[y, x]; continue
            nx, ny = dx / dist, dy / dist
            shift = k * dist * (dist / max_dist) ** falloff
            rv = sample_bilinear(r[..., None], x + nx * shift, y + ny * shift)
            bv = sample_bilinear(b[..., None], x - nx * shift, y - ny * shift)
            out[y, x] = [bv[0], g[y, x], rv[0]]
    return np.clip(out, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    src = make_test_image()

    # Show the falloff parameter's effect at equal amount.
    for fo in (0.0, 1.0):
        cv2.imwrite(f"out_step4_falloff_{fo}.png",
                    chromatic_fast(src, amount=40, falloff=fo))
        print(f"wrote out_step4_falloff_{fo}.png")

    # Off-center demo.
    cv2.imwrite("out_step4_offcenter.png",
                chromatic_fast(src, center=(100, 100), amount=40))
    print("wrote out_step4_offcenter.png (clean zone moved to upper-left)")

    # Timing: fast vs loop, same params.
    t0 = time.perf_counter(); chromatic_fast(src, amount=40); t1 = time.perf_counter()
    t2 = time.perf_counter(); chromatic_loop(src, amount=40); t3 = time.perf_counter()
    print(f"\nvectorized: {(t1-t0)*1000:8.1f} ms")
    print(f"per-pixel : {(t3-t2)*1000:8.1f} ms")
    print(f"speedup   : {(t3-t2)/(t1-t0):8.0f}x")

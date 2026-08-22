"""
Step 3 - Radial displacement: the heart of chromatic aberration.

For every OUTPUT pixel we compute a vector from the center, then sample the
red channel a bit further OUT and the blue channel a bit further IN along that
vector. Green stays put. The shift grows with distance from center, so the
middle stays clean and the corners fringe - exactly like a real lens.

Run:  python step3_radial.py
"""

import numpy as np
import cv2
from step2_bilinear import sample_bilinear  # reuse what we built and verified


def make_test_image(size=400):
    """White canvas with sharp shapes - edges are where fringing shows."""
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 3, (0, 0, 0), 6)  # ring outline
    for gx in range(0, size, 40):                                     # a grid
        cv2.line(img, (gx, 0), (gx, size), (0, 0, 0), 1)
        cv2.line(img, (0, gx), (size, gx), (0, 0, 0), 1)
    return img


def chromatic_aberration(img, strength):
    """Per-pixel radial channel displacement.

    strength `k`: fraction of the distance-from-center used as the shift.
                  e.g. 0.01 means a pixel 200px from center shifts its red
                  sample by ~2px outward.
    """
    img = img.astype(np.float64)
    h, w = img.shape[:2]
    cx, cy = w / 2.0, h / 2.0            # center of the image
    b, g, r = cv2.split(img)             # 2D channels; sample each separately
    out = np.zeros_like(img)

    for y in range(h):
        for x in range(w):
            dx = x - cx
            dy = y - cy
            dist = np.hypot(dx, dy)      # sqrt(dx^2 + dy^2)

            if dist < 1e-6:              # at the exact center: no fringing
                out[y, x] = img[y, x]
                continue

            nx, ny = dx / dist, dy / dist   # unit direction (outward)
            shift = strength * dist         # grows with distance

            # BACKWARD mapping: for this output pixel, where do we READ each channel?
            r_val = sample_bilinear(r[..., None], x + nx * shift, y + ny * shift)
            g_val = sample_bilinear(g[..., None], x,              y)
            b_val = sample_bilinear(b[..., None], x - nx * shift, y - ny * shift)

            out[y, x] = [b_val[0], g_val[0], r_val[0]]  # BGR order for OpenCV

    return np.clip(out, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    src = make_test_image()
    cv2.imwrite("out_step3_original.png", src)
    for k in (0.01, 0.015):
        res = chromatic_aberration(src, strength=k)
        cv2.imwrite(f"out_step3_aberration_{k}.png", res)
        print(f"wrote out_step3_aberration_{k}.png  (strength={k})")
    print("Look: center is clean, corners fringe. Higher strength = more fringe.")

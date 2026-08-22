"""
Step 5 - Hardening: edge handling, alpha layers, clamping, optional lens distortion.

New vs Step 4:
  edge_mode   : 'clamp' | 'reflect' | 'wrap' | 'transparent'  -> AE "Edge Behavior"
  distortion  : barrel(+)/pincushion(-) lens curve, layered under the aberration
  RGBA-aware  : if the image has an alpha channel, we shift it too and default to
                transparent edges - the correct behavior for logos/text on nothing.

Run:  python step5_hardening.py
"""

import numpy as np
import cv2


_BORDER = {
    'clamp':       cv2.BORDER_REPLICATE,
    'reflect':     cv2.BORDER_REFLECT_101,
    'wrap':        cv2.BORDER_WRAP,
    'transparent': cv2.BORDER_CONSTANT,   # constant value 0 -> transparent for alpha
}


def _amount_to_k(amount):
    return (amount / 100.0) * 0.02


def chromatic_aberration(img, center=None, amount=30.0, falloff=0.0,
                         invert=False, edge_mode='clamp', distortion=0.0):
    """Hardened chromatic aberration. Handles 3- or 4-channel (RGBA) input."""
    img = img.astype(np.float32)
    h, w = img.shape[:2]
    has_alpha = img.shape[2] == 4
    cx, cy = (w * 0.5, h * 0.5) if center is None else center
    k = _amount_to_k(amount) * (-1.0 if invert else 1.0)
    border = _BORDER[edge_mode]

    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    dx, dy = gx - cx, gy - cy
    dist = np.hypot(dx, dy)
    max_dist = np.hypot(max(cx, w - cx), max(cy, h - cy)) + 1e-6
    safe = np.where(dist < 1e-6, 1.0, dist)
    nx, ny = dx / safe, dy / safe

    # --- Optional lens distortion: bend the base sampling radius (barrel/pincushion).
    r_norm = dist / max_dist
    r_dist = r_norm * (1.0 + distortion * r_norm * r_norm)
    # Convert the distorted radius back into an extra displacement along the radial dir.
    base_shift = (r_dist - r_norm) * max_dist          # how many px the warp moves
    base_x = gx + nx * base_shift
    base_y = gy + ny * base_shift

    # --- Per-channel aberration offset, layered on top of the distortion warp.
    curve = (dist / max_dist) ** falloff
    shift = k * dist * curve

    def sample(channel, sgn):
        map_x = (base_x + sgn * nx * shift).astype(np.float32)
        map_y = (base_y + sgn * ny * shift).astype(np.float32)
        return cv2.remap(channel, map_x, map_y, cv2.INTER_LINEAR,
                         borderMode=border, borderValue=0)

    ch = cv2.split(img)
    b = sample(ch[0], -1.0)      # blue inward
    g = sample(ch[1],  0.0)      # green: still goes through the distortion warp, no aberration
    r = sample(ch[2], +1.0)      # red outward
    out = [b, g, r]
    if has_alpha:
        # Shift alpha with green (the reference) so the silhouette stays put,
        # while color fringes at the edge. Straight-alpha assumption.
        out.append(sample(ch[3], 0.0))
    out = cv2.merge(out)

    # Clamp: prevent 8-bit wraparound / out-of-range on export.
    return np.clip(out, 0, 255).astype(np.uint8)


def make_full_frame(size=400):
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 3, (0, 0, 0), 6)
    for gx in range(0, size, 40):
        cv2.line(img, (gx, 0), (gx, size), (0, 0, 0), 1)
        cv2.line(img, (0, gx), (size, gx), (0, 0, 0), 1)
    return img


def make_alpha_layer(size=400):
    """A white disc on TRANSPARENT background (RGBA) - like a logo on nothing."""
    img = np.zeros((size, size, 4), dtype=np.uint8)   # all transparent
    cv2.circle(img, (size // 2, size // 2), size // 3, (255, 255, 255, 255), -1)
    return img


if __name__ == "__main__":
    # Edge-mode comparison on full-frame footage (extreme amount to exaggerate borders).
    ff = make_full_frame()
    for mode in ('clamp', 'reflect', 'wrap'):
        cv2.imwrite(f"out_step5_edge_{mode}.png",
                    chromatic_aberration(ff, amount=90, edge_mode=mode))
        print(f"wrote out_step5_edge_{mode}.png")

    # Distortion demo.
    cv2.imwrite("out_step5_barrel.png",
                chromatic_aberration(ff, amount=40, distortion=0.4))
    print("wrote out_step5_barrel.png (barrel + aberration)")

    # Alpha layer: clamp (WRONG - smears) vs transparent (RIGHT - fringe dissolves).
    al = make_alpha_layer()
    for mode in ('clamp', 'transparent'):
        res = chromatic_aberration(al, amount=90, edge_mode=mode)
        # Composite over gray so you can SEE the transparency behavior.
        bg = np.full((400, 400, 3), 128, dtype=np.uint8)
        a = res[..., 3:4].astype(np.float32) / 255.0
        comp = (res[..., :3].astype(np.float32) * a + bg * (1 - a)).astype(np.uint8)
        cv2.imwrite(f"out_step5_alpha_{mode}.png", comp)
        print(f"wrote out_step5_alpha_{mode}.png")

"""
Step 2 - Sampling a pixel at a FRACTIONAL coordinate (bilinear interpolation).

This is the core technique behind every warp/distort/shift effect, including
chromatic aberration. Read step-by-step; the two _demo functions at the bottom
let you verify the math by hand.

Run:  python step2_bilinear.py
"""

import numpy as np
import cv2


def sample_bilinear(img, x, y):
    """Return the interpolated color of `img` at fractional position (x, y).

    img : (H, W, 3) float array   (we work in float so blends don't round badly)
    x, y: floats. x is the COLUMN, y is the ROW. (Note: NOT [row, col] here -
          we use (x, y) because that's how we'll think about displacement vectors.)

    Uses CLAMP edge handling: positions outside the image repeat the border pixel.
    """
    h, w = img.shape[:2]

    # 1) The four surrounding integer pixels.
    x0 = int(np.floor(x)); x1 = x0 + 1
    y0 = int(np.floor(y)); y1 = y0 + 1

    # 2) The fractional distances into the little square (each in 0..1).
    frac_x = x - x0
    frac_y = y - y0

    # 3) Clamp the integer coords so we never index out of bounds.
    #    This is the "repeat the edge" decision - revisited in Step 5.
    x0 = min(max(x0, 0), w - 1); x1 = min(max(x1, 0), w - 1)
    y0 = min(max(y0, 0), h - 1); y1 = min(max(y1, 0), h - 1)

    # 4) Grab the four corner colors.  Remember img is indexed [row, col] = [y, x].
    p00 = img[y0, x0]  # top-left
    p10 = img[y0, x1]  # top-right
    p01 = img[y1, x0]  # bottom-left
    p11 = img[y1, x1]  # bottom-right

    # 5) Interpolate along X on the top edge and the bottom edge...
    top    = p00 * (1 - frac_x) + p10 * frac_x
    bottom = p01 * (1 - frac_x) + p11 * frac_x
    # 6) ...then interpolate those two along Y.
    return top * (1 - frac_y) + bottom * frac_y


def demo_hand_check():
    """Prove the math: a 2x2 image with known corner values, sampled at the middle."""
    # A tiny image: one channel is enough to reason about. Corners 0,100,200,300.
    tiny = np.array([[[0], [100]],
                     [[200], [300]]], dtype=np.float64)  # shape (2,2,1)
    # Sample dead center (0.5, 0.5): should be the average of all four = 150.
    print("center of 2x2 (expect 150):", sample_bilinear(tiny, 0.5, 0.5).ravel())
    # Sample at (0.0, 0.0): exactly the top-left corner = 0.
    print("top-left corner (expect 0):", sample_bilinear(tiny, 0.0, 0.0).ravel())
    # Sample at (1.0, 0.0): exactly the top-right corner = 100.
    print("top-right corner (expect 100):", sample_bilinear(tiny, 1.0, 0.0).ravel())
    # Sample at (0.7, 0.0): along the top edge only -> 0*0.3 + 100*0.7 = 70.
    print("70% along top edge (expect 70):", sample_bilinear(tiny, 0.7, 0.0).ravel())


def demo_zoom():
    """Visual proof: upscale a small image 8x using our sampler vs nearest-neighbor.

    Bilinear should look smooth; nearest should look blocky. Same input, same size.
    """
    small = np.zeros((8, 8, 3), dtype=np.float64)
    cv2.circle(small, (4, 4), 3, (255, 255, 255), -1)  # a chunky white blob

    out_h, out_w = 256, 256
    bil = np.zeros((out_h, out_w, 3), dtype=np.float64)
    nn = np.zeros((out_h, out_w, 3), dtype=np.float64)
    for oy in range(out_h):
        for ox in range(out_w):
            # Map output pixel back to a fractional source position.
            sx = ox / out_w * small.shape[1]
            sy = oy / out_h * small.shape[0]
            bil[oy, ox] = sample_bilinear(small, sx, sy)
            nn[oy, ox] = small[min(int(sy), 7), min(int(sx), 7)]  # nearest: just floor

    cv2.imwrite("out_zoom_bilinear.png", bil.astype(np.uint8))
    cv2.imwrite("out_zoom_nearest.png", nn.astype(np.uint8))
    print("\nWrote out_zoom_bilinear.png (smooth) and out_zoom_nearest.png (blocky).")


if __name__ == "__main__":
    demo_hand_check()
    demo_zoom()

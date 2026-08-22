"""
Buildable Stroke - Step 4: A real distance transform (and corner styles).

Step 2's dilation was correct but wrong for shipping:
    - COST: O(width) full-image passes. A 60px stroke = 60 sweeps.
    - SHAPE: the distance wasn't Euclidean, so round things got faceted.

This step replaces the field with an EXACT distance transform that is:
    - O(N) total, INDEPENDENT of stroke width (a 3px and a 300px stroke cost
      the same), and
    - exactly the metric we choose - which, as we noted, IS the corner style.

Two algorithms live here:

A) Felzenszwalb-Huttenlocher (FH) EXACT EUCLIDEAN transform  -> ROUND / convex.
   The clever bit: the squared Euclidean distance transform is SEPARABLE. Do a
   1D transform down every column, then a 1D transform across every row of THAT
   result, and you get the exact 2D squared distance. The 1D transform itself
   is the "lower envelope of parabolas": each pixel plants an upward parabola
   f(i) + (x-i)^2, and the distance at x is the height of the lowest parabola
   over x. FH walk the parabolas in O(n). This is the CPU-ideal method the spec
   calls for, and the same math a JFA does approximately on the GPU.

B) Two-pass CHAMFER transform -> CITYBLOCK (L1, bevel) and CHESSBOARD (L-inf,
   miter). One forward sweep (look up/left) + one backward sweep (look
   down/right), taking the min of neighbour+cost. Exact for these metrics and
   dead simple - it's the classic way to get the square/diamond joins.

So "corner style" is literally which of these fills the distance field. Every
stroke from Step 3 keeps working - they only read 'dist'.

Run:  python bs_step4_distance_transform.py
"""

import time
import numpy as np
import cv2
from bs_step2_dilation import load_mask, distance_by_dilation
from bs_step3_multistroke import render_strokes, over

INF = 1e20


def _dt_1d(f):
    """1D squared-distance transform of sampled function f (Felzenszwalb-Huttenlocher).

    Returns d where d[x] = min_i ( f[i] + (x-i)^2 ). O(n) via lower envelope of
    parabolas. f is 0 at 'source' samples and +INF elsewhere.
    """
    n = f.shape[0]
    d = np.empty(n, np.float64)
    v = np.zeros(n, np.intp)      # x-coords of parabolas in the lower envelope
    z = np.empty(n + 1, np.float64)  # breakpoints between them
    k = 0
    v[0] = 0
    z[0] = -INF
    z[1] = INF
    for q in range(1, n):
        # intersection of parabola from q with the current top parabola v[k]
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        while s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        dx = q - v[k]
        d[q] = dx * dx + f[v[k]]
    return d


def edt_euclidean(mask):
    """Exact Euclidean distance from each pixel to the nearest True (opaque).

    f = 0 inside the shape, +INF outside. Separable: columns then rows on the
    squared distance, then sqrt. Result is 0 inside, grows outside.
    """
    f = np.where(mask, 0.0, INF).astype(np.float64)
    # transform along columns (axis 0)
    for x in range(f.shape[1]):
        f[:, x] = _dt_1d(f[:, x])
    # then along rows (axis 1)
    for y in range(f.shape[0]):
        f[y, :] = _dt_1d(f[y, :])
    return np.sqrt(f).astype(np.float32)


def chamfer(mask, diagonal):
    """Two-pass chamfer distance. diagonal=False -> L1 (bevel); True -> L-inf (miter).

    Cost to step orthogonally is 1; diagonally is 1 too when diagonal=True
    (that yields chessboard / L-inf), or disallowed when False (cityblock / L1).
    """
    d = np.where(mask, 0.0, INF).astype(np.float32)
    h, w = d.shape

    def relax(y, x, ny, nx, cost):
        if 0 <= ny < h and 0 <= nx < w:
            v = d[ny, nx] + cost
            if v < d[y, x]:
                d[y, x] = v

    # forward: top-left to bottom-right, look up/left (+diagonals)
    for y in range(h):
        for x in range(w):
            relax(y, x, y - 1, x, 1); relax(y, x, y, x - 1, 1)
            if diagonal:
                relax(y, x, y - 1, x - 1, 1); relax(y, x, y - 1, x + 1, 1)
    # backward: bottom-right to top-left, look down/right (+diagonals)
    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            relax(y, x, y + 1, x, 1); relax(y, x, y, x + 1, 1)
            if diagonal:
                relax(y, x, y + 1, x + 1, 1); relax(y, x, y + 1, x - 1, 1)
    return d


# The corner-style -> distance-field mapping, the whole point of the popup.
CORNER_STYLES = {
    "round": lambda m: edt_euclidean(m),        # L2  - convex round joins
    "miter": lambda m: chamfer(m, diagonal=True),   # Linf - square joins
    "bevel": lambda m: chamfer(m, diagonal=False),  # L1   - 45 flat-cut joins
}


def main():
    bgr, alpha, mask = load_mask("CTBS.png")
    h, w = mask.shape

    # --- speed & correctness vs Step 2's dilation --------------------------
    t = time.time(); dist_dil = distance_by_dilation(mask, 60, 8); t_dil = time.time() - t
    t = time.time(); dist_euc = edt_euclidean(mask);               t_euc = time.time() - t
    print(f"dilation (cap 60, square-ish): {t_dil:5.2f}s, max reach capped at 60")
    print(f"exact Euclidean (unbounded):   {t_euc:5.2f}s, max dist "
          f"{dist_euc.max():.1f}px - full range, width-independent cost")

    # The same 5-stroke stack from Step 3, now on the EXACT round field.
    strokes = [
        {"start": 0,  "width": 10, "color": (1.00, 1.00, 1.00), "opacity": 1.0},
        {"start": 10, "width": 8,  "color": (0.90, 0.10, 0.15), "opacity": 1.0},
        {"start": 18, "width": 8,  "color": (0.10, 0.30, 0.95), "opacity": 1.0},
        {"start": 30, "width": 6,  "color": (1.00, 0.80, 0.05), "opacity": 1.0},
        {"start": 40, "width": 18, "color": (0.10, 0.10, 0.12), "opacity": 0.35},
    ]
    src_rgb = (bgr[..., ::-1].astype(np.float32)) / 255.0

    def composite_and_save(dist, name):
        s_rgb, s_a = render_strokes(dist, strokes, (h, w))
        c_rgb, c_a = over(s_rgb, s_a, src_rgb, alpha)
        flat = c_rgb * c_a[..., None] + 1.0 * (1.0 - c_a[..., None])  # over white
        cv2.imwrite(name, (np.clip(flat[..., ::-1], 0, 1) * 255).astype(np.uint8))

    composite_and_save(dist_euc, "out_bs4_round.png")

    # --- corner styles: one FAT stroke so the joins are obvious ------------
    fat = [{"start": 4, "width": 26, "color": (0.95, 0.15, 0.15), "opacity": 1.0}]
    for style, fn in CORNER_STYLES.items():
        d = fn(mask)
        s_rgb, s_a = render_strokes(d, fat, (h, w))
        c_rgb, c_a = over(s_rgb, s_a, src_rgb, alpha)
        flat = c_rgb * c_a[..., None] + 1.0 * (1.0 - c_a[..., None])
        cv2.imwrite(f"out_bs4_corner_{style}.png",
                    (np.clip(flat[..., ::-1], 0, 1) * 255).astype(np.uint8))
    print("corner styles rendered: round (L2), miter (Linf), bevel (L1)")

    # A tight crop on the box's top-left corner to SEE the join difference.
    y0, y1, x0, x1 = 150, 320, 190, 380
    for style in CORNER_STYLES:
        img = cv2.imread(f"out_bs4_corner_{style}.png")
        cv2.imwrite(f"out_bs4_cornerzoom_{style}.png", img[y0:y1, x0:x1])

    print("\nWrote out_bs4_round.png, out_bs4_corner_{round,miter,bevel}.png,")
    print("and out_bs4_cornerzoom_*.png (crop of the box's top-left corner).")
    print("Compare the zooms: round bulges, miter squares off, bevel cuts 45.")


if __name__ == "__main__":
    main()

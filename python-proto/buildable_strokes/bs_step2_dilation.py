"""
Buildable Stroke - Step 2: Distance by dilation, and one stroke band.

Goal: turn the edge from Step 1 into a DISTANCE FIELD - one number per pixel,
"how far am I from the shape" - and use it to paint a single colored stroke.

The trick (morphological dilation as a stopwatch):
    Start from the mask. "Dilate" it = grow it outward by one pixel-ring, by
    saying: a pixel becomes inside if ANY neighbour was inside. (That's the
    exact mirror of Step 1's boundary test, which used ALL neighbours.)
    Do it again and again. A pixel that first turns on at iteration k is
    exactly k rings away from the shape -> its distance is k. So we don't just
    dilate, we timestamp: the iteration number IS the distance.

        cur = mask
        for k in 1..N:
            grown = dilate(cur)
            dist[grown & ~cur] = k     # newly reached this ring -> distance k
            cur = grown

Then a stroke is a band on that field:
        stroke where  start <= dist < start + width

Why this is the RIGHT thing to learn first, and also the WRONG final algorithm:
    - Right: it's ~5 lines, obviously correct, and the "band the distance field"
      idea is identical to what the fast method will feed. Get the LOOK here.
    - Wrong: cost is O(width) passes over the whole image - a 40px stroke = 40
      full-image dilations. And the DISTANCE IS NOT EUCLIDEAN. With 4-neighbour
      growth distance is |dx|+|dy| (a DIAMOND); with 8-neighbour it's
      max(|dx|,|dy|) (a SQUARE). Neither is a circle. Watch the round circle in
      CTBS.png get a subtly faceted stroke - that facet is the metric showing,
      and it's exactly what Step 4's real distance transform removes.

We show both metrics so you can SEE the anisotropy, then paint the stroke with
one of them.

Run:  python bs_step2_dilation.py
"""

import numpy as np
import cv2


def load_mask(path, thresh=0.5):
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra is None:
        raise FileNotFoundError(path)
    bgr = bgra[:, :, :3]
    alpha = bgra[:, :, 3].astype(np.float32) / 255.0
    return bgr, alpha, alpha >= thresh


def dilate_once(mask, connectivity=8):
    """Grow the True region by one ring. 'inside if ANY neighbour inside'.

    connectivity=4 -> up/down/left/right only     -> city-block (diamond) metric
    connectivity=8 -> also the 4 diagonals        -> chessboard (square) metric
    Border padded False = the shape does not wrap around the image edge.
    """
    p = np.pad(mask, 1, mode="constant", constant_values=False)
    grown = (
        p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]  # 4 orthogonal
    )
    if connectivity == 8:
        grown = grown | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:]
    return grown | mask  # keep what was already inside


def distance_by_dilation(mask, max_dist, connectivity=8):
    """Outside distance field via timestamped dilation.

    Returns float32 (H, W): 0 inside the shape, k on the k-th outer ring, and
    max_dist+1 (a sentinel "far") for anything not reached within max_dist.
    """
    dist = np.full(mask.shape, max_dist + 1, dtype=np.float32)
    dist[mask] = 0.0
    cur = mask.copy()
    for k in range(1, max_dist + 1):
        grown = dilate_once(cur, connectivity)
        newly = grown & ~cur          # the fresh ring at radius k
        dist[newly] = k
        cur = grown
    return dist


def paint_stroke(bgr, dist, start, width, color_bgr):
    """Composite a single opaque stroke band over the original image.

    band = start <= dist < start+width. We paint on a dimmed copy so the stroke
    reads clearly against the shape.
    """
    band = (dist >= start) & (dist < start + width)
    out = bgr.copy()
    out[band] = color_bgr
    return out, band


def main():
    bgr, alpha, mask = load_mask("CTBS.png")
    MAXD = 60  # enough rings for a fat stroke; also the per-iteration cost knob

    # Two metrics, same code, different connectivity - to expose anisotropy.
    dist8 = distance_by_dilation(mask, MAXD, connectivity=8)  # square
    dist4 = distance_by_dilation(mask, MAXD, connectivity=4)  # diamond

    print(f"max distance we computed: {MAXD} rings "
          f"= {MAXD} full-image dilations (that's the speed problem)")
    print(f"pixels within 20px (8-conn): {(dist8<=20).mean()*100:5.2f}%")
    print(f"pixels within 20px (4-conn): {(dist4<=20).mean()*100:5.2f}%"
          "   <- fewer: diamond reaches less area per ring")

    # Normalize distance to 0..255 for viewing (clip the 'far' sentinel out).
    def viz(d):
        v = np.clip(d, 0, MAXD) / MAXD
        return (v * 255).astype(np.uint8)
    cv2.imwrite("out_bs2_distance8.png", viz(dist8))
    cv2.imwrite("out_bs2_distance4.png", viz(dist4))

    # One stroke: a red band from 8px to 24px out (16px wide), on the square metric.
    stroke8, _ = paint_stroke(bgr, dist8, start=8, width=16, color_bgr=(0, 0, 255))
    cv2.imwrite("out_bs2_stroke8.png", stroke8)
    # Same stroke on the diamond metric - compare the circle: it goes faceted.
    stroke4, _ = paint_stroke(bgr, dist4, start=8, width=16, color_bgr=(0, 0, 255))
    cv2.imwrite("out_bs2_stroke4.png", stroke4)

    print("\nWrote out_bs2_distance{8,4}.png and out_bs2_stroke{8,4}.png")
    print("distance*.png: brighter = farther from the shape (a distance field).")
    print("stroke8 vs stroke4: same 16px stroke; on stroke4 the round circle "
          "picks up flat facets - that's the diamond metric. Step 4 fixes it.")


if __name__ == "__main__":
    main()

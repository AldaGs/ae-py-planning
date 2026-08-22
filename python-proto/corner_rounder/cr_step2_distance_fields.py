"""
Corner Rounder - Step 2: the same open/close, built from DISTANCE FIELDS.

Step 1 rounded corners with a disk kernel (cv2.erode/dilate). Correct, but the
kernel is O(r^2) per pixel - double the radius, quadruple the work - so a big
rounding radius in AE would crawl. This step gets the identical result for a
cost that DOES NOT DEPEND ON r, using the exact machinery Buildable Stroke
already owns: the Euclidean distance transform (FH-EDT on the CPU, JFA on the
GPU). Here scipy's distance_transform_edt stands in for those.

The equivalence (this is the whole step)
-----------------------------------------
Let S be the binary shape. Define two distance transforms:
    d_in (p)  = distance from p to the nearest OUTSIDE pixel   (0 outside)
              = edt(S)
    d_out(p)  = distance from p to the nearest INSIDE  pixel   (0 inside)
              = edt(~S)
Then the morphology primitives are just THRESHOLDS on these fields:
    erode by r  = { d_in  >  r }   keep inside pixels with clearance > r
    dilate by r = { d_out <= r }   add outside pixels within r of the shape
(both are radius-independent to compute: one edt, then a compare.)

Compose them exactly as in Step 1:
    OPENING = dilate(erode(S, r), r)     rounds CONVEX corners
    CLOSING = erode (dilate(S, r), r)    rounds CONCAVE corners
    ROUND   = closing(opening(S))        rounds everything, edges preserved

Each of open/close is TWO distance transforms; round-all is FOUR. All four are
O(N) and independent of r - that is the payoff. On the GPU these are four JFA
passes reusing the ping-pong seed/flood you already wrote.

Why it's actually MORE correct than the kernel: the EDT is exact Euclidean,
whereas a rasterized disk kernel is a staircased approximation of a circle. So
the distance-field arcs are rounder than Step 1's, not just faster.

The erode threshold uses '>' and dilate '<=' so that a straight edge round-trips
back to itself (erode-then-dilate returns the same edge to within the EDT's
half-pixel quantization). We MEASURE the agreement with Step 1 and the timing
vs radius below.

Run:  python cr_step2_distance_fields.py
"""

import time
import numpy as np
import cv2
from scipy.ndimage import distance_transform_edt

from cr_step1_morphology import synthetic_mask, disk_kernel


# --- distance-field morphology -------------------------------------------
def edt(mask_bool):
    """Euclidean distance from every True pixel to the nearest False pixel.
    (scipy computes distance-to-zero, so pass a boolean mask.) Stand-in for
    the FH-EDT / JFA transforms in the real plugin."""
    return distance_transform_edt(mask_bool)


def erode_df(S, r):
    """Keep inside pixels whose clearance to the background exceeds r."""
    return edt(S) > r


def dilate_df(S, r):
    """Add every pixel within r of the shape (inside pixels have d_out=0)."""
    return edt(~S) <= r


def opening_df(S, r):
    return dilate_df(erode_df(S, r), r)


def closing_df(S, r):
    return erode_df(dilate_df(S, r), r)


def round_df(S, r):
    """Open then close -> convex + concave rounded, straight edges preserved."""
    return closing_df(opening_df(S, r), r)


# --- Step-1 kernel versions (ground truth to compare against) ------------
def round_kernel(S_u8, r):
    k = disk_kernel(r)
    opn = cv2.dilate(cv2.erode(S_u8, k), k)
    return cv2.erode(cv2.dilate(opn, k), k)


def disagreement(a_bool, b_bool):
    """Percentage of pixels where two masks differ."""
    return (a_bool != b_bool).mean() * 100.0


def save_zoom(name, mask_bool, cx, cy, half=120, scale=6):
    m = mask_bool.astype(np.uint8)
    h, w = m.shape
    crop = m[max(0, cy-half):min(h, cy+half), max(0, cx-half):min(w, cx+half)] * 255
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(name, big)


def main():
    S_u8 = synthetic_mask()
    S = S_u8.astype(bool)
    h, w = S.shape
    print(f"image: {w}x{h}   opaque: {S.mean()*100:5.2f}%")

    r = 10
    rnd_df = round_df(S, r)
    rnd_k = round_kernel(S_u8, r).astype(bool)
    print(f"\nr={r}")
    print(f"round (distance field) opaque: {rnd_df.mean()*100:5.2f}%")
    print(f"round (kernel, step 1) opaque: {rnd_k.mean()*100:5.2f}%")
    print(f"disagreement DF vs kernel:     {disagreement(rnd_df, rnd_k):5.2f}%"
          f"  (a thin edge ring; EDT arcs are the more-exact circles)")

    # Save the field-based results + zooms on the star.
    cv2.imwrite("out_cr2_opening.png", opening_df(S, r).astype(np.uint8)*255)
    cv2.imwrite("out_cr2_closing.png", closing_df(S, r).astype(np.uint8)*255)
    cv2.imwrite("out_cr2_round.png",   rnd_df.astype(np.uint8)*255)
    save_zoom("out_cr2_zoom_round.png", rnd_df, 620, 200)
    save_zoom("out_cr2_zoom_round_kernel.png", rnd_k, 620, 200)

    # --- the payoff: cost is independent of r -----------------------------
    print("\ncost vs radius (round-all): kernel is O(r^2), distance field is flat")
    print(f"{'r':>4} | {'kernel ms':>10} | {'field ms':>9}")
    for rr in (5, 10, 20, 40, 80):
        t0 = time.perf_counter(); round_kernel(S_u8, rr); tk = (time.perf_counter()-t0)*1e3
        t0 = time.perf_counter(); round_df(S, rr);        td = (time.perf_counter()-t0)*1e3
        print(f"{rr:>4} | {tk:>10.1f} | {td:>9.1f}")

    print("\nWrote out_cr2_*.png. zoom_round vs zoom_round_kernel: the distance-"
          "field arc is a cleaner circle. In AE these four transforms are four "
          "JFA passes (GPU) / FH-EDT passes (CPU) - the Buildable Stroke engine.")


if __name__ == "__main__":
    main()

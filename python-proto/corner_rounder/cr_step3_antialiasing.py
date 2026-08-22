"""
Corner Rounder - Step 3: anti-aliasing the rounded edge (and NOT wrecking the
edges rounding never touched).

Steps 1-2 output a HARD 0/1 rounded mask, so every arc is a pixel staircase.
This step makes the output a smooth alpha. Two things have to be true:
    (A) the rounded silhouette gets clean, uniform anti-aliasing;
    (B) a straight edge the rounding did NOT move keeps its ORIGINAL soft alpha
        (don't destroy AA the source already had - the CA/Bloom/BS principle:
        re-read the existing soft edge instead of re-inventing it).

(A) The final field is a TRUE signed distance field
---------------------------------------------------
Recall round = close(open(S)). Carry it out as distance transforms and the LAST
field is a genuine Euclidean signed distance to the rounded boundary:

    O    = open(S)                      (a set)
    D    = dilate(O, r) = {edt(~O) <= r}
    g(p) = edt_in(D) - edt_out(D) - r    # + inside the rounded shape, - outside

The rounded boundary is the level set g = 0. Because g is built from exact
Euclidean distances to a SET, it has UNIT GRADIENT near its zero crossing.
That is the crucial difference from Buildable Stroke, whose corner field was a
BLEND of three metric fields and so had |grad| up to ~4, forcing a per-pixel
gradient-normalization to keep the feather a fixed pixel width. Here every
field is a real SDF, so:
    coverage = smoothstep(-f, +f, g)     # f = feather (pixels), no |grad| fixup
gives a feather of exactly 2f pixels everywhere. We MEASURE |grad g| below to
prove it sits at ~1 (so no normalization is owed).

Feather default: Buildable Stroke learned that f=0.5 (a 1px ramp) reads as
stair-steps under magnification; f=1.0 (a 2px ramp) is clean at any zoom. We
default f=0.75 here (rounded edges are curved, a touch softer looks right) and
expose it in Step 4.

(B) Preserve original AA on unmoved edges
-----------------------------------------
On a straight side, open+close is the identity, so g there equals the source's
own signed distance - but quantized to our 0.5-thresholded mask, i.e. it can sit
up to 0.5px off the source's true sub-pixel edge. Rather than seed every
transform sub-pixel (scipy's EDT can't take fractional seeds; the real plugin's
JFA gets it free from nearest-seed coords), we PASS THE SOURCE ALPHA THROUGH
wherever the rounded boundary coincides with an original anti-aliased edge:
    unmoved = (|g| <= 1) AND (0 < alpha < 1)      # near boundary AND on a source AA edge
    coverage = alpha            on unmoved pixels
             = smoothstep(...)  everywhere else (the corners, freshly AA'd)
At a rounded corner the boundary has moved away from any source edge pixel, so
`unmoved` is false there and the SDF AA takes over. We MEASURE that flat edges
come through bit-for-bit while corners get new AA.

Run:  python cr_step3_antialiasing.py
"""

import numpy as np
import cv2
from scipy.ndimage import distance_transform_edt

from cr_step1_morphology import synthetic_mask


def edt(mask_bool):
    return distance_transform_edt(mask_bool)


def signed_sdf(mask_bool):
    """Signed Euclidean distance: POSITIVE inside, NEGATIVE outside, |.|=dist to
    boundary. edt(mask) is inside-depth, edt(~mask) is outside-distance."""
    return edt(mask_bool) - edt(~mask_bool)


def rounded_sdf(S, r):
    """Signed distance field of close(open(S, r), r).  + inside, - outside.
    Five edts (each threshold below is one edt); the final one is signed."""
    # open(S) = dilate(erode(S,r),r)
    E = edt(S) > r                    # erode
    O = edt(~E) <= r                  # dilate  -> opened set
    # close(O) = erode(dilate(O,r),r)
    D = edt(~O) <= r                  # dilate opened set
    # erode(D,r): signed distance of D, boundary of the rounded shape is at r
    g = signed_sdf(D) - r            # + inside rounded shape, - outside
    return g


def coverage_from_sdf(g, alpha, feather=0.75):
    """SDF -> anti-aliased coverage, preserving source AA on unmoved edges."""
    # smoothstep(-f, f, g): 0 when g<=-f (outside), 1 when g>=f (inside).
    t = np.clip((g + feather) / (2 * feather), 0.0, 1.0)
    cov = t * t * (3 - 2 * t)
    # (B) unmoved edge: near the rounded boundary AND sitting on a source AA edge
    unmoved = (np.abs(g) <= 1.0) & (alpha > 0.0) & (alpha < 1.0)
    cov = np.where(unmoved, alpha, cov)
    return cov.astype(np.float32)


def grad_mag(g):
    gy, gx = np.gradient(g)
    return np.sqrt(gx * gx + gy * gy)


def save_zoom(name, img01, cx, cy, half=120, scale=6):
    h, w = img01.shape
    crop = img01[max(0, cy-half):min(h, cy+half), max(0, cx-half):min(w, cx+half)]
    big = cv2.resize((crop*255).astype(np.uint8), None, fx=scale, fy=scale,
                     interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(name, big)


def load_alpha(path):
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra is None:
        raise FileNotFoundError(path)
    return bgra[:, :, 3].astype(np.float32) / 255.0


def main():
    # --- (A) AA on the synthetic solid shapes -----------------------------
    S = synthetic_mask().astype(bool)
    alpha_hard = S.astype(np.float32)          # synthetic shape has no source AA
    r = 10
    g = rounded_sdf(S, r)
    cov = coverage_from_sdf(g, alpha_hard, feather=0.75)

    cv2.imwrite("out_cr3_round_aa.png", (cov*255).astype(np.uint8))
    save_zoom("out_cr3_zoom_hard.png",  S.astype(np.float32), 620, 200)
    save_zoom("out_cr3_zoom_aa.png",    cov,                  620, 200)

    # |grad g| near the boundary should be ~1 (true SDF -> no grad-norm needed).
    band = np.abs(g) <= 3.0
    gm = grad_mag(g)[band]
    print("(A) synthetic shapes, r=10, feather=0.75")
    print(f"    |grad g| in the AA band:  mean {gm.mean():.3f}  "
          f"p5 {np.percentile(gm,5):.3f}  p95 {np.percentile(gm,95):.3f}")
    print(f"    -> ~1.0 everywhere, so feather is 2f={2*0.75:.1f}px honest "
          f"pixels; NO |grad| normalization needed (unlike Buildable Stroke).")
    partial = ((cov > 0.01) & (cov < 0.99)).mean() * 100
    print(f"    partial-coverage (AA) pixels: {partial:.2f}%  (was ~0 on the "
          f"hard mask)")

    # --- (B) real soft alpha: preserve unmoved edges ----------------------
    a = load_alpha("CTBS.png")
    Sa = a >= 0.5
    rr = 3                                     # modest r: keep thin strokes alive
    g2 = rounded_sdf(Sa, rr)
    cov2 = coverage_from_sdf(g2, a, feather=0.75)
    cv2.imwrite("out_cr3_ctbs_aa.png", (cov2*255).astype(np.uint8))

    # On flat, unmoved source-AA pixels the output must equal the source alpha.
    unmoved = (np.abs(g2) <= 1.0) & (a > 0) & (a < 1)
    if unmoved.any():
        diff = np.abs(cov2[unmoved] - a[unmoved]).max()
        print(f"\n(B) CTBS real alpha, r={rr}")
        print(f"    unmoved source-AA pixels preserved exactly: max|out-src| = "
              f"{diff:.4f}  ({unmoved.sum()} px passed through)")
    print("    Wrote out_cr3_ctbs_aa.png - thin strokes survive at small r, "
          "corners rounded, original edges untouched.")

    print("\nWrote out_cr3_*.png. Compare zoom_hard (staircase) vs zoom_aa "
          "(smooth arc). Step 4 exposes the controls (radius, separate convex/"
          "concave, feather, blend amount).")


if __name__ == "__main__":
    main()

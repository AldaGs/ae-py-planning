"""
Corner Rounder - Step 4: the controls (the effect's real parameters).

Everything before this proved the engine: round = close(open(S)) via distance
fields, with clean unit-gradient AA. Step 4 wraps it in the knobs a user turns:

    Convex Radius   (px)   - how much the OUTER/convex corners round (open pass)
    Concave Radius  (px)   - how much the INNER/concave corners round (close pass)
                             Linked to one "Radius" by default; separable.
    Corner Profile  (0..1) - CIRCULAR (0) -> SQUIRCLE/superellipse (1). See below.
    Edge Softness   (px)   - the AA feather (Step 3 lesson: too small stair-steps)
    Amount          (0..1) - blend original alpha -> rounded result (dial it in)
    Preserve Src AA (bool) - pass source AA through on unmoved edges (Step 3 (B))

Corner Profile - answering "why did the corners look too geometric?"
-------------------------------------------------------------------
A CIRCULAR fillet is only tangent-continuous (G1): curvature jumps from 0 on the
straight edge to 1/r the instant the arc starts, which the eye reads as
"mechanical". A SUPERELLIPSE ("squircle") corner eases curvature in gradually,
the organic look (Apple's rounded rects). We get it by changing the SHAPE of the
structuring element we roll along the shape - the exact "corner style = the
distance metric" idea from Buildable Stroke:

    element = { (|x|/r)^p + (|y|/r)^p <= 1 }
    p = 2   -> a disk        -> circular corners (what Step 3 did)
    p = 4-6 -> a superellipse -> squircle corners (curvature eases in)
    p -> inf-> a square       -> no rounding / mitred

Profile 0..1 maps to p in [2, 6]. Morphology with a non-disk element is O(r^2)
here (a prototype); the port keeps it radius-independent by BLENDING metric
distance fields (L2 round <-> L-inf square), same as Buildable Stroke's corner
slider - a superellipse sits between those two.

AA still comes from ONE final L2 signed distance transform of the rounded SET,
so |grad|=1 and the feather stays honest pixels regardless of the profile.

Note (Step 1's caveat, now a real control interaction): Convex Radius erases
features thinner than 2*rv; Concave Radius fills gaps thinner than 2*rc. That is
inherent to morphological rounding - the Amount knob lets the user back off.

Run:  python cr_step4_controls.py
"""

import numpy as np
import cv2
from scipy.ndimage import distance_transform_edt

from cr_step1_morphology import synthetic_mask
from cr_step3_antialiasing import signed_sdf, coverage_from_sdf, edt


# ---------------------------------------------------------------------------
# CIRCULAR path: Step 3's all-distance-field pipeline. AA comes from thresholding
# a REAL distance field at a nonzero radius (fractional level set), never from
# re-distancing a hard mask - that was the 0%-AA trap. Generalized to separate
# convex (rv) and concave (rc) radii, with rv=0 / rc=0 handled so the AA field
# always ends on a threshold-at-r (stays anti-aliasable).
# ---------------------------------------------------------------------------
def rounded_sdf_rvrc(S, rv, rc):
    """Signed distance field (+ inside) of close(open(S, rv), rc), circular."""
    if rv >= 1:
        E = edt(S) > rv                    # erode by rv
        O = edt(~E) <= rv                  # dilate by rv -> opened set
    else:
        O = S
    if rc >= 1:
        D = edt(~O) <= rc                  # dilate by rc
        return signed_sdf(D) - rc          # erode by rc, signed; 0-level fractional
    if rv >= 1:
        return rv - edt(~E)                # convex-only: AA field from the dilate
    return signed_sdf(S)                   # radius 0: passthrough (no AA needed)


# ---------------------------------------------------------------------------
# PROFILE path: superellipse structuring element, rendered by SUPERSAMPLING (a
# prototype device to visualize any profile's true coverage). The SHIPPING port
# will produce the profile via metric-field BLENDING (L2 round <-> the square
# metric), the Buildable Stroke "corner style = distance metric" idea, so it
# stays radius-independent and needs no supersampling.
# ---------------------------------------------------------------------------
def superellipse_kernel(r, p):
    """{ (|x|/r)^p + (|y|/r)^p <= 1 }. p=2 disk, larger p -> squircle -> square."""
    r = max(1, int(round(r)))
    yy, xx = np.mgrid[-r:r+1, -r:r+1].astype(np.float64)
    return (((np.abs(xx)/r)**p + (np.abs(yy)/r)**p) <= 1.0).astype(np.uint8)


def profile_to_p(profile):
    """Profile 0..1 -> superellipse exponent p in [2, 6] (circular .. squircle)."""
    return 2.0 + 4.0 * float(np.clip(profile, 0.0, 1.0))


def round_coverage_supersampled(S_u8, rv, rc, profile, ss=4):
    """close(open(S)) with a superellipse profile, coverage via ss-supersampling."""
    p = profile_to_p(profile)
    big = cv2.resize(S_u8, None, fx=ss, fy=ss, interpolation=cv2.INTER_NEAREST)
    if rv >= 1:
        kv = superellipse_kernel(rv*ss, p)
        big = cv2.dilate(cv2.erode(big, kv), kv)
    if rc >= 1:
        kc = superellipse_kernel(rc*ss, p)
        big = cv2.erode(cv2.dilate(big, kc), kc)
    # area-downsample the hard hi-res set -> fractional coverage at full res
    cov = cv2.resize(big.astype(np.float32), (S_u8.shape[1], S_u8.shape[0]),
                     interpolation=cv2.INTER_AREA)
    return cov


def corner_rounder(alpha, radius=10, concave_radius=None, profile=0.0,
                   feather=0.75, amount=1.0, preserve_src_aa=True, thresh=0.5):
    """Full effect: soft alpha in -> soft alpha out.

    radius          convex radius (px). concave_radius defaults to = radius (linked).
    profile         0 circular (distance-field AA) .. 1 squircle (supersampled).
    amount          0 (original) .. 1 (fully rounded).
    """
    rv = radius
    rc = radius if concave_radius is None else concave_radius
    S = (alpha >= thresh).astype(np.uint8)
    Sb = S.astype(bool)

    if profile <= 1e-6:                              # circular: the shipping AA path
        g = rounded_sdf_rvrc(Sb, rv, rc)
        cov = coverage_from_sdf(g, alpha, feather) if preserve_src_aa \
            else _smoothstep_cov(g, feather)
    else:                                            # profile: supersampled proto
        cov = round_coverage_supersampled(S, rv, rc, profile)
        if preserve_src_aa:
            g = rounded_sdf_rvrc(Sb, rv, rc)         # locate unmoved edges via circular sdf
            unmoved = (np.abs(g) <= 1.0) & (alpha > 0) & (alpha < 1)
            cov = np.where(unmoved, alpha, cov)

    amount = float(np.clip(amount, 0.0, 1.0))
    return ((1.0 - amount) * alpha + amount * cov.astype(np.float32)).astype(np.float32)


def _smoothstep_cov(g, feather):
    t = np.clip((g + feather) / (2 * feather), 0.0, 1.0)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def save(name, img01):
    cv2.imwrite(name, (np.clip(img01, 0, 1) * 255).astype(np.uint8))


def save_zoom(name, img01, cx, cy, half=120, scale=6):
    h, w = img01.shape
    c = img01[max(0, cy-half):min(h, cy+half), max(0, cx-half):min(w, cx+half)]
    cv2.imwrite(name, cv2.resize((np.clip(c, 0, 1)*255).astype(np.uint8), None,
                                 fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST))


def main():
    S = synthetic_mask()
    alpha = S.astype(np.float32)      # synthetic: hard source, no AA to preserve

    # --- Corner Profile: circular vs squircle, side by side ---------------
    circ = corner_rounder(alpha, radius=14, profile=0.0)   # circular
    squi = corner_rounder(alpha, radius=14, profile=1.0)   # squircle
    save("out_cr4_profile_circular.png", circ)
    save("out_cr4_profile_squircle.png", squi)
    # zoom a convex corner of the SQUARE (cleanest place to see the profile)
    save_zoom("out_cr4_zoom_circular.png", circ, 40, 120, half=40, scale=14)
    save_zoom("out_cr4_zoom_squircle.png", squi, 40, 120, half=40, scale=14)

    # --- Radius sweep -----------------------------------------------------
    for r in (6, 14, 28):
        save(f"out_cr4_radius_{r}.png", corner_rounder(alpha, radius=r))

    # --- Separate convex/concave radii ------------------------------------
    save("out_cr4_convex_only.png",
         corner_rounder(alpha, radius=16, concave_radius=0))   # sharp notches
    save("out_cr4_concave_only.png",
         corner_rounder(alpha, radius=0, concave_radius=16))   # sharp points

    # --- Amount blend -----------------------------------------------------
    for amt in (0.0, 0.5, 1.0):
        save(f"out_cr4_amount_{amt:.1f}.png",
             corner_rounder(alpha, radius=14, amount=amt))

    # quick numeric sanity
    p_aa = ((circ > 0.01) & (circ < 0.99)).mean()*100
    print("Step 4 controls")
    print(f"  profile p range: {profile_to_p(0):.1f} (circular) .. "
          f"{profile_to_p(1):.1f} (squircle)")
    print(f"  circular r=14 AA pixels: {p_aa:.2f}%")
    print(f"  amount 0.0 == source?    "
          f"max|out-src|={np.abs(corner_rounder(alpha,14,amount=0.0)-alpha).max():.4f}")
    print("Wrote out_cr4_*.png. Compare zoom_circular vs zoom_squircle: the "
          "squircle corner is fuller and its curvature eases in (less "
          "'geometric'). radius_/amount_/convex_only/concave_only show the knobs.")


if __name__ == "__main__":
    main()

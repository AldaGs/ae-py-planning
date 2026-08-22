"""
Corner Rounder - Step 5: hardening + the pixels we never tested (COLOR).

Steps 1-4 operated on the ALPHA silhouette with white masks, so a real question
went untested: rounding CHANGES coverage in both directions - convex corners
LOSE pixels, concave corners and squircle bulges GAIN pixels. Removed pixels are
easy (alpha -> 0). But an ADDED pixel had alpha 0 in the source; its straight
RGB is undefined/garbage (often black), so filling it naively fringes the shape
black. This step gives added pixels a plausible colour and then stress-tests the
32-bit / edge-case behaviour the AE port must survive.

Colour policy for added pixels: NEAREST OPAQUE SOURCE COLOUR (edge extend)
-------------------------------------------------------------------------
We already compute distance transforms; scipy can hand back the INDEX of the
nearest opaque pixel for free (return_indices). So:
    out_rgb[p] = src_rgb[ nearest_opaque_pixel(p) ]
For an interior opaque pixel the nearest opaque pixel is itself -> RGB unchanged.
For a newly-added pixel it is the closest edge colour -> the corner fills with
the shape's own colour, no black fringe. AE hands SmartFX STRAIGHT rgb
([[ae-input-is-straight-not-premultiplied]]), so we keep straight rgb + the new
coverage as alpha and let AE premultiply.

Guards the port needs (all asserted below)
------------------------------------------
- radius 0            -> exact passthrough (rgb AND alpha identical).
- empty shape         -> passthrough, invent nothing (the BS phantom-box lesson).
- fully opaque frame  -> no contour, no change.
- HDR rgb (>1)        -> never clamped in the interior (32-bit float safe).
- NaN / negative alpha-> sanitised to [0,1], no NaN escapes.
- downsample_x/y      -> radius is in LAYER px, so scale by downsample (like BS).
- thin feature        -> opening erases < 2*rv; Amount backs it off (documented).

Run:  python cr_step5_hardening.py
"""

import numpy as np
import cv2
from scipy.ndimage import distance_transform_edt

from cr_step4_controls import corner_rounder, profile_to_p


def nearest_opaque_color(rgb, opaque):
    """For every pixel, the RGB of the nearest opaque source pixel (edge extend).
    Identity on the interior (nearest opaque pixel is itself)."""
    if not opaque.any():
        return rgb.copy()
    # distance_transform_edt gives, for each pixel, the index of the nearest ZERO.
    # Make opaque the zeros (input = ~opaque) so indices point at opaque pixels.
    iy, ix = distance_transform_edt(~opaque, return_indices=True)[1]
    return rgb[iy, ix]


def corner_rounder_rgba(rgba, radius=10, concave_radius=None, profile=0.0,
                        feather=0.75, amount=1.0, preserve_src_aa=True,
                        downsample=(1.0, 1.0), thresh=0.5):
    """Full RGBA effect. rgba: (H,W,4) float, straight alpha. Returns (H,W,4)."""
    rgb = rgba[:, :, :3].astype(np.float32)
    a = rgba[:, :, 3].astype(np.float32)

    # sanitise input alpha (32-bit float can carry NaN / out-of-range)
    a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=0.0)
    a = np.clip(a, 0.0, 1.0)

    # radius is specified in LAYER pixels; AE renders at downsample_x/y, so the
    # radius in RENDER pixels shrinks with the downsample (BS lesson).
    rv = radius * downsample[0]
    rc = (radius if concave_radius is None else concave_radius) * downsample[1]

    # short-circuits: nothing to do -> exact passthrough, invent nothing.
    opaque = a >= thresh
    if (rv < 0.5 and rc < 0.5) or amount <= 1e-6:
        return rgba.copy()
    if not opaque.any() or opaque.all():
        return rgba.copy()                      # no contour: empty or solid frame

    out_a = corner_rounder(a, radius=rv, concave_radius=rc, profile=profile,
                           feather=feather, amount=amount,
                           preserve_src_aa=preserve_src_aa, thresh=thresh)
    out_a = np.clip(np.nan_to_num(out_a, nan=0.0), 0.0, 1.0)

    out_rgb = nearest_opaque_color(rgb, opaque)  # edge-extend; identity interior
    out = np.dstack([out_rgb, out_a]).astype(np.float32)
    return out


# --------------------------------------------------------------------------
def colored_shapes(h=420, w=760):
    """Coloured square (red), cross (green), star (blue) on transparent bg,
    STRAIGHT alpha. RGB outside the shapes is left BLACK on purpose - that is
    exactly the garbage an added pixel would inherit without the colour policy."""
    rgba = np.zeros((h, w, 4), np.float32)

    def stamp(mask, color):
        rgba[mask, :3] = color
        rgba[mask, 3] = 1.0

    sq = np.zeros((h, w), bool); sq[120:280, 40:200] = True
    stamp(sq, (0, 0, 1))                                  # red (BGR)

    cr = np.zeros((h, w), np.uint8)
    cv2.rectangle(cr, (346, 110), (414, 290), 1, -1)
    cv2.rectangle(cr, (290, 166), (470, 234), 1, -1)
    stamp(cr.astype(bool), (0, 1, 0))                    # green

    st = np.zeros((h, w), np.uint8)
    pts = []
    for i in range(10):
        ang = -np.pi/2 + i*np.pi/5
        rad = 100 if i % 2 == 0 else 42
        pts.append((620 + rad*np.cos(ang), 200 + rad*np.sin(ang)))
    cv2.fillPoly(st, [np.array(pts, np.int32)], 1)
    stamp(st.astype(bool), (1, 0.4, 0))                  # blue-ish

    return rgba


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main():
    rgba = colored_shapes()
    a = rgba[:, :, 3]
    allok = True

    print("Corner Rounder - Step 5 hardening")

    # 1. radius 0 -> exact passthrough
    o = corner_rounder_rgba(rgba, radius=0)
    allok &= check("radius 0 is an exact passthrough",
                   np.array_equal(o, rgba))

    # 2. empty shape -> passthrough, invent nothing
    empty = np.zeros_like(rgba)
    o = corner_rounder_rgba(empty, radius=14)
    allok &= check("empty shape invents nothing", np.array_equal(o, empty))

    # 3. fully opaque frame -> no change
    solid = rgba.copy(); solid[:, :, 3] = 1.0
    o = corner_rounder_rgba(solid, radius=14)
    allok &= check("fully opaque frame unchanged", np.array_equal(o, solid))

    # 4. HDR rgb (>1) preserved in the interior
    hdr = rgba.copy(); hdr[a > 0.5, :3] *= 6.0            # bright interior
    o = corner_rounder_rgba(hdr, radius=14)
    interior = (o[:, :, 3] > 0.99)
    allok &= check("HDR interior rgb not clamped",
                   o[interior, :3].max() > 5.0)

    # 5. NaN / negative alpha sanitised, no NaN escapes
    bad = rgba.copy()
    bad[10, 10, 3] = np.nan; bad[11, 11, 3] = -3.0; bad[12, 12, 3] = 50.0
    o = corner_rounder_rgba(bad, radius=14)
    allok &= check("no NaN in output & alpha in [0,1]",
                   np.isfinite(o).all() and o[:, :, 3].min() >= 0
                   and o[:, :, 3].max() <= 1)

    # 6. concave fill has NO black fringe (added green-cross pixels are green)
    o = corner_rounder_rgba(rgba, radius=0, concave_radius=16)   # concave only
    # a pixel added in the cross's concave notch region should be greenish, not black
    added = (o[:, :, 3] > 0.5) & (a < 0.5)
    if added.any():
        # of the added pixels near the cross, green channel should dominate black
        greenish = (o[added, 1] > 0.3).mean()
        allok &= check(f"added concave pixels take edge colour, not black "
                       f"({greenish*100:.0f}% coloured)", greenish > 0.8)
    else:
        allok &= check("added concave pixels exist to colour", False)

    # 7. downsample scaling: half-res radius ~ half the pixels moved
    full = corner_rounder_rgba(rgba, radius=20, downsample=(1.0, 1.0))
    half = corner_rounder_rgba(rgba, radius=20, downsample=(0.5, 0.5))
    moved_full = (np.abs(full[:, :, 3] - a) > 0.5).sum()
    moved_half = (np.abs(half[:, :, 3] - a) > 0.5).sum()
    allok &= check(f"downsample shrinks radius (moved {moved_half} < "
                   f"{moved_full})", 0 < moved_half < moved_full)

    # 8. thin feature + Amount backoff (documented interaction, not a failure)
    print(f"  [note] profile p range {profile_to_p(0):.0f}..{profile_to_p(1):.0f}; "
          f"convex radius erases features thinner than 2*rv - Amount dials it back.")

    # hero renders (premultiply for a natural preview over black)
    def premult_png(name, img):
        pm = img[:, :, :3] * img[:, :, 3:4]
        cv2.imwrite(name, (np.clip(pm, 0, 1)*255).astype(np.uint8))
    premult_png("out_cr5_source.png", rgba)
    premult_png("out_cr5_round_circular.png",
                corner_rounder_rgba(rgba, radius=16, profile=0.0))
    premult_png("out_cr5_round_squircle.png",
                corner_rounder_rgba(rgba, radius=16, profile=0.6))
    premult_png("out_cr5_amount_50.png",
                corner_rounder_rgba(rgba, radius=16, amount=0.5))

    print(f"\n{'ALL CHECKS PASS' if allok else 'SOME CHECKS FAILED'}")
    print("Wrote out_cr5_*.png. Ready to scaffold the C++ port from the "
          "Buildable Stroke skeleton (SmartFX buffer expansion, crop-to-bbox, "
          "FH-EDT / JFA, dynamic groups all reused).")


if __name__ == "__main__":
    main()

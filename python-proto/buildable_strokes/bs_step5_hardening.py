"""
Buildable Stroke - Step 5: Hardening (the shape of the actual plugin).

Steps 1-4 got the idea right. This step turns it into something worth shipping -
the feature set Photoshop's Stroke layer effect and Illustrator's Appearance
stack have, which AE is missing. Five upgrades:

1. CORNER STYLE AS A CONTINUOUS SLIDER
   Step 4 showed the metric IS the corner style: L-inf -> miter, L2 -> round,
   L1 -> bevel. Here we make it a slider instead of 3 hardcoded cases.

   We first tried a slicker idea - compute the vector field (offset to nearest
   opaque pixel) ONCE, then get every style by applying a different L^p norm
   to it - and REJECTED it after measuring: the nearest feature under L2 is
   not the nearest under L1/L-inf, giving up to 73px of error (see norm_p).
   The p<1 "concave" case degenerated into radial spikes.

   What actually works: compute the three EXACT fields (each O(N), each an
   algorithm from Step 4), then blend between them. They're always ordered
   L-inf <= L2 <= L1, so a slider can lerp miter->round->bevel, and
   EXTRAPOLATING past bevel along (bevel - round) pushes the corner past flat
   into a scoop - that's CONCAVE, obtained honestly.
        corner = 0 miter | 1 round | 2 bevel | 3 concave

2. SIGNED distance -> inner / outer / center strokes.
   sdf < 0 inside the shape, > 0 outside, 0 on the boundary. Photoshop's
   "Position: Outside / Inside / Center" falls straight out of this.

3. SUB-PIXEL EDGE from the alpha ramp.
   A thresholded mask quantizes the edge to whole pixels, so every stroke
   inherits that stair-stepping. But the source alpha already knows the true
   coverage: alpha 0.5 means the edge passes through the pixel CENTER. So near
   the boundary we replace the integer distance with -(alpha - 0.5). This is
   what makes strokes land on the real contour instead of the pixel grid.

4. SMOOTHSTEP ANTI-ALIASING on the band edges.
   Step 3 used a hard (d >= start) test -> jaggies. Now each band edge is a
   smoothstep over +-0.5px, so coverage ramps 0->1 across one pixel. Coverage
   becomes the stroke's alpha, and it composites properly.

5. CUMULATIVE param model (gap/width), the "buildable" UX.
   The MATH always measures from the original shape - one field, absolute
   bands. But the USER shouldn't type absolute offsets; inserting a stroke
   would force renumbering everything after it. So each stroke declares
   (gap, width) and we accumulate:
        start = cursor + gap ;  cursor = start + width
   with a SEPARATE cursor per side (outer strokes stack outward, inner
   strokes stack inward). Reordering just re-walks the accumulator.

Run:  python bs_step5_hardening.py
"""

import numpy as np
import cv2
from scipy import ndimage

# ---------------------------------------------------------------------------
# 1. The vector distance field
# ---------------------------------------------------------------------------

def vector_distance(mask):
    """Offset to the nearest True pixel, per pixel.

    scipy's EDT with return_indices gives the COORDINATES of each pixel's
    nearest feature; subtracting our own coordinates turns that into a vector.
    (Our Step-4 FH transform extends the same way - you carry the source index
    through the parabola walk - which is what the C++ port will do.)

    Returns (dy, dx, (iy, ix)) - the vector, and the feature's coordinates.
    """
    _, (iy, ix) = ndimage.distance_transform_edt(~mask, return_indices=True)
    yy, xx = np.indices(mask.shape)
    return (iy - yy).astype(np.float32), (ix - xx).astype(np.float32), (iy, ix)


def norm_p(dy, dx, p):
    """Apply an L^p norm to offset vectors.

    WARNING - only valid for p=2 here. Tempting idea we TESTED AND REJECTED:
    "compute the vector field once, then get every corner style by applying a
    different L^p norm to it." It is wrong. The vector field records the
    nearest feature under L2, but the nearest feature under L1 or L-inf is a
    DIFFERENT pixel. Measured against exact transforms on CTBS.png:
        p=1   (bevel): max error 73px, >1px wrong on 13.4% of pixels
        p=inf (miter): max error 50px, >1px wrong on 15.9% of pixels
    It looks fine close to the shape (where the nearest feature usually
    agrees) and falls apart further out - the p<1 "concave" case degenerated
    into radial spikes. Kept only as the L2 evaluator + a cautionary note.
    """
    ady, adx = np.abs(dy), np.abs(dx)
    if p >= 50:
        return np.maximum(ady, adx)
    if p == 1.0:
        return ady + adx
    return (ady ** p + adx ** p) ** (1.0 / p)


def exact_metric_fields(mask):
    """The three EXACT distance fields, one per metric. Each is O(N).

    L-inf (chessboard) <= L2 (euclidean) <= L1 (taxicab), always. Those are the
    three corner styles, and because they're ordered we can slide between them.
    scipy's cdt IS the two-pass chamfer from Step 4; its edt is FH. Both are
    the algorithms we already wrote and will port to C++.
    """
    inv = ~mask
    return {
        "miter": ndimage.distance_transform_cdt(inv, metric="chessboard").astype(np.float32),
        "round": ndimage.distance_transform_edt(inv).astype(np.float32),
        "bevel": ndimage.distance_transform_cdt(inv, metric="taxicab").astype(np.float32),
    }


# Corner slider: 0 = miter, 1 = round, 2 = bevel, >2 = concave (extrapolated).
CORNER_PRESETS = {"miter": 0.0, "round": 1.0, "bevel": 2.0, "concave": 3.0}


def corner_field(fields, corner):
    """Blend the exact fields into one, by a continuous corner-style slider.

    0..1 lerps miter->round, 1..2 lerps round->bevel, and >2 EXTRAPOLATES past
    bevel along the (bevel - round) direction, which pushes the corner past
    flat into a scoop -> concave. Because the three fields are ordered and
    each is exact, every blend stays a sane monotone distance field.
    """
    m, r, b = fields["miter"], fields["round"], fields["bevel"]
    c = float(corner)
    if c <= 1.0:
        return m + (r - m) * max(c, 0.0)
    if c <= 2.0:
        return r + (b - r) * (c - 1.0)
    return b + (b - r) * (c - 2.0)


# ---------------------------------------------------------------------------
# 2+3. Signed field with a sub-pixel edge
# ---------------------------------------------------------------------------

def signed_distance(alpha, corner=1.0, thresh=0.5, subpixel=True):
    """Signed distance: negative inside, positive outside, 0 on the contour.

    'corner' is the corner-style slider (see corner_field). Inside and outside
    each get their own set of exact metric fields.

    THE SUB-PIXEL CORRECTION (this is what makes the AA actually work):
    the raw field is built from INTEGER offset vectors, so its values are
    quantized to the sparse set {sqrt(dx^2+dy^2)} - measured on this image,
    only ~5 distinct values exist in any 1px window. A smoothstep has almost
    nothing to ramp across, so stroke edges on curves stay jaggy no matter how
    wide you make the AA.
    The feature pixel we landed on knows better: its alpha coverage says where
    the true contour sits relative to its center, at (a - 0.5) along the
    outward normal. Subtracting that pushes every distance onto the real
    sub-pixel contour and takes the field from ~5 to ~37 distinct values per
    pixel of range. THEN the smoothstep has something to work with.
    """
    mask = alpha >= thresh

    d_out = corner_field(exact_metric_fields(mask), corner)    # dist to opaque
    d_in  = corner_field(exact_metric_fields(~mask), corner)   # dist to transparent
    if subpixel:
        # The half-pixel correction needs the nearest FEATURE's coverage. We
        # take it from the L2 feature transform for every metric: the term is
        # bounded by 0.5px, so borrowing it across metrics is a sub-pixel
        # approximation - unlike the 73px error from norm-swapping above.
        _, _, (iy_o, ix_o) = vector_distance(mask)
        _, _, (iy_i, ix_i) = vector_distance(~mask)
        # nearest opaque pixel has a >= 0.5 -> correction in [0, 0.5]
        d_out = d_out - (alpha[iy_o, ix_o] - 0.5)
        # nearest transparent pixel has a < 0.5 -> symmetric correction
        d_in = d_in - (0.5 - alpha[iy_i, ix_i])
    else:
        d_out, d_in = d_out - 0.5, d_in - 0.5
    sdf = np.where(mask, -d_in, d_out).astype(np.float32)

    # On the contour itself the pixel's OWN coverage is the exact answer.
    if subpixel:
        near = np.abs(sdf) <= 1.0
        sdf[near] = -(alpha[near] - 0.5)
    return sdf


# ---------------------------------------------------------------------------
# 4. Anti-aliased banding
# ---------------------------------------------------------------------------

def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def band_coverage(d, start, end, aa=0.5):
    """Soft membership of [start, end) with a +-aa pixel ramp on each side."""
    return smoothstep(start - aa, start + aa, d) * (
        1.0 - smoothstep(end - aa, end + aa, d))


# ---------------------------------------------------------------------------
# 5. The cumulative param model
# ---------------------------------------------------------------------------

def resolve_stack(strokes):
    """(gap, width, side) per stroke -> absolute [start, end) bands.

    One cursor PER SIDE: outer strokes stack outward from the contour, inner
    strokes stack inward. 'center' straddles the contour and is measured on the
    outer cursor. This is the only place the user's relative model is turned
    into the absolute distances the engine bands on.
    """
    cursor = {"outer": 0.0, "inner": 0.0, "center": 0.0}
    out = []
    for s in strokes:
        side = s.get("side", "outer")
        gap, width = s.get("gap", 0.0), s["width"]
        if side == "center":
            start = cursor["outer"] + gap
            lo, hi = start - width / 2.0, start + width / 2.0
            cursor["outer"] = hi
        else:
            lo = cursor[side] + gap
            hi = lo + width
            cursor[side] = hi
        out.append({**s, "side": side, "lo": lo, "hi": hi})
    return out


def measure_for_side(sdf, side):
    """Which direction this stroke reads the signed field in."""
    return -sdf if side == "inner" else sdf


# ---------------------------------------------------------------------------
# compositing
# ---------------------------------------------------------------------------

def over(dst_rgb, dst_a, src_rgb, src_a):
    out_a = src_a + dst_a * (1.0 - src_a)
    safe = np.maximum(out_a, 1e-6)[..., None]
    out_rgb = (src_rgb * src_a[..., None]
               + dst_rgb * dst_a[..., None] * (1.0 - src_a[..., None])) / safe
    return out_rgb.astype(np.float32), out_a.astype(np.float32)


def render(alpha, src_rgb, strokes, corner=1.0, aa=0.5):
    """Full effect: signed field -> resolved stack -> AA bands -> composite."""
    sdf = signed_distance(alpha, corner=corner)
    h, w = alpha.shape
    rgb = np.zeros((h, w, 3), np.float32)
    a = np.zeros((h, w), np.float32)
    for s in resolve_stack(strokes):
        d = measure_for_side(sdf, s["side"])
        cov = band_coverage(d, s["lo"], s["hi"], aa) * s.get("opacity", 1.0)
        layer = np.zeros((h, w, 3), np.float32)
        layer[:] = s["color"]
        rgb, a = over(rgb, a, layer, cov.astype(np.float32))
    # the source art sits ON TOP of its strokes
    return over(rgb, a, src_rgb, alpha)


# ---------------------------------------------------------------------------

def load(path):
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    alpha = bgra[:, :, 3].astype(np.float32) / 255.0
    rgb = bgra[:, :, 2::-1].astype(np.float32) / 255.0   # BGR->RGB
    return rgb, alpha


def save(name, rgb, a, bg=1.0):
    flat = rgb * a[..., None] + bg * (1.0 - a[..., None])
    cv2.imwrite(name, (np.clip(flat[..., ::-1], 0, 1) * 255).astype(np.uint8))


def main():
    src_rgb, alpha = load("CTBS.png")

    # --- correctness checks ------------------------------------------------
    print("checks:")
    mask = alpha >= 0.5
    dy, dx, _ = vector_distance(mask)
    ref = ndimage.distance_transform_edt(~mask)
    ours = norm_p(dy, dx, 2.0)
    err = np.abs(ours - ref).max()
    print(f"  [{'ok ' if err < 1e-4 else 'FAIL'}] vector-field L2 == scipy EDT "
          f"(max err {err:.2e})")

    # the sub-pixel correction must make the field finer, or AA can't work
    def levels(f):
        return len(np.unique(f[(f > 3.5) & (f < 4.5)]))
    coarse = levels(signed_distance(alpha, subpixel=False))
    fine = levels(signed_distance(alpha, subpixel=True))
    print(f"  [{'ok ' if fine > 4 * coarse else 'FAIL'}] sub-pixel refines field "
          f"({coarse} -> {fine} distinct values per 1px window)")

    sdf = signed_distance(alpha)
    print(f"  [{'ok ' if sdf.min() < 0 < sdf.max() else 'FAIL'}] sdf is signed "
          f"(range {sdf.min():.1f} .. {sdf.max():.1f})")
    zero_band = np.abs(sdf) <= 0.5
    print(f"  [{'ok ' if zero_band.sum() > 0 else 'FAIL'}] contour band exists "
          f"({zero_band.sum()} px within 0.5)")

    cov = band_coverage(np.array([4.0, 5.0, 5.5, 10.0, 10.5, 11.0]), 5.0, 10.5)
    print(f"  [{'ok ' if cov[0] == 0 and cov[3] > 0.99 and cov[5] == 0 else 'FAIL'}]"
          f" band AA ramps: {np.round(cov, 3)}")

    st = resolve_stack([{"width": 10}, {"width": 8}, {"gap": 4, "width": 6}])
    bands = [(s["lo"], s["hi"]) for s in st]
    print(f"  [{'ok ' if bands == [(0,10),(10,18),(22,28)] else 'FAIL'}] "
          f"accumulator: {bands}")

    # --- the hero render ---------------------------------------------------
    strokes = [
        {"width": 3,  "side": "inner",  "color": (1.00, 1.00, 1.00), "opacity": 0.9},
        {"width": 10, "side": "outer",  "color": (1.00, 1.00, 1.00)},
        {"width": 8,  "side": "outer",  "color": (0.90, 0.10, 0.15)},
        {"width": 8,  "side": "outer",  "color": (0.10, 0.30, 0.95)},
        {"gap": 4, "width": 6,  "side": "outer", "color": (1.00, 0.80, 0.05)},
        {"gap": 4, "width": 18, "side": "outer", "color": (0.10, 0.10, 0.12),
         "opacity": 0.35},
    ]
    print("\nresolved stack (relative gap/width -> absolute bands):")
    for i, s in enumerate(resolve_stack(strokes)):
        print(f"  #{i} {s['side']:6s} {s['lo']:6.1f}..{s['hi']:5.1f}px")

    rgb, a = render(alpha, src_rgb, strokes)
    save("out_bs5_hero.png", rgb, a)

    # --- corner styles from the SAME field ---------------------------------
    fat = [{"width": 26, "gap": 4, "side": "outer", "color": (0.95, 0.15, 0.15)}]
    for name, cv in CORNER_PRESETS.items():
        r, aa_ = render(alpha, src_rgb, fat, corner=cv)
        save(f"out_bs5_corner_{name}.png", r, aa_)
        img = cv2.imread(f"out_bs5_corner_{name}.png")
        cv2.imwrite(f"out_bs5_cornerzoom_{name}.png", img[150:320, 190:380])
    print(f"\ncorner styles via blended EXACT fields: "
          f"{', '.join(f'{k}(corner={v})' for k, v in CORNER_PRESETS.items())}")

    # --- AA proof, on a CURVED edge (where AA actually matters) ------------
    # Crop the outer stroke on the circle's upper-right diagonal.
    Y0, Y1, X0, X1 = 120, 200, 810, 910
    variants = (("hard", 0.001, True), ("aa", 0.5, True), ("aa_nosub", 0.5, False))
    for tag, aa_px, sub in variants:
        sdf_v = signed_distance(alpha, subpixel=sub)
        h, w = alpha.shape
        rgb_v = np.zeros((h, w, 3), np.float32)
        a_v = np.zeros((h, w), np.float32)
        for s in resolve_stack(fat):
            cov = band_coverage(sdf_v, s["lo"], s["hi"], aa_px)
            layer = np.zeros((h, w, 3), np.float32)
            layer[:] = s["color"]
            rgb_v, a_v = over(rgb_v, a_v, layer, cov.astype(np.float32))
        rgb_v, a_v = over(rgb_v, a_v, src_rgb, alpha)
        save(f"out_bs5_{tag}.png", rgb_v, a_v)
        img = cv2.imread(f"out_bs5_{tag}.png")
        cv2.imwrite(f"out_bs5_{tag}_zoom.png",
                    cv2.resize(img[Y0:Y1, X0:X1], None, fx=6, fy=6,
                               interpolation=cv2.INTER_NEAREST))

    print("\nWrote out_bs5_hero.png, out_bs5_corner{,zoom}_*.png, "
          "out_bs5_{hard,aa}_zoom.png")


if __name__ == "__main__":
    main()

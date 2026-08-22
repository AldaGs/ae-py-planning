"""
Corner Rounder - Step 1: rounding a corner IS a morphological operation.

Goal of the whole effect: take a layer's alpha silhouette and round its sharp
corners by a chosen radius r, the way Illustrator's "Round Corners" or a
rounded-rectangle widget does - but on a RASTER alpha (text, logos, masks),
not a vector path. This first step does no distance fields and nothing fast;
it just proves WHAT rounding is, visually, so the later steps have a ground
truth to match.

The one idea to take away
-------------------------
Round a corner = roll a disk of radius r along the shape and keep where the
disk can reach. That "roll a disk" operation is exactly mathematical
morphology, built from two primitives on a binary mask:

    EROSION  by disk r : keep a pixel only if the WHOLE disk centred on it fits
                         inside the shape. Shrinks the shape; outer corners get
                         clipped back to where the disk fits.
    DILATION by disk r : keep a pixel if the disk centred on it touches the
                         shape at all. Grows the shape; fills outward.

Neither alone rounds nicely - erosion shrinks, dilation grows. But composed:

    OPENING  = erode then dilate.  Rounds CONVEX corners (the outer points).
               The disk can't be pushed into a sharp outer corner, so after
               erode+dilate that corner becomes a quarter-circle of radius r.
               Straight edges and the interior come back unchanged.
    CLOSING  = dilate then erode.  Rounds CONCAVE corners (the inner notches),
               by the mirror argument.

    OPEN then CLOSE = round EVERY corner, convex and concave, and - because
    each of open/close leaves straight edges where they were - the flat sides
    of the shape stay put. Only the corners move. That is the "preserve the
    edges, round in place" behavior we want.

Why a DISK kernel (not a square)?
    The kernel shape is the shape the corner becomes. A disk -> circular-arc
    corners (true rounding). A square kernel would give chamfered/mitred
    corners. We build a disk once with a radius test x^2+y^2 <= r^2.

A CAVEAT you can already see in the math: opening ERASES any feature thinner
than the disk (erosion eats it before dilation can bring it back), and closing
FILLS any gap thinner than the disk. That is inherent to morphological
rounding - a big radius on thin strokes (like text or an outline) will consume
them. So this step demos on a SOLID synthetic shape with real corners; the
thin-featured CTBS.png returns in later steps once we care about AA on true
alpha, where we'll keep the radius modest.

This step uses cv2's erode/dilate purely to SEE the concept. It is O(r^2) per
pixel (slow for big r) and it works on the hard 0/1 mask so the rounded edge
is aliased. Step 2 replaces the kernel with a DISTANCE FIELD (the JFA/EDT
engine from Buildable Stroke) to make it radius-independent in cost, and Step 3
puts the anti-aliasing back by reading sub-pixel coverage. For now: just look
at which corners each operation rounds.

Run:  python cr_step1_morphology.py
Then open out_cr1_*.png - especially the *_zoom crops on a corner.
"""

import numpy as np
import cv2


def synthetic_mask(h=420, w=760):
    """A solid test canvas with unambiguous corners:
      - a filled SQUARE   : four convex right-angle corners.
      - a filled PLUS/CROSS: convex corners AND four concave inner corners.
      - a filled 5-point STAR: sharp convex points + deep concave notches.
    Returns a uint8 0/1 mask (H, W). Solid shapes so opening ~= mask (only the
    corners move), which makes open-vs-close obvious.
    """
    m = np.zeros((h, w), np.uint8)

    # square
    cv2.rectangle(m, (40, 120), (200, 280), 1, thickness=-1)

    # plus / cross (two overlapping rectangles) -> 4 concave inner corners
    cx, cy, arm, thick = 380, 200, 90, 34
    cv2.rectangle(m, (cx - thick, cy - arm), (cx + thick, cy + arm), 1, -1)
    cv2.rectangle(m, (cx - arm, cy - thick), (cx + arm, cy + thick), 1, -1)

    # 5-point star
    scx, scy, R, rr = 620, 200, 100, 42
    pts = []
    for i in range(10):
        ang = -np.pi / 2 + i * np.pi / 5
        rad = R if i % 2 == 0 else rr
        pts.append((scx + rad * np.cos(ang), scy + rad * np.sin(ang)))
    cv2.fillPoly(m, [np.array(pts, np.int32)], 1)

    return m


def disk_kernel(r):
    """A binary disk of radius r as a (2r+1, 2r+1) uint8 kernel.

    The kernel IS the shape a rounded corner takes. x^2+y^2 <= r^2 gives a
    circular disk -> circular-arc corners. Center at (r, r).
    """
    d = 2 * r + 1
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return ((xx * xx + yy * yy) <= r * r).astype(np.uint8)


def erode(mask_u8, k):
    return cv2.erode(mask_u8, k)


def dilate(mask_u8, k):
    return cv2.dilate(mask_u8, k)


def opening(mask_u8, k):
    """Erode then dilate -> rounds CONVEX corners with radius = kernel radius."""
    return dilate(erode(mask_u8, k), k)


def closing(mask_u8, k):
    """Dilate then erode -> rounds CONCAVE corners."""
    return erode(dilate(mask_u8, k), k)


def round_all(mask_u8, k):
    """Open then close -> round convex AND concave, straight edges stay put."""
    return closing(opening(mask_u8, k), k)


def save(name, mask_u8):
    cv2.imwrite(name, mask_u8 * 255)


def save_zoom(name, mask_u8, cx, cy, half=60, scale=6):
    """Crop a square window around (cx,cy) and nearest-neighbour zoom it, so the
    aliased corner staircase and the arc are both clearly visible."""
    h, w = mask_u8.shape
    x0, x1 = max(0, cx - half), min(w, cx + half)
    y0, y1 = max(0, cy - half), min(h, cy + half)
    crop = mask_u8[y0:y1, x0:x1] * 255
    big = cv2.resize(crop, None, fx=scale, fy=scale,
                     interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(name, big)


def main():
    mask = synthetic_mask()
    h, w = mask.shape
    print(f"image: {w}x{h}   opaque: {mask.mean()*100:5.2f}%")

    r = 10
    k = disk_kernel(r)
    print(f"disk kernel radius r={r}  ->  {k.shape[0]}x{k.shape[1]}, "
          f"{int(k.sum())} pixels set")

    ero = erode(mask, k)
    dil = dilate(mask, k)
    opn = opening(mask, k)
    cls = closing(mask, k)
    rnd = round_all(mask, k)

    # How much each operation changed the silhouette (sanity signal).
    def pct(m):
        return f"{m.mean()*100:5.2f}%"
    print(f"mask     {pct(mask)}")
    print(f"erode    {pct(ero)}   (shrunk)")
    print(f"dilate   {pct(dil)}   (grown)")
    print(f"opening  {pct(opn)}   (~mask, convex corners clipped)")
    print(f"closing  {pct(cls)}   (~mask, concave notches filled)")
    print(f"round    {pct(rnd)}   (all corners rounded, edges preserved)")

    save("out_cr1_mask.png", mask)
    save("out_cr1_erode.png", ero)
    save("out_cr1_dilate.png", dil)
    save("out_cr1_opening.png", opn)
    save("out_cr1_closing.png", cls)
    save("out_cr1_round.png", rnd)

    # Zoom on the STAR (has convex points AND concave notches side by side).
    cx, cy = 620, 200
    print(f"zoom on star at ({cx},{cy})")
    save_zoom("out_cr1_zoom_mask.png", mask, cx, cy, half=120)
    save_zoom("out_cr1_zoom_opening.png", opn, cx, cy, half=120)  # points rounded
    save_zoom("out_cr1_zoom_closing.png", cls, cx, cy, half=120)  # notches rounded
    save_zoom("out_cr1_zoom_round.png", rnd, cx, cy, half=120)    # both

    print("\nWrote out_cr1_*.png")
    print("Compare zoom_mask (sharp) vs zoom_opening (convex corner is now a "
          "quarter-circle) vs zoom_closing (convex corner untouched - closing "
          "only rounds concave). zoom_round rounds everything.")
    print("Note the aliased staircase on the rounded arc - Step 3 fixes that "
          "with sub-pixel coverage; Step 2 first makes it fast via distance "
          "fields instead of an O(r^2) kernel.")


if __name__ == "__main__":
    main()

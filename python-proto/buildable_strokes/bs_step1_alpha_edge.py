"""
Buildable Stroke - Step 1: The alpha edge (where a stroke is measured FROM).

Goal: a stroke is a band of color that sits at a chosen DISTANCE from the
layer's shape. Before we can talk about distance, we need two things nailed
down, and that's all this step does:
    (1) the SHAPE - a crisp binary mask of "opaque vs not", from the alpha.
    (2) the EDGE  - the boundary of that shape, the pixels distance 0 lives on.
Every later step (dilation rings, real distance transform, the colored bands)
is just "how far is each pixel from THIS edge". Get the edge right first.

Why threshold the alpha at all?
    Alpha is 0..255 with a thin anti-aliased ramp on every contour (in CTBS.png
    ~1.3% of pixels are partially transparent). "Distance to the edge" needs a
    yes/no notion of inside vs outside, so we cut the ramp at the half point:
        mask = alpha >= 128       # inside = opaque
    Threshold at the MIDDLE (128, i.e. 0.5) because that's where a 1-pixel AA
    edge crosses from mostly-out to mostly-in, so the binary contour lands on
    the visual contour. Too low and the stroke bulges into the fuzzy halo; too
    high and it eats into the shape.

Why two kinds of edge?
    A stroke that grows OUTWARD cares about the OUTER boundary: transparent
    pixels that touch the shape. A stroke growing INWARD cares about the INNER
    boundary: opaque pixels that touch transparent. We compute both by hand
    with 4-neighbour shifts (no cv2 morphology) - the exact same shift-and-
    compare move we'll iterate in Step 2 to actually measure distance.

Run:  python bs_step1_alpha_edge.py
Then open out_bs1_*.png. The edge images should trace the text + circle + box.
"""

import numpy as np
import cv2


def load_alpha(path):
    """Load an RGBA PNG and return (bgr_uint8, alpha_float01).

    cv2.IMREAD_UNCHANGED keeps the 4th channel; without it OpenCV drops alpha.
    OpenCV gives BGRA, so channel 3 is alpha. We return alpha as float [0,1]
    because that's how 32-bit AE pixels arrive - keeps the math portable.
    """
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra is None:
        raise FileNotFoundError(path)
    if bgra.shape[2] != 4:
        raise ValueError("expected an RGBA image with an alpha channel")
    bgr = bgra[:, :, :3]
    alpha = bgra[:, :, 3].astype(np.float32) / 255.0
    return bgr, alpha


def binarize(alpha, thresh=0.5):
    """Alpha ramp -> hard inside/outside mask. Boolean (H, W)."""
    return alpha >= thresh


def neighbours_any_false(mask):
    """True where a pixel has >=1 four-neighbour that is False (outside).

    We shift the mask by one pixel in each of the 4 cardinal directions and ask
    "was my neighbour outside?". Border is treated as outside (pad with False).
    This is the primitive that a distance transform iterates: reachability in
    one step. Here we use it once, just to find boundaries.
    """
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    up    = padded[:-2, 1:-1]
    down  = padded[2:,  1:-1]
    left  = padded[1:-1, :-2]
    right = padded[1:-1, 2:]
    # neighbour is outside if it's False -> "any neighbour False" = NOT(all True)
    return ~(up & down & left & right)


def inner_edge(mask):
    """Opaque pixels that touch a transparent neighbour (INWARD stroke edge)."""
    return mask & neighbours_any_false(mask)


def outer_edge(mask):
    """Transparent pixels that touch an opaque neighbour (OUTWARD stroke edge).

    Same idea, inverted: run the neighbour test on the INVERSE mask, then keep
    only the pixels that are themselves outside.
    """
    return (~mask) & neighbours_any_false(~mask)


def main():
    bgr, alpha = load_alpha("CTBS.png")
    h, w = alpha.shape
    mask = binarize(alpha)

    print(f"image: {w}x{h}")
    print(f"opaque (mask True):     {mask.mean()*100:5.2f}% of pixels")
    print(f"partial alpha (AA):     {((alpha>0)&(alpha<1)).mean()*100:5.2f}%")

    inner = inner_edge(mask)
    outer = outer_edge(mask)
    print(f"inner-edge pixels:      {inner.sum():6d}")
    print(f"outer-edge pixels:      {outer.sum():6d}  (slightly more: outside is longer)")

    # --- visualizations ---------------------------------------------------
    # Raw alpha as grayscale: the shape as the plugin will actually see it.
    cv2.imwrite("out_bs1_alpha.png", (alpha * 255).astype(np.uint8))
    # Hard mask after thresholding.
    cv2.imwrite("out_bs1_mask.png", (mask * 255).astype(np.uint8))
    # Edges on their own (white on black).
    cv2.imwrite("out_bs1_edge_inner.png", (inner * 255).astype(np.uint8))
    cv2.imwrite("out_bs1_edge_outer.png", (outer * 255).astype(np.uint8))

    # Overlay: original shape dimmed, inner edge in green, outer edge in red,
    # so you can see the two boundaries hug the contour from either side.
    overlay = (bgr.astype(np.float32) * 0.25).astype(np.uint8)
    overlay[inner] = (0, 255, 0)     # BGR green
    overlay[outer] = (0, 0, 255)     # BGR red
    cv2.imwrite("out_bs1_edges_overlay.png", overlay)

    print("\nWrote out_bs1_{alpha,mask,edge_inner,edge_outer,edges_overlay}.png")
    print("Open the overlay: green traces the shape from inside, red from just "
          "outside. Every stroke in later steps grows off one of these lines.")


if __name__ == "__main__":
    main()

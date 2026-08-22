"""
Buildable Stroke - Step 3: Stacking N strokes (the "buildable" part).

Goal: one distance field, MANY strokes. Each stroke is a small record:
    (start, width, color, opacity)
and the whole effect is: compute the field ONCE, then paint each stroke as a
band on it, compositing them in order. This is the entire feature the plugin
sells - "add another stroke" - and it costs almost nothing because the
expensive part (the distance field) is already done and shared.

Key ideas this step introduces:

1. COMPUTE ONCE, BAND MANY TIMES.
   distance_by_dilation() runs a single time. Adding a 6th stroke does NOT
   re-run it - it's just another threshold on the same array. That's why the
   AE param model can be "a repeatable group" cheaply.

2. START vs WIDTH (the designer's two knobs per stroke).
   start = gap from the shape's edge before this stroke begins (its offset).
   width = how thick the band is. So a stroke occupies [start, start+width).
   A gap between two strokes = one stroke's (start+width) < the next's start.

3. STACKING ORDER + OPACITY = real compositing.
   Strokes can overlap (especially once we add opacity < 1). We paint them
   back-to-front into a straight-alpha RGBA canvas with "over" compositing,
   then put the ORIGINAL SHAPE on top - because a stroke sits BEHIND the art,
   like an outline. The list order is the stacking order (last = frontmost
   among strokes). This mirrors the AE render: strokes are a layer under the
   source.

Still using the dilation distance field from Step 2 (square-ish metric, slow).
Step 4 swaps the field for a fast, true-Euclidean one - and every stroke here
keeps working unchanged, because they only read "distance".

Run:  python bs_step3_multistroke.py
"""

import numpy as np
import cv2
from bs_step2_dilation import load_mask, distance_by_dilation


def over(dst_rgb, dst_a, src_rgb, src_a):
    """Straight-alpha 'source OVER destination'. All args float [0,1].

    Standard Porter-Duff over:
        out_a   = src_a + dst_a*(1-src_a)
        out_rgb = (src_rgb*src_a + dst_rgb*dst_a*(1-src_a)) / out_a
    We guard divide-by-zero where both are transparent.
    """
    out_a = src_a + dst_a * (1.0 - src_a)
    safe = np.maximum(out_a, 1e-6)[..., None]
    out_rgb = (src_rgb * src_a[..., None]
               + dst_rgb * dst_a[..., None] * (1.0 - src_a[..., None])) / safe
    return out_rgb, out_a


def render_strokes(dist, strokes, canvas_shape):
    """Paint a list of strokes into a straight-alpha RGBA canvas.

    strokes: list of dicts {start, width, color (r,g,b in 0..1), opacity}.
    Painted in list order (first = backmost). Returns (rgb, a) floats.
    """
    h, w = canvas_shape
    rgb = np.zeros((h, w, 3), np.float32)
    a = np.zeros((h, w), np.float32)
    for s in strokes:
        band = (dist >= s["start"]) & (dist < s["start"] + s["width"])
        src_rgb = np.zeros((h, w, 3), np.float32)
        src_rgb[band] = s["color"]
        src_a = np.zeros((h, w), np.float32)
        src_a[band] = s["opacity"]
        rgb, a = over(rgb, a, src_rgb, src_a)
    return rgb, a


def main():
    bgr, alpha, mask = load_mask("CTBS.png")
    h, w = mask.shape

    # One distance field, shared by every stroke below.
    MAXD = 70
    dist = distance_by_dilation(mask, MAXD, connectivity=8)

    # The "buildable" stack. Colors are R,G,B in 0..1 (we convert to BGR to save).
    # Read it top-to-bottom = back-to-front: white halo first, then the art on top.
    strokes = [
        {"start": 0,  "width": 10, "color": (1.00, 1.00, 1.00), "opacity": 1.0},  # white base cushion
        {"start": 10, "width": 8,  "color": (0.90, 0.10, 0.15), "opacity": 1.0},  # red
        {"start": 18, "width": 8,  "color": (0.10, 0.30, 0.95), "opacity": 1.0},  # blue
        {"start": 30, "width": 6,  "color": (1.00, 0.80, 0.05), "opacity": 1.0},  # detached gold ring (gap 26->30)
        {"start": 40, "width": 18, "color": (0.10, 0.10, 0.12), "opacity": 0.35}, # soft dark glow-ish, semi-transparent
    ]
    print(f"strokes: {len(strokes)} (distance field computed ONCE, reused for all)")
    for i, s in enumerate(strokes):
        print(f"  #{i}: {s['start']:2d}..{s['start']+s['width']:2d}px  "
              f"opacity {s['opacity']:.2f}")

    s_rgb, s_a = render_strokes(dist, strokes, (h, w))

    # Put the ORIGINAL SHAPE on top of the strokes (strokes sit behind the art).
    src_rgb = (bgr[..., ::-1].astype(np.float32)) / 255.0   # BGR->RGB, 0..1
    comp_rgb, comp_a = over(s_rgb, s_a, src_rgb, alpha)

    # Save stroke-only (on black) and the final composite (on white, to see it).
    def to_bgr8(rgb, a, bg):
        flat = rgb * a[..., None] + bg * (1.0 - a[..., None])   # composite over bg
        return (np.clip(flat[..., ::-1], 0, 1) * 255).astype(np.uint8)

    cv2.imwrite("out_bs3_strokes_only.png", to_bgr8(s_rgb, s_a, 0.0))
    cv2.imwrite("out_bs3_composite.png",    to_bgr8(comp_rgb, comp_a, 1.0))

    print("\nWrote out_bs3_strokes_only.png and out_bs3_composite.png")
    print("composite: the art with a white cushion, red+blue stacked outlines, a "
          "detached gold ring, and a soft dark halo. Add/reorder dicts to 'build'.")


if __name__ == "__main__":
    main()

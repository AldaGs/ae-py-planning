"""
Step 1 - The tell: how to distinguish STRAIGHT from PREMULTIPLIED alpha.

You cannot ask After Effects "is this premultiplied?" - there is no flag you can
trust. So this tool does not ASK. It MEASURES the pixels and infers the format
from one mathematical fact about what premultiplication does:

    premultiplied  ->  color channels were ALREADY multiplied by alpha.
                       On a soft edge (alpha=0.3) a white pixel is stored as
                       RGB=(0.3,0.3,0.3). Color can NEVER exceed alpha.

    straight       ->  color is stored undarkened, independent of alpha.
                       That same white edge pixel is RGB=(1,1,1), alpha=0.3.
                       Color routinely EXCEEDS alpha.

So the decisive test is asymmetric:

    ONE partial-alpha pixel where any of R,G,B > alpha  ==>  PROVES straight.

The reverse is NOT symmetric. Never seeing overshoot does not prove premult,
because a frame with no soft edges (hard-edged, or fully opaque) carries no
evidence either way. That is why the verdict has THREE states, never a bool:

    STRAIGHT   - found overshoot; proven.
    PREMULT    - had partial-alpha edges, none overshot; very likely premult.
    AMBIGUOUS  - no partial-alpha pixels to judge by; refuse to guess.

This step builds LABELED straight and premult versions of the same image so we
know the ground truth, runs the detector, and confirms it calls each correctly.

Run:  python step1_the_tell.py
Then open the out_*.png overshoot maps and see the straight edges light up.
"""

import numpy as np
import cv2

EPS = 1.0 / 255.0  # one 8-bit step; anything smaller is rounding noise, not signal


# ---------------------------------------------------------------------------
# Build ground-truth test data (float RGBA in 0..1)
# ---------------------------------------------------------------------------
def make_soft_shape(size=400):
    """A white disc with a genuinely SOFT (anti-aliased) edge on transparent bg.

    The soft edge is the whole point - it is the band of partial-alpha pixels
    where straight vs premult actually differ. Returns STRAIGHT-alpha RGBA:
    color is full white everywhere the shape has any coverage, alpha is the
    smooth coverage ramp.
    """
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = size / 2.0
    r = size * 0.30
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    # Smooth 2px-wide coverage ramp across the circle boundary -> real AA edge.
    alpha = np.clip((r - dist) / 2.0 + 0.5, 0.0, 1.0).astype(np.float32)

    rgb = np.ones((size, size, 3), dtype=np.float32)   # undarkened white
    straight = np.dstack([rgb, alpha])                 # (H,W,4) straight RGBA
    return straight


def to_premult(straight):
    """Convert straight RGBA -> premultiplied RGBA by multiplying color by alpha."""
    out = straight.copy()
    a = out[..., 3:4]
    out[..., :3] = out[..., :3] * a
    return out


# ---------------------------------------------------------------------------
# The detector - this is the logic that will port to C++ verbatim
# ---------------------------------------------------------------------------
def detect_pixel_format(rgba, eps=EPS):
    """Infer alpha format from pixels. Returns (verdict_str, stats_dict).

    Scans once. Only partial-alpha pixels (0 < a < 1) carry evidence.
    A single channel exceeding alpha there proves STRAIGHT.
    """
    r = rgba[..., 0]
    g = rgba[..., 1]
    b = rgba[..., 2]
    a = rgba[..., 3]

    partial = (a > eps) & (a < 1.0 - eps)      # the AA edge band
    max_rgb = np.maximum(np.maximum(r, g), b)
    overshoot = np.maximum(0.0, max_rgb - a)   # >0 only where color beats alpha

    partial_count = int(partial.sum())
    # overshoot only counts as evidence WHERE alpha is partial
    proof = bool(((overshoot > eps) & partial).any())

    if proof:
        verdict = "STRAIGHT"
    elif partial_count > 0:
        verdict = "PREMULT"
    else:
        verdict = "AMBIGUOUS"

    stats = {
        "partial_alpha_pixels": partial_count,
        "max_overshoot": float(overshoot[partial].max()) if partial_count else 0.0,
        "overshoot_pixels": int(((overshoot > eps) & partial).sum()),
        "alpha_min": float(a.min()),
        "alpha_max": float(a.max()),
    }
    return verdict, stats


def overshoot_map(rgba):
    """Visualize the tell: max(0, maxRGB - alpha), amplified. Straight edges glow."""
    r, g, b, a = [rgba[..., i] for i in range(4)]
    max_rgb = np.maximum(np.maximum(r, g), b)
    om = np.clip((max_rgb - a) * 4.0, 0.0, 1.0)  # x4 so subtle overshoot is visible
    return (om * 255).astype(np.uint8)


def report(name, rgba, expected):
    verdict, stats = detect_pixel_format(rgba)
    ok = "OK " if verdict == expected else "!! WRONG"
    print(f"[{ok}] {name:24s} -> {verdict:9s} (expected {expected})")
    print(f"        partial-alpha px : {stats['partial_alpha_pixels']}")
    print(f"        overshoot px     : {stats['overshoot_pixels']}")
    print(f"        max overshoot    : {stats['max_overshoot']:.3f}")
    print(f"        alpha range      : {stats['alpha_min']:.3f} .. {stats['alpha_max']:.3f}")
    return verdict == expected


def main():
    straight = make_soft_shape()
    premult = to_premult(straight)

    # Two degenerate frames that SHOULD come back ambiguous (no evidence):
    opaque = np.ones((64, 64, 4), dtype=np.float32)          # fully opaque, no edges
    hard = np.zeros((64, 64, 4), dtype=np.float32)           # hard-edged square
    hard[16:48, 16:48, :] = 1.0                              # alpha is 0 or 1 only

    print("Detector verdicts (ground truth known):\n")
    results = [
        report("soft white, straight", straight, "STRAIGHT"),
        report("soft white, premult",  premult,  "PREMULT"),
        report("fully opaque",         opaque,   "AMBIGUOUS"),
        report("hard-edged square",    hard,     "AMBIGUOUS"),
    ]

    # Save the overshoot maps so you can SEE why the verdict came out that way.
    cv2.imwrite("out_overshoot_straight.png", overshoot_map(straight))
    cv2.imwrite("out_overshoot_premult.png",  overshoot_map(premult))
    # And the alpha channels, for reference.
    cv2.imwrite("out_alpha_straight.png", (straight[..., 3] * 255).astype(np.uint8))

    print("\nWrote out_overshoot_straight.png (edge GLOWS -> straight)")
    print("      out_overshoot_premult.png  (black -> premult, color never beats alpha)")
    print("      out_alpha_straight.png")
    print("\nAll correct:", all(results))


if __name__ == "__main__":
    main()

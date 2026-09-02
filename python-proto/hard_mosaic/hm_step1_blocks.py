"""
Hard Mosaic - Step 1: what a mosaic is, and exactly where the extra colours
come from.

The whole effect in one sentence: cut the frame into a grid of blocks and
paint each block a single flat colour. The catch - and the entire reason this
plugin exists - is the word "single". AE's Mosaic picks that colour by
AVERAGING the block, and an average of two colours is a third colour that was
never in the picture. Feed it anti-aliased artwork and the edge blocks come
back as in-between colours with in-between alpha.

This step builds the failure on purpose so the later steps have something to
be measured against.

The source we test on
---------------------
The user's real case: a plain solid rectangle, drawn by AE, so its edges are
ANTI-ALIASED. That means a one-pixel-wide border of pixels whose alpha is
somewhere strictly between 0 and 1 - the pixel is only partly covered by the
shape. Nothing is wrong with those pixels; they are how a rasteriser draws a
straight line that does not land on a pixel boundary. They only become a
problem once something averages them together with their neighbours.

Two ways to pick a block's colour
---------------------------------
    BOX AVERAGE   : mean of every pixel in the block.
                    This is AE Mosaic's default. It is a low-pass filter, and
                    like every low-pass filter it MANUFACTURES values between
                    the ones it was given.
    POINT SAMPLE  : read the one pixel at the block's centre and use it.
                    This is AE Mosaic's "Sharp Colors" checkbox. It cannot
                    manufacture a colour - whatever it returns was genuinely
                    in the source. But it is a lottery: if the centre pixel
                    happens to land on the anti-aliased border, the block
                    inherits that border pixel's partial alpha, and the whole
                    block goes semi-transparent.

So point sampling fixes the *invented colour* problem and leaves the
*partial alpha* problem to chance. And chance is the right word: whether any
centre pixel lands on the one-pixel fringe depends on the block size, the
block phase, and where the shape happens to sit. The sweep printed at the
bottom of this step shows the same image mosaicked at eleven block sizes; some
come out perfectly clean, others leak partial alpha over 5% of the frame.
An effect you cannot predict is an effect you cannot use, which is why
"Sharp Colors" was tried and was not enough.

There is a second, quieter failure in point sampling that no alpha count will
show you: the GEOMETRY lottery. A block that is 90% inside the shape vanishes
if its one centre pixel happens to fall outside, and a block that is 10%
inside appears in full if the centre falls inside. Step 2 has to decide block
membership from the whole block, not from one pixel.

The measurement this step makes
-------------------------------
For each method we count the DISTINCT (r,g,b,a) quadruples in the output. The
source has exactly two solid colours (background, rectangle) plus a fringe of
AA values. A perfect "solid colours only" mosaic would report exactly 2
distinct outputs. Anything above that is colour the effect invented.

Run:  python hm_step1_blocks.py
"""

import numpy as np
import cv2

OUT = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else "."

# --------------------------------------------------------------------------
# The source image: a solid rectangle with anti-aliased edges, on transparent.
# --------------------------------------------------------------------------
# We work in float32 RGBA, 0..1, STRAIGHT (un-premultiplied) alpha, because
# that is what After Effects hands a SmartFX plugin for a shape layer. Straight
# means: the RGB of a half-covered edge pixel is still the shape's FULL colour;
# only the alpha says "half". Remember that - it matters enormously in step 2.

H, W = 360, 640
ORANGE = np.float32([1.00, 0.663, 0.0])      # the user's yellow/orange
BG_RGB = np.float32([0.0, 0.0, 0.0])          # transparent black


def make_source(x0=96.4, y0=60.7, x1=520.3, y1=248.15):
    """Solid rectangle, edges at fractional coordinates so they anti-alias.

    Alpha is computed as exact pixel COVERAGE: the area of the intersection of
    the pixel square with the rectangle. That is what a good rasteriser does,
    and it is separable - overlap in x times overlap in y - so it is two 1-D
    ramps multiplied together, no supersampling needed.
    """
    xs = np.arange(W, dtype=np.float32)
    ys = np.arange(H, dtype=np.float32)
    # coverage of pixel i (spanning [i, i+1]) by the interval [x0, x1]
    cov_x = np.clip(np.minimum(xs + 1.0, x1) - np.maximum(xs, x0), 0.0, 1.0)
    cov_y = np.clip(np.minimum(ys + 1.0, y1) - np.maximum(ys, y0), 0.0, 1.0)
    alpha = np.outer(cov_y, cov_x).astype(np.float32)

    img = np.zeros((H, W, 4), np.float32)
    img[..., :3] = ORANGE          # straight alpha: full colour everywhere...
    img[..., 3] = alpha            # ...and the alpha carries the coverage
    # Outside the shape alpha is 0, so the RGB there is irrelevant, but keep it
    # black so a naive viewer does not see a full-frame orange field.
    img[..., :3] *= (alpha > 0)[..., None]
    return img


PALETTE = np.float32([
    [1.00, 0.663, 0.000],    # orange
    [0.90, 0.130, 0.140],    # red
    [0.10, 0.640, 0.330],    # green
    [0.13, 0.380, 0.860],    # blue
])


def make_source_multi(softness=2.0):
    """Harder source: four flat colours abutting, inside one soft-edged
    silhouette.

    The single-rectangle source above is deceptively easy - it has exactly one
    hue, so even a blur cannot produce a NEW hue, only a darker version of the
    one hue. Real flat-colour artwork has colours meeting each other, and that
    is where averaging really invents: the average of red and green is a mud
    that appears nowhere in the source.

    `softness` widens the anti-aliased fringe (a scaled-up layer, a feathered
    mask, a soft shadow). A one-pixel fringe is easy to survive by luck; a
    four-pixel one is not.
    """
    xs = np.arange(W, dtype=np.float32)
    ys = np.arange(H, dtype=np.float32)
    x0, x1, y0, y1 = 96.4, 520.3, 60.7, 248.15
    cov_x = np.clip(np.minimum(xs + 1.0, x1) - np.maximum(xs, x0), 0.0, 1.0)
    cov_y = np.clip(np.minimum(ys + 1.0, y1) - np.maximum(ys, y0), 0.0, 1.0)
    alpha = np.outer(cov_y, cov_x).astype(np.float32)
    if softness > 0:
        k = int(softness * 4) | 1
        alpha = cv2.GaussianBlur(alpha, (k, k), softness)

    # Four vertical bands of flat colour, boundaries at fractional x.
    img = np.zeros((H, W, 4), np.float32)
    edges = np.linspace(x0, x1, len(PALETTE) + 1)
    band = np.zeros((W, 3), np.float32)
    for i, c in enumerate(PALETTE):
        band[int(round(edges[i])):int(round(edges[i + 1]))] = c
    img[..., :3] = band[None, :, :]
    img[..., 3] = alpha
    img[..., :3] *= (alpha > 0)[..., None]
    return img


# --------------------------------------------------------------------------
# The two classic block-colour rules
# --------------------------------------------------------------------------

def block_grid(h, w, bw, bh):
    """Yield (y0, y1, x0, x1) for every block of a bw x bh grid over h x w.

    Blocks are laid out from the top-left origin and the last row/column is
    CLIPPED, not stretched. Keeping the block size exact rather than dividing
    the frame into N equal parts is what makes the result resolution-stable.
    """
    for y0 in range(0, h, bh):
        for x0 in range(0, w, bw):
            yield y0, min(y0 + bh, h), x0, min(x0 + bw, w)


def mosaic_average(img, bw, bh):
    """AE Mosaic's default: every channel averaged over the block."""
    out = np.empty_like(img)
    for y0, y1, x0, x1 in block_grid(img.shape[0], img.shape[1], bw, bh):
        out[y0:y1, x0:x1] = img[y0:y1, x0:x1].reshape(-1, 4).mean(0)
    return out


def mosaic_point(img, bw, bh):
    """AE Mosaic's "Sharp Colors": the pixel at the centre of the block."""
    out = np.empty_like(img)
    for y0, y1, x0, x1 in block_grid(img.shape[0], img.shape[1], bw, bh):
        cy = min((y0 + y1) // 2, img.shape[0] - 1)
        cx = min((x0 + x1) // 2, img.shape[1] - 1)
        out[y0:y1, x0:x1] = img[cy, cx]
    return out


# --------------------------------------------------------------------------
# Measurement + output
# --------------------------------------------------------------------------

def distinct_colours(img, decimals=4):
    """How many different RGBA values does this image contain?"""
    q = np.round(img.reshape(-1, 4), decimals)
    return np.unique(q, axis=0)


def partial_alpha_fraction(img, eps=1e-4):
    """Fraction of pixels whose alpha is neither 0 nor 1 - the thing the user
    is complaining about."""
    a = img[..., 3]
    partial = (a > eps) & (a < 1.0 - eps)
    return float(partial.mean())


def save(path, img, checker=True):
    """Composite straight RGBA over a checkerboard and write a PNG.

    We composite rather than writing the alpha channel out so that partial
    alpha is VISIBLE in the file - a semi-transparent orange block over grey
    checks reads as a washed-out block, which is the whole point.
    """
    rgb = img[..., :3]
    a = img[..., 3:4]
    if checker:
        yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
        chk = np.where(((yy // 8) + (xx // 8)) % 2 == 0, 0.62, 0.48)
        bg = np.repeat(chk[..., None].astype(np.float32), 3, axis=2)
    else:
        bg = np.zeros_like(rgb)
    comp = rgb * a + bg * (1.0 - a)
    bgr = np.clip(comp[..., ::-1] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    cv2.imwrite(path, bgr)


def report(name, img):
    u = distinct_colours(img)
    print(f"  {name:<16} distinct RGBA = {len(u):>5}   "
          f"partial-alpha pixels = {partial_alpha_fraction(img) * 100:6.2f}%")
    return u


if __name__ == "__main__":
    src = make_source()
    BW, BH = 16, 16

    print("Hard Mosaic - step 1")
    print(f"  source {W}x{H}, blocks {BW}x{BH}")
    print()
    print("The source itself:")
    report("source", src)
    print("  (2 solid colours + an anti-aliased fringe; the fringe is correct)")
    print()
    print("Mosaicked:")
    avg = mosaic_average(src, BW, BH)
    pnt = mosaic_point(src, BW, BH)
    u_avg = report("box average", avg)
    u_pnt = report("point sample", pnt)
    print()
    print("Every distinct colour the POINT sampler produced:")
    for c in u_pnt:
        print(f"    rgba = {c}")
    print()
    print("Point sampling across block sizes - the lottery:")
    print("    block   distinct RGBA   partial-alpha pixels")
    for b in (2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64):
        p = mosaic_point(src, b, b)
        print(f"    {b:>5}   {len(distinct_colours(p)):>13}   "
              f"{partial_alpha_fraction(p) * 100:>18.2f}%")
    print()
    print("Reading of the above")
    print("  - box average invents colours: every edge block is a blend of")
    print("    orange and nothing, so it is both a darker orange AND partly")
    print("    transparent. It fails at every block size.")
    print("  - point sample invents no COLOUR at all - note that even when it")
    print("    misbehaves the RGB stays exactly (1, 0.663, 0). That is the")
    print("    straight-alpha property: a half-covered edge pixel still")
    print("    carries the shape's full colour, and only its alpha is partial.")
    print("    So every extra 'distinct RGBA' above 2 is an ALPHA, never a hue.")
    print("  - but it fails unpredictably: clean at 48, 5.33% partial at 64.")
    print()
    print("  Step 2 fixes the alpha, and does it without throwing away the")
    print("  sub-pixel information that decides WHICH blocks survive.")

    save(f"{OUT}/out_hm1_source.png", src)
    save(f"{OUT}/out_hm1_average.png", avg)
    save(f"{OUT}/out_hm1_point.png", pnt)
    print(f"\n  wrote out_hm1_source.png / _average.png / _point.png to {OUT}")

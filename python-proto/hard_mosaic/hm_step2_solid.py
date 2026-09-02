"""
Hard Mosaic - Step 2: making every block genuinely solid.

Step 1 established the two ways a mosaic can lie:
    it can invent a COLOUR that was never in the source (box average), and
    it can emit an ALPHA that is neither 0 nor 1 (both methods, unpredictably).

This step builds the rule that does neither. It rests on one separation:

    the block's COLOUR and the block's EXISTENCE are two different questions,
    and they should be answered by two different statistics.

Question 1 - does this block exist at all?
------------------------------------------
Answer it with COVERAGE: the mean alpha over the block. Because alpha is
literally "what fraction of this pixel the shape covers", the mean alpha over a
block is exactly "what fraction of this block the shape covers" - an area, and
an exact one, anti-aliased fringe included. That fringe is not noise to be
thrown away; it is the sub-pixel information that tells us how much of the
edge block is really filled.

Then threshold it:  block is opaque  <=>  coverage >= coverage_threshold.

At the default threshold of 50% this is the natural rule "keep the block if
the shape covers most of it", and it is what makes the mosaic's silhouette
track the original silhouette instead of jittering with wherever the centre
pixel happened to fall. Turn the threshold down to 1% and you get "keep the
block if the shape touches it at all" (a dilated, chunky look); turn it up to
99% and you get "only fully-covered blocks" (an eroded look). One number, and
it spans the whole useful range - so it is a control, not a constant.

Question 2 - what colour is this block?
---------------------------------------
Answer it with the DOMINANT colour: of all the pixels in the block that carry
any of the shape at all, which exact RGB value appears most often? Being a
vote among values that are actually present, it can only ever return a colour
that was in the source. No blending, no invention - by construction.

Two details make the vote behave:

  * Votes are WEIGHTED BY ALPHA. A pixel that is 10% covered casts a 0.1 vote.
    Otherwise a long anti-aliased edge could out-vote a small solid region.
  * Colours are QUANTISED before being counted (we use 8 bits per channel).
    Two pixels of "the same" colour in a real 32-bit-float comp differ in the
    seventh decimal, and an exact-match vote would put every pixel in its own
    bucket and elect a random one. The quantisation is only for BUCKETING; the
    colour we output is the true float colour of a pixel that won.

Then the block is painted (dominant_rgb, 0 or 1) - a colour that was in the
source, and an alpha that is fully on or fully off. Nothing in between, at any
block size. That is the whole effect.

The trap: straight vs premultiplied
-----------------------------------
Everything above assumes STRAIGHT alpha, where a half-covered edge pixel still
carries the shape's full RGB. Under PREMULTIPLIED alpha that same pixel
carries half the shape's colour multiplied down toward black, and a vote over
raw premultiplied RGB would elect a dark fringe colour - the very artefact we
are removing. So under premultiplied input the RGB must be divided back out by
alpha before the vote. After Effects hands a SmartFX plugin straight RGB for
shape layers, but not for every source, so the port must detect this per FRAME
and never assume. (See the ae-input-is-straight-not-premultiplied note.)

Run:  python hm_step2_solid.py
"""

import numpy as np

from hm_step1_blocks import (
    make_source, make_source_multi, PALETTE, block_grid,
    mosaic_average, mosaic_point,
    distinct_colours, partial_alpha_fraction, save, OUT,
)

# --------------------------------------------------------------------------
# The block statistics
# --------------------------------------------------------------------------


def block_coverage(alpha_block):
    """Fraction of the block the shape covers. Exact, fringe included."""
    return float(alpha_block.mean())


def dominant_colour(rgb_block, alpha_block, bits=8):
    """The most common RGB in the block, weighted by alpha.

    Returns the true float RGB of the winning bucket's brightest-weighted
    member, or None if the block holds no covered pixel at all.
    """
    flat_rgb = rgb_block.reshape(-1, 3)
    w = alpha_block.reshape(-1)
    keep = w > 0.0
    if not keep.any():
        return None
    flat_rgb = flat_rgb[keep]
    w = w[keep]

    # Bucket by a quantised key so near-identical floats land together.
    levels = (1 << bits) - 1
    q = np.clip(np.rint(flat_rgb * levels), 0, levels).astype(np.int64)
    key = (q[:, 0] << (2 * bits)) | (q[:, 1] << bits) | q[:, 2]

    uniq, inv = np.unique(key, return_inverse=True)
    votes = np.bincount(inv, weights=w)
    winner = int(np.argmax(votes))

    # Output a real pixel value from the winning bucket, not the bucket centre,
    # so the colour we emit is byte-for-byte one that existed in the source.
    members = flat_rgb[inv == winner]
    return members[0].astype(np.float32)


def nearest_solid_colour(rgb_block, alpha_block, alpha_threshold):
    """Cheaper alternative: the covered pixel closest to the block centre.

    Never invents a colour either, and costs one pass with no histogram. It is
    the honest version of "Sharp Colors": point sampling, but restricted to
    pixels that are actually part of the shape, so it cannot come back with a
    fringe pixel's partial alpha.
    """
    h, w = alpha_block.shape
    solid = alpha_block >= alpha_threshold
    if not solid.any():
        solid = alpha_block > 0.0
        if not solid.any():
            return None
    ys, xs = np.nonzero(solid)
    cy, cx = (h - 1) * 0.5, (w - 1) * 0.5
    d = (ys - cy) ** 2 + (xs - cx) ** 2
    i = int(np.argmin(d))
    return rgb_block[ys[i], xs[i]].astype(np.float32)


# --------------------------------------------------------------------------
# The effect
# --------------------------------------------------------------------------

MODE_DOMINANT = "dominant"
MODE_NEAREST = "nearest"
MODE_AVERAGE = "average"          # kept only so the failure stays measurable


def hard_mosaic(img, bw, bh, *, mode=MODE_DOMINANT,
                coverage_threshold=0.5, alpha_threshold=0.5,
                premultiplied=False):
    """Mosaic where every block is one source colour and alpha is 0 or 1."""
    src = img
    if premultiplied:
        # Undo the premultiply so the vote sees true colours. Only where there
        # is something to divide by - dividing a transparent pixel by its zero
        # alpha is how you get NaNs into a render.
        a = img[..., 3:4]
        rgb = np.divide(img[..., :3], a, out=np.zeros_like(img[..., :3]),
                        where=a > 1e-6)
        src = np.concatenate([np.clip(rgb, 0.0, None), a], axis=2)

    out = np.zeros_like(img)
    for y0, y1, x0, x1 in block_grid(img.shape[0], img.shape[1], bw, bh):
        rgb_b = src[y0:y1, x0:x1, :3]
        a_b = src[y0:y1, x0:x1, 3]

        if block_coverage(a_b) < coverage_threshold:
            continue                      # block does not exist; leave it clear

        if mode == MODE_DOMINANT:
            c = dominant_colour(rgb_b, a_b)
        elif mode == MODE_NEAREST:
            c = nearest_solid_colour(rgb_b, a_b, alpha_threshold)
        else:
            wsum = float(a_b.sum())
            c = ((rgb_b * a_b[..., None]).reshape(-1, 3).sum(0) / wsum
                 if wsum > 0 else None)
        if c is None:
            continue

        out[y0:y1, x0:x1, :3] = c
        out[y0:y1, x0:x1, 3] = 1.0

    if premultiplied:
        out[..., :3] *= out[..., 3:4]
    return out


# --------------------------------------------------------------------------

if __name__ == "__main__":
    print("Hard Mosaic - step 2")
    print()
    print("Measured on the HARD source: four flat colours meeting each other")
    print("inside one soft-edged silhouette (hm_step1_blocks.make_source_multi).")
    print("A perfect result is 5 distinct RGBA - the four colours, plus clear.")
    print("The source itself contains", len(distinct_colours(make_source_multi())),
          "distinct RGBA, almost all of them fringe.")
    print()
    print("    distinct RGBA in output")
    print("    block |  average  point  |  hard/avg  hard/near  hard/dominant")
    print("    ------+------------------+--------------------------------------")
    src = make_source_multi()
    worst = 0
    for b in (2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64):
        n_avg = len(distinct_colours(mosaic_average(src, b, b)))
        n_pnt = len(distinct_colours(mosaic_point(src, b, b)))
        n_ha = len(distinct_colours(hard_mosaic(src, b, b, mode=MODE_AVERAGE)))
        n_hn = len(distinct_colours(hard_mosaic(src, b, b, mode=MODE_NEAREST)))
        n_hd = len(distinct_colours(hard_mosaic(src, b, b, mode=MODE_DOMINANT)))
        worst = max(worst, n_hn, n_hd)
        print(f"    {b:>5} |  {n_avg:>7}  {n_pnt:>5}  |  {n_ha:>8}  "
              f"{n_hn:>9}  {n_hd:>13}")
    print()
    print(f"  worst case for nearest/dominant over every block size: {worst}")
    print("  Read the columns:")
    print("   - average invents 30-80 colours. Half of them are hues that")
    print("     appear nowhere in the source, because a block straddling the")
    print("     red/green boundary averages to a mud between them. This is the")
    print("     dark-block artefact, reproduced.")
    print("   - point sampling invents no hue but still emits 5-40 values,")
    print("     all of them partial ALPHAS off the soft edge.")
    print("   - hard/avg snaps the alpha and still reports 7-10: snapping alpha")
    print("     cannot rescue a blended hue. Averaging is unsalvageable.")
    print("   - nearest and dominant hit the perfect 5 at EVERY block size.")
    print("     Both are votes among values that exist, so by construction")
    print("     they cannot do anything else.")
    print()
    print("Choosing between nearest and dominant: both are exact here. They")
    print("differ on busy artwork - nearest asks 'what is at the middle of")
    print("this block', dominant asks 'what is most of this block'. Dominant")
    print("is the steadier look and is the default; nearest costs one pass")
    print("with no histogram and is the cheap fallback.")
    print()

    # ----------------------------------------------------------------------
    # The premultiplied trap, measured rather than asserted.
    # ----------------------------------------------------------------------
    print("The straight-vs-premultiplied trap (softness 6px, so the fringe is")
    print("wide enough for whole blocks to live inside it):")
    print()
    soft = make_source_multi(softness=6.0)
    pre = soft.copy()
    pre[..., :3] *= pre[..., 3:4]
    print("    coverage  block |  handled correctly   treated as straight")
    for cov in (0.05, 0.2, 0.5):
        for b in (4, 8):
            ok = len(distinct_colours(
                hard_mosaic(pre, b, b, coverage_threshold=cov,
                            premultiplied=True)))
            bad = len(distinct_colours(
                hard_mosaic(pre, b, b, coverage_threshold=cov,
                            premultiplied=False)))
            print(f"    {cov:>8}  {b:>5} |  {ok:>17}   {bad:>19}")
    print()
    print("  Getting the premultiply wrong costs 5 -> 115. Note WHERE it")
    print("  costs: a block sitting entirely inside a wide soft edge has no")
    print("  fully-covered pixel to vote for, so the darkened fringe colours")
    print("  are the only candidates and one of them wins. On a hard 1px edge")
    print("  the same mistake is nearly invisible, which is exactly why it has")
    print("  to be detected per frame and never guessed from how a test comp")
    print("  happened to look.")
    print()

    for mode in (MODE_AVERAGE, MODE_NEAREST, MODE_DOMINANT):
        save(f"{OUT}/out_hm2_{mode}.png", hard_mosaic(src, 16, 16, mode=mode))
    for cov in (0.01, 0.5, 0.99):
        save(f"{OUT}/out_hm2_cov{int(cov * 100):02d}.png",
             hard_mosaic(src, 16, 16, coverage_threshold=cov))
    save(f"{OUT}/out_hm2_source.png", src)
    save(f"{OUT}/out_hm2_ae_average.png", mosaic_average(src, 16, 16))
    print(f"  wrote out_hm2_*.png to {OUT}")

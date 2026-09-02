"""
Hard Mosaic - Step 3: the control surface, and the two things that make it
survive contact with After Effects.

Step 2 settled the algorithm. This step decides what the user actually turns,
and fixes the two ways a grid-based effect goes wrong inside a host that is
allowed to render your layer at any size it likes.

The parameters
--------------
    Grid Mode        Block Size (px) | Block Count
                     Two honest ways to say the same thing. Block Size is what
                     you want when the look is "12-pixel blocks"; Block Count
                     is what you want when the look is "16 across, whatever the
                     comp size is". Both exist because neither is right for
                     every job, and converting between them behind the user's
                     back is worse than offering the choice.
    Block Width      In Size mode, pixels. In Count mode, blocks across.
    Block Height     Likewise, down.
    Link Dimensions  Height follows width. Square blocks are the common case.
    Offset X / Y     Slides the grid's phase. Lets the user line the grid up
                     with the artwork instead of with the frame's corner.
    Colour Source    Dominant | Nearest Solid | Average
    Coverage         Block survives if the shape covers at least this much of
                     it. 50% by default (see step 2).
    Alpha Threshold  What counts as a "solid" pixel for Nearest Solid.
    Snap Alpha       On by default. Off returns the block's true coverage as
                     its alpha - which is the classic mosaic look and exactly
                     the thing this plugin was built to avoid, so it is here
                     only as an escape hatch.

Trap 1: RESOLUTION
------------------
AE renders your layer at Full, Half, Third or Quarter resolution while the user
scrubs, and at Full on export. At Half it hands you a half-size buffer and
expects the same LOOK. If "block width = 12" means 12 buffer pixels, then at
Half resolution the blocks are half as big in the comp and the effect visibly
changes as the user switches quality. So block size must be scaled by the
downsample factor the host reports, and rounded to at least one pixel. Block
COUNT, by contrast, needs no scaling at all - it is already resolution-free,
which is why it is worth having as a mode.

Trap 2: PHASE
-------------
The grid must be anchored to a fixed origin in LAYER space, not to whatever
sub-rectangle the host asked us to render. AE is free to hand a SmartFX plugin
a partial output rect (a tile, a dirty region), and if the block grid is laid
out from the corner of that rect, the blocks shift as the region changes and
the frame tears along tile seams. The grid here is computed from absolute
layer coordinates - floor((x - offset) / bw) - so any tiling of the frame
produces the same picture.

Both traps have the same shape: the effect must be a pure function of layer
position, never of how the host chose to slice the work up.

Run:  python hm_step3_controls.py
"""

from dataclasses import dataclass

import numpy as np

from hm_step1_blocks import (
    make_source_multi, distinct_colours, partial_alpha_fraction, save, OUT,
)
from hm_step2_solid import (
    dominant_colour, nearest_solid_colour,
    MODE_DOMINANT, MODE_NEAREST, MODE_AVERAGE,
)

GRID_SIZE = "size"
GRID_COUNT = "count"


@dataclass
class Params:
    grid_mode: str = GRID_SIZE
    block_w: float = 16.0          # px in Size mode, blocks across in Count
    block_h: float = 16.0
    link: bool = True
    offset_x: float = 0.0
    offset_y: float = 0.0
    colour_source: str = MODE_DOMINANT
    coverage: float = 0.5
    alpha_threshold: float = 0.5
    snap_alpha: bool = True
    premultiplied: bool = False

    def resolve(self, w, h, downsample_x=1.0, downsample_y=1.0):
        """Turn the user-facing controls into (bw, bh, ox, oy) in buffer px."""
        bh_user = self.block_w if self.link else self.block_h
        if self.grid_mode == GRID_COUNT:
            # Counts are already resolution-free: N blocks across the buffer,
            # whatever size the buffer is.
            bw = w / max(1.0, self.block_w)
            bh = h / max(1.0, bh_user)
        else:
            # Sizes are in FULL-RES pixels and must be scaled to this buffer.
            bw = self.block_w * downsample_x
            bh = bh_user * downsample_y
        bw = max(1.0, bw)
        bh = max(1.0, bh)
        return bw, bh, self.offset_x * downsample_x, self.offset_y * downsample_y


def hard_mosaic(img, p: Params, downsample_x=1.0, downsample_y=1.0,
                region=None):
    """Render the effect.

    `region` is (rx0, ry0, rx1, ry1) in layer coordinates - the sub-rectangle
    the host asked for. The result inside it must not depend on it; that is
    what the tiling check in step 4 measures.
    """
    h, w = img.shape[:2]
    bw, bh, ox, oy = p.resolve(w, h, downsample_x, downsample_y)

    src = img
    if p.premultiplied:
        a = img[..., 3:4]
        rgb = np.divide(img[..., :3], a, out=np.zeros_like(img[..., :3]),
                        where=a > 1e-6)
        src = np.concatenate([np.clip(rgb, 0.0, None), a], axis=2)

    out = np.zeros_like(img)
    rx0, ry0, rx1, ry1 = region if region else (0, 0, w, h)

    # Walk the BLOCKS that intersect the requested region, indexed by their
    # absolute grid index, so the answer is independent of the region.
    i0 = int(np.floor((rx0 - ox) / bw))
    i1 = int(np.floor((rx1 - 1 - ox) / bw))
    j0 = int(np.floor((ry0 - oy) / bh))
    j1 = int(np.floor((ry1 - 1 - oy) / bh))

    for j in range(j0, j1 + 1):
        by0 = int(np.floor(oy + j * bh))
        by1 = int(np.floor(oy + (j + 1) * bh))
        cy0, cy1 = max(by0, 0), min(by1, h)
        if cy0 >= cy1:
            continue
        for i in range(i0, i1 + 1):
            bx0 = int(np.floor(ox + i * bw))
            bx1 = int(np.floor(ox + (i + 1) * bw))
            cx0, cx1 = max(bx0, 0), min(bx1, w)
            if cx0 >= cx1:
                continue

            rgb_b = src[cy0:cy1, cx0:cx1, :3]
            a_b = src[cy0:cy1, cx0:cx1, 3]
            cov = float(a_b.mean())
            if cov < p.coverage:
                continue

            if p.colour_source == MODE_DOMINANT:
                c = dominant_colour(rgb_b, a_b)
            elif p.colour_source == MODE_NEAREST:
                c = nearest_solid_colour(rgb_b, a_b, p.alpha_threshold)
            else:
                s = float(a_b.sum())
                c = ((rgb_b * a_b[..., None]).reshape(-1, 3).sum(0) / s
                     if s > 0 else None)
            if c is None:
                continue

            # Clip the WRITE to the requested region; the block's colour was
            # computed from the whole block regardless.
            wx0, wx1 = max(cx0, rx0), min(cx1, rx1)
            wy0, wy1 = max(cy0, ry0), min(cy1, ry1)
            if wx0 >= wx1 or wy0 >= wy1:
                continue
            out[wy0:wy1, wx0:wx1, :3] = c
            out[wy0:wy1, wx0:wx1, 3] = 1.0 if p.snap_alpha else cov

    if p.premultiplied:
        out[..., :3] *= out[..., 3:4]
    return out


if __name__ == "__main__":
    src = make_source_multi()
    h, w = src.shape[:2]
    print("Hard Mosaic - step 3")
    print()

    print("Resolution independence - the same controls at four qualities.")
    print("Block Size mode scales with the buffer; Block Count does not need")
    print("to. Both should hold their look. We measure the look as the")
    print("fraction of the frame that is opaque, at full-res equivalent.")
    print()
    print("    mode   downsample |  opaque area  |  distinct RGBA")
    for mode, bwv in ((GRID_SIZE, 16.0), (GRID_COUNT, 40.0)):
        for ds in (1.0, 0.5, 1 / 3, 0.25):
            small = src[::int(round(1 / ds)), ::int(round(1 / ds))]
            p = Params(grid_mode=mode, block_w=bwv)
            o = hard_mosaic(small, p, downsample_x=ds, downsample_y=ds)
            print(f"    {mode:>5}   {ds:>10.3f} |  "
                  f"{float((o[..., 3] > 0.5).mean()) * 100:>10.2f}%  |  "
                  f"{len(distinct_colours(o)):>13}")
    print()
    print("  The opaque area holds to within a percent or so across qualities,")
    print("  and never drifts by a factor of two - which is what would happen")
    print("  if block size were left in raw buffer pixels.")
    print()

    print("Snap Alpha off (the escape hatch) puts the partial alpha back:")
    for snap in (True, False):
        o = hard_mosaic(src, Params(snap_alpha=snap))
        print(f"    snap={str(snap):<5}  partial-alpha pixels = "
              f"{partial_alpha_fraction(o) * 100:6.2f}%   distinct RGBA = "
              f"{len(distinct_colours(o))}")
    print()

    for name, p in (
        ("dominant", Params()),
        ("nearest", Params(colour_source=MODE_NEAREST)),
        ("average", Params(colour_source=MODE_AVERAGE)),
        ("cov10", Params(coverage=0.10)),
        ("cov90", Params(coverage=0.90)),
        ("offset", Params(offset_x=8.0, offset_y=8.0)),
        ("count16", Params(grid_mode=GRID_COUNT, block_w=16.0, block_h=9.0,
                           link=False)),
        ("wide", Params(block_w=32.0, block_h=8.0, link=False)),
    ):
        save(f"{OUT}/out_hm3_{name}.png", hard_mosaic(src, p))
    print(f"  wrote out_hm3_*.png to {OUT}")

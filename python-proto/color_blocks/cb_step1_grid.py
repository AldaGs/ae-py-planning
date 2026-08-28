r"""
Colour Blocks - Step 1: THE GRID, THE WIPE, AND WHY NOTHING IS RASTERIZED.

A generator effect, not an image-processing one: there is no input image. The
output is a field of solid rectangles - 5 colours over a background - that wipe
in left-to-right and then wipe out left-to-right.

Three ideas, in the order they matter:

    1. LAYOUT      rows of random height, each a run of blocks of random width
    2. WIPE        two sweeps crossing the frame; the gap between them is what
                   you see. Reveal from a block's left edge, erase from the same
                   left edge, and per-block stagger falls out for free.
    3. COVERAGE    every edge lands on a fractional pixel. We never rasterize.
                   Each pixel gets the EXACT area of the rect that covers it.

IDEA 3 IS THE WHOLE POINT, so it goes first.
--------------------------------------------
A block's left edge is at x = 137.4, and next frame it is at 139.85. Three ways
to put that on a pixel grid:

    snap to integer     edge jumps 137 -> 140. The wipe judders, and a randomized
                        block width quantises to whole pixels.
    supersample         sample the rect 4x4 per pixel. Edge is quantised to 1/4
                        pixel, still judders, and costs 16x.
    analytic coverage   ask "what fraction of THIS pixel's square does the rect
                        cover?" and answer it in closed form.

For an axis-aligned rectangle the answer is exact and separable:

    cov(px,py) = overlap_1d(x0,x1, px,px+1) * overlap_1d(y0,y1, py,py+1)

    overlap_1d(a,b, c,d) = max(0, min(b,d) - max(a,c))

That is not an approximation of anti-aliasing - it IS the pixel's true integral,
because the shape is a box and the pixel is a box. A vertical edge produces a
single column of partial pixels with identical values top to bottom, so the edge
reads as a perfectly straight line, and it slides continuously as x0 moves by
hundredths of a pixel. Exactly the "straight and clean" the brief asks for.

The one subtlety: coverage is a LIGHT quantity, so the blend has to happen in
linear light. A 50%-covered block edge between black and saturated red is a
pixel that received half the red photons - that is 0.5 in linear, which is
~0.73 in sRGB, not 0.5. Blending coverage in sRGB (what naive code does) makes
every edge in the frame read too dark. We composite linear, encode at the end.

    python cb_step1_grid.py            # the still, plus a wipe strip
    python cb_step1_grid.py --explain  # coverage evidence: zooms + a slide test
"""

import argparse
import numpy as np
from PIL import Image

# ---------------------------------------------------------------- colour utils

def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, dtype=np.float64), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def hex_to_linear(h):
    h = h.lstrip("#")
    srgb = np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])
    return srgb_to_linear(srgb)


# The palette sampled off the reference image: yellow, red, green, blue + black.
PALETTE = ["#F0BC2C", "#EE3B24", "#2A5A28", "#4A5FC1", "#000000"]
BACKGROUND = "#000000"


# ---------------------------------------------------------------------- layout

class Block:
    """One rectangle in the layout, in continuous (sub-pixel) coordinates."""

    # `phase` is a stable per-block random number in [0,1), unused in step 1 and
    # used by step 2's time jitter. It lives here so both steps share one Block.
    __slots__ = ("x0", "x1", "y0", "y1", "colour", "phase")

    def __init__(self, x0, x1, y0, y1, colour, phase=0.0):
        self.x0, self.x1, self.y0, self.y1, self.colour = x0, x1, y0, y1, colour
        self.phase = phase


def jitter(rng, base, amount):
    """Scale `base` by a random multiplier controlled by `amount` in 0..1.

    amount=0 -> exactly base (a perfectly regular grid)
    amount=1 -> base * U[0.25, 4.0], i.e. a 16:1 spread of sizes

    The multiplier is drawn in LOG space so that 'half as wide' and 'twice as
    wide' are equally likely. Drawn linearly, a uniform multiplier in [0.25,4]
    would produce mostly-wide blocks, because 3 of the 4 units of that range sit
    above 1.0. The reference image has that log-ish feel: lots of thin slivers
    sitting next to a few very wide slabs.
    """
    if amount <= 0.0:
        return base
    spread = np.log(4.0) * amount
    return base * float(np.exp(rng.uniform(-spread, spread)))


def build_layout(width, height, cell_w, cell_h, w_rand, h_rand,
                 gap_chance, n_colours, seed):
    """Fill the frame with rows of blocks. Returns a list of Block."""
    rng = np.random.default_rng(seed)
    blocks = []
    y = 0.0
    while y < height:
        row_h = max(1.0, jitter(rng, cell_h, h_rand))
        x = 0.0
        while x < width:
            w = max(1.0, jitter(rng, cell_w, w_rand))
            # A block is either one of the palette colours or a background gap.
            # The gaps are not decoration - they are what makes the layout read
            # as blocks rather than as a solid quilt.
            if rng.random() >= gap_chance:
                blocks.append(Block(x, x + w, y, y + row_h,
                                    int(rng.integers(0, n_colours))))
            x += w
        y += row_h
    return blocks


# -------------------------------------------------------------------- the wipe

def wipe_window(block, t, wipe_len, hold):
    r"""Return the block's currently-visible x-range, or None.

    Two sweep lines cross the frame left to right. `lead` reveals, `trail`
    erases, and each block shows only the part of itself between them:

        visible = [max(x0, trail), min(x1, lead)]

    Because each block clips the same two global lines against its own extent,
    a block only starts moving once `lead` reaches ITS left edge - so blocks on
    the left animate first and blocks on the right animate later, with no
    per-block timing data anywhere. The stagger is a consequence of the
    geometry, not a thing we author.

    `wipe_len` is how far past the frame the sweeps travel, in x units; `hold`
    is the normalised time at which the trailing sweep sets off.
    """
    lead = t / max(hold, 1e-6) * wipe_len
    trail = (t - hold) / max(1.0 - hold, 1e-6) * wipe_len

    left = max(block.x0, trail)
    right = min(block.x1, lead)
    if right <= left:
        return None
    return left, right


# ------------------------------------------------------------------- rendering

def coverage_span(a, b, n):
    """1-D exact coverage of the interval [a,b] over n unit pixel cells.

    Cell i spans [i, i+1). Its coverage is the length of the overlap:

        max(0, min(b, i+1) - max(a, i))

    Interior cells give 1.0, the two boundary cells give the fractional part,
    everything else gives 0. This is the entire anti-aliasing story.
    """
    idx = np.arange(n, dtype=np.float64)
    return np.clip(np.minimum(b, idx + 1.0) - np.maximum(a, idx), 0.0, 1.0)


def render(width, height, blocks, t, wipe_len, hold,
           palette_lin, bg_lin, snap=False):
    """Composite every block into a linear-light RGB buffer."""
    img = np.repeat(np.asarray(bg_lin)[None, None, :], height, axis=0)
    img = np.repeat(img, width, axis=1).copy()

    for b in blocks:
        win = wipe_window(b, t, wipe_len, hold)
        if win is None:
            continue
        x0, x1 = win
        y0, y1 = b.y0, b.y1

        if snap:
            # The control case: quantise every edge to whole pixels. Kept so the
            # explain pass can show what we are avoiding.
            x0, x1 = round(x0), round(x1)
            y0, y1 = round(y0), round(y1)
            if x1 <= x0 or y1 <= y0:
                continue

        # Only touch the pixels the rect can possibly reach.
        ix0, ix1 = int(np.floor(x0)), min(int(np.ceil(x1)), width)
        iy0, iy1 = int(np.floor(y0)), min(int(np.ceil(y1)), height)
        ix0, iy0 = max(ix0, 0), max(iy0, 0)
        if ix1 <= ix0 or iy1 <= iy0:
            continue

        cx = coverage_span(x0 - ix0, x1 - ix0, ix1 - ix0)
        cy = coverage_span(y0 - iy0, y1 - iy0, iy1 - iy0)
        cov = cy[:, None] * cx[None, :]          # separable: the outer product

        tile = img[iy0:iy1, ix0:ix1, :]
        colour = palette_lin[b.colour]
        # Straight 'over' in LINEAR light. Doing this in sRGB is the classic
        # edge-too-dark bug.
        img[iy0:iy1, ix0:ix1, :] = tile + cov[:, :, None] * (colour - tile)

    return img


def render_static(width, height, blocks, palette_lin, bg_lin, snap=False):
    """The layout with no wipe applied - every block fully drawn.

    Not the same as render(t=huge): at large t BOTH sweeps have left the frame,
    so the trailing one has erased everything and the result is empty. `hold` is
    exactly the instant the lead has finished and the trail has not started, so
    that is the time to ask for if you want the finished layout.
    """
    return render(width, height, blocks, 1.0, 1e9, 1.0,
                  palette_lin, bg_lin, snap=snap)


def save(img_lin, path):
    out = (linear_to_srgb(img_lin) * 255.0 + 0.5).astype(np.uint8)
    Image.fromarray(out).save(path)
    print(f"  wrote {path}")


# ---------------------------------------------------------------------- explain

def explain(args, blocks, palette_lin, bg_lin):
    """Evidence that the coverage path does what the docstring claims."""
    W, H = args.width, args.height

    # 1. A zoom on a block corner, analytic vs snapped, blown up with nearest
    #    neighbour so individual pixels are visible.
    for tag, snap in (("cov", False), ("snap", True)):
        img = render(W, H, blocks, 0.45, args.wipe_len, args.hold,
                     palette_lin, bg_lin, snap=snap)
        crop = linear_to_srgb(img[80:140, 120:240])
        big = (crop * 255 + 0.5).astype(np.uint8)
        Image.fromarray(big).resize((120 * 6, 60 * 6), Image.NEAREST).save(
            f"explain_zoom_{tag}.png")
        print(f"  wrote explain_zoom_{tag}.png")

    # 2. THE SLIDE TEST. One block, moved right by 1/8 pixel at a time, and we
    #    ask where the rendered block actually IS - its horizontal centroid.
    #
    #    The measurement matters here. The obvious one, total mass, does not
    #    discriminate: snapping a rigid 40px-wide block rounds both edges the
    #    same way, so the block stays 40 wide and mass is conserved in both
    #    paths. Snapping does not destroy the block, it MISPLACES it, so the
    #    thing to measure is position. Under analytic coverage the centroid
    #    should track the offset exactly (error 0); under snapping it staircases
    #    and the error swings up to half a pixel. That error, frame over frame,
    #    is the judder.
    print("\n  slide test - one 40x20 block stepped right by 1/8 px.")
    print("  centroid error vs the true sub-pixel position:")
    print("    offset   analytic-err   snapped-err")
    xs = np.arange(100) + 0.5
    for k in range(9):
        off = k / 8.0
        one = [Block(20.0 + off, 60.0 + off, 10.0, 30.0, 0)]
        truth = 40.0 + off                      # centre of [20+off, 60+off]
        row = []
        for snap in (False, True):
            img = render_static(100, 40, one, palette_lin, bg_lin, snap=snap)
            cov = img[..., 0].sum(axis=0) / palette_lin[0][0]
            row.append((cov * xs).sum() / cov.sum() - truth)
        print(f"    {off:5.3f}   {row[0]:+12.6f}   {row[1]:+11.6f}")
    print("    (analytic should be 0 to float precision; snapped should reach"
          " +/-0.5)")

    # 3. Edge straightness: a vertical edge must be one constant column.
    one = [Block(20.37, 60.37, 5.0, 35.0, 0)]
    a = render_static(100, 40, one, palette_lin, bg_lin, snap=False)
    col = a[10:30, 20, 0] / palette_lin[0][0]
    print(f"\n  left-edge column, rows 10..30: min={col.min():.6f} "
          f"max={col.max():.6f} spread={col.max() - col.min():.2e}")
    print("    (a straight edge means spread == 0; expected value 0.63 = 1-0.37)")


# ------------------------------------------------------------------------ main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--width", type=int, default=1000)
    p.add_argument("--height", type=int, default=340)
    p.add_argument("--cell-w", type=float, default=62.0)
    p.add_argument("--cell-h", type=float, default=58.0)
    p.add_argument("--w-rand", type=float, default=0.95)
    p.add_argument("--h-rand", type=float, default=0.18)
    p.add_argument("--gap", type=float, default=0.20)
    p.add_argument("--hold", type=float, default=0.55)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--explain", action="store_true")
    args = p.parse_args()

    palette_lin = np.array([hex_to_linear(h) for h in PALETTE])
    bg_lin = hex_to_linear(BACKGROUND)
    # The sweeps must clear the frame plus the widest possible block.
    args.wipe_len = args.width + args.cell_w * 4.0

    blocks = build_layout(args.width, args.height, args.cell_w, args.cell_h,
                          args.w_rand, args.h_rand, args.gap,
                          len(PALETTE), args.seed)
    print(f"layout: {len(blocks)} blocks")

    save(render_static(args.width, args.height, blocks, palette_lin, bg_lin),
         "out_cb1_still.png")

    for i, t in enumerate([0.15, 0.35, 0.55, 0.75, 0.95]):
        save(render(args.width, args.height, blocks, t, args.wipe_len,
                    args.hold, palette_lin, bg_lin),
             f"out_cb1_wipe{i}_t{t:.2f}.png")

    if args.explain:
        explain(args, blocks, palette_lin, bg_lin)


if __name__ == "__main__":
    main()

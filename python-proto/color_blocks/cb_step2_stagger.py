r"""
Colour Blocks - Step 2: STAGGER, AND A PALETTE THAT ISN'T WELDED TO BLACK.

Step 1's wipe is one hard vertical line crossing the frame: every block is cut
at the same x, because every block clips the same global sweep. Graphic and
precise, but only one look. Step 2 adds the control that opens it up, and
splits the background out of the palette.

    1. STAGGER      each block gets its own reveal DURATION, not just its own
                    start. s=0 reproduces step 1 exactly; s=1 gives a ragged
                    front where blocks overlap in time.
    2. TIME JITTER  a per-block random shove in time, on top of stagger.
    3. PALETTE      5 free colour slots + an independent Background colour.

THE STAGGER TRICK
-----------------
In step 1 a block's visible right edge was `min(x1, lead)` - the global sweep
line, clipped. Write that as a per-block local progress instead:

    start(b) = x0 / L * hold          when the sweep arrives at this block
    d0(b)    = (x1 - x0) / L * hold   how long the sweep takes to cross it
    p(b,t)   = clamp((t - start) / d0)
    right    = x0 + (x1 - x0) * p

That is ALGEBRAICALLY IDENTICAL to step 1 - substitute and the x0 cancels. But
now the duration `d0` is a number sitting on its own, and we can change it
without touching the start time:

    d = lerp(d0, block_dur, stagger)

At stagger=0, d == d0 and every block finishes exactly as the sweep passes it:
the fronts of adjacent blocks line up into one straight edge. At stagger=1 every
block takes the SAME time `block_dur` regardless of how wide it is, so a wide
slab reveals slowly while the sliver next to it snaps in - the fronts no longer
line up and the edge breaks into a ragged, overlapping cascade.

The important property: start times are untouched at every stagger value, so the
overall left-to-right march is preserved. Stagger changes the texture of the
front, never its direction or its timing envelope.

This applies to the wipe-OUT identically, with its own start line, which is what
keeps "in from the left, out from the left" true at any stagger.

    python cb_step2_stagger.py            # a stagger ladder, in and out
    python cb_step2_stagger.py --explain  # proves s=0 == step 1, bit for bit
"""

import argparse
import numpy as np
from PIL import Image

from cb_step1_grid import (Block, coverage_span, hex_to_linear, jitter,
                           linear_to_srgb, save)
import cb_step1_grid as step1


# 5 free slots. The reference image's fifth "colour" really is black, so that is
# the default - but it is now a slot like any other, and the ground it sits on
# is a separate control.
PALETTE = ["#F0BC2C", "#EE3B24", "#2A5A28", "#4A5FC1", "#000000"]
BACKGROUND = "#000000"


# ---------------------------------------------------------------------- layout

def build_layout(width, height, cell_w, cell_h, w_rand, h_rand,
                 gap_chance, n_colours, seed):
    """As step 1, but each block also carries a random phase in [0,1).

    The phase is drawn at layout time and stored, so it is stable across frames.
    Drawing it per frame would make every block jitter in time independently on
    every frame instead of holding one fixed offset - noise, not stagger.
    """
    rng = np.random.default_rng(seed)
    blocks = []
    y = 0.0
    while y < height:
        row_h = max(1.0, jitter(rng, cell_h, h_rand))
        x = 0.0
        while x < width:
            w = max(1.0, jitter(rng, cell_w, w_rand))
            keep = rng.random() >= gap_chance
            colour = int(rng.integers(0, n_colours))
            phase = float(rng.random())
            if keep:
                b = Block(x, x + w, y, y + row_h, colour)
                b.phase = phase
                blocks.append(b)
            x += w
        y += row_h
    return blocks


# -------------------------------------------------------------------- the wipe

def edge_progress(b, t, L, t0, span, stagger, block_dur, time_rand):
    """Local 0..1 progress of one sweep across one block.

    `t0` is when this sweep starts globally and `span` is how long it runs, so
    the same function serves the reveal and the erase.
    """
    start = t0 + (b.x0 / L) * span
    d0 = ((b.x1 - b.x0) / L) * span
    d = d0 + (block_dur * span - d0) * stagger

    # The time shove is scaled by the block's own natural duration so it stays
    # proportionate: a sliver should not be flung further in time than a slab.
    start += (b.phase - 0.5) * time_rand * block_dur * span

    if d <= 1e-9:
        return 1.0 if t >= start else 0.0
    return float(np.clip((t - start) / d, 0.0, 1.0))


def wipe_window(b, t, L, hold, stagger, block_dur, time_rand):
    """Visible x-range of a block, or None. Both edges run left-to-right."""
    p_in = edge_progress(b, t, L, 0.0, hold,
                         stagger, block_dur, time_rand)
    p_out = edge_progress(b, t, L, hold, 1.0 - hold,
                          stagger, block_dur, time_rand)

    w = b.x1 - b.x0
    left = b.x0 + w * p_out      # erasing edge, also travelling left to right
    right = b.x0 + w * p_in      # revealing edge
    if right <= left:
        return None
    return left, right


# ------------------------------------------------------------------- rendering

def render(width, height, blocks, t, L, hold, stagger, block_dur, time_rand,
           palette_lin, bg_lin, snap=False):
    img = np.repeat(np.asarray(bg_lin)[None, None, :], height, axis=0)
    img = np.repeat(img, width, axis=1).copy()

    for b in blocks:
        win = wipe_window(b, t, L, hold, stagger, block_dur, time_rand)
        if win is None:
            continue
        x0, x1 = win
        y0, y1 = b.y0, b.y1

        if snap:
            x0, x1 = round(x0), round(x1)
            y0, y1 = round(y0), round(y1)
            if x1 <= x0 or y1 <= y0:
                continue

        ix0, ix1 = max(int(np.floor(x0)), 0), min(int(np.ceil(x1)), width)
        iy0, iy1 = max(int(np.floor(y0)), 0), min(int(np.ceil(y1)), height)
        if ix1 <= ix0 or iy1 <= iy0:
            continue

        cx = coverage_span(x0 - ix0, x1 - ix0, ix1 - ix0)
        cy = coverage_span(y0 - iy0, y1 - iy0, iy1 - iy0)
        cov = cy[:, None] * cx[None, :]

        tile = img[iy0:iy1, ix0:ix1, :]
        img[iy0:iy1, ix0:ix1, :] = (
            tile + cov[:, :, None] * (palette_lin[b.colour] - tile))

    return img


# ---------------------------------------------------------------------- explain

def explain(args, blocks, palette_lin, bg_lin):
    """The claim to check: at stagger=0 this is step 1, exactly.

    The rewrite from 'clip a global line' to 'per-block local progress' is meant
    to be an algebraic identity, not a look-alike. If it is really an identity
    the two renderers agree to floating-point noise, and any drift means the
    start times moved - which would mean stagger is not a clean pivot around
    step 1's behaviour.

    The broken control: run the SAME comparison at stagger=1. It must NOT match.
    A check that passes both ways is measuring nothing.
    """
    W, H = args.width, args.height
    L = args.wipe_len

    print("\n  step2(stagger=s) vs step1, max abs pixel difference:")
    for s in (0.0, 0.25, 1.0):
        worst = 0.0
        for t in (0.12, 0.3, 0.48, 0.66, 0.84):
            a = render(W, H, blocks, t, L, args.hold, s, args.block_dur, 0.0,
                       palette_lin, bg_lin)
            b = step1.render(W, H, blocks, t, L, args.hold,
                             palette_lin, bg_lin)
            worst = max(worst, float(np.abs(a - b).max()))
        verdict = "IDENTICAL" if worst < 1e-12 else "differs"
        print(f"    stagger={s:4.2f}   max|diff| = {worst:.3e}   {verdict}")
    print("    (s=0 must be IDENTICAL; s=1 must differ, or the control is dead)")

    # Start times must not move with stagger - that is the property that keeps
    # the left-to-right march intact.
    print("\n  first-pixel time per block (when reveal begins), by stagger:")
    probe = blocks[:6]
    for s in (0.0, 0.5, 1.0):
        ts = []
        for b in probe:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                if edge_progress(b, mid, L, 0.0, args.hold, s,
                                 args.block_dur, 0.0) > 0.0:
                    hi = mid
                else:
                    lo = mid
            ts.append(hi)
        print(f"    stagger={s:4.2f}  " + " ".join(f"{v:.4f}" for v in ts))
    print("    (rows must be identical: stagger changes duration, not start)")


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
    p.add_argument("--stagger", type=float, default=0.6)
    p.add_argument("--block-dur", type=float, default=0.30,
                   help="at stagger=1, each block's reveal takes this fraction "
                        "of the in-phase, whatever its width")
    p.add_argument("--time-rand", type=float, default=0.0)
    p.add_argument("--bg", default=BACKGROUND)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--explain", action="store_true")
    args = p.parse_args()

    palette_lin = np.array([hex_to_linear(h) for h in PALETTE])
    bg_lin = hex_to_linear(args.bg)
    args.wipe_len = args.width + args.cell_w * 4.0

    blocks = build_layout(args.width, args.height, args.cell_w, args.cell_h,
                          args.w_rand, args.h_rand, args.gap,
                          len(PALETTE), args.seed)
    print(f"layout: {len(blocks)} blocks")

    # A stagger ladder at one instant during the reveal.
    for s in (0.0, 0.35, 0.7, 1.0):
        save(render(args.width, args.height, blocks, 0.30, args.wipe_len,
                    args.hold, s, args.block_dur, args.time_rand,
                    palette_lin, bg_lin),
             f"out_cb2_stagger{int(s * 100):03d}.png")

    # And the full in/out arc at the default stagger.
    for i, t in enumerate([0.15, 0.35, 0.55, 0.75, 0.95]):
        save(render(args.width, args.height, blocks, t, args.wipe_len,
                    args.hold, args.stagger, args.block_dur, args.time_rand,
                    palette_lin, bg_lin),
             f"out_cb2_arc{i}_t{t:.2f}.png")

    if args.explain:
        explain(args, blocks, palette_lin, bg_lin)


if __name__ == "__main__":
    main()

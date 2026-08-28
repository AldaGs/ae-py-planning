r"""
Colour Blocks - Step 3: GENERATIONS. The effect stops ending.

Steps 1-2 render ONE layout and wipe it in, then out. Time runs 0..1 and then
the frame is empty forever. Step 3 makes three changes that are really one
change:

    1. EVERY BLOCK ON ITS OWN CLOCK   its own in-time, out-time and durations,
                                      not a share of a single global sweep
    2. PER-ROW DIRECTION              a row wipes left-to-right or right-to-left
    3. CONTINUOUS GENERATION          blocks keep being made, forever

(3) is the one that reshapes the code, and (1) is what makes (3) possible.

GENERATIONS
-----------
The unit is no longer "the layout". It is a GENERATION: a fresh partition of a
row into blocks, seeded by (row, generation number), born at

    T(g) = g * period

Generation g wipes in, holds, wipes out, and dies - while generation g+1 is
already being born on top of it. Set `period` shorter than a block's total
lifetime and the generations overlap, so the row is never empty and never
repeats: new widths, new colours, forever.

Only three generations can be on screen at once (the one dying, the one living,
the one arriving), so the render walks a 3-generation window around the current
time rather than simulating from t=0. The effect costs the same at frame 10 and
at frame 100000, and it can be scrubbed to any time without state - which is the
requirement AE actually imposes: **render any frame, in any order, statelessly.**

WHAT REGENERATES AND WHAT DOES NOT
----------------------------------
Row HEIGHTS are fixed for all time, seeded once. Only the x-partition inside a
row is redrawn per generation.

That is a deliberate asymmetry. Regenerating row heights too was the obvious
thing to write and it looks terrible: every row boundary in the frame slides at
a different moment, so the image never settles into a grid and reads as noise.
Fixed rows give the eye a stable armature that the changing blocks play against
- which is what the reference image has.

PER-ROW DIRECTION
-----------------
A row's direction only changes two things: which end a block's reveal starts
from, and which end of the ROW goes first.

    ltr:  reveal grows x0 -> x1, and blocks with small x0 start earlier
    rtl:  reveal grows x1 -> x0, and blocks with large x1 start earlier

Both are the same expression with a mirrored coordinate, so there is one code
path, not two.

    python cb_step3_infinite.py            # a strip of frames through time
    python cb_step3_infinite.py --explain  # statelessness + continuity checks
"""

import argparse
import numpy as np
from PIL import Image

from cb_step1_grid import (Block, coverage_span, hex_to_linear, jitter,
                           linear_to_srgb, save)

PALETTE = ["#F0BC2C", "#EE3B24", "#2A5A28", "#4A5FC1", "#000000"]
BACKGROUND = "#000000"

# Generations run negative at early times; seeds cannot. See row_generation().
GEN_BIAS = 1 << 20


# ------------------------------------------------------------------ structure

class Row:
    """A horizontal band with a fixed vertical extent and a fixed direction."""

    __slots__ = ("y0", "y1", "index", "ltr")

    def __init__(self, y0, y1, index, ltr):
        self.y0, self.y1, self.index, self.ltr = y0, y1, index, ltr


def build_rows(height, cell_h, h_rand, seed, dir_mode):
    """Row heights and directions, fixed for all time.

    Seeded from `seed` alone - never from the generation number - because these
    are the part of the layout that must not move. See the module docstring.
    """
    # The 0x520 is just a domain tag so the row stream cannot collide with the
    # per-generation block streams, which are seeded [seed, row, gen].
    rng = np.random.default_rng([seed, 0x520])
    rows = []
    y, i = 0.0, 0
    while y < height:
        h = max(1.0, jitter(rng, cell_h, h_rand))
        if dir_mode == "ltr":
            ltr = True
        elif dir_mode == "rtl":
            ltr = False
        elif dir_mode == "alternate":
            ltr = (i % 2 == 0)
        else:                                    # "random"
            ltr = bool(rng.random() < 0.5)
        rows.append(Row(y, min(y + h, height), i, ltr))
        y += h
        i += 1
    return rows


def row_generation(row, gen, width, cell_w, w_rand, gap, n_colours, seed):
    """The blocks of one row for one generation.

    Seeded by (seed, row index, generation) so it is reproducible from nothing
    but those numbers - no accumulated state, which is what lets any frame be
    rendered in isolation.
    """
    # Generation indices go NEGATIVE near t=0 (the generations already dying
    # when the effect starts), and a seed must be non-negative, so bias it.
    # The C++ port's hash has to tolerate negative generations the same way.
    rng = np.random.default_rng([seed, row.index, gen + GEN_BIAS])
    blocks = []
    x = 0.0
    while x < width:
        w = max(1.0, jitter(rng, cell_w, w_rand))
        keep = rng.random() >= gap
        colour = int(rng.integers(0, n_colours))
        phase = float(rng.random())
        dur_mul = float(np.exp(rng.uniform(-0.5, 0.5)))   # own speed
        if keep:
            b = Block(x, min(x + w, width), row.y0, row.y1, colour, phase)
            b.dur_mul = dur_mul
            blocks.append(b)
        x += w
    return blocks


# ----------------------------------------------------------------- block clock

def block_times(b, row, width, cfg, gen):
    """When this block starts revealing, starts erasing, and how long each takes.

    Everything here is per-block. The only thing shared with its neighbours is
    the generation's birth time.
    """
    w = b.x1 - b.x0
    # Position along the row in the direction of travel, 0 at the leading end.
    pos = (b.x0 / width) if row.ltr else (1.0 - b.x1 / width)

    # Natural duration: what a single sweep crossing the row would give this
    # block. Stagger blends it toward a fixed per-block duration, exactly as in
    # step 2, and dur_mul then gives each block its own private speed.
    natural = (w / width) * cfg.sweep
    fixed = cfg.block_dur * b.dur_mul
    dur = natural + (fixed - natural) * cfg.stagger
    dur = max(dur, 1e-6)

    t_in = gen * cfg.period + pos * cfg.sweep
    t_in += (b.phase - 0.5) * cfg.time_rand * cfg.block_dur
    t_out = t_in + cfg.hold
    return t_in, t_out, dur


def wipe_window(b, row, t, width, cfg, gen):
    """Visible x-range of a block at time t, or None if it is not on screen."""
    t_in, t_out, dur = block_times(b, row, width, cfg, gen)

    p_in = float(np.clip((t - t_in) / dur, 0.0, 1.0))
    p_out = float(np.clip((t - t_out) / dur, 0.0, 1.0))
    if p_out >= 1.0 or p_in <= 0.0:
        return None

    w = b.x1 - b.x0
    if row.ltr:
        # Reveal grows from the left edge; the erase follows from the left edge.
        left, right = b.x0 + w * p_out, b.x0 + w * p_in
    else:
        # Mirrored: both edges work in from the right.
        left, right = b.x1 - w * p_in, b.x1 - w * p_out
    return (left, right) if right > left else None


def live_generations(t, cfg):
    """The generation indices that can possibly be visible at time t.

    A block lives at most `sweep + hold + block_dur` past its generation's
    birth, so anything older than that is gone and anything newer is unborn.
    Walking this window instead of simulating from zero is what makes the
    effect stateless and O(1) in time.
    """
    lifetime = cfg.sweep + cfg.hold + cfg.block_dur * 2.0
    g_hi = int(np.floor(t / cfg.period))
    g_lo = int(np.floor((t - lifetime) / cfg.period))
    return range(g_lo, g_hi + 1)


# ------------------------------------------------------------------- rendering

def render(width, height, rows, t, cfg, palette_lin, bg_lin, snap=False):
    img = np.repeat(np.asarray(bg_lin)[None, None, :], height, axis=0)
    img = np.repeat(img, width, axis=1).copy()

    # Oldest generation first so newer blocks paint over dying ones.
    for gen in live_generations(t, cfg):
        for row in rows:
            for b in row_generation(row, gen, width, cfg.cell_w, cfg.w_rand,
                                    cfg.gap, len(palette_lin), cfg.seed):
                win = wipe_window(b, row, t, width, cfg, gen)
                if win is None:
                    continue
                x0, x1 = win
                y0, y1 = b.y0, b.y1
                if snap:
                    x0, x1, y0, y1 = round(x0), round(x1), round(y0), round(y1)
                    if x1 <= x0 or y1 <= y0:
                        continue

                ix0 = max(int(np.floor(x0)), 0)
                ix1 = min(int(np.ceil(x1)), width)
                iy0 = max(int(np.floor(y0)), 0)
                iy1 = min(int(np.ceil(y1)), height)
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

def explain(args, rows, cfg, palette_lin, bg_lin):
    W, H = args.width, args.height

    # 1. STATELESSNESS. AE renders frames out of order and caches them. A frame
    #    must depend only on t. Render a time twice, once cold and once after
    #    touching a pile of other times, and demand bit equality.
    a = render(W, H, rows, 4.37, cfg, palette_lin, bg_lin)
    for junk in (0.0, 11.2, 1.5, 99.9, 4.36, 4.38):
        render(W, H, rows, junk, cfg, palette_lin, bg_lin)
    b = render(W, H, rows, 4.37, cfg, palette_lin, bg_lin)
    d = float(np.abs(a - b).max())
    print(f"\n  stateless: |cold - after-scrubbing| = {d:.3e} "
          f"{'OK' if d == 0.0 else 'FAIL'}")

    # 2. CONTINUITY. The point of generations is that the frame never empties
    #    out and never freezes. Measure coverage (fraction of non-background
    #    pixels) over a long run: it must never hit 0, and it must keep moving.
    print("\n  coverage over time (must never reach 0.000):")
    ts = np.arange(0.0, 12.0, 0.5)
    covs = []
    for t in ts:
        im = render(W, H, rows, float(t), cfg, palette_lin, bg_lin)
        covs.append(float((im.sum(axis=2) > 1e-9).mean()))
    for i in range(0, len(ts), 6):
        chunk = " ".join(f"{c:.3f}" for c in covs[i:i + 6])
        print(f"    t={ts[i]:5.1f}  {chunk}")
    print(f"    min={min(covs):.3f}  max={max(covs):.3f}  "
          f"mean={np.mean(covs):.3f}")

    # 3. NON-REPETITION. Two generations must not produce the same partition.
    #    The control: ask for the same generation twice - that MUST match, or
    #    the seeding is not reproducible and check 1 passed by accident.
    r0 = rows[0]
    g5a = [(b.x0, b.x1, b.colour) for b in
           row_generation(r0, 5, W, cfg.cell_w, cfg.w_rand, cfg.gap, 5,
                          cfg.seed)]
    g5b = [(b.x0, b.x1, b.colour) for b in
           row_generation(r0, 5, W, cfg.cell_w, cfg.w_rand, cfg.gap, 5,
                          cfg.seed)]
    g6 = [(b.x0, b.x1, b.colour) for b in
          row_generation(r0, 6, W, cfg.cell_w, cfg.w_rand, cfg.gap, 5,
                         cfg.seed)]
    print(f"\n  gen 5 == gen 5 (reproducible): {g5a == g5b}")
    print(f"  gen 5 == gen 6 (must be False): {g5a == g6}")

    # 4. Direction actually differs per row.
    ltr = [r.index for r in rows if r.ltr]
    rtl = [r.index for r in rows if not r.ltr]
    print(f"\n  rows ltr={ltr} rtl={rtl}")


# ------------------------------------------------------------------------ main

class Cfg:
    pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--width", type=int, default=1000)
    p.add_argument("--height", type=int, default=340)
    p.add_argument("--cell-w", type=float, default=62.0)
    p.add_argument("--cell-h", type=float, default=58.0)
    p.add_argument("--w-rand", type=float, default=0.95)
    p.add_argument("--h-rand", type=float, default=0.18)
    p.add_argument("--gap", type=float, default=0.20)
    p.add_argument("--period", type=float, default=1.6,
                   help="time between generations; shorter = denser overlap")
    p.add_argument("--sweep", type=float, default=1.2,
                   help="time for the wave to cross the row end to end")
    p.add_argument("--hold", type=float, default=1.5,
                   help="time a block stays up before it starts erasing")
    p.add_argument("--block-dur", type=float, default=0.45)
    p.add_argument("--stagger", type=float, default=0.8)
    p.add_argument("--time-rand", type=float, default=0.6)
    p.add_argument("--dir-mode", default="alternate",
                   choices=["ltr", "rtl", "alternate", "random"])
    p.add_argument("--bg", default=BACKGROUND)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--explain", action="store_true")
    args = p.parse_args()

    cfg = Cfg()
    for k in ("cell_w", "w_rand", "gap", "period", "sweep", "hold",
              "block_dur", "stagger", "time_rand", "seed"):
        setattr(cfg, k, getattr(args, k))

    palette_lin = np.array([hex_to_linear(h) for h in PALETTE])
    bg_lin = hex_to_linear(args.bg)

    rows = build_rows(args.height, args.cell_h, args.h_rand, args.seed,
                      args.dir_mode)
    print(f"rows: {len(rows)}")

    for i, t in enumerate([0.6, 1.4, 2.2, 3.0, 3.8, 4.6, 7.3, 11.9]):
        save(render(args.width, args.height, rows, t, cfg, palette_lin, bg_lin),
             f"out_cb3_t{i}_{t:.1f}.png")

    if args.explain:
        explain(args, rows, cfg, palette_lin, bg_lin)


if __name__ == "__main__":
    main()

r"""
Colour Blocks - Step 4: THE ROWS ACTUALLY MOVE.

Step 3 read "each row moves left to right or inverse" as the direction the
transition travels. It meant PHYSICAL SCROLLING: the row is a conveyor belt,
and it is a per-row CHOICE, not an automatic alternation.

That breaks the assumption every earlier step rests on. Steps 1-3 partition
`[0, width]` - a row is exactly as wide as the frame. A scrolling row is
INFINITE in x, and the frame is a window sliding along it.

CHUNKS: how to partition an infinite line, statelessly
------------------------------------------------------
The old layout walked `x = 0; while x < width: ...`. You cannot walk an infinite
line, and you cannot walk from x=0 to x=800000 on every frame either. AE also
still demands that any frame be renderable in isolation, so we cannot keep a
running cursor between frames.

So the infinite x axis is cut into fixed-width CHUNKS. Chunk c covers
`[c*CHUNK, (c+1)*CHUNK)`, is seeded by `(seed, row, chunk, gen)`, and walks its
own blocks from its own left edge. To draw the window `[X0, X1]` we generate
only the chunks it touches. Cost is proportional to what is on screen, and
chunk 91238 is as cheap to reach as chunk 0.

THE PRICE, stated honestly: a block can never straddle a chunk boundary, so
there is a forced cut every CHUNK pixels. That is a real artefact. It is
invisible in practice only because the widths are random anyway - a forced
boundary every ~12 cells reads as one more block edge among many. CHUNK must
therefore stay large relative to cell width; at CHUNK ~ 2 cells the cuts become
a visible regular grid. That is the tradeoff, not a free lunch.

WHERE THE WIPE WAVE LIVES NOW
-----------------------------
In step 3 a block's reveal time came from its position across the row,
`pos = x0 / width`. On an infinite strip that is unbounded: a block at world
x = 400000 would get a start time 400000/W sweeps in the future and never
appear.

The fix is that `pos` wraps. It is measured in SCREEN space at the generation's
birth time, then taken modulo 1:

    pos = frac( (world_x - scroll_offset(T_gen)) / width )

so the wave is a repeating left-to-right ripple over the strip rather than a
single sweep with an origin. Bounded everywhere, and still a pure function of
(block, generation).

Scroll offset itself is just `sign * speed * t`, and `sign` comes from the same
per-row direction choice as before - so `scroll=0` collapses this file back to
step 3 (up to chunk seams), and the direction control now means one thing for
both the motion and the wave.

    python cb_step4_scroll.py            # scrolling strip + a scroll=0 control
    python cb_step4_scroll.py --explain  # seam, statelessness, wrap checks
"""

import argparse
import numpy as np
from PIL import Image

from cb_step1_grid import (Block, coverage_span, hex_to_linear, jitter,
                           linear_to_srgb, save)

PALETTE = ["#F0BC2C", "#EE3B24", "#2A5A28", "#4A5FC1", "#000000"]
BACKGROUND = "#000000"

# Chunk and generation indices both run negative; rng seeds cannot.
IDX_BIAS = 1 << 20


class Row:
    __slots__ = ("y0", "y1", "index", "ltr")

    def __init__(self, y0, y1, index, ltr):
        self.y0, self.y1, self.index, self.ltr = y0, y1, index, ltr


def build_rows(height, cell_h, h_rand, seed, dir_mode):
    """Row heights and directions, fixed for all time.

    `dir_mode` defaults to a CONSTANT direction. Alternating rows makes the
    frame read as a ping-pong loop - two interleaved combs shuttling against
    each other - which is a distinctive look but rarely the wanted one. It stays
    available; it is just not the default.
    """
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
        else:
            ltr = bool(rng.random() < 0.5)
        rows.append(Row(y, min(y + h, height), i, ltr))
        y += h
        i += 1
    return rows


# ------------------------------------------------------------------- the strip

def chunk_blocks(row, chunk, gen, cfg, n_colours):
    """Blocks of one chunk of one row for one generation, in WORLD x."""
    rng = np.random.default_rng(
        [cfg.seed, row.index, chunk + IDX_BIAS, gen + IDX_BIAS])
    x0_chunk = chunk * cfg.chunk
    x1_chunk = x0_chunk + cfg.chunk

    blocks = []
    x = x0_chunk
    while x < x1_chunk:
        w = max(1.0, jitter(rng, cfg.cell_w, cfg.w_rand))
        keep = rng.random() >= cfg.gap
        colour = int(rng.integers(0, n_colours))
        phase = float(rng.random())
        dur_mul = float(np.exp(rng.uniform(-0.5, 0.5)))
        # Clipped at the chunk edge - this is the seam described in the header.
        x_end = min(x + w, x1_chunk)
        if keep:
            b = Block(x, x_end, row.y0, row.y1, colour, phase, dur_mul)
            blocks.append(b)
        x += w
    return blocks


def scroll_offset(row, t, cfg):
    """World x currently at the left edge of the frame, for this row."""
    return (1.0 if row.ltr else -1.0) * cfg.scroll * t


def visible_chunks(row, t, width, cfg):
    off = scroll_offset(row, t, cfg)
    c0 = int(np.floor(off / cfg.chunk))
    c1 = int(np.floor((off + width) / cfg.chunk))
    return range(c0, c1 + 1)


# ----------------------------------------------------------------- block clock

def block_times(b, row, width, cfg, gen):
    w = b.x1 - b.x0
    # Screen position at the generation's birth, wrapped: a repeating ripple
    # rather than one sweep with an origin. See the header.
    birth = gen * cfg.period
    screen_x = b.x0 - scroll_offset(row, birth, cfg)
    pos = (screen_x / width) % 1.0
    if not row.ltr:
        pos = 1.0 - pos

    natural = (w / width) * cfg.sweep
    fixed = cfg.block_dur * b.dur_mul
    dur = max(natural + (fixed - natural) * cfg.stagger, 1e-6)

    t_in = birth + pos * cfg.sweep
    t_in += (b.phase - 0.5) * cfg.time_rand * cfg.block_dur
    return t_in, t_in + cfg.hold, dur


def wipe_window(b, row, t, width, cfg, gen):
    t_in, t_out, dur = block_times(b, row, width, cfg, gen)
    p_in = float(np.clip((t - t_in) / dur, 0.0, 1.0))
    p_out = float(np.clip((t - t_out) / dur, 0.0, 1.0))
    if p_out >= 1.0 or p_in <= 0.0:
        return None

    w = b.x1 - b.x0
    if row.ltr:
        left, right = b.x0 + w * p_out, b.x0 + w * p_in
    else:
        left, right = b.x1 - w * p_in, b.x1 - w * p_out
    return (left, right) if right > left else None


def live_generations(t, cfg):
    lifetime = cfg.sweep + cfg.hold + cfg.block_dur * 2.0
    return range(int(np.floor((t - lifetime) / cfg.period)),
                 int(np.floor(t / cfg.period)) + 1)


# ------------------------------------------------------------------- rendering

def render(width, height, rows, t, cfg, palette_lin, bg_lin, snap=False):
    img = np.repeat(np.asarray(bg_lin)[None, None, :], height, axis=0)
    img = np.repeat(img, width, axis=1).copy()

    for gen in live_generations(t, cfg):
        for row in rows:
            off = scroll_offset(row, t, cfg)
            for c in visible_chunks(row, t, width, cfg):
                for b in chunk_blocks(row, c, gen, cfg, len(palette_lin)):
                    win = wipe_window(b, row, t, width, cfg, gen)
                    if win is None:
                        continue
                    # World -> screen happens HERE and nowhere else: all the
                    # geometry and timing above is in world space.
                    x0, x1 = win[0] - off, win[1] - off
                    y0, y1 = b.y0, b.y1
                    if snap:
                        x0, x1 = round(x0), round(x1)
                        y0, y1 = round(y0), round(y1)
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

    # 1. Statelessness survives scrolling - still a pure function of t.
    a = render(W, H, rows, 4.37, cfg, palette_lin, bg_lin)
    for junk in (0.0, 11.2, 1.5, 99.9, 4.36):
        render(W, H, rows, junk, cfg, palette_lin, bg_lin)
    b = render(W, H, rows, 4.37, cfg, palette_lin, bg_lin)
    d = float(np.abs(a - b).max())
    print(f"\n  stateless: |cold - after-scrubbing| = {d:.3e} "
          f"{'OK' if d == 0.0 else 'FAIL'}")

    # 2. Reaching far into the strip must cost the same as the start. If this
    #    is not flat, something is walking from x=0.
    import time as _t
    print("\n  render cost vs how far along the strip we are:")
    for t in (0.5, 50.0, 5000.0):
        s = _t.perf_counter()
        render(W, H, rows, t, cfg, palette_lin, bg_lin)
        off = cfg.scroll * t
        print(f"    t={t:8.1f}  world x ~{off:11.0f}  "
              f"{(_t.perf_counter() - s) * 1000:7.1f} ms")

    # 3. THE SEAM. Chunk boundaries force a block edge. Measure how many
    #    vertical edges in a row land exactly on a chunk boundary versus
    #    anywhere else, and check the seam is rare relative to natural edges.
    row = rows[0]
    blocks = []
    for c in range(0, 8):
        blocks += chunk_blocks(row, c, 0, cfg, 5)
    edges = len(blocks) * 2
    seams = sum(1 for b in blocks
                if abs(b.x1 % cfg.chunk) < 1e-9 or abs(b.x0 % cfg.chunk) < 1e-9)
    print(f"\n  seam: {seams} forced edges out of {edges} total "
          f"({seams / edges:.1%}) at chunk={cfg.chunk:.0f}px, "
          f"cell={cfg.cell_w:.0f}px")
    print("    (keep this small; it grows as chunk approaches cell width)")

    # 4. The wrap. pos must stay in [0,1) no matter how far out we go, or
    #    distant blocks get start times in the far future and never appear.
    worst = 0.0
    for c in (0, 1000, 100000):
        for b in chunk_blocks(row, c, 3, cfg, 5):
            t_in, _, _ = block_times(b, row, W, cfg, 3)
            worst = max(worst, abs(t_in - 3 * cfg.period))
    print(f"\n  wrap: max |t_in - birth| across chunks 0..100000 = {worst:.4f}")
    print(f"    (must stay under sweep+jitter ~ "
          f"{cfg.sweep + cfg.time_rand * cfg.block_dur:.4f})")


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
    p.add_argument("--scroll", type=float, default=120.0,
                   help="pixels per unit time; 0 collapses to step 3")
    p.add_argument("--chunk", type=float, default=None,
                   help="width of one generation chunk (default 12 cells)")
    p.add_argument("--period", type=float, default=1.6)
    p.add_argument("--sweep", type=float, default=1.2)
    p.add_argument("--hold", type=float, default=1.5)
    p.add_argument("--block-dur", type=float, default=0.45)
    p.add_argument("--stagger", type=float, default=0.8)
    p.add_argument("--time-rand", type=float, default=0.6)
    p.add_argument("--dir-mode", default="ltr",
                   choices=["ltr", "rtl", "alternate", "random"])
    p.add_argument("--bg", default=BACKGROUND)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--explain", action="store_true")
    args = p.parse_args()

    cfg = Cfg()
    for k in ("cell_w", "w_rand", "gap", "period", "sweep", "hold",
              "block_dur", "stagger", "time_rand", "seed", "scroll"):
        setattr(cfg, k, getattr(args, k))
    cfg.chunk = args.chunk if args.chunk else args.cell_w * 12.0

    palette_lin = np.array([hex_to_linear(h) for h in PALETTE])
    bg_lin = hex_to_linear(args.bg)
    rows = build_rows(args.height, args.cell_h, args.h_rand, args.seed,
                      args.dir_mode)
    print(f"rows: {len(rows)}  chunk={cfg.chunk:.0f}px  scroll={cfg.scroll}")

    for i, t in enumerate([0.0, 1.1, 2.2, 3.3, 4.4, 9.9]):
        save(render(args.width, args.height, rows, t, cfg, palette_lin, bg_lin),
             f"out_cb4_t{i}_{t:.1f}.png")

    if args.explain:
        explain(args, rows, cfg, palette_lin, bg_lin)


if __name__ == "__main__":
    main()

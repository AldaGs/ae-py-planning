r"""
Colour Blocks - Step 5: ROWS YOU CHOOSE, AND CURVES ON THE TRANSITIONS.

Two additions, one of which fixes a flaw in step 4.

    1. ROWS       count and height become controls, not emergent side effects
    2. EASING     separate cubic-bezier curves for the IN and the OUT

ROWS: THE FLAW
--------------
Steps 1-4 built rows by walking down the frame:

    y = 0
    while y < height:  add a row of random height; y += h

Row count is whatever falls out, you cannot ask for eight, and the LAST ROW IS
CLIPPED at whatever fraction of its height happened to be left over. That clipped
row is visible - it is the one band that is always a different size from its
neighbours, and at low `h_rand` it is the only thing breaking an otherwise even
grid.

The fix is to stop walking and start PARTITIONING. Pick the row count first,
draw one random weight per row, then normalise the weights to sum to exactly the
frame height:

    h_i = height * w_i / sum(w)

Exact count, exact fill, no clipped row, and `h_rand` still controls how uneven
the bands are - at h_rand=0 every weight is 1 and the rows are perfectly equal.

Both sizing modes now reduce to this one operation:

    count mode:   n = the number you asked for
    height mode:  n = round(height / cell_h)      then partition as above

So "row height" means "target row height" and the rows still tile the frame
exactly. Count mode is the resolution-independent one: the layout survives a
comp resize or a half-res preview, which pixel heights do not.

EASING
------
Everything so far is linear: a block's reveal edge moves at constant speed,
because the visible width is `w * p` with `p` a raw normalised time. Real motion
design is almost never linear, so `p` goes through a curve first:

    p_eased = ease(p)      then      edge = x0 + w * p_eased

The curve is a CSS-style cubic-bezier with P0=(0,0) and P3=(1,1), so the two
control points are the whole spec - the same (x1,y1,x2,y2) you would type into
AE or CSS. IN and OUT get their own curve, which is the usual thing to want:
snap in, drift out.

The catch worth knowing: `x` here is time and `y` is progress, and the curve is
given as a parametric bezier in BOTH. So evaluating it at a given time means
first solving `Bx(s) = x` for the bezier parameter `s`, THEN reading `By(s)`.
There is no closed form; it is Newton-Raphson with a bisection fallback, which
is exactly what browsers do. That solver is the only non-obvious thing to port.

Control points with y outside [0,1] overshoot deliberately (anticipation, back-
ease). That is allowed and looks good, but for a WIPE an overshoot past 1 would
put the reveal edge outside the block, so the geometry is clamped to the block's
own extent while the curve itself is left free.

    python cb_step5_easing.py            # a curve comparison + row-count ladder
    python cb_step5_easing.py --explain  # solver accuracy, partition exactness
"""

import argparse
import numpy as np
from PIL import Image

from cb_step1_grid import coverage_span, hex_to_linear, linear_to_srgb, save
from cb_step4_scroll import (Cfg, Row, chunk_blocks, live_generations,
                             scroll_offset, visible_chunks)

PALETTE = ["#F0BC2C", "#EE3B24", "#2A5A28", "#4A5FC1", "#000000"]
BACKGROUND = "#000000"


# ---------------------------------------------------------------------- easing

class Bezier:
    """CSS-style cubic-bezier timing function through (0,0) and (1,1).

    `(x1,y1,x2,y2)` are the two control points - the same four numbers as
    `cubic-bezier()` in CSS or a bezier ease in AE.
    """

    __slots__ = ("x1", "y1", "x2", "y2", "linear")

    def __init__(self, x1, y1, x2, y2):
        # x controls must stay in [0,1] or the curve is not a function of time
        # (it would fold back and give two progresses for one instant). y is
        # deliberately left free so overshoot curves work.
        self.x1 = float(np.clip(x1, 0.0, 1.0))
        self.x2 = float(np.clip(x2, 0.0, 1.0))
        self.y1, self.y2 = float(y1), float(y2)
        self.linear = (self.x1 == self.y1 and self.x2 == self.y2)

    @staticmethod
    def _bez(a1, a2, s):
        """Bezier component with endpoints 0 and 1: 3(1-s)^2 s a1 + 3(1-s)s^2 a2 + s^3."""
        m = 1.0 - s
        return 3.0 * m * m * s * a1 + 3.0 * m * s * s * a2 + s * s * s

    @staticmethod
    def _dbez(a1, a2, s):
        m = 1.0 - s
        return (3.0 * m * m * a1
                + 6.0 * m * s * (a2 - a1)
                + 3.0 * s * s * (1.0 - a2))

    def _solve_s(self, x):
        """Find the bezier parameter s with Bx(s) == x.

        Newton-Raphson from a good guess, with a bisection fallback for the flat
        spots where the derivative collapses (x1=x2=0 makes Newton stall). The
        fallback is not optional: `cubic-bezier(0,0,1,1)`-style extremes are
        exactly the ones a user reaches for.
        """
        s = x
        for _ in range(8):
            d = self._dbez(self.x1, self.x2, s)
            if abs(d) < 1e-9:
                break
            err = self._bez(self.x1, self.x2, s) - x
            if abs(err) < 1e-9:
                return s
            s -= err / d
            s = min(max(s, 0.0), 1.0)

        lo, hi = 0.0, 1.0
        for _ in range(40):
            s = 0.5 * (lo + hi)
            if self._bez(self.x1, self.x2, s) < x:
                lo = s
            else:
                hi = s
        return 0.5 * (lo + hi)

    def __call__(self, x):
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        if self.linear:
            return x
        return self._bez(self.y1, self.y2, self._solve_s(x))


PRESETS = {
    "linear":      (0.00, 0.00, 1.00, 1.00),
    "ease":        (0.25, 0.10, 0.25, 1.00),
    "ease-in":     (0.42, 0.00, 1.00, 1.00),
    "ease-out":    (0.00, 0.00, 0.58, 1.00),
    "ease-in-out": (0.42, 0.00, 0.58, 1.00),
    "snap":        (0.10, 0.60, 0.30, 1.00),   # the one from the brief
    "back":        (0.68, -0.55, 0.27, 1.55),  # overshoots on purpose
}


def parse_ease(spec):
    """Accept a preset name or four comma-separated numbers."""
    if spec in PRESETS:
        return Bezier(*PRESETS[spec])
    parts = [float(v) for v in spec.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise ValueError(
            f"ease must be a preset {sorted(PRESETS)} or 'x1,y1,x2,y2', got {spec!r}")
    return Bezier(*parts)


# ------------------------------------------------------------------------ rows

def build_rows(height, seed, dir_mode, h_rand, n_rows=None, cell_h=None):
    """Partition the frame into exactly `n_rows` bands that tile it exactly.

    See the module docstring: this replaces step 4's walk-until-full, which
    produced an emergent row count and a clipped final row.
    """
    if n_rows is None:
        if not cell_h:
            raise ValueError("need n_rows or cell_h")
        n_rows = max(1, int(round(height / cell_h)))

    rng = np.random.default_rng([seed, 0x520])
    # Weights, not heights: the normalisation is what guarantees exact fill.
    spread = np.log(4.0) * float(np.clip(h_rand, 0.0, 1.0))
    w = np.exp(rng.uniform(-spread, spread, n_rows)) if spread > 0 \
        else np.ones(n_rows)
    edges = np.concatenate([[0.0], np.cumsum(w / w.sum()) * height])
    edges[-1] = height                      # kill float drift on the last edge

    rows = []
    for i in range(n_rows):
        if dir_mode == "ltr":
            ltr = True
        elif dir_mode == "rtl":
            ltr = False
        elif dir_mode == "alternate":
            ltr = (i % 2 == 0)
        else:
            ltr = bool(rng.random() < 0.5)
        rows.append(Row(float(edges[i]), float(edges[i + 1]), i, ltr))
    return rows


# ----------------------------------------------------------------- block clock

def block_times(b, row, width, cfg, gen):
    w = b.x1 - b.x0
    birth = gen * cfg.period
    pos = ((b.x0 - scroll_offset(row, birth, cfg)) / width) % 1.0
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
    raw_in = float(np.clip((t - t_in) / dur, 0.0, 1.0))
    raw_out = float(np.clip((t - t_out) / dur, 0.0, 1.0))
    if raw_out >= 1.0 or raw_in <= 0.0:
        return None

    # The curves go on the normalised TIME, before it becomes geometry.
    p_in = cfg.ease_in(raw_in)
    p_out = cfg.ease_out(raw_out)

    w = b.x1 - b.x0
    if row.ltr:
        left, right = b.x0 + w * p_out, b.x0 + w * p_in
    else:
        left, right = b.x1 - w * p_in, b.x1 - w * p_out

    # Overshooting curves (y outside [0,1]) are allowed, but the drawn edge is
    # clamped to the block's own extent - a reveal cannot run past the shape.
    left = min(max(left, b.x0), b.x1)
    right = min(max(right, b.x0), b.x1)
    return (left, right) if right > left else None


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

def explain(args, cfg):
    # 1. The solver. A timing function must satisfy f(0)=0, f(1)=1, and for
    #    non-overshooting controls it must be monotonic. More sharply: solving
    #    Bx(s)=x then evaluating is only correct if the solve is accurate, so
    #    measure the residual |Bx(s) - x| directly.
    print("\n  bezier solver residual max|Bx(s) - x| over 1001 samples:")
    xs = np.linspace(0, 1, 1001)
    for name in ("linear", "ease", "snap", "back", "ease-in-out"):
        bz = Bezier(*PRESETS[name])
        res = max(abs(bz._bez(bz.x1, bz.x2, bz._solve_s(x)) - x)
                  for x in xs[1:-1])
        ys = [bz(x) for x in xs]
        mono = all(ys[i + 1] >= ys[i] - 1e-12 for i in range(len(ys) - 1))
        print(f"    {name:12s} residual={res:.2e}  f(0)={ys[0]:.3f} "
              f"f(1)={ys[-1]:.3f}  monotonic={mono}")
    print("    ('back' is SUPPOSED to be non-monotonic - it overshoots")
    print("     by design. Any other row reporting False is a bug.)")

    # The degenerate case Newton alone fails on.
    hard = Bezier(0.0, 0.0, 0.0, 1.0)
    res = max(abs(hard._bez(hard.x1, hard.x2, hard._solve_s(x)) - x)
              for x in xs[1:-1])
    print(f"    flat-derivative case (0,0,0,1): residual={res:.2e} "
          f"{'OK' if res < 1e-6 else 'FAIL - bisection fallback broken'}")

    # 2. The partition. Exact count, exact fill, no clipped row - the thing
    #    step 4 got wrong.
    print("\n  row partition (count, total height, min/max band):")
    for n in (3, 8, 17):
        for hr in (0.0, 0.6):
            rows = build_rows(args.height, args.seed, "ltr", hr, n_rows=n)
            hs = np.array([r.y1 - r.y0 for r in rows])
            print(f"    n={n:3d} h_rand={hr:.1f}  got {len(rows):3d} rows  "
                  f"sum={hs.sum():.6f} (want {args.height})  "
                  f"min={hs.min():6.2f} max={hs.max():6.2f}")
    print("    (sum must equal frame height exactly; at h_rand=0 all bands"
          " equal)")


# ------------------------------------------------------------------------ main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--width", type=int, default=1000)
    p.add_argument("--height", type=int, default=340)
    p.add_argument("--rows", type=int, default=None,
                   help="exact row count; overrides --cell-h")
    p.add_argument("--cell-h", type=float, default=58.0,
                   help="target row height when --rows is not given")
    p.add_argument("--h-rand", type=float, default=0.18)
    p.add_argument("--cell-w", type=float, default=62.0)
    p.add_argument("--w-rand", type=float, default=0.95)
    p.add_argument("--gap", type=float, default=0.20)
    p.add_argument("--scroll", type=float, default=120.0)
    p.add_argument("--chunk", type=float, default=None)
    p.add_argument("--period", type=float, default=1.6)
    p.add_argument("--sweep", type=float, default=1.2)
    p.add_argument("--hold", type=float, default=1.5)
    p.add_argument("--block-dur", type=float, default=0.45)
    p.add_argument("--stagger", type=float, default=0.8)
    p.add_argument("--time-rand", type=float, default=0.6)
    p.add_argument("--ease-in", default="snap",
                   help="preset name or 'x1,y1,x2,y2'")
    p.add_argument("--ease-out", default="ease-in")
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
    cfg.ease_in = parse_ease(args.ease_in)
    cfg.ease_out = parse_ease(args.ease_out)

    palette_lin = np.array([hex_to_linear(h) for h in PALETTE])
    bg_lin = hex_to_linear(args.bg)

    rows = build_rows(args.height, args.seed, args.dir_mode, args.h_rand,
                      n_rows=args.rows, cell_h=args.cell_h)
    print(f"rows: {len(rows)}  ease_in={args.ease_in} "
          f"ease_out={args.ease_out}")

    for n in (3, 6, 12):
        rr = build_rows(args.height, args.seed, args.dir_mode, args.h_rand,
                        n_rows=n)
        save(render(args.width, args.height, rr, 3.3, cfg, palette_lin, bg_lin),
             f"out_cb5_rows{n:02d}.png")

    if args.explain:
        explain(args, cfg)


if __name__ == "__main__":
    main()

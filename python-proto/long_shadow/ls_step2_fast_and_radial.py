"""
Long Shadow - Step 2: width-independent directional + RADIAL / INVERSE-RADIAL.

Step 1 marched  cov(p) = max_{t in [0,L]} alpha(p - t*d)  in O(L) sampling
passes. Correct and smooth, but a 500px shadow cost 100x a 5px one. Two things
this step fixes:

1. DIRECTIONAL, WIDTH-INDEPENDENT via LOG-DOUBLING (a sparse-table max).
   max is idempotent and overlap-safe, so we can double the reach each pass:
       M0(p) = alpha(p)                              # covers offset {0}
       Mk(p) = max( M{k-1}(p), M{k-1}(p - 2^{k-1} d) )   # covers {0 .. 2^k-1}
   After k = ceil(log2 L) passes we can read the max over ANY window of length
   L by combining two overlapping power-of-two blocks (classic sparse table):
       res = max( Mk(p), Mk(p - (n - 2^k) d) )       # exactly offsets 0..n
   O(N log L) total instead of O(N L), and every pass is a plain bilinear
   shift+max over the whole frame - i.e. a PING-PONG doubling, the same shape as
   the JFA we used on the GPU for Buildable Stroke. This is the GPU-friendly
   path. (A constant direction is required - see radial below.)

2. RADIAL / INVERSE-RADIAL. Here the projection direction is per-pixel:
       radial:         d(p) = normalize(p - source)   # cast AWAY from a point
       inverse-radial: d(p) = normalize(source - p)   # cast TOWARD the point
   and Length is a PERCENT of the pixel's distance to the source. Because d
   varies per pixel, the single global shift of the doubling trick does not
   apply, so radial marches (per-pixel direction + per-pixel length). Still
   smooth (same soft-alpha sampling). The eventual fast path is a LOG-POLAR
   remap about the source, which turns radial back into "along +r" = the
   directional case - noted for a later step, not needed now.

Validates the fast directional field against Step 1's march (ground truth).

Run:  python ls_step2_fast_and_radial.py
"""

import numpy as np
import cv2
from ls_step1_directional import load, direction, over, save, \
    long_shadow_directional as march_directional


# ---------------------------------------------------------------------------
# shared: bilinear shift (sample cov at p - offset)
# ---------------------------------------------------------------------------

def _grid(h, w):
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    return xs, ys


def _shift(cov, ox, oy, xs, ys):
    """Sample cov at (x - ox, y - oy), bilinear, zero outside."""
    return cv2.remap(cov, xs - ox, ys - oy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)


# ---------------------------------------------------------------------------
# 1. directional, width-independent (log-doubling sparse-table max)
# ---------------------------------------------------------------------------

def long_shadow_directional_fast(cov, angle_deg, length):
    """max over t in [0, length] of cov(p - t*d), in O(N log length) passes."""
    h, w = cov.shape
    xs, ys = _grid(h, w)
    dx, dy = direction(angle_deg)

    n = int(np.floor(length))            # integer offsets 0..n via the table
    if n < 1:                            # sub-pixel length: one fractional tail
        res = cov.copy()
        if length > 1e-6:
            np.maximum(res, _shift(cov, dx * length, dy * length, xs, ys), out=res)
        return res

    samples = n + 1                      # number of integer offsets covered
    # build the doubling tables: tables[k] = max over offsets [0 .. 2^k - 1]
    tables = [cov.copy()]
    while (1 << (len(tables))) <= samples:
        k = len(tables)
        off = 1 << (k - 1)
        shifted = _shift(tables[k - 1], dx * off, dy * off, xs, ys)
        tables.append(np.maximum(tables[k - 1], shifted))

    kk = len(tables) - 1                 # largest k with 2^kk <= samples
    blk = 1 << kk
    a = tables[kk]                                        # offsets 0 .. blk-1
    start2 = samples - blk                               # second block start
    b = _shift(tables[kk], dx * start2, dy * start2, xs, ys)  # .. offset n
    res = np.maximum(a, b)

    frac = length - n                    # fractional tail at offset == length
    if frac > 1e-6:
        np.maximum(res, _shift(cov, dx * length, dy * length, xs, ys), out=res)
    return res


# ---------------------------------------------------------------------------
# 2. radial / inverse-radial (per-pixel direction + per-pixel length)
# ---------------------------------------------------------------------------

def long_shadow_radial(cov, source, pct, inverse=False, step=0.5, max_len=4000.0):
    """Radial long shadow. Length = pct% of each pixel's distance to source.

    source = (sx, sy) in pixels. inverse=False casts AWAY from source, True
    casts TOWARD it. Sampling is bilinear so the soft edge is preserved.
    """
    h, w = cov.shape
    xs, ys = _grid(h, w)
    sx, sy = source
    vx, vy = xs - sx, ys - sy            # vector source -> pixel
    dist = np.sqrt(vx * vx + vy * vy) + 1e-6
    # unit projection direction, per pixel
    if inverse:
        dx, dy = -vx / dist, -vy / dist
    else:
        dx, dy = vx / dist, vy / dist
    L = np.clip(dist * (pct / 100.0), 0.0, max_len)   # per-pixel length
    Lmax = float(L.max())

    out = cov.copy()
    t = step
    while t <= Lmax + 1e-6:
        active = (t <= L)                # pixels whose ray still runs at this t
        mapx = xs - dx * t
        mapy = ys - dy * t
        sampled = cv2.remap(cov, mapx.astype(np.float32), mapy.astype(np.float32),
                            cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                            borderValue=0.0)
        cand = np.where(active, sampled, 0.0)
        np.maximum(out, cand, out=out)
        t += step
    return out


# ---------------------------------------------------------------------------

def colored_shadow(cov, color, h, w):
    return (np.zeros((h, w, 3), np.float32) + np.asarray(color, np.float32))


def main():
    src_rgb, alpha = load("../buildable_strokes/CTBS.png")
    h, w = alpha.shape
    shadow_color = (0.0, 0.0, 0.0)

    # --- 1. fast directional == march (validation) -------------------------
    print("checks:")
    for ang, L in ((90.0, 60.0), (30.0, 120.0), (200.0, 45.0)):
        ref = march_directional(alpha, ang, L, step=0.5)     # ground truth
        fast = long_shadow_directional_fast(alpha, ang, L)
        err = np.abs(ref - fast)
        # the fast path samples on a 1px lattice, the march on 0.5px, so a
        # sub-pixel disagreement on thin diagonals is expected; coverage of the
        # SOLID interior and the smooth edge must still match closely.
        big = np.mean(err > 0.15) * 100
        print(f"  [{'ok ' if big < 1.0 else 'FAIL'}] dir fast~march "
              f"ang={ang:5.1f} L={L:4.0f}: max|d|={err.max():.3f}, "
              f">0.15 on {big:.3f}% px")

    # timing sketch: passes are ~log2(L) for fast vs L/step for march
    import time
    t0 = time.time(); _ = march_directional(alpha, 90.0, 300.0, 0.5); tm = time.time() - t0
    t0 = time.time(); _ = long_shadow_directional_fast(alpha, 90.0, 300.0); tf = time.time() - t0
    print(f"  L=300: march {tm*1e3:6.1f}ms  vs  fast {tf*1e3:6.1f}ms "
          f"({tm/max(tf,1e-6):.1f}x)")

    # --- 2. hero renders ---------------------------------------------------
    def render(cov, name):
        sh = colored_shadow(cov, shadow_color, h, w)
        rgb, a = over(sh, cov, src_rgb, alpha)
        save(name, rgb, a)

    render(long_shadow_directional_fast(alpha, 90.0, 60.0), "out_ls2_directional.png")

    src = (949.4, 183.6)                 # same source point as the reference UI
    render(long_shadow_radial(alpha, src, 30.0, inverse=False), "out_ls2_radial.png")
    render(long_shadow_radial(alpha, src, 30.0, inverse=True),  "out_ls2_inverse_radial.png")

    # a centered radial so the outward fan is obvious
    render(long_shadow_radial(alpha, (w / 2, h / 2), 40.0), "out_ls2_radial_center.png")

    # --- 3. radial sanity: direction points away/toward source -------------
    # a lone bright pixel near source should smear OUTWARD (radial) and INWARD
    # (inverse). Check the smear lands on the far / near side of the point.
    probe = np.zeros((h, w), np.float32)
    py, px = int(h * 0.5), int(w * 0.5)
    probe[py-2:py+3, px-2:px+3] = 1.0
    s2 = (px - 100, py)                  # source to the LEFT of the probe
    rad = long_shadow_radial(probe, s2, 100.0, inverse=False)
    inv = long_shadow_radial(probe, s2, 100.0, inverse=True)
    # radial casts away from source (to the RIGHT of the probe)
    right_mass = rad[py, px+10:px+60].sum()
    left_mass  = rad[py, px-60:px-10].sum()
    ok_rad = right_mass > left_mass
    # inverse casts toward source (LEFT)
    right_i = inv[py, px+10:px+60].sum(); left_i = inv[py, px-60:px-10].sum()
    ok_inv = left_i > right_i
    print(f"  [{'ok ' if ok_rad else 'FAIL'}] radial casts AWAY from source "
          f"(right {right_mass:.1f} > left {left_mass:.1f})")
    print(f"  [{'ok ' if ok_inv else 'FAIL'}] inverse casts TOWARD source "
          f"(left {left_i:.1f} > right {right_i:.1f})")

    print("\nWrote out_ls2_directional.png, out_ls2_radial.png, "
          "out_ls2_inverse_radial.png, out_ls2_radial_center.png")


if __name__ == "__main__":
    main()

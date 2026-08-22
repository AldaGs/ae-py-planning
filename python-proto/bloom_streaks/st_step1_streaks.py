"""
Bloom - Stage 4d prototype, Step 1: STREAKS (anisotropic glare).

Bloom's halo is ISOTROPIC (the 2D dual-filter pyramid - symmetric, can't make a
diagonal spike). Real night-vision glare adds ANISOTROPIC streaks: astigmatism
images a point source to a short LINE at the eye's meridian; eyelash/lens
diffraction adds a starburst. So streaks are a SECOND PSF component, summed on
top of the isotropic glow. (User's idea, from lights blooming while driving.)

KEY REUSE: a streak = directional accumulation of the bright-light buffer along
an axis = Extended Shadow's directional sweep, but with SUM instead of MAX and an
exponential falloff.

THE ALGORITHM CLAIM TO VALIDATE HERE
------------------------------------
ES used a log-doubling sparse table for the MAX (max is idempotent, so two
overlapping power-of-two blocks combine with no error). SUM is NOT idempotent, so
that exact trick doesn't port... UNLESS the weights are GEOMETRIC. We want the
geometric-weighted causal sum along direction d:

    streak(p) = sum_{t=0}^{n} decay^t * bright(p - t*d)

Because decay^t factors, the doubling recurrence is EXACT with DISJOINT blocks:

    S_k(p) = sum over offsets [0, 2^k) of decay^t * bright(p - t d)
    S_k(p) = S_{k-1}(p) + decay^(2^{k-1}) * S_{k-1}(p - 2^{k-1} d)

The second half is offsets [2^{k-1}, 2^k); shifting it by 2^{k-1} d aligns it to
[0, 2^{k-1}) and the whole block just needs one scalar factor decay^(2^{k-1}).
Then any length n is stitched from the DISJOINT power-of-two blocks in the binary
expansion of n+1 (no overlap, so no double counting). O(N log n), and every pass
is a bilinear shift+add over the whole frame = a ping-pong doubling, the SAME
GPU-friendly shape as JFA / the ES sweep. This is the whole reason streaks are
cheap and portable.

Validated numerically against a direct O(N*n) march (ground truth).

Multi-arm: a streak axis smears BOTH directions (+d and -d). `count` axes are
spread over 180 deg (count=1 -> astigmatism line; count=3 -> 6-spike star).

Everything runs in LINEAR light and is ADDITIVE, so it drops straight onto
Bloom's extract -> linear -> HDR -> tonemap -> composite spine.

Run:  python st_step1_streaks.py
"""

import time
import numpy as np
import cv2


# ---------------------------------------------------------------------------
# colour transfer (match the C++ Bloom: sRGB <-> linear)
# ---------------------------------------------------------------------------

def srgb_to_linear(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, np.float32), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1.0 / 2.4)) - 0.055)


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _grid(h, w):
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    return xs, ys


def direction(angle_deg):
    a = np.deg2rad(angle_deg)
    return float(np.cos(a)), float(np.sin(a))


def _shift(buf, ox, oy, xs, ys):
    """Sample buf at (x - ox, y - oy), bilinear, zero outside. Multi-channel OK."""
    return cv2.remap(buf, xs - ox, ys - oy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)


# ---------------------------------------------------------------------------
# 1. geometric-weighted directional SUM
# ---------------------------------------------------------------------------

def sweep_sum_fast(bright, angle, length, decay):
    """sum_{t=0}^{n} decay^t * bright(p - t*d) via log-doubling. O(N log n)."""
    h, w = bright.shape[:2]
    xs, ys = _grid(h, w)
    dx, dy = direction(angle)

    n = int(np.floor(length))
    if n < 1:
        return bright.copy()
    samples = n + 1                        # integer offsets 0..n

    # tables[k] = geometric-weighted sum over the DISJOINT block [0, 2^k)
    tables = [bright.copy()]
    while (1 << len(tables)) <= samples:
        k = len(tables)
        off = 1 << (k - 1)
        wk = decay ** off                  # scalar decay across the half-block
        shifted = _shift(tables[k - 1], dx * off, dy * off, xs, ys)
        tables.append(tables[k - 1] + wk * shifted)

    # stitch offsets [0, samples) from disjoint power-of-two blocks (binary of n+1)
    res = np.zeros_like(bright)
    base = 0
    wbase = 1.0                            # decay^base
    for k in range(len(tables) - 1, -1, -1):
        blk = 1 << k
        if base + blk <= samples:
            res += wbase * _shift(tables[k], dx * base, dy * base, xs, ys)
            wbase *= decay ** blk
            base += blk
    return res


def sweep_sum_march(bright, angle, length, decay, step=1.0):
    """Ground-truth O(N*n) march of the same geometric-weighted sum."""
    h, w = bright.shape[:2]
    xs, ys = _grid(h, w)
    dx, dy = direction(angle)
    out = bright.copy()
    t = step
    while t <= length + 1e-6:
        out += (decay ** t) * _shift(bright, dx * t, dy * t, xs, ys)
        t += step
    return out


# ---------------------------------------------------------------------------
# 2. multi-axis streaks (the PSF component Bloom adds)
# ---------------------------------------------------------------------------

def streaks(bright, base_angle, count, length, intensity, eps=0.02):
    """N-axis streak field. Each axis smears +d and -d; only the TAIL (t>=1) is
    kept (the core t=0 is the source itself, already in the art/halo). decay is
    derived from Length so the streak fades to `eps` of the core at the tip."""
    decay = float(np.exp(np.log(eps) / max(length, 1.0)))
    # normalize the tail so its integrated weight is ~1 regardless of length:
    #   sum_{t>=1} decay^t = decay/(1-decay)
    norm = (1.0 - decay) / decay
    acc = np.zeros_like(bright)
    for i in range(count):
        ang = base_angle + i * 180.0 / count
        for d in (ang, ang + 180.0):
            arm = sweep_sum_fast(bright, d, length, decay) - bright   # tail only
            acc += arm
    return (intensity * norm) * acc


# ---------------------------------------------------------------------------
# test scaffolding
# ---------------------------------------------------------------------------

def synthetic_points(h=540, w=960):
    """A few small HDR point sources on black - the clearest way to see a star."""
    lin = np.zeros((h, w, 3), np.float32)
    def disc(cx, cy, r, rgb, peak):
        ys, xs = np.ogrid[:h, :w]
        m = ((xs - cx) ** 2 + (ys - cy) ** 2) <= r * r
        for c in range(3):
            lin[..., c][m] = rgb[c] * peak
    disc(w * 0.30, h * 0.42, 3, (1, 1, 1), 8.0)     # bright white point
    disc(w * 0.62, h * 0.40, 2, (1, 0.6, 0.3), 12.0)  # warm point
    disc(w * 0.78, h * 0.66, 5, (0.5, 0.7, 1.0), 5.0)  # cool small disc
    return lin


def composite_over_black(art_lin, glow_lin):
    """Additive in linear, then encode. art assumed opaque for this proto."""
    out = art_lin + glow_lin
    return np.clip(linear_to_srgb(out) * 255.0, 0, 255).astype(np.uint8)


def save(name, img_u8):
    cv2.imwrite(name, cv2.cvtColor(img_u8, cv2.COLOR_RGB2BGR))


def main():
    ok_all = True

    # (a) ON-AXIS the doubling is EXACT: integer shifts, bilinear-at-integer is a
    # pixel copy, so the geometric sum equals the march bit-for-bit (fp only).
    print("=== (a) on-axis: fast log-doubling == march (exact algebra) ===")
    rng = np.random.default_rng(0)
    probe = rng.random((120, 160, 3), np.float32).astype(np.float32)
    for ang, L in ((0.0, 100.0), (90.0, 60.0), (270.0, 45.0)):
        decay = float(np.exp(np.log(0.02) / L))
        err = float(np.abs(sweep_sum_fast(probe, ang, L, decay)
                           - sweep_sum_march(probe, ang, L, decay, 1.0)).max())
        good = err < 2e-4
        ok_all &= good
        print(f"  [{'ok ' if good else 'FAIL'}] ang={ang:5.1f} L={L:4.0f}: "
              f"max|fast-march|={err:.2e}")

    # (b) AT ANY ANGLE the sweep CONSERVES ENERGY (a bilinear shift preserves the
    # total sum), so a single interior point's streak carries exactly the analytic
    # geometric energy  sum_{t=0}^{n} decay^t  - independent of angle. This is what
    # guarantees the right length/brightness; the diagonal fast path only spreads
    # LATERALLY by a sub-pixel (repeated bilinear), which for a glow is fine.
    print("=== (b) any angle: energy conserved == analytic geometric sum ===")
    for ang in (0.0, 15.0, 33.0, 45.0, 200.0):
        L = 90
        decay = float(np.exp(np.log(0.02) / L))
        pt = np.zeros((400, 400), np.float32); pt[200, 200] = 1.0
        n = int(np.floor(L))
        analytic = float(sum(decay ** t for t in range(n + 1)))
        e_fast = float(sweep_sum_fast(pt, ang, L, decay).sum())
        e_march = float(sweep_sum_march(pt, ang, L, decay, 1.0).sum())
        rf = abs(e_fast - analytic) / analytic
        rm = abs(e_march - analytic) / analytic
        good = rf < 2e-3 and rm < 2e-3
        ok_all &= good
        print(f"  [{'ok ' if good else 'FAIL'}] ang={ang:5.1f}: analytic={analytic:6.3f}"
              f"  fast err={rf:.2e}  march err={rm:.2e}")

    # timing: log-doubling vs march
    big = rng.random((540, 960, 3), np.float32).astype(np.float32)
    decay = float(np.exp(np.log(0.02) / 300))
    t0 = time.time(); sweep_sum_fast(big, 20.0, 300.0, decay); tf = time.time() - t0
    t0 = time.time(); sweep_sum_march(big, 20.0, 300.0, decay); tm = time.time() - t0
    print(f"  L=300 @ 960x540: fast {tf*1e3:6.1f}ms  vs  march {tm*1e3:6.1f}ms "
          f"({tm/max(tf,1e-6):.1f}x)")

    # --- hero renders on synthetic HDR points ------------------------------
    art = synthetic_points()
    save("out_st1_input.png", composite_over_black(art, np.zeros_like(art)))

    configs = [
        ("astig_line", 0.0, 1, 220, 0.9),    # 1 axis = astigmatism streak
        ("astig_diag", 45.0, 1, 220, 0.9),
        ("star4",      0.0, 2, 200, 0.7),    # 2 axes = 4-spike
        ("star6",     15.0, 3, 200, 0.6),    # 3 axes = 6-spike
        ("star8",      0.0, 4, 180, 0.5),
    ]
    for name, ang, cnt, L, inten in configs:
        st = streaks(art, ang, cnt, L, inten)
        save(f"out_st1_{name}.png", composite_over_black(art, st))
        print(f"  wrote out_st1_{name}.png  (angle={ang} count={cnt} L={L} I={inten})")

    # --- reach check: how far does a single point's streak carry? -----------
    # Scan the tail (excluding the core) rightward; find last pixel above 1/255
    # of the core, i.e. the visible streak length. decay is set so weight = eps at
    # t = L, but the point's own brightness scales that, so visible reach != L.
    core = 20.0
    pt = np.zeros((600, 600, 3), np.float32); pt[300, 300] = core
    L = 150
    decay = float(np.exp(np.log(0.02) / L))
    tail = (sweep_sum_fast(pt, 0.0, L, decay) - pt)[300, 301:, 0]  # rightward, past core
    vis = np.where(tail > (1.0 / 255.0))[0]
    reach = int(vis.max()) + 1 if len(vis) else 0
    print(f"\n  reach: point (core {core:.0f}), L={L}, decay={decay:.4f} -> "
          f"visible tail (>1/255) out to ~{reach}px")

    print("\nSUM log-doubling validated." if ok_all else "\n*** FAST != MARCH ***")


if __name__ == "__main__":
    main()

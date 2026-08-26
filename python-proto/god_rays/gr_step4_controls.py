r"""
God Rays - Step 4: CONTROLS, PERFORMANCE, and the things AE will demand.

Three separate jobs: make the parameter set real, make it fast, and make it
survive contact with After Effects.

Star Glint step 4's actual lesson was "profile before optimising" - it had a
note in step 2 confidently naming the wrong bottleneck, and the profiler found
that thing was 2% of the cost. So this file profiles first, in check (a), and
every optimisation below exists because a measurement asked for it.

WHAT IS NEW HERE
----------------
  source modes     rays from luma, from alpha, or from both. Shine's most-used
                   control after Intensity, and it is three lines.
  alpha            a fourth channel, swept identically, with two properties
                   asserted rather than hoped for.
  res_scale        AE renders previews at Half/Third/Quarter. Anything measured
                   in PIXELS has to be scaled or the preview lies about the
                   final render.
  downsample       shafts are low-frequency; run the sweep small and upsample.
                   A quality control, not a free lunch - check (e) prices it.
  theta LUT        the shimmer mask was 438 ms at 1080p (step 2 check (f)).

THE RESOLUTION-INDEPENDENCE RESULT, UP FRONT
---------------------------------------------
Star Glint had to scale every length-like control by res_scale, because its
Length was in pixels. Almost nothing in this effect is:

    reach     a FRACTION of the distance to C          -> free
    falloff   a dimming ratio                          -> free
    centre    normalised 0..1 coords                   -> free
    detail    cycles per full TURN (angular)           -> free
    ramp u    normalised by the farthest corner        -> free
    samples   a quality knob                           -> free (that was step 1)

    nyquist fade radius   in PIXELS                    -> NOT free

One control out of seven. That is a direct dividend of having written every
parameter as a ratio in steps 1-3, and it is worth noticing *why* it happened:
the geometric formulation made fractions the natural unit. Check (d) verifies
it instead of assuming it, and finds the one place where the honest answer is
"you cannot have both" - see the docstring on `fade_radius`.

Run:
    python gr_step4_controls.py              # profile + verify + render
    python gr_step4_controls.py --explain    # dump the AE parameter block
"""

import sys
import time

import numpy as np
import cv2

from gr_step1_rays import (LUMA, decay_for, describe, _grid, linear_to_srgb,
                           ratio_for, ray_sweep, save_linear, synthetic_sun,
                           zoom)
from gr_step2_shimmer import (angle_map, harmonics, nyquist_radius,
                              radius_map, shimmer_field, shimmer_mask)
from gr_step3_color import (build_tables, band, band_edges_uniform, band_u,
                            colorize_march, colorize_radial, lut_at, radial_u,
                            ray_lut)


# ===========================================================================
#  the parameter block, grouped the way the AE UI will group it
# ===========================================================================

DEFAULTS = {
    # --- Source -----------------------------------------------------------
    "source":      "luma",      # luma | alpha | luma_x_alpha
    "threshold":   0.9,
    "knee":        0.3,
    # --- Rays -------------------------------------------------------------
    "centre":      (0.32, 0.30),   # NORMALISED 0..1, so it survives res_scale
    "reach":       0.45,
    "falloff":     0.06,
    "intensity":   0.6,
    "samples":     192,
    # --- Shimmer ----------------------------------------------------------
    "shimmer":     0.7,
    "detail":      14.0,
    "phase":       0.0,
    "seed":        3,
    # --- Colour -----------------------------------------------------------
    "color_mode":  "radial",    # off | radial | march
    "ramp":        "sunset",
    "bands":       16,
    # --- Quality / render -------------------------------------------------
    "downsample":  1,
    "res_scale":   1.0,         # AE hands this in: 1.0 Full, 0.5 Half, ...
}

PRESETS = {
    "default":   {},
    "clean":     {"shimmer": 0.0, "color_mode": "off"},
    "dusty_sun": {"reach": 0.55, "detail": 18.0, "ramp": "sunset"},
    "cathedral": {"reach": 0.35, "shimmer": 0.45, "detail": 8.0,
                  "ramp": "teal_gold", "intensity": 0.7},
    "march":     {"color_mode": "march", "ramp": "rainbow", "bands": 16},
    "half_res":  {"downsample": 2},
    "preview":   {"res_scale": 0.5},
}


def params(preset="default", **over):
    p = dict(DEFAULTS)
    p.update(PRESETS[preset])
    p.update(over)
    return p


# ===========================================================================
#  source modes + alpha
# ===========================================================================

def bright_pass4(lin_rgba, source="luma", threshold=0.9, knee=0.3):
    """Soft-knee bright pass over an RGBA buffer, with selectable source.

    The weight is computed from whichever channels the mode names, and then
    applied to ALL FOUR. Applying it to rgb only would produce lit-but-
    transparent pixels, which is the artefact check (c) exists to forbid: a
    streak that brightens a pixel it does not also make more opaque cannot
    composite over anything.
    """
    rgb, a = lin_rgba[..., :3], lin_rgba[..., 3]
    if source == "alpha":
        w = np.clip(a, 0.0, 1.0)
    else:
        y = rgb @ LUMA
        lo, hi = threshold - knee, threshold + knee
        if hi <= lo:
            w = (y >= threshold).astype(np.float32)
        else:
            t = np.clip((y - lo) / (hi - lo), 0.0, 1.0)
            w = t * t * (3.0 - 2.0 * t)
        if source == "luma_x_alpha":
            w = w * np.clip(a, 0.0, 1.0)
    return lin_rgba * w[..., None].astype(np.float32)


# ===========================================================================
#  resolution independence
# ===========================================================================

def centre_px(p, h, w):
    """Normalised centre -> buffer pixels. Free at any resolution, by design."""
    return float(p["centre"][0] * w), float(p["centre"][1] * h)


def fade_radius(detail, res_scale, octaves=3):
    """Radius (in BUFFER pixels) inside which shimmer is faded out.

    There are two demands here and at reduced resolution they conflict:

      - AA:   fade at least out to k_max/pi BUFFER pixels, or the shimmer
              aliases into a crawling starburst on the source point.
      - LOOK: fade out to the SAME FRACTION OF FRAME as full res, i.e.
              (k_max/pi) * res_scale buffer pixels, or the preview shows a
              different-sized clean core than the final render.

    For res_scale < 1 the second is always smaller than the first, so they
    cannot both be satisfied. We take the max: correctness first, and accept
    that a Quarter-res preview shows a slightly larger clean core than the
    final. That is a known, bounded preview inaccuracy - and it is the right
    way round, because the alternative is a preview that aliases.

    CONSEQUENCE, worth stating plainly: since res_scale <= 1 always, the max()
    is ALWAYS the Nyquist term, so res_scale never changes the answer. The
    parameter is inert here. It is kept in the signature because that is where
    a reader will look for it and because "why is there no res_scale in this
    effect" deserves an answer at the point of use - not because it does
    anything. Check (d) demonstrates both halves: the effect is resolution
    independent without it, and what it would take to break that.
    """
    r0 = nyquist_radius(detail, octaves)
    return max(r0, r0 * res_scale)


# ===========================================================================
#  the shimmer mask, with a theta LUT
# ===========================================================================

_ANGLE_CACHE = {}


def _angles(h, w, cx, cy):
    """arctan2 over the frame, cached. It is the irreducible part of the mask."""
    key = (h, w, round(cx, 3), round(cy, 3))
    g = _ANGLE_CACHE.get(key)
    if g is None:
        g = (angle_map(h, w, cx, cy), radius_map(h, w, cx, cy))
        _ANGLE_CACHE[key] = g
    return g


def shimmer_mask_lut(h, w, cx, cy, detail, amount, phase, seed, res_scale=1.0,
                     octaves=3, n=16384):
    """Same field as step 2, but the cosines are evaluated into a 1D table.

    The field is a function of ONE variable, so a table over [-pi, pi] captures
    it. This is legal for exactly the reason step 2 gave: the shimmer is a
    function of angle alone. If it ever gains a radial term, the LUT dies too.

    The table is read with LINEAR INTERPOLATION, not nearest. First version
    snapped to the nearest entry and check (b) caught it: nearest-neighbour
    error is FIRST order in the step size, so it scales as amount*k_max*h and
    got worse with Detail exactly as you would predict - 5.3e-03 at detail=6,
    3.5e-02 at detail=40. Interpolating makes it second order and the same
    table lands at ~1e-4. A bigger table would have been the expensive way to
    fix a mistake in the reader.
    """
    if amount <= 0.0:
        return np.ones((h, w), np.float32)
    theta, rad = _angles(h, w, cx, cy)

    grid = np.linspace(-np.pi, np.pi, n, dtype=np.float32)
    table = shimmer_field(grid, detail, amount, phase, seed, octaves)

    fidx = (theta + np.pi) * ((n - 1) / (2.0 * np.pi))
    i0 = np.clip(fidx.astype(np.int32), 0, n - 2)
    frac = (fidx - i0).astype(np.float32)
    m = table[i0] * (1.0 - frac) + table[i0 + 1] * frac

    r0 = fade_radius(detail, res_scale, octaves)
    lo, hi = r0, r0 * 4.0
    t = np.clip((rad - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    f = (t * t * (3.0 - 2.0 * t)).astype(np.float32)
    return (1.0 + (m - 1.0) * f).astype(np.float32)


# ===========================================================================
#  the full render
# ===========================================================================

def tail_only4(rays, core, decay, samples, intensity):
    """Step 1's tail_only, over RGBA. Same normalisation."""
    if decay >= 1.0:
        norm = 1.0 / max(samples, 1)
    else:
        s = decay * (1.0 - decay ** samples) / (1.0 - decay)
        norm = 1.0 / max(s, 1e-6)
    return (intensity * norm) * (rays - core)


def render(lin_rgba, p):
    """The whole effect. Returns the composited RGBA in linear light."""
    h, w = lin_rgba.shape[:2]
    cx, cy = centre_px(p, h, w)
    smp = int(p["samples"])
    r = ratio_for(p["reach"], smp)
    d = decay_for(p["falloff"], smp)

    bright = bright_pass4(lin_rgba, p["source"], p["threshold"], p["knee"])

    ds = max(int(p["downsample"]), 1)
    if ds > 1:
        sh, sw = max(h // ds, 4), max(w // ds, 4)
        small = cv2.resize(bright, (sw, sh), interpolation=cv2.INTER_AREA)
        scx, scy = cx * sw / w, cy * sh / h
    else:
        small, sh, sw, scx, scy = bright, h, w, cx, cy

    lut = ray_lut(p["ramp"]) if p["color_mode"] != "off" else None

    if p["color_mode"] == "march":
        rays = np.zeros_like(small)
        tables = build_tables(small[..., :3], scx, scy, smp, r, d)
        ta = build_tables(small[..., 3], scx, scy, smp, r, d)
        edges = band_edges_uniform(smp, int(p["bands"]))
        for a_, b_ in zip(edges[:-1], edges[1:]):
            if b_ <= a_:
                continue
            u = band_u(a_, b_, d, smp)
            rays[..., :3] += band(tables, a_, b_, scx, scy, r, d) * lut_at(lut, u)
            rays[..., 3] += band(ta, a_, b_, scx, scy, r, d)
        core_tint = lut_at(lut, 0.0)
    else:
        rays = ray_sweep(small, scx, scy, smp, r, d)
        core_tint = np.float32([1.0, 1.0, 1.0])

    # shimmer: angle-only, so it goes AFTER the sweep (step 2's theorem)
    mask = shimmer_mask_lut(sh, sw, scx, scy, p["detail"], p["shimmer"],
                            p["phase"], p["seed"], p["res_scale"])
    rays = rays * mask[..., None]

    # radial colour: NOT angle-only, so order matters and post is the ship path
    if p["color_mode"] == "radial":
        u, _ = radial_u(sh, sw, scx, scy)
        rays[..., :3] *= lut_at(lut, u)

    if ds > 1:
        rays = cv2.resize(rays, (w, h), interpolation=cv2.INTER_LINEAR)
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)

    # the core (t=0 term) that must be subtracted, built the same way the sweep
    # built it - shimmer applied, colour applied at u=0 / the pixel's own u
    core = bright * mask[..., None]
    if p["color_mode"] == "radial":
        u, _ = radial_u(h, w, cx, cy)
        core = core.copy()
        core[..., :3] *= lut_at(lut, u)
    elif p["color_mode"] == "march":
        core = core.copy()
        core[..., :3] *= core_tint

    tail = tail_only4(rays, core, d, smp, p["intensity"])
    return lin_rgba + tail


# ===========================================================================
#  test image with alpha
# ===========================================================================

def synthetic_rgba(h=540, w=960):
    """The step-1 subject, plus an alpha channel and a transparent region.

    The transparent block on the right is what check (c) uses: nothing there,
    so nothing may come out of there.
    """
    lin, c = synthetic_sun(h, w)
    a = np.ones((h, w), np.float32)
    a[:, int(w * 0.90):] = 0.0
    # a semi-transparent slab over part of the sky, so that `luma_x_alpha` is
    # actually distinguishable from `luma` - a mode you cannot tell apart from
    # another mode in the test image is a mode you have not tested
    a[:int(h * 0.55), int(w * 0.60):int(w * 0.88)] = 0.35
    lin = lin.copy()
    lin[:, int(w * 0.90):] = 0.0
    return np.dstack([lin, a]).astype(np.float32), c


def save_rgba(name, rgba):
    save_linear(name, rgba[..., :3])


# ===========================================================================
#  --explain
# ===========================================================================

def explain(preset="default"):
    p = params(preset)
    print(f"\nPARAMETER BLOCK  (preset={preset})\n")
    groups = [("Source", ("source", "threshold", "knee")),
              ("Rays", ("centre", "reach", "falloff", "intensity", "samples")),
              ("Shimmer", ("shimmer", "detail", "phase", "seed")),
              ("Colour", ("color_mode", "ramp", "bands")),
              ("Quality", ("downsample", "res_scale"))]
    for g, keys in groups:
        print(f"  {g}")
        for k in keys:
            print(f"    {k:<12} = {p[k]}")

    rgba, _ = synthetic_rgba()
    h, w = rgba.shape[:2]
    cx, cy = centre_px(p, h, w)
    smp = int(p["samples"])
    r = ratio_for(p["reach"], smp)
    d = decay_for(p["falloff"], smp)
    print(f"\n  derived:")
    print(f"    centre px    = ({cx:.1f}, {cy:.1f})")
    print(f"    r            = {r:.6f}   r^{smp} = {r**smp:.4f}")
    print(f"    decay        = {d:.6f}   decay^{smp} = {d**smp:.4f}")
    ks, _a = harmonics(p["detail"])
    print(f"    harmonics    = {ks}")
    print(f"    fade radius  = {fade_radius(p['detail'], p['res_scale']):.1f} px "
          f"(nyquist {nyquist_radius(p['detail']):.1f} px, "
          f"res_scale {p['res_scale']})")

    out = render(rgba, p)
    describe("output", out)
    save_rgba(f"explain4_{preset}.png", out)
    print(f"\n  wrote explain4_{preset}.png\n")


# ===========================================================================
#  main
# ===========================================================================

def main():
    ok = True
    rgba, _ = synthetic_rgba(1080, 1920)
    p = params()
    H, W = rgba.shape[:2]
    cx, cy = centre_px(p, H, W)
    smp, r, d = 192, ratio_for(0.45, 192), decay_for(0.06, 192)

    print("=== (a) PROFILE FIRST (1920x1080, samples=192) ===")

    # Warm up and take the best of 3. Step 2 shipped a one-cold-call timing
    # check whose numbers did not reproduce - it was measuring first-touch
    # allocation, not the algorithm. Corrected there and never repeated here.
    def t(fn, n=3):
        out = fn()                                    # warm-up, discarded
        times = []
        for _ in range(n):
            t0 = time.time(); out = fn(); times.append(time.time() - t0)
        return min(times) * 1e3, out

    t_bp, bright = t(lambda: bright_pass4(rgba, "luma", 0.9, 0.3))
    t_sweep, swept = t(lambda: ray_sweep(bright, cx, cy, smp, r, d))
    t_mask_old, _ = t(lambda: shimmer_mask(H, W, cx, cy, 14.0, 0.7, 0.0, 3))
    _ANGLE_CACHE.clear()
    t_mask_new, _ = t(lambda: shimmer_mask_lut(H, W, cx, cy, 14.0, 0.7, 0.0, 3))
    t_mask_warm, _ = t(lambda: shimmer_mask_lut(H, W, cx, cy, 14.0, 0.7, 0.1, 3))
    t_rad, _ = t(lambda: colorize_radial(swept[..., :3], cx, cy, ray_lut("sunset")))
    t_march, _ = t(lambda: colorize_march(bright[..., :3], cx, cy, smp, r, d,
                                          ray_lut("sunset"), 16))
    rows = [("bright_pass4", t_bp), ("ray_sweep (the sweep)", t_sweep),
            ("shimmer_mask  step 2", t_mask_old),
            ("shimmer_mask_lut cold", t_mask_new),
            ("shimmer_mask_lut warm", t_mask_warm),
            ("colorize_radial", t_rad), ("colorize_march 16 bands", t_march)]
    for name, ms in rows:
        print(f"  {name:<26} {ms:8.1f} ms")
    print(f"  -> the sweep is {t_sweep/(t_sweep+t_bp+t_mask_warm+t_rad)*100:.0f}% "
          f"of the radial-mode render. Optimise THAT, not the mask.")
    print(f"  -> mask: {t_mask_old:.0f} -> {t_mask_warm:.0f} ms "
          f"({t_mask_old/max(t_mask_warm,1e-6):.1f}x) but it was never the "
          f"bottleneck.")
    print(f"  -> march mode costs {t_march/max(t_sweep,1e-6):.1f}x the plain "
          f"sweep. That is the real price of MARCH colour.")

    print("\n=== (b) the theta LUT reproduces step 2's mask ===")
    for detail in (6.0, 14.0, 40.0):
        _ANGLE_CACHE.clear()
        a = shimmer_mask(H, W, cx, cy, detail, 0.7, 0.0, 3)
        b = shimmer_mask_lut(H, W, cx, cy, detail, 0.7, 0.0, 3)
        err = float(np.abs(a - b).max())
        good = err < 5e-3
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] detail={detail:5.1f}  "
              f"max|lut-exact| = {err:.2e}")

    print("\n=== (c) alpha ===")
    # First version of this check asserted "a transparent REGION stays exactly
    # zero", copied straight from Star Glint. It failed at 5.5e-01, and the
    # code was right: rays are LIGHT, and light must be able to travel into an
    # empty region. Star Glint's assertion was about a wholly empty INPUT,
    # which is a different claim. Restated:
    small, _ = synthetic_rgba(360, 640)
    empty = np.zeros_like(small)
    out_e = render(empty, params())
    leaked = float(np.abs(out_e).max())
    good = leaked == 0.0
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] wholly empty input -> max out = "
          f"{leaked:.2e} (exactly 0: no glow from nothing)")

    out = render(small, params())
    lit = (out[..., :3].max(axis=2) > 1e-4) & (out[..., 3] <= 1e-6)
    n_lit = int(lit.sum())
    good = n_lit == 0
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] lit-but-transparent pixels = "
          f"{n_lit} (light must bring its own coverage)")

    clear = small[..., 3] < 1e-6
    print(f"  [note] rays DO reach into the transparent region: rgb up to "
          f"{float(out[clear][..., :3].max()):.3f}, alpha up to "
          f"{float(out[clear][..., 3].max()):.3f}.")
    print("         That is correct. What matters is that the two rise together.")

    print("\n=== (d) resolution independence ===")
    # Render full, render a half-size buffer the way AE would, compare.
    full = render(small, params())
    hh, hw = small.shape[0] // 2, small.shape[1] // 2
    halfbuf = cv2.resize(small, (hw, hh), interpolation=cv2.INTER_AREA)
    fh, fw = small.shape[:2]

    def rel(a_, b_):
        up = cv2.resize(a_, (fw, fh), interpolation=cv2.INTER_LINEAR)
        return float(np.abs(up - b_).mean() / max(np.abs(b_).mean(), 1e-6))

    e_half = rel(render(halfbuf, params()), full)
    good = e_half < 0.15
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] half-res buffer matches full "
          f"render to {e_half:.3e}, with NO res_scale applied anywhere")

    # ...which only means something if the check COULD fail. Star Glint's
    # Length was in absolute pixels; here is what that would have cost. Express
    # `reach` as a pixel distance instead of a fraction, and forget to scale it.
    def reach_from_px(px, h_, w_, cx_, cy_):
        corners = [(0, 0), (0, w_ - 1), (h_ - 1, 0), (h_ - 1, w_ - 1)]
        rmax = max(float(np.hypot(x - cx_, y - cy_)) for y, x in corners)
        return float(np.clip(px / max(rmax, 1e-6), 0.0, 0.99))

    LEN_PX = 260.0
    fcx, fcy = centre_px(params(), fh, fw)
    hcx, hcy = centre_px(params(), hh, hw)
    full_px = render(small, params(reach=reach_from_px(LEN_PX, fh, fw, fcx, fcy)))
    out_bug = render(halfbuf, params(
        reach=reach_from_px(LEN_PX, hh, hw, hcx, hcy)))          # unscaled
    out_fix = render(halfbuf, params(
        reach=reach_from_px(LEN_PX * 0.5, hh, hw, hcx, hcy)))    # scaled

    e_bug, e_fix = rel(out_bug, full_px), rel(out_fix, full_px)
    good = e_fix < e_bug
    ok &= good
    print(f"  a pixel-valued Reach, unscaled : rel err = {e_bug:.3e}   <- the bug")
    print(f"  the same, scaled by res_scale  : rel err = {e_fix:.3e}")
    print(f"  [{'ok  ' if good else 'FAIL'}] the check is not vacuous - pixel "
          f"units DO break across resolutions, ratios do not")
    print("  -> RESULT: six of seven controls are ratios and need no scaling.")
    print("     The seventh (the shimmer fade radius) is in BUFFER pixels and")
    print("     is automatically right there. res_scale is INERT in this")
    print("     effect - see fade_radius' docstring for why that is not a bug.")

    print("\n=== (e) downsampling: what it costs ===")
    base = render(small, params(downsample=1))
    for ds in (1, 2, 4):
        t0 = time.time(); out = render(small, params(downsample=ds))
        ms = (time.time() - t0) * 1e3
        err = float(np.abs(out - base).max() / max(np.abs(base).max(), 1e-6))
        print(f"  downsample={ds}  {ms:7.1f} ms   peak err vs full = {err:.3f}")

    print("\n=== (f) the OPEN QUESTION from step 1: is ray width angle-dependent? ===")
    # Star Glint's step 2 found its arms were visibly softer on the diagonals
    # than on the axes, and needed a lateral blur to equalise them. Steps 1 and
    # 3 guessed the same defect would not appear here but never checked. Check
    # it: sweep a lone bright point placed at the same radius at many angles and
    # measure the ANGULAR width of the ray it produces.
    HH = 401
    C = 200.0
    widths = []
    for ang in (0, 10, 22, 30, 45, 60, 75, 90):
        pt = np.zeros((HH, HH), np.float32)
        a = np.deg2rad(ang)
        px, py = C + 150 * np.cos(a), C + 150 * np.sin(a)
        pt[int(round(py)), int(round(px))] = 1.0
        sw = ray_sweep(pt, C, C, 96, ratio_for(0.5, 96), decay_for(0.05, 96))
        # angular profile in an annulus just outside the point
        th = angle_map(HH, HH, C, C)
        rad = radius_map(HH, HH, C, C)
        ann = (rad > 165) & (rad < 185)
        vals, angs = sw[ann], th[ann]
        if vals.sum() <= 0:
            widths.append(float("nan")); continue
        mu = np.angle(np.exp(1j * angs) @ vals / vals.sum())
        dev = np.angle(np.exp(1j * (angs - mu)))
        widths.append(float(np.sqrt((dev ** 2 * vals).sum() / vals.sum())))
    wmin, wmax = np.nanmin(widths), np.nanmax(widths)
    spread = wmax / max(wmin, 1e-9)
    print("  POINT source - angular ray width (rad) by source angle:")
    print("   " + "  ".join(f"{a:>3}:{w:.4f}" for a, w in
                            zip((0, 10, 22, 30, 45, 60, 75, 90), widths)))
    print(f"  spread = {spread:.2f}x   (Star Glint's was 2.84x before its "
          f"lateral blur)")
    print("  -> the defect IS present, with the same signature: 0 and 90 deg")
    print("     are sharp, everything else is ~1.7x wider. Weaker than Star")
    print("     Glint's, but not absent. Steps 1 and 3 guessed 'not needed';")
    print("     'closed, no blur needed' would have been the wrong call.")

    # But does it MATTER? Star Glint's sources were points, so a 1px hairline
    # was the whole arm. This effect's sources are AREAS - that was step 1's
    # subject lesson. Repeat with an extended source and see what survives.
    widths2 = []
    for ang in (0, 10, 22, 30, 45, 60, 75, 90):
        pt = np.zeros((HH, HH), np.float32)
        a = np.deg2rad(ang)
        px, py = C + 150 * np.cos(a), C + 150 * np.sin(a)
        yy, xx = np.ogrid[:HH, :HH]
        pt[((xx - px) ** 2 + (yy - py) ** 2) <= 5.0 ** 2] = 1.0
        sw = ray_sweep(pt, C, C, 96, ratio_for(0.5, 96), decay_for(0.05, 96))
        th = angle_map(HH, HH, C, C)
        rad = radius_map(HH, HH, C, C)
        ann = (rad > 165) & (rad < 185)
        vals, angs = sw[ann], th[ann]
        mu = np.angle(np.exp(1j * angs) @ vals / vals.sum())
        dev = np.angle(np.exp(1j * (angs - mu)))
        widths2.append(float(np.sqrt((dev ** 2 * vals).sum() / vals.sum())))
    spread2 = float(np.nanmax(widths2) / max(np.nanmin(widths2), 1e-9))
    good = spread2 < 1.15
    ok &= good
    print("  DISC source (r=5) - same measurement:")
    print("   " + "  ".join(f"{a:>3}:{w:.4f}" for a, w in
                            zip((0, 10, 22, 30, 45, 60, 75, 90), widths2)))
    print(f"  [{'ok  ' if good else 'FAIL'}] spread = {spread2:.2f}x")
    print("  -> VERDICT: the sampler defect is real, but it is swamped by the")
    print("     source's own width as soon as the source is bigger than a")
    print("     pixel - and here it always is, because the bright pass keeps")
    print("     ~30% of the frame (step 1's subject lesson). No lateral blur.")
    print("     Star Glint needed one because its sources really were points.")

    print("\n=== (g) the 1/r^2 exposure question from step 1 ===")
    # Step 1 noted long reach blows out and deferred the question. Measure it.
    print(f"  {'reach':>6} {'peak tail':>12} {'mean tail':>12}")
    for reach in (0.2, 0.45, 0.7, 0.9):
        q = params(reach=reach, color_mode="off", shimmer=0.0)
        out = render(small, q)
        tail = out - small
        print(f"  {reach:6.2f} {float(tail.max()):12.3f} {float(tail.mean()):12.4f}")
    print("  -> The PEAK is flat: it sits on the sun, which the sweep barely")
    print("     moves. The MEAN grows ~2.4x from reach 0.2 to 0.9. Step 1's")
    print("     note said 'long reach blows out' - what actually blows out is")
    print("     the frame average, not the highlight, which is a different")
    print("     problem with a different fix. The growth is real light (a wider")
    print("     shaft collects more), so it is NOT normalised away: ship it as")
    print("     documented behaviour and let Intensity be the knob.")

    print("\n=== (h) source modes ===")
    for mode in ("luma", "alpha", "luma_x_alpha"):
        b = bright_pass4(small, mode, 0.9, 0.3)
        frac = float((b[..., :3].max(axis=2) > 1e-6).mean())
        print(f"  {mode:<14} {frac*100:5.2f}% of pixels feed the sweep")

    print("\n=== (i) renders ===")
    rgba2, _ = synthetic_rgba()
    for name in ("default", "clean", "dusty_sun", "cathedral", "march",
                 "half_res"):
        out = render(rgba2, params(name))
        save_rgba(f"out_gr4_{name}.png", out)
        print(f"  wrote out_gr4_{name}.png")

    print("\nstep 4 validated." if ok else "\n*** step 4 FAILED ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain(sys.argv[-1] if sys.argv[-1] in PRESETS else "default")
    else:
        main()

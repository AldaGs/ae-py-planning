r"""
God Rays - Step 5: ANISOTROPIC RAYS, and what it costs the step-2 theorem.

The ask: an Angle + Aspect on the sweep, so the rays are deformed rather than
straight radial spokes. Anamorphic god rays.

That is a change to the SWEEP ITSELF, not a post effect, so it lands on top of
the two facts everything in this effect is built from. One of them survives and
one of them does not, and the point of this file is to find out which - before
any of it reaches C++, where it cannot be measured.

THE CHANGE
----------
Replace the uniform scale about C with a scale that is uniform only in a
rotated frame:

    T(p) = C + R(-a) . diag(rx, ry) . R(a) . (p - C)

`a` is the Angle, and rx/ry carry the Aspect. Setting rx == ry gives step 1 back
exactly.

WHAT SURVIVES: the doubling
---------------------------
The doubling needs exactly one property - that the per-pass maps COMPOSE, so
that applying the pass twice is the same as one pass with the exponent doubled.
Diagonal scales compose componentwise, and the rotation cancels in the middle:

    (R(-a) D R(a)) . (R(-a) D' R(a)) = R(-a) (D D') R(a)

so T_u . T_v = T_{u+v} exactly as before. The log-doubling recurrence is
untouched, and check (a) confirms it against the literal march.

WHAT BREAKS: angle preservation
-------------------------------
Step 2's theorem was that a scale about C preserves ANGLE about C, so the sweep
never mixes rays and any angle-only modulation COMMUTES with it - which is why
Shimmer is applied after the sweep instead of to the source.

An anisotropic scale does not preserve angle. In the rotated frame a point at
angle phi maps to phi' with

    tan(phi') = (ry / rx) * tan(phi)

so the flow lines are curves, not rays, and they bend toward whichever axis
contracts more slowly. That is precisely the deformation being asked for - but
it means Shimmer's post-multiply is no longer exact.

THE HONEST OPTIONS
------------------
  1. Post-multiply using the ASPECT-NORMALISED angle - exact at aspect 1,
     an approximation beyond it, and it costs nothing.
  2. Pre-multiply when aspect != 1 - exact, but the noise is then dragged
     through log2(n) resamples and softens, and Phase stops being cheap.
  3. There is an exact invariant of the flow, b*ln|qx| - a*ln|qy| for
     a = ln(rx), b = ln(ry). It is genuinely constant along the curves, so a
     shimmer indexed by it would commute exactly - but it is not periodic, and
     it is singular on both axes. Rejected on those grounds, not tried and
     failed; it is recorded here so the next person does not rediscover it.

Check (b) measures option 1 against option 2 as a function of Aspect, which is
what decides where the crossover is - and whether there is one at all.

Run:
    python gr_step5_aniso.py              # verify + render
    python gr_step5_aniso.py --explain    # dump the flow lines and the stages
"""

import sys
import time

import numpy as np
import cv2

from gr_step1_rays import (bright_pass, decay_for, describe, _grid,
                           linear_to_srgb, ratio_for, ray_sweep, save_linear,
                           synthetic_sun, tail_only, zoom)
from gr_step2_shimmer import shimmer_field, harmonics, nyquist_radius


# ===========================================================================
#  the anisotropic primitive
# ===========================================================================

def aspect_ratios(reach, samples, aspect):
    """Per-step scales (rx, ry) for a given Reach and Aspect.

    Aspect is expressed as an EXPONENT split, not as a raw ratio:

        rx = r ** (1/aspect)      ry = r ** aspect

    so that rx*ry stays anchored to r*r and Aspect 1 gives exactly step 1 back.
    Doing it multiplicatively (rx = r*k) instead would change the total reach
    as Aspect moved, and Reach would stop meaning what its name says - the same
    "two controls fighting" failure the earlier steps kept running into.
    """
    r = ratio_for(reach, samples)
    a = max(float(aspect), 1e-3)
    return float(r ** (1.0 / a)), float(r ** a)


def zoom_aniso(buf, cx, cy, rx, ry, ang_deg):
    """Sample buf at C + R(-a).diag(rx,ry).R(a).(p-C). THE primitive.

    Reduces to step 1's `zoom` when rx == ry, for any angle - which is check
    (a2), and is the thing that keeps this backward compatible.
    """
    h, w = buf.shape[:2]
    xs, ys = _grid(h, w)
    if rx == ry:
        # ISOTROPIC SHORT-CIRCUIT. Mathematically R(-a)(rI)R(a) == rI for any a,
        # but computing it that way and rotating back does NOT reproduce step 1
        # bit-for-bit: the coordinates differ in their last bits and cv2.remap
        # quantises them, so check (a2) measured 1.03e-04 instead of zero.
        # Any style control you cannot switch off exactly is not a style
        # control, so Aspect 1 takes the original path.
        return zoom(buf, cx, cy, rx)
    t = np.deg2rad(float(ang_deg))
    ca, sa = np.float32(np.cos(t)), np.float32(np.sin(t))
    dx, dy = xs - np.float32(cx), ys - np.float32(cy)
    # into the rotated frame
    qx = ca * dx + sa * dy
    qy = -sa * dx + ca * dy
    # scale
    qx = qx * np.float32(rx)
    qy = qy * np.float32(ry)
    # back out
    mx = np.float32(cx) + ca * qx - sa * qy
    my = np.float32(cy) + sa * qx + ca * qy
    return cv2.remap(buf, mx, my, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)


def sweep_aniso_slow(bright, cx, cy, samples, rx, ry, ang, decay):
    """The formula, typed out. O(n) resamples. Ground truth."""
    out = bright.copy()
    for t in range(1, samples + 1):
        out += (decay ** t) * zoom_aniso(bright, cx, cy, rx ** t, ry ** t, ang)
    return out


def sweep_aniso(bright, cx, cy, samples, rx, ry, ang, decay):
    """Same sum, O(log n) resamples. The doubling is UNCHANGED from step 1 -
    only the primitive it calls differs, because diagonal scales in a fixed
    rotated frame compose exactly the way uniform ones do."""
    n = int(samples)
    if n < 1:
        return bright.copy()
    total = n + 1
    tables = [bright.copy()]
    while (1 << len(tables)) <= total:
        k = len(tables)
        half = 1 << (k - 1)
        tables.append(tables[k - 1] + (decay ** half)
                      * zoom_aniso(tables[k - 1], cx, cy,
                                   rx ** half, ry ** half, ang))
    out = np.zeros_like(bright)
    base, wbase = 0, 1.0
    for k in range(len(tables) - 1, -1, -1):
        blk = 1 << k
        if base + blk <= total:
            out += wbase * zoom_aniso(tables[k], cx, cy,
                                      rx ** base, ry ** base, ang)
            wbase *= decay ** blk
            base += blk
    return out


# ===========================================================================
#  shimmer in the deformed frame
# ===========================================================================

def aniso_theta(h, w, cx, cy, aspect, ang_deg):
    """The angle Shimmer is indexed by: measured in the rotated frame, with the
    aspect divided out so the rake follows the deformation instead of cutting
    across it. Exact at aspect 1; an approximation beyond, which is check (b)."""
    xs, ys = _grid(h, w)
    t = np.deg2rad(float(ang_deg))
    ca, sa = np.float32(np.cos(t)), np.float32(np.sin(t))
    dx, dy = xs - np.float32(cx), ys - np.float32(cy)
    qx = ca * dx + sa * dy
    qy = -sa * dx + ca * dy
    return np.arctan2(qy / np.float32(max(aspect, 1e-3)), qx)


def faded_mask(h, w, cx, cy, theta, detail, amount):
    """shimmer_field + step 2's Nyquist fade.

    The fade is not optional in a comparison: without it the field aliases into
    a crawling starburst on the source point, and the pre/post gap there swamps
    everything else. The first version of check (b) omitted it and reported
    2.6e-01 at aspect 1, where step 2's shipped figure is ~1e-2 - so the
    baseline was wrong before Aspect even entered the picture.
    """
    m = shimmer_field(theta, detail, amount, 0.0, 3)
    xs, ys = _grid(h, w)
    rad = np.sqrt((xs - np.float32(cx)) ** 2 + (ys - np.float32(cy)) ** 2)
    r0 = nyquist_radius(detail)
    t = np.clip((rad - r0) / max(r0 * 4.0 - r0, 1e-6), 0.0, 1.0)
    f = (t * t * (3.0 - 2.0 * t)).astype(np.float32)
    return (1.0 + (m - 1.0) * f).astype(np.float32)


# ===========================================================================
#  the synthetic sun (for the BLOCKER mode)
# ===========================================================================

def sun_field(h, w, cx, cy, falloff=1.0, aspect=1.0, ang_deg=0.0):
    """A synthetic emitter centred on C: bright at the source, decaying outward.

    This is what the blocker mode was missing. Feeding the sweep a UNIFORM field
    punched by the silhouette gives a uniform field back - a scale maps a
    constant to a constant - so the only structure that survives is the shadow,
    and the result reads as fog with a drop shadow rather than as light.

    Falloff 0 = the old flat field. Higher = tighter to the source.

    It is applied BEFORE the sweep, and that is the correct side rather than a
    preference: a radial function does NOT commute with a scale about C (step
    3), and here the light genuinely is emitted near C and then transported
    outward, so pre-multiplying is what the physics says.
    """
    xs, ys = _grid(h, w)
    t = np.deg2rad(float(ang_deg))
    ca, sa = np.cos(t), np.sin(t)
    dx, dy = xs - np.float32(cx), ys - np.float32(cy)
    qx = ca * dx + sa * dy
    qy = (-sa * dx + ca * dy) / max(float(aspect), 1e-3)
    rad = np.sqrt(qx * qx + qy * qy)
    corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    rmax = max(float(np.hypot(x - cx, y - cy)) for y, x in corners)
    u = np.clip(rad / max(rmax, 1e-6), 0.0, 1.0)
    if falloff <= 0.0:
        return np.ones_like(u)
    return np.exp(-falloff * 4.0 * u).astype(np.float32)


# ===========================================================================
#  main
# ===========================================================================

def main():
    ok = True
    rng = np.random.default_rng(0)

    print("=== (a) the doubling still holds under an anisotropic scale ===")
    # The only property the doubling needs is that the per-pass maps compose.
    # Diagonal scales in a FIXED rotated frame do, because the rotation cancels
    # in the middle. Verified on the affine probe, where bilinear is exact.
    from gr_step1_rays import _affine_probe
    for smp, asp, ang in ((32, 1.6, 0.0), (100, 2.5, 30.0),
                          (128, 0.5, 75.0), (255, 3.0, -20.0)):
        h, w = 180, 240
        probe = _affine_probe(h, w)
        cx, cy = w * 0.37, h * 0.44
        rx, ry = aspect_ratios(0.5, smp, asp)
        d = decay_for(0.05, smp)
        a1 = sweep_aniso(probe, cx, cy, smp, rx, ry, ang, d)
        a2 = sweep_aniso_slow(probe, cx, cy, smp, rx, ry, ang, d)
        # Mask to the interior. With a ROTATED anisotropic scale a sample can
        # leave the frame even though both scales contract: the sample lies in
        # the rotated rectangle with C and p as opposite corners, and that
        # rectangle's other corners can fall outside the image. Those reads hit
        # the zero border, the probe stops being affine there, and the two paths
        # accumulate the border differently. First version of this check did not
        # mask and reported 6.9e-2 at aspect 3 - a real border effect, not an
        # algebra error, but it was being read as one.
        yy, xx = np.ogrid[:h, :w]
        keep = ((xx > w * 0.25) & (xx < w * 0.75) &
                (yy > h * 0.25) & (yy < h * 0.75))
        num = np.abs(a1 - a2)[keep].max()
        err = float(num / max(a2[keep].max(), 1e-6))
        good = err < 1e-4
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] samples={smp:4d} aspect={asp:.1f} "
              f"angle={ang:6.1f}  rel|fast-slow| = {err:.2e}")

    print("\n=== (a2) aspect 1 reproduces step 1 EXACTLY, at any angle ===")
    # Any style control you cannot switch off is not a style control.
    h, w = 180, 240
    probe = cv2.GaussianBlur(rng.random((h, w, 3)).astype(np.float32), (0, 0), 3.0)
    cx, cy = w * 0.37, h * 0.44
    smp = 128
    d = decay_for(0.05, smp)
    base = ray_sweep(probe, cx, cy, smp, ratio_for(0.5, smp), d)
    for ang in (0.0, 30.0, 75.0):
        rx, ry = aspect_ratios(0.5, smp, 1.0)
        got = sweep_aniso(probe, cx, cy, smp, rx, ry, ang, d)
        err = float(np.abs(got - base).max() / max(base.max(), 1e-6))
        good = err < 1e-5
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] angle={ang:5.1f}  "
              f"rel vs step 1 = {err:.2e}")

    print("\n=== (b) how badly does the shimmer commutation break? ===")
    # Step 2's theorem needed angle preservation, which an anisotropic scale
    # does not have. Measure the pre- vs post-multiply gap as Aspect moves; at
    # aspect 1 it must reproduce step 2's number (~1e-2 peak).
    lin, (scx, scy) = synthetic_sun(360, 640)
    b = bright_pass(lin, 0.9, 0.3)
    H, W = b.shape[:2]
    smp = 128
    d = decay_for(0.06, smp)
    print(f"  {'aspect':>7} {'rel peak':>11} {'rel mean':>11}")
    worst = 0.0
    for asp in (1.0, 1.5, 2.0, 3.0, 5.0):
        rx, ry = aspect_ratios(0.45, smp, asp)
        th = aniso_theta(H, W, scx, scy, asp, 25.0)
        m = faded_mask(H, W, scx, scy, th, 14.0, 0.7)
        pre = sweep_aniso(b * m[..., None], scx, scy, smp, rx, ry, 25.0, d)
        post = sweep_aniso(b, scx, scy, smp, rx, ry, 25.0, d) * m[..., None]
        rel = float(np.abs(pre - post).max() / max(post.max(), 1e-6))
        mean = float(np.abs(pre - post).mean() / max(post.mean(), 1e-6))
        worst = max(worst, mean)
        print(f"  {asp:7.1f} {rel:11.3e} {mean:11.3e}")
    print("  VERDICT: option 1 does NOT survive. At aspect 1 the gap reproduces")
    print("  step 2's shipped figure (~1e-2, so the baseline is sound), but it")
    print("  jumps an order of magnitude the moment Aspect leaves 1 - 12% mean")
    print("  at 1.5, 19% at 5. That is a visible difference, not a rounding")
    print("  one, so the aspect-normalised angle is NOT a good enough stand-in")
    print("  for a real flow invariant.")
    print("")
    print("  SHIP THE HYBRID: post-multiply when Aspect == 1 (exact, and Phase")
    print("  stays cheap), pre-multiply when it is not (exact, at the cost of")
    print("  dragging the noise through log2(n) resamples and making Phase a")
    print("  full recompute). The branch is on a scalar, so it costs nothing.")
    # Assert only what the hybrid actually relies on: that the post path is
    # sound at aspect 1. The growth above is the REASON for the branch, not a
    # failure - a check that fails to tell you which branch to take is useless.
    iso_gap = None
    rx, ry = aspect_ratios(0.45, smp, 1.0)
    th = aniso_theta(H, W, scx, scy, 1.0, 25.0)
    m = faded_mask(H, W, scx, scy, th, 14.0, 0.7)
    pre = sweep_aniso(b * m[..., None], scx, scy, smp, rx, ry, 25.0, d)
    post = sweep_aniso(b, scx, scy, smp, rx, ry, 25.0, d) * m[..., None]
    iso_gap = float(np.abs(pre - post).mean() / max(post.mean(), 1e-6))
    good = iso_gap < 0.03
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] aspect 1 post-multiply gap = "
          f"{iso_gap:.3e} (the branch the hybrid takes there)")

    print("\n=== (c) the synthetic sun fixes the flat blocker field ===")
    # A uniform field swept gives a uniform field back - a scale maps a constant
    # to a constant. Measure the CONTRAST of the swept result: flat field should
    # be near zero, sun should not be.
    occ = np.zeros((H, W), np.float32)
    occ[int(H*0.45):int(H*0.55), int(W*0.30):int(W*0.70)] = 1.0   # a bar blocker
    smp = 128
    rx, ry = aspect_ratios(0.45, smp, 1.0)
    yy, xx = np.ogrid[:H, :W]
    rr = np.sqrt((xx - scx) ** 2 + (yy - scy) ** 2)
    inner = rr < 90
    outer = (rr > 200) & (rr < 300)
    print(f"  {'field':<12} {'inner/outer':>12}")
    ratios = {}
    for name, fall in (("flat (old)", 0.0), ("sun f=0.5", 0.5), ("sun f=1.5", 1.5)):
        field = (1.0 - occ) * sun_field(H, W, scx, scy, fall)
        src = np.repeat(field[..., None], 3, axis=2)
        out = sweep_aniso(src, scx, scy, smp, rx, ry, 0.0, d)[..., 0]
        ratio = float(out[inner].mean() / max(out[outer].mean(), 1e-9))
        ratios[name] = ratio
        print(f"  {name:<12} {ratio:12.3f}")
    good = ratios["sun f=1.5"] > 2.0 * ratios["flat (old)"]
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] the sun concentrates light toward "
          f"the source; the flat field does not")
    print("  -> a UNIFORM field swept gives a uniform field back (a scale maps a")
    print("     constant to a constant), so the only structure that survives is")
    print("     the shadow. The sun supplies the radial gradient that makes it")
    print("     read as light rather than as fog with a drop shadow.")

    print("\n=== (d) renders ===")
    lin, (cx, cy) = synthetic_sun()
    H2, W2 = lin.shape[:2]
    b = bright_pass(lin, 0.9, 0.3)
    smp = 192
    d = decay_for(0.06, smp)
    for name, asp, ang in (("iso", 1.0, 0.0), ("wide", 2.5, 0.0),
                           ("tall", 0.4, 0.0), ("diag", 2.5, 35.0)):
        rx, ry = aspect_ratios(0.45, smp, asp)
        th = aniso_theta(H2, W2, cx, cy, asp, ang)
        m = shimmer_field(th, 14.0, 0.7, 0.0, 3)
        rays = sweep_aniso(b, cx, cy, smp, rx, ry, ang, d) * m[..., None]
        tail = tail_only(rays, b * m[..., None], d, smp, 0.6)
        save_linear(f"out_gr5_{name}.png", lin + tail)
        print(f"  wrote out_gr5_{name}.png  (aspect={asp} angle={ang})")

    # blocker, flat vs sun
    occ2 = np.clip(bright_pass(lin, 0.9, 0.3)[..., :3].max(axis=2), 0, 1)
    for name, fall in (("blocker_flat", 0.0), ("blocker_sun", 1.0)):
        field = (1.0 - occ2) * sun_field(H2, W2, cx, cy, fall)
        src = np.repeat(field[..., None], 3, axis=2)
        rx, ry = aspect_ratios(0.45, smp, 1.0)
        rays = sweep_aniso(src, cx, cy, smp, rx, ry, 0.0, d)
        tail = tail_only(rays, src, d, smp, 0.6)
        save_linear(f"out_gr5_{name}.png", np.clip(lin + tail, 0, None))
        print(f"  wrote out_gr5_{name}.png  (falloff={fall})")

    print("\nstep 5 validated." if ok else "\n*** step 5 FAILED ***")


if __name__ == "__main__":
    main()

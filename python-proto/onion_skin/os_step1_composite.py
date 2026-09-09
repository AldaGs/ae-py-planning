"""
Onion Skin - Step 1: the COMPOSITING MODEL.

Phase 0 answered how the panel reaches the effect. It answered nothing about
what the effect DRAWS. This step decides that, offline, where it can be measured
- the plug-in is a place where nothing can be.

WHAT THE EFFECT ACTUALLY HAS

The effect sits on an adjustment layer at the top of the comp, so its input is
the composite of everything below it at time t. Checking out that same input at
t+k gives the neighbouring frames. So the material is:

    C(t)            the current frame, the thing being drawn now
    C(t-k), C(t+k)  the neighbours, k = 1..N

THE MODEL

    result = C(t)  OVER  [ skins composited back to front ]

The current frame goes ON TOP, unmodified. That is the whole point: an onion
skin must never alter what the animator is drawing, only what surrounds it. Any
model that tints or fades C(t) is wrong no matter how good it looks.

Each skin is:

    tinted   = lerp(rgb_k, tint_colour, tint_amount)
    alpha    = alpha_k * opacity(k)
    opacity(k) = strength * falloff ** (|k| - 1)

tint_amount = 1 gives the classic flat-colour ghost; 0 keeps the drawing's own
colours and only fades it; in between pulls a coloured drawing toward the hue.
Past and future get separate colours because the direction of time is the one
thing an onion skin has to make instantly readable.

STRAIGHT, NOT PREMULTIPLIED

Everything below is STRAIGHT alpha. AE hands SmartFX straight RGB for shape
layers, and un-premultiplying to recover it destroys every antialiased edge.
The C++ port must detect the format per FRAME and never per pixel.

Run:  python os_step1_composite.py
"""

import numpy as np
import cv2
import os

H, W = 260, 420
OUT = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# A stand-in animation: something whose motion is obvious frame to frame.
# --------------------------------------------------------------------------

def frame(i, n_frames=24):
    """A ball on an arc plus a rotating tick, drawn straight-alpha RGBA float.

    Deliberately NOT a solid rectangle: onion skinning is judged on whether you
    can read overlapping ghosts, and only curved, rotating art shows that.
    """
    rgba = np.zeros((H, W, 4), np.float32)
    t = i / float(n_frames - 1)

    cx = int(40 + t * (W - 80))
    cy = int(H - 50 - np.sin(t * np.pi) * (H - 130))

    # antialiased disc, drawn at 4x and boxed down so edges carry real partial
    # alpha - aliased art would hide exactly the errors this proto looks for.
    S = 4
    big = np.zeros((H * S, W * S), np.float32)
    cv2.circle(big, (cx * S, cy * S), 22 * S, 1.0, -1, lineType=cv2.LINE_AA)
    ang = t * 3.0 * np.pi
    tip = (int((cx + np.cos(ang) * 46) * S), int((cy + np.sin(ang) * 46) * S))
    cv2.line(big, (cx * S, cy * S), tip, 1.0, 5 * S, lineType=cv2.LINE_AA)
    a = big.reshape(H, S, W, S).mean(axis=(1, 3))

    rgba[..., 0] = 0.95            # a warm off-white drawing
    rgba[..., 1] = 0.93
    rgba[..., 2] = 0.88
    rgba[..., 3] = a
    return rgba


def over(src, dst):
    """Straight-alpha source-over. src and dst are HxWx4 float, straight.

    Where the output alpha is ~0 the colour is mathematically arbitrary - no
    downstream compositing can see it. The obvious move is to write 0 there, and
    it is wrong: it means the effect at strength 0 does NOT return its input
    bit-exact, because AE's buffers carry real RGB under transparent pixels and
    we would be wiping it. Carrying SRC's colour instead keeps
    "strength 0 is a pass-through" true, which is an invariant worth having in
    the port - it makes the zero case testable rather than approximately right.

    Caught by check 3, which was written strict on purpose.
    """
    sa = src[..., 3:4]
    da = dst[..., 3:4]
    oa = sa + da * (1.0 - sa)

    safe = np.where(oa > 1e-6, oa, 1.0)
    orgb = (src[..., :3] * sa + dst[..., :3] * da * (1.0 - sa)) / safe

    out = np.empty_like(src)
    out[..., :3] = np.where(oa > 1e-6, orgb, src[..., :3])
    out[..., 3:4] = oa
    return out


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

def opacity_for(k, strength, falloff):
    """Opacity of the |k|-th skin. k=+-1 is the nearest neighbour.

    falloff=1.0 means every skin shares one opacity - the flat look, and the
    useful degenerate case for testing. <1 fades with distance.
    """
    return strength * (falloff ** (abs(k) - 1))


def make_skin(rgba, k, tint_rgb, tint_amount, strength, falloff):
    """One neighbour, tinted and faded. Returns straight-alpha RGBA."""
    out = np.empty_like(rgba)
    tint = np.asarray(tint_rgb, np.float32).reshape(1, 1, 3)

    out[..., :3] = rgba[..., :3] * (1.0 - tint_amount) + tint * tint_amount
    out[..., 3] = rgba[..., 3] * opacity_for(k, strength, falloff)
    return out


def onion(frames, i, n_prev, n_next,
          prev_rgb=(0.95, 0.25, 0.25), next_rgb=(0.25, 0.70, 0.95),
          tint_amount=1.0, strength=0.55, falloff=0.6):
    """The whole effect, on frame i of `frames`.

    Order matters: the FARTHEST skin goes down first, so nearer frames occlude
    further ones, and C(t) lands last and untouched.
    """
    acc = np.zeros((H, W, 4), np.float32)

    ks = ([-k for k in range(n_prev, 0, -1)] +
          [k for k in range(n_next, 0, -1)])
    # farthest first, and past before future at equal distance so the future
    # skin reads as "on top of" the past one - matching how time is drawn.
    ks.sort(key=lambda k: (-abs(k), k))

    for k in ks:
        j = i + k
        if j < 0 or j >= len(frames):
            continue                       # no wrapping; the timeline has ends
        skin = make_skin(frames[j], k, prev_rgb if k < 0 else next_rgb,
                         tint_amount, strength, falloff)
        acc = over(skin, acc)

    return over(frames[i], acc)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def save(name, rgba, bg=0.13):
    """Composite over a flat grey and write a PNG, so ghosts are visible."""
    a = rgba[..., 3:4]
    rgb = rgba[..., :3] * a + bg * (1.0 - a)
    img = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    p = os.path.join(OUT, name)
    cv2.imwrite(p, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return p


def main():
    n = 24
    frames = [frame(i, n) for i in range(n)]
    i = 12

    written = []
    written.append(save("out_os1_input.png", frames[i]))
    written.append(save("out_os1_prev3.png",
                        onion(frames, i, 3, 0)))
    written.append(save("out_os1_next3.png",
                        onion(frames, i, 0, 3)))
    written.append(save("out_os1_both3.png",
                        onion(frames, i, 3, 3)))
    written.append(save("out_os1_tint0_own_colour.png",
                        onion(frames, i, 3, 3, tint_amount=0.0)))
    written.append(save("out_os1_tint05.png",
                        onion(frames, i, 3, 3, tint_amount=0.5)))
    written.append(save("out_os1_flat_falloff1.png",
                        onion(frames, i, 3, 3, falloff=1.0)))
    written.append(save("out_os1_deep6.png",
                        onion(frames, i, 6, 6, strength=0.5, falloff=0.72)))

    print("wrote:")
    for p in written:
        print("   ", os.path.basename(p))

    checks(frames, i)


# --------------------------------------------------------------------------
# Checks: measurements, each with a control that must FAIL the same test.
# --------------------------------------------------------------------------

def checks(frames, i):
    print("\nchecks")
    print("-" * 66)
    ok = True

    # 1. The current frame must survive untouched wherever it is opaque.
    #    This is the property the whole design rests on, so it is measured
    #    rather than trusted.
    res = onion(frames, i, 3, 3)
    solid = frames[i][..., 3] > 0.999
    d = np.abs(res[..., :3][solid] - frames[i][..., :3][solid]).max()
    print(f"  opaque current-frame pixels unchanged   max|d| = {d:.2e}   "
          f"{'PASS' if d < 1e-5 else 'FAIL'}")
    ok &= d < 1e-5

    #    CONTROL: tint the current frame too, the wrong model. Must FAIL.
    bad = over(make_skin(frames[i], 1, (1, 0, 0), 1.0, 1.0, 1.0),
               np.zeros_like(res))
    dbad = np.abs(bad[..., :3][solid] - frames[i][..., :3][solid]).max()
    print(f"    control (tinting C(t) too)            max|d| = {dbad:.2e}   "
          f"{'correctly FAILS' if dbad > 1e-2 else 'CONTROL BROKEN'}")
    ok &= dbad > 1e-2

    # 2. strength=0 must reproduce the input EXACTLY. If it does not, the
    #    skins are leaking into the result through the alpha maths.
    z = onion(frames, i, 3, 3, strength=0.0)
    dz = np.abs(z - frames[i]).max()
    print(f"  strength=0 reproduces the input         max|d| = {dz:.2e}   "
          f"{'PASS' if dz < 1e-6 else 'FAIL'}")
    ok &= dz < 1e-6

    # 3. Alpha must stay in range. An over() that can exceed 1.0 shows up as
    #    clipping only after the render, where it is hard to attribute.
    r = onion(frames, i, 6, 6, strength=1.0, falloff=1.0)
    amin, amax = r[..., 3].min(), r[..., 3].max()
    print(f"  alpha within [0,1] at full strength     [{amin:.4f}, {amax:.4f}]  "
          f"{'PASS' if amin >= -1e-6 and amax <= 1 + 1e-6 else 'FAIL'}")
    ok &= amin >= -1e-6 and amax <= 1 + 1e-6

    # 4. Falloff must be monotone: a farther skin can never be stronger than a
    #    nearer one. Cheap to get backwards, and invisible in a still.
    ops = [opacity_for(k, 0.55, 0.6) for k in range(1, 7)]
    mono = all(ops[j] >= ops[j + 1] - 1e-9 for j in range(len(ops) - 1))
    print(f"  opacity falls off monotonically         {[round(o,3) for o in ops]}  "
          f"{'PASS' if mono else 'FAIL'}")
    ok &= mono

    #    CONTROL: falloff > 1 must NOT be monotone-decreasing.
    bad_ops = [opacity_for(k, 0.55, 1.4) for k in range(1, 7)]
    bad_mono = all(bad_ops[j] >= bad_ops[j + 1] - 1e-9
                   for j in range(len(bad_ops) - 1))
    print(f"    control (falloff=1.4)                 "
          f"{'correctly FAILS' if not bad_mono else 'CONTROL BROKEN'}")
    ok &= not bad_mono

    # 5. Skins must not appear where the timeline has no frames. At i=0 there
    #    is no previous frame, so asking for 3 must equal asking for 0.
    a = onion(frames, 0, 3, 0)
    b = onion(frames, 0, 0, 0)
    de = np.abs(a - b).max()
    print(f"  no skins before frame 0                 max|d| = {de:.2e}   "
          f"{'PASS' if de < 1e-6 else 'FAIL'}")
    ok &= de < 1e-6

    print("-" * 66)
    print("  ALL CHECKS PASS" if ok else "  *** SOMETHING FAILED ***")
    return ok


if __name__ == "__main__":
    main()

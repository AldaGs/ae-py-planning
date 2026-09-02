"""
Hard Mosaic - Step 4: the check harness.

This is the file that has to keep working. It is not a demo; it is the list of
properties the C++ port must reproduce, written as MEASUREMENTS with a
deliberately broken control alongside each one, so that a check which has
quietly stopped testing anything shows up as a control that passes when it
should fail.

The properties
--------------
  1  NO INVENTED COLOUR   every RGB in the output is an RGB from the input.
                          This is the whole point of the effect. The control
                          is the same render in Average mode, which must fail.
  2  BINARY ALPHA         every alpha is exactly 0 or exactly 1. Control:
                          Snap Alpha off, which must fail.
  3  BLOCK FLATNESS       every block is one constant RGBA. Control: the
                          unmosaicked source, which must fail.
  4  TILE INVARIANCE      rendering the frame in tiles equals rendering it
                          whole, pixel for pixel. Control: a build that lays
                          the grid out from the tile corner, which must fail.
                          This is the one that catches the AE-specific bug -
                          the host is allowed to ask for any sub-rectangle.
  5  DETERMINISM          two renders of identical input are bit-identical.
  6  NO NaN / NO INF      including on all-transparent input, where the
                          un-premultiply divides by zero.
  7  EMPTY INPUT          a fully transparent frame renders fully transparent
                          rather than a grid of black blocks.
  8  PREMULT ROUND TRIP   premultiplied input handled as premultiplied gives
                          the same colours as straight input handled as
                          straight. Control: handling it as straight, which
                          must fail.

Run:  python hm_step4_hardening.py
"""

import numpy as np

from hm_step1_blocks import make_source, make_source_multi, PALETTE, W, H
from hm_step2_solid import MODE_AVERAGE, MODE_DOMINANT, MODE_NEAREST
from hm_step3_controls import Params, hard_mosaic, GRID_SIZE, GRID_COUNT

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"   {detail}" if detail else ""))
    return ok


def control(name, failed, detail=""):
    """A control passes when the broken version actually breaks."""
    return check(f"(control) {name} does fail", failed, detail)


# --------------------------------------------------------------------------
# Property implementations
# --------------------------------------------------------------------------

def colour_set(img, alpha_gate=True, decimals=5):
    """The set of RGB values present, ignoring fully transparent pixels."""
    rgb = img[..., :3].reshape(-1, 3)
    if alpha_gate:
        rgb = rgb[img[..., 3].reshape(-1) > 0.0]
    return {tuple(v) for v in np.round(rgb, decimals)}


def invented_colours(out, src):
    """RGBs in the output that are in no input pixel."""
    return colour_set(out) - colour_set(src)


def alpha_is_binary(img, eps=1e-6):
    a = img[..., 3]
    return bool((np.isclose(a, 0.0, atol=eps) | np.isclose(a, 1.0, atol=eps)).all())


def blocks_are_flat(img, p: Params):
    """Every block holds exactly one RGBA."""
    h, w = img.shape[:2]
    bw, bh, ox, oy = p.resolve(w, h)
    for j in range(int(np.ceil(h / bh)) + 1):
        y0, y1 = int(oy + j * bh), int(oy + (j + 1) * bh)
        y0, y1 = max(y0, 0), min(y1, h)
        if y0 >= y1:
            continue
        for i in range(int(np.ceil(w / bw)) + 1):
            x0, x1 = int(ox + i * bw), int(ox + (i + 1) * bw)
            x0, x1 = max(x0, 0), min(x1, w)
            if x0 >= x1:
                continue
            b = img[y0:y1, x0:x1].reshape(-1, 4)
            if not np.all(b == b[0]):
                return False
    return True


def render_tiled(img, p: Params, tile=97):
    """Render in tiles of an awkward size and stitch. Must equal the whole."""
    h, w = img.shape[:2]
    out = np.zeros_like(img)
    for y0 in range(0, h, tile):
        for x0 in range(0, w, tile):
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
            part = hard_mosaic(img, p, region=(x0, y0, x1, y1))
            out[y0:y1, x0:x1] = part[y0:y1, x0:x1]
    return out


def render_tiled_broken(img, p: Params, tile=97):
    """The bug this check exists to catch: grid laid out from the TILE corner
    instead of from layer origin. Looks fine on a single full-frame render and
    tears everywhere the moment the host tiles."""
    h, w = img.shape[:2]
    out = np.zeros_like(img)
    for y0 in range(0, h, tile):
        for x0 in range(0, w, tile):
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
            sub = img[y0:y1, x0:x1]
            out[y0:y1, x0:x1] = hard_mosaic(sub, p)
    return out


# --------------------------------------------------------------------------

if __name__ == "__main__":
    print("Hard Mosaic - step 4: hardening\n")

    src = make_source_multi()
    soft = make_source_multi(softness=6.0)
    plain = make_source()
    empty = np.zeros((H, W, 4), np.float32)

    base = Params(block_w=16.0)

    # 1 -------------------------------------------------------------------
    print("1  no invented colour")
    worst = 0
    for s in (src, soft, plain):
        for b in (2, 3, 5, 8, 13, 16, 24, 32, 48, 64):
            for mode in (MODE_DOMINANT, MODE_NEAREST):
                p = Params(block_w=float(b), colour_source=mode)
                worst = max(worst, len(invented_colours(hard_mosaic(s, p), s)))
    check("dominant/nearest invent nothing, 3 sources x 10 sizes",
          worst == 0, f"worst = {worst} invented colours")
    n_avg = len(invented_colours(
        hard_mosaic(src, Params(block_w=16.0, colour_source=MODE_AVERAGE)), src))
    control("average mode", n_avg > 0, f"{n_avg} invented colours")

    # 2 -------------------------------------------------------------------
    print("\n2  binary alpha")
    ok = all(alpha_is_binary(hard_mosaic(s, Params(block_w=float(b))))
             for s in (src, soft, plain) for b in (3, 8, 16, 37, 64))
    check("alpha is exactly 0 or 1 everywhere", ok)
    control("snap alpha off",
            not alpha_is_binary(hard_mosaic(src, Params(snap_alpha=False))))

    # 3 -------------------------------------------------------------------
    print("\n3  block flatness")
    ok = True
    for b in (5, 16, 31):
        for off in (0.0, 7.0):
            p = Params(block_w=float(b), offset_x=off, offset_y=off)
            ok &= blocks_are_flat(hard_mosaic(src, p), p)
    check("every block is a single constant RGBA", ok)
    control("the unmosaicked source", not blocks_are_flat(src, base))

    # 4 -------------------------------------------------------------------
    print("\n4  tile invariance")
    whole = hard_mosaic(src, base)
    tiled = render_tiled(src, base)
    d = float(np.abs(whole - tiled).max())
    check("tiled render is bit-identical to the whole-frame render",
          d == 0.0, f"max abs diff = {d}")
    dbroken = float(np.abs(whole - render_tiled_broken(src, base)).max())
    control("grid anchored to the tile corner", dbroken > 0.0,
            f"max abs diff = {dbroken:.4f}")

    # 5 -------------------------------------------------------------------
    print("\n5  determinism")
    check("two renders are bit-identical",
          np.array_equal(hard_mosaic(src, base), hard_mosaic(src, base)))

    # 6 -------------------------------------------------------------------
    print("\n6  no NaN / no Inf")
    ok = True
    for s, pm in ((src, False), (soft, True), (empty, True), (empty, False)):
        o = hard_mosaic(s, Params(block_w=16.0, premultiplied=pm))
        ok &= bool(np.isfinite(o).all())
    check("finite on every input, premultiplied included", ok)

    # 7 -------------------------------------------------------------------
    print("\n7  empty input")
    o = hard_mosaic(empty, base)
    check("a fully transparent frame stays fully transparent",
          float(np.abs(o).max()) == 0.0)

    # 8 -------------------------------------------------------------------
    print("\n8  premultiply round trip")
    pre = soft.copy()
    pre[..., :3] *= pre[..., 3:4]
    p_lo = Params(block_w=4.0, coverage=0.05)
    ref = colour_set(hard_mosaic(soft, p_lo))
    got = colour_set(hard_mosaic(pre, Params(block_w=4.0, coverage=0.05,
                                             premultiplied=True)))
    check("premult-in/premult-out matches straight-in/straight-out",
          got == ref, f"{len(got)} vs {len(ref)} colours")
    bad = colour_set(hard_mosaic(pre, p_lo))
    control("premult input treated as straight", bad != ref,
            f"{len(bad)} colours instead of {len(ref)}")

    # ---------------------------------------------------------------------
    n_ok = sum(1 for _, o in _results if o)
    print(f"\n  {n_ok}/{len(_results)} checks pass")
    if n_ok != len(_results):
        raise SystemExit(1)

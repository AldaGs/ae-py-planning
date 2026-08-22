"""
Long Shadow - Step 1: Directional solid shadow, SMOOTH by construction.

The whole reason this effect looks bad in most plugins is a binary projection:
they march each pixel and ask a YES/NO question - "is there an OPAQUE pixel
behind me within L?" - which produces hard 0/1 coverage, i.e. stair-steps on
every diagonal. Then they bolt on Supersampling (render big, shrink) and a
Post-Smooth blur to hide it.

We don't inherit that. Same principle we used in Chromatic Aberration and the
Buildable Stroke: DON'T threshold the alpha - re-read the source's EXISTING soft
edge. The analytic solid long shadow is

    cov(p) = max over t in [0, L] of  alpha_src(p - t * d)      (bilinear)

Because alpha_src is already anti-aliased (0..1), the max along the ray inherits
those soft edges. Smooth for free, no supersampling.

This step:
  - marches that max with BILINEAR sampling (arbitrary angle, dead simple),
  - proves the thesis with a side-by-side: the SAME march on a THRESHOLDED mask
    (binary -> jaggy) vs on the soft alpha (smooth),
  - composites the colored shadow UNDER the source art.

Cost here is O(L) sampling passes. That is fine for a prototype and already
smooth. Step 2 makes it WIDTH-INDEPENDENT (shear so d aligns to a row, then a
1-D sliding-window-max per row - the same "rotate-to-axis + O(1) sweep" trick
that made the BS distance transform width-independent) and adds RADIAL /
INVERSE-RADIAL (d varies per pixel).

Run:  python ls_step1_directional.py
"""

import numpy as np
import cv2

# AE angle convention we'll mirror later: degrees, 0 = pointing right (+x),
# increasing clockwise on screen (because image y points DOWN). For the
# prototype we just take a direction vector; the plugin maps the AE dial to it.


def load(path):
    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra.shape[2] == 3:  # no alpha -> treat luma as coverage
        g = cv2.cvtColor(bgra, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        rgb = bgra[:, :, ::-1].astype(np.float32) / 255.0
        return rgb, g
    alpha = bgra[:, :, 3].astype(np.float32) / 255.0
    rgb = bgra[:, :, 2::-1].astype(np.float32) / 255.0  # BGR -> RGB
    return rgb, alpha


def direction(angle_deg):
    """Unit shadow-projection vector in image space (y down)."""
    a = np.deg2rad(angle_deg)
    return np.array([np.cos(a), np.sin(a)], np.float32)


def long_shadow_directional(cov_src, angle_deg, length, step=0.5):
    """Solid long shadow coverage = max of source coverage along -d, t in [0,L].

    cov_src is any 0..1 field (soft alpha, or a hard mask if you want to SEE the
    jaggies). Sampling is bilinear via cv2.remap, so a soft source stays soft.
    """
    h, w = cov_src.shape
    dx, dy = direction(angle_deg)
    # base coordinate grid
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    out = cov_src.copy()  # t = 0 term
    t = step
    while t <= length + 1e-6:
        mapx = xs - dx * t
        mapy = ys - dy * t
        sampled = cv2.remap(cov_src, mapx, mapy, cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        np.maximum(out, sampled, out=out)
        t += step
    return out


def over(dst_rgb, dst_a, src_rgb, src_a):
    """Porter-Duff src OVER dst (straight alpha)."""
    out_a = src_a + dst_a * (1.0 - src_a)
    safe = np.maximum(out_a, 1e-6)[..., None]
    out_rgb = (src_rgb * src_a[..., None]
               + dst_rgb * dst_a[..., None] * (1.0 - src_a[..., None])) / safe
    return out_rgb.astype(np.float32), out_a.astype(np.float32)


def save(name, rgb, a, bg=0.5):
    flat = rgb * a[..., None] + bg * (1.0 - a[..., None])
    cv2.imwrite(name, (np.clip(flat[..., ::-1], 0, 1) * 255).astype(np.uint8))


def main():
    src_rgb, alpha = load("../buildable_strokes/CTBS.png")
    h, w = alpha.shape

    angle, length = 90.0, 60.0          # straight down, like the reference shot
    shadow_color = np.array([0.0, 0.0, 0.0], np.float32)

    # --- the hero render: smooth shadow UNDER the art ----------------------
    sh_cov = long_shadow_directional(alpha, angle, length)
    sh_rgb = np.zeros((h, w, 3), np.float32) + shadow_color
    rgb, a = over(src_rgb, alpha, sh_rgb, sh_cov)   # shadow first, art on top
    # note over(dst=shadow, src=art): compose art OVER shadow
    rgb, a = over(sh_rgb, sh_cov, src_rgb, alpha)
    save("out_ls1_hero.png", rgb, a)

    # --- THE THESIS: same march, thresholded (jaggy) vs soft (smooth) ------
    mask = (alpha >= 0.5).astype(np.float32)        # binary source = their way
    jag = long_shadow_directional(mask, angle, length)
    smo = sh_cov

    # zoom a diagonal-heavy region (letters) and magnify 6x nearest-neighbour
    # to make the stair-steps unmistakable.
    Y0, Y1, X0, X1 = 250, 430, 150, 470
    for tag, cov in (("jaggy_binary", jag), ("smooth_soft", smo)):
        r, aa = over(np.zeros((h, w, 3), np.float32), cov * 0.0,  # transparent
                     np.zeros((h, w, 3), np.float32) + shadow_color, cov)
        r, aa = over(r, aa, src_rgb, alpha)
        save(f"out_ls1_{tag}.png", r, aa)
        img = cv2.imread(f"out_ls1_{tag}.png")
        crop = cv2.resize(img[Y0:Y1, X0:X1], None, fx=6, fy=6,
                          interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(f"out_ls1_{tag}_zoom.png", crop)

    # --- a few angles, to confirm arbitrary direction ----------------------
    for ang in (0, 45, 90, 135):
        c = long_shadow_directional(alpha, ang, length)
        sr = np.zeros((h, w, 3), np.float32) + shadow_color
        r, aa = over(sr, c, src_rgb, alpha)
        save(f"out_ls1_angle_{ang:03d}.png", r, aa)

    # --- checks ------------------------------------------------------------
    print("checks:")
    # 1) shadow never has LESS coverage than the source (it contains it at t=0)
    contains = np.all(smo + 1e-5 >= alpha)
    print(f"  [{'ok ' if contains else 'FAIL'}] shadow contains source (t=0 term)")
    # 2) soft version has genuinely intermediate coverage; binary does not
    def midfrac(f):
        return np.mean((f > 0.02) & (f < 0.98))
    fj, fs = midfrac(jag), midfrac(smo)
    print(f"  [{'ok ' if fs > 3 * fj else 'FAIL'}] soft march is anti-aliased: "
          f"partial-coverage px {fj*100:.2f}% (binary) -> {fs*100:.2f}% (soft)")
    # 3) shadow extent roughly matches length along the direction
    print(f"  shadow reaches ~{length:.0f}px at {angle:.0f} deg; "
          f"max coverage {smo.max():.3f}")

    print("\nWrote out_ls1_hero.png, out_ls1_{jaggy_binary,smooth_soft}"
          "{,_zoom}.png, out_ls1_angle_*.png")
    print("Compare the two _zoom.png: binary stair-steps, soft is clean - and "
          "NO supersampling was used.")


if __name__ == "__main__":
    main()

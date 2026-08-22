"""
Long Shadow - Step 3: FADE (in/out) + the COLOR / TINT / OPACITY model.

Steps 1-2 gave us WHERE the shadow is (coverage). This step gives it a look:

FADE. A shadow can ramp opacity along its length. The multiplier depends on how
far a shadow pixel sits FROM THE OBJECT along the projection direction:
    s = clamp(fadeDist / L, 0, 1)     # 0 at the object edge, 1 at the tip
    ramp(s) = lerp(fadeIn, fadeOut, s)
So fadeIn is the opacity where the shadow LEAVES the object and fadeOut is the
opacity at the TIP. Both 1.0 = no fade (the reference default). fadeOut=0 fades
to nothing at the tip; fadeIn=0 makes it grow OUT of the object. Getting fadeDist
means the pure max-along-ray (step 2's window trick) isn't enough - we also need
the DISTANCE at which each ray first hits the object. So fade uses a march that
returns (coverage, fadeDist, hit-point) in one pass. This is exactly the "fade
breaks the window-max, fall back to march" boundary we flagged.

COLOR / TINT. The hit-point also lets the shadow inherit the OBJECT's colour:
    objColor(p) = source_rgb at the object pixel this ray hit
    shadowRGB   = lerp(objColor, shadowColor, tintAmount)
tint=1 -> flat shadow colour (the usual long-shadow look); tint=0 -> the shadow
keeps the object's own colours (a projected silhouette copy); in between tints a
coloured object toward the shadow hue. Final shadow alpha = coverage * ramp *
opacity.

(Object Color = recolouring the SOURCE art itself, and Soft Shadow, stay v2.)

Run:  python ls_step3_fade_color.py
"""

import numpy as np
import cv2
from ls_step1_directional import load, direction, over, save


def sweep_directional(alpha, src_rgb, angle_deg, length, thresh=0.5, step=0.5):
    """One march returning coverage, fade-distance, and object-hit colour.

    coverage : max soft alpha along -d, t in [0, L]      (the smooth silhouette)
    fadeDist : t at which the ray FIRST hits the object   (0 at edge .. L at tip)
    objColor : source RGB sampled at that first-hit point (drives the tint)
    """
    h, w = alpha.shape
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    dx, dy = direction(angle_deg)

    cov = alpha.copy()
    fade = np.full((h, w), np.inf, np.float32)
    hitx, hity = xs.copy(), ys.copy()          # for t=0 hits (the object itself)
    hit0 = alpha >= thresh
    fade[hit0] = 0.0

    t = step
    while t <= length + 1e-6:
        mapx, mapy = xs - dx * t, ys - dy * t
        sampled = cv2.remap(alpha, mapx, mapy, cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        np.maximum(cov, sampled, out=cov)
        newhit = (sampled >= thresh) & ~np.isfinite(fade)
        fade[newhit] = t
        hitx[newhit] = mapx[newhit]
        hity[newhit] = mapy[newhit]
        t += step

    # sub-threshold soft skirt that never crossed the object: treat it as tip.
    fade[~np.isfinite(fade)] = length
    objColor = cv2.remap(src_rgb, hitx, hity, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
    return cov, fade, objColor


def shade(cov, fade, objColor, length, *, shadow_color, tint=1.0, opacity=1.0,
          fade_in=1.0, fade_out=1.0):
    """Turn (coverage, fadeDist, objColor) into premultiply-ready RGB + alpha."""
    s = np.clip(fade / max(length, 1e-6), 0.0, 1.0)
    ramp = fade_in + (fade_out - fade_in) * s              # opacity along length
    sc = np.asarray(shadow_color, np.float32)
    rgb = objColor * (1.0 - tint) + sc * tint             # tint toward shadow hue
    a = (cov * ramp * opacity).astype(np.float32)
    return rgb.astype(np.float32), a


def main():
    src_rgb, alpha = load("../buildable_strokes/CTBS.png")
    h, w = alpha.shape
    angle, length = 90.0, 80.0

    cov, fade, objc = sweep_directional(alpha, src_rgb, angle, length)

    def compose(rgb, a, name):
        out_rgb, out_a = over(rgb, a, src_rgb, alpha)      # art on top of shadow
        save(name, out_rgb, out_a)

    # --- fade variants -----------------------------------------------------
    # no fade (baseline)
    r, a = shade(cov, fade, objc, length, shadow_color=(0, 0, 0))
    compose(r, a, "out_ls3_nofade.png")
    # fade OUT toward the tip
    r, a = shade(cov, fade, objc, length, shadow_color=(0, 0, 0), fade_out=0.0)
    compose(r, a, "out_ls3_fadeout.png")
    # fade IN out of the object (transparent at edge -> opaque at tip)
    r, a = shade(cov, fade, objc, length, shadow_color=(0, 0, 0), fade_in=0.0)
    compose(r, a, "out_ls3_fadein.png")

    # --- tint variants -----------------------------------------------------
    # tint=1 flat shadow colour (teal), tint=0 keeps object colours
    r, a = shade(cov, fade, objc, length, shadow_color=(0.05, 0.35, 0.4), tint=1.0)
    compose(r, a, "out_ls3_tint1_teal.png")
    r, a = shade(cov, fade, objc, length, shadow_color=(0.05, 0.35, 0.4), tint=0.0)
    compose(r, a, "out_ls3_tint0_objcolor.png")
    r, a = shade(cov, fade, objc, length, shadow_color=(0.05, 0.35, 0.4), tint=0.5,
                 fade_out=0.15, opacity=0.85)
    compose(r, a, "out_ls3_combo.png")

    # --- checks ------------------------------------------------------------
    print("checks:")
    # fadeDist monotonic along the shadow: at the object edge ~0, at tip ~L
    edge = fade[(cov > 0.9) & (alpha < 0.5)]              # shadowed, not object
    print(f"  fadeDist range on shadow: {fade[cov>0.5].min():.1f} .. "
          f"{fade[cov>0.5].max():.1f} (L={length:.0f})")

    # fade_out=0 must drive tip opacity to ~0 while keeping the object edge solid
    _, a0 = shade(cov, fade, objc, length, shadow_color=(0, 0, 0), fade_out=0.0)
    _, a1 = shade(cov, fade, objc, length, shadow_color=(0, 0, 0))
    tip = (fade > 0.8 * length) & (cov > 0.5)
    root = (fade < 0.2 * length) & (cov > 0.5)
    ok_fade = a0[tip].mean() < 0.4 * a1[tip].mean() and \
              a0[root].mean() > 0.8 * a1[root].mean()
    print(f"  [{'ok ' if ok_fade else 'FAIL'}] fade_out=0 dims tip "
          f"({a1[tip].mean():.2f}->{a0[tip].mean():.2f}) keeps root "
          f"({a1[root].mean():.2f}->{a0[root].mean():.2f})")

    # tint=0 must reproduce object colours in the shadow; tint=1 must not
    r0, _ = shade(cov, fade, objc, length, shadow_color=(0, 0, 0), tint=0.0)
    r1, _ = shade(cov, fade, objc, length, shadow_color=(0, 0, 0), tint=1.0)
    # source has red/blue strokes, so a tint=0 shadow should be more saturated
    sat0 = (r0.max(2) - r0.min(2))[cov > 0.5].mean()
    sat1 = (r1.max(2) - r1.min(2))[cov > 0.5].mean()
    print(f"  [{'ok ' if sat0 > sat1 else 'FAIL'}] tint=0 inherits object colour "
          f"(saturation {sat1:.3f}@tint1 -> {sat0:.3f}@tint0)")

    print("\nWrote out_ls3_{nofade,fadeout,fadein}.png, "
          "out_ls3_{tint1_teal,tint0_objcolor,combo}.png")


if __name__ == "__main__":
    main()

"""
Gradient Map - Step 2: The ramp and the LUT (turning a tone into a color).

Goal: Step 1 gave us t in [0,1] per pixel. Now we build the OTHER half of a
gradient map: a color ramp that answers "given tone t, what color?". We do it
the way the real plugin will:

    color stops  --(interpolate once)-->  256-entry LUT  --(look up per pixel)-->  color

Why a LUT (look-up table) instead of interpolating at every pixel?
    A ramp is defined by a few color STOPS (e.g. black@0.0, teal@0.5, cream@1.0).
    To color a pixel you must find which two stops t falls between and blend them.
    That search + blend is real work. But t only has 256 meaningful levels in
    8-bit (and you can afford, say, 1024 for float). So do the work ONCE: fill a
    table[256] with the ramp color at every level. Then per pixel it's a single
    array index - basically free. In the AE plugin this table is rebuilt only in
    PARAMS_SETUP / when a stop changes, NOT inside the render loop. That build-
    once/lookup-many split is the whole point of this step.

Run:  python gm_step2_ramp_lut.py
Then open the ramp strips and confirm the colors blend smoothly between stops.
"""

import numpy as np
import cv2


# A "stop" is (position in [0,1], color as R,G,B floats in [0,1]).
# We store colors as R,G,B (human order) and convert to OpenCV's B,G,R only at
# the moment we write pixels - keeps the ramp definition readable.
def make_stops(name):
    """A few preset gradients so you can see the LUT with different stop counts."""
    presets = {
        # Classic 2-stop duotone: dark teal shadows -> warm cream highlights.
        "duotone": [(0.0, (0.5, 0.10, 0.15)),
                    (1.0, (0.01, 0.15, 0.80))],
        # 3-stop: adds a magenta midtone -> shows non-linear hue travel.
        "tri":     [(0.0, (0.02, 0.02, 0.10)),
                    (0.5, (0.85, 0.10, 0.55)),
                    (1.0, (1.00, 0.85, 0.30))],
        # 5-stop "spectrum" -> stresses the between-stops search logic.
        "spectrum":[(0.00, (0.00, 0.00, 0.00)),
                    (0.25, (0.10, 0.20, 0.90)),
                    (0.50, (0.10, 0.80, 0.40)),
                    (0.75, (0.95, 0.80, 0.10)),
                    (1.00, (1.00, 1.00, 1.00))],
    }
    return presets[name]


def build_lut(stops, n=256):
    """Interpolate color stops into an (n, 3) table of RGB floats.

    This is the 'do the work once' function. For each of the n levels we compute
    tone t = i/(n-1), find the stop interval [s0, s1] containing t, and linearly
    blend the two stop colors by how far t sits between them.

    Returns lut[i] = (R, G, B) float for tone level i.
    """
    stops = sorted(stops, key=lambda s: s[0])          # ensure ascending position
    positions = [p for p, _ in stops]
    colors = [np.array(c, dtype=np.float32) for _, c in stops]

    lut = np.zeros((n, 3), dtype=np.float32)
    for i in range(n):
        t = i / (n - 1)                                # this level's tone, 0..1

        # Clamp outside the stop range to the end colors (t below first / above last).
        if t <= positions[0]:
            lut[i] = colors[0]
            continue
        if t >= positions[-1]:
            lut[i] = colors[-1]
            continue

        # Find the interval: largest stop whose position <= t.
        k = 0
        while k < len(positions) - 1 and positions[k + 1] <= t:
            k += 1
        # Now positions[k] <= t < positions[k+1]. Blend factor along this segment.
        span = positions[k + 1] - positions[k]
        f = 0.0 if span == 0 else (t - positions[k]) / span   # 0 at s0, 1 at s1
        lut[i] = (1.0 - f) * colors[k] + f * colors[k + 1]     # linear RGB blend
    return lut


def lut_to_strip(lut, height=60):
    """Render an (n,3) LUT as a wide BGR image strip so we can look at it."""
    n = lut.shape[0]
    # RGB float -> BGR uint8. lut[:, ::-1] reverses the color axis (R,G,B -> B,G,R).
    row = (np.clip(lut[:, ::-1], 0, 1) * 255).astype(np.uint8)   # (n, 3)
    strip = np.tile(row[np.newaxis, :, :], (height, 1, 1))       # (height, n, 3)
    return strip


def main():
    for name in ("duotone", "tri", "spectrum"):
        stops = make_stops(name)
        lut = build_lut(stops, n=256)

        print(f"{name:9s}: {len(stops)} stops -> LUT shape {lut.shape}")
        # Spot-check a few levels so you trust the interpolation numerically.
        for level in (0, 64, 128, 192, 255):
            r, g, b = lut[level]
            print(f"   level {level:3d} (t={level/255:.2f}): "
                  f"RGB=({r:.2f},{g:.2f},{b:.2f})")

        strip = lut_to_strip(lut)
        cv2.imwrite(f"out_gm2_ramp_{name}.png", strip)

    print("\nWrote: out_gm2_ramp_{duotone,tri,spectrum}.png")
    print("These strips ARE the LUT, drawn left(t=0)->right(t=1). Next step feeds "
          "Step 1's per-pixel tone into this table to map a real image.")


if __name__ == "__main__":
    main()

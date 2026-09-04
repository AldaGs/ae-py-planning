# A1 — world + bake spine, on primitives

`python a1_primitives.py` → 13/13 checks, `a1_checks.png`, `a1_bake.json`.

Goal was the spine: scene → pymunk world → fixed-step sim → per-frame sample →
AE keyframe JSON, on hardcoded boxes and a circle. No AE, no C++, no panel.

## Conventions this step nailed down

**Simulate in a y-down frame.** x right, y down, origin top-left, gravity `+y`.
Chipmunk does not care which way down is, and the choice pays off: in a y-down
frame a mathematically positive (CCW) angle reads *clockwise* on screen, which
is exactly AE's rotation sense. So `ae_degrees = degrees(body.angle)` with no
sign flip anywhere. Verified by measurement, not by argument — check 5c drives a
body to +90° and confirms an anchor that started 100px to the right ends up
100px *below*.

**`pixels_per_meter = 100`.** A 1920×1080 comp becomes a 19.2 × 10.8 m room.

**Frames land on substep boundaries.** `dt = 1/(fps · substeps)`, 8 substeps at
24fps. Nothing is ever interpolated, which removes a whole class of run-to-run
difference.

**Bake schema `ae-physics-bake/1`** — this is what Phase B will produce from real
layers and consume when writing keyframes, so it is worth being fussy about now.

## What got falsified

Two predictions from the plan were wrong, and both were wrong in ways worth
keeping.

**The integrator is explicit, not semi-implicit.** The plan assumed Chipmunk
does `v += g·dt` then `x += v·dt`, which overshoots the closed form by
`0.5·g·dt·t`. The measurement came back off by *twice* that, in the other
direction — which pins the ordering: Chipmunk advances position with the
velocity from *before* the gravity update, accumulating `g·dt²·n(n−1)/2` and
**undershooting** by `0.5·g·dt·t`. At 24fps × 8 substeps that is 2.55px per
second of fall. Small, but it is a systematic bias, not noise, and it is now
matched to 3e-7 px.

**Wall D's upper half did not reproduce where predicted.** The reasoning was:
`collision_slop` is 0.1 *world* units, so pixel sink should grow linearly with
ppm and objects should visibly interpenetrate at high ppm. Measured:

| ppm | fall in 1s | rest sink |
|---|---|---|
| 1 | 4.87 px | +0.00 px |
| 10 | 48.74 px | +0.08 px |
| 100 | 487.45 px | +1.83 px |
| 1000 | 4874.48 px | +1.97 px |
| 10000 | 48744.79 px | +15.69 px |

Sink is *flat* from ppm=100 to ppm=1000 — the solver resolves far tighter than
slop allows, so slop is not the limiter. The upper wall is real but sits two
decades higher than predicted and looks like precision, not slop.

The half that did hold is the low end, and it is the half that matters
artistically: ppm is the comp's clock. At ppm=1 a fall down the height of the
frame takes **14.8 s**; at ppm=100 it takes **1.48 s**. That is the difference
between underwater and gravity.

**Usable band: ppm 10–1000.** 100 is comfortably in the middle.

## What A1 confirms

- **Determinism (Wall H).** Two runs are byte-identical at 71,205 bytes. The
  broken control — a 1e-9 px nudge to one starting position — does change the
  output, so the comparison is capable of failing.
- **Rotation unwrapping (Wall F).** Two turns bake as 720.000°, monotonic, no
  snap-backs. The control plots the raw wrapped angle, which sawtooths at ±180 —
  that sawtooth is exactly what AE would show without unwrapping.
- **Anchor transform (Wall E).** `position = com_world + R(θ)·(anchor − com)` is
  implemented and an offset anchor orbits the COM at exactly 100.000000 px.
  A2 still owns this — here both bodies are symmetric primitives whose COM is
  their centre, so nothing yet tests a COM that is *not* obvious.

Resting positions match geometry: bodies land at y=831 against floor 900 minus
half-height 70, and box_b stacks at 693 ≈ 831 − 140.

## Noted for later

`a1_bake.json` is 71 KB for 3 bodies × 121 frames. Extrapolating to 40 layers ×
300 frames gives roughly 2.3 MB of JSON. Not a problem yet, but Wall I
(keyframe volume) will want compact separators and probably fewer decimals.

## Files

- `sim.py` — scene dataclasses, world construction, stepping, sampling, bake
- `a1_primitives.py` — the A1 checks and plots

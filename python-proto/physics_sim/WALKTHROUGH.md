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

---

# A2 — layer space vs body space (Wall E)

`python a2_anchor_com.py` → 10/10 checks, `a2_checks.png`, `a2_bake.json`.

A1 only used symmetric primitives, whose COM is trivially their centre — which
hides the entire problem. A2's test shape is a thin L built from two convex
parts, and its centre of mass lands at layer-space **(60, 160)**, which is
**outside the material**: the layer rotates about a point that is not on it.
It sits 56.6 px from the bounding-box centre, so anyone reaching for the bbox
centre is wrong by that much on every frame.

## The two transforms

AE describes a layer as content in layer space plus an anchor plus a Position
saying where that anchor sits. The solver describes a body as a COM with
content hung off it. Converting is Wall E, and it runs both ways:

```
into the solver:  com_world = position  + R(theta) * (com_layer - anchor)
out to AE:        position  = com_world + R(theta) * (anchor - com_layer)
```

Being exact inverses is what makes the round-trip check possible: feed in a
position, bake frame 0, get the same position back. Over four cases including
±180° and an anchor placed exactly on the COM, the error is **0.000e+00 px**.

## The headline check: replay

The question that actually matters is *does AE, handed only our keyframes,
draw what the solver simulated?* So the check rebuilds every polygon the way AE
would — place the anchor at Position, rotate content about it — and compares
against the solver's own vertices.

Worst error over 91 frames × 2 layers: **2.97e-06 px**. And the error floor is
*keyframe rounding*, not the transform — the schema stores 6 decimal places, and
1e-6° of rotation across a ~250px shape is a few microns of vertex movement. The
transform itself contributes nothing measurable.

The broken control drops the anchor offset (treats the COM as the anchor — the
single likeliest way to get this wrong) and the error becomes **170.9 px**.

## Mass properties, computed twice

`geom.py` does the polygon math by hand — shoelace area, area centroid, and the
second moment `I = (rho/12)·Σ cross(Pi,Pj)·(Pi·Pi + Pi·Pj + Pj·Pj)` — then
subtracts `m·d²` to move it to the centroid. Cross-checked against
`pymunk.moment_for_poly` on a triangle: **relative difference 0.0**, exactly.

Compound bodies (several convex parts sharing one rigid body, combined by
area-weighted COM and the parallel axis theorem) are built here rather than in
A3, because that is precisely what A3's convex decomposition will hand us.

## Falsified again

One predicted number was wrong. I expected a spinning body's anchor to deviate
from its *starting* x by the offset radius (170.9 px); it deviates by 230.8.
The starting position is not the extreme of the circle, so that quantity is not
an invariant at all — it depends on where in the rotation the sim began. The
real invariant is the **span**, which over at least one full turn is a diameter:
measured 341.6 px against a predicted 341.8.

Meanwhile the COM holds its x to **1.5e-06 px** across 73 frames, as it must
with no horizontal force. That contrast is the whole of Wall E in one picture:
the COM is what obeys the physics, and AE's Position is a point corkscrewing
around it.

---

# A3 — bezier paths to bodies (Walls B and C, easy input)

`python a3_paths.py` → 15/15 checks, `a3_checks.png`.

Pipeline: **AE Shape → flatten → simplify → convex parts → PolyBody**.

## The AE format trap

An AE Path stores `vertices`, `inTangents`, `outTangents`, `closed` — and the
tangents are **relative to their own vertex**, not absolute control points:

```
P0 = v[i]                    P1 = v[i]   + outTangents[i]
P3 = v[i+1]                  P2 = v[i+1] + inTangents[i+1]
```

Reading them as absolute is checked as a broken control: a 200px circle's area
collapses to **42.7% of the truth** as the shape implodes toward the origin.

## Two errors that behave differently

Flattening has two error sources that are easy to conflate, and separating them
turned out to matter:

| | behaviour |
|---|---|
| **vertex radial error** | flat at 0.0545px regardless of tol |
| **area error** | linear in tol — 804 px² at tol=4 → 9.5 px² at tol=0.05 |

Flattened vertices lie *exactly on* the bezier, so their radial error is the
four-arc kappa circle's own approximation — measured 2.73e-04 of the radius,
which is the textbook 0.027%. Making the flattener 400× finer does not touch
it. What `tol` buys is chord sagitta, i.e. **area**.

## Decomposition

Ear clipping → triangles → Hertel–Mehlhorn merge. HM isn't optimal (that needs
Keil–Snoeyink) but it's within 4× and it keeps the part count off the solver's
back:

| shape | verts | triangles | convex parts |
|---|---|---|---|
| L | 6 | 4 | **2** |
| star | 10 | 8 | **5** |
| circle | 40 | 38 | **7** |

Area is preserved to a relative **1.2e-16**, every part is convex, and the worst
aspect ratio is 27.9 — no slivers, which is what would destabilise contact
normals.

## The check worth having: decomposition invariance

A2 built its L by hand as two rectangles and measured a COM of (60, 160). A3
builds the same L as a *single concave path*, flattens it, and lets ear clipping
and HM cut it up however they like. Two completely unrelated routes:

- COM: **(60.000000000, 160.000000000)** — matches to 1e-9
- area: **16000.000000** — matches
- moment: relative difference **0.0**, exactly

Decomposition is not allowed to move the mass, and it doesn't. A2's replay check
then still holds end to end: **3.27e-06 px** worst vertex error across 14 convex
parts on 3 bodies, and the pile settles without tunnelling or exploding.

## Corrected here

Two constants I had wrong, both caught by checks that failed:

- The kappa circle's radial error is **0.027%** of radius, not the 0.02% I first
  asserted.
- The kappa circle's *area* converges to πr² **+ 32 px²** at r=200 (2.5e-4
  relative), so it is not a tight bound on flattening either.

And one check was passing for the wrong reason: it compared the area deficit at
tol=4 against a *negative* deficit at tol=0.01, which any positive number beats.
The deficit changes sign at tol=0.25 because the bezier's own area sits above
πr². Measuring convergence against the finest run instead removes the bezier's
constant offset and gives the clean slope-1 line in the plot.

## Not done here

**Holes.** A ring needs its inner contour bridged into the outer one before it
can be triangulated. The plan puts that in A4, where holes and islands arrive
together with alpha contours.

**The layer-space assumption.** `aepath.py` assumes path coordinates arrive in
the same space the anchor is expressed in. That holds for a plain shape layer
with an untransformed group, but shape *group* transforms sit between the path
and the layer, and this sandbox has no real AE dump to check against. B1 is
where that gets confirmed against actual `evalScript` output instead of assumed.

## Files

- `aepath.py` — AE Shape format, adaptive flattening, RDP simplification
- `decompose.py` — ear clipping, Hertel–Mehlhorn merge, convexity/sliver tests
- `geom.py` — polygon area, centroid, moments; compound mass properties
- `sim.py` — scene dataclasses, world construction, stepping, sampling, bake
- `a1_primitives.py` — the A1 checks and plots
- `a2_anchor_com.py` — the A2 checks and plots
- `a3_paths.py` — the A3 checks and plots

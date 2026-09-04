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

---

# A4 — rendered alpha to bodies (Wall B, hard input)

`python a4_alpha.py` → 16/16 checks, `a4_checks.png`. Input is four real AE
render-queue exports in `png-ae-exports/`, and they were a far better test set
than anything synthetic: **Circ** is a disc with an enclosed hole *and* a notch
open to the boundary *and* three satellite islands; **Penta** is a pure ring;
**S** and **Star** are deeply concave single blobs.

Pipeline: alpha → marching squares → nesting → simplify → bridge holes →
convex parts → **one body per layer**.

## The bug worth the whole step

Marching squares interpolates each crossing to the alpha=threshold iso-line. If
a corner sample sits *exactly* on the threshold, both of its edges interpolate
to t=0 — the corner itself — so two different edge points become the same point.
Keyed by start point, one segment overwrites the other and the loop shatters.

**Eight pixels of alpha==128 in a 300×300 export turned one contour into 76
fragments, and nothing raised.** Penta passed perfectly throughout (straight
edges, 2 such pixels in harmless places), so the failure looked shape-dependent
rather than systemic.

Two things came out of it. Nudging any exactly-on-threshold sample off the
iso-value removes the entire class of failure. More importantly the routine now
returns **stats** — segment count, key collisions, unclosed chains — so the
failure is measurable instead of silent. That is the broken control: with the
nudge disabled, 4 collisions produce 287 open chains and 79 loops instead of 1.

## Ear clipping had to change

A bridged ring traverses the bridge twice, so vertices legitimately sit on top
of each other and on each other's edges. An inclusive point-in-triangle test
treats those as blockers, and ear clipping then finds **no ear at all** on every
shape with a hole — Circ and Penta both failed outright. The fix is a strict
interior test plus skipping vertices coincident with the ear's own corners, and
testing only reflex vertices.

## The image is its own ground truth

The luxury of this step: area and centroid can be checked against the pixels
directly, by code that never touches the contour path.

| | contour area | pixel area | rel | COM error |
|---|---|---|---|---|
| Circ | 29782.4 | 29799 | 0.0006 | 0.071 px |
| Penta | 18808.3 | 18782 | 0.0014 | 0.091 px |
| S | 34814.2 | 34816 | 0.0001 | 0.035 px |
| Star | 36418.1 | 36428 | 0.0003 | 0.017 px |

Sub-pixel centroid agreement is the interpolated iso-line earning its keep — it
uses the AA band as sub-pixel edge position rather than throwing it away.

Topology matches an independent `scipy.ndimage` labelling exactly, Circ's
4 islands and 1 hole included.

## What holes and islands are worth

- Ignoring Penta's hole makes it **58% too heavy**, with its COM in the hole.
- Keeping only Circ's largest island discards **18.6%** of the layer.

## Wall J: all four of Circ's islands stay on one body

Because **one AE layer is one transform** -- one Position, one Rotation, and
nowhere to write a second. So the disc and its three satellites are welded into
a single rigid body even though nothing touches. That deserves stating plainly:
it is a property of the *output format*, not of the physics. Physically those
pieces should fall apart; they cannot, because there is only one layer to bake
onto. Splitting them means *creating* layers, which is a Phase B capability and
a real feature for confetti, debris and shattered text.

The weld is not free, and mass ratio badly understates it:

| layer | islands | mass x | inertia x | COM shift | radius of gyration |
|---|---|---|---|---|---|
| Circ | 4 | 1.22 | **2.15** | 12.5 px | 70.7 -> 93.7 px |
| Penta | 1 | 1.00 | 1.00 | 0.0 px | 80.9 |
| S | 1 | 1.00 | 1.00 | 0.0 px | 84.4 |
| Star | 1 | 1.00 | 1.00 | 0.0 px | 78.3 |

Circ's satellites are 22% more mass but **115% more moment of inertia**.
Parallel axis is quadratic in distance, so a little mass a long way out
dominates: the welded layer spins visibly slower under the same torque than the
disc alone would. Reading the mass ratio and calling it a 22% effect would have
been wrong by a factor of five.

`geom.island_glue` measures it; `a4_alpha.glue_warning` turns it into the
sentence Phases B and C should show, and stays quiet below 1.25x -- the three
single-island layers report exactly 1.00, so the warning is not fired on
everything.

## Slivers: found, measured, not solved

A3's synthetic shapes gave a worst part aspect ratio of 27.9. Real contours give
**145.7**. Raising the Hertel–Mehlhorn merge cap from 8 to 24 does not move that
number at all — it only cuts part counts (Circ 35→30, Star 12→9). Slivers are
inherent to ear clipping on a densely sampled smooth curve: a sliver's
neighbours cannot absorb it without going concave.

So the useful question is not whether slivers exist but whether they carry mass,
and they do not — parts above aspect 60 hold **≤0.84%** of the area, and all
four layers simulate for 120 frames without instability. **Flagged as a Phase C
stability risk rather than fixed.** The merge cap default moved 8 → 12 anyway,
since it cuts part count for free.

## Also learned

Decomposing a *raw* 800-vertex contour is O(n³) ear clipping — the first run of
A4 hung on it. Mass properties for a shape with holes need no decomposition at
all: outer minus holes, area-weighted, with the parallel axis theorem in both
directions. `geom.with_holes_mass_properties` does it exactly and instantly.

RDP at tol=1.0 keeps 99.3–99.9% of the area while cutting 1320 vertices to 61.
The area lost through the pipeline is *simplification's*, never decomposition's,
which preserves the simplified ring to 6e-16.

## Files

- `alpha_contours.py` — marching squares with stats, nesting, hole bridging
- `aepath.py` — AE Shape format, adaptive flattening, RDP simplification
- `decompose.py` — ear clipping, Hertel–Mehlhorn merge, convexity/sliver tests
- `geom.py` — polygon area, centroid, moments; compound mass properties
- `sim.py` — scene dataclasses, world construction, stepping, sampling, bake
- `a1_primitives.py` — the A1 checks and plots
- `a2_anchor_com.py` — the A2 checks and plots
- `a3_paths.py` — the A3 checks and plots
- `a4_alpha.py` — the A4 checks and plots
- `png-ae-exports/` — four real AE render-queue exports used as A4's input

---

# A5 — the preview, and whether it can actually catch a bad bake

`python a5_preview.py` → 11/11 checks, `a5_preview.gif`, `a5_contact.png`,
`a5_checks.png`. Input is A4's four real AE exports, so the preview runs on the
hardest geometry the sandbox has.

The plan called the preview *"not decoration — it is the only way to see that a
bake is wrong."* That is a claim, so A5 tests it rather than assuming it: take
each convention A1–A4 settled, invert it in the baked JSON, render both, and
**measure** whether the preview shows the difference.

## Why the renderer shares nothing with the solver

`preview.py` imports neither `pymunk` nor `sim`. It reads the keyframe JSON and
the layer geometry, and applies AE's transform:

```
world = position + R(theta) * (vertex_layer - anchor)
```

That separation is the point. `sim.replay_from_keyframes` — the A2/A3/A4
workhorse — shares assumptions with the solver, so it cannot catch a fault *in
the conventions themselves*. A second consumer that only ever sees the JSON can.

It also settles what a bake is worth on its own: **nothing**. The schema carries
no geometry, and it should not — in AE the geometry is already on the layer.
So a preview takes two inputs, exactly as AE does: the keyframes, plus the
layers in layer space with their anchors. Phase B reads that second half out of
the real comp.

Comp space is y-down and so is image space, so there is no flip in the
rasteriser either — the A1 convention paying out a third time.

## The six faults, and what they cost

Each is one convention, inverted. "Silhouette" is symmetric difference over
union of the drawn masks — *would an artist see it*, which a vertex distance in
px does not answer, because 3 px on a 400 px shape and 3 px on a 6 px shape are
not the same event.

| fault | worst px | silhouette |
|---|---|---|
| rotation sign flipped (A1) | 366.70 | 52.7% |
| comp read as y-up (A1) | 740.00 | 100.0% |
| rotation in radians (A1) | 366.77 | 48.4% |
| rotation left wrapped (Wall F) | 366.77 | 21.5% |
| Position = COM, not anchor (Wall E) | 14.23 | 17.9% |
| positions rounded to whole px (Wall I) | 2.03 | 2.4% |

All six show. The interesting one is the last: **2 px moves 2.4% of the
silhouette.** A numeric diff shrugs at half a pixel; the render does not.

## The finding: a preview that draws only keyframes is not a preview

The first run measured two of those six faults at **exactly 0.00 px**, and the
reason is the most useful thing in this step.

**Wall F is invisible on any still.** Wrapped, 184.88° is written as −175.12° —
the *same orientation*. Every single frame draws identically. What differs is
the **tween**, which sweeps 360° the wrong way across the wrap. And that sweep
lives inside the **one frame interval** containing the crossing: a second pass
sampling every 8th frame *and* every 8th half-frame still measured 0.00. Nothing
short of every half-frame finds it.

**Decimation looked free** at stride 2 and 4 — because every frame sampled was a
multiple of 4, i.e. a keyframe the decimation had kept. The measurement was only
ever looking at the frames the fault had not touched.

Same lesson twice: **AE plays the tween, not the keyframes.** The fault sampling
is now every half-frame across the whole range, and the figure renders each
fault at the frame where it disagrees *most* rather than at a fixed frame —
picking frame 60 had the whole scene at rest, which showed four faults and
illustrated two.

## What linear interpolation costs

AE's Linear keyframes chord across a curve. Ground truth for the half-frames
comes from re-running the same scene at **48 fps with half the substeps**, so
`dt = 1/(fps·substeps)` is unchanged and it is the same trajectory sampled twice
as often — not a second simulation.

Doubling *both* (the first thing this file did) quarters dt, and the two runs
diverged by **142 px** by frame 75. That is two different simulations, and the
number is large and plausible enough to read as a finding. Worth remembering:
the check that verifies an interpolation has to hold the integrator fixed.

With that fixed, the prediction splits cleanly:

| | worst px | ballistic frames | worst there |
|---|---|---|---|
| Circ | 2.745 | 0 | — |
| Penta | 5.740 | 0 | — |
| S | 6.137 | 0 | — |
| Star | 2.552 | 14 | 0.2126 |

Chord sag on a parabola predicts `g·dt²/8` = **0.2127 px**, and on the frames
that are genuinely ballistic — second difference of position equal to gravity
and nothing else — the measurement is **0.2126 px**. Everywhere else it is 29×
worse. **Contact and rotation are not parabolas**, and that is where a sparse
bake breaks: not in the fall.

## Wall I: uniform decimation is not a strategy

| stride | keyframes/layer | json | worst px | silhouette |
|---|---|---|---|---|
| 1 | 97 | 100.0% | 0.00 | 0.0% |
| 2 | 49 | 52.1% | 22.94 | 16.7% |
| 4 | 25 | 28.1% | 36.47 | 27.3% |
| 8 | 13 | 16.1% | 104.10 | 72.1% |
| 16 | 7 | 10.2% | 149.38 | 79.7% |

One keyframe per frame is the only exact option, by definition. Merely *halving*
the keyframes costs 22.9 px for a 48% smaller file — and the error is not spread
evenly, it is piled up at the contacts, exactly where check 3 said the motion
stops being a parabola. So Wall I wants **error-driven keyframe reduction**
(keep a keyframe where dropping it exceeds a pixel budget), not a fixed stride.
That is a Phase B/C task with a number attached now.

## Determinism, extended to the pixels

A1 proved the JSON is byte-identical run to run. A5 extends that to the render:
two GIFs of one bake hash the same, and the broken control — moving a single
vertex by **0.05 px** — changes the digest. So agreement is evidence.

## Files

- `preview.py` — keyframe sampling, AE's transform, Pillow rasteriser, GIF and
  contact sheet, mask disagreement. No solver imports.
- `a5_preview.py` — the fault injectors, the measurements, the plots.
- `a5_preview.gif` — 97 frames of the four AE exports, drawn from the JSON.

---

# B1 — the input schema, and the half that AE has to answer

`python b1_scene.py` → 18/18 checks, `b1_checks.png`. Files: `scene_io.py`,
`b1_read_shapes.jsx`, `b1_fixture_scene.json`.

B1 is the first step whose deliverable is half unrunnable here. The split is
deliberate:

| | |
|---|---|
| **the script's job** | walk Contents, compose group transforms down to layer space, serialise. **Unverified until AE runs it.** |
| **the Python's job** | read that document, validate it usefully, build the same bodies A3/A4 built. Verified offline against a fixture. |

The transform maths the script depends on is *not* left unverified: `compose_*`
lives in `scene_io.py`, the jsx mirrors it line for line, and it is measured
against closed-form answers here. So when the first AE run disagrees, the
question is "did the script read the right property?" — not "is the maths
right?" — which is a much smaller place to look.

## Phase A had no input format

Its scenes were Python dataclasses written by hand in `a1..a5`. Fine for a
sandbox, useless the moment something outside Python has to produce one. So B1
begins by defining `ae-physics-scene/1`, and defining it is what surfaced
everything below.

## A3's open debt, paid

A3 wrote that it *assumed* path vertices arrive in the same space as the anchor,
and flagged it for B1. The answer: **the assumption is false in AE and true in
the schema.** A shape layer is a tree — Contents holds groups, groups hold
groups, and every group has its own transform. A path's vertices are in the
space of its enclosing group. Composing down is the *script's* job, so
complexity sits at the boundary rather than in the solver.

Nested composition is checked against a hand-worked answer: a point at (4,0)
through a group scaled 50% and rotated 90°, then one scaled 200% and rotated
−90°, lands at exactly (4,10). The broken control is the bug A3 could not rule
out — ignore the group transforms and that point stays at (4,0), **10 px wrong
on a shape only 4 px from its own origin**.

## Duplicate layer names, and why the bake schema is now /2

AE lets two layers share a name. "Shape Layer 1" twice is the *default*, not an
edge case. Phase A keyed the bake — and `preview.index_bake` — **by name**,
which worked only because Phase A invented its own names.

The fixture has two layers called "Shape Layer 1". Keying by name gives 2
entries for 3 layers: one is silently dropped, no error, and the preview would
simply not draw it. So `id` — the AE layer index, unique within a comp — is now
the join key, name is for humans, and the bake schema went to
**`ae-physics-bake/2`**. This is exactly the class of bug the sandbox exists to
find before AE does.

## The check that could not be written

The obvious round trip is Scene → document → Scene. It cannot be written, and
finding that out was worth more than the check.

**A `Scene` holds convex parts.** It sits downstream of flattening,
simplification and ear clipping; the contours it came from are gone. Writing the
parts back out as paths produces a *different* document that happens to draw the
same picture — and re-reading it re-runs containment nesting over polygons that
share edges, which dies in `triangulate` with no ear to clip. That is how it was
discovered: as a crash, not as an argument.

So the round trip is done where it means something — **text → document → Scene →
bake, twice, byte-identical** — and the fact itself is now a check (6b) so it
cannot quietly stop being true. Phase C will want a real scene writer for a
panel; that needs bodies to keep their source contours, which is a change to
`PolyBody`, not a detail of the IO module.

## Scale, baked into the geometry

A rigid body has no scale, so a layer at 200% cannot be modelled as-is. Scale
goes into the geometry at read time — vertices *and* anchor — which is exact
rather than approximate, because AE applies scale in layer space before rotation:

```
comp = position + R(theta) * (S * (p - anchor))
     = position + R(theta) * ((S*p) - (S*anchor))
```

Measured: 200% gives exactly 4.000000000× the area. Every loaded body comes out
anchor-centred, which is what makes the baked Position mean in Python what it
means in AE. The layer's own Scale property is never touched — we only write
Position and Rotation back. An *animated* scale is not a rigid body at all, and
gets a warning.

## A control that lied, and the metric that fixed it

The broken control for "tangents are relative, not absolute" first measured
**area**, and reported **1.00×** — the mistake looked harmless. It is not. A
wrecked path crosses itself, and the shoelace formula cancels the overlapping
lobes against each other.

Extent is the honest metric: how far from the centre the curve actually goes.
The same path swings **253 px from the centre instead of 100**. Worth keeping in
mind wherever a broken control is scored by a signed integral.

## Validation, because the producer is unreachable

The document comes from a hand-rolled ES3 serialiser inside a host application
that cannot be debugged from here, so `validate()` returns **every** problem at
once as sentences naming the layer and the field. Eight injected faults, eight
caught: wrong schema version, a `null` vertex (which is what `toFixed` on NaN
writes), tangent counts out of step with vertices, a one-vertex path, an open
path, a layer with no paths, duplicate ids, and a zero scale.

## What the script knows that this sandbox never had to

Writing the jsx surfaced constraints Phase A never met:

- **There is no `JSON` object in ExtendScript.** ES3. The serialiser is
  hand-rolled, which is precisely why `validate()` is as fussy as it is.
- **Parametric shapes have no vertices.** A Rectangle, Ellipse or Polystar is
  `ADBE Vector Shape - Rect` and friends, with no vertex list and no scripting
  API to convert one. The script reports them loudly and tells the user to
  right-click → Convert To Bezier Path, rather than silently reading nothing.
- **Open paths are skipped**, 3D layers are skipped, and animated paths,
  positions and scales each raise a warning rather than being quietly flattened
  to their value at time 0.

## Still open

`b1_read_shapes.jsx` has never run. The first real comp is expected to find
something — a match name that differs by AE version, a property that returns a
3-vector where a 2-vector was assumed. `validate()` exists so that whatever it
is arrives as a sentence rather than a stack trace.

---

# B2 — writing the bake into AE, and Wall I's real shape

`python b2_apply.py` → 14/14 checks with the report present, `b2_preview.png`.
Files: `b2_apply_bake.jsx`, `b2_apply.py`, `b2_bake.json`,
`b2_bake_result.json`, `jsx_check.py`.

The round trip closes here: a comp goes in through B1, a bake comes back out
through B2, and **AE holds exactly what the solver computed.**

| | stored | tween |
|---|---|---|
| Shape Layer 3 | 0.0000 px / 0.0000° | 0.0003 px |
| Shape Layer 2 | 0.0000 px / 0.0000° | 0.0020 px |
| Shape Layer 1 | 0.0000 px / 0.0000° | 0.0016 px |

The tween column is not noise in AE, it is the report's own 6-decimal rounding.

## Why the script reads its own work back

"It looked right" is not a measurement, and this is the one step where the
sandbox can see nothing at all. So after writing keyframes the script reads them
back **out** of AE and saves a report; Python compares that against the bake it
asked for.

It samples two different things, and the second is the point:

- `stored[]` — did AE keep the numbers we sent?
- `tween[]` — does AE *interpolate* the way the bake assumes?

That second one is A5's finding restated inside AE. AE's default for a new
Position keyframe is auto-bezier with **spatial** tangents: the motion path bows
between keys, adding curvature nobody simulated. That fault leaves every stored
value perfect and shows up only between keyframes. A verifier reading back only
stored values would have called such a bake flawless.

The verifier is calibrated before it is pointed at AE: a synthetic report from a
*perfect* AE must score exactly zero, and four injected faults must each be
caught — whole-pixel rounding, a bowed tween, a rotation sign flip, and a
dropped keyframe (caught by count, since sampling every 17th key steps straight
over it). Otherwise any number it printed later would be its own, not AE's.

## Wall I, measured — and it is not where the plan expected

The plan worried about "12,000 `setValueAtTime` calls, cost unknown until
measured". Measured, on AE 26.3x87, 6,486 keyframes across 3 layers:

| | µs per key | 12,000 keys |
|---|---|---|
| `setValueAtTime` in a loop | 6,850 | **82 s** |
| `setValuesAtTimes` in bulk | 19.6 | **0.2 s** |
| forcing LINEAR interpolation | 853 | 10 s |

**Bulk writing is 350× faster**, so the plan's worry was real and is now
answered. But the interesting number is the third row. Setting interpolation
costs **44× the bulk write itself** — 5.5 s of the run — it is per-key, there is
**no bulk form of it**, and it is *not optional*: skip it and AE bows the path.

So Wall I's actual shape is the opposite of what was assumed: **writing the
values is free; making them mean what we meant is not.** Any future performance
work belongs on the interpolation pass, not the value writes. (Open question for
Phase C: whether AE's default-interpolation preference can be set before the
keys are created, making the pass unnecessary. Not tested, not assumed.)

Total apply was 35.1 s, but ~22 s of that is the naive loop being timed *for
measurement only* and ~7 s is the read-back. The real cost of applying this bake
is about 5.7 s, nearly all of it interpolation.

## Two things AE taught us by failing

**Spatial tangents want three elements.** The first run died on:

```
Unable to call "setSpatialTangentsAtKey" because of parameter 2.
Value array does not have 3 elements
```

A 2D layer's Position accepts a **two**-element value — `setValuesAtTimes` had
taken `[x, y]` on that same property moments earlier — but demands a **three**
-element spatial tangent. The arity belongs to the *call*, not the property.
It is now probed once and recorded in the report (`spatial_tangent_arity: 3`)
rather than hard-coded, so the mirror image of this error on another AE version
shows up as data instead of a crash.

**An aborted script leaves the project wedged.** The throw happened between
`beginUndoGroup` and `endUndoGroup`, so AE was left with an undo group open and
keyframes half written. The second-order lesson is the better one: the apply now
runs in `try/catch/finally` and closes the group whatever happens, so a failure
is one Undo away from clean.

`jsx_check.py` came out of the same session — brace balance and unterminated
string literals. Not a parser. It exists because AE is otherwise the only syntax
check these files get, and a round trip through AE to find a stray newline in a
string literal is an expensive way to learn that. It has already caught two.

## The bake that escaped the comp

Found before AE ever saw it, and the worst thing in B2. Baking the real comp,
one layer left the frame by **838,591 px** and kept accelerating — and that
number was about to be written into a real project as a keyframe.

It is not a solver bug and not A4's sliver risk. The S **rolls steadily left,
reaches the end of the floor segment, and falls off the world.** The reader
script wrote one floor exactly comp-width with no walls: correct physics, and a
placeholder that happens to be dangerous.

Three responses, because one would not have been enough:

1. the reader now writes a **closed box** — floor, both walls, ceiling;
2. `escapes()` guards every bake and names the layer and the frame;
3. `b2_preview.png` renders the trajectory to **look at before applying**,
   which is A5's whole argument taken seriously at the moment it matters most.

## Matching by index is a tripwire, not a key

The bake joins on `id`, which is the AE layer index — and **index is
positional**. Adding, deleting or reordering a layer between the read and the
apply silently moves every id after it. So the script also checks the *name* at
that index and aborts if it disagrees, writing nothing. The name is not the key
(B1 established that duplicate names are AE's default); it is a cheap way to
notice that the comp has moved under us.

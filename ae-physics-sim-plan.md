# AE Physics Simulator — Plan

A Newton-3-style tool: take comp layers (shape-layer vectors first, rendered
alpha later), build a 2D rigid-body world from them, simulate, and bake the
result back onto the layers as keyframes.

**Guiding constraint:** prove the whole pipeline in Python, offline, before
touching After Effects or C++. AE is a place where nothing can be measured;
the sandbox is where the walls get found.

---

## Scope decisions

| Decision | Choice | Why |
|---|---|---|
| Body sources | Shape-layer paths **first**, rendered alpha **required** later | Paths are readable from script; alpha is not (see Wall A) |
| Baked channels | Position + rotation | A rigid solver outputs nothing else. Scale is deferred — see below |
| Prototype language | Python + numpy (+ pymunk) | Existing habit, plottable, no AE round-trip |
| Eventual UI host | CEP panel (decide for real at Phase C) | Dockable, `evalScript` bridge exists, Chromium runs WASM |

**On scale:** rigid-body dynamics produce a position and an orientation, full
stop. Scale keyframes can only come from (a) a faked squash-and-stretch pass
driven by impact velocity, or (b) real soft-body deformation. Neither is in the
rigid core. The solver layer gets structured so one can slot in later; nothing
more is promised now.

---

## The walls we expect to hit

The sandbox exists to hit these early. Listed so we can tell "found it" from
"surprised by it".

**A. Alpha is invisible to script.** Neither ExtendScript nor CEP can read
rendered pixels. Alpha-derived collision shapes require a C++ AEGP calling
`AEGP_RenderAndCheckoutFrame`. This is unavoidable and it is the single biggest
cost in the project. The sandbox defers it by loading PNGs exported from AE.

**B. Contour topology.** **CLOSED IN A4** against four real AE exports. Holes,
islands and specks all handled: nesting by containment depth, holes bridged into
their outer ring, specks dropped by min-area. Topology matches an independent
labelling exactly, and contour area/centroid match the pixel count to 0.14% and
0.09px. The real find was a DEGENERACY: a sample sitting exactly on the
threshold collapses two edge crossings onto one point and silently shatters the
contour -- 8 such pixels turned one loop into 76.

**C. Concavity.** **CLOSED IN A3** for a single contour. Ear clipping plus
Hertel-Mehlhorn merging: area preserved to 1.2e-16, every part convex, worst
aspect ratio 27.9 (no slivers), and part counts of 2/5/7 for an L, a star and
a 40-gon. Mass properties are invariant under decomposition, which is the
check that would catch a subtly wrong cut. **Amended by A4:** that "no slivers"
result was an artefact of synthetic shapes -- real contours reach aspect 146,
and the merge cap does not help. They carry <1% of the mass, so it is a Phase C
stability risk rather than a correctness bug.

**D. Units.** ~~Physics engines are tuned for objects sized ~0.1–10 units, so
expect interpenetration at high pixels-per-meter.~~ **MEASURED IN A1 — half
right.** The low end is the real wall: ppm is the comp's clock, and at ppm=1 a
fall down the frame takes 14.8s instead of 1.48s. The predicted high-end
interpenetration does *not* appear where expected — sink is flat at ~2px from
ppm=100 to ppm=1000 because the solver resolves much tighter than
`collision_slop`, and only breaks down at ppm=10000. **Usable band 10–1000;
settled on 100.**

**E. Anchor point vs centre of mass.** **CLOSED IN A2.** The solver tracks a
COM; the layer pivots on its anchor, and they never coincide. Both directions
are implemented and round-trip exactly:
`com_world = position + R(theta)*(com_layer - anchor)` going in,
`position = com_world + R(theta)*(anchor - com_layer)` coming out.
Verified by replay against solver ground truth, not by inspection.

**F. Rotation unwrapping.** Solvers report angle in (-pi, pi]. A wheel that spins
twice must bake as 720 degrees, not snap back to 0. Angles need accumulating
before they become keyframes. **CLOSED IN A1, and A5 found its blind spot:** a
wrapped angle is *invisible on every still* -- 184.88 and -175.12 are the same
orientation -- and the 360-degree backwards sweep it causes lives entirely
inside the one frame interval containing the crossing. Any check or preview
that samples only keyframed frames will score this fault at exactly 0.00 px.

**G. Timestep vs frame rate.** A stable solver wants a small fixed dt with
substeps; AE wants one sample per frame at comp fps. These are different clocks.

**H. Determinism.** A bake that differs between runs is unusable. Every sandbox
sim gets run twice and compared exactly.

**I. Keyframe volume (AE phase).** **CLOSED IN B2, and it is not where this
entry assumed.** Measured on AE 26.3x87 over 6,486 keyframes:

| | us per key | 12,000 keys |
|---|---|---|
| `setValueAtTime` in a loop | 6,850 | 82 s |
| `setValuesAtTimes` in bulk | 19.6 | 0.2 s |
| forcing LINEAR interpolation | 853 | 10 s |

Bulk writing is **350x** faster, so the worry about 12,000 calls was real and is
answered. But the cost that remains is **setting interpolation**: 44x the bulk
write, per-key with no bulk form, and NOT optional -- skipping it lets AE's
default spatial auto-bezier bow the motion path between our samples. Writing the
values is free; making them mean what we meant is not. Open for Phase C: whether
AE's default-interpolation preference can be set before the keys are created,
removing the pass. Not tested.

A5 closed the accuracy half separately: one keyframe per frame is the only exact
option, halving them costs 22.9 px, and the error piles up at contacts, so any
reduction must be **error-driven against a pixel budget**, not a fixed stride.

**J. One layer is one transform.** An AE layer has one Position and one
Rotation, so a layer whose alpha contains several disconnected islands gets ONE
rigid body -- invisible glue between pieces that are not touching. This is a
property of the OUTPUT format, not of the physics, and it is **settled for
Phases A and B: weld, and warn.** Splitting requires *creating* layers, which
only becomes possible in Phase B, and is the right behaviour for confetti,
debris and shattered text -- so it is a deferred feature, not a bug.

The warning is not cosmetic, because mass ratio badly understates the cost.
Measured on A4's `Circ` (a disc plus three satellites): the satellites are
**1.22x the mass but 2.15x the moment of inertia**, moving the COM 12.5 px and
the radius of gyration from 70.7 to 93.7 px. Parallel axis is quadratic in
distance, so a little mass a long way out dominates -- the welded layer spins
visibly slower under the same torque than the disc alone would.
`geom.island_glue` computes it and `a4_alpha.glue_warning` is the surface
Phases B and C call.

---

## Phase A — Python sandbox (no AE, no C++, no panel)

Everything here runs offline against synthetic and AE-exported inputs.

- **A1. World + bake, end to end on primitives.** DONE — 13/13 checks, see
  `python-proto/physics_sim/WALKTHROUGH.md`. Spine works; Walls D, F, G, H
  measured. Established the y-down convention (positive angle reads clockwise,
  matching AE, with no sign flip), ppm=100, and schema `ae-physics-bake/1`.
  Two plan predictions falsified: Chipmunk's integrator is explicit, not
  semi-implicit, and Wall D's upper half sits two decades higher than expected.
- **A2. The anchor/COM transform.** DONE — 10/10 checks. Wall E closed: both
  directions of the layer-space/body-space transform, verified by replaying
  each frame the way AE would and comparing to the solver's own vertices
  (worst error 3e-06 px, and that floor is keyframe rounding, not the
  transform). Test shape is an L whose COM falls outside the material.
  `geom.py` also builds the compound mass properties A3 will need.
- **A3. Bezier paths → bodies.** DONE — 15/15 checks. Flatten (adaptive de
  Casteljau) → simplify (RDP) → convex parts (ear clipping + Hertel-Mehlhorn)
  → PolyBody. Wall C closed for a single contour; Wall B only partly, since
  **holes are deferred to A4** (a ring needs its inner contour bridged before
  it can be triangulated). Decomposition is mass-invariant: the A2 L rebuilt
  as one concave path lands on the same COM (60,160) to 1e-9.
  Still synthetic input — the layer-space assumption is B1's to confirm.
- **A4. Alpha → bodies.** DONE — 16/16 checks, on four real AE exports. Wall B
  closed. The A3 and A4 pipelines did converge on one shared "polygon → body"
  stage: `simplify_closed` → `convex_parts` → `PolyBody` is common to both, and
  A4 only adds contour extraction and hole bridging ahead of it. Ear clipping
  needed a strict-interior test to survive bridged rings. All of a layer's
  islands stay on one body per Wall J, and `glue_warning` reports what that
  weld costs. Slivers are real on
  real contours (aspect 146 vs 28 synthetic) but carry <1% of the mass —
  flagged as a Phase C risk, not solved.
- **A5. Preview renderer.** DONE -- 11/11 checks. A Pillow renderer that shares
  nothing with the solver (no pymunk, no `sim`), reading the keyframe JSON plus
  the layer geometry and applying AE's transform. Each A1-A4 convention was
  inverted in the bake and measured: all six faults are visible, the smallest
  (whole-pixel rounding, 2 px) still moving 2.4% of the silhouette. The claim
  holds -- but only when the preview samples BETWEEN keyframes. Wall F's wrapped
  rotation is invisible on every still (same orientation, wrong tween) and lives
  inside one frame interval; uniform decimation looked free purely because the
  sampled frames were the surviving keyframes. Free-fall interpolation error is
  exactly the predicted chord sag g*dt^2/8 (0.2126 vs 0.2127 px); contact and
  rotation are 29x worse, so Wall I needs error-driven keyframe reduction, not a
  fixed stride.

Exit criterion: a PNG or path-dump goes in, a keyframe JSON comes out, the
preview looks right, and two runs agree exactly.

## Phase B — AE data round-trip (script only, still no C++)

- **B1. DONE** -- 22/22 checks. The script ran on a real comp and read three
  hand-drawn shape layers correctly; that export is now a permanent fixture. Defines `ae-physics-scene/1` (Phase A had no input format at all),
  `scene_io.py` to read and validate it, `b1_read_shapes.jsx` to produce it, and
  a fixture standing in for AE. Three findings: **A3's layer-space assumption is
  false in AE** -- vertices sit in their enclosing group's space, so composing
  down is the script's job (ignoring it is 10 px wrong on a shape 4 px from its
  origin); **AE allows duplicate layer names**, so keying the bake by name
  silently drops a layer and the join key is now the layer `id`
  (`ae-physics-bake/2`); and **a Scene cannot round-trip to a document**,
  because it holds convex parts and its source contours are gone. Scale is baked
  into the geometry (exactly 4.000000000x at 200%).
- **B2. DONE** -- 14/14 checks. `b2_apply_bake.jsx` writes the bake and reads
  its own work back out; `b2_apply.py` verifies the report. **The round trip
  closes: AE holds exactly what the solver computed** (0.0000 px / 0.0000 deg
  stored, and straight-line tweens to 0.002 px, which is the report's own
  rounding). Wall I measured -- see below. Three findings: a 2D layer's Position
  takes a TWO-element value but a THREE-element spatial tangent, so arity is
  probed rather than assumed; a throw between begin/endUndoGroup leaves AE
  wedged, so the apply is now try/finally; and the reader's floor-only scene let
  a rolling layer leave the world and reach 838,591 px, which was about to be
  written into a real project -- the reader now emits a closed box and
  `escapes()` guards every bake.
- **B3. DONE** -- 15/15 checks. `b3_loop.py` is the middle of the loop as ONE
  command with real parameters (`--gravity --ppm --substeps --frames --friction
  --elasticity --static --no-walls`), verified against predictions: lunar
  gravity stretches a 100 px fall from 11 frames to 27 where sqrt(9.8/1.6)
  predicts 27.2. Adds **pinned layers** (`--static`, which get NO keyframes --
  writing even a constant would overwrite the user's own placement) and promotes
  B2's escape finding to a refusal (exit 3).

  **The wall B3 exposes is STALENESS**, and only a loop could have exposed it: a
  bake made from a different comp, or from this one before it was edited, is
  well-formed, validates, has matching layer names, and silently destroys good
  animation. Every bake now carries a `source` block (scene SHA-256, comp
  identity, layer list, settings); `--check` guards it offline and
  `b2_apply_bake.jsx` refuses to write on a comp mismatch. The telling case is
  the small one: edit ONE vertex by 3 px and nothing but the hash can tell.

**K. A hand-carried loop goes stale.** **FOUND AND GUARDED IN B3.** Not a
physics wall -- a plumbing one, and invisible until the loop existed. See B3.
Phase C inherits it: a panel removes the hand-carried files but not the problem,
since a user can still edit a comp between simulate and apply. The `source`
block is the mechanism; the panel should re-read rather than trust it.

## Phase C — The panel

Port the solver and geometry pipeline to whatever runs in the panel (Rapier via
WASM being the likely pick), rebuild B1/B2 as `evalScript` calls, add the
viewport and the parameter UI. CEP vs UXP gets re-decided here with the AE
version actually in front of us, not from memory.

## Phase D — The AEGP

Native plugin that renders a layer and hands its alpha contour to the panel.
This is what unlocks alpha bodies inside AE. Deliberately last: it is the most
expensive component and Phase A already proved the algorithm it needs to run.

---

## Notes

- The physics engine is the *least* risky part of this project. Geometry
  extraction and the bake transform are where the time goes.
- pymunk in the sandbox is a learning vehicle. The engine API will not transfer
  to Phase C; the geometry pipeline and the bake math will. Build accordingly.

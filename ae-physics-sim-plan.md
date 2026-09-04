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
before they become keyframes.

**G. Timestep vs frame rate.** A stable solver wants a small fixed dt with
substeps; AE wants one sample per frame at comp fps. These are different clocks.

**H. Determinism.** A bake that differs between runs is unusable. Every sandbox
sim gets run twice and compared exactly.

**I. Keyframe volume (AE phase).** 300 frames x 40 layers is 12,000
`setValueAtTime` calls. Cost unknown until measured.

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
- **A4. Alpha → bodies.** DONE — 14/14 checks, on four real AE exports. Wall B
  closed. The A3 and A4 pipelines did converge on one shared "polygon → body"
  stage: `simplify_closed` → `convex_parts` → `PolyBody` is common to both, and
  A4 only adds contour extraction and hole bridging ahead of it. Ear clipping
  needed a strict-interior test to survive bridged rings. Slivers are real on
  real contours (aspect 146 vs 28 synthetic) but carry <1% of the mass —
  flagged as a Phase C risk, not solved.
- **A5. Preview renderer.** Matplotlib or Pillow animation of the baked result.
  Not decoration — it is the only way to see that a bake is wrong.

Exit criterion: a PNG or path-dump goes in, a keyframe JSON comes out, the
preview looks right, and two runs agree exactly.

## Phase B — AE data round-trip (script only, still no C++)

- **B1.** ExtendScript reads shape-layer paths and writes the same JSON schema
  Phase A consumes. Confirms the schema survived contact with reality.
- **B2.** ExtendScript applies a Phase-A keyframe JSON to real layers. Wall I
  gets measured here.
- **B3.** Close the loop: read comp → run the Python sandbox on the file →
  apply. Ugly, manual, and it is the entire product working.

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

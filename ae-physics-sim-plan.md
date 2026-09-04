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

**B. Contour topology.** A layer's alpha is not one blob. Expect holes (a donut
needs outer + inner contours, and the hole must actually collide), multiple
disjoint islands in one layer, and 1px noise specks that become spurious bodies.

**C. Concavity.** Every 2D engine collides convex shapes. Any real silhouette
needs convex decomposition, and a bad decomposition produces sliver polygons
that make the solver explode.

**D. Units.** ~~Physics engines are tuned for objects sized ~0.1–10 units, so
expect interpenetration at high pixels-per-meter.~~ **MEASURED IN A1 — half
right.** The low end is the real wall: ppm is the comp's clock, and at ppm=1 a
fall down the frame takes 14.8s instead of 1.48s. The predicted high-end
interpenetration does *not* appear where expected — sink is flat at ~2px from
ppm=100 to ppm=1000 because the solver resolves much tighter than
`collision_slop`, and only breaks down at ppm=10000. **Usable band 10–1000;
settled on 100.**

**E. Anchor point vs centre of mass.** The solver tracks a COM; the layer pivots
on its anchor, and they never coincide. The bake is
`position = com_world + R(theta) * (anchor_local - com_local)`.
Getting it wrong looks exactly like a solver bug.

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
- **A2. The anchor/COM transform.** Give each body an arbitrary anchor offset
  and verify a rendered preview matches the sim. Wall E, plus F via a spinner.
- **A3. Bezier paths → bodies.** Feed real shape-layer path data (vertices +
  tangents, dumped from AE by hand as JSON), flatten, simplify, convex-decompose.
  Walls B and C on the easy input.
- **A4. Alpha → bodies.** Load a PNG exported from AE. Marching squares →
  contour hierarchy → RDP simplify → decompose. Same walls, hard input, plus
  holes and islands. Compare the A3 and A4 pipelines — they should converge on
  one shared "polygon → body" stage.
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

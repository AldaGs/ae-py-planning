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
| Eventual UI host | **Tauri shell + AEGP bridge** (decided at Phase C, 2026-09-04) | Lives outside AE as its own suite; the AEGP is required by Wall A regardless, so CEP would be a second bridge thrown away in Phase D |

**On scale:** rigid-body dynamics produce a position and an orientation, full
stop. Scale keyframes can only come from (a) a faked squash-and-stretch pass
driven by impact velocity, or (b) real soft-body deformation. Neither is in the
rigid core. The solver layer gets structured so one can slot in later; nothing
more is promised now.

---

## Feature scope — measured against Newton 4 and PhysicDesk 2

Researched 2026-09-05. Newton 4's surface is taken from its official user guide
and is precise; PhysicDesk 2's comes from vendor blurbs and search results
because every one of its pages refuses automated fetches, so treat that column
as directionally right rather than exhaustive.

| | Newton 4 | PhysicDesk 2 | Here, today |
|---|---|---|---|
| Body types | 7 (static, kinematic, dynamic, dormant, AEmatic, dead, triggermatic) | 3 (dynamic, static, animated) | 2 (dynamic, static) |
| Joints | 7 (distance, pivot, piston, spring, wheel, blob, weld) | 5 (pivot, weld, spring, wheel, rope) | none |
| Forces | magnetism, buoyancy, grenade, per-body gravity scale | zones: wind, magnet, portal, explosion | global gravity only |
| Footage / precomp layers | **interpreted as rectangles** | unclear | **true alpha silhouette** (A4, proven) |
| Viewport | OpenGL preview, 5 tools, play/step/loop | real-time in viewport | contact sheets |
| Output | keyframes to a new or existing comp | keyframes plus generated shape layers | keyframes, in place |
| Liquid / soft bodies / particles | no | **yes, and it is the headline** | no |
| Squash and stretch | no | yes | deferred by decision |

### The distinction that matters for planning

**Some features change the document model and some are additive.** The first
kind has to be designed in now even if it is not implemented for a year; the
second can arrive feature by feature without disturbing anything. Sorting the
competitive feature list along that axis is more useful than ranking it by
popularity.

### Tier 0 — schema slots that must exist before C1

Not implementations. Slots. Everything here reshapes `ae-physics-scene/1` or
`ae-physics-bake/2` if it is retrofitted, so the next schema version should
carry the shape even where the value is always empty.

1. **`joints[]` as a top-level array.** A joint is a relation between two
   bodies with anchor points in comp space, not a property of a layer. Both
   competitors treat joints as core, and a physics tool without them is a toy.
2. **`zones[]`, same argument.** Force fields, wind, magnets, explosions.
3. **Animated (kinematic) bodies.** Newton's kinematic and AEmatic, PhysicDesk's
   "animated": a layer driven by its OWN existing AE keyframes that still shoves
   dynamic bodies. B1 currently reads time 0 only and *warns* about animation,
   so this means layers carrying optional input keyframe tracks. Heavily used in
   real work -- it is how a hand-animated hand knocks over a physics stack.
4. **The bake carries velocity and contact events.** Newton exports contacts as
   keyframes. This is the enabler for squash and stretch, sound sync and
   triggers WITHOUT re-simulating. Two extra channels now; a re-architecture
   later.
5. **Bake target: a new comp, or in place.** Newton exports to either. We
   currently overwrite the source layer's Position and Rotation, which is
   destructive and not what a careful user expects.
6. **One input layer may produce N output layers.** This is Wall J's island
   split and fracture, and they are the same capability. See below.
7. **Collision groups and a collide-with mask.** Newton has five groups and an
   interaction matrix. Trivial per-body fields; impossible to bolt on cleanly if
   the schema has no slot for them.

### Tier 1 — table stakes, but additive

Per-body damping (linear and angular), gravity scale, fixed rotation, initial
linear and angular velocity, bounciness and friction (present), convex-hull
approximation toggle, time divider for slow motion, substep control (present),
simulation frame range. Each is a scalar plus a control. None of them threaten
the model, so none of them belong in the critical path.

Workflow, likewise additive but disproportionately valuable in an app that owns
its own storage: scene save/load, snapshots, a record pool of takes, autosave,
presets, and Newton's per-property Randomize (a genuinely good motion-design
touch).

### Tier 2 — where this can be better rather than equal

- **Alpha-derived bodies for any layer type.** Newton interprets precomp and
  footage layers **as rectangles**. Phase D's path gives true silhouettes for
  footage, precomps, images and text alike, and A4 already validated that
  pipeline on four real AE exports, holes and islands included. This reframes
  Phase D: not the expensive last chore, but the reason to choose this tool. It
  is also the only honest answer to text layers, which are otherwise unreadable
  from script.
- **The external app.** A record pool, a scene library and settings that outlive
  a comp are natural in an application and awkward in a panel.
- **Correctness as a feature.** The round trip is verified to 0.0000 px with
  straight-line tweens, and A5's preview provably catches the faults that hide
  between keyframes. Neither competitor claims anything of the sort, and "the
  bake is exactly the simulation" is a claim that can be backed with numbers.

### Non-goals, chosen deliberately

Liquid, soft bodies and particle systems. PhysicDesk's liquid engine is a
different solver with a different output, and chasing it would double the
project while abandoning what this one is actually good at. Squash and stretch
is the near-miss exception: it is cheap **if** the bake carries contacts and
velocity, which is why Tier 0 item 4 exists.

### Fracture — in, as pre-fracture only

Judged separately because it looks expensive and is not.

**The physics is free**: a shard is a `PolyBody` and the solver does not change.

**The geometry is small and reuses verified code.** Voronoi cells are convex,
and every body is already decomposed into convex parts, so fracture is "clip
each convex part against each cell, group by cell" -- convex against convex,
which Sutherland-Hodgman does exactly in about forty lines with no degenerate
cases. Mass, COM and moment then come from `geom` untouched.

**The expensive part is shared.** Fracture needs one layer to become N layers,
which is Tier 0 item 6, which is also Wall J's island split. Build it once, get
both. The clean mechanism is to **duplicate the source layer N times and give
each a mask shaped like its shard**: it works for footage, precomps, text and
shape layers alike, preserves the original appearance exactly, and a mask is
just a path -- the same data already read and written. Each shard's anchor moves
to its own COM with Position compensated, which is A2's transform, verified.

**Impact fracture is out**, and that is the honest limit. Shattering on impact
changes the body set mid-simulation, and the bake model is N bodies with N fixed
tracks for the whole comp. A shard that does not exist until frame 40 needs a
birth time, which means in-point or opacity keyframes and a real schema change.
Pre-fracture -- shatter at frame 0, pieces resting in place until disturbed --
covers most real use and costs none of that.

**The number to watch is piece count.** Fifty shards over 300 frames is 30,000
keyframes, and B2 measured interpolation at 853 us/key with no bulk form: 25
seconds of interpolation alone, plus fifty layers to create. Fracture is what
turns spike C0.3 from an optimisation into a requirement.

Fracture is scene *authoring*, not solving -- it turns one layer into N bodies
before the simulation runs. So it can be prototyped and verified offline in the
Python sandbox exactly like A1 through A5, and it should be.

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

**G. Timestep vs frame rate.** **CLOSED IN A1.** A stable solver wants a small
fixed dt with substeps; AE wants one sample per frame at comp fps. Reconciled by
`dt = 1/(fps * substeps)`, so every frame lands exactly on a substep boundary and
nothing is ever interpolated inside the solver -- which removes a whole class of
run-to-run difference. A5 then measured what AE's LINEAR interpolation costs
*between* those samples: exactly the predicted chord sag `g*dt^2/8` in free fall
(0.2126 px measured vs 0.2127 predicted) and 29x worse at contacts, where the
motion stops being a parabola.

**H. Determinism.** **CLOSED IN A1 and held since.** A bake that differs between
runs is unusable, so every sim runs twice and is compared exactly. Byte-identical
in A1, with a 1e-9 px nudge to one starting position as the broken control.
Extended twice: A5 took it to the rendered PIXELS (two GIFs of one bake hash the
same; moving a single vertex 0.05 px changes the digest) and B1 took it to
DOCUMENTS (two loads of one scene file bake to the same bytes, so nothing depends
on dict order or float text).

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

## Phase C — The application

**The architecture is decided: a Tauri shell plus an AEGP bridge. No CEP, no
UXP.** Decided 2026-09-04, after Phases A and B, reversing the scope table's
placeholder above.

### Why the placeholder was wrong

The table picked a CEP panel for two reasons: it is dockable, and the
`evalScript` bridge already exists. Both are void.

- **Docking is not wanted.** The tool is to live *outside* After Effects as a
  standalone suite with its own settings. A CEP panel is an AE-owned window; it
  could never have been outside AE in that sense.
- **`evalScript` is not the only bridge.** `AEGP_ExecuteScript` runs the same
  ExtendScript from native code.

And one reason that outweighs both:

- **The AEGP has to exist anyway.** Wall A is unavoidable — neither
  ExtendScript nor CEP can read rendered pixels, so alpha bodies require an
  AEGP calling `AEGP_RenderAndCheckoutFrame`. Building CEP for Phase C means
  building a bridge that Phase D throws away. **One bridge, not two.**

### Why this shape specifically

It is the architecture `pieFX` locked after its own Phase 0, on the same host,
by the same hands — two processes, the AEGP owning all AE access on AE's UI
thread and a Tauri shell owning all UI, talking small JSON over a local socket
or named pipe. The frightening unknown was retired there by measurement, not
argument: **an out-of-process topmost window wins AE's z-order, and layer
selection is document state rather than focus state**, so the external window
can take focus like any other. Verified on Windows and macOS. See
`AldaGs/pieFX`, `ARCHITECTURE.md` and `SPIKES.md`.

**It deletes work rather than adding it.** The previous draft of this section
said "rebuild B1/B2 as `evalScript` calls". With an AEGP bridge they are not
rebuilt at all — the AEGP calls `AEGP_ExecuteScript` on the same ES3 files
already verified against real AE in B1 and B2. Those scripts stop being
scaffolding and become the product's AE layer.

**Tauri over Electron** because the toolchain and the architecture experience
already exist here. Electron's real advantage is Node in-process, which matters
when the bridge *is* Node; here the bridge is a socket to a C++ plug-in.

### The solver: deliberately not decided yet

Rapier native in the Tauri backend is the obvious end state, and the earlier
note about "Rapier via WASM" is obsolete — Tauri runs Rust natively, no WASM.
But switching Chipmunk to Rapier discards A1's falsified integrator finding and
the measured ppm 10–1000 band, both of which are engine-specific, and the
geometry pipeline (`aepath`, `alpha_contours`, `decompose`, `geom`, `scene_io`)
is ~1,200 lines of *verified* code that would need porting **and** re-verifying.

So the first version ships **Python as a Tauri sidecar**, keeping everything
Phases A and B proved, and the port happens when there is a reason rather than
on principle. The cost is a PyInstaller bundle, which is a real wart for a
"complete suite". The exit stays open: only `alpha_contours` touches numpy.

### C0 — spikes, before any product code

Same gate as pieFX's Phase 0: throwaway spikes that answer the questions the
whole design rests on. Each is pass/fail on a measurement.

- **C0.1 — the bridge. PASSED 2026-09-07** on AE 26.3x87: 2,185 bytes returned,
  byte-identical to the save dialog (sha256 `b589df0b0325b543`), 16 ms round
  trip. The gate is green and the architecture stands. Evidence in
  `AldaGs/ae_physics_simulator`, `SPIKES.md`. As specified it was: an AEGP opens a local socket, receives
  `{"cmd":"read_scene"}`, runs B1's reader through `AEGP_ExecuteScript`, and
  returns the scene JSON down the socket instead of through a save dialog.
  *Pass:* the JSON that arrives over the socket is byte-identical to what the
  save dialog writes today. pieFX already has AEGP hello-world, script
  execution and the socket, so this is assembly rather than invention.
  Watch for `AEGP_ExecuteScript`'s empty-but-non-NULL error handle.

- **C0.2 — payload size.** Can `AEGP_ExecuteScript` accept a 148 KB bake as a
  string argument, or must the script read a temp file? Genuinely unknown, and
  the answer shapes the protocol. B2's real bake is the test case: 6,486
  keyframes, 148 KB compact. *Pass:* either it takes the string, or the
  temp-file fallback round-trips with the same verification B2 already has.
  Measure the size at which it breaks, not just whether it works.

- **C0.3 — keyframes from native code.** B2 measured the real cost of an apply:
  values are free in bulk (19.6 us/key) but forcing LINEAR interpolation costs
  853 us/key with **no bulk form**, 5.5 s for one 45 s comp. Can
  `AEGP_KeyframeSuite` set values *and* interpolation faster than ExtendScript
  can? *Pass:* a measured us/key for the native path against B2's numbers on
  the same comp. This is Wall I's only remaining lever, and if it wins, the
  ExtendScript apply becomes a fallback rather than the path.

Gate: C0.1 must pass for the architecture to stand at all. C0.2 shapes the
protocol either way. C0.3 is an optimisation — a fail costs 5.5 s per apply and
nothing else.

### C1 onwards, once the gate is green

- **C1.** The shell: Tauri window, scene list, the B3 parameter set as real
  controls, settings persisted. **Ships `ae-physics-scene/2` and
  `ae-physics-bake/3` carrying every Tier 0 slot** (joints, zones, animated
  input tracks, contacts and velocity in the bake, bake target, N-outputs-per
  -layer, collision groups) even where the arrays are always empty. Retrofitting
  any of those reshapes every document; adding a scalar never does.
- **C2.** The viewport: `preview.py`'s renderer becomes the canvas, scrubbing
  the bake before it is applied. A5's argument — a bake is only checkable by
  looking at it — becomes the main surface rather than a contact sheet.
- **C3.** Staleness (**Wall K**) survives the panel. A GUI removes the
  hand-carried files but not the problem: a user can still edit the comp
  between simulate and apply. The `source` block is the mechanism, and the
  panel should **re-read and compare rather than trust it**.
- **C4.** The sliver risk A4 flagged and did not solve (real contours reach
  aspect 146) gets revisited against whatever solver C ends up running.
- **C5.** One layer becomes N: the output capability that Wall J's island split
  and pre-fracture both need. Duplicate the source layer, mask each copy with a
  shard path, move each anchor to its own COM with Position compensated. Prove
  the geometry offline in the sandbox first -- Voronoi cells clipped against the
  existing convex parts by Sutherland-Hodgman -- because fracture is scene
  authoring, not solving, and the sandbox is still where things can be measured.

### Where a sim lives — OPEN, and the question is not technical

Raised 2026-09-10. Today every pin and per-layer value sits in one global
settings file keyed by layer id, so switching comps transfers them onto whatever
happens to be layer 3 over there, and nothing travels with the project at all.

**The decision this waits on: is a sim part of the ARTWORK, or part of the
WORKING STATE?** If two people opening the same `.aep` should get the same
simulation, it has to live inside the project and a sidecar file is not enough.
If it is closer to a render-queue setting, a sidecar is simpler and better in
every other respect, and losing it is survivable. None of the options sort
themselves until that is answered, which is why this sits in the plan rather
than the roadmap.

The AE SDK has **no arbitrary per-project blob store**. Storing inside the
project means `comp.comment`, layer comments, markers, or arbitrary data on
an effect — all size-limited, all user-visible and user-deletable, and all
genuinely travelling with the file, Collect Files included. A sidecar beside the
`.aep` is readable, diffable, unlimited, and the natural home for something
that is already JSON — and a careless copy separates it from the project.

Either way the identity is the same, and it already exists: **project path +
comp id + the scene hash Wall K computes.** Without it, opening the wrong comp
applies somebody else's masses silently — which is Wall K wearing a different
hat, and Wall K's lesson was that only a re-read catches it.

## Phase D — The AEGP renders

**Narrowed by Phase C's decision.** The AEGP itself now arrives in C0 as the
bridge, so what is left for Phase D is the expensive half it was always really
about: `AEGP_RenderAndCheckoutFrame`, handing a layer's rendered alpha to the
contour pipeline, and thereby unlocking alpha bodies (Wall A).

Still deliberately last, for the original reason: it is the most expensive
component and A4 already proved the algorithm it needs to run, on four real AE
exports. The bridge landing early does not change that -- a socket and a script
call are cheap; rendering a layer out of the middle of a render queue is not.

---

## Notes

- The physics engine is the *least* risky part of this project. Geometry
  extraction and the bake transform are where the time goes.
- pymunk in the sandbox is a learning vehicle. The engine API will not transfer
  to Phase C; the geometry pipeline and the bake math will. Build accordingly.
  **Amended by Phase C's decision:** shipping Python as a Tauri sidecar means
  even that transfer is deferred, and it should be -- Phases A and B did not
  just write the geometry pipeline, they *verified* it, and a port throws the
  verification away along with the code.
- Two of the corrections in this project were to a MEASUREMENT rather than to
  the thing measured: B1 scored a wrecked path by area, which a self-crossing
  loop cancels to nothing, and B3 measured rebound from the deepest point,
  which is the final resting place. Both read as "the code is fine" and "the
  code is broken" respectively, and both were wrong. When a check surprises,
  suspect the metric before the mechanism.

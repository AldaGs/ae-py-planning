# AE Physics Simulator — Roadmap

**What to do next, in order.** Named to sit beside `ae-physics-sim-plan.md`,
since the repo root holds planning docs for several projects.

Three documents, and they must not duplicate each other:

| | holds | read it when |
|---|---|---|
| `ae-physics-sim-plan.md` | the reasoning: walls, decisions, feature scope, why | deciding, or tempted to relitigate |
| `python-proto/physics_sim/WALKTHROUGH.md` | the evidence: every measurement and falsified prediction, step by step | resuming, or doubting a number |
| **this file** | the sequence: status, gates, what each step owes | starting a work session |

If a fact appears here *and* in the plan, the plan wins and this file should be
cut back to a pointer. This file goes stale fastest; it is the one to distrust.

---

## The loop, as it runs today

```
(in AE)   File > Scripts > Run Script File...  b1_read_shapes.jsx   ->  scene.json
          python b3_loop.py scene.json --gravity 9.8 --frames 240   ->  bake.json + preview.png
(in AE)   File > Scripts > Run Script File...  b2_apply_bake.jsx    ->  keyframes + report.json
          python b2_apply.py                                        ->  verify the report
```

`python b3_loop.py --help` for the parameter set. `python jsx_check.py <file>.jsx`
before ever running a script in AE — AE is otherwise the only syntax check these
files get.

---

## Status

**Phases A and B are complete and verified in real After Effects (26.3x87).**
AE holds what the solver computed to **0.0000 px / 0.0000°**, with straight-line
tweens. 116 checks across eight steps, all green.

**C0 is complete too — all three spikes pass** (2026-09-07/08). The architecture
gate is green, the protocol choice turned out to be free, and Wall I's cost is
gone. **C1 is next.** Evidence in `ae_physics_simulator/SPIKES.md`, which also
records what each result does *not* cover.

| Step | What it settled | Checks |
|---|---|---|
| A1 | world, bake spine, y-down convention, ppm band | 13 |
| A2 | anchor ↔ COM transform, both directions | 10 |
| A3 | bezier paths → convex bodies | 15 |
| A4 | rendered alpha → bodies, on four real exports | 16 |
| A5 | the preview, and whether it catches a bad bake | 11 |
| B1 | `ae-physics-scene/1`, the reader, run on a real comp | 22 |
| B2 | the apply, the round trip, Wall I measured | 14 |
| B3 | one command, real parameters, staleness guarded | 15 |

### Walls

| | | |
|---|---|---|
| A | alpha is invisible to script | **open — it is Phase D's whole point** |
| B | contour topology | closed in A4 |
| C | concavity | closed in A3, amended by A4 (slivers real, ≤1% of mass) |
| D | units | measured in A1; usable ppm band 10–1000, settled on 100 |
| E | anchor vs centre of mass | closed in A2 |
| F | rotation unwrapping | closed in A1; A5 found it is invisible on stills |
| G | timestep vs frame rate | closed in A1 |
| H | determinism | closed in A1, extended to pixels (A5) and documents (B1) |
| I | keyframe volume | closed in B2 — and not where it was assumed; **C0.3 then removed the cost**, 88.5 µs/key native vs 853 |
| J | one layer is one transform | recorded; weld-and-warn, split deferred to C5 |
| K | a hand-carried loop goes stale | found and guarded in B3 |

---

## Critical path

### C0 — the spikes

Throwaway, pass/fail on a measurement, same gate as pieFX's Phase 0. **The
native side lives in its own repo, `AldaGs/ae_physics_simulator`** (cloned into
the SDK tree at `Examples/Template/PhysBridge`, where its include paths
resolve). `AldaGs/pieFX` supplied the AEGP scaffolding, script execution and the
pipe transport.

- [x] **C0.1 — the bridge. PASSES** (2026-09-07, AE 26.3x87). The AEGP ran
      `b1_read_shapes.jsx` through `AEGP_ExecuteScript` and returned a scene
      document **byte-identical** to the save dialog's: 2,185 bytes, sha256
      `b589df0b0325b543` on both sides, **16 ms** for the whole round trip.
      **The gate is green — Phase C's architecture stands**, and B1/B2's
      ExtendScript is *reused* rather than rebuilt as `evalScript` calls.
      Evidence in `ae_physics_simulator/SPIKES.md`.
- [x] **C0.2 — payload size. PASSES** (2026-09-08). **There is no ceiling
      anywhere near the bake.** Both roads — escaped into the script text, or a
      temp file the script opens — carry the real 145,090-byte `b2_bake.json`
      intact, and neither breaks below **32 MB**, 231× the bake, in either
      direction. They cost the same, to within the clock's resolution.
      The pipe was measured separately and is not a constraint either: 32 MB
      inline at ~79 MB/s, the bake in ~15 ms.
      **So the protocol choice is free**, and the recommendation is the *file*
      road on structural grounds — B2 already reads a file, the request stays
      tiny, and escaping a payload into source that is then executed is a
      second place to get it wrong. What actually costs is ExtendScript
      touching characters: `eval` of the bake is ~22 ms and the transfer is
      under one clock tick.
- [x] **C0.3 — keyframes from native code. PASSES** (2026-09-08). Yes, and
      the 853 µs/key was **the ExtendScript bridge**, not AE. The LINEAR pass
      costs **88.5 µs/key** natively — about **10×** — flat from 1,000 to
      6,486 keys, and 6,486 is B2's own count so that is a comparison rather
      than an extrapolation. **Fracture is affordable**: fifty shards × 300
      frames projects to **1.3 s** against 13 s.
      Three caveats live with the number. `AEGP_SetKeyframeFlag` is **O(n²)**
      — called per key as B2 does it, native is *worse* at scale — but dropping
      it leaves the path straight anyway. Native's batch add is *slower* than
      `setValuesAtTimes` (29 vs 19.6 µs/key), so the whole win is the
      interpolation pass: **7.4×** across a complete apply, not 10×. And the
      853 baseline is a Position/Rotation blend, so the comparison is generous
      to ExtendScript.
      One thing is assumed rather than measured: *why* dropping the auto-bezier
      call works. Reading the flag before the tangent phase would settle it.
      **This unblocks pre-fracture's Tier 2 gate.**

### Then C1–C5 — the application

- [ ] **C1.** ▶ **next.** Tauri shell: window, scene list, B3's parameters as
      real controls, settings persisted. **Owes `ae-physics-scene/2` + `ae-physics-bake/3`
      carrying every Tier 0 slot** (below), even where the arrays stay empty.
- [ ] **C2.** The viewport: `preview.py`'s renderer becomes the canvas, scrubbing
      the bake before it is applied. A5's argument becomes the main surface.
- [ ] **C3.** Staleness (Wall K) survives the GUI — re-read and compare, never
      trust the `source` block.
- [ ] **C4.** Revisit A4's sliver risk against whatever solver C ends up running.
- [ ] **C5.** One layer becomes N: the output capability behind both Wall J's
      island split and pre-fracture.

### Then D — the AEGP renders

- [ ] `AEGP_RenderAndCheckoutFrame`, alpha → the A4 contour pipeline. Newton
      interprets precomp and footage layers **as rectangles**; this is where that
      stops being true here, and the only honest route to text layers.

---

## Feature backlog

Reasoning lives in the plan under **Feature scope**. Sorted by whether a feature
reshapes the document model, not by popularity.

### Tier 0 — schema slots, before C1 ships a schema

Slots, not implementations. Retrofitting any of these reshapes every document
ever written.

- [ ] `joints[]` as a top-level array (relations between bodies, anchors in comp space)
- [ ] `zones[]` — wind, magnets, explosions
- [ ] animated / kinematic bodies carrying their own input keyframes
- [ ] velocity + contact events in the bake (enables squash, sound sync, triggers)
- [ ] bake target: a new comp, not only in place
- [ ] one input layer may produce N output layers
- [ ] collision groups + collide-with mask

### Tier 1 — table stakes, purely additive

- [ ] per-body linear/angular damping, gravity scale, fixed rotation
- [ ] initial linear + angular velocity
- [ ] convex-hull approximation toggle
- [ ] time divider (slow motion), simulation frame range
- [ ] joint implementations: pivot, spring, weld, distance first
- [ ] scene save/load, snapshots, record pool of takes, autosave, presets
- [ ] per-property randomize

### Tier 2 — better rather than equal

- [ ] alpha bodies for any layer type (gated on Phase D)
- [ ] pre-fracture (gated on C5; the **C0.3 keyframe-cost gate is now open** — fifty shards is ~1.3 s, not ~25 s)
- [ ] squash & stretch (cheap *once* the bake carries contacts + velocity)

### Non-goals, chosen deliberately

Liquid, soft bodies, particle systems. Impact fracture — it changes the body set
mid-simulation and the bake has no birth times.

---

## Invariants — do not break these without a version bump

- **y-down everywhere.** A positive angle reads clockwise, which is AE's
  rotation sense, so there is no sign flip anywhere in the codebase.
- **The join key is the AE layer `id`, never the name.** AE allows duplicate
  layer names; "Shape Layer 1" twice is the default.
- **Path vertices in a scene document are in LAYER space.** Composing shape-group
  transforms down is the *script's* job.
- **One keyframe per frame is the only exact bake.** Any reduction must be
  error-driven against a pixel budget, never a fixed stride.
- **Every key gets LINEAR interpolation with spatial tangents zeroed**, or AE
  bows the motion path between our samples.
- **Never write a bake that leaves the comp.** `escapes()` is a default, not a
  policy — `--allow-escapes` exists because it is the user's comp.

## How work is done here

Every step is **measurements paired with a broken control** — a check that
cannot fail proves nothing. Prototype offline in the sandbox before touching AE
or C++; AE is a place where nothing can be measured. Commit straight to main.

Twice now the wrong thing was the *metric*, not the mechanism: B1 scored a
wrecked path by area, which a self-crossing loop cancels to zero, and B3 measured
rebound from the deepest point, which is where the body ends up resting. Both
read as confident and both were wrong. **When a check surprises, suspect the
measurement before the mechanism.**

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
| I | keyframe volume | closed in B2 — and not where it was assumed |
| J | one layer is one transform | recorded; weld-and-warn, split deferred to C5 |
| K | a hand-carried loop goes stale | found and guarded in B3 |

---

## Critical path

### ▶ Next: C0 — the spikes

Throwaway, pass/fail on a measurement, same gate as pieFX's Phase 0. **The
native side lives in its own repo, `AldaGs/ae_physics_simulator`** (cloned into
the SDK tree at `Examples/Template/PhysBridge`, where its include paths
resolve). `AldaGs/pieFX` supplied the AEGP scaffolding, script execution and the
pipe transport.

- [ ] **C0.1 — the bridge.** AEGP opens a local socket, receives
      `{"cmd":"read_scene"}`, runs `b1_read_shapes.jsx` through
      `AEGP_ExecuteScript`, returns the scene JSON down the socket.
      *Pass:* byte-identical to what the save dialog writes today.
      **This is the gate — the architecture does not stand without it.**
      Watch: `AEGP_ExecuteScript` returns a non-NULL but *empty* error handle on
      success; model AEGP code on Persisto, never Commando.
      *Built, exports a bare `EntryPointFunc`, client written — awaiting a run
      in AE.* Plug-in in `ae_physics_simulator`, client at
      `python-proto/physics_sim/c01_client.py`.
- [ ] **C0.2 — payload size.** Does `AEGP_ExecuteScript` take a 148 KB bake as a
      string, or is a temp file needed? Measure *where it breaks*, not just
      whether it works. Shapes the protocol either way.
- [ ] **C0.3 — keyframes from native code.** Can `AEGP_KeyframeSuite` beat
      ExtendScript's 853 µs/key interpolation cost? Wall I's only remaining
      lever. Was an optimisation; **fracture makes it a requirement** — fifty
      shards is ~25 s of interpolation alone.

### Then C1–C5 — the application

- [ ] **C1.** Tauri shell: window, scene list, B3's parameters as real controls,
      settings persisted. **Owes `ae-physics-scene/2` + `ae-physics-bake/3`
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
- [ ] pre-fracture (gated on C5, and on C0.3 for the keyframe cost)
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

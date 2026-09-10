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
gone. Evidence in `ae_physics_simulator/SPIKES.md`, which also records what each
result does *not* cover.

**C1 IS COMPLETE** (2026-09-10). The whole loop now runs from a window
outside After Effects: read the comp over the C0.1 bridge, simulate, apply.
**AE holds the solver to 0.000057 px/deg with straight-line tweens** on a real
four-layer comp — C1.0's schema gate and C1.1's shell gate both closed in one
sitting, and the details, including the two things that sitting did NOT close,
are under C1.2 below.

Wall K's guard was then measured rather than argued: a nudged Position applies
anyway, because the bake's `source` block describes the scene and the apply
script can only see the comp. The shell now re-reads and compares hashes (C3,
built and unit-tested, not yet exercised live).

**The next step is C2, the viewport.**

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

- [ ] **C1.** ▶ **in progress.**
  - [x] **C1.0 — the schema. DONE** (2026-09-09). `ae-physics-scene/2` and
        `ae-physics-bake/3` carry every Tier 0 slot, arrays empty and scalars
        at their defaults. 21/21 checks in `python-proto/physics_sim/c1_schema.py`.
        Three things are worth carrying forward. **/1 is read, not migrated** —
        `scene_io.upgrade()` is the only code that knows the difference, so
        B1's captured export and hand-authored fixture stay usable as evidence;
        the fixture and its upgrade bake byte-identically, and the September
        bake of the real comp reproduces to the byte once its /3 fields are
        stripped. **A filled slot is refused, not ignored** — `load_scene`
        names the slot and the phase that implements it, because a layer marked
        kinematic and simulated as dynamic knocks over exactly the stack it was
        authored to knock over, only wrongly. And **an empty array is two
        facts**: the bake's `recorded` flags separate "nothing touched anything"
        from "nobody was writing it down".
        **Still owed: a run in AE.** No comp has yet produced a /2 document —
        every /2 in existence came from the upgrade path. `b1_read_shapes.jsx`
        emits /2 and passes `jsx_check.py`, which is not the same as having run.
  - [x] **C1.1 — the shell. BUILT** (2026-09-09), in
        `ae_physics_simulator/app` — one clone gets both halves of the product,
        and nothing in the MSBuild solution knows `app/` exists. Tauri 2 +
        vanilla TS; the solver is `b3_loop.py` in a subprocess against the
        existing checkout, so PyInstaller stays a packaging step rather than a
        prerequisite for seeing a window.
        Bridge light, read comp, scene list with click-to-pin, B3's parameters
        as controls at B3's defaults, simulate, settings saved on every change.
        **Every control is an argument to `b3_loop.py` and nothing else** —
        `b3_checks.py` already runs that command in a subprocess rather than
        calling `run()` in-process, and a GUI that reimplemented the loop would
        be a second implementation with its own bugs.
        It deliberately does **not** know the schema (the scene is parsed
        shallowly in the front end, because a third implementation after
        `scene_io.py` and the jsx is a third place to drift) and does **not**
        apply the bake.
        **It found a real defect in the transport.** The pipe server holds one
        instance of each pipe and re-creates them between clients; a connection
        landing in that window is read, answered, and the answer written to
        nobody (`write: no client connected, 23 bytes dropped` — exactly
        `{"ok":true,"pong":true}`). `c01_client.py` never hit it because a
        person runs it once; an application pings on launch and then reads,
        back to back. The exchange is retried on a close-without-answer and
        only then. Four live pings: 3312 ms, then 31, 78, 27.
        **Still owed: the scene list has never rendered a real comp.** The
        machine's active comp during verification had no shape layers, so what
        was exercised end-to-end is the bridge, the reader running inside AE,
        and the refusal path — not the layer list, the pinning, or a simulate
        run. That and C1.0's `/2`-from-AE gate are the same sitting: open a
        comp with shape layers and read it.
  - [x] **C1.2 — the sitting that closes both gates. DONE** (2026-09-10).
        Comp 1, 1920x1080 @24, four layers — three dynamic, FLOOR pinned, seven
        convex parts — went AE → shell → solver → AE. Both gates are closed:
        the first `ae-physics-scene/2` ever produced by After Effects rather
        than by `upgrade()`, and the first layer list, pinning and simulate run
        from the window.
        Verified against the bake and an in-AE readback (`ae-physics-report/1`):
        **AE holds the solver to 0.000057** over 45 sampled keys, and the tween
        midpoints sit **0.0034 px/deg** off the straight line — B2's
        `makeLinear()` really does defeat spatial auto-bezier on a real comp,
        which is the failure A5 says is invisible on every still. FLOOR got
        `pos_keys=0 rot_keys=0`. The duplicate-name trap fired and was survived:
        ids 1 and 3 are both "Shape Layer 1" and end in different places.
        Apply cost 5,412 ms, **~826 us/key** interpolation — B2's 853 confirmed
        in the wild, and still the ExtendScript path: **C0.3's 88.5 us/key
        native keyframing is measured but NOT wired into the product.** Spatial
        tangent arity 3 against a 2-element value, as B2 found.
        **Two things that sitting did not close.** It ran at **ppm 200, not
        B3's 100**, so "the shell reproduces a hand run byte-for-byte" is still
        untested. And layer 3 reaches **199.7°** — AE stored it unwrapped, so
        Wall F did not bite, but the crossing is between frames 69 and 70 and
        `sample_stride=17` sampled tweens at 68.5 and 85.5. The one interval A5
        says the damage hides in is the one interval nobody looked at.
        **It also needed a pinned interpreter.** `pymunk` was installed only in
        the per-user site-packages, which resolves from a shell and not from the
        Tauri app — same `python.exe`, different answer, reproducible with `-s`
        or with `APPDATA` unset. `solver.rs` does not touch the child
        environment. Fixed with a venv at `.venv-physics` pinned as
        `paths.python`, which is the case `settings.rs` already anticipated.
- [~] **Wall I, spent.** C0.3's native keyframing is **wired into the product**
      (2026-09-10): `apply_bake` in the AEGP, an Apply button in the shell, and
      Wall K checked in the backend before the bridge is touched so a front end
      cannot bypass it. `b2_apply_bake.jsx` stays as the reference
      implementation — every native number is a comparison against it, and one
      you can no longer run is one you can no longer check.
      The O(n^2) auto-bezier pass is **skipped**, per C0.3. But C0.3 measured
      that it was safe to skip and *assumed why*, and the product now depends on
      it — so the apply reads the flag back on a spread of keys and reports the
      count. If AE stops behaving the way C0.3 measured, the window says so
      rather than bowing every motion path invisibly.
      **Built and unit-tested, NOT yet run in AE.** The `.aex` needs an
      elevated copy into the Plug-ins folder, and the numbers above are C0.3's,
      not this code's. Nothing here is verified until an apply runs from the
      window and the reported us/key is read off a real comp.
- [ ] **C2.** The viewport: `preview.py`'s renderer becomes the canvas, scrubbing
      the bake before it is applied. A5's argument becomes the main surface.
- [~] **C3.** Staleness (Wall K) survives the GUI — re-read and compare, never
      trust the `source` block. **The guard is built and unit-tested
      (2026-09-10); it has not yet refused a stale bake in a live sitting.**
      **Wall K's gap was measured, not argued.** Nudge a layer's Position after
      simulating and `b2_apply_bake.jsx` applies the bake anyway, overwriting
      the nudge from keyframe 0. Its four checks are comp name, comp
      dimensions, layer id in range, and layer name at that id — and its own
      comment says why: *"Comp identity is the half AE can check."*
      The reason is structural rather than an oversight. The bake's `source`
      block describes **the scene**; the apply script can only see **the comp**;
      names and dimensions are the entire overlap. Every geometric field —
      position, rotation, scale, anchor, paths — lives in the scene and is
      invisible from inside AE. `scene_sha256` *is* compared, at
      `b3_loop.py:97`, but against a scene file on disk, and AE has no scene
      file. The hash is not ignored there; it is unreachable.
      **What closes it:** the shell re-reads the comp through the bridge,
      hashes the bytes, and compares them to `source.scene_sha256`. Sound only
      because the scene document is deterministic — it carries no timestamp
      (`made_at` is on the BAKE) — so the same comp hashes the same twice. If a
      nondeterministic field is ever added to `ae-physics-scene`, this guard
      becomes a permanent false alarm, and a guard that always fires is a guard
      nobody reads.
      It **reports rather than blocks**, deliberately unlike the solver's
      escape guard: re-reading after moving a layer you did not simulate is
      legitimate, and the honest sentence is "this was computed from different
      geometry", not "you may not". Five tests, and the load-bearing one asserts
      the identity checks stay **empty** on a nudged Position — so it cannot
      pass for the wrong reason and keeps proving the hash is what catches it.
      Still owed: it has only been exercised offline. The live sitting is to
      nudge a layer and watch the window say so.
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

### Tier 0 — schema slots, **shipped empty in C1.0**

Slots, not implementations. Retrofitting any of these reshapes every document
ever written, which is why the shape landed before the shell. Each box below is
now a *slot that exists and is refused when filled* — ticking it later means
building the behaviour, not changing the document.

- [x] `joints[]` as a top-level array (relations between bodies, anchors in comp space) — slot in `ae-physics-scene/2`
- [x] `zones[]` — wind, magnets, explosions — slot in `ae-physics-scene/2`
- [x] animated / kinematic bodies carrying their own input keyframes — `layer.motion` + `layer.input_keyframes`
- [x] velocity + contact events in the bake (enables squash, sound sync, triggers) — `layer.channels` + `contacts[]`, with `recorded` flags
- [x] bake target: a new comp, not only in place — `output.target`, refused by `b2_apply_bake.jsx` when it is not `in_place`
- [x] one input layer may produce N output layers — `layer.outputs` + the bake's `extra_outputs[]`
- [x] collision groups + collide-with mask — `layer.collision_group` + `layer.collide_with` (16-bit)

None of them is implemented. All of them are refused by name rather than
ignored — see C1.0 above for why that is the load-bearing half.

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

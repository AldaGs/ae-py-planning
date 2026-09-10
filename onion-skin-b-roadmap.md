# Onion Skin — Option B (managed guide layer + control panel) roadmap

> Successor to `onion-skin-roadmap.md`, which is closed. Read its header for why
> Option A died; read "If we jump to B" at its foot for what carried over.

**Goal.** Onion skinning that appears in the comp viewer, in previews and in
exports, driven by controls the user can reach **without selecting a layer**.

**Shape.** Three pieces, deliberately separated:

| Piece | Kind | Responsibility |
|---|---|---|
| `onionSkin.aex` | effect (SmartFX) | checks out its input at time offsets, tints, fades, composites |
| the managed layer | AE project state | an adjustment/guide layer the user never has to think about |
| `onionSkinPanel` | AEGP + `AEGP_PanelSuite` | dockable panel; creates/finds the layer, writes the effect's params |

**The rule that keeps this honest:** the effect's params are the single source of
truth. The panel *writes streams*; it holds no state of its own. Everything
therefore stays undoable, keyframeable, and saved in the project for free. A
panel that caches its own values is the first bug in this project and the hardest
to see.

## The premise, stated so it can be falsified

Option A's premise was about pixels. B's is about **reach**:

> The user can turn onion skinning on, change opacity, change frame counts and
> change colours, at any time, without selecting the onion-skin layer — and the
> comp view updates as if they had.

If the panel can't be built, or can't write params, or writing params doesn't
invalidate the render, then B degrades to "an effect you must select", which is
what the user explicitly asked us to avoid. That is the thing to falsify first.

The rendering half of B is *not* at risk. `PF_CHECKOUT_PARAM` at a time offset is
documented, ordinary, and already exercised in this tree. It is Phase 1 work, not
gate work.

## What is genuinely uncertain

Three things, and only three:

1. Whether one `.aex` can carry both an effect entry point and an AEGP entry
   point, or whether B ships two binaries.
2. Whether an AEGP panel can write another plug-in's effect params such that AE
   re-renders — and whether it can do so *without* stealing the user's layer
   selection.
3. What a panel actually costs to draw. `AEGP_PanelSuite` hands over a platform
   window handle and stops. The widgets are Win32 / Cocoa, written by us. **This
   is the real price of the feature, and it is the one most likely to be
   underestimated.**

Everything else — creating a layer, finding it again, tint maths, fade curves —
is either ordinary or already carried over from A.

---

## Phase 0 — the gate — **COMPLETE, 2026-09-09. BOTH GATE ROWS GREEN.**

> B2 and B3 both pass. B5 passes. B1 answered (two binaries). B4 not run — and
> now optional rather than pending: B5 showed shortcuts carry the
> highest-frequency interaction, so the panel's widget budget is a Phase 2
> scoping question, not a gate.
>
> **Phase 1 is unblocked.**

Same discipline as Option A's Phase 0: **throwaway spikes, hardcoded inputs, log
output, no onion-skin logic.** Measurements with a broken control, never
assertions.

### B1 — Binary: one `.aex` or two? — **ANSWERED: TWO, 2026-09-09**

> A single `.aex` declaring both PiPLs (16000 effect, 16001 AEGP) built fine —
> both entry points exported, both resources emitted — but AE loaded only the
> effect and never called `EntryPointFunc`. **One `.aex` is claimed by one kind.**
>
> The mechanism is deliberately not pinned down: "first PiPL wins" and "a file
> already claimed as an effect is skipped by the AEGP scan" predict the same
> observable, and the remedy is identical. Shipping as `osB3fx.aex` +
> `osB3panel.aex`, which find each other by match name — how Phase 2 was always
> going to work, so the split costs nothing architecturally.

Build a stub that declares both an effect and an AEGP entry point in its PiPL.
Load it in AE.

- Measure: does the effect appear in the Effect menu **and** does the AEGP
  `EntryPointFunc` get called at startup (log a line with a timestamp)?
- Broken control: a second build with the AEGP PiPL resource deliberately
  removed. Its startup line must be **absent**. If both builds log, the log is
  lying about where it came from.
- `dumpbin /EXPORTS` must show a bare `EntryPointFunc` in the passing build.

**Failure is cheap and expected-survivable:** ship two binaries, panel finds the
effect by match name. Record the answer; do not spend a second day on it.

### B2 — Panel: does a dockable panel exist and survive? — **PASS, run 1, 2026-09-09**

> Docks beside Effect Controls, survives a workspace switch and a full AE
> restart (AE recreates it from the saved workspace with no menu command), and
> clicks reach us with a single layer selected, a *different* layer selected, and
> **nothing selected**. That last row is the gate, and it holds. Full scoring in
> `OnionSkin/B2/B2_RESULT.md`; log at `_spikes/B2_run1.txt`.
>
> One runbook defect recorded there: it told the operator to read session
> boundaries off a `GetTickCount` reset, which cannot happen — that clock counts
> from system boot. A check that can only fail is as useless as one that can only
> pass.


Register one panel via `AEGP_PanelSuite`. Draw a single hardcoded rectangle and
one working button that writes to the log.

- Measure: appears under Window ▸; docks beside Effect Controls; **survives**
  closing and reopening AE, and a workspace switch.
- Measure: the button responds while a *different* layer is selected, and while
  *no* layer is selected.
- Broken control: a second button wired to nothing. It must log nothing when
  clicked. (Carried from pieFX S2 — a handler that fires on every click looks
  identical to a handler that works.)

### B3 — Write: can the panel change the effect's params, and does AE notice? — **PASS, run 1, 2026-09-09**

> **151 writes, zero selection changes.** `sel 23 -> 23`, `25 -> 25`, `-1 -> -1`,
> never once different across the arrow. Option B's premise is measured.
>
> Three findings carried into Phase 2, all of them things the spike existed to
> find:
> 1. **Panel controls may not call AEGP project APIs from their window callback.**
>    AE answers "no current context". They queue; an idle hook writes. Latency
>    median 27 ms, p90 43 ms, max 54 ms — under two frames at worst.
> 2. **Keyframed streams cannot be written** with `AEGP_SetStreamValue` (29
>    `REFUSED` lines prove the guard fires). Phase 2 needs a keyframe-aware path
>    or an honest refusal.
> 3. **Undo groups must be balanced by a flag, not by call order under `ERR`** —
>    `ERR(FUNC)` skips on a prior error, so `ERR(Start)` + bare `End()` is
>    unbalanced on the error path. That was the operator's one-off "Group
>    Mismatch", and it was our bug.
>
> Full scoring in `OnionSkin/B3/B3_RESULT.md`; log at `_spikes/B3_run1.txt`.

The gate proper. Panel button calls `AEGP_SetStreamValue` on a slider of an
`onionSkin` effect on a layer that is **not selected**.

- Measure: the comp viewer repaints with the new value, with the panel still
  focused and the selection unchanged. Timestamp the write and the repaint.
- Measure: the layer selection is **the same object** after the write as before.
  Log the selected layer's ID either side. This is the failure mode that would
  make the panel unusable while animating.
- Measure: one Ctrl+Z restores the old value, and does so in **one** step, not
  several.
- Broken control: write the value the param already holds. There must be no
  repaint and no undo entry. If a no-op write still repaints, we are measuring
  AE's idle redraw, not our write.
- Note whether `PF_OutFlag_NON_PARAM_VARY` is needed here. It probably is not
  (params *are* varying), but the effect reads neighbouring frames, so record
  what the cache actually does rather than reasoning about it.

### B4 — Cost: what does one real widget cost? — **ANSWERED BY DOING IT, 2026-09-09**

> Never run as a spike. Overtaken: the panel shipped with steppers, ran in real
> use, and the sliders were then written directly. Three drag sliders plus an
> owner-drawn pill button came to roughly 200 lines of Win32 — the multiplier
> this spike existed to produce, measured on the real thing instead of a
> stand-in.
>
> The finding the spike would have missed: the expensive part of a slider is not
> drawing it, it is deciding **when to commit**. A write per mouse-move gives
> live feedback and one undo entry per pixel of travel; a write on release gives
> one clean undo and no preview until the finger lifts. Shipped on release, and
> the tradeoff is written into the runbook where it can be argued with.

Build **one** slider — a real one, with drag, live update, a number readout, and
correct behaviour at the ends — on a Win32 panel, and on Cocoa if a mac session
is available.

- Measure: hours spent, and lines of code, for that one widget.
- This spike has no pass/fail. Its output is a multiplier. Four control groups ×
  that number is the panel's Phase 2 budget, and if the number is ugly the
  answer is not to cut the panel but to cut its ambition (see B5).

### B5 — The cheap escape: commands and shortcuts — **PASS, run 1, 2026-09-09**

> Three commands in the Animation menu, bound to keys, driving state that the
> panel paints. The keys work. Corroborated beyond the operator's report: the log
> holds bursts of six commands inside one second, a rate no menu-driven run could
> produce.
>
> **One row not evidenced:** all 62 lines read `selection=one layer, index 4`, so
> the no-selection step was never performed. Probably passes — the commands are
> enabled unconditionally — but reach-regardless-of-selection is Option B's
> central claim and is not something to infer. Open, and cheap. Details in
> `OnionSkin/B5/B5_RESULT.md`.


`AEGP_RegisterCommand` + a menu item. Register "Onion Skin: Toggle" and
"Onion Skin: More / Fewer Previous".

- Measure: the commands appear in the menu, fire, and are **assignable a keyboard
  shortcut** by the user in AE's own shortcut editor.
- Measure: they work with no layer selected.

Run this **before** B4, not after. It is a day, it needs no GUI code, and it
covers the highest-frequency friction (toggle on/off while drawing) on its own.
If B4's number comes back ugly, B5 plus a minimal panel is the shipped product.

### Gate table

| Spike | Question | Pass condition | Blocking? |
|---|---|---|---|
| B1 | one binary or two? | either answer passes | no — **ANSWERED: two** |
| B2 | panel exists and persists? | docks, survives restart, responds with no selection | **yes — PASSED run 1** |
| B3 | panel writes params, AE re-renders, selection intact? | repaint + selection unchanged + single-step undo | **yes — PASSED run 1** |
| B5 | shortcut-assignable commands? | toggle fires with no selection | no — **PASSED run 1**, no-selection row still open |
| B4 | what does a widget cost? | no pass/fail; produces a number | no — sets budget |

**Gate rule.** B2 and B3 are the gate. If either fails, the panel is dead and the
product falls back to B5 (commands + shortcuts) plus the ordinary Effect Controls
UI — still a real improvement over A, still shippable, and known within a week.

---

## Phase 1 — POC: the effect alone — **DONE, v1.4, 2026-09-09**

> Shipped as `Template/OnionSkin/fx/`. NumPy proto first
> (`python-proto/onion_skin/`), seven checks with two controls, then the port.
>
> Four defects found by running it, each named by a measurement rather than a
> guess:
> 1. `for (...; !err && ...)` made a failed checkout past the end of the timeline
>    skip every remaining skin *and* the final composite — ghosting stopped dead
>    instead of thinning. **Absence of a frame is not an error condition.**
> 2. **`PF_OutFlag_WIDE_TIME_INPUT` was missing.** AE cached the output as if it
>    depended only on the current frame; ghosts went stale until a manual purge.
> 3. **Layer params arrive empty.** `params[i]->u.ld.data` is always NULL before
>    render — `PF_CHECKOUT_PARAM` is the only read *and* the only way to test
>    whether one is set. See [[ae-layer-params-need-checkout]].
> 4. A checked-out layer param carries **none of its comp transform**, so it
>    ghosted artwork but not animated position.
>
> **Source Layer params retired in v1.4.** The alpha constraint is documented
> (`fx/USAGE.md`) rather than engineered around: the adjustment-layer placement
> ghosts every kind of animation including transforms, and that is what character
> work needs. Disk IDs kept reserved and invisible — deleting them would renumber
> everything after them.

No panel. Effect on a manually created adjustment layer.

- `PF_CHECKOUT_PARAM` at ±k frames; composite N−1 and N+1 over the input.
- Tint and fade maths carried from Option A unchanged.
- Verified against A1's calibration comp, which already knows what correct looks
  like.

**Prototype the tint/fade compositing in NumPy first**, per standing project
rules. The effect is a place where nothing can be measured.

## Phase 2 — MVP: the managed layer and the panel

- AEGP creates the layer on demand, names it, marks it a guide layer, keeps it at
  the top, and removes it cleanly. The user experiences a toggle.
- Panel drives the params from B3's mechanism. Scope set by B4's number.
- Layer is found by match name, not by index or by a stored ID — layers move.

## Closed question: controls *inside* the comp viewer

Asked 2026-09-09, after B2 passed. **Answer: not as always-on controls, and the
docked panel stands.** Recorded here so it is not re-opened.

- **Custom Comp UI** draws and clicks inside the viewer (Corner Pin's handles do
  exactly this) but AE delivers those events only while the effect is selected in
  Effect Controls — controls that appear only when the layer is selected do not
  remove the friction they exist to remove.
- **A topmost overlay over the viewer** (the pieFX mechanism) is refused for a
  *different* reason than Option A's, and the distinction matters: a button bar
  anchored to a panel corner needs the panel's screen rect, not the comp's pan,
  so A's geometric ceiling does not apply. It is refused because it still rests
  on **A2, never solved — nothing in AE can name its panels.** A3c's workaround
  was a five-second hover to point at the viewer. That is not a way to reach an
  on/off button.

The real answer for in-viewer *reach* is **B5**: an animator with a hand on the
pen wants a key, not a button to travel to.

## Phase 3 — on-canvas gizmos (optional, and only here)

Custom Comp UI (`PF_Event_DO_CLICK` / `DRAW`) for handles that only make sense
when the effect *is* selected. Explicitly **not** a substitute for the panel: comp
UI events arrive only while the effect is selected in Effect Controls, which is
the exact friction the panel exists to remove. Deferred on purpose.

## Phase 4 — macOS

Cocoa panel. Everything else is portable. Note that B4's mac half, if it was ever
run, is the estimate for this phase.

---

## Standing rules for this project

Carried forward from Option A, unchanged and still load-bearing:

- **Write checks as measurements plus a broken control, never as assertions.**
- **When the claim is about what the user sees, the check has to be what the user
  sees.**
- **Prototype pixel maths in NumPy before porting to C++.**
- `dumpbin /EXPORTS` must show a bare `EntryPointFunc`. Model AEGP code on
  `Persisto`, never `Commando`.
- AEGP build output goes to `_build_out\AEGP\` and deploys to AE's
  `Support Files\Plug-ins\AGS\`; the effect half deploys to the MediaCore path.
- Param disk IDs are append-only. The panel writes params **by match name and
  index**, which makes an inserted param a silent cross-wiring bug in two places
  instead of one.

## New rule, specific to B

- **The panel holds no state.** Every control reads its displayed value from the
  param stream and writes back to the param stream. If a value can be out of sync
  between panel and Effect Controls, it will be, and the user will trust the
  wrong one.

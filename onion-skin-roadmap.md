# Onion Skin — Option A (viewport overlay) roadmap

> **The live roadmap is now `onion-skin-b-roadmap.md`.** This file is the record
> of a closed line of work, kept for its findings.

> ## CLOSED 2026-09-09 — Option A is dead, and the gate is why it cost only Phase 0
>
> Called after A3f run 1 in AE: the errors seen in normal use would cost the
> customer. **The deciding number is not the 27.8% blind — that was fixable.** It
> is the projection of the *fully repaired* detector: 86–95% recoverable across
> the working range, but **65.3% at 100% zoom** and 49% at 150%. 100% is where
> animators work, so a third of pan positions there would show no overlay at all.
>
> The limit is geometric. At 100% zoom the comp is larger than the panel, and a
> transform recovered from the comp's own edges cannot be recovered when no edge
> is on screen. Three architectures were tried for `t` — infer from input,
> measure from edges, hold the last `s` — and each works when it is not needed
> and fails when it is. That is a missing input, not an unfinished
> implementation: **AE does not expose the comp viewer's pan.**
>
> Everything below is kept as the record of how that was established, and because
> the product design, the tint/fade maths, the calibration comp and the
> measurement harnesses all carry over to Option B unchanged.


**Goal.** Onion skinning drawn *over* the comp viewer by an AEGP plug-in, with no
effect applied to any layer and nothing added to the comp.

**Fallback.** Option B: an effect on an AEGP-managed guide/adjustment layer that
checks out its input at neighbouring times. Cheap, certain, and gets the
transform, caching, previews and exports from AE for free. B is the destination
if the Phase 0 gate fails.

## The premise, stated so it can be falsified

Option A is worth building **only if** the overlay can be kept aligned with the
comp viewer to the pixel, continuously, through zoom, pan, scroll, panel resize
and window moves. A misaligned onion skin is not "slightly off" — it is worse
than no onion skin, because it lies about where the previous drawing was.

Everything else in this project is ordinary work. That one thing is the moat, and
it is the thing with no documented API behind it.

## What A and B do NOT share

Worth being explicit, because it kills the usual "build the common core first"
hedge:

| Concern | Option A | Option B |
|---|---|---|
| Pixels for frame N±k | `AEGP_RenderAndCheckoutFrame`, main thread, our cache | `PF_CHECKOUT_PARAM` at time offset, AE's cache |
| Comp→screen transform | we reconstruct it | AE applies it |
| Compositing | our blit, our blend | AE's render pipeline |
| Appears in preview/export | no | yes |
| Invalidation | we detect and repaint | AE invalidates |

Shared: the product design (how many skins, spacing, before/after, tint, fade
curve, which layers), and the tint/fade maths — perhaps a day's work total.

**Therefore: Phase 0 is throwaway. Write no onion-skin logic in it.** The spikes
below answer capability questions with hardcoded inputs and log output. If the
gate fails, the loss is Phase 0 and nothing else.

---

## Phase 0 — the gate

Ordered by *kill probability per hour*, not by dependency. The riskiest,
most A-specific question runs first, with every prerequisite faked.

### A1 — Transform: can we know where comp (x, y) lands on screen?

**The question.** Given a comp point, produce the screen pixel it is drawn at, and
be right at arbitrary zoom, pan, scroll position and pixel aspect ratio.

**Why it is first.** There is, as far as we know, **no AEGP API for comp viewer
zoom or scroll offset**. `AEGP_PanelSuite` creates our own panels; it does not
describe AE's. If this cannot be solved, Option A is dead, and it is dead before
anything else has been built.

**Cheat the prerequisite.** Do not build viewer discovery for this spike. Pick the
HWND by hand — a click-to-pick mode, or a hardcoded handle logged once from
Spy++ — so A1 can run before A2 exists.

**Method.** Two candidate routes; try the cheap one first.

1. *Analytic.* Client rect of the viewer + comp dimensions + zoom + scroll. Hunt
   for a zoom/scroll source: walk child windows for the zoom combo, look for an
   undocumented suite in `AE_GeneralPlug.h`, check `AEGP_ItemViewSuite`.
2. *Empirical.* Screen-capture the viewer rect and correlate it against a
   known-content frame we rendered ourselves, solving for scale and offset. Slow,
   but it is a real fallback and it doubles as A1's own verifier.

**Pass criteria — measured, with a broken control.**

- A calibration comp with markers at the four corners and the centre. For 12
  states (3 zooms x 2 pan positions x 2 panel sizes), predicted screen position is
  within **1 px** of where the marker actually appears in a screen capture.
- Include one **deliberately wrong** transform (e.g. zoom ignored) and confirm the
  harness reports it as failing. A check that cannot fail is not a check.
- A non-square pixel aspect comp is among the 12.

**Fail = go to B.** No partial credit: "within 3 px at 100% zoom only" is a fail.

### A2 — Identity: which window is the comp viewer, and what is it showing?

**The question.** Find the comp viewer's HWND reliably, across layouts, floating
panels, second monitors, and more than one open viewer — and know which comp and
which time it is displaying.

**Known hostile ground (from pieFX S2).** Every AE panel is class
`DroverLord - Window Class`; window text carries only a structural role.
`AEGP_WindowType` is delivered only to the UpdateMenuHook and is stale in
practice. HWND identity is stable and distinct per panel — we can tell panels
apart, we just cannot name them.

**Likely shape of the answer.** Learn the viewer HWND rather than resolve it:
correlate a known comp-time change with which window repaints, or offer a
one-time "click your comp viewer" calibration. Either is acceptable for a v1 if
it survives a layout change.

**Pass criteria.**

- Correct viewer identified in 5 layouts, including a floating viewer and one on a
  second monitor.
- Two viewers open on different comps: we bind to the active one and follow when
  the user switches.
- Survives closing and reopening the viewer without an AE restart.

### A3 — Sync: does it stay glued under motion?

**The question.** Repaint the overlay in step with the viewer during a fast scrub,
a pan, a zoom, and a panel resize, without visible lag, tearing or flicker.

**Why it is a separate gate.** A1 can pass statically and this still fail. AE sits
in a modal loop during a drag and does not pump AEGP idle time (pieFX S2) — so a
pan is exactly the case where our repaint hook is starved and the overlay smears
behind the picture. Expect to need `SetTimer(NULL, 0, ...)`, which pieFX proved
*is* dispatched by AE's modal loops.

**Pass criteria — judged by eye, deliberately.**

- Scrub the CTI across 100 frames at speed, pan hard, zoom with the scroll wheel,
  drag the panel divider. Overlay stays glued; no smear, no flash of stale
  content, no tearing.
- Recorded to video and watched. A message trace measures messages, not what the
  user sees — the exact mistake made in pieFX S2.
- Overlay hides correctly when the viewer is occluded, minimised, or on a hidden
  tab.

### A3e — Slip: is one frame of capture latency good enough?

**The question.** A3c killed inferring `t` from input, so `t` is *measured* from a
capture of the viewer, and A3d2 measured that capture at 16.7 ms — one
composition sync, one display frame. A3e asks the only version of "is that good
enough" that can be answered: **how many pixels does the overlay slip, and under
which gesture?**

**Scope it before fearing it.** `t` changes only under pan, zoom, panel resize and
Fit. It does not change while scrubbing the CTI, which is the dominant onion-skin
gesture — so this latency never touches the main use case. And A3c incidentally
measured a real wheel-scroll at ~160 px/s, which at 16.7 ms is **2.7 px**.

**Method.** `os_A3e.exe`, out of process and needing no AE at all: the capture
recovers both `s` (span ÷ comp size, two axes) and `t` (the top-left crossing).
Capture runs on its **own thread** — the 16.7 ms is a sync wait, not CPU, so it
never lands in the paint path. This is the shipping configuration, not a stand-in.

Two boxes: **green** from the newest capture, **magenta** from a deliberately
100 ms-stale one. Magenta is a control **of the instrument** — an analysis that
cannot separate it from green was never able to detect a lag problem, and so may
not report the absence of one.

Two measurements, and neither is sufficient alone: the paint log gives sub-pixel
slip at 60 Hz but measures **the pipeline only** and is reported as a **lower
bound**; the **screen recording is what the verdict is read from**.

**Pass criteria — pre-committed, judged on the video, binned by gesture.**

- at rest ≤ 1 px, wheel scroll ≤ 5 px, hand drag ≤ 20 px → **GDI ships**, and
  Windows.Graphics.Capture becomes a polish item rather than a gate.
- worse → **WGC required on Windows**, before Phase 1.
- magenta not separable from green while moving → **INVALID**; fix and re-run.

**Note that WGC's real case is not latency.** Both are paced by the same display
frames, so half a frame of quantisation slip is unavoidable either way. WGC's
actual wins are **occlusion** (a screen blit reads the desktop, so any overlapping
window corrupts the read) and **self-capture** (it never sees our own overlay,
retiring the alpha-gap constraint). Those are correctness arguments and they
outrank the pixel count.

### A3f — Blindness: can `t` be found when the comp bounds are off screen?

**Why it exists.** A3e run 1 answered the latency question and turned up a bigger
one. For **12.6%** of a 60 s run the detector rejected every capture and the
overlay froze while the picture moved. Two causes, both measured off the
recording: the comp drawn **larger than the panel** (here at `s > 0.675`), and
the single fixed sample line **missing a small comp** at low zoom.

**Why it is a gate rather than a chore.** High zoom is when onion skinning is
wanted most, and high zoom is exactly where the detector is blind. This is not a
corner case; it is the main case.

**The ladder, cheapest first.**

1. **One AXIS instead of both** — stage 1a, and this is the correction that
   matters. Run 1 rejected the whole frame if either axis failed, but at 69% zoom
   the comp's left and right edges sat at x=493 and x=1818, both inside the
   panel: the horizontal axis was perfectly measurable and was thrown away
   because the vertical was not. That one policy accounts for the longest blind
   run in the session. (An earlier draft of this section said "one edge instead
   of two". That is stage 3 territory; the cheap win is per-axis.)
2. **Sample several rows and columns**, not one of each — stage 1b. The pixels
   are already captured and each scan is O(w+h), so ten lines cost what two did.
   Kills the missed-line cause outright.
3. **The residual**: comp covering the panel on *both* axes with no edge
   anywhere — roughly ≥ 83% zoom for a 1080p comp in that panel. Either
   frame-to-frame correlation of the captured panel (anchor + delta, integrating
   *pixels* — so unlike A3c it cannot miss a gesture), or degrade honestly.
   **Deliberately not built**: ship honest degradation and let usage decide.

**Run 1 result (2026-09-09): stage 2 PASSES, stage 1a PASSES, the gate FAILS.**
Blindness is now visible as blindness — video and paint log agree to within a
point on green / amber / hidden. Per-axis acceptance recovered 18.3% of captures
that were fully blind before. But 27.8% were still fully blind against a <1%
criterion, and *none of it was the unrecoverable regime*.

**And the ladder above is in the wrong order.** A geometry sweep over every
reachable pan position, worst axis:

| zoom | 5 lines, 2 edges | grid 16, 2 edges | **grid 16, 1 edge** |
|---|---|---|---|
| 0.05 | 0.0% | 82.1% | **91.4%** |
| 0.25 | 35.0% | 35.0% | **93.7%** |
| 0.50 | 2.0% | 2.0% | **95.2%** |
| 1.00 | 0.0% | 0.0% | **65.3%** |

More sample lines only matters below ~20% zoom. From 25% up, the binding
constraint is the two-edge requirement, and **only the one-edge solve moves it**.
So rung 2 is a free tidy-up and rung 3 is the main event.

**The one-edge solve needs `s`, and `s` cannot be held.** The zoom changed across
**6 of 7** blind runs and a held value would be a median 1463 ms old — blindness
is *caused* by zooming, so the zoom is exactly what changes while blind. The same
shape as the A3c lesson. `s` must come from `views[i].options.zoom` (A3b:
0.19 ms, live during a drag), which means **a component inside AE publishing one
double to the overlay process**. The overlay itself stays out of process; what
must never return is drawing or capture on AE's UI thread, which is what caused
the freezes. That is the open decision.

**Stages 1 and 2 were built and self-tested offline (2026-09-09);** the detector's
new within-axis vote is proven against synthetic panels including three broken
controls, and the cross-axis zoom check is *kept* for when both axes are present,
so the fix added a control rather than trading one away.

**Regardless of the ladder: blind must be visible as blind.** The probe held its
last good `t` and kept drawing a confident box. That is the roadmap's founding
failure — an onion skin that lies about where the previous drawing was — and it
is the minimum correction whatever else is built.

**Pass criteria.** Under 1% blind across a matrix that includes 100% zoom and a
comp smaller than a quarter of the panel; and every remaining blind frame is
*shown* as blind rather than drawn stale.

**Fail → Option B, and on the merits rather than as a fallback.** B takes the
transform from AE and is never blind, at any zoom, for free.

### A5 — macOS: is a capture-derived `t` viable there at all?

**Why it outranks A4.** The product is meant to work on macOS, and *everything*
above rests on measuring `t` from a capture. On macOS both
`CGWindowListCreateImage` and ScreenCaptureKit require the **Screen Recording TCC
permission**, granted per-application — and the application prompted is **After
Effects**, not the plug-in. That is a bigger product cost than 16.7 ms and it
applies to every capture route, so it can invalidate Windows work rather than
merely follow it.

**Pass criteria.**

- AE prompts once, the grant persists across AE restarts and plug-in updates.
- Capture cost measured, and the viewer window individually capturable.
- And the non-technical one, decided explicitly: **is "After Effects wants to
  record your screen" an acceptable install experience?**

**Fail is not automatically fatal to A** — it is fatal to *cross-platform* A. The
honest response is to choose deliberately between Windows-only and Option B, now,
rather than discovering the choice halfway through Phase 2. pieFX's
both-platforms precedent does not carry: that work needed no capture and no TCC
grant.

### A4 — Cost: can we get frames N±k fast enough?

**The question.** Render neighbouring frames via `AEGP_RenderAndCheckoutFrame` and
measure what it costs.

**Constraints known up front.** It is main-thread and synchronous, and cannot be
called reentrantly from inside a render. It must be driven from an idle hook, with
results cached keyed on comp time plus a change counter.

**Pass criteria.**

- 5 skins fetched and cached for a moderate comp; steady-state scrub stays
  interactive.
- Cache invalidation is correct: change a layer, the stale skin goes away.
- No reentrancy crash while a RAM preview or a render queue job is running.

### Gate table

| Spike | Pass | Fail |
|---|---|---|
| A1 transform | continue | **stop → Option B** |
| A2 identity | continue | **stop → Option B** |
| A3 sync | continue | **stop → Option B** |
| A3e slip | GDI ships | build WGC before Phase 1 |
| A3f blindness | continue | **stop → Option B** |
| A5 macOS capture | continue | Windows-only, **or stop → Option B** |
| A4 cost | continue | reduce default skin count; re-judge |

A1–A3 are hard stops. A3e, A5 and A4 all have negotiable outcomes, but they are
negotiable in different currencies and must not be traded against each other:
A3e buys engineering (WGC), A5 buys **scope** (a platform, or the whole option),
A4 buys defaults.

**Run order, as of 2026-09-09 (after A3e run 1): A3f → A3e run 2 → A5 → A2
→ A4.** A3e run 1 settled the latency question - the typical slip sits on the
quantisation floor that WGC shares - so WGC's case now rests entirely on
occlusion and self-capture, and it has dropped below A3f and A5.

**Run order before that was: A3e → A5 → A2 → A4.** A5 jumped ahead of A4
because it can invalidate work rather than merely delay it — every route to `t`
on macOS needs a Screen Recording grant that After Effects, not the plug-in, must
be given.

**Time box.** If A1 has not passed by the end of its box, that *is* the fail
result. Extending the box is how sunk cost gets spent.

---

## Phase 1 — POC (only if the gate is green)

One skin, previous frame only, fixed 50% opacity, no tint, Windows only, toggled
from a menu item. Proves the four spikes compose. Still disposable.

## Phase 2 — MVP

Skin count before/after, frame spacing, tint colours, fade curve, layer scoping,
a panel or keyboard toggle, settings persistence. The first code written to keep.

## Phase 3 — macOS

The pieFX precedent is good here: an overlay window and a local event monitor are
the same shape on both platforms, and the Mac plug-in was authored on Windows and
compiled nearly first try. But A1 and A2 must be **re-measured** on macOS, not
assumed — viewer geometry is platform-specific in a way the gesture work was not.

**And the pieFX precedent stops at the capture.** That work needed no screen
capture and therefore no Screen Recording grant; this does, for `t`, on every
frame. That is why the permission question is not deferred to this phase but
pulled all the way forward into Phase 0 as **A5** — a mac answer arriving here
would arrive after Phase 2 had been written against an assumption.

---

## If we jump to B

What survives: the product design, the tint/fade maths, the calibration comps and
the measurement harness from A1 (they verify B's alignment too, for free), and
everything learned about AE's viewer.

What is thrown away: overlay window code, transform reconstruction, the
idle-driven render cache, viewer identity.

B's own shape, for when it is needed: an effect on a guide layer that checks out
its input at time offsets, plus an AEGP that creates, positions and removes that
layer so the user experiences a toggle rather than an effect. This is the BTS
Overlay architecture, already verified in AE.

## Standing rules for this project

- **Write checks as measurements plus a broken control, never as assertions.**
  Carried from god-rays / star-glint.
- **When the claim is about what the user sees, the check has to be what the user
  sees.** Carried from pieFX S2, where a message trace said "suppressed" and the
  user's eyes said otherwise.
- `dumpbin /EXPORTS onionSkin.aex` must show a bare `EntryPointFunc`. Model AEGP
  code on `Persisto`, never `Commando`.
- Build output goes to `_build_out\AEGP\` and deploys to AE's own
  `Support Files\Plug-ins\AGS\`, not the MediaCore path the effects use.

# Onion Skin — Option A (viewport overlay) roadmap

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
| A4 cost | continue | reduce default skin count; re-judge |

A1–A3 are hard stops. A4 is the only one with a negotiable outcome.

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

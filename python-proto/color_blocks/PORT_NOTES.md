# Colour Blocks — AE SDK port notes

Companion to `WALKTHROUGH.md` (the NumPy prototype, steps 1–5). This file
covers only the C++ port.

Source lives in the SDK tree, not in this repo:
`C:\AE_SDK\ae25.6_61.64bit.AfterEffectsSDK\Examples\Template\ColourBlocks\`

    ColourBlocks.h          model, hazards, params, disk IDs, CBInfo
    ColourBlocks_Math.h     the pure math — no AE dependency at all
    ColourBlocks.cpp        params, PreRender, SmartRender, CPU renderer
    ColourBlocks_Strings.*  UI strings
    ColourBlocksPiPL.r      PiPL (the .rc is generated at build time)
    test/cb_math_test.cpp   offline harness
    Win/                    sln + vcxproj

Effect name **Colour Blocks**, match name **aldai ColourBlocks**, under
Effect > ags_utilities. CPU path only so far; no GPU kernels.

## Status
- Builds clean, Release x64 → `C:\AE_SDK\_build_out\ColourBlocks.aex`
- Offline math harness: **25/25 checks pass**, all controls break correctly
- v1.0.0 build 1 **verified in AE**: renders correctly, all controls work
- build 2 adds Transition Speed + two latent fixes below; not yet re-verified

## build 2 — Transition Speed, and answering "does it slow down over time?"

**The perf question, measured rather than guessed.** Suspicion was reasonable:
the effect continuously generates rectangles, and the obvious way to write that
accumulates them. It doesn't — nothing is ever kept, so nothing needs
discarding. A frame is rebuilt from hashes of `(seed,row,chunk,gen)` and two
windows bound how much exists at all. Measured worst-case blocks per frame:

    t ~ 1–9 s      952
    t ~ 90000 s    953

Flat across nearly five decades of timeline. Per-frame counts *oscillate*
(552…977) as `t` and the scroll offset cross window boundaries — 3 vs 4
generations, 3 vs 4 chunks — but they do not grow. So a slowdown deep in a
comp is AE's frame cache filling, not this effect.

The first version of that check asserted the count was *equal* across `t` and
failed. Equality was the wrong assertion; boundedness is the property that
matters.

**Two real latent bugs it did surface:**

1. **Float precision in the strip walk.** The cell walk used absolute strip
   coordinates. Past 2^24 (~16.7M units) a float cannot represent `x` and
   `x+1` distinctly, so `x += bw` stops advancing and *the loop never
   terminates* — AE hangs on a frame, with no symptom resembling a maths bug.
   At default Scroll Speed that is ~39 hours in; at high Scroll Speed it is
   barely an hour. Fixed by walking chunk-local and carrying a screen-space
   `chunkOrigin`. `CB_WavePos` was reworked the same way, using
   `screenX + scroll*(t - birth)` instead of the algebraically identical but
   unbounded `x_strip - scroll*birth`.

2. **Generation lifetime under-counted.** The bound was `sweep + hold +
   blockDur*2`, which is fine at default settings but too small once Time
   Random passes ~100% — generations were dropped while their slowest blocks
   were still mid-transition, so those blocks vanished in a single frame:

        timeRand   worst-life   old-bound   new-bound
             0%       3.4419      3.6000      3.4420
            60%       3.5754      3.6000      3.5770
           120%       3.7097      3.6000      3.7120   <- old too small
           200%       3.8891      3.6000      3.8920   <- old too small

   `CB_BlockLifetime` now accounts for the time shove, the per-block speed
   multiplier at its slowest (`exp(+0.5)`), and Transition Speed.

**Transition Speed** is a rate multiplier on Block Time (200% halves every
transition), leaving Period, Sweep and Hold alone. **0 is a deliberate special
case, not the limit**: an infinitely slow transition leaves every block at zero
progress and the frame goes *empty*. At 0 each block instead holds a static
partial reveal spread by its own phase, giving a frozen mid-transition field.

## The four things AE imposes that the prototype did not

**1. `NON_PARAM_VARY`.** The single most important line in `GlobalSetup`. This
effect varies with *time*, and time is not a parameter. Without the flag AE
concludes nothing changed between frames and serves a stale cached frame — the
effect looks frozen while the playhead moves. (Same trap as
`ae-non-param-vary`, reached from a different direction: there the effect read
state outside its params, here it reads the clock.)

**2. Statelessness.** AE renders frames out of order, in parallel, and caches
them. The prototype was already a pure function of `t`, which is the single
biggest reason this port was straightforward — the layout is recovered from
hashes of `(seed,row,chunk,gen)` every frame, with nothing accumulated.

**3. Resolution.** All geometry is computed in **full-res layout units** and
scaled to device pixels only inside `CB_BlendRect`. Working directly in device
pixels would give a *different layout* at half res, not merely a coarser one —
the block partition itself would change, so a preview wouldn't predict the
final render.

**4. Premultiplied output.** AE worlds are premultiplied. Compositing a
straight colour with coverage as its alpha into a premultiplied buffer is
`dst = colour*cov + dst*(1-cov)` — the same expression, so the coverage blend
needs no special-casing. Only the background fill does (bgRGB × bgAlpha), and
linear mode has to unpremultiply before the transfer curve and re-premultiply
after, which only bites on pixels where `0 < alpha < 1`.

## Things that got simpler in C++

- **No `GEN_BIAS`.** The prototype needed it because numpy seeds must be
  non-negative while generation and chunk indices go negative. The C++ mixer
  takes unsigned words, so a negative index just wraps. One fewer invariant to
  keep in sync between the two implementations.
- **Fully-covered fast path.** Interior pixels (`cov ≥ 1`) skip the blend and
  the transfer curve entirely and write the working-space colour directly. Only
  edge pixels ever pay for linear conversion — which is exactly where it
  matters.

## Why `ColourBlocks_Math.h` exists

Inside After Effects nothing can be measured. You look at a frame, it seems
fine, and that is how a half-pixel positioning error or a stalled bezier solver
ships. So every piece of pure math — hashing, colour transfer, easing,
coverage, row bands — lives in a header that depends on *nothing*, and
`test/cb_math_test.cpp` compiles it standalone and measures it.

    cl /EHsc /O2 /I.. cb_math_test.cpp && cb_math_test.exe

Results, matching the prototype's figures:

    vertical edge is one constant column   col=0.629999 spread=0.00e+00
    analytic centroid exact                max err = 0.00e+00
    CONTROL: snapping misplaces the block  max err = 0.5000
    flat-derivative (0,0,0,1)              residual=9.69e-07 (Newton alone stalls)
    y handles left free, 'back' overshoots peak progress = 1.0927
    exact row count and exact fill         sum == frame height
    hRand=0 gives equal bands              min == max
    CONTROL: different key -> different stream
    size jitter symmetric in log space     mean log-multiplier = -0.0017

Every check carries a deliberately broken control that must fail it — see
`godrays-star-glint-corrections`. The slide test in particular measures
*position*, not mass: snapping a rigid block rounds both edges the same way and
conserves mass, so a mass check passes both paths and proves nothing.

## Build & deploy

Per `ae-build-workflow`: build to a writable dir, close AE first (it locks the
.aex), then copy with an elevated shell.

```
set AE_PLUGIN_BUILD_DIR=C:\AE_SDK\_build_out\
MSBuild ColourBlocks.sln -p:Configuration=Release -p:Platform=x64
```

Flags must match between code and PiPL or AE refuses to load:
`out_flags 0x06000404`, `out_flags2 0x08001400`.

## Not done
- GPU kernels (CUDA / Metal). CPU only.
- macOS project.
- The four custom easing handles are hidden behind the preset popup via the
  DynamicStream suite; not yet exercised in AE.

## Watch out
Disk IDs are **append-only**. Inserting one renumbers every later param and
silently mis-maps saved projects — see `ae-disk-id-append-only`. Bump
`BUILD_VERSION` in the header and `AE_Effect_Version` in the PiPL *together*,
or AE refuses to load with a version mismatch.

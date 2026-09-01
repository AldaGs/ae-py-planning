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
- Offline math harness: **33/33 checks pass**, all controls break correctly
- **builds 1–4 verified in AE**: render correctly, all controls work
- **the macOS project builds and loads** (user compiled it 2026-09-01) — the
  "never compiled" caveat is retired
- build 5 fixes the flicker found in that build; not yet re-verified
- C++ source now lives in its own repo: **https://github.com/AldaGs/colour_blocks**
- macOS Xcode project (`Mac/ColourBlocks.xcodeproj`) **compiles and loads in
  AE** — authored on Windows against the CornerRounder sample and confirmed
  working first try. Ships a Release config (−O3, universal) because the SDK
  samples are Debug-only and this effect is fill-bound, so −O0 reads as "the
  Mac build feels sluggish" with nothing in the code to blame.

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

## build 3 — Transition Speed becomes a real ramp

Build 2 special-cased speed 0, which made the slider jump between 0% and 1%.
The justification given was "an infinitely slow transition leaves every block
at zero progress and the frame goes empty". **That reasoning was wrong**, and
it was wrong in an interesting way: the generation lifetime is *derived from*
the transition duration, so slowing the transition also keeps more generations
alive, and those older blocks have had proportionally longer to progress.

Measured (`test/cb_speed_probe.cpp`), the frame does the opposite of emptying:

    speed   live gens   visible blocks   mean drawn width   caught mid-transition
     100%           4              210              0.910           19.5%
      25%           5              363              0.518           84.6%
      10%           8              650              0.291           95.1%
       1%          50             4770              0.038           18.1%

It already looks frozen at low speed, continuously, with no special case.

What actually goes wrong is **cost**. The fill barely moves (1.79× → 2.01× the
frame — the extra blocks are thinner, so total coverage is nearly constant) but
time goes 9.5 ms → 82.6 ms at 1080p, because far more generations are alive and
each thin sliver is its own poorly-cached pass.

So build 3 puts a **knee at 25%**. Above it, Transition Speed is a true rate.
Below it the rate stops dropping and a *crossfade to a static field* dials in
instead — each block eases toward a fixed partial reveal set by its own phase
and stops erasing. Both ends of the crossfade are full frames, the two regimes
meet exactly at the knee, and the generation window stays pinned there.

    crossfade is continuous through zero    largest step = 0.0033 per 0.1% of slider
    CONTROL: build 2's special case         stepped 0.8499  <- the reported jump
    the two regimes meet at the knee        max mismatch = 0.000296

Cost below the knee is now flat at ~26 ms instead of running to 82 ms. It sits
above the knee's 11 ms because a frozen block is never culled, so every
generation in the window stays drawn and fill rises to 3.09× — bounded, but not
free.

## build 4 — Scroll Direction and Wipe Direction split apart

Two independent controls, so a row's strip can travel one way while its reveal
wave runs the other.

**The trap was in `CB_WavePos`**, where both directions meet in four lines. The
term that walks a block back from where it sits on screen *now* to where the
strip was when its generation was born has to follow the **scroll** — it must
cancel `CB_ScrollOffset` exactly, or the wave detaches from the blocks it is
moving through. Only the mirror at the end, deciding which end of the row goes
first, is the **wipe**. Confusing the two would have presented as a timing bug,
not a direction bug.

**Wipe Direction defaults to "Same as Scroll"** — compatibility, not
convenience. One control drove both until now, so a project saved earlier has
no value for this param; any other default would silently re-time every comp
that used Alternate or Right to Left. Checked rather than assumed:

    Same as Scroll reproduces the old coupled behaviour  wipe == scroll, all 4 modes
    two Random per Row streams are independent           20 of 40 rows differ
    CONTROL: one shared stream                           same shuffle on 40 of 40
    the random draw is mode-independent                  stream stays in step
    wipe popup maps onto the direction modes             MATCH->scroll, then value-1

`CB_DirForRow` and `CB_ResolveWipeMode` moved into `ColourBlocks_Math.h` so
that compatibility claim is measurable offline.

## build 5 — two causes of block flicker, both found by measurement

Reported from a real build as "some blocks flicker". `test/cb_flicker_probe.cpp`
identifies each block by `(row, generation, chunk, index)` — stable across
frames, since the layout is a pure function of those — renders consecutive
frames, and measures how far each block's drawn width moves in one frame step.

**The probe's own first version was wrong** and worth recording: it counted a
block scrolling off the frame edge as a pop, and reported hundreds of phantoms
in every scenario. Tracking screen extent and judging only blocks comfortably
on screen separated the real signal from that noise. A detector needs debugging
like anything else.

Two real causes then separated cleanly:

**1. Transitions finishing inside one frame.** At low Stagger a block's duration
is proportional to its *width*, and Width Random deliberately makes thin
slivers, so the thinnest got durations far below a frame — the whole wipe fell
between two rendered frames and the block blinked on. Pops appeared below ~80%
Stagger, **which is the default**, sitting exactly on the edge; also reachable
at any Stagger with a short Block Time. Fixed by flooring every wipe at
`CB_MIN_TRANSITION_FRAMES` (2.5) frames, using the real frame duration from
`in_data->time_step / time_scale`.

The floor is deliberately not higher. At low Stagger slivers are *supposed* to
snap — that is the control working — so the goal was to stop the blink, not to
slow the wipe.

**2. Frozen blocks evicted mid-draw.** Under build 3's freeze crossfade a block
never finishes wiping out, so it sat at full width until its generation aged out
of the window, then vanished in one frame. `CB_FreezeEnvelope` now fades each
generation in and out by age.

    scenario                worst jump before -> after
    Stagger 0%                    1.000 -> 0.400   pops 294 -> 0
    Stagger 30%                   0.448 -> 0.400   pops 454 -> 0
    Transition Speed 0            0.846 -> 0.046   pops 116 -> 0
    6656 wide, Stagger 0%         1.000 -> 0.400   pops 640 -> 0
    defaults                      0.184 -> 0.184   pops   0 -> 0

**A latent bug the work exposed:** `CB_BlockLifetime` bounded a block's life
using only the `fixed` end of the stagger blend, ignoring `natural` (which is
proportional to block width). With a long Sweep and wide blocks `natural`
dominates and the bound under-counted, culling generations early.
`CB_MaxBlockDuration` now takes the larger of the two ends.

## Is a GPU port worth it? Measured: not yet.

`test/cb_bench.cpp` models the renderer's inner loop over a plain buffer so
this is a number, not an opinion. 1080p, 32-bit float, single threaded:

    time                    9.1 ms
    pixel writes        3687294   (1.78x the frame)
    of which edge px      60774   (1.65%)

**The effect is bandwidth-bound, not compute-bound.** It writes ~1.8 frames'
worth of pixels and does almost no arithmetic per pixel — 98.35% of writes are
interior pixels that skip the blend and the transfer curve entirely. That is
the profile a GPU helps with *least*. MFR already spreads it across cores.

Where it does start to hurt (single threaded; divide by core count for AE):

    1080p, defaults                      9.1 ms    1.79x frame
    1080p, 40 rows / 14px blocks        22.3 ms    1.83x frame
    1080p, Regenerate Every 0.35s       35.4 ms    4.44x frame
    4K, defaults                        39.8 ms    1.78x frame
    4K, 40 rows / 14px blocks           81.3 ms    1.82x frame
    4K, 40 rows / 14px / fast regen    389.8 ms    4.70x frame

    6656x2270 (15.1 Mpx), defaults      74.7 ms    1.79x frame
    6656x2270, 200px blocks             56.9 ms    1.81x frame
    6656x2270, 16 rows                  78.1 ms    1.78x frame
    6656x2270, Regenerate Every 0.6s   167.5 ms    3.03x frame

Cost scales **linearly** with resolution — 1.79x frame holds from 1080p to
15 Mpx. At 6656x2270 that is ~75 ms single threaded, so roughly 5-10 ms once
MFR spreads it across cores.

The superlinear term is **overlapping generations, not resolution**: pixel
writes jump 1.78x → 4.44x when Regenerate Every drops, because more
generations are alive at once and every one repaints the row.

Verdict: not worth it now. Revisit if 4K + dense rows + fast regeneration
becomes a normal working config. Note too that a GPU *generator* pays a
readback whenever the rest of the chain is CPU (4K 32-bit is ~33 MB/frame),
which would eat much of the win — and three kernels plus a byte-exact params
struct is a large maintenance surface to keep in lock-step (see
`godrays-star-glint-corrections`).

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

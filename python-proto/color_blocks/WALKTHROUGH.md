# Colour Blocks — prototype walkthrough

A generator effect: a field of solid rectangles in 5 colours over a background,
wiping in left-to-right and then out left-to-right. Modelled on a reference
image of yellow / red / green / blue blocks on black.

Unlike every other plugin in this repo, there is **no input image**. That
changes what the hard part is: there is no filtering, no neighbourhood, no
multi-pass ping-pong. The entire difficulty is *geometry landing on a pixel
grid*.

## Step 1 — `cb_step1_grid.py`

Three pieces: layout, wipe, coverage.

### Layout
Rows of randomised height; each row an independent run of blocks of randomised
width. Size randomisation is a **multiplier drawn in log space**, so `amount`
0→1 goes from a perfectly regular grid to a 16:1 spread, with "half as wide" and
"twice as wide" equally likely. Drawn linearly instead, a multiplier uniform on
[0.25, 4] is above 1.0 three-quarters of the time and you get a grid of
mostly-wide blocks — none of the thin slivers that give the reference its
texture.

A `gap` probability leaves cells as background. This is not decoration: it is
what makes the layout read as *blocks* rather than as a solid quilt.

### Wipe
Two sweep lines cross the frame left→right; a block shows only the part of
itself between them:

    visible = [ max(x0, trail), min(x1, lead) ]

Each block clips the same two global lines against its own extent, so a block
does not start until `lead` reaches *its* left edge. **Per-block stagger is a
consequence of the geometry, not authored timing data.** `hold` is the
normalised time at which the trailing (erasing) sweep sets off.

Trap, hit while writing this: at large `t` *both* sweeps have left the frame, so
the trailing one has erased everything and the frame is empty. "Fully drawn" is
`hold`, not infinity — hence `render_static()`.

### Coverage — the actual point
Every edge lands on a fractional pixel: from randomised sizes, and from a wipe
edge that moves continuously. We never rasterize. For an axis-aligned rect the
pixel's true integral is closed-form and separable:

    cov(px,py) = overlap_1d(x0,x1, px,px+1) * overlap_1d(y0,y1, py,py+1)
    overlap_1d(a,b,c,d) = max(0, min(b,d) - max(a,c))

This is not an approximation *of* anti-aliasing — it **is** the pixel's exact
area integral, because the shape is a box and the pixel is a box. Cost is one
outer product per block.

Coverage is a light quantity, so the `over` happens in **linear light**.
Compositing coverage in sRGB is the classic every-edge-reads-too-dark bug.

## What was measured, and the control that had to break

`--explain` runs a **slide test**: one block stepped right by 1/8 px at a time,
against a deliberately-broken control that snaps every edge to integers.

The first version of this test measured total **mass**, and both paths passed at
800.0000 — snapping a rigid 40px-wide block rounds both edges the same way, so
the block stays 40 wide and mass is conserved. Snapping does not *destroy* the
block, it **misplaces** it. The measurement had to be position:

    offset   analytic-err   snapped-err
    0.000      +0.000000     +0.000000
    0.250      +0.000000     -0.250000
    0.500      +0.000000     -0.500000
    0.625      +0.000000     +0.375000     <- discontinuity: this is the judder
    1.000      +0.000000     +0.000000

Analytic centroid error is 0 at every sub-pixel offset. The snapped control
drifts to ±0.5 px and jumps discontinuously between 0.500 and 0.625.

Straightness check: a vertical edge at x=20.37 gives a single column of
**0.630000 with spread 0.00e+00** down its whole length — 1−0.37 exactly, and
identical every row. That is what "straight and clean" means numerically.

Generalising the lesson from `godrays-star-glint-corrections`: a check is only
worth having if the broken control actually fails it.

## Step 2 — `cb_step2_stagger.py`

Adds the control that opens the wipe up, and splits background out of the
palette (5 free slots + an independent Background colour).

### The stagger rewrite
Step 1's visible right edge was `min(x1, lead)` — a global line, clipped.
Rewritten as per-block local progress:

    start(b) = x0 / L * hold            when the sweep arrives here
    d0(b)    = (x1 - x0) / L * hold     how long it takes to cross
    p        = clamp((t - start) / d0)
    right    = x0 + (x1 - x0) * p

That is algebraically *identical* — substitute and `x0` cancels. The point of
the rewrite is that the duration is now a free variable:

    d = lerp(d0, block_dur, stagger)

At `stagger=0`, `d == d0` and adjacent fronts line up into one straight edge.
At `stagger=1` every block takes the same time regardless of width, so a wide
slab reveals slowly while the sliver beside it snaps in, and the front breaks
into a ragged cascade. **Start times are untouched at every stagger value**, so
the left-to-right march survives — stagger changes the texture of the front,
never its direction.

The wipe-out uses the same function with its own start line, which is what keeps
"in from the left, out from the left" true at any stagger.

`--time-rand` adds a per-block random shove in time, scaled by the block's own
natural duration so a sliver isn't flung further than a slab. The random phase
is drawn once at layout time and stored on the block — drawn per frame it would
be noise, not stagger.

### Measured
The rewrite claims to be an identity, so it is checked as one, against a control
that must fail:

    stagger=0.00   max|diff| vs step 1 = 9.903e-14   IDENTICAL
    stagger=0.25   max|diff| vs step 1 = 8.714e-01   differs
    stagger=1.00   max|diff| vs step 1 = 8.714e-01   differs

    first-pixel time per block, by stagger:
      stagger=0.00  0.0000 0.0137 0.0873 0.1100 0.2167 0.2328
      stagger=0.50  0.0000 0.0137 0.0873 0.1100 0.2167 0.2328
      stagger=1.00  0.0000 0.0137 0.0873 0.1100 0.2167 0.2328

Rows identical ⇒ stagger moves duration, not start, as claimed.

## Step 3 — `cb_step3_infinite.py`

Three requests — per-block individual transitions, per-row direction, and
continuous generation — that turn out to be one change.

### Generations
The unit stops being "the layout" and becomes a **generation**: a fresh
partition of one row into blocks, seeded by `(seed, row, gen)`, born at
`T(g) = g * period`. Generation `g` wipes in, holds, wipes out and dies while
`g+1` is already being born over it. With `period` shorter than a block's
lifetime the generations overlap, so a row is never empty and never repeats.

Only a bounded window of generations can be on screen, so the render walks that
window around the current time instead of simulating from t=0. This matters for
more than speed: **AE renders frames out of order and caches them**, so a frame
must be a pure function of `t`. Verified by rendering t=4.37 cold, scrubbing all
over the timeline, and re-rendering: `|diff| = 0.000e+00`, bit-identical.

### What regenerates and what does not
Row heights are fixed for all time; only the x-partition inside a row is
redrawn per generation. Regenerating row heights too was the obvious thing to
write and it reads as noise — every row boundary slides at a different moment
and the image never settles into a grid. Fixed rows give the eye a stable
armature for the changing blocks to play against, which is what the reference
image has.

### Per-row direction
Direction changes only which end a block's reveal grows from, and which end of
the row goes first. Both are the same expression on a mirrored coordinate, so
there is one code path:

    ltr:  pos = x0/W,      reveal grows x0 -> x1
    rtl:  pos = 1 - x1/W,  reveal grows x1 -> x0

Modes: `ltr`, `rtl`, `alternate`, `random`.

### Per-block clocks
Each block carries `phase` (time shove) and `dur_mul` (its own transition
speed), both drawn at generation time and stored, so they are stable across
frames. Nothing about a block's timing is shared with its neighbours except its
generation's birth time.

### Measured
    stateless: |cold - after-scrubbing| = 0.000e+00   OK
    coverage over t=0..12: min=0.508 max=0.680 mean=0.591   (never 0)
    gen 5 == gen 5 (reproducible): True
    gen 5 == gen 6 (must be False): False
    rows ltr=[0,2,4] rtl=[1,3,5]

The reproducibility pair is the control: if "gen 5 == gen 5" were False, the
statelessness check above would have passed by accident.

### Gotcha for the port
Generation indices go **negative** near t=0 (generations already dying when the
effect starts). NumPy seeds must be non-negative, hence `GEN_BIAS`; the C++
hash has to tolerate negative generations the same way.

## Param list for the C++ port
Colour 1–5, Background, Gap %, Cell Width, Width Random, Cell Height,
Height Random, Seed, Direction (ltr / rtl / alternate / random), Period,
Sweep, Hold, Block Reveal Time, Stagger, Time Random, Pixel Snap.

`Progress` is gone: step 3 has no start and no end, it runs off the layer's own
time. If a one-shot in/out is still wanted alongside the endless mode, that is a
mode switch, not a param tweak.

## Open
- Cell size in pixels vs. a column/row count (pixels for now; a count would
  make the layout resolution-independent).
- Wipe direction — currently hardcoded left→right both ways, per the brief.


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

## Step 4 — `cb_step4_scroll.py`

Step 3 read "each row moves left to right or inverse" as the direction the
*transition* travels. It meant **physical scrolling**, as a per-row choice.

That breaks the assumption every earlier step rests on: steps 1–3 partition
`[0, width]`, so a row is exactly as wide as the frame. A scrolling row is
**infinite in x**, and the frame is a window sliding along it.

### Chunks — partitioning an infinite line, statelessly
The old layout walked `x = 0; while x < width`. You can't walk an infinite line,
can't walk from 0 to 800000 every frame, and can't keep a cursor between frames
(AE still needs any frame renderable in isolation).

So the x axis is cut into fixed-width **chunks**. Chunk `c` covers
`[c*CHUNK, (c+1)*CHUNK)`, is seeded by `(seed, row, chunk, gen)`, and walks its
own blocks from its own left edge. Drawing a window generates only the chunks it
touches.

**The price, stated plainly:** a block can never straddle a chunk boundary, so
there's a forced cut every CHUNK pixels — one seam per chunk, measured at 10.8%
of edges with CHUNK = 12 cells. It's invisible in practice only because widths
are random anyway, so a forced boundary reads as one more block edge. CHUNK must
stay large relative to cell width; as it approaches ~2 cells the cuts become a
visible regular grid. That's a tradeoff, not a free lunch.

### Where the wipe wave lives now
In step 3 a block's reveal time came from `pos = x0 / width`. On an infinite
strip that's unbounded — a block at world x = 400000 gets a start time 400000/W
sweeps in the future and never appears. So `pos` **wraps**, measured in screen
space at the generation's birth time:

    pos = frac( (world_x - scroll_offset(T_gen)) / width )

The wave becomes a repeating left-to-right ripple over the strip rather than one
sweep with an origin. Bounded everywhere, still a pure function of
(block, generation).

Scroll offset is `sign * speed * t`, with `sign` from the same per-row direction
choice — so `scroll=0` collapses this file back to step 3 (up to chunk seams),
and one direction control now governs both the motion and the wave.

### Direction default changed
`ltr` (constant) is now the default. Alternating rows makes the frame read as a
ping-pong loop — two interleaved combs shuttling against each other. Still
available as `alternate`, just not the default.

### Measured
    stateless: |cold - after-scrubbing| = 0.000e+00   OK

    render cost vs distance along the strip:
      t=     0.5   world x ~      60      14.1 ms
      t=    50.0   world x ~    6000      15.7 ms
      t=  5000.0   world x ~  600000      14.9 ms

    seam: 14 forced edges of 130 (10.8%) at chunk=744px, cell=62px
    wrap: max |t_in - birth| over chunks 0..100000 = 1.0787 (bound 1.4700)

Flat cost at world x = 600000 is the check that nothing walks from x=0. The
wrap bound is the check that distant blocks still appear at all.

## Step 5 — `cb_step5_easing.py`

Row count as a control, and cubic-bezier curves on the transitions.

### Rows — fixing a flaw in steps 1–4
Earlier steps walked down the frame adding rows until they ran out:

    y = 0
    while y < height: add a row of random height; y += h

Row count was emergent (you couldn't ask for eight) and **the last row was
clipped** at whatever fraction of its height was left over — always a different
size from its neighbours, and at low `h_rand` the only thing breaking an
otherwise even grid.

Fixed by partitioning instead of walking: pick the count, draw one weight per
row, normalise the weights to sum to the frame height.

    h_i = height * w_i / sum(w)

Exact count, exact fill, no clipped row, and `h_rand` still controls unevenness
(at 0 every weight is 1 and the bands are equal). Both sizing modes reduce to
this: **count mode** takes `n` directly, **height mode** takes
`n = round(height / cell_h)`. So row height is now a *target*, and the rows
still tile the frame exactly. Count mode is the resolution-independent one — it
survives a comp resize or a half-res preview, which pixel heights do not.

### Easing
`p` goes through a curve before it becomes geometry:

    p_eased = ease(p)    then    edge = x0 + w * p_eased

CSS-style cubic-bezier through (0,0) and (1,1), so the two control points are
the whole spec — the same `(x1,y1,x2,y2)` you'd type into CSS or AE. **IN and
OUT have independent curves.** Presets: `linear`, `ease`, `ease-in`,
`ease-out`, `ease-in-out`, `snap` = (0.10, 0.60, 0.30, 1.00), `back`
(overshoots).

The subtlety worth knowing: `x` is time, `y` is progress, and the curve is
parametric in *both*. Evaluating at a given time means first solving
`Bx(s) = x` for the parameter `s`, then reading `By(s)`. No closed form —
Newton-Raphson with a bisection fallback, exactly what browsers do. **The
fallback is not optional:** flat-derivative controls like `(0,0,0,1)` stall
Newton, and those extremes are precisely what people reach for.

`x` controls are clamped to [0,1] — outside it the curve folds back and returns
two progresses for one instant. `y` is left free so overshoot works; the drawn
edge is then clamped to the block's own extent, since a reveal can't run past
its shape.

### Measured
    bezier solver residual max|Bx(s) - x| over 1001 samples:
      linear       residual=9.67e-10  f(0)=0.000 f(1)=1.000  monotonic=True
      ease         residual=9.95e-10  f(0)=0.000 f(1)=1.000  monotonic=True
      snap         residual=9.95e-10  f(0)=0.000 f(1)=1.000  monotonic=True
      back         residual=9.89e-10  f(0)=0.000 f(1)=1.000  monotonic=False
      ease-in-out  residual=9.87e-10  f(0)=0.000 f(1)=1.000  monotonic=True
      flat-derivative case (0,0,0,1): residual=9.88e-10  OK

    row partition:
      n= 3 h_rand=0.0  got  3 rows  sum=340.000000  min=113.33 max=113.33
      n= 8 h_rand=0.6  got  8 rows  sum=340.000000  min= 24.10 max= 67.24
      n=17 h_rand=0.0  got 17 rows  sum=340.000000  min= 20.00 max= 20.00

`back` reporting `monotonic=False` is correct — it overshoots by design. Any
other curve reporting False would be a bug. The `(0,0,0,1)` line is the check
that the bisection fallback actually runs.

## Param list for the C++ port
Colour 1–5, Background, Gap %, Cell Width, Width Random,
Row Sizing (Count | Height) + Row Count / Row Height, Height Random, Seed,
Direction (ltr / rtl / alternate / random; default ltr), Scroll Speed,
Chunk Width, Period, Sweep, Hold, Block Reveal Time, Stagger, Time Random,
Ease In (x1,y1,x2,y2), Ease Out (x1,y1,x2,y2), Pixel Snap.

The two eases are 4 floats each. In AE these are best as a preset popup plus
four sliders revealed on "Custom", rather than eight bare sliders. Note
[[ae-disk-id-append-only]] when adding them: append param IDs, never insert.

`Progress` is gone: step 3 has no start and no end, it runs off the layer's own
time. If a one-shot in/out is still wanted alongside the endless mode, that is a
mode switch, not a param tweak.

## Open
- Cell size in pixels vs. a column/row count (pixels for now; a count would
  make the layout resolution-independent).
- Wipe direction — currently hardcoded left→right both ways, per the brief.


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

## Open for step 2
- Global sweep vs per-block stagger amount (currently the sweep is one hard
  line, so the reveal front is a single vertical edge).
- 5 colour slots + a separate background colour, vs. black as the 5th slot
  (which is what the reference actually does).
- Cell size in pixels vs. a column/row count.

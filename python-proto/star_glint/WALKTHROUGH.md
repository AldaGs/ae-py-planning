# Star / Glint — Walkthrough

A lesson, not a spec. Read this with `sg_step1_streak.py` open beside it.
The spec for the effect is `../../ae-effect-plugins-spec.md` §7.

---

## How to use this folder

```bash
python sg_step1_streak.py
```
Runs the correctness checks and writes the hero renders (`out_sg1_*.png`).
If it ever prints `*** FAST != SLOW ***`, stop — the fast path is broken and
nothing built on top of it can be trusted.

```bash
python sg_step1_streak.py --explain
```
Runs the same pipeline **one stage at a time**. Prints shape/min/max/mean for
each intermediate buffer and writes `explain_0_src.png` … `explain_5_composite.png`.
Open those six in order — that sequence *is* the algorithm.

---

## Reading order for step 1

Read the file top to bottom; it is deliberately ordered as the pipeline runs.

| Read | Function | The one idea |
|---|---|---|
| 1 | `srgb_to_linear` / `linear_to_srgb` | Light adds up in *linear* space, not in PNG values |
| 2 | `bright_pass` | Pick the highlights, with a soft edge so it doesn't flicker |
| 3 | `shift` | The one primitive: resample the whole frame at an offset |
| 4 | `decay_for` | Turn a user-facing "Length" into a per-step dim factor |
| 5 | `streak_arm_slow` | The formula, typed out. Obviously correct, obviously slow |
| 6 | `streak_arm` | The same answer in log(n) passes — the actual algorithm |
| 7 | `tail_only` | Drop the core, normalise so Intensity is length-independent |
| 8 | `main` | How each claim above is *proved*, not asserted |

Read 5 before 6. `streak_arm_slow` is the definition; `streak_arm` is an
optimisation of it. If 6 ever confuses you, 5 is the ground truth you can fall
back to and compare against — that is exactly what check (a) does.

---

## The three ideas, in words

### 1. Linear light

A pixel value of `0.5` in a PNG is **not** half the light — the sRGB curve
makes it about `0.21`. So `0.5 + 0.5` in file values is not "two lights'
worth". Every additive optical effect must undo the curve first, add, then
re-apply it. In `--explain`, compare stage 0 (`src_srgb`, max ≈ 2.92) with
stage 1 (`linear`, max = 12.0): same picture, very different numbers. The
`12.0` is the real brightness of the warm point.

### 2. The soft-knee bright pass

Hard threshold:

```python
w = (y >= threshold)
```

A pixel at `0.999` contributes nothing; at `1.001` it contributes fully. Animate
a light drifting past that line and it *pops*. The soft knee replaces the step
with a `smoothstep` ramp over `[t-k, t+k]`, so the same pixel fades in over
half a stop.

Note the last line: we scale the **original rgb** by the weight, rather than
subtracting the threshold from it. Subtracting would pull colours toward
neutral — the warm `(1.0, 0.6, 0.3)` point would streak whiter than it is.

`--explain` reports `0.02% of pixels survived the bright pass`. That number is
the whole reason this effect is affordable: almost everything is zero.

### 3. Geometric doubling — the real algorithm

The streak is:

```
arm(p) = sum over t of  decay^t * bright(p - t*d)      for t = 0..n
```

Literally: `n` full-frame resamples. At `L=300` that measured **1698 ms**.

The weight `decay^t` is *geometric*, and that is the loophole. Let `S_k` be the
sum over the block of offsets `[0, 2^k)`. Split that block in half:

- offsets `[0, 2^(k-1))` — that's just `S_{k-1}`
- offsets `[2^(k-1), 2^k)` — shift it back by `2^(k-1)·d` and every term is
  short by the *same* scalar `decay^(2^(k-1))`

So:

```python
tables.append(tables[k-1] + (decay ** half) * shift(tables[k-1], dx*half, dy*half))
```

Each pass **doubles** the reach. Nine passes cover 300px: **82 ms**, a 21×
speedup, and it is *exact* — check (a) measures `1e-5` disagreement, which is
just float32 rounding.

The stitch loop at the end matters too. An arbitrary length `n+1` is assembled
from the **disjoint** power-of-two blocks in its binary expansion. Disjoint is
load-bearing: sum is not idempotent, so overlapping blocks would double-count
pixels. (Extended Shadow's sweep used `max`, which *is* idempotent, so it could
overlap freely — that's the difference between the two sweeps.)

Why this shape matters for the port: every pass is "resample the frame at an
offset, multiply by a scalar, add". That is a ping-pong texture op — the same
GPU-friendly structure as the Bloom pyramid and the JFA in Buildable Stroke.

---

## What `main()` proves

Three checks, and they're worth understanding as a *pattern* — this is how
every step file in this project should end.

- **(a) Exactness, on-axis.** At 0/90/180/270°, offsets land on whole pixels, so
  bilinear sampling is a plain copy and fast-vs-slow should agree bit-for-bit.
  It does, to `1e-5`.
- **(b) Energy, off-axis.** At 33° the shifts are fractional, so a per-pixel
  comparison would flag harmless sub-pixel spreading. Instead check the
  *invariant*: a bilinear shift preserves a buffer's total sum, so one lone
  point's streak must carry exactly `sum_t decay^t` of energy at **any** angle.
  It matches to `1e-8`. This is the thing that guarantees the streak is equally
  bright in every direction.
- **(c) Speed.** Not a correctness claim — a record, so a later regression is
  visible.

The general lesson: when a fast path can't be compared pixel-for-pixel against
ground truth, find a **conserved quantity** and check that instead.

---

## Try this (predict the answer first, then run it)

1. Set `knee=0.0` in `explain()`. What happens to the grey bar's edge? Now set
   `threshold=0.15` — the bar is at `0.18` linear, so it should start streaking.
2. Change `tip` in `decay_for` from `0.02` to `0.2`. Does the streak get shorter
   or brighter? Why does the *rendered* length change even though `length`
   didn't?
3. Delete the `- bright` in `tail_only`. The highlight cores get brighter — by
   how much, and why is that wrong?
4. Remove the `wbase *= decay ** blk` line in the stitch loop. Check (a) should
   fail. Look at *how* it fails: which offsets get the wrong weight?
5. Comment out `srgb_to_linear` (work directly in sRGB values). The streaks will
   look washed out and grey rather than hot. That's idea #1, made visible.

---

---

# Step 2 — N arms, and the bug that check (a) caught

`sg_step2_star.py`. Run it the same two ways.

Step 2 looks like a loop and nothing else:

```python
for ang in arm_angles(count, base_angle):
    acc += one_arm(bright, ang, ...)
```

It is not. The loop forces two decisions, and the *second* one exposed a real
defect in step 1 that would have shipped otherwise.

## Decision 1 — normalise by count

Eight arms each contribute their own energy, so an 8-point star is 8× brighter
than a 1-point streak at the same Intensity. Turning up "Points" would then blow
out the exposure and you'd chase it back down with Intensity — two controls
fighting. Dividing by `count` decouples them. Check (c) measures it: total energy
is flat at `23.52` across counts 1/2/4/8, while the un-normalised version grows
exactly 8×.

## Decision 2 — symmetry, and how it went wrong

"N arms at 360/N" is testable, not a matter of taste: rotate the render by one
arm's spacing and it must land on itself. That's check (a). First run:

```
count=2  0.00e+00      count=3  4.75e-01  FAIL
count=4  0.00e+00      count=6  3.92e-01  FAIL
                       count=8  3.64e-01  FAIL
```

Symmetric only when *every* arm happens to be on-axis — counts 2 and 4. The
render confirmed it: diagonal arms were visibly softer than the horizontal and
vertical ones.

**Cause.** Step 1's fast doubling chains `log n` bilinear resamples, and each
spreads the streak sideways by a sub-pixel. On-axis, offsets are whole pixels,
bilinear is a plain copy, and *nothing* blurs. Off-axis, the blur compounds.
Step 1's checks couldn't see this: check (a) only tested on-axis, and check (b)
tested *energy*, which is preserved. The shape isn't.

This is the useful lesson of step 2. Step 1's two checks were both correct and
both passed, and the code still had a visible defect — because neither check
measured the quantity that turned out to matter. A test suite tells you about
the properties you thought to measure, and nothing about the ones you didn't.

## The fix that didn't work — and why it's still in the file

The obvious repair: never sweep off-axis. Rotate the buffer so the arm lands on
+x, sweep exactly, rotate back. Every arm then takes the same two resamples.
`streak_arm_rotated` does this, and it *barely helped* (0.36 → 0.25).

Measuring instead of guessing found why: **rotating a single bright point by 45°
turns 24.0 units of energy into 31.6, a 32% gain.** `warpAffine` inverse-maps —
for each destination pixel it bilinearly samples the source — which preserves
*values* but not *sums*. Step 1's `shift` was a pure translation, where the
bilinear weights over the destination lattice sum to exactly 1; under rotation
they don't. So the rotated path trades a blur error for a brightness error:
diagonal arms come out too bright instead of too soft.

Rejected, but kept in the file with the measurement, because "translation is
energy-preserving, rotation is not" is worth knowing before you reach for a
rotate in the C++ port.

## The fix that did work

Measuring the direct sweep properly (check (a2)) reframed the problem:

| angle | lateral energy | width |
|---|---|---|
| 0° | 0.253 | **1.00 px** |
| 10° | 0.254 | 2.80 px |
| 30° | 0.268 | 1.37 px |
| 45° | 0.257 | **2.84 px** |
| 90° | 0.253 | 1.00 px |

Energy is flat to ~5% at every angle. Brightness was never the problem. The arm
is just more *concentrated* on-axis — a 1px hairline versus a 2.8px smear.

So don't fight the blur, **finish** it: blur every arm laterally to a common
width. A normalised Gaussian is exactly energy-preserving, so this introduces no
brightness error at all — unlike rotation. Widths add in quadrature, so a couple
of deliberate pixels swamp the intrinsic variation. Measured spread: **2.84× →
1.25×**.

And the 1px hairline was never right in the first place. Real diffraction spikes
have finite width, and a hairline aliases and crawls the instant anything moves.
The bug became the **Width** control.

One detail worth reading in `star()`: the core is subtracted *before* the blur.
`lateral_blur` is linear so the result is identical either way, but subtracting
first keeps the subtraction exact — the `t=0` term of the direct sweep is
`bright` itself, whereas the blurred sweep's core is a blurred `bright`, and
`arm - bright` would leave a residual ring.

## What's left, stated honestly

The width fix takes the worst case from 47% to ~16%, not to zero. Check (a)'s
tolerance is set at 0.2, which is a judgement call, so here is the basis for it:

- arm widths agree to 1.25× (check a2)
- along-arm falloff agrees to within 4–7% at every distance
- the remaining error is per-pixel sub-pixel ripple, which a max-abs metric
  reports harshly
- it is not visible in the render — compare `cmp_star8_w0.png` (before) with
  `cmp_star8_w3.png` (after)

If a later change pushes that number past 0.2, the check fails and the printed
table shows which count broke.

Also honest: **step 2 is slow.** 8 arms at L=300 is ~1530 ms, up from ~640 ms
before the blur, because `lateral_blur` is a non-separable `filter2D` with a
~11×11 kernel. It is correct, not fast. Step 4 is the performance step — the
blur is separable in arm-space, and streaks are low-frequency enough to run at
half resolution like Bloom's streak stage.

## Try this

1. Set `width=0` in `star()` and re-render `count=8`. The diagonal arms go soft
   and the axis arms go razor-thin. That's the bug, on screen.
2. Set `normalise=False` and render counts 2, 4, 8 at the same Intensity. Watch
   the exposure run away — this is the control-feel argument made visible.
3. `count=2, base_angle=0` should be indistinguishable from a single straight
   line through each highlight (the anamorphic look). Check (b) asserts exactly
   that by comparing energy left and right of centre.
4. Try `count=3, base_angle=90`. Odd counts are why arms span 360° rather than
   axes spanning 180°.
5. Run `--explain` and flip through `explain2_arm0..5.png`. Each image adds one
   spike. Predict before looking: does the *max* value stay constant as arms
   accumulate? (It doesn't — and check (c) explains what the normalisation
   actually holds constant instead.)

---

# Step 2b — per-arm lengths (the style knob)

`sg_step2b_armlengths.py`. Step 2 gave every arm the same length, which is the
one look nobody wants. Two new modes:

- **`alternate`** — even arms full length, odd arms `× ratio`. The classic
  sparkle: 8 points, 4 long and 4 short. See `out_sg2b_alt8_sparkle.png`.
- **`random`** — each arm its own seeded length. Organic, dirty-optics.
  See `out_sg2b_rand8_wild.png`, and compare `rand8_s1` vs `rand8_s2` — same
  settings, different seed, different signature.

The loop barely changes. Three things around it do, and those are the step.

## 1. Length and brightness are coupled

Step 1 tied these together and step 2 hid it by computing both once, outside the
loop:

```python
decay = decay_for(length)        # per-step dim factor, DERIVED from length
norm  = (1 - decay) / decay      # makes the tail's total energy ~= 1
```

The moment arms differ, **both move inside the loop**. Miss the second and arm
brightness drifts with arm length — so Variation would secretly also be a
brightness control, and every style change would need an Intensity re-balance.
Same "two controls fighting" failure as step 2's `/count`, in a new place.

Check (c) measures it, and — importantly — also runs a deliberately *broken*
version alongside, so the check can't quietly pass for the wrong reason:

```
fixed:  spread across all styles = 1.09e-03
buggy:  spread                   = 1.22e-01   (min 21.06, max 23.98)
```

A check that only ever sees correct code doesn't tell you it works. Reproducing
the bug next to it does. Worth stealing as a habit.

## 2. Length must stay an upper bound — a port decision, made early

The obvious randomisation is a multiplier around 1.0, say `[0.5, 1.5]`. Don't.

This effect will be SmartFX, and a SmartFX plugin must tell AE *up front* how
far outside the layer it will draw so the buffer can be expanded. If the
multiplier can exceed 1, the true reach is `length × max_multiplier` — a number
that depends on the seed. That's how you get arms clipped only on certain seeds.

So the multiplier lives in `[1 - variation, 1]`. `Length` is then exactly the
longest any arm can be, the buffer request is just `Length`, and no seed can
clip. Cost: high Variation makes the star smaller on average — predictable, and
much better than seed-dependent clipping.

This is a prototype decision that is really a port decision. Making it now, in
Python, is far cheaper than discovering it in C++.

## 3. Determinism

"Random" in an AE effect means *random-looking but identical every render*. Draw
fresh lengths per frame and the star boils; worse, two renders of the same frame
differ. Seeded generator, Seed as a parameter. Check (b) asserts same-seed
identity *and* different-seed difference — the second half matters, or a seed
control that did nothing would pass.

## One thing to know about the look

Every highlight in the frame gets the **same** arm lengths. That's correct, not a
shortcut: the star shape is a property of the lens, not the light, so every
source through the same optics flares identically — look at any real anamorphic
still. Per-highlight variation isn't a tweak to this code; it would require
identifying individual lights first, which is segmentation and a completely
different, far more expensive effect.

## Try this

1. `--explain` prints the per-arm table — angle, length, decay, norm. Watch
   `decay` and `norm` move together as length changes. That pairing *is* item 1.
2. In `star()`, hoist `decay` and `norm` back outside the loop (step 2's
   version). Check (c) should fail; the printed energies show which styles drift.
3. `alternate` with `ratio=1.0` and `random` with `variation=0` must both give
   back step 2's star *exactly* — check (a). Any style control you can't turn
   off isn't a style control.
4. Change `rng.uniform(1 - variation, 1, count)` to `rng.uniform(1 - variation,
   1 + variation, count)` and render `variation=0.9` — then work out what buffer
   expansion the C++ port would have to request. That's item 2, made concrete.
5. Compare `out_sg2b_rand8_s1.png` and `out_sg2b_rand8_s2.png`. Same controls,
   different seed. That's the "two instances don't look stamped" argument.

---

# Step 3 — colour

`sg_step3_color.py`. Two features; only one is hard.

**Per-arm tint** is one multiply. Give arm `i` a colour, scale the arm by it.
That's the "every spike a different hue" look — `out_sg3_tint_wheel.png`.

**A ramp along the arm** is the real work, and it collides head-on with step 1.

## The conflict

Starglow's signature is that a streak *changes colour as it travels* — hot white
at the core, cooling to a tint at the tip. That's a 1D ramp indexed by distance:

```
arm(p) = sum_{t=0..n} decay^t * c(t) * bright(p - t*d)
```

The problem is in the formula. The entire reason the sweep is fast is that it
collapses every `t` into one buffer. **Once collapsed, the distance is gone** —
there's no `t` left to index `c()` with. The speed and the feature are in direct
conflict.

## Slicing the sum into distance bands

Go back to what the doubling actually produces. `tables[k]` is the sum over the
disjoint offset block `[0, 2^k)`, and stitching them gives
`prefix(m) = sum_{t<m} decay^t · bright(p - t·d)`. Any contiguous band of
distances is then just a stitch that *starts* at `a` instead of 0:

```python
base, wbase = a, decay ** a
for k in descending:
    if base + blk <= b:
        out += wbase * shift(tables[k], base*dx, base*dy)
```

Build the tables **once** (the expensive part), then cut `B` bands out of them,
tint each with the ramp colour at its own distance, and sum. Cost: one table
build plus `B` stitches.

**Do not** write it the obvious way as `decay^a * shift(prefix(b-a), a*d)`.
That's algebraically identical and numerically worse: it resamples twice, and
`shift(shift(X,u),v) != shift(X,u+v)` — each bilinear resample adds blur. Fold
`a` into the stitch so every block takes exactly one shift.

## The check that was measuring the wrong thing

First run, checks (a) and (b) failed off-axis by ~7% — with a *flat white* ramp,
where the bands should have summed back to exactly the plain arm. The obvious
reading was "the band decomposition is broken". It wasn't. Measured against the
literal march (the real ground truth):

| angle | step 1 fast path vs march | band sum vs march |
|---|---|---|
| 0° | 1.1e-06 | 1.0e-06 |
| 33° | **8.2e-02** | 6.6e-02 |
| 45° | **9.8e-02** | 8.4e-02 |

**Step 1's fast doubling is itself ~8–10% off the exact sum, pointwise, at
off-axis angles.** The band sum is slightly *closer*. Check (a) had been
comparing a new approximation against an old approximation and blaming the new
one.

This is worth sitting with, because it's the third time in this effect that a
check reported a real number and the *reference* was the thing at fault. Step
1's checks never caught this: check (a) only tested on-axis, and check (b)
tested energy, which is conserved. Step 2 found the visible symptom (arm width)
and fixed it. This is the same underlying resampling error, measured a third
way.

So the checks were rewritten around the march:

- **(a)** on-axis, a flat ramp reproduces the exact sum to `1e-6` — clean algebra
  check on `band()`'s offsets and weights, with no resampling in the way.
- **(b)** off-axis, bands must be *no worse* than step 1's existing sweep. They
  aren't.
- **(b2)** changing the band count must not shift a flat ramp at all.

## How many bands?

Piecewise-constant colour is an approximation, so check (c) measures it against
a per-sample coloured march instead of eyeballing the render — and compares
geometric against linear band spacing:

```
bands    geometric      linear
   4     1.75e-01     3.65e-01
   8     6.97e-02     1.76e-01
  16     2.66e-02     8.24e-02
  32     1.55e-02     3.53e-02
  64     6.73e-03     2.35e-02
```

Geometric spacing is ~3× better at every count, because the streak is
decay-weighted: nearly all the energy and all the visible colour change sit near
the core, so linear bands waste their resolution out in the faint tip. **16
geometric bands lands within 2.7%**, which is the default.

## The free alternative, and why it isn't the main path

`streak_arm_dispersive` gives R, G and B their own decay rates. Colour then
shifts smoothly along the arm at *zero* extra cost, no bands, no approximation —
and it's what real dispersion does. The catch is in check (d): it can only ever
produce one monotonic core→tip shift. You cannot express "white, then gold, then
magenta". It ships as a cheap mode, not as the general control.

## Reuse

The ramp is Gradient Map's `build_lut`, imported unchanged — stops in, 256-entry
table out. That effect is finished and validated; writing a second one would be
strictly worse. This is the syllabus' cross-effect technique map paying out.

## Try this

1. `--explain` prints the per-band table — distance range, midpoint, `decay^a`,
   and the colour. Watch the bands get wider as they go out; that's the
   geometric spacing.
2. Set `bands=4` and render `ramp="rainbow"`. The steps become visible. Now
   `bands=16`. Check (c) told you the crossover before you looked.
3. Swap `np.geomspace` for `np.linspace` in `band_edges` and re-run check (c).
   You should recover roughly the "linear" column.
4. Write `band()` the obvious way — `decay**a * shift(prefix(...), a*d)` — and
   re-run check (b). Predict first: on-axis or off-axis, which one degrades?
5. Set `color_mode="dispersive"` with `ramp="rainbow"`. The ramp is ignored;
   dispersion can't express it. That's the limitation in check (d), on screen.

---

# Step 4 — performance, and the things AE will demand

`sg_step4_controls.py`. Two separate jobs: make it fast, and make it survive
contact with After Effects.

## Profile first. The guess was wrong.

Step 2 left a note saying the performance problem was `lateral_blur`, because it
was a non-separable `filter2D` and that *sounded* expensive. Measured at 1080p,
L=300:

| stage | cost |
|---|---|
| `bright_pass` | 37 ms |
| `build_tables` (1 arm) | 184 ms |
| one general band stitch | **76 ms** ← ×16 bands ×6 arms |
| `lateral_blur`, width 3 | 23 ms ← *the thing we were going to optimise* |
| coloured arm, 16 bands | 989 ms |

The blur is **2%** of the cost. The bands are ~75%. Optimising the blur would
have been a day spent making the effect 2% faster. The note in step 2's
walkthrough was a plausible guess written without a profiler, and it was wrong —
which is the reason this section exists.

## The band fix: make every band a power of two long

`tables[k]` is already the geometric sum over exactly `2^k` consecutive offsets.
So if a band's *length* is a power of two, no stitching is needed at all:

```
band(a, a + 2^k) = decay^a * shift(tables[k], a*d)     ONE shift
```

**76 ms → 16 ms per band.** `pow2_band_edges` partitions the tail into segments
of power-of-two length, kept roughly geometric. The trailing remainder segment
usually isn't a power of two and falls back to the general stitch — correct,
just not free (15 of 16 take the fast path).

One detail that had to be measured: snapping each length *down* to a power of
two makes every step short, so the partition overshoots (16 bands → 20 segments)
and the speed win gets spent on extra bands. Snapping to the *nearest* keeps the
count where you asked for it.

## Colour at the centroid, not the midpoint

Inside a band the samples are weighted `decay^t`, so the band's light sits near
its *close* end, not its middle. Colouring by the energy-weighted centroid
instead of the midpoint is free (a scalar per band) and measurably better:

| bands | midpoint | centroid |
|---|---|---|
| 8 | 0.0739 | **0.0677** |
| 12 | 0.0458 | **0.0383** |
| 16 | 0.0369 | **0.0309** |
| 32 | 0.0177 | **0.0148** |

## Honest accounting on the speed

End to end, full-res is **not** faster than step 3 — about 6 s either way. The
pow2 bands bought ~1.8× on the arm, and alpha spent it again by making every
buffer four channels instead of three.

What actually pays on CPU is downsampling — check (c) measures both sides of it:

| downsample | time | peak error vs full res |
|---|---|---|
| 1 | 6900 ms | — |
| 2 | 1630 ms | 0.22 |
| 4 | 490 ms | 0.45 |

22% peak error at half res is not nothing; streaks are thin, high-contrast
structures and that's the real limit of the trick. It's a quality control, not a
free lunch, which is why it's exposed rather than always-on.

The pow2 change still matters more than 1.8× suggests, but for a reason the CPU
timing hides: it changes the band cost from `O(B log n)` shifts to `O(B)`. Here
full-frame memory traffic dominates the resample (8 ms to shift, 37 ms to shift
+ scale + tint + accumulate as separate expressions — fused in-place now). On
the GPU, where every pass carries fixed setup cost, dropping ~9 passes per band
is the difference that counts. **The real fix is step 5.**

## Resolution independence — the AE bug you find last

AE renders previews at Half/Third/Quarter and hands the effect a smaller buffer
plus a downsample factor. Treat Length as "300 pixels" and the streak is 300
*buffer* pixels at every resolution — four times longer relative to frame at
Quarter than at Full. **The preview then lies about the final render.**

Every length-like control is multiplied by `res_scale` before use. Check (d)
renders full-res, renders a half-size buffer the way AE would, and compares —
then does it again with the scaling ignored so the check isn't vacuous:

```
with res_scale     : rel err = 2.23e-01
res_scale ignored  : rel err = 6.62e-01   <- the bug
```

## Alpha

A streak is light, and light landing on empty pixels has to bring its own
coverage or it won't composite. Alpha rides as a fourth channel and gets swept
identically. Two properties asserted rather than hoped for:

- transparent black in → *exactly* zero out (no glow from nothing)
- zero "lit but transparent" pixels — the streak never brightens a pixel it
  doesn't also make more opaque

## Try this

1. `--explain` dumps the whole parameter block grouped as the AE UI will group
   it, plus the band partition and how many segments take the fast path.
2. Set `downsample=4` and look at `out_sg4_preset_half_res.png` versus full res.
   Check (c) gave you the number; this is what 0.45 looks like.
3. Comment out the `* p["res_scale"]` on `length_px` and re-run check (d). The
   error jumps to 0.66 — that's what a lying preview measures as.
4. In `band_color_index`, return the midpoint index instead of the centroid and
   re-run check (a). Predict the direction of the change first.
5. Profile something yourself before optimising it. That's the actual lesson of
   this step.

---

## Where step 4 stops, and what comes next
- **step 5 → C++.** SmartFX with output buffer expansion, since arms leave the
  layer bounds.

Reused wholesale from work you've already shipped: Bloom's linear-light spine
and bright pass, Extended Shadow's directional sweep structure, Gradient Map's
ramp LUT. The only genuinely new idea in the whole effect is the geometric
doubling above — and step 1 already proves it.

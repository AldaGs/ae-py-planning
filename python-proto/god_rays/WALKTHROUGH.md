# God Rays / Volumetric Light — Walkthrough

A lesson, not a spec. Read this with `gr_step1_rays.py` open beside it.
The spec for the effect is `../../ae-effect-plugins-spec.md` §6.

This effect is the third one built on the same spine (Bloom → Star Glint → God
Rays), so the walkthrough is mostly about **what is different**. If a section
here says "same as Star Glint", go read that one — `../star_glint/WALKTHROUGH.md`.

---

## How to use this folder

```bash
python gr_step1_rays.py
```
Runs the correctness checks and writes the hero renders (`out_gr1_*.png`).
If it ever prints `*** FAST != SLOW ***`, stop — the fast path is broken and
nothing built on top of it can be trusted.

```bash
python gr_step1_rays.py --explain
```
Runs the same pipeline **one stage at a time**. Prints shape/min/max/mean for
each intermediate buffer and writes `explain_0_src.png` … `explain_5_composite.png`.
Open those six in order — that sequence *is* the algorithm.

---

## Reading order for step 1

| Read | Function | The one idea |
|---|---|---|
| 1 | `linear_to_srgb` | Same linear-light spine as Bloom and Star Glint |
| 2 | `bright_pass` | Lifted from Star Glint unchanged — see below for the twist |
| 3 | `zoom` | The one primitive: resample the whole frame **scaled about a point** |
| 4 | `ratio_for` / `decay_for` | Turn user-facing Reach and Falloff into per-step scalars |
| 5 | `ray_sweep_slow` | The formula, typed out. Obviously correct, obviously slow |
| 6 | `ray_sweep` | The same answer in log(n) passes — the actual algorithm |
| 7 | `tail_only` | Drop the core, normalise so Intensity is setting-independent |
| 8 | `synthetic_sun` | Why this subject, and the two subjects that failed first |
| 9 | `main` | How each claim above is *proved*, not asserted |

Read 5 before 6, same as Star Glint. `ray_sweep_slow` is the definition;
`ray_sweep` is an optimisation of it, and check (a) is the comparison.

---

## The one real idea: a radial march is a repeated **scale**

Star Glint's sweep marched along a fixed direction — the same vector for every
pixel:

```
arm(p) = sum_t decay^t * bright(p - t*d)                  <- TRANSLATION
```

God rays march every pixel *toward the light*. The direction is different for
every pixel, and so is the distance — a pixel at the far corner has much further
to travel than one next to the sun. Written the way GPU Gems 3 writes it (a
linear step of `(uv - light)/N`), that looks like a per-pixel operation with no
structure to exploit, and the standard implementation is exactly that: N texture
reads per pixel, in a loop, in the shader.

Change the step from linear to **geometric** and the structure appears:

```
ray(p) = sum_t decay^t * bright(C + (p - C) * r^t)        <- SCALE about C
```

Every step multiplies the offset-from-centre by one global scalar `r < 1`. There
is no per-pixel direction any more — there is one centre and one ratio. And
scales about a common centre **compose** exactly the way translations do:

```
zoom(zoom(X, a), b) = zoom(X, a*b)          just as   shift + shift = shift
```

Composition under addition of the exponent is the *only* property Star Glint's
geometric doubling ever needed. So the whole doubling carries over verbatim —
only the primitive changes:

```python
# Star Glint
tables.append(tables[k-1] + (decay ** half) * shift(tables[k-1], dx*half, dy*half))
# God Rays
tables.append(tables[k-1] + (decay ** half) * zoom(tables[k-1], cx, cy, r ** half))
```

`ray_sweep` is `streak_arm` with one call swapped. 128 samples in **8 passes**,
255 samples in 9 — measured at **19×** faster than the literal march, and exact.

Is the geometric step a *cheat*? It changes the sample distribution: samples
bunch up near the light instead of spacing evenly. That is arguably better, not
worse — the shaft's contrast is concentrated near the source, which is exactly
where you want the sample density. But it is a real difference from the GPU Gems
formulation and worth knowing you made it.

**For the port:** every pass is "resample the frame with one affine map,
multiply by a scalar, add". Same ping-pong texture op as Bloom's pyramid, Star
Glint's arms, and Buildable Stroke's JFA. Four effects, one GPU shape.

---

## Where the Star Glint analogy breaks: energy

Star Glint got a free invariant out of `shift`. A pure translation is
energy-preserving — the bilinear weights over the destination lattice sum to
exactly 1 — so a streak's total had to equal `sum_t decay^t` at any angle, and
check (b) could assert that to `1e-8`.

`zoom` has no such property. Magnifying by `1/r` spreads the same light over
`1/r^2` as many pixels, so the sum **grows**, by `1/r^2` per step. This is
physically right (a shaft really does get wider as it travels) but it means the
free invariant is gone.

So check (c) measures the growth instead of assuming it away:

| r | measured sum ratio | 1/r² | rel |
|---|---|---|---|
| 0.99 | 1.0229 | 1.0203 | 2.5e-03 |
| 0.95 | 1.1169 | 1.1080 | 8.0e-03 |
| 0.90 | 1.2276 | 1.2346 | 5.7e-03 |

That check isn't there to bless the maths — it's there so that if the sampler
ever starts losing light off the frame edge, or the centre drifts, the number
moves. An invariant you can't assert exactly is still worth measuring.

It also explains a thing you will see in the renders: **long reach blows out**.
The `long` preset ships at Intensity 0.5 while `short` and `classic` ship at
0.6, and that is not a taste call — it is `1/r^2` compounding over more steps.
Whether that should be normalised away is a step-4 question.

---

## Checking a fast path when nothing is exact

Star Glint's check (a) worked because it could pick angles — at 0/90/180/270°
the offsets are whole pixels, bilinear is a plain copy, and fast-vs-slow agrees
bit-for-bit. **A scale is never a plain copy.** There is no r that makes `zoom` a
memcpy except `r = 1`, which does nothing. That door is shut.

The way back in is `_affine_probe`. Bilinear interpolation reproduces **affine
functions exactly** — and a scale of an affine function is still affine. So on a
linear ramp image the sampler contributes *no error at all*, and any fast-vs-slow
disagreement must be a bug in the doubling algebra itself:

```
samples= 32   rel max|fast-slow| = 3.42e-05
samples=100   rel max|fast-slow| = 6.60e-05
samples=128   rel max|fast-slow| = 4.15e-05
samples=255   rel max|fast-slow| = 3.85e-05
```

Float32 noise, nothing more. And because `r < 1`, every sample lands *inside* the
frame, so the zero border never participates and the probe stays affine
everywhere it is read — that constraint is load-bearing, not a coincidence.

Then check (b) runs the *same* comparison on real (blurred-noise) content, where
the sampler does contribute. That number is not a pass/fail on the algebra, it's
the **price of the fast path**, recorded so a regression is visible:

```
samples= 32   rel peak = 1.6e-03   rel mean = 1.7e-04
samples=128   rel peak = 2.0e-03   rel mean = 3.1e-04
samples=255   rel peak = 2.2e-03   rel mean = 3.5e-04
```

Worth pausing on: Star Glint's equivalent number was **8–10%** (its step 3
walkthrough, the table comparing the fast path against the literal march
off-axis). Here it is **0.2%**, forty times better.

*Why* is unverified — the plausible story is that a translation's sub-pixel error
accumulates in one consistent direction across all 9 passes, while a scale's is
spread radially and partly cancels. That is a guess, and this project has already
been burned once by a plausible guess written without a measurement (Star Glint
step 2 predicted `lateral_blur` was the bottleneck; step 4's profiler found it
was 2% of the cost). So treat it as: **the number is real, the explanation is
not yet.** What follows from the number alone is that the blur-compensation
machinery Star Glint's step 2 needed is probably not needed here — which is a
thing to *check* in step 2, not to assume now.

The general pattern, third time now in this project: when a fast path can't be
compared pixel-for-pixel against ground truth, either find a case where the
sampler is exact, or find a conserved quantity — and if neither exists, measure
and record rather than assert.

---

## The subject is part of the experiment

This took two failed attempts and both are written into `synthetic_sun`'s
docstring rather than deleted, because the failures are the lesson.

**Attempt 1 — reuse Star Glint's subject.** Isolated HDR points on black. Result:
almost nothing. A lone bright point smeared radially is a small halo, not a god
ray. The effect had no subject to work on.

**Attempt 2 — small sun, occluders, dark sky.** Better, but still nearly
invisible, and *this is the interesting failure*: the bright pass keeps only the
sun, so the occluders never enter the sweep at all. They cut the *source*, not
the shafts. Cranking Reach just magnified the sun into a flood.

**What it actually needs.** A god ray is legible only when something interrupts a
**bright field**:

- the sky is *above* threshold, so it survives the bright pass
- the slats punch holes in it, and those holes become the shafts
- the sun is the hot core that sets where the shafts converge
- the ground is *below* threshold and must stay clean

And that reframes the effect. Star Glint's `--explain` reports **0.02% of pixels
survived the bright pass**, and its whole affordability argument rests on that
sparsity. God Rays reports **31.25%**. This effect is not sparse and never will
be — every optimisation that assumes "almost everything is zero" is off the
table. That is a real difference in the two effects' cost models, and it was
discovered by building a test image, not by reasoning about it.

Note also that the slats are *irregular widths*, deliberately. A regular comb
makes it very hard to tell a correct shaft from an aliasing artefact.

---

## The parameters, and the two that are secretly one control

The GPU Gems formulation exposes **density / decay / weight / exposure**. Taken
literally that is a bad UI, for two separate reasons.

**1. Three of the four are sample-count dependent.** Raw `decay` is per-step, so
raising the sample count for quality silently makes the shaft shorter and dimmer.
The user then re-tunes decay, and now their quality knob is a look knob. Fixed by
deriving the per-step scalars from what the user actually means:

```python
r     = (1 - reach)  ** (1 / samples)      # reach: fraction of the way to C
decay = falloff ** (1 / samples)           # falloff: dimming by the LAST step
```

`--explain` prints the round-trip so you can see it hold: `r^128 = 0.5500` for
`reach=0.45`, `decay^128 = 0.0600` for `falloff=0.06`. Change `samples` and the
look does not move — which is the entire point.

**2. `weight` and `exposure` multiply.** They are one degree of freedom wearing
two hats. Ship one knob, `intensity`, and `tail_only` normalises by the analytic
sum `decay·(1-decay^n)/(1-decay)` so it means the same thing at any Reach or
Falloff. Same normalisation reasoning as Star Glint's `/count`: two controls that
fight each other is a UI bug, not a feature.

---

## Try this (predict the answer first, then run it)

1. In `explain()`, change `samples` from 128 to 32 and then to 256. The shaft
   should barely change — only its smoothness. Now hack `ratio_for` to return a
   constant `r` and repeat. That's parameter reformulation #1, made visible.
2. Set `reach=0.95` at `intensity=0.6`. It blows out. Work out from the `1/r^2`
   table how much of that is the magnification and how much is the tail
   normalisation not accounting for it.
3. In `synthetic_sun`, drop the sky back to `0.05` (attempt 2's subject) and
   re-render. Watch the effect nearly vanish, and check `--explain`'s
   "% survived the bright pass" to see why.
4. Replace `_affine_probe` with random noise in check (a). The error jumps from
   `4e-05` to ~`2e-03` and the check fails — not because the code broke, but
   because you removed the thing that made the sampler exact. Understand that
   before trusting any tolerance in this project.
5. Look at `out_gr1_offscreen.png` and `out_gr1_corner.png`. The centre is
   outside the frame in the first. Nothing in `zoom` special-cases that — work
   out why it doesn't need to.
6. Swap `cv2.INTER_LINEAR` for `cv2.INTER_NEAREST` in `zoom` and re-run check
   (a). Predict which check fails hardest, and why the affine probe no longer
   saves you.

---

# Step 2 — Shimmer, and a theorem that makes it free

`gr_step2_shimmer.py`. Run it the same two ways.

Step 1's shafts are perfectly clean. Real light shafts are not — dust and haze
break them into a rake of brighter and darker rays, and this is the single
control that most separates "a radial blur" from "god rays". Compare
`out_gr2_clean.png` with `out_gr2_dusty.png`.

The obvious implementation is one line: multiply the bright buffer by some noise
before sweeping. That works. Measuring it turned up something better.

## The observation

A scale about C preserves **angle** about C — exactly, not approximately:

```
p' = C + (p - C) * r      =>      atan2(p' - C) == atan2(p - C)
```

Scaling slides a point along the ray it is already on; it cannot move it to a
different ray. This is the same fact that made step 1 fast, seen from the other
side: **the sweep never mixes rays.** Check (a) confirms it to `2.4e-07` rad,
which is float32 and nothing else.

## The consequence

The sweep at pixel `p` reads only the points `C + (p-C)·r^t` — every one of which
has the *same* angle `θ(p)`. So for any `m` that is a function of angle alone:

```
sweep(bright · m)(p) = sum_t decay^t · m(θ) · bright(...)
                     = m(θ) · sum_t decay^t · bright(...)
                     = m(θ) · sweep(bright)(p)
```

**Angular modulation commutes with the sweep.** Apply the shimmer *after* the
sweep — one multiply over the frame — instead of before it. Same picture.

Three things follow:

1. **The noise is never resampled**, so it stays sharp. The pre-sweep version
   drags the noise through 8 bilinear passes.
2. **Phase becomes cheap to animate.** Changing Shimmer Phase does not
   invalidate the swept buffer. Check (f) measures it at 1920×1080: full sweep
   **239 ms**, phase-only re-apply **106 ms** — 2.2×. In AE that is the
   difference between a cached render and a full recompute per frame.

   *Those numbers are the second set.* The first version of check (f) timed one
   cold call each and reported **2977 ms / 438 ms / 6.8×**. None of it
   reproduced — with the grid cache cold and the 1080p buffers being touched
   for the first time, it was measuring allocation and page faults, not the
   algorithm. The check now warms up and takes the best of three. A single
   timing run is not a measurement, and the 6.8× went on to cause real damage
   in step 4 before it was caught — see there.
3. **It is a check.** Two independent code paths that must agree, and the gap
   between them is a direct measurement of how much the bilinear sampler leaks
   light between neighbouring rays.

That third point is the reason the "rejected" implementation stays in the file.
`rays_pre` is not dead code — it is the reference that makes `rays_post`
trustworthy, the same role `streak_arm_slow` plays in step 1.

## The prediction that was half wrong

Check (b) came with a prediction written before it ran: *error grows with
Detail, because finer rays sit closer together and leak into each other more.*

```
detail= 4.0   rel peak = 9.9e-02   rel mean = 8.6e-03
detail=14.0   rel peak = 9.7e-03   rel mean = 1.2e-02
detail=40.0   rel peak = 8.2e-03   rel mean = 2.9e-02
```

The **mean** does exactly that, 0.9% → 2.9%. The **peak** does the opposite,
9.9% → 0.8%. Both numbers are correct and they measure different things: at low
Detail the field is a few broad lobes with steep walls, and the worst single
pixel sits on a wall, not on fine-ray crosstalk. Two mechanisms; only one of them
was predicted.

Both are printed rather than picking the column that agreed. This is the fourth
time in this project that a check has come back with a number the docstring did
not expect, and it is getting to be the point of writing them.

## The two constraints on the noise

Neither is a taste call; both are forced.

### 1. Periodic in θ with period 2π

Sample any ordinary 1D noise over `[-π, π]` and there is a discontinuity along
the `-π/+π` ray — one permanently wrong shaft, in a fixed screen direction, that
no amount of seed-shopping removes. A Fourier series with **integer**
frequencies is exactly periodic by construction, which is why `shimmer_field` is
built from cosines rather than from value noise or Perlin.

Check (c) evaluates the field either side of the seam, and — because a check
that only ever sees correct code proves nothing (Star Glint step 2b's lesson) —
runs a deliberately broken non-integer frequency beside it:

```
detail= 7.0   gap = 3.1e-06
detail=14.0   gap = 7.3e-06
detail=33.0   gap = 1.3e-05
[note] non-integer k=14.5:  gap = 1.03e+00   <- the seam the integers avoid
```

Octaves are integer multiples for the same reason, and give the field a fractal
feel — a few broad rays with finer structure inside them, which is what dust in
air actually looks like.

### 2. Faded out near C

Angular frequency measured in *pixels* is `k / radius`. At radius 3 a 32-cycle
shimmer is asking for ten cycles per pixel: it aliases into a hard, crawling
starburst sitting right on the source point. This is the single worst artefact in
the effect and it only appears when the centre is inside the frame — so it is
exactly the kind of thing that ships.

The fix is computable, not eyeballed. One pixel of arc at radius `R` subtends
`1/R` radians, so local frequency is `k_max / (2πR)` cycles/pixel; Nyquist at
0.5 gives `R = k_max / π`. `radial_fade` ramps the shimmer to 1.0 inside that
radius. Check (d):

```
unfaded  peak-to-peak inside r<50.9px = 1.1879
faded    peak-to-peak inside r<50.9px = 0.0000
```

## What check (e) is for

`amount=0` must give back step 1 **exactly** — asserted as `== 0.0`, not as a
tolerance. Any style control you cannot switch off is not a style control; same
rule as Star Glint step 2b. And seeds must be deterministic *and* actually do
something: same seed identical, different seed different. The second half
matters, or a Seed control that did nothing would pass.

## Try this

1. `--explain` writes `explain2_0_field.png` (raw) and `explain2_1_mask.png`
   (faded). The difference between them is the whole of constraint 2.
2. Set `octaves=1` in `shimmer_mask`. The rays become a clean sinusoidal comb —
   obviously artificial. Then note that `shimmer_field` divides by `sum(amps)`,
   and work out what would drift if it did not.
3. In `harmonics`, drop the `int(round(...))`. Render at `detail=14.3` and find
   the seam. It is always in the same screen direction, which is the tell.
4. Delete `radial_fade` from `shimmer_mask` and render `detail=34`. Look at the
   sun. Then animate Phase and imagine that crawling.
5. Swap `rays_post` for `rays_pre` in the render loop. The images are nearly
   identical — that is the theorem. Now diff them and find where they are not:
   the disagreement is concentrated where rays are closest together.
6. Predict, then check: does Shimmer change the *total* light in the frame? Look
   at what `tail_only` is normalising by in the render loop and why the mask is
   passed into it.

---

# Step 3 — Colour, and which of the two ramps is actually free

`gr_step3_color.py`. Run it the same two ways.

There are two completely different things "a ray colour ramp" can mean. They look
similar in a still and they cost wildly different amounts:

- **RADIAL** — colour indexed by the pixel's own distance from the source.
  *"the shaft is gold near the sun and blue out at the edges"*
- **MARCH** — colour indexed by how far the light travelled to get here.
  *"each contribution is tinted by its own path length"*

## Step 2's theorem, run backwards

Step 2 established that a scale about C preserves **angle**, so angle-only
modulation commutes with the sweep. Radius is the other half of that coordinate
system and it behaves the exact opposite way: a scale about C *changes* radius —
that is all it does. So:

| modulation | commutes? | consequence |
|---|---|---|
| angular | yes | before or after the sweep is the same picture |
| radial | **no** | before and after are two different **looks** |

That is not a bug to fix. Pre-multiplying tints the *source light* by where it
came from; post-multiplying tints the *shaft* by where it landed. Both are
legitimate; we ship post, because it is what the control name implies and it is
one multiply.

**And that is the punchline: the radial ramp is exact and free.** No bands, no
approximation, one multiply over the frame. Star Glint needed a 16-band
decomposition to get a coloured streak, because its distance coordinate *was* the
march index. Here the distance coordinate the artist cares about is screen
radius, which is sitting right there.

## The check that was measuring the wrong quantity

First version of check (d) compared raw pixel values, normalised by the global
max, and reported `4.8e-03` — which reads as "radial commutes too". It does not.
The metric was wrong: **the peak of both buffers is the sun**, where `u ≈ 0` and
the ramp agrees by construction, so a max-over-global-max metric is dominated by
the one region that *cannot* differ.

What actually differs is hue. Measuring hue (normalised rgb over lit pixels only)
— and measuring the angular case the same way as a control, so the number has
something to be large compared to:

```
radial  ramp, pre vs post: mean hue gap = 0.01747
angular ramp, pre vs post: mean hue gap = 0.00017   (104x smaller)
```

There it is. Step 2's theorem confirmed from the other direction, at 104×.

Fifth time now: a check reported a real number and the *metric*, not the code,
was at fault. The habit that keeps catching it is asking "what would this number
look like if the feature were broken?" — if the answer is "about the same", the
metric is not measuring the feature.

## Two Star Glint lessons, and neither of them transfers

This was the surprise of the step. Both were written into the file as
expectations, and both were wrong.

### Lesson 1: band edges must be geometric — **not here**

Star Glint discovered its band edges had to be spaced geometrically: its samples
sat at linearly-spaced distances while the weights were geometric, so linear
bands wasted resolution out in the faint tip. Its table had geometric ~3× better
at every band count.

Here the samples are **already** at geometrically-spaced radii (`r^t`), so uniform
spacing in `t` is already geometric in distance. Prediction: the correction buys
nothing. Check (b):

```
 bands    uniform-t   geometric-t
     4   6.2303e-03    1.7187e-01
     8   4.0820e-03    1.9067e-02
    16   2.8633e-03    2.9728e-03
    32   1.8416e-03    2.7998e-03
    64   1.4722e-03    2.0421e-03
```

Uniform wins at every count, and at 4 bands it wins by **28×** — applying Star
Glint's fix here would have made the effect dramatically worse at low band
counts. The two effects need opposite spacing for the same underlying reason.

Also worth noting: **16 uniform bands lands at 0.3%**, where Star Glint's 16
geometric bands landed at 2.7%. Colour is nearly ten times cheaper to approximate
in this effect.

### Lesson 2: never resample twice — **no measurable penalty here**

Star Glint found the obvious band formulation, `decay^a · shift(prefix(b-a), a·d)`,
disagreed with its plain sweep by 6%, because it resamples twice and
`shift(shift(X,u),v) ≠ shift(X,u+v)`. Check (a2) was written expecting to confirm
the same for `zoom`:

```
one resample per block: peak 1.848365e-03  mean 2.704650e-03
the obvious way     : peak 1.848365e-03  mean 2.704643e-03
the two formulations differ by rel 1.46e-07 - i.e. not at all.
```

Identical to seven significant figures. This is consistent with step 1's check
(b) — `zoom`'s composition error is ~40× smaller than `shift`'s — so the second
resample genuinely costs nothing.

The fold still ships, because it is no worse and one resample is one resample.
But the *reason* Star Glint shipped it does not apply, and check (a2) was
rewritten from "assert the fold is better" into "measure whether it is". A check
that asserts an ordering the data does not support is a check that will fail
every time someone runs it correctly.

**The general lesson across both:** a technique map like the syllabus'
cross-effect table tells you which *tools* transfer. It does not tell you which
*corrections* transfer, because a correction is a fix for a specific failure mode,
and the failure mode may simply not be present. Both of these would have shipped
unmeasured if the checks had been written as assertions instead of measurements.

## Dispersive: the free mode, and its ceiling

`ray_sweep_dispersive` gives R, G and B their own falloff. Colour then shifts
along the march at *zero* extra cost — no bands, no approximation — and it is what
real atmospheric extinction does (blue scatters out first, which is why sunbeams
go red). See `out_gr3_dispersive.png`.

The catch is stated as a measurement rather than a caveat. Check (e) computes the
chromatic variety each mode can span:

```
banded rainbow ramp : 0.0761
dispersive          : 0.0499
```

Dispersion can only ever produce one monotonic shift. You cannot express "white,
then gold, then magenta". It ships as a cheap mode, not as the general control —
same conclusion Star Glint reached about its own dispersive path, and the one
place in this step where the earlier effect's finding *did* carry over.

## Reuse

`build_lut` is Gradient Map's, imported unchanged — the **third** effect to do so.
`band()` is Star Glint step 3's stitch with `shift` swapped for `zoom`, which is
now the third algorithm that single substitution has carried across.

## Try this

1. `--explain` prints the band table: `a`, `b`, the centroid ramp position, and
   `r^a`/`r^b` — the actual radius fraction each band covers. Watch the radius
   fractions come out geometric from uniform `t`. That is lesson 1, in one column.
2. Render `radial` and `march` with the same ramp (`out_gr3_radial_sunset.png`
   vs `out_gr3_march_sunset.png`). Predict first which one has visible colour out
   at the frame edges, and why.
3. Swap `band_edges_uniform` for `band_edges_geometric` at `bands=4` and look at
   the render, not the number. 28× worse is visible.
4. In `band_u`, return the band midpoint instead of the energy-weighted centroid.
   Predict the direction of the change in check (b) before running it.
5. Set the radial ramp to a ramp that is flat (`"white"`) and confirm check (d)'s
   hue gap collapses. If it doesn't, the metric is measuring something else —
   which is the failure this step already made once.
6. Apply the radial ramp *pre*-sweep in the render loop and look at
   `teal_gold`. It is a genuinely different, arguably nicer look. Decide whether
   it deserves to be a mode.

---

# Step 4 — Controls, performance, and the things AE will demand

`gr_step4_controls.py`. Three jobs: make the parameter set real, make it fast,
and make it survive contact with After Effects.

```bash
python gr_step4_controls.py --explain cathedral
```
dumps the whole parameter block grouped the way the AE UI will group it, plus
the derived scalars and the fade radius, and renders that preset.

## Profile first. Then read what the profile says — including about you.

Star Glint step 4's lesson was "profile before optimising": it had a confident
note in step 2 naming `lateral_blur` as the bottleneck, and the profiler found
that thing was 2% of the cost.

This step managed to make the *same class* of mistake anyway, in a new way, and
the sequence is worth following because the trap was well hidden.

Step 2's check (f) reported the shimmer mask at **438 ms** at 1080p. That is a
big number, so step 4 opened by building a θ-LUT to kill it. Then the profile
ran:

```
bright_pass4                   41.2 ms
ray_sweep (the sweep)         306.8 ms
shimmer_mask  step 2           84.9 ms
shimmer_mask_lut warm          53.6 ms
colorize_radial                46.9 ms
colorize_march 16 bands      2060.6 ms
```

Two things wrong at once:

1. **The mask was never 438 ms.** It is 85 ms. Step 2's check timed one cold
   call and was measuring first-touch allocation. Both step 2's check and this
   one now warm up and take the best of three, and step 2's walkthrough number
   is corrected.
2. **Even at 85 ms it was not the bottleneck** — it is 12% of a radial-mode
   render, against the sweep's 68%. The LUT buys 1.6×, i.e. about 3% of the
   frame time.

So: an optimisation was written for a stage that was neither as slow as claimed
nor important. The LUT stays — it is correct, it is free at runtime, and the
reasoning behind it (the field is a function of one variable, *because* of step
2's theorem) is worth having written down. But it is documented as what it is.

Star Glint's lesson was "profile before optimising". The sharper version, learned
here: **profile before optimising, and don't trust a number just because you
measured it yourself.** The bad 438 ms was in this project's own walkthrough,
carrying full authority, for three steps.

The profile also priced the one thing that *is* expensive: **MARCH colour costs
6.7× a plain sweep.** That is the honest price of step 3's banded mode, and it is
why RADIAL is the default.

## The LUT had a bug the check caught

First version snapped to the nearest table entry. Check (b):

```
detail= 6.0   max|lut-exact| = 5.28e-03
detail=14.0   max|lut-exact| = 1.23e-02
detail=40.0   max|lut-exact| = 3.52e-02
```

Nearest-neighbour error is **first order** in the step size, so it scales as
`amount · k_max · h` — and it got worse with Detail exactly as that predicts. The
instinct is to grow the table. The fix is to interpolate: second order, same
table, `~1e-4`. A bigger table would have been the expensive way to fix a mistake
in the reader.

## Resolution independence: the dividend of writing everything as a ratio

AE renders previews at Half/Third/Quarter and hands the effect a smaller buffer
plus a downsample factor. Star Glint had to multiply *every* length-like control
by `res_scale`, because its Length was in pixels. Here:

| control | unit | needs scaling? |
|---|---|---|
| reach | fraction of the way to C | no |
| falloff | dimming ratio | no |
| centre | normalised 0..1 | no |
| detail | cycles per **turn** (angular) | no |
| ramp `u` | normalised by farthest corner | no |
| samples | quality knob | no (that was step 1) |
| shimmer fade radius | **buffer pixels** | see below |

Six of seven, free. That is not luck — the geometric formulation made fractions
the natural unit, and steps 1–3 kept picking them for other reasons.

Check (d) measures it: a half-size buffer matches the full render to **4.0e-02**
with `res_scale` applied nowhere at all.

And a check that cannot fail proves nothing, so the second half of (d) builds the
bug on purpose — re-expresses `reach` as an absolute pixel distance, Star
Glint-style, and forgets to scale it:

```
a pixel-valued Reach, unscaled : rel err = 1.384e+00   <- the bug
the same, scaled by res_scale  : rel err = 3.970e-02
```

35× worse. Pixel units really do break; ratios really don't.

### The seventh control, and an honest "you cannot have both"

The shimmer fade radius is in buffer pixels, and at reduced resolution two
demands conflict:

- **anti-aliasing** wants at least `k_max/π` *buffer* pixels, or the shimmer
  aliases into a crawling starburst on the source point;
- **matching the look** wants `(k_max/π)·res_scale` buffer pixels, so the preview
  shows the same-sized clean core as the final render.

For `res_scale < 1` the second is always smaller. They cannot both be satisfied.
`fade_radius` takes the max — correctness first — and accepts that a Quarter-res
preview shows a slightly larger clean core than the final. The alternative is a
preview that aliases, which is worse.

The consequence, stated plainly in the code because someone will otherwise file a
bug about it: **since `res_scale ≤ 1` always, the max is always the Nyquist term,
so `res_scale` is inert in this effect.** It stays in the signature because that
is where a reader will look for it.

## Alpha, and an assertion imported from the wrong effect

Alpha rides as a fourth channel and is swept identically. The first version of
check (c) asserted, copied straight from Star Glint, that *a transparent region
stays exactly zero*. It failed at `5.5e-01`.

The code was right and the check was wrong. **Rays are light, and light must be
able to travel into an empty region** — a shaft crossing a hole in the layer is
the entire point. Star Glint's assertion was about a wholly empty *input*, which
is a different claim. Restated, both halves now hold:

- a wholly empty input gives **exactly** zero out — no glow from nothing;
- **zero** lit-but-transparent pixels — the sweep never brightens a pixel it does
  not also make more opaque, or the result cannot composite over anything.

And the region case is reported as a `[note]` rather than a check: rgb rises to
0.554 there and alpha to 0.533. What matters is that the two rise *together*.

That is the third assertion inherited from Star Glint that turned out not to
apply — after geometric band edges and the double-resample fold, both in step 3.

## The open question from step 1, closed properly

Steps 1 and 3 both guessed that Star Glint's lateral blur — which equalised arm
width between on-axis and diagonal arms — would be unnecessary here, on the
grounds that `zoom`'s composition error is 40× smaller than `shift`'s. Step 1
even wrote "you probably don't need it here", hedged as a thing to check.

Check (f) checked. With a **point** source:

```
  0:0.0028   10:0.0047   22:0.0047   30:0.0044
 45:0.0047   60:0.0044   75:0.0047   90:0.0028      spread = 1.66x
```

The defect is *present*, with exactly Star Glint's signature — 0° and 90° sharp,
everything else ~1.7× wider. Weaker than its 2.84×, but not absent. "Closed, no
blur needed" would have been the wrong call.

The right question was not *is it there* but *does it matter*. Star Glint's
sources were points, so a 1px hairline was the whole arm. This effect's sources
are **areas** — that was step 1's subject lesson, the one that cost two failed
test images. Repeat the measurement with a disc of radius 5:

```
  0:0.0175   10:0.0168   22:0.0172   30:0.0167
 45:0.0169   60:0.0167   75:0.0173   90:0.0175      spread = 1.05x
```

Gone. The sampler defect is real and is swamped by the source's own width the
moment the source is bigger than a pixel — and here it always is, because the
bright pass keeps ~30% of the frame. **No lateral blur**, and now for a reason
rather than a hunch.

## Downsampling, and the exposure question

```
downsample=1     83.4 ms   peak err vs full = 0.000
downsample=2     36.0 ms   peak err vs full = 0.012
downsample=4     25.5 ms   peak err vs full = 0.018
```

2.3× for 1.2% peak error. Compare Star Glint, where half res cost **22%** peak
error — its streaks were thin high-contrast hairlines and downsampling destroyed
them. God-ray shafts are broad and low-frequency, so this is nearly free. Same
control, an order of magnitude different price, for the same structural reason
that killed the lateral blur.

Step 1 deferred the `1/r²` exposure question with "long reach blows out". Check
(g) settles it:

```
 reach    peak tail    mean tail
  0.20       11.549       0.2364
  0.90       11.548       0.5740
```

The **peak** is flat — it sits on the sun, which the sweep barely moves. The
**mean** grows 2.4×. So what blows out is the frame average, not the highlight:
a different problem with a different fix from the one step 1 assumed. The growth
is real light (a wider shaft collects more), so it is not normalised away — it
ships as documented behaviour with Intensity as the knob.

## Try this

1. `--explain cathedral` then `--explain march`. Diff the derived block: which
   numbers move, and which are the same because they are ratios?
2. In `shimmer_mask_lut`, go back to nearest-neighbour indexing and re-run check
   (b). Then instead of interpolating, try quadrupling `n`. Compare what each
   buys per byte.
3. Run check (a) twice in one session. The numbers should now be stable. Then
   delete the warm-up call and run it twice more.
4. Set `color_mode="march"` and time a render. 6.7× is the price of step 3's
   banded ramp — decide for yourself whether that mode earns its place.
5. In check (f), change the disc radius from 5 to 1. Find the radius at which the
   angular spread crosses back over 1.15×. That number is how big a source has to
   be before the lateral blur stops mattering.
6. Render `preview` (`res_scale=0.5`) and confirm nothing changes, then read
   `fade_radius`'s docstring and work out why that is the correct outcome rather
   than a broken parameter.

---

## Where the Python plan stops

Steps 1-4 are done and the effect is feature-complete as a prototype: radial
sweep, shimmer, three colour modes, source modes, alpha, resolution
independence, downsampling.

- **step 5 - C++.** SmartFX with output buffer expansion, since shafts leave the
  layer bounds. Then the CUDA/Metal port, where the ping-pong shape pays off:
  every pass in this effect is "resample the frame with one affine map, multiply
  by a scalar, add", which is the same GPU shape as Bloom's pyramid, Star
  Glint's arms and Buildable Stroke's JFA.

Reused wholesale from work already shipped: Bloom's linear-light spine and
bright pass, Star Glint's geometric doubling, grid cache and band stitch,
Gradient Map's ramp LUT. The genuinely new ideas are the translation->scale
rewrite (step 1) and the angular-commutation theorem (step 2, confirmed from the
other side in step 3) - and all of them are proved, not asserted.

---

## The one thing to take from this effect

**Star Glint's tools all transferred. Almost none of its corrections did.**

| Star Glint correction | here |
|---|---|
| geometric band edges | uniform-t wins, by 28x at 4 bands (step 3) |
| never resample twice | no measurable penalty (step 3) |
| lateral blur for arm width | defect real but swamped by source size (step 4) |
| transparent region stays zero | wrong claim - light must cross holes (step 4) |
| scale every length by res_scale | six of seven controls are ratios (step 4) |
| dispersive is a cheap mode, not the general one | transferred unchanged (step 3) |

One out of six. A cross-effect technique map tells you which *tools* transfer; it
cannot tell you which *corrections* do, because a correction is a fix for a
specific failure mode and the failure mode may simply not be present.

Every one of those was caught the same way: by writing the check as a
**measurement** rather than an assertion, and by pairing it with a deliberately
broken control so it could not pass for the wrong reason. Where a check did fail,
it was usually the reference or the metric at fault, not the code - step 3's hue
metric and step 4's alpha assertion both looked like bugs and were not.

And the reverse failure happened too: step 2 shipped a timing number that was
never real, this walkthrough repeated it with full authority for three steps, and
step 4 wrote an optimisation for it before the profiler caught on. Measure, but
do not trust a measurement just because it is yours.

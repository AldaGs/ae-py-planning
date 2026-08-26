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
   **2977 ms**, phase-only re-apply **438 ms** — 6.8×. In AE that is the
   difference between a cached render and a full recompute per frame.
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

## Where step 2 stops, and what comes next

- **step 3 — colour.** Star Glint's band decomposition applies: the doubling
  tables slice into distance bands, each tinted from a ramp LUT, and Gradient
  Map's `build_lut` gets imported for the third time. One thing to check early:
  because the step here is already geometric, *uniform* band edges in `t` are
  already geometric in distance — which is the spacing Star Glint had to
  discover the hard way. This may be cheaper here than it was there.
- **step 4 — controls, performance, and AE realities.** Source-mask modes
  (luma / alpha / matte) live here alongside alpha as a fourth channel.
  Resolution independence needs verifying rather than assuming: `reach` is a
  *fraction*, so it may already be free — but `detail` and the Nyquist fade are
  in pixels and certainly are not. Also the `1/r²` exposure question from step 1,
  and a LUT over θ for the shimmer mask (check (f) shows the mask build is 438 ms
  at 1080p, which is not nothing).
- **step 5 — C++.** SmartFX with output buffer expansion, since shafts leave the
  layer bounds. Then the CUDA/Metal port, where the ping-pong shape pays off.

Reused wholesale from work already shipped: Bloom's linear-light spine and
bright pass, Star Glint's geometric doubling and grid cache, Gradient Map's ramp
LUT (step 3). The genuinely new ideas are the translation→scale rewrite (step 1)
and the angular-commutation theorem (step 2) — and both are proved, not asserted.

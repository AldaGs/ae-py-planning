# After Effects Effect Plugins — Build & Research Reference

A menu of candidate first/second plugins. Each entry is self-contained so you can
open it cold later, research from the named algorithms, and start building. Read
the **Shared Foundations** section once — it applies to every plugin and is not
repeated per entry.

---

## Shared Foundations (read once)

### Platform & SDK
- **Language:** C++. Effects are `.aex` (Win) / `.plugin` (Mac) bundles.
- **SDK:** Adobe After Effects SDK (free download from Adobe developer console).
  The GPU pieces are shared with the Premiere Pro SDK.
- **Toolchain:** Visual Studio (Windows) or Xcode (macOS). The SDK ships sample
  projects — start by cloning `Skeleton` / `Checkout` samples, don't scaffold from zero.
- **Registration:** each effect needs a **PiPL** resource (still required for AE)
  describing name, match name, category, and global flags.
- **Entry model:** one `EffectMain` with a `PF_Cmd` command-selector `switch`
  (`PF_Cmd_GLOBAL_SETUP`, `_PARAMS_SETUP`, `_RENDER` / smart-render selectors, etc.).

### Pixel data & color
- **Bit depths:** 8-bit, 16-bit, and 32-bit float. **Always develop against 32-bit
  float** — it's where quality lives and avoids clamping/banding. Support the others
  via templating or a shared inner loop.
- **Color space:** AE hands you pixels in the project's working space. Effects that
  simulate *light* (glow, god rays, star) must convert **sRGB → linear**, process,
  then **linear → sRGB**. This single step is the biggest quality differentiator over
  AE's legacy effects.
- **Premultiplied alpha:** know whether you're operating on straight vs premultiplied;
  edge effects (stroke, glow) are sensitive to this.

### CPU vs GPU
- **CPU path is mandatory** — it's the required fallback and the fastest way to nail
  the *look* before you fight GPU plumbing.
- **GPU path (optional, do it second):** set `PF_OutFlag2_SUPPORTS_GPU_RENDER_F32`
  (and the DirectX flag for that path). Write **one kernel** using the **PrGPU macros**
  that compiles to CUDA, Metal, and DirectX.
  - AE 25.4 uses **CUDA 12.8** (Windows/NVIDIA). Prefer the CUDA **Driver API** for
    forward compatibility.
  - macOS uses **Metal**.
  - DirectX path needs the **DirectX Shader Compiler**; ship the generated
    `DirectXAssets` folder next to the binary.

### SmartFX & buffer expansion (critical for edge effects)
Any effect whose output is **larger than its input** — stroke, glow, long shadow,
god rays, star — must use the **SmartFX** API (`PF_OutFlag2_SUPPORTS_SMART_RENDER`):
- In `PF_Cmd_SMART_PRE_RENDER`, **expand the output request rect** so the extra pixels
  (the stroke ring, the glow halo, the shadow) aren't clipped to the layer bounds.
- Check out the input with the region you actually need.
- This is a common first-plugin gotcha: a glow that gets cut off at the layer edge
  almost always means the pre-render rect wasn't expanded.

### Multi-pass note
Per-pixel effects (chromatic aberration, gradient map, halftone) can be computed
looking only at the current pixel — simplest possible structure. **Neighborhood /
multi-pass effects** (everything involving blur, distance, or accumulation) must
ping-pong between buffers. Budget your learning for this jump; it's the real
skill gate.

### Math background by tier
- **Per-pixel effects:** basic arithmetic, `smoothstep`, bilinear sampling.
- **Convolution effects:** separable kernels, gradients (Sobel).
- **Distance / accumulation effects:** distance transforms, prefix sums.
- **Bloom/scatter:** mip pyramids, weighted accumulation.

---

## Recommended learning order

1. **Gradient map** or **Chromatic aberration** — per-pixel warmups; learn the SDK skeleton.
2. **Buildable stroke** — first multi-pass effect; owns the distance-field technique.
3. **Long shadow** — reuses stroke/distance work; finishes a related pair.
4. **Linear-light glow** — introduces the bloom pyramid + linear color.
5. **God rays** and **Star/glint** — radial/directional sampling; build on bloom.
6. **VHS glitch** — a composite of easy parts; the crowd-pleaser / portfolio piece.
7. **Pixel sort**, **Halftone** — stylistic, independent, medium.

---

# Per-Effect Specs

---

## 1. Chromatic Aberration  *(warmup — easy)*

**What it is:** RGB channels displaced radially from a center point, increasing with
distance, for a lens-fringe look.

**Type:** Per-pixel, single-pass. No SmartFX needed (can stay within bounds if
displacement is small; expand slightly if not).

**Techniques:** Radial displacement; per-channel independent sampling; bilinear
interpolation on resample. Optional barrel/lens distortion for realism.

**Algorithms:** For each output pixel, compute vector from center; offset R and B
sample positions along that vector by `±k·distance`; sample each channel separately
with bilinear filtering.

**Requirements:** SDK skeleton, bilinear sampler. No GPU strictly needed but it's a
trivial kernel to port.

**Steps:** (1) SDK skeleton + params (center, amount, falloff). (2) Per-pixel loop:
compute radial offset, sample 3 channels at 3 positions. (3) Add optional distortion
curve. (4) Port kernel to GPU (near-identical).

**Reality check:** free versions abound — this is for learning the pipeline, not
filling a gap.

---

## 2. Gradient Map / Duotone  *(warmup — easy)*

**What it is:** Remap each pixel's luminance through a color ramp (shadows→highlights
mapped to chosen colors).

**Type:** Per-pixel, single-pass.

**Techniques:** Luminance extraction; 1D LUT lookup.

**Algorithms:** Compute luma (Rec.709 weights); use it as index `t ∈ [0,1]` into a
gradient ramp; output ramp color. Precompute the ramp into a 256+ entry LUT for speed.

**Requirements:** SDK skeleton, a gradient/ramp param (or a small color-stop UI).

**Steps:** (1) Skeleton + gradient param. (2) Build LUT from stops on param change.
(3) Per-pixel: luma → LUT. (4) Optional: preserve original alpha, blend amount.

---

## 3. Buildable Stroke  *(first real project — medium)*

**What it is:** Generate any number of stacked, colored strokes expanding outward
(and/or inward) from the layer's alpha edge, each with its own offset, width, color.

**Type:** Multi-pass, output **larger than input** → **SmartFX required**.

**Techniques:** **Distance field** from the alpha, then band-thresholding. Do **not**
use pixel normals for this — normals give direction but not clean distance, and
produce ragged concentric rings.

**Algorithms (pick by stage):**
- **Stage 1 — morphological dilation:** 3×3 max filter, run N times; difference of
  successive passes = rings. Trivial to write, slow for wide strokes. Great for
  getting the look + multi-stroke UI right.
- **Stage 2 — real distance transform:**
  - **Felzenszwalb–Huttenlocher** — exact Euclidean, two linear-time passes, CPU-ideal.
  - **Jump Flooding Algorithm (JFA)** — approximate, `log(n)` passes, GPU standard.
    Learn this if heading to GPU; it also powers SDF glow/shadow/text.
- **Banding:** for each stroke, `if (d ≥ start && d < start+width) out = color`.
  Anti-alias with `smoothstep` on the fractional distance.

**Requirements:** SmartFX buffer expansion; a repeatable/array param model for N strokes
(offset, width, color, order); distance-transform implementation.

**Steps:** (1) SmartFX skeleton, expand pre-render rect. (2) Iterative dilation +
multi-stroke stacking UI — nail the look. (3) Replace core with FH (CPU) or JFA (GPU)
distance field. (4) Add smoothstep AA, inner/outer/both modes, per-stroke opacity.

**Reality check:** no great free realtime version; directly teaches the technique that
unlocks glow, long shadow, and SDF work. Best first *real* plugin.

---

## 4. Long Shadow  *(medium — reuses distance/edge work)*

**What it is:** Flat-design hard shadow cast in a fixed direction from the alpha.

**Type:** Multi-pass, output larger than input → **SmartFX required**.

**Techniques:** Directional accumulation along the shadow axis.

**Algorithms:**
- **Simple:** repeated translate-and-max (dilate along a line) — N steps for length N.
- **Fast:** **prefix-sum / running-max along the shadow direction** — one pass per row
  aligned to the axis; O(1) per pixel regardless of length.
- Optional: fade shadow with distance; composite under the layer.

**Requirements:** SmartFX; directional traversal (may need to sample along a rotated
axis with bilinear interp).

**Steps:** (1) SmartFX skeleton, expand rect toward shadow direction. (2) Translate-and-max
version for correctness. (3) Swap to prefix-sum accumulation for speed. (4) Add angle,
length, color, distance falloff, opacity.

**Reality check:** genuinely weak free options; finishes a natural pair with the stroke.

---

## 5. Linear-Light Glow / Bloom  *(medium — the Deep Glow alternative)*

**What it is:** Physically plausible bloom: bright areas bleed light with wide, smooth,
natural falloff. Beats AE's built-in Glow mainly by working in linear light.

**Type:** Multi-pass, output larger than input → **SmartFX required**.

**Techniques:** Linear color conversion; soft-threshold bright pass; **mip-pyramid
bloom**; additive composite.

**Algorithms:**
- **Linear light:** sRGB → linear before processing, back after. (Biggest visual win.)
- **Bright pass:** soft threshold with a "knee" (avoid hard clip).
- **Pyramid blur:** downsample bright pass into a mip pyramid; blur each level cheaply;
  upsample + accumulate. This is the Jimenez *"Next Generation Post Processing in Call
  of Duty: Advanced Warfare"* (SIGGRAPH 2014) approach. Alternative: **dual Kawase**
  filter. A single large Gaussian is too slow and looks worse.
- **Composite:** add bloom over original in linear, convert back.

**Requirements:** SmartFX (halo extends past bounds); mip/downsample-upsample buffers;
32-bit float essential.

**Steps:** (1) SmartFX skeleton. (2) CPU: linear convert → threshold → separable
Gaussian → add. Confirm the linear-light quality difference. (3) Replace blur with mip
pyramid. (4) Port to GPU. (5) Long-tail polish: falloff curve, highlight compression,
chromatic tint, radius/intensity/threshold controls.

**Reality check:** core is very reachable; *pixel-parity with Deep Glow* is a tuning
grind, not an algorithm problem.

---

## 6. God Rays / Volumetric Light  *(medium — high demand)*

**What it is:** Light streaks radiating outward from a source point (Trapcode Shine-like).

**Type:** Multi-pass, output can exceed bounds → **SmartFX** if rays leave the layer.

**Techniques:** Radial blur / post-process light scattering.

**Algorithms:** **"Volumetric Light Scattering as a Post-Process"** (Kenny Mitchell,
GPU Gems 3) is the canonical reference. Iterative radial sampling: step samples from
each pixel toward the light source, accumulating with per-step **decay**, weighted by
**density / weight / exposure**. Best done in linear light.

**Requirements:** SmartFX if unbounded; radial sampler; ideally GPU (many samples per
pixel is heavy on CPU).

**Steps:** (1) Skeleton + source-position, decay, density, weight, exposure params.
(2) CPU radial accumulation loop (low sample count to prototype). (3) Optimize / move
to GPU for real sample counts. (4) Combine with a bright-pass mask so only highlights
throw rays.

**Reality check:** Shine is expensive and free options are poor — high community value.

---

## 7. Star / Glint (Starglow-like)  *(medium)*

**What it is:** Directional star/streak flares radiating from highlights (4/6/8-point).

**Type:** Multi-pass, larger than input → **SmartFX**.

**Techniques:** Anisotropic (directional) streak blur at multiple angles; additive
accumulation; optional per-streak chromatic tint.

**Algorithms:** Bright pass → for each streak direction, a **directional streak blur**
(geometric-decay taps along the angle, à la a 1D Kawase streak) → sum all directions
additively. Reuses your bloom bright-pass and linear-light setup.

**Requirements:** SmartFX; directional 1D blur; multiple accumulation buffers.

**Steps:** (1) Skeleton + params (points, angle, length, per-streak color, boost).
(2) Bright pass. (3) One directional streak, then replicate at N angles and sum.
(4) Add chromatic per-streak tint, GPU port.

**Reality check:** Starglow is pricey; natural companion once glow exists.

---

## 8. VHS / Analog Glitch  *(medium — crowd favorite / portfolio piece)*

**What it is:** Composite retro-video degradation. The craft is in combining and
animating simple parts, not any single hard algorithm.

**Type:** Mostly per-pixel + per-row; single or few passes. Time-driven.

**Techniques (the stack):**
- **Chromatic aberration** — channel offset (see #1).
- **Scanlines** — sinusoidal luma modulation by row.
- **Horizontal line displacement / tracking** — per-row X offset driven by noise.
- **Chroma bleed / subcarrier shift** — smear color horizontally, separate from luma.
- **Static noise** — hash/value noise per pixel per frame.
- **Optional:** rolling bands, vertical hold jitter, edge ringing.

**Algorithms:** Per-row offset = `noise(row, time)`; noise via a hash function or
**value/Perlin/simplex** noise; animate everything off the layer time. Keep each
sub-effect toggleable with its own intensity.

**Requirements:** SDK skeleton, a noise function, time input. Little/no SmartFX.

**Steps:** (1) Skeleton + time + master intensity. (2) Add sub-effects one at a time,
each with its own controls. (3) Tune the *combination* and animation — this is where it
lives. (4) Preset the good combos.

**Reality check:** free versions are scattered and inconsistent; a polished native one
gets real traction and shows range.

---

## 9. Pixel Sort  *(medium — trendy)*

**What it is:** Sort pixels along each row/column within luminance-thresholded spans —
the melting/streaking glitch look.

**Type:** Per-row/column pass.

**Techniques:** Threshold-masked span detection + sorting by a key.

**Algorithms:** Classic **Kim Asendorf** pixel-sort: within each row, find contiguous
spans where luma is inside a threshold window; sort each span's pixels by luma (or hue/
saturation). Direction, threshold bounds, and sort key are the controls.

**Requirements:** SDK skeleton; a fast in-place sort; row/column traversal. A native
(vs scripted) version is the value-add — most free ones are slow.

**Steps:** (1) Skeleton + params (angle, low/high threshold, sort key). (2) Row span
detection. (3) Sort spans. (4) Generalize to arbitrary angle; optimize.

---

## 10. Halftone / Dot Screen  *(medium — stylish, thin free options)*

**What it is:** Print/comic halftone — image reproduced as a grid of variable-size dots.

**Type:** Per-pixel against a screen function; single-pass.

**Techniques:** Amplitude-modulated screening; rotated screens per channel for CMYK.

**Algorithms:** Compare each pixel's tone to a **rotated dot-grid screen function**;
dot size scales with local tone. For color, run **rotated screens per CMYK channel** at
classic screen angles. (Note: Floyd–Steinberg is *dithering* — related but a different
look; mention only if you want an error-diffusion mode.)

**Requirements:** SDK skeleton; screen/grid generation; per-channel angle handling.

**Steps:** (1) Skeleton + params (dot size, angle, mono/CMYK). (2) Mono AM screen.
(3) Add per-channel rotated screens. (4) AA the dot edges with smoothstep.

---

## Avoid for now

**Motion blur (ReelSmart-style):** requires optical-flow / motion-vector estimation.
Genuinely hard, easy to do badly. Revisit much later.

---

## Cross-effect technique map (what teaches what)

- **Distance field (JFA / FH):** stroke → long shadow → SDF glow/shadow/text.
- **Linear light + bright pass:** glow → god rays → star.
- **Mip pyramid:** glow → (downsample/upsample reused broadly).
- **Directional blur:** long shadow → star streaks.
- **Radial sampling:** chromatic aberration → god rays.
- **Noise + time:** VHS → any animated procedural effect.

Owning **distance fields** and the **bloom pyramid** unlocks the majority of this list.

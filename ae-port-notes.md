# Chromatic Aberration — AE SDK Port Notes

Companion to `ae-effect-plugins-spec.md`. Tracks the port of the Python prototype
(`python-proto/step1..5`) into a real After Effects `.aex` plugin.

Environment: After Effects 26.3, Visual Studio 18.9 (2026), Windows 11, new to C++.

---

## The architecture in one screen

- `.aex` = a DLL AE loads at startup. It calls ONE function: `EffectMain`.
- `EffectMain(cmd, in_data, out_data, params[], output, extra)` is a `switch (cmd)`:
  - `PF_Cmd_ABOUT`         — About-box text.
  - `PF_Cmd_GLOBAL_SETUP`  — declare version + global out-flags (bit depths, GPU).
  - `PF_Cmd_PARAMS_SETUP`  — create the UI params (our Step 4 controls).
  - `PF_Cmd_RENDER`        — classic render: loop pixels. 8/16-bit. <- WE START HERE.
  - `PF_Cmd_SMART_PRE_RENDER` / `PF_Cmd_SMART_RENDER` — SmartFX: 32-bit float,
    buffer expansion. Upgrade later.
  - `PF_Cmd_GLOBAL_SETDOWN`— cleanup.
- **PiPL** (`.r` resource) = plugin's "business card": name, match-name (immutable
  internal ID), category (Effect submenu), global flags. Wrong PiPL => effect never
  appears in AE.

## Pixels in classic render (maps to Python Step 1-3)

- Input/output are `PF_EffectWorld` = the `(H, W, channels)` array from Step 1.
- Access via raw pointer + `rowbytes` (bytes per row, may be padded):
  `pixel(x,y) = (PF_Pixel8*)((char*)world->data + y*world->rowbytes) + x`
- `PF_Pixel8` has named fields `.alpha .red .green .blue` (0..255). No BGR confusion.
- Bilinear sampler = write the Step 2 function in C++, reading via those pointers.
- 32-bit path uses `PF_Pixel32` (float 0..1) — same math, why the spec says
  "develop against 32-bit float."

## Parameter plan (from Python Step 4)

| Python param | AE control type        | Notes                                  |
|--------------|------------------------|----------------------------------------|
| center (cx,cy)| PF_Param_POINT        | artist drags on-screen; % of layer     |
| amount 0..100 | PF_Param_FLOAT_SLIDER | remap to internal k inside render      |
| falloff 0..1  | PF_Param_FLOAT_SLIDER | 0=linear, 1=distance-squared           |
| invert        | PF_Param_CHECKBOX     | flips sign of k                        |
| edge mode     | PF_Param_POPUP         | clamp / reflect / transparent (later)  |
| distortion    | PF_Param_FLOAT_SLIDER | optional barrel/pincushion (later)     |

Anti-aliasing: provided by the bilinear sampler itself (we re-read existing soft
edges). AE does NOT auto-AA effect output. No generative AA needed (we draw nothing).

## Setup milestone (Step 6)

Goal: build the STOCK `Skeleton` sample unmodified and see it in AE's Effect menu.

1. Download AE Plug-in SDK: https://developer.adobe.com/after-effects/  (free).
2. Unzip to a short, space-free path e.g. `C:\AE_SDK\`.
3. Open `Examples\Skeleton\Skeleton.sln` in Visual Studio. Accept retarget prompt.
4. Build Release x64. Output/copy the `.aex` to:
   `C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\Plug-ins\`
5. Launch AE -> Effect menu -> find the sample. Toolchain proven.

## Status: COMPLETE (Steps 7-10 done)

- Step 7 DONE: 4 params (Amount/Center/Falloff/Invert) under Effect > Learning.
- Step 8 DONE: classic `PF_Cmd_RENDER` 8/16-bit, manual bilinear sampler.
- Step 9 DONE: MFR enabled (thread-safe: no globals, state via local/handle).
- Step 10 DONE: SmartFX PreRender/SmartRender, 8/16/**32-bit float** via AE's
  subpixel_sample suites. Renders fast (MFR), center + all controls correct.

Effect name "Chromatic Aberration", match name "aldai ChromaticAberration".
Source lives in the SDK Skeleton sample dir (Examples/Template/Skeleton).

## Build & deploy workflow (important - this shell isn't elevated)

- Build to a WRITABLE dir, not Program Files:
  `AE_PLUGIN_BUILD_DIR=C:\AE_SDK\_build_out\` then MSBuild Debug|x64.
- CLOSE After Effects before every build (it locks the loaded .aex).
- Deploy needs admin: elevated copy from `_build_out\Skeleton.aex` to
  `C:\Program Files\Adobe\Common\Plug-ins\7.0\MediaCore\` (shared AE+Premiere path).
- Flags: out_flags 0x02000040, out_flags2 0x08001400 (code + PiPL must match).

## Possible next work

- Edge/alpha refinement: currently alpha = inP->alpha (unchanged). For
  transparent layers, sample/shift alpha and expose an edge-mode popup (Py Step 5).
- GPU path (CUDA/Metal/DirectX) via PrGPU macros - big, optional.
- Next effect in ae-effect-plugins-spec.md: Gradient Map (per-pixel warmup) or
  Buildable Stroke (first real multi-pass / distance-field project).

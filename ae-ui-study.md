# After Effects Plugin UI — Study & Reference

A map of what UI the AE SDK gives you, how each piece works, and how you'd
implement it — grounded in the samples actually present in this SDK
(`ae25.6_61.64bit.AfterEffectsSDK/Examples/`). Written as the next study topic
after the Gradient Map port; no code yet, just the lay of the land so we can
choose deliberately.

Read the **Two layers** section once — everything else hangs off that split.

---

## Two layers of UI

Every effect's controls come from one of two mechanisms. Knowing which layer a
feature lives in tells you how much work it is.

1. **Standard parameters** — you call `PF_ADD_*` in `ParamsSetup` and AE draws
   the control for you (sliders, checkboxes, color swatches, etc.). Zero drawing
   code. This is everything the Gradient Map uses today.

2. **Custom UI** — you take over a rectangle of screen (in the Effect Controls
   panel, the Comp, or the Layer panel) and *draw it yourself*, then handle mouse
   and keyboard events. This is the gradient bar, the Curves graph, the CCU color
   wheels. Much more work; total control.

Most "how do they do that?" questions (the gradient bar included) are answered by
"standard param to *store* the data + custom UI to *edit* it visually."

---

## Layer 1 — Standard parameter types (what `PF_ADD_*` gives you)

These need no drawing code. Reference sample: **`Effect/Paramarama`** (a tour of
param types); supervision shown in **`UI/Supervisor`**.

| Macro | Control | Value read from `params[i]->u.___` | Notes |
|-------|---------|-----------------------------------|-------|
| `PF_ADD_FLOAT_SLIDERX` | slider + type-in | `fs_d.value` | separate drag vs typed range (you used this for Blend) |
| `PF_ADD_FIXED` / `PF_ADD_SLIDER` | integer slider | `sd.value` / `fd.value` | older int sliders |
| `PF_ADD_CHECKBOXX` | checkbox | `bd.value` | Reverse, Linearize |
| `PF_ADD_COLOR` | color swatch + picker | `cd.value` (`PF_Pixel`, 8-bit) | your 3 stops; note **8-bit** colors even in 32-bit render |
| `PF_ADD_POINT` | crosshair (2D) | `td.x_value/y_value` (16.16) | CA center |
| `PF_ADD_POINT_3D` | 3D point | `point3d_d` | |
| `PF_ADD_ANGLE` | angle dial | `ad.value` | god-rays direction, etc. |
| `PF_ADD_POPUP` | dropdown | `pd.value` (1-based) | Interp mode |
| `PF_ADD_LAYER` | layer picker | `ld` | pull pixels from another layer (Colorama "use another layer") |
| `PF_ADD_FLOAT_SLIDERX` w/ `PF_ValueDisplayFlag_PERCENT` | % slider | `fs_d.value` | display sugar |
| `PF_ADD_BUTTON` | push button | handled via events | triggers an action |
| `PF_ADD_ARBITRARY_DATA` | **your custom blob** | `arb_d.value` (a handle) | the gradient's data lives here — see Layer 3 |
| `PF_ADD_TOPIC` / `PF_END_TOPIC` | collapsible group | — | organize the panel |

**Grouping & polish, still no custom UI:** topics (collapsible sections),
`PF_ParamFlag_START_COLLAPSED`, `PF_PUI_DISABLED` to gray out a param, and
supervision (below) to show/hide or relabel params dynamically. A 5-stop gradient
with position sliders — the "middle ground" we discussed — lives entirely here.

### Parameter supervision (dynamic params) — `UI/Supervisor`
Params that *react to each other*: gray out "Aspect" when Shape isn't Ellipse,
change a slider's max based on a popup, relabel on the fly. Done by handling
`PF_Cmd_USER_CHANGED_PARAM` and the `PF_Cmd_UPDATE_PARAMS_UI` command, or the
modern `DynamicStreamSuite`. No drawing — it's still standard params, just
supervised. Cheap way to make a panel feel smart.

---

## Layer 2 — Custom UI (drawing your own controls)

You register a UI area and draw into it. This is the same machinery you already
used for the Chromatic Aberration **comp overlay** — extended to the Effect
Controls panel.

**The three surfaces** (set in `PF_CustomUIInfo` during `ParamsSetup`):
- **ECW** (Effect Controls Window) — a custom rectangle *inside the params panel*.
  This is where a gradient bar, curve graph, or color wheel lives.
  `PF_CustomEFlag_EFFECT`, with `ui_width`/`ui_height`.
- **Comp** panel overlay — `PF_CustomEFlag_COMP` (your CA center/shape overlay).
- **Layer** panel overlay — `PF_CustomEFlag_LAYER`.

**How it works (the event loop):** you handle `PF_Cmd_EVENT`, switching on
`ev->e_type`:
- `PF_Event_DRAW` — paint the control. Modern drawing is via **Drawbot** suites
  (paths, fills, strokes, text, and gradient fills) — the same
  `Supplier`/`Surface`/`Path` API you used to stroke the CA overlay.
- `PF_Event_DO_CLICK` / `PF_Event_DRAG` — mouse down/drag: hit-test your handles,
  move a stop, select one.
- `PF_Event_ADJUST_CURSOR` — change the cursor when hovering a handle.
- `PF_Event_CHANGE_CURSOR`, keyboard events, etc.

**Reference samples for Layer 2:**
- **`UI/Custom_ECW_UI`** — the canonical "many custom-ECW features" demo: drawing,
  cursors, click handling. *Start here for custom UI drawing.*
- **`UI/CCU`** ("Custom Color UI") — color wheels drawn with Drawbot; a real
  interactive custom control.
- **`UI/HistoGrid`** — advanced: draws a preview that needs the **upstream frame**,
  using `RenderAsyncManager` inside `PF_Event_DRAW` to request that frame
  asynchronously (required since AE 13.5 split UI and render threads). Relevant if
  a control needs to *show image-derived data* (a histogram, a live preview).

---

## Layer 3 — Arbitrary data (custom values that save & animate)

When your control's value isn't a slider or color but a *structure* — a list of
gradient stops, a grid of colors, a spline's control points — you use
`PF_ADD_ARBITRARY_DATA`. AE stores an opaque handle; you teach it how to manage
that blob by handling the `PF_Cmd_ARBITRARY_*` commands.

**Reference sample: `UI/ColorGrid`** — an editable grid of colors stored as
arbitrary data, with custom UI to edit it. Its files show the exact split:
`ColorGrid_Arb_Handler.cpp` (the data callbacks) + `ColorGrid_UI_Handler.cpp`
(the drawing/interaction). **This is the closest existing model to a gradient
editor.**

**The arbitrary-data callbacks you must implement:**

| Command | Job | Why it's required |
|---------|-----|-------------------|
| `ARBITRARY_NEW` | make the default value | AE needs an initial blob |
| `ARBITRARY_DISPOSE` | free it | memory |
| `ARBITRARY_COPY` | duplicate | AE copies values around |
| `ARBITRARY_COMPARE` | equal? | change detection, caching, keyframe dedupe |
| `ARBITRARY_FLATTEN` / `_UNFLATTEN` | serialize ↔ disk (endian-safe, no pointers) | **so it saves in the project file** |
| `ARBITRARY_INTERP` | blend two values by a ratio | **so it animates between keyframes** |
| (`GET_FLATTENED_SIZE`) | byte size when flat | pairs with flatten |

The `INTERP` callback is the magic: implement it and your gradient *tweens* when
keyframed — stop colors and positions animate. Skip it and the param only steps.

---

## How the gradient bar is actually built (putting it together)

A visual gradient editor = **Layer 3 (data) + Layer 2 (editing)**:
1. **Data:** an arbitrary-data param holding `N` stops (`position ∈ [0,1]`,
   `color`), plus all the `ARBITRARY_*` callbacks (model it on `ColorGrid`).
2. **UI:** an ECW custom area that on `PF_Event_DRAW` paints the ramp (Drawbot
   gradient fill) and the stop handles; on `DO_CLICK`/`DRAG` adds/moves/selects a
   stop; on double-click pops the OS color picker for the selected stop.
3. **Render:** unchanged from what we have — read the stops from the arb param,
   run the *same* `GM_BuildLUT` we already wrote. **The algorithm doesn't change;
   only where the stops come from does.** That's the payoff of the LUT design.

Effort: comparable to the whole rest of the plugin. It's a real project, not a
tweak — which is why we're studying, not bolting it on.

---

## Suggested study path (when we take this on)

1. **`Effect/Paramarama`** — see every standard param type in one panel. Cheap win;
   also the basis for the 5-stop + position-slider middle ground.
2. **`UI/Supervisor`** — dynamic params (enable/disable, relabel). Makes panels
   feel smart with no drawing.
3. **`UI/Custom_ECW_UI`** — the core custom-UI drawing + event model in the params
   panel. The real gate.
4. **`UI/CCU`** — a full interactive custom color control, Drawbot-drawn.
5. **`UI/ColorGrid`** — arbitrary data + custom UI together: the direct template
   for a gradient editor.
6. **`UI/HistoGrid`** — only if a control must display image-derived data
   (async upstream-frame requests). Skip for a gradient.

You already own the hardest prerequisite: the `PF_Cmd_EVENT` / `PF_Event_DRAW` /
Drawbot path, from the CA comp overlay. The genuinely new muscle is **arbitrary
data** (Layer 3).

---

## Quick answers to "what's available / how / can we"

- **Is there a built-in gradient param?** No. No `PF_ADD_GRADIENT`. Gradient bars
  are always arb-data + custom UI.
- **Cheapest way to more gradient control?** More `PF_ADD_COLOR` stops + a
  position slider each (Layer 1 only). No custom UI.
- **Can the gradient animate?** Yes — via the `ARBITRARY_INTERP` callback.
- **Can a control show the image (histogram/preview)?** Yes — `HistoGrid`'s async
  upstream-frame pattern.
- **Does any of this change the render math?** No. Custom UI only changes how the
  user *edits* the stops; `GM_BuildLUT` + the per-pixel lookup stay as-is.

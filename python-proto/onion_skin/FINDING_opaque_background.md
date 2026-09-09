# The adjustment-layer model dies on an opaque background

Found 2026-09-09, first thing in Phase 1, by testing the assumption instead of
building on it.

## The measurement

`onion()` run on the same 24 frames, twice — once where the drawing sits on
transparency, once where the comp has an opaque background below the adjustment
layer (which is what an adjustment layer actually receives):

| comp | `max｜result − input｜` |
|---|---|
| drawing on transparency | 0.758 — onion skin visible |
| **opaque background below** | **0.000 — onion skin completely invisible** |

Not "degraded". **Exactly zero.** Every checked-out neighbour is fully opaque, so
each skin covers the one beneath it, and `C(t)` — also fully opaque — covers all
of them. The effect renders its own input, perfectly.

This is the failure mode already recorded in
`effect-bbox-vs-transport-reach`: *an opaque background hides the bug entirely.*
Here it does not hide a bug, it **is** the bug.

## Why the model was wrong, precisely

The adjustment layer's input is *the composite of everything below it*. Onion
skinning needs *the drawing's own alpha*. Those are the same thing only in a comp
with no background — which is not a comp anyone animates in.

The compositing model itself is fine and all its checks still pass. What is wrong
is **where the frames come from**.

## The fix, and it is documented SDK practice

Give the effect a **layer parameter** naming the drawing source, and check *that*
out at time offsets, rather than reading the effect's own input:

```c
PF_ADD_LAYER(...)                       // "Drawing Layer"
PF_CHECKOUT_PARAM(in_data, OS_SOURCE,
                  in_data->current_time + k * in_data->time_step,
                  in_data->time_step, in_data->time_scale, &checked_out);
```

This is exactly what the SDK's own `Checkout` sample does — `PF_ADD_LAYER` at
param `CHECK_LAYER`, checked out at
`current_time + params[CHECK_FRAME]->u.sd.value * time_step`. Verified in
`Examples/Effect/Checkout/Checkout.cpp:191`. Not speculative.

Consequences, all good:

- The skins carry the drawing's **real alpha**, whatever sits below.
- The background can be anywhere; it is never sampled.
- The effect still composites over its own input, so the result lands in the comp
  normally and exports normally.
- The layer param is a **stream like any other**, so the Phase 0 panel writes it
  the same way it writes everything else.

## What it costs

The user (or the AEGP) has to say **which layer is the drawing**. That is one
more control, and one more thing the managed-layer story has to set up.

For multiple drawing layers, the source becomes a precomp — the same answer AE
gives for every other "treat these layers as one" problem.

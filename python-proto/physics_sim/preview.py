"""
A5 -- the preview renderer.

This is deliberately NOT a debug view of the solver. It is a second, dumber
consumer of the bake: it reads the keyframe JSON and the layer geometry, and
draws what AE would draw. Nothing here imports pymunk or touches a Handle.

That separation is the whole point. `sim.replay_from_keyframes` shares code and
assumptions with the solver, so it cannot catch a fault in the conventions
themselves -- a y-up flip, degrees baked as radians, Position written as the COM
instead of the anchor. A renderer that only ever sees the JSON can.

WHAT THE PREVIEW NEEDS THAT THE BAKE DOES NOT CARRY
---------------------------------------------------
The bake has no geometry, and it should not: in AE the geometry already lives on
the layer, and the keyframes are the only thing we write. So a preview takes two
inputs, exactly as AE does -- the bake, plus the layers in LAYER space with
their anchors. Phase B reads that second half out of the real comp.

    world = position + R(theta) * (vertex_layer - anchor)

Comp space is y-down and so is image space, so there is no flip anywhere in the
rasteriser either. That is the y-down convention from A1 paying out a third time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

Vec = tuple[float, float]
Poly = list[Vec]

PALETTE = [
    (232, 106, 84), (78, 154, 208), (126, 196, 108), (238, 186, 74),
    (168, 124, 208), (86, 198, 190), (226, 132, 178), (150, 150, 150),
]


@dataclass
class PreviewLayer:
    """A layer as AE holds one: content in layer space, plus an anchor."""
    id: int
    name: str
    parts: list[Poly]
    anchor: Vec = (0.0, 0.0)
    color: tuple[int, int, int] | None = None


@dataclass
class PreviewScene:
    width: int = 1920
    height: int = 1080
    statics: list[tuple[Vec, Vec]] = field(default_factory=list)
    background: tuple[int, int, int] = (24, 26, 30)


# --------------------------------------------------------------------------
# Reading the bake
# --------------------------------------------------------------------------

def _sample(track: list, t: float, scalar: bool):
    """Linear interpolation between keyframes -- AE's Linear interpolation.

    At integer t this returns the stored value untouched, which is the case
    that matters: our bake writes one keyframe per frame. It takes float t so
    the preview can ask what happens BETWEEN keyframes, which is the only way
    to measure what a decimated bake costs (Wall I).
    """
    if t <= track[0][0]:
        return track[0][1]
    if t >= track[-1][0]:
        return track[-1][1]
    lo, hi = 0, len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    f0, v0 = track[lo]
    f1, v1 = track[hi]
    u = 0.0 if f1 == f0 else (t - f0) / (f1 - f0)
    if scalar:
        return v0 + (v1 - v0) * u
    return [v0[0] + (v1[0] - v0[0]) * u, v0[1] + (v1[1] - v0[1]) * u]


def transform_layer(layer: PreviewLayer, bake_layer: dict, t: float) -> list[Poly]:
    """The layer's world polygons at (possibly fractional) frame t.

    This is AE's transform written out, from the JSON alone.
    """
    kf = bake_layer["keyframes"]
    px, py = _sample(kf["position"], t, scalar=False)
    theta = math.radians(_sample(kf["rotation"], t, scalar=True))
    ax, ay = layer.anchor
    c, s = math.cos(theta), math.sin(theta)
    out = []
    for part in layer.parts:
        out.append([
            (px + (x - ax) * c - (y - ay) * s,
             py + (x - ax) * s + (y - ay) * c)
            for x, y in part
        ])
    return out


def index_bake(bake: dict) -> dict[int, dict]:
    """Keyed by id, never by name -- AE allows duplicate layer names, and this
    dict comprehension would silently drop one of them."""
    return {lay["id"]: lay for lay in bake["layers"]}


# --------------------------------------------------------------------------
# Rasterising
# --------------------------------------------------------------------------

def render_frame(bake: dict, layers: list[PreviewLayer], scene: PreviewScene,
                 t: float, scale: float = 0.5, mask_only: bool = False):
    """One frame, drawn the way AE would draw it.

    `mask_only` gives a 1-bit silhouette, which is what the fault measurements
    compare -- "how many pixels move" is a better answer to *would an artist
    see this* than any vertex distance.
    """
    w = max(1, int(round(scene.width * scale)))
    h = max(1, int(round(scene.height * scale)))
    by_id = index_bake(bake)

    img = Image.new("1" if mask_only else "RGB", (w, h),
                    0 if mask_only else scene.background)
    d = ImageDraw.Draw(img)

    if not mask_only:
        for (x1, y1), (x2, y2) in scene.statics:
            d.line([(x1 * scale, y1 * scale), (x2 * scale, y2 * scale)],
                   fill=(90, 96, 104), width=max(1, int(round(3 * scale))))

    for i, layer in enumerate(layers):
        bl = by_id.get(layer.id)
        if bl is None:
            continue
        fill = 1 if mask_only else (layer.color or PALETTE[i % len(PALETTE)])
        for part in transform_layer(layer, bl, t):
            if len(part) < 3:
                continue
            d.polygon([(x * scale, y * scale) for x, y in part], fill=fill)
    return img


def render_gif(path: str, bake: dict, layers: list[PreviewLayer],
               scene: PreviewScene, scale: float = 0.4,
               frames: list[float] | None = None) -> str:
    """Animated preview. Pillow only -- no ffmpeg, and byte-deterministic."""
    fps = bake["comp"]["fps"]
    if frames is None:
        frames = [float(f) for f in range(bake["comp"]["duration_frames"] + 1)]
    imgs = [render_frame(bake, layers, scene, t, scale).convert(
        "P", palette=Image.ADAPTIVE, colors=64) for t in frames]
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=int(round(1000.0 / fps)), loop=0, optimize=False)
    return path


def contact_sheet(bake: dict, layers: list[PreviewLayer], scene: PreviewScene,
                  ts: list[float], scale: float = 0.22, cols: int = 4):
    tiles = [render_frame(bake, layers, scene, t, scale) for t in ts]
    tw, th = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (12, 13, 15))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % cols) * tw, (i // cols) * th))
    return sheet


# --------------------------------------------------------------------------
# Comparing frames
# --------------------------------------------------------------------------

def mask_disagreement(a: Image.Image, b: Image.Image) -> float:
    """Fraction of the drawn silhouette that the two frames disagree on.

    Symmetric difference over union, so it is 0 for identical frames and 1 for
    two that share no pixel. This is the "would an artist see it" metric; a
    vertex distance in px is not, because a 3 px error on a 400 px shape and a
    3 px error on a 6 px shape are not the same event.
    """
    import numpy as np
    va = np.asarray(a.convert("L")) != 0
    vb = np.asarray(b.convert("L")) != 0
    union = int((va | vb).sum())
    if union == 0:
        return 0.0
    return 1.0 - int((va & vb).sum()) / union


def layers_from_scene(scene) -> tuple[list[PreviewLayer], PreviewScene]:
    """Build the preview's inputs from a sim.Scene of PolyBody specs.

    Phase B replaces this with a read of the real comp; the shape of what it
    returns is the point.
    """
    layers = [PreviewLayer(i + 1, b.name, b.parts, b.anchor)
              for i, b in enumerate(scene.bodies)]
    return layers, PreviewScene(scene.width, scene.height, list(scene.statics))

"""
Phase A spine: scene -> pymunk world -> fixed-step sim -> per-frame sample
-> AE keyframe JSON.

COORDINATE CONVENTION
---------------------
We simulate directly in an AE-like frame: x right, y DOWN, origin top-left,
gravity pointing +y. Chipmunk does not care which way "down" is, and this
choice buys us something real: in a y-down frame a mathematically positive
(CCW) angle appears CLOCKWISE on screen, which is exactly AE's rotation sense.
So AE degrees = degrees(body.angle), with no sign flip anywhere.

UNITS
-----
The solver works in metres. `pixels_per_meter` is the conversion at the
boundary. It is a real tuning knob, not bookkeeping -- see Wall D in
ae-physics-sim-plan.md and the probe in a1_primitives.py.

LAYER SPACE vs BODY SPACE  (A2)
-------------------------------
AE describes a layer as: content laid out in layer space, an anchor point
somewhere in that space, and a Position saying where the anchor sits in the
comp. The solver describes a body as: a centre of mass with content hung off
it. Those are different origins, and converting between them is Wall E.

    into the solver:  com_world = position + R(theta) * (com_layer - anchor)
    out to AE:        position  = com_world + R(theta) * (anchor - com_layer)

They are exact inverses, which is what makes the A2 round-trip check possible.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import pymunk

import geom

SCHEMA = "ae-physics-bake/1"


# --------------------------------------------------------------------------
# Scene description  (the input Phase B will hand us from real AE layers)
# --------------------------------------------------------------------------

@dataclass
class Body:
    """A primitive layer: a box or a circle, positioned by its COM.

    A1's shape. `anchor_offset` is the anchor expressed in the body's local
    frame relative to the COM; (0,0) puts the anchor on the COM.
    """
    name: str
    kind: str                      # "box" | "circle"
    size: tuple[float, float]      # box: (w,h);  circle: (radius, _)
    position: tuple[float, float]  # initial COM in comp px
    angle_deg: float = 0.0
    velocity: tuple[float, float] = (0.0, 0.0)   # px/s
    angular_velocity_deg: float = 0.0            # deg/s
    density: float = 1.0
    friction: float = 0.6
    elasticity: float = 0.2
    anchor_offset: tuple[float, float] = (0.0, 0.0)


@dataclass
class PolyBody:
    """A layer described the way AE describes one.

    `parts` are convex polygons in LAYER space (px) -- one for a convex
    silhouette, several for a concave one once A3's decomposition exists.
    `anchor` is the layer's anchor point in that same space, and `position`
    says where in the comp that anchor sits. The COM is DERIVED, never given.
    """
    name: str
    parts: list[list[tuple[float, float]]]
    anchor: tuple[float, float] = (0.0, 0.0)
    position: tuple[float, float] = (0.0, 0.0)
    angle_deg: float = 0.0
    velocity: tuple[float, float] = (0.0, 0.0)
    angular_velocity_deg: float = 0.0
    density: float = 1.0
    friction: float = 0.6
    elasticity: float = 0.2


@dataclass
class Handle:
    """What build_space hands back: the spec, the solver body, and the
    layer-space facts the bake needs."""
    spec: object
    body: pymunk.Body
    anchor_offset: tuple[float, float]        # (anchor - com), layer px
    parts_local: list[list[tuple[float, float]]]   # verts about the COM, px


@dataclass
class Scene:
    width: int = 1920
    height: int = 1080
    fps: float = 24.0
    duration_frames: int = 120
    gravity_m_s2: float = 9.8
    pixels_per_meter: float = 100.0
    substeps: int = 8
    bodies: list[object] = field(default_factory=list)
    # Static floor/walls as segments in comp px: ((x1,y1),(x2,y2))
    statics: list[tuple[tuple[float, float], tuple[float, float]]] = field(
        default_factory=list
    )
    static_friction: float = 0.9
    static_elasticity: float = 0.2


# --------------------------------------------------------------------------
# World construction
# --------------------------------------------------------------------------

def _build_primitive(spec: Body, ppm: float) -> Handle:
    if spec.kind == "box":
        w, h = spec.size[0] / ppm, spec.size[1] / ppm
        mass = spec.density * w * h
        body = pymunk.Body(mass, pymunk.moment_for_box(mass, (w, h)))
        shapes = [pymunk.Poly.create_box(body, (w, h))]
    elif spec.kind == "circle":
        r = spec.size[0] / ppm
        mass = spec.density * math.pi * r * r
        body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0.0, r))
        shapes = [pymunk.Circle(body, r)]
    else:
        raise ValueError(f"unknown body kind: {spec.kind!r}")

    body.position = (spec.position[0] / ppm, spec.position[1] / ppm)
    body.angle = math.radians(spec.angle_deg)
    body.velocity = (spec.velocity[0] / ppm, spec.velocity[1] / ppm)
    body.angular_velocity = math.radians(spec.angular_velocity_deg)
    for s in shapes:
        s.friction = spec.friction
        s.elasticity = spec.elasticity
    return Handle(spec, body, spec.anchor_offset, []), shapes


def _build_poly(spec: PolyBody, ppm: float) -> Handle:
    parts_m = [[(x / ppm, y / ppm) for x, y in p] for p in spec.parts]
    mass, com_m, moment = geom.compound_mass_properties(parts_m, spec.density)

    body = pymunk.Body(mass, moment)
    # Shape vertices live relative to the COM, because that is where the
    # solver's body origin is.
    local_m = [
        geom.ensure_ccw([(x - com_m[0], y - com_m[1]) for x, y in p])
        for p in parts_m
    ]
    shapes = [pymunk.Poly(body, p) for p in local_m]

    theta = math.radians(spec.angle_deg)
    anchor_m = (spec.anchor[0] / ppm, spec.anchor[1] / ppm)
    # com_world = position + R(theta) * (com_layer - anchor)
    d = geom.rotate((com_m[0] - anchor_m[0], com_m[1] - anchor_m[1]), theta)
    body.position = (spec.position[0] / ppm + d[0],
                     spec.position[1] / ppm + d[1])
    body.angle = theta
    body.velocity = (spec.velocity[0] / ppm, spec.velocity[1] / ppm)
    body.angular_velocity = math.radians(spec.angular_velocity_deg)
    for s in shapes:
        s.friction = spec.friction
        s.elasticity = spec.elasticity

    anchor_offset = ((anchor_m[0] - com_m[0]) * ppm,
                     (anchor_m[1] - com_m[1]) * ppm)
    parts_local = [[(x * ppm, y * ppm) for x, y in p] for p in local_m]
    return Handle(spec, body, anchor_offset, parts_local), shapes


def build_space(scene: Scene):
    """Returns (space, [Handle]) in scene order.

    Order matters: we never iterate a set or dict to touch the solver, because
    that is a classic source of run-to-run drift (Wall H).
    """
    ppm = scene.pixels_per_meter
    space = pymunk.Space()
    space.gravity = (0.0, scene.gravity_m_s2)   # +y is down, in m/s^2
    space.iterations = 20

    for (x1, y1), (x2, y2) in scene.statics:
        seg = pymunk.Segment(
            space.static_body, (x1 / ppm, y1 / ppm), (x2 / ppm, y2 / ppm),
            0.5 / ppm,
        )
        seg.friction = scene.static_friction
        seg.elasticity = scene.static_elasticity
        space.add(seg)

    handles = []
    for spec in scene.bodies:
        if isinstance(spec, PolyBody):
            handle, shapes = _build_poly(spec, ppm)
        else:
            handle, shapes = _build_primitive(spec, ppm)
        space.add(handle.body, *shapes)
        handles.append(handle)

    return space, handles


# --------------------------------------------------------------------------
# Stepping and sampling
# --------------------------------------------------------------------------

def _unwrap(prev_deg: float | None, raw_deg: float) -> float:
    """Accumulate rotation so a double spin bakes as 720, not 0 (Wall F)."""
    if prev_deg is None:
        return raw_deg
    delta = (raw_deg - prev_deg + 180.0) % 360.0 - 180.0
    return prev_deg + delta


def _anchor_world(body, anchor_offset_px, ppm):
    """AE Position is where the ANCHOR sits in comp space, not the COM.

        position = com_world + R(theta) * (anchor_local - com_local)

    anchor_offset is already (anchor_local - com_local). Same rotation matrix
    as the y-up case -- we are relabelling an axis, not mirroring. (Wall E)
    """
    cx, cy = body.position[0] * ppm, body.position[1] * ppm
    ax, ay = geom.rotate(anchor_offset_px, body.angle)
    return (cx + ax, cy + ay)


def _run(scene: Scene, trace: bool):
    ppm = scene.pixels_per_meter
    space, handles = build_space(scene)
    dt = 1.0 / (scene.fps * scene.substeps)

    tracks = [{"position": [], "rotation": []} for _ in handles]
    traces: list[list] = [[] for _ in handles]
    last_deg: list[float | None] = [None] * len(handles)

    for frame in range(scene.duration_frames + 1):
        if frame > 0:
            for _ in range(scene.substeps):
                space.step(dt)
        for i, h in enumerate(handles):
            px, py = _anchor_world(h.body, h.anchor_offset, ppm)
            deg = _unwrap(last_deg[i], math.degrees(h.body.angle))
            last_deg[i] = deg
            tracks[i]["position"].append([frame, [round(px, 6), round(py, 6)]])
            tracks[i]["rotation"].append([frame, round(deg, 6)])
            if trace:
                # Ground truth straight from the solver, in comp px.
                traces[i].append([
                    [tuple(v * ppm for v in h.body.local_to_world(
                        (x / ppm, y / ppm)))
                     for x, y in part]
                    for part in h.parts_local
                ])

    return handles, tracks, (traces if trace else None)


def simulate(scene: Scene):
    """Fixed-dt sim, sampled once per frame.

    Frames land exactly on substep boundaries, so nothing is ever interpolated
    -- one less source of run-to-run difference (Walls G and H).
    """
    handles, tracks, _ = _run(scene, trace=False)
    return handles, tracks


def simulate_traced(scene: Scene):
    """As simulate(), plus the solver's own world-space vertices per frame.

    This is the ground truth the baked keyframes get checked against.
    """
    return _run(scene, trace=True)


def replay_from_keyframes(handle: Handle, track: dict, frame: int):
    """Rebuild a layer's world vertices the way AE would, from keyframes alone.

    AE places the anchor at Position and rotates layer content about it, so:
        world = position + R(theta) * (vertex_layer - anchor)
    Our `parts_local` are already relative to the COM, and anchor_offset is
    (anchor - com), so (vertex_layer - anchor) == parts_local - anchor_offset.
    """
    pos = track["position"][frame][1]
    theta = math.radians(track["rotation"][frame][1])
    ax, ay = handle.anchor_offset
    out = []
    for part in handle.parts_local:
        out.append([
            tuple(p + q for p, q in
                  zip(pos, geom.rotate((x - ax, y - ay), theta)))
            for x, y in part
        ])
    return out


def bake(scene: Scene) -> dict:
    handles, tracks = simulate(scene)
    ppm = scene.pixels_per_meter
    return {
        "schema": SCHEMA,
        "comp": {
            "width": scene.width, "height": scene.height,
            "fps": scene.fps, "duration_frames": scene.duration_frames,
        },
        "sim": {
            "pixels_per_meter": ppm,
            "gravity_px_s2": [0.0, scene.gravity_m_s2 * ppm],
            "substeps": scene.substeps,
            "engine": "pymunk",
        },
        "layers": [
            {
                "name": h.spec.name,
                "index": i + 1,
                "anchor_offset": [round(v, 6) for v in h.anchor_offset],
                "keyframes": tracks[i],
            }
            for i, h in enumerate(handles)
        ],
    }


def bake_json(scene: Scene) -> str:
    return json.dumps(bake(scene), indent=2, sort_keys=True)

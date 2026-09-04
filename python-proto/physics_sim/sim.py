"""
Phase A1 spine: scene -> pymunk world -> fixed-step sim -> per-frame sample
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
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import pymunk

SCHEMA = "ae-physics-bake/1"


# --------------------------------------------------------------------------
# Scene description  (the input Phase B will hand us from real AE layers)
# --------------------------------------------------------------------------

@dataclass
class Body:
    """One dynamic layer.

    All lengths in comp pixels. `anchor_offset` is the layer's anchor point
    expressed in the BODY'S LOCAL FRAME, relative to its centre of mass --
    (0,0) means the anchor sits on the COM, which is the easy case A1 uses.
    A2 is where this gets exercised properly.
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
class Scene:
    width: int = 1920
    height: int = 1080
    fps: float = 24.0
    duration_frames: int = 120
    gravity_m_s2: float = 9.8
    pixels_per_meter: float = 100.0
    substeps: int = 8
    bodies: list[Body] = field(default_factory=list)
    # Static floor/walls as segments in comp px: ((x1,y1),(x2,y2))
    statics: list[tuple[tuple[float, float], tuple[float, float]]] = field(
        default_factory=list
    )
    static_friction: float = 0.9
    static_elasticity: float = 0.2


# --------------------------------------------------------------------------
# World construction
# --------------------------------------------------------------------------

def build_space(scene: Scene):
    """Returns (space, [(Body, pymunk.Body, com_local_px)]) in scene order.

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
        if spec.kind == "box":
            w, h = spec.size[0] / ppm, spec.size[1] / ppm
            area = w * h
            mass = spec.density * area
            moment = pymunk.moment_for_box(mass, (w, h))
            body = pymunk.Body(mass, moment)
            shape = pymunk.Poly.create_box(body, (w, h))
        elif spec.kind == "circle":
            r = spec.size[0] / ppm
            mass = spec.density * math.pi * r * r
            moment = pymunk.moment_for_circle(mass, 0.0, r)
            body = pymunk.Body(mass, moment)
            shape = pymunk.Circle(body, r)
        else:
            raise ValueError(f"unknown body kind: {spec.kind!r}")

        body.position = (spec.position[0] / ppm, spec.position[1] / ppm)
        body.angle = math.radians(spec.angle_deg)
        body.velocity = (spec.velocity[0] / ppm, spec.velocity[1] / ppm)
        body.angular_velocity = math.radians(spec.angular_velocity_deg)
        shape.friction = spec.friction
        shape.elasticity = spec.elasticity
        space.add(body, shape)
        handles.append((spec, body))

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
    ax, ay = anchor_offset_px
    c, s = math.cos(body.angle), math.sin(body.angle)
    return (cx + c * ax - s * ay, cy + s * ax + c * ay)


def simulate(scene: Scene):
    """Fixed-dt sim, sampled once per frame.

    Frames land exactly on substep boundaries, so nothing is ever interpolated
    -- one less source of run-to-run difference (Walls G and H).
    """
    ppm = scene.pixels_per_meter
    space, handles = build_space(scene)
    dt = 1.0 / (scene.fps * scene.substeps)

    tracks = [{"position": [], "rotation": []} for _ in handles]
    last_deg: list[float | None] = [None] * len(handles)

    for frame in range(scene.duration_frames + 1):
        if frame > 0:
            for _ in range(scene.substeps):
                space.step(dt)
        for i, (spec, body) in enumerate(handles):
            px, py = _anchor_world(body, spec.anchor_offset, ppm)
            deg = _unwrap(last_deg[i], math.degrees(body.angle))
            last_deg[i] = deg
            tracks[i]["position"].append([frame, [round(px, 6), round(py, 6)]])
            tracks[i]["rotation"].append([frame, round(deg, 6)])

    return handles, tracks


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
                "name": spec.name,
                "index": i + 1,
                "anchor_offset": list(spec.anchor_offset),
                "keyframes": tracks[i],
            }
            for i, (spec, _body) in enumerate(handles)
        ],
    }


def bake_json(scene: Scene) -> str:
    return json.dumps(bake(scene), indent=2, sort_keys=True)

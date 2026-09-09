"""
C1.0 -- `ae-physics-scene/2` and `ae-physics-bake/3`, checked offline.

WHAT THIS STEP IS FOR
---------------------
C1 ships an application, and an application writes documents that outlive it.
The plan's Tier 0 is the list of features that RESHAPE these documents rather
than extend them -- joints, zones, kinematic layers, collision groups, N outputs
per layer, per-frame velocity, contact events, a bake target other than in
place. None is being implemented now; each one, retrofitted later, invalidates
every document written before it. So the SHAPE ships first, with the arrays
empty, and the implementations become additive changes.

That claim -- "shape only, nothing behaves differently" -- is the thing to
check, and it splits into three:

    1. NOTHING OLD BREAKS.   A /1 document still validates, still loads, and
                             still produces the same simulation.
    2. THE SLOTS ARE REAL.   Each one is present, and each one rejects a
                             malformed value with a sentence rather than
                             absorbing it.
    3. AN EMPTY SLOT IS NOT A LIE.  Filling a slot that has no implementation
                             behind it is REFUSED, not silently ignored.

(3) is the one worth arguing for. The tempting alternative -- accept the field
and do nothing with it -- is how a slot becomes a bug: a layer marked kinematic
and simulated as dynamic knocks over exactly the stack it was authored to knock
over, only wrongly, and the document says it was asked for. `load_scene` names
the phase that will implement it instead.

WHAT IT CANNOT CHECK
--------------------
The jsx emitter, like all of B1's script half, needs AE. Section 5 does the one
thing that can be done offline: it reads `b1_read_shapes.jsx` as text and
compares the field names it emits against the ones `scene_io` fills in, which
catches the drift class that the plan already worries about for `compose_point`
-- two implementations of one contract, in two languages, edited apart.

Run:  python c1_schema.py
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys

import scene_io
import sim
from scene_io import SceneError, load_scene

FIXTURE = "b1_fixture_scene.json"
AE_EXPORT = "b1_ae_export.json"
STORED_BAKE = "b3_bake.json"          # a /2 bake, made 2026-09-04
READER_JSX = "b1_read_shapes.jsx"
APPLIER_JSX = "b2_apply_bake.jsx"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _break(doc, fn):
    d = copy.deepcopy(doc)
    fn(d)
    return d


# --------------------------------------------------------------------------
# 1. A /1 document is still a document
# --------------------------------------------------------------------------

def test_v1_still_reads():
    print("\n1. /1 STILL READS  (the evidence behind B1's 22 checks is /1, and "
          "no run in AE has ever produced a /2)")
    v1 = load(FIXTURE)
    check("the /1 fixture is untouched on disk",
          v1["schema"] == scene_io.SCENE_SCHEMA_V1,
          f"{FIXTURE} still says {v1['schema']!r} -- it was not migrated, it "
          "is read")
    check("a /1 document validates clean",
          scene_io.validate(v1) == [],
          "no problems: every Tier 0 check is satisfied by the defaults "
          "`upgrade` fills in, so none of them can fail on a document written "
          "before the slots existed")

    before = copy.deepcopy(v1)
    up = scene_io.upgrade(v1)
    check("upgrade does not touch the caller's document",
          v1 == before and up is not v1,
          "the /1 dict is byte-equal after the call; a shared-reference bug "
          "here would rewrite a document the caller still believes is /1")
    check("upgrade is idempotent",
          scene_io.upgrade(up) == up,
          "upgrading a /2 document returns it unchanged, so the number of "
          "times a document passes through the reader cannot matter")

    # The claim under test: /2 is shape, so the physics must be bit-identical.
    b1 = sim.bake_json(*load_scene(load(FIXTURE))[:1],
                       ids=[m["id"] for m in load_scene(load(FIXTURE))[1]["layers"]])
    s2, m2 = load_scene(up)
    b2 = sim.bake_json(s2, [m["id"] for m in m2["layers"]])
    check("a /1 document and its upgrade simulate identically",
          b1 == b2,
          f"both bakes are {len(b1)} bytes and compare equal -- the slots "
          "reach the document and stop there, never the solver")
    return v1, up


# --------------------------------------------------------------------------
# 2. Every slot exists
# --------------------------------------------------------------------------

SCENE_SLOTS = {
    "joints": "relations between bodies -- top-level, because a joint is not "
              "a property of one layer",
    "zones": "wind, magnets, explosions -- same argument",
    "output": "where the bake lands: in place, or a new comp",
}
LAYER_SLOTS = {
    "motion": "dynamic | static | kinematic",
    "input_keyframes": "a kinematic layer's own AE animation",
    "collision_group": "which group this body is in",
    "collide_with": "the 16-bit mask of groups it collides with",
    "outputs": "how many AE layers this input becomes (island split, fracture)",
}
BAKE_SLOTS = {
    "contacts": "collision events -- squash, sound sync, triggers, without "
                "re-simulating",
    "output": "echoed from the scene, so the bake is self-describing",
    "recorded": "which of the above were actually written down",
}
BAKE_LAYER_SLOTS = {
    "channels": "per-frame velocity and angular velocity",
    "extra_outputs": "outputs 2..N; the first stays in `keyframes`",
}


def test_slots_present(up):
    print("\n2. THE SEVEN SLOTS  (empty, and present)")
    missing = [k for k in SCENE_SLOTS if k not in up]
    check("every top-level scene slot is present",
          not missing,
          ", ".join(f"{k} ({v})" for k, v in SCENE_SLOTS.items())
          if not missing else f"missing {missing}")
    lay = up["layers"][0]
    missing = [k for k in LAYER_SLOTS if k not in lay]
    check("every per-layer scene slot is present",
          not missing,
          ", ".join(LAYER_SLOTS) if not missing else f"missing {missing}")

    scene, meta = load_scene(up)
    bake = sim.bake(scene, [m["id"] for m in meta["layers"]], meta["output"])
    check("the bake is /3",
          bake["schema"] == "ae-physics-bake/3",
          f"{bake['schema']}, up from ae-physics-bake/2")
    missing = [k for k in BAKE_SLOTS if k not in bake] + \
              [k for k in BAKE_LAYER_SLOTS if k not in bake["layers"][0]]
    check("every bake slot is present",
          not missing,
          ", ".join(list(BAKE_SLOTS) + list(BAKE_LAYER_SLOTS))
          if not missing else f"missing {missing}")

    # The distinction that makes an empty array readable.
    check("an empty slot says WHICH kind of empty it is",
          bake["recorded"] == {"channels": False, "contacts": False}
          and bake["contacts"] == [],
          "`contacts: []` with `recorded.contacts: false` means nobody was "
          "writing them down; the same array with `true` would mean nothing "
          "touched anything. A squash-and-stretch consumer that cannot tell "
          "those apart silently produces no squash on a scene full of impacts")
    return bake


# --------------------------------------------------------------------------
# 3. Every slot rejects a malformed value
# --------------------------------------------------------------------------

def test_slot_validation(up):
    print("\n3. MALFORMED SLOTS  (a slot that absorbs anything is not a "
          "contract)")
    check("the /2 document is itself clean",
          scene_io.validate(up) == [],
          "so the failures below are the injected ones")

    # Each case carries the sentence it is SUPPOSED to provoke. Without that,
    # a fault can be "caught" by an unrelated complaint and the table still
    # reads all-yes -- which is what happened on the first run here: joints
    # written against ids 1 and 2 were rejected for naming layers that are not
    # in this fixture (its ids are 3, 4 and 7), so the duplicate-id check was
    # never reached and looked verified.
    ids = [lay["id"] for lay in up["layers"]]
    a, b = ids[0], ids[1]

    def joint(**kw):
        j = {"id": 1, "type": "pivot", "bodies": [a, b],
             "anchors": [[0.0, 0.0], [1.0, 1.0]]}
        j.update(kw)
        return j

    cases = [
        ("joints is not a list",
         lambda d: d.__setitem__("joints", {}),
         "'joints' must be a list"),
        ("a joint of an unknown type",
         lambda d: d.__setitem__("joints", [joint(type="rubber")]),
         "'type' is 'rubber'"),
        ("a joint naming a layer that is not in the scene",
         lambda d: d.__setitem__("joints", [joint(bodies=[a, 999])]),
         "layer id 999, which is not in this scene"),
        ("a joint with one anchor",
         lambda d: d.__setitem__("joints", [joint(anchors=[[0.0, 0.0]])]),
         "'anchors' must be two points"),
        ("two joints with the same id",
         lambda d: d.__setitem__("joints", [joint(), joint(type="weld")]),
         "duplicate joint id 1"),
        ("a zone of an unknown type",
         lambda d: d.__setitem__("zones", [
             {"id": 1, "type": "gravity well", "region": None}]),
         "'type' is 'gravity well'"),
        ("a zone with half a frame range",
         lambda d: d.__setitem__("zones", [
             {"id": 1, "type": "wind", "region": None, "frames": [10]}]),
         "'frames' must be two integers or null"),
        ("motion spelled wrong",
         lambda d: d["layers"][0].__setitem__("motion", "fixed"),
         "'motion' is 'fixed'"),
        ("motion and `static` disagreeing",
         lambda d: d["layers"][0].__setitem__("motion", "static"),
         "they must agree"),
        ("kinematic with no input keyframes",
         lambda d: (d["layers"][0].__setitem__("motion", "kinematic"),
                    d["layers"][0].__setitem__("input_keyframes", None)),
         "it cannot move"),
        ("an input track that is not [frame, value] pairs",
         lambda d: d["layers"][0].__setitem__(
             "input_keyframes", {"position": [[0, 1, 2]], "rotation": []}),
         "is not a [frame, value] pair"),
        ("an input track keyed on a fractional frame",
         lambda d: d["layers"][0].__setitem__(
             "input_keyframes", {"position": [[0.5, [1, 2]]], "rotation": []}),
         "frame 0.5 is not an integer"),
        ("a collide_with mask wider than 16 bits",
         lambda d: d["layers"][0].__setitem__("collide_with", 1 << 20),
         "must be a 16-bit mask"),
        ("a negative collision group",
         lambda d: d["layers"][0].__setitem__("collision_group", -1),
         "'collision_group' must be a non-negative integer"),
        ("a layer that produces zero output layers",
         lambda d: d["layers"][0].__setitem__("outputs", 0),
         "'outputs' must be at least 1"),
        ("an unknown bake target",
         lambda d: d.__setitem__("output", {"target": "clipboard"}),
         "output.target is 'clipboard'"),
        ("target new_comp with no comp named",
         lambda d: d.__setitem__(
             "output", {"target": "new_comp", "comp_name": ""}),
         "'comp_name' is empty"),
    ]
    print("      injected fault                                  caught  "
          "message")
    print("      -------------------------------------------     ------  "
          "-------")
    missed = []
    for name, fn, want in cases:
        probs = scene_io.validate(_break(up, fn))
        hit = [q for q in probs if want in q]
        if not hit:
            missed.append(name)
        first = hit[0] if hit else (probs[0] if probs else "(nothing)")
        print(f"      {name:<45}   {'yes' if hit else 'NO':>4}   "
              f"{first[:78]}")
    check("every malformed slot is rejected for the RIGHT reason",
          not missed,
          f"{len(cases)}/{len(cases)} caught, each matched against the "
          "sentence it was supposed to provoke rather than against 'some "
          "complaint was raised'"
          if not missed else f"missed, or caught by something else: {missed}")


# --------------------------------------------------------------------------
# 4. A filled slot is refused, not ignored
# --------------------------------------------------------------------------

def test_unimplemented_is_refused(up):
    print("\n4. A SLOT IS NOT A PROMISE  (the failure mode this section exists "
          "to prevent is silence)")
    cases = [
        ("a kinematic layer",
         lambda d: (d["layers"][0].__setitem__("motion", "kinematic"),
                    d["layers"][0].__setitem__(
                        "input_keyframes",
                        {"position": [[0, [10.0, 20.0]]], "rotation": []})),
         "would be simulated as dynamic -- it would knock over exactly the "
         "stack it was authored to knock over, only wrongly"),
        ("a joint",
         lambda d: d.__setitem__("joints", [
             {"id": 1, "type": "pivot", "bodies": [1, None],
              "anchors": [[0.0, 0.0], [0.0, 0.0]]}]),
         "the bodies would simply fall apart, with the document saying they "
         "were joined"),
        ("a zone",
         lambda d: d.__setitem__("zones", [
             {"id": 1, "type": "wind", "region": None,
              "params": {"force": [100.0, 0.0]}}]),
         "no wind, and nothing to say so"),
        ("a layer asking for four output layers",
         lambda d: d["layers"][0].__setitem__("outputs", 4),
         "one layer would come back, and three shards would be missing"),
        ("a collision mask",
         lambda d: d["layers"][0].__setitem__("collide_with", 0b1),
         "everything would collide with everything, which is the opposite of "
         "what a mask was set to say"),
        ("a bake target of new_comp",
         lambda d: d.__setitem__(
             "output", {"target": "new_comp", "comp_name": "sim baked"}),
         "the source comp would be overwritten -- the exact outcome the "
         "setting exists to avoid"),
    ]
    print("      slot filled                          refused  if it were "
          "ignored instead")
    print("      ----------------------------------   -------  "
          "--------------------------")
    missed = []
    for name, fn, harm in cases:
        try:
            load_scene(_break(up, fn))
            refused = False
        except SceneError:
            refused = True
        if not refused:
            missed.append(name)
        print(f"      {name:<34}   {'yes' if refused else 'NO':>5}    "
              f"{harm[:60]}")
    check("filling an unimplemented slot is refused by name",
          not missed,
          f"{len(cases)}/{len(cases)} raise SceneError naming the slot and the "
          "phase that implements it, rather than loading and quietly doing "
          "something else"
          if not missed else f"silently accepted: {missed}")

    # And the refusal has to be legible, not just present.
    try:
        load_scene(_break(up, cases[0][1]))
        msg = ""
    except SceneError as e:
        msg = str(e)
    check("the refusal says what is not implemented",
          "kinematic" in msg and "does not read it yet" in msg,
          repr(msg.splitlines()[-1].strip()[:96]))


# --------------------------------------------------------------------------
# 5. The jsx emitter and the Python reader agree on the field names
# --------------------------------------------------------------------------

def test_emitter_agrees():
    print("\n5. THE SCRIPT AND THE READER  (two implementations of one "
          "contract, in two languages)")
    if not os.path.exists(READER_JSX):
        print(f"      {READER_JSX} not present -- skipping")
        return
    with open(READER_JSX, encoding="utf-8") as fh:
        src = fh.read()

    version = re.search(r'var SCHEMA = "([^"]+)"', src)
    check("the script emits the version the reader prefers",
          version and version.group(1) == scene_io.SCENE_SCHEMA,
          f"{READER_JSX} writes {version.group(1) if version else '?'!r}, "
          f"scene_io.SCENE_SCHEMA is {scene_io.SCENE_SCHEMA!r}")

    # Every key the serialiser writes, from the `"key":` literals in the file.
    emitted = set(re.findall(r'\\"([a-z_]+)\\":', src))
    want_scene = set(SCENE_SLOTS) | set(LAYER_SLOTS)
    absent = sorted(want_scene - emitted)
    check("the script writes every slot the reader fills in",
          not absent,
          f"{len(want_scene)} slots, all emitted: "
          + ", ".join(sorted(want_scene))
          if not absent else f"the script never writes {absent} -- a document "
          "from AE would rely on the upgrade path forever")

    with open(APPLIER_JSX, encoding="utf-8") as fh:
        ap = fh.read()
    accepted = re.search(r'var BAKE_SCHEMAS_OK = \[([^\]]+)\]', ap)
    accepted = set(re.findall(r'"([^"]+)"', accepted.group(1))) if accepted \
        else set()
    check("the applier accepts /3 and still accepts /2",
          accepted == {"ae-physics-bake/2", "ae-physics-bake/3"},
          f"{APPLIER_JSX} applies {sorted(accepted)} -- /3 changed nothing "
          "about what a keyframe is, so a bake made before today still "
          "applies")
    check("the applier refuses a bake target it cannot honour",
          'bake.output.target !== "in_place"' in ap,
          "a bake asking for a new comp, applied in place, would overwrite "
          "the layers the setting exists to protect")


# --------------------------------------------------------------------------
# 6. The real export, and a stored /2 bake
# --------------------------------------------------------------------------

STRIP_BAKE = set(BAKE_SLOTS)
STRIP_LAYER = set(BAKE_LAYER_SLOTS)


def strip_v3(bake: dict) -> dict:
    """A /3 bake with the /3 additions removed -- i.e. the /2 it would have
    been. Used to compare against a bake made before any of this existed."""
    out = {k: v for k, v in bake.items() if k not in STRIP_BAKE}
    out["schema"] = "ae-physics-bake/2"
    out["layers"] = [{k: v for k, v in lay.items() if k not in STRIP_LAYER}
                     for lay in bake["layers"]]
    return out


def test_real_export():
    print("\n6. THE REAL EXPORT  (b1_read_shapes.jsx's /1 output, and the /2 "
          "bake made from it in September)")
    if not os.path.exists(AE_EXPORT) or not os.path.exists(STORED_BAKE):
        print("      the AE export or the stored bake is not present -- "
              "skipping")
        return
    doc = load(AE_EXPORT)
    check("the real /1 export still validates",
          scene_io.validate(doc) == [],
          f"{len(doc['layers'])} layers out of comp "
          f"{doc['comp'].get('name')!r}, read with no complaint")

    stored = load(STORED_BAKE)
    src = stored["source"]["settings"]
    cmd = [sys.executable, "b3_loop.py", AE_EXPORT,
           "--out", "c1_bake_recheck.json", "--preview", "c1_bake_recheck.png",
           "--gravity", str(src["gravity_m_s2"]),
           "--ppm", str(src["pixels_per_meter"]),
           "--substeps", str(src["substeps"]),
           "--frames", str(src["frames"]),
           "--friction", str(src["friction"]),
           "--elasticity", str(src["elasticity"])]
    for name in src.get("static", []):
        cmd += ["--static", name]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        check("b3_loop reproduces the September bake", False,
              f"b3_loop.py exited {proc.returncode}: "
              f"{proc.stderr.strip().splitlines()[-1] if proc.stderr else ''}")
        return
    fresh = load("c1_bake_recheck.json")

    check("the fresh bake is /3 and carries the target",
          fresh["schema"] == "ae-physics-bake/3"
          and fresh["output"] == {"target": "in_place", "comp_name": None},
          f"{fresh['schema']}, output {fresh['output']['target']!r} -- the "
          "scene's own answer, carried through rather than assumed by the "
          "applier")

    # `source` differs by construction: made_at is a timestamp, and the stored
    # bake predates the settings B3 later added. Physics is the comparison.
    a = json.dumps(strip_v3(fresh)["layers"], sort_keys=True)
    b = json.dumps(stored["layers"], sort_keys=True)
    keys = sum(len(l["keyframes"]["position"]) + len(l["keyframes"]["rotation"])
               for l in fresh["layers"])
    check("stripped of its /3 slots, the bake is the September bake",
          a == b,
          f"{keys} keyframes across {len(fresh['layers'])} layers, identical "
          f"to {STORED_BAKE} ({len(b)} bytes) -- so /3 added fields and "
          "changed no number that reaches AE")
    for junk in ("c1_bake_recheck.json", "c1_bake_recheck.png"):
        if os.path.exists(junk):
            os.remove(junk)


# --------------------------------------------------------------------------

def main():
    print("C1.0 -- ae-physics-scene/2 and ae-physics-bake/3")
    print("=" * 74)
    v1, up = test_v1_still_reads()
    test_slots_present(up)
    test_slot_validation(up)
    test_unimplemented_is_refused(up)
    test_emitter_agrees()
    test_real_export()

    print("\n" + "=" * 74)
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} checks pass")
    for n in bad:
        print(f"  FAILED: {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

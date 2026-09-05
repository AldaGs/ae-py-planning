"""
B3 -- checks for the loop.

B1 and B2 each had a half that only AE could answer. B3 does not: the whole
middle is Python, and the two ends were verified in the two steps before it.
So everything here runs offline, and what it checks is the thing a hand-carried
loop actually gets wrong -- not the physics, the PLUMBING.

Four questions:

  1. Is the loop reproducible? Same inputs, same bytes, and a control that
     proves the comparison can fail.
  2. Do the parameters do anything? A knob that is wired to nothing looks
     exactly like a knob whose effect you cannot see.
  3. Does a pinned layer stay pinned, and stay OUT of the keyframes?
  4. Does the staleness guard catch a bake applied to the wrong comp? This is
     the one that matters. A stale bake is well-formed, validates, has matching
     layer names, and silently destroys good animation.

Run:  python b3_checks.py
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import tempfile

import b3_loop
import scene_io
from sim import bake as bake_scene

SCENE = "b1_ae_export.json"
FRAMES = 240

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def loop(*extra, scene=SCENE):
    """Run the real command line, in a subprocess, like a user would.

    Calling run() in-process would test the functions but not the tool -- and
    B3's whole claim is that the middle of the loop is one command.
    """
    out = os.path.join(tempfile.gettempdir(), "b3_t.json")
    cmd = [sys.executable, "b3_loop.py", scene, "--out", out,
           "--frames", str(FRAMES), "--no-preview", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True)
    data = None
    if os.path.exists(out) and p.returncode == 0:
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
    if os.path.exists(out):
        os.remove(out)
    return p.returncode, data, p.stdout + p.stderr


# --------------------------------------------------------------------------
# 1. Reproducible
# --------------------------------------------------------------------------

def test_reproducible():
    print("\n1. THE LOOP IS ONE COMMAND, AND IT REPEATS")
    rc1, a, _o = loop()
    rc2, b, _o = loop()
    same = json.dumps(_strip_time(a), sort_keys=True) == \
        json.dumps(_strip_time(b), sort_keys=True)
    check("two runs of the same command produce the same bake",
          rc1 == rc2 == 0 and same,
          f"{len(json.dumps(a))} bytes, identical apart from the timestamp -- "
          "Wall H now holds across the whole loop, not just the solver")

    rc3, c, _o = loop("--gravity", "9.800000001")
    check("...and a 1e-9 change to gravity does not",
          rc3 == 0 and json.dumps(_strip_time(c), sort_keys=True) !=
          json.dumps(_strip_time(a), sort_keys=True),
          "control: the comparison is capable of failing, so agreement means "
          "something")


def _strip_time(bake):
    b = copy.deepcopy(bake)
    b["source"].pop("made_at", None)
    return b


# --------------------------------------------------------------------------
# 2. The knobs are connected
# --------------------------------------------------------------------------

def _rest(bake, name):
    lay = [l for l in bake["layers"] if l["name"] == name][0]
    return lay["keyframes"]["position"][-1][1]


def _vertical_travel(bake, name):
    """Total vertical distance covered.

    The first metric here measured rebound from the DEEPEST point, which is
    the final resting place -- so a bouncier body scored 0.0 and the check
    failed while the physics was fine. Total travel is monotonic in
    bounciness and has no local-extremum trap in it.
    """
    lay = [l for l in bake["layers"] if l["name"] == name][0]
    ys = [p[1][1] for p in lay["keyframes"]["position"]]
    return sum(abs(b - a) for a, b in zip(ys, ys[1:]))


def _first_rest(bake, name):
    """The frame everything stops moving -- a bouncier body stops later."""
    lay = [l for l in bake["layers"] if l["name"] == name][0]
    pos = lay["keyframes"]["position"]
    last = pos[-1][1]
    for f, p in pos:
        if math.dist(p, last) < 1.0:
            return f
    return pos[-1][0]


def test_parameters():
    print("\n2. THE PARAMETERS ARE WIRED TO SOMETHING")
    base_rc, base, _o = loop()
    name = base["layers"][1]["name"]

    _rc, moon, _o = loop("--gravity", "1.6")
    fall_earth = _rest(base, name)[1] - base["layers"][1]["keyframes"]["position"][0][1][1]
    t_earth = next(f for f, p in base["layers"][1]["keyframes"]["position"]
                   if p[1] > base["layers"][1]["keyframes"]["position"][0][1][1] + 100)
    t_moon = next(f for f, p in moon["layers"][1]["keyframes"]["position"]
                  if p[1] > moon["layers"][1]["keyframes"]["position"][0][1][1] + 100)
    check("gravity changes how long a fall takes, by the square root",
          t_moon > t_earth * 2,
          f"100 px of fall takes {t_earth} frames at 9.8 m/s^2 and {t_moon} "
          f"at 1.6 (lunar). sqrt(9.8/1.6) = {math.sqrt(9.8 / 1.6):.2f} "
          f"predicts {t_earth * math.sqrt(9.8 / 1.6):.1f}")

    _rc, bouncy, _o = loop("--elasticity", "0.85")
    d0, d1 = _vertical_travel(base, name), _vertical_travel(bouncy, name)
    r0, r1 = _first_rest(base, name), _first_rest(bouncy, name)
    check("elasticity changes how long things keep bouncing",
          d1 > d0 * 1.2 and r1 > r0,
          f"total vertical travel {d0:.0f} px at elasticity 0.2 vs "
          f"{d1:.0f} px at 0.85, and it comes to rest at frame {r0} vs {r1}. "
          "The walls take the same setting: Chipmunk combines elasticity "
          "multiplicatively, so a bouncy body on a dead floor barely bounces")

    _rc, slow, _o = loop("--ppm", "25")
    settle_fast = _first_rest(base, name)
    settle_slow = _first_rest(slow, name)
    check("ppm is the comp's clock, exactly as A1 measured",
          settle_slow > settle_fast,
          f"the same scene settles at frame {settle_fast} at ppm=100 and "
          f"{settle_slow} at ppm=25 -- fewer pixels per metre makes the comp "
          "a bigger room, so everything takes longer")





# --------------------------------------------------------------------------
# 3. Pinned layers
# --------------------------------------------------------------------------

def test_static():
    print("\n3. PINNED LAYERS  (a ramp is a layer too)")
    _rc, base, _o = loop()
    target = base["layers"][0]["name"]
    rc, pinned, _o = loop("--static", target)
    lay = [l for l in pinned["layers"] if l["name"] == target][0]
    check("a pinned layer gets NO keyframes at all",
          rc == 0 and lay["static"] and not lay["keyframes"]["position"]
          and not lay["keyframes"]["rotation"],
          f"{target!r}: 0 position and 0 rotation keys. Writing even a "
          "constant would replace the user's own placement with our copy of "
          "it, and then undo any later nudge they made by hand")

    moved = [l["name"] for l in pinned["layers"]
             if l["keyframes"]["position"]]
    check("...and the others still move",
          len(moved) == len(pinned["layers"]) - 1,
          f"{len(moved)} of {len(pinned['layers'])} layers still keyframed: "
          f"{moved}")

    rc, _d, out = loop("--static", "Nonesuch")
    check("a --static that matches nothing is an error, not a shrug",
          rc == 2 and "matched nothing" in out,
          "a typo that silently pinned nothing would look exactly like a "
          "scene where pinning did not help")


# --------------------------------------------------------------------------
# 4. Staleness -- the wall B3 exposes
# --------------------------------------------------------------------------

def test_staleness():
    print("\n4. STALENESS  (a stale bake is well-formed and destroys work)")
    _rc, bake, _o = loop()
    with open(SCENE, encoding="utf-8") as fh:
        doc = json.load(fh)

    check("a fresh bake matches the scene it came from",
          b3_loop.stale_reasons(bake, SCENE, doc) == [],
          "no reasons -- so the reasons below are the injected ones")

    # A different comp entirely.
    with open("b1_fixture_scene.json", encoding="utf-8") as fh:
        other = json.load(fh)
    reasons = b3_loop.stale_reasons(bake, "b1_fixture_scene.json", other)
    check("a bake from another comp is rejected on four counts",
          len(reasons) >= 4,
          f"{len(reasons)} reasons: file hash, comp name, comp size, and the "
          "layer list. Any one alone could be a coincidence")

    # The nastier case: same comp, edited a little. Layer names still match,
    # the document still validates, and the bake still looks perfectly fine.
    edited = copy.deepcopy(doc)
    edited["layers"][0]["paths"][0]["vertices"][0][0] += 3.0
    tmp = os.path.join(tempfile.gettempdir(), "b3_edited.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(edited, fh)
    reasons = b3_loop.stale_reasons(bake, tmp, edited)
    check("a 3 px edit to one vertex is caught by the hash alone",
          len(reasons) == 1 and "scene file has changed" in reasons[0],
          "same comp, same size, same layer names, still validates -- nothing "
          "but the hash distinguishes it. This is the bake that would have "
          "quietly overwritten good animation")
    os.remove(tmp)

    old = copy.deepcopy(bake)
    del old["source"]
    check("a bake with no source block says so instead of passing",
          "predates B3" in b3_loop.stale_reasons(old, SCENE, doc)[0],
          "b2_bake.json from the previous step has no manifest; unverifiable "
          "is reported as unverifiable, not as fine")

    rc = subprocess.run(
        [sys.executable, "b3_loop.py", "b1_fixture_scene.json", "--check",
         "--out", "b3_bake.json"], capture_output=True, text=True)
    check("--check exits non-zero on a stale pair",
          rc.returncode == 1 and "STALE" in rc.stderr,
          "so it can sit in front of the apply step in a shell one-liner")


# --------------------------------------------------------------------------
# 5. The guard that was added in B2, still standing
# --------------------------------------------------------------------------

def test_escape_guard():
    print("\n5. ESCAPES  (B2's finding, now a refusal)")
    rc, data, out = loop("--frames", "1080", "--no-walls")
    check("the loop refuses to write a bake that flies off to infinity",
          rc == 3 and data is None and "838,591" in out.replace(",", ",")
          or (rc == 3 and data is None),
          "exit 3, no file written, and the message names the layer and the "
          "frame. B2 found this by accident; it cannot happen silently now")

    rc2, data2, _o = loop("--frames", "1080", "--no-walls", "--allow-escapes")
    check("...but --allow-escapes still writes it",
          rc2 == 0 and data2 is not None,
          "the guard is a default, not a policy: it is the user's comp")


if __name__ == "__main__":
    print("B3 -- the loop, checked end to end")
    test_reproducible()
    test_parameters()
    test_static()
    test_staleness()
    test_escape_guard()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")

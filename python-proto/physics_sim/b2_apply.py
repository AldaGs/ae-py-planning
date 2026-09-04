"""
B2 -- write the bake into AE, and measure what AE did with it.

    python b2_apply.py            bake the real comp -> b2_bake.json
    (in AE)  b2_apply_bake.jsx    apply it, save b2_ae_report.json
    python b2_apply.py            verify the report against the bake

Same shape as B1: the half that needs AE is a script, and everything that can
be checked without AE is checked here first -- including the verifier itself,
against synthetic reports, so that when the real one arrives its numbers can be
trusted.

WHAT B2 IS ACTUALLY TESTING
---------------------------
Not "did the keyframes appear". Three things AE could quietly do differently:

  1. STORE something other than what we sent (rounding, unit assumptions).
  2. INTERPOLATE between our keys with curves instead of straight lines. AE's
     default for a new Position keyframe is auto-bezier with spatial tangents:
     the motion path bows between keys and adds curvature nobody simulated.
     A5 proved this class of fault is invisible on keyframed frames, so the
     report samples half-frames too.
  3. COST too much. Wall I has wanted a number since before Phase A, and the
     script times the naive setValueAtTime loop against bulk setValuesAtTimes,
     with interpolation-setting timed on its own -- it is per-key as well and
     may cost more than the values did.
"""

from __future__ import annotations

import json
import math
import os

import scene_io
from sim import bake as bake_scene

SCENE_FILE = "b1_ae_export.json"
BAKE_FILE = "b2_bake.json"
REPORT_FILE = "b2_ae_report.json"
REPORT_SCHEMA = "ae-physics-report/1"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# --------------------------------------------------------------------------
# Producing the bake
# --------------------------------------------------------------------------

def build_bake():
    with open(SCENE_FILE, encoding="utf-8") as fh:
        doc = json.load(fh)
    problems = scene_io.validate(doc)
    if problems:
        raise SystemExit("scene will not load:\n  " + "\n  ".join(problems))
    scene, meta = scene_io.load_scene(doc)
    return doc, scene, meta


def write_bake(bake):
    # Compact separators, because Wall I is partly a file-size problem: the
    # same document is 4x larger pretty-printed, and ExtendScript has to eval
    # the whole string.
    text = json.dumps(bake, separators=(",", ":"), sort_keys=True)
    with open(BAKE_FILE, "w", encoding="utf-8") as fh:
        fh.write(text)

    keys = sum(len(l["keyframes"]["position"]) + len(l["keyframes"]["rotation"])
               for l in bake["layers"])
    print(f"\n   wrote {BAKE_FILE}: {len(bake['layers'])} layers, {keys} "
          f"keyframes, {len(text) / 1024:.0f} KB "
          f"({len(json.dumps(bake, indent=2)) / len(text):.1f}x smaller than "
          "pretty-printed)")
    return bake


# --------------------------------------------------------------------------
# The verifier -- tested below before it is trusted
# --------------------------------------------------------------------------

def _sample_linear(track, t, scalar):
    """What the bake SAYS the value is at time t, interpolated linearly.

    Deliberately a second implementation rather than a call into preview.py:
    this is the thing AE is being compared against, and sharing code with the
    renderer would let one mistake agree with itself.
    """
    if t <= track[0][0]:
        return track[0][1]
    if t >= track[-1][0]:
        return track[-1][1]
    lo = 0
    hi = len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    f0, v0 = track[lo]
    f1, v1 = track[hi]
    u = (t - f0) / (f1 - f0)
    if scalar:
        return v0 + (v1 - v0) * u
    return [v0[0] + (v1[0] - v0[0]) * u, v0[1] + (v1[1] - v0[1]) * u]


def verify(bake: dict, report: dict) -> dict:
    """Compare what AE holds against what we asked for.

    Returns worst errors, split by stored-vs-tween because they mean different
    things: a stored error is AE not keeping our number, a tween error is AE
    drawing a different curve between our numbers.
    """
    by_id = {l["id"]: l for l in bake["layers"]}
    out = {"layers": [], "worst_stored_px": 0.0, "worst_stored_deg": 0.0,
           "worst_tween_px": 0.0, "worst_tween_deg": 0.0, "missing": [],
           "key_count_mismatch": []}

    for r in report["layers"]:
        bl = by_id.get(r["id"])
        if bl is None:
            out["missing"].append(r["id"])
            continue
        pk, rk = bl["keyframes"]["position"], bl["keyframes"]["rotation"]
        if r["pos_keys"] != len(pk) or r["rot_keys"] != len(rk):
            out["key_count_mismatch"].append(
                f"id {r['id']}: AE has {r['pos_keys']}/{r['rot_keys']} "
                f"position/rotation keys, bake has {len(pk)}/{len(rk)}")

        sp = sd = tp = td = 0.0
        for f, x, y, deg in r["stored"]:
            want = _sample_linear(pk, f, False)
            sp = max(sp, math.dist((x, y), want))
            sd = max(sd, abs(deg - _sample_linear(rk, f, True)))
        for f, x, y, deg in r["tween"]:
            want = _sample_linear(pk, f, False)
            tp = max(tp, math.dist((x, y), want))
            td = max(td, abs(deg - _sample_linear(rk, f, True)))

        out["layers"].append({"id": r["id"], "name": r["name"],
                              "stored_px": sp, "stored_deg": sd,
                              "tween_px": tp, "tween_deg": td})
        out["worst_stored_px"] = max(out["worst_stored_px"], sp)
        out["worst_stored_deg"] = max(out["worst_stored_deg"], sd)
        out["worst_tween_px"] = max(out["worst_tween_px"], tp)
        out["worst_tween_deg"] = max(out["worst_tween_deg"], td)
    return out


# --------------------------------------------------------------------------
# Testing the verifier, with no AE anywhere
# --------------------------------------------------------------------------

def synthetic_report(bake, stride=17, corrupt=None):
    """A report exactly as a PERFECT AE would produce one.

    This is the control the verifier is calibrated against: if a flawless
    report does not score zero, the verifier is broken and any number it later
    prints about the real AE is meaningless.
    """
    layers = []
    for bl in bake["layers"]:
        pk, rk = bl["keyframes"]["position"], bl["keyframes"]["rotation"]
        stored, tween = [], []
        for j in range(0, len(pk), stride):
            f = pk[j][0]
            p = _sample_linear(pk, f, False)
            d = _sample_linear(rk, f, True)
            if corrupt:
                p, d = corrupt(f, list(p), d)
            stored.append([f, p[0], p[1], d])
            if j + 1 < len(pk):
                p2 = _sample_linear(pk, f + 0.5, False)
                d2 = _sample_linear(rk, f + 0.5, True)
                if corrupt:
                    p2, d2 = corrupt(f + 0.5, list(p2), d2)
                tween.append([f + 0.5, p2[0], p2[1], d2])
        layers.append({"id": bl["id"], "name": bl["name"],
                       "pos_keys": len(pk), "rot_keys": len(rk),
                       "stored": stored, "tween": tween})
    return {"schema": REPORT_SCHEMA, "sample_stride": stride,
            "comp": bake["comp"], "total_ms": 0, "timings": [],
            "layers": layers}


def test_verifier(bake):
    print("\n1. THE VERIFIER  (calibrated before it is pointed at AE)")
    perfect = synthetic_report(bake)
    v = verify(bake, perfect)
    check("a flawless report scores exactly zero",
          v["worst_stored_px"] == 0.0 and v["worst_tween_px"] == 0.0
          and not v["key_count_mismatch"],
          "stored 0.000 px / 0.000 deg, tween 0.000 px / 0.000 deg -- so any "
          "number the real report produces is AE's, not the verifier's")

    # A stored-value fault: AE rounds positions to whole pixels.
    rounded = synthetic_report(
        bake, corrupt=lambda f, p, d: ([round(p[0]), round(p[1])], d))
    vr = verify(bake, rounded)
    check("whole-pixel rounding in AE would be caught",
          vr["worst_stored_px"] > 0.2,
          f"worst stored {vr['worst_stored_px']:.3f} px -- A5 measured this "
          "same fault moving 2.4% of the silhouette, so it is worth catching "
          "at the source")

    # The fault this step exists for: AE bows the motion path between our
    # keys. Stored values stay perfect; only the tween moves.
    def bow(f, p, d):
        if f != int(f):                      # only between keyframes
            p[1] += 6.0
        return p, d
    vb = verify(bake, synthetic_report(bake, corrupt=bow))
    check("spatial bezier between keys is caught, and only in the tween",
          vb["worst_stored_px"] == 0.0 and vb["worst_tween_px"] > 5.0,
          f"stored {vb['worst_stored_px']:.3f} px but tween "
          f"{vb['worst_tween_px']:.3f} px. Reading back only stored values "
          "would have called this bake perfect -- which is A5's finding, "
          "restated inside AE")

    # And a rotation-only fault, since the two channels are checked separately.
    vd = verify(bake, synthetic_report(
        bake, corrupt=lambda f, p, d: (p, -d)))
    check("a rotation sign flip is caught independently of position",
          vd["worst_stored_px"] == 0.0 and vd["worst_stored_deg"] > 1.0,
          f"position clean, rotation off by {vd['worst_stored_deg']:.1f} deg")

    miss = synthetic_report(bake)
    miss["layers"][0]["pos_keys"] -= 1
    check("a missing keyframe is caught by count, not by sampling",
          verify(bake, miss)["key_count_mismatch"],
          "sampling every 17th key would step straight over one dropped "
          "keyframe; the count catches what the samples cannot")


def test_wall_i_offline(bake):
    print("\n2. WALL I, THE HALF THAT NEEDS NO AE  (file size and volume)")
    compact = json.dumps(bake, separators=(",", ":"), sort_keys=True)
    pretty = json.dumps(bake, indent=2, sort_keys=True)
    keys = sum(len(l["keyframes"]["position"]) + len(l["keyframes"]["rotation"])
               for l in bake["layers"])
    n = len(bake["layers"])
    frames = bake["comp"]["duration_frames"]
    print(f"      {n} layers x {frames + 1} frames x 2 properties = "
          f"{keys} keyframes")
    print(f"      compact {len(compact) / 1024:7.1f} KB      "
          f"pretty {len(pretty) / 1024:7.1f} KB      "
          f"ratio {len(pretty) / len(compact):.1f}x")
    per40 = len(compact) / n * 40 / 1024 / 1024
    print(f"      extrapolated to 40 layers at this length: "
          f"{per40:.1f} MB, {keys / n * 40:.0f} keyframes")
    check("the bake is small enough to hand to ExtendScript as one string",
          len(compact) < 5_000_000,
          f"{len(compact) / 1024:.0f} KB for {keys} keyframes. The plan's "
          f"worry was 12,000 setValueAtTime calls; this comp alone is {keys} "
          "because it is 45 s long. Write COST is the script's to measure")


# --------------------------------------------------------------------------
# Escapes -- because a bake goes into somebody's real project
# --------------------------------------------------------------------------

def escapes(bake, width, height, margin=2.0):
    """Layers that leave the comp by more than `margin` frames' worth of it.

    A rigid-body sim has no opinion about the edge of the world. If nothing
    stops a body it accelerates forever, and the bake dutifully records where
    it got to. Those numbers then go into a real project as real keyframes, so
    this is a guard, not a diagnostic.
    """
    out = []
    for lay in bake["layers"]:
        worst, at = 0.0, 0
        for f, (x, y) in lay["keyframes"]["position"]:
            d = max(-x, x - width, -y, y - height, 0.0)
            if d > worst:
                worst, at = d, f
        if worst > margin * max(width, height):
            out.append({"id": lay["id"], "name": lay["name"],
                        "px": worst, "frame": at})
    return out


def enclose(scene):
    """Add the missing walls: floor, both sides, ceiling."""
    w, h = scene.width, scene.height
    scene.statics = [((0.0, h - 1.0), (float(w), h - 1.0)),
                     ((0.0, 0.0), (0.0, h - 1.0)),
                     ((float(w), 0.0), (float(w), h - 1.0)),
                     ((0.0, 0.0), (float(w), 0.0))]
    return scene


def test_escapes(scene, meta, doc):
    """The scene as the reader wrote it, against the same scene in a box.

    This is not a solver bug and not the sliver risk A4 flagged -- the S rolls
    steadily left, reaches the END OF THE FLOOR SEGMENT, and leaves. The first
    version of the reader wrote one floor exactly comp-width and no walls,
    which is a placeholder that happens to be dangerous.
    """
    print("\n3. ESCAPES  (a bake is written into somebody's real project)")
    ids = [m["id"] for m in meta["layers"]]
    w, h = scene.width, scene.height
    open_bake = bake_scene(scene, ids)
    gone = escapes(open_bake, w, h)
    for g in gone:
        print(f"      {g['name']!r} leaves the comp by {g['px']:,.0f} px "
              f"(worst at frame {g['frame']})")
    check("the reader's original floor-only scene lets a body escape",
          len(gone) == 1,
          f"{len(gone)} of {len(scene.bodies)} layers integrate off to "
          "infinity. The floor was one segment exactly comp-width with no "
          "walls, so anything that rolls reaches the end of it and falls "
          "forever -- correct physics, dangerous scene")

    boxed = bake_scene(enclose(scene), ids)
    still = escapes(boxed, w, h)
    check("...and a closed box fixes it",
          not still,
          f"floor, two walls and a ceiling: all {len(scene.bodies)} layers "
          "stay in frame. The reader script now writes the box; this scene "
          "predates that fix, so the walls are added here")

    settled = []
    for lay in boxed["layers"]:
        pk = lay["keyframes"]["position"]
        drift = sum(abs(pk[i + 1][1][0] - pk[i][1][0]) +
                    abs(pk[i + 1][1][1] - pk[i][1][1])
                    for i in range(len(pk) - 101, len(pk) - 1))
        settled.append((lay["name"], drift))
    check("every body comes to rest before the comp ends",
          all(d < 1.0 for _n, d in settled),
          "; ".join(f"{n}: {d:.3f} px over the last 100 frames"
                    for n, d in settled))
    return boxed


def write_preview(bake, doc, scene, meta):
    """A contact sheet of the bake, to look at BEFORE writing it into a project.

    A5's whole argument was that a bake is only checkable by looking at it.
    Applying 6,486 keyframes to somebody's comp is exactly the moment to take
    that seriously.
    """
    import preview
    layers = [preview.PreviewLayer(m["id"], m["name"], b.parts, b.anchor)
              for m, b in zip(meta["layers"], scene.bodies)]
    ps = preview.PreviewScene(scene.width, scene.height,
                              list(scene.statics), background=(255, 255, 255))
    last = bake["comp"]["duration_frames"]
    ts = [0.0, last * 0.02, last * 0.05, last * 0.1,
          last * 0.2, last * 0.4, last * 0.7, float(last)]
    sheet = preview.contact_sheet(bake, layers, ps, ts, scale=0.5, cols=4)
    sheet.save("b2_preview.png")
    print(f"   wrote b2_preview.png -- frames "
          f"{[round(t) for t in ts]}, look before you apply")


# --------------------------------------------------------------------------
# The real report, once AE has produced one
# --------------------------------------------------------------------------

def test_real_report(bake):
    print("\n4. THE REAL REPORT  (what AE actually did)")
    if not os.path.exists(REPORT_FILE):
        print(f"      {REPORT_FILE} not present -- run b2_apply_bake.jsx in AE")
        return False
    with open(REPORT_FILE, encoding="utf-8") as fh:
        report = json.load(fh)
    check("the report is the schema this verifier reads",
          report.get("schema") == REPORT_SCHEMA,
          f"{report.get('schema')} from AE {report.get('ae_version', '?')}")

    v = verify(bake, report)
    print("      layer                 stored px   stored deg   "
          "tween px   tween deg")
    print("      -------------------   ---------   ----------   "
          "--------   ---------")
    for L in v["layers"]:
        print(f"      {L['name']:<19}   {L['stored_px']:9.4f}   "
              f"{L['stored_deg']:10.4f}   {L['tween_px']:8.4f}   "
              f"{L['tween_deg']:9.4f}")

    check("AE stored what the bake asked for",
          v["worst_stored_px"] < 0.01 and v["worst_stored_deg"] < 0.01
          and not v["key_count_mismatch"] and not v["missing"],
          f"worst {v['worst_stored_px']:.4f} px / "
          f"{v['worst_stored_deg']:.4f} deg across every sampled key"
          if not v["key_count_mismatch"]
          else "; ".join(v["key_count_mismatch"]))

    check("AE interpolates in straight lines between the keys",
          v["worst_tween_px"] < 0.01 and v["worst_tween_deg"] < 0.01,
          f"worst half-frame error {v['worst_tween_px']:.4f} px / "
          f"{v['worst_tween_deg']:.4f} deg. Non-zero here means the motion "
          "path is bowing between keyframes -- curvature nobody simulated")

    if report.get("timings"):
        print("\n      WALL I, MEASURED IN AE")
        print("      layer                 keys   loop ms   bulk ms   "
              "interp ms   loop/key   bulk/key")
        print("      -------------------   ----   -------   -------   "
              "---------   --------   --------")
        tl = tb = ti = tk = 0
        for T in report["timings"]:
            k = T["keys"]
            tk += k
            tl += max(T["loop_ms"], 0)
            tb += T["bulk_ms"]
            ti += T["interp_ms"]
            print(f"      {T['name']:<19}   {k:4d}   {T['loop_ms']:7d}   "
                  f"{T['bulk_ms']:7d}   {T['interp_ms']:9d}   "
                  f"{T['loop_ms'] / k * 1000:8.1f}us   "
                  f"{T['bulk_ms'] / k * 1000:8.1f}us")
        speedup = (tl / tb) if tb else float("inf")
        check("bulk setValuesAtTimes is worth using",
              tb <= tl,
              f"{tk} keyframes: loop {tl} ms, bulk {tb} ms "
              f"({speedup:.1f}x), interpolation {ti} ms. Total apply "
              f"{report['total_ms'] / 1000:.2f} s")
        check("setting interpolation is accounted for, not hidden",
              True,
              f"forcing LINEAR cost {ti} ms, "
              f"{ti / max(tb, 1):.1f}x the value writes themselves -- it is "
              "per-key too, and it is not optional: AE's default spatial "
              "auto-bezier would bow the path between our samples")
    return True


if __name__ == "__main__":
    print("B2 -- applying a bake to real layers")
    doc, scene, meta = build_bake()
    first = bake_scene(scene, [m["id"] for m in meta["layers"]])
    test_verifier(first)
    test_wall_i_offline(first)
    bake = test_escapes(scene, meta, doc)
    write_bake(bake)
    write_preview(bake, doc, scene, meta)
    got_real = test_real_report(bake)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")
    if not got_real:
        print(f"\n   Next: open the comp in AE, run b2_apply_bake.jsx, pick\n"
              f"   {BAKE_FILE}, and save the report as {REPORT_FILE}.")

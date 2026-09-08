"""
C0.3 client -- can AEGP_KeyframeSuite beat ExtendScript's 853 us/key?

    python c03_client.py bench                 # one run, 6,486 keys
    python c03_client.py bench --keys 1000     # a smaller one
    python c03_client.py bench --sweep         # several, with pauses between
    python c03_client.py bench --mode full     # the O(n^2) path, capped low

THE BASELINE, AND WHAT IT MEANS
-------------------------------
B2 measured on AE 26.3x87, over 6,486 real keyframes across 3 layers:

    setValueAtTime in a loop      6,850 us/key      82 s per 12,000
    setValuesAtTimes in bulk         19.6 us/key     0.2 s
    forcing LINEAR interpolation    853 us/key      10 s

So Wall I is not the value writes -- those are already free. It is the
interpolation pass: 44x the cost of the bulk write, per-key, with no bulk form
in ExtendScript, and not optional, because AE's default for a new Position
keyframe is auto-bezier with SPATIAL tangents and the motion path bows between
keys if you skip it.

WHAT READING THE SUITE ALREADY TOLD US
--------------------------------------
AEGP_SetKeyframeInterpolation is ALSO per-key. There is no bulk form natively
either, so going native does not change the SHAPE of the work -- it changes who
pays for each call.

Which makes the real question: is 853 us/key the ExtendScript bridge, or is it
AE doing the work? Only the first goes away. C0.2 found ExtendScript's
per-character cost dominated everything around it, which is grounds for
suspecting the bridge -- but a per-character tax is not a per-call one, and
suspicion is not a measurement.

If native interpolation lands near 20 us/key, Wall I is solved and fracture
becomes affordable. If it lands near 853, the cost is AE's, native buys nothing,
and the answer is decimation or AE's default-interpolation preference -- which
B2 flagged as untested and still is.

WHAT IT DOES TO YOUR PROJECT, AND WHAT IT ONCE DID TO A MACHINE
---------------------------------------------------------------
The bridge builds its own scratch comp and solid, times those, and deletes
them. Nothing of yours is touched.

But the first version of this sweep exhausted memory badly enough to force a
restart, and the reason is worth keeping written down: every call in
AEGP_KeyframeSuite is marked UNDOABLE, so 12,000 keys across four phases is
~48,000 operations AE has to retain -- and the scratch comp was deleted INSIDE
the same undo group, so AE had to keep all of it alive to be able to undo the
deletion. Eight of those back to back, with AEGP_SetKeyframeFlag turning out to
be O(n^2) on top, was too much.

So: one run per invocation unless --sweep is asked for, 6,486 keys maximum
(B2's real count -- everything above it was extrapolation), mode=full capped
lower still because it is the quadratic path, and the bridge closes the
measured undo group before deleting anything.

--purge empties AE's undo and image caches afterwards, which is the only thing
that actually RELEASES the retained state. It is off by default because it
discards your undo history for the whole project, not just this benchmark's
share of it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from c02_client import request                                  # noqa: E402

# B2's measurements, WALKTHROUGH.md "Wall I, measured".
ES_BULK_WRITE = 19.6
ES_INTERP = 853.0


def bench(n: int, mode: str = "nobezier", purge: bool = False,
          timeout=900.0) -> dict:
    msg = {"cmd": "bench_keys", "bytes": str(n), "mode": mode}
    if purge:
        msg["purge"] = "1"
    reply = request(msg, timeout=timeout)
    if reply.startswith('{"ok":false'):
        raise SystemExit("bridge error: " + json.loads(reply)["error"])
    return json.loads(reply)


def integrity(r: dict) -> tuple[bool, str]:
    """Did the pass actually make the path straight?

    Three things have to hold, and the last is the one A5 spent a phase
    learning to check: a cleared auto-bezier flag with a non-zero tangent
    still bows the path between keyframes, where every stored value looks
    perfect.

    The SAMPLED counts decide this, not `interp_readback`. That field reports
    one named key, and the sampler steps past it unless the stride happens to
    land there -- it read 0, meaning "never sampled", on a run where all 66
    sampled keys were clean, and 0 is indistinguishable from a total failure.
    It stays in the reply for the record and is deliberately not judged here.
    """
    n = r.get("keys_checked", 0)

    if not n:
        return False, "nothing was checked"
    if r.get("bad_interp") or r.get("bad_flag"):
        return False, (f"of {n} keys, {r.get('bad_interp', 0)} are not LINEAR "
                       f"and {r.get('bad_flag', 0)} are still auto-bezier")
    if r["tangent_magnitude"] > 1e-9:
        return False, (f"worst tangent over {n} keys is "
                       f"{r['tangent_magnitude']:.4f}, not zero")
    return True, f"straight ({n} keys)"


def linear_pass(r: dict) -> float:
    """The native equivalent of B2's makeLinear(), which is what 853 measures.

    makeLinear() makes THREE calls per Position key -- set the interpolation
    type, clear spatial auto-bezier, zero the spatial tangents -- and its
    timer covers all three. Comparing one native phase against that number
    would put a third of the work up against all of it, and would have
    reported native as ~49x faster when it is not.
    """
    bez = r["bezier_us_per_key"] if r.get("bezier_ran") else 0.0
    return r["interp_us_per_key"] + bez + r["tangent_us_per_key"]


def show(r: dict) -> None:
    ok, why = integrity(r)
    bez = (f"{r['bezier_us_per_key']:>7.1f}" if r.get("bezier_ran")
           else "      -")
    print(f"   {r['stored']:>7,} keys   "
          f"add {r['add_us_per_key']:>6.1f}   "
          f"interp {r['interp_us_per_key']:>6.1f}   "
          f"bezier {bez}   "
          f"tangents {r['tangent_us_per_key']:>6.1f}   "
          f"= {linear_pass(r):>7.1f} us/key   {why}")

    if r["stored"] != r["asked"]:
        print(f"      ! only {r['stored']:,} of {r['asked']:,} keys were "
              f"stored -- the timings are per stored key")


MAX_KEYS = 6486         # B2's real key count; above this was extrapolation
FULL_MAX = 3000         # mode=full is the O(n^2) path -- keep it small


def cmd_bench(args):
    """One size per invocation by default.

    The sweep that took a machine down ran eight benchmarks back to back with
    nothing released between them. Each one is cheap on its own; it was the
    accumulation that hurt. So a plain `bench` is now a single run, and the
    sweep has to be asked for.
    """
    mode = args.mode or "nobezier"

    if args.sweep:
        sizes = [n for n in (1000, 3000, MAX_KEYS)
                 if mode != "full" or n <= FULL_MAX]
    else:
        sizes = [args.keys or MAX_KEYS]

    for n in sizes:
        if n > MAX_KEYS:
            raise SystemExit(
                f"{n:,} keys is above the {MAX_KEYS:,} cap. That cap is B2's "
                f"real key count; everything above it was extrapolation, and "
                f"it is what the earlier sweep exhausted memory reaching.")
        if mode == "full" and n > FULL_MAX:
            raise SystemExit(
                f"mode=full at {n:,} keys is the O(n^2) path -- "
                f"AEGP_SetKeyframeFlag cost 3,197 us/key at 12,000 and that "
                f"run is what crashed the machine. It is capped at "
                f"{FULL_MAX:,}, which is enough to show the shape.")

    print("\n   native keyframes, via AEGP_KeyframeSuite")
    print(f"   ExtendScript baseline: bulk write {ES_BULK_WRITE} us/key, "
          f"the LINEAR pass {ES_INTERP} us/key")
    print(f"\n   mode={mode}  ("
          + ("all three calls, as B2 does it -- the O(n^2) path"
             if mode == "full" else "without the auto-bezier call")
          + (", purging after each run" if args.purge else "") + ")\n")

    last = None
    for i, n in enumerate(sizes):
        t0 = time.time()
        r = bench(n, mode, purge=args.purge)
        show(r)
        last = r
        if time.time() - t0 > 30:
            print(f"      ({time.time() - t0:.0f}s for that one)")

        # Let AE settle rather than queueing the next one straight behind it.
        # Cheap, and the failure it guards against cost a restart.
        if i + 1 < len(sizes):
            time.sleep(3)

    if last is None:
        return 1

    ok, why = integrity(last)
    print(f"\n   dimensionality {last['dim']}; final state: {why}")
    if not ok:
        print("   the pass did not take -- the timings above mean nothing")
        return 1

    p = linear_pass(last)
    n = last["stored"]
    print(f"\n   the LINEAR pass: {p:.1f} us/key native vs {ES_INTERP} "
          f"ExtendScript  --  {ES_INTERP / p:.0f}x faster")

    # Projected, and said so. Measuring at 12,000 is what the cap now forbids,
    # so quoting a 12,000-key figure as if it were measured would be a lie the
    # crash already paid for.
    print(f"   12,000 keys, PROJECTED from {n:,}: {p * 12000 / 1e6:.1f} s "
          f"(ExtendScript: {ES_INTERP * 12000 / 1e6:.0f} s)")

    fifty = p * 50 * 300 / 1e6
    print(f"   fifty shards x 300 frames, PROJECTED: {fifty:.1f} s "
          f"(ExtendScript: {ES_INTERP * 50 * 300 / 1e6:.0f} s)")

    total = last["add_us_per_key"] + p
    print(f"\n   whole apply, add + pass: {total:.1f} us/key")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("bench", help="time native keyframe writing")
    p.add_argument("--keys", type=int, default=None,
                   help="one key count instead of the sweep")
    p.add_argument("--mode", choices=("full", "nobezier"), default=None,
                   help="'nobezier' (the default) omits the auto-bezier call, "
                        "which turned out to be both the whole cost and "
                        "unnecessary; 'full' does all three calls B2 does and "
                        "is capped low because it is the O(n^2) path")
    p.add_argument("--sweep", action="store_true",
                   help="several key counts in one go, with a pause between. "
                        "Off by default: it was the accumulation of eight "
                        "back-to-back runs that exhausted memory, not any one "
                        "of them")
    p.add_argument("--purge", action="store_true",
                   help="purge AE's undo and image caches after each run. "
                        "This discards YOUR undo history for the whole "
                        "project, so it is off by default")
    p.set_defaults(fn=cmd_bench)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)

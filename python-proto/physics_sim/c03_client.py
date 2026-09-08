"""
C0.3 client -- can AEGP_KeyframeSuite beat ExtendScript's 853 us/key?

    python c03_client.py bench                 # sweep key counts
    python c03_client.py bench --keys 6486     # B2's exact key count

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

WHAT IT DOES TO YOUR PROJECT
----------------------------
Nothing that survives. The bridge builds its own scratch comp and solid, times
those, and deletes them, all inside one undo group.
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


def bench(n: int, mode: str = "full", timeout=900.0) -> dict:
    reply = request({"cmd": "bench_keys", "bytes": str(n), "mode": mode},
                    timeout=timeout)
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


def cmd_bench(args):
    sizes = [args.keys] if args.keys else [1000, 3000, 6486, 12000]
    modes = [args.mode] if args.mode else ["full", "nobezier"]

    print("\n   native keyframes, via AEGP_KeyframeSuite")
    print(f"   ExtendScript baseline: bulk write {ES_BULK_WRITE} us/key, "
          f"the LINEAR pass {ES_INTERP} us/key")

    last = None
    for mode in modes:
        label = ("all three calls, as B2 does it" if mode == "full"
                 else "without the auto-bezier call")
        print(f"\n   mode={mode}  ({label})\n")
        for n in sizes:
            t0 = time.time()
            r = bench(n, mode)
            show(r)
            if mode == "nobezier":
                last = r
            elif last is None:
                last = r
            if time.time() - t0 > 60:
                print(f"      ({time.time() - t0:.0f}s for that one)")

    if last is None:
        return 1

    ok, why = integrity(last)
    print(f"\n   dimensionality {last['dim']}; final state: {why}")
    if not ok:
        print("   the pass did not take -- the timings above mean nothing")
        return 1

    p = linear_pass(last)
    print(f"\n   the LINEAR pass, all three calls: {p:.1f} us/key native "
          f"vs {ES_INTERP} ExtendScript  --  {ES_INTERP / p:.0f}x faster")

    print(f"   12,000 keys: {p * 12000 / 1e6:.1f} s "
          f"(ExtendScript: {ES_INTERP * 12000 / 1e6:.0f} s)")

    # Fracture is the case that made this a requirement rather than a
    # nice-to-have: fifty shards is where the pass became tens of seconds.
    fifty = p * 50 * 300 / 1e6
    print(f"   fifty shards x 300 frames: {fifty:.1f} s "
          f"(ExtendScript: {ES_INTERP * 50 * 300 / 1e6:.0f} s)")

    total = last["add_us_per_key"] + p
    print(f"\n   whole apply, add + pass: {total:.1f} us/key, so 12,000 keys "
          f"is {total * 12000 / 1e6:.1f} s")
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
                   help="'full' does all three calls B2 does; 'nobezier' "
                        "omits the auto-bezier one, to find out whether "
                        "setting tangents already clears the flag")
    p.set_defaults(fn=cmd_bench)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)

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


def bench(n: int, timeout=600.0) -> dict:
    reply = request({"cmd": "bench_keys", "bytes": str(n)}, timeout=timeout)
    if reply.startswith('{"ok":false'):
        raise SystemExit("bridge error: " + json.loads(reply)["error"])
    return json.loads(reply)


def show(r: dict) -> None:
    add, interp, tan = (r["add_us_per_key"], r["interp_us_per_key"],
                        r["tangent_us_per_key"])

    # The comparison that matters is interpolation against 853; the add is
    # shown against the bulk write because that is the path B2 actually uses.
    print(f"   {r['stored']:>7,} keys   "
          f"add {add:>8.1f}   interp {interp:>8.1f}   tangents {tan:>8.1f} "
          f"us/key")

    if r["stored"] != r["asked"]:
        print(f"      ! only {r['stored']:,} of {r['asked']:,} keys were "
              f"stored -- the timings are per stored key")


def cmd_bench(args):
    sizes = [args.keys] if args.keys else [1000, 3000, 6486, 12000]

    print("\n   native keyframes, via AEGP_KeyframeSuite")
    print(f"   ExtendScript baseline: bulk write {ES_BULK_WRITE} us/key, "
          f"interpolation {ES_INTERP} us/key\n")

    last = None
    for n in sizes:
        t0 = time.time()
        r = bench(n)
        show(r)
        last = r
        # A slow phase is the finding, not a reason to stop, but a run that
        # takes minutes per size deserves to say so.
        if time.time() - t0 > 60:
            print(f"      ({time.time() - t0:.0f}s for that one)")

    if last is None:
        return 1

    print(f"\n   dimensionality {last['dim']}, interpolation read back as "
          f"{last['interp_readback']} "
          f"({'LINEAR' if last['interp_readback'] == 1 else 'NOT LINEAR'})")

    i = last["interp_us_per_key"]
    print(f"\n   interpolation: {i:.1f} us/key native vs {ES_INTERP} "
          f"ExtendScript  --  {ES_INTERP / i:.0f}x faster"
          if i > 0 else "")

    per_12k = i * 12000 / 1e6
    print(f"   12,000 keys would cost {per_12k:.1f} s of interpolation "
          f"(ExtendScript: {ES_INTERP * 12000 / 1e6:.0f} s)")

    # Fracture is the case that made this a requirement rather than a
    # nice-to-have: fifty shards is where 853 us/key became ~25 s.
    fifty = i * 50 * 300 / 1e6
    print(f"   fifty shards x 300 frames: {fifty:.1f} s "
          f"(ExtendScript: {ES_INTERP * 50 * 300 / 1e6:.0f} s)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("bench", help="time native keyframe writing")
    p.add_argument("--keys", type=int, default=None,
                   help="one key count instead of the sweep")
    p.set_defaults(fn=cmd_bench)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)

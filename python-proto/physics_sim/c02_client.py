"""
C0.2 client -- how big a payload will AEGP_ExecuteScript take, and by which road?

    python c02_client.py sweep                      # find the ceiling, IN
    python c02_client.py sweep --mode file          # the same, via a temp file
    python c02_client.py echo                       # find the ceiling, OUT
    python c02_client.py payload b2_bake.json       # the real bake, both roads
    python c02_client.py probe --bytes 200000       # one measurement

WHAT THE QUESTION ACTUALLY IS
-----------------------------
`b2_apply_bake.jsx` already reads its bake from a file: File.openDialog ->
read() -> eval. So "or must the script read a temp file?" is not asking whether
the file road works -- it demonstrably does, by hand, today. It asks whether
the OTHER road is viable: escaping the bake into the script text and handing
the whole thing to AEGP_ExecuteScript in one call.

If the literal road works and is not slower, the bridge can be self-contained:
the Tauri shell hands the bake to the AEGP and nothing touches the disk. If it
breaks, or costs more, B2 gets the same one-line prelude B1 got --
`var PHYS_BAKE_PATH = "..."` in place of the dialog -- and the bake goes via a
temp file. Either answer is cheap to act on; not knowing is what is expensive.

WHY THIS MEASURES INTEGRITY AND NOT JUST SURVIVAL
-------------------------------------------------
A payload that arrives truncated and still parses is exactly how a size
question gets a false pass -- C0.1's whole point was that 2 KB out proves
nothing about 148 KB in. So the script checksums what it received, the bridge
checksums what it sent, and this client compares the two. `head` and `tail` are
the first and last sixteen character codes: they turn "it differs" into "it was
cut at the end" or "the escaping mangled the front".

`chars` vs `bytes_sent` is deliberately NOT a failure on its own. ExtendScript
counts UTF-16 code units and the bridge counts bytes, so the two agree only for
ASCII -- the bridge reports whether the payload was ASCII, and a mismatch on a
non-ASCII payload is arithmetic, not corruption.

THE PIPE IS MEASURED SEPARATELY, AND ON PURPOSE
-----------------------------------------------
`sweep`, `echo` and `payload` all generate or read their payload INSIDE the
bridge, so what they produce is AEGP_ExecuteScript's ceiling and nothing else.
Getting a 145 KB bake from the shell INTO the bridge is a different limit, and
`pipe` is the one that measures it: there the payload travels in the request
line itself, and the bridge reports what arrived without touching an AEGP
suite. Keeping them apart is the point -- one number covering both would
describe neither.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

PIPE_TX = r"\\.\pipe\aephys_bridge_tx"      # AE writes, we read
PIPE_RX = r"\\.\pipe\aephys_bridge_rx"      # we write, AE reads

DEFAULT_PAYLOAD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "b2_bake.json")


# ---------------------------------------------------------------------------
# transport (same shape as c01_client, kept separate so C0.1 stays runnable)
# ---------------------------------------------------------------------------

def connect(timeout=10.0):
    """Open both pipes. TX first, because that is the order AE creates them."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            tx = open(PIPE_TX, "rb", buffering=0)      # AE -> us
            rx = open(PIPE_RX, "wb", buffering=0)      # us -> AE
            return tx, rx
        except OSError as e:
            last = e
            time.sleep(0.2)
    raise SystemExit(
        f"could not connect to the bridge ({last}).\n"
        "Is After Effects running with PhysBridge.aex installed? "
        "Check Window > 'PhysBridge: bridge status'."
    )


def request(msg: dict, timeout=300.0) -> str:
    """Send one request, read one newline-terminated reply.

    The timeout is generous because that is the point: a probe that is merely
    SLOW must not be recorded as a probe that failed. A real ceiling shows up
    as an error string from AE, not as silence.
    """
    tx, rx = connect()
    try:
        rx.write((json.dumps(msg) + "\n").encode("utf-8"))
        rx.flush()

        chunks: list[bytes] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = tx.read(1 << 16)
            if not b:
                time.sleep(0.01)
                continue
            chunks.append(b)
            if b"\n" in b:
                break
        data = b"".join(chunks)
        if not data:
            raise TimeoutError(f"no reply within {timeout:.0f}s")
        return data.split(b"\n", 1)[0].decode("utf-8")
    finally:
        tx.close()
        rx.close()


# ---------------------------------------------------------------------------
# the checksum, which must stay identical to the bridge's and the script's
# ---------------------------------------------------------------------------

def checksum(text: str) -> int:
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) % 4294967296
    return h


# ---------------------------------------------------------------------------
# one measurement
# ---------------------------------------------------------------------------

class Failed(Exception):
    """The bridge or AE refused. Carries the reason, which is the measurement."""


def probe(bytes_=None, path=None, mode="literal", echo=False) -> dict:
    msg = {"cmd": "send_payload" if path else "size_probe", "mode": mode}
    if path:
        msg["script"] = os.path.abspath(path)
    else:
        msg["bytes"] = str(bytes_)
    if echo:
        msg["echo"] = "1"

    t0 = time.time()
    reply = request(msg)
    wall_ms = (time.time() - t0) * 1000

    if reply.startswith('{"ok":false'):
        raise Failed(json.loads(reply)["error"])

    r = json.loads(reply)
    r["wall_ms"] = wall_ms
    return r


def verdict(r: dict) -> tuple[bool, str]:
    """Compare what the bridge says it sent with what the script says it saw."""
    sent, got = r["bytes_sent"], r["script"]

    if got["chars"] != sent and r["ascii"]:
        return False, (f"TRUNCATED  {got['chars']} of {sent} chars "
                       f"({sent - got['chars']} lost)")
    if got["sum"] != r["sum_sent"] and r["ascii"]:
        where = []
        if got["head"] != r["head_sent"]:
            where.append("the front differs")
        if got["tail"] != r["tail_sent"]:
            where.append("the end differs")
        return False, "CORRUPT  checksums disagree" + (
            " (" + ", ".join(where) + ")" if where else
            " (both ends intact, so the damage is interior)")
    if not r["ascii"]:
        return True, (f"intact by length; the payload is not ASCII so the "
                      f"checksum is not comparable")
    if got.get("parse_error"):
        return False, "arrived intact but eval failed: " + got["parse_error"]
    return True, "intact"


def show(r: dict, label: str = "") -> bool:
    ok, why = verdict(r)
    g = r["script"]
    parts = [f"{r['bytes_sent']:>9,} B",
             f"script {r['script_bytes']:>9,} B",
             f"{r['ms']:>6} ms"]
    if g["ms_sum"] >= 0:
        parts.append(f"sum {g['ms_sum']:>5} ms")
    if g["ms_eval"] >= 0:
        parts.append(f"eval {g['ms_eval']:>5} ms")
        parts.append(f"{g['keys']} keys")
    print(f"   {label:<8}{r['mode']:<8}" + "  ".join(parts) + f"   {why}")
    return ok


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_probe(args):
    try:
        return 0 if show(probe(bytes_=args.bytes, mode=args.mode)) else 1
    except (Failed, TimeoutError) as e:
        print(f"   {args.bytes:>9,} B  {args.mode:<8}FAILED: {e}")
        return 1


def cmd_sweep(args):
    """Double until it breaks, then bisect. 'Where' means a byte count."""
    print(f"\n   sweep, mode={args.mode} -- doubling until AE refuses\n")

    n, last_ok, first_bad = args.start, 0, None
    while n <= args.limit:
        try:
            if not show(probe(bytes_=n, mode=args.mode)):
                first_bad = n
                break
            last_ok = n
        except (Failed, TimeoutError) as e:
            print(f"   {n:>9,} B  {args.mode:<8}FAILED: {e}")
            first_bad = n
            break
        n *= 2

    if first_bad is None:
        print(f"\n   no ceiling below {args.limit:,} bytes in {args.mode} mode.")
        print("   That is the result: the limit is not where this spike can "
              "reach, so\n   the 148 KB bake is nowhere near it.")
        return 0

    # Bisect to a real number rather than reporting the doubling step, since
    # "somewhere between 8 MB and 16 MB" is not a limit anyone can design to.
    print(f"\n   broke at {first_bad:,}; bisecting between "
          f"{last_ok:,} and {first_bad:,}\n")
    lo, hi = last_ok, first_bad
    while hi - lo > max(4096, lo // 64):
        mid = (lo + hi) // 2
        try:
            ok = show(probe(bytes_=mid, mode=args.mode))
        except (Failed, TimeoutError) as e:
            print(f"   {mid:>9,} B  {args.mode:<8}FAILED: {e}")
            ok = False
        lo, hi = (mid, hi) if ok else (lo, mid)

    print(f"\n   CEILING  {lo:,} bytes of payload survives, {hi:,} does not "
          f"({args.mode} mode)")
    print(f"   the bake is {os.path.getsize(DEFAULT_PAYLOAD):,} bytes"
          if os.path.exists(DEFAULT_PAYLOAD) else "")
    return 0


def cmd_echo(args):
    """The return direction, on its own.

    Two calls per size: one report probe to learn the checksum of what the
    bridge synthesised, then the echo whose reply IS that payload. The payload
    is deterministic, so the first call is a legitimate statement of what the
    second one should contain -- and this client never has to keep its own copy
    of the bridge's alphabet, which would be a constant in two places waiting
    to drift.
    """
    print("\n   echo -- payload in, the same payload back out\n")

    n = args.start
    while n <= args.limit:
        try:
            expect = probe(bytes_=n, mode="literal")
            t0 = time.time()
            got = request({"cmd": "size_probe", "bytes": str(n),
                           "mode": "literal", "echo": "1"})
            ms = (time.time() - t0) * 1000
        except (Failed, TimeoutError) as e:
            print(f"   {n:>9,} B  FAILED: {e}")
            return 1

        if got.startswith('{"ok":false'):
            print(f"   {n:>9,} B  FAILED: {json.loads(got)['error']}")
            return 1

        same = len(got) == n and checksum(got) == expect["sum_sent"]
        print(f"   {n:>9,} B  back {len(got):>9,} B  {ms:>6.0f} ms   "
              + ("intact" if same else
                 f"MISMATCH  {len(got)} of {n} bytes returned"))
        if not same:
            return 1
        n *= 2

    print(f"\n   no ceiling on the return direction below {args.limit:,} bytes.")
    return 0


def cmd_pipe(args):
    """The half C0.2 explicitly did not measure: the request direction.

    Every other probe generates or reads its payload INSIDE the bridge, so it
    measures AEGP_ExecuteScript and nothing else. Here the payload travels in
    the request line itself, which is what a shell handing over a bake would
    actually do, and the bridge reports what arrived without touching an AEGP
    suite. What is under test is the transport and the request reader.

    The payload is JSON-escaped into the request by json.dumps, so it exercises
    the escaping too -- with --real, on a bake full of the quotes and
    backslashes that make escaping worth testing at all.
    """
    if args.real:
        src = open(args.real, encoding="utf-8").read()
        print(f"\n   pipe, request direction -- {os.path.basename(args.real)}, "
              f"{len(src):,} chars\n")
        sizes = [len(src)]
    else:
        print("\n   pipe, request direction -- doubling until the bridge "
              "refuses\n")
        sizes = []
        n = args.start
        while n <= args.limit:
            sizes.append(n)
            n *= 2

    alphabet = ("0123456789abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ.,:;-_/")

    for n in sizes:
        payload = src if args.real else "".join(
            alphabet[i % len(alphabet)] for i in range(n))

        t0 = time.time()
        try:
            reply = request({"cmd": "pipe_probe", "data": payload})
        except TimeoutError as e:
            print(f"   {n:>10,}  FAILED: {e}")
            return 1
        ms = (time.time() - t0) * 1000

        if reply.startswith('{"ok":false'):
            print(f"   {n:>10,}  REFUSED: {json.loads(reply)['error']}")
            return 1

        r = json.loads(reply)
        want = checksum(payload)
        head = "".join("%04x" % ord(c) for c in payload[:16])
        tail = "".join("%04x" % ord(c) for c in payload[-16:])

        if r["bytes_received"] != len(payload):
            why = (f"TRUNCATED  {r['bytes_received']:,} of {len(payload):,} "
                   f"arrived")
        elif r["sum"] != want:
            why = "CORRUPT  checksums disagree" + (
                "" if r["head"] == head else " (the front differs)") + (
                "" if r["tail"] == tail else " (the end differs)")
        else:
            why = "intact"

        # The request line is bigger than the payload: JSON escaping, plus the
        # envelope. On a real bake that gap is the escaping, and it is the
        # thing a size limit would actually be measured against.
        line = len(json.dumps({"cmd": "pipe_probe", "data": payload})) + 1
        print(f"   {len(payload):>10,} B payload   {line:>10,} B line   "
              f"{ms:>6.0f} ms   {why}")

        if why != "intact":
            return 1

    print(f"\n   no ceiling in the request direction below "
          f"{sizes[-1]:,} bytes.")
    return 0


def cmd_payload(args):
    """The real bake, down both roads, so the comparison is like for like."""
    path = os.path.abspath(args.path)
    if not os.path.exists(path):
        raise SystemExit(f"no such payload: {path}")

    size = os.path.getsize(path)
    print(f"\n   {os.path.basename(path)} -- {size:,} bytes, "
          f"both ingestion paths\n")

    rc = 0
    for mode in ("literal", "file"):
        try:
            if not show(probe(path=path, mode=mode)):
                rc = 1
        except (Failed, TimeoutError) as e:
            print(f"   {size:>9,} B  {mode:<8}FAILED: {e}")
            rc = 1
    print("\n   'eval' is the cost B2 already pays for its bake either way;\n"
          "   the difference between the two rows is what the road costs.")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="one measurement at one size")
    p.add_argument("--bytes", type=int, default=145090)
    p.add_argument("--mode", choices=("literal", "file"), default="literal")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("sweep", help="find the ceiling in the IN direction")
    p.add_argument("--mode", choices=("literal", "file"), default="literal")
    p.add_argument("--start", type=int, default=1 << 14)      # 16 KB
    p.add_argument("--limit", type=int, default=1 << 25)      # 32 MB
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("echo", help="find the ceiling in the OUT direction")
    p.add_argument("--start", type=int, default=1 << 14)
    p.add_argument("--limit", type=int, default=1 << 25)
    p.set_defaults(fn=cmd_echo)

    p = sub.add_parser("pipe", help="the request direction, over the pipe")
    p.add_argument("--start", type=int, default=1 << 14)
    p.add_argument("--limit", type=int, default=1 << 25)
    p.add_argument("--real", nargs="?", const=DEFAULT_PAYLOAD, default=None,
                   help="send a real file instead of the sweep, so the JSON "
                        "escaping is exercised on quotes and backslashes")
    p.set_defaults(fn=cmd_pipe)

    p = sub.add_parser("payload", help="the real bake, both roads")
    p.add_argument("path", nargs="?", default=DEFAULT_PAYLOAD)
    p.set_defaults(fn=cmd_payload)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)

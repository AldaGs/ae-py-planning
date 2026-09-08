"""
C0.1 client -- ask the PhysBridge AEGP for a scene, over a named pipe.

    python c01_client.py ping
    python c01_client.py read_scene [--out c01_bridge.json]
    python c01_client.py verify c01_dialog.json c01_bridge.json

THE PASS CRITERION, AND WHY IT IS SHAPED THIS WAY
-------------------------------------------------
"Byte-identical to what the save dialog writes" is only meaningful if both
readings see the SAME comp. It cannot be checked against `b1_ae_export.json`
from an earlier session: B2 has since written keyframes onto those layers, so
`b1_read_shapes.jsx` now emits an extra warning about pre-existing Position
keys, and the two files would differ for a reason that has nothing to do with
the bridge.

So the comparison is between two runs in one sitting:

    1. File > Scripts > Run Script File... -> b1_read_shapes.jsx -> c01_dialog.json
    2. python c01_client.py read_scene                          -> c01_bridge.json
    3. python c01_client.py verify c01_dialog.json c01_bridge.json

Any difference is then the bridge's, which is the only question C0.1 asks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

PIPE_TX = r"\\.\pipe\aephys_bridge_tx"      # AE writes, we read
PIPE_RX = r"\\.\pipe\aephys_bridge_rx"      # we write, AE reads

DEFAULT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "b1_read_shapes.jsx")


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


def request(cmd: str, script: str | None = None, timeout=120.0) -> str:
    """Send one request, read one newline-terminated reply.

    The reply is read a chunk at a time until the newline: a scene document is
    far larger than any single pipe read, and treating the first chunk as the
    whole answer is exactly how a payload-size question gets a false pass.
    """
    tx, rx = connect()
    try:
        msg = {"cmd": cmd}
        if script:
            msg["script"] = script
        rx.write((json.dumps(msg) + "\n").encode("utf-8"))
        rx.flush()

        chunks: list[bytes] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = tx.read(65536)
            if not b:
                time.sleep(0.01)
                continue
            chunks.append(b)
            if b.endswith(b"\n") or b"\n" in b:
                break
        data = b"".join(chunks)
        if not data:
            raise SystemExit("the bridge accepted the request but sent nothing "
                             "back within the timeout")
        return data.split(b"\n", 1)[0].decode("utf-8")
    finally:
        tx.close()
        rx.close()


def cmd_ping(args):
    t0 = time.time()
    reply = request("ping")
    print(f"   {reply}   ({(time.time() - t0) * 1000:.0f} ms round trip)")


def cmd_read_scene(args):
    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        raise SystemExit(f"no such script: {script}")

    t0 = time.time()
    reply = request("read_scene", script)
    ms = (time.time() - t0) * 1000

    if reply.startswith('{"ok":false'):
        raise SystemExit("bridge error: " + json.loads(reply)["error"])

    doc = json.loads(reply)          # proves it is a document, not a fragment
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(reply)

    print(f"   {len(reply)} bytes over the pipe in {ms:.0f} ms")
    print(f"   comp {doc['comp']['name']!r} "
          f"{doc['comp']['width']}x{doc['comp']['height']}, "
          f"{len(doc['layers'])} layers, "
          f"{len(doc.get('warnings', []))} warnings")
    print(f"   wrote {args.out}")


def cmd_verify(args):
    a = open(args.dialog, "rb").read()
    b = open(args.bridge, "rb").read()

    # The dialog path writes through a text stream; strip a trailing newline or
    # BOM before comparing, since neither is content.
    def norm(x: bytes) -> bytes:
        if x.startswith(b"\xef\xbb\xbf"):
            x = x[3:]
        return x.strip()

    na, nb = norm(a), norm(b)
    ha = hashlib.sha256(na).hexdigest()
    hb = hashlib.sha256(nb).hexdigest()

    print(f"   dialog : {len(a):7d} bytes  sha256 {ha[:16]}  {args.dialog}")
    print(f"   bridge : {len(b):7d} bytes  sha256 {hb[:16]}  {args.bridge}")

    if na == nb:
        print("\n   PASS  the bridge returns exactly what the save dialog writes")
        return 0

    print("\n   FAIL  the two differ")
    if len(na) != len(nb):
        print(f"         lengths {len(na)} vs {len(nb)}")
    for i, (x, y) in enumerate(zip(na, nb)):
        if x != y:
            lo = max(0, i - 40)
            print(f"         first difference at byte {i}")
            print(f"         dialog: ...{na[lo:i + 40].decode('utf-8', 'replace')}")
            print(f"         bridge: ...{nb[lo:i + 40].decode('utf-8', 'replace')}")
            break
    else:
        longer = "bridge" if len(nb) > len(na) else "dialog"
        tail = (nb if longer == "bridge" else na)[min(len(na), len(nb)):]
        print(f"         identical up to the shorter one; {longer} has "
              f"{len(tail)} extra bytes: {tail[:120].decode('utf-8', 'replace')}")

    # A difference is worth diagnosing structurally too -- a reordered key is a
    # very different problem from a wrong number.
    try:
        da, db = json.loads(na), json.loads(nb)
        print("\n         both still parse as JSON; "
              + ("the DATA is equal, so the difference is formatting or key order"
                 if da == db else "the DATA differs, not just the formatting"))
    except Exception as e:
        print(f"\n         one of them is not valid JSON: {e}")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping").set_defaults(fn=cmd_ping)

    p = sub.add_parser("read_scene")
    p.add_argument("--script", default=DEFAULT_SCRIPT)
    p.add_argument("--out", default="c01_bridge.json")
    p.set_defaults(fn=cmd_read_scene)

    p = sub.add_parser("verify")
    p.add_argument("dialog")
    p.add_argument("bridge")
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)

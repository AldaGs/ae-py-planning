# C0.1 — PhysBridge

**The question:** can an AEGP take a request over a local pipe, run the physics
sim's existing ExtendScript reader through `AEGP_ExecuteScript`, and hand the
scene JSON back down the pipe — byte-identical to what the script's own save
dialog writes?

If yes, Phase C's architecture stands: a Tauri shell outside AE talking to a
native bridge inside it, with B1/B2's already-verified `.jsx` files **reused**
rather than rebuilt as `evalScript` calls. If no, Phase C changes shape.

**Throwaway.** One command, no settings, Windows only. It exists to answer a
question; the answer belongs in the plan, not in this code.

## Where it lives

These files are the **canonical copy**; they are kept here so a throwaway spike
in an untracked SDK folder is not the only copy. They must be **built from the
SDK tree**, because the project's include paths are relative to `Examples/`:

```
C:\AE_SDK\ae25.6_61.64bit.AfterEffectsSDK\Examples\Template\PhysBridge\
    PhysBridge.cpp  PhysBridge.h  PhysBridge_PiPL.r
    Win\  PhysBridge.sln  PhysBridge.vcxproj  PhysBridge_PiPL.rc  resource.h
```

The depth matters: pieFX sits at `Template/pieFX/poc/native/Win`, five levels
below `Examples`, so its project uses `..\..\..\..\..\`. PhysBridge sits at
`Template/PhysBridge/Win`, which is three.

## Build

AE **locks a loaded `.aex`** — close After Effects before rebuilding.

```powershell
$env:AE_PLUGIN_BUILD_DIR = "C:\AE_SDK\_build_out\"
& "C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe" `
  "C:\AE_SDK\ae25.6_61.64bit.AfterEffectsSDK\Examples\Template\PhysBridge\Win\PhysBridge.sln" `
  -p:Configuration=Debug -p:Platform=x64
```

Then the ten-second check that catches the entire "Couldn't find main entry
point (48 :: 72)" class — the export must be a **bare** `EntryPointFunc`, not a
mangled C++ name:

```powershell
dumpbin /EXPORTS C:\AE_SDK\_build_out\AEGP\PhysBridge.aex | findstr EntryPointFunc
```

## Deploy (needs admin)

AEGPs go in a folder under AE's own `Plug-ins`, **not** the shared MediaCore
path the effects use — MediaCore is shared with Premiere, which has no AEGP host.

```powershell
Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-Command',
  'New-Item -ItemType Directory -Force "C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\Plug-ins\PhysBridge" | Out-Null;
   Copy-Item -Force "C:\AE_SDK\_build_out\AEGP\PhysBridge.aex" "C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\Plug-ins\PhysBridge\"'
```

Confirm it loaded: **Window → PhysBridge: bridge status**. It reports whether a
client is connected, how many requests it has served, and the size and duration
of the last result. A log also goes to `%TEMP%\physbridge.log`.

## Run the spike

The pass criterion is "byte-identical to what the save dialog writes", and that
is only meaningful if both readings see the **same comp**. It cannot be checked
against `b1_ae_export.json` from an earlier session: B2 has since written
keyframes onto those layers, so the reader now emits an extra warning about
pre-existing Position keys, and the files would differ for a reason that has
nothing to do with the bridge.

So both readings happen in one sitting:

1. Open the comp. **File → Scripts → Run Script File…** → `b1_read_shapes.jsx`,
   save as `c01_dialog.json` in `python-proto/physics_sim/`.
2. From that folder:
   ```
   python c01_client.py ping
   python c01_client.py read_scene --out c01_bridge.json
   python c01_client.py verify c01_dialog.json c01_bridge.json
   ```

## The one change this forced on B1

`b1_read_shapes.jsx` ended in a save dialog, so it had nothing to return. It now
builds the JSON **once** and only the destination differs: the dialog path is
unchanged, and when the caller prepends `var PHYS_RETURN_JSON = true;` the
script returns the string instead. The bridge prepends exactly that one line and
runs the rest verbatim — which is what makes the byte comparison mean anything,
since both paths format the JSON with the same code.

It also had to stop raising modals in that mode. An `alert()` from a script the
AEGP started **inside its idle hook** would block AE waiting for a click nobody
is there to give, so every user-facing message is guarded and failures come back
as a string the bridge turns into a JSON error.

## What this also measures, for free

C0.2 asks how large a payload `AEGP_ExecuteScript` can carry. Nothing in the
result path uses a fixed buffer — pieFX keeps script results in a 2 KB buffer,
which is right for its toasts and fatal here — so the returned length is logged
and shown in the status dialog. Whatever comes back is a real data point on the
**return** direction, which is the half C0.2 would otherwise have missed.

## Status

Built, exports verified, client written. **Not yet run in AE.**

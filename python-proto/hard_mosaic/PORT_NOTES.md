# Hard Mosaic - port notes

Prototype: `hm_step1_blocks.py` .. `hm_step4_hardening.py` (13/13 checks).
C++ plugin: **https://github.com/AldaGs/ae-hardMosaic** - shipped, verified in
After Effects, Windows + macOS projects, 14/14 offline checks.

## What survived the port unchanged

The rule itself: coverage (mean alpha) decides whether a block exists, a vote
among present colours decides what colour it is. Every number in the
prototype's tables reproduced in C++.

The block loop lives in `HardMosaic_Math.h` behind fetch/store callbacks
specifically so the C++ check harness drives **the same function the plugin
runs**, not a re-implementation of it. A harness that re-implements the loop
tests the harness.

## What the prototype got wrong, and how it showed

Both were caught by writing checks as *measurements with a deliberately broken
control* rather than as assertions - the check printed a number that
contradicted the sentence next to it.

1. **A single-hue test image proves nothing.** `make_source()` is one orange
   rectangle, and averaging one hue cannot produce a *new* hue - so the
   headline failure simply did not appear. `make_source_multi()` exists because
   of that: four flat colours *meeting each other*, inside one soft-edged
   silhouette. Averaging then invents 30-80 colours where there were 4.

2. **"Invents no colour" is the wrong measure for the premultiply trap.**
   Against a *premultiplied* source, treating the input as straight invents
   nothing at all - every darkened fringe value it elects genuinely is a pixel
   of that source. What has gone wrong is not invention, it is that the fringe
   wins votes it should never have been in. The right measure is **count**:
   5 colours become 164.

## What the prototype could not have found

Two performance bugs, both invisible until a UHD frame was timed in C++
(`test/hm_bench.cpp` in the plugin repo):

- Nearest Solid and Average were building the colour histogram and then never
  reading it.
- The vote table probed all 4096 slots per pixel once nearly full. A 128x128
  block of photographic noise turned a 75 ms frame into **11.1 seconds**.
  Bounding the probe to 32 brought the worst case to 221 ms.

Neither is visible in NumPy, where `np.unique` does the histogram and the cost
model is nothing like the C++ one. The lesson is narrower than "prototype
first": prototype the **algorithm**, benchmark the **port**.

## GPU

Measured and declined - see the plugin README. Short version: a UHD float frame
is 133 MB each way, the CPU frame is ~80 ms, and the block statistic is a
per-block hash reduction whose table does not fit in CUDA shared memory, so a
GPU port would need a different algorithm and would drift from the CPU one.

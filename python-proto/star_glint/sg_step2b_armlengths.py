r"""
Star / Glint - Step 2b: PER-ARM LENGTHS (the style knob).

Step 2 gave every arm the same length, which is the one look nobody actually
wants. Real star filters and real lens diffraction almost never produce a
perfectly even star:

    alternate   long/short/long/short - the classic sparkle. An 8-point star
                with 4 long and 4 short arms is the look you see on jewellery
                ads and on almost every game's bloom.
    random      each arm its own length, seeded - organic, dirty-optics feel.
                This is what makes two instances of the effect look different
                instead of stamped.

The loop barely changes. What DOES change is two things that are easy to get
wrong, and both are the actual content of this step.

----------------------------------------------------------------------------
1. LENGTH IS NOT INDEPENDENT OF BRIGHTNESS
----------------------------------------------------------------------------
Look at what step 1 tied together:

    decay = decay_for(length)          per-step dim factor, DERIVED from length
    norm  = (1 - decay) / decay        makes the tail's total energy ~= 1

Step 2 computed both ONCE, outside the arm loop, because every arm shared a
length. The moment arms differ, each arm needs its OWN decay and its OWN norm.
Miss the second one and arm brightness drifts with arm length - so the Variation
control would secretly also be a brightness control, and every time you changed
the style you would have to re-balance Intensity. That is the same "two controls
fighting" problem the /count normalisation solved in step 2, in a new place.

Check (c) below measures it: total energy stays flat across every mode and every
variation amount.

----------------------------------------------------------------------------
2. LENGTH MUST STAY AN UPPER BOUND (a decision driven by the C++ port)
----------------------------------------------------------------------------
The obvious way to randomise is a multiplier around 1.0, say [0.5, 1.5]. Don't.

In AE this effect is SmartFX, and a SmartFX plugin has to tell the host - up
front, before rendering - how far outside the layer its output will reach, so AE
can expand the buffer. If a random multiplier can exceed 1, the true reach is
`length * max_multiplier`, and now the buffer request has to account for a
number that depends on the seed. Ugly, and easy to get subtly wrong (clipped
arms only on certain seeds).

So the multiplier lives in [1 - variation, 1]. `Length` is then exactly the
longest any arm can be, the buffer request is just `Length`, and no seed can
ever clip. The cost is that turning Variation up makes the star smaller on
average - which is predictable and easy to compensate, unlike clipped arms.

Prototype decisions that are really port decisions are worth marking as such.

----------------------------------------------------------------------------
3. DETERMINISM
----------------------------------------------------------------------------
"Random" in an AE effect must mean "random-looking but identical every single
render". If the arm lengths were drawn fresh each frame the star would boil, and
worse, two renders of the same frame would differ. So: a seeded generator, with
Seed exposed as a parameter. Check (b) asserts it.

----------------------------------------------------------------------------
4. ONE STAR SHAPE PER LAYER, NOT PER HIGHLIGHT
----------------------------------------------------------------------------
Worth knowing before you look at the renders: every highlight in the frame gets
the SAME set of arm lengths. That is not a shortcut, it is correct - the star
shape is a property of the LENS, not of the light, so every source seen through
the same optics gets the same signature. (Look at any real anamorphic still:
every practical light flares identically.)

If you ever DID want per-highlight variation you could not do it this way at
all. The whole reason this is fast is that one sweep smears every highlight at
once; making each light differ means identifying individual lights first, which
is a segmentation problem and a completely different (and far more expensive)
effect.

Run:
    python sg_step2b_armlengths.py              # verify + render
    python sg_step2b_armlengths.py --explain    # print the per-arm table
"""

import sys

import numpy as np

import sg_step1_streak as s1
import sg_step2_star as s2

bright_pass = s1.bright_pass
streak_arm = s1.streak_arm
decay_for = s1.decay_for
save_linear = s1.save_linear
lateral_blur = s2.lateral_blur
arm_angles = s2.arm_angles


# ===========================================================================
#  the new bit: one length per arm
# ===========================================================================

LENGTH_MODES = ("uniform", "alternate", "random")


def arm_lengths(count, length, mode="uniform", ratio=0.5, variation=0.5, seed=0):
    """The per-arm length list. `length` is always the MAXIMUM (see docstring).

    uniform    every arm == length
    alternate  even arms == length, odd arms == length * ratio
    random     each arm == length * u,  u ~ Uniform[1 - variation, 1], seeded

    Returns floats clamped to >= 1.0, because an arm shorter than a pixel is
    just the core, and decay_for would divide by ~0.
    """
    if mode == "uniform":
        mult = np.ones(count, np.float64)
    elif mode == "alternate":
        mult = np.array([1.0 if i % 2 == 0 else ratio for i in range(count)])
    elif mode == "random":
        rng = np.random.default_rng(seed)          # seeded => frame-stable
        mult = rng.uniform(1.0 - variation, 1.0, count)
    else:
        raise ValueError(f"unknown length mode {mode!r}")
    return np.maximum(mult * length, 1.0)


def star(lin, count=4, base_angle=0.0, length=200.0, intensity=0.9,
         threshold=1.0, knee=0.5, normalise=True, tip=0.02, width=3.0,
         mode="uniform", ratio=0.5, variation=0.5, seed=0):
    """Step 2's star, but each arm carries its own length.

    The ONLY structural change from step 2: decay and the tail normalisation
    move INSIDE the loop, because they are both functions of that arm's length.
    """
    bright = bright_pass(lin, threshold, knee)
    lengths = arm_lengths(count, length, mode, ratio, variation, seed)

    acc = np.zeros_like(bright)
    for ang, L in zip(arm_angles(count, base_angle), lengths):
        decay = decay_for(L, tip)                  # per arm now
        norm = (1.0 - decay) / decay               # per arm now
        tail = streak_arm(bright, ang, L, decay) - bright
        acc += norm * lateral_blur(tail, ang, width * 0.5)

    scale = intensity / count if normalise else intensity
    return scale * acc


# ===========================================================================
#  measuring an arm's actual reach (so "length" can be checked, not trusted)
# ===========================================================================

def arm_reach(field, angle, floor=1.0 / 255.0, max_r=None):
    """How far along `angle` the streak is still above `floor`. Centre-origin."""
    h = field.shape[0]
    c = (h - 1) * 0.5
    max_r = int(c - 2) if max_r is None else max_r
    last = 0
    for r in range(2, max_r):
        e, _ = s2._arm_profile(field[..., 0] if field.ndim == 3 else field,
                              angle, float(r))
        if e > floor:
            last = r
    return last


# ===========================================================================
#  --explain
# ===========================================================================

def explain(count=8, mode="random", length=220.0, variation=0.6, seed=3,
            ratio=0.45, base_angle=0.0):
    print(f"\nARM LENGTHS  mode={mode}  count={count}  length={length}  "
          f"ratio={ratio}  variation={variation}  seed={seed}\n")

    for m in LENGTH_MODES:
        Ls = arm_lengths(count, length, m, ratio, variation, seed)
        print(f"  {m:>9}: " + "  ".join(f"{L:6.1f}" for L in Ls))
    print()

    lin = s1.synthetic_points()
    Ls = arm_lengths(count, length, mode, ratio, variation, seed)
    angles = arm_angles(count, base_angle)
    print("     arm   angle   length     decay      tail norm")
    for i, (a, L) in enumerate(zip(angles, Ls)):
        d = decay_for(L)
        print(f"     {i:3d}   {a:5.1f}   {L:6.1f}   {d:.5f}   {(1-d)/d:9.5f}")
    print("\n  Note how decay AND the norm both track the arm's own length -")
    print("  that pairing is what keeps every arm equally bright (check c).\n")

    st = star(lin, count=count, base_angle=base_angle, length=length,
              intensity=1.0, mode=mode, ratio=ratio, variation=variation, seed=seed)
    save_linear(f"explain2b_{mode}.png", lin + st)
    print(f"  wrote explain2b_{mode}.png\n")


# ===========================================================================
#  main
# ===========================================================================

def main():
    ok = True

    print("=== (a) the new modes reduce to step 2 at their identity settings ===")
    # ratio=1 and variation=0 must both give back a plain uniform star, exactly.
    # If a style control cannot be turned off, it is not a style control.
    lin = s1.synthetic_points()
    base = star(lin, count=8, length=180.0, mode="uniform")
    for label, kw in (("alternate, ratio=1.0", dict(mode="alternate", ratio=1.0)),
                      ("random,    variation=0", dict(mode="random", variation=0.0)),
                      ("random,    other seed", dict(mode="random", variation=0.0,
                                                     seed=99))):
        got = star(lin, count=8, length=180.0, **kw)
        err = float(np.abs(got - base).max())
        good = err < 1e-6
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] {label:<24} max|diff| = {err:.2e}")

    print("\n=== (b) random is DETERMINISTIC: same seed -> same star ===")
    # Non-negotiable for AE. A star that redraws itself every render boils.
    a = star(lin, count=8, length=180.0, mode="random", variation=0.6, seed=7)
    b = star(lin, count=8, length=180.0, mode="random", variation=0.6, seed=7)
    c = star(lin, count=8, length=180.0, mode="random", variation=0.6, seed=8)
    same = float(np.abs(a - b).max())
    diff = float(np.abs(a - c).max())
    good = same < 1e-9 and diff > 1e-4
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] seed 7 vs 7: {same:.2e} (want 0)   "
          f"seed 7 vs 8: {diff:.2e} (want > 0)")

    print("\n=== (c) style does not change brightness ===")
    # The reason decay and norm moved inside the loop. Total energy must be flat
    # across every mode and every variation amount, or Variation is secretly an
    # Intensity control and the two knobs fight each other.
    pt = s2.single_point()
    energies = []
    print("        mode                       total energy")
    cases = [("uniform", dict(mode="uniform")),
             ("alternate ratio=0.25", dict(mode="alternate", ratio=0.25)),
             ("alternate ratio=0.50", dict(mode="alternate", ratio=0.50)),
             ("random    var=0.3", dict(mode="random", variation=0.3, seed=1)),
             ("random    var=0.6", dict(mode="random", variation=0.6, seed=2)),
             ("random    var=0.9", dict(mode="random", variation=0.9, seed=3))]
    for label, kw in cases:
        e = float(star(pt, count=8, length=150.0, intensity=1.0, **kw).sum())
        energies.append(e)
        print(f"        {label:<26} {e:9.4f}")
    spread = (max(energies) - min(energies)) / max(energies)
    good = spread < 1e-2
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] spread across all styles = {spread:.2e}")

    print("\n  (for contrast: same test with the per-arm norm REMOVED)")
    # Deliberately reproduce the bug, to show the check is not vacuous.
    def star_buggy(lin_, **kw):
        mode = kw.pop("mode", "uniform")
        bright = bright_pass(lin_, 1.0, 0.5)
        Ls = arm_lengths(8, 150.0, mode, kw.get("ratio", 0.5),
                         kw.get("variation", 0.5), kw.get("seed", 0))
        d0 = decay_for(150.0)                       # ONE decay for all arms
        acc = np.zeros_like(bright)
        for ang, L in zip(arm_angles(8, 0.0), Ls):
            acc += lateral_blur(streak_arm(bright, ang, L, d0) - bright, ang, 1.5)
        return ((1.0 - d0) / d0 / 8.0) * acc
    be = [float(star_buggy(pt, **dict(kw)).sum()) for _, kw in cases]
    print(f"        buggy spread = {(max(be)-min(be))/max(be):.2e}  "
          f"(min {min(be):.3f}, max {max(be):.3f})")

    print("\n=== (d) alternate really does alternate ===")
    # Measure the reach of an even arm vs an odd arm and check the ratio.
    pt = s2.single_point(601, 8.0)
    st = star(pt, count=4, base_angle=0.0, length=200.0, intensity=1.0,
              mode="alternate", ratio=0.5)
    r_even = arm_reach(st, 0.0)
    r_odd = arm_reach(st, 90.0)
    got = r_odd / max(r_even, 1)
    good = 0.35 < got < 0.75
    ok &= good
    print(f"  [{'ok  ' if good else 'FAIL'}] reach even={r_even}px  odd={r_odd}px  "
          f"ratio={got:.2f} (asked for 0.50)")
    print("  (reach is where the arm falls under 1/255, so it tracks the ratio")
    print("   without matching it exactly - a dimmer arm also dies sooner.)")

    print("\n=== (e) renders ===")
    lin = s1.synthetic_points()
    configs = [
        ("alt4_sparkle", dict(count=4, length=260.0, mode="alternate", ratio=0.35)),
        ("alt8_sparkle", dict(count=8, length=260.0, mode="alternate", ratio=0.40)),
        ("alt8_subtle", dict(count=8, length=220.0, mode="alternate", ratio=0.75)),
        ("rand8_s1", dict(count=8, length=260.0, mode="random", variation=0.6, seed=1)),
        ("rand8_s2", dict(count=8, length=260.0, mode="random", variation=0.6, seed=2)),
        ("rand8_wild", dict(count=8, length=260.0, mode="random", variation=0.9, seed=5)),
        ("rand6_dirty", dict(count=6, base_angle=15.0, length=260.0, mode="random",
                             variation=0.7, seed=4)),
    ]
    for name, kw in configs:
        st = star(lin, intensity=1.0, **kw)
        save_linear(f"out_sg2b_{name}.png", lin + st)
        print(f"  wrote out_sg2b_{name}.png  {kw}")

    print("\nstep 2b validated." if ok else "\n*** step 2b FAILED ***")


if __name__ == "__main__":
    if "--explain" in sys.argv:
        explain()
    else:
        main()

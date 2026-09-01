# How MTOM and range are computed

Plain-language companion to the code. The full equations, parameter table and citations are
in [`mtom_and_reserve_range_methodology.md`](../mtom_and_reserve_range_methodology.md); this
file is the readable summary. Numbers below are from iteration 5 (`P1_test.stl`, L/D 14.38).

---

## Range: two numbers, and which one matters

`results.json` reports **two** range figures. They are not alternatives — one is a legacy
number kept for continuity.

### The reserve-based range — the one to trust

Weight is tracked through eleven segments, starting at MTOM:

```
taxi-out → takeoff → climb → CRUISE (the trip) → descent → approach
  → missed approach → climb + 45 min hold → 100 nm diversion → land at alternate
```

Everything after the destination approach is reserve. On top of that, 5 % of trip fuel is
held back as contingency and never burned. The **forward** solve asks "given the fuel that
fits in the tank, how far can the trip leg be and still close this whole chain?" The
**inverse** solve asks the opposite — "for a target range, how much fuel, and therefore how
much tank volume, is needed?" — which generalises the hand calculation that 4,000 nm needs
~16 m³.

For iteration 5 this gives **1,392 nm**, and the segment breakdown is reported per-leg
because that per-segment read is what catches modelling errors:

| | fuel | share of loaded fuel |
|---|---:|---:|
| trip (taxi → destination) | 478 kg | 75.1 % |
| reserves + contingency | 159 kg | **24.9 %** |

That a quarter of the tank never moves the aircraft toward its destination is a real result,
not an intermediate. A fixed 100 nm diversion and 45-minute hold take a far bigger bite out
of an LH2 fuel fraction (~9 % of MTOM) than the same policy takes from a kerosene aircraft's
(~35 % of MTOM). It is the tank-volume-is-binding finding seen from another angle.

**One correction worth knowing about.** The five segment weight fractions (taxi 0.995, climb
0.980, …) are calibrated on kerosene aircraft, where they encode roughly how much *energy* a
segment needs. Applied directly as LH2 *mass* fractions they overstate the burn by about the
energy-density ratio (LH2 ≈ 120 vs Jet-A ≈ 43 MJ/kg). Uncorrected, climb alone ate 23 % of
the tank and the reserve profile came out 83–86 % below the quick estimate. Rescaled to hold
segment energy constant, climb is 8 % and the reduction settles at 44–47 %. That correction
is on by default (`lhv_scaling_enabled: true`); set it false only to reproduce the
uncorrected result.

### The quick estimate — legacy, optimistic

`results["range"]` is single-segment Breguet with 100 % of the tank burned in cruise: no
taxi, climb, descent, diversion, hold or contingency. For iteration 5 it says 2,413 nm —
about **45 % optimistic**.

It is still reported for exactly one reason: iterations 1–3 were logged against it and
Fixture D validates against it unchanged. It is not the better number.

---

## MTOM: computed, not assumed

MTOM used to be a fixed 7,300 kg input. It is now a converged output, with 7,300 kg demoted
to a pass/fail gate on that output.

```
MTOM = OEW + payload + crew + fuel
OEW  = structure + propulsion + systems + tank system
```

Iteration 5:

| group | mass | note |
|---|---:|---|
| centerbody structure | 2 832 kg | σ × centerbody area — **largest term, unsourced σ** |
| systems | 921 kg | lumped statistical fraction of MTOM |
| propulsion | 702 kg | specific weight × thrust from T/W sizing |
| tank system | 637 kg | LH2 tanks, from η_g |
| outer wing | 304 kg | Raymer regression, outer panel only |
| landing gear | 248 kg | fraction of MTOM |
| **OEW** | **5 643 kg** | |
| payload | 600 kg | 6 passengers |
| crew | 200 kg | 2 crew |
| fuel (LH2) | 637 kg | fixed by tank volume |
| **MTOM** | **7 081 kg** | |

**Why the structure is split in two.** A BWB has no separable fuselage, so there is no
fuselage length to feed a tube-and-wing weight equation. The centerbody carries the
pressurised cabin and the tank system; the outer wing does not. The outer wing uses a
conventional regression on outer-panel-only geometry (extracted from the AVL section cards,
split at 35 % of half-span); the centerbody uses an areal-density method, σ × area. Elevons
and drag rudders are integrated into that structure, so there is deliberately no separate
empennage group.

**The convergence loop.** Wing, gear, systems and propulsion all scale with MTOM, and MTOM is
what's being solved for. So: guess MTOM, compute the groups, add payload + crew + fuel, and
repeat until it stops moving. Fuel mass does *not* depend on MTOM — it is fixed by tank
volume, a geometric constraint — which is what makes this converge quickly instead of running
away. The loop iterates to machine closure rather than stopping at the 0.1 % tolerance,
because the default starting guess is the cap, and stopping early would let the cap leak into
the result it is supposed to be judging.

**The cap is a gate, never an input.** A converged MTOM above 7,300 kg is a finding requiring
redesign, not something to tune away. Raising the cap changes only the verdict, never the
converged mass — there is a test that asserts exactly that.

**The PC-24 cross-check.** This exists to catch one specific embarrassment: the LH2-BWB
coming out structurally *lighter* than a PC-24 while being larger and, by this programme's
own finding, structurally harder. It works backwards from the one hard published number:

```
PC-24 implied structure = published OEW (5 300 kg)
                        − THIS MODEL's propulsion at PC-24 MTOW
                        − THIS MODEL's systems at PC-24 MTOW
```

That is **not** a published PC-24 structural weight — Pilatus doesn't publish one — so it
inherits any error in this model's propulsion and systems groups. Currently the BWB comes out
at 0.461 structural fraction against the PC-24's implied 0.409, i.e. heavier, which is the
expected direction. If that ever inverts, the flag demands a real structural-efficiency
citation rather than a shrug.

---

## Reading the gate verdict honestly

At the default σ = 60 kg/m², iteration 5 converges to 7,081 kg and **passes** the 7,300 kg cap
by 220 kg (3.0 %). That is not the whole story, and `results.json` says so itself via
`sizing.gate.verdict_decided`:

| σ (kg/m²) | MTOM | verdict | | η_g | MTOM | verdict |
|---:|---:|---|---|---:|---:|---|
| 40 | 5 758 | PASS | | 0.30 | 8 268 | FAIL |
| **60** | **7 081** | **PASS** | | 0.40 | 7 526 | FAIL |
| 80 | 8 399 | FAIL | | **0.50** | **7 081** | **PASS** |
| 100 | 9 715 | FAIL | | 0.60 | 6 783 | PASS |

The verdict flips inside the plausible range of *both* inputs, and both are unsourced. So the
honest reading is **not** "MTOM is 7,081 kg and passes" — it is *"MTOM lands within a few
percent of the cap, and which side it lands on is currently decided by two numbers nobody has
sourced."* Sourcing σ is the single highest-value open action in the model.

The pipeline computes this rather than narrating it: if the verdict flips anywhere in the
sweep, `verdict_decided` is `false` and the framing string in `results.json` and
`log_entry.md` says UNDECIDED in words.

---

## Deferred, on purpose

The AVL trim CL target still uses the **assumed** 7,300 kg, not the converged MTOM. The more
correct cruise weight is the geometric mean √(Wi·Wf), but adopting it would silently move
Fixture D's validated aero numbers. If it is ever implemented it must be an explicit opt-in
alongside the existing MTOM-based target, not a silent replacement.

# MTOM Closure & Reserve-Based Range Methodology

Responds directly to three findings in the latest review: no reserves in the range calc
(34% optimistic), MTOM is an unverified assumption with no mass model behind it, and tank
volume — not aerodynamics — is the binding constraint. This document specifies the
methodology for both fixes and how they're coupled, then hands it to Claude Code as an
implementation spec.

## What changes, what doesn't

- The existing `mission.compute_range()` (Stage 1, single-segment Breguet, 100% fuel burned
  in cruise) is **not deleted**. It stays, relabeled as a quick/optimistic estimate — the
  same way `results.json` already carries a `drag_model_note` flagging CD0 as
  ranking-only, not absolute. Fixture D's three validated iterations keep working
  unchanged against it.
- A new, separate reserve-based mission profile is added alongside it. `results.json` and
  `log_entry.md` report both, clearly labeled, so nobody mistakes one for the other.
- MTOM changes from a fixed input in `mission.yaml` to a **computed, converged output** of
  a real mass model — with the 7,300 kg figure now a hard pass/fail gate on that output,
  not an assumption baked into the calculation.

---

## Part A — Reserve-based range methodology

### Regulatory/industry basis

- **FAR 91.167** (legal minimum, U.S. Part 91): fuel to fly to destination, then to an
  alternate if required, then 45 minutes at normal cruise.
- **NBAA IFR reserves** (industry-standard, more conservative, what quoted bizjet ranges
  are typically built on): missed approach, climb and hold briefly for clearance, divert to
  an alternate at long-range cruise power, then land with reserve fuel remaining. Sources
  disagree on the exact diversion distance (100 nm has become common industry practice;
  200 nm is the more traditional, more conservative NBAA figure) and the exact hold
  parameters — treat both as **configurable, not fixed truths**, and say so in any
  documentation this feeds.
- **Contingency fuel** (5% of trip fuel) is standard commercial/international flight-planning
  practice, not a Part 91 legal requirement — adopted here as a reasonable conservative
  margin, labeled honestly as borrowed from commercial practice rather than mandated for
  this class of operation.

None of this is implemented against the primary NBAA document text — it's a defensible,
clearly-labeled engineering approximation of publicly described practice, not a certified
fuel-planning tool. Say so in the code comments and in any report this feeds investors or
certification discussions.

### Segment sequence

Weight tracked sequentially, in order, starting from MTOM:

| # | Segment | Method |
|---|---|---|
| 1 | Start | W₀ = MTOM |
| 2 | Taxi-out | W₁ = W₀ × f_taxi |
| 3 | Takeoff | W₂ = W₁ × f_takeoff |
| 4 | Climb to cruise altitude | W₃ = W₂ × f_climb |
| 5 | **Main cruise** | Breguet, solved either direction (below) |
| 6 | Descent | W₅ = W₄ × f_descent |
| 7 | Approach at destination | W₆ = W₅ × f_approach |
| 8 | Missed approach / go-around | Fixed fuel mass allowance (short duration, high thrust — not a distance-scaled fraction) |
| 9 | Climb + hold for clearance | Physics-based: fuel = TSFC × (W₇·g / (L/D)_loiter) × hold_time_s |
| 10 | Diversion cruise to alternate | Breguet, solved for fuel given fixed diversion distance |
| 11 | Land at alternate | W_final = W_after_diversion × f_approach |

**Two solve directions, both needed** (this is what makes it useful for the colleague's
tank-volume/range tradeoff work, not just a stricter number):

- **Forward** (what's needed right now): given available LH2 mass (from tank volume ×
  density × fill fraction — the actual binding constraint per the latest finding), solve
  segment 5 for the maximum trip cruise distance that still closes the whole chain with
  fuel to spare.
- **Inverse**: given a target trip range, solve for the LH2 mass — and therefore tank
  volume — required. This directly reproduces and generalizes the "closing to 4,000 nm
  needs ~15.9 m³" calculation already done by hand, so it doesn't need to be redone
  manually next time.

Segment 5's Breguet form is unchanged from what's already implemented — only the weights
feeding it and what's subtracted before/after it are new.

### Handling the contingency circularity

Contingency (5% of trip fuel) depends on trip fuel, which depends on the cruise distance
being solved for — a small implicit loop. Don't hunt for closed-form algebra: iterate
(guess trip fuel → compute contingency → resolve → repeat). Converges in 2–3 iterations
given contingency is a small correction; use a simple fixed-point loop, not a general solver.

### Loiter L/D — an intentionally conservative simplification

True loiter/max-endurance L/D is higher than max-range cruise L/D (different trim CL
entirely — would need a separate AVL sweep to get properly). For now, **use the same L/D
as cruise for the hold segment**. This overestimates hold fuel burn, which is the safe
direction for a reserve calculation — state this explicitly in code comments as a
deliberate conservative choice, not an oversight, so nobody "fixes" it into something
less conservative without realizing why it was there.

### Default parameters (starting values — flag confidence honestly, don't overclaim precision)

| Parameter | Default | Basis |
|---|---|---|
| f_taxi | 0.995 | Literature-typical business-jet-class value (Raymer-style mission segment fractions) — kerosene-turbine-calibrated, not yet validated for LH2 |
| f_takeoff | 0.995 | Same basis |
| f_climb | 0.980 | Same basis |
| f_descent | 0.990 | Same basis |
| f_approach | 0.992 | Same basis, applied once at destination, once at alternate |
| Missed approach allowance | ~2 min at high-thrust fuel flow | Placeholder, not literature-sourced — flag for refinement |
| Diversion distance | 100 nm | Common current industry practice; make configurable, 200 nm is the more conservative traditional NBAA figure |
| Hold time | 45 min | FAR 91.167 legal minimum, chosen as the more defensible default over NBAA's ~30 min figure given sourcing disagreement |
| Hold altitude | 1,500 ft | Representative, affects density in the loiter fuel-flow calc |
| Contingency | 5% of trip fuel | NBAA international flight-plan guidance, borrowed here as a conservative margin, not a domestic Part 91 requirement |

### Validation target

The colleague's manual estimate found the current no-reserve range ~34% optimistic. Once
built, recompute iteration 1/2/3 through the new profile and confirm the reduction lands in
a comparable range (roughly 25–35%) as a plausibility check — not a hard pass/fail like
Fixture D, since this is new capability, not a reproduction of an existing manual answer.
If the delta comes out wildly outside that band, that's worth understanding before trusting
it, not silently accepting.

---

## Part B — MTOM closure methodology

### Structure

```
MTOM = OEW + m_payload + m_crew + m_fuel(LH2)
OEW  = m_structure + m_propulsion + m_systems + m_tank_system
```

### Structure group — split by role, not treated as one generic fuselage+wing

A BWB doesn't have a separable fuselage, and the centerbody does a different structural job
than the outer wing (integrates the pressurized cabin and the tank system; the outer wing
doesn't) — model them separately, not with a single tube-and-wing weight equation:

- **Outer wing structure**: standard conceptual-design regression (Torenbeek- or
  Raymer-style: function of MTOM, Sref, AR, sweep, taper, ultimate load factor, t/c).
  Well-established, but calibrated on conventional aircraft — flag explicitly as a
  starting point pending the BWB-specific methods already in the project's own literature
  base (Hansmann & Stumpf UNICADO, the NASA BWB sizing report) once there's time to
  implement those properly.
- **Centerbody structure**: areal-density method (mass = σ × centerbody wetted/planform
  area), not a fuselage-length equation — there is no fuselage length. σ should come from
  BWB/HWB structural literature (Liebeck, NASA BWB sizing report — already in the project's
  reference set), not invented. Flag the exact σ value used as a citation-needed
  placeholder until sourced properly.
- **Landing gear**: standard fraction of MTOM (~3–4% is typical for this class) —
  literature-typical, not a program-specific number.
- **Empennage/control surfaces**: if elevons/drag rudders are integrated into the
  wing/centerbody structure rather than separate surfaces (per the program's existing
  terminology), fold this into the structure above rather than double-counting a separate
  empennage group.

### Propulsion group

Engine mass from specific weight (kg/kN or similar) for the assumed thrust class × required
thrust from T/W sizing, plus nacelle and accessories. Actual LH2-combustor-specific engine
weight data isn't public yet (per the program's own engine-OEM-partnership framing) — treat
this as a placeholder pending real data, not a firm number.

### Systems group

Flight controls, avionics, electrical, hydraulic/pneumatic, ECS, furnishings — standard
statistical weight fractions of MTOM/OEW for business-jet class (Raymer/Torenbeek-style).
Same caveat as the structure regressions: conventional-aircraft-calibrated, first-order only.

### Tank system — the LH2-specific piece, and the one that actually matters most here

```
m_tank_system = m_LH2_total × (1/η_g − 1)
```

Default `η_g = 0.50` as a starting point (between the program's near-term-metallic ~0.30 and
the 55–57% economic crossover target from the Greenleaf strategy doc) — configurable, and
this is the single highest-leverage number in the whole mass model given how much the
9× volume-vs-span-efficiency finding already shows fuel/tank mass dominates.

**This should be a modular input, not something this pipeline owns end-to-end.** If a more
detailed tank mass number is available from the separate tank-packaging analysis already
under way (the 2-vs-4-tank, pressure-vessel-wall-thickness, MLI-insulation work) —
whether from Huete & Pilidis' method already in the project's reference set, or the actual
packaging result — that number should be used in preference to the η_g proxy, with the
proxy as the fallback when a detailed number isn't available yet. Give it an explicit
override slot in config, exactly like `s_wet_override_m2` already works for wetted area.

### Iterative convergence loop

MTOM appears on both sides — wing/gear/systems weights scale with it, and it's also what's
being solved for:

1. Guess MTOM (start at the 7,300 kg cap, or the previous converged value on subsequent runs)
2. Compute OEW components that depend on the guess (wing structure, landing gear, systems)
3. Compute tank system mass from available/target LH2 fuel mass (Part A, either direction)
4. MTOM_new = OEW + payload + crew + fuel
5. If |MTOM_new − guess| / guess exceeds tolerance (e.g. 0.1%), set guess = MTOM_new, repeat
6. Once converged: check against the 7,300 kg cap as a **pass/fail gate**, matching the
   existing gate_passed pattern. **Never adjust the model to force convergence under the
   cap** — a converged MTOM above 7,300 kg is a real finding requiring redesign, not
   something to fudge. This mirrors the standing instruction already governing this whole
   program: the cap doesn't move to make a concept work.

### Validation — operationalizing "we need some way to verify this"

Run the same group-weight-buildup method against PC-24's own published MTOW/OEW/geometry as
a calibration check. If the LH2-BWB's computed *structural* mass fraction (excluding the
tank system, which has no kerosene equivalent) comes out lower than PC-24's actual
structural fraction despite being similar-or-larger and, per the program's own established
finding, structurally harder — that's a red flag requiring an actual citation for why a BWB
would be lighter here (real structural-efficiency literature, not an assumed benefit),
before trusting the number. This is the direct answer to "implies the BWB weighs less than
a PC-24 while being larger and structurally harder" — don't resolve that tension by picking
a number that feels right; resolve it by checking against real calibration data and citing
the justification if the numbers diverge.

---

## Part C — How the two parts couple

Tank volume is a **geometric/packaging constraint** (fixed by what physically fits in the
centerbody, independent of MTOM). Available fuel mass follows from it directly. Range
follows from available fuel mass via Part A. Meanwhile OEW depends on the MTOM guess in the
convergence loop (Part B), and tank *system* mass (structure, not fuel) depends on how much
fuel mass is being carried. So the two parts run inside the same outer convergence loop:
Part B's iteration calls Part A (forward direction: available fuel → achievable range) each
pass, using whatever fuel mass the current MTOM guess implies is available, until both MTOM
and the resulting range/fuel numbers stop moving.

**One deferred refinement, explicitly not required today:** the AVL trim CL target
currently uses raw MTOM (Stage 4). Once a real Wi/Wf schedule exists from Part A, the more
correct cruise weight for CL trim is the geometric mean √(Wi·Wf) — which is literally what
the Greenleaf strategy document's own methodology already specifies. Don't implement this
now — it would silently change Fixture D's validated numbers. Flag it as a known, deferred
improvement, and if it's ever implemented, do it as an explicit opt-in alongside the
existing MTOM-based CL target, not a silent replacement.

---

## Implementation stages for Claude Code

### Stage 10 — Reserve-based mission profile (Part A)

```
Implement the segment-sequence mission profile from Part A of the methodology doc as
pipeline/mission_profile.py, separate from the existing mission.compute_range() (which
stays as-is, relabeled in its docstring/output as the quick/no-reserve estimate — don't
delete or modify its behavior, Fixture D depends on it unchanged).

Implement both solve directions: forward (available fuel mass -> max achievable trip
range) and inverse (target trip range -> required fuel mass / tank volume). Use the
default parameters table from the methodology doc, added as new fields in mission.yaml,
all configurable. Implement the contingency circularity as a small fixed-point iteration
(2-3 passes), not closed-form algebra. Use the same L/D for the loiter segment as cruise,
with a code comment explaining this is a deliberate conservative simplification, not an
oversight -- don't "improve" it without discussion.

Recompute iterations 1/2/3 through this new profile (using their already-known L/D, e,
and geometry from Fixture D) and report the resulting range alongside the existing
no-reserve numbers, plus the percentage reduction. Compare against the ~25-35% reduction
already estimated by hand -- report the actual numbers, flag clearly if it's outside that
band rather than assuming it's fine.
```

### Stage 11 — Mass model (Part B)

```
Implement pipeline/mass_model.py per Part B of the methodology doc: the group weight
breakdown, the tank system mass via the eta_g proxy (default 0.50, configurable, with an
explicit override slot matching how s_wet_override_m2 already works, for when a more
detailed tank-packaging mass number is available), and the iterative MTOM convergence
loop.

Use standard literature-typical regression equations for outer wing structure, landing
gear, and systems fractions -- cite which method/source each equation is based on in a
comment, and flag them explicitly as conventional-aircraft-calibrated placeholders, not
BWB-validated. For centerbody structure, implement the areal-density method (sigma x
centerbody area); leave sigma as a clearly labeled placeholder value pending a literature
source, don't invent a number and present it as sourced.

Implement the PC-24 structural-fraction cross-check from Part B's Validation section as an
explicit function, not just a comment -- it should actually run the same buildup logic
against PC-24's published MTOW/OEW and flag if the LH2-BWB's structural fraction comes out
lower without justification.

Gate the converged MTOM against the 7,300 kg cap using the existing gate_passed pattern.
Report the actual converged MTOM, whether it passes the cap, and the PC-24 cross-check
result -- don't just confirm the code runs, run it against a real case (P1.stl / iteration
3, using the L/D and e already known from Fixture D) and report real numbers.
```

### Stage 12 — Couple them, update results.json and log_entry.md

```
Wire Stage 10 and Stage 11 together per Part C's outer convergence loop. Extend
results.json to carry both the existing no-reserve quick estimate AND the new
reserve-based range, both the assumed and the newly-converged MTOM, and the mass model's
gate result -- clearly labeled, not replacing the old fields. Extend log_entry.md's
Conclusion line to state both range numbers and flag if the converged MTOM fails the
7,300 kg gate. Update running_log.md's existing entries only by appending a note if you
recompute them under the new methodology -- don't silently rewrite history that's already
been reviewed and logged.
```

### Stage 13 — Document the methodology in README.md

```
Add a section to README.md explaining, in plain language a colleague could follow without
reading the source, how MTOM and range are actually computed:

- MTOM: the group weight breakdown at a glance (structure/propulsion/systems/tank/payload/
  crew), the iterative convergence loop in a few sentences, and the PC-24 structural-
  fraction cross-check -- what it checks and why it exists.
- Range: the reserve-based segment sequence at a glance (taxi through landing at the
  alternate), forward vs. inverse solve, and what the quick/no-reserve number in
  results.json still means now that it's not the primary number.
- A visible, unmissable list of every placeholder this stage introduced -- eta_g default,
  centerbody structural areal density (sigma), engine specific weight, the missed-approach
  fuel allowance -- so nobody downstream mistakes an early placeholder for a validated
  number. Don't bury these in prose; a short bulleted list is better than having them
  scattered through paragraphs.
- A one-line pointer to this methodology doc (mtom_and_reserve_range_methodology.md) for
  the full equations, parameter table, and citations -- don't duplicate the whole spec
  into the README, summarize it.
- One explicit sentence stating which range number to trust for real decisions (the
  reserve-based one) and why the quick estimate is still reported at all (backward
  compatibility with already-logged iterations, not because it's the better number).

This is new content on top of the README's original "10 minutes to first run" quickstart
goal from the earlier finishing stage -- keep this new section similarly scoped and
readable, not a copy-paste of this whole document. If it's running long enough to threaten
that goal, split it into a separate docs file (e.g. docs/mtom_and_range.md) and link it
from README.md instead of inlining it -- use judgment, but don't let this stage's
documentation bury the original quickstart under it.
```

## A note on scope

This is intentionally not a certification-grade fuel-planning or structural-sizing tool —
it's a conceptual-design-stage methodology built to be honest about its own placeholders,
matching how the rest of this pipeline has been built tonight. The regressions, sigma
value, engine specific weight, and eta_g default all need real literature sourcing or
program-specific data before this becomes something to put in front of an investor without
caveats. Treat every "literature-typical" and "placeholder" flag in this document as a
tracked TODO, not a solved problem.

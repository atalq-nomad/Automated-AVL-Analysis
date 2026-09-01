# LH₂ BWB Aero Pipeline

Takes a blended-wing-body STL, extracts an AVL vortex-lattice model from it, runs AVL at the
cruise CL, and returns L/D plus a Breguet cruise-range estimate — with geometry sanity checks
and AVL log validation as hard gates, so a converged-but-wrong result cannot pass silently.

## Prerequisites

- **Python 3.11.5** (what this was built and validated on).
- `pip install -r requirements.txt`
- **AVL 3.52**, obtained separately from <https://web.mit.edu/drela/Public/web/avl/>. It is
  GPL-licensed and **not included in this repo**. Put `avl352.exe` in the repo root, or point
  `avl_exe` in your case YAML at it. This pipeline was built and validated against **v3.52
  specifically** — the run script drives AVL's OPER menu by keystroke, so a different version
  whose prompts differ may hang waiting on stdin or write the wrong files.
- **A geometry STL.** Not in the repo (see `.gitignore`); geometry is distributed separately.
  Point `stl_path` in your case YAML at it.

## Quickstart

Run the tests **first**. They include the Fixture D regression against three hand-validated
iterations and need neither AVL nor an STL — if they pass, the physics and parsing are intact
in your environment, and anything that fails afterwards is environmental rather than a broken
pipeline.

```bash
pip install -r requirements.txt
python -m pytest tests -q          # expect: all passed
```

Then run a case. This needs AVL and an STL in place (see Prerequisites):

```bash
# geometry only — cheap, and the fastest way to check a new axes: value
python pipeline/run_case.py cases/iteration_4.yaml mission.yaml --skip-avl

# the full run
python pipeline/run_case.py cases/iteration_4.yaml mission.yaml

# once you have more than one iteration
python pipeline/compare.py
```

`mission.yaml` holds what must stay identical across iterations (cruise condition, MTOM, Cfe,
TSFC, tank sizing). `cases/*.yaml` holds only per-geometry settings. If `run_case.py` stops at
the geometry gate, read the `axes:` line in your case file first — a wrong axis spec is the
single most common cause.

VS Code users: **Terminal → Run Task** has `Run AVL case`, `Compare iterations` and `Run tests`.

## Where the output goes

Each run writes to `outputs/<case name>/<timestamp>/`:

- **`results.json`** — every number the run produced: geometry, aero, drag build-up, stability, range.
- **`log_entry.md`** — the same run formatted as a log entry, matching the hand-written format.
- **`running_log.md`** (repo root) — every iteration's entry appended in order, newest last.

Raw AVL output (`totals.txt`, `stability.txt`, `strips.txt`, `log.txt`) stays in the run
directory too, including for rejected runs, so a failure is diagnosable.

## MTOM and range, in one screen

**MTOM is computed, not assumed.** A group weight buildup (centerbody, outer wing, gear,
propulsion, systems, tank system) converges against payload, crew and fuel. The 7,300 kg
figure is a **gate on the result, never an input** — a converged MTOM above it is a finding
requiring redesign, not something to tune away.

**Two range numbers are reported, and only one is for decisions.** Use
`sizing.reserve_range` — it flies the full profile (taxi, takeoff, climb, cruise, descent,
approach, missed approach, 45-min hold, 100 nm diversion, landing at the alternate) plus 5 %
contingency, from the converged MTOM. The older `range` field is a quick single-segment
Breguet with the whole tank burned in cruise; it runs about 45 % optimistic and is retained
**only** so iterations logged before Stage 10 stay comparable, not because it is better.

Reserves and contingency consume about **25 % of loaded fuel** — a real result, and a much
larger bite than the same policy takes from a kerosene aircraft.

> **Read the MTOM gate with its sensitivity table, not alone.** At the default σ the current
> geometry passes by ~3 %, but the verdict flips inside the plausible range of *both*
> unsourced inputs below. `results.json` carries `sizing.gate.verdict_decided: false` and
> says UNDECIDED in words when that happens. It means "MTOM lands within a few percent of
> the cap and which side is decided by numbers nobody has sourced" — not a settled pass.

### Placeholders — do not mistake these for validated numbers

- **σ, centerbody structural areal density (60 kg/m²)** — *ungrounded.* No citation at all;
  not from Liebeck or the NASA BWB sizing report. Largest single term in the buildup (~40 %
  of MTOM) and it flips the cap verdict. **Sourcing this is the highest-value open action.**
- **η_g, LH2 tank gravimetric efficiency (0.50)** — bracketed by real programme figures
  (~0.30 metallic, 0.55–0.57 target), but the midpoint is a choice, not a measurement. Also
  flips the cap verdict.
- **Engine specific weight (19 kg/kN)** — a kerosene turbofan figure. LH2-combustor engine
  mass data is not public.
- **Missed-approach fuel allowance** — 2 min at 3× cruise thrust. Not literature-sourced.
- **Crew count (2 pilots)** — genuinely open: single-pilot certification is unresolved
  elsewhere in the programme. Each crew member is 100 kg straight onto MTOM.
- *Not* a placeholder: **payload = 600 kg**, the 6-passenger design brief at the standard
  100 kg/occupant convention.

Full explanation — segment sequence, the convergence loop, the PC-24 cross-check and the
LHV-scaling correction — is in [docs/mtom_and_range.md](docs/mtom_and_range.md). The
equations, parameter table and citations are in `mtom_and_reserve_range_methodology.md`.

## Don't change these without understanding why

**The `cwd=output_dir` argument in `pipeline/avl_runner.py`.** AVL resolves the airfoil paths
inside a `.avl` file against its own working directory at launch, *not* against the directory
holding the `.avl` file. Launch it from anywhere else and it does not error: it substitutes a
flat-plate zero-camber section for every airfoil it cannot find and returns a fully converged,
clean-looking, wrong result. Measured cost of getting this wrong: with 3 of 21 sections
flat-plated, CLtot was byte-identical to the good run and only alpha, CDind and e moved — about
2% in L/D. `run_avl()` passes `cwd` explicitly and passes the `.avl` file as a bare filename;
`check_afiles()` verifies every section resolves before launch. There is a regression test that
fails if `cwd` is anything but the run directory.

**`File OPEN error` is fatal, `LEFIND` is only a warning, in `pipeline/validate_log.py`.** They
are not interchangeable. Every `File OPEN error` means a section silently became a flat plate,
so it hard-fails unconditionally — there is no benign case. `LEFIND` means AVL could not locate
a section's leading edge; on this airframe that was traced by hand to a genuinely valid,
unusually sharp-nosed section, so hard-failing on it would have thrown away a correct analysis.
It is collected as a non-fatal warning, surfaced in the run output and in the log entry's
Conclusion line, for a human to judge. Separately, the benign `Mass file bwb.mass open error`
and `Run case file bwb.run open error` lines appear in *every* run and must never trigger a
failure.

The `avl_iter2/` and `avl_iter3/` directories are real AVL output used as test fixtures. Deleting
them weakens the suite.

## Why any of this is the way it is

`avl_pipeline_build_plan_v2.md` has the full rationale, the failure modes each gate corresponds
to, and the three hand-validated iterations used as regression data.

---

**Internal — confidential.** For the project team only. Do not publish to a public repository or
distribute outside the team; note that AVL itself is GPL and must be obtained separately rather
than vendored here.

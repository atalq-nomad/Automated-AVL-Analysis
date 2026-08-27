# LH₂ BWB Aero Pipeline — Automation Plan v2

This supersedes the earlier draft. That version was written before any manual run — this one
is written after three, so every stage below is grounded in an actual failure mode or an
actual formula we used by hand tonight, not a guess. Three things changed as a result:

1. **One bug turned out to matter more than everything else combined.** AVL resolves airfoil
   section paths relative to its own working directory at launch, not relative to the `.avl`
   file. Get this wrong and AVL doesn't error — it silently substitutes flat-plate zero-camber
   sections and hands back a fully converged, clean-looking, **wrong** result. This cost the
   most time tonight and is the single non-negotiable requirement in Stage 3 below.
2. **The range calculation turned out to be simple, not the open-ended integration risk it
   looked like.** Tonight's Breguet-with-tank-volume calculation is a handful of lines, fully
   specified below. Wiring it in is now a small, well-defined stage (Stage 6), not the
   high-uncertainty item the first draft treated it as. Hooking into your *full* existing
   physics-based sizing suite (mass closure, multi-segment mission, reserves) is still a real
   and separate piece of work — it's demoted to an optional Stage 8, not the default path.
3. **We now have three known-good answers to test against.** Any pipeline that doesn't
   reproduce iteration 1/2/3's numbers exactly is wrong, full stop. See the Appendix.

---

## What the finished pipeline actually produces

One command, one geometry in, one thing out: a `results.json` plus an auto-written log-entry
block in exactly the format you've been typing by hand all night.

| Parameter | Source | Derivation |
|---|---|---|
| Sref, Cref (MAC), Bref, AR | `stl_to_avl.py` + `totals.txt` | direct |
| S_wet | Onshape override (config) or mesh.area (auto) | override takes precedence if given |
| CLtot, converged alpha | `totals.txt` | direct |
| CDind, e (Oswald, Trefftz) | `totals.txt` | direct |
| CD0 | computed | `Cfe · S_wet / Sref` |
| CD_total | computed | `CD0 + CDind` |
| **L/D** | computed | `CLtot / CD_total` |
| Cmtot | `totals.txt` | sanity check only — not a trimmed condition unless CG is set |
| CLa, Cma | `stability.txt` | direct |
| Neutral point Xnp | `stability.txt` | direct |
| Static margin (%MAC) | computed | `(Xnp − Xref)/Cref × 100` — proxy vs. Xref until real CG exists |
| Spiral stability indicator | `stability.txt` | `Clb·Cnr / (Clr·Cnb)`, >1 favorable |
| Fuel mass | mission config | `ρ_LH2 · tank_volume · fill_fraction` |
| Wf | computed | `MTOM − fuel_mass` |
| **Range** | computed | Breguet, see Stage 1 below |

---

## Correctness requirements — non-negotiable

These aren't nice-to-haves; each one corresponds to a real wrong answer we got tonight before
catching it.

1. **AVL must be invoked with its working directory set to the folder containing `bwb.avl`
   and `sections/`** — via `subprocess.run(..., cwd=output_dir)`, never by passing a relative
   path like `avl/bwb.avl` from a parent directory. This is exactly the bug that produced a
   fully converged run using flat-plate zero-camber sections for every station, with no error
   raised. Test this explicitly (see Appendix Fixture A).
2. **`log.txt` must be parsed for `File OPEN error` and `LEFIND` and treated as failures**,
   not warnings buried in a log nobody reads. A run with either of these present must not
   silently produce a `results.json` — it must raise/exit non-zero with the offending line
   quoted.
3. **Do not false-positive on the benign, always-present messages.** Every single AVL run —
   including all three clean, trustworthy runs tonight — prints `Mass file bwb.mass open
   error` and `Run case file bwb.run open error`, because no `.mass`/`.run` file is ever
   provided. These are expected and must never trigger a failure. Test both directions
   (Appendix Fixture C).
4. **Never reuse a stale `run.txt`'s CL value.** The CL target depends on `Sref`, which
   changes every geometry. Generate `run.txt` fresh from the computed `cl_target` every run —
   don't let a human (or Claude Code) copy last iteration's file out of habit.
5. **stl_to_avl.py's own sanity checks (symmetry residual, Sref cross-check, t/c range,
   camber sign) must stop the pipeline before AVL is ever invoked**, not just print a warning
   that scrolls past.

---

## Architecture

```
mission.yaml            (shared across ALL cases — cruise condition, engine, tank sizing)
cases/iteration_N.yaml  (per-run — STL path, units, axes, S_wet override)
        │
        ▼
pipeline/run_case.py    (orchestrator)
        │
        ├─► stl_to_avl.py  ──►  outputs/iteration_N/<ts>/bwb.avl + sections/*.dat
        │                       + geometry_summary.json (Sref, S_wet, bref, cbar, AR)
        │
        ├─► pipeline/mission.py       ──►  cl_target (needs Sref from above)
        ├─► pipeline/avl_runner.py    ──►  writes run.txt, invokes avl352.exe with
        │                                  cwd=output_dir (Correctness Req. #1)
        │                                  ──► totals.txt, stability.txt, strips.txt, log.txt
        ├─► pipeline/validate_log.py  ──►  hard-fails on File OPEN error / LEFIND
        ├─► pipeline/parse_avl.py     ──►  CLtot, CDind, e, CLa, Cma, Xnp...
        └─► pipeline/report.py        ──►  results.json + log-entry.md (auto-formatted)
                                            + appends to running_log.md
```

`mission.yaml` holds everything that must stay **identical** across iterations for a fair
comparison — cruise altitude/Mach, MTOM assumption, Cfe, TSFC, tank volume, fill fraction.
Tonight's values:

```yaml
cruise_altitude_m: 12497      # FL410
cruise_mach: 0.75
mtom_kg: 7300
cfe: 0.0030
tsfc_kg_per_Ns: 6.63e-6
tank_volume_m3: 10.0
lh2_fill_fraction: 0.90
lh2_density_kgm3: 70.8
```

Per-case config only ever varies geometry-specific things: `stl_path`, `units`, `axes`,
optionally `s_wet_override_m2` (Onshape mass-properties value, preferred over the
mesh-approximated one when both are available).

---

## Files to hand Claude Code

- `stl_to_avl.py` (existing, working)
- `avl352.exe` (path on disk)
- This build plan
- The Appendix below, pasted directly into the first prompt — it contains the exact failure
  and success text AVL produced tonight, which is far more useful to Claude Code than a
  description of the failure modes in prose
- Your next STL export (mm units) to actually run the finished pipeline against

You do **not** need to hand over the existing 5-file physics-based sizing suite for the MVP —
only if/when you pursue the optional Stage 8.

---

## Appendix — real fixtures from tonight (use these as literal test data)

**Fixture A — must hard-fail (missing airfoil files):**
```
     Reading airfoil from file: sections/sec_00.dat

 File OPEN error:  sections/sec_00.dat
 **   Airfoil file not found  : sections/sec_00.dat
 **   Using default zero-camber airfoil
```

**Fixture B — must warn/flag prominently (leading-edge detection failure, sharp-nose section):**
```
     Reading airfoil from file: sections/sec_19.dat
 ** LEFIND: Leading edge not found.  Continuing...
     Reading airfoil from file: sections/sec_20.dat
```

**Fixture C — must NOT trigger any failure (present in every single run, including all three
good ones):**
```
 Trying to read file: bwb.mass  ...

 Mass file  bwb.mass  open error
 Internal mass defaults used

 ---------------------------------------------------------------
 Trying to read file: bwb.run  ...

 Run case file  bwb.run  open error
 Internal run case defaults used
```

**Fixture D — three known-good regression points.** Feed the pipeline each iteration's actual
geometry/inputs and confirm it reproduces these exactly before trusting it on anything new:

| | Sref (m²) | S_wet (m²) | CLtot | CDind | e | CD0 | CD_total | **L/D** | Range (nm) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Iter 1 | 70.526 | 152.0 | 0.14400 | 0.0029211 | 0.7045 | 0.00647 | 0.00939 | **15.3** | ~2,570 |
| Iter 2 | 69.118 | 151.1 | 0.14710 | 0.0042748 | 0.4781 | 0.00656 | 0.01083 | **13.58** | ~2,281 |
| Iter 3 | 69.120 | 151.3 | 0.14710 | 0.0036066 | 0.5710 | 0.00657 | 0.01017 | **14.46** | ~2,430 |

All three used `Cfe = 0.0030`, `TSFC = 6.63×10⁻⁶ kg/(N·s)`, tank volume `10 m³`, fill `90%`,
MTOM `7,300 kg`, FL410/M0.75.

---

## Build stages

### Stage 1 — Mission assumptions module (~30–40 min)

**Prompt:**
```
Create pipeline/mission.py with:

1. A dataclass MissionConfig loaded from mission.yaml with fields: cruise_altitude_m,
   cruise_mach, mtom_kg, cfe, tsfc_kg_per_Ns, tank_volume_m3, lh2_fill_fraction,
   lh2_density_kgm3.

2. isa_density_and_speed_of_sound(altitude_m) -> (rho, a). Implement the ISA stratosphere
   model (valid 11-20 km): T = 216.65 K constant, rho_11km = 0.3639 kg/m3,
   rho = rho_11km * exp(-g*(h-11000)/(R*T)), a = sqrt(1.4*R*T), with g=9.80665,
   R=287.053. Raise a clear NotImplementedError if altitude_m < 11000 — the troposphere
   lapse-rate formula isn't implemented and cruise altitude should never be below FL360
   for this program anyway, so this should never silently give a wrong answer for a
   low-altitude case.

3. compute_cl_target(mission: MissionConfig, sref_m2) -> float implementing:
   V = mach * a
   q = 0.5 * rho * V**2
   W = mtom_kg * 9.80665
   return W / (q * sref_m2)

4. compute_range(mission: MissionConfig, l_over_d, cl_target=None) -> dict returning at
   least {fuel_mass_kg, wf_kg, ln_wi_wf, range_km, range_nm}, implementing:
   fuel_mass = lh2_density_kgm3 * tank_volume_m3 * lh2_fill_fraction
   wf = mtom_kg - fuel_mass
   ln_wi_wf = ln(mtom_kg / wf)
   V = mach * a  (recompute or accept as arg)
   range_m = (V / (9.80665 * tsfc_kg_per_Ns)) * l_over_d * ln_wi_wf

Write a mission.yaml with tonight's exact values (given in the build plan). Add a test that
reproduces compute_cl_target and compute_range against Fixture D's iteration 1 numbers
(Sref=70.526 -> CL_target ~0.144; L/D=15.3 -> range ~2570 nm) to a few percent tolerance.
```

### Stage 2 — Per-case config & output scaffolding (~20 min)

**Prompt:**
```
Create cases/ for per-run YAML configs (e.g. cases/iteration_4.yaml) with fields: name,
stl_path, units, axes, n_sections/cluster/mach/nchord/cspace (pass-through to
stl_to_avl.py, defaulting to its own argparse defaults), s_wet_override_m2 (optional,
takes precedence over the mesh-computed value when present), avl_exe path.

Write pipeline/config.py to load/validate this into a dataclass with clear missing-field
errors, and pipeline/paths.py with a helper that creates and returns
outputs/<name>/<timestamp>/ per run — never overwrite a previous run's folder.
```

### Stage 3 — AVL run-script generator, with the working-directory fix baked in (~30–45 min)

**Prompt:**
```
This stage encodes the single most important bug from tonight's manual testing — read this
whole prompt before writing anything.

AVL resolves an .avl file's relative airfoil paths (e.g. "sections/sec_00.dat") against its
OWN CURRENT WORKING DIRECTORY at process launch — NOT against the directory containing the
.avl file itself. Get this wrong and AVL does not error: it prints "File OPEN error" per
section, silently substitutes a flat-plate zero-camber default for that section, and still
produces a fully converged, clean-looking totals.txt. This happened tonight and the wrong
numbers weren't caught until someone manually read log.txt line by line.

The fix: whatever process invokes avl352.exe MUST set its working directory to the folder
containing bwb.avl and sections/ — via subprocess's cwd parameter, never via a relative
path argument from a different directory.

Build:
1. write_run_script(cl_target, out_totals, out_stability, out_strips) -> str in
   pipeline/avl_runner.py. Verify the exact OPER-menu command syntax against the AVL 3.52
   user guide (don't guess): load geometry (or take it as a command-line arg to avl352.exe
   instead, matching tonight's convention), enter OPER, set alpha to solve for the CL
   constraint, execute (X), write FT/ST/FS to the three given filenames, quit cleanly.

2. run_avl(avl_exe_path, avl_file_path, run_script_text, output_dir) -> subprocess.
   CompletedProcess that:
   - Resolves avl_exe_path to an absolute path FIRST (it may not live in output_dir)
   - Writes run_script_text to a temp run.txt inside output_dir
   - Invokes avl_exe_path with the .avl filename (relative, since cwd=output_dir) as an
     arg, run.txt piped to stdin, cwd=output_dir explicitly
   - Captures stdout+stderr to output_dir/log.txt
   - Raises with a clear message if the process errors or expected output files are missing

Add a test that fails if cwd is anything other than the directory containing the .avl file
— that's the regression test for tonight's actual bug.
```

### Stage 4 — Orchestrator with fail-loud geometry gating (~30–45 min)

**Prompt:**
```
Create pipeline/run_case.py: python pipeline/run_case.py cases/iteration_4.yaml mission.yaml

1. Load both configs, create the timestamped output dir
2. Call stl_to_avl.py's extract()/write_avl() (import directly if feasible without invasive
   changes) writing bwb.avl + sections/*.dat into the output dir
3. Surface stl_to_avl.py's printed diagnostics prominently: axis inference, watertight
   status, symmetry residual, Sref cross-check (projected facets vs chord integral — fail
   if disagreement exceeds ~3%), t/c range/abort, camber sign
4. STOP before calling AVL if any of the above failed — don't proceed to AVL with geometry
   that hasn't passed these checks
5. Read Sref from geometry_summary.json, call mission.compute_cl_target()
6. Build run.txt (Stage 3) with the freshly computed cl_target — never reuse a prior run's
   value
7. Call run_avl() (Stage 3) with cwd correctly set to the output dir
```

### Stage 5 — Log validation as a hard gate (~20–30 min)

**Prompt:**
```
Create pipeline/validate_log.py with check_avl_log(log_path) -> None that raises a
descriptive exception if log.txt contains "File OPEN error" or "LEFIND" for any section.

It must NOT flag the benign "Mass file bwb.mass open error" / "Run case file bwb.run open
error" lines that appear in every single run (no .mass/.run file is ever provided — these
are expected, not failures).

Use these exact fixtures as tests — I'm giving you real AVL output, not a description:

[paste Fixture A from the build plan — must raise]
[paste Fixture B from the build plan — must raise, or at minimum flag distinctly, your
choice, but it must not be silently ignored]
[paste Fixture C from the build plan — must NOT raise]

Wire this into run_case.py (Stage 4) immediately after run_avl() returns, before any
parsing happens.
```

### Stage 6 — Parser, drag build-up, range calc, and auto-formatted log entry (~45–60 min)

**Prompt:**
```
1. Extend stl_to_avl.py's extract() to also return total wetted mesh area (trimesh's
   mesh.area). Write Sref, S_wet, bref, cbar, AR to geometry_summary.json.

2. pipeline/parse_avl.py: parse_totals(path) -> dict extracting CLtot, CDtot, CDvis, CDind,
   CLff, CDff, e, Cmtot, Sref, Cref, Bref, Alpha via regex (not fixed columns). Also
   parse_stability(path) -> dict extracting CLa, Cma, Xnp, and the spiral indicator
   (Clb*Cnr/(Clr*Cnb) — compute it from Clb/Cnr/Clr/Cnb if AVL doesn't print the ratio
   directly).

3. In run_case.py after validation: compute CD0 = cfe*S_wet/Sref (using
   s_wet_override_m2 from the case config if present, else the mesh-derived value),
   CD_total, L/D, static_margin_pct = (Xnp - Xref)/Cref * 100. Call mission.compute_range()
   with this L/D. Write everything to results.json.

4. pipeline/report.py: write a markdown block to outputs/<name>/<ts>/log_entry.md matching
   this exact format (this is the format used for every iteration tonight, don't deviate):

   **Concept [name] — Iteration N**

   Assumptions: [altitude], [mach], W = [mtom] kg (MTOM), Cfe [cfe], tank vol [vol] m³,
   LH2 fill [fill]%, TSFC [tsfc] kg/(N·s)

   Geometry: Sref [x] m², AR [x], S_wet [x] m²

   Aero: CL [x], alpha [x]°, e [x], CD0 [x], CD_total [x], L/D [x], Cma [x]/rad, static
   margin ~[x]% MAC

   Mass/Range: fuel [x] kg, Wf [x] kg, ln(Wi/Wf) [x], Range ≈ [x] km / [x] nm

   Conclusion: [one line — L/D, range, and one qualitative flag if e or Cma looks off
   relative to the previous iteration in running_log.md]

   Also append this block to a running running_log.md so every iteration accumulates in
   one place rather than scattered files.

Validate against Fixture D in the build plan: feed this stage iteration 1/2/3's actual
totals.txt/stability.txt content and confirm L/D and range match the table (15.3/13.58/14.46
and ~2570/2281/2430 nm) before trusting this on new geometry.
```

### Stage 7 — Cross-iteration comparison, trend-aware (~30 min)

**Prompt:**
```
Create pipeline/compare.py scanning outputs/*/latest/results.json, producing a markdown
table: iteration name, Sref, AR, S_wet, CL, alpha, e, L/D, static margin, range. Also flag,
per row, any parameter that moved by more than 15% from the previous iteration (e in
particular — that's what surfaced the twist/washin effect tonight) so a large jump gets
called out automatically rather than requiring someone to eyeball it across log entries.
```

### Stage 8 — Hook into the full existing physics-based sizing suite (optional, ~45–90 min, still the highest-uncertainty stage)

**Prompt:**
```
I have an existing Python sizing model suite (Breguet range, mass closure, tank sizing,
multi-segment mission, reserves — [point at the actual files]) from earlier in this
project, separate from tonight's quick single-cruise-segment Breguet estimate.

Before writing integration code: read those scripts and report how they currently take L/D
and other aero inputs (hardcoded, function args, or config?). Propose the smallest-diff way
to have them consume results.json from this pipeline instead. Don't implement until I
approve the approach. Once approved, wire it in and extend report.py to include the full
mission's MTOM/range/design-gate output alongside the quick-estimate numbers, clearly
labeled as two different fidelity levels.
```

### Stage 9 — VS Code convenience (~15–20 min, low priority)

**Prompt:**
```
Add .vscode/tasks.json: a task "Run AVL case" prompting for a case config path and running
python pipeline/run_case.py ${input:caseConfig} mission.yaml, and a "Compare iterations"
task running pipeline/compare.py.
```

---

## Time estimate

| Stage | Time | Risk |
|---|---:|---|
| 1 — Mission module | 30–40 min | Low |
| 2 — Case config/scaffolding | 20 min | Low |
| 3 — AVL run-script + cwd fix | 30–45 min | Low–Med (AVL syntax) |
| 4 — Orchestrator + gating | 30–45 min | Low |
| 5 — Log validation | 20–30 min | Low (fixtures make this well-specified) |
| 6 — Parser + range + report | 45–60 min | Low (regression data exists) |
| **MVP subtotal (1–6)** | **~3–4 hr** | **Produces L/D + range + auto log entry, validated against tonight's 3 iterations** |
| 7 — Cross-iteration comparison | 30 min | Low |
| 8 — Full sizing-suite integration | 45–90 min | High — unchanged from before, still genuinely uncertain |
| 9 — VS Code tasks | 15–20 min | Low |

## Suggested session plan

- **One sitting (~3–4 hr):** Stages 1–6. End state: `python pipeline/run_case.py
  cases/iteration_4.yaml mission.yaml` produces `results.json` and a correctly formatted
  `log_entry.md`, with the parser+calc verified against Fixture D before trusting it on new
  geometry.
- **Then:** Stage 7 if you're running enough iterations to want the auto-comparison.
- **Separately, later:** Stage 8 only once you actually want MTOM/range from the full
  physics-based model rather than the quick per-iteration cruise estimate — it's decoupled
  from everything else here and can wait indefinitely without blocking iteration work.

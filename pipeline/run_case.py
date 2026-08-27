"""Stage 4 — orchestrator.

    python pipeline/run_case.py cases/iteration_4.yaml mission.yaml

Sequence, in the order the build plan specifies:

    1. load both configs, create the timestamped output dir
    2. run stl_to_avl's extract()/write_avl() into that dir
    3. surface stl_to_avl's diagnostics prominently
    4. STOP before AVL if any fatal geometry check failed
    5. read Sref back out of geometry_summary.json, compute the CL target
    6. build run.txt from that freshly computed CL — never a stale one
    7. run AVL with cwd set to the output dir

Nothing here reuses a previous run's numbers: the output directory is new, the
CL target is recomputed from this run's Sref, and run.txt is regenerated.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # invoked as `python pipeline/run_case.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stl_to_avl  # noqa: E402  (needs the sys.path line above)

from pipeline.avl_runner import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    AvlRunError,
    describe_invocation,
    run_avl,
    write_run_script,
)
from pipeline.config import CaseConfig  # noqa: E402
from pipeline.geometry_checks import (  # noqa: E402
    evaluate_geometry,
    failures,
    sref_disagreement_pct,
    summarise,
)
from pipeline.mission import MissionConfig, compute_cl_target, default_mission_path  # noqa: E402
from pipeline.parse_avl import AvlParseError, parse_stability, parse_totals  # noqa: E402
from pipeline.paths import RunPaths, new_run_paths, update_latest_pointer  # noqa: E402
from pipeline.report import (  # noqa: E402
    append_running_log,
    find_previous_results,
    write_log_entry,
)
from pipeline.results import build_results  # noqa: E402
from pipeline.validate_log import AvlLogError, check_avl_log, parse_avl_log  # noqa: E402


class GeometryGateError(RuntimeError):
    """A fatal geometry check failed. AVL must not be invoked."""


class Tee(io.TextIOBase):
    """Echo stl_to_avl's prints live while also keeping a copy for the log."""

    def __init__(self, stream):
        self.stream = stream
        self.buffer_text = io.StringIO()

    def write(self, s):
        self.stream.write(s)
        self.stream.flush()
        self.buffer_text.write(s)
        return len(s)

    def flush(self):
        self.stream.flush()

    @property
    def text(self) -> str:
        return self.buffer_text.getvalue()


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def build_geometry(case: CaseConfig, paths: RunPaths) -> tuple[dict, str]:
    """Steps 2-3: run stl_to_avl into the run dir, collecting diagnostics."""
    banner("STEP 2/3 — geometry extraction (stl_to_avl.py)")
    print(f"  STL        : {case.stl_path}")
    print(f"  units      : {case.units} (scale {case.scale:g})")
    print(f"  axes       : {case.axes}")
    print(f"  n_sections : {case.n_sections}, cluster {case.cluster}")
    print(f"  into       : {paths.run_dir}")
    print()

    diag: dict = {}
    tee = Tee(sys.stdout)
    real_stdout, sys.stdout = sys.stdout, tee
    try:
        sections, sref, b_half = stl_to_avl.extract(
            **case.extract_kwargs(), recentre=True, diag=diag
        )
        if not sections:
            raise GeometryGateError(
                f"stl_to_avl extracted 0 sections from {case.stl_path}. Nothing to analyse."
            )
        stl_to_avl.write_avl(
            sections, sref, b_half,
            outdir=str(paths.run_dir),
            name=paths.avl_file.stem,
            **case.write_avl_kwargs(),
        )
    finally:
        sys.stdout = real_stdout

    # Recomputed from the returned sections, not scraped from the printed text.
    y = np.array([s["y"] for s in sections])
    chord = np.array([s["chord"] for s in sections])
    x_le = np.array([s["x_le"] for s in sections])
    cbar, x_quarter, sref_chords = stl_to_avl.mac_and_quarter_chord(y, chord, x_le)
    tc = [s["tc"] for s in sections]
    mean_camber = float(np.mean([np.mean(0.5 * (s["zu"] + s["zl"])) for s in sections]))

    bref = 2.0 * b_half
    diag.update({
        "sref_m2": float(sref),
        "sref_chord_integral_m2": float(sref_chords),
        "sref_disagreement_pct": sref_disagreement_pct(sref, sref_chords),
        "bref_m": float(bref),
        "cbar_m": float(cbar),
        "ar": float(bref ** 2 / sref),
        "xref_m": float(x_quarter),
        "tc_min": float(min(tc)),
        "tc_max": float(max(tc)),
        "mean_camber": mean_camber,
        # Wetted area from the mesh. The case config's s_wet_override_m2 takes
        # precedence over this in the CD0 build-up (Stage 6).
        "s_wet_mesh_m2": diag.get("mesh_area_m2"),
    })
    return diag, tee.text


def gate(diag: dict) -> list:
    """Step 4: evaluate and report. The caller decides whether to stop."""
    banner("STEP 4 — geometry gate")
    checks = evaluate_geometry(
        sref_projected=diag["sref_m2"],
        sref_chord_integral=diag["sref_chord_integral_m2"],
        tc_min=diag["tc_min"],
        tc_max=diag["tc_max"],
        mean_camber=diag["mean_camber"],
        symmetry_residual=diag["symmetry_residual"],
        watertight=diag["watertight"],
        n_requested=diag["n_sections_requested"],
        n_extracted=diag["n_sections_extracted"],
        ar=diag["ar"],
    )
    print(summarise(checks))
    return checks


def enforce_gate(checks: list, force: bool) -> None:
    """Raise unless every fatal check passed, or the gate was forced."""
    bad = failures(checks)
    if not bad:
        return
    print()
    if force:
        print("  !! --force-geometry: proceeding past "
              f"{len(bad)} FAILED check(s). The results are not trustworthy.")
        return
    detail = "\n\n".join(f"  {c.name}: {c.detail}" for c in bad)
    raise GeometryGateError(
        f"{len(bad)} fatal geometry check(s) failed — AVL was NOT invoked.\n\n"
        f"{detail}\n\n"
        "Fix the geometry or the axis convention. Running AVL on this would "
        "produce a converged result built on geometry that has not passed its "
        "own sanity checks. Use --force-geometry to override deliberately."
    )


def write_geometry_summary(paths: RunPaths, case: CaseConfig, diag: dict,
                           checks: list, forced: bool, timestamp: str) -> dict:
    summary = {
        "name": case.name,
        "timestamp": timestamp,
        "run_dir": str(paths.run_dir),
        "case_config": case.to_dict(),
        "geometry": {k: v for k, v in diag.items() if k != "case_config"},
        "checks": [asdict(c) for c in checks],
        "gate_passed": not failures(checks),
        "forced": forced,
    }
    paths.geometry_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def record_avl_log(paths: RunPaths, report) -> None:
    """Add the log-validation outcome to geometry_summary.json.

    Same pattern as gate_passed: a rejected run keeps its summary AND every
    raw file AVL produced (log.txt, totals.txt, ...). Nothing is deleted on the
    failure path, so a rejected run stays diagnosable instead of vanishing.
    """
    path = paths.geometry_summary
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["avl_log"] = report.to_dict()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def validate_log_step(paths: RunPaths, log_path: Path):
    """Stage 5 — run immediately after AVL returns, before anything is parsed."""
    banner("STEP 8 — AVL log validation")
    try:
        report = check_avl_log(log_path)
    except AvlLogError:
        if Path(log_path).is_file():
            record_avl_log(paths, parse_avl_log(
                Path(log_path).read_text(encoding="utf-8", errors="replace"),
                log_path=str(log_path),
            ))
        print("  [FAIL] fatal finding(s) in log.txt — raw AVL output left in place "
              "for diagnosis")
        raise

    record_avl_log(paths, report)
    print(f"  [OK  ] no 'File OPEN error' — AVL read all "
          f"{report.sections_read} airfoil sections")
    print(f"  [OK  ] {len(report.benign)} expected mass/run open-error line(s) "
          "recognised and ignored")

    if report.warnings:
        print()
        print("  " + "!" * 72)
        print("  " + report.warning_message().replace("\n", "\n  "))
        print("  " + "!" * 72)
    else:
        print("  [OK  ] no LEFIND warnings")
    return report


def run(case_path: Path, mission_path: Path, *, skip_avl: bool = False,
        force_geometry: bool = False, timeout: float = DEFAULT_TIMEOUT_S,
        base_dir: Path | None = None) -> dict:
    base_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    # -- Step 1 ------------------------------------------------------------
    banner("STEP 1 — configuration")
    case = CaseConfig.from_yaml(case_path, base_dir=base_dir)
    mission = MissionConfig.from_yaml(mission_path)
    print(f"  case    : {Path(case_path).resolve()}")
    print(f"  mission : {Path(mission_path).resolve()}")
    print(f"  cruise  : {mission.cruise_altitude_m:.0f} m, M{mission.cruise_mach:.2f}, "
          f"MTOM {mission.mtom_kg:.0f} kg")

    stamp = datetime.now()
    paths = new_run_paths(case.name, base_dir=base_dir, timestamp=stamp)
    print(f"  output  : {paths.run_dir}")

    # -- Steps 2-3 ---------------------------------------------------------
    diag, geometry_log = build_geometry(case, paths)
    paths.run_dir.joinpath("geometry_log.txt").write_text(geometry_log, encoding="utf-8")

    # -- Step 4 ------------------------------------------------------------
    # The summary is written BEFORE the gate is enforced, so a rejected run
    # still leaves the numbers behind to diagnose from. A failed run never
    # produces results.json and never updates the `latest` pointer, so nothing
    # downstream can mistake it for a good one.
    checks = gate(diag)
    summary = write_geometry_summary(
        paths, case, diag, checks, forced=force_geometry,
        timestamp=stamp.isoformat(timespec="seconds"),
    )
    print(f"\n  wrote {paths.geometry_summary.name}")
    enforce_gate(checks, force=force_geometry)

    # -- Step 5 ------------------------------------------------------------
    # Read Sref back out of the file rather than reusing the in-memory value,
    # so a summary that is wrong on disk cannot pass unnoticed.
    banner("STEP 5 — CL target")
    sref = json.loads(paths.geometry_summary.read_text(encoding="utf-8"))["geometry"]["sref_m2"]
    cl_target = compute_cl_target(mission, sref)
    print(f"  Sref (from geometry_summary.json) = {sref:.4f} m2")
    print(f"  AR = {diag['ar']:.3f}, bref = {diag['bref_m']:.4f} m, "
          f"MAC = {diag['cbar_m']:.4f} m")
    print(f"  CL target = {cl_target:.5f}  (fresh — never carried over from a prior run)")

    # -- Step 6 ------------------------------------------------------------
    banner("STEP 6 — AVL run script")
    script = write_run_script(cl_target)
    print("\n".join(f"  {ln}" for ln in script.splitlines()))

    result = {
        "geometry_summary": summary,
        "sref_m2": sref,
        "cl_target": cl_target,
        "run_dir": str(paths.run_dir),
        "avl_ran": False,
    }

    # -- Step 7 ------------------------------------------------------------
    banner("STEP 7 — AVL")
    print(f"  {describe_invocation(case.avl_exe, paths.avl_file, paths.run_dir)}")
    if skip_avl:
        paths.run_script.write_text(script, encoding="utf-8", newline="\n")
        print("\n  --skip-avl: stopping before invocation. run.txt written.")
        update_latest_pointer(paths.run_dir)
        return result

    print()
    avl = run_avl(case.avl_exe, paths.avl_file, script, paths.run_dir, timeout=timeout)
    print(f"  AVL exited {avl.returncode}; wrote {avl.totals.name}, "
          f"{avl.stability.name}, {avl.strips.name}")
    result["avl_ran"] = True
    result["avl_returncode"] = avl.returncode

    # -- Stage 5 ------------------------------------------------------------
    log_report = validate_log_step(paths, avl.log_path)
    result["log_validation"] = log_report.to_dict()

    # -- Stage 6 ------------------------------------------------------------
    banner("STEP 9 — parse, drag build-up, range")
    totals = parse_totals(paths.totals)
    stability = parse_stability(paths.stability)

    results = build_results(
        case=case, mission=mission, totals=totals, stability=stability,
        geometry=summary["geometry"], cl_target=cl_target,
        run_dir=str(paths.run_dir), timestamp=stamp.isoformat(timespec="seconds"),
        log_report=log_report.to_dict(),
    )
    paths.results.write_text(json.dumps(results, indent=2, sort_keys=True),
                             encoding="utf-8")

    a, s, r = results["aero"], results["stability"], results["range"]
    g = results["geometry"]
    print(f"  S_wet         = {g['s_wet_m2']:.3f} m2  ({g['s_wet_source']})")
    print(f"  CD0           = {a['cd0']:.5f}  = Cfe {mission.cfe} * S_wet / Sref")
    print(f"  CD_total      = {a['cd_total']:.5f}  = CD0 + CDind {a['CDind']:.7f}")
    print(f"  L/D           = {a['l_over_d']:.3f}")
    print(f"  static margin = {s['static_margin_pct']:+.2f} % MAC "
          f"(Xnp {s['Xnp']:.4f} - Xref {g['xref_m']:.4f}) / Cref {g['cref_m']:.4f}")
    print(f"    cross-check   -Cma/CLa = {s['static_margin_crosscheck']['from_derivatives_pct']:+.2f} % "
          f"-> {'consistent' if s['static_margin_crosscheck']['consistent'] else 'INCONSISTENT'}")
    print(f"  pitch stable  = {s['pitch_stable']} (Cma {s['Cma']:+.4f}/rad, "
          "sign-convention-independent)")
    print(f"  Range         = {r['range_km']:.0f} km / {r['range_nm']:.0f} nm")
    print(f"  wrote {paths.results.name}")

    # -- Step 10 ------------------------------------------------------------
    banner("STEP 10 — log entry")
    previous = find_previous_results(base_dir, paths.run_dir)
    entry = write_log_entry(results, paths.log_entry, previous)
    running = append_running_log(entry, base_dir)
    if previous is not None:
        print(f"  compared against {previous['name']} ({previous['timestamp']})")
    else:
        print("  no previous results.json found — no trend comparison in this entry")
    print(f"  wrote {paths.log_entry.name} and appended to {running.name}")
    print()
    print("\n".join(f"  {ln}" for ln in entry.splitlines()))

    result["results"] = results

    update_latest_pointer(paths.run_dir)
    banner("DONE")
    print(f"  {paths.run_dir}")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Run one geometry iteration through stl_to_avl and AVL.")
    p.add_argument("case", help="per-case YAML, e.g. cases/iteration_4.yaml")
    p.add_argument("mission", nargs="?", default=None,
                   help="mission YAML (default: mission.yaml at the project root)")
    p.add_argument("--skip-avl", action="store_true",
                   help="stop after writing run.txt; do not invoke AVL")
    p.add_argument("--force-geometry", action="store_true",
                   help="proceed even if a fatal geometry check fails (results are "
                        "not trustworthy; recorded in geometry_summary.json)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                   help=f"AVL timeout in seconds (default {DEFAULT_TIMEOUT_S})")
    a = p.parse_args(argv)

    mission_path = Path(a.mission) if a.mission else default_mission_path()
    try:
        run(Path(a.case), mission_path, skip_avl=a.skip_avl,
            force_geometry=a.force_geometry, timeout=a.timeout)
    except (GeometryGateError, AvlRunError, AvlLogError, AvlParseError) as exc:
        print(f"\n{'!' * 78}\nPIPELINE STOPPED\n{'!' * 78}\n{exc}\n", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(f"\n{'!' * 78}\nCONFIGURATION ERROR\n{'!' * 78}\n{exc}\n", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

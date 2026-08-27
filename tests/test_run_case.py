"""Stage 4 tests — the orchestrator.

These exercise run_case.run() end to end with stl_to_avl and AVL both replaced
by stubs, so the sequencing and the gate can be tested without a 27 MB STL or a
real solve. The point of most of them is what does NOT happen: AVL must not be
invoked when a geometry check has failed.
"""

import json
import subprocess

import numpy as np
import pytest

import stl_to_avl
from pipeline import run_case
from pipeline.run_case import GeometryGateError

MISSION = """\
cruise_altitude_m: 12497
cruise_mach: 0.75
mtom_kg: 7300
cfe: 0.0030
tsfc_kg_per_Ns: 6.63e-6
tank_volume_m3: 10.0
lh2_fill_fraction: 0.90
lh2_density_kgm3: 70.8
"""

CASE = """\
name: iteration_test
stl_path: model.stl
avl_exe: avl352.exe
units: mm
axes: xyz
n_sections: 5
"""


def fake_sections(n=5, tc=0.15, camber=0.01, chord_scale=1.0):
    """Sections shaped like stl_to_avl.extract()'s output."""
    xs = np.linspace(0.0, 1.0, 11)
    out = []
    for i in range(n):
        chord = (8.0 - 0.9 * i) * chord_scale
        bump = np.sin(np.pi * xs)
        out.append(dict(
            y=float(i * 1.5), x_le=float(0.6 * i), z_le=0.0,
            chord=float(chord), twist=0.0, xs=xs,
            zu=camber * bump + 0.5 * tc * bump,
            zl=camber * bump - 0.5 * tc * bump,
            tc=float(tc),
        ))
    return out


# Realistic AVL output for the stub to emit. Shaped exactly like avl_iter3's
# real files (same field layout, including the CLtot/Cltot and CLa/Cla case
# traps) so the Stage 6 parser is exercised, not bypassed.
STUB_TOTALS = """\
 ---------------------------------------------------------------
 Vortex Lattice Output -- Total Forces

  Sref =  74.400       Cref =  7.0165       Bref =  12.000
  Xref =  4.2352       Yref =  0.0000       Zref =  0.0000

  Alpha =   4.62314     pb/2V =  -0.00000     p'b/2V =  -0.00000
  Beta  =   0.00000     qc/2V =   0.00000
  Mach  =     0.000     rb/2V =  -0.00000     r'b/2V =  -0.00000

  CXtot =   0.00826     Cltot =  -0.00000     Cl'tot =  -0.00000
  CYtot =  -0.00000     Cmtot =  -0.06538
  CZtot =  -0.14691     Cntot =   0.00000     Cn'tot =   0.00000

  CLtot =   0.13670
  CDtot =   0.00361
  CDvis =   0.00000     CDind = 0.0036066
  CLff  =   0.13680     CDff  = 0.0034584    | Trefftz
  CYff  =  -0.00000         e =    0.5710    | Plane
"""

STUB_STABILITY = """\
  Sref =  74.400       Cref =  7.0165       Bref =  12.000
  Xref =  4.2352       Yref =  0.0000       Zref =  0.0000

 z' force CL |    CLa =   2.531806    CLb =   0.000000
 x' mom.  Cl'|    Cla =  -0.000000    Clb =  -0.077360
 y  mom.  Cm |    Cma =  -0.308260    Cmb =  -0.000000
 z' mom.  Cn'|    Cna =   0.000000    Cnb =   0.001827
 x' mom.  Cl'|    Clp =  -0.207887    Clq =  -0.000000    Clr =   0.086006
 z' mom.  Cn'|    Cnp =  -0.008839    Cnq =   0.000000    Cnr =  -0.008502

 Neutral point  Xnp =   5.089442

 Clb Cnr / Clr Cnb  =   4.185193    (  > 1 if spirally stable )
"""


def write_stub_avl_outputs(cwd):
    """Write the three files a real AVL run produces."""
    from pathlib import Path
    (Path(cwd) / "totals.txt").write_text(STUB_TOTALS)
    (Path(cwd) / "stability.txt").write_text(STUB_STABILITY)
    (Path(cwd) / "strips.txt").write_text("strip forces\n")


def chord_integral_sref(sections):
    """The same Sref the gate's cross-check computes from the sections.

    The stub derives Sref this way so a fixture is never accidentally
    inconsistent with its own geometry — that is what the gate exists to catch,
    and a self-inconsistent stub would just be testing the gate against itself.
    """
    y = np.array([s["y"] for s in sections])
    c = np.array([s["chord"] for s in sections])
    return 2.0 * float(np.trapezoid(c, y))


SREF_DEFAULT = chord_integral_sref(fake_sections())   # 74.4 m2
B_HALF_DEFAULT = 6.0                                  # max station y


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "model.stl").write_text("solid x\nendsolid x\n")
    (tmp_path / "avl352.exe").write_bytes(b"MZ")
    (tmp_path / "mission.yaml").write_text(MISSION)
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "iteration_test.yaml").write_text(CASE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def stub_stl(monkeypatch):
    """Replace extract() with a stub; write_avl() stays real."""
    # sref=None means "derive it from the sections", keeping the stub honest.
    state = {"diag": {}, "sections": fake_sections(), "sref": None,
             "b_half": B_HALF_DEFAULT}

    def fake_extract(stl_path, n_sections, scale, cluster, axes="xyz",
                     recentre=True, diag=None):
        print(f"  (stub) reading {stl_path} scale={scale}")
        sections = state["sections"]
        sref = (chord_integral_sref(sections) if state["sref"] is None
                else state["sref"])
        if diag is not None:
            diag.update({
                "watertight": True, "euler_number": 2,
                "symmetry_y0": 0.0, "symmetry_residual": 0.004,
                "mesh_area_m2": 151.3, "extents_m": [10.0, 15.5, 1.2],
                "axes_spec": axes, "axes_remapped": False,
                "n_sections_requested": n_sections,
                "n_sections_extracted": len(sections),
                "skipped_stations": [], "sref_projected_m2": sref,
                "b_half_m": state["b_half"],
            })
            diag.update(state["diag"])
        return sections, sref, state["b_half"]

    monkeypatch.setattr(stl_to_avl, "extract", fake_extract)
    return state


@pytest.fixture
def spy_avl(monkeypatch):
    """Record whether AVL was launched, and fake its output files."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        write_stub_avl_outputs(kwargs["cwd"])
        return subprocess.CompletedProcess(args, 0, stdout=FIXTURE_C_LOG)

    monkeypatch.setattr(run_case.run_avl.__globals__["subprocess"], "run", fake_run)
    return calls


def do_run(workspace, **kw):
    return run_case.run(
        workspace / "cases" / "iteration_test.yaml",
        workspace / "mission.yaml",
        base_dir=workspace,
        **kw,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_full_sequence_produces_a_run_dir_with_everything(workspace, stub_stl, spy_avl):
    result = do_run(workspace)
    run_dir = workspace / "outputs" / "iteration_test"
    made = next(d for d in run_dir.iterdir() if d.is_dir() and d.name != "latest")

    for name in ("bwb.avl", "geometry_summary.json", "geometry_log.txt",
                 "run.txt", "log.txt", "totals.txt", "stability.txt", "strips.txt",
                 "results.json", "log_entry.md"):
        assert (made / name).is_file(), name
    assert (made / "sections" / "sec_00.dat").is_file()
    assert (workspace / "running_log.md").is_file()
    assert result["avl_ran"] is True


def test_cl_target_is_computed_from_this_runs_sref(workspace, stub_stl, spy_avl):
    """Correctness Requirement #4, at the orchestrator level."""
    result = do_run(workspace)
    assert result["sref_m2"] == pytest.approx(74.4)
    assert result["cl_target"] == pytest.approx(0.13673, rel=1e-3)

    # A different geometry must move the CL target, not reuse the old one.
    stub_stl["sections"] = fake_sections(chord_scale=0.8)
    second = do_run(workspace)
    assert second["sref_m2"] == pytest.approx(59.52)
    assert second["cl_target"] == pytest.approx(0.17092, rel=1e-3)


def test_run_txt_carries_the_freshly_computed_cl(workspace, stub_stl, spy_avl):
    do_run(workspace)
    made = sorted((workspace / "outputs" / "iteration_test").glob("2*"))[-1]
    assert "c 0.1367" in (made / "run.txt").read_text()


def test_avl_is_launched_with_cwd_set_to_the_run_dir(workspace, stub_stl, spy_avl):
    from pathlib import Path

    result = do_run(workspace)
    assert len(spy_avl) == 1
    assert Path(spy_avl[0]["kwargs"]["cwd"]).resolve() == Path(result["run_dir"]).resolve()
    assert spy_avl[0]["args"][1] == "bwb.avl"


def test_geometry_summary_has_what_stage_6_needs(workspace, stub_stl, spy_avl):
    result = do_run(workspace)
    geom = json.loads(
        (workspace / result["run_dir"] / "geometry_summary.json").read_text()
    )["geometry"]
    for key in ("sref_m2", "s_wet_mesh_m2", "bref_m", "cbar_m", "ar"):
        assert key in geom, key
    assert geom["s_wet_mesh_m2"] == pytest.approx(151.3)
    assert geom["ar"] == pytest.approx((2 * B_HALF_DEFAULT) ** 2 / SREF_DEFAULT, rel=1e-6)


def test_each_run_gets_its_own_directory(workspace, stub_stl, spy_avl):
    a = do_run(workspace)["run_dir"]
    b = do_run(workspace)["run_dir"]
    assert a != b


def test_stl_to_avl_diagnostics_are_captured_to_geometry_log(workspace, stub_stl, spy_avl):
    result = do_run(workspace)
    text = (workspace / result["run_dir"] / "geometry_log.txt").read_text()
    assert "(stub) reading" in text
    assert "Sref (projected facets)" in text   # printed by the real write_avl


# ---------------------------------------------------------------------------
# THE GATE — AVL must not run on geometry that failed its checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad,expected", [
    ({"symmetry_residual": 0.30}, "symmetry residual"),
    ({"n_sections_extracted": 5, "mesh_area_m2": 151.3}, None),  # control: fine
])
def test_gate_blocks_or_allows(workspace, stub_stl, spy_avl, bad, expected):
    stub_stl["diag"] = bad
    if expected:
        with pytest.raises(GeometryGateError, match=expected):
            do_run(workspace)
        assert spy_avl == [], "AVL was invoked despite a failed geometry check"
    else:
        do_run(workspace)
        assert len(spy_avl) == 1


def test_thick_sections_stop_the_pipeline_before_avl(workspace, stub_stl, spy_avl):
    stub_stl["sections"] = fake_sections(tc=0.9)
    with pytest.raises(GeometryGateError, match="t/c"):
        do_run(workspace)
    assert spy_avl == [], "AVL was invoked with an impossible t/c"


def test_inverted_camber_stops_the_pipeline_before_avl(workspace, stub_stl, spy_avl):
    stub_stl["sections"] = fake_sections(camber=-0.05)
    with pytest.raises(GeometryGateError, match="camber"):
        do_run(workspace)
    assert spy_avl == []


def test_sref_disagreement_stops_the_pipeline_before_avl(workspace, stub_stl, spy_avl):
    stub_stl["sref"] = 40.0  # chord integral will be far from this
    with pytest.raises(GeometryGateError, match="Sref"):
        do_run(workspace)
    assert spy_avl == []


def test_zero_sections_stops_the_pipeline(workspace, stub_stl, spy_avl):
    stub_stl["sections"] = []
    with pytest.raises(GeometryGateError, match="0 sections"):
        do_run(workspace)
    assert spy_avl == []


def test_failed_gate_still_leaves_a_summary_to_diagnose_from(workspace, stub_stl, spy_avl):
    """A rejected run must be diagnosable, but never mistakable for a good one."""
    stub_stl["sections"] = fake_sections(tc=0.9)
    with pytest.raises(GeometryGateError):
        do_run(workspace)

    made = sorted((workspace / "outputs" / "iteration_test").glob("2*"))[-1]
    summary = json.loads((made / "geometry_summary.json").read_text())
    assert summary["gate_passed"] is False
    assert any(c["status"] == "FAIL" for c in summary["checks"])
    # ...but nothing downstream can pick it up as a result.
    assert not (made / "results.json").exists()
    assert not (made / "latest").exists()
    assert not (made.parent / "latest.txt").exists()


def test_passing_gate_records_gate_passed(workspace, stub_stl, spy_avl):
    result = do_run(workspace)
    summary = json.loads((workspace / result["run_dir"] / "geometry_summary.json").read_text())
    assert summary["gate_passed"] is True
    assert summary["forced"] is False


def test_force_geometry_overrides_the_gate_and_records_it(workspace, stub_stl, spy_avl):
    stub_stl["sections"] = fake_sections(tc=0.9)
    result = do_run(workspace, force_geometry=True)
    assert len(spy_avl) == 1
    summary = json.loads((workspace / result["run_dir"] / "geometry_summary.json").read_text())
    assert summary["forced"] is True
    assert any(c["status"] == "FAIL" for c in summary["checks"])


def test_non_watertight_mesh_does_not_block(workspace, stub_stl, spy_avl):
    stub_stl["diag"] = {"watertight": False}
    do_run(workspace)
    assert len(spy_avl) == 1


# ---------------------------------------------------------------------------
# Stage 5 wiring — log validation as a hard gate
# ---------------------------------------------------------------------------


FIXTURE_A_LOG = """\
 Reading airfoil from file: sections/sec_00.dat

 File OPEN error:  sections/sec_00.dat
 **   Airfoil file not found  : sections/sec_00.dat
 **   Using default zero-camber airfoil
"""

FIXTURE_B_LOG = """\
     Reading airfoil from file: sections/sec_19.dat
 ** LEFIND: Leading edge not found.  Continuing...
     Reading airfoil from file: sections/sec_20.dat
"""

FIXTURE_C_LOG = """\
 Mass file  bwb.mass  open error
 Internal mass defaults used
 Run case file  bwb.run  open error
 Internal run case defaults used
"""


@pytest.fixture
def avl_log_text(monkeypatch):
    """Control what the stubbed AVL writes to log.txt."""
    box = {"text": FIXTURE_C_LOG}

    def fake_run(args, **kwargs):
        write_stub_avl_outputs(kwargs["cwd"])
        return subprocess.CompletedProcess(args, 0, stdout=box["text"])

    monkeypatch.setattr(run_case.run_avl.__globals__["subprocess"], "run", fake_run)
    return box


def test_clean_log_passes_and_is_recorded(workspace, stub_stl, avl_log_text):
    result = do_run(workspace)
    assert result["log_validation"]["passed"] is True
    assert result["log_validation"]["benign_ignored"] == 2
    summary = json.loads((workspace / result["run_dir"] / "geometry_summary.json").read_text())
    assert summary["avl_log"]["passed"] is True


def test_file_open_error_stops_the_run(workspace, stub_stl, avl_log_text):
    from pipeline.validate_log import AvlLogError

    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_A_LOG
    with pytest.raises(AvlLogError, match="sec_00"):
        do_run(workspace)


def test_rejected_run_keeps_its_raw_avl_output(workspace, stub_stl, avl_log_text):
    """The Stage 4 lesson: a rejected run must stay diagnosable."""
    from pipeline.validate_log import AvlLogError

    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_A_LOG
    with pytest.raises(AvlLogError):
        do_run(workspace)

    made = sorted((workspace / "outputs" / "iteration_test").glob("2*"))[-1]
    # Nothing AVL produced is deleted.
    assert (made / "log.txt").is_file()
    assert "File OPEN error" in (made / "log.txt").read_text()
    assert "CLtot" in (made / "totals.txt").read_text()
    assert (made / "run.txt").is_file()
    # ...but it is marked as rejected, and nothing downstream can adopt it.
    summary = json.loads((made / "geometry_summary.json").read_text())
    assert summary["avl_log"]["passed"] is False
    assert summary["avl_log"]["errors"][0]["section"] == "sections/sec_00.dat"
    assert not (made / "results.json").exists()
    assert not (made.parent / "latest.txt").exists()


def test_lefind_warns_but_the_run_completes(workspace, stub_stl, avl_log_text):
    """A valid sharp-nosed section must not have its analysis thrown away."""
    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_B_LOG
    result = do_run(workspace)

    assert result["log_validation"]["passed"] is True
    warnings = result["log_validation"]["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["section"] == "sections/sec_19.dat"
    # The run completed: latest pointer updated, outputs intact.
    assert (workspace / "outputs" / "iteration_test" / "latest.txt").is_file()


def test_lefind_is_recorded_in_geometry_summary(workspace, stub_stl, avl_log_text):
    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_B_LOG
    result = do_run(workspace)
    summary = json.loads((workspace / result["run_dir"] / "geometry_summary.json").read_text())
    assert summary["avl_log"]["passed"] is True
    assert summary["avl_log"]["warnings"][0]["section"] == "sections/sec_19.dat"


def test_lefind_is_surfaced_prominently_in_the_output(workspace, stub_stl, avl_log_text, capsys):
    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_B_LOG
    do_run(workspace)
    out = capsys.readouterr().out
    assert "LEFIND warning(s)" in out
    assert "sections/sec_19.dat" in out
    assert "!!!!" in out


def test_log_validation_runs_before_any_parsing(workspace, stub_stl, avl_log_text, capsys):
    """Ordering matters: nothing may be read out of totals.txt first."""
    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_A_LOG
    from pipeline.validate_log import AvlLogError

    with pytest.raises(AvlLogError):
        do_run(workspace)
    out = capsys.readouterr().out
    assert "STEP 8 — AVL log validation" in out


def test_log_failure_exits_nonzero(workspace, stub_stl, avl_log_text):
    avl_log_text["text"] = FIXTURE_C_LOG + FIXTURE_A_LOG
    assert run_case.main(["cases/iteration_test.yaml", "mission.yaml"]) == 1


# ---------------------------------------------------------------------------
# --skip-avl and CLI
# ---------------------------------------------------------------------------


def test_skip_avl_writes_run_txt_without_launching(workspace, stub_stl, spy_avl):
    result = do_run(workspace, skip_avl=True)
    assert spy_avl == []
    assert result["avl_ran"] is False
    made = workspace / result["run_dir"]
    assert (made / "run.txt").is_file()
    assert not (made / "totals.txt").exists()


def test_cli_exit_codes(workspace, stub_stl, spy_avl):
    argv = ["cases/iteration_test.yaml", "mission.yaml", "--skip-avl"]
    assert run_case.main(argv) == 0

    stub_stl["sections"] = fake_sections(tc=0.9)
    assert run_case.main(argv) == 1, "a failed gate must exit non-zero"

    assert run_case.main(["cases/nope.yaml", "mission.yaml"]) == 2


def test_gate_failure_exits_nonzero_without_results(workspace, stub_stl, spy_avl):
    """Correctness Requirement #2's sibling: no quiet success on bad geometry."""
    stub_stl["sections"] = fake_sections(camber=-0.05)
    assert run_case.main(["cases/iteration_test.yaml", "mission.yaml"]) == 1
    made = sorted((workspace / "outputs" / "iteration_test").glob("2*"))[-1]
    assert not (made / "totals.txt").exists()
    assert not (made / "results.json").exists()

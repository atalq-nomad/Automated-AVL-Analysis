"""Stage 6 — Fixture D regression.

The build plan's three known-good iterations, fed through the real drag
build-up and range calculation. A pipeline that does not reproduce these is
wrong, full stop.

Tolerance note: the plan's table is quoted to 3-4 significant figures, and a
freshly computed cl_target differs from the hand-typed value in the fourth
decimal. So L/D and range are checked to a small relative tolerance rather than
to exact digits — the point is agreement, not digit-matching.
"""

import pytest

from pipeline.config import CaseConfig
from pipeline.mission import MissionConfig, compute_cl_target, default_mission_path
from pipeline.results import build_results, drag_buildup

# Fixture D, verbatim from the build plan's Appendix.
FIXTURE_D = [
    # name,    Sref,   S_wet,  CLtot,   CDind,     e,      CD0,     CD_total,  L/D,   nm
    ("iter1", 70.526, 152.0, 0.14400, 0.0029211, 0.7045, 0.00647, 0.00939, 15.3, 2570),
    ("iter2", 69.118, 151.1, 0.14710, 0.0042748, 0.4781, 0.00656, 0.01083, 13.58, 2281),
    ("iter3", 69.120, 151.3, 0.14710, 0.0036066, 0.5710, 0.00657, 0.01017, 14.46, 2430),
]

IDS = [row[0] for row in FIXTURE_D]


@pytest.fixture
def mission():
    return MissionConfig.from_yaml(default_mission_path())


@pytest.mark.parametrize("row", FIXTURE_D, ids=IDS)
def test_cd0_matches_fixture_d(mission, row):
    name, sref, s_wet, _cl, cd_ind, _e, cd0, _cdt, _lod, _nm = row
    got = drag_buildup(mission.cfe, s_wet, sref, cd_ind)["cd0"]
    assert got == pytest.approx(cd0, abs=5e-6), name


@pytest.mark.parametrize("row", FIXTURE_D, ids=IDS)
def test_cd_total_matches_fixture_d(mission, row):
    name, sref, s_wet, _cl, cd_ind, _e, _cd0, cd_total, _lod, _nm = row
    got = drag_buildup(mission.cfe, s_wet, sref, cd_ind)["cd_total"]
    assert got == pytest.approx(cd_total, abs=5e-6), name


@pytest.mark.parametrize("row", FIXTURE_D, ids=IDS)
def test_l_over_d_matches_fixture_d(mission, row):
    name, sref, s_wet, cl, cd_ind, _e, _cd0, _cdt, l_over_d, _nm = row
    got = cl / drag_buildup(mission.cfe, s_wet, sref, cd_ind)["cd_total"]
    assert got == pytest.approx(l_over_d, rel=0.005), name


@pytest.mark.parametrize("row", FIXTURE_D, ids=IDS)
def test_range_matches_fixture_d(mission, row):
    from pipeline.mission import compute_range

    name, sref, s_wet, cl, cd_ind, _e, _cd0, _cdt, _lod, range_nm = row
    l_over_d = cl / drag_buildup(mission.cfe, s_wet, sref, cd_ind)["cd_total"]
    got = compute_range(mission, l_over_d)["range_nm"]
    assert got == pytest.approx(range_nm, rel=0.01), name


# ---------------------------------------------------------------------------
# Full build_results() against the real avl_iter2 / avl_iter3 output files
# ---------------------------------------------------------------------------


def make_case(tmp_path, name, s_wet_override=None, **kw):
    (tmp_path / "m.stl").write_text("solid x\nendsolid x\n")
    (tmp_path / "avl.exe").write_bytes(b"MZ")
    data = {"name": name, "stl_path": "m.stl", "avl_exe": "avl.exe"}
    if s_wet_override is not None:
        data["s_wet_override_m2"] = s_wet_override
    data.update(kw)
    return CaseConfig.from_dict(data, base_dir=tmp_path)


@pytest.mark.parametrize("folder,expected_lod,expected_nm,expected_e", [
    ("avl_iter2", 13.58, 2281, 0.4781),
    ("avl_iter3", 14.46, 2430, 0.5710),
])
def test_end_to_end_from_real_avl_files(tmp_path, mission, folder,
                                        expected_lod, expected_nm, expected_e):
    """Parse the actual totals.txt/stability.txt and reproduce Fixture D."""
    from pipeline.parse_avl import parse_stability, parse_totals
    from pipeline.paths import project_root

    root = project_root() / folder
    if not (root / "totals.txt").is_file():
        pytest.skip(f"{folder} not present")

    totals = parse_totals(root / "totals.txt")
    stability = parse_stability(root / "stability.txt")
    s_wet = {"avl_iter2": 151.1, "avl_iter3": 151.3}[folder]

    results = build_results(
        case=make_case(tmp_path, folder, s_wet_override=s_wet),
        mission=mission, totals=totals, stability=stability,
        geometry={"ar": totals["Bref"] ** 2 / totals["Sref"], "s_wet_mesh_m2": s_wet},
        cl_target=compute_cl_target(mission, totals["Sref"]),
        run_dir=str(root), timestamp="2026-08-27T00:00:00",
    )

    assert results["aero"]["e"] == pytest.approx(expected_e, abs=1e-4)
    assert results["aero"]["l_over_d"] == pytest.approx(expected_lod, rel=0.005)
    assert results["range"]["range_nm"] == pytest.approx(expected_nm, rel=0.01)


def test_static_margin_sign_is_positive_and_stable_for_iter3(tmp_path, mission):
    """The empirically verified convention: X aft-positive, so SM > 0 = stable."""
    from pipeline.parse_avl import parse_stability, parse_totals
    from pipeline.paths import project_root

    root = project_root() / "avl_iter3"
    if not (root / "stability.txt").is_file():
        pytest.skip("avl_iter3 not present")

    totals = parse_totals(root / "totals.txt")
    stability = parse_stability(root / "stability.txt")
    results = build_results(
        case=make_case(tmp_path, "avl_iter3", s_wet_override=151.3),
        mission=mission, totals=totals, stability=stability,
        geometry={"ar": 3.493, "s_wet_mesh_m2": 151.3},
        cl_target=0.1471, run_dir=str(root), timestamp="2026-08-27T00:00:00",
    )
    s = results["stability"]
    assert s["static_margin_pct"] == pytest.approx(12.17, abs=0.05)
    assert s["static_margin_pct"] > 0
    assert s["pitch_stable"] is True          # Cma < 0, convention-independent
    assert s["static_margin_crosscheck"]["consistent"] is True
    assert s["spirally_stable"] is True


@pytest.mark.parametrize("folder", ["avl_iter2", "avl_iter3"])
def test_all_logged_iterations_are_pitch_stable(folder):
    """Cma < 0 is sign-convention-independent; it settles 'stable' by itself."""
    from pipeline.parse_avl import parse_stability
    from pipeline.paths import project_root

    path = project_root() / folder / "stability.txt"
    if not path.is_file():
        pytest.skip(f"{folder} not present")
    assert parse_stability(path)["Cma"] < 0.0


# ---------------------------------------------------------------------------
# S_wet source precedence
# ---------------------------------------------------------------------------


def test_override_takes_precedence_over_mesh_area():
    from pipeline.results import wetted_area

    value, source = wetted_area({"s_wet_mesh_m2": 151.324}, 152.0)
    assert value == 152.0 and source == "case_config_override"


def test_mesh_area_used_when_no_override():
    from pipeline.results import wetted_area

    value, source = wetted_area({"s_wet_mesh_m2": 151.324}, None)
    assert value == pytest.approx(151.324) and source == "mesh_area"


def test_missing_both_is_an_error():
    from pipeline.results import wetted_area

    with pytest.raises(ValueError, match="no wetted area"):
        wetted_area({}, None)


def test_override_changes_cd0_and_therefore_l_over_d(mission):
    """S_wet feeds CD0 directly, so the override must actually matter."""
    a = drag_buildup(mission.cfe, 151.3, 69.120, 0.0036066)
    b = drag_buildup(mission.cfe, 160.0, 69.120, 0.0036066)
    assert b["cd0"] > a["cd0"]
    assert 0.14710 / b["cd_total"] < 0.14710 / a["cd_total"]

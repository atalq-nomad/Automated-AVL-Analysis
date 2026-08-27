"""Stage 2 tests — case config loading and validation."""

import pytest

from pipeline.config import STL_TO_AVL_DEFAULTS, CaseConfig
from pipeline.paths import project_root


@pytest.fixture
def workspace(tmp_path):
    """A directory holding a stand-in STL and AVL executable."""
    (tmp_path / "P1_revised1.stl").write_text("solid x\nendsolid x\n")
    (tmp_path / "avl352.exe").write_bytes(b"MZ")
    return tmp_path


def minimal(**overrides):
    data = {"name": "iteration_4", "stl_path": "P1_revised1.stl", "avl_exe": "avl352.exe"}
    data.update(overrides)
    return data


def test_defaults_match_stl_to_avl(workspace):
    cfg = CaseConfig.from_dict(minimal(), base_dir=workspace)
    for key, default in STL_TO_AVL_DEFAULTS.items():
        assert getattr(cfg, key) == default
    assert cfg.s_wet_override_m2 is None


def test_relative_paths_resolve_against_base_dir(workspace):
    cfg = CaseConfig.from_dict(minimal(), base_dir=workspace)
    assert cfg.stl_path == (workspace / "P1_revised1.stl").resolve()
    assert cfg.avl_exe.is_absolute()


def test_units_drive_the_scale_factor(workspace):
    assert CaseConfig.from_dict(minimal(units="mm"), base_dir=workspace).scale == 1e-3
    assert CaseConfig.from_dict(minimal(units="m"), base_dir=workspace).scale == 1.0
    assert CaseConfig.from_dict(minimal(units="in"), base_dir=workspace).scale == 0.0254


def test_s_wet_override_is_carried_through(workspace):
    cfg = CaseConfig.from_dict(minimal(s_wet_override_m2=151.3), base_dir=workspace)
    assert cfg.s_wet_override_m2 == pytest.approx(151.3)


def test_extract_kwargs_match_stl_to_avl_signature(workspace):
    import inspect

    import stl_to_avl

    cfg = CaseConfig.from_dict(minimal(units="mm", n_sections=15), base_dir=workspace)
    kwargs = cfg.extract_kwargs()
    accepted = set(inspect.signature(stl_to_avl.extract).parameters)
    assert set(kwargs) <= accepted
    assert kwargs["scale"] == 1e-3
    assert kwargs["n_sections"] == 15

    accepted_write = set(inspect.signature(stl_to_avl.write_avl).parameters)
    assert set(cfg.write_avl_kwargs()) <= accepted_write


@pytest.mark.parametrize("field", ["name", "stl_path", "avl_exe"])
def test_missing_required_field_names_it(workspace, field):
    data = minimal()
    del data[field]
    with pytest.raises(ValueError, match=field):
        CaseConfig.from_dict(data, base_dir=workspace)


def test_unknown_field_is_rejected(workspace):
    with pytest.raises(ValueError, match="s_wet_overide_m2"):
        CaseConfig.from_dict(minimal(s_wet_overide_m2=151.3), base_dir=workspace)


def test_bad_units_rejected(workspace):
    with pytest.raises(ValueError, match="units"):
        CaseConfig.from_dict(minimal(units="cm"), base_dir=workspace)


@pytest.mark.parametrize("axes", ["xyz", "xzy", "x-zy", "z", "-z", "-x-y-z"])
def test_valid_axes_specs_accepted(workspace, axes):
    assert CaseConfig.from_dict(minimal(axes=axes), base_dir=workspace).axes == axes


@pytest.mark.parametrize("axes", ["xxy", "xy", "abc", "xyzz", ""])
def test_invalid_axes_specs_rejected(workspace, axes):
    with pytest.raises(ValueError, match="axes"):
        CaseConfig.from_dict(minimal(axes=axes), base_dir=workspace)


def test_supersonic_mach_rejected(workspace):
    with pytest.raises(ValueError, match="mach"):
        CaseConfig.from_dict(minimal(mach=1.2), base_dir=workspace)


def test_name_with_a_path_separator_rejected(workspace):
    with pytest.raises(ValueError, match="name"):
        CaseConfig.from_dict(minimal(name="iter/4"), base_dir=workspace)


def test_missing_stl_fails_at_load_not_mid_run(workspace):
    with pytest.raises(FileNotFoundError, match="stl_path"):
        CaseConfig.from_dict(minimal(stl_path="nope.stl"), base_dir=workspace)


def test_missing_avl_exe_fails_at_load(workspace):
    with pytest.raises(FileNotFoundError, match="avl_exe"):
        CaseConfig.from_dict(minimal(avl_exe="nope.exe"), base_dir=workspace)


def test_non_numeric_n_sections_rejected(workspace):
    with pytest.raises(ValueError, match="n_sections"):
        CaseConfig.from_dict(minimal(n_sections="twenty"), base_dir=workspace)


def test_shipped_iteration_4_case_loads():
    """The checked-in cases/iteration_4.yaml parses with the expected fields.

    require_files=False on purpose: the STL and avl352.exe are deliberately not
    in the repo (see .gitignore), so a fresh clone has neither. This test is
    about the YAML being well-formed, which must hold everywhere.
    """
    cfg = CaseConfig.from_yaml(
        project_root() / "cases" / "iteration_4.yaml",
        base_dir=project_root(),
        require_files=False,
    )
    assert cfg.name == "iteration_4"
    assert cfg.units == "mm"
    assert cfg.axes == "zxy"
    assert cfg.stl_path.name == "P1_revised1.stl"
    assert cfg.avl_exe.name == "avl352.exe"


def test_shipped_case_points_at_real_files_when_they_are_present():
    """Only meaningful in a working tree that has the geometry and AVL."""
    root = project_root()
    if not (root / "P1_revised1.stl").is_file() or not (root / "avl352.exe").is_file():
        pytest.skip("STL and/or avl352.exe not present — expected in a fresh clone")
    cfg = CaseConfig.from_yaml(root / "cases" / "iteration_4.yaml", base_dir=root)
    assert cfg.stl_path.is_file()
    assert cfg.avl_exe.is_file()


def test_to_dict_is_json_serialisable(workspace):
    import json

    cfg = CaseConfig.from_dict(minimal(), base_dir=workspace)
    json.dumps(cfg.to_dict())

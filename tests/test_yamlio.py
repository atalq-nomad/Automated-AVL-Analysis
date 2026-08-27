"""Tests for the flat-YAML fallback reader.

PyYAML is not installed in the interpreter this project runs on, so the
fallback is the live path today. It is tested directly regardless of which
loader load_yaml() happens to pick.
"""

import pytest

from pipeline.yamlio import HAVE_PYYAML, YamlError, load_yaml, loads_flat

MISSION_TEXT = """\
# a comment
cruise_altitude_m: 12497      # FL410
cruise_mach: 0.75
mtom_kg: 7300
tsfc_kg_per_Ns: 6.63e-6
name: iteration_4
quoted: "iteration 4"
optional:
flag: true
"""


def test_scalar_types():
    d = loads_flat(MISSION_TEXT)
    assert d["cruise_altitude_m"] == 12497 and isinstance(d["cruise_altitude_m"], int)
    assert d["cruise_mach"] == 0.75
    assert d["tsfc_kg_per_Ns"] == pytest.approx(6.63e-6)
    assert isinstance(d["tsfc_kg_per_Ns"], float)
    assert d["name"] == "iteration_4"
    assert d["quoted"] == "iteration 4"
    assert d["optional"] is None
    assert d["flag"] is True


def test_trailing_comments_stripped_but_hash_in_a_string_kept():
    d = loads_flat('a: 1  # note\nb: "c#d"\n')
    assert d["a"] == 1
    assert d["b"] == "c#d"


def test_duplicate_key_rejected():
    with pytest.raises(YamlError, match="duplicate"):
        loads_flat("a: 1\na: 2\n")


def test_nesting_rejected_rather_than_half_read():
    with pytest.raises(YamlError, match="flat"):
        loads_flat("outer:\n  inner: 1\n")


def test_lists_rejected():
    with pytest.raises(YamlError, match="lists"):
        loads_flat("items:\n- a\n")


def test_missing_colon_rejected():
    with pytest.raises(YamlError, match="expected"):
        loads_flat("just a line\n")


def test_load_yaml_missing_file():
    with pytest.raises(YamlError, match="not found"):
        load_yaml("no_such_file.yaml")


def test_load_yaml_empty_file(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("# nothing here\n")
    with pytest.raises(YamlError, match="empty"):
        load_yaml(p)


def test_load_yaml_reads_a_real_file(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(MISSION_TEXT, encoding="utf-8")
    d = load_yaml(p)
    assert d["mtom_kg"] == 7300
    assert d["tsfc_kg_per_Ns"] == pytest.approx(6.63e-6)


def test_both_loaders_agree_when_pyyaml_is_present(tmp_path):
    if not HAVE_PYYAML:
        pytest.skip("PyYAML not installed")
    import yaml

    assert yaml.safe_load(MISSION_TEXT) == loads_flat(MISSION_TEXT)

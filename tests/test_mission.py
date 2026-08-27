"""Stage 1 tests.

The regression points are Fixture D from the build plan — the three iterations
run by hand. A pipeline that does not reproduce them is wrong, so they are
asserted here rather than described.
"""

import math

import pytest

from pipeline.mission import (
    H_TROPOPAUSE,
    MissionConfig,
    compute_cl_target,
    compute_range,
    cruise_state,
    default_mission_path,
    isa_density_and_speed_of_sound,
)

# Fixture D: (Sref m2, CLtot, L/D, range nm). All three used the same mission.
FIXTURE_D = [
    ("iter1", 70.526, 0.14400, 15.3, 2570),
    ("iter2", 69.118, 0.14710, 13.58, 2281),
    ("iter3", 69.120, 0.14710, 14.46, 2430),
]


@pytest.fixture
def mission():
    return MissionConfig.from_yaml(default_mission_path())


def test_mission_yaml_matches_tonights_values(mission):
    assert mission.cruise_altitude_m == 12497
    assert mission.cruise_mach == 0.75
    assert mission.mtom_kg == 7300
    assert mission.cfe == 0.0030
    assert mission.tsfc_kg_per_Ns == pytest.approx(6.63e-6)
    assert mission.tank_volume_m3 == 10.0
    assert mission.lh2_fill_fraction == 0.90
    assert mission.lh2_density_kgm3 == 70.8


# -- ISA ------------------------------------------------------------------


def test_isa_at_tropopause_is_the_reference_point():
    rho, a = isa_density_and_speed_of_sound(H_TROPOPAUSE)
    assert rho == pytest.approx(0.3639)
    assert a == pytest.approx(295.07, abs=0.02)


def test_isa_at_cruise_altitude(mission):
    rho, a = isa_density_and_speed_of_sound(mission.cruise_altitude_m)
    assert rho == pytest.approx(0.2874, rel=1e-3)
    assert a == pytest.approx(295.07, abs=0.02)  # isothermal: same as at 11 km


def test_isa_below_tropopause_refuses_rather_than_guessing():
    with pytest.raises(NotImplementedError, match="troposphere"):
        isa_density_and_speed_of_sound(8000.0)


def test_isa_above_the_isothermal_layer_refuses():
    with pytest.raises(NotImplementedError):
        isa_density_and_speed_of_sound(25000.0)


def test_cruise_state_is_self_consistent(mission):
    s = cruise_state(mission)
    assert s["velocity_ms"] == pytest.approx(mission.cruise_mach * s["a_ms"])
    assert s["q_Pa"] == pytest.approx(0.5 * s["rho_kgm3"] * s["velocity_ms"] ** 2)
    assert s["velocity_ms"] == pytest.approx(221.3, abs=0.5)


# -- CL target (Fixture D) -------------------------------------------------


@pytest.mark.parametrize("label,sref,cl_expected,_lod,_range", FIXTURE_D)
def test_cl_target_reproduces_fixture_d(mission, label, sref, cl_expected, _lod, _range):
    cl = compute_cl_target(mission, sref)
    assert cl == pytest.approx(cl_expected, rel=0.02), label


def test_cl_target_scales_inversely_with_sref(mission):
    assert compute_cl_target(mission, 70.0) == pytest.approx(
        2.0 * compute_cl_target(mission, 140.0)
    )


def test_cl_target_rejects_nonpositive_sref(mission):
    with pytest.raises(ValueError):
        compute_cl_target(mission, 0.0)


# -- Range (Fixture D) -----------------------------------------------------


def test_fuel_and_mass_fraction(mission):
    r = compute_range(mission, 15.3)
    assert r["fuel_mass_kg"] == pytest.approx(637.2)          # 70.8 * 10 * 0.90
    assert r["wf_kg"] == pytest.approx(6662.8)                # 7300 - 637.2
    assert r["ln_wi_wf"] == pytest.approx(math.log(7300 / 6662.8))


@pytest.mark.parametrize("label,_sref,_cl,l_over_d,range_nm", FIXTURE_D)
def test_range_reproduces_fixture_d(mission, label, _sref, _cl, l_over_d, range_nm):
    r = compute_range(mission, l_over_d)
    assert r["range_nm"] == pytest.approx(range_nm, rel=0.02), label
    assert r["range_km"] == pytest.approx(r["range_nm"] * 1.852, rel=1e-9)


def test_range_is_linear_in_l_over_d(mission):
    assert compute_range(mission, 20.0)["range_m"] == pytest.approx(
        2.0 * compute_range(mission, 10.0)["range_m"]
    )


def test_range_echoes_cl_target_for_reporting(mission):
    assert compute_range(mission, 15.3, cl_target=0.144)["cl_target"] == 0.144


def test_range_rejects_nonpositive_l_over_d(mission):
    with pytest.raises(ValueError):
        compute_range(mission, 0.0)


# -- Config validation -----------------------------------------------------


def test_missing_field_names_the_field():
    data = MissionConfig.from_yaml(default_mission_path()).__dict__.copy()
    del data["tsfc_kg_per_Ns"]
    with pytest.raises(ValueError, match="tsfc_kg_per_Ns"):
        MissionConfig.from_dict(data)


def test_unknown_field_is_rejected_not_ignored():
    data = MissionConfig.from_yaml(default_mission_path()).__dict__.copy()
    data["cruise_altitud_m"] = 12497  # typo
    with pytest.raises(ValueError, match="cruise_altitud_m"):
        MissionConfig.from_dict(data)


def test_fill_fraction_out_of_range_is_rejected():
    data = MissionConfig.from_yaml(default_mission_path()).__dict__.copy()
    data["lh2_fill_fraction"] = 1.5
    with pytest.raises(ValueError, match="lh2_fill_fraction"):
        MissionConfig.from_dict(data)


def test_fuel_heavier_than_mtom_is_rejected():
    data = MissionConfig.from_yaml(default_mission_path()).__dict__.copy()
    data["tank_volume_m3"] = 200.0
    with pytest.raises(ValueError, match="MTOM"):
        MissionConfig.from_dict(data)


def test_tsfc_is_read_as_a_number_not_a_string(mission):
    # 6.63e-6 in YAML is a classic silent-string trap.
    assert isinstance(mission.tsfc_kg_per_Ns, float)

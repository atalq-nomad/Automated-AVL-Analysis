"""Stage 11 tests — planform split, group weights, MTOM closure, PC-24 check."""

from dataclasses import replace

import pytest

from pipeline.mass_model import (
    PC24,
    MassModelError,
    centerbody_mass_kg,
    converge_mtom,
    landing_gear_mass_kg,
    outer_wing_mass_kg,
    pc24_crosscheck,
    propulsion_mass_kg,
    sensitivity,
    systems_mass_kg,
    tank_system_mass_kg,
)
from pipeline.mission import MissionConfig, cruise_state, default_mission_path
from pipeline.planform import (
    split_planform,
    stations_from_avl,
    steepest_gradient_break,
)
from pipeline.paths import project_root

AVL3 = project_root() / "avl_iter3" / "bwb.avl"


@pytest.fixture
def mission():
    return MissionConfig.from_yaml(default_mission_path())


@pytest.fixture
def q_pa(mission):
    return cruise_state(mission)["q_Pa"]


@pytest.fixture
def stations():
    if not AVL3.is_file():
        pytest.skip("avl_iter3/bwb.avl not present")
    st = stations_from_avl(AVL3)
    for s in st:
        s["tc"] = 0.15
    return st


@pytest.fixture
def planform(stations, mission):
    return split_planform(stations, 69.1204, mission.centerbody_span_fraction)


# ---------------------------------------------------------------------------
# Planform
# ---------------------------------------------------------------------------


def test_reads_every_section_from_real_geometry(stations):
    assert len(stations) == 21
    assert stations[0]["chord"] == pytest.approx(10.263456, abs=1e-4)
    assert stations[-1]["chord"] == pytest.approx(0.886634, abs=1e-4)


def test_stations_are_sorted_outboard(stations):
    ys = [s["y"] for s in stations]
    assert ys == sorted(ys)


def test_split_areas_reconstruct_sref(planform):
    """Centerbody + outer must add back up to the reference area."""
    assert planform["planform_area_m2"] == pytest.approx(planform["sref_m2"], rel=0.01)


def test_centerbody_is_the_larger_share(planform):
    assert planform["centerbody_area_m2"] > planform["outer_wing_area_m2"]


def test_outer_panel_geometry_is_wing_like(planform):
    assert 3.0 < planform["outer_aspect_ratio"] < 8.0
    assert 0.05 < planform["outer_taper_ratio"] < 0.5
    assert 20.0 < planform["outer_sweep_quarter_chord_deg"] < 55.0


def test_steepest_gradient_break_lands_in_the_transition(stations):
    """Diagnostic: the chord knee on this geometry is around y = 2.4 m."""
    assert 1.8 < steepest_gradient_break(stations) < 3.2


def test_bigger_centerbody_fraction_moves_area_inboard(stations):
    small = split_planform(stations, 69.12, 0.25)
    large = split_planform(stations, 69.12, 0.45)
    assert large["centerbody_area_m2"] > small["centerbody_area_m2"]
    assert large["outer_wing_area_m2"] < small["outer_wing_area_m2"]


def test_invalid_split_fraction_rejected(stations):
    with pytest.raises(ValueError):
        split_planform(stations, 69.12, 1.5)


# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------


def test_wing_mass_is_plausible_and_scales_with_mtom(planform, q_pa):
    kw = dict(area_m2=planform["outer_wing_area_m2"],
              aspect_ratio=planform["outer_aspect_ratio"],
              sweep_deg=planform["outer_sweep_quarter_chord_deg"],
              taper_ratio=planform["outer_taper_ratio"], tc=0.15,
              q_pa=q_pa, ultimate_load_factor=3.75)
    light = outer_wing_mass_kg(mtom_kg=5000.0, **kw)
    heavy = outer_wing_mass_kg(mtom_kg=9000.0, **kw)
    assert 50.0 < light < 1000.0
    assert heavy > light


def test_zero_wing_fuel_does_not_zero_the_wing(planform, q_pa):
    """Raymer's Wfw^0.0035 term would collapse the product; it must be taken as 1."""
    mass = outer_wing_mass_kg(
        mtom_kg=7300.0, area_m2=planform["outer_wing_area_m2"],
        aspect_ratio=planform["outer_aspect_ratio"],
        sweep_deg=planform["outer_sweep_quarter_chord_deg"],
        taper_ratio=planform["outer_taper_ratio"], tc=0.15, q_pa=q_pa,
        ultimate_load_factor=3.75, wing_fuel_kg=0.0)
    assert mass > 0.0


def test_centerbody_mass_is_linear_in_sigma_and_area():
    assert centerbody_mass_kg(60.0, 47.18) == pytest.approx(60.0 * 47.18)
    assert centerbody_mass_kg(120.0, 47.18) == pytest.approx(2 * centerbody_mass_kg(60.0, 47.18))


def test_gear_and_systems_are_fractions_of_mtom():
    assert landing_gear_mass_kg(0.035, 7300.0) == pytest.approx(255.5)
    assert systems_mass_kg(0.13, 7300.0) == pytest.approx(949.0)


def test_propulsion_scales_with_thrust():
    a = propulsion_mass_kg(mtom_kg=7300.0, thrust_to_weight=0.38,
                           specific_weight_kg_per_kn=19.0, installation_factor=1.4)
    b = propulsion_mass_kg(mtom_kg=7300.0, thrust_to_weight=0.76,
                           specific_weight_kg_per_kn=19.0, installation_factor=1.4)
    assert b == pytest.approx(2 * a)
    assert 300.0 < a < 1500.0


def test_tank_system_follows_the_eta_g_formula():
    mass, source = tank_system_mass_kg(637.2, 0.50)
    assert mass == pytest.approx(637.2)          # 1/0.5 - 1 = 1
    assert source == "eta_g_proxy"
    assert tank_system_mass_kg(637.2, 0.25)[0] == pytest.approx(637.2 * 3.0)


def test_tank_override_takes_precedence_like_s_wet_override():
    mass, source = tank_system_mass_kg(637.2, 0.50, override_kg=410.0)
    assert mass == 410.0
    assert source == "case_config_override"


def test_invalid_eta_g_rejected():
    with pytest.raises(MassModelError):
        tank_system_mass_kg(637.2, 0.0)


# ---------------------------------------------------------------------------
# Convergence and the cap gate
# ---------------------------------------------------------------------------


def test_mtom_converges(mission, planform, q_pa):
    r = converge_mtom(mission, planform, q_pa)
    assert r.converged is True
    assert r.passes < 40
    assert 5000.0 < r.mtom_kg < 12000.0


def test_mtom_balances_its_own_groups(mission, planform, q_pa):
    r = converge_mtom(mission, planform, q_pa)
    assert r.oew_kg == pytest.approx(sum(r.groups.values()))
    assert r.mtom_kg == pytest.approx(
        r.oew_kg + r.payload_kg + r.crew_kg + r.fuel_kg, rel=mission.mtom_tolerance)


def test_converged_result_is_independent_of_the_starting_guess(mission, planform, q_pa):
    a = converge_mtom(mission, planform, q_pa, initial_guess_kg=5000.0).mtom_kg
    b = converge_mtom(mission, planform, q_pa, initial_guess_kg=11000.0).mtom_kg
    assert a == pytest.approx(b, rel=2 * mission.mtom_tolerance)


def test_cap_is_a_gate_on_the_result_not_an_input(mission, planform, q_pa):
    """Raising the cap must not change the converged MTOM — only the verdict."""
    base = converge_mtom(mission, planform, q_pa)
    raised = converge_mtom(replace(mission, mtom_cap_kg=20000.0), planform, q_pa)
    assert raised.mtom_kg == pytest.approx(base.mtom_kg, rel=1e-9)
    assert raised.gate_passed is True


def test_gate_fails_when_over_the_cap(mission, planform, q_pa):
    heavy = converge_mtom(replace(mission, sigma_centerbody_kg_m2=200.0), planform, q_pa)
    assert heavy.mtom_kg > heavy.cap_kg
    assert heavy.gate_passed is False
    assert heavy.to_dict()["margin_to_cap_kg"] < 0


def test_heavier_tank_gives_heavier_aircraft(mission, planform, q_pa):
    poor = converge_mtom(replace(mission, eta_g_tank=0.30), planform, q_pa)
    good = converge_mtom(replace(mission, eta_g_tank=0.60), planform, q_pa)
    assert poor.mtom_kg > good.mtom_kg


def test_structural_fraction_excludes_the_tank_system(mission, planform, q_pa):
    """The tank has no kerosene equivalent; including it corrupts the PC-24 check."""
    r = converge_mtom(mission, planform, q_pa)
    assert r.structure_kg == pytest.approx(
        r.groups["outer_wing"] + r.groups["centerbody"] + r.groups["landing_gear"])
    assert "tank_system" not in ("outer_wing", "centerbody", "landing_gear")
    assert r.structure_kg < r.oew_kg


def test_result_is_json_serialisable(mission, planform, q_pa):
    import json
    json.dumps(converge_mtom(mission, planform, q_pa).to_dict())


# ---------------------------------------------------------------------------
# PC-24 cross-check
# ---------------------------------------------------------------------------


def test_pc24_crosscheck_runs_the_same_model(mission, planform, q_pa):
    c = pc24_crosscheck(mission, converge_mtom(mission, planform, q_pa), q_pa)
    assert c["reference_mtow_kg"] == PC24["mtow_kg"]
    assert c["implied_structure_kg"] == pytest.approx(
        PC24["oew_kg"] - c["model_propulsion_kg"] - c["model_systems_kg"])
    assert 0.2 < c["implied_structural_fraction"] < 0.6


def test_pc24_flag_fires_when_the_bwb_comes_out_lighter(mission, planform, q_pa):
    """The red flag this check exists to raise."""
    light = converge_mtom(replace(mission, sigma_centerbody_kg_m2=5.0), planform, q_pa)
    c = pc24_crosscheck(mission, light, q_pa)
    assert c["bwb_lighter_than_reference"] is True
    assert "RED FLAG" in c["flag"]
    assert "citation" in c["flag"]


def test_pc24_flag_is_quiet_at_the_default(mission, planform, q_pa):
    c = pc24_crosscheck(mission, converge_mtom(mission, planform, q_pa), q_pa)
    assert c["bwb_lighter_than_reference"] is False
    assert c["flag"].startswith("OK")


def test_pc24_figures_are_labelled_as_unverified():
    assert "verify" in PC24["source"].lower()


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


def test_sensitivity_reports_both_placeholders(mission, planform, q_pa):
    s = sensitivity(mission, planform, q_pa)
    assert len(s["sigma_centerbody_kg_m2"]) == 4
    assert len(s["eta_g_tank"]) == 4
    sigmas = [r["mtom_kg"] for r in s["sigma_centerbody_kg_m2"]]
    assert sigmas == sorted(sigmas)             # heavier sigma -> heavier aircraft
    etas = [r["eta_g"] for r in s["eta_g_tank"]]
    mtoms = [r["mtom_kg"] for r in s["eta_g_tank"]]
    assert etas == sorted(etas) and mtoms == sorted(mtoms, reverse=True)


def test_sensitivity_shows_the_gate_can_flip(mission, planform, q_pa):
    """Quoting one MTOM from unsourced inputs would overstate what this knows."""
    s = sensitivity(mission, planform, q_pa)
    verdicts = {r["gate_passed"] for r in s["sigma_centerbody_kg_m2"]}
    assert verdicts == {True, False}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_mass_fields_are_optional_on_an_old_config():
    old = {"cruise_altitude_m": 12497, "cruise_mach": 0.75, "mtom_kg": 7300,
           "cfe": 0.0030, "tsfc_kg_per_Ns": 6.63e-6, "tank_volume_m3": 10.0,
           "lh2_fill_fraction": 0.90, "lh2_density_kgm3": 70.8}
    cfg = MissionConfig.from_dict(old)
    assert cfg.mtom_cap_kg == 7300.0 and cfg.eta_g_tank == 0.50


@pytest.mark.parametrize("field_name,bad", [
    ("eta_g_tank", 0.0), ("eta_g_tank", 1.0),
    ("centerbody_span_fraction", 0.0), ("sigma_centerbody_kg_m2", -1.0),
    ("mtom_cap_kg", 0.0),
])
def test_invalid_mass_parameters_rejected(mission, field_name, bad):
    with pytest.raises(ValueError, match=field_name):
        replace(mission, **{field_name: bad}).validate()

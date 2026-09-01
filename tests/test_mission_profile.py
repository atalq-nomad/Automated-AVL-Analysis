"""Stage 10 tests — reserve-based mission profile."""

import math
from dataclasses import replace

import pytest

from pipeline.mission import MissionConfig, compute_range, default_mission_path
from pipeline.mission_profile import (
    breguet_factor,
    effective_fractions,
    fly_profile,
    hold_fuel_fraction,
    missed_approach_fuel_kg,
    required_fuel_no_reserve,
    solve_max_range,
    solve_required_fuel,
)

LOD3 = 14.4593          # iteration 3


@pytest.fixture
def mission():
    return MissionConfig.from_yaml(default_mission_path())


# ---------------------------------------------------------------------------
# The quick estimate must be untouched
# ---------------------------------------------------------------------------


def test_quick_estimate_still_reproduces_fixture_d(mission):
    """Stage 10 must not perturb the number iterations 1-3 were logged against."""
    assert compute_range(mission, 14.4593)["range_nm"] == pytest.approx(2430, rel=0.01)
    assert compute_range(mission, 15.3406)["range_nm"] == pytest.approx(2570, rel=0.01)
    assert compute_range(mission, 13.5785)["range_nm"] == pytest.approx(2281, rel=0.01)


def test_quick_estimate_is_labelled_as_optimistic(mission):
    out = compute_range(mission, LOD3)
    assert out["method"] == "breguet_single_segment_no_reserve"
    assert "QUICK ESTIMATE" in out["note"]


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def test_lhv_scaling_is_on_by_default(mission):
    """A unit-consistency fix, not a tuning knob — must not ship off."""
    assert mission.lhv_scaling_enabled is True


def test_uncorrected_fractions_are_still_reachable(mission):
    """Item 3: the pre-fix behaviour stays available as an explicit override."""
    f = effective_fractions(replace(mission, lhv_scaling_enabled=False))
    assert f == {"taxi": 0.995, "takeoff": 0.995, "climb": 0.980,
                 "descent": 0.990, "approach": 0.992}


def test_default_fractions_are_lhv_scaled(mission):
    f = effective_fractions(mission)
    ratio = mission.lhv_kerosene_MJ_per_kg / mission.lhv_lh2_MJ_per_kg
    assert f["climb"] == pytest.approx(1.0 - 0.020 * ratio)
    assert all(f[k] > raw for k, raw in
               [("taxi", 0.995), ("climb", 0.980), ("descent", 0.990)])


def test_uncorrected_default_gives_a_much_shorter_range(mission):
    """The defect this fixes is worth ~3.5x in range — pin the magnitude."""
    corrected = solve_max_range(mission, LOD3).trip_range_nm
    uncorrected = solve_max_range(replace(mission, lhv_scaling_enabled=False),
                                  LOD3).trip_range_nm
    assert corrected > 3.0 * uncorrected


def test_reserves_and_contingency_share_is_reported(mission):
    """Item 2: a reportable finding, not just an intermediate."""
    r = solve_max_range(mission, LOD3)
    d = r.to_dict()
    assert d["reserve_and_contingency_kg"] == pytest.approx(
        r.reserve_fuel_kg + r.contingency_kg)
    assert d["reserve_and_contingency_pct_of_loaded_fuel"] == pytest.approx(
        r.reserve_and_contingency_kg / r.fuel_available_kg * 100.0)
    assert 20.0 < d["reserve_and_contingency_pct_of_loaded_fuel"] < 40.0


def test_trip_and_reserve_shares_account_for_all_loaded_fuel(mission):
    d = solve_max_range(mission, LOD3).to_dict()
    total = d["trip_fuel_pct_of_loaded_fuel"] + d["reserve_and_contingency_pct_of_loaded_fuel"]
    assert total == pytest.approx(100.0, abs=1e-6)


def test_per_segment_shares_are_visible(mission):
    """The per-segment read is what caught the original bug; keep it exposed."""
    segs = solve_max_range(mission, LOD3).to_dict()["segments"]
    assert len(segs) == 10
    assert all("pct_of_loaded_fuel" in s for s in segs)
    climb = next(s for s in segs if s["name"] == "climb")
    assert climb["pct_of_loaded_fuel"] < 15.0   # was 23% before the LHV fix


def test_lhv_scaling_holds_segment_energy_not_mass(mission):
    scaled = effective_fractions(replace(mission, lhv_scaling_enabled=True))
    ratio = mission.lhv_kerosene_MJ_per_kg / mission.lhv_lh2_MJ_per_kg
    assert scaled["climb"] == pytest.approx(1.0 - 0.020 * ratio)
    assert scaled["climb"] > 0.980          # burns less LH2 mass for the same energy


def test_breguet_factor_matches_the_quick_estimate_formula(mission):
    """Same physics as compute_range, so the two must agree on one leg."""
    quick = compute_range(mission, LOD3)
    ratio = breguet_factor(mission, quick["range_m"], LOD3)
    assert -math.log(ratio) == pytest.approx(quick["ln_wi_wf"], rel=1e-9)


def test_zero_distance_burns_nothing(mission):
    assert breguet_factor(mission, 0.0, LOD3) == 1.0


def test_hold_fuel_scales_with_time_and_inversely_with_l_over_d(mission):
    base = hold_fuel_fraction(mission, LOD3)
    assert hold_fuel_fraction(replace(mission, hold_time_min=90.0), LOD3) == pytest.approx(2 * base)
    assert hold_fuel_fraction(mission, 2 * LOD3) == pytest.approx(base / 2)


def test_missed_approach_override_wins(mission):
    over = replace(mission, missed_approach_fuel_kg=42.0)
    assert missed_approach_fuel_kg(over, LOD3) == 42.0


def test_missed_approach_default_is_derived_not_zero(mission):
    value = missed_approach_fuel_kg(mission, LOD3)
    assert 1.0 < value < 100.0


# ---------------------------------------------------------------------------
# Segment bookkeeping
# ---------------------------------------------------------------------------


def test_profile_has_every_segment_in_order(mission):
    names = [s.name for s in fly_profile(mission, LOD3, 500_000).segments]
    assert names == [
        "taxi-out", "takeoff", "climb", "cruise (trip)", "descent",
        "approach (destination)", "missed approach", "climb + hold",
        "diversion cruise", "approach (alternate)",
    ]


def test_weights_chain_without_gaps(mission):
    segs = fly_profile(mission, LOD3, 500_000).segments
    assert segs[0].w_start_kg == pytest.approx(mission.mtom_kg)
    for a, b in zip(segs, segs[1:]):
        assert a.w_end_kg == pytest.approx(b.w_start_kg)


def test_every_segment_burns_fuel(mission):
    assert all(s.fuel_kg > 0 for s in fly_profile(mission, LOD3, 500_000).segments)


def test_trip_and_reserve_fuel_split_at_the_destination(mission):
    r = fly_profile(mission, LOD3, 500_000)
    assert r.trip_fuel_kg + r.reserve_fuel_kg == pytest.approx(
        mission.mtom_kg - r.landing_weight_kg)


def test_contingency_is_five_percent_of_trip_fuel(mission):
    r = fly_profile(mission, LOD3, 500_000)
    assert r.contingency_kg == pytest.approx(0.05 * r.trip_fuel_kg)


def test_total_required_includes_contingency_on_top(mission):
    r = fly_profile(mission, LOD3, 500_000)
    assert r.total_fuel_required_kg == pytest.approx(
        (mission.mtom_kg - r.landing_weight_kg) + r.contingency_kg)


# ---------------------------------------------------------------------------
# Forward solve
# ---------------------------------------------------------------------------


def test_forward_solve_consumes_exactly_the_available_fuel(mission):
    """The defining property: at max range the budget closes to the kilogram."""
    r = solve_max_range(mission, LOD3)
    assert r.total_fuel_required_kg == pytest.approx(r.fuel_available_kg, abs=1e-3)
    assert r.feasible is True


def test_forward_solve_matches_an_independent_closed_form(mission):
    """Cross-check the fixed-point against algebra that solves for contingency.

    Derivation (A = taxi.takeoff.climb, P = descent.approach,
    Q = hold.divert.approach, c = contingency fraction):
        W0(1+c) + m_missed.Q - W0.A.P.E.(Q+c) = F_available
    """
    f = effective_fractions(mission)
    a = f["taxi"] * f["takeoff"] * f["climb"]
    p = f["descent"] * f["approach"]
    q = ((1 - hold_fuel_fraction(mission, LOD3))
         * breguet_factor(mission, mission.diversion_distance_nm * 1852.0, LOD3)
         * f["approach"])
    w0, c = mission.mtom_kg, mission.contingency_fraction
    missed = missed_approach_fuel_kg(mission, LOD3)

    e = (w0 * (1 + c) + missed * q - mission.fuel_mass_kg) / (w0 * a * p * (q + c))
    v = mission.cruise_mach * math.sqrt(1.4 * 287.053 * 216.65)
    k = 9.80665 * mission.tsfc_kg_per_Ns / (v * LOD3)
    expected_m = -math.log(e) / k

    assert solve_max_range(mission, LOD3).trip_range_m == pytest.approx(expected_m, rel=1e-6)


def test_contingency_loop_is_converged_within_three_passes(mission):
    """The methodology doc's claim, checked at engineering tolerance."""
    exact = solve_max_range(mission, LOD3).trip_range_m
    contingency = 0.0
    for _ in range(3):
        from pipeline.mission_profile import _range_for_burnable_fuel
        rng = _range_for_burnable_fuel(mission, LOD3, mission.fuel_mass_kg - contingency)
        contingency = fly_profile(mission, LOD3, rng).contingency_kg
    # ~0.06% of range after three passes — "converged" at engineering
    # tolerance, which is what the methodology document claims. The solver
    # itself iterates to 1e-6 kg of contingency, which takes about 7 passes.
    assert rng == pytest.approx(exact, rel=1e-3)


def test_more_fuel_gives_more_range(mission):
    a = solve_max_range(mission, LOD3, fuel_available_kg=600.0).trip_range_nm
    b = solve_max_range(mission, LOD3, fuel_available_kg=800.0).trip_range_nm
    assert b > a


def test_better_l_over_d_gives_more_range(mission):
    assert (solve_max_range(mission, 16.0).trip_range_nm
            > solve_max_range(mission, 13.0).trip_range_nm)


def test_reserve_range_is_always_below_the_quick_estimate(mission):
    for lod in (13.0, 14.4593, 16.0):
        assert solve_max_range(mission, lod).trip_range_nm < compute_range(
            mission, lod)["range_nm"]


def test_infeasible_case_reports_rather_than_raising(mission):
    r = solve_max_range(mission, LOD3, fuel_available_kg=50.0)
    assert r.feasible is False
    assert r.trip_range_nm == 0.0
    assert "INFEASIBLE" in r.note


def test_longer_hold_and_diversion_both_cost_range(mission):
    base = solve_max_range(mission, LOD3).trip_range_nm
    assert solve_max_range(replace(mission, hold_time_min=90.0), LOD3).trip_range_nm < base
    assert solve_max_range(replace(mission, diversion_distance_nm=200.0),
                           LOD3).trip_range_nm < base


def test_zero_contingency_gives_more_range(mission):
    assert (solve_max_range(replace(mission, contingency_fraction=0.0), LOD3).trip_range_nm
            > solve_max_range(mission, LOD3).trip_range_nm)


# ---------------------------------------------------------------------------
# Inverse solve
# ---------------------------------------------------------------------------


def test_inverse_round_trips_against_the_forward_solve(mission):
    """Fuel needed for R, fed back in, must give R again."""
    target = 400.0
    needed = solve_required_fuel(mission, LOD3, target)["required_fuel_kg"]
    back = solve_max_range(mission, LOD3, fuel_available_kg=needed)
    assert back.trip_range_nm == pytest.approx(target, rel=1e-6)


def test_inverse_gives_tank_volume_consistent_with_fuel(mission):
    out = solve_required_fuel(mission, LOD3, 1000.0)
    assert out["required_tank_volume_m3"] == pytest.approx(
        out["required_fuel_kg"] / (mission.lh2_density_kgm3 * mission.lh2_fill_fraction))


def test_no_reserve_inverse_reproduces_the_hand_calculation(mission):
    """~15.9 m3 to close 4,000 nm, computed by hand under the quick method."""
    out = required_fuel_no_reserve(mission, LOD3, 4000.0)
    assert out["required_tank_volume_m3"] == pytest.approx(16.0, abs=0.3)


def test_no_reserve_inverse_inverts_compute_range(mission):
    """Consistency with the function it inverts, at that function's own answer."""
    quick = compute_range(mission, LOD3)
    out = required_fuel_no_reserve(mission, LOD3, quick["range_nm"])
    assert out["required_fuel_kg"] == pytest.approx(quick["fuel_mass_kg"], rel=1e-6)


def test_reserve_inverse_needs_more_fuel_than_no_reserve(mission):
    a = required_fuel_no_reserve(mission, LOD3, 2000.0)["required_fuel_kg"]
    b = solve_required_fuel(mission, LOD3, 2000.0)["required_fuel_kg"]
    assert b > a


def test_inverse_rejects_nonpositive_target(mission):
    with pytest.raises(ValueError):
        solve_required_fuel(mission, LOD3, 0.0)


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def test_reserve_fields_are_optional(mission):
    """A mission.yaml written before Stage 10 must still load."""
    old = {"cruise_altitude_m": 12497, "cruise_mach": 0.75, "mtom_kg": 7300,
           "cfe": 0.0030, "tsfc_kg_per_Ns": 6.63e-6, "tank_volume_m3": 10.0,
           "lh2_fill_fraction": 0.90, "lh2_density_kgm3": 70.8}
    cfg = MissionConfig.from_dict(old)
    assert cfg.f_climb == 0.980 and cfg.hold_time_min == 45.0
    assert cfg.missed_approach_fuel_kg is None


def test_shipped_mission_yaml_carries_the_new_parameters(mission):
    assert mission.diversion_distance_nm == 100.0
    assert mission.contingency_fraction == 0.05
    assert mission.hold_altitude_ft == 1500.0


def test_bool_field_parses_from_yaml_text():
    from pipeline.mission import _as_bool
    assert _as_bool("true", "x", "y") is True
    assert _as_bool(False, "x", "y") is False
    with pytest.raises(ValueError):
        _as_bool("maybe", "x", "y")


@pytest.mark.parametrize("field_name,bad", [
    ("f_climb", 1.5), ("f_taxi", 0.0), ("contingency_fraction", 1.0),
    ("hold_time_min", 0.0), ("diversion_distance_nm", -5.0),
])
def test_invalid_reserve_parameters_are_rejected(mission, field_name, bad):
    with pytest.raises(ValueError, match=field_name):
        replace(mission, **{field_name: bad}).validate()


def test_still_rejects_unknown_fields(mission):
    with pytest.raises(ValueError, match="f_climbb"):
        MissionConfig.from_dict({**{f: getattr(mission, f) for f in
                                    ("cruise_altitude_m", "cruise_mach", "mtom_kg", "cfe",
                                     "tsfc_kg_per_Ns", "tank_volume_m3",
                                     "lh2_fill_fraction", "lh2_density_kgm3")},
                                 "f_climbb": 0.98})

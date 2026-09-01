"""Stage 12 tests — Part C outer loop, and the honesty of what it reports."""

from dataclasses import replace

import pytest

from pipeline.mass_model import converge_mtom, sensitivity
from pipeline.mission import MissionConfig, cruise_state, default_mission_path
from pipeline.paths import project_root
from pipeline.planform import split_planform, stations_from_avl
from pipeline.sizing import converge_sizing, gate_verdict

AVL3 = project_root() / "avl_iter3" / "bwb.avl"
LOD3 = 14.4593


@pytest.fixture
def mission():
    return MissionConfig.from_yaml(default_mission_path())


@pytest.fixture
def q_pa(mission):
    return cruise_state(mission)["q_Pa"]


@pytest.fixture
def planform(mission):
    if not AVL3.is_file():
        pytest.skip("avl_iter3/bwb.avl not present")
    st = stations_from_avl(AVL3)
    for s in st:
        s["tc"] = 0.15
    return split_planform(st, 69.1204, mission.centerbody_span_fraction)


@pytest.fixture
def sized(mission, planform, q_pa):
    return converge_sizing(mission, planform, q_pa, LOD3)


# ---------------------------------------------------------------------------
# The outer loop
# ---------------------------------------------------------------------------


def test_loop_converges(sized):
    assert sized["converged"] is True
    assert sized["passes"] <= 3          # fuel is tank-fixed, so it settles fast


def test_reserve_range_is_flown_from_the_converged_mtom(mission, planform, q_pa):
    """The whole point of Part C's coupling."""
    from pipeline.mission_profile import solve_max_range

    s = converge_sizing(mission, planform, q_pa, LOD3)
    at_converged = solve_max_range(
        replace(mission, mtom_kg=s["converged_mtom_kg"]), LOD3).trip_range_nm
    assert s["reserve_range"]["trip_range_nm"] == pytest.approx(at_converged, rel=1e-9)


def test_converged_mtom_moves_the_reserve_range_the_right_way(mission, planform, q_pa):
    """Range is flown at the converged MTOM, so a lighter aircraft flies further.

    Direction rather than sign: whether the converged MTOM lands above or below
    the assumed 7300 kg depends on the mass inputs, and it moved below when the
    payload was corrected from 8 to 6 occupants.
    """
    from pipeline.mission_profile import solve_max_range

    s = converge_sizing(mission, planform, q_pa, LOD3)
    at_assumed = solve_max_range(mission, LOD3).trip_range_nm
    reserve = s["reserve_range"]["trip_range_nm"]
    if s["converged_mtom_kg"] < mission.mtom_kg:
        assert reserve > at_assumed
    elif s["converged_mtom_kg"] > mission.mtom_kg:
        assert reserve < at_assumed


def test_both_mtom_values_are_reported_not_just_one(sized, mission):
    assert sized["assumed_mtom_kg"] == mission.mtom_kg
    assert sized["converged_mtom_kg"] != mission.mtom_kg
    assert sized["mtom_delta_kg"] == pytest.approx(
        sized["converged_mtom_kg"] - sized["assumed_mtom_kg"])


def test_tank_override_flows_through_the_loop(mission, planform, q_pa):
    light = converge_sizing(mission, planform, q_pa, LOD3,
                            tank_mass_override_kg=200.0)
    assert light["mass_model"]["tank_system_source"] == "case_config_override"
    assert light["converged_mtom_kg"] < converge_sizing(
        mission, planform, q_pa, LOD3)["converged_mtom_kg"]


def test_avl_cl_target_still_uses_the_assumed_mtom(sized):
    """Deferred by the methodology; changing it would move Fixture D."""
    assert "CL target" in sized["note"] or "assumed mtom_kg" in sized["note"]


# ---------------------------------------------------------------------------
# The gate framing — the point of this stage's addition
# ---------------------------------------------------------------------------


def test_gate_reports_undecided_when_placeholders_flip_it(sized):
    gate = sized["gate"]
    assert gate["verdict_decided"] is False
    assert gate["flips_on"]
    assert "UNDECIDED" in gate["framing"]
    assert "unsourced placeholders" in gate["framing"]
    assert "NOT as a settled pass or fail" in gate["framing"]


def test_gate_still_reports_the_nominal_verdict(sized):
    gate = sized["gate"]
    assert gate["nominal_verdict"] in ("PASS", "FAIL")
    assert isinstance(gate["gate_passed"], bool)
    assert gate["margin_kg"] == pytest.approx(
        gate["cap_kg"] - gate["converged_mtom_kg"])


def test_gate_is_decided_when_the_sweep_does_not_flip(mission, planform, q_pa):
    """A robust verdict must not be labelled undecided."""
    generous = replace(mission, mtom_cap_kg=50000.0)
    sweep = sensitivity(generous, planform, q_pa)
    verdict = gate_verdict(converge_mtom(generous, planform, q_pa), sweep)
    assert verdict["verdict_decided"] is True
    assert verdict["flips_on"] == []
    assert "holds across the swept range" in verdict["framing"]


def test_gate_without_a_sweep_does_not_claim_undecided(mission, planform, q_pa):
    verdict = gate_verdict(converge_mtom(mission, planform, q_pa), None)
    assert verdict["verdict_decided"] is True


def test_sensitivity_travels_into_the_output(sized):
    """It must reach results.json, not stay in someone's terminal."""
    sweep = sized["sensitivity"]
    assert sweep is not None
    assert len(sweep["sigma_centerbody_kg_m2"]) == 4
    assert len(sweep["eta_g_tank"]) == 4


def test_provenance_travels_into_the_output(sized):
    prov = sized["mass_model"]["input_provenance"]
    assert "UNGROUNDED PLACEHOLDER" in prov["sigma_centerbody_kg_m2"]
    # Payload is sourced from the programme design brief, not a placeholder.
    assert "SOURCED" in prov["payload_kg"] and "6-passenger" in prov["payload_kg"]
    # Crew count is genuinely open, which is a different thing from ungrounded.
    assert prov["crew_kg"].startswith("OPEN")
    assert "1 vs 2 pilot" in prov["crew_kg"]


def test_payload_matches_the_design_brief(mission):
    """6 passengers x 100 kg. Was briefly 8 x 100, contradicting the brief."""
    from pipeline.mass_model import payload_and_crew_kg

    payload, crew, provenance = payload_and_crew_kg(mission)
    assert payload == 600.0
    assert crew == 200.0
    assert "6-passenger" in provenance and "design brief" in provenance


def test_pc24_derivation_is_stated_in_the_output(sized):
    """A later reader must not mistake it for a published PC-24 figure."""
    text = sized["pc24_crosscheck"]["implied_structure_derivation"]
    assert "NOT a published PC-24 structural weight" in text
    assert "THIS MODEL" in text


# ---------------------------------------------------------------------------
# results.json / log_entry.md
# ---------------------------------------------------------------------------


def test_results_json_keeps_the_old_fields_and_adds_sizing(mission, planform, q_pa):
    from pipeline.config import CaseConfig
    from pipeline.parse_avl import parse_stability, parse_totals
    from pipeline.results import build_results

    root = project_root() / "avl_iter3"
    case = CaseConfig.from_dict(
        {"name": "iter3", "stl_path": "x.stl", "avl_exe": "y.exe"},
        base_dir=root, require_files=False)
    r = build_results(
        case=case, mission=mission,
        totals=parse_totals(root / "totals.txt"),
        stability=parse_stability(root / "stability.txt"),
        geometry={"ar": 3.493, "s_wet_mesh_m2": 151.3, "planform": planform},
        cl_target=0.1471, run_dir=str(root), timestamp="t", q_pa=q_pa)

    # Pre-Stage-12 fields untouched.
    assert r["range"]["range_nm"] == pytest.approx(2427, rel=0.01)
    assert r["aero"]["l_over_d"] == pytest.approx(14.46, rel=0.005)
    # New block present and labelled.
    assert r["sizing"]["converged_mtom_kg"] > 0
    assert "reserve" in r["range_model_note"]


def test_log_entry_states_both_ranges_and_the_gate(mission, planform, q_pa):
    from pipeline.report import format_log_entry
    from tests.test_report import make_results

    base = make_results()
    base["sizing"] = converge_sizing(mission, planform, q_pa, LOD3)
    text = format_log_entry(base)

    assert "reserve-based range" in text
    assert "quick no-reserve estimate" in text
    assert "MTOM closure:" in text
    assert "MTOM gate:" in text
    assert "UNDECIDED" in text
    assert "Placeholder sensitivity" in text
    assert "UNSOURCED" in text


def test_log_entry_without_sizing_still_works(mission):
    """Entries for runs predating Stage 12 must still render."""
    from pipeline.report import format_log_entry
    from tests.test_report import make_results

    text = format_log_entry(make_results())
    assert "quick no-reserve estimate" in text
    assert "MTOM closure:" not in text


def test_conclusion_leads_with_the_reserve_number(mission, planform, q_pa):
    from pipeline.report import build_conclusion
    from tests.test_report import make_results

    base = make_results()
    base["sizing"] = converge_sizing(mission, planform, q_pa, LOD3)
    c = build_conclusion(base, None)
    assert c.index("reserve-based range") < c.index("quick no-reserve")
    assert "optimistic" in c

"""Stage 6 — log entry formatting and the trend-aware conclusion line."""

import json

import pytest

from pipeline.report import (
    append_running_log,
    build_conclusion,
    find_previous_results,
    format_log_entry,
)


def make_results(**over):
    base = {
        "name": "iteration_4", "concept": "P1", "iteration": 4,
        "timestamp": "2026-08-27T15:00:00", "run_dir": "outputs/iteration_4/x",
        "mission": {"cruise_altitude_m": 12497, "cruise_mach": 0.75, "mtom_kg": 7300,
                    "cfe": 0.0030, "tsfc_kg_per_Ns": 6.63e-6, "tank_volume_m3": 10.0,
                    "lh2_fill_fraction": 0.90, "lh2_density_kgm3": 70.8},
        "geometry": {"sref_m2": 69.120, "cref_m": 7.0165, "bref_m": 15.538,
                     "xref_m": 4.2352, "ar": 3.493, "s_wet_m2": 151.3,
                     "s_wet_source": "mesh_area", "s_wet_mesh_m2": 151.3,
                     "s_wet_override_m2": None},
        "aero": {"cl_target": 0.14717, "CLtot": 0.14710, "alpha_deg": 4.62314,
                 "CDind": 0.0036066, "CDvis": 0.0, "CDtot_avl": 0.00361,
                 "CLff": 0.14720, "CDff": 0.0034584, "e": 0.5710, "Cmtot": -0.06538,
                 "cd0": 0.0065668, "cd_total": 0.0101734, "l_over_d": 14.459},
        "stability": {"CLa": 2.531806, "Cma": -0.308260, "Xnp": 5.089442,
                      "static_margin_pct": 12.1748,
                      "static_margin_crosscheck": {"consistent": True},
                      "pitch_stable": True, "spiral": 4.185, "spirally_stable": True,
                      "Clb": -0.07736, "Cnb": 0.001827,
                      "Clr": 0.086006, "Cnr": -0.008502},
        "range": {"fuel_mass_kg": 637.2, "wf_kg": 6662.8, "ln_wi_wf": 0.091335,
                  "range_km": 4497.0, "range_nm": 2427.1},
        "avl_log": {"passed": True, "warnings": []},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def test_entry_has_the_exact_section_labels_from_the_plan():
    text = format_log_entry(make_results())
    for label in ("Assumptions:", "Geometry:", "Aero:", "Mass/Range:", "Conclusion:"):
        assert label in text, label


def test_header_format():
    assert format_log_entry(make_results()).startswith("**Concept P1 — Iteration 4**")


def test_header_without_an_iteration_number():
    text = format_log_entry(make_results(concept="P1", iteration=None))
    assert text.startswith("**Concept P1**")


def test_geometry_line_carries_sref_ar_swet():
    line = [l for l in format_log_entry(make_results()).splitlines()
            if l.startswith("Geometry:")][0]
    assert "Sref 69.120 m²" in line and "AR 3.493" in line and "S_wet 151.3 m²" in line


def test_aero_line_carries_every_required_quantity():
    line = [l for l in format_log_entry(make_results()).splitlines()
            if l.startswith("Aero:")][0]
    for token in ("CL 0.14710", "alpha 4.623°", "e 0.5710", "CD0 0.00657",
                  "CD_total 0.01017", "L/D 14.46", "Cma -0.3083/rad",
                  "static margin ~12.2% MAC"):
        assert token in line, token


def test_mass_range_line():
    line = [l for l in format_log_entry(make_results()).splitlines()
            if l.startswith("Mass/Range:")][0]
    assert "fuel 637.2 kg" in line and "Wf 6662.8 kg" in line
    assert "Range ≈ 4497 km / 2427 nm" in line


def test_assumptions_line_uses_mission_values():
    line = [l for l in format_log_entry(make_results()).splitlines()
            if l.startswith("Assumptions:")][0]
    assert "12497 m" in line and "M0.75" in line and "7300 kg (MTOM)" in line
    assert "LH2 fill 90%" in line and "6.63e-06" in line


# ---------------------------------------------------------------------------
# Conclusion line
# ---------------------------------------------------------------------------


def test_conclusion_leads_with_l_over_d_and_range():
    c = build_conclusion(make_results(), None)
    assert "L/D 14.46" in c and "2427 nm" in c


def test_no_flags_when_nothing_moved():
    prev = make_results()
    assert "Flags:" not in build_conclusion(make_results(), prev)


def test_large_e_change_is_flagged():
    prev = make_results(aero={"e": 0.4781})       # iter2 -> iter3 is +19%
    c = build_conclusion(make_results(), prev)
    assert "Oswald e moved +19%" in c
    assert "twist" in c or "loading" in c


def test_small_e_change_is_not_flagged():
    assert "Oswald e moved" not in build_conclusion(
        make_results(), make_results(aero={"e": 0.5600})
    )


def test_large_cma_change_is_flagged():
    c = build_conclusion(make_results(), make_results(stability={"Cma": -0.15}))
    assert "Cma moved" in c


def test_pitch_instability_is_flagged():
    c = build_conclusion(make_results(stability={"Cma": 0.12, "pitch_stable": False}), None)
    assert "NOT negative" in c and "pitch-unstable" in c


def test_spiral_divergence_is_flagged():
    c = build_conclusion(make_results(stability={"spiral": 0.4, "spirally_stable": False}), None)
    assert "spirally divergent" in c


# -- LEFIND surfacing, the automated "worth checking" note ------------------


def test_lefind_warning_is_surfaced_in_the_conclusion():
    r = make_results(avl_log={"passed": True, "warnings": [
        {"section": "sections/sec_19.dat", "lineno": 37}]})
    c = build_conclusion(r, None)
    assert "1 LEFIND warning(s)" in c
    assert "sections/sec_19.dat" in c
    assert "sharp-nosed" in c


def test_multiple_lefind_sections_are_listed_once_each():
    r = make_results(avl_log={"passed": True, "warnings": [
        {"section": "sections/sec_19.dat"}, {"section": "sections/sec_19.dat"},
        {"section": "sections/sec_20.dat"}]})
    c = build_conclusion(r, None)
    assert "3 LEFIND warning(s)" in c
    assert c.count("sec_19.dat") == 1 and "sec_20.dat" in c


def test_lefind_appears_in_the_rendered_entry():
    r = make_results(avl_log={"passed": True, "warnings": [
        {"section": "sections/sec_19.dat"}]})
    assert "LEFIND" in format_log_entry(r)


def test_missing_avl_log_does_not_break_the_conclusion():
    assert "LEFIND" not in build_conclusion(make_results(avl_log=None), None)


# ---------------------------------------------------------------------------
# running_log.md and previous-run lookup
# ---------------------------------------------------------------------------


def test_running_log_accumulates_entries(tmp_path):
    append_running_log(format_log_entry(make_results(iteration=4)), tmp_path)
    append_running_log(format_log_entry(make_results(iteration=5)), tmp_path)
    text = (tmp_path / "running_log.md").read_text(encoding="utf-8")
    assert text.count("**Concept P1 —") == 2
    assert "Iteration 4" in text and "Iteration 5" in text
    assert text.index("Iteration 4") < text.index("Iteration 5")   # newest last


def test_find_previous_picks_the_latest_by_recorded_timestamp(tmp_path):
    for name, stamp in [("a", "2026-08-27T10:00:00"), ("b", "2026-08-27T12:00:00")]:
        d = tmp_path / "outputs" / name / "run"
        d.mkdir(parents=True)
        (d / "results.json").write_text(json.dumps(make_results(name=name, timestamp=stamp)))
    assert find_previous_results(tmp_path)["name"] == "b"


def test_find_previous_excludes_the_current_run(tmp_path):
    d = tmp_path / "outputs" / "a" / "run"
    d.mkdir(parents=True)
    (d / "results.json").write_text(json.dumps(make_results(name="a")))
    assert find_previous_results(tmp_path, d) is None


def test_find_previous_returns_none_when_there_are_none(tmp_path):
    assert find_previous_results(tmp_path) is None


def test_corrupt_results_json_is_skipped_not_fatal(tmp_path):
    good = tmp_path / "outputs" / "a" / "run"
    good.mkdir(parents=True)
    (good / "results.json").write_text(json.dumps(make_results(name="a")))
    bad = tmp_path / "outputs" / "b" / "run"
    bad.mkdir(parents=True)
    (bad / "results.json").write_text("{ not json")
    assert find_previous_results(tmp_path)["name"] == "a"


def test_entry_is_valid_markdown_and_ends_with_a_newline():
    text = format_log_entry(make_results())
    assert text.endswith("\n")
    assert text.count("**") == 2

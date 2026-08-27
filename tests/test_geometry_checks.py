"""Stage 4 tests — the geometry gate.

Correctness Requirement #5: these four checks must STOP the pipeline, not warn.
Each fatal case below is a wrong answer the pipeline would otherwise produce.
"""

import pytest

from pipeline.geometry_checks import (
    FAIL,
    OK,
    WARN,
    Check,
    evaluate_geometry,
    failures,
    sref_disagreement_pct,
    summarise,
)

# A geometry that passes everything, close to iteration 3's real numbers.
GOOD = dict(
    sref_projected=69.120,
    sref_chord_integral=69.500,
    tc_min=0.09,
    tc_max=0.18,
    mean_camber=0.0123,
    symmetry_residual=0.004,
    watertight=True,
    n_requested=21,
    n_extracted=21,
    ar=3.49,
)


def status_of(checks, name):
    return next(c.status for c in checks if c.name == name)


def test_good_geometry_passes_everything():
    checks = evaluate_geometry(**GOOD)
    assert failures(checks) == []
    assert all(c.status == OK for c in checks)


# -- the four fatal checks -------------------------------------------------


def test_asymmetric_geometry_is_fatal():
    checks = evaluate_geometry(**{**GOOD, "symmetry_residual": 0.05})
    assert status_of(checks, "symmetry residual") == FAIL
    assert "YDUPLICATE" in failures(checks)[0].detail


def test_symmetry_residual_at_the_limit_passes():
    assert status_of(evaluate_geometry(**{**GOOD, "symmetry_residual": 0.02}),
                     "symmetry residual") == OK


def test_sref_disagreement_over_3_percent_is_fatal():
    checks = evaluate_geometry(**{**GOOD, "sref_chord_integral": 75.0})
    assert status_of(checks, "Sref cross-check") == FAIL
    assert "CL target" in failures(checks)[0].detail


def test_sref_disagreement_under_3_percent_passes():
    checks = evaluate_geometry(**{**GOOD, "sref_chord_integral": 69.120 * 1.029})
    assert status_of(checks, "Sref cross-check") == OK


def test_thick_sections_are_fatal_and_blame_the_axes():
    checks = evaluate_geometry(**{**GOOD, "tc_max": 0.85})
    assert status_of(checks, "t/c range") == FAIL
    assert "axis" in failures(checks)[0].detail


def test_negative_camber_is_fatal():
    checks = evaluate_geometry(**{**GOOD, "mean_camber": -0.004})
    assert status_of(checks, "camber sign") == FAIL
    assert "wrong sign" in failures(checks)[0].detail


def test_several_failures_are_all_reported_not_just_the_first():
    checks = evaluate_geometry(
        **{**GOOD, "tc_max": 0.9, "mean_camber": -0.01, "symmetry_residual": 0.4}
    )
    assert {c.name for c in failures(checks)} == {
        "symmetry residual", "t/c range", "camber sign",
    }


# -- non-fatal diagnostics -------------------------------------------------


def test_non_watertight_warns_but_does_not_block():
    """stl_to_avl treats this as a warning; Requirement #5 does not list it."""
    checks = evaluate_geometry(**{**GOOD, "watertight": False})
    assert status_of(checks, "watertight") == WARN
    assert failures(checks) == []


def test_skipped_stations_warn_but_do_not_block():
    checks = evaluate_geometry(**{**GOOD, "n_extracted": 18})
    assert status_of(checks, "section extraction") == WARN
    assert "18 of 21" in next(c.detail for c in checks if c.name == "section extraction")
    assert failures(checks) == []


def test_nan_symmetry_residual_warns_rather_than_passing_silently():
    checks = evaluate_geometry(**{**GOOD, "symmetry_residual": float("nan")})
    assert status_of(checks, "symmetry residual") == WARN
    assert failures(checks) == []


def test_implausible_aspect_ratio_warns():
    assert status_of(evaluate_geometry(**{**GOOD, "ar": 120.0}), "aspect ratio") == WARN


def test_aspect_ratio_check_is_optional():
    checks = evaluate_geometry(**{**GOOD, "ar": None})
    assert not any(c.name == "aspect ratio" for c in checks)


# -- helpers ---------------------------------------------------------------


def test_sref_disagreement_maths():
    assert sref_disagreement_pct(100.0, 103.0) == pytest.approx(3.0)
    assert sref_disagreement_pct(100.0, 97.0) == pytest.approx(3.0)


def test_sref_disagreement_rejects_zero_area():
    with pytest.raises(ValueError):
        sref_disagreement_pct(0.0, 10.0)


def test_summary_reports_the_number_behind_every_verdict():
    text = summarise(evaluate_geometry(**GOOD))
    assert "69.1200" in text     # Sref
    assert "0.0040" in text      # symmetry residual
    assert "0.180" in text       # t/c max
    assert text.count("[OK") == 7


def test_check_line_is_aligned():
    assert Check("x", OK, "y").line().startswith("  [OK  ] x")

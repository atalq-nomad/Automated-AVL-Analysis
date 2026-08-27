"""Stage 7 tests — cross-iteration comparison."""

import json

import pytest

from pipeline import compare as cmp_mod
from pipeline.compare import (
    COLUMNS,
    build_rows,
    collect_results,
    compare,
    format_table,
    row_flags,
)
from pipeline.paths import update_latest_pointer
from pipeline.report import FLAG_PCT


def results(name, timestamp, **over):
    base = {
        "name": name, "timestamp": timestamp, "concept": "P1",
        "geometry": {"sref_m2": 69.12, "ar": 3.493, "s_wet_m2": 151.3},
        "aero": {"CLtot": 0.1471, "alpha_deg": 4.623, "e": 0.5710, "l_over_d": 14.46},
        "stability": {"static_margin_pct": 12.17, "pitch_stable": True},
        "range": {"range_nm": 2427.0},
        "avl_log": {"passed": True, "warnings": []},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def make_run(tmp_path, name, timestamp, with_results=True, **over):
    run_dir = tmp_path / "outputs" / name / timestamp.replace(":", "").replace("-", "")
    run_dir.mkdir(parents=True)
    if with_results:
        (run_dir / "results.json").write_text(
            json.dumps(results(name, timestamp, **over)), encoding="utf-8")
    update_latest_pointer(run_dir)
    return run_dir


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def test_collects_one_result_per_case(tmp_path):
    make_run(tmp_path, "iteration_2", "2026-08-27T13:24:00")
    make_run(tmp_path, "iteration_3", "2026-08-27T13:51:00")
    found = collect_results(tmp_path)
    assert [r["name"] for r in found] == ["iteration_2", "iteration_3"]


def test_ordered_by_recorded_timestamp_not_directory_name(tmp_path):
    make_run(tmp_path, "zzz_first", "2026-08-27T09:00:00")
    make_run(tmp_path, "aaa_second", "2026-08-27T18:00:00")
    assert [r["name"] for r in collect_results(tmp_path)] == ["zzz_first", "aaa_second"]


def test_run_without_results_json_is_skipped(tmp_path):
    """A failed gate or failed log validation never writes results.json."""
    make_run(tmp_path, "good", "2026-08-27T10:00:00")
    make_run(tmp_path, "rejected", "2026-08-27T11:00:00", with_results=False)
    assert [r["name"] for r in collect_results(tmp_path)] == ["good"]


def test_unreadable_results_json_is_skipped_not_fatal(tmp_path, capsys):
    make_run(tmp_path, "good", "2026-08-27T10:00:00")
    bad = make_run(tmp_path, "bad", "2026-08-27T11:00:00")
    (bad / "results.json").write_text("{ not json")
    assert [r["name"] for r in collect_results(tmp_path)] == ["good"]
    assert "skipping unreadable" in capsys.readouterr().err


def test_no_outputs_dir_is_empty_not_an_error(tmp_path):
    assert collect_results(tmp_path) == []


def test_latest_pointer_decides_which_run_is_compared(tmp_path):
    make_run(tmp_path, "iter", "2026-08-27T10:00:00", aero={"l_over_d": 11.0})
    newer = make_run(tmp_path, "iter", "2026-08-27T20:00:00", aero={"l_over_d": 14.46})
    update_latest_pointer(newer)
    found = collect_results(tmp_path)
    assert len(found) == 1
    assert found[0]["aero"]["l_over_d"] == 14.46


# ---------------------------------------------------------------------------
# Flagging — shared with Stage 6, not duplicated
# ---------------------------------------------------------------------------


def test_threshold_is_the_same_object_as_stage_6s():
    assert cmp_mod.FLAG_PCT is FLAG_PCT


def test_uses_report_pct_change():
    from pipeline.report import pct_change
    assert cmp_mod.pct_change is pct_change


def test_first_row_has_no_flags():
    assert row_flags(results("a", "t"), None) == []


def test_e_jump_is_flagged():
    prev = results("iter2", "t1", aero={"e": 0.4781})
    curr = results("iter3", "t2", aero={"e": 0.5710})
    assert row_flags(curr, prev) == ["e +19%"]


def test_change_under_threshold_is_not_flagged():
    prev = results("a", "t1", aero={"e": 0.5600})
    assert row_flags(results("b", "t2"), prev) == []


def test_every_column_is_trend_checked():
    prev = results("a", "t1")
    curr = results("b", "t2",
                   geometry={"sref_m2": 90.0, "ar": 5.0, "s_wet_m2": 200.0},
                   aero={"CLtot": 0.20, "alpha_deg": 6.5, "e": 0.80, "l_over_d": 20.0},
                   stability={"static_margin_pct": 20.0},
                   range={"range_nm": 3500.0})
    flagged = {f.split()[0] for f in row_flags(curr, prev)}
    assert flagged == {"Sref", "AR", "S_wet", "CL", "alpha", "e", "L/D", "SM", "range"}


def test_custom_threshold_is_honoured():
    prev = results("a", "t1", aero={"e": 0.5000})
    curr = results("b", "t2", aero={"e": 0.5500})   # +10%
    assert row_flags(curr, prev, threshold=15.0) == []
    assert row_flags(curr, prev, threshold=5.0) == ["e +10%"]


def test_missing_value_does_not_crash_the_comparison():
    prev = results("a", "t1", geometry={"ar": None})
    curr = results("b", "t2", geometry={"ar": None})
    assert row_flags(curr, prev) == []


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def test_table_has_every_required_column(tmp_path):
    make_run(tmp_path, "iteration_3", "2026-08-27T13:51:00")
    table = compare(tmp_path)
    for header in ("Iteration", "Sref m²", "AR", "S_wet m²", "CL", "alpha°",
                   "e", "L/D", "SM %MAC", "Range nm", "Flags"):
        assert header in table, header


def test_table_is_valid_markdown(tmp_path):
    make_run(tmp_path, "a", "2026-08-27T10:00:00")
    make_run(tmp_path, "b", "2026-08-27T11:00:00")
    lines = [l for l in compare(tmp_path).splitlines() if l.startswith("|")]
    assert len(lines) == 4                       # header, separator, 2 rows
    assert set(lines[1].replace(" ", "")) <= set("|-:")
    assert all(l.count("|") == lines[0].count("|") for l in lines)


def test_flag_appears_in_the_rendered_table(tmp_path):
    make_run(tmp_path, "iteration_2", "2026-08-27T13:24:00", aero={"e": 0.4781})
    make_run(tmp_path, "iteration_3", "2026-08-27T13:51:00", aero={"e": 0.5710})
    assert "e +19%" in compare(tmp_path)


def test_pitch_instability_is_called_out(tmp_path):
    make_run(tmp_path, "bad", "2026-08-27T10:00:00",
             stability={"static_margin_pct": -3.0, "pitch_stable": False})
    assert "UNSTABLE" in compare(tmp_path)


def test_lefind_count_shown_per_row(tmp_path):
    make_run(tmp_path, "iteration_2", "2026-08-27T13:24:00",
             avl_log={"passed": True, "warnings": [{"section": "sections/sec_19.dat"}]})
    assert "1×LEFIND" in compare(tmp_path)


def test_missing_value_renders_as_a_dash(tmp_path):
    make_run(tmp_path, "a", "2026-08-27T10:00:00", geometry={"ar": None})
    assert "—" in compare(tmp_path)


def test_empty_outputs_says_so_rather_than_printing_nothing(tmp_path):
    text = compare(tmp_path)
    assert "No results.json found" in text


def test_threshold_is_stated_in_the_output(tmp_path):
    make_run(tmp_path, "a", "2026-08-27T10:00:00")
    assert "more than 15%" in compare(tmp_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_a_file(tmp_path):
    make_run(tmp_path, "iteration_3", "2026-08-27T13:51:00")
    out = tmp_path / "comparison.md"
    assert cmp_mod.main(["--base-dir", str(tmp_path), "--output", str(out)]) == 0
    assert "iteration_3" in out.read_text(encoding="utf-8")


def test_cli_threshold_flag(tmp_path, capsys):
    make_run(tmp_path, "a", "2026-08-27T10:00:00", aero={"e": 0.50})
    make_run(tmp_path, "b", "2026-08-27T11:00:00", aero={"e": 0.55})
    cmp_mod.main(["--base-dir", str(tmp_path), "--threshold", "5"])
    assert "e +10%" in capsys.readouterr().out


def test_cli_on_empty_dir_exits_zero(tmp_path):
    assert cmp_mod.main(["--base-dir", str(tmp_path)]) == 0


@pytest.mark.parametrize("path,_h,_f", COLUMNS)
def test_every_column_path_resolves(path, _h, _f):
    from pipeline.compare import _dig
    assert _dig(results("a", "t"), path) is not None

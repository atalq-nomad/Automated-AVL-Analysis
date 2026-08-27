"""Stage 2 tests — run-directory scaffolding."""

from datetime import datetime

import pytest

from pipeline.paths import (
    RunPaths,
    iter_run_dirs,
    new_run_dir,
    new_run_paths,
    relative_to_run,
    resolve_latest,
    update_latest_pointer,
)

TS = datetime(2026, 8, 27, 14, 30, 0)


def test_run_dir_layout(tmp_path):
    d = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    assert d == tmp_path / "outputs" / "iteration_4" / "20260827-143000"
    assert d.is_dir()
    assert (d / "sections").is_dir()


def test_same_second_runs_never_share_a_folder(tmp_path):
    a = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    b = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    c = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    assert len({a, b, c}) == 3
    assert b.name == "20260827-143000-2"
    assert c.name == "20260827-143000-3"


def test_existing_run_is_never_overwritten(tmp_path):
    a = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    (a / "totals.txt").write_text("stale")
    b = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    assert (a / "totals.txt").read_text() == "stale"
    assert not (b / "totals.txt").exists()


def test_invalid_run_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        new_run_dir("iter/4", base_dir=tmp_path)


def test_run_paths_filenames(tmp_path):
    p = new_run_paths("iteration_4", base_dir=tmp_path, timestamp=TS)
    assert p.avl_file.name == "bwb.avl"
    assert p.run_script.name == "run.txt"
    assert p.log.name == "log.txt"
    assert p.avl_outputs == (p.totals, p.stability, p.strips)
    assert [q.name for q in p.avl_outputs] == ["totals.txt", "stability.txt", "strips.txt"]
    assert all(q.parent == p.run_dir for q in p.avl_outputs)


def test_latest_pointer_round_trips(tmp_path):
    new_run_dir("iteration_4", base_dir=tmp_path, timestamp=datetime(2026, 8, 27, 10, 0, 0))
    second = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    update_latest_pointer(second)
    assert resolve_latest("iteration_4", base_dir=tmp_path) == second.resolve()


def test_latest_falls_back_to_newest_timestamp_without_a_pointer(tmp_path):
    new_run_dir("iteration_4", base_dir=tmp_path, timestamp=datetime(2026, 8, 27, 10, 0, 0))
    newest = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    assert resolve_latest("iteration_4", base_dir=tmp_path) == newest.resolve()


def test_latest_is_none_for_an_unknown_case(tmp_path):
    assert resolve_latest("never_run", base_dir=tmp_path) is None


def test_iter_run_dirs_lists_every_case(tmp_path):
    a = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    b = new_run_dir("iteration_5", base_dir=tmp_path, timestamp=TS)
    update_latest_pointer(a)
    update_latest_pointer(b)
    found = dict(iter_run_dirs(base_dir=tmp_path))
    assert found == {"iteration_4": a.resolve(), "iteration_5": b.resolve()}


def test_relative_to_run_uses_forward_slashes(tmp_path):
    """AVL reads these paths relative to its own cwd; backslashes are a trap."""
    run = new_run_dir("iteration_4", base_dir=tmp_path, timestamp=TS)
    rel = relative_to_run(run / "sections" / "sec_00.dat", run)
    assert rel == "sections/sec_00.dat"


def test_run_paths_wraps_an_existing_dir(tmp_path):
    p = RunPaths(tmp_path)
    assert p.results == tmp_path / "results.json"
    assert p.log_entry == tmp_path / "log_entry.md"

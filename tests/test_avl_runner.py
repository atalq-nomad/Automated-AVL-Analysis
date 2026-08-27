"""Stage 3 tests.

The centrepiece is the working-directory regression suite below. It asserts on
the actual cwd AVL is launched in — both by inspecting the subprocess call and
by launching a real child process that reports the directory it woke up in —
because the bug it guards against produces a converged, clean-looking, WRONG
result rather than an error.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import avl_runner
from pipeline.avl_runner import (
    AvlRunError,
    check_afiles,
    describe_invocation,
    parse_afiles,
    run_avl,
    write_run_script,
)
from pipeline.paths import new_run_paths, project_root

ITER3 = project_root() / "avl_iter3"


# ---------------------------------------------------------------------------
# write_run_script
# ---------------------------------------------------------------------------


def test_run_script_matches_the_known_good_iteration_3_script():
    """Byte-for-byte against the run.txt that produced a trusted result."""
    produced = write_run_script(0.1471)
    on_disk = (ITER3 / "run.txt").read_text().replace("\r\n", "\n")
    assert produced.strip().splitlines() == on_disk.strip().splitlines()


def test_run_script_sequence():
    lines = write_run_script(0.1442).splitlines()
    assert lines == [
        "oper", "a", "c 0.1442", "x",
        "ft", "totals.txt",
        "st", "stability.txt",
        "fs", "strips.txt",
        "", "quit",
    ]


def test_run_script_uses_the_cl_it_is_given_not_a_cached_one():
    """Correctness Requirement #4: the CL is always the freshly computed one."""
    assert "c 0.1442" in write_run_script(0.14424)
    assert "c 0.1471" in write_run_script(0.14710)
    assert "c 0.1471" not in write_run_script(0.14424)


def test_run_script_honours_custom_output_names():
    text = write_run_script(0.144, "t.txt", "s.txt", "f.txt")
    assert "t.txt" in text and "s.txt" in text and "f.txt" in text


@pytest.mark.parametrize("cl", [0.0, -0.1, 5.0])
def test_run_script_rejects_implausible_cl(cl):
    with pytest.raises(ValueError):
        write_run_script(cl)


def test_run_script_rejects_non_numeric_cl():
    with pytest.raises(TypeError):
        write_run_script("0.144")


def test_run_script_rejects_absolute_output_paths():
    with pytest.raises(ValueError, match="relative"):
        write_run_script(0.144, out_totals=r"C:\tmp\totals.txt")


def test_run_script_rejects_overlong_filenames():
    with pytest.raises(ValueError, match="80"):
        write_run_script(0.144, out_totals="x" * 81 + ".txt")


def test_run_script_rejects_duplicate_output_names():
    with pytest.raises(ValueError, match="must differ"):
        write_run_script(0.144, "a.txt", "a.txt", "b.txt")


# ---------------------------------------------------------------------------
# AFILE preflight
# ---------------------------------------------------------------------------


def test_parses_afiles_from_the_real_iteration_3_geometry():
    names = parse_afiles(ITER3 / "bwb.avl")
    assert len(names) == 21
    assert names[0] == "sections/sec_00.dat"
    assert names[-1] == "sections/sec_20.dat"


def test_afiles_resolve_from_the_run_dir_and_only_from_there():
    """The same .avl passes from its own folder and fails from the parent."""
    check_afiles(ITER3 / "bwb.avl", ITER3)  # correct cwd: fine
    with pytest.raises(AvlRunError, match="flat-plate"):
        check_afiles(ITER3 / "bwb.avl", ITER3.parent)  # the bug's cwd: caught


def test_afile_check_names_the_missing_sections(tmp_path):
    (tmp_path / "bwb.avl").write_text("AFILE\nsections/sec_00.dat\nAFILE\nsections/sec_01.dat\n")
    with pytest.raises(AvlRunError) as exc:
        check_afiles(tmp_path / "bwb.avl", tmp_path)
    assert "sections/sec_00.dat" in str(exc.value)
    assert "2 of 2" in str(exc.value)


def test_geometry_with_no_afiles_is_rejected(tmp_path):
    (tmp_path / "bwb.avl").write_text("bwb\n0.0\nSURFACE\n")
    with pytest.raises(AvlRunError, match="flat plate"):
        check_afiles(tmp_path / "bwb.avl", tmp_path)


def test_afile_comments_are_stripped(tmp_path):
    (tmp_path / "bwb.avl").write_text("AFILE  ! airfoil\nsections/a.dat  # the section\n")
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "a.dat").write_text("x\n")
    assert check_afiles(tmp_path / "bwb.avl", tmp_path) == ["sections/a.dat"]


# ---------------------------------------------------------------------------
# Fixtures for run_avl
# ---------------------------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path):
    """A run directory laid out the way Stage 4 will produce it."""
    paths = new_run_paths("iteration_test", base_dir=tmp_path)
    paths.avl_file.write_text("bwb\n0.0\n#\nAFILE\nsections/sec_00.dat\n")
    (paths.sections_dir / "sec_00.dat").write_text("sec_00\n0.0 0.0\n1.0 0.0\n")
    return paths


@pytest.fixture
def fake_exe(tmp_path):
    """An executable stand-in for avl352.exe, living OUTSIDE the run dir."""
    exe = tmp_path / "tools" / "fake_avl.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    return exe


class SpyRun:
    """Records the exact subprocess.run call and fakes AVL's output files."""

    def __init__(self, returncode=0, write_outputs=True):
        self.returncode = returncode
        self.write_outputs = write_outputs
        self.args = None
        self.kwargs = None

    def __call__(self, args, **kwargs):
        self.args, self.kwargs = args, kwargs
        if self.write_outputs:
            for name in ("totals.txt", "stability.txt", "strips.txt"):
                (Path(kwargs["cwd"]) / name).write_text(f"{name} contents\n")
        return subprocess.CompletedProcess(args, self.returncode, stdout="AVL output\n")


# ---------------------------------------------------------------------------
# THE REGRESSION TEST — working directory
# ---------------------------------------------------------------------------


def test_cwd_is_exactly_the_directory_containing_the_avl_file(
    monkeypatch, run_dir, fake_exe
):
    """Correctness Requirement #1, asserted on the real call arguments."""
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)

    run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)

    assert spy.kwargs["cwd"] is not None, "cwd was not passed at all"
    passed = Path(spy.kwargs["cwd"]).resolve()
    assert passed == run_dir.run_dir.resolve()
    assert passed == run_dir.avl_file.parent.resolve()
    # The two directories the bug actually used, spelled out:
    assert passed != Path.cwd().resolve()
    assert passed != run_dir.run_dir.parent.resolve()


def test_avl_file_is_passed_as_a_bare_filename_not_a_relative_path(
    monkeypatch, run_dir, fake_exe
):
    """The other half of the bug: `avl352.exe avl/bwb.avl` from a parent dir."""
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)

    run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)

    avl_arg = spy.args[1]
    assert avl_arg == "bwb.avl"
    assert "/" not in avl_arg and "\\" not in avl_arg
    assert Path(avl_arg).parent == Path(".")


def test_executable_is_resolved_absolutely_before_cwd_changes(
    monkeypatch, run_dir, fake_exe, tmp_path
):
    """A relative exe path must not be re-resolved against the run dir."""
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)
    monkeypatch.chdir(tmp_path)

    run_avl(Path("tools/fake_avl.exe"), run_dir.avl_file,
            write_run_script(0.1442), run_dir.run_dir)

    exe_arg = Path(spy.args[0])
    assert exe_arg.is_absolute()
    assert exe_arg == fake_exe.resolve()
    assert exe_arg.parent != run_dir.run_dir


@pytest.mark.skipif(sys.platform != "win32", reason="uses a Windows .cmd stub")
def test_real_child_process_actually_starts_in_the_run_dir(run_dir, tmp_path):
    """End-to-end: launch a real process and ask it where it woke up.

    No mocking — this is the strongest available form of the assertion short
    of running avl352.exe itself.
    """
    stub = tmp_path / "tools" / "fake_avl.cmd"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "@echo off\r\n"
        "cd > cwd_seen.txt\r\n"
        "echo totals> totals.txt\r\n"
        "echo stability> stability.txt\r\n"
        "echo strips> strips.txt\r\n"
    )

    result = run_avl(stub, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)

    seen = Path((run_dir.run_dir / "cwd_seen.txt").read_text().strip())
    assert seen.resolve() == run_dir.run_dir.resolve()
    assert result.returncode == 0
    assert result.totals.is_file()
    assert result.log_path.is_file()


def test_refuses_an_avl_file_outside_the_output_dir(run_dir, fake_exe, tmp_path, monkeypatch):
    """Catches the misconfiguration before a process is ever launched."""
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)

    stray = tmp_path / "bwb.avl"
    stray.write_text("AFILE\nsections/sec_00.dat\n")

    with pytest.raises(AvlRunError, match="must live directly in output_dir"):
        run_avl(fake_exe, stray, write_run_script(0.1442), run_dir.run_dir)
    assert spy.args is None, "AVL was launched despite the misconfiguration"


def test_missing_sections_abort_before_launch(run_dir, fake_exe, monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)
    (run_dir.sections_dir / "sec_00.dat").unlink()

    with pytest.raises(AvlRunError, match="flat-plate"):
        run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)
    assert spy.args is None, "AVL was launched with missing airfoil files"


# ---------------------------------------------------------------------------
# run_avl behaviour
# ---------------------------------------------------------------------------


def test_run_script_is_written_into_the_run_dir_and_piped_to_stdin(
    monkeypatch, run_dir, fake_exe
):
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)
    script = write_run_script(0.1442)

    result = run_avl(fake_exe, run_dir.avl_file, script, run_dir.run_dir)

    assert spy.kwargs["input"] == script
    assert result.run_script_path == run_dir.run_script
    assert run_dir.run_script.read_text() == script


def test_stdout_and_stderr_are_captured_to_log_txt(monkeypatch, run_dir, fake_exe):
    spy = SpyRun()
    monkeypatch.setattr(avl_runner.subprocess, "run", spy)

    result = run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)

    assert spy.kwargs["stderr"] is subprocess.STDOUT
    assert result.log_path == run_dir.log
    assert run_dir.log.read_text() == "AVL output\n"


def test_nonzero_exit_raises_with_the_log_path(monkeypatch, run_dir, fake_exe):
    monkeypatch.setattr(avl_runner.subprocess, "run", SpyRun(returncode=3))
    with pytest.raises(AvlRunError, match="exited with code 3"):
        run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)
    assert run_dir.log.is_file(), "log must survive a failed run for diagnosis"


def test_missing_output_files_raise_even_on_a_clean_exit(monkeypatch, run_dir, fake_exe):
    monkeypatch.setattr(avl_runner.subprocess, "run", SpyRun(write_outputs=False))
    with pytest.raises(AvlRunError, match="did not produce usable output"):
        run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)


def test_empty_output_files_are_treated_as_failure(monkeypatch, run_dir, fake_exe):
    class EmptyOutputs(SpyRun):
        def __call__(self, args, **kwargs):
            for name in ("totals.txt", "stability.txt", "strips.txt"):
                (Path(kwargs["cwd"]) / name).write_text("")
            return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(avl_runner.subprocess, "run", EmptyOutputs())
    with pytest.raises(AvlRunError, match="empty"):
        run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)


def test_stale_outputs_are_cleared_so_prompts_stay_in_sync(monkeypatch, run_dir, fake_exe):
    """A leftover totals.txt makes AVL ask to overwrite, desyncing every keystroke."""
    run_dir.totals.write_text("stale numbers from a previous run\n")
    monkeypatch.setattr(avl_runner.subprocess, "run", SpyRun())

    result = run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442), run_dir.run_dir)

    assert "stale" not in result.totals.read_text()


def test_missing_executable_is_reported_clearly(run_dir, tmp_path):
    with pytest.raises(AvlRunError, match="executable not found"):
        run_avl(tmp_path / "nope.exe", run_dir.avl_file,
                write_run_script(0.1442), run_dir.run_dir)


def test_timeout_is_reported_and_partial_log_kept(monkeypatch, run_dir, fake_exe):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 1.0, output="partial output\n")

    monkeypatch.setattr(avl_runner.subprocess, "run", timeout)
    with pytest.raises(AvlRunError, match="timed out"):
        run_avl(fake_exe, run_dir.avl_file, write_run_script(0.1442),
                run_dir.run_dir, timeout=1.0)
    assert run_dir.log.read_text() == "partial output\n"


def test_describe_invocation_shows_the_cwd(run_dir, fake_exe):
    text = describe_invocation(fake_exe, run_dir.avl_file, run_dir.run_dir)
    assert f"cwd={str(run_dir.run_dir.resolve())!r}" in text
    assert repr(str(fake_exe.resolve())) in text
    assert "'bwb.avl'" in text

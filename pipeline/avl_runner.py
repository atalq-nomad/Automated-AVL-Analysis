"""Stage 3 — AVL run-script generation and invocation.

THE ONE BUG THIS MODULE EXISTS TO PREVENT
-----------------------------------------
AVL resolves the airfoil paths inside a .avl file (e.g. "sections/sec_00.dat")
against its OWN working directory at process launch — NOT against the directory
holding the .avl file. Launch it from the wrong directory and it does not
error out: it prints "File OPEN error" for each section, silently substitutes a
flat-plate zero-camber airfoil, and still returns a fully converged, clean
looking totals.txt. The numbers are wrong and nothing says so.

Two independent defences here:

  1. run_avl() always passes cwd=output_dir to subprocess.run, and passes the
     .avl file as a BARE relative filename. Never a path like "avl/bwb.avl"
     from a parent directory.
  2. check_afiles() reads the AFILE entries out of the .avl and confirms every
     one of them resolves under output_dir BEFORE the process is launched, so
     a misconfigured run fails with a Python traceback instead of producing
     plausible-looking garbage.

Stage 5's log validation is the third defence, after the fact. All three are
deliberate; do not remove one because another exists.

The OPER command sequence below is not guessed — it reproduces the run.txt
used for iterations 1-3, verified prompt-by-prompt against avl_iter3/log.txt.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# AVL reads filenames into a CHARACTER*80 buffer. Longer names are silently
# truncated, which puts us straight back in "wrong file, no error" territory.
AVL_FILENAME_MAX = 80

DEFAULT_TIMEOUT_S = 900


class AvlRunError(RuntimeError):
    """AVL could not be run, or did not produce what it was asked to."""


@dataclass(frozen=True)
class AvlRunResult:
    """Outcome of one AVL invocation."""

    completed: subprocess.CompletedProcess
    log_path: Path
    output_dir: Path
    run_script_path: Path
    totals: Path
    stability: Path
    strips: Path

    @property
    def returncode(self) -> int:
        return self.completed.returncode

    @property
    def stdout(self) -> str:
        return self.completed.stdout or ""


# ---------------------------------------------------------------------------
# Run script
# ---------------------------------------------------------------------------


def write_run_script(cl_target: float,
                     out_totals: str = "totals.txt",
                     out_stability: str = "stability.txt",
                     out_strips: str = "strips.txt") -> str:
    """Build the OPER keystroke sequence that solves at a fixed CL.

    Output filenames must be relative — AVL writes them into its own working
    directory, which run_avl() sets to the run's output directory.

    The sequence, one keystroke per line:

        oper            enter the OPER menu
        a               select the alpha constraint for editing
        c <CL>          constrain alpha by CL instead of by alpha
        x               execute the solution
        ft <file>       write total forces
        st <file>       write stability derivatives
        fs <file>       write strip forces
        <blank>         leave OPER, back to the top menu
        quit            exit cleanly
    """
    if not isinstance(cl_target, (int, float)) or isinstance(cl_target, bool):
        raise TypeError(f"cl_target must be a number, got {cl_target!r}")
    if not cl_target > 0.0:
        raise ValueError(
            f"cl_target must be > 0, got {cl_target!r}. A non-positive target means the "
            "mission calculation upstream failed — do not run AVL on it."
        )
    if cl_target > 3.0:
        raise ValueError(
            f"cl_target = {cl_target:.4f} is implausible for cruise. Sref is probably wrong "
            "(unit scaling?) — check geometry_summary.json before running AVL."
        )

    names = [_check_output_name(n, label) for n, label in (
        (out_totals, "out_totals"),
        (out_stability, "out_stability"),
        (out_strips, "out_strips"),
    )]
    if len(set(names)) != 3:
        raise ValueError(f"the three AVL output filenames must differ, got {names}")

    totals, stability, strips = names

    # 4 decimals matches the convention used for iterations 1-3, so a rerun of
    # a past geometry reproduces its run.txt exactly. The resolution is 1e-4 in
    # CL, which is far below anything that moves L/D.
    lines = [
        "oper",
        "a",
        f"c {cl_target:.4f}",
        "x",
        "ft",
        totals,
        "st",
        stability,
        "fs",
        strips,
        "",       # blank line leaves OPER
        "quit",
    ]
    return "\n".join(lines) + "\n"


def _check_output_name(name, label: str) -> str:
    text = str(name).strip().replace("\\", "/")
    if not text:
        raise ValueError(f"{label} must not be empty")
    if Path(text).is_absolute() or (len(text) > 1 and text[1] == ":"):
        raise ValueError(
            f"{label} must be relative to the run directory, got {text!r}. AVL writes "
            "output relative to its own working directory."
        )
    if ".." in Path(text).parts:
        raise ValueError(f"{label} must not escape the run directory, got {text!r}")
    if len(text) > AVL_FILENAME_MAX:
        raise ValueError(
            f"{label} is {len(text)} characters; AVL truncates filenames at "
            f"{AVL_FILENAME_MAX} and would write somewhere unexpected: {text!r}"
        )
    return text


# ---------------------------------------------------------------------------
# Pre-launch airfoil path check
# ---------------------------------------------------------------------------


def parse_afiles(avl_file_path) -> list[str]:
    """Return the airfoil filenames referenced by AFILE cards, in file order."""
    text = Path(avl_file_path).read_text(encoding="utf-8", errors="replace")
    names, expecting = [], False
    for raw in text.splitlines():
        line = raw.split("!")[0].split("#")[0].strip()
        if not line:
            continue
        if expecting:
            names.append(line.replace("\\", "/"))
            expecting = False
        elif line.upper().split()[0] == "AFILE":
            expecting = True
    if expecting:
        raise AvlRunError(f"{avl_file_path}: trailing AFILE card with no filename after it")
    return names


def check_afiles(avl_file_path, cwd) -> list[str]:
    """Confirm every AFILE path resolves relative to `cwd`, as AVL will read it.

    This is the pre-launch guard against the flat-plate substitution bug. AVL
    would not complain; it would substitute a zero-camber section and converge.
    """
    cwd = Path(cwd).resolve()
    names = parse_afiles(avl_file_path)
    if not names:
        raise AvlRunError(
            f"{avl_file_path} references no AFILE sections. AVL would run it as a "
            "flat plate — that is not the geometry you meant to analyse."
        )

    missing = [n for n in names if not (cwd / n).is_file()]
    if missing:
        shown = "\n  ".join(missing[:10])
        more = f"\n  ...and {len(missing) - 10} more" if len(missing) > 10 else ""
        raise AvlRunError(
            f"{len(missing)} of {len(names)} airfoil section file(s) referenced by "
            f"{Path(avl_file_path).name} do not exist relative to the working directory "
            f"AVL will be launched from:\n\n  cwd = {cwd}\n\nMissing:\n  {shown}{more}\n\n"
            "AVL would NOT report this as an error — it substitutes a flat-plate "
            "zero-camber section and returns a converged but wrong result."
        )
    return names


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def run_avl(avl_exe_path, avl_file_path, run_script_text: str, output_dir,
            timeout: float = DEFAULT_TIMEOUT_S) -> AvlRunResult:
    """Run AVL with its working directory set to `output_dir`.

    `output_dir` must be the directory that contains both the .avl file and its
    sections/ folder. That is the whole point of this function.
    """
    output_dir = Path(output_dir).resolve()
    if not output_dir.is_dir():
        raise AvlRunError(f"output_dir does not exist: {output_dir}")

    # Resolve the executable FIRST, against the CURRENT working directory.
    # Once cwd is handed to subprocess, a relative exe path is ambiguous at
    # best and resolved against the wrong directory at worst.
    exe = Path(avl_exe_path).expanduser()
    exe = exe.resolve() if exe.is_absolute() else (Path.cwd() / exe).resolve()
    if not exe.is_file():
        raise AvlRunError(f"AVL executable not found: {exe}")

    avl_file = Path(avl_file_path).resolve()
    if not avl_file.is_file():
        raise AvlRunError(f"AVL input file not found: {avl_file}")
    if avl_file.parent != output_dir:
        raise AvlRunError(
            f"the .avl file must live directly in output_dir, because AVL resolves its "
            f"airfoil paths against its working directory.\n"
            f"  .avl file  : {avl_file}\n"
            f"  output_dir : {output_dir}\n"
            "Write bwb.avl and sections/ into the run directory rather than pointing at "
            "them from elsewhere."
        )

    # Bare filename, never a path with a directory component: with cwd set to
    # output_dir, "bwb.avl" is unambiguous and "avl/bwb.avl" is the bug.
    avl_arg = avl_file.name
    check_afiles(avl_file, output_dir)

    run_script_path = output_dir / "run.txt"
    run_script_path.write_text(run_script_text, encoding="utf-8", newline="\n")

    totals, stability, strips = (output_dir / n for n in
                                 ("totals.txt", "stability.txt", "strips.txt"))
    # AVL prompts "overwrite?" if an output file already exists, which would
    # desynchronise every keystroke after it. Run dirs are fresh per run, so
    # this only fires on a manual rerun in place.
    for stale in (totals, stability, strips):
        if stale.exists():
            stale.unlink()

    log_path = output_dir / "log.txt"
    try:
        completed = subprocess.run(
            [str(exe), avl_arg],
            input=run_script_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(output_dir),          # <-- Correctness Requirement #1
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(exc.output or "", encoding="utf-8", errors="replace")
        raise AvlRunError(
            f"AVL timed out after {timeout:.0f} s (cwd={output_dir}). Partial output in "
            f"{log_path}. A hang usually means a keystroke in run.txt did not match the "
            "prompt AVL was showing, leaving it waiting on stdin."
        ) from exc
    except OSError as exc:
        raise AvlRunError(f"could not launch {exe} (cwd={output_dir}): {exc}") from exc

    log_path.write_text(completed.stdout or "", encoding="utf-8", errors="replace")

    if completed.returncode != 0:
        raise AvlRunError(
            f"AVL exited with code {completed.returncode} (cwd={output_dir}).\n"
            f"Full output: {log_path}\n\nLast lines:\n{_tail(completed.stdout)}"
        )

    missing = [p.name for p in (totals, stability, strips) if not p.is_file()]
    empty = [p.name for p in (totals, stability, strips) if p.is_file() and p.stat().st_size == 0]
    if missing or empty:
        raise AvlRunError(
            f"AVL exited 0 but did not produce usable output in {output_dir}.\n"
            + (f"  missing: {', '.join(missing)}\n" if missing else "")
            + (f"  empty:   {', '.join(empty)}\n" if empty else "")
            + f"Full output: {log_path}\n\nLast lines:\n{_tail(completed.stdout)}"
        )

    return AvlRunResult(
        completed=completed,
        log_path=log_path,
        output_dir=output_dir,
        run_script_path=run_script_path,
        totals=totals,
        stability=stability,
        strips=strips,
    )


def _tail(text: str | None, n: int = 25) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:]) if lines else "(no output captured)"


def describe_invocation(avl_exe_path, avl_file_path, output_dir) -> str:
    """Human-readable preview of the exact subprocess call, for logging."""
    exe = Path(avl_exe_path)
    exe = exe.resolve() if exe.is_absolute() else (Path.cwd() / exe).resolve()
    return (
        f"subprocess.run([{str(exe)!r}, {Path(avl_file_path).name!r}], "
        f"input=<run.txt>, cwd={str(Path(output_dir).resolve())!r}, "
        "stdout=PIPE, stderr=STDOUT, text=True)"
    )


__all__ = [
    "AVL_FILENAME_MAX",
    "AvlRunError",
    "AvlRunResult",
    "check_afiles",
    "describe_invocation",
    "parse_afiles",
    "run_avl",
    "write_run_script",
]

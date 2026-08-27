"""Stage 2 — output scaffolding.

Every run gets its own outputs/<name>/<timestamp>/ folder. A previous run's
folder is never overwritten: a same-second collision gets a -2, -3 suffix
rather than reusing the directory, so a stale totals.txt or run.txt can never
be mistaken for the current one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TIMESTAMP_FMT = "%Y%m%d-%H%M%S"
LATEST = "latest"
LATEST_POINTER = "latest.txt"

# Canonical filenames inside a run directory. Later stages read these off
# RunPaths rather than repeating string literals.
AVL_STEM = "bwb"


def project_root() -> Path:
    """Project root: the parent of this package."""
    return Path(__file__).resolve().parent.parent


def outputs_root(base_dir=None) -> Path:
    base = Path(base_dir) if base_dir is not None else project_root()
    return base / "outputs"


@dataclass(frozen=True)
class RunPaths:
    """Every file a single run reads or writes, in one place."""

    run_dir: Path

    @property
    def sections_dir(self) -> Path:
        return self.run_dir / "sections"

    @property
    def avl_file(self) -> Path:
        return self.run_dir / f"{AVL_STEM}.avl"

    @property
    def run_script(self) -> Path:
        return self.run_dir / "run.txt"

    @property
    def log(self) -> Path:
        return self.run_dir / "log.txt"

    @property
    def totals(self) -> Path:
        return self.run_dir / "totals.txt"

    @property
    def stability(self) -> Path:
        return self.run_dir / "stability.txt"

    @property
    def strips(self) -> Path:
        return self.run_dir / "strips.txt"

    @property
    def geometry_summary(self) -> Path:
        return self.run_dir / "geometry_summary.json"

    @property
    def results(self) -> Path:
        return self.run_dir / "results.json"

    @property
    def log_entry(self) -> Path:
        return self.run_dir / "log_entry.md"

    @property
    def avl_outputs(self) -> tuple[Path, Path, Path]:
        """The three files the AVL run script is expected to produce."""
        return (self.totals, self.stability, self.strips)


def new_run_dir(name: str, base_dir=None, timestamp: datetime | None = None) -> Path:
    """Create and return outputs/<name>/<timestamp>/. Never reuses a folder."""
    if not name or any(ch in name for ch in '\\/:*?"<>|'):
        raise ValueError(f"invalid run name {name!r}: it is used as a directory name")

    parent = outputs_root(base_dir) / name
    parent.mkdir(parents=True, exist_ok=True)

    stamp = (timestamp or datetime.now()).strftime(TIMESTAMP_FMT)
    candidate = parent / stamp
    n = 2
    while candidate.exists():
        candidate = parent / f"{stamp}-{n}"
        n += 1

    candidate.mkdir()
    (candidate / "sections").mkdir()
    return candidate


def new_run_paths(name: str, base_dir=None, timestamp: datetime | None = None) -> RunPaths:
    return RunPaths(new_run_dir(name, base_dir=base_dir, timestamp=timestamp))


def update_latest_pointer(run_dir) -> Path:
    """Point outputs/<name>/latest at `run_dir`.

    Prefers a directory symlink so `outputs/*/latest/results.json` globs
    directly. Creating one on Windows needs Developer Mode or admin rights, so
    when that fails we fall back to a latest.txt holding the folder name. Use
    resolve_latest() to read it back rather than assuming either form.
    """
    run_dir = Path(run_dir).resolve()
    parent = run_dir.parent
    link = parent / LATEST
    pointer = parent / LATEST_POINTER

    try:
        if link.is_symlink() or link.exists():
            if link.is_symlink():
                link.unlink()
            else:
                raise OSError(f"{link} exists and is not a symlink")
        link.symlink_to(run_dir, target_is_directory=True)
        if pointer.exists():
            pointer.unlink()
        return link
    except (OSError, NotImplementedError):
        pointer.write_text(run_dir.name + "\n", encoding="utf-8")
        return pointer


def resolve_latest(name: str, base_dir=None) -> Path | None:
    """Most recent run directory for `name`, or None if there are none.

    Reads the symlink or latest.txt if present; otherwise falls back to the
    newest timestamped folder by name, since the timestamp format sorts
    lexicographically.
    """
    parent = outputs_root(base_dir) / name
    if not parent.is_dir():
        return None

    link = parent / LATEST
    if link.is_symlink() and link.is_dir():
        return link.resolve()

    pointer = parent / LATEST_POINTER
    if pointer.is_file():
        target = parent / pointer.read_text(encoding="utf-8").strip()
        if target.is_dir():
            return target.resolve()

    runs = sorted(
        (p for p in parent.iterdir() if p.is_dir() and p.name != LATEST),
        key=lambda p: p.name,
    )
    return runs[-1].resolve() if runs else None


def iter_run_dirs(base_dir=None):
    """Yield (name, latest_run_dir) for every case with at least one run."""
    root = outputs_root(base_dir)
    if not root.is_dir():
        return
    for case_dir in sorted(root.iterdir(), key=lambda p: p.name):
        if not case_dir.is_dir():
            continue
        latest = resolve_latest(case_dir.name, base_dir=base_dir)
        if latest is not None:
            yield case_dir.name, latest


def relative_to_run(path, run_dir) -> str:
    """Path as AVL should see it: relative to the run dir, forward slashes.

    AVL resolves the airfoil paths inside a .avl file against its own working
    directory at launch, so every path written into bwb.avl or run.txt must be
    relative to the run dir that will become that working directory.
    """
    rel = os.path.relpath(Path(path).resolve(), Path(run_dir).resolve())
    return rel.replace(os.sep, "/")

"""Stage 5 — log validation.

AVL reports several serious problems only in its stdout, and returns 0 anyway.
This module reads log.txt and decides whether the run is trustworthy.

WHERE THIS SITS RELATIVE TO check_afiles() (Stage 3)
----------------------------------------------------
This is NOT the primary defence against the flat-plate substitution bug, and it
is not redundant with Stage 3 either. The two cover different windows:

    check_afiles()   BEFORE launch. Reads the AFILE cards out of bwb.avl and
                     confirms each resolves against the cwd AVL is about to be
                     given. Through the normal run_case.py path this makes
                     Fixture A's scenario unreachable — the run stops before
                     AVL starts.

    check_avl_log()  AFTER the run. Reads what AVL actually did.

The pre-flight check cannot cover:
  * AVL invoked directly — a manual avl352.exe run, a stale run.txt, a script
    that calls run_avl() without going through run_case.py, or anyone working
    inside an output directory by hand.
  * The geometry changing between pre-flight and launch — a file deleted, a
    sync/antivirus tool moving it, a parallel run overwriting sections/.
  * Anything AVL rejects for a reason unrelated to the path resolving: a
    permission error, a locked file, a truncated or malformed .dat.

In all of those the pre-flight passes and AVL still falls back to a flat plate.
So this is a genuine backstop, not dead redundancy. Keep both.

TWO CLASSES OF FINDING, DELIBERATELY TREATED DIFFERENTLY
--------------------------------------------------------
"File OPEN error"  -> FATAL, unconditionally. Every occurrence means a section
                      silently became a flat-plate zero-camber default while
                      AVL carried on and converged. There is no benign case;
                      it has now been observed twice, both times wrong.

"LEFIND"           -> WARNING, never fatal. A LEFIND on this airframe was
                      traced by hand to a genuinely valid, unusually
                      sharp-nosed section — not a corrupt file. Hard-failing on
                      it would have thrown away a correct analysis. It is
                      something a human should look at, so it is surfaced
                      prominently and recorded, but it does not stop the run.

Correctness Requirement #3: the benign "Mass file bwb.mass open error" and
"Run case file bwb.run open error" lines appear in EVERY run, including all the
good ones, because no .mass/.run file is ever provided. They must never trigger
a failure. They are matched explicitly and recorded as dismissed, so the
suppression is visible and testable rather than an accident of string matching.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Any line mentioning an open failure, in any casing. Deliberately broad: it is
# the candidate net, and each hit is then classified as benign or fatal below.
_OPEN_ERROR = re.compile(r"open error", re.IGNORECASE)

# AVL's actual error format for a section it could not open, anchored at the
# start of the line: " File OPEN error:  sections/sec_00.dat"
_FATAL_OPEN_ERROR = re.compile(r"^\s*File OPEN error\s*:", re.IGNORECASE)

# AVL echoes the name it is ABOUT to try before reporting success or failure:
# "Trying to read file: bwb.mass  ...". The echoed name is arbitrary text and
# can itself contain the words "open error" — avl_iter2/log.txt has a mass file
# literally named "/C:File OPEN error", from stray argv leaking into the launch
# command. These echoes are never findings in their own right; the outcome line
# that follows is what counts.
_TRYING_TO_READ = re.compile(r"Trying to read file\s*:", re.IGNORECASE)

# The two always-present, always-expected lines (Fixture C). No .mass or .run
# file is ever written by this pipeline, so AVL reports both on every run.
# The filename is matched non-greedily rather than as \S+ because it can
# contain spaces (see the avl_iter2 case above).
_BENIGN_OPEN_ERROR = re.compile(
    r"^\s*(?:mass file|run case file)\s+.+?\s+open error\s*$", re.IGNORECASE
)

_LEFIND = re.compile(r"LEFIND", re.IGNORECASE)
_READING_AIRFOIL = re.compile(r"Reading airfoil from file:\s*(\S+)", re.IGNORECASE)
_FILE_OPEN_ERROR_NAME = re.compile(r"File OPEN error:\s*(\S+)", re.IGNORECASE)
_NOT_FOUND_NAME = re.compile(r"Airfoil file not found\s*:\s*(\S+)", re.IGNORECASE)

FATAL = "File OPEN error"
WARNING = "LEFIND"


class AvlLogError(RuntimeError):
    """log.txt contains a finding that invalidates the run."""


@dataclass(frozen=True)
class LogFinding:
    kind: str                 # FATAL or WARNING
    lineno: int               # 1-based, into log.txt
    line: str
    section: str | None       # airfoil file the finding belongs to, if known
    context: tuple[str, ...] = ()

    def quoted(self) -> str:
        where = f" [{self.section}]" if self.section else ""
        body = "\n".join(f"      {c}" for c in (self.line, *self.context))
        return f"    line {self.lineno}{where}:\n{body}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["context"] = list(self.context)
        return d


@dataclass
class LogReport:
    log_path: str | None = None
    errors: list[LogFinding] = field(default_factory=list)
    warnings: list[LogFinding] = field(default_factory=list)
    benign: list[LogFinding] = field(default_factory=list)
    sections_read: int = 0

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "log_path": self.log_path,
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
            "benign_ignored": len(self.benign),
            "sections_read": self.sections_read,
        }

    def failure_message(self) -> str:
        quoted = "\n\n".join(f.quoted() for f in self.errors)
        return (
            f"{len(self.errors)} fatal finding(s) in {self.log_path or 'the AVL log'}:\n\n"
            f"{quoted}\n\n"
            "Each 'File OPEN error' means AVL could not read that airfoil section and "
            "silently substituted a flat-plate zero-camber default, then converged anyway. "
            "The results in this directory are WRONG despite looking clean.\n"
            "Check that sections/ is complete and that AVL was launched with its working "
            "directory set to the folder containing bwb.avl."
        )

    def warning_message(self) -> str:
        if not self.warnings:
            return ""
        lines = [f"{len(self.warnings)} LEFIND warning(s) — not fatal, but worth a look:"]
        for f in self.warnings:
            where = f.section or "unknown section"
            lines.append(f"    line {f.lineno} [{where}]: {f.line.strip()}")
        lines.append(
            "  AVL could not locate the leading edge of these sections and continued. On a "
            "sharp-nosed section this is expected and the result is still usable; on a "
            "section that should be blunt it points at a malformed .dat. Inspect the named "
            "sections before trusting the numbers."
        )
        return "\n".join(lines)


def parse_avl_log(text: str, log_path: str | None = None) -> LogReport:
    """Classify every finding in AVL log text. Pure; does not raise."""
    report = LogReport(log_path=log_path)
    lines = text.splitlines()
    current_section: str | None = None

    for i, raw in enumerate(lines):
        line = raw.rstrip()

        m = _READING_AIRFOIL.search(line)
        if m:
            current_section = m.group(1)
            report.sections_read += 1
            continue

        if _TRYING_TO_READ.search(line):
            # An echo of a filename AVL is about to open, not an outcome.
            continue

        if _FATAL_OPEN_ERROR.match(line) or _OPEN_ERROR.search(line):
            if not _FATAL_OPEN_ERROR.match(line) and _BENIGN_OPEN_ERROR.match(line):
                # Fixture C. Expected in every run; recorded so the dismissal
                # is visible in the report rather than silently invisible.
                report.benign.append(
                    LogFinding("benign", i + 1, line.strip(), None)
                )
            else:
                named = _FILE_OPEN_ERROR_NAME.search(line)
                section = named.group(1) if named else current_section
                report.errors.append(
                    LogFinding(FATAL, i + 1, line.strip(), section,
                               _context_after(lines, i))
                )
            continue

        if _LEFIND.search(line):
            report.warnings.append(
                LogFinding(WARNING, i + 1, line.strip(), current_section)
            )

    return report


def _context_after(lines: list[str], i: int, n: int = 2) -> tuple[str, ...]:
    """The follow-on lines AVL prints after a File OPEN error, for quoting."""
    out = []
    for raw in lines[i + 1: i + 1 + n]:
        line = raw.strip()
        if not line:
            continue
        if _NOT_FOUND_NAME.search(line) or "zero-camber" in line.lower():
            out.append(line)
    return tuple(out)


def check_avl_log(log_path) -> LogReport:
    """Validate an AVL log. Raises AvlLogError on any fatal finding.

    Returns the report (including non-fatal LEFIND warnings) when it passes, so
    the caller can surface warnings without re-reading the file.
    """
    path = Path(log_path)
    if not path.is_file():
        raise AvlLogError(
            f"AVL log not found: {path}. Without it there is no way to tell whether "
            "AVL silently substituted flat-plate sections, so the run cannot be trusted."
        )

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise AvlLogError(f"AVL log is empty: {path}. AVL produced no output at all.")

    report = parse_avl_log(text, log_path=str(path))
    if not report.passed:
        raise AvlLogError(report.failure_message())
    return report


__all__ = [
    "FATAL",
    "WARNING",
    "AvlLogError",
    "LogFinding",
    "LogReport",
    "check_avl_log",
    "parse_avl_log",
]

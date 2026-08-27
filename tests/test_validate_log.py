"""Stage 5 tests.

Fixtures A, B and C below are pasted verbatim from the build plan's Appendix —
real AVL output, not paraphrase. The real log.txt files produced by this
pipeline's own runs are also validated, so the module is tested against both
synthetic failure text and genuine passing output.
"""

import json

import pytest

from pipeline.paths import project_root
from pipeline.validate_log import (
    FATAL,
    WARNING,
    AvlLogError,
    check_avl_log,
    parse_avl_log,
)

# --- Appendix Fixture A — must hard-fail (missing airfoil files) -----------
FIXTURE_A = """\
     Reading airfoil from file: sections/sec_00.dat

 File OPEN error:  sections/sec_00.dat
 **   Airfoil file not found  : sections/sec_00.dat
 **   Using default zero-camber airfoil
"""

# --- Appendix Fixture B — must flag distinctly, must not be ignored --------
FIXTURE_B = """\
     Reading airfoil from file: sections/sec_19.dat
 ** LEFIND: Leading edge not found.  Continuing...
     Reading airfoil from file: sections/sec_20.dat
"""

# --- Appendix Fixture C — must NOT trigger any failure --------------------
FIXTURE_C = """\
 Trying to read file: bwb.mass  ...

 Mass file  bwb.mass  open error
 Internal mass defaults used

 ---------------------------------------------------------------
 Trying to read file: bwb.run  ...

 Run case file  bwb.run  open error
 Internal run case defaults used
"""


def write_log(tmp_path, text, name="log.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Fixture A — File OPEN error is fatal, unconditionally
# ---------------------------------------------------------------------------


def test_fixture_a_raises(tmp_path):
    with pytest.raises(AvlLogError) as exc:
        check_avl_log(write_log(tmp_path, FIXTURE_A))
    msg = str(exc.value)
    assert "sections/sec_00.dat" in msg
    assert "File OPEN error:  sections/sec_00.dat" in msg   # the offending line, quoted
    assert "flat-plate" in msg


def test_fixture_a_quotes_the_follow_on_lines(tmp_path):
    with pytest.raises(AvlLogError) as exc:
        check_avl_log(write_log(tmp_path, FIXTURE_A))
    msg = str(exc.value)
    assert "Airfoil file not found" in msg
    assert "Using default zero-camber airfoil" in msg


def test_fixture_a_finding_is_classified_and_attributed():
    report = parse_avl_log(FIXTURE_A)
    assert len(report.errors) == 1
    assert report.errors[0].kind == FATAL
    assert report.errors[0].section == "sections/sec_00.dat"
    assert report.errors[0].lineno == 3
    assert report.passed is False


def test_every_missing_section_is_reported_not_just_the_first():
    text = "".join(
        FIXTURE_A.replace("sec_00", f"sec_{i:02d}") for i in range(21)
    )
    report = parse_avl_log(text)
    assert len(report.errors) == 21
    assert report.errors[-1].section == "sections/sec_20.dat"


def test_file_open_error_is_fatal_even_with_no_section_context(tmp_path):
    with pytest.raises(AvlLogError):
        check_avl_log(write_log(tmp_path, " File OPEN error:  bwb.avl\n"))


# ---------------------------------------------------------------------------
# Fixture B — LEFIND warns, never blocks
# ---------------------------------------------------------------------------


def test_fixture_b_does_not_raise(tmp_path):
    """A real LEFIND traced to a valid sharp-nosed section must not kill the run."""
    report = check_avl_log(write_log(tmp_path, FIXTURE_B))
    assert report.passed is True


def test_fixture_b_is_recorded_as_a_warning_not_ignored():
    report = parse_avl_log(FIXTURE_B)
    assert len(report.warnings) == 1
    assert report.warnings[0].kind == WARNING
    assert report.errors == []


def test_fixture_b_attributes_the_warning_to_the_right_section():
    """The LEFIND belongs to sec_19, the file being read when it fired."""
    report = parse_avl_log(FIXTURE_B)
    assert report.warnings[0].section == "sections/sec_19.dat"


def test_fixture_b_warning_message_is_actionable():
    report = parse_avl_log(FIXTURE_B)
    msg = report.warning_message()
    assert "sections/sec_19.dat" in msg
    assert "sharp-nosed" in msg
    assert "not fatal" in msg


def test_multiple_lefinds_are_all_collected():
    report = parse_avl_log(FIXTURE_B * 3)
    assert len(report.warnings) == 3
    assert report.passed is True


# ---------------------------------------------------------------------------
# Fixture C — the benign lines present in every single run
# ---------------------------------------------------------------------------


def test_fixture_c_does_not_raise(tmp_path):
    """Correctness Requirement #3. These appear in all three good runs."""
    report = check_avl_log(write_log(tmp_path, FIXTURE_C))
    assert report.passed is True
    assert report.errors == []
    assert report.warnings == []


def test_fixture_c_lines_are_recorded_as_deliberately_dismissed():
    """Suppression must be explicit and visible, not an accident of matching."""
    report = parse_avl_log(FIXTURE_C)
    assert len(report.benign) == 2
    assert "Mass file" in report.benign[0].line
    assert "Run case file" in report.benign[1].line


# ---------------------------------------------------------------------------
# The fixtures combined — C must not mask A
# ---------------------------------------------------------------------------


def test_benign_lines_do_not_mask_a_real_failure(tmp_path):
    with pytest.raises(AvlLogError, match="sec_00"):
        check_avl_log(write_log(tmp_path, FIXTURE_C + FIXTURE_A))


def test_lefind_alongside_benign_lines_still_only_warns(tmp_path):
    report = check_avl_log(write_log(tmp_path, FIXTURE_C + FIXTURE_B))
    assert report.passed is True
    assert len(report.warnings) == 1
    assert len(report.benign) == 2


def test_all_three_fixtures_together(tmp_path):
    """A fails the run; B is still collected as a warning alongside it."""
    with pytest.raises(AvlLogError):
        check_avl_log(write_log(tmp_path, FIXTURE_C + FIXTURE_B + FIXTURE_A))
    report = parse_avl_log(FIXTURE_C + FIXTURE_B + FIXTURE_A)
    assert len(report.errors) == 1
    assert len(report.warnings) == 1
    assert len(report.benign) == 2


# ---------------------------------------------------------------------------
# Real logs from this pipeline's own runs
# ---------------------------------------------------------------------------


def real_logs():
    return sorted(project_root().glob("outputs/*/*/log.txt")) + [
        project_root() / "avl_iter3" / "log.txt",
        project_root() / "avl_iter2" / "log.txt",
    ]


@pytest.mark.parametrize("log", real_logs(), ids=lambda p: p.parent.name)
def test_real_logs_pass_and_their_benign_lines_are_seen(log):
    if not log.is_file():
        pytest.skip(f"{log} not present")
    report = check_avl_log(log)
    assert report.passed is True
    assert report.errors == []
    # Every real run contains exactly the two Fixture C lines.
    assert len(report.benign) == 2
    assert report.sections_read == 21


# ---------------------------------------------------------------------------
# Fixture E — real, from avl_iter2/log.txt
#
# Iteration 2's AVL process was launched with stray argv: an attempt to grep
# the log ("findstr /C:\"File OPEN error\"") was flattened into the argument
# list, so AVL took those strings as the .mass and .run filenames. The result
# is a log where the literal text "File OPEN error" appears as part of a
# FILENAME, in lines that are not failures at all. Both lines must stay benign,
# or the validator condemns three otherwise-good iterations.
# ---------------------------------------------------------------------------
FIXTURE_E = """\
 ---------------------------------------------------------------
 Trying to read file: /C:File OPEN error  ...

 Mass file  /C:File OPEN error  open error
 Internal mass defaults used

 ---------------------------------------------------------------
 Trying to read file: findstr  ...

 Run case file  findstr  open error
 Internal run case defaults used
"""


def test_fixture_e_stray_argv_does_not_raise(tmp_path):
    report = check_avl_log(write_log(tmp_path, FIXTURE_E))
    assert report.passed is True
    assert report.errors == []


def test_fixture_e_lines_classified_benign_not_fatal():
    report = parse_avl_log(FIXTURE_E)
    assert len(report.benign) == 2
    assert "/C:File OPEN error" in report.benign[0].line


def test_fixture_e_filename_echo_is_not_a_finding():
    """'Trying to read file:' echoes an arbitrary name; never a finding itself."""
    report = parse_avl_log(" Trying to read file: /C:File OPEN error  ...\n")
    assert report.errors == [] and report.warnings == [] and report.benign == []


def test_fixture_e_does_not_weaken_fixture_a(tmp_path):
    """The stray-argv tolerance must not let a real section failure through."""
    with pytest.raises(AvlLogError, match="sec_00"):
        check_avl_log(write_log(tmp_path, FIXTURE_E + FIXTURE_A))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_log_is_fatal(tmp_path):
    with pytest.raises(AvlLogError, match="not found"):
        check_avl_log(tmp_path / "nope.txt")


def test_empty_log_is_fatal(tmp_path):
    with pytest.raises(AvlLogError, match="empty"):
        check_avl_log(write_log(tmp_path, "   \n\n"))


def test_case_insensitive_open_error_still_caught(tmp_path):
    """Broad candidate net, then explicit classification."""
    with pytest.raises(AvlLogError):
        check_avl_log(write_log(tmp_path, " file open ERROR:  sections/sec_03.dat\n"))


def test_benign_matcher_is_anchored_not_a_loose_substring(tmp_path):
    """A line merely mentioning bwb.mass must not inherit the exemption."""
    text = " File OPEN error:  bwb.mass.dat extra text open error\n"
    with pytest.raises(AvlLogError):
        check_avl_log(write_log(tmp_path, text))


def test_report_is_json_serialisable():
    json.dumps(parse_avl_log(FIXTURE_C + FIXTURE_B).to_dict())


def test_report_dict_shape():
    d = parse_avl_log(FIXTURE_C + FIXTURE_B).to_dict()
    assert d["passed"] is True
    assert d["benign_ignored"] == 2
    assert len(d["warnings"]) == 1
    assert d["warnings"][0]["section"] == "sections/sec_19.dat"

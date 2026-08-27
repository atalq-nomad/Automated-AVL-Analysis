"""Stage 4 — the geometry gate.

Correctness Requirement #5: stl_to_avl.py's sanity checks must STOP the
pipeline before AVL is ever invoked, not print a warning that scrolls past.

The requirement names four checks specifically — symmetry residual, Sref
cross-check, t/c range, camber sign — and those four are FATAL here. Watertight
status and skipped stations are reported loudly but do not block, matching how
stl_to_avl.py itself treats them: trimesh's is_watertight is strict enough that
a mesh can fail it and still section cleanly.

These are pure functions over numbers so the gate can be tested without a mesh.
"""

from __future__ import annotations

from dataclasses import dataclass

# Thresholds. The first three mirror stl_to_avl.py's own limits so the two
# never disagree about what counts as bad geometry.
SYMMETRY_RESIDUAL_MAX = 0.02      # stl_to_avl warns above this
TC_MAX = 0.60                     # stl_to_avl aborts above this
SREF_DISAGREEMENT_MAX_PCT = 3.0   # build plan, Stage 4 item 3

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass(frozen=True)
class Check:
    """One geometry diagnostic, with the number that produced the verdict."""

    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def line(self) -> str:
        return f"  [{self.status:4s}] {self.name:22s} {self.detail}"


def sref_disagreement_pct(sref_projected: float, sref_chord_integral: float) -> float:
    """Percent disagreement between the two independent Sref estimates."""
    if sref_projected <= 0.0:
        raise ValueError(f"sref_projected must be > 0, got {sref_projected!r}")
    return abs(sref_projected - sref_chord_integral) / sref_projected * 100.0


def evaluate_geometry(*, sref_projected: float, sref_chord_integral: float,
                      tc_min: float, tc_max: float, mean_camber: float,
                      symmetry_residual: float, watertight: bool,
                      n_requested: int, n_extracted: int,
                      ar: float | None = None) -> list[Check]:
    """Run every gate check and return the verdicts, in report order."""
    checks: list[Check] = []

    # -- FATAL: symmetry residual ------------------------------------------
    if symmetry_residual != symmetry_residual:  # NaN
        checks.append(Check(
            "symmetry residual", WARN,
            "could not be computed (too few valid section pairs) — verify the mesh by eye",
        ))
    elif symmetry_residual > SYMMETRY_RESIDUAL_MAX:
        checks.append(Check(
            "symmetry residual", FAIL,
            f"{symmetry_residual:.4f} of mean chord > {SYMMETRY_RESIDUAL_MAX:.2f}. The "
            "geometry is genuinely asymmetric, not just off-centre. AVL's YDUPLICATE "
            "mirrors about y=0 and will not represent this model — fix the CAD.",
        ))
    else:
        checks.append(Check(
            "symmetry residual", OK,
            f"{symmetry_residual:.4f} of mean chord (limit {SYMMETRY_RESIDUAL_MAX:.2f})",
        ))

    # -- FATAL: Sref cross-check -------------------------------------------
    disagreement = sref_disagreement_pct(sref_projected, sref_chord_integral)
    detail = (f"projected facets {sref_projected:.4f} m2 vs chord integral "
              f"{sref_chord_integral:.4f} m2 = {disagreement:.2f}% apart")
    if disagreement > SREF_DISAGREEMENT_MAX_PCT:
        checks.append(Check(
            "Sref cross-check", FAIL,
            f"{detail}, over the {SREF_DISAGREEMENT_MAX_PCT:.0f}% limit. The extracted "
            "sections are not capturing the planform, so Sref — and therefore the CL "
            "target and every coefficient AVL returns — would be wrong.",
        ))
    else:
        checks.append(Check("Sref cross-check", OK,
                            f"{detail} (limit {SREF_DISAGREEMENT_MAX_PCT:.0f}%)"))

    # -- FATAL: t/c range ---------------------------------------------------
    if tc_max > TC_MAX:
        checks.append(Check(
            "t/c range", FAIL,
            f"max t/c = {tc_max:.3f} exceeds {TC_MAX:.2f}. No aerofoil section is that "
            "thick — the axis convention is almost certainly wrong. Check --axes.",
        ))
    else:
        checks.append(Check("t/c range", OK,
                            f"{tc_min:.3f} to {tc_max:.3f} (limit {TC_MAX:.2f})"))

    # -- FATAL: camber sign -------------------------------------------------
    if mean_camber < 0.0:
        checks.append(Check(
            "camber sign", FAIL,
            f"mean camber = {mean_camber:+.5f} is negative. The up axis is inverted, so "
            "every lift and pitching moment AVL returns would have the wrong sign. "
            "Re-run with a minus on the third axis.",
        ))
    else:
        checks.append(Check("camber sign", OK, f"mean camber = {mean_camber:+.5f}"))

    # -- Non-fatal diagnostics ---------------------------------------------
    checks.append(
        Check("watertight", OK, "mesh is closed")
        if watertight else
        Check("watertight", WARN,
              "mesh is NOT watertight — sections may be broken loops. Not blocking, but "
              "if the Sref cross-check is also marginal, suspect the mesh.")
    )

    if n_extracted < n_requested:
        checks.append(Check(
            "section extraction", WARN,
            f"only {n_extracted} of {n_requested} stations produced a section; the rest "
            "had no intersection with the mesh",
        ))
    else:
        checks.append(Check("section extraction", OK,
                            f"{n_extracted} of {n_requested} stations"))

    if ar is not None:
        checks.append(
            Check("aspect ratio", OK, f"AR = {ar:.3f}")
            if 0.5 < ar < 30.0 else
            Check("aspect ratio", WARN,
                  f"AR = {ar:.3f} is outside the plausible range for this airframe")
        )

    return checks


def failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.failed]


def summarise(checks: list[Check]) -> str:
    return "\n".join(c.line() for c in checks)

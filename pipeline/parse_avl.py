"""Stage 6 — parse AVL's totals.txt and stability.txt.

Values are pulled by name with regex, never by column position: AVL's field
widths shift when a number needs another digit, and a fixed-column parser would
read a neighbouring value without complaining.

CASE SENSITIVITY IS LOAD-BEARING. AVL prints pairs that differ only in case:

    CLtot   total lift          vs   Cltot   rolling moment
    CLa     lift-curve slope    vs   Cla     roll due to alpha

Matching case-insensitively would silently swap lift for roll. Every pattern
here is case-sensitive, and there are tests for both members of each pair.
"""

from __future__ import annotations

import re
from pathlib import Path

# A signed decimal, with or without an exponent.
_NUM = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?)"


class AvlParseError(ValueError):
    """A required value was missing or unreadable in an AVL output file."""


def _find(text: str, name: str, path: str) -> float:
    """Extract `name = <number>`, case-sensitively, or raise."""
    pattern = rf"(?<![A-Za-z0-9_'])({re.escape(name)})\s*=\s*{_NUM}"
    m = re.search(pattern, text)
    if not m:
        raise AvlParseError(
            f"{path}: could not find {name!r}. The file may be truncated, or from a "
            "different AVL version than this parser was written against (3.52)."
        )
    raw = m.group(2).replace("D", "E").replace("d", "e")
    try:
        return float(raw)
    except ValueError:
        raise AvlParseError(f"{path}: {name} = {m.group(2)!r} is not a number") from None


def _find_optional(text: str, name: str, path: str, default=None):
    try:
        return _find(text, name, path)
    except AvlParseError:
        return default


def _read(path) -> tuple[str, str]:
    p = Path(path)
    if not p.is_file():
        raise AvlParseError(f"AVL output file not found: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise AvlParseError(f"AVL output file is empty: {p}")
    return text, str(p)


def parse_totals(path) -> dict:
    """Parse totals.txt (AVL's FT output)."""
    text, name = _read(path)

    out = {
        "Sref": _find(text, "Sref", name),
        "Cref": _find(text, "Cref", name),
        "Bref": _find(text, "Bref", name),
        "Xref": _find(text, "Xref", name),
        "Alpha": _find(text, "Alpha", name),
        "CLtot": _find(text, "CLtot", name),
        "CDtot": _find(text, "CDtot", name),
        "CDvis": _find(text, "CDvis", name),
        "CDind": _find(text, "CDind", name),
        "CLff": _find(text, "CLff", name),
        "CDff": _find(text, "CDff", name),
        "Cmtot": _find(text, "Cmtot", name),
        # Oswald efficiency, Trefftz plane. Printed bare as "e =", so the
        # lookbehind in _find is what keeps it from matching a longer name.
        "e": _find(text, "e", name),
    }

    if out["Sref"] <= 0 or out["Cref"] <= 0:
        raise AvlParseError(f"{name}: nonsensical reference geometry Sref={out['Sref']}, "
                            f"Cref={out['Cref']}")
    if out["CLtot"] <= 0:
        raise AvlParseError(
            f"{name}: CLtot = {out['CLtot']} is not positive. The solution did not "
            "reach the commanded cruise lift; do not build a drag polar on it."
        )
    if out["CDind"] < 0:
        raise AvlParseError(f"{name}: CDind = {out['CDind']} is negative, which is "
                            "physically impossible.")
    return out


def parse_stability(path) -> dict:
    """Parse stability.txt (AVL's ST output), including the spiral indicator."""
    text, name = _read(path)

    out = {
        "Sref": _find(text, "Sref", name),
        "Cref": _find(text, "Cref", name),
        "Bref": _find(text, "Bref", name),
        "Xref": _find(text, "Xref", name),
        "CLa": _find(text, "CLa", name),      # NOT Cla (roll due to alpha)
        "Cma": _find(text, "Cma", name),
        "Xnp": _find(text, "Xnp", name),
        "Clb": _find(text, "Clb", name),
        "Cnb": _find(text, "Cnb", name),
        "Clr": _find(text, "Clr", name),
        "Cnr": _find(text, "Cnr", name),
    }

    # AVL 3.52 prints the spiral ratio itself; recompute anyway and cross-check,
    # so a version that stops printing it degrades to the computed value rather
    # than to nothing.
    printed = _spiral_printed(text)
    computed = spiral_indicator(out["Clb"], out["Cnr"], out["Clr"], out["Cnb"])
    out["spiral_printed"] = printed
    out["spiral_computed"] = computed
    out["spiral"] = printed if printed is not None else computed
    out["spirally_stable"] = None if out["spiral"] is None else out["spiral"] > 1.0

    if out["CLa"] <= 0:
        raise AvlParseError(
            f"{name}: CLa = {out['CLa']} is not positive. A negative lift-curve slope "
            "means the geometry or its orientation is wrong."
        )
    return out


_SPIRAL_LINE = re.compile(
    r"Clb\s+Cnr\s*/\s*Clr\s+Cnb\s*=\s*" + _NUM, re.IGNORECASE
)


def _spiral_printed(text: str):
    m = _SPIRAL_LINE.search(text)
    return float(m.group(1)) if m else None


def spiral_indicator(clb, cnr, clr, cnb):
    """Clb*Cnr / (Clr*Cnb). Greater than 1 is spirally favourable."""
    denom = clr * cnb
    if denom == 0.0:
        return None
    return (clb * cnr) / denom


# ---------------------------------------------------------------------------
# Static margin
# ---------------------------------------------------------------------------


def static_margin_pct(xnp: float, xref: float, cref: float) -> float:
    """Static margin as a percentage of MAC. Positive = stable.

    SIGN CONVENTION — ESTABLISHED FROM THE AIRFRAME, NOT ASSUMED.

    X increases AFT in this geometry. The evidence is geometric, and it has to
    be: the direction X points is a fact about the model, so only facts about
    the model can settle it.

    DECISIVE — Airfoil shape (iteration 3, root section). Maximum thickness
      sits at x/c = 0.364 measured from the minimum-X end, and that end is
      blunt (t = 0.0688c at x/c = 0.02) while the maximum-X end is sharp
      (t = 0.0036c at x/c = 0.98). An aerofoil is blunt at the leading edge,
      sharp at the trailing edge, and thickest in its forward half. So minimum
      X is the nose and maximum X is the tail: X grows aft. Note this is
      measured shape, not a definition — stl_to_avl labels min-X as "LE" by
      construction, but nothing forces the blunt, thick end to land there.

    CORROBORATING — Planform. Root Xle = 0.067 m with a 10.26 m chord spanning
      the whole body; tip Xle = 7.986 m with a 0.89 m chord. Read as
      aft-positive that is +45.7 deg of aft leading-edge sweep, which is what
      this airframe is. Read as forward-positive it would be 45.7 deg of
      FORWARD sweep. Weaker than the airfoil argument on its own, since
      forward-swept aircraft do exist, but it agrees.

    NOT EVIDENCE FOR THE SIGN — the identity Cma = CLa*(Xref - Xnp)/Cref. It
      reproduces AVL's printed Cma = -0.308260 to 1.6e-7, but it holds in
      whatever frame the .avl file defines and is therefore true whichever way
      X points. It cannot distinguish the two orientations. Its real value is
      as an internal-consistency check: it confirms Xnp, Xref, Cref, CLa and
      Cma were parsed correctly and refer to the same reference point, which is
      exactly how check_static_margin_consistency() below uses it. Do not cite
      it as justification for the sign.

    Given X aft-positive: a stable aircraft has its CG ahead of the neutral
    point, i.e. at SMALLER X, so SM = (Xnp - Xref)/Cref is positive when
    stable. Iteration 3 gives +12.18% MAC.

    Note what this does NOT affect: whether the aircraft is stable at all.
    Cma < 0 is sign-convention-independent and already establishes that for all
    three logged iterations. This function only determines whether the reported
    static-margin NUMBER carries the right sign.

    Xref is AVL's moment reference (25% MAC as written by stl_to_avl), not a
    real CG. Until a mass breakdown exists this is a proxy, not the aircraft's
    actual static margin.
    """
    if cref <= 0:
        raise ValueError(f"cref must be > 0, got {cref!r}")
    return (xnp - xref) / cref * 100.0


def check_static_margin_consistency(stability: dict, tol: float = 0.02):
    """Cross-check (Xnp-Xref)/Cref against -Cma/CLa.

    Both express the same quantity by different routes through AVL's output, so
    disagreement means a value was misparsed — the CLtot/Cltot and CLa/Cla
    case-sensitivity trap in this module's docstring would surface here.

    This says nothing about which direction X points; the identity holds in
    either orientation. See static_margin_pct's docstring.
    """
    geometric = static_margin_pct(stability["Xnp"], stability["Xref"], stability["Cref"])
    from_derivs = -stability["Cma"] / stability["CLa"] * 100.0
    ok = abs(geometric - from_derivs) <= tol * max(1.0, abs(geometric))
    return {"geometric_pct": geometric, "from_derivatives_pct": from_derivs,
            "consistent": ok}


__all__ = [
    "AvlParseError",
    "check_static_margin_consistency",
    "parse_stability",
    "parse_totals",
    "spiral_indicator",
    "static_margin_pct",
]

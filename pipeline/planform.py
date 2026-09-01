"""Spanwise planform geometry, split into centerbody and outer wing.

Stage 11's mass model needs outer-wing-only Sref, AR, sweep, taper and t/c —
a BWB has no separable fuselage, and the centerbody does a different structural
job (it carries the pressurised cabin and the tank system; the outer wing does
not). A single tube-and-wing weight equation over the whole planform would
model neither.

Geometry comes from the AVL SECTION cards, which are real extracted geometry
rather than assumed values.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def stations_from_avl(avl_path) -> list[dict]:
    """Read (y, chord, x_le, z_le, twist) per station from a .avl file.

    The SECTION card's data line is: Xle Yle Zle Chord Ainc Nspan Sspace
    """
    lines = Path(avl_path).read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.strip().upper() != "SECTION":
            continue
        for candidate in lines[i + 1: i + 4]:
            text = candidate.split("!")[0].split("#")[0].strip()
            if not text:
                continue
            parts = text.split()
            if len(parts) >= 5:
                try:
                    x, y, z, c, ainc = (float(v) for v in parts[:5])
                except ValueError:
                    continue
                out.append({"y": y, "chord": c, "x_le": x, "z_le": z, "twist": ainc})
            break
    if not out:
        raise ValueError(f"{avl_path}: no SECTION cards found")
    return sorted(out, key=lambda s: s["y"])


def stations_from_sections(sections: list[dict]) -> list[dict]:
    """Same shape, from stl_to_avl.extract()'s in-memory sections."""
    return [{"y": float(s["y"]), "chord": float(s["chord"]), "x_le": float(s["x_le"]),
             "z_le": float(s["z_le"]), "twist": float(s["twist"]),
             "tc": float(s["tc"])}
            for s in sorted(sections, key=lambda s: s["y"])]


def steepest_gradient_break(stations: list[dict]) -> float:
    """Spanwise station where chord falls fastest — the planform's knee.

    Reported as a diagnostic so a configured centerbody_span_fraction can be
    sanity-checked against the geometry rather than trusted blindly. Not used
    to set the split itself, because an automatic breakpoint that moves between
    iterations would make their mass numbers incomparable.
    """
    y = np.array([s["y"] for s in stations])
    c = np.array([s["chord"] for s in stations])
    if len(y) < 3:
        return float(y[-1] * 0.35)
    gradient = np.diff(c) / np.diff(y)
    mid = 0.5 * (y[:-1] + y[1:])
    return float(mid[int(np.argmin(gradient))])


def split_planform(stations: list[dict], sref_m2: float,
                   centerbody_span_fraction: float = 0.35) -> dict:
    """Split the half-planform at a fraction of half-span.

    Returns areas for the FULL span (both sides), matching how Sref is defined.
    """
    if not 0.0 < centerbody_span_fraction < 1.0:
        raise ValueError(
            f"centerbody_span_fraction must be in (0, 1), got {centerbody_span_fraction!r}")

    y = np.array([s["y"] for s in stations])
    c = np.array([s["chord"] for s in stations])
    x_le = np.array([s["x_le"] for s in stations])

    b_half = float(y[-1])
    y_break = centerbody_span_fraction * b_half

    # Dense resample so the break lands exactly, not on the nearest station.
    fine = np.linspace(float(y[0]), b_half, 2001)
    c_fine = np.interp(fine, y, c)
    inner = fine <= y_break

    centerbody_area = 2.0 * float(np.trapezoid(c_fine[inner], fine[inner]))
    outer_area = 2.0 * float(np.trapezoid(c_fine[~inner], fine[~inner]))

    chord_break = float(np.interp(y_break, y, c))
    chord_tip = float(c[-1])
    x_le_break = float(np.interp(y_break, y, x_le))

    outer_semispan = b_half - y_break
    outer_span = 2.0 * outer_semispan
    outer_ar = outer_span ** 2 / outer_area if outer_area > 0 else float("nan")
    taper = chord_tip / chord_break if chord_break > 0 else float("nan")

    # Sweep of the outer panel's quarter-chord line.
    x_q_break = x_le_break + 0.25 * chord_break
    x_q_tip = float(x_le[-1]) + 0.25 * chord_tip
    sweep_deg = math.degrees(math.atan2(x_q_tip - x_q_break, outer_semispan))

    tc_values = [s["tc"] for s in stations if "tc" in s and s["y"] >= y_break]
    outer_tc = float(np.mean(tc_values)) if tc_values else None

    return {
        "b_half_m": b_half,
        "centerbody_span_fraction": centerbody_span_fraction,
        "y_break_m": y_break,
        "steepest_gradient_break_m": steepest_gradient_break(stations),
        "centerbody_area_m2": centerbody_area,
        "outer_wing_area_m2": outer_area,
        "planform_area_m2": centerbody_area + outer_area,
        "sref_m2": sref_m2,
        "outer_span_m": outer_span,
        "outer_aspect_ratio": outer_ar,
        "outer_taper_ratio": taper,
        "outer_sweep_quarter_chord_deg": sweep_deg,
        "outer_mean_tc": outer_tc,
        "chord_at_break_m": chord_break,
        "chord_tip_m": chord_tip,
    }


__all__ = ["split_planform", "stations_from_avl", "stations_from_sections",
           "steepest_gradient_break"]

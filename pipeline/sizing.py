"""Stage 12 — couple the mass model (Part B) and the mission profile (Part A).

Part C's outer loop: MTOM converges, and the reserve-based range is then flown
from that converged MTOM rather than from the assumed 7,300 kg.

WHY THIS CONVERGES IMMEDIATELY, AND WHY IT IS STILL WRITTEN AS A LOOP
---------------------------------------------------------------------
Fuel mass is fixed by TANK VOLUME, which is a geometric/packaging constraint
independent of MTOM. So the dependency runs one way:

    tank volume -> fuel mass -> tank system mass -> MTOM
    MTOM (+ fuel, L/D)        -> reserve range

Range never feeds back into MTOM, because it cannot change how much fuel fits
in the tank. The loop therefore settles on the second pass. It is still written
as a loop, and convergence is still measured and reported, because that stops
being true the moment anyone makes fuel mass depend on MTOM — a fuel-fraction
sizing rule, a mass-driven tank resize — and a silent one-shot calculation
would give a wrong answer without complaining.

WHAT DELIBERATELY DOES NOT USE THE CONVERGED MTOM
-------------------------------------------------
The AVL CL target (Stage 4) still uses the ASSUMED mtom_kg. Changing it would
silently move Fixture D's validated aero numbers. The methodology document
defers this explicitly: the better cruise weight for CL trim is the geometric
mean sqrt(Wi*Wf), and if it is ever adopted it must be an opt-in alongside the
MTOM-based target, not a silent replacement.
"""

from __future__ import annotations

from dataclasses import replace

from .mass_model import converge_mtom, pc24_crosscheck, sensitivity
from .mission import MissionConfig, compute_range
from .mission_profile import solve_max_range

MAX_SIZING_PASSES = 12
SIZING_TOL = 1e-6


def converge_sizing(mission: MissionConfig, planform: dict, q_pa: float,
                    l_over_d: float, tank_mass_override_kg: float | None = None,
                    include_sensitivity: bool = True) -> dict:
    """Run the Part C outer loop and return everything Stage 12 reports."""
    mtom = mission.mtom_kg
    profile = None
    mass = None
    passes = 0
    converged = False

    for passes in range(1, MAX_SIZING_PASSES + 1):
        mass = converge_mtom(mission, planform, q_pa,
                             tank_mass_override_kg=tank_mass_override_kg)
        flown = replace(mission, mtom_kg=mass.mtom_kg)
        profile = solve_max_range(flown, l_over_d)
        if abs(mass.mtom_kg - mtom) / mtom <= SIZING_TOL:
            mtom = mass.mtom_kg
            converged = True
            break
        mtom = mass.mtom_kg

    crosscheck = pc24_crosscheck(mission, mass, q_pa)
    sweep = sensitivity(mission, planform, q_pa) if include_sensitivity else None
    verdict = gate_verdict(mass, sweep)

    # The quick estimate at the converged MTOM, so the two range numbers are
    # compared at the same weight rather than across a 33 kg difference.
    quick_at_converged = compute_range(
        replace(mission, mtom_kg=mass.mtom_kg), l_over_d)

    return {
        "method": "part_c_outer_loop",
        "converged": converged,
        "passes": passes,
        "assumed_mtom_kg": mission.mtom_kg,
        "converged_mtom_kg": mass.mtom_kg,
        "mtom_delta_kg": mass.mtom_kg - mission.mtom_kg,
        "mass_model": mass.to_dict(),
        "pc24_crosscheck": crosscheck,
        "sensitivity": sweep,
        "gate": verdict,
        "reserve_range": profile.to_dict(),
        "quick_range_at_converged_mtom": quick_at_converged,
        "note": (
            "Reserve range is flown from the CONVERGED MTOM. The AVL CL target "
            "still uses the assumed mtom_kg — changing it would silently move "
            "Fixture D's validated aero numbers, and the methodology document "
            "defers that refinement (sqrt(Wi*Wf)) as explicit opt-in only."
        ),
    }


def gate_verdict(mass, sweep: dict | None) -> dict:
    """The MTOM cap verdict, plus whether that verdict is actually decided.

    A bare pass/fail next to a number built on unsourced inputs reads as more
    certain than it is. If the verdict flips anywhere inside the swept range of
    the two placeholder inputs, it is UNDECIDED, and the output says so in
    words a later reader cannot miss.
    """
    passed = mass.gate_passed
    margin = mass.cap_kg - mass.mtom_kg
    margin_pct = margin / mass.cap_kg * 100.0

    flips: list[str] = []
    if sweep:
        for key, label, value_key in (
            ("sigma_centerbody_kg_m2", "centerbody areal density sigma", "sigma_kg_m2"),
            ("eta_g_tank", "tank gravimetric efficiency eta_g", "eta_g"),
        ):
            verdicts = {row["gate_passed"] for row in sweep[key]}
            if len(verdicts) > 1:
                span = [row[value_key] for row in sweep[key]]
                flips.append(f"{label} (verdict changes across {min(span)}-{max(span)})")

    decided = not flips
    if decided:
        framing = (
            f"MTOM {mass.mtom_kg:.1f} kg vs cap {mass.cap_kg:.0f} kg: "
            f"{'PASS' if passed else 'FAIL'} by {abs(margin):.1f} kg "
            f"({abs(margin_pct):.2f}%). The verdict holds across the swept range "
            "of the placeholder inputs."
        )
    else:
        framing = (
            f"MTOM {mass.mtom_kg:.1f} kg vs cap {mass.cap_kg:.0f} kg is nominally "
            f"{'PASS' if passed else 'FAIL'} by {abs(margin):.1f} kg "
            f"({abs(margin_pct):.2f}%), but THE VERDICT IS UNDECIDED: it flips "
            f"within the plausible range of {', '.join(flips)}. Both are unsourced "
            "placeholders. Read this as 'MTOM lands within a few percent of the cap "
            "and which side is currently decided by numbers nobody has sourced', "
            "NOT as a settled pass or fail. Sourcing them is what settles it."
        )

    return {
        "cap_kg": mass.cap_kg,
        "converged_mtom_kg": mass.mtom_kg,
        "margin_kg": margin,
        "margin_pct": margin_pct,
        "nominal_verdict": "PASS" if passed else "FAIL",
        "gate_passed": passed,
        "verdict_decided": decided,
        "flips_on": flips,
        "framing": framing,
    }


__all__ = ["converge_sizing", "gate_verdict"]

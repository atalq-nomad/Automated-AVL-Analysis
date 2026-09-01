"""Stage 6 — assemble results.json from parsed AVL output plus the mission.

Kept separate from run_case.py so the drag build-up and range can be tested
against Fixture D without running AVL or reading an STL.

The drag model here is deliberately crude and worth stating plainly: CD0 comes
from a flat-plate equivalent-skin-friction estimate, Cfe * S_wet / Sref, with a
single Cfe held constant across iterations. It carries no form drag, no
interference, no compressibility, no excrescence. It is fit for RANKING
iterations against each other, which is what this pipeline is for. It is not an
absolute drag prediction, and the L/D it produces should not be quoted as one.
"""

from __future__ import annotations

from .mission import MissionConfig, compute_range
from .parse_avl import check_static_margin_consistency, static_margin_pct
from .sizing import converge_sizing


def wetted_area(geometry: dict, s_wet_override_m2: float | None) -> tuple[float, str]:
    """Return (S_wet, source). The Onshape override wins when present."""
    if s_wet_override_m2 is not None:
        return float(s_wet_override_m2), "case_config_override"
    mesh_value = geometry.get("s_wet_mesh_m2")
    if mesh_value is None:
        raise ValueError(
            "no wetted area available: geometry_summary.json has no 's_wet_mesh_m2' "
            "and the case config sets no s_wet_override_m2. CD0 cannot be computed."
        )
    return float(mesh_value), "mesh_area"


def drag_buildup(cfe: float, s_wet_m2: float, sref_m2: float, cd_ind: float) -> dict:
    """CD0 = Cfe * S_wet / Sref; CD_total = CD0 + CDind."""
    if sref_m2 <= 0:
        raise ValueError(f"sref_m2 must be > 0, got {sref_m2!r}")
    if s_wet_m2 <= 0:
        raise ValueError(f"s_wet_m2 must be > 0, got {s_wet_m2!r}")
    cd0 = cfe * s_wet_m2 / sref_m2
    cd_total = cd0 + cd_ind
    if cd_total <= 0:
        raise ValueError(f"CD_total = {cd_total} is not positive")
    return {"cd0": cd0, "cd_total": cd_total}


def build_results(*, case, mission: MissionConfig, totals: dict, stability: dict,
                  geometry: dict, cl_target: float, run_dir: str,
                  timestamp: str, log_report: dict | None = None,
                  q_pa: float | None = None) -> dict:
    """Everything results.json holds, computed from parsed inputs."""
    sref = totals["Sref"]
    s_wet, s_wet_source = wetted_area(geometry, case.s_wet_override_m2)

    drag = drag_buildup(mission.cfe, s_wet, sref, totals["CDind"])
    l_over_d = totals["CLtot"] / drag["cd_total"]

    # Sign convention verified empirically — see parse_avl.static_margin_pct.
    sm_pct = static_margin_pct(stability["Xnp"], stability["Xref"], stability["Cref"])
    sm_check = check_static_margin_consistency(stability)

    rng = compute_range(mission, l_over_d, cl_target=cl_target)

    # Stage 12 — MTOM closure coupled to the reserve-based mission profile.
    # Additive: every pre-Stage-12 field above is unchanged, so already-logged
    # iterations stay comparable.
    sizing = None
    planform = geometry.get("planform")
    if planform and q_pa:
        try:
            sizing = converge_sizing(
                mission, planform, q_pa, l_over_d,
                tank_mass_override_kg=getattr(case, "tank_system_mass_override_kg", None))
        except (ValueError, KeyError) as exc:
            sizing = {"error": f"{type(exc).__name__}: {exc}",
                      "note": "Stage 12 sizing did not run; aero results above are unaffected."}

    concept, iteration = case.concept_and_iteration()

    return {
        "name": case.name,
        "concept": concept,
        "iteration": iteration,
        "timestamp": timestamp,
        "run_dir": run_dir,
        "mission": {
            "cruise_altitude_m": mission.cruise_altitude_m,
            "cruise_mach": mission.cruise_mach,
            "mtom_kg": mission.mtom_kg,
            "cfe": mission.cfe,
            "tsfc_kg_per_Ns": mission.tsfc_kg_per_Ns,
            "tank_volume_m3": mission.tank_volume_m3,
            "lh2_fill_fraction": mission.lh2_fill_fraction,
            "lh2_density_kgm3": mission.lh2_density_kgm3,
        },
        "geometry": {
            "sref_m2": sref,
            "cref_m": totals["Cref"],
            "bref_m": totals["Bref"],
            "xref_m": totals["Xref"],
            "ar": geometry.get("ar"),
            "s_wet_m2": s_wet,
            "s_wet_source": s_wet_source,
            "s_wet_mesh_m2": geometry.get("s_wet_mesh_m2"),
            "s_wet_override_m2": case.s_wet_override_m2,
        },
        "aero": {
            "cl_target": cl_target,
            "CLtot": totals["CLtot"],
            "alpha_deg": totals["Alpha"],
            "CDind": totals["CDind"],
            "CDvis": totals["CDvis"],
            "CDtot_avl": totals["CDtot"],
            "CLff": totals["CLff"],
            "CDff": totals["CDff"],
            "e": totals["e"],
            "Cmtot": totals["Cmtot"],
            "cd0": drag["cd0"],
            "cd_total": drag["cd_total"],
            "l_over_d": l_over_d,
        },
        "stability": {
            "CLa": stability["CLa"],
            "Cma": stability["Cma"],
            "Xnp": stability["Xnp"],
            "static_margin_pct": sm_pct,
            "static_margin_crosscheck": sm_check,
            # Cma < 0 is sign-convention-independent; it establishes stability
            # regardless of which way X is measured.
            "pitch_stable": stability["Cma"] < 0.0,
            "spiral": stability["spiral"],
            "spirally_stable": stability["spirally_stable"],
            "Clb": stability["Clb"], "Cnb": stability["Cnb"],
            "Clr": stability["Clr"], "Cnr": stability["Cnr"],
        },
        "range": rng,
        "range_model_note": (
            "range above is the QUICK no-reserve estimate, kept for continuity with "
            "iterations logged before Stage 10. For decisions use "
            "sizing.reserve_range, which flies the full reserve profile from the "
            "converged MTOM."
        ),
        "sizing": sizing,
        "avl_log": log_report,
        "drag_model_note": (
            "CD0 = Cfe*S_wet/Sref, flat-plate equivalent skin friction only. "
            "Valid for ranking iterations; not an absolute drag prediction."
        ),
    }


__all__ = ["build_results", "drag_buildup", "wetted_area"]

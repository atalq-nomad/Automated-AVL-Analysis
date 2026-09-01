"""Stage 11 — MTOM closure by group weight buildup (Part B).

    MTOM = OEW + payload + crew + fuel(LH2)
    OEW  = structure + propulsion + systems + tank_system

EVERY NUMBER IN HERE IS A CONCEPTUAL-DESIGN ESTIMATE, AND SEVERAL ARE
UNSOURCED PLACEHOLDERS. See PLACEHOLDERS below and the README list. This is not
a structural-sizing tool; it exists to turn MTOM from an unverified assumption
into something with a model behind it that can be argued with.

WHY THE STRUCTURE GROUP IS SPLIT
--------------------------------
A BWB has no separable fuselage, so there is no fuselage length to put into a
tube-and-wing weight equation. The centerbody carries the pressurised cabin and
the tank system; the outer wing does not. They are modelled separately:
  * outer wing  -> conventional-aircraft regression (Raymer), on outer-panel-
                   only geometry from pipeline.planform
  * centerbody  -> areal density, sigma x centerbody planform area
Elevons and drag rudders are integrated into the wing/centerbody structure on
this configuration, so there is deliberately NO separate empennage group — it
would double-count.

THE CAP IS A GATE, NOT AN INPUT
-------------------------------
The converged MTOM is checked against mtom_cap_kg afterwards. A converged MTOM
above the cap is a real finding requiring redesign. Never tune the model to
bring it under — that is the standing rule for this whole programme.

PLACEHOLDERS — READ THIS BEFORE QUOTING ANY NUMBER FROM THIS MODULE
-------------------------------------------------------------------
Two different kinds of weak input are mixed in here, and they are NOT equally
defensible. The distinction matters, so it is spelled out per parameter.

(a) UNGROUNDED — invented to be dimensionally plausible. Traces to NO source,
    not even loosely. Nobody has checked these against literature.

  sigma_centerbody_kg_m2 = 60.0 kg/m2
      THIS IS AN UNGROUNDED PLACEHOLDER. It was NOT taken from Liebeck, from
      the NASA BWB sizing report, or from any other document — those were named
      in the methodology as where it SHOULD come from, and that sourcing has
      not been done. 60.0 was chosen only because it sits in a range that looks
      reasonable for a pressurised composite/metallic centerbody. There is no
      citation behind it and it should not be described as "literature-typical".
      It is also the single largest term in the buildup (~39% of MTOM), and the
      MTOM cap verdict flips within a plausible range for it (see sensitivity()).
      Sourcing this is the highest-value open action in the whole mass model.

  crew_kg = 200.0  (the COUNT, not the per-occupant mass)
      2 crew at 100 kg each. The crew COUNT is genuinely open: single-pilot
      certification is unresolved elsewhere in the programme's materials, and
      it is not this model's place to settle it. 2 is the conservative
      assumption; 1 would remove 100 kg one-for-one from MTOM, since crew does
      not scale with MTOM. Flagged as OPEN rather than ungrounded — the
      uncertainty is real and known, not an absence of information.

  NOT A PLACEHOLDER — payload_kg = 600.0
      6-passenger payload per the programme design brief, at the standard
      100 kg/occupant conceptual-design convention (roughly a 77 kg person plus
      baggage). This is sourced. It was briefly modelled as 8 occupants /
      800 kg, which contradicted the brief; corrected.

(b) LITERATURE-TYPICAL BUT NOT PROGRAMME-VALIDATED — a real published method or
    a commonly quoted range, applied outside the population it was fitted on.

  outer wing regression      Raymer Eq 15.46, fitted on conventional metal GA
                             and business aircraft, not on a BWB outer panel.
  landing_gear_fraction      3-4% of MTOM is widely quoted for this class.
  systems_fraction           lumped statistical fraction, Raymer/Torenbeek-style.
  engine_specific_weight     kerosene turbofan figure (~19 kg/kN is typical of
                             the FJ44 class); LH2-combustor engine mass data is
                             not public, so this stands in for it.
  eta_g_tank = 0.50          bracketed by real programme figures — near-term
                             metallic ~0.30 and the 55-57% economic crossover
                             target — but the 0.50 midpoint is a choice, not a
                             measurement. Highest-leverage number after sigma.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .mission import G0, MissionConfig

# Unit conversions — the Raymer regressions below are published in US units.
M2_TO_FT2 = 10.763910416709722
KG_TO_LB = 2.2046226218487757
LB_TO_KG = 1.0 / KG_TO_LB
PA_TO_PSF = 0.020885434273039

MAX_MTOM_PASSES = 200
# Iterate to closure, not merely to the reporting tolerance — see converge_mtom.
CLOSURE_TOL = 1e-12


class MassModelError(ValueError):
    """The mass model could not produce a usable answer."""


# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------


def outer_wing_mass_kg(*, mtom_kg: float, area_m2: float, aspect_ratio: float,
                       sweep_deg: float, taper_ratio: float, tc: float,
                       q_pa: float, ultimate_load_factor: float,
                       wing_fuel_kg: float = 0.0) -> float:
    """Raymer general-aviation wing weight regression (Aircraft Design, Eq 15.46).

    PLACEHOLDER STATUS: calibrated on conventional metal GA/business aircraft,
    NOT on a BWB outer panel. Applied here to outer-panel-only geometry as a
    first-order starting point, pending the BWB-specific methods already in the
    project's reference set (Hansmann & Stumpf UNICADO, NASA BWB sizing report).

        W = 0.036 Sw^0.758 Wfw^0.0035 (A/cos^2 L)^0.6 q^0.006 lambda^0.04
            (100 t/c / cos L)^-0.3 (Nz Wdg)^0.49        [lb, ft^2, psf, deg]

    The Wfw^0.0035 term is taken as 1 when there is no fuel in the wing, which
    is the case here — all LH2 is in the centerbody. Left as written it would
    drive the whole product to zero.
    """
    for name, value in (("area_m2", area_m2), ("aspect_ratio", aspect_ratio),
                        ("tc", tc), ("q_pa", q_pa), ("mtom_kg", mtom_kg)):
        if value <= 0:
            raise MassModelError(f"outer_wing_mass_kg: {name} must be > 0, got {value!r}")
    if not 0.0 < taper_ratio <= 1.0:
        raise MassModelError(f"taper_ratio must be in (0, 1], got {taper_ratio!r}")
    if abs(sweep_deg) >= 90.0:
        raise MassModelError(f"sweep_deg must be within +/-90, got {sweep_deg!r}")

    sw = area_m2 * M2_TO_FT2
    wdg = mtom_kg * KG_TO_LB
    q = q_pa * PA_TO_PSF
    cos_sweep = math.cos(math.radians(sweep_deg))

    fuel_term = (wing_fuel_kg * KG_TO_LB) ** 0.0035 if wing_fuel_kg > 0 else 1.0

    w_lb = (0.036
            * sw ** 0.758
            * fuel_term
            * (aspect_ratio / cos_sweep ** 2) ** 0.6
            * q ** 0.006
            * taper_ratio ** 0.04
            * (100.0 * tc / cos_sweep) ** -0.3
            * (ultimate_load_factor * wdg) ** 0.49)
    return w_lb * LB_TO_KG


def centerbody_mass_kg(sigma_kg_m2: float, centerbody_area_m2: float) -> float:
    """Areal-density method: mass = sigma x centerbody planform area.

    Deliberately NOT a fuselage-length equation — a BWB has no fuselage length.

    sigma IS AN UNGROUNDED PLACEHOLDER (default 60.0 kg/m2). It does not trace
    to Liebeck, to the NASA BWB sizing report, or to anything else — it was
    chosen to be dimensionally plausible for a pressurised centerbody carrying
    the cabin and tank system. It is the largest single term in the buildup and
    the MTOM cap verdict flips within a plausible range for it. Source it before
    quoting any MTOM that depends on it.
    """
    if sigma_kg_m2 <= 0 or centerbody_area_m2 <= 0:
        raise MassModelError("centerbody_mass_kg needs positive sigma and area")
    return sigma_kg_m2 * centerbody_area_m2


def landing_gear_mass_kg(fraction: float, mtom_kg: float) -> float:
    """Fraction of MTOM. 3-4% is literature-typical for this class."""
    return fraction * mtom_kg


def systems_mass_kg(fraction: float, mtom_kg: float) -> float:
    """Lumped statistical fraction of MTOM (Raymer/Torenbeek-style).

    Covers flight controls, avionics, electrical, hydraulic/pneumatic, ECS and
    furnishings. Conventional-aircraft-calibrated, first-order only.
    """
    return fraction * mtom_kg


def propulsion_mass_kg(*, mtom_kg: float, thrust_to_weight: float,
                       specific_weight_kg_per_kn: float,
                       installation_factor: float) -> float:
    """Installed propulsion from thrust sizing.

    thrust = (T/W) x MTOM x g; mass = specific weight x thrust, scaled up by an
    installation factor covering nacelles, pylons and accessories.

    The specific weight is a KEROSENE turbofan figure. LH2-combustor engine
    mass data is not public, so this is a placeholder pending real data.
    """
    thrust_kn = thrust_to_weight * mtom_kg * G0 / 1000.0
    return specific_weight_kg_per_kn * thrust_kn * installation_factor


def payload_and_crew_kg(mission) -> tuple[float, float, str]:
    """Payload and crew, with their provenance attached.

    PAYLOAD IS SOURCED: 6-passenger payload per the programme design brief, at
    the standard 100 kg/occupant conceptual-design convention. Not a placeholder.

    CREW COUNT IS OPEN: 2 crew at 100 kg each. Single-pilot certification is
    unresolved elsewhere in the programme's materials and is not settled here;
    2 is the conservative assumption. Neither payload nor crew scales with
    MTOM, so each kilogram moves the converged MTOM one-for-one.
    """
    return (mission.payload_kg, mission.crew_kg,
            "payload: 6-passenger payload per programme design brief, at "
            "100 kg/occupant. crew: 2 x 100 kg — the CREW COUNT is OPEN "
            "(1 vs 2 pilot unresolved in the programme's materials)")


def tank_system_mass_kg(fuel_kg: float, eta_g: float,
                        override_kg: float | None = None) -> tuple[float, str]:
    """LH2 tank system mass. Returns (mass, source).

        m_tank_system = m_LH2 x (1/eta_g - 1)

    eta_g is gravimetric efficiency: fuel mass / (fuel + tank system) mass. The
    default sits between near-term metallic tanks (~0.30) and the 55-57%
    economic crossover target, and is the highest-leverage single number in the
    whole model.

    A detailed number from the separate tank-packaging analysis takes
    precedence when available, exactly as s_wet_override_m2 does for wetted
    area. The eta_g proxy is the fallback, not the preferred input.
    """
    if override_kg is not None:
        if override_kg < 0:
            raise MassModelError(f"tank mass override must be >= 0, got {override_kg!r}")
        return float(override_kg), "case_config_override"
    if not 0.0 < eta_g < 1.0:
        raise MassModelError(f"eta_g_tank must be in (0, 1), got {eta_g!r}")
    return fuel_kg * (1.0 / eta_g - 1.0), "eta_g_proxy"


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------


@dataclass
class MassResult:
    converged: bool
    passes: int
    mtom_kg: float
    oew_kg: float
    groups: dict
    fuel_kg: float
    payload_kg: float
    crew_kg: float
    cap_kg: float
    tank_source: str
    history: list
    tolerance_pass: int | None = None

    @property
    def gate_passed(self) -> bool:
        return self.converged and self.mtom_kg <= self.cap_kg

    @property
    def structure_kg(self) -> float:
        """Structure only — excludes the tank system, which has no kerosene
        equivalent and would corrupt any comparison against a kerosene type."""
        return (self.groups["outer_wing"] + self.groups["centerbody"]
                + self.groups["landing_gear"])

    @property
    def structural_fraction(self) -> float:
        return self.structure_kg / self.mtom_kg

    def to_dict(self) -> dict:
        return {
            "method": "group_weight_buildup_converged",
            "converged": self.converged,
            "passes": self.passes,
            "passes_to_tolerance": self.tolerance_pass,
            "mtom_kg": self.mtom_kg,
            "oew_kg": self.oew_kg,
            "groups_kg": self.groups,
            "fuel_kg": self.fuel_kg,
            "payload_kg": self.payload_kg,
            "crew_kg": self.crew_kg,
            "structure_kg": self.structure_kg,
            "structural_fraction": self.structural_fraction,
            "oew_fraction": self.oew_kg / self.mtom_kg,
            "tank_system_source": self.tank_source,
            "input_provenance": {
                "sigma_centerbody_kg_m2": "UNGROUNDED PLACEHOLDER — no citation; "
                                          "not from Liebeck or the NASA BWB report",
                "payload_kg": "SOURCED — 6-passenger payload per programme design "
                              "brief, at 100 kg/occupant",
                "crew_kg": "OPEN — 2 x 100 kg assumed; 1 vs 2 pilot certification "
                           "unresolved elsewhere in the programme",
                "eta_g_tank": "bracketed by programme figures (0.30 metallic, 0.55-0.57 "
                              "target); the 0.50 midpoint is a choice, not a measurement",
                "engine_specific_weight_kg_per_kN": "kerosene turbofan figure; LH2 "
                                                    "combustor engine mass is not public",
                "outer_wing": "Raymer Eq 15.46, fitted on conventional GA/bizjet, "
                              "not on a BWB outer panel",
                "landing_gear_fraction": "literature-typical 3-4% of MTOM",
                "systems_fraction": "lumped statistical fraction, Raymer/Torenbeek-style",
            },
            "mtom_cap_kg": self.cap_kg,
            "gate_passed": self.gate_passed,
            "margin_to_cap_kg": self.cap_kg - self.mtom_kg,
            "history_kg": self.history,
            "note": (
                "Conceptual-design group weight buildup. Outer wing and systems use "
                "conventional-aircraft regressions; centerbody sigma, eta_g, engine "
                "specific weight, payload and crew are unsourced placeholders. "
                "The cap is a gate on the result, never an input to it."
            ),
        }


def converge_mtom(mission: MissionConfig, planform: dict, q_pa: float,
                  fuel_kg: float | None = None,
                  tank_mass_override_kg: float | None = None,
                  initial_guess_kg: float | None = None) -> MassResult:
    """Iterate MTOM to closure.

    MTOM appears on both sides — wing, gear, systems and propulsion all scale
    with it. Fuel mass does NOT: it is fixed by tank volume, which is a
    geometric/packaging constraint independent of MTOM. That is what makes this
    converge quickly rather than running away.
    """
    fuel = mission.fuel_mass_kg if fuel_kg is None else float(fuel_kg)
    guess = float(initial_guess_kg if initial_guess_kg is not None else mission.mtom_cap_kg)
    if guess <= 0:
        raise MassModelError(f"initial MTOM guess must be > 0, got {guess!r}")

    tank_mass, tank_source = tank_system_mass_kg(
        fuel, mission.eta_g_tank, tank_mass_override_kg)

    # Iterate to machine-level closure, not merely to mtom_tolerance. If the
    # loop stopped at 0.1% the answer would still carry a trace of the starting
    # guess — and the default starting guess is the CAP, which would let the
    # cap leak into the result it is supposed to be judging. mtom_tolerance is
    # still honoured: it decides when we declare the result converged, and
    # tolerance_pass records where it was first met.
    history, groups, converged, passes = [], {}, False, 0
    tolerance_pass = None
    for passes in range(1, MAX_MTOM_PASSES + 1):
        groups = {
            "outer_wing": outer_wing_mass_kg(
                mtom_kg=guess,
                area_m2=planform["outer_wing_area_m2"],
                aspect_ratio=planform["outer_aspect_ratio"],
                sweep_deg=planform["outer_sweep_quarter_chord_deg"],
                taper_ratio=planform["outer_taper_ratio"],
                tc=planform["outer_mean_tc"] or 0.12,
                q_pa=q_pa,
                ultimate_load_factor=mission.ultimate_load_factor,
            ),
            "centerbody": centerbody_mass_kg(
                mission.sigma_centerbody_kg_m2, planform["centerbody_area_m2"]),
            "landing_gear": landing_gear_mass_kg(mission.landing_gear_fraction, guess),
            "propulsion": propulsion_mass_kg(
                mtom_kg=guess,
                thrust_to_weight=mission.thrust_to_weight,
                specific_weight_kg_per_kn=mission.engine_specific_weight_kg_per_kN,
                installation_factor=mission.propulsion_installation_factor),
            "systems": systems_mass_kg(mission.systems_fraction, guess),
            "tank_system": tank_mass,
        }
        oew = sum(groups.values())
        mtom_new = oew + mission.payload_kg + mission.crew_kg + fuel
        history.append(mtom_new)

        change = abs(mtom_new - guess) / guess
        if tolerance_pass is None and change <= mission.mtom_tolerance:
            tolerance_pass = passes
            converged = True
        guess = mtom_new
        if change <= CLOSURE_TOL:
            break

    oew = sum(groups.values())
    return MassResult(
        converged=converged, passes=passes, mtom_kg=guess, oew_kg=oew, groups=groups,
        fuel_kg=fuel, payload_kg=mission.payload_kg, crew_kg=mission.crew_kg,
        cap_kg=mission.mtom_cap_kg, tank_source=tank_source, history=history,
        tolerance_pass=tolerance_pass,
    )


# ---------------------------------------------------------------------------
# PC-24 calibration cross-check
# ---------------------------------------------------------------------------

# Published figures. VERIFY against Pilatus documentation before quoting these
# anywhere externally — they are used here only as a calibration anchor.
PC24 = {
    "name": "Pilatus PC-24",
    "mtow_kg": 8300.0,
    "oew_kg": 5300.0,
    "wing_area_m2": 30.9,
    "span_m": 17.0,
    "sweep_quarter_chord_deg": 15.0,
    "taper_ratio": 0.40,
    "tc": 0.13,
    "source": "published manufacturer figures — verify before external use",
}


def pc24_crosscheck(mission: MissionConfig, bwb: MassResult, q_pa: float) -> dict:
    """Calibrate the buildup against a real aircraft of similar class.

    The only hard published number for the PC-24 is OEW, so the check works
    backwards from it: apply THIS model's non-structural groups (propulsion,
    systems) at PC-24's MTOW, and whatever is left of the published OEW is its
    implied structure. That gives a structural fraction derived from real data
    plus a consistent model, rather than from a guessed structure/OEW split.

    The red flag this exists to catch: the LH2-BWB coming out structurally
    LIGHTER than the PC-24 despite being larger and, by this programme's own
    established finding, structurally harder. If that happens the number needs
    a real citation for why, not a shrug.
    """
    mtow = PC24["mtow_kg"]
    aspect_ratio = PC24["span_m"] ** 2 / PC24["wing_area_m2"]

    predicted_wing = outer_wing_mass_kg(
        mtom_kg=mtow, area_m2=PC24["wing_area_m2"], aspect_ratio=aspect_ratio,
        sweep_deg=PC24["sweep_quarter_chord_deg"], taper_ratio=PC24["taper_ratio"],
        tc=PC24["tc"], q_pa=q_pa, ultimate_load_factor=mission.ultimate_load_factor,
        wing_fuel_kg=0.30 * mtow,          # kerosene type: fuel IS in the wing
    )
    propulsion = propulsion_mass_kg(
        mtom_kg=mtow, thrust_to_weight=mission.thrust_to_weight,
        specific_weight_kg_per_kn=mission.engine_specific_weight_kg_per_kN,
        installation_factor=mission.propulsion_installation_factor)
    systems = systems_mass_kg(mission.systems_fraction, mtow)
    gear = landing_gear_mass_kg(mission.landing_gear_fraction, mtow)

    # HOW THE "IMPLIED" FRACTION IS BUILT — read before quoting it.
    #
    #   implied_structure = PC-24 published OEW
    #                       - THIS MODEL's propulsion regression at PC-24 MTOW
    #                       - THIS MODEL's systems fraction at PC-24 MTOW
    #
    # It is NOT a published PC-24 structural weight. Pilatus does not publish a
    # structure-only mass; OEW is the only hard figure available. So this is a
    # derived residual, and it inherits every error in this model's propulsion
    # and systems groups — if those are too light, the implied structure comes
    # out too heavy, and the comparison flatters the BWB. Treat it as a
    # consistency check between two applications of the SAME model, anchored on
    # one real number, not as a measurement of the PC-24's actual structure.
    implied_structure = PC24["oew_kg"] - propulsion - systems
    implied_fraction = implied_structure / mtow
    bwb_fraction = bwb.structural_fraction

    lighter = bwb_fraction < implied_fraction
    return {
        "reference": PC24["name"],
        "reference_source": PC24["source"],
        "implied_structure_derivation": (
            "PC-24 published OEW minus THIS MODEL's own propulsion and systems "
            "groups evaluated at PC-24 MTOW. NOT a published PC-24 structural "
            "weight — Pilatus does not publish one. A derived residual that "
            "inherits any error in this model's propulsion/systems estimates."
        ),
        "reference_mtow_kg": mtow,
        "reference_oew_kg": PC24["oew_kg"],
        "model_propulsion_kg": propulsion,
        "model_systems_kg": systems,
        "model_landing_gear_kg": gear,
        "model_predicted_wing_kg": predicted_wing,
        "implied_structure_kg": implied_structure,
        "implied_structural_fraction": implied_fraction,
        "bwb_structure_kg": bwb.structure_kg,
        "bwb_structural_fraction": bwb_fraction,
        "bwb_lighter_than_reference": lighter,
        "flag": (
            "RED FLAG: the LH2-BWB's structural fraction "
            f"({bwb_fraction:.3f}) is BELOW the PC-24's implied "
            f"({implied_fraction:.3f}). The BWB is larger and, per this "
            "programme's own finding, structurally harder. This needs a real "
            "structural-efficiency citation before it is trusted — do not "
            "resolve it by picking a number that feels right."
            if lighter else
            "OK: the LH2-BWB's structural fraction is at or above the PC-24's "
            "implied fraction, which is the expected direction."
        ),
    }


def sensitivity(mission: MissionConfig, planform: dict, q_pa: float,
                sigma_values=(40.0, 60.0, 80.0, 100.0),
                eta_g_values=(0.30, 0.40, 0.50, 0.60)) -> dict:
    """How far the two biggest placeholders move the converged MTOM.

    Reported because quoting a single MTOM from unsourced inputs would overstate
    what this model actually knows.
    """
    from dataclasses import replace

    sigma_rows, eta_rows = [], []
    for value in sigma_values:
        r = converge_mtom(replace(mission, sigma_centerbody_kg_m2=value), planform, q_pa)
        sigma_rows.append({"sigma_kg_m2": value, "mtom_kg": r.mtom_kg,
                           "gate_passed": r.gate_passed})
    for value in eta_g_values:
        r = converge_mtom(replace(mission, eta_g_tank=value), planform, q_pa)
        eta_rows.append({"eta_g": value, "mtom_kg": r.mtom_kg,
                         "gate_passed": r.gate_passed})
    return {"sigma_centerbody_kg_m2": sigma_rows, "eta_g_tank": eta_rows}


__all__ = [
    "PC24", "MassModelError", "MassResult", "centerbody_mass_kg", "converge_mtom",
    "landing_gear_mass_kg", "outer_wing_mass_kg", "pc24_crosscheck",
    "propulsion_mass_kg", "sensitivity", "systems_mass_kg", "tank_system_mass_kg",
]

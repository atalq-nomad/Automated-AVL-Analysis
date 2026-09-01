"""Stage 10 — reserve-based mission profile.

Implements Part A of mtom_and_reserve_range_methodology.md: weight tracked
sequentially through taxi, takeoff, climb, cruise, descent, approach, missed
approach, hold, diversion and landing at the alternate, with contingency held
back on top.

This is SEPARATE from mission.compute_range(), which stays exactly as it was.
That one is the quick/no-reserve estimate iterations 1-3 were logged against;
this one is the number to use for decisions.

WHAT THIS IS NOT
----------------
Not a certified fuel-planning tool. It is a clearly-labelled engineering
approximation of publicly described practice (FAR 91.167 for the 45-minute
hold, NBAA-style diversion and contingency conventions), not an implementation
against primary NBAA document text. Sources disagree on diversion distance
(100 nm is common current practice, 200 nm the more traditional figure) and on
hold parameters, so all of them are configurable rather than baked in. Say so
in anything this feeds to investors or certification discussions.

TWO DELIBERATE SIMPLIFICATIONS — do not "fix" without reading this
------------------------------------------------------------------
1. LOITER L/D EQUALS CRUISE L/D. True max-endurance L/D is higher (a different
   trim CL entirely; it would need its own AVL sweep). Using cruise L/D
   overestimates hold fuel, which is the SAFE direction for a reserve
   calculation. This is a conservative choice, not an oversight.
2. HOLD ALTITUDE DOES NOT ENTER THE ARITHMETIC. The methodology's hold formula
   is fuel = TSFC x (W g / (L/D)) x t. With a single constant TSFC and thrust
   set by weight and L/D, density — and therefore altitude — cancels out
   entirely. hold_altitude_ft is carried in config for the record and for a
   future altitude-dependent TSFC model, but it currently changes nothing.
   Better to say that plainly than to invent a density term the spec does not
   have.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .mission import G0, M_PER_NM, MissionConfig, cruise_state

MAX_CONTINGENCY_PASSES = 12
CONTINGENCY_TOL_KG = 1e-6


@dataclass(frozen=True)
class Segment:
    """One leg, with the weight either side of it and how it was computed."""

    name: str
    w_start_kg: float
    w_end_kg: float
    method: str

    @property
    def fuel_kg(self) -> float:
        return self.w_start_kg - self.w_end_kg


@dataclass
class ProfileResult:
    """Outcome of flying the full profile at a given trip cruise range."""

    feasible: bool
    segments: list[Segment] = field(default_factory=list)
    trip_range_m: float = 0.0
    trip_fuel_kg: float = 0.0          # start -> landing at destination
    reserve_fuel_kg: float = 0.0       # missed approach -> landing at alternate
    contingency_kg: float = 0.0        # held back, never burned
    total_fuel_required_kg: float = 0.0
    fuel_available_kg: float = 0.0
    landing_weight_kg: float = 0.0
    l_over_d: float = 0.0
    passes: int = 0
    note: str = ""

    @property
    def trip_range_nm(self) -> float:
        return self.trip_range_m / M_PER_NM

    @property
    def trip_range_km(self) -> float:
        return self.trip_range_m / 1000.0

    @property
    def fuel_margin_kg(self) -> float:
        return self.fuel_available_kg - self.total_fuel_required_kg

    @property
    def reserve_and_contingency_kg(self) -> float:
        """Fuel loaded but not available for the trip: reserves + contingency."""
        return self.reserve_fuel_kg + self.contingency_kg

    @property
    def reserve_and_contingency_pct(self) -> float:
        """Reserves + contingency as a percentage of total LOADED fuel.

        A reportable finding in its own right, not an intermediate. A fixed
        100 nm diversion and 45-minute hold take a far larger bite out of an
        LH2 fuel fraction (~9% of MTOM here) than the same policy takes out of
        a kerosene aircraft's (~35% of MTOM). This is the tank-volume-is-
        binding result showing up from a different angle.
        """
        if self.fuel_available_kg <= 0.0:
            return 0.0
        return self.reserve_and_contingency_kg / self.fuel_available_kg * 100.0

    @property
    def trip_fuel_pct(self) -> float:
        """Trip fuel as a percentage of total loaded fuel."""
        if self.fuel_available_kg <= 0.0:
            return 0.0
        return self.trip_fuel_kg / self.fuel_available_kg * 100.0

    def segment_shares(self) -> list[dict]:
        """Per-segment fuel and its share of loaded fuel.

        Kept visible on purpose: the per-segment sanity read is what exposed
        the kerosene-fraction bug (climb alone was eating 23% of the tank).
        A single aggregate number would have hidden it.
        """
        total = self.fuel_available_kg or 1.0
        return [
            {"name": s.name, "w_start_kg": s.w_start_kg, "w_end_kg": s.w_end_kg,
             "fuel_kg": s.fuel_kg, "pct_of_loaded_fuel": s.fuel_kg / total * 100.0,
             "method": s.method}
            for s in self.segments
        ]

    def to_dict(self) -> dict:
        return {
            "method": "reserve_based_segment_profile",
            "feasible": self.feasible,
            "trip_range_m": self.trip_range_m,
            "trip_range_km": self.trip_range_km,
            "trip_range_nm": self.trip_range_nm,
            "trip_fuel_kg": self.trip_fuel_kg,
            "reserve_fuel_kg": self.reserve_fuel_kg,
            "contingency_kg": self.contingency_kg,
            "total_fuel_required_kg": self.total_fuel_required_kg,
            "fuel_available_kg": self.fuel_available_kg,
            "fuel_margin_kg": self.fuel_margin_kg,
            "landing_weight_kg": self.landing_weight_kg,
            "l_over_d": self.l_over_d,
            "contingency_passes": self.passes,
            # Headline finding: how much of the loaded tank never moves the
            # aircraft toward its destination.
            "trip_fuel_pct_of_loaded_fuel": self.trip_fuel_pct,
            "reserve_and_contingency_kg": self.reserve_and_contingency_kg,
            "reserve_and_contingency_pct_of_loaded_fuel": self.reserve_and_contingency_pct,
            "segments": self.segment_shares(),
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def effective_fractions(mission: MissionConfig) -> dict:
    """The five segment weight fractions, optionally LHV-rescaled.

    The published fractions (0.995 taxi, 0.980 climb, ...) are MASS fractions
    calibrated on kerosene turbine aircraft, where fuel is roughly 35% of MTOM.
    On this airframe LH2 is about 9% of MTOM, so charging climb 2% of aircraft
    weight spends roughly a quarter of the entire tank before cruise begins.
    That is an artefact of transplanting a kerosene-calibrated fraction onto a
    fuel with 2.8x the energy per kilogram, not a physical result.

    With lhv_scaling_enabled the burn is rescaled to hold segment ENERGY, not
    segment mass, constant:  1 - f_LH2 = (1 - f_kero) x LHV_kero / LHV_LH2.

    Default ON: the methodology document specifies this as a fix to a
    unit-consistency defect in the published fraction values, not an optional
    tuning knob. Set lhv_scaling_enabled false only to reproduce the
    uncorrected result for comparison.

    It applies ONLY to these five weight-fraction segments. Cruise and
    diversion cruise already come from the Breguet equation using real LH2
    TSFC, so they are fuel-consistent already; contingency is a percentage of
    an already-corrected trip fuel and inherits the fix automatically.
    """
    raw = {
        "taxi": mission.f_taxi,
        "takeoff": mission.f_takeoff,
        "climb": mission.f_climb,
        "descent": mission.f_descent,
        "approach": mission.f_approach,
    }
    if not mission.lhv_scaling_enabled:
        return raw
    ratio = mission.lhv_kerosene_MJ_per_kg / mission.lhv_lh2_MJ_per_kg
    return {k: 1.0 - (1.0 - v) * ratio for k, v in raw.items()}


def breguet_factor(mission: MissionConfig, distance_m: float, l_over_d: float) -> float:
    """Wf/Wi for a cruise leg of `distance_m` — i.e. exp(-R g TSFC / (V L/D))."""
    if l_over_d <= 0.0:
        raise ValueError(f"l_over_d must be > 0, got {l_over_d!r}")
    if distance_m < 0.0:
        raise ValueError(f"distance_m must be >= 0, got {distance_m!r}")
    return math.exp(-_range_constant(mission, l_over_d) * distance_m)


def _range_constant(mission: MissionConfig, l_over_d: float) -> float:
    """k in Wf/Wi = exp(-k R): g TSFC / (V L/D), per metre."""
    v = cruise_state(mission)["velocity_ms"]
    return G0 * mission.tsfc_kg_per_Ns / (v * l_over_d)


def missed_approach_reference_weight_kg(mission: MissionConfig) -> float:
    """Arrival weight used to size the go-around allowance.

    Deliberately independent of trip range: it is the destination weight for a
    zero-length cruise, i.e. the HEAVIEST possible arrival. Two reasons:

    1. The methodology calls this a FIXED fuel mass allowance. Sizing it off
       the running weight would silently make it range-dependent, which is a
       different thing from what the document specifies.
    2. Heaviest arrival is the conservative end, and it makes the fuel budget
       close exactly — the forward solve and fly_profile then agree to the
       kilogram instead of drifting apart with range.
    """
    f = effective_fractions(mission)
    return (mission.mtom_kg * f["taxi"] * f["takeoff"] * f["climb"]
            * f["descent"] * f["approach"])


def missed_approach_fuel_kg(mission: MissionConfig, l_over_d: float) -> float:
    """Fixed fuel allowance for the go-around — a mass, not a fraction.

    Short and high-thrust, so it does not scale with distance. Modelled as
    time x (multiplier x thrust at the reference arrival weight) x TSFC. The
    multiplier is a PLACEHOLDER (see the methodology document's parameter
    table); missed_approach_fuel_kg in config overrides the whole calculation.
    """
    if mission.missed_approach_fuel_kg is not None:
        return float(mission.missed_approach_fuel_kg)
    weight_kg = missed_approach_reference_weight_kg(mission)
    thrust_n = mission.missed_approach_thrust_multiplier * weight_kg * G0 / l_over_d
    return mission.tsfc_kg_per_Ns * thrust_n * mission.missed_approach_time_s


def hold_fuel_fraction(mission: MissionConfig, l_over_d: float) -> float:
    """Fraction of weight burned holding — the hold is proportional to weight.

    fuel = TSFC x (W g / (L/D)) x t, so fuel/W is a constant and the hold is a
    weight ratio like the other segments. L/D is CRUISE L/D on purpose; see the
    module docstring.
    """
    return mission.tsfc_kg_per_Ns * G0 * (mission.hold_time_min * 60.0) / l_over_d


# ---------------------------------------------------------------------------
# Fly the profile
# ---------------------------------------------------------------------------


def fly_profile(mission: MissionConfig, l_over_d: float,
                trip_range_m: float) -> ProfileResult:
    """Track weight through every segment for a given trip cruise distance."""
    if trip_range_m < 0.0:
        raise ValueError(f"trip_range_m must be >= 0, got {trip_range_m!r}")

    f = effective_fractions(mission)
    segments: list[Segment] = []

    def leg(name, w_start, w_end, method):
        segments.append(Segment(name, w_start, w_end, method))
        return w_end

    w = mission.mtom_kg
    w = leg("taxi-out", w, w * f["taxi"], f"weight fraction {f['taxi']:.6f}")
    w = leg("takeoff", w, w * f["takeoff"], f"weight fraction {f['takeoff']:.6f}")
    w = leg("climb", w, w * f["climb"], f"weight fraction {f['climb']:.6f}")

    cruise_ratio = breguet_factor(mission, trip_range_m, l_over_d)
    w = leg("cruise (trip)", w, w * cruise_ratio,
            f"Breguet over {trip_range_m / M_PER_NM:.1f} nm at L/D {l_over_d:.3f}")

    w = leg("descent", w, w * f["descent"], f"weight fraction {f['descent']:.6f}")
    w = leg("approach (destination)", w, w * f["approach"],
            f"weight fraction {f['approach']:.6f}")

    weight_at_destination = w
    trip_fuel = mission.mtom_kg - weight_at_destination

    # --- reserves ------------------------------------------------------
    missed = missed_approach_fuel_kg(mission, l_over_d)
    w = leg("missed approach", w, w - missed,
            f"fixed allowance {missed:.2f} kg "
            f"({mission.missed_approach_time_s:.0f} s at "
            f"{mission.missed_approach_thrust_multiplier:.1f}x cruise thrust)"
            if mission.missed_approach_fuel_kg is None
            else f"fixed allowance {missed:.2f} kg (config override)")

    hold_fraction = hold_fuel_fraction(mission, l_over_d)
    w = leg("climb + hold", w, w * (1.0 - hold_fraction),
            f"{mission.hold_time_min:.0f} min at cruise L/D "
            f"(conservative; burns {hold_fraction * 100:.3f}% of weight)")

    diversion_m = mission.diversion_distance_nm * M_PER_NM
    w = leg("diversion cruise", w, w * breguet_factor(mission, diversion_m, l_over_d),
            f"Breguet over {mission.diversion_distance_nm:.0f} nm at cruise L/D")

    w = leg("approach (alternate)", w, w * f["approach"],
            f"weight fraction {f['approach']:.6f}")

    reserve_fuel = weight_at_destination - w
    contingency = mission.contingency_fraction * trip_fuel
    total_required = (mission.mtom_kg - w) + contingency

    return ProfileResult(
        feasible=True,
        segments=segments,
        trip_range_m=trip_range_m,
        trip_fuel_kg=trip_fuel,
        reserve_fuel_kg=reserve_fuel,
        contingency_kg=contingency,
        total_fuel_required_kg=total_required,
        fuel_available_kg=mission.fuel_mass_kg,
        landing_weight_kg=w,
        l_over_d=l_over_d,
        note=_scaling_note(mission),
    )


def _scaling_note(mission: MissionConfig) -> str:
    if mission.lhv_scaling_enabled:
        return ("Segment fractions LHV-rescaled "
                f"({mission.lhv_kerosene_MJ_per_kg:.0f}/{mission.lhv_lh2_MJ_per_kg:.0f} MJ/kg) "
                "to hold segment energy constant — the specified default.")
    return ("UNCORRECTED: kerosene-calibrated segment fractions applied as LH2 mass "
            "fractions. Overstates non-cruise burn by roughly the LHV ratio. Retained "
            "for comparison only — not the methodology's default.")


# ---------------------------------------------------------------------------
# Forward solve: available fuel -> maximum trip range
# ---------------------------------------------------------------------------


def solve_max_range(mission: MissionConfig, l_over_d: float,
                    fuel_available_kg: float | None = None) -> ProfileResult:
    """Maximum trip cruise range that still closes the whole chain.

    Contingency is 5% of trip fuel, and trip fuel depends on the range being
    solved for — a small implicit loop. Per the methodology document this is
    handled with a fixed-point iteration (guess contingency, solve, recompute,
    repeat), not closed-form algebra. It converges in 2-3 passes because
    contingency is a small correction.
    """
    if l_over_d <= 0.0:
        raise ValueError(f"l_over_d must be > 0, got {l_over_d!r}")
    available = mission.fuel_mass_kg if fuel_available_kg is None else float(fuel_available_kg)
    if available <= 0.0:
        raise ValueError(f"fuel_available_kg must be > 0, got {available!r}")

    contingency = 0.0
    passes = 0
    for passes in range(1, MAX_CONTINGENCY_PASSES + 1):
        trip_range_m = _range_for_burnable_fuel(
            mission, l_over_d, available - contingency)
        if trip_range_m is None:
            return _infeasible(mission, l_over_d, available)
        result = fly_profile(mission, l_over_d, trip_range_m)
        new_contingency = result.contingency_kg
        if abs(new_contingency - contingency) <= CONTINGENCY_TOL_KG:
            contingency = new_contingency
            break
        contingency = new_contingency

    result = fly_profile(mission, l_over_d, trip_range_m)
    result.passes = passes
    result.fuel_available_kg = available
    return result


def _range_for_burnable_fuel(mission: MissionConfig, l_over_d: float,
                             burnable_kg: float) -> float | None:
    """Closed-form trip range for a fixed burnable-fuel budget.

    Solves the chain for the cruise weight ratio E. Only the cruise leg is
    unknown, and everything downstream of it is either a fixed multiplier or a
    single fixed mass, so E falls out directly:

        W_destination = W0 . A . E . P          A = taxi.takeoff.climb
        W_final       = (W_destination - m_missed) . Q   P = descent.approach
        burnable      = W0 - W_final                    Q = hold . divert . approach

    Returns None when no positive cruise range closes the chain.
    """
    if burnable_kg <= 0.0:
        return None

    f = effective_fractions(mission)
    a = f["taxi"] * f["takeoff"] * f["climb"]
    p = f["descent"] * f["approach"]

    hold_fraction = hold_fuel_fraction(mission, l_over_d)
    if hold_fraction >= 1.0:
        return None
    diversion_m = mission.diversion_distance_nm * M_PER_NM
    q = ((1.0 - hold_fraction)
         * breguet_factor(mission, diversion_m, l_over_d)
         * f["approach"])

    w0 = mission.mtom_kg
    missed = missed_approach_fuel_kg(mission, l_over_d)

    e = (w0 + missed * q - burnable_kg) / (w0 * a * p * q)
    if not 0.0 < e < 1.0:
        return None
    return -math.log(e) / _range_constant(mission, l_over_d)


def _infeasible(mission: MissionConfig, l_over_d: float,
                available: float) -> ProfileResult:
    """Zero-range chain, to report the shortfall rather than raising."""
    zero = fly_profile(mission, l_over_d, 0.0)
    zero.feasible = False
    zero.fuel_available_kg = available
    zero.trip_range_m = 0.0
    zero.note = (
        f"INFEASIBLE: reserves alone need {zero.total_fuel_required_kg:.1f} kg but only "
        f"{available:.1f} kg is available. The aircraft cannot complete the reserve "
        "profile even with zero trip range. " + _scaling_note(mission)
    )
    return zero


# ---------------------------------------------------------------------------
# Inverse solve: target range -> required fuel and tank volume
# ---------------------------------------------------------------------------


def solve_required_fuel(mission: MissionConfig, l_over_d: float,
                        target_range_nm: float) -> dict:
    """Fuel mass — and therefore tank volume — needed for a target trip range.

    No iteration is needed in this direction: fixing the range fixes every
    weight in the chain, so trip fuel and its contingency follow directly.
    Generalises the hand calculation of what closing to 4,000 nm would take.
    """
    if target_range_nm <= 0.0:
        raise ValueError(f"target_range_nm must be > 0, got {target_range_nm!r}")

    result = fly_profile(mission, l_over_d, target_range_nm * M_PER_NM)
    required = result.total_fuel_required_kg
    volume = required / (mission.lh2_density_kgm3 * mission.lh2_fill_fraction)

    return {
        "method": "reserve_based_segment_profile_inverse",
        "target_range_nm": target_range_nm,
        "required_fuel_kg": required,
        "required_tank_volume_m3": volume,
        "trip_fuel_kg": result.trip_fuel_kg,
        "reserve_fuel_kg": result.reserve_fuel_kg,
        "contingency_kg": result.contingency_kg,
        "current_tank_volume_m3": mission.tank_volume_m3,
        "current_fuel_kg": mission.fuel_mass_kg,
        "l_over_d": l_over_d,
        "note": result.note,
    }


def required_fuel_no_reserve(mission: MissionConfig, l_over_d: float,
                             target_range_nm: float) -> dict:
    """Same inverse question under the QUICK no-reserve method, for comparison.

    Inverts mission.compute_range() without altering it. Present so the two
    methods can be quoted side by side, and because this is the form the
    existing hand calculation of tank volume for 4,000 nm was done in.
    """
    if target_range_nm <= 0.0:
        raise ValueError(f"target_range_nm must be > 0, got {target_range_nm!r}")
    k = _range_constant(mission, l_over_d)
    ratio = math.exp(k * target_range_nm * M_PER_NM)          # Wi/Wf
    required = mission.mtom_kg * (1.0 - 1.0 / ratio)
    volume = required / (mission.lh2_density_kgm3 * mission.lh2_fill_fraction)
    return {
        "method": "breguet_single_segment_no_reserve_inverse",
        "target_range_nm": target_range_nm,
        "required_fuel_kg": required,
        "required_tank_volume_m3": volume,
        "l_over_d": l_over_d,
    }


__all__ = [
    "ProfileResult",
    "Segment",
    "breguet_factor",
    "effective_fractions",
    "fly_profile",
    "hold_fuel_fraction",
    "missed_approach_fuel_kg",
    "missed_approach_reference_weight_kg",
    "required_fuel_no_reserve",
    "solve_max_range",
    "solve_required_fuel",
]

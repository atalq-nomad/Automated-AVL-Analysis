"""Stage 1 — mission assumptions, ISA atmosphere, CL target and Breguet range.

Everything in here is shared across ALL iterations. Nothing geometry-specific
belongs in this module or in mission.yaml; if it changes per run it goes in
cases/<name>.yaml instead, otherwise the iteration-to-iteration comparison
stops being a fair one.
"""

from __future__ import annotations

import math
from dataclasses import MISSING, dataclass, fields
from pathlib import Path

from .yamlio import load_yaml

# Fields that are not plain positive floats.
BOOL_FIELDS = {"lhv_scaling_enabled"}
OPTIONAL_FIELDS = {"missed_approach_fuel_kg"}

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _as_bool(raw, name: str, source: str) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{source}: field {name!r} must be true or false, got {raw!r}")

# Physical constants. Fixed here rather than in config so a typo in a YAML file
# can never move gravity.
G0 = 9.80665            # m/s^2
R_AIR = 287.053         # J/(kg K)
GAMMA = 1.4
T_STRAT = 216.65        # K, isothermal lower stratosphere
RHO_11KM = 0.3639       # kg/m^3 at the tropopause
H_TROPOPAUSE = 11000.0  # m
H_STRAT_TOP = 20000.0   # m, upper validity limit of the isothermal layer
M_PER_NM = 1852.0


@dataclass(frozen=True)
class MissionConfig:
    """Cruise condition, engine and tank assumptions held constant across runs."""

    cruise_altitude_m: float
    cruise_mach: float
    mtom_kg: float
    cfe: float
    tsfc_kg_per_Ns: float
    tank_volume_m3: float
    lh2_fill_fraction: float
    lh2_density_kgm3: float

    # ------------------------------------------------------------------
    # Stage 10 — reserve-based mission profile parameters.
    #
    # All optional with defaults, so every mission.yaml written before Stage
    # 10 still loads unchanged and Fixture D is untouched. Defaults come from
    # the parameter table in mtom_and_reserve_range_methodology.md, Part A.
    # They are literature-typical starting values, NOT program-validated —
    # see that document and the README placeholder list.
    # ------------------------------------------------------------------

    # Mission segment weight fractions (Raymer-style). Kerosene-turbine
    # calibrated; see lhv_scaling_enabled below for why that matters here.
    f_taxi: float = 0.995
    f_takeoff: float = 0.995
    f_climb: float = 0.980
    f_descent: float = 0.990
    f_approach: float = 0.992          # applied at destination AND at alternate

    # Missed approach / go-around: a fixed fuel mass, not a distance-scaled
    # fraction. Derived as time x (multiplier x cruise thrust) x TSFC unless
    # missed_approach_fuel_kg is set, which overrides it outright.
    missed_approach_time_s: float = 120.0
    missed_approach_thrust_multiplier: float = 3.0   # PLACEHOLDER, not sourced
    missed_approach_fuel_kg: float | None = None

    diversion_distance_nm: float = 100.0   # current industry practice; 200 = traditional NBAA
    hold_time_min: float = 45.0            # FAR 91.167 legal minimum
    hold_altitude_ft: float = 1500.0       # carried for the record; see mission_profile.py
    contingency_fraction: float = 0.05     # commercial practice, not Part 91 mandated

    # Corrects a unit-consistency defect in the five fractions above: they are
    # kerosene-calibrated, so applying them as LH2 MASS fractions overstates the
    # burn by roughly the LHV ratio. Default TRUE per the methodology document —
    # this is a bug fix, not a tuning knob. See effective_fractions().
    lhv_scaling_enabled: bool = True
    lhv_kerosene_MJ_per_kg: float = 43.0
    lhv_lh2_MJ_per_kg: float = 120.0

    # ------------------------------------------------------------------
    # Stage 11 — mass model (Part B). Several of these are UNSOURCED
    # placeholders; see pipeline/mass_model.py's header and the README list.
    # ------------------------------------------------------------------
    mtom_cap_kg: float = 7300.0        # a GATE on the result, never an input
    mtom_tolerance: float = 0.001      # relative, on the convergence loop
    payload_kg: float = 600.0          # 6 pax per programme design brief
    crew_kg: float = 200.0             # 2 crew — 1 vs 2 pilot is OPEN
    eta_g_tank: float = 0.50           # PLACEHOLDER, highest-leverage number
    sigma_centerbody_kg_m2: float = 60.0   # PLACEHOLDER, CITATION NEEDED
    landing_gear_fraction: float = 0.035
    systems_fraction: float = 0.130
    engine_specific_weight_kg_per_kN: float = 19.0   # kerosene turbofan figure
    thrust_to_weight: float = 0.38
    propulsion_installation_factor: float = 1.40
    ultimate_load_factor: float = 3.75     # 1.5 x 2.5 limit
    centerbody_span_fraction: float = 0.35 # of half-span; see planform.py

    @classmethod
    def from_yaml(cls, path) -> "MissionConfig":
        return cls.from_dict(load_yaml(path), source=str(path))

    @classmethod
    def from_dict(cls, data: dict, source: str = "<dict>") -> "MissionConfig":
        all_fields = fields(cls)
        names = [f.name for f in all_fields]
        required = [f.name for f in all_fields if f.default is MISSING]
        missing = [n for n in required if n not in data]
        if missing:
            raise ValueError(f"{source}: missing required mission field(s): {', '.join(missing)}")
        unknown = [k for k in data if k not in names]
        if unknown:
            raise ValueError(
                f"{source}: unknown mission field(s): {', '.join(sorted(unknown))}. "
                f"Known fields: {', '.join(names)}"
            )

        values = {}
        for f in all_fields:
            if f.name not in data:
                continue                       # keep the dataclass default
            raw = data[f.name]
            if f.name in BOOL_FIELDS:
                values[f.name] = _as_bool(raw, f.name, source)
                continue
            if raw is None and f.name in OPTIONAL_FIELDS:
                values[f.name] = None
                continue
            if isinstance(raw, bool) or raw is None:
                raise ValueError(f"{source}: field {f.name!r} must be a number, got {raw!r}")
            try:
                values[f.name] = float(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"{source}: field {f.name!r} must be a number, got {raw!r}") from None

        cfg = cls(**values)
        cfg.validate(source)
        return cfg

    def validate(self, source: str = "<config>") -> None:
        positive = [
            "cruise_altitude_m", "cruise_mach", "mtom_kg", "cfe",
            "tsfc_kg_per_Ns", "tank_volume_m3", "lh2_density_kgm3",
        ]
        for name in positive:
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{source}: {name} must be > 0, got {getattr(self, name)!r}")
        if not 0.0 < self.lh2_fill_fraction <= 1.0:
            raise ValueError(
                f"{source}: lh2_fill_fraction must be in (0, 1], got {self.lh2_fill_fraction!r}"
            )
        if self.fuel_mass_kg >= self.mtom_kg:
            raise ValueError(
                f"{source}: fuel mass {self.fuel_mass_kg:.1f} kg is not less than MTOM "
                f"{self.mtom_kg:.1f} kg — the aircraft would be all fuel and no structure"
            )

        # -- Stage 10 reserve parameters ---------------------------------
        for name in ("f_taxi", "f_takeoff", "f_climb", "f_descent", "f_approach"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(
                    f"{source}: {name} is a weight fraction and must be in (0, 1], got {value!r}"
                )
        for name in ("missed_approach_time_s", "missed_approach_thrust_multiplier",
                     "diversion_distance_nm", "hold_time_min",
                     "lhv_kerosene_MJ_per_kg", "lhv_lh2_MJ_per_kg",
                     "mtom_cap_kg", "mtom_tolerance", "payload_kg",
                     "sigma_centerbody_kg_m2", "landing_gear_fraction",
                     "systems_fraction", "engine_specific_weight_kg_per_kN",
                     "thrust_to_weight", "propulsion_installation_factor",
                     "ultimate_load_factor"):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{source}: {name} must be > 0, got {getattr(self, name)!r}")
        if self.missed_approach_fuel_kg is not None and self.missed_approach_fuel_kg < 0.0:
            raise ValueError(
                f"{source}: missed_approach_fuel_kg must be >= 0 when given, "
                f"got {self.missed_approach_fuel_kg!r}"
            )
        if not 0.0 <= self.contingency_fraction < 1.0:
            raise ValueError(
                f"{source}: contingency_fraction must be in [0, 1), "
                f"got {self.contingency_fraction!r}"
            )
        if not 0.0 < self.eta_g_tank < 1.0:
            raise ValueError(f"{source}: eta_g_tank must be in (0, 1), "
                             f"got {self.eta_g_tank!r}")
        if not 0.0 < self.centerbody_span_fraction < 1.0:
            raise ValueError(f"{source}: centerbody_span_fraction must be in (0, 1), "
                             f"got {self.centerbody_span_fraction!r}")
        if self.crew_kg < 0.0:
            raise ValueError(f"{source}: crew_kg must be >= 0, got {self.crew_kg!r}")
        if self.hold_altitude_ft < 0.0:
            raise ValueError(f"{source}: hold_altitude_ft must be >= 0, "
                             f"got {self.hold_altitude_ft!r}")

    # -- derived quantities -------------------------------------------------

    @property
    def fuel_mass_kg(self) -> float:
        """Usable LH2 mass: density x tank volume x fill fraction."""
        return self.lh2_density_kgm3 * self.tank_volume_m3 * self.lh2_fill_fraction

    @property
    def weight_N(self) -> float:
        return self.mtom_kg * G0


def isa_density_and_speed_of_sound(altitude_m: float) -> tuple[float, float]:
    """ISA density and speed of sound in the isothermal stratosphere.

    Valid 11-20 km only. Below the tropopause the troposphere lapse-rate
    formula applies and is deliberately NOT implemented: cruise for this
    program is never below FL360, so a low-altitude request means something
    upstream is wrong and must not quietly get a stratospheric answer.
    """
    if altitude_m < H_TROPOPAUSE:
        raise NotImplementedError(
            f"altitude {altitude_m:.1f} m is below the tropopause ({H_TROPOPAUSE:.0f} m). "
            "Only the isothermal stratosphere model (11-20 km) is implemented; the "
            "troposphere lapse-rate formula is not. Cruise should never be below FL360 "
            "for this program — check the mission config rather than extending this blindly."
        )
    if altitude_m > H_STRAT_TOP:
        raise NotImplementedError(
            f"altitude {altitude_m:.1f} m is above {H_STRAT_TOP:.0f} m, where the ISA "
            "temperature starts rising again. The isothermal model does not apply."
        )

    rho = RHO_11KM * math.exp(-G0 * (altitude_m - H_TROPOPAUSE) / (R_AIR * T_STRAT))
    a = math.sqrt(GAMMA * R_AIR * T_STRAT)
    return rho, a


def cruise_state(mission: MissionConfig) -> dict:
    """rho, speed of sound, true airspeed and dynamic pressure at cruise."""
    rho, a = isa_density_and_speed_of_sound(mission.cruise_altitude_m)
    v = mission.cruise_mach * a
    return {
        "rho_kgm3": rho,
        "a_ms": a,
        "velocity_ms": v,
        "q_Pa": 0.5 * rho * v * v,
    }


def compute_cl_target(mission: MissionConfig, sref_m2: float) -> float:
    """Cruise CL required for level flight at MTOM.

    Depends on Sref, which changes with every geometry — so this is recomputed
    from the current run's geometry_summary.json every time, never carried over
    from a previous iteration's run.txt.
    """
    if sref_m2 <= 0.0:
        raise ValueError(f"sref_m2 must be > 0, got {sref_m2!r}")
    state = cruise_state(mission)
    return mission.weight_N / (state["q_Pa"] * sref_m2)


def compute_range(mission: MissionConfig, l_over_d: float, cl_target: float | None = None) -> dict:
    """QUICK / NO-RESERVE cruise range estimate — optimistic by construction.

    Single-segment Breguet with 100% of the tank burned in cruise: no taxi, no
    takeoff, no climb, no descent, no approach, no diversion, no hold and no
    contingency. It is the number iterations 1-3 were logged against and is
    kept unchanged for that reason (Fixture D depends on it), NOT because it is
    the better number.

    For anything resembling a real decision use
    pipeline.mission_profile.solve_max_range(), which flies the full reserve
    profile. This function is retained and reported alongside it purely for
    continuity with already-logged iterations — the same way results.json
    carries drag_model_note to flag CD0 as ranking-only.

    Fuel is set by the tank, not by the mission: LH2 is volume-limited, so the
    burn is whatever fits in tank_volume_m3 at lh2_fill_fraction. `cl_target`
    is not used by the formula; it is accepted and echoed back so the reported
    range and the CL the aero solution was actually run at stay together.
    """
    if l_over_d <= 0.0:
        raise ValueError(f"l_over_d must be > 0, got {l_over_d!r}")

    state = cruise_state(mission)
    fuel_mass_kg = mission.fuel_mass_kg
    wf_kg = mission.mtom_kg - fuel_mass_kg
    if wf_kg <= 0.0:
        raise ValueError(
            f"end-of-cruise mass {wf_kg:.1f} kg is not positive: fuel mass "
            f"{fuel_mass_kg:.1f} kg exceeds MTOM {mission.mtom_kg:.1f} kg"
        )

    ln_wi_wf = math.log(mission.mtom_kg / wf_kg)
    range_m = (state["velocity_ms"] / (G0 * mission.tsfc_kg_per_Ns)) * l_over_d * ln_wi_wf

    return {
        "fuel_mass_kg": fuel_mass_kg,
        "wf_kg": wf_kg,
        "ln_wi_wf": ln_wi_wf,
        "range_m": range_m,
        "range_km": range_m / 1000.0,
        "range_nm": range_m / M_PER_NM,
        "l_over_d": l_over_d,
        "cl_target": cl_target,
        "method": "breguet_single_segment_no_reserve",
        "note": (
            "QUICK ESTIMATE — optimistic. 100% of tank fuel burned in cruise; no taxi, "
            "climb, descent, diversion, hold or contingency. Reported for continuity with "
            "iterations logged before Stage 10. Use the reserve-based profile for decisions."
        ),
        **state,
    }


def default_mission_path() -> Path:
    """mission.yaml at the project root (the parent of this package)."""
    return Path(__file__).resolve().parent.parent / "mission.yaml"

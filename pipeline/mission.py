"""Stage 1 — mission assumptions, ISA atmosphere, CL target and Breguet range.

Everything in here is shared across ALL iterations. Nothing geometry-specific
belongs in this module or in mission.yaml; if it changes per run it goes in
cases/<name>.yaml instead, otherwise the iteration-to-iteration comparison
stops being a fair one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from pathlib import Path

from .yamlio import load_yaml

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

    @classmethod
    def from_yaml(cls, path) -> "MissionConfig":
        return cls.from_dict(load_yaml(path), source=str(path))

    @classmethod
    def from_dict(cls, data: dict, source: str = "<dict>") -> "MissionConfig":
        names = [f.name for f in fields(cls)]
        missing = [n for n in names if n not in data]
        if missing:
            raise ValueError(f"{source}: missing required mission field(s): {', '.join(missing)}")
        unknown = [k for k in data if k not in names]
        if unknown:
            raise ValueError(
                f"{source}: unknown mission field(s): {', '.join(sorted(unknown))}. "
                f"Known fields: {', '.join(names)}"
            )

        values = {}
        for n in names:
            raw = data[n]
            if isinstance(raw, bool) or raw is None:
                raise ValueError(f"{source}: field {n!r} must be a number, got {raw!r}")
            try:
                values[n] = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{source}: field {n!r} must be a number, got {raw!r}") from None

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
    """Single-segment Breguet cruise range for a fixed LH2 tank volume.

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
        **state,
    }


def default_mission_path() -> Path:
    """mission.yaml at the project root (the parent of this package)."""
    return Path(__file__).resolve().parent.parent / "mission.yaml"

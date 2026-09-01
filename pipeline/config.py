"""Stage 2 — per-case configuration.

One YAML per iteration. Only geometry-specific things live here; anything that
must stay identical between iterations belongs in mission.yaml.

Relative paths (stl_path, avl_exe) resolve against `base_dir`, which defaults
to the current working directory — matching the documented invocation from the
project root:

    python pipeline/run_case.py cases/iteration_4.yaml mission.yaml
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .yamlio import load_yaml

# Defaults mirror stl_to_avl.py's argparse defaults (see its main()). A case
# file that omits a field gets the same behaviour as running the script bare.
STL_TO_AVL_DEFAULTS = {
    "units": "m",
    "axes": "xyz",
    "n_sections": 21,
    "cluster": 1.6,
    "mach": 0.0,
    "nchord": 14,
    "cspace": 1.0,
}

UNIT_SCALE = {"m": 1.0, "mm": 1e-3, "in": 0.0254}

REQUIRED = ("name", "stl_path", "avl_exe")


@dataclass
class CaseConfig:
    """One geometry iteration: what to mesh, how to orient it, what to run it with."""

    name: str
    stl_path: Path
    avl_exe: Path
    units: str = STL_TO_AVL_DEFAULTS["units"]
    axes: str = STL_TO_AVL_DEFAULTS["axes"]
    n_sections: int = STL_TO_AVL_DEFAULTS["n_sections"]
    cluster: float = STL_TO_AVL_DEFAULTS["cluster"]
    mach: float = STL_TO_AVL_DEFAULTS["mach"]
    nchord: int = STL_TO_AVL_DEFAULTS["nchord"]
    cspace: float = STL_TO_AVL_DEFAULTS["cspace"]
    # Onshape mass-properties wetted area. Takes precedence over the
    # mesh-derived value in the CD0 build-up when present (Stage 6).
    s_wet_override_m2: float | None = None
    # Detailed tank-system mass from the separate tank-packaging analysis.
    # Takes precedence over mass_model's eta_g proxy, exactly as
    # s_wet_override_m2 takes precedence over the mesh-derived wetted area.
    tank_system_mass_override_kg: float | None = None
    # Labels for the log entry header only; no effect on any calculation.
    # Both default to values derived from `name` (see concept_and_iteration).
    concept: str | None = None
    iteration: int | None = None
    source_path: Path | None = field(default=None, compare=False)

    @classmethod
    def from_yaml(cls, path, base_dir=None, require_files: bool = True) -> "CaseConfig":
        path = Path(path)
        base = Path(base_dir) if base_dir is not None else Path.cwd()
        cfg = cls.from_dict(load_yaml(path), base_dir=base, source=str(path),
                            require_files=require_files)
        cfg.source_path = path.resolve()
        return cfg

    @classmethod
    def from_dict(cls, data: dict, base_dir=None, source: str = "<dict>",
                  require_files: bool = True) -> "CaseConfig":
        base = Path(base_dir) if base_dir is not None else Path.cwd()
        names = [f.name for f in fields(cls) if f.name != "source_path"]

        missing = [n for n in REQUIRED if data.get(n) in (None, "")]
        if missing:
            raise ValueError(
                f"{source}: missing required case field(s): {', '.join(missing)}. "
                f"Required: {', '.join(REQUIRED)}"
            )
        unknown = [k for k in data if k not in names]
        if unknown:
            raise ValueError(
                f"{source}: unknown case field(s): {', '.join(sorted(unknown))}. "
                f"Known fields: {', '.join(names)}"
            )

        kwargs = {
            "name": str(data["name"]).strip(),
            "stl_path": _resolve(data["stl_path"], base),
            "avl_exe": _resolve(data["avl_exe"], base),
        }
        for key, default in STL_TO_AVL_DEFAULTS.items():
            raw = data.get(key, default)
            kwargs[key] = _coerce(key, raw, type(default), source)
        s_wet = data.get("s_wet_override_m2")
        kwargs["s_wet_override_m2"] = (
            None if s_wet is None else _coerce("s_wet_override_m2", s_wet, float, source)
        )
        tank_over = data.get("tank_system_mass_override_kg")
        kwargs["tank_system_mass_override_kg"] = (
            None if tank_over is None
            else _coerce("tank_system_mass_override_kg", tank_over, float, source))
        concept = data.get("concept")
        kwargs["concept"] = None if concept is None else str(concept).strip()
        iteration = data.get("iteration")
        kwargs["iteration"] = (
            None if iteration is None else _coerce("iteration", iteration, int, source)
        )

        cfg = cls(**kwargs)
        cfg.validate(source, require_files=require_files)
        return cfg

    def validate(self, source: str = "<config>", require_files: bool = True) -> None:
        if not self.name:
            raise ValueError(f"{source}: name must not be empty")
        if any(ch in self.name for ch in '\\/:*?"<>|'):
            raise ValueError(
                f"{source}: name {self.name!r} contains a path separator or reserved "
                "character — it is used as an output directory name"
            )
        if self.units not in UNIT_SCALE:
            raise ValueError(
                f"{source}: units must be one of {', '.join(UNIT_SCALE)}, got {self.units!r}"
            )
        _check_axes(self.axes, source)
        if self.n_sections < 2:
            raise ValueError(f"{source}: n_sections must be >= 2, got {self.n_sections}")
        if self.nchord < 1:
            raise ValueError(f"{source}: nchord must be >= 1, got {self.nchord}")
        if self.cluster <= 0.0:
            raise ValueError(f"{source}: cluster must be > 0 (1.0 = uniform), got {self.cluster}")
        if self.mach < 0.0 or self.mach >= 1.0:
            raise ValueError(
                f"{source}: mach must be in [0, 1) — AVL's Prandtl-Glauert correction is "
                f"subsonic only, got {self.mach}"
            )
        if (self.tank_system_mass_override_kg is not None
                and self.tank_system_mass_override_kg < 0.0):
            raise ValueError(
                f"{source}: tank_system_mass_override_kg must be >= 0 when given, "
                f"got {self.tank_system_mass_override_kg}")
        if self.s_wet_override_m2 is not None and self.s_wet_override_m2 <= 0.0:
            raise ValueError(
                f"{source}: s_wet_override_m2 must be > 0 when given, got {self.s_wet_override_m2}"
            )
        if require_files:
            if not self.stl_path.is_file():
                raise FileNotFoundError(f"{source}: stl_path does not exist: {self.stl_path}")
            if not self.avl_exe.is_file():
                raise FileNotFoundError(f"{source}: avl_exe does not exist: {self.avl_exe}")

    @property
    def scale(self) -> float:
        """Multiplier converting the STL's units to metres."""
        return UNIT_SCALE[self.units]

    def concept_and_iteration(self) -> tuple[str, int | None]:
        """Labels for the log entry header, derived from `name` if not given.

        "iteration_4" -> ("iteration", 4); "P1_rev2" -> ("P1_rev", 2);
        a name with no trailing number keeps the whole name and yields None.
        """
        concept, iteration = self.concept, self.iteration
        if concept is None or iteration is None:
            m = re.match(r"^(.*?)[_\-]?(\d+)$", self.name)
            if concept is None:
                concept = (m.group(1) or self.name) if m else self.name
            if iteration is None and m:
                iteration = int(m.group(2))
        return concept, iteration

    def extract_kwargs(self) -> dict:
        """Arguments for stl_to_avl.extract()."""
        return {
            "stl_path": str(self.stl_path),
            "n_sections": self.n_sections,
            "scale": self.scale,
            "cluster": self.cluster,
            "axes": self.axes,
        }

    def write_avl_kwargs(self) -> dict:
        """Arguments for stl_to_avl.write_avl() that come from this config."""
        return {"mach": self.mach, "nchord": self.nchord, "cspace": self.cspace}

    def to_dict(self) -> dict:
        """JSON-serialisable form, for embedding in results.json."""
        d = asdict(self)
        for key in ("stl_path", "avl_exe", "source_path"):
            d[key] = None if d[key] is None else str(d[key])
        return d


def _resolve(value, base: Path) -> Path:
    p = Path(str(value)).expanduser()
    return p.resolve() if p.is_absolute() else (base / p).resolve()


def _coerce(key: str, raw, target, source: str):
    if target is str:
        return str(raw).strip()
    if isinstance(raw, bool):
        raise ValueError(f"{source}: field {key!r} must be a number, got {raw!r}")
    try:
        if target is int:
            as_float = float(raw)
            if as_float != int(as_float):
                raise ValueError
            return int(as_float)
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"{source}: field {key!r} must be {'an integer' if target is int else 'a number'}, "
            f"got {raw!r}"
        ) from None


def _check_axes(spec: str, source: str) -> None:
    """Syntax check on stl_to_avl's --axes spec.

    Full validation of the one-letter short form needs the mesh extents, so it
    happens inside stl_to_avl.parse_axes(). This catches typos at config-load
    time instead of after the STL has been read.
    """
    tokens, i = [], 0
    while i < len(spec):
        if spec[i] in "+-":
            i += 1
        if i >= len(spec) or spec[i] not in "xyz":
            raise ValueError(
                f"{source}: bad axes spec {spec!r}. Give one letter (the spanwise axis, "
                "e.g. 'z' or '-z') or all three in order streamwise, spanwise, up "
                "(e.g. 'xzy' or 'x-zy')."
            )
        tokens.append(spec[i])
        i += 1
    if len(tokens) == 1:
        return
    if len(tokens) != 3 or sorted(tokens) != ["x", "y", "z"]:
        raise ValueError(
            f"{source}: bad axes spec {spec!r}; the three-axis form needs each of x, y, z once"
        )

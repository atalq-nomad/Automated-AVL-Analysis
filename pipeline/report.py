"""Stage 6 — the auto-written log entry.

The block format below is the one used by hand for every iteration; it is
reproduced exactly rather than improved on, so entries written tonight and
entries written by this pipeline read as one continuous log.

The Conclusion line is the only generated prose. It carries L/D and range, and
then any flags worth a human's attention: a large move in e or Cma against the
previous iteration, and any LEFIND warnings from the AVL log. That last part is
the automated version of the "worth checking before trusting" notes that were
being added by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

# A parameter moving more than this against the previous iteration gets called
# out. Matches the threshold Stage 7's comparison table uses.
FLAG_PCT = 15.0

RUNNING_LOG = "running_log.md"


def pct_change(new: float, old: float) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def find_previous_results(base_dir, current_run_dir=None) -> dict | None:
    """The most recent results.json before `current_run_dir`, or None.

    Ordered by the timestamp recorded inside each file rather than by mtime, so
    re-reading an old run does not reorder history.
    """
    root = Path(base_dir) / "outputs"
    if not root.is_dir():
        return None
    current = Path(current_run_dir).resolve() if current_run_dir else None

    found = []
    for path in root.glob("*/*/results.json"):
        if current is not None and path.parent.resolve() == current:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("timestamp"):
            found.append((data["timestamp"], data))
    if not found:
        return None
    found.sort(key=lambda pair: pair[0])
    return found[-1][1]


def build_conclusion(results: dict, previous: dict | None) -> str:
    """One line: the headline numbers, then anything a human should check."""
    aero = results["aero"]
    rng = results["range"]
    sizing = results.get("sizing") or {}
    reserve = sizing.get("reserve_range") or {}

    # Lead with the reserve range when it exists — it is the number to trust.
    # The quick estimate is still quoted, explicitly labelled, so entries
    # written before Stage 10 stay comparable with entries written after.
    if reserve.get("trip_range_nm") is not None:
        parts = [
            f"L/D {aero['l_over_d']:.2f} at CL {aero['CLtot']:.4f}; "
            f"reserve-based range {reserve['trip_range_nm']:.0f} nm "
            f"(quick no-reserve estimate {rng['range_nm']:.0f} nm, "
            f"{(rng['range_nm'] - reserve['trip_range_nm']) / rng['range_nm'] * 100:.0f}% "
            "optimistic)."
        ]
    else:
        parts = [
            f"L/D {aero['l_over_d']:.2f} at CL {aero['CLtot']:.4f}, "
            f"range ~{rng['range_nm']:.0f} nm (quick no-reserve estimate)."
        ]

    flags: list[str] = []
    if previous is not None:
        prev_name = previous.get("name", "previous")
        de = pct_change(aero["e"], previous.get("aero", {}).get("e"))
        if de is not None and abs(de) > FLAG_PCT:
            flags.append(
                f"Oswald e moved {de:+.0f}% vs {prev_name} "
                f"({previous['aero']['e']:.4f} -> {aero['e']:.4f}) — check spanwise "
                "loading/twist before reading anything into the L/D change"
            )
        dcma = pct_change(results["stability"]["Cma"],
                           previous.get("stability", {}).get("Cma"))
        if dcma is not None and abs(dcma) > FLAG_PCT:
            flags.append(
                f"Cma moved {dcma:+.0f}% vs {prev_name} "
                f"({previous['stability']['Cma']:+.4f} -> "
                f"{results['stability']['Cma']:+.4f}/rad)"
            )

    gate = sizing.get("gate") or {}
    if gate:
        if not gate.get("verdict_decided", True):
            flags.append(
                f"MTOM {gate['converged_mtom_kg']:.0f} kg vs {gate['cap_kg']:.0f} kg cap is "
                f"nominally {gate['nominal_verdict']} by {abs(gate['margin_kg']):.0f} kg "
                f"({abs(gate['margin_pct']):.2f}%), but the verdict is UNDECIDED — it flips "
                f"on {'; '.join(gate['flips_on'])}, both unsourced placeholders. Do not read "
                "this as a settled pass or fail"
            )
        elif not gate.get("gate_passed", True):
            flags.append(
                f"MTOM {gate['converged_mtom_kg']:.0f} kg EXCEEDS the {gate['cap_kg']:.0f} kg "
                f"cap by {abs(gate['margin_kg']):.0f} kg ({abs(gate['margin_pct']):.2f}%) — "
                "a real finding requiring redesign, not something to tune away"
            )
    if reserve.get("reserve_and_contingency_pct_of_loaded_fuel") is not None:
        flags.append(
            f"reserves + contingency consume "
            f"{reserve['reserve_and_contingency_pct_of_loaded_fuel']:.0f}% of loaded fuel"
        )
    if not results["stability"]["pitch_stable"]:
        flags.append(
            f"Cma = {results['stability']['Cma']:+.4f}/rad is NOT negative — "
            "this configuration is pitch-unstable"
        )
    if results["stability"]["spirally_stable"] is False:
        flags.append(
            f"spiral indicator {results['stability']['spiral']:.2f} < 1 — spirally divergent"
        )

    warnings = ((results.get("avl_log") or {}).get("warnings")) or []
    if warnings:
        where = ", ".join(sorted({w.get("section") or "unknown" for w in warnings}))
        flags.append(
            f"{len(warnings)} LEFIND warning(s) from AVL on {where} — leading edge not "
            "found; confirm those sections are genuinely sharp-nosed and not malformed "
            "before trusting these numbers"
        )

    if flags:
        parts.append(" Flags: " + "; ".join(flags) + ".")
    return "".join(parts)


def _sizing_lines(results: dict) -> str:
    """The Stage 12 block: converged MTOM, reserve range, and honest framing.

    A colleague reading this months from now must see the same caveats that
    were obvious when it was written — a bare MTOM with a PASS/FAIL beside it
    would read as far more settled than the inputs justify.
    """
    sizing = results.get("sizing") or {}
    if not sizing or sizing.get("error"):
        return ""

    gate = sizing.get("gate", {})
    mass = sizing.get("mass_model", {})
    reserve = sizing.get("reserve_range", {})
    groups = mass.get("groups_kg", {})
    order = sorted(groups.items(), key=lambda kv: -kv[1])

    lines = [
        f"MTOM closure: assumed {sizing['assumed_mtom_kg']:.0f} kg -> converged "
        f"{sizing['converged_mtom_kg']:.1f} kg "
        f"({sizing['mtom_delta_kg']:+.1f} kg). "
        + ", ".join(f"{k} {v:.0f}" for k, v in order)
        + f", payload {mass.get('payload_kg', 0):.0f}, crew {mass.get('crew_kg', 0):.0f}, "
          f"fuel {mass.get('fuel_kg', 0):.1f} kg.",
        "",
        f"MTOM gate: {gate.get('framing', 'not evaluated')}",
        "",
        f"Reserve profile: trip {reserve.get('trip_fuel_kg', 0):.1f} kg "
        f"({reserve.get('trip_fuel_pct_of_loaded_fuel', 0):.0f}% of loaded fuel), "
        f"reserves + contingency {reserve.get('reserve_and_contingency_kg', 0):.1f} kg "
        f"({reserve.get('reserve_and_contingency_pct_of_loaded_fuel', 0):.0f}%), "
        f"range {reserve.get('trip_range_km', 0):.0f} km / "
        f"{reserve.get('trip_range_nm', 0):.0f} nm.",
    ]

    sweep = sizing.get("sensitivity")
    if sweep:
        sigma = ", ".join(
            f"{r['sigma_kg_m2']:.0f}->{r['mtom_kg']:.0f}{'P' if r['gate_passed'] else 'F'}"
            for r in sweep["sigma_centerbody_kg_m2"])
        eta = ", ".join(
            f"{r['eta_g']:.2f}->{r['mtom_kg']:.0f}{'P' if r['gate_passed'] else 'F'}"
            for r in sweep["eta_g_tank"])
        lines += [
            "",
            "Placeholder sensitivity (MTOM kg, P=under cap / F=over). Both inputs are "
            "UNSOURCED — sigma has no citation at all, eta_g's midpoint is a choice:",
            f"  sigma kg/m²: {sigma}",
            f"  eta_g:       {eta}",
        ]
    return "\n".join(lines) + "\n"


def format_log_entry(results: dict, previous: dict | None = None) -> str:
    """The markdown block, in the exact format used for every iteration."""
    m = results["mission"]
    g = results["geometry"]
    a = results["aero"]
    s = results["stability"]
    r = results["range"]

    concept = results.get("concept") or results["name"]
    iteration = results.get("iteration")
    header = (f"**Concept {concept} — Iteration {iteration}**" if iteration is not None
              else f"**Concept {concept}**")

    return "\n".join([
        header,
        "",
        f"Assumptions: {m['cruise_altitude_m']:.0f} m, M{m['cruise_mach']:.2f}, "
        f"W = {m['mtom_kg']:.0f} kg (MTOM), Cfe {m['cfe']:.4f}, "
        f"tank vol {m['tank_volume_m3']:.1f} m³, "
        f"LH2 fill {m['lh2_fill_fraction'] * 100:.0f}%, "
        f"TSFC {m['tsfc_kg_per_Ns']:.3g} kg/(N·s)",
        "",
        f"Geometry: Sref {g['sref_m2']:.3f} m², AR {g['ar']:.3f}, "
        f"S_wet {g['s_wet_m2']:.1f} m²",
        "",
        f"Aero: CL {a['CLtot']:.5f}, alpha {a['alpha_deg']:.3f}°, e {a['e']:.4f}, "
        f"CD0 {a['cd0']:.5f}, CD_total {a['cd_total']:.5f}, L/D {a['l_over_d']:.2f}, "
        f"Cma {s['Cma']:+.4f}/rad, static margin ~{s['static_margin_pct']:.1f}% MAC",
        "",
        f"Mass/Range: fuel {r['fuel_mass_kg']:.1f} kg, Wf {r['wf_kg']:.1f} kg, "
        f"ln(Wi/Wf) {r['ln_wi_wf']:.5f}, "
        f"Range ≈ {r['range_km']:.0f} km / {r['range_nm']:.0f} nm (quick, no reserves)",
        "",
        _sizing_lines(results),
        f"Conclusion: {build_conclusion(results, previous)}",
        "",
    ])


def write_log_entry(results: dict, path, previous: dict | None = None) -> str:
    text = format_log_entry(results, previous)
    Path(path).write_text(text, encoding="utf-8")
    return text


def append_running_log(text: str, base_dir, filename: str = RUNNING_LOG) -> Path:
    """Append one entry to the cumulative log, newest last."""
    path = Path(base_dir) / filename
    if not path.exists():
        path.write_text(
            "# LH₂ BWB running log\n\n"
            "Auto-appended by `pipeline/run_case.py`, newest entry last.\n\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as f:
        f.write("\n---\n\n")
        f.write(text if text.endswith("\n") else text + "\n")
    return path


__all__ = [
    "FLAG_PCT",
    "append_running_log",
    "build_conclusion",
    "find_previous_results",
    "format_log_entry",
    "pct_change",
    "write_log_entry",
]

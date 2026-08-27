"""Stage 7 — cross-iteration comparison.

Scans each case's latest run for a results.json and prints one markdown table,
so a jump between iterations gets called out automatically instead of needing
someone to eyeball it across log entries.

Shares its trend logic with Stage 6 rather than restating it: FLAG_PCT and
pct_change come from report.py, so the threshold that flags a row here is the
same one that writes a flag into a log entry's Conclusion line. Change it in
one place and both move together.

Runs are ordered by the timestamp inside each results.json, and each row is
compared against the row before it, so "previous iteration" means chronological
predecessor rather than whatever the filesystem happened to list first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # invoked as `python pipeline/compare.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.paths import outputs_root, resolve_latest  # noqa: E402
from pipeline.report import FLAG_PCT, pct_change  # noqa: E402

# (json path, column header, format). Every one is trend-checked.
COLUMNS = [
    (("geometry", "sref_m2"), "Sref m²", "{:.3f}"),
    (("geometry", "ar"), "AR", "{:.3f}"),
    (("geometry", "s_wet_m2"), "S_wet m²", "{:.1f}"),
    (("aero", "CLtot"), "CL", "{:.5f}"),
    (("aero", "alpha_deg"), "alpha°", "{:.3f}"),
    (("aero", "e"), "e", "{:.4f}"),
    (("aero", "l_over_d"), "L/D", "{:.2f}"),
    (("stability", "static_margin_pct"), "SM %MAC", "{:+.2f}"),
    (("range", "range_nm"), "Range nm", "{:.0f}"),
]

# Short labels for the Flags column.
FLAG_LABELS = {
    "sref_m2": "Sref", "ar": "AR", "s_wet_m2": "S_wet", "CLtot": "CL",
    "alpha_deg": "alpha", "e": "e", "l_over_d": "L/D",
    "static_margin_pct": "SM", "range_nm": "range",
}


def _dig(data: dict, path: tuple):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def collect_results(base_dir=None) -> list[dict]:
    """One results.json per case, from that case's latest run, oldest first.

    A case whose latest run has no results.json is skipped: that is a run which
    failed its geometry gate or log validation, and those never produce one.
    """
    root = outputs_root(base_dir)
    if not root.is_dir():
        return []

    found = []
    for case_dir in sorted(root.iterdir(), key=lambda p: p.name):
        if not case_dir.is_dir():
            continue
        latest = resolve_latest(case_dir.name, base_dir=base_dir)
        if latest is None:
            continue
        path = latest / "results.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! skipping unreadable {path}: {exc}", file=sys.stderr)
            continue
        data["_path"] = str(path)
        found.append(data)

    found.sort(key=lambda d: d.get("timestamp") or "")
    return found


def row_flags(current: dict, previous: dict | None, threshold: float = FLAG_PCT) -> list[str]:
    """Every tracked parameter that moved more than `threshold` percent."""
    if previous is None:
        return []
    out = []
    for path, _header, _fmt in COLUMNS:
        change = pct_change(_dig(current, path), _dig(previous, path))
        if change is not None and abs(change) > threshold:
            out.append(f"{FLAG_LABELS.get(path[-1], path[-1])} {change:+.0f}%")
    return out


def build_rows(results: list[dict], threshold: float = FLAG_PCT) -> list[dict]:
    rows = []
    for i, data in enumerate(results):
        previous = results[i - 1] if i else None
        rows.append({
            "name": data.get("name", "?"),
            "timestamp": data.get("timestamp", ""),
            "values": [_dig(data, path) for path, _h, _f in COLUMNS],
            "flags": row_flags(data, previous, threshold),
            "lefind": len(((data.get("avl_log") or {}).get("warnings")) or []),
            "pitch_stable": _dig(data, ("stability", "pitch_stable")),
            "path": data.get("_path", ""),
        })
    return rows


def format_table(rows: list[dict], threshold: float = FLAG_PCT) -> str:
    headers = ["Iteration"] + [h for _p, h, _f in COLUMNS] + ["Flags"]
    body = []
    for row in rows:
        cells = [row["name"]]
        for (path, _h, fmt), value in zip(COLUMNS, row["values"]):
            cells.append("—" if value is None else fmt.format(value))
        notes = list(row["flags"])
        if row["pitch_stable"] is False:
            notes.append("**Cma>0 UNSTABLE**")
        if row["lefind"]:
            notes.append(f"{row['lefind']}×LEFIND")
        cells.append(", ".join(notes) if notes else "")
        body.append(cells)

    widths = [max(len(headers[i]), *(len(r[i]) for r in body)) if body else len(headers[i])
              for i in range(len(headers))]
    align = ["---" if i == 0 or i == len(headers) - 1 else "---:"
             for i in range(len(headers))]

    def line(cells):
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    out = [line(headers), "| " + " | ".join(a.ljust(w) for a, w in zip(align, widths)) + " |"]
    out += [line(r) for r in body]

    out.append("")
    out.append(f"Flags mark a parameter that moved more than {threshold:.0f}% "
               "from the previous iteration (chronological order).")
    if not rows:
        out.append("")
        out.append("No results.json found. Runs that fail the geometry gate or log "
                   "validation never produce one.")
    return "\n".join(out)


def compare(base_dir=None, threshold: float = FLAG_PCT) -> str:
    return format_table(build_rows(collect_results(base_dir), threshold), threshold)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare iterations across outputs/.")
    p.add_argument("--base-dir", default=None,
                   help="project root holding outputs/ (default: current directory)")
    p.add_argument("--threshold", type=float, default=FLAG_PCT,
                   help=f"percent move that earns a flag (default {FLAG_PCT:.0f})")
    p.add_argument("--output", default=None,
                   help="also write the table to this file, e.g. comparison.md")
    a = p.parse_args(argv)

    base = Path(a.base_dir) if a.base_dir else Path.cwd()
    rows = build_rows(collect_results(base), a.threshold)
    table = format_table(rows, a.threshold)

    print(f"\n# Iteration comparison — {len(rows)} case(s) with results\n")
    print(table)
    if a.output:
        Path(a.output).write_text(
            f"# Iteration comparison\n\n{table}\n", encoding="utf-8")
        print(f"\nwrote {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

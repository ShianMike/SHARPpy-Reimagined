"""Summarize actual Windows process samples and displayed PresentMon frames."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics


def memory_report(samples):
    start = datetime.fromisoformat(samples[0]["utc"])
    rows = [{"seconds": (datetime.fromisoformat(sample["utc"]) - start).total_seconds(),
             "private_mib": sum(p["private"] for p in sample["processes"]) / 2**20,
             "working_mib": sum(p["working"] for p in sample["processes"]) / 2**20,
             "handles": sum(p["handles"] for p in sample["processes"]),
             "processes": len(sample["processes"])} for sample in samples]
    if len(rows) < 200 or rows[-1]["seconds"] < 7200 or any(row["processes"] == 0 for row in rows):
        raise ValueError("A full two-hour native process sample sequence is required")
    warm = [row for row in rows if row["seconds"] >= 1800]
    quarter = max(1, len(warm) // 4)
    first = statistics.median(row["private_mib"] for row in warm[:quarter])
    last = statistics.median(row["private_mib"] for row in warm[-quarter:])
    slope, _ = statistics.linear_regression([row["seconds"] / 3600 for row in warm], [row["private_mib"] for row in warm])
    return {"samples": len(rows), "seconds": rows[-1]["seconds"], "warmup_seconds": 1800,
            "peak_private_mib": max(row["private_mib"] for row in rows),
            "peak_working_mib": max(row["working_mib"] for row in rows),
            "post_warmup_first_quarter_median_private_mib": first,
            "post_warmup_last_quarter_median_private_mib": last,
            "post_warmup_growth_mib": last - first, "post_warmup_private_slope_mib_per_hour": slope,
            "post_warmup_first_handles": statistics.median(row["handles"] for row in warm[:quarter]),
            "post_warmup_last_handles": statistics.median(row["handles"] for row in warm[-quarter:]),
            "scope": "Sum of private process bytes is committed memory. Working-set sums can count shared pages more than once. Review trend against this finite workload; no arbitrary memory cap is imposed."}


def presentation_report(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"ProcessID", "SwapChainAddress", "DisplayedTime", "CPUStartTime"} <= set(reader.fieldnames or []):
            raise ValueError(f"Missing PresentMon display metrics: {path}")
        groups = {}
        for row in reader:
            groups.setdefault((row["ProcessID"], row["SwapChainAddress"]), []).append(row)
    displayed = lambda row: row["DisplayedTime"] not in ("NA", "", None) and math.isfinite(float(row["DisplayedTime"])) and float(row["DisplayedTime"]) > 0
    if not groups:
        raise ValueError(f"No native presentation events: {path}")
    key, rows = max(groups.items(), key=lambda entry: sum(displayed(row) for row in entry[1]))
    durations = sorted(float(row["DisplayedTime"]) for row in rows if displayed(row))
    span = (float(rows[-1]["CPUStartTime"]) - float(rows[0]["CPUStartTime"])) / 1000
    if len(durations) < 100 or span < 30:
        raise ValueError(f"Insufficient displayed native frames: {path}")
    return {"file": path.name, "pid": key[0], "swapchain": key[1], "presented_rows": len(rows),
            "displayed_frames": len(durations), "not_displayed_rows": len(rows) - len(durations),
            "capture_span_seconds": span, "displayed_frames_per_second": len(durations) / span,
            "display_duration_median_ms": statistics.median(durations),
            "display_duration_p95_ms": durations[math.ceil(.95 * len(durations)) - 1],
            "display_duration_max_ms": max(durations), "display_durations_over_33_34_ms": sum(value > 33.34 for value in durations),
            "modes": sorted({row.get("PresentMode", "") for row in rows}),
            "scope": "Actual displayed frames of the most active native swapchain during a mixed interactive workload. Idle periods are included; this is not a universal hardware FPS or a before/after speedup."}


def report(root):
    complete = json.loads((root / "soak-complete.json").read_text(encoding="utf-8"))
    if complete["soak"]["elapsedSeconds"] < 7200 or complete["soak"]["errors"]:
        raise ValueError("Native workload did not complete two healthy hours")
    samples = [json.loads(line) for line in (root / "processes.ndjson").read_text(encoding="utf-8-sig").splitlines()]
    result = {"memory": memory_report(samples), "presentation": [presentation_report(root / f"presentation-{index}.csv") for index in range(2)],
              "workload": complete["soak"], "graphics": json.loads((root / "graphics.json").read_text(encoding="utf-8-sig"))}
    (root / "native-summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(report(parser.parse_args().directory), indent=2))

"""Summarize actual displayed PresentMon frames from one native capture."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    print(json.dumps(presentation_report(parser.parse_args().capture), indent=2))

"""Native acceptance cannot substitute present calls or short runs for evidence."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy

import pytest

REPORT = runpy.run_path(str(Path(__file__).resolve().parents[2] / "packaging/report_native_acceptance.py"))


def test_native_memory_summary_rejects_short_runs_and_measures_real_growth():
    start = datetime(2026, 10, 20, tzinfo=timezone.utc)
    samples = [{"utc": (start + timedelta(seconds=index * 30)).isoformat(),
                "processes": [{"private": (100 + index) * 2**20, "working": 400 * 2**20, "handles": 20}]}
               for index in range(241)]
    result = REPORT["memory_report"](samples)
    assert result["seconds"] == 7200
    assert result["post_warmup_private_slope_mib_per_hour"] == pytest.approx(120)
    assert result["post_warmup_growth_mib"] > 100
    with pytest.raises(ValueError, match="two-hour"):
        REPORT["memory_report"](samples[:200])


def test_presentation_requires_displayed_frames_not_just_present_calls(tmp_path):
    path = tmp_path / "capture.csv"
    fields = ["ProcessID", "SwapChainAddress", "CPUStartTime", "DisplayedTime"]

    def write(duration):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for index in range(2400):
                writer.writerow(dict(zip(fields, ["42", "0x01", index * (1000 / 60), duration])))

    write("NA")
    with pytest.raises(ValueError, match="Insufficient displayed"):
        REPORT["presentation_report"](path)
    write(1000 / 60)
    result = REPORT["presentation_report"](path)
    assert result["displayed_frames"] == 2400
    assert result["displayed_frames_per_second"] == pytest.approx(60, rel=.001)

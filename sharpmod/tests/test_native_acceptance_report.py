"""Native presentation evidence requires actual displayed frames."""
import csv
from pathlib import Path
import runpy

import pytest

REPORT = runpy.run_path(str(Path(__file__).resolve().parents[2] / "packaging/report_native_acceptance.py"))


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

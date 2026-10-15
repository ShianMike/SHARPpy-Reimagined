"""Regenerate the sounding contract fixtures from the real bridge handlers.

Writes, under ``src-tauri/contracts/``:

* ``bridge/soundings/*.json``: bridge results (snake_case), exactly as the
  handlers return them for ``examples/soundings/hrrr_point_36.68N_95.66W_f018.npz``;
* ``public/soundings/*.json``: the same payloads in the webview's camelCase;
* ``public/soundings/render.example.png``: the sounding image, scaled to half
  size, which the browser fixture transport serves.

The clock and the sounding id are pinned, so the output is deterministic.
Run from the repository root with the test lane's own interpreter, e.g.
``.gribenv/Scripts/python.exe scripts/export_sounding_fixtures.py``. The
diagnostics are floating-point work: Python 3.11 and 3.14 disagree in the
last digits, so fixtures written by another interpreter fail the exact
comparisons in ``test_bridge_ensemble`` and ``test_bridge_thresholds``.

The ensemble fixture replaces the network at the member download (as the map
fixtures do at the provider): one stand-in ``extract`` writes the example
sounding back with a fixed per-member offset, and one member fails to
download. Everything downstream -- decode, combine, ledger, diagnostics -- is
the real code.
"""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "src-tauri" / "contracts"
EXAMPLE = ROOT / "examples" / "soundings" / "hrrr_point_36.68N_95.66W_f018.npz"
#: A 49-time BUFKIT forecast file, for the trends fixture.
TREND_EXAMPLE = ROOT / "examples" / "soundings" / "hrrr_kbvo_20260625_06z.buf"
FIXED_NOW = datetime(2026, 10, 4, 18, 30, tzinfo=UTC)
FIXED_ID = "0123456789abcdef"
TREND_ID = "fedcba9876543210"


def _upload(path: Path) -> dict[str, str]:
    return {
        "file_name": path.name,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def camel(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            re.sub(r"_([a-z])", lambda m: m.group(1).upper(), k): camel(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [camel(v) for v in value]
    return value


def write(relative: str, payload: Any) -> None:
    path = CONTRACTS / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("wrote", path.relative_to(ROOT))


#: The ensemble fixture request, and the fixed offset (degrees) each loaded
#: member's profile carries so the spreads are real numbers.
ENSEMBLE_MEMBERS = ("c00", "p01", "p02", "p03")
ENSEMBLE_OFFSETS = {"c00": 0.0, "p01": 0.7, "p02": 1.4}
ENSEMBLE_REQUEST = {
    "model": "gefs",
    "latitude": 36.68,
    "longitude": -95.66,
    "forecast_hour": 12,
    "run_time": "2026-06-25T12:00:00Z",
    "location": "OUN",
    "members": list(ENSEMBLE_MEMBERS),
}

#: The threshold fixture rule: one joint definition whose two conditions
#: separate the stand-in members into qualifying and nonqualifying, leaving the
#: failed member as its own ledger row.
THRESHOLD_DEFINITION = {
    "name": "Warm-core shear",
    "conditions": [
        {"metric": "mlcape", "operator": ">=", "value": 2350.0},
        {"metric": "shear_6km", "operator": "between", "value": 20.0, "upper": 60.0},
    ],
}


def stand_in_extract(example: Path):
    """A deterministic member download: the example sounding, shifted."""
    import numpy as np

    from sharpmod.tools.era5_extract import RetrievalError

    base = dict(np.load(example, allow_pickle=False))

    def extract(_model, _lat, _lon, run_time=None, fxx=0, out_path=None, loc=None,
                member=None, **_):
        label = str(member)
        if label not in ENSEMBLE_OFFSETS:
            raise RetrievalError(f"no GRIB found for member {label}")
        shifted = dict(base)
        shifted["tmpc"] = base["tmpc"] + ENSEMBLE_OFFSETS[label]
        np.savez(out_path, **shifted)
        return out_path

    return extract


def main() -> int:
    from sharpmod.bridge import params, sounding_cache, soundings

    with tempfile.TemporaryDirectory() as tmp:
        sounding_cache.reset_cache(Path(tmp) / "soundings")
        params.now = lambda: FIXED_NOW  # pin the clock
        sounding_cache.SoundingCache.new_id = staticmethod(lambda: FIXED_ID)
        catalog = soundings.models_catalog({})
        summary = soundings.open_file(_upload(EXAMPLE))
        analysis = soundings.analysis({"sounding_id": summary["sounding_id"]})
        image = soundings.render({"sounding_id": summary["sounding_id"]})
        sounding_cache.SoundingCache.new_id = staticmethod(lambda: TREND_ID)
        trend_summary = soundings.open_file(_upload(TREND_EXAMPLE))
        trend = soundings.trends({"sounding_id": trend_summary["sounding_id"]})
        from sharpmod.bridge import ensemble, thresholds

        with patch("sharpmod.tools.model_extract.extract", stand_in_extract(EXAMPLE)):
            members = ensemble.summary(dict(ENSEMBLE_REQUEST))
            threshold_request = dict(ENSEMBLE_REQUEST)
            threshold_request["definition"] = dict(THRESHOLD_DEFINITION)
            threshold = thresholds.evaluate(dict(threshold_request))
            threshold_csv = thresholds.export_csv(dict(threshold_request))
            threshold_metrics = thresholds.metrics({})
        from sharpmod.bridge.render_client import client

        client().close()
        sounding_cache.reset_cache(None)
    write("bridge/soundings/models.catalog.result.json", catalog)
    write("bridge/soundings/open.result.example.json", summary)
    write("bridge/soundings/analysis.result.example.json", analysis)
    write("public/soundings/models.catalog.json", camel(catalog))
    write("public/soundings/summary.example.json", camel(summary))
    write("public/soundings/analysis.example.json", camel(analysis))
    write("bridge/soundings/open.result.multi-time.json", trend_summary)
    write("bridge/soundings/trends.result.example.json", trend)
    write("public/soundings/summary.multi-time.json", camel(trend_summary))
    write("public/soundings/trends.example.json", camel(trend))
    write("bridge/soundings/ensemble.result.example.json", members)
    write("public/soundings/ensemble.example.json", camel(members))
    write("bridge/soundings/threshold.result.example.json", threshold)
    write("bridge/soundings/threshold-metrics.result.json", threshold_metrics)
    write("bridge/soundings/threshold-csv.result.example.json", threshold_csv)
    write("public/soundings/threshold.example.json", camel(threshold))
    write("public/soundings/threshold-metrics.json", camel(threshold_metrics))
    write("public/soundings/threshold-csv.example.json", camel(threshold_csv))
    from PIL import Image

    picture = Image.open(io.BytesIO(base64.b64decode(image["png_base64"])))
    half = picture.resize((picture.width // 2, picture.height // 2), Image.LANCZOS)
    out = CONTRACTS / "public" / "soundings" / "render.example.png"
    half.save(out, format="PNG", optimize=True)
    print("wrote", out.relative_to(ROOT), half.size, out.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

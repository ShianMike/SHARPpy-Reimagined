"""Measure the real offscreen sounding renderer, retaining correctness evidence.

Run once per mode/backend in a fresh process. First-render samples include Qt
initialization; subsequent samples reuse imports, just like the bridge worker.
Profiling is separate from the elapsed samples. No network input is accepted.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def _memory() -> dict[str, int]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (key, ctypes.c_size_t) for key in (
                    "peak_working_set_bytes", "working_set_bytes", "peak_paged_pool",
                    "paged_pool", "peak_nonpaged_pool", "nonpaged_pool", "pagefile",
                    "peak_pagefile", "private_bytes",
                )
            ]

        value = Counters()
        value.size = ctypes.sizeof(value)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        query = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not query(kernel.GetCurrentProcess(), ctypes.byref(value), value.size):
            raise ctypes.WinError(ctypes.get_last_error())
        return {key: getattr(value, key) for key in (
            "peak_working_set_bytes", "working_set_bytes", "private_bytes")}
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"peak_working_set_bytes": int(peak * (1 if sys.platform == "darwin" else 1024))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=ROOT / "examples/soundings/hrrr_point_36.68N_95.66W_f018.npz")
    parser.add_argument("--mode", choices=("hd", "uhd", "lossless"), default="hd")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--profile", action="store_true", help="profile one additional warm render")
    args = parser.parse_args()
    if args.repeat < 2:
        parser.error("--repeat must be at least 2 to distinguish first and warm renders")
    source = args.input.resolve()
    if not source.is_file():
        parser.error("--input must be an existing local sounding")
    output = args.output_json.resolve()
    if output == source:
        parser.error("--output-json must not overwrite the input sounding")
    output.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("SHARPMOD_GEOCODER_URL", "off")

    started, cpu = time.perf_counter(), time.process_time()
    from sharpmod.backends import backend_info
    from sharpmod.rendering import cli
    from qtpy import QT_VERSION
    from qtpy.QtGui import QImage
    import numpy as np

    import_s, import_cpu_s = time.perf_counter() - started, time.process_time() - cpu
    assert Path(cli.__file__).resolve().is_relative_to(ROOT), "wrong renderer checkout"
    data = {"source_root": str(ROOT), "source_module": cli.__file__, "python": sys.version,
            "platform": platform.platform(), "numpy": np.__version__, "qt": QT_VERSION,
            "logical_cpus": os.cpu_count(), "backend": backend_info(),
            "input": str(source), "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "mode": args.mode, "import_s": import_s, "import_cpu_s": import_cpu_s,
            "environment": {key: value for key, value in os.environ.items()
                            if key.startswith(("SHARPMOD_", "QT_", "RAYON_"))}, "samples": []}
    with tempfile.TemporaryDirectory(prefix="sharpmod-render-bench-") as directory:
        image_path = Path(directory) / "chart.png"
        for index in range(args.repeat):
            started, cpu = time.perf_counter(), time.process_time()
            cli.render(str(source), str(image_path), image_mode=args.mode, show_locator=False)
            elapsed, cpu_s = time.perf_counter() - started, time.process_time() - cpu
            memory = _memory()
            image = QImage(str(image_path))
            assert not image.isNull(), "renderer produced an undecodable PNG"
            assert image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
            pixels = hashlib.sha256(rgba.constBits()).hexdigest()
            row = {"index": index, "elapsed_s": elapsed, "cpu_s": cpu_s,
                   "width": image.width(), "height": image.height(),
                   "png_bytes": image_path.stat().st_size, "pixels_sha256": pixels, **memory}
            del image, rgba
            if data["samples"]:
                assert pixels == data["samples"][0]["pixels_sha256"], "render pixels changed across repeats"
            data["samples"].append(row)
            output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(row), flush=True)
        data["warm_median_s"] = statistics.median(row["elapsed_s"] for row in data["samples"][1:])
        output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if args.profile:
            profiler = cProfile.Profile()
            profiler.runcall(cli.render, str(source), str(image_path),
                            image_mode=args.mode, show_locator=False)
            profiler.dump_stats(str(output.with_suffix(".prof")))
            with output.with_suffix(".profile.txt").open("w", encoding="utf-8") as stream:
                pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

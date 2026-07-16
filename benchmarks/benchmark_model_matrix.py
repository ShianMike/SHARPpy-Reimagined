"""Run the local-GRIB decoder benchmark for every enabled forecast model.

Network retrieval is deliberately outside this harness.  A manifest supplies
one complete local pressure-level subset (or an explicit unavailable reason)
for every model returned by ``model_extract.available_models()``.  Each model
runs in a fresh Python process so ecCodes global state and application caches
cannot leak between fixtures.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Sequence


_IMPLEMENTATIONS = (
    "old-python",
    "optimized-python",
    "old-rust",
    "optimized-rust",
)
_STAGES = (
    "application-cold",
    "warm-inventory-point-miss",
    "warm-dataset-point",
    "point-cache-hit",
    "profile-construction",
    "end-to-end",
)
_EXPECTED_STAGE_IMPLEMENTATIONS = {
    "application-cold": frozenset(_IMPLEMENTATIONS),
    "warm-inventory-point-miss": frozenset(
        ("old-python", "optimized-python", "old-rust")
    ),
    "warm-dataset-point": frozenset(("old-python", "old-rust")),
    "point-cache-hit": frozenset(("optimized-python", "optimized-rust")),
    "profile-construction": frozenset(_IMPLEMENTATIONS),
    "end-to-end": frozenset(_IMPLEMENTATIONS),
}


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read fixture manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("fixture manifest must contain one JSON object")
    schema_version = payload.get("schema_version", 1)
    if schema_version != 1:
        raise SystemExit(
            f"unsupported fixture manifest schema_version {schema_version!r}"
        )
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _enabled_models(checkout: Path) -> dict[str, str]:
    if not checkout.is_dir():
        raise SystemExit(f"--checkout is not a directory: {checkout}")
    text = str(checkout)
    if text not in sys.path:
        sys.path.insert(0, text)
    from sharpmod.tools.model_extract import available_models

    return {config.key: config.label for config in available_models()}


def _validated_rows(
    payload: dict[str, Any], manifest: Path, checkout: Path
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    enabled = _enabled_models(checkout)
    raw_rows = payload.get("fixtures", [])
    raw_unavailable = payload.get("unavailable", [])
    if not isinstance(raw_rows, list) or not isinstance(raw_unavailable, list):
        raise SystemExit("manifest fixtures/unavailable entries must be arrays")

    rows: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, dict[str, str]] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise SystemExit("every fixture entry must be an object")
        key = str(raw.get("model", "")).strip()
        if key in rows or key in unavailable:
            raise SystemExit(f"duplicate manifest entry for model {key!r}")
        if key not in enabled:
            raise SystemExit(f"fixture names non-enabled model {key!r}")
        try:
            latitude = float(raw["lat"])
            longitude = float(raw["lon"])
            source = Path(raw["path"]).expanduser()
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"invalid fixture entry for {key}: {exc}") from exc
        if not source.is_absolute():
            source = manifest.parent / source
        source = source.resolve()
        if not source.is_file():
            raise SystemExit(f"fixture for {key} does not exist: {source}")
        if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise SystemExit(
                f"fixture for {key} has invalid latitude {latitude!r}"
            )
        if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise SystemExit(
                f"fixture for {key} has invalid longitude {longitude!r}"
            )
        pressure_level_count = raw.get("pressure_level_count")
        if pressure_level_count is not None:
            try:
                expected_levels = int(pressure_level_count)
            except (TypeError, ValueError) as exc:
                raise SystemExit(
                    f"fixture for {key} has invalid pressure_level_count "
                    f"{pressure_level_count!r}"
                ) from exc
            if isinstance(pressure_level_count, bool) or expected_levels < 1:
                raise SystemExit(
                    f"fixture for {key} has invalid pressure_level_count "
                    f"{pressure_level_count!r}"
                )
            try:
                exact_count = float(pressure_level_count) == expected_levels
            except (TypeError, ValueError):
                exact_count = False
            if not exact_count:
                raise SystemExit(
                    f"fixture for {key} has non-integral pressure_level_count "
                    f"{pressure_level_count!r}"
                )
        row = dict(raw)
        row.update(
            model=key,
            label=str(raw.get("label") or enabled[key]),
            lat=latitude,
            lon=longitude,
            path=str(source),
        )
        if pressure_level_count is not None:
            row["pressure_level_count"] = expected_levels
        rows[key] = row

    for raw in raw_unavailable:
        if not isinstance(raw, dict):
            raise SystemExit("every unavailable entry must be an object")
        key = str(raw.get("model", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        if key in rows or key in unavailable:
            raise SystemExit(f"duplicate manifest entry for model {key!r}")
        if key not in enabled:
            raise SystemExit(f"unavailable entry names non-enabled model {key!r}")
        if not reason:
            raise SystemExit(f"unavailable entry for {key} needs a reason")
        unavailable[key] = {
            "model": key,
            "label": enabled[key],
            "reason": reason,
        }

    missing = sorted(set(enabled) - set(rows) - set(unavailable))
    if missing:
        raise SystemExit(
            "manifest must cover every enabled model; missing: "
            + ", ".join(missing)
        )
    ordered_rows = [rows[key] for key in enabled if key in rows]
    ordered_unavailable = [unavailable[key] for key in enabled if key in unavailable]
    return ordered_rows, ordered_unavailable


def _record_median_ms(result: dict[str, Any], implementation: str, stage: str):
    for record in result.get("records", []):
        if (
            record.get("implementation") == implementation
            and record.get("stage") == stage
        ):
            return float(record["median_seconds"]) * 1000.0
    return None


def _result_problems(
    result: dict[str, Any],
    implementations: Sequence[str],
    stages: Sequence[str],
    *,
    repeat: int,
    expected_levels: int | None,
) -> list[str]:
    """Reject incomplete rows before they can look like valid comparisons."""
    problems = []
    equivalence = result.get("equivalence", {})
    if equivalence.get("status") != "passed":
        problems.append(
            f"equivalence status is {equivalence.get('status', 'missing')!r}"
        )
    checked = set(equivalence.get("implementations", []))
    missing_checked = set(implementations) - checked
    if missing_checked:
        problems.append(
            "equivalence omitted " + ", ".join(sorted(missing_checked))
        )
    unexpected_checked = checked - set(implementations)
    if unexpected_checked:
        problems.append(
            "equivalence unexpectedly included "
            + ", ".join(sorted(unexpected_checked))
        )

    levels = equivalence.get("levels")
    if expected_levels is not None and levels != expected_levels:
        problems.append(
            f"decoded {levels!r} levels; manifest expects {expected_levels}"
        )

    raw_records = result.get("records", [])
    if not isinstance(raw_records, list):
        problems.append("result records must be an array")
        raw_records = []
    records_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in raw_records:
        if not isinstance(record, dict):
            problems.append("result contains a non-object timing record")
            continue
        pair = (str(record.get("implementation")), str(record.get("stage")))
        records_by_pair.setdefault(pair, []).append(record)

    selected = set(implementations)
    for stage in stages:
        expected = selected & _EXPECTED_STAGE_IMPLEMENTATIONS[stage]
        for implementation in sorted(expected):
            pair = (implementation, stage)
            matching = records_by_pair.get(pair, [])
            if len(matching) != 1:
                problems.append(
                    f"expected one {implementation}/{stage} timing; "
                    f"found {len(matching)}"
                )
                continue
            samples = matching[0].get("samples_seconds")
            if not isinstance(samples, list) or len(samples) != repeat:
                count = len(samples) if isinstance(samples, list) else "invalid"
                problems.append(
                    f"{implementation}/{stage} has {count} samples; "
                    f"expected {repeat}"
                )
                continue
            try:
                valid_samples = all(
                    math.isfinite(float(value)) and float(value) >= 0.0
                    for value in samples
                )
            except (TypeError, ValueError):
                valid_samples = False
            if not valid_samples:
                problems.append(
                    f"{implementation}/{stage} has invalid timing samples"
                )
    return problems


def _fmt_ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:,.3f}"


def _fmt_speedup(old: float | None, new: float | None) -> str:
    if old is None or new is None or new <= 0.0:
        return "N/A"
    return f"{old / new:.2f}x"


def _omega_status(equivalence: dict[str, Any]) -> str:
    omega = (
        equivalence.get("cross_generation", {})
        .get("optional_fields", {})
        .get("omeg", {})
    )
    status = omega.get("status")
    if status == "matched":
        return "matched"
    if status == "different":
        old_valid = omega.get("legacy_valid_levels", "?")
        new_valid = omega.get("optimized_valid_levels", "?")
        return f"different ({old_valid} -> {new_valid} valid)"
    return "N/A"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _environment_identity(result: dict[str, Any]) -> dict[str, Any]:
    """Return stable runtime/code fields that must not drift across models."""

    environment = result.get("environment", {})
    git = environment.get("git", {})
    return {
        "python_executable": environment.get("python_executable"),
        "python": environment.get("python"),
        "numpy": environment.get("numpy"),
        "cfgrib": environment.get("cfgrib"),
        "xarray": environment.get("xarray"),
        "eccodes_python": environment.get("eccodes_python"),
        "eccodes_library": environment.get("eccodes_library"),
        "source_fingerprints": environment.get("source_fingerprints"),
        "git_revision": git.get("revision"),
        "git_tracked_diff_sha256": git.get("tracked_diff_sha256"),
    }


def _markdown(payload: dict[str, Any]) -> str:
    has_application_cold = "application-cold" in payload.get(
        "settings", {}
    ).get("stages", [])
    lines = [
        "# All-model local GRIB decoding benchmark",
        "",
        "Network transfer is excluded. "
        + (
            "Times are application-cold medians; application caches and "
            "cfgrib indexes are cleared, while the operating-system file "
            "cache is not flushed."
            if has_application_cold
            else "Application-cold timing was not requested, so the timing "
            "columns below are N/A."
        ),
        "",
        "| Model | Production decode path | Levels old / optimized | "
        "Old/new omega | Old Python ms | Optimized Python ms | Python speedup | "
        "Old Rust hybrid ms | Optimized Rust ms | Rust speedup | "
        "Py/Rust optimized |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: |",
    ]
    for item in payload.get("models", []):
        result = item["result"]
        old_py = _record_median_ms(result, "old-python", "application-cold")
        new_py = _record_median_ms(result, "optimized-python", "application-cold")
        old_rs = _record_median_ms(result, "old-rust", "application-cold")
        new_rs = _record_median_ms(result, "optimized-rust", "application-cold")
        cross = (
            "N/A"
            if new_py is None or new_rs is None or new_rs <= 0.0
            else f"{new_py / new_rs:.3f}x"
        )
        equivalence = result.get("equivalence", {})
        generation_levels = equivalence.get("generation_levels", {})
        old_levels = generation_levels.get("old", "N/A")
        optimized_levels = generation_levels.get(
            "optimized", equivalence.get("levels", "N/A")
        )
        levels = f"{old_levels} / {optimized_levels}"
        omega = _omega_status(equivalence)
        lines.append(
            "| {label} | {path} | {levels} | {omega} | {old_py} | {new_py} | "
            "{py_speed} | {old_rs} | {new_rs} | {rs_speed} | {cross} |".format(
                label=_markdown_cell(item["fixture"]["label"]),
                path=_markdown_cell(
                    item["fixture"].get(
                        "production_decode_path", "not recorded"
                    )
                ),
                levels=levels,
                omega=omega,
                old_py=_fmt_ms(old_py),
                new_py=_fmt_ms(new_py),
                py_speed=_fmt_speedup(old_py, new_py),
                old_rs=_fmt_ms(old_rs),
                new_rs=_fmt_ms(new_rs),
                rs_speed=_fmt_speedup(old_rs, new_rs),
                cross=cross,
            )
        )
    for item in payload.get("unavailable", []):
        cells = [
            _markdown_cell(item["label"]),
            f"unavailable: {_markdown_cell(item['reason'])}",
            *(["N/A"] * 9),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    for item in payload.get("failures", []):
        cells = [
            _markdown_cell(item["label"]),
            "benchmark failed; see JSON",
            *(["N/A"] * 9),
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            f"Measured fixtures: {len(payload.get('models', []))}; explicitly "
            f"unavailable: {len(payload.get('unavailable', []))}; failed: "
            f"{len(payload.get('failures', []))}.",
            "",
            "`Old Rust hybrid` is the historical cfgrib/xarray decoder followed "
            "by native wind post-processing; the old extension did not decode GRIB. "
            "`Py/Rust optimized` is Python time divided by Rust time, so values "
            "above 1 mean Rust was faster.",
            "`Old/new omega` is diagnostic across generations. Python and Rust "
            "remain strict within each generation for all columns, including "
            "omega; old/new pressure, height, temperature, dewpoint, and wind "
            "columns are strict at every common pressure level.",
            "Production decode paths are manifest-declared metadata, not inferred "
            "by the benchmark driver.",
            "",
            "Complete fixture hashes, raw samples, equivalence output, "
            "environment metadata, and unavailable-stage reasons are retained "
            "in the companion JSON.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument(
        "--implementations",
        nargs="+",
        default=["old-python", "optimized-python", "old-rust", "optimized-rust"],
        choices=_IMPLEMENTATIONS,
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        default=["application-cold", "point-cache-hit"],
        choices=_STAGES,
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="record a failed model and continue with the remaining fixtures",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.repeat < 1 or args.warmup < 0:
        raise SystemExit("repeat must be >=1 and warmup must be >=0")
    if len(set(args.implementations)) != len(args.implementations):
        raise SystemExit("--implementations must not contain duplicates")
    if len(set(args.stages)) != len(args.stages):
        raise SystemExit("--stages must not contain duplicates")
    checkout = args.checkout.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve()
    rows, unavailable = _validated_rows(
        _load_manifest(manifest), manifest, checkout
    )
    benchmark = checkout / "benchmarks" / "benchmark_decoding.py"
    if not benchmark.is_file():
        raise SystemExit(f"decoder benchmark does not exist: {benchmark}")

    output_json = args.output_json.expanduser().resolve()
    output_markdown = args.output_markdown.expanduser().resolve()
    if os.path.normcase(output_json) == os.path.normcase(output_markdown):
        raise SystemExit("--output-json and --output-markdown must be different")

    input_fingerprints = {
        "manifest_sha256": _sha256(manifest),
        "matrix_driver_sha256": _sha256(Path(__file__).resolve()),
        "decoder_driver_sha256": _sha256(benchmark),
    }

    models = []
    failures = []
    fixture_hash_models: dict[str, str] = {}
    environment_identity: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="sharpmod-model-matrix-") as tmp:
        tmp_dir = Path(tmp)
        for index, row in enumerate(rows):
            result_path = tmp_dir / f"{index:02d}-{row['model']}.json"
            command = [
                sys.executable,
                str(benchmark),
                "--grib",
                row["path"],
                "--lat",
                str(row["lat"]),
                "--lon",
                str(row["lon"]),
                "--repeat",
                str(args.repeat),
                "--warmup",
                str(args.warmup),
                "--checkout",
                str(checkout),
                "--output",
                str(result_path),
                "--implementations",
                *args.implementations,
                "--stages",
                *args.stages,
            ]
            if row.get("valid_time"):
                command.extend(["--valid-time", str(row["valid_time"])])
            print(f"[{index + 1}/{len(rows)}] {row['label']}", flush=True)
            completed = subprocess.run(
                command,
                cwd=checkout,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if completed.returncode != 0 or not result_path.is_file():
                failure = {
                    "model": row["model"],
                    "label": row["label"],
                    "returncode": completed.returncode,
                    "output": completed.stdout,
                }
                failures.append(failure)
                if not args.continue_on_error:
                    print(completed.stdout)
                    raise SystemExit(
                        f"benchmark failed for {row['model']} with exit code "
                        f"{completed.returncode}"
                    )
                continue
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                failure = {
                    "model": row["model"],
                    "label": row["label"],
                    "returncode": completed.returncode,
                    "output": completed.stdout,
                    "validation_problems": [f"invalid result JSON: {exc}"],
                }
                failures.append(failure)
                if not args.continue_on_error:
                    raise SystemExit(
                        f"invalid benchmark result for {row['model']}: {exc}"
                    ) from exc
                continue
            problems = _result_problems(
                result,
                args.implementations,
                args.stages,
                repeat=args.repeat,
                expected_levels=row.get("pressure_level_count"),
            )
            current_environment = _environment_identity(result)
            if environment_identity is None:
                environment_identity = current_environment
            elif current_environment != environment_identity:
                problems.append(
                    "decoder source or runtime environment changed during "
                    "the matrix run"
                )
            fixture_hash = result.get("fixture", {}).get("sha256")
            prior_model = fixture_hash_models.get(str(fixture_hash))
            if not fixture_hash:
                problems.append("result omitted the fixture sha256")
            elif prior_model is not None:
                problems.append(
                    f"fixture bytes duplicate model {prior_model}; each model "
                    "needs an independently identified GRIB fixture"
                )
            else:
                fixture_hash_models[str(fixture_hash)] = row["model"]
            if problems:
                failure = {
                    "model": row["model"],
                    "label": row["label"],
                    "returncode": completed.returncode,
                    "output": completed.stdout,
                    "validation_problems": problems,
                }
                failures.append(failure)
                if not args.continue_on_error:
                    print(completed.stdout)
                    raise SystemExit(
                        f"incomplete benchmark for {row['model']}: "
                        + "; ".join(problems)
                    )
                continue
            models.append({"fixture": row, "result": result})

    final_fingerprints = {
        "manifest_sha256": _sha256(manifest),
        "matrix_driver_sha256": _sha256(Path(__file__).resolve()),
        "decoder_driver_sha256": _sha256(benchmark),
    }
    if final_fingerprints != input_fingerprints:
        failures.append(
            {
                "model": "benchmark-sources",
                "label": "Benchmark inputs",
                "returncode": None,
                "output": "",
                "validation_problems": [
                    "manifest or benchmark driver changed during the matrix run"
                ],
            }
        )

    payload = {
        "schema_version": 1,
        "benchmark": "SHARPpy Reimagined all-model local GRIB decoding",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest),
        "input_fingerprints": input_fingerprints,
        "environment_identity": environment_identity,
        "settings": {
            "repeat": args.repeat,
            "warmup": args.warmup,
            "implementations": list(args.implementations),
            "stages": list(args.stages),
        },
        "equivalence_scope": {
            "within_generation": "strict all columns including omega",
            "cross_generation_core": [
                "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v"
            ],
            "cross_generation_optional": ["omeg"],
        },
        "models": models,
        "unavailable": unavailable,
        "failures": failures,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    output_markdown.write_text(_markdown(payload), encoding="utf-8")
    print(f"JSON: {output_json}")
    print(f"Markdown: {output_markdown}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

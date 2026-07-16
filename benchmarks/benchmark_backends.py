"""Record comparable Python and Rust backend timings without speed claims.

The Rust extension is deliberately required: this harness must never label the
Python fallback as a native measurement. It validates numerical equivalence
before starting the timer and reports raw elapsed-time summaries only.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np


@dataclass(frozen=True)
class ProfileInputs:
    """Deterministic, reusable arrays for one benchmark size."""

    direction: np.ndarray
    speed: np.ndarray
    u: np.ndarray
    v: np.ndarray
    targets: np.ndarray
    coordinates: np.ndarray
    values: np.ndarray


@dataclass(frozen=True)
class Timing:
    """Elapsed samples for one operation/backend/case combination."""

    backend: str
    scenario: str
    operation: str
    calls: int
    samples: tuple[float, ...]


@dataclass(frozen=True)
class SoundingInputs:
    """Production-shaped columns used by integration-level scenarios."""

    pres: np.ndarray
    hght: np.ndarray
    tmpc: np.ndarray
    dwpc: np.ndarray
    wdir: np.ndarray
    wspd: np.ndarray

    @property
    def columns(self) -> tuple[np.ndarray, ...]:
        return self.pres, self.hght, self.tmpc, self.dwpc, self.wdir, self.wspd


@dataclass(frozen=True)
class ConcurrencyTiming:
    """Equal-work sequential and two-thread samples for one operation."""

    backend: str
    operation: str
    calls_per_worker: int
    sequential_samples: tuple[float, ...]
    threaded_samples: tuple[float, ...]


def _load_backends() -> tuple[Any, Any, Any]:
    """Require the reference backend, native adapter, and native module."""

    try:
        native_module = importlib.import_module("sharpmod_rs")
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "sharpmod_rs is required for this benchmark. Activate .gribenv, "
            "run `maturin develop --release` in rust/sharpmod-rs, and retry."
        ) from exc

    try:
        from sharpmod.backends.python_backend import PythonBackend
        from sharpmod.backends.rust_backend import RustBackend
    except ImportError as exc:
        raise RuntimeError(
            "The sharpmod backend package is unavailable. Install this checkout "
            "into .gribenv with `python -m pip install -e .`."
        ) from exc

    try:
        python_backend = PythonBackend()
        rust_backend = RustBackend()
    except Exception as exc:  # noqa: BLE001 - report adapter construction clearly
        raise RuntimeError(
            "Both PythonBackend() and RustBackend() must initialize before "
            "benchmarking."
        ) from exc

    for backend_name, backend in (
        ("PythonBackend", python_backend),
        ("RustBackend", rust_backend),
    ):
        for operation in (
            "wind_to_components",
            "components_to_wind",
            "interpolate_1d",
        ):
            if not callable(getattr(backend, operation, None)):
                raise RuntimeError(
                    f"{backend_name} does not expose callable {operation}"
                )

    return python_backend, rust_backend, native_module


def _profile_inputs(size: int) -> ProfileInputs:
    """Build deterministic atmospheric-shaped inputs without file I/O."""

    if size < 2:
        raise ValueError("benchmark profiles require at least two values")

    direction = np.linspace(-360.0, 720.0, size, dtype=np.float64)
    speed = np.linspace(0.0, 180.0, size, dtype=np.float64)
    radians = np.deg2rad(direction)
    u = -speed * np.sin(radians)
    v = -speed * np.cos(radians)

    # Descending pressure is the normal bottom-to-top sounding order. Targets
    # are ascending so the interpolation kernel has to honor its sorting
    # contract rather than receiving an already-normalized pair.
    coordinates = np.linspace(1050.0, 100.0, size, dtype=np.float64)
    values = np.linspace(35.0, -75.0, size, dtype=np.float64)
    targets = np.linspace(100.0, 1050.0, size, dtype=np.float64)
    return ProfileInputs(
        direction=direction,
        speed=speed,
        u=u,
        v=v,
        targets=targets,
        coordinates=coordinates,
        values=values,
    )


def _profile_batch_inputs(profile_count: int, levels: int) -> ProfileInputs:
    """Build a two-dimensional batch for the shape-aware wind kernels."""

    data = _profile_inputs(profile_count * levels)
    shape = (profile_count, levels)
    return ProfileInputs(
        direction=data.direction.reshape(shape),
        speed=data.speed.reshape(shape),
        u=data.u.reshape(shape),
        v=data.v.reshape(shape),
        targets=data.targets,
        coordinates=data.coordinates,
        values=data.values,
    )


def _sounding_inputs(size: int = 128) -> SoundingInputs:
    """Build a deterministic reported-level sounding used by the GUI path."""

    if size < 2:
        raise ValueError("benchmark soundings require at least two levels")
    pres = np.linspace(1020.0, 100.0, size, dtype=np.float64)
    hght = np.linspace(120.0, 16_000.0, size, dtype=np.float64)
    tmpc = 29.0 - (6.4 * (hght - hght[0]) / 1_000.0)
    dwpc = tmpc - np.linspace(3.0, 24.0, size, dtype=np.float64)
    wdir = np.linspace(155.0, 285.0, size, dtype=np.float64)
    wspd = np.linspace(8.0, 72.0, size, dtype=np.float64)
    return SoundingInputs(pres, hght, tmpc, dwpc, wdir, wspd)


def _production_interpolation_inputs(
    sounding: SoundingInputs,
) -> tuple[float, np.ndarray, tuple[np.ndarray, ...]]:
    """Return the scalar pressure lookup used by common SharpTab helpers."""

    coordinate = np.log10(sounding.pres)
    target = float(np.log10(700.0))
    radians = np.deg2rad(sounding.wdir)
    u = -sounding.wspd * np.sin(radians)
    v = -sounding.wspd * np.cos(radians)
    omega = -0.12 * np.sin(np.linspace(0.0, np.pi, sounding.pres.size))
    fields = (sounding.hght, sounding.tmpc, sounding.dwpc, u, v, omega)
    return target, coordinate, fields


def _plain_array(value: Any) -> np.ndarray:
    """Normalize ndarray or masked-array output for equivalence checks."""

    array = np.ma.asanyarray(value, dtype=np.float64)
    return np.asarray(array.filled(np.nan), dtype=np.float64)


def _assert_pair_close(
    python_value: Sequence[Any],
    rust_value: Sequence[Any],
    *,
    operation: str,
) -> None:
    """Validate the two arrays returned by a wind conversion."""

    if len(python_value) != 2 or len(rust_value) != 2:
        raise AssertionError(f"{operation} must return a pair")

    python_first = _plain_array(python_value[0])
    rust_first = _plain_array(rust_value[0])
    python_second = _plain_array(python_value[1])
    rust_second = _plain_array(rust_value[1])

    if operation == "components_to_wind":
        # Directions are circular: zero and 360 degrees are equivalent.
        direction_delta = (
            (rust_first - python_first + 180.0) % 360.0
        ) - 180.0
        np.testing.assert_allclose(
            direction_delta, 0.0, rtol=0.0, atol=1e-10, equal_nan=True
        )
    else:
        np.testing.assert_allclose(
            rust_first,
            python_first,
            rtol=1e-12,
            atol=1e-10,
            equal_nan=True,
        )

    np.testing.assert_allclose(
        rust_second,
        python_second,
        rtol=1e-12,
        atol=1e-10,
        equal_nan=True,
    )


def _assert_operation_close(
    reference_value: Any,
    candidate_value: Any,
    *,
    operation: str,
) -> None:
    """Compare one operation result, including circular wind direction."""

    if operation in ("wind_to_components", "components_to_wind"):
        _assert_pair_close(reference_value, candidate_value, operation=operation)
        return
    np.testing.assert_allclose(
        _plain_array(candidate_value),
        _plain_array(reference_value),
        rtol=1e-12,
        atol=1e-10,
        equal_nan=True,
    )


def _validate_equivalence(python_backend: Any, rust_backend: Any) -> None:
    """Refuse to time implementations that disagree numerically."""

    data = _profile_inputs(128)
    _assert_pair_close(
        python_backend.wind_to_components(data.direction, data.speed),
        rust_backend.wind_to_components(data.direction, data.speed),
        operation="wind_to_components",
    )
    _assert_pair_close(
        python_backend.components_to_wind(data.u, data.v),
        rust_backend.components_to_wind(data.u, data.v),
        operation="components_to_wind",
    )

    python_interp = python_backend.interpolate_1d(
        data.targets, data.coordinates, data.values
    )
    rust_interp = rust_backend.interpolate_1d(
        data.targets, data.coordinates, data.values
    )
    np.testing.assert_allclose(
        _plain_array(rust_interp),
        _plain_array(python_interp),
        rtol=1e-12,
        atol=1e-10,
        equal_nan=True,
    )

    batch = _profile_batch_inputs(4, 128)
    _assert_pair_close(
        python_backend.wind_to_components(batch.direction, batch.speed),
        rust_backend.wind_to_components(batch.direction, batch.speed),
        operation="wind_to_components",
    )
    _assert_pair_close(
        python_backend.components_to_wind(batch.u, batch.v),
        rust_backend.components_to_wind(batch.u, batch.v),
        operation="components_to_wind",
    )

    for levels in (32, 128):
        sounding = _sounding_inputs(levels)
        target, coordinate, fields = _production_interpolation_inputs(sounding)
        for field in fields:
            _assert_operation_close(
                python_backend.interpolate_1d(target, coordinate, field),
                rust_backend.interpolate_1d(target, coordinate, field),
                operation="interpolate_1d",
            )


def _operation_arguments(data: ProfileInputs) -> dict[str, tuple[np.ndarray, ...]]:
    """Return arguments in the shared backend protocol order."""

    return {
        "wind_to_components": (data.direction, data.speed),
        "components_to_wind": (data.u, data.v),
        "interpolate_1d": (data.targets, data.coordinates, data.values),
    }


def _measure_backends(
    functions: Sequence[tuple[str, Callable[..., Any]]],
    arguments: tuple[Any, ...],
    *,
    calls: int,
    repeat: int,
    warmup: int,
) -> dict[str, tuple[float, ...]]:
    """Measure both backends with alternating order on every repeat."""

    for _ in range(warmup):
        for _, function in functions:
            function(*arguments)

    samples: dict[str, list[float]] = {name: [] for name, _ in functions}
    gc_was_enabled = gc.isenabled()
    try:
        gc.disable()
        for repeat_index in range(repeat):
            ordered = (
                functions
                if repeat_index % 2 == 0
                else tuple(reversed(functions))
            )
            for name, function in ordered:
                result: Any = None
                started = time.perf_counter()
                for _ in range(calls):
                    result = function(*arguments)
                samples[name].append(time.perf_counter() - started)
                # Keep a live reference through the end of each sample. All
                # current kernels are eager, but this also avoids timing only
                # object setup if a future adapter returns an owning wrapper.
                if result is None:
                    raise RuntimeError(
                        "backend operation unexpectedly returned None")
    finally:
        if gc_was_enabled:
            gc.enable()
    return {name: tuple(values) for name, values in samples.items()}


def _record_callable_scenario(
    functions: Sequence[tuple[str, Callable[..., Any]]],
    arguments: tuple[Any, ...],
    *,
    scenario: str,
    operation: str,
    calls: int,
    repeat: int,
    warmup: int,
) -> list[Timing]:
    """Measure one custom operation with the normal alternating order."""

    measured = _measure_backends(
        functions,
        arguments,
        calls=calls,
        repeat=repeat,
        warmup=warmup,
    )
    return [
        Timing(name, scenario, operation, calls, measured[name])
        for name, _ in functions
    ]


def _record_production_interpolation(
    python_backend: Any,
    rust_backend: Any,
    *,
    calls: int,
    repeat: int,
    warmup: int,
) -> list[Timing]:
    """Measure 32/128-level scalar lookups and a repeated six-field lookup."""

    backends = (("python", python_backend), ("rust", rust_backend))
    records = []
    for levels in (32, 128):
        sounding = _sounding_inputs(levels)
        target, coordinate, _ = _production_interpolation_inputs(sounding)
        records.extend(
            _record_callable_scenario(
                tuple(
                    (name, backend.interpolate_1d) for name, backend in backends
                ),
                (target, coordinate, sounding.tmpc),
                scenario=f"scalar-700hpa-{levels}",
                operation="interpolate_1d",
                calls=calls,
                repeat=repeat,
                warmup=warmup,
            )
        )

    sounding = _sounding_inputs(128)
    target, coordinate, fields = _production_interpolation_inputs(sounding)

    def repeated_fields(backend: Any) -> Callable[[], tuple[Any, ...]]:
        return lambda: tuple(
            backend.interpolate_1d(target, coordinate, field) for field in fields
        )

    field_calls = max(1, calls // len(fields))
    records.extend(
        _record_callable_scenario(
            tuple(
                (name, repeated_fields(backend)) for name, backend in backends
            ),
            (),
            scenario="six-fields-700hpa-128",
            operation="interpolate_1d_x6",
            calls=field_calls,
            repeat=repeat,
            warmup=warmup,
        )
    )
    return records


def _record_scenario(
    python_backend: Any,
    rust_backend: Any,
    *,
    scenario: str,
    size: int,
    calls: int,
    repeat: int,
    warmup: int,
    operations: Sequence[str] | None = None,
    data: ProfileInputs | None = None,
) -> list[Timing]:
    """Measure all initial array kernels for one input size."""

    data = data or _profile_inputs(size)
    operation_args = _operation_arguments(data)
    selected_operations = tuple(operations or operation_args)
    records: list[Timing] = []
    backends = (
        ("python", python_backend),
        ("rust", rust_backend),
    )
    for operation in selected_operations:
        arguments = operation_args[operation]
        measured = _measure_backends(
            tuple(
                (backend_name, getattr(backend, operation))
                for backend_name, backend in backends
            ),
            arguments,
            calls=calls,
            repeat=repeat,
            warmup=warmup,
        )
        for backend_name, _ in backends:
            records.append(
                Timing(
                    backend=backend_name,
                    scenario=scenario,
                    operation=operation,
                    calls=calls,
                    samples=measured[backend_name],
                )
            )
    return records


def _record_profile_construction(
    *,
    calls: int,
    repeat: int,
    warmup: int,
) -> list[Timing]:
    """Measure real ``Profile`` construction with a pre-cached selector.

    Backend selection is process-global, so each sample configures and resolves
    one forced mode before the timer starts.  The timed Profile constructors
    still call the public facade and its cached ``get_backend()`` path.
    """

    from sharpmod import backends
    from sharpmod.sharptab.profile import Profile

    sounding = _sounding_inputs()
    prior_mode = os.environ.get("SHARPMOD_BACKEND")
    samples: dict[str, list[float]] = {"python": [], "rust": []}

    def configure(mode: str) -> None:
        os.environ["SHARPMOD_BACKEND"] = mode
        backends.reset_backend_cache()
        info = backends.backend_info()
        if info["active_backend"] != mode:
            raise RuntimeError(
                f"Profile benchmark requested {mode}, but selected {info}"
            )
        # Resolve once before timing so every constructor sees the cached
        # selection rather than measuring extension discovery/import.
        if backends.get_backend().name != mode:
            raise RuntimeError(f"cached selector did not retain {mode}")

    try:
        profiles = {}
        for mode in samples:
            configure(mode)
            profiles[mode] = Profile(*sounding.columns)
            for _ in range(warmup):
                Profile(*sounding.columns)
        for field in ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v"):
            np.testing.assert_allclose(
                _plain_array(getattr(profiles["rust"], field)),
                _plain_array(getattr(profiles["python"], field)),
                rtol=1e-12,
                atol=1e-10,
                equal_nan=True,
            )

        gc_was_enabled = gc.isenabled()
        try:
            gc.disable()
            modes = tuple(samples)
            for repeat_index in range(repeat):
                ordered = modes if repeat_index % 2 == 0 else tuple(reversed(modes))
                for mode in ordered:
                    configure(mode)
                    result = None
                    started = time.perf_counter()
                    for _ in range(calls):
                        result = Profile(*sounding.columns)
                    samples[mode].append(time.perf_counter() - started)
                    if result is None:
                        raise RuntimeError("Profile construction returned None")
        finally:
            if gc_was_enabled:
                gc.enable()
    finally:
        if prior_mode is None:
            os.environ.pop("SHARPMOD_BACKEND", None)
        else:
            os.environ["SHARPMOD_BACKEND"] = prior_mode
        backends.reset_backend_cache()

    return [
        Timing(
            mode,
            "profile-128-cached-selector",
            "Profile.__init__",
            calls,
            tuple(values),
        )
        for mode, values in samples.items()
    ]


def _repeat_call(
    function: Callable[..., Any], arguments: tuple[Any, ...], calls: int
) -> Any:
    result = None
    for _ in range(calls):
        result = function(*arguments)
    if result is None:
        raise RuntimeError("backend operation unexpectedly returned None")
    return result


def _record_concurrency_diagnostic(
    python_backend: Any,
    rust_backend: Any,
    *,
    size: int,
    calls_per_worker: int,
    repeat: int,
    warmup: int,
) -> list[ConcurrencyTiming]:
    """Compare equal work serially and in two persistent Python threads."""

    data = _profile_inputs(size)
    operation_args = _operation_arguments(data)
    records = []
    for backend_name, backend in (("python", python_backend), ("rust", rust_backend)):
        for operation, arguments in operation_args.items():
            function = getattr(backend, operation)
            reference = function(*arguments)
            sequential_samples = []
            threaded_samples = []
            with ThreadPoolExecutor(max_workers=2) as executor:
                # Start both persistent workers before warmup/timing so thread
                # creation is not attributed to the concurrent measurement.
                tuple(executor.map(lambda value: value, (0, 1)))

                def run_sequential() -> tuple[Any, Any]:
                    return (
                        _repeat_call(function, arguments, calls_per_worker),
                        _repeat_call(function, arguments, calls_per_worker),
                    )

                def run_threaded() -> tuple[Any, Any]:
                    futures = tuple(
                        executor.submit(
                            _repeat_call, function, arguments, calls_per_worker
                        )
                        for _ in range(2)
                    )
                    return tuple(future.result() for future in futures)

                for _ in range(warmup):
                    run_sequential()
                    run_threaded()
                for threaded_value in run_threaded():
                    _assert_operation_close(
                        reference, threaded_value, operation=operation
                    )

                gc_was_enabled = gc.isenabled()
                try:
                    gc.disable()
                    for repeat_index in range(repeat):
                        measurements = (
                            (("sequential", run_sequential), ("threaded", run_threaded))
                            if repeat_index % 2 == 0
                            else (
                                ("threaded", run_threaded),
                                ("sequential", run_sequential),
                            )
                        )
                        for measurement_name, measurement in measurements:
                            started = time.perf_counter()
                            results = measurement()
                            elapsed = time.perf_counter() - started
                            if len(results) != 2:
                                raise RuntimeError(
                                    "concurrency diagnostic lost a worker result"
                                )
                            target = (
                                sequential_samples
                                if measurement_name == "sequential"
                                else threaded_samples
                            )
                            target.append(elapsed)
                finally:
                    if gc_was_enabled:
                        gc.enable()
            records.append(
                ConcurrencyTiming(
                    backend_name,
                    operation,
                    calls_per_worker,
                    tuple(sequential_samples),
                    tuple(threaded_samples),
                )
            )
    return records


def _milliseconds(seconds: float) -> str:
    return f"{seconds * 1_000.0:.6f}"


def _rustc_version() -> str:
    executable = shutil.which(os.environ.get("RUSTC", "rustc"))
    if executable is None:
        return "not found on PATH"
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return completed.stdout.strip()


def _print_header(native_module: Any, arguments: argparse.Namespace) -> None:
    """Print the environment needed to interpret or reproduce a run."""

    print("SHARPpy Reimagined backend benchmark")
    print(f"platform: {platform.platform()}")
    print(f"machine: {platform.machine()}")
    print(f"cpu: {arguments.cpu_model or platform.processor() or 'not recorded'}")
    print(f"power mode: {arguments.power_mode or 'not recorded'}")
    print(f"python: {platform.python_version()} ({sys.executable})")
    print(f"numpy: {np.__version__}")
    print(f"sharpmod_rs: {getattr(native_module, '__version__', 'unknown')}")
    print(f"rustc: {_rustc_version()}")
    print(
        "settings: "
        f"repeat={arguments.repeat}, warmup={arguments.warmup}, "
        f"large_size={arguments.large_size}, "
        f"repeated_calls={arguments.repeated_calls}, "
        f"batch={arguments.batch_profiles}x{arguments.batch_levels}, "
        f"profile_constructions={arguments.profile_constructions}, "
        f"concurrency={arguments.concurrency_size}x"
        f"{arguments.concurrency_calls_per_worker}/worker"
    )
    print()


def _print_records(records: Sequence[Timing]) -> None:
    """Print raw timing summaries without ranking the backends."""

    headings = (
        "scenario",
        "operation",
        "backend",
        "calls",
        "median total ms",
        "min total ms",
        "max total ms",
        "median per call ms",
    )
    print(" | ".join(headings))
    print(" | ".join("---" for _ in headings))
    for record in records:
        median = statistics.median(record.samples)
        minimum = min(record.samples)
        maximum = max(record.samples)
        per_call = median / record.calls
        print(
            " | ".join(
                (
                    record.scenario,
                    record.operation,
                    record.backend,
                    str(record.calls),
                    _milliseconds(median),
                    _milliseconds(minimum),
                    _milliseconds(maximum),
                    _milliseconds(per_call),
                )
            )
        )


def _print_concurrency_records(records: Sequence[ConcurrencyTiming]) -> None:
    """Print equal-work thread diagnostics without treating them as a gate."""

    headings = (
        "backend",
        "operation",
        "calls / worker",
        "median sequential ms",
        "median two-thread ms",
        "threaded / sequential",
    )
    print()
    print("Two-thread concurrency diagnostic (same total work)")
    print(" | ".join(headings))
    print(" | ".join("---" for _ in headings))
    for record in records:
        sequential = statistics.median(record.sequential_samples)
        threaded = statistics.median(record.threaded_samples)
        print(
            " | ".join(
                (
                    record.backend,
                    record.operation,
                    str(record.calls_per_worker),
                    _milliseconds(sequential),
                    _milliseconds(threaded),
                    f"{threaded / sequential:.3f}",
                )
            )
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--large-size", type=int, default=100_000)
    parser.add_argument("--repeated-calls", type=int, default=1_000)
    parser.add_argument("--batch-profiles", type=int, default=2_048)
    parser.add_argument("--batch-levels", type=int, default=128)
    parser.add_argument("--profile-constructions", type=int, default=500)
    parser.add_argument("--concurrency-size", type=int, default=100_000)
    parser.add_argument("--concurrency-calls-per-worker", type=int, default=10)
    parser.add_argument("--cpu-model")
    parser.add_argument("--power-mode")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    if arguments.warmup < 0:
        raise SystemExit("--warmup cannot be negative")
    if arguments.large_size < 2:
        raise SystemExit("--large-size must be at least 2")
    if arguments.repeated_calls < 1:
        raise SystemExit("--repeated-calls must be at least 1")
    if arguments.batch_profiles < 1:
        raise SystemExit("--batch-profiles must be at least 1")
    if arguments.batch_levels < 2:
        raise SystemExit("--batch-levels must be at least 2")
    if arguments.profile_constructions < 1:
        raise SystemExit("--profile-constructions must be at least 1")
    if arguments.concurrency_size < 2:
        raise SystemExit("--concurrency-size must be at least 2")
    if arguments.concurrency_calls_per_worker < 1:
        raise SystemExit("--concurrency-calls-per-worker must be at least 1")

    python_backend, rust_backend, native_module = _load_backends()
    _validate_equivalence(python_backend, rust_backend)
    _print_header(native_module, arguments)

    records: list[Timing] = []
    records.extend(
        _record_scenario(
            python_backend,
            rust_backend,
            scenario="small-32",
            size=32,
            calls=1_000,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
        )
    )
    records.extend(
        _record_scenario(
            python_backend,
            rust_backend,
            scenario="ordinary-128",
            size=128,
            calls=500,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
        )
    )
    records.extend(
        _record_scenario(
            python_backend,
            rust_backend,
            scenario=f"large-{arguments.large_size}",
            size=arguments.large_size,
            calls=10,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
        )
    )
    records.extend(
        _record_scenario(
            python_backend,
            rust_backend,
            scenario=(
                f"batch-{arguments.batch_profiles}x{arguments.batch_levels}"
            ),
            size=arguments.batch_profiles * arguments.batch_levels,
            calls=5,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
            operations=("wind_to_components", "components_to_wind"),
            data=_profile_batch_inputs(
                arguments.batch_profiles, arguments.batch_levels),
        )
    )
    records.extend(
        _record_scenario(
            python_backend,
            rust_backend,
            scenario=f"repeated-128x{arguments.repeated_calls}",
            size=128,
            calls=arguments.repeated_calls,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
        )
    )
    records.extend(
        _record_production_interpolation(
            python_backend,
            rust_backend,
            calls=arguments.repeated_calls,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
        )
    )
    records.extend(
        _record_profile_construction(
            calls=arguments.profile_constructions,
            repeat=arguments.repeat,
            warmup=arguments.warmup,
        )
    )
    _print_records(records)
    concurrency_records = _record_concurrency_diagnostic(
        python_backend,
        rust_backend,
        size=arguments.concurrency_size,
        calls_per_worker=arguments.concurrency_calls_per_worker,
        repeat=arguments.repeat,
        warmup=arguments.warmup,
    )
    _print_concurrency_records(concurrency_records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

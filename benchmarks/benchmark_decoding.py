"""Benchmark legacy and optimized local-GRIB point decoding.

The benchmark is intentionally local-file only: network transfer and provider
availability are not decoder performance.  It records raw samples and enough
fixture/runtime metadata to make old/new comparisons auditable.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import numpy.ma as ma

import legacy_decoding


IMPLEMENTATIONS = (
    "old-python",
    "old-rust",
    "optimized-python",
    "optimized-rust",
)
STAGES = (
    "application-cold",
    "warm-inventory-point-miss",
    "warm-dataset-point",
    "point-cache-hit",
    "profile-construction",
    "end-to-end",
)
MISSING = -9999.0


class ImplementationUnavailable(RuntimeError):
    """A requested benchmark implementation is not installed or exposed."""


class StageUnavailable(RuntimeError):
    """An implementation does not provide a meaningful version of a stage."""


@dataclass
class StageSession:
    """Prepared callable plus lifecycle hooks for one timed stage."""

    call: Callable[[], Any]
    before_sample: Callable[[], None] = lambda: None
    after_sample: Callable[[], None] = lambda: None
    close: Callable[[], None] = lambda: None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimingRecord:
    implementation: str
    stage: str
    description: str
    samples_seconds: tuple[float, ...]
    details: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        samples = self.samples_seconds
        return {
            "implementation": self.implementation,
            "stage": self.stage,
            "description": self.description,
            "calls_per_sample": 1,
            "samples_seconds": list(samples),
            "median_seconds": statistics.median(samples),
            "minimum_seconds": min(samples),
            "maximum_seconds": max(samples),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class UnavailableRecord:
    implementation: str
    stage: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "implementation": self.implementation,
            "stage": self.stage,
            "reason": self.reason,
        }


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _activate_checkout(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise SystemExit(f"--checkout is not a directory: {resolved}")
    text = str(resolved)
    if text not in sys.path:
        sys.path.insert(0, text)
    os.chdir(resolved)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _module_version(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except (ImportError, OSError):
        return None
    value = getattr(module, "__version__", None)
    return None if value is None else str(value)


def _eccodes_version() -> str | None:
    try:
        module = importlib.import_module("eccodes")
        function = getattr(module, "codes_get_api_version", None)
        return str(function()) if callable(function) else _module_version("eccodes")
    except (ImportError, OSError, RuntimeError):
        return None


def _git_metadata(checkout: Path) -> dict[str, Any]:
    def run(*arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(checkout), *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip()

    status = run("status", "--porcelain")
    try:
        diff = subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "diff",
                "--binary",
                "--no-ext-diff",
                "HEAD",
                "--",
            ],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        diff = None
    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": None if status is None else bool(status),
        "status_sha256": (
            None
            if status is None
            else hashlib.sha256(status.encode("utf-8")).hexdigest()
        ),
        "tracked_diff_sha256": (
            None if diff is None else hashlib.sha256(diff).hexdigest()
        ),
    }


def _source_fingerprints() -> dict[str, Any]:
    """Identify the Python sources and native binary used by this run."""

    result: dict[str, Any] = {}
    paths = {"benchmark_decoding": Path(__file__).resolve()}
    for name in (
        "legacy_decoding",
        "sharpmod.backends.grib",
        "sharpmod.backends.python_backend",
        "sharpmod.backends.rust_backend",
        "sharpmod_rs",
        "sharpmod_rs.sharpmod_rs",
    ):
        try:
            module = importlib.import_module(name)
            module_path = getattr(module, "__file__", None)
            if module_path:
                paths[name] = Path(module_path).resolve()
        except (ImportError, OSError, RuntimeError):
            continue
    for name, path in paths.items():
        try:
            result[name] = {"path": str(path), "sha256": _sha256(path)}
        except OSError as exc:
            result[name] = {
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result


def _environment_metadata(checkout: Path) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "numpy": np.__version__,
        "cfgrib": _distribution_version("cfgrib"),
        "xarray": _distribution_version("xarray"),
        "eccodes_python": _distribution_version("eccodes"),
        "eccodes_library": _eccodes_version(),
        "sharpmod": _module_version("sharpmod"),
        "sharpmod_rs": _module_version("sharpmod_rs"),
        "source_fingerprints": _source_fingerprints(),
        "git": _git_metadata(checkout),
    }


def _index_template(directory: Path, fixture: Path) -> str:
    return str(directory / f"{fixture.name}.{{short_hash}}.idx")


def _point_matrix(point: Any) -> np.ndarray:
    matrix = getattr(point, "matrix", None)
    if matrix is not None:
        result = np.asarray(matrix, dtype=np.float64)
    elif isinstance(point, legacy_decoding.LegacyPoint):
        result = np.vstack(
            [
                point.pres,
                point.hght,
                point.tmpc,
                point.dwpc,
                point.wdir,
                point.wspd,
                point.omeg,
                point.uwnd,
                point.vwnd,
            ]
        )
    elif isinstance(point, Mapping):
        names = (
            "pres",
            "hght",
            "tmpc",
            "dwpc",
            "wdir",
            "wspd",
            "omeg",
            "u",
            "v",
        )
        result = np.vstack([np.asarray(point[name]) for name in names])
    else:
        raise TypeError(
            f"unsupported decoded-point result: {type(point).__name__}"
        )
    if result.ndim != 2 or result.shape[0] != 9:
        raise ValueError(
            f"decoded point matrix must have shape (9, levels), got {result.shape}"
        )
    return result


def _point_coordinates(point: Any) -> tuple[float, float]:
    try:
        return float(point.selected_lat), float(point.selected_lon)
    except AttributeError:
        if isinstance(point, Mapping):
            return float(point["selected_lat"]), float(point["selected_lon"])
        raise


def _point_vorticity(point: Any) -> float | None:
    if isinstance(point, Mapping):
        value = point.get("surface_relative_vorticity")
    else:
        value = getattr(point, "surface_relative_vorticity", None)
    if value is None:
        return None
    numeric = float(np.asarray(value).reshape(-1)[0])
    return numeric if np.isfinite(numeric) else None


def _masked(values: Any) -> ma.MaskedArray:
    array = ma.masked_invalid(ma.asarray(values, dtype=float))
    return ma.masked_where(np.asarray(array) == MISSING, array)


def _build_profile(point: Any) -> Any:
    from sharpmod.sharptab.profile import Profile

    matrix = _point_matrix(point)
    selected_lat, selected_lon = _point_coordinates(point)
    return Profile(
        _masked(matrix[0]),
        _masked(matrix[1]),
        _masked(matrix[2]),
        _masked(matrix[3]),
        _masked(matrix[4]),
        _masked(matrix[5]),
        omeg=_masked(matrix[6]),
        meta={
            "selected_lat": selected_lat,
            "selected_lon": selected_lon,
            "surface_relative_vorticity": _point_vorticity(point),
        },
    )


def _consume_result(result: Any) -> tuple[Any, ...]:
    if isinstance(result, tuple) and len(result) == 2:
        point, profile = result
        matrix = _point_matrix(point)
        return (
            matrix.shape,
            float(matrix[0, 0]) if matrix.shape[1] else None,
            int(np.asarray(profile.pres).size),
        )
    if hasattr(result, "pres") and not hasattr(result, "matrix") \
            and not isinstance(result, legacy_decoding.LegacyPoint):
        return (int(np.asarray(result.pres).size),)
    matrix = _point_matrix(result)
    selected_lat, selected_lon = _point_coordinates(result)
    return (
        matrix.shape,
        selected_lat,
        selected_lon,
        float(matrix[0, 0]) if matrix.shape[1] else None,
    )


def _compare_matrices(
    reference_matrix: np.ndarray,
    candidate_matrix: np.ndarray,
    label: str,
    column_names: Sequence[str] = (
        "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg", "u", "v"
    ),
) -> None:
    if reference_matrix.shape != candidate_matrix.shape:
        raise AssertionError(
            f"{label}: matrix shape {candidate_matrix.shape} != "
            f"reference shape {reference_matrix.shape}"
        )
    reference_mask = (~np.isfinite(reference_matrix)) | (
        reference_matrix == MISSING
    )
    candidate_mask = (~np.isfinite(candidate_matrix)) | (
        candidate_matrix == MISSING
    )
    np.testing.assert_array_equal(
        candidate_mask,
        reference_mask,
        err_msg=f"{label}: missing-value mask differs",
    )
    names = tuple(column_names)
    if reference_matrix.shape[0] != len(names):
        raise AssertionError(
            f"{label}: {reference_matrix.shape[0]} rows for {len(names)} names"
        )
    tolerances = {
        "pres": 1e-6,
        "hght": 1e-3,
        # cfgrib materializes these GRIB-packed fields as float32 before the
        # legacy Kelvin/Celsius and humidity conversions.  The direct
        # decoders retain the ecCodes double until after conversion, so allow
        # one float32-scale rounding step without hiding meteorological drift.
        "tmpc": 5e-5,
        "dwpc": 5e-5,
        "wdir": 1e-5,
        "wspd": 1e-5,
        "omeg": 1e-7,
        "u": 1e-6,
        "v": 1e-6,
    }
    for index, name in enumerate(names):
        good = ~reference_mask[index]
        if name == "wdir":
            difference = (
                candidate_matrix[index, good]
                - reference_matrix[index, good]
                + 180.0
            ) % 360.0 - 180.0
            np.testing.assert_allclose(
                difference,
                0.0,
                rtol=0.0,
                atol=tolerances[name],
                err_msg=f"{label}: {name} differs",
            )
        else:
            np.testing.assert_allclose(
                candidate_matrix[index, good],
                reference_matrix[index, good],
                rtol=1e-7,
                atol=tolerances[name],
                err_msg=f"{label}: {name} differs",
            )


def _compare_points(reference: Any, candidate: Any, label: str) -> None:
    _compare_matrices(
        _point_matrix(reference), _point_matrix(candidate), label
    )
    np.testing.assert_allclose(
        _point_coordinates(candidate),
        _point_coordinates(reference),
        rtol=0.0,
        atol=1e-9,
        err_msg=f"{label}: selected grid point differs",
    )


def _compare_cross_generation(
    legacy: Any,
    optimized: Any,
    label: str,
) -> dict[str, Any]:
    """Compare every legacy level while allowing recovered optimized levels.

    cfgrib can split one product into hypercubes with different pressure
    coordinates and retain only the 33-level group during the outer merge.
    Direct message iteration intentionally recovers all planner-selected
    levels.  A legacy-only level would still be a regression and is rejected.
    """
    legacy_matrix = _point_matrix(legacy)
    optimized_matrix = _point_matrix(optimized)
    legacy_pressure = legacy_matrix[0]
    optimized_pressure = optimized_matrix[0]
    for name, pressure in (
        ("legacy", legacy_pressure),
        ("optimized", optimized_pressure),
    ):
        if (
            np.any(~np.isfinite(pressure))
            or np.any(pressure <= 0.0)
            or np.any(np.diff(pressure) >= 0.0)
        ):
            raise AssertionError(
                f"{label}: {name} pressure is not finite, unique, and descending"
            )

    optimized_indexes = []
    for pressure in legacy_pressure:
        matching = np.flatnonzero(np.isclose(
            optimized_pressure, pressure, rtol=0.0, atol=1e-6
        ))
        if matching.size != 1:
            raise AssertionError(
                f"{label}: legacy pressure {pressure:g} hPa has "
                f"{matching.size} optimized matches"
            )
        optimized_indexes.append(int(matching[0]))
    optimized_aligned = optimized_matrix[:, optimized_indexes]
    core_indexes = (0, 1, 2, 3, 4, 5, 7, 8)
    core_names = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v")
    _compare_matrices(
        legacy_matrix[list(core_indexes)],
        optimized_aligned[list(core_indexes)],
        f"{label} on legacy pressure levels",
        core_names,
    )
    np.testing.assert_allclose(
        _point_coordinates(optimized),
        _point_coordinates(legacy),
        rtol=0.0,
        atol=1e-9,
        err_msg=f"{label}: selected grid point differs",
    )
    matched = set(optimized_indexes)
    extra = [
        float(pressure)
        for index, pressure in enumerate(optimized_pressure)
        if index not in matched
    ]
    legacy_omega = legacy_matrix[6]
    optimized_omega = optimized_aligned[6]
    legacy_omega_valid = np.isfinite(legacy_omega) & (
        legacy_omega != MISSING
    )
    optimized_omega_valid = np.isfinite(optimized_omega) & (
        optimized_omega != MISSING
    )
    both_omega = legacy_omega_valid & optimized_omega_valid
    omega_differences = np.abs(
        optimized_omega[both_omega] - legacy_omega[both_omega]
    )
    omega_matched = (
        np.array_equal(legacy_omega_valid, optimized_omega_valid)
        and np.all(omega_differences <= 1e-7)
    )
    optional_fields = {
        "omeg": {
            "status": "matched" if omega_matched else "different",
            "legacy_valid_levels": int(np.count_nonzero(legacy_omega_valid)),
            "optimized_valid_levels": int(
                np.count_nonzero(optimized_omega_valid)
            ),
            "common_valid_levels": int(np.count_nonzero(both_omega)),
            "max_abs_difference_on_common": (
                None
                if omega_differences.size == 0
                else float(np.max(omega_differences))
            ),
            "legacy_only_pressures_hpa": [
                float(value)
                for value in legacy_pressure[
                    legacy_omega_valid & ~optimized_omega_valid
                ]
            ],
            "optimized_only_pressures_hpa": [
                float(value)
                for value in legacy_pressure[
                    optimized_omega_valid & ~legacy_omega_valid
                ]
            ],
        }
    }
    return {
        "status": "passed",
        "legacy_levels": int(legacy_pressure.size),
        "optimized_levels": int(optimized_pressure.size),
        "common_levels": int(legacy_pressure.size),
        "optimized_extra_pressures_hpa": extra,
        "optional_fields": optional_fields,
    }


def _compare_vorticity(left: Any, right: Any, label: str) -> dict[str, Any]:
    """Compare the optional metadata within one backend generation.

    Some products publish no pressure-level vorticity field.  Their production
    path deliberately retains the compact xarray wind-gradient fallback, while
    the raw direct-decoder benchmark returns ``None``.  Python and Rust must
    still agree with each other within the old and optimized generations.
    """
    left_value = _point_vorticity(left)
    right_value = _point_vorticity(right)
    if left_value is None or right_value is None:
        if left_value is not None or right_value is not None:
            raise AssertionError(
                f"{label}: surface-relative-vorticity availability differs"
            )
        return {"status": "both-unavailable", "value": None}
    np.testing.assert_allclose(
        right_value,
        left_value,
        rtol=1e-6,
        atol=1e-9,
        err_msg=f"{label}: surface relative vorticity differs",
    )
    return {"status": "matched", "value": left_value}


class BaseAdapter:
    label: str
    backend: str

    def activate(self) -> None:
        """Select the matching backend before untimed setup or a timed call."""

        os.environ["SHARPMOD_BACKEND"] = self.backend
        try:
            from sharpmod import backends

            backends.reset_backend_cache()
            selected = backends.backend_info()["active_backend"]
        except Exception as exc:
            if self.backend == "rust":
                raise ImplementationUnavailable(str(exc)) from exc
            return
        if selected != self.backend:
            raise ImplementationUnavailable(
                f"requested {self.backend}, selected {selected}"
            )

    def decode_once(self, lat: float, lon: float) -> Any:
        session = self.stage_session(
            "application-cold", lat, lon, lat, lon
        )
        try:
            session.before_sample()
            result = session.call()
            _consume_result(result)
            return result
        finally:
            try:
                session.after_sample()
            finally:
                session.close()

    def stage_session(
        self,
        stage: str,
        lat: float,
        lon: float,
        second_lat: float,
        second_lon: float,
    ) -> StageSession:
        raise NotImplementedError


class LegacyAdapter(BaseAdapter):
    def __init__(
        self,
        label: str,
        fixture: Path,
        backend: str,
        valid_time: datetime | None,
        work_dir: Path,
    ) -> None:
        self.label = label
        self.fixture = fixture
        self.backend = backend
        self.valid_time = valid_time
        self.work_dir = work_dir
        self.activate()
        if backend == "rust":
            legacy_decoding._components_to_wind_rust(
                np.asarray([0.0]), np.asarray([0.0])
            )

    def _decode(self, lat: float, lon: float, indexpath: str) -> Any:
        return legacy_decoding.decode_grib_point(
            self.fixture,
            lat,
            lon,
            backend=self.backend,
            valid_time=self.valid_time,
            indexpath=indexpath,
        )

    def stage_session(
        self,
        stage: str,
        lat: float,
        lon: float,
        second_lat: float,
        second_lon: float,
    ) -> StageSession:
        if stage == "application-cold":
            def before() -> None:
                self.activate()

            def call() -> Any:
                return self._decode(lat, lon, "")

            return StageSession(
                call,
                before_sample=before,
                details={
                    "cache_state": "cfgrib index disabled explicitly",
                    "os_file_cache": "not flushed",
                },
            )

        if stage == "warm-inventory-point-miss":
            temporary = tempfile.TemporaryDirectory(
                prefix="legacy-warm-index-", dir=self.work_dir
            )
            indexpath = _index_template(Path(temporary.name), self.fixture)
            self._decode(lat, lon, indexpath)
            return StageSession(
                lambda: self._decode(second_lat, second_lon, indexpath),
                before_sample=self.activate,
                close=temporary.cleanup,
                details={
                    "cache_state": "persistent cfgrib indexes; new xarray merge",
                    "point": [second_lat, second_lon],
                },
            )

        if stage == "warm-dataset-point":
            temporary = tempfile.TemporaryDirectory(
                prefix="legacy-open-dataset-", dir=self.work_dir
            )
            opened = legacy_decoding.open_grib(
                self.fixture,
                backend=self.backend,
                indexpath=_index_template(Path(temporary.name), self.fixture),
                load=True,
            )

            def close() -> None:
                try:
                    opened.close()
                finally:
                    temporary.cleanup()

            return StageSession(
                lambda: opened.decode_point(
                    second_lat, second_lon, self.valid_time
                ),
                before_sample=self.activate,
                close=close,
                details={
                    "cache_state": "merged xarray dataset loaded and retained",
                    "point": [second_lat, second_lon],
                },
            )

        if stage == "point-cache-hit":
            raise StageUnavailable(
                "the frozen decoder had no decoded-point cache"
            )

        if stage == "profile-construction":
            temporary = tempfile.TemporaryDirectory(
                prefix="legacy-profile-", dir=self.work_dir
            )
            point = self._decode(
                lat,
                lon,
                _index_template(Path(temporary.name), self.fixture),
            )
            return StageSession(
                lambda: _build_profile(point),
                before_sample=self.activate,
                close=temporary.cleanup,
                details={"scope": "Profile from already-decoded columns"},
            )

        if stage == "end-to-end":
            def before() -> None:
                self.activate()

            def call() -> Any:
                point = self._decode(lat, lon, "")
                return point, _build_profile(point)

            return StageSession(
                call,
                before_sample=before,
                details={
                    "scope": (
                        "no-index GRIB decode plus Profile construction"
                    ),
                    "os_file_cache": "not flushed",
                },
            )

        raise StageUnavailable(f"unknown stage {stage}")


class OptimizedAdapter(BaseAdapter):
    def __init__(
        self,
        label: str,
        fixture: Path,
        backend: str,
        valid_time: datetime | None,
    ) -> None:
        self.label = label
        self.fixture = fixture
        self.backend = backend
        self.valid_time = valid_time
        try:
            grib = importlib.import_module("sharpmod.backends.grib")
        except (ImportError, OSError) as exc:
            raise ImplementationUnavailable(
                "optimized GRIB module is unavailable"
            ) from exc
        module_name = (
            "sharpmod.backends.rust_backend"
            if backend == "rust"
            else "sharpmod.backends.python_backend"
        )
        class_name = "RustBackend" if backend == "rust" else "PythonBackend"
        try:
            backend_class = getattr(
                importlib.import_module(module_name), class_name
            )
            self.backend_instance = backend_class()
            self.decode = self.backend_instance.decode_grib_point
        except Exception as exc:
            raise ImplementationUnavailable(
                f"{class_name}.decode_grib_point is unavailable"
            ) from exc
        self.clear = getattr(grib, "clear_grib_caches", None)
        self.cache_info = getattr(grib, "grib_cache_info", None)
        if not callable(self.decode):
            raise ImplementationUnavailable(
                "sharpmod.backends.decode_grib_point is unavailable"
            )
        if not callable(self.clear):
            raise ImplementationUnavailable(
                "sharpmod.backends.grib.clear_grib_caches is unavailable"
            )
        self.activate()

    def _decode(self, lat: float, lon: float) -> Any:
        # Use the production adapter while retaining its exact-point cache
        # independently from the other implementation in an alternating run.
        return self.decode(
            str(self.fixture),
            float(lat),
            float(lon),
            missing=MISSING,
        )

    def _clear_all(self) -> None:
        if self.backend == "python":
            self.clear()
        clear_native = getattr(self.backend_instance, "clear_grib_cache", None)
        if callable(clear_native):
            clear_native()

    def _clear_point_work(self) -> None:
        try:
            self.clear(
                inventory=False,
                nearest=True,
                points=True,
                reset_stats=False,
            )
        except TypeError as exc:
            raise StageUnavailable(
                "optimized cache API cannot preserve the warm inventory"
            ) from exc

    def _cache_snapshot(self) -> Any:
        snapshot = (
            getattr(self.backend_instance, "grib_cache_info", None)
            if self.backend == "rust"
            else self.cache_info
        )
        if not callable(snapshot):
            return None
        try:
            value = snapshot()
            return dict(value) if isinstance(value, Mapping) else value
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def stage_session(
        self,
        stage: str,
        lat: float,
        lon: float,
        second_lat: float,
        second_lon: float,
    ) -> StageSession:
        if stage == "application-cold":

            def before() -> None:
                self.activate()
                self._clear_all()

            return StageSession(
                lambda: self._decode(lat, lon),
                before_sample=before,
                details={
                    "cache_state": "all application GRIB caches cleared",
                    "os_file_cache": "not flushed",
                },
            )

        if stage == "warm-inventory-point-miss":
            if self.backend == "rust":
                raise StageUnavailable(
                    "optimized Rust intentionally performs a direct message scan "
                    "and does not retain an inventory cache"
                )
            self.activate()
            self._clear_all()
            self._decode(lat, lon)

            def before() -> None:
                self.activate()
                self._clear_point_work()

            return StageSession(
                lambda: self._decode(second_lat, second_lon),
                before_sample=before,
                details={
                    "cache_state": (
                        "inventory retained; nearest and point work cleared"
                    ),
                    "point": [second_lat, second_lon],
                    "cache_after_setup": self._cache_snapshot(),
                },
            )

        if stage == "warm-dataset-point":
            raise StageUnavailable(
                "optimized decoder exposes bounded caches, not an open xarray session"
            )

        if stage == "point-cache-hit":
            self.activate()
            self._clear_all()
            self._decode(lat, lon)
            return StageSession(
                lambda: self._decode(lat, lon),
                before_sample=self.activate,
                details={
                    "cache_state": "same decoded-point key retained",
                    "cache_after_setup": self._cache_snapshot(),
                },
            )

        if stage == "profile-construction":
            self.activate()
            self._clear_all()
            point = self._decode(lat, lon)
            return StageSession(
                lambda: _build_profile(point),
                before_sample=self.activate,
                details={"scope": "Profile from already-decoded columns"},
            )

        if stage == "end-to-end":

            def before() -> None:
                self.activate()
                self._clear_all()

            def call() -> Any:
                point = self._decode(lat, lon)
                return point, _build_profile(point)

            return StageSession(
                call,
                before_sample=before,
                details={
                    "scope": "application-cold GRIB decode plus Profile",
                    "os_file_cache": "not flushed",
                },
            )

        raise StageUnavailable(f"unknown stage {stage}")


def _create_adapters(
    selected: Sequence[str],
    fixture: Path,
    valid_time: datetime | None,
    work_dir: Path,
) -> tuple[list[BaseAdapter], list[UnavailableRecord]]:
    adapters: list[BaseAdapter] = []
    unavailable: list[UnavailableRecord] = []
    for label in selected:
        backend = "rust" if label.endswith("rust") else "python"
        try:
            if label.startswith("old-"):
                adapter: BaseAdapter = LegacyAdapter(
                    label,
                    fixture,
                    backend,
                    valid_time,
                    work_dir,
                )
            else:
                adapter = OptimizedAdapter(
                    label,
                    fixture,
                    backend,
                    valid_time,
                )
            adapters.append(adapter)
        except Exception as exc:
            unavailable.append(
                UnavailableRecord(
                    label,
                    None,
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return adapters, unavailable


def _validate_equivalence(
    adapters: Sequence[BaseAdapter],
    lat: float,
    lon: float,
) -> dict[str, Any]:
    if not adapters:
        return {"status": "not-run", "reason": "no implementations available"}
    by_label: dict[str, Any] = {}
    for adapter in adapters:
        by_label[adapter.label] = adapter.decode_once(lat, lon)
    generation_references = {}
    vorticity = {}
    for generation, labels in {
        "old": ("old-python", "old-rust"),
        "optimized": ("optimized-python", "optimized-rust"),
    }.items():
        left_label, right_label = labels
        present = [label for label in labels if label in by_label]
        if present:
            generation_references[generation] = present[0]
        if left_label in by_label and right_label in by_label:
            _compare_points(
                by_label[left_label],
                by_label[right_label],
                f"{right_label} vs {left_label}",
            )
            vorticity[generation] = _compare_vorticity(
                by_label[left_label],
                by_label[right_label],
                f"{right_label} vs {left_label}",
            )

    cross_generation = None
    if set(generation_references) == {"old", "optimized"}:
        cross_generation = _compare_cross_generation(
            by_label[generation_references["old"]],
            by_label[generation_references["optimized"]],
            "optimized vs legacy",
        )

    reference_label = generation_references.get(
        "optimized", generation_references.get("old", next(iter(by_label)))
    )
    reference = by_label[reference_label]
    matrix = _point_matrix(reference)
    selected_lat, selected_lon = _point_coordinates(reference)
    generation_levels = {
        generation: int(_point_matrix(by_label[label]).shape[1])
        for generation, label in generation_references.items()
    }
    return {
        "status": "passed",
        "reference": reference_label,
        "implementations": list(by_label),
        "levels": int(matrix.shape[1]),
        "generation_levels": generation_levels,
        "cross_generation": cross_generation,
        "selected_lat": selected_lat,
        "selected_lon": selected_lon,
        "surface_relative_vorticity": vorticity,
    }


def _measure(
    adapters: Sequence[BaseAdapter],
    stages: Sequence[str],
    lat: float,
    lon: float,
    second_lat: float,
    second_lon: float,
    repeat: int,
    warmup: int,
) -> tuple[list[TimingRecord], list[UnavailableRecord]]:
    records: list[TimingRecord] = []
    unavailable: list[UnavailableRecord] = []
    descriptions = {
        "application-cold": "full local decode with application caches cleared",
        "warm-inventory-point-miss": "inventory/index warm, different point key",
        "warm-dataset-point": "point extraction from one retained loaded dataset",
        "point-cache-hit": "same point served from the decoded-point cache",
        "profile-construction": "Profile construction from decoded arrays only",
        "end-to-end": "application-cold local decode plus Profile construction",
    }
    for stage in stages:
        sessions: dict[str, StageSession] = {}
        for adapter in adapters:
            try:
                sessions[adapter.label] = adapter.stage_session(
                    stage,
                    lat,
                    lon,
                    second_lat,
                    second_lon,
                )
            except Exception as exc:
                unavailable.append(
                    UnavailableRecord(
                        adapter.label,
                        stage,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
        samples: dict[str, list[float]] = {
            label: [] for label in sessions
        }
        try:
            for session in sessions.values():
                for _ in range(warmup):
                    session.before_sample()
                    try:
                        result = session.call()
                        _consume_result(result)
                    finally:
                        session.after_sample()
            gc_was_enabled = gc.isenabled()
            try:
                gc.disable()
                labels = tuple(sessions)
                for repeat_index in range(repeat):
                    ordered = (
                        labels
                        if repeat_index % 2 == 0
                        else tuple(reversed(labels))
                    )
                    for label in ordered:
                        session = sessions[label]
                        session.before_sample()
                        try:
                            started = time.perf_counter_ns()
                            result = session.call()
                            elapsed = (
                                time.perf_counter_ns() - started
                            ) / 1_000_000_000.0
                            _consume_result(result)
                            samples[label].append(elapsed)
                        finally:
                            session.after_sample()
            finally:
                if gc_was_enabled:
                    gc.enable()
        finally:
            for session in sessions.values():
                session.close()
        for label, values in samples.items():
            records.append(
                TimingRecord(
                    label,
                    stage,
                    descriptions[stage],
                    tuple(values),
                    sessions[label].details,
                )
            )
    return records, unavailable


def _print_results(
    records: Sequence[TimingRecord],
    unavailable: Sequence[UnavailableRecord],
) -> None:
    headings = (
        "stage",
        "implementation",
        "samples",
        "median ms",
        "min ms",
        "max ms",
    )
    print(" | ".join(headings))
    print(" | ".join("---" for _ in headings))
    for record in records:
        samples = record.samples_seconds
        values = (
            record.stage,
            record.implementation,
            str(len(samples)),
            f"{statistics.median(samples) * 1000.0:.6f}",
            f"{min(samples) * 1000.0:.6f}",
            f"{max(samples) * 1000.0:.6f}",
        )
        print(" | ".join(values))
    if unavailable:
        print("\nUnavailable implementations/stages")
        for item in unavailable:
            scope = item.implementation
            if item.stage is not None:
                scope += f"/{item.stage}"
            print(f"- {scope}: {item.reason}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grib",
        type=Path,
        required=True,
        help="explicit local GRIB/GRIB2 fixture path",
    )
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument(
        "--second-lat",
        type=float,
        help="point-cache miss latitude (default: --lat + 0.05)",
    )
    parser.add_argument(
        "--second-lon",
        type=float,
        help="point-cache miss longitude (default: --lon + 0.05)",
    )
    parser.add_argument(
        "--valid-time",
        help="optional ISO timestamp used when a fixture contains several times",
    )
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument(
        "--implementations",
        nargs="+",
        choices=IMPLEMENTATIONS,
        default=list(IMPLEMENTATIONS),
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=STAGES,
        default=list(STAGES),
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        default=Path.cwd(),
        help="checkout whose optimized modules and git metadata are recorded",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="parent for temporary index directories (default: system temp)",
    )
    parser.add_argument("--output", type=Path, help="write complete JSON result")
    parser.add_argument(
        "--skip-equivalence",
        action="store_true",
        help="skip the pre-timing numerical comparison",
    )
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="fail if a requested implementation or stage is unavailable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    if arguments.warmup < 0:
        raise SystemExit("--warmup cannot be negative")
    fixture = arguments.grib.expanduser().resolve()
    if not fixture.is_file():
        raise SystemExit(f"GRIB fixture does not exist: {fixture}")
    checkout = arguments.checkout.expanduser().resolve()
    _activate_checkout(checkout)
    valid_time = _parse_datetime(arguments.valid_time)
    second_lat = (
        arguments.second_lat
        if arguments.second_lat is not None
        else min(90.0, arguments.lat + 0.05)
    )
    second_lon = (
        arguments.second_lon
        if arguments.second_lon is not None
        else arguments.lon + 0.05
    )
    work_parent = (
        None
        if arguments.work_dir is None
        else arguments.work_dir.expanduser().resolve()
    )
    if work_parent is not None:
        work_parent.mkdir(parents=True, exist_ok=True)

    prior_backend = os.environ.get("SHARPMOD_BACKEND")
    with tempfile.TemporaryDirectory(
        prefix="sharpmod-decode-benchmark-",
        dir=work_parent,
    ) as temporary:
        adapters, unavailable = _create_adapters(
            arguments.implementations,
            fixture,
            valid_time,
            Path(temporary),
        )
        if arguments.skip_equivalence:
            equivalence = {"status": "skipped"}
        else:
            equivalence = _validate_equivalence(
                adapters, arguments.lat, arguments.lon
            )
        records, stage_unavailable = _measure(
            adapters,
            arguments.stages,
            arguments.lat,
            arguments.lon,
            second_lat,
            second_lon,
            arguments.repeat,
            arguments.warmup,
        )
        unavailable.extend(stage_unavailable)

    if prior_backend is None:
        os.environ.pop("SHARPMOD_BACKEND", None)
    else:
        os.environ["SHARPMOD_BACKEND"] = prior_backend
    try:
        from sharpmod import backends

        backends.reset_backend_cache()
    except Exception:
        pass

    fixture_stat = fixture.stat()
    payload = {
        "schema_version": 1,
        "benchmark": "SHARPpy Reimagined local GRIB point decoding",
        "fixture": {
            "path": str(fixture),
            "size_bytes": fixture_stat.st_size,
            "mtime_ns": fixture_stat.st_mtime_ns,
            "sha256": _sha256(fixture),
        },
        "environment": _environment_metadata(checkout),
        "settings": {
            "lat": arguments.lat,
            "lon": arguments.lon,
            "second_lat": second_lat,
            "second_lon": second_lon,
            "valid_time": (
                None if valid_time is None else valid_time.isoformat()
            ),
            "repeat": arguments.repeat,
            "warmup": arguments.warmup,
            "implementations": list(arguments.implementations),
            "stages": list(arguments.stages),
            "application_cold_definition": (
                "application caches/indexes cleared; operating-system file "
                "cache is not flushed"
            ),
        },
        "equivalence": equivalence,
        "records": [record.as_dict() for record in records],
        "unavailable": [item.as_dict() for item in unavailable],
    }

    print("SHARPpy Reimagined local GRIB decoding benchmark")
    print(f"fixture: {fixture}")
    print(f"fixture sha256: {payload['fixture']['sha256']}")
    print(f"equivalence: {equivalence.get('status')}")
    print()
    _print_results(records, unavailable)
    if arguments.output is not None:
        output = arguments.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON: {output}")

    if arguments.require_all and unavailable:
        return 2
    if not records:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

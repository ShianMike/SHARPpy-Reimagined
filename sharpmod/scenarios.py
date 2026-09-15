"""Saved, reproducible hypothetical sounding sensitivity scenarios.

The baseline is copied once and never mutated.  Scenario operations describe
their units, vertical extent, and interpolation rule explicitly; applying one
always builds a fresh profile so the ordinary viewer's undo history and the
metrics cache cannot silently alter the original observation or forecast.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Callable, Iterable, Mapping
from uuid import uuid4

import numpy as np


SCENARIO_VERSION = 1
MAX_PERTURBATIONS = 24
MAX_SENSITIVITY_CELLS = 121
_CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg")
_THERMO_KINDS = {
    "surface_temperature",
    "surface_dewpoint",
    "moisture_layer",
    "cap_temperature",
}


class ScenarioError(ValueError):
    """A scenario document or requested perturbation is invalid."""


class ScenarioCancelled(RuntimeError):
    """A bounded sensitivity calculation stopped cooperatively."""


def _finite(value, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScenarioError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ScenarioError(f"{name} must be a finite number")
    return number


def _column(profile, name: str) -> tuple[float | None, ...]:
    source = getattr(profile, name, None)
    if source is None:
        return ()
    raw = np.ma.asarray(source, dtype=float).reshape(-1)
    data = np.asarray(raw.filled(np.nan), dtype=float)
    mask = np.ma.getmaskarray(raw) | ~np.isfinite(data) | (data <= -9998.0)
    return tuple(None if mask[index] else float(data[index]) for index in range(data.size))


def _masked(values: Iterable[float | None]):
    values = tuple(values)
    return np.ma.array(
        [np.nan if value is None else float(value) for value in values],
        mask=[value is None for value in values],
        dtype=float,
    )


def _iso(value) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScenarioError(f"invalid scenario timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ProfileSnapshot:
    """Portable immutable baseline columns and essential provenance."""

    pres: tuple[float | None, ...]
    hght: tuple[float | None, ...]
    tmpc: tuple[float | None, ...]
    dwpc: tuple[float | None, ...]
    wdir: tuple[float | None, ...]
    wspd: tuple[float | None, ...]
    omeg: tuple[float | None, ...]
    location: str
    valid_time: datetime | None
    latitude: float | None
    storm_motion: tuple[float, ...] | None = None

    def __post_init__(self):
        sizes = {len(getattr(self, name)) for name in _CORE_FIELDS if name != "omeg"}
        if sizes == {0} or len(sizes) != 1:
            raise ScenarioError("baseline profile columns must have one non-zero length")
        size = next(iter(sizes))
        if self.omeg and len(self.omeg) != size:
            raise ScenarioError("baseline omega column length does not match")
        if self.latitude is not None:
            latitude = _finite(self.latitude, name="latitude")
            if not -90.0 <= latitude <= 90.0:
                raise ScenarioError("latitude must be between -90 and 90 degrees")

    @classmethod
    def from_profile(cls, profile) -> "ProfileSnapshot":
        storm = getattr(profile, "srwind", None)
        try:
            storm = tuple(float(value) for value in storm) if storm is not None else None
        except (TypeError, ValueError):
            storm = None
        latitude = getattr(profile, "latitude", None)
        try:
            latitude = float(latitude) if latitude is not None else None
        except (TypeError, ValueError):
            latitude = None
        return cls(
            *(_column(profile, name) for name in _CORE_FIELDS),
            location=str(getattr(profile, "location", "") or ""),
            valid_time=_parse_time(getattr(profile, "date", None)),
            latitude=latitude,
            storm_motion=storm,
        )

    def to_profile(self):
        """Create a fresh profile compatible with the application's backend."""
        from sharppy.sharptab import profile as profile_module

        kwargs = {
            name: _masked(getattr(self, name))
            for name in _CORE_FIELDS
            if name != "omeg" or self.omeg
        }
        profile = profile_module.create_profile(
            profile="raw",
            location=self.location,
            date=self.valid_time,
            latitude=self.latitude if self.latitude is not None else 0.0,
            missing=-9999.0,
            **kwargs,
        )
        if self.storm_motion is not None:
            profile.srwind = tuple(self.storm_motion)
        return profile

    def as_dict(self) -> dict[str, object]:
        return {
            **{name: list(getattr(self, name)) for name in _CORE_FIELDS},
            "location": self.location,
            "valid_time": _iso(self.valid_time),
            "latitude": self.latitude,
            "storm_motion": (
                list(self.storm_motion) if self.storm_motion is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProfileSnapshot":
        try:
            columns = [tuple(value.get(name, ())) for name in _CORE_FIELDS]
            return cls(
                *columns,
                location=str(value.get("location", "")),
                valid_time=_parse_time(value.get("valid_time")),
                latitude=value.get("latitude"),
                storm_motion=(
                    tuple(value["storm_motion"])
                    if value.get("storm_motion") is not None
                    else None
                ),
            )
        except (KeyError, TypeError) as exc:
            raise ScenarioError("scenario baseline is malformed") from exc


@dataclass(frozen=True)
class Perturbation:
    """One physically bounded hypothetical change."""

    kind: str
    amount: float
    units: str
    bottom_m_agl: float | None = None
    top_m_agl: float | None = None
    interpolation: str = "surface-only"
    secondary_amount: float | None = None

    def __post_init__(self):
        kind = str(self.kind).strip().lower()
        if kind not in {*_THERMO_KINDS, "storm_motion"}:
            raise ScenarioError(f"unsupported perturbation kind {kind!r}")
        object.__setattr__(self, "kind", kind)
        amount = _finite(self.amount, name="perturbation amount")
        object.__setattr__(self, "amount", amount)
        expected_units = "kt" if kind == "storm_motion" else "degC"
        if str(self.units) != expected_units:
            raise ScenarioError(f"{kind} perturbations use {expected_units}")
        if abs(amount) > (100.0 if kind == "storm_motion" else 30.0):
            raise ScenarioError(f"{kind} perturbation is outside the supported bound")

        bottom = self.bottom_m_agl
        top = self.top_m_agl
        if kind == "moisture_layer":
            bottom = 0.0 if bottom is None else _finite(bottom, name="layer bottom")
            top = _finite(top, name="layer top")
            rules = {"uniform", "linear-taper"}
        elif kind == "cap_temperature":
            bottom = _finite(bottom, name="cap bottom")
            top = _finite(top, name="cap top")
            rules = {"uniform", "triangle"}
        elif kind == "storm_motion":
            if self.secondary_amount is None:
                raise ScenarioError("storm motion needs both u and v changes")
            secondary = _finite(self.secondary_amount, name="v-wind change")
            if abs(secondary) > 100.0:
                raise ScenarioError("storm-motion perturbation is outside the bound")
            object.__setattr__(self, "secondary_amount", secondary)
            bottom = top = None
            rules = {"vector-delta"}
        else:
            bottom = top = 0.0
            rules = {"surface-only"}
        rule = str(self.interpolation).strip().lower()
        if rule not in rules:
            raise ScenarioError(
                f"{kind} interpolation must be one of {', '.join(sorted(rules))}"
            )
        if bottom is not None and (bottom < 0.0 or bottom > 20_000.0):
            raise ScenarioError("vertical bounds must be within 0-20 km AGL")
        if kind in {"moisture_layer", "cap_temperature"} and (
            top <= float(bottom) or top > 20_000.0
        ):
            raise ScenarioError("layer top must be above its bottom and at most 20 km")
        object.__setattr__(self, "bottom_m_agl", bottom)
        object.__setattr__(self, "top_m_agl", top)
        object.__setattr__(self, "interpolation", rule)

    @property
    def description(self) -> str:
        if self.kind == "storm_motion":
            return (
                f"storm motion Δu={self.amount:+g} kt, "
                f"Δv={self.secondary_amount:+g} kt; vector delta"
            )
        if self.kind.startswith("surface_"):
            return f"{self.kind.replace('_', ' ')} {self.amount:+g} °C; surface only"
        return (
            f"{self.kind.replace('_', ' ')} {self.amount:+g} °C from "
            f"{self.bottom_m_agl:g}-{self.top_m_agl:g} m AGL; "
            f"{self.interpolation}"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "amount": self.amount,
            "units": self.units,
            "bottom_m_agl": self.bottom_m_agl,
            "top_m_agl": self.top_m_agl,
            "interpolation": self.interpolation,
            "secondary_amount": self.secondary_amount,
        }


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    perturbations: tuple[Perturbation, ...]
    created_at: datetime
    hypothetical: bool = True

    def __post_init__(self):
        if not str(self.scenario_id).strip():
            raise ScenarioError("scenario id must not be empty")
        name = str(self.name).strip()
        if not name:
            raise ScenarioError("scenario name must not be empty")
        if len(self.perturbations) > MAX_PERTURBATIONS:
            raise ScenarioError(f"a scenario supports at most {MAX_PERTURBATIONS} changes")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "created_at", _parse_time(self.created_at))

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.scenario_id,
            "name": self.name,
            "hypothetical": True,
            "created_at": _iso(self.created_at),
            "perturbations": [item.as_dict() for item in self.perturbations],
        }


@dataclass(frozen=True)
class MetricDelta:
    key: str
    baseline: float | None
    scenario: float | None
    delta: float | None


@dataclass(frozen=True)
class SensitivityCell:
    temperature_delta_c: float
    dewpoint_delta_c: float
    metrics: Mapping[str, float | None]
    deltas: Mapping[str, float | None]


@dataclass(frozen=True)
class SensitivityGrid:
    temperature_deltas_c: tuple[float, ...]
    dewpoint_deltas_c: tuple[float, ...]
    cells: tuple[SensitivityCell, ...]
    requested_cell_count: int
    cancelled: bool
    moisture_depth_m_agl: float

    @property
    def completed_cell_count(self) -> int:
        return len(self.cells)

    def cell(self, temperature_delta_c: float, dewpoint_delta_c: float):
        return next(
            (
                item
                for item in self.cells
                if item.temperature_delta_c == float(temperature_delta_c)
                and item.dewpoint_delta_c == float(dewpoint_delta_c)
            ),
            None,
        )


def _profile_arrays(snapshot: ProfileSnapshot) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(
            [np.nan if value is None else float(value) for value in getattr(snapshot, name)],
            dtype=float,
        )
        for name in _CORE_FIELDS
    }


def _surface_index(arrays: Mapping[str, np.ndarray]) -> int:
    pressure = arrays["pres"]
    valid = np.flatnonzero(np.isfinite(pressure))
    if not valid.size:
        raise ScenarioError("baseline has no valid pressure level")
    return int(valid[np.argmax(pressure[valid])])


def _apply_perturbations(
    baseline: ProfileSnapshot,
    perturbations: Iterable[Perturbation],
) -> ProfileSnapshot:
    arrays = _profile_arrays(baseline)
    surface = _surface_index(arrays)
    surface_height = arrays["hght"][surface]
    if not math.isfinite(surface_height):
        raise ScenarioError("baseline surface height is unavailable")
    agl = arrays["hght"] - surface_height
    storm = baseline.storm_motion
    for item in tuple(perturbations):
        if item.kind == "surface_temperature":
            arrays["tmpc"][surface] += item.amount
        elif item.kind == "surface_dewpoint":
            arrays["dwpc"][surface] += item.amount
        elif item.kind in {"moisture_layer", "cap_temperature"}:
            target = "dwpc" if item.kind == "moisture_layer" else "tmpc"
            inside = (
                np.isfinite(agl)
                & (agl >= float(item.bottom_m_agl))
                & (agl <= float(item.top_m_agl))
            )
            if not np.any(inside):
                raise ScenarioError(
                    f"{item.kind.replace('_', ' ')} contains no reported level"
                )
            weights = np.ones_like(agl)
            if item.interpolation == "linear-taper":
                span = float(item.top_m_agl) - float(item.bottom_m_agl)
                weights = 1.0 - (agl - float(item.bottom_m_agl)) / span
            elif item.interpolation == "triangle":
                bottom = float(item.bottom_m_agl)
                top = float(item.top_m_agl)
                middle = (bottom + top) / 2.0
                half = (top - bottom) / 2.0
                weights = 1.0 - np.abs(agl - middle) / half
            arrays[target][inside] += item.amount * weights[inside]
        elif item.kind == "storm_motion":
            if storm is None or len(storm) < 2:
                raise ScenarioError(
                    "baseline has no storm-motion vector to perturb"
                )
            changed = list(storm)
            changed[0] += item.amount
            changed[1] += float(item.secondary_amount)
            storm = tuple(changed)

    valid_temperature = np.isfinite(arrays["tmpc"])
    valid_dewpoint = np.isfinite(arrays["dwpc"])
    if np.any(arrays["tmpc"][valid_temperature] < -120.0) or np.any(
        arrays["tmpc"][valid_temperature] > 70.0
    ):
        raise ScenarioError("temperature perturbation produced an unsupported value")
    if np.any(arrays["dwpc"][valid_dewpoint] < -130.0) or np.any(
        arrays["dwpc"][valid_dewpoint] > 60.0
    ):
        raise ScenarioError("dewpoint perturbation produced an unsupported value")
    supersaturated = valid_temperature & valid_dewpoint & (
        arrays["dwpc"] > arrays["tmpc"] + 0.01
    )
    if np.any(supersaturated):
        level = int(np.flatnonzero(supersaturated)[0])
        raise ScenarioError(
            f"perturbation makes dewpoint exceed temperature at level {level}"
        )
    values = {
        name: tuple(None if not math.isfinite(value) else float(value) for value in array)
        for name, array in arrays.items()
    }
    return replace(baseline, **values, storm_motion=storm)


class ScenarioBook:
    """Mutable named-scenario catalog around one immutable baseline."""

    def __init__(self, baseline: ProfileSnapshot, *, source: Mapping | None = None):
        self.baseline = baseline
        self.source = MappingProxyType(dict(source or {}))
        self._scenarios: dict[str, Scenario] = {}
        self._profile_cache: dict[str, object] = {}
        self._metric_cache: dict[tuple[str, tuple[str, ...]], tuple[MetricDelta, ...]] = {}

    @property
    def scenarios(self) -> tuple[Scenario, ...]:
        return tuple(self._scenarios.values())

    def scenario(self, scenario_id: str) -> Scenario:
        try:
            return self._scenarios[str(scenario_id)]
        except KeyError as exc:
            raise ScenarioError(f"unknown scenario {scenario_id!r}") from exc

    def _unique_name(self, name: str, *, ignore: str | None = None) -> str:
        name = str(name).strip()
        if not name:
            raise ScenarioError("scenario name must not be empty")
        if any(
            item.name.casefold() == name.casefold() and item.scenario_id != ignore
            for item in self._scenarios.values()
        ):
            raise ScenarioError(f"a scenario named {name!r} already exists")
        return name

    def create(
        self,
        name: str,
        perturbations: Iterable[Perturbation],
        *,
        scenario_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Scenario:
        changes = tuple(perturbations)
        # Validate the complete combination now rather than failing later when
        # a user opens or exports what appeared to be a saved scenario.
        _apply_perturbations(self.baseline, changes)
        scenario = Scenario(
            scenario_id or uuid4().hex,
            self._unique_name(name),
            changes,
            created_at or datetime.now(timezone.utc),
        )
        if scenario.scenario_id in self._scenarios:
            raise ScenarioError(f"duplicate scenario id {scenario.scenario_id!r}")
        self._scenarios[scenario.scenario_id] = scenario
        return scenario

    def rename(self, scenario_id: str, name: str) -> Scenario:
        scenario = self.scenario(scenario_id)
        changed = replace(
            scenario,
            name=self._unique_name(name, ignore=scenario.scenario_id),
        )
        self._scenarios[scenario.scenario_id] = changed
        return changed

    def duplicate(self, scenario_id: str, name: str | None = None) -> Scenario:
        source = self.scenario(scenario_id)
        return self.create(name or f"{source.name} copy", source.perturbations)

    def reset(self, scenario_id: str) -> Scenario:
        scenario = self.scenario(scenario_id)
        changed = replace(scenario, perturbations=())
        self._scenarios[scenario.scenario_id] = changed
        self._invalidate(scenario.scenario_id)
        return changed

    def replace_perturbations(
        self, scenario_id: str, perturbations: Iterable[Perturbation]
    ) -> Scenario:
        scenario = self.scenario(scenario_id)
        changes = tuple(perturbations)
        _apply_perturbations(self.baseline, changes)
        changed = replace(scenario, perturbations=changes)
        self._scenarios[scenario.scenario_id] = changed
        self._invalidate(scenario.scenario_id)
        return changed

    def remove(self, scenario_id: str) -> None:
        scenario = self.scenario(scenario_id)
        del self._scenarios[scenario.scenario_id]
        self._invalidate(scenario.scenario_id)

    def _invalidate(self, scenario_id: str) -> None:
        self._profile_cache.pop(scenario_id, None)
        for key in tuple(self._metric_cache):
            if key[0] == scenario_id:
                del self._metric_cache[key]

    def profile(self, scenario_id: str | None = None):
        if scenario_id in (None, "", "baseline"):
            return self.baseline.to_profile()
        scenario = self.scenario(str(scenario_id))
        cached = self._profile_cache.get(scenario.scenario_id)
        if cached is None:
            cached = _apply_perturbations(
                self.baseline, scenario.perturbations
            ).to_profile()
            self._profile_cache[scenario.scenario_id] = cached
        return cached

    def metric_deltas(self, engine, scenario_id: str, keys: Iterable[str]):
        wanted = tuple(dict.fromkeys(str(key) for key in keys))
        cache_key = (str(scenario_id), wanted)
        cached = self._metric_cache.get(cache_key)
        if cached is not None:
            return cached
        baseline_values = engine.values(self.profile(), wanted)
        scenario_values = engine.values(self.profile(scenario_id), wanted)
        rows = tuple(
            MetricDelta(
                key,
                baseline_values.get(key),
                scenario_values.get(key),
                (
                    scenario_values[key] - baseline_values[key]
                    if scenario_values.get(key) is not None
                    and baseline_values.get(key) is not None
                    else None
                ),
            )
            for key in wanted
        )
        self._metric_cache[cache_key] = rows
        return rows

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "sharpmod-scenarios",
            "version": SCENARIO_VERSION,
            "baseline": self.baseline.as_dict(),
            "source": dict(self.source),
            "scenarios": [item.as_dict() for item in self.scenarios],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ScenarioBook":
        if value.get("format") != "sharpmod-scenarios":
            raise ScenarioError("not a SHARPpy Reimagined scenario document")
        if int(value.get("version", -1)) != SCENARIO_VERSION:
            raise ScenarioError("unsupported scenario document version")
        baseline = value.get("baseline")
        if not isinstance(baseline, Mapping):
            raise ScenarioError("scenario document has no baseline")
        book = cls(
            ProfileSnapshot.from_dict(baseline),
            source=value.get("source") if isinstance(value.get("source"), Mapping) else {},
        )
        raw_scenarios = value.get("scenarios", ())
        if not isinstance(raw_scenarios, list):
            raise ScenarioError("scenario list is malformed")
        for raw in raw_scenarios:
            if not isinstance(raw, Mapping):
                raise ScenarioError("scenario entry is malformed")
            changes = tuple(
                Perturbation(**item)
                for item in raw.get("perturbations", ())
                if isinstance(item, Mapping)
            )
            book.create(
                str(raw.get("name", "")),
                changes,
                scenario_id=str(raw.get("id", "")),
                created_at=_parse_time(raw.get("created_at")),
            )
        return book


def run_sensitivity_grid(
    book: ScenarioBook,
    engine,
    temperature_deltas_c: Iterable[float],
    dewpoint_deltas_c: Iterable[float],
    keys: Iterable[str],
    *,
    moisture_depth_m_agl: float = 1000.0,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> SensitivityGrid:
    """Evaluate a bounded grid, retaining every completed cell on cancel."""
    temperatures = tuple(_finite(value, name="temperature delta") for value in temperature_deltas_c)
    dewpoints = tuple(_finite(value, name="dewpoint delta") for value in dewpoint_deltas_c)
    if not temperatures or not dewpoints:
        raise ScenarioError("sensitivity axes must not be empty")
    if len(temperatures) * len(dewpoints) > MAX_SENSITIVITY_CELLS:
        raise ScenarioError(
            f"sensitivity grid is limited to {MAX_SENSITIVITY_CELLS} cells"
        )
    depth = _finite(moisture_depth_m_agl, name="moisture depth")
    wanted = tuple(dict.fromkeys(str(key) for key in keys))
    baseline = engine.values(book.profile(), wanted)
    cells = []
    total = len(temperatures) * len(dewpoints)
    was_cancelled = False
    for temperature in temperatures:
        for dewpoint in dewpoints:
            if cancelled is not None and cancelled():
                was_cancelled = True
                break
            changes = (
                Perturbation(
                    "surface_temperature", temperature, "degC", interpolation="surface-only"
                ),
                Perturbation(
                    "moisture_layer",
                    dewpoint,
                    "degC",
                    bottom_m_agl=0.0,
                    top_m_agl=depth,
                    interpolation="linear-taper",
                ),
            )
            profile = _apply_perturbations(book.baseline, changes).to_profile()
            values = engine.values(profile, wanted)
            deltas = {
                key: (
                    values[key] - baseline[key]
                    if values.get(key) is not None and baseline.get(key) is not None
                    else None
                )
                for key in wanted
            }
            cells.append(
                SensitivityCell(
                    temperature,
                    dewpoint,
                    MappingProxyType(dict(values)),
                    MappingProxyType(deltas),
                )
            )
            if progress is not None:
                progress(len(cells), total)
        if was_cancelled:
            break
    return SensitivityGrid(
        temperatures,
        dewpoints,
        tuple(cells),
        total,
        was_cancelled,
        depth,
    )


def scenario_from_cell(
    book: ScenarioBook,
    cell: SensitivityCell,
    *,
    moisture_depth_m_agl: float,
    name: str | None = None,
) -> Scenario:
    return book.create(
        name
        or f"T {cell.temperature_delta_c:+g} C / Td {cell.dewpoint_delta_c:+g} C",
        (
            Perturbation(
                "surface_temperature",
                cell.temperature_delta_c,
                "degC",
                interpolation="surface-only",
            ),
            Perturbation(
                "moisture_layer",
                cell.dewpoint_delta_c,
                "degC",
                bottom_m_agl=0.0,
                top_m_agl=moisture_depth_m_agl,
                interpolation="linear-taper",
            ),
        ),
    )


def scenario_collection(book: ScenarioBook, scenario_id: str, template_collection):
    """Build a one-member collection identified as a hypothetical scenario."""
    from sharppy.sharptab.prof_collection import ProfCollection

    scenario = book.scenario(scenario_id)
    profile = book.profile(scenario_id)
    valid = book.baseline.valid_time
    metadata = dict(getattr(template_collection, "_meta", {}) or {})
    metadata.update(
        {
            "scenario": True,
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.name,
            "scenario_hypothetical": True,
            "scenario_perturbations": [item.as_dict() for item in scenario.perturbations],
            "profile_edited": True,
            "loc": f"{metadata.get('loc', book.baseline.location)} — {scenario.name} (hypothetical)",
        }
    )
    target_type = getattr(template_collection, "_target_type", None)
    kwargs = {"target_type": target_type} if target_type is not None else {}
    return ProfCollection({"scenario": [profile]}, [valid], **kwargs, **metadata)


def write_scenario_book(path, book: ScenarioBook) -> Path:
    """Atomically write a portable scenario catalog."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(book.as_dict(), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(handle)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_scenario_book(path) -> ScenarioBook:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"scenario document could not be read: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ScenarioError("scenario document root must be an object")
    return ScenarioBook.from_dict(value)


__all__ = [
    "MAX_SENSITIVITY_CELLS",
    "MetricDelta",
    "Perturbation",
    "ProfileSnapshot",
    "SCENARIO_VERSION",
    "Scenario",
    "ScenarioBook",
    "ScenarioCancelled",
    "ScenarioError",
    "SensitivityCell",
    "SensitivityGrid",
    "read_scenario_book",
    "run_sensitivity_grid",
    "scenario_collection",
    "scenario_from_cell",
    "write_scenario_book",
]

"""Core result types and per-profile calculations for box-analysis fields.

The public ``box_analysis`` module coordinates these types with fast and composite
calculation tiers; sampling plans are produced by ``box_sounding`` and consumed by
analysis and map workflows."""

from __future__ import annotations

from dataclasses import dataclass
from sharpmod.analysis.box_sounding import BoxSamplePlan
from typing import Mapping
import numpy as np
from sharpmod.analysis import box_analysis as _api


@dataclass(frozen=True)
class EnvelopeResult:
    """Per-level spread of one profile column across every sounding in a box.

    This answers the question a single sounding cannot: not "what does the
    atmosphere look like here" but "how much does it differ across this area,
    and at which levels". A box whose envelope is a narrow ribbon is one
    airmass; a box whose envelope fans out at low levels has a boundary in it.
    """

    field: str
    levels: tuple[float, ...]
    minimum: tuple[float | None, ...]
    low: tuple[float | None, ...]
    median: tuple[float | None, ...]
    high: tuple[float | None, ...]
    maximum: tuple[float | None, ...]
    #: How many soundings contributed at each level. Falls off near the ground,
    #: where higher terrain has already ended.
    counts: tuple[int, ...]
    #: Every contributing column, for optional spaghetti rendering.
    columns: tuple[tuple[float | None, ...], ...]
    percentiles: tuple[float, float]

    @property
    def soundings(self) -> int:
        return len(self.columns)

    def spread_at(self, pressure) -> float | None:
        """Return max minus min at the ladder level nearest ``pressure``."""
        if not self.levels:
            return None
        target = float(pressure)
        index = min(
            range(len(self.levels)),
            key=lambda position: abs(self.levels[position] - target),
        )
        low = self.minimum[index]
        high = self.maximum[index]
        if low is None or high is None:
            return None
        return high - low

    @property
    def widest_level(self) -> float | None:
        """The pressure at which the box disagrees with itself the most."""
        best = None
        best_span = None
        for index, level in enumerate(self.levels):
            low = self.minimum[index]
            high = self.maximum[index]
            if low is None or high is None:
                continue
            span = high - low
            if best_span is None or span > best_span:
                best, best_span = level, span
        return best


@dataclass(frozen=True)
class BoxAnalysis:
    """Scalar fields for every node of one box sample plan."""

    plan: BoxSamplePlan
    points: tuple[_api.BoxPointAnalysis, ...]
    tiers: tuple[str, ...]
    run_time: object = None
    valid_time: object = None
    fxx: int = 0

    @property
    def rows(self) -> int:
        return self.plan.rows

    @property
    def cols(self) -> int:
        return self.plan.cols

    @property
    def analyzed(self) -> tuple[_api.BoxPointAnalysis, ...]:
        """Nodes that produced at least one value."""
        return tuple(point for point in self.points if point.ok)

    @property
    def failures(self) -> tuple[_api.BoxPointAnalysis, ...]:
        return tuple(point for point in self.points if point.error is not None)

    def point_at(self, row, col) -> _api.BoxPointAnalysis | None:
        """Return the node at ``(row, col)``, or ``None`` when out of range."""
        if not 0 <= row < self.rows or not 0 <= col < self.cols:
            return None
        index = int(row) * self.cols + int(col)
        try:
            return self.points[index]
        except IndexError:
            return None

    def available_parameters(self) -> tuple[_api.BoxParameter, ...]:
        """Return registry entries that at least one node produced.

        This is what a picker should offer: a field that is blank everywhere is
        not a choice, it is a dead end.
        """
        present = set()
        for point in self.points:
            present.update(point.values)
        return tuple(item for item in _api.PARAMETERS if item.key in present)

    def field(self, key) -> tuple[tuple[float | None, ...], ...]:
        """Return the field as ``rows`` tuples of ``cols`` optional values.

        Row 0 is the north edge, matching :class:`~sharpmod.analysis.box_sounding.\
        BoxSamplePoint` and raster order.
        """
        item = _api.parameter(key)
        grid = []
        for row in range(self.rows):
            line = []
            for col in range(self.cols):
                point = self.point_at(row, col)
                line.append(None if point is None else point.value(item.key))
            grid.append(tuple(line))
        return tuple(grid)

    def values_of(self, key) -> tuple[float, ...]:
        """Return every present value for one field, unordered."""
        item = _api.parameter(key)
        return tuple(
            point.values[item.key] for point in self.points if item.key in point.values
        )

    def statistics(self, key) -> _api.BoxFieldStats | None:
        """Return area statistics for one field, or ``None`` when it is empty."""
        item = _api.parameter(key)
        present = [point for point in self.points if item.key in point.values]
        if not present:
            return None
        numbers = np.asarray([point.values[item.key] for point in present], dtype=float)
        extreme_index = (
            int(np.argmax(numbers))
            if item.notable == "high"
            else int(np.argmin(numbers))
        )
        return _api.BoxFieldStats(
            parameter=item,
            count=int(numbers.size),
            minimum=float(np.min(numbers)),
            maximum=float(np.max(numbers)),
            mean=float(np.mean(numbers)),
            median=float(np.median(numbers)),
            p10=float(np.percentile(numbers, 10.0)),
            p90=float(np.percentile(numbers, 90.0)),
            extreme=present[extreme_index],
        )

    def ranked(self, key, *, limit=None) -> tuple[_api.BoxPointAnalysis, ...]:
        """Return nodes ordered with the most notable value first."""
        item = _api.parameter(key)
        present = [point for point in self.points if item.key in point.values]
        present.sort(
            key=lambda point: point.values[item.key],
            reverse=item.notable == "high",
        )
        if limit is not None:
            present = present[: max(0, int(limit))]
        return tuple(present)

    def extreme(self, key) -> _api.BoxPointAnalysis | None:
        """Return the single most notable node for one field."""
        stats = self.statistics(key)
        return None if stats is None else stats.extreme

    def field_transect(self, key, *, orientation="ew", index=None):
        """Return one row or column of a field with its along-box distances.

        ``orientation`` is ``"ew"`` for a west-to-east row or ``"ns"`` for a
        north-to-south column. ``index`` defaults to the middle of the box,
        which is the slice a forecaster means by "across the box".

        Returns ``(distances_km, values, points)``.
        """
        item = _api.parameter(key)
        region = self.plan.region
        if orientation not in {"ew", "ns"}:
            raise _api.BoxAnalysisError('orientation must be "ew" or "ns"')
        if orientation == "ew":
            row = self.rows // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for col in range(self.cols)]
            total = region.width_km
        else:
            col = self.cols // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for row in range(self.rows)]
            total = region.height_km
        if any(node is None for node in nodes):
            raise _api.BoxAnalysisError("transect index is outside the box")
        count = len(nodes)
        step = 0.0 if count < 2 else total / float(count - 1)
        distances = tuple(step * position for position in range(count))
        values = tuple(node.value(item.key) for node in nodes)
        return distances, values, tuple(nodes)

    def vertical_transect(
        self,
        *,
        field="tmpc",
        orientation="ew",
        index=None,
        levels=_api.DEFAULT_TRANSECT_LEVELS,
    ):
        """Return a pressure-by-distance slice of one raw profile column.

        Reads the columns captured during analysis, so no profile is re-decoded
        to draw a slice. Only a caller supplying its own ``levels`` falls back to
        re-reading the files.

        Returns ``(distances_km, levels, grid)`` where ``grid[level][node]`` is
        the interpolated value or ``None`` below ground / above the profile top.
        """
        from sharpmod import backends

        if field not in _api.TRANSECT_FIELDS:
            raise _api.BoxAnalysisError(
                f"transect field must be one of {', '.join(_api.TRANSECT_FIELDS)}"
            )
        if orientation not in {"ew", "ns"}:
            raise _api.BoxAnalysisError('orientation must be "ew" or "ns"')
        region = self.plan.region
        if orientation == "ew":
            row = self.rows // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for col in range(self.cols)]
            total = region.width_km
        else:
            col = self.cols // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for row in range(self.rows)]
            total = region.height_km
        if any(node is None for node in nodes):
            raise _api.BoxAnalysisError("transect index is outside the box")

        ladder = np.asarray([float(value) for value in levels], dtype=float)
        if ladder.size == 0 or not np.all(ladder > 0.0):
            raise _api.BoxAnalysisError("transect levels must all be positive hPa")
        # Interpolate linearly in log-pressure, which is how a sounding is read.
        # Note the backend's own ``log=`` flag does the opposite of what is
        # wanted here: it exponentiates the *result*, for callers recovering a
        # pressure. The coordinate is therefore transformed explicitly and the
        # flag left off.
        log_ladder = np.log10(ladder)
        columns = [_api._interpolated_column(node, field, log_ladder) for node in nodes]

        count = len(nodes)
        step = 0.0 if count < 2 else total / float(count - 1)
        distances = tuple(step * position for position in range(count))
        grid = tuple(
            tuple(
                column[level_index] if level_index < len(column) else None
                for column in columns
            )
            for level_index in range(ladder.size)
        )
        return distances, tuple(float(v) for v in ladder), grid

    @property
    def cell_area_km2(self) -> float:
        """Approximate ground area one sample node represents, in km2."""
        from sharpmod.analysis.box_sounding import KM_PER_DEG_LAT, km_per_deg_lon

        region = self.plan.region
        dlat = (
            region.lat_span / float(self.rows - 1) if self.rows > 1 else region.lat_span
        )
        dlon = (
            region.lon_span / float(self.cols - 1) if self.cols > 1 else region.lon_span
        )
        return dlat * KM_PER_DEG_LAT * dlon * km_per_deg_lon(region.center_lat)

    def _resolve_criteria(self, criteria) -> tuple[_api.Criterion, ...]:
        """Accept a screen name, one Criterion, or an iterable of them."""
        if isinstance(criteria, str):
            return _api.screen(criteria)
        if isinstance(criteria, _api.Criterion):
            return (criteria,)
        resolved = tuple(criteria or ())
        if not resolved:
            raise _api.BoxAnalysisError("at least one criterion is required")
        for item in resolved:
            if not isinstance(item, _api.Criterion):
                raise _api.BoxAnalysisError(
                    "criteria must be Criterion values or a screen name"
                )
        return resolved

    def mask(self, criteria) -> tuple[tuple[bool | None, ...], ...]:
        """Return a rows-by-cols grid of ``True``/``False``/``None``.

        ``None`` marks a node that could not be judged because it lacks one of
        the fields the criteria need. ``criteria`` may be a screen name, one
        :class:`Criterion`, or any iterable of them.
        """
        resolved = self._resolve_criteria(criteria)
        grid = []
        for row in range(self.rows):
            line = []
            for col in range(self.cols):
                point = self.point_at(row, col)
                line.append(
                    None if point is None else _api._evaluate(resolved, point.values)
                )
            grid.append(tuple(line))
        return tuple(grid)

    def coverage(self, criteria) -> _api.CoverageResult:
        """Return how much of the box satisfies ``criteria``, and which nodes.

        This is the question a single field cannot answer: not "where is CAPE
        largest" but "where are all of these true at once, and over how much
        ground".
        """
        resolved = self._resolve_criteria(criteria)
        matched = []
        evaluated = 0
        unknown = 0
        for point in self.points:
            verdict = _api._evaluate(resolved, point.values)
            if verdict is None:
                unknown += 1
                continue
            evaluated += 1
            if verdict:
                matched.append(point)
        return _api.CoverageResult(
            criteria=resolved,
            matched=tuple(matched),
            evaluated=evaluated,
            unknown=unknown,
            total=len(self.points),
            cell_area_km2=self.cell_area_km2,
        )

    def screens(self) -> tuple[str, ...]:
        """Return the screen names every field of which this box can evaluate.

        A screen whose fields were never computed is not offered: it would report
        zero coverage and read as "nothing here" rather than "not asked".
        """
        present = set()
        for point in self.points:
            present.update(point.values)
        return tuple(
            name
            for name, criteria in _api.INGREDIENT_SCREENS.items()
            if all(item.parameter in present for item in criteria)
        )

    def envelope(
        self,
        *,
        field="tmpc",
        levels=_api.DEFAULT_TRANSECT_LEVELS,
        percentiles=(10.0, 90.0),
    ) -> _api.EnvelopeResult:
        """Return the per-level spread of one profile column across the box.

        Every extracted sounding is interpolated onto a shared log-pressure
        ladder and reduced to min / low percentile / median / high percentile /
        max at each level. Levels below a node's terrain simply do not
        contribute, which is why :attr:`EnvelopeResult.counts` thins out near the
        ground instead of the envelope quietly widening there.
        """
        if field not in _api.TRANSECT_FIELDS:
            raise _api.BoxAnalysisError(
                f"envelope field must be one of {', '.join(_api.TRANSECT_FIELDS)}"
            )
        try:
            low_pct, high_pct = (float(value) for value in percentiles)
        except (TypeError, ValueError) as exc:
            raise _api.BoxAnalysisError("percentiles must be two numbers") from exc
        if not 0.0 <= low_pct < high_pct <= 100.0:
            raise _api.BoxAnalysisError("percentiles must be ascending and within [0, 100]")
        ladder = np.asarray([float(value) for value in levels], dtype=float)
        if ladder.size == 0 or not np.all(ladder > 0.0):
            raise _api.BoxAnalysisError("envelope levels must all be positive hPa")
        log_ladder = np.log10(ladder)

        columns = []
        for point in self.points:
            column = _api._interpolated_column(point, field, log_ladder)
            if any(value is not None for value in column):
                columns.append(tuple(column))

        minimum, low, median, high, maximum, counts = [], [], [], [], [], []
        for index in range(ladder.size):
            present = [column[index] for column in columns if column[index] is not None]
            counts.append(len(present))
            if not present:
                for series in (minimum, low, median, high, maximum):
                    series.append(None)
                continue
            values = np.asarray(present, dtype=float)
            if field == "wdir":
                # Keep a cluster straddling north contiguous (350, 10 becomes
                # 350, 370) before reducing it.  Normal scalar percentiles
                # would instead invent a southerly median and a 340-degree
                # spread from a narrow northerly cluster.
                radians = np.deg2rad(np.mod(values, 360.0))
                center = (
                    np.degrees(
                        np.arctan2(
                            np.mean(np.sin(radians)),
                            np.mean(np.cos(radians)),
                        )
                    )
                    % 360.0
                )
                if center < 180.0 and np.any(values > 180.0):
                    center += 360.0
                values = center + ((values - center + 180.0) % 360.0 - 180.0)
            minimum.append(float(np.min(values)))
            low.append(float(np.percentile(values, low_pct)))
            median.append(float(np.median(values)))
            high.append(float(np.percentile(values, high_pct)))
            maximum.append(float(np.max(values)))

        return _api.EnvelopeResult(
            field=str(field),
            levels=tuple(float(value) for value in ladder),
            minimum=tuple(minimum),
            low=tuple(low),
            median=tuple(median),
            high=tuple(high),
            maximum=tuple(maximum),
            counts=tuple(counts),
            columns=tuple(columns),
            percentiles=(low_pct, high_pct),
        )

    def summary(self, keys=None) -> str:
        """Return a reader-facing table of area statistics."""
        items = (
            self.available_parameters()
            if keys is None
            else tuple(_api.parameter(key) for key in keys)
        )
        lines = [
            f"{'Field':24s} {'Min':>10s} {'Mean':>10s} {'Max':>10s}  Extreme",
        ]
        for item in items:
            stats = self.statistics(item.key)
            if stats is None:
                continue
            node = stats.extreme
            lines.append(
                f"{item.label:24s} "
                f"{item.format(stats.minimum):>10s} "
                f"{item.format(stats.mean):>10s} "
                f"{item.format(stats.maximum):>10s}"
                f"  {node.lat:.2f},{node.lon:.2f}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class BoxSequence:
    """One box analyzed at several forecast hours.

    A single box says where the atmosphere is favourable. A sequence says *when*,
    which is usually the harder half of the question: a field can be strong at
    F12, gone by F18, and the peak can sit between the hours a forecaster would
    otherwise have checked.
    """

    hours: tuple[int, ...]
    analyses: Mapping[int, _api.BoxAnalysis]
    run_time: object = None

    @property
    def plan(self):
        """The shared sample plan. Every hour samples the same lattice."""
        first = self.at(self.hours[0]) if self.hours else None
        return None if first is None else first.plan

    def at(self, hour) -> _api.BoxAnalysis | None:
        """Return the analysis for one forecast hour, if present."""
        try:
            return self.analyses.get(int(hour))
        except (TypeError, ValueError):
            return None

    def available_parameters(self) -> tuple[_api.BoxParameter, ...]:
        """Fields every hour can show.

        The intersection rather than the union: a field that exists at only some
        hours would make the slider appear to lose and regain data.
        """
        if not self.hours:
            return ()
        shared = None
        for hour in self.hours:
            analysis = self.at(hour)
            keys = (
                set()
                if analysis is None
                else {item.key for item in analysis.available_parameters()}
            )
            shared = keys if shared is None else (shared & keys)
        shared = shared or set()
        return tuple(item for item in _api.PARAMETERS if item.key in shared)

    def statistics_series(self, key):
        """Return ``((hour, BoxFieldStats | None), ...)`` for one field."""
        item = _api.parameter(key)
        series = []
        for hour in self.hours:
            analysis = self.at(hour)
            series.append(
                (
                    hour,
                    None if analysis is None else analysis.statistics(item.key),
                )
            )
        return tuple(series)

    def coverage_series(self, criteria):
        """Return ``((hour, CoverageResult | None), ...)`` for a criteria set."""
        series = []
        for hour in self.hours:
            analysis = self.at(hour)
            if analysis is None:
                series.append((hour, None))
                continue
            try:
                series.append((hour, analysis.coverage(criteria)))
            except _api.BoxAnalysisError:
                series.append((hour, None))
        return tuple(series)

    def peak_hour(self, key) -> int | None:
        """Return the hour whose extreme is the most notable for one field."""
        item = _api.parameter(key)
        best_hour = None
        best_value = None
        for hour, stats in self.statistics_series(item.key):
            if stats is None:
                continue
            value = stats.maximum if item.notable == "high" else stats.minimum
            if best_value is None or (
                value > best_value if item.notable == "high" else value < best_value
            ):
                best_hour, best_value = hour, value
        return best_hour

    def peak_coverage_hour(self, criteria) -> int | None:
        """Return the hour with the largest qualifying area."""
        best_hour = None
        best_count = None
        for hour, coverage in self.coverage_series(criteria):
            if coverage is None:
                continue
            if best_count is None or coverage.count > best_count:
                best_hour, best_count = hour, coverage.count
        return best_hour

    def summary(self, key) -> str:
        """Return a per-hour table of one field's range across the box."""
        item = _api.parameter(key)
        lines = [
            f"{item.label}" + (f" ({item.units})" if item.units else ""),
            f"{'Hour':>6s} {'Min':>10s} {'Mean':>10s} {'Max':>10s}",
        ]
        peak = self.peak_hour(item.key)
        for hour, stats in self.statistics_series(item.key):
            if stats is None:
                lines.append(f"F{hour:03d}".rjust(6) + "         no data")
                continue
            marker = "  <- peak" if hour == peak else ""
            lines.append(
                f"F{hour:03d}".rjust(6)
                + f" {item.format(stats.minimum):>10s}"
                + f" {item.format(stats.mean):>10s}"
                + f" {item.format(stats.maximum):>10s}{marker}"
            )
        return "\n".join(lines)

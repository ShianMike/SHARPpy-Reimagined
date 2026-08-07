"""Reproducible 2015-2025 historical TOI case catalogue from official sources.

Outcomes come from the NOAA NCEI Storm Events bulk CSV export.  This module
reduces those tornado segments to one record per CONUS convective day, applies
the named :func:`~sharpmod.guidance.toi_dataset.high_risk_worthy_proxy_v1`
screen, and samples a stratified catalogue of outbreak, ordinary-severe, and
null/control days across regions, seasons, forecast leads, and HRRR operational
eras.

Two properties are load-bearing:

* **No observation leakage into predictors.** A catalogue entry carries the
  observed counts only as the *label* input.  The forecast anchor is never taken
  from where tornadoes were later reported; entries default to
  ``model_forecast_maximum_stp``, which the archive runner resolves at run time
  from a fixed CONUS centre using forecast fields alone.
* **Honest frequency.** Real high-end tornado days are rare.  The catalogue
  records the true population base rate it measured, so
  ``--weights population`` can restore it after deliberate oversampling of
  positives.

Every retrieval records the exact source URL, file name, retrieval date, byte
count, and SHA-256 so a dataset can be traced to the precise export used.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import os
import re
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .toi_archive import (
    HRRR_ARCHIVE_FIRST_YEAR,
    HRRR_ARCHIVE_LAST_YEAR,
    HRRR_F18_AVAILABLE_FROM,
    NCEI_STORM_EVENTS_BASE_URL,
    NCEI_STORM_EVENTS_LICENSE,
    maximum_available_forecast_hour,
)
from .toi_dataset import TOI_CASE_CLASSES, TOIDatasetError, high_risk_worthy_proxy_v1
from .toi_evaluation import strict_json_dumps
from .toi_strata import conus_region, hrrr_era, season_name

TOI_CATALOG_VERSION = "sharpmod_toi_catalog_v1"
USER_AGENT = "SHARPpy-Reimagined/toi-catalog (+https://github.com/ShianMike)"

#: The published TOI window starts at 06Z the day before the event, so the
#: model cycle for an event day D is 06Z on D-1.
DEFAULT_CYCLE_HOUR = 6
DEFAULT_CYCLE_OFFSET_DAYS = 1

#: Forecast hours rotated across catalogue entries so the dataset spans leads
#: rather than concentrating on one.  All lie inside the 18-hour window.
DEFAULT_FORECAST_HOURS = (6, 12, 18)

#: EF-scale text as it appears in the NCEI ``TOR_F_SCALE`` column.
_EF_PATTERN = re.compile(r"EF\s*(\d)", re.IGNORECASE)

#: Ordinary-severe screen: a real tornado day that is clearly not high-end.
SEVERE_MINIMUM_TORNADOES = 1

#: MEASURED in the 2026-08-05 pilot: a genuinely quiet day (2025-02-18) has no
#: connected forecast proxy-STP region anywhere, so TOI is undefined and the
#: runner correctly skips it.  Completely quiet days are therefore *outside TOI's
#: domain of applicability* and cannot supply negative cases.  Negatives must
#: come from days where TOI is computable but the outcome was not high-end.
#: Sampling nulls from the convective season concentrates them where a risk
#: region is likely to exist; the remaining skips are reported, never hidden.
NULL_SEASON_MONTHS = (3, 4, 5, 6, 7, 8)
NULL_DOMAIN_NOTE = (
    "Quiet-day controls are frequently skipped because no forecast proxy-STP "
    "region exists, which means TOI is undefined rather than negative. Null "
    "controls are drawn from convective-season days without tornado reports so "
    "a risk region is usually present; every skip is still recorded with its "
    "reason."
)


class TOICatalogError(ValueError):
    """Raised when an outcome export cannot be used to build a catalogue."""


@dataclass(frozen=True)
class SourceFile:
    """One retrieved outcome file, recorded for provenance."""

    url: str
    name: str
    retrieved_at: str
    bytes: int
    sha256: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "name": self.name,
            "retrieved_at": self.retrieved_at,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class TornadoDay:
    """Aggregated observed tornado outcome for one CONUS calendar day."""

    date: str
    tornado_count: int
    ef2_plus_count: int
    ef3_plus_count: int
    ef4_plus_count: int
    longest_ef2_plus_path_miles: float
    centroid_latitude: float | None
    centroid_longitude: float | None

    @property
    def observed(self) -> dict[str, Any]:
        """The exact input consumed by ``high_risk_worthy_proxy_v1``."""

        return {
            "tornado_count": self.tornado_count,
            "ef2_plus_count": self.ef2_plus_count,
            "ef3_plus_count": self.ef3_plus_count,
            "ef4_plus_count": self.ef4_plus_count,
            "longest_ef2_plus_path_miles": self.longest_ef2_plus_path_miles,
        }

    @property
    def is_high_end(self) -> bool:
        return high_risk_worthy_proxy_v1(self.observed) == 1

    def to_mapping(self) -> dict[str, Any]:
        payload = {"date": self.date, **self.observed}
        payload["observed_centroid_latitude"] = self.centroid_latitude
        payload["observed_centroid_longitude"] = self.centroid_longitude
        return payload


def _ef_number(text: str) -> int | None:
    match = _EF_PATTERN.search(str(text or ""))
    return int(match.group(1)) if match else None


def _float(value: Any) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0  # reject NaN


def ncei_detail_urls(
    years: Sequence[int],
    *,
    listing: str | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> dict[int, str]:
    """Resolve the newest ``StormEvents_details`` URL for each requested year.

    NCEI encodes a creation date in each file name, so the listing must be read
    rather than guessed; the resolved name is recorded as the dataset version.
    """

    if listing is None:
        reader = fetch or _http_get
        listing = reader(NCEI_STORM_EVENTS_BASE_URL).decode("utf-8", "replace")
    found: dict[int, tuple[str, str]] = {}
    pattern = re.compile(
        r"StormEvents_details-ftp_v1\.0_d(\d{4})_c(\d{8})\.csv\.gz"
    )
    for match in pattern.finditer(listing):
        year, created = int(match.group(1)), match.group(2)
        if year not in {int(item) for item in years}:
            continue
        current = found.get(year)
        if current is None or created > current[1]:
            found[year] = (match.group(0), created)
    missing = sorted({int(item) for item in years}.difference(found))
    if missing:
        raise TOICatalogError(
            "NCEI Storm Events detail files not found for year(s): "
            + ",".join(str(year) for year in missing)
        )
    return {
        year: NCEI_STORM_EVENTS_BASE_URL + name
        for year, (name, _created) in sorted(found.items())
    }


def _http_get(url: str, *, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def load_tornado_days(
    urls: Mapping[int, str],
    *,
    fetch: Callable[[str], bytes] | None = None,
) -> tuple[dict[str, TornadoDay], tuple[SourceFile, ...]]:
    """Aggregate NCEI tornado segments into one record per calendar day."""

    reader = fetch or _http_get
    accumulator: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "tornado_count": 0,
            "ef2_plus_count": 0,
            "ef3_plus_count": 0,
            "ef4_plus_count": 0,
            "longest_ef2_plus_path_miles": 0.0,
            "latitudes": [],
            "longitudes": [],
        }
    )
    sources: list[SourceFile] = []
    for _year, url in sorted(urls.items()):
        raw = reader(url)
        sources.append(
            SourceFile(
                url=url,
                name=url.rsplit("/", 1)[-1],
                retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
        try:
            text = gzip.decompress(raw).decode("utf-8", "replace")
        except OSError:
            text = raw.decode("utf-8", "replace")
        for row in csv.DictReader(io.StringIO(text)):
            if str(row.get("EVENT_TYPE", "")).strip().casefold() != "tornado":
                continue
            begin = str(row.get("BEGIN_DATE_TIME", "")).strip()
            date = _ncei_date(begin, row)
            if date is None:
                continue
            bucket = accumulator[date]
            bucket["tornado_count"] += 1
            scale = _ef_number(row.get("TOR_F_SCALE"))
            length = _float(row.get("TOR_LENGTH"))
            if scale is not None and scale >= 2:
                bucket["ef2_plus_count"] += 1
                bucket["longest_ef2_plus_path_miles"] = max(
                    bucket["longest_ef2_plus_path_miles"], length
                )
                if scale >= 3:
                    bucket["ef3_plus_count"] += 1
                if scale >= 4:
                    bucket["ef4_plus_count"] += 1
            latitude = _float(row.get("BEGIN_LAT"))
            longitude = _float(row.get("BEGIN_LON"))
            if latitude and longitude:
                bucket["latitudes"].append(latitude)
                bucket["longitudes"].append(longitude)

    days: dict[str, TornadoDay] = {}
    for date, bucket in accumulator.items():
        latitudes = bucket.pop("latitudes")
        longitudes = bucket.pop("longitudes")
        days[date] = TornadoDay(
            date=date,
            tornado_count=int(bucket["tornado_count"]),
            ef2_plus_count=int(bucket["ef2_plus_count"]),
            ef3_plus_count=int(bucket["ef3_plus_count"]),
            ef4_plus_count=int(bucket["ef4_plus_count"]),
            longest_ef2_plus_path_miles=float(
                bucket["longest_ef2_plus_path_miles"]
            ),
            centroid_latitude=(
                sum(latitudes) / len(latitudes) if latitudes else None
            ),
            centroid_longitude=(
                sum(longitudes) / len(longitudes) if longitudes else None
            ),
        )
    return days, tuple(sources)


def _ncei_date(begin: str, row: Mapping[str, Any]) -> str | None:
    """Parse an NCEI date, preferring the explicit YEARMONTH/DAY columns."""

    year_month = str(row.get("BEGIN_YEARMONTH", "")).strip()
    day = str(row.get("BEGIN_DAY", "")).strip()
    if len(year_month) == 6 and day.isdigit():
        return f"{year_month[:4]}-{year_month[4:]}-{int(day):02d}"
    for pattern in ("%d-%b-%y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(begin, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class CatalogPlan:
    """How many cases of each class to sample, and how to spread them."""

    positive_cases: int = 60
    severe_cases: int = 240
    null_cases: int = 300
    forecast_hours: tuple[int, ...] = DEFAULT_FORECAST_HOURS
    first_year: int = HRRR_ARCHIVE_FIRST_YEAR
    last_year: int = HRRR_ARCHIVE_LAST_YEAR
    cycle_hour: int = DEFAULT_CYCLE_HOUR
    cycle_offset_days: int = DEFAULT_CYCLE_OFFSET_DAYS
    anchor_source: str = "model_forecast_maximum_stp"
    #: Draw null controls from the convective season only.  See
    #: :data:`NULL_DOMAIN_NOTE` for the measured reason.
    null_convective_season_only: bool = True
    seed: int = 20260805

    def __post_init__(self) -> None:
        for name in ("positive_cases", "severe_cases", "null_cases"):
            if int(getattr(self, name)) < 0:
                raise TOICatalogError(f"{name} must be non-negative")
        if not self.forecast_hours:
            raise TOICatalogError("forecast_hours must not be empty")
        if self.last_year < self.first_year:
            raise TOICatalogError("last_year must not precede first_year")

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(range(int(self.first_year), int(self.last_year) + 1))

    @property
    def total_cases(self) -> int:
        return int(self.positive_cases + self.severe_cases + self.null_cases)

    def to_mapping(self) -> dict[str, Any]:
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__}
        payload["forecast_hours"] = list(self.forecast_hours)
        payload["years"] = list(self.years)
        payload["total_cases"] = self.total_cases
        return payload


def _cycle_for(date: str, plan: CatalogPlan) -> datetime:
    event_day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return event_day - timedelta(days=int(plan.cycle_offset_days)) + timedelta(
        hours=int(plan.cycle_hour)
    )


def _null_dates(
    plan: CatalogPlan,
    tornado_dates: set[str],
    *,
    convective_season_only: bool = True,
) -> list[str]:
    """Deterministic control days with no reported tornadoes.

    Restricted to the convective season by default: an out-of-season quiet day
    usually has no forecast risk region at all, which makes TOI undefined rather
    than negative and simply wastes a download.  See :data:`NULL_DOMAIN_NOTE`.
    """

    months = set(NULL_SEASON_MONTHS) if convective_season_only else set(range(1, 13))
    candidates: list[str] = []
    for year in plan.years:
        cursor = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year, 12, 31, tzinfo=timezone.utc)
        while cursor <= end:
            date = cursor.strftime("%Y-%m-%d")
            if date not in tornado_dates and cursor.month in months:
                candidates.append(date)
            cursor += timedelta(days=1)
    return candidates


def _stride_sample(items: Sequence[Any], count: int) -> list[Any]:
    """Take an evenly spread deterministic sample without shuffling."""

    total = len(items)
    wanted = min(int(count), total)
    if wanted <= 0:
        return []
    if wanted == total:
        return list(items)
    step = total / wanted
    return [items[min(total - 1, int(index * step))] for index in range(wanted)]


def build_case_catalog(
    days: Mapping[str, TornadoDay],
    *,
    plan: CatalogPlan | None = None,
    sources: Sequence[SourceFile] = (),
    label_source: str | None = None,
) -> dict[str, Any]:
    """Build a stratified, leakage-safe manifest payload for 2015-2025."""

    plan = plan or CatalogPlan()
    in_window = {
        date: day
        for date, day in days.items()
        if plan.first_year <= int(date[:4]) <= plan.last_year
    }
    if not in_window:
        raise TOICatalogError(
            "no observed tornado days fall inside "
            f"{plan.first_year}-{plan.last_year}"
        )
    high_end = sorted(date for date, day in in_window.items() if day.is_high_end)
    severe = sorted(
        date
        for date, day in in_window.items()
        if not day.is_high_end
        and day.tornado_count >= SEVERE_MINIMUM_TORNADOES
    )
    nulls = _null_dates(
        plan,
        set(in_window),
        convective_season_only=plan.null_convective_season_only,
    )

    population_days = len(nulls) + len(severe) + len(high_end)
    population_base_rate = len(high_end) / population_days if population_days else 0.0

    selections = (
        ("outbreak", _stride_sample(high_end, plan.positive_cases)),
        ("severe", _stride_sample(severe, plan.severe_cases)),
        ("null", _stride_sample(nulls, plan.null_cases)),
    )

    cases: list[dict[str, Any]] = []
    clamped_hours = 0
    for case_class, dates in selections:
        for index, date in enumerate(dates):
            cycle = _cycle_for(date, plan)
            forecast_hour = plan.forecast_hours[index % len(plan.forecast_hours)]
            # MEASURED: at 06Z the archive publishes nothing beyond F15 before
            # HRRRv2 (2016-08-23), so requesting F18 there is guaranteed to
            # fail.  Clamp to the largest planned hour the cycle can actually
            # serve rather than queueing a case that can only ever error.
            #
            # Clamping down to a planned hour (12) instead of to the true
            # maximum (15) is deliberate: an f015 bin would contain only
            # pre-HRRRv2 cases, so forecast lead would be perfectly confounded
            # with model era and neither stratum could be interpreted.
            available = maximum_available_forecast_hour(cycle)
            if forecast_hour > available:
                usable = [
                    hour for hour in sorted(plan.forecast_hours) if hour <= available
                ]
                if not usable:
                    raise TOICatalogError(
                        f"cycle {cycle.isoformat()} publishes only F{available:03d}, "
                        "which no planned forecast hour fits"
                    )
                forecast_hour = usable[-1]
                clamped_hours += 1
            day = in_window.get(date)
            observed = (
                day.observed
                if day is not None
                else {
                    "tornado_count": 0,
                    "ef2_plus_count": 0,
                    "ef3_plus_count": 0,
                    "ef4_plus_count": 0,
                    "longest_ef2_plus_path_miles": 0.0,
                }
            )
            cases.append(
                {
                    # One event id per convective day; every cycle for that day
                    # therefore shares one event_year.
                    "event_id": f"{case_class}-{date}",
                    "case_class": case_class,
                    "run_time": cycle.isoformat(),
                    "forecast_hour": int(forecast_hour),
                    # A placeholder anchor: the runner replaces it with the
                    # forecast proxy-STP maximum found from a fixed CONUS
                    # centre.  Observed tornado locations are never used.
                    "latitude": 39.0,
                    "longitude": -98.0,
                    "anchor_source": plan.anchor_source,
                    "observed": observed,
                    "notes": f"event day {date}",
                }
            )
    if not cases:
        raise TOICatalogError("catalogue plan selected no cases")

    counts = {
        name: sum(1 for case in cases if case["case_class"] == name)
        for name in TOI_CASE_CLASSES
    }
    missing = [name for name, value in counts.items() if value == 0]
    if missing:
        raise TOICatalogError(
            "a calibration catalogue needs outbreak, severe, and null cases; "
            "missing: " + ", ".join(missing)
        )

    strata = _catalog_strata(cases)
    # Measured archive limitation: 06Z cycles before HRRRv2 top out at F15, so
    # those cases can only ever reach 15 h of coverage and are reported as
    # degraded.  Surface the count up front instead of discovering it mid-run.
    legacy = [
        case
        for case in cases
        if maximum_available_forecast_hour(datetime.fromisoformat(case["run_time"]))
        < 18
    ]
    return {
        "target_definition": "high_risk_worthy_proxy_v1",
        "label_source": label_source
        or (
            "NOAA NCEI Storm Events bulk CSV export; files and hashes recorded "
            "in catalog_sources"
        ),
        "dataset_kind": "historical",
        "population_base_rate": round(max(1e-6, population_base_rate), 6),
        "notes": (
            "Generated by sharpmod toi_catalog; anchors resolved from forecast "
            "fields at issuance, never from observed tornado locations."
        ),
        "catalog_version": TOI_CATALOG_VERSION,
        "catalog_plan": plan.to_mapping(),
        "catalog_sources": {
            "outcomes": [item.to_mapping() for item in sources],
            "license": NCEI_STORM_EVENTS_LICENSE,
        },
        "catalog_population": {
            "observed_tornado_days": len(in_window),
            "high_end_days": len(high_end),
            "ordinary_severe_days": len(severe),
            "null_days": len(nulls),
            "population_days": population_days,
            "population_base_rate": round(population_base_rate, 6),
        },
        "catalog_counts": counts,
        "catalog_strata": strata,
        "catalog_sampling_limits": {
            "f18_available_from": HRRR_F18_AVAILABLE_FROM.isoformat(),
            "legacy_f15_cases": len(legacy),
            "legacy_f15_fraction": (
                round(len(legacy) / len(cases), 4) if cases else 0.0
            ),
            "forecast_hours_clamped": clamped_hours,
            "forecast_hour_clamp_note": (
                "Requested forecast hours above what a cycle publishes were "
                "clamped down to the largest planned hour it can serve, so no "
                "case is queued that could only ever fail. Clamping targets a "
                "planned hour rather than the true maximum so forecast lead "
                "does not become perfectly confounded with model era."
            ),
            "note": (
                "Cases before HRRRv2 reach only 15 h of coverage because the "
                "archive publishes no F18 at 06Z; they remain usable but are "
                "reported as degraded, and per-era skill must be inspected."
            ),
            "null_domain_note": NULL_DOMAIN_NOTE,
            "null_convective_season_only": plan.null_convective_season_only,
        },
        "cases": cases,
        "experimental_not_official": True,
    }


def _catalog_strata(cases: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Summarize planned coverage of season, era, lead, region, and year."""

    result: dict[str, dict[str, int]] = {
        "season": defaultdict(int),
        "hrrr_era": defaultdict(int),
        "forecast_hour": defaultdict(int),
        "event_year": defaultdict(int),
        "planned_anchor_region": defaultdict(int),
    }
    for case in cases:
        cycle = datetime.fromisoformat(str(case["run_time"]))
        result["season"][season_name(cycle.month)] += 1
        result["hrrr_era"][hrrr_era(cycle)] += 1
        result["forecast_hour"][f"f{int(case['forecast_hour']):03d}"] += 1
        result["event_year"][str(cycle.year)] += 1
        result["planned_anchor_region"][
            conus_region(case["latitude"], case["longitude"])
        ] += 1
    return {key: dict(sorted(value.items())) for key, value in result.items()}


def save_catalog(payload: Mapping[str, Any], path: str | os.PathLike[str]) -> str:
    """Write a catalogue manifest as strict JSON."""

    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(strict_json_dumps(payload, indent=2))
        handle.write("\n")
    return target


@dataclass(frozen=True)
class CatalogSummary:
    """Compact description of a generated catalogue, for CLI output."""

    total_cases: int
    counts: Mapping[str, int]
    population_base_rate: float
    strata: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CatalogSummary:
        if "cases" not in payload:
            raise TOIDatasetError("catalogue payload has no cases")
        return cls(
            total_cases=len(payload["cases"]),
            counts=dict(payload.get("catalog_counts", {})),
            population_base_rate=float(payload.get("population_base_rate", 0.0)),
            strata=payload.get("catalog_strata", {}),
        )


__all__ = [
    "DEFAULT_CYCLE_HOUR",
    "DEFAULT_FORECAST_HOURS",
    "SEVERE_MINIMUM_TORNADOES",
    "TOI_CATALOG_VERSION",
    "CatalogPlan",
    "CatalogSummary",
    "SourceFile",
    "TOICatalogError",
    "TornadoDay",
    "build_case_catalog",
    "load_tornado_days",
    "ncei_detail_urls",
    "save_catalog",
]

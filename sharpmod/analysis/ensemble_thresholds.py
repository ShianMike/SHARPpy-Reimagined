"""Member-wise ensemble ingredient threshold evaluation.

Missing values are never treated as failed conditions.  A member belongs to
exactly one of three groups for a definition at a valid time: qualifying,
nonqualifying, or unusable.  Fractions use only the common usable subset while
coverage remains relative to the originally requested ensemble.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from sharpmod.analysis.ensemble_members import EnsembleAcquisition


FORMAT = "sharpmod-ensemble-thresholds"
VERSION = 1
OPERATORS = frozenset({">", ">=", "<", "<=", "==", "between"})
DISCLAIMER = (
    "Ensemble ingredient agreement; not a calibrated severe-weather probability."
)

#: Where one member stands for one definition at one valid time. Exactly one of
#: these applies, and together they partition the requested ensemble -- which is
#: what makes the displayed counts reconcile.
#:
#: The three unusable states were previously one undifferentiated outcome. A
#: member that never downloaded, a member with no profile at this exact time, a
#: member whose diagnostics raised, and a member missing a required diagnostic are
#: four different problems with four different remedies, and collapsing them told
#: a reader only that the number was smaller than expected.
MEMBER_QUALIFYING = "qualifying"
MEMBER_NONQUALIFYING = "nonqualifying"
MEMBER_UNUSABLE = "unusable"
MEMBER_NO_PROFILE = "no_profile"
MEMBER_FAILED = "failed"
MEMBER_NOT_LOADED = "not_loaded"
MEMBER_NOT_EVALUATED = "not_evaluated"

MEMBER_STATES = (
    MEMBER_QUALIFYING,
    MEMBER_NONQUALIFYING,
    MEMBER_UNUSABLE,
    MEMBER_NO_PROFILE,
    MEMBER_FAILED,
    MEMBER_NOT_LOADED,
    MEMBER_NOT_EVALUATED,
)

#: States that were loaded but produced no verdict. Distinct from
#: :data:`MEMBER_NOT_LOADED`, which never reached the evaluator at all.
LOADED_UNUSABLE_STATES = (
    MEMBER_UNUSABLE,
    MEMBER_NO_PROFILE,
    MEMBER_FAILED,
    MEMBER_NOT_EVALUATED,
)

#: One canonical phrase per state, so the member table, the status line, the
#: accessible description, and the exported CSV cannot describe the same member
#: differently. Mirrors ``timeline_frames.STATE_LABELS``.
MEMBER_STATE_LABELS = MappingProxyType(
    {
        MEMBER_QUALIFYING: "qualifies",
        MEMBER_NONQUALIFYING: "does not qualify",
        MEMBER_UNUSABLE: "loaded, a required diagnostic is unavailable",
        MEMBER_NO_PROFILE: "loaded, no profile at this valid time",
        MEMBER_FAILED: "loaded, diagnostic calculation failed",
        MEMBER_NOT_LOADED: "not loaded",
        MEMBER_NOT_EVALUATED: "not evaluated, the run was cancelled first",
    }
)

#: Short forms for a table cell, where the long phrase would not fit.
MEMBER_STATE_SHORT_LABELS = MappingProxyType(
    {
        MEMBER_QUALIFYING: "Qualifies",
        MEMBER_NONQUALIFYING: "Does not qualify",
        MEMBER_UNUSABLE: "Diagnostic unavailable",
        MEMBER_NO_PROFILE: "No profile at this time",
        MEMBER_FAILED: "Calculation failed",
        MEMBER_NOT_LOADED: "Not loaded",
        MEMBER_NOT_EVALUATED: "Not evaluated",
    }
)


def fraction_text(numerator, denominator, *, decimals: int = 0) -> str:
    """Render a fraction with its denominator visible, never as a bare percentage.

    A lone ``50%`` cannot be checked and cannot be reconciled against anything: it
    hides whether it came from one member of two or from fifteen of thirty, and
    those carry very different weight. Every ratio this module reports therefore
    shows the counts that produced it.
    """
    try:
        below = int(denominator)
        above = int(numerator)
    except (TypeError, ValueError):
        return "\u2014"
    if below <= 0:
        return "\u2014"
    share = above / below
    return f"{above}/{below} ({share * 100:.{max(0, int(decimals))}f}%)"


class ThresholdError(ValueError):
    """A threshold definition or ensemble collection is invalid."""


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > -9998.0 else None


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    checker = getattr(cancel, "is_set", None)
    return bool(checker()) if callable(checker) else bool(cancel)


@dataclass(frozen=True)
class ThresholdCondition:
    metric: str
    operator: str
    value: float
    upper: float | None = None
    units: str = ""

    def __post_init__(self):
        metric = str(self.metric).strip()
        operator = str(self.operator).strip()
        value = _finite(self.value)
        upper = _finite(self.upper)
        if not metric:
            raise ThresholdError("a threshold metric is required")
        if operator not in OPERATORS:
            raise ThresholdError(f"unsupported threshold operator: {operator}")
        if value is None:
            raise ThresholdError("threshold value must be finite")
        if operator == "between":
            if upper is None or upper < value:
                raise ThresholdError(
                    "between requires an upper value at least as large"
                )
        elif upper is not None:
            raise ThresholdError("upper is only valid with the between operator")
        object.__setattr__(self, "metric", metric)
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "units", str(self.units or ""))

    def evaluate(self, candidate) -> bool | None:
        number = _finite(candidate)
        if number is None:
            return None
        if self.operator == ">":
            return number > self.value
        if self.operator == ">=":
            return number >= self.value
        if self.operator == "<":
            return number < self.value
        if self.operator == "<=":
            return number <= self.value
        if self.operator == "==":
            return math.isclose(number, self.value, rel_tol=1e-9, abs_tol=1e-9)
        return self.value <= number <= self.upper

    @property
    def summary(self) -> str:
        """Render the rule the way a reader states it, e.g. ``MLCAPE >= 1000 J/kg``.

        The definition was only ever shown as a name the reader typed, so a saved
        or restored definition called "Ingredient agreement" said nothing about
        what it actually tested. Units come from the registry's display form, so
        the sentence matches every other number on screen.
        """
        from sharpmod.analysis.box_analysis import parameter as _parameter

        try:
            item = _parameter(self.metric)
            label = item.label
            units = item.display_units
            decimals = item.decimals
        except Exception:  # noqa: BLE001 - an unknown key still names itself
            label = self.metric.replace("_", " ").upper()
            units = self.units
            decimals = 1
        suffix = f" {units}" if units else ""
        low = f"{self.value:.{max(0, int(decimals))}f}"
        if self.operator == "between":
            high = f"{self.upper:.{max(0, int(decimals))}f}"
            return f"{label} between {low} and {high}{suffix}"
        symbol = {">=": "\u2265", "<=": "\u2264", "==": "="}.get(
            self.operator, self.operator
        )
        return f"{label} {symbol} {low}{suffix}"

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "operator": self.operator,
            "value": self.value,
            "upper": self.upper,
            "units": self.units,
        }


@dataclass(frozen=True)
class ThresholdDefinition:
    name: str
    conditions: tuple[ThresholdCondition, ...]

    def __post_init__(self):
        name = str(self.name).strip()
        conditions = tuple(self.conditions)
        if not name:
            raise ThresholdError("a threshold name is required")
        if not conditions:
            raise ThresholdError("at least one threshold condition is required")
        if len({condition.metric for condition in conditions}) != len(conditions):
            raise ThresholdError("a metric may appear only once in a joint threshold")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "conditions", conditions)

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(condition.metric for condition in self.conditions)

    @property
    def joint(self) -> bool:
        return len(self.conditions) > 1

    @property
    def summary(self) -> str:
        """Every condition, joined by ``and`` because a joint rule is a conjunction."""
        return " and ".join(condition.summary for condition in self.conditions)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "conditions": [condition.as_dict() for condition in self.conditions],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        try:
            conditions = tuple(
                ThresholdCondition(**dict(item))
                for item in payload.get("conditions", ())
            )
        except (TypeError, ValueError) as exc:
            raise ThresholdError(f"threshold conditions are malformed: {exc}") from exc
        return cls(str(payload.get("name", "")), conditions)


@dataclass(frozen=True)
class ThresholdMemberResult:
    member: str
    loaded: bool
    usable: bool
    satisfied: bool | None
    values: Mapping[str, float | None]
    reason: str | None = None
    #: Which of :data:`MEMBER_STATES` this member is in. Left blank by an external
    #: caller, it is derived from the flags; the evaluator sets it explicitly
    #: because three of the states are indistinguishable from the flags alone.
    state: str = ""

    def __post_init__(self):
        if self.state:
            if self.state not in MEMBER_STATES:
                raise ThresholdError(f"unsupported member state {self.state!r}")
            return
        if self.satisfied is True:
            derived = MEMBER_QUALIFYING
        elif self.satisfied is False:
            derived = MEMBER_NONQUALIFYING
        elif not self.loaded:
            derived = MEMBER_NOT_LOADED
        else:
            derived = MEMBER_UNUSABLE
        object.__setattr__(self, "state", derived)

    @property
    def state_label(self) -> str:
        return MEMBER_STATE_LABELS.get(self.state, self.state)

    @property
    def short_state_label(self) -> str:
        return MEMBER_STATE_SHORT_LABELS.get(self.state, self.state)

    @property
    def excluded(self) -> bool:
        """Whether this member contributed to no qualifying/nonqualifying count."""
        return self.state not in (MEMBER_QUALIFYING, MEMBER_NONQUALIFYING)


@dataclass(frozen=True)
class ThresholdResult:
    definition: ThresholdDefinition
    valid_time: datetime | None
    requested_member_count: int
    loaded_member_count: int
    usable_member_count: int
    qualifying_member_count: int
    members: tuple[ThresholdMemberResult, ...]
    cancelled: bool = False

    @property
    def agreement_fraction(self) -> float | None:
        if not self.usable_member_count:
            return None
        return self.qualifying_member_count / self.usable_member_count

    @property
    def requested_coverage(self) -> float | None:
        if not self.requested_member_count:
            return None
        return self.usable_member_count / self.requested_member_count

    @property
    def nonqualifying_member_count(self) -> int:
        """Usable members that failed a condition.

        Previously implicit. A reader could only reach it by subtracting, and the
        one number that mattered most for reading an agreement fraction -- how many
        usable members said no -- was the one never shown.
        """
        return self.usable_member_count - self.qualifying_member_count

    @property
    def loaded_unusable_member_count(self) -> int:
        """Members that downloaded but produced no verdict."""
        return self.loaded_member_count - self.usable_member_count

    @property
    def not_loaded_member_count(self) -> int:
        return self.requested_member_count - self.loaded_member_count

    @property
    def not_evaluated_member_count(self) -> int:
        """Members withheld because the run was cancelled before reaching them."""
        return sum(item.state == MEMBER_NOT_EVALUATED for item in self.members)

    @property
    def resolved_member_count(self) -> int:
        """Members that reached a final state, evaluated or classified for free.

        Not "evaluated": a member that never loaded reaches a certain final state
        without any diagnostic being run, and counting those as evaluated would
        overstate how much of a cancelled run actually completed.
        """
        return self.requested_member_count - self.not_evaluated_member_count

    @property
    def reconciles(self) -> bool:
        """Whether the four terminal groups add up to the requested ensemble.

        The counts are displayed as a funnel, so they have to be a partition. This
        is asserted rather than assumed: an unusable member silently entering the
        agreement denominator is precisely the failure mode that makes a threshold
        result untrustworthy.
        """
        return (
            self.qualifying_member_count
            + self.nonqualifying_member_count
            + self.loaded_unusable_member_count
            + self.not_loaded_member_count
        ) == self.requested_member_count == len(self.members)

    @property
    def state_counts(self) -> Mapping[str, int]:
        """How many members are in each state, including the zeros."""
        tally = dict.fromkeys(MEMBER_STATES, 0)
        for item in self.members:
            tally[item.state] = tally.get(item.state, 0) + 1
        return MappingProxyType(tally)

    def members_in(self, *states: str) -> tuple[ThresholdMemberResult, ...]:
        """Return the member results in any of ``states``, in requested order."""
        wanted = frozenset(str(state) for state in states)
        return tuple(item for item in self.members if item.state in wanted)

    @property
    def agreement_text(self) -> str:
        """The agreement fraction with its denominator visible."""
        return fraction_text(self.qualifying_member_count, self.usable_member_count)

    @property
    def coverage_text(self) -> str:
        """Usable coverage of the requested ensemble, with its denominator visible."""
        return fraction_text(self.usable_member_count, self.requested_member_count)

    @property
    def qualifying_members(self) -> tuple[str, ...]:
        return tuple(item.member for item in self.members if item.satisfied is True)

    @property
    def nonqualifying_members(self) -> tuple[str, ...]:
        return tuple(item.member for item in self.members if item.satisfied is False)

    @property
    def unusable_members(self) -> tuple[str, ...]:
        """Every member without a verdict, whether or not it loaded.

        The umbrella tuple, matching ``EnsembleAcquisition.unavailable_members``.
        Use :meth:`members_in` when the distinction between "never downloaded" and
        "downloaded but unusable" matters, because it usually does.
        """
        return tuple(item.member for item in self.members if not item.usable)

    @property
    def retryable_members(self) -> tuple[str, ...]:
        """Members a targeted retry could plausibly fix, in requested order."""
        return tuple(
            item.member
            for item in self.members
            if item.state in (MEMBER_NOT_LOADED, MEMBER_NO_PROFILE)
        )

    @property
    def evaluation_retryable_members(self) -> tuple[str, ...]:
        """Members whose diagnostic work is missing or failed.

        This is intentionally separate from :attr:`retryable_members`, which is
        the data-acquisition retry boundary.  Re-evaluating a member that never
        downloaded cannot make a profile appear, while downloading an already
        loaded member again cannot repair a transient diagnostic failure.
        """
        return tuple(
            item.member
            for item in self.members
            if item.state in (MEMBER_FAILED, MEMBER_NOT_EVALUATED)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "definition": self.definition.as_dict(),
            "definition_summary": self.definition.summary,
            "valid_time": _iso(self.valid_time),
            "requested_member_count": self.requested_member_count,
            "loaded_member_count": self.loaded_member_count,
            "usable_member_count": self.usable_member_count,
            "qualifying_member_count": self.qualifying_member_count,
            "nonqualifying_member_count": self.nonqualifying_member_count,
            "loaded_unusable_member_count": self.loaded_unusable_member_count,
            "not_loaded_member_count": self.not_loaded_member_count,
            "resolved_member_count": self.resolved_member_count,
            "not_evaluated_member_count": self.not_evaluated_member_count,
            "agreement_fraction": self.agreement_fraction,
            "agreement": self.agreement_text,
            "requested_coverage": self.requested_coverage,
            "coverage": self.coverage_text,
            "reconciles": self.reconciles,
            "cancelled": self.cancelled,
            "label": DISCLAIMER,
            "members": [
                {
                    "member": item.member,
                    "loaded": item.loaded,
                    "usable": item.usable,
                    "satisfied": item.satisfied,
                    "state": item.state,
                    "state_label": item.state_label,
                    "values": dict(item.values),
                    "reason": item.reason,
                }
                for item in self.members
            ],
        }


def _date_index(collection, valid_time):
    dates = tuple(getattr(collection, "_dates", ()) or ())
    if valid_time is None:
        try:
            valid_time = collection.getCurrentDate()
        except (AttributeError, IndexError, TypeError, ValueError):
            valid_time = dates[0] if dates else None
    try:
        return valid_time, dates.index(valid_time)
    except ValueError:
        return valid_time, None


def evaluate_threshold(
    collection,
    definition,
    engine,
    *,
    valid_time=None,
    cancel=None,
    resume: ThresholdResult | None = None,
    progress: Callable[[int, int], object] | None = None,
):
    """Evaluate one single or joint definition on the common member subset.

    ``cancel`` is polled before each member, and every member already evaluated is
    retained. A thirty-member ensemble with composite diagnostics could previously
    only be interrupted between valid times, so a single evaluation ran to
    completion however long it took and discarded nothing because it kept nothing.
    """
    if not isinstance(definition, ThresholdDefinition):
        definition = ThresholdDefinition.from_dict(definition)
    valid_time, index = _date_index(collection, valid_time)
    acquisition = EnsembleAcquisition.from_collection(collection)
    profiles = getattr(collection, "_profs", {}) or {}
    loaded = tuple(acquisition.loaded_members) or tuple(str(key) for key in profiles)
    requested = tuple(acquisition.requested_members) or loaded
    # Hand-built/legacy collections remain honest even when their metadata did
    # not know about a profile present in memory.
    loaded = tuple(dict.fromkeys((*loaded, *(str(key) for key in profiles))))
    requested = tuple(dict.fromkeys((*requested, *loaded)))
    loaded_set = set(loaded)
    available_profiles = {}
    for member in requested:
        if member not in loaded_set or member not in profiles:
            continue
        try:
            profile = profiles[member][index] if index is not None else None
        except (IndexError, KeyError, TypeError):
            profile = None
        if profile is not None:
            available_profiles[member] = profile

    previous = {}
    if resume is not None:
        if not isinstance(resume, ThresholdResult):
            raise ThresholdError("threshold resume state is invalid")
        if resume.definition != definition:
            raise ThresholdError("threshold resume definition does not match")
        if resume.valid_time != valid_time:
            raise ThresholdError("threshold resume valid time does not match")
        if tuple(item.member for item in resume.members) != requested:
            raise ThresholdError("threshold resume member request does not match")
        previous = {item.member: item for item in resume.members}
        for member, item in previous.items():
            is_loaded = member in loaded_set and member in profiles
            has_profile = member in available_profiles
            if item.loaded != is_loaded:
                raise ThresholdError("threshold resume loaded-member set does not match")
            if item.loaded and (item.state == MEMBER_NO_PROFILE) == has_profile:
                raise ThresholdError("threshold resume profile availability does not match")

    total = len(requested)
    processed = 0

    def report():
        if callable(progress):
            progress(processed, total)

    report()

    batched_values = {}
    values_many = getattr(engine, "values_many", None)
    # Batching computes every member at once, so it cannot be interrupted part
    # way. Skipping it once cancellation is already pending keeps the request
    # responsive; skipping it otherwise would give up the fast path for nothing.
    retry_members = {
        member
        for member in requested
        if member not in previous
        or previous[member].state in (MEMBER_FAILED, MEMBER_NOT_EVALUATED)
    }
    batch_profiles = {
        member: profile
        for member, profile in available_profiles.items()
        if member in retry_members
    }
    if callable(values_many) and batch_profiles and not _cancelled(cancel):
        try:
            rows = tuple(values_many(batch_profiles.values(), definition.metrics))
            if len(rows) == len(batch_profiles):
                batched_values = dict(zip(batch_profiles, rows, strict=True))
        except Exception:  # noqa: BLE001 - serial fallback preserves isolation
            batched_values = {}

    members = []
    empty = MappingProxyType({key: None for key in definition.metrics})
    was_cancelled = False
    for member in requested:
        prior = previous.get(member)
        if prior is not None and prior.state not in (
            MEMBER_FAILED,
            MEMBER_NOT_EVALUATED,
        ):
            members.append(prior)
            processed += 1
            report()
            continue
        is_loaded = member in loaded_set and member in profiles
        # Classifications that need no diagnostic run happen whether or not a
        # cancel is pending. They are free and they are certain, so reporting them
        # as "not evaluated" would replace a known answer with an unknown one.
        if not is_loaded:
            members.append(
                ThresholdMemberResult(
                    member,
                    False,
                    False,
                    None,
                    empty,
                    "member was not loaded",
                    MEMBER_NOT_LOADED,
                )
            )
            processed += 1
            report()
            continue
        profile = available_profiles.get(member)
        if profile is None:
            members.append(
                ThresholdMemberResult(
                    member,
                    True,
                    False,
                    None,
                    empty,
                    "member has no profile at the exact valid time",
                    MEMBER_NO_PROFILE,
                )
            )
            processed += 1
            report()
            continue
        # Only the diagnostic run is interruptible, so the cancel is polled here
        # rather than at the top of the loop: that is where the time goes.
        if was_cancelled or _cancelled(cancel):
            was_cancelled = True
            members.append(
                ThresholdMemberResult(
                    member,
                    True,
                    False,
                    None,
                    empty,
                    "not evaluated: the run was cancelled before this member",
                    MEMBER_NOT_EVALUATED,
                )
            )
            continue
        try:
            computed = (
                batched_values[member]
                if member in batched_values
                else engine.values(profile, definition.metrics)
            )
        except Exception as exc:  # noqa: BLE001 - backend isolation per member
            members.append(
                ThresholdMemberResult(
                    member,
                    True,
                    False,
                    None,
                    empty,
                    f"diagnostic calculation failed: {exc}",
                    MEMBER_FAILED,
                )
            )
            processed += 1
            report()
            continue
        values = MappingProxyType(
            {key: _finite(computed.get(key)) for key in definition.metrics}
        )
        outcomes = tuple(
            condition.evaluate(values[condition.metric])
            for condition in definition.conditions
        )
        usable = all(outcome is not None for outcome in outcomes)
        satisfied = all(outcomes) if usable else None
        if not usable:
            # Name the diagnostics that were missing, not just that some were. A
            # joint rule over three ingredients gave one message for all three.
            absent = ", ".join(
                condition.metric
                for condition, outcome in zip(definition.conditions, outcomes)
                if outcome is None
            )
            state = MEMBER_UNUSABLE
            reason = f"no value for {absent}"
        else:
            state = MEMBER_QUALIFYING if satisfied else MEMBER_NONQUALIFYING
            reason = None
        members.append(
            ThresholdMemberResult(
                member, True, usable, satisfied, values, reason, state
            )
        )
        processed += 1
        report()
    usable_count = sum(item.usable for item in members)
    qualifying_count = sum(item.satisfied is True for item in members)
    return ThresholdResult(
        definition,
        valid_time,
        len(requested),
        sum(member in loaded_set for member in requested),
        usable_count,
        qualifying_count,
        tuple(members),
        was_cancelled,
    )


def evaluate_threshold_timeline(
    collection,
    definition,
    engine,
    *,
    valid_times: Iterable[datetime] | None = None,
    cancel: Callable[[], bool] | object | None = None,
    resume: Iterable[ThresholdResult] | None = None,
    progress: Callable[[int, int], object] | None = None,
) -> tuple[ThresholdResult, ...]:
    """Evaluate ordered exact times, preserving all results before cancel.

    ``cancel`` reaches the member loop as well, so a cancellation part way through
    a valid time keeps the members that time already evaluated instead of throwing
    the whole time away.
    """
    times = (
        tuple(getattr(collection, "_dates", ()) or ())
        if valid_times is None
        else tuple(valid_times)
    )
    previous = tuple(resume or ())
    previous_by_time = {}
    for result in previous:
        if not isinstance(result, ThresholdResult):
            raise ThresholdError("threshold timeline resume state is invalid")
        if result.valid_time in previous_by_time:
            raise ThresholdError("threshold timeline resume has duplicate valid times")
        if result.valid_time not in times:
            raise ThresholdError("threshold timeline resume valid time does not match")
        previous_by_time[result.valid_time] = result
    acquisition = EnsembleAcquisition.from_collection(collection)
    profiles = tuple(str(item) for item in (getattr(collection, "_profs", {}) or {}))
    requested = tuple(acquisition.requested_members) or profiles
    requested = tuple(dict.fromkeys((*requested, *profiles)))
    member_total = len(requested)
    total = len(times) * member_total
    resolved_before = 0
    if callable(progress):
        progress(0, total)
    results = []
    for valid_time in times:
        if _cancelled(cancel):
            break

        def time_progress(done, _time_total, *, offset=resolved_before):
            if callable(progress):
                progress(offset + done, total)

        result = evaluate_threshold(
            collection,
            definition,
            engine,
            valid_time=valid_time,
            cancel=cancel,
            resume=previous_by_time.get(valid_time),
            progress=time_progress,
        )
        # Preserve every certain member outcome. A cancelled time containing only
        # withheld members is noise; a known unavailable or failed member is still
        # useful and is also required for an exact retry boundary.
        if not result.cancelled or result.resolved_member_count:
            results.append(result)
        resolved_before += result.resolved_member_count
        if result.cancelled:
            break
    return tuple(results)


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_threshold_results(path, definition, results=()) -> Path:
    """Atomically persist a definition and any computed timeline results."""
    if not isinstance(definition, ThresholdDefinition):
        definition = ThresholdDefinition.from_dict(definition)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "definition": definition.as_dict(),
        "results": [result.as_dict() for result in results],
    }
    return _atomic_json(Path(path), payload)


def read_threshold_definition(path) -> ThresholdDefinition:
    """Load a threshold definition without trusting executable content."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThresholdError(f"threshold file could not be read: {exc}") from exc
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ThresholdError("unsupported ensemble threshold file")
    return ThresholdDefinition.from_dict(payload.get("definition", {}))


def export_threshold_csv(path, results: Iterable[ThresholdResult]) -> Path:
    """Export summary plus member classifications with explicit denominators."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    fields = (
        "definition",
        # What the definition actually tested, so an exported file is readable
        # without the application that produced it.
        "definition_summary",
        "valid_time",
        "member",
        "loaded",
        "usable",
        "satisfied",
        "reason",
        # Which of the seven member states this row is in, and the same phrase the
        # application shows for it.
        "state",
        "state_label",
        "qualifying_count",
        # The counts that were previously only derivable by subtraction.
        "nonqualifying_count",
        "loaded_unusable_count",
        "not_loaded_count",
        "usable_count",
        "loaded_count",
        "requested_count",
        "resolved_count",
        "not_evaluated_count",
        "agreement_fraction",
        "agreement",
        "requested_coverage",
        "coverage",
        "counts_reconcile",
        "cancelled",
        "diagnostic_values",
        "interpretation",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for result in results:
                for member in result.members:
                    writer.writerow(
                        {
                            "definition": result.definition.name,
                            "definition_summary": result.definition.summary,
                            "valid_time": _iso(result.valid_time),
                            "member": member.member,
                            "loaded": member.loaded,
                            "usable": member.usable,
                            "satisfied": (
                                "" if member.satisfied is None else member.satisfied
                            ),
                            "reason": member.reason or "",
                            "state": member.state,
                            "state_label": member.state_label,
                            "qualifying_count": result.qualifying_member_count,
                            "nonqualifying_count": result.nonqualifying_member_count,
                            "loaded_unusable_count": (
                                result.loaded_unusable_member_count
                            ),
                            "not_loaded_count": result.not_loaded_member_count,
                            "usable_count": result.usable_member_count,
                            "loaded_count": result.loaded_member_count,
                            "requested_count": result.requested_member_count,
                            "resolved_count": result.resolved_member_count,
                            "not_evaluated_count": result.not_evaluated_member_count,
                            "agreement_fraction": result.agreement_fraction,
                            "agreement": result.agreement_text,
                            "requested_coverage": result.requested_coverage,
                            "coverage": result.coverage_text,
                            "counts_reconcile": result.reconciles,
                            "cancelled": result.cancelled,
                            "diagnostic_values": json.dumps(
                                dict(member.values), sort_keys=True
                            ),
                            "interpretation": DISCLAIMER,
                        }
                    )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return destination


__all__ = [
    "DISCLAIMER",
    "LOADED_UNUSABLE_STATES",
    "MEMBER_FAILED",
    "MEMBER_NONQUALIFYING",
    "MEMBER_NOT_EVALUATED",
    "MEMBER_NOT_LOADED",
    "MEMBER_NO_PROFILE",
    "MEMBER_QUALIFYING",
    "MEMBER_STATES",
    "MEMBER_STATE_LABELS",
    "MEMBER_STATE_SHORT_LABELS",
    "MEMBER_UNUSABLE",
    "OPERATORS",
    "ThresholdCondition",
    "ThresholdDefinition",
    "ThresholdError",
    "ThresholdMemberResult",
    "ThresholdResult",
    "evaluate_threshold",
    "evaluate_threshold_timeline",
    "export_threshold_csv",
    "fraction_text",
    "read_threshold_definition",
    "write_threshold_results",
]

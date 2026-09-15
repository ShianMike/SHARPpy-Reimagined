"""Explicit ensemble request, acquisition, and retry accounting.

An ensemble is not defined by the members that happened to download.  This
module keeps the original request set beside the usable in-memory profiles so
the GUI, sessions, and exports can distinguish an unavailable member from a
member that genuinely failed an ingredient test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping


_REQUESTED = "ensemble_requested_members"
_LOADED = "ensemble_loaded_members"
_FAILURES = "ensemble_member_failures"
_SPECS = "ensemble_request_specs"


def _member(value) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("ensemble member labels must be non-empty")
    return text


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except Exception:
        try:
            value = collection._meta.get(key, default)
        except (AttributeError, TypeError):
            return default
    return default if value is None else value


def _set_meta(collection, key, value) -> None:
    try:
        collection.setMeta(key, value)
    except Exception:
        collection._meta[key] = value


def _error_text(error) -> str | None:
    if not isinstance(error, Mapping):
        return None
    for key in ("message", "detail", "error", "type"):
        value = error.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _serialize_spec(spec) -> dict[str, object]:
    run_time = getattr(spec, "run_time", None)
    return {
        "request_id": str(getattr(spec, "request_id")),
        "model": str(getattr(spec, "model")),
        "label": str(getattr(spec, "label")),
        "run_time": run_time if isinstance(run_time, datetime) else str(run_time),
        "fxx": int(getattr(spec, "fxx")),
        "member": _member(getattr(spec, "member")),
    }


@dataclass(frozen=True)
class MemberFailure:
    """One requested member that did not produce a loaded profile."""

    member: str
    status: str
    reason: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "member", _member(self.member))
        status = str(self.status).strip().lower()
        if status not in {"failed", "cancelled", "unknown"}:
            raise ValueError(f"unsupported ensemble member status {status!r}")
        object.__setattr__(self, "status", status)
        if self.reason is not None:
            object.__setattr__(self, "reason", str(self.reason))


@dataclass(frozen=True)
class EnsembleAcquisition:
    """Immutable acquisition ledger for one ensemble request."""

    requested_members: tuple[str, ...]
    loaded_members: tuple[str, ...]
    failures: tuple[MemberFailure, ...] = ()
    request_specs: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self):
        requested = tuple(_member(item) for item in self.requested_members)
        loaded = tuple(_member(item) for item in self.loaded_members)
        if len(set(requested)) != len(requested):
            raise ValueError("requested ensemble members must be unique")
        if len(set(loaded)) != len(loaded):
            raise ValueError("loaded ensemble members must be unique")
        if any(item not in requested for item in loaded):
            raise ValueError("loaded members must belong to the original request")
        failures = tuple(self.failures)
        failed_names = tuple(item.member for item in failures)
        if len(set(failed_names)) != len(failed_names):
            raise ValueError("each unavailable member needs one final status")
        if any(item not in requested for item in failed_names):
            raise ValueError("failed members must belong to the original request")
        if set(loaded) & set(failed_names):
            raise ValueError("a member cannot be both loaded and unavailable")
        object.__setattr__(self, "requested_members", requested)
        object.__setattr__(self, "loaded_members", loaded)
        object.__setattr__(self, "failures", failures)
        object.__setattr__(
            self,
            "request_specs",
            tuple(MappingProxyType(dict(item)) for item in self.request_specs),
        )

    @property
    def requested_count(self) -> int:
        return len(self.requested_members)

    @property
    def loaded_count(self) -> int:
        return len(self.loaded_members)

    @property
    def failed_members(self) -> tuple[str, ...]:
        return tuple(item.member for item in self.failures if item.status == "failed")

    @property
    def cancelled_members(self) -> tuple[str, ...]:
        return tuple(
            item.member for item in self.failures if item.status == "cancelled"
        )

    @property
    def unknown_members(self) -> tuple[str, ...]:
        resolved = set(self.loaded_members) | {
            item.member for item in self.failures if item.status != "unknown"
        }
        return tuple(item for item in self.requested_members if item not in resolved)

    @property
    def unavailable_members(self) -> tuple[str, ...]:
        loaded = set(self.loaded_members)
        return tuple(item for item in self.requested_members if item not in loaded)

    def failure_for(self, member: object) -> MemberFailure | None:
        wanted = _member(member)
        return next((item for item in self.failures if item.member == wanted), None)

    def specs_for_retry(self) -> tuple[Mapping[str, object], ...]:
        unavailable = set(self.unavailable_members)
        return tuple(
            MappingProxyType(dict(item))
            for item in self.request_specs
            if str(item.get("member", "")) in unavailable
        )

    def to_metadata(self) -> dict[str, object]:
        failures = [
            {
                "member": item.member,
                "status": item.status,
                "reason": item.reason,
            }
            for item in self.failures
        ]
        return {
            _REQUESTED: list(self.requested_members),
            _LOADED: list(self.loaded_members),
            _FAILURES: failures,
            _SPECS: [dict(item) for item in self.request_specs],
            "ensemble_requested_count": self.requested_count,
            "ensemble_loaded_count": self.loaded_count,
        }

    def attach(self, collection) -> None:
        """Attach the portable ledger to a profile collection."""
        for key, value in self.to_metadata().items():
            _set_meta(collection, key, value)
        _set_meta(collection, "ensemble", True)
        # ``ensemble_count`` historically represented the only count present.
        # It now means the ensemble the user requested; the explicit loaded
        # field and legacy member count retain the in-memory member count.
        _set_meta(collection, "ensemble_count", self.requested_count)
        _set_meta(collection, "ensemble_member_count", self.loaded_count)
        _set_meta(collection, "ensemble_members", list(self.loaded_members))

    @classmethod
    def complete(cls, members: Iterable[object]) -> "EnsembleAcquisition":
        labels = tuple(_member(item) for item in members)
        return cls(labels, labels)

    @classmethod
    def from_batch(cls, specs, result) -> "EnsembleAcquisition":
        """Build a ledger from request specs and an ordered batch result."""
        specs = tuple(specs)
        serialized = tuple(_serialize_spec(spec) for spec in specs)
        requested = tuple(str(item["member"]) for item in serialized)
        if len(set(requested)) != len(requested):
            raise ValueError("ensemble request contains duplicate members")
        by_id = {
            str(getattr(item, "id")): item for item in tuple(getattr(result, "items", ()))
        }
        loaded = []
        failures = []
        for spec, payload in zip(specs, serialized):
            member = str(payload["member"])
            item = by_id.get(str(getattr(spec, "request_id")))
            status = str(getattr(item, "status", "unknown")).lower()
            if status == "completed":
                loaded.append(member)
            else:
                if status not in {"failed", "cancelled"}:
                    status = "unknown"
                failures.append(
                    MemberFailure(member, status, _error_text(getattr(item, "error", None)))
                )
        return cls(requested, tuple(loaded), tuple(failures), serialized)

    @classmethod
    def from_collection(cls, collection) -> "EnsembleAcquisition":
        profiles = tuple(str(item) for item in getattr(collection, "_profs", {}))
        requested_raw = _meta(collection, _REQUESTED, profiles)
        loaded_raw = _meta(collection, _LOADED, profiles)
        requested = tuple(str(item) for item in requested_raw or profiles)
        loaded_set = set(profiles)
        loaded = tuple(
            str(item) for item in (loaded_raw or profiles) if str(item) in loaded_set
        )
        raw_failures = _meta(collection, _FAILURES, ())
        failures = []
        if isinstance(raw_failures, (list, tuple)):
            for item in raw_failures:
                if not isinstance(item, Mapping):
                    continue
                try:
                    failures.append(
                        MemberFailure(
                            str(item.get("member", "")),
                            str(item.get("status", "unknown")),
                            item.get("reason"),
                        )
                    )
                except ValueError:
                    continue
        failure_names = {item.member for item in failures}
        for member in requested:
            if member not in loaded and member not in failure_names:
                failures.append(MemberFailure(member, "unknown", None))
        raw_specs = _meta(collection, _SPECS, ())
        specs = tuple(item for item in raw_specs or () if isinstance(item, Mapping))
        return cls(requested, loaded, tuple(failures), specs)


__all__ = [
    "EnsembleAcquisition",
    "MemberFailure",
]

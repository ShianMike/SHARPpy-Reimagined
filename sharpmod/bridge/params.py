"""Request validation and result shaping shared by the sounding handlers.

Handlers that take parameters validate them here, so every bad value becomes
one ``INVALID_ARGUMENT`` sentence that names the field.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sharpmod.bridge.errors import BridgeError


def now() -> datetime:
    """The current UTC time (the fixture exporter pins it)."""
    return datetime.now(UTC)


def invalid(what: str) -> BridgeError:
    return BridgeError("INVALID_ARGUMENT", f"The request was not valid: {what}.", False)


def keys(
    params: dict[str, Any], required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> None:
    missing = [key for key in required if key not in params]
    extra = sorted(set(params) - set(required) - set(optional))
    if missing or extra:
        raise invalid(
            f"expected {list(required)} (optional {list(optional)}), "
            f"missing {missing}, unexpected {extra}"
        )


def text(params: dict[str, Any], key: str, low: int, high: int) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise invalid(f"{key} must be text of {low} to {high} characters")
    return value.strip()


def optional_text(params: dict[str, Any], key: str, high: int) -> str | None:
    if params.get(key) is None:
        return None
    return text(params, key, 1, high)


def number(params: dict[str, Any], key: str, low: float, high: float) -> float:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise invalid(f"{key} must be a number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise invalid(f"{key} must be within [{low:g}, {high:g}]")
    return result


def integer(params: dict[str, Any], key: str, low: int, high: int) -> int:
    value = params.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise invalid(f"{key} must be an integer within [{low}, {high}]")
    return value


def time(params: dict[str, Any], key: str) -> datetime:
    value = params.get(key)
    if not isinstance(value, str) or len(value) > 40:
        raise invalid(f"{key} must be an RFC 3339 time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise invalid(f"{key} must be an RFC 3339 time") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if (
        not datetime(1900, 1, 1, tzinfo=UTC)
        <= parsed
        <= now() + timedelta(days=30)
    ):
        raise invalid(f"{key} is outside the supported range")
    return parsed


def choice(
    params: dict[str, Any], key: str, choices: tuple[str, ...], default: str
) -> str:
    value = params.get(key, default)
    if not isinstance(value, str) or value not in choices:
        raise invalid(f"{key} must be one of {', '.join(choices)}")
    return value


def iso(value: Any) -> str | None:
    """RFC 3339 UTC with whole seconds; a naive datetime is UTC, as in SHARPpy."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def num(value: Any) -> float | None:
    """A finite float, or ``None`` for masked, missing, or non-finite values."""
    try:
        import numpy.ma as ma

        if value is None or value is ma.masked or ma.is_masked(value):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    from sharpmod.sharptab.constants import is_missing

    if not math.isfinite(result) or is_missing(result):
        return None
    return result

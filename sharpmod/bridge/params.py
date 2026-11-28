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
    import sharpmod_rs

    sharpmod_rs.bridge_validate_param_keys(
        params, list(required), list(optional)
    )


def text(params: dict[str, Any], key: str, low: int, high: int) -> str:
    import sharpmod_rs

    return sharpmod_rs.bridge_validate_text(
        params.get(key), key, int(low), int(high)
    )


def optional_text(params: dict[str, Any], key: str, high: int) -> str | None:
    if params.get(key) is None:
        return None
    return text(params, key, 1, high)


def number(params: dict[str, Any], key: str, low: float, high: float) -> float:
    import sharpmod_rs

    return sharpmod_rs.bridge_validate_number(
        params.get(key), key, float(low), float(high)
    )


def integer(params: dict[str, Any], key: str, low: int, high: int) -> int:
    import sharpmod_rs

    return sharpmod_rs.bridge_validate_integer(
        params.get(key), key, int(low), int(high)
    )


def time(params: dict[str, Any], key: str) -> datetime:
    import sharpmod_rs

    micros = sharpmod_rs.bridge_validate_rfc3339_time(
        params.get(key), key, int(now().timestamp() * 1_000_000)
    )
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=micros)


def choice(
    params: dict[str, Any], key: str, choices: tuple[str, ...], default: str
) -> str:
    import sharpmod_rs

    return sharpmod_rs.bridge_validate_choice(
        params.get(key, default), key, list(choices)
    )


def iso(value: Any) -> str | None:
    """RFC 3339 UTC with whole seconds; a naive datetime is UTC, as in SHARPpy."""
    if not isinstance(value, datetime):
        return None
    import sharpmod_rs

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    # Exact integer seconds, floored like ``strftime`` drops the fraction;
    # ``int(timestamp())`` would truncate toward zero before 1970.
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return sharpmod_rs.bridge_format_iso_utc(delta.days * 86400 + delta.seconds)


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

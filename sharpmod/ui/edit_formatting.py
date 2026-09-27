"""Readable sounding edit summaries and unit-aware values."""

from __future__ import annotations

from sharpmod.ui.features.gui_edit_feedback import (
    TEMPERATURE_FIELDS, WIND_FIELDS, COMPONENT_FIELDS,
    _FIELD_LABELS, _DEVIANT_LABELS,
)

from sharpmod.ui.features.gui_sounding_readout import KT_TO_MS
from sharpmod.ui.features.gui_sounding_readout import finite_number
import math


def _is_fahrenheit(units) -> bool:
    return str(units).strip().casefold() == "fahrenheit"


def _is_meters_per_second(units) -> bool:
    return str(units).strip().casefold() in {"m/s", "ms", "meters per second"}


def _temperature_text(value_c: float | None, units) -> str:
    if value_c is None:
        return "not reported"
    if _is_fahrenheit(units):
        return f"{value_c * 9.0 / 5.0 + 32.0:.1f} °F"
    return f"{value_c:.1f} °C"


def _temperature_delta_text(delta_c: float | None, units) -> str:
    if delta_c is None:
        return "not comparable"
    # A difference converts by ratio only; adding the 32 °F offset to a delta
    # would report a 1 °C change as a 33.8 °F change.
    if _is_fahrenheit(units):
        return f"{delta_c * 9.0 / 5.0:+.1f} °F"
    return f"{delta_c:+.1f} °C"


def _speed_text(speed_kt: float | None, units) -> str:
    if speed_kt is None:
        return "not reported"
    if _is_meters_per_second(units):
        return f"{speed_kt * KT_TO_MS:.1f} m/s"
    return f"{speed_kt:.0f} kt"


def _wind_text(direction_deg: float | None, speed_kt: float | None, units) -> str:
    if direction_deg is None and speed_kt is None:
        return "not reported"
    if speed_kt is not None and speed_kt < 0.05:
        return f"calm 0 {'m/s' if _is_meters_per_second(units) else 'kt'}"
    if direction_deg is None:
        return f"direction not reported/{_speed_text(speed_kt, units)}"
    direction = int(round(direction_deg)) % 360
    return f"{direction:03d}°/{_speed_text(speed_kt, units)}"


def _direction_delta(before_deg: float | None, after_deg: float | None) -> float | None:
    """Return the signed shortest turn, so 355° to 005° is +10° not -350°."""
    if before_deg is None or after_deg is None:
        return None
    return ((after_deg - before_deg + 180.0) % 360.0) - 180.0


def _wind_delta_text(
    before_direction: float | None,
    before_speed: float | None,
    after_direction: float | None,
    after_speed: float | None,
    units,
) -> str:
    parts: list[str] = []
    if before_speed is not None and after_speed is not None:
        delta_kt = after_speed - before_speed
        if _is_meters_per_second(units):
            delta = delta_kt * KT_TO_MS
            if abs(delta) >= 0.05:
                parts.append(f"{delta:+.1f} m/s")
        elif abs(delta_kt) >= 0.5:
            parts.append(f"{delta_kt:+.0f} kt")
    turn = _direction_delta(before_direction, after_direction)
    if turn is not None and abs(turn) >= 0.5:
        parts.append(f"{turn:+.0f}°")
    if parts:
        return ", ".join(parts)
    incomplete = None in (
        before_direction,
        before_speed,
        after_direction,
        after_speed,
    )
    return "not comparable" if incomplete else "none"


def wind_from_components(u, v) -> tuple[float | None, float | None]:
    """Return ``(direction_deg, speed_kt)`` using the project's wind convention.

    This mirrors :func:`sharpmod.sharptab.interp.vec` rather than introducing a
    second convention for the same conversion.
    """
    u_value = finite_number(u, minimum=-500.0, maximum=500.0)
    v_value = finite_number(v, minimum=-500.0, maximum=500.0)
    if u_value is None or v_value is None:
        return None, None
    speed = math.hypot(u_value, v_value)
    direction = (270.0 - math.degrees(math.atan2(v_value, u_value))) % 360.0
    return direction, speed


def _wind_pair(values) -> tuple[float | None, float | None]:
    """Resolve one wind vector from either reported or component fields."""
    direction = finite_number(values.get("wdir"), minimum=0.0, maximum=360.0)
    speed = finite_number(values.get("wspd"), minimum=0.0, maximum=500.0)
    if direction is None or speed is None:
        derived_direction, derived_speed = wind_from_components(
            values.get("u"), values.get("v")
        )
        direction = direction if direction is not None else derived_direction
        speed = speed if speed is not None else derived_speed
    if direction is not None:
        direction %= 360.0
    return direction, speed


def _header(prefix: str, pressure_hpa: float | None) -> str:
    pressure = finite_number(pressure_hpa, minimum=0.01, maximum=1200.0)
    if pressure is None:
        return f"{prefix} at an unreported level"
    return f"{prefix} at {pressure:.1f} hPa"


def format_level_changes(
    original,
    proposed,
    *,
    pressure_hpa: float | None = None,
    temp_units: str = "Celsius",
    wind_units: str = "knots",
    prefix: str = "Editing",
) -> str:
    """Describe one level's original value, proposed value, and the change.

    ``original`` and ``proposed`` are field-keyed mappings. Only the fields
    present in either mapping are described, so a temperature drag never
    invents a wind claim.
    """
    original = dict(original or {})
    proposed = dict(proposed or {})
    segments = [_header(prefix, pressure_hpa)]

    for field in TEMPERATURE_FIELDS:
        if field not in original and field not in proposed:
            continue
        before = finite_number(original.get(field), minimum=-273.15, maximum=100.0)
        after = finite_number(proposed.get(field), minimum=-273.15, maximum=100.0)
        delta = None if before is None or after is None else after - before
        segments.append(
            f"{_FIELD_LABELS[field]}: original {_temperature_text(before, temp_units)}"
        )
        segments.append(f"proposed {_temperature_text(after, temp_units)}")
        segments.append(f"change {_temperature_delta_text(delta, temp_units)}")

    wind_keys = WIND_FIELDS + COMPONENT_FIELDS
    if any(key in original or key in proposed for key in wind_keys):
        before_direction, before_speed = _wind_pair(original)
        after_direction, after_speed = _wind_pair(proposed)
        segments.append(
            f"Wind: original {_wind_text(before_direction, before_speed, wind_units)}"
        )
        segments.append(
            f"proposed {_wind_text(after_direction, after_speed, wind_units)}"
        )
        segments.append(
            "change "
            + _wind_delta_text(
                before_direction,
                before_speed,
                after_direction,
                after_speed,
                wind_units,
            )
        )
    return " · ".join(segments)


def format_vector_change(
    deviant: str,
    original,
    proposed,
    *,
    wind_units: str = "knots",
    prefix: str = "Editing",
) -> str:
    """Describe a storm-motion vector edit in the same original/proposed form."""
    label = _DEVIANT_LABELS.get(
        str(deviant).strip().casefold(), "Storm motion"
    )
    before_direction, before_speed = wind_from_components(*_pair(original))
    after_direction, after_speed = wind_from_components(*_pair(proposed))
    return " · ".join(
        (
            f"{prefix} {label[0].lower() + label[1:]}",
            f"original {_wind_text(before_direction, before_speed, wind_units)}",
            f"proposed {_wind_text(after_direction, after_speed, wind_units)}",
            "change "
            + _wind_delta_text(
                before_direction,
                before_speed,
                after_direction,
                after_speed,
                wind_units,
            ),
        )
    )


def _pair(value) -> tuple[object, object]:
    try:
        first, second = value
    except (TypeError, ValueError):
        return None, None
    return first, second

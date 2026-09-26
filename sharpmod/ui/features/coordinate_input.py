"""Interpret explicit coordinate axes conservatively, without Qt or data retrieval."""

from dataclasses import dataclass
import math
import re


class CoordinateInputError(ValueError):
    """Invalid or ambiguous pasted coordinates require user correction."""


@dataclass(frozen=True)
class CoordinateInterpretation:
    lat: float
    lon: float
    explanation: str


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_UNSIGNED = r"(?:\d+(?:\.\d*)?|\.\d+)"
_ANGLE = rf"{_NUMBER}\s*°?(?:\s*{_UNSIGNED}\s*'(?:\s*{_UNSIGNED}\s*\")?)?"
_HEMISPHERE_ANGLE = re.compile(rf"(?:[NSEW]\s*{_ANGLE}|{_ANGLE}\s*[NSEW])", re.IGNORECASE)
_LABEL = re.compile(r"\b(latitude|longitude|lat|lon|long|lng)\s*[:=]?\s*", re.IGNORECASE)
_SEPARATORS = " \t\r\n,;()/"


def _normalized(text):
    value = str(text or "").strip().replace("−", "-").replace("′", "'").replace("″", '"')
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    for name, letter in (("north", "N"), ("south", "S"), ("east", "E"), ("west", "W")):
        value = re.sub(rf"\b{name}\b", letter, value, flags=re.IGNORECASE)
    if not value:
        raise CoordinateInputError("Paste a coordinate pair to preview it.")
    if len(value) > 1024:
        raise CoordinateInputError("Paste only the coordinate pair, not a whole document.")
    return value


def _angle(text, *, expected_axis=None):
    value = text.strip(_SEPARATORS).upper()
    hemispheres = re.findall(r"[NSEW]", value)
    if len(hemispheres) > 1:
        raise CoordinateInputError("Use one hemisphere per coordinate.")
    hemisphere = hemispheres[0] if hemispheres else None
    axis = "lat" if hemisphere in {"N", "S"} else ("lon" if hemisphere else expected_axis)
    if hemisphere and expected_axis and axis != expected_axis:
        raise CoordinateInputError("The hemisphere does not match the latitude/longitude label.")
    body = re.sub(r"[NSEW]", "", value).strip()
    if re.fullmatch(_ANGLE, body) is None:
        raise CoordinateInputError("Use decimal degrees or degrees° minutes′ seconds″.")
    numbers = [float(number) for number in re.findall(_NUMBER, body)]
    degrees = numbers[0]
    if len(numbers) > 1:
        if not degrees.is_integer():
            raise CoordinateInputError("Use whole degrees when minutes are supplied.")
        if not 0 <= numbers[1] < 60:
            raise CoordinateInputError("Minutes must be at least 0 and less than 60.")
        if len(numbers) > 2:
            if not numbers[1].is_integer():
                raise CoordinateInputError("Use whole minutes when seconds are supplied.")
            if not 0 <= numbers[2] < 60:
                raise CoordinateInputError("Seconds must be at least 0 and less than 60.")
        magnitude = abs(degrees) + numbers[1] / 60 + (numbers[2] / 3600 if len(numbers) > 2 else 0)
    else:
        magnitude = abs(degrees)
    negative = body.startswith("-")
    if hemisphere:
        hemisphere_negative = hemisphere in {"S", "W"}
        if (negative and not hemisphere_negative) or (body.startswith("+") and hemisphere_negative):
            raise CoordinateInputError("The signed value conflicts with its hemisphere.")
        negative = hemisphere_negative
    result = -magnitude if negative else magnitude
    if not math.isfinite(result):
        raise CoordinateInputError("Coordinates must be finite numbers.")
    return result, axis


def _checked(lat, lon, explanation):
    if not math.isfinite(lat) or not -90 <= lat <= 90:
        raise CoordinateInputError("Latitude must be between -90° and 90°.")
    if not math.isfinite(lon) or not -180 <= lon <= 180:
        raise CoordinateInputError("Longitude must be between -180° and 180°.")
    return CoordinateInterpretation(lat, lon, explanation)


def interpret_coordinates(text, *, order="auto"):
    """Return confirmed axes or an actionable error, never guess a plausible pair.

    Automatic mode accepts axis labels, N/S/E/W, and a plain pair only when one
    magnitude exceeds 90° and therefore can only be longitude. Two values inside
    ±90° require labels/hemispheres or an explicitly selected axis order.
    """
    if order not in {"auto", "lat_lon", "lon_lat"}:
        raise CoordinateInputError("Choose Automatic, Latitude first, or Longitude first.")
    value = _normalized(text)
    labels = list(_LABEL.finditer(value))
    if labels:
        if len(labels) != 2 or value[:labels[0].start()].strip(_SEPARATORS):
            raise CoordinateInputError("Supply exactly one latitude and one longitude label.")
        coordinates = {}
        for position, match in enumerate(labels):
            axis = "lat" if match.group(1).lower() in {"lat", "latitude"} else "lon"
            if axis in coordinates:
                raise CoordinateInputError("Supply one latitude and one longitude, not a repeated axis.")
            end = labels[position + 1].start() if position + 1 < len(labels) else len(value)
            coordinates[axis], _axis = _angle(value[match.end():end], expected_axis=axis)
        return _checked(coordinates["lat"], coordinates["lon"], "Latitude/longitude labels")
    chunks = list(_HEMISPHERE_ANGLE.finditer(value))
    if chunks:
        if len(chunks) != 2:
            raise CoordinateInputError("Supply one N/S latitude and one E/W longitude.")
        remainder = value[:chunks[0].start()] + value[chunks[0].end():chunks[1].start()] + value[chunks[1].end():]
        if remainder.strip(_SEPARATORS):
            raise CoordinateInputError("Remove extra text and supply one latitude/longitude pair.")
        coordinates = {}
        for chunk in chunks:
            number, axis = _angle(chunk.group())
            if axis in coordinates:
                raise CoordinateInputError("Supply one N/S latitude and one E/W longitude.")
            coordinates[axis] = number
        return _checked(coordinates["lat"], coordinates["lon"], "Hemisphere-marked coordinates")
    pair = re.fullmatch(rf"\s*({_NUMBER})\s*(?:[,;/]\s*|\s+)({_NUMBER})\s*", value.strip("() "))
    if pair is None:
        raise CoordinateInputError("Use two decimal numbers, axis labels, or N/S/E/W coordinates.")
    first, second = map(float, pair.groups())
    if order == "lat_lon":
        return _checked(first, second, "Explicit order: latitude, longitude")
    if order == "lon_lat":
        return _checked(second, first, "Explicit order: longitude, latitude")
    if abs(first) > 90 and abs(second) <= 90:
        return _checked(second, first, "First value can only be longitude")
    if abs(second) > 90 and abs(first) <= 90:
        return _checked(first, second, "Second value can only be longitude")
    if abs(first) > 90 or abs(second) > 90:
        raise CoordinateInputError("A latitude must be between -90° and 90°.")
    raise CoordinateInputError(
        "Both values could be latitude. Choose Latitude first / Longitude first, or add lat/lon labels or N/S/E/W."
    )

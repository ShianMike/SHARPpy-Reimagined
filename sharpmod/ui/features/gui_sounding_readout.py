"""One truthful vertical sample shared by the Skew-T and hodograph."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import math
import weakref

import numpy as np
import numpy.ma as ma
from qtpy.QtCore import QEvent, QObject, QSize, Qt
from qtpy.QtWidgets import QLabel, QSizePolicy

from sharpmod.sharptab import interp
from sharpmod.sharptab.constants import is_missing
from sharpmod.ui.styles.theme import OBJ_NUMERIC

KT_TO_MS = 0.514444
_PAUSED_TEXT = "Linked readout paused in Edit mode."
_EMPTY_TEXT = "Linked readout: move over the Skew-T or hodograph."


@dataclass(frozen=True)
class ReadoutSample:
    pressure_hpa: float | None
    height_agl_m: float | None
    height_msl_m: float | None
    temperature_c: float | None
    dewpoint_c: float | None
    wind_direction_deg: float | None
    wind_speed_kt: float | None


EMPTY_SAMPLE = ReadoutSample(None, None, None, None, None, None, None)


def _number(value, *, minimum=None, maximum=None) -> float | None:
    if value is None or is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # SHARPpy's historical raw missing sentinel can still reach plugin/profile
    # objects that predate masked-array normalization.
    if not math.isfinite(number) or number <= -9000.0:
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


# One missing-aware conversion for every sounding measurement surface. The edit
# feedback strip needs exactly the same sentinel/NaN/range handling, so it
# imports this rather than re-deriving it.
finite_number = _number


def _interpolated(callable_, *args, minimum=None, maximum=None):
    try:
        value = callable_(*args)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    return _number(value, minimum=minimum, maximum=maximum)


def _has_local_support(profile, coordinate_name, target, field_names) -> bool:
    """Require the reported level or its immediate bracket to contain data.

    The shared scientific interpolator intentionally skips masked samples for
    broad calculation workflows. A point readout must be stricter: silently
    drawing a value across a reported-data gap would imply an observation that
    is not present.
    """
    try:
        coordinate = ma.asarray(getattr(profile, coordinate_name), dtype=float)
        fields = [ma.asarray(getattr(profile, name), dtype=float) for name in field_names]
    except (AttributeError, TypeError, ValueError):
        return False
    if coordinate.ndim != 1 or any(
        field.ndim != 1 or len(field) != len(coordinate) for field in fields
    ):
        return False

    def available(index: int) -> bool:
        return all(_number(field[index]) is not None for field in fields)

    coordinates = [_number(value) for value in coordinate]
    exact = [
        index
        for index, value in enumerate(coordinates)
        if value is not None
        and math.isclose(value, target, rel_tol=1e-9, abs_tol=1e-6)
    ]
    if exact:
        return any(available(index) for index in exact)
    for index in range(len(coordinates) - 1):
        low, high = coordinates[index], coordinates[index + 1]
        if low is None or high is None:
            continue
        if min(low, high) < target < max(low, high):
            return available(index) and available(index + 1)
    return False


def _profile_interpolated(
    callable_,
    profile,
    pressure,
    field_names,
    *,
    minimum=None,
    maximum=None,
):
    if not _has_local_support(profile, "pres", pressure, field_names):
        return None
    return _interpolated(
        callable_, profile, pressure, minimum=minimum, maximum=maximum
    )


def readout_at_pressure(profile, pressure_hpa) -> ReadoutSample:
    """Return one internally consistent, missing-aware profile sample."""
    pressure = _number(pressure_hpa, minimum=0.01, maximum=1200.0)
    if pressure is None:
        return EMPTY_SAMPLE
    if profile is None:
        return ReadoutSample(pressure, None, None, None, None, None, None)

    height_msl = _profile_interpolated(
        interp.hght,
        profile,
        pressure,
        ("hght",),
        minimum=-1000.0,
        maximum=60000.0,
    )
    height_agl = None
    if height_msl is not None:
        height_agl = _interpolated(
            interp.to_agl,
            profile,
            height_msl,
            minimum=-1000.0,
            maximum=60000.0,
        )
    temperature = _profile_interpolated(
        interp.temp,
        profile,
        pressure,
        ("tmpc",),
        minimum=-273.15,
        maximum=100.0,
    )
    dewpoint = _profile_interpolated(
        interp.dwpt,
        profile,
        pressure,
        ("dwpc",),
        minimum=-273.15,
        maximum=100.0,
    )
    try:
        has_wind = _has_local_support(profile, "pres", pressure, ("u", "v"))
        direction_raw, speed_raw = (
            interp.vec(profile, pressure) if has_wind else (None, None)
        )
    except (AttributeError, IndexError, TypeError, ValueError):
        direction_raw, speed_raw = None, None
    direction = _number(direction_raw, minimum=0.0, maximum=360.0)
    speed = _number(speed_raw, minimum=0.0, maximum=500.0)
    if direction is not None:
        direction %= 360.0
    if direction is None or speed is None:
        direction = speed = None

    return ReadoutSample(
        pressure,
        height_agl,
        height_msl,
        temperature,
        dewpoint,
        direction,
        speed,
    )


def _temperature_text(value_c: float | None, units: str, name: str) -> str:
    if value_c is None:
        return f"{name} not reported"
    if str(units).strip().casefold() == "fahrenheit":
        return f"{name} {value_c * 9.0 / 5.0 + 32.0:.1f} °F"
    return f"{name} {value_c:.1f} °C"


def format_readout(
    sample: ReadoutSample,
    *,
    temp_units: str = "Celsius",
    wind_units: str = "knots",
) -> str:
    """Format every required field with explicit references and missing text."""
    if sample.pressure_hpa is None:
        pressure = "Pressure not reported"
    else:
        pressure = f"{sample.pressure_hpa:.1f} hPa"

    if sample.height_agl_m is not None and sample.height_msl_m is not None:
        height = (
            f"{sample.height_agl_m:,.0f} m AGL / "
            f"{sample.height_msl_m:,.0f} m MSL"
        )
    elif sample.height_agl_m is not None:
        height = f"{sample.height_agl_m:,.0f} m AGL / MSL not reported"
    elif sample.height_msl_m is not None:
        height = f"AGL not reported / {sample.height_msl_m:,.0f} m MSL"
    else:
        height = "Height not reported"

    temperature = _temperature_text(
        sample.temperature_c, temp_units, "T"
    )
    dewpoint = _temperature_text(sample.dewpoint_c, temp_units, "Td")
    if sample.wind_direction_deg is None or sample.wind_speed_kt is None:
        wind = "Wind not reported"
    elif sample.wind_speed_kt < 0.05:
        unit = "m/s" if str(wind_units).strip().casefold() == "m/s" else "kt"
        wind = f"Wind calm 0 {unit}"
    else:
        direction = int(round(sample.wind_direction_deg)) % 360
        if str(wind_units).strip().casefold() == "m/s":
            speed = sample.wind_speed_kt * KT_TO_MS
            wind = f"Wind {direction:03d}°/{speed:.1f} m/s"
        else:
            wind = f"Wind {direction:03d}°/{sample.wind_speed_kt:.0f} kt"
    return " · ".join((pressure, height, temperature, dewpoint, wind))


def nearest_hodograph_height(
    u,
    v,
    height_msl,
    *,
    x: float,
    y: float,
    to_pixels,
    max_distance: float = 18.0,
) -> float | None:
    """Return the height on the nearest valid hodograph segment."""
    u_values = ma.asarray(u, dtype=float)
    v_values = ma.asarray(v, dtype=float)
    h_values = ma.asarray(height_msl, dtype=float)
    if not (u_values.ndim == v_values.ndim == h_values.ndim == 1):
        return None
    if not (len(u_values) == len(v_values) == len(h_values)) or len(u_values) < 2:
        return None
    mask = (
        ma.getmaskarray(u_values)
        | ma.getmaskarray(v_values)
        | ma.getmaskarray(h_values)
    )
    uf = np.asarray(u_values.filled(np.nan), dtype=float)
    vf = np.asarray(v_values.filled(np.nan), dtype=float)
    hf = np.asarray(h_values.filled(np.nan), dtype=float)
    valid = (~mask) & np.isfinite(uf) & np.isfinite(vf) & np.isfinite(hf)
    try:
        px, py = to_pixels(uf, vf)
    except (TypeError, ValueError):
        return None
    px = np.asarray(ma.asarray(px).filled(np.nan), dtype=float)
    py = np.asarray(ma.asarray(py).filled(np.nan), dtype=float)
    if px.shape != uf.shape or py.shape != vf.shape:
        return None

    best_distance = float(max_distance)
    best_height = None
    for index in range(len(uf) - 1):
        if not (valid[index] and valid[index + 1]):
            continue
        x0, y0 = px[index], py[index]
        dx, dy = px[index + 1] - x0, py[index + 1] - y0
        if not all(math.isfinite(value) for value in (x0, y0, dx, dy)):
            continue
        length_sq = dx * dx + dy * dy
        if length_sq <= 0.0:
            continue
        fraction = max(
            0.0,
            min(1.0, ((float(x) - x0) * dx + (float(y) - y0) * dy) / length_sq),
        )
        near_x, near_y = x0 + fraction * dx, y0 + fraction * dy
        distance = math.hypot(float(x) - near_x, float(y) - near_y)
        if distance <= best_distance:
            best_distance = distance
            best_height = hf[index] + fraction * (hf[index + 1] - hf[index])
    return _number(best_height, minimum=-1000.0, maximum=60000.0)


class MeasurementLabel(QLabel):
    """A monospace label that wraps between complete measurements.

    Both the linked readout and the edit-feedback strip present the same kind
    of " · "-separated measurement list, so they share this one widget rather
    than each re-deriving the wrapping, elision-free layout, and accessible
    text behavior that large interface text sizes require.
    """

    MIN_WIDTH = 300
    MAX_WIDTH = 980

    def __init__(
        self,
        parent=None,
        *,
        accessible_name: str = "Linked sounding level readout",
        minimum_width: int | None = None,
    ):
        super().__init__(parent)
        self._full_text = ""
        if minimum_width is not None:
            self.MIN_WIDTH = max(0, int(minimum_width))
        self.setObjectName(OBJ_NUMERIC)
        self.setAccessibleName(accessible_name)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self.setAccessibleDescription(self._full_text)
        self._refresh_layout()

    def sizeHint(self):  # noqa: N802 - Qt override
        metrics = self.fontMetrics()
        display = self._display_for_width(self.MAX_WIDTH)
        lines = display.splitlines() or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines)
        height = max(super().sizeHint().height(), metrics.lineSpacing() * len(lines) + 4)
        return QSize(max(self.MIN_WIDTH, min(self.MAX_WIDTH, width)), height)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_layout()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in {QEvent.FontChange, QEvent.StyleChange}:
            self._refresh_layout()

    def _refresh_layout(self) -> None:
        display = self._display_for_width(max(self.MIN_WIDTH, self.width()))
        QLabel.setText(self, display)
        lines = display.count("\n") + 1
        self.setMinimumHeight(self.fontMetrics().lineSpacing() * lines + 4)

    def _display_for_width(self, width: int) -> str:
        """Wrap between complete measurements so none silently disappears."""
        if not self._full_text:
            return ""
        metrics = self.fontMetrics()
        segments = self._full_text.split(" · ")
        lines = []
        current = ""
        for segment in segments:
            candidate = f"{current} · {segment}" if current else segment
            if not current or metrics.horizontalAdvance(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = segment
        if current:
            lines.append(current)
        return "\n".join(lines)


# The linked readout keeps its original private name; the shared widget above
# is the same class, now reused by the edit-feedback strip.
_ReadoutLabel = MeasurementLabel


class LinkedReadoutController(QObject):
    """Synchronize both scientific plots and one accessible text readout."""

    def __init__(self, win):
        super().__init__(win)
        self._win_ref = weakref.ref(win)
        self._sound = win.spc_widget.sound
        self._hodo = win.spc_widget.hodo
        self._enabled = bool(getattr(self._sound, "readout", False))
        self.sample = EMPTY_SAMPLE
        self.label = _ReadoutLabel(win)
        win.statusBar().addPermanentWidget(self.label, 1)

        self._sound.cursor_toggle.connect(self._on_cursor_toggle)
        self._sound.cursor_move.connect(self._on_cursor_move)
        self._hodo.installEventFilter(self)
        win.spc_widget._sharpmod_linked_readout = self
        win._sharpmod_linked_readout = self
        if self._enabled:
            self.refresh()
        else:
            self.label.set_full_text(_EMPTY_TEXT)

    def refresh(self) -> None:
        if not self._enabled:
            self.label.set_full_text(_PAUSED_TEXT)
            return
        pressure = getattr(self._sound, "readout_pres", None)
        profile = getattr(self._sound, "prof", None)
        self.sample = readout_at_pressure(profile, pressure)
        text = format_readout(
            self.sample,
            temp_units=getattr(self._sound, "sfc_units", "Celsius"),
            wind_units=getattr(self._hodo, "wind_units", "knots"),
        )
        self.label.set_full_text(f"Linked · {text}")
        height = self.sample.height_agl_m
        try:
            self._hodo.cursorMove(height if height is not None else -999.0)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            try:
                self._hodo.readout_hght = height if height is not None else -999.0
                self._hodo.update()
            except (AttributeError, RuntimeError):
                pass

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is not self._hodo or event.type() != QEvent.MouseMove:
            return False
        if not self._enabled:
            return False
        try:
            if event.buttons() & Qt.LeftButton:
                return False
        except AttributeError:
            pass
        win = self._win_ref()
        mode = getattr(win, "_sharpmod_interaction_mode", None) if win else None
        if mode is not None and getattr(mode, "mode", "inspect") != "inspect":
            return False
        point = self._event_point(event)
        if point is not None:
            self._sync_from_hodograph(point[0], point[1])
        return False

    def _on_cursor_toggle(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if self._enabled:
            self.refresh()
        else:
            self.label.set_full_text(_PAUSED_TEXT)

    def _on_cursor_move(self, _legacy_height: float) -> None:
        if self._enabled:
            self.refresh()

    @staticmethod
    def _event_point(event):
        try:
            point = event.position()
            return float(point.x()), float(point.y())
        except (AttributeError, TypeError, ValueError):
            try:
                point = event.pos()
                return float(point.x()), float(point.y())
            except (AttributeError, TypeError, ValueError):
                return None

    def _sync_from_hodograph(self, x: float, y: float) -> None:
        profile = getattr(self._hodo, "prof", None)
        if profile is None:
            return
        height_msl = nearest_hodograph_height(
            getattr(self._hodo, "u", ()),
            getattr(self._hodo, "v", ()),
            getattr(self._hodo, "hght", ()),
            x=x,
            y=y,
            to_pixels=self._hodo.uv_to_pix,
        )
        if height_msl is None:
            return
        if not _has_local_support(profile, "hght", height_msl, ("pres",)):
            return
        pressure = _interpolated(
            interp.pres, profile, height_msl, minimum=0.01, maximum=1200.0
        )
        if pressure is None:
            return
        self._sound.readout_pres = pressure
        try:
            self._sound.track_cursor = True
            self._sound.updateReadout()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self.refresh()


def install_linked_readout(win):
    """Install one idempotent linked readout controller on a sounding window."""
    existing = getattr(win, "_sharpmod_linked_readout", None)
    if existing is not None:
        return existing
    sw = getattr(win, "spc_widget", None)
    if sw is None or getattr(sw, "sound", None) is None or getattr(sw, "hodo", None) is None:
        return None
    return LinkedReadoutController(win)


def install_linked_readout_hooks(spc_widget_class) -> None:
    """Refresh a stationary linked cursor after profile/time changes."""
    if getattr(spc_widget_class, "_sharpmod_readout_hooks_installed", False):
        return
    original = getattr(spc_widget_class, "updateProfs", None)
    if not callable(original):
        return

    @wraps(original)
    def update_profs(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        controller = getattr(self, "_sharpmod_linked_readout", None)
        if controller is not None:
            controller.refresh()
        return result

    spc_widget_class.updateProfs = update_profs
    spc_widget_class._sharpmod_readout_hooks_installed = True


__all__ = [
    "EMPTY_SAMPLE",
    "KT_TO_MS",
    "MeasurementLabel",
    "ReadoutSample",
    "LinkedReadoutController",
    "finite_number",
    "format_readout",
    "install_linked_readout",
    "install_linked_readout_hooks",
    "nearest_hodograph_height",
    "readout_at_pressure",
]

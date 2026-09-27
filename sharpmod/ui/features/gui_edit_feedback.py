"""Original-versus-proposed edit feedback and an immutable original overlay.

The vendored Skew-T and hodograph commit a drag on mouse release and then
simply redraw the modified profile.  Nothing states what the value used to be,
what it is becoming, or how large the change is, and the pristine profile that
``ProfCollection`` already retains for Reset is never drawn.  This module adds
that feedback while reusing the existing retained originals, the existing
history wrapper, and the existing unit preferences.

Two deliberate truthfulness rules apply throughout:

* The baseline shown as ``original`` is the collection's retained pre-edit
  profile -- the same data the overlay draws -- so the readout and the ghost
  curve always refer to one thing.
* A level comparison is only offered when the retained original still shares
  the current profile's vertical grid at that index.  After an interpolation the
  index no longer means the same level, so the comparison is withheld instead of
  subtracting two different levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import logging
import math
import weakref

import numpy.ma as ma
from qtpy import QtCore, QtGui
from qtpy.QtCore import QEvent, QObject, Qt
from qtpy.QtGui import QAction, QColor
from qtpy.QtWidgets import QMenu

from sharpmod.ui.features.gui_sounding_readout import (
    KT_TO_MS,
    MeasurementLabel,
    finite_number,
)
from sharpmod.sharptab import interp

_LOGGER = logging.getLogger(__name__)

OVERLAY_SETTING_KEY = "viewer/original_overlay"

#: Fields whose per-level change is worth naming in the readout.
TEMPERATURE_FIELDS = ("tmpc", "dwpc")
WIND_FIELDS = ("wdir", "wspd")
COMPONENT_FIELDS = ("u", "v")
TRACKED_FIELDS = TEMPERATURE_FIELDS + WIND_FIELDS + COMPONENT_FIELDS

_FIELD_LABELS = {"tmpc": "T", "dwpc": "Td"}
_DEVIANT_LABELS = {"right": "Right-mover storm motion", "left": "Left-mover storm motion"}

# A level index only survives a vertical-grid change when its pressure does.
_PRESSURE_TOLERANCE_HPA = 0.05

_GHOST_ALPHA = 170
_GHOST_TEMP_STYLE = Qt.DashLine
_GHOST_DEWP_STYLE = Qt.DashDotLine
_GHOST_WIND_PATTERN = (5.0, 4.0)
_GHOST_CAPTION = "Original profile: dashed, edits ringed"
_GHOST_MAX_AGL_M = 12000.0

#: An edited level is marked with a hollow ring at its original value and a
#: connector to the current value. Without this the dashed original curve is
#: invisible wherever the data is unchanged, which is nearly everywhere after a
#: single-level edit, so the overlay would promise a reference the user cannot
#: see exactly where it matters.
_MARKER_RADIUS = 4.5
_MARKER_LIMIT = 60
_VALUE_TOLERANCE_C = 0.01
_VALUE_TOLERANCE_KT = 0.05

_EMPTY_TEXT = ""
_NOT_COMPARABLE = (
    "Original level not comparable after interpolation; use Reset to restore it."
)


# ---------------------------------------------------------------------------
# Unit-aware presentation
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Reading actual profile levels
# ---------------------------------------------------------------------------


def _level_index(index) -> int | None:
    try:
        position = int(index)
    except (TypeError, ValueError):
        return None
    return position if position >= 0 else None


def level_values(profile, index, fields=TRACKED_FIELDS) -> dict[str, float | None]:
    """Return one level's reported values, with missing data kept as ``None``."""
    values: dict[str, float | None] = {}
    position = _level_index(index)
    if profile is None or position is None:
        return values
    for field in fields:
        array = getattr(profile, field, None)
        if array is None:
            continue
        try:
            raw = array[position]
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        values[field] = None if raw is ma.masked else finite_number(raw)
    return values


def level_pressure(profile, index) -> float | None:
    """Return the reported pressure at ``index``, or ``None`` when unavailable."""
    position = _level_index(index)
    if profile is None or position is None:
        return None
    array = getattr(profile, "pres", None)
    if array is None:
        return None
    try:
        raw = array[position]
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if raw is ma.masked:
        return None
    return finite_number(raw, minimum=0.01, maximum=1200.0)


def changed_fields(before, after) -> tuple[str, ...]:
    """Return the tracked fields whose reported value actually differs."""
    changed = []
    for field in TRACKED_FIELDS:
        if field not in before and field not in after:
            continue
        first, second = before.get(field), after.get(field)
        if first is None and second is None:
            continue
        if first is None or second is None:
            changed.append(field)
            continue
        if not math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-9):
            changed.append(field)
    return tuple(changed)


def _active_collection(spc_widget):
    try:
        index = int(spc_widget.pc_idx)
        return spc_widget.prof_collections[index]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _active_profile(spc_widget):
    collection = _active_collection(spc_widget)
    if collection is None:
        return None
    try:
        return collection.getHighlightedProf()
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def retained_original(collection):
    """Return the pristine profile the collection already retains for Reset."""
    if collection is None:
        return None
    originals = getattr(collection, "_orig_profs", None)
    if not isinstance(originals, dict):
        return None
    try:
        return originals.get(collection._prof_idx)
    except AttributeError:
        return None


def comparable_level(original, current, index) -> bool:
    """Whether ``index`` still refers to the same level in both profiles."""
    if original is None or current is None:
        return False
    if original is current:
        return True
    original_pressure = level_pressure(original, index)
    current_pressure = level_pressure(current, index)
    if original_pressure is None or current_pressure is None:
        return False
    try:
        if len(original.pres) != len(current.pres):
            return False
    except (AttributeError, TypeError):
        return False
    return abs(original_pressure - current_pressure) <= _PRESSURE_TOLERANCE_HPA


# ---------------------------------------------------------------------------
# Vendored mutation hooks
# ---------------------------------------------------------------------------

_HOOKED_METHODS = (
    "modifyProf",
    "modifyVector",
    "interpProf",
    "resetProfModifications",
    "resetProfInterpolation",
    "resetVector",
)


def install_edit_feedback_hooks(spc_widget_class) -> None:
    """Report the fields one vendored edit actually changed on the active profile.

    Install after the history and interaction-mode wrappers so a blocked
    Inspect-mode gesture never produces feedback: the guarded call changes
    nothing, so nothing is reported.
    """
    if getattr(spc_widget_class, "_sharpmod_edit_feedback_hooks_installed", False):
        return
    for name in _HOOKED_METHODS:
        original = getattr(spc_widget_class, name, None)
        if not callable(original):
            continue

        @wraps(original)
        def wrapped(self, *args, __original=original, __name=name, **kwargs):
            controller = getattr(self, "_sharpmod_edit_feedback", None)
            if controller is None:
                return __original(self, *args, **kwargs)
            index = args[0] if (__name == "modifyProf" and args) else None
            deviant = args[0] if (__name == "modifyVector" and args) else None
            before_profile = _active_profile(self)
            before = level_values(before_profile, index)
            before_vector = _storm_motion(before_profile)
            result = __original(self, *args, **kwargs)
            after_profile = _active_profile(self)
            fields = changed_fields(before, level_values(after_profile, index))
            if fields:
                _notify(controller, "profile_edited", index, fields)
            elif deviant is not None:
                after_vector = _storm_motion(after_profile)
                if before_vector != after_vector:
                    _notify(
                        controller,
                        "vector_edited",
                        str(deviant),
                        before_vector,
                        after_vector,
                    )
            _notify(controller, "refresh")
            return result

        setattr(spc_widget_class, name, wrapped)
    spc_widget_class._sharpmod_edit_feedback_hooks_installed = True


def _notify(controller, name: str, *args) -> None:
    handler = getattr(controller, name, None)
    if not callable(handler):
        return
    try:
        handler(*args)
    except Exception:
        _LOGGER.exception("edit_feedback.%s_failed", name)


def _storm_motion(profile):
    """Return the right/left mover components as plain floats, if reported."""
    vector = getattr(profile, "srwind", None)
    try:
        values = tuple(finite_number(item, minimum=-500.0, maximum=500.0) for item in vector)
    except TypeError:
        return None
    return values if len(values) >= 4 else None


# ---------------------------------------------------------------------------
# Original-profile overlay drawing
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

_POINTER_EVENTS = {QEvent.MouseMove, QEvent.MouseButtonRelease}


@dataclass(frozen=True)
class _Preview:
    text: str


def _setting_flag(settings, key: str, default: bool) -> bool:
    if settings is None:
        return default
    try:
        raw = settings.value(key, default, bool)
    except TypeError:
        raw = settings.value(key, default)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().casefold()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return default


def _window_menu(win, title: str):
    wanted = title.replace("&", "").strip().casefold()
    try:
        menus = win.menuBar().findChildren(QMenu)
    except (AttributeError, RuntimeError):
        return None
    for menu in menus:
        if menu.title().replace("&", "").strip().casefold() == wanted:
            return menu
    return None


class EditFeedbackController(QObject):
    """Show original/proposed/change for the active edit and the original curve."""

    def __init__(self, win, *, settings=None):
        super().__init__(win)
        self._win_ref = weakref.ref(win)
        self._settings = settings
        self._sw = win.spc_widget
        self._sound = self._sw.sound
        self._hodo = self._sw.hodo
        self._preview: _Preview | None = None
        self._level: int | None = None
        self._level_collection = None
        self._overlay_profile = None
        self._overlay_wanted = _setting_flag(settings, OVERLAY_SETTING_KEY, True)

        install_original_overlay(self._sound, self._hodo)
        install_drag_coordinate_refresh(type(self._sound))
        # The canvas is normally resized before this controller exists, so bring
        # the existing draggables onto the current transform straight away.
        _refresh_drag_coordinates(self._sound)

        self.label = MeasurementLabel(
            win,
            accessible_name="Sounding edit feedback",
            minimum_width=240,
        )
        self.label.hide()
        try:
            win.statusBar().addPermanentWidget(self.label)
        except (AttributeError, RuntimeError):
            pass

        self.overlay_action = QAction("Show original profile overlay", win)
        self.overlay_action.setCheckable(True)
        self.overlay_action.setToolTip(
            "Draw the retained original sounding as dashed reference curves on "
            "the Skew-T and hodograph"
        )
        self.overlay_action.setStatusTip(
            "The original profile is drawn dashed; the edited profile stays solid."
        )
        self._install_overlay_action(win)
        self.overlay_action.toggled.connect(self._on_overlay_toggled)

        for surface in (self._sound, self._hodo):
            try:
                surface.installEventFilter(self)
            except (AttributeError, RuntimeError):
                pass

        self._sw._sharpmod_edit_feedback = self
        win._sharpmod_edit_feedback = self
        win._sharpmod_original_overlay_action = self.overlay_action

        history = getattr(win, "_sharpmod_history", None)
        add_listener = getattr(history, "add_listener", None)
        if callable(add_listener):
            add_listener(self.refresh)
        else:
            self.refresh()

    # -- public API ---------------------------------------------------------

    def profile_edited(self, level_index, fields) -> None:
        """Record the level a committed vendored edit actually changed."""
        self._preview = None
        position = _level_index(level_index)
        if position is not None and fields:
            self._level = position
            # Remembered weakly and bound to the collection that was edited, so
            # a later undo/redo can restore the same description while another
            # loaded profile never inherits this level's comparison.
            collection = _active_collection(self._sw)
            self._level_collection = (
                weakref.ref(collection) if collection is not None else None
            )
        self.refresh()

    def vector_edited(self, deviant, original, proposed) -> None:
        """Report a committed storm-motion change with its original vector."""
        self._preview = None
        text = format_vector_change(
            deviant,
            _deviant_components(deviant, original),
            _deviant_components(deviant, proposed),
            wind_units=self._wind_units(),
            prefix="Edited",
        )
        self._show(text)

    def refresh(self) -> None:
        """Re-resolve the retained original, the overlay, and the readout text."""
        original = retained_original(_active_collection(self._sw))
        self._apply_overlay(original)
        self._refresh_action(original is not None)
        if self._preview is not None:
            self._show(self._preview.text)
            return
        self._show(self._committed_text(original))

    @property
    def overlay_profile(self):
        return self._overlay_profile

    # -- overlay ------------------------------------------------------------

    def _install_overlay_action(self, win) -> None:
        menu = _window_menu(win, "View")
        if menu is None:
            try:
                menu = win.menuBar().addMenu("&View")
            except (AttributeError, RuntimeError):
                return
        menu.addAction(self.overlay_action)
        try:
            from qtpy.QtGui import QKeySequence

            from sharpmod.ui.features.gui_commands import collect_commands

            shortcut = QKeySequence("Ctrl+Alt+O")
            taken = any(
                shortcut == key
                for command in collect_commands(win)
                for key in command.action.shortcuts()
            )
            if not taken:
                self.overlay_action.setShortcut(shortcut)
        except Exception:
            _LOGGER.debug("edit_feedback.overlay_shortcut_skipped", exc_info=True)

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._overlay_wanted = bool(checked)
        if self._settings is not None:
            try:
                self._settings.setValue(OVERLAY_SETTING_KEY, bool(checked))
            except (AttributeError, RuntimeError, TypeError):
                pass
        self.refresh()

    def _refresh_action(self, available: bool) -> None:
        action = self.overlay_action
        try:
            action.setEnabled(bool(available))
            wanted = bool(available and self._overlay_wanted)
            if action.isChecked() != wanted:
                blocked = action.blockSignals(True)
                action.setChecked(wanted)
                action.blockSignals(blocked)
        except RuntimeError:
            pass

    def _apply_overlay(self, original) -> None:
        wanted = original if (original is not None and self._overlay_wanted) else None
        if wanted is self._overlay_profile:
            return
        self._overlay_profile = wanted
        for surface in (self._sound, self._hodo):
            try:
                surface._sharpmod_original_profile = wanted
            except (AttributeError, RuntimeError):
                continue
            _redraw(surface)

    # -- readout text -------------------------------------------------------

    def _committed_text(self, original) -> str:
        if original is None or self._level is None:
            # Undo, Reset, or a profile with no retained original: there is
            # nothing to compare, but the level stays remembered so a Redo can
            # restore the same description.
            return _EMPTY_TEXT
        remembered = self._level_collection() if self._level_collection else None
        if remembered is not None and remembered is not _active_collection(self._sw):
            return _EMPTY_TEXT
        current = _active_profile(self._sw)
        if not comparable_level(original, current, self._level):
            return _NOT_COMPARABLE
        before = level_values(original, self._level)
        after = level_values(current, self._level)
        fields = changed_fields(before, after)
        if not fields:
            # An undo restored this level; there is nothing left to compare.
            return _EMPTY_TEXT
        selected_before, selected_after = _select_fields(before, after, fields)
        return format_level_changes(
            selected_before,
            selected_after,
            pressure_hpa=level_pressure(current, self._level),
            temp_units=self._temp_units(),
            wind_units=self._wind_units(),
            prefix="Edited",
        )

    def _show(self, text: str) -> None:
        try:
            if text:
                self.label.set_full_text(text)
                self.label.show()
            else:
                self.label.set_full_text("")
                self.label.hide()
        except RuntimeError:
            pass

    def _temp_units(self) -> str:
        return str(getattr(self._sound, "sfc_units", "Celsius") or "Celsius")

    def _wind_units(self) -> str:
        return str(getattr(self._hodo, "wind_units", "knots") or "knots")

    # -- live drag preview --------------------------------------------------

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        kind = event.type()
        if kind not in _POINTER_EVENTS:
            return False
        if kind == QEvent.MouseButtonRelease:
            if self._preview is not None:
                self._preview = None
                self.refresh()
            return False
        if kind != QEvent.MouseMove or not self._edits_allowed():
            return False
        try:
            if watched is self._sound:
                self._preview_skew(event)
            elif watched is self._hodo:
                self._preview_hodo(event)
        except Exception:
            _LOGGER.debug("edit_feedback.preview_failed", exc_info=True)
        return False

    def _edits_allowed(self) -> bool:
        """Never propose a change the current mode would refuse to commit."""
        win = self._win_ref()
        mode = getattr(win, "_sharpmod_interaction_mode", None) if win else None
        allows = getattr(mode, "allows_edits", None)
        if not callable(allows):
            return True
        try:
            return bool(allows())
        except Exception:
            return False

    def _baseline(self, index):
        """Return the values the edit will replace, preferring the retained original."""
        current = _active_profile(self._sw)
        original = retained_original(_active_collection(self._sw))
        if original is not None and comparable_level(original, current, index):
            return level_values(original, index)
        return level_values(current, index)

    def _preview_skew(self, event) -> None:
        sound = self._sound
        for name, field, restriction_name in (
            ("drag_tmpc", "tmpc", "_restTmpc"),
            ("drag_dwpc", "dwpc", "_restDwpc"),
        ):
            drag = getattr(sound, name, None)
            if drag is None or not _is_dragging(drag):
                continue
            index = _level_index(getattr(drag, "_drag_idx", None))
            if index is None:
                continue
            point = _event_point(event)
            if point is None:
                continue
            x, y = point
            restriction = getattr(sound, restriction_name, None)
            if callable(restriction):
                x, y = restriction(x, y)
            scale = float(getattr(sound, "scale", 1.0) or 1.0)
            trans_x = (float(x) - float(getattr(sound, "originx", 0.0))) * scale
            trans_y = (float(y) - float(getattr(sound, "originy", 0.0))) * scale
            proposed = finite_number(
                sound.pix_to_tmpc(trans_x, trans_y), minimum=-273.15, maximum=100.0
            )
            baseline = self._baseline(index)
            self._preview = _Preview(
                format_level_changes(
                    {field: baseline.get(field)},
                    {field: proposed},
                    pressure_hpa=level_pressure(_active_profile(self._sw), index),
                    temp_units=self._temp_units(),
                    wind_units=self._wind_units(),
                    prefix="Editing",
                )
            )
            self._show(self._preview.text)
            return

    def _preview_hodo(self, event) -> None:
        hodo = self._hodo
        point = _event_point(event)
        if point is None:
            return
        drag = getattr(hodo, "drag_hodo", None)
        if drag is not None and _is_dragging(drag):
            index = _level_index(getattr(drag, "_drag_idx", None))
            if index is None:
                return
            u, v = hodo.pix_to_uv(point[0], point[1])
            baseline = self._baseline(index)
            self._preview = _Preview(
                format_level_changes(
                    {key: baseline.get(key) for key in WIND_FIELDS},
                    {"u": u, "v": v},
                    pressure_hpa=level_pressure(_active_profile(self._sw), index),
                    temp_units=self._temp_units(),
                    wind_units=self._wind_units(),
                    prefix="Editing",
                )
            )
            self._show(self._preview.text)
            return
        for name, deviant in (("drag_rm", "right"), ("drag_lm", "left")):
            vector_drag = getattr(hodo, name, None)
            if vector_drag is None or not _is_dragging(vector_drag):
                continue
            original = _storm_motion(_active_profile(self._sw))
            if original is None:
                return
            self._preview = _Preview(
                format_vector_change(
                    deviant,
                    _deviant_components(deviant, original),
                    hodo.pix_to_uv(point[0], point[1]),
                    wind_units=self._wind_units(),
                    prefix="Editing",
                )
            )
            self._show(self._preview.text)
            return


def _select_fields(before, after, fields):
    """Keep only the changed measurements, expanding wind to one vector pair."""
    selected_before: dict[str, float | None] = {}
    selected_after: dict[str, float | None] = {}
    for field in TEMPERATURE_FIELDS:
        if field in fields:
            selected_before[field] = before.get(field)
            selected_after[field] = after.get(field)
    if any(field in fields for field in WIND_FIELDS + COMPONENT_FIELDS):
        for field in WIND_FIELDS:
            selected_before[field] = before.get(field)
            selected_after[field] = after.get(field)
    return selected_before, selected_after


def _deviant_components(deviant, vector):
    """Pick the right/left mover pair out of one ``srwind`` tuple."""
    if vector is None:
        return None, None
    if len(vector) == 2:
        return vector[0], vector[1]
    if len(vector) < 4:
        return None, None
    if str(deviant).strip().casefold() == "left":
        return vector[2], vector[3]
    return vector[0], vector[1]


def _is_dragging(drag) -> bool:
    checker = getattr(drag, "isDragging", None)
    if callable(checker):
        try:
            return bool(checker())
        except (RuntimeError, TypeError, ValueError):
            return False
    return getattr(drag, "_drag_idx", None) is not None


def _event_point(event):
    for name in ("position", "pos"):
        getter = getattr(event, name, None)
        if getter is None:
            continue
        try:
            point = getter()
            return float(point.x()), float(point.y())
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def _redraw(surface) -> None:
    for name in ("clearData", "plotData", "update"):
        method = getattr(surface, name, None)
        if not callable(method):
            continue
        try:
            method()
        except Exception:
            _LOGGER.debug("edit_feedback.redraw_failed", exc_info=True)
            return


def install_edit_feedback(win, *, settings=None):
    """Install one idempotent edit-feedback controller on a sounding window."""
    existing = getattr(win, "_sharpmod_edit_feedback", None)
    if existing is not None:
        return existing
    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return None
    if getattr(sw, "sound", None) is None or getattr(sw, "hodo", None) is None:
        return None
    return EditFeedbackController(win, settings=settings)


__all__ = [
    "COMPONENT_FIELDS",
    "OVERLAY_SETTING_KEY",
    "TEMPERATURE_FIELDS",
    "TRACKED_FIELDS",
    "WIND_FIELDS",
    "EditFeedbackController",
    "changed_fields",
    "comparable_level",
    "edited_levels",
    "edited_wind_levels",
    "level_wind_components",
    "wind_component_arrays",
    "format_level_changes",
    "format_vector_change",
    "install_drag_coordinate_refresh",
    "install_edit_feedback",
    "install_edit_feedback_hooks",
    "install_original_overlay",
    "install_surface_overlay",
    "level_pressure",
    "level_values",
    "retained_original",
    "wind_from_components",
]


from sharpmod.ui.edit_overlay import (  # noqa: E402
    _ghost_color,
    _float_array,
    wind_component_arrays,
    level_wind_components,
    _reported,
    edited_levels,
    edited_wind_levels,
    _draw_change_markers,
    _draw_skew_ghost,
    _draw_skew_change_markers,
    _skew_point,
    _draw_ghost_caption,
    _draw_hodo_ghost,
    _draw_hodo_ghost_trace,
    _draw_hodo_change_markers,
    _hodo_point,
    install_surface_overlay,
    install_original_overlay,
    _refresh_drag_coordinates,
    install_drag_coordinate_refresh
)


from sharpmod.ui.edit_formatting import (  # noqa: E402
    _is_fahrenheit,
    _is_meters_per_second,
    _temperature_text,
    _temperature_delta_text,
    _speed_text,
    _wind_text,
    _direction_delta,
    _wind_delta_text,
    wind_from_components,
    _wind_pair,
    _header,
    format_level_changes,
    format_vector_change,
    _pair
)

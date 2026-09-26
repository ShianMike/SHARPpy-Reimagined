"""Forecast-timeline playback: speed, loop range, exact times, and coverage.

The previous toolbar was a slider, three buttons, a loop toggle, and a hard-coded
900 ms timer built out of closures. It could not answer three questions a reader
has constantly:

* *How fast is this going, and can I slow it down?* There was one speed.
* *Which hours did I actually ask for?* The slider spans the hours that **loaded**,
  so a request for twelve hours that returned four presented itself as a
  four-hour forecast with no indication anything was missing.
* *What happened to the ones that are not here?* The failure strings lived on a
  Qt worker object and were deleted with it.

:class:`FrameStrip` answers the last two by drawing one cell per *requested* hour,
coloured by its :mod:`sharpmod.analysis.timeline_frames` state, so a gap is a visible gap
with a reason on hover rather than an absence. The slider still scrubs the loaded
hours, because those are the only ones that can be displayed; the strip is what
says how many there were meant to be.

Every callback here resolves the window from a weak reference. The toolbar, its
actions, and its timer are all children of the window, and Qt holds their
connections C++-side, so a strong capture makes ``win -> child -> connection ->
callback -> win`` an uncollectable cycle whose Python wrapper then outlives the
C++ object and crashes on free. Measured on the equivalent handler in
``gui_viewer._install_tip_bar``: six crashes in fourteen runs with a strong
capture, none in fourteen once held weakly.
"""

from __future__ import annotations

from datetime import datetime
import weakref

from qtpy.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from qtpy.QtGui import QAction, QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.core import local_time
from sharpmod.ui.features.gui_theme import current_theme, mono_font
from sharpmod.ui.styles.theme import SPACE
from sharpmod.analysis.timeline_frames import STATE_LABELS, TimelineCoverage

#: Playback rates, as multipliers of :data:`BASE_INTERVAL_MS`. Offered because a
#: 72-hour timeline at one speed is either too slow to see the trend or too fast
#: to read any single hour, and which of those it is depends on the hour count.
SPEEDS = ((0.25, "0.25x"), (0.5, "0.5x"), (1.0, "1x"), (2.0, "2x"), (4.0, "4x"))

#: The rate the toolbar has always run at, kept as 1x so existing behaviour is
#: the default rather than a new one.
BASE_INTERVAL_MS = 900


def interval_for_speed(speed) -> int:
    """Return the timer interval one speed multiplier needs."""
    try:
        multiplier = float(speed)
    except (TypeError, ValueError):
        multiplier = 1.0
    if multiplier <= 0.0:
        multiplier = 1.0
    return max(40, int(round(BASE_INTERVAL_MS / multiplier)))


def _state_ink(state, theme=None):
    """Map a frame state onto a colour role, never onto hue alone.

    The strip pairs each colour with a distinct fill pattern in
    :meth:`FrameStrip.paintEvent`, so the categories survive a colourblind
    palette and a monochrome print.
    """
    theme = theme or current_theme()
    return {
        "loaded": theme.success,
        "loading": theme.info,
        "requested": theme.text_tertiary,
        "failed": theme.danger,
        "canceled": theme.warning,
        "unavailable": theme.border_strong,
        # Its own role rather than sharing the warning colour with ``canceled``:
        # the two mean different things, and a rendered "?" is not a reliable
        # distinction at cell size or with a substituted font.
        "unknown": theme.text_secondary,
    }.get(state, theme.text_tertiary)


class FrameStrip(QWidget):
    """One cell per requested forecast hour, coloured and patterned by state."""

    #: Emitted with a forecast hour when a cell is chosen.
    frameChosen = Signal(int)

    MIN_CELL = 6.0
    HEIGHT = 18.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sharpmodTimelineFrameStrip")
        self.setAccessibleName("Forecast timeline coverage")
        self.setFixedHeight(int(self.HEIGHT))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._coverage = TimelineCoverage()
        self._active_hour = None
        self._hover_hour = None
        self._range = None
        self._state_words = {}
        self._all_frames_clickable = False
        self._describe()

    # -- data --------------------------------------------------------------- #

    def set_coverage(self, coverage, *, active_hour=None, loop_range=None):
        """Show a coverage ledger, the playing hour, and the loop sub-range."""
        self._coverage = coverage if isinstance(coverage, TimelineCoverage) else TimelineCoverage()
        self._active_hour = None if active_hour is None else int(active_hour)
        self._range = (
            None
            if loop_range is None
            else (int(loop_range[0]), int(loop_range[1]))
        )
        self.setMinimumWidth(int(max(1, len(self._coverage)) * self.MIN_CELL))
        self._describe()
        self.update()

    def coverage(self):
        return self._coverage

    def set_state_words(self, words=None):
        """Override state wording without forking the shared state machine.

        T21 calls a selectable map frame ``ready`` while T09 calls the same
        canonical ledger state ``loaded``.  A presentation alias lets both
        views use one coverage object and still use their required wording.
        """
        self._state_words = {
            str(key): str(value) for key, value in dict(words or {}).items()
        }
        self._describe()
        self.update()

    def set_all_frames_clickable(self, enabled=True):
        """Use map-selection wording where every offered hour can be chosen."""
        self._all_frames_clickable = bool(enabled)
        self._describe()

    def _state_word(self, state):
        return self._state_words.get(state, STATE_LABELS[state])

    def _summary(self):
        if not self._state_words:
            return self._coverage.summary()
        if not self._coverage.frames:
            return "No forecast hours are offered."
        counts = {}
        for frame in self._coverage.frames:
            word = self._state_word(frame.state)
            counts[word] = counts.get(word, 0) + 1
        parts = []
        for word in ("ready", "loading", "missing", "failed"):
            if counts.get(word):
                parts.append(f"{counts.pop(word)} {word}")
        parts.extend(f"{count} {word}" for word, count in counts.items())
        return f"{len(self._coverage.frames)} offered hour(s): " \
            + "; ".join(parts) + "."

    def _describe(self):
        summary = self._summary()
        lines = tuple(
            f"{frame.label}: {self._state_word(frame.state)}"
            + (f" — {frame.reason}" if frame.reason else "")
            for frame in self._coverage.frames
            if frame.state != "loaded"
        )
        if lines:
            summary += " " + " ".join(lines)
        self.setAccessibleDescription(
            "Requested forecast hours and their state. "
            + summary
            + f" {self._state_word('loaded').title()} hours are solid; every "
            "other state is patterned as well as "
            "coloured. Choose an hour to jump to it."
        )
        self.setToolTip(self._summary())

    # -- geometry ----------------------------------------------------------- #

    def _cells(self):
        frames = self._coverage.frames
        if not frames:
            return ()
        width = max(1.0, float(self.width()))
        step = width / len(frames)
        return tuple(
            (QRectF(index * step, 0.0, max(1.0, step - 1.0), float(self.height())), frame)
            for index, frame in enumerate(frames)
        )

    def _frame_at(self, x):
        for rect, frame in self._cells():
            if rect.left() <= x <= rect.right():
                return frame
        return None

    # -- interaction -------------------------------------------------------- #

    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        frame = self._frame_at(event.position().x())
        if frame is not None:
            self.frameChosen.emit(int(frame.forecast_hour))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        frame = self._frame_at(event.position().x())
        hour = None if frame is None else int(frame.forecast_hour)
        if hour != self._hover_hour:
            self._hover_hour = hour
            # Per-cell hover, because the whole point is that the cells differ.
            self.setToolTip(
                self._summary() if frame is None else self._frame_tooltip(frame)
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        if self._hover_hour is not None:
            self._hover_hour = None
            self.setToolTip(self._summary())
            self.update()
        super().leaveEvent(event)

    def _frame_tooltip(self, frame):
        lines = [f"{frame.label} \u2014 {self._state_word(frame.state)}"]
        if isinstance(frame.valid_time, datetime):
            lines.append(f"Valid {frame.valid_time:%Y-%m-%d %H:%MZ}")
        if frame.reason:
            lines.append(str(frame.reason))
        lines.append(
            "Click to select this map hour"
            if self._all_frames_clickable
            else "Click to open this hour"
            if frame.available
            else "This hour has no profile to open"
        )
        return "\n".join(lines)

    # -- painting ----------------------------------------------------------- #

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        theme = current_theme()
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(theme.surface_sunken))
        cells = self._cells()
        if not cells:
            qp.end()
            return
        for rect, frame in cells:
            colour = QColor(_state_ink(frame.state, theme))
            if frame.state == "loaded":
                qp.setPen(Qt.NoPen)
                qp.setBrush(colour)
                qp.drawRect(rect)
            else:
                # Every non-loaded state gets a hollow cell so a gap is visible
                # without relying on hue, plus a pattern that names the category.
                faded = QColor(colour)
                faded.setAlpha(60)
                qp.setPen(Qt.NoPen)
                qp.setBrush(faded)
                qp.drawRect(rect)
                qp.setPen(QPen(colour, 1.2))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(rect)
                self._draw_pattern(qp, rect, frame.state, colour)
            if self._range is not None and not (
                self._range[0] <= frame.forecast_hour <= self._range[1]
            ):
                # Outside the loop range: dim it rather than hide it, so the
                # requested extent stays visible while playback is narrowed.
                shade = QColor(theme.surface_sunken)
                shade.setAlpha(150)
                qp.setPen(Qt.NoPen)
                qp.setBrush(shade)
                qp.drawRect(rect)
        for rect, frame in cells:
            if self._active_hour is not None and frame.forecast_hour == self._active_hour:
                qp.setPen(QPen(QColor(theme.accent), 2.0))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
            elif self._hover_hour is not None and frame.forecast_hour == self._hover_hour:
                qp.setPen(QPen(QColor(theme.focus_ring), 1.2))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        qp.setPen(QPen(QColor(theme.border), 1.0))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0))
        qp.end()

    @staticmethod
    def _draw_pattern(qp, rect, state, colour):
        """Distinguish the non-loaded states without relying on colour."""
        qp.setPen(QPen(colour, 1.2))
        mid_y = rect.center().y()
        if state == "failed":
            # A cross: attempted and broken.
            qp.drawLine(rect.topLeft(), rect.bottomRight())
            qp.drawLine(rect.bottomLeft(), rect.topRight())
        elif state == "canceled":
            # A single slash: deliberately stopped.
            qp.drawLine(rect.bottomLeft(), rect.topRight())
        elif state == "unavailable":
            # A bar: nothing to get.
            qp.drawLine(
                QPointF(rect.left() + 1.0, mid_y), QPointF(rect.right() - 1.0, mid_y)
            )
        elif state == "loading":
            # A rising tick: in flight.
            qp.drawLine(
                QPointF(rect.left() + 1.0, rect.bottom() - 2.0),
                QPointF(rect.right() - 1.0, rect.top() + 2.0),
            )
        elif state == "unknown":
            # A centre dot, not a "?": glyph rendering is not guaranteed at cell
            # size and a substituted font can drop it entirely.
            qp.setBrush(colour)
            radius = min(3.0, rect.width() / 3.0)
            qp.drawEllipse(rect.center(), radius, radius)
            qp.setBrush(Qt.NoBrush)
        # "requested" stays a plain hollow cell: nothing has happened to it yet.


class MapFramePlayback(QWidget):
    """Compact map scrubber and playback over one T09 ledger (T21.3)."""

    frameChosen = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sharpmodMapFramePlayback")
        self.setAccessibleName("Map frame playback range")
        self._coverage = TimelineCoverage()
        self._active_hour = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["xxs"])
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["xxs"])
        self.previous_button = QToolButton(self)
        self.previous_button.setText("Previous")
        self.previous_button.setToolTip("Previous ready forecast hour")
        self.play_button = QToolButton(self)
        self.play_button.setText("Play")
        self.play_button.setCheckable(True)
        self.play_button.setToolTip("Play or pause ready map frames")
        self.next_button = QToolButton(self)
        self.next_button.setText("Next")
        self.next_button.setToolTip("Next ready forecast hour")
        self.range_label = QLabel(self)
        self.range_label.setWordWrap(True)
        outer.addWidget(self.range_label)
        row.addWidget(self.previous_button)
        row.addWidget(self.play_button)
        row.addWidget(self.next_button)
        row.addStretch(1)
        outer.addLayout(row)

        self.strip = FrameStrip(self)
        self.strip.set_state_words({
            "loaded": "ready",
            "loading": "loading",
            "requested": "loading",
            "failed": "failed",
            "canceled": "missing",
            "unavailable": "missing",
            "unknown": "missing",
        })
        self.strip.set_all_frames_clickable(True)
        outer.addWidget(self.strip)

        self.timer = QTimer(self)
        self.timer.setInterval(BASE_INTERVAL_MS)
        self.timer.timeout.connect(self._advance)
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.next_button.clicked.connect(lambda: self._step(1))
        self.play_button.toggled.connect(self._on_play_toggled)
        self.strip.frameChosen.connect(self._choose)
        self._sync_controls()

    def coverage(self):
        return self._coverage

    def set_coverage(self, coverage, *, active_hour=None):
        self._coverage = coverage if isinstance(
            coverage, TimelineCoverage) else TimelineCoverage()
        self._active_hour = None if active_hour is None else int(active_hour)
        self.strip.set_coverage(
            self._coverage, active_hour=self._active_hour,
            loop_range=self._range(),
        )
        self._sync_controls()

    def _ready_hours(self):
        return tuple(frame.forecast_hour for frame in self._coverage.frames
                     if frame.state == "loaded")

    def _range(self):
        frames = self._coverage.frames
        if not frames:
            return None
        return frames[0].forecast_hour, frames[-1].forecast_hour

    def _sync_controls(self):
        ready = self._ready_hours()
        frames = self._coverage.frames
        loading = len(self._coverage.hours_in("loading", "requested"))
        failed = len(self._coverage.hours_in("failed"))
        missing = len(frames) - len(ready) - loading - failed
        if frames:
            text = (
                f"F{frames[0].forecast_hour:03d}–F{frames[-1].forecast_hour:03d}"
                f" · {len(ready)} ready"
            )
            if loading:
                text += f" · {loading} loading"
            if missing:
                text += f" · {missing} missing"
            if failed:
                text += f" · {failed} failed"
        else:
            text = "No frames offered"
        self.range_label.setText(text)
        self.range_label.setToolTip(self.strip.toolTip())
        can_step = len(ready) > 1
        self.previous_button.setEnabled(can_step)
        self.next_button.setEnabled(can_step)
        self.play_button.setEnabled(can_step)
        if not can_step and self.play_button.isChecked():
            self.play_button.setChecked(False)
        self.setAccessibleDescription(
            text + ". Previous, Play, and Next visit only ready hours; the "
            "strip retains loading, missing, and failed hours."
        )

    def _choose(self, hour):
        self._active_hour = int(hour)
        self.strip.set_coverage(
            self._coverage, active_hour=self._active_hour,
            loop_range=self._range(),
        )
        self.frameChosen.emit(self._active_hour)

    def _step(self, direction):
        ready = self._ready_hours()
        if not ready:
            return
        if self._active_hour not in ready:
            target = ready[0] if direction >= 0 else ready[-1]
        else:
            index = ready.index(self._active_hour)
            target = ready[(index + int(direction)) % len(ready)]
        self._choose(target)

    def _advance(self):
        self._step(1)

    def _on_play_toggled(self, enabled):
        self.play_button.setText("Pause" if enabled else "Play")
        if enabled:
            self.timer.start()
        else:
            self.timer.stop()


class TimelinePlaybackBar(QToolBar):
    """Playback with a speed, a loop range, exact-time selection, and coverage."""

    def __init__(self, win, collection):
        super().__init__("Forecast Timeline", win)
        self.setObjectName("sharpmodForecastTimeline")
        self.setMovable(False)
        self.setFloatable(False)
        self._win_ref = weakref.ref(win)
        self._collection = collection
        self._coverage = TimelineCoverage.from_collection(collection)
        self._syncing = False

        self.previous_action = QAction("Previous", win)
        self.previous_action.setToolTip("Previous forecast hour")
        self.addAction(self.previous_action)

        self.play_action = QAction("Play", win)
        self.play_action.setCheckable(True)
        self.play_action.setToolTip("Play or pause the forecast timeline")
        self.addAction(self.play_action)

        self.next_action = QAction("Next", win)
        self.next_action.setToolTip("Next forecast hour")
        self.addAction(self.next_action)

        self.addSeparator()
        self.label = QLabel(win)
        self.label.setMinimumWidth(205)
        self.addWidget(self.label)

        self.slider = QSlider(Qt.Horizontal, win)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTracking(True)
        self.slider.setMinimumWidth(200)
        self.slider.setToolTip("Drag to a loaded forecast valid time")
        # Nothing to scrub with one loaded hour, but the bar still installs so the
        # coverage strip can say what happened to the rest of the request.
        self.slider.setEnabled(len(self.dates()) > 1)
        self.addWidget(self.slider)

        # The requested extent, beside the slider that only spans what loaded.
        self.strip = FrameStrip(win)
        self.addWidget(self.strip)

        self.addSeparator()
        # Exact valid times only, never an interpolated one: the same discipline
        # ReplayClock.select enforces for archived products.
        self.time_choice = QComboBox(win)
        self.time_choice.setObjectName("sharpmodTimelineValidTime")
        self.time_choice.setAccessibleName("Jump to a loaded valid time")
        self.time_choice.setToolTip(
            "Jump straight to one loaded valid time. Only hours that loaded are "
            "listed, so no time here is interpolated."
        )
        self.time_choice.setFont(mono_font("caption"))
        self.addWidget(self.time_choice)

        self.speed_choice = QComboBox(win)
        self.speed_choice.setObjectName("sharpmodTimelineSpeed")
        self.speed_choice.setAccessibleName("Playback speed")
        self.speed_choice.setToolTip("How fast playback advances")
        for multiplier, text in SPEEDS:
            self.speed_choice.addItem(text, float(multiplier))
        self.speed_choice.setCurrentIndex(self.speed_choice.findData(1.0))
        self.addWidget(self.speed_choice)

        self.range_start = QComboBox(win)
        self.range_end = QComboBox(win)
        for combo, name, tip in (
            (
                self.range_start,
                "Playback range start",
                "First forecast hour playback loops from",
            ),
            (
                self.range_end,
                "Playback range end",
                "Last forecast hour playback loops to",
            ),
        ):
            combo.setAccessibleName(name)
            combo.setToolTip(tip)
            combo.setFont(mono_font("caption"))
            self.addWidget(combo)

        # UTC stays the primary reading in the label; this adds the local
        # equivalent beside it with its zone, offset, and daylight-saving state.
        self.local_time_action = QAction("Local time", win)
        self.local_time_action.setCheckable(True)
        self.local_time_action.setToolTip(
            "Also show the local equivalent of the active valid time, with its "
            "zone, UTC offset, and whether daylight saving is in force. UTC stays "
            "primary."
        )
        self.addAction(self.local_time_action)

        self.loop_action = QAction("Loop", win)
        self.loop_action.setCheckable(True)
        self.loop_action.setChecked(True)
        self.loop_action.setToolTip("Loop to the range start after its end")
        self.addAction(self.loop_action)

        from qtpy.QtCore import QTimer

        self.timer = QTimer(win)
        self.timer.setInterval(interval_for_speed(1.0))

        self.previous_action.triggered.connect(lambda _checked=False: self.move(-1))
        self.next_action.triggered.connect(lambda _checked=False: self.move(1))
        self.slider.valueChanged.connect(self.set_index)
        self.timer.timeout.connect(lambda: self.move(1))
        self.play_action.toggled.connect(self._on_play_toggled)
        self.speed_choice.currentIndexChanged.connect(self._on_speed_changed)
        self.time_choice.currentIndexChanged.connect(self._on_time_chosen)
        self.range_start.currentIndexChanged.connect(self._on_range_changed)
        self.range_end.currentIndexChanged.connect(self._on_range_changed)
        self.strip.frameChosen.connect(self._on_frame_chosen)
        self.local_time_action.toggled.connect(
            lambda _checked=False: self._sync_controls(self.current_index())
        )
        win.destroyed.connect(lambda *_args: self.timer.stop())

    # -- state -------------------------------------------------------------- #

    def dates(self):
        return tuple(getattr(self._collection, "_dates", ()) or ())

    def coverage(self):
        return self._coverage

    def set_coverage(self, coverage):
        """Adopt a new ledger, e.g. as streamed hours resolve."""
        if isinstance(coverage, TimelineCoverage):
            self._coverage = coverage
            self.refresh()

    def hours(self):
        try:
            values = self._collection.getMeta("timeline_hours")
        except (AttributeError, KeyError, TypeError, ValueError):
            return ()
        return tuple(values) if isinstance(values, (list, tuple)) else ()

    def hour_at(self, index):
        hours = self.hours()
        if 0 <= index < len(hours):
            try:
                return int(hours[index])
            except (TypeError, ValueError):
                return None
        return None

    def current_index(self):
        dates = self.dates()
        try:
            return dates.index(self._collection.getCurrentDate())
        except (AttributeError, ValueError):
            return 0

    def speed(self):
        return float(self.speed_choice.currentData() or 1.0)

    def loop_range(self):
        """Return the inclusive ``(start, end)`` forecast hours playback covers."""
        hours = [hour for hour in (self.hour_at(i) for i in range(len(self.dates()))) if hour is not None]
        if not hours:
            return None
        start = self.range_start.currentData()
        end = self.range_end.currentData()
        start = min(hours) if start is None else int(start)
        end = max(hours) if end is None else int(end)
        if start > end:
            start, end = end, start
        return start, end

    def playable_indices(self):
        """Indices of loaded frames inside the loop range, in order.

        Playback walks loaded hours only -- there is nothing to display for the
        others -- but the strip keeps showing that they were asked for.
        """
        bounds = self.loop_range()
        indices = []
        for index in range(len(self.dates())):
            hour = self.hour_at(index)
            if bounds is None or hour is None or bounds[0] <= hour <= bounds[1]:
                indices.append(index)
        return tuple(indices) or tuple(range(len(self.dates())))

    # -- actions ------------------------------------------------------------ #

    def set_index(self, index):
        win = self._win_ref()
        if win is None:
            return
        dates = self.dates()
        if not dates:
            return
        self.slider.setMaximum(len(dates) - 1)
        index = max(0, min(len(dates) - 1, int(index)))
        self._collection.setCurrentDate(dates[index])
        # Keep other non-observed overlays on the same valid time, matching the
        # vendored left/right-arrow behaviour.
        for other in getattr(win.spc_widget, "prof_collections", ()):
            if other is self._collection:
                continue
            try:
                if not other.getMeta("observed"):
                    other.setCurrentDate(dates[index])
            except (AttributeError, KeyError):
                pass
        win.spc_widget.updateProfs()
        self._sync_controls(index)

    def move(self, delta):
        indices = self.playable_indices()
        if not indices:
            return
        current = self.current_index()
        if current in indices:
            position = indices.index(current) + int(delta)
        else:
            position = 0 if int(delta) >= 0 else len(indices) - 1
        if position >= len(indices):
            if self.loop_action.isChecked():
                position = 0
            else:
                self.play_action.setChecked(False)
                self.timer.stop()
                position = len(indices) - 1
        elif position < 0:
            position = len(indices) - 1 if self.loop_action.isChecked() else 0
        self.set_index(indices[position])

    def refresh(self):
        """Re-read the collection after a streamed hour landed."""
        dates = self.dates()
        self.slider.setMaximum(max(0, len(dates) - 1))
        self.slider.setEnabled(len(dates) > 1)
        self.set_index(self.current_index())

    # -- synchronisation ---------------------------------------------------- #

    def _sync_controls(self, index):
        if self._syncing:
            return
        self._syncing = True
        try:
            dates = self.dates()
            self.slider.blockSignals(True)
            self.slider.setValue(index)
            self.slider.blockSignals(False)
            self._rebuild_time_choice(index)
            self._rebuild_range_choices()
            hour = self.hour_at(index)
            self.strip.set_coverage(
                self._coverage, active_hour=hour, loop_range=self.loop_range()
            )
            prefix = "" if hour is None else f"F{hour:03d}  \u2022  "
            valid = dates[index] if dates else None
            # UTC first, always. The local reading is appended only when asked
            # for, and only when it can be trusted.
            text = f"{prefix}{local_time.format_utc(valid)}" if dates else ""
            detail = text
            if dates and self.local_time_action.isChecked():
                reading = local_time.local_reading(valid)
                if reading is not None:
                    text += f"  \u00b7  {reading.text}"
                    if reading.abbreviation:
                        text += f" {reading.abbreviation}"
                    detail = f"{prefix}{local_time.format_utc(valid)}\n{reading.describe()}"
            self.label.setText(text)
            self.label.setAccessibleName(f"Active forecast frame: {detail}")
            self.label.setToolTip(
                f"{detail}\n{self._coverage.summary()}"
                if text
                else self._coverage.summary()
            )
            self.slider.setToolTip(
                "Drag to a loaded forecast valid time. " + self._coverage.summary()
            )
        finally:
            self._syncing = False

    def _rebuild_time_choice(self, index):
        dates = self.dates()
        blocked = self.time_choice.blockSignals(True)
        try:
            self.time_choice.clear()
            for position, date in enumerate(dates):
                hour = self.hour_at(position)
                prefix = "" if hour is None else f"F{hour:03d} "
                self.time_choice.addItem(f"{prefix}{date:%d %H:%MZ}", position)
            if 0 <= index < self.time_choice.count():
                self.time_choice.setCurrentIndex(index)
        finally:
            self.time_choice.blockSignals(blocked)

    def _rebuild_range_choices(self):
        hours = [
            hour
            for hour in (self.hour_at(i) for i in range(len(self.dates())))
            if hour is not None
        ]
        if not hours:
            return
        for combo, fallback in ((self.range_start, min(hours)), (self.range_end, max(hours))):
            wanted = combo.currentData()
            blocked = combo.blockSignals(True)
            try:
                combo.clear()
                for hour in hours:
                    combo.addItem(f"F{hour:03d}", hour)
                position = combo.findData(wanted if wanted is not None else fallback)
                combo.setCurrentIndex(position if position >= 0 else combo.findData(fallback))
            finally:
                combo.blockSignals(blocked)

    # -- slots -------------------------------------------------------------- #

    def _on_play_toggled(self, enabled):
        self.play_action.setText("Pause" if enabled else "Play")
        if enabled:
            self.timer.setInterval(interval_for_speed(self.speed()))
            self.timer.start()
        else:
            self.timer.stop()

    def _on_speed_changed(self, *_args):
        interval = interval_for_speed(self.speed())
        self.timer.setInterval(interval)
        if self.play_action.isChecked():
            # Restart so the new rate takes effect now rather than after the
            # remainder of the old interval.
            self.timer.start()

    def _on_time_chosen(self, position):
        if self._syncing:
            return
        index = self.time_choice.itemData(position)
        if index is not None:
            self.set_index(int(index))

    def _on_range_changed(self, *_args):
        if self._syncing:
            return
        self.strip.set_coverage(
            self._coverage,
            active_hour=self.hour_at(self.current_index()),
            loop_range=self.loop_range(),
        )

    def _on_frame_chosen(self, hour):
        """Open a chosen hour, or explain why it cannot be opened."""
        frame = self._coverage.frame_for(hour)
        if frame is None:
            return
        if frame.available and isinstance(frame.valid_time, datetime):
            dates = self.dates()
            if frame.valid_time in dates:
                self.set_index(dates.index(frame.valid_time))
                return
        self.label.setText(frame.describe())
        self.label.setToolTip(frame.describe())


__all__ = [
    "BASE_INTERVAL_MS",
    "FrameStrip",
    "MapFramePlayback",
    "SPEEDS",
    "TimelinePlaybackBar",
    "interval_for_speed",
]

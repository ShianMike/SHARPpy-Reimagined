"""Chart view for comparing time-aligned sounding collections.

It owns the chart widget and visual interactions; analysis comparison helpers provide
aligned data, and the containing workspace coordinates selection and export."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QHBoxLayout
from qtpy.QtWidgets import QLabel
from qtpy.QtWidgets import QSizePolicy
from qtpy.QtWidgets import QVBoxLayout
from qtpy.QtWidgets import QWidget
from sharpmod.ui.features.gui_theme import current_theme
from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.ui.features.gui_theme import ui_font
from sharpmod.ui.styles.theme import OBJ_HINT
from sharpmod.ui.styles.theme import SPACE
import math
from sharpmod.ui.features.gui_comparison_charts import (
    ARRANGEMENT_SIDE_BY_SIDE,
    CHART_HODOGRAPH,
    CHART_SKEWT,
    RENDERED_MODES,
    _CHART_MINIMUM,
    _CursorOverlay,
    _ElidedLabel,
    _LegendStrip,
    _MODE_NAMES,
    _Swatch,
    _colour_text,
    _renderer_classes,
    _sharptab,
    overlay_palette,
    slot_colour
)


class ComparisonChartView(QWidget):
    """One comparison slot drawn by the established scientific renderer."""

    def __init__(self, mode=CHART_SKEWT, parent=None):
        super().__init__(parent)
        self._mode = mode if mode in RENDERED_MODES else CHART_SKEWT
        self._renderer = None
        self._overlay = None
        self._panel = None
        self._sources = ()
        self._active_index = 0
        self._unavailable = ""
        self._cursor_height = None
        self.setMinimumSize(*_CHART_MINIMUM)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(SPACE["xs"])
        self.swatch = _Swatch(slot_colour(1), self)
        self.title = _ElidedLabel(self)
        self.title.setObjectName("analysisChartSlotTitle")
        font = ui_font("small")
        font.setBold(True)
        self.title.setFont(font)
        header.addWidget(self.swatch)
        header.addWidget(self.title, 1)
        outer.addLayout(header)

        self.message = QLabel("", self)
        self.message.setObjectName(OBJ_HINT)
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setVisible(False)
        outer.addWidget(self.message, 1)

        self._host = QWidget(self)
        self._host.setObjectName("analysisChartHost")
        # The chart is the subject of this view, so it gets a floor of its own.
        # Without one, the heading, legend, and message size hints won at 200%
        # text and squeezed the Skew-T down to an unreadable strip.
        self._host.setMinimumHeight(150)
        host_layout = QVBoxLayout(self._host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        self._host_layout = host_layout
        outer.addWidget(self._host, 1)

        self.legend = _LegendStrip(self)
        self.legend.setObjectName(OBJ_HINT)
        self.legend.setFont(mono_font("small"))
        outer.addWidget(self.legend)

    # -- construction of the vendored renderer ----------------------------- #

    def mode(self):
        return self._mode

    def set_mode(self, mode):
        """Swap the renderer class, rebuilding it because the widget differs."""
        if mode == self._mode:
            return
        self._mode = mode if mode in RENDERED_MODES else CHART_SKEWT
        self._discard_renderer()
        self._apply()

    def renderer(self):
        """Return the live vendored widget, or ``None`` when unavailable."""
        return self._renderer

    def unavailable_reason(self):
        """Return why no chart is drawn, or an empty string when one is."""
        return self._unavailable

    def _discard_renderer(self):
        if self._overlay is not None:
            self._overlay.setParent(None)
            self._overlay.deleteLater()
            self._overlay = None
        if self._renderer is not None:
            self._host_layout.removeWidget(self._renderer)
            self._renderer.setParent(None)
            self._renderer.deleteLater()
            self._renderer = None

    def _ensure_renderer(self):
        if self._renderer is not None:
            return self._renderer
        classes, error = _renderer_classes()
        if classes is None:
            self._unavailable = (
                f"The scientific renderers are unavailable: {error}"
            )
            return None
        factory = classes[self._mode]
        try:
            renderer = (
                factory(dgz=False, pbl=False)
                if self._mode == CHART_SKEWT
                else factory()
            )
        except Exception as exc:  # noqa: BLE001 - reported in the slot
            self._unavailable = f"The chart could not be created: {exc}"
            return None
        renderer.setParent(self._host)
        # Read-only: the comparison view never edits, so the drag handles that
        # resizeEvent would leave stale are never exposed.
        renderer.setEnabled(False)
        if self._mode == CHART_SKEWT:
            # Silence the renderer's own stacked titles on this instance only.
            #
            # They were written for one large chart. In a comparison slot they
            # overlapped each other and ran across the plot, and they name only
            # the Skew-T's collections -- the hodograph has no titles at all, so
            # relying on them would mean the two modes identified profiles
            # differently. The slot heading and the legend below carry the same
            # information for both modes and can elide. Bound to the instance,
            # never the class, so the main sounding window keeps its titles.
            renderer.drawTitles = lambda _qp: None
        renderer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._host_layout.addWidget(renderer)
        self._renderer = renderer
        self._overlay = _CursorOverlay(self._host)
        self._overlay.setGeometry(self._host.rect())
        self._overlay.raise_()
        return renderer

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._overlay is not None:
            self._overlay.setGeometry(self._host.rect())
            self._overlay.raise_()
        self._refresh_cursor()

    # -- data assignment ---------------------------------------------------- #

    def set_panel(self, panel, sources, active_index, colours, *, arrangement=None):
        """Assign this slot's profile plus any companions drawn behind it.

        ``sources`` is ordered, and the order matters: it becomes the renderer's
        collection list, and the renderer walks that list positionally to pick
        background colours and to stack titles.
        """
        self._panel = panel
        self._sources = tuple(sources)
        self._active_index = int(active_index)
        self._colours = tuple(colours)
        self._arrangement = arrangement or ARRANGEMENT_SIDE_BY_SIDE
        self._apply()

    def _apply(self):
        panel = self._panel
        theme = current_theme()
        self._unavailable = ""
        if panel is None:
            self.title.set_full_text("Slot: not assigned")
            self.swatch.set_colour(theme.border)
            self._show_message("No profile is assigned to this slot.")
            self.legend.set_entries(())
            return

        colour = slot_colour(getattr(panel, "slot_number", 1))
        self.swatch.set_colour(colour)
        title = f"Slot {getattr(panel, 'slot_number', '?')}: {getattr(panel, 'label', '')}"
        if getattr(panel, "reference", False):
            title += " · reference"
        if getattr(panel, "modified", False):
            title += " · modified"
        self.title.set_full_text(title)

        if not self._sources:
            # Nothing is drawn here, so do not give the slot a curve colour: a
            # coloured chip beside an empty panel reads as "this profile is here".
            self.swatch.set_colour(theme.border)
            # The panel already knows why -- an unassigned slot, a profile that is
            # no longer loaded, or no profile at the exact reference valid time.
            # Repeat its words so both views explain a gap identically.
            self._show_message(
                str(getattr(panel, "unavailable_reason", "") or "No profile available.")
            )
            self.legend.set_entries(())
            return

        renderer = self._ensure_renderer()
        if renderer is None:
            self._show_message(self._unavailable)
            self.legend.set_entries(())
            return

        try:
            self._load(renderer)
        except Exception as exc:  # noqa: BLE001 - a partial profile must not crash
            # A history-restored or wind-free profile lacks the derived fields the
            # hodograph needs. Say which chart cannot be drawn and why, rather
            # than tearing down the workspace.
            self._unavailable = (
                f"{_MODE_NAMES.get(self._mode, 'This chart')} cannot be drawn for "
                f"this profile: {exc}"
            )
            self._discard_renderer()
            self._show_message(self._unavailable)
            self.legend.set_entries(())
            return

        self._unavailable = ""
        self.message.setVisible(False)
        self._host.setVisible(True)
        self.setMinimumSize(*_CHART_MINIMUM)
        self.legend.set_entries(self._legend_entries())
        self._refresh_cursor()

    def has_chart(self):
        """Whether this slot is currently drawing a chart."""
        return self._renderer is not None and not self._unavailable

    def _load(self, renderer):
        """Point the renderer at this slot's collections, then draw once."""
        for existing in list(getattr(renderer, "prof_collections", ())):
            renderer.rmProfileCollection(existing)
        for source in self._sources:
            renderer.addProfileCollection(source)
        palette = overlay_palette(self._sources, self._active_index, self._colours)
        if palette:
            renderer.background_colors = [QColor(value) for value in palette]
        # update_gui=False so the omega guard below lands before the first paint.
        renderer.setActiveCollection(self._active_index, update_gui=False)
        # Omega needs a field a comparison profile is not guaranteed to carry, and
        # the vendored plotData calls it without a guard.
        renderer.plot_omega = False
        if self._mode == CHART_HODOGRAPH:
            # The vendored hodograph already owns a height readout designed to be
            # driven from the Skew-T's cursor; reuse it rather than inventing one.
            renderer.cursorToggle(True)
        renderer.clearData()
        renderer.plotData()
        renderer.update()

    def _show_message(self, text):
        """Show why this slot has no chart, and report the same text to callers.

        ``unavailable_reason`` used to stay empty when the *panel* was the thing
        that was unavailable, so a slot could show an explanation on screen while
        telling a caller, and an accessibility check, that all was well.
        """
        text = str(text or "")
        self._unavailable = text
        self.message.setText(text)
        self.message.setVisible(True)
        self._host.setVisible(False)
        # A slot showing one sentence must not reserve a chart's worth of height,
        # or a two-sounding comparison exports with half the image left blank.
        self.setMinimumSize(200, 56)

    def _legend_entries(self):
        """Name every curve and state the colour it was actually drawn in.

        The chips have to match the ink, which rules out using the slot colour for
        everything. The renderer draws its *active* profile with its own
        temperature and dewpoint colours, and colours a hodograph by height band;
        only the companion profiles are drawn in the palette this module supplies.
        A single slot-coloured chip beside the active curve would therefore name a
        colour that is nowhere in the chart.
        """
        if not self._sources:
            return ()
        renderer = self._renderer
        entries = []
        active = self._sources[self._active_index]
        if self._mode == CHART_SKEWT:
            entries.append(
                (
                    (
                        _colour_text(getattr(renderer, "temp_color", None), "red"),
                        _colour_text(getattr(renderer, "dewp_color", None), "green"),
                    ),
                    f"{active.label} (this slot: T, Td)",
                )
            )
        else:
            entries.append(((), f"{active.label} (this slot: coloured by height)"))
        palette = overlay_palette(self._sources, self._active_index, self._colours)
        others = [
            source
            for index, source in enumerate(self._sources)
            if index != self._active_index
        ]
        for source, colour in zip(others, palette):
            entries.append(((_colour_text(colour, "neutral"),), source.label))
        return tuple(entries)

    # -- shared cursor and readout ------------------------------------------ #

    def set_cursor_height(self, msl_m):
        """Place the shared cursor at one height above mean sea level."""
        self._cursor_height = None if msl_m is None else float(msl_m)
        self._refresh_cursor()

    def _refresh_cursor(self):
        if self._overlay is None or self._renderer is None:
            return
        if self._cursor_height is None or not self._sources:
            self._overlay.set_line(None)
            if self._mode == CHART_HODOGRAPH:
                self._drive_hodograph_readout(None)
            return
        profile = self._sources[self._active_index].profile
        if self._mode == CHART_HODOGRAPH:
            self._overlay.set_line(None)
            self._drive_hodograph_readout(profile)
            return
        y = self._skewt_y(profile, self._cursor_height)
        caption = f"{self._cursor_height / 1000.0:.1f} km MSL"
        pressure = self._pressure_at(profile, self._cursor_height)
        if pressure is not None:
            caption += f" · {pressure:.0f} hPa"
        self._overlay.set_line(y, caption if y is not None else "")

    def _pressure_at(self, profile, msl_m):
        tab = _sharptab()
        if tab is None:
            return None
        try:
            pressure = float(tab.interp.pres(profile, float(msl_m)))
        except Exception:  # noqa: BLE001 - readout is optional detail
            return None
        return pressure if math.isfinite(pressure) else None

    def _skewt_y(self, profile, msl_m):
        """Map a height onto the renderer's own pressure axis."""
        renderer = self._renderer
        pressure = self._pressure_at(profile, msl_m)
        if renderer is None or pressure is None:
            return None
        try:
            y = float(
                renderer.originy + renderer.pres_to_pix(pressure) / renderer.scale
            )
        except Exception:  # noqa: BLE001 - geometry differs before first layout
            return None
        if not math.isfinite(y) or not 0.0 <= y <= float(self._host.height()):
            return None
        return y

    def _drive_hodograph_readout(self, profile):
        """Feed the vendored hodograph readout in the units it expects (AGL)."""
        renderer = self._renderer
        if renderer is None:
            return
        if profile is None or self._cursor_height is None:
            try:
                renderer.cursorMove(-999.0)
            except Exception:  # noqa: BLE001 - readout is optional detail
                pass
            return
        tab = _sharptab()
        try:
            if tab is not None:
                agl = float(tab.interp.to_agl(profile, float(self._cursor_height)))
            else:
                agl = float(self._cursor_height)
            renderer.cursorMove(agl)
        except Exception:  # noqa: BLE001 - readout is optional detail
            pass

    # -- linked versus independent axes ------------------------------------- #

    def axis_state(self):
        """Return the renderer's own pan/zoom state, or ``None``."""
        renderer = self._renderer
        if renderer is None or self._mode != CHART_SKEWT:
            return None
        try:
            return (
                float(renderer.originx),
                float(renderer.originy),
                float(renderer.scale),
            )
        except (AttributeError, TypeError, ValueError):
            return None

    def apply_axis_state(self, state):
        """Adopt another slot's pan/zoom so the two are read on one scale."""
        renderer = self._renderer
        if renderer is None or state is None or self._mode != CHART_SKEWT:
            return False
        try:
            originx, originy, scale = (float(value) for value in state)
        except (TypeError, ValueError):
            return False
        if scale <= 0.0:
            return False
        try:
            renderer.originx = originx
            renderer.originy = originy
            renderer.scale = scale
            renderer.plotBackground()
            renderer.clearData()
            renderer.plotData()
            renderer.update()
        except Exception:  # noqa: BLE001 - a failed sync must not break the slot
            return False
        self._refresh_cursor()
        return True

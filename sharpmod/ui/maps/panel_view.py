"""Synchronized HRRR map panel view and shared-map facade."""

from __future__ import annotations

import logging

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.gui_maps import PointMapWidget
from sharpmod.ui.maps.overlays.controllers import HrrrFieldController
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    OBJ_CARD,
    OBJ_HINT,
    OBJ_PLAIN,
    SPACE,
)

_LOGGER = logging.getLogger("sharpmod.gui")

from sharpmod.ui.maps.panels import (
    MAX_PANELS, PANEL_COUNTS, DEFAULT_PANEL_PRODUCTS,
    _MapPanel, migrate_panel_preset_choice,
    normalise_panel_category_memory, preset_panel_products,
    resolve_panel_products,
)


class _SharedMapFacade:
    """Present every panel map to one overlay controller as a single map.

    Each controller in :mod:`sharpmod.ui.maps.overlays.controllers` talks to its map
    through the same small vocabulary -- attach a layer under a key, drop it,
    toggle it, ask what the view is -- and none of them connect to a map signal;
    the picker relays in that direction. So one controller can drive the whole
    grid: writes reach every panel, reads are answered by the first.

    Sharing one controller is the requirement, not an optimisation. These panels
    differ by *field* and by nothing else, so an outlook boundary or a radar sweep
    present on one panel and absent from another would be a difference the
    comparison cannot account for. One controller also means one fetch, where a
    controller per panel would ask the same server for the same image two to four
    times and then have to reconcile four arrival times.

    Every method is written out rather than forwarded through ``__getattr__``.
    A catch-all would answer a *write* the controllers gain later from panel 0
    alone, leaving three panels quietly missing an overlay; an ``AttributeError``
    is the better failure.
    """

    def __init__(self, maps) -> None:
        self._maps = tuple(maps)

    # -- reads: the panels share one view, so the first one answers ---------- #
    def view_bounds(self):
        return self._maps[0].view_bounds()

    def overlay(self, key: str):
        return self._maps[0].overlay(key)

    def overlay_keys(self) -> tuple[str, ...]:
        return self._maps[0].overlay_keys()

    def is_overlay_visible(self, key: str) -> bool:
        return self._maps[0].is_overlay_visible(key)

    def context_point(self):
        return self._maps[0].context_point()

    def width(self) -> int:
        return self._maps[0].width()

    def height(self) -> int:
        return self._maps[0].height()

    # -- writes: every panel, or the comparison stops being one -------------- #
    def set_overlay(self, key: str, layer, *, visible: bool | None = None) -> None:
        for widget in self._maps:
            widget.set_overlay(key, layer, visible=visible)

    def remove_overlay(self, key: str) -> None:
        for widget in self._maps:
            widget.remove_overlay(key)

    def set_overlay_visible(self, key: str, visible: bool) -> None:
        for widget in self._maps:
            widget.set_overlay_visible(key, visible)

    def set_valid_time(self, when) -> None:
        for widget in self._maps:
            widget.set_valid_time(when)

    def set_radar_sites(self, sites) -> None:
        for widget in self._maps:
            widget.set_radar_sites(sites)

    def set_radar_site_selected(self, site_id) -> None:
        for widget in self._maps:
            widget.set_radar_site_selected(site_id)

    def set_loaded_profile_points(self, points) -> None:
        for widget in self._maps:
            widget.set_loaded_profile_points(points)

    # -- legend choices: every panel, or the comparison stops being one ------- #
    def legend_state(self):
        return self._maps[0].legend_state()

    def restore_legend_state(self, payload) -> None:
        for widget in self._maps:
            widget.restore_legend_state(payload)

    def set_legend_corner(self, corner: str = "") -> None:
        for widget in self._maps:
            widget.set_legend_corner(corner)

    def set_legend_collapsed(self, collapsed: bool) -> None:
        for widget in self._maps:
            widget.set_legend_collapsed(collapsed)

    def set_scale_locked(self, product: str, locked: bool = True) -> None:
        for widget in self._maps:
            widget.set_scale_locked(product, locked)

    def is_scale_locked(self, product: str = "") -> bool:
        return self._maps[0].is_scale_locked(product)

    # -- time header, forecast reference, live/history mode (T21) ------------- #
    def time_header_text(self):
        return self._maps[0].time_header_text()

    def time_match_states(self):
        return self._maps[0].time_match_states()

    def time_state(self):
        return self._maps[0].time_state()

    def restore_time_state(self, payload) -> None:
        for widget in self._maps:
            widget.restore_time_state(payload)

    def set_forecast_reference(self, run, fxx) -> None:
        for widget in self._maps:
            widget.set_forecast_reference(run, fxx)

    def set_map_mode(self, mode) -> None:
        for widget in self._maps:
            widget.set_map_mode(mode)

    def map_mode(self) -> str:
        return self._maps[0].map_mode()



class MapPanelsView(QWidget):
    """Two or four HRRR field panels sharing one synchronized view."""

    #: Emitted when the user changes how many panels are shown, so a host can
    #: persist it the way the analysis workspace persists its own layout count.
    layoutChanged = Signal(int)

    #: Emitted when the user changes a panel's field, carrying the panel position
    #: and the resolved key. Paired with :attr:`layoutChanged`, these are the two
    #: moments a remembered arrangement goes stale.
    productChanged = Signal(int, str)

    #: Relayed from whichever panel was clicked. The host decides what a chosen
    #: point means; this view only reports it.
    pointSelected = Signal(float, float)
    pointActivated = Signal(float, float)

    #: Emitted once the whole grid has settled on one view, for a host to nudge
    #: the view-dependent overlays it owns. Each panel's *field* is retried from
    #: inside this view; the shared overlays belong to the host, which is the
    #: arrangement every other tab already uses -- a map never reaches into a
    #: controller and a controller never listens to a map.
    viewSettled = Signal()

    #: Emitted with a radar antenna clicked on any panel, for the host to point
    #: its shared radar controller at.
    radarSiteSelected = Signal(str)

    #: Emitted with the hovered geographic location, for assistive text and
    #: hosts that mirror it outside this view. Carries ``(lat, lon)``.
    crosshairMoved = Signal(float, float)

    #: Emitted when every panel drops the shared crosshair (mouse left a
    #: panel), so outside mirrors can clear too.
    crosshairCleared = Signal()

    def __init__(self, *, parent=None, panel_count=None, products=None):
        super().__init__(parent)
        self.setObjectName(OBJ_PLAIN)
        self._syncing = False
        self._placing_point = False
        self._running = False
        self._panel_count = PANEL_COUNTS[0]

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(SPACE["xs"])

        self._last_clicked = 0
        self._panels: list[_MapPanel] = []
        self._crosshair: tuple[float, float] | None = None
        self._maximised: int | None = None
        # Restored fields are handed to the panel at construction rather than set
        # afterwards, so a controller is built already pointing at the right
        # product instead of fetching the default and then being redirected.
        startup = resolve_panel_products(products)
        for index in range(MAX_PANELS):
            panel = _MapPanel(index, startup[index], parent=self)
            panel.viewSettled.connect(self._on_panel_view_settled)
            panel.productChanged.connect(self.productChanged)
            panel.pointSelected.connect(self._on_panel_point)
            panel.pointActivated.connect(self.pointActivated)
            panel.map.radarSiteSelected.connect(self.radarSiteSelected)
            panel.map.crosshairHover.connect(self._on_panel_crosshair)
            panel.map.installEventFilter(self)
            # Two columns, one row for two panels and two for four -- the same
            # arithmetic the sounding comparison uses, so both views agree about
            # what "4 panels" looks like.
            self._grid.addWidget(panel, index // 2, index % 2)
            self._panels.append(panel)

        # Built over every panel, including hidden ones, so a shared overlay is
        # already attached when a two-panel layout grows to four rather than
        # arriving one fetch later than the fields beside it.
        self._shared = _SharedMapFacade(tuple(panel.map for panel in self._panels))

        self.set_panel_count(panel_count)
        # One view for all of them from the outset, so the first pan is a
        # refinement rather than the moment they first agree.
        self._sync_from(self._panels[0])

    # -- public API ---------------------------------------------------------- #
    def panel_count(self) -> int:
        return int(self._panel_count)

    def set_panel_count(self, count: int) -> None:
        """Show ``count`` panels, clamping anything unrecognised to the smallest."""
        try:
            wanted = int(count)
        except (TypeError, ValueError):
            wanted = PANEL_COUNTS[0]
        if wanted not in PANEL_COUNTS:
            wanted = PANEL_COUNTS[0]
        previous = int(getattr(self, "_panel_count", PANEL_COUNTS[0]))
        self._panel_count = wanted
        # Guarded, because showing a widget resizes it and a resize is an outright
        # extent set -- so a panel that has been hidden at some stale view
        # announces a settled view the instant it appears. Ungated, that stale
        # panel becomes the sync source and overwrites the view every visible
        # panel was already sharing, which showed up as switching to four panels
        # silently zooming all of them back out to the whole country.
        self._syncing = True
        try:
            for position, panel in enumerate(self._panels):
                panel.set_active(position < wanted)
        finally:
            self._syncing = False
        self._maximised = None
        for panel in self._panels:
            try:
                panel.set_maximised_selected(False)
            except (AttributeError, RuntimeError):
                continue
        # Panel 0 is always visible, so it is always the one holding the view the
        # others should adopt.
        self._sync_from(self._panels[0])
        if wanted != previous:
            try:
                self.layoutChanged.emit(int(wanted))
            except RuntimeError:  # pragma: no cover - teardown
                pass

    def set_forecast_reference(self, run, fxx) -> None:
        """Point every panel at one run and forecast hour."""
        for panel in self._panels:
            try:
                panel.set_forecast_reference(run, fxx)
            except Exception:  # noqa: BLE001 - one panel must not stop the rest
                _LOGGER.warning("map_panels.reference_failed", exc_info=True)
        shared = getattr(self, "_shared", None)
        if shared is not None:
            try:
                shared.set_forecast_reference(run, fxx)
            except (AttributeError, RuntimeError):
                pass

    # -- time header and live/history mode (T21): the shared facade --------- #
    def time_header_text(self):
        return self._shared.time_header_text()

    def time_match_states(self):
        return self._shared.time_match_states()

    def time_state(self):
        return self._shared.time_state()

    def restore_time_state(self, payload) -> None:
        self._shared.restore_time_state(payload)

    def set_map_mode(self, mode) -> None:
        self._shared.set_map_mode(mode)

    def map_mode(self) -> str:
        return self._shared.map_mode()

    def panel_products(self) -> tuple[str, ...]:
        """Return every panel's field, hidden ones included.

        All :data:`MAX_PANELS` of them, not just the visible ones, so the value
        round-trips through :meth:`set_panel_products` unchanged. Reporting only
        the visible panels would mean dropping to two panels and back to four
        discarded the fields chosen for the third and fourth -- the arrangement
        would quietly reset on a layout change that was meant to be reversible.
        """
        return tuple(panel.product() for panel in self._panels)

    def visible_panel_products(self) -> tuple[str, ...]:
        """Return the fields currently on show."""
        return tuple(panel.product() for panel in self._panels[: self._panel_count])

    def set_panel_products(self, products) -> None:
        """Point each panel at a field, positionally, ignoring any extras."""
        for panel, key in zip(self._panels, tuple(products)):
            if key:
                panel.set_product(str(key))

    def swap_panels(self, first: int, second: int) -> bool:
        """Swap two panels' fields, keeping slots stable (T24.2).

        Slots never repack: the two configured positions exchange products,
        hidden panels included, and unavailable slots keep their meaning
        rather than letting a later product shift into them.
        """
        try:
            first_index, second_index = int(first), int(second)
        except (TypeError, ValueError, OverflowError):
            return False
        if first_index == second_index:
            return False
        if not (0 <= first_index < MAX_PANELS and 0 <= second_index < MAX_PANELS):
            return False
        first_key = self._panels[first_index].product()
        second_key = self._panels[second_index].product()
        if first_key == second_key:
            return False
        self._panels[first_index].set_product(second_key)
        self._panels[second_index].set_product(first_key)
        return True

    def move_panel_product(self, source: int, destination: int) -> bool:
        """Move a field from one slot to another, shifting between (T24.2)."""
        try:
            source_index, destination_index = int(source), int(destination)
        except (TypeError, ValueError, OverflowError):
            return False
        if source_index == destination_index:
            return False
        if not (0 <= source_index < MAX_PANELS
                and 0 <= destination_index < MAX_PANELS):
            return False
        products = list(self.panel_products())
        key = products.pop(source_index)
        products.insert(destination_index, key)
        for panel, product in zip(self._panels, products):
            panel.set_product(str(product))
        return True

    def category_memory(self) -> dict:
        """Return the merged ``{category: product}`` memory (T24.2)."""
        merged: dict[str, str] = {}
        for panel in self._panels:
            try:
                memory = panel.category_memory()
            except (AttributeError, RuntimeError):
                continue
            if isinstance(memory, dict):
                merged.update({str(key): str(value)
                               for key, value in memory.items()})
        return normalise_panel_category_memory(merged)

    def set_category_memory(self, memory) -> None:
        """Replace every panel's remembered per-category products (T24.2)."""
        cleaned = normalise_panel_category_memory(memory)
        for panel in self._panels:
            try:
                panel.set_category_memory(cleaned)
            except (AttributeError, RuntimeError):
                continue

    def apply_preset(self, key: str) -> str:
        """Apply a named four-field combination, returning its key (T24.4)."""
        resolved = migrate_panel_preset_choice(key)
        self.set_panel_products(preset_panel_products(resolved))
        return resolved

    def set_area(self, name: str) -> None:
        """Put every panel on a named region.

        Goes through each map's ``set_area`` rather than the sync path on purpose.
        Syncing uses ``restore_view_bounds``, which clears the map's remembered
        area name -- so a followed map would lose ``reset_view`` and the region
        control would stop meaning anything after the first pan.
        """
        self._syncing = True
        try:
            for panel in self._panels:
                panel.map.set_area(str(name))
        finally:
            self._syncing = False
        self._announce_settled()

    def set_point(self, lat: float, lon: float, *, center: bool = False) -> None:
        """Mark one location on every panel.

        The same marker in all of them, because the point is what a sounding
        would be taken at and each panel is a different field *at that point*.
        Guarded: ``set_point`` makes the map report a selected point, which would
        otherwise come straight back in as a fresh user choice.
        """
        if self._placing_point:
            return
        self._placing_point = True
        try:
            for panel in self._panels:
                try:
                    panel.map.set_point(float(lat), float(lon), center=bool(center))
                except (AttributeError, RuntimeError, ValueError):
                    _LOGGER.debug("map_panels.point_rejected", exc_info=True)
        finally:
            self._placing_point = False

    def set_domain(self, bounds, label: str = "", outline=None) -> None:
        """Outline the model's coverage on every panel."""
        for panel in self._panels:
            try:
                panel.map.set_domain(bounds, label, outline=outline)
            except (AttributeError, RuntimeError, TypeError):
                _LOGGER.debug("map_panels.domain_rejected", exc_info=True)

    def maps(self) -> tuple:
        """Return every panel's map, for a host applying a global preference."""
        return tuple(panel.map for panel in self._panels)

    def shared_map(self):
        """Return a stand-in map that drives every panel at once.

        Hand this to an overlay controller and its layer appears on all of them
        from one fetch. Not for the model field: each panel picks its own, and all
        23 products share a single overlay key, so one controller across the grid
        is exactly the collision the per-panel controllers exist to avoid.
        """
        return self._shared

    def active_field_controller(self):
        """Return the field controller of the panel last clicked in.

        Which panel that is decides what travels onto a sounding taken here: ask
        for a sounding after clicking the panel showing significant tornado
        parameter and it is that field the sounding's locator inset should carry,
        not whichever panel happens to be first.
        """
        index = self._last_clicked
        if not 0 <= index < len(self._panels):
            index = 0
        panel = self._panels[index]
        if not panel.is_active():
            panel = self._panels[0]
        return panel.field

    # -- maximise / restore (T24.3) ------------------------------------------ #
    def maximised_panel(self) -> int | None:
        """Return the maximised panel index, or ``None``."""
        maximised = getattr(self, "_maximised", None)
        try:
            index = None if maximised is None else int(maximised)
        except (TypeError, ValueError, OverflowError):
            return None
        if index is None or not 0 <= index < len(self._panels):
            return None
        if not self._panels[index].is_active():
            return None
        return index

    def set_maximised_panel(self, index) -> bool:
        """Maximise one visible panel, hiding the others (T24.3).

        Shared extent, products, locks, and the shared crosshair survive:
        hidden panels stop fetching through the existing active gate but keep
        every configured choice, so restore returns the same comparison.
        Returns whether the arrangement changed.
        """
        try:
            wanted = None if index is None else int(index)
        except (TypeError, ValueError, OverflowError):
            return False
        if wanted is not None and not 0 <= wanted < len(self._panels):
            return False
        if wanted is not None and not self._panels[wanted].is_active():
            return False
        current = self.maximised_panel()
        if wanted == current:
            return False
        self._syncing = True
        try:
            for position, panel in enumerate(self._panels):
                if wanted is None:
                    panel.setVisible(panel.is_active())
                else:
                    panel.setVisible(position == wanted)
                panel._sync_enabled()
        finally:
            self._syncing = False
            self._maximised = wanted
        for position, panel in enumerate(self._panels):
            try:
                panel.set_maximised_selected(
                    wanted is not None and position == wanted)
            except (AttributeError, RuntimeError):
                continue
        if wanted is not None:
            self._last_clicked = int(wanted)
        # The maximised panel alone holds the shared view now; re-sync from
        # it so restore (or a second maximise) starts from the same extent.
        try:
            anchor = self._panels[wanted] if wanted is not None \
                else self._panels[0]
            self._sync_from(anchor)
        except (AttributeError, RuntimeError, IndexError):
            pass
        try:
            self.layoutChanged.emit(int(self._panel_count))
        except RuntimeError:  # pragma: no cover - teardown
            pass
        return True

    def restore_maximised(self) -> bool:
        """Restore the full grid after a maximise (T24.3)."""
        return self.set_maximised_panel(None)

    def set_map_tool(self, tool) -> None:
        """Apply one explicit tool to every panel map (T24.3/lock parity).

        Point maps stay point maps: ``box`` degrades to ``select`` so a
        global tool choice can reach this grid without arming a rectangle
        gesture none of these panels draws.
        """
        from sharpmod.maps.map_field_inspection import normalise_tool

        wanted = normalise_tool(tool, allow_box=False)
        for panel in self._panels:
            try:
                panel.map.set_map_tool(wanted)
            except (AttributeError, RuntimeError):
                continue

    def set_point_locked(self, locked: bool) -> None:
        """Lock or unlock the sounding point on every panel (T24.3)."""
        for panel in self._panels:
            try:
                panel.map.set_point_locked(bool(locked))
            except (AttributeError, RuntimeError):
                continue

    def is_point_locked(self) -> bool:
        """Whether every panel agrees the sounding point is locked (T24.3)."""
        states = []
        for panel in self._panels:
            try:
                states.append(bool(panel.map.is_point_locked()))
            except (AttributeError, RuntimeError):
                continue
        return bool(states) and all(states)

    def has_recent_point(self) -> bool:
        """Whether any panel holds a reversible previous point (T24.3)."""
        for panel in self._panels:
            try:
                if bool(panel.map.has_recent_point()):
                    return True
            except (AttributeError, RuntimeError):
                continue
        return False

    def restore_recent_point(self) -> bool:
        """Restore the first available panel recent point (T24.3)."""
        for panel in self._panels:
            try:
                if panel.map.restore_recent_point():
                    return True
            except (AttributeError, RuntimeError):
                continue
        return False

    # -- Qt overrides -------------------------------------------------------- #
    def showEvent(self, event):  # noqa: N802 - Qt override
        """Start fetching once this tab is the one being looked at.

        Tied to visibility rather than to construction. The tab is built the first
        time it is reached, and a controller that is switched on requests straight
        away, so binding the two together meant merely materializing this tab --
        which a test suite or a pass through the source list both do -- launched
        two to four HRRR downloads and left a recurring refresh running behind
        whatever the reader actually went on to look at.
        """
        super().showEvent(event)
        self._set_running(True)

    def hideEvent(self, event):  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._set_running(False)

    def shutdown(self) -> None:
        """Stop every panel's timers and drain its workers."""
        for panel in self._panels:
            try:
                panel.shutdown()
            except Exception:  # noqa: BLE001 - teardown must not raise
                _LOGGER.warning("map_panels.shutdown_failed", exc_info=True)

    # -- internals ----------------------------------------------------------- #
    def _set_running(self, running: bool) -> None:
        """Switch every panel's fetching on or off together."""
        running = bool(running)
        if running == self._running:
            return
        self._running = running
        for panel in self._panels:
            try:
                panel.set_running(running)
            except Exception:  # noqa: BLE001 - one panel must not stop the rest
                _LOGGER.debug("map_panels.running_failed", exc_info=True)

    def _on_panel_point(self, source, lat: float, lon: float) -> None:
        """Fan a clicked point out to the siblings, then report it once."""
        if self._placing_point:
            return
        self._last_clicked = int(getattr(source, "index", 0))
        self.set_point(lat, lon)
        self.pointSelected.emit(float(lat), float(lon))

    # -- shared geographic crosshair (T24.1) --------------------------------- #
    def crosshair(self) -> tuple[float, float] | None:
        """Return the shared ``(lat, lon)``, or ``None`` when no hover."""
        crosshair = getattr(self, "_crosshair", None)
        if crosshair is None:
            return None
        try:
            return (float(crosshair[0]), float(crosshair[1]))
        except (TypeError, ValueError, OverflowError, IndexError):
            return None

    def crosshair_texts(self) -> tuple[str, ...]:
        """Return each visible panel's value at the shared location (T24.1)."""
        lines = []
        for panel in self._panels:
            if not panel.is_active():
                continue
            try:
                text = panel.map.crosshair_text()
            except (AttributeError, RuntimeError):
                continue
            if text:
                lines.append(f"Panel {panel.index + 1}: {text}")
        return tuple(lines)

    def crosshair_accessible_text(self) -> str:
        """Return the spoken shared-crosshair equivalent (T24.1)."""
        lines = self.crosshair_texts()
        if not lines:
            crosshair = self.crosshair()
            if crosshair is None:
                return "No shared map crosshair."
            return (f"Shared map crosshair at {crosshair[0]:.3f}, "
                    f"{crosshair[1]:.3f}; no panel value.")
        return "; ".join(lines)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        """Drop the shared crosshair when the mouse leaves any panel map."""
        from qtpy.QtCore import QEvent

        try:
            is_leave = event.type() == QEvent.Leave
        except (AttributeError, RuntimeError):
            is_leave = False
        if is_leave:
            maps = []
            try:
                maps = [panel.map for panel in self._panels]
            except (AttributeError, RuntimeError):
                maps = []
            if any(watched is widget for widget in maps):
                self.clear_crosshair()
        return super().eventFilter(watched, event)

    def clear_crosshair(self) -> None:
        """Drop the shared crosshair on every panel (T24.1)."""
        self._crosshair = None
        for panel in self._panels:
            try:
                panel.map.clear_crosshair()
            except (AttributeError, RuntimeError):
                continue
        try:
            self.crosshairCleared.emit()
        except RuntimeError:  # pragma: no cover - teardown
            pass

    def _on_panel_crosshair(self, lat: float, lon: float) -> None:
        """Mirror one hovered location onto every *other* panel (T24.1).

        The hovered panel keeps its own hover readout; siblings gain dashed
        guides plus a readout line sampling their own field at the same
        geography. Never a fetch: each panel reads its remembered derived
        grid, and imagery-only/outside-domain panels say so.
        """
        try:
            latitude, longitude = float(lat), float(lon)
        except (TypeError, ValueError, OverflowError):
            return
        import math as _math

        if not (_math.isfinite(latitude) and _math.isfinite(longitude)):
            return
        sender = None
        try:
            sender = self.sender()
        except RuntimeError:  # pragma: no cover - teardown
            sender = None
        self._crosshair = (float(latitude), float(longitude))
        for panel in self._panels:
            if not panel.is_active():
                continue
            widget = getattr(panel, "map", None)
            if widget is None:
                continue
            try:
                if sender is not None and widget is sender:
                    widget.clear_crosshair()
                    continue
            except RuntimeError:  # pragma: no cover - teardown
                continue
            try:
                widget.set_crosshair(float(latitude), float(longitude))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        try:
            self.crosshairMoved.emit(float(latitude), float(longitude))
        except RuntimeError:  # pragma: no cover - teardown
            pass

    def _on_panel_view_settled(self, source) -> None:
        if self._syncing:
            return
        self._sync_from(source)

    def _sync_from(self, source) -> None:
        """Copy ``source``'s view onto every other panel, exactly once.

        The guard is load-bearing, not defensive: ``restore_view_bounds`` emits
        ``viewSettled`` synchronously, so without it the first followed panel
        would announce a settled view back into this method while it was still
        running and the recursion would not terminate.
        """
        try:
            bounds = source.map.view_bounds()
        except (AttributeError, RuntimeError):
            return
        maximised = self.maximised_panel()
        self._syncing = True
        try:
            for panel in self._panels:
                if panel is source or not panel.is_active():
                    continue
                if maximised is not None and panel.index != maximised and \
                        panel.index != getattr(source, "index", -1):
                    continue
                try:
                    if tuple(panel.map.view_bounds()) == tuple(bounds):
                        continue
                    panel.map.restore_view_bounds(bounds)
                except (ValueError, RuntimeError):
                    # A degenerate or non-finite extent is rejected by the map
                    # rather than clamped, and one bad follower must not abort
                    # the fan-out to the others.
                    _LOGGER.debug("map_panels.bounds_rejected bounds=%s", bounds)
        finally:
            self._syncing = False
        self._announce_settled()

    def _announce_settled(self) -> None:
        """Let each field retry now the view has stopped moving.

        Safe to call for every panel on every settle: the controller checks
        coverage, compares against what it already has, and debounces, so this
        only becomes a fetch where the view has newly entered HRRR's domain.
        A maximised grid retries only the visible panel through the active
        gate; hidden panels keep their configured products without spending
        requests (T24.3/T24.5).
        """
        maximised = self.maximised_panel()
        for panel in self._panels:
            if not panel.is_active():
                continue
            if maximised is not None and panel.index != maximised:
                continue
            try:
                panel.on_view_settled()
            except Exception:  # noqa: BLE001 - a retry is not worth raising
                _LOGGER.debug("map_panels.retry_failed", exc_info=True)
        # The shared overlays belong to the host, so it gets the same nudge the
        # fields just had: single-site radar picks its antenna from the view, and
        # storm reports and surface observations re-query the area on show.
        self.viewSettled.emit()

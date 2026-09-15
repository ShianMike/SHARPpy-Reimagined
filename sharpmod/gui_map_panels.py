"""Side-by-side HRRR field panels on one synchronized view.

Reading a forecast is a comparison: reflectivity means one thing over 3000 J/kg
of CAPE and another over 300, and shear only matters where the instability is.
A single map shows one field at a time -- all 23 products share the ``hrrr_field``
overlay key precisely so two opaque colour ramps never stack -- so comparing two
of them meant switching back and forth and holding the first in your head.

This view puts two or four fields side by side instead, each on its own map with
its own selector, and keeps every view locked together so a feature is at the
same screen position in all of them. Panning one pans all; that is the whole
point, because a comparison between panels that are not looking at the same place
is not a comparison.

Why separate maps rather than one widget painting N panels
---------------------------------------------------------
:mod:`sharpmod.gui_visual_comparison` does the latter for soundings: one canvas
paints N rectangles and shares one set of axis ranges. That works there because
the panels are inert drawings. Here each panel needs the real map -- its own
basemap cache, projection, raster overlay slot, pan and wheel handling -- and one
:class:`~sharpmod.gui_overlay_controls.HrrrFieldController` of its own, since a
controller owns exactly one map's single field slot. So these are real sibling
widgets, and the synchronization has to be wired rather than implied.

The feedback loop that shape creates
------------------------------------
``StationMapWidget.restore_view_bounds`` emits ``viewSettled`` *synchronously*,
inside the same call. Fanning a settled view out to the siblings therefore makes
every sibling announce a settled view of its own, straight back into the handler
that is still running -- unbounded recursion on the first pan. :class:`MapPanelsView`
holds a re-entrancy flag for exactly this, and it is the one piece of this module
that is not optional.

A view, not a window
--------------------
This is a plain ``QWidget`` so the picker can host it as a source tab. It was a
separate ``QMainWindow`` first, which was worse in a way that only showed up in
use: a floating window inherits a run and forecast hour from whatever the picker
happened to be showing and has no controls of its own to correct it, so every
panel sat on "not available for that cycle" with nothing on screen to fix. Hosted
as a tab, the run controls sit in the same rail as the panels and the fields
follow them.
"""

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
    QVBoxLayout,
    QWidget,
)

from sharpmod.gui_maps import PointMapWidget
from sharpmod.gui_overlay_controls import HrrrFieldController
from sharpmod.theme import (
    CONTROL_H,
    OBJ_CARD,
    OBJ_HINT,
    OBJ_PLAIN,
    SPACE,
)

_LOGGER = logging.getLogger("sharpmod.gui")

#: Panel counts offered, in the order the chooser lists them. Mirrors
#: ``VisualComparisonWidget``'s two/four choice so the two comparison views in the
#: application agree about what a layout is.
PANEL_COUNTS = (2, 4)

#: The most panels this view will ever build. Every panel is constructed once and
#: then shown or hidden, rather than created and destroyed as the count changes:
#: a controller owns timers and a worker fleet, and churning those on a combo box
#: change is how a fetch ends up orphaned against a dead map.
MAX_PANELS = max(PANEL_COUNTS)

#: What each panel starts on. Deliberately four *different* fields following the
#: ingredients a forecaster actually composes -- what is happening, how unstable
#: it is, how sheared, and what the composite makes of the two -- because four
#: copies of composite reflectivity would demonstrate the layout and none of the
#: point of it.
DEFAULT_PANEL_PRODUCTS = (
    "refc",
    "mlcape",
    "shear-0-6km",
    "stp",
)


def resolve_panel_products(products=None) -> tuple[str, ...]:
    """Return one field key per panel, filling every gap with the default.

    Lenient on purpose, because its only caller is a restore. A remembered
    arrangement is written by whatever build was installed at the time, so a
    truncated list, a blank entry, a list longer than :data:`MAX_PANELS`, or no
    preference at all has to degrade to a working set of panels rather than stop
    the tab building. Keys are *not* checked against the catalogue here:
    ``HrrrFieldController`` resolves an unknown one to the default already, so
    validating it twice would only put the fallback in two places.
    """
    supplied = tuple(products or ())
    resolved: list[str] = []
    for index in range(MAX_PANELS):
        default = DEFAULT_PANEL_PRODUCTS[index % len(DEFAULT_PANEL_PRODUCTS)]
        key = str(supplied[index] or "").strip() if index < len(supplied) else ""
        resolved.append(key or default)
    return tuple(resolved)


class _SharedMapFacade:
    """Present every panel map to one overlay controller as a single map.

    Each controller in :mod:`sharpmod.gui_overlay_controls` talks to its map
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


class _FieldSelector(QWidget):
    """Category, then field -- the same two-step choice the overlay rail uses.

    One flat list was tried first and is what this replaces. Every entry had to
    carry its group to be unambiguous, so all 23 rows read ``Wind Shear · Bulk
    Shear: 0-6 km AGL``: the widest part of each row was the part shared with its
    neighbours, and the popup was taller than the window it opened over, which put
    a panel's field list on top of the panels it was there to be compared against.
    Splitting the choice in two makes the group a heading rather than a prefix
    repeated 23 times, and no list is longer than seven rows.
    """

    #: Emitted only for a field the *user* chose, carrying the resolved key.
    productChanged = Signal(str)

    def __init__(self, product: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_PLAIN)
        # Imported here, not at module scope: the catalogue pulls in NumPy, and
        # the picker's import path is kept clear of it so opening the application
        # does not pay for a field nobody has asked for yet.
        from sharpmod import hrrr_products

        self._catalogue = hrrr_products
        wanted = hrrr_products.get_product(product)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["xs"])

        self._category = QComboBox(self)
        self._category.setAccessibleName("Field group")
        self._category.setToolTip("Which group of fields to choose from")
        self._category.setMinimumHeight(CONTROL_H["sm"])
        for name, _members in hrrr_products.products_by_category():
            self._category.addItem(name, name)
        row.addWidget(self._category)

        self._product = QComboBox(self)
        self._product.setAccessibleName("Field")
        self._product.setToolTip("The field this panel draws")
        self._product.setMinimumHeight(CONTROL_H["sm"])
        self._product.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self._product, 1)

        # Populate before connecting, so building the initial list cannot look
        # like a user selection.
        self._category.setCurrentIndex(
            max(0, self._category.findData(wanted.category))
        )
        self._reload(wanted.key)
        self._category.currentIndexChanged.connect(self._on_category)
        self._product.currentIndexChanged.connect(self._on_product)

    # -- public API ---------------------------------------------------------- #
    def product(self) -> str:
        return str(self._product.currentData() or self._catalogue.DEFAULT_PRODUCT)

    def set_product(self, key: str) -> None:
        """Move to ``key`` without it looking like a user choice."""
        spec = self._catalogue.get_product(key)
        blocked = self._category.blockSignals(True)
        try:
            index = self._category.findData(spec.category)
            if index >= 0:
                self._category.setCurrentIndex(index)
        finally:
            self._category.blockSignals(blocked)
        self._reload(spec.key)

    # -- internals ----------------------------------------------------------- #
    def _reload(self, prefer: str | None = None) -> None:
        """Refill the field box for the selected group, silently.

        Signals are blocked while the list is rebuilt: Qt emits
        ``currentIndexChanged`` as items come and go, and each of those would
        otherwise look like the user picking a field.
        """
        category = str(self._category.currentData() or "")
        blocked = self._product.blockSignals(True)
        try:
            self._product.clear()
            for spec in self._catalogue.available_products():
                if spec.category != category:
                    continue
                self._product.addItem(spec.label, spec.key)
                self._product.setItemData(
                    self._product.count() - 1,
                    _field_tooltip(spec),
                    Qt.ToolTipRole,
                )
            index = self._product.findData(prefer) if prefer else -1
            self._product.setCurrentIndex(max(0, index))
        finally:
            self._product.blockSignals(blocked)

    def _on_category(self, _index: int) -> None:
        """Move to another group, taking its first field."""
        self._reload()
        self._on_product(0)

    def _on_product(self, _index: int) -> None:
        key = self._product.currentData()
        if key:
            self.productChanged.emit(str(key))


def _field_tooltip(spec) -> str:
    """Describe one field, and say so where the model cannot deliver it exactly."""
    lines = [spec.label]
    if spec.description:
        lines.append(str(spec.description))
    if getattr(spec, "caveat", ""):
        lines.append(f"Caveat: {spec.caveat}")
    return "\n".join(lines)


class _MapPanel(QFrame):
    """One map, its own field selector, and the status of its fetch."""

    #: Relayed from the map so the view can fan a settled view out to the
    #: siblings. Carries the panel so the handler knows which view not to
    #: overwrite.
    viewSettled = Signal(object)

    #: Emitted only for a field the *user* chose, carrying this panel's position
    #: and the resolved key, so a host can persist the arrangement.
    productChanged = Signal(int, str)

    #: Relayed from the map. A click chooses where a sounding would be taken;
    #: a double-click asks for it. The click carries the panel, the way
    #: :attr:`viewSettled` does, because which panel was clicked decides which
    #: field a sounding taken here should carry.
    pointSelected = Signal(object, float, float)
    pointActivated = Signal(float, float)

    def __init__(self, index: int, product: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_CARD)
        self._index = int(index)
        # Whether this panel is part of the current layout, tracked here rather
        # than read back from ``isVisible()``. Qt's answer folds in the ancestors:
        # every panel reports invisible while the tab hosting them is in the
        # background, or before it has been shown at all. Gating the view sync on
        # that meant a layout built off-screen fanned its view out to nobody.
        self._active = False
        # Whether the tab hosting this panel is the one on screen. Fetching is
        # gated on *both*, so a panel that exists but is not being looked at costs
        # nothing: the tab is built lazily and would otherwise fire two to four
        # HRRR requests, plus a recurring refresh, for a view nobody switched to.
        self._running = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        outer.setSpacing(SPACE["xs"])

        self.selector = _FieldSelector(product, parent=self)
        outer.addWidget(self.selector)

        # A point map, not a plain station map, because choosing where to take a
        # sounding is half of what these panels are for: the field tells you
        # where to look, and the click has to be available in the same place.
        self.map = PointMapWidget()
        self.map.setParent(self)
        # Flat, always. A field raster cannot be placed correctly on a cone, so a
        # curved projection hides map imagery -- which in this view would mean
        # panels that are empty for a reason nothing on screen explains.
        self.map.set_projection("flat")
        self.map.setMinimumSize(220, 160)
        self.map.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(self.map, 1)

        self.status = QLabel("", self)
        self.status.setObjectName(OBJ_HINT)
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        outer.addWidget(self.status)

        # One controller per map, which is forced rather than chosen: every
        # product writes the same ``hrrr_field`` overlay key, so two controllers
        # sharing a map would clobber each other on every fetch.
        #
        # Built switched *off*. An enabled controller requests immediately from its
        # constructor, so building this tab would reach the network before anyone
        # had looked at it; :meth:`set_running` turns the visible panels on once
        # the tab is shown.
        self.field = HrrrFieldController(
            self.map, parent=self, enabled=False, product=product
        )
        self.field.statusChanged.connect(self._on_status)
        self.selector.productChanged.connect(self._on_product_chosen)
        self.selector.set_product(self.field.product())
        self.map.viewSettled.connect(self._on_view_settled)
        self.map.pointSelected.connect(self._on_point_selected)
        self.map.pointActivated.connect(self.pointActivated)

    # -- public API ---------------------------------------------------------- #
    def product(self) -> str:
        return self.field.product()

    def set_product(self, key: str) -> None:
        self.field.set_product(str(key))
        self.selector.set_product(self.field.product())

    def is_active(self) -> bool:
        """Whether this panel is part of the current layout."""
        return self._active

    def set_active(self, active: bool) -> None:
        """Show or hide the panel, and stop a hidden one doing any work.

        Disabling the controller matters as much as hiding the widget: a hidden
        panel that kept fetching would spend a request and a worker on a field
        nobody can see, on every run, forecast hour, and pan.
        """
        self._active = bool(active)
        self.setVisible(self._active)
        self._sync_enabled()

    def set_running(self, running: bool) -> None:
        """Say whether the view hosting this panel is on screen."""
        self._running = bool(running)
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        """Fetch only while this panel is both in the layout and on screen."""
        wanted = self._active and self._running
        if self.field.is_enabled() != wanted:
            self.field.set_enabled(wanted)

    def set_forecast_reference(self, run, fxx) -> None:
        self.field.set_forecast_reference(run, fxx)

    def on_view_settled(self) -> None:
        """Let the field retry once the view moves back into HRRR coverage."""
        self.field.on_view_settled()

    def shutdown(self) -> None:
        self.field.shutdown()

    # -- internals ----------------------------------------------------------- #
    def _on_product_chosen(self, key: str) -> None:
        self.field.set_product(str(key))
        # Report what the controller settled on rather than what was picked: the
        # catalogue is what decides the key, and that is the value worth storing.
        self.productChanged.emit(self._index, self.field.product())

    def _on_status(self, text: str) -> None:
        """Show what a panel cannot otherwise say about its own field.

        The controller describes a delivered field in three lines -- its name, the
        run that answered, and the product's caveat. Two of those are already on
        this panel: the name is in the selector directly above the map, and the
        caveat is the selector's hover text. Repeated under every panel they cost
        three lines of map each and said nothing new, which is what filled the
        space under the panels in a four-up layout.

        So a *successful* fetch reports only the run and age. Anything else --
        fetching, unavailable, failed -- is passed through whole, because that is
        exactly when the words are the only thing explaining an empty panel.
        """
        raster = self.field.attached_raster()
        if raster is not None:
            text = str(getattr(raster, "subtitle", "") or "")
        self.status.setText(str(text or ""))
        self.status.setVisible(bool(text))

    def _on_view_settled(self) -> None:
        self.viewSettled.emit(self)

    def _on_point_selected(self, lat: float, lon: float) -> None:
        self.pointSelected.emit(self, float(lat), float(lon))

    @property
    def index(self) -> int:
        return self._index


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

    def __init__(self, *, parent=None, panel_count=None, products=None):
        super().__init__(parent)
        self.setObjectName(OBJ_PLAIN)
        self._syncing = False
        self._placing_point = False
        self._running = False
        self._panel_count = PANEL_COUNTS[0]

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(SPACE["sm"])

        self._last_clicked = 0
        self._panels: list[_MapPanel] = []
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
        # Panel 0 is always visible, so it is always the one holding the view the
        # others should adopt.
        self._sync_from(self._panels[0])

    def set_forecast_reference(self, run, fxx) -> None:
        """Point every panel at one run and forecast hour."""
        for panel in self._panels:
            try:
                panel.set_forecast_reference(run, fxx)
            except Exception:  # noqa: BLE001 - one panel must not stop the rest
                _LOGGER.warning("map_panels.reference_failed", exc_info=True)

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
        self._syncing = True
        try:
            for panel in self._panels:
                if panel is source or not panel.is_active():
                    continue
                try:
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
        """
        for panel in self._panels:
            if not panel.is_active():
                continue
            try:
                panel.on_view_settled()
            except Exception:  # noqa: BLE001 - a retry is not worth raising
                _LOGGER.debug("map_panels.retry_failed", exc_info=True)
        # The shared overlays belong to the host, so it gets the same nudge the
        # fields just had: single-site radar picks its antenna from the view, and
        # storm reports and surface observations re-query the area on show.
        self.viewSettled.emit()


__all__ = [
    "DEFAULT_PANEL_PRODUCTS",
    "MAX_PANELS",
    "PANEL_COUNTS",
    "MapPanelsView",
    "resolve_panel_products",
]

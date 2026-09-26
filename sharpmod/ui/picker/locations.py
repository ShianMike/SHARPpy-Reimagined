"""Saved and recent map locations for the picker."""

from __future__ import annotations

from qtpy.QtGui import QAction

from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_locations import SavedLocationsDialog
from sharpmod.state.saved_locations import is_generated_recent_label


class PickerLocationsMixin:
    """Save, restore, and display recently used coordinates."""

    def _current_location_point(self):
        tab = self._tabs.tabText(self._tabs.currentIndex())
        if tab == "Reanalysis (ERA5)" and hasattr(self, "_era5_lat"):
            return self._era5_lat.value(), self._era5_lon.value()
        if (
            tab == "Open File"
            and hasattr(self, "_file_modes")
            and self._file_modes.currentIndex() == 1
        ):
            return self._wrf_lat.value(), self._wrf_lon.value()
        if hasattr(self, "_model_lat"):
            return self._model_lat.value(), self._model_lon.value()
        return None

    def _show_saved_locations(self) -> None:
        dialog = SavedLocationsDialog(
            self._saved_location_store,
            current_point=self._current_location_point,
            use_callback=self._apply_saved_location,
            parent=self,
        )
        dialog.exec()
        self._refresh_location_markers()

    def _apply_saved_location(self, location) -> None:
        self._apply_location(location, preserve_label=True)

    def _apply_recent_location(self, location) -> None:
        """Apply a recent point without promoting its generated coordinates.

        Recent points created without a user label are persisted with a
        coordinate string so they remain identifiable in the menu.  Keeping
        that generated string in the source label field would make the next
        fetch treat it as an explicit place name and skip automatic town
        resolution.
        """
        self._apply_location(
            location,
            preserve_label=not is_generated_recent_label(
                location.name, location.lat, location.lon
            ),
        )

    def _apply_location(self, location, *, preserve_label: bool) -> None:
        label = location.name if preserve_label else ""
        tab = self._tabs.tabText(self._tabs.currentIndex())
        if tab == "Reanalysis (ERA5)":
            self._era5_lat.setValue(location.lat)
            self._era5_lon.setValue(location.lon)
            self._era5_loc.setText(label)
            self._era5_map.set_point(location.lat, location.lon, center=True)
        elif tab == "Field Panels":
            lat_blocked = self._panels_lat.blockSignals(True)
            lon_blocked = self._panels_lon.blockSignals(True)
            try:
                self._panels_lat.setValue(location.lat)
                self._panels_lon.setValue(location.lon)
            finally:
                self._panels_lat.blockSignals(lat_blocked)
                self._panels_lon.blockSignals(lon_blocked)
            self._panels_point_from_spins(center=True)
        elif (
            tab == "Open File"
            and hasattr(self, "_file_modes")
            and self._file_modes.currentIndex() == 1
        ):
            self._wrf_lat.setValue(location.lat)
            self._wrf_lon.setValue(location.lon)
            self._wrf_loc.setText(label)
            self._wrf_map.set_point(location.lat, location.lon, center=True)
        else:
            self._select_tab("Forecast Model")
            self._model_lat.setValue(location.lat)
            self._model_lon.setValue(location.lon)
            self._model_loc.setText(label)
            self._model_map.set_point(location.lat, location.lon, center=True)
        self._remember_point(location.lat, location.lon, location.name)

    def _safe_locations(self, store):
        try:
            return store.load()
        except Exception:  # noqa: BLE001 - corrupt settings should not block GUI
            _LOGGER.exception("saved_locations.load_failed key=%s", store.key)
            return []

    def _refresh_location_markers(self) -> None:
        locations = self._safe_locations(self._saved_location_store)
        for name in ("_model_map", "_era5_map", "_wrf_map"):
            map_widget = getattr(self, name, None)
            if map_widget is not None:
                map_widget.set_saved_points(locations)

    def _remember_point(self, lat, lon, label=None) -> None:
        try:
            location = self._recent_location_store.remember_recent(lat, lon, label)
        except Exception:  # noqa: BLE001 - recents are never fetch-critical
            _LOGGER.exception("recent_locations.save_failed")
            return
        source = self._tabs.tabText(self._tabs.currentIndex())
        self._record_recent_destination("location", label=location.name, lat=location.lat,
                                        lon=location.lon, details=f"Requested point from {source}")
        self._refresh_recent_location_menu()

    def _refresh_recent_location_menu(self) -> None:
        menu = getattr(self, "_recent_locations_menu", None)
        if menu is None:
            return
        menu.clear()
        locations = self._safe_locations(self._recent_location_store)
        from sharpmod.state.recent_destinations import RecentDestinationStore, RecentFormatError, destination

        try:
            entries = RecentDestinationStore(self._settings).load(locations=locations)
        except RecentFormatError:
            entries = ()
        last_used = {entry.identity: entry.last_used_label for entry in entries}
        for location in locations:
            action = QAction(
                f"{location.name}  ({location.lat:.4f}, {location.lon:.4f})",
                menu,
            )
            action.triggered.connect(
                lambda _checked=False, location=location: self._apply_recent_location(
                    location
                )
            )
            identity = destination("location", label=location.name, lat=location.lat, lon=location.lon).identity
            action.setToolTip(last_used.get(identity, "Last used: not recorded (older history)"))
            menu.addAction(action)
        if not locations:
            empty = QAction("(no recent points yet)", menu)
            empty.setEnabled(False)
            menu.addAction(empty)

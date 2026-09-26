"""Remembered map source and overlay preferences."""

from __future__ import annotations

from contextlib import suppress

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.styles.theme import (
    OBJ_CARD_RULE, OBJ_MAP_LAYER_TABS, OBJ_PLAIN, OBJ_SECTION_LABEL, SPACE,
)
from sharpmod.ui.picker.layout import rail_card as _rail_card
from sharpmod.ui.picker.observed_sources import (
    DEFAULT_OBSERVED_SOURCE, OBSERVED_SOURCES,
)
from sharpmod.ui.picker.selection import refresh_selection_feedback

TAB_LOCATOR_SELECTORS = {
    "Station Map": "_map_locator",
    "Forecast Model": "_model_locator",
    "Field Panels": "_panels_locator",
}




class LayerPreferencesMixin:
    """Read stored layer choices before the controls exist."""

    def _startup_observed_source(self) -> str:
        """Return the remembered observed source, for a rail being built.

        Unlike the overlays, the source *is* restored on launch: it costs no
        network by itself, and someone who works from one archive should not
        have to re-pick it every session. A key written by a version that
        offered different sources degrades to the default.
        """
        offered = {key for key, _label, _tooltip in OBSERVED_SOURCES}
        try:
            stored = (
                str(self._settings.value("observed/provider", "") or "").strip().lower()
            )
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return DEFAULT_OBSERVED_SOURCE
        return stored if stored in offered else DEFAULT_OBSERVED_SOURCE

    def _observed_source(self) -> str:
        """Return the selected source key, before or after the rail exists."""
        combo = getattr(self, "_map_source", None)
        if combo is None:
            # Availability probes and fetches can be requested by a test or a
            # restored session before the map tab has been built.
            return self._startup_observed_source()
        key = combo.currentData()
        return str(key) if key else DEFAULT_OBSERVED_SOURCE

    def _map_on_source_changed(self) -> None:
        """Persist the chosen source and re-grade the current selection.

        The preflight cache is keyed by source, so nothing needs discarding
        here: a switch simply misses the cache and re-probes.
        """
        key = self._observed_source()
        try:
            self._settings.setValue("observed/provider", key)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("observed_source.unsaved", exc_info=True)
        _LOGGER.info("observed_source.selected provider=%s", key)
        self._queue_availability(
            self._map_selected_id, self._map_when(), self._map_avail
        )
        refresh_selection_feedback(self, "map")
        refresh_selection_feedback(self, "uwyo")

    def _startup_field_product(self) -> str:
        """Return the remembered HRRR field, for a controller being built.

        Only the *product* is remembered, not whether the overlay was on. A
        model field costs a GRIB subset and a reprojection, so restoring it
        automatically would mean every launch reaches for the network before the
        user has asked for anything -- the same reason the outlook and radar
        overlays both default to off. Remembering the choice still means the user
        who always looks at MLCAPE finds it already selected.
        """
        # Imported here, not at module scope: the catalogue pulls in NumPy, and
        # the picker has to reach first paint without it.
        from sharpmod.providers import hrrr_products

        try:
            stored = self._settings.value("overlays/hrrr_field_product", "")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return hrrr_products.DEFAULT_PRODUCT
        # get_product resolves anything unknown, so a key written by a different
        # version of the catalogue degrades to the default instead of failing.
        return hrrr_products.get_product(str(stored or "")).key

    def _startup_radar_scope(self) -> str:
        """Return the remembered radar scope, for a controller being built.

        As with the field, only the *choice* is remembered and never whether the
        overlay was on, so a launch still reaches for no network until asked.
        """
        from sharpmod.ui.maps.overlays.controllers import SCOPE_MOSAIC, SCOPE_SITE

        try:
            stored = (
                str(self._settings.value("overlays/radar_scope", "") or "")
                .strip()
                .lower()
            )
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return SCOPE_SITE
        return stored if stored in (SCOPE_SITE, SCOPE_MOSAIC) else SCOPE_SITE

    def _startup_radar_site(self) -> str:
        """Return the WSR-88D pinned last session, or follow the map.

        An unknown identifier degrades to following the map rather than failing:
        the catalogue is a bundled resource and a site can leave it between
        versions.
        """
        from sharpmod.ui.maps.overlays.controllers import SITE_AUTO

        try:
            stored = (
                str(self._settings.value("overlays/radar_site", "") or "")
                .strip()
                .upper()
            )
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return SITE_AUTO
        from sharpmod.providers.radar_site import site_by_id

        return stored if site_by_id(stored) is not None else SITE_AUTO

    def _startup_satellite_channel(self) -> str:
        try:
            stored = str(
                self._settings.value("overlays/goes_channel", "infrared")
                or "infrared"
            ).lower()
        except Exception:  # noqa: BLE001 - an unreadable preference is harmless
            stored = "infrared"
        return stored if stored in {"visible", "infrared"} else "infrared"

    def _startup_surface_density(self) -> str:
        try:
            stored = str(
                self._settings.value("overlays/surface_density", "normal")
                or "normal"
            ).lower()
        except Exception:  # noqa: BLE001 - an unreadable preference is harmless
            stored = "normal"
        return stored if stored in {"sparse", "normal", "dense"} else "normal"

    def _startup_layer_opacity(self, key: str, fallback: float) -> float:
        """Return a remembered opacity in the slider band (T19.2).

        A stored value outside the band clamps rather than failing the launch;
        an unreadable or missing value degrades to the controller default.
        """
        from sharpmod.maps.map_layers import normalise_opacity

        try:
            stored = self._settings.value(f"overlays/{key}_opacity", None)
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return float(fallback)
        if stored is None or str(stored).strip() == "":
            return float(fallback)
        try:
            return normalise_opacity(float(stored))
        except (TypeError, ValueError, OverflowError):
            return float(fallback)

    def _startup_field_opacity(self) -> float:
        """Return the remembered model-field opacity (T19.2)."""
        return self._startup_layer_opacity("hrrr_field", 0.75)

    def _startup_radar_opacity(self) -> float:
        """Return the remembered radar opacity (T19.2)."""
        return self._startup_layer_opacity("radar", 0.85)

    def _startup_satellite_opacity(self) -> float:
        """Return the remembered satellite opacity (T19.2)."""
        return self._startup_layer_opacity("goes", 0.72)

    def _startup_layer_preset(self) -> str:
        """Return the remembered layer preset name, migrated (T19.2)."""
        from sharpmod.maps.map_layers import migrate_preset_choice

        try:
            stored = self._settings.value("overlays/layer_preset", "")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return ""
        return migrate_preset_choice(stored) if stored else ""

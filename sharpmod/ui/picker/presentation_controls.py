"""Picker map labels, legends, and color-scale preferences."""

from __future__ import annotations

from qtpy.QtWidgets import QCheckBox

from sharpmod.ui.features.gui_common import _LOGGER


class MapPresentationControlsMixin:
    """Persist and synchronize presentation across picker maps."""

    def _map_presentation_combo(self, kinds, current, tooltip, accessible,
                                on_changed):
        """Return a small persisted presentation combo for a Region card."""
        from qtpy.QtWidgets import QComboBox

        combo = QComboBox()
        for key, label in kinds:
            combo.addItem(label, key)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.setToolTip(tooltip)
        combo.setAccessibleName(accessible)
        combo.currentIndexChanged.connect(
            lambda _index: on_changed(combo.currentData()))
        return combo

    def _startup_map_presentation(self) -> dict:
        """Return the remembered geography presentation (T16.4, T20 legend)."""
        try:
            density = str(
                self._settings.value("maps/label_density", "standard")
                or "standard")
            units = str(
                self._settings.value("maps/scale_units", "metric") or "metric")
            legend = self._settings.value("maps/legend", None)
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return {"label_density": "standard", "scale_units": "metric"}
        from sharpmod.maps.map_geography import normalise_label_density
        from sharpmod.maps.map_legends import restore_legend_state

        density = normalise_label_density(density)
        units = "imperial" if units.strip().lower() in {
            "imperial", "mi", "miles"} else "metric"
        state = {"label_density": density, "scale_units": units}
        if isinstance(legend, dict):
            state["legend"] = restore_legend_state(legend)
        return state

    def _on_map_presentation_chosen(self, key, value) -> None:
        """Persist one geography presentation choice and push it to maps."""
        if key == "label_density":
            from sharpmod.maps.map_geography import normalise_label_density

            value = normalise_label_density(value)
        else:
            value = "imperial" if str(value or "").strip().lower() in {
                "imperial", "mi", "miles"} else "metric"
        try:
            self._settings.setValue(f"maps/{key}", value)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("maps.presentation_unsaved", exc_info=True)
        for widget in self._map_widgets():
            try:
                if key == "label_density" and hasattr(
                        widget, "set_label_density"):
                    widget.set_label_density(value)
                elif hasattr(widget, "set_scale_units"):
                    widget.set_scale_units(value)
            except Exception:  # noqa: BLE001 - one map must not block the rest
                _LOGGER.debug("maps.presentation_failed", exc_info=True)
        if key == "scale_units":
            # A locator may be pinned independently of the main map, but its
            # scale bar still uses the same selected km/mi units. Map widgets
            # repaint on this preference change without emitting viewSettled,
            # so refresh each open sounding's collection-level presentation.
            for viewer in tuple(getattr(self, "_viewers", ())):
                try:
                    binding = getattr(viewer, "_sharpmod_locator_binding", None)
                    if binding is not None:
                        binding.sync()
                except RuntimeError:
                    continue
        _LOGGER.info("maps.presentation %s=%s", key, value)

    def _apply_map_presentation(self) -> None:
        """Push the remembered presentation onto every map that exists."""
        try:
            remembered = self._startup_map_presentation()
        except Exception:  # noqa: BLE001 - presentation never blocks maps
            return
        for widget in self._map_widgets():
            try:
                widget.restore_presentation_preferences(remembered)
            except Exception:  # noqa: BLE001 - one map must not block the rest
                _LOGGER.debug("maps.presentation_failed", exc_info=True)

    def _persist_legend_state(self) -> None:
        """Persist every map's legend choices under one key (T20/session)."""
        try:
            states = [widget.legend_state() for widget in self._map_widgets()
                      if hasattr(widget, "legend_state")]
        except Exception:  # noqa: BLE001 - legend never blocks the UI
            return
        if not states:
            return
        merged: dict = {"version": 1, "corner": "", "collapsed": False,
                        "locks": {}}
        for state in states:
            if not isinstance(state, dict):
                continue
            if state.get("corner") and not merged["corner"]:
                merged["corner"] = str(state["corner"])
            if state.get("collapsed"):
                merged["collapsed"] = True
            locks = state.get("locks")
            if isinstance(locks, dict):
                merged["locks"].update(locks)
        try:
            self._settings.setValue("maps/legend", merged)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("maps.legend_unsaved", exc_info=True)

    def _legend_widgets(self) -> list:
        """Return every map widget answering legend controls (T20)."""
        widgets = []
        for widget in self._map_widgets():
            if all(hasattr(widget, name) for name in (
                    "set_legend_corner", "set_legend_collapsed",
                    "set_scale_locked", "is_scale_locked")):
                widgets.append(widget)
        return widgets

    def _apply_legend_choice(self, action, *args) -> None:
        """Apply a legend choice to every map, then persist (T20)."""
        for widget in self._legend_widgets():
            try:
                getattr(widget, action)(*args)
            except Exception:  # noqa: BLE001 - one map must not block rest
                _LOGGER.debug("maps.legend_failed", exc_info=True)
        self._persist_legend_state()
        self._sync_legend_controls()

    def _legend_lock_product(self) -> str:
        """Return the field product the Fixed-scale control governs (T20.3)."""
        for attribute in ("_model_field", "_map_field"):
            controller = getattr(self, attribute, None)
            if controller is None:
                continue
            try:
                return str(controller.product() or "")
            except (AttributeError, RuntimeError):
                continue
        return ""

    def _startup_scale_locked(self) -> bool:
        """Return whether any fixed-scale lock is remembered (T20.3)."""
        try:
            legend = self._settings.value("maps/legend", None)
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return False
        return bool(isinstance(legend, dict) and legend.get("locks"))

    def _on_legend_corner_chosen(self, value) -> None:
        """Persist the preferred legend corner and push it (T20.1)."""
        from sharpmod.maps.map_legends import LEGEND_CORNERS

        wanted = str(value or "").strip().lower().replace("_", "-")
        self._apply_legend_choice("set_legend_corner",
                                  wanted if wanted in LEGEND_CORNERS else "")
        _LOGGER.info("maps.legend corner=%s", wanted or "auto")

    def _on_legend_collapsed_toggled(self, collapsed: bool) -> None:
        """Persist legend collapse and push it to every map (T20.1)."""
        self._apply_legend_choice("set_legend_collapsed", bool(collapsed))
        _LOGGER.info("maps.legend collapsed=%s", bool(collapsed))

    def _legend_corner_combo(self):
        """Return a Region-card legend-corner combo (T20.1).

        One instance per rail, all routed through the same choice applier, so
        every tab answers identically without sharing a widget across layouts.
        """
        from sharpmod.maps.map_legends import LEGEND_CORNERS

        try:
            remembered = self._startup_map_presentation()
            corner = str((remembered.get("legend") or {}).get("corner") or "")
        except Exception:  # noqa: BLE001 - presentation never blocks controls
            corner = ""
        combo = self._map_presentation_combo(
            (("", "Legend: auto corner"),) + tuple(
                (name, f"Legend: {name}") for name in LEGEND_CORNERS),
            corner,
            "Choose a fixed map-legend corner. Automatic starts at bottom "
            "right and avoids the sounding point and pinned inspection card; "
            "pointer hover never moves either mode.",
            "Legend corner",
            lambda value: self._on_legend_corner_chosen(value),
        )
        combos = getattr(self, "_legend_corner_combos", None)
        if combos is None:
            combos = []
            self._legend_corner_combos = combos
        combos.append(combo)
        self._map_legend_corner_combo = combo
        return combo

    def _legend_collapsed_check(self):
        """Return a Region-card compact-legend check (T20.1)."""
        try:
            remembered = self._startup_map_presentation()
            collapsed = bool((remembered.get("legend") or {}).get(
                "collapsed", False))
        except Exception:  # noqa: BLE001 - presentation never blocks controls
            collapsed = False
        check = QCheckBox("Compact legend")
        check.setChecked(collapsed)
        check.setToolTip(
            "Collapse the legend to one line keeping product, units, and "
            "scale. The key and prose return on expansion.")
        check.setAccessibleName("Compact legend")
        check.toggled.connect(self._on_legend_collapsed_toggled)
        checks = getattr(self, "_legend_collapsed_checks", None)
        if checks is None:
            checks = []
            self._legend_collapsed_checks = checks
        checks.append(check)
        self._map_legend_collapsed_check = check
        return check

    def _scale_lock_check(self):
        """Return a Region-card fixed-scale check (T20.3)."""
        check = QCheckBox("Fixed color scale")
        check.setChecked(self._startup_scale_locked())
        check.setToolTip(
            "Lock the field's color scale so comparisons across panels and "
            "times read one stated range. Range changes are labelled, never "
            "silent.")
        check.setAccessibleName("Fixed color scale")
        check.toggled.connect(self._on_scale_lock_toggled)
        checks = getattr(self, "_scale_lock_checks", None)
        if checks is None:
            checks = []
            self._scale_lock_checks = checks
        checks.append(check)
        self._map_scale_lock_check = check
        return check

    def _sync_legend_controls(self) -> None:
        """Bring every rail's legend controls to the persisted state (T20).

        A choice made on one tab routes to every map through
        :meth:`_apply_legend_choice`; this keeps the sibling rails' widgets
        showing the same choice instead of disagreeing until they are touched.
        """
        try:
            remembered = self._startup_map_presentation()
            legend = remembered.get("legend") or {}
        except Exception:  # noqa: BLE001 - controls never break the UI
            return
        corner = str(legend.get("corner") or "")
        collapsed = bool(legend.get("collapsed", False))
        locked = self._startup_scale_locked()
        for combo in getattr(self, "_legend_corner_combos", []) or []:
            try:
                if combo.currentData() != corner:
                    combo.blockSignals(True)
                    try:
                        combo.setCurrentIndex(max(0, combo.findData(corner)))
                    finally:
                        combo.blockSignals(False)
            except (AttributeError, RuntimeError):
                continue
        for check in getattr(self, "_legend_collapsed_checks", []) or []:
            try:
                if check.isChecked() != collapsed:
                    check.blockSignals(True)
                    try:
                        check.setChecked(collapsed)
                    finally:
                        check.blockSignals(False)
            except (AttributeError, RuntimeError):
                continue
        for check in getattr(self, "_scale_lock_checks", []) or []:
            try:
                if check.isChecked() != locked:
                    check.blockSignals(True)
                    try:
                        check.setChecked(locked)
                    finally:
                        check.blockSignals(False)
            except (AttributeError, RuntimeError):
                continue

    def _on_scale_lock_toggled(self, locked: bool) -> None:
        """Lock or release the field scale on every map (T20.3)."""
        product = self._legend_lock_product()
        if not product:
            self.statusBar().showMessage(
                "No model field is selected to lock.", 4000)
            check = getattr(self, "_map_scale_lock_check", None)
            if check is not None and check.isChecked() != (not locked):
                check.blockSignals(True)
                try:
                    check.setChecked(False)
                finally:
                    check.blockSignals(False)
            return
        self._apply_legend_choice("set_scale_locked", product, bool(locked))
        self.statusBar().showMessage(
            f"Fixed color scale {'on' if locked else 'off'} for {product}.",
            5000)
        _LOGGER.info("maps.legend scale_locked=%s product=%s", bool(locked),
                     product)

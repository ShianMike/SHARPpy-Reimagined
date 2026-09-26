"""PickerPreferences methods for PickerWindow."""

from __future__ import annotations

from pathlib import Path
from qtpy.QtCore import QUrl
from qtpy.QtGui import QAction
from qtpy.QtGui import QActionGroup
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import QApplication
from qtpy.QtWidgets import QCheckBox
from qtpy.QtWidgets import QFileDialog
from qtpy.QtWidgets import QMessageBox
from sharpmod.ui.features.gui_common import APP_NAME
from sharpmod.ui.features.gui_common import APP_VERSION
from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_common import _install_fullscreen_action
from sharpmod.ui.features.gui_common import _show_controls_dialog
from sharpmod.ui.features.gui_maps import MAP_PROJECTIONS
from sharpmod.ui.features.gui_settings import UNIT_DEFAULTS
from sharpmod.ui.features.gui_settings import UNIT_OPTIONS
from sharpmod.ui.features.gui_settings import _DEFAULT_SKEWT_PARCEL
from sharpmod.ui.features.gui_settings import _UnitPreferencesDialog
from sharpmod.ui.features.gui_settings import _add_default_parcel_tab
from sharpmod.ui.features.gui_settings import _add_interface_tab
from sharpmod.ui.features.gui_settings import _apply_default_parcel_to_window
from sharpmod.ui.features.gui_settings import _apply_selected_color_style
from sharpmod.ui.features.gui_settings import _apply_unit_preferences_to_window
from sharpmod.ui.features.gui_settings import _normalize_default_parcel
from sharpmod.ui.features.gui_settings import _normalize_interface_preferences
from sharpmod.ui.features.gui_settings import _normalize_unit_preferences
from sharpmod.ui.features.gui_settings import _read_config_preferences
from sharpmod.ui.features.gui_settings import _read_config_unit
from sharpmod.ui.features.gui_settings import _read_interface_preferences
from sharpmod.ui.features.gui_settings import _read_settings_preferences
from sharpmod.ui.features.gui_settings import _save_interface_preferences
from sharpmod.ui.features.gui_settings import _save_settings_preferences
from sharpmod.ui.features.gui_settings import _write_config_preferences
from sharpmod.ui.features.gui_settings import _write_unit_preferences_to_config
from sharpmod.ui.features.gui_theme import apply_theme
import tempfile
from sharpmod import gui_picker as _picker_api


class PickerPreferencesMixin:
    """Picker behavior grouped by responsibility."""

    def _build_menu(self) -> None:
        filemenu = self.menuBar().addMenu("&File")
        open_act = QAction("&Open Sounding File\u2026", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._browse_and_open)
        filemenu.addAction(open_act)
        session_act = QAction("Open Analysis &Session…", self)
        session_act.setShortcut("Ctrl+Shift+O")
        session_act.triggered.connect(self._open_analysis_session)
        filemenu.addAction(session_act)
        self._open_session_action = session_act
        from sharpmod.ui.picker.recents import install_recent_destinations

        install_recent_destinations(self, filemenu)
        filemenu.addSeparator()
        pref_act = QAction("&Preferences\u2026", self)
        pref_act.setShortcut("Ctrl+,")
        pref_act.triggered.connect(self.preferencesbox)
        filemenu.addAction(pref_act)
        units_act = QAction("&Units\u2026", self)
        units_act.setShortcut("Ctrl+U")
        units_act.triggered.connect(lambda _checked=False: self.unit_preferencesbox())
        filemenu.addAction(units_act)
        filemenu.addSeparator()
        combine_act = QAction("Add New Soundings to Active Window", self)
        combine_act.setCheckable(True)
        combine_act.setChecked(self._combine_soundings_enabled())
        combine_act.toggled.connect(self._set_combine_soundings_enabled)
        filemenu.addAction(combine_act)
        self._combine_soundings_action = combine_act
        prefetch_act = QAction("Prefetch Next Forecast Hour", self)
        prefetch_act.setCheckable(True)
        prefetch_act.setChecked(self._model_prefetch_enabled())
        prefetch_act.toggled.connect(self._save_model_prefetch)
        filemenu.addAction(prefetch_act)
        self._model_prefetch_action = prefetch_act
        cache_library_act = QAction("Downloaded Data &Library…", self)
        cache_library_act.triggered.connect(self._show_cache_manager)
        filemenu.addAction(cache_library_act)
        self._cache_library_action = cache_library_act
        clear_cache_act = QAction("Clear Downloaded Model Cache", self)
        clear_cache_act.triggered.connect(self._clear_model_cache)
        filemenu.addAction(clear_cache_act)
        self._clear_model_cache_action = clear_cache_act
        filemenu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        filemenu.addAction(quit_act)

        locations_menu = self.menuBar().addMenu("&Locations")
        manage_locations = QAction("Manage Saved Locations…", self)
        manage_locations.setShortcut("Ctrl+L")
        manage_locations.triggered.connect(self._show_saved_locations)
        locations_menu.addAction(manage_locations)
        self._manage_locations_action = manage_locations
        self._recent_locations_menu = locations_menu.addMenu("Recent Points")

        export_menu = self.menuBar().addMenu("&Export")
        map_export = QAction("Active Map Figure (PNG)…", self)
        map_export.triggered.connect(self._export_active_map_figure)
        export_menu.addAction(map_export)
        panels_export = QAction("Two/Four-Panel Map Figure (PNG)…", self)
        panels_export.triggered.connect(self._export_panel_map_figure)
        export_menu.addAction(panels_export)
        export_menu.addSeparator()
        recent_maps = QAction("Recent Exports…", self)
        recent_maps.triggered.connect(self._show_map_recent_exports)
        export_menu.addAction(recent_maps)
        self._map_export_action = map_export
        self._panels_export_action = panels_export

        viewmenu = self.menuBar().addMenu("&View")
        _install_fullscreen_action(self, viewmenu)

        # Beside full screen, because both answer "give the content more room".
        # It was a button in each source's own header, which spent a window-width
        # row on one control and repeated it six times.
        hide_controls = QAction("&Hide Controls", self)
        hide_controls.setCheckable(True)
        hide_controls.setShortcut("Ctrl+Shift+H")
        hide_controls.setToolTip(
            "Hide this source's configuration controls for more map space, "
            "without changing the selection or any active request"
        )
        hide_controls.toggled.connect(self._on_hide_controls_toggled)
        hide_controls.setEnabled(False)
        viewmenu.addAction(hide_controls)
        self._hide_controls_action = hide_controls

        # Map projection. One menu group rather than a control on each map tab:
        # it is a preference about how maps look, not a per-tab setting, and
        # four copies of it could disagree with each other.
        viewmenu.addSeparator()
        projection_menu = viewmenu.addMenu("Map &Projection")
        self._projection_group = QActionGroup(self)
        self._projection_group.setExclusive(True)
        self._projection_actions = {}
        for name, label, tip in (
            (
                "flat",
                "&Flat (equirectangular)",
                "Straight meridians and parallels. Correct at every extent, and "
                "the only view that can show map imagery such as radar.",
            ),
            (
                "curved",
                "&Curved (conformal conic)",
                "Meridians converge and parallels bow, the way an operational "
                "forecast chart is drawn.\n"
                "Regional views only: a whole-hemisphere or equator-centred "
                "extent falls back to flat, and map imagery is hidden because it "
                "cannot be placed correctly on a cone.",
            ),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.setData(name)
            self._projection_group.addAction(action)
            projection_menu.addAction(action)
            self._projection_actions[name] = action
        stored = str(self._settings.value("maps/projection", "flat") or "flat").lower()
        if stored not in MAP_PROJECTIONS:
            stored = "flat"
        self._map_projection = stored
        self._projection_actions[stored].setChecked(True)
        self._projection_group.triggered.connect(self._on_projection_chosen)

        # T17.2 map navigation menu: the active source tab's map answers, so
        # the actions always describe the map being looked at rather than a
        # fixed tab. Shortcuts match the rail row and the map-focus keys.
        # T18.1 adds the explicit tools, lock, and recent-point actions beside
        # them so every map interaction is reachable from one menu.
        viewmenu.addSeparator()
        map_tool_menu = viewmenu.addMenu("Map &Tool")
        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_actions = {}
        for tool, label, tip in (
            ("select", "&Select Point",
             "Place the sounding point; double-click fetches (V with map focus)"),
            ("inspect", "&Inspect Values",
             "Read values and pin cards without moving the point (I with map focus)"),
            ("box", "Draw &Box",
             "Drag a rectangle; a click still places the point (B with map focus)"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.setStatusTip(tip)
            action.setData(tool)
            action.triggered.connect(
                lambda _checked=False, _tool=tool: self._trigger_map_tool(_tool))
            self._tool_group.addAction(action)
            map_tool_menu.addAction(action)
            self._tool_actions[tool] = action
        try:
            stored_tool = str(self._settings.value("maps/tool", "select") or "select")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            stored_tool = "select"
        from sharpmod.maps.map_field_inspection import normalise_tool

        stored_tool = normalise_tool(stored_tool, allow_box=True)
        self._tool_actions[stored_tool].setChecked(True)
        self._map_tool_name = stored_tool
        lock_act = QAction("&Lock Sounding Point", self)
        lock_act.setCheckable(True)
        lock_act.setToolTip("Lock the sounding point so clicks cannot move it; "
                            "inspect, pan, zoom, history, and box keep working")
        lock_act.setStatusTip("Lock the sounding point so clicks cannot move it")
        lock_act.triggered.connect(
            lambda checked=False: self._trigger_map_lock(bool(checked)))
        viewmenu.addAction(lock_act)
        self._lock_point_action = lock_act
        recent_act = QAction("Restore &Recent Point", self)
        recent_act.setToolTip("Return to the previous sounding point (U with map focus)")
        recent_act.setStatusTip("Return to the previous sounding point")
        recent_act.triggered.connect(self._trigger_map_recent_point)
        viewmenu.addAction(recent_act)
        self._recent_point_action = recent_act
        copy_coords_act = QAction("Copy Pinned &Coordinates", self)
        copy_coords_act.setToolTip("Copy the pinned card's coordinates")
        copy_coords_act.setStatusTip("Copy the pinned card's coordinates")
        copy_coords_act.triggered.connect(self._trigger_map_copy_coordinates)
        viewmenu.addAction(copy_coords_act)
        self._copy_coords_action = copy_coords_act
        copy_value_act = QAction("Copy Pinned &Value", self)
        copy_value_act.setToolTip("Copy the pinned card's value with units")
        copy_value_act.setStatusTip("Copy the pinned card's value with units")
        copy_value_act.triggered.connect(self._trigger_map_copy_value)
        viewmenu.addAction(copy_value_act)
        self._copy_value_action = copy_value_act
        save_point_act = QAction("&Save Pinned Point…", self)
        save_point_act.setToolTip("Save the pinned card's point as a named location")
        save_point_act.setStatusTip("Save the pinned card's point as a named location")
        save_point_act.triggered.connect(self._trigger_map_save_point)
        viewmenu.addAction(save_point_act)
        self._save_point_action = save_point_act
        self._nav_actions = {}
        for action_id, label, shortcut, tip in (
            ("zoom-in", "Zoom Map &In", "Ctrl++",
             "Zoom the active map in at its centre"),
            ("zoom-out", "Zoom Map &Out", "Ctrl+-",
             "Zoom the active map out from its centre"),
            ("fit", "&Fit Map to Region", "Ctrl+0",
             "Return the active map to its region's extent"),
            ("center-on-selection", "&Centre Map on Selection", "",
             "Centre the active map on its sounding point or station (L with map focus)"),
            ("previous-view", "&Previous Map View", "Alt+Left",
             "Restore the active map's previous view"),
            ("next-view", "&Next Map View", "Alt+Right",
             "Redo the active map's undone view"),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.setToolTip(tip)
            action.setStatusTip(tip)
            action.setData(action_id)
            action.triggered.connect(
                lambda _checked=False, _id=action_id: self._trigger_map_navigation(_id))
            viewmenu.addAction(action)
            self._nav_actions[action_id] = action

        # A shortcut to the Field Panels source tab, not a window. Kept in this
        # menu as well as the source list because the panels answer a question
        # about the *map* -- which field to trust where -- and that is what a
        # reader is already in this menu for.
        viewmenu.addSeparator()
        panels_act = QAction("Model Field &Panels", self)
        panels_act.setShortcut("Ctrl+Shift+P")
        panels_act.setToolTip(
            "Compare two or four HRRR fields side by side on one synchronized view"
        )
        panels_act.triggered.connect(self._show_model_panels)
        viewmenu.addAction(panels_act)
        self._model_panels_action = panels_act

        self._begin_help_menu()

    def _begin_help_menu(self) -> None:
        """Begin the Help menu after the View menu is complete."""
        helpmenu = self.menuBar().addMenu("&Help")
        from sharpmod.ui.features.gui_commands import install_command_palette

        install_command_palette(self, menu=helpmenu)
        controls_act = QAction("Sounding Window &Controls", self)
        controls_act.triggered.connect(self._show_controls_help)
        helpmenu.addAction(controls_act)
        debug_act = QAction("Open &Debug Log Folder", self)
        debug_act.triggered.connect(self._open_debug_log_folder)
        helpmenu.addAction(debug_act)
        helpmenu.addSeparator()
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._about)
        helpmenu.addAction(about_act)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "A modernized, standalone fork of SHARPpy.<br>"
            "SPC-style skew-T / hodograph sounding analysis (Qt6/PySide6).<br><br>"
            "<b>Tips:</b> type to filter stations, double-click one to open it, "
            "or drag a sounding file onto the window.",
        )

    def _open_analysis_session(self, path=None, *, recovered=False) -> None:
        """Open a validated session as one new multi-sounding viewer."""
        if isinstance(path, bool):  # QAction.triggered supplies ``checked``.
            path = None
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open Analysis Session",
                str(Path.home()),
                "SHARPpy Analysis Session (*.sharpmod-session)",
            )
        path = str(path or "").strip()
        if not path:
            return
        from sharpmod.state.sessions import (
            SessionFormatError,
            read_session,
            restore_collection,
        )

        try:
            # Validate and reconstruct every sounding before composing a window;
            # a malformed file therefore cannot partially mutate the UI.
            document = read_session(path)
            collections = [
                restore_collection(payload) for payload in document["collections"]
            ]
        except (OSError, SessionFormatError, ValueError) as exc:
            _LOGGER.exception("analysis_session.open_failed path=%s", path)
            QMessageBox.critical(
                self, APP_NAME, f"The analysis session could not be opened:\n{exc}"
            )
            return

        first = collections[0]
        try:
            stn_id = first.getMeta("loc")
        except Exception:
            stn_id = "Session"
        try:
            win = _picker_api.compose_interactive(self._config(), first, self, stn_id=stn_id)
            for collection in collections[1:]:
                win.addProfileCollection(collection, focus=True, check_integrity=False)
                add_locator = getattr(win, "_sharpmod_add_locator_collection", None)
                if callable(add_locator):
                    add_locator(collection)
                _picker_api._start_locator_overlay_fetch(
                    win,
                    collection,
                    product=_picker_api._overlay_product_for(self),
                    controller=self,
                    spec=_picker_api._locator_spec_for(self, collection),
                )
            _picker_api._apply_viewer_session_state(
                win,
                document.get("active_collection", 0),
                document.get("ui_state"),
            )
        except Exception as exc:  # noqa: BLE001 - user-facing restore error
            _LOGGER.exception("analysis_session.display_failed path=%s", path)
            try:
                win.close()
            except Exception:
                pass
            QMessageBox.critical(
                self,
                APP_NAME,
                f"The session was valid, but its viewer could not be opened:\n{exc}",
            )
            return

        win._sharpmod_session_path = "" if recovered else path
        if recovered:
            win._sharpmod_recovered_from = path
        else:
            win._sharpmod_session_saved_depth = 0
            win._sharpmod_session_saved_revision = int(
                getattr(win, "_sharpmod_session_revision", 0) or 0
            )
        history = getattr(win, "_sharpmod_history", None)
        if history is not None:
            history.clear()
        self._viewers.append(win)
        viewer_id = id(win)
        win.destroyed.connect(
            lambda *_args, viewer_id=viewer_id, path=path: _LOGGER.info(
                "viewer.closed viewer=%s session=%s", viewer_id, path
            )
        )
        watch_profiles = getattr(self, "_watch_loaded_profile_viewer", None)
        if callable(watch_profiles):
            watch_profiles(win)
        refresh_profiles = getattr(self, "_refresh_loaded_profile_markers", None)
        if callable(refresh_profiles):
            refresh_profiles()
        verb = "Recovered" if recovered else "Opened"
        self.statusBar().showMessage(
            f"{verb} analysis session with {len(collections)} sounding"
            f"{'s' if len(collections) != 1 else ''}",
            5000,
        )
        _LOGGER.info(
            "analysis_session.%s path=%s viewer=%s soundings=%d",
            "recovered" if recovered else "opened",
            path,
            viewer_id,
            len(collections),
        )
        record = getattr(self, "_record_recent_destination", None)
        if callable(record) and not recovered:
            from sharpmod.state.recent_destinations import describe_collection

            noun = "sounding" if len(collections) == 1 else "soundings"
            details = f"{len(collections)} {noun} · " + " / ".join(
                describe_collection(collection) for collection in collections[:3]
            )
            record("session", path, details=details)
        return True

    def _show_controls_help(self) -> None:
        _show_controls_dialog(self)

    def _open_debug_log_folder(self) -> None:
        log_path = _picker_api._configure_debug_logging()
        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(log_path.parent.resolve()))
        )
        if opened:
            self.statusBar().showMessage(f"Debug log: {log_path}")
            _LOGGER.info("diagnostics.folder_opened path=%s", log_path.parent)
        else:
            QMessageBox.information(
                self,
                APP_NAME,
                f"Debug log location:\n{log_path}",
            )

    def preferencesbox(self) -> None:
        """Open the SHARPpy preferences dialog (palette + units).

        Mirrors the legacy ``Main.preferencesbox``: the dialog edits the shared
        :attr:`config` in place; on acceptance we apply the fork's complete
        style palette and broadcast :attr:`config_changed` so every open
        sounding window refreshes its profiles and palette.
        """
        try:
            config = self._config()
            dialog = _picker_api._build_preferences_dialog(config, parent=self)
        except Exception as exc:  # pragma: no cover - vendored dep always present
            QMessageBox.warning(self, APP_NAME, f"Preferences are unavailable:\n{exc}")
            return
        parcel_box = _add_default_parcel_tab(dialog, self._default_parcel())
        interface_boxes = _add_interface_tab(
            dialog, _read_interface_preferences(getattr(self, "_settings", None))
        )
        try:
            accepted = dialog.exec()
            parcel_key = (
                _normalize_default_parcel(parcel_box.currentData())
                if accepted and parcel_box is not None else None
            )
            interface_preferences = _read_interface_preferences(
                getattr(self, "_settings", None)
            )
            if accepted and interface_boxes is not None:
                interface_preferences = {
                    key: str(box.currentData())
                    for key, box in interface_boxes.items()
                }
        finally:
            # Schedule disposal only after the native modal loop has unwound.
            # Copy child values first: retheming/viewer refresh can process Qt
            # events, so child widgets must not be read after this point.
            try:
                dialog.deleteLater()
            except RuntimeError:
                pass
        if accepted:
            # PrefDialog applies its upstream palette on accept.  Normalize it
            # once through the fork's complete style map before persistence and
            # before the single live-window signal is emitted.
            _apply_selected_color_style(config)
            self._save_config_preferences(config)
            # Retheme the chrome to match the newly chosen canvas palette,
            # before the signal, so an open sounding window repaints its canvas
            # and its frame in the same event-loop turn instead of briefly
            # showing a light canvas inside dark chrome.
            #
            # Guarded and resolved via getattr: this is presentation only and
            # must never prevent a preference from being persisted or the
            # config_changed fan-out from running. ``preferencesbox`` is also
            # invoked unbound against a duck-typed owner, which need not supply
            # the chrome hook at all.
            if interface_boxes is not None:
                _save_interface_preferences(
                    getattr(self, "_settings", None), interface_preferences
                )
            reapply_chrome = getattr(self, "_apply_chrome_theme", None)
            if callable(reapply_chrome):
                try:
                    reapply_chrome(
                        config,
                        interface_preferences=interface_preferences,
                    )
                except Exception:
                    _LOGGER.exception("chrome_theme.preferences_reapply_failed")
            self.config_changed.emit(config)
            if parcel_key is not None:
                self._save_default_parcel(parcel_key)
                self._apply_default_parcel_to_viewers(parcel_key)

    def unit_preferencesbox(self, parent=None) -> None:
        """Open the compact display-units popup."""
        dialog = _UnitPreferencesDialog(self._unit_preferences(), parent=parent or self)
        try:
            prefs = dialog.preferences() if dialog.exec() else None
        finally:
            try:
                dialog.deleteLater()
            except RuntimeError:
                pass
        if prefs is not None:
            self._save_unit_preferences(prefs)
            config = self._config()
            _write_unit_preferences_to_config(config, prefs)
            self.config_changed.emit(config)

    def _apply_chrome_theme(
        self, config=None, *, interface_preferences=None
    ) -> None:
        """Re-apply the chrome theme paired with the current canvas palette.

        Reads the palette choice from ``config`` when supplied, so this runs
        after :func:`_apply_selected_color_style` has normalized it, and falls
        back to the persisted value otherwise.

        Applied on the ``QApplication``, so every open sounding window and all
        lazily-built panels pick it up without being enumerated here.
        """
        style = None
        if config is not None:
            style = _read_config_preferences(config).get("color_style")
        if style is None:
            style = _read_settings_preferences(getattr(self, "_settings", None)).get(
                "color_style"
            )
        interface = _normalize_interface_preferences(
            interface_preferences
            or _read_interface_preferences(getattr(self, "_settings", None))
        )
        try:
            apply_theme(
                QApplication.instance(),
                color_style=style,
                text_scale=interface["text_scale"],
                density=interface["density"],
            )
        except Exception:
            _LOGGER.exception("chrome_theme.reapply_failed")

    def focusPicker(self) -> None:  # noqa: N802 - matches SPCWindow's caller
        """Bring the picker back to the front (the ``W`` key target)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _config(self):
        """Build and cache the shared render config on first real use."""
        if self.config is None:
            saved_preferences = _read_settings_preferences(
                getattr(self, "_settings", None)
            )
            try:
                self.statusBar().showMessage("Loading analysis engine\u2026")
                QApplication.processEvents()
            except Exception:
                pass
            self.config = _picker_api._render().build_config(tempfile.gettempdir())
            _write_config_preferences(self.config, saved_preferences)
        return self.config

    def _unit_preferences(self):
        prefs = dict(UNIT_DEFAULTS)
        settings = getattr(self, "_settings", None)
        if settings is not None:
            for key in UNIT_DEFAULTS:
                value = settings.value("units/" + key, "", str)
                if value in UNIT_OPTIONS[key]:
                    prefs[key] = value
        if self.config is not None:
            prefs.update(self._unit_preferences_from_config(self.config))
        return _normalize_unit_preferences(prefs)

    def _interface_preferences(self):
        """Return validated chrome text-size and density preferences."""
        return _read_interface_preferences(getattr(self, "_settings", None))

    def _save_interface_preferences(self, preferences) -> None:
        _save_interface_preferences(
            getattr(self, "_settings", None), preferences
        )

    def _unit_preferences_from_config(self, config):
        prefs = {}
        for key in UNIT_DEFAULTS:
            value = _read_config_unit(config, key)
            if value is not None:
                prefs[key] = value
        return _normalize_unit_preferences(prefs)

    def _save_unit_preferences(self, preferences) -> None:
        _save_settings_preferences(
            getattr(self, "_settings", None),
            _normalize_unit_preferences(preferences),
        )

    def _save_config_preferences(self, config) -> None:
        _save_settings_preferences(
            getattr(self, "_settings", None),
            _read_config_preferences(config),
        )

    def _default_parcel(self) -> str:
        """Return the persistent parcel used for newly opened Skew-Ts."""
        settings = getattr(self, "_settings", None)
        if settings is None:
            return _DEFAULT_SKEWT_PARCEL
        return _normalize_default_parcel(
            settings.value("parcel/default_skewt", _DEFAULT_SKEWT_PARCEL, str)
        )

    def _save_default_parcel(self, parcel_key) -> None:
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.setValue(
                "parcel/default_skewt",
                _normalize_default_parcel(parcel_key),
            )
            settings.sync()

    def _model_prefetch_enabled(self) -> bool:
        settings = getattr(self, "_settings", None)
        return (
            False
            if settings is None
            else settings.value("model/prefetch_next_hour", False, bool)
        )

    def _save_model_prefetch(self, enabled) -> None:
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.setValue("model/prefetch_next_hour", bool(enabled))
            settings.sync()
        if not enabled:
            self._cancel_model_prefetch(wait=False)

    def _combine_soundings_enabled(self) -> bool:
        """Whether future opens should join the last visible analysis window."""
        action = getattr(self, "_combine_soundings_action", None)
        if action is not None:
            return action.isChecked()
        return bool(self._settings.value("viewer/combine_soundings", True, bool))

    def map_projection(self) -> str:
        """Return the chosen map view, ``"flat"`` or ``"curved"``."""
        return getattr(self, "_map_projection", "flat")

    def _map_widgets(self):
        """Yield every map widget that has been built so far.

        Panels are created lazily, so this reaches for the attributes rather
        than holding a registry that could fall out of step with them. Panel
        maps are deliberately absent: they stay flat by design, so projection
        and presentation appliers must not reach them (tools do, separately,
        through :meth:`_trigger_map_tool`).
        """
        for name in ("_map", "_model_map", "_era5_map", "_wrf_map"):
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "set_projection"):
                yield widget
        window = getattr(self, "_box_window", None)
        field_map = getattr(window, "_map", None) if window is not None else None
        if field_map is not None and hasattr(field_map, "set_projection"):
            yield field_map

    def _tool_widgets(self):
        """Yield every map the explicit tool choice applies to (T18.1)."""
        seen = set()
        for widget in self._map_widgets():
            seen.add(id(widget))
            yield widget
        view = getattr(self, "_panels_view", None)
        if view is not None:
            try:
                maps = view.maps()
            except (AttributeError, RuntimeError):
                maps = ()
            for widget in maps:
                if id(widget) in seen:
                    continue
                seen.add(id(widget))
                if hasattr(widget, "set_map_tool"):
                    yield widget

    def _on_projection_chosen(self, action) -> None:
        name = str(action.data() or "flat")
        if name not in MAP_PROJECTIONS:
            name = "flat"
        self._map_projection = name
        self._settings.setValue("maps/projection", name)
        self._settings.sync()
        self._apply_map_projection()
        _LOGGER.info("maps.projection projection=%s", name)

    def _save_combine_soundings(self, enabled: bool) -> None:
        self._settings.setValue("viewer/combine_soundings", bool(enabled))
        self._settings.sync()
        _LOGGER.info("viewer.combine_soundings enabled=%s", bool(enabled))

    def _set_combine_soundings_enabled(self, enabled: bool) -> None:
        """Synchronize the File action with source-tab multi-sounding controls."""

        enabled = bool(enabled)
        action = getattr(self, "_combine_soundings_action", None)
        if action is not None and action.isChecked() != enabled:
            action.blockSignals(True)
            action.setChecked(enabled)
            action.blockSignals(False)
        for checkbox in getattr(self, "_multi_sounding_checkboxes", ()):
            if checkbox.isChecked() == enabled:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)
        self._save_combine_soundings(enabled)

    def _make_multi_sounding_checkbox(self, source: str) -> QCheckBox:
        checkbox = QCheckBox("Add to active sounding window")
        checkbox.setChecked(self._combine_soundings_enabled())
        checkbox.setToolTip(
            f"Keep this enabled, change the {source} time or point, and fetch "
            "again to overlay multiple soundings in one analysis window."
        )
        checkbox.toggled.connect(self._set_combine_soundings_enabled)
        self._multi_sounding_checkboxes.append(checkbox)
        return checkbox

    def _apply_default_parcel_to_viewers(self, parcel_key) -> None:
        self._prune_closed_viewers()
        for viewer in list(getattr(self, "_viewers", [])):
            try:
                _apply_default_parcel_to_window(viewer, parcel_key)
            except RuntimeError:
                continue

    def _apply_unit_preferences_to_viewers(self, config) -> None:
        self._prune_closed_viewers()
        for viewer in list(getattr(self, "_viewers", [])):
            try:
                _apply_unit_preferences_to_window(viewer, config)
            except RuntimeError:
                continue

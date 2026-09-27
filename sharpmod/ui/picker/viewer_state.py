"""PickerViewer methods for PickerWindow."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from qtpy.QtCore import QDate
from qtpy.QtCore import QTimer
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication
from qtpy.QtWidgets import QMessageBox
from sharpmod.ui.features.gui_cache import parse_spatial_point
from sharpmod.ui.features.gui_common import APP_NAME
from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_maps import MAP_AREAS
import os
from sharpmod import gui_picker as _picker_api


class PickerViewerMixin:
    """Picker behavior grouped by responsibility."""

    def _on_model_fetch_ok(self, npz_path, label, run_time, fxx) -> None:
        worker = self.sender()
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None and worker is not None and point.worker is worker:
            return
        self._on_model_fetch_progress("rendering", self._model_progress_total)
        self.statusBar().showMessage(f"Rendering {label} F{int(fxx):03d}\u2026")
        QApplication.processEvents()
        try:
            R = _picker_api._render()
            prof_col, stn_id = R.decode(npz_path)
            title = f"{APP_NAME} \u2014 {label} {run_time:%Y-%m-%d %H}Z F{int(fxx):03d}"
            win = self._show_sounding(prof_col, stn_id, title=title)
            _picker_api._retain_model_data_until_close(win, npz_path, os.path.dirname(npz_path))
            self.statusBar().showMessage(
                f"Opened {label} {run_time:%Y-%m-%d %H}Z F{int(fxx):03d}"
            )
            _LOGGER.info(
                "model_fetch.displayed label=%s run=%s fxx=%03d viewer=%s",
                label,
                run_time,
                int(fxx),
                id(win),
            )
            if isinstance(worker, _picker_api._ModelFetchWorker):
                request = (
                    worker._model,
                    worker._lat,
                    worker._lon,
                    run_time,
                    int(fxx),
                    worker._member,
                )
                QTimer.singleShot(
                    0, lambda request=request: self._start_model_prefetch(*request)
                )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "model_fetch.display_failed label=%s run=%s fxx=%03d",
                label,
                run_time,
                int(fxx),
            )
            _picker_api._cleanup_model_data(npz_path, os.path.dirname(npz_path))
            QMessageBox.critical(
                self, APP_NAME, f"Fetched, but could not display:\n{exc}"
            )

    def _show_model_panels(self) -> None:
        """Bring the field-panels tab forward.

        A menu entry rather than a window: the panels are a source like the
        others, and the run they draw is chosen in their own rail.
        """
        self._select_tab("Field Panels")

    def _stored_panel_layout(self):
        """Return the remembered panel arrangement, unvalidated.

        Count/products degrade in the view; the preset and per-category
        memory degrade in ``gui_map_panels`` helpers, so a preference left
        by a different build lands on a working grid in one place instead
        of several that could disagree.
        """
        try:
            count = self._settings.value("panels/count", None)
            # The ``list`` hint matters: an INI list that has come down to a single
            # entry reads back as a bare string otherwise, and iterating that would
            # spread one product's letters across the panels.
            products = self._settings.value("panels/products", [], list)
            preset = self._settings.value("panels/preset", "")
            memory = self._settings.value("panels/category_memory", None)
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return None, (), "", {}
        if isinstance(memory, str):
            try:
                import json as _json

                memory = _json.loads(memory)
            except ValueError:
                memory = {}
        return (count, tuple(str(key) for key in (products or ())),
                str(preset or ""), memory if isinstance(memory, dict) else {})

    def _remember_panel_layout(self, *_args) -> None:
        """Persist the field-panels arrangement as the user changes it.

        Saved on change rather than at shutdown so the preference survives a
        crash or a kill, and because the two signals it is connected to are
        exactly the moments the stored value goes stale. Count, products,
        preset, and per-category memory persist; frames never do, so a
        relaunch re-requests rather than replaying pixels (T24.5).
        """
        view = getattr(self, "_panels_view", None)
        if view is None:
            return
        try:
            import json as _json

            self._settings.setValue("panels/count", int(view.panel_count()))
            self._settings.setValue("panels/products", list(view.panel_products()))
            preset_combo = getattr(self, "_panels_preset_combo", None)
            if preset_combo is not None:
                try:
                    self._settings.setValue(
                        "panels/preset", str(preset_combo.currentData() or ""))
                except (AttributeError, RuntimeError):
                    pass
            self._settings.setValue(
                "panels/category_memory",
                _json.dumps(view.category_memory(), sort_keys=True))
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("map_panels.layout_unsaved", exc_info=True)

    def selected_map_area(self) -> str:
        """Return the region the front map tab is showing, for a new view to adopt."""
        for attribute in ("_area_combo", "_model_area_combo"):
            combo = getattr(self, attribute, None)
            if combo is None:
                continue
            name = combo.currentText()
            if name in MAP_AREAS:
                return name
        return "United States (CONUS)"

    def _reuse_cache_entry(self, entry) -> None:
        """Open a portable cache item or re-extract from cached GRIB data."""
        self._ensure_model_cache()
        if entry.valid_sounding:
            soundings = self._model_disk_cache.valid_sounding_paths(entry.path)
            if soundings:
                sounding = soundings[0]
                try:
                    prof_col, stn_id = _picker_api._render().decode(str(sounding))
                    self._show_sounding(
                        prof_col,
                        stn_id,
                        title=f"{APP_NAME} — Cached {entry.model.upper()} sounding",
                    )
                    self.statusBar().showMessage(
                        f"Opened cached {entry.model.upper()} sounding offline",
                        5000,
                    )
                except Exception as exc:  # noqa: BLE001 - cache/render boundary
                    QMessageBox.critical(
                        self,
                        APP_NAME,
                        f"The cached sounding could not be opened:\n{exc}",
                    )
                return

        try:
            run_time = datetime.fromisoformat(entry.run.replace("Z", "+00:00"))
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=timezone.utc)
            else:
                run_time = run_time.astimezone(timezone.utc)
        except (AttributeError, TypeError, ValueError):
            QMessageBox.warning(
                self, APP_NAME, "This cache entry has no usable run time."
            )
            return
        point = parse_spatial_point(entry.spatial)
        if entry.model.casefold() == "era5":
            self._select_tab("Reanalysis (ERA5)")
            self._era5_date.setDate(QDate(run_time.year, run_time.month, run_time.day))
            hour_index = self._era5_hour.findData(run_time.hour)
            if hour_index >= 0:
                self._era5_hour.setCurrentIndex(hour_index)
            if point is not None:
                self._era5_lat.setValue(point[0])
                self._era5_lon.setValue(point[1])
            self._era5_fetch()
            return

        self._select_tab("Forecast Model")
        from sharpmod.tools import model_extract

        try:
            cfg = model_extract.get_config(entry.model)
        except Exception as exc:  # noqa: BLE001 - stale provider entry
            QMessageBox.warning(
                self,
                APP_NAME,
                f"The cached model adapter is no longer available:\n{exc}",
            )
            return
        preferred_area = {
            "Alaska": "Alaska",
            "Hawaii": "Hawaii",
            "Puerto Rico": "Puerto Rico",
            "CONUS": "United States (CONUS)",
        }.get(cfg.domain, "World")
        area_index = self._model_area_combo.findText(preferred_area)
        if area_index >= 0:
            self._model_area_combo.setCurrentIndex(area_index)
        model_index = self._model_combo.findData(cfg.key)
        if model_index < 0:
            world_index = self._model_area_combo.findText("World")
            if world_index >= 0:
                self._model_area_combo.setCurrentIndex(world_index)
            model_index = self._model_combo.findData(cfg.key)
        if model_index < 0:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"{cfg.label} is not selectable in the current provider catalog.",
            )
            return
        self._model_combo.setCurrentIndex(model_index)
        self._model_date.setDate(QDate(run_time.year, run_time.month, run_time.day))
        cycle_index = self._model_cycle.findData(run_time.hour)
        if cycle_index >= 0:
            self._model_cycle.setCurrentIndex(cycle_index)
        fxx_index = self._model_fxx_combo.findData(int(entry.fxx))
        if fxx_index >= 0:
            self._model_fxx_combo.setCurrentIndex(fxx_index)
        if entry.member and self._model_member.isEnabled():
            self._model_member.setText(entry.member)
        if point is not None:
            self._model_lat.setValue(point[0])
            self._model_lon.setValue(point[1])
        self._model_fetch(cache_entry=entry)

    def _select_tab(self, title: str) -> None:
        self._ensure_tab(title)
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index) == title:
                self._tabs.setCurrentIndex(index)
                return

    def _record_recent_destination(self, kind, target=None, **metadata) -> None:
        from sharpmod.state.recent_destinations import RecentDestinationStore

        try:
            RecentDestinationStore(self._settings).remember(kind, target, **metadata)
        except Exception:  # noqa: BLE001 - discovery metadata cannot fail a load/save
            _LOGGER.exception("recent_destination.save_failed kind=%s", kind)

    def _show_recent_destinations(self, *_args, query="", kind="all") -> None:
        self._recent_destinations_controller.open(query=query, kind=kind)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._select_tab("Open File")
                filename = os.path.basename(path).lower()
                if filename.startswith("wrfout") or filename.endswith((".nc", ".nc4")):
                    self._file_modes.setCurrentIndex(1)
                    self._wrf_path_edit.setText(path)
                    self._wrf_start_inspection()
                else:
                    self._file_modes.setCurrentIndex(0)
                    self._file_edit.setText(path)
                    self._open_file(path)
                break

    def _active_overlay_controller(self):
        """Return the overlay controller owned by the tab currently in front."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return None
        try:
            title = tabs.tabText(tabs.currentIndex())
        except (AttributeError, RuntimeError):
            return None
        attribute = _picker_api.TAB_OVERLAY_CONTROLLERS.get(title)
        if attribute is None:
            return None
        return getattr(self, attribute, None)

    def selected_overlay_product(self) -> str | None:
        """Return the map-overlay hazard the user has selected, if any.

        Read by the sounding viewer so a sounding opened while looking at, say,
        the wind probability keeps showing that hazard on its locator inset
        rather than reverting to the categorical outlook.

        The tab in front decides. Two tabs own an overlay, and ranking them in a
        fixed order answered with whichever came first in that order: a sounding
        fetched from the Forecast Model tab while showing the wind probability
        came back categorical whenever the Station Map tab happened to have its
        own overlay switched on. Tabs that host no overlay of their own fall back
        to a configured one, preferring an enabled overlay over a merely
        configured one. ``None`` leaves the choice to the default.
        """
        active = self._active_overlay_controller()
        if active is not None:
            try:
                return active.product()
            except (AttributeError, RuntimeError):
                pass

        controllers = [
            getattr(self, name, None) for name in _picker_api.TAB_OVERLAY_CONTROLLERS.values()
        ]
        controllers = [c for c in controllers if c is not None]
        for controller in controllers:
            try:
                if controller.is_enabled():
                    return controller.product()
            except (AttributeError, RuntimeError):
                continue
        for controller in controllers:
            try:
                return controller.product()
            except (AttributeError, RuntimeError):
                continue
        return None

    def selected_model_field(self):
        """Return the gridded field image the user is looking at, or ``None``.

        Read by the sounding viewer, so a profile pulled while the supercell
        composite is on the map arrives with that same field on its locator inset
        instead of a bare outline. Resolved tab-first for the reason
        :meth:`selected_overlay_product` is: the map in front is the one the
        reader was working from.
        """
        active = self._active_field_controller()
        if active is not None:
            try:
                raster = active.attached_raster()
            except (AttributeError, RuntimeError):
                raster = None
            if raster is not None:
                return raster
        for name in _picker_api.TAB_FIELD_CONTROLLERS.values():
            controller = getattr(self, name, None)
            if controller is None:
                continue
            try:
                raster = controller.attached_raster()
            except (AttributeError, RuntimeError):
                continue
            if raster is not None:
                return raster
        return None

    def _active_field_controller(self):
        """Return the field controller owned by the tab currently in front."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return None
        try:
            title = tabs.tabText(tabs.currentIndex())
        except (AttributeError, RuntimeError):
            return None
        attribute = _picker_api.TAB_FIELD_CONTROLLERS.get(title)
        if attribute is None:
            return None
        return getattr(self, attribute, None)

    def active_environment_context(self):
        """Return context for the map in front, then any loaded map context."""

        tabs = getattr(self, "_tabs", None)
        if tabs is not None:
            try:
                title = tabs.tabText(tabs.currentIndex())
            except (AttributeError, RuntimeError):
                title = ""
            attribute = _picker_api.TAB_CONTEXT_CONTROLLERS.get(title)
            controller = getattr(self, attribute, None) if attribute else None
            if controller is not None:
                return controller
        for attribute in ("_model_context", "_map_context"):
            controller = getattr(self, attribute, None)
            if controller is None:
                continue
            if (
                controller.satellite_overlay() is not None
                or controller.surface_observations() is not None
            ):
                return controller
        return None

    def _show_sounding(self, prof_col, stn_id, title=None):
        self._prune_closed_viewers()
        if self._combine_soundings_enabled() and self._viewers:
            win = self._viewers[-1]
            watch_profiles = getattr(self, "_watch_loaded_profile_viewer", None)
            if callable(watch_profiles):
                watch_profiles(win)
            _picker_api._fill_profile_metadata(prof_col, stn_id)
            win.addProfileCollection(
                prof_col,
                focus=True,
                check_integrity=False,
            )
            add_locator = getattr(win, "_sharpmod_add_locator_collection", None)
            if callable(add_locator):
                add_locator(prof_col)
            _picker_api._start_locator_overlay_fetch(
                win,
                prof_col,
                product=_picker_api._overlay_product_for(self),
                controller=self,
                spec=_picker_api._locator_spec_for(self),
            )
            count = len(getattr(win.spc_widget, "prof_collections", []))
            win.setWindowTitle(
                f"{APP_NAME} — {count} Sounding{'s' if count != 1 else ''}"
            )
            win.showNormal()
            win.raise_()
            win.activateWindow()
            refresh_profiles = getattr(self, "_refresh_loaded_profile_markers", None)
            if callable(refresh_profiles):
                refresh_profiles()
            _LOGGER.info(
                "viewer.profile_added viewer=%s title=%s soundings=%d",
                id(win),
                title or stn_id or "Sounding",
                count,
            )
            return win

        # Compose the real, interactive SPCWindow with this picker as its Qt
        # parent/controller (so the W key refocuses the picker and Preferences
        # routes here). The window shows itself; we retain a reference.
        win = _picker_api.compose_interactive(self._config(), prof_col, self, stn_id=stn_id)
        if title:
            win.setWindowTitle(title)
        self._viewers.append(win)
        viewer_id = id(win)
        viewer_title = title or stn_id or "Sounding"
        win.destroyed.connect(
            lambda *_args, viewer_id=viewer_id, viewer_title=viewer_title: _LOGGER.info(
                "viewer.closed viewer=%s title=%s", viewer_id, viewer_title
            )
        )
        watch_profiles = getattr(self, "_watch_loaded_profile_viewer", None)
        if callable(watch_profiles):
            watch_profiles(win)
        refresh_profiles = getattr(self, "_refresh_loaded_profile_markers", None)
        if callable(refresh_profiles):
            refresh_profiles()
        _LOGGER.info(
            "viewer.opened viewer=%s title=%s active_viewers=%d",
            viewer_id,
            viewer_title,
            len(self._viewers),
        )
        return win

    def _prune_closed_viewers(self) -> None:
        """Drop references to windows the user has closed."""
        before = len(self._viewers)
        alive = []
        for w in self._viewers:
            try:
                if w.isVisible():
                    alive.append(w)
            except RuntimeError:
                continue  # already deleted by Qt
        self._viewers = alive
        _LOGGER.debug("viewer.prune before=%d after=%d", before, len(self._viewers))

    def _viewer_closed_refresh(self) -> None:
        """Remove a destroyed viewer and its profile markers after Qt settles."""

        self._prune_closed_viewers()
        self._refresh_loaded_profile_markers()

    def _watch_loaded_profile_viewer(self, viewer) -> None:
        """Keep loaded-profile map markers synchronized with ``viewer``.

        The vendored SPC widget changes ``pc_idx`` in several paths (menu
        focus, Space, time advance, and removal) but exposes no focus-changed
        signal.  Wrapping its common ``updateProfs`` convergence point keeps
        the map's focused ring truthful without coupling T22 to each gesture.
        """

        try:
            if bool(getattr(viewer, "_sharpmod_profile_map_watch", False)):
                return
            viewer._sharpmod_profile_map_watch = True
            widget = viewer.spc_widget
            original_update = widget.updateProfs

            def update_profiles_and_map(*args, **kwargs):
                result = original_update(*args, **kwargs)
                QTimer.singleShot(0, self._refresh_loaded_profile_markers)
                return result

            widget.updateProfs = update_profiles_and_map
            viewer.destroyed.connect(
                lambda *_args: QTimer.singleShot(0, self._viewer_closed_refresh)
            )
        except (AttributeError, RuntimeError):
            return

    @staticmethod
    def _profile_collection_meta(collection, key: str, default=None):
        try:
            value = collection.getMeta(key)
        except Exception:  # noqa: BLE001 - heterogeneous decoder metadata
            return default
        return default if value is None else value

    def _loaded_profile_marker_payload(self) -> tuple[dict, ...]:
        """Flatten every open viewer into map-safe loaded-profile markers."""

        resolved: dict[int, dict] = {}
        for viewer in tuple(getattr(self, "_viewers", ())):
            try:
                widget = viewer.spc_widget
                collections = tuple(widget.prof_collections)
                active_index = int(getattr(widget, "pc_idx", 0) or 0)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
            for index, collection in enumerate(collections):
                identity = id(collection)
                if identity in resolved:
                    resolved[identity]["active"] = bool(
                        resolved[identity]["active"] or index == active_index
                    )
                    continue
                latitude = self._profile_collection_meta(collection, "lat")
                longitude = self._profile_collection_meta(collection, "lon")
                try:
                    latitude = float(latitude)
                    longitude = float(longitude)
                except (TypeError, ValueError, OverflowError):
                    # A decoder that supplied no location cannot honestly be
                    # placed on the map. It remains loaded in the viewer.
                    continue
                observed = self._profile_collection_meta(
                    collection, "observed", None
                )
                model = str(
                    self._profile_collection_meta(collection, "model", "") or ""
                ).strip()
                if observed is True:
                    kind = "observed"
                elif observed is False or model:
                    kind = "model"
                else:
                    kind = "imported"
                location = str(
                    self._profile_collection_meta(collection, "loc", "") or ""
                ).strip()
                member = str(
                    self._profile_collection_meta(collection, "member", "") or ""
                ).strip()
                label_parts = [part for part in (location, model, member) if part]
                label = " · ".join(dict.fromkeys(label_parts)) or "Loaded profile"
                source = str(
                    self._profile_collection_meta(collection, "source", "") or ""
                ).strip()
                if not source:
                    source = model or (
                        "Observed sounding" if kind == "observed" else "Imported file"
                    )
                try:
                    valid_time = collection.getCurrentDate()
                except Exception:  # noqa: BLE001 - optional profile timestamp
                    valid_time = None
                resolved[identity] = {
                    "id": f"profile:{identity}",
                    "label": label,
                    "lat": latitude,
                    "lon": longitude,
                    "kind": kind,
                    "active": index == active_index,
                    "valid_time": valid_time,
                    "source": source,
                }
        return tuple(resolved.values())

    def _loaded_profile_maps(self) -> tuple:
        """Return every currently constructed picker map exactly once."""

        maps = []
        seen = set()
        for attribute in ("_map", "_model_map", "_era5_map", "_wrf_map"):
            widget = getattr(self, attribute, None)
            if widget is not None and id(widget) not in seen:
                seen.add(id(widget))
                maps.append(widget)
        panels = getattr(self, "_panels_view", None)
        if panels is not None:
            try:
                panel_maps = panels.maps()
            except (AttributeError, RuntimeError):
                panel_maps = ()
            for widget in panel_maps:
                if id(widget) not in seen:
                    seen.add(id(widget))
                    maps.append(widget)
        return tuple(maps)

    def _refresh_loaded_profile_markers(self) -> None:
        """Mirror open sounding locations onto every picker map (T22.4)."""

        points = self._loaded_profile_marker_payload()
        for widget in self._loaded_profile_maps():
            try:
                widget.set_loaded_profile_points(points)
            except (AttributeError, RuntimeError):
                continue

    def _restore_state(self) -> None:
        last = self._settings.value("last_station", "", str)
        self._restored_last_station = last
        # Restore the visible map immediately, but do not start a network probe
        # before first paint. Selecting/changing a station still probes as usual.
        if last and self._station(last) is not None:
            self._map.set_selected(last)
            self._map.center_on(last)
            self._map_on_select(last, check_availability=False)

    def _restore_station_list_selection(self) -> None:
        last = getattr(self, "_restored_last_station", "")
        if not last or not hasattr(self, "_station_list"):
            return
        for index in range(self._station_list.topLevelItemCount()):
            item = self._station_list.topLevelItem(index)
            if item.data(0, Qt.UserRole) == last:
                self._station_list.setCurrentItem(item)
                self._station_list.scrollToItem(item)
                return

    def _station(self, sid):
        return next((s for s in self._all_stations if s["id"] == sid), None)

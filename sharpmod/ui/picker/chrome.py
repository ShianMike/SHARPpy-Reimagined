"""Picker source tabs, top bar, map actions, and UTC clock."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.ui.styles.theme import (
    OBJ_HINT, OBJ_TOP_BAR, OBJ_TOP_BAR_END, OBJ_TOP_BAR_LABEL,
    OBJ_TOP_BAR_MENU, OBJ_TOP_BAR_SOURCE, OBJ_UTC_CLOCK, SPACE,
)
from sharpmod.ui.picker.layout import (
    SelectionPane as _SelectionPane, SUMMARY_JOIN as _SUMMARY_JOIN,
    TOP_SUMMARY_CHARS as _TOP_SUMMARY_CHARS,
    TOP_SUMMARY_HEADROOM as _TOP_SUMMARY_HEADROOM,
    TOP_SUMMARY_MIN_CHARS as _TOP_SUMMARY_MIN_CHARS,
    UTC_CLOCK_SAMPLE as _UTC_CLOCK_SAMPLE,
    fit_summary_groups as _fit_summary_groups,
)


class PickerChromeMixin:
    """Build source navigation and keep top-level controls in sync."""

    @staticmethod
    def _lazy_tab_placeholder(title: str) -> QWidget:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        label = QLabel(f"Preparing {title}…")
        label.setAlignment(Qt.AlignCenter)
        label.setObjectName(OBJ_HINT)
        layout.addWidget(label, 1)
        return placeholder

    def _ensure_tab(self, title: str) -> QWidget | None:
        """Materialize one expensive tab on first use, preserving its index."""
        builder = self._lazy_tab_builders.pop(title, None)
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index) == title:
                break
        else:
            return None
        if builder is None:
            return self._tabs.widget(index)

        previous_title = self._tabs.tabText(self._tabs.currentIndex())
        placeholder = self._tabs.widget(index)
        widget = builder()
        self._tabs.blockSignals(True)
        try:
            self._tabs.removeTab(index)
            self._tabs.insertTab(index, widget, title)
            for current in range(self._tabs.count()):
                if self._tabs.tabText(current) == previous_title:
                    self._tabs.setCurrentIndex(current)
                    break
        finally:
            self._tabs.blockSignals(False)
        placeholder.deleteLater()

        if title == "Station List":
            self._restore_station_list_selection()
            self._refresh_station_catalog(self._selected_when())
        elif title == "Open File":
            self._file_tab = widget
            self._load_recent_files()
        self._refresh_location_markers()
        return widget

    def _on_tab_changed(self, index: int) -> None:
        if index < 0:
            return
        title = self._tabs.tabText(index)
        self._ensure_tab(title)
        self._sync_tab_status()
        # The summary and the Hide Controls item belong to whichever source is on
        # display, so they have to follow the tab rather than stay on the pane that
        # happened to be built first.
        self._sync_top_bar_context()
        self._refresh_navigation_actions()
        # A freshly built tab starts on Select; an existing one keeps the
        # persisted tool choice so switching tabs cannot strand a mode.
        self._apply_stored_map_tool(title)

    def _apply_stored_map_tool(self, title: str) -> None:
        """Apply the persisted tool to one tab's map, tolerating laziness."""
        wanted = getattr(self, "_map_tool_name", "select")
        candidates = {
            "Station Map": ("_map",),
            "Forecast Model": ("_model_map",),
            "Reanalysis (ERA5)": ("_era5_map",),
            "Open File": ("_wrf_map",),
        }.get(str(title), ())
        for name in candidates:
            widget = getattr(self, name, None)
            if widget is None or not hasattr(widget, "set_map_tool"):
                continue
            try:
                allowed = (widget._allowed_tools()
                           if hasattr(widget, "_allowed_tools") else ("select",))
                widget.set_map_tool(wanted if wanted in allowed else "select")
            except (AttributeError, RuntimeError):
                pass
        if str(title) == "Field Panels":
            view = getattr(self, "_panels_view", None)
            if view is not None and hasattr(view, "set_map_tool"):
                try:
                    view.set_map_tool(wanted)
                except (AttributeError, RuntimeError):
                    pass
        self._refresh_tool_actions()

    def _sync_tab_status(self, *_args) -> None:
        if not hasattr(self, "_tabs") or self.statusBar() is None:
            return
        tab = self._tabs.tabText(self._tabs.currentIndex())
        if tab == "Forecast Model":
            self.statusBar().showMessage(
                "Ready \u2014 pick a point, model, run, and forecast hour"
            )
        elif tab == "Field Panels":
            self.statusBar().showMessage(
                "Ready \u2014 compare HRRR fields, then click a point for a sounding"
            )
        elif tab == "Reanalysis (ERA5)":
            self.statusBar().showMessage(
                "Ready \u2014 pick a global point and ERA5 analysis hour"
            )
        elif tab == "Open File":
            self.statusBar().showMessage(
                "Ready \u2014 open a sounding or extract raw WRF output"
            )
        else:
            self.statusBar().showMessage("Ready \u2014 pick a station and press Fetch")

    def _install_source_picker(self) -> None:
        """Place the mutually-exclusive loading source beside the menus."""
        self._source_picker = QFrame(self.menuBar())
        self._source_picker.setObjectName(OBJ_TOP_BAR)
        row = QHBoxLayout(self._source_picker)
        # The trailing margin is `md` rather than `sm` because the frame's right
        # edge now carries the hairline dividing this cluster from the menus, and
        # a divider needs matched air on both sides -- the menu items supply
        # their own `md` on the far side.
        #
        # No vertical margins: the frame's own height floor (QFrame#topBar) sets
        # how tall this cluster is, and margins on top of it would only make the
        # cluster -- rather than the menu titles it sits beside -- the thing that
        # decides how tall the whole strip is. A menu bar sizes itself to fit its
        # corner widgets, so that is a real consequence rather than a detail.
        #
        # Height is deliberately *not* taken from QMenuBar padding, which would
        # raise every menu bar in the application, including the sounding window's,
        # whose height the canvas-fit calculation subtracts to size a render the PNG
        # CLI must reproduce byte-for-byte.
        row.setContentsMargins(SPACE["xs"], 0, SPACE["md"], 0)
        row.setSpacing(SPACE["sm"])

        self._source_picker_label = QLabel("Load From", self._source_picker)
        # An eyebrow labelling the dropdown, not a resolved value. OBJ_EMPHASIS
        # is semibold `text_primary` at body size, which put this label at the
        # same weight as both the control it names and the menu titles beside it.
        self._source_picker_label.setObjectName(OBJ_TOP_BAR_LABEL)
        self._source_picker_label.setBuddy(self._tabs.navigation_widget())
        # Centred, not filled. A box layout stretches its children to the layout
        # height by default, which would grow the dropdown to the frame's floor and
        # undo the work of matching it to a menu title's height.
        row.addWidget(self._source_picker_label, 0, Qt.AlignVCenter)
        self._tabs.navigation_widget().setObjectName(OBJ_TOP_BAR_SOURCE)
        # The popup is named separately because it is a top-level window: a
        # descendant selector through the combo does not reach it. See
        # QAbstractItemView#topBarMenu, which gives it a menu's metrics so opening
        # this control looks like opening File or View beside it.
        self._tabs.navigation_widget().view().setObjectName(OBJ_TOP_BAR_MENU)
        row.addWidget(self._tabs.navigation_widget(), 0, Qt.AlignVCenter)
        self.menuBar().setCornerWidget(self._source_picker, Qt.TopLeftCorner)

    def _install_utc_clock(self) -> None:
        """Show the current UTC time in the menu bar's top-right corner.

        Every run, cycle, and valid time in this application is UTC, and the
        operating system clock is not, so the conversion was being done in the
        user's head on every selection. The date already belongs to the active
        selection and map header, so the live clock shows only the current UTC
        time; its tooltip retains the full date and seconds.

        Monospaced, so the width does not twitch as the digits change.
        """
        # Built with a full-width sample rather than an empty string. A menu bar
        # sizes its corner widget from the size hint the widget had when it was
        # attached, and this label's real text only arrives from the timer after
        # that -- so an empty start left it too narrow forever. Because the text
        # is right-aligned, the overflow was clipped on its *left* edge, which
        # rendered "UTC" as "JTC".
        self._utc_clock = QLabel(self._format_utc_clock(_UTC_CLOCK_SAMPLE))
        # The object name is what actually delivers the monospaced family: a
        # style-sheet `font-family` beats `setFont`, so the base chrome rule was
        # silently putting the proportional UI face back and the "does not
        # twitch" promise above was not being kept. The `setFont` call remains
        # for hosts that never apply the style sheet at all.
        #
        # OBJ_UTC_CLOCK carries that family plus the secondary colour. It replaces
        # OBJ_NUMERIC, which delivered only the family and left the clock at full
        # `text_primary` weight -- as loud as the source control opposite.
        self._utc_clock.setObjectName(OBJ_UTC_CLOCK)
        self._utc_clock.setFont(mono_font("caption"))
        self._utc_clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Pin the width the sample needs. The format is fixed and the font is
        # monospaced, so this measurement holds for every future value. The label
        # carries no border or padding of its own -- both belong to the frame below
        # -- so nothing the style sheet adds can outgrow this measurement and get
        # clipped off the label's left edge.
        self._utc_clock.setMinimumWidth(
            self._utc_clock.sizeHint().width() + SPACE["xs"]
        )

        # Wrapped in a frame that mirrors the source cluster's, with the same
        # vertical margins, so the hairline dividers at the two ends of the bar are
        # the same length by construction. Carrying the divider on the label
        # instead meant tuning a height token until the two happened to agree, and
        # that height then became the tallest thing in the bar and set how tall the
        # whole strip was.
        self._utc_clock_frame = QFrame(self.menuBar())
        self._utc_clock_frame.setObjectName(OBJ_TOP_BAR_END)
        clock_row = QHBoxLayout(self._utc_clock_frame)
        clock_row.setContentsMargins(SPACE["md"], 0, SPACE["md"], 0)
        clock_row.setSpacing(0)

        # The active source's selection context, immediately left of the clock and
        # divided from it by the same middle dot used throughout compact metadata.
        # It used to sit under the rail toggle inside the pane,
        # where it took a full-width row of the window for a single line that reads
        # as chrome rather than as content.
        #
        # Capped and elided rather than allowed to size itself: the text is
        # data-dependent, and ResponsivePickerHeader budgets this whole frame
        # against the menu row, so an unbounded label here would push the bar into
        # its stacked layout as soon as a long place name arrived.
        self._top_summary = QLabel("", self._utc_clock_frame)
        self._top_summary.setObjectName(OBJ_UTC_CLOCK)
        self._top_summary.setFont(mono_font("caption"))
        self._top_summary.setTextFormat(Qt.PlainText)
        self._top_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._top_summary_full = ""
        self._top_summary_width = 0
        self._top_summary.setFixedWidth(0)
        self._top_summary.hide()
        self._top_summary_divider = QLabel(" \u00b7 ", self._utc_clock_frame)
        self._top_summary_divider.setObjectName(OBJ_UTC_CLOCK)
        self._top_summary_divider.setFont(mono_font("caption"))
        self._top_summary_divider.setAlignment(Qt.AlignCenter)
        self._top_summary_divider.hide()
        clock_row.addWidget(self._top_summary, 0, Qt.AlignVCenter)
        clock_row.addWidget(self._top_summary_divider, 0, Qt.AlignVCenter)

        clock_row.addWidget(self._utc_clock, 0, Qt.AlignVCenter)
        self.menuBar().setCornerWidget(self._utc_clock_frame, Qt.TopRightCorner)

        self._utc_timer = QTimer(self)
        self._utc_timer.setInterval(1000)
        self._utc_timer.timeout.connect(self._update_utc_clock)
        self._utc_timer.start()
        self._update_utc_clock()

    @staticmethod
    def _format_utc_clock(now) -> str:
        """Return the clock's text. Shared so the measured sample cannot drift."""
        return f"{now:%H:%M} UTC"

    @staticmethod
    def _compact_top_summary(text: str) -> str:
        """Remove forecast times already shown by the map's time header.

        The full selection remains in the tooltip and accessible description.
        This is only the menu-bar rendering. It keeps the model, point, and
        member while the map owns run, lead, and valid time.
        """
        text = str(text or "").strip()
        groups = [group for group in text.split(_SUMMARY_JOIN) if group]
        initialization = next(
            (index for index, group in enumerate(groups)
             if group.startswith("Initialization ")),
            None,
        )
        lead_index = next(
            (index for index, group in enumerate(groups)
             if group.startswith("Lead ")),
            None,
        )
        valid_index = next(
            (index for index, group in enumerate(groups)
             if group.startswith("Valid ")),
            None,
        )
        if None in (initialization, lead_index, valid_index):
            visible = [
                group for group in groups
                if not group.startswith((
                    "Requested observation ",
                    "Analysis / valid ",
                    "Valid ",
                ))
            ]
            return _SUMMARY_JOIN.join(visible)

        run_match = re.fullmatch(
            r"Initialization (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC",
            groups[initialization],
        )
        lead_match = re.fullmatch(r"Lead \+(-?\d+) h", groups[lead_index])
        valid_match = re.fullmatch(
            r"Valid (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC",
            groups[valid_index],
        )
        if not (run_match and lead_match and valid_match):
            return text
        try:
            run = datetime.strptime(run_match.group(1), "%Y-%m-%d %H:%M")
            lead = int(lead_match.group(1))
            valid = datetime.strptime(valid_match.group(1), "%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OverflowError):
            return text
        if valid != run + timedelta(hours=lead):
            return text

        redundant = {initialization, lead_index, valid_index}
        return _SUMMARY_JOIN.join(
            group for index, group in enumerate(groups)
            if index not in redundant
        )

    def _active_selection_pane(self):
        """Return the ``SelectionPane`` of the source currently on display."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return None
        current = tabs.currentWidget()
        if current is None:
            return None
        if isinstance(current, _SelectionPane):
            return current
        return current.findChild(_SelectionPane)

    def _active_map_widget(self):
        """Return the map widget of the source currently on display (T17.2).

        The View-menu navigation actions route through this so they always
        describe the map being looked at. Tabs built lazily may not exist
        yet; maps that were never built are skipped rather than constructed
        as a side effect of navigation. A maximised field panel answers for
        Field Panels so lock/recent/copy/navigation describe the map being
        looked at rather than a hidden sibling.
        """
        tabs = getattr(self, "_tabs", None)
        title = ""
        if tabs is not None:
            try:
                title = str(tabs.tabText(tabs.currentIndex()))
            except (AttributeError, RuntimeError, TypeError):
                title = ""
        candidates = []
        if title == "Reanalysis (ERA5)":
            candidates = ["_era5_map", "_model_map", "_map"]
        elif title == "Field Panels":
            view = getattr(self, "_panels_view", None)
            if view is not None:
                try:
                    maximised = view.maximised_panel()
                except (AttributeError, RuntimeError):
                    maximised = None
                if maximised is not None:
                    try:
                        widget = view._panels[maximised].map
                    except (AttributeError, IndexError, RuntimeError,
                            TypeError):
                        widget = None
                    if widget is not None and hasattr(
                            widget, "view_bounds"):
                        return widget
                try:
                    index = int(getattr(view, "_last_clicked", 0) or 0)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    index = 0
                try:
                    panels = list(getattr(view, "_panels", ()))
                except (AttributeError, RuntimeError, TypeError):
                    panels = []
                ordered = []
                if 0 <= index < len(panels):
                    ordered.append(panels[index])
                ordered.extend(
                    panel for position, panel in enumerate(panels)
                    if position != index)
                for panel in ordered:
                    widget = getattr(panel, "map", None)
                    if widget is not None and hasattr(widget, "view_bounds"):
                        try:
                            if not widget.isVisible():
                                continue
                        except RuntimeError:
                            continue
                        return widget
            candidates = ["_model_map", "_map"]
        elif title == "Forecast Model":
            candidates = ["_model_map", "_map"]
        elif "WRF" in title or "Open File" in title:
            candidates = ["_wrf_map", "_model_map", "_map"]
        else:
            candidates = ["_map", "_model_map", "_era5_map", "_wrf_map"]
        for name in candidates:
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "view_bounds"):
                try:
                    if not widget.isVisible():
                        continue
                except RuntimeError:
                    continue
                return widget
        for name in ("_map", "_model_map", "_era5_map", "_wrf_map"):
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "view_bounds"):
                return widget
        return None

    def _show_map_recent_exports(self) -> None:
        from sharpmod.ui.features.gui_export import RecentExportsDialog

        dialog = RecentExportsDialog(self._settings, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _export_active_map_figure(self) -> None:
        from sharpmod.ui.features.export_presentation import ExportIdentity
        from sharpmod.ui.maps.export import capture_map, export_map_figure

        title = self._tabs.tabText(self._tabs.currentIndex())
        if title == "Field Panels":
            view = getattr(self, "_panels_view", None)
            if view is None or view.maximised_panel() is None:
                self._export_panel_map_figure()
                return
            title = view._panels[view.maximised_panel()].title.text()
        widget = self._active_map_widget()
        if widget is None or not widget.isVisible():
            QMessageBox.warning(self, "Export map", "Open a map source before exporting.")
            return
        try:
            frame = capture_map(widget, title=title)
            identity = ExportIdentity(
                location=frame.selection or title,
                source=title, initialization=frame.run,
                valid_start=frame.requested_time,
            )
            export_map_figure(
                self, (frame,), settings=self._settings,
                namespace="map-standalone", identity=identity,
                kind="map", title=f"{title} · captured analysis",
                status=lambda message: self.statusBar().showMessage(message, 8000),
            )
        except Exception as exc:  # noqa: BLE001 - export UI boundary
            QMessageBox.warning(self, "Export map", f"Could not capture map: {exc}")

    def _export_panel_map_figure(self) -> None:
        from sharpmod.ui.features.export_presentation import ExportIdentity
        from sharpmod.ui.maps.export import capture_map, export_map_figure

        view = getattr(self, "_panels_view", None)
        if view is None or not view.isVisible():
            QMessageBox.warning(
                self, "Export field panels",
                "Open Field Panels to capture the visible two/four-panel analysis.")
            return
        if view.maximised_panel() is not None:
            QMessageBox.information(
                self, "Export field panels",
                "Restore the two/four-panel layout before exporting every panel. "
                "Active Map Figure exports the maximised panel alone.")
            return
        try:
            frames = tuple(capture_map(panel.map, title=panel.title.text())
                           for panel in view._panels[:view.panel_count()])
            first = frames[0]
            identity = ExportIdentity(
                location=first.selection or "field-panels", source="HRRR",
                initialization=first.run, valid_start=first.requested_time,
            )
            export_map_figure(
                self, frames, settings=self._settings,
                namespace="map-panels", identity=identity,
                kind=f"map-{len(frames)}panel",
                title=f"HRRR · {len(frames)}-panel field analysis",
                status=lambda message: self.statusBar().showMessage(message, 8000),
            )
        except Exception as exc:  # noqa: BLE001 - export UI boundary
            QMessageBox.warning(self, "Export field panels",
                                f"Could not capture field panels: {exc}")

    def _trigger_map_tool(self, tool: str) -> None:
        """Apply one View-menu tool choice to every built map (T18.1)."""
        from sharpmod.maps.map_field_inspection import normalise_tool

        wanted = normalise_tool(tool, allow_box=True)
        self._map_tool_name = wanted
        try:
            self._settings.setValue("maps/tool", wanted)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("maps.tool_unsaved", exc_info=True)
        for widget in self._tool_widgets():
            try:
                allowed = (widget._allowed_tools()
                           if hasattr(widget, "_allowed_tools") else ("select",))
                widget.set_map_tool(wanted if wanted in allowed else "select")
            except (AttributeError, RuntimeError):
                continue
        action = getattr(self, "_tool_actions", {}).get(wanted)
        if action is not None and not action.isChecked():
            action.blockSignals(True)
            try:
                action.setChecked(True)
            finally:
                action.blockSignals(False)

    def _trigger_map_lock(self, locked: bool) -> None:
        """Lock or unlock the active map's sounding point (T18.1, T24.3)."""
        tabs = getattr(self, "_tabs", None)
        try:
            title = str(tabs.tabText(tabs.currentIndex())) if tabs is not None else ""
        except (AttributeError, RuntimeError, TypeError):
            title = ""
        if title == "Field Panels":
            view = getattr(self, "_panels_view", None)
            if view is not None and hasattr(view, "set_point_locked"):
                try:
                    view.set_point_locked(bool(locked))
                except (AttributeError, RuntimeError):
                    pass
                self._panels_describe_point()
                self._refresh_tool_actions()
                return
        widget = self._active_map_widget()
        if widget is None or not hasattr(widget, "set_point_locked"):
            self.statusBar().showMessage("No map is available to lock.", 4000)
            return
        try:
            widget.set_point_locked(bool(locked))
        except (AttributeError, RuntimeError):
            return
        self._refresh_tool_actions()

    def _trigger_map_recent_point(self) -> None:
        """Restore the active map's reversible previous point (T18.1, T24.3)."""
        tabs = getattr(self, "_tabs", None)
        try:
            title = str(tabs.tabText(tabs.currentIndex())) if tabs is not None else ""
        except (AttributeError, RuntimeError, TypeError):
            title = ""
        if title == "Field Panels":
            try:
                self._panels_restore_recent_point()
            except (AttributeError, RuntimeError):
                self.statusBar().showMessage("No previous sounding point.", 4000)
                return
            self._refresh_tool_actions()
            return
        widget = self._active_map_widget()
        if widget is None or not hasattr(widget, "restore_recent_point"):
            self.statusBar().showMessage("No map is available.", 4000)
            return
        try:
            restored = bool(widget.restore_recent_point())
        except (AttributeError, RuntimeError):
            return
        if not restored:
            self.statusBar().showMessage("No previous sounding point.", 4000)
            return
        self._sync_point_spins_from_map(widget)
        self._refresh_tool_actions()

    def _sync_point_spins_from_map(self, widget) -> None:
        """Reflect a map's point back into its tab's spin boxes (T18.1)."""
        try:
            lat, lon = widget.context_point()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        if widget is getattr(self, "_model_map", None):
            self._model_syncing_point = True
            try:
                self._model_lat.setValue(float(lat))
                self._model_lon.setValue(float(lon))
            finally:
                self._model_syncing_point = False
            self._model_update_fetch_state()
        elif widget is getattr(self, "_era5_map", None):
            self._era5_syncing_point = True
            try:
                self._era5_lat.setValue(float(lat))
                self._era5_lon.setValue(float(lon))
            finally:
                self._era5_syncing_point = False
            self._era5_update_state()
        elif widget is getattr(self, "_wrf_map", None):
            self._wrf_syncing_point = True
            try:
                self._wrf_lat.setValue(float(lat))
                self._wrf_lon.setValue(float(lon))
            finally:
                self._wrf_syncing_point = False
            self._wrf_update_fetch_state()
        else:
            view = getattr(self, "_panels_view", None)
            panels = list(getattr(view, "_panels", ())) if view is not None else []
            if any(widget is getattr(panel, "map", None) for panel in panels):
                self._panels_syncing_point = True
                try:
                    self._panels_lat.setValue(float(lat))
                    self._panels_lon.setValue(float(lon))
                finally:
                    self._panels_syncing_point = False
                self._panels_describe_point()

    def _trigger_map_copy_coordinates(self) -> None:
        """Copy the active map's pinned coordinates (T18.3)."""
        widget = self._active_map_widget()
        if widget is None or not hasattr(widget, "copy_inspection_coordinates"):
            self.statusBar().showMessage("No map is available.", 4000)
            return
        try:
            text = str(widget.copy_inspection_coordinates())
        except (AttributeError, RuntimeError):
            return
        try:
            from qtpy.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
        except Exception:  # noqa: BLE001 - clipboard is never critical
            pass
        self.statusBar().showMessage(text, 6000)

    def _trigger_map_copy_value(self) -> None:
        """Copy the active map's pinned value with units (T18.3/4)."""
        widget = self._active_map_widget()
        if widget is None or not hasattr(widget, "copy_inspection_value"):
            self.statusBar().showMessage("No map is available.", 4000)
            return
        try:
            text = str(widget.copy_inspection_value())
        except (AttributeError, RuntimeError):
            return
        try:
            from qtpy.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
        except Exception:  # noqa: BLE001 - clipboard is never critical
            pass
        self.statusBar().showMessage(text, 6000)

    def _trigger_map_save_point(self) -> None:
        """Save the active map's pinned point as a named location (T18.3)."""
        from qtpy.QtWidgets import QInputDialog

        widget = self._active_map_widget()
        if widget is None or not hasattr(widget, "inspection_save_payload"):
            self.statusBar().showMessage("No map is available.", 4000)
            return
        try:
            snapshot = widget.inspection_snapshot()
        except (AttributeError, RuntimeError):
            snapshot = None
        if snapshot is None:
            self.statusBar().showMessage(
                "Pin an inspection card first, then save it.", 6000)
            return
        name, accepted = QInputDialog.getText(
            self, "Save Pinned Point", "Location name:")
        if not accepted:
            return
        try:
            payload = widget.inspection_save_payload(name)
        except ValueError as error:
            from qtpy.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Save Pinned Point", str(error))
            return
        try:
            self._saved_location_store.upsert(
                payload["name"], payload["lat"], payload["lon"])
        except Exception as error:  # noqa: BLE001 - store errors are user-facing
            from qtpy.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Save Pinned Point", str(error))
            return
        self._refresh_location_markers()
        self.statusBar().showMessage(
            f"Saved {payload['name']} "
            f"({payload['lat']:.4f}, {payload['lon']:.4f}).", 6000)

    def _refresh_tool_actions(self) -> None:
        """Sync View-menu tool/lock/recent/copy state with the active map."""
        widget = self._active_map_widget()
        tools = getattr(self, "_tool_actions", None)
        if tools:
            try:
                active = widget.map_tool() if widget is not None else "select"
            except (AttributeError, RuntimeError):
                active = "select"
            if active not in tools:
                active = "select"
            for tool, action in tools.items():
                if action.isChecked() != (tool == active):
                    action.blockSignals(True)
                    try:
                        action.setChecked(tool == active)
                    finally:
                        action.blockSignals(False)
        lock_action = getattr(self, "_lock_point_action", None)
        if lock_action is not None:
            try:
                locked = bool(widget.is_point_locked()) if widget is not None \
                    and hasattr(widget, "is_point_locked") else False
            except (AttributeError, RuntimeError):
                locked = False
            if lock_action.isChecked() != locked:
                lock_action.blockSignals(True)
                try:
                    lock_action.setChecked(locked)
                finally:
                    lock_action.blockSignals(False)
        recent_action = getattr(self, "_recent_point_action", None)
        if recent_action is not None:
            try:
                has_recent = bool(widget.has_recent_point()) \
                    if widget is not None \
                    and hasattr(widget, "has_recent_point") else False
            except (AttributeError, RuntimeError):
                has_recent = False
            recent_action.setEnabled(has_recent)
        for attribute in ("_copy_coords_action", "_copy_value_action",
                          "_save_point_action"):
            action = getattr(self, attribute, None)
            if action is None:
                continue
            try:
                has_card = widget.inspection_snapshot() is not None \
                    if widget is not None \
                    and hasattr(widget, "inspection_snapshot") else False
            except (AttributeError, RuntimeError):
                has_card = False
            action.setEnabled(has_card)

    def _trigger_map_navigation(self, action_id: str) -> None:
        """Run one View-menu navigation action on the active map (T17.2)."""
        widget = self._active_map_widget()
        if widget is None:
            self.statusBar().showMessage("No map is available to navigate.", 4000)
            return
        triggers = {
            entry["id"]: entry["trigger"]
            for entry in widget.navigation_actions()
            if callable(entry.get("trigger"))
        }
        trigger = triggers.get(str(action_id))
        if trigger is None:
            return
        try:
            trigger()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            _LOGGER.debug("maps.navigation_failed action=%s", action_id,
                          exc_info=True)
            return
        self._refresh_navigation_actions()

    def _refresh_navigation_actions(self) -> None:
        """Sync View-menu history availability with the active map (T17.2)."""
        self._refresh_tool_actions()
        actions = getattr(self, "_nav_actions", None)
        if not actions:
            return
        widget = self._active_map_widget()
        can_back = bool(widget.can_go_back()) if widget is not None \
            and hasattr(widget, "can_go_back") else False
        can_forward = bool(widget.can_go_forward()) if widget is not None \
            and hasattr(widget, "can_go_forward") else False
        previous = actions.get("previous-view")
        if previous is not None:
            previous.setEnabled(can_back)
        following = actions.get("next-view")
        if following is not None:
            following.setEnabled(can_forward)

    def _top_summary_budget(self) -> int:
        """Return the width the summary may take from the menu row, or zero.

        The summary is the only flexible thing in the bar, so it gets what is left
        after the menus, the source control, and the clock, and it gives that space
        up entirely rather than letting the row run out of room. Measuring it any
        other way -- a natural width, or a fixed character cap -- widened the
        top-right corner widget until ``ResponsivePickerHeader`` concluded the
        corners could not fit and permanently restacked the bar.
        """
        label = getattr(self, "_top_summary", None)
        bar = self.menuBar()
        if label is None or bar is None or bar.width() <= 0:
            return 0
        header = getattr(self, "_responsive_header", None)
        if header is not None and header.is_separated():
            # The bar has already given up on holding its corner widgets on the
            # menu row. An optional summary must not be what re-widens that row
            # and pins it in the stacked layout, so it stands down completely.
            return 0
        metrics = bar.fontMetrics()
        menus = sum(
            metrics.horizontalAdvance(action.text().replace("&", "")) + SPACE["lg"]
            for action in bar.actions()
        )
        source = getattr(self, "_source_picker", None)
        source_width = 0
        if source is not None:
            source_width = max(
                source.sizeHint().width(), source.minimumSizeHint().width()
            )
        # Qt's own measurement of the clock corner without the summary in it,
        # rather than a reconstruction from the label width and the frame margins.
        # Reconstructing it missed the frame's style-sheet padding and left the
        # budget about seventeen pixels too generous, which was enough for reflow
        # to decide the corners no longer fit and restack the whole bar.
        frame = self._utc_clock_frame
        divider = self._top_summary_divider
        occupied = (label.width() if label.isVisible() else 0) + (
            divider.width() if divider.isVisible() else 0
        )
        bare_clock = (
            max(frame.sizeHint().width(), frame.minimumSizeHint().width()) - occupied
        )
        divider_width = divider.sizeHint().width()
        # The same basis reflow measures against -- the window width less its own
        # margins -- minus reflow's own SPACE["lg"] term, minus headroom so a few
        # pixels of style-sheet drift can never flip the layout.
        spare = (
            self.width()
            - 2 * SPACE["sm"]
            - menus
            - source_width
            - bare_clock
            - divider_width
            - SPACE["lg"]
            - _TOP_SUMMARY_HEADROOM
        )
        character = max(1, label.fontMetrics().averageCharWidth())
        spare = min(spare, character * _TOP_SUMMARY_CHARS)
        # Below a legible fragment the summary is noise; the tooltip and the
        # accessible description still carry it, and the row stays intact.
        if spare < character * _TOP_SUMMARY_MIN_CHARS:
            return 0
        return int(spare)

    def _refresh_top_summary_width(self) -> None:
        """Re-budget the summary after a resize, a font change, or a new value."""
        label = getattr(self, "_top_summary", None)
        if label is None:
            return
        budget = self._top_summary_budget()
        if budget != self._top_summary_width:
            self._top_summary_width = budget
            label.setFixedWidth(budget)
        self._apply_top_summary()
        frame = getattr(self, "_utc_clock_frame", None)
        header = getattr(self, "_responsive_header", None)
        if frame is not None:
            hint = (frame.sizeHint().width(), frame.sizeHint().height())
            previous_hint = getattr(self, "_top_summary_corner_hint", None)
            self._top_summary_corner_hint = hint
            if (hint != previous_hint and header is not None
                    and not header.is_separated()):
                # QMenuBar fixes a corner widget's geometry when it is first
                # attached. Changing a child label's fixed width later does not
                # reliably resize that corner, so the summary can paint beyond
                # the window even though its calculated budget fits. Reattach
                # only when the frame's actual hint changes; this refreshes the
                # geometry without creating a resize/reflow loop.
                header.bar.setCornerWidget(frame, Qt.TopRightCorner)
        pane = self._active_selection_pane()
        if pane is not None:
            # No room up here means the pane shows it instead. Losing the
            # selection context altogether is not an option.
            pane.set_summary_inline(budget <= 0)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_top_summary_width()

    def _set_top_summary(self, text: str) -> None:
        """Show one source's selection context beside the clock, or nothing."""
        label = getattr(self, "_top_summary", None)
        if label is None:
            return
        self._top_summary_full = str(text or "").strip()
        self._refresh_top_summary_width()

    def _apply_top_summary(self) -> None:
        """Paint the retained summary into whatever width it currently has."""
        label = getattr(self, "_top_summary", None)
        if label is None:
            return
        text = self._top_summary_full
        divider = self._top_summary_divider
        if not text or self._top_summary_width <= 0:
            label.clear()
            label.hide()
            divider.hide()
            return
        visible_text = self._compact_top_summary(text)
        label.setText(_fit_summary_groups(
            visible_text, label.fontMetrics(), self._top_summary_width
        ))
        # The untruncated context stays reachable: this line can be long, and the
        # scientific detail remains in both the tooltip and accessible description
        # even though the visible version removes a derived duplicate.
        label.setToolTip(text)
        label.setAccessibleName("Current selection")
        label.setAccessibleDescription(text)
        label.show()
        divider.show()

    def _sync_top_bar_context(self, *_args) -> None:
        """Point the top bar's summary and View item at the active source."""
        pane = self._active_selection_pane()
        action = getattr(self, "_hide_controls_action", None)
        if pane is None:
            self._set_top_summary("")
            if action is not None:
                action.setEnabled(False)
            return
        if not getattr(pane, "_sharpmod_top_bar_linked", False):
            # Connected on first activation rather than at construction, so the
            # six lazily built sources do not each need a wiring call site.
            pane.summaryChanged.connect(self._on_pane_summary_changed)
            pane.collapsedChanged.connect(self._on_pane_collapsed_changed)
            pane._sharpmod_top_bar_linked = True
        self._set_top_summary(pane.summary_text())
        if action is not None:
            action.setEnabled(True)
            blocked = action.blockSignals(True)
            action.setChecked(pane.is_collapsed())
            action.blockSignals(blocked)

    def _on_pane_summary_changed(self, text: str) -> None:
        if self.sender() is self._active_selection_pane():
            self._set_top_summary(text)

    def _on_pane_collapsed_changed(self, collapsed: bool) -> None:
        action = getattr(self, "_hide_controls_action", None)
        if action is None or self.sender() is not self._active_selection_pane():
            return
        blocked = action.blockSignals(True)
        action.setChecked(bool(collapsed))
        action.blockSignals(blocked)

    def _on_hide_controls_toggled(self, checked: bool) -> None:
        pane = self._active_selection_pane()
        if pane is not None:
            pane.set_collapsed(bool(checked))

    def _update_utc_clock(self) -> None:
        clock = getattr(self, "_utc_clock", None)
        if clock is None:
            return
        now = datetime.now(timezone.utc)
        minute = self._format_utc_clock(now)
        if clock.text() != minute:
            clock.setText(minute)
        clock.setToolTip(
            f"Current UTC time: {now:%Y-%m-%d %H:%M:%S}Z\n"
            f"Zulu is the same instant, written {now:%H%M}Z.\n"
            "Model runs, cycles, and valid times here are all UTC."
        )

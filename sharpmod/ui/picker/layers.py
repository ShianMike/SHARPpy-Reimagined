"""Picker overlay selectors, layer lists, and map modes."""

from __future__ import annotations

from contextlib import suppress

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.styles.theme import (
    OBJ_CARD_TOGGLE, OBJ_PLAIN, OBJ_SECTION_LABEL, SPACE,
)
from sharpmod.ui.picker.layer_compact import CompactLayerControls
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


from sharpmod.ui.picker.layer_preferences import LayerPreferencesMixin


class PickerLayersMixin(LayerPreferencesMixin):
    """Manage visible layers, source controls, and map mode."""


    @staticmethod
    def _overlay_group_label(text: str) -> QLabel:
        """Return the shared heading for the map's compact layer rows."""
        label = QLabel(text)
        label.setObjectName(OBJ_SECTION_LABEL)
        return label

    def _layer_manager_card(self, scope_label: str):
        """Return a single layer surface with actions above the controls."""
        card, root = _rail_card("Map layers")
        overview = QLabel("No optional layers shown", card)
        overview.setObjectName("pickerSectionSummary")
        overview.setTextFormat(Qt.PlainText)
        overview.setWordWrap(True)
        overview.setAccessibleName("Map layer summary")
        root.addWidget(overview)

        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(SPACE["xs"])
        root.addLayout(actions)

        choices_page = QWidget(card)
        choices_page.setObjectName(OBJ_PLAIN)
        choices = QVBoxLayout(choices_page)
        choices.setContentsMargins(0, SPACE["xs"], 0, 0)
        choices.setSpacing(SPACE["xs"])
        choices.addWidget(self._overlay_group_label(scope_label))
        root.addWidget(choices_page)
        card.set_summary(overview.text())
        return card, choices, actions, overview

    def _install_inline_layer_statuses(self, tab: str) -> None:
        """Replace expanding on-switch settings with compact editable rows."""
        _, outlook, reports, radar, field, context = self._layer_controllers(tab)
        controls = CompactLayerControls(
            (outlook, reports, radar, field, context),
            retry=lambda key: self._retry_layer(tab, key),
        )
        setattr(self, f"_{tab}_compact_layers", controls)
        setattr(self, f"_{tab}_layer_statuses", controls.rows)

    def _refresh_inline_layer_statuses(self, tab: str, entries: list) -> None:
        controls = getattr(self, f"_{tab}_compact_layers", None)
        if controls is not None:
            controls.refresh(entries)

    def _add_locator_selector(self, layout, tab: str):
        """Keep locator choices behind a summary disclosure by default."""
        from sharpmod.ui.maps.overlays.controllers import LocatorOverlaySelector

        toggle = QToolButton()
        toggle.setObjectName(OBJ_CARD_TOGGLE)
        toggle.setText("Sounding locator")
        toggle.setAccessibleName("Sounding locator options")
        toggle.setCheckable(True)
        toggle.setArrowType(Qt.RightArrow)
        toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle.setAutoRaise(True)
        toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addSpacing(SPACE["xs"])
        layout.addWidget(toggle)
        selector = LocatorOverlaySelector(
            parent=self, settings=self._settings, show_heading=False,
        )
        options = selector.controls_widget()
        layout.addWidget(options)
        options.hide()

        def set_locator_open(opened: bool) -> None:
            options.setVisible(opened)
            toggle.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)

        toggle.toggled.connect(set_locator_open)
        setattr(self, f"_{tab}_locator_toggle", toggle)
        return selector

    def selected_locator_spec(self):
        """Return the locator selection, for the sounding window to honour.

        Read duck-typed by :func:`sharpmod.gui_viewer.start_locator_overlay_fetch`.
        The tab in front decides, for the reason
        :meth:`selected_overlay_product` documents: ranking the tabs in a fixed
        order instead would answer with whichever came first regardless of which
        map the user was actually reading. ``None`` means "not stated", which
        leaves the previous behaviour in place.
        """
        selectors = []
        tabs = getattr(self, "_tabs", None)
        if tabs is not None:
            try:
                title = tabs.tabText(tabs.currentIndex())
            except (AttributeError, RuntimeError):
                title = ""
            front = TAB_LOCATOR_SELECTORS.get(title)
            if front is not None:
                selectors.append(front)
        selectors.extend(
            name for name in TAB_LOCATOR_SELECTORS.values() if name not in selectors
        )
        for attribute in selectors:
            selector = getattr(self, attribute, None)
            if selector is None:
                continue
            try:
                return selector.spec()
            except Exception:  # noqa: BLE001 - a preference is not worth raising
                return None
        return None

    def _remember_locator_choice(self) -> None:
        """Persist the locator selection alongside the other overlay choices."""
        for attribute in ("_map_locator", "_model_locator"):
            selector = getattr(self, attribute, None)
            if selector is None:
                continue
            with suppress(Exception):
                selector.remember()
                return

    def _remember_overlay_choices(self) -> None:
        """Persist the selected HRRR field, radar scope, and pinned radar.

        Never raises: this runs from ``closeEvent``, and a window must not refuse
        to close over a preference it could not write.
        """
        field = getattr(self, "_map_field", None) or getattr(self, "_model_field", None)
        radar = (
            getattr(self, "_map_radar", None)
            or getattr(self, "_model_radar", None)
            or getattr(self, "_panels_radar", None)
        )
        context = (
            getattr(self, "_map_context", None)
            or getattr(self, "_model_context", None)
            or getattr(self, "_panels_context", None)
        )
        try:
            if field is not None:
                self._settings.setValue("overlays/hrrr_field_product", field.product())
                self._settings.setValue(
                    "overlays/hrrr_field_opacity", round(field.opacity(), 2))
            if radar is not None:
                self._settings.setValue("overlays/radar_scope", radar.scope())
                self._settings.setValue("overlays/radar_site", radar.site())
                self._settings.setValue(
                    "overlays/radar_opacity", round(radar.opacity(), 2))
            if context is not None:
                self._settings.setValue("overlays/goes_channel", context.channel())
                self._settings.setValue("overlays/surface_density", context.density())
                self._settings.setValue(
                    "overlays/goes_opacity", round(context.opacity(), 2))
            self._settings.sync()
        except Exception:  # noqa: BLE001 - closing must not fail on a preference
            _LOGGER.debug("overlays.choices_unsaved", exc_info=True)

    def _apply_map_projection(self) -> None:
        """Push the chosen view onto every map that exists."""
        name = self.map_projection()
        for widget in self._map_widgets():
            try:
                widget.set_projection(name)
            except Exception:  # noqa: BLE001 - one map must not block the rest
                _LOGGER.debug("maps.projection_failed", exc_info=True)

    def _layer_presets(self) -> dict:
        """Return the named compatible layer presets (T19.2)."""
        from sharpmod.maps.map_layers import LAYER_PRESETS

        return dict(LAYER_PRESETS)

    def _layer_controllers(self, tab: str) -> tuple:
        """Return ``(map, outlook, reports, radar, field, context)`` for a tab."""
        prefixes = {"map": "_map", "model": "_model", "panels": "_panels"}
        prefix = prefixes.get(tab, "_map")
        return (
            getattr(self, f"{prefix}_map" if tab != "panels" else "_panels_view",
                    None),
            getattr(self, f"{prefix}_outlook", None),
            getattr(self, f"{prefix}_reports", None),
            getattr(self, f"{prefix}_radar", None),
            getattr(self, f"{prefix}_field", None)
            if tab != "panels" else None,
            getattr(self, f"{prefix}_context", None),
        )

    def _build_layer_entries(self, tab: str) -> list:
        """Snapshot one tab's layers for the active-layer list (T19.1-T19.4)."""
        from sharpmod.maps.map_layers import (
            LAYER_GROUPS,
            LayerEntry,
            occlusion_guard,
            paint_order_index,
            reports_dependency,
        )

        _map, outlook, reports, radar, field, context = \
            self._layer_controllers(tab)
        entries: list[LayerEntry] = []

        def _controller_state(controller, key: str, facet: str = ""):
            if key in ("goes_context", "surface_observations"):
                try:
                    if key == "goes_context":
                        enabled = bool(controller.is_satellite_enabled())
                    else:
                        enabled = bool(controller.is_surface_enabled())
                except (AttributeError, RuntimeError):
                    return None
            else:
                try:
                    enabled = bool(controller.is_enabled())
                except (AttributeError, RuntimeError):
                    return None
            try:
                state_text = str(controller.layer_state_text(facet) or "") \
                    if facet else str(controller.layer_state_text() or "")
            except TypeError:
                state_text = str(controller.layer_state_text() or "")
            except (AttributeError, RuntimeError):
                state_text = ""
            try:
                if facet:
                    time_text = str(controller.layer_time_text(facet) or "")
                else:
                    time_text = str(controller.layer_time_text() or "")
            except TypeError:
                # Compatibility for a controller that exposes one combined row.
                try:
                    time_text = str(controller.layer_time_text() or "")
                except (AttributeError, RuntimeError):
                    time_text = ""
            except (AttributeError, RuntimeError):
                time_text = ""
            try:
                error_text = str(controller.layer_error_text(facet) or "") \
                    if facet else str(controller.layer_error_text() or "")
            except TypeError:
                error_text = str(controller.layer_error_text() or "")
            except (AttributeError, RuntimeError):
                error_text = ""
            lowered = state_text.casefold()
            if error_text:
                # Failure outranks the switch (T19.5): an enabled layer that
                # failed and was then hidden still reports the failure, with
                # its retry, until the failure itself is resolved.
                state, detail = "failed", error_text
            elif not enabled:
                state, detail = "off", ""
            elif "loading" in lowered or "matching" in lowered:
                state, detail = "loading", state_text
            elif "no " in lowered and ("cover" in lowered or "report" in lowered
                                       or "frame" in lowered or "field" in lowered
                                       or "range" in lowered or "match" in lowered):
                state, detail = "empty", state_text
            elif "outside" in lowered or "stale" in lowered or "old" in lowered:
                state, detail = "stale", state_text
            elif state_text and not time_text:
                state, detail = "ok", state_text
            else:
                state, detail = "ok", ""
            if key == "hrrr_field" and enabled:
                # The field controller has typed outcomes; parsing its prose
                # would collapse offline/no-data/outside-domain into a generic
                # failure or 'stale' row even while a cached image is drawn.
                try:
                    field_state = controller.layer_state()
                except (AttributeError, RuntimeError):
                    field_state = None
                if field_state in (
                        "loading", "offline", "no-data", "failed",
                        "outside-domain", "canceled", "available"):
                    state = "ok" if field_state == "available" else field_state
                    detail = state_text if state != "ok" else ""
            try:
                has_payload = controller_attached(controller)
            except Exception:  # noqa: BLE001 - a missing payload is "off"
                has_payload = False
            if not enabled:
                pass
            elif has_payload is False and state in ("ok",):
                state, detail = "empty", state_text or "Nothing here yet"
            if facet and not enabled:
                detail = ""
            return enabled, state, detail, time_text

        def controller_attached(controller) -> bool:
            for reader in ("attached_raster", "attached_layer"):
                reader_fn = getattr(controller, reader, None)
                if callable(reader_fn):
                    try:
                        return reader_fn() is not None
                    except (AttributeError, RuntimeError):
                        continue
            return True

        specs = []
        if field is not None:
            try:
                product_label = field.product()
            except (AttributeError, RuntimeError):
                product_label = ""
            specs.append(("hrrr_field", "HRRR model field", LAYER_GROUPS[0],
                          field, product_label))
        if radar is not None:
            try:
                from sharpmod.ui.maps.overlays.controllers import SCOPE_MOSAIC

                scope = radar.scope()
                key = "radar_mosaic" if scope == SCOPE_MOSAIC else "radar_site"
                scope_text = ("CONUS mosaic" if scope == SCOPE_MOSAIC
                              else "nearest site")
            except (AttributeError, RuntimeError):
                key, scope_text = "radar_site", ""
            specs.append((key,
                          f"Radar ({scope_text})" if scope_text else "Radar",
                          LAYER_GROUPS[0], radar, scope_text))
        if outlook is not None:
            try:
                product_label = outlook.product()
            except (AttributeError, RuntimeError):
                product_label = ""
            specs.append(("spc_outlook", "SPC convective outlook",
                          LAYER_GROUPS[1], outlook, product_label))
        if reports is not None:
            outlook_on = False
            try:
                outlook_on = bool(outlook.is_enabled()) if outlook is not None \
                    else True
            except (AttributeError, RuntimeError):
                outlook_on = True
            _available, reason, action = reports_dependency(outlook_on)
            specs.append(("storm_reports", "Storm reports", LAYER_GROUPS[1],
                          reports, "", reason, action))
        if context is not None:
            # Both context rows always appear (T19.1): hiding the "off" one
            # would make the list unable to answer "what is hidden".
            specs.append(("goes_context", "GOES satellite", LAYER_GROUPS[0],
                          context, "satellite"))
            specs.append(("surface_observations", "Surface observations",
                          LAYER_GROUPS[1], context, "surface"))
        entries_by_key: dict[str, LayerEntry] = {}
        for spec in specs:
            key, name, group, controller = spec[0], spec[1], spec[2], spec[3]
            extra = spec[4] if len(spec) > 4 else ""
            reason = spec[5] if len(spec) > 5 else ""
            action = spec[6] if len(spec) > 6 else ""
            facet = extra if key in ("goes_context", "surface_observations") else ""
            resolved = _controller_state(controller, key, facet)
            if resolved is None:
                continue
            enabled, state, detail, time_text = resolved
            try:
                opacity = (1.0 if key == "surface_observations" else
                           float(controller.opacity()))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                opacity = 1.0
            if key in ("goes_context", "surface_observations"):
                label = name
            else:
                label = f"{name} — {extra}" if extra and extra not in name else name
            # A failed layer keeps its error text even when hidden (T19.5):
            # the failure is what the row exists to report, and a retry must
            # stay reachable until it is resolved rather than clearing on hide.
            if state in ("failed", "unavailable", "offline", "no-data") \
                    and not enabled:
                try:
                    failure_detail = str(controller.layer_error_text() or "")
                except (AttributeError, RuntimeError):
                    failure_detail = ""
                detail = failure_detail or detail or reason
            # T21.2: the row's time-match state from the map's own verdict,
            # read from the same attached payloads the legend paints. Hidden
            # rows carry none -- there is no frame to judge.
            match_text = ""
            try:
                matches = _map.time_match_states() if _map is not None else {}
            except (AttributeError, RuntimeError):
                matches = {}
            if enabled and isinstance(matches, dict):
                verdict = matches.get(key)
                if verdict is None and key in ("radar_mosaic", "radar_site"):
                    verdict = matches.get("radar_mosaic") or matches.get(
                        "radar_site")
                if verdict is not None:
                    try:
                        match_state, match_detail = verdict
                    except (TypeError, ValueError):
                        match_state, match_detail = "", ""
                    if match_state == "exact":
                        match_text = "exact"
                    elif match_detail:
                        match_text = str(match_detail)
                else:
                    # An enabled row with no attached payload still needs one
                    # of T21.2's explicit verdicts.  Loading/failure remains in
                    # the row state; the match text says why no time can yet be
                    # compared rather than leaving a blank that looks omitted.
                    if state == "loading":
                        match_text = "Unavailable · requested frame loading"
                    elif state in ("failed", "offline", "no-data"):
                        match_text = f"Unavailable · {state}"
                    else:
                        match_text = "Unavailable · no displayed frame"
            entries_by_key[key] = LayerEntry(
                key=key, name=label, group=group, visible=enabled,
                opacity=opacity, time_text=time_text, state=state,
                detail=detail or reason, requires=action or reason,
                scope="main map", match_text=match_text)
        try:
            occluded = occlusion_guard(list(entries_by_key.values()))
        except Exception:  # noqa: BLE001 - occlusion never breaks the list
            occluded = {}
        for key, note in occluded.items():
            if key in entries_by_key:
                entries_by_key[key].occlusion = note
                entries_by_key[key].state = "occluded"
        entries = sorted(entries_by_key.values(),
                         key=lambda entry: paint_order_index(entry.key))
        entries.append(LayerEntry(
            key="geography", name="Geography (coastlines, labels, scale)",
            group=LAYER_GROUPS[2], visible=True, opacity=1.0,
            time_text="", state="ok", detail="Always drawn",
            scope="main map"))
        # T19.3: what travels onto the sounding's locator inset reads in its own
        # trailing section, namespaced so it cannot be confused with a main-map
        # row for the same family. Never painted here -- scope says where.
        try:
            entries.extend(self._locator_layer_entries(tab))
        except Exception:  # noqa: BLE001 - locator rows are advisory
            _LOGGER.debug("layers.locator_failed", exc_info=True)
        return entries

    def _locator_layer_entries(self, tab: str) -> list:
        """Return sounding-locator rows for one tab (T19.3)."""
        from sharpmod.maps.map_layers import locator_entry

        prefixes = {"map": "_map", "model": "_model", "panels": "_panels"}
        selector = getattr(self, f"{prefixes.get(tab, '_map')}_locator", None)
        if selector is None:
            return []
        try:
            chosen = list(selector.selection())
        except (AttributeError, RuntimeError):
            return []
        try:
            hazard = selector.hazard()
        except (AttributeError, RuntimeError):
            hazard = None
        labels = getattr(selector, "LABELS", {})
        rows = []
        for item in chosen:
            family = getattr(item, "family", "")
            product = getattr(item, "product", None)
            name = str(labels.get(family, family) or family)
            if family == "risk" and (product or hazard):
                detail = f"Pinned hazard: {product or hazard}"
            elif family == "risk":
                detail = "Follows the map hazard"
            else:
                detail = "On the sounding locator"
            rows.append(locator_entry(str(family), name, detail=detail))
        return rows

    def _refresh_layer_list(self, tab: str) -> None:
        """Rebuild one tab's active-layer list from its controllers (T19.1)."""
        widget = {"map": "_map_layers", "model": "_model_layers",
                  "panels": "_panels_layers"}.get(tab)
        view = getattr(self, widget, None) if widget else None
        if view is None:
            return
        try:
            entries = self._build_layer_entries(tab)
            view.set_entries(entries)
            self._refresh_inline_layer_statuses(tab, entries)
            shown = sum(
                bool(entry.visible)
                for entry in entries
                if entry.scope == "main map" and entry.key != "geography"
            )
            locator = sum(
                bool(entry.visible)
                for entry in entries
                if str(entry.key).startswith("locator:")
            )
            attention = sum(
                str(entry.state) in (
                    "failed", "unavailable", "offline", "no-data", "canceled"
                )
                for entry in entries
            )
            summary = (
                f"{shown} enabled on map · {locator} on locator"
                + (f" · {attention} need attention" if attention else "")
            )
            overview = getattr(self, f"_{tab}_layer_overview", None)
            if overview is not None:
                overview.setText(summary)
                overview.setAccessibleDescription(summary)
            locator_toggle = getattr(self, f"_{tab}_locator_toggle", None)
            if locator_toggle is not None:
                locator_toggle.setText(
                    f"Sounding locator · {locator} selected"
                )
                locator_toggle.setToolTip(
                    "Choose layers on the sounding's small locator map"
                )
                locator_toggle.setAccessibleDescription(
                    f"{locator} locator layers selected"
                )
            card = getattr(self, f"_{tab}_layers_card", None)
            if card is not None:
                card.set_summary(summary)
            if tab in ("model", "panels"):
                refresh_details = getattr(self, "_update_forecast_map_details", None)
                if refresh_details is not None:
                    refresh_details(tab)
        except (AttributeError, RuntimeError):
            pass

    def _refresh_all_layer_lists(self) -> None:
        """Rebuild every built active-layer list (T19.1)."""
        for tab in ("map", "model", "panels"):
            try:
                self._refresh_layer_list(tab)
            except Exception:  # noqa: BLE001 - one list must not block rest
                _LOGGER.debug("layers.refresh_failed", exc_info=True)

    def _refresh_map_time_header(self, tab: str) -> None:
        """Restate one tab's persistent time header from its map (T21.1).

        The text comes from the map's own :meth:`time_header_text`, so the
        rail, the painted legend, the accessible description, and the export
        all read one line. Never blocks the rail: a missing map or label
        simply leaves the last text in place.
        """
        from sharpmod.maps.map_time import mode_label

        prefixes = {"map": "_map", "model": "_model", "panels": "_panels"}
        prefix = prefixes.get(tab, "_map")
        widget = getattr(self, {
            "map": "_map",
            "model": "_model_map",
            "panels": "_panels_view",
        }.get(tab, "_map"), None)
        label = getattr(self, f"{prefix}_time_lbl", None)
        if widget is None or label is None:
            return
        try:
            text = str(widget.time_header_text() or "")
            mode = mode_label(widget.map_mode())
        except (AttributeError, RuntimeError):
            return
        try:
            label.setText(f"{text}\n{mode}" if text else mode)
            if tab == "panels":
                compact = getattr(self, "_panels_valid_lbl", None)
                if compact is not None:
                    compact.setToolTip(label.text())
                    compact.setAccessibleDescription(label.text())
        except (AttributeError, RuntimeError):
            pass
        if tab in ("model", "panels"):
            refresh_details = getattr(self, "_update_forecast_map_details", None)
            if refresh_details is not None:
                refresh_details(tab)

    def _startup_map_mode(self, tab: str) -> str:
        """Return the remembered live/history mode for a tab (T21.4)."""
        from sharpmod.maps.map_time import normalise_mode

        try:
            stored = str(
                self._settings.value(f"maps/{tab}_mode", "live") or "live")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return "live"
        return normalise_mode(stored)

    def _apply_map_mode(self, tab: str, mode: str) -> None:
        """Persist a live/history choice and push it to the tab's map (T21.4).

        Historical pins the current run/hour: refreshes keep the labelled
        frame and the current overlay never swaps in unasked. Live releases
        back to following the newest published run.
        """
        from sharpmod.maps.map_time import normalise_mode

        wanted = normalise_mode(mode)
        try:
            self._settings.setValue(f"maps/{tab}_mode", wanted)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("maps.mode_unsaved", exc_info=True)
        prefixes = {"map": "_map", "model": "_model", "panels": "_panels"}
        prefix = prefixes.get(tab, "_map")
        widget = getattr(self, {
            "map": "_map",
            "model": "_model_map",
            "panels": "_panels_view",
        }.get(tab, "_map"), None)
        if widget is not None:
            try:
                widget.set_map_mode(wanted)
            except (AttributeError, RuntimeError):
                pass
        # Radar is latest-only in this application.  The map shelves its
        # pixels; the controller must also suspend its refresh timers so a
        # historical view neither spends requests nor accumulates current
        # frames behind the pin.
        radar = getattr(self, f"{prefix}_radar", None)
        set_radar_mode = getattr(radar, "set_map_mode", None)
        if callable(set_radar_mode):
            try:
                set_radar_mode(wanted)
            except (AttributeError, RuntimeError):
                pass
        combo = getattr(self, f"{prefix}_mode_combo", None)
        if combo is not None:
            try:
                index = combo.findData(wanted)
                if index >= 0 and combo.currentIndex() != index:
                    combo.blockSignals(True)
                    try:
                        combo.setCurrentIndex(index)
                    finally:
                        combo.blockSignals(False)
            except (AttributeError, RuntimeError):
                pass
        self._refresh_map_time_header(tab)
        _LOGGER.info("maps.mode tab=%s mode=%s", tab, wanted)

    def _on_model_map_mode_changed(self, _index) -> None:
        combo = getattr(self, "_model_mode_combo", None)
        if combo is None:
            return
        try:
            self._apply_map_mode("model", combo.currentData())
        except (AttributeError, RuntimeError):
            pass

    def _on_station_map_mode_changed(self, _index) -> None:
        combo = getattr(self, "_map_mode_combo", None)
        if combo is None:
            return
        try:
            self._apply_map_mode("map", combo.currentData())
        except (AttributeError, RuntimeError):
            pass

    def _on_panels_map_mode_changed(self, _index) -> None:
        combo = getattr(self, "_panels_mode_combo", None)
        if combo is None:
            return
        try:
            self._apply_map_mode("panels", combo.currentData())
        except (AttributeError, RuntimeError):
            pass

    def _wire_layer_list_refresh(self, tab: str) -> None:
        """Refresh one tab's list whenever its controllers report (T19.1).

        Connected once per controller, guarded by attribute so a tab rebuilt
        around the same controllers cannot double-connect. Reports reach the
        list through the outlook's own switch signal because the two are one
        dependency: the reports row refreshes when the outlook changes too.
        Opacity moves persist immediately through the same wiring pass, so a
        drag on any rail writes the one shared remembered value.
        """
        _map, outlook, reports, radar, field, context = \
            self._layer_controllers(tab)
        seen = set()
        for controller in (outlook, reports, radar, field, context):
            if controller is None or id(controller) in seen:
                continue
            seen.add(id(controller))
            for signal_name in ("layerChanged", "contextChanged"):
                signal = getattr(controller, signal_name, None)
                if signal is None:
                    continue
                key = (tab, id(controller), signal_name)
                wired = getattr(self, "_layer_list_wired", None)
                if wired is None:
                    wired = set()
                    self._layer_list_wired = wired
                if key in wired:
                    continue
                wired.add(key)
                try:
                    signal.connect(
                        lambda _arg=None, _tab=tab: self._refresh_layer_list(_tab))
                except (AttributeError, RuntimeError, TypeError):
                    continue
            slider = getattr(controller, "_opacity", None)
            if slider is not None:
                key = (tab, id(controller), "opacity")
                wired = getattr(self, "_layer_list_wired", None)
                if wired is None:
                    wired = set()
                    self._layer_list_wired = wired
                if key not in wired:
                    wired.add(key)
                    try:
                        slider.valueChanged.connect(
                            lambda _value, _controller=controller:
                            self._remember_controller_opacity(_controller))
                    except (AttributeError, RuntimeError, TypeError):
                        pass
        selector = getattr(self, f"_{tab}_locator", None)
        if selector is not None:
            key = (tab, id(selector), "locator")
            wired = getattr(self, "_layer_list_wired", None)
            if wired is None:
                wired = set()
                self._layer_list_wired = wired
            if key not in wired:
                wired.add(key)
                try:
                    selector.selectionChanged.connect(
                        lambda _tab=tab: self._refresh_layer_list(_tab))
                except (AttributeError, RuntimeError, TypeError):
                    pass
        if outlook is not None and reports is not None:
            try:
                outlook._check.toggled.connect(
                    lambda _on: self._refresh_layer_list(tab))
            except (AttributeError, RuntimeError):
                pass

    def _set_layer_visible(self, tab: str, key: str, visible: bool) -> None:
        """Route a list visibility toggle to the owning controller (T19.1)."""
        if key.startswith("locator:") or key == "geography":
            # Locator rows describe the sounding inset, not this map; geography
            # is always drawn. Neither has a visibility switch to route.
            return
        _map, outlook, reports, radar, field, context = \
            self._layer_controllers(tab)
        targets = {
            "hrrr_field": (field,),
            "radar_site": (radar,), "radar_mosaic": (radar,),
            "spc_outlook": (outlook,), "storm_reports": (reports,),
            "goes_context": (context,), "surface_observations": (context,),
        }.get(key, ())
        routed = False
        for controller in targets:
            if controller is None:
                continue
            setter = None
            if key in ("goes_context", "surface_observations") and \
                    hasattr(controller, "_sat_check"):
                check = getattr(controller, "_sat_check", None) \
                    if key == "goes_context" else getattr(
                        controller, "_surface_check", None)
                if check is not None:
                    check.setChecked(bool(visible))
                    routed = True
                    continue
            setter = getattr(controller, "set_enabled", None)
            if callable(setter):
                try:
                    setter(bool(visible))
                    routed = True
                except (AttributeError, RuntimeError):
                    continue
        if routed:
            self._refresh_layer_list(tab)

    def _on_map_layer_toggled(self, key: str, visible: bool) -> None:
        self._set_layer_visible("map", key, visible)

    def _on_model_layer_toggled(self, key: str, visible: bool) -> None:
        self._set_layer_visible("model", key, visible)

    def _on_panels_layer_toggled(self, key: str, visible: bool) -> None:
        self._set_layer_visible("panels", key, visible)

    def _retry_layer(self, tab: str, key: str) -> None:
        """Retry one layer alone, leaving the others untouched (T19.5)."""
        if key.startswith("locator:") or key == "geography":
            return
        _map, outlook, reports, radar, field, context = \
            self._layer_controllers(tab)
        targets = {
            "hrrr_field": (field,),
            "radar_site": (radar,), "radar_mosaic": (radar,),
            "spc_outlook": (outlook,), "storm_reports": (reports,),
            "goes_context": (context,), "surface_observations": (context,),
        }.get(key, ())
        for controller in targets:
            if controller is None:
                continue
            if key in ("goes_context", "surface_observations"):
                retry_layer = getattr(controller, "retry_layer", None)
                if callable(retry_layer):
                    retry_layer(key)
                    continue
            retry = getattr(controller, "retry", None) or getattr(
                controller, "refresh", None)
            if callable(retry):
                try:
                    retry()
                except (AttributeError, RuntimeError):
                    continue
        self.statusBar().showMessage(f"Retrying {key}…", 4000)
        self._refresh_layer_list(tab)

    def _on_map_layer_retry(self, key: str) -> None:
        self._retry_layer("map", key)

    def _on_model_layer_retry(self, key: str) -> None:
        self._retry_layer("model", key)

    def _on_panels_layer_retry(self, key: str) -> None:
        self._retry_layer("panels", key)

    def _apply_layer_preset(self, tab: str, preset_key: str) -> None:
        """Apply a named preset's layers and opacities, compatibly (T19.2).

        Opacity moves are user edits like slider drags: each controller writes
        its own remembered value straight away, so quitting mid-session keeps
        what the preset set rather than only what ``closeEvent`` saw last.
        """
        from sharpmod.maps.map_layers import (
            migrate_preset_choice,
            preset_layers,
        )

        key = migrate_preset_choice(preset_key)
        _map, outlook, reports, radar, field, context = \
            self._layer_controllers(tab)
        by_key = {
            "hrrr_field": field,
            "radar_site": radar, "radar_mosaic": radar,
            "spc_outlook": outlook, "storm_reports": reports,
            "goes_context": context, "surface_observations": context,
        }
        wanted = dict(preset_layers(key))
        for layer_key, controller in by_key.items():
            if controller is None:
                continue
            try:
                if layer_key in wanted:
                    opacity = float(wanted[layer_key])
                    setter = getattr(controller, "set_opacity_value", None)
                    if callable(setter):
                        setter(opacity)
                    else:
                        self._remember_layer_opacity(layer_key, opacity)
                    controller.set_enabled(True)
                else:
                    controller.set_enabled(False)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        try:
            self._settings.setValue("overlays/layer_preset", key)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("layers.preset_unsaved", exc_info=True)
        self._refresh_layer_list(tab)
        self.statusBar().showMessage(f"Applied layer preset “{key}”.", 5000)

    def _on_map_layer_preset(self) -> None:
        view = getattr(self, "_map_layers", None)
        self._apply_layer_preset("map", view.selected_preset() if view else "")

    def _on_model_layer_preset(self) -> None:
        view = getattr(self, "_model_layers", None)
        self._apply_layer_preset("model", view.selected_preset() if view else "")

    def _on_panels_layer_preset(self) -> None:
        view = getattr(self, "_panels_layers", None)
        self._apply_layer_preset("panels", view.selected_preset() if view else "")

    def _remember_layer_opacity(self, layer_key: str, opacity: float) -> None:
        """Persist one layer opacity immediately (T19.2).

        Controllers with sliders persist through their own value setters via
        the shared opacity hook below; this covers the switch-only layers a
        preset still names (outlook/reports/surface carry no opacity slider).
        The stored band matches the sliders so a later read clamps identically.
        """
        from sharpmod.maps.map_layers import normalise_opacity

        settings_key = {
            "hrrr_field": "overlays/hrrr_field_opacity",
            "radar_site": "overlays/radar_opacity",
            "radar_mosaic": "overlays/radar_opacity",
            "goes_context": "overlays/goes_opacity",
        }.get(layer_key)
        if settings_key is None:
            return
        try:
            self._settings.setValue(
                settings_key, round(normalise_opacity(float(opacity)), 2))
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("layers.opacity_unsaved", exc_info=True)

    def _remember_controller_opacity(self, controller) -> None:
        """Persist a slider-owning controller's current opacity (T19.2).

        Matched by identity against every tab's controllers, so a drag on any
        rail writes the one shared remembered value its tab will read back.
        """
        if controller is None:
            return
        try:
            value = round(float(controller.opacity()), 2)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        names = {"_field": "hrrr_field", "_radar": "radar_site",
                 "_context": "goes_context"}
        for tab in ("map", "model", "panels"):
            _map, _outlook, _reports, radar, field, context = \
                self._layer_controllers(tab)
            _ = (_map, _outlook, _reports)
            for owned, candidate in (("_field", field), ("_radar", radar),
                                     ("_context", context)):
                if candidate is controller:
                    self._remember_layer_opacity(names[owned], value)
                    return

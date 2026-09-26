"""Station-map mouse, keyboard, and inspection interactions."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt, QPointF
from qtpy.QtWidgets import QMenu, QToolTip

from sharpmod.maps.map_overlays import (
    OverlayMarkerHit, describe_at, marker_hits_at,
)
from sharpmod.ui.maps.navigation import PAN_BUTTONS, SECONDARY_PAN_BUTTONS


class StationInteractionsMixin:
    """Handle map selection, inspection, and viewport gestures."""

    @staticmethod
    def _pos(event) -> QPointF:
        return (
            event.position()
            if hasattr(event, "position")
            else QPointF(event.x(), event.y())
        )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        if self._drag_last is not None and (event.buttons() & PAN_BUTTONS):
            press = self._press_pos if self._press_pos is not None else self._drag_last
            if not self._dragged and (
                abs(pos.x() - press.x()) + abs(pos.y() - press.y()) <= 3
            ):
                # Ignore click-sized jitter. Measure from the press rather than
                # the previous event so a slow drag still crosses the threshold.
                return
            # Pan by inverse-projecting both cursor positions and shifting the
            # view by the difference between them. A pixel delta divided by the
            # scale only works for an affine transform; this holds for the conic
            # too, and gives the same answer for the flat one.
            p = self._proj()
            was_lon, was_lat = p.inverse(self._drag_last.x(), self._drag_last.y())
            now_lon, now_lat = p.inverse(pos.x(), pos.y())
            dlon = was_lon - now_lon
            dlat = was_lat - now_lat
            if not (math.isfinite(dlon) and math.isfinite(dlat)):
                self._drag_last = pos
                return
            if not self._dragged:
                # Save the pre-pan bounds once, before the first actual move.
                # The old condition skipped a fast drag and saved a tiny jitter
                # instead, breaking Previous view for the usual gesture.
                self._push_history()
                self._pan_push_armed = True
                self._dragged = True
            self._lon0 += dlon
            self._lon1 += dlon
            self._lat0 += dlat
            self._lat1 += dlat
            self._drag_last = pos
            self._queue_map_preview()
            return
        previous_hover_id = self._hover_id
        previous_radar_id = self._radar_hover_id
        self._hover_lonlat = self._to_lonlat(pos.x(), pos.y())
        near = self._nearest(pos.x(), pos.y())
        hover_id = near["id"] if near else None
        if hover_id != self._hover_id:
            # The lightweight station-label layout may change to reveal the
            # hovered station. Town geography is intentionally stable: making
            # that 40-label layout stale here caused a visible jump and tens of
            # milliseconds of work whenever the pointer crossed a marker.
            self._hover_id = hover_id
            self._station_label_layout_cache = None
        # Resolved exactly as a click is, so what lights up under the cursor is
        # what pressing there would select.
        hit = self._radar_site_hit(pos.x(), pos.y())
        rival = self._station_rival(pos, near)
        try:
            hover_lon, hover_lat = self._hover_lonlat
        except (TypeError, ValueError):
            hover_lon, hover_lat = None, None
        if hit is not None and (rival is None or rival >= hit[1]):
            self._radar_hover_id = hit[0][0]
            self.setToolTip(
                f"{hit[0][0]} radar — click to use this site; "
                "middle/right-drag pans, right-click for map navigation")
        elif near is not None:
            self._radar_hover_id = None
            if getattr(self, "_map_tool", "select") == "inspect":
                if hover_lon is not None:
                    self._update_inspect_hover_for_station(hover_lat, hover_lon)
                self.setToolTip(
                    f"{near['id']}  {near['name']} — click to pin an inspection "
                    "card without selecting; middle/right-drag pans, "
                    "right-click for map navigation")
            else:
                self._hover_inspection_text = ""
                self.setToolTip(
                    f"{near['id']}  {near['name']} — click to select, "
                    "double-click to open; middle/right-drag pans, "
                    "right-click for map navigation")
        else:
            self._radar_hover_id = None
            if getattr(self, "_map_tool", "select") == "inspect":
                if hover_lon is not None:
                    self._update_inspect_hover_for_station(hover_lat, hover_lon)
                else:
                    self._hover_inspection_text = ""
                    self.setToolTip(self.tool_hint_text())
            else:
                self._hover_inspection_text = ""
                self.setToolTip(self.tool_hint_text())
        if (getattr(self, "_crosshair_overlay", None) is not None
                and not event.buttons()
                and hover_id == previous_hover_id
                and self._radar_hover_id == previous_radar_id):
            return
        self.update()

    def _update_inspect_hover_for_station(self, lat: float, lon: float) -> None:
        """Refresh the station-map inspect hover without moving anything."""
        from sharpmod.maps.map_field_inspection import hover_text

        try:
            sample = self.sample_field_at(float(lat), float(lon))
            text = hover_text(sample)
        except Exception:  # noqa: BLE001 - hover text is advisory
            text = ""
        if not text:
            try:
                text = f"{float(lat):.3f}, {float(lon):.3f}"
            except (TypeError, ValueError, OverflowError):
                text = ""
        self._hover_inspection_text = text
        if text:
            self.setToolTip(text + " — click to pin; inspecting never moves "
                                   "the sounding selection")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() & PAN_BUTTONS:
            self._drag_last = self._pos(event)
            self._dragged = False
            self._pan_push_armed = False
            # T17.4 click-versus-pan contract: remember the press point and
            # button for the release decision. A right/middle press never
            # selects; whether it opens the context menu is decided on
            # release, once a drag can be ruled out.
            self._press_pos = self._pos(event)
            self._press_button = event.button()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """Offer navigation actions where right-clicked (T17.4).

        Opens only for a genuine click: a drag that panned suppresses it via
        the ``_pan_consumed_press`` flag set on release, so panning with the
        right button never pops a menu at the drop point. Actions mirror the
        rail row and the command-palette entries from
        :meth:`navigation_actions`.
        """
        if getattr(self, "_pan_consumed_press", False):
            self._pan_consumed_press = False
            return
        menu = QMenu(self)
        for entry in self.navigation_actions():
            action = menu.addAction(f"{entry['label']}\t{entry['shortcut']}")
            action.setToolTip(entry["tooltip"])
            action.setStatusTip(entry["tooltip"])
            action.triggered.connect(entry["trigger"])
        centre_here = menu.addAction("Centre here")
        centre_here.setToolTip("Centre the view on the clicked location")
        centre_here.setStatusTip("Centre the view on the clicked location")
        try:
            clicked = self._to_lonlat(event.x(), event.y())
        except Exception:  # noqa: BLE001 - menu still offers the actions
            clicked = None
        if clicked is None or not all(math.isfinite(value) for value in clicked):
            centre_here.setEnabled(False)
        else:
            lon, lat = clicked
            centre_here.triggered.connect(
                lambda _checked=False, _lon=lon, _lat=lat:
                self.center_on_lonlat(_lon, _lat))
        try:
            where = event.globalPos()
        except Exception:  # noqa: BLE001 - headless/fake events have no screen
            try:
                where = event.globalPosition().toPoint()
            except Exception:  # noqa: BLE001 - fall back to the widget origin
                where = self.mapToGlobal(QPointF(0, 0).toPoint())
        menu.exec_(where)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() & SECONDARY_PAN_BUTTONS:
            # A middle or right drag only ever pans, so it ends here rather than
            # falling through to a selection.
            was_drag = self._dragged
            self._drag_last = None
            self._dragged = False
            self._pan_push_armed = False
            self._press_pos = None
            # T17.4: a right/middle drag consumed the press, so the context
            # menu must not open at the drop point. A clean click leaves the
            # flag clear and Qt delivers contextMenuEvent normally.
            self._pan_consumed_press = bool(was_drag)
            if was_drag:
                try:
                    self._clamp_view()
                except Exception:  # noqa: BLE001 - clamp never breaks release
                    pass
                self._refresh_map_accessible_description()
            return
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        self._pan_push_armed = False
        self._press_pos = None
        if was_drag:
            # T17.4: a left-drag pan ends here and never selects. Without this
            # guard a pan released over a station dot also selected that
            # station, so looking around changed the sounding.
            try:
                self._clamp_view()
            except Exception:  # noqa: BLE001 - clamp never breaks release
                pass
            self._refresh_map_accessible_description()
            return
        near = self._nearest(pos.x(), pos.y())
        if self._pick_radar_site(pos, self._station_rival(pos, near)):
            return
        if self._describe_marker_overlay_at(pos, event, include_station=True):
            return
        if near is not None:
            # Inspect pins an inspection card without selecting (T18.1/2).
            if getattr(self, "_map_tool", "select") == "inspect":
                self._pin_station_snapshot(near)
                return
            # A locked selection cannot be changed accidentally (T18.1): the
            # click still describes the overlay beneath it, but the selection
            # stays where it is.
            if self.is_point_locked():
                self._describe_overlay_at(pos, event)
                return
            if near["id"] != self._selected_id:
                self._selected_id = near["id"]
                self._label_layout_cache = None
            self.stationSelected.emit(near["id"])
            self.update()
            return
        # Nothing else wanted this click, so answer the other question a click
        # on a coloured area is asking: what is this? Placed last on purpose --
        # describing an overlay must never cost a station or radar selection,
        # and the areas are wide enough that there is always bare ground nearby
        # to ask from.
        self._describe_overlay_at(pos, event)

    @staticmethod
    def _global_point(event):
        """Screen position of ``event``, across the two Qt bindings' spellings."""
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()  # pragma: no cover - Qt5 naming

    def _describe_overlay_at(self, pos, event) -> bool:
        """Show what the overlay under ``pos`` is, if anything is there."""
        layers = self._visible_overlays()
        if not layers:
            return False
        try:
            lon, lat = self._proj().inverse(pos.x(), pos.y())
        except Exception:  # noqa: BLE001 - a limit point is not worth a crash
            return False
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return False
        # Report markers are handled above in pixel space. Their old geographic
        # rings may be much larger than the visible symbol after zooming.
        text = describe_at(layers, lon, lat, include_markers=False)
        if not text:
            return False
        QToolTip.showText(self._global_point(event), text, self)
        return True

    def _describe_marker_overlay_at(
        self, pos, event, *, include_station: bool = False
    ) -> bool:
        """Show or choose point markers while leaving broad area clicks free.

        T22.1 keeps every coincident marker reachable.  A single hit preserves
        the direct tooltip gesture; two or more open a chooser in visual-priority
        order instead of silently returning whichever shape happened to rank or
        draw last.
        """
        choices = list(self.marker_choices_at(pos.x(), pos.y()))
        if not include_station:
            choices = [choice for choice in choices if choice["kind"] != "station"]
        if not choices:
            return False
        # A bare sounding station keeps its direct select/activate gesture. It
        # joins the chooser only when another marker competes for the click.
        if len(choices) == 1 and choices[0]["kind"] == "station":
            return False
        where = self._global_point(event)
        if len(choices) == 1:
            QToolTip.showText(where, choices[0]["description"], self)
            return True
        menu = QMenu(self)
        menu.setTitle(f"Choose marker ({len(choices)})")
        actions = []
        for choice in choices:
            action = menu.addAction(choice["label"])
            action.setToolTip(choice["description"])
            action.setStatusTip(choice["description"].replace("\n", " · "))
            actions.append((action, choice))
        chosen = menu.exec_(where)
        for action, choice in actions:
            if chosen is action:
                if choice["kind"] == "station":
                    self._activate_station_marker_choice(choice["id"], where)
                else:
                    QToolTip.showText(where, choice["description"], self)
                break
        return True

    def _activate_station_marker_choice(self, station_id: str, where) -> None:
        """Apply the usual station gesture after it wins an overlap chooser."""

        station = self._station(station_id)
        if station is None:
            return
        if getattr(self, "_map_tool", "select") == "inspect":
            self._pin_station_snapshot(station)
            return
        if self.is_point_locked():
            QToolTip.showText(where, "Selection is locked", self)
            return
        if station_id != self._selected_id:
            self._selected_id = station_id
            self._label_layout_cache = None
            self._station_label_layout_cache = None
        self.stationSelected.emit(station_id)
        self.update()

    def marker_choices_at(self, x: float, y: float) -> tuple[dict, ...]:
        """Return every overlay-marker choice at widget pixel ``(x, y)``.

        This non-modal seam drives tests, accessibility, and future keyboard
        affordances from the exact same hit set as the pointer chooser.
        """

        try:
            projection = self._proj()
            lon, lat = projection.inverse(float(x), float(y))
        except (TypeError, ValueError, OverflowError):
            return ()
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return ()
        layers = self._visible_overlays()
        hits = list(marker_hits_at(
            layers, lon, lat, point_px=(float(x), float(y)),
            project=projection.forward,
        ))
        cache = getattr(self, "_report_caption_hit_cache", None)
        if (cache is not None and cache[0] is projection
                and cache[1] == frozenset(id(layer) for layer in layers)):
            seen = {id(hit.shape) for hit in hits}
            for rect, layer, shape in cache[2]:
                if rect.contains(float(x), float(y)) and id(shape) not in seen:
                    hits.append(OverlayMarkerHit(layer, shape))
                    seen.add(id(shape))
        choices = [
            {
                "id": str(getattr(hit.shape, "marker_id", "") or ""),
                "kind": str(getattr(hit.shape, "marker_kind", "") or "marker"),
                "label": hit.label,
                "description": hit.description,
                "rank": int(getattr(hit.shape, "rank", 0) or 0),
            }
            for hit in hits
        ]
        choices.extend(
            {
                "id": str(marker.get("id") or ""),
                "kind": "profile",
                "label": str(marker.get("label") or "Loaded profile"),
                "description": self._profile_marker_description(marker),
                "rank": 100 if marker.get("active") else 90,
            }
            for marker in self._profile_marker_hits(x, y)
        )
        station = self._nearest(float(x), float(y))
        if station is not None:
            station_id = str(station.get("id") or "")
            name = str(station.get("name") or "").strip()
            detail = f"Sounding station {station_id}"
            if name:
                detail += f" — {name}"
            detail += (
                f"\nLocation: {float(station['lat']):.3f}, "
                f"{float(station['lon']):.3f}"
            )
            choices.append(
                {
                    "id": station_id,
                    "kind": "station",
                    "label": f"Station {station_id}" + (f" — {name}" if name else ""),
                    "description": detail,
                    "rank": 80,
                }
            )
        choices.sort(key=lambda item: (-item["rank"], item["label"].casefold()))
        return tuple(choices)

    def _station_rival(self, pos, near) -> float | None:
        """Squared distance to the station competing for a click, if any."""
        if near is None:
            return None
        return self._distance2(pos, near["lon"], near["lat"])

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        near = self._nearest(pos.x(), pos.y())
        if self._pick_radar_site(pos, self._station_rival(pos, near)):
            return
        if self._describe_marker_overlay_at(pos, event, include_station=True):
            return
        if near is not None:
            # Inspect never activates: pinning is the whole gesture (T18.1).
            if getattr(self, "_map_tool", "select") == "inspect":
                self._pin_station_snapshot(near)
                return
            if self.is_point_locked():
                return
            if near["id"] != self._selected_id:
                self._selected_id = near["id"]
                self._label_layout_cache = None
            self.stationSelected.emit(near["id"])
            self.stationActivated.emit(near["id"])
            self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Keyboard map navigation (T17.2) plus explicit tools (T18.1).

        ``+``/``-``/``0`` zoom and fit, arrows pan, ``L`` centres on the
        selection, and ``Alt+Left``/``Alt+Right`` walk the view history. The
        Ctrl-modified zoom/fit forms work from anywhere the host rail
        installs them (``Ctrl+L`` stays the Locations manager, so centre has
        no Ctrl form); these bare keys only run when the map itself has
        focus, so typing ``+``, ``-``, or ``0`` in a text field never zooms
        the map. ``V``/``I``/``B`` switch Select/Inspect/Box and ``Esc``
        clears the pinned card first, then falls through to the box handling
        on PointMapWidget; modified keys never switch tools.
        """
        modifiers = event.modifiers() if hasattr(event, "modifiers") else Qt.NoModifier
        ctrl = bool(modifiers & Qt.ControlModifier)
        alt = bool(modifiers & Qt.AltModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        if not (ctrl or alt or shift):
            if event.key() == Qt.Key_Escape and getattr(
                    self, "_inspection", None) is not None:
                self._inspection = None
                self.update()
                event.accept()
                return
            if event.key() == Qt.Key_V:
                self.set_map_tool("select")
                event.accept()
                return
            if event.key() == Qt.Key_I:
                if "inspect" in self._allowed_tools():
                    self.set_map_tool("inspect")
                    event.accept()
                    return
            if event.key() == Qt.Key_B:
                if "box" in self._allowed_tools():
                    self.set_map_tool("box")
                    event.accept()
                    return
        if ctrl and event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom(0.8)
            event.accept()
            return
        if ctrl and event.key() == Qt.Key_Minus:
            self.zoom(1.25)
            event.accept()
            return
        if ctrl and event.key() == Qt.Key_0:
            self.fit_region()
            event.accept()
            return
        if alt and event.key() == Qt.Key_Left:
            if self.go_back():
                event.accept()
                return
        if alt and event.key() == Qt.Key_Right:
            if self.go_forward():
                event.accept()
                return
        if event.key() == Qt.Key_L and not (ctrl or alt or shift):
            # Single-key centre is discoverable from the rail tooltip; it only
            # runs with map focus so it never steals typing elsewhere.
            if self.center_on_selection():
                event.accept()
                return
        elif event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom(0.8)
            event.accept()
            return
        elif event.key() == Qt.Key_Minus:
            self.zoom(1.25)
            event.accept()
            return
        elif event.key() == Qt.Key_0:
            self.fit_region()
            event.accept()
            return
        elif event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            if alt:
                # Alt+arrows are history, handled above; a failed history step
                # (empty stack) must not also pan the map.
                super().keyPressEvent(event)
                return
            self._pan_by_key(event.key())
            event.accept()
            return
        super().keyPressEvent(event)

    def _pan_by_key(self, key) -> None:
        """Pan the view one tenth of its span (T16.2 keyboard access)."""
        lon_span = self._lon1 - self._lon0
        lat_span = self._lat1 - self._lat0
        if key == Qt.Key_Left:
            dlon, dlat = -lon_span / 10.0, 0.0
        elif key == Qt.Key_Right:
            dlon, dlat = lon_span / 10.0, 0.0
        elif key == Qt.Key_Up:
            dlon, dlat = 0.0, lat_span / 10.0
        else:
            dlon, dlat = 0.0, -lat_span / 10.0
        self._push_history()
        self._lon0 += dlon
        self._lon1 += dlon
        self._lat0 = max(-89.99, self._lat0 + dlat)
        self._lat1 = min(89.99, self._lat1 + dlat)
        self._clamp_view()
        self._queue_map_preview()

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = self._wheel_zoom_factor(event)
        if factor is None:
            return
        pos = self._pos(event)
        try:
            clon, clat = self._to_lonlat(pos.x(), pos.y())
        except Exception:  # noqa: BLE001 - a limit point zooms about the centre
            clon = (self._lon0 + self._lon1) / 2.0
            clat = (self._lat0 + self._lat1) / 2.0
        if not (math.isfinite(clon) and math.isfinite(clat)):
            clon = (self._lon0 + self._lon1) / 2.0
            clat = (self._lat0 + self._lat1) / 2.0
        self._zoom_about(factor, clon, clat)
        self._queue_map_preview()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._invalidate()
        super().resizeEvent(event)

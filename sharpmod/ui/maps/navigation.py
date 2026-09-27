"""Viewport navigation shared by the station and point map widgets."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt

#: Map views the user can choose between.
#:
#: ``flat`` is an equirectangular with a cosine-of-latitude longitude squeeze --
#: straight meridians and parallels, and the projection every version of this map
#: has used.
#:
#: ``curved`` is a Lambert conformal conic, the projection operational forecast
#: charts are drawn on: meridians converge toward the pole and parallels bow, so
#: a continent-wide view keeps its shape instead of stretching east-west with
#: latitude.
MAP_PROJECTIONS = ("flat", "curved")

#: Zoom limits for interactive navigation (T17.1). A longitude span narrower
#: than this is street level on a map whose data is synoptic scale: coastlines
#: quantize, labels collide, and one more notch buys nothing. A span wider
#: than the maximum unwraps the world twice over and turns every overlay into
#: a sliver. Latitude limits keep the poles' degenerate rows out of the view.
NAV_MIN_LON_SPAN = 0.2
NAV_MAX_LON_SPAN = 720.0
NAV_MIN_LAT_SPAN = 0.1
NAV_MAX_LAT_SPAN = 178.0

#: Bounded previous/next view history (T17.2). Deep enough to recover from a
#: lost view after panning across a front, shallow enough that a session of
#: exploration cannot grow it without bound.
NAV_HISTORY_LIMIT = 50

#: Wheel-zoom feel (T17.1). One physical 120-unit notch zooms by this factor;
#: smaller high-resolution deltas scale proportionally so a trackpad gesture
#: glides rather than stepping. Exponent, not repeated fixed steps, so a fast
#: flick and a slow roll cover the same ground per unit of input.
WHEEL_NOTCH_FACTOR = 0.83
WHEEL_NOTCH_UNITS = 120.0

#: Named map extents for the "Map Area" selector: (lon0, lon1, lat0, lat1).
MAP_AREAS: dict[str, tuple[float, float, float, float]] = {
    "United States (CONUS)": (-125.0, -66.0, 23.0, 50.0),
    "Alaska": (-180.0, -125.0, 48.0, 73.0),
    "Hawaii": (-162.5, -152.0, 15.0, 24.5),
    "Puerto Rico": (-69.5, -64.0, 16.5, 20.0),
    "North America": (-170.0, -50.0, 8.0, 75.0),
    "Caribbean / Gulf": (-100.0, -50.0, 5.0, 35.0),
    "Western Pacific": (115.0, 170.0, -5.0, 30.0),
    "Northern Hemisphere": (-180.0, 180.0, 0.0, 88.0),
    "Southern Hemisphere": (-180.0, 180.0, -88.0, 0.0),
    "Europe": (-15.0, 45.0, 34.0, 72.0),
    "Australia / Oceania": (110.0, 180.0, -50.0, 5.0),
    "Tropics": (-180.0, 180.0, -30.0, 30.0),
    "World": (-180.0, 180.0, -85.0, 85.0),
}


#: Buttons that pan the map but never select anything. Keeping a pan available on
#: a button the box gesture does not use is what lets the map still be moved
#: while box mode is armed -- the same arrangement selection tools elsewhere use.
SECONDARY_PAN_BUTTONS = Qt.MiddleButton | Qt.RightButton

#: Every button that can pan. The left button pans only when no other gesture
#: (a box drag) has claimed the press.
PAN_BUTTONS = Qt.LeftButton | SECONDARY_PAN_BUTTONS


class MapNavigationMixin:
    """Zoom, pan history, and named-region viewport behavior."""

    def _clamp_view(self) -> None:
        """Clamp the current extent to the navigable range (T17.1).

        Keeps ordering (lon1 > lon0, lat1 > lat0), enforces minimum/maximum
        spans, and keeps latitude inside the projectable band. Longitude is
        intentionally not wrapped: a view panned across the antimeridian is a
        single increasing range the projection needs.
        """
        lon0, lon1 = float(self._lon0), float(self._lon1)
        lat0, lat1 = float(self._lat0), float(self._lat1)
        if not all(math.isfinite(value) for value in (lon0, lon1, lat0, lat1)):
            return
        if lon1 <= lon0:
            lon1 = lon0 + NAV_MIN_LON_SPAN
        if lat1 <= lat0:
            lat1 = lat0 + NAV_MIN_LAT_SPAN
        lon_span = lon1 - lon0
        if lon_span < NAV_MIN_LON_SPAN:
            mid = (lon0 + lon1) / 2.0
            lon0, lon1 = mid - NAV_MIN_LON_SPAN / 2.0, mid + NAV_MIN_LON_SPAN / 2.0
        elif lon_span > NAV_MAX_LON_SPAN:
            mid = (lon0 + lon1) / 2.0
            lon0, lon1 = mid - NAV_MAX_LON_SPAN / 2.0, mid + NAV_MAX_LON_SPAN / 2.0
        lat_span = lat1 - lat0
        if lat_span < NAV_MIN_LAT_SPAN:
            mid = (lat0 + lat1) / 2.0
            lat0, lat1 = mid - NAV_MIN_LAT_SPAN / 2.0, mid + NAV_MIN_LAT_SPAN / 2.0
        elif lat_span > NAV_MAX_LAT_SPAN:
            mid = (lat0 + lat1) / 2.0
            lat0, lat1 = mid - NAV_MAX_LAT_SPAN / 2.0, mid + NAV_MAX_LAT_SPAN / 2.0
        lat0 = max(-89.99, lat0)
        lat1 = min(89.99, lat1)
        if lat1 - lat0 < NAV_MIN_LAT_SPAN:
            lat1 = min(89.99, lat0 + NAV_MIN_LAT_SPAN)
            lat0 = lat1 - NAV_MIN_LAT_SPAN
        self._lon0, self._lon1, self._lat0, self._lat1 = lon0, lon1, lat0, lat1

    @staticmethod
    def _wheel_zoom_factor(event) -> float | None:
        """Return the proportional zoom factor for a wheel/trackpad event.

        Prefers high-resolution pixel deltas (trackpads) and falls back to
        angle deltas (physical wheels). Respects platform inversion, honours
        horizontal two-finger scroll as zoom only when vertical input is
        absent, and returns ``None`` when there is no scroll input at all.
        ``factor`` < 1 zooms in.
        """
        pixel = None
        pixel_x, pixel_y = 0, 0
        try:
            pixel = event.pixelDelta()
            pixel_x = pixel.x() if hasattr(pixel, "x") else 0
            pixel_y = pixel.y() if hasattr(pixel, "y") else 0
        except Exception:  # noqa: BLE001 - foreign event spellings zoom by angle
            pixel, pixel_x, pixel_y = None, 0, 0
        angle = None
        angle_x, angle_y = 0, 0
        try:
            angle = event.angleDelta()
            angle_y = angle.y() if hasattr(angle, "y") else 0
            angle_x = angle.x() if hasattr(angle, "x") else 0
        except Exception:  # noqa: BLE001 - an unreadable event zooms nowhere
            return None
        try:
            inverted = bool(event.inverted() if hasattr(event, "inverted")
                            else event.isInverted())
        except Exception:  # noqa: BLE001 - assume conventional direction
            inverted = False
        units = None
        if pixel is not None and (pixel_x or pixel_y):
            units = float(pixel_y if pixel_y else pixel_x)
            units *= 4.0
        elif angle is not None and (angle_x or angle_y):
            units = float(angle_y if angle_y else angle_x)
        if units is None or units == 0:
            return None
        if inverted:
            units = -units
        return float(WHEEL_NOTCH_FACTOR ** (units / WHEEL_NOTCH_UNITS))

    def _zoom_about(self, factor: float, anchor_lon: float, anchor_lat: float,
                    *, push_history: bool = True) -> None:
        """Zoom by ``factor`` keeping ``(anchor_lon, anchor_lat)`` fixed."""
        try:
            factor = float(factor)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(factor) or factor <= 0.0:
            return
        factor = min(4.0, max(0.25, factor))
        if push_history:
            self._push_history()
        self._lon0 = anchor_lon + (self._lon0 - anchor_lon) * factor
        self._lon1 = anchor_lon + (self._lon1 - anchor_lon) * factor
        self._lat0 = anchor_lat + (self._lat0 - anchor_lat) * factor
        self._lat1 = anchor_lat + (self._lat1 - anchor_lat) * factor
        self._clamp_view()

    def _push_history(self) -> None:
        """Record the current extent for previous-view navigation (T17.2)."""
        if getattr(self, "_navigating_history", False):
            return
        back = getattr(self, "_view_back", None)
        forward = getattr(self, "_view_forward", None)
        if back is None or forward is None:
            return
        current = (float(self._lon0), float(self._lon1),
                   float(self._lat0), float(self._lat1))
        if back and back[-1] == current:
            return
        back.append(current)
        if len(back) > NAV_HISTORY_LIMIT:
            del back[:len(back) - NAV_HISTORY_LIMIT]
        forward.clear()
        self._refresh_map_accessible_description()

    def _restore_history_entry(self, bounds) -> None:
        """Apply a history entry without pushing it back (T17.2)."""
        self._navigating_history = True
        try:
            self._lon0, self._lon1, self._lat0, self._lat1 = (
                float(bounds[0]), float(bounds[1]),
                float(bounds[2]), float(bounds[3]))
            self._area_name = ""
            self._clamp_view()
            # A history step supersedes any in-flight gesture preview: its
            # cone and raster belong to the view being left, not the one
            # being restored.
            self._conic_freeze = None
            self._basemap_refresh_timer.stop()
            self._basemap_cache = None
            self._cache_key = None
            self._cache_proj = None
            self._warp_cache = None
            self._warp_proj = None
            self._label_layout_cache = None
            self._outline_cache = None
            self._outline_raster_cache = None
            self._refresh_map_accessible_description()
            self.update()
            self._announce_view_settled()
        finally:
            self._navigating_history = False
        self._refresh_map_accessible_description()

    def can_go_back(self) -> bool:
        """Return whether a previous view exists (T17.2)."""
        return bool(getattr(self, "_view_back", ()))

    def can_go_forward(self) -> bool:
        """Return whether a next view exists (T17.2)."""
        return bool(getattr(self, "_view_forward", ()))

    def go_back(self) -> bool:
        """Restore the previous view, keeping the current one redoable."""
        back = getattr(self, "_view_back", None)
        forward = getattr(self, "_view_forward", None)
        if back is None or forward is None or not back:
            return False
        current = (float(self._lon0), float(self._lon1),
                   float(self._lat0), float(self._lat1))
        forward.append(current)
        self._restore_history_entry(back.pop())
        return True

    def go_forward(self) -> bool:
        """Redo a view undone by :meth:`go_back`."""
        back = getattr(self, "_view_back", None)
        forward = getattr(self, "_view_forward", None)
        if back is None or forward is None or not forward:
            return False
        current = (float(self._lon0), float(self._lon1),
                   float(self._lat0), float(self._lat1))
        back.append(current)
        if len(back) > NAV_HISTORY_LIMIT:
            del back[:len(back) - NAV_HISTORY_LIMIT]
        self._restore_history_entry(forward.pop())
        return True

    def navigation_history_depth(self) -> int:
        """Return the stored previous-view depth (bounded by T17.2)."""
        return len(getattr(self, "_view_back", ()))

    def fit_region(self) -> None:
        """Return to the current named region's default extent (T17.2)."""
        if self._area_name in MAP_AREAS:
            self._push_history()
            self.set_area(self._area_name)
            return
        self._push_history()
        self.reset_view()

    def center_on_selection(self) -> bool:
        """Centre the view on the selected sounding point or station.

        Point maps centre on the sounding point; station maps centre on the
        selected station. The zoom span is preserved, and the extent is
        clamped back into the projectable band. Returns whether the view
        moved. Never changes the selection itself.
        """
        point = getattr(self, "_point_lonlat", None)
        if point is not None:
            try:
                lon, lat = float(point[0]), float(point[1])
            except (TypeError, ValueError, OverflowError):
                return False
            if not (math.isfinite(lon) and math.isfinite(lat)):
                return False
            self._push_history()
            span_lon = (self._lon1 - self._lon0) / 2.0
            span_lat = (self._lat1 - self._lat0) / 2.0
            self._lon0, self._lon1 = lon - span_lon, lon + span_lon
            self._lat0 = max(-89.99, lat - span_lat)
            self._lat1 = min(89.99, lat + span_lat)
            self._clamp_view()
            self._invalidate()
            return True
        selected = getattr(self, "_selected_id", None)
        if selected:
            before = self.view_bounds()
            self._push_history()
            self.center_on(selected)
            return self.view_bounds() != before
        return False

    def center_on_lonlat(self, lon: float, lat: float) -> bool:
        """Centre the view on ``(lon, lat)`` keeping the zoom span (T17.2)."""
        try:
            lon = ((float(lon) + 180.0) % 360.0) - 180.0
            lat = max(-89.99, min(89.99, float(lat)))
        except (TypeError, ValueError, OverflowError):
            return False
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return False
        self._push_history()
        span_lon = (self._lon1 - self._lon0) / 2.0
        span_lat = (self._lat1 - self._lat0) / 2.0
        self._lon0, self._lon1 = lon - span_lon, lon + span_lon
        self._lat0, self._lat1 = lat - span_lat, lat + span_lat
        self._clamp_view()
        self._invalidate()
        return True

    def navigation_actions(self) -> tuple[dict, ...]:
        """Describe the map navigation actions for rails and palettes (T17.2).

        Each entry carries a stable ``id``, a human ``label``, a ``shortcut``
        string for display, a ``tooltip`` explaining the gesture, and a
        ``trigger`` callable. Shortcuts use Ctrl-modified zoom keys so typing
        ``+``/``-`` in a text field never zooms the map; plain ``+``/``-``
        still work when the map itself has focus.
        """
        return (
            {"id": "zoom-in", "label": "Zoom in",
             "shortcut": "Ctrl++", "tooltip": "Zoom in at the map centre (+)",
             "trigger": lambda: self.zoom(0.8)},
            {"id": "zoom-out", "label": "Zoom out",
             "shortcut": "Ctrl+-", "tooltip": "Zoom out from the map centre (-)",
             "trigger": lambda: self.zoom(1.25)},
            {"id": "fit", "label": "Fit region",
             "shortcut": "Ctrl+0", "tooltip": "Return to the region's extent (0)",
             "trigger": self.fit_region},
            {"id": "center-on-selection", "label": "Center on selection",
             "shortcut": "L", "tooltip": "Centre on the selected point (L)",
             "trigger": self.center_on_selection},
            {"id": "previous-view", "label": "Previous view",
             "shortcut": "Alt+Left", "tooltip": "Restore the previous view",
             "trigger": self.go_back},
            {"id": "next-view", "label": "Next view",
             "shortcut": "Alt+Right", "tooltip": "Redo the undone view",
             "trigger": self.go_forward},
        )

    def set_area(self, name: str, *, push_history: bool = False) -> None:
        """Jump to a named region, optionally recording history (T17.2).

        Region-combo and area-control jumps pass ``push_history=True`` so the
        previous view stays recoverable; internal callers such as
        :meth:`fit_region` push explicitly instead to keep one push per
        gesture.
        """
        if name in MAP_AREAS:
            if push_history:
                self._push_history()
            self._area_name = name
            self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[name]
            self._invalidate()

    def set_extent(self, lon0, lon1, lat0, lat1, *, pad=0.2) -> None:
        """Frame an arbitrary lon/lat extent, with proportional padding.

        Complements :meth:`set_area`, which is limited to the named regions. A
        wrapped extent (``lon0 > lon1``) is unrolled eastward so the view keeps
        a single increasing longitude range, which is what the projection needs.
        """
        lon0 = float(lon0)
        lon1 = float(lon1)
        lat0, lat1 = sorted((float(lat0), float(lat1)))
        if lon1 <= lon0:
            lon1 += 360.0
        lon_pad = max(0.05, (lon1 - lon0) * float(pad))
        lat_pad = max(0.05, (lat1 - lat0) * float(pad))
        self._area_name = ""
        self._lon0 = lon0 - lon_pad
        self._lon1 = lon1 + lon_pad
        self._lat0 = max(-89.99, lat0 - lat_pad)
        self._lat1 = min(89.99, lat1 + lat_pad)
        self._invalidate()

    def restore_view_bounds(self, bounds) -> None:
        """Restore a previously captured viewport without adding padding.

        ``set_extent`` intentionally frames new data with a minimum border.
        Reusing it for session restoration compounds that border on every
        save/open cycle, so persisted view bounds have a distinct exact path.
        Never pushes view history: a session restore is not user navigation.
        """
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError("view bounds must contain four values")
        lon0, lon1, lat0, lat1 = (float(value) for value in bounds)
        if not all(math.isfinite(value) for value in (lon0, lon1, lat0, lat1)):
            raise ValueError("view bounds must be finite")
        if lon1 <= lon0 or lat1 <= lat0:
            raise ValueError("view bounds must be increasing")
        if lat0 < -89.99 or lat1 > 89.99:
            raise ValueError("view latitude bounds are out of range")
        self._area_name = ""
        self._lon0, self._lon1 = lon0, lon1
        self._lat0, self._lat1 = lat0, lat1
        self._invalidate()

    def reset_view(self) -> None:
        """Snap back to the current named region's default extent.

        Kept for existing callers; :meth:`fit_region` is the history-aware
        equivalent new navigation chrome should use. A named region restores
        exactly, while a custom view keeps its span but recentres on the
        region's middle so a lost view stays recoverable.
        """
        if self._area_name in MAP_AREAS:
            self.set_area(self._area_name)
            return
        self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[
            "United States (CONUS)"]
        self._invalidate()

    def zoom(self, factor: float) -> None:
        """Zoom about the map center (``factor`` < 1 zooms in)."""
        try:
            factor = float(factor)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(factor) or factor <= 0.0:
            return
        factor = min(4.0, max(0.25, factor))
        self._push_history()
        clon = (self._lon0 + self._lon1) / 2.0
        clat = (self._lat0 + self._lat1) / 2.0
        self._lon0 = clon + (self._lon0 - clon) * factor
        self._lon1 = clon + (self._lon1 - clon) * factor
        self._lat0 = clat + (self._lat0 - clat) * factor
        self._lat1 = clat + (self._lat1 - clat) * factor
        self._clamp_view()
        self._invalidate()

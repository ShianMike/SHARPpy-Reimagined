"""Map labels, scale bars, and presentation preferences."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPen

from sharpmod.ui.features.gui_theme import current_theme, mono_font, ui_font
from sharpmod.maps.map_geography import (
    declutter_labels, format_scale_length, km_per_pixel_at_latitude,
    labels_visible_for_span, normalise_label_density, pick_scale_length,
)
from sharpmod.ui.styles.theme import MapPalette, map_palette
from sharpmod.ui.maps.navigation import MAP_PROJECTIONS


def _map() -> MapPalette:
    """Return the map palette paired with the active chrome theme.

    Resolved on each call rather than cached on the widget, because the theme
    can change at runtime (File -> Preferences) and the maps repaint in response
    without being rebuilt.
    """
    return map_palette(current_theme())


def _estimate_label_widths(labels) -> dict:
    """Estimate caption-font widths without touching Qt text metrics.

    Uses a per-character average scaled by the active interface text size, so
    headless tests and paint-time declutter agree without constructing a
    ``QFontMetrics`` (which crashed the suite when built off-widget). The
    painter still measures the real width when drawing; this only reserves
    space during declutter.
    """
    try:
        from sharpmod.ui.features.gui_theme import current_font_text_scale
        scale = int(current_font_text_scale())
    except (ImportError, TypeError, ValueError, OverflowError):
        scale = 100
    per_char = 6.5 * max(1.0, scale / 100.0)
    return {str(label): float(len(str(label))) * per_char for label in labels}


def _estimated_label_height(base: float) -> float:
    """Return a conservative caption height at the active UI text scale."""
    try:
        from sharpmod.ui.features.gui_theme import current_font_text_scale
        scale = int(current_font_text_scale())
    except (ImportError, TypeError, ValueError, OverflowError):
        scale = 100
    return float(base) * max(1.0, scale / 100.0)


class MapPresentationMixin:
    """Labels and presentation state shared by map widgets."""

    def label_density(self) -> str:
        """Return the place-label density: off/sparse/standard/dense."""
        return normalise_label_density(getattr(self, "_label_density", "standard"))

    def set_label_density(self, density) -> None:
        """Choose how many bundled place labels are drawn (T16.2)."""
        wanted = normalise_label_density(density)
        if wanted == getattr(self, "_label_density", "standard"):
            return
        self._label_density = wanted
        self._label_layout_cache = None
        self._refresh_map_accessible_description()
        self.update()

    def pinned_place(self):
        """Return the pinned ``(name, lon, lat)`` label, or ``None``."""
        return getattr(self, "_pinned_place", None)

    def set_pinned_place(self, name, lon=None, lat=None) -> None:
        """Pin one place label so it survives the declutter (T16.2).

        ``set_pinned_place(None)`` clears it. The pin names the selected,
        hovered, or explicitly pinned location; ordinary town labels never
        displace it.
        """
        if name is None or (lon is None and lat is None):
            self._pinned_place = None
            self._label_layout_cache = None
            self._refresh_map_accessible_description()
            self.update()
            return
        try:
            lon = ((float(lon) + 180.0) % 360.0) - 180.0
            lat = float(lat)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(lon) or not math.isfinite(lat):
            return
        if not -90.0 <= lat <= 90.0 or not str(name).strip():
            return
        self._pinned_place = (str(name).strip(), lon, lat)
        self._label_layout_cache = None
        self._refresh_map_accessible_description()
        self.update()

    def _label_protected_points(self, p) -> list:
        """Marker positions ordinary labels must not overprint (T16.2)."""
        protected = []
        # Pointer hover is deliberately absent: it changes every mouse move and
        # must not make the whole town layout churn or visually jump. The hover
        # readout/label paints later and remains visible without rearranging the
        # base geography.
        for lonlat in (getattr(self, "_point_lonlat", None),):
            if lonlat is None:
                continue
            try:
                protected.append(self._to_px(float(lonlat[0]), float(lonlat[1]), p))
            except (TypeError, ValueError, OverflowError):
                continue
        for station in getattr(self, "_stations", ()):
            try:
                if station.get("id") not in {
                    self._selected_id,
                    self._pinned_station_id(),
                }:
                    continue
                protected.append(self._to_px(station["lon"], station["lat"], p))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        pinned = getattr(self, "_pinned_place", None)
        if pinned is not None:
            try:
                protected.append(self._to_px(pinned[1], pinned[2], p))
            except (TypeError, ValueError, OverflowError):
                pass
        return [
            (point.x(), point.y()) for point in protected
            if math.isfinite(point.x()) and math.isfinite(point.y())
        ]

    def _protected_station_label_rects(self, p, *, include_hover=False) -> tuple:
        """Text rectangles reserved for selected/pinned station labels."""
        wanted = {
            item for item in (
                self._selected_id,
                self._pinned_station_id(),
                self._hover_id if include_hover else None,
            ) if item
        }
        if not wanted:
            return ()
        labels = []
        for station in self._stations:
            try:
                station_id = str(station["id"])
                if station_id not in wanted:
                    continue
                point = self._to_px(float(station["lon"]), float(station["lat"]), p)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            name = str(station.get("name") or "").strip()
            labels.append((point.x(), point.y(),
                           f"{station_id} {name}".strip()))
        return self._label_rectangles(
            labels, x_offset=6.0, height=_estimated_label_height(15.0))

    def _label_rectangles(self, labels, *, x_offset=5.0,
                          height=None) -> tuple:
        """Return the exact clipped text rectangles used by map labels."""
        if height is None:
            height = _estimated_label_height(14.0)
        height = max(1.0, float(height))
        widths = _estimate_label_widths(label for _x, _y, label in labels)
        result = []
        for x, y, label in labels:
            extent = float(widths.get(str(label), 0.0))
            draw_x = min(float(x) + x_offset,
                         max(0.0, self.width() - extent - 4.0))
            result.append((draw_x, float(y) - height + 1.0, extent + 8.0,
                           height))
        return tuple(result)

    def place_labels(self) -> tuple:
        """Return the decluttered ``(x, y, label)`` place labels (T16.2).

        Cached per view: recomputing the declutter on every repaint costs a
        full pass over the place index per frame, which is what made pans and
        hovers stutter once labels existed. Callers that change a declutter
        input (view, size, density, scale, selection, pins) clear
        :attr:`_label_layout_cache`; paint paths read through
        :meth:`cached_place_labels` so they never pay for a stale layout.
        """
        return self.cached_place_labels()

    def _label_layout_key(self) -> tuple:
        """Everything the decluttered layout depends on. See :meth:`place_labels`."""
        try:
            from sharpmod.ui.features.gui_theme import current_font_text_scale
            scale = int(current_font_text_scale())
        except (ImportError, TypeError, ValueError, OverflowError):
            scale = 100
        pinned = getattr(self, "_pinned_place", None)
        return (
            self._projection,
            round(self._lon0, 4), round(self._lon1, 4),
            round(self._lat0, 4), round(self._lat1, 4),
            self.width(), self.height(),
            round(self.devicePixelRatioF(), 4),
            self._conic_freeze,
            self.label_density(),
            scale,
            self._selected_id, self._pinned_station_id(),
            tuple(pinned) if pinned is not None else None,
            self._point_lonlat if hasattr(self, "_point_lonlat") else None,
        )

    def cached_place_labels(self) -> tuple:
        """Return the decluttered layout, recomputing only when stale."""
        key = self._label_layout_key()
        cached = self._label_layout_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        if (cached is not None and len(cached) > 2
                and self._basemap_refresh_timer.isActive()
                and cached[0][0] == key[0]
                and cached[0][5:8] == key[5:8]
                and cached[0][9:] == key[9:]):
            previous = cached[2]
            current = self._proj()
            if previous.cone == current.cone:
                destination, source = self._reblit_geometry(
                    previous, current, self.width(), self.height())
                scale_x = destination.width() / source.width()
                scale_y = destination.height() / source.height()
                return tuple((destination.x() + x * scale_x,
                              destination.y() + y * scale_y, label)
                             for x, y, label in cached[1])
        labels = self._compute_place_labels()
        self._label_layout_cache = (key, labels, self._proj())
        return labels

    def _compute_place_labels(self) -> tuple:
        """Compute the decluttered ``(x, y, label)`` layout. See :meth:`place_labels`."""
        from sharpmod.maps.place_names import _conus_place_cells

        density = self.label_density()
        lon_span = abs(self._lon1 - self._lon0)
        show_major, show_towns, show_smalls = labels_visible_for_span(
            density, lon_span)
        p = self._proj()
        width, height = self.width(), self.height()
        vlon0, vlon1 = min(self._lon0, self._lon1), max(self._lon0, self._lon1)
        vlat0, vlat1 = min(self._lat0, self._lat1), max(self._lat0, self._lat1)
        # Major metros stay in curated population order. Towns are read only
        # from visible one-degree cells and reduced to a 4x4 geographic sample
        # per cell before projection. A regional view can contain 15k Census
        # records; projecting all of them took ~30 ms on every settled zoom even
        # though only 40 could ever be drawn.
        candidates = []
        cells = {}
        if show_major or show_towns or show_smalls:
            from sharpmod.maps.map_geography import MAJOR_CITIES

            for label, place_lat, place_lon in MAJOR_CITIES:
                if not (vlon0 <= place_lon <= vlon1
                        and vlat0 <= place_lat <= vlat1):
                    continue
                try:
                    point = self._to_px(place_lon, place_lat, p)
                except Exception:  # noqa: BLE001 - one bad record skips
                    continue
                if not (0 <= point.x() <= width and 0 <= point.y() <= height):
                    continue
                candidates.append((1, point.x(), point.y(), str(label)))
            if show_towns or show_smalls:
                try:
                    cells = _conus_place_cells()
                except Exception:  # noqa: BLE001 - labels never break the map
                    cells = {}
        for lat_cell in range(math.floor(vlat1), math.floor(vlat0) - 1, -1):
            for lon_cell in range(math.floor(vlon0), math.floor(vlon1) + 1):
                records = cells.get((lat_cell, lon_cell), ())
                sampled = {}
                for label, place_lat, place_lon, kind, land_area in records:
                    if not (vlon0 <= place_lon <= vlon1
                            and vlat0 <= place_lat <= vlat1):
                        continue
                    try:
                        area = float(land_area or 0.0)
                    except (TypeError, ValueError, OverflowError):
                        area = 0.0
                    # Towns once the view closes in; small places and
                    # subdivisions only when the reader asked for dense at a
                    # close zoom. Major metros come from the curated list
                    # above, never from an area cutoff, so no branch here may
                    # admit a tier-1 label.
                    if kind != "place":
                        if not show_smalls:
                            continue
                        tier = 3
                    elif show_towns:
                        tier = 2
                    elif show_smalls and area < 5.0:
                        tier = 3
                    else:
                        continue
                    subcell = (
                        max(0, min(3, int((place_lat - lat_cell) * 4.0))),
                        max(0, min(3, int((place_lon - lon_cell) * 4.0))),
                    )
                    previous = sampled.get(subcell)
                    score = (tier == 2, area, str(label))
                    if previous is None or score > previous[0]:
                        sampled[subcell] = (
                            score,
                            (label, place_lat, place_lon, tier),
                        )
                for _score, record in sampled.values():
                    label, place_lat, place_lon, tier = record
                    try:
                        point = self._to_px(place_lon, place_lat, p)
                    except Exception:  # noqa: BLE001 - one bad record skips
                        continue
                    if not (0 <= point.x() <= width and 0 <= point.y() <= height):
                        continue
                    candidates.append((tier, point.x(), point.y(), str(label)))
        pinned = getattr(self, "_pinned_place", None)
        if pinned is not None:
            try:
                point = self._to_px(pinned[1], pinned[2], p)
                if 0 <= point.x() <= width and 0 <= point.y() <= height:
                    candidates.append((0, point.x(), point.y(), pinned[0]))
            except Exception:  # noqa: BLE001 - a bad pin never breaks paint
                pass
        kept = declutter_labels(
            candidates, protected=self._label_protected_points(p),
            protected_rects=self._protected_station_label_rects(p),
            min_separation_px=self._label_separation_px(),
            limit=self._label_max_count(),
            label_widths=_estimate_label_widths(
                tuple(str(label) for _, _, _, label in candidates)),
            viewport_size=(width, height))
        return tuple((x, y, label) for _importance, x, y, label in kept)

    def _label_max_count(self) -> int:
        """Return the label cap for the active text size (T16.2)."""
        from sharpmod.maps.map_geography import (
            LABEL_MAX_COUNT, LABEL_MAX_COUNT_LARGE_TEXT,
        )

        try:
            from sharpmod.ui.features.gui_theme import current_font_text_scale
            scale = int(current_font_text_scale())
        except (ImportError, TypeError, ValueError, OverflowError):
            return int(LABEL_MAX_COUNT)
        if scale >= 200:
            return int(LABEL_MAX_COUNT_LARGE_TEXT)
        if scale >= 150:
            return int((LABEL_MAX_COUNT + LABEL_MAX_COUNT_LARGE_TEXT) // 2)
        return int(LABEL_MAX_COUNT)

    def _label_separation_px(self) -> float:
        """Return the declutter separation for the active text size (T16.2)."""
        from sharpmod.maps.map_geography import (
            LABEL_MIN_SEPARATION_PX, LABEL_MIN_SEPARATION_PX_LARGE_TEXT,
        )

        try:
            from sharpmod.ui.features.gui_theme import current_font_text_scale
        except ImportError:  # pragma: no cover - theme module is local
            return float(LABEL_MIN_SEPARATION_PX)
        try:
            scale = int(current_font_text_scale())
        except (TypeError, ValueError, OverflowError):
            return float(LABEL_MIN_SEPARATION_PX)
        if scale >= 200:
            return float(LABEL_MIN_SEPARATION_PX_LARGE_TEXT)
        if scale >= 150:
            return float(LABEL_MIN_SEPARATION_PX_LARGE_TEXT - 24.0)
        return float(LABEL_MIN_SEPARATION_PX)

    def _draw_place_labels(self, qp, p) -> None:
        """Draw decluttered place labels above weather, below markers (T16.2).

        Labels are clipped away from the legend, scale bar, and readout
        corners: those captions are drawn later in the same frame, so a town
        name placed under them would either overprint chrome or be overprinted
        by it. The readout owns the top strip, the legend the bottom strip,
        the scale bar the bottom-right block.
        """
        labels = self.place_labels()
        if not labels:
            return
        palette = _map()
        width, height = self.width(), self.height()
        # T20: the legend moves, so the label clip follows the last painted
        # layout rather than assuming bottom-left. Unknown layout keeps the old
        # bottom-strip reserve rather than risking overprint.
        info = self.legend_layout_info()
        if info.get("bar") is not None or info.get("corner") != "bottom-left":
            legend_reserve = 16.0
            legend_box = self._legend_clip_box()
        else:
            legend_reserve = 60.0 if self._visible_overlays() or self._visible_rasters() else 16.0
            legend_box = None
        try:
            scale_px = float(self.scale_bar_info().get("length_px", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            scale_px = 0.0
        scale_top = height - 92.0
        scale_left = width - scale_px - 70.0
        station_rects = self._protected_station_label_rects(
            p, include_hover=True)
        visible = []
        qp.save()
        qp.setFont(ui_font("caption"))
        line_height = max(
            14.0, math.ceil(qp.fontMetrics().height() + 1.0))
        for x, y, label in labels:
            if y <= 20.0 or y >= height - legend_reserve:
                continue
            text_w = qp.fontMetrics().horizontalAdvance(label)
            draw_x = min(x + 5.0, max(0.0, width - text_w - 4.0))
            text_rect = (
                draw_x, y - line_height + 1.0, text_w + 8.0, line_height)

            def intersects(box, pad=4.0):
                if box is None:
                    return False
                bx, by, bw, bh = box
                return (
                    text_rect[0] < bx + bw + pad
                    and text_rect[0] + text_rect[2] + pad > bx
                    and text_rect[1] < by + bh + pad
                    and text_rect[1] + text_rect[3] + pad > by
                )

            if y >= scale_top and text_rect[0] + text_rect[2] >= scale_left:
                continue
            if intersects(legend_box) or any(
                    intersects(rect, pad=3.0) for rect in station_rects):
                continue
            visible.append((x, y, label, text_w, draw_x))
        if not visible:
            qp.restore()
            return
        for x, y, label, text_w, draw_x in visible:
            # Anchor east-edge labels inside the widget: a metro on the coast
            # would otherwise start its name off-screen and read truncated.
            for pen, offset in (
                (QPen(QColor(palette.readout_shadow)), 1.0),
                (QPen(QColor(palette.readout_text)), 0.0),
            ):
                qp.setPen(pen)
                qp.drawText(
                    QRectF(
                        draw_x + offset, y - line_height + 1.0 + offset,
                        text_w + 8.0, line_height),
                    Qt.AlignLeft | Qt.AlignVCenter, label,
                )
        qp.restore()
        self._last_place_labels = tuple(
            (x, y, label) for x, y, label, _text_w, _draw_x in visible)

    def presentation_state(self) -> dict:
        """Return the persisted map presentation (T16.4, T20 legend, T21 mode).

        Only presentation: label density, scale units, legend corner/collapse,
        fixed-scale locks, and the live/history mode. Never scientific layer
        values, selections, run/hour choices, or view bounds -- those belong
        to sessions and map state, not to how geography looks.
        """
        return {
            "label_density": self.label_density(),
            "scale_units": self.scale_units(),
            "legend": self.legend_state(),
            "mode": self.map_mode(),
        }

    def restore_presentation_preferences(self, state) -> None:
        """Restore persisted presentation, ignoring unknown values (T16.4)."""
        if not isinstance(state, dict):
            return
        if "label_density" in state:
            # Unknown density names keep the current choice: a hand-edited or
            # future value must not silently reset the reader's setting.
            try:
                from sharpmod.maps.map_geography import LABEL_DENSITY_LEVELS
            except ImportError:  # pragma: no cover - module is local
                LABEL_DENSITY_LEVELS = ("off", "sparse", "standard", "dense")
            wanted = str(state.get("label_density") or "").strip().lower()
            if wanted in LABEL_DENSITY_LEVELS:
                self.set_label_density(wanted)
        if "scale_units" in state:
            self.set_scale_units(state.get("scale_units"))
        if "legend" in state:
            try:
                self.restore_legend_state(state.get("legend"))
            except Exception:  # noqa: BLE001 - legend never blocks maps
                pass
        if "mode" in state:
            try:
                self.set_map_mode(state.get("mode"))
            except Exception:  # noqa: BLE001 - mode never blocks maps
                pass
        self.update()

    def scale_units(self) -> str:
        """Return ``"metric"`` (km) or ``"imperial"`` (mi)."""
        return "imperial" if getattr(self, "_scale_units", "metric") == "imperial" \
            else "metric"

    def set_scale_units(self, units) -> None:
        """Choose the scale-bar units without changing the measured distance."""
        wanted = "imperial" if str(units or "").strip().lower() in {
            "imperial", "mi", "miles", "in"} else "metric"
        if wanted == getattr(self, "_scale_units", "metric"):
            return
        self._scale_units = wanted
        self._refresh_map_accessible_description()
        self.update()

    def scale_bar_info(self) -> dict:
        """Return the scale bar's ground truth for this view (T16.3).

        ``km_per_pixel`` is measured from the live projection at the view's
        centre latitude -- the flat view's cosine squeeze or the conic's local
        scale -- never from an assumed constant screen distance. A flat view
        squeezes every parallel by the *view centre*, so the bar measures that
        same latitude rather than claiming a different parallel's scale.
        ``length_km`` is the snapped nice length; ``label`` is units-facing.
        """
        imperial = self.scale_units() == "imperial"
        p = self._proj()
        mid_lat = (self._lat0 + self._lat1) / 2.0
        mid_lon = (self._lon0 + self._lon1) / 2.0
        try:
            centre = p.forward(mid_lon, mid_lat)
            east = p.forward(mid_lon + 0.5, mid_lat)
        except Exception:  # noqa: BLE001 - a limit point has no scale
            return {
                "length_km": 0.0, "length_px": 0.0, "km_per_pixel": float("nan"),
                "label": "Scale unavailable at this view",
                "projection": self._projection,
                "imperial": imperial,
            }
        pixels_per_half_degree = abs(east[0] - centre[0])
        if not math.isfinite(pixels_per_half_degree) \
                or pixels_per_half_degree <= 1e-9:
            return {
                "length_km": 0.0, "length_px": 0.0, "km_per_pixel": float("nan"),
                "label": "Scale unavailable at this view",
                "projection": self._projection,
                "imperial": imperial,
            }
        half_degree_km = math.radians(0.5) * math.cos(
            math.radians(max(-89.0, min(89.0, mid_lat)))) * 6371.0088
        if p.affine:
            # Cross-check against the documented flat-view contract: the two
            # must agree, or one of them drifted from the projection.
            contract = km_per_pixel_at_latitude(
                abs(self._lon1 - self._lon0), max(1, self.width()), mid_lat)
            measured = half_degree_km / (pixels_per_half_degree or 1.0)
            km_per_pixel = measured if math.isfinite(contract) and abs(
                measured - contract) / max(contract, 1e-9) < 0.05 \
                else measured
        else:
            km_per_pixel = half_degree_km / pixels_per_half_degree
        length, length_px, unit = pick_scale_length(
            km_per_pixel, imperial=imperial)
        if length <= 0.0:
            return {
                "length_km": 0.0, "length_px": 0.0,
                "km_per_pixel": km_per_pixel,
                "label": "Scale unavailable at this view",
                "projection": self._projection,
                "imperial": imperial,
            }
        return {
            "length_km": float(length * (1.609344 if imperial else 1.0)),
            "length_px": float(length_px),
            "km_per_pixel": float(km_per_pixel),
            "label": format_scale_length(length, unit),
            "projection": self._projection,
            "imperial": imperial,
        }

    def _draw_scale_bar(self, qp, p) -> None:
        """Draw the distance scale and projection context (T16.3).

        Bottom-right unless the legend sits there (T20.1): the legend owns
        whichever corner it chose, and the bar yields to it rather than
        overprinting. Text blocks are clipped to the widget so a long
        projection name cannot run past the east edge the way the first 200%
        capture showed.
        """
        # Detached export figures repeat this exact scale and orientation in
        # their non-eliding context block. Suppress the in-canvas copy there so
        # it cannot cross a colour legend in a short multi-panel cell. Live
        # maps retain the established visual scale bar.
        if bool(getattr(self, "_export_suppress_scale_bar", False)):
            return
        info = self.scale_bar_info()
        palette = _map()
        qp.save()
        qp.setFont(mono_font("caption"))
        margin = 10.0
        bar_y = self.height() - 52.0
        # T20.1: when the legend takes the bottom-right corner, the bar moves
        # to the bottom-left so the two never overprint. Any other corner (or
        # no legend at all) keeps the historical bottom-right placement. Only
        # the *bottom* half of the corner matters: a top-right legend leaves
        # the bar where it has always been.
        legend_box = self._legend_clip_box()
        legend_bottom_right = False
        if legend_box is not None and legend_box[2] > 0 and legend_box[3] > 0:
            lx, ly, lw, lh = legend_box
            legend_bottom_right = (lx + lw > self.width() / 2.0
                                   and ly + lh > self.height() / 2.0)
        elif (self._visible_overlays() or self._visible_rasters()
              or self.profile_marker_key()):
            # First painted frame has no prior legend rectangle. Use the stable
            # preference immediately so scale and legend never overlap for one
            # frame before a hover happens to trigger another repaint.
            legend_bottom_right = self.legend_corner() in ("", "bottom-right")
        bar_right = legend_bottom_right
        if info["length_px"] <= 0.0:
            text = info["label"]
            width = qp.fontMetrics().horizontalAdvance(text) + 16.0
            x = margin if bar_right else self.width() - width - margin
            for pen, offset in (
                (QPen(QColor(palette.readout_shadow)), 1.0),
                (QPen(QColor(palette.readout_text)), 0.0),
            ):
                qp.setPen(pen)
                qp.drawText(
                    QRectF(x + offset, bar_y - 14.0 + offset, width, 14.0),
                    Qt.AlignRight | Qt.AlignVCenter, text)
            qp.restore()
            return
        bar_w = info["length_px"]
        x = margin if bar_right else self.width() - bar_w - margin
        qp.setPen(QPen(QColor(palette.readout_text), 2.0))
        qp.drawLine(QPointF(x, bar_y), QPointF(x + bar_w, bar_y))
        qp.drawLine(QPointF(x, bar_y - 5.0), QPointF(x, bar_y + 1.0))
        qp.drawLine(QPointF(x + bar_w, bar_y - 5.0), QPointF(x + bar_w, bar_y + 1.0))
        # Alternating halves so the bar reads at a glance, not just its label.
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor(palette.readout_text)))
        qp.drawRect(QRectF(x, bar_y - 4.0, bar_w / 2.0, 4.0))
        for pen, offset in (
            (QPen(QColor(palette.readout_shadow)), 1.0),
            (QPen(QColor(palette.readout_text)), 0.0),
        ):
            qp.setPen(pen)
            label_rect = QRectF(
                max(margin, x + offset - 60.0), bar_y - 22.0 + offset,
                min(bar_w + 60.0, self.width() - margin
                    - max(margin, x + offset - 60.0)), 14.0)
            qp.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, info["label"])
            effective = self.effective_projection()
            projection_name = (
                "Lambert conformal conic"
                if effective == "curved" else "Equirectangular")
            if self._projection in MAP_PROJECTIONS and effective != self._projection:
                projection_name += " (curved unavailable here)"
            orientation = (
                f"North is up · {projection_name}" if p.affine
                else f"North varies · {projection_name}")
            align = Qt.AlignLeft | Qt.AlignVCenter if bar_right \
                else Qt.AlignRight | Qt.AlignVCenter
            qp.drawText(
                QRectF(margin + offset, bar_y - 36.0 + offset,
                       self.width() - 2.0 * margin, 14.0),
                align, orientation)
        qp.restore()
        self._last_scale_bar = info

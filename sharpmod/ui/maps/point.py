"""Point-map interaction, inspection, and paint behavior."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import QMenu

from sharpmod.ui.features.gui_theme import mono_font, ui_font
from sharpmod.ui.maps.presentation import _map
from sharpmod.ui.maps.navigation import PAN_BUTTONS, SECONDARY_PAN_BUTTONS

MAP_TOOLS = ("select", "inspect", "box")

#: Smallest drag, in pixels, that counts as drawing a box rather than clicking.
#: Applied in screen space rather than degrees so the threshold means the same
#: thing at every zoom level.
MIN_BOX_DRAG_PX = 6.0


def _inspection_run_iso(run) -> str:
    """Return the ISO run stamp carried on an inspection snapshot."""
    try:
        return str(run.isoformat())
    except AttributeError:
        return str(run)


def _inspection_valid_iso(run, fxx) -> str:
    """Return the ISO valid stamp carried on an inspection snapshot."""
    try:
        from datetime import timedelta as _timedelta

        return str((run + _timedelta(hours=int(fxx))).isoformat())
    except Exception:  # noqa: BLE001 - valid stamps are advisory
        return ""


def _inspection_run_value(run_iso):
    """Return a datetime for an ISO run stamp, or the raw value."""
    try:
        from datetime import datetime as _datetime

        return _datetime.fromisoformat(str(run_iso))
    except (TypeError, ValueError, OverflowError):
        return run_iso


class PointMapMixin:
    """Point selection and field inspection on a forecast map."""

    def _allowed_tools(self) -> tuple[str, ...]:
        return MAP_TOOLS

    def set_map_tool(self, tool) -> None:
        """Choose the active tool; ``box`` arms sticky box drawing (T18.1)."""
        from sharpmod.maps.map_field_inspection import normalise_tool

        wanted = normalise_tool(tool, allow_box=True)
        if wanted == getattr(self, "_map_tool", "select"):
            if wanted == "box" and not self._box_mode:
                self.set_box_mode(True)
            return
        self._map_tool = wanted
        if wanted == "box":
            if not self._box_mode:
                self.set_box_mode(True)
        elif self._box_mode:
            self.set_box_mode(False)
        self._refresh_tool_chrome()
        self.update()

    def tool_hint_text(self) -> str:
        """Return the one-line hint describing the active tool (T18.1)."""
        gestures = ("middle/right-drag always pans), right-click for zoom, "
                    "fit, centre, and view history.")
        tool = getattr(self, "_map_tool", "select")
        if tool == "inspect":
            return ("Inspect: hover to read values, click to pin a card, "
                    "double-click does nothing — inspecting never moves the "
                    "sounding selection. Scroll to zoom at the cursor, drag "
                    "to pan (" + gestures)
        if tool == "box":
            return ("Draw box: drag a rectangle to preview an area; drag a "
                    "handle to resize or its interior to move it. Review & "
                    "extract is a separate action. A click still places the "
                    "sounding point. Shift-drag draws in any tool. Scroll to "
                    "zoom, drag to pan (" + gestures)
        return super().tool_hint_text()

    def has_recent_point(self) -> bool:
        """Whether a reversible previous selection exists (T18.1)."""
        recent = getattr(self, "_recent_point", None)
        return recent is not None

    def recent_point(self) -> tuple[float, float] | None:
        """Return the reversible previous selection as ``(lat, lon)``."""
        recent = getattr(self, "_recent_point", None)
        if recent is None:
            return None
        try:
            return (float(recent[1]), float(recent[0]))
        except (TypeError, ValueError, OverflowError, IndexError):
            return None

    def restore_recent_point(self) -> bool:
        """Return to the previous selection without touching history (T18.1)."""
        recent = getattr(self, "_recent_point", None)
        if recent is None:
            return False
        try:
            lon, lat = float(recent[0]), float(recent[1])
        except (TypeError, ValueError, OverflowError):
            return False
        if self.is_point_locked():
            return False
        self._recent_point = None
        self.set_point(lat, lon)
        self.pointSelected.emit(float(lat), float(lon))
        return True

    def set_inspection_product(self, product_key, *, run=None,
                               fxx: int | None = None) -> None:
        """Choose which numeric field inspection reads (T18.4)."""
        if not product_key or fxx is None or run is None:
            self._inspection_source = None
            return
        try:
            hour = int(fxx)
        except (TypeError, ValueError, OverflowError):
            self._inspection_source = None
            return
        self._inspection_source = (str(product_key), run, hour)

    def inspection_product(self) -> tuple | None:
        """Return the active ``(product_key, run, fxx)`` source, if any."""
        source = getattr(self, "_inspection_source", None)
        return tuple(source) if source is not None else None

    def crosshair(self) -> tuple[float, float] | None:
        """Return the shared crosshair location as ``(lat, lon)``, if any."""
        crosshair = getattr(self, "_crosshair_lonlat", None)
        if crosshair is None:
            return None
        try:
            latitude, longitude = float(crosshair[0]), float(crosshair[1])
        except (TypeError, ValueError, OverflowError, IndexError):
            return None
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            return None
        return (latitude, longitude)

    def set_crosshair(self, lat, lon) -> None:
        """Mirror a sibling panel's hovered location without moving state."""
        try:
            latitude = float(lat)
            longitude = float(lon)
        except (TypeError, ValueError, OverflowError):
            return
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            return
        self._crosshair_lonlat = (float(latitude), float(longitude))
        overlay = getattr(self, "_crosshair_overlay", None)
        if overlay is None:
            self.update()
        else:
            overlay.refresh()

    def clear_crosshair(self) -> None:
        """Drop the shared crosshair, if any."""
        if getattr(self, "_crosshair_lonlat", None) is not None:
            self._crosshair_lonlat = None
            overlay = getattr(self, "_crosshair_overlay", None)
            if overlay is None:
                self.update()
            else:
                overlay.refresh()

    def crosshair_text(self) -> str:
        """Return this panel's value at the shared crosshair (T24.1).

        Each panel reports its *own* field, units, actual valid time, and
        unavailable reason at one shared geographic location. Missing state
        is truthful: imagery-only, outside-domain, and no-value each read
        differently, and a panel with no depicted product says so instead
        of borrowing a sibling's number.
        """
        crosshair = self.crosshair()
        if crosshair is None:
            return ""
        lat, lon = crosshair
        try:
            sample = self.sample_field_at(float(lat), float(lon))
        except Exception:  # noqa: BLE001 - crosshair text is advisory
            return ""
        from sharpmod.maps.map_field_inspection import format_field_value

        label = str(getattr(sample, "label", "") or
                    getattr(sample, "product_key", "") or "field").strip()
        units = str(getattr(sample, "units", "") or "").strip()
        units_text = f" {units}" if units else ""
        valid = ""
        run_iso = str(getattr(sample, "run_iso", "") or "")
        fxx = getattr(sample, "fxx", None)
        if run_iso and fxx is not None:
            try:
                from sharpmod.providers import hrrr_field

                valid = str(hrrr_field.field_valid_label(
                    _inspection_run_value(run_iso), int(fxx)) or "")
            except (AttributeError, TypeError, ValueError, OverflowError):
                valid = ""
        if getattr(sample, "imagery_only", False):
            if not getattr(sample, "covered", True):
                return (f"Crosshair {lat:.3f}, {lon:.3f} — "
                        f"{label or 'field'}: outside model coverage")
            return (f"Crosshair {lat:.3f}, {lon:.3f} — "
                    f"{label or 'field'}: imagery only, no numeric value")
        if not getattr(sample, "covered", True):
            return (f"Crosshair {lat:.3f}, {lon:.3f} — "
                    f"{label or 'field'}: outside model coverage")
        if not getattr(sample, "has_value", False):
            reason = str(getattr(sample, "reason", "") or "")
            if reason in ("no-field", "unknown-product"):
                return (f"Crosshair {lat:.3f}, {lon:.3f} — "
                        f"{label or 'field'}: no field depicted")
            return (f"Crosshair {lat:.3f}, {lon:.3f} — "
                    f"{label or 'field'}: no value at this grid cell")
        try:
            number = format_field_value(float(sample.value))
        except (TypeError, ValueError, OverflowError):
            return (f"Crosshair {lat:.3f}, {lon:.3f} — "
                    f"{label or 'field'}: no value at this grid cell")
        note = str(getattr(sample, "display_note", "") or "").strip()
        text = (f"Crosshair {lat:.3f}, {lon:.3f} — {label or 'field'} "
                f"{number}{units_text}")
        if note:
            text += f" ({note})"
        if valid:
            text += f" · {valid}"
        return text

    def crosshair_accessible_text(self) -> str:
        """Return the spoken shared-crosshair equivalent (T24.1).

        Names each panel's value without needing the pixels; the chart
        information equivalent T01.3 requires for any meaningful map state.
        """
        text = self.crosshair_text()
        if not text:
            return ""
        try:
            product = self.inspection_product()
        except (AttributeError, RuntimeError):
            product = None
        if product:
            return f"{text} · field {product[0]}"
        return text

    def _legend_avoid_points_extra(self, p) -> list[tuple[float, float]]:
        """Return extra legend-avoid points; crosshair guide on panels."""
        crosshair = self.crosshair() if hasattr(self, "crosshair") else None
        if crosshair is None:
            return []
        try:
            point = self._to_px(float(crosshair[1]), float(crosshair[0]), p)
        except (AttributeError, RuntimeError, TypeError, ValueError,
                OverflowError):
            return []
        try:
            return [(float(point.x()), float(point.y()))]
        except (TypeError, ValueError, OverflowError):
            return []

    def sample_field_at(self, lat: float, lon: float):
        """Sample the active numeric field at ``lat``/``lon`` (T18.4)."""
        from sharpmod.maps.map_field_inspection import FieldSample, imagery_only_sample

        source = getattr(self, "_inspection_source", None)
        if source is None:
            rasters = bool(self._visible_rasters())
            return imagery_only_sample(lat, lon, rasters_visible=rasters)
        try:
            product_key, run, fxx = source
        except (TypeError, ValueError):
            return imagery_only_sample(lat, lon, rasters_visible=False)
        try:
            from sharpmod.providers import hrrr_field, hrrr_products

            product = hrrr_products.get_product(product_key)
            if str(getattr(product, "key", "")) != str(product_key):
                rasters = bool(self._visible_rasters())
                return imagery_only_sample(lat, lon, rasters_visible=rasters)
            value, sampled_lat, sampled_lon, distance, covered, note = (
                hrrr_field.sample_derived(product.key, run, int(fxx), lat, lon))
            if not covered:
                return FieldSample(
                    product_key=product.key, label=product.label,
                    requested_lat=float(lat), requested_lon=float(lon),
                    sampled_lat=float(lat), sampled_lon=float(lon),
                    distance_km=0.0, covered=False, reason="outside-domain")
            if value is None:
                rasters = bool(self._visible_rasters())
                if not hrrr_field.has_derived(product.key, run, int(fxx),
                                              hrrr_field.DEFAULT_FRAME_SIZE):
                    return imagery_only_sample(lat, lon,
                                               rasters_visible=rasters)
                return FieldSample(
                    product_key=product.key, label=product.label,
                    units=product.palette.units,
                    run_iso=_inspection_run_iso(run), fxx=int(fxx),
                    valid_iso=_inspection_valid_iso(run, int(fxx)),
                    requested_lat=float(lat), requested_lon=float(lon),
                    sampled_lat=float(sampled_lat), sampled_lon=float(sampled_lon),
                    distance_km=float(distance), covered=True, reason="no-data",
                    resolution_km=3.0)
            return FieldSample(
                product_key=product.key, label=product.label, value=float(value),
                units=product.palette.units,
                run_iso=_inspection_run_iso(run), fxx=int(fxx),
                valid_iso=_inspection_valid_iso(run, int(fxx)),
                requested_lat=float(lat), requested_lon=float(lon),
                sampled_lat=float(sampled_lat), sampled_lon=float(sampled_lon),
                distance_km=float(distance), covered=True,
                display_note=str(note or ""), resolution_km=3.0)
        except Exception:  # noqa: BLE001 - sampling never breaks the map
            return imagery_only_sample(lat, lon, rasters_visible=False)

    def _pin_field_snapshot(self, lat: float, lon: float) -> None:
        """Pin a numeric inspection card for ``lat``/``lon`` (T18.2/3/4)."""
        from sharpmod.providers import hrrr_field

        sample = self.sample_field_at(lat, lon)
        snapshot = {
            "kind": "field",
            "product": sample.product_key,
            "label": sample.label,
            "value": sample.value if sample.has_value else None,
            "units": sample.units,
            "run_iso": sample.run_iso,
            "fxx": sample.fxx,
            "valid_label": hrrr_field.field_valid_label(
                _inspection_run_value(sample.run_iso), sample.fxx)
            if sample.run_iso and sample.fxx is not None else "",
            "requested_lat": float(sample.requested_lat),
            "requested_lon": float(sample.requested_lon),
            "sampled_lat": float(sample.sampled_lat),
            "sampled_lon": float(sample.sampled_lon),
            "distance_km": float(sample.distance_km),
            "resolution_km": float(sample.resolution_km),
            "display_note": sample.display_note,
            "reason": ("outside-domain" if not sample.covered
                       else sample.reason),
            "imagery_only": bool(sample.imagery_only),
        }
        self._inspection = snapshot
        self.update()

    def _pin_point_snapshot(self) -> None:
        """Pin a card for the sounding point itself (T18.2)."""
        lon, lat = self._point_lonlat
        self._inspection = {
            "kind": "point",
            "lat": float(lat),
            "lon": float(lon),
            "rasters_visible": bool(self._visible_rasters()),
        }
        self.update()

    def set_saved_points(self, locations) -> None:
        """Show user-named locations as passive map markers."""
        points = []
        for location in locations or ():
            try:
                if isinstance(location, dict):
                    name = location["name"]
                    lat = location["lat"]
                    lon = location["lon"]
                else:
                    name = location.name
                    lat = location.lat
                    lon = location.lon
                lat = float(lat)
                lon = ((float(lon) + 180.0) % 360.0) - 180.0
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if -90.0 <= lat <= 90.0:
                points.append((str(name), lon, lat))
        self._saved_points = tuple(points)
        self.update()

    def set_point(self, lat: float, lon: float, center: bool = False) -> None:
        """Move the sounding point; programmatic moves keep the recent (T18.1)."""
        lon = ((float(lon) + 180.0) % 360.0) - 180.0
        lat = max(-90.0, min(90.0, float(lat)))
        self._point_lonlat = (lon, lat)
        if center:
            # View/projection limits are rendering details, not the requested
            # point reported to source context or entered in the picker.
            center_lat = max(-89.99, min(89.99, lat))
            span_lon = (self._lon1 - self._lon0) / 2.0
            span_lat = (self._lat1 - self._lat0) / 2.0
            self._lon0, self._lon1 = lon - span_lon, lon + span_lon
            self._lat0, self._lat1 = center_lat - span_lat, center_lat + span_lat
            self._invalidate()
        else:
            self.update()

    def context_point(self) -> tuple[float, float]:
        """Use the explicitly selected forecast point for nearby observations."""

        lon, lat = self._point_lonlat
        return float(lat), float(lon)

    def set_domain(self, bounds, label: str = "", outline=None) -> None:
        self._domain_bounds = tuple(bounds) if bounds is not None else None
        self._domain_outline = tuple(outline or ())
        self._domain_label = label or ""
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        self._clear_empty_legend_layout()
        overlay = getattr(self, "_crosshair_overlay", None)
        if overlay is not None:
            preview_timer = getattr(self, "_basemap_refresh_timer", None)
            if preview_timer is not None and preview_timer.isActive():
                # A pan or wheel gesture changes the map itself. Keep its
                # existing transformed-preview path direct; building a full
                # frame cache for every moving view costs more than it saves.
                qp = QPainter(self)
                self._draw_point_map_frame(qp)
                qp.end()
                overlay.refresh()
                return
            self._paint_cached_frame(_event)
            overlay.refresh()
            return

        qp = QPainter(self)
        self._draw_point_map_frame(qp)
        qp.end()

    def _draw_point_map_frame(self, qp) -> None:
        """Draw the stable map layers into a painter for the widget or cache."""
        self._draw_basemap(qp)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        # Imagery first, beneath the domain outline and the picked point. See
        # StationMapWidget.paintEvent -- both orders are hardcoded, so a new
        # layer has to be added to each.
        self._draw_raster_overlays(qp, p)
        self._draw_overlays(qp, p)
        self._draw_domain(qp, p)
        # T16.1: the domain perimeter and box stay above imagery, and geography
        # stays legible above filled weather layers.
        self._draw_boundary_outlines(qp, p)
        self._draw_place_labels(qp, p)
        # Above the domain outline: a box is the active selection, and it has to
        # read clearly against the dashed domain perimeter it usually sits
        # inside. Below the markers, which are small enough not to hide it.
        self._draw_box(qp, p)
        self._draw_saved_points(qp, p)
        self._draw_radar_sites(qp, p)
        self._draw_loaded_profile_markers(qp, p)
        self._draw_point(qp, p)
        self._draw_scale_bar(qp, p)
        self._draw_readout(qp)
        # T20: the legend draws after (and avoids) the readout and inspection
        # card, so the key and the card stop overprinting each other.
        # T21: the time header draws last, matching the station map.
        self._draw_overlay_legend(qp)
        self._draw_time_header(qp)

    def _paint_cached_frame(self, event) -> None:
        """Blit the last unchanged map frame during cursor-only repaints.

        Qt repaints the parent underneath a translucent crosshair child so the
        child's transparent pixels can be composited. The map itself has not
        changed during that cursor move, but without this buffer each of four
        panels repeats its labels, legends, and overlay drawing anyway. Calls to
        ``update`` invalidate the buffer; view, device-scale, and theme inputs
        are also part of its key for changes Qt delivers without that method.
        """
        from sharpmod.ui.features.gui_theme import mono_font, ui_font

        palette = _map()
        key = (
            self.width(),
            self.height(),
            round(float(self.devicePixelRatioF()), 4),
            tuple(self.view_bounds()),
            str(getattr(self, "_projection", "flat")),
            getattr(self, "_conic_freeze", None),
            repr(palette),
            mono_font("body").toString(),
            ui_font("caption").toString(),
            tuple((name, id(layer), bool(self._overlay_visible.get(name, True)))
                  for name, layer in self._rasters.items()),
            tuple((name, id(layer), bool(self._overlay_visible.get(name, True)))
                  for name, layer in self._overlays.items()),
        )
        cached = getattr(self, "_map_frame_cache", None)
        if cached is None or cached[0] != key:
            ratio = float(self.devicePixelRatioF())
            frame = QImage(
                max(1, round(self.width() * ratio)),
                max(1, round(self.height() * ratio)),
                QImage.Format_ARGB32_Premultiplied,
            )
            frame.setDevicePixelRatio(ratio)
            frame.fill(QColor(palette.background))
            painter = QPainter(frame)
            self._draw_point_map_frame(painter)
            painter.end()
            self._map_frame_cache = (key, frame)
        else:
            frame = cached[1]

        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.drawImage(0, 0, frame)
        painter.end()

    def _draw_domain(self, qp, p) -> None:
        if self._domain_bounds is None:
            return
        lon0, lon1, lat0, lat1 = self._domain_bounds
        if lon0 <= -179.0 and lon1 >= 179.0 and lat0 <= -85.0 and lat1 >= 85.0:
            return
        fill = QColor(80, 140, 220, 34)
        edge = QColor(_map().domain_edge)
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1.4, Qt.DashLine))
        if self._domain_outline:
            # A rotated grid can wrap around a pole. Filling its geographic
            # polygon with a planar Qt winding rule shades the complement near
            # the antimeridian, so render the precise perimeter only.
            qp.setBrush(Qt.NoBrush)
            unwrapped = []
            previous_lon = None
            for lon, lat in self._domain_outline:
                lon = float(lon)
                if previous_lon is not None:
                    lon = previous_lon + ((lon - previous_lon + 180.0) % 360.0) - 180.0
                unwrapped.append((lon, float(lat)))
                previous_lon = lon
            # Draw adjacent longitude copies so an antimeridian-crossing
            # rotated grid remains visible in both world and regional views.
            for shift in (-360.0, 0.0, 360.0):
                poly = QPolygonF(
                    [self._to_px(lon + shift, lat, p) for lon, lat in unwrapped]
                )
                qp.drawPolygon(poly)
            return
        spans = ((lon0, lon1),) if lon0 <= lon1 else ((lon0, 180.0), (-180.0, lon1))
        for start, end in spans:
            qp.drawPolygon(self._lonlat_box_polygon(p, start, end, lat0, lat1))

    def _draw_saved_points(self, qp, p) -> None:
        # User-supplied place labels: prose, not figures.
        qp.setFont(ui_font("caption"))
        for name, lon, lat in self._saved_points:
            pt = self._to_px(lon, lat, p)
            if (
                pt.x() < -20
                or pt.y() < -20
                or pt.x() > self.width() + 20
                or pt.y() > self.height() + 20
            ):
                continue
            qp.setBrush(QBrush(QColor(_map().saved)))
            qp.setPen(QPen(QColor(_map().saved_edge), 1.3))
            qp.drawEllipse(pt, 4.0, 4.0)
            qp.setPen(QPen(QColor(_map().readout_text), 1.0))
            qp.drawText(QPointF(pt.x() + 6, pt.y() - 5), name)

    def _draw_point(self, qp, p) -> None:
        lon, lat = self._point_lonlat
        pt = self._to_px(lon, lat, p)
        if (
            pt.x() < -20
            or pt.y() < -20
            or pt.x() > self.width() + 20
            or pt.y() > self.height() + 20
        ):
            return
        if self.is_point_locked():
            # A locked point reads padlocked: the same marker inside a ring,
            # so "locked" never depends on hue alone.
            qp.setBrush(QBrush(QColor(_map().selected)))
            qp.setPen(QPen(QColor(_map().selected_edge), 2.0))
            qp.drawEllipse(pt, 7.0, 7.0)
            qp.setBrush(Qt.NoBrush)
            qp.setPen(QPen(QColor(_map().selected_edge), 1.6))
            qp.drawEllipse(pt, 11.5, 11.5)
        else:
            qp.setBrush(QBrush(QColor(_map().selected)))
            qp.setPen(QPen(QColor(_map().selected_edge), 2.0))
            qp.drawEllipse(pt, 7.0, 7.0)
        # Drawn across the marker itself, so it contrasts with `selected`
        # rather than with the basemap.
        qp.setPen(QPen(QColor(_map().selected_crosshair), 1.4))
        qp.drawLine(QPointF(pt.x() - 10, pt.y()), QPointF(pt.x() + 10, pt.y()))
        qp.drawLine(QPointF(pt.x(), pt.y() - 10), QPointF(pt.x(), pt.y() + 10))
        self._draw_crosshair_guides(qp, p)

    def _draw_crosshair_guides(self, qp, p) -> None:
        """Paint the shared field-panel crosshair guides (T24.1).

        Dashed full-extent horizontal/vertical guides through one geographic
        location, drawn with the same marker/edge pairing as the sounding
        point so the location reads without hue alone. Own hover and sounding
        point are not guides: this mirrors a *sibling's* hover only.
        """
        if (getattr(self, "_crosshair_suppressed", False)
                or getattr(self, "_crosshair_overlay", None) is not None):
            return
        crosshair = self.crosshair()
        if crosshair is None:
            return
        try:
            point = self._to_px(float(crosshair[1]), float(crosshair[0]), p)
        except (TypeError, ValueError, OverflowError):
            return
        pen = QPen(QColor(_map().selected_edge), 1.2, Qt.DashLine)
        qp.setPen(pen)
        qp.drawLine(QPointF(0.0, point.y()),
                    QPointF(float(self.width()), point.y()))
        qp.drawLine(QPointF(point.x(), 0.0),
                    QPointF(point.x(), float(self.height())))

    def _draw_readout(self, qp) -> None:
        lines = []
        overlay = getattr(self, "_crosshair_overlay", None)
        crosshair_line = ""
        if overlay is None and not getattr(self, "_crosshair_suppressed", False):
            try:
                crosshair_line = self.crosshair_text()
            except Exception:  # noqa: BLE001 - crosshair text is advisory
                crosshair_line = ""
        if crosshair_line:
            lines.append(crosshair_line)
        hover_text = getattr(self, "_hover_inspection_text", "")
        if hover_text and getattr(self, "_map_tool", "select") != "inspect":
            # Inspect hover text carries the numeric value; on other tools it
            # would duplicate the cursor line below it.
            hover_text = ""
        if overlay is not None:
            hover_text = ""
        if hover_text:
            lines.append(hover_text)
        elif overlay is None and self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            lines.append(f"Cursor  {lat:.3f}, {lon:.3f}")
        if not getattr(self, "_compact_readout", False):
            lon, lat = self._point_lonlat
            point_line = f"Point   {lat:.3f}, {lon:.3f}"
            if self.is_point_locked():
                point_line += "  (locked)"
            lines.append(point_line)
        # While dragging, the size is the number the user is actually steering
        # by, so it replaces the committed box line rather than joining it.
        if self._box_anchor is not None and self._box_drag is not None:
            anchor_lon, anchor_lat = self._box_anchor
            drag_lon, drag_lat = self._box_drag
            lines.append(
                "Box     "
                + self._box_size_text(
                    (
                        min(anchor_lat, drag_lat),
                        min(anchor_lon, drag_lon),
                        max(anchor_lat, drag_lat),
                        max(anchor_lon, drag_lon),
                    )
                )
            )
        elif self._box_corners is not None:
            lines.append("Box     " + self._box_size_text(self._box_corners))
            if self._box_note:
                lines.append("        " + self._box_note)
        elif getattr(self, "_map_tool", "select") == "box":
            lines.append("Box     drag to select an area")
        if self._domain_label:
            lines.append(self._domain_label)
        if not lines:
            self._readout_rect = None
            self._draw_inspection_card(qp)
            return
        # Coordinate readout: monospace so the digits do not shift as the
        # pointer moves across the map.
        qp.setFont(mono_font("body"))
        metrics = qp.fontMetrics()
        line_height = max(18.0, math.ceil(metrics.height() + 2.0))
        readout_width = min(
            max(0.0, float(self.width() - 16)),
            max(float(metrics.horizontalAdvance(text)) for text in lines) + 3.0,
        )
        self._readout_rect = (
            8.0, 8.0, readout_width, len(lines) * line_height)
        y = 8.0
        for text in lines:
            rect = QRectF(8, y, self.width() - 16, line_height)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += line_height
        self._draw_inspection_card(qp)

    def _select_from_pos(self, pos: QPointF, activate: bool = False) -> None:
        lon, lat = self._to_lonlat(pos.x(), pos.y())
        lon = ((lon + 180.0) % 360.0) - 180.0
        lat = max(-89.99, min(89.99, lat))
        # A locked selection cannot be changed accidentally (T18.1).
        if self.is_point_locked():
            return
        previous = getattr(self, "_point_lonlat", None)
        self.set_point(lat, lon)
        try:
            if previous is not None and tuple(previous) != (float(lon), float(lat)):
                self._recent_point = (float(previous[0]), float(previous[1]))
        except (TypeError, ValueError, OverflowError):
            pass
        self.pointSelected.emit(float(lat), float(lon))
        if activate:
            self.pointActivated.emit(float(lat), float(lon))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._box_corners is not None:
            # A completed object remains editable even after the draw tool
            # is disarmed; middle/right drag still pans through the object.
            if self._begin_box_edit(self._pos(event)):
                self.update()
                return
        if event.button() == Qt.LeftButton and self._box_gesture(event):
            pos = self._pos(event)
            # Deliberately unwrapped: the inverse projection can return values
            # beyond +/-180 when the view is panned across the dateline, and that
            # is what preserves the dragged span.
            self._box_anchor = self._to_lonlat(pos.x(), pos.y())
            self._box_drag = self._box_anchor
            self._box_anchor_px = pos
            self._box_drag_px = pos
            # Suppress the inherited pan for this gesture.
            self._drag_last = None
            self._dragged = False
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._box_edit_kind is not None and (event.buttons() & Qt.LeftButton):
            self._update_box_edit(self._pos(event))
            return
        if self._box_anchor is not None and (event.buttons() & Qt.LeftButton):
            pos = self._pos(event)
            self._box_drag = self._to_lonlat(pos.x(), pos.y())
            self._box_drag_px = pos
            # Keep the cursor readout live without letting the base class pan.
            lon, lat = self._box_drag
            self._hover_lonlat = (((lon + 180.0) % 360.0) - 180.0, lat)
            self.setToolTip(
                self._box_size_text(
                    (
                        min(self._box_anchor[1], lat),
                        min(self._box_anchor[0], lon),
                        max(self._box_anchor[1], lat),
                        max(self._box_anchor[0], lon),
                    )
                )
            )
            self.update()
            return
        panning = (
            getattr(self, "_drag_last", None) is not None
            and bool(event.buttons() & PAN_BUTTONS)
        )
        super().mouseMoveEvent(event)
        if panning:
            # The base map has queued the gesture preview. Its previous hover
            # is stale while the extent moves, and another update here only
            # adds work to every drag event.
            return
        # The base class already set a radar tooltip if one is under the cursor;
        # overwriting it with bare coordinates would hide the only label the
        # antenna markers have at a continental extent. A radar hover keeps
        # its own readout and never publishes a crosshair.
        if self._radar_hover_id is not None:
            if not event.buttons():
                self._update_box_edit_cursor(self._pos(event))
            overlay = getattr(self, "_crosshair_overlay", None)
            if overlay is not None and not event.buttons():
                overlay.refresh()
            else:
                self.update()
            return
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            if getattr(self, "_map_tool", "select") == "inspect":
                self._update_inspect_hover(lat, lon)
            else:
                self._hover_inspection_text = ""
                self.setToolTip(
                    f"{lat:.3f}, {lon:.3f} — click to place the sounding point, "
                    "double-click to fetch; middle/right-drag pans, right-click "
                    "for map navigation")
            # One geographic cursor across panels (T24.1): sibling panels
            # mirror this hover without moving their own sounding point,
            # selection, or pinned card. Panning/drawing gestures keep their
            # own readout and never publish a crosshair.
            if not event.buttons():
                try:
                    self._crosshair_suppressed = True
                    try:
                        self.crosshairHover.emit(float(lat), float(lon))
                    finally:
                        self._crosshair_suppressed = False
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    self._crosshair_suppressed = False
        if not event.buttons():
            self._update_box_edit_cursor(self._pos(event))
        overlay = getattr(self, "_crosshair_overlay", None)
        if overlay is not None and not event.buttons():
            overlay.refresh()
        else:
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Clear own hover and drop the mirrored crosshair (T24.1)."""
        self._hover_lonlat = None
        self._hover_inspection_text = ""
        self._crosshair_lonlat = None
        self._crosshair_suppressed = False
        try:
            super().leaveEvent(event)
        except Exception:  # noqa: BLE001 - leave must not raise
            pass
        overlay = getattr(self, "_crosshair_overlay", None)
        if overlay is not None:
            overlay.refresh()
        self.update()

    def _update_inspect_hover(self, lat: float, lon: float) -> None:
        """Refresh the numeric hover readout without moving anything (T18.2)."""
        from sharpmod.maps.map_field_inspection import hover_text

        try:
            sample = self.sample_field_at(lat, lon)
            text = hover_text(sample)
        except Exception:  # noqa: BLE001 - hover text is advisory
            text = ""
        if not text:
            try:
                text = f"{float(lat):.3f}, {float(lon):.3f}"
            except (TypeError, ValueError, OverflowError):
                text = ""
        self._hover_inspection_text = text
        self.setToolTip(text + " — click to pin; inspecting never moves "
                               "the sounding point")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() & SECONDARY_PAN_BUTTONS:
            # Let the base class end its pan; a secondary button never draws a
            # box or picks a point.
            super().mouseReleaseEvent(event)
            return
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        if self._box_edit_kind is not None:
            # Apply the release position even when Qt coalesced the final move,
            # then publish exactly one changed rectangle.  No data work is
            # started from this map signal; the picker only refreshes preview.
            kind = self._box_edit_kind
            self._update_box_edit(pos)
            moved = self._box_edit_moved
            self._finish_box_edit()
            if not moved and kind == "move":
                # Clicking inside the area is still an ordinary point/inspect
                # click. Only a real drag claims it for moving the box.
                if self._pick_radar_site(pos) or self._describe_marker_overlay_at(
                        pos, event):
                    return
                if getattr(self, "_map_tool", "select") == "inspect":
                    lon, lat = self._to_lonlat(pos.x(), pos.y())
                    self._pin_field_snapshot(float(lat), float(lon))
                else:
                    self._select_from_pos(pos)
            return
        if self._box_anchor is not None:
            anchor = self._box_anchor
            anchor_px = self._box_anchor_px or pos
            drag = self._box_drag or anchor
            self._cancel_box_drag()
            moved_x = abs(pos.x() - anchor_px.x())
            moved_y = abs(pos.y() - anchor_px.y())
            if moved_x < MIN_BOX_DRAG_PX or moved_y < MIN_BOX_DRAG_PX:
                # Too small to be a rectangle. Treat it as an ordinary pick so a
                # stray Shift-click still does something useful instead of
                # silently doing nothing. Inspect pins instead of placing.
                self.update()
                if self._pick_radar_site(pos):
                    return
                if self._describe_marker_overlay_at(pos, event):
                    return
                if getattr(self, "_map_tool", "select") == "inspect":
                    lon, lat = self._to_lonlat(pos.x(), pos.y())
                    self._pin_field_snapshot(float(lat), float(lon))
                    return
                self._select_from_pos(pos)
                return
            corners = (
                min(anchor[1], drag[1]),
                min(anchor[0], drag[0]),
                max(anchor[1], drag[1]),
                max(anchor[0], drag[0]),
            )
            self._box_corners = corners
            self._box_nodes = ()
            self._box_node_coverage = ()
            self._box_note = ""
            self.update()
            self.boxSelected.emit(*(float(value) for value in corners))
            return
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        self._pan_push_armed = False
        self._press_pos = None
        if was_drag:
            # A left-drag pan ends here and never picks a point: releasing over
            # the sounding marker after looking around must not move it.
            try:
                self._clamp_view()
            except Exception:  # noqa: BLE001 - clamp never breaks release
                pass
            self._refresh_map_accessible_description()
            return
        # An antenna under the cursor claims the click. Falling through would
        # move the sounding point as well, so one click would both retarget the
        # radar and move the profile location -- two answers to one gesture.
        if self._pick_radar_site(pos):
            return
        if self._describe_marker_overlay_at(pos, event):
            return
        # Inspect pins an inspection card without moving anything (T18.1/2).
        if getattr(self, "_map_tool", "select") == "inspect":
            lon, lat = self._to_lonlat(pos.x(), pos.y())
            self._pin_field_snapshot(float(lat), float(lon))
            return
        self._select_from_pos(pos)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # A double click while drawing a box would otherwise leave a stale
        # anchor behind and pick a point at the same time.
        if self._box_anchor is not None or self._box_mode:
            return
        # Inspect never fetches: pinning is the whole gesture (T18.1).
        if getattr(self, "_map_tool", "select") == "inspect":
            pos = self._pos(event)
            lon, lat = self._to_lonlat(pos.x(), pos.y())
            self._pin_field_snapshot(float(lat), float(lon))
            return
        pos = self._pos(event)
        if self._pick_radar_site(pos):
            return
        if self._describe_marker_overlay_at(pos, event):
            return
        self._select_from_pos(pos, activate=True)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """Offer point actions where right-clicked without moving it (T17.4).

        The sounding selection stays exactly where it is: this menu reports
        the clicked location and offers centre-here plus the shared
        navigation actions, but never re-points the sounding. A drag that
        panned suppresses the menu via the inherited consumed-press flag.
        T18.2 adds pin/copy actions for the clicked location; those pin an
        inspection card and copy coordinates, still without moving anything.
        """
        if getattr(self, "_pan_consumed_press", False):
            self._pan_consumed_press = False
            return
        menu = QMenu(self)
        try:
            lon, lat = self._to_lonlat(event.x(), event.y())
        except Exception:  # noqa: BLE001 - menu still offers navigation
            lon, lat = None, None
        if lon is not None and math.isfinite(lon) and math.isfinite(lat):
            here = menu.addAction(f"Centre here ({lat:.2f}, {lon:.2f})")
            here.setToolTip("Centre the view on the clicked location")
            here.setStatusTip("Centre the view on the clicked location")
            here.triggered.connect(
                lambda _checked=False, _lon=lon, _lat=lat:
                self.center_on_lonlat(_lon, _lat))
            pin = menu.addAction(f"Pin inspection here ({lat:.3f}, {lon:.3f})")
            pin.setToolTip("Pin a numeric inspection card here without "
                           "moving the sounding point")
            pin.setStatusTip("Pin a numeric inspection card here without "
                             "moving the sounding point")
            pin.triggered.connect(
                lambda _checked=False, _lon=lon, _lat=lat:
                self._pin_field_snapshot(float(_lat), float(_lon)))
            copy = menu.addAction("Copy coordinates here")
            copy.setToolTip("Copy this location at picker precision")
            copy.setStatusTip("Copy this location at picker precision")
            copy.triggered.connect(
                lambda _checked=False, _lon=lon, _lat=lat:
                self._copy_text(f"{float(_lat):.4f}, {float(_lon):.4f}"))
            if self.inspection_snapshot() is not None:
                copy_value = menu.addAction("Copy pinned value")
                copy_value.setToolTip("Copy the pinned card's value")
                copy_value.setStatusTip("Copy the pinned card's value")
                copy_value.triggered.connect(
                    lambda _checked=False: self._copy_text(
                        self.copy_inspection_value()))
                unpin = menu.addAction("Unpin inspection")
                unpin.setToolTip("Clear the pinned inspection card")
                unpin.setStatusTip("Clear the pinned inspection card")
                unpin.triggered.connect(lambda _checked=False: self.clear_inspection())
            menu.addSeparator()
        for entry in self.navigation_actions():
            action = menu.addAction(f"{entry['label']}\t{entry['shortcut']}")
            action.setToolTip(entry["tooltip"])
            action.setStatusTip(entry["tooltip"])
            action.triggered.connect(entry["trigger"])
        try:
            where = event.globalPos()
        except Exception:  # noqa: BLE001 - headless/fake events have no screen
            try:
                where = event.globalPosition().toPoint()
            except Exception:  # noqa: BLE001 - fall back to the widget origin
                where = self.mapToGlobal(QPointF(0, 0).toPoint())
        menu.exec_(where)

    @staticmethod
    def _copy_text(text: str) -> None:
        """Copy ``text`` to the clipboard, silently when headless (T18.3)."""
        try:
            from qtpy.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(str(text))
        except Exception:  # noqa: BLE001 - clipboard is never critical
            pass

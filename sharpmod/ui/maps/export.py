"""Frozen, no-network map figures shared by picker, panels, box and locator.

Each snapshot keeps a display-list fallback plus the semantic painter state
needed to lay the same map out intentionally at another output size. The state
retains the chosen payload, time, extent, z-order, opacity, legend and selection
even if a controller later receives a new frame; detached output has no
controller and cannot ask a provider for replacement imagery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy

from qtpy.QtCore import QPoint, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QImage, QPainter, QPicture, QPixmap
from qtpy.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QMessageBox,
    QProgressDialog, QVBoxLayout,
)

from sharpmod.ui.features.export_presentation import (
    ExportIdentity, ExportPresentation, available_export_path, export_filename,
    load_presentation, save_presentation,
)
from sharpmod.ui.features.gui_export import (
    ExportCompletionPanel, ExportImageDialog, resolved_export_theme,
)
from sharpmod.rendering.cli import save_pixmap_png_atomic


@dataclass(frozen=True)
class MapLayerSnapshot:
    key: str
    title: str
    visible: bool
    opacity: float
    actual_time: datetime | None
    source: str
    attribution: str
    time_match: str


_MAP_STATE_ATTRIBUTES = (
    # Geography/presentation and exact requested viewport.
    "_stations", "_area_name", "_projection", "_lon0", "_lon1", "_lat0",
    "_lat1", "_label_density", "_scale_units", "_pinned_place",
    # Selected, inspected and loaded-profile context.
    "_selected_id", "_hover_id", "_hover_lonlat", "_point_locked",
    "_inspection", "_hover_inspection_text", "_profile_markers",
    "_radar_sites", "_radar_site_id", "_radar_hover_id",
    # Exact attached payload objects plus the captured visibility/paint order.
    "_overlays", "_rasters", "_overlay_visible", "_valid_time",
    "_forecast_run", "_forecast_fxx", "_map_mode", "_held_frames",
    # User legend choices, including fixed product scales.
    "_legend_corner", "_legend_collapsed", "_legend_locks",
    # Point/box map presentation.
    "_point_lonlat", "_saved_points", "_domain_bounds", "_domain_outline",
    "_domain_label", "_crosshair_lonlat", "_crosshair_suppressed",
    "_box_mode", "_box_corners", "_box_nodes", "_box_node_coverage",
    "_box_note", "_map_tool", "_inspection_source",
    # Box-analysis field, auto scale, hatch and selected cell.
    "_analysis", "_field_key", "_field_scale", "_show_values",
    "_selected_cell", "_screen", "_screen_mask", "_screen_coverage",
)


def _frozen_value(name: str, value):
    # Analyses can own large NumPy arrays. They are replaced, never mutated, by
    # the workspace, so retaining this exact object freezes the chosen hour
    # without duplicating tens of megabytes. Everything user-editable is copied.
    if name == "_analysis":
        return value
    try:
        return deepcopy(value)
    except Exception:  # noqa: BLE001 - immutable Qt-adjacent values may not copy
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return tuple(value)
        return value


@dataclass(frozen=True)
class _FrozenMapState:
    widget_type: type
    attributes: dict
    reference_time: datetime

    def render(self, width: int, height: int) -> QPixmap:
        """Repaint the frozen model at export resolution, with no controller."""

        from sharpmod.ui.features.gui_theme import font_text_scale
        from sharpmod.ui.features.gui_maps import StationMapWidget

        width, height = max(1, int(width)), max(1, int(height))
        # Use a logical map canvas suited to label/legend layout, then let Qt's
        # device ratio rasterize that composition at the requested output
        # resolution.  This keeps type, strokes and markers sharp without
        # shrinking 8.5-point labels into print-sized figures.  The scoped
        # 100% ramp makes the export independent of the live UI's 100-200%
        # accessibility setting; the device ratio is the explicit export style.
        density = min(2.0, max(1.25, min(width / 700.0, height / 300.0)))
        if "BoxField" in self.widget_type.__name__:
            density = min(density, 1.5)
        logical_width = max(1, round(width / density))
        logical_height = max(1, round(height / density))
        if self.widget_type is StationMapWidget:
            widget = self.widget_type(self.attributes.get("_stations", ()))
        else:
            widget = self.widget_type()
        try:
            widget.setMinimumSize(1, 1)
            widget.resize(logical_width, logical_height)
            for name, value in self.attributes.items():
                setattr(widget, name, _frozen_value(name, value))
            # Relative-age/staleness prose is part of the captured legend. It
            # must describe the instant the analyst exported, not tick forward
            # whenever preview or save happens to repaint this detached map.
            widget._export_reference_time = self.reference_time
            widget._export_suppress_scale_bar = True
            if getattr(widget, "_label_density", "standard") != "off":
                widget._label_density = "sparse"
            # Do not carry screen-size caches into another geometry. This only
            # rebuilds bundled geography/decoded captured payloads; no controller
            # or provider is attached to the detached widget.
            widget._raster_pixmaps = {}
            widget._proj_cache = None
            widget._basemap_cache = None
            widget._cache_key = None
            widget._cache_proj = None
            widget._warp_cache = None
            widget._warp_proj = None
            widget._conic_freeze = None
            widget._label_layout_cache = None
            widget._station_label_layout_cache = None
            widget._outline_cache = None
            widget._outline_raster_cache = None
            widget.ensurePolished()
            pixmap = QPixmap(width, height)
            pixmap.setDevicePixelRatio(density)
            pixmap.fill(Qt.transparent)
            with font_text_scale(100):
                widget.render(pixmap)
            # The image stores the physical pixels; reset its ratio so the
            # figure compositor treats them as exactly ``width × height``.
            image = pixmap.toImage()
            image.setDevicePixelRatio(1.0)
            return QPixmap.fromImage(image)
        finally:
            widget.close()
            widget.deleteLater()


@dataclass(frozen=True)
class MapSnapshot:
    """The displayed map and the facts used to caption it, frozen together."""

    title: str
    picture: QPicture
    source_size: tuple[int, int]
    extent: tuple[float, float, float, float] | None
    projection: str
    mode: str
    requested_time: datetime | None
    run: datetime | None
    forecast_hour: int | None
    time_header: str
    selection: str
    layers: tuple[MapLayerSnapshot, ...]
    legend: str
    scale: str
    attribution: str
    source_label: str = ""
    locator_source: object | None = None
    map_state: _FrozenMapState | None = None
    map_scale: str = ""
    captured_at: datetime | None = None

    @property
    def source(self) -> str:
        return ", ".join(dict.fromkeys(
            item for item in [self.source_label] +
            [layer.source or layer.title for layer in self.layers if layer.visible]
            if item
        ))


def _capture_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("map capture time must be timezone-aware")
    return value


def capture_map(
    widget, *, title: str, captured_at: datetime | None = None,
) -> MapSnapshot:
    """Record precisely what the live map paints; never reconstruct its data."""

    reference_time = _capture_time(captured_at)
    had_reference = hasattr(widget, "_export_reference_time")
    old_reference = getattr(widget, "_export_reference_time", None)
    widget._export_reference_time = reference_time
    try:
        return _capture_map_at_reference(
            widget, title=title, captured_at=reference_time)
    finally:
        if had_reference:
            widget._export_reference_time = old_reference
        else:
            try:
                delattr(widget, "_export_reference_time")
            except AttributeError:
                pass


def _capture_map_at_reference(
    widget, *, title: str, captured_at: datetime,
) -> MapSnapshot:
    """Capture while the live painter temporarily uses ``captured_at``."""

    size = (int(widget.width()), int(widget.height()))
    if min(size) <= 0:
        raise ValueError("The map has no visible area to capture")
    picture = QPicture()
    painter = QPainter(picture)
    try:
        widget.render(painter, QPoint())
    finally:
        painter.end()

    order = widget.paint_order_keys()
    matches = widget.time_match_states()
    layers = []
    for key in order:
        layer = widget.overlay(key)
        if layer is None:
            continue
        actual = getattr(layer, "valid_time", None)
        if actual is None:
            actual = getattr(layer, "valid_from", None)
        layers.append(MapLayerSnapshot(
            key=key, title=str(getattr(layer, "title", key)),
            visible=widget.is_overlay_visible(key),
            opacity=float(getattr(layer, "opacity", 1.0)),
            actual_time=actual,
            source=str(getattr(layer, "short_name", "") or getattr(layer, "title", key)),
            attribution=str(getattr(layer, "attribution", "") or ""),
            time_match=str(matches.get(key, ("", ""))[1]),
        ))
    selection = ""
    point = getattr(widget, "_point_lonlat", None)
    if point is not None:
        selection = f"Point {point[1]:.3f}°, {point[0]:.3f}°"
    elif getattr(widget, "_selected_id", None):
        selection = f"Station {widget._selected_id}"
    box = widget.box() if hasattr(widget, "box") else None
    if box is not None:
        selection += (" · " if selection else "") + "Box " + ", ".join(
            f"{coordinate:.2f}°" for coordinate in box)
    map_scale = widget.scale_bar_info().get("label", "")
    scale = map_scale
    box_scale = widget.scale() if hasattr(widget, "scale") else None
    if box_scale is not None:
        scale += f" · Field auto scale {box_scale.minimum:g}–{box_scale.maximum:g}"
    legend = widget.legend_accessible_text()
    if box_scale is not None:
        legend = f"Field {widget.field()} · {scale}"
    frozen_state = _FrozenMapState(
        type(widget),
        {name: _frozen_value(name, getattr(widget, name))
         for name in _MAP_STATE_ATTRIBUTES if hasattr(widget, name)},
        captured_at,
    )
    return MapSnapshot(
        title=title, picture=picture, source_size=size,
        extent=tuple(widget.view_bounds()),
        projection=widget.effective_projection(), mode=widget.map_mode(),
        requested_time=widget.valid_time(),
        run=getattr(widget, "_forecast_run", None),
        forecast_hour=getattr(widget, "_forecast_fxx", None),
        time_header=widget.time_header_text(), selection=selection,
        layers=tuple(layers), legend=legend, scale=scale,
        attribution="; ".join(dict.fromkeys(
            ["Census/Natural Earth geography"] +
            [layer.attribution for layer in layers if layer.visible and layer.attribution]
        )),
        source_label=str(getattr(widget, "_domain_label", "") or ""),
        map_state=frozen_state, map_scale=map_scale, captured_at=captured_at,
    )


def capture_locator(source, *, captured_at: datetime | None = None) -> MapSnapshot:
    """Freeze the expanded locator using its established presentation painter."""

    reference_time = _capture_time(captured_at)

    from sharpmod.maps import locator_presentation
    from sharpmod.maps.map_overlays import LOCATOR_OVERLAY_META_KEY
    from sharpmod.viz import hodo_locator

    collection = source.prof_collections[int(source.pc_idx)]
    point = hodo_locator.point_from_widget(source)
    keys = (
        "lat", "lon", "requested_lat", "requested_lon", "box_mean_bounds",
        "town", "place_name", "location_name", "location", "station_name",
        "stn_id", "loc", "model", "valid", "date",
        "sharpmod_locator_overlay_descriptors", LOCATOR_OVERLAY_META_KEY,
        locator_presentation.PRESENTATION_META_KEY,
        locator_presentation.OVERLAY_STATUS_META_KEY,
    )
    metadata = {}
    for key in keys:
        try:
            value = collection.getMeta(key)
        except (AttributeError, KeyError, TypeError):
            value = getattr(collection, "_meta", {}).get(key)
        if value is not None:
            metadata[key] = deepcopy(value)
    if point is not None:
        metadata["lat"], metadata["lon"] = point
    valid = hodo_locator.valid_time_from_widget(source)

    class FrozenCollection:
        def getMeta(self, key):  # noqa: N802 - profile collection interface
            return metadata.get(key)

        def getCurrentDate(self):  # noqa: N802 - profile collection interface
            return valid

    frozen = SimpleNamespace(
        prof_collections=(FrozenCollection(),), pc_idx=0, prof=None,
        bg_color=QColor(getattr(source, "bg_color", "#05090b")),
        fg_color=QColor(getattr(source, "fg_color", "#ffffff")),
    )
    # Record the established locator paint path once for the shared model;
    # output re-renders this *frozen proxy* at the chosen size so its geographic
    # strokes, labels and provenance footer remain readable in a large figure.
    width, height = 1200, 760
    pixmap = hodo_locator.render_locator_pixmap(
        frozen, width=width, height=height, expanded=True)
    picture = QPicture()
    painter = QPainter(picture)
    try:
        painter.drawPixmap(0, 0, pixmap)
    finally:
        painter.end()
    state = locator_presentation.collection_presentation(frozen.prof_collections[0])
    requested = hodo_locator.requested_point_from_widget(frozen)
    bounds = hodo_locator.bounds_from_widget(frozen)
    location = hodo_locator.location_name_from_widget(frozen)
    time_text = f"Valid {valid:%d %b %Y %H:%M}Z" if isinstance(valid, datetime) else ""
    contexts = hodo_locator.overlay_context_for_widget(frozen)
    layers = tuple(MapLayerSnapshot(
        key=str(item.get("key") or ""),
        title=str(item.get("title") or item.get("key") or "Layer"),
        visible=item.get("status") != "not-selected", opacity=1.0,
        actual_time=item.get("actual_time"),
        source=str(item.get("source") or ""),
        attribution=str(item.get("source") or ""),
        time_match=str(item.get("status") or item.get("time_basis") or ""),
    ) for item in contexts)
    points = []
    if requested is not None:
        points.append(f"Requested {requested[0]:.3f}°, {requested[1]:.3f}°")
    if point is not None:
        points.append(f"Sampled {point[0]:.3f}°, {point[1]:.3f}°")
    return MapSnapshot(
        title=f"Locator · {location}", picture=picture,
        source_size=(width, height),
        extent=(bounds[0], bounds[2], bounds[1], bounds[3]) if bounds else None,
        projection="locator", mode=state.link_mode,
        requested_time=valid if isinstance(valid, datetime) else None,
        run=None, forecast_hour=None, time_header=time_text,
        selection=" · ".join(points), layers=layers,
        legend=state.summary(), scale=state.scale_units,
        attribution="; ".join(dict.fromkeys(
            ["U.S. Census Bureau boundaries"] +
            [layer.attribution for layer in layers if layer.attribution])),
        locator_source=frozen, captured_at=reference_time,
    )


def _context_lines(frame: MapSnapshot) -> tuple[str, ...]:
    lines = [frame.time_header or "No requested/actual time recorded"]
    if frame.source:
        lines.append(f"Source: {frame.source}")
    if frame.extent is not None:
        west, east, south, north = frame.extent
        lines.append(
            f"{frame.mode.title()} · {frame.projection} · "
            f"extent {west:.2f}–{east:.2f}°E, {south:.2f}–{north:.2f}°N"
            + (f" · {frame.selection}" if frame.selection else "")
        )
    elif frame.selection:
        lines.append(frame.selection)
    if frame.layers:
        lines.append("Layers bottom → top: " + "; ".join(
            f"{layer.title} {round(layer.opacity * 100)}%"
            + (" (hidden)" if not layer.visible else "")
            + (f" actual {layer.actual_time:%d %b %Y %H:%M}Z"
               if layer.visible and isinstance(layer.actual_time, datetime) else "")
            + (f" [{layer.time_match}]" if layer.visible and layer.time_match not in ("", "exact") else "")
            for layer in frame.layers
        ))
    if frame.map_scale:
        orientation = "North varies" if frame.projection == "curved" else "North is up"
        projection = (
            "Lambert conformal conic"
            if frame.projection == "curved" else "Equirectangular"
        )
        lines.append(
            f"Map scale/orientation: {frame.map_scale} · {orientation} · {projection}"
        )
    elif frame.projection == "locator":
        units = "miles" if frame.scale == "imperial" else "kilometers"
        lines.append(
            f"Map scale/orientation: scale bar in {units} · North is up · "
            "Equirectangular"
        )
    if frame.legend:
        lines.append("Legend: " + frame.legend)
    elif frame.scale:
        lines.append(f"Scale {frame.scale}")
    lines.append(frame.attribution)
    return tuple(lines)


def _wrap(text: str, metrics, width: int) -> tuple[str, ...]:
    """Wrap even long non-space tokens; no ellipses in scientific context."""

    words = str(text).split()
    if not words:
        return ()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if metrics.horizontalAdvance(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while word and metrics.horizontalAdvance(word) > width:
            left, right = 1, len(word)
            while left < right:
                midpoint = (left + right + 1) // 2
                if metrics.horizontalAdvance(word[:midpoint]) <= width:
                    left = midpoint
                else:
                    right = midpoint - 1
            lines.append(word[:left])
            word = word[left:]
        current = word
    if current:
        lines.append(current)
    return tuple(lines)


def render_map_figure(
    frames: tuple[MapSnapshot, ...], choices: ExportPresentation, *, title: str,
) -> QPixmap:
    """Compose exact-sized output entirely from captured display lists/state."""

    if len(frames) not in (1, 2, 4):
        raise ValueError("A map figure needs one, two, or four captured maps")
    width, height = choices.size
    columns = 1 if len(frames) == 1 or (len(frames) == 2 and height > width) else 2
    rows = (len(frames) + columns - 1) // columns
    margin = max(10, round(min(width, height) * 0.014))
    gap = margin
    tile_candidate = width / columns
    row_candidate = height / rows
    font_size = max(
        12,
        min(30, round(min(tile_candidate / 45.0, row_candidate / 32.0))),
    )
    if len(frames) == 4:
        font_size = min(font_size, 20)
    elif len(frames) == 2:
        font_size = min(font_size, 24)
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    dark = resolved_export_theme(choices.theme) == "dark"
    background = QColor("#141b27" if dark else "#f6f8fb")
    card = QColor("#222c3b" if dark else "#ffffff")
    ink = QColor("#f3f6fc" if dark else "#182638")
    subdued = QColor("#c3d0e0" if dark else "#3e5266")
    image.fill(background)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont()
        font.setPixelSize(font_size)
        painter.setFont(font)
        line_h = painter.fontMetrics().height() + 3
        heading_lines = ()
        heading_height = 0
        tile_w = (width - 2 * margin - (columns - 1) * gap) // columns
        map_w = tile_w - 2 * margin
        if title.strip():
            heading = QFont(font)
            heading.setBold(True)
            heading.setPixelSize(font_size + 6)
            painter.setFont(heading)
            heading_lines = _wrap(title.strip(), painter.fontMetrics(),
                                  width - 2 * margin)
            heading_line_h = painter.fontMetrics().height() + 3
            heading_height = len(heading_lines) * heading_line_h + margin
            painter.setFont(font)
        tile_h = (height - 2 * margin - heading_height - (rows - 1) * gap) // rows
        if map_w < 210 or tile_h <= 0:
            raise ValueError("Too little space for each map. Choose larger dimensions.")
        painter.setPen(ink)
        for number, line in enumerate(heading_lines):
            painter.drawText(QRectF(margin, margin + number * heading_line_h,
                                    width - 2 * margin, heading_line_h),
                             Qt.AlignLeft | Qt.AlignVCenter, line)
        for index, frame in enumerate(frames):
            x = margin + (index % columns) * (tile_w + gap)
            y = margin + heading_height + (index // columns) * (tile_h + gap)
            painter.fillRect(QRectF(x, y, tile_w, tile_h), card)
            painter.setPen(ink)
            label_lines = _wrap(frame.title, painter.fontMetrics(), map_w)
            caption_lines = tuple(
                line for paragraph in _context_lines(frame)
                for line in _wrap(paragraph, painter.fontMetrics(), map_w)
            ) if choices.include_caption else ()
            label_h = len(label_lines) * line_h + 6
            caption_h = len(caption_lines) * line_h + (margin if caption_lines else 0)
            map_h = tile_h - 2 * margin - label_h - caption_h
            sx, sy = frame.source_size
            scale = min(map_w / sx, map_h / sy)
            if (map_w < 210 or map_h < 155 or
                    (frame.map_state is None and frame.locator_source is None
                     and scale < 0.75)):
                raise ValueError(
                    "This layout leaves too little space for readable map labels "
                    "and complete context. Choose larger dimensions or fewer panels."
                )
            for number, line in enumerate(label_lines):
                painter.drawText(QRectF(x + margin, y + margin + number * line_h,
                                        map_w, line_h),
                                 Qt.AlignLeft | Qt.AlignVCenter, line)
            top = y + margin + label_h
            factor = min(map_w / sx, map_h / sy)
            drawn_w, drawn_h = (
                (map_w, map_h)
                if frame.map_state is not None or frame.locator_source is not None
                else (sx * factor, sy * factor)
            )
            left = x + margin + (map_w - drawn_w) / 2
            top += (map_h - drawn_h) / 2
            if frame.locator_source is not None:
                from sharpmod.viz import hodo_locator

                locator = hodo_locator.render_locator_pixmap(
                    frame.locator_source,
                    width=round(drawn_w), height=round(drawn_h), expanded=True,
                )
                painter.drawPixmap(QRectF(left, top, drawn_w, drawn_h),
                                   locator, QRectF(locator.rect()))
            elif frame.map_state is not None:
                map_pixmap = frame.map_state.render(round(drawn_w), round(drawn_h))
                painter.drawPixmap(QRectF(left, top, drawn_w, drawn_h),
                                   map_pixmap, QRectF(map_pixmap.rect()))
            else:
                painter.save()
                painter.setClipRect(QRectF(left, top, drawn_w, drawn_h))
                painter.translate(left, top)
                painter.scale(factor, factor)
                painter.drawPicture(0, 0, frame.picture)
                painter.restore()
            if choices.include_caption:
                painter.setPen(subdued)
                bottom = y + tile_h - margin - len(caption_lines) * line_h
                for number, line in enumerate(caption_lines):
                    painter.drawText(QRectF(x + margin, bottom + number * line_h,
                                            map_w, line_h),
                                     Qt.AlignLeft | Qt.AlignVCenter, line)
    finally:
        painter.end()
    return QPixmap.fromImage(image)


def export_map_figure(
    owner, frames: tuple[MapSnapshot, ...], *, settings=None,
    namespace: str, identity: ExportIdentity, kind: str, title: str,
    status=None,
) -> Path | None:
    """Use T27 size/preview/path/history/completion for every map entry point."""

    if not frames:
        raise ValueError("No map was captured")
    default = (ExportPresentation(2400, 1600, "print")
               if len(frames) == 4 else ExportPresentation())
    dialog = ExportImageDialog(
        lambda choices, figure_title: render_map_figure(
            frames, choices, title=figure_title),
        content=" · ".join(frame.title for frame in frames),
        presentation=load_presentation(settings, namespace, default=default),
        title=title, parent=owner,
    )
    try:
        accepted = dialog.exec() == QDialog.Accepted
        choices = dialog.presentation if accepted else None
        pixmap = dialog.output_pixmap if accepted else None
        figure_title = dialog.figure_title
    finally:
        dialog.release()
    if not accepted or choices is None or pixmap is None:
        return None
    try:
        initial = available_export_path(
            export_filename(identity, kind=kind, extension="png"),
            settings=settings,
        )
        name, _ = QFileDialog.getSaveFileName(
            owner, "Export Map Figure", str(initial), "PNG image (*.png)")
        if not name:
            return None
        target = Path(name)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        progress = QProgressDialog("Publishing captured map figure…", "", 0, 2, owner)
        progress.setCancelButton(None)
        progress.setWindowTitle("Export map")
        progress.setMinimumDuration(0)
        progress.setValue(1)
        QApplication.processEvents()
        try:
            if not save_pixmap_png_atomic(pixmap, str(target)):
                raise OSError("PNG encoding failed; previous destination preserved")
            progress.setValue(2)
        finally:
            progress.close()
            progress.deleteLater()
    except Exception as exc:  # noqa: BLE001 - failure must retain old file
        QMessageBox.warning(
            owner, "Could not export map",
            f"Could not complete map export to:\n{locals().get('target', 'destination')}\n"
            f"{exc}\nCheck the destination and retry. No partial output was published.",
        )
        return None
    # From this point the complete file exists. A settings, status or history
    # failure must never report it as an unpublished/partial export.
    save_presentation(settings, namespace, choices)
    if status is not None:
        try:
            status(f"Saved the map figure to {target}")
        except (AttributeError, RuntimeError):
            pass
    try:
        completion = QDialog(owner)
        completion.setAttribute(Qt.WA_DeleteOnClose)
        completion.setWindowTitle("Map export complete")
        layout = QVBoxLayout(completion)
        panel = ExportCompletionPanel(lambda: settings, completion)
        layout.addWidget(panel)
        panel.completed(target, kind="map PNG", summary=figure_title,
                        dimensions=choices.size)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, completion)
        buttons.rejected.connect(completion.close)
        layout.addWidget(buttons)
        completion.show()
    except Exception as exc:  # noqa: BLE001 - published bytes remain usable
        QMessageBox.warning(
            owner, "Map saved; completion actions unavailable",
            f"The PNG was saved to:\n{target}\n"
            f"Completion actions could not open: {exc}",
        )
    return target


__all__ = ["MapLayerSnapshot", "MapSnapshot", "capture_map", "capture_locator",
           "export_map_figure", "render_map_figure"]

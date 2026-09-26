"""Cached basemap geometry and graticule drawing."""

from __future__ import annotations

from collections import OrderedDict

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.maps.map_geography import boundary_draw_order, boundary_width
from sharpmod.ui.maps.presentation import _map


_SHARED_FRAME_LIMIT = 2
_SHARED_BASEMAP_FRAMES: OrderedDict[tuple, QPixmap] = OrderedDict()
_SHARED_OUTLINE_FRAMES: OrderedDict[tuple, QPixmap] = OrderedDict()


def _remember_frame(cache: OrderedDict, key: tuple, pixmap: QPixmap) -> None:
    cache[key] = pixmap
    cache.move_to_end(key)
    while len(cache) > _SHARED_FRAME_LIMIT:
        cache.popitem(last=False)


class MapBasemapMixin:
    """Prepare and draw map boundaries and grid lines."""

    def _device_pixmap(self):
        """Return an offscreen pixmap matching the widget's *physical* pixels.

        ``QPixmap(self.size())`` allocates a **logical**-size buffer. On a 1.5x
        display the widget's backing store is 1.5x larger, so blitting such a
        buffer 1:1 makes Qt bilinearly upscale it, and every raster routed
        through an offscreen cache -- the vector basemap and the warped imagery
        composite -- arrived on screen softened. Nothing downstream needs to
        change: a painter opened on a pixmap that carries a device pixel ratio
        applies that ratio to its transform, so the drawing code keeps working in
        the same logical coordinates and simply resolves finer.

        The ratio is deliberately not rounded. The picker asks for
        ``PassThrough`` scale-factor rounding, so 1.25 and 1.75 are ordinary.
        """
        ratio = self.devicePixelRatioF()
        pm = QPixmap(
            max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio))
        )
        pm.setDevicePixelRatio(ratio)
        return pm

    @staticmethod
    def _independent_size(pixmap) -> tuple[float, float]:
        """Return ``pixmap``'s size in the logical units a painter addresses.

        Spelled out rather than using ``deviceIndependentSize`` so the call still
        works under the Qt5 bindings qtpy may supply.
        """
        ratio = pixmap.devicePixelRatio() or 1.0
        return pixmap.width() / ratio, pixmap.height() / ratio

    @staticmethod
    def _device_source(pixmap, source):
        """Convert a logical source rectangle into the device pixels Qt expects.

        ``drawPixmap``'s destination is in logical coordinates but its *source*
        rectangle is measured in the pixmap's own device pixels, so a
        high-resolution cache needs the two expressed in different units.
        """
        ratio = pixmap.devicePixelRatio() or 1.0
        if ratio == 1.0:
            return source
        return QRectF(
            source.x() * ratio,
            source.y() * ratio,
            source.width() * ratio,
            source.height() * ratio,
        )

    def _basemap_key(self) -> tuple:
        """Everything the baked basemap raster depends on.

        The palette is part of it because the pixmap bakes in the background,
        graticule, and border colours, so switching theme has to invalidate it or
        the previous theme's basemap stays on screen; it is a frozen dataclass of
        strings, hence hashable. The projection is part of it because the
        transform is painted in just as permanently.

        Shared with :meth:`_draw_basemap` so the preview shortcut compares like
        with like. Two keys built to different recipes never compare equal, which
        silently turns "has the view moved?" into a constant.
        """
        return (
            self.width(),
            self.height(),
            # Dragging the window to a monitor of a different density has to
            # rebuild the raster, or a cache baked for the old ratio is
            # rescaled onto the new backing store.
            round(self.devicePixelRatioF(), 4),
            round(self._lon0, 4),
            round(self._lon1, 4),
            round(self._lat0, 4),
            round(self._lat1, 4),
            self._projection,
            _map(),
        )

    def _basemap_pixmap(self):
        key = self._basemap_key()
        if self._basemap_cache is not None and self._cache_key == key:
            return self._basemap_cache

        p = self._proj()
        shared_key = (
            self.width(), self.height(), self.devicePixelRatioF(),
            self._lon0, self._lon1, self._lat0, self._lat1,
            self._projection, p.cone, id(self._layers), _map(),
            mono_font("caption").toString(),
        )
        shared = _SHARED_BASEMAP_FRAMES.get(shared_key)
        if shared is not None:
            _SHARED_BASEMAP_FRAMES.move_to_end(shared_key)
            self._basemap_cache = shared
            self._cache_key = key
            self._cache_proj = p
            return shared

        pm = self._device_pixmap()
        pm.fill(QColor(_map().background))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        self._draw_graticule(qp, p)
        # T16.1 hierarchy, dimmest first: internal borders, national borders,
        # lake shores, then coastline on top. Weather imagery is blitted above
        # this raster at paint time, so boundaries would vanish under a filled
        # field; the crisp outline pass in paintEvent redraws them there.
        self._draw_boundary_layers(qp, p)
        qp.end()

        self._basemap_cache = pm
        self._cache_key = key
        self._cache_proj = p
        _remember_frame(_SHARED_BASEMAP_FRAMES, shared_key, pm)
        return pm

    def boundary_zorder(self) -> tuple[str, ...]:
        """Return the boundary paint order, dimmest first (T16.1)."""
        return boundary_draw_order(tuple(self._layers.keys()))

    def _draw_boundary_layers(self, qp, p) -> None:
        """Draw every boundary family in hierarchy order (T16.1)."""
        palette = _map()
        order = self.boundary_zorder()
        widths = {
            "states": boundary_width("states"),
            "countries": boundary_width("countries"),
            # Lake shores share the coastline colour and sit just under it:
            # they are the same kind of boundary, so giving them their own hue
            # would imply a distinction that does not exist. Slightly finer,
            # because a lake outline enclosing a small area reads heavier than
            # an open coast of the same weight.
            "lakes": boundary_width("lakes"),
            "coastline": boundary_width("coastline"),
        }
        colours = {
            "states": palette.states,
            "countries": palette.countries,
            "lakes": palette.coastline,
            "coastline": palette.coastline,
        }
        for name in order:
            self._draw_layer(
                qp, self._layers.get(name, []),
                colours.get(name, palette.countries),
                widths.get(name, 1.0), p,
            )

    def _draw_boundary_outlines(self, qp, p) -> None:
        """Redraw boundaries above filled weather imagery (T16.1).

        The basemap raster bakes boundaries under everything painted later, so
        essential geography would vanish under a continent-wide model field or
        radar wash. This crisp vector pass runs above rasters and vector
        overlays (but below markers, readouts, and legends) so coastlines and
        borders stay legible while the weather data remains the visual focus.
        Antialiasing stays on: these are the same polylines, not a second
        styling.

        Cached per view: reprojecting ~113k basemap vertices on every paint
        frame is the other half of the lag the labels added. The clip keeps
        drawing inside the window; the cache key in :meth:`_outline_key`
        invalidates on any view/size/projection/palette change.
        """
        key = (self._outline_key(p), round(self.devicePixelRatioF(), 4))
        cached = getattr(self, "_outline_raster_cache", None)
        if cached is not None and cached[0] == key:
            qp.drawPixmap(0, 0, cached[1])
            return
        palette = _map()
        appearance = (self.width(), self.height(), key[1], palette.states,
                      palette.countries, palette.coastline)
        if (cached is not None
                and cached[3] == appearance
                and cached[2].cone == p.cone
                and self._basemap_refresh_timer.isActive()):
            source_w, source_h = self._independent_size(cached[1])
            destination, source = self._reblit_geometry(
                cached[2], p, source_w, source_h)
            qp.save()
            qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            qp.drawPixmap(
                destination, cached[1], self._device_source(cached[1], source))
            qp.restore()
            return

        shared_key = (key, id(self._layers))
        shared = _SHARED_OUTLINE_FRAMES.get(shared_key)
        if shared is not None:
            _SHARED_OUTLINE_FRAMES.move_to_end(shared_key)
            self._outline_raster_cache = (key, shared, p, appearance)
            qp.drawPixmap(0, 0, shared)
            return

        layer = self._device_pixmap()
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            for poly, colour, width in self._cached_boundary_outlines(p):
                painter.setPen(QPen(QColor(colour), width))
                painter.drawPolyline(poly)
        finally:
            painter.end()
        self._outline_raster_cache = (key, layer, p, appearance)
        _remember_frame(_SHARED_OUTLINE_FRAMES, shared_key, layer)
        qp.drawPixmap(0, 0, layer)

    def _outline_key(self, p) -> tuple:
        """Everything the projected boundary outlines depend on.

        Keyed on the resolved transform itself, not the rounded view bounds:
        a cache hit must mean these exact pixels, not pixels for a view
        within rounding tolerance.
        """
        palette = _map()
        return (
            p.kind,
            p.cone,
            round(p.scale, 9),
            round(p.offx, 4),
            round(p.offy, 4),
            round(p.k, 9),
            round(p.x0, 6),
            round(p.y1, 6),
            self.width(),
            self.height(),
            palette.states,
            palette.countries,
            palette.coastline,
        )

    def _cached_boundary_outlines(self, p) -> tuple:
        """Return projected ``(QPolygonF, colour, width)`` outlines, cached."""
        key = self._outline_key(p)
        cached = self._outline_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        palette = _map()
        colours = {
            "states": palette.states,
            "countries": palette.countries,
            "lakes": palette.coastline,
            "coastline": palette.coastline,
        }
        widths = {
            "states": boundary_width("states"),
            "countries": boundary_width("countries"),
            "lakes": boundary_width("lakes"),
            "coastline": boundary_width("coastline"),
        }
        lon_pad = (self._lon1 - self._lon0) * 0.15
        lat_pad = (self._lat1 - self._lat0) * 0.15
        vlon0, vlon1 = self._lon0 - lon_pad, self._lon1 + lon_pad
        vlat0, vlat1 = self._lat0 - lat_pad, self._lat1 + lat_pad
        outlines = []
        for name in self.boundary_zorder():
            colour = colours.get(name, palette.countries)
            width = widths.get(name, 1.0)
            for (blo0, blo1, bla0, bla1), pts in self._layers.get(name, []):
                if blo1 < vlon0 or blo0 > vlon1 or bla1 < vlat0 or bla0 > vlat1:
                    continue
                poly = QPolygonF()
                for lon, lat in pts:
                    x, y = p.forward(lon, lat)
                    poly.append(QPointF(x, y))
                outlines.append((poly, colour, width))
        outlines = tuple(outlines)
        self._outline_cache = (key, outlines)
        return outlines

    def _draw_basemap(self, qp: QPainter) -> None:
        """Draw either the exact basemap or a fast transformed wheel preview."""
        key = self._basemap_key()
        new_projection = self._proj()
        previewing = (
            self._basemap_cache is not None
            and self._cache_proj is not None
            and self._cache_key != key
            and self._basemap_refresh_timer.isActive()
            # The shortcut below re-blits the cached raster under a changed
            # view, which holds exactly while the two transforms differ by a
            # scale-and-translate. Both views reach pixels that way -- flat from
            # squeezed degrees, curved from the cone's plane -- so what has to
            # match is the step in front of it: the cone. ``None == None`` is
            # the flat case; identical constants is a curved view whose cone is
            # pinned for the gesture. A *different* cone bows its parallels
            # differently and genuinely has to be redrawn.
            and self._cache_proj.cone == new_projection.cone
        )
        if not previewing:
            qp.drawPixmap(0, 0, self._basemap_pixmap())
            return

        source_w, source_h = self._independent_size(self._basemap_cache)
        destination, source = self._reblit_geometry(
            self._cache_proj, new_projection, source_w, source_h
        )
        qp.save()
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        qp.drawPixmap(
            destination,
            self._basemap_cache,
            self._device_source(self._basemap_cache, source),
        )
        qp.restore()

    @staticmethod
    def _reblit_geometry(old, new, source_w: float, source_h: float):
        """Map a raster drawn under ``old`` into the view ``new``.

        Returns ``(destination, source)`` for a single ``drawPixmap``. Valid only
        while ``old.cone == new.cone``: the two transforms then differ by how the
        shared intermediate plane is fitted to the widget, which is a
        scale-and-translate, and that is what this expresses. A different cone
        bows its parallels differently and genuinely has to be redrawn.

        Shared by the basemap preview and the warped-imagery preview so the two
        cannot drift apart on arithmetic they both depend on.
        """
        old_k, old_scale, old_offx, old_offy, old_x0, old_y1 = (
            old.k,
            old.scale,
            old.offx,
            old.offy,
            old.x0,
            old.y1,
        )
        new_k, new_scale, new_offx, new_offy, new_x0, new_y1 = (
            new.k,
            new.scale,
            new.offx,
            new.offy,
            new.x0,
            new.y1,
        )
        scale_x = new_k * new_scale / (old_k * old_scale)
        dest_x = (
            new_offx
            + ((old_x0 - old_offx / old_scale) * (new_k / old_k) - new_x0) * new_scale
        )
        scale_y = new_scale / old_scale
        dest_y = new_offy + (new_y1 - old_y1) * new_scale - old_offy * scale_y
        source = QRectF(0.0, 0.0, source_w, source_h)
        destination = QRectF(dest_x, dest_y, source_w * scale_x, source_h * scale_y)
        return destination, source

    def _draw_graticule(self, qp, p) -> None:
        span = self._lon1 - self._lon0
        step = 5 if span <= 40 else (10 if span <= 90 else (20 if span <= 200 else 30))
        grid = QPen(QColor(_map().graticule), 1)
        label = QColor(_map().graticule_label)
        # Graticule labels are figures, so the tabular family keeps the degree
        # values aligned. "Helvetica" here resolved to a silent substitution on
        # Windows, and changed again once render.install_font replaced QFont.
        qp.setFont(mono_font("caption"))
        # A straight line between two endpoints is only a meridian or a parallel
        # while the transform is affine. On the conic both bow, so they are
        # sampled and drawn as polylines.
        samples = 1 if p.affine else 24

        def graticule_line(lon_a, lat_a, lon_b, lat_b):
            if samples == 1:
                qp.drawLine(self._to_px(lon_a, lat_a, p), self._to_px(lon_b, lat_b, p))
                return
            poly = QPolygonF()
            for index in range(samples + 1):
                frac = index / samples
                poly.append(
                    self._to_px(
                        lon_a + (lon_b - lon_a) * frac,
                        lat_a + (lat_b - lat_a) * frac,
                        p,
                    )
                )
            qp.drawPolyline(poly)

        lon = int(self._lon0 // step * step)
        while lon <= self._lon1:
            qp.setPen(grid)
            graticule_line(lon, self._lat0, lon, self._lat1)
            qp.setPen(QPen(label))
            top = self._to_px(lon, self._lat1, p)
            qp.drawText(
                QRectF(top.x() - 24, 2, 48, 12), Qt.AlignCenter, self._fmt_lon(lon)
            )
            lon += step
        lat = int(self._lat0 // step * step)
        while lat <= self._lat1:
            qp.setPen(grid)
            graticule_line(self._lon0, lat, self._lon1, lat)
            qp.setPen(QPen(label))
            left = self._to_px(self._lon0, lat, p)
            qp.drawText(
                QRectF(3, left.y() - 7, 34, 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._fmt_lat(lat),
            )
            lat += step

    @staticmethod
    def _fmt_lat(lat: int) -> str:
        return f"{abs(lat)}\u00b0{'N' if lat >= 0 else 'S'}"

    @staticmethod
    def _fmt_lon(lon: int) -> str:
        lon = ((lon + 180) % 360) - 180  # normalize to [-180, 180)
        return f"{abs(lon)}\u00b0{'E' if lon >= 0 else 'W'}"

    def _draw_layer(self, qp, prepped, color, width, p) -> None:
        if not prepped:
            return
        qp.setPen(QPen(QColor(color), width))
        # Pad the clip window by one extent-span so partially visible lines draw.
        lon_pad = (self._lon1 - self._lon0) * 0.15
        lat_pad = (self._lat1 - self._lat0) * 0.15
        vlon0, vlon1 = self._lon0 - lon_pad, self._lon1 + lon_pad
        vlat0, vlat1 = self._lat0 - lat_pad, self._lat1 + lat_pad
        for (blo0, blo1, bla0, bla1), pts in prepped:
            if blo1 < vlon0 or blo0 > vlon1 or bla1 < vlat0 or bla0 > vlat1:
                continue  # bbox entirely outside the view
            poly = QPolygonF()
            for lon, lat in pts:
                poly.append(self._to_px(lon, lat, p))
            qp.drawPolyline(poly)

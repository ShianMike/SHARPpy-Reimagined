"""Raster image caching and curved-projection warping."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt, QRectF
from qtpy.QtGui import QImage, QPainter, QPixmap, QTransform

from sharpmod.maps.map_overlays import OverlayRaster

#: Target cell size, in degrees, for warping imagery into a curved projection.
#:
#: The piecewise transform treats each cell as affine, so the error inside one
#: grows with how much the projection curves across it. Two degrees keeps that
#: under a pixel at every extent the picker offers, measured against projecting
#: each pixel exactly, while staying coarse enough that a full-CONUS frame is a
#: few hundred blits rather than a few thousand.
_WARP_TARGET_CELL_DEG = 2.0
#: Bounds on the lattice. The floor keeps a tiny extent from being warped by a
#: single cell, which would be the rectangle this code exists to avoid; the
#: ceiling bounds one frame's cost.
_WARP_MIN_CELLS = 2
_WARP_MAX_CELLS = 48


class MapRasterMixin:
    """Draw raster overlays in flat and curved projections."""

    def _raster_pixmap(self, key: str, raster: OverlayRaster):
        """Return the decoded pixmap for ``raster``, decoding at most once.

        The no-data pixels in a WMS radar frame are white with zero alpha, so the
        image is converted to a premultiplied format before it is ever scaled.
        Scaling straight ARGB32 interpolates those white pixels into the edge of
        every echo and rings each storm with a pale halo.
        """
        cached = self._raster_pixmaps.get(key)
        if cached is not None and cached[0] is raster.image_bytes:
            return cached[1]

        image = QImage()
        if not image.loadFromData(raster.image_bytes):
            # Remember the failure. Retrying a corrupt payload on every repaint
            # would burn the decode cost dozens of times a second while hovering.
            self._raster_pixmaps[key] = (raster.image_bytes, None)
            return None
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._raster_pixmaps[key] = (raster.image_bytes, None)
            return None
        self._raster_pixmaps[key] = (raster.image_bytes, pixmap)
        return pixmap

    def _visible_lonlat_bounds(self, p) -> tuple[float, float, float, float]:
        """Return the lon/lat envelope the widget can actually show.

        Not the same thing as the requested extent, and that difference is a bug
        magnet. Both transforms *letterbox*: the extent is fitted into the widget
        at one uniform scale, so whichever axis is not the limiting one leaves
        margin, and the conic additionally fits a **bowed** outline into a
        rectangle, which leaves a crescent of margin along every edge. All of
        that margin is real map -- the basemap draws coastlines into it -- so
        anything that clamps itself to the requested extent instead stops short
        of the window and appears to be cut off along a curve.

        Derived by inverse-projecting a ring of points around the widget border
        rather than by padding a guess, because the margin's width depends on the
        aspect ratio and on how hard the cone bows, and a fixed pad would be too
        small at some extents and wasteful at others. The corners are included:
        on the conic they reach furthest outside the extent.
        """
        width = max(1, self.width())
        height = max(1, self.height())
        inverse = p.inverse
        lons: list[float] = []
        lats: list[float] = []
        steps = 8
        for index in range(steps + 1):
            fx = width * index / steps
            fy = height * index / steps
            for x, y in ((fx, 0.0), (fx, float(height)), (0.0, fy), (float(width), fy)):
                try:
                    lon, lat = inverse(x, y)
                except Exception:  # noqa: BLE001 - a limit point is not fatal
                    continue
                if math.isfinite(lon) and math.isfinite(lat):
                    lons.append(lon)
                    lats.append(lat)
        if not lons:
            # Nothing invertible: fall back to the requested extent, which is at
            # worst the behaviour this replaced.
            return (
                min(self._lon0, self._lon1),
                max(self._lon0, self._lon1),
                min(self._lat0, self._lat1),
                max(self._lat0, self._lat1),
            )
        return (min(lons), max(lons), min(lats), max(lats))

    def _draw_raster_overlays(self, qp, p) -> None:
        """Blit every visible georeferenced image into the current projection.

        Only the visible sub-rectangle of the source is drawn. Zoomed in far
        enough, the full destination rectangle would be orders of magnitude
        larger than the widget, and letting Qt scale the whole frame and then
        clip it wastes that work on pixels nobody sees.

        On the flat view a lon/lat box maps to an axis-aligned pixel box, so a
        plate-carree source needs no resampling beyond the scale Qt is already
        doing, and the whole visible window goes down in one call.

        The curved view has no such rectangle, so it takes
        :meth:`_draw_raster_warped` instead. Imagery used to be withheld there
        entirely -- correct at the time, since blitting it as a rectangle would
        have put radar echoes a hundred kilometres from the storm, and a
        misplaced echo is worse than an absent one. It is drawn now because the
        transform is applied properly rather than ignored.
        """
        if not p.affine:
            self._draw_raster_warped(qp, p)
            return
        view = self._visible_lonlat_bounds(p)
        for key, raster in self._visible_rasters():
            pixmap = self._raster_pixmap(key, raster)
            if pixmap is None:
                continue

            min_lon, max_lon, min_lat, max_lat = raster.bounds
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            if lon_span <= 0.0 or lat_span <= 0.0:
                continue

            # Visible window, clamped to what the image actually covers. The
            # window is what the widget can *show*, not what was requested, so
            # the image fills the letterbox margin instead of stopping at the
            # extent edge with basemap still drawn beyond it.
            vlon0 = max(min_lon, view[0])
            vlon1 = min(max_lon, view[1])
            vlat0 = max(min_lat, view[2])
            vlat1 = min(max_lat, view[3])
            if vlon1 <= vlon0 or vlat1 <= vlat0:
                continue

            img_w = float(pixmap.width())
            img_h = float(pixmap.height())
            # Source pixels: x grows east, y grows south from the north edge.
            sx0 = (vlon0 - min_lon) / lon_span * img_w
            sx1 = (vlon1 - min_lon) / lon_span * img_w
            sy0 = (max_lat - vlat1) / lat_span * img_h
            sy1 = (max_lat - vlat0) / lat_span * img_h
            source = QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)
            if source.width() <= 0.0 or source.height() <= 0.0:
                continue

            destination = QRectF(
                self._to_px(vlon0, vlat1, p),
                self._to_px(vlon1, vlat0, p),
            )

            # Smooth only when shrinking the image. Magnifying a reflectivity
            # field bilinearly invents gradients between data cells, which is
            # what makes a zoomed-in radar overlay look blurred rather than
            # coarse; nearest-neighbour keeps the cells the source actually
            # published, the way dedicated radar displays present them. When
            # minifying, smoothing is still wanted -- it stops isolated cells
            # aliasing in and out as the view moves.
            # The destination is logical and the source is in image pixels, so
            # the comparison has to be brought into one unit first. Left in
            # logical terms it understates the destination by exactly the device
            # pixel ratio, concluding "minifying" while genuinely enlarging, and
            # smoothing the cells it meant to preserve.
            ratio = self.devicePixelRatioF()
            magnifying = (
                destination.width() * ratio > source.width()
                or destination.height() * ratio > source.height()
            )
            qp.save()
            qp.setRenderHint(QPainter.SmoothPixmapTransform, not magnifying)
            qp.setOpacity(raster.opacity)
            qp.drawPixmap(destination, pixmap, source)
            qp.restore()

    def _draw_raster_warped(self, qp, p) -> None:
        """Blit imagery through a non-affine transform, one small cell at a time.

        A conic has no rectangle to blit into, but it is smooth: over a small
        enough patch of the map it is indistinguishable from a scale, rotation and
        shear, which is exactly what a ``QTransform`` expresses. So the visible
        window is cut into a grid of cells, each cell's four corners are projected
        properly, and each is drawn through the transform that carries its source
        rectangle onto the resulting quadrilateral. The seams line up because
        adjacent cells are built from the same projected corners.

        Cell count follows the view span rather than being fixed: the error inside
        a cell grows with how much the projection curves across it, so a
        continent-wide view needs a fine grid and a county-wide view needs almost
        none. :data:`_WARP_TARGET_CELL_DEG` sets the target cell size, and the
        bounds keep a single frame's cost bounded either way.

        The result is cached and, during a pan or zoom, re-blitted rather than
        rebuilt -- the same treatment the vector basemap gets, and for the same
        reason. A full-CONUS lattice is a few hundred transformed blits, which
        measured 28 ms a frame with one field attached and 53 ms with two; that is
        a fine cost once per settled view and far too much per mouse-move. The
        cone is pinned for the duration of a gesture, so the cached frame differs
        from the new view by a scale-and-translate and :meth:`_reblit_geometry`
        maps it exactly.
        """
        rasters = self._visible_rasters()
        if not rasters:
            return
        key = self._warp_cache_key(rasters, p)
        cached = self._warp_cache
        if cached is not None and cached[0] == key:
            qp.drawPixmap(0, 0, cached[1])
            return

        if (
            cached is not None
            and self._warp_proj is not None
            and self._basemap_refresh_timer.isActive()
            and self._warp_proj.cone == p.cone
        ):
            source_w, source_h = self._independent_size(cached[1])
            destination, source = self._reblit_geometry(
                self._warp_proj, p, source_w, source_h
            )
            qp.save()
            qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            qp.drawPixmap(
                destination, cached[1], self._device_source(cached[1], source)
            )
            qp.restore()
            return

        composed = self._compose_warped(rasters, p)
        if composed is None:
            return
        self._warp_cache = (key, composed)
        self._warp_proj = p
        qp.drawPixmap(0, 0, composed)

    def _warp_cache_key(self, rasters, p) -> tuple:
        """Everything the warped composite depends on.

        Payload identity rather than equality, matching the decode cache: a new
        frame is a new ``bytes`` object, and comparing megabytes of image on every
        repaint would cost more than the warp it is meant to avoid.
        """
        return (
            self.width(),
            self.height(),
            round(self.devicePixelRatioF(), 4),
            round(self._lon0, 4),
            round(self._lon1, 4),
            round(self._lat0, 4),
            round(self._lat1, 4),
            p.cone,
            tuple(
                (key, id(raster.image_bytes), round(raster.opacity, 3))
                for key, raster in rasters
            ),
        )

    def _compose_warped(self, rasters, p):
        """Warp every visible image into one widget-sized transparent layer.

        Composited rather than drawn straight to the widget so the result can be
        cached and re-blitted as a unit. Each image contributes at its own
        opacity here, so the composite blits at full strength and looks identical
        to drawing them one at a time.
        """
        if self.width() <= 0 or self.height() <= 0:
            return None
        layer = self._device_pixmap()
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        try:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self._warp_rasters_onto(painter, rasters, p)
        finally:
            painter.end()
        return layer

    def _warp_rasters_onto(self, qp, rasters, p) -> None:
        """Warp each image in ``rasters`` onto ``qp``. See above."""
        view = self._visible_lonlat_bounds(p)
        for key, raster in rasters:
            pixmap = self._raster_pixmap(key, raster)
            if pixmap is None:
                continue

            min_lon, max_lon, min_lat, max_lat = raster.bounds
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            if lon_span <= 0.0 or lat_span <= 0.0:
                continue

            # What the widget can show, clamped to what the image covers. Using
            # the requested extent here is what made the field stop along a bowed
            # arc with the basemap still drawn past it: the conic fits that bowed
            # outline into a rectangle, and everything between the two is real map.
            vlon0 = max(min_lon, view[0])
            vlon1 = min(max_lon, view[1])
            vlat0 = max(min_lat, view[2])
            vlat1 = min(max_lat, view[3])
            if vlon1 <= vlon0 or vlat1 <= vlat0:
                continue

            cols = max(
                _WARP_MIN_CELLS,
                min(
                    _WARP_MAX_CELLS,
                    int(math.ceil((vlon1 - vlon0) / _WARP_TARGET_CELL_DEG)),
                ),
            )
            rows = max(
                _WARP_MIN_CELLS,
                min(
                    _WARP_MAX_CELLS,
                    int(math.ceil((vlat1 - vlat0) / _WARP_TARGET_CELL_DEG)),
                ),
            )

            img_w = float(pixmap.width())
            img_h = float(pixmap.height())
            forward = p.forward

            # Corner lattice, projected once and shared by the four cells that
            # meet at it: projecting per cell would trace every interior corner
            # four times and let rounding open seams between neighbours.
            lons = [vlon0 + (vlon1 - vlon0) * index / cols for index in range(cols + 1)]
            lats = [vlat1 + (vlat0 - vlat1) * index / rows for index in range(rows + 1)]
            projected = [[forward(lon, lat) for lon in lons] for lat in lats]

            qp.save()
            qp.setOpacity(raster.opacity)
            qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for row in range(rows):
                for col in range(cols):
                    self._warp_cell(
                        qp,
                        pixmap,
                        projected,
                        lons,
                        lats,
                        row,
                        col,
                        min_lon,
                        max_lat,
                        lon_span,
                        lat_span,
                        img_w,
                        img_h,
                    )
            qp.restore()

    @staticmethod
    def _warp_cell(
        qp,
        pixmap,
        projected,
        lons,
        lats,
        row,
        col,
        min_lon,
        max_lat,
        lon_span,
        lat_span,
        img_w,
        img_h,
    ) -> None:
        """Draw one cell of a warped raster. See :meth:`_draw_raster_warped`."""
        # Two rectangles, and the distinction matters. ``base`` is the cell's
        # true source extent, and it is what the projected corners correspond to,
        # so it is what the transform must be derived from. ``bleed`` is that
        # grown half a pixel on every side and is what gets drawn, so adjacent
        # cells overlap by a pixel instead of leaving a hairline of background
        # showing through every seam.
        #
        # Deriving the transform from ``bleed`` instead would scale every cell
        # down by the bleed, opening exactly the seams the bleed exists to close.
        sx0 = (lons[col] - min_lon) / lon_span * img_w
        sx1 = (lons[col + 1] - min_lon) / lon_span * img_w
        sy0 = (max_lat - lats[row]) / lat_span * img_h
        sy1 = (max_lat - lats[row + 1]) / lat_span * img_h
        if sx1 <= sx0 or sy1 <= sy0:
            return
        base = QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)
        bleed = QRectF(sx0 - 0.5, sy0 - 0.5, (sx1 - sx0) + 1.0, (sy1 - sy0) + 1.0)

        top_left = projected[row][col]
        top_right = projected[row][col + 1]
        bottom_right = projected[row + 1][col + 1]
        bottom_left = projected[row + 1][col]

        # Off-screen cells still cost a transform and a clip, so reject them on
        # the projected corners first. A continent-wide conic view puts most of
        # the lattice outside the widget.
        xs = (top_left[0], top_right[0], bottom_right[0], bottom_left[0])
        ys = (top_left[1], top_right[1], bottom_right[1], bottom_left[1])
        if max(xs) < -2.0 or min(xs) > qp.device().width() + 2.0:
            return
        if max(ys) < -2.0 or min(ys) > qp.device().height() + 2.0:
            return

        # An *affine* fit to the four corners, not the exact projective map
        # through them. Two reasons, and the first is the important one: Qt
        # rasterizes a perspective transform on a far slower path than an affine
        # one, and measured over a full-CONUS lattice that difference was 44 ms a
        # frame against 7 ms. The second is that it costs nothing here -- the
        # cells are small enough that the projection is already affine across one
        # to well under a pixel, which is what
        # ``test_the_warp_places_geometry_where_the_projection_says`` holds.
        #
        # The fit averages both pairs of opposite edges and pins the cell centre,
        # so the residual is split across the cell instead of piling up at the one
        # corner a three-point fit would leave out.
        width = base.width()
        height = base.height()
        if width <= 0.0 or height <= 0.0:
            return
        m11 = ((top_right[0] - top_left[0]) + (bottom_right[0] - bottom_left[0])) / (
            2.0 * width
        )
        m12 = ((top_right[1] - top_left[1]) + (bottom_right[1] - bottom_left[1])) / (
            2.0 * width
        )
        m21 = ((bottom_left[0] - top_left[0]) + (bottom_right[0] - top_right[0])) / (
            2.0 * height
        )
        m22 = ((bottom_left[1] - top_left[1]) + (bottom_right[1] - top_right[1])) / (
            2.0 * height
        )
        centre_x = (top_left[0] + top_right[0] + bottom_right[0] + bottom_left[0]) / 4.0
        centre_y = (top_left[1] + top_right[1] + bottom_right[1] + bottom_left[1]) / 4.0
        source_cx = base.left() + width / 2.0
        source_cy = base.top() + height / 2.0
        dx = centre_x - m11 * source_cx - m21 * source_cy
        dy = centre_y - m12 * source_cx - m22 * source_cy
        if not (m11 * m22 - m12 * m21):
            # Degenerate: a cell collapsed to a line at the projection's limit.
            # Skipping it loses one cell, not the frame.
            return

        qp.save()
        qp.setTransform(QTransform(m11, m12, m21, m22, dx, dy), True)
        qp.drawPixmap(bleed, pixmap, bleed)
        qp.restore()

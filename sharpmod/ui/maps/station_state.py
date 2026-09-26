"""MapStationState behavior for station maps."""

from __future__ import annotations

from qtpy.QtCore import QPointF
from sharpmod.ui.maps.navigation import MAP_PROJECTIONS
from sharpmod.ui.maps.projection import _Projection
from sharpmod.ui.maps.projection import _conic_constants
import math
from sharpmod.ui.features.gui_maps import RADAR_PICK_MARGIN_PX


class MapStationStateMixin:
    """StationMapWidget methods grouped by responsibility."""

    def set_stations(self, stations) -> None:
        """Replace the plotted station set (keeps a valid selection).

        Used when the datetime-aware station list is refreshed from UWyo: the
        map redraws with exactly the stations available at that time. The
        current selection/hover is cleared if it no longer exists.
        """
        self._stations = list(stations)
        self._stations_revision += 1
        self._station_hit_cache = None
        # Selected/pinned station text rectangles are protected inputs to the
        # town declutter, so a refreshed catalog can change both layouts even
        # when the station id itself stays the same.
        self._label_layout_cache = None
        self._station_label_layout_cache = None
        ids = {s["id"] for s in self._stations}
        if self._selected_id not in ids:
            self._selected_id = None
        if self._hover_id not in ids:
            self._hover_id = None
            self._hover_lonlat = None
        self.update()

    def set_selected(self, sid: str | None) -> None:
        if sid == self._selected_id:
            return
        self._selected_id = sid
        self._label_layout_cache = None
        self._station_label_layout_cache = None
        self._refresh_map_accessible_description()
        self.update()

    def _pin_station_snapshot(self, station) -> None:
        """Pin an inspection card for a station without selecting it (T18.2)."""
        try:
            lat = float(station["lat"])
            lon = float(station["lon"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        when = ""
        try:
            valid = self.valid_time()
            if valid is not None:
                when = valid.strftime("Selected %d %b %H%MZ")
        except Exception:  # noqa: BLE001 - time text is advisory
            when = ""
        self._inspection = {
            "kind": "station",
            "station_id": str(station.get("id") or ""),
            "station_name": str(station.get("name") or ""),
            "lat": float(lat),
            "lon": float(lon),
            "when_label": when,
        }
        self.update()

    def center_on(self, sid: str) -> None:
        """Pan the view so ``sid`` is centred (keeps the current zoom span)."""
        st = self._station(sid)
        if st is None:
            return
        span_lon = (self._lon1 - self._lon0) / 2.0
        span_lat = (self._lat1 - self._lat0) / 2.0
        self._lon0, self._lon1 = st["lon"] - span_lon, st["lon"] + span_lon
        self._lat0, self._lat1 = st["lat"] - span_lat, st["lat"] + span_lat
        self._invalidate()

    def view_bounds(self) -> tuple[float, float, float, float]:
        """Return the visible extent as ``(lon0, lon1, lat0, lat1)``.

        Exists so a controller can decide whether a regional product could be
        seen at all before spending a request on it, without reaching into the
        widget's private viewport fields.
        """
        return (self._lon0, self._lon1, self._lat0, self._lat1)

    def context_point(self) -> tuple[float, float]:
        """Return ``(latitude, longitude)`` for nearby environmental data.

        A selected radiosonde station is the most relevant anchor. With no
        selection, use the map centre so optional context remains usable while
        the user is navigating.
        """

        station = self._station(self._selected_id) if self._selected_id else None
        if station is not None:
            return float(station["lat"]), float(station["lon"])
        return (self._lat0 + self._lat1) / 2.0, (self._lon0 + self._lon1) / 2.0

    def _invalidate(self) -> None:
        self._basemap_refresh_timer.stop()
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        # The warped imagery is built under the same transform as the
        # basemap, so it goes stale at exactly the same moments.
        self._warp_cache = None
        self._warp_proj = None
        self._conic_freeze = None
        # Label and outline layouts are projected pixels: a new view, size, or
        # projection invalidates them exactly like the basemap raster.
        self._label_layout_cache = None
        self._station_label_layout_cache = None
        self._outline_cache = None
        self._outline_raster_cache = None
        self._refresh_map_accessible_description()
        self.update()
        # An extent set outright -- a region change, a reset, a resize -- is a
        # settled view straight away; there is no gesture to wait out.
        self._announce_view_settled()

    def _queue_map_preview(self) -> None:
        """Reuse the current raster during rapid pan or wheel input.

        Re-rasterizing the vector basemap takes tens of milliseconds, so doing
        it for every input event makes input queue up.  Keep the last crisp
        frame as a transformed preview and rebuild it once input pauses.

        On the curved view this also pins the cone, which is what makes the
        preview possible at all. Re-blitting the cached raster needs the two
        transforms to differ by a scale-and-translate, and refitting the
        standard parallels to the moving extent breaks that: every mouse-move
        then re-projects all ~113k basemap vertices, which measured 101 ms a
        frame against 3 ms with the cone pinned.

        The pin lasts for the gesture and no longer. It used to be held until a
        region was picked, on the reasoning that a projection is a property of a
        chart and panning only moves the viewport through it. That is true of a
        GIS layer in a fixed coordinate system, and wrong here: this cone is
        fitted to the view, so holding it across a session's worth of pans left
        the map leaning further and further from north with no way back. See
        :meth:`_finish_map_preview`, which releases it once the view settles.

        Held, not frozen: :meth:`_build_proj` still fits a fresh cone each time
        to decide whether the view is one a cone can represent at all, so
        zooming out past the conic limit falls back to flat as it always did.
        """
        if self._basemap_cache is None or self._cache_proj is None:
            # Before the first painted frame there is nothing to re-blit. Keep
            # the gesture pending instead of treating each move as a settled
            # view (which also wakes every view-dependent overlay listener).
            self._basemap_refresh_timer.start()
            self.update()
            return
        if self._conic_freeze is None:
            # The cone of the raster being previewed, so the transform stays a
            # scale-and-translate away from what is already on screen. ``None``
            # on a flat view, which leaves that path exactly as it was.
            self._conic_freeze = self._cache_proj.cone
        self._basemap_refresh_timer.start()
        self.update()

    def _finish_map_preview(self) -> None:
        """Discard the temporary preview and request one crisp vector frame.

        The cone is released here, so the settled view is drawn on a cone fitted
        to *it* rather than to wherever the view happened to be when the region
        was chosen.

        It used to survive, to avoid the map re-bowing a fifth of a second after
        the drag ended. The cost of that turned out to be far worse than the
        settle it avoided: a conic's central meridian is the longitude it was cut
        along, and screen-up only points at true north there. Every degree the
        view travels east or west leaves the map leaning by ``n`` degrees -- about
        0.6 at CONUS latitudes -- and because the cone was only released when a
        region was picked, the lean accumulated across every pan of the session
        and never came back. Measured from a CONUS fit: 12 degrees of lean after
        panning 20 degrees west, 16.9 by the west coast, 13.3 over New England.
        A map that leans by sixteen degrees is not a map anybody asked for.

        So the settle is accepted and the lean is not. Refitting restores true
        north exactly, and the content shift it brings arrives while the basemap
        is being rebuilt anyway, at rest, after the user has let go -- which reads
        as the map finishing rather than as the map moving.

        The cone is still held *during* the gesture by :meth:`_queue_map_preview`,
        which is what keeps a drag re-blittable instead of re-projecting 113k
        vertices per mouse-move. Only the settled frame refits.
        """
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        # Released so the settled frame refits to the view it actually shows.
        self._conic_freeze = None
        # The warped imagery is built under the same transform as the
        # basemap, so it goes stale at exactly the same moments.
        self._warp_cache = None
        self._warp_proj = None
        self._outline_raster_cache = None
        self.update()
        self._announce_view_settled()

    def _announce_view_settled(self) -> None:
        """Tell listeners the view has come to rest. See :attr:`viewSettled`.

        Guarded because this runs from a timer and during teardown: a listener
        raising here would otherwise escape into the Qt event loop, where an
        exception has nowhere useful to go.
        """
        try:
            self.viewSettled.emit()
        except RuntimeError:  # pragma: no cover - widget already destroyed
            pass

    def projection(self) -> str:
        """Return the active map view, ``"flat"`` or ``"curved"``."""
        return self._projection

    def set_projection(self, name) -> None:
        """Choose the map view. Unknown names fall back to ``"flat"``.

        The selected geographic point and the usable extent are preserved
        (T17.3): switching views recentres the conic's central meridian on
        the current view but never moves the sounding selection or station,
        and the extent is clamped back into the projectable band. The
        effective (drawn) projection is reported by
        :meth:`effective_projection` and the scale bar, so a fallback to
        flat is visible rather than silent.
        """
        wanted = str(name or "flat").strip().lower()
        if wanted not in MAP_PROJECTIONS:
            wanted = "flat"
        if wanted == self._projection:
            return
        # Capture the geographic anchor before the transform changes so the
        # same ground stays under the cursor: the view centre in lon/lat.
        anchor_lon = (self._lon0 + self._lon1) / 2.0
        anchor_lat = (self._lat0 + self._lat1) / 2.0
        self._projection = wanted
        # The cached basemap raster bakes the transform in, so it cannot be
        # reused across a projection change.
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        # The warped imagery is built under the same transform as the
        # basemap, so it goes stale at exactly the same moments.
        self._warp_cache = None
        self._warp_proj = None
        self._conic_freeze = None
        # Projected-pixel caches, same contract as _invalidate.
        self._label_layout_cache = None
        self._outline_cache = None
        self._outline_raster_cache = None
        self._clamp_view()
        # Keep the anchor centred: the two views fit the same extent with
        # different letterboxing, so without this a switch drifts the view
        # by the margin difference.
        try:
            span_lon = (self._lon1 - self._lon0) / 2.0
            span_lat = (self._lat1 - self._lat0) / 2.0
            self._lon0, self._lon1 = anchor_lon - span_lon, anchor_lon + span_lon
            self._lat0 = max(-89.99, anchor_lat - span_lat)
            self._lat1 = min(89.99, anchor_lat + span_lat)
            self._clamp_view()
        except Exception:  # noqa: BLE001 - anchor restore never breaks the switch
            pass
        self._refresh_map_accessible_description()
        self.update()

    def _proj(self) -> "_Projection":
        """Return the resolved transform for the current view and size.

        A curved view that a cone cannot represent -- too tall, or centred on the
        equator -- resolves to the flat transform instead of a distorted conic.
        That fallback is silent by design: the alternative is refusing to draw a
        map the user asked for.

        The result is memoized on the view, the widget size, and any pinned
        cone. It is a pure function of those, and a conic one costs 68 boundary
        samples to fit, which is not something to repeat for every mouse-move
        readout.
        """
        w = max(1, self.width())
        h = max(1, self.height())
        key = (
            self._projection,
            self._lon0,
            self._lon1,
            self._lat0,
            self._lat1,
            w,
            h,
            self._conic_freeze,
        )
        cached = self._proj_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        projection = self._build_proj(w, h)
        self._proj_cache = (key, projection)
        return projection

    def _build_proj(self, w: int, h: int) -> "_Projection":
        """Fit the current view into ``w`` x ``h``. See :meth:`_proj`."""
        conic = None
        if self._projection == "curved":
            conic = _conic_constants(
                self._lat0, self._lat1, (self._lon0 + self._lon1) / 2.0
            )
            # Across pans and zooms the cone is the one pinned to the chosen
            # view, so the transform stays re-blittable and the map does not
            # re-bow under the cursor. The fresh fit above is still what decides
            # *whether* there is a cone at all, so zooming out past the conic
            # limit falls back to flat instead of drawing a cone wrapped past
            # the pole. See :meth:`_queue_map_preview`.
            if conic is not None and self._conic_freeze is not None:
                conic = self._conic_freeze
        if conic is not None:
            probe = _Projection("curved", scale=1.0, offx=0.0, offy=0.0, conic=conic)
            # The edges of a conic view bow, so the projected bounds come from
            # sampling the boundary rather than from the four corners alone --
            # corners alone would clip the bulge off the top and bottom edges.
            xs, ys = [], []
            steps = 16
            for index in range(steps + 1):
                frac = index / steps
                lon = self._lon0 + (self._lon1 - self._lon0) * frac
                lat = self._lat0 + (self._lat1 - self._lat0) * frac
                for point in (
                    probe._conic_plane(lon, self._lat0),
                    probe._conic_plane(lon, self._lat1),
                    probe._conic_plane(self._lon0, lat),
                    probe._conic_plane(self._lon1, lat),
                ):
                    xs.append(point[0])
                    ys.append(point[1])
            box_w = max(1e-9, max(xs) - min(xs))
            box_h = max(1e-9, max(ys) - min(ys))
            scale = min(w / box_w, h / box_h)
            return _Projection(
                "curved",
                scale=scale,
                offx=(w - box_w * scale) / 2.0,
                offy=(h - box_h * scale) / 2.0,
                x0=min(xs),
                y1=max(ys),
                conic=conic,
            )

        lat_ref = math.radians((self._lat0 + self._lat1) / 2.0)
        k = max(0.05, math.cos(lat_ref))
        x0 = self._lon0 * k
        x1 = self._lon1 * k
        box_w = max(1e-6, x1 - x0)
        box_h = max(1e-6, self._lat1 - self._lat0)
        scale = min(w / box_w, h / box_h)
        return _Projection(
            "flat",
            scale=scale,
            offx=(w - box_w * scale) / 2.0,
            offy=(h - box_h * scale) / 2.0,
            k=k,
            x0=x0,
            y1=self._lat1,
        )

    def _to_px(self, lon: float, lat: float, p=None) -> QPointF:
        x, y = (self._proj() if p is None else p).forward(lon, lat)
        return QPointF(x, y)

    def _to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return self._proj().inverse(x, y)

    def _station(self, sid):
        for s in self._stations:
            if s["id"] == sid:
                return s
        return None

    def _nearest(self, x: float, y: float, max_px: float = 12.0):
        if not self._stations:
            return None
        p = self._proj()
        cached = getattr(self, "_station_hit_cache", None)
        if cached is None or cached[0] is not p or cached[1] != self._stations_revision:
            w, h = self.width(), self.height()
            projected = []
            for station in self._stations:
                point = self._to_px(station["lon"], station["lat"], p)
                px, py = point.x(), point.y()
                if -5 <= px <= w + 5 and -5 <= py <= h + 5:
                    projected.append((station, px, py))
            cached = (p, self._stations_revision, tuple(projected))
            self._station_hit_cache = cached
        best, best_d2 = None, max_px * max_px
        for station, px, py in cached[2]:
            d2 = (px - x) ** 2 + (py - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = station, d2
        return best

    def set_radar_sites(self, sites) -> None:
        """Show a set of radar antennas as pickable markers.

        Accepts anything with ``id``/``lat``/``lon``, as attributes or as
        mapping keys, so the catalogue's own records can be passed straight in.
        An empty sequence turns the layer off, which is how the radar controller
        hides it when the mosaic scope or the whole overlay is deselected.
        """
        resolved: list[tuple[str, float, float]] = []
        for entry in sites or ():
            try:
                if isinstance(entry, dict):
                    site_id = str(entry["id"])
                    lat = float(entry["lat"])
                    lon = float(entry["lon"])
                else:
                    site_id = str(entry.id)
                    lat = float(entry.lat)
                    lon = float(entry.lon)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if not (-90.0 <= lat <= 90.0):
                continue
            resolved.append((site_id.upper(), ((lon + 180.0) % 360.0) - 180.0, lat))
        markers = tuple(resolved)
        if markers == self._radar_sites:
            return
        self._radar_sites = markers
        self._radar_hit_cache = None
        if not markers:
            self._radar_hover_id = None
        self.update()

    def radar_sites_visible(self) -> bool:
        return bool(self._radar_sites)

    def set_radar_site_selected(self, site_id: str | None) -> None:
        """Mark one antenna as the chosen one, or ``None`` for none."""
        wanted = None if not site_id else str(site_id).strip().upper()
        if wanted == self._radar_site_id:
            return
        self._radar_site_id = wanted
        self.update()

    def _radar_site_hit(self, x: float, y: float, max_px: float = 11.0):
        """Return ``((id, lon, lat), distance_squared)``, or ``None``."""
        if not self._radar_sites:
            return None
        p = self._proj()
        cached = getattr(self, "_radar_hit_cache", None)
        if cached is None or cached[0] is not p or cached[1] is not self._radar_sites:
            w, h = self.width(), self.height()
            projected = []
            for marker in self._radar_sites:
                _site_id, lon, lat = marker
                point = self._to_px(lon, lat, p)
                px, py = point.x(), point.y()
                if -5 <= px <= w + 5 and -5 <= py <= h + 5:
                    projected.append((marker, px, py))
            cached = (p, self._radar_sites, tuple(projected))
            self._radar_hit_cache = cached
        best, best_d2 = None, max_px * max_px
        for marker, px, py in cached[2]:
            d2 = (px - x) ** 2 + (py - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = marker, d2
        return None if best is None else (best, best_d2)

    def _radar_site_at(self, x: float, y: float, max_px: float = 11.0):
        """Return ``(id, lon, lat)`` for the antenna under a point, or ``None``."""
        hit = self._radar_site_hit(x, y, max_px)
        return None if hit is None else hit[0]

    def _distance2(self, pos, lon: float, lat: float) -> float:
        """Squared pixel distance from ``pos`` to a geographic point."""
        pt = self._to_px(lon, lat, self._proj())
        return (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2

    def _pick_radar_site(self, pos, rival_d2: float | None = None) -> bool:
        """Select the antenna under ``pos``. Returns whether one was taken.

        ``rival_d2`` is the squared distance to whatever else is competing for
        the click. Many sounding sites share a mast with a WSR-88D -- Dodge City
        with KDDC, Lincoln with KILX -- so giving the radar an unconditional veto
        would make those stations unselectable for as long as the layer was
        showing, and giving it the click whenever it is merely nearer decides
        co-located pairs by rounding error. The antenna therefore has to be
        :data:`RADAR_PICK_MARGIN_PX` clearly closer than the rival to take the
        click, and the station keeps every shared mast.

        The caller stops when this returns ``True``: a click resolved to a radar
        must not also move the sounding point or select a station under it.
        """
        hit = self._radar_site_hit(pos.x(), pos.y())
        if hit is None:
            return False
        marker, distance2 = hit
        if rival_d2 is not None:
            # Compared as distances, not squares: the margin is a pixel count,
            # and squared units would scale it with how far out the click landed.
            if math.sqrt(distance2) + RADAR_PICK_MARGIN_PX >= math.sqrt(rival_d2):
                return False
        self._radar_site_id = marker[0]
        self.update()
        self.radarSiteSelected.emit(marker[0])
        return True

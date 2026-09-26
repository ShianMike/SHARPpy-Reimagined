"""Map overlay state, visibility, and paint ordering."""

from __future__ import annotations

from sharpmod.maps.map_overlays import OverlayRaster

#: Bottom-to-top paint order for image overlays that share the map.
#:
#: A model field is an environment: a broad, smooth area telling the user what
#: the atmosphere is like. Radar is a location: small, bright, and the thing
#: being located within that environment. So the field goes down first and radar
#: lands on top of it, which is the order every mesoanalysis page uses and the
#: only one where both remain readable -- radar echoes are far too small to
#: survive being covered by a continent-wide wash of colour.
#:
#: Keys not listed here take :data:`RASTER_ORDER_DEFAULT`, so an overlay added
#: later draws above the model field and below nothing, rather than disappearing.
#:
#: This is also the T19 layer-list order: the active-layer list presents rows
#: bottom-to-top in this same sequence (vector overlays above the rasters), so
#: naming the paint order here is what lets the list state it truthfully.
RASTER_DRAW_ORDER = {
    "goes_context": 5,
    "hrrr_field": 10,
    "radar_mosaic": 40,
    "radar_site": 50,
}
RASTER_ORDER_DEFAULT = 30

#: Raster products that only ever represent the provider's newest frame.
#:
#: They have no historical selector, so drawing one while the map is pinned to
#: a past hour would present current conditions as if they belonged to that
#: hour.  Historical mode shelves these frames until the user explicitly
#: returns to live mode.  GOES is intentionally absent: its controller requests
#: the scan nearest the selected time and therefore *is* time-addressed.
LIVE_ONLY_RASTER_KEYS = frozenset(("radar_mosaic", "radar_site"))

#: Bottom-to-top paint order for vector overlays, continuing past the rasters.
#: Storm reports are symbols over areas; the outlook boundary is what they are
#: read against, so it draws last and stays legible.
VECTOR_DRAW_ORDER = {
    "surface_observations": 60,
    "storm_reports": 70,
    "spc_outlook": 80,
}
VECTOR_ORDER_DEFAULT = 65


class MapLayerStateMixin:
    """Maintain visible raster and vector overlays."""

    def set_overlay(self, key: str, layer, *, visible: bool | None = None) -> None:
        """Attach or replace the overlay stored under ``key``.

        Passing ``None`` for ``layer`` removes it. Visibility is remembered
        across replacements so refreshing an overlay for a new valid time does
        not silently re-enable one the user turned off; pass ``visible`` to set
        it explicitly.
        """
        if layer is None:
            self.remove_overlay(key)
            return
        # Routed on type so callers attach either kind through one method. A key
        # is claimed by whichever kind arrives, and the other registry is cleared
        # of it so a product that changes representation cannot leave a stale
        # twin drawing underneath.
        if isinstance(layer, OverlayRaster):
            self._overlays.pop(key, None)
            # T21.4: history is pinned to the *requested* time, not merely to
            # whichever raster happened to be on screen when the mode changed.
            # Comparing against the old raster rejected the correct response
            # after the user intentionally moved from one historical hour to
            # another.  The selected time is the only honest acceptance key.
            if getattr(self, "_map_mode", "live") == "history":
                held = getattr(self, "_held_frames", None)
                if not isinstance(held, dict):
                    held = {}
                    self._held_frames = held
                current = self._rasters.get(key)
                newcomer = getattr(layer, "valid_time", None)
                requested = getattr(self, "_valid_time", None)
                live_only = key in LIVE_ONLY_RASTER_KEYS
                matches_request = requested is not None \
                    and newcomer is not None and newcomer == requested
                if key == "goes_context" and requested is not None \
                        and newcomer is not None:
                    # GOES publishes discrete scans; the provider deliberately
                    # returns the nearest scan within ±20 minutes.  That is the
                    # selected historical answer, not a leaked live frame.
                    from sharpmod.maps.map_time import layer_time_match

                    match_state, _detail = layer_time_match(
                        layer_key=key, requested=requested, actual=newcomer)
                    matches_request = match_state in ("exact", "nearest")
                if live_only or not matches_request:
                    # Keep the newest shelved payload.  A late, older response
                    # must not replace a more recent live frame that is waiting
                    # for the user to leave historical mode.
                    previous = held.get(key)
                    previous_time = (
                        getattr(previous, "valid_time", None)
                        or getattr(previous, "retrieved_at", None)
                    ) if previous is not None else None
                    newcomer_time = newcomer or getattr(layer, "retrieved_at", None)
                    replace_waiting = previous is None or previous_time is None \
                        or newcomer_time is None
                    if not replace_waiting:
                        try:
                            replace_waiting = newcomer_time >= previous_time
                        except TypeError:
                            # A provider returning a naive stamp is malformed,
                            # but shelving it is still safer than drawing it as
                            # the selected historical hour.
                            replace_waiting = True
                    if replace_waiting:
                        held[key] = layer
                    # A live-only image already on screen must leave at once
                    # when history is active; it can return only with an
                    # explicit switch back to Live.
                    if live_only and current is not None:
                        self._rasters.pop(key, None)
                        self._raster_pixmaps.pop(key, None)
                    self.update()
                    return
                # The requested historical frame has arrived.  If the shelved
                # copy is the same frame, it no longer needs a second home;
                # leave any genuinely newer live frame waiting for Live mode.
                waiting = held.get(key)
                if waiting is layer or (
                    waiting is not None
                    and getattr(waiting, "valid_time", None) == newcomer
                ):
                    held.pop(key, None)
            self._rasters[key] = layer
        else:
            self._rasters.pop(key, None)
            self._raster_pixmaps.pop(key, None)
            self._overlays[key] = layer
        if visible is not None:
            self._overlay_visible[key] = bool(visible)
        else:
            self._overlay_visible.setdefault(key, True)
        self.update()

    def remove_overlay(self, key: str) -> None:
        """Detach the overlay stored under ``key``, keeping its toggle state."""
        removed = self._overlays.pop(key, None) is not None
        removed |= self._rasters.pop(key, None) is not None
        held = getattr(self, "_held_frames", None)
        if isinstance(held, dict):
            removed |= held.pop(key, None) is not None
        self._raster_pixmaps.pop(key, None)
        if removed:
            self.update()

    def overlay(self, key: str):
        """Return the overlay stored under ``key``, vector or raster, or ``None``."""
        layer = self._overlays.get(key)
        if layer is not None:
            return layer
        return self._rasters.get(key)

    def overlay_keys(self) -> tuple[str, ...]:
        return tuple(self._overlays) + tuple(self._rasters)

    def set_overlay_visible(self, key: str, visible: bool) -> None:
        """Show or hide one overlay without discarding its geometry.

        The layer is kept so toggling back on is instant and needs no refetch.
        """
        visible = bool(visible)
        if self._overlay_visible.get(key) == visible:
            return
        self._overlay_visible[key] = visible
        self.update()

    def is_overlay_visible(self, key: str) -> bool:
        return bool(self._overlay_visible.get(key, True))

    def _visible_overlays(self) -> list:
        return [
            layer
            for key, layer in self._overlays.items()
            if self._overlay_visible.get(key, True) and layer
        ]

    def _ordered_vector_layers(self) -> list:
        """Return visible vector overlays bottom-to-top (T19.4)."""
        entries = [
            (key, layer)
            for key, layer in self._overlays.items()
            if self._overlay_visible.get(key, True) and layer
        ]
        entries.sort(key=lambda entry: VECTOR_DRAW_ORDER.get(
            entry[0], VECTOR_ORDER_DEFAULT))
        return [layer for _key, layer in entries]

    def paint_order_keys(self) -> tuple[str, ...]:
        """Return every attached overlay key, bottom-to-top (T19.1/T19.4).

        Raster keys in :data:`RASTER_DRAW_ORDER` order first, then vector keys
        in :data:`VECTOR_DRAW_ORDER` order -- exactly the sequence the paint
        path draws. The active-layer list presents this same order through
        :mod:`sharpmod.maps.map_layers` so the user can read which layer covers
        which.
        """
        rasters = sorted(
            (key for key in self._rasters if self._rasters.get(key) is not None),
            key=lambda key: RASTER_DRAW_ORDER.get(key, RASTER_ORDER_DEFAULT))
        vectors = sorted(
            (key for key in self._overlays if self._overlays.get(key) is not None),
            key=lambda key: VECTOR_DRAW_ORDER.get(key, VECTOR_ORDER_DEFAULT))
        return tuple(rasters) + tuple(vectors)

    def occlusion_warnings(self) -> dict[str, str]:
        """Return ``{key: note}`` for visibly occluded fill layers (T19.4).

        Names the covering layer and the mitigation, in the same words the
        active-layer list shows. Only covering-strength fills count: a
        translucent field still reads, and an obscured-but-translucent stack
        is a comparison choice, not a lie.
        """
        from sharpmod.maps.map_layers import OCCLUSION_OPACITY, OPAQUE_FILL_KEYS

        fills = []
        for key in self.paint_order_keys():
            raster = self._rasters.get(key)
            if key not in OPAQUE_FILL_KEYS or raster is None:
                continue
            if not self._overlay_visible.get(key, True):
                continue
            try:
                opacity = float(getattr(raster, "opacity", 1.0))
            except (TypeError, ValueError, OverflowError):
                opacity = 1.0
            if opacity >= OCCLUSION_OPACITY - 1e-9:
                fills.append(key)
        if len(fills) < 2:
            return {}
        top = fills[-1]
        names = {
            "goes_context": "GOES satellite",
            "hrrr_field": "HRRR model field",
            "radar_mosaic": "Radar",
            "radar_site": "Radar",
        }
        top_name = names.get(top, top)
        return {
            key: (f"hidden under {top_name} — lower {top_name}'s opacity "
                  f"below {int(OCCLUSION_OPACITY * 100)}% or switch one off")
            for key in fills[:-1]
        }

    def _raster_decode_failed(self, key: str, raster: OverlayRaster) -> bool:
        """Report whether this exact payload has already failed to decode."""
        cached = self._raster_pixmaps.get(key)
        return (
            cached is not None and cached[1] is None and cached[0] is raster.image_bytes
        )

    def _visible_rasters(self) -> list[tuple[str, OverlayRaster]]:
        """Return visible raster overlays that overlap the current view.

        The view test happens here rather than in the paint loop so a product
        covering somewhere the map is not looking never has its payload decoded.

        A payload already known not to decode is dropped too. That is what keeps
        the legend honest: it is drawn from this same list, so without the filter
        a corrupt frame would be captioned and credited on screen while nothing
        at all had been painted. The entry clears itself when a new frame
        arrives, because the decode cache is keyed on the payload object.
        """
        view = (self._lon0, self._lon1, self._lat0, self._lat1)
        visible = [
            (key, raster)
            for key, raster in self._rasters.items()
            if self._overlay_visible.get(key, True)
            and raster
            and raster.intersects(view)
            and not self._raster_decode_failed(key, raster)
        ]
        # Sorted, because these images stack and dict insertion order is not a
        # z-order: it depends on which overlay the user happened to switch on
        # first, and a remove/set cycle silently reshuffles it. A model field and
        # a radar frame are routinely shown together, and which one ends up on
        # top has to be a decision rather than an accident.
        visible.sort(
            key=lambda entry: RASTER_DRAW_ORDER.get(entry[0], RASTER_ORDER_DEFAULT)
        )
        return visible

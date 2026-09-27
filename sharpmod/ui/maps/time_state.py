"""Map valid-time and live/historical frame state."""

from __future__ import annotations

from datetime import datetime, timezone

from sharpmod.ui.maps.layers import LIVE_ONLY_RASTER_KEYS


class MapTimeMixin:
    """Coordinate map mode, valid time, and held frames."""

    def set_valid_time(self, when: datetime | None) -> None:
        """Record the valid time the map's overlays should describe.

        Only used for display: the map reports when an attached overlay does
        not cover this time so a mismatched product cannot pass for a current
        one. Fetching the right overlay stays the caller's job.
        """
        if self._valid_time == when:
            return
        self._valid_time = when
        # A frame may already have arrived while another historical hour was
        # selected.  Promote it only when its own timestamp exactly matches the
        # newly requested hour; live-only frames never qualify.
        if getattr(self, "_map_mode", "live") == "history" and when is not None:
            held = getattr(self, "_held_frames", None)
            if isinstance(held, dict):
                for key, layer in list(held.items()):
                    if key in LIVE_ONLY_RASTER_KEYS:
                        continue
                    stamp = getattr(layer, "valid_time", None)
                    accepted = stamp == when
                    if key == "goes_context" and stamp is not None:
                        from sharpmod.maps.map_time import layer_time_match

                        state, _detail = layer_time_match(
                            layer_key=key, requested=when, actual=stamp)
                        accepted = state in ("exact", "nearest")
                    if not accepted:
                        continue
                    self._rasters[key] = layer
                    held.pop(key, None)
        self._refresh_map_accessible_description()
        if self._overlays or self._rasters:
            self.update()

    def valid_time(self) -> datetime | None:
        return self._valid_time

    def set_forecast_reference(self, run, fxx) -> None:
        """Pin the run/hour this map's time header names (T21.1).

        The map never fetches from this: controllers own the network. It only
        records what the owning tab chose, so the painted header and the
        accessible description can name run and lead beside requested/actual.
        Passing ``None`` releases the pin back to following a valid moment.
        """
        try:
            hour = None if fxx is None else int(fxx)
        except (TypeError, ValueError, OverflowError):
            hour = None
        if run is None or hour is None:
            if self._forecast_run is None and self._forecast_fxx is None:
                return
            self._forecast_run = None
            self._forecast_fxx = None
        else:
            try:
                moment = run if run.tzinfo is not None else run.replace(
                    tzinfo=timezone.utc)
            except AttributeError:
                moment = None
            if moment is None:
                return
            if self._forecast_run == moment and self._forecast_fxx == hour:
                return
            self._forecast_run = moment
            self._forecast_fxx = hour
        self._refresh_map_accessible_description()
        self.update()

    def forecast_reference(self) -> tuple | None:
        """Return the pinned ``(run, fxx)``, or ``None`` when following (T21.1)."""
        if self._forecast_run is None or self._forecast_fxx is None:
            return None
        return (self._forecast_run, int(self._forecast_fxx))

    def set_map_mode(self, mode) -> None:
        """Enter ``"live"`` or ``"history"`` mode (T21.4).

        Returning to live releases any shelved newer frames onto the map, so
        the view the mode held aside is the one that appears -- labelled with
        its own time, never silently.
        """
        from sharpmod.maps.map_time import normalise_mode

        wanted = normalise_mode(mode)
        if wanted == getattr(self, "_map_mode", "live"):
            return
        self._map_mode = wanted
        held = getattr(self, "_held_frames", None)
        if not isinstance(held, dict):
            held = {}
            self._held_frames = held
        if wanted == "history":
            # Radar is a latest-only service in this application.  Evacuate an
            # already drawn frame as well as blocking later refreshes, otherwise
            # merely changing the mode label would leave current conditions
            # painted underneath a historical timestamp.
            for key in LIVE_ONLY_RASTER_KEYS:
                layer = self._rasters.pop(key, None)
                if layer is not None:
                    held[key] = layer
                    self._raster_pixmaps.pop(key, None)
        else:
            if held:
                for key, layer in list(held.items()):
                    try:
                        self._rasters[key] = layer
                    except (AttributeError, RuntimeError):
                        pass
                held.clear()
        self._refresh_map_accessible_description()
        self.update()

    def held_frames(self) -> dict:
        """Return shelved newer rasters held aside in history mode (T21.4)."""
        held = getattr(self, "_held_frames", None)
        return dict(held) if isinstance(held, dict) else {}

    def map_mode(self) -> str:
        """Return ``"live"`` or ``"history"`` (T21.4)."""
        from sharpmod.maps.map_time import normalise_mode

        return normalise_mode(getattr(self, "_map_mode", "live"))

    def time_header_text(self) -> str:
        """Return the compact persistent time header line (T21.1).

        Requested valid time, actual displayed time only when it differs, run
        and lead when pinned, and the live observation time for live layers --
        the same line the paint path draws, so tests, exports, and assistive
        text agree without re-deriving it.
        """
        from sharpmod.maps.map_time import resolve_displayed, time_header

        requested = getattr(self, "_valid_time", None)
        rasters = self._visible_rasters()
        displayed = None
        for key, raster in rasters:
            if key != "hrrr_field":
                continue
            displayed = getattr(raster, "valid_time", None)
            break
        if displayed is None:
            for layer in self._visible_overlays():
                candidate = getattr(layer, "valid_from", None)
                if candidate is not None:
                    displayed = None
                    break
        def _utc_key(moment):
            if moment is None:
                return None
            try:
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                return moment.astimezone(timezone.utc)
            except (AttributeError, OverflowError, ValueError):
                return None

        # The key used by the environmental-context controller is
        # ``goes_context``.  Keep radar aliases beside it because either scope
        # may be the live observation layer on a station map.
        observation_rows = [
            (key, raster, getattr(raster, "valid_time", None))
            for key, raster in rasters
            if key in ("radar_mosaic", "radar_site", "goes_context")
        ]
        vector_observations = [
            stamp
            for layer in self._visible_overlays()
            for stamp in (
                _utc_key(value)
                for value in getattr(layer, "actual_times", ())
            )
            if stamp is not None
        ]
        observation_candidates = [
            stamp for stamp in (_utc_key(row[2]) for row in observation_rows)
            if stamp is not None
        ] + vector_observations
        observation = max(observation_candidates, default=None)
        retrieval_candidates = [stamp for stamp in (
                _utc_key(getattr(raster, "retrieved_at", None))
                for _key, raster, _actual in observation_rows
            ) if stamp is not None]
        retrieval_candidates.extend(
            stamp
            for stamp in (
                _utc_key(getattr(layer, "issued", None))
                for layer in self._visible_overlays()
                if getattr(layer, "actual_times", ())
            )
            if stamp is not None
        )
        retrieved_at = max(retrieval_candidates, default=None)
        run = getattr(self, "_forecast_run", None)
        fxx = getattr(self, "_forecast_fxx", None)
        shown = resolve_displayed(requested=requested, actual=displayed)
        if displayed is None:
            displayed = shown
        # A compact explicit mixed-age warning survives screenshots.  Per-layer
        # offsets remain in the legend/list; this line merely says the reader
        # must consult them rather than assuming one shared timestamp.
        actual_moments = {_utc_key(requested)} if requested is not None else set()
        actual_moments.update(
            key for key in (
                _utc_key(getattr(raster, "valid_time", None))
                for _layer_key, raster in rasters
            ) if key is not None
        )
        actual_moments.update(vector_observations)
        unknown_live_time = any(
            key in LIVE_ONLY_RASTER_KEYS
            and getattr(raster, "valid_time", None) is None
            for key, raster in rasters
        )
        mixed = len(actual_moments) > 1 or (
            unknown_live_time and bool(requested) and len(rasters) > 1
        )
        return time_header(requested=requested, displayed=displayed, run=run,
                           forecast_hour=fxx, observation=observation,
                           retrieved_at=retrieved_at, mixed=mixed)

    def time_match_states(self) -> dict[str, tuple[str, str]]:
        """Return ``{key: (state, detail)}`` per visible layer (T21.2).

        ``exact`` / ``nearest ±offset`` / ``outside coverage`` /
        ``unavailable``, using each product's own match rule from
        :mod:`sharpmod.maps.map_time`. Read from the same attached payloads the
        paint path draws, so the list and the legend cannot disagree.
        """
        from sharpmod.maps.map_time import SURFACE_STALE_OFFSET, layer_time_match

        requested = getattr(self, "_valid_time", None)

        def _utc_key(moment):
            if moment is None:
                return None
            try:
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                return moment.astimezone(timezone.utc)
            except (AttributeError, OverflowError, ValueError):
                return None

        matches: dict[str, tuple[str, str]] = {}
        for key, raster in self._visible_rasters():
            try:
                actual = getattr(raster, "valid_time", None)
                if key in LIVE_ONLY_RASTER_KEYS and actual is None:
                    retrieved = getattr(raster, "retrieved_at", None)
                    suffix = ""
                    if retrieved is not None:
                        suffix = f" · retrieved {retrieved:%d %b %H:%M}Z"
                    state = "unavailable"
                    detail = (
                        "Unavailable · source observation time not provided"
                        f"{suffix}"
                    )
                else:
                    state, detail = layer_time_match(
                        layer_key=key, requested=requested,
                        actual=actual, available=raster is not None,
                        loading=False,
                        update_interval_s=getattr(
                            raster, "update_interval_s", None))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                state, detail = "unavailable", "Unavailable"
            matches[key] = (state, detail)
        for layer in self._visible_overlays():
            key = str(getattr(layer, "key", "") or
                      getattr(layer, "title", "") or "overlay")
            try:
                actual_times = tuple(
                    moment for moment in (
                        _utc_key(value)
                        for value in getattr(layer, "actual_times", ())
                    ) if moment is not None
                )
                if actual_times and requested is not None:
                    requested_utc = _utc_key(requested)
                    nearest = min(
                        actual_times,
                        key=lambda moment: abs(
                            (moment - requested_utc).total_seconds()),
                    )
                    state, detail = layer_time_match(
                        layer_key=key, requested=requested_utc, actual=nearest,
                        available=bool(layer), loading=False)
                    offsets = sorted(
                        (moment - requested_utc).total_seconds()
                        for moment in actual_times
                    )
                    if offsets and (len(set(offsets)) > 1 or abs(offsets[0]) >= 60):
                        from sharpmod.maps.map_time import format_offset

                        state = "nearest" if state == "exact" else state
                        edge = format_offset(offsets[0])
                        if offsets[-1] != offsets[0]:
                            edge += f" to {format_offset(offsets[-1])}"
                        detail = f"{state} · displayed offsets {edge}"
                        if key in ("surface", "surface_observations") \
                                and max(abs(value) for value in offsets) \
                                > SURFACE_STALE_OFFSET.total_seconds():
                            detail += " · stale beyond 30 min"
                else:
                    state, detail = layer_time_match(
                        layer_key=key, requested=requested,
                        valid_from=getattr(layer, "valid_from", None),
                        valid_to=getattr(layer, "valid_to", None),
                        available=bool(layer),
                        loading=False)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                state, detail = "unavailable", "Unavailable"
            matches[key] = (state, detail)
        if self.map_mode() == "history":
            held = getattr(self, "_held_frames", None)
            if isinstance(held, dict):
                for key in LIVE_ONLY_RASTER_KEYS:
                    if key not in held or not self._overlay_visible.get(key, True):
                        continue
                    matches[key] = (
                        "unavailable",
                        "Unavailable · live-only layer hidden in historical view",
                    )
        return matches

    def time_state(self) -> dict:
        """Return the portable time-choice snapshot (T21/session)."""
        from sharpmod.maps.map_time import time_state

        return time_state(run=getattr(self, "_forecast_run", None),
                          forecast_hour=getattr(self, "_forecast_fxx", None),
                          requested=getattr(self, "_valid_time", None),
                          mode=self.map_mode())

    def restore_time_state(self, payload) -> None:
        """Restore run/hour/mode choices, ignoring unknown values (T21)."""
        from sharpmod.maps.map_time import restore_time_state

        state = restore_time_state(payload)
        run = state.get("run") or ""
        try:
            moment = datetime.fromisoformat(str(run)) if run else None
        except ValueError:
            moment = None
        try:
            hour = None if state.get("forecast_hour") is None else int(
                state.get("forecast_hour"))
        except (TypeError, ValueError, OverflowError):
            hour = None
        self._forecast_run = moment
        self._forecast_fxx = hour
        try:
            requested = state.get("requested") or ""
            when = datetime.fromisoformat(str(requested)) if requested else None
        except ValueError:
            when = None
        self.set_valid_time(when)
        # Use the public transition so restoring History evacuates live-only
        # rasters and restoring Live releases any shelved frame.  Assigning the
        # string alone changed the label but not what the map actually drew.
        self.set_map_mode(state.get("mode", "live"))
        self._refresh_map_accessible_description()
        self.update()

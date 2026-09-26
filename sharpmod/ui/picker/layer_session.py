"""Picker map-layer session save and restore."""

from __future__ import annotations

from sharpmod.ui.picker.cycles import _select_cycle


class PickerLayerSessionMixin:
    """Persist layer choices, panel state, and valid times."""

    def session_layer_state(self) -> dict:
        """Return the portable layer-choice snapshot (T19.5/session, T24).

        Context writes per-half slots (``satellite``/``surface``) beside the
        legacy whole-controller ``context`` slot, so a restore can tell
        "satellite on, surface off" apart from "everything on". Named
        ``session_layer_state`` (not ``session_map_state``) on purpose: the
        viewer-session hook in ``gui_sessions`` calls hooks by that map-extent
        name, and layer choices must not be mistaken for extents. Field
        panels add their arrangement (count/products/preset/category memory)
        beside the layers; fetched frames never persist (T24.5).
        """
        from sharpmod.maps.map_layers import layer_session_state

        enabled, products, opacities = {}, {}, {}
        for tab in ("map", "model", "panels"):
            _map, outlook, reports, radar, field, context = \
                self._layer_controllers(tab)
            for key, controller in (
                    ("spc_outlook", outlook), ("storm_reports", reports),
                    ("radar", radar), ("hrrr_field", field)):
                if controller is None:
                    continue
                try:
                    on = bool(controller.is_enabled())
                except (AttributeError, RuntimeError):
                    continue
                enabled[f"{tab}/{key}"] = on
                for reader, writer in (("product", products),
                                       ("opacity", opacities)):
                    getter = getattr(controller, reader, None)
                    if callable(getter):
                        try:
                            writer[f"{tab}/{key}"] = getter()
                        except (AttributeError, RuntimeError, TypeError):
                            pass
            if context is not None:
                try:
                    sat_on = bool(context.is_satellite_enabled())
                    surface_on = bool(context.is_surface_enabled())
                except (AttributeError, RuntimeError):
                    continue
                enabled[f"{tab}/satellite"] = sat_on
                enabled[f"{tab}/surface"] = surface_on
                enabled[f"{tab}/context"] = sat_on or surface_on
                try:
                    products[f"{tab}/satellite"] = context.channel()
                    products[f"{tab}/surface"] = context.density()
                except (AttributeError, RuntimeError, TypeError):
                    pass
                try:
                    opacities[f"{tab}/satellite"] = context.opacity()
                    opacities[f"{tab}/context"] = context.opacity()
                except (AttributeError, RuntimeError, TypeError):
                    pass
        try:
            preset = str(self._settings.value("overlays/layer_preset", "") or "")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            preset = ""
        # T21: run/hour/mode choices ride beside the layers so a session
        # restores *when* as well as *what* -- requested time, pinned cycle,
        # and live/history per tab. Frames never persist; a restore
        # re-requests them.
        times = {}
        for tab, attribute in (("map", "_map"), ("model", "_model_map")):
            widget = getattr(self, attribute, None)
            if widget is None:
                continue
            try:
                times[tab] = widget.time_state()
            except (AttributeError, RuntimeError):
                pass
        view = getattr(self, "_panels_view", None)
        if view is not None:
            try:
                times["panels"] = view.time_state()
            except (AttributeError, RuntimeError):
                pass
        panels_state = None
        if view is not None:
            try:
                from sharpmod.ui.maps.panels import panel_state

                panels_state = panel_state(
                    count=view.panel_count(),
                    products=view.panel_products(),
                    preset=str(getattr(
                        self, "_panels_preset_combo", None).currentData()
                        if getattr(self, "_panels_preset_combo", None)
                        is not None else "") or "",
                    category_memory=view.category_memory())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                panels_state = None
        state = layer_session_state(enabled=enabled, products=products,
                                    opacities=opacities,
                                    preset=preset or None, times=times or None)
        if panels_state is not None:
            state["panels"] = panels_state
        return state

    def restore_session_layer_state(self, payload) -> None:
        """Restore layer and field-panel choices without replaying the network.

        Context is two layers sharing one controller, so its enabled/product
        writes fan out to the satellite/surface halves explicitly rather than
        through the single-switch path the other controllers use. Time choices
        (run/hour/mode per tab) restore through the same path: modes land
        first so a historical pin is in force before the frames re-request,
        then run/hour controls move (which refetches), then the maps record
        the pin for their headers. Field panels restore their arrangement
        first so the time/choice restores below land on the right slots.
        """
        from sharpmod.maps.map_layers import (
            normalise_opacity,
            restore_layer_session_state,
        )

        state = restore_layer_session_state(payload)
        try:
            panels_payload = payload.get("panels") if isinstance(
                payload, dict) else None
        except AttributeError:
            panels_payload = None
        if isinstance(panels_payload, dict):
            try:
                self._restore_session_panels(panels_payload)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        # T21: modes first (historical pins hold frames), then times below.
        try:
            times = state.get("times") or {}
        except AttributeError:
            times = {}
        if isinstance(times, dict):
            for tab in ("model", "panels"):
                slot = times.get(tab)
                if not isinstance(slot, dict):
                    continue
                try:
                    self._apply_map_mode(tab, slot.get("mode", "live"))
                except (AttributeError, RuntimeError):
                    pass
        for tab in ("map", "model", "panels"):
            _map, outlook, reports, radar, field, context = \
                self._layer_controllers(tab)
            for key, controller in (
                    ("spc_outlook", outlook), ("storm_reports", reports),
                    ("radar", radar), ("hrrr_field", field),
                    ("context", context)):
                if controller is None:
                    continue
                slot = f"{tab}/{key}"
                if key == "context":
                    self._restore_session_context(controller, state,
                                                  prefix=f"{tab}/")
                    continue
                if slot in state["enabled"]:
                    try:
                        controller.set_enabled(bool(state["enabled"][slot]))
                    except (AttributeError, RuntimeError):
                        pass
                if slot in state["products"]:
                    setter = getattr(controller, "set_product", None)
                    if callable(setter):
                        try:
                            setter(str(state["products"][slot]))
                        except (AttributeError, RuntimeError):
                            pass
                if slot in state["opacities"]:
                    setter = getattr(controller, "set_opacity_value", None)
                    if callable(setter):
                        try:
                            setter(normalise_opacity(state["opacities"][slot]))
                        except (AttributeError, RuntimeError):
                            pass
        if state.get("preset"):
            try:
                self._settings.setValue("overlays/layer_preset", state["preset"])
                self._settings.sync()
            except Exception:  # noqa: BLE001 - preference is advisory
                pass
        # T21: run/hour/mode choices per tab, restored after the layers so
        # the controls they move refetch exactly once.
        if isinstance(times, dict):
            try:
                self._restore_session_times(times)
            except (AttributeError, RuntimeError):
                pass
        self._refresh_all_layer_lists()
        for tab in ("map", "model", "panels"):
            try:
                self._refresh_map_time_header(tab)
            except (AttributeError, RuntimeError):
                pass

    def _restore_session_panels(self, payload) -> None:
        """Restore the field-panel arrangement without replaying frames (T24).

        Count, products, preset, and per-category memory restore; the
        maximised slot never persists (T24.3 restores the full grid so a
        hidden comparison cannot reopen half-hidden). Frames re-request
        through the existing time restore below.
        """
        from sharpmod.ui.maps.panels import restore_panel_state

        state = restore_panel_state(payload)
        view = getattr(self, "_panels_view", None)
        if view is not None:
            try:
                view.set_panel_count(int(state.get("count", 2)))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            try:
                view.set_panel_products(tuple(state.get("products") or ()))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            try:
                view.set_category_memory(state.get("category_memory"))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            try:
                view.restore_maximised()
            except (AttributeError, RuntimeError):
                pass
            try:
                self._rebuild_panels_tool_rows()
            except (AttributeError, RuntimeError):
                pass
        preset = str(state.get("preset") or "")
        if preset:
            try:
                self._settings.setValue("panels/preset", preset)
                self._settings.sync()
            except Exception:  # noqa: BLE001 - preference is advisory
                pass
            combo = getattr(self, "_panels_preset_combo", None)
            if combo is not None:
                try:
                    index = combo.findData(preset)
                    if index >= 0:
                        combo.blockSignals(True)
                        try:
                            combo.setCurrentIndex(index)
                        finally:
                            combo.blockSignals(False)
                except (AttributeError, RuntimeError):
                    pass

    def _restore_session_times(self, times: dict) -> None:
        """Restore run/hour/mode choices without replaying frames (T21).

        Observed tab first (a moment, no cycle), then the two forecast tabs
        (run + hour, which refetch on the move). Maps record the pin for
        their headers after the controls land, so the first paint already
        names the restored run/hour.
        """
        from datetime import datetime as _datetime

        slot = times.get("map")
        if isinstance(slot, dict):
            requested = slot.get("requested") or ""
            try:
                when = _datetime.fromisoformat(str(requested)) \
                    if requested else None
            except ValueError:
                when = None
            if when is not None:
                try:
                    stamp = when.date()
                    from qtpy.QtCore import QDate as _QDate

                    self._map_date.setDate(_QDate(stamp.year, stamp.month,
                                                  stamp.day))
                    _select_cycle(self._map_cycle, when.hour)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
        for tab, date_attr, cycle_attr, fxx_attr in (
                ("model", "_model_date", "_model_cycle", "_model_fxx_combo"),
                ("panels", "_panels_date", "_panels_cycle",
                 "_panels_fxx_combo")):
            slot = times.get(tab)
            if not isinstance(slot, dict):
                continue
            run = slot.get("run") or ""
            try:
                moment = _datetime.fromisoformat(str(run)) if run else None
            except ValueError:
                moment = None
            hour = slot.get("forecast_hour")
            try:
                hour = None if hour is None else int(hour)
            except (TypeError, ValueError, OverflowError):
                hour = None
            if moment is None or hour is None:
                continue
            try:
                from qtpy.QtCore import QDate as _QDate

                date_widget = getattr(self, date_attr, None)
                date_widget.setDate(_QDate(moment.year, moment.month,
                                           moment.day))
                _select_cycle(getattr(self, cycle_attr, None), moment.hour)
                fxx_widget = getattr(self, fxx_attr, None)
                index = fxx_widget.findData(int(hour))
                if index >= 0:
                    fxx_widget.setCurrentIndex(index)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        for tab, attribute in (("map", "_map"), ("model", "_model_map")):
            slot = times.get(tab)
            widget = getattr(self, attribute, None)
            if not isinstance(slot, dict) or widget is None:
                continue
            try:
                widget.restore_time_state(slot)
            except (AttributeError, RuntimeError):
                pass
        view = getattr(self, "_panels_view", None)
        slot = times.get("panels")
        if isinstance(slot, dict) and view is not None:
            try:
                view.restore_time_state(slot)
            except (AttributeError, RuntimeError):
                pass

    @staticmethod
    def _restore_session_context(controller, state, *, prefix: str) -> None:
        """Restore one context controller's satellite/surface halves (T19.5)."""
        from sharpmod.maps.map_layers import normalise_opacity

        sat_check = getattr(controller, "_sat_check", None)
        surface_check = getattr(controller, "_surface_check", None)
        enabled = state.get("enabled") or {}
        products = state.get("products") or {}
        opacities = state.get("opacities") or {}
        # Legacy payloads (and the current writer) store one "context" slot;
        # per-half slots win when present so a satellite-only restore cannot
        # drag surface observations along with it.
        sat_on = enabled.get(f"{prefix}satellite",
                             enabled.get(f"{prefix}context"))
        surface_on = enabled.get(f"{prefix}surface",
                                 enabled.get(f"{prefix}context"))
        # A stored single bool is the old whole-controller shape: apply it to
        # both halves rather than dropping the restore.
        if isinstance(sat_on, bool) and sat_check is not None:
            try:
                sat_check.setChecked(sat_on)
            except (AttributeError, RuntimeError):
                pass
        if isinstance(surface_on, bool) and surface_check is not None:
            try:
                surface_check.setChecked(surface_on)
            except (AttributeError, RuntimeError):
                pass
        channel = products.get(f"{prefix}satellite",
                               products.get(f"{prefix}context"))
        if channel is not None:
            setter = getattr(controller, "set_channel", None)
            if callable(setter):
                try:
                    setter(str(channel))
                except (AttributeError, RuntimeError):
                    pass
        density = products.get(f"{prefix}surface")
        if density is not None:
            try:
                index = controller._density.findData(str(density))
            except (AttributeError, RuntimeError):
                index = -1
            if index is not None and index >= 0:
                try:
                    controller._density.setCurrentIndex(index)
                except (AttributeError, RuntimeError):
                    pass
        opacity = opacities.get(f"{prefix}satellite",
                                opacities.get(f"{prefix}context"))
        if opacity is not None:
            setter = getattr(controller, "set_opacity_value", None)
            if callable(setter):
                try:
                    setter(normalise_opacity(opacity))
                except (AttributeError, RuntimeError):
                    pass

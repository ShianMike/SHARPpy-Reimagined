"""Window-owned reference and display visibility, keyed by actual profile IDs."""

import weakref
from dataclasses import dataclass
from datetime import datetime, timezone

from qtpy.QtCore import QObject, Signal

from sharpmod.state.collection_identity import describe_identity


CLOSED_LIMIT = 8


@dataclass(frozen=True)
class ClosedProfile:
    profile_id: str
    collection: object
    index: int
    current_date: datetime | None
    identity: object
    closed_at: datetime
    hidden: bool
    reference: bool
    focused: bool


class CollectionState(QObject):
    changed = Signal()
    feedback = Signal(str, str)

    def __init__(self, window):
        super().__init__(window)
        self._window_ref = weakref.ref(window)
        self.reference_id = None
        self.hidden_ids = set()
        self.closed = ()
        self._closed_dialog = None
        widget = self.widget()
        focus = getattr(widget, "setProfileCollection", None)
        self._focus_original = weakref.WeakMethod(focus) if focus is not None else None
        if focus is not None:
            widget.setProfileCollection = self.focus
        remove = getattr(window, "rmProfileCollection", None)
        self._remove_original = weakref.WeakMethod(remove) if remove is not None else None
        if remove is not None:
            window.rmProfileCollection = self.remove
        # Upstream's mapper captured the old bound slot before this installer.
        mapper = getattr(window, "focus_mapper", None)
        if mapper is not None:
            mapper.mapped[str].disconnect()
            mapper.mapped[str].connect(self.focus)
        mapper = getattr(window, "remove_mapper", None)
        if mapper is not None:
            mapper.mapped[str].disconnect()
            mapper.mapped[str].connect(self.remove)
        for name in ("sound", "hodo"):
            canvas = getattr(widget, name, None)
            if canvas is not None and hasattr(canvas, "plotData"):
                self._install_paint_filter(canvas)
        self._install_closed_action(window)

    def window(self):
        return self._window_ref()

    def widget(self):
        return getattr(self.window(), "spc_widget", None)

    def ids(self):
        return tuple(getattr(self.widget(), "prof_ids", ()) or ())

    def focused_id(self):
        widget, ids = self.widget(), self.ids()
        try:
            return ids[int(widget.pc_idx)]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def pin(self, profile_id):
        if profile_id is not None and profile_id not in self.ids():
            self.feedback.emit("The reference is no longer loaded. Choose another profile.", "warning")
            return False
        if self.reference_id != profile_id:
            self.reference_id = profile_id
            self.feedback.emit("", "normal")
            self.changed.emit()
        return True

    def prune(self):
        ids = set(self.ids())
        self.hidden_ids.intersection_update(ids)
        if self.reference_id is not None and self.reference_id not in ids:
            self.reference_id = None
            self.feedback.emit("The pinned reference was removed. Choose another reference.", "warning")

    def focus(self, profile_id):
        if profile_id not in self.ids():
            return False
        if profile_id in self.hidden_ids:
            self.feedback.emit("This profile is hidden. Show it before bringing it into focus.", "warning")
            return False
        original = self._focus_original() if self._focus_original is not None else None
        if original is None:
            return False
        original(profile_id)
        return self.focused_id() == profile_id

    def remove(self, profile_id):
        ids = self.ids()
        if profile_id not in ids or len(ids) <= 1:
            return False
        if profile_id not in self.hidden_ids and len(set(ids) - self.hidden_ids) <= 1:
            self.feedback.emit("Show another profile before removing the last visible profile.", "warning")
            return False
        original = self._remove_original() if self._remove_original is not None else None
        if original is None:
            return False
        index = ids.index(profile_id)
        collection = self.widget().prof_collections[index]
        try:
            current_date = collection.getCurrentDate()
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            current_date = None
        record = ClosedProfile(profile_id, collection, index, current_date,
                               describe_identity(profile_id, collection, index), datetime.now(timezone.utc),
                               profile_id in self.hidden_ids, profile_id == self.reference_id,
                               profile_id == self.focused_id())
        original(profile_id)
        removed = profile_id not in self.ids()
        if removed:
            self.closed = (record, *(entry for entry in self.closed if entry.profile_id != profile_id))[:CLOSED_LIMIT]
            self._remove_hidden_menus(profile_id)
            self._closed_action.setEnabled(True)
        self.prune()
        self.changed.emit()
        return removed

    def _remove_hidden_menus(self, profile_id):
        window = self.window()
        parent = getattr(window, "profilemenu", None)
        menus = getattr(window, "menu_items", None)
        if parent is None or menus is None:
            return
        for menu in tuple(menus):
            if menu.title() == profile_id and not menu.menuAction().isVisible():
                parent.removeAction(menu.menuAction())
                menus.remove(menu)
                menu.deleteLater()

    def reopen(self, profile_id):
        record = next((entry for entry in self.closed if entry.profile_id == profile_id), None)
        if record is None:
            self.feedback.emit("This closed profile is no longer retained. Reopen its source; retrieval may be required.", "warning")
            return False
        if profile_id in self.ids():
            self.feedback.emit("This profile identity is already loaded. It was not overwritten; close it before restoring this retained profile.", "warning")
            return False
        add = getattr(self.window(), "addProfileCollection", None)
        if add is None:
            self.feedback.emit("This window cannot restore retained data. Reopen the source; retrieval may be required.", "warning")
            return False
        widget = self.widget()
        before_ids = list(widget.prof_ids)
        before_collections = list(widget.prof_collections)
        before_active = int(widget.pc_idx)
        before_hidden, before_reference = set(self.hidden_ids), self.reference_id
        before_menus = tuple(getattr(self.window(), "menu_items", ()) or ())
        before_canvas = {}
        for name in ("sound", "hodo"):
            canvas = getattr(widget, name, None)
            if canvas is not None:
                before_canvas[name] = (list(canvas.prof_collections), int(canvas.pc_idx))
        try:
            add(record.collection, focus=record.focused and not record.hidden, check_integrity=False)
            found = next((index for index, collection in enumerate(widget.prof_collections)
                          if collection is record.collection), None)
            if found is None:
                raise ValueError("The existing reader did not restore the retained collection")
            actual_id = widget.prof_ids[found]
            target = min(record.index, len(widget.prof_ids) - 1)
            focused = widget.prof_collections[int(widget.pc_idx)]
            pairs = list(zip(widget.prof_ids, widget.prof_collections))
            pairs.insert(target, pairs.pop(found))
            widget.prof_ids[:] = [key for key, _collection in pairs]
            widget.prof_collections[:] = [collection for _key, collection in pairs]
            widget.pc_idx = next(index for index, collection in enumerate(widget.prof_collections) if collection is focused)
            for name in ("sound", "hodo"):
                canvas = getattr(widget, name, None)
                if canvas is not None:
                    canvas.prof_collections[:] = widget.prof_collections
                    canvas.pc_idx = widget.pc_idx
            self._restore_profile_menu_position(actual_id, target)
            if isinstance(record.current_date, datetime):
                record.collection.setCurrentDate(record.current_date)
            if record.hidden:
                self.hidden_ids.add(actual_id)
            if record.reference and self.reference_id is None:
                self.reference_id = actual_id
            self.ensure_visible_focus()
            widget.updateProfs()
        except Exception as exc:  # noqa: BLE001 - native reopen boundary
            from sharpmod.ui.features.gui_common import _LOGGER

            widget.prof_ids[:] = before_ids
            widget.prof_collections[:] = before_collections
            widget.pc_idx = before_active
            self.hidden_ids, self.reference_id = before_hidden, before_reference
            for name, (collections, active) in before_canvas.items():
                canvas = getattr(widget, name, None)
                if canvas is not None:
                    canvas.prof_collections[:] = collections
                    canvas.pc_idx = active
            self._remove_new_profile_menus(before_menus)
            try:
                widget.updateProfs()
            except Exception:  # noqa: BLE001 - preserve original reopen error
                _LOGGER.exception("closed_profile.rollback_refresh_failed id=%s", profile_id)
            _LOGGER.exception("closed_profile.reopen_failed id=%s", profile_id)
            self.feedback.emit(f"Could not restore retained profile: {exc}. Its closed entry was kept.", "error")
            return False
        self.closed = tuple(entry for entry in self.closed if entry is not record)
        self._closed_action.setEnabled(bool(self.closed))
        self.feedback.emit("Reopened retained data without retrieval." + (" The profile is still hidden; use Show selected." if record.hidden else ""), "normal")
        self.changed.emit()
        return True

    def _remove_new_profile_menus(self, before_menus):
        window = self.window()
        parent, menus = getattr(window, "profilemenu", None), getattr(window, "menu_items", None)
        if parent is None or menus is None:
            return
        for menu in tuple(menus):
            if menu not in before_menus:
                parent.removeAction(menu.menuAction())
                menus.remove(menu)
                menu.deleteLater()
        multiple = len(self.ids()) > 1
        for menu in menus:
            for action in menu.actions():
                if action.text().replace("&", "") == "Remove":
                    action.setVisible(multiple)

    def _restore_profile_menu_position(self, profile_id, target):
        window = self.window()
        parent, menus = getattr(window, "profilemenu", None), getattr(window, "menu_items", None)
        if parent is None or menus is None:
            return
        restored = next((menu for menu in menus if menu.title() == profile_id and menu.menuAction().isVisible()), None)
        if restored is None:
            return
        next_id = self.ids()[target + 1] if target + 1 < len(self.ids()) else None
        following = next((menu for menu in menus if menu.title() == next_id and menu.menuAction().isVisible()), None)
        parent.removeAction(restored.menuAction())
        if following is None:
            parent.addMenu(restored)
        else:
            parent.insertMenu(following.menuAction(), restored)
        menus.remove(restored)
        insert_at = menus.index(following) if following in menus else len(menus)
        menus.insert(insert_at, restored)

    def _install_closed_action(self, window):
        from qtpy.QtGui import QAction, QKeySequence

        menu = next((action.menu() for action in window.menuBar().actions()
                     if action.menu() is not None and action.text().replace("&", "").casefold() == "file"), None)
        if menu is None:
            menu = window.menuBar().addMenu("File")
        action = QAction("Reopen closed sounding…", window)
        from sharpmod.ui.features.gui_commands import collect_commands

        shortcut = QKeySequence("Ctrl+Shift+T")
        if not any(shortcut == key for command in collect_commands(window) for key in command.action.shortcuts()):
            action.setShortcut(shortcut)
        action.setToolTip("Restore one of the last eight closed profiles retained in this window; no retrieval is needed")
        action.setEnabled(False)
        action.triggered.connect(self.show_closed)
        menu.addAction(action)
        self._closed_action = action

    def show_closed(self):
        from sharpmod.ui.features.gui_closed_profiles import ClosedProfilesDialog

        window = self.window()
        if window is None:
            return
        if self._closed_dialog is not None:
            try:
                self._closed_dialog.deleteLater()
            except RuntimeError:
                pass
        self._closed_dialog = ClosedProfilesDialog(window, self)
        self._closed_dialog.show()
        self._closed_dialog.raise_()
        self._closed_dialog.activateWindow()

    def ensure_visible_focus(self):
        """Protect upstream Space/time paths without changing collection order."""
        self.prune()
        focused, ids = self.focused_id(), self.ids()
        if focused not in self.hidden_ids:
            return
        visible = next((profile_id for profile_id in ids if profile_id not in self.hidden_ids), None)
        if visible is not None:
            self.widget().pc_idx = ids.index(visible)

    def set_visible(self, profile_id, visible):
        self.prune()
        ids = self.ids()
        if profile_id not in ids:
            self.feedback.emit("This profile is no longer loaded.", "warning")
            return False
        if not visible and profile_id not in self.hidden_ids and len(set(ids) - self.hidden_ids) <= 1:
            self.feedback.emit("Keep at least one profile visible; hiding is separate from removal.", "warning")
            return False
        if visible:
            self.hidden_ids.discard(profile_id)
        else:
            self.hidden_ids.add(profile_id)
        self.ensure_visible_focus()
        self.feedback.emit("", "normal")
        widget = self.widget()
        update = getattr(widget, "updateProfs", None)
        if update is not None:
            update()
        self.changed.emit()
        return True

    def _install_paint_filter(self, canvas):
        if getattr(canvas, "_sharpmod_visibility_wrapped", False):
            return
        state_ref, canvas_ref = weakref.ref(self), weakref.ref(canvas)
        original = canvas.plotData
        if getattr(original, "__self__", None) is not None:
            original_ref = weakref.WeakMethod(original)
        else:
            # Renderer patches can install a plain instance callback. Keep it
            # on its existing canvas, never in a window-capturing closure.
            canvas._sharpmod_visibility_original_plot = original
            original_ref = lambda: getattr(canvas_ref(), "_sharpmod_visibility_original_plot", None)

        def plot_data():
            state, live, original = state_ref(), canvas_ref(), original_ref()
            if original is None or live is None:
                return None
            if state is None or not state.hidden_ids:
                return original()
            widget = state.widget()
            hidden = {id(collection) for profile_id, collection in zip(widget.prof_ids, widget.prof_collections)
                      if profile_id in state.hidden_ids}
            collections, active = live.prof_collections, live.pc_idx
            if not 0 <= active < len(collections):
                return original()
            focused = collections[active]
            visible = [collection for collection in collections if id(collection) not in hidden]
            if not any(collection is focused for collection in visible):
                return original()
            # These are canvas-only lists. The SPCWidget source arrays are never
            # changed. Restore even on failure, before any subsequent Qt event.
            live.prof_collections = visible
            live.pc_idx = next(index for index, collection in enumerate(visible) if collection is focused)
            try:
                return original()
            finally:
                live.prof_collections, live.pc_idx = collections, active

        canvas.plotData = plot_data
        canvas._sharpmod_visibility_wrapped = True

    def session_state(self):
        self.prune()
        return {"version": 1, "reference_id": self.reference_id,
                "hidden_ids": [profile_id for profile_id in self.ids() if profile_id in self.hidden_ids]}

    def restore_session_state(self, state):
        if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
            return False
        hidden = state.get("hidden_ids", [])
        if not isinstance(hidden, list) or any(not isinstance(value, str) for value in hidden):
            self.feedback.emit("Saved profile visibility is invalid; current visibility was kept.", "warning")
            return False
        ids = self.ids()
        self.hidden_ids = set(hidden).intersection(ids)
        reference = state.get("reference_id")
        self.reference_id = reference if isinstance(reference, str) and reference in ids else None
        if ids and len(self.hidden_ids) == len(ids):
            self.hidden_ids.discard(self.focused_id() or ids[0])
            self.feedback.emit("The saved state hid every profile; the focused one was kept visible.", "warning")
        self.ensure_visible_focus()
        update = getattr(self.widget(), "updateProfs", None)
        if update is not None:
            update()
        self.changed.emit()
        return True


def install_collection_state(window):
    state = getattr(window, "_sharpmod_collection_state", None)
    if state is None:
        state = CollectionState(window)
        window._sharpmod_collection_state = state
    return state

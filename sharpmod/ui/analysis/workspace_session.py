"""WorkspaceSession behavior for the sounding analysis workspace."""

from __future__ import annotations

from collections.abc import Mapping
from sharpmod.ui.features import gui_trend_tracks as trend_tracks
from sharpmod.ui.analysis.workspace import _LOGGER


class WorkspaceSessionMixin:
    """Focused methods shared by AnalysisWorkspace."""

    def session_state(self):
        """Return JSON-safe workspace controls."""
        dock = getattr(self, "dock", None)
        return {
            # Version 3 removes retired pages and renumbers the remaining tabs.
            # ``tab_key`` is the durable identifier; ``tab`` stays for older readers.
            "version": 3,
            "visible": bool(dock is not None and not dock.isHidden()),
            "tab": int(self.tabs.currentIndex()),
            "tab_key": self.tab_key(),
            # Every sounding is plotted now, so this is no longer a filter: it
            # records which series the reader last opened, which is what the
            # chart highlights and what the single-sounding CSV exports.
            "trend_collection": int(self._trend_collection_index),
            "trend_metric": self.trend_metric.currentData(),
            # Which parameters were stacked, and whether their ranges were fixed,
            # is part of how the reader was reading the trend.
            "trend_tracks": list(self.trend_track_keys()),
            "trend_scales": self.trend_scale_state(),
            "trend_local_time": bool(self.trend_local_time.isChecked()),
            "comparison_reference": self.compare_reference.currentData(),
            "comparison_reference_id": (self._comparison_ids[self.compare_reference.currentData()]
                                        if isinstance(self.compare_reference.currentData(), int)
                                        and 0 <= self.compare_reference.currentData() < len(self._comparison_ids)
                                        and isinstance(self._comparison_ids[self.compare_reference.currentData()], str)
                                        else None),
            "comparison_layout": self.visual_compare.layout_count(),
            "comparison_slots": self.visual_compare.slot_state(),
            # Whether the reader fixed the axes is part of how they were reading
            # the comparison, so it travels with the session.
            "comparison_scales": self.visual_compare.scale_state(),
            # Which chart the reader was on, how it was arranged, and whether the
            # slots were held on one scale is likewise part of how they were
            # reading it, so restoring the session restores the same view.
            "comparison_charts": self.visual_compare.chart_state(),
            "comparison_metrics": list(self.comparison_metric_keys()),
            "comparison_split": [int(size) for size in self.compare_split.sizes()],
            # How the reader chose to divide table against chart is part of how
            # they were reading the ensemble, so it travels with the session.
            "ensemble_split": [int(size) for size in self.ensemble_split.sizes()],
            "ensemble_mode": int(self.ensemble_modes.currentIndex()),
            "ensemble_threshold": self.threshold_explorer.session_state(),
            "animation": self.animation_workspace.session_state(),
        }

    def _restore_tab_choice(self, state):
        """Resolve remaining pages across older saved tab orders."""
        key = state.get("tab_key")
        if isinstance(key, str):
            index = self.tab_index_for(key)
            if index is not None:
                return index
        stored = state.get("tab", self.TAB_TRENDS)
        try:
            version = int(state.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        try:
            stored = int(stored)
        except (TypeError, ValueError):
            return self.TAB_TRENDS
        if version < 2:
            return {0: self.TAB_TRENDS, 1: self.TAB_COMPARE,
                    2: self.TAB_ENSEMBLE, 7: self.TAB_ANIMATION}.get(
                        stored, self.TAB_TRENDS)
        if version < 3:
            return {0: self.TAB_TRENDS, 1: self.TAB_COMPARE,
                    2: self.TAB_ENSEMBLE, 8: self.TAB_ANIMATION}.get(
                        stored, self.TAB_TRENDS)
        return stored if 0 <= stored < self.tabs.count() else self.TAB_TRENDS

    def restore_session_state(self, state):
        """Restore a prior JSON-safe state, ignoring unknown future fields."""
        if not isinstance(state, Mapping):
            return
        self._populate_controls()
        metric = state.get("trend_metric")
        metric_index = self.trend_metric.findData(metric)
        ref_index = self.compare_reference.findData(state.get("comparison_reference"))
        reference_id = state.get("comparison_reference_id")
        if reference_id is not None and reference_id in self._comparison_ids:
            ref_index = self._comparison_ids.index(reference_id)
        tab = self._restore_tab_choice(state)
        try:
            self._trend_collection_index = max(0, int(state.get("trend_collection")))
        except (TypeError, ValueError):
            self._trend_collection_index = 0
        for combo, index in (
            (self.trend_metric, metric_index),
            (self.compare_reference, ref_index),
        ):
            if index >= 0:
                blocked = combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(blocked)
        try:
            tab = max(0, min(self.tabs.count() - 1, int(tab)))
        except (TypeError, ValueError):
            tab = self.TAB_TRENDS
        blocked = self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(tab)
        self.tabs.blockSignals(blocked)
        # Signals were blocked to avoid a refresh per restored field, so bring
        # the grouped chooser onto the restored page explicitly.
        self._sync_navigator(tab)
        sizes = state.get("ensemble_split")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                self.ensemble_split.setSizes([int(size) for size in sizes])
            except (TypeError, ValueError):
                # A hand-edited or future session file must not stop a restore.
                _LOGGER.debug("analysis_workspace.split_restore_skipped")
        try:
            ensemble_mode = max(
                0,
                min(
                    self.ensemble_modes.count() - 1,
                    int(state.get("ensemble_mode", 0)),
                ),
            )
        except (TypeError, ValueError):
            ensemble_mode = 0
        self.ensemble_modes.setCurrentIndex(ensemble_mode)
        self.threshold_explorer.restore_session_state(
            state.get("ensemble_threshold")
        )
        self.animation_workspace.restore_session_state(state.get("animation"))
        compare_sizes = state.get("comparison_split")
        if isinstance(compare_sizes, (list, tuple)) and len(compare_sizes) == 2:
            try:
                self.compare_split.setSizes([int(size) for size in compare_sizes])
            except (TypeError, ValueError):
                _LOGGER.debug("analysis_workspace.compare_split_restore_skipped")
        # Before the slot/scale restore, so one comparison recompute covers the
        # restored columns as well. A session without the key keeps the durable
        # preference rather than silently reverting to the built-in default.
        columns = state.get("comparison_metrics")
        if isinstance(columns, (list, tuple)) and columns:
            self.set_comparison_metric_keys(columns, persist=False)
        # A session without the key keeps the durable preference rather than
        # silently reverting the reader's tracks to the built-in default.
        tracks = state.get("trend_tracks")
        if isinstance(tracks, (list, tuple)) and tracks:
            self._trend_track_keys = trend_tracks.normalize_keys(tracks)
        self.restore_trend_scale_state(state.get("trend_scales") or {})
        if "trend_local_time" in state:
            wanted = bool(state.get("trend_local_time"))
            blocked = self.trend_local_time.blockSignals(True)
            self.trend_local_time.setChecked(wanted)
            self.trend_local_time.blockSignals(blocked)
            self.trend_chart.set_show_local_time(wanted)
        self.visual_compare.restore_slot_state(state.get("comparison_slots"))
        try:
            self.visual_compare.set_layout_count(int(state.get("comparison_layout", 2)))
        except (TypeError, ValueError):
            self.visual_compare.set_layout_count(2)
        # After the layout, so a restored lock applies to the slots actually
        # shown. An empty mapping rather than None, so a session predating the
        # lock restores the documented automatic default instead of inheriting
        # whatever the workspace happened to be set to.
        self.visual_compare.restore_scale_state(
            state.get("comparison_scales") or {}
        )
        # An older session has no chart key; leaving the workspace on its default
        # difference view is the honest fallback, not guessing a chart.
        self.visual_compare.restore_chart_state(
            state.get("comparison_charts") or {}
        )
        self._populate_controls()
        dock = getattr(self, "dock", None)
        if dock is not None and "visible" in state:
            dock.setVisible(bool(state["visible"]))
        if bool(state.get("visible", False)):
            self._refresh_active_tab(tab)

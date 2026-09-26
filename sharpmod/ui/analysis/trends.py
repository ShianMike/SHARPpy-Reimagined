"""Trend page construction, rendering, tracks, and export."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

from qtpy.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QSizePolicy

from sharpmod.ui.features import gui_trend_tracks as trend_tracks
from sharpmod.state import run_updates
from sharpmod.analysis.box_analysis import PARAMETERS
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.ui.features.gui_common import action_label
from sharpmod.ui.features.gui_jobs import JobStatus
from sharpmod.analysis.profile_metrics import (
    DEFAULT_METRIC_KEYS, ProfileMetricsEngine, export_timeline_csv,
    export_timeline_series_csv, freeze_collection,
)
from sharpmod.ui.styles.theme import SPACE
from sharpmod.ui.analysis_controls import bounded_field
from sharpmod.ui.analysis.charts import (
    _TrendChart, _TrendSeries, _collection_label, _format_time, _get,
    _metric_title, _metric_value, _series_colours, _short_labels, _time_scope,
)
from sharpmod.ui.analysis.jobs import _TrendRequest, _compute_trends

_LOGGER = logging.getLogger("sharpmod.ui.analysis.workspace")


class TrendWorkspaceMixin:
    """Own the trend page and its interactions."""

    def _build_trends_tab(self):
        self.trends_tab, layout = self._new_page(self.tabs)
        selectors = self._new_row()
        self.trend_metric = QComboBox(self.trends_tab)
        self.trend_metric.setObjectName("analysisTrendMetric")
        self.trend_metric.setToolTip("Parameter plotted against valid time")
        self.trend_metric.setAccessibleName("Trend parameter")
        bounded_field(self.trend_metric)
        for item in PARAMETERS:
            self.trend_metric.addItem(item.display_label, item.key)
        default_metric = (
            str(DEFAULT_METRIC_KEYS[0]) if DEFAULT_METRIC_KEYS else "mlcape"
        )
        index = self.trend_metric.findData(default_metric)
        self.trend_metric.setCurrentIndex(max(0, index))
        # One parameter across every loaded sounding, rather than one sounding at
        # a time: there is no sounding picker here because the chart plots them
        # all, and the legend names the colours.
        selectors.addWidget(self.trend_metric, 1)
        from sharpmod.ui.features.gui_selectors import selector_search_button

        selectors.addWidget(selector_search_button(self.trend_metric))
        selectors.addStretch(1)
        layout.addLayout(selectors)

        actions = QHBoxLayout()
        actions.setSpacing(SPACE["sm"])
        self.trend_status = self._new_status(self.trends_tab)
        self.trend_status.setWordWrap(True)
        self.trend_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.trend_status)
        self.trend_job = JobStatus(self.trends_tab)
        self.trend_job.set_compact_terminal()
        self.trend_job.cancelRequested.connect(lambda: self._cancel_analysis("trends"))
        self.trend_job.retryRequested.connect(self._retry_trends)
        layout.addWidget(self.trend_job)
        self.trend_load = self._new_action(
            "Load forecast timeline…",
            "Open the existing Forecast Model picker to load a run with valid times",
            self.trends_tab,
        )
        self.trend_load.setHidden(True)
        layout.addWidget(self.trend_load)
        # Stacking is a deliberate choice, and so is fixing the ranges: an
        # automatically refitted axis makes two updates look alike when they are
        # not, and a silently fixed one hides how far the data actually goes.
        self.trend_tracks = self._new_action(
            "Tracks…",
            "Choose which diagnostics are stacked as separate trend tracks",
            self.trends_tab,
        )
        # UTC stays primary and is never replaced; local time is added beside it,
        # with its zone, offset, and daylight-saving state, because a bare local
        # hour is not something a reader can act on across a DST boundary.
        self.trend_local_time = QCheckBox("Local time", self.trends_tab)
        self.trend_local_time.setObjectName("analysisTrendLocalTime")
        self.trend_local_time.setAccessibleName(
            "Also show local time beside UTC"
        )
        self.trend_local_time.setToolTip(
            "Show the local equivalent alongside UTC, with its zone, UTC offset, "
            "and whether daylight saving is in force. UTC stays primary."
        )
        # A newer run is offered, never applied: swapping the data underneath an
        # analysis would invalidate cached comparison numbers.
        self.trend_newer_run = self._new_action(
            "Load newer run…",
            "A newer model run has published. Loading it is an explicit choice; "
            "the analysis on screen does not change until you make it.",
            self.trends_tab,
        )
        self.trend_newer_run.setHidden(True)
        layout.addWidget(self.trend_newer_run)
        self.trend_lock = QCheckBox("Fixed ranges", self.trends_tab)
        self.trend_lock.setObjectName("analysisTrendLockScales")
        self.trend_lock.setAccessibleName(
            "Fix the trend value ranges instead of refitting them"
        )
        self.trend_lock.setToolTip(
            "Fix each track's value range so successive updates stay comparable. "
            "Values beyond a fixed range are clamped and reported, never silently "
            "truncated."
        )
        self.trend_refit = self._new_action(
            "Refit", "Re-fit the fixed ranges to the data loaded now", self.trends_tab
        )
        self.trend_refit.setEnabled(False)
        self.trend_refresh = self._new_action(
            action_label("refresh"), "Recompute this timeline", self.trends_tab
        )
        self.trend_export = self._new_action(
            action_label("export_csv"),
            "Save the plotted series as CSV",
            self.trends_tab,
        )
        self.trend_export.setEnabled(False)
        settings = QHBoxLayout()
        settings.setSpacing(SPACE["lg"])
        settings.addWidget(self.trend_local_time)
        settings.addWidget(self.trend_lock)
        settings.addStretch(1)
        layout.addLayout(settings)
        for action in (
            self.trend_refit, self.trend_tracks, self.trend_refresh,
            self.trend_export,
        ):
            actions.addWidget(action, 1)
        actions.addStretch(1)
        for action in (
            self.trend_load, self.trend_newer_run, self.trend_refit,
            self.trend_tracks, self.trend_refresh, self.trend_export,
        ):
            action.setMinimumWidth(0)
            action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            action.setMaximumWidth(150)
        layout.addLayout(actions)

        self.trend_chart = _TrendChart(self.trends_tab)
        layout.addWidget(self.trend_chart, 1)
        self._add_tab(self.trends_tab, "trends")

        self.trend_tracks.clicked.connect(self._choose_trend_tracks)
        self.trend_lock.toggled.connect(self._trend_lock_toggled)
        self.trend_refit.clicked.connect(self._trend_refit_clicked)
        self.trend_local_time.toggled.connect(self._trend_local_time_toggled)
        self.trend_newer_run.clicked.connect(self._load_newer_run)

    def _refresh_trends(self, *_args):
        if self.tabs.currentIndex() != self.TAB_TRENDS:
            return
        collections = self._collections()
        if not collections:
            self.cancel_pending()
            self._trend_samples = ()
            self._trend_series = ()
            self.trend_chart.set_series((), "")
            self.trend_export.setEnabled(False)
            self._set_status(
                self.trend_status,
                "No soundings are loaded. Load a forecast-model run with one or "
                "more valid times to plot a trend.",
            )
            self._show_recovery(self.trend_load, True)
            return
        self._show_recovery(self.trend_load, False)
        # Every stacked track is asked for, so adding a track computes it rather
        # than drawing a blank band from a value set that never included it.
        keys = self.trend_track_keys()
        labels = tuple(
            _collection_label(collection, index)
            for index, collection in enumerate(collections)
        )
        shorts = _short_labels(collections)
        self._set_status(
            self.trend_status,
            f"Computing {len(collections)} timeline(s)\u2026",
        )

        frozen = tuple(freeze_collection(collection) for collection in collections)
        times = _time_scope(date for collection in frozen for date in collection._dates)
        self._start_trends(_TrendRequest(
            frozen, tuple(id(item) for item in collections), keys, labels, shorts,
            "; ".join(labels) + " · " + ", ".join(keys) + " · valid " + times
            + " · captured " + _format_time(datetime.now(timezone.utc)),
            isinstance(self.engine, ProfileMetricsEngine),
        ))

    def _start_trends(self, request):
        engine = self.engine
        self._request(
            "trends", lambda cancel, progress: _compute_trends(engine, request, cancel, progress),
            request=request,
        )

    def _render_trends(self, results, *, keys=None):
        colours = _series_colours(len(tuple(results or ())))
        series = []
        for position, entry in enumerate(tuple(results or ())):
            index, label, short, samples = entry
            series.append(
                _TrendSeries(
                    int(index), str(label), str(short), colours[position], samples
                )
            )
        self._trend_series = tuple(series)
        # Kept pointing at one series' samples: the CSV export and the session
        # both describe a focused sounding, and a flat tuple cannot say which
        # sounding a row came from.
        focused = self._focused_series()
        self._trend_samples = focused.samples if focused is not None else ()
        self.trend_chart.set_tracks(self._trend_series, keys or self.trend_track_keys())
        self._describe_trends()

    def _describe_trends(self):
        """Restate what is plotted, how many gaps there are, and the range mode."""
        update = self._refresh_run_notice()
        key = self.trend_chart.tracks()[0]
        plotted = sum(len(item.samples) for item in self._trend_series)
        available = sum(
            _metric_value(sample, key) is not None
            and bool(_get(sample, "available", True))
            for item in self._trend_series
            for sample in item.samples
        )
        missing = plotted - available
        if not plotted:
            text = (
                "The loaded soundings contain no forecast times to plot. Load a "
                "forecast-model run with one or more valid times."
            )
        else:
            count = len(self._trend_series)
            text = f"{available} available time(s)"
            if count > 1:
                text += f" across {count} soundings"
            if missing:
                text += f"; {missing} missing gap(s) are marked \u00d7"
            tracks = self.trend_chart.tracks()
            if len(tracks) > 1:
                # Say the tracks do not share a value scale: reading a stacked
                # chart as one scale is the mistake it invites.
                text += (
                    f"; {len(tracks)} stacked tracks share the time axis only, "
                    "each with its own units and scale"
                )
            if self.trend_chart.scale_lock():
                text += "; ranges fixed"
                clipped = self.trend_chart.clipped_tracks()
                if clipped:
                    names = ", ".join(
                        _metric_title(metric)[0] for metric in clipped
                    )
                    text += (
                        f"; {names} clamped at the axis edge, use Refit to see it"
                    )
            if self.trend_chart.show_local_time():
                text += "; local time shown beside UTC"
            if update.available:
                # Stated, not acted on. The wording has to make clear the plot is
                # still the run it was.
                text += f"; a newer {update.model.upper()} run has published"
            text += ". Click a point, or focus the chart and use \u2190 \u2192 Enter."
        self._set_status(self.trend_status, text)
        self.trend_export.setEnabled(bool(plotted))
        self._show_recovery(self.trend_load, not plotted or not available)

    def trend_track_keys(self):
        """Return the metrics stacked as trend tracks, primary first.

        The selector remains the primary track, so the tooltip, the CSV export,
        and the chart keep describing the parameter the reader chose there.
        """
        primary = str(self.trend_metric.currentData() or "")
        keys = [primary] if primary else []
        for key in self._trend_track_keys:
            if key != primary and key not in keys:
                keys.append(key)
        return tuple(trend_tracks.normalize_keys(keys or (primary,)))

    def set_trend_track_keys(self, keys, *, persist=True):
        """Choose the stacked tracks, optionally storing the preference."""
        normalized = trend_tracks.normalize_keys(keys)
        changed = normalized != self._trend_track_keys
        self._trend_track_keys = normalized
        if persist:
            trend_tracks.write_preference(self._settings(), normalized)
        if changed:
            self._refresh_trends()
        else:
            self._render_trend_tracks()

    def _choose_trend_tracks(self):
        dialog = trend_tracks.track_dialog(self.trend_track_keys(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.selected_keys()
        if not chosen:
            return
        # A chosen primary stays primary: the first track is what the selector and
        # every downstream consumer name.
        index = self.trend_metric.findData(chosen[0])
        if index >= 0:
            blocked = self.trend_metric.blockSignals(True)
            self.trend_metric.setCurrentIndex(index)
            self.trend_metric.blockSignals(blocked)
        self.set_trend_track_keys(chosen)

    def _trend_lock_toggled(self, checked):
        self.trend_chart.set_scale_lock(bool(checked))
        self.trend_refit.setEnabled(bool(checked))
        self._describe_trends()

    def _trend_refit_clicked(self):
        self.trend_chart.refit_scales()
        self._describe_trends()

    def _render_trend_tracks(self):
        """Re-draw the chart from the cached series at the current track set."""
        if not self._trend_series:
            return
        self.trend_chart.set_tracks(self._trend_series, self.trend_track_keys())
        self._describe_trends()

    def trend_scale_state(self):
        return self.trend_chart.scale_state()

    def restore_trend_scale_state(self, state):
        self.trend_chart.restore_scale_state(state)
        locked = self.trend_chart.scale_lock()
        blocked = self.trend_lock.blockSignals(True)
        self.trend_lock.setChecked(locked)
        self.trend_lock.blockSignals(blocked)
        self.trend_refit.setEnabled(locked)

    def _trend_local_time_toggled(self, checked):
        self.trend_chart.set_show_local_time(bool(checked))
        self._describe_trends()

    def run_update(self):
        """Report whether the focused sounding's model has a newer run."""
        collections = self._collections()
        index = max(0, min(len(collections) - 1, self._trend_collection_index))
        if not collections:
            return run_updates.check("", None)
        return run_updates.check_collection(collections[index])

    def _refresh_run_notice(self):
        """Offer a newer run if one exists. Never adopts it."""
        update = self.run_update()
        self._show_recovery(self.trend_newer_run, bool(update.available))
        if update.available:
            self.trend_newer_run.setToolTip(
                update.summary()
                + "\nLoading it opens the existing Forecast Model picker; the "
                "analysis on screen is left exactly as it is."
            )
        return update

    def _load_newer_run(self):
        """Open the existing picker so the reader loads the newer run themselves.

        Deliberately does not fetch or swap anything: the sounding on screen keeps
        its run, and the newer one arrives as an additional sounding through the
        same path any other sounding does.
        """
        update = self.run_update()
        self._set_status(
            self.trend_status,
            update.summary()
            + " The loaded sounding is unchanged; choose the newer run in the "
            "picker to add it.",
        )
        self._request_sounding_input("Forecast Model")

    def _focused_series(self):
        """The series for the sounding the viewer currently has in front."""
        if not self._trend_series:
            return None
        wanted = self._trend_collection_index
        for item in self._trend_series:
            if item.collection_index == wanted:
                return item
        return self._trend_series[0]

    def _activate_trend_point(self, point_index):
        """Open the valid time a plotted point stands for, in its own sounding.

        With every sounding on one axis the point has to carry its collection:
        activating a RAP point must not move the HRRR timeline.
        """
        item, sample = self.trend_chart.sample_at(point_index)
        if item is None or sample is None:
            return
        collections = self._collections()
        target = int(item.collection_index)
        try:
            collections[target].setCurrentDate(_get(sample, "valid_time"))
        except (AttributeError, IndexError, TypeError, ValueError):
            return
        self._trend_collection_index = target
        win = self._window()
        widget = getattr(win, "spc_widget", None) if win is not None else None
        if widget is None:
            return
        try:
            prof_ids = tuple(getattr(widget, "prof_ids", ()))
            if target < len(prof_ids) and hasattr(widget, "setProfileCollection"):
                widget.setProfileCollection(prof_ids[target])
            else:
                widget.pc_idx = target
                widget.updateProfs()
        except Exception:
            _LOGGER.exception("analysis_workspace.trend_activation_failed")

    def _choose_trend_export(self):
        if not self._trend_samples:
            return
        try:
            suggested = export_file_path("sounding-trend.csv")
        except ExportDirectoryError as exc:
            self._set_status(self.trend_status, str(exc), level="error")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export parameter trend",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            try:
                saved = self.export_trend(path)
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self._set_status(
                    self.trend_status, f"CSV export failed: {exc}", level="error"
                )
            else:
                self._set_status(self.trend_status, f"Saved {saved}")

    def export_trend(self, path):
        """Export what is plotted, without re-running any analysis.

        A single sounding writes the plain timeline columns; several add a
        leading ``sounding`` column, because otherwise the rows could not be told
        apart. The header describes the data, which is the same rule the metric
        columns already follow.
        """
        # Every stacked track, because all of them are plotted and a CSV that
        # dropped the lower bands would not describe the chart it came from.
        keys = self.trend_track_keys()
        if len(self._trend_series) > 1:
            return export_timeline_series_csv(
                Path(path),
                ((item.label, item.samples) for item in self._trend_series),
                keys=keys,
            )
        return export_timeline_csv(Path(path), self._trend_samples, keys=keys)

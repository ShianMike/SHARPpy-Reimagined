"""Public desktop entry point and composition root for the sounding picker.

``PickerWindow`` combines focused picker/map workflow mixins, starts background
availability and fetch workers, and opens sounding viewers; ``main`` configures the
native Qt runtime and launches the application. Keep historical imports here stable
while new picker behavior lives under ``sharpmod.ui.picker``."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path



from sharpmod.ui.features.gui_common import (
    APP_NAME,
    APP_VERSION,
    CONTROLS_HTML,
    MAX_RECENTS,
    SYNOPTIC_HOURS,
    _LOGGER,
    _configure_debug_logging,
    _format_progress_bytes,
    _format_progress_duration,
    _most_recent_synoptic,
    as_utc,
    action_label,
    make_status_label,
    scrollable_page,
    install_month_calendar,
    install_popup_placement,
    install_wheel_guard,
    _render,
    _install_fullscreen_action,
    _show_controls_dialog,
    _uwyo_catalog,
)
from sharpmod.ui.shell import ResponsivePickerHeader, SourceSelector
from sharpmod.ui.features.gui_jobs import JobCounts, JobStatus
from sharpmod.ui.picker.box_workflow import BoxWorkflowMixin
from sharpmod.ui.picker.panels_workflow import HRRR_FIELD_MODEL_KEY, PanelsWorkflowMixin
from sharpmod.ui.picker.model_selection import ModelSelectionMixin
from sharpmod.ui.picker.model_jobs import ModelJobsMixin
from sharpmod.ui.picker.files import FileSourceMixin
from sharpmod.ui.picker.observed_workflow import ObservedWorkflowMixin
from sharpmod.ui.picker.map_workflow import MapSourceMixin
from sharpmod.ui.picker.locations import PickerLocationsMixin
from sharpmod.ui.picker.chrome import PickerChromeMixin
from sharpmod.ui.picker.presentation_controls import MapPresentationControlsMixin
from sharpmod.ui.picker.layers import TAB_LOCATOR_SELECTORS, PickerLayersMixin
from sharpmod.ui.picker.layer_session import PickerLayerSessionMixin
from sharpmod.ui.picker.layout import (
    ActiveLayerList as _ActiveLayerList,
    fit_summary_groups as _fit_summary_groups,
    DATE_DISPLAY_FORMAT as _DATE_DISPLAY_FORMAT,
    TOWN_LOOKUP_TOOLTIP as _TOWN_LOOKUP_TOOLTIP,
    UTC_CLOCK_SAMPLE as _UTC_CLOCK_SAMPLE,
    map_tool_row as _map_tool_row,
    order_rail_cards as _order_rail_cards,
    rail_card as _rail_card,
    rail_form as _rail_form,
    rail_row as _rail_row,
    rail_zoom_row as _rail_zoom_row,
    scrolling_control_rail as _scrolling_control_rail,
    SelectionPane as _SelectionPane,
    selection_pane as _selection_pane,
    SUMMARY_JOIN as _SUMMARY_JOIN,
    TOP_SUMMARY_CHARS as _TOP_SUMMARY_CHARS,
    TOP_SUMMARY_HEADROOM as _TOP_SUMMARY_HEADROOM,
    TOP_SUMMARY_MIN_CHARS as _TOP_SUMMARY_MIN_CHARS,
    set_button_busy as _set_button_busy,
    town_lookup_attribution_label as _town_lookup_attribution_label,
)
from sharpmod.ui.picker.era5 import Era5PickerMixin
from sharpmod.ui.picker.selection import (
    FileSelectionFeedback,
    install_selection_feedback,
    refresh_selection_feedback,
)
from sharpmod.ui.picker.wrf import WrfPickerMixin
from sharpmod.ui.features.gui_theme import apply_theme, ensure_theme_applied, mono_font
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    FIELD_W,
    OBJ_ATTRIBUTION,
    OBJ_CARD_RULE,
    OBJ_CARD_TOGGLE,
    OBJ_EMPHASIS,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_MAP_LAYER_TABS,
    OBJ_NUMERIC,
    OBJ_PLAIN,
    OBJ_POINT_LOCK,
    OBJ_PRIMARY,
    OBJ_PROGRESS_DETAIL,
    OBJ_SECTION_LABEL,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    OBJ_TOP_BAR,
    OBJ_TOP_BAR_END,
    OBJ_TOP_BAR_LABEL,
    OBJ_TOP_BAR_MENU,
    OBJ_TOP_BAR_SOURCE,
    OBJ_UTC_CLOCK,
    PROGRESS_H,
    RAIL_W,
    SCROLLBAR_W,
    SPACE,
)
from sharpmod.ui.features.gui_maps import (
    MAP_AREAS,
    MAP_PROJECTIONS,
    PointMapWidget,
    StationMapWidget,
)
from sharpmod.ui.features.gui_cache import CacheManagerDialog, parse_spatial_point
from sharpmod.ui.features.gui_locations import SavedLocationsDialog
from sharpmod.state.saved_locations import (
    RECENT_SETTINGS_KEY,
    SavedLocationStore,
    is_generated_recent_label,
)
from sharpmod.ui.maps.overlays.controllers import (
    HrrrFieldController,
    OutlookOverlayController,
    RadarOverlayController,
    StormReportsOverlayController,
)
from sharpmod.ui.features.gui_sessions import _apply_viewer_session_state
from sharpmod.ui.features.gui_threading import (
    retain_worker_until_finished,
    shutdown_picker_workers,
)
from sharpmod.ui.features.gui_settings import (
    UNIT_DEFAULTS,
    UNIT_OPTIONS,
    _DEFAULT_SKEWT_PARCEL,
    _UnitPreferencesDialog,
    _add_default_parcel_tab,
    _add_interface_tab,
    _apply_selected_color_style,
    _apply_default_parcel_to_window,
    _apply_unit_preferences_to_window,
    _build_preferences_dialog,
    _build_settings,
    _normalize_default_parcel,
    _normalize_interface_preferences,
    _normalize_unit_preferences,
    _read_config_preferences,
    _read_config_unit,
    _read_interface_preferences,
    _read_settings_preferences,
    _save_settings_preferences,
    _save_interface_preferences,
    _write_config_preferences,
    _write_unit_preferences_to_config,
)
from sharpmod.ui.features.gui_workers import (
    AVAIL_AVAILABLE,
    AVAIL_CHECKING,
    AVAIL_FALLBACK,
    AVAIL_UNKNOWN,
    _AVAIL_LABELS,
    _AvailabilityIndicator,
    _AvailabilityWorker,
    _FetchWorker,
    _ERA5FetchWorker,
    _ModelAvailabilityWorker,
    _ModelCachePruneWorker,
    _ModelFetchWorker,
    _ModelPrefetchWorker,
    _StationListWorker,
    _WRFExtractWorker,
    _WRFInspectWorker,
    _cleanup_model_data,
    _cleanup_point_data,
    _retain_model_data_until_close,
    _retain_point_data_until_close,
    _station_label,
)

from qtpy.QtCore import (
    Qt,
    QThread,
    QTimer,
    Signal,
    QDate,
    QSettings,
    QPointF,
    QRectF,
    QSize,
    QUrl,
)
from qtpy.QtGui import (
    QAction,
    QActionGroup,
    QPainter,
    QColor,
    QPen,
    QBrush,
    QPolygonF,
    QFont,
    QPixmap,
    QIcon,
    QTransform,
    QDesktopServices,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QDateEdit,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QStatusBar,
    QToolButton,
    QScrollArea,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
    QLayout,
    QCompleter,
)

from sharpmod.ui.picker.cycles import (
    _CYCLE_LATEST, _select_cycle, _run_datetime, _latest_elapsed_cycle,
    _annotate_cycle_combo, _fxx_item_text, _fill_cycle_combo,
    _newest_cycle_not_after,
)
from sharpmod.ui.picker.observed_sources import (
    OBSERVED_SOURCES, DEFAULT_OBSERVED_SOURCE,
)

from sharpmod.ui.picker.pages import SourcePagesMixin

_STABLE_GUI_RUNTIME_ENV = "SHARPMOD_GUI_STABLE_RUNTIME"

#: The gridded map overlay is HRRR. When this product is the tab's selection the
#: overlay can be pinned to the exact cycle on screen; for anything else it can
#: only match the valid time, because another model's run and forecast hour do
#: not name an HRRR forecast.

#: Opt-in for an intentionally windowless ``main`` (GUI smoke tests, CI).
_HEADLESS_GUI_ENV = "SHARPMOD_GUI_HEADLESS"

#: Qt platform plugins that cannot present a window at all. ``vnc``, ``xcb``,
#: ``wayland`` and friends are excluded on purpose: they do present, just not
#: necessarily locally, so overriding them would override a real choice.
_NON_VISUAL_QT_PLATFORMS = frozenset({"offscreen", "minimal"})






#: Source tab title -> the overlay controller attribute that tab owns. Tabs
#: absent from this map host no overlay of their own. Keyed by title because
#: that is what ``SourceSelector`` exposes and what the rest of the picker keys
#: on; the tab order is not stable enough to index by.

#: The gridded-field controller per tab, resolved the same way the outlook one
#: is: the tab in front decides, because that is the map the user was reading
#: when they asked for the sounding.


#: Which tab's locator selector answers for a sounding opened from it.










#: First Python feature release the Windows desktop GUI is not known to
#: survive. ``pyproject.toml`` bounds the distribution at ``<3.14`` for the same
#: reason: under 3.14 the picker has access-violated during a worker thread's
#: garbage collection inside pandas, with no catchable traceback.






def _project_gui_runtime() -> tuple[Path, Path] | None:
    """Return a project Python suitable for the Windows desktop GUI.

    Release executables are frozen with the supported Python runtime.  Source
    checkouts may instead be invoked by whichever ``python`` is first on PATH,
    so prefer their local environment when it exists.

    The candidate's version is verified before it is offered. An environment
    that cannot state its version, or states an equally unsupported one, is not
    a rescue: relaunching into it would reach the same crash, and the child
    cannot tell that it was already the fallback. Requiring a known-supported
    version is what makes a single relaunch attempt provably terminal.
    """
    project_root = Path(__file__).resolve().parents[1]
    current = Path(sys.executable).resolve()
    for environment in (".gribenv", ".venv", "venv"):
        root = project_root / environment
        version = _venv_python_version(root)
        if version is None or not _supported_gui_python(version):
            continue
        for executable in ("pythonw.exe", "python.exe"):
            candidate = root / "Scripts" / executable
            if candidate.is_file() and candidate.resolve() != current:
                return candidate, project_root
    return None




#: Held open for the process lifetime once ``faulthandler`` is armed against it.
_NATIVE_FAULT_STREAM = None






from sharpmod.ui.picker.startup import (
    compose_interactive,
    _observed_source_label,
    _overlay_product_for,
    _locator_spec_for,
    _start_locator_overlay_fetch,
    _fill_profile_metadata,
    _supported_gui_python,
    _venv_python_version,
    _show_stable_gui_runtime_required,
    _native_crash_capture,
    _relaunch_stable_windows_gui,
    TAB_OVERLAY_CONTROLLERS,
    TAB_FIELD_CONTROLLERS,
    TAB_CONTEXT_CONTROLLERS,
    _MAX_GUI_PYTHON,
)


from sharpmod.ui.picker.preferences import PickerPreferencesMixin
from sharpmod.ui.picker.availability import PickerAvailabilityMixin
from sharpmod.ui.picker.viewer_state import PickerViewerMixin


from sharpmod.ui.picker.fetch_handlers import PickerFetchMixin


class PickerWindow(PickerFetchMixin, PickerPreferencesMixin, PickerAvailabilityMixin, PickerViewerMixin, PickerChromeMixin, MapPresentationControlsMixin, PickerLayersMixin, PickerLayerSessionMixin, PickerLocationsMixin, MapSourceMixin, ObservedWorkflowMixin, FileSourceMixin, ModelSelectionMixin, ModelJobsMixin, PanelsWorkflowMixin, BoxWorkflowMixin, SourcePagesMixin, Era5PickerMixin, WrfPickerMixin, QMainWindow):
    """The launcher: fetch an observed sounding or open a local sounding file.

    Designed to be immediately usable: the full UWyo station catalogue is loaded
    up front and filtered live as you type, the observation time defaults to the
    most recent sounding cycle, and a sounding opens on a double-click (or the
    single Fetch button). Local files can be dropped straight onto the window.
    """

    #: Emitted after the preferences change; every open ``SPCWindow`` subscribes
    #: to this (via its Qt parent) to refresh profiles + re-apply the palette.
    #: The picker doubles as the SHARPpy controller (mirrors the legacy Main
    #: window), so it owns the shared config and this signal.
    config_changed = Signal(object)

    def __init__(self):
        super().__init__()
        # The chrome theme is applied once on the QApplication, not per window:
        # four of the five source panels below are built lazily and every dialog
        # is constructed on demand, so a window-level style sheet would miss
        # everything created after this point.
        #
        # ``main`` applies it before the first widget exists, which avoids a
        # visible restyle at startup. This call covers the other entry points --
        # the test suite and any embedder construct PickerWindow directly -- and
        # is a no-op when the theme is already applied.
        interface = _startup_interface_preferences()
        ensure_theme_applied(
            color_style=_startup_color_style(),
            text_scale=interface["text_scale"],
            density=interface["density"],
        )
        # Same reasoning, same shape: ``main`` installs this before any widget
        # exists, and this call covers the direct-construction paths.
        install_wheel_guard()
        install_popup_placement()

        self.setWindowTitle(f"{APP_NAME} \u2014 Sounding Picker")
        self.resize(1000, 720)
        self.setMinimumSize(900, 620)
        self.setAcceptDrops(True)  # drag a sounding file onto the window

        # Keep every opened sounding window alive. Each vendored ``SPCWindow`` is
        # a top-level window parented to this picker, but we also hold a Python
        # reference so it is never garbage-collected out from under Qt.
        self._viewers: list = []
        self._worker: _FetchWorker | None = None
        self._observed_token = None
        self._observed_request = None
        self._observed_job = None
        self._observed_job_retry = None
        self._observed_job_scroll = None
        self._model_worker: _ModelFetchWorker | None = None
        self._model_timeline_worker = None
        self._model_timeline_viewer = None
        self._model_timeline_collection = None
        self._model_timeline_output_dir: str | None = None
        self._model_timeline_paths: list[str] = []
        self._model_timeline_failures: dict[int, str] = {}
        self._model_timeline_viewer_closed = False
        # Box-sounding state. The extraction and analysis stages are separate
        # workers because analysis can be re-run (to add the expensive composite
        # tier) without re-fetching anything.
        self._box_extract_worker = None
        self._box_analysis_worker = None
        self._box_mean_worker = None
        self._box_window = None
        self._box_window_closed = False
        self._box_extraction = None
        # "mean" collapses the box to one sounding; "field" opens the workspace.
        self._box_mode = "mean"
        self._box_output_dir: str | None = None
        self._model_prefetch_worker: _ModelPrefetchWorker | None = None
        self._era5_worker: _ERA5FetchWorker | None = None
        self._era5_token = None
        self._era5_request = None
        self._era5_job = None
        self._era5_job_retry = None
        self._era5_job_scroll = None
        self._wrf_inspect_worker: _WRFInspectWorker | None = None
        self._wrf_extract_worker: _WRFExtractWorker | None = None
        self._wrf_token = None
        self._wrf_request = None
        self._wrf_job = None
        self._wrf_job_retry = None
        self._wrf_job_scroll = None
        self._wrf_domain: dict | None = None
        # The persistent cache imports NumPy-backed sounding validation and its
        # prune walks every cached payload. Create it only when a model/ERA5 or
        # library action actually needs it, then prune on a worker thread.
        self._model_disk_cache = None
        self._model_hour_cache = None
        self._model_cache_prune_worker: _ModelCachePruneWorker | None = None
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_model_cache)
        self._model_progress_stage = ""
        self._model_progress_total = 0
        self._model_progress_started = 0.0
        self._model_progress_download_baseline = 0
        self._model_availability_waiting_for_worker = False
        self._model_progress_timer = QTimer(self)
        self._model_progress_timer.setInterval(500)
        self._model_progress_timer.timeout.connect(self._poll_model_fetch_progress)
        self._settings = _build_settings()
        self._multi_sounding_checkboxes: list[QCheckBox] = []
        self._saved_location_store = SavedLocationStore(self._settings)
        self._recent_location_store = SavedLocationStore(
            self._settings, key=RECENT_SETTINGS_KEY, max_entries=12
        )
        self._all_stations = _uwyo_catalog().all_stations()

        # -- availability pre-flight check state ----------------------------- #
        # A background probe grades the selected station/time as green (usable),
        # red (nothing archived / unreachable), or gray (present but too sparse
        # or corrupt). Checks are debounced and stale results are discarded via
        # a monotonically increasing token.
        self._avail_workers: list[_AvailabilityWorker] = []
        self._avail_pending: dict[int, _AvailabilityIndicator] = {}
        self._avail_latest: dict[int, int] = {}
        self._avail_token = 0
        self._avail_request: tuple | None = None
        self._observed_profile_cache: dict[tuple[str, datetime], object] = {}
        self._avail_timer = QTimer(self)
        self._avail_timer.setSingleShot(True)
        self._avail_timer.setInterval(350)
        self._avail_timer.timeout.connect(self._run_pending_availability)

        # Forecast catalog checks are independent of the actual fetch worker.
        # They are deliberately advisory: a failed inventory request never
        # disables Fetch because catalog propagation and upstream mirrors can
        # lag behind the data itself.
        self._model_availability_workers: list[_ModelAvailabilityWorker] = []
        self._model_availability_token = 0
        self._model_availability_request: tuple | None = None
        self._model_available_run: datetime | None = None
        self._model_availability_timer = QTimer(self)
        self._model_availability_timer.setSingleShot(True)
        self._model_availability_timer.setInterval(450)
        self._model_availability_timer.timeout.connect(self._run_model_availability)

        # -- datetime-aware station catalogue state -------------------------- #
        # The station set shown in the map + list is refreshed from UWyo for the
        # selected observation time, so relocated / re-indexed stations appear
        # for the period they actually reported. The bundled catalogue is the
        # offline fallback used until the first live list arrives (or if the
        # network is unavailable). Refreshes are debounced and stale results are
        # discarded via a monotonically increasing token.
        self._catalog_when: datetime | None = None
        self._catalog_worker: _StationListWorker | None = None
        self._catalog_token = 0
        self._catalog_request: datetime | None = None
        self._catalog_timer = QTimer(self)
        self._catalog_timer.setSingleShot(True)
        self._catalog_timer.setInterval(300)
        self._catalog_timer.timeout.connect(self._run_pending_catalog)

        # The one shared render/display config, owned by the controller (this
        # window). Built lazily on first sounding/preference use so the picker
        # window appears before the heavy render stack is imported.
        self.config = None

        # A compact top-bar selector rather than tabs or a permanent left rail.
        # ``SourceSelector`` keeps the QTabWidget surface (addTab / tabText /
        # setCurrentIndex / currentChanged), so the title-keyed call sites and
        # lazy placeholder swap in ``_ensure_tab`` stay unchanged.
        # "Load from" rather than "Source": two of the five entries (Station Map
        # and Station List) are the same UWyo source reached two ways, and the
        # map panel already has a "Sounding source" card naming the provider.
        self._tabs = SourceSelector(header="LOAD FROM")
        self._tabs.addTab(self._build_map_tab(), "Station Map")
        # "Field Panels" is a source like the others: it draws HRRR fields to
        # decide *where* to look, and a click in it picks the point a sounding is
        # taken at. It was a floating window first, which put its panels on a run
        # it had no controls to change.
        self._lazy_tab_builders = {
            "Station List": self._build_uwyo_tab,
            "Forecast Model": self._build_model_tab,
            "Field Panels": self._build_panels_tab,
            "Reanalysis (ERA5)": self._build_era5_tab,
            "Open File": self._build_file_tab,
        }
        for title in self._lazy_tab_builders:
            self._tabs.addTab(self._lazy_tab_placeholder(title), title)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        self.setStatusBar(QStatusBar())
        self._sync_tab_status()

        self._build_menu()
        self._install_source_picker()
        self._install_utc_clock()
        self._responsive_header = ResponsivePickerHeader(
            self, self._tabs, self._source_picker, self._utc_clock_frame,
            after_reflow=self._refresh_top_summary_width,
        )
        self._restore_state()
        self._refresh_location_markers()
        self._refresh_recent_location_menu()
        # After the menu, the clock, and the restored tab all exist: earlier tab
        # changes fired before there was anywhere to put the summary.
        self._sync_top_bar_context()


    # -- UTC clock ----------------------------------------------------------- #


    # -- top-bar selection context ------------------------------------------- #


    # -- menu ---------------------------------------------------------------- #







    # -- controller contract (this window doubles as the SHARPpy controller) - #
















    # -- map projection -------------------------------------------------- #






    # ====================================================================== #
    # T19 active-layer list, presets, scoped retry, session choices
    # ====================================================================== #







    # ====================================================================== #
    # Availability pre-flight check (green / red / gray)
    # ====================================================================== #





    # ====================================================================== #
    # Datetime-aware station catalogue refresh
    # ====================================================================== #




    # ====================================================================== #
    # Station Map tab (legacy-SHARPpy style)
    # ====================================================================== #


    # -- map inspection sampler (T18.2-T18.4) -------------------------------- #
    _INSPECTION_MAP_ATTRIBUTES = ("_model_map", "_map")


    # Observed controls live in ui/picker/observed_workflow.py. Keep the
    # constructor here for the legacy gui_picker._FetchWorker patch point.


    # The worker constructor stays here so legacy callers can replace
    # gui_picker._ModelAvailabilityWorker during availability checks.


    # Box workflow methods live in ui/picker/box_workflow.py. This callback
    # stays here because callers patch the legacy cleanup function below.
    # -- box mean: the whole area as one sounding -------------------------- #




    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop owned QThreads when the picker window itself is closed."""
        self._shutdown_model_cache()
        # Radar controllers are torn down here but are deliberately absent from
        # TAB_OVERLAY_CONTROLLERS: that map answers "which SPC hazard is the user
        # looking at" for the sounding locator, and a radar product key is not an
        # answer to that question.
        for attr in (
            "_map_outlook",
            "_model_outlook",
            "_map_radar",
            "_model_radar",
            "_map_field",
            "_model_field",
            "_map_reports",
            "_model_reports",
            "_map_context",
            "_model_context",
            "_panels_outlook",
            "_panels_radar",
            "_panels_reports",
            "_panels_context",
        ):
            controller = getattr(self, attr, None)
            if controller is not None:
                controller.shutdown()
        # The field-panels tab owns one field controller per panel, so closing the
        # picker without this leaves up to four fetches running against maps that
        # are about to be destroyed.
        panels = getattr(self, "_panels_view", None)
        if panels is not None:
            with suppress(RuntimeError):
                panels.shutdown()
        self._remember_overlay_choices()
        self._remember_locator_choice()
        super().closeEvent(event)




    # ====================================================================== #
    # Download library and saved/recent points
    # ====================================================================== #








    # ====================================================================== #
    # Open File tab
    # ====================================================================== #


    # -- recents ------------------------------------------------------------- #



    # ====================================================================== #
    # Drag & drop (open a file dropped anywhere on the window)
    # ====================================================================== #


    # ====================================================================== #
    # Shared / lifecycle
    # ====================================================================== #

















def _app_icon() -> QIcon:
    """Resolve the bundled application icon as a :class:`QIcon`.

    Prefers the multi-resolution ``app.ico`` and falls back to ``app.png``,
    resolved package-relative via :mod:`importlib.resources` so it works both
    from a source checkout and inside the frozen PyInstaller bundle. Returns an
    empty ``QIcon`` if the resource is missing (the app still runs).
    """
    try:
        from importlib.resources import files

        icons = files("sharpmod.resources").joinpath("icons")
        for name in ("app.ico", "app.png"):
            res = icons.joinpath(name)
            try:
                if res.is_file():
                    return QIcon(str(res))
            except (FileNotFoundError, OSError):
                continue
    except Exception:  # noqa: BLE001 -- icon is cosmetic; never block launch
        pass
    return QIcon()


def _startup_color_style() -> str:
    """Read the persisted palette choice before any widget is built.

    The chrome theme is paired with the canvas palette, so it has to be known
    before the first window appears -- otherwise the app flashes the default
    dark chrome and then repaints light for an ``inverted`` user. Reads the
    durable INI directly rather than going through
    :meth:`PickerWindow._config`, which builds the heavy render config.

    Never raises: an unreadable settings file falls back to the default.
    """
    try:
        settings = _build_settings()
        return _read_settings_preferences(settings).get("color_style", "standard")
    except Exception:
        _LOGGER.warning("startup.color_style_unreadable", exc_info=True)
        return "standard"


def _startup_interface_preferences() -> dict[str, str]:
    """Read chrome sizing before the first widget is constructed."""
    try:
        return _read_interface_preferences(_build_settings())
    except Exception:
        _LOGGER.warning("startup.interface_preferences_unreadable", exc_info=True)
        return _normalize_interface_preferences({})


def _configure_high_dpi() -> None:
    """Opt into fractional display scaling before ``QApplication`` exists.

    Qt6 enables high-DPI scaling by default but still *rounds* the scale factor,
    so a 150% display is treated as either 100% or 200%. ``PassThrough`` keeps
    the true factor, which matters here because the chrome uses hairline (1 px)
    borders that vanish or double under rounding.

    Must run before the application object is constructed; Qt ignores it
    afterwards.
    """
    try:
        from qtpy.QtCore import Qt as _Qt
        from qtpy.QtGui import QGuiApplication

        if QApplication.instance() is not None:
            # An application already exists (embedded or test host), so the
            # policy is locked in and setting it now would be a no-op warning.
            return
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            _Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        # Older bindings lack the enum; default rounding is still usable.
        _LOGGER.debug("startup.high_dpi_policy_unavailable", exc_info=True)


def _enable_native_fault_reports() -> None:
    """Dump a stack to the native log if the interpreter faults.

    A segmentation fault or access violation cannot be raised as a Python
    exception, so the excepthook installed by ``_configure_debug_logging`` never
    sees it and the rotating log just stops. Arming ``faulthandler`` against a
    file this application owns means such a crash is still described somewhere,
    whichever way the GUI was launched -- the relaunch path is not the only one
    that can fault.
    """
    global _NATIVE_FAULT_STREAM
    if _NATIVE_FAULT_STREAM is not None:
        return
    try:
        import faulthandler

        path = Path(_configure_debug_logging()).with_name("sharpmod-gui-native.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "a", encoding="utf-8", errors="replace")
        stream.write(
            "\n=== faulthandler armed %s pid=%d python=%s ===\n"
            % (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                os.getpid(),
                sys.version.split()[0],
            )
        )
        stream.flush()
        # Held for the process lifetime: faulthandler writes to the file
        # descriptor, so letting this be collected would arm a closed stream.
        _NATIVE_FAULT_STREAM = stream
        faulthandler.enable(file=stream, all_threads=True)
    except Exception:  # noqa: BLE001 - diagnostics must never block startup
        _LOGGER.debug("startup.faulthandler_unavailable", exc_info=True)


def _native_qt_platform() -> str:
    """Return the Qt platform plugin that presents windows on this OS."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "cocoa"
    return "xcb"


def _requested_qt_platform() -> str:
    """Return the plugin name from ``QT_QPA_PLATFORM``, without its options.

    Qt accepts ``plugin:option=value`` and a ``;``-separated fallback list, so
    the raw value cannot be compared to a plugin name directly.
    """
    raw = os.environ.get("QT_QPA_PLATFORM", "").strip()
    return raw.split(";", 1)[0].split(":", 1)[0].strip().lower()


def _restore_visual_qt_platform() -> None:
    """Refuse an inherited headless Qt platform for the desktop entry point.

    ``sharpmod.gui`` only *defaults* ``QT_QPA_PLATFORM``, so a value already in
    the environment wins. That is correct for the renderer and the test suite,
    which both want ``offscreen``, and wrong here: this is the launcher whose
    only job is to put a window on screen.

    Inheriting a non-presenting plugin is silent and looks exactly like a hang.
    Qt creates no native window, ``PickerWindow`` builds and "shows" happily,
    the event loop runs, and nothing is logged after the theme is applied. The
    process then sits there with no window and no error until it is killed --
    and because the relaunch helper detaches the child, each attempt leaves
    another invisible process behind. A shell that exported ``offscreen`` for a
    headless render or a test run is enough to cause it.

    Set ``SHARPMOD_GUI_HEADLESS=1`` to keep the inherited platform for a
    deliberately windowless run.
    """
    requested = _requested_qt_platform()
    if requested not in _NON_VISUAL_QT_PLATFORMS:
        return
    if os.environ.get(_HEADLESS_GUI_ENV, "").strip() == "1":
        _LOGGER.info("startup.headless_platform_kept platform=%s", requested)
        return
    if QApplication.instance() is not None:
        # Qt resolved the plugin when that application was constructed. Editing
        # the variable now would change the log and nothing else.
        _LOGGER.warning("startup.headless_platform_locked platform=%s", requested)
        return
    native = _native_qt_platform()
    os.environ["QT_QPA_PLATFORM"] = native
    _LOGGER.warning(
        "startup.headless_platform_overridden inherited=%s using=%s override_with=%s=1",
        requested,
        native,
        _HEADLESS_GUI_ENV,
    )


def main(argv: list[str] | None = None) -> int:
    """Launch the interactive picker. Entry point for ``sharpmod-gui``."""
    _configure_debug_logging()
    _enable_native_fault_reports()
    relaunch_arguments = list(sys.argv[1:]) if argv is None else list(argv[1:])
    if _relaunch_stable_windows_gui(relaunch_arguments):
        return 0
    _LOGGER.info("application.start argv=%r", sys.argv if argv is None else argv)

    # Before QApplication: Qt resolves the platform plugin at construction.
    _restore_visual_qt_platform()
    _configure_high_dpi()

    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    # Style, palette, bundled chrome fonts, and the generated style sheet, in
    # one place and before the first widget is constructed.
    interface = _startup_interface_preferences()
    apply_theme(
        app,
        color_style=_startup_color_style(),
        text_scale=interface["text_scale"],
        density=interface["density"],
    )
    # Before any widget too, so no control is ever briefly wheel-sensitive, and no
    # dropdown ever opens once with the old placement before the filter arrives.
    install_wheel_guard(app)
    install_popup_placement(app)

    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    picker = PickerWindow()
    picker.showMaximized()
    result = app.exec()
    _LOGGER.info("application.exit code=%s", result)
    return result

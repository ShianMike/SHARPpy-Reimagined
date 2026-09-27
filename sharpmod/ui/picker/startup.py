"""Picker startup helpers and source context lookup."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from datetime import timezone
from pathlib import Path
from sharpmod.ui.features.gui_common import APP_NAME
from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_common import _configure_debug_logging
from sharpmod.ui.picker.observed_sources import OBSERVED_SOURCES
import os
import subprocess
import sys
from sharpmod.gui_picker import _STABLE_GUI_RUNTIME_ENV


def compose_interactive(*args, **kwargs):
    """Import the heavy sounding-viewer stack only when a viewer is opened.

    Keeping this module-level shim preserves the public/monkeypatch surface
    while allowing the picker itself to reach first paint without importing
    NumPy and the full SHARPpy rendering graph.
    """
    from sharpmod.gui_viewer import compose_interactive as _compose_interactive

    return _compose_interactive(*args, **kwargs)

def _observed_source_label(key: str) -> str:
    """Return the rail label for a source key, or the key itself."""
    for candidate, label, _tooltip in OBSERVED_SOURCES:
        if candidate == key:
            return label
    return str(key)

TAB_OVERLAY_CONTROLLERS = {
    "Station Map": "_map_outlook",
    "Forecast Model": "_model_outlook",
}

TAB_FIELD_CONTROLLERS = {
    "Station Map": "_map_field",
    "Forecast Model": "_model_field",
    # Rebound on every click to the panel that was clicked in, because that tab
    # has two to four fields on screen and the one being pointed at is the one
    # the reader means.
    "Field Panels": "_panels_field",
}

TAB_CONTEXT_CONTROLLERS = {
    "Station Map": "_map_context",
    "Forecast Model": "_model_context",
    "Field Panels": "_panels_context",
}

def _overlay_product_for(owner):
    """Return ``owner``'s selected overlay hazard, tolerating a minimal owner.

    Some entry points are driven with a stand-in object that implements only the
    handful of attributes they touch, so this must not require the full picker
    surface. Resolved through the viewer's own duck-typed reader so both sides
    agree on what "no preference" means.
    """
    from sharpmod.gui_viewer import _controller_overlay_product

    return _controller_overlay_product(owner)

def _locator_spec_for(owner, collection=None):
    """Return ``owner``'s locator selection, tolerating a minimal owner.

    Duck-typed for the same reason :func:`_overlay_product_for` is: several entry
    points are driven with a stand-in that implements only what they touch, and
    demanding the full picker surface here would break them. ``None`` means "not
    stated", which leaves the sounding window's previous behaviour in place
    rather than silently reducing it to a bare inset.
    """
    if collection is not None:
        try:
            from sharpmod.maps.locator_overlay import SELECTION_META_KEY

            stored = collection.getMeta(SELECTION_META_KEY)
        except Exception:
            stored = None
        if isinstance(stored, str):
            return stored
    getter = getattr(owner, "selected_locator_spec", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - a preference is not worth an exception
        return None

def _start_locator_overlay_fetch(*args, **kwargs):
    """Lazily fetch a sounding's locator-inset overlay.

    Needed on the paths that add a collection to an existing viewer rather than
    composing a new one, since only :func:`compose_interactive` starts the fetch
    itself. Without this, a sounding opened in combine mode or restored from a
    session would show no risk on its inset.
    """
    from sharpmod.gui_viewer import start_locator_overlay_fetch

    return start_locator_overlay_fetch(*args, **kwargs)

def _fill_profile_metadata(*args, **kwargs):
    """Lazily normalize metadata before adding to an existing viewer."""
    from sharpmod.gui_viewer import _fill_metadata

    return _fill_metadata(*args, **kwargs)

_MAX_GUI_PYTHON = (3, 14)

def _supported_gui_python(version) -> bool:
    """Return whether ``version`` may run the visible Windows picker."""
    return tuple(version[:2]) < _MAX_GUI_PYTHON

def _venv_python_version(environment_root: Path) -> tuple[int, ...] | None:
    """Return a virtual environment's Python version from ``pyvenv.cfg``.

    Read rather than executed: spawning the candidate to ask for its version
    would have to happen before the relaunch it is deciding on, on the one path
    that is already recovering from a broken runtime. ``None`` means the version
    could not be established, which is treated as unusable rather than assumed
    good.
    """
    try:
        text = (environment_root / "pyvenv.cfg").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key.strip().lower() not in {
            "version",
            "version_info",
        }:
            continue
        parts: list[int] = []
        for piece in value.strip().split("."):
            digits = "".join(char for char in piece if char.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        if len(parts) >= 2:
            return tuple(parts)
    return None

def _show_stable_gui_runtime_required() -> None:
    """Explain why an unsupported Windows source runtime cannot continue."""
    message = (
        "SHARPpy Reimagined cannot safely start its Windows desktop GUI with "
        "Python 3.14. Create this checkout's .venv with Python 3.11-3.13, or "
        "use the packaged Windows release (which includes Python 3.11)."
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:  # noqa: BLE001 - stderr is the non-GUI fallback
        print(message, file=sys.stderr)

def _native_crash_capture():
    """Return an appendable file for the relaunched child's console output.

    The relaunch target is ``pythonw.exe``, which has no console, and the child
    inherits nothing to write to. Anything it emits outside the logging system
    is therefore discarded -- including the interpreter's own report for a
    native crash, which is exactly the failure mode this relaunch exists to
    work around. When that happened the rotating log simply stopped mid
    startup with no exit and no traceback, which is unfalsifiable: it looks
    identical to the user closing the window.

    Capturing the stream to a bounded file beside the rotating log makes the
    next hard crash diagnosable instead. Returns ``None`` if the file cannot be
    opened, because losing diagnostics is far better than refusing to start.
    """
    try:
        path = Path(_configure_debug_logging()).with_name("sharpmod-gui-native.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Trim before handing the handle over rather than mid-write, since the
        # child holds it open for its whole life and cannot roll it over.
        if path.is_file() and path.stat().st_size > 1_000_000:
            path.unlink()
        stream = open(path, "a", encoding="utf-8", errors="replace")
        stream.write(
            "\n=== relaunch %s pid=%d ===\n"
            % (datetime.now(timezone.utc).isoformat(timespec="seconds"), os.getpid())
        )
        stream.flush()
        return stream
    except OSError:
        _LOGGER.warning("application.native_capture_unavailable", exc_info=True)
        return None

def _relaunch_stable_windows_gui(arguments: list[str]) -> bool:
    """Relaunch an unfrozen Python 3.14 Windows GUI with project Python.

    CPython 3.14 can access-violate inside ``python314.dll`` while PySide6 is
    dispatching the visible Windows picker, leaving no catchable traceback.
    The project/release runtime is Python 3.11, so switch before QApplication
    starts rather than allowing a native crash.

    The child is started with ``faulthandler`` enabled and its console output
    captured, so a crash that Python cannot raise as an exception still leaves
    a stack behind. See :func:`_native_crash_capture`.
    """
    if sys.platform != "win32" or _supported_gui_python(sys.version_info):
        return False
    if getattr(sys, "frozen", False):
        return False
    # This flag used to suppress the guard outright. That made a copy of it left
    # behind in the environment -- by an earlier relaunch, a test run, or a
    # shell that exported it once -- disable the only check standing between an
    # unsupported interpreter and a native crash. It is how 3.14 reached
    # QApplication and then access-violated during a worker thread's garbage
    # collection inside pandas, leaving a log that simply stopped.
    #
    # An environment variable cannot overrule sys.version_info: if this
    # interpreter is too new then it is not the stable runtime, whatever the
    # variable claims. The relaunch stays single-shot because
    # _project_gui_runtime only offers an interpreter it has verified is
    # supported, so the child takes the supported-version branch above.
    if os.environ.get(_STABLE_GUI_RUNTIME_ENV) == "1":
        _LOGGER.warning(
            "application.stale_stable_runtime_flag python=%s executable=%s",
            ".".join(str(part) for part in sys.version_info[:3]),
            sys.executable,
        )

    from sharpmod import gui_picker as _picker_api

    runtime = _picker_api._project_gui_runtime()
    if runtime is None:
        _LOGGER.error("application.stable_runtime_missing python=%s", sys.executable)
        _picker_api._show_stable_gui_runtime_required()
        return True

    python, project_root = runtime
    environment = os.environ.copy()
    environment[_STABLE_GUI_RUNTIME_ENV] = "1"
    # Turn an uncatchable native fault into a printed stack. Cheap at runtime
    # and worthless only if nothing ever crashes.
    environment.setdefault("PYTHONFAULTHANDLER", "1")
    command = [str(python), "-m", "sharpmod.gui", *arguments]
    capture = _native_crash_capture()
    try:
        subprocess.Popen(
            command,
            cwd=str(project_root),
            env=environment,
            close_fds=True,
            stdout=capture,
            stderr=subprocess.STDOUT if capture is not None else None,
        )
    except OSError:
        _LOGGER.exception(
            "application.stable_runtime_relaunch_failed runtime=%s", python
        )
        return False
    finally:
        # The child received its own duplicated handle, so this copy is done.
        if capture is not None:
            with suppress(OSError):
                capture.close()

    _LOGGER.info(
        "application.stable_runtime_relaunch source=%s target=%s",
        sys.executable,
        python,
    )
    return True

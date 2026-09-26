"""Viewer Export for the sounding viewer."""

from __future__ import annotations

from pathlib import Path
from qtpy.QtCore import QUrl
from qtpy.QtGui import QAction
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import QApplication
from qtpy.QtWidgets import QDialog
from qtpy.QtWidgets import QFileDialog
from qtpy.QtWidgets import QMessageBox
from sharpmod.export_paths import ExportDirectoryError
from sharpmod.export_paths import export_directory
from sharpmod.export_paths import export_file_path
from sharpmod.ui.features.export_presentation import available_export_path
from sharpmod.ui.features.export_presentation import export_filename
from sharpmod.ui.features.export_presentation import export_identity
from sharpmod.ui.features.export_presentation import load_presentation
from sharpmod.ui.features.export_presentation import recent_exports
from sharpmod.ui.features.export_presentation import remember_export
from sharpmod.ui.features.export_presentation import save_presentation
from sharpmod.ui.features.gui_common import APP_NAME
from sharpmod.ui.features.gui_common import _LOGGER
import re
import weakref
from sharpmod import gui_viewer as _api


def _default_export_basename(prof_col) -> str:
    """Build a friendly export filename stem like ``OUN_2024052000Z``."""
    base = "sounding"
    try:
        loc = prof_col.getMeta("loc") or "sounding"
        run = prof_col.getMeta("run")
        base = f"{loc}_{run:%Y%m%d%H}Z" if run is not None else str(loc)
    except Exception:
        pass
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", base) or "sounding"


def _focused_profile_collection(win, fallback):
    """Return the collection currently focused in a combined viewer."""
    try:
        widget = win.spc_widget
        return widget.prof_collections[int(widget.pc_idx)]
    except (AttributeError, IndexError, TypeError, ValueError):
        return fallback


def _install_export_menu(win, prof_col, controller) -> None:
    """Add an ``Export`` menu (image / text) to a composed sounding window.

    The configured PNG action previews the focused sounding canvas, including
    mounted derived-parameter panels, at independent output dimensions and
    names it by source/time. The text action writes the focused profile as a
    SHARPpy text file that loads back into the app.
    """
    R = _api._render()
    settings = getattr(controller, "_settings", None)
    # Every handler below re-resolves the window from this weakref into a local
    # of the same name, so none of them closes over the outer ``win``. The
    # actions are children of the window, and Qt holds their connections
    # C++-side, so a strong capture would make win -> action -> connection ->
    # closure -> win an uncollectable cycle. That is not merely a leak: the
    # window's Python wrapper then survives to interpreter exit, after Qt has
    # torn the C++ side down, and freeing it is an access violation. Measured on
    # the tip bar, which had the same shape: 6 crashes in 14 runs before, 0 in 14
    # after. See _install_fullscreen_action for the original of this pattern.
    win_ref = weakref.ref(win)
    last_export = [None]

    def _start_path(default_name: str) -> str | None:
        win = win_ref()
        if win is None:
            return None
        try:
            return str(export_file_path(default_name, settings=settings))
        except ExportDirectoryError as exc:
            QMessageBox.critical(win, APP_NAME, str(exc))
            return None

    def _notify(message: str) -> None:
        win = win_ref()
        if win is None:
            return
        try:
            win.statusBar().showMessage(message, 4000)
        except Exception:
            pass

    def export_image(_checked=False) -> None:
        win = win_ref()
        if win is None:
            return
        from sharpmod.ui.features.gui_export import ExportImageDialog

        focused = _api._focused_profile_collection(win, prof_col)
        identity = export_identity(focused)
        name = export_filename(identity, kind="sounding-image", extension="png")
        try:
            start = available_export_path(name, settings=settings)
        except ExportDirectoryError as exc:
            QMessageBox.critical(win, APP_NAME, str(exc))
            return
        def render_preview(choices):
            window = win_ref()
            if window is None:
                raise RuntimeError("The sounding window was closed before preview finished")
            return R.compose_widget_pixmap(
                window.spc_widget,
                choices.width,
                choices.height,
                caption=identity.caption if choices.include_caption else "",
                theme=choices.theme,
            )

        dialog = ExportImageDialog(
            render_preview,
            content=f"Focused sounding canvas · {identity.caption}",
            presentation=load_presentation(settings, "sounding-image"),
            parent=win,
        )
        try:
            accepted = dialog.exec() == QDialog.Accepted
            choices = dialog.presentation if accepted else None
            snapshot = dialog.output_pixmap if accepted else None
        except Exception as exc:  # noqa: BLE001 - preview boundary
            QMessageBox.warning(
                win, APP_NAME,
                f"Could not preview the sounding image: {exc}. "
                "Adjust the output dimensions and retry.",
            )
            return
        finally:
            dialog.release()
        if not accepted:
            return
        if snapshot is None or snapshot.isNull():
            QMessageBox.warning(win, APP_NAME, "No completed image preview is available. Retry the preview before saving.")
            return
        fn, _ok = QFileDialog.getSaveFileName(
            win, "Export Sounding Image", str(start), "PNG image (*.png)"
        )
        if fn:
            if not fn.lower().endswith(".png"):
                fn += ".png"
            try:
                if not R.save_pixmap_png_atomic(snapshot, fn):
                    raise OSError("PNG encoding failed; the previous file was left intact")
                remember_export(
                    settings, fn, kind="PNG", summary=identity.caption,
                    dimensions=choices.size,
                )
            except Exception as exc:  # noqa: BLE001 - export boundary
                QMessageBox.warning(
                    win, APP_NAME,
                    f"Could not complete image export to:\n{fn}\n{exc}\n"
                    "Check the destination and retry. No partial output was published.",
                )
                return
            save_presentation(settings, "sounding-image", choices)
            last_export[0] = str(Path(fn).resolve())
            _notify(f"Saved {choices.width} × {choices.height} PNG: {last_export[0]}")

    def copy_image() -> None:
        win = win_ref()
        if win is None:
            return
        try:
            pixmap = R.grab_widget_pixmap(win.spc_widget)
            QApplication.clipboard().setPixmap(pixmap)
            _notify("Sounding image copied to clipboard")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(win, APP_NAME, f"Could not copy image:\n{exc}")

    def export_text() -> None:
        win = win_ref()
        if win is None:
            return
        focused = _api._focused_profile_collection(win, prof_col)
        base = _api._default_export_basename(focused)
        start = _start_path(base + ".txt")
        if start is None:
            return
        fn, _ok = QFileDialog.getSaveFileName(
            win, "Export Sounding Text (SHARPpy)", start, "SHARPpy text (*.txt)"
        )
        if fn:
            if not fn.lower().endswith(".txt"):
                fn += ".txt"
            try:
                from sharpmod.io.sharppy_export import export_profile_to_sharppy

                export_profile_to_sharppy(win.spc_widget.default_prof, fn)
                _notify(f"Exported SHARPpy text to {fn}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(win, APP_NAME, f"Could not export text:\n{exc}")

    def open_export_folder() -> None:
        win = win_ref()
        if win is None:
            return
        try:
            directory = export_directory(settings=settings)
        except ExportDirectoryError as exc:
            QMessageBox.critical(win, APP_NAME, str(exc))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            QMessageBox.warning(
                win,
                APP_NAME,
                f"The export folder could not be opened:\n{directory}",
            )

    def _last_completed_export():
        if last_export[0] is not None:
            return Path(last_export[0])
        records = recent_exports(settings)
        return Path(records[0].path) if records else None

    def open_last_export() -> None:
        win = win_ref()
        if win is None:
            return
        from sharpmod.ui.features.gui_export import _open_target

        path = _last_completed_export()
        if path is None:
            QMessageBox.information(win, APP_NAME, "No completed export is recorded yet.")
        else:
            _open_target(win, path)

    def copy_last_export_path() -> None:
        win = win_ref()
        if win is None:
            return
        from sharpmod.ui.features.gui_export import copy_export_path

        path = _last_completed_export()
        if path is None:
            QMessageBox.information(win, APP_NAME, "No completed export is recorded yet.")
        else:
            copy_export_path(path)
            _notify(f"Copied export path: {path}")

    def show_recent_exports() -> None:
        win = win_ref()
        if win is None:
            return
        from sharpmod.ui.features.gui_export import RecentExportsDialog

        dialog = RecentExportsDialog(settings, win)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    try:
        menu = win.menuBar().addMenu("Export")
        act_img = QAction("Export Sounding Image (PNG)\u2026", win)
        act_img.setShortcut("Ctrl+E")
        act_img.triggered.connect(export_image)
        menu.addAction(act_img)
        act_copy = QAction("Copy Image to Clipboard", win)
        act_copy.setShortcut("Ctrl+Shift+C")
        act_copy.triggered.connect(copy_image)
        menu.addAction(act_copy)
        act_txt = QAction("Export Text (SHARPpy)\u2026", win)
        act_txt.triggered.connect(export_text)
        menu.addAction(act_txt)
        menu.addSeparator()
        act_open = QAction("Open Export Folder", win)
        act_open.triggered.connect(open_export_folder)
        menu.addAction(act_open)
        act_last = QAction("Open Last Completed Export", win)
        act_last.triggered.connect(open_last_export)
        menu.addAction(act_last)
        act_copy_path = QAction("Copy Last Export Path", win)
        act_copy_path.triggered.connect(copy_last_export_path)
        menu.addAction(act_copy_path)
        act_recent = QAction("Recent Exports\u2026", win)
        act_recent.triggered.connect(show_recent_exports)
        menu.addAction(act_recent)
    except Exception as exc:
        # Never let an export-menu hiccup block the interactive window.
        _LOGGER.exception("export_menu.install_failed")
        _api._record_install_failure(win, "Export menu", exc)

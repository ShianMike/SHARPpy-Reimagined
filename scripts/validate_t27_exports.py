"""Offline real-viewer T27 preview/output captures and byte-level checks.

The second GIF frame is deliberately a labeled validation gap, not a forecast.
No provider requests or application preferences are changed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from qtpy.QtCore import QSettings  # noqa: E402
from qtpy.QtGui import QImage, QPixmap  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

import regenerate_readme_main_gui as captures  # noqa: E402
from sharpmod import gui_picker, gui_viewer, render  # noqa: E402
from sharpmod.animation_exports import (  # noqa: E402
    AnimationFrame,
    animation_frame_preview_png,
    write_animation_gif,
)
from sharpmod.export_paths import export_directory  # noqa: E402
from sharpmod.export_presentation import (  # noqa: E402
    ExportPresentation,
    export_filename,
    export_identity,
)
from sharpmod.gui_export import ExportImageDialog  # noqa: E402
from sharpmod.gui_theme import apply_theme  # noqa: E402


def _save_widget(widget, path, *, width=None):
    widget.ensurePolished()
    if width is not None:
        widget.resize(width, max(widget.height(), widget.sizeHint().height()))
    pixmap = QPixmap(widget.size())
    pixmap.fill()
    widget.render(pixmap)
    if not render.save_pixmap_png_atomic(pixmap, str(path)):
        raise RuntimeError(f"Could not save validation UI capture: {path}")


def capture(text_scale: int):
    app = QApplication.instance() or QApplication([])
    render.install_font(app)
    suffix = f"standard-text{text_scale}"
    destination = export_directory() / "improvement-goal" / "t27"
    destination.mkdir(parents=True, exist_ok=True)
    previous_settings = os.environ.get("SHARPMOD_SETTINGS_PATH")
    with tempfile.TemporaryDirectory(prefix="sharpmod-t27-exports-") as scratch:
        settings_path = Path(scratch) / "settings.ini"
        captures._seed_settings(settings_path, "standard")
        settings = QSettings(str(settings_path), QSettings.IniFormat)
        settings.setValue("interface/text_scale", str(text_scale))
        settings.sync()
        os.environ["SHARPMOD_SETTINGS_PATH"] = str(settings_path)
        picker = viewer = dialog = None
        try:
            apply_theme(app, color_style="standard", text_scale=text_scale)
            with patch.object(gui_picker.PickerWindow, "_refresh_station_catalog", lambda *_a: None), \
                 patch.object(gui_picker.PickerWindow, "_queue_availability", lambda *_a, **_kw: None), \
                 patch.object(gui_picker.PickerWindow, "_queue_model_availability", lambda *_a, **_kw: None), \
                 patch.object(gui_viewer, "start_locator_overlay_fetch", lambda *_a, **_kw: None):
                picker = gui_picker.PickerWindow()
                for name in ("_avail_timer", "_catalog_timer", "_model_availability_timer"):
                    getattr(picker, name).stop()
                collection, station = render.decode(str(captures.SOURCE))
                viewer = picker._show_sounding(collection, station)
                failures = getattr(viewer, "_sharpmod_install_failures", ())
                if failures:
                    raise RuntimeError("; ".join(failures))
                viewer.resize(1440, 900)
                captures._settle(app)
                identity = export_identity(collection)
                choice = ExportPresentation(1600, 1200, "presentation", True, "current")
                dialog = ExportImageDialog(
                    lambda value: render.compose_widget_pixmap(
                        viewer.spc_widget,
                        value.width, value.height,
                        caption=identity.caption if value.include_caption else "",
                        theme=value.theme,
                    ),
                    content=f"Focused sounding canvas · {identity.caption}",
                    presentation=choice,
                    parent=viewer,
                )
                dialog.show()
                captures._settle(app)
                dialog.capture_preview()
                before = viewer.spc_widget.size()
                preview = destination / f"{suffix}-png-preview-dialog.png"
                _save_widget(dialog, preview)
                still = destination / (
                    f"{suffix}-" + export_filename(
                        identity, kind="sounding-image", extension="png"
                    )
                )
                snapshot = dialog.output_pixmap
                if not render.save_pixmap_png_atomic(snapshot, str(still)):
                    raise RuntimeError("Real viewer PNG could not be published")
                saved = QImage(str(still))
                if saved.size() != snapshot.size() or saved != snapshot.toImage():
                    raise RuntimeError("Saved PNG differs from the actual preview composition")
                if viewer.spc_widget.size() != before:
                    raise RuntimeError("Viewer was resized to produce the PNG")
                dialog.close()
                dialog.release()
                dialog = None

                workspace = viewer._sharpmod_analysis_workspace.animation_workspace
                workspace.sizes.set_presentation(
                    ExportPresentation(1600, 1200, "presentation", True, "current")
                )
                workspace.refresh()
                workspace._preview_frame()
                workspace.show()
                captures._settle(app)
                _save_widget(
                    workspace, destination / f"{suffix}-gif-preview-controls.png",
                    width=620,
                )

                real_frame = workspace.capture_animation_frames(
                    specs=workspace._selected_specs()[:1],
                    output_size=(1600, 1200), theme="dark",
                )[0]
                when = real_frame.valid_time
                if not isinstance(when, datetime):
                    when = datetime(2014, 6, 16, 19, tzinfo=timezone.utc)
                missing = AnimationFrame(
                    "Validation gap · no forecast data",
                    None,
                    when + timedelta(hours=3),
                    real_frame.run_time,
                    None,
                    "Validation fixture: no sounding is available at this time",
                )
                gap_preview = destination / f"{suffix}-explicit-gap-preview.png"
                gap_preview.write_bytes(animation_frame_preview_png(
                    missing, mode="forecast-timeline", output_size=(1600, 1200),
                    theme="dark", context=f"{identity.location} · {identity.source}",
                ))
                animation = destination / f"{suffix}-validation-gap-cards.gif"
                write_animation_gif(
                    animation,
                    (real_frame, missing),
                    mode="forecast-timeline", frame_duration_ms=500,
                    output_size=(1600, 1200), gap_policy="cards", theme="dark",
                    context=f"{identity.location} · {identity.source}",
                )
                with Image.open(animation) as image:
                    manifest = json.loads(image.info["comment"].decode("utf-8"))
                    if image.size != (1600, 1200) or image.n_frames != 2:
                        raise RuntimeError("GIF dimensions or frame count differ")
                    if not manifest["missing"][1].startswith("Validation fixture"):
                        raise RuntimeError("GIF validation gap was not retained")
                print(f"{preview} · dialog actual composition")
                print(f"{still} · 1600x1200 · exact preview pixels")
                print(f"{animation} · 1600x1200 · 2 frames, explicit validation gap")
        finally:
            if dialog is not None:
                dialog.close()
                dialog.release()
            if viewer is not None:
                viewer.close()
            if picker is not None:
                picker.close()
            captures._settle(app, 4)
            if previous_settings is None:
                os.environ.pop("SHARPMOD_SETTINGS_PATH", None)
            else:
                os.environ["SHARPMOD_SETTINGS_PATH"] = previous_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-scale", type=int, choices=(100, 200), default=100)
    args = parser.parse_args()
    capture(args.text_scale)


if __name__ == "__main__":
    main()

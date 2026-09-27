"""PNG modes and temporary high-density Qt pixmap allocation."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from qtpy import QtCore, QtGui
from qtpy.QtWidgets import QApplication

from sharpmod.viz import unit_text

PNG_IMAGE_HD = "hd"
PNG_IMAGE_UHD = "uhd"
PNG_IMAGE_LOSSLESS = "lossless"
PNG_IMAGE_MODES = (PNG_IMAGE_HD, PNG_IMAGE_UHD, PNG_IMAGE_LOSSLESS)
_NATIVE_QPIXMAP = QtGui.QPixmap
_PIXMAP_DENSITY_LOCK = threading.RLock()


def _bounded_float_env(name: str, default: float, lower: float,
                       upper: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(upper, value))


def _bounded_int_env(name: str, default: int, lower: int,
                     upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(upper, value))


def _normalise_png_image_mode(mode: str | None) -> str:
    """Return a canonical PNG export mode name."""
    value = (mode or PNG_IMAGE_HD).strip().lower().replace("_", "-")
    aliases = {
        "hires": PNG_IMAGE_HD,
        "hi-res": PNG_IMAGE_HD,
        "high-resolution": PNG_IMAGE_HD,
        "high-definition": PNG_IMAGE_HD,
        "ultra": PNG_IMAGE_UHD,
        "ultrahd": PNG_IMAGE_UHD,
        "ultra-hd": PNG_IMAGE_UHD,
        "ultra-high-definition": PNG_IMAGE_UHD,
        "compact": PNG_IMAGE_LOSSLESS,
        "original": PNG_IMAGE_LOSSLESS,
        "original-size": PNG_IMAGE_LOSSLESS,
    }
    value = aliases.get(value, value)
    if value not in PNG_IMAGE_MODES:
        valid = ", ".join(PNG_IMAGE_MODES)
        raise ValueError(f"unknown PNG image mode {mode!r}; expected {valid}")
    return value


def _png_lossless_compression_quality() -> int:
    """Return Qt's lossless PNG compression setting for exported images.

    For PNG, Qt's ``quality`` argument controls compression effort, not pixel
    dimensions or visual fidelity. ``0`` gives the smallest lossless PNG while
    preserving the same decoded pixels as the legacy ``quality=100`` save.
    """
    return _bounded_int_env("SHARPMOD_PNG_QUALITY", 0, 0, 100)


def _png_hd_image_scale() -> float:
    """Return the HD export pixel scale.

    A 2x render roughly moves the current 370-400 KB chart exports into the
    requested 1-2 MB range while improving text/line density. Clamp the env
    override so accidental values do not create enormous offscreen pixmaps.
    """
    return _bounded_float_env("SHARPMOD_HD_SCALE", 2.0, 1.0, 4.0)


def _png_hd_compression_quality() -> int:
    """Return Qt's PNG compression setting for HD exports."""
    return _bounded_int_env("SHARPMOD_HD_PNG_QUALITY", 0, 0, 100)


def _png_uhd_image_scale() -> float:
    """Return the UHD export pixel scale."""
    return _bounded_float_env("SHARPMOD_UHD_SCALE", 2.8, 1.0, 4.0)


def _png_uhd_compression_quality() -> int:
    """Return Qt's PNG compression setting for UHD exports."""
    return _bounded_int_env("SHARPMOD_UHD_PNG_QUALITY", 0, 0, 100)


def _png_image_scale(image_mode: str | None) -> float:
    """Return the target pixel-density multiplier for one export mode."""
    mode = _normalise_png_image_mode(image_mode)
    if mode == PNG_IMAGE_UHD:
        return _png_uhd_image_scale()
    if mode == PNG_IMAGE_HD:
        return _png_hd_image_scale()
    return 1.0


def _make_density_pixmap_type(scale: float):
    """Build a QPixmap type with high-density storage and logical geometry.

    SHARPpy's scientific widgets paint into persistent ``plotBitMap`` caches.
    Those caches are created with logical widget dimensions and then blitted by
    each widget's paint event.  During HD/UHD capture, merely scaling the final
    painter enlarges those already-rasterized caches, including their text.

    This type keeps the geometry contract expected by the vendored widgets
    while allocating ``scale`` times as many physical pixels.  Qt's device
    pixel ratio then maps their unchanged logical paint coordinates onto the
    denser storage.  Copy rectangles need the same logical-to-physical mapping
    because the skew-T and hodograph keep secondary bitmap copies.
    """
    density = max(1.0, float(scale))
    native_pixmap = _NATIVE_QPIXMAP

    class DensityPixmap(native_pixmap):
        _sharpmod_density_scale = density

        def __init__(self, *args, **kwargs):
            scaled = False
            if not kwargs and len(args) == 2 and all(
                    isinstance(value, int) for value in args):
                super().__init__(
                    max(0, int(round(args[0] * density))),
                    max(0, int(round(args[1] * density))),
                )
                scaled = True
            elif not kwargs and len(args) == 1 \
                    and isinstance(args[0], QtCore.QSize):
                size = args[0]
                super().__init__(
                    max(0, int(round(size.width() * density))),
                    max(0, int(round(size.height() * density))),
                )
                scaled = True
            else:
                # Empty, filename, and QPixmap-copy constructors retain their
                # normal Qt behavior.  The copy case also preserves its DPR.
                super().__init__(*args, **kwargs)
            if scaled:
                self.setDevicePixelRatio(density)

        def width(self):
            raw = super().width()
            dpr = self.devicePixelRatioF()
            return int(round(raw / dpr)) if dpr > 1.0 else raw

        def height(self):
            raw = super().height()
            dpr = self.devicePixelRatioF()
            return int(round(raw / dpr)) if dpr > 1.0 else raw

        def size(self):
            return QtCore.QSize(self.width(), self.height())

        def rect(self):
            return QtCore.QRect(0, 0, self.width(), self.height())

        @staticmethod
        def _map_copy_axis(start, length, dpr, physical_extent):
            physical_start = int(round(start * dpr))
            if length < 0:
                # Resolve Qt's -1 "through the edge" sentinel explicitly.
                # Passing -1 with physical x/y through PySide can be treated
                # as a null QRect and copy the entire pixmap instead.
                return physical_start, max(
                    0, int(physical_extent) - physical_start)
            physical_end = int(round((start + length) * dpr))
            return physical_start, max(0, physical_end - physical_start)

        def copy(self, *args):
            dpr = self.devicePixelRatioF()
            mapped = args
            if dpr > 1.0 and args:
                if len(args) == 1 and isinstance(args[0], QtCore.QRect):
                    rect = args[0]
                    x, width = self._map_copy_axis(
                        rect.x(), rect.width(), dpr, super().width())
                    y, height = self._map_copy_axis(
                        rect.y(), rect.height(), dpr, super().height())
                    mapped = (QtCore.QRect(x, y, width, height),)
                elif len(args) == 4:
                    x, width = self._map_copy_axis(
                        args[0], args[2], dpr, super().width())
                    y, height = self._map_copy_axis(
                        args[1], args[3], dpr, super().height())
                    mapped = (x, y, width, height)

            # The binding returns the native base type.  Re-wrap it so copied
            # caches keep logical geometry and scaled-copy behavior.
            return DensityPixmap(super().copy(*mapped))

    DensityPixmap.__name__ = (
        f"_DensityPixmap_{str(density).replace('.', '_')}")
    return DensityPixmap


@contextmanager
def _target_density_pixmaps(scale: float):
    """Create new render-window bitmap caches at ``scale`` pixel density.

    ``QtGui.QPixmap`` is process-global, so the temporary substitution is
    serialized, restricted to Qt's GUI thread, and restored even if window
    composition or export fails.  The context must begin before composing the
    offscreen window; changing an already-built tree leaves stale 1x caches.
    """
    density = max(1.0, float(scale))
    if density <= 1.0:
        # Original-size export: leave font hinting at Qt's default, which
        # measured crisper than vertical-only hinting at 1x.
        yield _NATIVE_QPIXMAP
        return

    app = QApplication.instance()
    if app is None:
        raise RuntimeError(
            "target-density pixmaps require an active QApplication")
    if QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError(
            "target-density pixmaps must be created on the Qt GUI thread")

    with _PIXMAP_DENSITY_LOCK:
        if QtGui.QPixmap is not _NATIVE_QPIXMAP:
            raise RuntimeError(
                "QtGui.QPixmap is already temporarily overridden")
        QtGui.QPixmap = _make_density_pixmap_type(density)
        # Glyphs painted into these density caches are rasterised through a
        # scaled transform, where vertical-only hinting measured crisper.  Every
        # font is created after this point, so the flag is set before the tree.
        previous_scaled = unit_text.set_scaled_export(True)
        try:
            yield _NATIVE_QPIXMAP
        finally:
            unit_text.set_scaled_export(previous_scaled)
            QtGui.QPixmap = _NATIVE_QPIXMAP

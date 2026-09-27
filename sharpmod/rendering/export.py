"""High-density Qt widget composition and atomic PNG writing."""

from __future__ import annotations

import inspect
import logging
import os
import tempfile
from contextlib import contextmanager

from qtpy import QtCore, QtGui, QtWidgets

from sharpmod.rendering.density import (
    PNG_IMAGE_HD, PNG_IMAGE_UHD, _NATIVE_QPIXMAP, _normalise_png_image_mode,
    _png_hd_compression_quality, _png_image_scale,
    _png_lossless_compression_quality, _png_uhd_compression_quality,
    _target_density_pixmaps,
)

_LOGGER = logging.getLogger(__name__)


def grab_widget_pixmap(widget, scale: float = 1.0):
    """Grab ``widget`` as a pixmap, optionally rendered at higher pixel scale.

    ``scale=1`` intentionally mirrors upstream SHARPpy's ``SPCWidget`` grab so
    compact/lossless exports keep the original widget shape and dimensions.
    Higher scales render through a scaled painter instead of resizing a final
    screenshot.  The headless :func:`render` path additionally composes its
    scientific-panel caches inside :func:`_target_density_pixmaps`, so cached
    text, barbs, and grid lines are rasterized at the final HD/UHD density.
    """
    scale = max(1.0, float(scale))
    if scale <= 1.0:
        return widget.grab()

    size = widget.size()
    width = max(1, int(round(size.width() * scale)))
    height = max(1, int(round(size.height() * scale)))
    # The destination is already specified in physical output pixels.  Bypass
    # the temporary density-aware cache constructor or it would be scaled a
    # second time.
    pixmap = _NATIVE_QPIXMAP(width, height)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))

    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.scale(scale, scale)
        widget.render(painter, QtCore.QPoint(0, 0))
    finally:
        painter.end()
    return pixmap


def _zero_arg_method(widget, name):
    """Return ``widget.name`` only if it is callable with no arguments.

    The vendored panels are not uniform: most paint into their cache from
    ``plotData()``, but a few take the live widget painter instead
    (``plotAdvection.plotData(qp)`` draws its data straight onto the widget and
    keeps only the background in its cache). Those must not be invoked here --
    and they do not need to be, since anything painted live is already
    rasterized at the export density.
    """
    method = getattr(widget, name, None)
    if not callable(method):
        return None
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):  # pragma: no cover - C-implemented slot
        return method
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.default is parameter.empty:
            return None
    return method


def _panel_background_colour(widget):
    """Return the fill a scientific panel expects behind its background pass."""
    for name in ("bg_color", "bg", "backgroundColor"):
        value = getattr(widget, name, None)
        if isinstance(value, QtGui.QColor) and value.isValid():
            return value
        if isinstance(value, str) and value:
            colour = QtGui.QColor(value)
            if colour.isValid():
                return colour
    return QtGui.QColor(0, 0, 0)


def _cached_panels(root):
    """Yield every widget in ``root``'s tree that keeps a ``plotBitMap`` cache."""
    candidates = [root]
    try:
        candidates.extend(root.findChildren(QtWidgets.QWidget))
    except (AttributeError, RuntimeError):
        pass
    for widget in candidates:
        bitmap = getattr(widget, "plotBitMap", None)
        if bitmap is None:
            continue
        try:
            if bitmap.isNull():
                continue
        except (AttributeError, RuntimeError):
            continue
        yield widget


@contextmanager
def _panels_at_target_density(root, scale: float):
    """Re-rasterize a live widget tree's panel caches at ``scale`` density.

    Every scientific panel paints into a persistent ``plotBitMap`` and blits it
    from ``paintEvent``. The headless renderer gets those caches at the export
    density for free, because it composes the whole window inside
    :func:`_target_density_pixmaps`. An interactive window is composed at 1x, so
    capturing it through a scaled painter enlarges caches that were already
    rasterized -- text included -- which is what makes an on-screen export look
    softer than the command-line one at the same pixel dimensions.

    Rebuilding the caches here closes that gap for the live window. Only the
    cache is replaced: the widgets' own ``initUI`` is deliberately *not* re-run,
    because it recomputes fonts and geometry and would discard the hodograph
    centring and skew-T zoom the user has set. Each panel's existing cache
    supplies the logical size it chose for itself, and the originals are put
    back afterwards so the window on screen is left exactly as it was.
    """
    density = max(1.0, float(scale))
    if density <= 1.0:
        yield 0
        return

    saved: list[tuple] = []
    rebuilt = 0
    try:
        with _target_density_pixmaps(density):
            for panel in _cached_panels(root):
                original = panel.plotBitMap
                original_background = getattr(panel, "backgroundBitMap", None)
                try:
                    replacement = QtGui.QPixmap(original.size())
                    replacement.fill(_panel_background_colour(panel))
                    panel.plotBitMap = replacement
                    saved.append((panel, original, original_background))

                    # Order mirrors the widgets' own initialisation: the
                    # background pass paints into the fresh cache, the background
                    # snapshot is taken from it, and the data pass draws on top.
                    #
                    # ``clearData`` is deliberately not called. It is a reset for
                    # when the profile changes, not a step in the draw sequence,
                    # and most panels here have no ``backgroundBitMap`` for it to
                    # restore from -- it simply allocates a blank cache. Calling
                    # it between the two passes therefore discarded everything
                    # the background pass had just drawn, and the data pass put
                    # back only the data: axes, tick labels, titles, and legends
                    # were silently missing from HD and UHD exports.
                    plot_background = _zero_arg_method(panel, "plotBackground")
                    if plot_background is not None:
                        plot_background()
                    if original_background is not None:
                        panel.backgroundBitMap = panel.plotBitMap.copy()
                    plot_data = _zero_arg_method(panel, "plotData")
                    if plot_data is not None:
                        plot_data()
                    rebuilt += 1
                except Exception:  # noqa: BLE001 - one panel must not fail all
                    _LOGGER.exception(
                        "export.panel_density_failed panel=%s",
                        type(panel).__name__)
            yield rebuilt
    finally:
        # Restore outside the density context so the live window goes back to
        # its 1x caches even if the capture raised.
        for panel, original, original_background in saved:
            try:
                panel.plotBitMap = original
                if original_background is not None:
                    panel.backgroundBitMap = original_background
            except (AttributeError, RuntimeError):
                continue


def _composition_colours(widget, theme: str):
    """Resolve the margin/caption colours without changing the live palette."""
    requested = str(theme or "current").strip().lower()
    if requested == "light":
        return QtGui.QColor("#f4f1eb"), QtGui.QColor("#ffffff"), QtGui.QColor("#1a1815")
    if requested == "dark":
        return QtGui.QColor("#0d0d0c"), QtGui.QColor("#1f1e1b"), QtGui.QColor("#f1efeb")
    try:
        palette = QtWidgets.QApplication.palette()
        background = palette.color(QtGui.QPalette.Window)
        foreground = palette.color(QtGui.QPalette.WindowText)
        caption = palette.color(QtGui.QPalette.Base)
        if not background.isValid() or not foreground.isValid() or not caption.isValid():
            raise ValueError("invalid application palette")
        return background, caption, foreground
    except Exception:
        return QtGui.QColor("#0d0d0c"), QtGui.QColor("#1f1e1b"), QtGui.QColor("#f1efeb")


def compose_widget_pixmap(
    widget,
    width: int,
    height: int,
    *,
    caption: str = "",
    theme: str = "current",
):
    """Compose ``widget`` into an exact-size canvas without resizing it.

    The same returned pixmap can be scaled into a preview and atomically saved
    as the final output, making preview/final composition agreement explicit.
    The live widget geometry and its persistent panel caches are restored before
    this function returns.
    """
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError) as exc:
        raise ValueError("export dimensions must be integer pixels") from exc
    if not 1 <= width <= 16_384 or not 1 <= height <= 16_384:
        raise ValueError("export dimensions must be between 1 and 16384 pixels")
    logical_width = max(1, int(widget.width()))
    logical_height = max(1, int(widget.height()))
    caption_text = str(caption or "").strip()
    caption_height = (
        max(40, min(72, int(round(height * 0.065)))) if caption_text else 0
    )
    content_height = max(1, height - caption_height)
    scale = min(width / logical_width, content_height / logical_height)
    rendered_width = max(1, int(round(logical_width * scale)))
    rendered_height = max(1, int(round(logical_height * scale)))
    offset_x = (width - rendered_width) // 2
    offset_y = (content_height - rendered_height) // 2
    background, caption_background, foreground = _composition_colours(widget, theme)

    def _compose():
        canvas = _NATIVE_QPIXMAP(width, height)
        canvas.fill(background)
        painter = QtGui.QPainter(canvas)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.save()
            painter.translate(offset_x, offset_y)
            painter.scale(scale, scale)
            widget.render(painter, QtCore.QPoint(0, 0))
            painter.restore()
            if caption_height:
                painter.fillRect(
                    QtCore.QRect(0, content_height, width, caption_height),
                    caption_background,
                )
                font = painter.font()
                font.setPixelSize(max(12, min(24, int(round(caption_height * 0.34)))))
                painter.setFont(font)
                painter.setPen(foreground)
                metrics = QtGui.QFontMetrics(font)
                visible = metrics.elidedText(
                    caption_text,
                    QtCore.Qt.ElideRight,
                    max(1, width - 32),
                )
                painter.drawText(
                    QtCore.QRect(16, content_height, max(1, width - 32), caption_height),
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                    visible,
                )
        finally:
            painter.end()
        return canvas

    already_dense = QtGui.QPixmap is not _NATIVE_QPIXMAP
    if scale > 1.0 and not already_dense:
        try:
            with _panels_at_target_density(widget, scale):
                return _compose()
        except RuntimeError:
            _LOGGER.debug("export.composition_density_unavailable", exc_info=True)
    return _compose()


def save_pixmap_png_atomic(pixmap, outfile: str, *, quality: int = 0) -> bool:
    """Save a complete PNG beside its destination and publish by replacement."""
    destination = os.path.abspath(os.fspath(outfile))
    parent = os.path.dirname(destination) or os.curdir
    os.makedirs(parent, exist_ok=True)
    handle, candidate = tempfile.mkstemp(
        prefix=f".{os.path.basename(destination)}.", suffix=".ready.png", dir=parent
    )
    os.close(handle)
    try:
        if pixmap is None or pixmap.isNull() or not pixmap.save(candidate, "PNG", quality):
            return False
        with open(candidate, "rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                return False
        if os.path.getsize(candidate) <= 8:
            return False
        os.replace(candidate, destination)
        candidate = ""
        return True
    finally:
        if candidate:
            try:
                os.unlink(candidate)
            except OSError:
                pass


def save_widget_png(
    widget,
    outfile: str,
    image_mode: str = PNG_IMAGE_HD,
    *,
    width: int | None = None,
    height: int | None = None,
    caption: str = "",
    theme: str = "current",
) -> bool:
    """Save ``widget`` as HD, UHD, or original-size lossless PNG.

    When the caller has not already composed the tree at the export density --
    which is the interactive viewer's case -- the panel caches are re-rasterized
    at that density first, so an on-screen export is as crisp as the equivalent
    ``sharpmod-render`` output rather than a smooth enlargement of 1x text.
    """
    mode = _normalise_png_image_mode(image_mode)
    scale = _png_image_scale(mode)
    if mode == PNG_IMAGE_UHD:
        quality = _png_uhd_compression_quality()
    elif mode == PNG_IMAGE_HD:
        quality = _png_hd_compression_quality()
    else:
        quality = _png_lossless_compression_quality()

    if (width is None) != (height is None):
        raise ValueError("custom PNG export requires both width and height")
    if width is not None:
        pixmap = compose_widget_pixmap(
            widget,
            width,
            height,
            caption=caption,
            theme=theme,
        )
    else:
        already_dense = QtGui.QPixmap is not _NATIVE_QPIXMAP
        if scale > 1.0 and not already_dense:
            try:
                with _panels_at_target_density(widget, scale):
                    pixmap = grab_widget_pixmap(widget, scale=scale)
            except RuntimeError:
                # No QApplication, wrong thread, or a nested override: the export is
                # still worth producing, just without the density rebuild.
                _LOGGER.debug("export.density_rebuild_unavailable", exc_info=True)
                pixmap = grab_widget_pixmap(widget, scale=scale)
        else:
            pixmap = grab_widget_pixmap(widget, scale=scale)
    return save_pixmap_png_atomic(pixmap, outfile, quality=quality)

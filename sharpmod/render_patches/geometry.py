"""Text and annotation geometry shared by Skew-T and inset patches."""

from __future__ import annotations

import os

# Maximum point size for the conditional tornado / VROT probability insets.
# Their vendored draw methods size text from widget height and place labels in
# very narrow rects, so the custom font can clip the title/legend/x labels.
COND_PROB_LABEL_MAX_PT = int(os.environ.get("COND_PROB_LABEL_MAX_PT", "10"))


def _fit_rect_to_skewt_plot(widget, qtcore, rect, pad=2):
    """Return ``rect`` shifted/shrunk so it stays inside the skew-T plot box."""
    import sharpmod_rs

    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad

    if right_limit <= left_limit or bottom_limit <= top_limit:
        return rect

    left, top, width, height = sharpmod_rs.skewt_fit_rect(
        float(rect.x()),
        float(rect.y()),
        float(rect.width()),
        float(rect.height()),
        left_limit,
        right_limit,
        top_limit,
        bottom_limit,
    )
    return qtcore.QRectF(left, top, width, height)


def _skewt_surface_label_rect(widget, qtcore, center_x, line_y, width, height,
                              below_offset=4, pad=2):
    """Place a near-surface label below its line, or above it if needed."""
    import sharpmod_rs

    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", line_y))) - pad

    if right_limit <= left_limit or bottom_limit <= top_limit:
        left = float(center_x) - float(width) / 2.0
        top = float(line_y) + below_offset
        if top + float(height) > bottom_limit:
            top = float(line_y) - below_offset - float(height)
        return qtcore.QRectF(left, top, float(width), float(height))

    left, top, out_w, out_h = sharpmod_rs.skewt_surface_label_rect(
        float(center_x),
        float(line_y),
        float(width),
        float(height),
        left_limit,
        right_limit,
        top_limit,
        bottom_limit,
        below_offset=float(below_offset),
    )
    return qtcore.QRectF(left, top, out_w, out_h)


def _layout_skewt_surface_labels(widget, qtcore, labels, *, gap=4, pad=2):
    """Pack surface-value labels into bounded, non-overlapping rows.

    ``labels`` contains dictionaries with ``center_x``, ``line_y``, ``width``
    and ``height``.  The returned rectangles follow the input order.  Anchors
    are sorted only for layout, preserving their meteorological left-to-right
    order while a forward/backward pass opens at least ``gap`` logical pixels
    between adjacent masks.  A narrow plot automatically creates extra rows.
    """
    if not labels:
        return []

    import sharpmod_rs

    gap = max(0.0, float(gap))
    pad = max(0.0, float(pad))
    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad
    label_specs = [
        (
            float(label["center_x"]),
            float(label["line_y"]),
            float(label["width"]),
            float(label["height"]),
        )
        for label in labels
    ]
    rects = sharpmod_rs.skewt_layout_surface_labels(
        label_specs, left_limit, right_limit, top_limit, bottom_limit, gap=gap
    )
    return [qtcore.QRectF(x, y, w, h) for x, y, w, h in rects]


def _upsert_skewt_surface_label(queue, dedupe_key, entry):
    """Keep the final draw pass for each displayed surface trace."""
    entry["dedupe_key"] = dedupe_key
    for index, queued in enumerate(queue):
        if queued.get("dedupe_key") == dedupe_key:
            queue[index] = entry
            return
    queue.append(entry)


def _font_metrics_advance(metrics, text):
    advance = getattr(metrics, "horizontalAdvance", None)
    if advance is None:
        advance = metrics.width
    return advance(str(text))


def _fit_font_to_rect(qtgui, base_font, text, max_width, max_height,
                      *, max_pt=COND_PROB_LABEL_MAX_PT, min_pt=6):
    """Return a copy of ``base_font`` small enough for ``text`` to fit."""
    font = qtgui.QFont(base_font)
    pt = font.pointSizeF()
    if pt <= 0:
        px = font.pixelSize()
        pt = float(px) if px > 0 else float(max_pt)
    pt = min(float(pt), float(max_pt))

    while pt > float(min_pt):
        font.setPointSizeF(pt)
        metrics = qtgui.QFontMetrics(font)
        if (metrics.height() <= max_height and
                _font_metrics_advance(metrics, text) <= max_width):
            return font
        pt -= 0.5

    font.setPointSizeF(max(float(min_pt), 1.0))
    return font


def _centered_rect_in_bounds(qtcore, center_x, top, width, height,
                             left_limit, right_limit):
    import sharpmod_rs

    left, top_out, out_w, out_h = sharpmod_rs.skewt_centered_rect(
        float(center_x),
        float(top),
        float(width),
        float(height),
        float(left_limit),
        float(right_limit),
    )
    return qtcore.QRectF(left, top_out, out_w, out_h)


def _draw_fitted_text(qp, qtcore, qtgui, rect, text, font, color,
                      flags=None, *, max_pt=COND_PROB_LABEL_MAX_PT,
                      min_pt=6, elide=False):
    """Draw text inside rect after fitting the font and optionally eliding."""
    if flags is None:
        flags = qtcore.Qt.AlignCenter
    fitted = _fit_font_to_rect(
        qtgui, font, text, max(1.0, rect.width() - 2),
        max(1.0, rect.height() - 1), max_pt=max_pt, min_pt=min_pt)
    qp.setFont(fitted)
    qp.setPen(qtgui.QPen(color, 1, qtcore.Qt.SolidLine))
    draw_text = str(text)
    if elide:
        metrics = qtgui.QFontMetrics(fitted)
        draw_text = metrics.elidedText(
            draw_text, qtcore.Qt.ElideRight, max(1, int(rect.width()) - 2))
    qp.drawText(rect, flags, draw_text)

"""Text and annotation geometry shared by Skew-T and inset patches."""

from __future__ import annotations

import os

# Maximum point size for the conditional tornado / VROT probability insets.
# Their vendored draw methods size text from widget height and place labels in
# very narrow rects, so the custom font can clip the title/legend/x labels.
COND_PROB_LABEL_MAX_PT = int(os.environ.get("COND_PROB_LABEL_MAX_PT", "10"))


def _fit_rect_to_skewt_plot(widget, qtcore, rect, pad=2):
    """Return ``rect`` shifted/shrunk so it stays inside the skew-T plot box."""
    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad

    if right_limit <= left_limit or bottom_limit <= top_limit:
        return rect

    width = min(float(rect.width()), max(1.0, right_limit - left_limit))
    height = min(float(rect.height()), max(1.0, bottom_limit - top_limit))
    max_left = right_limit - width
    max_top = bottom_limit - height
    left = min(max(float(rect.x()), left_limit), max_left)
    top = min(max(float(rect.y()), top_limit), max_top)
    return qtcore.QRectF(left, top, width, height)


def _skewt_surface_label_rect(widget, qtcore, center_x, line_y, width, height,
                              below_offset=4, pad=2):
    """Place a near-surface label below its line, or above it if needed."""
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", line_y))) - pad
    left = float(center_x) - float(width) / 2.0
    top = float(line_y) + below_offset
    if top + float(height) > bottom_limit:
        top = float(line_y) - below_offset - float(height)
    rect = qtcore.QRectF(left, top, float(width), float(height))
    return _fit_rect_to_skewt_plot(widget, qtcore, rect, pad=pad)


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

    gap = max(0.0, float(gap))
    pad = max(0.0, float(pad))
    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad
    available = max(1.0, right_limit - left_limit)

    prepared = []
    for index, label in enumerate(labels):
        width = min(max(1.0, float(label["width"])), available)
        height = min(
            max(1.0, float(label["height"])),
            max(1.0, bottom_limit - top_limit),
        )
        preferred = _skewt_surface_label_rect(
            widget,
            qtcore,
            float(label["center_x"]),
            float(label["line_y"]),
            width,
            height,
            pad=pad,
        )
        prepared.append({
            "index": index,
            "center_x": float(label["center_x"]),
            "line_y": float(label["line_y"]),
            "width": width,
            "height": height,
            "preferred": preferred,
        })
    prepared.sort(key=lambda item: (item["center_x"], item["index"]))

    # Split only when the complete label cluster cannot fit in one row.
    rows = []
    row = []
    used = 0.0
    for item in prepared:
        extra = item["width"] + (gap if row else 0.0)
        if row and used + extra > available:
            rows.append(row)
            row = []
            used = 0.0
            extra = item["width"]
        row.append(item)
        used += extra
    if row:
        rows.append(row)

    result = [None] * len(labels)
    primary_above = sum(
        item["preferred"].center().y() < item["line_y"]
        for item in prepared
    ) >= (len(prepared) / 2.0)
    direction = -1.0 if primary_above else 1.0
    base_top = min(
        float(item["preferred"].top()) for item in prepared)
    placed_rows = []

    def _row_fits(top, height):
        if top < top_limit or top + height > bottom_limit:
            return False
        return all(
            top + height + gap <= other_top
            or top >= other_top + other_height + gap
            for other_top, other_height in placed_rows
        )

    for row_index, current in enumerate(rows):
        row_height = max(item["height"] for item in current)
        if row_index == 0:
            row_top = min(
                max(base_top, top_limit),
                max(top_limit, bottom_limit - row_height),
            )
        else:
            highest_top = min(top for top, _height in placed_rows)
            lowest_bottom = max(
                top + height for top, height in placed_rows)
            above = highest_top - gap - row_height
            below = lowest_bottom + gap
            candidates = (
                (above, below) if direction < 0 else (below, above)
            )
            row_top = next((
                candidate for candidate in candidates
                if _row_fits(candidate, row_height)
            ), None)

            if row_top is None:
                # Search every remaining bounded vertical slot, nearest the
                # preferred surface-label row first. This handles a first row
                # that fits below its anchor but leaves room only above.
                slot_candidates = [top_limit]
                for other_top, other_height in placed_rows:
                    slot_candidates.extend((
                        other_top - gap - row_height,
                        other_top + other_height + gap,
                    ))
                slot_candidates.sort(
                    key=lambda candidate: abs(candidate - base_top))
                row_top = next((
                    candidate for candidate in slot_candidates
                    if _row_fits(candidate, row_height)
                ), None)

            if row_top is None:
                # The available vertical area is mathematically too small for
                # another row. Keep the rectangle bounded and minimize overlap
                # instead of collapsing every remaining row onto one edge.
                bounded = [
                    min(
                        max(candidate, top_limit),
                        max(top_limit, bottom_limit - row_height),
                    )
                    for candidate in (above, below, base_top)
                ]

                def overlap_cost(candidate):
                    total = 0.0
                    for other_top, other_height in placed_rows:
                        overlap = min(
                            candidate + row_height,
                            other_top + other_height,
                        ) - max(candidate, other_top)
                        total += max(0.0, overlap + gap)
                    return total

                row_top = min(
                    bounded,
                    key=lambda candidate: (
                        overlap_cost(candidate),
                        abs(candidate - base_top),
                    ),
                )
        placed_rows.append((row_top, row_height))

        desired = [
            min(
                max(float(item["preferred"].left()), left_limit),
                right_limit - item["width"],
            )
            for item in current
        ]
        lefts = []
        cursor = left_limit
        for item, wanted in zip(current, desired):
            left = max(wanted, cursor)
            lefts.append(left)
            cursor = left + item["width"] + gap

        # Project back from the right edge, then forward once more. Since rows
        # were split by total width, these two passes always have a feasible
        # non-overlapping solution.
        cursor = right_limit
        for index in range(len(current) - 1, -1, -1):
            item = current[index]
            lefts[index] = min(lefts[index], cursor - item["width"])
            cursor = lefts[index] - gap
        cursor = left_limit
        for index, item in enumerate(current):
            lefts[index] = max(lefts[index], cursor)
            cursor = lefts[index] + item["width"] + gap

        for item, left in zip(current, lefts):
            top = row_top
            if direction < 0:
                # Bottom-align mixed font heights within an upward row.
                top += row_height - item["height"]
            result[item["index"]] = qtcore.QRectF(
                left, top, item["width"], item["height"])

    return result


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
    width = min(float(width), max(1.0, float(right_limit) - float(left_limit)))
    left = float(center_x) - width / 2.0
    max_left = float(right_limit) - width
    left = min(max(left, float(left_limit)), max_left)
    return qtcore.QRectF(left, float(top), width, float(height))


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

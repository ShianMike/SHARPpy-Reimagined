"""Paint time-aware overlay legends and colour bars."""

from __future__ import annotations

import math

from qtpy import QtGui
from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPen, QPolygonF

from sharpmod.ui.features.gui_theme import mono_font, ui_font
from sharpmod.maps.map_overlays import format_age
from sharpmod.ui.maps.hatching import hatch_brush
from sharpmod.ui.maps.presentation import _map


from sharpmod.ui.maps.legend import (
    COLOUR_BAR_H, COLOUR_BAR_HEADER_H, COLOUR_BAR_TICK_H,
    LEGEND_BLOCK_GAP, LEGEND_LINE_H, LEGEND_SWATCH_H,
)


def draw_overlay_legend(self, qp) -> None:
    """Draw the overlay title, validity, and category swatches.

    T20: the legend adapts -- corner placement avoids the sounding point,
    permanent readout, and inspection card; a collapsed or corner-less layout
    keeps the one-line identity (name · units · scale); categorical rows
    wrap past eight; the continuous bar carries a fixed/auto scale line
    with explicit change notes, a hover marker, and native resolution
    context. Bottom-right leads (the scale bar yields to it), bottom-left
    stays the fallback, because the coordinate readout owns the top-left
    corner and the pinned card owns the top-right.
    The validity line is the visible half of "time aware": it states the
    window the product covers. Extra prose is reserved for a mismatch or a
    selected time outside that window.
    T21: the first caption is the time header (requested vs actual, run and
    lead when pinned), and each raster/vector row carries its match state
    (exact / nearest ±offset / outside / unavailable) from the same
    attached payloads the T19 list rows read.
    """
    from sharpmod.maps.map_time import layer_time_match

    layers = self._visible_overlays()
    rasters = self._visible_rasters()
    profile_key = self.profile_marker_key()
    profile_rows = [
        (label, stroke, fill, 0, marker_shape)
        for label, stroke, fill, marker_shape in profile_key
    ]
    if not layers and not rasters and not profile_rows:
        self._legend_bar = None
        self._legend_bar_range = None
        self._legend_clip = None
        return

    captions: list[str] = []
    credits: list[str] = []
    # Field panels paint their moving value marker in the lightweight cursor
    # child. Sampling it here would redraw every static legend element on each
    # hover even though the underlying map frame is otherwise unchanged.
    hover_value = (
        None if getattr(self, "_crosshair_overlay", None) is not None
        else self._legend_hover_value()
    )
    # T21: no header/mode lines here -- the time chip paints them above
    # the map, and repeating all three facts as captions cost the legend
    # two of its scarcest rows (the crop showed header, mode, subtitle,
    # and scale line naming the same run/hour four times over).
    # T20.2: the field title already names the product above the bar
    # (drawn below), so repeating it as the first caption line wastes the
    # scarcest legend space and pushes the prose off narrow maps. The
    # time chip above the map carries the actual run · hour · valid, so
    # the subtitle is dropped with it (see below).
    seen_field_title = False
    for layer in layers:
        captions.append(layer.title)
        if layer.subtitle:
            captions.append(layer.subtitle)
        # T21.2 match state beside the coverage wording it refines: exact /
        # nearest ±offset / outside / unavailable from the layer's own
        # window, using the product's own tolerance.
        key = str(getattr(layer, "key", "") or "")
        try:
            state, detail = layer_time_match(
                layer_key=key or "spc_outlook", requested=self._valid_time,
                valid_from=getattr(layer, "valid_from", None),
                valid_to=getattr(layer, "valid_to", None),
                available=bool(layer))
            if state != "exact" and detail:
                marker = "⚠ " if state in ("outside", "unavailable") else ""
                captions.append(f"{marker}Time match: {detail}")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        # The subtitle already states the positive coverage window. Repeat
        # the selected time only when it falls outside that window, where
        # the relationship is an actionable warning rather than reassurance.
        if self._valid_time is not None:
            relationship = {
                "spc_outlook": "this outlook",
                "storm_reports": "this report window",
                "surface_observations": "these surface observations",
            }.get(key, "this layer")
            if not layer.covers(self._valid_time):
                captions.append(
                    f"\u26a0 Selected {self._valid_time:%d %b %H%M}Z is "
                    f"outside {relationship}"
                )
        credit = getattr(layer, "attribution", "")
        if credit and credit not in credits:
            credits.append(credit)

    profiles = getattr(self, "_profile_markers", ())
    if profile_rows:
        active_count = sum(bool(item.get("active")) for item in profiles)
        caption = (
            f"Loaded profiles · {len(profiles)} point"
            f"{'s' if len(profiles) != 1 else ''}"
        )
        if active_count:
            caption += f" · {active_count} focused"
        captions.append(caption)

    occluded = self.occlusion_warnings()
    for key, raster in rasters:
        # Age belongs on the same line as the title. A live image with no
        # time on it invites the assumption that it is current, which is
        # exactly the assumption that misleads once a fetch starts failing.
        # The model-field title is skipped (T20.2, see above); its subtitle
        # carries the actual time the bar cannot.
        reference_time = getattr(self, "_export_reference_time", None)
        age = format_age(raster.age_seconds(reference_time))
        title = raster.title
        if age:
            marker = "\u26a0 " if raster.is_stale(reference_time) else ""
            title = f"{marker}{title} \u00b7 {age}"
        # T19.4: an occluded fill says so on the map itself, not only in
        # the layer list -- implying both fields are visible while only the
        # top one can be read is the lie this guards.
        if key in occluded:
            title = f"\u26a0 {title} — hidden under another field"
        if key == "hrrr_field" and not seen_field_title:
            seen_field_title = True
            # T21: the header already names run · hour · valid, so the
            # field subtitle would repeat the same three facts verbatim
            # on the next line. The bar's scale line below carries the
            # range; the subtitle has nothing left to add.
            if age and not getattr(raster, "subtitle", ""):
                captions.append(title)
        else:
            captions.append(title)
        # T21.2 match state for the raster row: the HRRR frame names its
        # valid time; live frames name their observation time with age.
        try:
            state, detail = layer_time_match(
                layer_key=key, requested=self._valid_time,
                actual=getattr(raster, "valid_time", None),
                available=raster is not None)
            if state != "exact" and detail:
                marker = "⚠ " if state in ("outside", "unavailable") else ""
                captions.append(f"{marker}Time match: {detail}")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        credit = getattr(raster, "attribution", "")
        if credit and credit not in credits:
            credits.append(credit)

    # Forecast tabs show attribution and scale context in their setup rail;
    # their map legend remains a compact visual key.
    rail_details = bool(getattr(self, "_legend_metadata_in_rail", False))
    if credits and not rail_details:
        captions.append("Source: " + ", ".join(credits))

    overlay_rows = self._overlay_legend_rows()
    rows = overlay_rows + profile_rows
    palette = self._field_palette(rasters)
    if not captions and not rows and palette is None:
        self._legend_bar = None
        self._legend_bar_range = None
        self._legend_clip = None
        return

    from sharpmod.maps.map_legends import (
        choose_legend_corner,
        compact_identity,
        encoding_tag,
        scale_change_note,
        scale_lock_label,
        should_collapse,
        wrap_category_rows,
    )

    # T20.3: the bar draws against the locked range while locked, so a
    # comparison across panels or times reads one stated scale. The change
    # note names any range the lock overruled -- never silently rescaling.
    field_key = ""
    field_raster = None
    for raster_key, raster in rasters:
        if raster_key == "hrrr_field":
            field_key = str(getattr(raster, "short_name", "") or "")
            field_raster = raster
            break
    draw_palette, lock = self._effective_palette(field_key, field_raster)
    bar_low = bar_high = None
    bar_units = str(getattr(draw_palette, "units", "") or "") \
        if draw_palette is not None else ""
    if lock is not None:
        try:
            bar_low, bar_high = (float(lock["minimum"]),
                                 float(lock["maximum"]))
            bar_units = str(lock.get("units") or bar_units)
        except (TypeError, ValueError, OverflowError, KeyError):
            bar_low = bar_high = None
    scale_line = ""
    change_line = ""
    if draw_palette is not None and field_key:
        label = self._product_label_for(field_key)
        locked = lock is not None
        if locked and bar_low is not None:
            scale_line = scale_lock_label(
                product=label, minimum=bar_low, maximum=bar_high,
                units=bar_units, locked=True)
            try:
                change_line = scale_change_note(
                    product=label,
                    old=(float(draw_palette.minimum),
                         float(draw_palette.maximum)),
                    new=(bar_low, bar_high), units=bar_units)
            except (TypeError, ValueError, OverflowError):
                change_line = ""
        else:
            scale_line = scale_lock_label(
                product=label, minimum=float(draw_palette.minimum),
                maximum=float(draw_palette.maximum),
                units=bar_units, locked=False)

    # T20.2: wrapped categorical rows and the encoding tag. The tag is what
    # keeps a banded reflectivity scale from reading as a continuum and a
    # report scatter from reading as a key.
    # Loaded-profile symbols get their own key line. Combining them with
    # an outlook category made the last source shape run past the legend
    # edge on an ordinary picker, which defeated the T22 marker key.
    shown_rows, hidden_count = wrap_category_rows(overlay_rows)
    shown_profile_rows, hidden_profile_count = wrap_category_rows(
        profile_rows
    )
    encoding_line = ""
    if draw_palette is not None:
        encoding_line = encoding_tag(
            continuous=True,
            stepped=bool(getattr(draw_palette, "stepped", False)))
    elif rows:
        encoding_line = encoding_tag(continuous=False)

    # T20.4: native grid context beside the rendered smoothness.
    from sharpmod.maps.map_legends import resolution_context

    resolution_line = ""
    if field_raster is not None:
        resolution_line = resolution_context(layer_key="hrrr_field")
    elif any(
        str(getattr(layer, "key", "") or "") == "storm_reports"
        for layer in layers
    ) and not draw_palette:
        resolution_line = resolution_context(layer_key="storm_reports")

    # Reserve exactly what each block draws, gaps included: the colour bar
    # block, the swatch block, and one line per caption/extras row. The
    # blocks are then laid out top-down from that total, so the bottom
    # caption lands on the bottom margin and nothing is drawn over anything
    # else. The bar block reserves its header, bar, and tick row even when
    # ticks collapse to the ends under a lock -- the tick row is still
    # drawn, so dropping its height would overprint the captions.
    ui_metrics = QtGui.QFontMetricsF(ui_font("caption"))
    mono_metrics = QtGui.QFontMetricsF(mono_font("caption"))
    line_h = max(LEGEND_LINE_H, math.ceil(ui_metrics.height() + 2.0))
    swatch_h = max(
        LEGEND_SWATCH_H, math.ceil(mono_metrics.height() + 2.0))
    block_gap = max(
        LEGEND_BLOCK_GAP, math.ceil(ui_metrics.height() * 0.30))
    bar_header_h = max(
        COLOUR_BAR_HEADER_H, math.ceil(mono_metrics.height()))
    bar_h = max(COLOUR_BAR_H, math.ceil(mono_metrics.height() * 0.48))
    bar_tick_h = max(
        COLOUR_BAR_TICK_H, math.ceil(mono_metrics.height()))
    colour_bar_block_h = bar_header_h + bar_h + bar_tick_h
    bar_w = self._colour_bar_width()
    overlay_layout = self._category_row_layout(
        shown_rows, hidden_count, bar_w, mono_metrics, swatch_h)
    profile_layout = self._category_row_layout(
        shown_profile_rows, hidden_profile_count, bar_w, mono_metrics,
        swatch_h)
    extras = [] if rail_details else [
        line for line in (scale_line, change_line, encoding_line,
                          resolution_line) if line]
    if rail_details:
        captions = [line for line in captions
                    if line.startswith("⚠") and "hidden under" in line]
        if draw_palette is None and not shown_rows and not shown_profile_rows \
                and not captions:
            self._legend_bar = None
            self._legend_bar_range = None
            self._legend_clip = None
            return
    block_h = (len(captions) + len(extras)) * line_h
    for chunks, hidden in (overlay_layout, profile_layout):
        if chunks or hidden:
            block_h += len(chunks) * swatch_h + (line_h if hidden else 0) + block_gap
    full_block_h = block_h
    if draw_palette is not None:
        full_block_h += colour_bar_block_h + block_gap
    probe = self._proj()
    avoid = self._legend_avoid_points(probe)
    corner_box = choose_legend_corner(
        width=self.width(), height=self.height(), box_width=bar_w,
        box_height=full_block_h, avoid=tuple(avoid),
        preferred=str(getattr(self, "_legend_corner", "") or ""))
    collapsed = bool(getattr(self, "_legend_collapsed", False)) or (
        full_block_h > self.height() - 20.0 if rail_details else
        should_collapse(width=self.width(), height=self.height(),
                        box_width=bar_w, box_height=full_block_h,
                        avoid=tuple(avoid),
                        user_collapsed=False))
    self._legend_layout_corner = corner_box.corner
    self._legend_layout_collapsed = collapsed
    if collapsed:
        # T20.1/T20.2: one line keeps product · units · scale; the key and
        # the prose wait for expansion rather than crowding the point.
        identity = ""
        if draw_palette is not None and field_key:
            label = self._product_label_for(field_key)
            identity = compact_identity(
                product=label, units=bar_units,
                scale_text=((scale_line.split(": ", 1)[0] if not rail_details else "")
                            if scale_line else ""))
        elif rows:
            identity = compact_identity(
                product=str(captions[0]) if captions else "Outlook",
                units="", scale_text=encoding_line)
        elif captions:
            identity = compact_identity(product=str(captions[0]))
        self._legend_bar = None
        self._legend_bar_range = None
        if identity:
            origin_x, origin_y = self._legend_origin(
                corner_box.corner, bar_w, line_h)
            self._legend_clip = self._draw_legend_panel(
                qp, origin_x, origin_y, bar_w, line_h)
            qp.setFont(ui_font("caption"))
            shown = qp.fontMetrics().elidedText(
                identity + "  (legend collapsed)", Qt.ElideRight,
                int(bar_w))
            self._draw_legend_text_row(
                qp, shown, origin_x, origin_y, bar_w, line_h)
        else:
            self._legend_clip = None
        return

    origin_x, origin_y = self._legend_origin(corner_box.corner, bar_w,
                                             full_block_h)
    self._legend_clip = self._draw_legend_panel(
        qp, origin_x, origin_y, bar_w, full_block_h)
    y = origin_y

    if draw_palette is not None:
        # A continuous field cannot be read from a list of swatches, so it
        # gets a bar. Above the categorical swatches because it belongs to
        # the image underneath everything, and the reading order then matches
        # the paint order.
        bar_range = (bar_low, bar_high) if bar_low is not None else (
            float(draw_palette.minimum), float(draw_palette.maximum))
        self._draw_field_colour_bar(qp, draw_palette, origin_x, y,
                                    bar_range=bar_range,
                                    hover_value=hover_value,
                                    header_h=bar_header_h,
                                    bar_h=bar_h,
                                    tick_h=bar_tick_h)
        self._legend_bar = (origin_x, y + bar_header_h, bar_w, bar_h)
        self._legend_bar_range = (float(bar_range[0]), float(bar_range[1]))
        y += colour_bar_block_h + block_gap
    else:
        self._legend_bar = None
        self._legend_bar_range = None

    if overlay_layout[0] or overlay_layout[1]:
        qp.setFont(mono_font("caption"))
        self._draw_category_rows(qp, shown_rows, hidden_count, origin_x,
                                 y, bar_w, row_height=swatch_h,
                                 line_height=line_h, layout=overlay_layout)
        y += len(overlay_layout[0]) * swatch_h + (
            line_h if overlay_layout[1] else 0) + block_gap

    if profile_layout[0] or profile_layout[1]:
        qp.setFont(mono_font("caption"))
        self._draw_category_rows(
            qp,
            shown_profile_rows,
            hidden_profile_count,
            origin_x,
            y,
            bar_w,
            row_height=swatch_h,
            line_height=line_h,
            layout=profile_layout,
        )
        y += len(profile_layout[0]) * swatch_h + (
            line_h if profile_layout[1] else 0) + block_gap

    # Product name and validity window: prose, so the UI family reads best.
    # T20.2: elided to the legend width, so a long validity line cannot run
    # past the box onto the map it explains (the crop showed the resolution
    # line doing exactly that). Elision keeps the one-line-per-row geometry
    # the reservation above measured; wrapping would need a second pass to
    # re-measure and still risks pushing the prose off narrow maps.
    qp.setFont(ui_font("caption"))
    for text in list(captions) + extras:
        shown = text
        if qp.fontMetrics().horizontalAdvance(text) > bar_w:
            shown = qp.fontMetrics().elidedText(text, Qt.ElideRight,
                                                int(bar_w))
        rect = QRectF(origin_x, y, bar_w, line_h)
        qp.setPen(QPen(QColor(_map().readout_shadow)))
        qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter,
                    shown)
        qp.setPen(QPen(QColor(_map().readout_text)))
        qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, shown)
        y += line_h

"""Winter and fire-weather render patch implementations."""

from __future__ import annotations


def install_winter_text_fit(
    *,
    _fit_font_to_rect,
    WINTER_LABEL_MAX_PT,
    WINTER_MIN_ROW_PX,
    WINTER_DGZ_ROWS,
    WINTER_ENERGY_ROWS,
):
    """Keep winter/DGZ panel text inside its columns.

    The vendored winter panel uses a height-scaled font and then draws long
    strings into one-tenth-width rectangles with ``TextDontClip``. The result is
    column bleed and right-edge clipping. This caps the panel font and redraws
    dynamic rows into actual full-width/column-width rects with elision only as
    a last resort.
    """
    try:
        import platform as _platform
        import sharppy.sharptab as _tab
        import sharppy.viz.winter as _winter_mod

        _QtGui = _winter_mod.QtGui
        _QtCore = _winter_mod.QtCore
        _bg = _winter_mod.backgroundWinter
        _plot = _winter_mod.plotWinter
        if getattr(_plot, "_sharpmod_text_fit", False):
            return

        _orig_init = _bg.initUI

        def initUI(self):
            _orig_init(self)
            try:
                f = _QtGui.QFont(self.label_font)
                ps = f.pointSizeF()
                if ps <= 0:
                    ps = float(
                        f.pixelSize() if f.pixelSize() > 0 else WINTER_LABEL_MAX_PT
                    )
                if ps > WINTER_LABEL_MAX_PT:
                    f.setPointSizeF(float(WINTER_LABEL_MAX_PT))
                    self.label_font = f
                    self.label_metrics = _QtGui.QFontMetrics(f)
                    self.os_mod = (
                        self.label_metrics.descent()
                        if _platform.system() == "Windows"
                        else 0
                    )
                    self.label_height = self.label_metrics.xHeight() + self.tpad
                    self.ylast = self.label_height
                    self.plotBitMap.fill(self.bg_color)
                    self.plotBackground()
            except Exception:
                pass

        def _columns(self):
            split = float(self.brx) * 0.48
            left_x = int(self.lpad)
            left_w = max(1, int(split - left_x - 6))
            right_x = int(split + 8)
            right_w = max(1, int(float(self.brx) - right_x - self.rpad - 4))
            return left_x, left_w, right_x, right_w

        def _natural_row_height(self):
            metrics = _QtGui.QFontMetrics(self.label_font)
            return max(int(metrics.height()), int(self.label_height) + 4)

        def _frame_bottom(self, row_h):
            """Return where the frame's last row ends for a given row height.

            Mirrors the walk in :func:`draw_frame` and the two row loops, so the
            fit below is measured against the real layout instead of an
            estimate. An earlier estimate that left out the per-row ``os_mod``
            and the taller bold precipitation rows still overran by 13 px.
            """
            gap = _row_gap(self)
            section = _section_gap(self)
            step = row_h + gap
            os_mod = int(getattr(self, "os_mod", 0) or 0)
            precip_h = _precip_row_height_for(self, row_h)
            block = row_h + gap + os_mod

            y = float(self.tpad) + step  # header -> OPRH
            y += step + section  # OPRH -> growth zone
            y += WINTER_DGZ_ROWS * block  # growth-zone rows
            y += _dgz_divider_gap(self) - gap  # divider
            y += section  # -> initial phase
            y += step + section - gap  # phase -> divider
            y += section  # -> warm/cold block
            y += WINTER_ENERGY_ROWS * block  # warm/cold rows
            y += section - gap
            y += section  # -> precip header
            y += step + section  # header -> type
            y += precip_h + section  # type -> sfc temp
            return y + precip_h  # bottom of last row

        def _row_height(self):
            """Row height that always fits the panel it is drawn in.

            The vendored panel took this from font metrics alone. Because the
            font is itself scaled from panel height, a taller panel grew its
            rows faster than it gained room and a short one never shrank them at
            all -- measured at 200x260 the last three rows, the precipitation
            type among them, were drawn below the frame.
            """
            natural = _natural_row_height(self)
            limit = float(getattr(self, "bry", 0)) - 2.0
            if limit <= 0:
                return natural
            candidate = natural
            while (
                candidate > WINTER_MIN_ROW_PX and _frame_bottom(self, candidate) > limit
            ):
                candidate -= 1
            return candidate

        def _row_gap(self):
            return 2

        def _section_gap(self):
            return 4

        def _dgz_divider_gap(self):
            return 8

        def _row_step(self):
            return _row_height(self) + _row_gap(self)

        def _precip_font(self):
            big = _QtGui.QFont(self.label_font)
            big.setBold(True)
            big.setPointSizeF(
                min(WINTER_LABEL_MAX_PT + 3, max(big.pointSizeF(), WINTER_LABEL_MAX_PT))
            )
            return big

        def _precip_row_height_for(self, row_h):
            metrics = _QtGui.QFontMetrics(_precip_font(self))
            return max(int(row_h), int(metrics.height()) + 2)

        def _precip_row_height(self):
            return _precip_row_height_for(self, _row_height(self))

        def _draw_text(
            self,
            qp,
            rect,
            text,
            color=None,
            align=None,
            base_font=None,
            max_pt=None,
            min_pt=4,
        ):
            if align is None:
                align = _QtCore.Qt.AlignLeft | _QtCore.Qt.AlignVCenter
            if color is None:
                color = self.fg_color
            if max_pt is None:
                max_pt = WINTER_LABEL_MAX_PT
            fit_height = max(float(rect.height()), float(_row_height(self)))
            font = _fit_font_to_rect(
                _QtGui,
                base_font or self.label_font,
                str(text),
                max(1, int(rect.width()) - 2),
                max(1, int(fit_height)),
                max_pt=max_pt,
                min_pt=min_pt,
            )
            qp.setFont(font)
            qp.setPen(_QtGui.QPen(color, 1, _QtCore.Qt.SolidLine))
            qp.drawText(rect, align | _QtCore.Qt.TextDontClip, str(text))

        def draw_frame(self, qp):
            qp.setPen(_QtGui.QPen(self.dgz_color, 1, _QtCore.Qt.SolidLine))
            qp.setFont(self.label_font)

            row_h = _row_height(self)
            step = _row_step(self)
            section_gap = _section_gap(self)
            # The two row loops advance by ``row_h + gap + os_mod`` while the
            # frame reserved only ``row_h + gap``. Three rows absorbed the
            # difference; a fourth did not, and the growth zone's last row was
            # drawn on top of the initial-phase line below it.
            block = step + int(getattr(self, "os_mod", 0) or 0)

            header_rect = _QtCore.QRectF(0, self.tpad, self.wid, row_h)
            _draw_text(
                self,
                qp,
                header_rect,
                "*** DENDRITIC GROWTH ZONE (-12 TO -17 C) ***",
                color=self.dgz_color,
                align=_QtCore.Qt.AlignCenter,
                min_pt=4,
            )

            self.oprh_y1 = self.tpad + step
            self.layers_y1 = self.oprh_y1 + step + section_gap
            begin = self.layers_y1 + step
            # Four rows: the vendored three plus the growth zone's pressure
            # bounds and the snow-to-liquid ratio.
            y1 = (
                self.layers_y1
                + WINTER_DGZ_ROWS * block
                + _dgz_divider_gap(self)
                - _row_gap(self)
            )

            qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
            qp.drawLine(0, y1, self.brx, y1)
            qp.drawLine(self.brx * 0.48, y1, self.brx * 0.48, begin)

            self.init_phase_y1 = y1 + section_gap
            y1 = self.init_phase_y1 + step + section_gap - _row_gap(self)
            qp.drawLine(0, y1, self.brx, y1)

            backup = y1 + section_gap
            y1 = backup + WINTER_ENERGY_ROWS * block + section_gap - _row_gap(self)

            self.energy_y1 = backup
            qp.drawLine(0, y1, self.brx, y1)
            qp.drawLine(self.brx * 0.48, y1, self.brx * 0.48, backup)
            y1 += section_gap

            best_guess_rect = _QtCore.QRectF(0, y1, self.wid, row_h)
            _draw_text(
                self,
                qp,
                best_guess_rect,
                "*** BEST GUESS PRECIP TYPE ***",
                align=_QtCore.Qt.AlignCenter,
                min_pt=4,
            )
            self.precip_type_y1 = y1 + step + section_gap
            self.ptype_tmpf_y1 = (
                self.precip_type_y1 + _precip_row_height(self) + section_gap
            )

        def drawDGZLayer(self, qp):
            pen = _QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine)
            qp.setPen(pen)
            qp.setFont(self.label_font)
            y1 = self.layers_y1
            sep = _row_gap(self)
            lh = _row_height(self)
            left_x, left_w, right_x, right_w = _columns(self)

            depth = (
                "Layer Depth: "
                + _tab.utils.INT2STR(self.dgz_depth)
                + " ft ("
                + _tab.utils.INT2STR(self.dgz_zbot)
                + "-"
                + _tab.utils.INT2STR(self.dgz_ztop)
                + " ft msl)"
            )
            _draw_text(
                self,
                qp,
                _QtCore.QRectF(left_x, y1, self.brx - left_x - self.rpad - 4, lh),
                depth,
            )
            y1 += lh + sep + self.os_mod

            if self.dgz_meanomeg == 10 * self.prof.missing:
                omeg = "N/A"
            else:
                omeg = _tab.utils.FLOAT2STR(self.dgz_meanomeg, 1) + " ub/s"

            # The growth zone is only ever reported in feet MSL, but the
            # Skew-T's own axis is pressure and the band is drawn against it, so
            # the bounds are given in both.
            # ``QC`` is truthy for ``None``, so the presence of the attribute has
            # to be tested separately before either value is converted.
            pbot = getattr(self, "dgz_pbot", None)
            ptop = getattr(self, "dgz_ptop", None)
            bounds = "DGZ: none"
            if pbot is not None and ptop is not None:
                try:
                    if (
                        _tab.utils.QC(pbot)
                        and _tab.utils.QC(ptop)
                        and float(pbot) != float(ptop)
                    ):
                        bounds = (
                            "DGZ: "
                            + _tab.utils.INT2STR(pbot)
                            + "-"
                            + _tab.utils.INT2STR(ptop)
                            + " hPa"
                        )
                except (TypeError, ValueError):
                    bounds = "DGZ: none"

            # Neither upstream nor this fork computed a snow ratio anywhere, so
            # the panel could describe the growth zone in five ways and still
            # not say how much snow an inch of liquid would make.
            try:
                from sharpmod.sharptab import winter as _winter_calc

                ratio = _winter_calc.format_snow_liquid_ratio(
                    _winter_calc.profile_snow_liquid_ratio(self.prof)
                )
            except Exception:  # noqa: BLE001 - a panel row is not worth a crash
                ratio = "M"

            rows = [
                (
                    "Mean Layer RH: " + _tab.utils.FLOAT2STR(self.dgz_meanrh, 0) + " %",
                    "Mean Layer MixRat: "
                    + _tab.utils.FLOAT2STR(self.dgz_meanq, 1)
                    + " g/kg",
                ),
                (
                    "Mean Layer PW: " + _tab.utils.FLOAT2STR(self.dgz_pw, 1) + " in",
                    "Mean Layer Omega: " + omeg,
                ),
                (bounds, "Kuchera SLR: " + ratio),
            ]
            for left, right in rows:
                _draw_text(self, qp, _QtCore.QRectF(left_x, y1, left_w, lh), left)
                _draw_text(self, qp, _QtCore.QRectF(right_x, y1, right_w, lh), right)
                y1 += lh + sep + self.os_mod

        def drawInitial(self, qp):
            qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
            qp.setFont(self.label_font)
            rect = _QtCore.QRectF(
                self.lpad,
                self.init_phase_y1,
                self.brx - self.lpad - self.rpad - 4,
                _row_height(self),
            )
            if self.plevel > 100:
                hght = _tab.utils.M2FT(_tab.interp.hght(self.prof, self.plevel))
                text = (
                    "Inital Phase: "
                    + self.init_st
                    + " from: "
                    + _tab.utils.INT2STR(self.plevel)
                    + " mb ("
                    + _tab.utils.INT2STR(hght)
                    + " ft msl; "
                    + _tab.utils.FLOAT2STR(self.init_tmp, 1)
                    + " C)"
                )
            else:
                text = "Initial Phase:  No Precipitation layers found."
            _draw_text(self, qp, rect, text)

        def drawWCLayer(self, qp):
            sep = _row_gap(self)
            lh = _row_height(self)
            left_x, left_w, right_x, right_w = _columns(self)

            if self.tpos > 0 and self.tneg < 0:
                string = (
                    "P/N: "
                    + str(round(self.tpos, 0))
                    + " / "
                    + str(round(self.tneg, 0))
                    + " J/kg"
                )
                left_labels = [
                    "TEMPERATURE PROFILE",
                    string,
                    "Melt Lyr: "
                    + str(int(self.ttop))
                    + "-"
                    + str(int(self.tbot))
                    + " mb",
                    "Frz Lyr: "
                    + str(int(self.tbot))
                    + "-"
                    + str(int(self.prof.pres[self.prof.sfc]))
                    + " mb",
                ]
            else:
                left_labels = [
                    "TEMPERATURE PROFILE",
                    "",
                    "Warm/Cold layers not found.",
                    "",
                ]

            if self.wpos > 0 and self.wneg < 0:
                string = (
                    "P/N: "
                    + str(round(self.wpos, 0))
                    + " / "
                    + str(round(self.wneg, 0))
                    + " J/kg"
                )
                right_labels = [
                    "WETBULB PROFILE",
                    string,
                    "Melt Lyr: "
                    + str(int(self.wtop))
                    + "-"
                    + str(int(self.wbot))
                    + " mb",
                    "Frz Lyr: "
                    + str(int(self.wbot))
                    + "-"
                    + str(int(self.prof.pres[self.prof.sfc]))
                    + " mb",
                ]
            else:
                right_labels = [
                    "WETBULB PROFILE",
                    "",
                    "Warm/Cold layers not found.",
                    "",
                ]

            for x, width, labels in (
                (left_x, left_w, left_labels),
                (right_x, right_w, right_labels),
            ):
                y1 = self.energy_y1
                for text in labels:
                    _draw_text(self, qp, _QtCore.QRectF(x, y1, width, lh), text)
                    y1 += lh + sep + self.os_mod

        def drawOPRH(self, qp):
            if (
                self.oprh < -0.1
                and _tab.utils.QC(self.oprh)
                and self.dgz_meanomeg != -99990.0
            ):
                color = _QtCore.Qt.red
            else:
                color = self.fg_color

            if self.dgz_meanomeg == -99990.0:
                text = "OPRH (Omega*PW*RH): N/A"
            else:
                text = "OPRH (Omega*PW*RH): " + _tab.utils.FLOAT2STR(self.oprh, 2)
            rect = _QtCore.QRectF(0, self.oprh_y1, self.wid, _row_height(self))
            _draw_text(self, qp, rect, text, color=color, align=_QtCore.Qt.AlignCenter)

        def drawPrecipType(self, qp):
            big = _precip_font(self)
            metrics = _QtGui.QFontMetrics(big)
            height = max(_precip_row_height(self), metrics.height() + 2)
            rect = _QtCore.QRectF(0, self.precip_type_y1, self.wid, height)
            _draw_text(
                self,
                qp,
                rect,
                self.precip_type,
                align=_QtCore.Qt.AlignCenter,
                base_font=big,
                max_pt=WINTER_LABEL_MAX_PT + 3,
                min_pt=5,
            )

        def drawPrecipTypeTemp(self, qp):
            small = _QtGui.QFont(self.label_font)
            small.setPointSizeF(max(6.0, min(WINTER_LABEL_MAX_PT, small.pointSizeF())))
            metrics = _QtGui.QFontMetrics(small)
            height = max(_row_height(self), metrics.height() + 2)
            rect = _QtCore.QRectF(0, self.ptype_tmpf_y1, self.wid, height)
            _draw_text(
                self,
                qp,
                rect,
                self.ptype_tmpf_string,
                align=_QtCore.Qt.AlignCenter,
                base_font=small,
                min_pt=5,
            )

        _bg.initUI = initUI
        _bg.draw_frame = draw_frame
        _plot.drawDGZLayer = drawDGZLayer
        _plot.drawInitial = drawInitial
        _plot.drawWCLayer = drawWCLayer
        _plot.drawOPRH = drawOPRH
        _plot.drawPrecipType = drawPrecipType
        _plot.drawPrecipTypeTemp = drawPrecipTypeTemp
        _plot._sharpmod_text_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def install_fire_text_fit(*, _fit_font_to_rect, FIRE_LABEL_MAX_PT):
    """Keep fire-weather panel text inside its columns.

    The same fault the winter panel had, and it went unnoticed for longer because
    this panel is only reachable by right-clicking one specific box. The vendored
    widget scales its font from the panel's height, then writes the moisture and
    low-level-wind rows into rects two fifths of the width with ``TextDontClip``.
    Measured on a real profile at 320x340, six of twenty-one rows were drawn
    wider than the box holding them -- "0-1 km mean = 169/22" wanted 400 px of a
    256 px column -- so the right-aligned wind column ran back across the
    left-aligned moisture column and the two interleaved.

    Two corrections, matching :func:`_install_winter_text_fit`: the label font is
    capped, and every row is drawn into a real column rect with the font fitted
    to it. The columns are also widened to the panel's actual padding; the
    vendored geometry left a tenth of the width empty on each side while
    overflowing the columns between them, which is the worst of both.
    """
    try:
        import platform as _platform

        import sharppy.sharptab as _tab
        import sharppy.viz.fire as _fire_mod

        _QtGui = _fire_mod.QtGui
        _QtCore = _fire_mod.QtCore
        _bg = _fire_mod.backgroundFire
        _plot = _fire_mod.plotFire
        if getattr(_plot, "_sharpmod_text_fit", False):
            return

        _orig_init = _bg.initUI

        def initUI(self):
            _orig_init(self)
            try:
                capped = False
                for name in ("label_font", "fosberg_font"):
                    font = _QtGui.QFont(getattr(self, name))
                    size = font.pointSizeF()
                    if size <= 0:
                        size = float(
                            font.pixelSize()
                            if font.pixelSize() > 0
                            else FIRE_LABEL_MAX_PT
                        )
                    # The Fosberg/Haines rows are the panel's headline numbers
                    # and are drawn full width, so they keep the two points of
                    # extra size the vendored widget gives them.
                    ceiling = (
                        FIRE_LABEL_MAX_PT
                        if name == "label_font"
                        else FIRE_LABEL_MAX_PT + 2
                    )
                    if size > ceiling:
                        font.setPointSizeF(float(ceiling))
                        setattr(self, name, font)
                        capped = True
                if not capped:
                    return
                self.label_metrics = _QtGui.QFontMetrics(self.label_font)
                self.fosberg_metrics = _QtGui.QFontMetrics(self.fosberg_font)
                self.os_mod = (
                    self.label_metrics.descent()
                    if _platform.system() == "Windows"
                    else 0
                )
                self.label_height = self.label_metrics.xHeight() + self.tpad
                self.ylast = self.label_height
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
            except Exception:
                pass

        def _columns(self):
            """Left and right column rectangles, using the real padding.

            Split at the midpoint with a gutter, so the left column is
            left-aligned moisture and the right column is right-aligned wind and
            the two cannot meet.
            """
            split = float(self.brx) * 0.5
            left_x = float(self.lpad)
            left_w = max(1.0, split - left_x - 4.0)
            right_x = split + 4.0
            right_w = max(1.0, float(self.brx) - self.rpad - right_x)
            return left_x, left_w, right_x, right_w

        #: Rows the panel stacks vertically: the title, the two column captions,
        #: four paired moisture/wind rows, the mixing-height line, the derived
        #: heading, and the three derived indices.
        _FIRE_ROWS = 11
        _FIRE_ROW_GAP = 2.0

        def _row_height(self):
            """Row height that keeps all eleven rows inside the panel.

            The vendored widget sized rows from font metrics alone and let the
            bottom rows fall off the frame -- the Haines row is drawn below the
            panel at 420x400 before this patch, and the whole derived block goes
            under at smaller sizes. So the height available per row is the
            ceiling, and the metrics only make rows *smaller* than that.
            """
            metrics = _QtGui.QFontMetrics(self.label_font)
            wanted = max(int(metrics.height()), int(self.label_height) + 4)
            available = float(self.bry) - self.tpad - self.bpad
            budget = available / _FIRE_ROWS - _FIRE_ROW_GAP
            return max(6.0, min(float(wanted), budget))

        def _row_step(self):
            return _row_height(self) + _FIRE_ROW_GAP

        def _draw_text(
            self,
            qp,
            rect,
            text,
            color=None,
            align=None,
            base_font=None,
            max_pt=None,
            min_pt=5,
        ):
            if align is None:
                align = _QtCore.Qt.AlignLeft | _QtCore.Qt.AlignVCenter
            if color is None:
                color = self.fg_color
            if max_pt is None:
                max_pt = FIRE_LABEL_MAX_PT
            fit_height = max(float(rect.height()), float(_row_height(self)))
            font = _fit_font_to_rect(
                _QtGui,
                base_font or self.label_font,
                str(text),
                max(1, int(rect.width()) - 2),
                max(1, int(fit_height)),
                max_pt=max_pt,
                min_pt=min_pt,
            )
            qp.setFont(font)
            qp.setPen(_QtGui.QPen(color, 1, _QtCore.Qt.SolidLine))
            qp.drawText(rect, align | _QtCore.Qt.TextDontClip, str(text))

        def _publish_layout(self):
            """Compute and store every row position, top to bottom.

            One cursor walked down the panel, rather than the vendored mix of
            fractional offsets and running totals. Both ``draw_frame`` and
            ``drawPBLchar`` read these, so the captions and the rows beneath them
            cannot drift apart.
            """
            row_h = _row_height(self)
            step = _row_step(self)
            left_x, left_w, right_x, right_w = _columns(self)
            self.moist_x, self.moist_width = left_x, left_w
            self.llw_x, self.llw_width = right_x, right_w
            self.moswindsep = _FIRE_ROW_GAP

            y = float(self.tpad)
            self.title_y1 = y
            y += step
            self.caption_y1 = y
            y += step
            self.caption_rule_y = y - _FIRE_ROW_GAP / 2.0
            self.start_data_y1 = y
            y += 4 * step  # four moisture/wind pairs
            self.pbl_y1 = y
            y += step
            self.derived_y1 = y
            y += step
            self.derived_rule_y = y - _FIRE_ROW_GAP / 2.0
            self.fosberg_y1 = y
            self.fosberg_x = 0
            self.fosberg_width = self.brx
            y += step
            self.haines_y1 = y
            self.haines_x = 0
            self.haines_width = self.brx
            y += step
            # Ventilation rate joins the derived indices rather than the mixed
            # layer rows above, because that is what it is: a composite of the
            # mixing height and the transport wind, both already printed.
            self.vent_y1 = y
            self.vent_width = self.brx
            return row_h

        def draw_frame(self, qp):
            """Title, the two column captions, and the two dividers.

            Restated rather than wrapped because this method is what publishes
            the geometry every row below is drawn into, and both widening the
            columns and fitting the rows to the panel height depend on owning it.
            """
            row_h = _publish_layout(self)
            self.labels = 2 * self.label_height + self.tpad + self.os_mod

            _draw_text(
                self,
                qp,
                _QtCore.QRectF(0, self.title_y1, self.wid, row_h),
                "Fire Weather Parameters",
                align=_QtCore.Qt.AlignCenter,
                base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2,
            )

            _draw_text(
                self,
                qp,
                _QtCore.QRectF(self.moist_x, self.caption_y1, self.moist_width, row_h),
                "Moisture",
                color=_QtGui.QColor("#00CC33"),
            )
            _draw_text(
                self,
                qp,
                _QtCore.QRectF(self.llw_x, self.caption_y1, self.llw_width, row_h),
                "Low-Level Wind",
                color=_QtGui.QColor("#0066CC"),
                align=_QtCore.Qt.AlignRight | _QtCore.Qt.AlignVCenter,
            )

            qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
            for rule in (self.caption_rule_y, self.derived_rule_y):
                qp.drawLine(0, int(rule), int(self.brx), int(rule))

            _draw_text(
                self,
                qp,
                _QtCore.QRectF(0, self.derived_y1, self.brx, row_h),
                "Derived Indices",
                color=_QtGui.QColor("#FF6633"),
                align=_QtCore.Qt.AlignCenter,
            )

        def drawPBLchar(self, qp):  # noqa: N802 - upstream Qt API
            # ``plotData`` can reach here before ``draw_frame`` has run on a
            # freshly resized widget, so the layout is published either way.
            row_h = _publish_layout(self)
            left_x, left_w = self.moist_x, self.moist_width
            right_x, right_w = self.llw_x, self.llw_width
            step = _row_step(self)
            right_align = _QtCore.Qt.AlignRight | _QtCore.Qt.AlignVCenter

            wind_rows = (
                (
                    "SFC = %s/%s"
                    % (
                        _tab.utils.INT2STR(self.sfc_wind[0]),
                        _tab.utils.INT2STR(self.sfc_wind[1]),
                    ),
                    None,
                ),
                (
                    "0-1 km mean = %s/%s"
                    % (
                        _tab.utils.INT2STR(self.meanwind01km[0]),
                        _tab.utils.INT2STR(self.meanwind01km[1]),
                    ),
                    None,
                ),
                (
                    "BL mean = %s/%s"
                    % (
                        _tab.utils.INT2STR(self.meanwindpbl[0]),
                        _tab.utils.INT2STR(self.meanwindpbl[1]),
                    ),
                    None,
                ),
                (
                    "BL max = %s/%s"
                    % (
                        _tab.utils.INT2STR(self.maxwindpbl[0]),
                        _tab.utils.INT2STR(self.maxwindpbl[1]),
                    ),
                    self.getMaxWindFormat()[0],
                ),
            )
            y1 = self.start_data_y1
            for text, color in wind_rows:
                _draw_text(
                    self,
                    qp,
                    _QtCore.QRectF(right_x, y1, right_w, row_h),
                    text,
                    color=color,
                    align=right_align,
                )
                y1 += step

            moisture_rows = (
                (
                    "SFC RH = %s%%" % _tab.utils.INT2STR(self.sfc_rh),
                    self.getSfcRHFormat()[0],
                ),
                ("0-1 km RH = %s%%" % _tab.utils.INT2STR(self.rh01km), None),
                ("BL mean RH = %s%%" % _tab.utils.INT2STR(self.pblrh), None),
                (
                    "PW = %s in" % _tab.utils.FLOAT2STR(self.pwat, 2),
                    self.getPWColor()[0],
                ),
            )
            y1 = self.start_data_y1
            for text, color in moisture_rows:
                _draw_text(
                    self,
                    qp,
                    _QtCore.QRectF(left_x, y1, left_w, row_h),
                    text,
                    color=color,
                )
                y1 += step

            _draw_text(
                self,
                qp,
                _QtCore.QRectF(0, self.pbl_y1, self.brx, row_h),
                "PBL Height = %sft / %sm"
                % (
                    _tab.utils.FLOAT2STR(_tab.utils.M2FT(self.pbl_h), 0),
                    _tab.utils.FLOAT2STR(self.pbl_h, 0),
                ),
                align=_QtCore.Qt.AlignCenter,
            )

        def drawFosberg(self, qp):  # noqa: N802 - upstream Qt API
            value = (
                "M"
                if self.fosberg == self.prof.missing
                else _tab.utils.INT2STR(self.fosberg)
            )
            _draw_text(
                self,
                qp,
                _QtCore.QRectF(
                    0, self.fosberg_y1, self.fosberg_width, _row_height(self)
                ),
                "Fosberg FWI = %s" % value,
                color=self.getFosbergFormat(),
                align=_QtCore.Qt.AlignCenter,
                base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2,
            )

        def drawHainesIndex(self, qp):  # noqa: N802 - upstream Qt API
            elevation = ("L", "M", "H")[self.haines_hght]
            _draw_text(
                self,
                qp,
                _QtCore.QRectF(0, self.haines_y1, self.haines_width, _row_height(self)),
                "Haines Index (%s) = %s"
                % (elevation, _tab.utils.INT2STR(self.haines_index[self.haines_hght])),
                color=self.getHainesFormat(),
                align=_QtCore.Qt.AlignCenter,
                base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2,
            )

        def drawVentilationRate(self, qp):  # noqa: N802 - upstream Qt API
            """Mixing height times transport wind, the smoke-dispersion number.

            Built from ``pbl_h`` and ``meanwindpbl`` -- the same two values this
            panel already prints as "PBL Height" and "BL mean" -- so the three
            rows cannot disagree with each other.

            Left in the foreground colour on purpose. The other rows here are
            graded, but the breakpoints between poor and good ventilation are set
            by whichever agency issues the forecast and differ between them, so
            colouring this one would assert a threshold that is not ours to set.
            """
            from sharpmod.sharptab.constants import is_missing
            from sharpmod.sharptab.fire import ventilation_rate

            # ``meanwindpbl`` was converted to (direction, speed) in setProf.
            speed = self.meanwindpbl[1] if len(self.meanwindpbl) > 1 else None
            rate = ventilation_rate(self.pbl_h, speed)
            text = (
                "Vent Rate = M"
                if is_missing(rate)
                else "Vent Rate = %s m2/s" % _tab.utils.INT2STR(rate)
            )
            _draw_text(
                self,
                qp,
                _QtCore.QRectF(0, self.vent_y1, self.vent_width, _row_height(self)),
                text,
                align=_QtCore.Qt.AlignCenter,
                base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2,
            )

        def plotData(self):  # noqa: N802 - upstream Qt API
            """Redraw the panel body. Mirrors the vendored order, plus the

            ventilation-rate row this project adds.
            """
            if self.prof is None:
                return
            qp = _QtGui.QPainter()
            qp.begin(self.plotBitMap)
            try:
                qp.setRenderHint(qp.RenderHint.Antialiasing)
                qp.setRenderHint(qp.RenderHint.TextAntialiasing)
                self.drawPBLchar(qp)
                self.drawFosberg(qp)
                self.drawHainesIndex(qp)
                self.drawVentilationRate(qp)
            finally:
                qp.end()

        _bg.initUI = initUI
        _bg.draw_frame = draw_frame
        _plot.drawPBLchar = drawPBLchar
        _plot.drawFosberg = drawFosberg
        _plot.drawHainesIndex = drawHainesIndex
        _plot.drawVentilationRate = drawVentilationRate
        _plot.plotData = plotData
        _plot._sharpmod_text_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


__all__ = ["install_fire_text_fit", "install_winter_text_fit"]

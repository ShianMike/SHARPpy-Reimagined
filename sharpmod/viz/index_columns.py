"""Convective, kinematic, and composite index board columns."""

from __future__ import annotations

from qtpy import QtGui
from qtpy.QtCore import QRect
from qtpy.QtCore import Qt
from sharpmod.viz import colors
from sharpmod.viz.unit_text import value_unit_width
from sharpmod.viz.index_board import PCL_ATTR, MISS, _f, _mag, i0, f1, f2, dirspd, uv_dirspd, KT_TO_MS, IN_TO_CM


class IndexColumnsMixin:
    """Draw the index board columns."""

    def _col_conv(self, qp, R, rh):
        x, y, w = R.x(), R.y(), R.width()
        cols = ["PCL", "CAPE", "CINH", "LCL", "LI", "LFC", "EL", "MPL"]
        cw = w / len(cols)
        qp.setFont(self.hf)
        for i, c in enumerate(cols):
            self._text(qp, QRect(int(x + i * cw), y, int(cw), rh), c,
                       self.hdr, Qt.AlignHCenter)
        qp.setFont(self.rf)
        y += rh + 1
        # Distribute leftover vertical space across the two section dividers so
        # the column fills its height instead of clustering at the top. Content
        # below the header = 4 parcel + 6 stats + 5 lapse = 15 rows.
        per_div = max(6, int((R.height() - 16 * rh - 1) / 2))
        # Remember the parcel column's screen rect so a double-click here opens
        # the "Show Parcels" selector (wired by the interactive GUI).
        self._conv_rect = QRect(int(x), int(R.y()), int(w), int(R.height()))
        self._parcel_rows = []
        for name in list(self.pcl_types)[:4]:
            attr = PCL_ATTR.get(name, "sfcpcl")
            self._parcel_rows.append(
                (name, QRect(int(x), int(y), int(w), int(rh))))
            cape = self._p(attr, "bplus")
            cin = self._p(attr, "bminus")
            lcl = self._p(attr, "lclhght")
            li = self._p(attr, "li5")
            lfc = self._p(attr, "lfchght")
            el = self._p(attr, "elhght")
            mpl = self._p(attr, "mplhght")
            has_cape = cape is not None and cape > 0
            # Color CAPE/CINH/LCL/LI on a white -> yellow -> red -> pink
            # intensity scale (brighter = more significant), only when the
            # parcel has positive CAPE; LFC/EL and the parcel name stay neutral.
            if has_cape:
                cape_c = self._wyrp(cape, 1000, 2500, 4000, higher=True)
                # CINH uses the legacy SHARPpy scheme: weak inhibition is green
                # and it escalates (orange -> red) as the cap strengthens.
                cinh_c = self._cinh_legacy(cin)
                li_c = self._wyrp(li, -4, -7, -10, higher=False)
            else:
                cape_c = cinh_c = li_c = self.fg
            # LCL is left uncolored (no meaningful legacy tier scale here).
            lcl_c = self.fg
            cells = [
                (name, self.fg),
                (i0(cape), cape_c),
                (i0(cin), cinh_c),
                (i0(lcl), lcl_c),
                (i0(li), li_c),
                (i0(lfc), self.fg),
                (i0(el), self.fg),
                (i0(mpl), self.fg),
            ]
            for i, (v, c) in enumerate(cells):
                self._text(qp, QRect(int(x + i * cw), y, int(cw), rh), v,
                           c, Qt.AlignHCenter)
            y += rh
        y += per_div // 2
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += per_div - per_div // 2

        def suf(v, s):
            # Append a unit suffix, but keep the missing placeholder untouched.
            return v if v == MISS else v + s

        col1 = [("PWAT", self._pwat(self._sf("pwat"))),
                ("MeanW", suf(f2(self._sf("mean_mixr")), " g/kg")),
                ("LowRH", suf(i0(self._sf("low_rh")), "%")),
                ("MidRH", suf(i0(self._sf("mid_rh")), "%")),
                ("DCAPE", i0(self._sf("dcape"))),
                ("DownT", self._temp(self._sf("drush")))]
        col2 = [("K", i0(self._sf("k_idx"))), ("TT", i0(self._sf("totals_totals"))),
                ("ConvT", self._temp(self._sf("convT"))),
                ("MaxT", self._temp(self._sf("maxT"))),
                ("ESP", f1(self._sf("esp"))), ("MMP", f2(self._sf("mmp")))]
        b3 = self._p("mlpcl", "b3km")
        b6 = self._p("mlpcl", "b6km")
        col3 = [("WNDG", f1(self._sf("wndg"))), ("TEI", i0(self._sf("tei"))),
                ("3CAPE", i0(b3), self._cape3_color(b3)),
                ("6CAPE", i0(b6), self._cape3_color(b6)),
                ("MBURST", i0(self._sf("mburst"))),
                ("SigSvr", suf(i0(self._sf("sig_severe")), " m\u00b3/s\u00b3"))]
        fm = QtGui.QFontMetrics(self.rf)
        stat_cols = (col1, col2, col3)
        gutter = 4
        min_widths = [max(
            fm.horizontalAdvance(entry[0] + " = ")
            + value_unit_width(self.rf, entry[1]) + 2
            for entry in col)
            for col in stat_cols]
        min_total = sum(min_widths) + gutter * (len(stat_cols) - 1)
        if min_total <= w:
            extra, remainder = divmod(w - min_total, len(stat_cols))
            stat_widths = [width + extra + (idx < remainder)
                           for idx, width in enumerate(min_widths)]
        else:
            gutter = 0
            base, remainder = divmod(w, len(stat_cols))
            stat_widths = [base + (idx < remainder)
                           for idx in range(len(stat_cols))]

        stat_xs = []
        cursor_x = x
        for width in stat_widths:
            stat_xs.append(cursor_x)
            cursor_x += width + gutter

        for ci, col in enumerate(stat_cols):
            cx = stat_xs[ci]
            col_width = stat_widths[ci]
            val_right = cx + col_width
            for ri, entry in enumerate(col):
                lbl, val = entry[0], entry[1]
                cc = entry[2] if len(entry) > 2 else self.fg
                ry = y + ri * rh
                # Left-align "label = " then the value right after it, so long
                # labels are never clipped on the left (right-aligning them was
                # cutting off e.g. MBURST -> BURST in the narrow sub-columns).
                ltext = lbl + " = "
                self._text(qp, QRect(cx, ry, col_width, rh), ltext, cc)
                lw = fm.horizontalAdvance(ltext)
                vw = max(0, int(val_right) - (cx + lw) - 2)
                # Shrink the value font just enough to fit its slot, so unit
                # suffixes (e.g. SigSvr's "m3/s3") are never clipped even in the
                # narrow sub-columns on smaller panels.
                vfont = self.rf
                if vw > 0 and value_unit_width(vfont, val) > vw:
                    px = self.rf.pixelSize()
                    while px > 9:
                        px -= 1
                        vfont = QtGui.QFont(self.rf); vfont.setPixelSize(px)
                        if value_unit_width(vfont, val) <= vw:
                            break
                    qp.setFont(vfont)
                self._text(qp, QRect(cx + lw, ry, vw, rh),
                           val, cc, Qt.AlignLeft)
                if vfont is not self.rf:
                    qp.setFont(self.rf)
        y += 6 * rh
        y += per_div // 2
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += per_div - per_div // 2

        lapse = [("SFC-500m LR", self._d("lapserate_sfc_500m"), True),
                 ("SFC-1km LR", self._d("lapserate_sfc_1km"), True),
                 ("SFC-3km LR", self._sf("lapserate_3km"), False),
                 ("850-500 LR", self._sf("lapserate_850_500"), False),
                 ("700-500 LR", self._sf("lapserate_700_500"), False)]
        # Severe Weather Composite drawn BESIDE the lapse rates, using the free
        # space on the right of this section (moved out of the composite col).
        # Each value is colored by its own threshold tier (Rich Thompson / SPC
        # scales) via colors.tier_color, not a fixed hue, so the color tracks
        # the current value the same way the legacy renderer's drawSevere did.
        def _svr_color(param, v):
            if v is None:
                return self.fg
            try:
                return QtGui.QColor(colors.tier_color(
                    param, v, bg_color=self.bg.name(),
                    fg_color=self.fg.name()))
            except Exception:
                return self.fg

        scp_v = self._sf("right_scp")
        stpc_v = self._sf("stp_cin")
        stpf_v = self._sf("stp_fixed")
        ship_v = self._sf("ship")
        lrgh_v = self._d("lrghail")
        dcp_v = self._d("dcp")
        severe = [("Supercell Comp", f1(scp_v), self._scp_color(scp_v)),
                  ("STP(cin)", f1(stpc_v), _svr_color("stp_cin", stpc_v)),
                  ("STP(fix)", f1(stpf_v), _svr_color("stp_fixed", stpf_v)),
                  ("SHIP", f1(ship_v), _svr_color("ship", ship_v)),
                  ("Derecho Comp", f1(dcp_v), self._dcp_color(dcp_v))]
        fm_l = QtGui.QFontMetrics(self.rf)
        lwid = int(w * 0.49)
        bx = x + int(w * 0.50)          # severe box left edge (with margin)
        sx = bx + 7                     # severe text, small inset off the
                                        # separator (just clear of the border)
        swid = (x + w) - sx - 2
        y -= 3                          # nudge the lapse / severe block up a
                                        # touch (both columns move together)
        sec_top = y
        # Match the bottom baseline used by the ECAPE row in the neighboring
        # composite column, so the paired lapse/severe block has no extra gap.
        section_bottom = int(R.y() + R.height())

        row_count = max(len(lapse), len(severe))
        if row_count > 1:
            usable = max(0, section_bottom - sec_top - rh)
            compact_step = max(rh, int(round(rh * 1.12)))
            row_step = min(compact_step, usable / float(row_count - 1))
        else:
            row_step = rh

        def _row_ys(count, y_bias=0):
            if count <= 0:
                return []
            block_h = (count - 1) * row_step + rh
            avail_h = max(rh, section_bottom - sec_top)
            start = sec_top + max(0, (avail_h - block_h) / 2.0)
            max_start = section_bottom - block_h
            start += y_bias
            if max_start >= sec_top:
                start = min(start, max_start)
            return [int(round(start + i * row_step)) for i in range(count)]

        # The lapse and severe lists describe the same five visual rows. Use a
        # shared baseline sequence so paired entries stay horizontally aligned.
        row_ys = _row_ys(row_count)
        lapse_ys = row_ys[:len(lapse)]
        severe_ys = row_ys[:len(severe)]

        for row_y, (llbl, lval, _isnew) in zip(lapse_ys, lapse):
            c = self._lapse_color(lval)   # color by value (thermo.py table)
            # lapse rate (left): "label = value C/km"
            lt = llbl + " = "
            self._text(qp, QRect(x, row_y, lwid, rh), lt, c)
            lw2 = fm_l.horizontalAdvance(lt)
            lvt = (f1(lval) + " C/km") if lval is not None else MISS
            self._text(qp, QRect(x + lw2, row_y, lwid - lw2, rh), lvt, c,
                       Qt.AlignLeft)

        for row_y, (slbl, sval, sc) in zip(severe_ys, severe):
            # severe composite (right): value left-aligned right after the "=".
            st = slbl + " = "
            self._text(qp, QRect(sx, row_y, swid, rh), st, sc)
            sw2 = fm_l.horizontalAdvance(st)
            self._text(qp, QRect(sx + sw2, row_y, swid - sw2 - 2, rh), sval, sc,
                       Qt.AlignLeft)

        # Left separation line between the lapse rates and the severe composite.
        qp.setPen(QtGui.QPen(self.rule, 1))
        line_pad = max(4, rh // 4)
        line_top = sec_top + line_pad
        line_bottom = int(R.y() + R.height() - 2)
        if line_bottom > line_top:
            qp.drawLine(bx, line_top, bx, line_bottom)

    def _col_kin(self, qp, R, rh):
        x, y, w = R.x(), R.y(), R.width()
        # SRH and bulk shear are compact numeric readouts, while MnWind and
        # SRW can contain ``DDD/SSS`` vectors. Allocate the latter two their
        # own wider tracks instead of splitting the available width equally.
        lw = int(w * 0.28)
        srh_w = int(w * 0.13)
        shear_w = int(w * 0.13)
        vector_w = max(1, (w - lw - srh_w - shear_w) // 2)
        srw_w = max(1, w - lw - srh_w - shear_w - vector_w)
        # Pull only the SRH readout toward the layer labels.  The remaining
        # tracks retain their existing starts so their spacing and widths do
        # not move with it.
        srh_left_shift = max(4, int(w * 0.04))
        value_xs = (
            x + lw - srh_left_shift,
            x + lw + srh_w,
            x + lw + srh_w + shear_w,
            x + lw + srh_w + shear_w + vector_w,
        )
        value_ws = (srh_w, shear_w, vector_w, srw_w)
        # Two-line headers: short label on top, unit on a small second line, so
        # the unit-bearing headers never overlap their neighbours horizontally.
        qp.setFont(self.hfs)
        wunit = self._wind_unit()
        units = ["m2/s2", wunit, "\u00b0/" + wunit, "\u00b0/" + wunit]
        for i, hh in enumerate(["SRH", "Shear", "MnWind", "SRW"]):
            cx, cw = value_xs[i], value_ws[i]
            self._text(qp, QRect(cx, y, cw, rh), hh, self.hdr, Qt.AlignHCenter)
            self._text(qp, QRect(cx, y + rh - 5, cw, rh),
                       "(" + units[i] + ")", self.rule, Qt.AlignHCenter)
        qp.setFont(self.rf)
        y += 2 * rh - 4
        mw500 = self._dr("mean_wind_sfc_500m")
        srw500 = self._dr("srw_sfc_500m")
        rows = [
            ("SFC-500m", i0(self._d("srh500")),
             self._wind_scalar(self._d("shear_sfc_500m")),
             self._uv_dirspd(mw500), self._uv_dirspd(srw500), False),
            ("SFC-1km", i0(self._sf("srh1km")),
             self._wind_scalar(_mag(self._s("sfc_1km_shear"))),
             self._dirspd(self._s("mean_1km")),
             self._dirspd(self._s("srw_1km")), False),
            ("SFC-3km", i0(self._sf("srh3km")),
             self._wind_scalar(_mag(self._s("sfc_3km_shear"))),
             self._dirspd(self._s("mean_3km")),
             self._dirspd(self._s("srw_3km")), False),
            ("Eff Inflow", i0(self._sf("right_esrh")),
             self._wind_scalar(_mag(self._s("eff_shear"))),
             self._uv_dirspd(self._s("mean_eff")),
             self._uv_dirspd(self._s("srw_eff")), False),
            ("SFC-6km", MISS, self._wind_scalar(_mag(self._s("sfc_6km_shear"))),
             self._dirspd(self._s("mean_6km")),
             self._dirspd(self._s("srw_6km")), False),
            ("SFC-8km", MISS, self._wind_scalar(_mag(self._s("sfc_8km_shear"))),
             self._dirspd(self._s("mean_8km")),
             self._dirspd(self._s("srw_8km")), False),
            ("LCL-EL", MISS, self._wind_scalar(_mag(self._s("lcl_el_shear"))),
             self._dirspd(self._s("mean_lcl_el")),
             self._dirspd(self._s("srw_lcl_el")), False),
            ("Eff Shear", MISS, self._wind_scalar(self._sf("ebwspd")),
             self._uv_dirspd(self._s("mean_ebw")),
             self._uv_dirspd(self._s("srw_ebw")), False),
        ]
        for lbl, srh, shr, mnw, srw, isnew in rows:
            # Kinematics values are drawn neutral (no intensity coloring).
            base = self.new if isnew else self.fg
            self._text(qp, QRect(x, y, lw, rh), lbl, base)
            for i, v in enumerate((srh, shr, mnw, srw)):
                if v == MISS:
                    continue  # leave unavailable cells blank (no "--")
                self._text(qp, QRect(value_xs[i], y, value_ws[i], rh), v,
                           base, Qt.AlignHCenter)
            y += rh
        # Distribute the leftover vertical space across the two gaps below so
        # the storm-motion block ends near the bottom (no dead space).
        fm_k = QtGui.QFontMetrics(self.rf)
        bottom_pad = 0
        # BRN(2) + storm header(1) + storm(4). The final storm-motion row shares
        # the ECAPE bottom baseline rather than reserving a separate gap.
        remaining = 7 * rh + 4 + bottom_pad
        kg = max(4, int((R.y() + R.height() - y - remaining) / 2))
        y += 4
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += kg

        # Top of the right-hand whitespace beside the BRN/SR-wind + storm-motion
        # rows; the AGL wind barbs are anchored here so they occupy that empty
        # region instead of floating low beside the storm-motion vectors.
        barb_top = y
        # BRN Shear (m2/s2) and 4-6km SR wind; drawn neutral (no coloring).
        brn = self._p("mupcl", "brnshear")
        for lbl, val, unit in [
                ("BRN Shear", i0(brn), " m2/s2"),
                ("4-6km SR Wind", self._dirspd(self._s("right_srw_4_5km")),
                 " " + wunit)]:
            lt = lbl + " = "
            self._text(qp, QRect(x, y, w, rh), lt)
            lw3 = fm_k.horizontalAdvance(lt)
            vt = (val + unit) if val != MISS else MISS
            self._text(qp, QRect(x + lw3, y, w - lw3 - 2, rh), vt, self.fg,
                       Qt.AlignLeft)
            y += rh
        y += kg

        srw = self._s("srwind")
        if isinstance(srw, (tuple, list)) and len(srw) >= 4:
            br = self._uv_dirspd((srw[0], srw[1]))
            bl = self._uv_dirspd((srw[2], srw[3]))
        else:
            br = bl = MISS
        uds = self._s("upshear_downshear")
        if isinstance(uds, (tuple, list)) and len(uds) >= 4:
            cor_up = self._uv_dirspd((uds[0], uds[1]))
            cor_dn = self._uv_dirspd((uds[2], uds[3]))
        else:
            cor_up = cor_dn = MISS
        # Reserve a right-hand region for the 1 km / 6 km AGL wind barbs + their
        # label; the storm-motion vectors take the rest. Sized from the label's
        # own width so the vectors are never clipped when the column is wide
        # enough (the renderer widens the canvas to guarantee this).
        # Keep a dedicated, centered right-side barb region while giving the
        # storm-motion readouts a little more than half of this narrowed column.
        # That accommodates complete three-digit Corfidi direction/speed values.
        barb_region = int(QtGui.QFontMetrics(self.hfs).horizontalAdvance(
            "1km & 6km AGL") * 1.75) + 28
        text_w = max(int(w * 0.54), w - barb_region)
        sm_top = y
        self._text(qp, QRect(x, y, text_w, rh),
                   "...Storm Motion Vectors..."); y += rh
        # Bunkers Right (cyan) / Left (red) follow legacy SHARPpy; Corfidi
        # vectors stay neutral. Labels stay white; only the value is colored.
        for lbl, val, vcol in [("Bunkers Right", br, self.cyan),
                               ("Bunkers Left", bl, self.red),
                               ("Corfidi Dshr", cor_dn, self.fg),
                               ("Corfidi Ushr", cor_up, self.fg)]:
            lt = lbl + " = "
            self._text(qp, QRect(x, y, text_w, rh), lt)
            lw3 = fm_k.horizontalAdvance(lt)
            vt = (val + " " + wunit) if val != MISS else MISS
            self._text(qp, QRect(x + lw3, y, text_w - lw3 - 2, rh), vt,
                       vcol, Qt.AlignLeft)
            y += rh
        # 1 km & 6 km AGL wind barbs in the reserved right region, beside the
        # BRN/SR-wind + storm-motion rows (legacy SHARPpy kinematics-panel
        # feature). Anchored at ``barb_top`` so they fill the whitespace above
        # rather than sitting low beside the storm-motion vectors.
        # Use the full remaining column height so the group settles into the
        # otherwise-empty lower-right space instead of floating above it.
        agl_h = max(rh * 5, R.y() + R.height() - barb_top - bottom_pad)
        self._draw_agl_barbs(qp, x + text_w, barb_top, w - text_w,
                             agl_h)

    def _col_comp(self, qp, R, rh):
        x, y, w = R.x(), R.y(), R.width()
        qp.setFont(self.rf)
        fm = QtGui.QFontMetrics(self.rf)

        def row_at(cx, cw, cy, lbl, val, color):
            # Draw "label = " then the value left-aligned right after it, so the
            # value sits next to its label instead of being pushed to the far
            # right edge (that gap is what made the column look wide).
            ltext = lbl + " = "
            self._text(qp, QRect(cx, cy, cw, rh), ltext, color)
            lw = fm.horizontalAdvance(ltext)
            self._text(qp, QRect(cx + lw, cy, cw - lw - 2, rh), val, color, Qt.AlignLeft)

        def row(lbl, val, color):
            row_at(x, w, y, lbl, val, color)

        # The core Severe Weather Composite (SCP / STP / SHIP / DCP) lives
        # beside the lapse rates in the convective column. LRGHAIL is retained
        # here directly below MOSHE.
        pesk = self._d("peskov")
        mcs = self._d("mcs_index")
        ehi1 = self._d("ehi_0_1km")
        ehi3 = self._d("ehi_0_3km")
        lscp = self._d("lscp")
        if lscp is None:
            lscp = self._sf("left_scp")
        nstp = self._d("nstp")
        mshe = self._d("modified_sherbe")
        lrgh = self._d("lrghail")
        swt = self._sweat()
        top = [("EHI 0-1km", f1(ehi1), self._ehi_color(ehi1)),
               ("EHI 0-3km", f1(ehi3), self._ehi_color(ehi3)),
               ("VGP", f2(self._d("vgp")), self.fg),
               ("Peskov Index", f1(pesk), self._peskov_color(pesk)),
               ("MCS Index", f1(mcs), self._mcs_color(mcs)),
               ("SWEAT", i0(swt), self._sweat_color(swt)),
               ("MOSHE", f1(mshe), self._wyrp(mshe, 1.0, 2.0, 3.0, higher=True)),
               ("LRGHAIL", f1(lrgh), self._lrghail_color(lrgh))]
        hgz = self._d("hgz_cape")
        ncape = self._d("ncape")
        wbz = self._d("wbz_height")
        ecape = self._d("ecape")
        # CAPE-energy rows use the white->yellow->red->pink scale; WBZ is neutral.
        bot = [(("HGZ CAPE", i0(hgz), " J/kg",
                 self._wyrp(hgz, 1000, 2500, 4000, higher=True)),
                ("NSTP", f1(nstp), "",
                 self._wyrp(nstp, 1.0, 2.0, 4.0, higher=True))),
               (("NCAPE", f2(ncape), " J/kg/m",
                 self._wyrp(ncape, 0.1, 0.2, 0.3, higher=True)),
               ("ECAPE", i0(ecape), " J/kg",
                 self._wyrp(ecape, 1000, 2500, 4000, higher=True))),
               (("LSCP", f1(lscp), "",
                 self._wyrp(lscp, -1.0, -4.0, -8.0, higher=False)),
                None),
               (("WBZ Height", i0(wbz), " m AGL", self.fg), None)]
        # SHIP box-and-whisker chart at the TOP (above the EHI indices), then
        # the indices, then the CAPE block pushed to the bottom.
        # The composite indices are laid out in two columns, so they only take
        # ceil(len/2) rows -- freeing vertical space for a taller SHIP chart.
        top_rows = (len(top) + 1) // 2
        n_rows = top_rows + len(bot)
        slack = max(0, R.height() - n_rows * rh)
        # Fixed divider gaps that match the small spacing used elsewhere, so all
        # the vertical space freed by the two-column indices feeds the SHIP
        # chart (making it as tall as possible) instead of leaving a dead band
        # mid-panel. The chart's own divider below it reserves CHART_DIV px.
        MID_GAP = 12          # gap around the indices -> CAPE divider rule
        CHART_DIV = 8         # gap the SHIP chart's divider rule consumes below
        if slack > 70:
            mid_gap = MID_GAP
            # Consume every remaining pixel of slack into the chart height.
            chart_h = slack - mid_gap - CHART_DIV
        else:
            chart_h = 0
            mid_gap = max(6, slack)

        if chart_h >= 50:
            self._ship_chart(qp, QRect(x, y, w, chart_h))
            y += chart_h + 2
            qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
            y += CHART_DIV - 2
        # Restore the normal row font (the SHIP chart set the small header font).
        qp.setFont(self.rf)
        # Keep a compact, predictable right readout column for NSTP and ECAPE.
        col_gutter = 6
        min_right_w = max(60, int(w * 0.32))
        right_x = x + int(w * 0.54)
        right_x = min(right_x, x + w - min_right_w)
        left_w = max(1, right_x - x - col_gutter)
        right_w = max(1, x + w - right_x - 2)
        col_x = (x, right_x)
        left_n = top_rows
        for idx, (lbl, val, c) in enumerate(top):
            ci = 0 if idx < left_n else 1
            ri = idx if idx < left_n else idx - left_n
            row_at(col_x[ci], (left_w, right_w)[ci], y + ri * rh, lbl, val, c)
        y += top_rows * rh
        y += mid_gap // 2
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += mid_gap - mid_gap // 2
        for left, right in bot:
            lbl, val, sfx, c = left
            if right is None:
                row(lbl, (val + sfx) if val != MISS else MISS, c)
            else:
                row_at(x, left_w, y, lbl,
                       (val + sfx) if val != MISS else MISS, c)
                r_lbl, r_val, r_sfx, r_c = right
                row_at(col_x[1], right_w, y, r_lbl,
                       (r_val + r_sfx) if r_val != MISS else MISS, r_c)
            y += rh

    def _ship_chart(self, qp, R):
        try:
            import sharppy.databases.inset_data as _ins
            d = _ins.shipData()
        except Exception:
            return
        dist = d.get("ship_dist")
        xt = ["< 2 in", ">= 2 in"]
        if dist is None or len(dist) == 0:
            return
        x, y, w, h = R.x(), R.y(), R.width(), R.height()
        qp.setFont(self.hfs)
        self._text(qp, QRect(x, y, w, 12), "Sig Hail Param (SHIP)",
                   self.hdr, Qt.AlignHCenter)
        top = y + 14
        bottom = y + h - 12          # leave room for the x-axis labels
        if bottom - top < 20:
            return
        ymin, ymax = 0.0, 5.0

        def toy(v):
            v = max(ymin, min(ymax, float(v)))
            return int(bottom - (v - ymin) / (ymax - ymin) * (bottom - top))

        ax0 = x + 16
        ax1 = x + w - 3
        # dashed gridlines + y-axis labels 0..5
        for gv in range(0, 6):
            gy = toy(gv)
            qp.setPen(QtGui.QPen(QtGui.QColor("#2f6d88"), 1, Qt.DashLine))
            qp.drawLine(ax0, gy, ax1, gy)
            self._text(qp, QRect(x, gy - 6, 13, 12), str(gv), self.fg, Qt.AlignRight)
        # box-and-whisker per hail category, colored by hail-size class:
        # "< 2 in" -> yellow, ">= 2 in" -> red (matching the STP EF scale look).
        n = len(dist)
        plotw = ax1 - ax0
        cat_colors = [
            self._theme_qcolor("#FFFF00"),
            self._theme_qcolor("#FF0000"),
        ]
        for i in range(n):
            col = dist[i]
            wl, bb, med, bt, wh = (float(col[0]), float(col[1]), float(col[2]),
                                   float(col[3]), float(col[4]))
            cx = ax0 + int((i + 0.5) * plotw / n)
            bw = max(6, int(plotw / n * 0.28))
            cc = cat_colors[i] if i < len(cat_colors) else self.fg
            qp.setPen(QtGui.QPen(cc, 2))
            qp.setBrush(Qt.NoBrush)
            qp.drawLine(cx, toy(min(wl, bb)), cx, toy(bb))
            qp.drawLine(cx, toy(bt), cx, toy(max(wh, bt)))
            qp.drawRect(cx - bw, toy(bt), 2 * bw, toy(bb) - toy(bt))
            qp.drawLine(cx - bw, toy(med), cx + bw, toy(med))
            if i < len(xt):
                self._text(qp, QRect(cx - 44, bottom + 1, 88, 11), xt[i],
                           cc, Qt.AlignHCenter)
        # Current SHIP value as a reference line, colored to match the hail
        # size class it falls in: red once it reaches the sig-hail (>= 2 in)
        # regime (SHIP >= 1), yellow below -- the same yellow/red scheme as the
        # box-and-whisker categories.
        sv = self._sf("ship")
        if sv is not None:
            sy = toy(sv)
            line_col = (self._theme_qcolor("#FF0000") if sv >= 1.0
                        else self._theme_qcolor("#FFFF00"))
            qp.setPen(QtGui.QPen(line_col, 2))
            qp.drawLine(ax0, sy, ax1, sy)

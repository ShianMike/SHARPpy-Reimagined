"""SHARPpy Reimagined index board -- a from-scratch, legacy-styled reimplementation of the
SHARPpy bottom index tables, laid out across THREE columns with our own computed
spacing (so the bundled Space Grotesk font never overlaps the vendored
fixed-column panels). The vendored Effective Layer STP graphic is kept as the
4th column alongside this board.

Columns:
  1. Convective  -- parcel table (PCL/CAPE/CINH/LCL/LI/LFC/EL/MPL for SFC/ML/FCST/MU),
     the thermo stats block (3 sub-columns), and the lapse-rate box (SFC-1km LR
     first -- a SHARPpy Reimagined-derived addition).
  2. Kinematics  -- SRH/Shear/MnWind/SRW table (SFC-500m first -- derived),
     BRN Shear / 4-6km SR wind, the Storm-Motion vectors, and the coloured
     Supercell / STP(cin) / STP(fix) / SHIP / DCP severe box.
  3. Composite Indices -- the SHARPpy Reimagined-derived composites: EHI 0-1/0-3km,
     VGP, Peskov, MCS, and HGZ CAPE / NCAPE / WBZ Height / ECAPE.

Existing values are read from the analyzed SHARPpy convective profile; the new
ones from the SHARPpy Reimagined derived Profile. Nothing is recomputed (Req 13.3);
unavailable values render ``--``.
"""

from __future__ import annotations

import math

from qtpy import QtGui
from qtpy.QtCore import QRect, Qt, Signal
from qtpy.QtWidgets import QFrame

from sharpmod import colors
from sharpmod.viz.unit_text import draw_text_with_smaller_unit, value_unit_width

#: Parcel display-name -> Profile attribute (the six SHARPpy parcels).
PCL_ATTR = {
    "SFC": "sfcpcl", "ML": "mlpcl", "FCST": "fcstpcl",
    "MU": "mupcl", "EFF": "effpcl", "USER": "usrpcl",
}
from sharpmod.sharptab.constants import is_missing

__all__ = ["IndexBoard"]

MISS = colors.MISSING_STR


def _f(v):
    if v is None or is_missing(v):
        return None
    if isinstance(v, (tuple, list)):
        return _f(v[0]) if v else None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _mag(v):
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        u, w = _f(v[0]), _f(v[1])
        if u is None or w is None:
            return None
        return math.hypot(u, w)
    return _f(v)


def i0(x):
    return MISS if x is None else str(int(round(x)))


def f1(x):
    return MISS if x is None else "%.1f" % x


def f2(x):
    return MISS if x is None else "%.2f" % x


def dirspd(v):
    if not isinstance(v, (tuple, list)) or len(v) < 2:
        return MISS
    d, s = _f(v[0]), _f(v[1])
    if d is None or s is None:
        return MISS
    return "%03d/%02d" % (int(round(d)) % 360, int(round(s)))


def uv_dirspd(v):
    if not isinstance(v, (tuple, list)) or len(v) < 2:
        return MISS
    u, w = _f(v[0]), _f(v[1])
    if u is None or w is None:
        return MISS
    spd = math.hypot(u, w)
    d = (270.0 - math.degrees(math.atan2(w, u))) % 360.0
    return "%03d/%02d" % (int(round(d)) % 360, int(round(spd)))


KT_TO_MS = 0.514444
IN_TO_CM = 2.54


class IndexBoard(QFrame):
    #: Emitted when the parcel table is double-clicked (opens "Show Parcels").
    parcelDialogRequested = Signal()
    #: Emitted with a parcel key ("SFC"/"ML"/...) when a parcel row is clicked
    #: (sets that parcel's trace on the Skew-T, like legacy SHARPpy).
    parcelClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sp = None
        self.dp = None
        #: Which four parcels the convective column shows (matches the vendored
        #: ``plotText`` selection; updated when the user picks via "Show
        #: Parcels"). Defaults keep the headless PNG render unchanged.
        self.pcl_types = ["SFC", "ML", "FCST", "MU"]
        #: Screen rect of the parcel (convective) column, set during paint so a
        #: double-click there can raise the parcel selector.
        self._conv_rect = QRect()
        #: (key, QRect) for each drawn parcel row, so a single click can select
        #: that parcel's trace on the Skew-T.
        self._parcel_rows = []
        self._outer_border_lines = ()
        self.temp_units = "Fahrenheit"
        self.wind_units = "knots"
        self.pw_units = "in"
        self.setMinimumHeight(240)
        self.setStyleSheet(
            "QFrame { background-color: rgb(0,0,0); border: 0px; margin: 0px; }")
        self.bg = QtGui.QColor(colors.BG_COLOR)
        self.fg = QtGui.QColor(colors.FG_COLOR)
        self.new = QtGui.QColor(colors.ALERT_L2_COLOR)
        self.rule = QtGui.QColor("#8a8a8a")
        self.hdr = QtGui.QColor("#ffffff")
        self.cyan = QtGui.QColor("#00b0b0")
        self.magenta = QtGui.QColor("#ff40ff")
        self.red = QtGui.QColor("#ff4040")
        self.yellow = QtGui.QColor("#e0c000")
        self.hf = QtGui.QFont("Helvetica"); self.hf.setPixelSize(13); self.hf.setBold(True)
        self.rf = QtGui.QFont("Helvetica"); self.rf.setPixelSize(13)
        # Smaller bold font for tight column headers (kinematics table), so the
        # unit-bearing labels do not overflow their narrow value columns.
        self.hfs = QtGui.QFont("Helvetica"); self.hfs.setPixelSize(10); self.hfs.setBold(True)
        # Force antialiased, quality glyph rendering. Without this the bold
        # "Helvetica" (substituted on Windows) can fall back to a bitmap/hinted
        # face that renders pixelated -- unlike the smooth vendored STP/SARS
        # insets -- so the board text stays visually consistent with them.
        _strat = (QtGui.QFont.StyleStrategy.PreferAntialias
                  | QtGui.QFont.StyleStrategy.PreferQuality)
        for _f in (self.hf, self.rf, self.hfs):
            _f.setStyleStrategy(_strat)
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

    def setPreferences(self, update_gui: bool = True, **prefs) -> None:
        """Apply SHARPpy preferences, including units, to this custom board."""
        if "bg_color" in prefs:
            self.bg = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg = QtGui.QColor(prefs["fg_color"])
        if "alert_l2_color" in prefs:
            self.new = QtGui.QColor(prefs["alert_l2_color"])
        if ("temp_units" in prefs
                and prefs["temp_units"] in {"Fahrenheit", "Celsius"}):
            self.temp_units = prefs["temp_units"]
        if "wind_units" in prefs and prefs["wind_units"] in {"knots", "m/s"}:
            self.wind_units = prefs["wind_units"]
        if "pw_units" in prefs and prefs["pw_units"] in {"in", "cm"}:
            self.pw_units = prefs["pw_units"]
        if update_gui:
            self.clearData()
            self.plotData()
            self.update()

    def setData(self, sp, dp):
        self.sp, self.dp = sp, dp
        self._sweat_cache = "unset"
        self.clearData(); self.plotData(); self.update()

    def clearData(self):
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

    def resizeEvent(self, e):
        super().resizeEvent(e); self.clearData(); self.plotData()

    def paintEvent(self, e):
        super().paintEvent(e)
        qp = QtGui.QPainter(); qp.begin(self)
        qp.setClipRect(self.rect()); qp.drawPixmap(0, 0, self.plotBitMap); qp.end()

    def mousePressEvent(self, e):
        """Single-click a parcel row -> show that parcel's trace on the Skew-T.

        Mirrors legacy SHARPpy ("clicking on any of the 4 parcels changes the
        parcel trace drawn on the Skew-T"). The interactive GUI connects
        :attr:`parcelClicked`; headless renders leave it unconnected (no-op).
        """
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        for key, rect in self._parcel_rows:
            if rect.contains(pos):
                self.parcelClicked.emit(key)
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        """Double-click the parcel column -> open the "Show Parcels" selector.

        Mirrors the legacy SHARPpy behaviour (double-click the thermo/parcel
        inset). The interactive GUI connects :attr:`parcelDialogRequested` to
        the parcel selection dialog; in the headless renderer nothing is
        connected, so this is a harmless no-op.
        """
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        if self._conv_rect.isNull() or self._conv_rect.contains(pos):
            self.parcelDialogRequested.emit()

    # value accessors
    def _p(self, name, field):
        pcl = getattr(self.sp, name, None) if self.sp is not None else None
        return _f(getattr(pcl, field, None)) if pcl is not None else None

    def _s(self, a):
        return getattr(self.sp, a, None) if self.sp is not None else None

    def _sf(self, a):
        return _f(getattr(self.sp, a, None)) if self.sp is not None else None

    def _d(self, a):
        return _f(getattr(self.dp, a, None)) if self.dp is not None else None

    def _dr(self, a):
        # Raw derived-profile read (no float coercion) so vector-valued
        # attributes like the SFC-500m mean/SR wind keep their (u, v) tuple.
        v = getattr(self.dp, a, None) if self.dp is not None else None
        return None if is_missing(v) else v

    def _wind_unit(self):
        return "m/s" if self.wind_units == "m/s" else "kt"

    def _wind_speed(self, knots):
        v = _f(knots)
        if v is None:
            return None
        return v * KT_TO_MS if self.wind_units == "m/s" else v

    def _wind_scalar(self, knots):
        return i0(self._wind_speed(knots))

    def _dirspd(self, v):
        if not isinstance(v, (tuple, list)) or len(v) < 2:
            return MISS
        d, s = _f(v[0]), self._wind_speed(v[1])
        if d is None or s is None:
            return MISS
        return "%03d/%02d" % (int(round(d)) % 360, int(round(s)))

    def _uv_dirspd(self, v):
        if not isinstance(v, (tuple, list)) or len(v) < 2:
            return MISS
        u, w = _f(v[0]), _f(v[1])
        if u is None or w is None:
            return MISS
        spd = self._wind_speed(math.hypot(u, w))
        d = (270.0 - math.degrees(math.atan2(w, u))) % 360.0
        return "%03d/%02d" % (int(round(d)) % 360, int(round(spd)))

    def _temp(self, fahrenheit):
        v = _f(fahrenheit)
        if v is None:
            return MISS
        if self.temp_units == "Celsius":
            return "%d\u00b0C" % int(round((v - 32.0) * 5.0 / 9.0))
        return "%d\u00b0F" % int(round(v))

    def _pwat(self, inches):
        v = _f(inches)
        if v is None:
            return MISS
        if self.pw_units == "cm":
            return "%.1f cm" % (v * IN_TO_CM)
        return "%.2f in" % v

    def _peskov_color(self, v):
        return QtGui.QColor(colors.peskov_color(v))

    def _lrghail_color(self, v):
        return QtGui.QColor(colors.lrghail_color(v))

    def _scp_color(self, v):
        return QtGui.QColor(colors.scp_color(v))

    def _ehi_color(self, v):
        return QtGui.QColor(colors.ehi_color(v))

    def _dcp_color(self, v):
        return QtGui.QColor(colors.dcp_color(v))

    def _barb_path(self, wdir, wspd, shemis, scale):
        # Build a wind-barb painter path (staff + barbs/flags) in local
        # coordinates with the plotted point at the origin, already rotated to
        # ``wdir`` and scaled by ``scale``. Returns ``None`` on bad input.
        try:
            from sharpmod.viz import custom_barbs as cb
            wdir = float(wdir); wspd = float(wspd)
        except (TypeError, ValueError, Exception):
            return None
        if not (math.isfinite(wdir) and math.isfinite(wspd)):
            return None
        spd = int(round(wspd / 5.) * 5)
        path = QtGui.QPainterPath()
        if spd > 0:
            path.moveTo(0, 0)
            path.lineTo(25, 0)
            while spd >= 50:
                cb.drawFlag(path, shemis=shemis); spd -= 50
            while spd >= 10:
                cb.drawFullBarb(path, shemis=shemis); spd -= 10
            while spd >= 5:
                cb.drawHalfBarb(path, shemis=shemis); spd -= 5
        else:
            path.addEllipse(-3, -3, 6, 6)
        t = QtGui.QTransform()
        t.scale(scale, scale)
        t.rotate(wdir - 90)
        return t.map(path)

    def _draw_agl_barbs(self, qp, rx, top, rw, h):
        # 1 km (red) & 6 km (blue) AGL wind barbs drawn from a common origin,
        # with the two barbs' combined bounding box centered in the reserved
        # region [rx, rx+rw] and a two-line label beneath -- mirroring legacy
        # SHARPpy's kinematics panel. Centering the *bounding box* (not the barb
        # origin) keeps the barbs visually centered over the label regardless of
        # which way the staffs point.
        w1 = getattr(self.sp, "wind1km", None) if self.sp is not None else None
        w6 = getattr(self.sp, "wind6km", None) if self.sp is not None else None
        d1, s1 = (
            (_f(w1[0]), _f(w1[1]))
            if isinstance(w1, (tuple, list)) and len(w1) >= 2
            else (None, None)
        )
        d6, s6 = (
            (_f(w6[0]), _f(w6[1]))
            if isinstance(w6, (tuple, list)) and len(w6) >= 2
            else (None, None)
        )
        if d1 is None and d6 is None:
            return
        shemis = (_f(getattr(self.sp, "latitude", 0)) or 0) < 0
        # Scale the barbs to fill the reserved region without crossing the
        # right/bottom edge. The unscaled barb spans ~35 px (25 px staff +
        # barbs); size it against the room available above the two-line label.
        barb_span = 35.0
        avail = min(max(1.0, rw - 12.0), max(1.0, h * 0.54))
        scale = max(0.95, min(1.25, avail / barb_span))
        # Enlarge the label proportionally so it stays balanced with the barbs.
        lbl_font = QtGui.QFont(self.hfs)
        base_px = self.hfs.pixelSize() if self.hfs.pixelSize() > 0 else 10
        lbl_font.setPixelSize(max(base_px + 1,
                                  int(round(base_px * min(scale, 1.48)))))
        fm_label = QtGui.QFontMetrics(lbl_font)
        label_h = 2 * fm_label.height() + 2
        label_top = top + max(0, h - label_h - 2)
        barb_bottom = max(top + 1, label_top - 4)

        # Build both barbs (shared origin) and center their combined bounds.
        barbs = []
        if d6 is not None and s6 is not None:
            p6 = self._barb_path(d6, s6, shemis, scale)
            if p6 is not None:
                barbs.append((p6, "#0A74C6"))               # 6 km : blue
        if d1 is not None and s1 is not None:
            p1 = self._barb_path(d1, s1, shemis, scale)
            if p1 is not None:
                barbs.append((p1, "#AA0000"))               # 1 km : red
        center_x = rx + rw * 0.48
        if barbs:
            bounds = None
            for path, _c in barbs:
                br = path.boundingRect()
                bounds = br if bounds is None else bounds.united(br)
            cx = center_x
            cy = top + (barb_bottom - top) * 0.5
            dx = cx - bounds.center().x()
            dy = cy - bounds.center().y()
            pen = QtGui.QPen(Qt.NoPen)
            for path, color in barbs:
                pen = QtGui.QPen(QtGui.QColor(color), 1, Qt.SolidLine)
                pen.setWidthF(1.4 * max(1.0, scale ** 0.5))
                qp.setPen(pen)
                qp.setBrush(Qt.NoBrush)
                qp.save()
                qp.translate(dx, dy)
                qp.drawPath(path)
                qp.restore()

        qp.setFont(lbl_font)
        qp.setPen(QtGui.QPen(QtGui.QColor("#0A74C6"), 1))
        label_w = int(rw * 1.16)
        label_x = int(center_x - label_w * 0.5)
        lbl_rect = QRect(label_x, label_top, label_w, label_h)
        qp.drawText(lbl_rect, int(Qt.TextWordWrap | Qt.AlignHCenter | Qt.AlignTop),
                    "1km & 6km AGL\nWind Barbs")

    def _tier_qcolor(self, param, value, **ctx):
        # Resolve a documented tier color (colors.tier_color) to a QColor,
        # falling back to the neutral foreground for missing values or any
        # lookup failure.
        if value is None:
            return self.fg
        try:
            return QtGui.QColor(colors.tier_color(param, value, **ctx))
        except Exception:
            return self.fg

    def _cinh_legacy(self, cin):
        # Legacy SHARPpy parcel CINH coloring: weak inhibition is green and
        # escalates as the cap strengthens. Legacy breakpoints -50 / -100:
        #   >= -50  -> green (little inhibition)
        #   -100..-50 -> orange (moderate cap)
        #   < -100  -> red (strong cap)
        if cin is None:
            return self.fg
        if cin >= -50:
            return QtGui.QColor("#00FF00")
        if cin >= -100:
            return QtGui.QColor("#FFA500")
        return QtGui.QColor("#FF0000")

    def _wyrp(self, v, yellow, red, pink, higher=True):
        """4-color intensity scale: white -> yellow -> red -> pink.

        ``higher=True``  : larger values escalate (yellow<=red<=pink thresholds).
        ``higher=False`` : smaller / more-negative values escalate (pink is the
                           lowest/most-extreme bound).
        A missing value stays neutral white.
        """
        return QtGui.QColor(colors.common_gradient_color(
            v, yellow, red, pink, higher=higher))

    def _sweat_color(self, v):
        # SWEAT index color scale (colors.py): < 250 blue, 250-350 white,
        # 350-500 yellow, 500-650 red, >= 650 pink.
        if v is None:
            return self.fg
        return QtGui.QColor(colors.sweat_color(v))

    def _sweat(self):
        # SWEAT is not stored on the analyzed profile; compute it on demand
        # from the SHARPpy params helper. Guarded so a missing input or an
        # unavailable sharppy install never breaks the board.
        if getattr(self, "_sweat_cache", "unset") != "unset":
            return self._sweat_cache
        val = None
        if self.sp is not None:
            try:
                import sharppy.sharptab.params as _params
                val = _f(_params.sweat(self.sp))
            except Exception:
                val = None
        self._sweat_cache = val
        return val

    def _lapse_color(self, v):
        # Lapse-rate color table (from thermo.py): green<=6, yellow<=7,
        # orange<=8, red<=9, magenta>9.
        return QtGui.QColor(colors.lapse_rate_color(v))

    def _cape3_color(self, v):
        # 3CAPE color table (from thermo.py, by MLCAPE 0-3 km):
        # magenta>125, red>100, orange>75, yellow>50, green>25, else fg.
        if v is None:
            return self.fg
        if v > 125:
            return QtGui.QColor("#FF00FF")
        if v > 100:
            return QtGui.QColor("#FF0000")
        if v > 75:
            return QtGui.QColor("#FFA500")
        if v > 50:
            return QtGui.QColor("#FFFF00")
        if v > 25:
            return QtGui.QColor("#00FF00")
        return self.fg

    def _mcs_color(self, v):
        return QtGui.QColor(colors.mcs_color(v))

    def _text(self, qp, rect, s, color=None, align=Qt.AlignLeft):
        qp.setPen(QtGui.QPen(color or self.fg, 1))
        if draw_text_with_smaller_unit(qp, rect, s, align):
            return
        qp.drawText(rect, int(Qt.TextSingleLine | align | Qt.AlignVCenter), s)

    def plotData(self):
        W, H = self.plotBitMap.width(), self.plotBitMap.height()
        if W <= 6 or H <= 6:
            return
        qp = QtGui.QPainter(); qp.begin(self.plotBitMap)
        try:
            # Antialias glyphs (and shapes) so the board's text matches the
            # smooth vendored insets (STP/SARS) instead of rendering pixelated.
            qp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            qp.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
            qp.setClipRect(QRect(0, 0, W, H))
            qp.fillRect(QRect(0, 0, W, H), self.bg)
            fm = QtGui.QFontMetrics(self.rf)
            rh = fm.height() + 3
            # The outer grid reserves more width for Effective Layer STP. Keep
            # the convective and kinematics panels at their prior physical
            # widths while compacting the SHIP/composite column just enough for
            # its two-column readouts to remain fully visible.
            x1 = int(W * 0.38)   # end of convective column
            x2 = int(W * 0.718)  # end of kinematics column
            qp.setPen(QtGui.QPen(self.rule, 1))
            qp.drawLine(x1, 2, x1, H - 2)
            qp.drawLine(x2, 2, x2, H - 2)
            self._col_conv(qp, QRect(4, 2, x1 - 8, H - 4), rh)
            self._col_kin(qp, QRect(x1 + 6, 2, x2 - x1 - 10, H - 4), rh)
            # Let the composite panel reach the board frame rather than
            # reserving an unused right-side gutter before Effective STP.
            self._col_comp(qp, QRect(x2 + 6, 2, W - x2 - 7, H - 4), rh)
            # The parent bottom-band frame owns the full section border.
            self._outer_border_lines = ()
        finally:
            qp.end()

    # ---- column 1: convective -----------------------------------------
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
                return QtGui.QColor(colors.tier_color(param, v))
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

    # ---- column 2: kinematics -----------------------------------------
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

    # ---- column 3: composite indices ----------------------------------
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
                 self._wyrp(lscp, -1.0, -4.0, -8.0, higher=False)), None),
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

    # ---- Significant Hail Param (SHIP) box-and-whisker mini chart ------
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
        cat_colors = [QtGui.QColor("#FFFF00"), QtGui.QColor("#FF0000")]
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
            line_col = QtGui.QColor("#FF0000") if sv >= 1.0 else QtGui.QColor("#FFFF00")
            qp.setPen(QtGui.QPen(line_col, 2))
            qp.drawLine(ax0, sy, ax1, sy)

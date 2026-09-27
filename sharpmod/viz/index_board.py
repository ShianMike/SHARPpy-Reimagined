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
     VGP, Peskov, MCS, HGZ CAPE / NCAPE / WBZ Height / ECAPE.

Existing values are read from the analyzed SHARPpy convective profile; the new
ones from the SHARPpy Reimagined derived Profile. Nothing is recomputed (Req 13.3);
unavailable values render ``--``.
"""

from __future__ import annotations

import math

from qtpy import QtGui
from qtpy.QtCore import QRect, Qt, Signal
from qtpy.QtWidgets import QFrame

from sharpmod.viz import colors
from sharpmod.viz.unit_text import (
    apply_render_font_quality,
    draw_text_with_smaller_unit,
    value_unit_width,
)

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


from sharpmod.viz.index_columns import IndexColumnsMixin


class IndexBoard(IndexColumnsMixin, QFrame):
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
        self.bg = QtGui.QColor(colors.BG_COLOR)
        self.fg = QtGui.QColor(colors.FG_COLOR)
        self._apply_palette(colors.ALERT_L2_COLOR)
        self.hf = QtGui.QFont("Helvetica"); self.hf.setPixelSize(13); self.hf.setBold(True)
        self.rf = QtGui.QFont("Helvetica"); self.rf.setPixelSize(13)
        # Smaller bold font for tight column headers (kinematics table), so the
        # unit-bearing labels do not overflow their narrow value columns.
        self.hfs = QtGui.QFont("Helvetica"); self.hfs.setPixelSize(10); self.hfs.setBold(True)
        # Force antialiased, quality glyph rendering. Without this the bold
        # "Helvetica" (substituted on Windows) can fall back to a bitmap/hinted
        # face that renders pixelated -- unlike the smooth vendored STP/SARS
        # insets -- so the board text stays visually consistent with them.
        # Shared with the PNG export path: antialiased quality plus vertical-only
        # hinting, which measured crisper in every image mode (see unit_text).
        for _f in (self.hf, self.rf, self.hfs):
            apply_render_font_quality(_f)
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

    def setPreferences(self, update_gui: bool = True, **prefs) -> None:
        """Apply SHARPpy preferences, including units, to this custom board."""
        if "bg_color" in prefs:
            self.bg = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg = QtGui.QColor(prefs["fg_color"])
        self._apply_palette(prefs.get("alert_l2_color"))
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

    def _apply_palette(self, alert_l2=None):
        palette = colors.semantic_palette(self.bg.name(), self.fg.name())
        self.new = self._theme_qcolor(alert_l2 or palette["amber_l2"])
        self.rule = QtGui.QColor(palette["rule"])
        self.hdr = QtGui.QColor(palette["header"])
        self.cyan = QtGui.QColor(palette["cyan"])
        self.magenta = QtGui.QColor(palette["magenta"])
        self.red = QtGui.QColor(palette["red"])
        self.yellow = QtGui.QColor(palette["yellow"])
        self.green = QtGui.QColor(palette["green"])
        self.orange = QtGui.QColor(palette["orange"])
        self.blue = QtGui.QColor(palette["blue"])
        self.setStyleSheet(
            "QFrame { background-color: %s; border: 0px; margin: 0px; }"
            % self.bg.name())

    def _theme_qcolor(self, color, minimum=4.5):
        return QtGui.QColor(colors.resolve_theme_color(
            color, self.bg.name(), self.fg.name(), minimum=minimum))

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
        """Select a parcel from the relevant painted row.

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
        return self._theme_qcolor(colors.peskov_color(v))

    def _lrghail_color(self, v):
        return self._theme_qcolor(colors.lrghail_color(v))

    def _scp_color(self, v):
        return self._theme_qcolor(colors.scp_color(v))

    def _ehi_color(self, v):
        return self._theme_qcolor(colors.ehi_color(v))

    def _dcp_color(self, v):
        return self._theme_qcolor(colors.dcp_color(v))

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
            return QtGui.QColor(colors.tier_color(
                param, value, bg_color=self.bg.name(),
                fg_color=self.fg.name(), **ctx))
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
            return self.green
        if cin >= -100:
            return self.orange
        return self.red

    def _wyrp(self, v, yellow, red, pink, higher=True):
        """4-color intensity scale: white -> yellow -> red -> pink.

        ``higher=True``  : larger values escalate (yellow<=red<=pink thresholds).
        ``higher=False`` : smaller / more-negative values escalate (pink is the
                           lowest/most-extreme bound).
        A missing value stays neutral white.
        """
        return self._theme_qcolor(colors.common_gradient_color(
            v, yellow, red, pink, higher=higher))

    def _sweat_color(self, v):
        # SWEAT index color scale (colors.py): < 250 blue, 250-350 white,
        # 350-500 yellow, 500-650 red, >= 650 pink.
        if v is None:
            return self.fg
        return self._theme_qcolor(colors.sweat_color(v))

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
        return self._theme_qcolor(colors.lapse_rate_color(v))

    def _cape3_color(self, v):
        # 3CAPE color table (from thermo.py, by MLCAPE 0-3 km):
        # magenta>125, red>100, orange>75, yellow>50, green>25, else fg.
        if v is None:
            return self.fg
        if v > 125:
            return self.magenta
        if v > 100:
            return self.red
        if v > 75:
            return self.orange
        if v > 50:
            return self.yellow
        if v > 25:
            return self.green
        return self.fg

    def _mcs_color(self, v):
        return self._theme_qcolor(colors.mcs_color(v))

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

    # ---- column 2: kinematics -----------------------------------------

    # ---- column 3: composite indices ----------------------------------

    # ---- Significant Hail Param (SHIP) box-and-whisker mini chart ------

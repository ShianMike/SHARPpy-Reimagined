"""SHARPpy Reimagined index board -- a from-scratch, legacy-styled reimplementation of the
SHARPpy bottom index tables, laid out across THREE columns with our own computed
spacing (so the bundled Space Grotesk font never overlaps the vendored
fixed-column panels). The vendored Effective Layer STP graphic is kept as the
4th column alongside this board.

Columns:
  1. Convective  -- parcel table (PCL/CAPE/CINH/LCL/LI/LFC/EL for SFC/ML/FCST/MU),
     the thermo stats block (3 sub-columns), and the lapse-rate box (SFC-1km LR
     first -- a SHARPpy Reimagined-derived addition).
  2. Kinematics  -- SRH/Shear/MnWind/SRW table (SFC-500m first -- derived),
     BRN Shear / 4-6km SR wind, the Storm-Motion vectors, and the coloured
     Supercell / STP(cin) / STP(fix) / SHIP / DCP severe box.
  3. Composite Indices -- the SHARPpy Reimagined-derived composites: EHI 0-1/0-3km, HPI,
     LRGHAIL, Peskov, MCS, then HGZ CAPE / NCAPE / NCIN / ECAPE.

Existing values are read from the analyzed SHARPpy convective profile; the new
ones from the SHARPpy Reimagined derived Profile. Nothing is recomputed (Req 13.3);
unavailable values render ``--``.
"""

from __future__ import annotations

import math

from qtpy import QtGui
from qtpy.QtCore import QRect, Qt
from qtpy.QtWidgets import QFrame

from sharpmod import colors
from sharpmod.sharptab.constants import is_missing

__all__ = ["IndexBoard"]

MISS = colors.MISSING_STR

#: Bright pink used for the most extreme tiers (supercell / large-hail / EHI /
#: DCP / SWEAT tops), replacing the near-black legacy purple on black.
PINK = "#FF00FF"

# Peskov-index color scale: a rainbow from purple (<= -5) through blue / cyan /
# green / yellow / orange / red to magenta (>= 9.5). Evaluated as descending
# thresholds -- a value takes the color of the first band whose bound it meets.
PESKOV_COLORS = [
    (9.5, "#FF00FF"), (9.0, "#E0007F"), (8.5, "#B00030"), (8.0, "#C00000"),
    (7.5, "#D01000"), (7.0, "#E52200"), (6.5, "#FF4000"), (6.0, "#FF6600"),
    (5.5, "#FF8000"), (5.0, "#FF9900"), (4.5, "#FFB300"), (4.0, "#FFCC00"),
    (3.5, "#FFFF00"), (3.0, "#CCFF33"), (2.5, "#99FF33"), (2.0, "#66FF33"),
    (1.5, "#33FF44"), (1.0, "#33FF77"), (0.5, "#33FFCC"), (0.0, "#00FFFF"),
    (-1.0, "#0099FF"), (-2.0, "#0066FF"), (-3.0, "#3333FF"), (-4.0, "#6600FF"),
    (-5.0, "#9900FF"),
]


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


class IndexBoard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sp = None
        self.dp = None
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
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

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

    def _peskov_color(self, v):
        if v is None:
            return self.fg
        for thr, hexc in PESKOV_COLORS:
            if v >= thr:
                return QtGui.QColor(hexc)
        return QtGui.QColor(PESKOV_COLORS[-1][1])

    def _lrghail_color(self, v):
        # LRGHAIL (SPC Large Hail Parameter) tier color table (colors.py).
        # The documented top tier is a dark purple that reads poorly on black;
        # surface the most extreme large-hail values as pink instead.
        if v is None:
            return self.fg
        hexc = colors.lrghail_color(v)
        if hexc == colors.ALERT_TIERS[6]:
            return QtGui.QColor(PINK)
        return QtGui.QColor(hexc)

    def _scp_color(self, v):
        # Supercell Composite Parameter tier color. Recolor the extreme top
        # tier (dark purple) as pink so strong supercell environments pop.
        if v is None:
            return self.fg
        hexc = colors.scp_color(v)
        if hexc == colors.ALERT_TIERS[6]:
            return QtGui.QColor(PINK)
        return QtGui.QColor(hexc)

    def _ehi_color(self, v):
        # Energy-Helicity Index color scale: < 1 default, 1-2 yellow,
        # 2-3 red, >= 3 pink (significant/violent tornado potential).
        if v is None:
            return self.fg
        if v < 1.0:
            return self.fg
        if v < 2.0:
            return QtGui.QColor("#FFFF00")
        if v < 3.0:
            return QtGui.QColor("#FF0000")
        return QtGui.QColor(PINK)

    def _dcp_color(self, v):
        # Derecho Composite Parameter: < 1 default, 1-2 yellow, 2-4 orange,
        # 4-6 red, >= 6 pink.
        if v is None:
            return self.fg
        if v < 1.0:
            return self.fg
        if v < 2.0:
            return QtGui.QColor("#FFFF00")
        if v < 4.0:
            return QtGui.QColor("#FFA500")
        if v < 6.0:
            return QtGui.QColor("#FF0000")
        return QtGui.QColor(PINK)

    def _barb(self, qp, ox, oy, wdir, wspd, color, shemis=False):
        # Draw a wind barb at (ox, oy) in a fixed color (used for the 1 km /
        # 6 km AGL barbs, colored to distinguish the two levels like legacy
        # SHARPpy). Reuses the custom_barbs path builders for barb/flag shapes.
        try:
            from sharpmod.viz import custom_barbs as cb
            wdir = float(wdir); wspd = float(wspd)
        except (TypeError, ValueError, Exception):
            return
        if not (math.isfinite(wdir) and math.isfinite(wspd)):
            return
        pen = QtGui.QPen(QtGui.QColor(color), 1, Qt.SolidLine)
        pen.setWidthF(1.4)
        qp.setPen(pen)
        qp.setBrush(Qt.NoBrush)
        spd = int(round(wspd / 5.) * 5)
        qp.translate(ox, oy)
        try:
            if spd > 0:
                qp.rotate(wdir - 90)
                path = QtGui.QPainterPath()
                path.moveTo(0, 0)
                path.lineTo(25, 0)
                while spd >= 50:
                    cb.drawFlag(path, shemis=shemis); spd -= 50
                while spd >= 10:
                    cb.drawFullBarb(path, shemis=shemis); spd -= 10
                while spd >= 5:
                    cb.drawHalfBarb(path, shemis=shemis); spd -= 5
                qp.drawPath(path)
                qp.rotate(90 - wdir)
            else:
                qp.drawEllipse(-3, -3, 6, 6)
        finally:
            qp.translate(-ox, -oy)

    def _draw_agl_barbs(self, qp, rx, top, rw, h):
        # 1 km (red) & 6 km (blue) AGL wind barbs drawn from a common origin,
        # centered in the reserved region [rx, rx+rw], with a two-line label
        # beneath -- faithfully mirroring legacy SHARPpy's kinematics panel.
        w1 = getattr(self.sp, "wind1km", None) if self.sp is not None else None
        w6 = getattr(self.sp, "wind6km", None) if self.sp is not None else None
        d1, s1 = (_f(w1[0]), _f(w1[1])) if isinstance(w1, (tuple, list)) and len(w1) >= 2 else (None, None)
        d6, s6 = (_f(w6[0]), _f(w6[1])) if isinstance(w6, (tuple, list)) and len(w6) >= 2 else (None, None)
        if d1 is None and d6 is None:
            return
        shemis = (_f(getattr(self.sp, "latitude", 0)) or 0) < 0
        # Common barb origin: centered in the reserved region, above the label.
        ox = rx + rw // 2
        oy = top + int(h * 0.40)
        if d6 is not None and s6 is not None:
            self._barb(qp, ox, oy, d6, s6, "#0A74C6", shemis)   # 6 km : blue
        if d1 is not None and s1 is not None:
            self._barb(qp, ox, oy, d1, s1, "#AA0000", shemis)   # 1 km : red
        qp.setFont(self.hfs)
        qp.setPen(QtGui.QPen(QtGui.QColor("#0A74C6"), 1))
        fh = QtGui.QFontMetrics(self.hfs).height()
        lbl_rect = QRect(rx - 6, top + int(h * 0.66), rw + 12, 2 * fh)
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
        if v is None:
            return self.fg
        if higher:
            if v >= pink:
                return QtGui.QColor(PINK)
            if v >= red:
                return QtGui.QColor("#FF0000")
            if v >= yellow:
                return QtGui.QColor("#FFFF00")
            return self.fg
        if v <= pink:
            return QtGui.QColor(PINK)
        if v <= red:
            return QtGui.QColor("#FF0000")
        if v <= yellow:
            return QtGui.QColor("#FFFF00")
        return self.fg

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
        if v is None:
            return self.fg
        if v <= 6.0:
            return QtGui.QColor("#00FF00")
        if v <= 7.0:
            return QtGui.QColor("#FFFF00")
        if v <= 8.0:
            return QtGui.QColor("#FFA500")
        if v <= 9.0:
            return QtGui.QColor("#FF0000")
        return QtGui.QColor("#FF00FF")

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
        # MCS index color table: cyan < -1.5, white -1.5..0, red 0..3, pink > 3.
        if v is None:
            return self.fg
        if v < -1.5:
            return QtGui.QColor("#00FFFF")
        if v < 0.0:
            return QtGui.QColor("#FFFFFF")
        if v <= 3.0:
            return QtGui.QColor("#FF0000")
        return QtGui.QColor("#FF00FF")

    def _text(self, qp, rect, s, color=None, align=Qt.AlignLeft):
        qp.setPen(QtGui.QPen(color or self.fg, 1))
        qp.drawText(rect, int(Qt.TextSingleLine | align | Qt.AlignVCenter), s)

    def plotData(self):
        W, H = self.plotBitMap.width(), self.plotBitMap.height()
        if W <= 6 or H <= 6:
            return
        qp = QtGui.QPainter(); qp.begin(self.plotBitMap)
        try:
            qp.setClipRect(QRect(0, 0, W, H))
            qp.fillRect(QRect(0, 0, W, H), self.bg)
            fm = QtGui.QFontMetrics(self.rf)
            rh = fm.height() + 3
            x1 = int(W * 0.40)   # end of convective column
            x2 = int(W * 0.74)   # end of kinematics column
            qp.setPen(QtGui.QPen(self.rule, 1))
            qp.drawLine(x1, 2, x1, H - 2)
            qp.drawLine(x2, 2, x2, H - 2)
            self._col_conv(qp, QRect(4, 2, x1 - 8, H - 4), rh)
            self._col_kin(qp, QRect(x1 + 6, 2, x2 - x1 - 10, H - 4), rh)
            self._col_comp(qp, QRect(x2 + 6, 2, W - x2 - 10, H - 4), rh)
        finally:
            qp.end()

    # ---- column 1: convective -----------------------------------------
    def _col_conv(self, qp, R, rh):
        x, y, w = R.x(), R.y(), R.width()
        cols = ["PCL", "CAPE", "CINH", "LCL", "LI", "LFC", "EL"]
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
        for name, attr in [("SFC", "sfcpcl"), ("ML", "mlpcl"),
                           ("FCST", "fcstpcl"), ("MU", "mupcl")]:
            cape = self._p(attr, "bplus")
            cin = self._p(attr, "bminus")
            lcl = self._p(attr, "lclhght")
            li = self._p(attr, "li5")
            lfc = self._p(attr, "lfchght")
            el = self._p(attr, "elhght")
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

        # Temperatures come from the SHARPpy profile in Fahrenheit.
        tsym = "\u00b0F"
        col1 = [("PWAT", suf(f1(self._sf("pwat")), " in")),
                ("MeanW", f1(self._sf("mean_mixr"))),
                ("LowRH", suf(i0(self._sf("low_rh")), "%")),
                ("MidRH", suf(i0(self._sf("mid_rh")), "%")),
                ("DCAPE", i0(self._sf("dcape"))),
                ("DownT", suf(i0(self._sf("drush")), tsym))]
        col2 = [("K", i0(self._sf("k_idx"))), ("TT", i0(self._sf("totals_totals"))),
                ("ConvT", suf(i0(self._sf("convT")), tsym)),
                ("MaxT", suf(i0(self._sf("maxT")), tsym)),
                ("ESP", f1(self._sf("esp"))), ("MMP", f1(self._sf("mmp")))]
        b3 = self._p("mlpcl", "b3km")
        b6 = self._p("mlpcl", "b6km")
        col3 = [("WNDG", f1(self._sf("wndg"))), ("TEI", i0(self._sf("tei"))),
                ("3CAPE", i0(b3), self._cape3_color(b3)),
                ("6CAPE", i0(b6), self._cape3_color(b6)),
                ("MBURST", i0(self._sf("mburst"))),
                ("SigSvr", i0(self._sf("sig_severe")))]
        scw = w / 3.0
        fm = QtGui.QFontMetrics(self.rf)
        for ci, col in enumerate((col1, col2, col3)):
            cx = int(x + ci * scw)
            for ri, entry in enumerate(col):
                lbl, val = entry[0], entry[1]
                cc = entry[2] if len(entry) > 2 else self.fg
                ry = y + ri * rh
                # Left-align "label = " then the value right after it, so long
                # labels are never clipped on the left (right-aligning them was
                # cutting off e.g. MBURST -> BURST in the narrow sub-columns).
                ltext = lbl + " = "
                self._text(qp, QRect(cx, ry, int(scw), rh), ltext, cc)
                lw = fm.horizontalAdvance(ltext)
                self._text(qp, QRect(cx + lw, ry, int(scw) - lw - 2, rh),
                           val, cc, Qt.AlignLeft)
        y += 6 * rh
        y += per_div // 2
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += per_div - per_div // 2

        lapse = [("SFC-1km LR", self._d("lapserate_sfc_1km"), True),
                 ("SFC-3km LR", self._sf("lapserate_3km"), False),
                 ("3-6km LR", self._sf("lapserate_3_6km"), False),
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
        for (llbl, lval, isnew), (slbl, sval, sc) in zip(lapse, severe):
            c = self._lapse_color(lval)   # color by value (thermo.py table)
            # lapse rate (left): "label = value C/km"
            lt = llbl + " = "
            self._text(qp, QRect(x, y, lwid, rh), lt, c)
            lw2 = fm_l.horizontalAdvance(lt)
            lvt = (f1(lval) + " C/km") if lval is not None else MISS
            self._text(qp, QRect(x + lw2, y, lwid - lw2, rh), lvt, c, Qt.AlignLeft)
            # severe composite (right): value left-aligned right after the "=".
            # Kept on the same baseline as the lapse-rate row beside it.
            st = slbl + " = "
            self._text(qp, QRect(sx, y, swid, rh), st, sc)
            sw2 = fm_l.horizontalAdvance(st)
            self._text(qp, QRect(sx + sw2, y, swid - sw2 - 2, rh), sval, sc,
                       Qt.AlignLeft)
            y += rh
        # Left separation line between the lapse rates and the severe composite.
        qp.setPen(QtGui.QPen(self.rule, 1))
        qp.drawLine(bx, sec_top - 2, bx, y - 2)

    # ---- column 2: kinematics -----------------------------------------
    def _col_kin(self, qp, R, rh):
        x, y, w = R.x(), R.y(), R.width()
        lw = w * 0.30
        vc = (w - lw) / 4.0
        # Two-line headers: short label on top, unit on a small second line, so
        # the unit-bearing headers never overlap their neighbours horizontally.
        qp.setFont(self.hfs)
        units = ["m2/s2", "kt", "\u00b0/kt", "\u00b0/kt"]
        for i, hh in enumerate(["SRH", "Shear", "MnWind", "SRW"]):
            cx = int(x + lw + i * vc)
            self._text(qp, QRect(cx, y, int(vc), rh), hh, self.hdr, Qt.AlignHCenter)
            self._text(qp, QRect(cx, y + rh - 5, int(vc), rh),
                       "(" + units[i] + ")", self.rule, Qt.AlignHCenter)
        qp.setFont(self.rf)
        y += 2 * rh - 4
        mw500 = self._dr("mean_wind_sfc_500m")
        srw500 = self._dr("srw_sfc_500m")
        rows = [
            ("SFC-500m", i0(self._d("srh500")), i0(self._d("shear_sfc_500m")),
             uv_dirspd(mw500), uv_dirspd(srw500), True),
            ("SFC-1km", i0(self._sf("srh1km")), i0(_mag(self._s("sfc_1km_shear"))),
             dirspd(self._s("mean_1km")), dirspd(self._s("srw_1km")), False),
            ("SFC-3km", i0(self._sf("srh3km")), i0(_mag(self._s("sfc_3km_shear"))),
             dirspd(self._s("mean_3km")), dirspd(self._s("srw_3km")), False),
            ("Eff Inflow", i0(self._sf("right_esrh")),
             i0(_mag(self._s("eff_shear"))), uv_dirspd(self._s("mean_eff")),
             uv_dirspd(self._s("srw_eff")), False),
            ("SFC-6km", MISS, i0(_mag(self._s("sfc_6km_shear"))),
             dirspd(self._s("mean_6km")), dirspd(self._s("srw_6km")), False),
            ("SFC-8km", MISS, i0(_mag(self._s("sfc_8km_shear"))),
             dirspd(self._s("mean_8km")), dirspd(self._s("srw_8km")), False),
            ("LCL-EL", MISS, i0(_mag(self._s("lcl_el_shear"))),
             dirspd(self._s("mean_lcl_el")), dirspd(self._s("srw_lcl_el")), False),
            ("Eff Shear", MISS, i0(self._sf("ebwspd")),
             uv_dirspd(self._s("mean_ebw")), uv_dirspd(self._s("srw_ebw")), False),
        ]
        for lbl, srh, shr, mnw, srw, isnew in rows:
            # Kinematics values are drawn neutral (no intensity coloring); the
            # SFC-500m derived row keeps its amber "new" marker.
            base = self.new if isnew else self.fg
            self._text(qp, QRect(x, y, int(lw), rh), lbl, base)
            for i, v in enumerate((srh, shr, mnw, srw)):
                if v == MISS:
                    continue  # leave unavailable cells blank (no "--")
                self._text(qp, QRect(int(x + lw + i * vc), y, int(vc), rh), v,
                           base, Qt.AlignHCenter)
            y += rh
        # Distribute the leftover vertical space across the two gaps below so
        # the storm-motion block ends near the bottom (no dead space).
        fm_k = QtGui.QFontMetrics(self.rf)
        remaining = 7 * rh + 4          # BRN(2) + storm header(1) + storm(4)
        kg = max(4, int((R.y() + R.height() - y - remaining) / 2))
        y += 4
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += kg

        # BRN Shear (m2/s2) and 4-6km SR wind; drawn neutral (no coloring).
        brn = self._p("mupcl", "brnshear")
        for lbl, val, unit in [
                ("BRN Shear", i0(brn), " m2/s2"),
                ("4-6km SR Wind", dirspd(self._s("right_srw_4_5km")), " kt")]:
            lt = lbl + " = "
            self._text(qp, QRect(x, y, w, rh), lt)
            lw3 = fm_k.horizontalAdvance(lt)
            vt = (val + unit) if val != MISS else MISS
            self._text(qp, QRect(x + lw3, y, w - lw3 - 2, rh), vt, self.fg,
                       Qt.AlignLeft)
            y += rh
        y += kg

        srw = self._s("srwind")
        br = uv_dirspd((srw[0], srw[1])) if isinstance(srw, (tuple, list)) and len(srw) >= 4 else MISS
        bl = uv_dirspd((srw[2], srw[3])) if isinstance(srw, (tuple, list)) and len(srw) >= 4 else MISS
        uds = self._s("upshear_downshear")
        if isinstance(uds, (tuple, list)) and len(uds) >= 4:
            cor_up = uv_dirspd((uds[0], uds[1]))
            cor_dn = uv_dirspd((uds[2], uds[3]))
        else:
            cor_up = cor_dn = MISS
        # Reserve a right-hand region for the 1 km / 6 km AGL wind barbs + their
        # label; the storm-motion vectors take the rest. Sized from the label's
        # own width so the vectors are never clipped when the column is wide
        # enough (the renderer widens the canvas to guarantee this).
        barb_region = QtGui.QFontMetrics(self.hfs).horizontalAdvance(
            "1km & 6km AGL") + 18
        text_w = max(int(w * 0.5), w - barb_region)
        sm_top = y
        self._text(qp, QRect(x, y, text_w, rh),
                   "...Storm Motion Vectors..."); y += rh
        # Bunkers Right (cyan) / Left (red) follow legacy SHARPpy; Corfidi
        # vectors stay neutral. Labels stay white; only the value is colored.
        for lbl, val, vcol in [("Bunkers Right", br, self.cyan),
                               ("Bunkers Left", bl, self.red),
                               ("Corfidi Downshear", cor_dn, self.fg),
                               ("Corfidi Upshear", cor_up, self.fg)]:
            lt = lbl + " = "
            self._text(qp, QRect(x, y, text_w, rh), lt)
            lw3 = fm_k.horizontalAdvance(lt)
            vt = (val + " kt") if val != MISS else MISS
            self._text(qp, QRect(x + lw3, y, text_w - lw3 - 2, rh), vt,
                       vcol, Qt.AlignLeft)
            y += rh
        # 1 km & 6 km AGL wind barbs in the reserved right region, beside the
        # storm-motion vectors (legacy SHARPpy kinematics-panel feature).
        self._draw_agl_barbs(qp, x + text_w, sm_top, w - text_w,
                             max(rh * 5, y - sm_top))

    # ---- column 3: composite indices ----------------------------------
    def _col_comp(self, qp, R, rh):
        x, y, w = R.x(), R.y(), R.width()
        qp.setFont(self.rf)
        fm = QtGui.QFontMetrics(self.rf)

        def row(lbl, val, color):
            # Draw "label = " then the value left-aligned right after it, so the
            # value sits next to its label instead of being pushed to the far
            # right edge (that gap is what made the column look wide).
            ltext = lbl + " = "
            self._text(qp, QRect(x, y, w, rh), ltext, color)
            lw = fm.horizontalAdvance(ltext)
            self._text(qp, QRect(x + lw, y, w - lw - 2, rh), val, color, Qt.AlignLeft)

        # Note: the Severe Weather Composite (SCP / STP / SHIP / DCP) now lives
        # beside the lapse rates in the convective column, not here.
        pesk = self._d("peskov")
        mcs = self._d("mcs_index")
        lrgh = self._d("lrghail")
        ehi1 = self._d("ehi_0_1km")
        ehi3 = self._d("ehi_0_3km")
        swt = self._sweat()
        top = [("EHI 0-1km", f1(ehi1), self._ehi_color(ehi1)),
               ("EHI 0-3km", f1(ehi3), self._ehi_color(ehi3)),
               ("Hail Psbl Index", f1(self._d("hpi")), self.fg),
               ("LRGHAIL", f1(lrgh), self._lrghail_color(lrgh)),
               ("Peskov Index", f1(pesk), self._peskov_color(pesk)),
               ("MCS Index", f1(mcs), self._mcs_color(mcs)),
               ("SWEAT", i0(swt), self._sweat_color(swt))]
        hgz = self._d("hgz_cape")
        ncape = self._d("ncape")
        ncin = self._d("ncin")
        ecape = self._d("ecape")
        # HGZ CAPE / NCAPE / NCIN / ECAPE on the white->yellow->red->pink scale.
        bot = [("HGZ CAPE", i0(hgz), " J/kg",
                self._wyrp(hgz, 1000, 2500, 4000, higher=True)),
               ("NCAPE", f1(ncape), " J/kg/m",
                self._wyrp(ncape, 0.1, 0.2, 0.3, higher=True)),
               ("NCIN", f1(ncin), " J/kg/m",
                self._wyrp(ncin, -0.6, -0.3, -0.1, higher=True)),
               ("ECAPE", i0(ecape), " J/kg",
                self._wyrp(ecape, 1000, 2500, 4000, higher=True))]
        # SHIP box-and-whisker chart at the TOP (above the EHI indices), then
        # the indices, then the CAPE block pushed to the bottom.
        n_rows = len(top) + len(bot)
        slack = max(0, R.height() - n_rows * rh)
        chart_h = min(slack - 14, 150) if slack > 70 else 0
        mid_gap = max(6, slack - chart_h - 8)

        if chart_h >= 50:
            self._ship_chart(qp, QRect(x, y, w, chart_h))
            y += chart_h + 2
            qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
            y += 6
        # Restore the normal row font (the SHIP chart set the small header font).
        qp.setFont(self.rf)
        for lbl, val, c in top:
            row(lbl, val, c)
            y += rh
        y += mid_gap // 2
        qp.setPen(QtGui.QPen(self.rule, 1)); qp.drawLine(x, y, x + w, y)
        y += mid_gap - mid_gap // 2
        for lbl, val, sfx, c in bot:
            row(lbl, (val + sfx) if val != MISS else MISS, c)
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

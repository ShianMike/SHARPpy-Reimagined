"""SHARPpy Reimagined parameter board -- a self-contained replacement for the vendored
SHARPpy bottom index tables.

Rather than injecting new rows into the vendored ``plotText`` / ``plotKinematics``
/ SARS / STP panels (whose fixed, dense layouts leave no room and squish when
forced), this widget OWNS the entire bottom band and lays every parameter out in
its own outlined, titled group boxes with comfortable spacing -- so both the
existing SHARPpy-computed values and the SHARPpy Reimagined-derived parameters have a
clean home and grouping is entirely under our control (Requirement 22 legibility
+ 13.3 read-only).

Values are *read* off two profiles and never recomputed here:

* the analyzed **SHARPpy convective profile** (``sp_prof``) for the existing
  parameters (parcel CAPE/CIN, PW, K, lapse rates, SRH, shear, STP/SCP/SHIP,
  ...); and
* the **SHARPpy Reimagined derived Profile** (``derived_prof``) for the new lazy-derived
  parameters (6CAPE, HGZ CAPE, ECAPE, NCAPE, NCIN, SFC-1km LR, SFC-500m
  kinematics, DCP, EHI, HPI, LRGHAIL, Peskov, MCS).

Missing / masked / unavailable values render the documented ``--`` indicator.
"""

from __future__ import annotations

import math

from qtpy import QtGui, QtCore
from qtpy.QtCore import QRect, Qt
from qtpy.QtWidgets import QFrame

from sharpmod import colors
from sharpmod.sharptab.constants import is_missing

__all__ = ["ParamBoard"]

MISSING_STR = colors.MISSING_STR


# ---------------------------------------------------------------------------
# Value coercion helpers
# ---------------------------------------------------------------------------
def _scalar(v):
    """Coerce a scalar, (total, ...) tuple, or (u, v) pair to a float or None."""
    if v is None or is_missing(v):
        return None
    if isinstance(v, (tuple, list)):
        if len(v) == 0:
            return None
        # SRH tuples are (total, pos, neg) -> total; other 1-elem -> that.
        return _scalar(v[0])
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _mag(v):
    """Magnitude of a (u, v) pair (shear/mean-wind), else scalar."""
    if isinstance(v, (tuple, list)) and len(v) == 2:
        u = _scalar(v[0])
        w = _scalar(v[1])
        if u is None or w is None:
            return None
        return math.hypot(u, w)
    return _scalar(v)


def _fmt0(f):
    return str(int(round(f)))


def _fmt1(f):
    return f"{f:.1f}"


class ParamBoard(QFrame):
    """Full-width board of outlined, titled parameter group boxes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sp = None          # analyzed SHARPpy convective profile
        self.dp = None          # SHARPpy Reimagined derived Profile
        self._min_h = 220
        self.setMinimumHeight(self._min_h)
        self.setStyleSheet(
            "QFrame { background-color: rgb(0,0,0); border: 0px; margin: 0px; }"
        )
        self.bg = QtGui.QColor(colors.BG_COLOR)
        self.fg = QtGui.QColor(colors.FG_COLOR)
        self.border = QtGui.QColor("#3399CC")
        self.hdr = QtGui.QColor(colors.ALERT_L2_COLOR)

        self.title_font = QtGui.QFont("Helvetica")
        self.title_font.setPixelSize(12)
        self.title_font.setBold(True)
        self.row_font = QtGui.QFont("Helvetica")
        self.row_font.setPixelSize(12)

        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

    # -- data -------------------------------------------------------------
    def setData(self, sp_prof, derived_prof):
        self.sp = sp_prof
        self.dp = derived_prof
        self.clearData()
        self.plotData()
        self.update()

    def clearData(self):
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.clearData()
        self.plotData()

    def paintEvent(self, e):
        super().paintEvent(e)
        qp = QtGui.QPainter()
        qp.begin(self)
        qp.setClipRect(self.rect())
        qp.drawPixmap(0, 0, self.plotBitMap)
        qp.end()

    # -- value resolution -------------------------------------------------
    def _entry(self, label, value, fmt, tier=None):
        """Build a (label, text, QColor) row from a resolved float value."""
        if value is None:
            return (label, MISSING_STR, self.fg)
        text = fmt(value)
        color = self.fg
        if tier is not None:
            try:
                color = QtGui.QColor(colors.tier_color(tier, value))
            except Exception:
                color = self.fg
        return (label, text, color)

    def _sp(self, attr):
        return getattr(self.sp, attr, None) if self.sp is not None else None

    def _dp(self, attr):
        return getattr(self.dp, attr, None) if self.dp is not None else None

    def _parcel(self, name, field):
        pcl = getattr(self.sp, name, None) if self.sp is not None else None
        return getattr(pcl, field, None) if pcl is not None else None

    # -- group content ---------------------------------------------------
    def _groups(self):
        """Return ``[(title, [rows]), ...]`` with each row a (label,text,color).

        Existing values come from the analyzed SHARPpy profile; the new derived
        values from the SHARPpy Reimagined Profile. Everything is read, never recomputed.
        """
        E = self._entry
        groups = []

        # CAPE / CIN -----------------------------------------------------
        groups.append(("CAPE / CIN", [
            E("SBCAPE", _scalar(self._parcel("sfcpcl", "bplus")), _fmt0, "cape"),
            E("MLCAPE", _scalar(self._parcel("mlpcl", "bplus")), _fmt0, "cape"),
            E("MUCAPE", _scalar(self._parcel("mupcl", "bplus")), _fmt0, "cape"),
            E("6CAPE", _scalar(self._dp("cape_0_6km")), _fmt0, "cape"),
            E("HGZ CAPE", _scalar(self._dp("hgz_cape")), _fmt0, "cape"),
            E("ECAPE", _scalar(self._dp("ecape")), _fmt0, "cape"),
            E("DCAPE", _scalar(self._sp("dcape")), _fmt0, None),
            E("MUCIN", _scalar(self._parcel("mupcl", "bminus")), _fmt0, "cinh"),
            E("NCAPE", _scalar(self._dp("ncape")), _fmt1, None),
            E("NCIN", _scalar(self._dp("ncin")), _fmt1, None),
        ]))

        # Moisture / Lapse rates ----------------------------------------
        groups.append(("Moisture / Lapse", [
            E("PW (in)", _scalar(self._sp("pwat")), _fmt1, None),
            E("K-index", _scalar(self._sp("k_idx")), _fmt0, None),
            E("SFC-1km LR", _scalar(self._dp("lapserate_sfc_1km")), _fmt1, "lapse_rate"),
            E("SFC-3km LR", _scalar(self._sp("lapserate_3km")), _fmt1, "lapse_rate"),
            E("3-6km LR", _scalar(self._sp("lapserate_3_6km")), _fmt1, "lapse_rate"),
            E("850-500 LR", _scalar(self._sp("lapserate_850_500")), _fmt1, "lapse_rate"),
            E("700-500 LR", _scalar(self._sp("lapserate_700_500")), _fmt1, "lapse_rate"),
        ]))

        # Kinematics -----------------------------------------------------
        groups.append(("Kinematics", [
            E("SFC-500m SRH", _scalar(self._dp("srh500")), _fmt0, None),
            E("SFC-500m Shr", _scalar(self._dp("shear_sfc_500m")), _fmt0, None),
            E("SFC-500m MnW", _mag(self._dp("mean_wind_sfc_500m")), _fmt0, None),
            E("SFC-500m SRW", _mag(self._dp("srw_sfc_500m")), _fmt0, None),
            E("0-1km SRH", _scalar(self._sp("srh1km")), _fmt0, None),
            E("0-3km SRH", _scalar(self._sp("srh3km")), _fmt0, None),
            E("Eff SRH", _scalar(self._sp("right_esrh")), _fmt0, None),
            E("0-6km Shear", _mag(self._sp("sfc_6km_shear")), _fmt0, None),
            E("EBWD", _scalar(self._sp("ebwspd")), _fmt0, None),
        ]))

        # Composite indices ---------------------------------------------
        groups.append(("Composite Indices", [
            E("STP (cin)", _scalar(self._sp("stp_cin")), _fmt1, "stp"),
            E("STP (fix)", _scalar(self._sp("stp_fixed")), _fmt1, "stp"),
            E("SCP", _scalar(self._sp("right_scp")), _fmt1, "scp"),
            E("SHIP", _scalar(self._sp("ship")), _fmt1, "ship"),
            E("DCP", _scalar(self._dp("dcp")), _fmt1, None),
            E("EHI 0-1km", _scalar(self._dp("ehi_0_1km")), _fmt1, None),
            E("EHI 0-3km", _scalar(self._dp("ehi_0_3km")), _fmt1, None),
            E("Peskov", _scalar(self._dp("peskov")), _fmt1, None),
            E("MCS", _scalar(self._dp("mcs_index")), _fmt1, None),
        ]))

        # Hail -----------------------------------------------------------
        groups.append(("Hail", [
            E("HPI", _scalar(self._dp("hpi")), _fmt1, None),
            E("LRG HAIL", _scalar(self._dp("lrghail")), _fmt1, None),
        ]))

        return groups

    # -- drawing ----------------------------------------------------------
    def plotData(self):
        W = self.plotBitMap.width()
        H = self.plotBitMap.height()
        if W <= 2 or H <= 2:
            return
        groups = self._groups()
        n = len(groups)
        if n == 0:
            return

        qp = QtGui.QPainter()
        qp.begin(self.plotBitMap)
        try:
            qp.setClipRect(QRect(0, 0, W, H))
            qp.fillRect(QRect(0, 0, W, H), self.bg)

            gap = 6
            box_w = (W - gap * (n + 1)) / n
            box_h = H - 2 * gap
            fm_row = QtGui.QFontMetrics(self.row_font)
            row_h = fm_row.height() + 3

            for i, (title, rows) in enumerate(groups):
                x = int(gap + i * (box_w + gap))
                bw = int(box_w)
                y = gap
                bh = int(box_h)

                # Outlined box + header bar.
                qp.setPen(QtGui.QPen(self.border, 1, Qt.SolidLine))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(x, y, bw, bh)

                qp.setFont(self.title_font)
                th = QtGui.QFontMetrics(self.title_font).height() + 4
                qp.fillRect(QRect(x, y, bw, th), QtGui.QColor("#10222c"))
                qp.setPen(QtGui.QPen(self.hdr, 1, Qt.SolidLine))
                qp.drawText(QRect(x + 5, y, bw - 8, th),
                            int(Qt.AlignLeft | Qt.AlignVCenter), title)
                qp.setPen(QtGui.QPen(self.border, 1, Qt.SolidLine))
                qp.drawLine(x, y + th, x + bw, y + th)

                # Rows: label left, value right.
                qp.setFont(self.row_font)
                ry = y + th + 4
                lbl_w = int(bw * 0.62)
                for label, text, color in rows:
                    if ry + row_h > y + bh:
                        break
                    qp.setPen(QtGui.QPen(self.fg, 1, Qt.SolidLine))
                    qp.drawText(
                        QRect(x + 6, ry, lbl_w - 6, row_h),
                        int(Qt.TextSingleLine | Qt.AlignLeft | Qt.AlignVCenter),
                        label)
                    qp.setPen(QtGui.QPen(color, 1, Qt.SolidLine))
                    qp.drawText(
                        QRect(x + lbl_w, ry, bw - lbl_w - 6, row_h),
                        int(Qt.TextSingleLine | Qt.AlignRight | Qt.AlignVCenter),
                        text)
                    ry += row_h
        finally:
            qp.end()

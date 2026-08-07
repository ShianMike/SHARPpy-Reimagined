"""Regional-guidance display formatters and an optional standalone preview."""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import dataclass

from qtpy import QtCore, QtGui, QtWidgets

from sharpmod import colors
from sharpmod.guidance import (
    GuidanceState,
    RegionalGuidance,
    TOIGuidance,
)

TOI_DISPLAY_NAME = "Tornado Outbreak Indicator (TOI)"


@dataclass(frozen=True)
class GuidanceDisplayCell:
    label: str
    state: GuidanceState
    value: str
    detail: str


@dataclass(frozen=True)
class TOIExplanationRow:
    """One accessible, deterministic row in the TOI details panel."""

    section: str
    label: str
    value: str


#: Provenance values that count as an affirmative validation flag.
_VALIDATED_TRUTHY = frozenset({"yes", "true", "1", "validated"})


def toi_probability_is_supported(summary) -> bool:
    """Is the displayed TOI probability backed by a validated calibration?

    MEASURED: the shipped public-anchor transform scored a Brier skill of -0.561
    against climatology, and its most confident bin (forecasting 77%) verified at
    7.3% - below the base rate.  A number rendered as a percentage next to real
    thermodynamic parameters reads as a calibrated probability whether or not it
    is one, and a caveat inside a click-through dialog does not undo that for a
    reader who never clicks.

    So a percentage is shown only when an offline calibration artifact that
    actually passed the promotion gate is in use.  This is a *policy*, not a
    one-off edit: if an artifact is ever validated, the probability returns with
    no further code change, and until then the display falls back to the
    experimental score, which makes no calibration claim.
    """

    provenance = getattr(summary, "provenance", None) or {}
    if not isinstance(provenance, Mapping):
        return False
    flag = str(provenance.get("toi_calibration_validated", "")).strip().casefold()
    return flag in _VALIDATED_TRUTHY


def _percent(value: float) -> str:
    return f"{round(value * 100):d}%"


def _version_detail(method_version: str, calibration_version: str) -> str:
    parts = [part for part in (method_version, calibration_version) if part]
    return " | ".join(parts)


def _toi_cell(
    product: TOIGuidance, *, supported: bool = False
) -> GuidanceDisplayCell:
    if not product.available:
        return GuidanceDisplayCell(
            TOI_DISPLAY_NAME, product.state, "UNAVAILABLE", product.reason
        )
    values: list[str] = []
    if product.score is not None:
        values.append(f"Score {product.score:.1f}")
    if product.high_risk_probability is not None:
        # Only claim a probability when a validated calibration supports one;
        # otherwise say what the number actually is.
        if supported:
            values.append(f"P(RIV>=4) {_percent(product.high_risk_probability)}")
        else:
            values.append(
                f"uncalibrated {_percent(product.high_risk_probability)}"
            )
    features = product.features
    if not values and features is not None:
        values.extend(
            (
                f"Jet move {features.translation_speed_kt:.0f} kt",
                f"STP {features.maximum_stp:.1f}",
            )
        )
    detail_parts: list[str] = []
    if features is not None:
        detail_parts.append(
            f"{features.pressure_level_hpa} hPa | "
            f"max {features.maximum_jet_speed_kt:.0f} kt | "
            f"{features.jet_to_risk_distance_km:.0f} km @"
            f" {features.jet_to_risk_bearing_deg:.0f} deg"
        )
    version = _version_detail(product.method_version, product.calibration_version)
    if version:
        detail_parts.append(version)
    return GuidanceDisplayCell(
        TOI_DISPLAY_NAME,
        product.state,
        " | ".join(values) if values else "REGIONAL FEATURES",
        " | ".join(detail_parts),
    )


def guidance_display_cells(
    summary: RegionalGuidance,
) -> tuple[GuidanceDisplayCell, ...]:
    """Return the TOI cell painted by :class:`GuidanceStrip`."""

    return (
        _toi_cell(
            summary.toi, supported=toi_probability_is_supported(summary)
        ),
    )


def toi_probability_tier(probability: float | None) -> tuple[str, str]:
    """Return the exact index-board probability tier and its dark-theme color."""

    if probability is None:
        return "Unavailable", colors.FG_COLOR
    if probability >= 0.75:
        return "75%+", colors.GRADIENT_PINK
    if probability >= 0.50:
        return "50-74%", colors.GRADIENT_RED
    if probability >= 0.25:
        return "25-49%", colors.GRADIENT_YELLOW
    return "Below 25%", colors.FG_COLOR


def _time_range(summary: RegionalGuidance) -> str:
    start, end = summary.valid_start, summary.valid_end
    if start is None and end is None:
        return colors.MISSING_STR
    if start is None:
        return end.isoformat()
    if end is None or end == start:
        return start.isoformat()
    return f"{start.isoformat()} to {end.isoformat()}"


#: Offline-calibration identity surfaced beside the other version rows.
_CALIBRATION_ROWS = (
    ("toi_calibration_years", "Calibration years"),
    ("toi_calibration_target", "Calibration target"),
    ("toi_calibration_validated", "Calibration validated"),
)


def _component_rows(raw: str) -> tuple[TOIExplanationRow, ...]:
    labels = {
        "translation": "Translation component",
        "location": "Jet-location component",
        "maximum_jet": "Maximum-jet component",
        "season": "Seasonal adjustment",
        "stp_bin": "Peak-STP bin",
    }
    rows: list[TOIExplanationRow] = []
    for token in str(raw).split(";"):
        key, separator, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or not key or not value:
            continue
        rows.append(
            TOIExplanationRow(
                "Score breakdown",
                labels.get(key, key.replace("_", " ").title()),
                value.replace("*", " x "),
            )
        )
    if rows:
        return tuple(rows)
    return (
        TOIExplanationRow(
            "Score breakdown", "Score components", raw or colors.MISSING_STR
        ),
    )


def toi_explanation_rows(
    summary: RegionalGuidance | None,
) -> tuple[TOIExplanationRow, ...]:
    """Describe every available TOI input, result, version, and provenance field."""

    if summary is None:
        summary = RegionalGuidance.unavailable()
    product = summary.toi
    features = product.features
    probability = product.high_risk_probability
    tier, _tier_color = toi_probability_tier(probability)
    missing = colors.MISSING_STR
    rows = [
        TOIExplanationRow("Result", "State", product.state.value.title()),
        TOIExplanationRow(
            "Result",
            "High-risk probability",
            _percent(probability) if probability is not None else missing,
        ),
        TOIExplanationRow("Result", "Display color tier", tier),
        TOIExplanationRow(
            "Result",
            "Experimental score",
            f"{product.score:.2f} / 5.00" if product.score is not None else missing,
        ),
        TOIExplanationRow("Result", "Status / limitation", product.reason or missing),
        TOIExplanationRow(
            "Regional inputs",
            "Jet layer",
            f"{features.pressure_level_hpa} hPa" if features else missing,
        ),
        TOIExplanationRow(
            "Regional inputs",
            "Jet translation speed",
            f"{features.translation_speed_kt:.1f} kt" if features else missing,
        ),
        TOIExplanationRow(
            "Regional inputs",
            "Maximum jet speed",
            f"{features.maximum_jet_speed_kt:.1f} kt" if features else missing,
        ),
        TOIExplanationRow(
            "Regional inputs",
            "Jet-to-risk distance",
            f"{features.jet_to_risk_distance_km:.1f} km" if features else missing,
        ),
        TOIExplanationRow(
            "Regional inputs",
            "Jet-to-risk bearing",
            f"{features.jet_to_risk_bearing_deg:.1f} deg" if features else missing,
        ),
        TOIExplanationRow(
            "Regional inputs",
            "Peak STP",
            f"{features.maximum_stp:.2f}" if features else missing,
        ),
        TOIExplanationRow(
            "Regional inputs",
            "Seasonal month",
            (
                f"{calendar.month_name[features.month]} ({features.month})"
                if features
                else missing
            ),
        ),
        TOIExplanationRow(
            "Versioning", "Feature / score method", product.method_version or missing
        ),
        TOIExplanationRow(
            "Versioning",
            "Probability calibration",
            product.calibration_version or missing,
        ),
    ]

    provenance = dict(summary.provenance)

    # Measured performance belongs with the result, not at the bottom of a
    # generic provenance dump.  A probability that has been measured as worse
    # than climatology has to say so where the forecaster is already looking,
    # otherwise the disclosure exists only in the changelog.
    measured = provenance.pop("toi_measured_skill", "")
    measured_version = provenance.pop("toi_measured_skill_version", "")
    if measured:
        insert_at = next(
            (
                index + 1
                for index, row in enumerate(rows)
                if row.label == "Status / limitation"
            ),
            len(rows),
        )
        rows.insert(
            insert_at,
            TOIExplanationRow("Result", "Measured skill", measured),
        )
        if measured_version:
            rows.append(
                TOIExplanationRow(
                    "Versioning", "Measured-skill evaluation", measured_version
                )
            )

    # Calibration identity belongs beside the other versions, not buried in the
    # generic provenance dump.
    for key, label in _CALIBRATION_ROWS:
        rows.append(
            TOIExplanationRow(
                "Versioning", label, provenance.pop(key, "") or missing
            )
        )
    rows += [
        TOIExplanationRow("Data", "Source", summary.source or missing),
        TOIExplanationRow("Data", "Valid period", _time_range(summary)),
        TOIExplanationRow("Data", "Schema version", str(summary.schema_version)),
    ]

    components = provenance.pop("toi_components", "")
    if components:
        rows.extend(_component_rows(components))
    for key, value in provenance.items():
        label = key.replace("_", " ").strip().title()
        rows.append(TOIExplanationRow("Provenance", label, value or missing))
    return tuple(rows)


class TOIExplanationPanel(QtWidgets.QFrame):
    """Scrollable, accessible explanation of the embedded TOI probability."""

    def __init__(self, summary: RegionalGuidance | None = None, parent=None):
        super().__init__(parent)
        self.summary = summary or RegionalGuidance.unavailable()
        self.bg = QtGui.QColor(colors.BG_COLOR)
        self.fg = QtGui.QColor(colors.FG_COLOR)
        self.rows: tuple[TOIExplanationRow, ...] = ()
        self._row_values: dict[str, str] = {}
        self.setAccessibleName("TOI explanation panel")
        self.setObjectName("toiExplanationPanel")
        self.setMinimumSize(540, 460)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(7)
        self.title_label = QtWidgets.QLabel(TOI_DISPLAY_NAME)
        self.title_label.setObjectName("toiExplanationTitle")
        self.title_label.setAccessibleName("TOI explanation title")
        title_font = self.title_label.font()
        title_font.setPointSize(max(13, title_font.pointSize() + 4))
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        outer.addWidget(self.title_label)

        result_layout = QtWidgets.QHBoxLayout()
        self.probability_label = QtWidgets.QLabel(colors.MISSING_STR)
        self.probability_label.setObjectName("toiExplanationProbability")
        self.probability_label.setAccessibleName("TOI high-risk probability")
        probability_font = self.probability_label.font()
        probability_font.setPointSize(max(18, probability_font.pointSize() + 9))
        probability_font.setBold(True)
        self.probability_label.setFont(probability_font)
        result_layout.addWidget(self.probability_label)
        self.tier_label = QtWidgets.QLabel("Unavailable")
        self.tier_label.setObjectName("toiExplanationTier")
        self.tier_label.setAccessibleName("TOI display color tier")
        result_layout.addWidget(self.tier_label)
        result_layout.addStretch(1)
        outer.addLayout(result_layout)

        self.disclaimer_label = QtWidgets.QLabel()
        self.disclaimer_label.setObjectName("toiExplanationDisclaimer")
        self.disclaimer_label.setAccessibleName("TOI experimental status")
        self.disclaimer_label.setWordWrap(True)
        outer.addWidget(self.disclaimer_label)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("TOI inputs and provenance")
        outer.addWidget(self.scroll, 1)
        self._apply_palette()
        self.setGuidance(self.summary)

    @property
    def row_values(self) -> dict[str, str]:
        """Return displayed values by row label for tests and accessibility."""

        return dict(self._row_values)

    def _apply_palette(self) -> None:
        palette = colors.semantic_palette(self.bg.name(), self.fg.name())
        self.rule = QtGui.QColor(palette["rule"])
        self.orange = QtGui.QColor(palette["orange"])
        self.setStyleSheet(
            "#toiExplanationPanel {"
            f"background: {self.bg.name()}; color: {self.fg.name()};"
            f"border: 1px solid {self.rule.name()};"
            "}"
            "QLabel { background: transparent; border: 0; }"
            "QScrollArea { background: transparent; border: 0; }"
        )
        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(
                f"color: {self.fg.name()}; font-weight: bold;"
            )
        if hasattr(self, "scroll"):
            self.scroll.viewport().setStyleSheet(
                f"background: {self.bg.name()}; color: {self.fg.name()};"
            )

    def setPreferences(self, **prefs) -> None:
        if "bg_color" in prefs:
            self.bg = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg = QtGui.QColor(prefs["fg_color"])
        self._apply_palette()
        self.setGuidance(self.summary)

    def setGuidance(self, summary: RegionalGuidance | None) -> None:
        self.summary = summary or RegionalGuidance.unavailable()
        self.rows = toi_explanation_rows(self.summary)
        self._row_values = {row.label: row.value for row in self.rows}
        probability = self.summary.toi.high_risk_probability
        tier, tier_color = toi_probability_tier(probability)
        probability_text = _percent(probability) if probability is not None else "--"
        resolved = colors.resolve_theme_color(
            tier_color, self.bg.name(), self.fg.name(), minimum=4.5
        )
        self.probability_label.setText(probability_text)
        self.probability_label.setStyleSheet(f"color: {resolved};")
        self.tier_label.setText(f"Display tier: {tier}")
        self.tier_label.setStyleSheet(f"color: {resolved}; font-weight: bold;")
        self.disclaimer_label.setText(
            "Experimental SHARPpy reconstruction - not official SPC guidance. "
            "TOI needs regional, time-evolving fields and cannot be derived "
            "from a single sounding."
            if self.summary.experimental_not_official
            else "Regional TOI guidance supplied with this sounding."
        )
        self.disclaimer_label.setStyleSheet(
            f"color: {self.orange.name()}; font-weight: bold;"
        )
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        body = QtWidgets.QWidget()
        body.setObjectName("toiExplanationRows")
        body.setStyleSheet(
            f"#toiExplanationRows {{ background: {self.bg.name()}; "
            f"color: {self.fg.name()}; }}"
        )
        form = QtWidgets.QFormLayout(body)
        form.setContentsMargins(2, 4, 8, 4)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(5)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        current_section = None
        for row in self.rows:
            if row.section != current_section:
                current_section = row.section
                section = QtWidgets.QLabel(current_section.upper())
                section.setAccessibleName(f"TOI {current_section} section")
                section.setStyleSheet(
                    f"color: {self.rule.name()}; font-weight: bold; "
                    "padding-top: 8px;"
                )
                form.addRow(section)
            label = QtWidgets.QLabel(row.label)
            label.setAccessibleName(f"TOI {row.label} label")
            value = QtWidgets.QLabel(row.value)
            value.setObjectName(
                "toiRow" + "".join(part.title() for part in row.label.split())
            )
            value.setAccessibleName(f"TOI {row.label}: {row.value}")
            display_value = row.value.replace("_", "_\u200b").replace(";", ";\u200b")
            value.setText(display_value)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            form.addRow(label, value)
        self.scroll.setWidget(body)


class TOIExplanationDialog(QtWidgets.QDialog):
    """Non-modal dialog opened by clicking the embedded TOI index-board cell."""

    def __init__(self, summary: RegionalGuidance | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TOI_DISPLAY_NAME)
        self.setModal(False)
        self.setAccessibleName("TOI explanation dialog")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        self.panel = TOIExplanationPanel(summary, self)
        layout.addWidget(self.panel, 1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.resize(680, 650)

    def setGuidance(self, summary: RegionalGuidance | None) -> None:
        self.panel.setGuidance(summary)


class GuidanceStrip(QtWidgets.QFrame):
    """Render regional TOI without deriving it from a point profile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary = RegionalGuidance.unavailable()
        self.bg = QtGui.QColor(colors.BG_COLOR)
        self.fg = QtGui.QColor(colors.FG_COLOR)
        self._apply_palette()
        self.title_font = QtGui.QFont("Helvetica")
        self.title_font.setPixelSize(11)
        self.title_font.setBold(True)
        self.label_font = QtGui.QFont("Helvetica")
        self.label_font.setPixelSize(11)
        self.label_font.setBold(True)
        self.value_font = QtGui.QFont("Helvetica")
        self.value_font.setPixelSize(13)
        self.value_font.setBold(True)
        self.detail_font = QtGui.QFont("Helvetica")
        self.detail_font.setPixelSize(9)
        self.state_font = QtGui.QFont("Helvetica")
        self.state_font.setPixelSize(8)
        self.state_font.setBold(True)
        strategy = (
            QtGui.QFont.StyleStrategy.PreferAntialias
            | QtGui.QFont.StyleStrategy.PreferQuality
        )
        for font in (
            self.title_font,
            self.label_font,
            self.value_font,
            self.detail_font,
            self.state_font,
        ):
            font.setStyleStrategy(strategy)
        self.setMinimumHeight(86)
        self.setMaximumHeight(96)
        self.setAccessibleName("Regional tornado guidance")
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)
        self.plotData()

    def _apply_palette(self) -> None:
        palette = colors.semantic_palette(self.bg.name(), self.fg.name())
        self.rule = QtGui.QColor(palette["rule"])
        self.header = QtGui.QColor(palette["header"])
        self.disclaimer = QtGui.QColor(palette["orange"])
        self.state_colors = {
            GuidanceState.UNAVAILABLE: QtGui.QColor(palette["marker_gray"]),
            GuidanceState.EXTERNAL: QtGui.QColor(palette["cyan"]),
            GuidanceState.PROXY: QtGui.QColor(palette["yellow"]),
            GuidanceState.EXPERIMENTAL: QtGui.QColor(palette["magenta"]),
            GuidanceState.OFFICIAL: QtGui.QColor(palette["green"]),
        }
        self.setStyleSheet(
            f"QFrame {{ background-color: {self.bg.name()}; "
            "border: 0px; margin: 0px; }"
        )

    def setGuidance(self, summary: RegionalGuidance) -> None:
        self.summary = summary
        self.clearData()
        self.plotData()
        self.update()

    def setPreferences(self, update_gui: bool = True, **prefs) -> None:
        if "bg_color" in prefs:
            self.bg = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg = QtGui.QColor(prefs["fg_color"])
        self._apply_palette()
        if update_gui:
            self.clearData()
            self.plotData()
            self.update()

    def display_cells(self) -> tuple[GuidanceDisplayCell, ...]:
        return guidance_display_cells(self.summary)

    def clearData(self) -> None:
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.clearData()
        self.plotData()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        try:
            painter.setClipRect(self.rect())
            painter.drawPixmap(0, 0, self.plotBitMap)
        finally:
            painter.end()

    @staticmethod
    def _elide(metrics: QtGui.QFontMetrics, text: str, width: int) -> str:
        return metrics.elidedText(text or "--", QtCore.Qt.ElideRight, max(1, width))

    def plotData(self) -> None:
        width, height = self.plotBitMap.width(), self.plotBitMap.height()
        if width < 8 or height < 8:
            return
        painter = QtGui.QPainter(self.plotBitMap)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            painter.setClipRect(QtCore.QRect(0, 0, width, height))
            painter.fillRect(QtCore.QRect(0, 0, width, height), self.bg)
            painter.setPen(QtGui.QPen(self.rule, 1))
            painter.drawLine(0, 0, width, 0)

            margin = 8
            header_height = 21
            painter.setFont(self.title_font)
            painter.setPen(QtGui.QPen(self.header, 1))
            title_rect = QtCore.QRect(margin, 1, width // 2, header_height)
            painter.drawText(
                title_rect,
                int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                "REGIONAL TORNADO GUIDANCE",
            )
            status_text = (
                "EXPERIMENTAL - NOT OFFICIAL SPC GUIDANCE"
                if self.summary.experimental_not_official
                else "EMBEDDED REGIONAL GUIDANCE"
            )
            painter.setPen(QtGui.QPen(self.disclaimer, 1))
            status_rect = QtCore.QRect(
                width // 2, 1, width // 2 - margin, header_height
            )
            painter.drawText(
                status_rect,
                int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter),
                status_text,
            )

            top = header_height + 1
            cells = self.display_cells()
            cell_width = (width - 2 * margin) / max(1, len(cells))
            for index, cell in enumerate(cells):
                left = int(margin + index * cell_width)
                right = int(margin + (index + 1) * cell_width)
                inner_left = left + 7
                inner_width = max(1, right - inner_left - 7)
                if index:
                    painter.setPen(QtGui.QPen(self.rule, 1))
                    painter.drawLine(left, top + 3, left, height - 5)

                color = self.state_colors[cell.state]
                painter.setFont(self.state_font)
                state_metrics = QtGui.QFontMetrics(self.state_font)
                state_text = cell.state.value.upper()
                state_width = min(
                    inner_width // 2,
                    state_metrics.horizontalAdvance(state_text) + 4,
                )
                state_rect = QtCore.QRect(
                    inner_left + inner_width - state_width,
                    top + 1,
                    state_width,
                    17,
                )
                painter.setFont(self.label_font)
                painter.setPen(QtGui.QPen(self.header, 1))
                label_metrics = QtGui.QFontMetrics(self.label_font)
                label_rect = QtCore.QRect(
                    inner_left,
                    top + 1,
                    max(1, inner_width - state_width - 8),
                    17,
                )
                painter.drawText(
                    label_rect,
                    int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                    self._elide(label_metrics, cell.label, label_rect.width()),
                )
                painter.setFont(self.state_font)
                painter.setPen(QtGui.QPen(color, 1))
                painter.drawText(
                    state_rect,
                    int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter),
                    state_text,
                )

                painter.setFont(self.value_font)
                painter.setPen(QtGui.QPen(color, 1))
                metrics = QtGui.QFontMetrics(self.value_font)
                value = self._elide(metrics, cell.value, inner_width)
                painter.drawText(
                    QtCore.QRect(inner_left, top + 19, inner_width, 21),
                    int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                    value,
                )

                painter.setFont(self.detail_font)
                painter.setPen(QtGui.QPen(self.fg, 1))
                detail_metrics = QtGui.QFontMetrics(self.detail_font)
                detail = self._elide(detail_metrics, cell.detail, inner_width)
                painter.drawText(
                    QtCore.QRect(inner_left, top + 41, inner_width, 17),
                    int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                    detail,
                )
        finally:
            painter.end()


__all__ = [
    "GuidanceDisplayCell",
    "GuidanceStrip",
    "TOIExplanationDialog",
    "TOIExplanationPanel",
    "TOIExplanationRow",
    "guidance_display_cells",
    "toi_explanation_rows",
    "toi_probability_is_supported",
    "toi_probability_tier",
]

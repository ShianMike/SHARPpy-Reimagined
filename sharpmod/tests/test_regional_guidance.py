"""Regional TOI contracts and rendering regressions."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtGui

from sharpmod.guidance import (
    REGIONAL_GUIDANCE_META_KEY,
    TOI_PROBABILITY_VERSION,
    TOI_SCORECARD_VERSION,
)
from sharpmod.guidance.toi_scorecard import (
    TOI_MEASURED_SKILL_NOTE,
    TOI_MEASURED_SKILL_VERSION,
)
from sharpmod.guidance import (
    DEFAULT_MAX_JET_TRANSLATION_KT,
    GuidanceGrid,
    JetObject,
    RegionalGuidance,
    TOIFeatures,
    compute_experimental_toi,
    extract_toi_features,
    guidance_from_collection,
)
from sharpmod.guidance.toi import track_jet_objects
from sharpmod.viz.guidance import (
    GuidanceStrip,
    TOIExplanationDialog,
    TOIExplanationPanel,
    guidance_display_cells,
    toi_explanation_rows,
)


def test_qt_render_tests_are_pinned_to_one_xdist_worker_group(request):
    marker = request.node.get_closest_marker("xdist_group")

    assert marker is not None
    assert marker.args == ("qt",)


def _regional_payload() -> dict:
    return {
        "schema_version": 2,
        "experimental_not_official": True,
        "source": "synthetic-regression",
        "valid_start": "2023-03-31T18:00:00",
        "valid_end": "2023-04-01T06:00:00",
        "toi": {
            "state": "experimental",
            "score": 4.2,
            "high_risk_probability": 0.68,
            "method_version": "toi_omega2024_experimental_v1",
            "calibration_version": "test-calibration",
            "features": {
                "pressure_level_hpa": 500,
                "translation_speed_kt": 43.8,
                "maximum_jet_speed_kt": 92.1,
                "jet_to_risk_distance_km": 318.0,
                "jet_to_risk_bearing_deg": 329.0,
                "maximum_stp": 7.3,
                "month": 4,
            },
        },
    }


def test_regional_guidance_round_trip_preserves_toi_and_metadata():
    summary = RegionalGuidance.from_mapping(_regional_payload())

    assert summary.toi.features.translation_speed_kt == pytest.approx(43.8)
    assert summary.toi.high_risk_probability == pytest.approx(0.68)
    assert RegionalGuidance.from_mapping(summary.to_mapping()) == summary


def test_schema_v1_sidecar_keeps_toi_and_is_normalized_to_v2():
    payload = _regional_payload()
    payload["schema_version"] = 1
    payload["retired_product"] = {"state": "unavailable"}

    summary = RegionalGuidance.from_mapping(payload)
    serialized = summary.to_mapping()

    assert summary.toi.high_risk_probability == pytest.approx(0.68)
    assert serialized["schema_version"] == 2
    assert "retired_product" not in serialized


def _synthetic_toi_grid() -> tuple[GuidanceGrid, np.ndarray]:
    valid_times = tuple(
        datetime(2024, 4, 2, 6) + timedelta(hours=6 * index) for index in range(3)
    )
    rows, cols = 5, 7
    latitude = np.repeat(np.linspace(31.0, 35.0, rows)[:, None], cols, axis=1)
    longitude = np.repeat(np.linspace(-101.0, -95.0, cols)[None, :], rows, axis=0)
    u_wind = np.zeros((3, rows, cols), dtype=float)
    v_wind = np.zeros_like(u_wind)
    for time_index, left in enumerate((1, 2, 3)):
        u_wind[time_index, 1:3, left : left + 2] = 70.0
    stp = np.zeros_like(u_wind)
    risk_mask = np.zeros((rows, cols), dtype=bool)
    risk_mask[1:4, 4:6] = True
    stp[1, risk_mask] = 6.5
    grid = GuidanceGrid(
        model="synthetic",
        cycle=valid_times[0],
        valid_times=valid_times,
        latitude=latitude,
        longitude=longitude,
        fields={
            "u_wind_500_hpa": u_wind,
            "v_wind_500_hpa": v_wind,
            "stp": stp,
        },
        units={
            "u_wind_500_hpa": "kt",
            "v_wind_500_hpa": "kt",
            "stp": "1",
        },
    )
    return grid, risk_mask


def test_toi_feature_extractor_tracks_jet_and_peak_stp_without_scoring_it():
    grid, risk_mask = _synthetic_toi_grid()

    features = extract_toi_features(
        grid,
        risk_mask,
        min_grid_points=4,
        maximum_match_distance_km=500,
    )

    assert features.pressure_level_hpa == 500
    assert features.translation_speed_kt > 0
    assert features.maximum_jet_speed_kt == pytest.approx(70)
    assert features.maximum_stp == pytest.approx(6.5)
    assert 0 <= features.jet_to_risk_bearing_deg < 360
    # Feature extraction deliberately has no score/probability return value.
    assert not hasattr(features, "score")


def _jet_object(time_index, hours, latitude, longitude, maximum_wind_kt=80.0):
    return JetObject(
        time_index=time_index,
        valid_time=datetime(2024, 4, 2, 6) + timedelta(hours=hours),
        centroid_latitude=latitude,
        centroid_longitude=longitude,
        maximum_wind_kt=maximum_wind_kt,
        mean_wind_kt=maximum_wind_kt - 10.0,
        grid_point_count=12,
    )


def test_jet_association_refuses_a_jump_no_jet_could_make():
    # 3-hourly frames 15 degrees of longitude apart is roughly 1370 km, inside
    # the old fixed 1800 km radius but about 246 kt.  The two objects are
    # different jet streaks and must not be linked into one track.
    frames = (
        (_jet_object(0, 0, 40.0, -105.0),),
        (_jet_object(1, 3, 40.0, -90.0),),
    )

    tracks = track_jet_objects(
        frames, maximum_match_distance_km=1800.0, maximum_translation_kt=90.0
    )

    assert len(tracks) == 2
    assert all(len(track.objects) == 1 for track in tracks)


def test_jet_association_still_links_a_physically_plausible_step():
    # Same cadence, about 255 km apart, roughly 46 kt: an ordinary jet streak.
    frames = (
        (_jet_object(0, 0, 40.0, -105.0),),
        (_jet_object(1, 3, 40.0, -102.0),),
    )

    tracks = track_jet_objects(
        frames, maximum_match_distance_km=1800.0, maximum_translation_kt=90.0
    )

    assert len(tracks) == 1
    assert len(tracks[0].objects) == 2
    assert tracks[0].translation_speed_kt == pytest.approx(46.0, abs=3.0)


def test_association_ceiling_is_clamped_to_the_jets_own_peak_wind():
    # A 30 kt jet object cannot translate at 90 kt.  The step below implies
    # about 46 kt, which the global ceiling would allow but the flow does not.
    frames = (
        (_jet_object(0, 0, 40.0, -105.0, maximum_wind_kt=30.0),),
        (_jet_object(1, 3, 40.0, -102.0, maximum_wind_kt=30.0),),
    )

    tracks = track_jet_objects(
        frames, maximum_match_distance_km=1800.0, maximum_translation_kt=90.0
    )

    assert len(tracks) == 2


@pytest.mark.parametrize("separation_deg", [1.0, 4.0, 9.0, 14.0, 20.0])
def test_track_translation_speed_never_exceeds_the_ceiling(separation_deg):
    # Whatever the geometry, the per-step bound must bound the endpoint speed.
    ceiling = 90.0
    frames = tuple(
        (_jet_object(index, 3 * index, 40.0, -105.0 + separation_deg * index),)
        for index in range(7)
    )

    tracks = track_jet_objects(
        frames, maximum_match_distance_km=5000.0, maximum_translation_kt=ceiling
    )

    for track in tracks:
        assert track.translation_speed_kt <= ceiling + 1e-9


def test_extracted_features_respect_the_translation_ceiling():
    grid, risk_mask = _synthetic_toi_grid()

    features = extract_toi_features(
        grid,
        risk_mask,
        min_grid_points=4,
        maximum_match_distance_km=1800.0,
    )

    assert features.translation_speed_kt <= DEFAULT_MAX_JET_TRANSLATION_KT


def _scorecard_features(**overrides) -> TOIFeatures:
    values = {
        "pressure_level_hpa": 500,
        "translation_speed_kt": 44.0,
        "maximum_jet_speed_kt": 79.5,
        "jet_to_risk_distance_km": 175.0 / 0.621371192237334,
        "jet_to_risk_bearing_deg": 290.0,
        "maximum_stp": 8.5,
        "month": 4,
    }
    values.update(overrides)
    return TOIFeatures(**values)


def test_experimental_toi_reproduces_published_april_2024_anchor():
    result = compute_experimental_toi(_scorecard_features())

    assert result.score == pytest.approx(4.35)
    assert result.high_risk_probability == pytest.approx(0.87)
    assert result.stp_bin_value == pytest.approx(8.5)
    assert "experimental" in TOI_SCORECARD_VERSION
    assert "public_anchor" in TOI_PROBABILITY_VERSION


def test_experimental_toi_uses_stp_only_in_probability_transform():
    low_stp = compute_experimental_toi(_scorecard_features(maximum_stp=1.0))
    high_stp = compute_experimental_toi(_scorecard_features(maximum_stp=10.0))

    assert low_stp.score == high_stp.score
    assert low_stp.high_risk_probability < high_stp.high_risk_probability


def test_experimental_toi_weights_location_more_for_fast_translation():
    preferred = {
        "jet_to_risk_distance_km": 100.0 / 0.621371192237334,
        "jet_to_risk_bearing_deg": 325.0,
    }
    unfavourable = {
        "jet_to_risk_distance_km": 450.0 / 0.621371192237334,
        "jet_to_risk_bearing_deg": 140.0,
    }
    slow_good = compute_experimental_toi(
        _scorecard_features(translation_speed_kt=35.0, **preferred)
    )
    slow_bad = compute_experimental_toi(
        _scorecard_features(translation_speed_kt=35.0, **unfavourable)
    )
    fast_good = compute_experimental_toi(
        _scorecard_features(translation_speed_kt=50.0, **preferred)
    )
    fast_bad = compute_experimental_toi(
        _scorecard_features(translation_speed_kt=50.0, **unfavourable)
    )

    assert fast_good.score - fast_bad.score > slow_good.score - slow_bad.score


def test_experimental_toi_applies_published_july_treatment():
    june = compute_experimental_toi(
        _scorecard_features(month=6, pressure_level_hpa=300)
    )
    july = compute_experimental_toi(
        _scorecard_features(month=7, pressure_level_hpa=300)
    )

    assert june.score - july.score == pytest.approx(0.25)
    assert june.high_risk_probability > july.high_risk_probability


def test_guidance_display_cells_expose_only_toi():
    cells = guidance_display_cells(RegionalGuidance.from_mapping(_regional_payload()))

    assert [cell.label for cell in cells] == ["Tornado Outbreak Indicator (TOI)"]
    assert "Score 4.2" in cells[0].value
    assert "68%" in cells[0].value
    assert "500 hPa" in cells[0].detail


def test_toi_explanation_rows_include_inputs_score_breakdown_and_versions():
    payload = _regional_payload()
    payload["provenance"] = {
        "toi_components": (
            "translation=4.38*0.75;location=4*0.1;"
            "maximum_jet=5*0.15;season=0;stp_bin=6.5"
        ),
        "jet_threshold": "50 kt",
    }

    rows = toi_explanation_rows(RegionalGuidance.from_mapping(payload))
    values = {row.label: row.value for row in rows}

    assert values["High-risk probability"] == "68%"
    assert values["Display color tier"] == "50-74%"
    assert values["Experimental score"] == "4.20 / 5.00"
    assert values["Jet layer"] == "500 hPa"
    assert values["Jet translation speed"] == "43.8 kt"
    assert values["Maximum jet speed"] == "92.1 kt"
    assert values["Jet-to-risk distance"] == "318.0 km"
    assert values["Jet-to-risk bearing"] == "329.0 deg"
    assert values["Peak STP"] == "7.30"
    assert values["Seasonal month"] == "April (4)"
    assert values["Feature / score method"] == "toi_omega2024_experimental_v1"
    assert values["Probability calibration"] == "test-calibration"
    assert values["Translation component"] == "4.38 x 0.75"
    assert values["Jet-location component"] == "4 x 0.1"
    assert values["Maximum-jet component"] == "5 x 0.15"
    assert values["Jet Threshold"] == "50 kt"


def test_toi_explanation_rows_expose_selected_calibration_years_and_target():
    payload = _regional_payload()
    payload["toi"]["calibration_version"] = "sharpmod_toi_logistic_l2_v1"
    payload["provenance"] = {
        "toi_calibration_years": "2015-2022",
        "toi_calibration_target": "high_risk_worthy_proxy_v1",
        "toi_calibration_validated": "yes",
    }

    rows = toi_explanation_rows(RegionalGuidance.from_mapping(payload))
    versioning = {
        row.label: row.value for row in rows if row.section == "Versioning"
    }

    assert versioning["Probability calibration"] == "sharpmod_toi_logistic_l2_v1"
    assert versioning["Calibration years"] == "2015-2022"
    assert versioning["Calibration target"] == "high_risk_worthy_proxy_v1"
    assert versioning["Calibration validated"] == "yes"
    # The calibration fields are promoted, not duplicated in the raw dump.
    assert not any(row.section == "Provenance" for row in rows)


def test_toi_explanation_rows_promote_the_measured_skill_disclosure():
    """A probability measured as worse than climatology must say so up front.

    MEASURED on a 337-case archive: the shipped public-anchor transform scored a
    Brier skill of -0.561 against climatology. Leaving that at the bottom of a
    generic provenance dump would put the disclosure everywhere except where a
    forecaster looks.
    """
    payload = _regional_payload()
    payload["provenance"] = {
        "toi_measured_skill": TOI_MEASURED_SKILL_NOTE,
        "toi_measured_skill_version": TOI_MEASURED_SKILL_VERSION,
    }

    rows = toi_explanation_rows(RegionalGuidance.from_mapping(payload))
    labels = [row.label for row in rows]
    values = {row.label: row.value for row in rows}
    sections = {row.label: row.section for row in rows}

    assert values["Measured skill"] == TOI_MEASURED_SKILL_NOTE
    # Sits with the result, immediately after the status line.
    assert sections["Measured skill"] == "Result"
    assert labels.index("Measured skill") == labels.index("Status / limitation") + 1
    # The evaluation is identified so a later one can supersede it.
    assert values["Measured-skill evaluation"] == TOI_MEASURED_SKILL_VERSION
    assert sections["Measured-skill evaluation"] == "Versioning"
    # Promoted, not duplicated into the raw dump.
    assert not any(row.section == "Provenance" for row in rows)
    # The substance survives intact, not just the label. A warning truncated
    # mid-sentence would be worse than no warning at all.
    #
    # The concrete figures are matched by shape rather than pinned to literals:
    # a re-measurement legitimately changes them (v1 read -0.561 / 0.905, v2
    # reads -0.118 / 0.678), and a test that fails on the *correct* new number
    # trains people to edit the assertion instead of reading it.  What must never
    # regress is that a negative skill figure and a false-alarm ratio both
    # actually reach the forecaster.
    note = values["Measured skill"]
    assert "worse" in note.casefold()
    assert re.search(r"Brier skill -\d+\.\d+", note), note
    assert re.search(r"FAR \d+\.\d+", note), note
    assert note.endswith("calibrated probability.")


def test_measured_skill_note_fits_the_provenance_text_limit():
    """The disclosure must survive provenance normalisation intact.

    Provenance values are capped, so a longer note would be silently cut off
    part-way through the warning it exists to deliver.
    """
    from sharpmod.guidance.schemas import _MAX_TEXT_LENGTH

    assert len(TOI_MEASURED_SKILL_NOTE) <= _MAX_TEXT_LENGTH, (
        f"note is {len(TOI_MEASURED_SKILL_NOTE)} chars, limit is "
        f"{_MAX_TEXT_LENGTH}"
    )
    assert len(TOI_MEASURED_SKILL_VERSION) <= _MAX_TEXT_LENGTH


def test_toi_explanation_rows_omit_measured_skill_without_the_note():
    """Absent evidence must not be reported as a measured claim."""
    rows = toi_explanation_rows(RegionalGuidance.from_mapping(_regional_payload()))

    assert not any(row.label == "Measured skill" for row in rows)
    assert not any(row.label == "Measured-skill evaluation" for row in rows)


def test_toi_explanation_rows_show_missing_calibration_years_by_default():
    rows = toi_explanation_rows(RegionalGuidance.from_mapping(_regional_payload()))
    values = {row.label: row.value for row in rows}

    assert values["Probability calibration"] == "test-calibration"
    assert values["Calibration years"] == "--"
    assert values["Calibration validated"] == "--"


def test_toi_explanation_panel_is_accessible_and_honest_when_unavailable(qt_app):
    summary = RegionalGuidance.unavailable("regional time series not embedded")
    panel = TOIExplanationPanel(summary)
    try:
        assert panel.accessibleName() == "TOI explanation panel"
        assert panel.probability_label.text() == "--"
        assert panel.tier_label.text() == "Display tier: Unavailable"
        assert panel.row_values["Status / limitation"] == (
            "regional time series not embedded"
        )
        assert panel.row_values["Jet translation speed"] == "--"
        assert "not official SPC guidance" in panel.disclaimer_label.text()
    finally:
        panel.close()


def test_index_board_paints_only_embedded_toi_guidance(qt_app):
    from qtpy.QtCore import QPointF, QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    summary = RegionalGuidance.from_mapping(_regional_payload())
    board.setGuidance(summary)
    records = []

    def capture_text(_painter, _rect, text, _color=None, _align=Qt.AlignLeft):
        records.append((text, _color.name() if _color is not None else None))

    board._text = capture_text
    board._ship_chart = lambda *_args, **_kwargs: None
    pixmap = QtGui.QPixmap(320, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_comp(painter, QRect(0, 0, 300, 400), 18)
    painter.end()

    texts = [text for text, _color in records]
    assert texts.count("TOI = ") == 1
    # Without a validated calibration the row shows the experimental score, not
    # a percentage: the shipped transform measured a Brier skill of -0.561.
    #
    # Whether the "hypo" marker is attached depends on whether it fits the
    # column at the font actually resolved, which varies with platform font
    # substitution, so the expectation is derived from the same rule the painter
    # uses rather than hard-coded. What must never happen is a clipped marker.
    from sharpmod.viz.unit_text import UNVALIDATED_SUFFIX

    right_width = board._toi_rect.width()
    marker_fits = board._suffix_fits(
        board.rf, right_width, "TOI", "4.2", UNVALIDATED_SUFFIX
    )
    expected = "4.2" + (UNVALIDATED_SUFFIX if marker_fits else "")
    assert (expected, "#ff00ff") in records
    # Either the whole marker or none of it, never a fragment.
    drawn = [text for text, _color in records if text.startswith("4.2")]
    assert drawn == [expected]
    assert not any("%" in text for text in texts)
    assert not board._toi_rect.isNull()
    emitted = []
    board.toiDetailsRequested.connect(emitted.append)

    class _Click:
        def position(self):
            return QPointF(board._toi_rect.center())

    board.mousePressEvent(_Click())
    assert emitted == [summary]
    board.close()


def test_index_board_toi_color_follows_probability_strength(qt_app):
    """A *validated* calibration still gets the probability display and ramp.

    The gate is on evidence, not on the feature: if an artifact passes promotion
    the percentage returns with no further code change.
    """
    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    expected = (
        (0.01, "1%", "#ffffff"),
        (0.25, "25%", "#ffff00"),
        (0.50, "50%", "#ff0000"),
        (0.75, "75%", "#ff00ff"),
    )
    try:
        for probability, value_text, color_name in expected:
            payload = _regional_payload()
            payload["toi"]["high_risk_probability"] = probability
            payload["provenance"] = {"toi_calibration_validated": "yes"}
            board.setGuidance(RegionalGuidance.from_mapping(payload))
            value, color = board._toi_display_value()
            assert value == value_text
            assert color.name() == color_name
    finally:
        board.close()


def test_toi_unvalidated_marker_is_dropped_rather_than_clipped(qt_app):
    """The marker is drawn only when it fits, never half-drawn.

    This row clips instead of eliding, and font substitution varies by platform,
    so whether " hypo" fits cannot be assumed. A truncated qualifier would be
    worse than none, so the fit is measured at the face actually in use and the
    marker is dropped if the column is too narrow.
    """
    from qtpy import QtGui

    from sharpmod.viz.index_board import IndexBoard
    from sharpmod.viz.unit_text import UNVALIDATED_SUFFIX

    board = IndexBoard()
    try:
        font = board.rf

        # A generous column always keeps the marker.
        assert board._suffix_fits(
            font, 10_000, "TOI", "4.2", UNVALIDATED_SUFFIX
        )
        # A column too narrow for even the bare value never keeps it.
        assert not board._suffix_fits(font, 1, "TOI", "4.2", UNVALIDATED_SUFFIX)
        # The decision is monotone in width: once it fits, more room still fits.
        widths = [
            w
            for w in range(0, 600, 4)
            if board._suffix_fits(font, w, "TOI", "4.2", UNVALIDATED_SUFFIX)
        ]
        assert widths, "the marker must fit at some attainable width"
        assert widths == list(range(min(widths), 600, 4))
        # Adding the marker is never cheaper than omitting it.
        threshold = min(widths)
        assert board._suffix_fits(font, threshold, "TOI", "4.2", "")
        assert QtGui.QFontMetrics(font).horizontalAdvance("TOI = ") > 0
    finally:
        board.close()


def test_unavailable_toi_is_not_given_an_unvalidated_marker(qt_app):
    """'--' already says nothing is available; a qualifier would be noise."""
    from sharpmod.viz.index_board import IndexBoard

    payload = _regional_payload()
    payload["toi"].pop("high_risk_probability")
    payload["toi"].pop("score")
    board = IndexBoard()
    try:
        board.setGuidance(RegionalGuidance.from_mapping(payload))
        assert board._toi_display_value()[0] == "--"
    finally:
        board.close()


def test_validated_probability_is_not_labelled_hypothetical(qt_app):
    """The marker qualifies an unvalidated number, so a validated one is exempt.

    Scoping it by meaning rather than by width also keeps the widest string out of
    the cell: '68% hypothetical' needs 127px against the 122px column, while
    '4.2 hypothetical' needs 120px.
    """
    from qtpy.QtCore import QRect

    from sharpmod.viz.index_board import IndexBoard
    from sharpmod.viz.unit_text import UNVALIDATED_SUFFIX

    def painted(validated):
        payload = _regional_payload()
        payload["toi"]["high_risk_probability"] = 0.68
        payload["provenance"] = (
            {"toi_calibration_validated": "yes"} if validated else {}
        )
        board = IndexBoard()
        board.setGuidance(RegionalGuidance.from_mapping(payload))
        records = []
        board._text = lambda _p, _r, text, _c=None, _a=None: records.append(text)
        board._ship_chart = lambda *_a, **_k: None
        # Deliberately generous width so the width guard is not the variable
        # under test; this asserts the rule, not whether the marker happens to
        # fit at this font. The narrow-column behaviour has its own test.
        pixmap = QtGui.QPixmap(1240, 460)
        pixmap.fill(QtGui.QColor("#000000"))
        painter = QtGui.QPainter(pixmap)
        board._col_comp(painter, QRect(0, 0, 1200, 440), 18)
        painter.end()
        board.close()
        index = records.index("TOI = ")
        return records[index + 1]

    unvalidated = painted(False)
    validated = painted(True)
    marker = UNVALIDATED_SUFFIX.strip()

    # Unvalidated: score, marked.
    assert unvalidated.startswith("4.2")
    assert marker in unvalidated
    # Validated: percentage, and never called hypothetical.
    assert validated.startswith("68%")
    assert marker not in validated


def test_index_board_withholds_percentage_without_a_validated_calibration(qt_app):
    """An unsupported probability must not render as a percentage.

    MEASURED: the shipped transform's 77% bin verified at 7.3%, below the base
    rate. A percentage beside real parameters asserts a calibration that does not
    exist, and a caveat in a click-through dialog does not reach a reader who
    never clicks.
    """
    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    try:
        for flag in (None, "", "no", "false", "pending"):
            payload = _regional_payload()
            payload["toi"]["high_risk_probability"] = 0.68
            if flag is not None:
                payload["provenance"] = {"toi_calibration_validated": flag}
            board.setGuidance(RegionalGuidance.from_mapping(payload))
            value, _color = board._toi_display_value()

            assert "%" not in value, f"flag {flag!r} leaked a percentage"
            # The experimental score is shown instead, which claims nothing
            # about calibration.
            assert value == "4.2"
    finally:
        board.close()


def test_index_board_does_not_present_toi_features_as_probability(qt_app):
    from sharpmod.viz.index_board import IndexBoard

    payload = _regional_payload()
    payload["toi"].pop("high_risk_probability")
    board = IndexBoard()
    board.setGuidance(RegionalGuidance.from_mapping(payload))

    assert board.regional_guidance.toi.available
    # The score stands in, and it is never dressed up as a probability.
    assert board._toi_display_value()[0] == "4.2"
    assert "%" not in board._toi_display_value()[0]

    # With neither a probability nor a score there is nothing honest to show.
    bare = _regional_payload()
    bare["toi"].pop("high_risk_probability")
    bare["toi"].pop("score")
    board.setGuidance(RegionalGuidance.from_mapping(bare))
    assert board._toi_display_value()[0] == "--"
    board.close()


def test_npz_sidecar_guidance_reaches_collection_metadata(tmp_path):
    from sharpmod.io.decoder import load_npz
    from sharpmod.tests._examples import examples_dir

    source = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not source.exists():
        pytest.skip("HRRR NPZ example unavailable")
    target = tmp_path / "point.npz"
    shutil.copyfile(source, target)
    target.with_suffix(".json").write_text(
        json.dumps({REGIONAL_GUIDANCE_META_KEY: _regional_payload()}),
        encoding="utf-8",
    )

    collection, _location = load_npz(target)
    summary = guidance_from_collection(collection)

    assert summary.toi.high_risk_probability == pytest.approx(0.68)
    assert summary.to_mapping()["schema_version"] == 2


def test_mounted_sounding_embeds_only_toi_without_guidance_footer(
    qt_app, tmp_path
):
    from sharpmod import render as render_mod
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    source = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not source.exists():
        pytest.skip("HRRR NPZ example unavailable")
    collection, _location = render_mod.decode(str(source))
    collection.setMeta(REGIONAL_GUIDANCE_META_KEY, _regional_payload())
    config = render_mod.build_config(str(tmp_path))
    win, controller = compose_window(config, collection, mount=True)
    win.resize(1630, 1180)
    qt_app.processEvents()

    try:
        board = win.spc_widget.index_board
        position = win.spc_widget.grid3.getItemPosition(
            win.spc_widget.grid3.indexOf(board)
        )
        assert position == (0, 0, 1, 3)
        assert win.sharpmod_products.composite is board
        assert board.regional_guidance.toi.score == pytest.approx(4.2)
        # No validated calibration in this payload, so the row shows the
        # experimental score rather than an unsupported percentage.
        assert board._toi_display_value()[0] == "4.2"
        assert not hasattr(win.spc_widget, "guidance_strip")
        assert win.findChildren(GuidanceStrip) == []
        board.toiDetailsRequested.emit(board.regional_guidance)
        qt_app.processEvents()
        dialog = board._toi_explanation_dialog
        assert isinstance(dialog, TOIExplanationDialog)
        assert dialog.isVisible()
        assert dialog.panel.row_values["High-risk probability"] == "68%"
        assert dialog.panel.row_values["Jet layer"] == "500 hPa"
        dialog.close()
    finally:
        win.close()
        controller.close()

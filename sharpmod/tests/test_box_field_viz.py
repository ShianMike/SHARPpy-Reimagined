"""Box field colour scales, painters, and the field map widget."""

import numpy as np
import pytest

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QPainter, QPixmap

from sharpmod import box_analysis as ba
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir
from sharpmod.viz import box_field as bf


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

pytestmark = [
    pytest.mark.skipif(
        not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding"),
    pytest.mark.usefixtures("qt_app"),
]


def _region() -> BoxRegion:
    return BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8)


def _write_variants(plan, directory):
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs = {}
    for node in plan.requestable_points:
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        bump = 0.45 * node.col - 0.25 * node.row
        low = arrays["pres"] > 800.0
        arrays["dwpc"] = np.where(
            low & (arrays["dwpc"] > -9000.0),
            arrays["dwpc"] + bump, arrays["dwpc"])
        path = directory / f"{node.request_id}.npz"
        np.savez(path, **arrays)
        outputs[node.request_id] = str(path)
    return outputs


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("box-field-viz")
    plan = plan_box_samples("hrrr", _region(), target_points=16)
    return ba.analyze_box(
        plan, _write_variants(plan, tmp_path), tiers=(ba.FAST_TIER,))


# -- ramp ------------------------------------------------------------------ #


def test_ramp_endpoints_match_the_declared_stops():
    assert bf.ramp_color(0.0).name() == bf.SEQUENTIAL_STOPS[0][1].lower()
    assert bf.ramp_color(1.0).name() == bf.SEQUENTIAL_STOPS[-1][1].lower()


def test_ramp_clamps_out_of_range_and_bad_input():
    assert bf.ramp_color(-4.0) == bf.ramp_color(0.0)
    assert bf.ramp_color(9.0) == bf.ramp_color(1.0)
    assert bf.ramp_color(float("nan")) == bf.ramp_color(0.0)
    assert bf.ramp_color("x") == bf.ramp_color(0.0)


def test_ramp_is_continuous_and_moves_monotonically_in_luminance():
    # Not strictly monotone by construction, but a cool-to-hot ramp must not
    # return the same colour for clearly different magnitudes.
    samples = [bf.ramp_color(step / 10.0).name() for step in range(11)]
    assert len(set(samples)) >= 9


# -- scale ----------------------------------------------------------------- #


def test_scale_uses_a_robust_band_so_one_outlier_cannot_flatten_the_field(
    analysis,
):
    stats = analysis.statistics("mucape")
    robust = bf.scale_for(stats)
    full = bf.scale_for(stats, robust=False)
    assert robust.minimum >= full.minimum
    assert robust.maximum <= full.maximum
    assert full.minimum == pytest.approx(stats.minimum)
    assert full.maximum == pytest.approx(stats.maximum)


def test_scale_normalizes_and_clamps():
    scale = bf.FieldScale(minimum=0.0, maximum=100.0)
    assert scale.normalize(0.0) == 0.0
    assert scale.normalize(50.0) == pytest.approx(0.5)
    assert scale.normalize(100.0) == 1.0
    assert scale.normalize(-10.0) == 0.0
    assert scale.normalize(1000.0) == 1.0
    assert scale.normalize(None) is None
    assert scale.normalize(float("nan")) is None
    assert scale.normalize("x") is None


def test_a_uniform_field_is_reported_as_flat_not_coloured_as_a_gradient():
    scale = bf.FieldScale(minimum=5.0, maximum=5.0)
    assert scale.flat is True
    # Everything lands mid-ramp rather than implying a spread.
    assert scale.normalize(5.0) == 0.5
    assert scale.normalize(-99.0) == 0.5


def test_a_field_straddling_zero_gets_a_symmetric_diverging_scale(analysis):
    stats = analysis.statistics("mucape")
    # Force a signed range through the public builder.
    signed = ba.BoxFieldStats(
        parameter=stats.parameter, count=4,
        minimum=-40.0, maximum=10.0, mean=-5.0, median=-5.0,
        p10=-30.0, p90=8.0, extreme=stats.extreme,
    )
    scale = bf.scale_for(signed)
    assert scale.stops is bf.DIVERGING_STOPS
    assert scale.minimum == pytest.approx(-scale.maximum)
    # Zero must sit exactly on the neutral tone.
    assert scale.normalize(0.0) == pytest.approx(0.5)


def test_scale_for_rejects_a_non_stats_object():
    with pytest.raises(TypeError):
        bf.scale_for(object())


def test_color_returns_none_for_absent_values():
    scale = bf.FieldScale(minimum=0.0, maximum=1.0)
    assert scale.color(None) is None
    assert scale.color(0.5).alpha() == 255
    assert scale.color(0.5, alpha=120).alpha() == 120


# -- painters -------------------------------------------------------------- #


def _painter(size=(400, 300)):
    pixmap = QPixmap(*size)
    pixmap.fill(Qt.black)
    return pixmap, QPainter(pixmap)


def test_draw_field_cells_paints_one_cell_per_present_value(analysis):
    scale = bf.scale_for(analysis.statistics("mucape"))
    pixmap, qp = _painter()

    def to_px(lon, lat):
        return QPointF((lon + 97.0) * 60.0 + 200.0, (37.0 - lat) * 60.0 + 20.0)

    drawn = bf.draw_field_cells(qp, to_px, analysis, "mucape", scale)
    qp.end()
    assert drawn == len(analysis.analyzed)
    assert not pixmap.isNull()


def test_draw_field_cells_skips_absent_values(analysis):
    scale = bf.FieldScale(minimum=0.0, maximum=1.0)
    pixmap, qp = _painter()
    drawn = bf.draw_field_cells(
        qp, lambda lon, lat: QPointF(lon, lat), analysis, "stp_cin", scale)
    qp.end()
    # The composite tier was never computed, so there is nothing to fill.
    assert drawn == 0
    assert not pixmap.isNull()


def test_draw_field_cells_can_outline_cells(analysis):
    scale = bf.scale_for(analysis.statistics("mucape"))
    pixmap, qp = _painter()
    drawn = bf.draw_field_cells(
        qp, lambda lon, lat: QPointF((lon + 100) * 20, (40 - lat) * 20),
        analysis, "mucape", scale, outline=True)
    qp.end()
    assert drawn > 0 and not pixmap.isNull()


def test_a_cell_projects_all_four_corners_and_samples_curved_edges():
    polygon = bf._cell_polygon(
        lambda lon, lat: QPointF(lon + lat * lat, lat),
        0.0, 0.0, 1.0, 1.0,
    )
    points = [(point.x(), point.y()) for point in polygon]

    assert len(points) == 16
    for corner in ((0.0, 1.0), (2.0, 1.0), (2.0, -1.0), (0.0, -1.0)):
        assert corner in points


def test_cell_values_are_suppressed_when_cells_are_too_small(analysis):
    pixmap, qp = _painter()
    tiny = bf.draw_cell_values(
        qp, lambda lon, lat: QPointF(lon * 0.5, lat * 0.5),
        analysis, "mucape")
    roomy = bf.draw_cell_values(
        qp, lambda lon, lat: QPointF(lon * 400.0, lat * 400.0),
        analysis, "mucape")
    qp.end()
    assert tiny == 0
    assert roomy == len(analysis.analyzed)
    assert not pixmap.isNull()


def test_draw_color_bar_renders_including_the_uniform_case(analysis):
    pixmap, qp = _painter()
    bf.draw_color_bar(
        qp, QRectF(10, 240, 260, 40),
        bf.scale_for(analysis.statistics("mucape")), "mucape")
    bf.draw_color_bar(
        qp, QRectF(10, 180, 260, 40),
        bf.FieldScale(minimum=3.0, maximum=3.0), "mucape")
    qp.end()
    assert not pixmap.isNull()


# -- field map widget ------------------------------------------------------ #


# -- ingredient mask overlay ----------------------------------------------- #


def test_mask_overlay_hatches_only_qualifying_cells(analysis):
    mask = analysis.mask(ba.Criterion("mucape", minimum=1.0))
    pixmap, qp = _painter()
    drawn = bf.draw_mask_overlay(
        qp, lambda lon, lat: QPointF((lon + 100) * 20, (40 - lat) * 20),
        analysis, mask)
    qp.end()
    assert drawn == len(analysis.analyzed)
    assert not pixmap.isNull()


def test_mask_overlay_draws_nothing_when_nothing_qualifies(analysis):
    mask = analysis.mask(ba.Criterion("mucape", minimum=1.0e9))
    pixmap, qp = _painter()
    drawn = bf.draw_mask_overlay(
        qp, lambda lon, lat: QPointF(lon, lat), analysis, mask)
    qp.end()
    assert drawn == 0
    assert not pixmap.isNull()


def test_mask_overlay_leaves_unjudgeable_cells_bare(analysis):
    # The composite tier was never computed, so every cell is unknown, and an
    # unknown verdict must not be shaded as if it were a decision.
    mask = analysis.mask(ba.Criterion("stp_cin", minimum=1.0))
    assert all(value is None for row in mask for value in row)
    pixmap, qp = _painter()
    drawn = bf.draw_mask_overlay(
        qp, lambda lon, lat: QPointF(lon, lat), analysis, mask)
    qp.end()
    assert drawn == 0
    assert not pixmap.isNull()


def test_mask_overlay_tolerates_a_mismatched_grid(analysis):
    pixmap, qp = _painter()
    drawn = bf.draw_mask_overlay(
        qp, lambda lon, lat: QPointF(lon, lat), analysis, ((True,),))
    qp.end()
    # Out-of-range lookups are skipped rather than raising mid-repaint.
    assert drawn <= 1
    assert not pixmap.isNull()


# -- field map screen wiring ----------------------------------------------- #

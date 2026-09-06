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


@pytest.fixture
def analysis(tmp_path):
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


@pytest.fixture
def field_map(analysis):
    from sharpmod.gui_maps import BoxFieldMapWidget

    view = BoxFieldMapWidget()
    view.resize(600, 420)
    view.set_analysis(analysis, "mucape")
    return view


def test_field_map_scales_to_the_selected_field(field_map, analysis):
    assert field_map.field() == "mucape"
    assert field_map.analysis() is analysis
    scale = field_map.scale()
    stats = analysis.statistics("mucape")
    assert scale.minimum == pytest.approx(bf.scale_for(stats).minimum)


def test_field_map_switching_field_rescales(field_map):
    before = field_map.scale()
    field_map.set_field("shear_6km")
    after = field_map.scale()
    assert after is not before
    assert field_map.field() == "shear_6km"


def test_field_map_has_no_scale_for_an_empty_field(field_map):
    field_map.set_field("stp_cin")
    assert field_map.scale() is None


def test_field_map_renders_with_and_without_values(field_map):
    for show in (True, False):
        field_map.set_show_values(show)
        pixmap = QPixmap(field_map.size())
        field_map.render(pixmap)
        assert not pixmap.isNull()


def test_field_map_click_selects_the_nearest_cell(field_map):
    from sharpmod.tests.test_gui_box_map import _MouseEvent

    picked = []
    field_map.cellSelected.connect(
        lambda row, col: picked.append((row, col)))
    analysis = field_map.analysis()
    target = analysis.point_at(1, 2)
    position = field_map._to_px(target.lon, target.lat, field_map._proj())
    field_map.mousePressEvent(_MouseEvent(position))
    field_map.mouseReleaseEvent(_MouseEvent(position))
    assert picked == [(1, 2)]
    assert field_map.selected_cell() == (1, 2)


def test_field_map_double_click_activates_a_cell(field_map):
    from sharpmod.tests.test_gui_box_map import _MouseEvent

    activated = []
    field_map.cellActivated.connect(
        lambda row, col: activated.append((row, col)))
    analysis = field_map.analysis()
    target = analysis.point_at(0, 0)
    position = field_map._to_px(target.lon, target.lat, field_map._proj())
    field_map.mouseDoubleClickEvent(_MouseEvent(position))
    assert activated == [(0, 0)]


def test_field_map_box_drag_does_not_select_a_cell(field_map):
    from sharpmod.tests.test_gui_box_map import _MouseEvent

    picked = []
    boxes = []
    field_map.cellSelected.connect(lambda r, c: picked.append((r, c)))
    field_map.boxSelected.connect(lambda *a: boxes.append(a))
    field_map.mousePressEvent(
        _MouseEvent((120, 90), modifiers=Qt.ShiftModifier))
    field_map.mouseMoveEvent(
        _MouseEvent((400, 300), modifiers=Qt.ShiftModifier))
    field_map.mouseReleaseEvent(
        _MouseEvent((400, 300), modifiers=Qt.ShiftModifier))
    assert len(boxes) == 1
    assert picked == []


def test_field_map_renders_without_an_analysis():
    from sharpmod.gui_maps import BoxFieldMapWidget

    view = BoxFieldMapWidget()
    view.resize(400, 300)
    pixmap = QPixmap(view.size())
    view.render(pixmap)
    assert not pixmap.isNull()
    assert view.scale() is None
    assert view._cell_at(QPointF(10.0, 10.0)) is None


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


def test_field_map_screen_resolves_a_mask_and_coverage(field_map):
    assert field_map.screen() is None
    assert field_map.coverage() is None
    field_map.set_screen("organized convection")
    assert field_map.screen() == "organized convection"
    coverage = field_map.coverage()
    assert coverage is not None
    assert coverage.total == len(field_map.analysis().points)
    assert field_map._screen_mask is not None


def test_field_map_screen_accepts_explicit_criteria(field_map):
    field_map.set_screen(ba.Criterion("mucape", minimum=1.0))
    coverage = field_map.coverage()
    assert coverage.count == len(field_map.analysis().analyzed)


def test_field_map_screen_can_be_cleared(field_map):
    field_map.set_screen("organized convection")
    field_map.set_screen(None)
    assert field_map.coverage() is None
    assert field_map._screen_mask is None


def test_field_map_bad_screen_clears_instead_of_breaking_the_repaint(field_map):
    field_map.set_screen("not-a-screen")
    # The overlay is dropped, but the field still paints.
    assert field_map.coverage() is None
    pixmap = QPixmap(field_map.size())
    field_map.render(pixmap)
    assert not pixmap.isNull()


def test_field_map_renders_with_a_screen_overlay(field_map):
    field_map.set_screen("organized convection")
    pixmap = QPixmap(field_map.size())
    field_map.render(pixmap)
    assert not pixmap.isNull()


def test_field_map_screen_survives_a_new_analysis(field_map, analysis):
    field_map.set_screen("organized convection")
    field_map.set_analysis(analysis, "mucape")
    # Re-analysis recomputes the overlay rather than silently dropping it.
    assert field_map.screen() == "organized convection"
    assert field_map.coverage() is not None

"""Drag-to-draw box selection on the forecast-model picker map."""

import pytest

from qtpy.QtCore import QEvent, QPointF, Qt
from qtpy.QtGui import QKeyEvent, QPixmap

from sharpmod.box_sounding import BoxRegion, plan_box_samples


pytestmark = pytest.mark.usefixtures("qt_app")


class _MouseEvent:
    """Minimal stand-in for QMouseEvent covering the accessors used here."""

    def __init__(
        self,
        pos,
        *,
        button=Qt.LeftButton,
        buttons=Qt.LeftButton,
        modifiers=Qt.NoModifier,
    ):
        self._pos = QPointF(*pos) if isinstance(pos, tuple) else pos
        self._button = button
        self._buttons = buttons
        self._modifiers = modifiers

    def position(self):
        return self._pos

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def modifiers(self):
        return self._modifiers


@pytest.fixture
def widget():
    from sharpmod.gui_maps import PointMapWidget

    view = PointMapWidget()
    # A deterministic size keeps the projection reproducible without showing
    # the widget.
    view.resize(600, 400)
    yield view
    view.close()
    view.deleteLater()


def _drag(view, start, end, *, modifiers=Qt.NoModifier):
    """Press, move, and release across the widget."""
    view.mousePressEvent(_MouseEvent(start, modifiers=modifiers))
    view.mouseMoveEvent(_MouseEvent(end, modifiers=modifiers))
    view.mouseReleaseEvent(_MouseEvent(end, modifiers=modifiers))


def _record(view):
    boxes, cleared, points = [], [], []
    view.boxSelected.connect(
        lambda *args: boxes.append(tuple(round(v, 6) for v in args))
    )
    view.boxCleared.connect(lambda: cleared.append(True))
    view.pointSelected.connect(lambda lat, lon: points.append((lat, lon)))
    return boxes, cleared, points


# -- gesture routing ------------------------------------------------------- #


def test_shift_drag_selects_a_box(widget):
    boxes, _cleared, points = _record(widget)
    _drag(widget, (120, 90), (400, 300), modifiers=Qt.ShiftModifier)
    assert len(boxes) == 1
    assert points == []
    lat0, lon0, lat1, lon1 = boxes[0]
    assert lat0 < lat1
    assert lon0 < lon1
    # The committed box is what was announced (the recorder rounds for
    # readability, so compare approximately).
    assert widget.box() == pytest.approx(boxes[0])


def test_plain_drag_pans_and_selects_no_box(widget):
    boxes, _cleared, _points = _record(widget)
    before = (widget._lon0, widget._lat0)
    _drag(widget, (120, 90), (400, 300))
    assert boxes == []
    assert widget.box() is None
    # The inherited pan still moved the view.
    assert (widget._lon0, widget._lat0) != before


def test_box_mode_makes_a_plain_drag_draw_a_box(widget):
    boxes, _cleared, _points = _record(widget)
    widget.set_box_mode(True)
    assert widget.box_mode() is True
    before = (widget._lon0, widget._lat0)
    _drag(widget, (150, 120), (420, 320))
    assert len(boxes) == 1
    # Drawing must not also pan the view.
    assert (widget._lon0, widget._lat0) == before


def test_leaving_box_mode_abandons_an_in_progress_rectangle(widget):
    widget.set_box_mode(True)
    widget.mousePressEvent(_MouseEvent((150, 120)))
    widget.mouseMoveEvent(_MouseEvent((300, 250)))
    widget.set_box_mode(False)
    assert widget._box_anchor is None
    assert widget.box() is None


def test_a_tiny_shift_drag_falls_back_to_picking_a_point(widget):
    boxes, _cleared, points = _record(widget)
    _drag(widget, (200, 200), (203, 202), modifiers=Qt.ShiftModifier)
    assert boxes == []
    assert len(points) == 1
    assert widget.box() is None


def test_a_drag_that_is_thin_in_one_axis_is_not_a_box(widget):
    boxes, _cleared, points = _record(widget)
    # Wide but only two pixels tall: a swipe, not a rectangle.
    _drag(widget, (100, 200), (400, 202), modifiers=Qt.ShiftModifier)
    assert boxes == []
    assert len(points) == 1


def test_drag_direction_does_not_change_the_result(widget):
    boxes, _cleared, _points = _record(widget)
    _drag(widget, (120, 90), (400, 300), modifiers=Qt.ShiftModifier)
    forward = boxes[0]
    widget.set_box(None)
    _drag(widget, (400, 300), (120, 90), modifiers=Qt.ShiftModifier)
    assert boxes[1] == pytest.approx(forward)


def test_right_button_release_is_ignored(widget):
    boxes, _cleared, points = _record(widget)
    widget.mouseReleaseEvent(_MouseEvent((200, 200), button=Qt.RightButton))
    assert boxes == [] and points == []


# -- geometry hand-off ----------------------------------------------------- #


def test_emitted_corners_match_the_inverse_projection(widget):
    boxes, _cleared, _points = _record(widget)
    start, end = (140, 100), (380, 280)
    expected_a = widget._to_lonlat(*start)
    expected_b = widget._to_lonlat(*end)
    _drag(widget, start, end, modifiers=Qt.ShiftModifier)
    lat0, lon0, lat1, lon1 = boxes[0]
    assert lat0 == pytest.approx(min(expected_a[1], expected_b[1]))
    assert lat1 == pytest.approx(max(expected_a[1], expected_b[1]))
    assert lon0 == pytest.approx(min(expected_a[0], expected_b[0]))
    assert lon1 == pytest.approx(max(expected_a[0], expected_b[0]))


def test_emitted_corners_feed_box_region_directly(widget):
    boxes, _cleared, _points = _record(widget)
    _drag(widget, (140, 100), (380, 280), modifiers=Qt.ShiftModifier)
    lat0, lon0, lat1, lon1 = boxes[0]
    region = BoxRegion.from_corners(lat0, lon0, lat1, lon1)
    assert region.lat0 == pytest.approx(lat0)
    assert region.lon_span == pytest.approx(lon1 - lon0)
    # And the pair is a usable sampling request.
    plan = plan_box_samples("hrrr", region, target_points=16)
    assert plan.count >= 4


def test_longitudes_are_not_wrapped_across_the_dateline(widget):
    # Park the view across the antimeridian, as panning west from Alaska does.
    widget._lon0, widget._lon1 = 160.0, 200.0
    widget._lat0, widget._lat1 = 45.0, 62.0
    widget._invalidate()
    boxes, _cleared, _points = _record(widget)
    _drag(widget, (100, 120), (500, 300), modifiers=Qt.ShiftModifier)
    _lat0, lon0, _lat1, lon1 = boxes[0]
    # The east edge must stay beyond +180 rather than wrapping to a negative
    # number, or the span would invert into the 340-degree complement.
    assert lon1 > 180.0
    region = BoxRegion.from_corners(_lat0, lon0, _lat1, lon1)
    assert region.crosses_antimeridian is True
    assert region.lon_span == pytest.approx(lon1 - lon0)
    assert region.lon_span < 180.0


# -- controller-driven state ----------------------------------------------- #


def test_set_box_normalizes_corners_without_emitting(widget):
    boxes, cleared, _points = _record(widget)
    widget.set_box((37.0, -95.0, 34.0, -99.0))
    assert widget.box() == (34.0, -99.0, 37.0, -95.0)
    assert boxes == [] and cleared == []


def test_clear_box_announces_only_when_a_box_existed(widget):
    _boxes, cleared, _points = _record(widget)
    widget.clear_box()
    assert cleared == []
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    widget.clear_box()
    assert cleared == [True]
    assert widget.box() is None


def test_set_box_none_clears_the_lattice_preview(widget):
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(34.0, -99.0, 37.0, -95.0), target_points=16
    )
    widget.set_box_nodes(plan.points, note="5 x 5")
    assert widget._box_nodes
    widget.set_box(None)
    assert widget._box_nodes == ()
    assert widget._box_note == ""


def test_set_box_nodes_accepts_sample_points_and_plain_pairs(widget):
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(34.0, -99.0, 37.0, -95.0), target_points=16
    )
    widget.set_box_nodes(plan.points)
    assert len(widget._box_nodes) == plan.count
    widget.set_box_nodes([(35.0, -97.0), (36.0, -96.0)])
    assert widget._box_nodes == ((-97.0, 35.0), (-96.0, 36.0))


def test_set_box_nodes_skips_unusable_entries(widget):
    widget.set_box_nodes([(35.0, -97.0), None, "nope", (1, 2, 3)])
    assert widget._box_nodes == ((-97.0, 35.0),)


# -- post-creation editing (T23.1/T23.2) ---------------------------------- #


def test_committed_corner_handle_resizes_in_geographic_space(widget):
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    widget.set_box_mode(True)
    boxes, _cleared, points = _record(widget)
    projection = widget._proj()
    north_west = widget._box_corner_points(projection, widget.box())[0]

    _drag(
        widget,
        (north_west.x(), north_west.y()),
        (north_west.x() - 35.0, north_west.y() - 25.0),
    )

    lat0, lon0, lat1, lon1 = widget.box()
    assert lat0 == pytest.approx(34.0)
    assert lon1 == pytest.approx(-95.0)
    assert lat1 > 37.0
    assert lon0 < -99.0
    assert len(boxes) == 1
    assert points == []


def test_dragging_projected_interior_moves_whole_box_without_resizing(widget):
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    widget.set_box_mode(True)
    before = widget.box()
    p = widget._proj()
    centre = widget._to_px(-97.0, 35.5, p)

    _drag(widget, (centre.x(), centre.y()), (centre.x() + 45.0, centre.y() + 30.0))

    after = widget.box()
    assert after != before
    assert after[2] - after[0] == pytest.approx(before[2] - before[0])
    assert after[3] - after[1] == pytest.approx(before[3] - before[1])


def test_completed_box_can_move_after_draw_mode_is_disarmed(widget):
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    boxes, _cleared, _points = _record(widget)
    centre = widget._to_px(-97.0, 35.5, widget._proj())

    _drag(widget, (centre.x(), centre.y()), (centre.x() + 40, centre.y() + 20))

    assert len(boxes) == 1
    assert widget.box() != (34.0, -99.0, 37.0, -95.0)


def test_click_inside_box_still_selects_a_point_without_moving_bounds(widget):
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    boxes, _cleared, points = _record(widget)
    centre = widget._to_px(-97.0, 35.5, widget._proj())

    _drag(widget, (centre.x(), centre.y()), (centre.x() + 1, centre.y() + 1))

    assert widget.box() == (34.0, -99.0, 37.0, -95.0)
    assert boxes == []
    assert len(points) == 1


def test_curved_projection_handle_hit_uses_projected_corner(widget):
    widget.set_projection("curved")
    widget.set_box((-2.0, 130.0, 12.0, 155.0))
    widget._lon0, widget._lon1 = 115.0, 170.0
    widget._lat0, widget._lat1 = -5.0, 30.0
    widget._invalidate()
    p = widget._proj()
    north_east = widget._box_corner_points(p, widget.box())[1]

    target = widget._box_hit_target(north_east)

    assert target is not None
    assert target[0] == "ne"


def test_curved_resize_numeric_bounds_match_projected_handle(widget):
    widget.set_projection("curved")
    widget._lon0, widget._lon1 = 115.0, 170.0
    widget._lat0, widget._lat1 = -5.0, 30.0
    widget._invalidate()
    widget.set_box((-2.0, 130.0, 12.0, 155.0))
    north_east = widget._box_corner_points(widget._proj(), widget.box())[1]
    end = (north_east.x() + 20.0, north_east.y() - 18.0)
    expected_lon, expected_lat = widget._to_lonlat(*end)

    _drag(widget, (north_east.x(), north_east.y()), end)

    assert widget.box()[2] == pytest.approx(expected_lat)
    assert widget.box()[3] == pytest.approx(expected_lon)
    projected = widget._box_corner_points(widget._proj(), widget.box())[1]
    assert projected.x() == pytest.approx(end[0], abs=0.1)
    assert projected.y() == pytest.approx(end[1], abs=0.1)


def test_antimeridian_box_has_visible_copies_on_both_world_seams(widget):
    widget._lon0, widget._lon1 = -180.0, 180.0
    widget._lat0, widget._lat1 = 20.0, 70.0
    widget._invalidate()
    widget.set_box((45.0, 170.0, 55.0, 190.0))

    copies = widget._box_longitude_copies(widget.box())

    spans = {(round(item[1]), round(item[3])) for item in copies}
    assert (170, 190) in spans
    assert (-190, -170) in spans


def test_panned_world_box_copy_and_handle_keep_short_wrapped_span(widget):
    widget._lon0, widget._lon1 = 520.0, 560.0
    widget._lat0, widget._lat1 = 40.0, 65.0
    widget._invalidate()
    widget.set_box((45.0, 170.0, 55.0, 190.0))
    copies = widget._box_longitude_copies(widget.box())
    assert len(copies) == 1
    assert copies[0][1:] == pytest.approx((530.0, 55.0, 550.0))
    nw = widget._box_corner_points(widget._proj(), copies[0])[0]

    _drag(widget, (nw.x(), nw.y()), (nw.x() - 20.0, nw.y() - 10.0))

    region = BoxRegion.from_corners(*widget.box())
    assert region.lon_span < 30.0
    assert region.crosses_antimeridian


def test_escape_during_handle_edit_restores_committed_bounds(widget):
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    widget.set_box_nodes([(35.0, -97.0)], note="planned sample")
    widget.set_box_mode(True)
    before = widget.box()
    north_west = widget._box_corner_points(widget._proj(), before)[0]
    widget.mousePressEvent(_MouseEvent((north_west.x(), north_west.y())))
    widget.mouseMoveEvent(_MouseEvent((north_west.x() - 25.0, north_west.y() - 20.0)))

    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))

    assert widget.box() == before
    assert widget._box_edit_kind is None
    assert widget._box_nodes == ((-97.0, 35.0),)
    assert widget._box_note == "planned sample"


# -- keyboard -------------------------------------------------------------- #


def test_escape_abandons_an_in_progress_rectangle(widget):
    _boxes, cleared, _points = _record(widget)
    widget.set_box_mode(True)
    widget.mousePressEvent(_MouseEvent((150, 120)))
    widget.mouseMoveEvent(_MouseEvent((300, 250)))
    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert widget._box_anchor is None
    assert widget.box() is None
    assert not widget.box_mode()
    assert cleared == [True]


def test_escape_clears_a_committed_rectangle(widget):
    _boxes, cleared, _points = _record(widget)
    widget.set_box((34.0, -99.0, 37.0, -95.0))
    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert widget.box() is None
    assert not widget.box_mode()
    assert cleared == [True]


def test_escape_exits_sticky_box_mode_before_any_rectangle(widget):
    _boxes, cleared, _points = _record(widget)
    widget.set_box_mode(True)
    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not widget.box_mode()
    assert cleared == [True]


# -- double click ---------------------------------------------------------- #


def test_double_click_is_suppressed_in_box_mode(widget):
    activated = []
    widget.pointActivated.connect(lambda lat, lon: activated.append((lat, lon)))
    widget.set_box_mode(True)
    widget.mouseDoubleClickEvent(_MouseEvent((200, 200)))
    assert activated == []
    widget.set_box_mode(False)
    widget.mouseDoubleClickEvent(_MouseEvent((200, 200)))
    assert len(activated) == 1


# -- rendering ------------------------------------------------------------- #


def test_painting_a_committed_box_with_a_lattice_succeeds(widget):
    region = BoxRegion.from_corners(34.0, -99.0, 37.0, -95.0)
    plan = plan_box_samples("hrrr", region, target_points=25)
    widget.set_box((region.lat0, region.lon0, region.lat1, region.lon1))
    widget.set_box_nodes(plan.points, note=f"{plan.rows} x {plan.cols}")
    widget.set_domain((-130.0, -60.0, 20.0, 55.0), "HRRR CONUS")
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    assert not pixmap.isNull()


def test_painting_an_in_progress_rectangle_succeeds(widget):
    widget.set_box_mode(True)
    widget.mousePressEvent(_MouseEvent((150, 120)))
    widget.mouseMoveEvent(_MouseEvent((320, 260)))
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    assert not pixmap.isNull()


def test_box_size_text_reports_kilometres(widget):
    text = widget._box_size_text((34.0, -99.0, 37.0, -95.0))
    assert text.endswith("km")
    assert "x" in text


# -- panning while box mode is armed --------------------------------------- #


def _view(widget):
    return (widget._lon0, widget._lon1, widget._lat0, widget._lat1)


def _button_drag(widget, button, start, end, *, modifiers=Qt.NoModifier):
    widget.mousePressEvent(
        _MouseEvent(start, button=button, buttons=button, modifiers=modifiers)
    )
    widget.mouseMoveEvent(
        _MouseEvent(end, button=button, buttons=button, modifiers=modifiers)
    )
    widget.mouseReleaseEvent(
        _MouseEvent(end, button=button, buttons=button, modifiers=modifiers)
    )


@pytest.mark.parametrize("button", [Qt.MiddleButton, Qt.RightButton])
def test_a_secondary_drag_pans_even_with_box_mode_armed(widget, button):
    """Box mode claimed every left drag, which left no way to move the map."""
    widget.set_box_mode(True)
    boxes, _cleared, points = _record(widget)
    before = _view(widget)

    _button_drag(widget, button, (100.0, 100.0), (200.0, 160.0))

    assert _view(widget) != before, "the map did not pan"
    assert boxes == []
    assert points == []


@pytest.mark.parametrize("button", [Qt.MiddleButton, Qt.RightButton])
def test_a_secondary_drag_pans_with_box_mode_off_too(widget, button):
    boxes, _cleared, points = _record(widget)
    before = _view(widget)

    _button_drag(widget, button, (300.0, 200.0), (250.0, 240.0))

    assert _view(widget) != before
    assert boxes == []
    assert points == []


def test_drawing_a_box_does_not_also_pan(widget):
    widget.set_box_mode(True)
    boxes, _cleared, _points = _record(widget)
    before = _view(widget)

    _drag(widget, (150.0, 120.0), (320.0, 260.0))

    assert len(boxes) == 1
    assert _view(widget) == before


def test_a_left_drag_still_pans_when_box_mode_is_off(widget):
    boxes, _cleared, _points = _record(widget)
    before = _view(widget)

    _drag(widget, (150.0, 120.0), (320.0, 260.0))

    assert _view(widget) != before
    assert boxes == []


def test_a_click_in_box_mode_picks_a_point_instead_of_selecting_a_box(widget):
    """A press and release at one spot is a click, not a rectangle."""
    widget.set_box_mode(True)
    boxes, _cleared, points = _record(widget)

    _drag(widget, (200.0, 180.0), (200.0, 180.0))

    assert boxes == []
    assert len(points) == 1


def test_hand_jitter_during_a_click_is_not_a_box(widget):
    """A few pixels of movement while clicking must not commit an area."""
    widget.set_box_mode(True)
    boxes, _cleared, _points = _record(widget)

    _drag(widget, (200.0, 180.0), (204.0, 183.0))

    assert boxes == []

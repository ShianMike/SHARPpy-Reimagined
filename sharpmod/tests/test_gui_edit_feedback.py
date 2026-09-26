"""Original/proposed edit feedback and immutable ghost-overlay regressions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy.ma as ma
from qtpy.QtWidgets import QMainWindow, QWidget

from sharpmod.gui_edit_feedback import (
    EditFeedbackController,
    format_level_changes,
    install_edit_feedback_hooks,
)


def _profile(*, temperature=18.0, dewpoint=12.0, direction=200.0, speed=20.0):
    return SimpleNamespace(
        pres=ma.array([1000.0, 900.0, 800.0]),
        hght=ma.array([100.0, 1000.0, 2000.0]),
        tmpc=ma.array([25.0, temperature, 10.0]),
        dwpc=ma.array([20.0, dewpoint, 5.0]),
        wdir=ma.array([180.0, direction, 220.0]),
        wspd=ma.array([10.0, speed, 30.0]),
        u=ma.array([0.0, 6.84, 0.0]),
        v=ma.array([0.0, 18.79, 0.0]),
    )


def test_temperature_feedback_names_original_proposed_and_change():
    text = format_level_changes(
        {"tmpc": 18.0},
        {"tmpc": 17.0},
        pressure_hpa=900.0,
        temp_units="Celsius",
        wind_units="knots",
        prefix="Editing",
    )

    assert text == (
        "Editing at 900.0 hPa · T: original 18.0 °C · "
        "proposed 17.0 °C · change -1.0 °C"
    )


def test_wind_feedback_uses_preferences_and_wrap_safe_direction_change():
    text = format_level_changes(
        {"wdir": 355.0, "wspd": 20.0},
        {"wdir": 5.0, "wspd": 24.0},
        pressure_hpa=900.0,
        temp_units="Fahrenheit",
        wind_units="m/s",
        prefix="Edited",
    )

    assert "Wind: original 355°/10.3 m/s" in text
    assert "proposed 005°/12.3 m/s" in text
    assert "change +2.1 m/s, +10°" in text


def test_mutation_hook_reports_only_an_actual_active_profile_change():
    calls = []

    class _Collection:
        def __init__(self):
            self.profile = _profile()

        def getHighlightedProf(self):  # noqa: N802 - upstream API
            return self.profile

    class _Widget:
        def __init__(self):
            self.pc_idx = 1
            self.prof_collections = [_Collection(), _Collection()]
            self._sharpmod_edit_feedback = SimpleNamespace(
                profile_edited=lambda idx, fields: calls.append((idx, tuple(fields)))
            )

        def modifyProf(self, idx, changes):  # noqa: N802 - upstream API
            profile = self.prof_collections[self.pc_idx].profile
            for field, value in changes.items():
                getattr(profile, field)[idx] = value

        def updateProfs(self):  # noqa: N802 - upstream API
            return None

    install_edit_feedback_hooks(_Widget)
    widget = _Widget()

    widget.modifyProf(1, {"tmpc": 17.0})

    assert widget.prof_collections[0].profile.tmpc[1] == 18.0
    assert widget.prof_collections[1].profile.tmpc[1] == 17.0
    assert calls == [(1, ("tmpc",))]


class _Surface(QWidget):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.prof = profile
        self.redraws = 0

    def clearData(self):  # noqa: N802 - upstream API
        self.redraws += 1

    def plotData(self):  # noqa: N802 - upstream API
        self.redraws += 1


def test_controller_offers_toggleable_original_profile_on_both_plots(qt_app):
    original = _profile()
    current = _profile(temperature=17.0)
    collection = SimpleNamespace(
        _prof_idx=0,
        _orig_profs={0: original},
        getHighlightedProf=lambda: current,
    )
    win = QMainWindow()
    win.menuBar().addMenu("&View")
    sound = _Surface(current, win)
    hodo = _Surface(current, win)
    win.spc_widget = SimpleNamespace(
        pc_idx=0,
        prof_collections=[collection],
        sound=sound,
        hodo=hodo,
    )

    controller = EditFeedbackController(win)

    assert win._sharpmod_original_overlay_action.isEnabled()
    assert win._sharpmod_original_overlay_action.isChecked()
    assert sound._sharpmod_original_profile is original
    assert hodo._sharpmod_original_profile is original
    assert "dashed" in win._sharpmod_original_overlay_action.toolTip().casefold()

    win._sharpmod_original_overlay_action.setChecked(False)
    assert sound._sharpmod_original_profile is None
    assert hodo._sharpmod_original_profile is None
    assert sound.redraws > 0 and hodo.redraws > 0
    controller.refresh()
    win.close()


def test_comparable_level_refuses_a_changed_vertical_grid():
    from sharpmod.gui_edit_feedback import comparable_level

    original = _profile()
    same_grid = _profile(temperature=17.0)
    assert comparable_level(original, same_grid, 1)

    interpolated = SimpleNamespace(
        pres=ma.array([1000.0, 975.0, 950.0, 925.0]),
        tmpc=ma.array([25.0, 22.0, 19.0, 16.0]),
    )
    assert not comparable_level(original, interpolated, 1)

    shifted = _profile()
    shifted.pres = ma.array([1000.0, 850.0, 800.0])
    assert not comparable_level(original, shifted, 1)


def _real_window(qt_app, tmp_path):
    """Compose the actual vendored window used by the interactive viewer."""
    from sharpmod import gui_sessions, gui_viewer, render
    from sharpmod.viz.SPCWindow import compose_window

    render.install_font(qt_app)
    render.install_render_patches()
    source = (
        gui_viewer.Path(gui_viewer.__file__).resolve().parents[1]
        / "examples/soundings/14061619.OAX"
    )
    collection, station = render.decode(str(source))
    gui_viewer._fill_metadata(collection, station)
    config = render.build_config(str(tmp_path))
    win, owner = compose_window(config, collection, mount=True)
    # The overlay is judged from real painted output, so the scientific canvas
    # has to reach its real composed size rather than a collapsed stub.
    render.align_top_row(win)
    render.apply_layout_compensation(win.spc_widget)
    for _ in range(4):
        qt_app.processEvents()
    render._grow_for_family_panels(win)
    for _ in range(4):
        qt_app.processEvents()
    render.enlarge_canvas(win)
    for _ in range(6):
        qt_app.processEvents()
    gui_sessions._install_analysis_actions(
        win,
        SimpleNamespace(_open_analysis_session=lambda: None, _settings=None),
    )
    gui_viewer._install_view_controls(win)
    assert win.spc_widget.sound.height() > 200
    return win, owner, collection


def test_real_edit_reports_original_proposed_change_and_paints_the_overlay(
    qt_app, tmp_path
):
    """Exercise the actual signal -> mode -> history -> feedback -> overlay route."""
    from sharpmod.gui_edit_feedback import install_edit_feedback
    from sharpmod.gui_interaction_mode import (
        MODE_EDIT,
        MODE_INSPECT,
        install_interaction_mode,
    )

    win, owner, collection = _real_window(qt_app, tmp_path)
    try:
        mode = install_interaction_mode(win)
        feedback = install_edit_feedback(win)
        assert feedback is not None
        sound = win.spc_widget.sound
        hodo = win.spc_widget.hodo

        # Nothing is retained before an edit, so there is nothing to overlay.
        assert not win._sharpmod_original_overlay_action.isEnabled()
        assert not win._sharpmod_original_overlay_action.isChecked()
        assert feedback.overlay_profile is None
        assert not feedback.label.isVisible()

        profile = collection.getHighlightedProf()
        idx = int(profile.sfc) + 4
        original_tmpc = float(profile.tmpc[idx])
        pressure = float(profile.pres[idx])

        # Inspect refuses the edit, so there is nothing to report.
        sound.modified.emit(idx, {"tmpc": original_tmpc + 2.5})
        assert feedback.label.toolTip() == ""
        assert feedback.overlay_profile is None

        mode.set_mode(MODE_EDIT, announce=False)
        sound.modified.emit(idx, {"tmpc": original_tmpc + 2.5})

        # The viewer's actual configured unit is Fahrenheit, so the reported
        # change must convert by ratio (2.5 °C is 4.5 °F, never 36.5 °F).
        assert sound.sfc_units == "Fahrenheit"
        text = feedback.label.toolTip()
        assert f"Edited at {pressure:.1f} hPa" in text
        assert f"T: original {original_tmpc * 9.0 / 5.0 + 32.0:.1f} °F" in text
        assert f"proposed {(original_tmpc + 2.5) * 9.0 / 5.0 + 32.0:.1f} °F" in text
        assert "change +4.5 °F" in text
        assert "Wind" not in text
        assert "nan" not in text.casefold()

        sound.sfc_units = "Celsius"
        feedback.refresh()
        celsius = feedback.label.toolTip()
        assert f"T: original {original_tmpc:.1f} °C" in celsius
        assert f"proposed {original_tmpc + 2.5:.1f} °C" in celsius
        assert "change +2.5 °C" in celsius

        # The retained original is now available on both scientific surfaces.
        assert win._sharpmod_original_overlay_action.isEnabled()
        assert win._sharpmod_original_overlay_action.isChecked()
        retained = collection._orig_profs[collection._prof_idx]
        assert sound._sharpmod_original_profile is retained
        assert hodo._sharpmod_original_profile is retained
        assert float(retained.tmpc[idx]) == original_tmpc

        with_ghost_skew = sound.plotBitMap.toImage()
        with_ghost_hodo = hodo.plotBitMap.toImage()

        win._sharpmod_original_overlay_action.setChecked(False)
        assert sound._sharpmod_original_profile is None
        assert hodo._sharpmod_original_profile is None
        without_ghost_skew = sound.plotBitMap.toImage()
        without_ghost_hodo = hodo.plotBitMap.toImage()

        assert with_ghost_skew != without_ghost_skew
        assert with_ghost_hodo != without_ghost_hodo

        # The dashed curve is named on the canvas itself, so an exported image
        # is still interpretable: its caption region gains ink.
        caption_left = int(getattr(sound, "lpad", 0) or 0)
        caption_top = 2 + int(sound.title_height)
        caption_ink = sum(
            1
            for y in range(caption_top, min(caption_top + int(sound.title_height),
                                            with_ghost_skew.height()))
            for x in range(caption_left, min(caption_left + 160,
                                            with_ghost_skew.width()))
            if with_ghost_skew.pixel(x, y) != without_ghost_skew.pixel(x, y)
        )
        assert caption_ink > 0

        win._sharpmod_original_overlay_action.setChecked(True)
        assert sound._sharpmod_original_profile is retained

        # Undo restores the level, so no stale comparison is left on screen.
        win._sharpmod_undo_action.trigger()
        assert float(collection.getHighlightedProf().tmpc[idx]) == original_tmpc
        assert feedback.label.toolTip() == ""
        assert feedback.overlay_profile is None
        assert not win._sharpmod_original_overlay_action.isEnabled()

        # Redo brings the edit and its comparison back unchanged.
        win._sharpmod_redo_action.trigger()
        assert "change +2.5 °C" in feedback.label.toolTip()

        # A real wind edit at the same level must reach the hodograph overlay,
        # add its change markers, and be named alongside the temperature change.
        from sharpmod.gui_edit_feedback import (
            edited_wind_levels,
            level_wind_components,
        )

        before_wind = collection.getHighlightedProf()
        u_before = float(before_wind.u[idx])
        v_before = float(before_wind.v[idx])
        hodo.modified.emit(idx, {"u": u_before + 9.0, "v": v_before - 7.0})

        both = feedback.label.toolTip()
        assert "T: original" in both
        assert "Wind: original" in both
        assert "Wind: original 0" not in both.replace("Wind: original 00", "")

        retained = collection._orig_profs[collection._prof_idx]
        after_wind = collection.getHighlightedProf()
        assert float(after_wind.u[idx]) != u_before

        # A history-restored original is a raw profile whose u/v are None while
        # wdir/wspd are populated, so the wind comparison must resolve either
        # representation rather than quietly stopping after an undo/redo.
        assert retained.u is None
        assert level_wind_components(retained, idx)[0] is not None
        assert idx in edited_wind_levels(retained, after_wind)

        hodo_with = hodo.plotBitMap.toImage()
        win._sharpmod_original_overlay_action.setChecked(False)
        hodo_without = hodo.plotBitMap.toImage()

        # The ring must appear at the level's *original* wind vector, which is
        # the only place the overlay can show what the edit replaced.
        origin_x, origin_y = hodo.uv_to_pix(*level_wind_components(retained, idx))
        ring_ink = [
            (x, y)
            for y in range(int(origin_y) - 8, int(origin_y) + 9)
            for x in range(int(origin_x) - 8, int(origin_x) + 9)
            if 0 <= x < hodo_with.width()
            and 0 <= y < hodo_with.height()
            and hodo_with.pixel(x, y) != hodo_without.pixel(x, y)
        ]
        assert ring_ink
        win._sharpmod_original_overlay_action.setChecked(True)

        # An interpolation changes the vertical grid, so the index no longer
        # refers to the same level and the comparison is withheld.
        win.spc_widget.interpProf()
        withheld = feedback.label.toolTip()
        assert "not comparable" in withheld
        assert "Reset" in withheld

        mode.set_mode(MODE_INSPECT, announce=False)
    finally:
        win.close()
        owner.deleteLater()


def test_real_drag_preview_names_the_proposed_value_before_commit(qt_app, tmp_path):
    """A live drag must state the value it would commit, not only after release."""
    from qtpy.QtCore import QEvent, QPointF

    from sharpmod.gui_edit_feedback import install_edit_feedback
    from sharpmod.gui_interaction_mode import MODE_EDIT, install_interaction_mode

    win, owner, collection = _real_window(qt_app, tmp_path)
    try:
        mode = install_interaction_mode(win)
        feedback = install_edit_feedback(win)
        sound = win.spc_widget.sound
        profile = collection.getHighlightedProf()
        idx = int(profile.sfc) + 4
        original_tmpc = float(profile.tmpc[idx])
        pressure = float(profile.pres[idx])

        xs, ys = sound.drag_tmpc.getCoords()
        drag_x = float(xs[idx]) + 12.0
        drag_y = float(ys[idx])
        move = SimpleNamespace(
            type=lambda: QEvent.MouseMove,
            position=lambda: QPointF(drag_x, drag_y),
            buttons=lambda: None,
        )

        def _arm_drag():
            sound.drag_tmpc._drag_idx = idx
            sound.drag_tmpc._click_start = (float(xs[idx]), drag_y)

        # Inspect cannot commit an edit, so it must not propose one either.
        _arm_drag()
        feedback.eventFilter(sound, move)
        assert feedback.label.toolTip() == ""

        sound.sfc_units = "Celsius"
        mode.set_mode(MODE_EDIT, announce=False)
        # Switching modes deliberately cancels any armed drag, so re-arm the
        # real draggable the way a press inside Edit mode would.
        _arm_drag()
        feedback.eventFilter(sound, move)
        expected = float(
            sound.pix_to_tmpc(
                (drag_x - sound.originx) * sound.scale,
                (drag_y - sound.originy) * sound.scale,
            )
        )
        text = feedback.label.toolTip()
        assert f"Editing at {pressure:.1f} hPa" in text
        assert f"T: original {original_tmpc:.1f} °C" in text
        assert f"proposed {expected:.1f} °C" in text
        assert f"change {expected - original_tmpc:+.1f} °C" in text
        assert expected > original_tmpc

        # Releasing without a committed change clears the proposal.
        sound.drag_tmpc._drag_idx = None
        feedback.eventFilter(
            sound, SimpleNamespace(type=lambda: QEvent.MouseButtonRelease)
        )
        assert feedback.label.toolTip() == ""
    finally:
        win.close()
        owner.deleteLater()


def test_resize_keeps_skew_t_drag_targets_on_the_current_transform(qt_app, tmp_path):
    """A resized canvas must not hit-test edits against the previous size.

    ``plotSkewT.wheelEvent`` rebuilds the draggable coordinates but
    ``resizeEvent`` does not, so before this fix a click on the visible
    temperature curve could miss or grab a different level after any resize.
    """
    from sharpmod.gui_edit_feedback import install_edit_feedback

    win, owner, collection = _real_window(qt_app, tmp_path)
    try:
        install_edit_feedback(win)
        sound = win.spc_widget.sound
        profile = collection.getHighlightedProf()
        idx = int(profile.sfc) + 4

        stale_x = float(sound.drag_tmpc.getCoords()[0][idx])
        stale_width = sound.width()

        # Resize the window, the way a user does; the canvas follows its layout.
        win.resize(win.width() + 260, win.height() + 180)
        for _ in range(6):
            qt_app.processEvents()
        assert sound.width() != stale_width

        expected_x = float(
            sound.originx
            + sound.tmpc_to_pix(profile.tmpc[idx], profile.pres[idx]) / sound.scale
        )
        expected_y = float(
            sound.originy + sound.pres_to_pix(profile.pres[idx]) / sound.scale
        )
        # The resize must actually move the transform, or the check is vacuous.
        assert abs(expected_x - stale_x) > 1.0

        xs, ys = sound.drag_tmpc.getCoords()
        assert abs(float(xs[idx]) - expected_x) < 0.5
        assert abs(float(ys[idx]) - expected_y) < 0.5

        dewpoint_x = float(sound.drag_dwpc.getCoords()[0][idx])
        assert abs(
            dewpoint_x
            - float(
                sound.originx
                + sound.tmpc_to_pix(profile.dwpc[idx], profile.pres[idx]) / sound.scale
            )
        ) < 0.5

        # The visible curve is now grabbable at the level it appears to be.
        assert sound.drag_tmpc.click(expected_x, expected_y)
        assert int(sound.drag_tmpc._drag_idx) == idx

        # A resize during a drag must not move the anchor under the cursor.
        held = float(sound.drag_tmpc.getCoords()[0][idx])
        win.resize(win.width() + 120, win.height() + 80)
        for _ in range(6):
            qt_app.processEvents()
        assert float(sound.drag_tmpc.getCoords()[0][idx]) == held
        sound.drag_tmpc._drag_idx = None
        sound.drag_tmpc._click_start = None
    finally:
        win.close()
        owner.deleteLater()

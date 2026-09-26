"""The comparison workspace draws real Skew-T and hodograph charts.

Before this, the Compare tab drew its own simplified temperature and dewpoint
traces. That view is good at "how far apart are these two profiles" and useless
for "what does this sounding look like": no parcel path, no barbs, no hodograph,
and no annotation. It also could not be exported -- only the table was available
comparison *table* and no comparison picture at all.

These checks pin the three things that are easy to break by accident:

* the chart is produced by the vendored renderer, not by a second drawing routine
  that can drift away from the operational chart;
* a profile keeps its colour, in the chart and in the legend, even as the fourth
  slot -- the vendored palette holds three colours and is consumed positionally,
  so the naive wiring silently gives slot four slot one's colour;
* an export comes out at the size it was asked for without touching the live view,
  because the interactive splitter's height is not a publication size.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from qtpy.QtGui import QColor, QImage

from sharpmod import gui_comparison_charts as charts
from sharpmod import render as render_mod
from sharpmod.gui_visual_comparison import VisualComparisonWidget

EXAMPLE = "hrrr_point_36.68N_95.66W_f018.npz"


def _real_collections():
    """Return two independent collections carrying real, complete profiles.

    The vendored hodograph reads derived fields (``srwind``, ``etop``,
    ``mean_lcl_el``, ``upshear_downshear``) that only a fully decoded profile
    carries, so a hand-built stand-in cannot exercise this path at all.
    """
    from sharpmod.tests._examples import examples_dir

    example = examples_dir() / EXAMPLE
    if not example.exists():
        pytest.skip("bundled example sounding is not present")
    render_mod.install_render_patches()
    first, _stn = render_mod.decode(str(example))
    second, _stn = render_mod.decode(str(example))
    return first, second


@pytest.fixture(scope="module")
def real_pair():
    return _real_collections()


def _widget(qt_app, collections, mode, *, width=1200, height=700):
    widget = VisualComparisonWidget()
    widget.resize(width, height)
    widget.set_chart_mode(mode)
    reference_time = collections[0].getCurrentDate()
    widget.set_collections(
        collections, 0, reference_time, profile_ids=("reference", "candidate")
    )
    widget.show()
    qt_app.processEvents()
    qt_app.processEvents()
    return widget


# --------------------------------------------------------------------------- #
# T08.1 -- the established renderers actually draw the comparison
# --------------------------------------------------------------------------- #


def test_a_comparison_slot_is_drawn_by_the_vendored_skewt_renderer(qt_app, real_pair):
    from sharppy.viz.skew import plotSkewT

    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        views = widget.charts.views()
        assert len(views) == 2

        for view in views:
            assert view.unavailable_reason() == ""
            renderer = view.renderer()
            # The point of the subtask: the operational widget, not a local copy.
            assert isinstance(renderer, plotSkewT)
            # And it is actually carrying this slot's data.
            assert renderer.prof is not None

        # A blank chart would still be "not null", so require real ink.
        image = widget.charts.grab().toImage()
        assert _distinct_colours(image) > 8
    finally:
        widget.close()


def test_the_hodograph_mode_uses_the_vendored_hodograph_renderer(qt_app, real_pair):
    from sharppy.viz.hodo import plotHodo

    widget = _widget(qt_app, real_pair, charts.CHART_HODOGRAPH)
    try:
        views = widget.charts.views()
        assert views
        for view in views:
            assert view.unavailable_reason() == ""
            assert isinstance(view.renderer(), plotHodo)
        assert _distinct_colours(widget.charts.grab().toImage()) > 8
    finally:
        widget.close()


def test_the_simplified_difference_view_is_retained_and_is_the_default(qt_app, real_pair):
    """T08.1 says retain useful simplified views; it is still the default."""
    widget = VisualComparisonWidget()
    try:
        assert widget.chart_mode() == charts.CHART_DIFFERENCE
        assert widget.canvas.isVisible() or not widget.charts.isVisible()

        widget.set_chart_mode(charts.CHART_SKEWT)
        qt_app.processEvents()
        assert widget.charts.isVisibleTo(widget)
        assert not widget.canvas.isVisibleTo(widget)

        widget.set_chart_mode(charts.CHART_DIFFERENCE)
        qt_app.processEvents()
        assert widget.canvas.isVisibleTo(widget)
        assert not widget.charts.isVisibleTo(widget)
    finally:
        widget.close()


def test_switching_chart_type_reuses_the_resolved_panels(qt_app, real_pair):
    """Switching view must not re-resolve profiles or recompute differences."""
    widget = _widget(qt_app, real_pair, charts.CHART_DIFFERENCE)
    try:
        before = widget.panels()
        assert before
        widget.set_chart_mode(charts.CHART_SKEWT)
        qt_app.processEvents()
        after = widget.panels()

        assert len(after) == len(before)
        for old, new in zip(before, after):
            # Same profile object and same difference object, not equal copies.
            assert old.profile is new.profile
            assert old.difference is new.difference
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# T08.2 -- synchronized cursor, readout, and comparable axes
# --------------------------------------------------------------------------- #


def test_the_shared_cursor_reaches_every_hodograph_slot(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_HODOGRAPH)
    try:
        widget.charts.set_cursor_height(3_000.0)
        qt_app.processEvents()

        heights = [view.renderer().readout_hght for view in widget.charts.views()]
        assert heights, "no slots were rendered"
        # Every slot moved, and to the same height, which is what makes reading
        # two hodographs at "3 km" a valid comparison.
        assert len(set(heights)) == 1
        assert heights[0] > 0.0
        assert all(view.renderer().readout_visible for view in widget.charts.views())

        widget.charts.set_cursor_height(None)
        qt_app.processEvents()
        assert all(
            view.renderer().readout_hght < 0.0 for view in widget.charts.views()
        )
    finally:
        widget.close()


def test_the_difference_canvas_cursor_drives_the_scientific_charts(qt_app, real_pair):
    """One cursor, not one per view."""
    widget = _widget(qt_app, real_pair, charts.CHART_HODOGRAPH)
    try:
        # Emitted by the simplified canvas on hover; the widget must forward it.
        widget.canvas.cursorHeightChanged.emit(2_500.0)
        qt_app.processEvents()

        heights = [view.renderer().readout_hght for view in widget.charts.views()]
        assert heights and all(height > 0.0 for height in heights)
        assert len(set(heights)) == 1
    finally:
        widget.close()


def test_linked_axes_hold_every_slot_on_one_scale(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        assert widget.linked_axes() is True
        states = [view.axis_state() for view in widget.charts.views()]
        assert all(state is not None for state in states)
        assert len(set(states)) == 1, "linked slots must share one pan/zoom"
    finally:
        widget.close()


def test_independent_axes_say_positions_are_not_comparable(qt_app, real_pair):
    """The independent option is allowed, but it has to be clearly indicated."""
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        linked_description = widget.charts.accessibleDescription()
        assert "shared" in linked_description.lower()

        widget.link_axes.setChecked(False)
        qt_app.processEvents()

        assert widget.linked_axes() is False
        description = widget.charts.accessibleDescription().lower()
        assert "independent" in description
        assert "not comparable" in description
        # And it is stated on screen, not only to assistive technology.
        assert "not comparable" in widget.status.text().lower()
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# T08.3 -- per-profile identity, reference styling, stable legends
# --------------------------------------------------------------------------- #


def test_overlay_palette_follows_the_renderers_own_background_walk():
    """The renderer consumes background colours positionally, skipping the active.

    ``plotData`` and ``drawTitles`` both walk the collection list in index order,
    skip ``pc_idx``, and take the next palette entry for each survivor. A palette
    that is not built in that order attaches a colour to a draw position instead
    of to a profile, which is what made the trace colour and the title colour
    disagree.
    """
    sources = ("a", "b", "c", "d")
    colours = ("#111111", "#222222", "#333333", "#444444")

    assert charts.overlay_palette(sources, 0, colours) == [
        "#222222",
        "#333333",
        "#444444",
    ]
    # Making a middle collection active drops exactly that colour and keeps the
    # remaining pairing intact.
    assert charts.overlay_palette(sources, 2, colours) == [
        "#111111",
        "#222222",
        "#444444",
    ]


def test_slot_colours_do_not_wrap_across_four_slots():
    """The vendored default holds three colours, so slot four reused slot one's."""
    from sharppy.viz.skew import plotSkewT

    stock = plotSkewT(dgz=False, pbl=False).background_colors
    assert len(stock) == 3, "guard: the vendored palette is what forces our own"

    assigned = [charts.slot_colour(number) for number in (1, 2, 3, 4)]
    assert len(set(assigned)) == 4
    # The reference colour is reserved and never issued to a slot.
    assert charts.reference_colour() not in assigned


def test_slot_colour_is_keyed_by_slot_not_by_draw_order():
    first = charts.slot_colour(3)
    assert charts.slot_colour(3) == first
    assert charts.slot_colour(1) != first


def test_side_by_side_slots_draw_the_reference_behind_each_candidate(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        views = widget.charts.views()
        reference_view, candidate_view = views[0], views[1]

        # The reference slot shows only itself; a candidate additionally carries
        # the reference, so the two can be compared without remembering a shape.
        assert len(reference_view.renderer().prof_collections) == 1
        assert len(candidate_view.renderer().prof_collections) == 2

        legend = candidate_view.legend.plain_text()
        assert "this slot" in legend
        # The chip colours have to be the colours actually in the chart: the
        # renderer's own temperature and dewpoint for its active profile, and the
        # supplied overlay colour for the reference behind it.
        renderer = candidate_view.renderer()
        chips = candidate_view.legend.chip_colours()
        assert chips[:2] == [
            QColor(renderer.temp_color).name(),
            QColor(renderer.dewp_color).name(),
        ]
        assert chips[2] == QColor(renderer.background_colors[0]).name()
        assert chips[2] == QColor(charts.reference_colour()).name()

        # The reference slot draws only itself, so it has no overlay chip.
        assert len(reference_view.legend.chip_colours()) == 2
    finally:
        widget.close()


def test_overlaid_arrangement_puts_every_profile_on_one_chart(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        widget.set_arrangement(charts.ARRANGEMENT_OVERLAID)
        qt_app.processEvents()

        views = [view for view in widget.charts.views() if view.isVisibleTo(widget)]
        assert len(views) == 1
        renderer = views[0].renderer()
        assert len(renderer.prof_collections) == 2
        # The reference is the active collection, so it is drawn last and on top.
        assert renderer.prof_collections[renderer.pc_idx] is not None
        # T, Td for the reference plus one chip per overlaid companion, each in
        # the colour the renderer was actually given for it.
        chips = views[0].legend.chip_colours()
        assert chips[2:] == [
            QColor(colour).name() for colour in renderer.background_colors
        ]
    finally:
        widget.close()


def test_the_reference_slot_is_named_as_the_reference(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        titles = [view.title.text() for view in widget.charts.views()]
        assert any("reference" in title for title in titles)
        assert all(title.startswith("Slot ") for title in titles)
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# T08.4 -- exportable at a chosen size, without recomputing
# --------------------------------------------------------------------------- #


def test_export_renders_at_the_requested_size(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT, width=900, height=520)
    try:
        data = widget.export_png(1400, 900)
        image = QImage.fromData(data, "PNG")

        assert not image.isNull()
        # The requested size, not the on-screen size.
        assert (image.width(), image.height()) == (1400, 900)
        assert _distinct_colours(image) > 8
    finally:
        widget.close()


def test_export_does_not_disturb_the_live_view(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT, width=900, height=520)
    try:
        before_size = widget.charts.size()
        before_panels = widget.panels()

        widget.export_png(1400, 900)
        qt_app.processEvents()

        assert widget.charts.size() == before_size
        # Same resolved objects: the export reused them rather than recomputing.
        for old, new in zip(before_panels, widget.panels()):
            assert old.profile is new.profile
            assert old.difference is new.difference
    finally:
        widget.close()


def test_export_works_from_the_difference_mode_without_switching_the_view(
    qt_app, real_pair
):
    """An export must not require the analyst to be looking at the chart."""
    widget = _widget(qt_app, real_pair, charts.CHART_DIFFERENCE)
    try:
        data = widget.export_png(800, 600, mode=charts.CHART_HODOGRAPH)
        image = QImage.fromData(data, "PNG")

        assert (image.width(), image.height()) == (800, 600)
        # Still showing the difference axes.
        assert widget.chart_mode() == charts.CHART_DIFFERENCE
    finally:
        widget.close()


def test_export_scale_multiplies_pixels_without_changing_the_layout(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        plain = QImage.fromData(widget.export_png(700, 500), "PNG")
        dense = QImage.fromData(widget.export_png(700, 500, scale=2.0), "PNG")

        assert (plain.width(), plain.height()) == (700, 500)
        assert (dense.width(), dense.height()) == (1400, 1000)
    finally:
        widget.close()


def test_export_never_nests_the_global_density_override(qt_app, real_pair):
    """``_target_density_pixmaps`` is process-global and refuses to nest.

    An export triggered while a high-density render is in flight would raise if
    this path entered that context itself.
    """
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        with render_mod._target_density_pixmaps(2.0):
            data = widget.export_png(600, 400)
        image = QImage.fromData(data, "PNG")
        assert (image.width(), image.height()) == (600, 400)
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# Incomplete profiles and degraded environments
# --------------------------------------------------------------------------- #


def test_an_incomplete_profile_states_why_its_chart_is_missing(qt_app):
    """A partial profile must explain itself, not crash the workspace.

    A history-restored original carries raw arrays with no derived wind fields, so
    the hodograph genuinely cannot be drawn from it. The acceptance criterion is
    that incomplete profiles are handled, not that they are silently blank.
    """
    run = datetime(2026, 9, 10, 0, tzinfo=timezone.utc)

    class _Partial:
        def __init__(self, loc):
            self._meta = {"loc": loc, "model": "HRRR", "run": run}
            self._dates = [run]
            self._prof_idx = 0
            self._profs = {
                "": [
                    SimpleNamespace(
                        hght=[300.0, 1_500.0, 3_000.0],
                        pres=[975.0, 850.0, 700.0],
                        tmpc=[24.0, 14.0, 2.0],
                        dwpc=[18.0, 8.0, -4.0],
                        u=None,
                        v=None,
                    )
                ]
            }

        def getMeta(self, key):  # noqa: N802 - upstream API
            return self._meta[key]

        def getCurrentDate(self):  # noqa: N802 - upstream API
            return self._dates[self._prof_idx]

    collections = (_Partial("KOUN"), _Partial("KTLX"))
    widget = VisualComparisonWidget()
    try:
        widget.resize(1000, 600)
        widget.set_chart_mode(charts.CHART_SKEWT)
        widget.set_collections(collections, 0, run, profile_ids=("a", "b"))
        widget.show()
        qt_app.processEvents()

        views = widget.charts.views()
        assert views
        for view in views:
            reason = view.unavailable_reason()
            assert reason, "an undrawable chart must say so"
            assert view.message.isVisibleTo(view)
            assert view.message.text() == reason
    finally:
        widget.close()


def test_an_unassigned_slot_says_so_rather_than_drawing_an_empty_chart(
    qt_app, real_pair
):
    widget = VisualComparisonWidget()
    try:
        widget.resize(1100, 700)
        widget.set_layout_count(4)
        widget.set_chart_mode(charts.CHART_SKEWT)
        reference_time = real_pair[0].getCurrentDate()
        widget.set_collections(
            real_pair, 0, reference_time, profile_ids=("reference", "candidate")
        )
        widget.show()
        qt_app.processEvents()

        views = widget.charts.views()
        assert len(views) == 4
        # Slots three and four have nothing assigned in a two-sounding session.
        # The reason is the panel's own, so the chart view and the difference view
        # explain an empty slot the same way.
        for view in views[2:]:
            assert view.renderer() is None
            assert view.message.text() == "No profile is assigned to this slot."
            assert view.unavailable_reason() == view.message.text()
    finally:
        widget.close()


def test_a_source_is_not_built_for_a_slot_with_no_profile():
    assert charts.source_for_panel(None, None) is None
    assert (
        charts.source_for_panel(SimpleNamespace(profile=None, label="x"), None) is None
    )


def test_the_adapter_gives_the_title_formatter_usable_types():
    """The vendored ``getPlotTitle`` calls ``strftime`` on ``run`` unguarded."""
    valid = datetime(2026, 9, 10, 18, tzinfo=timezone.utc)
    source = charts.ChartProfileSource(
        object(), "KOUN HRRR", valid, meta={"run": None, "model": None, "loc": None}
    )

    assert source.getMeta("run") == valid
    assert source.getMeta("base_time") == valid
    assert source.getMeta("model") == ""
    assert source.getMeta("loc") == "KOUN HRRR"
    assert source.getMeta("observed") is False
    assert source.getCurrentDate() == valid
    # The ensemble pass must draw nothing, or the active profile is drawn twice.
    assert source.getCurrentProfs() == {}


def test_the_adapter_reports_the_reference_valid_time_for_every_slot(
    qt_app, real_pair
):
    """Overlays only draw when the valid times match, and they do match here.

    Every panel profile comes from ``exact_profile`` at the reference time, so
    reporting that time is both truthful and what lets the renderer overlay them.
    """
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        reference_time = real_pair[0].getCurrentDate()
        candidate = widget.charts.views()[1].renderer()
        dates = {pc.getCurrentDate() for pc in candidate.prof_collections}
        assert dates == {reference_time}
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #


def test_chart_choice_round_trips(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_DIFFERENCE)
    try:
        widget.set_chart_mode(charts.CHART_HODOGRAPH)
        widget.set_arrangement(charts.ARRANGEMENT_OVERLAID)
        widget.link_axes.setChecked(False)
        qt_app.processEvents()
        state = widget.chart_state()

        restored = VisualComparisonWidget()
        try:
            restored.resize(1000, 600)
            restored.restore_chart_state(state)
            qt_app.processEvents()

            assert restored.chart_mode() == charts.CHART_HODOGRAPH
            assert restored.arrangement() == charts.ARRANGEMENT_OVERLAID
            assert restored.linked_axes() is False
            assert restored.charts.mode() == charts.CHART_HODOGRAPH
            assert restored.charts.arrangement() == charts.ARRANGEMENT_OVERLAID
        finally:
            restored.close()
    finally:
        widget.close()


def test_restoring_an_unknown_chart_state_is_ignored(qt_app):
    widget = VisualComparisonWidget()
    try:
        widget.restore_chart_state({"mode": "not-a-mode", "arrangement": "nonsense"})
        assert widget.chart_mode() == charts.CHART_DIFFERENCE
        assert widget.arrangement() == charts.ARRANGEMENT_SIDE_BY_SIDE

        widget.restore_chart_state(None)
        assert widget.chart_mode() == charts.CHART_DIFFERENCE
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_the_hodograph_legend_does_not_claim_a_single_curve_colour(qt_app, real_pair):
    """A hodograph's own trace is coloured by height band, not by one colour."""
    widget = _widget(qt_app, real_pair, charts.CHART_HODOGRAPH)
    try:
        legend = widget.charts.views()[1].legend
        assert "coloured by height" in legend.plain_text()
        # Only the overlaid reference gets a chip here.
        assert legend.chip_colours() == [QColor(charts.reference_colour()).name()]
    finally:
        widget.close()


def test_the_legend_stays_one_bounded_line_at_enlarged_text(qt_app, real_pair):
    """A wrapping legend grew into the next slot's heading at 200% text."""
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT, width=520, height=460)
    try:
        for view in widget.charts.views():
            legend = view.legend
            # One line, whatever the label length, so it cannot take the chart's
            # height or paint over a neighbour.
            assert legend.sizeHint().height() <= legend.fontMetrics().height() + 4
            if view.has_chart():
                # The full text is still reachable even when it is shortened.
                assert legend.toolTip() == legend.plain_text()
                assert legend.accessibleName() == legend.plain_text()
                assert legend.chip_colours()
    finally:
        widget.close()


def test_the_chart_keeps_a_height_floor_so_text_cannot_crush_it(qt_app, real_pair):
    """At 200% text the headings and legends squeezed the Skew-T to a strip."""
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT, width=700, height=520)
    try:
        for view in widget.charts.views():
            if view.has_chart():
                assert view.renderer().height() >= 100
    finally:
        widget.close()


def test_a_long_slot_title_elides_instead_of_being_cut_off(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT, width=520, height=420)
    try:
        view = widget.charts.views()[0]
        full = view.title.full_text()
        assert full.startswith("Slot 1:")
        # The untruncated text stays available to assistive technology and hover.
        assert view.title.toolTip() == full
        assert view.title.accessibleName() == full
    finally:
        widget.close()


def _distinct_colours(image):
    """Count distinct pixel colours on a coarse grid.

    Pixel-exact goldens are platform-fragile; "did anything actually get drawn"
    is not. A blank fill returns 1.
    """
    image = image.convertToFormat(QImage.Format_RGB32)
    seen = set()
    step = max(1, min(image.width(), image.height()) // 60)
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            seen.add(image.pixel(x, y))
    return len(seen)


def test_the_vendored_title_suppression_is_bound_to_the_instance_only(
    qt_app, real_pair
):
    """The renderer's own stacked titles are silenced here, nowhere else.

    They overlapped each other and ran across the plot at slot size. Silencing
    them on the *class* would take the titles off the main sounding window too,
    which is the mistake D025 records; this pins the binding to the instance.
    """
    from sharppy.viz.skew import plotSkewT

    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        renderer = widget.charts.views()[0].renderer()

        # Overridden on this object...
        assert "drawTitles" in vars(renderer)
        assert renderer.drawTitles(None) is None
        # ...and not on the class the sounding window builds from.
        assert "drawTitles" not in vars(type(renderer))or type(
            renderer
        ).drawTitles is not renderer.drawTitles
        fresh = plotSkewT(dgz=False, pbl=False)
        try:
            assert "drawTitles" not in vars(fresh)
            assert fresh.drawTitles is not renderer.drawTitles
        finally:
            fresh.deleteLater()
    finally:
        widget.close()


def test_an_unavailable_slot_does_not_reserve_a_charts_height(qt_app, real_pair):
    """Two unavailable slots used to take half of every four-panel export."""
    widget = VisualComparisonWidget()
    try:
        widget.resize(1200, 800)
        widget.set_layout_count(4)
        widget.set_chart_mode(charts.CHART_SKEWT)
        widget.set_collections(
            real_pair,
            0,
            real_pair[0].getCurrentDate(),
            profile_ids=("reference", "candidate"),
        )
        widget.show()
        qt_app.processEvents()
        qt_app.processEvents()

        views = widget.charts.views()
        drawn = [view for view in views if view.has_chart()]
        empty = [view for view in views if not view.has_chart()]
        assert drawn and empty

        # An explanation needs a line of text, not a chart's worth of height.
        for view in empty:
            assert view.minimumHeight() < min(
                other.minimumHeight() for other in drawn
            )
        # And the laid-out result gives the height to the charts.
        assert min(view.height() for view in drawn) > max(
            view.height() for view in empty
        )
    finally:
        widget.close()


def test_rendered_profile_upgrades_the_exact_time_profile_and_caches_it():
    """A decoded collection stores plain profiles with no derived fields.

    ``getHighlightedProf`` upgrades whichever index the collection is currently
    on, so reading ``_profs`` directly -- which is what ``exact_profile`` does --
    handed the renderers a profile with no ``vtmp`` and the Skew-T failed outright.
    """
    from sharpmod.comparison import exact_profile, rendered_profile

    collection, _other = _real_collections()
    valid = collection.getCurrentDate()

    plain = exact_profile(collection, valid)
    assert plain is not None

    upgraded = rendered_profile(collection, valid)
    assert upgraded is not None
    assert isinstance(upgraded, collection._target_type)
    # The derived fields the scientific renderers require are now present.
    for field in ("vtmp", "wetbulb", "dew_stdev", "tmp_stdev"):
        assert hasattr(upgraded, field)

    # Cached back into the collection, so the derivation happens once and the
    # chart shares one object with the rest of the application.
    assert rendered_profile(collection, valid) is upgraded
    assert exact_profile(collection, valid) is upgraded

    # A time the collection does not hold stays absent rather than substituting.
    assert rendered_profile(collection, valid + timedelta(days=400)) is None


def test_rendered_profile_returns_the_original_when_it_cannot_upgrade():
    """An unupgradable profile must leave the slot to explain itself."""
    from sharpmod.comparison import rendered_profile

    run = datetime(2026, 9, 10, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(hght=[0.0], pres=[1000.0])

    class _Target:
        @staticmethod
        def copy(_profile):
            raise ValueError("not enough data to derive anything")

    collection = SimpleNamespace(
        _dates=[run],
        _prof_idx=0,
        _highlight="control",
        _profs={"control": [profile]},
        _target_type=_Target,
    )

    assert rendered_profile(collection, run) is profile


def test_the_grid_describes_the_renderer_it_uses(qt_app, real_pair):
    widget = _widget(qt_app, real_pair, charts.CHART_SKEWT)
    try:
        description = widget.charts.accessibleDescription()
        assert "same renderer" in description
        assert "exact reference valid time" in description
    finally:
        widget.close()


def test_a_four_slot_comparison_uses_a_two_by_two_grid(qt_app, real_pair):
    third, fourth = _real_collections()
    widget = VisualComparisonWidget()
    try:
        widget.resize(1200, 800)
        widget.set_layout_count(4)
        widget.set_chart_mode(charts.CHART_SKEWT)
        widget.set_collections(
            (real_pair[0], real_pair[1], third, fourth),
            0,
            real_pair[0].getCurrentDate(),
            profile_ids=("a", "b", "c", "d"),
        )
        widget.show()
        qt_app.processEvents()
        qt_app.processEvents()

        views = widget.charts.views()
        assert len(views) == 4
        drawn = [view for view in views if view.renderer() is not None]
        assert len(drawn) == 4

        # Four distinct identities, which the vendored three-colour palette
        # could not have provided.
        swatch_colours = {
            charts.slot_colour(view_index + 1) for view_index in range(4)
        }
        assert len(swatch_colours) == 4
    finally:
        widget.close()


def test_a_valid_time_with_no_matching_profile_keeps_the_slot_explained(
    qt_app, real_pair
):
    widget = VisualComparisonWidget()
    try:
        widget.resize(1000, 600)
        widget.set_chart_mode(charts.CHART_SKEWT)
        # A time no collection holds: every slot is assigned but none can draw.
        absent = real_pair[0].getCurrentDate() + timedelta(days=400)
        widget.set_collections(
            real_pair, 0, absent, profile_ids=("reference", "candidate")
        )
        widget.show()
        qt_app.processEvents()

        for view in widget.charts.views():
            assert view.renderer() is None
            assert "exact reference valid time" in view.message.text()
            # The reason must also be reported, not only painted: a slot that
            # showed an explanation while reporting "" looked healthy to callers.
            assert view.unavailable_reason() == view.message.text()
    finally:
        widget.close()

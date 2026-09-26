"""Composition and installer helpers for auxiliary products on the SPC sounding window.

They attach HGZ, thermal-level, and CAPE overlays and mount product panels while keeping
customizations isolated from vendored SHARPpy widgets."""

from __future__ import annotations

from contextlib import suppress
from qtpy import QtGui
from qtpy.QtCore import QObject
from qtpy.QtCore import QRect
from sharpmod.viz.skew import draw_cape_fill
from sharpmod.viz.skew import draw_hgz_overlay
from sharppy.viz.SPCWindow import SPCWidget as _VendoredSPCWidget
from sharpmod.viz import SPCWindow as _api


def compose_window(config, prof_col=None, *, check_integrity=False,
                   mount=False, custom_config=None, custom_sars_lines=None,
                   controller=None):
    """Compose an :class:`SPCWindow` with a real :class:`RenderController`.

    Parameters
    ----------
    config : sutils.config.Config
        The render configuration; owned by the controller and passed to
        ``SPCWindow`` as its ``cfg``.
    prof_col : optional
        A profile collection to load into the window. When provided it is added
        with ``check_integrity`` (default ``False`` to match the headless
        renderer, which fills in metadata itself).
    check_integrity : bool, keyword-only
        Forwarded to ``SPCWindow.addProfileCollection``.
    mount : bool, keyword-only
        When ``True``, mount the SHARPpy Reimagined products onto the composed
        window via :func:`mount_products`: the combined index board and
        Streamwiseness chart are placed *inside* the vendored bottom table band
        (``grid3``), and the skew-T HGZ overlay is attached. The resulting
        :class:`MountResult` is attached to the window as
        ``win.sharpmod_products``. Defaults to ``False`` so existing callers are
        unaffected.
    custom_config, custom_sars_lines : optional, keyword-only
        Accepted for backward compatibility; currently unused (the vendored
        SARS inset is left pristine).
    controller : QWidget, keyword-only
        An existing controller/parent to compose ``SPCWindow`` onto. It must
        provide the same ``config_changed`` signal + ``preferencesbox`` slot
        contract as :class:`RenderController` (e.g. the interactive picker
        window, which doubles as the controller so the ``W`` key can refocus
        it). When omitted a headless :class:`RenderController` is created, which
        preserves the existing offscreen-render behaviour.

    Returns
    -------
    tuple(SPCWindow, controller)
        The composed window and its controller. The controller is the window's
        Qt parent and **must outlive it**, so the caller has to retain the
        returned reference for the window's duration.
    """
    _api._install_streamwiseness_hooks()
    _api._install_export_directory_hooks()
    # Install before ``SPCWidget`` is constructed so its existing Qt signal
    # connections bind the history-aware mutation methods. Headless renderers
    # never attach an ``AnalysisHistory``, so these wrappers are no-ops there.
    from sharpmod.state.sessions import install_history_hooks
    install_history_hooks(_VendoredSPCWidget)
    # Install the mode guard after history so allowed edits still pass through
    # the existing capture/commit wrapper, while Inspect-mode signals stop
    # before either the scientific collection or its history can change.
    from sharpmod.ui.features.gui_interaction_mode import install_interaction_mode_hooks
    install_interaction_mode_hooks(_VendoredSPCWidget)
    # Outermost of the three mutation wrappers: a gesture the mode guard blocks
    # changes nothing, so the comparison below reports nothing. Headless
    # renderers attach no feedback controller, so this is a no-op there.
    from sharpmod.ui.features.gui_edit_feedback import install_edit_feedback_hooks
    install_edit_feedback_hooks(_VendoredSPCWidget)
    from sharpmod.ui.features.gui_sounding_readout import install_linked_readout_hooks
    install_linked_readout_hooks(_VendoredSPCWidget)
    if controller is None:
        controller = _api.RenderController(config)
    win = _api.SPCWindow(parent=controller, cfg=config)
    # The vendored window subscribes the signal to profile refresh and only to
    # the outer window stylesheet.  Keep one additional retained slot that
    # applies the config to the actual SPCWidget and every mounted extension.
    def _apply_preferences(changed_config):
        _api.apply_preferences_to_window(win, changed_config)

    preferences_connection = controller.config_changed.connect(
        _apply_preferences
    )
    win._sharpmod_preferences_slot = _apply_preferences
    win._sharpmod_preferences_connection = preferences_connection

    # ``config_changed`` belongs to the longer-lived picker/controller.  A
    # plain Python closure is not disconnected automatically when ``win``'s
    # C++ object is deleted, so it would retain the dead wrapper and call back
    # into already-destroyed child widgets on the next Preferences change.
    def _disconnect_preferences(*_args):
        with suppress(TypeError, RuntimeError):
            QObject.disconnect(preferences_connection)

    win.destroyed.connect(_disconnect_preferences)
    win._sharpmod_preferences_disconnect_slot = _disconnect_preferences
    if prof_col is not None:
        win.addProfileCollection(prof_col, check_integrity=check_integrity)

    if mount:
        prof = _api._highlighted_profile(prof_col)
        win.sharpmod_products = _api.mount_products(win, prof)
    # One complete initial application follows the same path as a live picker
    # change.  This updates every vendored surface plus all mounted products.
    _api.apply_preferences_to_window(win, config)
    return win, controller


def attach_hgz_overlay(
    skewt, *, fill_color=None, edge_color=None, draw_fill=True, profile=None
):
    """Install the Hail-Growth-Zone overlay pass on a vendored skew-T widget.

    This is the concrete mount seam for Requirements 19.9-19.11: it wraps the
    skew-T's ``plotData`` so that, *after* the vendored widget renders its
    temperature/dewpoint traces onto its backing pixmap, the HGZ band is drawn
    over the -10 degrees C to -30 degrees C layer via
    :func:`sharpmod.viz.skew.draw_hgz_overlay`, using the widget's own
    pressure->pixel transform (``originy + pres_to_pix(p) / scale`` -- the same
    composition the vendored widget applies to every plotted level) and its plot
    rectangle (``tlx/tly/brx/bry``). The overlay draws nothing when
    ``skewt.prof.hgz_cape`` is missing (Requirement 19.11) and is clipped to the
    plot rectangle (Requirement 19.10). ``profile`` may supply the SHARPpy
    Reimagined derived profile when the vendored skew-T profile lacks those
    lazy-derived attributes.

    The wrapper is defensive: any failure in the overlay pass is swallowed so it
    can never break the base skew-T rendering. Returns ``True`` when the pass was
    installed, ``False`` when the widget does not expose the required hooks
    (``plotData`` / ``plotBitMap`` / the plot geometry).
    """
    if skewt is None:
        return False
    if getattr(skewt, "_sharpmod_hgz_attached", False):
        return True
    original_plot_data = getattr(skewt, "plotData", None)
    if not callable(original_plot_data):
        return False
    if profile is not None:
        skewt._sharpmod_derived_profile = profile

    def _plot_rect():
        tlx = getattr(skewt, "tlx", None)
        tly = getattr(skewt, "tly", None)
        brx = getattr(skewt, "brx", None)
        bry = getattr(skewt, "bry", None)
        if None in (tlx, tly, brx, bry):
            return None
        left = int(tlx)
        # When the omega meter is drawn it sits at the cold (left) end of the
        # skew-T (~-49..-41 C at 1000 mb). Start the HGZ band to the right of it
        # so the translucent fill does not wash out the omega bars.
        if getattr(skewt, "plot_omega", False):
            try:
                omega_right = skewt.tmpc_to_pix(-39, 1000)
                if omega_right == omega_right:  # NaN guard
                    left = max(left, int(omega_right))
            except Exception:
                pass
        return QRect(left, int(tly), int(brx - left), int(bry - tly))

    def _transform(p):
        # Match the vendored widget's own level transform so the band aligns
        # with the plotted isotherms even under pan/zoom.
        originy = getattr(skewt, "originy", 0.0) or 0.0
        scale = getattr(skewt, "scale", 1.0) or 1.0
        return originy + skewt.pres_to_pix(p) / scale

    def _wrapped_plot_data(*args, **kwargs):
        result = original_plot_data(*args, **kwargs)
        try:
            prof = getattr(skewt, "_sharpmod_derived_profile", None)
            if prof is None:
                prof = profile if profile is not None \
                    else getattr(skewt, "prof", None)
            rect = _plot_rect()
            bitmap = getattr(skewt, "plotBitMap", None)
            if prof is not None and rect is not None and bitmap is not None:
                qp = QtGui.QPainter()
                qp.begin(bitmap)
                try:
                    draw_hgz_overlay(
                        qp, prof, rect, _transform,
                        fill_color=fill_color, edge_color=edge_color,
                        draw_fill=draw_fill,
                    )
                finally:
                    qp.end()
        except Exception:
            # The overlay pass must never break the base skew-T rendering.
            pass
        return result

    skewt.plotData = _wrapped_plot_data
    skewt._sharpmod_hgz_attached = True
    return True


def attach_thermal_levels(skewt):
    """Draw the freezing level, wet-bulb zero, and growth zone at all times.

    The vendored skew-T makes these mutually exclusive with the maximum
    lapse-rate layer and the parcel's 0/-20/-30 degree levels, through a single
    ``if self.plotdgz ... else ...`` in ``plotData``. Because ``plotdgz`` is
    turned on only while the winter panel is the one on display, the effect is
    that choosing a panel silently changes *which thermodynamic levels the
    Skew-T is willing to label* -- pick the winter panel and the freezing level
    and wet-bulb zero appear while the lapse-rate layer vanishes; pick any other
    and the reverse happens.

    None of that follows from the physics. The freezing level and the wet-bulb
    zero are read for hail size, precipitation type, and icing whatever else is
    being looked at, and the growth zone is where it is regardless of which
    numbers are in the corner. So this draws whichever set the vendored branch
    skipped, leaving the sounding annotated the same way every time.

    Safe to overlay because the two label families are placed on opposite sides
    of their shared tick column -- ``draw_sig_levels`` right-aligns its text to
    the left of the tick and ``draw_temp_levels`` left-aligns to the right -- so
    drawing both cannot make them collide.

    Returns whether the pass was installed.
    """
    if skewt is None:
        return False
    if getattr(skewt, "_sharpmod_thermal_levels_attached", False):
        return True
    original_plot_data = getattr(skewt, "plotData", None)
    if not callable(original_plot_data):
        return False

    def _dgz_drawn_by_vendor(prof):
        """Did the vendored branch already draw the growth zone and the levels?"""
        if getattr(skewt, "plotdgz", False) is not True:
            return False
        bot = getattr(prof, "dgz_pbot", None)
        top = getattr(prof, "dgz_ptop", None)
        return bot is not None and top is not None and bot != top

    def _draw_growth_zone_and_levels(qp, prof):
        """The vendored ``if`` branch's annotations, minus its panel condition."""
        import numpy as np

        import sharppy.sharptab as tab

        with suppress(Exception):
            qp.setFont(skewt.hght_font)

        bot = getattr(prof, "dgz_pbot", None)
        top = getattr(prof, "dgz_ptop", None)
        if bot is not None and top is not None and bot != top:
            # Redrawn through the widget's own trace and tick helpers so the
            # band lands on the same isotherms the vendored route puts it on.
            with suppress(Exception):
                pres = np.ma.masked_invalid(
                    np.arange(top, bot, 5)[::-1])
                tmpc = np.ma.masked_invalid(tab.interp.temp(prof, pres))
                skewt.drawTrace(tmpc, skewt.dgz_color, qp, p=pres, label=False)
            for level in (bot, top):
                with suppress(Exception):
                    skewt.draw_sig_levels(
                        qp, plevel=level,
                        color=QtGui.QColor(_api._DGZ_TICK_COLOR))

        # Drawn even when there is no growth zone, which is the other half of the
        # repair: the vendored code skipped the whole block in that case, so a
        # sounding with no -12 to -17 layer lost its freezing level as well.
        with suppress(Exception):
            skewt.draw_sig_levels(
                qp, plevel=tab.params.temp_lvl(prof, 0, wetbulb=True),
                color=QtGui.QColor(skewt.dewp_color), var_id="WBZ=")
        with suppress(Exception):
            skewt.draw_sig_levels(
                qp, plevel=tab.params.temp_lvl(prof, 0),
                color=QtGui.QColor(_api._FRZ_LABEL_COLOR), var_id="FRZ=")

    def _draw_lapse_rate_and_parcel_levels(qp):
        """The vendored ``else`` branch's annotations."""
        with suppress(Exception):
            skewt.draw_max_lapse_rate_layer(qp)
        with suppress(Exception):
            skewt.draw_temp_levels(qp)

    def _wrapped_plot_data(*args, **kwargs):
        result = original_plot_data(*args, **kwargs)
        try:
            prof = getattr(skewt, "prof", None)
            bitmap = getattr(skewt, "plotBitMap", None)
            if prof is None or bitmap is None:
                return result
            qp = QtGui.QPainter()
            qp.begin(bitmap)
            try:
                clip = getattr(skewt, "clip", None)
                if clip is not None:
                    qp.setClipRect(clip)
                qp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                qp.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
                if _dgz_drawn_by_vendor(prof):
                    _draw_lapse_rate_and_parcel_levels(qp)
                else:
                    _draw_growth_zone_and_levels(qp, prof)
            finally:
                qp.end()
        except Exception:
            # An annotation pass must never break the base skew-T rendering.
            pass
        return result

    skewt.plotData = _wrapped_plot_data
    skewt._sharpmod_thermal_levels_attached = True
    return True


def attach_cape_fill(skewt, *, pos_color=None, neg_color=None):
    """Install the CAPE/CIN buoyancy-area fill pass on a vendored skew-T widget.

    Wraps the skew-T's ``plotData`` so that, *after* the vendored widget renders
    its temperature/dewpoint/parcel traces onto its backing pixmap, the area
    between the current parcel's virtual-temperature trace (``skewt.pcl.ttrace``
    / ``ptrace``) and the environment virtual-temperature trace
    (``skewt.prof.vtmp`` / ``pres``) is shaded via
    :func:`sharpmod.viz.skew.draw_cape_fill`: orange where the parcel is warmer
    (CAPE) and blue where it is colder (CIN).

    The pass uses the widget's own composed transforms -- ``originy +
    pres_to_pix(p)/scale`` and ``originx + tmpc_to_pix(t, p)/scale`` -- so the
    shading aligns with the plotted parcel trace under pan/zoom, and is clipped
    to the plot rectangle (``tlx/tly/brx/bry``). It draws nothing when no parcel
    has been set. The wrapper swallows any error so it can never break the base
    skew-T rendering. Returns ``True`` when the pass was installed, ``False``
    when the widget does not expose the required hooks.
    """
    if skewt is None:
        return False
    if getattr(skewt, "_sharpmod_cape_fill_attached", False):
        return True
    original_plot_data = getattr(skewt, "plotData", None)
    if not callable(original_plot_data):
        return False

    def _plot_rect():
        tlx = getattr(skewt, "tlx", None)
        tly = getattr(skewt, "tly", None)
        brx = getattr(skewt, "brx", None)
        bry = getattr(skewt, "bry", None)
        if None in (tlx, tly, brx, bry):
            return None
        return QRect(int(tlx), int(tly), int(brx - tlx), int(bry - tly))

    def _to_y(p):
        originy = getattr(skewt, "originy", 0.0) or 0.0
        scale = getattr(skewt, "scale", 1.0) or 1.0
        return originy + skewt.pres_to_pix(p) / scale

    def _to_x(t, p):
        originx = getattr(skewt, "originx", 0.0) or 0.0
        scale = getattr(skewt, "scale", 1.0) or 1.0
        return originx + skewt.tmpc_to_pix(t, p) / scale

    def _wrapped_plot_data(*args, **kwargs):
        result = original_plot_data(*args, **kwargs)
        try:
            prof = getattr(skewt, "prof", None)
            pcl = getattr(skewt, "pcl", None)
            rect = _plot_rect()
            bitmap = getattr(skewt, "plotBitMap", None)
            if prof is not None and pcl is not None and rect is not None and bitmap is not None:
                qp = QtGui.QPainter()
                qp.begin(bitmap)
                try:
                    qp.setRenderHint(qp.Antialiasing)
                    draw_cape_fill(
                        qp,
                        getattr(pcl, "ttrace", None),
                        getattr(pcl, "ptrace", None),
                        getattr(prof, "pres", None),
                        getattr(prof, "vtmp", None),
                        rect,
                        _to_x,
                        _to_y,
                        pos_color=pos_color,
                        neg_color=neg_color,
                    )
                finally:
                    qp.end()
        except Exception:
            # The fill pass must never break the base skew-T rendering.
            pass
        return result

    skewt.plotData = _wrapped_plot_data
    skewt._sharpmod_cape_fill_attached = True
    return True


def mount_products(
    win,
    prof=None,
    *,
    custom_config=None,
    custom_sars_lines=None,
):
    """Place the SHARPpy Reimagined derived-parameter family panels INSIDE the chart.

    The combined IndexBoard spans row 0 columns 0-2, Streamwiseness occupies
    row 0 column 3, and the Effective Layer STP chart occupies row 0 column 4.
    The skew-T HGZ overlay is also attached
    (Requirement 19.9).

    Values are read OFF a SHARPpy Reimagined Profile derived from ``prof``
    (:func:`_derived_profile`) and never recomputed (Requirement 13.3);
    unavailable values render the documented ``--`` indicator.

    Every step is individually guarded: a failure is captured in
    :attr:`MountResult.blocked` (naming the step) instead of raising, so a
    partial mount is observable rather than fatal.

    Parameters
    ----------
    win :
        The composed window (a vendored ``SPCWindow``) or any object exposing the
        vendored ``SPCWidget`` surface via a ``spc_widget`` attribute (or being
        one itself).
    prof : optional
        The analyzed Profile the derived Profile is built from.
    custom_config, custom_sars_lines : optional
        Accepted for backward compatibility; currently unused.

    Returns
    -------
    MountResult
        Which panels were placed into ``grid3`` (with their cells), which steps
        were blocked (with reasons), whether the SARS inset was detached, and
        whether the HGZ overlay pass was installed.
    """
    sw = getattr(win, "spc_widget", None) or win
    derived = _api._derived_profile(prof)
    result = _api.MountResult()

    # Refactor: within the vendored bottom band (grid3), replace the
    # convective / kinematics / SARS panels with the SHARPpy Reimagined IndexBoard (a
    # legacy-styled 3-column reimplementation whose columns are computed so
    # Space Grotesk never overlaps, with the derived params woven in). The
    # vendored Effective Layer STP graphic is kept but later moved beside the
    # streamwiseness chart. The board spans grid3 columns 0-2.
    try:
        from sharpmod.viz.index_board import IndexBoard

        grid3 = getattr(sw, "grid3", None)
        for attr in ("convective", "kinematic", "left_inset_ob"):
            wdg = getattr(sw, attr, None)
            if wdg is not None and grid3 is not None:
                try:
                    grid3.removeWidget(wdg)
                    wdg.hide()
                except Exception:
                    pass
        board = IndexBoard(parent=getattr(sw, "text", None) or sw)
        board.setData(prof, derived)
        if grid3 is not None:
            grid3.addWidget(board, 0, 0, 1, 3)
            # Give Effective Layer STP a larger dedicated column. The IndexBoard
            # compensates internally so only its SHIP/composite section tightens.
            try:
                grid3.setColumnStretch(0, 4)
                grid3.setColumnStretch(1, 4)
                grid3.setColumnStretch(2, 4)
                grid3.setColumnStretch(3, 7)
            except Exception:
                pass
        board.show()
        sw.index_board = board
        result.composite = board
        result.mounted.append("IndexBoard (cols 0-2)")
    except Exception as exc:  # noqa: BLE001 - record, never abort the mount
        result.blocked.append(f"IndexBoard: {exc}")

    # Place the streamwiseness profile directly left of the existing right
    # inset.  The original board-to-right-inset width share remains stable;
    # only the old STP allocation is split between these two charts.
    try:
        from sharpmod.viz.height_charts import SwappableHeightChart

        grid3 = getattr(sw, "grid3", None)
        right = getattr(sw, "right_inset_ob", None)
        if grid3 is None or right is None:
            raise RuntimeError("grid3/right_inset_ob not found")
        # The slot shows streamwiseness by default and offers the storm-relative
        # wind, theta/theta-e, and stepwise CIN/CAPE charts from its context
        # menu. It forwards the whole inset contract, so everything downstream --
        # ``sw.streamwiseness``, the deviant-vector hook, the profile refresh --
        # keeps addressing it as the single widget this column used to hold.
        stream = SwappableHeightChart(
            parent=getattr(sw, "text", None) or sw)
        stream.setProf(prof)
        latitude = getattr(prof, "latitude", 0.0) if prof is not None else 0.0
        stream.setDeviant("left" if latitude < 0 else "right")
        grid3.addWidget(stream, 0, 3)
        sw.streamwiseness = stream
        if isinstance(getattr(sw, "insets", None), dict):
            sw.insets["SHARPMOD STREAMWISENESS"] = stream
        if not _api._place_right_inset_after_streamwiseness(sw):
            raise RuntimeError("could not move right inset to column 4")
        stream.show()
        result.streamwiseness = stream
        result.mounted.append(
            "Swappable height chart (col 3, %d charts); "
            "right/STP inset narrowed (col 4)"
            % len(stream.availableCharts()))
    except Exception as exc:  # noqa: BLE001 - record, never abort the mount
        result.blocked.append(f"Streamwiseness: {exc}")

    # 3. CAPE/CIN buoyancy fill + HGZ overlay -> skew-T (Requirement 19.9).
    #    The CAPE/CIN fill is attached first so it renders *behind* the HGZ
    #    boundary lines/label. The HGZ band's own translucent fill is suppressed
    #    (``draw_fill=False``) so it does not muddy the buoyancy shading over the
    #    -10/-30 C layer -- only its dashed boundaries + label remain.
    skewt = getattr(sw, "sound", None)
    try:
        if _api.attach_cape_fill(skewt):
            result.mounted.append("CAPE/CIN buoyancy fill (skew-T)")
        else:
            result.blocked.append(
                "CAPE/CIN fill: skew-T widget did not expose plotData/geometry"
            )
    except Exception as exc:  # noqa: BLE001
        result.blocked.append(f"CAPE/CIN fill: {exc}")

    try:
        if _api.attach_hgz_overlay(skewt, draw_fill=False, profile=derived):
            result.hgz_attached = True
            result.mounted.append("HGZ overlay (skew-T)")
        else:
            result.blocked.append(
                "HGZ overlay: skew-T widget did not expose plotData/geometry"
            )
    except Exception as exc:  # noqa: BLE001
        result.blocked.append(f"HGZ overlay: {exc}")

    # Last of the skew-T passes, so it draws over the fills rather than under
    # them: these are single-pixel ticks and short labels, and a translucent
    # buoyancy wash across them would cost more legibility than it gains.
    try:
        if _api.attach_thermal_levels(skewt):
            result.mounted.append(
                "Freezing level / wet-bulb zero / growth zone (skew-T)")
        else:
            result.blocked.append(
                "Thermal levels: skew-T widget did not expose plotData"
            )
    except Exception as exc:  # noqa: BLE001
        result.blocked.append(f"Thermal levels: {exc}")

    return result

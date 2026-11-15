//! Winter composition and Qt integration; Python retains only dispatch wiring.
mod height_panels;
mod panels;
mod plots;
mod precipitation_panel;
mod tables;
mod value_colors;

use crate::{profile_input::column, winter::WinterProfile};
use pyo3::{
    prelude::*,
    types::{PyCFunction, PyDict, PyTuple},
};

fn profile(prof: &Bound<'_, PyAny>) -> PyResult<WinterProfile> {
    let prepare =
        PyModule::import(prof.py(), "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let columns = ["pres", "hght", "tmpc", "dwpc", "wetbulb", "omeg", "u", "v"]
        .iter()
        .map(|name| column(&prepare, &prof.getattr(*name)?, name))
        .collect::<PyResult<Vec<_>>>()?;
    WinterProfile::new(
        &columns,
        prof.getattr("sfc")?.extract()?,
        prof.getattr("missing")?.extract()?,
    )
    .map_err(pyo3::exceptions::PyValueError::new_err)
}

fn svg_panel(root: &Bound<'_, PyAny>, svg: &str, rect: [i32; 4]) -> PyResult<()> {
    let py = root.py();
    let bytes = PyModule::import(py, "qtpy.QtCore")?
        .getattr("QByteArray")?
        .call1((pyo3::types::PyBytes::new(py, svg.as_bytes()),))?;
    // A vector widget paints directly into the HD/UHD capture, avoiding QLabel's
    // device-pixel-ratio resampling of an intermediate pixmap.
    let widget = PyModule::import(py, "qtpy.QtSvgWidgets")?
        .getattr("QSvgWidget")?
        .call1((root,))?;
    widget.call_method1("load", (bytes,))?;
    widget.call_method1("setGeometry", (rect[0], rect[1], rect[2], rect[3]))?;
    widget.call_method0("show")?;
    Ok(())
}
#[pyfunction]
fn configure_winter_view<'py>(
    py: Python<'py>,
    win: &Bound<'py, PyAny>,
    view: &Bound<'py, PyDict>,
    app: &Bound<'py, PyAny>,
    original: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let sw = win.getattr("spc_widget")?;
    let sound = sw.getattr("sound")?;
    let hodo = sw.getattr("hodo")?;
    // Capture the settled severe geometry before replacing its diagnostic bank.
    // Reuse its actual inset positions as well as its overall image dimensions.
    let width: i32 = sw.call_method0("width")?.extract()?;
    let height: i32 = sw.call_method0("height")?.extract()?;
    let zero = PyModule::import(py, "qtpy.QtCore")?
        .getattr("QPoint")?
        .call1((0, 0))?;
    let rect = |name: &str| -> PyResult<[i32; 4]> {
        let widget = sw.getattr(name)?;
        let point = widget.call_method1("mapTo", (&sw, &zero))?;
        Ok([
            point.call_method0("x")?.extract()?,
            point.call_method0("y")?.extract()?,
            widget.call_method0("width")?.extract()?,
            widget.call_method0("height")?.extract()?,
        ])
    };
    let frames = PyModule::import(py, "sharpmod.render_patches.frames")?;
    let retained = [
        "sound",
        "speed_vs_height",
        "inferred_temp_advection",
        "hodo",
        "brand",
    ]
    .into_iter()
    .map(|name| {
        // Keep the severe layout's shared-border ownership before reparenting.
        // Resize events can arrive before the next sibling has been moved,
        // making the native detector temporarily paint both sides of a seam.
        if name != "brand" {
            let widget = sw.getattr(name)?;
            widget.setattr(
                "_sharpmod_frame_sides",
                frames.call_method1("_frame_sides_for_widget", (&widget,))?,
            )?;
        }
        Ok((name, rect(name)?))
    })
    .collect::<PyResult<Vec<_>>>()?;
    let hodo_rect = rect("hodo")?;
    let insets = rect("storm_slinky")?;
    let bank = rect("text")?;
    let hodo_left = hodo_rect[0];
    // Align the inset frame to the Skew-T frame, above its temperature-axis
    // labels. Move the whole row upward and fit the hodograph above it.
    let plot_bottom =
        (f64::from(rect("sound")?[1]) + sound.getattr("bry")?.extract::<f64>()?).round() as i32;
    let inset_top = plot_bottom - insets[3];
    // Keep the native plots and compact version label from the severe view.
    let layout = sw.call_method0("layout")?;
    while layout.call_method0("count")?.extract::<usize>()? > 0 {
        let item = layout.call_method1("takeAt", (0,))?;
        let widget = item.call_method0("widget")?;
        if !widget.is_none() {
            widget.call_method0("hide")?;
        }
    }
    sw.call_method1("setFixedSize", (width, height))?;
    for (name, [x, y, w, h]) in retained {
        let widget = sw.getattr(name)?;
        widget.call_method1("setParent", (&sw,))?;
        let h = if name == "hodo" { inset_top - y } else { h };
        widget.call_method1("setGeometry", (x, y, w, h))?;
        widget.call_method0("show")?;
    }
    let noop = PyCFunction::new_closure(
        py,
        Some(c"winter_omit_severe_annotation"),
        None,
        |args: &Bound<'_, PyTuple>, _: Option<&Bound<'_, PyDict>>| -> PyResult<Py<PyAny>> {
            Ok(args.py().None())
        },
    )?;
    for name in [
        "draw_max_lapse_rate_layer",
        "draw_effective_layer",
        "draw_parcel_levels",
        "drawVirtualParcelTrace",
        "draw_temp_levels",
    ] {
        sound.setattr(name, &noop)?;
    }
    for name in [
        "drawSMV",
        "drawCorfidi",
        "drawLCLtoEL_MW",
        "drawCriticalAngle",
    ] {
        hodo.setattr(name, &noop)?;
    }
    // The instance wrappers add CAPE/HGZ and severe thermal labels. Use the same
    // underlying Skew-T painter, then add only the winter layers in Rust.
    let base = sound.get_type().getattr("plotData")?.unbind();
    let winter_hodo = hodo.clone().unbind();
    let paint = PyCFunction::new_closure(
        py,
        Some(c"winter_skew_layers"),
        None,
        move |args: &Bound<'_, PyTuple>, _: Option<&Bound<'_, PyDict>>| -> PyResult<Py<PyAny>> {
            let sound = args.get_item(0)?;
            base.bind(args.py()).call1((&sound,))?;
            let prof = sound.getattr("prof")?;
            let data = profile(&prof)?;
            let diagnostics = data.diagnostics(panels::scalar(&prof, "plevel")?);
            plots::skew_layers(&sound, &data)?;
            plots::winter_markers(&sound, winter_hodo.bind(args.py()), &data, &diagnostics)?;
            Ok(args.py().None())
        },
    )?;
    let partial = PyModule::import(py, "functools")?.getattr("partial")?;
    sound.setattr("plotData", partial.call1((paint, &sound))?)?;
    sound.setattr("plotdgz", false)?;
    sound.setattr("pcl", py.None())?;
    // Retain the severe view's zoom and selected center. The native resize
    // handler reapplies that center to the new height without changing winds.
    win.call_method0("adjustSize")?;
    for _ in 0..3 {
        app.call_method0("processEvents")?;
    }

    let interactive = PyModule::import(py, "sharpmod.rendering.interactive")?;
    let edits = PyModule::import(py, "copy")?
        .call_method1("deepcopy", (view.get_item("edits")?.unwrap(),))?;
    if let Some(drag) = view.get_item("drag")? {
        if !drag.is_none() {
            interactive.call_method1("_drag", (&sw, drag, &edits))?;
        }
    }
    let prof = sw.getattr("default_prof")?;
    let data = profile(&prof)?;
    let diagnostics = data.diagnostics(panels::scalar(&prof, "plevel")?);
    let palette = panels::Palette::from_widget(&sound)?;
    // clearData restores the native background cache. Painting the background
    // again would compound antialiasing on axes, rings, grid lines and borders.
    sound.call_method0("clearData")?;
    sound.call_method0("plotData")?;
    sound.call_method0("update")?;
    hodo.call_method0("clearData")?;
    hodo.call_method0("plotData")?;
    plots::winter_markers(&sound, &hodo, &data, &diagnostics)?;
    hodo.call_method0("update")?;

    // Read the settled native hazard inset after profile edits. Its selected
    // deviant, label and theme color are the same ones shown in severe mode.
    let watch = sw.getattr("watch_type")?;
    let hazard: String = watch.getattr("watch_type")?.extract()?;
    let hazard_color_attr: String = watch.getattr("color_map")?.get_item(&hazard)?.extract()?;
    let hazard_color: String = watch
        .getattr(hazard_color_attr.as_str())?
        .call_method0("name")?
        .extract()?;
    svg_panel(
        &sw,
        &precipitation_panel::render(
            &diagnostics,
            &prof,
            &palette,
            [hodo_rect[2], insets[3]],
            (&hazard, &hazard_color),
        )?,
        [hodo_left, inset_top, hodo_rect[2], insets[3]],
    )?;
    svg_panel(
        &sw,
        &tables::diagnostics(&data, &diagnostics, &prof, &palette)?,
        [bank[0], bank[1], hodo_left - bank[0], bank[3]],
    )?;
    height_panels::mount(
        &sw,
        &data,
        &palette,
        [hodo_left, bank[1], bank[0] + bank[2] - hodo_left, bank[3]],
    )?;
    for _ in 0..3 {
        app.call_method0("processEvents")?;
    }

    // Reuse the owning hit-target/original-value logic after the new geometry
    // has settled. Hidden severe panel selections remain intact for switching back.
    let metadata_view = view.copy()?;
    metadata_view.set_item("mode", "severe")?;
    if metadata_view.contains("drag")? {
        metadata_view.del_item("drag")?;
    }
    metadata_view.set_item("edits", edits)?;
    metadata_view.set_item("panel", sw.getattr("right_inset")?)?;
    let result = interactive.call_method1("configure_view", (win, metadata_view, app, original))?;
    result.set_item("options", pyo3::types::PyList::empty(py))?;
    let points = result.get_item("points")?;
    let visible = pyo3::types::PyList::empty(py);
    for point in points.try_iter()? {
        let point = point?;
        if !point
            .get_item("id")?
            .extract::<String>()?
            .starts_with("storm:")
        {
            visible.append(point)?;
        }
    }
    result.set_item("points", visible)?;
    Ok(result)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(configure_winter_view, module)?)
}

#[cfg(test)]
mod tests;

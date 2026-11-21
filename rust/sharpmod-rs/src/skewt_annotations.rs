//! Skew-T drawing helpers. Science values remain owned by the profile.
use pyo3::{prelude::*, types::PyModule};

#[pyfunction]
fn set_skewt_trace_opacity(
    widget: &Bound<'_, PyAny>,
    data: &Bound<'_, PyAny>,
    pressure: &Bound<'_, PyAny>,
    painter: &Bound<'_, PyAny>,
) -> PyResult<()> {
    // Comparison traces supply their own pressure array.
    if pressure.is_none()
        && ["tmpc", "dwpc"]
            .into_iter()
            .any(|field| widget.getattr(field).is_ok_and(|trace| trace.is(data)))
    {
        let pen = painter.call_method0("pen")?;
        let color = pen.call_method0("color")?;
        let alpha: f64 = color.call_method0("alphaF")?.extract()?;
        color.call_method1("setAlphaF", (alpha * 0.70,))?;
        pen.call_method1("setColor", (color,))?;
        painter.call_method1("setPen", (pen,))?;
    }
    Ok(())
}

pub(crate) fn temperature_column(
    widget: &Bound<'_, PyAny>,
    temperature: f64,
    scale: f64,
) -> PyResult<f64> {
    let origin: f64 = widget.getattr("originx")?.extract()?;
    let offset: f64 = widget
        .call_method1("tmpc_to_pix", (temperature, widget.getattr("pmax")?))?
        .extract()?;
    Ok(origin + offset / scale)
}

#[pyfunction]
fn effective_inflow_tag_columns(
    bracket_x: f64,
    height_width: f64,
    helicity_width: f64,
    gap: f64,
    left_limit: f64,
    right_limit: f64,
) -> (f64, f64) {
    let row_width = height_width + gap + helicity_width;
    let left =
        (bracket_x - row_width / 2.0).clamp(left_limit, (right_limit - row_width).max(left_limit));
    (left, left + height_width + gap)
}

#[pyfunction]
#[pyo3(signature = (widget, painter, bound=4.5))]
fn draw_fixed_lapse_layer(
    widget: &Bound<'_, PyAny>,
    painter: &Bound<'_, PyAny>,
    bound: f64,
) -> PyResult<()> {
    let py = widget.py();
    let layer = widget.getattr("prof")?.getattr("max_lapse_rate_2_6")?;
    let qc = PyModule::import(py, "sharppy.sharptab.utils")?.getattr("QC")?;
    for index in 0..3 {
        if !qc.call1((layer.get_item(index)?,))?.is_truthy()? {
            return Ok(());
        }
    }
    let rate: f64 = layer.get_item(0)?.extract()?;
    if rate < bound {
        return Ok(());
    }
    let scale: f64 = widget.getattr("scale")?.extract()?;
    let origin_y: f64 = widget.getattr("originy")?.extract()?;
    let x = temperature_column(widget, -25.0, scale)?;
    let bottom = origin_y
        + widget
            .call_method1("pres_to_pix", (layer.get_item(1)?,))?
            .extract::<f64>()?
            / scale;
    let top = origin_y
        + widget
            .call_method1("pres_to_pix", (layer.get_item(2)?,))?
            .extract::<f64>()?
            / scale;
    let tier = if rate >= 8. {
        5
    } else if rate >= 7. {
        4
    } else if rate >= 6. {
        1
    } else {
        0
    };
    let background = widget.getattr("bg_color")?;
    let lightness: u8 = background.call_method0("lightness")?.extract()?;
    let color = if tier <= 1 && lightness <= 127 {
        let rgb = if tier == 1 {
            (255, 196, 107)
        } else {
            (210, 162, 97)
        };
        PyModule::import(py, "qtpy.QtGui")?
            .getattr("QColor")?
            .call1(rgb)?
    } else {
        widget.getattr("alert_colors")?.get_item(tier)?
    };
    painter.call_method0("save")?;
    let result = draw_layer_bracket(
        widget,
        painter,
        (x, top, bottom),
        &format!("{rate:.1} °C/km"),
        &color,
        true,
    );
    painter.call_method0("restore")?;
    result
}

#[pyfunction]
#[pyo3(signature = (widget, painter, plot_rect, band, edge_color=None))]
fn draw_fixed_hgz_layer(
    widget: &Bound<'_, PyAny>,
    painter: &Bound<'_, PyAny>,
    plot_rect: &Bound<'_, PyAny>,
    band: Option<(f64, f64)>,
    edge_color: Option<&Bound<'_, PyAny>>,
) -> PyResult<bool> {
    let Some((top, bottom)) = band else {
        return Ok(false);
    };
    let scale: f64 = widget.getattr("scale")?.extract()?;
    let x = temperature_column(widget, -35.0, scale)?;
    let left: f64 = plot_rect.call_method0("left")?.extract()?;
    let right: f64 = plot_rect.call_method0("right")?.extract()?;
    if !(left..=right).contains(&x) {
        return Ok(false);
    }

    let color = match edge_color {
        Some(color) => color.clone(),
        None => growth_zone_color(widget)?,
    };
    painter.call_method0("save")?;
    let result = (|| {
        painter.call_method1("setClipRect", (plot_rect,))?;
        draw_layer_bracket(widget, painter, (x, top, bottom), "HGZ", &color, false)
    })();
    painter.call_method0("restore")?;
    result?;
    Ok(true)
}

pub(crate) fn growth_zone_color<'py>(widget: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let lightness: i32 = widget
        .getattr("bg_color")?
        .call_method0("lightness")?
        .extract()?;
    let (green, blue) = if lightness > 127 {
        (117, 153)
    } else {
        (200, 255)
    };
    PyModule::import(widget.py(), "qtpy.QtGui")?
        .getattr("QColor")?
        .call1((0, green, blue))
}

pub(crate) fn draw_layer_bracket(
    widget: &Bound<'_, PyAny>,
    painter: &Bound<'_, PyAny>,
    (x, top, bottom): (f64, f64, f64),
    text: &str,
    color: &Bound<'_, PyAny>,
    emphasize_label: bool,
) -> PyResult<()> {
    let py = widget.py();
    let core = PyModule::import(py, "qtpy.QtCore")?;
    let gui = PyModule::import(py, "qtpy.QtGui")?;
    let qt = core.getattr("Qt")?;
    let mut font = widget.getattr("esrh_font")?;
    if emphasize_label {
        font = gui.getattr("QFont")?.call1((&font,))?;
        font.call_method1("setBold", (true,))?;
    }
    let metrics = gui.getattr("QFontMetrics")?.call1((&font,))?;
    let advance = metrics
        .call_method1("horizontalAdvance", (text,))?
        .extract::<f64>()?;
    let width = advance + 4.0;
    let font_height: f64 = metrics.call_method0("height")?.extract()?;
    let height = widget
        .getattr("esrh_height")?
        .extract::<f64>()?
        .max(font_height + 2.0);
    let rect = core
        .getattr("QRectF")?
        .call1((x - width / 2., top - height, width, height))?;
    let pen_type = gui.getattr("QPen")?;
    let line = qt.getattr("SolidLine")?;
    let pen = pen_type.call1((color, if emphasize_label { 2.0 } else { 1.5 }, &line))?;
    painter.call_method1("setClipping", (true,))?;
    painter.call_method1("setPen", (pen,))?;
    painter.call_method1("setFont", (&font,))?;
    for coords in [
        (x - 10., bottom, x + 10., bottom),
        (x - 10., top, x + 10., top),
        (x, bottom, x, top),
    ] {
        painter.call_method1("drawLine", coords)?;
    }
    let flags: i32 = ["TextDontClip", "AlignHCenter", "AlignVCenter"]
        .iter()
        .map(|name| qt.getattr(*name)?.extract::<i32>())
        .collect::<PyResult<Vec<_>>>()?
        .into_iter()
        .fold(0, |flags, item| flags | item);
    if emphasize_label {
        painter.call_method1("setClipping", (false,))?;
        // Clear the grid around the glyphs while leaving the plot visible between them.
        let ascent: f64 = metrics.call_method0("ascent")?.extract()?;
        let glyphs = gui.getattr("QPainterPath")?.call0()?;
        glyphs.call_method1(
            "addText",
            (
                x - advance / 2.0,
                top - (height + font_height) / 2.0 + ascent,
                &font,
                text,
            ),
        )?;
        let halo = pen_type.call1((widget.getattr("bg_color")?, 2.5, line))?;
        halo.call_method1("setJoinStyle", (qt.getattr("RoundJoin")?,))?;
        painter.call_method1("strokePath", (glyphs, halo))?;
    }
    painter.call_method1("drawText", (rect, flags, text))?;
    if emphasize_label {
        painter.call_method1("setClipping", (true,))?;
    }
    Ok(())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(set_skewt_trace_opacity, module)?)?;
    module.add_function(wrap_pyfunction!(effective_inflow_tag_columns, module)?)?;
    module.add_function(wrap_pyfunction!(draw_fixed_lapse_layer, module)?)?;
    module.add_function(wrap_pyfunction!(draw_fixed_hgz_layer, module)?)
}

#[cfg(test)]
mod tests;

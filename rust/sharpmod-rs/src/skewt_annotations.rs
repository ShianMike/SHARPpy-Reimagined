//! Fixed display anchors; science values remain owned by the profile.
use pyo3::{prelude::*, types::PyModule};

fn temperature_column(widget: &Bound<'_, PyAny>, temperature: f64, scale: f64) -> PyResult<f64> {
    let origin: f64 = widget.getattr("originx")?.extract()?;
    let offset: f64 = widget
        .call_method1("tmpc_to_pix", (temperature, widget.getattr("pmax")?))?
        .extract()?;
    Ok(origin + offset / scale)
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
    let x = temperature_column(widget, -30.0, scale)?;
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
    let color = widget.getattr("alert_colors")?.get_item(tier)?;
    draw_layer_bracket(
        widget,
        painter,
        (x, top, bottom),
        &format!("{rate:.1} °C/km"),
        &color,
        true,
    )
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
        None => {
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
                .call1((0, green, blue))?
        }
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

fn draw_layer_bracket(
    widget: &Bound<'_, PyAny>,
    painter: &Bound<'_, PyAny>,
    (x, top, bottom): (f64, f64, f64),
    text: &str,
    color: &Bound<'_, PyAny>,
    allow_label_outside_plot: bool,
) -> PyResult<()> {
    let py = widget.py();
    let core = PyModule::import(py, "qtpy.QtCore")?;
    let gui = PyModule::import(py, "qtpy.QtGui")?;
    let qt = core.getattr("Qt")?;
    let font = widget.getattr("esrh_font")?;
    let metrics = gui.getattr("QFontMetrics")?.call1((&font,))?;
    let width = metrics
        .call_method1("horizontalAdvance", (text,))?
        .extract::<f64>()?
        + 4.0;
    let height = widget
        .getattr("esrh_height")?
        .extract::<f64>()?
        .max(metrics.call_method0("height")?.extract::<f64>()? + 2.0);
    let rect = core
        .getattr("QRectF")?
        .call1((x - width / 2., top - height, width, height))?;
    let pen = gui
        .getattr("QPen")?
        .call1((color, 1.5, qt.getattr("SolidLine")?))?;
    painter.call_method1("setClipping", (true,))?;
    painter.call_method1("setPen", (pen,))?;
    painter.call_method1("setFont", (font,))?;
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
    if allow_label_outside_plot {
        painter.call_method1("setClipping", (false,))?;
    }
    painter.call_method1("drawText", (rect, flags, text))?;
    if allow_label_outside_plot {
        painter.call_method1("setClipping", (true,))?;
    }
    Ok(())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(draw_fixed_lapse_layer, module)?)?;
    module.add_function(wrap_pyfunction!(draw_fixed_hgz_layer, module)?)
}

#[cfg(test)]
mod tests;

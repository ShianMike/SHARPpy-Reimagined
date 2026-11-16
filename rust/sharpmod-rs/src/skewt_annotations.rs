//! Fixed display anchors; science values remain owned by the profile.
use pyo3::{prelude::*, types::PyModule};

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
    let origin_x: f64 = widget.getattr("originx")?.extract()?;
    let origin_y: f64 = widget.getattr("originy")?.extract()?;
    let reference = widget.getattr("pmax")?;
    // Use the bottom-axis -30 C tick, keeping SFC's -20 C column clear.
    let x = origin_x
        + widget
            .call_method1("tmpc_to_pix", (-30.0, reference))?
            .extract::<f64>()?
            / scale;
    let y1 = origin_y
        + widget
            .call_method1("pres_to_pix", (layer.get_item(1)?,))?
            .extract::<f64>()?
            / scale;
    let y2 = origin_y
        + widget
            .call_method1("pres_to_pix", (layer.get_item(2)?,))?
            .extract::<f64>()?
            / scale;
    let core = PyModule::import(py, "qtpy.QtCore")?;
    let gui = PyModule::import(py, "qtpy.QtGui")?;
    let qt = core.getattr("Qt")?;
    let font = widget.getattr("esrh_font")?;
    let metrics = gui.getattr("QFontMetrics")?.call1((&font,))?;
    let text = format!("{rate:.1} °C/km");
    let width = metrics
        .call_method1("horizontalAdvance", (&text,))?
        .extract::<f64>()?
        + 4.0;
    let height = widget
        .getattr("esrh_height")?
        .extract::<f64>()?
        .max(metrics.call_method0("height")?.extract::<f64>()? + 2.0);
    let rect = core
        .getattr("QRectF")?
        .call1((x - width / 2., y2 - height, width, height))?;
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
    let pen = gui
        .getattr("QPen")?
        .call1((color, 1.5, qt.getattr("SolidLine")?))?;
    painter.call_method1("setClipping", (true,))?;
    painter.call_method1("setPen", (pen,))?;
    painter.call_method1("setFont", (font,))?;
    for coords in [
        (x - 10., y1, x + 10., y1),
        (x - 10., y2, x + 10., y2),
        (x, y1, x, y2),
    ] {
        painter.call_method1("drawLine", coords)?;
    }
    let flags: i32 = ["TextDontClip", "AlignHCenter", "AlignVCenter"]
        .iter()
        .map(|name| qt.getattr(*name)?.extract::<i32>())
        .collect::<PyResult<Vec<_>>>()?
        .into_iter()
        .fold(0, |flags, item| flags | item);
    painter.call_method1("setClipping", (false,))?;
    painter.call_method1("drawText", (rect, flags, text))?;
    painter.call_method1("setClipping", (true,))?;
    Ok(())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(draw_fixed_lapse_layer, module)?)
}

#[cfg(test)]
mod tests;

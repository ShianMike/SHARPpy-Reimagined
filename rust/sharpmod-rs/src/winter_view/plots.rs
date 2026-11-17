use super::*;
use crate::winter::diagnostics::WinterDiagnostics;

// Keep winter markers clear of the existing omega meter, including bars whose
// values extend beyond its +/-10 scale. Use the same geometry as severe mode.
fn annotation_left(sound: &Bound<'_, PyAny>) -> PyResult<f64> {
    let py = sound.py();
    let left: f64 = sound.getattr("lpad")?.extract()?;
    let band = PyModule::import(py, "sharpmod.render_patches.skewt_labels")?.call_method1(
        "_skewt_omega_band",
        (sound, PyModule::import(py, "qtpy.QtGui")?),
    )?;
    if band.is_none() {
        return Ok(left + 8.);
    }
    let (_, right): (f64, f64) = band.extract()?;
    Ok((left + 8.).max(right + 12.))
}

pub(super) fn winter_markers(
    sound: &Bound<'_, PyAny>,
    hodo: &Bound<'_, PyAny>,
    data: &WinterProfile,
    d: &WinterDiagnostics,
    hodo_only: bool,
) -> PyResult<()> {
    let py = sound.py();
    let gui = PyModule::import(py, "qtpy.QtGui")?;
    let core = PyModule::import(py, "qtpy.QtCore")?;
    for (widget, is_hodo) in [(sound, false), (hodo, true)] {
        if hodo_only && !is_hodo {
            continue;
        }
        let qp = gui
            .getattr("QPainter")?
            .call1((widget.getattr("plotBitMap")?,))?;
        let result = (|| -> PyResult<()> {
            let font = gui.getattr("QFont")?.call0()?;
            font.call_method1("setPixelSize", (12,))?;
            qp.call_method1("setFont", (&font,))?;
            if is_hodo {
                for (label, uv, color, offset) in [
                    ("PBL", d.pbl_wind, "#eab566", -10.),
                    ("DGZ", d.dgz_wind, "#70b9ec", 20.),
                ] {
                    if !uv.iter().all(|v| v.is_finite()) {
                        continue;
                    }
                    let (x, y): (f64, f64) =
                        hodo.call_method1("uv_to_pix", (uv[0], uv[1]))?.extract()?;
                    let pen = gui
                        .getattr("QPen")?
                        .call1((gui.getattr("QColor")?.call1((color,))?,))?;
                    pen.call_method1("setWidthF", (2.,))?;
                    qp.call_method1("setPen", (pen,))?;
                    qp.call_method1(
                        "drawEllipse",
                        (core.getattr("QPointF")?.call1((x, y))?, 5., 5.),
                    )?;
                    qp.call_method1(
                        "drawText",
                        (core.getattr("QPointF")?.call1((x + 9., y + offset))?, label),
                    )?;
                }
            } else {
                let left = annotation_left(sound)?;
                let scale: f64 = sound.getattr("scale")?.extract()?;
                let origin: f64 = sound.getattr("originy")?.extract()?;
                let origin_x: f64 = sound.getattr("originx")?.extract()?;
                let bottom_pressure: f64 = sound.getattr("pmax")?.extract()?;
                let top: f64 = sound.getattr("tpad")?.extract()?;
                let bottom: f64 = sound.getattr("bry")?.extract()?;
                let metrics = gui.getattr("QFontMetricsF")?.call1((&font,))?;
                let text_height: f64 = metrics.call_method0("height")?.extract()?;
                let align_center = core.getattr("Qt")?.getattr("AlignCenter")?;
                let y = |p: f64| -> PyResult<f64> {
                    Ok((origin
                        + sound.call_method1("pres_to_pix", (p,))?.extract::<f64>()? / scale)
                        .clamp(top, bottom))
                };
                // Match the bottom-axis temperature ticks, keeping fixed vertical
                // columns clear of the environmental traces and omega meter.
                for (label, layers, temperature, side, color) in [
                    ("CLOUD", &d.clouds, 20., -1., "#aaaaaa"),
                    ("ICING", &data.icing, 30., 1., "#d994c5"),
                ] {
                    let x = origin_x
                        + sound
                            .call_method1("tmpc_to_pix", (temperature, bottom_pressure))?
                            .extract::<f64>()?
                            / scale;
                    let text_width: f64 = metrics
                        .call_method1("horizontalAdvance", (label,))?
                        .extract()?;
                    let pen = gui
                        .getattr("QPen")?
                        .call1((gui.getattr("QColor")?.call1((color,))?,))?;
                    pen.call_method1("setWidthF", (3.,))?;
                    qp.call_method1("setPen", (pen,))?;
                    for l in layers {
                        let a = y(l.top.p)?;
                        let b = y(l.bottom.p)?;
                        qp.call_method1(
                            "drawLine",
                            (core.getattr("QLineF")?.call1((x, a, x, b))?,),
                        )?;
                        if b - a >= text_width + 6. {
                            qp.call_method0("save")?;
                            qp.call_method1(
                                "translate",
                                (x + side * (text_height * 0.5 + 5.), (a + b) * 0.5),
                            )?;
                            qp.call_method1("rotate", (-90.,))?;
                            qp.call_method1(
                                "drawText",
                                (
                                    core.getattr("QRectF")?.call1((
                                        (a - b) * 0.5,
                                        -text_height * 0.5,
                                        b - a,
                                        text_height,
                                    ))?,
                                    &align_center,
                                    label,
                                ),
                            )?;
                            qp.call_method0("restore")?;
                        }
                    }
                }
                for (label, level, color) in [
                    ("PBL", d.pbl, "#eab566"),
                    ("FRZ", data.freezing.first().copied(), "#70b9ec"),
                    ("WB0", data.wet_zero.first().copied(), "#b591e8"),
                ] {
                    if let Some(l) = level {
                        qp.call_method1("setPen", (gui.getattr("QColor")?.call1((color,))?,))?;
                        let x = left
                            + match label {
                                "PBL" => 190.,
                                "FRZ" => 260.,
                                _ => 330.,
                            };
                        qp.call_method1(
                            "drawText",
                            (core.getattr("QPointF")?.call1((x, y(l.p)? - 3.))?, label),
                        )?;
                    }
                }
            }
            Ok(())
        })();
        qp.call_method0("end")?;
        result?;
    }
    Ok(())
}

pub(super) fn skew_layers(sound: &Bound<'_, PyAny>, data: &WinterProfile) -> PyResult<()> {
    let py = sound.py();
    let gui = PyModule::import(py, "qtpy.QtGui")?;
    let core = PyModule::import(py, "qtpy.QtCore")?;
    let painter = gui
        .getattr("QPainter")?
        .call1((sound.getattr("plotBitMap")?,))?;
    let result = (|| -> PyResult<()> {
        let left: f64 = sound.getattr("lpad")?.extract()?;
        let right: f64 = sound.getattr("brx")?.extract()?;
        let scale: f64 = sound.getattr("scale")?.extract()?;
        let origin: f64 = sound.getattr("originy")?.extract()?;
        let origin_x: f64 = sound.getattr("originx")?.extract()?;
        let bottom_pressure: f64 = sound.getattr("pmax")?.extract()?;
        let marker_left = origin_x
            + sound
                .call_method1("tmpc_to_pix", (-40.0, bottom_pressure))?
                .extract::<f64>()?
                / scale;
        let top: f64 = sound.getattr("tpad")?.extract()?;
        let bottom: f64 = sound.getattr("bry")?.extract()?;
        let font = gui.getattr("QFont")?.call0()?;
        font.call_method1("setPixelSize", (12,))?;
        painter.call_method1("setFont", (font,))?;
        let y = |p: f64| -> PyResult<f64> {
            Ok(
                (origin + sound.call_method1("pres_to_pix", (p,))?.extract::<f64>()? / scale)
                    .clamp(top, bottom),
            )
        };
        painter.call_method1(
            "setClipRect",
            (core
                .getattr("QRectF")?
                .call1((left, top, right - left, bottom - top))?,),
        )?;
        for layer in &data.dgz {
            let a = y(layer.top.p)?;
            let b = y(layer.bottom.p)?;
            let fill = gui.getattr("QColor")?.call1(("#70b9ec",))?;
            fill.call_method1("setAlpha", (26,))?;
            painter.call_method1(
                "fillRect",
                (
                    core.getattr("QRectF")?
                        .call1((left, a, right - left, b - a))?,
                    fill,
                ),
            )?;
            painter.call_method1("setPen", (gui.getattr("QColor")?.call1(("#70b9ec",))?,))?;
            painter.call_method1(
                "drawLine",
                (core
                    .getattr("QLineF")?
                    .call1((marker_left, a, marker_left, b))?,),
            )?;
            if b - a > 15. {
                painter.call_method1(
                    "drawText",
                    (
                        core.getattr("QPointF")?
                            .call1((marker_left + 8., (a + b) * 0.5 + 4.))?,
                        "DGZ",
                    ),
                )?;
            }
        }
        // Highlight warm layers with a slim marker beside the wind barbs.
        let pen = gui
            .getattr("QPen")?
            .call1((gui.getattr("QColor")?.call1(("#f2a45e",))?,))?;
        pen.call_method1("setWidthF", (3.,))?;
        painter.call_method1("setPen", (pen,))?;
        for layer in data.warm.iter().filter(|l| l.bottom.z < 5000.) {
            painter.call_method1(
                "drawLine",
                (core.getattr("QLineF")?.call1((
                    right - 8.,
                    y(layer.bottom.p)?,
                    right - 8.,
                    y(layer.top.p)?,
                ))?,),
            )?;
        }
        Ok(())
    })();
    painter.call_method0("end")?;
    result
}

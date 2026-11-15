//! Shared vector typography and the sounding palette.
use super::*;

use std::fmt::Write;

pub(super) struct Palette {
    pub bg: String,
    pub fg: String,
    pub temp: String,
    pub dew: String,
    pub wet: String,
}
impl Palette {
    pub fn from_widget(widget: &Bound<'_, PyAny>) -> PyResult<Self> {
        let color = |key: &str| -> PyResult<String> {
            PyModule::import(widget.py(), "qtpy.QtGui")?
                .getattr("QColor")?
                .call1((widget.getattr(key)?,))?
                .call_method0("name")?
                .extract()
        };
        Ok(Self {
            bg: color("bg_color")?,
            fg: color("fg_color")?,
            temp: color("temp_color")?,
            dew: color("dewp_color")?,
            wet: color("wetbulb_color")?,
        })
    }
}
pub(super) fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
pub(super) fn canvas(w: i32, h: i32, p: &Palette) -> String {
    format!("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\" viewBox=\"0 0 {w} {h}\"><rect width=\"{w}\" height=\"{h}\" fill=\"{}\"/><g font-family=\"Space Grotesk, sans-serif\" fill=\"{}\">", p.bg, p.fg)
}
pub(super) fn text(svg: &mut String, x: f64, y: f64, size: i32, value: &str, color: &str) {
    let _ = write!(
        svg,
        "<text x=\"{x:.1}\" y=\"{y:.1}\" font-size=\"{size}\" fill=\"{color}\">{}</text>",
        escape(value)
    );
}
pub(super) fn line(svg: &mut String, x: f64, y: f64, xx: f64, yy: f64, color: &str, opacity: f64) {
    let _ = write!(svg, "<path d=\"M{x:.1} {y:.1}L{xx:.1} {yy:.1}\" fill=\"none\" stroke=\"{color}\" opacity=\"{opacity}\"/>");
}
pub(super) fn number(v: f64, digits: usize, unit: &str) -> String {
    if v.is_finite() {
        format!("{v:.digits$}{unit}")
    } else {
        "N/A".into()
    }
}
pub(super) fn scalar(prof: &Bound<'_, PyAny>, name: &str) -> PyResult<f64> {
    Ok(crate::profile_input::masked_number(
        &prof.getattr(name)?,
        &PyModule::import(prof.py(), "numpy.ma")?,
    )?
    .filter(|v| *v != -9999.)
    .unwrap_or(f64::NAN))
}

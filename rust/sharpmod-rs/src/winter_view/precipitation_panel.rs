//! Precipitation estimates, a double-width source and native possible hazard.
use super::{panels::*, *};
use crate::winter::diagnostics::WinterDiagnostics;
use std::fmt::Write;

fn centered(svg: &mut String, x: f64, y: f64, size: i32, label: &str, color: &str) {
    let _ = write!(svg, "<text x=\"{x:.1}\" y=\"{y:.1}\" font-size=\"{size}\" text-anchor=\"middle\" fill=\"{color}\">{}</text>", escape(label));
}

pub(super) fn render(
    d: &WinterDiagnostics,
    prof: &Bound<'_, PyAny>,
    p: &Palette,
    [width, height]: [i32; 2],
    (hazard, hazard_color): (&str, &str),
) -> PyResult<String> {
    let w = f64::from(width);
    let h = f64::from(height);
    let slot = w / 4.;
    let hazard_top = h - 50.;
    let value_y = (58. + (h - 70.) / 2.).min(hazard_top - 14.);
    let mut svg = canvas(width, height, p);
    let _ = write!(
        svg,
        "<rect x=\"0.5\" y=\"0.5\" width=\"{}\" height=\"{}\" fill=\"none\" stroke=\"{}\"/>",
        width - 1,
        height - 1,
        p.fg
    );
    line(&mut svg, slot, 32., slot, h, &p.fg, 1.);
    line(&mut svg, slot * 2., 0., slot * 2., h, &p.fg, 1.);
    line(&mut svg, 0., 32., w, 32., &p.fg, 0.6);
    centered(&mut svg, slot, 20., 13, "Estimated SFC PTYPE", &p.fg);
    let spc: String = prof.getattr("precip_type")?.extract()?;
    for (i, (method, value)) in [
        ("Bourgouin/Birk", d.ptype),
        ("SPC / SHARPpy", spc.trim_end_matches('.')),
    ]
    .into_iter()
    .enumerate()
    {
        let x = slot * (i as f64 + 0.5);
        centered(&mut svg, x, 49., 11, method, &p.fg);
        let lower = value.to_lowercase();
        let color = if lower.contains("freezing") {
            "#e274c5"
        } else if lower.contains("snow") {
            p.wet.as_str()
        } else if lower.contains("ice") {
            "#c58ce7"
        } else {
            p.fg.as_str()
        };
        // Break long guidance into readable lines inside its own single slot.
        let mut lines = vec![String::new()];
        for word in value.to_uppercase().split_whitespace() {
            if !lines.last().unwrap().is_empty()
                && lines.last().unwrap().len() + word.len() + 1 > 18
            {
                lines.push(String::new());
            }
            let line = lines.last_mut().unwrap();
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(word);
        }
        for (j, label) in lines.iter().enumerate() {
            let y = value_y + (j as f64 - (lines.len() - 1) as f64 / 2.) * 20.;
            centered(&mut svg, x, y, 15, label, color);
        }
        if i == 0 {
            centered(&mut svg, x, h - 9., 9, "Experimental layer method", &p.fg);
        }
    }
    centered(&mut svg, slot * 3., 20., 13, "SOURCE", &p.fg);
    centered(
        &mut svg,
        slot * 3.,
        49.,
        11,
        "Precipitation Source · Height AGL",
        &p.fg,
    );
    let label = d.source.map_or_else(
        || "Not identified".into(),
        |l| format!("{:.0} hPa · {:.2} km · {:.1} °C", l.p, l.z / 1000., l.t),
    );
    // Center within the source's own content area, which ends at the hazard
    // divider. Use Qt SVG's actual text bounds, including glyph bearings.
    let hazard_heading = "Severe Weather Possible Hazard Type";
    let measure = format!(
        "{}<text id=\"source-value\" x=\"0\" y=\"0\" font-size=\"17\">{}</text><text id=\"hazard-heading\" x=\"0\" y=\"0\" font-size=\"10\">{}</text></g></svg>",
        canvas(width, height, p),
        escape(&label),
        escape(hazard_heading),
    );
    let py = prof.py();
    let bytes = PyModule::import(py, "qtpy.QtCore")?
        .getattr("QByteArray")?
        .call1((pyo3::types::PyBytes::new(py, measure.as_bytes()),))?;
    let renderer = PyModule::import(py, "qtpy.QtSvg")?
        .getattr("QSvgRenderer")?
        .call1((bytes,))?;
    let bounds = renderer.call_method1("boundsOnElement", ("source-value",))?;
    let center = bounds.call_method0("center")?;
    let x = slot * 3. - center.call_method0("x")?.extract::<f64>()?;
    let y = (58. + hazard_top) / 2. - center.call_method0("y")?.extract::<f64>()?;
    text(&mut svg, x, y, 17, &label, "#c58ce7");
    line(&mut svg, slot * 2., hazard_top, w, hazard_top, &p.fg, 0.6);
    let heading_width: f64 = renderer
        .call_method1("boundsOnElement", ("hazard-heading",))?
        .call_method0("width")?
        .extract()?;
    let available = (slot * 2. - 16.).max(1.);
    let heading_size = if heading_width > available {
        9. * available / heading_width
    } else {
        10.
    };
    let _ = write!(
        svg,
        "<text x=\"{:.1}\" y=\"{:.1}\" font-size=\"{heading_size:.3}\" text-anchor=\"middle\" fill=\"{}\">{}</text>",
        slot * 3.,
        hazard_top + 16.,
        p.fg,
        escape(hazard_heading),
    );
    centered(&mut svg, slot * 3., h - 12., 15, hazard, hazard_color);
    svg.push_str("</g></svg>");
    Ok(svg)
}

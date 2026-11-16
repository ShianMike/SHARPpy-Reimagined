//! The winter replacement for the severe-weather diagnostic bank.
use super::value_colors::{Scale, ValueColors};
use super::{panels::*, *};
use crate::winter::{diagnostics::WinterDiagnostics, wind_chill, Layer, Level};
use std::fmt::Write;

const HEIGHT: i32 = 430;

fn row(
    svg: &mut String,
    x: f64,
    right: f64,
    y: f64,
    label: &str,
    (value, color): (&str, &str),
    p: &Palette,
) {
    text(svg, x, y, 13, label, &p.fg);
    let _ = write!(svg, "<text x=\"{right}\" y=\"{y}\" font-size=\"13\" text-anchor=\"end\" font-weight=\"600\" fill=\"{color}\">{}</text>", escape(value));
}
fn split_row(
    svg: &mut String,
    [x, right, y]: [f64; 3],
    label: &str,
    parts: &[(String, String)],
    p: &Palette,
    renderer: &Bound<'_, PyAny>,
) -> PyResult<()> {
    text(svg, x, y, 13, label, &p.fg);
    // Qt's SVG renderer collapses whitespace at colored span boundaries.
    // Measure with that renderer too, so font fallback and weight match the
    // captured image. Keep an explicit gap on both sides of each separator.
    let mut measure = canvas(1200, HEIGHT, p);
    for (i, (value, _)) in parts.iter().enumerate() {
        let _ = write!(measure, "<text id=\"part{i}\" x=\"0\" y=\"20\" font-size=\"13\" text-anchor=\"end\" font-weight=\"600\">{}</text>", escape(value));
    }
    measure.push_str("</g></svg>");
    let py = renderer.py();
    let bytes = PyModule::import(py, "qtpy.QtCore")?
        .getattr("QByteArray")?
        .call1((pyo3::types::PyBytes::new(py, measure.as_bytes()),))?;
    renderer.call_method1("load", (bytes,))?;
    let mut end = right;
    for (i, (value, color)) in parts.iter().enumerate().rev() {
        let _ = write!(svg, "<text x=\"{end}\" y=\"{y}\" font-size=\"13\" text-anchor=\"end\" font-weight=\"600\" fill=\"{color}\">{}</text>", escape(value));
        end -= renderer
            .call_method1("boundsOnElement", (format!("part{i}"),))?
            .call_method0("width")?
            .extract::<f64>()?
            + 4.;
    }
    Ok(())
}
fn wind(u: f64, v: f64) -> String {
    let speed = u.hypot(v);
    if !speed.is_finite() {
        return "N/A".into();
    }
    if speed < 0.5 {
        return "Calm".into();
    }
    let dir = (270. - v.atan2(u).to_degrees()).rem_euclid(360.);
    let compass = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW",
        "NW", "NNW",
    ];
    format!(
        "{} {speed:.0} kt",
        compass[(dir / 22.5).round() as usize % 16]
    )
}
fn zero(levels: &[Level], surface: f64) -> String {
    levels.first().map_or_else(
        || {
            if surface.is_finite() {
                "No crossing".into()
            } else {
                "N/A".into()
            }
        },
        |l| format!("{:.0} m · {:.0} hPa", l.z, l.p),
    )
}
fn count(checks: &[Option<bool>]) -> String {
    let known = checks.iter().filter(|x| x.is_some()).count();
    let met = checks.iter().filter(|x| **x == Some(true)).count();
    if known == checks.len() {
        format!("{met}/{} ingredients met", checks.len())
    } else {
        format!("{met} met · {} unavailable", checks.len() - known)
    }
}
fn check(
    svg: &mut String,
    y: f64,
    label: &str,
    value: &str,
    state: Option<bool>,
    p: &Palette,
    colors: &ValueColors,
) {
    text(svg, 860., y, 12, label, &p.fg);
    let status = match state {
        Some(true) => "Met",
        Some(false) => "Not met",
        None => "N/A",
    };
    let color = colors.check_color(state);
    let _ = write!(svg, "<text x=\"1125\" y=\"{y}\" font-size=\"12\" text-anchor=\"end\" fill=\"{color}\">{}</text>", escape(value));
    let _ = write!(svg, "<text x=\"1190\" y=\"{y}\" font-size=\"11\" text-anchor=\"end\" fill=\"{color}\">{status}</text>");
}

fn footer(svg: &mut String, p: &Palette, renderer: &Bound<'_, PyAny>) -> PyResult<()> {
    let notes: [(f64, f64, f64, &[&str]); 4] = [
        (
            10.,
            300.,
            1.,
            &[
                "Base / top / depth: m AGL; Tw: °C",
                "Tw: warm-layer max / cold-layer min",
                "Energy: signed J/kg below source",
            ],
        ),
        (
            320.,
            550.,
            1.,
            &[
                "DGZ: −12 to −18 °C",
                "Negative omega / OPRH: ascent",
                "SLR: snow at surface only; not a snowfall forecast.",
            ],
        ),
        (
            570.,
            840.,
            0.72,
            &[
                "PBL context: Sczepanski et al. (2024)",
                "Snow squalls: Banacos et al. (2014)",
            ],
        ),
        (
            860.,
            1190.,
            0.72,
            &[
                "Drizzle: Huffman & Norman (1988)",
                "Rauber et al. (2000); McDonough et al. (2004)",
            ],
        ),
    ];
    let py = renderer.py();
    let byte_array = PyModule::import(py, "qtpy.QtCore")?.getattr("QByteArray")?;
    for (column, (left, right, opacity, notes)) in notes.into_iter().enumerate() {
        let mut lines = Vec::new();
        for note in notes {
            let mut current = String::new();
            for word in note.split_whitespace() {
                let candidate = if current.is_empty() {
                    word.to_owned()
                } else {
                    format!("{current} {word}")
                };
                // Use the same SVG family, size and Qt font fallback as the
                // capture. Character counts miss proportional glyph widths.
                let mut measure = canvas(1200, HEIGHT, p);
                let _ = write!(
                    measure,
                    "<text id=\"note\" x=\"0\" y=\"20\" font-size=\"11\">{}</text></g></svg>",
                    escape(&candidate)
                );
                let bytes =
                    byte_array.call1((pyo3::types::PyBytes::new(py, measure.as_bytes()),))?;
                renderer.call_method1("load", (bytes,))?;
                let width: f64 = renderer
                    .call_method1("boundsOnElement", ("note",))?
                    .call_method0("width")?
                    .extract()?;
                // Both text edges stay 10 SVG units inside their column.
                if !current.is_empty() && width > right - left {
                    lines.push(current);
                    current = word.to_owned();
                } else {
                    current = candidate;
                }
            }
            lines.push(current);
        }
        // Fill opacity preserves vector glyphs at capture resolution. Each
        // column ends on the same baseline, regardless of its wrapped height.
        let _ = write!(
            svg,
            "<g id=\"winter-footer-{column}\" fill-opacity=\"{opacity}\">"
        );
        let first = f64::from(HEIGHT) - 12. - 18. * (lines.len() - 1) as f64;
        for (i, label) in lines.iter().enumerate() {
            text(svg, left, first + i as f64 * 18., 11, label, &p.fg);
        }
        svg.push_str("</g>");
    }
    Ok(())
}

pub(super) fn diagnostics(
    data: &WinterProfile,
    d: &WinterDiagnostics,
    prof: &Bound<'_, PyAny>,
    p: &Palette,
) -> PyResult<String> {
    let colors = ValueColors::from_palette(prof.py(), p)?;
    let value_renderer = PyModule::import(prof.py(), "qtpy.QtSvg")?
        .getattr("QSvgRenderer")?
        .call0()?;
    let mut svg = canvas(1200, HEIGHT, p);
    // The adjacent height chart owns the right-hand divider. Do not add a
    // second outline beside it when the vector and native panels touch.
    let _ = write!(
        svg,
        "<path d=\"M1199.5 0.5H0.5V{}H1199.5\" fill=\"none\" stroke=\"{}\"/>",
        f64::from(HEIGHT) - 0.5,
        p.fg
    );
    for x in [310., 560., 850.] {
        line(&mut svg, x, 0., x, f64::from(HEIGHT), &p.fg, 0.6);
    }
    for (x, title) in [
        (10., "THERMAL LAYERS"),
        (320., "SNOW GROWTH · DGZ"),
        (570., "COLD & PBL"),
        (860., "FREEZING FOG"),
    ] {
        text(&mut svg, x, 25., 14, title, &p.fg);
    }
    text(
        &mut svg,
        10.,
        48.,
        12,
        "Wet-bulb Melting / Refreezing",
        &p.fg,
    );
    text(&mut svg, 10., 76., 12, "Layer", &p.fg);
    // Share right edges between headings and values, leaving wider cells for
    // signed wet-bulb temperatures and energies.
    let numeric_right = [122., 163., 204., 252., 300.];
    for (right, label) in numeric_right
        .into_iter()
        .zip(["Base", "Top", "Depth", "Tw", "J/kg"])
    {
        let _ = write!(svg, "<text x=\"{right}\" y=\"76\" font-size=\"11\" font-family=\"JetBrains Mono, monospace\" text-anchor=\"end\">{label}</text>");
    }
    line(&mut svg, 10., 86., 300., 86., &p.fg, 0.3);
    for (i, layer) in d.thermal.iter().take(8).enumerate() {
        let y = 106. + i as f64 * 29.;
        let warm = layer.energy > 0.;
        let label = match (warm, layer.bottom.z <= 1.) {
            (true, true) => "SFC Warm",
            (false, true) => "SFC Cold",
            (true, false) => "Warm Aloft",
            (false, false) => "Cold Aloft",
        };
        text(
            &mut svg,
            10.,
            y,
            12,
            label,
            &colors.color(Scale::Energy, layer.energy),
        );
        for (right, (value, color)) in numeric_right.into_iter().zip([
            (number(layer.bottom.z, 0, ""), p.fg.clone()),
            (number(layer.top.z, 0, ""), p.fg.clone()),
            (
                number(layer.depth(), 0, ""),
                colors.color(
                    if warm {
                        Scale::WarmDepth
                    } else {
                        Scale::ColdDepth
                    },
                    layer.depth(),
                ),
            ),
            (
                number(layer.extreme, 1, ""),
                colors.color(Scale::Temperature, layer.extreme),
            ),
            (
                number(layer.energy, 0, ""),
                colors.color(Scale::Energy, layer.energy),
            ),
        ]) {
            let _ = write!(svg, "<text x=\"{right}\" y=\"{y}\" font-size=\"11\" font-family=\"JetBrains Mono, monospace\" text-anchor=\"end\" fill=\"{color}\">{}</text>", escape(&value));
        }
    }
    if d.thermal.is_empty() {
        text(
            &mut svg,
            10.,
            106.,
            12,
            "No source layers / missing data",
            &p.fg,
        );
    }
    if d.thermal.len() > 8 {
        text(
            &mut svg,
            10.,
            335.,
            12,
            &format!("{} more resolved layers", d.thermal.len() - 8),
            &p.fg,
        );
    }
    let sfc = data.levels[0];
    let [rh, rhi, omega] = data.dgz_means();
    let dgz_depth = data.dgz.iter().map(Layer::depth).sum::<f64>();
    let bounds = data.dgz.first().map_or_else(
        || "None in profile".into(),
        |l| format!("{:.2}–{:.2} km", l.bottom.z / 1000., l.top.z / 1000.),
    );
    let snow = d.ptype.to_lowercase().contains("snow");
    let slr_value = if snow {
        data.snow_ratio().unwrap_or(f64::NAN)
    } else {
        f64::NAN
    };
    let slr = if snow {
        number(slr_value, 1, ":1")
    } else {
        "Not snow".into()
    };
    let column_pw = scalar(prof, "pwat")?;
    let dgz_rows = [
        ("Lowest DGZ", bounds, p.fg.clone()),
        (
            "Layers / Depth",
            if data.dgz.is_empty() {
                "None".into()
            } else {
                format!("{} / {dgz_depth:.0} m", data.dgz.len())
            },
            colors.color(Scale::ColdDepth, dgz_depth),
        ),
        (
            "Omega",
            number(omega, 1, " µbar/s"),
            colors.color(Scale::Omega, omega),
        ),
        (
            "DGZ PW",
            number(d.dgz_pw, 3, " in"),
            colors.color(Scale::DgzWater, d.dgz_pw),
        ),
        (
            "OPRH",
            number(d.oprh, 3, ""),
            colors.color(Scale::Oprh, d.oprh),
        ),
        (
            "Kuchera SLR",
            slr,
            colors.color(Scale::SnowRatio, slr_value),
        ),
        (
            "Column PWAT",
            number(column_pw, 2, " in"),
            colors.color(Scale::ColumnWater, column_pw),
        ),
        ("FRZ", zero(&data.freezing, sfc.t), p.fg.clone()),
        ("WB0", zero(&data.wet_zero, sfc.tw), p.fg.clone()),
    ];
    for (i, (label, value, color)) in dgz_rows.iter().enumerate() {
        row(
            &mut svg,
            320.,
            550.,
            53. + (i + usize::from(i >= 2)) as f64 * 27.,
            label,
            (value, color),
            p,
        );
    }
    split_row(
        &mut svg,
        [320., 550., 107.],
        "RH / RHi",
        &[
            (number(rh, 0, "%"), colors.color(Scale::Humidity, rh)),
            ("/".into(), p.fg.clone()),
            (number(rhi, 0, "%"), colors.color(Scale::Humidity, rhi)),
        ],
        p,
        &value_renderer,
    )?;

    let wind10 = d.wind10.map_or_else(|| "N/A".into(), |l| wind(l.u, l.v));
    let chill_value = d
        .wind10
        .and_then(|l| wind_chill(sfc.t, l.speed()))
        .unwrap_or(f64::NAN);
    let chill = if chill_value.is_finite() {
        format!("{:.0} °F / {chill_value:.1} °C", chill_value * 1.8 + 32.)
    } else {
        "N/A".into()
    };
    let lapse = d.pbl.map_or(f64::NAN, |l| (sfc.t - l.t) / l.z * 1000.);
    split_row(
        &mut svg,
        [570., 840., 53.],
        "Surface T / TW",
        &[
            (
                number(sfc.t, 1, ""),
                colors.color(Scale::Temperature, sfc.t),
            ),
            ("/".into(), p.fg.clone()),
            (
                number(sfc.tw, 1, " °C"),
                colors.color(Scale::Temperature, sfc.tw),
            ),
        ],
        p,
        &value_renderer,
    )?;
    for (i, (label, value, color)) in [
        (
            "Wind Chill",
            chill,
            colors.color(Scale::WindChill, chill_value),
        ),
        (
            "PBL Gust",
            number(d.gust, 0, " kt"),
            colors.color(Scale::Wind, d.gust),
        ),
        (
            "PBL Depth",
            d.pbl.map_or_else(|| "N/A".into(), |l| number(l.z, 0, " m")),
            p.fg.clone(),
        ),
        (
            "PBL Lapse Rate",
            number(lapse, 1, " °C/km"),
            colors.color(Scale::LapseRate, lapse),
        ),
        (
            "Snow Squall",
            number(d.squall, 2, ""),
            colors.color(Scale::SnowSquall, d.squall),
        ),
    ]
    .iter()
    .enumerate()
    {
        row(
            &mut svg,
            570.,
            840.,
            77. + i as f64 * 24.,
            label,
            (value, color),
            p,
        );
    }
    line(&mut svg, 570., 191., 840., 191., &p.fg, 0.25);
    text(&mut svg, 570., 214., 14, "WINDS · GROUND RELATIVE", &p.fg);
    let winds = [
        (
            "10 m (interp.)",
            wind10,
            colors.color(Scale::Wind, d.wind10.map_or(f64::NAN, Level::speed)),
        ),
        (
            "PBL Mean",
            wind(d.pbl_wind[0], d.pbl_wind[1]),
            colors.color(Scale::Wind, d.pbl_wind[0].hypot(d.pbl_wind[1])),
        ),
        (
            "DGZ Mean",
            wind(d.dgz_wind[0], d.dgz_wind[1]),
            colors.color(Scale::Wind, d.dgz_wind[0].hypot(d.dgz_wind[1])),
        ),
        (
            "Max 0–2 km",
            number(d.max_wind, 0, " kt"),
            colors.color(Scale::Wind, d.max_wind),
        ),
    ];
    for (i, (label, value, color)) in winds.iter().enumerate() {
        row(
            &mut svg,
            570.,
            840.,
            241. + i as f64 * 24.,
            label,
            (value, color),
            p,
        );
    }
    for (i, level) in [850., 700., 500.].into_iter().enumerate() {
        let (wind_text, speed, temperature) = data.at_pressure(level).map_or_else(
            || ("N/A".into(), f64::NAN, f64::NAN),
            |l| (wind(l.u, l.v), l.speed(), l.t),
        );
        split_row(
            &mut svg,
            [570., 840., 337. + i as f64 * 24.],
            &format!("{level:.0} hPa"),
            &[
                (wind_text, colors.color(Scale::Wind, speed)),
                ("·".into(), p.fg.clone()),
                (
                    number(temperature, 1, " °C"),
                    colors.color(Scale::Temperature, temperature),
                ),
            ],
            p,
            &value_renderer,
        )?;
    }

    text(&mut svg, 860., 48., 13, &count(&d.fog), &p.fg);
    let inv = d.inversion.map_or_else(
        || "N/A".into(),
        |(t, z)| format!("+{t:.1}° / {:.2} km", z / 1000.),
    );
    for (i, (label, value)) in [
        ("Surface T < 0 °C", number(sfc.t, 1, " °C")),
        ("Surface RH ≥95%", number(sfc.rh, 0, "%")),
        (
            "10m Wind ≤6 kt",
            d.wind10
                .map_or_else(|| "N/A".into(), |l| number(l.speed(), 0, " kt")),
        ),
        ("SFC IVRN > 0.5 °C", inv),
    ]
    .iter()
    .enumerate()
    {
        check(
            &mut svg,
            77. + i as f64 * 25.,
            label,
            value,
            d.fog[i],
            p,
            &colors,
        );
    }
    svg.push_str("<g fill-opacity=\"0.72\">");
    text(
        &mut svg,
        860.,
        168.,
        10,
        "Fog context: Gultepe et al. (2007)",
        &p.fg,
    );
    svg.push_str("</g>");
    line(&mut svg, 860., 174., 1190., 174., &p.fg, 0.25);
    text(&mut svg, 860., 200., 15, "FREEZING DRIZZLE", &p.fg);
    text(&mut svg, 860., 225., 13, &count(&d.drizzle), &p.fg);
    let cloud = d.clouds.iter().find(|l| l.bottom.z <= 2000.);
    for (i, (label, value)) in [
        ("Surface TW ≤ 0 °C", number(sfc.tw, 1, " °C")),
        (
            "Cloud Top > −10 °C",
            cloud.map_or_else(|| "N/A".into(), |l| number(l.top.t, 1, " °C")),
        ),
        ("1km Above: RHi ≤75%", number(d.rh_above, 0, "%")),
        (
            "Cloud Depth ≥500 m",
            cloud.map_or_else(|| "N/A".into(), |l| number(l.depth(), 0, " m")),
        ),
        (
            "Lift in Cloud: Omega < 0",
            number(d.cloud_omega, 1, " µbar/s"),
        ),
    ]
    .iter()
    .enumerate()
    {
        check(
            &mut svg,
            252. + i as f64 * 25.,
            label,
            value,
            d.drizzle[i],
            p,
            &colors,
        );
    }
    row(
        &mut svg,
        860.,
        1190.,
        380.,
        "Icing Screen",
        (
            &format!(
                "{} layers · {:.0} m",
                data.icing.len(),
                data.icing.iter().map(Layer::depth).sum::<f64>().max(0.)
            ),
            &p.fg,
        ),
        p,
    );
    // Context citations describe the ingredients, not calibrated hazard scores.
    footer(&mut svg, p, &value_renderer)?;
    svg.push_str("</g></svg>");
    Ok(svg)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires the pinned offscreen Qt environment"]
    fn winter_footer_stays_within_its_column_and_bottom_padding() {
        std::env::set_var("QT_QPA_PLATFORM", "offscreen");
        Python::initialize();
        Python::attach(|py| -> PyResult<()> {
            let app_type = PyModule::import(py, "qtpy.QtWidgets")?.getattr("QApplication")?;
            let existing = app_type.call_method0("instance")?;
            let _app = if existing.is_none() {
                app_type.call1((pyo3::types::PyList::empty(py),))?
            } else {
                existing
            };
            let renderer = PyModule::import(py, "qtpy.QtSvg")?
                .getattr("QSvgRenderer")?
                .call0()?;
            let palette = Palette {
                bg: "#000000".into(),
                fg: "#ffffff".into(),
                temp: "#ff0000".into(),
                dew: "#00ff00".into(),
                wet: "#00ffff".into(),
            };
            let mut svg = canvas(1200, HEIGHT, &palette);
            footer(&mut svg, &palette, &renderer)?;
            svg.push_str("</g></svg>");
            let bytes = PyModule::import(py, "qtpy.QtCore")?
                .getattr("QByteArray")?
                .call1((pyo3::types::PyBytes::new(py, svg.as_bytes()),))?;
            assert!(renderer.call_method1("load", (bytes,))?.extract::<bool>()?);
            for (column, (left, right)) in [(10., 300.), (320., 550.), (570., 840.), (860., 1190.)]
                .into_iter()
                .enumerate()
            {
                let bounds = renderer
                    .call_method1("boundsOnElement", (format!("winter-footer-{column}"),))?;
                assert!(bounds.call_method0("left")?.extract::<f64>()? >= left - 1.);
                assert!(bounds.call_method0("right")?.extract::<f64>()? <= right + 1.);
                assert!(bounds.call_method0("bottom")?.extract::<f64>()? <= f64::from(HEIGHT) - 5.);
            }
            Ok(())
        })
        .unwrap();
    }
}

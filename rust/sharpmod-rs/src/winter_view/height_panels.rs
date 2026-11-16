//! Winter series in the same native height-chart widgets as the severe view.
use super::{panels::Palette, *};
use crate::winter::Level;
use numpy::IntoPyArray;
use pyo3::types::PyList;

type ChartSeries<'a> = (&'a str, &'a str, &'a str, fn(Level) -> f64);
type ChartDefinition<'a> = (
    &'a str,
    &'a str,
    &'a [ChartSeries<'a>],
    Option<f64>,
    Option<f64>,
);

pub(super) fn mount(
    root: &Bound<'_, PyAny>,
    data: &WinterProfile,
    palette: &Palette,
    [x, y, width, height]: [i32; 4],
) -> PyResult<()> {
    let py = root.py();
    let qt = PyModule::import(py, "qtpy.QtWidgets")?;
    let api = PyModule::import(py, "sharpmod.viz.height_charts")?;
    let group = qt.getattr("QFrame")?.call1((root,))?;
    group.call_method1("setObjectName", ("winter_height_profiles",))?;
    group.call_method1(
        "setStyleSheet",
        (format!(
            "QFrame#winter_height_profiles {{ border: 1px solid {}; border-left: 0px; background-color: {}; }}",
            palette.fg, palette.bg
        ),),
    )?;
    group.call_method1("setGeometry", (x, y, width, height))?;
    let layout = qt.getattr("QHBoxLayout")?.call1((&group,))?;
    // Each height chart paints its left divider; the container owns only the
    // top, right and bottom outline. Its styled frame supplies those insets.
    layout.call_method1("setContentsMargins", (0, 0, 0, 0))?;
    layout.call_method1("setSpacing", (0,))?;

    // Keep missing levels as gaps; add the exact upper endpoint without
    // extrapolating profiles that do not reach 3 km AGL.
    let mut levels: Vec<_> = data
        .levels
        .iter()
        .copied()
        .filter(|l| !l.z.is_finite() || l.z < 3000.)
        .collect();
    if let Some(top) = data.at_height(3000.) {
        levels.push(top);
    }
    let heights = levels
        .iter()
        .map(|l| l.z / 1000.)
        .collect::<Vec<_>>()
        .into_pyarray(py);
    let charts: [ChartDefinition<'_>; 4] = [
        (
            "T / Wet Bulb",
            "Temperature (°C)",
            &[
                ("t", "T", &palette.temp, |l| l.t),
                ("tw", "Tw", &palette.wet, |l| l.tw),
            ],
            None,
            None,
        ),
        (
            "Relative Humidity",
            "RH / RHi (%)",
            &[
                ("rh", "RH", &palette.dew, |l| l.rh),
                ("rhi", "RHi", &palette.wet, |l| l.rhi),
            ],
            Some(0.),
            Some(120.),
        ),
        (
            "Wind Speed",
            "Wind (kt)",
            &[("wind", "Wind", "#e9b44c", Level::speed)],
            Some(0.),
            None,
        ),
        (
            "θe Profile",
            "θe (K)",
            &[("theta_e", "θe", "#c58ce7", Level::theta_e)],
            None,
            None,
        ),
    ];
    let widgets = PyList::empty(py);
    for (title, label, series, min, max) in charts {
        let chart = api.getattr("HeightChartInset")?.call1((&group,))?;
        chart.setattr("TITLE", title)?;
        chart.setattr("X_LABEL", label)?;
        chart.setattr("MAX_HEIGHT_KM", 3.)?;
        chart.setattr("X_MIN", min)?;
        chart.setattr("X_MAX", max)?;
        chart.setattr("ZERO_LINE", title == "T / Wet Bulb")?;
        let prefs = PyDict::new(py);
        prefs.set_item("bg_color", &palette.bg)?;
        prefs.set_item("fg_color", &palette.fg)?;
        chart.call_method("setPreferences", (false,), Some(&prefs))?;
        let colors = chart.getattr("_palette")?;
        let values = PyDict::new(py);
        let keys = PyList::empty(py);
        for &(key, legend, color, value) in series {
            colors.set_item(key, color)?;
            keys.append((key, legend, key))?;
            values.set_item(
                key,
                levels
                    .iter()
                    .copied()
                    .map(|level| {
                        if level.z.is_finite() {
                            value(level)
                        } else {
                            f64::NAN
                        }
                    })
                    .collect::<Vec<_>>()
                    .into_pyarray(py),
            )?;
        }
        chart.setattr("SERIES", keys)?;
        chart.setattr(
            "data",
            api.getattr("HeightSeries")?.call1((&heights, values))?,
        )?;
        layout.call_method1("addWidget", (&chart, 1))?;
        chart.call_method0("_redraw")?;
        chart.call_method0("show")?;
        widgets.append(&chart)?;
    }
    // Retain the Python wrappers as well as their Qt parent ownership.
    root.setattr("winter_height_charts", widgets)?;
    group.call_method0("show")?;
    Ok(())
}

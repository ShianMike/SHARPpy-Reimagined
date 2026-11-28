//! Global 500 mb geopotential height anomaly product and ERA5 climatology bindings.

use numpy::{
    ndarray::{Array2, ArrayD},
    AllowTypeChange, IntoPyArray, PyArray2, PyArrayDyn, PyArrayLike2, PyArrayLikeDyn,
    PyUntypedArrayMethods,
};
use pyo3::{
    exceptions::{PyKeyError, PyValueError},
    prelude::*,
    types::{PyDict, PyTuple},
};
use rayon::prelude::*;
use sharpmod_weather::hgt500_climatology;
use time::OffsetDateTime;

const PRODUCT_KEY: &str = "hgt-anom-500";
const ANOMALY_FLOOR: f64 = -200.0;
const ANOMALY_TICKS: [f64; 9] = [-40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0];

// 82 1-dam stepped bands from <-40 dam (index 0) through >=+40 dam (index 81).
const HGT500_ANOMALY_COLOURS: [&str; 82] = [
    "#caf5fa", "#cfdef0", "#d4c4f1", "#d8aef0", "#da95ea", "#e07ce9", "#e07ce9", "#c66dcc",
    "#b55bb4", "#a44a9c", "#923983", "#79236b", "#6e154e", "#6f1651", "#761f63", "#782778",
    "#782d8a", "#7b339e", "#7c3ab2", "#8040c6", "#8242ce", "#7b42cd", "#6a41cb", "#5a41c9",
    "#4940c3", "#383fbf", "#283eba", "#2d46b4", "#435cbc", "#5972c4", "#7088cd", "#869dd5",
    "#9cb3dd", "#b2c9e5", "#b5cce8", "#c1d5ec", "#ceddf0", "#dae6f4", "#e6eef7", "#f3f7fb",
    "#ffffff", "#ffffff", "#fef5ec", "#fdecd9", "#fde2c6", "#fcd8b2", "#fbcf9f", "#fac58c",
    "#fbb770", "#f4a65d", "#ed954b", "#e68438", "#df7325", "#d86213", "#d15100", "#cd4b00",
    "#c54203", "#bc3907", "#b4300a", "#ac260d", "#a31d11", "#9b1414", "#900e0f", "#841016",
    "#77121c", "#6b1423", "#5f162a", "#521830", "#461a37", "#441834", "#46283e", "#493748",
    "#4b4752", "#4d575c", "#506666", "#537671", "#567b75", "#75828b", "#8f999f", "#abacab",
    "#c3c0bd", "#e0d5cc",
];

pub(super) fn supports_model(model: &str) -> bool {
    matches!(
        model,
        "gfs"
            | "aigfs"
            | "openmeteo-ecmwf-ifs025"
            | "ecmwf-ifs-9km"
            | "gdps"
            | "openmeteo-icon-global"
    )
}

fn anomaly_stops() -> Vec<(f64, &'static str)> {
    let mut stops = Vec::with_capacity(HGT500_ANOMALY_COLOURS.len() + 1);
    for (index, &colour) in HGT500_ANOMALY_COLOURS.iter().enumerate() {
        stops.push(((index as i32 - 41) as f64, colour));
    }
    stops.push((
        41.0,
        HGT500_ANOMALY_COLOURS[HGT500_ANOMALY_COLOURS.len() - 1],
    ));
    stops
}

#[pyfunction]
fn metres_to_dam<'py>(
    py: Python<'py>,
    values: PyArrayLikeDyn<'py, f64, AllowTypeChange>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let shape = values.shape().to_vec();
    let values: Vec<f64> = values.as_array().iter().copied().collect();
    let output = py.detach(move || {
        values
            .into_iter()
            .map(|value| {
                let converted = (value * 0.1) as f32;
                if value.is_finite() && converted.is_finite() {
                    converted
                } else {
                    f32::NAN
                }
            })
            .collect()
    });
    Ok(ArrayD::from_shape_vec(shape, output)
        .expect("conversion preserves the validated input shape")
        .into_pyarray(py))
}

#[pyfunction]
fn derive_hgt500_anomaly<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArray2<f32>>> {
    let hgt_item = fields
        .get_item("hgt500")?
        .ok_or_else(|| PyKeyError::new_err("decoded 500 mb height is missing"))?;
    let hgt: PyArrayLike2<'py, f32, AllowTypeChange> = hgt_item.extract()?;
    let array = hgt.as_array();
    let (rows, cols) = array.dim();
    if rows < 2 || cols == 0 {
        return Err(PyValueError::new_err(format!(
            "invalid 500 mb height grid dimensions ({rows}, {cols})"
        )));
    }
    let timestamp: i64 = fields
        .get_item("__valid_time")?
        .ok_or_else(|| PyKeyError::new_err("forecast valid time is missing"))?
        .extract()?;
    let valid_time = OffsetDateTime::from_unix_timestamp(timestamp)
        .map_err(|error| PyValueError::new_err(format!("invalid forecast valid time: {error}")))?;
    let values: Vec<f32> = array.iter().copied().collect();
    let output = py.detach(move || {
        let climo = hgt500_climatology::evaluate_grid(valid_time);
        let weights = hgt500_climatology::lon_weights(cols);
        let mut out = vec![0.0_f32; rows * cols];
        out.par_chunks_mut(cols).enumerate().for_each(|(row, dst)| {
            let start = row * cols;
            hgt500_climatology::anomaly_row_dam(
                &climo,
                &weights,
                row,
                rows,
                &values[start..start + cols],
                dst,
            );
        });
        out
    });
    Ok(Array2::from_shape_vec((rows, cols), output)
        .expect("anomaly derivation preserves the input grid shape")
        .into_pyarray(py))
}

pub(super) fn extend_catalogue(
    py: Python<'_>,
    model: &str,
    products: &mut Vec<Py<PyAny>>,
) -> PyResult<()> {
    if !supports_model(model) {
        return Ok(());
    }
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field = catalogue
        .getattr("FieldSpec")?
        .call1(("hgt500", "prs", "HGT", "500 mb"))?;

    let palette_options = PyDict::new(py);
    palette_options.set_item("stepped", true)?;
    palette_options.set_item("floor", ANOMALY_FLOOR)?;
    palette_options.set_item("ticks", PyTuple::new(py, ANOMALY_TICKS)?)?;
    palette_options.set_item("display", "fixed")?;
    let palette = catalogue.getattr("Palette")?.call(
        (PyTuple::new(py, anomaly_stops())?, "dam"),
        Some(&palette_options),
    )?;

    let contour_options = PyDict::new(py);
    contour_options.set_item("convert", wrap_pyfunction!(metres_to_dam, py)?)?;
    contour_options.set_item("label", "dam")?;
    let contour = catalogue
        .getattr("ContourSpec")?
        .call(("hgt500", 3.0), Some(&contour_options))?;

    let options = PyDict::new(py);
    options.set_item("key", PRODUCT_KEY)?;
    options.set_item("label", "500 mb Height Anomaly")?;
    options.set_item("chip", "500 mb Anom")?;
    options.set_item("category", "Height and Wind")?;
    options.set_item("fields", (field,))?;
    options.set_item("derive", wrap_pyfunction!(derive_hgt500_anomaly, py)?)?;
    options.set_item("palette", palette)?;
    options.set_item("contour", contour)?;
    options.set_item("wind", false)?;
    options.set_item(
        "description",
        "500 mb geopotential height anomaly relative to the 1990-2019 ERA5 6-hourly climatology, in decametres, with 500 mb height contours every 3 dam.",
    )?;
    options.set_item(
        "caveat",
        "Anomalies are computed against a bundled 1990-2019 ERA5 6-hourly 500 hPa harmonic climatology (1.5 degree grid).",
    )?;
    products.push(
        catalogue
            .getattr("HrrrProduct")?
            .call((), Some(&options))?
            .unbind(),
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn height_anomaly_targets_only_the_six_global_models() {
        for model in [
            "gfs",
            "aigfs",
            "openmeteo-ecmwf-ifs025",
            "ecmwf-ifs-9km",
            "gdps",
            "openmeteo-icon-global",
        ] {
            assert!(supports_model(model), "{model} must be supported");
        }
        for model in [
            "hrrr",
            "rap",
            "nam",
            "nam-3km-conus",
            "rrfs-a",
            "hrw-wrf-arw",
            "hrw-wrf-nssl",
            "hrw-fv3",
            "rdps",
            "hrdps",
            "refs",
            "refs-m1",
            "unknown",
        ] {
            assert!(!supports_model(model), "{model} must not be supported");
        }
    }

    #[test]
    fn anomaly_palette_preserves_82_stepped_bands_and_anchors() {
        let stops = anomaly_stops();
        assert_eq!(stops.len(), 83);
        assert!(stops.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(stops.iter().all(|(_, hex)| {
            hex.len() == 7
                && hex.starts_with('#')
                && hex[1..].bytes().all(|b| b.is_ascii_hexdigit())
        }));
        assert!(ANOMALY_FLOOR < stops[0].0);
        assert!(ANOMALY_TICKS
            .iter()
            .all(|tick| stops.iter().any(|(val, _)| val == tick)));

        for (index, expected) in [
            (0, (-41.0, "#caf5fa")),
            (1, (-40.0, "#cfdef0")),
            (11, (-30.0, "#79236b")),
            (21, (-20.0, "#7b42cd")),
            (31, (-10.0, "#869dd5")),
            (40, (-1.0, "#ffffff")),
            (41, (0.0, "#ffffff")),
            (51, (10.0, "#e68438")),
            (61, (20.0, "#9b1414")),
            (71, (30.0, "#493748")),
            (81, (40.0, "#e0d5cc")),
            (82, (41.0, "#e0d5cc")),
        ] {
            assert_eq!(stops[index], expected, "stop {index}");
        }
    }
}

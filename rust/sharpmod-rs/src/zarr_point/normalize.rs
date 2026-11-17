use super::*;
use numpy::{ndarray::Array2, IntoPyArray, PyReadonlyArray1};
#[pyfunction]
#[allow(clippy::too_many_arguments)] // Match the cached-point/provider constructor boundary.
#[pyo3(name="hrrr_zarr_point_dataset",signature=(levels,columns,selected_lat,selected_lon,run,requested_lat,requested_lon,surface=None))]
pub(super) fn point_dataset<'py>(
    py: Python<'py>,
    levels: &Bound<'py, PyAny>,
    columns: &Bound<'py, PyDict>,
    selected_lat: f64,
    selected_lon: f64,
    run: &Bound<'py, PyAny>,
    requested_lat: f64,
    requested_lon: f64,
    surface: Option<&Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyAny>> {
    let np = PyModule::import(py, "numpy")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("dtype", np.getattr("float64")?)?;
    let array = np.call_method("asarray", (levels,), Some(&kwargs))?;
    let levels: Vec<_> = array
        .extract::<PyReadonlyArray1<'_, f64>>()?
        .as_array()
        .iter()
        .copied()
        .collect();
    let mut order: Vec<_> = (0..levels.len()).collect();
    order.sort_by(|&a, &b| levels[b].total_cmp(&levels[a]));
    let mut data = std::collections::HashMap::new();
    for (key, value) in columns.iter() {
        let key: String = key.extract()?;
        if value.is_none() {
            continue;
        }
        let value = np.call_method("asarray", (value,), Some(&kwargs))?;
        let values = value.extract::<PyReadonlyArray1<'_, f64>>()?;
        let values = values.as_array();
        if values.len() != levels.len() {
            return Err(error(py, "HRRR Zarr column lengths disagree"));
        }
        data.insert(key, order.iter().map(|&i| values[i]).collect::<Vec<_>>());
    }
    if ["HGT", "TMP", "UGRD", "VGRD"]
        .iter()
        .any(|&key| !data.contains_key(key))
    {
        return Err(error(
            py,
            "HRRR Zarr point is missing a required sounding column",
        ));
    }
    if !data.contains_key("RH") && !data.contains_key("SPFH") {
        return Err(error(
            py,
            "HRRR Zarr point is missing RH and specific humidity",
        ));
    }
    let surface_value = |name: &str| -> PyResult<f64> {
        Ok(surface
            .map(|s| s.get_item(name))
            .transpose()?
            .flatten()
            .filter(|v| !v.is_none())
            .map(|v| {
                py.get_type::<pyo3::types::PyFloat>()
                    .call1((v,))
                    .and_then(|v| v.extract::<f64>())
            })
            .transpose()?
            .unwrap_or(f64::NAN))
    };
    let mut ground = [
        surface_value("PRES")? / 100.,
        surface_value("HGT")?,
        surface_value("TMP")? - 273.15,
        surface_value("DPT")? - 273.15,
        surface_value("UGRD")?,
        surface_value("VGRD")?,
    ];
    let transformed = py.detach(move || {
        let valid = |v: f64| v.is_finite() && v != -9999.;
        let reference = crate::wind_reference::Reference::lambert(38.5, 38.5, -97.5).unwrap();
        if valid(ground[4]) && valid(ground[5]) {
            (ground[4], ground[5]) = reference.rotate(ground[4], ground[5], selected_lon);
        }
        let get = |name: &str, i: usize| {
            data.get(name)
                .map(|v| v[i])
                .filter(|&v| valid(v))
                .unwrap_or(f64::NAN)
        };
        let levels: Vec<_> = order.iter().map(|&i| levels[i]).collect();
        let n = levels.len();
        let mut values: [Vec<f64>; 9] = std::array::from_fn(|_| Vec::with_capacity(n));
        for (i, &p) in levels.iter().enumerate() {
            let t = get("TMP", i) - 273.15;
            let td = if let Some(humidity) = data.get("RH") {
                let rh = humidity[i];
                if valid(t) && valid(rh) {
                    let g = (rh.clamp(1e-3, 100.) / 100.).ln() + 17.625 * t / (243.04 + t);
                    243.04 * g / (17.625 - g)
                } else {
                    f64::NAN
                }
            } else {
                let q = get("SPFH", i);
                if valid(t) && valid(q) {
                    let e = q * p / (0.622 + 0.378 * q);
                    let l = (e.max(1e-6) / 6.112).ln();
                    243.04 * l / (17.625 - l)
                } else {
                    f64::NAN
                }
            };
            let u = get("UGRD", i);
            let v = get("VGRD", i);
            let (u, v) = if valid(u) && valid(v) {
                reference.rotate(u, v, selected_lon)
            } else {
                (f64::NAN, f64::NAN)
            };
            for (field, value) in [
                p,
                get("HGT", i),
                t,
                td,
                (270. - v.atan2(u).to_degrees()).rem_euclid(360.),
                u.hypot(v) * 1.94384449,
                get("VVEL", i),
                u,
                v,
            ]
            .into_iter()
            .enumerate()
            {
                values[field].push(if value.is_finite() { value } else { -9999. });
            }
        }
        let ground = crate::model_surface::GroundLevel::verified(ground, Some(-9999.));
        let (values, removed) = if let Some(ground) = ground {
            let merged = crate::model_surface::merge(values, ground, -9999.);
            (merged.columns, merged.removed)
        } else {
            (values, 0)
        };
        let vorticity = data.get("ABSV").and_then(|absv| {
            levels
                .iter()
                .zip(absv)
                .find(|(p, v)| ground.is_none_or(|g| **p <= g.pressure()) && valid(**v))
                .map(|(_, v)| *v - 2. * 7.2921159e-5 * selected_lat.to_radians().sin())
        });
        (values, removed, ground.is_some(), vorticity)
    });
    let (columns, removed, merged, vorticity) = transformed;
    let count = columns[0].len();
    let matrix = Array2::from_shape_vec((9, count), columns.into_iter().flatten().collect())
        .map_err(|e| error(py, e.to_string()))?;
    let decoded = PyModule::import(py, "sharpmod.backends.grib")?
        .getattr("DecodedPoint")?
        .call1((
            matrix.into_pyarray(py),
            selected_lat,
            selected_lon,
            vorticity,
            merged,
            removed,
        ))?;
    let utc = PyModule::import(py, "datetime")?
        .getattr("timezone")?
        .getattr("utc")?;
    let run = if run.getattr("tzinfo")?.is_none() {
        let kw = PyDict::new(py);
        kw.set_item("tzinfo", utc)?;
        run.call_method("replace", (), Some(&kw))?
    } else {
        run.call_method1("astimezone", (utc,))?
    };
    let fields = PyDict::new(py);
    fields.set_item("decoded", decoded)?;
    fields.set_item("requested_lat", requested_lat)?;
    fields.set_item("requested_lon", requested_lon)?;
    fields.set_item("run_time", &run)?;
    fields.set_item("valid_time", run)?;
    PyModule::import(py, "sharpmod.providers.hrrr_zarr")?
        .getattr("HrrrZarrPointDataset")?
        .call((), Some(&fields))
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(point_dataset, module)?)
}

//! Select NetCDF hyperslabs before materializing arrays, then transform owned columns.
use numpy::{IntoPyArray, PyReadonlyArray1};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PySlice, PyTuple},
};

fn column(
    py: Python<'_>,
    ds: &Bound<'_, PyAny>,
    name: &str,
    time: usize,
    y: usize,
    x: usize,
    stagger: Option<&str>,
) -> PyResult<Option<Vec<f64>>> {
    if !ds.contains(name)? {
        return Ok(None);
    }
    let array = ds.get_item(name)?;
    let dims: Vec<String> = array.getattr("dims")?.extract()?;
    let selected = PyDict::new(py);
    for (index, dim) in dims.iter().enumerate() {
        if dim == "Time" {
            selected.set_item(dim, time)?;
        } else if dim.starts_with("south_north") || (dims.len() >= 3 && index == dims.len() - 2) {
            if stagger == Some("y") {
                selected.set_item(dim, PySlice::new(py, y as isize, y as isize + 2, 1))?;
            } else {
                selected.set_item(dim, y)?;
            }
        } else if dim.starts_with("west_east") || (dims.len() >= 3 && index == dims.len() - 1) {
            if stagger == Some("x") {
                selected.set_item(dim, PySlice::new(py, x as isize, x as isize + 2, 1))?;
            } else {
                selected.set_item(dim, x)?;
            }
        }
    }
    let values = array.call_method1("isel", (selected,))?.getattr("values")?;
    let numpy = PyModule::import(py, "numpy")?;
    let dtype = PyDict::new(py);
    dtype.set_item("dtype", numpy.getattr("float64")?)?;
    let values = numpy.call_method("asarray", (values,), Some(&dtype))?;
    let shape: Vec<usize> = values.getattr("shape")?.extract()?;
    let raw = values
        .call_method0("ravel")?
        .extract::<PyReadonlyArray1<'_, f64>>()?
        .as_array()
        .iter()
        .copied()
        .collect::<Vec<_>>();
    if stagger.is_some() {
        if shape.len() != 2 || shape[1] != 2 {
            return Err(PyValueError::new_err(format!(
                "WRF {name} stagger does not contain adjacent cells"
            )));
        }
        let (pairs, _) = raw.as_chunks::<2>();
        return Ok(Some(
            pairs
                .iter()
                .map(|[left, right]| 0.5 * (left + right))
                .collect(),
        ));
    }
    if shape.len() > 1 {
        return Err(PyValueError::new_err(format!(
            "WRF {name} did not select one vertical column"
        )));
    }
    Ok(Some(raw))
}

#[pyfunction]
fn wrf_point_columns<'py>(
    py: Python<'py>,
    ds: &Bound<'py, PyAny>,
    lat: f64,
    lon: f64,
    valid: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    let api = PyModule::import(py, "sharpmod.tools.wrf_extract")?;
    let times = api.call_method1("_wrf_times", (ds,))?;
    let time: usize = api
        .call_method1("_select_time_index", (&times, valid))?
        .extract()?;
    let grid = |name: &str| -> PyResult<Bound<'py, PyAny>> {
        if !ds.contains(name)? {
            return Err(PyValueError::new_err(
                "WRF file lacks XLAT/XLONG coordinates",
            ));
        }
        let array = ds.get_item(name)?;
        let dims: Vec<String> = array.getattr("dims")?.extract()?;
        let selected = PyDict::new(py);
        if dims.iter().any(|name| name == "Time") {
            selected.set_item("Time", time)?;
        }
        api.call_method1(
            "_grid2d",
            (array.call_method1("isel", (selected,))?.getattr("values")?,),
        )
    };
    let latitude = grid("XLAT")?;
    let longitude = grid("XLONG")?;
    let domain = PyDict::new(py);
    domain.set_item(
        "boundary",
        api.call_method1("_grid_boundary", (&latitude, &longitude))?,
    )?;
    if !api
        .call_method1("point_in_domain", (domain, lat, lon))?
        .is_truthy()?
    {
        return Err(PyErr::from_value(
            api.getattr("ParameterRangeError")?.call1((format!(
                "requested point ({lat:.4}, {lon:.4}) lies outside the WRF grid domain"
            ),))?,
        ));
    }
    let ((y, x), glat, glon): ((usize, usize), f64, f64) = api
        .call_method1("select_nearest_grid_point", (latitude, longitude, lat, lon))?
        .extract()?;
    let required = |name: &str| -> PyResult<Vec<f64>> {
        column(py, ds, name, time, y, x, None)?
            .ok_or_else(|| PyValueError::new_err(format!("WRF file lacks {name}")))
    };
    let p = required("P")?;
    let pb = required("PB")?;
    let ph = required("PH")?;
    let phb = required("PHB")?;
    let t = required("T")?;
    let q = column(py, ds, "QVAPOR", time, y, x, None)?;
    let u = column(py, ds, "U", time, y, x, Some("x"))?;
    let v = column(py, ds, "V", time, y, x, Some("y"))?;
    let ca = column(py, ds, "COSALPHA", time, y, x, None)?.and_then(|v| v.first().copied());
    let sa = column(py, ds, "SINALPHA", time, y, x, None)?.and_then(|v| v.first().copied());
    let n = p.len();
    if n < 2 || pb.len() != n || t.len() != n || ph.len() != n + 1 || phb.len() != n + 1 {
        return Err(PyValueError::new_err(
            "WRF pressure, temperature and geopotential levels disagree",
        ));
    }
    let columns = py.detach(move || {
        let mut rows = Vec::with_capacity(n);
        for i in 0..n {
            let pressure = (p[i] + pb[i]) / 100.;
            let height = 0.5 * ((ph[i] + phb[i]) + (ph[i + 1] + phb[i + 1])) / 9.80665;
            let temperature =
                (t[i] + 300.) * ((pressure * 100.) / 100000.).powf(287. / 1004.) - 273.15;
            let dewpoint = q
                .as_ref()
                .filter(|q| q.len() == n)
                .map(|q| {
                    if !q[i].is_finite() {
                        return f64::NAN;
                    }
                    let w = q[i].max(0.);
                    let e = (w * pressure / (0.622 + w)).max(1e-6);
                    let l = (e / 6.112).ln();
                    243.04 * l / (17.625 - l)
                })
                .unwrap_or(f64::NAN);
            let (direction, speed) = match (&u, &v) {
                (Some(u), Some(v)) if u.len() == n && v.len() == n => {
                    let (u, v) = match (ca, sa) {
                        (Some(c), Some(s)) => (u[i] * c - v[i] * s, u[i] * s + v[i] * c),
                        _ => (u[i], v[i]),
                    };
                    (
                        (270. - v.atan2(u).to_degrees()).rem_euclid(360.),
                        u.hypot(v) * 1.94384449,
                    )
                }
                _ => (f64::NAN, f64::NAN),
            };
            rows.push([pressure, height, temperature, dewpoint, direction, speed]);
        }
        rows.sort_by(|a, b| b[0].total_cmp(&a[0]));
        rows
    });
    let result = PyDict::new(py);
    for (i, name) in ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]
        .iter()
        .enumerate()
    {
        result.set_item(
            name,
            columns
                .iter()
                .map(|row| if row[i].is_finite() { row[i] } else { -9999. })
                .collect::<Vec<_>>()
                .into_pyarray(py),
        )?;
    }
    let selected = PyDict::new(py);
    selected.set_item("glat", glat)?;
    selected.set_item("glon", (glon + 180.).rem_euclid(360.) - 180.)?;
    selected.set_item("valid", times.get_item(time)?)?;
    PyTuple::new(py, [result, selected])
}
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(wrf_point_columns, module)?)
}

#[cfg(test)]
mod tests;

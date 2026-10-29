//! Small compatibility boundary for parcel display calculations.

use crate::profile_input::{column, masked_number};
use numpy::PyReadonlyArray1;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList, PyTuple},
};

#[pyfunction]
fn profile_thermo<'py>(py: Python<'py>, prof: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let columns = ["pres", "hght", "tmpc", "dwpc", "vtmp"]
        .iter()
        .map(|name| column(&prepare, &prof.getattr(*name)?, name))
        .collect::<PyResult<Vec<_>>>()?;
    let sfc: usize = prof.getattr("sfc")?.extract()?;
    let sentinel = prof.getattr("missing")?.extract()?;
    let (mean_ratio, convective, surface_pressure) = py
        .detach(move || {
            let profile = crate::parcels::convective_temperature::TemperatureSearch::new(
                [0, 1, 2, 3, 4].map(|i| columns[i].as_slice()),
                sfc,
                Some(sentinel),
            )?;
            let ratio = profile.mean_mixing_ratio();
            Ok::<_, String>((
                ratio,
                profile.convective_temperature(ratio),
                columns[0][sfc],
            ))
        })
        .map_err(PyValueError::new_err)?;
    let result = PyDict::new(py);
    let missing = PyModule::import(py, "numpy.ma")?.getattr("masked")?;
    for (name, value) in [
        ("mean_mixr", mean_ratio),
        (
            "convT",
            convective.map(|t| t * 1.8 + 32.).unwrap_or(f64::NAN),
        ),
    ] {
        match value.is_finite() {
            true => result.set_item(name, value)?,
            false => result.set_item(name, &missing)?,
        }
    }
    let params = PyModule::import(py, "sharppy.sharptab.params")?;
    for (name, function) in [
        ("k_idx", "k_index"),
        ("pwat", "precip_water"),
        ("max_lapse_rate_2_6", "max_lapse_rate"),
        ("low_rh", "mean_relh"),
        ("totals_totals", "t_totals"),
    ] {
        result.set_item(name, params.call_method1(function, (prof,))?)?;
    }
    for (name, lower, upper, pressure) in [
        ("lapserate_3km", 0., 3000., false),
        ("lapserate_3_6km", 3000., 6000., false),
        ("lapserate_850_500", 850., 500., true),
        ("lapserate_700_500", 700., 500., true),
    ] {
        result.set_item(
            name,
            params.call_method1("lapse_rate", (prof, lower, upper, pressure))?,
        )?;
    }
    let thermo = PyModule::import(py, "sharppy.sharptab.thermo")?;
    result.set_item(
        "maxT",
        thermo.call_method1("ctof", (params.call_method1("max_temp", (prof,))?,))?,
    )?;
    result.set_item(
        "mid_rh",
        params.call_method1(
            "mean_relh",
            (prof, surface_pressure - 150., surface_pressure - 350.),
        )?,
    )?;
    let keywords = PyDict::new(py);
    keywords.set_item("lat", prof.getattr("latitude")?)?;
    result.set_item(
        "inf_temp_adv",
        params.call_method("inferred_temp_adv", (prof,), Some(&keywords))?,
    )?;
    Ok(result)
}

#[pyfunction(signature=(prof, parcel, smu=None, smv=None))]
fn parcel_trajectory<'py>(
    py: Python<'py>,
    prof: &Bound<'py, PyAny>,
    parcel: &Bound<'py, PyAny>,
    smu: Option<&Bound<'py, PyAny>>,
    smv: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyTuple>> {
    use crate::parcels::trajectory::{parcel_trajectory, TrajectoryState};

    let masked = PyModule::import(py, "numpy.ma")?;
    let missing = masked.getattr("masked")?;
    let storm = match (smu, smv) {
        (Some(u), Some(v)) => [masked_number(u, &masked)?, masked_number(v, &masked)?],
        _ => {
            let storm = prof.getattr("srwind")?;
            [
                masked_number(&storm.get_item(0)?, &masked)?,
                masked_number(&storm.get_item(1)?, &masked)?,
            ]
        }
    };
    let cape = masked_number(&parcel.getattr("bplus")?, &masked)?.unwrap_or(f64::NAN);
    if cape < 1.0e-3 {
        return (missing, f64::NAN).into_pyobject(py);
    }
    let state = TrajectoryState {
        cape,
        lfc_pressure: masked_number(&parcel.getattr("lfcpres")?, &masked)?.unwrap_or(f64::NAN),
        lfc_height: masked_number(&parcel.getattr("lfchght")?, &masked)?,
        el_height: masked_number(&parcel.getattr("elhght")?, &masked)?,
        storm_motion: storm,
    };
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let columns = ["pres", "hght", "vtmp", "u", "v"]
        .iter()
        .map(|name| column(&prepare, &prof.getattr(*name)?, name))
        .collect::<PyResult<Vec<_>>>()?;
    let el = state
        .el_height
        .unwrap_or(*columns[1].last().unwrap_or(&f64::NAN));
    let trace = if state.lfc_height.is_some_and(|z| z < el) {
        [
            column(&prepare, &parcel.getattr("ptrace")?, "ptrace")?,
            column(&prepare, &parcel.getattr("ttrace")?, "ttrace")?,
        ]
    } else {
        [Vec::new(), Vec::new()]
    };
    let sfc = prof.getattr("sfc")?.extract()?;
    let sentinel = prof.getattr("missing")?.extract()?;
    let result = py
        .detach(move || {
            parcel_trajectory(
                [0, 1, 2, 3, 4].map(|i| columns[i].as_slice()),
                trace.each_ref().map(Vec::as_slice),
                state,
                sfc,
                Some(sentinel),
            )
        })
        .map_err(PyValueError::new_err)?;
    let Some(result) = result else {
        return (missing, f64::NAN).into_pyobject(py);
    };
    let positions = PyList::empty(py);
    for (i, point) in result.positions.into_iter().enumerate() {
        let values: Vec<_> = point
            .into_iter()
            .enumerate()
            .map(|(axis, value)| match value {
                Some(_) if i == 0 && axis < 2 => 0_i32.into_pyobject(py).unwrap().into_any(),
                Some(value) => value.into_pyobject(py).unwrap().into_any(),
                None => missing.clone(),
            })
            .collect();
        positions.append(PyTuple::new(py, values)?)?;
    }
    let tilt = match result.tilt {
        Some(value) => value.into_pyobject(py)?.into_any(),
        None => missing,
    };
    (positions, tilt).into_pyobject(py)
}

#[pyfunction(signature=(pressure,height,temperature,environment,start,el,sfc=0,missing=Some(-9999.)))]
#[allow(clippy::too_many_arguments)] // Four reported columns and one parcel's state.
fn parcel_maximum_level(
    py: Python<'_>,
    pressure: PyReadonlyArray1<'_, f64>,
    height: PyReadonlyArray1<'_, f64>,
    temperature: PyReadonlyArray1<'_, f64>,
    environment: PyReadonlyArray1<'_, f64>,
    start: (f64, f64, f64),
    el: f64,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<(f64, f64)> {
    let columns = [
        pressure.as_slice()?.to_vec(),
        height.as_slice()?.to_vec(),
        temperature.as_slice()?.to_vec(),
        environment.as_slice()?.to_vec(),
    ];
    py.detach(move || {
        crate::parcels::maximum_parcel_level(
            columns.each_ref().map(Vec::as_slice),
            start,
            el,
            sfc,
            missing,
        )
    })
    .map_err(PyValueError::new_err)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(profile_thermo, module)?)?;
    module.add_function(wrap_pyfunction!(parcel_maximum_level, module)?)?;
    module.add_function(wrap_pyfunction!(parcel_trajectory, module)?)?;
    Ok(())
}

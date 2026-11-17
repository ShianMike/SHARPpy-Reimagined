use crate::{
    parcels::display::Display,
    profile_input::{column, masked_number},
};
use numpy::{ndarray::Array2, IntoPyArray};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyTuple},
};
fn workspace(py: Python<'_>, prof: &Bound<'_, PyAny>) -> PyResult<(Display, Vec<Vec<f64>>, bool)> {
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let columns = ["pres", "hght", "tmpc", "dwpc", "vtmp", "u", "v"]
        .into_iter()
        .map(|n| column(&prepare, &prof.getattr(n)?, n))
        .collect::<PyResult<Vec<_>>>()?;
    let display = Display::new(
        &columns,
        prof.getattr("sfc")?.extract()?,
        Some(prof.getattr("missing")?.extract()?),
    )
    .map_err(PyValueError::new_err)?;
    let winds = prof
        .getattr("wdir")?
        .call_method0("count")?
        .extract::<usize>()?
        > 0;
    Ok((display, columns, winds))
}
pub(crate) fn thermo<'py>(
    py: Python<'py>,
    prof: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let (display, columns, winds) = workspace(py, prof)?;
    let latitude = PyModule::import(py, "sharpmod.bridge.params")?
        .call_method1("num", (prof.getattr("latitude")?,))?
        .extract::<Option<f64>>()?;
    let (values, maximum, advection) = py.detach(move || {
        let sfc = display.pressure[display.surface];
        let t8 = display.at(1, 850.);
        let t7 = display.at(1, 700.);
        let t5 = display.at(1, 500.);
        let d7 = display.at(2, 700.);
        let d8 = display.at(2, 850.);
        let values = [
            ("k_idx", t8 - t5 + d8 - (t7 - d7)),
            ("totals_totals", d8 + t8 - 2. * t5),
            ("pwat", display.water()),
            ("low_rh", display.humidity(sfc, sfc - 100.)),
            ("mid_rh", display.humidity(sfc - 150., sfc - 350.)),
            ("maxT", display.max_temp()),
            ("lapserate_3km", display.lapse(0., 3000., false)),
            ("lapserate_3_6km", display.lapse(3000., 6000., false)),
            ("lapserate_850_500", display.lapse(850., 500., true)),
            ("lapserate_700_500", display.lapse(700., 500., true)),
        ];
        let _ = columns;
        (
            values,
            display.maximum_lapse(),
            display.advection(latitude, winds),
        )
    });
    let result = PyDict::new(py);
    let missing = PyModule::import(py, "numpy.ma")?.getattr("masked")?;
    for (name, value) in values {
        if value.is_finite() {
            result.set_item(name, value)?;
        } else {
            result.set_item(name, &missing)?;
        }
    }
    let mut parts = Vec::new();
    for value in maximum {
        parts.push(if value.is_finite() {
            value.into_pyobject(py)?.into_any()
        } else {
            missing.clone()
        });
    }
    result.set_item("max_lapse_rate_2_6", PyTuple::new(py, parts)?)?;
    if let Some((values, bounds)) = advection {
        let n = bounds.len();
        let bounds = Array2::from_shape_vec((n, 2), bounds.into_iter().flatten().collect())
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        result.set_item(
            "inf_temp_adv",
            (values.into_pyarray(py), bounds.into_pyarray(py)),
        )?;
    } else {
        result.set_item("inf_temp_adv", (&missing, &missing))?;
    }
    Ok(result)
}
#[pyfunction]
fn mmp_linear_predictor(py: Python<'_>, prof: &Bound<'_, PyAny>) -> PyResult<Option<f64>> {
    let (display, _, winds) = workspace(py, prof)?;
    let masked = PyModule::import(py, "numpy.ma")?;
    let cape =
        masked_number(&prof.getattr("mupcl")?.getattr("bplus")?, &masked)?.unwrap_or(f64::NAN);
    // Private integrations can replace the public SharpTab scalar adapters.
    // Preserve those explicit overrides; the normal path evaluates both in Rust.
    let mut inputs = [None; 2];
    let params = PyModule::import(py, "sharppy.sharptab.params")?;
    let layers = PyModule::import(py, "sharppy.sharptab.winds")?;
    let lapse = params.getattr("lapse_rate")?;
    if lapse
        .getattr("__module__")?
        .extract::<Option<String>>()?
        .as_deref()
        != Some("sharppy.sharptab.params")
    {
        inputs[0] = Some(
            masked_number(&lapse.call1((prof, 3000., 8000., false))?, &masked)?.unwrap_or(f64::NAN),
        );
    }
    let mean = layers.getattr("mean_wind")?;
    if mean
        .getattr("__module__")?
        .extract::<Option<String>>()?
        .as_deref()
        != Some("sharppy.sharptab.winds")
    {
        let kwargs = PyDict::new(py);
        kwargs.set_item("pbot", display.pressure_at(3000.))?;
        kwargs.set_item("ptop", display.pressure_at(12000.))?;
        let vector = mean.call((prof,), Some(&kwargs))?;
        let u = masked_number(&vector.get_item(0)?, &masked)?.unwrap_or(f64::NAN);
        let v = masked_number(&vector.get_item(1)?, &masked)?.unwrap_or(f64::NAN);
        inputs[1] = Some(u.hypot(v));
    }
    Ok(py.detach(move || display.mmp(cape, winds, inputs)))
}
#[pyfunction]
fn populate_severe_indices(py: Python<'_>, prof: &Bound<'_, PyAny>) -> PyResult<()> {
    let (display, _, winds) = workspace(py, prof)?;
    let masked = PyModule::import(py, "numpy.ma")?;
    let missing = masked.getattr("masked")?;
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let thetae = column(&prepare, &prof.getattr("thetae")?, "thetae")?;
    let ml = prof.getattr("mlpcl")?;
    let number = |object: Bound<'_, PyAny>| -> PyResult<f64> {
        Ok(masked_number(&object, &masked)?.unwrap_or(f64::NAN))
    };
    let cape = number(ml.getattr("bplus")?)?;
    let cin = number(ml.getattr("bminus")?)?;
    let cape3 = number(ml.getattr("b3km")?)?;
    let mu = number(prof.getattr("mupcl")?.getattr("bplus")?)?;
    let shear = prof.getattr("sfc_6km_shear")?;
    let shear = [number(shear.get_item(0)?)?, number(shear.get_item(1)?)?];
    let values = py.detach(move || {
        let sfc = display.pressure[display.surface];
        let mut low = f64::INFINITY;
        let mut high = f64::NEG_INFINITY;
        for (&p, &t) in display.pressure.iter().zip(&thetae) {
            if p >= sfc - 400. && t.is_finite() {
                low = low.min(t);
                high = high.max(t);
            }
        }
        let lapse = display.lapse(0., 3000., false);
        let esp = if lapse < 7. || cape < 250. {
            0.
        } else {
            cape3 / 50. * (lapse - 7.)
        };
        let [u, v] = display.mean_wind(display.pressure_at(1000.), display.pressure_at(3500.));
        let lr = if lapse < 7. { 0. } else { lapse };
        let cin = if cin < -50. { -50. } else { cin };
        let wndg = cape / 2000. * (lr / 9.) * (u.hypot(v) * 0.514444 / 15.) * ((50. + cin) / 40.);
        let mmp = if mu < 100. {
            0.
        } else {
            display
                .mmp(mu, winds, [None; 2])
                .map(|x| {
                    if x >= 0. {
                        let e = (-x).exp();
                        e / (1. + e)
                    } else {
                        1. / (1. + x.exp())
                    }
                })
                .unwrap_or(f64::NAN)
        };
        [
            ("tei", high - low),
            ("esp", esp),
            ("wndg", wndg),
            ("sig_severe", cape * shear[0].hypot(shear[1]) * 0.514444),
            ("mmp", mmp),
        ]
    });
    for (key, value) in values {
        if value.is_finite() {
            prof.setattr(key, value)?;
        } else {
            prof.setattr(key, &missing)?;
        }
    }
    Ok(())
}
pub(crate) fn ship<'py>(py: Python<'py>, prof: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let (display, columns, _) = workspace(py, prof)?;
    let masked = PyModule::import(py, "numpy.ma")?;
    let number = |value: Bound<'_, PyAny>| -> PyResult<f64> {
        Ok(masked_number(&value, &masked)?.unwrap_or(f64::NAN))
    };
    let mu = prof.getattr("mupcl")?;
    let cape = number(mu.getattr("bplus")?)?;
    let p = number(mu.getattr("pres")?)?;
    let td = number(mu.getattr("dwpc")?)?;
    let shear = prof.getattr("sfc_6km_shear")?;
    let speed = number(shear.get_item(0)?)?.hypot(number(shear.get_item(1)?)?) * 0.514444;
    let value = py.detach(move || {
        crate::parcels::display::ship([
            cape,
            crate::parcels::mix_ratio(p, td),
            display.lapse(700., 500., true),
            display.at(1, 500.),
            speed,
            display.freezing_height(&columns[2]),
        ])
    });
    if value.is_finite() {
        Ok(value.into_pyobject(py)?.into_any())
    } else {
        masked.getattr("masked")
    }
}
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(populate_severe_indices, module)?)?;
    module.add_function(wrap_pyfunction!(mmp_linear_predictor, module)?)
}
#[cfg(test)]
mod tests;

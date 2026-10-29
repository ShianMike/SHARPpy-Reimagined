//! Display wind work is shared while the reference SRH and motion formulas stay intact.

use crate::{
    profile_input::{column, masked_number},
    wind_layers::{Vector, WindProfile},
};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList, PyTuple},
};

fn vector<'py>(
    py: Python<'py>,
    values: Vector,
    missing: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(
        py,
        values.into_iter().map(|value| match value {
            Some(value) => value.into_pyobject(py).unwrap().into_any(),
            None => missing.clone(),
        }),
    )
}

#[pyfunction]
fn profile_wind_layers<'py>(
    py: Python<'py>,
    prof: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let masked = PyModule::import(py, "numpy.ma")?;
    let missing = masked.getattr("masked")?;
    let interp = PyModule::import(py, "sharppy.sharptab.interp")?;
    let winds = PyModule::import(py, "sharppy.sharptab.winds")?;
    let params = PyModule::import(py, "sharppy.sharptab.params")?;
    let utils = PyModule::import(py, "sharppy.sharptab.utils")?;
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let columns = ["hght", "logp", "u", "v"]
        .iter()
        .map(|name| column(&prepare, &prof.getattr(*name)?, name))
        .collect::<PyResult<Vec<_>>>()?;
    let has_wind = prof
        .getattr("wdir")?
        .call_method0("count")?
        .extract::<usize>()?
        > 0;
    let sfc = prof.getattr("sfc")?;
    let surface = masked_number(&prof.getattr("pres")?.get_item(&sfc)?, &masked)?;
    let terrain = masked_number(&prof.getattr("hght")?.get_item(&sfc)?, &masked)?;
    let bottom = prof.getattr("ebottom")?;
    let top = prof.getattr("etop")?;
    let effective = !bottom.is(&missing) && !top.is(&missing);
    let parcel = prof.getattr("mupcl")?;
    let lcl = masked_number(&parcel.getattr("lclpres")?, &masked)?;
    let el = masked_number(&parcel.getattr("elpres")?, &masked)?;
    let result = PyDict::new(py);
    let mut effective_bounds = None;
    let motion = if effective {
        let keywords = PyDict::new(py);
        keywords.set_item("mupcl", &parcel)?;
        keywords.set_item("pbot", &bottom)?;
        let base = prof.getattr("ebotm")?;
        let height = parcel.getattr("elhght")?.sub(&base)?.div(2.)?.add(&base)?;
        let upper = interp.call_method1(
            "pres",
            (prof, interp.call_method1("to_msl", (prof, height))?),
        )?;
        effective_bounds = Some((
            masked_number(&bottom, &masked)?,
            masked_number(&top, &masked)?,
            masked_number(&upper, &masked)?,
        ));
        params.call_method("bunkers_storm_motion", (prof,), Some(&keywords))?
    } else {
        result.set_item("etopm", &missing)?;
        result.set_item("ebotm", &missing)?;
        winds.call_method1("non_parcel_bunkers_motion", (prof,))?
    };
    result.set_item("bunkers", &motion)?;
    let user = prof.getattr("user_srwind")?;
    let storm = if user.is_none() { motion } else { user };
    result.set_item("user_srwind", &storm)?;
    let storm_values: Vec<_> = (0..4)
        .map(|index| masked_number(&storm.get_item(index)?, &masked))
        .collect::<PyResult<_>>()?;
    result.set_item(
        "srwind",
        PyTuple::new(
            py,
            storm_values
                .iter()
                .map(|value| match value.filter(|v| v.is_finite()) {
                    Some(value) => value.into_pyobject(py).unwrap().into_any(),
                    None => missing.clone(),
                }),
        )?,
    )?;
    let (components, layers) = py
        .detach(move || {
            let profile = WindProfile::new([0, 1, 2, 3].map(|i| columns[i].as_slice()), has_wind)?;
            let pressures = [
                1000., 3000., 4000., 5000., 6000., 8000., 9000., 2000., 11000.,
            ]
            .map(|height| profile.pressure(terrain.map(|sfc| sfc + height)));
            let components = [
                profile.components(pressures[0]),
                profile.components(pressures[4]),
            ];
            let mut bounds = vec![
                (surface, pressures[0]),
                (surface, pressures[1]),
                (surface, pressures[4]),
                (surface, pressures[5]),
                (surface, pressures[6]),
                (lcl, el),
                (pressures[2], pressures[3]),
                (surface, pressures[7]),
                (pressures[2], pressures[4]),
                (pressures[6], pressures[8]),
            ];
            if let Some((bottom, top, upper)) = effective_bounds {
                bounds.extend([(bottom, top), (bottom, upper)]);
            }
            let layers = bounds
                .into_iter()
                .map(|(bottom, top)| profile.layer(bottom, top))
                .collect::<Result<Vec<_>, _>>()?;
            Ok::<_, String>((components, layers))
        })
        .map_err(PyValueError::new_err)?;
    for (name, values) in ["wind1km", "wind6km"].into_iter().zip(components) {
        let values = vector(py, values, &missing)?;
        result.set_item(name, utils.call_method1("comp2vec", values)?)?;
    }
    for (index, name) in [
        "sfc_1km_shear",
        "sfc_3km_shear",
        "sfc_6km_shear",
        "sfc_8km_shear",
        "sfc_9km_shear",
        "lcl_el_shear",
    ]
    .into_iter()
    .enumerate()
    {
        result.set_item(name, vector(py, layers[index].shear, &missing)?)?;
    }
    for (index, name) in [
        (0, "mean_1km"),
        (1, "mean_3km"),
        (2, "mean_6km"),
        (3, "mean_8km"),
        (5, "mean_lcl_el"),
    ] {
        result.set_item(
            name,
            utils.call_method1("comp2vec", vector(py, layers[index].mean, &missing)?)?,
        )?;
    }
    for (side, offset) in [("right", 0), ("left", 2)] {
        let motion = [storm_values[offset], storm_values[offset + 1]];
        for (index, suffix, polar) in [
            (0, "1km", true),
            (1, "3km", true),
            (2, "6km", true),
            (3, "8km", true),
            (6, "4_5km", true),
            (5, "lcl_el", true),
            (7, "0_2km", false),
            (8, "4_6km", false),
            (9, "9_11km", false),
        ] {
            let values = [0, 1].map(|i| {
                layers[index].mean[i]
                    .zip(motion[i])
                    .map(|(mean, motion)| mean - motion)
            });
            let values = vector(py, values, &missing)?;
            result.set_item(
                format!("{side}_srw_{suffix}"),
                if polar {
                    utils.call_method1("comp2vec", values)?.into_any()
                } else {
                    values.into_any()
                },
            )?;
        }
        let keywords = PyDict::new(py);
        keywords.set_item("stu", storm.get_item(offset)?)?;
        keywords.set_item("stv", storm.get_item(offset + 1)?)?;
        for (index, suffix) in [(10, "eff"), (11, "ebw")] {
            if effective {
                result.set_item(
                    format!("{side}_srw_{suffix}"),
                    vector(
                        py,
                        [0, 1].map(|i| {
                            layers[index].mean[i]
                                .zip(motion[i])
                                .map(|(mean, motion)| mean - motion)
                        }),
                        &missing,
                    )?,
                )?;
            } else {
                result.set_item(
                    format!("{side}_srw_{suffix}"),
                    PyList::new(py, [-9999.; 3])?,
                )?;
            }
        }
        if effective {
            result.set_item(
                format!("{side}_esrh"),
                winds.call_method(
                    "helicity",
                    (prof, prof.getattr("ebotm")?, prof.getattr("etopm")?),
                    Some(&keywords),
                )?,
            )?;
            result.set_item(
                format!("{side}_critical_angle"),
                winds.call_method("critical_angle", (prof,), Some(&keywords))?,
            )?;
        } else {
            result.set_item(
                format!("{side}_esrh"),
                PyList::new(py, [&missing, &missing, &missing])?,
            )?;
            result.set_item(format!("{side}_critical_angle"), &missing)?;
        }
        for (height, name) in [(1000., "srh1km"), (3000., "srh3km")] {
            result.set_item(
                format!("{side}_{name}"),
                winds.call_method("helicity", (prof, 0, height), Some(&keywords))?,
            )?;
        }
    }
    if effective {
        for (index, suffix) in [(10, "eff"), (11, "ebw")] {
            result.set_item(
                format!("mean_{suffix}"),
                vector(py, layers[index].mean, &missing)?,
            )?;
            result.set_item(
                if index == 10 { "eff_shear" } else { "ebwd" },
                vector(py, layers[index].shear, &missing)?,
            )?;
        }
        result.set_item(
            "ebwspd",
            utils.call_method1("mag", vector(py, layers[11].shear, &missing)?)?,
        )?;
    } else {
        result.set_item("eff_shear", PyList::new(py, [-9999.; 2])?)?;
        for name in ["ebwd", "mean_eff", "mean_ebw"] {
            result.set_item(name, PyList::new(py, [-9999.; 3])?)?;
        }
        result.set_item("ebwspd", -9999.)?;
    }
    for name in [
        "srw_eff",
        "srw_ebw",
        "esrh",
        "critical_angle",
        "srw_1km",
        "srw_3km",
        "srw_6km",
        "srw_8km",
        "srw_4_5km",
        "srw_lcl_el",
        "srw_0_2km",
        "srw_4_6km",
        "srw_9_11km",
        "srh1km",
        "srh3km",
    ] {
        result.set_item(name, result.get_item(format!("right_{name}"))?.unwrap())?;
    }
    result.set_item(
        "upshear_downshear",
        winds.call_method1("mbe_vectors", (prof,))?,
    )?;
    Ok(result)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(profile_wind_layers, module)?)?;
    Ok(())
}

//! Preserve SHARPpy's analogue tuples and profile fields around native matching.

use crate::sars;
use numpy::IntoPyArray;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyTuple},
};
use std::path::PathBuf;

#[pyfunction]
fn match_sars_hail<'py>(
    py: Python<'py>,
    database: PathBuf,
    input: [f64; 8],
) -> PyResult<Bound<'py, PyTuple>> {
    let (names, sizes, matched) = py
        .detach(move || {
            let records = sars::catalogue::hail(&database)?;
            let matched = sars::hail(&records, input);
            let names: Vec<_> = matched
                .quality
                .iter()
                .map(|&i| records[i].name.clone())
                .collect();
            let sizes: Vec<_> = matched
                .quality
                .iter()
                .map(|&i| records[i].outcome)
                .collect();
            Ok::<_, String>((names, sizes, matched))
        })
        .map_err(PyValueError::new_err)?;
    let names = PyModule::import(py, "numpy")?.call_method1("array", (names,))?;
    let probability = if matched.loose > 0 && input[1] > 0. {
        matched.probability.into_pyobject(py)?.into_any()
    } else {
        0_i32.into_pyobject(py)?.into_any()
    };
    (
        names,
        sizes.into_pyarray(py),
        matched.loose as f64,
        matched.significant as f64,
        probability,
    )
        .into_pyobject(py)
}

#[pyfunction]
fn match_sars_supercell<'py>(
    py: Python<'py>,
    database: PathBuf,
    input: [f64; 9],
) -> PyResult<Bound<'py, PyTuple>> {
    let (names, labels, matched) = py
        .detach(move || {
            let records = sars::catalogue::supercell(&database)?;
            let matched = sars::supercell(&records, input);
            let names: Vec<_> = matched
                .quality
                .iter()
                .map(|&i| records[i].name.clone())
                .collect();
            let labels: Vec<String> = matched
                .quality
                .iter()
                .map(|&i| {
                    let category: String = records[i].classification.chars().take(7).collect();
                    match category.as_str() {
                        "2" => "SIGTOR".into(),
                        "1" => "WEAKTOR".into(),
                        "0" => "NONTOR".into(),
                        _ => category,
                    }
                })
                .collect();
            Ok::<_, String>((names, labels, matched))
        })
        .map_err(PyValueError::new_err)?;
    let numpy = PyModule::import(py, "numpy")?;
    (
        numpy.call_method1("array", (names,))?,
        numpy.call_method1("array", (labels,))?,
        matched.loose,
        matched.significant,
        matched.probability,
    )
        .into_pyobject(py)
}

fn first(profile: &Bound<'_, PyAny>, name: &str) -> PyResult<f64> {
    profile.getattr(name)?.get_item(0)?.extract()
}

fn empty(py: Python<'_>) -> PyResult<Bound<'_, PyTuple>> {
    (Vec::<f64>::new(), Vec::<f64>::new(), 0, 0, 0).into_pyobject(py)
}

#[pyfunction]
fn profile_sars<'py>(py: Python<'py>, profile: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    let utils = PyModule::import(py, "sharppy.sharptab.utils")?;
    let params = PyModule::import(py, "sharppy.sharptab.params")?;
    let interp = PyModule::import(py, "sharppy.sharptab.interp")?;
    let thermo = PyModule::import(py, "sharppy.sharptab.thermo")?;
    let mut shear_ms = [0.; 3];
    let mut shear_knots = [0.; 3];
    for (i, name) in ["sfc_6km_shear", "sfc_9km_shear", "sfc_3km_shear"]
        .iter()
        .enumerate()
    {
        let vector = profile.getattr(*name)?;
        let magnitude = utils.call_method1("mag", (vector.get_item(0)?, vector.get_item(1)?))?;
        let speed = utils.call_method1("KTS2MS", (magnitude,))?;
        shear_ms[i] = speed.extract()?;
        shear_knots[i] = utils.call_method1("MS2KTS", (speed,))?.extract()?;
    }
    let temperature: f64 = interp.call_method1("temp", (profile, 500.))?.extract()?;
    let lapse: f64 = params
        .call_method1("lapse_rate", (profile, 700., 500., true))?
        .extract()?;
    let mu = profile.getattr("mupcl")?;
    let ml = profile.getattr("mlpcl")?;
    let mu_cape = mu.getattr("bplus")?.extract()?;
    let ml_cape = ml.getattr("bplus")?.extract()?;
    let lcl = ml.getattr("lclhght")?.extract()?;
    let ratio = thermo
        .call_method1("mixratio", (mu.getattr("pres")?, mu.getattr("dwpc")?))?
        .extract()?;
    let hail = [
        ratio,
        mu_cape,
        temperature,
        lapse,
        shear_ms[0],
        shear_ms[1],
        shear_ms[2],
        first(profile, "right_srh3km")?,
    ];
    let supercell = [
        ml_cape,
        lcl,
        temperature,
        lapse,
        shear_knots[0],
        first(profile, "right_srh1km")?,
        shear_knots[2],
        shear_knots[1],
        hail[7],
    ];
    let mut hail_left = hail;
    hail_left[7] = -first(profile, "left_srh3km")?;
    let mut supercell_left = supercell;
    supercell_left[5] = -first(profile, "left_srh1km")?;
    supercell_left[8] = hail_left[7];

    let result = PyDict::new(py);
    result.set_item("ship", params.call_method1("ship", (profile,))?)?;
    result.set_item("hail_database", "sars_hail.txt")?;
    result.set_item("supercell_database", "sars_supercell.txt")?;
    let directory = PyModule::import(py, "sharppy.databases.sars")?
        .getattr("__file__")
        .and_then(|file| file.extract::<PathBuf>())
        .ok()
        .and_then(|file| file.parent().map(|parent| parent.to_path_buf()));
    if let Some(directory) = directory {
        for (name, input) in [("right_matches", hail), ("left_matches", hail_left)] {
            result.set_item(
                name,
                match_sars_hail(py, directory.join("sars_hail.txt"), input)
                    .or_else(|_| empty(py))?,
            )?;
        }
        for (name, input) in [
            ("right_supercell_matches", supercell),
            ("left_supercell_matches", supercell_left),
        ] {
            result.set_item(
                name,
                match_sars_supercell(py, directory.join("sars_supercell.txt"), input)
                    .or_else(|_| empty(py))?,
            )?;
        }
    } else {
        for name in [
            "right_matches",
            "left_matches",
            "right_supercell_matches",
            "left_supercell_matches",
        ] {
            result.set_item(name, empty(py)?)?;
        }
    }
    let side = if profile.getattr("latitude")?.extract::<f64>()? < 0. {
        "left"
    } else {
        "right"
    };
    result.set_item(
        "matches",
        result.get_item(format!("{side}_matches"))?.unwrap(),
    )?;
    result.set_item(
        "supercell_matches",
        result
            .get_item(format!("{side}_supercell_matches"))?
            .unwrap(),
    )?;
    Ok(result)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(match_sars_hail, module)?)?;
    module.add_function(wrap_pyfunction!(match_sars_supercell, module)?)?;
    module.add_function(wrap_pyfunction!(profile_sars, module)?)?;
    Ok(())
}

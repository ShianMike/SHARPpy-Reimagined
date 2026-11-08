//! Optional integration with the installed SHARPpy pipeline, not a Python test path.
use super::*;
use pyo3::types::PyList;

#[test]
#[ignore = "requires SHARPMOD_BOX_DIAGNOSTIC_FIXTURE and the existing Python environment"]
fn unavailable_effective_winds_remain_masked_in_the_existing_diagnostic_pipeline() {
    let fixture = std::env::var("SHARPMOD_BOX_DIAGNOSTIC_FIXTURE").unwrap();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let path = PyModule::import(py, "pathlib")?
            .getattr("Path")?
            .call1((fixture,))?;
        let decoded = PyModule::import(py, "sharpmod.bridge.sounding_cache")?
            .getattr("decode_collection")?
            .call1((path,))?;
        let prof = decoded.get_item(0)?.call_method0("getHighlightedProf")?;
        let ma = PyModule::import(py, "numpy.ma")?;
        assert!(
            ma.getattr("is_masked")?
                .call1((prof.getattr("ebottom")?,))?
                .is_truthy()?,
            "fixture must have no effective inflow layer"
        );
        let result = crate::wind_layer_bindings::profile_wind_layers(py, &prof)?;
        for key in [
            "eff_shear",
            "ebwd",
            "mean_eff",
            "mean_ebw",
            "right_srw_eff",
            "left_srw_eff",
            "right_srw_ebw",
            "left_srw_ebw",
        ] {
            let values = result.get_item(key)?.unwrap();
            for index in 0..values.len()? {
                assert!(
                    ma.getattr("is_masked")?
                        .call1((values.get_item(index)?,))?
                        .is_truthy()?,
                    "{key} must stay missing rather than become a wind vector"
                );
            }
        }
        assert!(ma
            .getattr("is_masked")?
            .call1((result.get_item("ebwspd")?.unwrap(),))?
            .is_truthy()?);
        Ok(())
    })
    .unwrap();
}

#[test]
#[ignore = "requires the existing Python environment"]
fn malformed_box_inputs_use_existing_request_errors() {
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let kind =
            PyModule::import(py, "sharpmod.tools.era5_extract")?.getattr("ParameterRangeError")?;
        assert!(bounds(py.None().bind(py))
            .unwrap_err()
            .is_instance(py, &kind));
        let value = PyDict::new(py);
        for (key, number) in [
            ("west", -98.0),
            ("east", -97.0),
            ("south", 35.0),
            ("north", 36.0),
        ] {
            value.set_item(key, number)?;
        }
        assert!(bounds(value.as_any()).is_ok());
        value.set_item("west", true)?;
        assert!(bounds(value.as_any()).unwrap_err().is_instance(py, &kind));
        value.set_item("west", -98.0)?;
        value.set_item("unknown", 1)?;
        assert!(bounds(value.as_any()).unwrap_err().is_instance(py, &kind));
        Ok(())
    })
    .unwrap();
}

#[test]
#[ignore = "requires a verified one-column GFS fixture and SHARPMOD_BOX_DIAGNOSTIC_OUTPUT"]
fn a_single_native_column_preserves_existing_sharppy_diagnostics() {
    let fixture = std::env::var("SHARPMOD_GRIB_BENCH_FIXTURE").unwrap();
    let library = std::env::var("SHARPMOD_ECCODES_LIBRARY").unwrap();
    let report = std::path::PathBuf::from(std::env::var("SHARPMOD_BOX_DIAGNOSTIC_OUTPUT").unwrap());
    let bounds = Bounds::new(-97.46, 35.16, -97.42, 35.20).unwrap();
    let area = area::decode(
        Path::new(&fixture),
        Path::new(&library),
        bounds,
        Limits::default(),
        &|| false,
    )
    .unwrap();
    assert_eq!(
        area.averaged.valid_columns, 1,
        "use a box contained in one native GFS cell"
    );
    let reference = crate::grib::decode_grib_point(
        Path::new(&fixture),
        Path::new(&library),
        35.18,
        -97.44,
        Some(-9999.0),
    )
    .unwrap();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let mx = PyModule::import(py, "sharpmod.tools.model_extract")?;
        let datetime = PyModule::import(py, "datetime")?.getattr("datetime")?;
        let run = datetime.call_method1("fromisoformat", ("2026-11-07T06:00:00+00:00",))?;
        let valid = datetime.call_method1("fromisoformat", ("2026-11-07T09:00:00+00:00",))?;
        let mut profiles = Vec::new();
        let mut metadata = serde_json::Map::new();
        for (label, point) in [("point", reference), ("box", area.averaged.profile)] {
            let path = report.with_file_name(format!("single-column-{label}.npz"));
            let kwargs = PyDict::new(py);
            let decoded = PyModule::import(py, "sharpmod.backends.grib")?
                .getattr("DecodedPoint")?
                .call1(crate::decoded_point_into_py(py, point)?)?;
            kwargs.set_item(
                "dataset",
                mx.getattr("DecodedModelPointDataset")?
                    .call1((decoded, &valid))?,
            )?;
            kwargs.set_item("run_time", &run)?;
            kwargs.set_item("fxx", 3)?;
            kwargs.set_item("out_path", path.to_str().unwrap())?;
            mx.getattr("extract")?
                .call(("gfs", 35.18, -97.44), Some(&kwargs))?;
            let path = PyModule::import(py, "pathlib")?
                .getattr("Path")?
                .call1((path.to_str().unwrap(),))?;
            let collection = PyModule::import(py, "sharpmod.bridge.sounding_cache")?
                .getattr("decode_collection")?
                .call1((path,))?
                .get_item(0)?;
            profiles.push(collection.call_method0("getHighlightedProf")?);
            metadata.insert(
                label.into(),
                serde_json::json!({"times":collection.getattr("_dates")?.len()?}),
            );
        }
        let metrics = PyList::new(
            py,
            [
                "mucape",
                "mlcape",
                "sbcape",
                "mucin",
                "mlcin",
                "srh_1km",
                "srh_3km",
                "shear_6km",
            ],
        )?;
        let engine = PyModule::import(py, "sharpmod.analysis.profile_metrics")?
            .getattr("ProfileMetricsEngine")?
            .call0()?;
        let mut values = Vec::new();
        let finite = PyModule::import(py, "sharpmod.bridge.params")?.getattr("num")?;
        for profile in profiles {
            let result = engine.call_method1("values", (&profile, &metrics))?;
            let mut normalized = serde_json::Map::new();
            for metric in [
                "mucape",
                "mlcape",
                "sbcape",
                "mucin",
                "mlcin",
                "srh_1km",
                "srh_3km",
                "shear_6km",
            ] {
                let value: Option<f64> = finite.call1((result.get_item(metric)?,))?.extract()?;
                normalized.insert(metric.into(), serde_json::json!(value));
            }
            for parcel in ["sfcpcl", "mlpcl", "mupcl"] {
                for name in ["bplus", "bminus", "lclhght", "lfchght", "elhght"] {
                    let value: Option<f64> = finite
                        .call1((profile.getattr(parcel)?.getattr(name)?,))?
                        .extract()?;
                    normalized.insert(format!("{parcel}.{name}"), serde_json::json!(value));
                }
            }
            values.push(normalized);
        }
        for (key, point) in &values[0] {
            let area = &values[1][key];
            if let (Some(a), Some(b)) = (point.as_f64(), area.as_f64()) {
                assert!(
                    (a - b).abs() <= 1e-6 * a.abs().max(1.0),
                    "{key}: point {a} vs single-cell box {b}"
                );
            } else {
                assert_eq!(point, area, "availability differs for {key}");
            }
        }
        metadata.insert("point_diagnostics".into(), values[0].clone().into());
        metadata.insert("box_diagnostics".into(), values[1].clone().into());
        std::fs::write(report, serde_json::to_vec_pretty(&metadata).unwrap()).unwrap();
        Ok(())
    })
    .unwrap();
}

//! Exercise portable trace data through the actual SHARPpy collection boundary.
use super::*;
use crate::grib::DecodedPoint;
use numpy::{IntoPyArray, PyReadonlyArray1};

#[test]
#[ignore = "requires SHARPMOD_BOX_TRACE_FIXTURE, SHARPMOD_BOX_TRACE_OUTPUT and the Python environment"]
fn grid_traces_round_trip_without_changing_the_mean_or_analysis_collection() {
    let fixture = std::env::var("SHARPMOD_BOX_TRACE_FIXTURE").unwrap();
    let output = std::env::var("SHARPMOD_BOX_TRACE_OUTPUT").unwrap();
    std::fs::copy(&fixture, &output).unwrap();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let decoder = PyModule::import(py, "sharpmod.io.decoder")?;
        let collection = decoder
            .getattr("load_npz")?
            .call1((&fixture,))?
            .get_item(0)?;
        let mean = collection.call_method0("getHighlightedProf")?;
        let mut matrix = Vec::new();
        for name in [
            "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg", "u", "v",
        ] {
            let values = mean.getattr(name)?.call_method1("filled", (-9999.0,))?;
            let values = values.extract::<PyReadonlyArray1<'_, f64>>()?;
            matrix.extend_from_slice(values.as_slice()?);
        }
        let levels = matrix.len() / 9;
        let point = DecodedPoint {
            matrix,
            level_count: levels,
            selected_latitude: 35.18,
            selected_longitude: -97.44,
            surface_relative_vorticity: None,
            surface_merged: true,
            below_ground_levels_removed: 0,
        };
        let mut warmer = point.clone();
        for field in [2, 3] {
            for value in &mut warmer.matrix[field * levels..(field + 1) * levels] {
                if *value != -9999.0 {
                    *value += 1.0;
                }
            }
        }
        warmer.selected_longitude += 0.03;
        let columns = [point, warmer];
        assert_eq!(
            trace_render::attach_box_profiles(py, &collection, &fixture)?,
            0
        );
        trace_artifact::persist(py, &output, &columns)?;
        let loaded = trace_artifact::load(py, &output)?;
        assert_eq!(loaded.len(), 2);
        for (trace, original) in loaded.iter().zip(&columns) {
            assert_eq!(trace.matrix, original.matrix);
            assert_eq!(trace.latitude, original.selected_latitude);
            assert_eq!(trace.longitude, original.selected_longitude);
        }
        let numpy = PyModule::import(py, "numpy")?;
        let old = numpy.getattr("load")?.call1((&fixture,))?;
        let new = numpy.getattr("load")?.call1((&output,))?;
        for name in old.getattr("files")?.extract::<Vec<String>>()? {
            assert!(
                numpy
                    .getattr("array_equal")?
                    .call1((old.get_item(&name)?, new.get_item(&name)?))?
                    .is_truthy()?,
                "mean array {name} changed"
            );
        }
        old.call_method0("close")?;
        new.call_method0("close")?;
        let highlight = collection.getattr("_highlight")?.extract::<String>()?;
        let cape = mean.getattr("sfcpcl")?.getattr("bplus")?.extract::<f64>()?;
        assert_eq!(
            trace_render::attach_box_profiles(py, &collection, &output)?,
            2
        );
        assert!(mean.is(&collection.call_method0("getHighlightedProf")?));
        assert_eq!(
            highlight,
            collection.getattr("_highlight")?.extract::<String>()?
        );
        assert_eq!(
            cape,
            mean.getattr("sfcpcl")?.getattr("bplus")?.extract::<f64>()?
        );
        let current = collection.call_method0("getCurrentProfs")?;
        assert_eq!(current.len()?, 3);
        let basic = PyModule::import(py, "sharppy.sharptab.profile")?.getattr("BasicProfile")?;
        for (index, column) in columns.iter().enumerate() {
            let profile = current.get_item(format!("Grid {:04}", index + 1))?;
            assert!(profile.get_type().is(&basic));
            for (field, name) in [
                (0, "pres"),
                (1, "hght"),
                (2, "tmpc"),
                (3, "dwpc"),
                (4, "wdir"),
                (5, "wspd"),
            ] {
                let values = profile.getattr(name)?.call_method1("filled", (-9999.0,))?;
                let values = values.extract::<PyReadonlyArray1<'_, f64>>()?;
                assert_eq!(
                    values.as_slice()?,
                    &column.matrix[field * levels..(field + 1) * levels]
                );
            }
        }
        // Reattaching is idempotent. Analysis decoding still sees one mean only.
        trace_render::attach_box_profiles(py, &collection, &output)?;
        assert_eq!(collection.getattr("_profs")?.len()?, 3);
        let analysis = decoder
            .getattr("load_npz")?
            .call1((&output,))?
            .get_item(0)?;
        assert_eq!(analysis.getattr("_profs")?.len()?, 1);
        Ok(())
    })
    .unwrap();
}

#[test]
#[ignore = "requires SHARPMOD_BOX_TRACE_OUTPUT and the Python environment"]
fn malformed_trace_archives_fail_before_any_profiles_are_attached() {
    let output = Path::new(&std::env::var("SHARPMOD_BOX_TRACE_OUTPUT").unwrap())
        .with_extension("invalid.npz");
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let arrays = PyDict::new(py);
        arrays.set_item("box_trace_version", vec![1_u64].into_pyarray(py))?;
        arrays.set_item("box_trace_data", vec![1000.0_f64; 18].into_pyarray(py))?;
        arrays.set_item(
            "box_trace_coordinates",
            vec![35.0_f64, -97.0].into_pyarray(py),
        )?;
        for offsets in [vec![0_u64, 27], vec![0, 0], vec![0, 18, 9], vec![0, 19]] {
            arrays.set_item("box_trace_offsets", offsets.into_pyarray(py))?;
            PyModule::import(py, "numpy")?
                .getattr("savez_compressed")?
                .call((output.to_string_lossy().as_ref(),), Some(&arrays))?;
            assert!(trace_artifact::load(py, output.to_str().unwrap()).is_err());
        }
        // Correct offsets with invalid pressure ordering are rejected too.
        arrays.set_item("box_trace_offsets", vec![0_u64, 18].into_pyarray(py))?;
        PyModule::import(py, "numpy")?
            .getattr("savez_compressed")?
            .call((output.to_string_lossy().as_ref(),), Some(&arrays))?;
        assert!(trace_artifact::load(py, output.to_str().unwrap()).is_err());
        Ok(())
    })
    .unwrap();
}

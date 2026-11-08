use super::*;
use pyo3::types::PyList;

pub(super) fn build<'py>(py: Python<'py>, output: &area::Output) -> PyResult<Bound<'py, PyDict>> {
    let value = PyDict::new(py);
    let bounds = PyDict::new(py);
    for (key, number) in [
        ("west", output.bounds.west),
        ("south", output.bounds.south),
        ("east", output.bounds.east),
        ("north", output.bounds.north),
    ] {
        bounds.set_item(key, number)?;
    }
    value.set_item("bounds", bounds)?;
    let (_, latitude) = output.bounds.center();
    let radius_km = 6371.0088;
    value.set_item(
        "width_km",
        radius_km * output.bounds.width().to_radians() * latitude.to_radians().cos(),
    )?;
    value.set_item(
        "height_km",
        radius_km * (output.bounds.north - output.bounds.south).to_radians(),
    )?;
    value.set_item("area_km2", output.requested_area_m2 / 1_000_000.0)?;
    value.set_item("grid_resolution_km", output.grid_resolution_km)?;
    value.set_item("grid_type", &output.grid_type)?;
    value.set_item("selected_columns", output.averaged.selected_columns)?;
    value.set_item("valid_columns", output.averaged.valid_columns)?;
    value.set_item("surface_pressure_hpa", output.averaged.surface_pressure_hpa)?;
    value.set_item("method", crate::box_soundings::METHOD_VERSION)?;
    value.set_item("profile_cache_hit", output.cache_hit)?;
    let coverage = PyList::empty(py);
    for level in &output.averaged.coverage {
        let row = PyDict::new(py);
        row.set_item("pressure_hpa", level.pressure_hpa)?;
        row.set_item("valid_columns", level.valid_columns)?;
        row.set_item("fraction", level.fraction)?;
        row.set_item("retained", level.retained)?;
        coverage.append(row)?;
    }
    value.set_item("coverage", coverage)?;
    let timings = PyDict::new(py);
    for (key, number) in [
        ("source_ms", output.timings.source_ms),
        ("geometry_ms", output.timings.geometry_ms),
        ("decode_ms", output.timings.decode_ms),
        ("unpack_read_ms", output.timings.unpack_read_ms),
        ("column_assembly_ms", output.timings.column_assembly_ms),
        ("interpolation_ms", output.timings.interpolation_ms),
        ("spatial_ms", output.timings.spatial_ms),
        ("aggregation_ms", output.timings.aggregation_ms),
        ("total_ms", output.timings.total_ms),
    ] {
        timings.set_item(key, number)?;
    }
    value.set_item("timings", timings)?;
    Ok(value)
}

/// Keep the selection and coverage with the portable artifact's existing sidecar.
pub(super) fn persist(py: Python<'_>, path: &str, metadata: &Bound<'_, PyDict>) -> PyResult<()> {
    let encoded: String = PyModule::import(py, "json")?
        .getattr("dumps")?
        .call1((metadata,))?
        .extract()?;
    let sidecar = Path::new(path).with_extension("json");
    let temporary = Path::new(path).with_extension("json.box-tmp");
    let write = || -> Result<(), Box<dyn std::error::Error>> {
        let mut value: serde_json::Value = serde_json::from_slice(&std::fs::read(&sidecar)?)?;
        value["box"] = serde_json::from_str(&encoded)?;
        std::fs::write(&temporary, serde_json::to_vec_pretty(&value)?)?;
        std::fs::rename(&temporary, &sidecar)?;
        Ok(())
    };
    write().map_err(|error| {
        failure(
            py,
            Error::Data(format!(
                "Could not preserve box extraction metadata: {error}"
            )),
        )
    })
}

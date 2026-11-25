mod composites;
mod stp;
#[cfg(test)]
mod tests;

use pyo3::prelude::*;

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(stp::fast_effective_stp_cin, module)?)?;
    module.add_function(wrap_pyfunction!(
        composites::max_thetae_vertical_velocity,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(composites::derived_dcp, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_ncape_ncin, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_ehi, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_vgp, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_hpi, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_peskov, module)?)?;
    module.add_function(wrap_pyfunction!(
        composites::derived_peskov_profile,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(composites::derived_lscp, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_nstp, module)?)?;
    module.add_function(wrap_pyfunction!(composites::derived_moshe, module)?)?;
    module.add_function(wrap_pyfunction!(
        composites::classify_hazard_inputs,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        composites::kuchera_snow_liquid_ratio,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        composites::column_max_temperature_c,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(composites::fire_ventilation_rate, module)?)?;
    Ok(())
}

//! Small validation helpers shared by the sounding and model DTOs.

/// Fails on the first repeated value, naming the field that must be unique.
pub fn unique(values: &[String], what: &str) -> Result<(), String> {
    let mut seen = std::collections::BTreeSet::new();
    for value in values {
        if !seen.insert(value) {
            return Err(format!("{what} must be unique; {value:?} appears twice"));
        }
    }
    Ok(())
}

pub fn check_text(field: &str, value: &str, low: usize, high: usize) -> Result<(), String> {
    if (low..=high).contains(&value.chars().count()) {
        Ok(())
    } else {
        Err(format!("{field} must be {low} to {high} characters"))
    }
}

pub fn check_opt_text(field: &str, value: Option<&String>, high: usize) -> Result<(), String> {
    value.map_or(Ok(()), |v| check_text(field, v, 0, high))
}

pub fn check_lat_lon(lat: Option<f64>, lon: Option<f64>) -> Result<(), String> {
    if lat.is_some_and(|v| !(-90.0..=90.0).contains(&v)) {
        return Err("latitude must be within [-90, 90]".into());
    }
    if lon.is_some_and(|v| !(-180.0..=180.0).contains(&v)) {
        return Err("longitude must be within [-180, 180]".into());
    }
    Ok(())
}

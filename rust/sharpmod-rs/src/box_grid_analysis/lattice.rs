use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyBool;

pub(crate) const MAX_BOX_POINTS: usize = 256;
pub(crate) const DEFAULT_TARGET_POINTS: usize = 64;
pub(crate) const MAX_POINT_PROVIDER_POINTS: usize = 24;
pub(crate) const DEFAULT_POINT_PROVIDER_TARGET: usize = 12;
pub(crate) const KM_PER_DEG_LAT: f64 = 111.32;
pub(crate) const MIN_SPAN_DEG: f64 = 1.0e-4;
const MIN_COS_LAT: f64 = 0.05;

fn box_error(py: Python<'_>, cls_name: &str, msg: &str) -> PyErr {
    py.import("sharpmod.analysis.box_sounding")
        .and_then(|module| module.getattr(cls_name))
        .and_then(|cls| cls.call1((msg,)))
        .map(PyErr::from_value)
        .unwrap_or_else(|_| PyRuntimeError::new_err(msg.to_string()))
}

fn py_to_f64_or_err(value: &Bound<'_, PyAny>) -> Result<f64, String> {
    if let Ok(v) = value.extract::<f64>() {
        return Ok(v);
    }
    if let Ok(text) = value.extract::<String>() {
        return text
            .trim()
            .parse::<f64>()
            .map_err(|err| format!("could not convert string to float: {err}"));
    }
    let type_name = value
        .get_type()
        .name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|_| "object".to_string());
    Err(format!(
        "float() argument must be a string or a real number, not '{type_name}'"
    ))
}

fn py_to_int_like_python(value: &Bound<'_, PyAny>) -> Result<i64, String> {
    if value.is_instance_of::<PyBool>() {
        return Err("boolean not allowed for integer parameter".to_string());
    }
    if let Ok(v) = value.extract::<i64>() {
        return Ok(v);
    }
    if let Ok(v) = value.extract::<f64>() {
        if !v.is_finite() {
            return Err("cannot convert non-finite float to integer".to_string());
        }
        return Ok(v as i64);
    }
    if let Ok(text) = value.extract::<String>() {
        return text
            .trim()
            .parse::<i64>()
            .map_err(|err| format!("invalid literal for int(): {err}"));
    }
    Err("expected integer".to_string())
}

pub(crate) fn normalize_lon180(lon: f64) -> f64 {
    ((lon + 180.0).rem_euclid(360.0)) - 180.0
}

pub(crate) fn km_per_deg_lon(lat: f64) -> f64 {
    let cos_lat = lat.to_radians().cos().abs();
    KM_PER_DEG_LAT * cos_lat.max(MIN_COS_LAT)
}

fn round6(value: f64) -> f64 {
    (value * 1_000_000.0).round_ties_even() / 1_000_000.0
}

fn validate_region(lat0: f64, lat1: f64, lon0: f64, lon_span: f64) -> Result<(), String> {
    for (name, val) in [
        ("lat0", lat0),
        ("lat1", lat1),
        ("lon0", lon0),
        ("lon_span", lon_span),
    ] {
        if !val.is_finite() {
            return Err(format!("box {name} must be a finite number"));
        }
    }
    if lat1 - lat0 < MIN_SPAN_DEG {
        return Err(
            "box latitude span is too small to sample; the two corners must differ in latitude"
                .to_string(),
        );
    }
    if lon_span < MIN_SPAN_DEG {
        return Err(
            "box longitude span is too small to sample; the two corners must differ in longitude"
                .to_string(),
        );
    }
    if lon_span > 360.0 {
        return Err("box longitude span cannot exceed 360 degrees".to_string());
    }
    if !(-90.0..=90.0).contains(&lat0) || !(-90.0..=90.0).contains(&lat1) {
        return Err("box latitudes must lie within [-90, 90]".to_string());
    }
    Ok(())
}

pub(crate) fn region_from_corners_impl(
    lat_a: f64,
    lon_a: f64,
    lat_b: f64,
    lon_b: f64,
) -> Result<(f64, f64, f64, f64), String> {
    let (raw0, raw1) = if lat_a <= lat_b {
        (lat_a, lat_b)
    } else {
        (lat_b, lat_a)
    };
    let lat0 = raw0.clamp(-90.0, 90.0);
    let lat1 = raw1.clamp(-90.0, 90.0);
    let (west, east) = if lon_a <= lon_b {
        (lon_a, lon_b)
    } else {
        (lon_b, lon_a)
    };
    let span = (east - west).min(360.0);
    let lon0 = normalize_lon180(west);
    validate_region(lat0, lat1, lon0, span)?;
    Ok((lat0, lat1, lon0, span))
}

pub(crate) fn region_from_bounds_impl(
    lon0: f64,
    lon1: f64,
    lat0_raw: f64,
    lat1_raw: f64,
) -> Result<(f64, f64, f64, f64), String> {
    let mut span = (lon1 - lon0).rem_euclid(360.0);
    if span < MIN_SPAN_DEG && (lon1 - lon0).abs() >= 180.0 {
        span = 360.0;
    }
    let lat0 = lat0_raw.min(lat1_raw);
    let lat1 = lat0_raw.max(lat1_raw);
    let norm_lon0 = normalize_lon180(lon0);
    validate_region(lat0, lat1, norm_lon0, span)?;
    Ok((lat0, lat1, norm_lon0, span))
}

pub(crate) fn region_contains_impl(
    lat0: f64,
    lat1: f64,
    lon0: f64,
    lon_span: f64,
    lat: f64,
    lon: f64,
) -> bool {
    if !(lat0 <= lat && lat <= lat1) {
        return false;
    }
    let delta = (normalize_lon180(lon) - lon0).rem_euclid(360.0);
    if delta <= lon_span {
        return true;
    }
    let tol = (1.0e-9 * delta.abs().max(lon_span.abs())).max(1.0e-9);
    (delta - lon_span).abs() <= tol
}

fn lattice_shape(lat_span: f64, lon_span: f64, center_lat: f64, spacing_km: f64) -> (usize, usize) {
    let lat_step = spacing_km / KM_PER_DEG_LAT;
    let lon_step = spacing_km / km_per_deg_lon(center_lat);
    let rows = ((lat_span / lat_step).floor() as isize + 1).max(1) as usize;
    let cols = ((lon_span / lon_step).floor() as isize + 1).max(1) as usize;
    (rows, cols)
}

fn snap_to_native(spacing_km: f64, native_km: f64) -> f64 {
    let multiple = ((spacing_km / native_km).ceil() as isize).max(1) as f64;
    multiple * native_km
}

fn even_nodes(start: f64, span: f64, count: usize) -> Vec<f64> {
    if count <= 1 {
        return vec![start + span / 2.0];
    }
    let step = span / ((count - 1) as f64);
    (0..count).map(|idx| start + step * (idx as f64)).collect()
}

pub(crate) type PlannedNode = (usize, usize, f64, f64, f64, f64);

/// Spacing in km, row and column counts, planned nodes, and notes.
pub(crate) type BoxLatticePlan = (f64, usize, usize, Vec<PlannedNode>, Vec<String>);

#[allow(clippy::too_many_arguments)]
pub(crate) fn plan_box_lattice_impl(
    lat0: f64,
    lat1: f64,
    lon0: f64,
    lon_span: f64,
    native_km: f64,
    point_only: bool,
    model_label: &str,
    spacing_km: Option<f64>,
    target_points: Option<i64>,
    max_points: Option<i64>,
) -> Result<BoxLatticePlan, String> {
    let mut notes: Vec<String> = Vec::new();
    let ceiling = if point_only {
        MAX_POINT_PROVIDER_POINTS
    } else {
        MAX_BOX_POINTS
    };
    let budget = match max_points {
        None => ceiling,
        Some(v) => {
            if v < 1 {
                return Err("max_points must be at least one".to_string());
            }
            (v as usize).min(ceiling)
        }
    };
    if point_only {
        notes.push(format!(
            "{model_label} answers one request per point rather than sharing one downloaded grid, so this box is limited to {budget} soundings."
        ));
    }
    if spacing_km.is_some() && target_points.is_some() {
        return Err("give either spacing_km or target_points, not both".to_string());
    }

    let lat_span = lat1 - lat0;
    let center_lat = (lat0 + lat1) / 2.0;
    let width_km = lon_span * km_per_deg_lon(center_lat);
    let height_km = lat_span * KM_PER_DEG_LAT;
    let area_km2 = width_km * height_km;

    let requested = if let Some(req) = spacing_km {
        if !req.is_finite() || req <= 0.0 {
            return Err("spacing_km must be positive".to_string());
        }
        if req < native_km {
            notes.push(format!(
                "Requested {req:.1} km spacing is finer than the {native_km:.1} km {model_label} grid and was raised to {native_km:.1} km so no two soundings come from one grid cell."
            ));
        }
        req
    } else {
        let mut target = match target_points {
            None => {
                if point_only {
                    DEFAULT_POINT_PROVIDER_TARGET
                } else {
                    DEFAULT_TARGET_POINTS
                }
            }
            Some(v) => {
                if v < 1 {
                    return Err("target_points must be at least one".to_string());
                }
                v as usize
            }
        };
        target = target.min(budget);
        let area = area_km2.max(1.0e-6);
        (area / (target as f64)).sqrt()
    };

    let mut spacing = snap_to_native(requested.max(native_km), native_km);
    let (mut rows, mut cols) = lattice_shape(lat_span, lon_span, center_lat, spacing);
    let mut coarsened_from: Option<f64> = None;
    let mut guard = 0usize;
    while rows * cols > budget && guard < 64 {
        guard += 1;
        if coarsened_from.is_none() {
            coarsened_from = Some(spacing);
        }
        let scale = ((rows * cols) as f64 / (budget as f64)).sqrt();
        let m1 = ((spacing / native_km).round_ties_even() as isize) + 1;
        let m2 = (spacing * scale / native_km).ceil() as isize;
        let multiple = m1.max(m2) as f64;
        spacing = multiple * native_km;
        (rows, cols) = lattice_shape(lat_span, lon_span, center_lat, spacing);
    }
    if let Some(orig) = coarsened_from {
        notes.push(format!(
            "Coarsened from {orig:.1} km to {spacing:.1} km to stay within {budget} soundings ({rows}x{cols})."
        ));
    }

    let lats = even_nodes(lat0, lat_span, rows);
    let lons = even_nodes(lon0, lon_span, cols);
    let mut points = Vec::with_capacity(rows * cols);
    for (row, &raw_lat) in lats.iter().rev().enumerate() {
        for (col, &lon_raw) in lons.iter().enumerate() {
            let raw_lon = normalize_lon180(lon_raw);
            points.push((row, col, raw_lat, raw_lon, round6(raw_lat), round6(raw_lon)));
        }
    }
    Ok((spacing, rows, cols, points, notes))
}

#[pyfunction]
pub(super) fn box_region_from_corners(
    py: Python<'_>,
    lat_a: &Bound<'_, PyAny>,
    lon_a: &Bound<'_, PyAny>,
    lat_b: &Bound<'_, PyAny>,
    lon_b: &Bound<'_, PyAny>,
) -> PyResult<(f64, f64, f64, f64)> {
    let parse = |val: &Bound<'_, PyAny>| {
        py_to_f64_or_err(val).map_err(|err| {
            box_error(
                py,
                "BoxRegionError",
                &format!("box corners must be numeric: {err}"),
            )
        })
    };
    let la = parse(lat_a)?;
    let lna = parse(lon_a)?;
    let lb = parse(lat_b)?;
    let lnb = parse(lon_b)?;
    region_from_corners_impl(la, lna, lb, lnb).map_err(|msg| box_error(py, "BoxRegionError", &msg))
}

#[pyfunction]
pub(super) fn box_region_from_bounds(
    py: Python<'_>,
    bounds: &Bound<'_, PyAny>,
) -> PyResult<(f64, f64, f64, f64)> {
    let invalid = || {
        box_error(
            py,
            "BoxRegionError",
            "bounds must be four numbers (lon0, lon1, lat0, lat1)",
        )
    };
    let mut iter = bounds.try_iter().map_err(|_| invalid())?;
    let mut vals = [0.0f64; 4];
    for slot in &mut vals {
        let item = iter.next().ok_or_else(invalid)?.map_err(|_| invalid())?;
        *slot = py_to_f64_or_err(&item).map_err(|_| invalid())?;
    }
    if iter.next().is_some() {
        return Err(invalid());
    }
    region_from_bounds_impl(vals[0], vals[1], vals[2], vals[3])
        .map_err(|msg| box_error(py, "BoxRegionError", &msg))
}

#[pyfunction]
pub(super) fn box_region_contains(
    lat0: f64,
    lat1: f64,
    lon0: f64,
    lon_span: f64,
    lat: f64,
    lon: f64,
) -> bool {
    region_contains_impl(lat0, lat1, lon0, lon_span, lat, lon)
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (
    lat0,
    lat1,
    lon0,
    lon_span,
    native_km,
    point_only,
    model_label,
    spacing_km = None,
    target_points = None,
    max_points = None
))]
pub(super) fn plan_box_lattice(
    py: Python<'_>,
    lat0: f64,
    lat1: f64,
    lon0: f64,
    lon_span: f64,
    native_km: f64,
    point_only: bool,
    model_label: &str,
    spacing_km: Option<&Bound<'_, PyAny>>,
    target_points: Option<&Bound<'_, PyAny>>,
    max_points: Option<&Bound<'_, PyAny>>,
) -> PyResult<BoxLatticePlan> {
    let spacing = match spacing_km {
        Some(obj) if !obj.is_none() => Some(
            py_to_f64_or_err(obj)
                .map_err(|_| box_error(py, "BoxSampleError", "spacing_km must be a number"))?,
        ),
        _ => None,
    };
    let target = match target_points {
        Some(obj) if !obj.is_none() => Some(
            py_to_int_like_python(obj)
                .map_err(|_| box_error(py, "BoxSampleError", "target_points must be an integer"))?,
        ),
        _ => None,
    };
    let budget = match max_points {
        Some(obj) if !obj.is_none() => Some(
            py_to_int_like_python(obj)
                .map_err(|_| box_error(py, "BoxSampleError", "max_points must be an integer"))?,
        ),
        _ => None,
    };

    plan_box_lattice_impl(
        lat0,
        lat1,
        lon0,
        lon_span,
        native_km,
        point_only,
        model_label,
        spacing,
        target,
        budget,
    )
    .map_err(|msg| box_error(py, "BoxSampleError", &msg))
}

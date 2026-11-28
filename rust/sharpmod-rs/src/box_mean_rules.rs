//! Native box-mean sounding rules (milestone M40).
//!
//! Rust ports of the pure half of ``sharpmod.analysis.box_mean`` -- the
//! module that averages every extracted sounding in a box into one
//! composite column:
//!
//! * ``mean_model_label`` ([`box_mean_model_label`]) and
//!   ``box_mean_badge_lines`` ([`box_mean_badge_lines`]) -- the wording that
//!   makes the Skew-T title and its on-plot callout say the column is an
//!   average;
//! * ``BoxMeanProfile.describe`` ([`box_mean_describe`]) -- the reader-facing
//!   one-line summary with its ``:.0f`` surface pressure;
//! * ``_reference_member`` ([`box_mean_reference_member_index`]) -- the
//!   deepest-member-wins, ties-toward-the-centre ladder rule;
//! * ``_interpolate`` ([`box_mean_interpolate_log_pressure`]) --
//!   ``np.interp`` in log-pressure with ``left=NaN``/``right=NaN``, exact
//!   duplicate-x handling included;
//! * ``_nanmean`` ([`box_mean_nanmean_stack`]) -- the NaN-ignoring column
//!   mean and contributor counts;
//! * ``_speed_direction`` ([`box_mean_speed_direction`]) -- the averaged
//!   u/v components resolved back to speed and meteorological direction,
//!   with the calm-vector rule;
//! * ``_strictly_stacked`` ([`box_mean_strictly_stacked_mask`]) -- the
//!   pressure-falling/height-rising run mask (NaN comparisons stay false
//!   exactly like NumPy's);
//! * ``_centre``'s coordinate fallback
//!   ([`box_mean_circular_centre`]) -- the circular mean that keeps a box on
//!   the antimeridian at the antimeridian, raising the module's
//!   ``BoxMeanError`` when no coordinates are available.
//!
//! Results match the Python sources: the same tie-to-even ``round()`` and
//! ``:.0f`` formatting, the same ``min``-by-``(-levels, distance)`` first-
//! wins semantics, ``np.interp``'s binary search (exact hits on a duplicate
//! run return the last duplicate), the same ``hypot``/``arctan2``
//! component round trip, and the same ``[-180, 180)`` longitude wrap.  The
//! one known sub-ulp divergence, shared with the rest of the crate's numpy
//! ports: column sums are sequential, while ``numpy`` switches to pairwise
//! summation for eight or more addends, so means of very large stacks can
//! differ in the last bit.

use numpy::ndarray::{ArrayView2, Axis};
use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::{PyIndexError, PyValueError};
use pyo3::prelude::*;

type PyNanmeanPair<'py> = (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<i64>>);
type PyWindPair<'py> = (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>);

// ---------------------------------------------------------------------------
// BoxMeanError helper (sharpmod.analysis.box_mean.BoxMeanError)
// ---------------------------------------------------------------------------

/// Raise the Python ``BoxMeanError`` class with ``message``, falling back to
/// a plain ``ValueError`` when the module cannot be imported (``BoxMeanError``
/// subclasses ``Exception`` through the module's own hierarchy).
fn box_mean_error(py: Python<'_>, message: String) -> PyErr {
    match PyModule::import(py, "sharpmod.analysis.box_mean")
        .and_then(|module| module.getattr("BoxMeanError"))
        .and_then(|class| class.call1((message.clone(),)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => PyValueError::new_err(message),
    }
}

/// Read a 1-D read-only array as a ``Vec``, contiguous or strided.
fn readonly_array1(array: &PyReadonlyArray1<'_, f64>) -> Vec<f64> {
    match array.as_slice() {
        Ok(slice) => slice.to_vec(),
        Err(_) => array.as_array().iter().copied().collect(),
    }
}

// ---------------------------------------------------------------------------
// Display wording (mean_model_label / box_mean_badge_lines)
// ---------------------------------------------------------------------------

/// The pure half of ``mean_model_label``: the model name to display for a
/// mean of ``members`` soundings (``str(model or "").strip() or "MODEL"``,
/// ``int(members)`` falling back to 0, then the count suffix).
pub(crate) fn model_label_core(model: Option<&str>, members: Option<i64>) -> String {
    let trimmed = model.unwrap_or("").trim();
    let label = if trimmed.is_empty() { "MODEL" } else { trimmed };
    let count = members.unwrap_or(0);
    if count <= 0 {
        format!("{label} box mean")
    } else {
        format!("{label} box mean of {count}")
    }
}

/// The model name to display for a mean of ``members`` soundings
/// (``box_mean.mean_model_label``).
///
/// ``None`` or an unconvertible member count counts as zero, so the label
/// is ``"HRRR box mean"`` without a count and ``"HRRR box mean of 132"``
/// with one; a missing product name becomes ``MODEL``.
#[pyfunction]
#[pyo3(signature = (model = None, members = None))]
fn box_mean_model_label(model: Option<&str>, members: Option<i64>) -> String {
    model_label_core(model, members)
}

/// The pure half of ``box_mean_badge_lines``: the on-plot callout for a mean
/// sounding, or nothing for a point sounding.
pub(crate) fn badge_lines_core(box_mean: bool, members: Option<i64>) -> Vec<String> {
    if !box_mean {
        return vec![];
    }
    let count = members.unwrap_or(0);
    let headline = if count > 0 {
        format!("BOX MEAN \u{00b7} {count} SOUNDINGS")
    } else {
        "BOX MEAN".to_string()
    };
    vec![
        headline,
        "indices of the mean, not mean indices".to_string(),
    ]
}

/// The on-plot callout for a mean sounding, or ``[]`` for a point sounding
/// (``box_mean.box_mean_badge_lines``).
///
/// Two lines: the headline naming the member count (``BOX MEAN · N
/// SOUNDINGS``, or ``BOX MEAN`` without one) and the reminder that the
/// printed indices belong to the mean column, not the mean of the members'
/// indices.
#[pyfunction]
#[pyo3(signature = (box_mean, members = None))]
fn box_mean_badge_lines(box_mean: bool, members: Option<i64>) -> Vec<String> {
    badge_lines_core(box_mean, members)
}

// ---------------------------------------------------------------------------
// Reader-facing summary (BoxMeanProfile.describe)
// ---------------------------------------------------------------------------

/// Format one float the way Python's ``f"{value:.0f}"`` does for the values
/// a surface pressure can take: ties to even, with C spellings for the
/// non-finite outliers.
fn format_surface_hpa(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value.is_sign_negative() {
            "-inf".to_string()
        } else {
            "inf".to_string()
        };
    }
    format!("{}", value.round_ties_even() as i64)
}

/// The pure half of ``BoxMeanProfile.describe``: the one-line summary with
/// the member/level counts, the ``:.0f`` surface pressure, the unreadable
/// members, and the trimmed/clamped level tallies.
pub(crate) fn describe_core(
    members: i64,
    levels: usize,
    surface_pressure_hpa: f64,
    requested: i64,
    trimmed_below: i64,
    trimmed_above: i64,
    clamped_dewpoints: i64,
) -> String {
    let surface = format_surface_hpa(surface_pressure_hpa);
    let mut text = format!("mean of {members} soundings, {levels} levels, surface {surface} hPa");
    if requested > members {
        text.push_str(&format!("; {} unreadable", requested - members));
    }
    let trimmed = trimmed_below + trimmed_above;
    if trimmed != 0 {
        text.push_str(&format!(
            "; {trimmed} level(s) outside the layer every sounding shares"
        ));
    }
    if clamped_dewpoints != 0 {
        text.push_str(&format!("; {clamped_dewpoints} dewpoint(s) clamped"));
    }
    text
}

/// The reader-facing one-line summary of a box mean
/// (``BoxMeanProfile.describe``).
///
/// Exactly the Python wording: the member and level counts and the
/// ``:.0f``-formatted surface pressure, then the unreadable-member count
/// when more soundings were offered than contributed, the trimmed-level
/// count when the members do not all span the shared layer, and the
/// clamped-dewpoint count when the averaged mixing ratio implied
/// supersaturation.
#[pyfunction]
fn box_mean_describe(
    members: i64,
    levels: usize,
    surface_pressure_hpa: f64,
    requested: i64,
    trimmed_below: i64,
    trimmed_above: i64,
    clamped_dewpoints: i64,
) -> String {
    describe_core(
        members,
        levels,
        surface_pressure_hpa,
        requested,
        trimmed_below,
        trimmed_above,
        clamped_dewpoints,
    )
}

// ---------------------------------------------------------------------------
// Reference member (box_mean._reference_member)
// ---------------------------------------------------------------------------

/// The pure half of ``_reference_member``: pick the member whose ladder
/// becomes the shared vertical coordinate.  The deepest member wins; ties
/// break toward the requested centre by cosine-weighted squared degree
/// distance; further ties keep the earlier member, exactly like Python's
/// first-minimum ``min`` (a NaN distance never displaces the incumbent).
pub(crate) fn reference_member_index_core(
    members: &[(usize, Option<f64>, Option<f64>)],
    lat: Option<f64>,
    lon: Option<f64>,
) -> Option<usize> {
    let scale = match lat {
        Some(value) => value.to_radians().cos(),
        None => 1.0,
    };
    let mut best: Option<(usize, usize, f64)> = None; // (index, levels, distance)
    for (index, (levels, member_lat, member_lon)) in members.iter().enumerate() {
        let distance = match (lat, lon, *member_lat, *member_lon) {
            (Some(lat_value), Some(lon_value), Some(m_lat), Some(m_lon)) => {
                (m_lat - lat_value).powi(2) + ((m_lon - lon_value) * scale).powi(2)
            }
            _ => 0.0,
        };
        let replaces = match best {
            None => true,
            Some((_, best_levels, best_distance)) => {
                // Python's rank tuple (-levels, distance) under `<`.
                if *levels > best_levels {
                    true
                } else if *levels == best_levels {
                    distance < best_distance
                } else {
                    false
                }
            }
        };
        if replaces {
            best = Some((index, *levels, distance));
        }
    }
    best.map(|(index, _, _)| index)
}

/// The index of the member whose levels become the shared vertical ladder
/// (``box_mean._reference_member``).
///
/// Each entry of ``member_meta`` is one member's ``(levels, lat, lon)``.
/// The deepest member wins; ties break toward the requested ``(lat, lon)``
/// by squared degree distance with a cosine weight on the longitude
/// difference (a member without coordinates, or a call without a centre,
/// scores zero like Python); further ties keep the earlier member.  An
/// empty list resolves to ``None``.
#[pyfunction]
#[pyo3(signature = (member_meta, lat = None, lon = None))]
fn box_mean_reference_member_index(
    member_meta: Vec<(usize, Option<f64>, Option<f64>)>,
    lat: Option<f64>,
    lon: Option<f64>,
) -> Option<usize> {
    reference_member_index_core(&member_meta, lat, lon)
}

// ---------------------------------------------------------------------------
// Log-pressure interpolation (box_mean._interpolate / np.interp)
// ---------------------------------------------------------------------------

/// The pure half of ``_interpolate``: ``np.interp`` in log-pressure with
/// ``left=NaN`` and ``right=NaN``.  Finite ``(source, value)`` pairs are
/// sorted stably by source; targets outside the member's own range, NaN
/// targets, and members with fewer than two finite pairs all yield NaN, so
/// a member that does not reach a level does not vote on it.
pub(crate) fn interpolate_log_pressure_core(
    target_log: &[f64],
    source_log: &[f64],
    values: &[f64],
) -> Result<Vec<f64>, String> {
    if source_log.len() != values.len() {
        return Err(format!(
            "operands could not be broadcast together with shapes ({},) ({},) ",
            source_log.len(),
            values.len()
        ));
    }
    let mut pairs: Vec<(f64, f64)> = source_log
        .iter()
        .zip(values.iter())
        .filter(|(source, value)| source.is_finite() && value.is_finite())
        .map(|(&source, &value)| (source, value))
        .collect();
    if pairs.len() < 2 {
        return Ok(vec![f64::NAN; target_log.len()]);
    }
    // np.interp needs an increasing coordinate; log-pressure decreases
    // upward, and a stable sort keeps duplicate sources in input order.
    pairs.sort_by(|left, right| left.0.total_cmp(&right.0));
    let last = pairs.len() - 1;
    let first_source = pairs[0].0;
    let last_source = pairs[last].0;
    let mut result = Vec::with_capacity(target_log.len());
    for &target in target_log {
        if !target.is_finite() || target < first_source || target > last_source {
            // NaN in, NaN out; left=NaN below the member's own range and
            // right=NaN above it.
            result.push(f64::NAN);
            continue;
        }
        // np.interp's bracket search: i and j end adjacent with
        // pairs[i].0 <= target <= pairs[j].0, so an exact hit lands on one
        // of them (the last of a duplicate run), and the divided difference
        // never divides by zero.
        let mut low = 0;
        let mut high = last;
        while high - low > 1 {
            let midpoint = (low + high) / 2;
            if target < pairs[midpoint].0 {
                high = midpoint;
            } else {
                low = midpoint;
            }
        }
        let value = if target == pairs[low].0 {
            pairs[low].1
        } else if target == pairs[high].0 {
            pairs[high].1
        } else {
            let (x_low, y_low) = pairs[low];
            let (x_high, y_high) = pairs[high];
            let slope = (y_high - y_low) / (x_high - x_low);
            slope * (target - x_low) + y_low
        };
        result.push(value);
    }
    Ok(result)
}

/// Interpolate ``values`` onto ``target_log`` in log-pressure
/// (``box_mean._interpolate``).
///
/// Pairs where either the source or the value is non-finite are dropped;
/// fewer than two surviving pairs yields all NaN.  The surviving pairs are
/// sorted stably by source and interpolated exactly like ``np.interp`` with
/// ``left=NaN``/``right=NaN``: targets outside ``[source[0],
/// source[-1]]`` and NaN targets become NaN, exact hits return the value
/// of the last duplicate in a run, and interior targets get the bracket's
/// divided difference.
#[pyfunction]
fn box_mean_interpolate_log_pressure<'py>(
    py: Python<'py>,
    target_log: PyReadonlyArray1<'py, f64>,
    source_log: PyReadonlyArray1<'py, f64>,
    values: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let target: Vec<f64> = readonly_array1(&target_log);
    let source: Vec<f64> = readonly_array1(&source_log);
    let column: Vec<f64> = readonly_array1(&values);
    let interpolated =
        interpolate_log_pressure_core(&target, &source, &column).map_err(PyValueError::new_err)?;
    Ok(interpolated.into_pyarray(py))
}

// ---------------------------------------------------------------------------
// NaN-ignoring column means (box_mean._nanmean)
// ---------------------------------------------------------------------------

/// The pure half of ``_nanmean``: for each column of a stack, the count of
/// finite entries and their mean, NaN when nothing in the column is finite.
pub(crate) fn nanmean_stack_core(stack: ArrayView2<'_, f64>) -> (Vec<f64>, Vec<i64>) {
    let mut means: Vec<f64> = Vec::new();
    let mut counts: Vec<i64> = Vec::new();
    for column in stack.axis_iter(Axis(1)) {
        let mut total = 0.0;
        let mut count: i64 = 0;
        for &value in column.iter() {
            if value.is_finite() {
                total += value;
                count += 1;
            }
        }
        counts.push(count);
        means.push(if count > 0 {
            total / count as f64
        } else {
            f64::NAN
        });
    }
    (means, counts)
}

/// Mean and contributor count down a stack of columns, ignoring NaN
/// (``box_mean._nanmean``).
///
/// For each column, ``counts`` holds the number of finite entries and
/// ``means`` their arithmetic mean, or NaN when no entry is finite
/// (infinities count as absent, like ``np.isfinite``).  The stack may be
/// non-contiguous; a stack with zero rows yields NaN means with zero
/// counts, and a zero-column stack yields empty arrays.
#[pyfunction]
fn box_mean_nanmean_stack<'py>(
    py: Python<'py>,
    stack: PyReadonlyArray2<'py, f64>,
) -> PyResult<PyNanmeanPair<'py>> {
    let (means, counts) = nanmean_stack_core(stack.as_array());
    Ok((means.into_pyarray(py), counts.into_pyarray(py)))
}

// ---------------------------------------------------------------------------
// Averaged wind components (box_mean._speed_direction)
// ---------------------------------------------------------------------------

/// The pure half of ``_speed_direction``: resolve averaged ``(u, v)``
/// components back to speed and meteorological direction.  Non-finite
/// components NaN both outputs; a calm mean (speed below ``1e-6`` kt)
/// reports direction ``0.0`` so it cannot read as due north.
pub(crate) fn speed_direction_core(u: &[f64], v: &[f64]) -> Result<(Vec<f64>, Vec<f64>), String> {
    if u.len() != v.len() {
        return Err(format!(
            "operands could not be broadcast together with shapes ({},) ({},) ",
            u.len(),
            v.len()
        ));
    }
    let mut speeds = Vec::with_capacity(u.len());
    let mut directions = Vec::with_capacity(u.len());
    for (&u_value, &v_value) in u.iter().zip(v.iter()) {
        let both_finite = u_value.is_finite() && v_value.is_finite();
        if !both_finite {
            speeds.push(f64::NAN);
            directions.push(f64::NAN);
            continue;
        }
        let speed = u_value.hypot(v_value);
        // Direction is where the wind comes *from*, in [0, 360).
        let direction = ((-u_value).atan2(-v_value).to_degrees() + 360.0).rem_euclid(360.0);
        speeds.push(speed);
        directions.push(if speed < 1e-6 { 0.0 } else { direction });
    }
    Ok((speeds, directions))
}

/// Resolve averaged wind components to ``(speed, direction)``
/// (``box_mean._speed_direction``).
///
/// ``speed`` is ``hypot(u, v)`` and ``direction`` the meteorological
/// bearing in ``[0, 360)``; a level where either component is non-finite
/// gets NaN in both outputs, and a calm mean (``speed < 1e-6``) reports
/// direction ``0.0`` rather than a due-north vector.  Mismatched lengths
/// are refused with NumPy's broadcast message.
#[pyfunction]
fn box_mean_speed_direction<'py>(
    py: Python<'py>,
    u: PyReadonlyArray1<'py, f64>,
    v: PyReadonlyArray1<'py, f64>,
) -> PyResult<PyWindPair<'py>> {
    let u_values = readonly_array1(&u);
    let v_values = readonly_array1(&v);
    let (speeds, directions) =
        speed_direction_core(&u_values, &v_values).map_err(PyValueError::new_err)?;
    Ok((speeds.into_pyarray(py), directions.into_pyarray(py)))
}

// ---------------------------------------------------------------------------
// Vertical stacking mask (box_mean._strictly_stacked)
// ---------------------------------------------------------------------------

/// The pure half of ``_strictly_stacked``: keep the longest run with
/// pressure strictly falling and height strictly rising.  The first level
/// always starts the run; afterwards a level survives only when
/// ``p < last_p and h > last_h`` (false whenever either side is NaN, so
/// kept-NaN references poison the rest, exactly like the Python loop).
pub(crate) fn strictly_stacked_mask_core(
    pressure: &[f64],
    height: &[f64],
) -> Result<Vec<bool>, String> {
    let mut keep = vec![false; pressure.len()];
    let mut last: Option<(f64, f64)> = None;
    for (index, &p) in pressure.iter().enumerate() {
        // float(height[index]): a short height column fails at the first
        // out-of-bounds access, exactly as in Python.
        let h = match height.get(index) {
            Some(&value) => value,
            None => {
                return Err(format!(
                    "index {index} is out of bounds for axis 0 with size {}",
                    height.len()
                ))
            }
        };
        if last.is_some_and(|(last_p, last_h)| !(p < last_p && h > last_h)) {
            continue;
        }
        keep[index] = true;
        last = Some((p, h));
    }
    Ok(keep)
}

/// The mask of levels with pressure strictly falling and height strictly
/// rising (``box_mean._strictly_stacked``).
///
/// The first level always starts the run and updates the reference pair;
/// a level survives only when its pressure is below the last kept pressure
/// and its height above the last kept height, so NaN on either side drops
/// the level without updating the reference.  A height column shorter than
/// the pressure column raises ``IndexError`` at the first missing entry
/// (NumPy's message); a longer one simply has its tail unused.
#[pyfunction]
fn box_mean_strictly_stacked_mask(
    pressure: PyReadonlyArray1<'_, f64>,
    height: PyReadonlyArray1<'_, f64>,
) -> PyResult<Vec<bool>> {
    let pressure_values = readonly_array1(&pressure);
    let height_values = readonly_array1(&height);
    strictly_stacked_mask_core(&pressure_values, &height_values).map_err(PyIndexError::new_err)
}

// ---------------------------------------------------------------------------
// Circular centre (box_mean._centre)
// ---------------------------------------------------------------------------

/// The pure half of ``_centre``'s coordinate fallback: the arithmetic mean
/// latitude and the circular mean longitude, wrapped into ``[-180, 180)``.
pub(crate) fn circular_centre_core(lats: &[f64], lons: &[f64]) -> Result<(f64, f64), String> {
    if lats.is_empty() || lons.is_empty() {
        return Err(
            "these soundings carry no coordinates, so the centre of the \
             averaged area is unknown; pass lat and lon"
                .to_string(),
        );
    }
    let mean_lat = lats.iter().sum::<f64>() / lats.len() as f64;
    let mut sin_sum = 0.0;
    let mut cos_sum = 0.0;
    for &lon in lons {
        let radians = lon.to_radians();
        sin_sum += radians.sin();
        cos_sum += radians.cos();
    }
    let count = lons.len() as f64;
    // np.deg2rad -> np.mean(np.sin), np.mean(np.cos) -> np.arctan2 ->
    // np.degrees, then the [-180, 180) wrap.
    let mean_lon = (sin_sum / count).atan2(cos_sum / count).to_degrees();
    let wrapped = (mean_lon + 180.0).rem_euclid(360.0) - 180.0;
    Ok((mean_lat, wrapped))
}

/// The centre a mean sounding represents, from its members' coordinates
/// (``box_mean._centre``'s fallback).
///
/// The latitude is the arithmetic mean; the longitude is the circular mean
/// (``atan2`` of the mean sine and cosine), which leaves a box around the
/// antimeridian at the antimeridian instead of moving it to Greenwich, and
/// is wrapped into ``[-180, 180)``.  Either empty list raises the module's
/// ``BoxMeanError`` verbatim.
#[pyfunction]
fn box_mean_circular_centre(
    py: Python<'_>,
    lats: Vec<f64>,
    lons: Vec<f64>,
) -> PyResult<(f64, f64)> {
    circular_centre_core(&lats, &lons).map_err(|message| box_mean_error(py, message))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(box_mean_model_label, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_badge_lines, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_describe, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_reference_member_index, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_interpolate_log_pressure, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_nanmean_stack, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_speed_direction, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_strictly_stacked_mask, module)?)?;
    module.add_function(wrap_pyfunction!(box_mean_circular_centre, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use numpy::ndarray::Array2;

    #[test]
    fn model_label_names_the_average() {
        for (members, expected) in [
            (Some(132), "HRRR box mean of 132"),
            (Some(2), "HRRR box mean of 2"),
            (Some(0), "HRRR box mean"),
            (None, "HRRR box mean"),
            (Some(-4), "HRRR box mean"),
        ] {
            assert_eq!(model_label_core(Some("HRRR"), members), expected);
        }
        assert_eq!(model_label_core(Some(""), Some(5)), "MODEL box mean of 5");
        assert_eq!(model_label_core(None, Some(5)), "MODEL box mean of 5");
        assert_eq!(
            model_label_core(Some("   "), Some(5)),
            "MODEL box mean of 5"
        );
        assert_eq!(
            model_label_core(Some("  HRRR  "), Some(4)),
            "HRRR box mean of 4"
        );
    }

    #[test]
    fn badge_lines_call_out_the_average() {
        assert!(badge_lines_core(false, Some(12)).is_empty());
        assert_eq!(
            badge_lines_core(true, Some(12)),
            vec![
                "BOX MEAN \u{00b7} 12 SOUNDINGS".to_string(),
                "indices of the mean, not mean indices".to_string(),
            ]
        );
        for members in [None, Some(0), Some(-3)] {
            assert_eq!(
                badge_lines_core(true, members),
                vec![
                    "BOX MEAN".to_string(),
                    "indices of the mean, not mean indices".to_string(),
                ]
            );
        }
    }

    #[test]
    fn describe_reports_the_reader_facing_summary() {
        let text = describe_core(4, 42, 1013.5, 4, 0, 0, 0);
        assert_eq!(text, "mean of 4 soundings, 42 levels, surface 1014 hPa");
        // f"{:.0f}" rounds ties to even: 1012.5 -> 1012.
        let text = describe_core(4, 42, 1012.5, 4, 0, 0, 0);
        assert_eq!(text, "mean of 4 soundings, 42 levels, surface 1012 hPa");
        let text = describe_core(3, 10, 1000.45, 4, 2, 1, 3);
        assert_eq!(
            text,
            "mean of 3 soundings, 10 levels, surface 1000 hPa; 1 unreadable; \
             3 level(s) outside the layer every sounding shares; 3 dewpoint(s) clamped"
        );
        // No caveats when every member contributed and nothing was trimmed.
        let text = describe_core(4, 12, 1012.4, 4, 0, 0, 0);
        assert_eq!(text, "mean of 4 soundings, 12 levels, surface 1012 hPa");
    }

    #[test]
    fn surface_format_matches_python_dot_zero_f() {
        assert_eq!(format_surface_hpa(1012.4), "1012");
        assert_eq!(format_surface_hpa(1012.5), "1012");
        assert_eq!(format_surface_hpa(1013.5), "1014");
        assert_eq!(format_surface_hpa(1013.6), "1014");
        assert_eq!(format_surface_hpa(0.0), "0");
        assert_eq!(format_surface_hpa(f64::NAN), "nan");
        assert_eq!(format_surface_hpa(f64::INFINITY), "inf");
        assert_eq!(format_surface_hpa(f64::NEG_INFINITY), "-inf");
    }

    #[test]
    fn reference_member_is_the_deepest_then_the_nearest() {
        let members = [
            (5, Some(36.0), Some(-96.0)),
            (9, Some(38.0), Some(-96.0)),
            (9, Some(36.2), Some(-96.0)),
            (7, Some(36.1), Some(-96.0)),
        ];
        // Deepest wins; between the two nine-level members the one nearer
        // the requested centre wins.
        assert_eq!(
            reference_member_index_core(&members, Some(36.0), Some(-96.0)),
            Some(2)
        );
        // Without a centre every distance is zero: the first deepest wins.
        let tied = [(9, Some(38.0), Some(-96.0)), (9, Some(36.2), Some(-96.0))];
        assert_eq!(reference_member_index_core(&tied, None, None), Some(0));
        // A centre without a longitude also zeroes every distance.
        assert_eq!(
            reference_member_index_core(&tied, Some(36.0), None),
            Some(0)
        );
        // A member without coordinates cannot compete on distance.
        let partial = [(9, None, None), (9, Some(36.5), Some(-96.0))];
        assert_eq!(
            reference_member_index_core(&partial, Some(36.4), Some(-96.0)),
            Some(0)
        );
        assert_eq!(
            reference_member_index_core(&[], Some(36.0), Some(-96.0)),
            None
        );
    }

    #[test]
    fn interpolate_matches_np_interp_on_a_clean_ladder() {
        let out = interpolate_log_pressure_core(&[1.0], &[0.0, 2.0], &[0.0, 10.0]).unwrap();
        assert_eq!(out, vec![5.0]);
        // Decreasing coordinates are sorted together with their values.
        let out = interpolate_log_pressure_core(&[1.0], &[2.0, 0.0], &[10.0, 0.0]).unwrap();
        assert_eq!(out, vec![5.0]);
    }

    #[test]
    fn interpolate_extrapolates_to_nan_on_both_sides() {
        let target = [
            -1.0,
            0.0,
            2.0,
            3.0,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
        ];
        let out = interpolate_log_pressure_core(&target, &[0.0, 2.0], &[5.0, 9.0]).unwrap();
        assert!(out[0].is_nan(), "left of the member's own range");
        assert_eq!(out[1], 5.0, "exact lower endpoint");
        assert_eq!(out[2], 9.0, "exact upper endpoint");
        assert!(out[3].is_nan(), "right of the member's own range");
        assert!(out[4].is_nan(), "NaN target");
        assert!(out[5].is_nan(), "+inf target");
        assert!(out[6].is_nan(), "-inf target");
    }

    #[test]
    fn interpolate_needs_two_finite_pairs() {
        let out = interpolate_log_pressure_core(&[1.0], &[0.0, f64::NAN], &[1.0, 2.0]).unwrap();
        assert!(out[0].is_nan());
        let out = interpolate_log_pressure_core(&[1.0], &[0.0, 1.0], &[1.0, f64::NAN]).unwrap();
        assert!(out[0].is_nan());
        // Non-finite pairs are dropped from an otherwise usable member.
        let out = interpolate_log_pressure_core(
            &[1.5],
            &[0.0, f64::NAN, 2.0, f64::INFINITY],
            &[0.0, 7.0, 10.0, 7.0],
        )
        .unwrap();
        assert_eq!(out[0], 7.5);
        // Mismatched coordinate/value lengths refuse to broadcast.
        assert!(interpolate_log_pressure_core(&[0.0], &[0.0, 1.0], &[1.0]).is_err());
    }

    #[test]
    fn interpolate_handles_duplicates_like_np_interp() {
        // Exact hits on a duplicate run return the last of the run, and the
        // interior bracket interpolates from it (verified against np.interp).
        let source = [1.0, 1.0, 2.0];
        let values = [10.0, 20.0, 30.0];
        for (target, expected) in [(1.0, 20.0), (1.5, 25.0), (2.0, 30.0)] {
            let out = interpolate_log_pressure_core(&[target], &source, &values).unwrap();
            assert_eq!(out[0], expected);
        }
        let out =
            interpolate_log_pressure_core(&[0.0], &[0.0, 0.0, 1.0], &[1.0, 2.0, 3.0]).unwrap();
        assert_eq!(out[0], 2.0);
        let source = [1.0, 2.0, 2.0, 2.0, 3.0];
        let values = [10.0, 20.0, 21.0, 22.0, 30.0];
        for (target, expected) in [(2.0, 22.0), (2.5, 26.0)] {
            let out = interpolate_log_pressure_core(&[target], &source, &values).unwrap();
            assert_eq!(out[0], expected);
        }
    }

    #[test]
    fn nanmean_counts_and_averages_each_column() {
        let stack = Array2::from_shape_vec(
            (3, 4),
            vec![
                1.0,
                f64::NAN,
                f64::INFINITY,
                2.0, //
                3.0,
                f64::NAN,
                5.0,
                f64::NAN, //
                f64::NAN,
                f64::NAN,
                7.0,
                4.0,
            ],
        )
        .unwrap();
        let (means, counts) = nanmean_stack_core(stack.view());
        assert_eq!(counts, vec![2, 0, 2, 2]);
        assert_eq!(means[0], 2.0);
        assert!(means[1].is_nan(), "a column of only NaN/inf is NaN");
        assert_eq!(means[2], 6.0, "infinities count as absent");
        assert_eq!(means[3], 3.0);
        // Zero rows: every column reports NaN with a zero count.
        let empty = Array2::from_shape_vec((0, 2), Vec::<f64>::new()).unwrap();
        let (means, counts) = nanmean_stack_core(empty.view());
        assert_eq!(counts, vec![0, 0]);
        assert!(means[0].is_nan() && means[1].is_nan());
        // Zero columns: empty outputs.
        let empty = Array2::from_shape_vec((2, 0), Vec::<f64>::new()).unwrap();
        let (means, counts) = nanmean_stack_core(empty.view());
        assert!(means.is_empty() && counts.is_empty());
    }

    #[test]
    fn opposing_winds_average_across_north_not_through_south() {
        // 350 and 10 degrees at 20 kt: the component mean points north.
        let mut u_sum = 0.0;
        let mut v_sum = 0.0;
        for direction in [350.0, 10.0] {
            let radians = f64::to_radians(direction);
            u_sum += -20.0 * radians.sin();
            v_sum += -20.0 * radians.cos();
        }
        let (speeds, directions) = speed_direction_core(&[u_sum / 2.0], &[v_sum / 2.0]).unwrap();
        assert!(
            directions[0] < 0.5 || directions[0] > 359.5,
            "the mean bearing is north, not south: {}",
            directions[0]
        );
        let expected = 20.0 * f64::to_radians(10.0).cos();
        assert!((speeds[0] - expected).abs() < 0.5);
    }

    #[test]
    fn a_cancelling_mean_is_calm_not_due_north() {
        let (speeds, directions) = speed_direction_core(&[0.0], &[0.0]).unwrap();
        assert_eq!(speeds[0], 0.0);
        assert_eq!(directions[0], 0.0);
        // Sub-threshold speeds also read as calm.
        let (speeds, directions) = speed_direction_core(&[4e-7], &[0.0]).unwrap();
        assert_eq!(directions[0], 0.0);
        assert!(speeds[0] < 1e-6);
    }

    #[test]
    fn speed_direction_follows_the_meteorological_convention() {
        let (speeds, directions) =
            speed_direction_core(&[3.0, 5.0, 0.0, 0.0], &[4.0, 0.0, -5.0, 5.0]).unwrap();
        assert_eq!(speeds, vec![5.0, 5.0, 5.0, 5.0]);
        assert!((directions[0] - 216.869_897_645_844).abs() < 1e-9);
        assert_eq!(directions[1], 270.0, "from the west: positive u, no v");
        assert_eq!(directions[2], 0.0, "from the north");
        // A due-south vector reads 180, not -180.
        assert_eq!(directions[3], 180.0);
    }

    #[test]
    fn non_finite_components_nan_both_outputs() {
        let (speeds, directions) =
            speed_direction_core(&[1.0, f64::NAN, 2.0], &[f64::NAN, 1.0, f64::INFINITY]).unwrap();
        for pair in [speeds, directions] {
            assert!(pair[0].is_nan() && pair[1].is_nan() && pair[2].is_nan());
        }
        assert!(speed_direction_core(&[1.0], &[1.0, 2.0]).is_err());
    }

    #[test]
    fn strictly_stacked_keeps_the_longest_ascending_run() {
        let mask =
            strictly_stacked_mask_core(&[1000.0, 850.0, 700.0], &[10.0, 1500.0, 3000.0]).unwrap();
        assert_eq!(mask, vec![true, true, true]);
        // A non-rising height drops the level without updating the run.
        let mask = strictly_stacked_mask_core(&[1000.0, 900.0, 800.0], &[10.0, 5.0, 20.0]).unwrap();
        assert_eq!(mask, vec![true, false, true]);
        // Equal pressure is not strictly falling.
        let mask =
            strictly_stacked_mask_core(&[1000.0, 1000.0, 900.0], &[10.0, 20.0, 30.0]).unwrap();
        assert_eq!(mask, vec![true, false, true]);
    }

    #[test]
    fn strictly_stacked_treats_nan_comparisons_as_false() {
        // NaN mid-column is dropped and does not update the reference.
        let mask = strictly_stacked_mask_core(&[1000.0, f64::NAN, 850.0], &[10.0, 5000.0, 3000.0])
            .unwrap();
        assert_eq!(mask, vec![true, false, true]);
        // A leading NaN poisons every later comparison, like Python's.
        let mask =
            strictly_stacked_mask_core(&[f64::NAN, 900.0, 800.0], &[10.0, 1000.0, 2000.0]).unwrap();
        assert_eq!(mask, vec![true, false, false]);
        // So does a NaN height stored as the reference.
        let mask = strictly_stacked_mask_core(&[1000.0, 900.0, 800.0], &[f64::NAN, 1000.0, 2000.0])
            .unwrap();
        assert_eq!(mask, vec![true, false, false]);
    }

    #[test]
    fn strictly_stacked_rejects_a_short_height_column() {
        let error = strictly_stacked_mask_core(&[1000.0, 900.0, 800.0], &[10.0]).unwrap_err();
        assert_eq!(error, "index 1 is out of bounds for axis 0 with size 1");
        // Extra height entries are simply unused, as in Python.
        let mask = strictly_stacked_mask_core(&[1000.0], &[10.0, 99.0, 42.0]).unwrap();
        assert_eq!(mask, vec![true]);
    }

    #[test]
    fn circular_centre_wraps_longitude_at_the_antimeridian() {
        let (lat, lon) = circular_centre_core(&[10.0, 12.0], &[179.0, -179.0]).unwrap();
        assert!((lat - 11.0).abs() < 1e-12);
        assert!(
            (lon.abs() - 180.0).abs() < 1e-9,
            "the box stays at the antimeridian: {lon}"
        );
        let (lat, lon) = circular_centre_core(&[36.0, 36.2], &[-96.0, -96.0]).unwrap();
        assert!((lat - 36.1).abs() < 1e-12);
        assert!((lon + 96.0).abs() < 1e-12);
        // A box split across the Greenwich meridian stays there too.
        let (_lat, lon) = circular_centre_core(&[0.0, 0.0], &[-1.0, 1.0]).unwrap();
        assert!(lon.abs() < 1e-9);
    }

    #[test]
    fn circular_centre_without_coordinates_is_an_error() {
        let error = circular_centre_core(&[], &[1.0]).unwrap_err();
        assert_eq!(
            error,
            "these soundings carry no coordinates, so the centre of the \
             averaged area is unknown; pass lat and lon"
        );
        assert!(circular_centre_core(&[36.0], &[]).is_err());
    }
}

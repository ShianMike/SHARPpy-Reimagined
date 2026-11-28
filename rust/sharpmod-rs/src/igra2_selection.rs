//! Native ports of the station-resolution and archive-selection rules of
//! ``sharpmod.io.igra2_decoder`` and the wind-gradient surface-vorticity
//! helpers of ``sharpmod.tools.era5_columns``.
//!
//! Python keeps owning the network, caches, and dataset objects; these
//! bindings move only the pure decision logic, so station queries no longer
//! walk the catalogue twice and the neighbour differencing window no longer
//! re-runs its bounds checks per component.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Standard gravitational acceleration, m/s^2 (``era5_extract.G0``).
const G0: f64 = 9.80665;

/// Surface scalars (pres, hght, tmpc, dwpc) after unit normalisation.
pub(crate) type SurfaceScalars = (Option<f64>, Option<f64>, Option<f64>, Option<f64>);

fn value_error(message: String) -> PyErr {
    PyValueError::new_err(message)
}

/// Quote a string the way Python's ``repr`` does for the quoting cases a
/// station query can produce.
fn py_repr_str(text: &str) -> String {
    if !text.contains('\'') {
        format!("'{text}'")
    } else if !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

// ---------------------------------------------------------------------------
// Station resolution
// ---------------------------------------------------------------------------

/// Port of ``IGRA_Decoder.resolve_station`` against parallel identifier/name
/// columns: the full IGRA id first, then a bare WMO number zero-padded to
/// five digits, then a 3-4 character ICAO callsign, then a name substring,
/// all case-folding like the Python walk.
///
/// A single match resolves; multiple matches raise the ambiguous-station
/// error; a duplicated exact id falls through to the later stages exactly
/// like Python's ``len(exact) == 1`` check.  A Unicode-digit query can never
/// equal an ASCII identifier column, so gating the WMO stage on ASCII digits
/// cannot drift from Python's wider ``str.isdigit``.
pub(crate) fn resolve_station_index_core(
    query: &str,
    station_ids: &[&str],
    wmo_ids: &[&str],
    icao_ids: &[&str],
    names: &[&str],
) -> Result<usize, String> {
    for (column, length) in [
        ("wmo_ids", wmo_ids.len()),
        ("icao_ids", icao_ids.len()),
        ("names", names.len()),
    ] {
        if length != station_ids.len() {
            return Err(format!(
                "station_ids and {column} must have the same length ({} != {length})",
                station_ids.len()
            ));
        }
    }
    let requested = query.trim();
    if requested.is_empty() {
        return Err("no station query given".to_string());
    }
    let normalized = requested.to_uppercase();
    let count = station_ids.len();

    let exact: Vec<usize> = (0..count)
        .filter(|&index| station_ids[index] == normalized)
        .collect();
    if exact.len() == 1 {
        return Ok(exact[0]);
    }

    if !normalized.is_empty() && normalized.chars().all(|c| c.is_ascii_digit()) {
        // ``str.zfill(5)`` of a digit string is exactly the 0-padded form.
        let padded = format!("{normalized:0>5}");
        let by_wmo: Vec<usize> = (0..count)
            .filter(|&index| wmo_ids[index] == padded)
            .collect();
        if by_wmo.len() == 1 {
            return Ok(by_wmo[0]);
        }
        if by_wmo.len() > 1 {
            return Err(ambiguous_station_message(
                requested,
                &by_wmo,
                station_ids,
                names,
            ));
        }
    }

    if (3..=4).contains(&normalized.chars().count())
        && normalized.chars().all(char::is_alphanumeric)
    {
        // ``normalized[-4:]`` of a 3-4 character string is the string itself.
        let by_icao: Vec<usize> = (0..count)
            .filter(|&index| icao_ids[index] == normalized)
            .collect();
        if by_icao.len() == 1 {
            return Ok(by_icao[0]);
        }
        if by_icao.len() > 1 {
            return Err(ambiguous_station_message(
                requested,
                &by_icao,
                station_ids,
                names,
            ));
        }
    }

    let folded = requested.to_lowercase();
    let by_name: Vec<usize> = (0..count)
        .filter(|&index| names[index].to_lowercase().contains(&folded))
        .collect();
    if by_name.len() == 1 {
        return Ok(by_name[0]);
    }
    if by_name.len() > 1 {
        return Err(ambiguous_station_message(
            requested,
            &by_name,
            station_ids,
            names,
        ));
    }
    Err(format!(
        "station query {} matched no IGRA station",
        py_repr_str(requested)
    ))
}

/// Port of ``IGRA_Decoder._ambiguous``: list up to eight matches sorted by
/// station id, then summarise whatever remains.
fn ambiguous_station_message(
    requested: &str,
    matches: &[usize],
    station_ids: &[&str],
    names: &[&str],
) -> String {
    let mut ordered: Vec<usize> = matches.to_vec();
    ordered.sort_by(|&left, &right| station_ids[left].cmp(station_ids[right]));
    let listed = ordered
        .iter()
        .take(8)
        .map(|&index| format!("{} ({})", station_ids[index], names[index]))
        .collect::<Vec<_>>()
        .join(", ");
    let suffix = if matches.len() <= 8 {
        String::new()
    } else {
        format!(", and {} more", matches.len() - 8)
    };
    format!(
        "station query {} matched {} IGRA stations: {listed}{suffix}",
        py_repr_str(requested),
        matches.len()
    )
}

#[pyfunction]
fn igra2_resolve_station_index(
    query: &str,
    station_ids: Vec<String>,
    wmo_ids: Vec<String>,
    icao_ids: Vec<String>,
    names: Vec<String>,
) -> PyResult<usize> {
    let station_ids: Vec<&str> = station_ids.iter().map(String::as_str).collect();
    let wmo_ids: Vec<&str> = wmo_ids.iter().map(String::as_str).collect();
    let icao_ids: Vec<&str> = icao_ids.iter().map(String::as_str).collect();
    let names: Vec<&str> = names.iter().map(String::as_str).collect();
    resolve_station_index_core(query, &station_ids, &wmo_ids, &icao_ids, &names)
        .map_err(value_error)
}

// ---------------------------------------------------------------------------
// Archive selection
// ---------------------------------------------------------------------------

/// Port of the nearest-header loop in ``IGRA_Decoder.fetch``: return the
/// header closest to the requested epoch inside ``tolerance_seconds``.
///
/// ``None`` epochs stand for headers without a nominal valid time and never
/// match; non-finite epochs (and therefore a non-finite request time, whose
/// distance is never a number) are skipped the same way, and an exact hit
/// short-circuits the scan.  Only a strictly smaller distance replaces the
/// current leader, so ties keep the first header in file order, exactly like
/// the Python loop.
pub(crate) fn select_nearest_header_index_core(
    header_epochs: &[Option<f64>],
    target_epoch: f64,
    tolerance_seconds: f64,
) -> Option<(usize, f64)> {
    let mut best: Option<(usize, f64)> = None;
    for (index, epoch) in header_epochs.iter().enumerate() {
        let Some(epoch) = epoch else { continue };
        if !epoch.is_finite() {
            continue;
        }
        let delta = (epoch - target_epoch).abs();
        if !delta.is_finite() || delta > tolerance_seconds {
            continue;
        }
        if best.is_none_or(|(_, best_delta)| delta < best_delta) {
            best = Some((index, delta));
            if delta == 0.0 {
                break;
            }
        }
    }
    best
}

#[pyfunction]
fn igra2_select_nearest_header_index(
    header_epochs: Vec<Option<f64>>,
    target_epoch: f64,
    tolerance_seconds: f64,
) -> Option<(usize, f64)> {
    select_nearest_header_index_core(&header_epochs, target_epoch, tolerance_seconds)
}

/// Port of the candidate-year walk in ``IGRA_Decoder._y2d_url``: probe the
/// remembered year first, then the current and previous calendar years,
/// dropping the falsy ``0`` sentinel and duplicates.
pub(crate) fn y2d_candidate_years_core(remembered: Option<i64>, current_year: i64) -> Vec<i64> {
    let mut candidates: Vec<i64> = Vec::new();
    for year in [remembered, Some(current_year), Some(current_year - 1)]
        .into_iter()
        .flatten()
    {
        if year != 0 && !candidates.contains(&year) {
            candidates.push(year);
        }
    }
    candidates
}

#[pyfunction]
#[pyo3(signature = (remembered, current_year))]
fn igra2_y2d_candidate_years(remembered: Option<i64>, current_year: i64) -> Vec<i64> {
    y2d_candidate_years_core(remembered, current_year)
}

// ---------------------------------------------------------------------------
// Wind-grid surface vorticity
// ---------------------------------------------------------------------------

/// Port of the finite-difference neighbour window in
/// ``era5_columns._surface_relative_vorticity_from_wind_grid``: both wind
/// components must share one rectangular shape, the local neighbour indices
/// must fall inside that window, and the metre-scale spacings must be finite
/// with magnitude at least one metre.  The returned value is
/// ``dv/dx - du/dy`` or ``None`` when it is not finite.
#[allow(clippy::too_many_arguments)]
pub(crate) fn grid_surface_relative_vorticity_core(
    u_window: &[Vec<f64>],
    v_window: &[Vec<f64>],
    dx_m: f64,
    dy_m: f64,
    local_y: usize,
    local_x: usize,
    local_x0: usize,
    local_x1: usize,
    local_y0: usize,
    local_y1: usize,
) -> Option<f64> {
    let height = u_window.len();
    let width = u_window.first().map_or(0, Vec::len);
    if v_window.len() != height
        || v_window.iter().any(|row| row.len() != width)
        || u_window.iter().any(|row| row.len() != width)
    {
        return None;
    }
    if local_y >= height
        || local_y0 >= height
        || local_y1 >= height
        || local_x >= width
        || local_x0 >= width
        || local_x1 >= width
    {
        return None;
    }
    if !dx_m.is_finite() || !dy_m.is_finite() || dx_m.abs() < 1.0 || dy_m.abs() < 1.0 {
        return None;
    }
    let dvdx = (v_window[local_y][local_x1] - v_window[local_y][local_x0]) / dx_m;
    let dudy = (u_window[local_y1][local_x] - u_window[local_y0][local_x]) / dy_m;
    let value = dvdx - dudy;
    if value.is_finite() {
        Some(value)
    } else {
        None
    }
}

/// Port of the bottom-most level choice in
/// ``era5_columns._surface_relative_vorticity_from_wind_grid``: keep finite
/// levels, drop levels above a known surface pressure, and take the highest
/// remaining level with ``np.nanargmax`` first-occurrence tie-breaking.
pub(crate) fn select_surface_level_index_core(
    levels: &[f64],
    surface_pressure: Option<f64>,
) -> Option<usize> {
    let mut best: Option<(usize, f64)> = None;
    for (index, &level) in levels.iter().enumerate() {
        if !level.is_finite() {
            continue;
        }
        if let Some(pressure) = surface_pressure {
            if pressure.is_nan() || level > pressure {
                // ``levels <= NaN`` is false for every level, so a NaN
                // surface pressure leaves nothing eligible.
                continue;
            }
        }
        if best.is_none_or(|(_, best_level)| level > best_level) {
            best = Some((index, level));
        }
    }
    best.map(|(index, _)| index)
}

/// Port of the neighbour surface-pressure gate in
/// ``era5_columns._surface_relative_vorticity_from_wind_grid``: every
/// pressure must be finite and positive, values above 2000 are pascals and
/// convert to hPa, and the minimum feeds the wind-column merge.
pub(crate) fn normalize_surface_pressures_hpa_core(pressures: &[f64]) -> Option<f64> {
    if pressures.is_empty() {
        return None;
    }
    let mut minimum: Option<f64> = None;
    for &pressure in pressures {
        if !pressure.is_finite() || pressure <= 0.0 {
            return None;
        }
        let converted = if pressure > 2000.0 {
            pressure / 100.0
        } else {
            pressure
        };
        minimum = Some(converted.min(minimum.unwrap_or(converted)));
    }
    minimum
}

/// Port of the surface unit conversions in ``era5_columns._build_columns``:
/// pressures above 2000 become hPa, geopotential divides by ``G0``, and
/// temperatures above 150 are Kelvin and convert to Celsius.  ``None`` and
/// NaN pass through untouched, like the Python ``is not None`` guards.
pub(crate) fn normalize_surface_scalars_core(
    pres: Option<f64>,
    hght: Option<f64>,
    hght_is_geopotential: bool,
    tmpc: Option<f64>,
    dwpc: Option<f64>,
) -> SurfaceScalars {
    (
        pres.map(|value| if value > 2000.0 { value / 100.0 } else { value }),
        hght.map(|value| {
            if hght_is_geopotential {
                value / G0
            } else {
                value
            }
        }),
        tmpc.map(|value| if value > 150.0 { value - 273.15 } else { value }),
        dwpc.map(|value| if value > 150.0 { value - 273.15 } else { value }),
    )
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn era5_grid_surface_relative_vorticity(
    u_window: Vec<Vec<f64>>,
    v_window: Vec<Vec<f64>>,
    dx_m: f64,
    dy_m: f64,
    local_y: usize,
    local_x: usize,
    local_x0: usize,
    local_x1: usize,
    local_y0: usize,
    local_y1: usize,
) -> Option<f64> {
    grid_surface_relative_vorticity_core(
        &u_window, &v_window, dx_m, dy_m, local_y, local_x, local_x0, local_x1, local_y0, local_y1,
    )
}

#[pyfunction]
#[pyo3(signature = (levels, surface_pressure))]
fn era5_select_surface_level_index(
    levels: Vec<f64>,
    surface_pressure: Option<f64>,
) -> Option<usize> {
    select_surface_level_index_core(&levels, surface_pressure)
}

#[pyfunction]
fn era5_normalize_surface_pressures_hpa(pressures: Vec<f64>) -> Option<f64> {
    normalize_surface_pressures_hpa_core(&pressures)
}

#[pyfunction]
#[pyo3(signature = (pres, hght, hght_is_geopotential, tmpc, dwpc))]
fn era5_normalize_surface_scalars(
    pres: Option<f64>,
    hght: Option<f64>,
    hght_is_geopotential: bool,
    tmpc: Option<f64>,
    dwpc: Option<f64>,
) -> SurfaceScalars {
    normalize_surface_scalars_core(pres, hght, hght_is_geopotential, tmpc, dwpc)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(igra2_resolve_station_index, module)?)?;
    module.add_function(wrap_pyfunction!(igra2_select_nearest_header_index, module)?)?;
    module.add_function(wrap_pyfunction!(igra2_y2d_candidate_years, module)?)?;
    module.add_function(wrap_pyfunction!(
        era5_grid_surface_relative_vorticity,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(era5_select_surface_level_index, module)?)?;
    module.add_function(wrap_pyfunction!(
        era5_normalize_surface_pressures_hpa,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(era5_normalize_surface_scalars, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // Verbatim station rows from test_igra2_reader.py's REAL_STATION_LINES,
    // with absent WMO/ICAO ids carried as empty column entries (the Python
    // ``None`` analog that never equals a non-empty query form).
    const IDS: [&str; 4] = ["ACM00078861", "AEM00041217", "AEXUAE05467", "AFM00040911"];
    const WMO: [&str; 4] = ["78861", "41217", "", "40911"];
    const ICAO: [&str; 4] = ["", "", "", ""];
    const NAMES: [&str; 4] = [
        "COOLIDGE FIELD (UA)",
        "ABU DHABI INTERNATIONAL AIRPOR",
        "SHARJAH",
        "MAZAR-I-SHARIF",
    ];

    fn resolve(query: &str) -> Result<usize, String> {
        resolve_station_index_core(query, &IDS, &WMO, &ICAO, &NAMES)
    }

    #[test]
    fn station_resolution_exact_id_is_case_insensitive() {
        assert_eq!(resolve("acm00078861"), Ok(0));
        assert_eq!(resolve("ACM00078861"), Ok(0));
        // Surrounding whitespace trims away like Python's str.strip().
        assert_eq!(resolve("  AEXUAE05467 \n"), Ok(2));
    }

    #[test]
    fn station_resolution_wmo_number_zero_pads() {
        // The bare WMO bridge resolves through the network code.
        assert_eq!(resolve("41217"), Ok(1));
        // Queries shorter than five digits zero-fill before the compare.
        let wmo = ["78861", "41217", "00123", "40911"];
        assert_eq!(
            resolve_station_index_core("123", &IDS, &wmo, &ICAO, &NAMES),
            Ok(2)
        );
    }

    #[test]
    fn station_resolution_icao_tail_matches_three_or_four_chars() {
        // Four-character callsigns resolve through the tail stage.
        let icao = ["", "", "", "KOUN"];
        assert_eq!(
            resolve_station_index_core("koun", &IDS, &WMO, &icao, &NAMES),
            Ok(3)
        );
        // Three-character queries use the string itself as the tail.
        let icao = ["OUN", "", "", ""];
        assert_eq!(
            resolve_station_index_core("OUN", &IDS, &WMO, &icao, &NAMES),
            Ok(0)
        );
    }

    #[test]
    fn station_resolution_name_substring_is_case_insensitive() {
        assert_eq!(resolve("sharjah"), Ok(2));
        assert_eq!(resolve("  Abu DhAbi "), Ok(1));
    }

    #[test]
    fn station_resolution_empty_and_unknown_queries_use_exact_messages() {
        assert_eq!(resolve(""), Err("no station query given".to_string()));
        assert_eq!(resolve("   \t"), Err("no station query given".to_string()));
        assert_eq!(
            resolve("nowhere at all"),
            Err("station query 'nowhere at all' matched no IGRA station".to_string())
        );
    }

    #[test]
    fn station_resolution_ambiguous_messages_sort_and_cap_candidates() {
        let ids = ["USM00072358", "USM00072357"];
        let names = ["NORMAN NORTH", "NORMAN SOUTH"];
        // Stations are listed by id, not input order, in "{id} ({name})" form.
        assert_eq!(
            resolve_station_index_core("norman", &ids, &["", ""], &["", ""], &names),
            Err("station query 'norman' matched 2 IGRA stations: \
                 USM00072357 (NORMAN SOUTH), USM00072358 (NORMAN NORTH)"
                .to_string())
        );
        // Ambiguous WMO numbers raise the same shaped error.
        assert_eq!(
            resolve_station_index_core("72357", &ids, &["72357", "72357"], &["", ""], &names)
                .unwrap_err(),
            "station query '72357' matched 2 IGRA stations: \
             USM00072357 (NORMAN SOUTH), USM00072358 (NORMAN NORTH)"
        );
    }

    #[test]
    fn station_resolution_lists_eight_then_counts_the_rest() {
        let owned: Vec<String> = (0..9).map(|index| format!("AAA00000000{index}")).collect();
        let ids: Vec<&str> = owned.iter().map(String::as_str).collect();
        let names: Vec<String> = (0..9).map(|index| format!("NORMAN {index}")).collect();
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let empty = ["", "", "", "", "", "", "", "", ""];
        let message =
            resolve_station_index_core("norman", &ids, &empty, &empty, &names).unwrap_err();
        assert!(message.contains("matched 9 IGRA stations"));
        assert!(message.ends_with(", and 1 more"));
        // The listed candidates are the first eight by id, not input order.
        assert!(message.contains("AAA000000000 (NORMAN 0)"));
        assert!(message.contains("AAA000000007 (NORMAN 7)"));
        assert!(!message.contains("AAA000000008"));
    }

    #[test]
    fn station_resolution_duplicate_exact_ids_fall_through() {
        // Two exact hits do not resolve; Python moves on to the later stages,
        // where the id query matches no name and reports no station.
        let ids = ["USM00072357", "USM00072357"];
        assert_eq!(
            resolve_station_index_core(
                "USM00072357",
                &ids,
                &["", ""],
                &["", ""],
                &["NORMAN NORTH", "NORMAN SOUTH"]
            ),
            Err("station query 'USM00072357' matched no IGRA station".to_string())
        );
    }

    #[test]
    fn station_resolution_rejects_mismatched_column_lengths() {
        assert_eq!(
            resolve_station_index_core("ACM00078861", &IDS, &["78861"], &ICAO, &NAMES),
            Err("station_ids and wmo_ids must have the same length (4 != 1)".to_string())
        );
    }

    #[test]
    fn nearest_header_prefers_exact_and_skips_far_headers() {
        let target = 1_800_000_000.0;
        let six_hours = target - 21_600.0;
        // A special release one hour out answers an 18Z-style request, while
        // the six-hour-distant header and the header without a valid time are
        // never considered.
        let epochs = [Some(six_hours), None, Some(target + 3_600.0)];
        assert_eq!(
            select_nearest_header_index_core(&epochs, target, 10_800.0),
            Some((2, 3_600.0))
        );
        // Everything beyond the tolerance window leaves no match.
        let too_far = [Some(six_hours), None];
        assert_eq!(
            select_nearest_header_index_core(&too_far, target, 10_800.0),
            None
        );
        // An exact hit short-circuits the scan.
        let exact = [Some(target - 600.0), Some(target), Some(target)];
        assert_eq!(
            select_nearest_header_index_core(&exact, target, 10_800.0),
            Some((1, 0.0))
        );
    }

    #[test]
    fn nearest_header_keeps_first_tie_and_tracks_closer_later() {
        let target = 0.0;
        // An hour early and an hour late tie; the strict "<" keeps the first.
        let tied = [Some(-3_600.0), Some(3_600.0)];
        assert_eq!(
            select_nearest_header_index_core(&tied, target, 10_800.0),
            Some((0, 3_600.0))
        );
        // A strictly closer candidate later in the file replaces the leader.
        let closer = [Some(-3_600.0), Some(-600.0)];
        assert_eq!(
            select_nearest_header_index_core(&closer, target, 10_800.0),
            Some((1, 600.0))
        );
    }

    #[test]
    fn nearest_header_ignores_non_finite_epochs_and_targets() {
        let epochs = [Some(f64::NAN), Some(f64::INFINITY), Some(5.0)];
        // Non-finite header epochs never answer; the finite one matches.
        assert_eq!(
            select_nearest_header_index_core(&epochs, 5.0, 10.0),
            Some((2, 0.0))
        );
        // A non-finite request time has no distance at all.
        assert_eq!(
            select_nearest_header_index_core(&epochs, f64::NAN, 10.0),
            None
        );
    }

    #[test]
    fn y2d_candidate_years_dedupe_and_drop_the_zero_sentinel() {
        assert_eq!(y2d_candidate_years_core(None, 2026), [2026, 2025]);
        // The falsy 0 remembered year is skipped, not appended.
        assert_eq!(y2d_candidate_years_core(Some(0), 2026), [2026, 2025]);
        assert_eq!(y2d_candidate_years_core(Some(2026), 2026), [2026, 2025]);
        // The remembered year is probed first, even when it is current-1.
        assert_eq!(y2d_candidate_years_core(Some(2025), 2026), [2025, 2026]);
    }

    #[test]
    fn wind_grid_vorticity_matches_the_finite_difference() {
        let zeros: Vec<Vec<f64>> = vec![vec![0.0; 3]; 3];
        let mut u_window = zeros.clone();
        u_window[0][1] = 1.0;
        u_window[2][1] = 3.0;
        let mut v_window = zeros.clone();
        v_window[1][2] = 5.0;
        let value = grid_surface_relative_vorticity_core(
            &u_window, &v_window, 100_000.0, 100_000.0, 1, 1, 0, 2, 0, 2,
        )
        .unwrap();
        // dv/dx contributes 5e-5 and du/dy subtracts 2e-5.
        assert!((value - 3.0e-5).abs() < 1.0e-12);
    }

    #[test]
    fn wind_grid_vorticity_rejects_broken_windows() {
        let zeros: Vec<Vec<f64>> = vec![vec![0.0; 3]; 3];
        let mut v_window = zeros.clone();
        v_window[1][2] = 5.0;
        // The canonical 3x3 centre difference succeeds.
        let value = grid_surface_relative_vorticity_core(
            &zeros, &v_window, 100_000.0, 100_000.0, 1, 1, 0, 2, 0, 2,
        )
        .unwrap();
        assert!((value - 5.0e-5).abs() < 1.0e-12);
        // Mismatched window heights are not equal 2-D shapes.
        assert_eq!(
            grid_surface_relative_vorticity_core(
                &zeros[..2],
                &v_window,
                100_000.0,
                100_000.0,
                1,
                1,
                0,
                2,
                0,
                2
            ),
            None
        );
        // A ragged row is not a 2-D array.
        let ragged = vec![vec![0.0; 3], vec![0.0; 2], vec![0.0; 3]];
        assert_eq!(
            grid_surface_relative_vorticity_core(
                &ragged, &v_window, 100_000.0, 100_000.0, 1, 1, 0, 2, 0, 2
            ),
            None
        );
        // Spacings below one metre or non-finite are refused.
        assert_eq!(
            grid_surface_relative_vorticity_core(
                &zeros, &v_window, 0.5, 100_000.0, 1, 1, 0, 2, 0, 2
            ),
            None
        );
        assert_eq!(
            grid_surface_relative_vorticity_core(
                &zeros,
                &v_window,
                100_000.0,
                f64::NAN,
                1,
                1,
                0,
                2,
                0,
                2
            ),
            None
        );
        // Out-of-bounds local neighbour indices cannot address the window.
        assert_eq!(
            grid_surface_relative_vorticity_core(
                &zeros, &v_window, 100_000.0, 100_000.0, 3, 1, 0, 2, 0, 2
            ),
            None
        );
        // A NaN wind cell poisons the difference result.
        let mut v_nan = v_window.clone();
        v_nan[1][0] = f64::NAN;
        assert_eq!(
            grid_surface_relative_vorticity_core(
                &zeros, &v_nan, 100_000.0, 100_000.0, 1, 1, 0, 2, 0, 2
            ),
            None
        );
    }

    #[test]
    fn surface_level_index_matches_nanargmax_rules() {
        // Highest finite level wins; the first occurrence wins on a tie.
        assert_eq!(
            select_surface_level_index_core(&[900.0, 950.0, 950.0, 850.0], None),
            Some(1)
        );
        // NaN levels never become eligible.
        assert_eq!(
            select_surface_level_index_core(&[900.0, f64::NAN, 1000.0], None),
            Some(2)
        );
        // A known surface pressure keeps only levels <= pressure.
        assert_eq!(
            select_surface_level_index_core(&[1000.0, 950.0, 900.0], Some(960.0)),
            Some(1)
        );
        // Nothing eligible: above the surface, non-finite, or empty input.
        assert_eq!(
            select_surface_level_index_core(&[1000.0], Some(950.0)),
            None
        );
        assert_eq!(select_surface_level_index_core(&[f64::NAN], None), None);
        assert_eq!(select_surface_level_index_core(&[], None), None);
        // A NaN surface pressure makes nothing eligible, like NumPy compares.
        assert_eq!(
            select_surface_level_index_core(&[900.0, 950.0], Some(f64::NAN)),
            None
        );
    }

    #[test]
    fn surface_pressure_normalisation_converts_and_takes_the_minimum() {
        // hPa-scale values pass through untouched.
        assert_eq!(
            normalize_surface_pressures_hpa_core(&[1013.25, 998.0]),
            Some(998.0)
        );
        // Pascal-scale values convert before the minimum.
        assert_eq!(
            normalize_surface_pressures_hpa_core(&[101_325.0, 95_000.0]),
            Some(950.0)
        );
        // Non-positive or non-finite pressures and empty input never answer.
        assert_eq!(normalize_surface_pressures_hpa_core(&[0.0]), None);
        assert_eq!(normalize_surface_pressures_hpa_core(&[-1.0]), None);
        assert_eq!(normalize_surface_pressures_hpa_core(&[f64::NAN]), None);
        assert_eq!(normalize_surface_pressures_hpa_core(&[f64::INFINITY]), None);
        assert_eq!(normalize_surface_pressures_hpa_core(&[]), None);
    }

    #[test]
    fn surface_scalars_normalise_units_like_build_columns() {
        let converted = normalize_surface_scalars_core(
            Some(101_325.0),
            Some(9_806.65),
            true,
            Some(290.65),
            Some(283.15),
        );
        assert!((converted.0.unwrap() - 1_013.25).abs() < 1.0e-9);
        assert!((converted.1.unwrap() - 1_000.0).abs() < 1.0e-9);
        assert!((converted.2.unwrap() - 17.5).abs() < 1.0e-9);
        assert!((converted.3.unwrap() - 10.0).abs() < 1.0e-9);
        // Already-Celsius values sit still, the 150.0 boundary is a strict
        // "greater than", and a plain height is not divided by G0.
        let passthrough = normalize_surface_scalars_core(
            Some(1_013.25),
            Some(381.0),
            false,
            Some(150.0),
            Some(20.0),
        );
        assert_eq!(
            passthrough,
            (Some(1_013.25), Some(381.0), Some(150.0), Some(20.0))
        );
        // None passes through and NaN temperatures stay NaN.
        let untouched =
            normalize_surface_scalars_core(Some(f64::NAN), None, true, Some(f64::NAN), None);
        assert!(untouched.0.unwrap().is_nan());
        assert_eq!(untouched.1, None);
        assert!(untouched.2.unwrap().is_nan());
        assert_eq!(untouched.3, None);
    }
}

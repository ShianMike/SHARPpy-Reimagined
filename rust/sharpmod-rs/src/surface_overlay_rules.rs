//! Native surface-observation station-plot rules (milestone M37).
//!
//! Rust ports of the pure presentation half of
//! ``sharpmod.providers.surface_observations``:
//!
//! * the truthful station-plot wind state (``SurfaceObservation.wind_state``,
//!   [`surface_wind_state`]) and the ``(u, v)`` barb components it gates
//!   (``SurfaceObservation.wind_uv_kt``, [`surface_wind_uv_kt`]);
//! * the screen-space declutter grid shared by ``declutter_observations``
//!   and the status-station pass of ``surface_overlay_layer``
//!   ([`surface_declutter_indices`], with call-supplied occupied pixels so
//!   one grid serves both passes);
//! * the 13-point station hit ring (``surface_overlay_layer``'s ``ring``,
//!   [`surface_station_ring`]) and its geographic radii
//!   ([`surface_ring_radii`]);
//! * the station description text ([`surface_format_observation_text`]) and
//!   the layer subtitle ([`surface_format_subtitle`]);
//! * the atomic CSV export behind ``export_surface_csv``
//!   ([`surface_write_csv`]).
//!
//! Missing or non-finite readings stay missing: a calm circle is a real
//! measured zero, never a coerced ``None``, and a non-calm speed without a
//! direction stays a distinct partial value.  The description text matches
//! the Python source byte for byte -- em dash, degree sign, banker's
//! rounding through [`f64::round_ties_even`], and the signed minute offset
//! of ``f"{offset:+.0f}"`` (including ``+0`` and ``-0``).

use std::fs::{self, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};

use pyo3::prelude::*;
use ring::rand::{SecureRandom, SystemRandom};

/// One ring closes with the sample at the full turn: 13 points over 12
/// sectors (``surface_overlay_layer``'s ``ring`` loop).
const RING_POINTS: usize = 13;

/// The declutter grid's smallest usable cell (``declutter_observations``'s
/// ``max(10, int(minimum_spacing_px))``).
const MIN_CELL_PX: i64 = 10;

// ---------------------------------------------------------------------------
// Wind state and barb components (SurfaceObservation)
// ---------------------------------------------------------------------------

/// The pure half of ``SurfaceObservation.wind_state``: a missing or
/// non-finite speed is ``"missing"``, a reported magnitude below 0.5 kt is
/// ``"calm"`` (a real measured zero, even without a direction), a non-calm
/// speed without a direction is ``"direction-missing"``, and only a
/// finite speed with a direction is ``"available"``.
pub(crate) fn wind_state_core(
    wind_speed_kt: Option<f64>,
    wind_direction_deg: Option<f64>,
) -> &'static str {
    let Some(speed) = wind_speed_kt.filter(|speed| speed.is_finite()) else {
        return "missing";
    };
    if speed.abs() < 0.5 {
        return "calm";
    }
    match wind_direction_deg {
        Some(direction) if direction.is_finite() => "available",
        _ => "direction-missing",
    }
}

/// The pure half of ``SurfaceObservation.wind_uv_kt``: calm is exactly
/// ``(0.0, 0.0)``, anything else missing a component is ``(None, None)``,
/// and a full observation resolves to the meteorological ``(u, v)`` pair
/// ``(-speed * sin(radians(dir)), -speed * cos(radians(dir)))``.
pub(crate) fn wind_uv_core(
    wind_speed_kt: Option<f64>,
    wind_direction_deg: Option<f64>,
) -> (Option<f64>, Option<f64>) {
    if wind_state_core(wind_speed_kt, wind_direction_deg) == "calm" {
        return (Some(0.0), Some(0.0));
    }
    match (wind_speed_kt, wind_direction_deg) {
        (Some(speed), Some(direction)) if speed.is_finite() && direction.is_finite() => {
            let angle = direction.to_radians();
            (Some(-speed * angle.sin()), Some(-speed * angle.cos()))
        }
        _ => (None, None),
    }
}

/// The truthful station-plot wind state for one observation.
///
/// ``wind_speed_kt``/``wind_direction_deg`` cross the boundary as plain
/// floats; missing values are ``None`` and non-finite values are treated as
/// missing, exactly like the ``_finite`` filter the Python objects apply at
/// construction.  The result is one of ``"missing"``, ``"calm"``,
/// ``"direction-missing"``, or ``"available"``.
#[pyfunction]
fn surface_wind_state(wind_speed_kt: Option<f64>, wind_direction_deg: Option<f64>) -> &'static str {
    wind_state_core(wind_speed_kt, wind_direction_deg)
}

/// The ``(u, v)`` barb components for one observation, in knots.
///
/// A calm observation (a measured magnitude below 0.5 kt) is exactly
/// ``(0.0, 0.0)``; an observation missing either component -- including the
/// direction-missing partial -- is ``(None, None)``; otherwise the pair is
/// ``(-speed * sin(radians(dir)), -speed * cos(radians(dir)))``.
#[pyfunction]
fn surface_wind_uv_kt(
    wind_speed_kt: Option<f64>,
    wind_direction_deg: Option<f64>,
) -> (Option<f64>, Option<f64>) {
    wind_uv_core(wind_speed_kt, wind_direction_deg)
}

// ---------------------------------------------------------------------------
// Screen-space decluttering (declutter_observations / surface_overlay_layer)
// ---------------------------------------------------------------------------

/// The pure half of ``declutter_observations``, generalized over the
/// status-station pass of ``surface_overlay_layer``.
///
/// ``coords`` are ``(lon, lat)`` pairs.  ``bounds`` is the view's
/// ``(lon0, lon1, lat0, lat1)``; ``pixel_size`` the viewport in pixels;
/// ``min_spacing_px`` the desired spacing (floored at 10); ``max_stations``
/// the keep limit (floored at 1, exactly like ``max(1, int(max_stations))``);
/// ``occupied_px`` the screen points already claimed by earlier passes.
///
/// Each in-bounds station is projected to screen pixels, skipped when any
/// occupied point lies within ``cell`` pixels, and otherwise kept -- until
/// ``limit`` stations survive.  The status pass of ``surface_overlay_layer``
/// stops when ``shown + kept >= limit``, which its caller expresses by
/// passing ``max_stations = limit - shown``: a non-positive value keeps at
/// most one station, matching the Python loop's append-then-break order.
///
/// Returns the kept coordinate indices in input order and the final
/// occupied-pixel list (seeded points first, kept points after).
pub(crate) fn declutter_indices_core(
    coords: &[(f64, f64)],
    bounds: (f64, f64, f64, f64),
    pixel_size: (i64, i64),
    min_spacing_px: i64,
    max_stations: i64,
    occupied_px: &[(f64, f64)],
) -> (Vec<usize>, Vec<(f64, f64)>) {
    let (lon0, lon1, lat0, lat1) = bounds;
    let width = pixel_size.0.max(1) as f64;
    let height = pixel_size.1.max(1) as f64;
    let cell = min_spacing_px.max(MIN_CELL_PX) as f64;
    let limit = max_stations.max(1) as usize;
    let mut occupied = occupied_px.to_vec();
    let mut kept: Vec<usize> = Vec::new();
    for (index, &(lon, lat)) in coords.iter().enumerate() {
        if !(lon0 <= lon && lon <= lon1 && lat0 <= lat && lat <= lat1) {
            continue;
        }
        let x = (lon - lon0) / (lon1 - lon0).max(1.0e-9) * width;
        let y = (lat1 - lat) / (lat1 - lat0).max(1.0e-9) * height;
        if occupied
            .iter()
            .any(|&(old_x, old_y)| (x - old_x).hypot(y - old_y) < cell)
        {
            continue;
        }
        occupied.push((x, y));
        kept.push(index);
        if kept.len() >= limit {
            break;
        }
    }
    (kept, occupied)
}

/// Greedily declutter station plots in screen space.
///
/// ``coords`` are ``(lon, lat)`` pairs inside a view described by
/// ``bounds`` ``(lon0, lon1, lat0, lat1)`` and ``pixel_size`` (already
/// integers, like the Python callers' ``int`` items).  Stations outside the
/// bounds are dropped, stations within ``min_spacing_px`` pixels (floored at
/// 10) of an already-kept point are dropped, and at most ``max_stations``
/// (floored at 1) survive.  ``occupied_px`` optionally seeds the grid with
/// screen points claimed earlier, which is how the layer's status-station
/// pass shares one grid with the observations.  Returns
/// ``(kept_indices, occupied_px)`` -- the kept positions in input order and
/// the final occupied-pixel list.
#[pyfunction]
#[pyo3(signature = (coords, bounds, pixel_size, min_spacing_px, max_stations, occupied_px = None))]
fn surface_declutter_indices(
    coords: Vec<(f64, f64)>,
    bounds: (f64, f64, f64, f64),
    pixel_size: (i64, i64),
    min_spacing_px: i64,
    max_stations: i64,
    occupied_px: Option<Vec<(f64, f64)>>,
) -> (Vec<usize>, Vec<(f64, f64)>) {
    let occupied = occupied_px.unwrap_or_default();
    declutter_indices_core(
        &coords,
        bounds,
        pixel_size,
        min_spacing_px,
        max_stations,
        &occupied,
    )
}

// ---------------------------------------------------------------------------
// The station hit ring and its radii (surface_overlay_layer)
// ---------------------------------------------------------------------------

/// The pure half of ``surface_overlay_layer``'s ``ring``: the 13-point
/// polygon that serves as a station's hit/dot footprint, sampled around a
/// full turn as ``(lon + radius_lon * cos(angle), lat + radius_lat *
/// sin(angle))`` with ``angle = tau * index / 12.0``.
pub(crate) fn station_ring_core(
    lon: f64,
    lat: f64,
    radius_lon: f64,
    radius_lat: f64,
) -> Vec<(f64, f64)> {
    (0..RING_POINTS)
        .map(|index| {
            let angle = std::f64::consts::TAU * index as f64 / 12.0;
            (
                lon + radius_lon * angle.cos(),
                lat + radius_lat * angle.sin(),
            )
        })
        .collect()
}

/// The pure half of the radius rule in ``surface_overlay_layer``:
/// ``(radius_lon, radius_lat)`` keep the rings readable at any zoom, with
/// ``0.018`` degrees as the floor and the viewport span sharing ``3.5``
/// degrees over at least 200 (longitude) or 150 (latitude) pixels.
pub(crate) fn ring_radii_core(bounds: (f64, f64, f64, f64), pixel_size: (f64, f64)) -> (f64, f64) {
    let (lon0, lon1, lat0, lat1) = bounds;
    let radius_lon = 0.018_f64.max((lon1 - lon0).abs() * 3.5 / 200.0_f64.max(pixel_size.0));
    let radius_lat = 0.018_f64.max((lat1 - lat0).abs() * 3.5 / 150.0_f64.max(pixel_size.1));
    (radius_lon, radius_lat)
}

/// Build one station's 13-point hit/dot footprint ring.
///
/// The polygon is only the hit target; the wind staff and labels are drawn
/// in screen pixels by the map.  ``lon``/``lat`` is the station, and
/// ``radius_lon``/``radius_lat`` come from [`surface_ring_radii`].
#[pyfunction]
fn surface_station_ring(lon: f64, lat: f64, radius_lon: f64, radius_lat: f64) -> Vec<(f64, f64)> {
    station_ring_core(lon, lat, radius_lon, radius_lat)
}

/// Resolve the geographic ring radii for one viewport.
///
/// ``bounds`` is the view's ``(lon0, lon1, lat0, lat1)`` and ``pixel_size``
/// its size in pixels.  Each radius is ``0.018`` degrees or
/// ``abs(span) * 3.5 / max(200.0, pixels)`` (longitude) /
/// ``max(150.0, pixels)`` (latitude), whichever is larger.
#[pyfunction]
fn surface_ring_radii(bounds: (f64, f64, f64, f64), pixel_size: (f64, f64)) -> (f64, f64) {
    ring_radii_core(bounds, pixel_size)
}

// ---------------------------------------------------------------------------
// Python fixed-point formatting
// ---------------------------------------------------------------------------

/// Python's ``f"{value:.0f}"``: the nearest integer with ties to even and
/// Python's signed negative zero (``-0.4`` renders ``-0``).
fn format_fixed0(value: f64) -> String {
    let rounded = value.round_ties_even();
    let digits = rounded.abs() as i64;
    if rounded.is_sign_negative() {
        format!("-{digits}")
    } else {
        format!("{digits}")
    }
}

/// Python's ``f"{value:+.0f}"``: like [`format_fixed0`] but always signed --
/// ``+0`` for a non-negative zero and ``-0`` for a negative one.
fn format_plus_fixed0(value: f64) -> String {
    let rounded = value.round_ties_even();
    let digits = rounded.abs() as i64;
    if rounded.is_sign_negative() {
        format!("-{digits}")
    } else {
        format!("+{digits}")
    }
}

/// Python's ``f"{value:.1f}"``: one decimal place with ties to even, via
/// the crate's scaled [`f64::round_ties_even`] idiom (exact for the API's
/// one-decimal readings).
fn format_fixed1(value: f64) -> String {
    let scaled = (value * 10.0).round_ties_even() / 10.0;
    format!("{scaled:.1}")
}

// ---------------------------------------------------------------------------
// Station description and layer subtitle (surface_overlay_layer)
// ---------------------------------------------------------------------------

/// The pure half of the observation description built by
/// ``surface_overlay_layer``.
///
/// ``observed_display`` is the caller-formatted
/// ``"%Y-%m-%d %H:%M:%SZ"`` observation time; ``offset_min`` the signed
/// minutes from the requested time; the readings are ``None`` when missing
/// (non-finite values read as missing, like the ``_finite`` filter); and
/// ``stale`` is the observation's more-than-30-minutes verdict.  The wind
/// line follows [`wind_state_core`], and the gust appends after any state.
#[allow(clippy::too_many_arguments)]
pub(crate) fn format_observation_text_core(
    station_id: &str,
    station_name: &str,
    observed_display: &str,
    offset_min: f64,
    temp_c: Option<f64>,
    dewp_c: Option<f64>,
    wind_dir_deg: Option<f64>,
    wind_spd_kt: Option<f64>,
    wind_gust_kt: Option<f64>,
    stale: bool,
) -> String {
    let temperature = match temp_c {
        Some(value) if value.is_finite() => format!("{} \u{00b0}C", format_fixed1(value)),
        _ => "missing".to_string(),
    };
    let dewpoint = match dewp_c {
        Some(value) if value.is_finite() => format!("{} \u{00b0}C", format_fixed1(value)),
        _ => "missing".to_string(),
    };
    let state = wind_state_core(wind_spd_kt, wind_dir_deg);
    let mut wind = match (state, wind_spd_kt, wind_dir_deg) {
        ("calm", _, _) => "calm (0 kt)".to_string(),
        ("direction-missing", Some(speed), _) => {
            format!("{} kt; direction missing", format_fixed0(speed))
        }
        ("available", Some(speed), Some(direction)) => format!(
            "{}\u{00b0} at {} kt",
            format_fixed0(direction),
            format_fixed0(speed)
        ),
        _ => "missing (not calm)".to_string(),
    };
    // The gust reached neither the plot nor this description before: it is
    // the one reading that distinguishes a benign surface wind from an
    // outflow-driven one, so it appends after whichever state applies.
    if let Some(gust) = wind_gust_kt.filter(|gust| gust.is_finite()) {
        wind.push_str(&format!(", gusting {} kt", format_fixed0(gust)));
    }
    let mut description = String::new();
    description.push_str(&format!("{station_id} \u{2014} {station_name}\n"));
    description.push_str(&format!(
        "Observed {observed_display} ({} min from requested)\n",
        format_plus_fixed0(offset_min),
    ));
    description.push_str(&format!(
        "Temperature {temperature}; dewpoint {dewpoint}; wind {wind}\n"
    ));
    description.push_str("Source: NOAA/NWS API station observation");
    if stale {
        description.push_str("\nStale/mismatched: more than 30 minutes from requested time");
    }
    description
}

/// The pure half of the layer subtitle built by ``surface_overlay_layer``.
///
/// ``requested_display`` is the caller-formatted
/// ``"%Y-%m-%d %H:%MZ"`` requested time.  ``no_match_count`` is the caller-computed
/// ``len(set(unmatched) - failed)`` (the suffix is skipped when zero, which
/// is also the Python behaviour for an empty unmatched tuple);
/// ``failed_count`` is ``len(failed_station_ids)`` (not deduplicated); and
/// ``unqueried_count`` is ``max(0, discovered - queried)``.  Each
/// non-negative count appends its own accounting clause.
#[allow(clippy::too_many_arguments)]
pub(crate) fn format_subtitle_core(
    shown_count: usize,
    matched_count: usize,
    discovered_count: i64,
    queried_count: i64,
    requested_display: &str,
    stale_count: i64,
    no_match_count: i64,
    failed_count: i64,
    unqueried_count: i64,
) -> String {
    let mut subtitle = format!(
        "{shown_count} displayed / {matched_count} time-matched / \
         {discovered_count} nearby stations \u{00b7} {queried_count} queried \
         \u{00b7} requested {requested_display}"
    );
    if stale_count != 0 {
        subtitle.push_str(&format!(" \u{00b7} {stale_count} stale/mismatched"));
    }
    if no_match_count != 0 {
        subtitle.push_str(&format!(" \u{00b7} {no_match_count} no observation"));
    }
    if failed_count != 0 {
        subtitle.push_str(&format!(" \u{00b7} {failed_count} query failed"));
    }
    if unqueried_count != 0 {
        subtitle.push_str(&format!(" \u{00b7} {unqueried_count} not queried"));
    }
    subtitle
}

/// Compose one displayed observation's description text.
///
/// Inputs mirror ``surface_overlay_layer``: the station identity, the
/// caller-formatted observation time, the signed minute offset from the
/// requested time, the temperature/dewpoint in Celsius, the wind direction,
/// speed and gust in knots (``None`` when missing), and the stale verdict.
/// The wind line follows the truthful wind state, and the exact multi-line
/// text (em dash, degree signs, ``f"{offset:+.0f}"``) matches the Python
/// source.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn surface_format_observation_text(
    station_id: &str,
    station_name: &str,
    observed_display: &str,
    offset_min: f64,
    temp_c: Option<f64>,
    dewp_c: Option<f64>,
    wind_dir_deg: Option<f64>,
    wind_spd_kt: Option<f64>,
    wind_gust_kt: Option<f64>,
    stale: bool,
) -> String {
    format_observation_text_core(
        station_id,
        station_name,
        observed_display,
        offset_min,
        temp_c,
        dewp_c,
        wind_dir_deg,
        wind_spd_kt,
        wind_gust_kt,
        stale,
    )
}

/// Compose the surface-observation layer subtitle.
///
/// Counts mirror ``surface_overlay_layer``'s accounting: displayed shapes,
/// time-matched observations, discovered nearby stations, queried stations,
/// the caller-formatted requested time, then the optional
/// stale/no-observation/query-failed/not-queried clauses, each appended only
/// when its count is non-zero.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn surface_format_subtitle(
    shown_count: usize,
    matched_count: usize,
    discovered_count: i64,
    queried_count: i64,
    requested_display: &str,
    stale_count: i64,
    no_match_count: i64,
    failed_count: i64,
    unqueried_count: i64,
) -> String {
    format_subtitle_core(
        shown_count,
        matched_count,
        discovered_count,
        queried_count,
        requested_display,
        stale_count,
        no_match_count,
        failed_count,
        unqueried_count,
    )
}

// ---------------------------------------------------------------------------
// Atomic CSV export (export_surface_csv)
// ---------------------------------------------------------------------------

/// A staging file removed on drop, so a failed export leaves no debris
/// (and a successful one simply fails to remove the renamed-away path).
struct Pending(PathBuf);

impl Drop for Pending {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

/// The pure half of ``export_surface_csv``: stage the quoted CRLF records
/// in a unique same-directory temporary file, fsync it, close it, and
/// replace the destination only on success -- the close-then-rename order
/// is what lets an existing Windows destination be replaced.  Parent
/// directories are created like ``Path.mkdir(parents=True)``.
pub(crate) fn write_csv_core(path: &Path, rows: &[Vec<String>]) -> io::Result<()> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent)?;
    if path.file_name().is_none() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "CSV output needs a filename",
        ));
    }
    let permissions = match fs::metadata(path) {
        Ok(metadata) => Some(metadata.permissions()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(error) => return Err(error),
    };
    let mut opened = None;
    for _ in 0..16 {
        let mut random = [0_u8; 8];
        SystemRandom::new()
            .fill(&mut random)
            .map_err(|_| io::Error::other("could not choose a temporary CSV filename"))?;
        let temporary = parent.join(format!(
            ".{}.{:016x}.tmp",
            path.file_name()
                .map(|name| name.to_string_lossy())
                .unwrap_or_default(),
            u64::from_ne_bytes(random),
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
        {
            Ok(file) => {
                opened = Some((Pending(temporary), file));
                break;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error),
        }
    }
    let (pending, file) = opened.ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::AlreadyExists,
            "could not create a unique temporary CSV",
        )
    })?;
    let mut writer = csv::WriterBuilder::new()
        .terminator(csv::Terminator::CRLF)
        .from_writer(file);
    if let Some(permissions) = permissions {
        writer.get_ref().set_permissions(permissions)?;
    }
    for row in rows {
        writer.write_record(row).map_err(io::Error::other)?;
    }
    writer.flush()?;
    writer.get_ref().sync_all()?;
    // Close the file before replacing an existing Windows destination.
    drop(writer);
    fs::rename(&pending.0, path)?;
    Ok(())
}

/// Write one quoted CSV document atomically.
///
/// The GIL is detached for the whole write.  ``path``'s parent directories
/// are created, the records are staged in a unique temporary file beside
/// the destination using Python ``csv.writer``'s CRLF terminator, the file
/// is flushed and fsynced, and the destination is replaced atomically --
/// so a partially written document never appears at ``path`` and an
/// existing Windows destination can still be replaced.  Filesystem errors
/// surface as ``OSError`` exactly like ``export_surface_csv``.
#[pyfunction]
fn surface_write_csv(py: Python<'_>, path: &str, rows: Vec<Vec<String>>) -> PyResult<()> {
    let owned_path = path.to_string();
    py.detach(move || write_csv_core(Path::new(&owned_path), &rows))?;
    Ok(())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(surface_wind_state, module)?)?;
    module.add_function(wrap_pyfunction!(surface_wind_uv_kt, module)?)?;
    module.add_function(wrap_pyfunction!(surface_declutter_indices, module)?)?;
    module.add_function(wrap_pyfunction!(surface_station_ring, module)?)?;
    module.add_function(wrap_pyfunction!(surface_ring_radii, module)?)?;
    module.add_function(wrap_pyfunction!(surface_format_observation_text, module)?)?;
    module.add_function(wrap_pyfunction!(surface_format_subtitle, module)?)?;
    module.add_function(wrap_pyfunction!(surface_write_csv, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wind_states_keep_missing_calm_and_partial_distinct() {
        assert_eq!(wind_state_core(None, Some(225.0)), "missing");
        assert_eq!(wind_state_core(Some(f64::NAN), Some(225.0)), "missing");
        assert_eq!(wind_state_core(Some(f64::INFINITY), None), "missing");
        assert_eq!(wind_state_core(Some(0.0), None), "calm");
        assert_eq!(wind_state_core(Some(0.49), None), "calm");
        assert_eq!(wind_state_core(Some(-0.4), Some(90.0)), "calm");
        assert_eq!(wind_state_core(Some(12.0), None), "direction-missing");
        assert_eq!(
            wind_state_core(Some(12.0), Some(f64::NAN)),
            "direction-missing"
        );
        assert_eq!(wind_state_core(Some(12.0), Some(225.0)), "available");
        assert_eq!(
            wind_state_core(Some(0.5), Some(225.0)),
            "available",
            "only a magnitude below 0.5 kt is calm"
        );
    }

    #[test]
    fn wind_uv_matches_the_barb_components() {
        assert_eq!(wind_uv_core(Some(0.0), None), (Some(0.0), Some(0.0)));
        assert_eq!(wind_uv_core(Some(-0.2), None), (Some(0.0), Some(0.0)));
        assert_eq!(wind_uv_core(None, Some(225.0)), (None, None));
        assert_eq!(wind_uv_core(Some(f64::NAN), Some(225.0)), (None, None));
        assert_eq!(wind_uv_core(Some(10.0), None), (None, None));
        assert_eq!(wind_uv_core(Some(10.0), Some(f64::INFINITY)), (None, None));
        // 19.4384 kt (10 m/s) from 225 degrees: u = v = 13.745...
        let (u, v) = wind_uv_core(Some(19.4384), Some(225.0));
        assert!((u.unwrap() - 13.745).abs() < 1.0e-2);
        assert!((v.unwrap() - 13.745).abs() < 1.0e-2);
        // From 0 degrees the barb points north: u = 0, v = -speed.
        let (u, v) = wind_uv_core(Some(10.0), Some(0.0));
        assert!(u.unwrap().abs() < 1.0e-12);
        assert_eq!(v, Some(-10.0));
    }

    #[test]
    fn declutter_keeps_spaced_in_bounds_stations_in_order() {
        let coords = [(-97.00, 35.00), (-97.01, 35.01), (5.0, 5.0)];
        let (kept, occupied) =
            declutter_indices_core(&coords, (-98.0, -96.0, 34.0, 36.0), (400, 300), 50, 24, &[]);
        assert_eq!(kept, vec![0]);
        assert_eq!(occupied, vec![(200.0, 150.0)]);
    }

    #[test]
    fn declutter_respects_the_limit_and_existing_occupancy() {
        let coords = [(-97.0, 35.0), (-97.5, 35.5), (-96.5, 34.5)];
        let (kept, occupied) =
            declutter_indices_core(&coords, (-98.0, -96.0, 34.0, 36.0), (400, 300), 10, 2, &[]);
        assert_eq!(kept, vec![0, 1]);
        assert_eq!(occupied.len(), 2);
        // A non-positive max_stations still keeps one, like max(1, ...).
        let (kept, _) =
            declutter_indices_core(&coords, (-98.0, -96.0, 34.0, 36.0), (400, 300), 10, 0, &[]);
        assert_eq!(kept, vec![0]);
        // Pre-occupied pixels are honoured and carried through.
        let first = occupied[0];
        let (kept, occupied) = declutter_indices_core(
            &coords,
            (-98.0, -96.0, 34.0, 36.0),
            (400, 300),
            10,
            5,
            &[first],
        );
        assert_eq!(kept, vec![1, 2]);
        assert_eq!(occupied[0], first);
        assert_eq!(occupied.len(), 3);
    }

    #[test]
    fn station_ring_samples_thirteen_points_over_one_turn() {
        let points = station_ring_core(-97.0, 35.0, 0.1, 0.2);
        assert_eq!(points.len(), RING_POINTS);
        assert!((points[0].0 - (-97.0 + 0.1)).abs() < 1.0e-12);
        assert!((points[0].1 - 35.0).abs() < 1.0e-12);
        // A quarter turn: cos(tau/4) ~ 0, sin(tau/4) = 1.
        assert!((points[3].0 - (-97.0)).abs() < 1.0e-12);
        assert!((points[3].1 - (35.0 + 0.2)).abs() < 1.0e-12);
        // Half turn reaches the opposite side, and the closing sample returns to the start.
        assert!((points[6].0 - (-97.0 - 0.1)).abs() < 1.0e-12);
        assert!((points[6].1 - 35.0).abs() < 1.0e-12);
        assert!((points[12].0 - points[0].0).abs() < 1.0e-12);
        assert!((points[12].1 - points[0].1).abs() < 1.0e-12);
    }

    #[test]
    fn ring_radii_follow_the_pixel_span_floors() {
        let (lon, lat) = ring_radii_core((-98.0, -96.0, 34.0, 36.0), (400.0, 300.0));
        assert_eq!(lon, 0.018, "2 * 3.5 / 400 stays below the floor");
        assert!((lat - 7.0 / 300.0).abs() < 1.0e-12);
        let (lon, lat) = ring_radii_core((-98.0, -96.0, 34.0, 36.0), (200.0, 100.0));
        assert!((lon - 7.0 / 200.0).abs() < 1.0e-12);
        assert!((lat - 7.0 / 150.0).abs() < 1.0e-12, "the 150-pixel floor");
        // A degenerate span falls back to the 0.018-degree floor.
        let (lon, lat) = ring_radii_core((-96.0, -96.0, 35.0, 35.0), (900.0, 600.0));
        assert_eq!(lon, 0.018);
        assert_eq!(lat, 0.018);
    }

    #[test]
    fn observation_text_matches_the_python_composition() {
        let text = format_observation_text_core(
            "KAAA",
            "Station KAAA",
            "2026-09-13 01:35:00Z",
            -10.0,
            Some(22.0),
            Some(15.0),
            Some(225.0),
            Some(19.4384),
            None,
            false,
        );
        assert_eq!(
            text,
            "KAAA \u{2014} Station KAAA\n\
             Observed 2026-09-13 01:35:00Z (-10 min from requested)\n\
             Temperature 22.0 \u{00b0}C; dewpoint 15.0 \u{00b0}C; \
             wind 225\u{00b0} at 19 kt\n\
             Source: NOAA/NWS API station observation"
        );
    }

    #[test]
    fn observation_text_covers_wind_states_gusts_and_staleness() {
        let text = |speed: Option<f64>, dir: Option<f64>, gust: Option<f64>, stale: bool| {
            format_observation_text_core(
                "K",
                "S",
                "T",
                0.0,
                Some(20.0),
                Some(15.0),
                dir,
                speed,
                gust,
                stale,
            )
        };
        // A measured zero keeps the calm wording, and the offset signs +0.
        assert!(text(Some(0.0), None, None, false).contains("(+0 min from"));
        assert!(text(Some(0.0), None, None, false).contains("wind calm (0 kt)\n"));
        // A non-calm speed without a direction stays a partial value.
        assert!(text(Some(12.3), None, None, false).contains("wind 12 kt; direction missing\n"));
        // A missing speed is never coerced to the calm circle.
        assert!(text(None, None, None, false).contains("wind missing (not calm)\n"));
        // The gust appends after the full state.
        assert!(text(Some(19.44), Some(225.0), Some(25.4), false)
            .contains("wind 225\u{00b0} at 19 kt, gusting 25 kt\n"));
        // A non-finite gust reads as missing.
        assert!(text(Some(19.44), Some(225.0), Some(f64::NAN), false)
            .ends_with("Source: NOAA/NWS API station observation"));
        assert!(!text(Some(19.44), Some(225.0), Some(f64::NAN), false).contains("gusting"));
        // Stale observations carry the mismatch note.
        assert!(text(Some(0.0), None, None, true)
            .ends_with("\nStale/mismatched: more than 30 minutes from requested time"));
        // Missing readings word themselves explicitly.
        let missing =
            format_observation_text_core("K", "S", "T", 0.0, None, None, None, None, None, false);
        assert!(missing.contains("Temperature missing; dewpoint missing;"));
    }

    #[test]
    fn signed_minute_offsets_round_half_to_even_like_python() {
        let line = |offset: f64| {
            format_observation_text_core("K", "S", "T", offset, None, None, None, None, None, false)
        };
        assert!(line(0.0).contains("(+0 min"));
        assert!(line(-0.4).contains("(-0 min"));
        assert!(line(0.4).contains("(+0 min"));
        assert!(line(0.5).contains("(+0 min"), "0.5 ties to 0");
        assert!(line(-0.5).contains("(-0 min"), "-0.5 ties to -0");
        assert!(line(4.5).contains("(+4 min"));
        assert!(line(5.5).contains("(+6 min"));
        assert!(line(-4.5).contains("(-4 min"));
        assert!(line(-5.5).contains("(-6 min"));
        assert!(line(-600.0 / 60.0).contains("(-10 min"));
    }

    #[test]
    fn fixed_point_formats_round_half_to_even() {
        assert_eq!(format_fixed0(19.4384), "19");
        assert_eq!(format_fixed0(225.0), "225");
        assert_eq!(format_fixed0(0.4), "0");
        assert_eq!(format_fixed0(-0.4), "-0");
        assert_eq!(format_fixed0(2.5), "2");
        assert_eq!(format_fixed0(3.5), "4");
        assert_eq!(format_fixed1(22.0), "22.0");
        assert_eq!(format_fixed1(22.25), "22.2", "the 22.25 tie rounds to even");
        assert_eq!(format_fixed1(-0.04), "-0.0");
        assert_eq!(format_fixed1(0.0), "0.0");
    }

    #[test]
    fn subtitle_lists_display_and_accounting_counts() {
        let base = format_subtitle_core(1, 2, 2, 3, "2026-09-13 01:45Z", 0, 0, 0, 0);
        assert_eq!(
            base,
            "1 displayed / 2 time-matched / 2 nearby stations \u{00b7} 3 queried \
             \u{00b7} requested 2026-09-13 01:45Z"
        );
        let full = format_subtitle_core(1, 2, 4, 3, "2026-09-13 01:45Z", 1, 1, 2, 1);
        assert_eq!(
            full,
            "1 displayed / 2 time-matched / 4 nearby stations \u{00b7} 3 queried \
             \u{00b7} requested 2026-09-13 01:45Z \u{00b7} 1 stale/mismatched \
             \u{00b7} 1 no observation \u{00b7} 2 query failed \u{00b7} 1 not queried"
        );
        // Zero counts contribute no clauses, in any combination.
        assert_eq!(
            format_subtitle_core(2, 2, 2, 2, "T", 1, 0, 0, 0),
            "2 displayed / 2 time-matched / 2 nearby stations \u{00b7} 2 queried \
             \u{00b7} requested T \u{00b7} 1 stale/mismatched"
        );
    }

    #[test]
    fn csv_export_is_quoted_crlf_and_atomic() {
        let mut random = [0_u8; 8];
        SystemRandom::new()
            .fill(&mut random)
            .expect("system randomness");
        let directory = std::env::temp_dir().join(format!(
            "sharpmod-surface-csv-{:016x}",
            u64::from_ne_bytes(random)
        ));
        let destination = directory.join("nested").join("surface.csv");
        let rows = vec![
            vec![
                "station_id".to_string(),
                "station_name".to_string(),
                "latitude_deg".to_string(),
            ],
            vec![
                "KAAA".to_string(),
                "Alpha, Beta".to_string(),
                "35.0".to_string(),
            ],
            vec![
                "KBBB".to_string(),
                "say \"hi\"".to_string(),
                "35.5".to_string(),
            ],
        ];
        write_csv_core(&destination, &rows).expect("write succeeds");
        let written = fs::read_to_string(&destination).expect("read back");
        assert_eq!(
            written,
            "station_id,station_name,latitude_deg\r\n\
             KAAA,\"Alpha, Beta\",35.0\r\n\
             KBBB,\"say \"\"hi\"\"\",35.5\r\n"
        );
        // A second export replaces the existing destination in place.
        write_csv_core(&destination, &[vec!["x".to_string()]]).expect("replace succeeds");
        let replaced = fs::read_to_string(&destination).expect("read back");
        assert_eq!(replaced, "x\r\n");
        // The parent keeps only the destination: no staging debris remains.
        let staged: Vec<String> = fs::read_dir(destination.parent().unwrap())
            .expect("list parent directory")
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert!(staged.iter().all(|name| !name.ends_with(".tmp")));
        // An empty document is still created and synced.
        let empty = directory.join("empty.csv");
        write_csv_core(&empty, &[]).expect("empty export succeeds");
        assert_eq!(fs::metadata(&empty).unwrap().len(), 0);
        let _ = fs::remove_dir_all(&directory);
    }

    #[test]
    fn csv_export_needs_a_filename() {
        let error = write_csv_core(Path::new(".."), &[]).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        assert_eq!(error.to_string(), "CSV output needs a filename");
    }
}

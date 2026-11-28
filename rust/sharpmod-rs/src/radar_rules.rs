//! Native radar scan-selection, mosaic-coverage, and site-choice rules
//! (milestone M36).
//!
//! Rust ports of the pure half of three NOAA radar provider modules:
//!
//! * ``sharpmod.providers.radar_time`` -- ``select_time``'s history
//!   window and tolerance arithmetic ([`radar_select_scan_epoch`]);
//! * ``sharpmod.providers.radar_mosaic`` -- ``covers``
//!   ([`radar_mosaic_covers`]), ``_frame_size``
//!   ([`radar_mosaic_frame_size`]), and ``_service_exception_text``
//!   ([`radar_service_exception_text`]);
//! * ``sharpmod.providers.radar_site`` -- ``RadarSite.bounds``
//!   ([`radar_site_bounds`]), ``_distance_km``
//!   ([`radar_site_distance_km`]), ``nearest_site``
//!   ([`radar_site_nearest_index`]), and ``_frame_size``
//!   ([`radar_site_frame_size`]).
//!
//! Every window and message matches the Python sources verbatim, so a
//! caller can translate the scan selector's ``time_error`` /
//! ``scan_missing`` reasons into ``RadarTimeError`` and
//! ``RadarScanMissing`` one to one, and a WMS failure still carries
//! GeoServer's own explanation.

use pyo3::prelude::*;

/// Advertised-history window of ``radar_time.select_time`` (60 minutes).
const HISTORY_MS: i64 = 3_600_000;
/// Largest honest distance between the requested and selected scan
/// (10 minutes).
const TOLERANCE_MS: i64 = 600_000;
/// How far into the future a request is still served (1 minute).
const FUTURE_SLACK_MS: i64 = 60_000;
/// Requests within this distance of "now" are current views (2 minutes).
const CURRENT_WINDOW_MS: i64 = 120_000;

// ---------------------------------------------------------------------------
// Scan selection (radar_time.select_time)
// ---------------------------------------------------------------------------

/// The pure half of ``radar_time.select_time``: pick the advertised
/// scan nearest one requested instant, and never substitute a latest
/// image for history.
///
/// Returns ``(Some(scan_ms), None, None)`` on success.  A request
/// outside ``reference - 70 minutes ..= reference + 1 minute`` is
/// ``(None, Some("time_error"), Some("radar history is available only
/// for the past 60 minutes"))``.  An inventory with no scan inside
/// ``[reference - 60 minutes, reference]`` is a ``scan_missing`` gap, as
/// is a selected scan more than 10 minutes from a non-current request;
/// both messages name the layer exactly as Python formats them.
pub(crate) fn select_scan_epoch_core(
    layer: &str,
    scan_epochs_ms: &[i64],
    requested_epoch_ms: i64,
    reference_epoch_ms: i64,
) -> (Option<i64>, Option<String>, Option<String>) {
    let earliest_allowed = reference_epoch_ms.saturating_sub(HISTORY_MS + TOLERANCE_MS);
    let latest_allowed = reference_epoch_ms.saturating_add(FUTURE_SLACK_MS);
    if requested_epoch_ms < earliest_allowed || requested_epoch_ms > latest_allowed {
        return (
            None,
            Some("time_error".to_string()),
            Some("radar history is available only for the past 60 minutes".to_string()),
        );
    }
    // Only scans inside the advertised history window are candidates.
    let window_start = reference_epoch_ms.saturating_sub(HISTORY_MS);
    let mut selected: Option<i64> = None;
    for &scan in scan_epochs_ms {
        if scan < window_start || scan > reference_epoch_ms {
            continue;
        }
        // min(candidates, key=(abs(scan - requested), scan)): the
        // nearest scan wins and a tie keeps the earlier one.
        let key = (scan.abs_diff(requested_epoch_ms), scan);
        if selected.is_none_or(|current| key < (current.abs_diff(requested_epoch_ms), current)) {
            selected = Some(scan);
        }
    }
    let Some(selected) = selected else {
        return (
            None,
            Some("scan_missing".to_string()),
            Some(format!(
                "{layer} advertises no historical scans in the past 60 minutes"
            )),
        );
    };
    let current = reference_epoch_ms.abs_diff(requested_epoch_ms) <= CURRENT_WINDOW_MS as u64;
    if !current && selected.abs_diff(requested_epoch_ms) > TOLERANCE_MS as u64 {
        return (
            None,
            Some("scan_missing".to_string()),
            Some(format!(
                "{layer} has no advertised scan within 10 minutes of the requested time"
            )),
        );
    }
    (Some(selected), None, None)
}

/// Select the advertised radar scan nearest one requested instant.
///
/// ``layer`` is the WMS layer name used verbatim in the error messages,
/// ``scan_epochs_ms`` the layer's advertised scan times in epoch
/// milliseconds (the caller resolves them from the capabilities
/// document), ``requested_epoch_ms`` the target instant, and
/// ``reference_epoch_ms`` the current instant ``now``.
///
/// The result is ``(selected, reason, message)``: on success
/// ``(Some(scan_ms), None, None)`` with a scan that is at most 10
/// minutes from the request (unless the request is a current view, two
/// minutes from now, which honestly shows a delayed latest scan);
/// otherwise ``(None, Some(reason), Some(message))`` with the reason
/// ``"time_error"`` or ``"scan_missing"`` and the exact message
/// ``radar_time.select_time`` raises for it.
#[pyfunction]
fn radar_select_scan_epoch(
    layer: &str,
    scan_epochs_ms: Vec<i64>,
    requested_epoch_ms: i64,
    reference_epoch_ms: i64,
) -> (Option<i64>, Option<String>, Option<String>) {
    select_scan_epoch_core(
        layer,
        &scan_epochs_ms,
        requested_epoch_ms,
        reference_epoch_ms,
    )
}

// ---------------------------------------------------------------------------
// Mosaic coverage and frame geometry (radar_mosaic)
// ---------------------------------------------------------------------------

/// The pure half of ``radar_mosaic.covers``: a ``(lon0, lon1, lat0,
/// lat1)`` view meets the coverage only when both axes overlap it.
pub(crate) fn mosaic_covers_core(
    view: Option<&[f64]>,
    coverage_bounds: (f64, f64, f64, f64),
) -> bool {
    let Some(view) = view else {
        return false;
    };
    if view.len() != 4 {
        return false;
    }
    let (min_lon, max_lon, min_lat, max_lat) = coverage_bounds;
    let [lon0, lon1, lat0, lat1] = [view[0], view[1], view[2], view[3]];
    if lon0.max(lon1) < min_lon || lon0.min(lon1) > max_lon {
        return false;
    }
    if lat0.max(lat1) < min_lat || lat0.min(lat1) > max_lat {
        return false;
    }
    true
}

/// Report whether a map view can intersect the mosaic's coverage.
///
/// ``view`` is the viewport as ``(lon0, lon1, lat0, lat1)`` or ``None``
/// when the caller has none, and ``coverage_bounds`` the product's own
/// extent in the same order (MRMS CONUS is ``(-130, -60, 20, 55)``).  A
/// view that cannot overlap the coverage returns ``False`` so the
/// caller skips the request entirely; a view that is not four numbers
/// is not a view.
#[pyfunction]
fn radar_mosaic_covers(view: Option<Vec<f64>>, coverage_bounds: (f64, f64, f64, f64)) -> bool {
    mosaic_covers_core(view.as_deref(), coverage_bounds)
}

/// The pure half of ``radar_mosaic._frame_size``: clamp each dimension
/// into ``[min_px, max_px]`` exactly like ``min(max_px, max(min_px,
/// value))``.
pub(crate) fn mosaic_frame_size_core(
    width: i64,
    height: i64,
    min_px: i64,
    max_px: i64,
) -> (i64, i64) {
    (
        min_px.max(width).min(max_px),
        min_px.max(height).min(max_px),
    )
}

/// Clamp a requested mosaic frame size into the pixel budget.
///
/// Both dimensions clamp into ``[min_px, max_px]`` (the mosaic's own
/// 256..=4096 bounds), the upper bound being what stops a request
/// asking a shared public service for something enormous.
#[pyfunction]
fn radar_mosaic_frame_size(width: i64, height: i64, min_px: i64, max_px: i64) -> (i64, i64) {
    mosaic_frame_size_core(width, height, min_px, max_px)
}

/// The opening tag bytes of a WMS ``ServiceException``.
const SERVICE_EXCEPTION_OPEN: &[u8] = b"<ServiceException";
/// The closing tag bytes of a WMS ``ServiceException``.
const SERVICE_EXCEPTION_CLOSE: &[u8] = b"</ServiceException";

/// ``\s`` as the Python bytes regular expression defines it.
fn is_regex_whitespace(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0C | 0x0B)
}

/// Find the next ``<`` + ``needle`` markup start at or after ``from``,
/// comparing case-insensitively; ``needle`` excludes the ``<``.
fn find_markup(scan: &[u8], from: usize, needle: &[u8]) -> Option<usize> {
    let mut search = from;
    while let Some(found) = memchr::memchr(b'<', &scan[search..]) {
        let start = search + found;
        if scan.len() - start > needle.len()
            && scan[start + 1..start + 1 + needle.len()].eq_ignore_ascii_case(needle)
        {
            return Some(start);
        }
        search = start + 1;
    }
    None
}

/// The pure half of ``radar_mosaic._service_exception_text``: lift the
/// first ``ServiceException`` message out of a non-image payload.
///
/// The match reproduces ``<ServiceException(?:\s[^>]*)?>(.*?)</ServiceException\s*>``
/// with ``re.DOTALL | re.IGNORECASE`` over the bounded payload prefix:
/// the tag name must be followed by whitespace or its closing bracket
/// (so ``<ServiceExceptionReport>`` never matches), the content stops
/// at the first closing tag, and any whitespace before its ``>`` is
/// allowed.  The captured text is decoded UTF-8-lossy, collapsed to
/// single spaces, and truncated to 300 characters.
pub(crate) fn service_exception_text_core(payload: &[u8], max_scan_bytes: usize) -> String {
    let scan = &payload[..payload.len().min(max_scan_bytes)];
    let open_needle = &SERVICE_EXCEPTION_OPEN[1..];
    let close_needle = &SERVICE_EXCEPTION_CLOSE[1..];
    let mut open_search = 0;
    while let Some(start) = find_markup(scan, open_search, open_needle) {
        let after_name = start + SERVICE_EXCEPTION_OPEN.len();
        let content_start = match scan.get(after_name) {
            // A bare ">" closes the opening tag immediately.
            Some(b'>') => Some(after_name + 1),
            // Whitespace starts the attribute list, which runs to the
            // first ">" after it.
            Some(byte) if is_regex_whitespace(*byte) => {
                memchr::memchr(b'>', &scan[after_name..]).map(|offset| after_name + offset + 1)
            }
            // Anything else is another tag such as
            // <ServiceExceptionReport>, not a match.
            _ => None,
        };
        let Some(content_start) = content_start else {
            open_search = after_name;
            continue;
        };
        let mut close_search = content_start;
        while let Some(close) = find_markup(scan, close_search, close_needle) {
            let after_close = close + SERVICE_EXCEPTION_CLOSE.len();
            // The non-greedy content ends at the first closing tag,
            // whose ">" may be preceded by whitespace.
            let mut end = after_close;
            while scan.get(end).is_some_and(|byte| is_regex_whitespace(*byte)) {
                end += 1;
            }
            if scan.get(end) == Some(&b'>') {
                let text = String::from_utf8_lossy(&scan[content_start..close]);
                let collapsed: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
                return collapsed.chars().take(300).collect();
            }
            close_search = after_close;
        }
        open_search = content_start;
    }
    String::new()
}

/// Lift the ``ServiceException`` message out of a WMS error payload.
///
/// ``payload`` is the response body and ``max_scan_bytes`` the bounded
/// prefix to inspect (the mosaic's own ``_EXCEPTION_SCAN_BYTES`` is
/// 4096).  The tag match is case-insensitive, spans newlines, and never
/// mistakes the enclosing ``<ServiceExceptionReport>`` for the
/// exception itself; the message is decoded UTF-8-lossy, its
/// GeoServer pretty-printing collapsed to single spaces, and truncated
/// to 300 characters.  Without a match the result is the empty string,
/// leaving the caller to report the raw failure.
#[pyfunction]
fn radar_service_exception_text(payload: &[u8], max_scan_bytes: usize) -> String {
    service_exception_text_core(payload, max_scan_bytes)
}

// ---------------------------------------------------------------------------
// Single-site geometry and choice (radar_site)
// ---------------------------------------------------------------------------

/// The pure half of ``RadarSite.bounds``: the 10-degree antenna box as
/// ``(lon0, lon1, lat0, lat1)``, clamped into the valid coordinate
/// ranges so an antimeridian or polar site still yields a box the
/// overlay contract accepts.
pub(crate) fn site_bounds_core(lat: f64, lon: f64, half_span_deg: f64) -> (f64, f64, f64, f64) {
    let span = half_span_deg;
    (
        (lon - span).max(-180.0),
        (lon + span).min(180.0),
        (lat - span).max(-90.0),
        (lat + span).min(90.0),
    )
}

/// Return one radar site's frame extent as
/// ``(lon0, lon1, lat0, lat1)``.
///
/// The box spans ``half_span_deg`` either side of the antenna and is
/// clamped into the valid longitude and latitude ranges, exactly as
/// ``RadarSite.bounds`` does.
#[pyfunction]
fn radar_site_bounds(lat: f64, lon: f64, half_span_deg: f64) -> (f64, f64, f64, f64) {
    site_bounds_core(lat, lon, half_span_deg)
}

/// The pure half of ``radar_site._distance_km``: the great-circle
/// distance in kilometres, so site choice stays right at high latitude
/// where a degree of longitude is half its equatorial length.
pub(crate) fn site_distance_km_core(lat0: f64, lon0: f64, lat1: f64, lon1: f64) -> f64 {
    let radius = 6371.0;
    let phi0 = lat0.to_radians();
    let phi1 = lat1.to_radians();
    let dphi = phi1 - phi0;
    let dlambda = (lon1 - lon0).to_radians();
    let a = (dphi / 2.0).sin().powi(2) + phi0.cos() * phi1.cos() * (dlambda / 2.0).sin().powi(2);
    2.0 * radius * a.sqrt().min(1.0).asin()
}

/// Great-circle distance between two points, in kilometres.
///
/// The haversine form matches ``radar_site._distance_km`` exactly,
/// including the ``min(1.0, sqrt(a))`` guard before ``asin``.
#[pyfunction]
fn radar_site_distance_km(lat0: f64, lon0: f64, lat1: f64, lon1: f64) -> f64 {
    site_distance_km_core(lat0, lon0, lat1, lon1)
}

/// The pure half of ``radar_site.nearest_site``: scan the site
/// catalogue for the closest site that publishes the wanted product,
/// then honour the maximum useful range.
///
/// ``site_coords`` carries ``(lat, lon, supported)`` per site, where
/// ``supported`` mirrors ``RadarSite.supports``; unsupported entries
/// are skipped.  The strict ``<`` update keeps the first of two
/// equally distant sites, like Python's loop.
pub(crate) fn site_nearest_index_core(
    lat: f64,
    lon: f64,
    site_coords: &[(f64, f64, bool)],
    max_km: f64,
) -> Option<(usize, f64)> {
    let mut best: Option<(usize, f64)> = None;
    for (index, &(site_lat, site_lon, supported)) in site_coords.iter().enumerate() {
        if !supported {
            continue;
        }
        let distance = site_distance_km_core(lat, lon, site_lat, site_lon);
        if best.is_none_or(|(_, best_km)| distance < best_km) {
            best = Some((index, distance));
        }
    }
    match best {
        Some((index, km)) if km <= max_km => Some((index, km)),
        _ => None,
    }
}

/// Choose the nearest radar site for one point.
///
/// ``site_coords`` is the catalogue as ``(lat, lon, supported)``
/// triples (the caller resolves each site's product support), and
/// ``max_km`` the useful range (the provider's own
/// ``MAX_SITE_DISTANCE_KM`` is 460).  The result is the winning
/// catalogue entry's index and its distance in kilometres, or ``None``
/// when nothing supported is within ``max_km`` -- the ocean-and-outside-
/// the-network answer that avoids spending a request.
#[pyfunction]
fn radar_site_nearest_index(
    lat: f64,
    lon: f64,
    site_coords: Vec<(f64, f64, bool)>,
    max_km: f64,
) -> Option<(usize, f64)> {
    site_nearest_index_core(lat, lon, &site_coords, max_km)
}

/// The pure half of ``radar_site._frame_size``: size a site frame to the
/// antenna box's own aspect, keeping it plate carree, and never below
/// the minimum edge.
pub(crate) fn site_frame_size_core(
    bounds: (f64, f64, f64, f64),
    pixels: i64,
    min_px: i64,
) -> (i64, i64) {
    let (lon0, lon1, lat0, lat1) = bounds;
    // A degree box shorter than a microdegree still has an aspect.
    let lon_span = (lon1 - lon0).max(1e-6);
    let lat_span = (lat1 - lat0).max(1e-6);
    if lon_span >= lat_span {
        let height = (pixels as f64 * lat_span / lon_span).round_ties_even() as i64;
        (pixels, min_px.max(height))
    } else {
        let width = (pixels as f64 * lon_span / lat_span).round_ties_even() as i64;
        (min_px.max(width), pixels)
    }
}

/// Size one site frame to ``(width, height)`` pixels.
///
/// ``bounds`` is the site's clamped extent from
/// [`radar_site_bounds`] and ``pixels`` the budget along the longer
/// edge.  The longer degree axis gets the full budget and the shorter
/// one scales by the degree ratio, rounded with Python's
/// banker's rounding, floored at ``min_px``.  Sizing to the degree box
/// rather than the ground distance is what keeps the frame plate
/// carree for the blit.
#[pyfunction]
fn radar_site_frame_size(bounds: (f64, f64, f64, f64), pixels: i64, min_px: i64) -> (i64, i64) {
    site_frame_size_core(bounds, pixels, min_px)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(radar_select_scan_epoch, module)?)?;
    module.add_function(wrap_pyfunction!(radar_mosaic_covers, module)?)?;
    module.add_function(wrap_pyfunction!(radar_mosaic_frame_size, module)?)?;
    module.add_function(wrap_pyfunction!(radar_service_exception_text, module)?)?;
    module.add_function(wrap_pyfunction!(radar_site_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(radar_site_distance_km, module)?)?;
    module.add_function(wrap_pyfunction!(radar_site_nearest_index, module)?)?;
    module.add_function(wrap_pyfunction!(radar_site_frame_size, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const MINUTE_MS: i64 = 60_000;
    const HOUR_MS: i64 = 3_600_000;
    /// 2026-10-17T12:00:00Z in epoch milliseconds, the suite's "now".
    const NOW_MS: i64 = 1_792_238_400_000;

    #[test]
    fn select_scan_epoch_rejects_gaps_future_and_out_of_range_requests() {
        // 11:05Z, 11:59Z, and 12:01Z: the future scan is not a
        // candidate, and the nearest real one is 19 minutes from a
        // 20-minute-old request.
        let scans = [
            NOW_MS - 55 * MINUTE_MS,
            NOW_MS - MINUTE_MS,
            NOW_MS + MINUTE_MS,
        ];
        let (selected, code, message) =
            select_scan_epoch_core("radar", &scans, NOW_MS - 20 * MINUTE_MS, NOW_MS);
        assert_eq!(selected, None);
        assert_eq!(code.as_deref(), Some("scan_missing"));
        assert_eq!(
            message.as_deref(),
            Some("radar has no advertised scan within 10 minutes of the requested time")
        );
        // Two hours old and two minutes ahead are both out of range.
        for requested in [NOW_MS - 2 * HOUR_MS, NOW_MS + 2 * MINUTE_MS] {
            let (selected, code, message) =
                select_scan_epoch_core("radar", &scans, requested, NOW_MS);
            assert_eq!(selected, None);
            assert_eq!(code.as_deref(), Some("time_error"));
            assert_eq!(
                message.as_deref(),
                Some("radar history is available only for the past 60 minutes")
            );
        }
        // A request at "now" still gets the real 11:59Z scan.
        let (selected, code, message) = select_scan_epoch_core("radar", &scans, NOW_MS, NOW_MS);
        assert_eq!(selected, Some(NOW_MS - MINUTE_MS));
        assert_eq!(code, None);
        assert_eq!(message, None);
    }

    #[test]
    fn sixty_minute_target_can_wait_in_the_preload_queue() {
        // A minute-rounded history target may sit behind other preload
        // downloads, so a request up to 70 minutes old passes the gate
        // while its scan stays inside the 60-minute window.
        let selected_scan = NOW_MS - 59 * MINUTE_MS - 11_000;
        let (picked, code, message) =
            select_scan_epoch_core("radar", &[selected_scan], NOW_MS - 62 * MINUTE_MS, NOW_MS);
        assert_eq!(picked, Some(selected_scan));
        assert_eq!(code, None);
        assert_eq!(message, None);
    }

    #[test]
    fn current_view_can_show_a_late_scan_without_inventing_history() {
        let selected_scan = NOW_MS - 25 * MINUTE_MS;
        // 45 seconds old is a current view: the latest scan is shown
        // honestly even though it is 25 minutes old.
        let (picked, code, message) =
            select_scan_epoch_core("radar", &[selected_scan], NOW_MS - 45_000, NOW_MS);
        assert_eq!(picked, Some(selected_scan));
        assert_eq!(code, None);
        assert_eq!(message, None);
        // Ten minutes old is not current, and the gap is real.
        let (picked, code, message) =
            select_scan_epoch_core("radar", &[selected_scan], NOW_MS - 10 * MINUTE_MS, NOW_MS);
        assert_eq!(picked, None);
        assert_eq!(code.as_deref(), Some("scan_missing"));
        assert_eq!(
            message.as_deref(),
            Some("radar has no advertised scan within 10 minutes of the requested time")
        );
    }

    #[test]
    fn empty_inventory_is_a_confirmed_gap_and_ties_pick_the_earlier_scan() {
        let (picked, code, message) =
            select_scan_epoch_core("conus_cref_qcd", &[], NOW_MS - 20 * MINUTE_MS, NOW_MS);
        assert_eq!(picked, None);
        assert_eq!(code.as_deref(), Some("scan_missing"));
        assert_eq!(
            message.as_deref(),
            Some("conus_cref_qcd advertises no historical scans in the past 60 minutes")
        );
        // Two scans equally distant from the request: the earlier one
        // wins the tie, like Python's min with the scan in the key.
        let scans = [NOW_MS - 21 * MINUTE_MS, NOW_MS - 19 * MINUTE_MS];
        let (picked, code, message) =
            select_scan_epoch_core("radar", &scans, NOW_MS - 20 * MINUTE_MS, NOW_MS);
        assert_eq!(picked, Some(NOW_MS - 21 * MINUTE_MS));
        assert_eq!(code, None);
        assert_eq!(message, None);
    }

    #[test]
    fn the_history_window_is_inclusive_at_both_ends() {
        // A scan exactly 60 minutes old is a candidate; one a
        // millisecond older is not.  The exact-10-minute tolerance
        // boundary is inclusive too.
        let scans = [NOW_MS - HISTORY_MS - 1, NOW_MS - HISTORY_MS, NOW_MS];
        let (picked, _, _) =
            select_scan_epoch_core("radar", &scans, NOW_MS - 50 * MINUTE_MS, NOW_MS);
        assert_eq!(picked, Some(NOW_MS - HISTORY_MS));
        // A future scan is never a candidate: a request at the future
        // slack edge still gets the on-time scan.
        let (picked, _, _) = select_scan_epoch_core("radar", &scans, NOW_MS + MINUTE_MS, NOW_MS);
        assert_eq!(picked, Some(NOW_MS));
        // A scan at exactly the request matches with no tolerance
        // complaint.
        let (picked, code, _) = select_scan_epoch_core(
            "radar",
            &[NOW_MS - 30 * MINUTE_MS],
            NOW_MS - 30 * MINUTE_MS,
            NOW_MS,
        );
        assert_eq!(picked, Some(NOW_MS - 30 * MINUTE_MS));
        assert_eq!(code, None);
    }

    #[test]
    fn mosaic_covers_gates_the_conus_envelope() {
        let bounds = (-130.0, -60.0, 20.0, 55.0);
        assert!(mosaic_covers_core(
            Some(&[-100.0, -90.0, 35.0, 45.0]),
            bounds
        )); // Kansas
        assert!(mosaic_covers_core(
            Some(&[-125.0, -65.0, 25.0, 50.0]),
            bounds
        )); // CONUS
        assert!(mosaic_covers_core(
            Some(&[-180.0, 180.0, -90.0, 90.0]),
            bounds
        )); // world
        assert!(!mosaic_covers_core(Some(&[0.0, 20.0, 40.0, 60.0]), bounds)); // Europe
        assert!(!mosaic_covers_core(
            Some(&[100.0, 140.0, 20.0, 45.0]),
            bounds
        )); // east Asia
        assert!(!mosaic_covers_core(
            Some(&[-60.0, -40.0, -30.0, -10.0]),
            bounds
        )); // S. America
        assert!(!mosaic_covers_core(None, bounds)); // no view, no request
                                                    // A view is exactly four numbers; anything else is not one.
        assert!(!mosaic_covers_core(Some(&[-100.0, -90.0, 35.0]), bounds));
        assert!(!mosaic_covers_core(
            Some(&[-100.0, -90.0, 35.0, 45.0, 1.0]),
            bounds
        ));
        assert!(!mosaic_covers_core(Some(&[]), bounds));
    }

    #[test]
    fn mosaic_frame_size_clamps_to_the_pixel_bounds() {
        assert_eq!(mosaic_frame_size_core(1024, 512, 256, 4096), (1024, 512));
        assert_eq!(
            mosaic_frame_size_core(99999, 99999, 256, 4096),
            (4096, 4096)
        );
        assert_eq!(mosaic_frame_size_core(1, 1, 256, 4096), (256, 256));
        assert_eq!(mosaic_frame_size_core(255, 4097, 256, 4096), (256, 4096));
        assert_eq!(mosaic_frame_size_core(256, 4096, 256, 4096), (256, 4096));
    }

    #[test]
    fn service_exception_text_lifts_the_enclosed_message() {
        let payload = concat!(
            "<?xml version=\"1.0\"?><ServiceExceptionReport>",
            "<ServiceException code=\"LayerNotDefined\">\n",
            "  Could not find layer conus_bogus\n",
            "</ServiceException></ServiceExceptionReport>",
        )
        .as_bytes();
        assert_eq!(
            service_exception_text_core(payload, 4096),
            "Could not find layer conus_bogus"
        );
        // The Report wrapper alone is not an exception.
        assert_eq!(
            service_exception_text_core(b"<ServiceExceptionReport/>", 4096),
            ""
        );
        assert_eq!(
            service_exception_text_core(b"<html>Service unavailable</html>", 4096),
            ""
        );
        // Matching is case-insensitive on both tags, the closing tag
        // may carry trailing whitespace, and newlines collapse.
        assert_eq!(
            service_exception_text_core(b"<serviceexception>\n oops \n</SERVICEEXCEPTION  >", 4096),
            "oops"
        );
        // The content is the first exception only, with its markup
        // passed through like any other text.
        assert_eq!(
            service_exception_text_core(
                b"<ServiceException>first</ServiceException><ServiceException>second</ServiceException>",
                4096
            ),
            "first"
        );
        assert_eq!(
            service_exception_text_core(
                b"<ServiceException>bad <b>xml</b> here</ServiceException>",
                4096
            ),
            "bad <b>xml</b> here"
        );
        // Invalid UTF-8 decodes lossily, one replacement per bad byte,
        // exactly like Python's errors="replace".
        assert_eq!(
            service_exception_text_core(
                b"<ServiceException>\xff\xfe broken</ServiceException>",
                4096
            ),
            "\u{fffd}\u{fffd} broken"
        );
        // Only the bounded prefix is inspected.
        let payload = b"<ServiceException>hi</ServiceException>";
        assert_eq!(service_exception_text_core(payload, 5), "");
        assert_eq!(service_exception_text_core(payload, payload.len()), "hi");
        assert_eq!(service_exception_text_core(payload, usize::MAX), "hi");
        assert_eq!(service_exception_text_core(b"", 4096), "");
        // The message truncates at 300 characters.
        let mut long = b"<ServiceException>".to_vec();
        long.extend(std::iter::repeat_n(b'x', 400));
        long.extend_from_slice(b"</ServiceException>");
        let lifted = service_exception_text_core(&long, 4096);
        assert_eq!(lifted.len(), 300);
        assert_eq!(lifted.chars().count(), 300);
        assert_eq!(lifted, "x".repeat(300));
    }

    #[test]
    fn site_bounds_clamps_to_the_valid_coordinate_ranges() {
        assert_eq!(
            site_bounds_core(35.0, -97.0, 5.0),
            (-102.0, -92.0, 30.0, 40.0)
        );
        // The antimeridian clamps the eastern edge at 180.
        assert_eq!(site_bounds_core(0.0, 179.0, 5.0), (174.0, 180.0, -5.0, 5.0));
        // The poles clamp the latitude edges.
        assert_eq!(site_bounds_core(88.0, 0.0, 5.0), (-5.0, 5.0, 83.0, 90.0));
        assert_eq!(site_bounds_core(-88.0, 0.0, 5.0), (-5.0, 5.0, -90.0, -83.0));
    }

    #[test]
    fn site_distance_km_uses_the_great_circle() {
        // Verified against the Python haversine with the same inputs.
        let oklahoma_city = site_distance_km_core(35.333, -97.279, 45.5, -73.6);
        assert!((oklahoma_city - 2_288.848_377_592_081_8).abs() < 1.0e-6);
        let nearby = site_distance_km_core(35.333, -97.279, 35.0, -97.0);
        assert!((nearby - 44.880_331_860_566_23).abs() < 1.0e-6);
        // Antipodal on the equator is half the circumference, where
        // the min(1.0, sqrt(a)) guard pins asin at its maximum.
        let halfway = site_distance_km_core(0.0, 0.0, 0.0, 180.0);
        assert!((halfway - std::f64::consts::PI * 6371.0).abs() < 1.0e-6);
        assert_eq!(site_distance_km_core(45.5, -73.6, 45.5, -73.6), 0.0);
        // The measure is symmetric.
        let forward = site_distance_km_core(45.5, -73.6, 35.0, -97.0);
        let backward = site_distance_km_core(35.0, -97.0, 45.5, -73.6);
        assert!((forward - backward).abs() < 1.0e-9);
    }

    #[test]
    fn site_nearest_index_skips_unsupported_sites_and_respects_the_range() {
        let sites: Vec<(f64, f64, bool)> = vec![
            (45.0, -73.0, true),
            (35.0, -97.0, true),
            (61.0, -150.0, true),
        ];
        let (index, distance) = site_nearest_index_core(45.5, -73.6, &sites, 460.0).unwrap();
        assert_eq!(index, 0);
        assert!(distance < 100.0, "distance {distance}");

        // The closest antenna is skipped when it does not publish the
        // wanted product.
        let sites = vec![(45.0, -73.0, false), (45.0, -75.0, true)];
        let (index, _) = site_nearest_index_core(45.5, -73.6, &sites, 460.0).unwrap();
        assert_eq!(index, 1);
        // An all-unsupported catalogue and an empty one have no answer.
        let sites = vec![(45.0, -73.0, false)];
        assert_eq!(site_nearest_index_core(45.5, -73.6, &sites, 460.0), None);
        assert_eq!(site_nearest_index_core(45.5, -73.6, &[], 460.0), None);
        // Beyond the useful range there is no site either.
        let sites = vec![(44.0, -75.0, true)];
        assert_eq!(site_nearest_index_core(45.5, -73.6, &sites, 100.0), None);
        // The range boundary is inclusive: a zero-distance site is
        // within a zero budget.
        let sites = vec![(45.5, -73.6, true)];
        assert_eq!(
            site_nearest_index_core(45.5, -73.6, &sites, 0.0),
            Some((0, 0.0))
        );
        // Two sites at exactly the same distance keep the first one.
        let sites = vec![(45.5, -72.6, true), (45.5, -74.6, true)];
        let (index, distance) = site_nearest_index_core(45.5, -73.6, &sites, 460.0).unwrap();
        assert_eq!(index, 0);
        assert!(distance > 70.0 && distance < 85.0, "distance {distance}");
    }

    #[test]
    fn site_frame_size_matches_the_site_aspect() {
        // A ten-degree box is square in degrees, so the pixel budget
        // lands on both axes -- the radar-history suite's 4096x4096.
        let tlx = (-102.0, -92.0, 30.0, 40.0);
        assert_eq!(site_frame_size_core(tlx, 4096, 256), (4096, 4096));
        assert_eq!(site_frame_size_core(tlx, 2048, 256), (2048, 2048));
        // Wide boxes scale the height, tall ones the width.
        let wide = (-110.0, -90.0, 35.0, 40.0);
        assert_eq!(site_frame_size_core(wide, 2048, 256), (2048, 512));
        assert_eq!(site_frame_size_core(wide, 300, 256), (300, 256));
        assert_eq!(site_frame_size_core(wide, 300, 64), (300, 75));
        let tall = (-100.0, -95.0, 30.0, 50.0);
        assert_eq!(site_frame_size_core(tall, 2048, 256), (512, 2048));
        // Python's round() breaks exact ties to even.
        let degenerate = (0.0, 10.0, 0.0, 1.0);
        assert_eq!(site_frame_size_core(degenerate, 5, 0), (5, 0));
        assert_eq!(site_frame_size_core(degenerate, 15, 0), (15, 2));
        assert_eq!(site_frame_size_core(degenerate, 25, 0), (25, 2));
        assert_eq!(site_frame_size_core(degenerate, 35, 0), (35, 4));
        // A collapsed edge floors at min_px instead of dividing by zero.
        let clamped = (5.0, 5.0, 0.0, 10.0);
        assert_eq!(site_frame_size_core(clamped, 2048, 256), (256, 2048));
        // A zero-height tie floors at min_px as well.
        assert_eq!(site_frame_size_core(degenerate, 5, 256), (5, 256));
    }
}

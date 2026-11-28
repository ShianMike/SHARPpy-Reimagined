//! Native NWS local storm report rules (milestone M34).
//!
//! Rust ports of the pure halves of ``sharpmod.providers.storm_reports``:
//!
//! * ``StormReport.magnitude_text`` ([`report_magnitude_text`]) and
//!   ``StormReport.is_significant`` ([`report_is_significant`]);
//! * ``normalise_report_filter`` ([`report_normalise_filter`]);
//! * ``report_window_label`` ([`report_window_label`]);
//! * ``_report_count_label`` ([`report_count_label`]);
//! * ``marker_radius`` ([`report_marker_radius`]) and ``_marker_ring``
//!   ([`report_marker_ring`]);
//! * ``_normalise_view`` ([`report_normalise_view`]) and ``build_url``
//!   ([`report_build_url`]);
//! * ``_cache_key`` ([`report_cache_key`]);
//! * ``window_is_settled`` ([`report_window_is_settled`]);
//! * the ``keep`` predicate of ``filter_report_layer``
//!   ([`report_layer_shape_matches`]).
//!
//! Python ``timedelta`` windows cross the boundary as plain seconds and
//! the deep-archive ``around`` moment as an epoch, so ``time`` is only
//! consulted for the UTC calendar fields the IEM service expects.  Rounding
//! follows Python exactly: ``round()`` ties to even via
//! ``f64::round_ties_even``, and ``:.2f``/``:.0f``/``:.4f`` match Python's
//! float formatting digit for digit.  The query string is built in the
//! provider's exact parameter order and percent-encoded the way
//! ``urllib.parse.urlencode`` does, and the cache key is the same SHA-256
//! prefix ``hashlib`` produced.  Hazard metadata, the CSV parser,
//! downloads, and the caches live elsewhere; every rule here is pure.

use pyo3::prelude::*;
use ring::digest::{digest, SHA256};
use std::f64::consts::PI;
use time::OffsetDateTime;

// ---------------------------------------------------------------------------
// module constants (sharpmod.providers.storm_reports)
// ---------------------------------------------------------------------------

/// The IEM local storm report query service (``_BASE``).
const LSR_BASE: &str = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py";

/// How far back to ask for reports when a caller does not say
/// (``DEFAULT_WINDOW``: six hours).
const DEFAULT_WINDOW_S: f64 = 21_600.0;

/// The 15-minute floor on any query span.
const MIN_WINDOW_S: f64 = 900.0;

/// The ``recent`` cap the service documents.
const RECENT_CAP_S: i64 = 999_999;

/// How far past a window's end it must be before that window counts as
/// settled (``ARCHIVE_SETTLED_AFTER``: six hours).
const ARCHIVE_SETTLED_AFTER_S: f64 = 21_600.0;

/// Geographic radius retained for shape bounds and spatial queries, as a
/// fraction of the view span (``MARKER_SPAN_FRACTION``).
const MARKER_SPAN_FRACTION: f64 = 0.004;

/// Bounds on the retained geographic marker radius.
const MIN_MARKER_DEG: f64 = 0.02;
const MAX_MARKER_DEG: f64 = 0.30;

/// Vertices in the retained geographic ring (``_MARKER_VERTICES``).
const MARKER_VERTICES: i32 = 16;

/// ``REPORT_FILTER_LABELS``: user-facing filter names by normalised key.
fn report_filter_label(key: &str) -> Option<&'static str> {
    match key {
        "all" => Some("All convective hazards"),
        "tornado" => Some("Tornado"),
        "hail" => Some("Hail"),
        "wind" => Some("Wind + wind damage"),
        "significant" => Some("Significant hail/wind"),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// shared helpers
// ---------------------------------------------------------------------------

/// Python's ``f"{value:g}"`` (C ``%g``, six significant digits): plain
/// decimal notation from ``1e-4`` up to ``1e6``, scientific notation with a
/// two-digit exponent outside that band, and trailing zeros stripped.
fn format_g(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".to_string()
        } else {
            "0".to_string()
        };
    }
    let negative = value < 0.0;
    let magnitude = value.abs();
    let scientific = format!("{magnitude:.5e}");
    let (mantissa, exponent_text) = match scientific.split_once('e') {
        Some(parts) => parts,
        None => return scientific,
    };
    let exponent: i32 = match exponent_text.parse() {
        Ok(exponent) => exponent,
        Err(_) => return scientific,
    };
    let body = if !(-4..6).contains(&exponent) {
        let trimmed = mantissa.trim_end_matches('0').trim_end_matches('.');
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{trimmed}e{sign}{:02}", exponent.abs())
    } else {
        let decimals = (5 - exponent).max(0) as usize;
        let plain = format!("{magnitude:.decimals$}");
        if plain.contains('.') {
            plain
                .trim_end_matches('0')
                .trim_end_matches('.')
                .to_string()
        } else {
            // An integer rendering keeps its trailing zeros: 123450 is
            // already the shortest %g form.
            plain
        }
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}

/// The bounded query span behind ``build_url`` and ``window_is_settled``:
/// ``max(timedelta(minutes=15), window)``.  A non-finite window cannot be
/// expressed as a Python ``timedelta`` at all and degrades to the floor.
fn bounded_span(window_seconds: f64) -> f64 {
    if !window_seconds.is_finite() || window_seconds < MIN_WINDOW_S {
        MIN_WINDOW_S
    } else {
        window_seconds
    }
}

/// UTC calendar fields the IEM service expects in its ``year1``/``month1``/
/// ``day1``/``hour1``/``minute1`` parameters.
struct UtcParts {
    year: i32,
    month: u8,
    day: u8,
    hour: u8,
    minute: u8,
}

/// Decompose one epoch into UTC calendar parts, or ``None`` when the
/// instant is non-finite or lies outside the representable range.
fn utc_parts(epoch: f64) -> Option<UtcParts> {
    if !epoch.is_finite() {
        return None;
    }
    let moment = OffsetDateTime::from_unix_timestamp(epoch.floor() as i64).ok()?;
    Some(UtcParts {
        year: moment.year(),
        month: u8::from(moment.month()),
        day: moment.day(),
        hour: moment.hour(),
        minute: moment.minute(),
    })
}

/// Percent-encode one value the way ``urllib.parse.urlencode`` does:
/// alphanumerics and ``_.-~`` pass, spaces become ``+``, and anything else
/// becomes ``%XX`` with uppercase hex.  The values built here are numeric
/// and bounding-box strings, so in practice nothing needs escaping.
fn quote_plus(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for &byte in value.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                encoded.push(byte as char);
            }
            b' ' => encoded.push('+'),
            other => encoded.push_str(&format!("%{other:02X}")),
        }
    }
    encoded
}

// ---------------------------------------------------------------------------
// report text (magnitude_text, is_significant)
// ---------------------------------------------------------------------------

/// The pure half of ``StormReport.magnitude_text``: the magnitude the way a
/// forecaster would say it.  Hail keeps two decimals with bare units,
/// gusts round to whole miles per hour, and anything ungraded prints
/// through ``%g``; a missing magnitude prints nothing.
pub(crate) fn magnitude_text_core(magnitude: Option<f64>, units: &str) -> String {
    let Some(magnitude) = magnitude else {
        return String::new();
    };
    match units {
        "in" => format!("{magnitude:.2} in"),
        "mph" => format!("{magnitude:.0} mph"),
        _ => format_g(magnitude),
    }
}

/// The pure half of ``StormReport.is_significant``: whether SPC would plot
/// this report as significant severe.  A report without a magnitude is
/// never significant, and neither is one whose hazard carries no threshold
/// (a significant tornado is EF2+, a damage survey's verdict days later).
pub(crate) fn is_significant_core(magnitude: Option<f64>, significant_at: Option<f64>) -> bool {
    significant_at.is_some_and(|threshold| magnitude.is_some_and(|value| value >= threshold))
}

// ---------------------------------------------------------------------------
// filters and labels (normalise_report_filter, report_window_label,
// _report_count_label)
// ---------------------------------------------------------------------------

/// The pure half of ``normalise_report_filter``: a supported hazard-filter
/// key, defaulting safely to ``all``.  Aliases resolve, underscores become
/// dashes, and anything unknown answers ``all``.
pub(crate) fn normalise_filter_core(value: Option<&str>) -> String {
    // Python: str(value or "all")
    let raw = match value {
        Some(text) if !text.is_empty() => text,
        _ => "all",
    };
    // Python: .strip().lower().replace("_", "-")
    let cleaned = raw.trim().to_lowercase().replace('_', "-");
    // Python: aliases.get(key, key)
    let aliased = match cleaned.as_str() {
        "t" => "tornado",
        "h" => "hail",
        "g" | "d" => "wind",
        "sig" | "significant-severe" => "significant",
        other => other,
    };
    if report_filter_label(aliased).is_some() {
        aliased.to_string()
    } else {
        "all".to_string()
    }
}

/// The pure half of ``report_window_label``: a symmetric total query span
/// described as a compact half-window.
///
/// A missing (or, here, unusable) window falls back to the default six
/// hours exactly as Python's ``window or DEFAULT_WINDOW`` treats a
/// zero-length span; the span is then floored to fifteen minutes, halved
/// into minutes with ties rounding to even, and folded to whole hours
/// whenever the half-window divides evenly.
pub(crate) fn window_label_core(window_seconds: Option<f64>) -> String {
    let requested = match window_seconds {
        Some(seconds) if seconds != 0.0 && seconds.is_finite() => seconds,
        _ => DEFAULT_WINDOW_S,
    };
    let span = bounded_span(requested);
    let half_minutes = (span / 120.0).round_ties_even() as i64;
    if half_minutes % 60 == 0 {
        let hours = half_minutes / 60;
        if hours == 1 {
            "\u{00b1}1 hour".to_string()
        } else {
            format!("\u{00b1}{hours} hours")
        }
    } else {
        format!("\u{00b1}{half_minutes} min")
    }
}

/// The pure half of ``_report_count_label``: a count and its optional
/// filter named in one compact caption unit.
///
/// ``filter_key`` is a value produced by [`normalise_filter_core`].  A key
/// outside ``REPORT_FILTER_LABELS`` raises ``KeyError`` in Python -- which
/// callers there avoid by normalising first -- where the Rust port answers
/// with the bare noun instead of panicking.
pub(crate) fn count_label_core(filter_key: &str, count: i64) -> String {
    let noun = if count == 1 {
        "storm report"
    } else {
        "storm reports"
    };
    match (report_filter_label(filter_key), filter_key) {
        // Python checks "all" before the lookup and returns the bare noun.
        (Some(_), "all") => noun.to_string(),
        (Some(label), _) => format!("{noun} ({label})"),
        (None, _) => noun.to_string(),
    }
}

// ---------------------------------------------------------------------------
// marker geometry (marker_radius, _marker_ring)
// ---------------------------------------------------------------------------

/// The pure half of ``marker_radius``: a geographic query radius, as a
/// fraction of the view span and clamped for drawable markers.  The visible
/// symbols use screen pixels; only the retained geometry follows this.
pub(crate) fn marker_radius_core(span_deg: Option<f64>) -> f64 {
    let span = match span_deg {
        Some(span) if span.is_finite() && span > 0.0 => span,
        // A continental view, the common default.
        _ => 60.0,
    };
    MIN_MARKER_DEG.max(MAX_MARKER_DEG.min(span * MARKER_SPAN_FRACTION))
}

/// The pure half of ``_marker_ring``: a small closed ring standing in for a
/// point.  Longitude is stretched by ``1/cos(latitude)`` (floored at 0.2 so
/// the poles stay bounded) so the marker stays visibly round on a
/// plate-carree map; the ring closes with a repeat of its first vertex.
pub(crate) fn marker_ring_core(lat: f64, lon: f64, radius: f64) -> Vec<(f64, f64)> {
    let stretch = 1.0 / lat.to_radians().cos().max(0.2);
    let mut ring = Vec::with_capacity(MARKER_VERTICES as usize + 1);
    for index in 0..MARKER_VERTICES {
        let angle = 2.0 * PI * f64::from(index) / f64::from(MARKER_VERTICES);
        ring.push((
            lon + radius * stretch * angle.cos(),
            lat + radius * angle.sin(),
        ));
    }
    let first = ring[0];
    ring.push(first);
    ring
}

// ---------------------------------------------------------------------------
// query construction (_normalise_view, build_url, _cache_key)
// ---------------------------------------------------------------------------

/// The pure half of ``_normalise_view``: a service-safe bounding box, or
/// ``None`` for an unusable view.
///
/// Exactly four finite coordinates are required; the pairs are sorted, the
/// range checks are strict (``west < east``, ``south < north``), and the
/// four-decimal formatting must not collapse the box afterwards -- a
/// highly zoomed extent can still shrink onto one rounded edge, which the
/// service would reject with HTTP 422.
pub(crate) fn normalise_view_core(
    view: Option<&[f64]>,
) -> Option<(String, String, String, String)> {
    let view = view?;
    if view.len() != 4 {
        return None;
    }
    let (lon0, lon1, lat0, lat1) = (view[0], view[1], view[2], view[3]);
    if !(lon0.is_finite() && lon1.is_finite() && lat0.is_finite() && lat1.is_finite()) {
        return None;
    }
    let (west, east) = if lon0 <= lon1 {
        (lon0, lon1)
    } else {
        (lon1, lon0)
    };
    let (south, north) = if lat0 <= lat1 {
        (lat0, lat1)
    } else {
        (lat1, lat0)
    };
    if !(-180.0 <= west
        && west < east
        && east <= 180.0
        && -90.0 <= south
        && south < north
        && north <= 90.0)
    {
        return None;
    }
    let formatted = (
        format!("{west:.4}"),
        format!("{east:.4}"),
        format!("{south:.4}"),
        format!("{north:.4}"),
    );
    let west_back: f64 = formatted.0.parse().ok()?;
    let east_back: f64 = formatted.1.parse().ok()?;
    let south_back: f64 = formatted.2.parse().ok()?;
    let north_back: f64 = formatted.3.parse().ok()?;
    if !(west_back < east_back && south_back < north_back) {
        return None;
    }
    Some(formatted)
}

/// The window edges of a dated (deep-archive) query, or ``None`` when the
/// moment cannot be decomposed and the caller must fall back to the
/// recency form.
fn dated_edges(span: f64, around: f64) -> Option<(UtcParts, UtcParts)> {
    let half = span / 2.0;
    Some((utc_parts(around - half)?, utc_parts(around + half)?))
}

/// The pure half of ``build_url``: the query for a window of reports,
/// optionally within ``view``.
///
/// Parameters run in the provider's exact order: ``justcsv``, then either
/// ``recent`` (whole seconds back from now, floored to fifteen minutes and
/// capped at 999999) or the ``year1``/``month1``/``day1``/``hour1``/
/// ``minute1`` ... ``minute2`` fields centred on ``around_epoch``, then
/// the optional ``west``/``east``/``south``/``north`` bounding box.  The
/// service's own hazard filter is deliberately never used -- measured
/// against the live feed it answers 200 OK with no rows -- and the query
/// string is percent-encoded exactly like ``urllib.parse.urlencode``.  A
/// non-finite or unrepresentable ``around_epoch`` degrades to the recency
/// query, since no dated window can be built from it.
pub(crate) fn build_url_core(
    window_seconds: f64,
    view: Option<&[f64]>,
    around_epoch: Option<f64>,
) -> String {
    let span = bounded_span(window_seconds);
    let mut params: Vec<(String, String)> = vec![("justcsv".to_string(), "1".to_string())];
    let dated = around_epoch
        .filter(|around| around.is_finite())
        .and_then(|around| dated_edges(span, around));
    match dated {
        None => {
            // The recency form: whole seconds back, floored and capped as
            // the service requires.
            let seconds = span as i64;
            params.push(("recent".to_string(), seconds.min(RECENT_CAP_S).to_string()));
        }
        Some((start, end)) => {
            for (index, edge) in [(1usize, start), (2usize, end)] {
                params.extend([
                    (format!("year{index}"), edge.year.to_string()),
                    (format!("month{index}"), edge.month.to_string()),
                    (format!("day{index}"), edge.day.to_string()),
                    (format!("hour{index}"), edge.hour.to_string()),
                    (format!("minute{index}"), edge.minute.to_string()),
                ]);
            }
        }
    }
    if let Some((west, east, south, north)) = normalise_view_core(view) {
        params.extend([
            ("west".to_string(), west),
            ("east".to_string(), east),
            ("south".to_string(), south),
            ("north".to_string(), north),
        ]);
    }
    let query = params
        .iter()
        .map(|(key, value)| format!("{}={}", quote_plus(key), quote_plus(value)))
        .collect::<Vec<_>>()
        .join("&");
    format!("{LSR_BASE}?{query}")
}

/// The pure half of ``_cache_key``: a filesystem-safe identity for a
/// request.  The query carries a bounding box with four decimal places and
/// a date, so it is hashed rather than transcribed: the first 32 lowercase
/// hex characters of the URL's SHA-256, exactly as ``hashlib`` produced.
pub(crate) fn cache_key_core(url: &str) -> String {
    let digest = digest(&SHA256, url.as_bytes());
    let mut hex = String::with_capacity(32);
    for byte in &digest.as_ref()[..16] {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

// ---------------------------------------------------------------------------
// caching judgement (window_is_settled)
// ---------------------------------------------------------------------------

/// The pure half of ``window_is_settled``: whether this window has closed
/// long enough ago to be worth keeping on disk.
///
/// A live request never is, by definition -- a missing (or unusable)
/// ``around`` means the window is still moving.  A past one is, once
/// late-arriving reports have had ``ARCHIVE_SETTLED_AFTER`` to turn up
/// after its end.
pub(crate) fn window_is_settled_core(
    window_seconds: f64,
    around_epoch: Option<f64>,
    now_epoch: f64,
) -> bool {
    let Some(around) = around_epoch.filter(|around| around.is_finite()) else {
        return false;
    };
    let ends = around + bounded_span(window_seconds) / 2.0;
    (now_epoch - ends) >= ARCHIVE_SETTLED_AFTER_S
}

// ---------------------------------------------------------------------------
// layer filtering (filter_report_layer's keep predicate)
// ---------------------------------------------------------------------------

/// The pure half of ``filter_report_layer``'s ``keep`` predicate: whether
/// one marker shape survives a UI hazard filter.
///
/// ``filter_key`` is a value produced by [`normalise_filter_core`], and
/// the fall-through branch is the wind filter, exactly as Python's final
/// ``return`` serves every key the earlier branches did not claim.
/// ``category`` is the shape's lowercased ``marker_category``
/// (``"t"``, ``"g"``, ``"d"``, ``"h"``, or ``"significant"``), ``label``
/// its display text, and ``significant`` its ``marker_significant`` flag.
pub(crate) fn layer_shape_matches_core(
    filter_key: &str,
    category: &str,
    label: &str,
    significant: bool,
) -> bool {
    let category = category.to_lowercase();
    let label = label.to_lowercase();
    match filter_key {
        "all" => true,
        "significant" => significant,
        "tornado" => category == "t",
        "hail" => (category == "h" || category == "significant") && label.contains("hail"),
        _ => {
            (category == "g" || category == "d" || category == "significant")
                && (label.contains("wind") || label.contains("gust"))
        }
    }
}

// ---------------------------------------------------------------------------
// Python bindings
// ---------------------------------------------------------------------------

/// Return the magnitude the way a forecaster would say it.
///
/// ``units`` selects the format: ``"in"`` keeps two decimals (``"3.25 in"``),
/// ``"mph"`` rounds to whole gusts (``"54 mph"``), and anything else
/// prints through ``%g``.  A missing magnitude prints the empty string.
/// Rounding ties to even exactly like Python's ``:.2f``/``:.0f``.
#[pyfunction]
fn report_magnitude_text(magnitude: Option<f64>, units: &str) -> String {
    magnitude_text_core(magnitude, units)
}

/// Report whether SPC would plot this report as significant severe.
///
/// Both the magnitude and the hazard's ``significant_at`` threshold must be
/// present, and the magnitude must reach the threshold; anything else --
/// including a NaN magnitude -- answers ``False``.
#[pyfunction]
fn report_is_significant(magnitude: Option<f64>, significant_at: Option<f64>) -> bool {
    is_significant_core(magnitude, significant_at)
}

/// Return a supported hazard-filter key, defaulting safely to ``all``.
///
/// The value is trimmed, lowercased, and has underscores replaced by
/// dashes; the single-letter and ``sig`` aliases resolve to their filter;
/// anything not in ``REPORT_FILTER_LABELS`` answers ``all``.
#[pyfunction]
fn report_normalise_filter(value: Option<&str>) -> String {
    normalise_filter_core(value)
}

/// Describe a symmetric total query span as a compact half-window.
///
/// ``window_seconds`` is the total span: ``None`` or zero means the
/// default six hours (``"\u{00b1}3 hours"``), the span is floored to
/// fifteen minutes, and the half-window folds to whole hours whenever it
/// divides evenly (``"\u{00b1}1 hour"``, ``"\u{00b1}90 min"``).  Halving
/// rounds ties to even, so the fifteen-minute floor reads
/// ``"\u{00b1}8 min"``.
#[pyfunction]
fn report_window_label(window_seconds: Option<f64>) -> String {
    window_label_core(window_seconds)
}

/// Name a count and its optional filter in one compact caption unit.
///
/// The count pluralises the noun, and any filter key other than ``all``
/// appends its ``REPORT_FILTER_LABELS`` name: ``"2 storm reports (Wind +
/// wind damage)"``.
#[pyfunction]
fn report_count_label(filter_key: &str, count: i64) -> String {
    count_label_core(filter_key, count)
}

/// Return a geographic query radius for one view span.
///
/// A missing, non-finite, or non-positive span falls back to the
/// continental default of 60 degrees; the radius is the span times
/// ``0.004``, clamped to ``[0.02, 0.30]``.  Visible markers use screen
/// pixels; this radius feeds only spatial queries and bounds.
#[pyfunction]
fn report_marker_radius(span_deg: Option<f64>) -> f64 {
    marker_radius_core(span_deg)
}

/// Return a small closed ring standing in for a point.
///
/// Sixteen vertices plus the closing vertex, with longitude stretched by
/// ``1/cos(lat)`` (floored at ``1/0.2``) so the marker stays visibly round
/// away from the equator.  Each point is ``(lon, lat)``.
#[pyfunction]
fn report_marker_ring(lat: f64, lon: f64, radius: f64) -> Vec<(f64, f64)> {
    marker_ring_core(lat, lon, radius)
}

/// Return a service-safe bounding box for ``view``, or ``None``.
///
/// ``view`` must hold exactly four finite coordinates.  The pairs are
/// sorted, ``-180 <= west < east <= 180`` and ``-90 <= south < north <= 90``
/// must hold, and the four-decimal formatting must leave the box
/// non-degenerate; the result is the formatted
/// ``(west, east, south, north)`` strings.
#[pyfunction]
fn report_normalise_view(view: Option<Vec<f64>>) -> Option<(String, String, String, String)> {
    normalise_view_core(view.as_deref())
}

/// Return the query for a window of reports, optionally within ``view``.
///
/// ``window_seconds`` is the total span (floored to fifteen minutes).
/// Without ``around_epoch`` the query asks for ``recent`` whole seconds
/// back, capped at 999999; with it, the numeric ``year1``..``minute2``
/// fields centre the window on that moment, rolling dates and months as
/// needed.  A usable ``view`` appends the ``west``/``east``/``south``/
/// ``north`` bounding box.  The parameter order and percent-encoding
/// match ``urllib.parse.urlencode`` exactly.
#[pyfunction]
fn report_build_url(
    window_seconds: f64,
    view: Option<Vec<f64>>,
    around_epoch: Option<f64>,
) -> String {
    build_url_core(window_seconds, view.as_deref(), around_epoch)
}

/// Return a filesystem-safe identity for one request URL.
///
/// The first 32 lowercase hex characters of the URL's SHA-256, exactly as
/// Python's ``hashlib.sha256(url.encode()).hexdigest()[:32]`` produced.
#[pyfunction]
fn report_cache_key(url: &str) -> String {
    cache_key_core(url)
}

/// Report whether this window has closed long enough ago to be worth
/// keeping on disk.
///
/// A live request never is (``around_epoch`` of ``None``); a dated one is
/// once ``now_epoch`` sits at least six hours past the window's end.
#[pyfunction]
fn report_window_is_settled(
    window_seconds: f64,
    around_epoch: Option<f64>,
    now_epoch: f64,
) -> bool {
    window_is_settled_core(window_seconds, around_epoch, now_epoch)
}

/// Report whether one marker shape survives a UI hazard filter.
///
/// ``filter_key`` is a value produced by [`report_normalise_filter`].
/// ``"all"`` keeps everything, ``"significant"`` keeps flagged shapes,
/// ``"tornado"`` matches the lowercased category ``"t"``, and ``"hail"``
/// and the wind fall-through additionally require the label to name the
/// hazard (``"hail"`` or ``"wind"``/``"gust"``).
#[pyfunction]
fn report_layer_shape_matches(
    filter_key: &str,
    category: &str,
    label: &str,
    significant: bool,
) -> bool {
    layer_shape_matches_core(filter_key, category, label, significant)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(report_magnitude_text, module)?)?;
    module.add_function(wrap_pyfunction!(report_is_significant, module)?)?;
    module.add_function(wrap_pyfunction!(report_normalise_filter, module)?)?;
    module.add_function(wrap_pyfunction!(report_window_label, module)?)?;
    module.add_function(wrap_pyfunction!(report_count_label, module)?)?;
    module.add_function(wrap_pyfunction!(report_marker_radius, module)?)?;
    module.add_function(wrap_pyfunction!(report_marker_ring, module)?)?;
    module.add_function(wrap_pyfunction!(report_normalise_view, module)?)?;
    module.add_function(wrap_pyfunction!(report_build_url, module)?)?;
    module.add_function(wrap_pyfunction!(report_cache_key, module)?)?;
    module.add_function(wrap_pyfunction!(report_window_is_settled, module)?)?;
    module.add_function(wrap_pyfunction!(report_layer_shape_matches, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// ``Month`` from its one-based index.
    fn month_from_index(month: u8) -> Option<time::Month> {
        match month {
            1 => Some(time::Month::January),
            2 => Some(time::Month::February),
            3 => Some(time::Month::March),
            4 => Some(time::Month::April),
            5 => Some(time::Month::May),
            6 => Some(time::Month::June),
            7 => Some(time::Month::July),
            8 => Some(time::Month::August),
            9 => Some(time::Month::September),
            10 => Some(time::Month::October),
            11 => Some(time::Month::November),
            12 => Some(time::Month::December),
            _ => None,
        }
    }

    /// Epoch seconds for one UTC wall-clock moment.
    fn utc(year: i32, month: u8, day: u8, hour: u8, minute: u8) -> f64 {
        let date =
            time::Date::from_calendar_date(year, month_from_index(month).unwrap(), day).unwrap();
        let clock = time::Time::from_hms(hour, minute, 0).unwrap();
        time::PrimitiveDateTime::new(date, clock)
            .assume_utc()
            .unix_timestamp() as f64
    }

    #[test]
    fn magnitude_text_matches_the_units_the_feed_publishes() {
        assert_eq!(magnitude_text_core(Some(3.25), "in"), "3.25 in");
        assert_eq!(magnitude_text_core(Some(54.0), "mph"), "54 mph");
        assert_eq!(magnitude_text_core(Some(1.0), "in"), "1.00 in");
        assert_eq!(
            magnitude_text_core(Some(1.0), ""),
            "1",
            "an ungraded hazard prints %g"
        );
        assert_eq!(magnitude_text_core(None, "in"), "");
        assert_eq!(magnitude_text_core(None, "mph"), "");
    }

    #[test]
    fn magnitude_text_rounds_ties_to_even_like_python() {
        // 1.125 and 1.375 are exactly representable halfway values.
        assert_eq!(magnitude_text_core(Some(1.125), "in"), "1.12 in");
        assert_eq!(magnitude_text_core(Some(1.375), "in"), "1.38 in");
        assert_eq!(magnitude_text_core(Some(65.5), "mph"), "66 mph");
        assert_eq!(magnitude_text_core(Some(2.5), "mph"), "2 mph");
    }

    #[test]
    fn significance_requires_a_threshold_and_a_magnitude() {
        assert!(
            !is_significant_core(None, Some(2.0)),
            "a report with no magnitude is not significant"
        );
        assert!(
            !is_significant_core(Some(2.0), None),
            "a tornado is not graded on this map"
        );
        assert!(
            is_significant_core(Some(2.0), Some(2.0)),
            "the threshold is inclusive"
        );
        assert!(is_significant_core(Some(2.01), Some(2.0)));
        assert!(
            !is_significant_core(Some(1.0), Some(2.0)),
            "1 in hail is severe, not significant"
        );
        assert!(is_significant_core(Some(80.0), Some(65.0 * 1.15078)));
        assert!(
            !is_significant_core(Some(f64::NAN), Some(2.0)),
            "NaN never reaches a threshold"
        );
    }

    #[test]
    fn report_filters_normalise_aliases_and_default_to_all() {
        assert_eq!(normalise_filter_core(None), "all");
        assert_eq!(normalise_filter_core(Some("")), "all");
        assert_eq!(normalise_filter_core(Some("   ")), "all");
        assert_eq!(normalise_filter_core(Some("all")), "all");
        assert_eq!(normalise_filter_core(Some("T")), "tornado");
        assert_eq!(normalise_filter_core(Some(" h ")), "hail");
        assert_eq!(normalise_filter_core(Some("G")), "wind");
        assert_eq!(normalise_filter_core(Some("d")), "wind");
        assert_eq!(normalise_filter_core(Some("SIG")), "significant");
        assert_eq!(
            normalise_filter_core(Some("significant_severe")),
            "significant"
        );
        assert_eq!(
            normalise_filter_core(Some("significant-severe")),
            "significant"
        );
        assert_eq!(normalise_filter_core(Some("Wind")), "wind");
        assert_eq!(normalise_filter_core(Some("tornado")), "tornado");
        assert_eq!(normalise_filter_core(Some("nope")), "all");
    }

    #[test]
    fn window_labels_fold_to_hours_when_whole() {
        assert_eq!(window_label_core(None), "\u{00b1}3 hours");
        assert_eq!(window_label_core(Some(21_600.0)), "\u{00b1}3 hours");
        assert_eq!(window_label_core(Some(7_200.0)), "\u{00b1}1 hour");
        assert_eq!(window_label_core(Some(86_400.0)), "\u{00b1}12 hours");
        assert_eq!(window_label_core(Some(10_800.0)), "\u{00b1}90 min");
        assert_eq!(window_label_core(Some(1_800.0)), "\u{00b1}15 min");
        // The fifteen-minute floor halves to 7.5 minutes, which rounds
        // to even.
        assert_eq!(window_label_core(Some(900.0)), "\u{00b1}8 min");
        assert_eq!(window_label_core(Some(1.0)), "\u{00b1}8 min");
        assert_eq!(window_label_core(Some(-5.0)), "\u{00b1}8 min");
        // A zero-length window is falsy in Python and takes the default.
        assert_eq!(window_label_core(Some(0.0)), "\u{00b1}3 hours");
        assert_eq!(window_label_core(Some(f64::NAN)), "\u{00b1}3 hours");
    }

    #[test]
    fn count_labels_name_the_filter_in_the_caption_unit() {
        assert_eq!(count_label_core("all", 3), "storm reports");
        assert_eq!(count_label_core("all", 1), "storm report");
        assert_eq!(count_label_core("all", 0), "storm reports");
        assert_eq!(count_label_core("tornado", 1), "storm report (Tornado)");
        assert_eq!(count_label_core("hail", 2), "storm reports (Hail)");
        assert_eq!(
            count_label_core("wind", 2),
            "storm reports (Wind + wind damage)"
        );
        assert_eq!(
            count_label_core("significant", 5),
            "storm reports (Significant hail/wind)"
        );
    }

    #[test]
    fn marker_radius_spans_a_fraction_of_the_view() {
        assert_eq!(marker_radius_core(Some(60.0)), 60.0 * MARKER_SPAN_FRACTION);
        assert_eq!(marker_radius_core(None), 0.24);
        assert_eq!(marker_radius_core(Some(0.0)), 0.24);
        assert_eq!(marker_radius_core(Some(-5.0)), 0.24);
        assert_eq!(marker_radius_core(Some(f64::NAN)), 0.24);
        assert_eq!(marker_radius_core(Some(2.0)), MIN_MARKER_DEG);
        assert_eq!(marker_radius_core(Some(0.001)), MIN_MARKER_DEG);
        assert_eq!(marker_radius_core(Some(10_000.0)), MAX_MARKER_DEG);
        assert!(marker_radius_core(Some(2.0)) < marker_radius_core(Some(60.0)));
    }

    #[test]
    fn marker_rings_close_and_stay_round_away_from_the_equator() {
        let ring = marker_ring_core(60.0, -100.0, 0.1);
        assert_eq!(
            ring.len(),
            MARKER_VERTICES as usize + 1,
            "16 vertices plus the closing vertex"
        );
        assert_eq!(ring[16], ring[0]);
        // The first vertex sits on the zero-angle ray, stretched by
        // 1/cos(lat).
        let stretch = 1.0 / 60.0f64.to_radians().cos().max(0.2);
        assert_eq!(ring[0].0, -100.0 + 0.1 * stretch);
        assert_eq!(ring[0].1, 60.0);
        let lons: Vec<f64> = ring[..16].iter().map(|point| point.0).collect();
        let lats: Vec<f64> = ring[..16].iter().map(|point| point.1).collect();
        let lon_span = lons.iter().cloned().fold(f64::MIN, f64::max)
            - lons.iter().cloned().fold(f64::MAX, f64::min);
        let lat_span = lats.iter().cloned().fold(f64::MIN, f64::max)
            - lats.iter().cloned().fold(f64::MAX, f64::min);
        assert!(lon_span > lat_span, "longitude is stretched by 1/cos(lat)");
        // Near the pole the stretch clamps at 1/0.2.
        let polar = marker_ring_core(89.9, 0.0, 0.1);
        let polar_stretch = 1.0 / 89.9f64.to_radians().cos().max(0.2);
        assert_eq!(polar[0].0, 0.1 * polar_stretch);
        assert_eq!(polar[0].1, 89.9);
    }

    #[test]
    fn views_are_normalised_to_four_decimals_or_dropped() {
        let boxed = normalise_view_core(Some(&[-103.0, -94.0, 33.5, 37.5]));
        assert_eq!(
            boxed,
            Some((
                "-103.0000".to_string(),
                "-94.0000".to_string(),
                "33.5000".to_string(),
                "37.5000".to_string(),
            ))
        );
        // A reversed view is sorted before formatting.
        assert_eq!(
            normalise_view_core(Some(&[-94.0, -103.0, 37.5, 33.5])),
            Some((
                "-103.0000".to_string(),
                "-94.0000".to_string(),
                "33.5000".to_string(),
                "37.5000".to_string(),
            ))
        );
        // A near-degenerate but distinct box survives the rounding.
        assert_eq!(
            normalise_view_core(Some(&[-100.0, -99.99, 35.0, 35.001])),
            Some((
                "-100.0000".to_string(),
                "-99.9900".to_string(),
                "35.0000".to_string(),
                "35.0010".to_string(),
            ))
        );
        // Collapsed, collapsed-by-rounding, and out-of-range views are
        // omitted to avoid a provider 422.
        assert_eq!(normalise_view_core(None), None);
        assert_eq!(normalise_view_core(Some(&[0.0, 0.0, 0.0, 0.0])), None);
        assert_eq!(
            normalise_view_core(Some(&[-100.0, -90.0, 35.0, 35.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[-100.0, -100.0, 35.0, 40.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[-100.0, -99.99999, 35.0, 40.0])),
            None,
            "the rounding collapses this box onto one edge"
        );
        assert_eq!(
            normalise_view_core(Some(&[f64::NAN, -90.0, 35.0, 40.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[f64::INFINITY, -90.0, 35.0, 40.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[-181.0, -90.0, 35.0, 40.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[-100.0, 181.0, 35.0, 40.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[-100.0, -90.0, -91.0, 40.0])),
            None
        );
        assert_eq!(
            normalise_view_core(Some(&[-100.0, -90.0, 35.0, 91.0])),
            None
        );
        // Wrong-length views are unusable.
        assert_eq!(normalise_view_core(Some(&[1.0, 2.0, 3.0])), None);
        assert_eq!(normalise_view_core(Some(&[1.0, 2.0, 3.0, 4.0, 5.0])), None);
    }

    #[test]
    fn live_urls_ask_for_reports_back_from_now() {
        assert_eq!(
            build_url_core(21_600.0, None, None),
            format!("{LSR_BASE}?justcsv=1&recent=21600")
        );
        assert_eq!(
            build_url_core(10_800.0, None, None),
            format!("{LSR_BASE}?justcsv=1&recent=10800")
        );
        // The window is floored to a useful minimum and capped as the
        // service requires.
        assert!(build_url_core(1.0, None, None).ends_with("recent=900"));
        assert!(build_url_core(90.0 * 86_400.0, None, None).ends_with("recent=999999"));
        // The service's own hazard filter is deliberately never used.
        assert!(!build_url_core(21_600.0, None, None).contains("type="));
    }

    #[test]
    fn past_windows_are_expressed_with_the_numeric_date_fields() {
        let around = utc(2021, 5, 1, 18, 0);
        assert_eq!(
            build_url_core(21_600.0, None, Some(around)),
            format!(
                "{LSR_BASE}?justcsv=1&year1=2021&month1=5&day1=1&hour1=15&minute1=0\
                 &year2=2021&month2=5&day2=1&hour2=21&minute2=0"
            )
        );
        // Centred on the moment asked about, not leading up to it.
        let four_hour = build_url_core(14_400.0, None, Some(around));
        assert!(four_hour.contains("hour1=16"));
        assert!(four_hour.contains("hour2=20"));
        // A window crossing midnight rolls the date.
        let midnight = build_url_core(14_400.0, None, Some(utc(2021, 5, 1, 23, 0)));
        assert!(midnight.contains("day1=1"));
        assert!(midnight.contains("hour1=21"));
        assert!(midnight.contains("day2=2"));
        assert!(midnight.contains("hour2=1"));
        // A non-finite moment cannot anchor a dated window and degrades
        // to the recency query.
        assert!(build_url_core(21_600.0, None, Some(f64::NAN)).contains("recent="));
    }

    #[test]
    fn a_bounding_box_narrows_the_query_in_exact_order() {
        let conus = [-103.0, -94.0, 33.5, 37.5];
        let boxed = build_url_core(21_600.0, Some(&conus), None);
        assert_eq!(
            boxed,
            format!(
                "{LSR_BASE}?justcsv=1&recent=21600&west=-103.0000&east=-94.0000\
                 &south=33.5000&north=37.5000"
            )
        );
        let around = utc(2021, 5, 1, 18, 0);
        let dated_boxed = build_url_core(21_600.0, Some(&conus), Some(around));
        assert_eq!(
            dated_boxed,
            format!(
                "{LSR_BASE}?justcsv=1&year1=2021&month1=5&day1=1&hour1=15&minute1=0\
                 &year2=2021&month2=5&day2=1&hour2=21&minute2=0&west=-103.0000\
                 &east=-94.0000&south=33.5000&north=37.5000"
            )
        );
        // An unusable view is omitted rather than sent.
        let collapsed = [-100.0, -99.99999, 35.0, 40.0];
        assert!(!build_url_core(21_600.0, Some(&collapsed), None).contains("west="));
    }

    #[test]
    fn cache_keys_are_the_sha256_prefix_of_the_url() {
        // SHA-256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a3...
        assert_eq!(cache_key_core("abc"), "ba7816bf8f01cfea414140de5dae2223");
        assert_eq!(
            cache_key_core(
                "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py?justcsv=1&recent=21600"
            ),
            "c02a708764b536829155d863e68f7687"
        );
        assert_eq!(cache_key_core("abc").len(), 32);
        // Keys differ per URL, so separate queries never share an entry.
        assert_ne!(cache_key_core("a"), cache_key_core("b"));
    }

    #[test]
    fn settled_windows_have_closed_beyond_the_archive_lag() {
        let now = utc(2026, 9, 7, 6, 0);
        let past = utc(2021, 5, 1, 18, 0);
        assert!(window_is_settled_core(3_600.0, Some(past), now));
        assert!(
            !window_is_settled_core(3_600.0, None, now),
            "a live window is never settled"
        );
        // A window that just ended still expects late reports.
        let just_ended = now - 1_800.0;
        assert!(!window_is_settled_core(3_600.0, Some(just_ended), now));
        // The six-hour boundary is inclusive.
        let ends = 450.0; // around 0 with the fifteen-minute floor
        assert!(window_is_settled_core(
            900.0,
            Some(0.0),
            ends + ARCHIVE_SETTLED_AFTER_S
        ));
        assert!(!window_is_settled_core(
            900.0,
            Some(0.0),
            ends + ARCHIVE_SETTLED_AFTER_S - 0.001
        ));
        // A non-finite moment cannot be settled either.
        assert!(!window_is_settled_core(3_600.0, Some(f64::NAN), now));
    }

    #[test]
    fn the_layer_filter_matches_on_category_label_and_significance() {
        assert!(layer_shape_matches_core("all", "h", "Hail 1.00 in", false));
        assert!(layer_shape_matches_core("all", "", "", false));
        assert!(layer_shape_matches_core(
            "significant",
            "h",
            "Sig Hail 2.25 in",
            true
        ));
        assert!(!layer_shape_matches_core(
            "significant",
            "h",
            "Hail 1.00 in",
            false
        ));
        assert!(
            layer_shape_matches_core("tornado", "T", "Tornado", false),
            "the category is lowercased before comparison"
        );
        assert!(!layer_shape_matches_core(
            "tornado",
            "g",
            "Wind 54 mph",
            false
        ));
        assert!(!layer_shape_matches_core(
            "tornado",
            "significant",
            "Sig Wind 80 mph",
            true
        ));
        assert!(layer_shape_matches_core("hail", "h", "Hail 1.00 in", false));
        assert!(layer_shape_matches_core(
            "hail",
            "significant",
            "Sig Hail 2.25 in",
            true
        ));
        assert!(
            !layer_shape_matches_core("hail", "h", "Wind 54 mph", false),
            "the label must name hail"
        );
        assert!(!layer_shape_matches_core("hail", "g", "Wind 54 mph", false));
        assert!(layer_shape_matches_core("wind", "g", "Wind 54 mph", false));
        assert!(layer_shape_matches_core("wind", "d", "Wind damage", false));
        assert!(layer_shape_matches_core(
            "wind",
            "g",
            "Gust to 58 mph",
            false
        ));
        assert!(layer_shape_matches_core(
            "wind",
            "significant",
            "Sig Wind 80 mph",
            true
        ));
        assert!(!layer_shape_matches_core("wind", "t", "Tornado", false));
        // Any other key falls through to the wind filter, exactly like
        // Python's final return.
        assert!(layer_shape_matches_core(
            "anything-else",
            "g",
            "Wind 54 mph",
            false
        ));
        assert!(!layer_shape_matches_core(
            "anything-else",
            "h",
            "Hail 1.00 in",
            false
        ));
    }

    #[test]
    fn format_g_matches_the_feed_magnitudes() {
        assert_eq!(format_g(1.0), "1");
        assert_eq!(format_g(54.0), "54");
        assert_eq!(format_g(0.25), "0.25");
        assert_eq!(format_g(4.0), "4");
        assert_eq!(format_g(0.0), "0");
    }
}

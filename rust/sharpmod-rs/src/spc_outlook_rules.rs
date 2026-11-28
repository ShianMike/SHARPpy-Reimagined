//! Native SPC convective outlook resolution and styling rules (milestone
//! M33).
//!
//! Rust ports of the pure halves of ``sharpmod.providers.spc_outlook``:
//!
//! * ``cig_level`` ([`spc_cig_level`]) and ``is_qualifier_label``
//!   ([`spc_is_qualifier_label`]);
//! * the 12Z boundary arithmetic of ``convective_day_start``
//!   ([`spc_convective_day_start_epoch`]) and ``outlook_day_offset``
//!   ([`spc_outlook_day_offset`]), with instants carried as raw epoch
//!   seconds so pre-1970 timestamps stay exact;
//! * ``format_product_days`` ([`spc_format_product_days`]) and
//!   ``covers_location`` ([`spc_covers_location`]);
//! * ``candidates_for`` ([`spc_candidates_for`]): the archived issuance
//!   clock, the live endpoints and their pre-noon rollover rule, the
//!   newest-first ordering, and the candidate labels and URLs;
//! * ``_parse_stamp`` ([`spc_parse_stamp_epoch`]);
//! * ``_feature_style`` ([`spc_feature_style`]), ``_display_label``
//!   ([`spc_display_label`]) and ``_colour`` ([`spc_validate_colour`]).
//!
//! Everything Python does with tz-aware ``datetime`` objects happens on
//! Unix epoch seconds here; ``time`` is consulted only for calendar
//! decomposition (archive-year cutoffs, ``YYYYMMDDHHMM`` stamps, and URL
//! dates).  Rounding follows Python exactly: ``round()`` ties to even via
//! ``f64::round_ties_even``, probability labels render through the ``%g``
//! port mirrored from ``profile_and_progress_rules``, and unknown palette
//! or category fields fall back exactly where Python falls back.  Network
//! access, the caches, and the GeoJSON decode live elsewhere; every rule
//! here is pure.

use pyo3::prelude::*;
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time};

// ---------------------------------------------------------------------------
// module constants (sharpmod.providers.spc_outlook)
// ---------------------------------------------------------------------------

/// Live and archived GeoJSON root (``_BASE``).
const BASE: &str = "https://www.spc.noaa.gov/products/outlook";

/// The categorical GeoJSON archive starts here (``ARCHIVE_FIRST_YEAR``).
const ARCHIVE_FIRST_YEAR: i32 = 2020;

/// Seconds of one UTC day.
const DAY_S: i64 = 86_400;

/// SPC convective days are bounded by 12Z, not local midnight
/// (``CONVECTIVE_DAY_START_HOUR``).
const CONVECTIVE_DAY_START_S: i64 = 12 * 3_600;

/// Outlook days this module can resolve (``SUPPORTED_DAYS``).  Days 4-8
/// publish no ``_cat`` GeoJSON.
const SUPPORTED_DAYS: [i64; 3] = [1, 2, 3];

/// Upper bound on candidate URLs tried for one valid time
/// (``MAX_CANDIDATES``).
const MAX_CANDIDATES: usize = 12;

/// Approximate envelope the convective outlooks cover, as
/// ``(lon0, lon1, lat0, lat1)`` (``COVERAGE_BOUNDS``).
const COVERAGE_BOUNDS: (f64, f64, f64, f64) = (-130.0, -60.0, 20.0, 55.0);

/// The label marking SPC's significant severe area (``SIGNIFICANT_LABEL``).
const SIGNIFICANT_LABEL: &str = "SIGN";

/// Conditional Intensity Group label prefix (``CIG_LABEL_PREFIX``).
const CIG_LABEL_PREFIX: &str = "CIG";

/// Highest published Conditional Intensity Group level (``CIG_MAX_LEVEL``).
const CIG_MAX_LEVEL: i64 = 3;

/// Ranks the hatched significant-severe area above every probability band
/// (``_SIGNIFICANT_RANK``).
const SIGNIFICANT_RANK: i64 = 10_000;

/// ``len(CATEGORIES)``: the rank base for an unrecognised category.
const KNOWN_CATEGORY_COUNT: i64 = 6;

/// One resolved candidate URL row, mirroring ``OutlookRequest``:
/// ``(url, day, issued epoch or None, label, live)``.
pub(crate) type OutlookRequestRow = (String, i64, Option<i64>, String, bool);

/// One feature's style tuple, mirroring ``_feature_style``'s result:
/// ``(name, rank, stroke, fill, hatch, hatch_level)``.
pub(crate) type FeatureStyleRow = (String, i64, String, String, bool, i64);

// ---------------------------------------------------------------------------
// calendar decomposition
// ---------------------------------------------------------------------------

/// UTC calendar components of one instant, for archive cutoffs, stamps,
/// and URL fields.
struct UtcParts {
    year: i32,
    month: u8,
    day: u8,
    hour: u8,
    minute: u8,
}

/// Decompose one epoch into UTC calendar parts, or ``None`` when the instant
/// lies outside the representable range.
fn utc_parts(epoch: i64) -> Option<UtcParts> {
    let moment = OffsetDateTime::from_unix_timestamp(epoch).ok()?;
    Some(UtcParts {
        year: moment.year(),
        month: u8::from(moment.month()),
        day: moment.day(),
        hour: moment.hour(),
        minute: moment.minute(),
    })
}

/// ``Month`` from its one-based index, as ``Date::from_calendar_date``
/// wants it.
fn month_from_index(month: u8) -> Option<Month> {
    match month {
        1 => Some(Month::January),
        2 => Some(Month::February),
        3 => Some(Month::March),
        4 => Some(Month::April),
        5 => Some(Month::May),
        6 => Some(Month::June),
        7 => Some(Month::July),
        8 => Some(Month::August),
        9 => Some(Month::September),
        10 => Some(Month::October),
        11 => Some(Month::November),
        12 => Some(Month::December),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Python number parsing for payload labels
// ---------------------------------------------------------------------------

/// Python accepts one underscore between two ASCII decimal digits when the
/// ``int`` constructor parses a string; any other underscore invalidates it.
fn python_underscores_valid(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.iter().enumerate().any(|(index, byte)| {
        *byte == b'_'
            && (index == 0
                || index + 1 == bytes.len()
                || !bytes[index - 1].is_ascii_digit()
                || !bytes[index + 1].is_ascii_digit())
    })
}

/// Port of the string form of Python's ``int`` constructor (base 10 only):
/// surrounding whitespace, one optional sign, and one underscore between
/// two digits are accepted.  ``None`` models the ``ValueError`` Python
/// raises; a magnitude beyond ``i64`` also reports ``None``, which every
/// caller treats as "no level" exactly as Python's range checks would.
fn python_int(text: &str) -> Option<i64> {
    let trimmed = text.trim();
    let (negative, magnitude) = match trimmed.as_bytes().first() {
        Some(b'+') => (false, &trimmed[1..]),
        Some(b'-') => (true, &trimmed[1..]),
        Some(_) => (false, trimmed),
        None => return None,
    };
    if magnitude.is_empty() || !python_underscores_valid(magnitude) {
        return None;
    }
    let mut value: i64 = 0;
    for byte in magnitude.bytes() {
        if byte == b'_' {
            continue;
        }
        if !byte.is_ascii_digit() {
            return None;
        }
        value = value
            .checked_mul(10)
            .and_then(|scaled| scaled.checked_add(i64::from(byte - b'0')))?;
    }
    Some(if negative { -value } else { value })
}

/// Port of the ``float(label)`` conversions on payload labels: surrounding
/// whitespace is trimmed (Python accepts it), but underscores are rejected
/// outright -- Python accepts a digit-delimited underscore and SPC never
/// publishes one, so such a label degrades to the fallback rank and name
/// exactly like any other unparseable value.
fn python_float(text: &str) -> Option<f64> {
    let trimmed = text.trim();
    if trimmed.contains('_') {
        return None;
    }
    trimmed.parse::<f64>().ok()
}

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

// ---------------------------------------------------------------------------
// qualifier labels (cig_level, is_qualifier_label)
// ---------------------------------------------------------------------------

/// The pure half of ``cig_level``: the Conditional Intensity Group level
/// ``label`` names, else ``0``.  ``SIGN`` reports ``0`` deliberately: it is
/// a hatch qualifier too, but an ungraded one, and reporting it as level 1
/// would assert an intensity SPC never published for it.
pub(crate) fn cig_level_core(label: Option<&str>) -> i64 {
    let text = label.unwrap_or("").trim().to_uppercase();
    let Some(rest) = text.strip_prefix(CIG_LABEL_PREFIX) else {
        return 0;
    };
    match python_int(rest) {
        Some(level) if (1..=CIG_MAX_LEVEL).contains(&level) => level,
        _ => 0,
    }
}

/// The pure half of ``is_qualifier_label``: whether ``label`` names an area
/// qualifying a band beneath it (``SIGN`` or any Conditional Intensity
/// Group).
pub(crate) fn is_qualifier_label_core(label: Option<&str>) -> bool {
    label.unwrap_or("").trim().to_uppercase() == SIGNIFICANT_LABEL || cig_level_core(label) > 0
}

// ---------------------------------------------------------------------------
// convective day grid (convective_day_start, outlook_day_offset)
// ---------------------------------------------------------------------------

/// The pure half of ``convective_day_start``: the epoch of the 12Z boundary
/// at or before ``epoch_seconds``.  ``div_euclid``/``rem_euclid`` keep the
/// grid exact for negative (pre-1970) timestamps, and the ``i128``
/// arithmetic cannot overflow for any ``i64`` input.
pub(crate) fn convective_day_start_core(epoch_seconds: i64) -> i64 {
    let epoch = i128::from(epoch_seconds);
    let day = epoch.div_euclid(i128::from(DAY_S));
    let seconds_into_day = epoch.rem_euclid(i128::from(DAY_S));
    let start_day = if seconds_into_day >= i128::from(CONVECTIVE_DAY_START_S) {
        day
    } else {
        day - 1
    };
    (start_day * i128::from(DAY_S) + i128::from(CONVECTIVE_DAY_START_S)) as i64
}

/// The pure half of ``outlook_day_offset``: how many whole convective days
/// separate ``valid_epoch`` from ``now_epoch``.  ``0`` is the convective
/// day in progress, ``1`` tomorrow's, and so on; negative values are past
/// days, for which the Day 1 archive is the best available record.
pub(crate) fn outlook_day_offset_core(valid_epoch: i64, now_epoch: i64) -> i64 {
    let delta = i128::from(convective_day_start_core(valid_epoch))
        - i128::from(convective_day_start_core(now_epoch));
    delta.div_euclid(i128::from(DAY_S)) as i64
}

// ---------------------------------------------------------------------------
// product metadata (format_product_days, resolve_product)
// ---------------------------------------------------------------------------

/// The pure half of ``format_product_days``: phrase the outlook days a
/// product publishes, collapsing the single-day products (``"Day 3"``) and
/// spanning the multi-day ones (``"Days 1\u{2013}3"``).
pub(crate) fn format_product_days_core(days: &[i64]) -> String {
    let Some(first) = days.first() else {
        return String::new();
    };
    if days.len() == 1 {
        return format!("Day {first}");
    }
    format!("Days {first}\u{2013}{}", days[days.len() - 1])
}

/// ``resolve_product``: the requested product's key and published days
/// (``OutlookProduct.key``/``.days``), falling back to the categorical
/// product for empty or unknown keys.
fn resolve_product(product: &str) -> (&'static str, &'static [i64]) {
    match product {
        "torn" => ("torn", &[1, 2]),
        "wind" => ("wind", &[1, 2]),
        "hail" => ("hail", &[1, 2]),
        "prob" => ("prob", &[3]),
        _ => ("cat", &[1, 2, 3]),
    }
}

// ---------------------------------------------------------------------------
// coverage gate (covers_location)
// ---------------------------------------------------------------------------

/// The pure half of ``covers_location``: whether an outlook could
/// plausibly apply near ``lat``/``lon``.  Missing or non-finite coordinates
/// answer ``true`` so an unusable location degrades to attempting the fetch
/// rather than silently suppressing the overlay where it would have been
/// wanted.  Longitude wraps to ``[-180, 180)`` the way Python's modulo does.
pub(crate) fn covers_location_core(lat: Option<f64>, lon: Option<f64>) -> bool {
    let (Some(lat), Some(lon)) = (lat, lon) else {
        return true;
    };
    if !lat.is_finite() || !lon.is_finite() {
        return true;
    }
    let (lon0, lon1, lat0, lat1) = COVERAGE_BOUNDS;
    let lon = ((lon + 180.0).rem_euclid(360.0)) - 180.0;
    lon0 <= lon && lon <= lon1 && lat0 <= lat && lat <= lat1
}

// ---------------------------------------------------------------------------
// candidate resolution (candidates_for)
// ---------------------------------------------------------------------------

/// Issuance clock times per outlook day (``_ISSUANCES``).  Order here is
/// irrelevant: candidates are sorted chronologically at resolution time,
/// because Day 1's 01Z update is filed under the following date and is
/// therefore the *latest* issuance for its convective day despite having
/// the smallest clock value.
fn issuance_hours(day: i64) -> &'static [(i64, i64)] {
    match day {
        1 => &[(20, 0), (16, 30), (13, 0), (12, 0), (1, 0)],
        2 => &[(17, 30), (6, 0), (7, 0)],
        _ => &[(19, 30), (7, 30), (8, 30)],
    }
}

/// Day 1's 0100Z update is filed under the following calendar date
/// (``_NEXT_DAY_ISSUANCE_HOURS``).
fn next_day_issuance(day: i64, hour: i64) -> bool {
    day == 1 && hour == 1
}

/// ``_live_url``: the always-current endpoint for one outlook day.
fn live_url(day: i64, product: &str) -> String {
    format!("{BASE}/day{day}otlk_{product}.nolyr.geojson")
}

/// ``_archive_url``: the archived GeoJSON for one issuance, stamped
/// ``%Y``/``%Y%m%d``/``%H%M`` exactly as Python formats it.
fn archive_url(day: i64, issued: &UtcParts, product: &str) -> String {
    let year = issued.year;
    let month = issued.month;
    let stamp_day = issued.day;
    let hour = issued.hour;
    let minute = issued.minute;
    format!(
        "{BASE}/archive/{year:04}/day{day}otlk_\
         {year:04}{month:02}{stamp_day:02}_{hour:02}{minute:02}_{product}.nolyr.geojson"
    )
}

/// The pure half of ``candidates_for``: every candidate outlook URL for
/// ``valid_epoch``, best first, as ``(url, day, issued, label, live)``.
///
/// The ordering encodes the forecast-quality preference twice over: across
/// days the lowest outlook day that has been issued wins, and within a day
/// the newest issuance made *at or before* the valid hour leads, with later
/// issuances still following as a fallback.  Live endpoints precede the
/// archive once the target's convective day has begun (or rolls forward
/// before 12Z).  Issuances after ``now_epoch`` and years before
/// ``ARCHIVE_FIRST_YEAR`` are never requested, and the list is capped at
/// ``MAX_CANDIDATES``.
pub(crate) fn candidates_for_core(
    valid_epoch: i64,
    now_epoch: i64,
    product: &str,
) -> Vec<OutlookRequestRow> {
    let day_start = convective_day_start_core(valid_epoch);
    // Calendar decomposition feeds the archive-year cutoff; an instant
    // outside the representable range has no archive at all.
    let Some(start_parts) = utc_parts(day_start) else {
        return Vec::new();
    };
    if start_parts.year < ARCHIVE_FIRST_YEAR {
        return Vec::new();
    }

    let offset = outlook_day_offset_core(valid_epoch, now_epoch);
    let now_hour = now_epoch.rem_euclid(DAY_S) / 3_600;
    let (spec_key, spec_days) = resolve_product(product);

    let mut requests: Vec<OutlookRequestRow> = Vec::new();
    for day in SUPPORTED_DAYS {
        if !spec_days.contains(&day) {
            continue;
        }
        // Morning outlooks roll forward before 12Z; their payload windows,
        // rather than the clock alone, decide which convective day they
        // cover.
        if offset == day - 1 || (offset == day && now_hour < 12) {
            requests.push((
                live_url(day, spec_key),
                day,
                None,
                format!("Day {day} (current)"),
                true,
            ));
        }
        // A Day *N* outlook for the target convective day is issued N-1
        // days earlier, with Day 1's 0100Z update filed a day later still.
        let base = day_start - (day - 1) * DAY_S;
        let mut issuances: Vec<(i64, UtcParts)> = Vec::new();
        for &(hour, minute) in issuance_hours(day) {
            let mut stamp = base + (hour - 12) * 3_600 + minute * 60;
            if next_day_issuance(day, hour) {
                stamp += DAY_S;
            }
            if stamp > now_epoch {
                continue;
            }
            let Some(parts) = utc_parts(stamp) else {
                continue;
            };
            if parts.year < ARCHIVE_FIRST_YEAR {
                continue;
            }
            issuances.push((stamp, parts));
        }
        issuances.sort_unstable_by(|a, b| b.0.cmp(&a.0));
        // The outlook in force at a given hour is the newest one issued at
        // or before that hour, so try those first, newest first; issuances
        // made after the target still follow as a fallback, since their
        // validity window can extend over it.
        let ordered: Vec<&(i64, UtcParts)> = issuances
            .iter()
            .filter(|(stamp, _)| *stamp <= valid_epoch)
            .chain(issuances.iter().filter(|(stamp, _)| *stamp > valid_epoch))
            .collect();
        for (stamp, parts) in ordered {
            requests.push((
                archive_url(day, parts, spec_key),
                day,
                Some(*stamp),
                format!(
                    "Day {day} \u{00b7} {:02}{:02}Z issuance",
                    parts.hour, parts.minute
                ),
                false,
            ));
        }
    }
    requests.truncate(MAX_CANDIDATES);
    requests
}

// ---------------------------------------------------------------------------
// stamp parsing (_parse_stamp)
// ---------------------------------------------------------------------------

/// The pure half of ``_parse_stamp``: decode an SPC ``YYYYMMDDHHMM`` stamp
/// into epoch seconds.  Exactly twelve ASCII digits are required, the
/// calendar fields are validated by ``time``, and year 0 stays unparseable
/// exactly as Python's ``datetime`` rejects it.
pub(crate) fn parse_stamp_epoch_core(value: &str) -> Option<i64> {
    let bytes = value.as_bytes();
    if bytes.len() != 12 || !bytes.iter().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let field = |start: usize, end: usize| -> i64 {
        let mut number: i64 = 0;
        for &byte in &bytes[start..end] {
            number = number * 10 + i64::from(byte - b'0');
        }
        number
    };
    let year = field(0, 4) as i32;
    let month = field(4, 6) as u8;
    let day = field(6, 8) as u8;
    let hour = field(8, 10) as u8;
    let minute = field(10, 12) as u8;
    // Python's datetime cannot represent year 0 (MINYEAR is 1).
    if year < 1 {
        return None;
    }
    let date = Date::from_calendar_date(year, month_from_index(month)?, day).ok()?;
    let clock = Time::from_hms(hour, minute, 0).ok()?;
    Some(
        PrimitiveDateTime::new(date, clock)
            .assume_utc()
            .unix_timestamp(),
    )
}

// ---------------------------------------------------------------------------
// feature styling (_feature_style, _display_label, _colour)
// ---------------------------------------------------------------------------

/// One categorical risk level's ``(name, rank, stroke, fill)``.
type CategoryEntry = (&'static str, i64, &'static str, &'static str);

/// ``DN`` -> category metadata (``CATEGORIES``); values and colours
/// harvested from the live service.  ``DN 7`` is deliberately unused.
fn category_entry(dn: i64) -> Option<CategoryEntry> {
    match dn {
        2 => Some(("General Thunderstorms", 0, "#55BB55", "#C1E9C1")),
        3 => Some(("Marginal Risk", 1, "#005500", "#66A366")),
        4 => Some(("Slight Risk", 2, "#DDAA00", "#FFE066")),
        5 => Some(("Enhanced Risk", 3, "#FF6600", "#FFA366")),
        6 => Some(("Moderate Risk", 4, "#CC0000", "#E06666")),
        8 => Some(("High Risk", 5, "#CC00CC", "#EE99EE")),
        _ => None,
    }
}

/// One palette entry: ``(stroke, fill)``.
type PaletteEntry = (&'static str, &'static str);

/// Fallback colours for the tornado probability product, keyed by
/// ``LABEL`` (part of ``_PRODUCT_PALETTES``).
const TORN_PALETTE: [(&str, PaletteEntry); 8] = [
    ("0.02", ("#005500", "#66A366")),
    ("0.05", ("#70380f", "#9d4e15")),
    ("0.10", ("#DDAA00", "#FFE066")),
    ("0.15", ("#CC0000", "#E06666")),
    ("0.30", ("#CC00CC", "#EE99EE")),
    ("0.45", ("#a300cc", "#d633ff")),
    ("0.60", ("#a300cc", "#d633ff")),
    (SIGNIFICANT_LABEL, ("#000000", "#888888")),
];

/// Fallback colours for the wind probability product; hail and the Day 3
/// total-severe probability share this scale, so they read the same table.
const WIND_PALETTE: [(&str, PaletteEntry); 8] = [
    ("0.05", ("#70380f", "#9d4e15")),
    ("0.15", ("#DDAA00", "#FFE066")),
    ("0.30", ("#CC0000", "#E06666")),
    ("0.45", ("#CC00CC", "#EE99EE")),
    ("0.60", ("#a300cc", "#d633ff")),
    ("0.75", ("#7d00b3", "#c04dff")),
    ("0.90", ("#5c0080", "#a366ff")),
    (SIGNIFICANT_LABEL, ("#000000", "#888888")),
];

/// ``_PRODUCT_PALETTES.get(product, {}).get(label, ...)``: one label's
/// fallback colours, or ``None`` for a product without a palette.
fn palette_lookup(product: &str, label: &str) -> Option<PaletteEntry> {
    let entries: &[(&str, PaletteEntry)] = match product {
        "torn" => &TORN_PALETTE,
        "wind" | "hail" | "prob" => &WIND_PALETTE,
        _ => return None,
    };
    entries
        .iter()
        .find(|(key, _)| *key == label)
        .map(|(_, value)| *value)
}

/// The significant-severe fallback colours for one product, defaulting
/// where the product publishes no palette.
fn significant_palette(product: &str) -> PaletteEntry {
    palette_lookup(product, SIGNIFICANT_LABEL).unwrap_or(("#000000", "#888888"))
}

/// The categorical half of ``_feature_style``: a known ``DN`` resolves to
/// its ``CATEGORIES`` entry; anything unknown sorts above every known one,
/// so a new severity level is never hidden beneath the areas it supersedes.
fn category_style(dn: Option<i64>, label: &str) -> FeatureStyleRow {
    if let Some((name, rank, stroke, fill)) = dn.and_then(category_entry) {
        return (
            name.to_string(),
            rank,
            stroke.to_string(),
            fill.to_string(),
            false,
            0,
        );
    }
    let name = if label.is_empty() {
        "Unknown".to_string()
    } else {
        label.to_string()
    };
    (
        name,
        KNOWN_CATEGORY_COUNT + dn.unwrap_or(0),
        "#FFFFFF".to_string(),
        "#FFFFFF".to_string(),
        false,
        0,
    )
}

/// The probabilistic half of ``_feature_style``: palette colours by label,
/// the rank from ``int(round(float(label) * 1000))`` (ties to even), and the
/// ``f"{float(label) * 100:g}% probability"`` name.
fn probability_style(product: &str, dn: Option<i64>, label: &str) -> FeatureStyleRow {
    let (stroke, fill) = palette_lookup(product, label).unwrap_or(("#FFFFFF", "#FFFFFF"));
    let value = python_float(label);
    // ``int(round(float(label) * 1000))``: a NaN makes Python's round raise
    // ValueError, which falls back to ``dn or 0``; an infinity raises an
    // OverflowError Python never catches, where the Rust port saturates to
    // the top of the i64 ranks instead of unwinding the whole parse.
    let rank = match value {
        Some(number) => {
            let scaled = number * 1000.0;
            if scaled.is_nan() {
                dn.unwrap_or(0)
            } else {
                scaled.round_ties_even() as i64
            }
        }
        None => dn.unwrap_or(0),
    };
    let name = match value {
        Some(number) => format!("{}% probability", format_g(number * 100.0)),
        None => label.to_string(),
    };
    (name, rank, stroke.to_string(), fill.to_string(), false, 0)
}

/// The pure half of ``_feature_style``: ``(name, rank, stroke, fill,
/// hatch, hatch_level)`` for one feature.
///
/// Ranking derives from ``LABEL`` rather than ``DN`` so it stays correct for
/// both product families: ``SIGN``/CIG qualifiers rank at
/// ``10_000 + level`` and paint hatched, categorical areas order by
/// severity, and probability bands rank at their percentage times ten.
pub(crate) fn feature_style_core(product: &str, dn: Option<i64>, label: &str) -> FeatureStyleRow {
    let level = cig_level_core(Some(label));
    if level != 0 || label.trim().to_uppercase() == SIGNIFICANT_LABEL {
        let (stroke, fill) = significant_palette(product);
        let name = if level != 0 {
            format!("Conditional Intensity Group {level}")
        } else {
            "Significant severe".to_string()
        };
        // Above every probability band so the qualifier paints on top, and
        // ordered among themselves so the higher group wins where their
        // boundaries touch.
        return (
            name,
            SIGNIFICANT_RANK + level,
            stroke.to_string(),
            fill.to_string(),
            true,
            level,
        );
    }
    if product == "cat" {
        return category_style(dn, label);
    }
    probability_style(product, dn, label)
}

/// The pure half of ``_display_label``: the label shown on a legend swatch
/// or badge.  Categorical labels and qualifiers stand unchanged; a bare
/// probability reads as a quantity, so ``"0.05"`` becomes ``"5%"``.
pub(crate) fn display_label_core(product: &str, raw: &str) -> String {
    if product == "cat" || is_qualifier_label_core(Some(raw)) {
        return raw.to_string();
    }
    match python_float(raw) {
        Some(value) => format!("{}%", format_g(value * 100.0)),
        None => raw.to_string(),
    }
}

/// The pure half of ``_colour``: a ``#rrggbb`` colour from the payload, or
/// the fallback.  The original hex case is preserved; the six digits must
/// be ASCII hex (Python's ``int(s, 16)`` would additionally tolerate a
/// sign, which a payload colour never carries).
pub(crate) fn validate_colour_core(value: Option<&str>, fallback: &str) -> String {
    let Some(raw) = value else {
        return fallback.to_string();
    };
    let text = raw.trim();
    let bytes = text.as_bytes();
    if bytes.len() == 7
        && bytes[0] == b'#'
        && bytes[1..].iter().all(|byte| byte.is_ascii_hexdigit())
    {
        text.to_string()
    } else {
        fallback.to_string()
    }
}

// ---------------------------------------------------------------------------
// Python bindings
// ---------------------------------------------------------------------------

/// Return the Conditional Intensity Group level ``label`` names, else ``0``.
///
/// ``CIG1``/``CIG2``/``CIG3`` map to 1-3 after trimming and uppercasing,
/// with Python's ``int`` tolerance for surrounding spaces, a sign, and one
/// underscore between digits.  Every other label -- including ``SIGN``, an
/// ungraded qualifier -- reports ``0``.
#[pyfunction]
fn spc_cig_level(label: Option<&str>) -> i64 {
    cig_level_core(label)
}

/// Report whether ``label`` names an area qualifying a band beneath it.
///
/// ``SIGN`` and any Conditional Intensity Group label (after trimming and
/// uppercasing) are qualifiers; ``None`` and ordinary labels are not.
#[pyfunction]
fn spc_is_qualifier_label(label: Option<&str>) -> bool {
    is_qualifier_label_core(label)
}

/// Return the epoch of the 12Z convective day boundary at or before
/// ``epoch_seconds``.
///
/// Exactly on the boundary belongs to the day it starts, and any earlier
/// hour belongs to the previous day.  ``div_euclid`` arithmetic keeps the
/// grid exact for negative (pre-1970) timestamps.
#[pyfunction]
fn spc_convective_day_start_epoch(epoch_seconds: i64) -> i64 {
    convective_day_start_core(epoch_seconds)
}

/// Return how many whole convective days separate ``valid_epoch`` from
/// ``now_epoch``.
///
/// ``0`` is the convective day in progress, ``1`` tomorrow's, and so on;
/// negative values are past days.  Both instants are first rounded down to
/// their 12Z convective day boundary.
#[pyfunction]
fn spc_outlook_day_offset(valid_epoch: i64, now_epoch: i64) -> i64 {
    outlook_day_offset_core(valid_epoch, now_epoch)
}

/// Phrase the outlook days a product publishes.
///
/// An empty list yields ``""``, a single day ``"Day N"``, and a span
/// ``"Days first\u{2013}last"`` with an en dash, so the Day 3
/// total-severe probability reads ``"Day 3"`` rather than ``"Days 3-3"``.
#[pyfunction]
fn spc_format_product_days(days: Vec<i64>) -> String {
    format_product_days_core(&days)
}

/// Report whether an outlook could plausibly apply near ``lat``/``lon``.
///
/// Missing or non-finite coordinates answer ``True`` so an unusable
/// location degrades to attempting the fetch.  Longitude wraps to
/// ``[-180, 180)`` before the ``COVERAGE_BOUNDS`` test, so a sounding
/// reported as 262.6 degrees east still matches -97.4.
#[pyfunction]
fn spc_covers_location(lat: Option<f64>, lon: Option<f64>) -> bool {
    covers_location_core(lat, lon)
}

/// Return the candidate outlook URLs for ``valid_epoch``, best first.
///
/// ``now_epoch`` is the reference instant and ``product`` one of
/// ``cat``/``torn``/``wind``/``hail``/``prob`` (an empty or unknown key
/// falls back to the categorical product).  Each row is
/// ``(url, day, issued_epoch, label, live)``: live rows carry ``None`` for
/// the issuance and the label ``"Day N (current)"``; archive rows carry the
/// issuance instant and the label ``"Day N \u{00b7} HHMMZ issuance"``.
/// Rows run by ascending outlook day, live endpoint first, then the newest
/// issuance made at or before the valid hour before any later one, and the
/// list is capped at 12 candidates.
#[pyfunction]
fn spc_candidates_for(valid_epoch: i64, now_epoch: i64, product: &str) -> Vec<OutlookRequestRow> {
    candidates_for_core(valid_epoch, now_epoch, product)
}

/// Decode an SPC ``YYYYMMDDHHMM`` stamp into epoch seconds, or ``None``.
///
/// Exactly twelve ASCII digits are required and the calendar fields are
/// validated (leap days included); year 0 stays unparseable exactly as
/// Python's ``datetime`` rejects it.
#[pyfunction]
fn spc_parse_stamp_epoch(value: &str) -> Option<i64> {
    parse_stamp_epoch_core(value)
}

/// Return ``(name, rank, stroke, fill, hatch, hatch_level)`` for one
/// outlook feature.
///
/// ``SIGN`` and the Conditional Intensity Groups rank at ``10_000 +
/// level`` and hatch over the band they qualify; the categorical product
/// orders by severity, keeping unknown ``DN`` values above every known
/// one; the probabilistic products order by the probability itself
/// (``int(round(label * 1000))``, ties to even) and name their areas
/// ``"{label * 100:g}% probability"``.
#[pyfunction]
fn spc_feature_style(product: &str, dn: Option<i64>, label: &str) -> FeatureStyleRow {
    feature_style_core(product, dn, label)
}

/// Return the label shown on a legend swatch or badge.
///
/// Categorical labels and qualifiers stand unchanged; a bare probability
/// reads as a quantity, so ``"0.05"`` becomes ``"5%"``.
#[pyfunction]
fn spc_display_label(product: &str, raw: &str) -> String {
    display_label_core(product, raw)
}

/// Return a ``#rrggbb`` colour from the payload, or the fallback.
///
/// The value is trimmed and must be a ``#`` plus six ASCII hex digits,
/// with the original hex case preserved; anything else -- including
/// ``None`` -- reports ``fallback``.
#[pyfunction]
fn spc_validate_colour(value: Option<&str>, fallback: &str) -> String {
    validate_colour_core(value, fallback)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(spc_cig_level, module)?)?;
    module.add_function(wrap_pyfunction!(spc_is_qualifier_label, module)?)?;
    module.add_function(wrap_pyfunction!(spc_convective_day_start_epoch, module)?)?;
    module.add_function(wrap_pyfunction!(spc_outlook_day_offset, module)?)?;
    module.add_function(wrap_pyfunction!(spc_format_product_days, module)?)?;
    module.add_function(wrap_pyfunction!(spc_covers_location, module)?)?;
    module.add_function(wrap_pyfunction!(spc_candidates_for, module)?)?;
    module.add_function(wrap_pyfunction!(spc_parse_stamp_epoch, module)?)?;
    module.add_function(wrap_pyfunction!(spc_feature_style, module)?)?;
    module.add_function(wrap_pyfunction!(spc_display_label, module)?)?;
    module.add_function(wrap_pyfunction!(spc_validate_colour, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Epoch seconds for one UTC wall-clock moment.
    fn utc(year: i32, month: u8, day: u8, hour: u8, minute: u8) -> i64 {
        let date = Date::from_calendar_date(year, month_from_index(month).unwrap(), day).unwrap();
        let clock = Time::from_hms(hour, minute, 0).unwrap();
        PrimitiveDateTime::new(date, clock)
            .assume_utc()
            .unix_timestamp()
    }

    /// The candidate rows narrowed to one outlook day.
    fn day_rows(got: &[OutlookRequestRow], day: i64) -> Vec<&OutlookRequestRow> {
        got.iter().filter(|row| row.1 == day).collect()
    }

    #[test]
    fn cig_levels_parse_the_prefix_after_normalisation() {
        assert_eq!(cig_level_core(Some("CIG1")), 1);
        assert_eq!(cig_level_core(Some("cig2")), 2);
        assert_eq!(cig_level_core(Some("  cig3  ")), 3);
        // Python's int() tolerates an inner space and a sign.
        assert_eq!(cig_level_core(Some("CIG 1")), 1);
        assert_eq!(cig_level_core(Some("CIG+2")), 2);
        assert_eq!(cig_level_core(Some("CIG0")), 0);
        assert_eq!(cig_level_core(Some("CIG4")), 0);
        assert_eq!(cig_level_core(Some("CIGN")), 0);
        assert_eq!(cig_level_core(Some("CIG")), 0);
        assert_eq!(cig_level_core(Some("CIG-1")), 0);
        // 1_0 parses as 10, which exceeds the highest published level.
        assert_eq!(cig_level_core(Some("CIG1_0")), 0);
        assert_eq!(cig_level_core(Some("CIG_1")), 0);
        assert_eq!(
            cig_level_core(Some("SIGN")),
            0,
            "SIGN is an ungraded qualifier"
        );
        assert_eq!(cig_level_core(Some("sign")), 0);
        assert_eq!(
            cig_level_core(Some("SIG1")),
            0,
            "only the CIG prefix counts"
        );
        assert_eq!(cig_level_core(Some("")), 0);
        assert_eq!(cig_level_core(None), 0);
    }

    #[test]
    fn qualifier_labels_cover_sign_and_the_cig_groups() {
        assert!(is_qualifier_label_core(Some("SIGN")));
        assert!(is_qualifier_label_core(Some(" sign ")));
        assert!(is_qualifier_label_core(Some("CIG1")));
        assert!(is_qualifier_label_core(Some("cig3")));
        assert!(!is_qualifier_label_core(Some("SLGT")));
        assert!(!is_qualifier_label_core(Some("0.15")));
        assert!(!is_qualifier_label_core(Some("")));
        assert!(!is_qualifier_label_core(None));
    }

    #[test]
    fn convective_day_starts_round_down_to_the_previous_12z() {
        // Inside the day that began at 12Z.
        assert_eq!(
            convective_day_start_core(utc(2025, 5, 14, 18, 0)),
            utc(2025, 5, 14, 12, 0)
        );
        // Exactly on the boundary belongs to the day it starts.
        assert_eq!(
            convective_day_start_core(utc(2025, 5, 14, 12, 0)),
            utc(2025, 5, 14, 12, 0)
        );
        // One minute before belongs to the previous day.
        assert_eq!(
            convective_day_start_core(utc(2025, 5, 14, 11, 59)),
            utc(2025, 5, 13, 12, 0)
        );
        // An overnight 06Z sounding belongs to the previous calendar day.
        assert_eq!(
            convective_day_start_core(utc(2025, 5, 15, 6, 0)),
            utc(2025, 5, 14, 12, 0)
        );
        assert_eq!(
            convective_day_start_core(utc(2025, 5, 14, 0, 0)),
            utc(2025, 5, 13, 12, 0)
        );
    }

    #[test]
    fn convective_day_starts_handle_pre_1970_epochs() {
        assert_eq!(convective_day_start_core(-1), -43_200); // 1969-12-31 23:59:59Z
        assert_eq!(convective_day_start_core(0), -43_200); // 1970-01-01 00:00Z
        assert_eq!(convective_day_start_core(43_199), -43_200); // 1969-12-31 11:59:59Z
        assert_eq!(convective_day_start_core(43_200), 43_200); // 1970-01-01 12:00Z
        assert_eq!(convective_day_start_core(86_399), 43_200);
        assert_eq!(convective_day_start_core(86_400), 43_200); // Jan 2 00Z rounds back
        assert_eq!(convective_day_start_core(-43_201), -129_600); // 1969-12-30 12:00Z
    }

    #[test]
    fn outlook_day_offsets_count_whole_convective_days() {
        let now = utc(2025, 5, 14, 15, 0);
        for (hours, expected) in [(0, 0), (12, 0), (24, 1), (48, 2), (-24, -1), (72, 3)] {
            assert_eq!(
                outlook_day_offset_core(now + hours * 3_600, now),
                expected,
                "offset for +{hours}h"
            );
        }
    }

    #[test]
    fn product_day_phrases_collapse_single_days() {
        assert_eq!(format_product_days_core(&[]), "");
        assert_eq!(format_product_days_core(&[1]), "Day 1");
        assert_eq!(format_product_days_core(&[3]), "Day 3");
        assert_eq!(format_product_days_core(&[1, 2, 3]), "Days 1\u{2013}3");
        assert_eq!(format_product_days_core(&[1, 2]), "Days 1\u{2013}2");
    }

    #[test]
    fn coverage_bounds_gate_the_fetch() {
        for (lat, lon, covered) in [
            (35.2, -97.4, true),   // Norman, OK
            (25.8, -80.2, true),   // Miami
            (47.6, -122.3, true),  // Seattle
            (13.5, 144.8, false),  // Guam
            (52.0, 13.4, false),   // Berlin
            (64.8, -147.7, false), // Fairbanks
            (21.3, -157.9, false), // Honolulu
        ] {
            assert_eq!(covers_location_core(Some(lat), Some(lon)), covered);
        }
        // 262.6 degrees east is the same meridian as -97.4.
        assert!(covers_location_core(Some(35.2), Some(262.6)));
    }

    #[test]
    fn unusable_coordinates_still_attempt_the_fetch() {
        assert!(covers_location_core(None, None));
        assert!(covers_location_core(Some(f64::NAN), Some(0.0)));
        assert!(covers_location_core(Some(0.0), Some(f64::INFINITY)));
    }

    #[test]
    fn pre_archive_dates_short_circuit_to_no_candidates() {
        // ERA5 reaches back decades; probing 1998 would be nine dead
        // requests.
        let now = utc(2025, 5, 14, 15, 0);
        assert!(candidates_for_core(utc(1998, 5, 20, 18, 0), now, "cat").is_empty());
    }

    #[test]
    fn beyond_day_three_has_no_candidates() {
        let now = utc(2025, 5, 14, 15, 0);
        assert!(candidates_for_core(now + 6 * DAY_S, now, "cat").is_empty());
    }

    #[test]
    fn the_current_day_leads_with_its_live_endpoint() {
        let now = utc(2025, 5, 14, 15, 0);
        let got = candidates_for_core(utc(2025, 5, 14, 18, 0), now, "cat");
        assert!(got[0].4, "the live endpoint leads for the current day");
        assert_eq!(got[0].1, 1);
        assert_eq!(got[0].2, None);
        assert_eq!(got[0].3, "Day 1 (current)");
        assert!(got[0].0.ends_with("day1otlk_cat.nolyr.geojson"));
        let days: Vec<i64> = got.iter().map(|row| row.1).collect();
        assert!(
            days.windows(2).all(|pair| pair[0] <= pair[1]),
            "day 1 is exhausted before day 2"
        );
    }

    #[test]
    fn unissued_products_are_not_requested() {
        // Tomorrow's Day 1 does not exist yet, so Day 2 must lead.
        let now = utc(2025, 5, 14, 15, 0);
        let got = candidates_for_core(utc(2025, 5, 15, 18, 0), now, "cat");
        assert!(!got.is_empty());
        assert!(got.iter().all(|row| row.1 != 1));
        assert_eq!(got[0].1, 2);
        assert!(got[0].4);
        assert!(got
            .iter()
            .all(|row| row.2.is_none_or(|issued| issued <= now)));
    }

    #[test]
    fn the_outlook_in_force_at_the_target_hour_is_tried_first() {
        let now = utc(2025, 5, 14, 15, 0);
        let valid = utc(2023, 3, 31, 18, 0);
        let got = candidates_for_core(valid, now, "cat");
        // Day 1: the newest issuance at or before 18Z first, then the
        // earlier ones, then the issuances made after the target.
        let day1: Vec<Option<i64>> = day_rows(&got, 1).iter().map(|row| row.2).collect();
        assert_eq!(
            day1,
            vec![
                Some(utc(2023, 3, 31, 16, 30)),
                Some(utc(2023, 3, 31, 13, 0)),
                Some(utc(2023, 3, 31, 12, 0)),
                Some(utc(2023, 4, 1, 1, 0)),
                Some(utc(2023, 3, 31, 20, 0)),
            ]
        );
        assert_eq!(
            got[0].0,
            "https://www.spc.noaa.gov/products/outlook/archive/2023/\
             day1otlk_20230331_1630_cat.nolyr.geojson"
        );
        assert_eq!(got[0].3, "Day 1 \u{00b7} 1630Z issuance");
        assert!(!got[0].4);
        assert!(got.len() <= MAX_CANDIDATES);
    }

    #[test]
    fn the_0100z_update_wins_for_an_overnight_target() {
        // The 01Z update is the freshest product for a 06Z sounding and is
        // filed under the next calendar date, so it must be tried first.
        let now = utc(2025, 5, 14, 15, 0);
        let got = candidates_for_core(utc(2023, 4, 1, 6, 0), now, "cat");
        let day1 = day_rows(&got, 1);
        assert_eq!(day1[0].2, Some(utc(2023, 4, 1, 1, 0)));
        assert!(day1[0].0.contains("day1otlk_20230401_0100_cat"));
        let midnight: Vec<&str> = day1
            .iter()
            .filter(|row| row.2 == Some(utc(2023, 4, 1, 1, 0)))
            .map(|row| row.0.as_str())
            .collect();
        assert_eq!(midnight.len(), 1, "the 0100Z issuance appears exactly once");
    }

    #[test]
    fn morning_rollover_before_noon_lifts_the_live_endpoint() {
        // 09Z is still the previous convective day, but SPC has already
        // published the new day's outlooks, so the live endpoint leads and
        // the overnight 0100Z update is the first archive candidate.
        let now = utc(2026, 10, 28, 9, 0);
        let got = candidates_for_core(now, now, "cat");
        assert!(got[0].4);
        assert!(got[0].0.ends_with("day1otlk_cat.nolyr.geojson"));
        assert!(!got[1].4);
        assert_eq!(got[1].2, Some(utc(2026, 10, 28, 1, 0)));
        assert!(got[1].0.contains("day1otlk_20261028_0100_cat"));
    }

    #[test]
    fn the_best_outlook_day_improves_as_time_passes() {
        let target = utc(2026, 5, 20, 21, 0);
        let mut first_days: Vec<Option<i64>> = Vec::new();
        for now in [
            utc(2026, 5, 18, 15, 0),
            utc(2026, 5, 19, 9, 0),
            utc(2026, 5, 20, 13, 0),
        ] {
            let got = candidates_for_core(target, now, "cat");
            first_days.push(got.first().map(|row| row.1));
        }
        assert_eq!(first_days, vec![Some(3), Some(2), Some(1)]);
    }

    #[test]
    fn a_hazard_absent_on_day_three_becomes_available_on_day_two() {
        let target = utc(2026, 5, 20, 21, 0);
        let early = utc(2026, 5, 18, 15, 0);
        let later = utc(2026, 5, 19, 9, 0);
        assert!(candidates_for_core(target, early, "torn").is_empty());
        assert!(!candidates_for_core(target, later, "torn").is_empty());
    }

    #[test]
    fn hazard_products_never_reach_day_three() {
        let now = utc(2025, 5, 14, 15, 0);
        let day3 = now + 2 * DAY_S + 3 * 3_600; // 2025-05-16 18Z
        let cat = candidates_for_core(day3, now, "cat");
        assert!(cat.iter().any(|row| row.1 == 3));
        for key in ["torn", "wind", "hail"] {
            let got = candidates_for_core(day3, now, key);
            assert!(
                got.iter().all(|row| row.1 != 3),
                "{key} must not reach Day 3"
            );
        }
        // The combined total-severe probability is the only Day 3 product.
        assert!(candidates_for_core(day3, now, "prob")
            .iter()
            .any(|row| row.1 == 3));
    }

    #[test]
    fn every_product_key_reaches_its_own_url() {
        let now = utc(2025, 5, 14, 15, 0);
        let valid = utc(2023, 3, 31, 18, 0);
        for key in ["cat", "torn", "wind", "hail", "prob"] {
            let got = candidates_for_core(valid, now, key);
            assert!(!got.is_empty(), "no candidates for {key}");
            assert!(got
                .iter()
                .all(|row| row.0.contains(&format!("_{key}.nolyr.geojson"))));
        }
        // Empty and unknown products fall back to the categorical one.
        for key in ["", "nope"] {
            let got = candidates_for_core(valid, now, key);
            assert!(got.iter().all(|row| row.0.contains("_cat.nolyr.geojson")));
        }
    }

    #[test]
    fn the_issuance_in_force_is_chosen_for_each_forecast_hour() {
        let run = utc(2026, 4, 15, 0, 0);
        let late = utc(2026, 8, 29, 12, 0);
        for (fxx, (year, month, day, hour, minute)) in [
            (12, (2026, 4, 15, 12, 0)),
            (14, (2026, 4, 15, 13, 0)),
            (17, (2026, 4, 15, 16, 30)),
            (18, (2026, 4, 15, 16, 30)),
            (21, (2026, 4, 15, 20, 0)),
            (24, (2026, 4, 15, 20, 0)),
            (26, (2026, 4, 16, 1, 0)),
            (35, (2026, 4, 16, 1, 0)),
            (36, (2026, 4, 16, 12, 0)),
        ] {
            let got = candidates_for_core(run + fxx * 3_600, late, "cat");
            assert_eq!(
                got.first().and_then(|row| row.2),
                Some(utc(year, month, day, hour, minute)),
                "F{fxx:03} must resolve to the issuance in force"
            );
        }
        // F036 lands exactly on the 12Z boundary and starts the next
        // convective day's archive.
        let got = candidates_for_core(run + 36 * 3_600, late, "cat");
        assert!(got[0].0.contains("day1otlk_20260416_1200"));
    }

    #[test]
    fn stamps_decode_exactly_twelve_ascii_digits() {
        assert_eq!(
            parse_stamp_epoch_core("202503311630"),
            Some(utc(2025, 3, 31, 16, 30))
        );
        assert_eq!(
            parse_stamp_epoch_core("202402291200"),
            Some(utc(2024, 2, 29, 12, 0))
        );
        assert_eq!(
            parse_stamp_epoch_core("000103311630"),
            Some(utc(1, 3, 31, 16, 30))
        );
        for bad in [
            "nope",
            "",
            "20250331",
            "2025033116300",
            "202513011200",
            "202502291200",                             // 2025 is not a leap year
            "202504311200",                             // April has thirty days
            "202503312400",                             // hour 24 rolls over in Python too
            "202503311660",                             // minute 60 likewise
            "000001010000",                             // Python's datetime cannot represent year 0
            "\u{0662}\u{0660}\u{0662}\u{0665}03311630", // non-ASCII digits
        ] {
            assert_eq!(
                parse_stamp_epoch_core(bad),
                None,
                "{bad:?} must stay unparseable"
            );
        }
    }

    #[test]
    fn feature_styles_order_categories_by_severity() {
        let (name, rank, stroke, fill, hatch, hatch_level) =
            feature_style_core("cat", Some(3), "MRGL");
        assert_eq!(name, "Marginal Risk");
        assert_eq!(rank, 1);
        assert_eq!(stroke, "#005500");
        assert_eq!(fill, "#66A366");
        assert!(!hatch);
        assert_eq!(hatch_level, 0);

        let high = feature_style_core("cat", Some(8), "HIGH");
        assert_eq!(high.0, "High Risk");
        assert_eq!(high.1, 5);
        assert_eq!(high.2, "#CC00CC");
        assert_eq!(high.3, "#EE99EE");
        assert!(!high.4);
    }

    #[test]
    fn unrecognised_categories_sort_above_known_ones() {
        let unknown = feature_style_core("cat", Some(99), "NEW");
        assert_eq!(unknown.0, "NEW");
        assert_eq!(unknown.1, KNOWN_CATEGORY_COUNT + 99);
        assert_eq!(unknown.2, "#FFFFFF");
        assert_eq!(unknown.3, "#FFFFFF");

        let unnamed = feature_style_core("cat", None, "");
        assert_eq!(unnamed.0, "Unknown");
        assert_eq!(unnamed.1, KNOWN_CATEGORY_COUNT);

        // DN 7 is unused by SPC, but an unrecognised value must be kept.
        let seven = feature_style_core("cat", Some(7), "NEW");
        assert_eq!(seven.1, KNOWN_CATEGORY_COUNT + 7);
        assert_eq!(seven.0, "NEW");

        let zero = feature_style_core("cat", Some(0), "");
        assert_eq!(zero.0, "Unknown");
        assert_eq!(zero.1, KNOWN_CATEGORY_COUNT);
    }

    #[test]
    fn qualifiers_rank_above_every_band_and_hatch() {
        let sign = feature_style_core("torn", Some(10), "SIGN");
        assert_eq!(sign.0, "Significant severe");
        assert_eq!(sign.1, SIGNIFICANT_RANK);
        assert_eq!(sign.2, "#000000");
        assert_eq!(sign.3, "#888888");
        assert!(sign.4);
        assert_eq!(sign.5, 0);

        let cig = feature_style_core("wind", Some(2), "CIG2");
        assert_eq!(cig.0, "Conditional Intensity Group 2");
        assert_eq!(cig.1, SIGNIFICANT_RANK + 2);
        assert_eq!(cig.2, "#000000");
        assert_eq!(cig.3, "#888888");
        assert!(cig.4);
        assert_eq!(cig.5, 2);

        // A product without a palette still hatches the qualifier.
        let cat_sign = feature_style_core("cat", Some(2), "sign");
        assert_eq!(cat_sign.0, "Significant severe");
        assert_eq!(cat_sign.2, "#000000");
        assert_eq!(cat_sign.3, "#888888");
        assert!(cat_sign.4);
    }

    #[test]
    fn probability_bands_rank_by_their_percentage() {
        let band = feature_style_core("torn", Some(10), "0.10");
        assert_eq!(band.0, "10% probability");
        assert_eq!(band.1, 100);
        assert_eq!(band.2, "#DDAA00");
        assert_eq!(band.3, "#FFE066");
        assert!(!band.4);

        let wind = feature_style_core("wind", Some(5), "0.05");
        assert_eq!(wind.0, "5% probability");
        assert_eq!(wind.1, 50);
        assert_eq!(wind.2, "#70380f");
        assert_eq!(wind.3, "#9d4e15");

        // 0.15 is amber for wind but red for tornado: DN cannot drive colour.
        let wind_15 = feature_style_core("wind", Some(15), "0.15");
        let torn_15 = feature_style_core("torn", Some(15), "0.15");
        assert_eq!(wind_15.2, "#DDAA00");
        assert_eq!(torn_15.2, "#CC0000");
        assert_ne!(wind_15.2, torn_15.2);

        // Hail and prob share the wind scale, including the newest bands.
        let gust75 = feature_style_core("prob", Some(75), "0.75");
        assert_eq!(gust75.0, "75% probability");
        assert_eq!(gust75.1, 750);
        assert_eq!(gust75.2, "#7d00b3");
        assert_eq!(gust75.3, "#c04dff");
        let gust90 = feature_style_core("hail", Some(90), "0.90");
        assert_eq!(gust90.0, "90% probability");
        assert_eq!(gust90.1, 900);
        assert_eq!(gust90.2, "#5c0080");
        assert_eq!(gust90.3, "#a366ff");

        // 0.0625 * 1000 is the exact 62.5 tie, which rounds to even (62).
        let tie = feature_style_core("torn", None, "0.0625");
        assert_eq!(tie.1, 62);
        assert_eq!(tie.0, "6.25% probability");

        // Unknown labels fall back to dn or 0 and keep their text.
        let junk = feature_style_core("torn", Some(7), "SIGNX");
        assert_eq!(junk.0, "SIGNX");
        assert_eq!(junk.1, 7);
        assert_eq!(junk.2, "#FFFFFF");
        assert_eq!(junk.3, "#FFFFFF");
        let junk_no_dn = feature_style_core("nope", None, "junk");
        assert_eq!(junk_no_dn.0, "junk");
        assert_eq!(junk_no_dn.1, 0);
        assert_eq!(junk_no_dn.2, "#FFFFFF");

        // A "nan" label makes Python's round raise ValueError, which
        // falls back to dn; the name still renders the quantity.
        let nan_rank = feature_style_core("torn", Some(9), "nan");
        assert_eq!(nan_rank.1, 9);
    }

    #[test]
    fn display_labels_read_probabilities_as_quantities() {
        assert_eq!(display_label_core("cat", "MRGL"), "MRGL");
        assert_eq!(display_label_core("torn", "0.10"), "10%");
        assert_eq!(display_label_core("torn", "0.02"), "2%");
        assert_eq!(display_label_core("wind", "0.05"), "5%");
        assert_eq!(display_label_core("prob", "0.45"), "45%");
        assert_eq!(display_label_core("hail", "0.60"), "60%");
        assert_eq!(display_label_core("torn", "SIGN"), "SIGN");
        // A qualifier check is case-insensitive, but the raw label stands.
        assert_eq!(display_label_core("torn", "cig3"), "cig3");
        assert_eq!(display_label_core("hail", "junk"), "junk");
    }

    #[test]
    fn colours_require_seven_trimmed_hex_characters() {
        assert_eq!(validate_colour_core(Some("#005500"), "#FFFFFF"), "#005500");
        assert_eq!(
            validate_colour_core(Some(" #A366FF "), "#FFFFFF"),
            "#A366FF",
            "the original hex case is preserved"
        );
        assert_eq!(validate_colour_core(Some("#00550"), "#FFFFFF"), "#FFFFFF");
        assert_eq!(validate_colour_core(Some("#0055000"), "#FFFFFF"), "#FFFFFF");
        assert_eq!(validate_colour_core(Some("#00gg00"), "#FFFFFF"), "#FFFFFF");
        assert_eq!(validate_colour_core(Some("005500"), "#FFFFFF"), "#FFFFFF");
        assert_eq!(
            validate_colour_core(Some("not-a-colour"), "#FFFFFF"),
            "#FFFFFF"
        );
        assert_eq!(validate_colour_core(Some(""), "#FFFFFF"), "#FFFFFF");
        assert_eq!(validate_colour_core(None, "#FFFFFF"), "#FFFFFF");
        assert_eq!(validate_colour_core(None, "#66A366"), "#66A366");
    }

    #[test]
    fn format_g_matches_the_probability_labels() {
        assert_eq!(format_g(2.0), "2");
        assert_eq!(format_g(5.0), "5");
        assert_eq!(format_g(10.0), "10");
        assert_eq!(format_g(15.0), "15");
        assert_eq!(format_g(45.0), "45");
        assert_eq!(format_g(60.0), "60");
        assert_eq!(format_g(75.0), "75");
        assert_eq!(format_g(90.0), "90");
        assert_eq!(format_g(6.25), "6.25");
        assert_eq!(format_g(0.0), "0");
    }
}

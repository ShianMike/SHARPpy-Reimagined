//! Native ports of the Open-Meteo access and provider-normalization contracts.
//!
//! These functions mirror the credential-contained surface of
//! ``sharpmod.providers.openmeteo_access`` and the pure helpers of
//! ``sharpmod.providers.openmeteo``: URL redaction and validation against
//! CPython's ``urllib.parse.urlsplit``, access-mode resolution, the one-way
//! usage-accounting scopes, and the cycle/time/level selection math the fetch
//! layer drives. Every exception message, tie-break, and NaN behavior matches
//! the Python source, because the credential containment here is a safety
//! boundary rather than a convenience.

use std::cmp::Ordering;

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use ring::digest::{digest, SHA256};

const REDACTED: &str = "<redacted>";
const API_KEY_ENV: &str = "SHARPMOD_OPENMETEO_API_KEY";
const MODE_ENV: &str = "SHARPMOD_OPENMETEO_MODE";
const SELF_HOSTED_URL_ENV: &str = "SHARPMOD_OPENMETEO_URL";

const MODE_FREE: &str = "free-direct";
const MODE_CUSTOMER: &str = "customer-direct";
const MODE_SELF_HOSTED: &str = "self-hosted";
const MODES: [&str; 3] = [MODE_FREE, MODE_CUSTOMER, MODE_SELF_HOSTED];

const FREE_HOST: &str = "single-runs-api.open-meteo.com";
const FREE_BASE_URL: &str = "https://single-runs-api.open-meteo.com";
const CUSTOMER_BASE_URL: &str = "https://customer-single-runs-api.open-meteo.com";

const PRESSURE_FAMILIES: [&str; 5] = [
    "temperature",
    "dew_point",
    "wind_speed",
    "wind_direction",
    "geopotential_height",
];
const SURFACE_VARIABLES: [&str; 5] = [
    "surface_pressure",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
];

/// ``str.isspace`` / ``str.strip`` membership: Unicode White_Space plus the
/// C0 file/group/record/unit separators ``0x1c`` through ``0x1f``.
fn python_is_space(value: char) -> bool {
    value.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&value)
}

/// ``str.strip()`` on both ends, per CPython's per-character space table.
fn python_trim(text: &str) -> &str {
    text.trim_matches(python_is_space)
}

/// ``str.strip()`` and a lower-cased copy, in that order, as the catalog
/// helpers normalize their identifiers.
fn normalised_identifier(text: &str) -> String {
    python_trim(text).to_lowercase()
}

/// ``repr`` of a Python ``str`` for the message fragments the Python source
/// formats with ``%r``. The quote-selection rule matches CPython: single
/// quotes unless the text holds one and no double quote.
fn python_str_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' | '"' | '\'' => {
                if character == quote || character == '\\' {
                    out.push('\\');
                }
                out.push(character);
            }
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ => out.push(character),
        }
    }
    out.push(quote);
    out
}

// ---------------------------------------------------------------------------
// Credential redaction
// ---------------------------------------------------------------------------

/// Case-insensitive single-character match for one ``apikey=`` letter, using
/// the full case-folding equivalences CPython's ``re`` applies under
/// ``IGNORECASE``: dotless/dotted capital I fold to ``i`` and the Kelvin sign
/// folds to ``k``. No other codepoints fold onto the ASCII letters of
/// ``apikey=``.
fn apikey_marker_char_matches(actual: char, expected: char) -> bool {
    if actual == expected {
        return true;
    }
    match expected {
        'a' => actual == 'A',
        'p' => actual == 'P',
        'i' => actual == 'I' || actual == '\u{130}' || actual == '\u{131}',
        'k' => actual == 'K' || actual == '\u{212a}',
        'e' => actual == 'E',
        'y' => actual == 'Y',
        _ => false,
    }
}

/// Value character of ``[^&\s"']+``: anything except the query separator, the
/// two quote characters, and a Python whitespace character.
fn is_apikey_value_char(value: char) -> bool {
    value != '&' && value != '"' && value != '\'' && !python_is_space(value)
}

/// Port of ``openmeteo_access.redact``: every ``apikey=`` query value becomes
/// ``<redacted>`` while the matched ``apikey=`` spelling is preserved.
pub(crate) fn redact_core(text: &str) -> String {
    const MARKER: [char; 7] = ['a', 'p', 'i', 'k', 'e', 'y', '='];
    let characters: Vec<char> = text.chars().collect();
    let mut output = String::with_capacity(text.len() + REDACTED.len());
    let mut index = 0;
    while index < characters.len() {
        let mut matched = true;
        for (offset, expected) in MARKER.iter().enumerate() {
            match characters.get(index + offset) {
                Some(actual) if apikey_marker_char_matches(*actual, *expected) => {}
                _ => {
                    matched = false;
                    break;
                }
            }
        }
        if matched {
            let mut end = index + MARKER.len();
            while end < characters.len() && is_apikey_value_char(characters[end]) {
                end += 1;
            }
            if end > index + MARKER.len() {
                output.extend(characters[index..index + MARKER.len()].iter().copied());
                output.push_str(REDACTED);
                index = end;
                continue;
            }
        }
        output.push(characters[index]);
        index += 1;
    }
    output
}

// ---------------------------------------------------------------------------
// URL parsing, matching urllib.parse.urlsplit (3.11.4+ behaviour)
// ---------------------------------------------------------------------------

/// Scheme and netloc of ``url`` exactly as ``urlsplit`` extracts them: leading
/// WHATWG C0-control-or-space characters are dropped (trailing ones are kept
/// and tab/newline/carriage-return are removed anywhere, per the CPython
/// security fixes), the scheme must start with an ASCII letter and consist only
/// of scheme characters before the first colon, and the netloc ends at the
/// first ``/``, ``?`` or ``#`` after ``//``.
fn url_split_parts(url: &str) -> (String, String) {
    let stripped = url.trim_start_matches(|value: char| value <= '\u{20}');
    let cleaned: String = stripped
        .chars()
        .filter(|value| !matches!(*value, '\t' | '\n' | '\r'))
        .collect();
    let mut scheme = String::new();
    let mut rest = cleaned.as_str();
    if let Some(colon) = cleaned.find(':') {
        let head = &cleaned[..colon];
        if colon > 0
            && head
                .chars()
                .next()
                .is_some_and(|first| first.is_ascii_alphabetic())
            && head
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'-' | b'.'))
        {
            scheme = head.to_lowercase();
            rest = &cleaned[colon + 1..];
        }
    }
    let netloc = match rest.strip_prefix("//") {
        Some(after) => {
            let end = after.find(['/', '?', '#']).unwrap_or(after.len());
            &after[..end]
        }
        None => "",
    };
    (scheme, netloc.to_string())
}

/// Hostname of a netloc exactly as ``urlsplit(...).hostname`` computes it:
/// everything after the last ``@``; with a bracket, everything inside the
/// brackets; otherwise, everything before the first ``:``. Empty means ``None``
/// in Python. The address part is lower-cased while a ``%zone`` suffix for
/// scoped IPv6 addresses is kept verbatim.
fn netloc_hostname(netloc: &str) -> Option<String> {
    if netloc.is_empty() {
        return None;
    }
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    let rawhostname = match hostinfo.split_once('[') {
        Some((_, bracketed)) => match bracketed.split_once(']') {
            Some((host, _)) => host,
            None => bracketed,
        },
        None => match hostinfo.split_once(':') {
            Some((host, _)) => host,
            None => hostinfo,
        },
    };
    if rawhostname.is_empty() {
        return None;
    }
    match rawhostname.split_once('%') {
        Some((address, zone)) => Some(format!("{}%{zone}", address.to_lowercase())),
        None => Some(rawhostname.to_lowercase()),
    }
}

/// Accept/reject port of ``ipaddress.IPv4Address`` text parsing: exactly four
/// strictly decimal octets, each at most three digits, no leading zeros
/// except a bare ``0``, and no value above 255.
fn parse_ipv4_text(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    let mut count = 0;
    for octet in text.split('.') {
        let bytes = octet.as_bytes();
        if bytes.is_empty()
            || bytes.len() > 3
            || !bytes.iter().all(|byte| byte.is_ascii_digit())
            || (bytes[0] == b'0' && bytes.len() > 1)
        {
            return false;
        }
        if !octet.parse::<u16>().is_ok_and(|value| value <= 255) {
            return false;
        }
        count += 1;
    }
    count == 4
}

fn parse_hextet(part: &str) -> bool {
    let bytes = part.as_bytes();
    !bytes.is_empty() && bytes.len() <= 4 && bytes.iter().all(|byte| byte.is_ascii_hexdigit())
}

/// Accept/reject port of ``ipaddress.IPv6Address`` text parsing, including
/// RFC 4007 zone ids. Mirrors the CPython algorithm step for step: the scope
/// split happens first, the address (without zone) is limited to 45
/// characters, an IPv4-style final group expands to two hextets, there is at
/// most one ``::``, and endpoint colons are only legal as part of it.
fn parse_ipv6_text(text: &str) -> bool {
    let address = match text.split_once('%') {
        Some((address, zone)) => {
            if zone.is_empty() || zone.contains('%') {
                return false;
            }
            address
        }
        None => text,
    };
    if address.is_empty() || address.chars().count() > 45 {
        return false;
    }
    // ``split(':', maxsplit=9)``: up to ten parts, extras stay fused in the last.
    let mut parts: Vec<&str> = address.splitn(10, ':').collect();
    if parts.len() < 3 {
        return false;
    }
    // An IPv4-style suffix becomes two hextets, exactly as in ipaddress.
    if parts[parts.len() - 1].contains('.') {
        if !parse_ipv4_text(parts[parts.len() - 1]) {
            return false;
        }
        parts.pop();
        parts.push("0");
        parts.push("0");
    }
    if parts.len() > 9 {
        return false;
    }
    let mut skip_index: Option<usize> = None;
    for (index, part) in parts
        .iter()
        .enumerate()
        .take(parts.len().saturating_sub(1))
        .skip(1)
    {
        if part.is_empty() {
            if skip_index.is_some() {
                return false;
            }
            skip_index = Some(index);
        }
    }
    let (parts_hi, parts_lo) = match skip_index {
        Some(index) => {
            let mut hi = index;
            let mut lo = parts.len() - index - 1;
            if parts[0].is_empty() {
                hi -= 1;
                if hi > 0 {
                    return false;
                }
            }
            if parts[parts.len() - 1].is_empty() {
                lo -= 1;
                if lo > 0 {
                    return false;
                }
            }
            if hi + lo > 7 {
                return false;
            }
            (hi, lo)
        }
        None => {
            if parts.len() != 8 || parts[0].is_empty() || parts[parts.len() - 1].is_empty() {
                return false;
            }
            (parts.len(), 0)
        }
    };
    for part in &parts[..parts_hi] {
        if !parse_hextet(part) {
            return false;
        }
    }
    for part in &parts[parts.len() - parts_lo..] {
        if !parse_hextet(part) {
            return false;
        }
    }
    true
}

enum AddressFamily {
    V4,
    V6,
}

/// Accept/reject mirror of ``ipaddress.ip_address``: IPv4 is attempted first,
/// then IPv6.
fn parse_ip_address(text: &str) -> Option<AddressFamily> {
    if parse_ipv4_text(text) {
        return Some(AddressFamily::V4);
    }
    if parse_ipv6_text(text) {
        return Some(AddressFamily::V6);
    }
    None
}

/// ``[v1.x]``-style IPvFuture literals: ``[vV][a-fA-F0-9]+\..+`` with any
/// characters except a newline in the tail.
fn is_ipv_future_body(body: &str) -> bool {
    let bytes = body.as_bytes();
    let Some(dot) = bytes.iter().position(|&byte| byte == b'.') else {
        return false;
    };
    if dot == 0 || bytes[..dot].iter().any(|byte| !byte.is_ascii_hexdigit()) {
        return false;
    }
    let tail = &bytes[dot + 1..];
    !tail.is_empty() && tail.iter().all(|&byte| byte != b'\n')
}

/// Port of ``urllib.parse._check_bracketed_host`` with the messages ``urlsplit``
/// itself raises for bracketed hosts.
fn check_bracketed_host(hostname: &str) -> Result<(), String> {
    if hostname.starts_with('v') || hostname.starts_with('V') {
        return if is_ipv_future_body(&hostname[1..]) {
            Ok(())
        } else {
            Err("IPvFuture address is invalid".to_string())
        };
    }
    match parse_ip_address(hostname) {
        Some(AddressFamily::V4) => Err("An IPv4 address cannot be in brackets".to_string()),
        Some(AddressFamily::V6) => Ok(()),
        None => Err(format!(
            "{} does not appear to be an IPv4 or IPv6 address",
            python_str_repr(hostname)
        )),
    }
}

/// Bracketed-netloc validation exactly as ``urlsplit`` performs it before the
/// result is ever returned. Only payload without a Python-visible exception
/// path here: the messages match the ValueErrors the Python code propagates.
fn check_netloc(netloc: &str) -> Result<(), String> {
    let has_open = netloc.contains('[');
    let has_close = netloc.contains(']');
    if has_open != has_close {
        return Err("Invalid IPv6 URL".to_string());
    }
    if !has_open {
        return Ok(());
    }
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    let host = match hostinfo.split_once('[') {
        Some((before, bracketed)) => {
            if !before.is_empty() {
                return Err("Invalid IPv6 URL".to_string());
            }
            match bracketed.split_once(']') {
                Some((host, tail)) => {
                    if !tail.is_empty() && !tail.starts_with(':') {
                        return Err("Invalid IPv6 URL".to_string());
                    }
                    host
                }
                // Unreachable once the balanced-brackets check above passed,
                // but ``partition(']')`` semantics would hand us the whole
                // remainder, so mirror that instead of asserting.
                None => bracketed,
            }
        }
        None => match hostinfo.split_once(':') {
            Some((host, _)) => host,
            None => hostinfo,
        },
    };
    check_bracketed_host(host)
}

/// Port of ``_host_of``: the fully lower-cased hostname, empty when absent.
pub(crate) fn host_of_core(url: &str) -> String {
    let (_, netloc) = url_split_parts(url);
    // ``urlsplit(...).hostname`` keeps scoped-IPv6 zone ids verbatim, but the
    // Python helper lower-cases the whole result afterwards.
    netloc_hostname(&netloc).unwrap_or_default().to_lowercase()
}

/// Port of ``_normalise_base_url``. The receiver is the trimmed text; the
/// error strings are the exact, already-redacted Python messages.
pub(crate) fn normalise_base_url_core(url: &str) -> Result<String, String> {
    let text = python_trim(url).trim_end_matches('/');
    if text.is_empty() {
        return Err("Open-Meteo base URL is empty".to_string());
    }
    let (scheme, netloc) = url_split_parts(text);
    // urlsplit validates the bracketed netloc before any caller judgement is
    // made, and its ValueError propagates uncaught in the Python source.
    check_netloc(&netloc)?;
    if scheme != "http" && scheme != "https" {
        return Err(format!(
            "Open-Meteo base URL must be http or https: {}",
            redact_core(text)
        ));
    }
    if netloc_hostname(&netloc).is_none() {
        return Err(format!(
            "Open-Meteo base URL has no host: {}",
            redact_core(text)
        ));
    }
    Ok(text.to_string())
}

// ---------------------------------------------------------------------------
// Access resolution
// ---------------------------------------------------------------------------

fn sha256_hex(data: &[u8]) -> String {
    digest(&SHA256, data)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Port of ``OpenMeteoAccess.credential_scope``. The digests are one-way so two
/// subscriptions can be told apart without ever storing either key.
pub(crate) fn credential_scope_core(mode: &str, api_key: Option<&str>, host: &str) -> String {
    if mode == MODE_CUSTOMER && api_key.is_some() {
        return format!(
            "customer-{}",
            &sha256_hex(api_key.unwrap_or("").as_bytes())[..16]
        );
    }
    if mode == MODE_SELF_HOSTED {
        return format!("self-hosted-{}", &sha256_hex(host.as_bytes())[..16]);
    }
    "free".to_string()
}

/// Port of ``resolve_access`` for explicitly supplied arguments. The
/// environment fallback stays on the Python side; only the validation rules,
/// the mode inference, and the exact messages are mirrored here. Empty or
/// whitespace-only values count as absent, and a key is never echoed back in
/// an error.
pub(crate) fn resolve_access_core(
    mode: Option<&str>,
    api_key: Option<&str>,
    base_url: Option<&str>,
) -> Result<(String, String, Option<String>), String> {
    let key_text = python_trim(api_key.unwrap_or(""));
    let url_text = python_trim(base_url.unwrap_or(""));
    let mut requested = normalised_identifier(mode.unwrap_or(""));
    if requested.is_empty() {
        if !url_text.is_empty() {
            requested = MODE_SELF_HOSTED.to_string();
        } else if !key_text.is_empty() {
            requested = MODE_CUSTOMER.to_string();
        } else {
            requested = MODE_FREE.to_string();
        }
    } else if !MODES.contains(&requested.as_str()) {
        return Err(format!(
            "unknown Open-Meteo access mode {}; expected one of {}",
            python_str_repr(&requested),
            MODES.join(", ")
        ));
    }
    if requested == MODE_SELF_HOSTED {
        if url_text.is_empty() {
            return Err(format!(
                "Open-Meteo self-hosted access needs a base URL; set {SELF_HOSTED_URL_ENV}"
            ));
        }
        if !key_text.is_empty() {
            return Err(format!(
                "refusing to forward an Open-Meteo key to a self-hosted instance; \
                 unset {API_KEY_ENV} or switch to customer access"
            ));
        }
        let base_url = normalise_base_url_core(url_text)?;
        return Ok((requested, base_url, None));
    }
    if requested == MODE_CUSTOMER {
        let secret = if key_text.is_empty() {
            None
        } else {
            Some(key_text.to_string())
        };
        return Ok((requested, CUSTOMER_BASE_URL.to_string(), secret));
    }
    if !key_text.is_empty() {
        return Err(format!(
            "free Open-Meteo access does not accept a key, and sending one to \
             {FREE_HOST} would leak it; unset {API_KEY_ENV} or set \
             {MODE_ENV}={MODE_CUSTOMER}"
        ));
    }
    Ok((requested, FREE_BASE_URL.to_string(), None))
}

// ---------------------------------------------------------------------------
// Provider catalog and normalization helpers
// ---------------------------------------------------------------------------

/// Port of ``namespaced_key``.
pub(crate) fn namespaced_key_core(api_model: &str) -> String {
    format!(
        "openmeteo-{}",
        normalised_identifier(api_model).replace('_', "-")
    )
}

/// Port of ``_api_model_for_key``.
pub(crate) fn api_model_for_key_core(model_key: &str) -> String {
    let text = normalised_identifier(model_key);
    let stripped = text.strip_prefix("openmeteo-").unwrap_or(&text);
    stripped.replace('-', "_")
}

/// Port of ``is_selectable_api_model``. Seamless, best-match, and ensemble
/// identifiers are rejected by shape as well as by list, so a provider adding
/// one later cannot become selectable through an omission.
pub(crate) fn is_selectable_api_model_core(
    api_model: &str,
    enabled_api_models: &[String],
    withheld_api_models: &[String],
) -> bool {
    let text = normalised_identifier(api_model);
    if text.is_empty() || withheld_api_models.contains(&text) {
        return false;
    }
    if text == "best_match" || text.ends_with("_seamless") {
        return false;
    }
    if text.contains("ensemble") || text.contains("_member") {
        return false;
    }
    enabled_api_models.contains(&text)
}

/// Port of ``hourly_variables``: one variable per pressure family and level,
/// then the surface fields, in the exact Python order.
pub(crate) fn hourly_variables_core(pressure_levels: &[i64]) -> Vec<String> {
    let mut names = Vec::with_capacity(
        PRESSURE_FAMILIES.len() * pressure_levels.len() + SURFACE_VARIABLES.len(),
    );
    for family in PRESSURE_FAMILIES {
        for level in pressure_levels {
            names.push(format!("{family}_{level}hPa"));
        }
    }
    for variable in SURFACE_VARIABLES {
        names.push(variable.to_string());
    }
    names
}

/// Port of ``normalise_longitude``: the -180..180 convention. NaN passes
/// through because every comparison against it in the domain test is false.
pub(crate) fn normalise_longitude_core(lon: f64) -> f64 {
    (lon + 180.0).rem_euclid(360.0) - 180.0
}

/// Port of ``point_in_domain``. ``domain_bounds`` follows the Python
/// ``(lon0, lon1, lat0, lat1)`` order, and a wrapped ``lon0 > lon1`` box means
/// a domain crossing the antimeridian.
pub(crate) fn point_in_domain_core(
    domain_bounds: (f64, f64, f64, f64),
    lat: f64,
    lon: f64,
) -> bool {
    let (lon0, lon1, lat0, lat1) = domain_bounds;
    if !(-90.0..=90.0).contains(&lat) {
        return false;
    }
    if !(lat0..=lat1).contains(&lat) {
        return false;
    }
    let longitude = normalise_longitude_core(lon);
    if lon0 <= lon1 {
        return (lon0..=lon1).contains(&longitude);
    }
    // Wrapped box across the antimeridian.
    longitude >= lon0 || longitude <= lon1
}

/// Port of ``weighted_units``: Open-Meteo bills a call as more than one unit
/// once it exceeds ten variables.
pub(crate) fn weighted_units_core(variable_count: i64) -> f64 {
    (variable_count as f64 / 10.0).max(1.0)
}

/// One selectable cycle: exactly the epoch ``datetime.replace(hour=cycle)``
/// would produce. Datetime refuses hours outside 0..=23 and so does this.
fn cycle_epoch(day_start: i64, cycle: i64) -> Result<i64, String> {
    if !(0..=23).contains(&cycle) {
        return Err("hour must be in 0..23".to_string());
    }
    Ok(day_start + cycle * 3600)
}

/// Port of ``_floor_cycle`` in epoch space: the most recent cycle at or before
/// the given instant, floored onto UTC days with euclidean division so
/// pre-epoch timestamps behave exactly like Python's aware-UTC datetime path.
pub(crate) fn floor_cycle_epoch_core(epoch_seconds: i64, cycles: &[i64]) -> Result<i64, String> {
    let mut ordered: Vec<i64> = cycles.to_vec();
    ordered.sort_unstable();
    ordered.dedup();
    let Some(&latest) = ordered.last() else {
        return Err("model publishes no cycles".to_string());
    };
    let day_start = epoch_seconds.div_euclid(86_400) * 86_400;
    let hour = epoch_seconds.rem_euclid(86_400) / 3600;
    for &cycle in ordered.iter().rev() {
        if cycle <= hour {
            return cycle_epoch(day_start, cycle);
        }
    }
    cycle_epoch(day_start - 86_400, latest)
}

/// Port of ``_retry_after`` given the raw header string: a parsed float clamped
/// to ``[0.0, 300.0]``. NaN collapses to 0.0 because Python's
/// ``max(0.0, float('nan'))`` keeps the left-hand zero, and any unparsable or
/// missing value is 0.0; ``inf`` clamps to 300.0 and ``-inf`` to 0.0.
pub(crate) fn retry_after_seconds_core(raw: Option<&str>) -> f64 {
    let value = match raw.map(str::trim).map(str::parse::<f64>) {
        Some(Ok(value)) => value,
        _ => return 0.0,
    };
    if value.is_nan() {
        return 0.0;
    }
    let clamped = value.clamp(0.0, 300.0);
    // ``max(0.0, value)`` leaves Python with +0.0 even for "-0" input.
    if clamped == 0.0 {
        return 0.0;
    }
    clamped
}

// ---------------------------------------------------------------------------
// Response-side selection math
// ---------------------------------------------------------------------------

/// Message derived from ``openmeteo.py``'s surface-height refusals.
#[derive(Debug, PartialEq)]
pub(crate) enum SurfaceHeightError {
    TooFewLevels,
    UnusableLevels,
    /// ``math.log`` of a non-positive surface pressure raises
    /// ``ValueError: math domain error`` in the Python source.
    DomainError,
    NotFinite,
}

impl SurfaceHeightError {
    fn message(&self) -> &'static str {
        match self {
            Self::TooFewLevels => {
                "Open-Meteo returned too few pressure levels to place the surface"
            }
            Self::UnusableLevels => "Open-Meteo pressure levels are unusable for a surface height",
            Self::DomainError => "math domain error",
            Self::NotFinite => "derived Open-Meteo surface height is not finite",
        }
    }
}

/// Port of ``_derive_surface_height``: the model's own geopotential profile
/// interpolated in log pressure to the reported surface pressure, with the same
/// straddling-pair search, the same nearest-edge fallback, and the same
/// refusals for degenerate ladders and non-finite results.
pub(crate) fn derive_surface_height_core(
    pairs: &[(f64, f64)],
    surface_pressure: f64,
) -> Result<f64, SurfaceHeightError> {
    if pairs.len() < 2 {
        return Err(SurfaceHeightError::TooFewLevels);
    }
    let straddling = pairs
        .windows(2)
        .find(|window| window[0].0 >= surface_pressure && surface_pressure >= window[1].0);
    let (lower, upper) = match straddling {
        Some(window) => (window[0], window[1]),
        None if surface_pressure > pairs[0].0 => (pairs[0], pairs[1]),
        None => (pairs[pairs.len() - 2], pairs[pairs.len() - 1]),
    };
    let (p_low, h_low) = lower;
    let (p_high, h_high) = upper;
    // ``p_low == p_high`` is a rejected ladder, not a usable static level; the
    // partial_cmp spelling keeps NaN unordered, exactly like Python's ``==``.
    if p_low <= 0.0 || p_high <= 0.0 || p_low.partial_cmp(&p_high) == Some(Ordering::Equal) {
        return Err(SurfaceHeightError::UnusableLevels);
    }
    if surface_pressure <= 0.0 {
        return Err(SurfaceHeightError::DomainError);
    }
    let slope = (h_high - h_low) / (p_high.ln() - p_low.ln());
    let derived = h_low + slope * (surface_pressure.ln() - p_low.ln());
    if !derived.is_finite() {
        return Err(SurfaceHeightError::NotFinite);
    }
    Ok(derived)
}

/// Pure part of ``_select_time_index``: the messages for an absent or empty
/// time axis, a target the run does not contain, and an ambiguous timestamp.
/// ``epochs`` holds one entry per returned time, ``None`` where the Python
/// ``int(stamp)`` itself was refused.
pub(crate) fn select_time_index_core(
    epochs: &[Option<i64>],
    target_epoch: i64,
    label: &str,
    run_stamp: &str,
    valid_stamp: &str,
) -> Result<usize, String> {
    if epochs.is_empty() {
        return Err(format!(
            "Open-Meteo returned no time axis for {label} run {run_stamp}"
        ));
    }
    let mut matches: Vec<usize> = Vec::with_capacity(1);
    for (index, epoch) in epochs.iter().enumerate() {
        if *epoch == Some(target_epoch) {
            matches.push(index);
        }
    }
    if matches.is_empty() {
        return Err(format!(
            "{label} run {run_stamp} does not contain {valid_stamp}. The run may not \
             be ingested yet, or that forecast hour may lie past its horizon."
        ));
    }
    if matches.len() > 1 {
        return Err(format!(
            "Open-Meteo returned {} values for {valid_stamp}; refusing an \
             ambiguous timestamp",
            matches.len()
        ));
    }
    Ok(matches[0])
}

// ---------------------------------------------------------------------------
// PyO3 bindings
// ---------------------------------------------------------------------------
#[pyfunction]
fn openmeteo_redact(text: &str) -> String {
    redact_core(text)
}

#[pyfunction]
fn openmeteo_host_of(url: &str) -> String {
    host_of_core(url)
}

#[pyfunction]
fn openmeteo_normalise_base_url(url: &str) -> PyResult<String> {
    normalise_base_url_core(url).map_err(PyValueError::new_err)
}

#[pyfunction]
fn openmeteo_credential_scope(mode: &str, api_key: Option<&str>, host: &str) -> String {
    credential_scope_core(mode, api_key, host)
}

#[pyfunction]
#[pyo3(signature = (mode = None, api_key = None, base_url = None))]
fn openmeteo_resolve_access(
    mode: Option<&str>,
    api_key: Option<&str>,
    base_url: Option<&str>,
) -> PyResult<(String, String, Option<String>)> {
    resolve_access_core(mode, api_key, base_url).map_err(PyValueError::new_err)
}

#[pyfunction]
fn openmeteo_retry_after_seconds(raw: Option<&str>) -> f64 {
    retry_after_seconds_core(raw)
}

#[pyfunction]
fn openmeteo_namespaced_key(api_model: &str) -> String {
    namespaced_key_core(api_model)
}

#[pyfunction]
fn openmeteo_api_model_for_key(model_key: &str) -> String {
    api_model_for_key_core(model_key)
}

#[pyfunction]
fn openmeteo_is_selectable_api_model(
    api_model: &str,
    enabled_api_models: Vec<String>,
    withheld_api_models: Vec<String>,
) -> bool {
    is_selectable_api_model_core(api_model, &enabled_api_models, &withheld_api_models)
}

#[pyfunction]
fn openmeteo_hourly_variables(pressure_levels: Vec<i64>) -> Vec<String> {
    hourly_variables_core(&pressure_levels)
}

#[pyfunction]
fn openmeteo_normalise_longitude(lon: f64) -> f64 {
    normalise_longitude_core(lon)
}

#[pyfunction]
fn openmeteo_point_in_domain(domain_bounds: (f64, f64, f64, f64), lat: f64, lon: f64) -> bool {
    point_in_domain_core(domain_bounds, lat, lon)
}

#[pyfunction]
fn openmeteo_floor_cycle_epoch(epoch_seconds: i64, cycles: Vec<i64>) -> PyResult<i64> {
    floor_cycle_epoch_core(epoch_seconds, &cycles).map_err(PyValueError::new_err)
}

#[pyfunction]
fn openmeteo_select_time_index(
    times: &Bound<'_, PyAny>,
    target_epoch: i64,
    label: &str,
    run_stamp: &str,
    valid_stamp: &str,
) -> PyResult<usize> {
    // ``isinstance(times, (list, tuple))`` gates everything, exactly like the
    // Python guard, so iteration is only ever attempted for real sequences.
    let usable_axis = times.is_instance_of::<PyList>() || times.is_instance_of::<PyTuple>();
    if !usable_axis {
        return Err(PyValueError::new_err(format!(
            "Open-Meteo returned no time axis for {label} run {run_stamp}"
        )));
    }
    let py = times.py();
    let integer = PyModule::import(py, "builtins")?.getattr("int")?;
    let mut epochs: Vec<Option<i64>> = Vec::new();
    for item in times.try_iter()? {
        let item = item?;
        let stamp = match integer.call1((&item,)) {
            Ok(stamp) => stamp,
            // ``_as_epoch`` swallows exactly TypeError and ValueError, the
            // two failures ``int(stamp)`` signals for unusable values.
            Err(error)
                if error.is_instance_of::<PyTypeError>(py)
                    || error.is_instance_of::<PyValueError>(py) =>
            {
                epochs.push(None);
                continue;
            }
            Err(error) => return Err(error),
        };
        // An integer wider than i64 simply never equals the target, mirroring
        // the Python equality test against an unbounded int.
        match stamp.extract::<i64>() {
            Ok(epoch) => epochs.push(Some(epoch)),
            Err(_) => epochs.push(None),
        }
    }
    select_time_index_core(&epochs, target_epoch, label, run_stamp, valid_stamp)
        .map_err(PyValueError::new_err)
}

#[pyfunction]
fn openmeteo_derive_surface_height(pairs: Vec<(f64, f64)>, surface_pressure: f64) -> PyResult<f64> {
    derive_surface_height_core(&pairs, surface_pressure)
        .map_err(|error| PyValueError::new_err(error.message()))
}

#[pyfunction]
fn openmeteo_weighted_units(variable_count: i64) -> f64 {
    weighted_units_core(variable_count)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(openmeteo_redact, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_host_of, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_normalise_base_url, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_credential_scope, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_resolve_access, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_retry_after_seconds, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_namespaced_key, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_api_model_for_key, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_is_selectable_api_model, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_hourly_variables, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_normalise_longitude, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_point_in_domain, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_floor_cycle_epoch, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_select_time_index, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_derive_surface_height, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_weighted_units, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redact_scrubs_values_and_preserves_marker_case() {
        let secret = "om-live-key-3f9c2a17";
        assert_eq!(
            redact_core(&format!(
                "https://host/v1/forecast?lat=1&apikey={secret}&b=2"
            )),
            "https://host/v1/forecast?lat=1&apikey=<redacted>&b=2"
        );
        assert_eq!(
            redact_core(&format!(
                "HTTPError for url: https://host/v1/forecast?apikey={secret}"
            )),
            "HTTPError for url: https://host/v1/forecast?apikey=<redacted>"
        );
        assert_eq!(
            redact_core(&format!("apikey={secret}")),
            "apikey=<redacted>"
        );
        assert!(!redact_core(&format!("apikey={secret}")).contains(secret));
        // The matched marker keeps its own spelling; every occurrence is hit.
        assert_eq!(redact_core("APIKEY=abc"), "APIKEY=<redacted>");
        assert_eq!(
            redact_core("ApiKey=abc&apikey=def"),
            "ApiKey=<redacted>&apikey=<redacted>"
        );
        // No word boundary is required; the marker matches mid-string.
        assert_eq!(redact_core("xapikey=ab"), "xapikey=<redacted>");
    }

    #[test]
    fn redact_requires_a_value_and_stops_at_quote_query_and_whitespace() {
        assert_eq!(redact_core("apikey="), "apikey=");
        assert_eq!(redact_core("apikey=&x=1"), "apikey=&x=1");
        assert_eq!(redact_core("apikey=''"), "apikey=''");
        assert_eq!(redact_core("apikey=a'b"), "apikey=<redacted>'b");
        assert_eq!(redact_core("apikey=a b"), "apikey=<redacted> b");
        assert_eq!(redact_core("apikey=a\nb"), "apikey=<redacted>\nb");
        // The value may contain '=' but cannot span '&' or a second marker.
        assert_eq!(redact_core("apikey=a=b&c=d"), "apikey=<redacted>&c=d");
        assert_eq!(redact_core("apikey=oneapikey=two"), "apikey=<redacted>");
        // The C0 unit separators count as whitespace for re's \s class.
        assert_eq!(redact_core("apikey=a\u{1c}b"), "apikey=<redacted>\u{1c}b");
        // Dotless capital I and dotless i fold onto i, the Kelvin sign onto k.
        assert_eq!(redact_core("apİkey=abc"), "apİkey=<redacted>");
        assert_eq!(redact_core("apıkey=abc"), "apıkey=<redacted>");
        assert_eq!(redact_core("apIkey=abc"), "apIkey=<redacted>");
        assert_eq!(redact_core("api\u{212a}ey=abc"), "api\u{212a}ey=<redacted>");
        // Non-folding lookalikes must not match under re.IGNORECASE.
        assert_eq!(redact_core("apiϰey=abc"), "apiϰey=abc");
    }

    #[test]
    fn retry_after_parses_clamps_and_neutralizes() {
        assert_eq!(retry_after_seconds_core(Some("42")), 42.0);
        assert_eq!(retry_after_seconds_core(Some("  42  ")), 42.0);
        assert_eq!(retry_after_seconds_core(Some("1e2")), 100.0);
        assert_eq!(retry_after_seconds_core(Some("+.5")), 0.5);
        assert_eq!(retry_after_seconds_core(Some("-5")), 0.0);
        assert_eq!(retry_after_seconds_core(Some("500")), 300.0);
        assert_eq!(retry_after_seconds_core(Some("inf")), 300.0);
        assert_eq!(retry_after_seconds_core(Some("INF")), 300.0);
        assert_eq!(retry_after_seconds_core(Some("-inf")), 0.0);
        assert_eq!(retry_after_seconds_core(Some("1e999")), 300.0);
        assert_eq!(retry_after_seconds_core(Some("-1e999")), 0.0);
        // Python: min(300.0, max(0.0, float("nan"))) == 0.0.
        assert_eq!(retry_after_seconds_core(Some("nan")), 0.0);
        assert_eq!(retry_after_seconds_core(Some("-NaN")), 0.0);
        assert_eq!(retry_after_seconds_core(Some("abc")), 0.0);
        assert_eq!(retry_after_seconds_core(Some("")), 0.0);
        assert_eq!(retry_after_seconds_core(None), 0.0);
    }

    #[test]
    fn credential_scope_hashes_key_or_host_and_stays_one_way() {
        assert_eq!(
            credential_scope_core(MODE_CUSTOMER, Some("abc"), "ignored.example"),
            "customer-ba7816bf8f01cfea"
        );
        assert_eq!(
            credential_scope_core(MODE_CUSTOMER, None, "ignored.example"),
            "free"
        );
        assert_eq!(
            credential_scope_core(MODE_SELF_HOSTED, Some("never forwarded"), "om.example.test"),
            "self-hosted-8fa0190a19da4ff9"
        );
        assert_eq!(
            credential_scope_core(MODE_SELF_HOSTED, None, "single-runs-api.open-meteo.com"),
            "self-hosted-b550f5fac623bc26"
        );
        assert_eq!(credential_scope_core(MODE_FREE, None, "any"), "free");
        // Two keys stay distinguishable without either being stored.
        assert_eq!(
            credential_scope_core(MODE_CUSTOMER, Some("om-live-key-3f9c2a17"), "h"),
            "customer-e148ddac4daa07f7"
        );
    }

    #[test]
    fn host_of_matches_urlsplit_hostname_behaviour() {
        assert_eq!(
            host_of_core("https://single-runs-api.open-meteo.com/v1/forecast"),
            "single-runs-api.open-meteo.com"
        );
        assert_eq!(
            host_of_core("HTTPS://Host.Example.COM:8443/p?q#f"),
            "host.example.com"
        );
        assert_eq!(
            host_of_core("https://user:pass@Host.Example:80/x"),
            "host.example"
        );
        assert_eq!(host_of_core("https://@host/"), "host");
        // No scheme: "//host/path" still exposes a netloc, "host/path" does not.
        assert_eq!(host_of_core("//host/path"), "host");
        assert_eq!(host_of_core("host/path"), "");
        assert_eq!(host_of_core("not-a-url"), "");
        assert_eq!(host_of_core("1http://host/"), "");
        assert_eq!(host_of_core("://host/"), "");
        assert_eq!(host_of_core("http://"), "");
        // IPv6 hosts, with the scope id lower-cased by the Python helper.
        assert_eq!(host_of_core("https://[2001:db8::1]:8080/x"), "2001:db8::1");
        assert_eq!(
            host_of_core("https://[fe80::822a:a8ff:fe49:470c%tESt]:1234/keys"),
            "fe80::822a:a8ff:fe49:470c%test"
        );
        // \t/\n/\r are removed anywhere before parsing (CPython behaviour).
        assert_eq!(host_of_core(" http://spaced/"), "spaced");
        assert_eq!(host_of_core("http\n://newline/"), "newline");
        assert_eq!(host_of_core("http://a\rb/x"), "ab");
        // A net-loc that only exists because '?' terminates it.
        assert_eq!(host_of_core("http://host?apikey=1"), "host");
        assert_eq!(host_of_core("http://[::1]"), "::1");
    }

    #[test]
    fn normalise_base_url_accepts_and_trims_like_python() {
        assert_eq!(
            normalise_base_url_core("https://om.example.test/"),
            Ok("https://om.example.test".to_string())
        );
        assert_eq!(
            normalise_base_url_core("  http://om.example.test///  "),
            Ok("http://om.example.test".to_string())
        );
        // The scheme comparison follows urlsplit, which lower-cases schemes.
        assert_eq!(
            normalise_base_url_core("HTTP://om.example.test"),
            Ok("HTTP://om.example.test".to_string())
        );
        assert_eq!(
            normalise_base_url_core(" http://[2001:db8::1]:9000 "),
            Ok("http://[2001:db8::1]:9000".to_string())
        );
        // These malformed self-hosted inputs carry the exact Python messages.
        assert_eq!(
            normalise_base_url_core(""),
            Err("Open-Meteo base URL is empty".to_string())
        );
        assert_eq!(
            normalise_base_url_core("///"),
            Err("Open-Meteo base URL is empty".to_string())
        );
        assert_eq!(
            normalise_base_url_core("ftp://om.example.test"),
            Err("Open-Meteo base URL must be http or https: ftp://om.example.test".to_string())
        );
        assert_eq!(
            normalise_base_url_core("not-a-url"),
            Err("Open-Meteo base URL must be http or https: not-a-url".to_string())
        );
        assert_eq!(
            normalise_base_url_core("https://"),
            Err("Open-Meteo base URL has no host: https:".to_string())
        );
        assert_eq!(
            normalise_base_url_core("http://user@/"),
            Err("Open-Meteo base URL has no host: http://user@".to_string())
        );
        // A redact-able payload is scrubbed without a second thought.
        assert_eq!(
            normalise_base_url_core("ftp://h/?apikey=om-live-key-3f9c2a17"),
            Err("Open-Meteo base URL must be http or https: \
                 ftp://h/?apikey=<redacted>"
                .to_string())
        );
    }

    #[test]
    fn normalise_base_url_validates_bracketed_hosts_like_urlsplit() {
        assert_eq!(
            normalise_base_url_core("http://[::1/x"),
            Err("Invalid IPv6 URL".to_string())
        );
        assert_eq!(
            normalise_base_url_core("http://x[1]/x"),
            Err("Invalid IPv6 URL".to_string())
        );
        assert_eq!(
            normalise_base_url_core("http://[1]x/"),
            Err("Invalid IPv6 URL".to_string())
        );
        assert_eq!(
            normalise_base_url_core("http://[1.2.3.4]/x"),
            Err("An IPv4 address cannot be in brackets".to_string())
        );
        assert_eq!(
            normalise_base_url_core("http://[v.foo]/x"),
            Err("IPvFuture address is invalid".to_string())
        );
        assert_eq!(
            normalise_base_url_core("http://[12345::]/x"),
            Err("'12345::' does not appear to be an IPv4 or IPv6 address".to_string())
        );
        let quoted = normalise_base_url_core("http://[1:2::3:4:5:6:7:8:9]/x");
        assert!(quoted.is_err());
        assert!(quoted
            .unwrap_err()
            .contains("does not appear to be an IPv4 or IPv6 address"));
        // Balanced, valid literals pass through untouched.
        assert_eq!(
            normalise_base_url_core("http://[fe80::1%eth0]:9/x"),
            Ok("http://[fe80::1%eth0]:9/x".to_string())
        );
    }

    #[test]
    fn resolve_access_infers_and_validates_like_python() {
        let (mode, base, key) = resolve_access_core(None, None, None).unwrap();
        assert_eq!(mode, "free-direct");
        assert_eq!(base, "https://single-runs-api.open-meteo.com");
        assert!(key.is_none());
        let (mode, base, key) =
            resolve_access_core(None, Some(" om-live-key-3f9c2a17 "), None).unwrap();
        assert_eq!(mode, "customer-direct");
        assert_eq!(base, "https://customer-single-runs-api.open-meteo.com");
        assert_eq!(key.as_deref(), Some("om-live-key-3f9c2a17"));
        // An empty or whitespace key counts as absent, so the mode becomes free.
        let (mode, _, key) = resolve_access_core(None, Some("   "), None).unwrap();
        assert_eq!(mode, "free-direct");
        assert!(key.is_none());
        let (mode, base, key) =
            resolve_access_core(None, None, Some(" https://om.example.test ")).unwrap();
        assert_eq!(mode, "self-hosted");
        assert_eq!(base, "https://om.example.test");
        assert!(key.is_none());
        let (_, base, key) = resolve_access_core(Some("CUSTOMER-DIRECT"), Some("k"), None).unwrap();
        assert_eq!(base, "https://customer-single-runs-api.open-meteo.com");
        assert_eq!(key.as_deref(), Some("k"));
    }

    #[test]
    fn resolve_access_refusals_never_echo_the_credential() {
        let secret = "om-live-key-3f9c2a17";
        for (mode, key, url, expected) in [
            (
                Some("Proxy-Through-SharpPy"),
                None,
                None,
                "unknown Open-Meteo access mode 'proxy-through-sharppy'; expected one \
                 of free-direct, customer-direct, self-hosted",
            ),
            (
                Some("self-hosted"),
                None,
                None,
                "Open-Meteo self-hosted access needs a base URL; set SHARPMOD_OPENMETEO_URL",
            ),
            (
                Some("self-hosted"),
                Some(secret),
                Some("https://om.example.test"),
                "refusing to forward an Open-Meteo key to a self-hosted instance; \
                 unset SHARPMOD_OPENMETEO_API_KEY or switch to customer access",
            ),
            (
                Some("free-direct"),
                Some(secret),
                None,
                "free Open-Meteo access does not accept a key, and sending one to \
                 single-runs-api.open-meteo.com would leak it; unset \
                 SHARPMOD_OPENMETEO_API_KEY or set SHARPMOD_OPENMETEO_MODE=customer-direct",
            ),
        ] {
            let error = resolve_access_core(mode, key, url).unwrap_err();
            assert_eq!(error, expected.to_string());
            assert!(!error.contains(secret));
        }
        // A malformed URL surfaces for self-hosted mode through urlsplit.
        assert_eq!(
            resolve_access_core(Some("self-hosted"), None, Some("https://")).unwrap_err(),
            "Open-Meteo base URL has no host: https:"
        );
    }

    #[test]
    fn floor_cycle_finds_the_most_recent_cycle() {
        // 2026-08-29T14:30Z, as in the provider's own tests.
        let afternoon = 1_788_013_800;
        assert_eq!(
            floor_cycle_epoch_core(afternoon, &[18, 6, 12, 0]),
            Ok(1_788_004_800) // 12Z that day
        );
        assert_eq!(
            floor_cycle_epoch_core(afternoon, &[0, 12]),
            Ok(1_788_004_800)
        );
        assert_eq!(
            floor_cycle_epoch_core(afternoon, &[6, 18]),
            Ok(1_787_983_200)
        );
        assert_eq!(floor_cycle_epoch_core(afternoon, &[12]), Ok(1_788_004_800));
        assert_eq!(floor_cycle_epoch_core(afternoon, &[6]), Ok(1_787_983_200));
        assert_eq!(floor_cycle_epoch_core(afternoon, &[0]), Ok(1_787_961_600));
        // 03:00Z with only 6Z/18Z runs falls back to the previous 18Z (9h back).
        assert_eq!(
            floor_cycle_epoch_core(1_787_972_400, &[6, 18]),
            Ok(1_787_972_400 - 9 * 3600)
        );
        // Midnight itself keeps the cycle that struck at 00Z.
        assert_eq!(
            floor_cycle_epoch_core(1_787_961_600, &[0, 6]),
            Ok(1_787_961_600)
        );
        // ... but with only later cycles it rolls to the previous day.
        assert_eq!(
            floor_cycle_epoch_core(1_787_961_600, &[6]),
            Ok(1_787_896_800)
        );
        // Euclidean division keeps pre-epoch timestamps on the UTC grid:
        // 1969-12-31T23:30Z relative to 6Z cycles lands on that day's 06Z.
        assert_eq!(floor_cycle_epoch_core(-1800, &[6]), Ok(-64_800));
        // Duplicates are collapsed; the runs publish no cycles at all.
        assert_eq!(
            floor_cycle_epoch_core(afternoon, &[12, 12, 12]),
            Ok(1_788_004_800)
        );
        assert_eq!(
            floor_cycle_epoch_core(afternoon, &[]),
            Err("model publishes no cycles".to_string())
        );
        // datetime.replace would raise for an impossible hour, and so does this.
        assert_eq!(
            floor_cycle_epoch_core(afternoon, &[30]),
            Err("hour must be in 0..23".to_string())
        );
        assert_eq!(
            floor_cycle_epoch_core(-1800, &[-1]),
            Err("hour must be in 0..23".to_string())
        );
    }

    #[test]
    fn derive_surface_height_interpolates_and_extrapolates_like_python() {
        let ladder = [(1000.0, 100.0), (850.0, 1000.0)];
        assert!(
            (derive_surface_height_core(&ladder, 900.0).unwrap() - 683.4671960064932).abs() < 1e-12
        );
        assert!(
            (derive_surface_height_core(&ladder, 1100.0).unwrap() - (-427.8102808637259)).abs()
                < 1e-12
        );
        assert!(
            (derive_surface_height_core(&ladder, 800.0).unwrap() - 1335.7280275190292).abs()
                < 1e-12
        );
        // A middle bracket must be preferred over the default edge pair.
        let tall = [(1000.0, 111.0), (850.0, 200.0), (700.0, 777.0)];
        assert!(
            (derive_surface_height_core(&tall, 950.0).unwrap() - 139.0896706285189).abs() < 1e-12
        );
        assert!(
            (derive_surface_height_core(&tall, 760.0).unwrap() - 532.601788492816).abs() < 1e-12
        );
        // The last pair extrapolates a surface below the lowest isobar.
        assert!(
            (derive_surface_height_core(&tall, 650.0).unwrap() - 997.2368031493589).abs() < 1e-12
        );
        // A surface exactly on an isobar uses the straddling window it opens;
        // libm rounding means the second boundary needs a tolerance, exactly
        // like the Python float it mirrors.
        assert_eq!(derive_surface_height_core(&tall, 1000.0), Ok(111.0));
        assert!((derive_surface_height_core(&tall, 700.0).unwrap() - 777.0).abs() < 1e-9);
    }

    #[test]
    fn derive_surface_height_refuses_degenerate_ladders_like_python() {
        assert_eq!(
            derive_surface_height_core(&[(1000.0, 5.0)], 999.0),
            Err(SurfaceHeightError::TooFewLevels)
        );
        assert_eq!(
            derive_surface_height_core(&[], 999.0),
            Err(SurfaceHeightError::TooFewLevels)
        );
        assert_eq!(
            derive_surface_height_core(&[(0.0, 1.0), (-5.0, 2.0)], 900.0),
            Err(SurfaceHeightError::UnusableLevels)
        );
        assert_eq!(
            derive_surface_height_core(&[(900.0, 1.0), (900.0, 2.0)], 900.0),
            Err(SurfaceHeightError::UnusableLevels)
        );
        // math.log of a non-positive surface pressure raises in Python.
        assert_eq!(
            derive_surface_height_core(&[(1000.0, 1.0), (850.0, 2.0)], 0.0),
            Err(SurfaceHeightError::DomainError)
        );
        assert_eq!(
            derive_surface_height_core(&[(1000.0, 1.0), (850.0, 2.0)], -12.0),
            Err(SurfaceHeightError::DomainError)
        );
        // NaN flows through the logarithms to the not-finite refusal.
        assert_eq!(
            derive_surface_height_core(&[(1000.0, 1.0), (850.0, 2.0)], f64::NAN),
            Err(SurfaceHeightError::NotFinite)
        );
        assert_eq!(
            derive_surface_height_core(&[(f64::NAN, 1.0), (850.0, 2.0)], 900.0),
            Err(SurfaceHeightError::NotFinite)
        );
    }

    #[test]
    fn select_time_index_core_matches_by_timestamp_or_refuses() {
        let epochs: [Option<i64>; 4] = [Some(100), Some(200), None, Some(300)];
        assert_eq!(
            select_time_index_core(
                &epochs,
                100,
                "ICON Global 11 km",
                "2026-08-29 00:00Z",
                "2026-08-29 12:00Z"
            ),
            Ok(0)
        );
        // Unusable items are skipped; the first target wins.
        assert_eq!(
            select_time_index_core(&epochs, 200, "ICON Global 11 km", "r", "v"),
            Ok(1)
        );
        let unique: [Option<i64>; 2] = [Some(5), Some(6)];
        assert_eq!(select_time_index_core(&unique, 6, "m", "r", "v"), Ok(1));
        assert_eq!(
            select_time_index_core(&unique, 999, "m", "r", "v"),
            Err(
                "m run r does not contain v. The run may not be ingested yet, or \
                 that forecast hour may lie past its horizon."
                    .to_string()
            )
        );
        assert_eq!(
            select_time_index_core(&[], 999, "model", "2026-08-29 00:00Z", "v"),
            Err("Open-Meteo returned no time axis for model run 2026-08-29 00:00Z".to_string())
        );
        let duplicate: [Option<i64>; 3] = [Some(7), None, Some(7)];
        assert_eq!(
            select_time_index_core(&duplicate, 7, "m", "r", "2026-08-29 12:00Z"),
            Err(
                "Open-Meteo returned 2 values for 2026-08-29 12:00Z; refusing an \
                 ambiguous timestamp"
                    .to_string()
            )
        );
    }

    #[test]
    fn catalog_helpers_namespace_keys_and_gate_selection() {
        // namespaced_key <-> _api_model_for_key round-trips with Python's
        // trim-then-lower normalisation, and an unnamespaced key only loses
        // case and dashes.
        assert_eq!(namespaced_key_core("icon_global"), "openmeteo-icon-global");
        assert_eq!(
            namespaced_key_core(" ICON_Global "),
            "openmeteo-icon-global"
        );
        assert_eq!(
            namespaced_key_core("ecmwf_ifs025"),
            "openmeteo-ecmwf-ifs025"
        );
        assert_eq!(namespaced_key_core(""), "openmeteo-");
        assert_eq!(
            api_model_for_key_core("openmeteo-icon-global"),
            "icon_global"
        );
        assert_eq!(
            api_model_for_key_core("openmeteo-ecmwf-ifs025"),
            "ecmwf_ifs025"
        );
        assert_eq!(
            api_model_for_key_core("Icon-Global"),
            "icon-global".replace('-', "_")
        );
        assert_eq!(api_model_for_key_core("icon_global"), "icon_global");
        assert_eq!(api_model_for_key_core("openmeteo-"), "");

        // is_selectable_api_model refuses by list and by shape, and trims and
        // lower-cases the candidate first.
        let enabled = [String::from("icon_global"), String::from("ecmwf_ifs025")];
        let withheld = [
            "best_match",
            "ecmwf_ifs",
            "ecmwf_aifs025_single",
            "ncep_gfs_global",
            "jma_gsm",
        ]
        .map(String::from);
        assert!(is_selectable_api_model_core(
            "icon_global",
            &enabled,
            &withheld
        ));
        assert!(is_selectable_api_model_core(
            "ECMWF_IFS025",
            &enabled,
            &withheld
        ));
        assert!(is_selectable_api_model_core(
            " icon_global ",
            &enabled,
            &withheld
        ));
        for rejected in [
            "best_match",
            "ecmwf_aifs025_single",
            "ncep_hrrr_conus",
            "jma_gsm",
            // Shape rejections apply even to identifiers nobody listed.
            "icon_seamless",
            "ukmo_seamless",
            "ncep_hgefs025_ensemble_mean",
            "gfs_member_4",
            "",
            "cma_grapes_global",
        ] {
            assert!(!is_selectable_api_model_core(rejected, &enabled, &withheld));
        }
    }

    #[test]
    fn hourly_variables_lay_out_families_then_surface_fields() {
        let icon = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100];
        let names = hourly_variables_core(&icon);
        assert_eq!(names.len(), 5 * 12 + 5);
        assert_eq!(names[0], "temperature_1000hPa");
        assert_eq!(names[1], "temperature_925hPa");
        assert_eq!(names[11], "temperature_100hPa");
        assert_eq!(names[12], "dew_point_1000hPa");
        assert_eq!(names[59], "geopotential_height_100hPa");
        assert_eq!(names[60], "surface_pressure");
        assert_eq!(names[64], "wind_direction_10m");
        let unique: std::collections::HashSet<&str> = names.iter().map(String::as_str).collect();
        assert_eq!(unique.len(), names.len());
        assert!(names
            .iter()
            .all(|name| !name.starts_with("vertical_velocity")));
        // An empty ladder still yields the surface fields, like the tuple would.
        assert_eq!(
            hourly_variables_core(&[]),
            SURFACE_VARIABLES
                .iter()
                .map(|name| name.to_string())
                .collect::<Vec<String>>()
        );
        // weighted_units: a call costs more than one unit past ten variables,
        // which is what keeps a local budget honest for a wide sounding.
        assert_eq!(weighted_units_core(names.len() as i64), 6.5);
        assert_eq!(weighted_units_core(125), 12.5);
        assert_eq!(weighted_units_core(10), 1.0);
        assert_eq!(weighted_units_core(4), 1.0);
        assert_eq!(weighted_units_core(11), 1.1);
    }

    #[test]
    fn point_in_domain_respects_bounds_and_the_antimeridian_wrap() {
        let global = (-180.0, 180.0, -90.0, 90.0);
        assert!(point_in_domain_core(global, 38.77, -90.87));
        assert!(point_in_domain_core(global, 0.0, 180.0));
        assert!(!point_in_domain_core(global, 95.0, 0.0));
        assert!(!point_in_domain_core(global, -91.0, 0.0));
        assert!(!point_in_domain_core(global, f64::NAN, 0.0));
        assert!(!point_in_domain_core(global, 10.0, f64::NAN));
        let regional = (-125.0, -66.0, 24.0, 50.0);
        assert!(point_in_domain_core(regional, 36.68, -95.66));
        assert!(!point_in_domain_core(regional, 36.68, -60.0));
        assert!(!point_in_domain_core(regional, 55.0, -95.66));
        // Wrapped box: 170..(-170) means "crossing 180 degrees".
        let wrapped = (170.0, -170.0, 50.0, 60.0);
        assert!(point_in_domain_core(wrapped, 55.0, 175.0));
        assert!(point_in_domain_core(wrapped, 55.0, -175.0));
        assert!(!point_in_domain_core(wrapped, 55.0, 0.0));
        // A dateline longitude normalises into range first.
        assert!(point_in_domain_core(wrapped, 55.0, 185.0));
        // normalise_longitude: the shared -180..180 convention, with NaN
        // passing through because no comparison against it is ever true.
        for (value, expected) in [
            (-90.87, -90.87),
            (190.0, -170.0),
            (-190.0, 170.0),
            (180.0, -180.0),
            (-180.0, -180.0),
            (360.0, 0.0),
            (540.0, -180.0),
            (1.5, 1.5),
        ] {
            assert_eq!(normalise_longitude_core(value), expected);
        }
        assert!(normalise_longitude_core(f64::NAN).is_nan());
    }

    #[test]
    fn python_helpers_match_cpython_whitespace_and_repr() {
        assert_eq!(python_trim(" \t x \n "), "x");
        // The C0 unit separators strip like Python's str.strip() sees them.
        assert_eq!(python_trim("\u{1c}x\u{1d}"), "x");
        assert_eq!(python_trim("\u{a0}x\u{a0}"), "x");
        assert_eq!(python_trim(""), "");
        // ``%r`` of a str: single quotes unless the body holds one and no
        // double quote, with backslashes, newlines, and tabs escaped.
        assert_eq!(python_str_repr("proxy"), "'proxy'");
        assert_eq!(python_str_repr("can't"), "\"can't\"");
        assert_eq!(python_str_repr("say \"hi\""), "'say \"hi\"'");
        assert_eq!(
            python_str_repr("both \"x\" and 'y'"),
            "'both \"x\" and \\'y\\''"
        );
        assert_eq!(python_str_repr(""), "''");
        assert_eq!(python_str_repr("tab\tnew\n"), "'tab\\tnew\\n'");
        assert_eq!(python_str_repr("back\\slash"), "'back\\\\slash'");
    }
}

//! Native University of Wyoming (UWyo) sounding rules (milestone M39).
//!
//! Rust ports of the pure bookkeeping half of the two UWyo Python modules:
//!
//! * ``sharpmod.io.uwyo_decoder`` -- the high-resolution thinning rules
//!   ``UWyo_Decoder._pressure_spaced_indices``
//!   ([`uwyo_pressure_spaced_indices`]) and ``_subsample_indices``
//!   ([`uwyo_subsample_indices`]), the "no data for this station/time"
//!   page heuristic ``_looks_unavailable`` ([`uwyo_looks_unavailable`]),
//!   the ``<H1>``/``<H2>`` header extractor ``_parse_metadata``
//!   ([`uwyo_parse_metadata`]), and the station-resolution ladder of
//!   ``UWyo_Decoder.resolve_station`` ([`uwyo_resolve_station_index`]);
//! * ``sharpmod.io.uwyo_catalog`` -- ``_clean_name``
//!   ([`uwyo_clean_station_name`]) and ``search_stations``
//!   ([`uwyo_search_stations`]).
//!
//! Behaviour matches the Python sources verbatim: the same float
//! comparisons (NaN compares false exactly like NumPy), the same
//! ``round()`` ties-to-even rounding, the same ``%b``/``%d``/``%H``/``%Y``
//! acceptance as ``datetime.strptime`` (including valid-calendar-day and
//! year 1..9999 checks), full Unicode ``casefold()`` and Python
//! ``str.isspace()`` trimming semantics, and the same first-hit-wins
//! resolution order (exact id, exact caseless name, caseless substring).

use numpy::PyReadonlyArray1;
use pyo3::prelude::*;

/// Markers a UWyo "no data for this station/time" page carries
/// (``UWyo_Decoder._looks_unavailable``; compared against a casefolded
/// page, plain substring test like Python's ``in``).
const UNAVAILABLE_MARKERS: [&str; 7] = [
    "can't get",
    "cannot get",
    "no data",
    "sorry",
    "unable to",
    "not available",
    "invalid",
];

/// The ``%b`` month abbreviations ``datetime.strptime`` accepts in the C
/// locale, in month order (compared case-insensitively, exactly three
/// characters like the ``_strptime`` alternation).
const MONTH_ABBREVIATIONS: [&str; 12] = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// ---------------------------------------------------------------------------
// High-resolution thinning (UWyo_Decoder._pressure_spaced_indices)
// ---------------------------------------------------------------------------

/// The pure half of ``_pressure_spaced_indices``: indices of the levels at
/// least ``min_dp`` hPa apart with strictly decreasing pressure, always
/// keeping the first and the topmost finite-pressure level.
pub(crate) fn pressure_spaced_indices_core(pres: &[f64], min_dp: f64) -> Vec<usize> {
    let mut keep: Vec<usize> = Vec::new();
    let mut last_p: Option<f64> = None;
    for (index, &value) in pres.iter().enumerate() {
        if !value.is_finite() {
            continue;
        }
        if last_p.is_none_or(|previous| (previous - value) >= min_dp) {
            keep.push(index);
            last_p = Some(value);
        }
    }
    // Ensure the topmost valid level (lowest pressure) is present.
    if let Some(last_finite) = pres.iter().rposition(|value| value.is_finite()) {
        if keep.last() != Some(&last_finite)
            && last_p.is_none_or(|previous| pres[last_finite] < previous)
        {
            keep.push(last_finite);
        }
    }
    keep
}

/// Indices of the levels at least ``min_dp`` hPa apart (strictly decreasing
/// pressure), mirroring ``UWyo_Decoder._pressure_spaced_indices``.
///
/// Levels with non-finite pressure are dropped and never update the
/// reference pressure, and the topmost finite-pressure level is always
/// retained so the full sounding depth survives.  Strided (non-contiguous)
/// arrays are read through ``as_array()`` and work like contiguous ones.
#[pyfunction]
fn uwyo_pressure_spaced_indices(
    pres: PyReadonlyArray1<'_, f64>,
    min_dp: f64,
) -> PyResult<Vec<usize>> {
    let values: Vec<f64> = pres.as_array().iter().copied().collect();
    Ok(pressure_spaced_indices_core(&values, min_dp))
}

// ---------------------------------------------------------------------------
// Uniform subsampling (UWyo_Decoder._subsample_indices)
// ---------------------------------------------------------------------------

/// The pure half of ``_subsample_indices``: uniformly reduce ``indices`` to
/// ``max_count`` picks, endpoints guaranteed, with Python's ``round()``
/// ties-to-even applied to the ``np.linspace(0, n - 1, max_count)`` grid.
pub(crate) fn subsample_indices_core(indices: &[usize], max_count: usize) -> Vec<usize> {
    let count = indices.len();
    if count <= max_count || max_count < 2 {
        return indices.to_vec();
    }
    let mut chosen: Vec<usize> = (0..max_count)
        .map(|pick| {
            let position = ((count - 1) as f64 * pick as f64) / ((max_count - 1) as f64);
            position.round_ties_even() as usize
        })
        .collect();
    chosen.sort_unstable();
    chosen.dedup();
    // Guarantee both endpoints survive the rounding/dedup.
    if chosen.first() != Some(&0) {
        chosen.insert(0, 0);
    }
    if chosen.last() != Some(&(count - 1)) {
        chosen.push(count - 1);
    }
    chosen.iter().map(|pick| indices[*pick]).collect()
}

/// Uniformly reduce ``indices`` to ``max_count`` entries (endpoints kept),
/// mirroring ``UWyo_Decoder._subsample_indices``.
///
/// Positions follow ``np.linspace(0, n - 1, max_count)`` rounded with
/// Python's ties-to-even ``round``, then sorted, deduplicated, and pinned to
/// both endpoints before mapping back through ``indices``.
#[pyfunction]
fn uwyo_subsample_indices(indices: Vec<usize>, max_count: usize) -> Vec<usize> {
    subsample_indices_core(&indices, max_count)
}

// ---------------------------------------------------------------------------
// Unavailable-page heuristic (UWyo_Decoder._looks_unavailable)
// ---------------------------------------------------------------------------

/// Match Python ``str.isspace()`` / regex ``\s`` semantics (adds the four ASCII
/// file separators ``U+001C..=U+001F`` beyond Rust ``char::is_whitespace``).
fn is_python_space(ch: char) -> bool {
    ch.is_whitespace() || matches!(ch, '\u{001C}'..='\u{001F}')
}

fn python_strip(text: &str) -> &str {
    text.trim_matches(is_python_space)
}

/// Full Unicode case folding (``str.casefold()``) matching Python verbatim.
pub(crate) fn python_casefold(text: &str) -> String {
    if text.is_ascii() {
        return text.to_ascii_lowercase();
    }
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\u{00B5}' => out.push('\u{03BC}'),
            '\u{00DF}' | '\u{1E9E}' => out.push_str("ss"),
            '\u{0149}' => out.push_str("\u{02BC}n"),
            '\u{017F}' => out.push('s'),
            '\u{01F0}' => out.push_str("j\u{030C}"),
            '\u{0345}' | '\u{1FBE}' => out.push('\u{03B9}'),
            '\u{0390}' => out.push_str("\u{03B9}\u{0308}\u{0301}"),
            '\u{03B0}' => out.push_str("\u{03C5}\u{0308}\u{0301}"),
            '\u{03C2}' => out.push('\u{03C3}'),
            '\u{03D0}' => out.push('\u{03B2}'),
            '\u{03D1}' => out.push('\u{03B8}'),
            '\u{03D5}' => out.push('\u{03C6}'),
            '\u{03D6}' => out.push('\u{03C0}'),
            '\u{03F0}' => out.push('\u{03BA}'),
            '\u{03F1}' => out.push('\u{03C1}'),
            '\u{03F5}' => out.push('\u{03B5}'),
            '\u{0587}' => out.push_str("\u{0565}\u{0582}"),
            '\u{13A0}'..='\u{13F5}' => out.push(ch),
            '\u{13F8}'..='\u{13FD}' => out.extend(char::from_u32(ch as u32 - 8)),
            '\u{1C80}' => out.push('\u{0432}'),
            '\u{1C81}' => out.push('\u{0434}'),
            '\u{1C82}' => out.push('\u{043E}'),
            '\u{1C83}' => out.push('\u{0441}'),
            '\u{1C84}' | '\u{1C85}' => out.push('\u{0442}'),
            '\u{1C86}' => out.push('\u{044A}'),
            '\u{1C87}' => out.push('\u{0463}'),
            '\u{1C88}' => out.push('\u{A64B}'),
            '\u{1E96}' => out.push_str("h\u{0331}"),
            '\u{1E97}' => out.push_str("t\u{0308}"),
            '\u{1E98}' => out.push_str("w\u{030A}"),
            '\u{1E99}' => out.push_str("y\u{030A}"),
            '\u{1E9A}' => out.push_str("a\u{02BE}"),
            '\u{1E9B}' => out.push('\u{1E61}'),
            '\u{1F50}' => out.push_str("\u{03C5}\u{0313}"),
            '\u{1F52}' => out.push_str("\u{03C5}\u{0313}\u{0300}"),
            '\u{1F54}' => out.push_str("\u{03C5}\u{0313}\u{0301}"),
            '\u{1F56}' => out.push_str("\u{03C5}\u{0313}\u{0342}"),
            '\u{1F80}'..='\u{1F87}' => {
                out.extend(char::from_u32(ch as u32 - 0x80));
                out.push('\u{03B9}');
            }
            '\u{1F88}'..='\u{1F8F}' => {
                out.extend(char::from_u32(ch as u32 - 0x88));
                out.push('\u{03B9}');
            }
            '\u{1F90}'..='\u{1F97}' => {
                out.extend(char::from_u32(ch as u32 - 0x70));
                out.push('\u{03B9}');
            }
            '\u{1F98}'..='\u{1F9F}' => {
                out.extend(char::from_u32(ch as u32 - 0x78));
                out.push('\u{03B9}');
            }
            '\u{1FA0}'..='\u{1FA7}' => {
                out.extend(char::from_u32(ch as u32 - 0x40));
                out.push('\u{03B9}');
            }
            '\u{1FA8}'..='\u{1FAF}' => {
                out.extend(char::from_u32(ch as u32 - 0x48));
                out.push('\u{03B9}');
            }
            '\u{1FB2}' => out.push_str("\u{1F70}\u{03B9}"),
            '\u{1FB3}' | '\u{1FBC}' => out.push_str("\u{03B1}\u{03B9}"),
            '\u{1FB4}' => out.push_str("\u{03AC}\u{03B9}"),
            '\u{1FB6}' => out.push_str("\u{03B1}\u{0342}"),
            '\u{1FB7}' => out.push_str("\u{03B1}\u{0342}\u{03B9}"),
            '\u{1FC2}' => out.push_str("\u{1F74}\u{03B9}"),
            '\u{1FC3}' | '\u{1FCC}' => out.push_str("\u{03B7}\u{03B9}"),
            '\u{1FC4}' => out.push_str("\u{03AE}\u{03B9}"),
            '\u{1FC6}' => out.push_str("\u{03B7}\u{0342}"),
            '\u{1FC7}' => out.push_str("\u{03B7}\u{0342}\u{03B9}"),
            '\u{1FD2}' => out.push_str("\u{03B9}\u{0308}\u{0300}"),
            '\u{1FD3}' => out.push_str("\u{03B9}\u{0308}\u{0301}"),
            '\u{1FD6}' => out.push_str("\u{03B9}\u{0342}"),
            '\u{1FD7}' => out.push_str("\u{03B9}\u{0308}\u{0342}"),
            '\u{1FE2}' => out.push_str("\u{03C5}\u{0308}\u{0300}"),
            '\u{1FE3}' => out.push_str("\u{03C5}\u{0308}\u{0301}"),
            '\u{1FE4}' => out.push_str("\u{03C1}\u{0313}"),
            '\u{1FE6}' => out.push_str("\u{03C5}\u{0342}"),
            '\u{1FE7}' => out.push_str("\u{03C5}\u{0308}\u{0342}"),
            '\u{1FF2}' => out.push_str("\u{1F7C}\u{03B9}"),
            '\u{1FF3}' | '\u{1FFC}' => out.push_str("\u{03C9}\u{03B9}"),
            '\u{1FF4}' => out.push_str("\u{03CE}\u{03B9}"),
            '\u{1FF6}' => out.push_str("\u{03C9}\u{0342}"),
            '\u{1FF7}' => out.push_str("\u{03C9}\u{0342}\u{03B9}"),
            '\u{AB70}'..='\u{ABBF}' => out.extend(char::from_u32(ch as u32 - 0x97D0)),
            '\u{FB00}' => out.push_str("ff"),
            '\u{FB01}' => out.push_str("fi"),
            '\u{FB02}' => out.push_str("fl"),
            '\u{FB03}' => out.push_str("ffi"),
            '\u{FB04}' => out.push_str("ffl"),
            '\u{FB05}' | '\u{FB06}' => out.push_str("st"),
            '\u{FB13}' => out.push_str("\u{0574}\u{0576}"),
            '\u{FB14}' => out.push_str("\u{0574}\u{0565}"),
            '\u{FB15}' => out.push_str("\u{0574}\u{056B}"),
            '\u{FB16}' => out.push_str("\u{057E}\u{0576}"),
            '\u{FB17}' => out.push_str("\u{0574}\u{056D}"),
            _ => out.extend(ch.to_lowercase()),
        }
    }
    out
}

/// The pure half of ``_looks_unavailable``: any one of the markers in a
/// casefolded page means the station/time is not archived.
pub(crate) fn looks_unavailable_core(text: &str) -> bool {
    let lowered = python_casefold(text);
    UNAVAILABLE_MARKERS
        .iter()
        .any(|marker| lowered.contains(marker))
}

/// Whether ``text`` looks like a UWyo "no data for this station/time" page
/// (``UWyo_Decoder._looks_unavailable``).
///
/// The markers ``"can't get"``, ``"cannot get"``, ``"no data"``,
/// ``"sorry"``, ``"unable to"``, ``"not available"``, and ``"invalid"`` are
/// plain substrings of the casefolded text, so e.g. any page merely
/// containing the word "invalid" counts, exactly as in Python.
#[pyfunction]
fn uwyo_looks_unavailable(text: &str) -> bool {
    looks_unavailable_core(text)
}

// ---------------------------------------------------------------------------
// Header metadata (UWyo_Decoder._parse_metadata)
// ---------------------------------------------------------------------------

/// Slice a string by Python index semantics with negative support
/// (``s[start:end]`` clamped to ``0..len``, empty when the range inverts).
fn python_slice_chars(chars: &[char], start: i64, end: i64) -> &[char] {
    let length = chars.len() as i64;
    let start = if start < 0 {
        (length + start).max(0)
    } else {
        start.min(length)
    };
    let end = if end < 0 {
        (length + end).max(0)
    } else {
        end.min(length)
    };
    if end <= start {
        return &[];
    }
    &chars[start as usize..end as usize]
}

/// Consume one-or-more whitespace characters (the ``\s+`` the ``_strptime``
/// compiler substitutes for every literal space in a format string).
fn skip_whitespace(chars: &[char], pos: &mut usize) -> bool {
    let start = *pos;
    while chars
        .get(*pos)
        .is_some_and(|character| character.is_whitespace())
    {
        *pos += 1;
    }
    *pos > start
}

/// Consume zero-or-more whitespace characters (a regex ``\s*``).
fn skip_whitespace_optional(chars: &[char], pos: &mut usize) {
    while chars
        .get(*pos)
        .is_some_and(|character| character.is_whitespace())
    {
        *pos += 1;
    }
}

/// Match a case-insensitive ASCII literal (``strptime`` compiles its format
/// regex with ``re.IGNORECASE``, so literals like ``UTC`` and ``Z`` match
/// any casing).
fn match_literal_ci(chars: &[char], pos: &mut usize, literal: &str) -> bool {
    for (offset, expected) in literal.chars().enumerate() {
        match chars.get(*pos + offset) {
            Some(&actual) if actual.eq_ignore_ascii_case(&expected) => {}
            _ => return false,
        }
    }
    *pos += literal.chars().count();
    true
}

/// Scan ``%H`` (``2[0-3] | [0-1]\d | \d``), consuming the longest
/// alternative that matches at the cursor, as the regex's first-match
/// backtracking does for these hour-only formats.
fn take_hour(chars: &[char], pos: &mut usize) -> Option<u32> {
    let first = *chars.get(*pos)?;
    let second = chars.get(*pos + 1).copied();
    if first == '2' && second.is_some_and(|digit| matches!(digit, '0'..='3')) {
        *pos += 2;
        return Some(20 + second?.to_digit(10)?);
    }
    if matches!(first, '0' | '1') && second.is_some_and(|digit| digit.is_ascii_digit()) {
        *pos += 2;
        return Some(first.to_digit(10)? * 10 + second?.to_digit(10)?);
    }
    if first.is_ascii_digit() {
        *pos += 1;
        return first.to_digit(10);
    }
    None
}

/// Scan ``%d`` (``3[01] | [12]\d | 0[1-9] | [1-9]``).  The regex's space
/// padded alternative ``" [1-9]"`` never applies here because every ``%d``
/// follows a ``\s+`` separator that already consumed the whitespace run.
fn take_day(chars: &[char], pos: &mut usize) -> Option<u32> {
    let first = *chars.get(*pos)?;
    let second = chars.get(*pos + 1).copied();
    if first == '3' && second.is_some_and(|digit| matches!(digit, '0' | '1')) {
        *pos += 2;
        return Some(30 + second?.to_digit(10)?);
    }
    if matches!(first, '1' | '2') && second.is_some_and(|digit| digit.is_ascii_digit()) {
        *pos += 2;
        return Some(first.to_digit(10)? * 10 + second?.to_digit(10)?);
    }
    if first == '0' && second.is_some_and(|digit| matches!(digit, '1'..='9')) {
        *pos += 2;
        return second?.to_digit(10);
    }
    if matches!(first, '1'..='9') {
        *pos += 1;
        return first.to_digit(10);
    }
    None
}

/// Scan ``%b``: one of the twelve case-insensitive three-letter month
/// abbreviations, returning the month number.
fn take_month(chars: &[char], pos: &mut usize) -> Option<u32> {
    for (index, abbreviation) in MONTH_ABBREVIATIONS.iter().enumerate() {
        let mut offset = 0;
        let mut matched = true;
        for expected in abbreviation.chars() {
            match chars.get(*pos + offset) {
                Some(&actual) if actual.eq_ignore_ascii_case(&expected) => offset += 1,
                _ => {
                    matched = false;
                    break;
                }
            }
        }
        if matched {
            *pos += offset;
            return Some(index as u32 + 1);
        }
    }
    None
}

/// Scan ``%Y``: exactly four ASCII digits.
fn take_year(chars: &[char], pos: &mut usize) -> Option<u32> {
    let mut digits = [0u32; 4];
    for (offset, slot) in digits.iter_mut().enumerate() {
        let character = *chars.get(*pos + offset)?;
        if !character.is_ascii_digit() {
            return None;
        }
        *slot = character.to_digit(10)?;
    }
    *pos += 4;
    Some(digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3])
}

/// Days in one month for a Gregorian year (``_strptime``'s calendar-day
/// check, leap years included).
#[allow(clippy::manual_is_multiple_of)]
fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}

/// Compose the naive ISO stamp ``YYYY-MM-DDTHH:MM:SS`` after the same
/// validations ``datetime`` performs: year 1..9999 and a real calendar day.
fn compose_iso_stamp(hour: u32, month: u32, day: u32, year: u32) -> Option<String> {
    if !(1..=9999).contains(&year) {
        return None;
    }
    if day == 0 || day > days_in_month(year, month) {
        return None;
    }
    Some(format!("{year:04}-{month:02}-{day:02}T{hour:02}:00:00"))
}

/// Parse the ``%H UTC %d %b %Y`` stamp of a modern ``<H1>`` title.
fn parse_h1_stamp(when: &str) -> Option<String> {
    let chars: Vec<char> = when.chars().collect();
    let mut pos = 0;
    let hour = take_hour(&chars, &mut pos)?;
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    if !match_literal_ci(&chars, &mut pos, "UTC") {
        return None;
    }
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    let day = take_day(&chars, &mut pos)?;
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    let month = take_month(&chars, &mut pos)?;
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    let year = take_year(&chars, &mut pos)?;
    if pos != chars.len() {
        // strptime rejects unconverted data remains.
        return None;
    }
    compose_iso_stamp(hour, month, day, year)
}

/// Parse the ``%HZ %d %b %Y`` stamp of a legacy ``<H2>`` title tail.
fn parse_h2_stamp(when: &str) -> Option<String> {
    let chars: Vec<char> = when.chars().collect();
    let mut pos = 0;
    let hour = take_hour(&chars, &mut pos)?;
    if !match_literal_ci(&chars, &mut pos, "Z") {
        return None;
    }
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    let day = take_day(&chars, &mut pos)?;
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    let month = take_month(&chars, &mut pos)?;
    if !skip_whitespace(&chars, &mut pos) {
        return None;
    }
    let year = take_year(&chars, &mut pos)?;
    if pos != chars.len() {
        return None;
    }
    compose_iso_stamp(hour, month, day, year)
}

/// Scan the ``latitude:\s*(-?\d+(?:\.\d+)?)\s*longitude:\s*(-?\d+(?:\.\d+)?)``
/// pattern of the modern coordinate line, at one start offset.
fn match_lat_lon_at(chars: &[char], start: usize) -> Option<(f64, f64)> {
    let mut pos = start;
    if !match_literal_exact(chars, &mut pos, "latitude:") {
        return None;
    }
    skip_whitespace_optional(chars, &mut pos);
    let latitude = take_plain_number(chars, &mut pos)?;
    skip_whitespace_optional(chars, &mut pos);
    if !match_literal_exact(chars, &mut pos, "longitude:") {
        return None;
    }
    skip_whitespace_optional(chars, &mut pos);
    let longitude = take_plain_number(chars, &mut pos)?;
    Some((latitude, longitude))
}

/// Match an exact literal (the coordinate regex is case-sensitive, and the
/// haystack is already lowercased by the caller).
fn match_literal_exact(chars: &[char], pos: &mut usize, literal: &str) -> bool {
    for (offset, expected) in literal.chars().enumerate() {
        match chars.get(*pos + offset) {
            Some(&actual) if actual == expected => {}
            _ => return false,
        }
    }
    *pos += literal.chars().count();
    true
}

/// Scan ``-?\d+(?:\.\d+)?`` and convert the captured text with full-precision
/// decimal parsing (identical to Python's ``float`` on the same text).
fn take_plain_number(chars: &[char], pos: &mut usize) -> Option<f64> {
    let mut cursor = *pos;
    if chars.get(cursor) == Some(&'-') {
        cursor += 1;
    }
    let digits_start = cursor;
    while chars
        .get(cursor)
        .is_some_and(|character| character.is_ascii_digit())
    {
        cursor += 1;
    }
    if cursor == digits_start {
        return None;
    }
    let mut end = cursor;
    if chars.get(cursor) == Some(&'.') {
        let mut frac = cursor + 1;
        let frac_start = frac;
        while chars
            .get(frac)
            .is_some_and(|character| character.is_ascii_digit())
        {
            frac += 1;
        }
        if frac > frac_start {
            end = frac;
        }
    }
    let text: String = chars[*pos..end].iter().collect();
    *pos = end;
    text.parse::<f64>().ok()
}

/// Search a lowercased line for the coordinate pattern, left to right like
/// ``re.search``.
fn search_lat_lon(low: &str) -> Option<(f64, f64)> {
    let chars: Vec<char> = low.chars().collect();
    for start in 0..chars.len() {
        if let Some(found) = match_lat_lon_at(&chars, start) {
            return Some(found);
        }
    }
    None
}

/// One digitpart of a Python numeric literal: a leading digit followed by
/// (``"_"?`` digit)*, so underscores appear only between two digits.
fn take_digitpart(chars: &[char], pos: &mut usize) -> bool {
    if !chars.get(*pos).is_some_and(|c| c.is_ascii_digit()) {
        return false;
    }
    *pos += 1;
    while let Some(&character) = chars.get(*pos) {
        if character.is_ascii_digit() {
            *pos += 1;
        } else if character == '_' {
            if !chars.get(*pos + 1).is_some_and(|c| c.is_ascii_digit()) {
                return false;
            }
            *pos += 2;
        } else {
            break;
        }
    }
    true
}

/// The pure half of Python's ``float()`` on a stripped token: optional sign,
/// ``inf``/``infinity``/``nan`` spellings (case-insensitive), and decimal or
/// scientific numbers with PEP 515 digit-group underscores.  The validated
/// text is then handed to Rust's correctly-rounded parser with underscores
/// removed, which matches Python's conversion bit for bit.
fn python_float(raw: &str) -> Option<f64> {
    let text = raw.trim();
    let mut chars = text.chars().collect::<Vec<char>>();
    if chars.is_empty() {
        return None;
    }
    let negative = match chars[0] {
        '+' => {
            chars.remove(0);
            false
        }
        '-' => {
            chars.remove(0);
            true
        }
        _ => false,
    };
    let rest: String = chars.iter().collect();
    let lowered = rest.to_ascii_lowercase();
    if lowered == "inf" || lowered == "infinity" {
        return Some(if negative {
            f64::NEG_INFINITY
        } else {
            f64::INFINITY
        });
    }
    if lowered == "nan" {
        return Some(f64::NAN);
    }
    let chars = rest.chars().collect::<Vec<char>>();
    let len = chars.len();
    let mut pos = 0;
    let mut seen_digits = false;
    if take_digitpart(&chars, &mut pos) {
        seen_digits = true;
    }
    if chars.get(pos) == Some(&'.') {
        pos += 1;
        if !seen_digits {
            if !take_digitpart(&chars, &mut pos) {
                return None;
            }
            seen_digits = true;
        } else {
            // "41." is valid; a fractional digitpart is optional.
            take_digitpart(&chars, &mut pos);
        }
    }
    if !seen_digits {
        return None;
    }
    if matches!(chars.get(pos), Some('e') | Some('E')) {
        pos += 1;
        if matches!(chars.get(pos), Some('+') | Some('-')) {
            pos += 1;
        }
        if !take_digitpart(&chars, &mut pos) {
            return None;
        }
    }
    if pos != len {
        return None;
    }
    let cleaned: String = rest.chars().filter(|c| *c != '_').collect();
    let signed = if negative {
        format!("-{cleaned}")
    } else {
        cleaned
    };
    signed.parse::<f64>().ok()
}

/// The pure half of ``_parse_metadata``: extract ``(valid, loc, lat, lon)``
/// from either header style, with the ISO stamp standing in for the parsed
/// ``datetime`` (naive, minute/second zero, formatted
/// ``YYYY-MM-DDTHH:MM:SS``).
pub(crate) fn parse_metadata_core(lines: &[&str]) -> (Option<String>, String, f64, f64) {
    let mut valid: Option<String> = None;
    let mut loc = String::new();
    let mut lat = f64::NAN;
    let mut lon = f64::NAN;

    for line in lines {
        let trimmed = line.trim();
        let low = trimmed.to_lowercase();

        // Modern <H1> title with an embedded "HH UTC DD Mon YYYY" timestamp.
        if valid.is_none() && trimmed.starts_with("<H1>") && trimmed.contains(" UTC ") {
            let after = &trimmed[4..];
            let frag = match after.find("</H1>") {
                Some(position) => &after[..position],
                None => after,
            };
            // Python: frag.split(" at ", 1)[1].strip() -- an absent " at "
            // raises IndexError, which the except swallows.
            if let Some(stamp) = frag
                .split_once(" at ")
                .and_then(|(_, when)| parse_h1_stamp(when.trim()))
            {
                valid = Some(stamp);
            }
            continue;
        }

        // Legacy / fixture <H2> title: "LOC   Observations at HHZ DD Mon".
        if trimmed.starts_with("<H2>") && trimmed.ends_with("</H2>") {
            if loc.is_empty() {
                let after = &trimmed[4..];
                let before = match after.find("Observations") {
                    Some(position) => &after[..position],
                    None => after,
                };
                loc = before.trim().to_string();
            }
            if valid.is_none() {
                let chars: Vec<char> = trimmed.chars().collect();
                let window = python_slice_chars(&chars, -20, -5);
                let window: String = window.iter().collect();
                if let Some(stamp) = parse_h2_stamp(&window) {
                    valid = Some(stamp);
                }
            }
            continue;
        }

        // Modern station-name subtitle.
        if loc.is_empty() && trimmed.starts_with("<H3>") && trimmed.ends_with("</H3>") {
            let name = trimmed[4..trimmed.len() - 5].trim();
            if !name.to_lowercase().contains("station information") {
                loc = name.to_string();
            }
            continue;
        }

        // Modern "<I>Latitude: X Longitude: Y</I>" line (any markup prefix;
        // the numbers are pulled out with the coordinate regex).
        if low.contains("latitude") && low.contains("longitude") && lat.is_nan() {
            if let Some((found_lat, found_lon)) = search_lat_lon(&low) {
                lat = found_lat;
                lon = found_lon;
            }
            continue;
        }

        // Legacy "Station latitude:" line.
        if low.contains("station latitude") && lat.is_nan() {
            let token = trimmed.rsplit(':').next().unwrap_or("").trim();
            if let Some(value) = python_float(token) {
                lat = value;
            }
            continue;
        }
    }

    (valid, loc, lat, lon)
}

/// Extract ``(valid_iso, loc, lat, lon)`` from a UWyo response's header
/// lines, mirroring ``UWyo_Decoder._parse_metadata``.
///
/// Handles the modern ``/wsgi/`` header (``<H1>... at HH UTC DD Mon
/// YYYY</H1>``, ``<H3>NAME</H3>``, a ``Latitude: X Longitude: Y`` line) and
/// the legacy fixture header (``<H2>LOC Observations at HHZ DD Mon
/// YYYY</H2>`` plus ``Station latitude:``).  A valid observation time is
/// returned as a naive ISO stamp (``YYYY-MM-DDTHH:MM:SS``); unparsed fields
/// come back as ``None``, an empty name, and NaN coordinates.
#[pyfunction]
fn uwyo_parse_metadata(lines: Vec<String>) -> (Option<String>, String, f64, f64) {
    let borrowed: Vec<&str> = lines.iter().map(String::as_str).collect();
    parse_metadata_core(&borrowed)
}

// ---------------------------------------------------------------------------
// Station-name cleanup (uwyo_catalog._clean_name)
// ---------------------------------------------------------------------------

/// The pure half of ``_clean_name``: collapse every whitespace run to a
/// single space, trim, and drop pandas ``repr`` dumps.
pub(crate) fn clean_station_name_core(raw: Option<&str>) -> String {
    let text = raw.unwrap_or("");
    let collapsed: String = text
        .split(is_python_space)
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let lowered = python_casefold(&collapsed);
    if lowered.contains("dtype:") || lowered.contains("stationid") {
        return String::new();
    }
    collapsed
}

/// Normalize a station name from the ``sounding_json`` payload
/// (``uwyo_catalog._clean_name``).
///
/// Whitespace runs collapse to single spaces and the result is trimmed;
/// ``None`` becomes the empty string; and a name carrying ``dtype:`` or
/// ``stationid`` (case-insensitively) is a corrupt dump and comes back
/// empty so pickers show a blank label instead of garbage.
#[pyfunction]
#[pyo3(signature = (raw = None))]
fn uwyo_clean_station_name(raw: Option<&str>) -> String {
    clean_station_name_core(raw)
}

// ---------------------------------------------------------------------------
// Catalogue search (uwyo_catalog.search_stations)
// ---------------------------------------------------------------------------

/// The pure half of ``search_stations`` over an ``(id, name)`` catalogue:
/// an exact id wins alone, otherwise caseless substring matches sorted by
/// id and sliced to ``limit``; the result holds the matched indices.
pub(crate) fn search_stations_core(
    stations: &[(String, String)],
    query: &str,
    limit: Option<usize>,
) -> Vec<usize> {
    let trimmed = python_strip(query);
    if trimmed.is_empty() {
        return vec![];
    }
    // get_station: the first record whose id equals the query, verbatim.
    if let Some(index) = stations
        .iter()
        .position(|(identifier, _name)| identifier == trimmed)
    {
        return vec![index];
    }
    let lowered = python_casefold(trimmed);
    let mut matches: Vec<(usize, &str)> = stations
        .iter()
        .enumerate()
        .filter(|(_index, (identifier, name))| {
            python_casefold(identifier).contains(&lowered)
                || python_casefold(name).contains(&lowered)
        })
        .map(|(index, (identifier, _name))| (index, identifier.as_str()))
        .collect();
    // Python's matches.sort(key=id) is stable; so is sort_by.
    matches.sort_by(|left, right| left.1.cmp(right.1));
    let mut indices: Vec<usize> = matches.into_iter().map(|(index, _id)| index).collect();
    if let Some(limit) = limit {
        indices.truncate(limit);
    }
    indices
}

/// Search an ``(id, name)`` station list by id or name substring
/// (``uwyo_catalog.search_stations``, returning matched indices).
///
/// A whitespace-only query matches nothing; an exact id match is returned
/// alone (first occurrence); otherwise every station whose casefolded id or
/// name contains the casefolded query is returned, ordered by id ascending
/// (stable on ties) and sliced to ``limit`` when one is given.
#[pyfunction]
#[pyo3(signature = (stations, query, limit = None))]
fn uwyo_search_stations(
    stations: Vec<(String, String)>,
    query: &str,
    limit: Option<usize>,
) -> Vec<usize> {
    search_stations_core(&stations, query, limit)
}

// ---------------------------------------------------------------------------
// Station resolution (UWyo_Decoder.resolve_station)
// ---------------------------------------------------------------------------

/// The pure half of ``resolve_station``'s ladder, returning the matched
/// index (or the reason it cannot be resolved) instead of raising:
/// ``(Some(index), "ok", [])`` on a unique resolution, and
/// ``(None, status, candidates)`` with one of ``empty``, ``multiple_name``,
/// ``not_found``, or ``multiple`` otherwise.
pub(crate) fn resolve_station_core(
    stations: &[(String, String)],
    query: Option<&str>,
) -> (Option<usize>, String, Vec<String>) {
    let Some(query) = query else {
        return (None, "empty".to_string(), vec![]);
    };
    let trimmed = python_strip(query);
    if trimmed.is_empty() {
        return (None, "empty".to_string(), vec![]);
    }

    // 1. Exact station-id match takes precedence and is unambiguous.
    if let Some(index) = stations
        .iter()
        .position(|(identifier, _name)| identifier == trimmed)
    {
        return (Some(index), "ok".to_string(), vec![]);
    }

    let lowered = python_casefold(trimmed);

    // 2. Exact (case-insensitive) name match.
    let exact_name: Vec<String> = stations
        .iter()
        .filter(|(_id, name)| python_casefold(name) == lowered)
        .map(|(identifier, _name)| identifier.clone())
        .collect();
    if exact_name.len() > 1 {
        let mut sorted = exact_name;
        sorted.sort();
        return (None, "multiple_name".to_string(), sorted);
    }
    if exact_name.len() == 1 {
        let index = stations
            .iter()
            .position(|(identifier, _name)| *identifier == exact_name[0])
            .unwrap_or(0);
        return (Some(index), "ok".to_string(), vec![]);
    }

    // 3. Substring match against ids and names.
    let matches: Vec<String> = stations
        .iter()
        .filter(|(identifier, name)| {
            python_casefold(identifier).contains(&lowered)
                || python_casefold(name).contains(&lowered)
        })
        .map(|(identifier, _name)| identifier.clone())
        .collect();
    match matches.len() {
        0 => (None, "not_found".to_string(), vec![]),
        1 => {
            let index = stations
                .iter()
                .position(|(identifier, _name)| *identifier == matches[0])
                .unwrap_or(0);
            (Some(index), "ok".to_string(), vec![])
        }
        _ => {
            let mut sorted = matches;
            sorted.sort();
            (None, "multiple".to_string(), sorted)
        }
    }
}

/// Resolve a station query to exactly one catalogue index, mirroring
/// ``UWyo_Decoder.resolve_station`` without raising.
///
/// Returns ``(index, status, candidates)``: a ``None``/whitespace query is
/// ``empty``; an exact id match wins outright; an exact casefolded name
/// match resolves when unique and reports ``multiple_name`` with the sorted
/// ids otherwise; a casefolded substring match over ids and names resolves
/// when unique and reports ``multiple`` (sorted ids) or ``not_found``
/// otherwise.  The candidates are sorted exactly like the Python error
/// messages print them.
#[pyfunction]
#[pyo3(signature = (stations, query = None))]
fn uwyo_resolve_station_index(
    stations: Vec<(String, String)>,
    query: Option<&str>,
) -> (Option<usize>, String, Vec<String>) {
    resolve_station_core(&stations, query)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(uwyo_pressure_spaced_indices, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_subsample_indices, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_looks_unavailable, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_parse_metadata, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_clean_station_name, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_search_stations, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_resolve_station_index, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pressure_spaced_indices_keep_a_clean_ladder() {
        let pres = [1000.0, 900.0, 800.0, 700.0];
        assert_eq!(pressure_spaced_indices_core(&pres, 2.0), vec![0, 1, 2, 3]);
    }

    #[test]
    fn pressure_spaced_indices_drop_close_levels() {
        // Skipped levels never update the reference pressure, so 998 is
        // still 2.0 hPa below the kept 1000 and survives the >= min_dp cut.
        let pres = [1000.0, 999.0, 998.0, 900.0, 899.5, 700.0];
        assert_eq!(pressure_spaced_indices_core(&pres, 2.0), vec![0, 2, 3, 5]);
    }

    #[test]
    fn pressure_spaced_indices_skip_non_finite_without_updating_last() {
        let pres = [1000.0, f64::NAN, f64::INFINITY, 900.0, 800.0];
        assert_eq!(pressure_spaced_indices_core(&pres, 2.0), vec![0, 3, 4]);
    }

    #[test]
    fn pressure_spaced_indices_always_keep_first_and_topmost() {
        // No level is 10 hPa below the surface value, but the topmost finite
        // level still survives (it keeps pressure strictly decreasing).
        let pres = [1000.0, 998.0, 996.0, 997.0];
        assert_eq!(pressure_spaced_indices_core(&pres, 10.0), vec![0, 3]);
        // The topmost level is not appended when that would repeat pressure.
        let flat = [1000.0, 1000.0];
        assert_eq!(pressure_spaced_indices_core(&flat, 2.0), vec![0]);
    }

    #[test]
    fn pressure_spaced_indices_all_non_finite_is_empty() {
        let pres = [f64::NAN, f64::INFINITY, f64::NAN];
        assert!(pressure_spaced_indices_core(&pres, 2.0).is_empty());
    }

    #[test]
    fn pressure_spaced_indices_nan_min_dp_behaves_like_numpy() {
        // (last_p - p) >= NaN is false in Python too: only the first and the
        // strictly-lower topmost level survive.
        let pres = [1000.0, 500.0, 400.0];
        assert_eq!(pressure_spaced_indices_core(&pres, f64::NAN), vec![0, 2]);
    }

    #[test]
    fn subsample_indices_identity_cases() {
        let indices = vec![10, 11, 12];
        assert_eq!(subsample_indices_core(&indices, 10), indices);
        assert_eq!(subsample_indices_core(&indices, 3), indices);
        assert_eq!(subsample_indices_core(&indices, 1), indices);
        assert_eq!(subsample_indices_core(&indices, 0), indices);
        assert!(subsample_indices_core(&[], 5).is_empty());
    }

    #[test]
    fn subsample_indices_match_the_numpy_rounding() {
        // Verified against np.linspace(0, n - 1, max_count) + round().
        let indices: Vec<usize> = (100..110).collect();
        assert_eq!(
            subsample_indices_core(&indices, 7),
            vec![100, 102, 103, 104, 106, 108, 109]
        );
        let nine: Vec<usize> = (0..9).collect();
        assert_eq!(subsample_indices_core(&nine, 7), vec![0, 1, 3, 4, 5, 7, 8]);
    }

    #[test]
    fn subsample_indices_round_ties_to_even() {
        // linspace(0, 5, 3) == [0, 2.5, 5]; round(2.5) == 2.
        let six: Vec<usize> = (10..16).collect();
        assert_eq!(subsample_indices_core(&six, 3), vec![10, 12, 15]);
        // linspace(0, 7, 3) == [0, 3.5, 7]; round(3.5) == 4.
        let eight: Vec<usize> = (20..28).collect();
        assert_eq!(subsample_indices_core(&eight, 3), vec![20, 24, 27]);
    }

    #[test]
    fn subsample_indices_keep_endpoints_under_pressure() {
        let indices: Vec<usize> = (0..400).collect();
        let chosen = subsample_indices_core(&indices, 300);
        assert_eq!(chosen.first(), Some(&0));
        assert_eq!(chosen.last(), Some(&399));
        let mut sorted = chosen.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(chosen, sorted, "chosen stays sorted and deduplicated");
        assert_eq!(chosen.len(), 300);
        assert_eq!(&chosen[..5], &[0, 1, 3, 4, 5]);
        assert_eq!(&chosen[295..], &[394, 395, 396, 398, 399]);
    }

    #[test]
    fn unavailable_markers_are_case_insensitive_substrings() {
        let page = "<HTML>\n<TITLE>University of Wyoming - Radiosonde Data</TITLE>\n\
                    <BODY>\n<H2>Sorry, the server can't get the data you requested.</H2>\n\
                    No data available for this station and time.\n\
                    </BODY></HTML>\n";
        assert!(looks_unavailable_core(page));
        assert!(looks_unavailable_core("INVALID station"));
        assert!(
            looks_unavailable_core("nothing invalid here"),
            "the word alone trips the marker, like Python's substring test"
        );
        assert!(looks_unavailable_core("we are UNABLE TO help"));
        assert!(!looks_unavailable_core("a perfectly good sounding"));
        assert!(!looks_unavailable_core(""));
    }

    #[test]
    fn parse_metadata_modern_wsgi_header() {
        let lines = [
            "<H1>Observations for Station 72558 at 00 UTC 16 Jun 2014</H1>",
            "<H3>OAX Omaha/Valley, NE</H3>",
            "<I>Latitude: 41.32 Longitude: -96.37</I>",
        ];
        let (valid, loc, lat, lon) = parse_metadata_core(&lines);
        assert_eq!(valid.as_deref(), Some("2014-06-16T00:00:00"));
        assert_eq!(loc, "OAX Omaha/Valley, NE");
        assert_eq!(lat, 41.32);
        assert_eq!(lon, -96.37);
    }

    #[test]
    fn parse_metadata_legacy_fixture_header() {
        let lines = [
            "<H2>OAX    Observations at 00Z 16 Jun 2014</H2>",
            "</PRE><H3>Station information and sounding indices</H3>",
            "                         Station latitude: 41.32",
        ];
        let (valid, loc, lat, lon) = parse_metadata_core(&lines);
        assert_eq!(valid.as_deref(), Some("2014-06-16T00:00:00"));
        assert_eq!(loc, "OAX");
        assert_eq!(lat, 41.32);
        assert!(lon.is_nan());
    }

    #[test]
    fn parse_metadata_stamps_follow_strptime() {
        // Leap day valid in a leap year only.
        let lines = ["<H2>ABCD Observations at 00Z 29 Feb 2016</H2>"];
        let (valid, _loc, _lat, _lon) = parse_metadata_core(&lines);
        assert_eq!(valid.as_deref(), Some("2016-02-29T00:00:00"));
        let lines = ["<H2>ABCD Observations at 00Z 29 Feb 2015</H2>"];
        assert!(parse_metadata_core(&lines).0.is_none());
        // Case-insensitive month names and the strptime literals: %b and
        // "UTC" match any casing once the branch is entered, but the branch
        // gate itself (" UTC " in the raw line) is case-sensitive.
        let lines = ["<H1>Observations for Station 72558 at 12 UTC 16 jun 2014</H1>"];
        let (valid, _loc, _lat, _lon) = parse_metadata_core(&lines);
        assert_eq!(valid.as_deref(), Some("2014-06-16T12:00:00"));
        let lines = ["<H1>Observations for Station 72558 at 12 utc 16 jun 2014</H1>"];
        assert!(
            parse_metadata_core(&lines).0.is_none(),
            "a lowercase 'utc' never passes the ' UTC ' gate"
        );
        // Single-digit hour and day, doubled spaces.
        let lines = ["<H1>Observations for Station 72558 at 0 UTC  6 Jun 2014</H1>"];
        let (valid, _loc, _lat, _lon) = parse_metadata_core(&lines);
        assert_eq!(valid.as_deref(), Some("2014-06-06T00:00:00"));
    }

    #[test]
    fn parse_metadata_rejects_invalid_stamps_like_strptime() {
        let cases = [
            "<H2>ABCD Observations at 00Z 30 Feb 2014</H2>", // Feb 30
            "<H2>Some Station Name Observations at 24Z 16 Jun 2014</H2>", // hour 24
            "<H2>Some Station Name Observations at 00Z 16 June 2014</H2>", // full month
            "<H2>Some Station Name Observations at 00Z 16 Jun 004</H2>", // 3-digit year
            "<H2>Some Station Name Observations at 00Z 16 Jun 2014x</H2>", // trailing junk
        ];
        for case in cases {
            let (valid, _loc, _lat, _lon) = parse_metadata_core(&[case]);
            assert!(valid.is_none(), "{case} must not parse");
        }
    }

    #[test]
    fn parse_metadata_window_slices_from_the_line_tail() {
        // A short stamp window (fewer than 20 characters before </H2>) still
        // slices exactly like Python's s[-20:-5].
        let lines = ["<H2>AB</H2>"];
        let (valid, loc, _lat, _lon) = parse_metadata_core(&lines);
        assert!(valid.is_none());
        assert_eq!(
            loc, "AB</H2>",
            "an H2 without 'Observations' keeps the closing tag in loc, as in Python"
        );
        // The H2 branch still yields loc when the stamp fails -- and with no
        // "Observations" marker the whole s[4:] tail is the name, closing
        // tag included, exactly like Python's split()[0].
        let lines = ["<H2>Some unrelated page</H2>"];
        let (valid, loc, _lat, _lon) = parse_metadata_core(&lines);
        assert!(valid.is_none());
        assert_eq!(loc, "Some unrelated page</H2>");
    }

    #[test]
    fn parse_metadata_h1_failures_leave_fields_unset() {
        // No " at " in the fragment: IndexError is swallowed, valid stays None.
        let lines = ["<H1>Observations for Station 72558, 00 UTC 16 Jun 2014</H1>"];
        let (valid, _loc, _lat, _lon) = parse_metadata_core(&lines);
        assert!(valid.is_none());
        // A second H1 cannot overwrite a parsed valid stamp.
        let lines = [
            "<H1>Observations for Station 72558 at 00 UTC 16 Jun 2014</H1>",
            "<H1>Observations for Station 72558 at 12 UTC 17 Jul 2015</H1>",
        ];
        let (valid, _loc, _lat, _lon) = parse_metadata_core(&lines);
        assert_eq!(valid.as_deref(), Some("2014-06-16T00:00:00"));
    }

    #[test]
    fn parse_metadata_coordinate_line_needs_both_labels() {
        let lines = ["<BR/>Latitude: 41.32 Longitude: -96.37"];
        let (valid, _loc, lat, lon) = parse_metadata_core(&lines);
        assert!(valid.is_none());
        assert_eq!(lat, 41.32);
        assert_eq!(lon, -96.37);
        // Without "longitude" the modern branch never fires.
        let lines = ["<I>Latitude: 41.32</I>"];
        let (_valid, _loc, lat, lon) = parse_metadata_core(&lines);
        assert!(lat.is_nan());
        assert!(lon.is_nan());
        // The regex rejects a trailing dot on the latitude.
        let lines = ["Latitude: 41. longitude: -96.37"];
        let (_valid, _loc, lat, lon) = parse_metadata_core(&lines);
        assert!(lat.is_nan());
        assert!(lon.is_nan());
        // The first latitude: with a full match wins, as re.search does.
        let lines = ["latitude: 1.0 x latitude: 2.0 longitude: 3.0"];
        let (_valid, _loc, lat, lon) = parse_metadata_core(&lines);
        assert_eq!(lat, 2.0);
        assert_eq!(lon, 3.0);
    }

    #[test]
    fn parse_metadata_station_latitude_line_parses_like_float() {
        let lines = ["Station latitude: 41.32"];
        let (_valid, _loc, lat, _lon) = parse_metadata_core(&lines);
        assert_eq!(lat, 41.32);
        // A second coordinate line cannot overwrite the first (isnan guard).
        let lines = ["Station latitude: 41.32", "Station latitude: -12.5"];
        let (_valid, _loc, lat, _lon) = parse_metadata_core(&lines);
        assert_eq!(lat, 41.32);
        // float() spellings the legacy line may carry.
        for (token, expected) in [
            ("+41.5", 41.5),
            ("-.5", -0.5),
            ("41.", 41.0),
            ("1e2", 100.0),
            ("1_0.5", 10.5),
            ("-INF", f64::NEG_INFINITY),
        ] {
            assert_eq!(python_float(token), Some(expected), "{token}");
        }
        for token in ["0x10", "", "  ", "4 2", "1_", "1__0", "1_e2", "nan1"] {
            assert!(python_float(token).is_none(), "{token} must not parse");
        }
        assert!(python_float("NAN").is_some_and(f64::is_nan));
    }

    #[test]
    fn clean_station_name_collapses_whitespace_and_drops_dumps() {
        assert_eq!(clean_station_name_core(None), "");
        assert_eq!(
            clean_station_name_core(Some("  OAX   Omaha/Valley,\tNE ")),
            "OAX Omaha/Valley, NE"
        );
        assert_eq!(
            clean_station_name_core(Some("Stationid\n73111    Kapuskasing Dtype: Str")),
            ""
        );
        assert_eq!(clean_station_name_core(Some("DTYPE: str")), "");
        assert_eq!(clean_station_name_core(Some("")), "");
        assert_eq!(
            clean_station_name_core(Some("\u{00a0}\u{00a0}Kapuskasing\u{00a0}\u{00a0}")),
            "Kapuskasing",
            "unicode whitespace collapses like \\s+"
        );
    }

    #[test]
    fn the_seed_catalogue_resolves_like_the_decoder() {
        let stations: Vec<(String, String)> = [
            ("72558", "OAX Omaha/Valley, NE"),
            ("72562", "LBF North Platte, NE"),
            ("72249", "FWD Fort Worth, TX"),
            ("72469", "DNR Denver, CO"),
            ("72520", "PIT Pittsburgh, PA"),
            ("72403", "IAD Washington Dulles, VA"),
            ("72451", "DDC Dodge City, KS"),
            ("72357", "OUN Norman, OK"),
            ("72572", "SLC Salt Lake City, UT"),
            ("74560", "ILX Lincoln, IL"),
            ("72694", "SLE Salem, OR"),
            ("72797", "UIL Quillayute, WA"),
            ("72776", "TFX Great Falls, MT"),
            ("72214", "TLH Tallahassee, FL"),
        ]
        .iter()
        .map(|(id, name)| (id.to_string(), name.to_string()))
        .collect();

        // Exact id.
        let (index, status, candidates) = resolve_station_core(&stations, Some("72558"));
        assert_eq!(
            (index, status.as_str(), candidates.as_slice()),
            (Some(0), "ok", &[] as &[String])
        );
        // Unique name substring and call sign.
        let (index, status, _candidates) = resolve_station_core(&stations, Some("norman"));
        assert_eq!((index, status.as_str()), (Some(7), "ok"));
        let (index, status, _candidates) = resolve_station_core(&stations, Some("OUN"));
        assert_eq!((index, status.as_str()), (Some(7), "ok"));
        // Ambiguous substring names the candidates, sorted.
        let (index, status, candidates) = resolve_station_core(&stations, Some("NE"));
        assert_eq!(index, None);
        assert_eq!(status, "multiple");
        assert_eq!(candidates, vec!["72558".to_string(), "72562".to_string()]);
        // Empty and missing queries.
        for query in [None, Some(""), Some("   ")] {
            let (index, status, candidates) = resolve_station_core(&stations, query);
            assert_eq!(
                (index, status.as_str(), candidates.as_slice()),
                (None, "empty", &[] as &[String])
            );
        }
        // No match at all.
        let (index, status, candidates) = resolve_station_core(&stations, Some("NoSuchPlace12345"));
        assert_eq!(
            (index, status.as_str(), candidates.as_slice()),
            (None, "not_found", &[] as &[String])
        );
        // Exact caseless name resolution.
        let (index, status, _candidates) =
            resolve_station_core(&stations, Some("oax omaha/valley, ne"));
        assert_eq!((index, status.as_str()), (Some(0), "ok"));
    }

    #[test]
    fn resolve_reports_multiple_name_for_duplicated_names() {
        let stations = vec![
            ("00001".to_string(), "ALPHA Springfield".to_string()),
            ("00002".to_string(), "BETA Springfield".to_string()),
            ("00003".to_string(), "Twin Spot".to_string()),
            ("00004".to_string(), "Twin Spot".to_string()),
        ];
        // Substring ambiguity between the Springfields.
        let (index, status, candidates) = resolve_station_core(&stations, Some("Springfield"));
        assert_eq!(index, None);
        assert_eq!(status, "multiple");
        assert_eq!(candidates, vec!["00001".to_string(), "00002".to_string()]);
        // Two stations sharing one exact name.
        let (index, status, candidates) = resolve_station_core(&stations, Some("twin spot"));
        assert_eq!(index, None);
        assert_eq!(status, "multiple_name");
        assert_eq!(candidates, vec!["00003".to_string(), "00004".to_string()]);
        // The unique ALPHA token still resolves.
        let (index, status, _candidates) = resolve_station_core(&stations, Some("ALPHA"));
        assert_eq!((index, status.as_str()), (Some(0), "ok"));
    }

    #[test]
    fn search_returns_exact_id_first_and_sorted_substrings() {
        let stations: Vec<(String, String)> = [
            ("9", "Zulu North Field"),
            ("72558", "OAX Omaha/Valley, NE"),
            ("5", "Alpha North Site"),
        ]
        .iter()
        .map(|(id, name)| (id.to_string(), name.to_string()))
        .collect();

        // Whitespace-only query matches nothing.
        assert!(search_stations_core(&stations, "   ", Some(10)).is_empty());
        // Exact id wins alone.
        assert_eq!(search_stations_core(&stations, "72558", None), vec![1]);
        // Substring matches are sorted by id ascending.
        assert_eq!(search_stations_core(&stations, "NORTH", None), vec![2, 0]);
        // ...and sliced to the limit after sorting.
        assert_eq!(search_stations_core(&stations, "north", Some(1)), vec![2]);
        // Substrings match ids too.
        assert_eq!(search_stations_core(&stations, "25", None), vec![1]);
        assert!(search_stations_core(&stations, "nowhere", None).is_empty());
    }

    #[test]
    fn search_and_casefold_match_python_unicode_and_whitespace() {
        let stations: Vec<(String, String)> = [
            ("04360", "Tasiilaq / Ammassalik"),
            ("01004", "Ny-Alesund II / Svalbard"),
            ("01400", "Ekofisk"),
            ("72357", "OUN Norman, OK"),
        ]
        .iter()
        .map(|(id, name)| (id.to_string(), name.to_string()))
        .collect();

        assert_eq!(search_stations_core(&stations, "\u{00DF}", None), vec![0]);
        assert_eq!(
            search_stations_core(&stations, "\u{017F}", None),
            vec![1, 2, 0]
        );
        assert_eq!(search_stations_core(&stations, "\u{FB01}", None), vec![2]);
        assert_eq!(
            search_stations_core(&stations, "\u{001C}72357", None),
            vec![3]
        );
        assert_eq!(
            search_stations_core(&stations, "\u{00A0}72357", None),
            vec![3]
        );
        assert!(search_stations_core(&stations, "\u{FEFF}72357", None).is_empty());
        assert_eq!(
            python_casefold("STRASSE \u{00DF} \u{FB03}"),
            "strasse ss ffi"
        );
    }
}

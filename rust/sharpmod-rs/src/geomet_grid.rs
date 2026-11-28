//! Native ECCC GeoMet rotated-grid, request, and capabilities rules
//! (milestone M35).
//!
//! Rust ports of the pure geometry and bookkeeping half of
//! ``sharpmod.providers.eccc_geomet``:
//!
//! * ``RotatedGridDomain.to_rotated`` ([`geomet_to_rotated`]) and
//!   ``RotatedGridDomain.to_geographic`` ([`geomet_to_geographic`]) -- the
//!   spherical rotation between geographic coordinates and an ECCC
//!   RLatLon grid, including the ``rem_euclid`` pole-longitude wrap into
//!   ``[-180, 180)``;
//! * ``RotatedGridDomain.contains`` ([`geomet_rotated_contains`]) and
//!   ``RotatedGridDomain.outline`` ([`geomet_rotated_outline`]) -- the
//!   RDPS native-grid acceptance test and the sampled geographic
//!   perimeter exposed to the map;
//! * the map-envelope half of ``point_in_domain``
//!   ([`geomet_point_in_bounds`]) -- antimeridian-aware bounds checks
//!   over a provider's geographic extent;
//! * ``_floor_cycle`` ([`geomet_floor_cycle_epoch`]) -- snapping one UTC
//!   instant to the latest advertised model cycle, expressed as epoch
//!   seconds;
//! * the BBOX value of ``build_feature_info_params``
//!   ([`geomet_feature_info_bbox`]) -- a WMS 1.3 latitude-first 3x3
//!   pixel request box with clamped corners;
//! * ``_dimension_defaults`` ([`geomet_parse_dimension_defaults`]) -- a
//!   strict XML well-formedness scan of the layer-specific
//!   ``GetCapabilities`` document that extracts every ``Dimension``
//!   element's ``name`` and ``default`` attributes.
//!
//! Error messages match the Python sources verbatim where Python raises
//! one.  Malformed capabilities XML reports the exact wording of the
//! Python ``RetrievalError`` as a ``ValueError``, and the cycle snap
//! reports the empty-cycle and hour-range failures as ``ValueError``s,
//! which is what the Python ``datetime.replace`` call raises in their
//! place.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use time::{Duration, OffsetDateTime, PrimitiveDateTime, Time};

// ---------------------------------------------------------------------------
// Rotated-grid coordinate conversion (RotatedGridDomain)
// ---------------------------------------------------------------------------

/// The pure half of ``RotatedGridDomain.to_rotated``: convert one
/// geographic coordinate to this grid's rotated degrees.
///
/// The pole-relative longitude uses Python's modulo sign (``rem_euclid``),
/// and the ``asin`` argument is clamped to its mathematical domain so a
/// round-tripped coordinate cannot produce NaN.
pub(crate) fn to_rotated_core(
    north_pole_lat: f64,
    north_pole_lon: f64,
    lat: f64,
    lon: f64,
) -> (f64, f64) {
    let phi = lat.to_radians();
    let delta_lon = (((lon - north_pole_lon + 180.0).rem_euclid(360.0)) - 180.0).to_radians();
    let pole_lat = north_pole_lat.to_radians();
    let rotated_lat = (phi.sin() * pole_lat.sin() + phi.cos() * pole_lat.cos() * delta_lon.cos())
        .clamp(-1.0, 1.0)
        .asin();
    let rotated_lon = -(phi.cos() * delta_lon.sin())
        .atan2(phi.sin() * pole_lat.cos() - phi.cos() * pole_lat.sin() * delta_lon.cos());
    (rotated_lat.to_degrees(), rotated_lon.to_degrees())
}

/// Convert one geographic coordinate to an ECCC grid's rotated degrees.
///
/// The grid is identified by its rotated north pole, exactly as
/// ``RotatedGridDomain`` is.  The result is
/// ``(rotated_lat, rotated_lon)`` in degrees, matching
/// ``RotatedGridDomain.to_rotated`` bit for bit for every in-domain
/// input.
#[pyfunction]
fn geomet_to_rotated(north_pole_lat: f64, north_pole_lon: f64, lat: f64, lon: f64) -> (f64, f64) {
    to_rotated_core(north_pole_lat, north_pole_lon, lat, lon)
}

/// The pure half of ``RotatedGridDomain.to_geographic``: convert one
/// rotated-grid coordinate back to ``(lat, lon)`` degrees.
pub(crate) fn to_geographic_core(
    north_pole_lat: f64,
    north_pole_lon: f64,
    rotated_lat: f64,
    rotated_lon: f64,
) -> (f64, f64) {
    let phi_r = rotated_lat.to_radians();
    let lon_r = rotated_lon.to_radians();
    let pole_lat = north_pole_lat.to_radians();
    let lat = (phi_r.sin() * pole_lat.sin() + phi_r.cos() * pole_lat.cos() * lon_r.cos())
        .clamp(-1.0, 1.0)
        .asin();
    let delta_lon = (-phi_r.cos() * lon_r.sin())
        .atan2(phi_r.sin() * pole_lat.cos() - phi_r.cos() * pole_lat.sin() * lon_r.cos());
    let lon = (north_pole_lon + delta_lon.to_degrees() + 180.0).rem_euclid(360.0) - 180.0;
    (lat.to_degrees(), lon)
}

/// Convert one rotated-grid coordinate to geographic degrees.
///
/// The result is ``(lat, lon)`` with the longitude wrapped into
/// ``[-180, 180)``, exactly as ``RotatedGridDomain.to_geographic``
/// returns it.
#[pyfunction]
fn geomet_to_geographic(
    north_pole_lat: f64,
    north_pole_lon: f64,
    rotated_lat: f64,
    rotated_lon: f64,
) -> (f64, f64) {
    to_geographic_core(north_pole_lat, north_pole_lon, rotated_lat, rotated_lon)
}

// ---------------------------------------------------------------------------
// Rotated-domain membership (RotatedGridDomain.contains / .outline)
// ---------------------------------------------------------------------------

/// The pure half of ``RotatedGridDomain.contains``: finite, valid
/// latitude converts to the grid's rotated degrees, which must then lie
/// inside the rotated rectangle.
#[allow(clippy::too_many_arguments)]
pub(crate) fn rotated_contains_core(
    north_pole_lat: f64,
    north_pole_lon: f64,
    rlon_min: f64,
    rlon_max: f64,
    rlat_min: f64,
    rlat_max: f64,
    lat: f64,
    lon: f64,
) -> bool {
    if !lat.is_finite() || !lon.is_finite() {
        return false;
    }
    if !(-90.0..=90.0).contains(&lat) {
        return false;
    }
    let (rotated_lat, rotated_lon) = to_rotated_core(north_pole_lat, north_pole_lon, lat, lon);
    (rlat_min..=rlat_max).contains(&rotated_lat) && (rlon_min..=rlon_max).contains(&rotated_lon)
}

/// Report whether a geographic point lies on a rotated ECCC model grid.
///
/// The grid is the rotated pole plus the rotated rectangle
/// ``(rlon_min, rlon_max, rlat_min, rlat_max)``; the point is tested in
/// the grid's native rotated coordinates, because a rotated rectangle
/// wraps the pole and the antimeridian and no geographic envelope can
/// describe it.  Non-finite or out-of-range coordinates are simply not
/// on the grid.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn geomet_rotated_contains(
    north_pole_lat: f64,
    north_pole_lon: f64,
    rlon_min: f64,
    rlon_max: f64,
    rlat_min: f64,
    rlat_max: f64,
    lat: f64,
    lon: f64,
) -> bool {
    rotated_contains_core(
        north_pole_lat,
        north_pole_lon,
        rlon_min,
        rlon_max,
        rlat_min,
        rlat_max,
        lat,
        lon,
    )
}

/// The pure half of ``RotatedGridDomain.outline``: sample the rotated
/// rectangle's perimeter and return it as geographic ``(lon, lat)``
/// points.
///
/// Each edge contributes ``max(2, samples_per_edge)`` samples starting
/// at its first corner, and the first point repeats at the end so the
/// perimeter closes.  The returned points are geographic longitudes and
/// latitudes, because that is the coordinate order the map consumes.
pub(crate) fn rotated_outline_core(
    north_pole_lat: f64,
    north_pole_lon: f64,
    rlon_min: f64,
    rlon_max: f64,
    rlat_min: f64,
    rlat_max: f64,
    samples_per_edge: i64,
) -> Vec<(f64, f64)> {
    let count = samples_per_edge.max(2) as usize;

    fn values(start: f64, stop: f64, count: usize) -> impl Iterator<Item = f64> {
        (0..count).map(move |index| start + (stop - start) * (index as f64) / (count as f64))
    }

    let mut rotated: Vec<(f64, f64)> = Vec::new();
    for lon in values(rlon_min, rlon_max, count) {
        rotated.push((rlat_min, lon));
    }
    for lat in values(rlat_min, rlat_max, count) {
        rotated.push((lat, rlon_max));
    }
    for lon in values(rlon_max, rlon_min, count) {
        rotated.push((rlat_max, lon));
    }
    for lat in values(rlat_max, rlat_min, count) {
        rotated.push((lat, rlon_min));
    }
    rotated.push(rotated[0]);
    rotated
        .into_iter()
        .map(|(lat, lon)| {
            let (geo_lat, geo_lon) = to_geographic_core(north_pole_lat, north_pole_lon, lat, lon);
            (geo_lon, geo_lat)
        })
        .collect()
}

/// Return a rotated ECCC grid's geographic perimeter as ``(lon, lat)``
/// points.
///
/// The rectangle is sampled on each edge at
/// ``max(2, samples_per_edge)`` points, converted to geographic
/// coordinates, and closed by repeating the first point, matching
/// ``RotatedGridDomain.outline``'s contract for the map's domain
/// outline.
#[pyfunction]
fn geomet_rotated_outline(
    north_pole_lat: f64,
    north_pole_lon: f64,
    rlon_min: f64,
    rlon_max: f64,
    rlat_min: f64,
    rlat_max: f64,
    samples_per_edge: i64,
) -> Vec<(f64, f64)> {
    rotated_outline_core(
        north_pole_lat,
        north_pole_lon,
        rlon_min,
        rlon_max,
        rlat_min,
        rlat_max,
        samples_per_edge,
    )
}

// ---------------------------------------------------------------------------
// Geographic envelope membership (point_in_domain)
// ---------------------------------------------------------------------------

/// The pure half of ``point_in_domain``'s envelope branch: wrap the
/// longitude into ``[-180, 180)`` and apply one provider's geographic
/// bounds, honouring an antimeridian-spanning ``lon0 > lon1`` extent.
pub(crate) fn point_in_bounds_core(
    lat: f64,
    lon: f64,
    lon0: f64,
    lon1: f64,
    lat0: f64,
    lat1: f64,
) -> bool {
    let lon = (lon + 180.0).rem_euclid(360.0) - 180.0;
    if !lat.is_finite() || !lon.is_finite() {
        return false;
    }
    let longitude_ok = if lon0 <= lon1 {
        (lon0..=lon1).contains(&lon)
    } else {
        lon >= lon0 || lon <= lon1
    };
    (lat0..=lat1).contains(&lat) && longitude_ok
}

/// Report whether a point lies inside one provider's geographic bounds.
///
/// The bounds are ``(lon0, lon1, lat0, lat1)``; a bound pair with
/// ``lon0 > lon1`` spans the antimeridian, and the query longitude is
/// first wrapped into ``[-180, 180)``.  Non-finite coordinates are never
/// in bounds.  This is the non-rotated branch of
/// ``point_in_domain``; rotated-grid providers are tested with
/// [`geomet_rotated_contains`].
#[pyfunction]
fn geomet_point_in_bounds(lat: f64, lon: f64, lon0: f64, lon1: f64, lat0: f64, lat1: f64) -> bool {
    point_in_bounds_core(lat, lon, lon0, lon1, lat0, lat1)
}

// ---------------------------------------------------------------------------
// Model cycle snapping (_floor_cycle)
// ---------------------------------------------------------------------------

/// The pure half of ``_floor_cycle``: snap one UTC instant to the latest
/// advertised cycle hour.
///
/// Cycles are sorted like ``tuple(sorted(int(hour) ...))``; the latest
/// cycle at or before the instant's hour wins, and when none is, the
/// instant rolls back one day to the latest cycle.  The result is the
/// cycle's epoch seconds, exactly as Python's
/// ``value.replace(hour=..., minute=0, second=0, microsecond=0)``.
pub(crate) fn floor_cycle_epoch_core(epoch_seconds: i64, cycles: &[i64]) -> Result<i64, String> {
    let mut sorted = cycles.to_vec();
    sorted.sort_unstable();
    if sorted.is_empty() {
        return Err("GeoMet cycle hours cannot be empty".to_string());
    }
    let value = OffsetDateTime::from_unix_timestamp(epoch_seconds)
        .map_err(|_| "epoch seconds are out of range".to_string())?;
    let current_hour = i64::from(value.hour());
    let mut day = value;
    let hour = match sorted
        .iter()
        .copied()
        .filter(|cycle| *cycle <= current_hour)
        .next_back()
    {
        Some(hour) => hour,
        None => {
            day = value
                .checked_sub(Duration::days(1))
                .ok_or_else(|| "epoch seconds are out of range".to_string())?;
            sorted[sorted.len() - 1]
        }
    };
    if !(0..=23).contains(&hour) {
        // datetime.replace(hour=hour) raises the same ValueError.
        return Err("hour must be in 0..23".to_string());
    }
    let midnight =
        Time::from_hms(hour as u8, 0, 0).map_err(|_| "hour must be in 0..23".to_string())?;
    let floored = PrimitiveDateTime::new(day.date(), midnight).assume_utc();
    Ok(floored.unix_timestamp())
}

/// Snap one UTC instant to the latest advertised model cycle hour.
///
/// ``epoch_seconds`` is the instant and ``cycles`` the model's published
/// cycle hours (such as GDPS's ``[0, 12]`` or RDPS's
/// ``[0, 6, 12, 18]``).  The result is the cycle's UTC epoch seconds.
/// Empty cycle lists raise ``ValueError("GeoMet cycle hours cannot be
/// empty")``; cycle hours outside ``0..=23`` raise the ``ValueError``
/// Python's ``datetime.replace`` produces, and an instant outside the
/// supported calendar range reports that as a ``ValueError`` too.
#[pyfunction]
fn geomet_floor_cycle_epoch(epoch_seconds: i64, cycles: Vec<i64>) -> PyResult<i64> {
    floor_cycle_epoch_core(epoch_seconds, &cycles).map_err(PyValueError::new_err)
}

// ---------------------------------------------------------------------------
// WMS GetFeatureInfo bounding box (build_feature_info_params)
// ---------------------------------------------------------------------------

/// The pure half of the BBOX value in ``build_feature_info_params``:
/// wrap the longitude, then clamp the 3x3 pixel request box into the
/// valid coordinate range and format it latitude-first.
pub(crate) fn feature_info_bbox_core(lat: f64, lon: f64, margin: f64) -> String {
    let lon = (lon + 180.0).rem_euclid(360.0) - 180.0;
    format!(
        "{:.6},{:.6},{:.6},{:.6}",
        (lat - margin).max(-90.0),
        (lon - margin).max(-180.0),
        (lat + margin).min(90.0),
        (lon + margin).min(180.0),
    )
}

/// Format the WMS 1.3 ``GetFeatureInfo`` BBOX for one point.
///
/// EPSG:4326 uses latitude-first axis order in WMS 1.3, so the result is
/// ``"min_lat,min_lon,max_lat,max_lon"`` with six decimals, exactly as
/// ``build_feature_info_params`` builds it.  The longitude wraps into
/// ``[-180, 180)`` first, and the margins (0.2 degrees in the provider)
/// are clamped so the 3x3 request never leaves the valid coordinate
/// range.
#[pyfunction]
fn geomet_feature_info_bbox(lat: f64, lon: f64, margin: f64) -> String {
    feature_info_bbox_core(lat, lon, margin)
}

// ---------------------------------------------------------------------------
// Capabilities dimension defaults (_dimension_defaults)
// ---------------------------------------------------------------------------

/// The message a malformed capabilities document reports, matching the
/// Python ``RetrievalError`` wording.
const INVALID_CAPABILITIES_XML: &str = "GeoMet returned invalid capabilities XML";

/// Sentinel error for any well-formedness violation; every failure
/// carries the same caller-facing message.
struct InvalidXml;

type XmlResult<T> = Result<T, InvalidXml>;

/// UTF-8 byte-order mark, allowed ahead of the XML declaration.
const UTF8_BOM: [u8; 3] = [0xEF, 0xBB, 0xBF];

/// One parsed start tag: its possibly prefixed name, the decoded values
/// of its unprefixed ``name`` and ``default`` attributes when present,
/// and whether it closed itself with ``/>``.
struct StartTag<'a> {
    name: &'a str,
    name_attr: Option<String>,
    default_attr: Option<String>,
    self_closing: bool,
}

/// The local part of a prefixed tag name, the part ElementTree exposes
/// after namespace resolution.
fn local_name(name: &str) -> &str {
    match name.rfind(':') {
        Some(index) => &name[index + 1..],
        None => name,
    }
}

/// Record one ``Dimension`` element's ``name``/``default`` pair.
///
/// Mirrors ``_dimension_defaults``' element loop: any tag prefix is
/// ignored, the attribute names must match exactly (namespaced
/// attributes belong to another namespace), and both values must be
/// present and non-empty.
fn record_dimension(
    name: &str,
    name_attr: Option<String>,
    default_attr: Option<String>,
    dimensions: &mut Vec<(String, String)>,
) {
    if local_name(name) != "Dimension" {
        return;
    }
    if let (Some(name_value), Some(default_value)) = (name_attr, default_attr) {
        if !name_value.is_empty() && !default_value.is_empty() {
            dimensions.push((name_value, default_value));
        }
    }
}

/// XML 1.0's well-formed character set: the three whitespace exceptions
/// and the remaining scalar-value ranges, which excludes surrogates and
/// controls.
fn is_valid_xml_char_code(code: u32) -> bool {
    matches!(
        code,
        0x9 | 0xA | 0xD | 0x20..=0xD7FF | 0xE000..=0xFFFD | 0x10000..=0x10FFFF
    )
}

/// Byte-level scanner enforcing XML well-formedness on the documents a
/// WMS ``GetCapabilities`` response can be: one document element,
/// balanced and matching tags, quoted attribute values, declared only
/// through the five predefined entities and numeric character
/// references, and no markup or text outside the document element.
struct XmlScanner<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> XmlScanner<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        XmlScanner { bytes, pos: 0 }
    }

    fn at_end(&self) -> bool {
        self.pos >= self.bytes.len()
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.pos).copied()
    }

    fn peek_at(&self, offset: usize) -> Option<u8> {
        self.bytes.get(self.pos + offset).copied()
    }

    fn starts_with(&self, prefix: &[u8]) -> bool {
        self.bytes[self.pos..].starts_with(prefix)
    }

    fn is_xml_whitespace(byte: u8) -> bool {
        matches!(byte, b' ' | b'\t' | b'\n' | b'\r')
    }

    fn skip_whitespace(&mut self) {
        while self.peek().is_some_and(Self::is_xml_whitespace) {
            self.pos += 1;
        }
    }

    fn is_name_start(byte: u8) -> bool {
        byte.is_ascii_alphabetic() || byte == b'_' || byte == b':' || byte >= 0x80
    }

    fn is_name_char(byte: u8) -> bool {
        Self::is_name_start(byte) || byte.is_ascii_digit() || byte == b'-' || byte == b'.'
    }

    /// Parse one XML name.  Names may carry any UTF-8 bytes, which the
    /// underlying ``&str`` input guarantees are valid.
    fn parse_name(&mut self) -> XmlResult<&'a str> {
        let bytes: &'a [u8] = self.bytes;
        let start = self.pos;
        match bytes.get(start) {
            Some(&byte) if Self::is_name_start(byte) => self.pos += 1,
            _ => return Err(InvalidXml),
        }
        while self.peek().is_some_and(Self::is_name_char) {
            self.pos += 1;
        }
        std::str::from_utf8(&bytes[start..self.pos]).map_err(|_| InvalidXml)
    }

    /// Parse one reference at ``&``, decoding the five predefined
    /// entities and numeric character references; any other name is not
    /// well-formed.
    fn parse_reference(&mut self) -> XmlResult<String> {
        self.pos += 1; // '&'
        match self.peek() {
            Some(b'#') => {
                self.pos += 1;
                let hex = self.peek() == Some(b'x');
                if hex {
                    self.pos += 1;
                }
                let start = self.pos;
                loop {
                    match self.peek() {
                        Some(b';') => break,
                        Some(byte) if hex && byte.is_ascii_hexdigit() => self.pos += 1,
                        Some(byte) if !hex && byte.is_ascii_digit() => self.pos += 1,
                        _ => return Err(InvalidXml),
                    }
                }
                if self.pos == start {
                    return Err(InvalidXml);
                }
                let digits =
                    std::str::from_utf8(&self.bytes[start..self.pos]).map_err(|_| InvalidXml)?;
                let radix = if hex { 16 } else { 10 };
                let code = u32::from_str_radix(digits, radix).map_err(|_| InvalidXml)?;
                self.pos += 1; // ';'
                if !is_valid_xml_char_code(code) {
                    return Err(InvalidXml);
                }
                Ok(char::from_u32(code).ok_or(InvalidXml)?.to_string())
            }
            Some(_) => {
                let name = self.parse_name()?;
                if self.peek() != Some(b';') {
                    return Err(InvalidXml);
                }
                self.pos += 1; // ';'
                let decoded = match name {
                    "amp" => "&",
                    "lt" => "<",
                    "gt" => ">",
                    "quot" => "\"",
                    "apos" => "'",
                    _ => return Err(InvalidXml),
                };
                Ok(decoded.to_string())
            }
            None => Err(InvalidXml),
        }
    }

    /// Scan character data until the next ``<`` markup or ``&``
    /// reference, rejecting the forbidden ``]]>`` sequence.
    fn skip_char_data(&mut self) -> XmlResult<()> {
        loop {
            match self.peek() {
                None | Some(b'<') | Some(b'&') => return Ok(()),
                Some(b']') if self.starts_with(b"]]>") => return Err(InvalidXml),
                Some(_) => self.pos += 1,
            }
        }
    }

    /// Skip a comment whose content may not contain ``--``.
    fn skip_comment(&mut self) -> XmlResult<()> {
        self.pos += b"<!--".len();
        loop {
            let Some(offset) = memchr::memchr(b'-', &self.bytes[self.pos..]) else {
                return Err(InvalidXml);
            };
            self.pos += offset;
            if self.starts_with(b"-->") {
                self.pos += 3;
                return Ok(());
            }
            if self.peek_at(1) == Some(b'-') {
                // "--" can only ever start the terminator.
                return Err(InvalidXml);
            }
            self.pos += 1;
        }
    }

    /// Skip a CDATA section, which ends at the first ``]]>``.
    fn skip_cdata(&mut self) -> XmlResult<()> {
        self.pos += b"<![CDATA[".len();
        loop {
            let Some(offset) = memchr::memchr(b']', &self.bytes[self.pos..]) else {
                return Err(InvalidXml);
            };
            self.pos += offset;
            if self.starts_with(b"]]>") {
                self.pos += 3;
                return Ok(());
            }
            self.pos += 1;
        }
    }

    fn skip_to_pi_end(&mut self) -> XmlResult<()> {
        loop {
            let Some(offset) = memchr::memchr(b'?', &self.bytes[self.pos..]) else {
                return Err(InvalidXml);
            };
            self.pos += offset;
            if self.peek_at(1) == Some(b'>') {
                self.pos += 2;
                return Ok(());
            }
            self.pos += 1;
        }
    }

    /// Skip a processing instruction; the target ``xml`` is reserved
    /// for the document declaration, which is handled separately.
    fn skip_processing_instruction(&mut self) -> XmlResult<()> {
        self.pos += 2; // "<?"
        let target = self.parse_name()?;
        if target.eq_ignore_ascii_case("xml") {
            return Err(InvalidXml);
        }
        self.skip_to_pi_end()
    }

    /// Skip the document's XML declaration, which must be the first
    /// markup of the document.
    fn skip_xml_declaration(&mut self) -> XmlResult<()> {
        self.pos += 2; // "<?"
        self.parse_name()?; // "xml"
        self.skip_to_pi_end()
    }

    /// Skip one quoted string, used inside a DOCTYPE's internal subset.
    fn skip_quoted(&mut self) -> XmlResult<()> {
        let quote = self.peek().ok_or(InvalidXml)?;
        self.pos += 1;
        loop {
            match self.peek() {
                None => return Err(InvalidXml),
                Some(byte) if byte == quote => {
                    self.pos += 1;
                    return Ok(());
                }
                Some(_) => self.pos += 1,
            }
        }
    }

    /// Skip a DOCTYPE declaration, including its bracketed internal
    /// subset; declared entities are not resolved, so referencing one
    /// stays an error.
    fn skip_doctype(&mut self) -> XmlResult<()> {
        self.pos += b"<!DOCTYPE".len();
        self.skip_whitespace();
        self.parse_name()?;
        let mut brackets = 0usize;
        loop {
            match self.peek() {
                None => return Err(InvalidXml),
                Some(b'"') | Some(b'\'') => self.skip_quoted()?,
                Some(b'[') => {
                    brackets += 1;
                    self.pos += 1;
                }
                Some(b']') => {
                    brackets = brackets.saturating_sub(1);
                    self.pos += 1;
                }
                Some(b'>') => {
                    self.pos += 1;
                    if brackets == 0 {
                        return Ok(());
                    }
                }
                Some(_) => self.pos += 1,
            }
        }
    }

    /// Parse one attribute value: single or double quoted, free of raw
    /// ``<``, with references decoded.
    fn parse_attribute_value(&mut self) -> XmlResult<String> {
        let Some(quote) = self.peek().filter(|byte| *byte == b'"' || *byte == b'\'') else {
            return Err(InvalidXml);
        };
        self.pos += 1;
        let mut raw: Vec<u8> = Vec::new();
        loop {
            match self.peek() {
                None => return Err(InvalidXml),
                Some(byte) if byte == quote => {
                    self.pos += 1;
                    break;
                }
                Some(b'<') => return Err(InvalidXml),
                Some(b'&') => {
                    let decoded = self.parse_reference()?;
                    raw.extend_from_slice(decoded.as_bytes());
                }
                Some(byte) => {
                    raw.push(byte);
                    self.pos += 1;
                }
            }
        }
        String::from_utf8(raw).map_err(|_| InvalidXml)
    }

    /// Parse one start tag with its attributes, rejecting duplicates
    /// and values that are missing their quotes.
    fn parse_start_tag(&mut self) -> XmlResult<StartTag<'a>> {
        self.pos += 1; // '<'
        let name = self.parse_name()?;
        let mut name_attr: Option<String> = None;
        let mut default_attr: Option<String> = None;
        let mut seen: Vec<&str> = Vec::new();
        let mut self_closing = false;
        loop {
            let whitespace_start = self.pos;
            self.skip_whitespace();
            let separated = self.pos > whitespace_start;
            match self.peek() {
                None => return Err(InvalidXml),
                Some(b'>') => {
                    self.pos += 1;
                    break;
                }
                Some(b'/') if self.peek_at(1) == Some(b'>') => {
                    self.pos += 2;
                    self_closing = true;
                    break;
                }
                Some(_) => {
                    if !separated {
                        // Attributes need separating whitespace.
                        return Err(InvalidXml);
                    }
                    let attribute = self.parse_name()?;
                    self.skip_whitespace();
                    if self.peek() != Some(b'=') {
                        return Err(InvalidXml);
                    }
                    self.pos += 1;
                    self.skip_whitespace();
                    let value = self.parse_attribute_value()?;
                    if seen.contains(&attribute) {
                        return Err(InvalidXml);
                    }
                    seen.push(attribute);
                    if attribute == "name" {
                        name_attr = Some(value);
                    } else if attribute == "default" {
                        default_attr = Some(value);
                    }
                }
            }
        }
        Ok(StartTag {
            name,
            name_attr,
            default_attr,
            self_closing,
        })
    }

    fn parse_end_tag(&mut self) -> XmlResult<&'a str> {
        self.pos += 2; // "</"
        let name = self.parse_name()?;
        self.skip_whitespace();
        if self.peek() != Some(b'>') {
            return Err(InvalidXml);
        }
        self.pos += 1;
        Ok(name)
    }

    /// Parse the whole document: prolog, root element, content, and
    /// epilog, collecting every ``Dimension`` default pair in document
    /// order.
    fn parse_document(&mut self) -> XmlResult<Vec<(String, String)>> {
        let mut dimensions: Vec<(String, String)> = Vec::new();
        if self.starts_with(&UTF8_BOM) {
            self.pos += UTF8_BOM.len();
        }
        // The XML declaration, when present, must be the first markup.
        if self.starts_with(b"<?xml") {
            match self.peek_at(5) {
                Some(b' ') | Some(b'\t') | Some(b'\n') | Some(b'\r') | Some(b'?') => {
                    self.skip_xml_declaration()?;
                }
                _ => {}
            }
        }
        // Prolog: whitespace, comments, processing instructions, and at
        // most one DOCTYPE may precede the document element.
        loop {
            self.skip_whitespace();
            if self.at_end() {
                return Err(InvalidXml);
            }
            if self.starts_with(b"<!DOCTYPE") {
                self.skip_doctype()?;
            } else if self.starts_with(b"<!--") {
                self.skip_comment()?;
            } else if self.starts_with(b"<?") {
                self.skip_processing_instruction()?;
            } else {
                break;
            }
        }
        // The document element must be a start tag.
        if self.at_end()
            || self.peek() != Some(b'<')
            || self.starts_with(b"</")
            || self.starts_with(b"<!")
            || self.starts_with(b"<?")
        {
            return Err(InvalidXml);
        }
        let root = self.parse_start_tag()?;
        record_dimension(
            root.name,
            root.name_attr,
            root.default_attr,
            &mut dimensions,
        );
        if !root.self_closing {
            self.parse_content(root.name, &mut dimensions)?;
        }
        self.parse_epilog()?;
        Ok(dimensions)
    }

    /// Parse the open document element's content until its matching end
    /// tag, rejecting mismatched, unclosed, and invalid markup.
    fn parse_content(
        &mut self,
        root_name: &'a str,
        dimensions: &mut Vec<(String, String)>,
    ) -> XmlResult<()> {
        let mut stack: Vec<&'a str> = vec![root_name];
        loop {
            // Character data and entity references up to the next markup.
            loop {
                self.skip_char_data()?;
                if self.at_end() {
                    // An open element never closed.
                    return Err(InvalidXml);
                }
                if self.peek() == Some(b'&') {
                    // Content references are validated, not retained.
                    self.parse_reference()?;
                } else {
                    break;
                }
            }
            if self.starts_with(b"</") {
                let name = self.parse_end_tag()?;
                match stack.pop() {
                    Some(open) if open == name => {}
                    _ => return Err(InvalidXml),
                }
                if stack.is_empty() {
                    return Ok(());
                }
            } else if self.starts_with(b"<!--") {
                self.skip_comment()?;
            } else if self.starts_with(b"<![CDATA[") {
                self.skip_cdata()?;
            } else if self.starts_with(b"<?") {
                self.skip_processing_instruction()?;
            } else {
                // Anything else must be a start tag; "<!..." declarations
                // and invalid names fail inside parse_start_tag.
                let tag = self.parse_start_tag()?;
                record_dimension(tag.name, tag.name_attr, tag.default_attr, dimensions);
                if !tag.self_closing {
                    stack.push(tag.name);
                }
            }
        }
    }

    /// After the document element only whitespace, comments, and
    /// processing instructions may follow.
    fn parse_epilog(&mut self) -> XmlResult<()> {
        loop {
            self.skip_whitespace();
            if self.at_end() {
                return Ok(());
            }
            if self.starts_with(b"<!--") {
                self.skip_comment()?;
            } else if self.starts_with(b"<?") {
                self.skip_processing_instruction()?;
            } else {
                return Err(InvalidXml);
            }
        }
    }
}

/// The pure half of ``_dimension_defaults``: verify the capabilities
/// document is well-formed XML and collect every ``Dimension``
/// element's non-empty ``name``/``default`` attribute pair, in document
/// order (Python builds its dict from these pairs, so a repeated name
/// keeps its last value there).
pub(crate) fn parse_dimension_defaults_core(
    xml_text: &str,
) -> Result<Vec<(String, String)>, String> {
    XmlScanner::new(xml_text.as_bytes())
        .parse_document()
        .map_err(|_| INVALID_CAPABILITIES_XML.to_string())
}

/// Read the ``Dimension`` defaults out of one capabilities document.
///
/// The document must be well-formed XML: unclosed or mismatched tags,
/// unclosed attribute quotes, an invalid tag name, duplicate
/// attributes, undeclared entities, a missing document element, or any
/// text or markup after it all raise
/// ``ValueError("GeoMet returned invalid capabilities XML")`` --
/// the wording of the Python ``RetrievalError``.  The XML declaration,
/// comments, CDATA sections, and a DOCTYPE (including its internal
/// subset) are handled and skipped.  Every ``<Dimension>`` or
/// ``<ns:Dimension>`` element at any depth contributes its unprefixed
/// ``name`` and ``default`` attributes, single or double quoted, with
/// the predefined and numeric entities decoded; elements missing either
/// attribute, or carrying an empty value, contribute nothing.  The
/// pairs return in document order.
#[pyfunction]
fn geomet_parse_dimension_defaults(xml_text: &str) -> PyResult<Vec<(String, String)>> {
    parse_dimension_defaults_core(xml_text).map_err(PyValueError::new_err)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(geomet_to_rotated, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_to_geographic, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_rotated_contains, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_rotated_outline, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_point_in_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_floor_cycle_epoch, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_feature_info_bbox, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_parse_dimension_defaults, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The operational RLatLon0.09 grid metadata published by ECCC, the
    /// same values ``RDPS_ROTATED_DOMAIN`` carries.
    const RDPS_NORTH_POLE_LAT: f64 = 31.758312;
    const RDPS_NORTH_POLE_LON: f64 = 87.597031;
    const RDPS_RLON_MIN: f64 = -53.858588;
    const RDPS_RLON_MAX: f64 = 48.990594;
    const RDPS_RLAT_MIN: f64 = -48.805954;
    const RDPS_RLAT_MAX: f64 = 45.464939;

    const HOUR: i64 = 3_600;
    /// 2026-07-22T00:00:00Z, the GDPS run of the Python suite.
    const T00_JULY_22: i64 = 1_784_678_400;

    #[test]
    fn rotated_round_trip_matches_the_operational_rdps_corner() {
        let (lat, lon) = to_geographic_core(
            RDPS_NORTH_POLE_LAT,
            RDPS_NORTH_POLE_LON,
            RDPS_RLAT_MIN,
            RDPS_RLON_MIN,
        );
        assert!((lat + 3.771_776).abs() < 1.0e-5, "latitude {lat}");
        assert!((lon + 124.613_048).abs() < 1.0e-5, "longitude {lon}");

        let (rotated_lat, rotated_lon) =
            to_rotated_core(RDPS_NORTH_POLE_LAT, RDPS_NORTH_POLE_LON, lat, lon);
        assert!((rotated_lat - RDPS_RLAT_MIN).abs() < 1.0e-8);
        assert!((rotated_lon - RDPS_RLON_MIN).abs() < 1.0e-8);
    }

    #[test]
    fn rotated_contains_matches_the_rdps_domain() {
        let contains = |lat: f64, lon: f64| {
            rotated_contains_core(
                RDPS_NORTH_POLE_LAT,
                RDPS_NORTH_POLE_LON,
                RDPS_RLON_MIN,
                RDPS_RLON_MAX,
                RDPS_RLAT_MIN,
                RDPS_RLAT_MAX,
                lat,
                lon,
            )
        };
        // Montreal, Oklahoma City, and Anchorage sit on the grid.
        for (lat, lon) in [(45.5, -73.6), (35.2, -97.4), (61.2, -149.9)] {
            assert!(contains(lat, lon), "{lat}, {lon}");
        }
        // The Philippines and Guam fall outside the rotated rectangle.
        for (lat, lon) in [(14.5995, 120.9842), (13.4443, 144.7937)] {
            assert!(!contains(lat, lon), "{lat}, {lon}");
        }
        // The envelope is nearly global; only the rotated test is honest.
        assert!(!contains(f64::NAN, 0.0));
        assert!(!contains(0.0, f64::NAN));
        assert!(!contains(90.5, 0.0));
        assert!(!contains(-91.0, 0.0));
    }

    #[test]
    fn rotated_outline_samples_every_edge_and_closes_itself() {
        let outline = rotated_outline_core(
            RDPS_NORTH_POLE_LAT,
            RDPS_NORTH_POLE_LON,
            RDPS_RLON_MIN,
            RDPS_RLON_MAX,
            RDPS_RLAT_MIN,
            RDPS_RLAT_MAX,
            28,
        );
        assert_eq!(outline.len(), 4 * 28 + 1);
        assert!(outline.len() > 40, "the map outline needs real detail");

        // Two samples per edge visit both corners of every edge, plus
        // the closing repeat; anything smaller clamps to two.
        let two = rotated_outline_core(
            RDPS_NORTH_POLE_LAT,
            RDPS_NORTH_POLE_LON,
            RDPS_RLON_MIN,
            RDPS_RLON_MAX,
            RDPS_RLAT_MIN,
            RDPS_RLAT_MAX,
            2,
        );
        assert_eq!(two.len(), 9);
        assert_eq!(
            rotated_outline_core(
                RDPS_NORTH_POLE_LAT,
                RDPS_NORTH_POLE_LON,
                RDPS_RLON_MIN,
                RDPS_RLON_MAX,
                RDPS_RLAT_MIN,
                RDPS_RLAT_MAX,
                0,
            )
            .len(),
            9
        );
        assert_eq!(
            rotated_outline_core(
                RDPS_NORTH_POLE_LAT,
                RDPS_NORTH_POLE_LON,
                RDPS_RLON_MIN,
                RDPS_RLON_MAX,
                RDPS_RLAT_MIN,
                RDPS_RLAT_MAX,
                -3,
            )
            .len(),
            9
        );

        // The perimeter starts and closes on the same rotated corner,
        // converted to a geographic (lon, lat) point.
        let (corner_lat, corner_lon) = to_geographic_core(
            RDPS_NORTH_POLE_LAT,
            RDPS_NORTH_POLE_LON,
            RDPS_RLAT_MIN,
            RDPS_RLON_MIN,
        );
        assert!((two[0].0 - corner_lon).abs() < 1.0e-9);
        assert!((two[0].1 - corner_lat).abs() < 1.0e-9);
        assert!((two[8].0 - two[0].0).abs() < 1.0e-12);
        assert!((two[8].1 - two[0].1).abs() < 1.0e-12);
        // The second point is a quarter of the way along the southern
        // edge, verified against the Python outline's own value.
        assert!((two[1].0 + 94.027_604).abs() < 1.0e-5);
        assert!((two[1].1 - 9.406_391).abs() < 1.0e-5);
    }

    #[test]
    fn point_in_bounds_wraps_longitude_and_handles_the_antimeridian() {
        // GDPS's global envelope admits every valid coordinate.
        assert!(point_in_bounds_core(
            45.5, -73.6, -180.0, 180.0, -90.0, 90.0
        ));
        assert!(point_in_bounds_core(
            14.5995, 120.9842, -180.0, 180.0, -90.0, 90.0
        ));
        // RDPS's coarse map envelope stops at -3.825 south.
        assert!(point_in_bounds_core(
            45.5, -73.6, -180.0, 180.0, -3.825, 90.0
        ));
        assert!(!point_in_bounds_core(
            -10.0, 0.0, -180.0, 180.0, -3.825, 90.0
        ));
        // lon0 > lon1 spans the antimeridian.
        assert!(point_in_bounds_core(0.0, 160.0, 150.0, -150.0, -5.0, 5.0));
        assert!(point_in_bounds_core(0.0, -170.0, 150.0, -150.0, -5.0, 5.0));
        assert!(!point_in_bounds_core(0.0, 0.0, 150.0, -150.0, -5.0, 5.0));
        // 190 degrees east wraps to -170, -190 to 170.
        assert!(point_in_bounds_core(0.0, 190.0, 150.0, -150.0, -5.0, 5.0));
        assert!(point_in_bounds_core(
            0.0, -190.0, -180.0, 180.0, -90.0, 90.0
        ));
        // Non-finite and out-of-range coordinates are never in bounds.
        assert!(!point_in_bounds_core(
            f64::NAN,
            0.0,
            -180.0,
            180.0,
            -90.0,
            90.0
        ));
        assert!(!point_in_bounds_core(
            0.0,
            f64::NAN,
            -180.0,
            180.0,
            -90.0,
            90.0
        ));
        assert!(!point_in_bounds_core(95.0, 0.0, -180.0, 180.0, -90.0, 90.0));
    }

    #[test]
    fn floor_cycle_snaps_to_the_latest_advertised_hour() {
        // The fixture instant really is 2026-07-22T00:00:00Z.
        let midnight = OffsetDateTime::from_unix_timestamp(T00_JULY_22).unwrap();
        assert_eq!(midnight.year(), 2026);
        assert_eq!(midnight.day(), 22);
        assert_eq!(midnight.hour(), 0);

        // GDPS publishes 0 and 12; RDPS 0, 6, 12, and 18.
        let half_past_five = T00_JULY_22 + 5 * HOUR + 1_800;
        assert_eq!(
            floor_cycle_epoch_core(half_past_five, &[0, 12]).unwrap(),
            T00_JULY_22
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 13 * HOUR, &[0, 12]).unwrap(),
            T00_JULY_22 + 12 * HOUR
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22, &[0, 12]).unwrap(),
            T00_JULY_22
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 3 * HOUR, &[0, 6, 12, 18]).unwrap(),
            T00_JULY_22
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 7 * HOUR, &[0, 6, 12, 18]).unwrap(),
            T00_JULY_22 + 6 * HOUR
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 21 * HOUR + 1_800, &[0, 6, 12, 18]).unwrap(),
            T00_JULY_22 + 18 * HOUR
        );
        // No cycle at or before the hour rolls back to the previous
        // day's latest cycle.
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 3 * HOUR, &[6, 18]).unwrap(),
            T00_JULY_22 - 6 * HOUR
        );
        // Unsorted and duplicated hours behave like the sorted set.
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 13 * HOUR, &[12, 0]).unwrap(),
            T00_JULY_22 + 12 * HOUR
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22 + 13 * HOUR, &[0, 12, 12, 0]).unwrap(),
            T00_JULY_22 + 12 * HOUR
        );
    }

    #[test]
    fn floor_cycle_rejects_empty_cycles_and_invalid_hours() {
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22, &[]).unwrap_err(),
            "GeoMet cycle hours cannot be empty"
        );
        // Hour 24 with an hour-0 instant rolls back a day and then
        // fails the datetime.replace hour range.
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22, &[24]).unwrap_err(),
            "hour must be in 0..23"
        );
        assert_eq!(
            floor_cycle_epoch_core(T00_JULY_22, &[-1]).unwrap_err(),
            "hour must be in 0..23"
        );
        // An instant outside the supported calendar range.
        assert_eq!(
            floor_cycle_epoch_core(i64::MIN, &[0, 12]).unwrap_err(),
            "epoch seconds are out of range"
        );
    }

    #[test]
    fn feature_info_bbox_formats_the_clamped_wms_corners() {
        assert_eq!(
            feature_info_bbox_core(45.5, -73.6, 0.20),
            "45.300000,-73.800000,45.700000,-73.400000"
        );
        // 190 degrees east normalizes to -170 before the margin applies.
        assert_eq!(
            feature_info_bbox_core(45.5, 190.0, 0.20),
            "45.300000,-170.200000,45.700000,-169.800000"
        );
        // The 3x3 pixel request never leaves the valid coordinate range.
        assert_eq!(
            feature_info_bbox_core(89.95, 0.0, 0.20),
            "89.750000,-0.200000,90.000000,0.200000"
        );
        assert_eq!(
            feature_info_bbox_core(-89.95, -179.95, 0.20),
            "-90.000000,-180.000000,-89.750000,-179.750000"
        );
        assert_eq!(
            feature_info_bbox_core(45.5, -73.6, 0.0),
            "45.500000,-73.600000,45.500000,-73.600000"
        );
    }

    #[test]
    fn dimension_defaults_read_the_capabilities_document() {
        let capabilities = concat!(
            "<WMS_Capabilities xmlns=\"http://www.opengis.net/wms\">",
            "<Capability><Layer>",
            "<Dimension name=\"time\" units=\"ISO8601\" ",
            "default=\"2026-07-22T06:00:00Z\">unused</Dimension>",
            "<Dimension name=\"reference_time\" units=\"ISO8601\" ",
            "default=\"2026-07-22T00:00:00Z\">unused</Dimension>",
            "</Layer></Capability></WMS_Capabilities>",
        );
        assert_eq!(
            parse_dimension_defaults_core(capabilities).unwrap(),
            vec![
                ("time".to_string(), "2026-07-22T06:00:00Z".to_string()),
                (
                    "reference_time".to_string(),
                    "2026-07-22T00:00:00Z".to_string()
                ),
            ]
        );
        // Repeated names are kept in document order; Python's dict keeps
        // the last one.
        let repeated = concat!(
            "<a><Dimension name=\"t\" default=\"1\"/>",
            "<Dimension name=\"t\" default=\"2\"/></a>"
        );
        assert_eq!(
            parse_dimension_defaults_core(repeated).unwrap(),
            vec![
                ("t".to_string(), "1".to_string()),
                ("t".to_string(), "2".to_string()),
            ]
        );
        // The document element itself counts, like root.iter() in Python.
        assert_eq!(
            parse_dimension_defaults_core("<Dimension name=\"t\" default=\"d\"/>").unwrap(),
            vec![("t".to_string(), "d".to_string())]
        );
    }

    #[test]
    fn dimension_defaults_accept_namespaces_quotes_and_entities() {
        let namespaced = concat!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<wms:WMS_Capabilities xmlns:wms=\"http://www.opengis.net/wms\">",
            "<wms:Capability><wms:Layer>",
            "<wms:Dimension name='time' default='2026-07-22T06:00:00Z'/>",
            "<Dimension name=\"reference_time\" units=\"ISO8601\" ",
            "default=\"2026-07-22T00:00:00Z\">unused</Dimension>",
            "</wms:Layer></wms:Capability></wms:WMS_Capabilities>",
        );
        assert_eq!(
            parse_dimension_defaults_core(namespaced).unwrap(),
            vec![
                ("time".to_string(), "2026-07-22T06:00:00Z".to_string()),
                (
                    "reference_time".to_string(),
                    "2026-07-22T00:00:00Z".to_string()
                ),
            ]
        );
        // Predefined and numeric entities decode inside both attributes.
        assert_eq!(
            parse_dimension_defaults_core("<Dimension name=\"a&amp;b\" default=\"&#65;&#x42;\"/>")
                .unwrap(),
            vec![("a&b".to_string(), "AB".to_string())]
        );
        assert_eq!(
            parse_dimension_defaults_core(
                "<Dimension name='&lt;&gt;&quot;&apos;' default='&#x27;'/>"
            )
            .unwrap(),
            vec![("<>\"'".to_string(), "'".to_string())]
        );
        // A namespaced attribute name belongs to another namespace and
        // never matches the unprefixed lookup.
        assert_eq!(
            parse_dimension_defaults_core(
                "<p:Dimension xmlns:p=\"http://e.x\" p:name=\"skipped\" name=\"kept\" default=\"d\"/>"
            )
            .unwrap(),
            vec![("kept".to_string(), "d".to_string())]
        );
    }

    #[test]
    fn dimension_defaults_skip_elements_without_both_values() {
        assert!(parse_dimension_defaults_core("<Dimension default=\"x\"/>")
            .unwrap()
            .is_empty());
        assert!(parse_dimension_defaults_core("<Dimension name=\"t\"/>")
            .unwrap()
            .is_empty());
        assert!(
            parse_dimension_defaults_core("<Dimension name=\"\" default=\"x\"/>")
                .unwrap()
                .is_empty()
        );
        assert!(
            parse_dimension_defaults_core("<Dimension name=\"t\" default=\"\"/>")
                .unwrap()
                .is_empty()
        );
        assert!(
            parse_dimension_defaults_core("<a><Other name=\"t\" default=\"d\"/></a>")
                .unwrap()
                .is_empty()
        );
        // Attributes of non-Dimension elements never leak.
        assert_eq!(
            parse_dimension_defaults_core(
                "<Layer name=\"t\" default=\"d\"><Dimension name=\"x\" default=\"y\"/></Layer>"
            )
            .unwrap(),
            vec![("x".to_string(), "y".to_string())]
        );
    }

    #[test]
    fn dimension_defaults_handle_prolog_declarations_and_cdata() {
        // Declaration, comments, a DOCTYPE with an internal subset, CDATA
        // hiding a fake Dimension, and trailing comments all parse, and
        // only the real element is extracted.
        let document = concat!(
            "<?xml version=\"1.0\"?>",
            "<!-- lead comment -->",
            "<!DOCTYPE WMS_Capabilities [<!ELEMENT Dimension EMPTY>]>\n",
            "<root><?php-like data?><!-- inner -->",
            "<![CDATA[<Dimension name=\"not\" default=\"real\"/>]]>",
            "text with an entity: ]]&gt; stays valid",
            "<Dimension name=\"t\" default=\"d\"/>",
            "</root><!-- after -->"
        );
        assert_eq!(
            parse_dimension_defaults_core(document).unwrap(),
            vec![("t".to_string(), "d".to_string())]
        );
        // A processing instruction that merely starts with "xml" is fine.
        let stylesheet = concat!(
            "<?xml-stylesheet href=\"plain.xsl\"?>",
            "<root><Dimension name=\"t\" default=\"d\"/></root>"
        );
        assert_eq!(
            parse_dimension_defaults_core(stylesheet).unwrap(),
            vec![("t".to_string(), "d".to_string())]
        );
        // A leading byte-order mark is transparent.
        let bommed = concat!(
            "\u{feff}",
            "<root><Dimension name=\"t\" default=\"d\"/></root>"
        );
        assert_eq!(
            parse_dimension_defaults_core(bommed).unwrap(),
            vec![("t".to_string(), "d".to_string())]
        );
    }

    #[test]
    fn dimension_defaults_reject_malformed_documents() {
        for broken in [
            "not XML",
            "",
            "   ",
            "<WMS_Capabilities>",
            "<a><b></a></b>",
            "<a foo=\"bar\">",
            "<a foo='bar>",
            "<a/>junk",
            "<a/> <b/>",
            "<a/><b/>",
            "<1a/>",
            "<>",
            "< a/>",
            "<a b=\"1\"b=\"2\"/>",
            "<a b=\"1\" b=\"2\"/>",
            "<!-- never closed",
            "<!-- a -- b -->",
            "<a><![CDATA[unterminated</a>",
            "<a><?unterminated</a>",
            "<a><?xml version=\"1.0\"?></a>",
            "<a>&nope;</a>",
            "<a b=\"&nope;\"/>",
            "<a>bad ]]> here</a>",
            "<a>&#;</a>",
            "<a>&#x;</a>",
            "<a>&#0;</a>",
            "<a>&#xD800;</a>",
            "<!DOCTYPE>",
            "<!DOCTYPE wms [<!ENTITY x \"x\">]>",
            "<a></a >x</a>",
        ] {
            assert_eq!(
                parse_dimension_defaults_core(broken).unwrap_err(),
                INVALID_CAPABILITIES_XML,
                "unexpectedly accepted: {broken:?}"
            );
        }
    }
}

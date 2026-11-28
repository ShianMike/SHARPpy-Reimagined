//! Native overlay marker hit-testing and place-name rules (milestone M38).
//!
//! Rust ports of the point-picking and reverse-geocode logic behind
//! ``sharpmod.maps.map_overlays`` and ``sharpmod.maps.place_names``:
//!
//! * the marker hit selection of ``marker_hits_at``
//!   ([`overlay_marker_hits_select`]) -- every reachable marker under a
//!   click, ranked by shape rank with the layer/shape order as the
//!   tie-break, where the screen-pixel grab radius takes precedence over
//!   the geographic ring;
//! * the prose composition of ``describe_at``
//!   ([`overlay_format_description`]) and the wording of
//!   ``OverlayMarkerHit.label`` ([`overlay_marker_hit_label`]);
//! * ``needs_town_name`` and its coordinate-pattern fallback
//!   ([`place_needs_town_name`]), the address promotion of
//!   ``_place_from_payload`` ([`place_from_address_fields`]), the U.S.
//!   gate of ``_payload_is_us`` ([`place_is_us_address`]), the bundled
//!   Census record parser ``_place_record_from_fields``
//!   ([`place_parse_record_fields`]), the settlement footprint radius of
//!   ``_area_proxy_radius_km`` ([`place_area_proxy_radius_km`]), and the
//!   tolerance-aware ring containment of ``_point_in_polygon``
//!   ([`place_point_in_polygon`]).
//!
//! The coordinate pattern is a hand-rolled equivalent of the module's
//! verbose, case-insensitive regular expression -- ASCII digits, Unicode
//! whitespace runs, the ``[,/ ]`` separator class, and ASCII word
//! boundaries -- so the crate needs no regex dependency.

use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// Marker hit selection (marker_hits_at)
// ---------------------------------------------------------------------------

/// One point-marker candidate for [`overlay_marker_hits_select`], carrying
/// the per-shape state ``marker_hits_at`` reads as
/// ``(rank, layer_index, shape_index, is_marker, radius_px,
/// projected_center_px, geom_contains)``.
///
/// ``projected_center_px`` is the marker's projected screen position,
/// supplied only when the caller has a projection and the shape carries a
/// marker longitude/latitude; ``geom_contains`` is the shape's geographic
/// containment of the clicked point.
pub(crate) type MarkerCandidate = (i64, usize, usize, bool, f64, Option<(f64, f64)>, bool);

/// The pure half of ``marker_hits_at``: keep every candidate marker the
/// click reaches -- non-markers are skipped; a marker with a positive pixel
/// radius, a projected center and a click position is kept within
/// ``max(6.0, radius + 2.5)`` pixels (geometry is not consulted in that
/// branch, matching the Python precedence); anything else falls back to
/// ``geom_contains``.  Survivors sort by ``(-rank, layer_index,
/// shape_index)``.
pub(crate) fn marker_hits_select_core(
    candidates: &[MarkerCandidate],
    point_px: Option<(f64, f64)>,
) -> Vec<(usize, usize)> {
    let mut found: Vec<(i64, usize, usize)> = Vec::new();
    for &(rank, layer_index, shape_index, is_marker, radius_px, projected, contains) in candidates {
        if !is_marker {
            continue;
        }
        let reached = match (radius_px > 0.0, projected, point_px) {
            (true, Some((center_x, center_y)), Some((x, y))) => {
                let hit_radius = 6.0_f64.max(radius_px + 2.5);
                (x - center_x) * (x - center_x) + (y - center_y) * (y - center_y)
                    <= hit_radius * hit_radius
            }
            _ => contains,
        };
        if reached {
            found.push((-rank, layer_index, shape_index));
        }
    }
    found.sort();
    found
        .into_iter()
        .map(|(_, layer_index, shape_index)| (layer_index, shape_index))
        .collect()
}

/// Select every point marker reachable at one click position.
///
/// ``candidates`` carry each shape's marker flag, rank, pixel radius,
/// projected screen center, and geographic containment; ``point_px`` is
/// the click in screen pixels (``None`` without a projection context).
/// The result is every ``(layer_index, shape_index)`` the click reaches,
/// highest-ranked first with the input order as the tie-break -- rank is a
/// visual priority, never an accessibility filter.
#[pyfunction]
#[pyo3(signature = (candidates, point_px = None))]
fn overlay_marker_hits_select(
    candidates: Vec<MarkerCandidate>,
    point_px: Option<(f64, f64)>,
) -> Vec<(usize, usize)> {
    marker_hits_select_core(&candidates, point_px)
}

// ---------------------------------------------------------------------------
// Prose composition (describe_at / OverlayMarkerHit.label)
// ---------------------------------------------------------------------------

/// The pure half of ``describe_at``'s text assembly: the owner layer's
/// title and the subject's label join into a heading (em dash), the
/// subject's description follows, and a distinct qualifier appends the
/// ``Also significant: ...`` / ``Also conditional intensity group N: ...``
/// note.  ``None`` when nothing at all can be said.
pub(crate) fn format_description_core(
    owner_title: &str,
    subject_label: &str,
    subject_description: &str,
    has_distinct_qualifier: bool,
    qualifier_level: i64,
    qualifier_label: &str,
) -> Option<String> {
    let mut heading = String::new();
    if !owner_title.is_empty() {
        heading = owner_title.to_string();
    }
    if !subject_label.is_empty() {
        heading = if heading.is_empty() {
            subject_label.to_string()
        } else {
            format!("{heading} \u{2014} {subject_label}")
        };
    }
    let mut lines: Vec<String> = Vec::new();
    if !heading.is_empty() {
        lines.push(heading);
    }
    if !subject_description.is_empty() {
        lines.push(subject_description.to_string());
    }
    if has_distinct_qualifier {
        let level = qualifier_level;
        let note = if !qualifier_label.is_empty() {
            qualifier_label.to_string()
        } else if level == 0 {
            // The pre-2026 ungraded area has no level of its own.
            "Significant severe".to_string()
        } else {
            String::new()
        };
        let graded = if level != 0 {
            format!("conditional intensity group {level}")
        } else {
            "significant".to_string()
        };
        let line = if note.is_empty() {
            format!("Also {graded}")
        } else {
            format!("Also {graded}: {note}")
        };
        lines.push(line);
    }
    if lines.is_empty() {
        None
    } else {
        Some(lines.join("\n"))
    }
}

/// Compose the prose naming whatever overlay covers a point.
///
/// ``owner_title``/``subject_label``/``subject_description`` describe the
/// shape the point sits in (the band when one covers it, otherwise the
/// qualifier).  ``has_distinct_qualifier`` is ``True`` when a hatched
/// qualifier exists besides the subject; its ``qualifier_level`` (the
/// ``hatch_level``, ``0`` for ungraded) and ``qualifier_label`` build the
/// trailing note.  Returns ``None`` when no heading, description, or
/// qualifier note exists, so ``"no overlay here"`` stays distinguishable
/// from ``"an overlay with nothing to say"``.
#[pyfunction]
fn overlay_format_description(
    owner_title: &str,
    subject_label: &str,
    subject_description: &str,
    has_distinct_qualifier: bool,
    qualifier_level: i64,
    qualifier_label: &str,
) -> Option<String> {
    format_description_core(
        owner_title,
        subject_label,
        subject_description,
        has_distinct_qualifier,
        qualifier_level,
        qualifier_label,
    )
}

/// The pure half of ``OverlayMarkerHit.label``: a report marker's own text
/// stands alone, other markers pair the product title with the item
/// (em dash), and an empty side falls back to the other -- or to
/// ``"Map marker"``.
pub(crate) fn marker_hit_label_core(
    layer_title: &str,
    shape_label: &str,
    marker_kind: &str,
) -> String {
    let product = layer_title.trim();
    let item = shape_label.trim();
    if marker_kind == "report" && !item.is_empty() {
        return item.to_string();
    }
    if !product.is_empty() && !item.is_empty() {
        return format!("{product} \u{2014} {item}");
    }
    if !product.is_empty() {
        product.to_string()
    } else if !item.is_empty() {
        item.to_string()
    } else {
        "Map marker".to_string()
    }
}

/// Word one reachable marker the way the overlap chooser presents it.
///
/// ``layer_title`` is the layer's title and ``shape_label`` the shape's
/// label (both read as ``str(value or "").strip()`` by the Python
/// property); ``marker_kind`` is the shape's marker kind.  A
/// ``"report"`` marker with a label returns that label alone, any other
/// pairable title and label join with an em dash, and a missing side falls
/// back to the other -- or to ``"Map marker"``.
#[pyfunction]
fn overlay_marker_hit_label(layer_title: &str, shape_label: &str, marker_kind: &str) -> String {
    marker_hit_label_core(layer_title, shape_label, marker_kind)
}

// ---------------------------------------------------------------------------
// The coordinate-label pattern (place_names._COORDINATE_LABEL_RE)
// ---------------------------------------------------------------------------

/// ASCII word character for the pattern's `\b` boundaries: `[0-9A-Za-z_]`.
fn is_ascii_word(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '_'
}

/// Python's `\b` at one position: ``previous``/``next`` are the
/// neighbouring characters (``None`` at the text ends, which count as
/// non-word), and exactly one side may be a word character.
fn word_boundary(previous: Option<char>, next: Option<char>) -> bool {
    match (previous, next) {
        (None, None) => false,
        (None, Some(next)) => is_ascii_word(next),
        (Some(previous), None) => is_ascii_word(previous),
        (Some(previous), Some(next)) => is_ascii_word(previous) != is_ascii_word(next),
    }
}

/// The ASCII digits starting at ``position`` (the pattern's `\d`).
fn digits_at(text: &[char], position: usize) -> usize {
    let mut end = position;
    while text
        .get(end)
        .is_some_and(|character| character.is_ascii_digit())
    {
        end += 1;
    }
    end - position
}

/// Consume the optional `(?:\.\d+)` fraction at ``position``: a literal dot
/// followed by one or more ASCII digits.  Returns the end position, or
/// ``None`` when the fraction does not start there (the group then
/// matches empty).
fn fraction_end(text: &[char], position: usize) -> Option<usize> {
    if text.get(position) != Some(&'.') {
        return None;
    }
    let end = position + 1 + digits_at(text, position + 1);
    (end > position + 1).then_some(end)
}

/// Whether the `\s*[,/ ]+\s*` bridge between the hemispheres decomposes:
/// leading whitespace, then a nonempty contiguous run of ``[,/ ]``, then
/// trailing whitespace.  Only the ASCII space is both whitespace and a
/// class member, so every other whitespace character must sit outside the
/// first-to-last comma-or-slash run, and a bridge without a comma or slash
/// still needs one space for the class run itself.
fn bridge_decomposes(bridge: &[char]) -> bool {
    let separator = |character: &char| *character == ',' || *character == '/';
    let Some(first) = bridge.iter().position(separator) else {
        return bridge.contains(&' ');
    };
    let last = bridge.iter().rposition(separator).unwrap_or(first);
    bridge[first..=last]
        .iter()
        .all(|character| *character == ',' || *character == '/' || *character == ' ')
}

/// Try the word-bounded hemisphere alternative at ``start``:
/// `\b\d{1,2}(?:\.\d+)?\s*[NS]\s*[,/ ]+\s*\d{1,3}(?:\.\d+)?\s*[EW]\b`
/// (case-insensitive).  Returns the match end.
fn hemisphere_match_at(text: &[char], start: usize) -> Option<usize> {
    if !word_boundary(
        start.checked_sub(1).map(|index| text[index]),
        text.get(start).copied(),
    ) {
        return None;
    }
    let latitude_digits = digits_at(text, start);
    if latitude_digits == 0 {
        return None;
    }
    // `\d{1,2}` is greedy and backtracks to one digit.
    for latitude_count in [2_usize, 1] {
        if latitude_count > latitude_digits {
            continue;
        }
        let after_digits = start + latitude_count;
        let after_fraction = fraction_end(text, after_digits).unwrap_or(after_digits);
        // `\s*[NS]`: greedy whitespace is safe, the letter is not one.
        let mut position = after_fraction;
        while text
            .get(position)
            .is_some_and(|character| character.is_whitespace())
        {
            position += 1;
        }
        if !matches!(text.get(position), Some('N' | 'n' | 'S' | 's')) {
            continue;
        }
        position += 1;
        // The bridge must span every whitespace/comma/slash character up
        // to the longitude digits, then decompose into the three parts.
        let bridge_start = position;
        while matches!(
            text.get(position),
            Some(character) if character.is_whitespace() || *character == ',' || *character == '/'
        ) {
            position += 1;
        }
        if position == bridge_start
            || !text
                .get(position)
                .is_some_and(|character| character.is_ascii_digit())
        {
            continue;
        }
        if !bridge_decomposes(&text[bridge_start..position]) {
            continue;
        }
        // `\d{1,3}(?:\.\d+)?\s*[EW]\b`, backtracking the digit count.
        let longitude_digits = digits_at(text, position);
        for longitude_count in [3_usize, 2, 1] {
            if longitude_count > longitude_digits {
                continue;
            }
            let after_longitude = position + longitude_count;
            let after_fraction = fraction_end(text, after_longitude).unwrap_or(after_longitude);
            let mut end = after_fraction;
            while text
                .get(end)
                .is_some_and(|character| character.is_whitespace())
            {
                end += 1;
            }
            if !matches!(text.get(end), Some('E' | 'e' | 'W' | 'w')) {
                continue;
            }
            if word_boundary(Some(text[end]), text.get(end + 1).copied()) {
                return Some(end + 1);
            }
        }
    }
    None
}

/// Try the comma-separated alternative at ``start``:
/// `[-+]?\d{1,2}(?:\.\d+)?\s*,\s*[-+]?\d{1,3}(?:\.\d+)?`.
/// Returns the match end.
fn comma_pair_match_at(text: &[char], start: usize) -> Option<usize> {
    let mut position = start;
    if matches!(text.get(position), Some('-' | '+')) {
        position += 1;
    }
    let latitude_digits = digits_at(text, position);
    if latitude_digits == 0 {
        return None;
    }
    // `\d{1,2}` is greedy and backtracks to one digit.
    for latitude_count in [2_usize, 1] {
        if latitude_count > latitude_digits {
            continue;
        }
        let after_digits = position + latitude_count;
        let after_fraction = fraction_end(text, after_digits).unwrap_or(after_digits);
        let mut comma = after_fraction;
        while text
            .get(comma)
            .is_some_and(|character| character.is_whitespace())
        {
            comma += 1;
        }
        if text.get(comma) != Some(&',') {
            continue;
        }
        let mut rest = comma + 1;
        while text
            .get(rest)
            .is_some_and(|character| character.is_whitespace())
        {
            rest += 1;
        }
        if matches!(text.get(rest), Some('-' | '+')) {
            rest += 1;
        }
        let longitude_digits = digits_at(text, rest);
        if longitude_digits == 0 {
            continue;
        }
        let after_longitude = rest + longitude_digits.min(3);
        return Some(fraction_end(text, after_longitude).unwrap_or(after_longitude));
    }
    None
}

/// The pure half of ``needs_town_name``'s ``_COORDINATE_LABEL_RE.search``:
/// either alternative matching anywhere decides the label is only a
/// coordinate fallback.
pub(crate) fn coordinate_label_matches(text: &str) -> bool {
    let characters: Vec<char> = text.chars().collect();
    (0..characters.len()).any(|start| {
        hemisphere_match_at(&characters, start).is_some()
            || comma_pair_match_at(&characters, start).is_some()
    })
}

/// Python's ``" ".join(value.split())``: runs of whitespace collapse to
/// single spaces and the result is trimmed.
fn whitespace_normalized(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// The pure half of ``needs_town_name``: a missing, empty, or
/// coordinate-pattern label needs a town name, and so does a label that is
/// only the model's own fallback wording (``model``, ``model`` + ``pt``,
/// or ``model`` + ``point``, compared case-insensitively over
/// whitespace-normalized text).
pub(crate) fn needs_town_name_core(label: Option<&str>, model: Option<&str>) -> bool {
    let text = whitespace_normalized(label.unwrap_or(""));
    if text.is_empty() || coordinate_label_matches(&text) {
        return true;
    }
    let model_text = whitespace_normalized(model.unwrap_or(""));
    if model_text.is_empty() {
        return false;
    }
    let folded = text.to_lowercase();
    let model_folded = model_text.to_lowercase();
    folded == model_folded
        || folded == format!("{model_folded}pt")
        || folded == format!("{model_folded} point")
}

/// Report whether a location label is only a generic model/coordinate
/// fallback.
///
/// ``label`` and ``model`` are read like ``str(value or "")``: ``None`` is
/// the empty string, and whitespace runs collapse before the checks.  An
/// empty label, a coordinate pattern (hemisphere pairs like
/// ``41.53N 88.39W`` or comma pairs like ``41.53, -88.39``), or the
/// model's own wording (``HRRR``, ``HRRRpt``, ``HRRR point``) all need a
/// town name.
#[pyfunction]
#[pyo3(signature = (label, model = None))]
fn place_needs_town_name(label: Option<&str>, model: Option<&str>) -> bool {
    needs_town_name_core(label, model)
}

// ---------------------------------------------------------------------------
// Reverse-geocode address rules
// ---------------------------------------------------------------------------

/// The pure half of ``_place_from_payload``: promote the first settlement
/// field carrying a value (``city``/``town``/``village``/
/// ``municipality``/``hamlet``, stripped), then append the first truthy
/// region (``state``/``region``/``country``, also stripped) unless it
/// repeats the settlement case-insensitively.  A region that is only
/// whitespace is truthy before stripping and then vanishes, leaving the
/// settlement alone -- exactly the Python ``or`` chain.
#[allow(clippy::too_many_arguments)]
pub(crate) fn from_address_fields_core(
    city: Option<&str>,
    town: Option<&str>,
    village: Option<&str>,
    municipality: Option<&str>,
    hamlet: Option<&str>,
    state: Option<&str>,
    region: Option<&str>,
    country: Option<&str>,
) -> String {
    let settlement = [city, town, village, municipality, hamlet]
        .into_iter()
        .flatten()
        .map(str::trim)
        .find(|value| !value.is_empty());
    let Some(settlement) = settlement else {
        return String::new();
    };
    let region = [state, region, country]
        .into_iter()
        .flatten()
        .find(|value| !value.is_empty())
        .map(str::trim)
        .unwrap_or("");
    if !region.is_empty() && region.to_lowercase() != settlement.to_lowercase() {
        format!("{settlement}, {region}")
    } else {
        settlement.to_string()
    }
}

/// Promote one reverse-geocode address payload to a town/region label.
///
/// The settlement fields are tried in the documented order and the first
/// one whose stripped value survives wins; the region is the first
/// non-empty of ``state``/``region``/``country``.  A region that repeats
/// the settlement case-insensitively is dropped, and no settlement at all
/// yields the empty string (counties, suburbs, and neighborhoods never
/// promote).
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn place_from_address_fields(
    city: Option<&str>,
    town: Option<&str>,
    village: Option<&str>,
    municipality: Option<&str>,
    hamlet: Option<&str>,
    state: Option<&str>,
    region: Option<&str>,
    country: Option<&str>,
) -> String {
    from_address_fields_core(
        city,
        town,
        village,
        municipality,
        hamlet,
        state,
        region,
        country,
    )
}

/// The pure half of ``_payload_is_us``: a country code decides outright
/// (case-insensitive ``"us"``); with no code, a missing country name is
/// unspecified (allowed) and otherwise the country name must be one of the
/// U.S. wordings.
pub(crate) fn is_us_address_core(country_code: Option<&str>, country: Option<&str>) -> bool {
    let code = country_code.unwrap_or("").trim().to_lowercase();
    if !code.is_empty() {
        return code == "us";
    }
    let name = country.unwrap_or("").trim().to_lowercase();
    if name.is_empty() {
        return true;
    }
    matches!(
        name.as_str(),
        "united states" | "united states of america" | "u.s." | "u.s.a." | "us" | "usa"
    )
}

/// Report whether an online reverse-geocode result is explicitly U.S. or
/// simply unspecified.
///
/// ``country_code`` decides when present (only ``"us"`` passes,
/// case-insensitively); without one, an empty ``country`` name means
/// unspecified and passes, and a present name must be one of the U.S.
/// wordings (``united states``, ``united states of america``, ``u.s.``,
/// ``u.s.a.``, ``us``, ``usa``).
#[pyfunction]
fn place_is_us_address(country_code: Option<&str>, country: Option<&str>) -> bool {
    is_us_address_core(country_code, country)
}

// ---------------------------------------------------------------------------
// Bundled Census place records
// ---------------------------------------------------------------------------

/// Statute miles to kilometres (``_KM_PER_MILE``).
const KM_PER_MILE: f64 = 1.609344;

/// Conservative widening of a settlement's circular footprint
/// (``_AREA_PROXY_RADIUS_SCALE``).
const AREA_PROXY_RADIUS_SCALE: f64 = 1.25;

/// The pure half of ``_place_record_from_fields``: current six-column and
/// legacy five-column records parse with Python ``float()`` semantics
/// (surrounding whitespace tolerated), the label must be non-empty, the
/// kind must be exactly ``place`` or ``subdivision``, the coordinates and
/// land area must be finite, and the area must be non-negative (absent
/// legacy columns read as ``0.0``).  The sixth census column is ignored.
pub(crate) fn parse_record_fields_core(
    fields: &[String],
) -> Option<(String, f64, f64, String, f64)> {
    if fields.len() < 5 {
        return None;
    }
    let label = fields[0].as_str();
    let kind = fields[3].as_str();
    let area_text = fields.get(5).map(String::as_str).unwrap_or("");
    let lat: f64 = fields[1].trim().parse().ok()?;
    let lon: f64 = fields[2].trim().parse().ok()?;
    let land_area_sqmi: f64 = if area_text.is_empty() {
        0.0
    } else {
        area_text.trim().parse().ok()?
    };
    if label.is_empty()
        || !matches!(kind, "place" | "subdivision")
        || !lat.is_finite()
        || !lon.is_finite()
        || !land_area_sqmi.is_finite()
        || land_area_sqmi < 0.0
    {
        return None;
    }
    Some((
        label.to_string(),
        lat,
        lon,
        kind.to_string(),
        land_area_sqmi,
    ))
}

/// Parse one bundled Census place record from its tab-separated fields.
///
/// Accepts the current six-column rows (label, latitude, longitude, kind,
/// geoid, land area) and the legacy five-column rows (land area reads as
/// ``0.0``); returns ``(label, latitude, longitude, kind, land_area_sqmi)``
/// or ``None`` for short, unparseable, non-finite, negative-area, or
/// foreign-kind rows.
#[pyfunction]
fn place_parse_record_fields(fields: Vec<String>) -> Option<(String, f64, f64, String, f64)> {
    parse_record_fields_core(&fields)
}

/// The pure half of ``_area_proxy_radius_km``: a Census entity's land area
/// becomes the radius of its equivalent circle, converted to kilometres
/// and widened by the conservative scale.  Missing, non-finite, or
/// non-positive areas carry no footprint.
pub(crate) fn area_proxy_radius_km_core(land_area_sqmi: Option<f64>) -> f64 {
    let Some(area) = land_area_sqmi else {
        return 0.0;
    };
    if !area.is_finite() || area <= 0.0 {
        return 0.0;
    }
    (area / std::f64::consts::PI).sqrt() * KM_PER_MILE * AREA_PROXY_RADIUS_SCALE
}

/// Resolve a settlement's conservative circular-footprint radius in
/// kilometres from its land area in square miles.
///
/// The area becomes the equivalent circle ``sqrt(area / pi)`` in miles,
/// converted with ``1.609344`` km per mile and widened by ``1.25``;
/// missing, non-finite, or non-positive areas return ``0.0``.
#[pyfunction]
fn place_area_proxy_radius_km(land_area_sqmi: Option<f64>) -> f64 {
    area_proxy_radius_km_core(land_area_sqmi)
}

// ---------------------------------------------------------------------------
// Tolerance-aware point-in-polygon (_point_in_polygon)
// ---------------------------------------------------------------------------

/// The on-segment tolerance of ``_point_on_segment``.
const SEGMENT_TOLERANCE: f64 = 1.0e-8;

/// The pure half of ``_point_on_segment``: a point lies on a segment when
/// the cross product stays within ``1e-8`` of the segment's scale and the
/// point sits inside the segment's padded bounding box.
pub(crate) fn point_on_segment_core(x: f64, y: f64, x1: f64, y1: f64, x2: f64, y2: f64) -> bool {
    let cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1);
    let scale = 1.0_f64.max((x2 - x1).abs()).max((y2 - y1).abs());
    if cross.abs() > SEGMENT_TOLERANCE * scale {
        return false;
    }
    x1.min(x2) - SEGMENT_TOLERANCE <= x
        && x <= x1.max(x2) + SEGMENT_TOLERANCE
        && y1.min(y2) - SEGMENT_TOLERANCE <= y
        && y <= y1.max(y2) + SEGMENT_TOLERANCE
}

/// The pure half of ``_point_in_polygon``: ray-cast a longitude/latitude
/// point against one closed ring, flipping parity across each edge the
/// ray crosses, with any on-segment hit counting as inside.  ``points``
/// are ``(lon, lat)`` pairs; an empty ring decides nothing.
pub(crate) fn point_in_polygon_core(lat: f64, lon: f64, points: &[(f64, f64)]) -> bool {
    let x = lon;
    let y = lat;
    let Some(mut previous) = points.last().copied() else {
        return false;
    };
    let mut inside = false;
    for &current in points {
        let (x1, y1) = previous;
        let (x2, y2) = current;
        if point_on_segment_core(x, y, x1, y1, x2, y2) {
            return true;
        }
        if (y1 > y) != (y2 > y) {
            let crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1);
            if x < crossing {
                inside = !inside;
            }
        }
        previous = current;
    }
    inside
}

/// Ray-cast one point against a closed boundary ring.
///
/// ``lat``/``lon`` is the query point and ``points`` the ring's
/// ``(lon, lat)`` vertices.  A point within ``1e-8`` of any edge counts as
/// inside; otherwise the odd/even crossing parity decides.  An empty ring
/// safely contains nothing.
#[pyfunction]
fn place_point_in_polygon(lat: f64, lon: f64, points: Vec<(f64, f64)>) -> bool {
    point_in_polygon_core(lat, lon, &points)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(overlay_marker_hits_select, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_format_description, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_marker_hit_label, module)?)?;
    module.add_function(wrap_pyfunction!(place_needs_town_name, module)?)?;
    module.add_function(wrap_pyfunction!(place_from_address_fields, module)?)?;
    module.add_function(wrap_pyfunction!(place_is_us_address, module)?)?;
    module.add_function(wrap_pyfunction!(place_parse_record_fields, module)?)?;
    module.add_function(wrap_pyfunction!(place_area_proxy_radius_km, module)?)?;
    module.add_function(wrap_pyfunction!(place_point_in_polygon, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn marker_hits_follow_rank_then_input_order() {
        let candidates = [
            (0, 0usize, 0usize, false, 0.0, None, true),
            (0, 0, 1, true, 0.0, None, true),
            (5, 1, 0, true, 4.0, Some((100.0, 100.0)), false),
            (5, 1, 1, true, 4.0, Some((100.0, 100.0)), false),
            (3, 2, 0, true, 4.0, Some((100.0, 100.0)), false),
            (0, 3, 0, true, 0.0, None, false),
        ];
        let hits = marker_hits_select_core(&candidates, Some((102.0, 100.0)));
        assert_eq!(hits, vec![(1, 0), (1, 1), (2, 0), (0, 1)]);
    }

    #[test]
    fn pixel_hits_take_precedence_over_geometry() {
        let projected = [(0, 0usize, 0usize, true, 10.0, Some((50.0, 50.0)), true)];
        // A pixel miss is skipped even though the geographic ring contains
        // the point, matching marker_hits_at's precedence.
        assert_eq!(
            marker_hits_select_core(&projected, Some((70.0, 50.0))),
            Vec::<(usize, usize)>::new()
        );
        // The grab radius is max(6.0, radius + 2.5) = 12.5 pixels here.
        assert_eq!(
            marker_hits_select_core(&projected, Some((59.0, 50.0))),
            vec![(0, 0)]
        );
        // The boundary is inclusive.
        assert_eq!(
            marker_hits_select_core(&projected, Some((62.5, 50.0))),
            vec![(0, 0)]
        );
        // The minimum grab radius applies to small markers.
        let small = [(0, 0usize, 0usize, true, 1.0, Some((50.0, 50.0)), false)];
        assert_eq!(
            marker_hits_select_core(&small, Some((55.0, 50.0))),
            vec![(0, 0)],
            "max(6.0, 3.5) still grabs 6 pixels"
        );
        assert_eq!(
            marker_hits_select_core(&small, Some((57.0, 50.0))),
            Vec::<(usize, usize)>::new()
        );
    }

    #[test]
    fn geometry_decides_without_a_projection_or_radius() {
        let geom_only = [(0, 0usize, 0usize, true, 0.0, None, true)];
        assert_eq!(
            marker_hits_select_core(&geom_only, Some((10.0, 10.0))),
            vec![(0, 0)]
        );
        assert_eq!(marker_hits_select_core(&geom_only, None), vec![(0, 0)]);
        // A radius without a click position still falls back to geometry.
        let unprojected = [(0, 0usize, 0usize, true, 5.0, None, true)];
        assert_eq!(marker_hits_select_core(&unprojected, None), vec![(0, 0)]);
        let geom_miss = [(0, 0usize, 0usize, true, 5.0, None, false)];
        assert!(marker_hits_select_core(&geom_miss, None).is_empty());
        assert!(marker_hits_select_core(&geom_miss, Some((1.0, 1.0))).is_empty());
    }

    #[test]
    fn descriptions_join_heading_and_qualifier_note() {
        assert_eq!(
            format_description_core(
                "Storm Prediction Center",
                "15%",
                "A 15% contour.",
                false,
                0,
                ""
            ),
            Some("Storm Prediction Center \u{2014} 15%\nA 15% contour.".to_string())
        );
        // A graded qualifier names its level and label.
        assert_eq!(
            format_description_core("Outlooks", "SIGN", "Band.", true, 2, "SIGN"),
            Some(
                "Outlooks \u{2014} SIGN\nBand.\n\
                 Also conditional intensity group 2: SIGN"
                    .to_string()
            )
        );
        // The ungraded qualifier with no label keeps the historic wording.
        assert_eq!(
            format_description_core("Outlooks", "15%", "Band.", true, 0, ""),
            Some("Outlooks \u{2014} 15%\nBand.\nAlso significant: Significant severe".to_string())
        );
        // A graded qualifier without a label has no note.
        assert_eq!(
            format_description_core("Outlooks", "15%", "Band.", true, 3, ""),
            Some("Outlooks \u{2014} 15%\nBand.\nAlso conditional intensity group 3".to_string())
        );
        // A label alone can carry the heading.
        assert_eq!(
            format_description_core("", "MRGL", "", false, 0, ""),
            Some("MRGL".to_string())
        );
        // Nothing to say yields None, not an empty string.
        assert_eq!(format_description_core("", "", "", false, 0, ""), None);
    }

    #[test]
    fn hit_labels_prefer_report_text_and_pair_the_rest() {
        assert_eq!(
            marker_hit_label_core("Storm reports", "Hail 1.00 in", "report"),
            "Hail 1.00 in"
        );
        assert_eq!(
            marker_hit_label_core("Surface observations", "KAAA", "observation"),
            "Surface observations \u{2014} KAAA"
        );
        assert_eq!(marker_hit_label_core("", "Item", ""), "Item");
        assert_eq!(marker_hit_label_core("Layer", "", ""), "Layer");
        assert_eq!(marker_hit_label_core("", "", ""), "Map marker");
        // The Python property strips both sides before pairing.
        assert_eq!(
            marker_hit_label_core("  Layer  ", "  Item  ", "report"),
            "Item"
        );
        assert_eq!(
            marker_hit_label_core("Layer", "   ", "observation"),
            "Layer",
            "a blank item leaves the product title"
        );
    }

    #[test]
    fn coordinate_labels_need_a_town_name() {
        assert!(needs_town_name_core(None, None));
        assert!(needs_town_name_core(Some(""), None));
        assert!(needs_town_name_core(Some("   "), None));
        // Hemisphere pairs, in every separator spelling.
        assert!(needs_town_name_core(
            Some("HRRR 41.53N 88.39W"),
            Some("HRRR")
        ));
        assert!(needs_town_name_core(Some("41.53N 88.39W"), None));
        assert!(needs_town_name_core(Some("41.53N/88.39W"), None));
        assert!(needs_town_name_core(Some("41.53N, 88.39W"), None));
        assert!(needs_town_name_core(Some("41.53N  88.39W"), None));
        assert!(
            needs_town_name_core(Some("41.53n 88.39w"), None),
            "case-insensitive"
        );
        assert!(needs_town_name_core(Some("41.53N\t,\t88.39W"), None));
        // A trailing word character breaks the [EW] boundary.
        assert!(!needs_town_name_core(Some("41.53N 88.39West"), None));
        // Tabs alone cannot bridge raw regex text; once whitespace is
        // collapsed by needs_town_name, the pair becomes space-separated.
        assert!(!coordinate_label_matches("41.53N\t\t88.39W"));
        assert!(needs_town_name_core(Some("41.53N\t\t88.39W"), None));
        // A tab inside the comma/slash run breaks the raw contiguous class,
        // while collapsed whitespace turns it into spaces.
        assert!(!coordinate_label_matches("41.53N\t,\t/88.39W"));
        assert!(needs_town_name_core(Some("41.53N\t,\t/88.39W"), None));
        // A three-digit leading number blocks the restart at its own digits...
        assert!(!needs_town_name_core(Some("123N 45.6W"), None));
        // ...but the fraction after a dot restarts the pattern.
        assert!(needs_town_name_core(Some("141.53N 88.39W"), None));
        assert!(needs_town_name_core(Some("X41.53N 88.39W"), None));
        // Words after the pair keep the match.
        assert!(needs_town_name_core(Some("41.53N 88.39W extra"), None));
    }

    #[test]
    fn comma_pairs_need_a_town_name() {
        assert!(needs_town_name_core(Some("41.53, -88.39"), None));
        assert!(needs_town_name_core(Some("41.53,-88.39"), None));
        assert!(needs_town_name_core(Some("-41.53,-88.39"), None));
        assert!(needs_town_name_core(Some("+41.53, +88.39"), None));
        assert!(needs_town_name_core(
            Some("HRRR 41.53, -88.39"),
            Some("HRRR")
        ));
        assert!(needs_town_name_core(Some("14.5995, 120.9842"), None));
        // No comma, no pair.
        assert!(!needs_town_name_core(Some("Plattville, Illinois"), None));
        assert!(!needs_town_name_core(Some("41.53 -88.39"), None));
        // The longitude digits cap at three, but the fraction still parses.
        assert!(needs_town_name_core(Some("41.53, 1234.5"), None));
    }

    #[test]
    fn generic_model_labels_need_a_town_name() {
        assert!(needs_town_name_core(Some("HRRR"), Some("HRRR")));
        assert!(
            needs_town_name_core(Some("hrrr"), Some("HRRR")),
            "casefolded"
        );
        assert!(needs_town_name_core(Some("HRRRpt"), Some("HRRR")));
        assert!(needs_town_name_core(Some("HRRR point"), Some("HRRR")));
        assert!(needs_town_name_core(Some("HRRR Point"), Some("hrrr")));
        assert!(
            needs_town_name_core(Some(" hrrr  point "), Some("HRRR")),
            "normalized"
        );
        assert!(
            !needs_town_name_core(Some("HRRR"), Some("")),
            "an empty model decides nothing"
        );
        assert!(!needs_town_name_core(Some("HRRR"), None));
        assert!(!needs_town_name_core(
            Some("Plattville, Illinois"),
            Some("HRRR")
        ));
    }

    #[test]
    fn address_fields_promote_settlements_with_regions() {
        assert_eq!(
            from_address_fields_core(
                Some("Madison"),
                None,
                None,
                None,
                None,
                Some("Wisconsin"),
                None,
                None,
            ),
            "Madison, Wisconsin"
        );
        assert_eq!(
            from_address_fields_core(
                None,
                None,
                Some("Bruce"),
                None,
                None,
                Some("Wisconsin"),
                None,
                Some("United States"),
            ),
            "Bruce, Wisconsin"
        );
        assert_eq!(
            from_address_fields_core(
                Some("Singapore"),
                None,
                None,
                None,
                None,
                None,
                None,
                Some("Singapore"),
            ),
            "Singapore",
            "a region repeating the settlement is dropped"
        );
        // No settlement: counties, suburbs, and neighborhoods stay put.
        assert_eq!(
            from_address_fields_core(
                None,
                None,
                None,
                None,
                None,
                Some("Illinois"),
                None,
                Some("United States"),
            ),
            ""
        );
        // Settlements are tried in the documented order and stripped.
        assert_eq!(
            from_address_fields_core(
                Some("  "),
                None,
                Some(" Bruce "),
                None,
                None,
                Some("Wisconsin"),
                None,
                None,
            ),
            "Bruce, Wisconsin"
        );
        // A whitespace-only state is truthy before stripping, then empty.
        assert_eq!(
            from_address_fields_core(
                Some("Bruce"),
                None,
                None,
                None,
                None,
                Some("   "),
                None,
                Some("Wisconsin"),
            ),
            "Bruce"
        );
        // The region falls back through state, region, then country.
        assert_eq!(
            from_address_fields_core(
                Some("Cerro"),
                None,
                None,
                None,
                None,
                None,
                Some("Guanajuato"),
                Some("Mexico"),
            ),
            "Cerro, Guanajuato"
        );
    }

    #[test]
    fn us_addresses_accept_codes_then_names() {
        assert!(is_us_address_core(Some("us"), None));
        assert!(
            is_us_address_core(Some(" US "), Some("Canada")),
            "the code decides"
        );
        assert!(!is_us_address_core(Some("ca"), Some("United States")));
        assert!(is_us_address_core(None, Some("United States of America")));
        assert!(is_us_address_core(None, Some("U.S.A.")));
        assert!(is_us_address_core(None, Some("  usa  ")));
        assert!(!is_us_address_core(None, Some("Canada")));
        assert!(is_us_address_core(None, None), "unspecified is allowed");
        assert!(
            is_us_address_core(None, Some("   ")),
            "an empty name is unspecified"
        );
    }

    #[test]
    fn record_fields_parse_legacy_and_area_aware_rows() {
        let legacy = parse_record_fields_core(&[
            "Legacy Town, State".to_string(),
            "40.0".to_string(),
            "-100.0".to_string(),
            "place".to_string(),
            "0000000".to_string(),
        ])
        .expect("legacy five-column row");
        assert_eq!(
            legacy,
            (
                "Legacy Town, State".to_string(),
                40.0,
                -100.0,
                "place".to_string(),
                0.0
            )
        );
        let area = parse_record_fields_core(&[
            "Current Town, State".to_string(),
            "40.0".to_string(),
            "-100.0".to_string(),
            "subdivision".to_string(),
            "0000000".to_string(),
            "25.125".to_string(),
        ])
        .expect("six-column row");
        assert_eq!(
            area,
            (
                "Current Town, State".to_string(),
                40.0,
                -100.0,
                "subdivision".to_string(),
                25.125
            )
        );
        // Python's float() tolerates surrounding whitespace.
        let spaced = parse_record_fields_core(&[
            "Town".to_string(),
            " 40.5 ".to_string(),
            " -100.5 ".to_string(),
            "place".to_string(),
            "0".to_string(),
            " 26.5 ".to_string(),
        ])
        .expect("whitespace-tolerant floats");
        assert_eq!(spaced.1, 40.5);
        assert_eq!(spaced.2, -100.5);
        assert_eq!(spaced.4, 26.5);
    }

    #[test]
    fn record_fields_reject_unusable_rows() {
        assert!(parse_record_fields_core(&[]).is_none());
        assert!(
            parse_record_fields_core(&[
                "Town".to_string(),
                "40.0".to_string(),
                "-100.0".to_string(),
                "place".to_string(),
            ])
            .is_none(),
            "four fields are too few"
        );
        assert!(
            parse_record_fields_core(&[
                "".to_string(),
                "40.0".to_string(),
                "-100.0".to_string(),
                "place".to_string(),
                "0".to_string(),
            ])
            .is_none(),
            "an empty label is unusable"
        );
        assert!(
            parse_record_fields_core(&[
                "Town".to_string(),
                "40.0".to_string(),
                "-100.0".to_string(),
                "county".to_string(),
                "0".to_string(),
            ])
            .is_none(),
            "only place/subdivision kinds load"
        );
        assert!(
            parse_record_fields_core(&[
                "Town".to_string(),
                "north".to_string(),
                "-100.0".to_string(),
                "place".to_string(),
                "0".to_string(),
            ])
            .is_none(),
            "the latitude must parse"
        );
        assert!(
            parse_record_fields_core(&[
                "Town".to_string(),
                "40.0".to_string(),
                "nan".to_string(),
                "place".to_string(),
                "0".to_string(),
            ])
            .is_none(),
            "the longitude must be finite"
        );
        assert!(
            parse_record_fields_core(&[
                "Town".to_string(),
                "40.0".to_string(),
                "-100.0".to_string(),
                "place".to_string(),
                "0".to_string(),
                "-1.0".to_string(),
            ])
            .is_none(),
            "a negative land area is unusable"
        );
        assert!(
            parse_record_fields_core(&[
                "Town".to_string(),
                "40.0".to_string(),
                "-100.0".to_string(),
                "place".to_string(),
                "0".to_string(),
                "inf".to_string(),
            ])
            .is_none(),
            "a non-finite land area is unusable"
        );
        // A whitespace-only area column is truthy in Python and fails float().
        assert!(parse_record_fields_core(&[
            "Town".to_string(),
            "40.0".to_string(),
            "-100.0".to_string(),
            "place".to_string(),
            "0".to_string(),
            " ".to_string(),
        ])
        .is_none());
    }

    #[test]
    fn area_proxy_radius_scales_a_circular_footprint() {
        assert_eq!(area_proxy_radius_km_core(None), 0.0);
        assert_eq!(area_proxy_radius_km_core(Some(0.0)), 0.0);
        assert_eq!(area_proxy_radius_km_core(Some(-12.0)), 0.0);
        assert_eq!(area_proxy_radius_km_core(Some(f64::NAN)), 0.0);
        assert_eq!(area_proxy_radius_km_core(Some(f64::INFINITY)), 0.0);
        let radius = area_proxy_radius_km_core(Some(153.074));
        let expected =
            (153.074 / std::f64::consts::PI).sqrt() * KM_PER_MILE * AREA_PROXY_RADIUS_SCALE;
        assert_eq!(radius, expected);
        assert!(radius > 0.0);
    }

    #[test]
    fn polygon_containment_covers_inside_outside_and_boundary() {
        // A square ring stored as (lon, lat) pairs.
        let square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)];
        assert!(point_in_polygon_core(5.0, 1.0, &square));
        assert!(point_in_polygon_core(5.0, 9.0, &square));
        assert!(!point_in_polygon_core(15.0, 5.0, &square));
        assert!(!point_in_polygon_core(-1.0, 5.0, &square));
        // The boundary counts as inside.
        assert!(point_in_polygon_core(0.0, 5.0, &square));
        assert!(point_in_polygon_core(5.0, 0.0, &square));
        // A degenerate one-point ring covers only itself.
        let point = [(5.0, 5.0)];
        assert!(point_in_polygon_core(5.0, 5.0, &point));
        assert!(!point_in_polygon_core(6.0, 6.0, &point));
        // An empty ring decides nothing.
        assert!(!point_in_polygon_core(5.0, 5.0, &[]));
    }

    #[test]
    fn segment_tolerance_admits_sub_pixel_offsets() {
        // cross = 1e-8 against scale 10 stays within 1e-8 * 10.
        assert!(point_on_segment_core(5.0, 1.0e-9, 0.0, 0.0, 10.0, 0.0));
        // 2e-8 exceeds the tolerance for this segment's scale.
        assert!(!point_on_segment_core(5.0, 2.0e-8, 0.0, 0.0, 10.0, 0.0));
        // The box check bounds the point even when the cross product passes.
        assert!(!point_on_segment_core(15.0, 0.0, 0.0, 0.0, 10.0, 0.0));
        // A vertical segment measures the offset across its own span.
        assert!(point_on_segment_core(1.0e-9, 5.0, 0.0, 0.0, 0.0, 10.0));
        assert!(!point_on_segment_core(2.0e-8, 5.0, 0.0, 0.0, 0.0, 10.0));
    }

    #[test]
    fn polygon_parity_flips_per_crossing() {
        // A triangle ring `(lon, lat)`: `(0, 0) -> (10, 10) -> (20, 0)`.
        let ring = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0)];
        assert!(
            point_in_polygon_core(5.0, 10.0, &ring),
            "inside the triangle"
        );
        assert!(
            !point_in_polygon_core(12.0, 10.0, &ring),
            "above the triangle"
        );
        assert!(
            !point_in_polygon_core(5.0, 30.0, &ring),
            "east of the triangle"
        );
        // The closing edge wrap-around flips parity too.
        assert!(point_in_polygon_core(0.5, 10.0, &ring));
    }
}

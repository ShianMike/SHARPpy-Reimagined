//! Map geography presentation: boundaries, label declutter, scale, contrast.
//!
//! Rust mirror of `sharpmod.maps.map_geography`: the boundary paint
//! hierarchy and stroke widths, the adaptive place-label density tiers, the
//! importance-ordered declutter pass with its deterministic viewport
//! round-robin, the projection-aware scale bar arithmetic, and the WCAG
//! luminance/contrast helpers.  Everything here is UI-free and total.

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyTuple};
use std::collections::{BTreeSet, HashMap};

const BOUNDARY_ZORDER: [&str; 4] = ["states", "countries", "lakes", "coastline"];
const LABEL_DENSITY_LEVELS: [&str; 4] = ["off", "sparse", "standard", "dense"];
const LABEL_SPAN_STANDARD: f64 = 30.0;
const LABEL_SPAN_DENSE: f64 = 12.0;
const LABEL_MIN_SEPARATION_PX: f64 = 48.0;
const LABEL_MAX_COUNT: i64 = 40;
const SCALE_BAR_STEPS_KM: [f64; 10] = [
    10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0,
];
const SCALE_BAR_TARGET_PX: f64 = 120.0;
const KM_PER_MILE: f64 = 1.609344;

/// Bounds for a float that converts to i64 without overflow.
const I64_SAFE_MIN: f64 = -9.2e18;
const I64_SAFE_MAX: f64 = 9.2e18;

/// One `(importance, x, y, label)` label candidate.
#[derive(Clone, Debug)]
struct Candidate {
    importance: i64,
    x: f64,
    y: f64,
    label: String,
}

/// CPython's two-argument `max` keeps NaN when it comes first and the other
/// operand otherwise; this reproduces that ordering.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn parse_integer(value: &Bound<'_, PyAny>) -> Option<i64> {
    if let Ok(small) = value.extract::<i64>() {
        return Some(small);
    }
    let wide = value.extract::<f64>().ok()?;
    if !wide.is_finite() || !(I64_SAFE_MIN..=I64_SAFE_MAX).contains(&wide) {
        return None;
    }
    Some(wide.trunc() as i64)
}

fn boundary_draw_order_core(names: &[String]) -> Vec<String> {
    let mut ordered = Vec::with_capacity(names.len());
    for known in BOUNDARY_ZORDER {
        if names.iter().any(|name| name == known) {
            ordered.push(known.to_string());
        }
    }
    for name in names {
        if !BOUNDARY_ZORDER.contains(&name.as_str()) {
            ordered.push(name.clone());
        }
    }
    ordered
}

fn boundary_base_width(family: &str) -> f64 {
    match family {
        "states" => 1.0,
        "countries" => 1.2,
        "lakes" => 1.1,
        "coastline" => 1.4,
        _ => 1.0,
    }
}

fn boundary_width_core(base: f64, scale: f64) -> f64 {
    let clamped = scale.clamp(0.5, 2.0);
    (base * clamped * 1000.0).round_ties_even() / 1000.0
}

/// The caller has already applied the `value or "standard"` truthiness rule;
/// this works on the resulting text.
fn normalise_label_density_core(text: &str) -> String {
    let cleaned = text.trim().to_lowercase();
    if LABEL_DENSITY_LEVELS.contains(&cleaned.as_str()) {
        cleaned
    } else {
        "standard".to_string()
    }
}

fn normalise_density(value: &Bound<'_, PyAny>) -> PyResult<String> {
    if !value.is_truthy()? {
        return Ok("standard".to_string());
    }
    Ok(normalise_label_density_core(value.str()?.to_str()?))
}

fn labels_visible_core(density: &str, lon_span: f64) -> (bool, bool, bool) {
    if density == "off" {
        return (false, false, false);
    }
    let span = lon_span.abs();
    let span = if span.is_finite() {
        span
    } else {
        f64::INFINITY
    };
    if density == "sparse" {
        return (true, false, false);
    }
    if density == "dense" {
        return (true, span <= LABEL_SPAN_STANDARD, span <= LABEL_SPAN_DENSE);
    }
    (true, span <= LABEL_SPAN_STANDARD, false)
}

/// Centre-out y/x order for callers that cannot describe a viewport.
fn spread_without_viewport(items: &[Candidate]) -> Vec<Candidate> {
    if items.is_empty() {
        return Vec::new();
    }
    let centre_y = items.iter().map(|item| item.y).sum::<f64>() / items.len() as f64;
    let mut sorted: Vec<&Candidate> = items.iter().collect();
    sorted.sort_by(|a, b| {
        (a.y - centre_y)
            .abs()
            .total_cmp(&(b.y - centre_y).abs())
            .then(a.x.total_cmp(&b.x))
            .then(a.y.total_cmp(&b.y))
            .then(a.label.cmp(&b.label))
    });
    sorted.into_iter().cloned().collect()
}

fn cell_index(value: f64, cell_size: f64, cells: i64) -> i64 {
    let position = (value / cell_size) as i64;
    if position < 0 {
        return 0;
    }
    position.min(cells - 1)
}

/// Deterministic viewport grid round-robin: one candidate from each occupied
/// screen cell before a second from any cell, checkerboard phase first.
fn spread_with_viewport(
    items: &[Candidate],
    minimum: f64,
    cap: i64,
    viewport: (f64, f64),
) -> Vec<Candidate> {
    let (viewport_w, viewport_h) = viewport;
    let aspect = viewport_w / viewport_h;
    let rounded = (cap.max(1) as f64 * aspect).sqrt().round_ties_even() as i64;
    let mut columns = rounded.max(1);
    let mut rows = (cap.max(1) / columns).max(1);
    columns = columns.min(((viewport_w / (minimum * 2.5).max(1.0)) as i64).max(1));
    rows = rows.min(((viewport_h / (minimum * 1.6).max(1.0)) as i64).max(1));
    while columns.saturating_mul(rows) > cap.max(1) {
        if columns >= rows && columns > 1 {
            columns -= 1;
        } else if rows > 1 {
            rows -= 1;
        } else {
            break;
        }
    }
    let cell_w = viewport_w / columns as f64;
    let cell_h = viewport_h / rows as f64;
    let mut buckets: HashMap<(i64, i64), Vec<Candidate>> = HashMap::new();
    for item in items {
        let col = cell_index(item.x, cell_w, columns);
        let row = cell_index(item.y, cell_h, rows);
        buckets.entry((row, col)).or_default().push(item.clone());
    }
    for bucket in buckets.values_mut() {
        bucket.sort_by(|a, b| {
            a.label
                .cmp(&b.label)
                .then(a.y.total_cmp(&b.y))
                .then(a.x.total_cmp(&b.x))
        });
    }
    let mut cells: Vec<(i64, i64)> = buckets.keys().copied().collect();
    cells.sort_by(|&(row, col), &(other_row, other_col)| {
        ((row + col) % 2)
            .cmp(&((other_row + other_col) % 2))
            .then(
                (row as f64 - (rows - 1) as f64 / 2.0)
                    .abs()
                    .total_cmp(&(other_row as f64 - (rows - 1) as f64 / 2.0).abs()),
            )
            .then(
                (col as f64 - (columns - 1) as f64 / 2.0)
                    .abs()
                    .total_cmp(&(other_col as f64 - (columns - 1) as f64 / 2.0).abs()),
            )
            .then(row.cmp(&other_row))
            .then(col.cmp(&other_col))
    });
    let mut spread: Vec<Candidate> = Vec::with_capacity(items.len());
    let mut depth = 0usize;
    loop {
        let mut added = false;
        for &cell in &cells {
            let bucket = &buckets[&cell];
            if depth < bucket.len() {
                spread.push(bucket[depth].clone());
                added = true;
            }
        }
        if !added {
            break;
        }
        depth += 1;
    }
    spread
}

#[allow(clippy::too_many_arguments)]
fn declutter_core(
    valid: Vec<Candidate>,
    protected: &[(f64, f64)],
    protected_rects: &[(f64, f64, f64, f64)],
    minimum: f64,
    cap: i64,
    widths: &HashMap<String, f64>,
    always_keep: Option<i64>,
    viewport: Option<(f64, f64)>,
) -> Vec<(i64, f64, f64, String)> {
    if cap <= 0 {
        return Vec::new();
    }
    let tiers: Vec<i64> = valid
        .iter()
        .map(|candidate| candidate.importance)
        .collect::<BTreeSet<i64>>()
        .into_iter()
        .collect();
    let mut ordered: Vec<Candidate> = Vec::with_capacity(valid.len());
    for tier in tiers {
        let tier_items: Vec<Candidate> = valid
            .iter()
            .filter(|candidate| candidate.importance == tier)
            .cloned()
            .collect();
        if tier == 1 {
            // Curated population order is the continental tie-break.
            ordered.extend(tier_items);
            continue;
        }
        let spread = match viewport {
            Some(size) => spread_with_viewport(&tier_items, minimum, cap, size),
            None => spread_without_viewport(&tier_items),
        };
        ordered.extend(spread);
    }

    let mut kept: Vec<(i64, f64, f64, String)> = Vec::new();
    let mut placed: Vec<(f64, f64)> = protected.to_vec();
    let mut reserved: Vec<(f64, f64, f64, f64)> = protected_rects.to_vec();
    for candidate in &ordered {
        let Candidate {
            importance,
            x,
            y,
            label,
        } = candidate;
        if !x.is_finite() || !y.is_finite() {
            continue;
        }
        let force_keep = always_keep == Some(*importance);
        if !force_keep
            && placed
                .iter()
                .any(|&(px, py)| (x - px).hypot(y - py) < minimum)
        {
            continue;
        }
        let stored = widths.get(label.as_str()).copied().unwrap_or(0.0);
        let extent = if stored.is_finite() && stored >= 0.0 {
            stored
        } else {
            0.0
        };
        let mut draw_x = x + 5.0;
        if let Some((viewport_w, _)) = viewport {
            draw_x = draw_x.min((viewport_w - extent - 4.0).max(0.0));
        }
        let candidate_rect = (draw_x, y - 13.0, extent + 8.0, 14.0);
        let blocked = reserved.iter().any(|&(rx, ry, rw, rh)| {
            candidate_rect.0 < rx + rw + 4.0
                && candidate_rect.0 + candidate_rect.2 + 4.0 > rx
                && candidate_rect.1 < ry + rh + 2.0
                && candidate_rect.1 + candidate_rect.3 + 2.0 > ry
        });
        if blocked && !force_keep {
            continue;
        }
        kept.push((*importance, *x, *y, label.clone()));
        placed.push((*x, *y));
        reserved.push(candidate_rect);
        if kept.len() >= cap as usize {
            break;
        }
    }
    kept
}

fn collect_candidates(candidates: &Bound<'_, PyAny>) -> PyResult<Vec<Candidate>> {
    let mut valid: Vec<Candidate> = Vec::new();
    if !candidates.is_truthy()? {
        return Ok(valid);
    }
    for entry in candidates.try_iter()? {
        let entry = entry?;
        let fields: Vec<Bound<'_, PyAny>> = entry.try_iter()?.collect::<PyResult<Vec<_>>>()?;
        if fields.len() != 4 {
            continue;
        }
        let label = fields[3].str()?.to_str()?.to_owned();
        if label.trim().is_empty() {
            continue;
        }
        let Some(importance) = parse_integer(&fields[0]) else {
            continue;
        };
        let x = fields[1].extract::<f64>().unwrap_or(f64::NAN);
        let y = fields[2].extract::<f64>().unwrap_or(f64::NAN);
        valid.push(Candidate {
            importance,
            x,
            y,
            label,
        });
    }
    Ok(valid)
}

fn collect_widths(mapping: &Bound<'_, PyAny>, widths: &mut HashMap<String, f64>) -> PyResult<()> {
    if let Ok(dict) = mapping.cast::<PyDict>() {
        for (key, value) in dict.iter() {
            if let (Ok(label), Ok(width)) = (key.extract::<String>(), value.extract::<f64>()) {
                widths.insert(label, width);
            }
        }
        return Ok(());
    }
    for entry in mapping.try_iter()? {
        let fields: Vec<Bound<'_, PyAny>> = entry?.try_iter()?.collect::<PyResult<Vec<_>>>()?;
        if fields.len() != 2 {
            continue;
        }
        if let (Ok(label), Ok(width)) = (fields[0].extract::<String>(), fields[1].extract::<f64>())
        {
            widths.insert(label, width);
        }
    }
    Ok(())
}

fn parse_viewport(viewport_size: Option<&Bound<'_, PyAny>>) -> PyResult<Option<(f64, f64)>> {
    let Some(value) = viewport_size else {
        return Ok(None);
    };
    let (Some(first), Some(second)) = (value.get_item(0).ok(), value.get_item(1).ok()) else {
        return Ok(None);
    };
    let (Ok(w), Ok(h)) = (first.extract::<f64>(), second.extract::<f64>()) else {
        return Ok(None);
    };
    let viewport_w = py_max(1.0, w);
    let viewport_h = py_max(1.0, h);
    if viewport_w.is_finite() && viewport_h.is_finite() {
        Ok(Some((viewport_w, viewport_h)))
    } else {
        Ok(None)
    }
}

fn km_per_pixel_core(span: f64, width: f64, latitude: f64, cos_adjusted: bool) -> f64 {
    if !span.is_finite() || !width.is_finite() || width <= 0.0 {
        return f64::NAN;
    }
    if !latitude.is_finite() {
        return f64::NAN;
    }
    let clamped = latitude.clamp(-89.0, 89.0);
    let mut squeeze = if cos_adjusted {
        (clamped * (std::f64::consts::PI / 180.0)).cos()
    } else {
        1.0
    };
    squeeze = squeeze.max(0.05);
    span / width * squeeze * 111.195
}

fn pick_scale_length_core(scale: f64, target: f64, imperial: bool) -> (f64, f64, String) {
    if !scale.is_finite() || scale <= 0.0 || !target.is_finite() || target <= 0.0 {
        return (0.0, 0.0, if imperial { "mi" } else { "km" }.to_string());
    }
    let unit = if imperial { "mi" } else { "km" };
    let mut best = 0.0f64;
    let mut best_gap = f64::INFINITY;
    for &step in SCALE_BAR_STEPS_KM.iter() {
        let length = if imperial { step / KM_PER_MILE } else { step };
        let gap = (length / scale - target).abs();
        if gap < best_gap {
            best = length;
            best_gap = gap;
        }
    }
    (best, best / scale, unit.to_string())
}

fn format_scale_length_core(value: f64, unit: &str) -> String {
    if !value.is_finite() || value <= 0.0 {
        return format!("Unknown {unit}");
    }
    let text = if value >= 100.0 {
        format!("{value:.0}")
    } else {
        format!("{value:.1}")
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_string()
    };
    format!("{text} {unit}")
}

fn luminance_core(text: &str) -> f64 {
    let text = text.trim().trim_start_matches('#');
    if text.len() != 6 || !text.is_ascii() {
        return f64::NAN;
    }
    let mut channels = [0.0f64; 3];
    for (slot, offset) in channels.iter_mut().zip([0usize, 2, 4]) {
        let Ok(byte) = u8::from_str_radix(&text[offset..offset + 2], 16) else {
            return f64::NAN;
        };
        *slot = byte as f64 / 255.0;
    }
    let linear = |channel: f64| {
        if channel <= 0.03928 {
            channel / 12.92
        } else {
            ((channel + 0.055) / 1.055).powf(2.4)
        }
    };
    0.2126 * linear(channels[0]) + 0.7152 * linear(channels[1]) + 0.0722 * linear(channels[2])
}

fn luminance_of(hex_colour: &Bound<'_, PyAny>) -> PyResult<f64> {
    if !hex_colour.is_truthy()? {
        return Ok(luminance_core(""));
    }
    Ok(luminance_core(hex_colour.str()?.to_str()?))
}

fn contrast_core(first: f64, second: f64) -> f64 {
    if !first.is_finite() || !second.is_finite() {
        return f64::NAN;
    }
    let lighter = first.max(second);
    let darker = first.min(second);
    (lighter + 0.05) / (darker + 0.05)
}

/// Return known boundary families dimmest-first, unknowns last in place.
#[pyfunction]
fn geo_boundary_draw_order<'py>(
    py: Python<'py>,
    names: &Bound<'_, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    let mut items: Vec<String> = Vec::new();
    for name in names.try_iter()? {
        items.push(name?.str()?.to_str()?.to_owned());
    }
    PyTuple::new(py, boundary_draw_order_core(&items))
}

/// Return the stroke width for one boundary family.
#[pyfunction]
#[pyo3(signature = (family, density_scale = None))]
fn geo_boundary_width(family: &str, density_scale: Option<&Bound<'_, PyAny>>) -> f64 {
    let scale = density_scale
        .and_then(|value| value.extract::<f64>().ok())
        .filter(|scale| scale.is_finite() && *scale > 0.0)
        .unwrap_or(1.0);
    boundary_width_core(boundary_base_width(family), scale)
}

/// Return a valid label-density key, defaulting to `standard`.
#[pyfunction]
fn geo_normalise_label_density(value: &Bound<'_, PyAny>) -> PyResult<String> {
    normalise_density(value)
}

/// Return `(major, towns, smalls)` visibility for a view span.
#[pyfunction]
fn geo_labels_visible_for_span(
    density: &Bound<'_, PyAny>,
    lon_span: &Bound<'_, PyAny>,
) -> PyResult<(bool, bool, bool)> {
    let density = normalise_density(density)?;
    let span = lon_span.extract::<f64>().unwrap_or(f64::INFINITY);
    Ok(labels_visible_core(&density, span))
}

/// Select non-overlapping labels, most important first.
///
/// Candidates are `(importance, x, y, label)` with lower importance meaning
/// more important.  Placement honours the minimum pixel separation, the
/// protected marker positions and rectangles, optional drawn-label pixel
/// extents, and an optional exempt tier, stopping at the cap.
#[pyfunction]
#[pyo3(signature = (candidates, protected = None, protected_rects = None, min_separation_px = None, limit = None, label_widths = None, always_keep_importance = None, viewport_size = None))]
#[allow(clippy::too_many_arguments)]
fn geo_declutter_labels(
    candidates: &Bound<'_, PyAny>,
    protected: Option<&Bound<'_, PyAny>>,
    protected_rects: Option<&Bound<'_, PyAny>>,
    min_separation_px: Option<&Bound<'_, PyAny>>,
    limit: Option<&Bound<'_, PyAny>>,
    label_widths: Option<&Bound<'_, PyAny>>,
    always_keep_importance: Option<i64>,
    viewport_size: Option<&Bound<'_, PyAny>>,
) -> PyResult<Vec<(i64, f64, f64, String)>> {
    let minimum = min_separation_px
        .and_then(|value| value.extract::<f64>().ok())
        .filter(|value| value.is_finite() && *value >= 0.0)
        .unwrap_or(LABEL_MIN_SEPARATION_PX);
    let cap = limit
        .and_then(parse_integer)
        .unwrap_or(LABEL_MAX_COUNT)
        .max(0);
    let mut widths: HashMap<String, f64> = HashMap::new();
    if let Some(mapping) = label_widths {
        if mapping.is_truthy()? {
            collect_widths(mapping, &mut widths)?;
        }
    }
    let valid = collect_candidates(candidates)?;
    let mut placed: Vec<(f64, f64)> = Vec::new();
    if let Some(entries) = protected {
        if entries.is_truthy()? {
            for entry in entries.try_iter()? {
                let fields: Vec<Bound<'_, PyAny>> =
                    entry?.try_iter()?.collect::<PyResult<Vec<_>>>()?;
                if fields.len() != 2 {
                    continue;
                }
                let (Ok(x), Ok(y)) = (fields[0].extract::<f64>(), fields[1].extract::<f64>())
                else {
                    continue;
                };
                if x.is_finite() && y.is_finite() {
                    placed.push((x, y));
                }
            }
        }
    }
    let mut reserved: Vec<(f64, f64, f64, f64)> = Vec::new();
    if let Some(rects) = protected_rects {
        if rects.is_truthy()? {
            for rect in rects.try_iter()? {
                let fields: Vec<Bound<'_, PyAny>> =
                    rect?.try_iter()?.collect::<PyResult<Vec<_>>>()?;
                if fields.len() < 4 {
                    continue;
                }
                let mut values = [0.0f64; 4];
                let mut usable = true;
                for (slot, field) in values.iter_mut().zip(fields.iter().take(4)) {
                    match field.extract::<f64>() {
                        Ok(value) if value.is_finite() => *slot = value,
                        _ => {
                            usable = false;
                            break;
                        }
                    }
                }
                if !usable {
                    continue;
                }
                let (rx, ry, rw, rh) = (values[0], values[1], values[2], values[3]);
                if rw >= 0.0 && rh >= 0.0 {
                    reserved.push((rx, ry, rw, rh));
                }
            }
        }
    }
    let viewport = parse_viewport(viewport_size)?;
    Ok(declutter_core(
        valid,
        &placed,
        &reserved,
        minimum,
        cap,
        &widths,
        always_keep_importance,
        viewport,
    ))
}

/// Return kilometres per screen pixel at one latitude on a flat view.
#[pyfunction]
#[pyo3(signature = (lon_span, widget_width_px, latitude, cos_adjusted = true))]
fn geo_km_per_pixel_at_latitude(
    lon_span: &Bound<'_, PyAny>,
    widget_width_px: &Bound<'_, PyAny>,
    latitude: &Bound<'_, PyAny>,
    cos_adjusted: bool,
) -> f64 {
    let (Ok(span), Ok(width), Ok(lat)) = (
        lon_span.extract::<f64>(),
        widget_width_px.extract::<f64>(),
        latitude.extract::<f64>(),
    ) else {
        return f64::NAN;
    };
    km_per_pixel_core(span.abs(), width, lat, cos_adjusted)
}

/// Return `(length_value, length_px, unit_label)` for the scale bar.
#[pyfunction]
#[pyo3(signature = (km_per_pixel, target_px = None, imperial = false))]
fn geo_pick_scale_length(
    km_per_pixel: &Bound<'_, PyAny>,
    target_px: Option<&Bound<'_, PyAny>>,
    imperial: bool,
) -> (f64, f64, String) {
    // A bad target invalidates the scale too, matching the reference's
    // all-or-nothing coercion.
    let parsed_target = match target_px {
        Some(value) => value.extract::<f64>(),
        None => Ok(SCALE_BAR_TARGET_PX),
    };
    let (scale, target) = match (km_per_pixel.extract::<f64>(), parsed_target) {
        (Ok(scale), Ok(target)) => (scale, target),
        _ => (f64::NAN, SCALE_BAR_TARGET_PX),
    };
    pick_scale_length_core(scale, target, imperial)
}

/// Format a scale-bar length without trailing noise.
#[pyfunction]
fn geo_format_scale_length(length: &Bound<'_, PyAny>, unit: &str) -> String {
    match length.extract::<f64>() {
        Ok(value) => format_scale_length_core(value, unit),
        Err(_) => format!("Unknown {unit}"),
    }
}

/// Return the relative luminance of a `#rrggbb` colour, 0 to 1.
#[pyfunction]
fn geo_luminance(hex_colour: &Bound<'_, PyAny>) -> PyResult<f64> {
    luminance_of(hex_colour)
}

/// Return the WCAG contrast ratio between two `#rrggbb` colours.
#[pyfunction]
fn geo_contrast_ratio(first: &Bound<'_, PyAny>, second: &Bound<'_, PyAny>) -> PyResult<f64> {
    let first_luminance = luminance_of(first)?;
    let second_luminance = luminance_of(second)?;
    Ok(contrast_core(first_luminance, second_luminance))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(geo_boundary_draw_order, module)?)?;
    module.add_function(wrap_pyfunction!(geo_boundary_width, module)?)?;
    module.add_function(wrap_pyfunction!(geo_normalise_label_density, module)?)?;
    module.add_function(wrap_pyfunction!(geo_labels_visible_for_span, module)?)?;
    module.add_function(wrap_pyfunction!(geo_declutter_labels, module)?)?;
    module.add_function(wrap_pyfunction!(geo_km_per_pixel_at_latitude, module)?)?;
    module.add_function(wrap_pyfunction!(geo_pick_scale_length, module)?)?;
    module.add_function(wrap_pyfunction!(geo_format_scale_length, module)?)?;
    module.add_function(wrap_pyfunction!(geo_luminance, module)?)?;
    module.add_function(wrap_pyfunction!(geo_contrast_ratio, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(importance: i64, x: f64, y: f64, label: &str) -> Candidate {
        Candidate {
            importance,
            x,
            y,
            label: label.to_string(),
        }
    }

    fn label_names(rows: &[(i64, f64, f64, String)]) -> Vec<&str> {
        rows.iter().map(|(_, _, _, label)| label.as_str()).collect()
    }

    #[test]
    fn boundary_draw_order_keeps_the_paint_hierarchy() {
        assert_eq!(
            boundary_draw_order_core(&[
                "coastline".to_string(),
                "states".to_string(),
                "countries".to_string(),
                "lakes".to_string(),
            ]),
            vec![
                "states".to_string(),
                "countries".to_string(),
                "lakes".to_string(),
                "coastline".to_string(),
            ]
        );
        assert_eq!(
            boundary_draw_order_core(&["states".to_string(), "future-layer".to_string()]),
            vec!["states".to_string(), "future-layer".to_string()]
        );
        assert_eq!(
            boundary_draw_order_core(&["states".to_string(), "states".to_string()]),
            vec!["states".to_string()]
        );
        assert_eq!(boundary_draw_order_core(&[]), Vec::<String>::new());
    }

    #[test]
    fn boundary_widths_keep_coastline_heaviest() {
        assert_eq!(boundary_base_width("coastline"), 1.4);
        assert!(boundary_base_width("coastline") > boundary_base_width("countries"));
        assert!(boundary_base_width("countries") >= boundary_base_width("lakes"));
        assert!(boundary_base_width("lakes") >= boundary_base_width("states"));
        assert_eq!(boundary_base_width("unknown-family"), 1.0);
        // Density scale clamps to [0.5, 2.0] and rounds to three decimals.
        assert_eq!(boundary_width_core(1.4, 0.25), 0.7);
        assert_eq!(boundary_width_core(1.4, 5.0), 2.8);
        assert_eq!(boundary_width_core(1.2, 1.0), 1.2);
        assert_eq!(boundary_width_core(1.1, 0.44), 0.55);
        assert_eq!(boundary_width_core(1.0, 1.0), 1.0);
    }

    #[test]
    fn label_density_normalises_to_standard_by_default() {
        assert_eq!(normalise_label_density_core("Dense "), "dense");
        assert_eq!(normalise_label_density_core("  OFF "), "off");
        assert_eq!(normalise_label_density_core("bogus"), "standard");
        assert_eq!(normalise_label_density_core("standard"), "standard");
        assert_eq!(normalise_label_density_core("42"), "standard");
    }

    #[test]
    fn label_tiers_appear_as_the_view_closes_in() {
        assert_eq!(labels_visible_core("standard", 60.0), (true, false, false));
        assert_eq!(labels_visible_core("standard", 20.0), (true, true, false));
        assert_eq!(labels_visible_core("standard", 8.0), (true, true, false));
        assert_eq!(labels_visible_core("dense", 20.0), (true, true, false));
        assert_eq!(labels_visible_core("dense", 8.0), (true, true, true));
        assert_eq!(labels_visible_core("sparse", 8.0), (true, false, false));
        assert_eq!(labels_visible_core("off", 8.0), (false, false, false));
        assert_eq!(
            labels_visible_core("dense", f64::INFINITY),
            (true, false, false)
        );
        assert_eq!(labels_visible_core("dense", f64::NAN), (true, false, false));
        assert_eq!(
            labels_visible_core("standard", -20.0),
            (true, true, false),
            "an absolute span ignores the sign"
        );
    }

    #[test]
    fn declutter_keeps_important_labels_and_protects_markers() {
        let candidates = vec![
            candidate(1, 100.0, 100.0, "Town A"),
            candidate(1, 110.0, 105.0, "Town B"),
            candidate(2, 300.0, 300.0, "Town C"),
            candidate(0, 500.0, 100.0, "Selected place"),
        ];
        let kept = declutter_core(
            candidates,
            &[(505.0, 105.0)],
            &[],
            48.0,
            40,
            &HashMap::new(),
            None,
            None,
        );
        assert_eq!(label_names(&kept), ["Town A", "Town C"]);
    }

    #[test]
    fn declutter_is_bounded_and_skips_nonfinite_positions() {
        let mut candidates = vec![candidate(0, f64::NAN, 0.0, "Nowhere")];
        for index in 0..60 {
            candidates.push(candidate(
                1,
                index as f64 * 100.0,
                0.0,
                &format!("Place {index}"),
            ));
        }
        let kept = declutter_core(
            candidates.clone(),
            &[],
            &[],
            48.0,
            40,
            &HashMap::new(),
            None,
            None,
        );
        assert_eq!(kept.len(), 40);
        assert_eq!(kept[0], (1, 0.0, 0.0, "Place 0".to_string()));
        assert!(kept.iter().all(|(_, _, _, label)| !label.trim().is_empty()));
        assert!(
            declutter_core(candidates, &[], &[], 48.0, 0, &HashMap::new(), None, None).is_empty()
        );
    }

    #[test]
    fn declutter_tier_one_keeps_curated_input_order() {
        let candidates: Vec<Candidate> = (0..10)
            .map(|index| candidate(1, index as f64 * 100.0, 0.0, &format!("L{index}")))
            .collect();
        let kept = declutter_core(candidates, &[], &[], 48.0, 3, &HashMap::new(), None, None);
        assert_eq!(label_names(&kept), ["L0", "L1", "L2"]);
    }

    #[test]
    fn declutter_grid_visits_cells_checkerboard_first() {
        let candidates = vec![
            candidate(2, 100.0, 300.0, "A"),
            candidate(2, 500.0, 300.0, "B"),
            candidate(2, 150.0, 450.0, "C"),
            candidate(2, 550.0, 200.0, "D"),
        ];
        let kept = declutter_core(
            candidates,
            &[],
            &[],
            48.0,
            4,
            &HashMap::new(),
            None,
            Some((800.0, 600.0)),
        );
        // A 2x2 grid on the 800x600 viewport: cells (1,1), (0,1), (1,0) in
        // checkerboard order, one candidate per occupied cell per depth.
        assert_eq!(label_names(&kept), ["B", "D", "A", "C"]);
    }

    #[test]
    fn declutter_without_viewport_spreads_from_the_centre() {
        let candidates = vec![
            candidate(2, 10.0, 10.0, "E"),
            candidate(2, 50.0, 500.0, "F"),
            candidate(2, 100.0, -300.0, "G"),
            candidate(2, 150.0, 100.0, "H"),
        ];
        let kept = declutter_core(candidates, &[], &[], 48.0, 4, &HashMap::new(), None, None);
        // centre_y = 77.5, so the y-ordered spread is H, E, G, F.
        assert_eq!(label_names(&kept), ["H", "E", "G", "F"]);
    }

    #[test]
    fn declutter_centre_out_ties_break_on_x_then_drop_close_pairs() {
        let candidates = vec![
            candidate(2, 50.0, 0.0, "Right"),
            candidate(2, 10.0, 0.0, "Left"),
            candidate(2, 30.0, 0.0, "Middle"),
        ];
        let kept = declutter_core(candidates, &[], &[], 48.0, 40, &HashMap::new(), None, None);
        // Equal dy keys order by x (Left, Middle, Right), then only Left
        // survives the 48 px minimum separation.
        assert_eq!(label_names(&kept), ["Left"]);
    }

    #[test]
    fn declutter_force_keeps_the_exempt_tier() {
        let candidates = vec![
            candidate(0, 500.0, 100.0, "Selected place"),
            candidate(1, 100.0, 100.0, "Town A"),
        ];
        let kept = declutter_core(
            candidates.clone(),
            &[(505.0, 105.0)],
            &[],
            48.0,
            40,
            &HashMap::new(),
            None,
            None,
        );
        assert_eq!(label_names(&kept), ["Town A"]);
        // The exempt tier bypasses both the point separation and the
        // protected rectangles; "Town A" is now blocked by the rectangle.
        let kept = declutter_core(
            candidates,
            &[(505.0, 105.0)],
            &[(110.0, 92.0, 400.0, 16.0)],
            48.0,
            40,
            &HashMap::new(),
            Some(0),
            None,
        );
        assert_eq!(label_names(&kept), ["Selected place"]);
    }

    #[test]
    fn declutter_label_extents_block_eastern_neighbours_in_viewport() {
        let mut widths = HashMap::new();
        widths.insert("A".to_string(), 300.0);
        let candidates = vec![
            candidate(2, 10.0, 50.0, "A"),
            candidate(2, 100.0, 50.0, "B"),
        ];
        let kept = declutter_core(
            candidates.clone(),
            &[],
            &[],
            48.0,
            40,
            &widths,
            None,
            Some((400.0, 300.0)),
        );
        assert_eq!(label_names(&kept), ["A"], "B starts inside A's extent");
        let kept = declutter_core(
            candidates,
            &[],
            &[],
            48.0,
            40,
            &HashMap::new(),
            None,
            Some((400.0, 300.0)),
        );
        assert_eq!(label_names(&kept), ["A", "B"]);
    }

    #[test]
    fn declutter_extents_do_not_block_across_draw_bands() {
        let mut widths = HashMap::new();
        widths.insert("A".to_string(), 300.0);
        let candidates = vec![
            candidate(2, 10.0, 50.0, "A"),
            candidate(2, 100.0, 100.0, "B"),
        ];
        let kept = declutter_core(candidates, &[], &[], 48.0, 40, &widths, None, None);
        assert_eq!(label_names(&kept), ["A", "B"]);
    }

    #[test]
    fn declutter_protected_rects_block_flat_overlaps() {
        let candidates = vec![
            candidate(2, 120.0, 110.0, "Inside"),
            candidate(2, 60.0, 110.0, "Clear"),
        ];
        let kept = declutter_core(
            candidates,
            &[],
            &[(100.0, 100.0, 50.0, 20.0)],
            48.0,
            40,
            &HashMap::new(),
            None,
            None,
        );
        assert_eq!(label_names(&kept), ["Clear"]);
    }

    #[test]
    fn grid_cells_clamp_like_integer_truncation() {
        assert_eq!(cell_index(10.0, 133.33, 3), 0);
        assert_eq!(cell_index(300.0, 133.33, 3), 2);
        assert_eq!(cell_index(9999.0, 133.33, 3), 2);
        assert_eq!(cell_index(-40.5, 133.33, 3), 0);
        assert_eq!(cell_index(f64::NAN, 133.33, 3), 0);
    }

    #[test]
    fn flat_scale_uses_cosine_squeeze_not_constant_distance() {
        let equatorial = km_per_pixel_core(59.0, 800.0, 0.0, true);
        let northern = km_per_pixel_core(59.0, 800.0, 60.0, true);
        assert!((equatorial - 8.20).abs() < 0.05);
        assert!((northern - equatorial / 2.0).abs() < 1e-9);
        assert!(km_per_pixel_core(59.0, 0.0, 35.0, true).is_nan());
        assert!(km_per_pixel_core(f64::NAN, 800.0, 0.0, true).is_nan());
        assert!(km_per_pixel_core(59.0, 800.0, f64::INFINITY, true).is_nan());
        // The cosine squeeze floors at 0.05 and ignores the sign.
        let edge = km_per_pixel_core(59.0, 800.0, 89.0, true);
        assert!((edge - 59.0 / 800.0 * 0.05 * 111.195).abs() < 1e-9);
        let unadjusted = km_per_pixel_core(59.0, 800.0, -60.0, false);
        assert!((unadjusted - 59.0 / 800.0 * 111.195).abs() < 1e-12);
    }

    #[test]
    fn scale_bar_snaps_to_a_nice_length_near_the_target() {
        let (length, pixels, unit) = pick_scale_length_core(8.2, 120.0, false);
        assert_eq!((length, unit), (1000.0, "km".to_string()));
        assert!((pixels - 1000.0 / 8.2).abs() < 1e-9);
        let (length, pixels, unit) = pick_scale_length_core(8.2, 120.0, true);
        assert_eq!(unit, "mi");
        let expected = 2000.0 / KM_PER_MILE;
        assert!((length - expected).abs() < 1e-9);
        assert!((pixels - length / 8.2).abs() < 1e-12);
        assert_eq!(
            pick_scale_length_core(f64::NAN, 120.0, false),
            (0.0, 0.0, "km".to_string())
        );
        assert_eq!(
            pick_scale_length_core(f64::NAN, 120.0, true),
            (0.0, 0.0, "mi".to_string())
        );
        assert_eq!(pick_scale_length_core(0.0, 120.0, false).0, 0.0);
        assert_eq!(pick_scale_length_core(8.2, 0.0, false).0, 0.0);
    }

    #[test]
    fn scale_lengths_format_without_trailing_noise() {
        assert_eq!(format_scale_length_core(1000.0, "km"), "1000 km");
        assert_eq!(format_scale_length_core(50.0, "km"), "50 km");
        assert_eq!(format_scale_length_core(100.0, "km"), "100 km");
        assert_eq!(format_scale_length_core(2.5, "km"), "2.5 km");
        assert_eq!(format_scale_length_core(1.0, "km"), "1 km");
        assert_eq!(format_scale_length_core(f64::NAN, "km"), "Unknown km");
        assert_eq!(format_scale_length_core(0.0, "mi"), "Unknown mi");
        assert_eq!(format_scale_length_core(-5.0, "km"), "Unknown km");
    }

    #[test]
    fn luminance_matches_the_wcag_channel_curves() {
        assert!((luminance_core("#ffffff") - 1.0).abs() < 1e-12);
        assert!((luminance_core("FFFFFF") - 1.0).abs() < 1e-12);
        assert_eq!(luminance_core("#000000"), 0.0);
        assert_eq!(luminance_core("##000000"), 0.0);
        assert!((luminance_core("#808080") - 0.2158).abs() < 0.001);
        assert!(luminance_core("#fff").is_nan());
        assert!(luminance_core("zzzzzz").is_nan());
        assert!(luminance_core("").is_nan());
    }

    #[test]
    fn contrast_ratios_match_the_wcag_formula() {
        let white = luminance_core("#ffffff");
        let black = luminance_core("#000000");
        assert!((contrast_core(black, white) - 21.0).abs() < 1e-9);
        let grey = luminance_core("#808080");
        assert!((contrast_core(grey, grey) - 1.0).abs() < 1e-12);
        assert!(contrast_core(f64::NAN, black).is_nan());
        assert!(contrast_core(black, f64::NAN).is_nan());
    }
}

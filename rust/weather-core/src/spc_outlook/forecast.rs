//! Build nested Draw Forecast coverage and conditional-intensity overlays.

mod editable;
mod geometry;
#[cfg(test)]
mod tests;

use super::{display_label, feature_style};
use i_overlay::i_shape::int::shape::{IntContour, IntShapes};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

const MAX_POLYGONS: usize = 64;
const MAX_ID_LENGTH: usize = 64;
const RISK_SPACING_KM: f64 = 2.0 * 1.609_344;
const CATEGORIES: [(&str, i64); 6] = [
    ("TSTM", 2),
    ("MRGL", 3),
    ("SLGT", 4),
    ("ENH", 5),
    ("MDT", 6),
    ("HIGH", 8),
];

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ForecastProduct {
    Cat,
    Torn,
    Hail,
    Wind,
}

impl ForecastProduct {
    fn key(self) -> &'static str {
        match self {
            Self::Cat => "cat",
            Self::Torn => "torn",
            Self::Hail => "hail",
            Self::Wind => "wind",
        }
    }

    fn levels(self) -> &'static [u8] {
        match self {
            Self::Cat => &[0, 1, 2, 3, 4, 5],
            Self::Torn => &[2, 5, 10, 15, 30, 45, 60],
            Self::Hail => &[5, 15, 30, 45, 60],
            Self::Wind => &[5, 15, 30, 45, 60, 75, 90],
        }
    }

    fn max_hatch_level(self) -> u8 {
        match self {
            Self::Cat => 0,
            Self::Hail => 2,
            Self::Torn | Self::Wind => 3,
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ForecastPolygon {
    /// A unique ID of 1 to 64 UTF-16 code units without surrounding whitespace or control characters.
    pub id: String,
    pub level: u8,
    pub hatch: bool,
    /// The authored source of an adjusted lower rung.
    pub source_id: Option<String>,
    /// One open longitude/latitude ring in either winding.
    pub points: Vec<[f64; 2]>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ForecastDrawing {
    pub areas: Vec<ForecastArea>,
    /// Authored hatch IDs with some area inside and some outside the probability envelope.
    pub clipped_hatches: Vec<String>,
    /// Authored hatch IDs with no area inside the probability envelope.
    pub hidden_hatches: Vec<String>,
    /// Editable contours for source/rung pairs that still use automatic coverage.
    pub generated_contours: Vec<GeneratedContour>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GeneratedContour {
    pub source_id: String,
    pub level: u8,
    /// An open ring of editing vertices, including contours across the antimeridian.
    pub points: Vec<[f64; 2]>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ForecastArea {
    pub label: String,
    pub description: String,
    pub fill: String,
    pub stroke: String,
    pub rank: i64,
    pub hatch: bool,
    pub hatch_level: u8,
    /// Open longitude/latitude rings, with a counterclockwise exterior and clockwise holes.
    pub rings: Vec<Vec<[f64; 2]>>,
}

/// Build coverage from the current authored polygons, in ascending display rank.
///
/// Automatic envelopes use approximately two miles per rung from the authored
/// coverage or the latest adjusted envelope for that source.
/// Hatch status is measured against the lowest probability envelope before
/// resolving overlaps in favor of the highest visible CIG.
pub fn build_forecast(
    product: ForecastProduct,
    polygons: &[ForecastPolygon],
) -> Result<ForecastDrawing, String> {
    if polygons.len() > MAX_POLYGONS {
        return Err(format!(
            "A forecast can contain at most {MAX_POLYGONS} polygons."
        ));
    }

    let mut ids = HashMap::with_capacity(polygons.len());
    for (index, polygon) in polygons.iter().enumerate() {
        if !valid_id(&polygon.id) {
            return Err(format!(
                "Area {}: ID must be 1 to 64 characters without surrounding whitespace or control characters",
                index + 1
            ));
        }
        if ids.insert(polygon.id.as_str(), polygon).is_some() {
            return Err(format!("Area {}: duplicate area ID", index + 1));
        }
    }

    let levels = product.levels();
    let mut hatches = Vec::new();
    let mut sources = Vec::new();
    let mut adjustment_keys = HashSet::new();
    let mut adjustments = HashMap::new();
    for (index, polygon) in polygons.iter().enumerate() {
        let error = |message| format!("Area {}: {message}", index + 1);
        let ring = geometry::validate_ring(&polygon.points).map_err(error)?;
        let ring = geometry::round_ring(ring);
        if polygon.hatch {
            if !(1..=product.max_hatch_level()).contains(&polygon.level) {
                return Err(error("invalid CIG level for this product"));
            }
            if polygon.source_id.is_some() {
                return Err(error("CIG areas cannot adjust a generated risk"));
            }
            hatches.push((polygon, geometry::world_parts(vec![vec![ring]])));
            continue;
        }

        let rung = levels
            .iter()
            .position(|&level| level == polygon.level)
            .ok_or_else(|| error("invalid risk or probability level for this product"))?;
        if let Some(source_id) = polygon.source_id.as_deref() {
            if !valid_id(source_id) {
                return Err(error(
                    "source ID must be 1 to 64 characters without surrounding whitespace or control characters",
                ));
            }
            let key = (source_id, polygon.level);
            if !adjustment_keys.insert(key) {
                return Err(error("duplicate adjustment for this source and level"));
            }
            if let Some(source) = ids.get(source_id) {
                if source.hatch
                    || source.level <= polygon.level
                    || source
                        .source_id
                        .as_deref()
                        .is_some_and(|id| ids.contains_key(id))
                {
                    return Err(error(
                        "an adjustment needs an authored risk at a higher level",
                    ));
                }
                adjustments.insert(key, ring);
                continue;
            }
        }
        // A removed source leaves an ordinary area at the saved adjustment's rung.
        sources.push((index, polygon.id.as_str(), rung, ring));
    }

    let mut coverage = vec![IntShapes::new(); levels.len()];
    let mut generated_contours = Vec::new();
    for (index, source_id, rung, ring) in sources {
        add_source_coverage(
            &mut coverage[..=rung],
            &levels[..=rung],
            source_id,
            ring,
            &mut adjustments,
            &mut generated_contours,
        )
        .map_err(|message| format!("Area {}: {message}", index + 1))?;
    }
    generated_contours
        .sort_unstable_by(|a, b| (&a.source_id, a.level).cmp(&(&b.source_id, b.level)));
    let coverage: Vec<_> = coverage.into_iter().map(geometry::union).collect();
    let mut drawing = ForecastDrawing {
        areas: Vec::new(),
        clipped_hatches: Vec::new(),
        hidden_hatches: Vec::new(),
        generated_contours,
    };

    let mut hatch_levels = vec![IntShapes::new(); usize::from(product.max_hatch_level())];
    for (polygon, shape) in hatches {
        let (visible, clipped) = geometry::clip_hatch(&shape, &coverage[0]);
        if visible.is_empty() {
            drawing.hidden_hatches.push(polygon.id.clone());
            continue;
        }
        if clipped {
            drawing.clipped_hatches.push(polygon.id.clone());
        }
        hatch_levels[usize::from(polygon.level - 1)].extend(visible);
    }

    for (&level, shapes) in levels.iter().zip(coverage) {
        append_areas(&mut drawing.areas, product, level, false, shapes);
    }
    for (index, shapes) in hatch_levels.iter().enumerate() {
        if !shapes.is_empty() {
            let visible = geometry::visible_hatch(shapes, &hatch_levels[index + 1..]);
            append_areas(
                &mut drawing.areas,
                product,
                (index + 1) as u8,
                true,
                visible,
            );
        }
    }
    Ok(drawing)
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.encode_utf16().count() <= MAX_ID_LENGTH
        && id.trim() == id
        && !id.chars().any(char::is_control)
}

fn add_source_coverage<'a>(
    coverage: &mut [IntShapes],
    levels: &[u8],
    source_id: &'a str,
    ring: IntContour,
    adjustments: &mut HashMap<(&'a str, u8), IntContour>,
    generated: &mut Vec<GeneratedContour>,
) -> Result<(), &'static str> {
    let top = levels.len() - 1;
    let longitude = geometry::center_longitude(&ring);
    let mut anchor_rung = top;
    let mut anchor = vec![vec![ring]];
    let mut higher = anchor.clone();
    coverage[top].extend(geometry::world_parts(higher.clone()));
    for (rung, &level) in levels[..top].iter().enumerate().rev() {
        let mut next = geometry::buffer(&anchor, RISK_SPACING_KM * (anchor_rung - rung) as f64);
        if anchor_rung != top {
            // Preserve containment even when the offset is quantized or merges components.
            next.extend(higher.iter().cloned());
            next = geometry::union(next);
        } else if next.is_empty() {
            return Err("the ring could not be buffered");
        }
        if let Some(ring) = adjustments.remove(&(source_id, level)) {
            next = geometry::replace_envelope(ring, longitude, &higher, &next);
            anchor = next.clone();
            anchor_rung = rung;
        } else if let Some(points) = editable::points(&next) {
            generated.push(GeneratedContour {
                source_id: source_id.to_owned(),
                level,
                points,
            });
        }
        coverage[rung].extend(geometry::world_parts(next.clone()));
        higher = next;
    }
    Ok(())
}

fn append_areas(
    areas: &mut Vec<ForecastArea>,
    product: ForecastProduct,
    level: u8,
    hatch: bool,
    shapes: IntShapes,
) {
    if shapes.is_empty() {
        return;
    }
    let (code, dn) = if hatch {
        (format!("CIG{level}"), None)
    } else if product == ForecastProduct::Cat {
        let (code, dn) = CATEGORIES[usize::from(level)];
        (code.to_owned(), Some(dn))
    } else {
        (format!("0.{level:02}"), None)
    };
    let (description, rank, stroke, fill, hatch, hatch_level) =
        feature_style(product.key(), dn, &code);
    let label = display_label(product.key(), &code);
    areas.extend(shapes.into_iter().map(|shape| ForecastArea {
        label: label.clone(),
        description: description.clone(),
        fill: fill.to_owned(),
        stroke: stroke.to_owned(),
        rank,
        hatch,
        hatch_level,
        rings: shape.into_iter().map(geometry::geographic_ring).collect(),
    }));
}

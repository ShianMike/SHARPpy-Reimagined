//! Bundled CONUS place index, boundary mask, and reverse-town lookup.

use flate2::read::GzDecoder;
use std::cmp::Ordering;
use std::collections::HashMap;
use std::f64::consts::PI;
use std::io::{BufRead, BufReader};
use std::sync::OnceLock;

const CONUS_PLACES_GZ: &[u8] = include_bytes!("../../../sharpmod/resources/conus-places.tsv.gz");
const CONUS_BOUNDS: (f64, f64, f64, f64) = (24.2, -125.0, 49.6, -66.4);
const AREA_PROXY_RADIUS_SCALE: f64 = 1.25;
const KM_PER_MILE: f64 = 1.609344;
const EARTH_RADIUS_KM: f64 = 6371.0088;
const BOUNDARY_PREFIX: &str = "@boundary\t";

pub const MAJOR_CITIES: [(&str, f64, f64); 52] = [
    ("New York, New York", 40.71, -74.00),
    ("Los Angeles, California", 34.05, -118.24),
    ("Chicago, Illinois", 41.88, -87.63),
    ("Houston, Texas", 29.76, -95.37),
    ("Phoenix, Arizona", 33.45, -112.07),
    ("Philadelphia, Pennsylvania", 39.95, -75.17),
    ("San Antonio, Texas", 29.42, -98.49),
    ("San Diego, California", 32.72, -117.16),
    ("Dallas, Texas", 32.78, -96.80),
    ("Denver, Colorado", 39.74, -104.99),
    ("Seattle, Washington", 47.61, -122.33),
    ("Minneapolis, Minnesota", 44.98, -93.27),
    ("New Orleans, Louisiana", 29.95, -90.07),
    ("Atlanta, Georgia", 33.75, -84.39),
    ("Miami, Florida", 25.76, -80.19),
    ("Nashville, Tennessee", 36.16, -86.78),
    ("St. Louis, Missouri", 38.63, -90.20),
    ("Kansas City, Missouri", 39.10, -94.58),
    ("Indianapolis, Indiana", 39.77, -86.16),
    ("Columbus, Ohio", 39.96, -83.00),
    ("Pittsburgh, Pennsylvania", 40.44, -79.99),
    ("Baltimore, Maryland", 39.29, -76.61),
    ("Charlotte, North Carolina", 35.23, -80.84),
    ("Memphis, Tennessee", 35.15, -90.05),
    ("Milwaukee, Wisconsin", 43.04, -87.91),
    ("Detroit, Michigan", 42.33, -83.05),
    ("Cleveland, Ohio", 41.50, -81.69),
    ("Oklahoma City, Oklahoma", 35.47, -97.52),
    ("Albuquerque, New Mexico", 35.08, -106.65),
    ("Salt Lake City, Utah", 40.76, -111.89),
    ("Las Vegas, Nevada", 36.17, -115.14),
    ("Portland, Oregon", 45.52, -122.68),
    ("San Francisco, California", 37.77, -122.42),
    ("Sacramento, California", 38.58, -121.49),
    ("Tucson, Arizona", 32.22, -110.97),
    ("El Paso, Texas", 31.76, -106.49),
    ("Omaha, Nebraska", 41.26, -95.93),
    ("Little Rock, Arkansas", 34.75, -92.29),
    ("Birmingham, Alabama", 33.52, -86.80),
    ("Jacksonville, Florida", 30.33, -81.66),
    ("Tampa, Florida", 27.95, -82.46),
    ("Orlando, Florida", 28.54, -81.38),
    ("Richmond, Virginia", 37.54, -77.44),
    ("Washington, District of Columbia", 38.90, -77.04),
    ("Boston, Massachusetts", 42.36, -71.06),
    ("Buffalo, New York", 42.89, -78.88),
    ("Boise, Idaho", 43.62, -116.20),
    ("Billings, Montana", 45.78, -108.50),
    ("Fargo, North Dakota", 46.88, -96.79),
    ("Wichita, Kansas", 37.69, -97.34),
    ("Cheyenne, Wyoming", 41.14, -104.82),
    ("Spokane, Washington", 47.66, -117.43),
];

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct PlaceLabel {
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub tier: u8,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PlaceKind {
    Place,
    Subdivision,
}

struct PlaceRecord {
    label: String,
    lat: f64,
    lon: f64,
    kind: PlaceKind,
    land_area_sqmi: f64,
}

struct BoundaryPolygon {
    min_lat: f64,
    min_lon: f64,
    max_lat: f64,
    max_lon: f64,
    points: Vec<(f64, f64)>,
}

struct PlaceIndex {
    cells: HashMap<(i32, i32), Vec<PlaceRecord>>,
    boundaries: Vec<BoundaryPolygon>,
    labels: Vec<PlaceLabel>,
}

fn index() -> &'static PlaceIndex {
    static INDEX: OnceLock<PlaceIndex> = OnceLock::new();
    INDEX.get_or_init(load_index)
}

fn load_index() -> PlaceIndex {
    let decoder = GzDecoder::new(CONUS_PLACES_GZ);
    let reader = BufReader::new(decoder);
    let mut cells: HashMap<(i32, i32), Vec<PlaceRecord>> = HashMap::new();
    let mut boundaries: Vec<BoundaryPolygon> = Vec::new();

    for line in reader.lines().map_while(Result::ok) {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix(BOUNDARY_PREFIX) {
            if let Some((_, coords)) = rest.split_once('\t') {
                let mut points = Vec::new();
                let mut valid = true;
                for encoded in coords.split_whitespace() {
                    let Some((lon_text, lat_text)) = encoded.split_once(',') else {
                        valid = false;
                        break;
                    };
                    let (Ok(lon), Ok(lat)) = (lon_text.parse::<f64>(), lat_text.parse::<f64>())
                    else {
                        valid = false;
                        break;
                    };
                    if !lon.is_finite() || !lat.is_finite() {
                        valid = false;
                        break;
                    }
                    points.push((lon, lat));
                }
                if valid && points.len() >= 4 {
                    if points.first() != points.last() {
                        points.push(points[0]);
                    }
                    let mut min_lat = f64::INFINITY;
                    let mut max_lat = f64::NEG_INFINITY;
                    let mut min_lon = f64::INFINITY;
                    let mut max_lon = f64::NEG_INFINITY;
                    for &(lon, lat) in &points {
                        min_lon = min_lon.min(lon);
                        max_lon = max_lon.max(lon);
                        min_lat = min_lat.min(lat);
                        max_lat = max_lat.max(lat);
                    }
                    boundaries.push(BoundaryPolygon {
                        min_lat,
                        min_lon,
                        max_lat,
                        max_lon,
                        points,
                    });
                }
            }
            continue;
        }
        let mut fields = line.split('\t');
        let (Some(label), Some(lat_text), Some(lon_text), Some(kind_text), Some(_geoid)) = (
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
        ) else {
            continue;
        };
        let area_text = fields.next().unwrap_or("");
        let kind = match kind_text {
            "place" => PlaceKind::Place,
            "subdivision" => PlaceKind::Subdivision,
            _ => continue,
        };
        let (Ok(lat), Ok(lon)) = (lat_text.parse::<f64>(), lon_text.parse::<f64>()) else {
            continue;
        };
        let land_area_sqmi = if area_text.is_empty() {
            0.0
        } else if let Ok(area) = area_text.parse::<f64>() {
            area
        } else {
            continue;
        };
        if label.is_empty()
            || !lat.is_finite()
            || !lon.is_finite()
            || !land_area_sqmi.is_finite()
            || land_area_sqmi < 0.0
        {
            continue;
        }
        cells
            .entry((lat.floor() as i32, lon.floor() as i32))
            .or_default()
            .push(PlaceRecord {
                label: label.to_owned(),
                lat,
                lon,
                kind,
                land_area_sqmi,
            });
    }

    type CellKey = (i32, i32, i32, i32);
    type CellWinner<'a> = (f64, &'a str, f64, f64);
    let mut best: HashMap<CellKey, CellWinner<'_>> = HashMap::new();
    for (&(lat_cell, lon_cell), records) in &cells {
        for record in records {
            if record.kind != PlaceKind::Place {
                continue;
            }
            let q_lat = (((record.lat - f64::from(lat_cell)) * 4.0).floor() as i32).clamp(0, 3);
            let q_lon = (((record.lon - f64::from(lon_cell)) * 4.0).floor() as i32).clamp(0, 3);
            let key = (lat_cell, lon_cell, q_lat, q_lon);
            let replace = match best.get(&key) {
                None => true,
                Some(&(best_area, best_label, _, _)) => {
                    record
                        .land_area_sqmi
                        .total_cmp(&best_area)
                        .then_with(|| record.label.as_str().cmp(best_label))
                        == Ordering::Greater
                }
            };
            if replace {
                best.insert(
                    key,
                    (record.land_area_sqmi, &record.label, record.lat, record.lon),
                );
            }
        }
    }
    let mut towns: Vec<(f64, &str, f64, f64)> = best.into_values().collect();
    towns.sort_by(|a, b| b.0.total_cmp(&a.0).then_with(|| a.1.cmp(b.1)));

    let mut labels = Vec::with_capacity(MAJOR_CITIES.len() + towns.len());
    for &(name, lat, lon) in &MAJOR_CITIES {
        labels.push(PlaceLabel {
            name: name.to_owned(),
            lat,
            lon,
            tier: 1,
        });
    }
    for (_, name, lat, lon) in towns {
        labels.push(PlaceLabel {
            name: name.to_owned(),
            lat,
            lon,
            tier: 2,
        });
    }
    PlaceIndex {
        cells,
        boundaries,
        labels,
    }
}

/// Major US metros followed by the largest Census place per quarter-degree cell.
pub fn place_labels() -> &'static [PlaceLabel] {
    &index().labels
}

pub fn place_labels_json() -> Result<String, String> {
    #[derive(serde::Serialize)]
    struct PlaceLabelsReply<'a> {
        places: &'a [PlaceLabel],
    }
    serde_json::to_string(&PlaceLabelsReply {
        places: place_labels(),
    })
    .map_err(|error| error.to_string())
}

fn point_on_segment(x: f64, y: f64, x1: f64, y1: f64, x2: f64, y2: f64) -> bool {
    let tolerance = 1.0e-8;
    let cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1);
    let scale = 1.0_f64.max((x2 - x1).abs()).max((y2 - y1).abs());
    if cross.abs() > tolerance * scale {
        return false;
    }
    x >= x1.min(x2) - tolerance
        && x <= x1.max(x2) + tolerance
        && y >= y1.min(y2) - tolerance
        && y <= y1.max(y2) + tolerance
}

fn point_in_polygon(lat: f64, lon: f64, points: &[(f64, f64)]) -> bool {
    let mut inside = false;
    let mut previous = *points.last().expect("nonempty boundary ring");
    for &current in points {
        let (x1, y1) = previous;
        let (x2, y2) = current;
        if point_on_segment(lon, lat, x1, y1, x2, y2) {
            return true;
        }
        if (y1 > lat) != (y2 > lat) {
            let crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1);
            if lon < crossing {
                inside = !inside;
            }
        }
        previous = current;
    }
    inside
}

/// Return whether a coordinate is inside the lower 48 US states or D.C.
pub fn is_conus_point(lat: f64, lon: f64) -> bool {
    if !lat.is_finite() || !lon.is_finite() {
        return false;
    }
    let (south, west, north, east) = CONUS_BOUNDS;
    if !(south..=north).contains(&lat) || !(west..=east).contains(&lon) {
        return false;
    }
    index().boundaries.iter().any(|poly| {
        lat >= poly.min_lat
            && lat <= poly.max_lat
            && lon >= poly.min_lon
            && lon <= poly.max_lon
            && point_in_polygon(lat, lon, &poly.points)
    })
}

fn distance_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let lat1r = lat1.to_radians();
    let lat2r = lat2.to_radians();
    let dlat = lat2r - lat1r;
    let dlon = (lon2 - lon1).to_radians();
    let hav = (dlat * 0.5).sin().powi(2) + lat1r.cos() * lat2r.cos() * (dlon * 0.5).sin().powi(2);
    EARTH_RADIUS_KM * 2.0 * hav.clamp(0.0, 1.0).sqrt().asin()
}

fn area_proxy_radius_km(land_area_sqmi: f64) -> f64 {
    if !land_area_sqmi.is_finite() || land_area_sqmi <= 0.0 {
        return 0.0;
    }
    (land_area_sqmi / PI).sqrt() * KM_PER_MILE * AREA_PROXY_RADIUS_SCALE
}

/// Return the nearest bundled Census place for a CONUS coordinate.
pub fn offline_conus_town_name(lat: f64, lon: f64, max_distance_km: f64) -> String {
    if !lat.is_finite() || !lon.is_finite() || !max_distance_km.is_finite() {
        return String::new();
    }
    let limit = max_distance_km.max(1.0);
    if !is_conus_point(lat, lon) {
        return String::new();
    }
    let lat_delta = limit / 110.5 + 0.1;
    let lon_scale = lat.to_radians().cos().max(0.35);
    let lon_delta = limit / (111.0 * lon_scale) + 0.1;
    let idx = index();

    let mut best: Option<(f64, u8, &str)> = None;
    let mut containing_best: Option<(u8, f64, f64, &str)> = None;
    let lat_start = (lat - lat_delta).floor() as i32;
    let lat_end = (lat + lat_delta).floor() as i32;
    let lon_start = (lon - lon_delta).floor() as i32;
    let lon_end = (lon + lon_delta).floor() as i32;

    for lat_cell in lat_start..=lat_end {
        for lon_cell in lon_start..=lon_end {
            let Some(records) = idx.cells.get(&(lat_cell, lon_cell)) else {
                continue;
            };
            for record in records {
                let distance = distance_km(lat, lon, record.lat, record.lon);
                let kind_rank = match record.kind {
                    PlaceKind::Place => 0_u8,
                    PlaceKind::Subdivision => 1_u8,
                };
                let replace_best = match best {
                    None => true,
                    Some((b_dist, b_rank, b_label)) => {
                        distance
                            .total_cmp(&b_dist)
                            .then_with(|| kind_rank.cmp(&b_rank))
                            .then_with(|| record.label.as_str().cmp(b_label))
                            == Ordering::Less
                    }
                };
                if replace_best {
                    best = Some((distance, kind_rank, &record.label));
                }
                let proxy = area_proxy_radius_km(record.land_area_sqmi);
                if proxy > 0.0 && distance <= proxy {
                    let replace_cont = match containing_best {
                        None => true,
                        Some((c_rank, c_dist, c_area, c_label)) => {
                            kind_rank
                                .cmp(&c_rank)
                                .then_with(|| distance.total_cmp(&c_dist))
                                .then_with(|| record.land_area_sqmi.total_cmp(&c_area))
                                .then_with(|| record.label.as_str().cmp(c_label))
                                == Ordering::Less
                        }
                    };
                    if replace_cont {
                        containing_best =
                            Some((kind_rank, distance, record.land_area_sqmi, &record.label));
                    }
                }
            }
        }
    }
    if let Some((_, _, _, label)) = containing_best {
        return label.to_owned();
    }
    match best {
        Some((distance, _, label)) if distance <= limit => label.to_owned(),
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn bundled_conus_index_matches_metadata_counts_and_labels() {
        let idx = index();
        let record_count: usize = idx.cells.values().map(Vec::len).sum();
        assert_eq!(record_count, 52_818);
        assert_eq!(idx.boundaries.len(), 831);
        let labels = place_labels();
        let tier1: Vec<&str> = labels
            .iter()
            .filter(|p| p.tier == 1)
            .map(|p| p.name.as_str())
            .collect();
        assert_eq!(tier1.len(), MAJOR_CITIES.len());
        assert_eq!(tier1[0], "New York, New York");
        let towns: Vec<&PlaceLabel> = labels.iter().filter(|p| p.tier == 2).collect();
        assert!(towns.len() > 1000);
        let mut seen = HashSet::new();
        for town in towns {
            let whole_lat = town.lat.floor() as i32;
            let q_lat = (((town.lat - f64::from(whole_lat)) * 4.0) as i32).min(3);
            let whole_lon = town.lon.floor() as i32;
            let q_lon = (((town.lon - f64::from(whole_lon)) * 4.0) as i32).min(3);
            assert!(seen.insert((whole_lat, q_lat, whole_lon, q_lon)));
        }
    }

    #[test]
    fn conus_mask_and_area_proxy_lookup_match_reference_points() {
        for (lat, lon) in [
            (47.6062, -122.3321),
            (25.7617, -80.1918),
            (25.9017, -97.4975),
            (42.3314, -83.0458),
            (45.76, -91.60),
        ] {
            assert!(is_conus_point(lat, lon));
        }
        for (lat, lon) in [
            (43.6532, -79.3832),
            (32.5149, -117.0382),
            (27.0, -90.0),
            (35.0, -70.0),
        ] {
            assert!(!is_conus_point(lat, lon));
            assert_eq!(offline_conus_town_name(lat, lon, 200.0), "");
        }
        assert_eq!(
            offline_conus_town_name(39.7392, -104.9903, 200.0),
            "Denver, Colorado"
        );
        assert_eq!(
            offline_conus_town_name(39.703052, -104.936157, 200.0),
            "Glendale, Colorado"
        );
        assert_eq!(
            offline_conus_town_name(45.76, -91.60, 200.0),
            "Town of Birchwood, Wisconsin"
        );
        assert_eq!(
            offline_conus_town_name(38.5, -100.5, 200.0),
            "Dighton Township, Kansas"
        );
    }
}

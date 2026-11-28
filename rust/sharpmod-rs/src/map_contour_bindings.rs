//! Complete display geometry and real contour-level label anchors for map frames.

use std::collections::{HashMap, HashSet};

use numpy::ndarray::Array2;
use numpy::{
    AllowTypeChange, IntoPyArray, PyArray2, PyArrayLikeDyn, PyReadonlyArray2, PyUntypedArrayMethods,
};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyDict};

use crate::{
    contours::{self, Projection, Segment},
    isobar_paths::smooth_paths,
    pressure_centers,
};

struct Label {
    position: [f64; 2],
    value: f64,
}

#[derive(Default)]
struct Geometry {
    segments: Vec<Segment>,
    paths: Vec<(f64, Vec<[f64; 2]>)>,
    labels: Vec<Label>,
}

enum ContourKind {
    Segments,
    Pressure,
    Dam,
}

fn label_anchors(
    candidates: impl IntoIterator<Item = (f64, [f64; 2])>,
    bounds: (f64, f64, f64, f64),
) -> Vec<Label> {
    let (west, east, south, north) = bounds;
    let mut occupied = HashSet::new();
    let mut labels = Vec::new();
    for (value, position) in candidates {
        let cell = (
            ((position[0] - west) / (east - west) * 24.0).floor() as i32,
            ((position[1] - south) / (north - south) * 12.0).floor() as i32,
        );
        if occupied.insert(cell) {
            labels.push(Label { position, value });
            if labels.len() == 256 {
                break;
            }
        }
    }
    labels
}

/// Place up to three distinct pressure levels in each geographic tile. The
/// lowest, middle, and highest levels let the client label both local systems
/// and the broader pressure pattern. The client resolves screen collisions.
fn pressure_label_anchors(
    paths: &[(f64, Vec<[f64; 2]>)],
    (west, east, south, north): (f64, f64, f64, f64),
) -> Vec<Label> {
    const COLUMNS: usize = 36;
    const ROWS: usize = 18;
    let scale_x = COLUMNS as f64 / (east - west);
    let scale_y = ROWS as f64 / (north - south);
    let mut tiles = vec![HashMap::new(); COLUMNS * ROWS];
    for (value, path) in paths {
        for pair in path.windows(2) {
            let [a, b] = [pair[0], pair[1]];
            let dx = b[0] - a[0];
            let dy = b[1] - a[1];
            // Long straight segments can span several tiles after simplification.
            let steps = (dx.abs() * scale_x).max(dy.abs() * scale_y).ceil() as usize;
            let steps = steps.max(1);
            for step in 0..steps {
                let t = (step as f64 + 0.5) / steps as f64;
                let position = [a[0] + t * dx, a[1] + t * dy];
                let x = (position[0] - west) * scale_x;
                let y = (position[1] - south) * scale_y;
                let col = (x as usize).min(COLUMNS - 1);
                let row = (y as usize).min(ROWS - 1);
                let distance = (x - col as f64 - 0.5).powi(2) + (y - row as f64 - 0.5).powi(2);
                let closest = tiles[row * COLUMNS + col]
                    .entry(value.to_bits())
                    .or_insert((distance, position));
                if distance < closest.0 {
                    *closest = (distance, position);
                }
            }
        }
    }

    let mut labels = Vec::new();
    for levels in tiles {
        let mut levels: Vec<Label> = levels
            .into_iter()
            .map(|(value, (_, position))| Label {
                position,
                value: f64::from_bits(value),
            })
            .collect();
        levels.sort_unstable_by(|a, b| a.value.total_cmp(&b.value));
        let count = levels.len();
        labels.extend(levels.into_iter().enumerate().filter_map(|(index, label)| {
            (index == 0 || index == count / 2 || index + 1 == count).then_some(label)
        }));
    }
    labels
}

fn display_geometry(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    bounds: (f64, f64, f64, f64),
    kind: ContourKind,
) -> Result<Geometry, String> {
    let projection = Projection::new(height, width, bounds)?;
    let filtered_pressure = matches!(kind, ContourKind::Pressure)
        .then(|| contours::pressure::smooth_grid(values, height, width, &projection));
    let values = filtered_pressure.as_deref().unwrap_or(values);
    let mut segments = contours::extract_display_levels(values, height, width, interval, 100_000)?;
    if matches!(kind, ContourKind::Segments) {
        for (_, (a, b)) in &mut segments {
            let [ax, ay] = projection.point([a.0, a.1]);
            let [bx, by] = projection.point([b.0, b.1]);
            *a = (ax, ay);
            *b = (bx, by);
        }
        let labels = label_anchors(
            segments
                .iter()
                .map(|&(value, (a, b))| (value, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0])),
            bounds,
        );
        return Ok(Geometry {
            segments: segments.into_iter().map(|(_, segment)| segment).collect(),
            labels,
            ..Geometry::default()
        });
    }
    let mut paths = smooth_paths(&segments, values, height, width);
    for (_, path) in &mut paths {
        for point in path {
            *point = projection.point(*point);
        }
    }
    let labels = if matches!(kind, ContourKind::Pressure) {
        pressure_label_anchors(&paths, bounds)
    } else {
        label_anchors(
            paths.iter().flat_map(|(value, path)| {
                path.windows(2).map(|pair| {
                    let [a, b] = [pair[0], pair[1]];
                    (*value, [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0])
                })
            }),
            bounds,
        )
    };
    Ok(Geometry {
        paths,
        labels,
        ..Geometry::default()
    })
}

/// One 1000-500 mb thickness cell in decametres. The 1000 mb height is
/// legitimately negative inside deep surface lows (surface pressure below
/// 1000 hPa), so only finiteness gates a cell — never a sign test, which
/// would erase the cores of deep cyclones. Non-finite input heights read
/// NaN so contour holes stay holes.
fn thickness_cell(hgt500: f32, hgt1000: f32) -> f32 {
    if hgt500.is_finite() && hgt1000.is_finite() {
        (hgt500 - hgt1000) / 10.0
    } else {
        f32::NAN
    }
}

/// Decametre 1000-500 mb thickness from paired geopotential-height grids.
///
/// Both grids must share one `(height, width)` shape; a mismatch is a caller
/// bug and raises instead of guessing an alignment. Every output cell is
/// `(hgt500 - hgt1000) / 10` where both heights are finite, and NaN wherever
/// either input is missing, above ground, or otherwise non-finite.
#[pyfunction]
pub fn thickness_dam<'py>(
    py: Python<'py>,
    hgt500: PyReadonlyArray2<'py, f32>,
    hgt1000: PyReadonlyArray2<'py, f32>,
) -> PyResult<Py<PyArray2<f32>>> {
    let (top, bottom) = (hgt500.as_array(), hgt1000.as_array());
    if top.shape() != bottom.shape() {
        return Err(PyValueError::new_err(
            "thickness height grids must share one shape",
        ));
    }
    let (height, width) = (top.shape()[0], top.shape()[1]);
    let output = Array2::from_shape_fn((height, width), |(row, col)| {
        thickness_cell(top[[row, col]], bottom[[row, col]])
    });
    Ok(output.into_pyarray(py).unbind())
}

/// Label rows exactly as the bridge protocol writes them.
fn label_dicts(py: Python<'_>, labels: Vec<Label>) -> PyResult<Vec<Bound<'_, PyDict>>> {
    labels
        .into_iter()
        .map(|label| {
            let dict = PyDict::new(py);
            dict.set_item("position", label.position)?;
            dict.set_item("value", label.value)?;
            Ok(dict)
        })
        .collect()
}

/// A contour-operation failure, classified the way the bridge already
/// surfaces unreadable contour products.
fn field_fetch_failed(py: Python<'_>, message: String) -> PyErr {
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|errors| errors.getattr("BridgeError"))
        .and_then(|class| class.call1(("FIELD_FETCH_FAILED", message, false)))
    {
        Ok(exception) => PyErr::from_value(exception),
        Err(error) => error,
    }
}

#[pyfunction(signature = (product, run_time, forecast_hour, unit, interval, values=None, bounds=None, wind=None, thickness_values=None, thickness_interval=None))]
#[allow(clippy::too_many_arguments)]
fn map_contour_frame(
    py: Python<'_>,
    product: &str,
    run_time: &str,
    forecast_hour: u32,
    unit: &str,
    interval: f64,
    values: Option<PyArrayLikeDyn<'_, f64, AllowTypeChange>>,
    bounds: Option<(f64, f64, f64, f64)>,
    wind: Option<Py<PyAny>>,
    thickness_values: Option<PyArrayLikeDyn<'_, f64, AllowTypeChange>>,
    thickness_interval: Option<f64>,
) -> PyResult<Py<PyAny>> {
    let (geometry, centers) = match values {
        None => (Geometry::default(), Vec::new()),
        Some(field) => {
            if field.ndim() != 2 {
                return Err(PyValueError::new_err(
                    "contour field must be a two-dimensional grid",
                ));
            }
            let Some(bounds) = bounds else {
                return Err(PyValueError::new_err(
                    "contour bounds are required with a contour field",
                ));
            };
            let (height, width) = (field.shape()[0], field.shape()[1]);
            let values: Vec<f64> = field.as_array().iter().copied().collect();
            let isobars = unit == "hPa";
            let kind = match unit {
                "hPa" => ContourKind::Pressure,
                "dam" => ContourKind::Dam,
                _ => ContourKind::Segments,
            };
            let (west, east, south, north) = bounds;
            let output = py.detach(move || {
                display_geometry(&values, height, width, interval, bounds, kind).map(|geometry| {
                    let centers = if isobars {
                        let field: Vec<f32> = values.iter().map(|&value| value as f32).collect();
                        // Pressure centers keep [west, south, east, north]
                        // order; the frame tuple lists its longitudes first.
                        pressure_centers::detect(
                            &field,
                            height,
                            width,
                            &[west, south, east, north],
                            "hPa",
                        )
                    } else {
                        Vec::new()
                    };
                    (geometry, centers)
                })
            });
            match output {
                Ok(output) => output,
                Err(message) => return Err(field_fetch_failed(py, message)),
            }
        }
    };
    let thickness = match thickness_values {
        None => None,
        Some(field) => {
            if field.ndim() != 2 {
                return Err(PyValueError::new_err(
                    "thickness field must be a two-dimensional grid",
                ));
            }
            let Some(bounds) = bounds else {
                return Err(PyValueError::new_err(
                    "contour bounds are required with a thickness field",
                ));
            };
            let (height, width) = (field.shape()[0], field.shape()[1]);
            if height < 2 || width < 2 {
                return Err(PyValueError::new_err(
                    "thickness field must span at least two cells per side",
                ));
            }
            let values: Vec<f64> = field.as_array().iter().copied().collect();
            let t_interval = thickness_interval.unwrap_or(6.0);
            // Thickness contours are exactly what the isobar pipeline draws
            // well: levels of a smooth field, joined and rounded in place.
            let output = py.detach(move || {
                display_geometry(&values, height, width, t_interval, bounds, ContourKind::Dam)
            });
            match output {
                Ok(Geometry { paths, labels, .. }) => {
                    let path_rows = paths
                        .into_iter()
                        .map(|(value, points)| {
                            let row = PyDict::new(py);
                            row.set_item("value", value)?;
                            row.set_item("points", points)?;
                            Ok(row)
                        })
                        .collect::<PyResult<Vec<_>>>()?;
                    let dict = PyDict::new(py);
                    dict.set_item("unit", "dam")?;
                    dict.set_item("interval", t_interval)?;
                    dict.set_item("paths", path_rows)?;
                    dict.set_item("labels", label_dicts(py, labels)?)?;
                    Some(dict.into_any().unbind())
                }
                Err(message) => return Err(field_fetch_failed(py, message)),
            }
        }
    };
    let result = PyDict::new(py);
    result.set_item("product", product)?;
    result.set_item("run_time", run_time)?;
    result.set_item("forecast_hour", forecast_hour)?;
    result.set_item("unit", unit)?;
    result.set_item("interval", interval)?;
    result.set_item(
        "segments",
        geometry
            .segments
            .into_iter()
            .map(|(a, b)| [[a.0, a.1], [b.0, b.1]])
            .collect::<Vec<_>>(),
    )?;
    result.set_item("wind", wind)?;
    if !geometry.paths.is_empty() {
        let paths: Vec<Vec<[f64; 2]>> = geometry.paths.into_iter().map(|(_, path)| path).collect();
        result.set_item("paths", paths)?;
    }
    if !geometry.labels.is_empty() {
        result.set_item("labels", label_dicts(py, geometry.labels)?)?;
    }
    if !centers.is_empty() {
        let center_rows = centers
            .into_iter()
            .map(|center| {
                let dict = PyDict::new(py);
                dict.set_item("lon", center.lon)?;
                dict.set_item("lat", center.lat)?;
                dict.set_item("kind", center.kind)?;
                dict.set_item("value", center.value)?;
                Ok(dict)
            })
            .collect::<PyResult<Vec<_>>>()?;
        result.set_item("centers", center_rows)?;
    }
    if let Some(thickness) = thickness {
        result.set_item("thickness", thickness)?;
    }
    Ok(result.into_any().unbind())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(map_contour_frame, module)?)?;
    module.add_function(wrap_pyfunction!(thickness_dam, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use numpy::PyArrayMethods;

    #[test]
    fn pressure_geometry_suppresses_grid_noise_and_retains_the_broad_low_at_two_hpa() {
        let (height, width) = (40, 60);
        let mut pressure = vec![1001.0; height * width];
        pressure[10 * width + 12] = 1005.0;
        for row in 10..30 {
            pressure[row * width + 35..row * width + 54].fill(991.0);
        }
        let bounds = (-105.0, -102.6, 37.0, 38.6);
        let raw = contours::extract_display_levels(&pressure, height, width, 2.0, 1000).unwrap();
        assert!(raw.iter().any(|(level, _)| *level == 1004.0));
        let geometry =
            display_geometry(&pressure, height, width, 2.0, bounds, ContourKind::Pressure).unwrap();
        let levels: Vec<_> = geometry.paths.iter().map(|(level, _)| *level).collect();
        assert!([992.0, 994.0, 996.0, 998.0, 1000.0]
            .iter()
            .all(|level| levels.contains(level)));
        assert!(levels
            .iter()
            .all(|level| *level <= 1000.0 && level % 2.0 == 0.0));
        assert!(geometry
            .labels
            .iter()
            .all(|label| levels.contains(&label.value)));
    }

    #[test]
    fn pressure_anchors_cover_distinct_levels_across_a_global_map() {
        let values = [1024.0, 1014.0, 1008.0, 1000.0];
        let paths: Vec<_> = values
            .into_iter()
            .enumerate()
            .map(|(i, level)| {
                (
                    level,
                    vec![[-180.0, 31.0 + i as f64], [180.0, 31.0 + i as f64]],
                )
            })
            .collect();
        let labels = pressure_label_anchors(&paths, (-180.0, 180.0, -90.0, 90.0));
        for west in [-180.0, -90.0, 0.0, 170.0] {
            let local: Vec<_> = labels
                .iter()
                .filter(|label| (west..west + 10.0).contains(&label.position[0]))
                .map(|label| label.value)
                .collect();
            assert_eq!(local, [1000.0, 1014.0, 1024.0]);
        }
    }
    use pyo3::types::PyList;

    #[test]
    fn height_geometry_labels_use_the_contour_interval_in_decametres() {
        let field = [543.0, 552.0, 543.0, 552.0, 543.0, 552.0];
        let geometry = display_geometry(
            &field,
            3,
            2,
            6.0,
            (-120.0, -60.0, 20.0, 50.0),
            ContourKind::Dam,
        )
        .unwrap();
        assert!(geometry.segments.is_empty());
        assert_eq!(geometry.paths.len(), 2);
        assert_ne!(geometry.paths[0].0, geometry.paths[1].0);
        for level in [546.0, 552.0] {
            assert!(geometry.labels.iter().any(|label| label.value == level));
        }
        for label in geometry.labels {
            assert!(geometry
                .paths
                .iter()
                .any(|(value, path)| *value == label.value
                    && path.windows(2).any(|pair| {
                        let [a, b] = [pair[0], pair[1]];
                        label.position == [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0]
                    })));
        }
    }

    #[test]
    fn numeric_labels_use_actual_levels_and_lie_on_their_own_segment() {
        let levels = [996.0, 1000.0, 1004.0];
        let segments = [
            ((0.0, 1.0), (0.0, 3.0)),
            ((10.0, 2.0), (12.0, 2.0)),
            ((20.0, 3.0), (22.0, 3.0)),
        ];
        let labels = label_anchors(
            levels
                .into_iter()
                .zip(segments)
                .map(|(value, (a, b))| (value, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0])),
            (-30.0, 30.0, 0.0, 10.0),
        );
        assert_eq!(labels.len(), 3);
        for (index, label) in labels.iter().enumerate() {
            assert_eq!(label.value, levels[index]);
            let (a, b) = segments[index];
            assert_eq!(label.position, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0]);
        }
    }

    #[test]
    fn pressure_labels_are_sparse_and_bounded_without_inventing_values() {
        let segments: Vec<_> = (0..100_000)
            .map(|index| {
                let x = f64::from(index % 1000) / 1000.0 * 360.0 - 180.0;
                let y = f64::from(index / 1000) / 100.0 * 170.0 - 85.0;
                ((x, y), (x + 0.001, y))
            })
            .collect();
        let labels = label_anchors(
            segments
                .into_iter()
                .map(|(a, b)| (1012.0, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0])),
            (-180.0, 180.0, -85.0, 85.0),
        );
        assert!(labels.len() > 10 && labels.len() <= 256);
        assert!(labels.iter().all(|label| label.value == 1012.0));
        assert!(label_anchors([], (-180.0, 180.0, -85.0, 85.0)).is_empty());
    }

    #[test]
    fn thickness_cells_keep_negative_1000_mb_heights_and_gate_on_finiteness() {
        // Deep surface lows sit below 1000 hPa, so the 1000 mb height can be
        // negative; only missing samples write NaN, nothing else.
        assert_eq!(thickness_cell(5580.0, -120.0), 570.0);
        assert_eq!(thickness_cell(5400.0, 0.0), 540.0);
        assert_eq!(thickness_cell(5100.0, -87.0), 518.7);
        assert!(thickness_cell(f32::NAN, -120.0).is_nan());
        assert!(thickness_cell(5580.0, f32::INFINITY).is_nan());
        assert!(thickness_cell(f32::NEG_INFINITY, f32::NEG_INFINITY).is_nan());
    }

    #[test]
    fn thickness_dam_pairs_grids_and_raises_on_shape_mismatch() {
        Python::initialize();
        Python::attach(|py| {
            let hgt500 = Array2::from_shape_vec(
                (2, 3),
                vec![5580.0, 5400.0, 5460.0, f32::NAN, 5520.0, 5500.0],
            )
            .unwrap()
            .into_pyarray(py);
            let hgt1000 =
                Array2::from_shape_vec((2, 3), vec![-120.0, 0.0, 96.0, 110.0, -40.0, f32::NAN])
                    .unwrap()
                    .into_pyarray(py);
            let thickness = thickness_dam(py, hgt500.readonly(), hgt1000.readonly()).unwrap();
            let readonly = thickness.bind(py).readonly();
            let view = readonly.as_array();
            assert_eq!(view[[0, 0]], 570.0);
            assert_eq!(view[[0, 1]], 540.0);
            assert_eq!(view[[0, 2]], 536.4);
            assert_eq!(view[[1, 1]], 556.0);
            assert!(view[[1, 0]].is_nan());
            assert!(view[[1, 2]].is_nan());
            let odd = Array2::from_shape_vec((1, 6), vec![5400.0; 6])
                .unwrap()
                .into_pyarray(py);
            assert!(thickness_dam(py, hgt500.readonly(), odd.readonly()).is_err());
        });
    }

    #[test]
    fn thickness_contours_smooth_into_paths_with_labels_on_the_dam_interval() {
        // A clean anti-diagonal thickness ramp from 480 dam upward: one level
        // set every 6 dam, exactly what a thickness overlay draws.
        let (height, width) = (24, 38);
        let field: Vec<f64> = (0..height * width)
            .map(|index| 480.0 + 3.0 * (index / width + index % width) as f64)
            .collect();
        let bounds = (-100.0, -60.0, 25.0, 50.0);
        let geometry =
            display_geometry(&field, height, width, 6.0, bounds, ContourKind::Dam).unwrap();
        assert!(geometry.segments.is_empty());
        assert!(geometry.paths.len() >= 20);
        for (value, path) in &geometry.paths {
            let multiple = value / 6.0;
            assert!(multiple.is_finite() && (multiple - multiple.round()).abs() < 1e-8);
            assert!(path.len() >= 2);
            for [lon, lat] in path {
                assert!((-100.0..=-60.0).contains(lon));
                assert!((25.0..=50.0).contains(lat));
            }
        }
        assert!(!geometry.labels.is_empty());
        for label in &geometry.labels {
            let multiple = label.value / 6.0;
            assert!(multiple.is_finite() && (multiple - multiple.round()).abs() < 1e-8);
        }
        // A fully missing thickness grid reads as no lines, not as a level of a
        // zero field.
        let missing_field = vec![f64::NAN; height * width];
        let missing =
            display_geometry(&missing_field, height, width, 6.0, bounds, ContourKind::Dam).unwrap();
        assert!(missing.paths.is_empty() && missing.labels.is_empty());
    }

    #[test]
    fn frames_carry_centers_and_thickness_in_one_contract() {
        Python::initialize();
        Python::attach(|py| {
            let (height, width) = (24, 38);
            let mslp: Vec<f64> = (0..height * width)
                .map(|index| {
                    let (row, col) = (index / width, index % width);
                    let offset = ((row as i64 - 10) * (row as i64 - 10)
                        + (col as i64 - 18) * (col as i64 - 18))
                        as f64;
                    if offset <= 36.0 {
                        1035.0 - 0.5 * offset
                    } else {
                        1013.0
                    }
                })
                .collect();
            let thickness_field: Vec<f64> = (0..height * width)
                .map(|index| 500.0 + (index / width + index % width) as f64)
                .collect();
            let mslp: PyArrayLikeDyn<'_, f64, AllowTypeChange> =
                Array2::from_shape_vec((height, width), mslp)
                    .unwrap()
                    .into_pyarray(py)
                    .extract()
                    .unwrap();
            let thickness_field: PyArrayLikeDyn<'_, f64, AllowTypeChange> =
                Array2::from_shape_vec((height, width), thickness_field)
                    .unwrap()
                    .into_pyarray(py)
                    .extract()
                    .unwrap();
            let frame = map_contour_frame(
                py,
                "mslp",
                "2026-11-06T00:00:00Z",
                0,
                "hPa",
                4.0,
                Some(mslp),
                Some((-100.0, -60.0, 25.0, 50.0)),
                None,
                Some(thickness_field),
                None,
            )
            .unwrap();
            let frame = frame.bind(py);
            let dict = frame.cast::<PyDict>().unwrap();
            let centers_value = dict.get_item("centers").unwrap().unwrap();
            let centers = centers_value.cast::<PyList>().unwrap();
            assert_eq!(centers.len(), 1);
            let center = centers.get_item(0).unwrap();
            assert_eq!(
                center
                    .get_item("kind")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "H"
            );
            assert_eq!(
                center.get_item("value").unwrap().extract::<i32>().unwrap(),
                1035
            );
            let center_lon = center.get_item("lon").unwrap().extract::<f64>().unwrap();
            let center_lat = center.get_item("lat").unwrap().extract::<f64>().unwrap();
            assert!((-100.0..=-60.0).contains(&center_lon));
            assert!((25.0..=50.0).contains(&center_lat));
            let thickness_value = dict.get_item("thickness").unwrap().unwrap();
            let thickness = thickness_value.cast::<PyDict>().unwrap();
            assert_eq!(
                thickness
                    .get_item("unit")
                    .unwrap()
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "dam"
            );
            assert_eq!(
                thickness
                    .get_item("interval")
                    .unwrap()
                    .unwrap()
                    .extract::<f64>()
                    .unwrap(),
                6.0
            );
            let thickness_paths_value = thickness.get_item("paths").unwrap().unwrap();
            let thickness_paths = thickness_paths_value.cast::<PyList>().unwrap();
            assert!(!thickness_paths.is_empty());
            for row in thickness_paths.iter() {
                let row = row.cast::<PyDict>().unwrap();
                let value = row
                    .get_item("value")
                    .unwrap()
                    .unwrap()
                    .extract::<f64>()
                    .unwrap();
                assert!(((value / 6.0) - (value / 6.0).round()).abs() < 1e-8);
                let points = row.get_item("points").unwrap().unwrap();
                assert!(points.cast::<PyList>().unwrap().len() >= 2);
            }
            // A frame with no fields omits both optional keys.
            let bare = map_contour_frame(
                py,
                "mslp",
                "2026-11-06T00:00:00Z",
                0,
                "hPa",
                4.0,
                None,
                None,
                None,
                None,
                None,
            )
            .unwrap();
            let bare = bare.bind(py).cast::<PyDict>().unwrap();
            assert!(bare.get_item("centers").unwrap().is_none());
            assert!(bare.get_item("thickness").unwrap().is_none());
        });
    }
}

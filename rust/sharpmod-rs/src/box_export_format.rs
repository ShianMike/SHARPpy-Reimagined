//! Native ports of the box-sounding export contracts.
//!
//! Mirrors the pure formatting half of ``sharpmod.analysis.box_export``:
//! CPython-identical ``:.6g`` number rendering, the ``-180..180`` longitude
//! convention, cell spacing, RFC 7946 cell geometry with the antimeridian
//! split, the CSV row layout (including its exact Python element types), and
//! the one-line export summary. Byte-identical exports matter here: the desktop
//! workspace and the command line must write the same file from the same box.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// One exported node: ``(hour, request_id, row, col, lat, lon, status,
/// per-field values, matches-verdict)``.
pub(crate) type BoxCsvRowInput = (
    Option<i64>,
    String,
    i64,
    i64,
    f64,
    f64,
    String,
    Vec<Option<f64>>,
    Option<bool>,
);

/// The identity columns that identify a node before any field values.
const IDENTITY_COLUMNS: [&str; 6] = ["request_id", "row", "col", "lat", "lon", "status"];

// ---------------------------------------------------------------------------
// Number formatting, matching CPython ``f"{value:.6g}"``
// ---------------------------------------------------------------------------

/// Port of ``_number``: six significant digits, scientific notation with a
/// lowercase ``e`` and a signed, zero-padded exponent below ``1e-4`` and at
/// ``1e6`` and above, trailing zeros stripped, ``nan``/``inf``/``-inf`` spelled
/// like Python, and ``-0`` kept for negative zero. Rounding is exact against
/// the binary value with ties-to-even, exactly like CPython.
fn format_g6(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value.is_sign_negative() {
            "-inf".to_string()
        } else {
            "inf".to_string()
        };
    }
    let negative = value.is_sign_negative();
    if value.classify() == std::num::FpCategory::Zero {
        return if negative { "-0" } else { "0" }.to_string();
    }
    let sign = if negative { "-" } else { "" };
    // Rust's ``{:.5e}`` renders the magnitude with exactly six significant,
    // correctly rounded digits, so the whole problem reduces to presentation.
    let scientific = format!("{:.5e}", value.abs());
    let (mantissa, exponent_text) = scientific
        .split_once('e')
        .unwrap_or((scientific.as_str(), "0"));
    let exponent: i32 = exponent_text.parse().unwrap_or(0);
    let digits = mantissa.replace('.', "");
    let digits = digits.trim_end_matches('0');
    // CPython picks scientific notation below 1e-4 and from the precision up.
    if !(-4..6).contains(&exponent) {
        let significand = if digits.len() == 1 {
            digits.to_string()
        } else {
            format!("{}.{}", &digits[..1], &digits[1..])
        };
        let exponent_sign = if exponent < 0 { '-' } else { '+' };
        return format!("{sign}{significand}e{exponent_sign}{:02}", exponent.abs());
    }
    // Fixed notation: place the decimal point, padding with zeros either side.
    let point = exponent + 1;
    if point <= 0 {
        return format!("{sign}0.{}{digits}", "0".repeat((-point) as usize));
    }
    if point as usize >= digits.len() {
        return format!(
            "{sign}{digits}{}",
            "0".repeat(point as usize - digits.len())
        );
    }
    let split = point as usize;
    format!("{sign}{}.{}", &digits[..split], &digits[split..])
}

/// Port of ``_number``: an absent field is an empty string, never a zero and
/// never a ``-9999``, so absence survives the trip into a spreadsheet or GIS.
pub(crate) fn format_number_core(value: Option<f64>) -> String {
    value.map_or_else(String::new, format_g6)
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

/// Port of ``_normalize_lon180``: the -180..180 convention every export uses.
pub(crate) fn normalize_lon180_core(lon: f64) -> f64 {
    (lon + 180.0).rem_euclid(360.0) - 180.0
}

/// ``max(-90.0, min(90.0, value))`` with CPython's left-to-right argument
/// evaluation: a NaN latitude clamps to 90.0 rather than propagating, -inf
/// clamps to -90.0, and +inf to 90.0.
fn clamp_latitude(value: f64) -> f64 {
    let upper = if value < 90.0 { value } else { 90.0 };
    if upper > -90.0 {
        upper
    } else {
        -90.0
    }
}

/// Pure decision from ``_cell_geometry``: clamped latitudes, normalized
/// longitudes, and whether the cell needs the antimeridian treatment that
/// RFC 7946 requires of a wrapping polygon.
enum CellGeometry {
    Polygon {
        west: f64,
        east: f64,
        south: f64,
        north: f64,
    },
    MultiPolygon {
        west: f64,
        east: f64,
        south: f64,
        north: f64,
    },
}

fn cell_geometry_core(west: f64, east: f64, south: f64, north: f64) -> CellGeometry {
    let south = clamp_latitude(south);
    let north = clamp_latitude(north);
    let west = normalize_lon180_core(west);
    let east = normalize_lon180_core(east);
    // A normalized west greater than its east means a cell crossing 180
    // degrees, which RFC 7946 forbids as one polygon.
    if west <= east {
        CellGeometry::Polygon {
            west,
            east,
            south,
            north,
        }
    } else {
        CellGeometry::MultiPolygon {
            west,
            east,
            south,
            north,
        }
    }
}

/// A closed, counter-clockwise GeoJSON linear ring of five ``(lon, lat)``
/// points, materialized with Python list objects.
fn linear_ring<'py>(
    py: Python<'py>,
    west: f64,
    east: f64,
    south: f64,
    north: f64,
) -> PyResult<Bound<'py, PyList>> {
    let corners = [
        (west, south),
        (east, south),
        (east, north),
        (west, north),
        (west, south),
    ];
    let mut points: Vec<Bound<'py, PyList>> = Vec::with_capacity(corners.len());
    for (longitude, latitude) in corners {
        points.push(PyList::new(py, [longitude, latitude])?);
    }
    PyList::new(py, points)
}

// ---------------------------------------------------------------------------
// CSV rows
// ---------------------------------------------------------------------------

/// One CSV cell before it becomes a Python object, preserving the element
/// types ``box_csv_rows`` yields: ``int`` for the hour and the grid indices,
/// ``str`` for everything else, floats only after formatting.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum CsvCell {
    Integer(i64),
    Text(String),
}

pub(crate) type CsvRow = Vec<CsvCell>;

/// Port of ``box_csv_rows``: the header first, then one row per node, with an
/// ``fxx`` column inserted first for a sequence and a ``matches`` column
/// appended when a screen's verdicts are exported.
///
/// ``lat`` and ``lon`` are rendered with six fixed decimals and the longitude
/// wrapped into range first, exactly like the Python f-strings; hours are
/// integers except a missing one, which becomes ``""``.
pub(crate) fn csv_rows_core(
    sequence: bool,
    field_keys: &[String],
    include_matches: bool,
    points: &[BoxCsvRowInput],
) -> Vec<CsvRow> {
    let mut rows: Vec<CsvRow> = Vec::with_capacity(points.len() + 1);
    let mut header: CsvRow = Vec::with_capacity(
        IDENTITY_COLUMNS.len()
            + field_keys.len()
            + usize::from(sequence)
            + usize::from(include_matches),
    );
    if sequence {
        header.push(CsvCell::Text("fxx".to_string()));
    }
    for column in IDENTITY_COLUMNS {
        header.push(CsvCell::Text(column.to_string()));
    }
    for key in field_keys {
        header.push(CsvCell::Text(key.clone()));
    }
    if include_matches {
        header.push(CsvCell::Text("matches".to_string()));
    }
    let header_len = header.len();
    rows.push(header);
    for point in points {
        let (hour, request_id, row_index, col_index, lat, lon, status, values, verdict) = point;
        let mut row: CsvRow = Vec::with_capacity(header_len);
        if sequence {
            match hour {
                Some(hour) => row.push(CsvCell::Integer(*hour)),
                None => row.push(CsvCell::Text(String::new())),
            }
        }
        row.push(CsvCell::Text(request_id.clone()));
        row.push(CsvCell::Integer(*row_index));
        row.push(CsvCell::Integer(*col_index));
        row.push(CsvCell::Text(format!("{lat:.6}")));
        row.push(CsvCell::Text(format!("{:.6}", normalize_lon180_core(*lon))));
        row.push(CsvCell::Text(status.clone()));
        // A field the node does not have is an empty cell, never a zero.
        for (index, _key) in field_keys.iter().enumerate() {
            let value = values.get(index).copied().flatten();
            row.push(CsvCell::Text(format_number_core(value)));
        }
        if include_matches {
            let verdict = match verdict {
                Some(true) => "yes",
                Some(false) => "no",
                None => "",
            };
            row.push(CsvCell::Text(verdict.to_string()));
        }
        rows.push(row);
    }
    rows
}

// ---------------------------------------------------------------------------
// Spacing and summary
// ---------------------------------------------------------------------------

/// Port of ``_cell_steps``: the spacing between sampled nodes down each axis,
/// which degenerates to the whole span for a single row or column.
pub(crate) fn cell_steps_core(
    lat_span: f64,
    lon_span: f64,
    rows: usize,
    cols: usize,
) -> (f64, f64) {
    let dlat = if rows > 1 {
        lat_span / (rows as f64 - 1.0)
    } else {
        lat_span
    };
    let dlon = if cols > 1 {
        lon_span / (cols as f64 - 1.0)
    } else {
        lon_span
    };
    (dlat, dlon)
}

/// Port of ``summarize_export``, including its singular/plural wordings.
pub(crate) fn summarize_core(
    pair_count: usize,
    total_nodes: usize,
    first_grid_points: usize,
    field_count: usize,
) -> String {
    if pair_count > 1 {
        format!(
            "{total_nodes} rows ({pair_count} forecast hours x \
             {first_grid_points} points), {field_count} fields"
        )
    } else {
        format!("{total_nodes} points, {field_count} fields")
    }
}

// ---------------------------------------------------------------------------
// PyO3 bindings
// ---------------------------------------------------------------------------

#[pyfunction]
fn box_export_normalize_lon180(lon: f64) -> f64 {
    normalize_lon180_core(lon)
}

#[pyfunction]
fn box_export_format_number(value: Option<f64>) -> String {
    format_number_core(value)
}

#[pyfunction]
fn box_export_cell_steps(lat_span: f64, lon_span: f64, rows: usize, cols: usize) -> (f64, f64) {
    cell_steps_core(lat_span, lon_span, rows, cols)
}

#[pyfunction]
fn box_export_cell_geometry(
    py: Python<'_>,
    west: f64,
    east: f64,
    south: f64,
    north: f64,
) -> PyResult<Py<PyDict>> {
    let document = PyDict::new(py);
    match cell_geometry_core(west, east, south, north) {
        CellGeometry::Polygon {
            west,
            east,
            south,
            north,
        } => {
            let ring = linear_ring(py, west, east, south, north)?;
            let coordinates = PyList::new(py, [ring])?;
            document.set_item("type", "Polygon")?;
            document.set_item("coordinates", coordinates)?;
        }
        CellGeometry::MultiPolygon {
            west,
            east,
            south,
            north,
        } => {
            let west_side = linear_ring(py, west, 180.0, south, north)?;
            let east_side = linear_ring(py, -180.0, east, south, north)?;
            let coordinates = PyList::new(
                py,
                [PyList::new(py, [west_side])?, PyList::new(py, [east_side])?],
            )?;
            document.set_item("type", "MultiPolygon")?;
            document.set_item("coordinates", coordinates)?;
        }
    }
    Ok(document.unbind())
}

#[pyfunction]
fn box_export_csv_rows(
    py: Python<'_>,
    sequence: bool,
    field_keys: Vec<String>,
    include_matches: bool,
    points: Vec<BoxCsvRowInput>,
) -> PyResult<Vec<Py<PyList>>> {
    let rows = csv_rows_core(sequence, &field_keys, include_matches, &points);
    let mut lists = Vec::with_capacity(rows.len());
    for row in rows {
        let list = PyList::empty(py);
        for cell in row {
            match cell {
                CsvCell::Integer(value) => list.append(value)?,
                CsvCell::Text(value) => list.append(value)?,
            }
        }
        lists.push(list.unbind());
    }
    Ok(lists)
}

#[pyfunction]
fn box_export_summarize(
    pair_count: usize,
    total_nodes: usize,
    first_grid_points: usize,
    field_count: usize,
) -> String {
    summarize_core(pair_count, total_nodes, first_grid_points, field_count)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(box_export_normalize_lon180, module)?)?;
    module.add_function(wrap_pyfunction!(box_export_format_number, module)?)?;
    module.add_function(wrap_pyfunction!(box_export_cell_steps, module)?)?;
    module.add_function(wrap_pyfunction!(box_export_cell_geometry, module)?)?;
    module.add_function(wrap_pyfunction!(box_export_csv_rows, module)?)?;
    module.add_function(wrap_pyfunction!(box_export_summarize, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn g6_formats_ordinary_values_without_padded_zeros() {
        for (value, expected) in [
            (0.0, "0"),
            (1.0, "1"),
            (-1.0, "-1"),
            (0.5, "0.5"),
            (-0.5, "-0.5"),
            (1.23, "1.23"),
            (-1.23, "-1.23"),
            (1.5, "1.5"),
            (15.0, "15"),
            (12.5, "12.5"),
            (100.0, "100"),
            (-100.0, "-100"),
            (1000.0, "1000"),
            (123.456, "123.456"),
            (-123.456, "-123.456"),
            (1234.5625, "1234.56"),
            (14.0625, "14.0625"),
            (12.0625, "12.0625"),
            (0.1875, "0.1875"),
            (0.125, "0.125"),
            (0.09375, "0.09375"),
            (10.9375, "10.9375"),
            (150000.0, "150000"),
            (100000.0, "100000"),
        ] {
            assert_eq!(format_g6(value), expected.to_string());
        }
    }

    #[test]
    fn g6_switches_presentations_at_the_python_boundaries() {
        // Fixed holds down to 1e-4; scientific starts at 1e-5.
        for (value, expected) in [
            (0.001, "0.001"),
            (0.01, "0.01"),
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (-1e-5, "-1e-05"),
            (0.000123456, "0.000123456"),
            (-0.000123456, "-0.000123456"),
            (0.0000123456, "1.23456e-05"),
            (-0.0000123456, "-1.23456e-05"),
            (1e-6, "1e-06"),
            // Fixed holds up to 999999; scientific starts at 1000000.
            (123456.0, "123456"),
            (-123456.0, "-123456"),
            (999999.0, "999999"),
            (999999.5, "1e+06"),
            (999998.5, "999998"),
            (999999.49999, "999999"),
            (1234567.0, "1.23457e+06"),
            (-1234567.0, "-1.23457e+06"),
            (1234567.5, "1.23457e+06"),
            (1e6, "1e+06"),
            (1.4e6, "1.4e+06"),
            (1e16, "1e+16"),
            (1e15, "1e+15"),
            (1.5e15, "1.5e+15"),
        ] {
            assert_eq!(format_g6(value), expected.to_string());
        }
    }

    #[test]
    fn g6_rounds_the_last_kept_digit_like_cpython() {
        for (value, expected) in [
            (9.999999, "10"),
            (-9.999999, "-10"),
            (0.333_333_333_333_333_3, "0.333333"),
            (0.666_666_666_666_666_6, "0.666667"),
            (0.1, "0.1"),
            (0.3, "0.3"),
            (0.7, "0.7"),
            (999.9996, "1000"),
            (999999.9996, "1e+06"),
            (0.9999995, "1"),
            (9999995.5, "1e+07"),
            (9999994.0, "9.99999e+06"),
            (9999995.0, "1e+07"),
            (999999500.0, "1e+09"),
            (1.5000001, "1.5"),
            (999999.0161779785, "999999"),
            (123456.5, "123456"),
            (123456.4, "123456"),
            (118.03125, "118.031"),
        ] {
            assert_eq!(format_g6(value), expected.to_string());
        }
    }

    #[test]
    fn g6_breaks_ties_to_even_from_the_exact_binary_value() {
        // Entirely representable doubles whose seventh significant digit is a
        // lone 5: CPython rounds half to even, so parity decides.
        for (value, expected) in [
            (1234565.0, "1.23456e+06"),
            (1234575.0, "1.23458e+06"),
            (12345650.0, "1.23456e+07"),
            (12345655.0, "1.23457e+07"),
            (1.203125, "1.20312"),
            (-1.203125, "-1.20312"),
            (1.296875, "1.29688"),
            (12.03125, "12.0312"),
            (19.03125, "19.0312"),
        ] {
            assert_eq!(format_g6(value), expected.to_string());
        }
    }

    #[test]
    fn g6_spells_special_values_and_extremes_like_python() {
        for (value, expected) in [
            (f64::NAN, "nan"),
            (-f64::NAN, "nan"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
            (-0.0, "-0"),
            (5e-324, "4.94066e-324"),
            (f64::from_bits(1), "4.94066e-324"),
            (2.225_073_858_507_201_4e-308, "2.22507e-308"),
            (1.797_693_134_862_315_7e308, "1.79769e+308"),
        ] {
            assert_eq!(format_g6(value), expected.to_string());
        }
        // Exponents are signed and zero-padded to at least two digits.
        for (value, expected) in [
            (1e-9, "1e-09"),
            (1.5e-7, "1.5e-07"),
            (5.5e-10, "5.5e-10"),
            (1e100, "1e+100"),
            (2e100, "2e+100"),
            (1e-100, "1e-100"),
            (1e-300, "1e-300"),
            (2e-300, "2e-300"),
            (9007199254740992.0, "9.0072e+15"),
            (9007199254740991.0, "9.0072e+15"),
            (2_147_483_648.0, "2.14748e+09"),
            (-2_147_483_648.0, "-2.14748e+09"),
        ] {
            assert_eq!(format_g6(value), expected.to_string());
        }
    }

    #[test]
    fn format_number_writes_absence_as_an_empty_cell() {
        assert_eq!(format_number_core(None), "");
        assert_eq!(format_number_core(Some(1234.5678)), "1234.57");
        assert_eq!(format_number_core(Some(0.0)), "0");
        assert_eq!(format_number_core(Some(-0.0)), "-0");
    }

    #[test]
    fn normalize_lon180_matches_the_export_convention() {
        for (value, expected) in [
            (-95.66, -95.66),
            (190.0, -170.0),
            (-190.0, 170.0),
            (180.0, -180.0),
            (-180.0, -180.0),
            (185.0, -175.0),
            (360.0, 0.0),
            (0.0, 0.0),
        ] {
            assert_eq!(normalize_lon180_core(value), expected);
        }
        assert!(normalize_lon180_core(f64::NAN).is_nan());
    }

    #[test]
    fn latitudes_clamp_like_python_min_max_argument_order() {
        for (value, expected) in [
            (36.0, 36.0),
            (90.0, 90.0),
            (100.0, 90.0),
            (-90.0, -90.0),
            (-100.0, -90.0),
            (f64::INFINITY, 90.0),
            (f64::NEG_INFINITY, -90.0),
        ] {
            assert_eq!(clamp_latitude(value), expected);
        }
        // NaN clamps to 90.0 because min(90.0, nan) keeps its left argument.
        assert_eq!(clamp_latitude(f64::NAN), 90.0);
    }

    #[test]
    fn cell_steps_divide_the_span_between_samples() {
        // A 2-degree span across three rows is one degree between samples.
        assert_eq!(cell_steps_core(2.0, 3.0, 3, 4), (1.0, 1.0));
        assert_eq!(cell_steps_core(2.5, 0.0, 3, 1), (1.25, 0.0));
        // A single row or column degenerates to the whole span.
        assert_eq!(cell_steps_core(2.5, 3.5, 1, 5), (2.5, 0.875));
        assert_eq!(cell_steps_core(2.5, 3.5, 1, 1), (2.5, 3.5));
        assert_eq!(cell_steps_core(0.0, 0.0, 9, 9), (0.0, 0.0));
    }

    #[test]
    fn cell_geometry_emits_a_closed_clamped_polygon() {
        let geometry = cell_geometry_core(-96.55, -96.45, 36.0, 36.1);
        match geometry {
            CellGeometry::Polygon {
                west,
                east,
                south,
                north,
            } => {
                assert_eq!(west, -96.55);
                assert_eq!(east, -96.45);
                assert_eq!(south, 36.0);
                assert_eq!(north, 36.1);
            }
            CellGeometry::MultiPolygon { .. } => panic!("interior cell split at 180"),
        }
        // Pole-spanning cells clamp instead of emitting invalid coordinates.
        match cell_geometry_core(10.0, 40.0, 84.0, 95.0) {
            CellGeometry::Polygon {
                west,
                east,
                south,
                north,
            } => {
                assert_eq!(west, 10.0);
                assert_eq!(east, 40.0);
                assert_eq!(south, 84.0);
                assert_eq!(north, 90.0);
            }
            CellGeometry::MultiPolygon { .. } => panic!("clamped cell must not split"),
        }
        // Longitudes normalize before the split decision.
        match cell_geometry_core(170.0, 190.0, 50.0, 56.0) {
            CellGeometry::MultiPolygon { west, east, .. } => {
                assert_eq!(west, 170.0);
                assert_eq!(east, -170.0);
            }
            CellGeometry::Polygon { .. } => panic!("cell crossing 180 must split"),
        }
    }

    #[test]
    fn cell_geometry_nan_edges_follow_the_python_paths() {
        // A NaN west/east never satisfies west <= east, so the split branch
        // is the one Python takes too, carrying the NaN coordinates.
        match cell_geometry_core(f64::NAN, f64::NAN, 0.0, 1.0) {
            CellGeometry::MultiPolygon { west, east, .. } => {
                assert!(west.is_nan() && east.is_nan());
            }
            CellGeometry::Polygon { .. } => panic!("NaN longitudes must take the split"),
        }
        // A NaN latitude clamps to 90.0 on both ends.
        match cell_geometry_core(0.0, 1.0, f64::NAN, f64::NAN) {
            CellGeometry::Polygon { south, north, .. } => {
                assert_eq!(south, 90.0);
                assert_eq!(north, 90.0);
            }
            CellGeometry::MultiPolygon { .. } => panic!("unit cell must not split"),
        }
        // Negative infinity clamps to the south pole; NaN remains the 90.0
        // from the north clamp.
        match cell_geometry_core(-1.0, 1.0, f64::NEG_INFINITY, f64::NAN) {
            CellGeometry::Polygon { south, north, .. } => {
                assert_eq!(south, -90.0);
                assert_eq!(north, 90.0);
            }
            CellGeometry::MultiPolygon { .. } => panic!("unit cell must not split"),
        }
    }

    #[test]
    fn csv_rows_build_identity_then_fields_for_one_hour() {
        let points = [sample_point()];
        let rows = csv_rows_core(
            false,
            &["mucape".to_string(), "shear_6km".to_string()],
            false,
            &points,
        );
        assert_eq!(rows.len(), 2);
        assert_eq!(
            rows[0],
            [
                CsvCell::Text("request_id".to_string()),
                CsvCell::Text("row".to_string()),
                CsvCell::Text("col".to_string()),
                CsvCell::Text("lat".to_string()),
                CsvCell::Text("lon".to_string()),
                CsvCell::Text("status".to_string()),
                CsvCell::Text("mucape".to_string()),
                CsvCell::Text("shear_6km".to_string()),
            ]
        );
        // Row/col stay integers; lat/lon render six fixed decimals with the
        // longitude wrapped first; absence stays empty; runs trace to CPython.
        assert_eq!(
            rows[1],
            [
                CsvCell::Text("hrrr-f018-r00-c02".to_string()),
                CsvCell::Integer(0),
                CsvCell::Integer(2),
                CsvCell::Text("36.680000".to_string()),
                CsvCell::Text("-95.666000".to_string()),
                CsvCell::Text("ok".to_string()),
                CsvCell::Text("1234.57".to_string()),
                CsvCell::Text("".to_string()),
            ]
        );
        // A missing hour is not even offered when the export is not a sequence.
        assert!(!rows[1]
            .iter()
            .any(|cell| matches!(cell, CsvCell::Integer(18))));
    }

    #[test]
    fn csv_rows_render_latitudes_and_longitudes_like_the_fstrings() {
        let mut point = sample_point();
        point.4 = 38.7700004;
        point.5 = 179.99999946;
        let rows = csv_rows_core(false, &[], false, &[point.clone()]);
        assert_eq!(rows[1][3], CsvCell::Text("38.770000".to_string()));
        assert_eq!(rows[1][4], CsvCell::Text("179.999999".to_string()));
        point.4 = -0.0;
        point.5 = 185.0;
        let rows = csv_rows_core(false, &[], false, &[point]);
        // Negative zero keeps its sign; dateline longitudes wrap to -180..180.
        assert_eq!(rows[1][3], CsvCell::Text("-0.000000".to_string()));
        assert_eq!(rows[1][4], CsvCell::Text("-175.000000".to_string()));
    }

    #[test]
    fn csv_rows_add_sequence_and_matches_columns_like_python() {
        let mut point = sample_point();
        point.0 = Some(18);
        point.8 = Some(true);
        let rows = csv_rows_core(true, &["mucape".to_string()], true, &[point.clone()]);
        assert_eq!(
            rows[0],
            [
                CsvCell::Text("fxx".to_string()),
                CsvCell::Text("request_id".to_string()),
                CsvCell::Text("row".to_string()),
                CsvCell::Text("col".to_string()),
                CsvCell::Text("lat".to_string()),
                CsvCell::Text("lon".to_string()),
                CsvCell::Text("status".to_string()),
                CsvCell::Text("mucape".to_string()),
                CsvCell::Text("matches".to_string()),
            ]
        );
        let expected: [CsvCell; 9] = [
            CsvCell::Integer(18),
            CsvCell::Text("hrrr-f018-r00-c02".to_string()),
            CsvCell::Integer(0),
            CsvCell::Integer(2),
            CsvCell::Text("36.680000".to_string()),
            CsvCell::Text("-95.666000".to_string()),
            CsvCell::Text("ok".to_string()),
            CsvCell::Text("1234.57".to_string()),
            CsvCell::Text("yes".to_string()),
        ];
        assert_eq!(rows[1], expected.to_vec());
        // The native verdict spellings cover every verdict state.
        point.8 = Some(false);
        let rows = csv_rows_core(true, &[], true, &[point.clone()]);
        assert_eq!(rows[1].last(), Some(&CsvCell::Text("no".to_string())));
        point.8 = None;
        point.0 = None;
        let rows = csv_rows_core(true, &[], true, &[point]);
        // An hour-less sequence row still lands in the file, as an empty fxx.
        assert_eq!(rows[1][0], CsvCell::Text(String::new()));
        assert_eq!(rows[1].last(), Some(&CsvCell::Text(String::new())));
    }

    #[test]
    fn csv_rows_keep_the_header_first_across_many_nodes() {
        let mut points = [sample_point(), sample_point(), sample_point()];
        // Every node keeps its own identity, so the exported rows stay
        // distinguishable even when the request ids differ.
        points[1].1 = "other-node".to_string();
        let rows = csv_rows_core(false, &[], false, &points);
        assert_eq!(rows.len(), 4);
        assert_eq!(rows[0].len(), 6);
        for row in &rows[1..] {
            assert_eq!(row.len(), 6);
            assert_ne!(row[0], CsvCell::Text("request_id".to_string()));
        }
        assert_eq!(rows[1][0], CsvCell::Text("hrrr-f018-r00-c02".to_string()));
        assert_eq!(rows[2][0], CsvCell::Text("other-node".to_string()));
    }

    #[test]
    fn summarize_wordings_match_the_python_export() {
        assert_eq!(summarize_core(1, 360, 360, 4), "360 points, 4 fields");
        assert_eq!(
            summarize_core(2, 720, 360, 3),
            "720 rows (2 forecast hours x 360 points), 3 fields"
        );
        assert_eq!(
            summarize_core(12, 4320, 360, 9),
            "4320 rows (12 forecast hours x 360 points), 9 fields"
        );
        // The sequence summary only switches once more than one hour exists.
        assert_eq!(
            summarize_core(2, 10, 5, 0),
            "10 rows (2 forecast hours x 5 points), 0 fields"
        );
    }

    fn sample_point() -> BoxCsvRowInput {
        (
            None,
            "hrrr-f018-r00-c02".to_string(),
            0,
            2,
            36.68,
            -95.666,
            "ok".to_string(),
            vec![Some(1234.5678), None],
            None,
        )
    }
}

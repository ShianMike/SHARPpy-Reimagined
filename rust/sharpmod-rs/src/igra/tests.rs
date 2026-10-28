use super::*;

// Captured NCEI records, also used by the existing public reader checks.
const HEADER: &str = "#USM00072357 2026 01 01 00 2302  217 ncdc-nws ncdc-gts  351808  -974378";
const LEVEL: &str = "21     0  97788B  345   176B  260   197   235    15";

#[test]
fn decodes_published_header_and_level_units() {
    let row = header(HEADER, 7);
    assert_eq!(row.station, "USM00072357");
    assert_eq!(row.date, [2026, 1, 1, 0]);
    assert_eq!((row.release, row.levels, row.line), (2302, 217, 7));
    assert_eq!(row.sources, ["ncdc-nws", "ncdc-gts"]);
    assert_eq!(row.position, [35.1808, -97.4378]);

    let row = level(LEVEL).unwrap().unwrap();
    assert_eq!(row.values[..3], [977.88, 345., 17.6]);
    assert!((row.values[3] - -2.1).abs() < 1e-12);
    assert_eq!(row.values[4], 235.);
    assert_eq!(row.values[5], 1.5 * (3600. / 1852.));
    assert!(row.surface);
    assert!(!row.reconstructed);
    assert!(level("short").unwrap().is_none());
    assert!(level(&LEVEL.replace("97788", "-9999")).unwrap().is_none());
    assert!(level(&LEVEL.replace("97788", "-8888")).unwrap().is_none());

    let partial = level("21     0  97788B  345").unwrap().unwrap();
    assert_eq!(partial.values[2..], [MISSING; 4]);
    assert_eq!(
        level(&LEVEL.replace("97788", "９７７８８"))
            .unwrap()
            .unwrap()
            .values,
        row.values
    );
    assert_eq!(
        level(&LEVEL.replace("97788", "9_778"))
            .unwrap()
            .unwrap()
            .values[0],
        97.78
    );
}

#[test]
fn streams_only_decreasing_pressure_and_counts_reconstructed_and_dropped_levels() {
    let lower = LEVEL.replace("97788", "95000");
    let reconstructed = LEVEL.replace("97788", "92500").replace("  197", "-9999");
    let absent = LEVEL.replace("97788", "-8888");
    let text = [
        HEADER,
        LEVEL,
        &lower,
        &lower,
        LEVEL,
        &absent,
        &reconstructed,
        HEADER,
        LEVEL,
    ]
    .join("\n");
    let rows = sounding_rows(&text, 0, 99).unwrap();
    assert_eq!(rows.columns[0], [977.88, 950., 925.]);
    assert!(rows.columns.iter().all(|column| column.len() == 3));
    assert_eq!((rows.reconstructed, rows.dropped), (1, 3));
    assert_eq!(rows.columns[3][2], dewpoint(17.6, 26.).unwrap());
    assert_eq!(sounding_rows(&text, 0, 1).unwrap().columns[0], [977.88]);
    assert!(sounding_rows(&text, 0, -1).unwrap().columns[0].is_empty());
    assert!(sounding_rows(&text, 8, 1).unwrap().columns[0].is_empty());
    assert!(matches!(sounding_rows(&text, 9, 1), Err(Error::PastEnd)));
}

#[test]
fn matches_python_line_boundaries_and_header_missing_positions() {
    for separator in [
        "\n", "\r\n", "\r", "\u{000b}", "\u{000c}", "\u{001c}", "\u{001d}", "\u{001e}", "\u{0085}",
        "\u{2028}", "\u{2029}",
    ] {
        let text = ["padding", HEADER, LEVEL, ""].join(separator);
        let headers = headers(&text);
        assert_eq!(headers.len(), 1, "{separator:?}");
        assert_eq!(headers[0].line, 1, "{separator:?}");
        let rows = sounding_rows(&text, 1, 217).unwrap();
        assert_eq!(rows.columns[0], [977.88], "{separator:?}");
        assert_eq!(rows.dropped, 0);
    }
    let row = header("#", 0);
    assert_eq!(row.date, [0, 0, 0, 99]);
    assert_eq!((row.release, row.levels), (9999, 0));
    assert!(row.position.iter().all(|coordinate| coordinate.is_nan()));
    let mobile = HEADER.replace(" 351808  -974378", "-988888 -9988888");
    assert!(header(&mobile, 0)
        .position
        .iter()
        .all(|coordinate| coordinate.is_nan()));
}

#[test]
fn preserves_humidity_guards_and_math_failures() {
    assert!((dewpoint(20., 50.).unwrap() - 9.261_106_630_534_236).abs() < 1e-10);
    assert_eq!(dewpoint(20., 140.).unwrap(), dewpoint(20., 100.).unwrap());
    for humidity in [0., -5., f64::NAN, f64::INFINITY] {
        assert_eq!(dewpoint(20., humidity).unwrap(), MISSING);
    }
    assert_eq!(dewpoint(f64::NAN, 50.).unwrap(), MISSING);
    assert!(matches!(dewpoint(-243.04, 50.), Err(Error::DivisionByZero)));
    assert!(matches!(dewpoint(-243.1, 50.), Err(Error::Overflow)));
    assert!(dewpoint(f64::MAX, 50.).unwrap().is_nan());
}

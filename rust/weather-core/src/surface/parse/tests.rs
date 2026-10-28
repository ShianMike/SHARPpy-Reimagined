use super::*;
use serde_json::json;

#[test]
fn converts_documented_units_without_inventing_missing_values() {
    for (raw, code, expected) in [(20., "degC", 20.), (68., "degF", 20.), (293.15, "K", 20.)] {
        assert!(
            (quantity(
                Some(&json!({"value":raw,"unitCode":format!("wmoUnit:{code}")})),
                Unit::Celsius
            )
            .unwrap()
                - expected)
                .abs()
                < 1e-10
        );
    }
    for (code, multiplier) in [
        ("m_s-1", 1.9438444924406),
        ("m_s^-1", 1.9438444924406),
        ("km_h-1", 0.539956803),
        ("kt", 1.),
        ("mph", 0.868976242),
    ] {
        assert_eq!(
            quantity(Some(&json!({"value":10.,"unitCode":code})), Unit::Knots),
            Some(10. * multiplier)
        );
    }
    assert_eq!(
        quantity(
            Some(&json!({"value":-90.,"unitCode":"degree_(angle)"})),
            Unit::Degrees
        ),
        Some(270.)
    );
    assert_eq!(
        quantity(Some(&json!({"value":100.,"unitCode":"ft"})), Unit::Metres),
        Some(30.48)
    );
    assert_eq!(
        quantity(Some(&json!({"value":0.,"unitCode":"kt"})), Unit::Knots),
        Some(0.)
    );
    for value in [
        json!(null),
        json!({"value":null,"unitCode":"kt"}),
        json!({"value":"NaN","unitCode":"kt"}),
        json!({"value":10,"unitCode":"unknown"}),
    ] {
        assert_eq!(quantity(Some(&value), Unit::Knots), None);
    }
}

#[test]
fn station_rows_preserve_order_metadata_and_reject_invalid_positions() {
    let valid = json!({"id":"https://api.weather.gov/stations/KAAA","geometry":{"coordinates":[-97,35]},"properties":{"stationIdentifier":" kaaa ","name":"Test Station","elevation":{"value":300,"unitCode":"m"}}});
    let mut invalid = valid.clone();
    invalid["geometry"]["coordinates"][1] = json!(91);
    let stations = stations(&json!({"features":[null,invalid,valid]}));
    assert_eq!(stations.len(), 1);
    assert_eq!(stations[0].id, "KAAA");
    assert_eq!(stations[0].name, "Test Station");
    assert_eq!(stations[0].elevation_m, Some(300.));
    assert_eq!((stations[0].lat, stations[0].lon), (35., -97.));
}

#[test]
fn observations_keep_actual_utc_times_stable_order_and_calm_missing_distinction() {
    let rows = observations(&json!({"features":[
        {"properties":{"timestamp":"2026-09-13T03:35:00+02:00","windSpeed":{"value":null,"unitCode":"kt"}}},
        {"id":"calm","properties":{"timestamp":"2026-09-13T01:30:00Z","windSpeed":{"value":0,"unitCode":"kt"},"windDirection":{"value":null,"unitCode":"degree_(angle)"}}},
        {"properties":{"timestamp":"bad"}},
        {"properties":{"timestamp":"0000-09-13T00:00:00Z"}},
        {"properties":{"timestamp":"2026-09-13T01:35:00Z","textDescription":"same time, later row"}}
    ]}));
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0].source_url, "calm");
    assert_eq!(rows[0].speed_kt, Some(0.));
    assert_eq!(rows[0].direction_deg, None);
    assert_eq!(rows[1].speed_kt, None);
    assert_eq!(rows[1].observed_at, rows[2].observed_at);
    assert_eq!(rows[1].observed_at.offset(), UtcOffset::UTC);
    assert_eq!(rows[2].text, "same time, later row");
}

#[test]
fn nearest_match_chooses_older_on_ties_and_keeps_inclusive_tolerance() {
    let requested = OffsetDateTime::parse("2026-09-13T01:45:00Z", &Rfc3339).unwrap();
    let times = [
        requested + Duration::minutes(60),
        requested - Duration::minutes(60),
    ];
    assert_eq!(nearest_index(times, requested, Duration::hours(1)), Some(1));
    assert_eq!(nearest_index(times, requested, Duration::minutes(59)), None);
    assert_eq!(nearest_index([], requested, Duration::hours(1)), None);
    assert_eq!(
        nearest_index([requested, requested], requested, Duration::ZERO),
        Some(0)
    );
}

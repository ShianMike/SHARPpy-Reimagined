use super::*;

#[test]
fn cape_clipping_and_heating_match_pinned_soundings() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/convective_temperature.json"
    ))
    .unwrap();
    for source in fixture.as_array().unwrap() {
        let columns = ["pres", "hght", "tmpc", "dwpc", "vtmp"].map(|name| {
            source["columns"][name]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_f64().unwrap_or(f64::NAN))
                .collect::<Vec<_>>()
        });
        let profile = TemperatureSearch::new(
            columns.each_ref().map(Vec::as_slice),
            source["sfc"].as_u64().unwrap() as usize,
            None,
        )
        .unwrap();
        for case in source["cape_checks"].as_array().unwrap() {
            let start = (
                source["start_pressure"].as_f64().unwrap(),
                case["temperature_c"].as_f64().unwrap(),
                source["start_dewpoint"].as_f64().unwrap(),
            );
            let actual = profile.cape(start).unwrap();
            for (actual, name) in [(actual.0, "reference_cape"), (actual.1, "reference_cin")] {
                let expected = case[name].as_f64().unwrap();
                assert!(
                    (actual - expected).abs() < 1e-6,
                    "{} {start:?} {name}: {actual} != {expected}",
                    source["sample"]
                );
            }
        }
        let mixing_ratio = profile.mean_mixing_ratio();
        assert!((mixing_ratio - source["mean_mixing_ratio"].as_f64().unwrap()).abs() < 1e-10);
        let actual = profile.convective_temperature(mixing_ratio);
        assert_eq!(actual, source["convective_temperature_c"].as_f64());
        let mut gap_columns = columns.clone();
        gap_columns[3][1] = f64::NAN;
        let gap = TemperatureSearch::new(
            gap_columns.each_ref().map(Vec::as_slice),
            source["sfc"].as_u64().unwrap() as usize,
            None,
        )
        .unwrap();
        let ratio = gap.mean_mixing_ratio();
        assert!((ratio - source["gap_mean_mixing_ratio"].as_f64().unwrap()).abs() < 1e-10);
        assert_eq!(
            gap.convective_temperature(ratio),
            source["gap_convective_temperature_c"].as_f64()
        );
    }
}

#[test]
fn malformed_columns_and_missing_environment_cannot_produce_a_temperature() {
    let p = [1000., 800.];
    let h = [0., 2000.];
    let t = [25., 10.];
    let td = [20., 5.];
    let missing = [f64::NAN; 2];
    assert!(TemperatureSearch::new([&p, &h, &t, &[], &t], 0, None).is_err());
    assert!(TemperatureSearch::new([&p, &h, &t, &td, &t], 2, None).is_err());
    let profile = TemperatureSearch::new([&p, &h, &t, &td, &missing], 0, None).unwrap();
    assert!(profile.cape((1000., 25., 20.)).is_none());
    assert!(profile
        .convective_temperature(profile.mean_mixing_ratio())
        .is_none());
}

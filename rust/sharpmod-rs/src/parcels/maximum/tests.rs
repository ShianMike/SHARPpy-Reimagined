use super::*;

#[test]
fn native_maximum_levels_match_the_existing_python_example() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/maximum_parcel_level.json"
    ))
    .unwrap();
    let column = |name: &str| {
        fixture[name]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_f64().unwrap_or(f64::NAN))
            .collect::<Vec<_>>()
    };
    let columns = [
        column("pres"),
        column("hght"),
        column("tmpc"),
        column("vtmp"),
    ];
    for parcel in fixture["parcels"].as_array().unwrap() {
        let start = parcel["start"].as_array().unwrap();
        let result = maximum_parcel_level(
            columns.each_ref().map(Vec::as_slice),
            (
                start[0].as_f64().unwrap(),
                start[1].as_f64().unwrap(),
                start[2].as_f64().unwrap(),
            ),
            parcel["el"].as_f64().unwrap(),
            0,
            None,
        )
        .unwrap();
        assert_eq!(
            result.0,
            parcel["maximum"][0].as_f64().unwrap(),
            "{}",
            parcel["kind"]
        );
        assert!(
            (result.1 - parcel["maximum"][1].as_f64().unwrap()).abs() < 1e-6,
            "{}: {result:?}",
            parcel["kind"]
        );
        let raised: Vec<_> = columns[1].iter().map(|value| value + 2300.).collect();
        let raised_result = maximum_parcel_level(
            [&columns[0], &raised, &columns[2], &columns[3]],
            (
                start[0].as_f64().unwrap(),
                start[1].as_f64().unwrap(),
                start[2].as_f64().unwrap(),
            ),
            parcel["el"].as_f64().unwrap(),
            0,
            None,
        )
        .unwrap();
        assert_eq!(raised_result.0, result.0);
        assert!((raised_result.1 - result.1).abs() < 1e-6);
        let top = result.0 + 0.01;
        let clipped: Vec<Vec<f64>> = columns
            .iter()
            .enumerate()
            .map(|(field, values)| {
                let mut column: Vec<_> = columns[0]
                    .iter()
                    .zip(values)
                    .filter(|(pressure, _)| **pressure > top)
                    .map(|(_, value)| *value)
                    .collect();
                column.push(if field == 0 {
                    top
                } else {
                    PressureSeries::new(&columns[0], values, None).at(top)
                });
                column
            })
            .collect();
        let edge = maximum_parcel_level(
            [&clipped[0], &clipped[1], &clipped[2], &clipped[3]],
            (
                start[0].as_f64().unwrap(),
                start[1].as_f64().unwrap(),
                start[2].as_f64().unwrap(),
            ),
            parcel["el"].as_f64().unwrap(),
            0,
            None,
        )
        .unwrap();
        assert_eq!(edge.0, result.0);
        assert!(edge.1.is_nan());
    }
}

#[test]
fn incomplete_and_invalid_profiles_cannot_invent_a_maximum_level() {
    let pressure = [1000., 900., 800.];
    let height = [0., 1000., 2000.];
    let temperature = [20., 15., 10.];
    let columns = [
        &pressure[..],
        &height[..],
        &temperature[..],
        &temperature[..],
    ];
    for el in [f64::NAN, -9999., 0.] {
        let result = maximum_parcel_level(columns, (1000., 20., 15.), el, 0, Some(-9999.)).unwrap();
        assert!(result.0.is_nan() && result.1.is_nan());
    }
    assert!(maximum_parcel_level(columns, (1000., 20., 15.), 500., 3, None).is_err());
    assert!(maximum_parcel_level(
        [&pressure[..2], &height, &temperature, &temperature],
        (1000., 20., 15.),
        500.,
        0,
        None
    )
    .is_err());
    let result = maximum_parcel_level(columns, (1000., 20., 15.), 500., 0, None).unwrap();
    assert!(result.0.is_nan() && result.1.is_nan());
}

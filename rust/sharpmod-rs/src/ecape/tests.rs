use super::*;
use serde_json::Value;

fn fixtures() -> Value {
    serde_json::from_str(include_str!("../../tests/data/ecape_metpy171.json")).unwrap()
}

fn columns(case: &Value) -> [Vec<f64>; 6] {
    std::array::from_fn(|index| {
        case["columns"][index]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_f64().unwrap())
            .collect()
    })
}

#[test]
fn recorded_real_and_synthetic_profiles_match_metpy_171_workspace() {
    for case in fixtures()["cases"].as_array().unwrap() {
        let actual = building_blocks(columns(case)).unwrap();
        let expected = &case["blocks"];
        if expected.is_null() {
            assert!(actual.is_none(), "{}: {actual:?}", case["name"]);
            continue;
        }
        let actual = actual.unwrap_or_else(|| panic!("{}: missing workspace", case["name"]));
        // The reference uses LSODA. Bound its numerical solver difference far
        // below the published ECAPE allowance of max(5%, 10 J/kg).
        assert!(
            (actual.cape - expected["cape"].as_f64().unwrap()).abs() < 0.1,
            "{}: {actual:?}, expected {expected}",
            case["name"]
        );
        for (name, value, tolerance) in [
            ("ncape", actual.ncape, 1e-7),
            ("sr_wind", actual.sr_wind, 1e-9),
            ("el_z", actual.el_z, 1e-8),
        ] {
            match (value, expected[name].as_f64()) {
                (Some(a), Some(b)) => assert!(
                    (a - b).abs() < tolerance,
                    "{} {name}: {a} versus {b}",
                    case["name"]
                ),
                (None, None) => {}
                values => panic!("{} {name}: {values:?}", case["name"]),
            }
        }
    }
}

#[test]
fn parcel_and_condensation_levels_match_the_independent_reference() {
    for case in fixtures()["cases"].as_array().unwrap() {
        let [p, _, mut t, mut td, _, _] = columns(case);
        for value in t.iter_mut().chain(&mut td) {
            *value += 273.15;
        }
        for i in 0..td.len() {
            td[i] = thermodynamics::dewpoint_from_humidity(
                p[i],
                thermodynamics::specific_humidity(p[i], td[i]),
            );
        }
        let index = buoyancy::most_unstable(&p, &t, &td).unwrap();
        assert_eq!(
            index as u64,
            case["mu_index"].as_u64().unwrap(),
            "{}",
            case["name"]
        );
        let (pl, tl) = thermodynamics::lcl(p[0], t[index], td[index]).unwrap();
        assert!(
            (pl - case["lcl"][0].as_f64().unwrap()).abs() < 1e-8,
            "{}: {pl}",
            case["name"]
        );
        assert!(
            (tl - case["lcl"][1].as_f64().unwrap()).abs() < 1e-8,
            "{}: {tl}",
            case["name"]
        );
        let trace = thermodynamics::parcel_profile(&p, t[index], td[index]).unwrap();
        let error = trace
            .iter()
            .zip(case["parcel_trace_k"].as_array().unwrap())
            .map(|(actual, expected)| (actual - expected.as_f64().unwrap()).abs())
            .fold(0_f64, f64::max);
        assert!(
            error < 0.0001,
            "{}: parcel temperature error {error}",
            case["name"]
        );
    }
}

#[test]
fn malformed_columns_fail_at_the_native_boundary() {
    let data = fixtures();
    let valid = columns(&data["cases"][0]);
    let mut short = valid.clone();
    short[1].pop();
    assert!(building_blocks(short).is_err());
    for invalid in [f64::NAN, f64::INFINITY, 0., -9999.] {
        let mut changed = valid.clone();
        changed[0][0] = invalid;
        assert!(building_blocks(changed).is_err());
    }
    let mut reversed = valid;
    reversed[0].reverse();
    assert!(building_blocks(reversed).is_err());
    assert!(building_blocks(std::array::from_fn(|_| vec![1., 2.])).is_err());
}

#[test]
fn profile_above_the_freezing_stratosphere_keeps_the_same_integral() {
    let data = fixtures();
    let cases = data["cases"].as_array().unwrap();
    let get = |name: &str| {
        building_blocks(columns(cases.iter().find(|c| c["name"] == name).unwrap()))
            .unwrap()
            .unwrap()
    };
    let original = get("unstable");
    let extended = get("upper_stratosphere");
    assert!((original.cape - extended.cape).abs() < 1e-8);
    assert_eq!(original.ncape, extended.ncape);
    assert_eq!(original.el_z, extended.el_z);
}

#[test]
fn sparse_and_duplicate_pressure_profiles_do_not_panic() {
    assert_eq!(
        buoyancy::most_unstable(
            &[1000., 1000., 600.],
            &[300., 300., 230.],
            &[290., 290., 210.]
        ),
        Some(0)
    );
    let data = fixtures();
    let valid = columns(&data["cases"][6]);
    let mut duplicate = valid.clone();
    for column in &mut duplicate {
        column.insert(2, column[1]);
    }
    let a = building_blocks(valid.clone()).unwrap().unwrap();
    let b = building_blocks(duplicate).unwrap().unwrap();
    assert!((a.cape - b.cape).abs() < 0.02);
    // NCAPE deliberately uses a reported-level cumulative mean, so duplicating
    // a level can change NCAPE; do not incorrectly assert resampling invariance.
    for length in 3..valid[0].len() {
        let clipped = valid.clone().map(|mut column| {
            column.truncate(length);
            column
        });
        let _ = building_blocks(clipped).unwrap();
    }
}

use super::{
    explicit_parcel, profile_convective_parcels, profile_dcape, profile_parcels,
    profile_thermodynamic_diagnostics, profile_thermodynamics, CONVECTIVE_PARCEL_COUNT,
    PARCEL_COUNT, PARCEL_WIDTH,
};
use super::{ParcelProfile, PressureSeries};

#[test]
fn bundled_soundings_preserve_thermodynamic_diagnostics() {
    for (name, text) in [
        (
            "observed_oax",
            include_str!("../../../../examples/soundings/14061619.OAX"),
        ),
        (
            "forecast_hrrr",
            include_str!("../../../../examples/soundings/hrrr_point_36.68N_95.66W_f018.spc"),
        ),
    ] {
        let raw = text
            .split_once("%RAW%")
            .unwrap()
            .1
            .split_once("%END%")
            .unwrap()
            .0;
        let rows = crate::parsing::parse_sounding_rows(raw, -9999.0).unwrap();
        let columns: [Vec<f64>; 4] =
            std::array::from_fn(|column| rows.iter().map(|row| row[column]).collect());
        let [pressure, height, temperature, dewpoint] = columns;
        let surface = temperature
            .iter()
            .position(|value| value.is_finite() && *value != -9999.0)
            .unwrap();
        let result = profile_thermodynamics(
            &pressure,
            &height,
            &temperature,
            &dewpoint,
            surface,
            Some(-9999.0),
        )
        .unwrap();
        let compact = profile_thermodynamic_diagnostics(
            &pressure,
            &height,
            &temperature,
            &dewpoint,
            surface,
            Some(-9999.0),
        )
        .unwrap();
        // Reference CAPE/CIN from these bundled soundings before endpoint reuse.
        let expected = if name == "observed_oax" {
            [
                [5769.227600989068, -0.6446882423912884],
                [5209.442837428716, -0.3146990965285444],
                [5769.227600989068, -0.6446882423912884],
                [4205.978162098738, -59.01525997809417],
                [3066.066746662548, -105.54983920643541],
            ]
        } else {
            [
                [2993.8044866739724, -47.83477703560904],
                [3451.8446199549085, 0.0],
                [2993.8044866739724, -47.83477703560904],
                [2465.4150227329797, -25.56174471883464],
                [1610.6352143958434, -6.048700997701934],
            ]
        };
        let mut fingerprint = 0xcbf29ce484222325_u64;
        for ((parcel, summary), expected) in result
            .convective
            .parcels
            .iter()
            .zip(compact.convective_parcels)
            .zip(expected)
        {
            for (&actual, expected) in parcel.diagnostics[10..12].iter().zip(expected) {
                assert!(
                    (actual - expected).abs() < 1.0e-8,
                    "{name}: {actual} != {expected}"
                );
            }
            for (&left, right) in parcel.diagnostics.iter().zip(summary) {
                assert!((left.is_nan() && right.is_nan()) || left.to_bits() == right.to_bits());
            }
            for value in parcel
                .diagnostics
                .iter()
                .chain(&parcel.pressure_trace)
                .chain(&parcel.temperature_trace)
            {
                let bits = if value.is_nan() {
                    f64::NAN.to_bits()
                } else {
                    value.to_bits()
                };
                fingerprint = (fingerprint ^ bits).wrapping_mul(0x100000001b3);
            }
            eprintln!("{name} parcel: {:?}", parcel.diagnostics);
        }
        eprintln!(
            "{name} levels={} surface={surface} fingerprint={fingerprint:016x} bounds={:?} downdraft={:?}",
            pressure.len(),
            [result.convective.effective_bottom_pressure, result.convective.effective_top_pressure],
            [result.downdraft.cape, result.downdraft.source_pressure, result.downdraft.downrush_temperature],
        );
    }
}

fn unstable_profile() -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) {
    let height: Vec<f64> = (0..17).map(|index| index as f64 * 1_000.0).collect();
    let pressure: Vec<f64> = height
        .iter()
        .map(|height| 1_000.0 * (-height / 8_000.0).exp())
        .collect();
    let temperature: Vec<f64> = height
        .iter()
        .map(|height| 30.0 - 7.0 * height / 1_000.0)
        .collect();
    let dewpoint: Vec<f64> = temperature
        .iter()
        .enumerate()
        .map(|(index, value)| value - 3.0 - index as f64)
        .collect();
    (pressure, height, temperature, dewpoint)
}

#[test]
fn saturated_surface_lcl_keeps_its_height_and_lfc() {
    let (mut pressure, height, mut temperature, mut dewpoint) = unstable_profile();
    for index in 0..height.len() {
        pressure[index] *= 0.925;
        temperature[index] = if height[index] <= 12_000.0 {
            25.0 - 7.0 * height[index] / 1_000.0
        } else {
            -59.0 + (height[index] - 12_000.0) / 1_000.0
        };
        dewpoint[index] = temperature[index] - 5.0;
    }
    dewpoint[0] = temperature[0];
    let result = profile_parcels(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    assert_eq!(result[0][5], 0.0);
    assert!(result[0][6].is_finite() && result[0][7].is_finite());
    let series = PressureSeries::new(&[925.0, 850.0], &[0.0, 800.0], None);
    assert!(
        series.at(930.0).is_nan(),
        "real extrapolation remains unavailable"
    );
}

#[test]
fn mixed_layer_uses_independent_temperature_and_dewpoint_masks() {
    let height: Vec<f64> = [0., 250., 500., 750., 1000., 2000., 3000., 4000., 5000.].into();
    let pressure: Vec<_> = height
        .iter()
        .map(|z| 1000.0 * (-z / 8000.0).exp())
        .collect();
    let mut temperature: Vec<_> = height.iter().map(|z| 30.0 - 7.0 * z / 1000.0).collect();
    let mut dewpoint: Vec<_> = temperature.iter().map(|t| t - 3.0).collect();
    temperature[2] = 32.0;
    dewpoint[2] = f64::NAN;
    let profile = ParcelProfile::new(&pressure, &height, &temperature, &dewpoint, 0, None)
        .unwrap()
        .unwrap();
    assert!((profile.mixed_layer_start().temperature - 33.220112).abs() < 0.000001);
    let moisture = profile.exact_layer_means(1000., 900.);
    temperature[1] = f64::NAN;
    let profile = ParcelProfile::new(&pressure, &height, &temperature, &dewpoint, 0, None)
        .unwrap()
        .unwrap();
    let independent = profile.exact_layer_means(1000., 900.);
    assert_eq!(independent.1, moisture.1);
    assert_eq!(independent.2, moisture.2);
}

#[test]
fn missing_terminal_temperature_keeps_the_last_usable_cape_layer() {
    let pressure = [1000., 950., 900., 800., 700., 600.];
    let height = [0., 400., 800., 1800., 2800., 4000.];
    let temperature = [30., 27., 24., 15., 6., f64::NAN];
    let dewpoint = [27., 24., 21., 12., 3., -7.];
    let result = profile_parcels(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    assert!((result[0][10] - 430.594602).abs() < 0.000001);
}

#[test]
fn unstable_profile_produces_three_finite_parcels() {
    let (pressure, height, temperature, dewpoint) = unstable_profile();
    let result = profile_parcels(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();

    assert_eq!(result.len(), PARCEL_COUNT);
    assert_eq!(result[0].len(), PARCEL_WIDTH);
    assert!(result[0][4].is_finite());
    assert!(result[0][10].is_finite());
    assert!(result[1][0].is_finite());
    assert!(result[2][0].is_finite());
}

#[test]
fn extended_workspace_and_explicit_ascent_include_traces() {
    let (pressure, height, temperature, dewpoint) = unstable_profile();
    let result =
        profile_convective_parcels(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    assert_eq!(result.parcels.len(), CONVECTIVE_PARCEL_COUNT);
    assert!(result
        .parcels
        .iter()
        .all(|parcel| parcel.pressure_trace.len() == parcel.temperature_trace.len()));
    assert!(result.parcels[0].pressure_trace.len() > 2);

    let user = explicit_parcel(
        &pressure,
        &height,
        &temperature,
        &dewpoint,
        pressure[2],
        temperature[2],
        dewpoint[2],
        0,
        None,
    )
    .unwrap();
    assert!(user.diagnostics[10].is_finite());
    assert_eq!(user.pressure_trace.len(), user.temperature_trace.len());
}

#[test]
fn downdraft_workspace_has_finite_summary_and_trace() {
    let (pressure, height, temperature, dewpoint) = unstable_profile();
    let result = profile_dcape(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    assert!(result.cape.is_finite());
    assert!(result.source_pressure.is_finite());
    assert!(result.downrush_temperature.is_finite());
    assert!(result.pressure_trace.len() > 2);
    assert_eq!(result.pressure_trace.len(), result.temperature_trace.len());
}

#[test]
fn shared_thermodynamic_workspace_matches_standalone_results() {
    let (pressure, height, temperature, dewpoint) = unstable_profile();
    let combined =
        profile_thermodynamics(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    let convective =
        profile_convective_parcels(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    let downdraft = profile_dcape(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();

    let assert_row = |left: &[f64; PARCEL_WIDTH], right: &[f64; PARCEL_WIDTH]| {
        assert!(left
            .iter()
            .zip(right)
            .all(|(left, right)| left == right || (left.is_nan() && right.is_nan())));
    };
    assert_row(&combined.parcels[0], &convective.parcels[0].diagnostics);
    assert_row(&combined.parcels[1], &convective.parcels[2].diagnostics);
    assert_row(&combined.parcels[2], &convective.parcels[3].diagnostics);
    assert_eq!(
        combined.convective.effective_bottom_pressure,
        convective.effective_bottom_pressure
    );
    assert_eq!(
        combined.convective.effective_top_pressure,
        convective.effective_top_pressure
    );
    assert_eq!(combined.downdraft.cape, downdraft.cape);
    assert_eq!(combined.downdraft.pressure_trace, downdraft.pressure_trace);
    assert_eq!(
        combined.downdraft.temperature_trace,
        downdraft.temperature_trace
    );
}

#[test]
fn trace_free_thermodynamics_matches_traced_diagnostics_bit_for_bit() {
    let (pressure, height, temperature, dewpoint) = unstable_profile();
    let traced =
        profile_thermodynamics(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
    let compact =
        profile_thermodynamic_diagnostics(&pressure, &height, &temperature, &dewpoint, 0, None)
            .unwrap();
    let same = |left: f64, right: f64| {
        (left.is_nan() && right.is_nan()) || left.to_bits() == right.to_bits()
    };
    for (left, right) in compact.parcels.iter().zip(&traced.parcels) {
        assert!(left
            .iter()
            .zip(right)
            .all(|(&left, &right)| same(left, right)));
    }
    for (left, right) in compact
        .convective_parcels
        .iter()
        .zip(&traced.convective.parcels)
    {
        assert!(left
            .iter()
            .zip(&right.diagnostics)
            .all(|(&left, &right)| same(left, right)));
    }
    assert!(same(
        compact.effective_bottom_pressure,
        traced.convective.effective_bottom_pressure,
    ));
    assert!(same(
        compact.effective_top_pressure,
        traced.convective.effective_top_pressure,
    ));
    assert!(same(compact.downdraft[0], traced.downdraft.cape));
    assert!(same(compact.downdraft[1], traced.downdraft.source_pressure,));
    assert!(same(
        compact.downdraft[2],
        traced.downdraft.downrush_temperature,
    ));
}

#[test]
fn configured_sentinels_match_nan_in_all_trace_free_diagnostics() {
    let (pressure, height, mut sentinel_temperature, mut sentinel_dewpoint) = unstable_profile();
    sentinel_temperature[5] = -9_999.0;
    sentinel_dewpoint[9] = -9_999.0;
    let mut nan_temperature = sentinel_temperature.clone();
    let mut nan_dewpoint = sentinel_dewpoint.clone();
    nan_temperature[5] = f64::NAN;
    nan_dewpoint[9] = f64::NAN;

    let sentinel = profile_thermodynamic_diagnostics(
        &pressure,
        &height,
        &sentinel_temperature,
        &sentinel_dewpoint,
        0,
        Some(-9_999.0),
    )
    .unwrap();
    let nan = profile_thermodynamic_diagnostics(
        &pressure,
        &height,
        &nan_temperature,
        &nan_dewpoint,
        0,
        Some(-9_999.0),
    )
    .unwrap();
    let same = |left: f64, right: f64| {
        (left.is_nan() && right.is_nan()) || left.to_bits() == right.to_bits()
    };

    assert!(sentinel
        .parcels
        .iter()
        .flatten()
        .zip(nan.parcels.iter().flatten())
        .all(|(&left, &right)| same(left, right)));
    assert!(sentinel
        .convective_parcels
        .iter()
        .flatten()
        .zip(nan.convective_parcels.iter().flatten())
        .all(|(&left, &right)| same(left, right)));
    assert!(same(
        sentinel.effective_bottom_pressure,
        nan.effective_bottom_pressure,
    ));
    assert!(same(
        sentinel.effective_top_pressure,
        nan.effective_top_pressure,
    ));
    assert!(sentinel
        .downdraft
        .iter()
        .zip(nan.downdraft.iter())
        .all(|(&left, &right)| same(left, right)));
}

#[test]
fn invalid_and_shallow_profiles_are_handled() {
    assert!(profile_parcels(
        &[1_000.0, 900.0],
        &[0.0],
        &[20.0, 10.0],
        &[15.0, 5.0],
        0,
        None,
    )
    .is_err());
    let shallow = profile_parcels(
        &[1_000.0, 900.0],
        &[0.0, 1_000.0],
        &[20.0, 10.0],
        &[15.0, 5.0],
        0,
        None,
    )
    .unwrap();
    assert!(shallow.iter().flatten().all(|value| value.is_nan()));
}

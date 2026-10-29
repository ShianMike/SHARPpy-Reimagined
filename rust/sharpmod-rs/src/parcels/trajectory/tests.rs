use super::*;

fn state() -> TrajectoryState {
    TrajectoryState {
        cape: 1000.,
        lfc_pressure: 1000.,
        lfc_height: Some(0.),
        el_height: Some(250.),
        storm_motion: [Some(10.), Some(-5.)],
    }
}

#[test]
fn real_hrrr_trajectory_matches_pinned_sharppy() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/parcel_trajectory.json"
    ))
    .unwrap();
    let columns = ["pres", "hght", "vtmp", "u", "v", "ptrace", "ttrace"].map(|name| {
        fixture["columns"][name]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_f64().unwrap_or(f64::NAN))
            .collect::<Vec<_>>()
    });
    let source = &fixture["state"];
    let state = TrajectoryState {
        cape: source["bplus"].as_f64().unwrap(),
        lfc_pressure: source["lfcpres"].as_f64().unwrap(),
        lfc_height: source["lfchght"].as_f64(),
        el_height: source["elhght"].as_f64(),
        storm_motion: [0, 1].map(|i| source["storm_motion"][i].as_f64()),
    };
    let result = parcel_trajectory(
        [0, 1, 2, 3, 4].map(|i| columns[i].as_slice()),
        [&columns[5], &columns[6]],
        state,
        source["sfc"].as_u64().unwrap() as usize,
        None,
    )
    .unwrap()
    .unwrap();
    let expected = fixture["positions"].as_array().unwrap();
    assert_eq!(result.positions.len(), expected.len());
    for (actual, expected) in result.positions.iter().zip(expected) {
        for (axis, value) in actual.iter().enumerate() {
            assert!((value.unwrap() - expected[axis].as_f64().unwrap()).abs() < 1e-7);
        }
    }
    assert!((result.tilt.unwrap() - fixture["tilt"].as_f64().unwrap()).abs() < 1e-9);
}

#[test]
fn nudge_wind_conversion_and_terrain_reference_are_preserved() {
    let pressure = [1000., 100.];
    let environment = [20., 20.];
    let u = [20., 20.];
    let v = [-10., -10.];
    for terrain in [0., 2300.] {
        let height = [terrain, terrain + 10000.];
        let result = parcel_trajectory(
            [&pressure, &height, &environment, &u, &v],
            [&pressure, &environment],
            state(),
            0,
            None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(result.positions.len(), 3);
        let last = result.positions.last().unwrap();
        assert!((last[0].unwrap() - 257.222).abs() < 1e-10);
        assert!((last[1].unwrap() + 128.611).abs() < 1e-10);
        assert_eq!(last[2], Some(250.));
    }
}

#[test]
fn missing_columns_motion_and_lfc_remain_masked() {
    let pressure = [1000., 100.];
    let height = [0., 10000.];
    let environment = [20., 20.];
    let absent = [f64::NAN; 2];
    let mut start = state();
    start.storm_motion[1] = None;
    let result = parcel_trajectory(
        [&pressure, &height, &absent, &absent, &environment],
        [&pressure, &environment],
        start,
        0,
        None,
    )
    .unwrap()
    .unwrap();
    assert_eq!(
        result.positions,
        [[Some(0.), Some(0.), Some(0.)], [None, None, None]]
    );
    assert_eq!(result.tilt, None);

    let mut start = state();
    start.lfc_height = None;
    let result = parcel_trajectory(
        [&pressure, &height, &environment, &environment, &environment],
        [&[], &[]],
        start,
        0,
        None,
    )
    .unwrap()
    .unwrap();
    assert_eq!(result.positions, [[Some(0.), Some(0.), None]]);
    assert_eq!(result.tilt, None);
}

#[test]
fn incomplete_and_nonconvergent_trajectories_cannot_hang_a_worker() {
    let mut start = state();
    start.cape = 0.;
    assert!(parcel_trajectory([&[]; 5], [&[]; 2], start, 0, None)
        .unwrap()
        .is_none());
    let pressure = [1000., 100.];
    let height = [0., 1e9];
    let environment = [20., 20.];
    assert!(parcel_trajectory(
        [&pressure, &height, &environment, &environment, &environment],
        [&pressure, &[20.]],
        state(),
        0,
        None,
    )
    .is_err());
    assert!(parcel_trajectory(
        [&pressure, &height, &environment, &environment, &environment],
        [&pressure, &environment],
        state(),
        2,
        None,
    )
    .is_err());
    let mut start = state();
    start.el_height = Some(1e9);
    assert!(parcel_trajectory(
        [&pressure, &height, &environment, &environment, &environment],
        [&pressure, &environment],
        start,
        0,
        None,
    )
    .unwrap()
    .is_none());
}

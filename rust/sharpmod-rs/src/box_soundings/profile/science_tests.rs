use super::*;

#[test]
fn identical_convective_columns_preserve_nonzero_cape_cin_srh_shear_and_motion() {
    let text = include_str!("../../../../../examples/soundings/hrrr_point_36.68N_95.66W_f018.spc");
    let raw = text
        .split_once("%RAW%")
        .unwrap()
        .1
        .split_once("%END%")
        .unwrap()
        .0;
    let rows = crate::parsing::parse_sounding_rows(raw, MISSING).unwrap();
    let n = rows.len();
    let mut matrix = vec![MISSING; 9 * n];
    for (index, row) in rows.iter().enumerate() {
        for field in 0..6 {
            matrix[field * n + index] = row[field];
        }
        let angle = row[4].to_radians();
        matrix[7 * n + index] = -row[5] * angle.sin() / MPS_TO_KNOTS;
        matrix[8 * n + index] = -row[5] * angle.cos() / MPS_TO_KNOTS;
    }
    let point = DecodedPoint {
        matrix,
        level_count: n,
        selected_latitude: 36.68,
        selected_longitude: -95.66,
        surface_relative_vorticity: None,
        surface_merged: true,
        below_ground_levels_removed: 0,
    };
    let columns = [1.0, 2.0, 3.0, 4.0].map(|area| Column {
        profile: &point,
        overlap_area: area,
        vorticity: &[],
    });
    let averaged = aggregate(
        &columns,
        Bounds::new(-95.7, 36.6, -95.6, 36.7).unwrap(),
        10.0,
        Limits::default(),
        &|| false,
    )
    .unwrap()
    .profile;
    let fields = |profile: &DecodedPoint| -> [Vec<f64>; 4] {
        std::array::from_fn(|field| {
            profile.matrix[field * profile.level_count..(field + 1) * profile.level_count].to_vec()
        })
    };
    let [p, z, t, td] = fields(&point);
    let reference = crate::parcels::profile_parcels(&p, &z, &t, &td, 0, Some(MISSING)).unwrap();
    let [p, z, t, td] = fields(&averaged);
    let mean = crate::parcels::profile_parcels(&p, &z, &t, &td, 0, Some(MISSING)).unwrap();
    assert!(
        reference[0][10] > 2000.0,
        "fixture must exercise substantial instability"
    );
    let close = |a: f64, b: f64| {
        assert!(
            a.is_nan() && b.is_nan() || (a - b).abs() <= 1e-7 * a.abs().max(1.0),
            "{a} differs from {b}"
        );
    };
    for (a, b) in reference.iter().flatten().zip(mean.iter().flatten()) {
        close(*a, *b);
    }
    let kinematics = |profile: &DecodedPoint| {
        let n = profile.level_count;
        let row = |field| &profile.matrix[field * n..(field + 1) * n];
        crate::kinematics::profile_kinematics(
            row(0),
            row(1),
            row(7),
            row(8),
            &[500.0, 1000.0, 3000.0, 6000.0],
            0,
            Some(MISSING),
        )
        .unwrap()
    };
    let a = kinematics(&point);
    let b = kinematics(&averaged);
    for (a, b) in a
        .storm_motion
        .iter()
        .chain(a.layers.iter().flatten())
        .zip(b.storm_motion.iter().chain(b.layers.iter().flatten()))
    {
        close(*a, *b);
    }
}

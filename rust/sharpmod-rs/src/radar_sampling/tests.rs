use super::*;

#[test]
fn keeps_gate_edges_range_folded_codes_missing_sectors_and_north_wrap() {
    let radials = Radials::new(
        &[3, 4, 5, 6, 1, 2],
        3,
        2,
        &[0., 1., 359.5],
        &[0.5, 1.5, 0.],
        250.,
        2125.,
    )
    .unwrap();
    let output = radials
        .sample(
            &[
                359.75,
                0.25,
                0.25,
                0.25,
                0.25,
                0.75,
                1.25,
                359.25,
                f32::NAN,
                0.,
            ],
            &[
                2125.,
                2124.99,
                2125.,
                2375.,
                2625.,
                2250.,
                2375.,
                2250.,
                2250.,
                f32::INFINITY,
            ],
        )
        .unwrap();
    assert_eq!(output, [1, 0, 3, 4, 0, 0, 6, 0, 0, 0]);
}

#[test]
fn keeps_native_codes_at_every_centre_and_rejects_geometry_mismatches() {
    let codes: Vec<_> = (0..=255).collect();
    let bearings = vec![90.5; 256];
    let ranges: Vec<_> = (0..256).map(|gate| gate as f32 * 1000. + 500.).collect();
    let radials = Radials::new(&codes, 1, 256, &[90.], &[91.], 1000., 0.).unwrap();
    assert_eq!(radials.sample(&bearings, &ranges).unwrap(), codes);
    assert!(radials.sample(&bearings, &ranges[..255]).is_err());
    assert!(Radials::new(&codes, 1, 256, &[90.], &[91.], 0., 0.).is_err());
    assert!(Radials::new(&codes, 2, 256, &[90.], &[91.], 1000., 0.).is_err());
    assert!(Radials::new(&codes, 1, 256, &[f64::NAN], &[91.], 1000., 0.).is_err());
    assert!(Radials::new(&[1, 2], 2, 1, &[180., 0.], &[181., 1.], 1000., 0.).is_err());
}

#[test]
fn samples_large_frames_in_the_original_pixel_order() {
    let azimuth: Vec<_> = (0..20_000).map(|i| i as f32 % 360.).collect();
    let ranges = vec![500.; azimuth.len()];
    let radials = Radials::new(&[19, 29], 2, 1, &[0., 180.], &[180., 0.], 1000., 0.).unwrap();
    let output = radials.sample(&azimuth, &ranges).unwrap();
    assert_eq!(
        output,
        azimuth
            .iter()
            .map(|&angle| if angle < 180. { 19 } else { 29 })
            .collect::<Vec<_>>()
    );
}

use sharpmod_rs::{interpolation, parsing, quality_control, records, wind};

#[test]
fn public_kernel_modules_form_one_usable_crate() {
    let (u, v) = wind::wind_to_components(&[270.0], &[10.0], None).unwrap();
    assert!((u[0] - 10.0).abs() < 1.0e-12);
    assert!(v[0].abs() < 1.0e-12);

    let interpolated =
        interpolation::interpolate_1d(&[0.5], &[0.0, 1.0], &[0.0, 10.0], None, false).unwrap();
    assert_eq!(interpolated, vec![5.0]);

    let rows = parsing::parse_sounding_rows("1000,100,20,15,180,10\n900,1000,12,8,200,20", -9999.0)
        .unwrap();
    let qc = quality_control::basic_sounding_qc(
        &[rows[0][0], rows[1][0]],
        &[rows[0][1], rows[1][1]],
        &[rows[0][2], rows[1][2]],
        &[rows[0][3], rows[1][3]],
        &[rows[0][4], rows[1][4]],
        &[rows[0][5], rows[1][5]],
        Some(-9999.0),
    )
    .unwrap();
    assert!(qc.valid);
    assert_eq!(
        records::pressure_sort_dedup_indices(&[900.0, 1000.0, 900.0], Some(-9999.0)),
        vec![1, 0]
    );
}

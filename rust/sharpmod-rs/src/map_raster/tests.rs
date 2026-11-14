use super::*;

fn palette(stepped: bool) -> Palette {
    Palette::new(
        vec![
            (0.0, "#000000".into()),
            (1.0, "#808080".into()),
            (2.0, "#ffffff".into()),
        ],
        stepped,
        None,
        None,
    )
    .unwrap()
}

#[test]
fn band_boundaries_overflow_colors_and_transparency_preserve_source_classes() {
    let palette = palette(true);
    let values = Buffer::Double(vec![
        -1.0,
        0.0,
        0.9999,
        1.0,
        1.9999,
        2.0,
        3.0,
        f64::NAN,
        f64::INFINITY,
        f64::NEG_INFINITY,
    ]);
    let pixels = palette.colorize(&values, 10, 1024).unwrap();
    let pixel = |index: usize| &pixels[4 * index..4 * index + 4];
    assert_eq!(pixel(0), [0, 0, 0, 0]);
    assert_eq!(pixel(1), [0, 0, 0, 255]);
    assert_eq!(pixel(2), [0, 0, 0, 255]);
    assert_eq!(pixel(3), [128, 128, 128, 255]);
    assert_eq!(pixel(4), [128, 128, 128, 255]);
    assert_eq!(pixel(5), [255, 255, 255, 255]);
    assert_eq!(pixel(6), [255, 255, 255, 255]);
    assert_eq!(pixel(7), [255, 255, 255, 0]);
    assert_eq!(pixel(8), [255, 255, 255, 0]);
    assert_eq!(pixel(9), [0, 0, 0, 0]);
}

#[test]
fn continuous_tables_interpolate_channels_and_preserve_exact_endpoints() {
    let palette = palette(false);
    assert_eq!(
        palette.lut(5).unwrap(),
        [
            0, 0, 0, 255, 64, 64, 64, 255, 128, 128, 128, 255, 191, 191, 191, 255, 255, 255, 255,
            255
        ]
    );
    let values = Buffer::Single(vec![0.0, 0.5, 1.0, 1.5, 2.0]);
    assert_eq!(
        palette.colorize(&values, 5, 5).unwrap(),
        palette.lut(5).unwrap()
    );
    assert!(palette.lut(1).is_err());
    assert!(palette.lut(65537).is_err());
}

#[test]
fn floors_and_ceilings_are_inclusive_and_follow_float32_comparison_precision() {
    let mut palette = palette(true);
    palette.floor = Some(0.1);
    palette.ceiling = Some(1.8);
    let values = Buffer::Single(vec![0.09, 0.1, 1.8, 1.9]);
    let pixels = palette.colorize(&values, 4, 1024).unwrap();
    assert_eq!(
        pixels
            .as_chunks::<4>()
            .0
            .iter()
            .map(|p| p[3])
            .collect::<Vec<_>>(),
        [0, 255, 255, 0]
    );
    palette.floor = Some(0.1 + 1e-12);
    let pixels = palette.colorize(&values, 4, 1024).unwrap();
    assert_eq!(pixels[7], 255);
}

#[test]
fn contour_crossings_need_two_finite_neighbors_and_expand_once_when_thick() {
    let values = Buffer::Single(vec![0.0, 0.0, 2.0, 0.0, f32::NAN, 2.0]);
    let mut thin = vec![42; 24];
    draw_contours(&mut thin, &values, 2, 3, 1.0, [1, 2, 3], 1.2);
    assert_eq!(&thin[4..8], &[1, 2, 3, 255]);
    assert!(thin[..4].iter().all(|v| *v == 42));
    assert!(thin[12..].iter().all(|v| *v == 42));
    let mut thick = vec![42; 24];
    draw_contours(&mut thick, &values, 2, 3, 1.0, [1, 2, 3], 2.0);
    for index in [1, 2, 4] {
        assert_eq!(&thick[index * 4..index * 4 + 4], &[1, 2, 3, 255]);
    }
    assert!(thick[20..].iter().all(|v| *v == 42));
}

#[test]
fn empty_rasters_and_invalid_palette_inputs_are_handled_at_the_boundary() {
    let palette = palette(true);
    assert!(palette
        .colorize(&Buffer::Single(Vec::new()), 0, 1024)
        .unwrap()
        .is_empty());
    draw_contours(
        &mut [],
        &Buffer::Double(Vec::new()),
        0,
        0,
        1.0,
        [0, 0, 0],
        1.2,
    );
    assert!(Palette::new(vec![], true, None, None).is_err());
    assert!(Palette::new(
        vec![(1.0, "#000000".into()), (0.0, "#ffffff".into())],
        false,
        None,
        None
    )
    .is_err());
    assert!(rgb("#ffGG00").is_err());
    assert!(rgb("#fff").is_err());
}

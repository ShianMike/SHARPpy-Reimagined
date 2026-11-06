use super::*;
use std::collections::{BTreeMap, BTreeSet};

#[test]
fn ordinary_display_geometry_keeps_strict_order_pairing_and_missing_endpoints() {
    for values in [
        [0., 2., 0., 2.],
        [0., 2., 2., 0.],
        [f64::NAN, 0., 0., 2.],
        [f64::NAN; 4],
        [1.; 4],
    ] {
        let strict = extract(&values, 2, 2, 1., 100).unwrap();
        let display = extract_display(&values, 2, 2, 1., 100).unwrap();
        let labeled = extract_display_levels(&values, 2, 2, 1., 100).unwrap();
        assert_eq!(display, strict);
        assert_eq!(
            labeled
                .into_iter()
                .map(|(_, segment)| segment)
                .collect::<Vec<_>>(),
            strict
        );
    }
    assert_eq!(
        extract_display_levels(&[0., 2., 2., 0.], 2, 2, 1., 100).unwrap(),
        vec![
            (1., ((0.5, 0.), (1., 0.5))),
            (1., ((0.5, 1.), (0., 0.5))),
            (2., ((1., 0.), (1., 0.))),
            (2., ((0., 1.), (0., 1.))),
        ]
    );
}

#[test]
fn formerly_dense_field_recovers_complete_lines_without_raising_the_hard_cap() {
    let (height, width) = (404, 403);
    let values: Vec<_> = (0..height * width)
        .map(|index| if (index % width) % 4 < 2 { 0. } else { 2. })
        .collect();
    assert_eq!(
        extract(&values, height, width, 1., 100_000).unwrap_err(),
        TOO_DENSE
    );
    for limit in [100_000, usize::MAX] {
        let segments = extract_display_levels(&values, height, width, 1., limit).unwrap();
        assert_eq!(segments.len(), 81_204);
        assert!(segments.len() < 100_000);
        assert!(segments
            .iter()
            .all(|(level, _)| *level == 1. || *level == 2.));
        // Every level-1 line reaches both ends of the domain without truncation.
        // Stride two gives x=1,3,...,401; the short last row still ends at 403.
        let mut lines: BTreeMap<usize, Vec<(f64, f64)>> = BTreeMap::new();
        for (_, ((x0, y0), (x1, y1))) in segments.iter().filter(|(level, _)| *level == 1.) {
            assert_eq!(x0, x1);
            lines
                .entry(*x0 as usize)
                .or_default()
                .push((y0.min(*y1), y0.max(*y1)));
        }
        assert_eq!(
            lines.keys().copied().collect::<Vec<_>>(),
            (1..width).step_by(2).collect::<Vec<_>>()
        );
        for pieces in lines.values_mut() {
            pieces.sort_by(|a, b| a.0.total_cmp(&b.0));
            assert_eq!(pieces.first().unwrap().0, 0.);
            assert_eq!(pieces.last().unwrap().1, (height - 1) as f64);
            assert!(pieces.windows(2).all(|pair| pair[0].1 == pair[1].0));
        }
    }
    assert!(values
        .iter()
        .enumerate()
        .all(|(index, value)| { *value == if (index % width) % 4 < 2 { 0. } else { 2. } }));
}

#[test]
fn adaptive_affine_segments_carry_exact_requested_levels_and_original_coordinates() {
    let (height, width) = (33, 35);
    let value_at = |x: f64, y: f64| -7.125 + 0.75 * x + 0.5 * y;
    let values: Vec<_> = (0..height * width)
        .map(|index| value_at((index % width) as f64, (index / width) as f64))
        .collect();
    let original = values.clone();
    assert_eq!(
        extract(&values, height, width, 0.5, 200).unwrap_err(),
        TOO_DENSE
    );
    let labeled = extract_display_levels(&values, height, width, 0.5, 200).unwrap();
    assert!(!labeled.is_empty());
    assert!(labeled.len() <= 200);
    let levels: BTreeSet<_> = labeled
        .iter()
        .map(|(level, _)| (level * 2.) as i32)
        .collect();
    assert_eq!(levels, (-14..=68).collect());
    for (level, segment) in &labeled {
        assert_eq!((level / 0.5).fract(), 0.);
        for (x, y) in [segment.0, segment.1] {
            assert!((0.0..=(width - 1) as f64).contains(&x));
            assert!((0.0..=(height - 1) as f64).contains(&y));
            assert!((value_at(x, y) - level).abs() < 1e-12);
        }
    }
    assert!(labeled.iter().any(|(_, (a, b))| a.0 == 34. || b.0 == 34.));
    assert!(labeled.iter().any(|(_, (a, b))| a.1 == 32. || b.1 == 32.));
    let geometry = extract_display(&values, height, width, 0.5, 200).unwrap();
    assert_eq!(
        geometry,
        labeled
            .iter()
            .map(|(_, segment)| *segment)
            .collect::<Vec<_>>()
    );
    assert_eq!(values, original);

    let mut projected = geometry.clone();
    project(&mut projected, height, width, (-130., -60., 20., 60.)).unwrap();
    for (before, after) in geometry.iter().zip(projected) {
        for ((x, y), (longitude, latitude)) in [(before.0, after.0), (before.1, after.1)] {
            let expected_longitude = -130. + (x + 0.5) * 70. / width as f64;
            let expected_latitude = 60. - (y + 0.5) * 40. / height as f64;
            assert!((longitude - expected_longitude).abs() <= 0.000_005_000_01);
            assert!((latitude - expected_latitude).abs() <= 0.000_005_000_01);
        }
    }
}

#[test]
fn adaptive_cells_cannot_bridge_missing_samples_between_their_finite_corners() {
    let (height, width) = (17, 17);
    for missing in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut values: Vec<_> = (0..height * width)
            .map(|index| (index % width) as f64)
            .collect();
        // The first successful display step is four. This sample is not one
        // of its corners; checking only those corners would bridge the hole.
        values[3 * width + 3] = missing;
        assert_eq!(
            extract(&values, height, width, 1., 100).unwrap_err(),
            TOO_DENSE
        );
        let labeled = extract_display_levels(&values, height, width, 1., 100).unwrap();
        assert!(!labeled.is_empty());
        assert!(labeled.len() <= 100);
        for (level, ((x0, y0), (x1, y1))) in &labeled {
            assert_eq!(x0, level);
            assert_eq!(x1, level);
            assert!(!(*x0 > 0. && *x0 <= 4. && y0.min(*y1) < 4.));
        }
        assert!(labeled.iter().any(|(level, ((_, y0), (_, y1)))| {
            *level == 3. && y0.min(*y1) == 4. && y0.max(*y1) == 8.
        }));
    }
}

#[test]
fn display_keeps_shape_interval_integer_precision_and_impossible_budget_errors() {
    for (values, height, width) in [(&[1.][..], 2, 2), (&[][..], usize::MAX, 2)] {
        assert_eq!(
            extract_display(values, height, width, 1., 100).unwrap_err(),
            "contour grid dimensions do not match its values"
        );
    }
    for interval in [0., -1., f64::NAN, f64::INFINITY] {
        assert!(extract_display(&[0., 2., 0., 2.], 2, 2, interval, 100)
            .unwrap()
            .is_empty());
    }
    for limit in [0, 1] {
        assert_eq!(
            extract_display(&[0., 2., 0., 2.], 2, 2, 1., limit).unwrap_err(),
            TOO_DENSE
        );
    }
    for interval in [1e-20, f64::MIN_POSITIVE] {
        assert_eq!(
            extract_display(&[0., 1., 0., 1.], 2, 2, interval, 100_000).unwrap_err(),
            TOO_DENSE
        );
    }
    assert!(extract_display(&[1.; 4], 2, 2, 1., 0).unwrap().is_empty());
    assert!(extract_display(&[], 0, 0, 1., 100).unwrap().is_empty());
    assert!(extract_display(&[1.], 1, 1, 1., 100).unwrap().is_empty());
}

#[test]
fn work_is_bounded_even_for_numerically_unresolvable_crossings() {
    // Subtraction overflows in the existing crossing convention. No segments
    // need be emitted for these levels, so level work needs its own budget.
    let values = [-f64::MAX, f64::MAX, -f64::MAX, f64::MAX];
    assert_eq!(
        extract_display(&values, 2, 2, 1e304, 10).unwrap_err(),
        TOO_DENSE
    );
    let invalid_islands = [0., f64::NAN, f64::NAN, 1e100];
    assert!(extract_display(&invalid_islands, 2, 2, 1., 100)
        .unwrap()
        .is_empty());
}

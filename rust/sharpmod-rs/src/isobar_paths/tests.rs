use super::*;

fn at_level(level: f64, points: &[Point]) -> Vec<(f64, Segment)> {
    points
        .windows(2)
        .map(|p| (level, ((p[0][0], p[0][1]), (p[1][0], p[1][1]))))
        .collect()
}

#[test]
fn shuffled_segments_join_and_round_a_long_open_line_with_pinned_ends() {
    let points = [[1., 1.], [3., 1.], [3., 3.], [5., 3.], [5., 5.]];
    let mut segments = at_level(1012., &points);
    segments.swap(0, 2);
    segments[2].1 = (segments[2].1 .1, segments[2].1 .0);
    let mut paths = smooth_isobars(&segments, &[1012.; 49], 7, 7);
    assert_eq!(paths.len(), 1);
    let (level, path) = paths.pop().unwrap();
    assert_eq!(level, 1012.);
    assert_eq!(
        path,
        [
            [1., 1.],
            [2.5, 1.],
            [3., 1.5],
            [3., 2.5],
            [3.5, 3.],
            [4.5, 3.],
            [5., 3.5],
            [5., 5.]
        ]
    );
    assert_eq!(path.len(), 2 * segments.len());
}

#[test]
fn matching_uses_level_and_crossing_edge_as_well_as_rounded_position() {
    let epsilon = 2e-10;
    let other_level = f64::from_bits(1000f64.to_bits() + 1);
    let paths = smooth_isobars(
        &[
            (1000., ((0., 1.5), (2.25, 2.))),
            (1000., ((2.25 + epsilon, 2.), (4., 2.5))),
            (other_level, ((4., 2.5), (5., 2.75))),
        ],
        &[1000.; 36],
        6,
        6,
    );
    assert_eq!(paths.len(), 2);
    assert_eq!(paths[0].0, 1000.);
    assert_eq!(paths[0].1.first(), Some(&[0., 1.5]));
    assert_eq!(paths[0].1.last(), Some(&[4., 2.5]));
    assert_eq!(paths[1], (other_level, vec![[4., 2.5], [5., 2.75]]));

    let segments = [
        (1000., ((0., 0.5), (1. - epsilon, 1.))),
        (1000., ((1. + epsilon, 1.), (2., 1.5))),
        (1000., ((1., 1. + epsilon), (1.5, 2.))),
        (1000., ((1., 1.), (0.5, 2.))),
    ];
    // The four near-coincident endpoints belong to distinct grid edges or
    // an exact vertex. Rounding coordinates alone would create a junction.
    let paths = smooth_isobars(&segments, &[1000.; 9], 3, 3);
    assert_eq!(paths.len(), segments.len());
    for ((level, path), (expected, (a, b))) in paths.into_iter().zip(segments) {
        assert_eq!(level, expected);
        assert_eq!(path, [[a.0, a.1], [b.0, b.1]]);
    }
}

#[test]
fn a_junction_pins_all_path_endpoints_even_if_one_path_returns_to_it() {
    let junction = [2., 2.];
    let mut segments = at_level(1004., &[junction, [4., 2.], [4., 4.], [2., 4.], junction]);
    segments.extend(at_level(1004., &[junction, [2., 0.]]));
    let paths = smooth_isobars(&segments, &[1004.; 25], 5, 5);
    assert_eq!(paths.len(), 2);
    assert_eq!(
        paths[0].1,
        [
            junction,
            [3.5, 2.],
            [4., 2.5],
            [4., 3.5],
            [3.5, 4.],
            [2.5, 4.],
            [2., 3.5],
            junction
        ]
    );
    assert_eq!(paths[1].1, [junction, [2., 0.]]);
}

#[test]
fn closed_rings_round_and_remain_closed_even_when_the_seam_has_no_spare_vertex() {
    let ring = [[1., 1.], [3., 1.], [3., 3.], [1., 3.], [1., 1.]];
    let segments = at_level(1008., &ring);
    let paths = smooth_isobars(&segments, &[1008.; 16], 4, 4);
    assert_eq!(paths.len(), 1);
    let path = &paths[0].1;
    assert_eq!(path.len(), 8);
    assert_eq!(path.first(), path.last());
    assert_eq!(path.first(), Some(&ring[0]));
    assert!(path.iter().any(|point| !ring.contains(point)));

    // A redundant point on an edge supplies space to round the closing seam too.
    let segments = at_level(
        1008.,
        &[ring[0], [2., 1.], ring[1], ring[2], ring[3], ring[4]],
    );
    let paths = smooth_isobars(&segments, &[1008.; 16], 4, 4);
    let path = &paths[0].1;
    assert_eq!(path.len(), 9);
    assert_eq!(path.first(), path.last());
    assert!(!path.iter().any(|point| ring.contains(point)));
}

#[test]
fn collinear_closed_trace_retains_its_reversal_and_repeated_endpoint() {
    let segments = at_level(1008., &[[0., 1.], [1., 1.], [2., 1.], [3., 1.], [0., 1.]]);
    let paths = smooth_isobars(&segments, &[1008.; 8], 2, 4);
    assert_eq!(paths, [(1008., vec![[0., 1.], [3., 1.], [0., 1.]])]);
}

#[test]
fn a_missing_sample_inside_the_cut_footprint_keeps_only_that_corner_intact() {
    let points = [[1., 4.], [4., 4.], [4., 1.], [1., 1.]];
    let segments = at_level(1000., &points);
    for missing in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut values = [1000.; 25];
        values[3 * 5 + 3] = missing;
        let paths = smooth_isobars(&segments, &values, 5, 5);
        assert_eq!(
            paths[0].1,
            [[1., 4.], [4., 4.], [4., 1.5], [3.5, 1.], [1., 1.]]
        );
    }
}

#[test]
fn long_segments_are_cut_by_at_most_half_a_sample_and_straight_sections_compact() {
    let points = [[0., 0.], [60., 0.], [128., 0.], [128., 128.]];
    let mut segments = at_level(1000., &points);
    segments.extend(at_level(1000., &[points[1], points[0], points[0]]));
    let paths = smooth_isobars(&segments, &vec![1000.; 129 * 129], 129, 129);
    assert_eq!(
        paths,
        [(
            1000.,
            vec![[0., 0.], [127.5, 0.], [128., 0.5], [128., 128.]]
        )]
    );
}

#[test]
fn full_input_budget_keeps_every_ring_and_caps_the_complete_frame() {
    let width = 750;
    let height = 300;
    let segments: Vec<_> = (0..100)
        .flat_map(|y| {
            (0..250).flat_map(move |x| {
                let (x, y) = (f64::from(x * 3), f64::from(y * 3));
                at_level(
                    1000.,
                    &[[x, y], [x + 1., y], [x + 1., y + 1.], [x, y + 1.], [x, y]],
                )
            })
        })
        .collect();
    assert_eq!(segments.len(), 100_000);
    let paths = smooth_isobars(&segments, &vec![1000.; width * height], height, width);
    assert_eq!(paths.len(), 25_000);
    assert_eq!(
        paths.iter().map(|(_, path)| path.len()).sum::<usize>(),
        200_000
    );
    assert!(paths.iter().all(|(level, path)| {
        *level == 1000. && path.len() >= 5 && path.first() == path.last()
    }));
    assert!(paths.iter().any(|(_, path)| path.len() == 5));
    assert!(paths.iter().any(|(_, path)| path.len() == 9));
}

#[test]
fn an_empty_contour_set_returns_no_paths() {
    assert!(smooth_isobars(&[], &[], 0, 0).is_empty());
}

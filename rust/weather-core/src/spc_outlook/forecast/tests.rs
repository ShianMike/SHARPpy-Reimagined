use super::*;
use crate::spc_outlook::bounds_of;
use i_overlay::core::{fill_rule::FillRule, overlay_rule::OverlayRule};
use i_overlay::float::single::SingleFloatOverlay;
use i_overlay::i_shape::base::data::Shapes;
use serde_json::json;

const TWO_MILES_KM: f64 = 3.218_688;
const KM_PER_LATITUDE_DEGREE: f64 = 111.2;
const CLEARANCE_TOLERANCE_KM: f64 = 0.05;

fn assert_two_mile_clearance(distance_km: f64) {
    assert!(
        (distance_km - TWO_MILES_KM).abs() < CLEARANCE_TOLERANCE_KM,
        "expected two miles of clearance, got {distance_km} km"
    );
}

fn rectangle(id: &str, level: u8, hatch: bool, [w, e, s, n]: [f64; 4]) -> ForecastPolygon {
    ForecastPolygon {
        id: id.into(),
        level,
        hatch,
        source_id: None,
        points: vec![[w, s], [e, s], [e, n], [w, n]],
    }
}

fn adjustment(source: &str, id: &str, level: u8, bounds: [f64; 4]) -> ForecastPolygon {
    ForecastPolygon {
        source_id: Some(source.into()),
        ..rectangle(id, level, false, bounds)
    }
}

fn coverage(drawing: &ForecastDrawing, label: &str) -> Shapes<[f64; 2]> {
    drawing
        .areas
        .iter()
        .filter(|area| area.label == label)
        .map(|area| area.rings.clone())
        .collect()
}

fn contains(shapes: &Shapes<[f64; 2]>, [x, y]: [f64; 2]) -> bool {
    shapes.iter().any(|rings| {
        let crossings = rings
            .iter()
            .flat_map(|ring| {
                ring.iter()
                    .copied()
                    .zip(ring.iter().copied().cycle().skip(1))
            })
            .filter(|&([ax, ay], [bx, by])| {
                (ay > y) != (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)
            });
        crossings.count() % 2 == 1
    })
}

fn signed_area(ring: &[[f64; 2]]) -> f64 {
    ring.iter()
        .zip(ring.iter().cycle().skip(1))
        .map(|(&[ax, ay], &[bx, by])| ax * by - bx * ay)
        .sum()
}

#[test]
fn rounding_caps_trim_on_long_edges_and_keeps_the_straight_sections() {
    let polygon = rectangle("risk", 0, false, [-2.0, 2.0, -0.1, 0.1]);
    let drawing = build_forecast(ForecastProduct::Cat, &[polygon]).unwrap();
    let shapes = coverage(&drawing, "TSTM");
    assert_eq!(shapes.len(), 1);
    assert_eq!(shapes[0].len(), 1);
    assert_eq!(bounds_of(&shapes[0]).unwrap(), [-2.0, 2.0, -0.1, 0.1]);

    let lon_trim = 32.0 / 111.195;
    let lat_trim = 0.20 * 0.2;
    let t = 1.0_f64 / 6.0;
    for expected in [
        [-2.0 + lon_trim, -0.1],
        [-2.0, -0.1 + lat_trim],
        [
            -2.0 + t.powi(2) * lon_trim,
            -0.1 + (1.0 - t).powi(2) * lat_trim,
        ],
    ] {
        assert!(shapes[0][0].iter().any(|&[x, y]| {
            (x - expected[0]).abs() < 0.000001 && (y - expected[1]).abs() < 0.000001
        }));
    }
    assert!(!contains(&shapes, [-1.999, -0.099]));
    assert!(contains(&shapes, [-1.70, -0.099999]));
}

#[test]
fn the_lower_envelope_buffers_the_rounded_tip() {
    let upper = ForecastPolygon {
        id: "tip".into(),
        level: 1,
        hatch: false,
        source_id: None,
        points: vec![[0.0, 0.0], [2.0, 0.0], [1.0, 3.0]],
    };
    let drawing = build_forecast(ForecastProduct::Cat, &[upper]).unwrap();
    let lower_north = bounds_of(&drawing.areas[0].rings).unwrap()[3];
    let upper_north = bounds_of(&drawing.areas[1].rings).unwrap()[3];
    assert!(upper_north < 2.9);
    assert_two_mile_clearance((lower_north - upper_north) * KM_PER_LATITUDE_DEGREE);
}

#[test]
fn a_high_risk_builds_six_strictly_nested_levels() {
    let high = rectangle("high", 5, false, [-100.0, -99.0, 39.5, 40.5]);
    let drawing = build_forecast(ForecastProduct::Cat, &[high]).unwrap();
    let labels: Vec<_> = drawing.areas.iter().map(|a| a.label.as_str()).collect();
    assert_eq!(labels, ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]);
    assert_eq!(drawing.areas[5].description, "High Risk");
    assert_eq!(drawing.areas[5].fill, "#EE99EE");
    assert_eq!(drawing.generated_contours.len(), 5);
    for (level, contour) in drawing.generated_contours.iter().enumerate() {
        assert_eq!(contour.source_id, "high");
        assert_eq!(usize::from(contour.level), level);
        assert!(
            (8..=12).contains(&contour.points.len()),
            "level {level} has {} handles",
            contour.points.len()
        );
        assert_ne!(contour.points.first(), contour.points.last());
        assert!(geometry::validate_ring(&contour.points).is_ok());
    }
    for (index, levels) in drawing.areas.windows(2).enumerate() {
        let [lower, upper] = levels else {
            unreachable!()
        };
        assert_eq!(upper.rank, index as i64 + 1);
        assert!(upper
            .rings
            .overlay(&lower.rings, OverlayRule::Difference, FillRule::EvenOdd)
            .is_empty());
        let [west, east, south, north] = bounds_of(&lower.rings).unwrap();
        let [w, e, s, n] = bounds_of(&upper.rings).unwrap();
        let lon_km = KM_PER_LATITUDE_DEGREE * 40.0_f64.to_radians().cos();
        for km in [
            (w - west) * lon_km,
            (east - e) * lon_km,
            (s - south) * KM_PER_LATITUDE_DEGREE,
            (north - n) * KM_PER_LATITUDE_DEGREE,
        ] {
            assert_two_mile_clearance(km);
        }
    }
}

#[test]
fn buffering_a_concave_risk_closes_a_gap_and_preserves_the_remaining_hole() {
    // The narrow entrance closes on the first rung and the bay fills on the third.
    let high = ForecastPolygon {
        id: "concave-high".into(),
        level: 5,
        hatch: false,
        source_id: None,
        points: vec![
            [-100.0, 35.0],
            [-96.0, 35.0],
            [-96.0, 36.98],
            [-97.0, 36.98],
            [-97.0, 36.93],
            [-99.0, 36.93],
            [-99.0, 37.07],
            [-97.0, 37.07],
            [-97.0, 37.02],
            [-96.0, 37.02],
            [-96.0, 39.0],
            [-100.0, 39.0],
        ],
    };
    let drawing = build_forecast(ForecastProduct::Cat, std::slice::from_ref(&high)).unwrap();
    let edit = drawing
        .generated_contours
        .iter()
        .find(|c| c.level == 4)
        .unwrap();
    let adjusted = build_forecast(
        ForecastProduct::Cat,
        &[
            high,
            ForecastPolygon {
                id: "edited".into(),
                level: edit.level,
                hatch: false,
                source_id: Some(edit.source_id.clone()),
                points: edit.points.clone(),
            },
        ],
    )
    .unwrap();
    let adjusted_moderate = coverage(&adjusted, "MDT");
    assert_eq!(adjusted_moderate[0].len(), 2);
    assert!(!contains(&adjusted_moderate, [-98.0, 37.0]));
    let high = coverage(&drawing, "HIGH");
    let moderate = coverage(&drawing, "MDT");
    assert_eq!(moderate[0].len(), 2);
    assert!(high
        .overlay(&moderate, OverlayRule::Difference, FillRule::EvenOdd)
        .is_empty());
    assert!(contains(&moderate, [-96.5, 37.0]));
    assert!(!contains(&moderate, [-98.0, 37.0]));
    assert!(contains(&coverage(&drawing, "SLGT"), [-98.0, 37.0]));
}

#[test]
fn a_narrow_concavity_uses_smaller_curves_without_closing_its_gap() {
    let polygon = ForecastPolygon {
        id: "narrow-gap".into(),
        level: 0,
        hatch: false,
        source_id: None,
        points: vec![
            [0.0, 0.0],
            [3.0, 0.0],
            [3.0, 1.0],
            [1.0, 1.0],
            [1.0, 2.7],
            [1.06, 2.7],
            [1.06, 1.03],
            [1.12, 1.03],
            [1.12, 3.0],
            [0.0, 3.0],
        ],
    };
    let drawing = build_forecast(ForecastProduct::Cat, &[polygon]).unwrap();
    let shapes = coverage(&drawing, "TSTM");
    assert_eq!(shapes.len(), 1);
    assert_eq!(shapes[0].len(), 1);
    assert!(geometry::validate_ring(&shapes[0][0]).is_ok());
    assert!(!contains(&shapes, [1.065, 1.02]));
    assert!(!contains(&shapes, [0.005, 0.005]));
    assert!(contains(&shapes, [0.04, 0.04]));
}

#[test]
fn tiny_and_nearly_collinear_rings_keep_their_area_and_winding() {
    for points in [
        vec![[0.0, 0.0], [0.000001, 0.0], [0.0, 0.000001]],
        vec![
            [0.0, 0.0],
            [5.0, 2.0],
            [10.0, 4.000010],
            [10.0, 4.000030],
            [5.0, 2.000025],
            [0.0, 0.000020],
        ],
    ] {
        let authored_area = signed_area(&points);
        let polygon = ForecastPolygon {
            id: "thin".into(),
            level: 1,
            hatch: false,
            source_id: None,
            points,
        };
        let drawing = build_forecast(ForecastProduct::Cat, &[polygon]).unwrap();
        let lower = coverage(&drawing, "TSTM");
        let shapes = coverage(&drawing, "MRGL");
        assert_eq!(lower.len(), 1);
        assert_eq!(shapes.len(), 1);
        assert_eq!(shapes[0].len(), 1);
        let ring = &shapes[0][0];
        assert!(geometry::validate_ring(ring).is_ok());
        assert!(signed_area(ring) >= 0.5 * authored_area);
        let seed = &drawing.generated_contours[0].points;
        assert!(geometry::validate_ring(seed).is_ok());
        let seed_area = signed_area(seed);
        let outline_area = signed_area(&lower[0][0]);
        assert!(
            seed_area >= 0.92 * outline_area,
            "the seed retained {:.3} of the lower outline for authored area {authored_area}",
            seed_area / outline_area
        );
    }
}

#[test]
fn thresholds_follow_the_product_specific_ladders_and_palette() {
    for (product, highest, expected) in [
        (ForecastProduct::Torn, 60, &[2, 5, 10, 15, 30, 45, 60][..]),
        (ForecastProduct::Hail, 60, &[5, 15, 30, 45, 60][..]),
        (ForecastProduct::Wind, 90, &[5, 15, 30, 45, 60, 75, 90][..]),
    ] {
        let drawing = build_forecast(
            product,
            &[rectangle(
                "risk",
                highest,
                false,
                [-100.0, -99.0, 35.0, 36.0],
            )],
        )
        .unwrap();
        let ranks: Vec<_> = drawing.areas.iter().map(|a| a.rank / 10).collect();
        assert_eq!(ranks, expected);
        for pair in drawing.areas.windows(2) {
            let lower_north = bounds_of(&pair[0].rings).unwrap()[3];
            let upper_north = bounds_of(&pair[1].rings).unwrap()[3];
            assert_two_mile_clearance((lower_north - upper_north) * KM_PER_LATITUDE_DEGREE);
        }
        let generated_levels: Vec<_> = drawing
            .generated_contours
            .iter()
            .map(|c| i64::from(c.level))
            .collect();
        assert_eq!(generated_levels, expected[..expected.len() - 1]);
        if product == ForecastProduct::Wind {
            assert_eq!(drawing.areas[5].stroke, "#7d00b3");
            assert_eq!(drawing.areas[6].fill, "#a366ff");
        } else {
            for level in [75, 90] {
                let risk = rectangle("risk", level, false, [-100.0, -99.0, 35.0, 36.0]);
                assert!(build_forecast(product, &[risk]).is_err());
            }
        }
    }
}

#[test]
fn authored_lower_risks_union_with_generated_coverage() {
    let upper = rectangle("high", 5, false, [-100.0, -99.0, 38.0, 39.0]);
    let mut lower = rectangle("manual", 4, false, [-102.0, -99.8, 38.0, 39.0]);
    lower.points.reverse();
    let other = rectangle("separate", 4, false, [-110.0, -109.0, 42.0, 43.0]);
    let inputs = [upper, lower, other];
    let drawing = build_forecast(ForecastProduct::Cat, &inputs).unwrap();
    let moderate = coverage(&drawing, "MDT");
    assert_eq!(moderate.len(), 2);
    assert!(contains(&moderate, [-101.7, 38.5]));
    assert!(contains(&moderate, [-98.98, 38.5]));
    assert!(contains(&moderate, [-109.5, 42.5]));
    assert!(contains(&coverage(&drawing, "ENH"), [-102.02, 38.5]));
    assert!(!contains(&coverage(&drawing, "HIGH"), [-101.7, 38.5]));
    let mut reversed = inputs;
    reversed.reverse();
    assert_eq!(
        drawing,
        build_forecast(ForecastProduct::Cat, &reversed).unwrap()
    );
}

#[test]
fn dense_source_seeds_have_a_compact_open_ring() {
    let mut polygon = rectangle("dense", 5, false, [-100.0, -99.0, 35.0, 36.0]);
    polygon.points = (0..256)
        .map(|i| {
            let angle = f64::from(i) * std::f64::consts::TAU / 256.0;
            [-99.5 + 0.5 * angle.cos(), 35.5 + 0.5 * angle.sin()]
        })
        .collect();
    let drawing = build_forecast(ForecastProduct::Cat, &[polygon]).unwrap();
    assert_eq!(drawing.generated_contours.len(), 5);
    for (contour, area) in drawing.generated_contours.iter().zip(&drawing.areas) {
        assert!(
            (8..=16).contains(&contour.points.len()),
            "{} has {} handles",
            area.label,
            contour.points.len()
        );
        assert!(geometry::validate_ring(&contour.points).is_ok());
        assert!(area.rings[0].len() > 2 * contour.points.len());
        assert!(signed_area(&contour.points) >= 0.92 * signed_area(&area.rings[0]));
    }
}

#[test]
fn continental_generated_handles_leave_the_dense_display_outline_intact() {
    let high = rectangle("high", 5, false, [-104.0, -97.0, 32.0, 39.0]);
    let source = geometry::round_ring(geometry::validate_ring(&high.points).unwrap());
    let drawing = build_forecast(ForecastProduct::Cat, &[high]).unwrap();
    assert_eq!(drawing.generated_contours.len(), 5);
    for (contour, area) in drawing.generated_contours.iter().zip(&drawing.areas) {
        let radius = TWO_MILES_KM * f64::from(5 - contour.level);
        let buffered = geometry::buffer(&[vec![source.clone()]], radius);
        let expected = geometry::union(buffered)
            .remove(0)
            .into_iter()
            .map(geometry::geographic_ring)
            .collect::<Vec<_>>();
        assert_eq!(area.rings, expected);
        assert!(area.rings[0].len() > 2 * contour.points.len());
        assert!(
            (8..=16).contains(&contour.points.len()),
            "{} has {} handles",
            area.label,
            contour.points.len()
        );
        assert!(geometry::validate_ring(&contour.points).is_ok());
    }
}

#[test]
fn complex_generated_handles_keep_deep_bays_and_narrow_lobes() {
    let mut risk = rectangle("comb", 1, false, [-112.0, -92.0, 34.0, 39.0]);
    risk.points.truncate(2);
    for tooth in 0..8 {
        let east = -92.0 - 2.5 * f64::from(tooth);
        risk.points.extend([
            [east, 39.0],
            [east - 1.25, 39.0],
            [east - 1.25, 35.5],
            [east - 2.5, 35.5],
        ]);
    }
    let drawing = build_forecast(ForecastProduct::Cat, &[risk]).unwrap();
    let seed = &drawing.generated_contours[0].points;
    assert!(seed.len() > 20);
    assert!(geometry::validate_ring(seed).is_ok());
    let seed = vec![vec![seed.clone()]];
    let display = coverage(&drawing, "TSTM");
    for shapes in [&seed, &display] {
        for tooth in 0..8 {
            let east = -92.0 - 2.5 * f64::from(tooth);
            assert!(contains(shapes, [east - 0.625, 38.0]));
            assert!(!contains(shapes, [east - 1.875, 37.0]));
        }
    }
}

#[test]
fn split_generated_coverage_seeds_the_largest_component() {
    let small = rectangle("small", 0, false, [-99.0, -98.5, 34.0, 34.5]);
    let large = rectangle("large", 0, false, [-104.0, -101.0, 35.0, 37.0]);
    let shapes: Vec<_> = [small, large]
        .into_iter()
        .map(|risk| vec![geometry::validate_ring(&risk.points).unwrap()])
        .collect();
    let seed = editable::points(&shapes).unwrap();
    assert!(geometry::validate_ring(&seed).is_ok());
    assert!(seed.iter().all(|point| point[0] <= -101.0));
    let coverage = vec![vec![seed]];
    assert!(contains(&coverage, [-102.0, 36.0]));
    assert!(!contains(&coverage, [-98.75, 34.25]));
}

#[test]
fn inward_adjustment_removes_its_previous_fringe_and_rebuilds_lower_levels() {
    let source = rectangle("high", 5, false, [-100.0, -99.0, 35.0, 36.0]);
    let previous = adjustment("high", "moderate", 4, [-100.07, -98.93, 34.93, 36.07]);
    let edit = adjustment("high", "moderate", 4, [-100.015, -98.985, 34.985, 36.015]);
    let baseline = build_forecast(ForecastProduct::Cat, &[source.clone(), previous]).unwrap();
    let edited = build_forecast(ForecastProduct::Cat, &[edit, source]).unwrap();
    for (label, previous_fringe) in [("MDT", [-100.055, 35.5]), ("ENH", [-100.09, 35.5])] {
        assert!(contains(&coverage(&baseline, label), previous_fringe));
        assert!(!contains(&coverage(&edited, label), previous_fringe));
    }
    assert_eq!(coverage(&baseline, "HIGH"), coverage(&edited, "HIGH"));
    assert_eq!(
        edited
            .generated_contours
            .iter()
            .map(|c| c.level)
            .collect::<Vec<_>>(),
        [0, 1, 2, 3]
    );
    for pair in edited.areas.windows(2) {
        assert!(pair[1]
            .rings
            .overlay(&pair[0].rings, OverlayRule::Difference, FillRule::EvenOdd)
            .is_empty());
    }
}

#[test]
fn an_adjustment_bends_only_within_two_miles_of_the_higher_risk() {
    let source = rectangle("high", 5, false, [-100.0, -99.0, 35.0, 36.0]);
    // The edit also leaves a strip of the higher risk beyond its south edge.
    let mut edit = adjustment("high", "moderate", 4, [-100.8, -98.886, 35.02, 36.6]);
    edit.points[1][0] = -99.0124;
    let edited = build_forecast(ForecastProduct::Cat, &[source, edit]).unwrap();
    let moderate = coverage(&edited, "MDT");
    assert!(contains(&moderate, [-100.6, 35.4]));
    let lon_km = KM_PER_LATITUDE_DEGREE * 35.5_f64.to_radians().cos();
    let buffered_east = -99.0 + TWO_MILES_KM / lon_km;
    let epsilon = CLEARANCE_TOLERANCE_KM / lon_km;
    // This slanted edge clears the same straight high-risk edge by 1.3 km
    // at 35.35°N and 3.8 km at 35.7°N. Only the first section should move.
    for (latitude, east) in [(35.35, buffered_east), (35.7, -98.958)] {
        let just_inside = contains(&moderate, [east - epsilon, latitude]);
        let just_outside = contains(&moderate, [east + epsilon, latitude]);
        assert!(
            just_inside && !just_outside,
            "expected the east edge at longitude {east} at latitude {latitude}"
        );
    }
    let extended_west = [-100.82, 35.4];
    assert!(!contains(&moderate, extended_west));
    assert!(contains(&coverage(&edited, "ENH"), extended_west));
    assert!(coverage(&edited, "HIGH")
        .overlay(&moderate, OverlayRule::Difference, FillRule::EvenOdd)
        .is_empty());
}

#[test]
fn removing_an_adjustment_restores_its_seed_and_a_missing_source_leaves_an_authored_area() {
    let source = rectangle("high", 5, false, [-100.0, -99.0, 35.0, 36.0]);
    let edit = adjustment("high", "moderate", 4, [-100.15, -98.85, 34.85, 36.15]);
    let baseline = build_forecast(ForecastProduct::Cat, std::slice::from_ref(&source)).unwrap();
    let mut inputs = vec![source, edit.clone()];
    let edited = build_forecast(ForecastProduct::Cat, &inputs).unwrap();
    assert!(baseline.generated_contours.iter().any(|c| c.level == 4));
    assert!(!edited.generated_contours.iter().any(|c| c.level == 4));
    inputs.pop();
    assert_eq!(
        baseline,
        build_forecast(ForecastProduct::Cat, &inputs).unwrap()
    );

    let orphan = build_forecast(ForecastProduct::Cat, std::slice::from_ref(&edit)).unwrap();
    let standalone = ForecastPolygon {
        source_id: None,
        ..edit
    };
    assert_eq!(
        orphan,
        build_forecast(ForecastProduct::Cat, &[standalone]).unwrap()
    );
    assert_eq!(orphan.generated_contours.len(), 4);
    assert!(orphan
        .generated_contours
        .iter()
        .all(|c| c.source_id == "moderate"));
}

#[test]
fn a_dense_saved_adjustment_keeps_the_effect_of_an_inserted_vertex() {
    let source = rectangle("high", 5, false, [-100.0, -99.0, 35.0, 36.0]);
    let mut saved = adjustment("high", "customized", 4, [-101.0, -98.0, 34.0, 37.0]);
    saved.points = (0..40)
        .map(|i| {
            let angle = f64::from(i) * std::f64::consts::TAU / 40.0;
            [-99.5 + angle.cos(), 35.5 + angle.sin()]
        })
        .collect();
    let before_insert =
        build_forecast(ForecastProduct::Cat, &[source.clone(), saved.clone()]).unwrap();
    saved.points.insert(1, [-98.46, 35.58]);
    let restored = build_forecast(ForecastProduct::Cat, &[source, saved]).unwrap();
    let near_insert = [-98.48, 35.58];
    assert!(!contains(&coverage(&before_insert, "MDT"), near_insert));
    assert!(contains(&coverage(&restored, "MDT"), near_insert));
    assert!(restored.generated_contours.iter().all(|c| c.level < 4));
}

#[test]
fn adjustments_are_independent_across_sources_and_rungs() {
    let west = rectangle("west", 5, false, [-100.0, -99.0, 35.0, 36.0]);
    let east = rectangle("east", 5, false, [-99.2, -98.0, 35.0, 36.0]);
    let west_moderate = adjustment("west", "west-mdt", 4, [-100.15, -98.9, 34.85, 36.15]);
    let west_slight = adjustment("west", "west-slgt", 2, [-101.5, -98.7, 34.3, 36.7]);
    let baseline = build_forecast(ForecastProduct::Cat, &[west.clone(), east.clone()]).unwrap();
    let mut inputs = [west, east, west_moderate, west_slight];
    let drawing = build_forecast(ForecastProduct::Cat, &inputs).unwrap();
    let moderate = coverage(&drawing, "MDT");
    let west_fringe = [-100.12, 35.5];
    assert!(!contains(&coverage(&baseline, "MDT"), west_fringe));
    assert!(contains(&moderate, west_fringe));
    assert!(contains(&moderate, [-97.98, 35.5]));
    for contour in &drawing.generated_contours {
        if contour.source_id == "east" || contour.level == 3 {
            let previous = baseline
                .generated_contours
                .iter()
                .find(|c| c.source_id == contour.source_id && c.level == contour.level)
                .unwrap();
            assert_eq!(contour == previous, contour.source_id == "east");
        }
    }
    assert_eq!(
        drawing
            .generated_contours
            .iter()
            .filter(|c| c.source_id == "west")
            .map(|c| c.level)
            .collect::<Vec<_>>(),
        [0, 1, 3]
    );
    assert!(contains(&coverage(&drawing, "MRGL"), [-101.52, 35.5]));
    inputs.reverse();
    assert_eq!(
        drawing,
        build_forecast(ForecastProduct::Cat, &inputs).unwrap()
    );
}

#[test]
fn clipping_preserves_a_probability_hole_and_reports_each_authored_hatch() {
    let mut inputs = vec![
        rectangle("bottom", 5, false, [-100.0, -96.0, 33.0, 34.0]),
        rectangle("top", 5, false, [-100.0, -96.0, 36.0, 37.0]),
        rectangle("left", 5, false, [-100.0, -99.0, 33.5, 36.5]),
        rectangle("right", 5, false, [-97.0, -96.0, 33.5, 36.5]),
    ];
    let base = build_forecast(ForecastProduct::Wind, &inputs).unwrap();
    inputs.extend([
        rectangle("partial", 3, true, [-101.0, -95.0, 32.0, 38.0]),
        rectangle("in-hole", 1, true, [-98.8, -97.2, 34.5, 35.5]),
        rectangle("touches-edge", 2, true, [-96.0, -95.0, 33.5, 36.0]),
        rectangle(
            "in-trimmed-corner",
            1,
            true,
            [-99.999, -99.99, 33.001, 33.01],
        ),
    ]);
    let drawing = build_forecast(ForecastProduct::Wind, &inputs).unwrap();
    assert_eq!(&drawing.areas[..1], &base.areas);
    assert_eq!(drawing.clipped_hatches, ["partial"]);
    assert_eq!(
        drawing.hidden_hatches,
        ["in-hole", "touches-edge", "in-trimmed-corner"]
    );
    assert!(coverage(&drawing, "CIG3")
        .overlay(
            &coverage(&drawing, "5%"),
            OverlayRule::Xor,
            FillRule::EvenOdd
        )
        .is_empty());
    let rings = &drawing.areas[1].rings;
    assert_eq!(rings.len(), 2);
    assert!(signed_area(&rings[0]) > 0.0);
    assert!(signed_area(&rings[1]) < 0.0);
    assert!(!contains(&coverage(&drawing, "CIG3"), [-98.0, 35.0]));
}

#[test]
fn higher_cig_takes_overlap_and_same_class_hatches_dissolve() {
    let inputs = [
        rectangle("probability", 2, false, [-100.0, -95.0, 35.0, 40.0]),
        rectangle("cig1-west", 1, true, [-99.0, -96.5, 36.0, 39.0]),
        rectangle("cig1-east", 1, true, [-98.5, -96.0, 36.0, 39.0]),
        rectangle("cig1-covered", 1, true, [-98.0, -97.0, 37.0, 38.0]),
        rectangle("cig3", 3, true, [-98.4, -96.7, 36.5, 38.5]),
    ];
    let drawing = build_forecast(ForecastProduct::Torn, &inputs).unwrap();
    assert!(drawing.clipped_hatches.is_empty());
    assert!(drawing.hidden_hatches.is_empty());
    let low = coverage(&drawing, "CIG1");
    let high = coverage(&drawing, "CIG3");
    assert_eq!(low.len(), 1);
    assert_eq!(low[0].len(), 2);
    assert!(coverage(&drawing, "CIG2").is_empty());
    assert!(low
        .overlay(&high, OverlayRule::Intersect, FillRule::EvenOdd)
        .is_empty());
    assert!(contains(&low, [-98.7, 37.0]));
    assert!(contains(&high, [-97.5, 37.5]));
    let trimmed_high_corner = [-98.395, 36.505];
    assert!(!contains(&high, trimmed_high_corner));
    assert!(contains(&low, trimmed_high_corner));
    assert_eq!(drawing.areas[2].hatch_level, 3);
    assert_eq!(drawing.areas[2].rank, 10_003);
}

#[test]
fn hatch_uses_generated_lowest_envelope_and_rebuilds_after_edits() {
    let mut upper = rectangle("upper", 10, false, [-100.0, -99.0, 35.0, 36.0]);
    let manual = rectangle("manual", 2, false, [-115.0, -114.0, 30.0, 31.0]);
    let hatch = rectangle("hatch", 2, true, [-100.062, -100.044, 35.3, 35.7]);
    let active = build_forecast(
        ForecastProduct::Torn,
        &[upper.clone(), manual.clone(), hatch.clone()],
    )
    .unwrap();
    let visible = coverage(&active, "CIG2");
    assert!(contains(&visible, [-100.05, 35.5]));
    assert!(visible
        .overlay(
            &coverage(&active, "5%"),
            OverlayRule::Intersect,
            FillRule::EvenOdd
        )
        .is_empty());
    assert!(active.hidden_hatches.is_empty() && active.clipped_hatches.is_empty());

    upper.points.iter_mut().for_each(|point| point[0] += 5.0);
    let moved = build_forecast(
        ForecastProduct::Torn,
        &[upper, manual.clone(), hatch.clone()],
    )
    .unwrap();
    assert_eq!(moved.hidden_hatches, ["hatch"]);
    assert!(!contains(&coverage(&moved, "2%"), [-100.05, 35.5]));
    assert!(contains(&coverage(&moved, "10%"), [-94.5, 35.5]));

    let removed = build_forecast(ForecastProduct::Torn, &[manual, hatch.clone()]).unwrap();
    assert_eq!(removed.areas.len(), 1);
    assert_eq!(removed.hidden_hatches, ["hatch"]);
    assert!(contains(&coverage(&removed, "2%"), [-114.5, 30.5]));
    let hatch_only = build_forecast(ForecastProduct::Torn, &[hatch]).unwrap();
    assert!(hatch_only.areas.is_empty());
    assert_eq!(hatch_only.hidden_hatches, ["hatch"]);
}

#[test]
fn hatches_clip_to_the_adjusted_lowest_envelope() {
    let source = rectangle("upper", 10, false, [-100.0, -99.0, 35.0, 36.0]);
    let old_fringe = rectangle("old-fringe", 2, true, [-100.30, -100.25, 35.3, 35.7]);
    let new_fringe = rectangle("new-fringe", 3, true, [-98.05, -97.4, 35.3, 35.7]);
    let previous = adjustment("upper", "adjustment", 2, [-100.32, -98.81, 34.4, 36.6]);
    let edit = adjustment("upper", "adjustment", 2, [-100.06, -97.6, 34.4, 36.6]);
    let baseline = build_forecast(
        ForecastProduct::Torn,
        &[
            source.clone(),
            old_fringe.clone(),
            new_fringe.clone(),
            previous,
        ],
    )
    .unwrap();
    assert_eq!(baseline.hidden_hatches, ["new-fringe"]);
    assert!(baseline.clipped_hatches.is_empty());
    let edited = build_forecast(
        ForecastProduct::Torn,
        &[source, old_fringe, new_fringe, edit],
    )
    .unwrap();
    assert_eq!(edited.hidden_hatches, ["old-fringe"]);
    assert_eq!(edited.clipped_hatches, ["new-fringe"]);
    let lowest = coverage(&edited, "2%");
    let lowest_west = bounds_of(&lowest[0]).unwrap()[0];
    let higher_west = bounds_of(&coverage(&edited, "5%")[0]).unwrap()[0];
    let lon_km = KM_PER_LATITUDE_DEGREE * 35.5_f64.to_radians().cos();
    assert_two_mile_clearance((higher_west - lowest_west) * lon_km);
    let hatch = coverage(&edited, "CIG3");
    assert!(contains(&hatch, [-97.8, 35.5]));
    assert!(hatch
        .overlay(&lowest, OverlayRule::Difference, FillRule::EvenOdd)
        .is_empty());
    assert_eq!(
        edited
            .generated_contours
            .iter()
            .map(|c| c.level)
            .collect::<Vec<_>>(),
        [5]
    );
}

#[test]
fn antimeridian_cuts_keep_both_sides_local_and_hatch_clipped() {
    let mut inputs = [
        rectangle("upper", 15, false, [179.6, -179.7, 34.0, 35.0]),
        rectangle("manual", 5, false, [-179.68, -178.9, 34.2, 34.8]),
        rectangle("hatch", 1, true, [179.7, -178.7, 34.4, 34.6]),
    ];
    let drawing = build_forecast(ForecastProduct::Wind, &inputs).unwrap();
    let upper = coverage(&drawing, "15%");
    let lower = coverage(&drawing, "5%");
    let hatch = coverage(&drawing, "CIG1");
    assert_eq!(upper.len(), 2);
    assert_eq!(lower.len(), 2);
    assert_eq!(hatch.len(), 2);
    assert_eq!(drawing.clipped_hatches, ["hatch"]);
    for x in [-179.8, 179.8] {
        assert!(contains(&upper, [x, 34.5]));
        assert!(contains(&hatch, [x, 34.5]));
    }
    assert!(!contains(&upper, [179.601, 34.001]));
    assert!(!contains(&upper, [-179.701, 34.001]));
    assert!(contains(&lower, [-179.0, 34.5]));
    assert!(!contains(&lower, [0.0, 34.5]));
    assert!(hatch
        .overlay(&lower, OverlayRule::Difference, FillRule::EvenOdd)
        .is_empty());
    assert!(upper
        .overlay(&lower, OverlayRule::Difference, FillRule::EvenOdd)
        .is_empty());
    for ring in drawing.areas.iter().flat_map(|area| &area.rings) {
        let [west, east, _, _] = bounds_of(std::slice::from_ref(ring)).unwrap();
        assert!(west >= -180.0 && east <= 180.0 && east - west < 2.0);
        assert_ne!(ring.first(), ring.last());
    }
    inputs[0].points.rotate_left(1);
    assert_eq!(
        drawing,
        build_forecast(ForecastProduct::Wind, &inputs).unwrap()
    );

    let nearby = rectangle("nearby", 1, false, [179.7, 179.98, 34.0, 35.0]);
    let drawing = build_forecast(ForecastProduct::Cat, &[nearby]).unwrap();
    assert_eq!(coverage(&drawing, "MRGL").len(), 1);
    assert!(contains(&coverage(&drawing, "TSTM"), [-179.99, 34.5]));
}

#[test]
fn antimeridian_source_has_one_editable_contour_and_accepts_it_as_an_adjustment() {
    let source = rectangle("upper", 15, false, [179.6, -179.7, 34.0, 35.0]);
    let baseline = build_forecast(ForecastProduct::Wind, std::slice::from_ref(&source)).unwrap();
    let [seed] = baseline.generated_contours.as_slice() else {
        panic!("expected one generated probability");
    };
    assert_eq!(seed.level, 5);
    assert!(seed.points.iter().any(|p| p[0] < -179.0));
    assert!(seed.points.iter().any(|p| p[0] > 179.0));
    assert!(geometry::validate_ring(&seed.points).is_ok());
    let edit = ForecastPolygon {
        id: "adjustment".into(),
        level: seed.level,
        hatch: false,
        source_id: Some(seed.source_id.clone()),
        points: seed.points.clone(),
    };
    let edited = build_forecast(ForecastProduct::Wind, &[source, edit]).unwrap();
    assert!(edited.generated_contours.is_empty());
    let lower = coverage(&edited, "5%");
    assert_eq!(lower.len(), 2);
    assert!(contains(&lower, [179.58, 34.5]));
    assert!(contains(&lower, [-179.68, 34.5]));
    assert!(!contains(&lower, [0.0, 34.5]));
    assert!(coverage(&edited, "15%")
        .overlay(&lower, OverlayRule::Difference, FillRule::EvenOdd)
        .is_empty());
}

#[test]
fn the_polar_edge_bounds_the_buffer() {
    let high = rectangle("high", 5, false, [10.0, 11.0, 89.6, 90.0]);
    let drawing = build_forecast(ForecastProduct::Cat, &[high]).unwrap();
    let [west, east, south, north] = bounds_of(&drawing.areas[0].rings).unwrap();
    assert_eq!(north, 90.0);
    assert!(south > 87.0 && east - west < 45.0);
    assert_eq!(drawing.generated_contours.len(), 5);
    for contour in &drawing.generated_contours {
        assert!(geometry::validate_ring(&contour.points).is_ok());
    }
    let upper = coverage(&drawing, "HIGH");
    assert!(contains(&upper, [10.5, 89.9999]));
    assert!(!contains(&upper, [10.0001, 89.9999]));
}

#[test]
fn rejects_invalid_rings_without_exposing_ids() {
    let good = rectangle("valid", 0, false, [-100.0, -99.0, 35.0, 36.0]);
    for points in [
        vec![[0.0, 0.0], [1.0, 1.0]],
        vec![[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]],
        vec![[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]],
        vec![[0.0, 0.0], [3.0, 2.0], [0.0, 2.0], [2.0, 0.0]],
        vec![[0.0, 0.0], [2.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        vec![[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [1.0, 0.0], [0.0, 2.0]],
        vec![[0.0, 0.0], [180.0, 0.0], [179.0, 1.0]],
        vec![[0.0, 0.0], [f64::NAN, 1.0], [0.0, 1.0]],
        vec![[0.0, 0.0], [1.0, f64::INFINITY], [0.0, 1.0]],
        vec![[0.0, 0.0], [1.0, 91.0], [0.0, 1.0]],
        vec![[0.0, 0.0]; 257],
    ] {
        let input = ForecastPolygon {
            id: "opaque-id".into(),
            points,
            ..good.clone()
        };
        let error = build_forecast(ForecastProduct::Cat, &[good.clone(), input]).unwrap_err();
        assert!(error.starts_with("Area 2: "), "{error}");
        assert!(!error.contains("opaque-id"));
    }
}

#[test]
fn validates_level_id_and_polygon_limits() {
    let good = rectangle("opaque-id", 0, false, [-100.0, -99.0, 35.0, 36.0]);
    for (product, hatch, level) in [
        (ForecastProduct::Cat, false, 6),
        (ForecastProduct::Cat, true, 1),
        (ForecastProduct::Torn, false, 3),
        (ForecastProduct::Torn, true, 0),
        (ForecastProduct::Hail, false, 2),
        (ForecastProduct::Hail, true, 3),
        (ForecastProduct::Wind, true, 4),
    ] {
        assert!(build_forecast(
            product,
            &[ForecastPolygon {
                hatch,
                level,
                ..good.clone()
            }]
        )
        .is_err());
    }
    for id in [
        String::new(),
        " area".into(),
        "area\n".into(),
        "a".repeat(65),
    ] {
        assert!(build_forecast(
            ForecastProduct::Cat,
            &[ForecastPolygon { id, ..good.clone() }]
        )
        .unwrap_err()
        .starts_with("Area 1: ID"));
    }
    assert_eq!(
        build_forecast(ForecastProduct::Cat, &[good.clone(), good.clone()]).unwrap_err(),
        "Area 2: duplicate area ID",
    );
    assert!(
        build_forecast(ForecastProduct::Cat, &vec![good.clone(); 65])
            .unwrap_err()
            .contains("64 polygons")
    );

    let mut within_limits = (0..64)
        .map(|index| ForecastPolygon {
            id: index.to_string(),
            ..good.clone()
        })
        .collect::<Vec<_>>();
    within_limits[0].id = "a".repeat(64);
    within_limits[0].points = (0..256)
        .map(|index| {
            let angle = f64::from(index) * std::f64::consts::TAU / 256.0;
            [-99.5 + 0.4 * angle.cos(), 35.5 + 0.4 * angle.sin()]
        })
        .collect();
    assert_eq!(
        build_forecast(ForecastProduct::Cat, &within_limits)
            .unwrap()
            .areas
            .len(),
        1
    );
}

#[test]
fn rejects_duplicate_and_invalid_adjustment_links() {
    let bounds = [-100.0, -99.0, 35.0, 36.0];
    let source = rectangle("source", 30, false, bounds);
    let edit = adjustment("source", "edit", 15, bounds);
    let result =
        |areas: &[ForecastPolygon]| build_forecast(ForecastProduct::Torn, areas).unwrap_err();
    for invalid in [
        ForecastPolygon {
            level: 30,
            ..edit.clone()
        },
        ForecastPolygon {
            hatch: true,
            level: 3,
            ..edit.clone()
        },
        adjustment(" edit", "other", 10, bounds),
        adjustment("edit", "edit", 10, bounds),
    ] {
        assert!(result(&[source.clone(), invalid]).starts_with("Area 2: "));
    }
    let nested = adjustment("edit", "nested", 10, bounds);
    assert!(result(&[source.clone(), edit.clone(), nested]).contains("authored risk"));
    let hatch = rectangle("hatch", 3, true, bounds);
    assert!(
        result(&[hatch, adjustment("hatch", "adjustment", 2, bounds)]).contains("authored risk")
    );
    let duplicate = adjustment("source", "duplicate", 15, bounds);
    assert!(result(&[source, edit, duplicate]).contains("duplicate adjustment"));
}

#[test]
fn serde_matches_the_outlook_area_contract() {
    let input = json!({
        "id": "thunder", "level": 0, "hatch": false,
        "points": [[-100, 35], [-99, 35], [-99, 36], [-100, 36]],
    });
    let product = serde_json::from_value(json!("cat")).unwrap();
    let polygon = serde_json::from_value(input.clone()).unwrap();
    let mut unknown = input;
    unknown["extra"] = json!(true);
    assert!(serde_json::from_value::<ForecastPolygon>(unknown).is_err());
    assert!(serde_json::from_value::<ForecastProduct>(json!("prob")).is_err());
    let output = serde_json::to_value(build_forecast(product, &[polygon]).unwrap()).unwrap();
    assert_eq!(output["clippedHatches"], json!([]));
    assert_eq!(output["hiddenHatches"], json!([]));
    assert_eq!(output["generatedContours"], json!([]));
    assert_eq!(output.as_object().unwrap().len(), 4);
    let area = output["areas"][0].as_object().unwrap();
    assert_eq!(area.len(), 8);
    for (key, value) in [
        ("label", json!("TSTM")),
        ("description", json!("General Thunderstorms")),
        ("fill", json!("#C1E9C1")),
        ("stroke", json!("#55BB55")),
        ("rank", json!(0)),
        ("hatch", json!(false)),
        ("hatchLevel", json!(0)),
    ] {
        assert_eq!(area[key], value);
    }
    let rings = area["rings"].as_array().unwrap();
    assert_eq!(rings.len(), 1);
    assert!(rings[0].as_array().unwrap().len() > 4);

    let source = rectangle("marginal", 1, false, [-100.0, -99.0, 35.0, 36.0]);
    let drawing = build_forecast(product, std::slice::from_ref(&source)).unwrap();
    let contours = serde_json::to_value(&drawing).unwrap();
    let contour = &contours["generatedContours"][0];
    assert_eq!(contour["sourceId"], "marginal");
    assert_eq!(contour["level"], 0);
    assert_eq!(contour.as_object().unwrap().len(), 3);
    let edit = serde_json::from_value(json!({
        "id": "edit", "level": contour["level"], "hatch": false,
        "sourceId": contour["sourceId"], "points": contour["points"],
    }))
    .unwrap();
    let adjusted = build_forecast(product, &[source, edit]).unwrap();
    assert!(adjusted.generated_contours.is_empty());
}

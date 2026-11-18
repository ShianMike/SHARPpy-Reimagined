use super::OutlookArea;
use crate::place_names::conus_state_boundaries;
use i_overlay::core::{fill_rule::FillRule, overlay_rule::OverlayRule};
use i_overlay::float::single::SingleFloatOverlay;

// ANSI state codes stored on the bundled Census boundary rings.
const STATES: &[(u8, &str)] = &[
    (1, "Alabama"),
    (4, "Arizona"),
    (5, "Arkansas"),
    (6, "California"),
    (8, "Colorado"),
    (9, "Connecticut"),
    (10, "Delaware"),
    (11, "District of Columbia"),
    (12, "Florida"),
    (13, "Georgia"),
    (16, "Idaho"),
    (17, "Illinois"),
    (18, "Indiana"),
    (19, "Iowa"),
    (20, "Kansas"),
    (21, "Kentucky"),
    (22, "Louisiana"),
    (23, "Maine"),
    (24, "Maryland"),
    (25, "Massachusetts"),
    (26, "Michigan"),
    (27, "Minnesota"),
    (28, "Mississippi"),
    (29, "Missouri"),
    (30, "Montana"),
    (31, "Nebraska"),
    (32, "Nevada"),
    (33, "New Hampshire"),
    (34, "New Jersey"),
    (35, "New Mexico"),
    (36, "New York"),
    (37, "North Carolina"),
    (38, "North Dakota"),
    (39, "Ohio"),
    (40, "Oklahoma"),
    (41, "Oregon"),
    (42, "Pennsylvania"),
    (44, "Rhode Island"),
    (45, "South Carolina"),
    (46, "South Dakota"),
    (47, "Tennessee"),
    (48, "Texas"),
    (49, "Utah"),
    (50, "Vermont"),
    (51, "Virginia"),
    (53, "Washington"),
    (54, "West Virginia"),
    (55, "Wisconsin"),
    (56, "Wyoming"),
];

pub(super) fn highest_risk_states(areas: &[OutlookArea]) -> Vec<&'static str> {
    let Some(rank) = areas
        .iter()
        .filter(|area| !area.hatch)
        .map(|area| area.rank)
        .max()
    else {
        return Vec::new();
    };
    let boundaries = conus_state_boundaries();
    let mut affected = [false; u8::MAX as usize + 1];

    for area in areas.iter().filter(|area| !area.hatch && area.rank == rank) {
        let [west, east, south, north] = area.bounds;
        for boundary in boundaries {
            let state = usize::from(boundary.state);
            let [left, right, bottom, top] = boundary.bounds;
            if affected[state] || east <= left || west >= right || north <= bottom || south >= top {
                continue;
            }
            // Even-odd clipping preserves holes and excludes line-only border contact.
            affected[state] = !area
                .rings
                .overlay(&boundary.points, OverlayRule::Intersect, FillRule::EvenOdd)
                .is_empty();
        }
    }
    STATES
        .iter()
        .filter_map(|&(id, name)| affected[usize::from(id)].then_some(name))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spc_outlook::bounds_of;

    fn rectangle([west, east, south, north]: [f64; 4]) -> Vec<[f64; 2]> {
        vec![[west, south], [east, south], [east, north], [west, north]]
    }

    fn risk(rank: i64, hatch: bool, rings: Vec<Vec<[f64; 2]>>) -> OutlookArea {
        OutlookArea {
            bounds: bounds_of(&rings).unwrap(),
            rings,
            stroke: "#000000".into(),
            fill: "#FFFFFF".into(),
            label: String::new(),
            description: String::new(),
            rank,
            hatch,
            hatch_level: u8::from(hatch),
        }
    }

    #[test]
    fn includes_all_top_rank_polygons_without_lower_risk_or_hatching() {
        let florida = rectangle([-82.3, -81.7, 27.4, 28.0]);
        let plains = rectangle([-100.0, -97.0, 33.0, 36.0]);
        let washington = rectangle([-122.0, -121.0, 46.0, 47.0]);
        let areas = [
            risk(1, false, vec![washington.clone()]),
            risk(2, false, vec![florida]),
            risk(2, false, vec![plains.clone()]),
            risk(2, false, vec![plains]),
            risk(10_000, true, vec![washington]),
        ];
        assert_eq!(
            highest_risk_states(&areas),
            ["Florida", "Oklahoma", "Texas"]
        );
    }

    #[test]
    fn respects_holes_and_offshore_outlooks() {
        // Delaware lies completely inside an SPC polygon hole.
        let delmarva = risk(
            3,
            false,
            vec![
                rectangle([-77.0, -74.0, 38.0, 40.0]),
                rectangle([-75.85, -75.0, 38.4, 39.86]),
            ],
        );
        let states = highest_risk_states(&[delmarva]);
        assert!(states.contains(&"Maryland"));
        assert!(!states.contains(&"Delaware"));

        let gulf = risk(0, false, vec![rectangle([-91.0, -89.0, 26.0, 27.0])]);
        assert!(highest_risk_states(&[gulf]).is_empty());
        assert!(highest_risk_states(&[]).is_empty());
    }
}

use super::*;

fn grid(first: f64, second: f64) -> Grid {
    Grid::new(Specification {
        lat_origin: 38.5,
        lon_origin: -97.5,
        lat_1: first,
        lat_2: second,
        radius: 6371229.0,
        shape: (1059, 1799),
        spacing: 3000.0,
        x0: -2697520.1425219304,
        y0: -1587306.1525566636,
    })
    .unwrap()
}

#[test]
fn tangent_secant_and_southern_cones_round_trip_geographic_points() {
    for (first, second) in [(38.5, 38.5), (33.0, 45.0), (-33.0, -45.0)] {
        let grid = grid(first, second);
        for lon in [-130.0, -97.5, -65.0] {
            for lat in [-50.0, -20.0, 22.0, 38.5, 52.0] {
                let (x, y) = grid.forward(lon, lat);
                let (back_lon, back_lat) = grid.inverse(x, y);
                assert!((back_lon - lon).abs() < 1e-9);
                assert!((back_lat - lat).abs() < 1e-9);
            }
        }
    }
}

#[test]
fn operational_hrrr_origin_curved_edges_and_membership_match_the_published_grid() {
    let grid = grid(38.5, 38.5);
    let outline = grid.outline(32);
    assert_eq!(outline.len(), 129);
    assert_eq!(outline.first(), outline.last());
    assert!((outline[0].0 - (-122.719528)).abs() < 1e-9);
    assert!((outline[0].1 - 21.138123).abs() < 1e-9);
    assert!(outline[16].1 > outline[0].1 + 3.0);
    assert!(grid.contains(35.2, -97.4));
    assert!(!grid.contains(38.5, 82.5));
    for (lat, lon) in [(f64::NAN, -97.5), (38.5, f64::INFINITY), (200.0, -97.5)] {
        assert!(!grid.contains(lat, lon));
    }
    let (west, east, south, north) = grid.bounds(32);
    assert!(west < outline[0].0 && east > outline[32].0 && north > 50.0);
    assert!((south - outline[0].1).abs() < 1e-9);
}

#[test]
fn bounds_preserve_closed_edges_and_degenerate_single_cell_grids() {
    let mut spec = grid(38.5, 38.5).spec;
    spec.shape = (1, 1);
    let grid = Grid::new(spec).unwrap();
    let outline = grid.outline(2);
    assert_eq!(outline.len(), 9);
    assert!(outline.iter().all(|p| *p == outline[0]));
    assert_eq!(grid.extent(), (grid.spec.x0, grid.spec.y0));
}

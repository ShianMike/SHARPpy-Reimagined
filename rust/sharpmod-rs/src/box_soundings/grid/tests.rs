use super::*;

fn regular() -> Grid {
    Grid {
        nx: 4,
        ny: 4,
        first_longitude: 0.0,
        first_latitude: 0.0,
        radius: 6_371_229.0,
        scanning: Scanning {
            j_positive: true,
            ..Scanning::default()
        },
        projection: Projection::RegularLatLon {
            longitude_increment: 1.0,
            latitude_increment: 1.0,
        },
    }
}

#[test]
fn selects_overlapping_cells_rather_than_a_nearest_point_sample() {
    let grid = regular();
    let bounds = Bounds::new(0.25, 0.25, 1.25, 1.25).unwrap();
    let cells = grid.select(bounds, Limits::default(), &|| false).unwrap();
    assert_eq!(
        cells.iter().map(|cell| cell.index).collect::<Vec<_>>(),
        vec![0, 1, 4, 5]
    );
    let area: f64 = cells.iter().map(|cell| cell.overlap_area).sum();
    assert!((area / bounds.area(grid.radius) - 1.0).abs() < 1.0e-10);
    assert!(cells[0].overlap_area < cells[3].overlap_area / 4.0);
    let tiny = Bounds::new(0.01, 0.01, 0.02, 0.02).unwrap();
    let one = grid.select(tiny, Limits::default(), &|| false).unwrap();
    assert_eq!(one.len(), 1);
    assert_eq!(one[0].index, 0);
}

#[test]
fn respects_all_native_scanning_orders() {
    let grid = regular();
    assert_eq!(grid.native_index(1, 2), 9);
    let alternate = Grid {
        scanning: Scanning {
            alternating: true,
            ..grid.scanning
        },
        ..grid
    };
    assert_eq!(alternate.native_index(0, 1), 7);
    let column = Grid {
        scanning: Scanning {
            j_consecutive: true,
            ..grid.scanning
        },
        ..grid
    };
    assert_eq!(column.native_index(1, 2), 6);
    let both = Grid {
        scanning: Scanning {
            j_consecutive: true,
            alternating: true,
            ..grid.scanning
        },
        ..grid
    };
    assert_eq!(both.native_index(1, 0), 7);
}

#[test]
fn global_grids_wrap_native_indexes_across_the_dateline() {
    let grid = Grid {
        nx: 360,
        ny: 4,
        ..regular()
    };
    let bounds = Bounds::new(179.75, 0.1, -179.75, 0.2).unwrap();
    let cells = grid.select(bounds, Limits::default(), &|| false).unwrap();
    assert_eq!(cells.len(), 1);
    assert_eq!(cells[0].index, 180);
    let zero = Bounds::new(-0.25, 0.1, 0.25, 0.2).unwrap();
    let cells = grid.select(zero, Limits::default(), &|| false).unwrap();
    assert_eq!(cells.len(), 1);
    assert_eq!(cells[0].index, 0);
}

#[test]
fn lambert_round_trips_northern_southern_and_secant_grids() {
    for (longitude, p1, p2, latitude) in [
        (-97.5, 38.5, 38.5, 35.0),
        (-95.0, 25.0, 45.0, 50.0),
        (140.0, -30.0, -60.0, -40.0),
    ] {
        let projection = Lambert::new(longitude, p1, p2, 6_371_229.0).unwrap();
        for offset in [-10.0, 0.0, 10.0] {
            let (x, y) = projection.project(longitude + offset, latitude);
            let (actual_lon, actual_lat) = projection.unproject(x, y);
            assert!((actual_lon - longitude - offset).abs() < 1.0e-10);
            assert!((actual_lat - latitude).abs() < 1.0e-10);
        }
    }
}

#[test]
fn projected_cells_partition_the_geographic_selection_and_have_nonuniform_areas() {
    let projection = Lambert::new(-97.5, 38.5, 38.5, 6_371_229.0).unwrap();
    let grid = Grid {
        nx: 40,
        ny: 40,
        first_longitude: -98.0,
        first_latitude: 35.0,
        radius: projection.radius,
        scanning: Scanning {
            j_positive: true,
            ..Scanning::default()
        },
        projection: Projection::Lambert {
            projection,
            dx: 3000.0,
            dy: 3000.0,
        },
    };
    let bounds = Bounds::new(-97.8, 35.2, -97.6, 35.4).unwrap();
    let cells = grid.select(bounds, Limits::default(), &|| false).unwrap();
    assert!(cells.len() > 30);
    let area: f64 = cells.iter().map(|cell| cell.overlap_area).sum();
    assert!((area / bounds.area(grid.radius) - 1.0).abs() < 1.0e-7);
    let smallest = cells
        .iter()
        .map(|cell| cell.overlap_area)
        .reduce(f64::min)
        .unwrap();
    let largest = cells
        .iter()
        .map(|cell| cell.overlap_area)
        .reduce(f64::max)
        .unwrap();
    assert!(largest > smallest * 2.0);
}

#[test]
fn invalid_outside_overlarge_and_cancelled_selections_are_explicit_failures() {
    let grid = regular();
    assert!(matches!(
        grid.select(
            Bounds::new(-90.0, 30.0, -89.0, 31.0).unwrap(),
            Limits::default(),
            &|| false
        ),
        Err(Error::Data(_))
    ));
    let bounds = Bounds::new(0.25, 0.25, 1.25, 1.25).unwrap();
    assert_eq!(
        grid.select(bounds, Limits::default(), &|| true)
            .unwrap_err(),
        Error::Cancelled
    );
    assert!(matches!(
        grid.select(
            bounds,
            Limits {
                max_columns: 2,
                ..Limits::default()
            },
            &|| false
        ),
        Err(Error::Invalid(_))
    ));
}

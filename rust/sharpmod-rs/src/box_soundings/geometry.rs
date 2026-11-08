//! Geographic boxes, clipping and physical area on the model's spherical Earth.

use super::Error;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bounds {
    pub west: f64,
    pub south: f64,
    pub east: f64,
    pub north: f64,
}

pub type Coordinate = (f64, f64); // longitude, latitude in degrees

impl Bounds {
    pub fn new(west: f64, south: f64, east: f64, north: f64) -> Result<Self, Error> {
        if ![west, south, east, north]
            .iter()
            .all(|value| value.is_finite())
            || !(-180.0..=180.0).contains(&west)
            || !(-180.0..=180.0).contains(&east)
            || south < -90.0
            || north > 90.0
            || south >= north
            || west == east
        {
            return Err(Error::Invalid(
                "Choose a nonempty geographic box within valid latitude and longitude bounds"
                    .into(),
            ));
        }
        let bounds = Self {
            west,
            south,
            east,
            north,
        };
        if bounds.width() <= 0.0 || bounds.width() > 180.0 {
            return Err(Error::Invalid(
                "Draw a smaller box spanning at most 180 degrees of longitude".into(),
            ));
        }
        Ok(bounds)
    }

    pub fn width(self) -> f64 {
        if self.east > self.west {
            self.east - self.west
        } else {
            360.0 + self.east - self.west
        }
    }

    pub fn center(self) -> Coordinate {
        (
            normalize_longitude(self.west + self.width() / 2.0),
            (self.south + self.north) / 2.0,
        )
    }

    /// Unwrap around the box centre, keeping dateline-crossing cells contiguous.
    pub fn unwrap(self, longitude: f64) -> f64 {
        let center = self.west + self.width() / 2.0;
        center + (longitude - center + 180.0).rem_euclid(360.0) - 180.0
    }

    pub fn area(self, radius: f64) -> f64 {
        radius
            * radius
            * self.width().to_radians()
            * (self.north.to_radians().sin() - self.south.to_radians().sin())
    }

    /// Clip the native cell boundary to geographic meridians and parallels.
    pub fn overlap(self, polygon: &[Coordinate], radius: f64) -> f64 {
        let mut clipped: Vec<_> = polygon
            .iter()
            .map(|&(lon, lat)| (self.unwrap(lon), lat))
            .collect();
        for (axis, edge, above) in [
            (0, self.west, true),
            (0, self.west + self.width(), false),
            (1, self.south, true),
            (1, self.north, false),
        ] {
            if clipped.is_empty() {
                return 0.0;
            }
            let input = std::mem::take(&mut clipped);
            let mut previous = *input.last().expect("nonempty polygon");
            for current in input {
                let a = if axis == 0 { previous.0 } else { previous.1 };
                let b = if axis == 0 { current.0 } else { current.1 };
                let previous_inside = if above { a >= edge } else { a <= edge };
                let current_inside = if above { b >= edge } else { b <= edge };
                if previous_inside != current_inside {
                    let ratio = (edge - a) / (b - a);
                    clipped.push((
                        previous.0 + ratio * (current.0 - previous.0),
                        previous.1 + ratio * (current.1 - previous.1),
                    ));
                }
                if current_inside {
                    clipped.push(current);
                }
                previous = current;
            }
        }
        spherical_area(&clipped, radius)
    }
}

pub fn normalize_longitude(longitude: f64) -> f64 {
    (longitude + 180.0).rem_euclid(360.0) - 180.0
}

/// Integral of sin(latitude) d(longitude) for linear geographic boundary segments.
/// Parallel edges are exact; curved projected edges are subdivided by their adapter.
pub fn spherical_area(polygon: &[Coordinate], radius: f64) -> f64 {
    if polygon.len() < 3 {
        return 0.0;
    }
    let mut integral = 0.0;
    let mut previous = polygon[polygon.len() - 1];
    for &current in polygon {
        let (lon_a, lat_a) = (previous.0.to_radians(), previous.1.to_radians());
        let (lon_b, lat_b) = (current.0.to_radians(), current.1.to_radians());
        let mean_sine = if (lat_b - lat_a).abs() < 1.0e-10 {
            ((lat_a + lat_b) / 2.0).sin()
        } else {
            // A midpoint identity avoids cancellation near the equator.
            2.0 * ((lat_a + lat_b) / 2.0).sin() * ((lat_b - lat_a) / 2.0).sin() / (lat_b - lat_a)
        };
        integral += (lon_b - lon_a) * mean_sine;
        previous = current;
    }
    (integral * radius * radius).abs()
}

#[cfg(test)]
mod tests {
    use super::*;
    const RADIUS: f64 = 6_371_229.0;

    #[test]
    fn overlap_matches_analytic_partial_latitude_longitude_cells() {
        let cell = [(-1.0, 39.0), (1.0, 39.0), (1.0, 41.0), (-1.0, 41.0)];
        let bounds = Bounds::new(0.0, 40.0, 2.0, 42.0).unwrap();
        let expected = Bounds::new(0.0, 40.0, 1.0, 41.0).unwrap().area(RADIUS);
        assert!((bounds.overlap(&cell, RADIUS) / expected - 1.0).abs() < 1.0e-10);
        let equatorial = Bounds::new(-1.0, 0.0, 1.0, 2.0).unwrap().area(RADIUS);
        let polar = Bounds::new(-1.0, 70.0, 1.0, 72.0).unwrap().area(RADIUS);
        assert!(equatorial > polar * 2.0);
    }

    #[test]
    fn dateline_cells_are_clipped_without_selecting_the_opposite_hemisphere() {
        let bounds = Bounds::new(179.5, 0.0, -179.5, 1.0).unwrap();
        assert_eq!(bounds.width(), 1.0);
        let cell = [(179.0, 0.0), (-179.0, 0.0), (-179.0, 1.0), (179.0, 1.0)];
        assert!((bounds.overlap(&cell, RADIUS) / bounds.area(RADIUS) - 1.0).abs() < 1.0e-10);
        assert_eq!(
            bounds.overlap(&[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], RADIUS),
            0.0
        );
    }

    #[test]
    fn invalid_and_empty_geographic_selections_are_rejected() {
        for values in [
            [0.0, 0.0, 0.0, 1.0],
            [0.0, 1.0, 1.0, 0.0],
            [0.0, 90.0, 1.0, 91.0],
            [f64::NAN, 0.0, 1.0, 1.0],
        ] {
            assert!(Bounds::new(values[0], values[1], values[2], values[3]).is_err());
        }
    }
}

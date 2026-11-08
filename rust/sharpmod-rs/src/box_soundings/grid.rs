//! Native regular latitude/longitude and Lambert cell geometry; no horizontal regrid.

use super::{
    geometry::{normalize_longitude, Bounds, Coordinate},
    Error, Limits,
};

#[derive(Clone, Copy, Debug, Default)]
pub struct Scanning {
    pub i_negative: bool,
    pub j_positive: bool,
    pub j_consecutive: bool,
    pub alternating: bool,
}

#[derive(Clone, Copy, Debug)]
pub struct Lambert {
    pub central_longitude: f64,
    pub radius: f64,
    n: f64,
    factor: f64,
}

impl Lambert {
    pub fn new(
        central_longitude: f64,
        first_parallel: f64,
        second_parallel: f64,
        radius: f64,
    ) -> Result<Self, Error> {
        let a = first_parallel.to_radians();
        let b = second_parallel.to_radians();
        let n = if (a - b).abs() < 1.0e-10 {
            a.sin()
        } else {
            (a.cos() / b.cos()).ln()
                / (((std::f64::consts::FRAC_PI_4 + b / 2.0).tan())
                    / (std::f64::consts::FRAC_PI_4 + a / 2.0).tan())
                .ln()
        };
        let factor = a.cos() * (std::f64::consts::FRAC_PI_4 + a / 2.0).tan().powf(n) / n;
        if ![central_longitude, radius, n, factor]
            .iter()
            .all(|value| value.is_finite())
            || n.abs() < 1.0e-8
            || radius <= 0.0
        {
            return Err(Error::Data(
                "Unsupported Lambert projection parameters".into(),
            ));
        }
        Ok(Self {
            central_longitude,
            radius,
            n,
            factor,
        })
    }

    pub fn project(self, longitude: f64, latitude: f64) -> Coordinate {
        let rho = self.radius * self.factor
            / (std::f64::consts::FRAC_PI_4 + latitude.to_radians() / 2.0)
                .tan()
                .powf(self.n);
        let angle = self.n * normalize_longitude(longitude - self.central_longitude).to_radians();
        (rho * angle.sin(), -rho * angle.cos())
    }

    pub fn unproject(self, x: f64, y: f64) -> Coordinate {
        let sign = self.n.signum();
        let rho = sign * x.hypot(y);
        let angle = (sign * x).atan2(-sign * y);
        let latitude = 2.0 * (self.radius * self.factor / rho).powf(1.0 / self.n).atan()
            - std::f64::consts::FRAC_PI_2;
        (
            normalize_longitude(self.central_longitude + (angle / self.n).to_degrees()),
            latitude.to_degrees(),
        )
    }
}

#[derive(Clone, Copy, Debug)]
pub enum Projection {
    RegularLatLon {
        longitude_increment: f64,
        latitude_increment: f64,
    },
    Lambert {
        projection: Lambert,
        dx: f64,
        dy: f64,
    },
}

#[derive(Clone, Copy, Debug)]
pub struct Grid {
    pub nx: usize,
    pub ny: usize,
    pub first_longitude: f64,
    pub first_latitude: f64,
    pub radius: f64,
    pub scanning: Scanning,
    pub projection: Projection,
}

#[derive(Clone, Copy, Debug)]
pub struct Cell {
    pub index: usize,
    pub longitude: f64,
    pub latitude: f64,
    pub overlap_area: f64,
}

impl Grid {
    pub fn validate(self) -> Result<Self, Error> {
        let (dx, dy) = match self.projection {
            Projection::RegularLatLon {
                longitude_increment,
                latitude_increment,
            } => (longitude_increment, latitude_increment),
            Projection::Lambert { dx, dy, .. } => (dx, dy),
        };
        if self.nx == 0
            || self.ny == 0
            || self
                .nx
                .checked_mul(self.ny)
                .is_none_or(|count| count > i32::MAX as usize)
            || ![
                self.first_longitude,
                self.first_latitude,
                self.radius,
                dx,
                dy,
            ]
            .iter()
            .all(|value| value.is_finite())
            || dx <= 0.0
            || dy <= 0.0
            || self.radius <= 0.0
            || self.first_latitude.abs() > 90.0
        {
            return Err(Error::Data(
                "The model has invalid native grid geometry".into(),
            ));
        }
        Ok(self)
    }

    pub fn native_index(self, x: usize, y: usize) -> usize {
        if self.scanning.j_consecutive {
            x * self.ny
                + if self.scanning.alternating && x % 2 == 1 {
                    self.ny - 1 - y
                } else {
                    y
                }
        } else {
            y * self.nx
                + if self.scanning.alternating && y % 2 == 1 {
                    self.nx - 1 - x
                } else {
                    x
                }
        }
    }

    pub fn select(
        self,
        bounds: Bounds,
        limits: Limits,
        cancelled: &dyn Fn() -> bool,
    ) -> Result<Vec<Cell>, Error> {
        let grid = self.validate()?;
        let limits = limits.validate()?;
        let requested_area = bounds.area(grid.radius);
        if requested_area > limits.max_area_km2 * 1_000_000.0 {
            return Err(Error::Invalid(format!(
                "The box exceeds the {:.0} square-kilometre limit",
                limits.max_area_km2
            )));
        }
        let (origin, dx, dy, cyclic) = match grid.projection {
            Projection::RegularLatLon {
                longitude_increment,
                latitude_increment,
            } => (
                (bounds.unwrap(grid.first_longitude), grid.first_latitude),
                longitude_increment,
                latitude_increment,
                (longitude_increment * grid.nx as f64 - 360.0).abs() < longitude_increment * 0.01,
            ),
            Projection::Lambert { projection, dx, dy } => (
                projection.project(grid.first_longitude, grid.first_latitude),
                dx,
                dy,
                false,
            ),
        };
        let dx = if grid.scanning.i_negative { -dx } else { dx };
        let dy = if grid.scanning.j_positive { dy } else { -dy };
        let mut extent = [
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
        ];
        // Sample the geographic boundary, including projection curvature, then
        // pad by two native cells. Cell overlap itself uses the native boundary.
        for step in 0..=80 {
            let f = step as f64 / 80.0;
            let longitude = bounds.west + bounds.width() * f;
            let latitude = bounds.south + (bounds.north - bounds.south) * f;
            for (lon, lat) in [
                (longitude, bounds.south),
                (longitude, bounds.north),
                (bounds.west, latitude),
                (bounds.west + bounds.width(), latitude),
            ] {
                let (x, y) = match grid.projection {
                    Projection::RegularLatLon { .. } => (lon, lat),
                    Projection::Lambert { projection, .. } => projection.project(lon, lat),
                };
                let (x, y) = ((x - origin.0) / dx, (y - origin.1) / dy);
                if !x.is_finite() || !y.is_finite() {
                    return Err(Error::Invalid(
                        "The box lies outside this model projection".into(),
                    ));
                }
                extent[0] = extent[0].min(x);
                extent[1] = extent[1].max(x);
                extent[2] = extent[2].min(y);
                extent[3] = extent[3].max(y);
            }
        }
        let x_start = (extent[0].floor() - 2.0).max(if cyclic { f64::NEG_INFINITY } else { 0.0 });
        let x_end = (extent[1].ceil() + 2.0).min(if cyclic {
            f64::INFINITY
        } else {
            grid.nx as f64 - 1.0
        });
        let y_start = (extent[2].floor() - 2.0).max(0.0);
        let y_end = (extent[3].ceil() + 2.0).min(grid.ny as f64 - 1.0);
        if x_start > x_end || y_start > y_end {
            return Err(Error::Data(
                "The box is outside the native model grid".into(),
            ));
        }
        let (x_start, x_end, y_start, y_end) =
            (x_start as i64, x_end as i64, y_start as i64, y_end as i64);
        let candidates = (x_end - x_start + 1).max(0) as u64 * (y_end - y_start + 1).max(0) as u64;
        if candidates > limits.max_columns as u64 * 64 {
            return Err(Error::Invalid(
                "This selection requires too much native-grid work; draw a smaller box".into(),
            ));
        }
        let mut cells = Vec::new();
        for y in y_start..=y_end {
            if cancelled() {
                return Err(Error::Cancelled);
            }
            for x in x_start..=x_end {
                let (cx, cy) = (origin.0 + x as f64 * dx, origin.1 + y as f64 * dy);
                let (longitude, latitude) = match grid.projection {
                    Projection::RegularLatLon { .. } => (normalize_longitude(cx), cy),
                    Projection::Lambert { projection, .. } => projection.unproject(cx, cy),
                };
                let polygon = grid.cell_boundary(cx, cy, dx.abs(), dy.abs());
                let area = bounds.overlap(&polygon, grid.radius);
                if area <= requested_area * 1.0e-12 {
                    continue;
                }
                let ix = if cyclic {
                    x.rem_euclid(grid.nx as i64) as usize
                } else {
                    x as usize
                };
                cells.push(Cell {
                    index: grid.native_index(ix, y as usize),
                    longitude,
                    latitude,
                    overlap_area: area,
                });
                if cells.len() > limits.max_columns {
                    return Err(Error::Invalid(format!(
                        "The box contains more than {} native columns; draw a smaller box",
                        limits.max_columns
                    )));
                }
            }
        }
        if cells.is_empty() {
            return Err(Error::Data(
                "The box does not overlap any native model cells".into(),
            ));
        }
        Ok(cells)
    }

    fn cell_boundary(self, x: f64, y: f64, dx: f64, dy: f64) -> Vec<Coordinate> {
        let corners = [
            (x - dx / 2.0, y - dy / 2.0),
            (x + dx / 2.0, y - dy / 2.0),
            (x + dx / 2.0, y + dy / 2.0),
            (x - dx / 2.0, y + dy / 2.0),
        ];
        match self.projection {
            Projection::RegularLatLon { .. } => corners
                .into_iter()
                .map(|(lon, lat)| (lon, lat.clamp(-90.0, 90.0)))
                .collect(),
            Projection::Lambert { projection, .. } => {
                let mut boundary = Vec::with_capacity(32);
                for side in 0..4 {
                    let a = corners[side];
                    let b = corners[(side + 1) % 4];
                    for part in 0..8 {
                        let f = part as f64 / 8.0;
                        boundary.push(
                            projection.unproject(a.0 + f * (b.0 - a.0), a.1 + f * (b.1 - a.1)),
                        );
                    }
                }
                boundary
            }
        }
    }
}

#[cfg(test)]
mod tests;

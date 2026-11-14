//! Spherical Lambert geometry shared by model domains and point selection.

use crate::box_soundings::grid::Lambert;
use std::f64::consts::{FRAC_PI_4, PI};

pub(crate) struct Specification {
    pub lat_origin: f64,
    pub lon_origin: f64,
    pub lat_1: f64,
    pub lat_2: f64,
    pub radius: f64,
    pub shape: (usize, usize),
    pub spacing: f64,
    pub x0: f64,
    pub y0: f64,
}

pub(crate) struct Grid {
    spec: Specification,
    projection: Lambert,
    n: f64,
    f: f64,
    rho0: f64,
}

pub(crate) fn normalize_longitude(lon: f64) -> f64 {
    (lon + 180.0).rem_euclid(360.0) - 180.0
}

impl Grid {
    pub fn new(spec: Specification) -> Result<Self, &'static str> {
        if [
            spec.lat_origin,
            spec.lon_origin,
            spec.lat_1,
            spec.lat_2,
            spec.radius,
            spec.spacing,
            spec.x0,
            spec.y0,
        ]
        .iter()
        .any(|v| !v.is_finite())
            || spec.radius <= 0.0
            || spec.spacing <= 0.0
            || spec.shape.0 == 0
            || spec.shape.1 == 0
            || spec.lat_origin.abs() >= 90.0
            || spec.lat_1.abs() >= 90.0
            || spec.lat_2.abs() >= 90.0
        {
            return Err("invalid Lambert grid specification");
        }
        let projection = Lambert::new(spec.lon_origin, spec.lat_1, spec.lat_2, spec.radius)
            .map_err(|_| "invalid Lambert cone")?;
        let (n, f) = projection.constants();
        let rho0 = spec.radius * f
            / (FRAC_PI_4 + spec.lat_origin.to_radians() / 2.0)
                .tan()
                .powf(n);
        Ok(Self {
            spec,
            projection,
            n,
            f,
            rho0,
        })
    }

    pub fn constants(&self) -> (f64, f64, f64) {
        (self.n, self.f, self.rho0)
    }

    pub fn extent(&self) -> (f64, f64) {
        (
            self.spec.x0 + (self.spec.shape.1 - 1) as f64 * self.spec.spacing,
            self.spec.y0 + (self.spec.shape.0 - 1) as f64 * self.spec.spacing,
        )
    }

    pub fn forward(&self, lon: f64, lat: f64) -> (f64, f64) {
        // Retain the compatibility transform's clamp, including nonfinite latitudes.
        let lat = if lat < 89.9 {
            if lat > -89.9 {
                lat
            } else {
                -89.9
            }
        } else {
            89.9
        };
        let (x, y) = self.projection.project(lon, lat);
        (x, self.rho0 + y)
    }

    pub fn inverse(&self, x: f64, y: f64) -> (f64, f64) {
        let dy = self.rho0 - y;
        let rho = x.hypot(dy).copysign(self.n);
        if rho == 0.0 {
            return (
                normalize_longitude(self.spec.lon_origin),
                if self.n > 0.0 { 90.0 } else { -90.0 },
            );
        }
        self.projection.unproject(x, y - self.rho0)
    }

    pub fn contains(&self, lat: f64, lon: f64) -> bool {
        if !lat.is_finite()
            || !lon.is_finite()
            || !(-90.0..=90.0).contains(&lat)
            || (normalize_longitude(lon - self.spec.lon_origin).to_radians() * self.n).abs() >= PI
        {
            return false;
        }
        let (x, y) = self.forward(lon, lat);
        let (x1, y1) = self.extent();
        self.spec.x0 <= x && x <= x1 && self.spec.y0 <= y && y <= y1
    }

    pub fn outline(&self, count: usize) -> Vec<(f64, f64)> {
        let count = count.max(2);
        let (x0, y0) = (self.spec.x0, self.spec.y0);
        let (x1, y1) = self.extent();
        let mut points = Vec::with_capacity(4 * count + 1);
        for edge in 0..4 {
            for index in 0..count {
                let along = |start, stop| start + (stop - start) * index as f64 / count as f64;
                let (x, y) = match edge {
                    0 => (along(x0, x1), y0),
                    1 => (x1, along(y0, y1)),
                    2 => (along(x1, x0), y1),
                    _ => (x0, along(y1, y0)),
                };
                points.push(self.inverse(x, y));
            }
        }
        points.push(points[0]);
        points
    }

    pub fn bounds(&self, count: usize) -> (f64, f64, f64, f64) {
        self.outline(count).into_iter().fold(
            (
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
            ),
            |(west, east, south, north), (lon, lat)| {
                (west.min(lon), east.max(lon), south.min(lat), north.max(lat))
            },
        )
    }
}

#[cfg(test)]
mod tests;

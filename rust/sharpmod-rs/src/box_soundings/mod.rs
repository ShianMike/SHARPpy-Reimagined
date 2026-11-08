//! Geographic selection and conservative aggregation of native atmospheric columns.

pub mod geometry;
pub mod grid;
pub mod profile;

use std::fmt;

/// Changes to the surface convention, weighting or interpolation invalidate results.
pub const METHOD_VERSION: &str = "native-area-log-pressure-highest-ground-v1";
pub(crate) const MAX_DISPLAY_COLUMNS: usize = 64;

#[derive(Clone, Debug, PartialEq)]
pub enum Error {
    Invalid(String),
    Data(String),
    Cancelled,
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(message) | Self::Data(message) => formatter.write_str(message),
            Self::Cancelled => formatter.write_str("Box sounding cancelled"),
        }
    }
}

impl std::error::Error for Error {}

#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub max_columns: usize,
    pub max_area_km2: f64,
    pub minimum_coverage: f64,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_columns: 4096,
            max_area_km2: 1_000_000.0,
            minimum_coverage: 0.8,
        }
    }
}

impl Limits {
    pub fn validate(self) -> Result<Self, Error> {
        if !(1..=65_536).contains(&self.max_columns)
            || !self.max_area_km2.is_finite()
            || self.max_area_km2 <= 0.0
            || !self.minimum_coverage.is_finite()
            || !(0.5..=1.0).contains(&self.minimum_coverage)
        {
            return Err(Error::Invalid(
                "Invalid box sounding workload or coverage limits".into(),
            ));
        }
        Ok(self)
    }
}

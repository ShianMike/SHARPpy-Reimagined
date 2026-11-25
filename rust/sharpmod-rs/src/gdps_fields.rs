//! Published MSC Datamart addresses for Canadian forecast maps.

use sharpmod_weather::model_http::Error;
use std::collections::HashSet;
use time::{OffsetDateTime, UtcOffset};

pub const WIDTH: usize = 2400;
pub const HEIGHT: usize = 1201;
pub const SPACING: f64 = 0.15;
pub const MAX_FIELD_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_INVENTORY_BYTES: usize = 512 * 1024;
pub const PRODUCTS: &[&str] = &[
    "hgt-wind-200",
    "hgt-wind-300",
    "hgt-wind-500",
    "hgt-wind-700",
    "hgt-wind-850",
    "tmp-2m",
    "dpt-2m",
    "thetae-2m",
    "rh-2m",
    "gust",
    "wind-10m",
    "apcp",
    "prate",
    "prate-frzr",
    "prate-sleet",
    "prate-snow",
    "snod",
    "cape",
    "lapse-700-500",
    "mslp",
];

pub const HRDPS_PRODUCTS: &[&str] = &[
    "hgt-wind-500",
    "hgt-wind-700",
    "hgt-wind-850",
    "tmp-2m",
    "dpt-2m",
    "thetae-2m",
    "rh-2m",
    "gust",
    "prate-ptype",
    "qpf-6h",
    "qpf-12h",
    "qpf-24h",
    "apcp",
    "snow-10-1",
    "lapse-700-500",
    "sbcape",
    "lcl",
    "shear-sfc-500",
    "srh-0-3km",
    "ehi-0-3km",
];

#[derive(Clone, Copy)]
pub enum Level {
    Surface,
    AboveGround(u16),
    Isobaric(u16),
    MeanSea,
}

impl Level {
    pub fn metadata(self) -> (&'static str, u16) {
        match self {
            Self::Surface => ("surface", 0),
            Self::AboveGround(height) => ("heightAboveGround", height),
            Self::Isobaric(pressure) => ("isobaricInhPa", pressure),
            Self::MeanSea => ("meanSea", 0),
        }
    }
}

#[derive(Clone, Copy)]
pub enum Period {
    Instant,
    RunTotal,
    ThreeHours,
    OneHour,
    SixHours,
    TwelveHours,
    TwentyFourHours,
}

#[derive(Clone, Copy)]
pub struct Record {
    variable: &'static str,
    pub category: u8,
    pub number: u8,
    pub level: Level,
    pub period: Period,
}

impl Record {
    fn stem_for(self, model: Model) -> String {
        let level = match self.level {
            Level::Surface => "Sfc".into(),
            Level::AboveGround(height) => format!("AGL-{height}m"),
            Level::Isobaric(pressure) => {
                if model == Model::Hrdps {
                    format!("ISBL_{pressure:04}")
                } else {
                    format!("IsbL-{pressure:04}")
                }
            }
            Level::MeanSea => "MSL".into(),
        };
        format!("{}_{level}", self.variable)
    }
}

pub fn record(name: &str) -> Result<Record, Error> {
    use Level::{AboveGround as Agl, Isobaric, MeanSea, Surface};
    use Period::{Instant, RunTotal, ThreeHours};
    let (variable, category, number, level, period) = match name {
        "tmp2m" => ("AirTemp", 0, 0, Agl(2), Instant),
        "dpt2m" => ("DewPoint", 0, 6, Agl(2), Instant),
        "rh2m" => ("RelativeHumidity", 1, 1, Agl(2), Instant),
        "pres" => ("Pressure", 3, 0, Surface, Instant),
        "u10" => ("WindU", 2, 2, Agl(10), Instant),
        "v10" => ("WindV", 2, 3, Agl(10), Instant),
        "gust" => ("WindGust", 2, 22, Agl(10), Instant),
        "apcp" => ("Precip-Accum", 1, 8, Surface, RunTotal),
        "prate" => ("PrecipRate", 1, 7, Surface, Instant),
        "gdps_frzr" => ("FreezingRain-Accum3h", 1, 67, Surface, ThreeHours),
        "gdps_sleet" => ("IcePellets-Accum3h", 1, 68, Surface, ThreeHours),
        "gdps_snow" => ("Snow-Accum3h", 1, 66, Surface, ThreeHours),
        "snod" => ("SnowDepth", 1, 11, Surface, Instant),
        "sbcape" => ("CAPE", 7, 6, Surface, Instant),
        "sbcin" => ("CIN", 7, 7, Surface, Instant),
        "mslp" => ("Pressure", 3, 1, MeanSea, Instant),
        _ => {
            let (prefix, value) = name.split_at(
                name.find(|ch: char| ch.is_ascii_digit())
                    .unwrap_or(name.len()),
            );
            let pressure = value
                .parse()
                .ok()
                .filter(|level| [200, 300, 500, 700, 850].contains(level));
            match (prefix, pressure) {
                ("tmp", Some(pressure)) => ("AirTemp", 0, 0, Isobaric(pressure), Instant),
                ("hgt", Some(pressure)) => {
                    ("GeopotentialHeight", 3, 5, Isobaric(pressure), Instant)
                }
                ("ugrd", Some(pressure)) => ("WindU", 2, 2, Isobaric(pressure), Instant),
                ("vgrd", Some(pressure)) => ("WindV", 2, 3, Isobaric(pressure), Instant),
                _ => return Err(Error::Invalid(format!("No GDPS record for {name}"))),
            }
        }
    };
    Ok(Record {
        variable,
        category,
        number,
        level,
        period,
    })
}

pub fn rdps_record(name: &str) -> Result<Record, Error> {
    let mut result = record(name)?;
    match name {
        "gdps_frzr" => result.variable = "FreezingRain-Accum1h",
        "gdps_sleet" => result.variable = "IcePellets-Accum1h",
        "gdps_snow" => result.variable = "Snow-Accum1h",
        _ => return Ok(result),
    }
    result.period = Period::OneHour;
    Ok(result)
}

pub fn hrdps_record(name: &str) -> Result<Record, Error> {
    use Level::{AboveGround as Agl, Isobaric, Surface};
    use Period::{Instant, RunTotal, SixHours, TwelveHours, TwentyFourHours};
    let (variable, category, number, level, period) = match name {
        "tmp2m" => ("TMP", 0, 0, Agl(2), Instant),
        "dpt2m" => ("DPT", 0, 6, Agl(2), Instant),
        "rh2m" => ("RH", 1, 1, Agl(2), Instant),
        "pres" => ("PRES", 3, 0, Surface, Instant),
        "u10" => ("UGRD", 2, 2, Agl(10), Instant),
        "v10" => ("VGRD", 2, 3, Agl(10), Instant),
        "gust" => ("GUST", 2, 22, Agl(10), Instant),
        "prate" => ("PRATE", 1, 7, Surface, Instant),
        "ptype" => ("PTYPE", 1, 19, Surface, Instant),
        "apcp" => ("APCP", 1, 8, Surface, RunTotal),
        "qpf6h" => ("APCP-Accum6h", 1, 8, Surface, SixHours),
        "qpf12h" => ("APCP-Accum12h", 1, 8, Surface, TwelveHours),
        "qpf24h" => ("APCP-Accum24h", 1, 8, Surface, TwentyFourHours),
        "weasn" => ("WEASN", 1, 66, Surface, RunTotal),
        "sbcape" => ("CAPE", 7, 6, Surface, Instant),
        "srh03" => ("HLCY", 7, 8, Surface, Instant),
        _ => {
            let (prefix, value) = name.split_at(
                name.find(|ch: char| ch.is_ascii_digit())
                    .unwrap_or(name.len()),
            );
            let pressure = value
                .parse()
                .ok()
                .filter(|level| [500, 700, 850].contains(level));
            match (prefix, pressure) {
                ("tmp", Some(pressure)) => ("TMP", 0, 0, Isobaric(pressure), Instant),
                ("hgt", Some(pressure)) => ("HGT", 3, 5, Isobaric(pressure), Instant),
                ("ugrd", Some(pressure)) => ("UGRD", 2, 2, Isobaric(pressure), Instant),
                ("vgrd", Some(pressure)) => ("VGRD", 2, 3, Isobaric(pressure), Instant),
                _ => return Err(Error::Invalid(format!("No HRDPS record for {name}"))),
            }
        }
    };
    Ok(Record {
        variable,
        category,
        number,
        level,
        period,
    })
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Model {
    Gdps,
    Rdps,
    Hrdps,
}

pub struct Frame {
    pub model: Model,
    pub date: i32,
    pub cycle: u8,
    pub hour: u16,
}

impl Frame {
    pub fn new(run: OffsetDateTime, hour: u16) -> Result<Self, Error> {
        Self::for_model(Model::Gdps, run, hour)
    }

    pub fn for_model(model: Model, run: OffsetDateTime, hour: u16) -> Result<Self, Error> {
        let run = run.to_offset(UtcOffset::UTC);
        if !(1..=9999).contains(&run.year())
            || !matches!(run.hour(), 0 | 6 | 12 | 18)
            || run.minute() != 0
            || run.second() != 0
            || run.nanosecond() != 0
            || (model == Model::Gdps
                && (!matches!(run.hour(), 0 | 12) || hour > 240 || !hour.is_multiple_of(3)))
            || (model == Model::Rdps && hour > 84)
            || (model == Model::Hrdps && hour > 48)
        {
            return Err(Error::Invalid(
                "GDPS uses 00/12Z and three-hour steps through F240; RDPS uses six-hourly runs through F084; HRDPS uses six-hourly runs through F048".into(),
            ));
        }
        Ok(Self {
            model,
            date: run.year() * 10000
                + i32::from(u8::from(run.month())) * 100
                + i32::from(run.day()),
            cycle: run.hour(),
            hour,
        })
    }

    pub fn directory(&self) -> String {
        match self.model {
            Model::Gdps => format!(
                "https://dd.weather.gc.ca/{:08}/WXO-DD/model_gdps/15km/{:02}/{:03}/",
                self.date, self.cycle, self.hour
            ),
            Model::Rdps => format!(
                "https://dd.weather.gc.ca/{:08}/WXO-DD/model_rdps/10km/{:02}/{:03}/",
                self.date, self.cycle, self.hour
            ),
            Model::Hrdps => format!(
                "https://dd.weather.gc.ca/{:08}/WXO-DD/model_hrdps/continental/2.5km/{:02}/{:03}/",
                self.date, self.cycle, self.hour
            ),
        }
    }

    fn prefix(&self) -> String {
        let model = match self.model {
            Model::Gdps => "GDPS",
            Model::Rdps => "RDPS",
            Model::Hrdps => "HRDPS",
        };
        format!("{:08}T{:02}Z_MSC_{model}_", self.date, self.cycle)
    }

    fn suffix(&self) -> String {
        let grid = match self.model {
            Model::Gdps => "LatLon0.15",
            Model::Rdps => "RLatLon0.09",
            Model::Hrdps => "RLatLon0.0225",
        };
        format!("_{grid}_PT{:03}H.grib2", self.hour)
    }

    pub fn file_url(&self, record: Record) -> String {
        format!(
            "{}{}{}{}",
            self.directory(),
            self.prefix(),
            record.stem_for(self.model),
            self.suffix()
        )
    }

    pub fn inventory(&self, listing: &str) -> HashSet<String> {
        let prefix = self.prefix();
        let suffix = self.suffix();
        listing
            .split("href=\"")
            .skip(1)
            .filter_map(|link| {
                link.split_once('"')?
                    .0
                    .strip_prefix(&prefix)?
                    .strip_suffix(&suffix)
            })
            .map(str::to_owned)
            .collect()
    }

    pub fn has_records(&self, inventory: &HashSet<String>, records: &[Record]) -> bool {
        records
            .iter()
            .all(|record| inventory.contains(&record.stem_for(self.model)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    #[test]
    fn inventory_is_specific_to_the_run_and_includes_all_ingredients() {
        let run = OffsetDateTime::parse("2026-11-08T00:00:00Z", &Rfc3339).unwrap();
        let frame = Frame::new(run, 24).unwrap();
        let temperature = record("tmp2m").unwrap();
        let u = record("ugrd500").unwrap();
        let v = record("vgrd500").unwrap();
        let pressure = record("mslp").unwrap();
        assert!(frame.file_url(pressure).ends_with(
            "/20261108/WXO-DD/model_gdps/15km/00/024/20261108T00Z_MSC_GDPS_Pressure_MSL_LatLon0.15_PT024H.grib2"
        ));
        let listing = concat!(
            "<a href=\"20261108T00Z_MSC_GDPS_AirTemp_AGL-2m_LatLon0.15_PT024H.grib2\">",
            "<a href=\"20261108T00Z_MSC_GDPS_WindU_IsbL-0500_LatLon0.15_PT024H.grib2\">",
            "<a href=\"20261107T00Z_MSC_GDPS_WindV_IsbL-0500_LatLon0.15_PT024H.grib2\">",
            "<a href=\"20261108T00Z_MSC_GDPS_WindV_IsbL-0500_LatLon0.15_PT027H.grib2\">",
        );
        let inventory = frame.inventory(listing);
        assert!(frame.has_records(&inventory, &[temperature]));
        assert!(!frame.has_records(&inventory, &[u, v]));
        assert!(Frame::new(run, 240).is_ok());
        assert!(Frame::new(run, 241).is_err());
        assert!(Frame::new(run, 1).is_err());
        assert!(Frame::new(run + time::Duration::hours(6), 24).is_err());
    }

    #[test]
    fn rdps_uses_hourly_regional_files_and_one_hour_winter_accumulations() {
        let run = OffsetDateTime::parse("2026-11-14T06:00:00Z", &Rfc3339).unwrap();
        let frame = Frame::for_model(Model::Rdps, run, 1).unwrap();
        let record = rdps_record("gdps_frzr").unwrap();
        assert!(frame.file_url(record).ends_with("/model_rdps/10km/06/001/20261114T06Z_MSC_RDPS_FreezingRain-Accum1h_Sfc_RLatLon0.09_PT001H.grib2"));
        assert!(matches!(record.period, Period::OneHour));
        assert!(Frame::for_model(Model::Rdps, run, 84).is_ok());
        assert!(Frame::for_model(Model::Rdps, run, 85).is_err());
        assert!(Frame::for_model(Model::Rdps, run + time::Duration::hours(1), 1).is_err());
    }

    #[test]
    fn hrdps_uses_continental_2p5km_paths_isbl_levels_and_rolling_qpf() {
        let run = OffsetDateTime::parse("2026-11-23T06:00:00Z", &Rfc3339).unwrap();
        let frame = Frame::for_model(Model::Hrdps, run, 24).unwrap();
        assert!(frame.file_url(hrdps_record("hgt500").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_HGT_ISBL_0500_RLatLon0.0225_PT024H.grib2"
        ));
        assert!(frame.file_url(hrdps_record("qpf6h").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_APCP-Accum6h_Sfc_RLatLon0.0225_PT024H.grib2"
        ));
        assert!(frame.file_url(hrdps_record("qpf12h").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_APCP-Accum12h_Sfc_RLatLon0.0225_PT024H.grib2"
        ));
        assert!(frame.file_url(hrdps_record("qpf24h").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_APCP-Accum24h_Sfc_RLatLon0.0225_PT024H.grib2"
        ));
        assert!(frame.file_url(hrdps_record("ptype").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_PTYPE_Sfc_RLatLon0.0225_PT024H.grib2"
        ));
        assert!(frame.file_url(hrdps_record("weasn").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_WEASN_Sfc_RLatLon0.0225_PT024H.grib2"
        ));
        assert!(frame.file_url(hrdps_record("srh03").unwrap()).ends_with(
            "/model_hrdps/continental/2.5km/06/024/20261123T06Z_MSC_HRDPS_HLCY_Sfc_RLatLon0.0225_PT024H.grib2"
        ));
        assert_eq!(HRDPS_PRODUCTS.len(), 20);
        assert!(Frame::for_model(Model::Hrdps, run, 0).is_ok());
        assert!(Frame::for_model(Model::Hrdps, run, 48).is_ok());
        assert!(Frame::for_model(Model::Hrdps, run, 49).is_err());
        assert!(Frame::for_model(Model::Hrdps, run + time::Duration::hours(3), 12).is_err());
    }
}

//! Native DWD ICON Global file addresses, publication intervals and inventory.

use futures_util::stream::{self, StreamExt, TryStreamExt};
use sharpmod_weather::model_http::{Error, ModelClient};
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
    time::{Duration, Instant},
};
use time::{OffsetDateTime, UtcOffset};
use tokio::sync::Mutex;

const LAST_HOURLY_HOUR: u16 = 78;
const GUST_WINDOW: u16 = 6;
const LISTING_MAX_BYTES: usize = 64 * 1024;
const INVENTORY_TTL: Duration = Duration::from_secs(60);
const BASE_URL: &str = "https://opendata.dwd.de/weather/nwp/v1/m/icon/p";
const SINGLE_LEVEL: &[&str] = &[
    "CLAT",
    "CLON",
    "T_2M",
    "TD_2M",
    "RELHUM_2M",
    "PS",
    "PMSL",
    "WW",
    "TOT_PREC",
    "TQV",
    "CAPE_ML",
    "U_10M",
    "V_10M",
    "VMAX_10M",
];
const PRESSURES: [u16; 4] = [500, 700, 850, 925];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Level {
    Surface,
    AboveGround(u16),
    Isobaric(u16),
    MeanSea,
    MixedLayer,
}

impl Level {
    fn metadata(self) -> (&'static str, u16) {
        match self {
            Self::Surface => ("surface", 0),
            Self::AboveGround(height) => ("heightAboveGround", height),
            Self::Isobaric(pressure) => ("isobaricInhPa", pressure),
            Self::MeanSea => ("meanSea", 0),
            Self::MixedLayer => ("unknown", 0),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Statistic {
    Instant,
    RunTotal,
    HourlyMax,
    Invariant,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Record {
    variable: &'static str,
    parameter: (u8, u8),
    level: Level,
    statistic: Statistic,
}

const PRECIPITATION: Record = Record {
    variable: "TOT_PREC",
    parameter: (1, 52),
    level: Level::Surface,
    statistic: Statistic::RunTotal,
};
const GUST: Record = Record {
    variable: "VMAX_10M",
    parameter: (2, 22),
    level: Level::AboveGround(10),
    statistic: Statistic::HourlyMax,
};

fn record(name: &str) -> Result<Record, Error> {
    use Level::{AboveGround as Agl, Isobaric, MeanSea, MixedLayer, Surface};
    let (variable, category, parameter, level) = match name {
        "tmp2m" => ("T_2M", 0, 0, Agl(2)),
        "dpt2m" => ("TD_2M", 0, 6, Agl(2)),
        "rh2m" => ("RELHUM_2M", 1, 1, Agl(2)),
        "pres" => ("PS", 3, 0, Surface),
        "mslp" => ("PMSL", 3, 1, MeanSea),
        "u10" => ("U_10M", 2, 2, Agl(10)),
        "v10" => ("V_10M", 2, 3, Agl(10)),
        "ww" => ("WW", 19, 25, Surface),
        "pwat" => ("TQV", 1, 64, Surface),
        "mlcape" => ("CAPE_ML", 7, 6, MixedLayer),
        "apcp" => return Ok(PRECIPITATION),
        _ => {
            let (prefix, pressure) = name.split_at(
                name.find(|ch: char| ch.is_ascii_digit())
                    .unwrap_or(name.len()),
            );
            let pressure = pressure.parse().ok().filter(|value| {
                // Only geopotential reaches 1000 hPa: the thickness base.
                PRESSURES.contains(value) || (prefix == "hgt" && *value == 1000)
            });
            match (prefix, pressure) {
                ("tmp", Some(p @ (500 | 700))) => ("T", 0, 0, Isobaric(p)),
                ("hgt", Some(p)) => ("FI", 3, 4, Isobaric(p)),
                ("ugrd", Some(p)) => ("U", 2, 2, Isobaric(p)),
                ("vgrd", Some(p)) => ("V", 2, 3, Isobaric(p)),
                _ => return Err(Error::Invalid(format!("No ICON Global record for {name}"))),
            }
        }
    };
    Ok(Record {
        variable,
        parameter: (category, parameter),
        level,
        statistic: Statistic::Instant,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Run {
    pub(crate) date: u32,
    pub(crate) cycle: u8,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Frame {
    pub(crate) run: Run,
    pub(crate) hour: u16,
}

impl Frame {
    pub(crate) fn new(run: OffsetDateTime, hour: u16) -> Result<Self, Error> {
        let run = run.to_offset(UtcOffset::UTC);
        let limit = if matches!(run.hour(), 6 | 18) {
            120
        } else {
            180
        };
        if !(1..=9999).contains(&run.year())
            || !matches!(run.hour(), 0 | 6 | 12 | 18)
            || run.minute() != 0
            || run.second() != 0
            || run.nanosecond() != 0
            || hour > limit
            || (hour > LAST_HOURLY_HOUR && !hour.is_multiple_of(3))
        {
            return Err(Error::Invalid(
                "ICON Global uses 00/06/12/18Z runs and hourly leads through F078, then every three hours through F180 (F120 at 06/18Z)".into(),
            ));
        }
        Ok(Self {
            run: Run {
                date: run.year() as u32 * 10000
                    + u32::from(u8::from(run.month())) * 100
                    + u32::from(run.day()),
                cycle: run.hour(),
            },
            hour,
        })
    }

    pub(crate) fn plan(self, name: &str) -> Result<Plan, Error> {
        let hour = self.hour;
        if name == "gust" {
            if hour < GUST_WINDOW {
                return Err(Error::Unavailable(404));
            }
            return Ok(Plan {
                files: (hour - GUST_WINDOW + 1..=hour)
                    .rev()
                    .map(|hour| File { record: GUST, hour })
                    .collect(),
                reduction: Reduction::Max,
            });
        }
        let (window, scale) = match name {
            "prate" => {
                let step = if hour <= LAST_HOURLY_HOUR { 1 } else { 3 };
                (step, 1.0 / (f32::from(step) * 3600.0))
            }
            // The published 3-h average rate, in millimetres over the window.
            "prate3h" => (3, 1.0),
            "qpf6h" => (6, 1.0),
            "qpf24h" => (24, 1.0),
            "qpf48h" => (48, 1.0),
            "qpf120h" => (120, 1.0),
            _ => {
                return Ok(Plan {
                    files: vec![File {
                        record: record(name)?,
                        hour,
                    }],
                    reduction: Reduction::Direct,
                })
            }
        };
        let start = hour.checked_sub(window).ok_or(Error::Unavailable(404))?;
        Ok(Plan {
            files: [hour, start]
                .map(|hour| File {
                    record: PRECIPITATION,
                    hour,
                })
                .into(),
            reduction: Reduction::Difference(scale),
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct File {
    record: Record,
    pub(crate) hour: u16,
}

impl File {
    fn directory(self) -> Directory {
        let pressure = match self.record.level {
            Level::Isobaric(pressure) => Some(pressure),
            _ => None,
        };
        Directory(self.record.variable, pressure)
    }

    pub(crate) fn url(self, run: Run) -> String {
        format!("{}PT{:03}H00M.grib2", self.directory().url(run), self.hour)
    }

    pub(crate) fn parameter(self) -> (u8, u8) {
        self.record.parameter
    }

    pub(crate) fn level(self) -> (&'static str, u16) {
        self.record.level.metadata()
    }

    pub(crate) fn step(self) -> (&'static str, u16) {
        match self.record.statistic {
            Statistic::Instant | Statistic::Invariant => ("instant", self.hour),
            Statistic::RunTotal => ("accum", 0),
            Statistic::HourlyMax => ("max", self.hour - 1),
        }
    }

    pub(crate) fn scale(self) -> f64 {
        if self.record.variable == "FI" {
            1.0 / 9.80665
        } else {
            1.0
        }
    }
}

pub(crate) fn coordinates() -> [File; 2] {
    [("CLAT", 1), ("CLON", 2)].map(|(variable, parameter)| File {
        record: Record {
            variable,
            parameter: (191, parameter),
            level: Level::Surface,
            statistic: Statistic::Invariant,
        },
        hour: 0,
    })
}

pub(crate) struct Plan {
    pub(crate) files: Vec<File>,
    pub(crate) reduction: Reduction,
}

#[derive(Clone, Copy)]
pub(crate) enum Reduction {
    Direct,
    Difference(f32),
    Max,
}

impl Reduction {
    pub(crate) fn combine(self, first: f32, second: f32) -> f32 {
        if !first.is_finite() || !second.is_finite() {
            return f32::NAN;
        }
        match self {
            Self::Direct => unreachable!("a direct ICON field has one record"),
            // Independent packing of the two totals can introduce tiny negative differences.
            Self::Difference(scale) => (first - second).max(0.0) * scale,
            Self::Max => first.max(second),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct Directory(&'static str, Option<u16>);

impl Directory {
    fn url(self, run: Run) -> String {
        let Self(variable, pressure) = self;
        let date = run.date;
        let (year, month, day) = (date / 10000, date / 100 % 100, date % 100);
        let directory = match pressure {
            Some(pressure) => format!("{variable}/lvt1/100/lv1/{}", u32::from(pressure) * 100),
            None => variable.to_owned(),
        };
        format!(
            "{BASE_URL}/{directory}/r/{year:04}-{month:02}-{day:02}T{:02}%3A00/s/",
            run.cycle
        )
    }
}

#[derive(Default)]
pub(crate) struct Inventory(HashMap<Directory, HashSet<u16>>);

impl Inventory {
    pub(crate) fn contains(&self, file: File) -> bool {
        self.0
            .get(&file.directory())
            .is_some_and(|hours| hours.contains(&file.hour))
    }
}

struct CachedInventory {
    run: Run,
    fetched: Instant,
    files: Arc<Inventory>,
}

pub(crate) async fn inventory(
    run: Run,
    timeout: f64,
    cancel: impl Fn() -> Result<bool, String>,
) -> Result<Arc<Inventory>, Error> {
    // The directory listing carries all hours for a cycle and is shared across playback requests.
    static CACHE: [Mutex<Option<CachedInventory>>; 4] = [const { Mutex::const_new(None) }; 4];
    let mut cached = CACHE[usize::from(run.cycle / 6)].lock().await;
    if cancel().map_err(Error::Invalid)? {
        return Err(Error::Cancelled);
    }
    if let Some(entry) = cached
        .as_ref()
        .filter(|entry| entry.run == run && entry.fetched.elapsed() < INVENTORY_TTL)
    {
        return Ok(Arc::clone(&entry.files));
    }
    let directories = SINGLE_LEVEL
        .iter()
        .map(|&name| Directory(name, None))
        .chain(
            ["FI", "U", "V"]
                .into_iter()
                .flat_map(|name| PRESSURES.map(|pressure| Directory(name, Some(pressure)))),
        )
        // FI also publishes at 1000 hPa for the 1000-500 mb thickness.
        .chain([1000].map(|pressure| Directory("FI", Some(pressure))))
        .chain([500, 700].map(|pressure| Directory("T", Some(pressure))));
    let entries: HashMap<_, _> = stream::iter(directories)
        .map(|directory| {
            let cancel = &cancel;
            async move {
                let url = directory.url(run);
                let hours = match ModelClient::shared()
                    .fetch(&url, timeout, LISTING_MAX_BYTES, None, cancel)
                    .await
                {
                    Ok(payload) => {
                        let listing = std::str::from_utf8(&payload).map_err(|_| {
                            Error::Invalid("Invalid ICON Global file listing".into())
                        })?;
                        listing_hours(listing)
                    }
                    Err(Error::Unavailable(404)) => HashSet::new(),
                    Err(error) => return Err(error),
                };
                Ok((directory, hours))
            }
        })
        .buffer_unordered(6)
        .try_collect()
        .await?;
    let files = Arc::new(Inventory(entries));
    *cached = Some(CachedInventory {
        run,
        fetched: Instant::now(),
        files: Arc::clone(&files),
    });
    Ok(files)
}

fn listing_hours(text: &str) -> HashSet<u16> {
    text.split("href=\"")
        .skip(1)
        .filter_map(|part| {
            let (name, _) = part.split_once('"')?;
            let hour = name.strip_prefix("PT")?.strip_suffix("H00M.grib2")?;
            (hour.len() == 3 && hour.bytes().all(|byte| byte.is_ascii_digit()))
                .then_some(hour)?
                .parse()
                .ok()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    #[test]
    fn dwd_native_filenames_and_forecast_cadence() {
        let run = OffsetDateTime::parse("2026-11-23T06:00:00Z", &Rfc3339).unwrap();
        let frame = Frame::new(run, 24).unwrap();
        let [file] = frame.plan("hgt925").unwrap().files.try_into().unwrap();
        assert!(file
            .url(frame.run)
            .ends_with("/FI/lvt1/100/lv1/92500/r/2026-11-23T06%3A00/s/PT024H00M.grib2"));
        let [file] = frame.plan("hgt1000").unwrap().files.try_into().unwrap();
        assert!(file
            .url(frame.run)
            .ends_with("/FI/lvt1/100/lv1/100000/r/2026-11-23T06%3A00/s/PT024H00M.grib2"));
        assert!(coordinates()[0]
            .url(frame.run)
            .ends_with("/CLAT/r/2026-11-23T06%3A00/s/PT000H00M.grib2"));
        assert!(Frame::new(run, 78).is_ok());
        assert!(Frame::new(run, 79).is_err());
        assert!(Frame::new(run, 81).is_ok());
        assert!(Frame::new(run, 120).is_ok());
        assert!(Frame::new(run, 123).is_err());
        assert!(Frame::new(run + time::Duration::hours(6), 180).is_ok());
        assert!(Frame::new(run + time::Duration::hours(3), 24).is_err());
    }

    #[test]
    fn accumulations_use_exact_endpoints_and_gust_needs_every_hour() {
        let run = OffsetDateTime::parse("2026-11-23T06:00:00Z", &Rfc3339).unwrap();
        let get = |name, hour| Frame::new(run, hour).unwrap().plan(name);
        let hours = |plan: Plan| plan.files.iter().map(|file| file.hour).collect::<Vec<_>>();
        assert_eq!(hours(get("qpf120h", 120).unwrap()), [120, 0]);
        assert!(matches!(get("qpf120h", 117), Err(Error::Unavailable(404))));
        assert_eq!(hours(get("qpf48h", 81).unwrap()), [81, 33]);
        assert_eq!(hours(get("prate", 78).unwrap()), [78, 77]);
        assert_eq!(hours(get("prate", 81).unwrap()), [81, 78]);
        assert!(matches!(get("prate", 0), Err(Error::Unavailable(404))));
        // The 3-h average rate pairs this hour with the hour three steps back.
        assert_eq!(hours(get("prate3h", 78).unwrap()), [78, 75]);
        assert_eq!(hours(get("prate3h", 81).unwrap()), [81, 78]);
        assert_eq!(hours(get("prate3h", 6).unwrap()), [6, 3]);
        assert!(matches!(get("prate3h", 2), Err(Error::Unavailable(404))));
        assert!((get("prate3h", 81).unwrap().reduction.combine(21.6, 10.8) - 10.8).abs() < 1e-8);
        assert_eq!(hours(get("gust", 78).unwrap()), [78, 77, 76, 75, 74, 73]);
        assert!(matches!(get("gust", 5), Err(Error::Unavailable(404))));
        assert_eq!(hours(get("gust", 81).unwrap()), [81, 80, 79, 78, 77, 76]);
        assert_eq!(
            hours(get("gust", 120).unwrap()),
            [120, 119, 118, 117, 116, 115]
        );
        assert!((get("prate", 81).unwrap().reduction.combine(21.6, 10.8) - 0.001).abs() < 1e-8);
        assert_eq!(Reduction::Difference(1.0).combine(0.0, 0.01), 0.0);
        assert_eq!(Reduction::Max.combine(12.0, 15.0), 15.0);
        assert!(Reduction::Max.combine(12.0, f32::NAN).is_nan());
    }

    #[test]
    fn inventory_only_accepts_complete_hour_files() {
        let text = concat!(
            "<a href=\"../\">",
            "<a href=\"PT079H00M.grib2\">",
            "<a href=\"PT081H15M.grib2\">",
            "<a href=\"PT081H00M.grib2\">",
            "<a href=\"PT084H00M.grib2.html\">",
        );
        assert_eq!(listing_hours(text), HashSet::from([79, 81]));
    }
}

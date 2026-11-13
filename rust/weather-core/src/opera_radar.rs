//! EUMETNET OPERA open radar volumes and single-site scans.

mod decode;
#[cfg(test)]
mod tests;

use crate::polar_radar::{self, Palette};
use crate::radar_provider::{Error, Frame, Request};
use crate::{format_rfc3339_utc, radar_sites::RadarSite};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Deserialize;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const API: &str =
    "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/locations";
const LICENSE: &str = "https://creativecommons.org/licenses/by/4.0/";
const MAX_ODIM_BYTES: usize = 32 * 1024 * 1024;
const ETALAB: &str =
    "https://www.etalab.gouv.fr/wp-content/uploads/2018/11/open-licence.pdf (Etalab2.0)";
const CREDIT_CC: &str = "EUMETNET OPERA and national radar provider · CC BY 4.0 (creativecommons.org/licenses/by/4.0/) · rendered by SHARPpy Reimagined";
const CREDIT_FR: &str = "Météo-France / EUMETNET OPERA · Licence Ouverte 2.0 (data.gouv.fr/pages/legal/licences/etalab-2-0) · rendered by SHARPpy Reimagined";

#[derive(Deserialize)]
struct FeatureCollection {
    features: Vec<Feature>,
}
#[derive(Deserialize)]
struct Feature {
    id: String,
    geometry: Geometry,
    properties: Properties,
}
#[derive(Deserialize)]
struct Geometry {
    #[serde(rename = "type")]
    kind: String,
    coordinates: Vec<f64>,
}
#[derive(Deserialize)]
struct Properties {
    name: Option<String>,
    #[serde(rename = "parameter-name")]
    parameters: Vec<String>,
}

#[derive(Deserialize)]
struct CoverageCollection {
    coverages: Vec<Coverage>,
}
#[derive(Deserialize)]
struct Coverage {
    parameters: HashMap<String, serde_json::Value>,
    links: Vec<Link>,
    domain: Domain,
    #[serde(rename = "metocean:license")]
    license: Option<String>,
}
#[derive(Deserialize)]
struct Link {
    href: String,
    #[serde(rename = "type")]
    kind: String,
}
#[derive(Deserialize)]
struct Domain {
    axes: Axes,
}
#[derive(Deserialize)]
struct Axes {
    z: NumericAxis,
    t: TimeAxis,
}
#[derive(Deserialize)]
struct NumericAxis {
    values: Vec<f64>,
}
#[derive(Deserialize)]
struct TimeAxis {
    values: Vec<String>,
}

struct Scan {
    url: String,
    interval_s: f64,
    attribution: &'static str,
    nominal_time: OffsetDateTime,
}

impl Clone for Scan {
    fn clone(&self) -> Self {
        Self {
            url: self.url.clone(),
            interval_s: self.interval_s,
            attribution: self.attribution,
            nominal_time: self.nominal_time,
        }
    }
}

struct Catalogue {
    checked: Instant,
    sites: Vec<RadarSite>,
}
struct Listing {
    site: String,
    start: OffsetDateTime,
    end: OffsetDateTime,
    checked: Instant,
    scans: Vec<Scan>,
}

pub struct RadarClient {
    client: Result<reqwest::Client, String>,
    catalogue: Mutex<Option<Catalogue>>,
    listings: Mutex<VecDeque<Listing>>,
    frames: Mutex<VecDeque<(String, Arc<Frame>)>>,
    requests: tokio::sync::Semaphore,
}

pub fn client() -> &'static RadarClient {
    static CLIENT: OnceLock<RadarClient> = OnceLock::new();
    CLIENT.get_or_init(RadarClient::default)
}

impl Default for RadarClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: reqwest::Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .redirect(reqwest::redirect::Policy::none())
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs(25))
                .build()
                .map_err(|error| error.to_string()),
            catalogue: Mutex::new(None),
            listings: Mutex::new(VecDeque::new()),
            frames: Mutex::new(VecDeque::new()),
            requests: tokio::sync::Semaphore::new(3),
        }
    }
}

impl RadarClient {
    pub fn clear(&self) {
        self.listings
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
    }

    pub async fn sites(&self) -> Vec<RadarSite> {
        if let Some(sites) = self
            .catalogue
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .as_ref()
            .filter(|entry| entry.checked.elapsed() < Duration::from_secs(900))
            .map(|entry| entry.sites.clone())
        {
            return sites;
        }
        let url = format!("{API}?f=GeoJSON");
        if let Ok(Ok(bytes)) =
            tokio::time::timeout(Duration::from_secs(5), self.download(&url, 2 * 1024 * 1024)).await
        {
            if let Ok(sites) = parse_sites(&bytes) {
                *self
                    .catalogue
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner) = Some(Catalogue {
                    checked: Instant::now(),
                    sites: sites.clone(),
                });
                return sites;
            }
        }
        self.catalogue
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .as_ref()
            .map_or_else(Vec::new, |entry| entry.sites.clone())
    }

    pub async fn fetch(
        &self,
        request: Request,
        now: OffsetDateTime,
    ) -> Result<Option<Arc<Frame>>, Error> {
        let Some(id) = request.site_id.as_deref().filter(|id| is_opera_id(id)) else {
            return Ok(None);
        };
        let site = self
            .sites()
            .await
            .into_iter()
            .find(|site| site.id.eq_ignore_ascii_case(id))
            .ok_or_else(|| Error::Invalid("OPERA radar is not in the current catalogue.".into()))?;
        if request
            .product
            .as_deref()
            .is_some_and(|product| product != "reflectivity")
        {
            return Err(Error::Invalid(
                "OPERA currently supports reflectivity only.".into(),
            ));
        }
        if request.tilt.as_deref().is_some_and(|tilt| tilt != "0") {
            return Err(Error::Invalid(
                "OPERA currently uses its lowest available scan.".into(),
            ));
        }
        let target = request
            .valid_time
            .as_deref()
            .map(|value| OffsetDateTime::parse(value, &Rfc3339))
            .transpose()
            .map_err(|_| Error::Invalid("A valid radar time is required.".into()))?
            .unwrap_or(now);
        if target < now - time::Duration::minutes(70) || target > now + time::Duration::minutes(1) {
            return Err(Error::Invalid(
                "Radar history is available only for the past 60 minutes.".into(),
            ));
        }
        for scan in self.scans(&site, target, now).await?.into_iter().take(6) {
            let url = scan.url;
            if let Some(frame) = self
                .frames
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .iter()
                .find(|(cached, frame)| cached == &url && frame.attribution == scan.attribution)
                .map(|(_, frame)| Arc::clone(frame))
            {
                if frame.actual_time <= target
                    && frame.actual_time >= target - time::Duration::minutes(10)
                {
                    return Ok(Some(frame));
                }
                continue;
            }
            let bytes = match self.download(&url, MAX_ODIM_BYTES).await {
                Err(Error::ScanMissing) => continue,
                other => other?,
            };
            let source = url.clone();
            let site = site.clone();
            let result = tokio::task::spawn_blocking(move || {
                make_frame(
                    &site,
                    &bytes,
                    target,
                    now,
                    source,
                    scan.interval_s,
                    scan.attribution,
                )
            })
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
            let frame = match result {
                Ok(frame) => Arc::new(frame),
                Err(Error::ScanMissing) => continue,
                Err(error) => return Err(error),
            };
            let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
            if cache.len() >= 12 {
                cache.pop_front();
            }
            cache.push_back((url, Arc::clone(&frame)));
            return Ok(Some(frame));
        }
        Err(Error::ScanMissing)
    }

    async fn scans(
        &self,
        site: &RadarSite,
        target: OffsetDateTime,
        now: OffsetDateTime,
    ) -> Result<Vec<Scan>, Error> {
        let window_start = target - time::Duration::minutes(15);
        if let Some(scans) = self
            .listings
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|entry| {
                entry.site == site.id
                    && entry.start <= window_start
                    && entry.end >= target
                    && entry.checked.elapsed() < Duration::from_secs(45)
            })
            .map(|entry| {
                entry
                    .scans
                    .iter()
                    .filter(|scan| {
                        scan.nominal_time >= window_start
                            && scan.nominal_time <= target + time::Duration::minutes(2)
                    })
                    .cloned()
                    .collect::<Vec<_>>()
            })
        {
            return Ok(scans);
        }
        let query_start = if target < now - time::Duration::minutes(1) {
            window_start.min(now - time::Duration::minutes(75))
        } else {
            window_start
        };
        let query_end = if target < now - time::Duration::minutes(1) {
            now
        } else {
            target
        };
        let start = query_start
            .format(&Rfc3339)
            .map_err(|e| Error::Invalid(e.to_string()))?;
        let stamp = query_end
            .format(&Rfc3339)
            .map_err(|error| Error::Invalid(error.to_string()))?;
        let mut url = reqwest::Url::parse(&format!("{API}/{}", site.id))
            .map_err(|e| Error::Invalid(e.to_string()))?;
        url.query_pairs_mut()
            .append_pair("datetime", &format!("{start}/{stamp}"))
            .append_pair("f", "CoverageJSON")
            .append_pair("format", "ODIM");
        let bytes = self.download(url.as_str(), 4 * 1024 * 1024).await?;
        let scans = parse_scans(&bytes, site)?;
        let filtered = scans
            .iter()
            .filter(|scan| {
                scan.nominal_time >= window_start
                    && scan.nominal_time <= target + time::Duration::minutes(2)
            })
            .cloned()
            .collect::<Vec<_>>();
        let mut cache = self.listings.lock().unwrap_or_else(PoisonError::into_inner);
        if cache.len() >= 32 {
            cache.pop_front();
        }
        cache.push_back(Listing {
            site: site.id.clone(),
            start: query_start,
            end: query_end,
            checked: Instant::now(),
            scans,
        });
        Ok(filtered)
    }

    async fn download(&self, url: &str, limit: usize) -> Result<Vec<u8>, Error> {
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
        let response = self
            .client
            .as_ref()
            .map_err(|error| Error::Fetch(error.clone()))?
            .get(url)
            .send()
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(Error::ScanMissing);
        }
        let response = response
            .error_for_status()
            .map_err(|error| Error::Fetch(error.to_string()))?;
        crate::http::body(response, limit)
            .await
            .map_err(Error::Fetch)
    }
}

pub(crate) fn is_opera_id(id: &str) -> bool {
    let parts = id.split('-').collect::<Vec<_>>();
    parts.len() == 4
        && parts[0] == "0"
        && (1..=3).contains(&parts[1].len())
        && parts[1].bytes().all(|b| b.is_ascii_digit())
        && parts[2] == "0"
        && parts[3].len() == 5
        && parts[3].as_bytes()[..2].iter().all(u8::is_ascii_alphabetic)
        && parts[3].bytes().all(|b| b.is_ascii_alphanumeric())
}

fn parse_sites(bytes: &[u8]) -> Result<Vec<RadarSite>, String> {
    let data: FeatureCollection =
        serde_json::from_slice(bytes).map_err(|error| error.to_string())?;
    if data.features.len() > 1000 {
        return Err("OPERA catalogue is too large".into());
    }
    let mut seen = HashSet::new();
    let mut sites = Vec::new();
    for feature in data.features {
        if !is_opera_id(&feature.id)
            || feature.id.starts_with("0-276-0-")
            || feature.geometry.kind != "Point"
            || feature.geometry.coordinates.len() != 2
            || !feature
                .properties
                .parameters
                .iter()
                .any(|p| p == "DBZH:scan")
        {
            continue;
        }
        let (lon, lat) = (
            feature.geometry.coordinates[0],
            feature.geometry.coordinates[1],
        );
        if !lat.is_finite()
            || !lon.is_finite()
            || !(-90.0..=90.0).contains(&lat)
            || !(-180.0..=180.0).contains(&lon)
            || !seen.insert(feature.id.to_ascii_lowercase())
        {
            continue;
        }
        let code = feature.id.rsplit('-').next().expect("validated OPERA id");
        let country = code[..2].to_ascii_uppercase();
        let name = feature
            .properties
            .name
            .as_deref()
            .map(str::trim)
            .map(|name| {
                name.strip_prefix('[')
                    .and_then(|rest| rest.split_once(']'))
                    .filter(|(prefix, _)| prefix.eq_ignore_ascii_case(code))
                    .map_or(name, |(_, name)| name.trim())
            })
            .filter(|name| {
                !name.is_empty()
                    && !name.eq_ignore_ascii_case(code)
                    && name.len() <= 120
                    && !name.chars().any(char::is_control)
            })
            .or_else(|| crate::radar_sites::opera_site_name(code))
            .map(str::to_owned);
        sites.push(RadarSite {
            id: feature.id,
            lat,
            lon,
            half_span_deg: 0.0,
            products: vec!["reflectivity".into()],
            name,
            country: Some(country),
            wmo: None,
            provider: Some("OPERA".into()),
        });
    }
    Ok(sites)
}

fn parse_scans(bytes: &[u8], site: &RadarSite) -> Result<Vec<Scan>, Error> {
    let collection: CoverageCollection =
        serde_json::from_slice(bytes).map_err(|e| Error::Fetch(e.to_string()))?;
    if collection.coverages.len() > 1000 {
        return Err(Error::Fetch("OPERA response is too large".into()));
    }
    let nod = site.id.rsplit('-').next().expect("validated OPERA id");
    let mut scans = Vec::new();
    let mut unsupported_license = false;
    for coverage in collection.coverages {
        if !coverage.parameters.contains_key("DBZH:scan") {
            continue;
        }
        let attribution = match coverage.license.as_deref() {
            Some(LICENSE | "https://creativecommons.org/licenses/by/4.0/ (CC BY 4.0)") => CREDIT_CC,
            Some(ETALAB) if site.country.as_deref() == Some("FR") => CREDIT_FR,
            _ => {
                unsupported_license = true;
                continue;
            }
        };
        let Some(&angle) = coverage.domain.axes.z.values.first() else {
            continue;
        };
        if !angle.is_finite() || !(0.0..=30.0).contains(&angle) {
            continue;
        }
        let mut times = coverage
            .domain
            .axes
            .t
            .values
            .iter()
            .filter_map(|value| OffsetDateTime::parse(value, &Rfc3339).ok())
            .collect::<Vec<_>>();
        times.sort_unstable();
        let interval_s = times
            .windows(2)
            .rev()
            .map(|pair| (pair[1] - pair[0]).whole_seconds())
            .find(|seconds| (1..=3600).contains(seconds))
            .unwrap_or(0) as f64;
        for link in coverage.links {
            if link.kind != "application/x-odim" {
                continue;
            }
            let Ok(url) = reqwest::Url::parse(&link.href) else {
                continue;
            };
            if url.scheme() != "https"
                || url.host_str() != Some("s3.waw3-1.cloudferro.com")
                || url.port().is_some()
                || !url.username().is_empty()
                || url.password().is_some()
                || url.query().is_some()
                || url.fragment().is_some()
            {
                continue;
            }
            let Some(parts) = url.path_segments().map(|parts| parts.collect::<Vec<_>>()) else {
                continue;
            };
            if parts.len() != 8
                || parts[0] != "openradar-24h"
                || !parts[5].eq_ignore_ascii_case(nod)
            {
                continue;
            }
            let file = parts[7].split('@').collect::<Vec<_>>();
            if file.len() != 4
                || !file[0].eq_ignore_ascii_case(nod)
                || !file[3]
                    .strip_suffix(".h5")
                    .is_some_and(|q| q.split('_').any(|x| x == "DBZH"))
            {
                continue;
            }
            if file[1].len() != 13
                || file[1].as_bytes()[8] != b'T'
                || !file[1]
                    .bytes()
                    .enumerate()
                    .all(|(i, b)| i == 8 || b.is_ascii_digit())
            {
                continue;
            }
            let nominal_stamp = format!(
                "{}-{}-{}T{}:{}:00Z",
                &file[1][0..4],
                &file[1][4..6],
                &file[1][6..8],
                &file[1][9..11],
                &file[1][11..13]
            );
            let Ok(nominal_time) = OffsetDateTime::parse(&nominal_stamp, &Rfc3339) else {
                continue;
            };
            scans.push((
                angle,
                file[1].to_owned(),
                Scan {
                    url: url.to_string(),
                    interval_s,
                    attribution,
                    nominal_time,
                },
            ));
        }
    }
    if scans.is_empty() && unsupported_license {
        return Err(Error::Fetch(
            "This radar's published data license is not supported.".into(),
        ));
    }
    scans.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| b.1.cmp(&a.1)));
    let mut seen = HashSet::new();
    Ok(scans
        .into_iter()
        .filter_map(|(_, _, scan)| seen.insert(scan.url.clone()).then_some(scan))
        .collect())
}

fn make_frame(
    site: &RadarSite,
    bytes: &[u8],
    target: OffsetDateTime,
    now: OffsetDateTime,
    url: String,
    interval_s: f64,
    attribution: &str,
) -> Result<Frame, Error> {
    let sweep = decode::decode(bytes, site, target).map_err(|error| {
        if error == "No recent ODIM DBZH sweep" {
            Error::ScanMissing
        } else {
            Error::Fetch(error)
        }
    })?;
    let actual = OffsetDateTime::parse(&sweep.valid_time, &Rfc3339)
        .map_err(|e| Error::Fetch(e.to_string()))?;
    let mut palette: Palette = serde_json::from_str(include_str!(
        "../resources/australian_reflectivity.palette.json"
    ))
    .expect("bundled radar palette");
    palette.floor = Some(0.0);
    let (bounds, png) = polar_radar::render(&sweep, site, &palette).map_err(Error::Fetch)?;
    let elevation_angle = sweep
        .radials
        .iter()
        .all(|ray| ray.elevation_angle == Some(sweep.elevation_angle))
        .then_some(sweep.elevation_angle);
    Ok(Frame {
        actual_time: actual,
        product: "reflectivity".into(),
        site_id: site.id.clone(),
        elevation_angle,
        palette,
        legend_png_base64: None,
        label: "Doppler-filtered Reflectivity".into(),
        units: "dBZ".into(),
        bounds,
        retrieved_at: format_rfc3339_utc(now),
        valid_time: format_rfc3339_utc(actual),
        update_interval_s: interval_s,
        attribution: attribution.into(),
        source_url: url,
        byte_length: png.len(),
        png_base64: STANDARD.encode(png),
    })
}

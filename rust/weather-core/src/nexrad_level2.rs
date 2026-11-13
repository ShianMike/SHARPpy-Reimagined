//! US NEXRAD Level II S3 volume listing, decoding, tilt inventory, and rendering.

pub(crate) mod decode;
#[cfg(test)]
mod tests;

use self::decode::{decode_volume, DecodedVolume, MomentKind, MAX_FILE_BYTES, TILTS};
use crate::polar_radar::{self, Bounds, Palette, RenderOptions};
use crate::radar::{RadarTiltStatus, RadarTilts};
use crate::radar_provider::{Error, Frame, Request};
use crate::radar_sites::RadarSite;
use crate::{format_rfc3339_utc, polar_radar as render};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::collections::{HashMap, VecDeque};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{format_description::well_known::Rfc3339, Date, OffsetDateTime, PrimitiveDateTime};

const BASE: &str = "https://unidata-nexrad-level2.s3.amazonaws.com";
const LIST_LIMIT: usize = 512 * 1024;
const CACHE_TTL: Duration = Duration::from_secs(45);
const MAX_CACHE_ENTRIES: usize = 32;
const MAX_CACHE_BYTES: u64 = 512 * 1024 * 1024;
#[cfg(not(test))]
const DEFAULT_PIXELS: usize = 4096;
#[cfg(test)]
const DEFAULT_PIXELS: usize = 64;
const ATTRIBUTION: &str = "NOAA/NEXRAD Level II · NSF Unidata";

type ScanEntry = (OffsetDateTime, String, usize);
type VolumeSlot = Arc<tokio::sync::Mutex<Option<Result<Arc<DecodedVolume>, String>>>>;

struct InflightGuard<'a> {
    map: &'a Mutex<HashMap<String, VolumeSlot>>,
    key: String,
}

impl Drop for InflightGuard<'_> {
    fn drop(&mut self) {
        self.map
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .remove(&self.key);
    }
}

struct ListingCache {
    prefix: String,
    checked: Instant,
    scans: Vec<ScanEntry>,
}

pub struct RadarClient {
    client: Result<reqwest::Client, String>,
    listings: Mutex<VecDeque<ListingCache>>,
    volumes: Mutex<VecDeque<(String, Arc<DecodedVolume>)>>,
    inflight: Mutex<HashMap<String, VolumeSlot>>,
    frames: Mutex<VecDeque<(String, Arc<Frame>)>>,
    requests: tokio::sync::Semaphore,
    #[cfg(test)]
    base: String,
}

pub fn client() -> &'static RadarClient {
    static CLIENT: OnceLock<RadarClient> = OnceLock::new();
    CLIENT.get_or_init(RadarClient::default)
}

impl Default for RadarClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or(30.0)
            .max(45.0);
        Self {
            client: reqwest::Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| error.to_string()),
            listings: Mutex::new(VecDeque::new()),
            volumes: Mutex::new(VecDeque::new()),
            inflight: Mutex::new(HashMap::new()),
            frames: Mutex::new(VecDeque::new()),
            requests: tokio::sync::Semaphore::new(4),
            #[cfg(test)]
            base: BASE.into(),
        }
    }
}

impl RadarClient {
    pub fn clear(&self) {
        self.listings
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.volumes
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.inflight
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        polar_radar::clear_cache();
    }

    pub async fn availability(
        &self,
        site: &'static RadarSite,
        product: &str,
        target: Option<OffsetDateTime>,
        now: OffsetDateTime,
        cache_dir: Option<&Path>,
    ) -> Result<RadarTilts, Error> {
        if MomentKind::from_product(product).is_none() {
            return Err(Error::Invalid("unknown Level II product".into()));
        }
        let moment = MomentKind::from_product(product).expect("checked above");
        let selected = self.select(site, target, now).await?;
        let volume = match selected {
            Some(scan) => Some(self.volume(site, &scan, cache_dir).await?),
            None => None,
        };
        let tilts = TILTS
            .iter()
            .map(|(cut, _, _)| RadarTiltStatus {
                tilt: (*cut).into(),
                status: if volume
                    .as_ref()
                    .is_some_and(|vol| vol.cuts.contains_key(&(moment, *cut)))
                {
                    "available"
                } else {
                    "unavailable"
                }
                .into(),
            })
            .collect();
        Ok(RadarTilts {
            site_id: site.id.clone(),
            product: product.into(),
            checked_at: format_rfc3339_utc(now),
            tilts,
        })
    }

    pub(crate) async fn fetch_site(
        &self,
        request: Request,
        now: OffsetDateTime,
        site: &'static RadarSite,
        cache_dir: Option<&Path>,
    ) -> Result<Option<Arc<Frame>>, Error> {
        self.fetch_site_with_pixels(request, now, site, DEFAULT_PIXELS, cache_dir)
            .await
    }

    pub async fn fetch_site_with_pixels(
        &self,
        request: Request,
        now: OffsetDateTime,
        site: &'static RadarSite,
        pixels: usize,
        cache_dir: Option<&Path>,
    ) -> Result<Option<Arc<Frame>>, Error> {
        let product = request.product.as_deref().unwrap_or("reflectivity");
        let Some(moment) = MomentKind::from_product(product) else {
            return Ok(None);
        };
        let tilt = request.tilt.as_deref().unwrap_or("0");
        let Some(&(tilt_key, _, _)) = TILTS.iter().find(|(key, _, _)| *key == tilt) else {
            return Err(Error::Invalid("unknown radar tilt".into()));
        };
        let requested = request
            .valid_time
            .as_deref()
            .map(|value| OffsetDateTime::parse(value, &Rfc3339))
            .transpose()
            .map_err(|_| Error::Invalid("A valid radar time is required.".into()))?;
        let selected = self
            .select(site, requested, now)
            .await?
            .ok_or(Error::ScanMissing)?;
        let pixels = pixels.clamp(64, 4096);
        let cache_key = format!("level2:{product}:{}:{tilt_key}:{pixels}", selected.1);
        if let Some(frame) = self
            .frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|(key, _)| key == &cache_key)
            .map(|(_, frame)| Arc::clone(frame))
        {
            if !frame
                .actual_time
                .is_some_and(|actual| matches_window(actual, requested, now))
            {
                return Err(Error::ScanMissing);
            }
            return Ok(Some(frame));
        }
        let volume = self.volume(site, &selected, cache_dir).await?;
        let Some(cut) = volume.cuts.get(&(moment, tilt_key)) else {
            return Err(Error::ScanMissing);
        };
        if !matches_window(cut.when, requested, now) {
            return Err(Error::ScanMissing);
        }
        let bounds = site_bounds(site);
        let palette = palette_for(product);
        let folded_rgb = (moment == MomentKind::Reflectivity).then_some([128, 0, 255]);
        let source_url = format!("{}/{}", self.base_url(), selected.1);
        let render_sweep = polar_radar::Sweep {
            site_id: cut.sweep.site_id.clone(),
            valid_time: cut.sweep.valid_time.clone(),
            elevation_angle: cut.sweep.elevation_angle,
            radials: cut
                .sweep
                .radials
                .iter()
                .map(|r| polar_radar::Radial {
                    azimuth: r.azimuth,
                    width: r.width,
                    elevation_angle: r.elevation_angle,
                    first_gate_m: r.first_gate_m,
                    gate_spacing_m: r.gate_spacing_m,
                    values: r.values.clone(),
                })
                .collect(),
        };
        let render_site = site.clone();
        let render_palette = palette.clone();
        let (bounds, png) = tokio::task::spawn_blocking(move || {
            render::render_with_options(
                &render_sweep,
                &render_site,
                &render_palette,
                RenderOptions {
                    pixels,
                    bounds: Some(bounds),
                    folded_rgb,
                },
            )
        })
        .await
        .map_err(|error| Error::Fetch(error.to_string()))?
        .map_err(Error::Fetch)?;
        let (label, units) = if moment == MomentKind::CorrelationCoefficient {
            ("Correlation Coefficient", "")
        } else {
            ("Base Reflectivity", "dBZ")
        };
        let frame = Arc::new(Frame {
            actual_time: Some(cut.when),
            product: product.into(),
            site_id: site.id.clone(),
            elevation_angle: Some(cut.sweep.elevation_angle),
            palette: Some(palette),
            legend_png_base64: None,
            label: format!("{label} · {:.1}°", cut.sweep.elevation_angle),
            units: units.into(),
            bounds,
            retrieved_at: format_rfc3339_utc(now),
            valid_time: Some(format_rfc3339_utc(cut.when)),
            update_interval_s: 150.0,
            attribution: ATTRIBUTION.into(),
            source_url,
            byte_length: png.len(),
            png_base64: STANDARD.encode(png),
        });
        let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
        cache.retain(|(key, _)| key != &cache_key);
        if cache.len() >= 12 {
            cache.pop_front();
        }
        cache.push_back((cache_key, Arc::clone(&frame)));
        Ok(Some(frame))
    }

    fn base_url(&self) -> &str {
        #[cfg(not(test))]
        {
            BASE
        }
        #[cfg(test)]
        {
            self.base.as_str()
        }
    }

    async fn select(
        &self,
        site: &RadarSite,
        requested: Option<OffsetDateTime>,
        now: OffsetDateTime,
    ) -> Result<Option<ScanEntry>, Error> {
        let target = requested.unwrap_or(now);
        if target < now - time::Duration::minutes(70) || target > now + time::Duration::minutes(1) {
            return Err(Error::Invalid(
                "radar history is available only for the past 60 minutes".into(),
            ));
        }
        let cutoff = now - time::Duration::minutes(70);
        let mut days = vec![cutoff.date()];
        if now.date() != cutoff.date() {
            days.push(now.date());
        }
        let mut scans = Vec::new();
        for day in days {
            for scan in self.listing(site, day, cutoff).await? {
                if scan.0 >= cutoff && scan.0 <= now {
                    scans.push(scan);
                }
            }
        }
        let selected = match requested {
            Some(_) => scans
                .into_iter()
                .filter(|(when, _, _)| (*when - target).abs() <= time::Duration::minutes(10))
                .min_by_key(|(when, _, _)| (*when - target).abs()),
            None => scans.into_iter().max_by_key(|(when, _, _)| *when),
        };
        Ok(selected)
    }

    async fn listing(
        &self,
        site: &RadarSite,
        day: Date,
        cutoff: OffsetDateTime,
    ) -> Result<Vec<ScanEntry>, Error> {
        let prefix = format!(
            "{:04}/{:02}/{:02}/{}/",
            day.year(),
            u8::from(day.month()),
            day.day(),
            site.id
        );
        if let Some(scans) = self
            .listings
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|entry| entry.prefix == prefix && entry.checked.elapsed() < CACHE_TTL)
            .map(|entry| entry.scans.clone())
        {
            return Ok(scans);
        }
        let start_after = format!(
            "{prefix}{}{:04}{:02}{:02}_{:02}{:02}{:02}",
            site.id,
            cutoff.year(),
            u8::from(cutoff.month()),
            cutoff.day(),
            cutoff.hour(),
            cutoff.minute(),
            cutoff.second()
        );
        let mut url = reqwest::Url::parse(&format!("{}/", self.base_url()))
            .map_err(|error| Error::Fetch(error.to_string()))?;
        url.query_pairs_mut().extend_pairs([
            ("list-type", "2"),
            ("prefix", prefix.as_str()),
            ("max-keys", "1000"),
            ("start-after", start_after.as_str()),
        ]);
        let body = self.download(url.as_str(), LIST_LIMIT).await?;
        let scans = parse_listing(&body, &prefix, &site.id).map_err(Error::Fetch)?;
        let mut cache = self.listings.lock().unwrap_or_else(PoisonError::into_inner);
        cache.retain(|entry| entry.prefix != prefix);
        if cache.len() >= 32 {
            cache.pop_front();
        }
        cache.push_back(ListingCache {
            prefix,
            checked: Instant::now(),
            scans: scans.clone(),
        });
        Ok(scans)
    }

    async fn volume(
        &self,
        site: &'static RadarSite,
        scan: &ScanEntry,
        cache_dir: Option<&Path>,
    ) -> Result<Arc<DecodedVolume>, Error> {
        let (expected, key, size) = scan;
        if let Some(volume) = self
            .volumes
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|(cached_key, _)| cached_key == key)
            .map(|(_, volume)| Arc::clone(volume))
        {
            return Ok(volume);
        }
        let slot = {
            let mut inflight = self.inflight.lock().unwrap_or_else(PoisonError::into_inner);
            Arc::clone(
                inflight
                    .entry(key.clone())
                    .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(None))),
            )
        };
        let mut guard = slot.lock().await;
        if let Some(result) = guard.as_ref() {
            return result.clone().map_err(Error::Fetch);
        }
        let _cleanup = InflightGuard {
            map: &self.inflight,
            key: key.clone(),
        };
        let fetched = self
            .load_volume(site, *expected, key, *size, cache_dir)
            .await;
        match &fetched {
            Ok(volume) => {
                let mut cache = self.volumes.lock().unwrap_or_else(PoisonError::into_inner);
                cache.retain(|(cached_key, _)| cached_key != key);
                if cache.len() >= 2 {
                    cache.pop_front();
                }
                cache.push_back((key.clone(), Arc::clone(volume)));
                *guard = Some(Ok(Arc::clone(volume)));
            }
            Err(message) => {
                *guard = Some(Err(message.clone()));
            }
        }
        fetched.map_err(Error::Fetch)
    }

    async fn load_volume(
        &self,
        site: &'static RadarSite,
        expected: OffsetDateTime,
        key: &str,
        size: usize,
        cache_dir: Option<&Path>,
    ) -> Result<Arc<DecodedVolume>, String> {
        let cache_path = cache_dir.and_then(|dir| volume_cache_path(dir, key));
        if let Some(path) = cache_path.as_ref() {
            if let Some(bytes) = read_cached_volume(path, size) {
                let site_ref = site;
                let decoded = tokio::task::spawn_blocking(move || {
                    decode_volume(&bytes, site_ref, Some(expected))
                })
                .await
                .map_err(|error| error.to_string())?;
                match decoded {
                    Ok(volume) => return Ok(Arc::new(volume)),
                    Err(_) => {
                        let _ = fs::remove_file(path);
                    }
                }
            }
        }
        let url = format!("{}/{}", self.base_url(), key);
        let bytes = self
            .download(&url, MAX_FILE_BYTES)
            .await
            .map_err(|error| format!("Level II volume could not be loaded: {error}"))?;
        if bytes.len() != size || bytes.len() > MAX_FILE_BYTES {
            return Err(
                "Level II volume could not be loaded: incomplete or oversized Level II volume"
                    .into(),
            );
        }
        let bytes_for_cache = cache_path.as_ref().map(|_| bytes.clone());
        let decoded = tokio::task::spawn_blocking(move || {
            let volume = decode_volume(&bytes, site, Some(expected))
                .map_err(|error| format!("Level II volume could not be loaded: {error}"))?;
            if let (Some(path), Some(raw)) = (cache_path, bytes_for_cache) {
                store_cached_volume(&path, &raw);
            }
            Ok::<_, String>(Arc::new(volume))
        })
        .await
        .map_err(|error| error.to_string())??;
        Ok(decoded)
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
            .map_err(|error| Error::Fetch(error.to_string()))?
            .error_for_status()
            .map_err(|error| Error::Fetch(error.to_string()))?;
        crate::http::body(response, limit)
            .await
            .map_err(Error::Fetch)
    }
}

fn matches_window(
    actual: OffsetDateTime,
    requested: Option<OffsetDateTime>,
    now: OffsetDateTime,
) -> bool {
    if actual > now {
        return false;
    }
    match requested {
        Some(target) => (actual - target).abs() <= time::Duration::minutes(10),
        None => actual >= now - time::Duration::minutes(70),
    }
}

fn site_bounds(site: &RadarSite) -> Bounds {
    let span = site.half_span_deg;
    Bounds {
        west: (site.lon - span).max(-180.0),
        east: (site.lon + span).min(180.0),
        south: (site.lat - span).max(-90.0),
        north: (site.lat + span).min(90.0),
    }
}

pub(crate) fn palette_for(product: &str) -> Palette {
    if product == "correlation-coefficient" {
        serde_json::from_str(include_str!("../resources/nexrad_correlation.palette.json"))
            .expect("bundled correlation palette")
    } else {
        serde_json::from_str(include_str!(
            "../resources/australian_reflectivity.palette.json"
        ))
        .expect("bundled reflectivity palette")
    }
}

pub(crate) fn parse_listing(
    payload: &[u8],
    prefix: &str,
    site_id: &str,
) -> Result<Vec<ScanEntry>, String> {
    if payload.len() > LIST_LIMIT {
        return Err("Level II listing exceeded the byte budget".into());
    }
    let text = String::from_utf8_lossy(payload);
    let upper = text.to_ascii_uppercase();
    if upper.contains("<!DOCTYPE") || upper.contains("<!ENTITY") {
        return Err("Level II returned an unsafe volume listing".into());
    }
    if !text.contains("<ListBucketResult") || !text.contains("<IsTruncated>false</IsTruncated>") {
        return Err("Level II returned an invalid volume listing".into());
    }
    let stamp_fmt =
        time::format_description::parse_borrowed::<2>("[year][month][day]_[hour][minute][second]")
            .expect("static Level II listing timestamp format");
    let key_prefix = format!("{prefix}{site_id}");
    let mut scans = Vec::new();
    for chunk in text.split("<Contents>").skip(1) {
        let Some((body, _)) = chunk.split_once("</Contents>") else {
            return Err("Level II returned an invalid volume listing".into());
        };
        let Some(key) = xml_tag(body, "Key") else {
            continue;
        };
        let Some(suffix) = key.strip_prefix(&key_prefix) else {
            continue;
        };
        if suffix.len() != 19
            || !suffix.as_bytes()[..8].iter().all(u8::is_ascii_digit)
            || suffix.as_bytes()[8] != b'_'
            || !suffix.as_bytes()[9..15].iter().all(u8::is_ascii_digit)
            || &suffix.as_bytes()[15..17] != b"_V"
            || !suffix.as_bytes()[17..19].iter().all(u8::is_ascii_digit)
        {
            continue;
        }
        let Ok(parsed) = PrimitiveDateTime::parse(&suffix[..15], &stamp_fmt) else {
            continue;
        };
        let Ok(size) = xml_tag(body, "Size").unwrap_or("0").trim().parse::<usize>() else {
            return Err("Level II returned an invalid volume listing".into());
        };
        if size > 0 && size <= MAX_FILE_BYTES {
            scans.push((parsed.assume_utc(), key.to_owned(), size));
        }
    }
    Ok(scans)
}

fn xml_tag<'a>(xml: &'a str, tag: &str) -> Option<&'a str> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let (_, rest) = xml.split_once(&open)?;
    let (value, _) = rest.split_once(&close)?;
    Some(value)
}

fn volume_cache_path(dir: &Path, key: &str) -> Option<PathBuf> {
    let file = key.rsplit('/').next()?;
    if file.is_empty()
        || file.contains("..")
        || !file
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    {
        return None;
    }
    Some(dir.join(format!("{file}.ar2")))
}

fn read_cached_volume(path: &Path, expected_size: usize) -> Option<Vec<u8>> {
    let meta = fs::symlink_metadata(path).ok()?;
    if meta.file_type().is_symlink() || meta.len() != expected_size as u64 {
        return None;
    }
    let bytes = fs::read(path).ok()?;
    (bytes.len() == expected_size && bytes.len() <= MAX_FILE_BYTES).then_some(bytes)
}

fn store_cached_volume(path: &Path, payload: &[u8]) {
    static DISK_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    let Some(parent) = path.parent() else {
        return;
    };
    let _guard = DISK_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(PoisonError::into_inner);
    if fs::create_dir_all(parent).is_err() {
        return;
    }
    let Ok(read_dir) = fs::read_dir(parent) else {
        return;
    };
    let mut entries = Vec::new();
    let mut total: u64 = 0;
    for entry in read_dir.flatten() {
        let entry_path = entry.path();
        if entry_path == path || entry_path.extension().and_then(|e| e.to_str()) != Some("ar2") {
            continue;
        }
        let Ok(meta) = fs::symlink_metadata(&entry_path) else {
            continue;
        };
        if meta.file_type().is_symlink() {
            continue;
        }
        let modified = meta.modified().unwrap_or(std::time::SystemTime::UNIX_EPOCH);
        total = total.saturating_add(meta.len());
        entries.push((modified, meta.len(), entry_path));
    }
    entries.sort_by_key(|(modified, _, _)| *modified);
    let mut count = entries.len();
    let needed = payload.len() as u64;
    for (_, size, old_path) in entries {
        if count < MAX_CACHE_ENTRIES && total.saturating_add(needed) <= MAX_CACHE_BYTES {
            break;
        }
        if fs::remove_file(&old_path).is_ok() {
            count -= 1;
            total = total.saturating_sub(size);
        }
    }
    if count >= MAX_CACHE_ENTRIES || total.saturating_add(needed) > MAX_CACHE_BYTES {
        return;
    }
    let unique = format!(
        "{}.{}.part",
        path.file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("volume"),
        std::process::id()
    );
    let temp = parent.join(unique);
    if let Ok(mut file) = fs::File::create(&temp) {
        if file.write_all(payload).is_ok() && file.sync_all().is_ok() {
            drop(file);
            if fs::rename(&temp, path).is_ok() {
                return;
            }
        }
    }
    let _ = fs::remove_file(&temp);
}

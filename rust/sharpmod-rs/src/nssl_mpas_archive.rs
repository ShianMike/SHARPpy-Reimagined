//! Raw NSSL MPAS forecasts from the public THREDDS TAR archive.

use crate::model_field_cache::{Cache, WaitError};
use sharpmod_weather::{
    bridge_cache::{decompress_gzip_bounded, BridgeCacheError},
    model_http::{Error, ModelClient, ObjectIdentity},
};
use std::{
    collections::{HashMap, VecDeque},
    future::Future,
    io::{self, Read},
    sync::{Arc, Mutex, OnceLock},
    time::{Duration, Instant},
};
use time::{format_description::well_known::Rfc3339, Date, Month, OffsetDateTime, UtcOffset};
use tokio::sync::{Mutex as AsyncMutex, Semaphore};

const HOST: &str = "https://data.nssl.noaa.gov/thredds";
const DIRECTORY: &str = "FRDD/NSSL-MPAS";
const CATALOG_LIMIT: usize = 4 * 1024 * 1024;
const CATALOG_TTL: Duration = Duration::from_secs(300);
const RECENT_DAYS: i64 = 14;

const BLOCK: usize = 512;
const PREFIX_BYTES: usize = 32 * 1024;
const HEADER_WINDOW_BYTES: usize = 8 * 1024;
const MAX_COMPRESSED: usize = 160 * 1024 * 1024;
const MAX_GRIB: usize = 192 * 1024 * 1024;
const MAX_ARCHIVE: u64 = 16 * 1024 * 1024 * 1024;
const INDEX_TTL: Duration = Duration::from_secs(15 * 60);
const INDEX_CAPACITY: usize = 16;

const HOUR_TTL: Duration = Duration::from_secs(300);
const HOUR_CACHE_BYTES: usize = 256 * 1024 * 1024;
const POLL: Duration = Duration::from_millis(50);

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum Model {
    Htpo,
    Rn,
}

impl Model {
    fn from_key(key: &str) -> Option<Self> {
        match key {
            "nssl-mpas-htpo" => Some(Self::Htpo),
            "nssl-mpas-rn" => Some(Self::Rn),
            _ => None,
        }
    }

    fn tag(self) -> &'static str {
        match self {
            Self::Htpo => "mpasht2",
            Self::Rn => "mpasrn",
        }
    }

    fn cycle_hours(self) -> u8 {
        match self {
            Self::Htpo => 6,
            Self::Rn => 12,
        }
    }

    fn last_hour(self) -> u32 {
        match self {
            Self::Htpo => 48,
            Self::Rn => 84,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct Run {
    model: Model,
    init: OffsetDateTime,
}

impl Run {
    fn new(model: Model, init: OffsetDateTime) -> Option<Self> {
        let init = init.checked_to_offset(UtcOffset::UTC)?;
        ((0..=9999).contains(&init.year())
            && init.hour() % model.cycle_hours() == 0
            && init.minute() == 0
            && init.second() == 0
            && init.nanosecond() == 0)
            .then_some(Self { model, init })
    }

    fn stamp(self) -> String {
        format!(
            "{:04}{:02}{:02}{:02}",
            self.init.year(),
            u8::from(self.init.month()),
            self.init.day(),
            self.init.hour()
        )
    }

    fn archive_path(self) -> String {
        let stamp = self.stamp();
        format!(
            "{DIRECTORY}/{:04}/{}_{}.tar",
            self.init.year(),
            &stamp[2..],
            self.model.tag()
        )
    }

    fn archive_url(self) -> String {
        format!("{HOST}/fileServer/{}", self.archive_path())
    }
}

pub(crate) fn is_model(model: &str) -> bool {
    Model::from_key(model).is_some()
}

pub(crate) fn archive_url(model: &str, run: OffsetDateTime) -> Option<String> {
    Some(Run::new(Model::from_key(model)?, run)?.archive_url())
}

#[derive(Clone, Copy)]
struct Publication {
    run: Run,
    modified: OffsetDateTime,
}

type Catalog = Arc<[Publication]>;

fn parse_catalog(xml: &[u8], year: i32) -> Result<Catalog, Error> {
    let xml =
        std::str::from_utf8(xml).map_err(|_| invalid("invalid THREDDS catalogue encoding"))?;
    if !xml.contains("<catalog ") || !xml.trim_end().ends_with("</catalog>") {
        return Err(invalid("invalid THREDDS catalogue"));
    }

    // THREDDS lists the TAR objects as flat, double-quoted dataset entries.
    // Accept only paths generated for these two models and this catalogue year.
    Ok(xml
        .split("<dataset ")
        .skip(1)
        .filter_map(|entry| {
            let (attributes, body) = entry.split_once('>')?;
            let path = attributes.split_once(r#" urlPath=""#)?.1.split_once('"')?.0;
            let name = path.rsplit_once('/')?.1;
            let (stamp, tag) = name.strip_suffix(".tar")?.split_once('_')?;
            let model = match tag {
                "mpasht2" => Model::Htpo,
                "mpasrn" => Model::Rn,
                _ => return None,
            };
            if stamp.len() != 8 || !stamp.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            let init = timestamp(
                u16::try_from(year).ok()?,
                stamp[2..4].parse().ok()?,
                stamp[4..6].parse().ok()?,
                stamp[6..8].parse().ok()?,
                0,
                0,
            )?;
            let run = Run::new(model, init)?;
            if run.archive_path() != path {
                return None;
            }
            let modified = body
                .split_once(r#"<date type="modified">"#)?
                .1
                .split_once("</date>")?
                .0;
            let modified = OffsetDateTime::parse(modified.trim(), &Rfc3339).ok()?;
            (modified >= init).then_some(Publication { run, modified })
        })
        .collect())
}

/// Catalogue entries from the last two weeks, newest first. Member availability
/// is established separately by the range reader.
pub(crate) fn recent_runs(
    model: &str,
    now: OffsetDateTime,
    timeout: Duration,
) -> Result<Vec<OffsetDateTime>, Error> {
    let model = Model::from_key(model).ok_or_else(|| invalid("unknown model key"))?;
    let now = now
        .checked_to_offset(UtcOffset::UTC)
        .ok_or_else(|| invalid("invalid UTC date"))?;
    let since = now.saturating_sub(time::Duration::days(RECENT_DAYS));
    let deadline = deadline(timeout)?;
    let provider = provider();
    let mut runs = Vec::new();
    for year in (since.year()..=now.year()).rev() {
        let catalog = provider.catalog(year, deadline)?;
        runs.extend(catalog.iter().filter_map(|entry| {
            (entry.run.model == model && entry.run.init >= since && entry.modified <= now)
                .then_some(entry.run.init)
        }));
    }
    runs.sort_unstable_by(|a, b| b.cmp(a));
    runs.dedup();
    Ok(runs)
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct Hour {
    run: Run,
    lead: u32,
}

type HourBytes = Arc<Vec<u8>>;
type HourResult = Result<HourBytes, Error>;
type ArchiveIndexes = VecDeque<(Run, Arc<AsyncMutex<ArchiveIndex>>)>;

struct Provider {
    catalogs: Mutex<HashMap<i32, (Instant, Catalog)>>,
    indexes: Mutex<ArchiveIndexes>,
    ready: Mutex<VecDeque<(Hour, Instant, HourBytes)>>,
    flights: Arc<Cache<Hour, HourResult>>,
    transfers: Semaphore,
}

fn provider() -> &'static Provider {
    static PROVIDER: OnceLock<Provider> = OnceLock::new();
    PROVIDER.get_or_init(|| Provider {
        catalogs: Mutex::new(HashMap::new()),
        indexes: Mutex::new(VecDeque::new()),
        ready: Mutex::new(VecDeque::new()),
        flights: Cache::new(0, 64),
        transfers: Semaphore::new(2),
    })
}

impl Provider {
    fn catalog(&self, year: i32, deadline: Instant) -> Result<Catalog, Error> {
        if let Some((_, entries)) = self
            .catalogs
            .lock()
            .unwrap()
            .get(&year)
            .filter(|(fetched, _)| fetched.elapsed() < CATALOG_TTL)
        {
            return Ok(Arc::clone(entries));
        }
        let timeout = deadline.saturating_duration_since(Instant::now());
        if timeout.is_zero() {
            return Err(timed_out());
        }
        let url = format!("{HOST}/catalog/{DIRECTORY}/{year:04}/catalog.xml");
        let response = crate::weather::runtime().map_err(runtime_error)?.block_on(
            ModelClient::shared().fetch(&url, timeout.as_secs_f64(), CATALOG_LIMIT, None, || {
                Ok(Instant::now() >= deadline)
            }),
        );
        let entries = match response {
            Ok(xml) => parse_catalog(&xml, year)?,
            Err(Error::Unavailable(404) | Error::Http(410)) => return Ok(Arc::from([])),
            Err(Error::Cancelled) => return Err(timed_out()),
            Err(error) => return Err(error),
        };
        let mut catalogs = self.catalogs.lock().unwrap();
        catalogs.retain(|cached_year, (fetched, _)| {
            cached_year.abs_diff(year) <= 1 && fetched.elapsed() < CATALOG_TTL
        });
        catalogs.insert(year, (Instant::now(), Arc::clone(&entries)));
        Ok(entries)
    }

    fn index(&self, run: Run) -> Arc<AsyncMutex<ArchiveIndex>> {
        let mut indexes = self.indexes.lock().unwrap();
        let entry = indexes
            .iter()
            .position(|(key, _)| *key == run)
            .and_then(|position| indexes.remove(position))
            .unwrap_or_else(|| (run, Arc::new(AsyncMutex::new(ArchiveIndex::new()))));
        let index = Arc::clone(&entry.1);
        indexes.push_back(entry);
        if indexes.len() > INDEX_CAPACITY {
            indexes.pop_front();
        }
        index
    }

    fn cached_hour(&self, hour: Hour) -> Option<HourBytes> {
        let mut ready = self.ready.lock().unwrap();
        ready.retain(|(_, loaded, _)| loaded.elapsed() < HOUR_TTL);
        let position = ready.iter().position(|(key, _, _)| *key == hour)?;
        let entry = ready.remove(position)?;
        let bytes = Arc::clone(&entry.2);
        ready.push_back(entry);
        Some(bytes)
    }

    fn remember_hour(&self, hour: Hour, bytes: HourBytes) {
        let mut ready = self.ready.lock().unwrap();
        ready.retain(|(key, loaded, _)| *key != hour && loaded.elapsed() < HOUR_TTL);
        let mut stored = ready
            .iter()
            .map(|(_, _, bytes)| bytes.capacity())
            .sum::<usize>();
        while stored + bytes.capacity() > HOUR_CACHE_BYTES {
            let Some((_, _, evicted)) = ready.pop_front() else {
                return;
            };
            stored -= evicted.capacity();
        }
        ready.push_back((hour, Instant::now(), bytes));
    }

    async fn transfer_hour(
        &self,
        hour: Hour,
        timeout: f64,
        stopped: &impl Fn() -> bool,
    ) -> Result<Vec<u8>, Error> {
        let url = hour.run.archive_url();
        let index = self.index(hour.run);
        let client = ModelClient::shared();

        for attempt in 0..2 {
            let mut scan = wait_for(index.lock(), stopped).await?;
            let reused = scan.checked.is_some();
            if scan
                .checked
                .is_none_or(|checked| checked.elapsed() >= INDEX_TTL)
            {
                let identity = client
                    .identity(&url, timeout, || Ok(stopped()))
                    .await
                    .map_err(unpublished)?;
                // Current archives have no strong ETag. An aged unversioned
                // index is rebuilt, and hits are rechecked during transfer.
                if identity.is_none() || scan.identity != identity {
                    *scan = ArchiveIndex::new();
                }
                scan.identity = identity;
                scan.checked = Some(Instant::now());
            }
            let member = match scan.find(hour, &url, timeout, stopped).await {
                Ok(member) => member,
                Err(error) if attempt == 0 && reused && possible_rewrite(&error) => {
                    *scan = ArchiveIndex::new();
                    continue;
                }
                Err(error) => return Err(unpublished(error)),
            };
            let identity = scan.identity.clone();
            let checked = scan.checked;
            drop(scan);

            let end = member.header_at + BLOCK as u64 + member.size as u64 - 1;
            let received = client
                .fetch_version(
                    &url,
                    timeout,
                    BLOCK + member.size,
                    Some((member.header_at, end)),
                    identity.as_ref(),
                    || Ok(stopped()),
                )
                .await
                .and_then(|bytes| {
                    validate_member(&bytes, hour, member)?;
                    Ok(bytes)
                });
            match received {
                Err(error) if attempt == 0 && reused && possible_rewrite(&error) => {
                    let mut scan = wait_for(index.lock(), stopped).await?;
                    if scan.checked == checked {
                        *scan = ArchiveIndex::new();
                    }
                }
                result => return result.map_err(unpublished),
            }
        }
        Err(invalid("archive changed while acquiring a member"))
    }
}

/// Uses the shared Tokio runtime. Call from a synchronous worker with the GIL released.
pub(crate) fn hour_grib(
    model: &str,
    run: OffsetDateTime,
    hour: u32,
    timeout: Duration,
    cancelled: &dyn Fn() -> Result<bool, String>,
) -> Result<Arc<Vec<u8>>, Error> {
    let model = Model::from_key(model).ok_or_else(|| invalid("unknown model key"))?;
    let run = Run::new(model, run).ok_or_else(|| invalid("invalid initialization time"))?;
    if hour > model.last_hour() {
        return Err(invalid("forecast hour outside model schedule"));
    }
    if cancelled().map_err(Error::Invalid)? {
        return Err(Error::Cancelled);
    }
    let deadline = deadline(timeout)?;
    let hour = Hour { run, lead: hour };
    let provider = provider();
    if let Some(bytes) = provider.cached_hour(hour) {
        return Ok(bytes);
    }

    let runtime = crate::weather::runtime().map_err(runtime_error)?;
    let (consumer, producer) = provider.flights.reserve_transient(hour);
    if let Some(producer) = producer {
        runtime.spawn(async move {
            let permit = match wait_for(provider.transfers.acquire(), &|| !producer.shared()).await
            {
                Ok(Ok(permit)) => permit,
                Ok(Err(_)) => {
                    producer.complete_transient(Err(Error::Offline(
                        "NSSL transfer pool closed".into(),
                    )));
                    return;
                }
                Err(error) => {
                    producer.complete_transient(Err(error));
                    return;
                }
            };
            let _task = runtime.spawn_blocking(move || {
                let _permit = permit;
                let stopped = || !producer.shared();
                let result = if let Some(bytes) = provider.cached_hour(hour) {
                    Ok(bytes)
                } else {
                    runtime
                        .block_on(provider.transfer_hour(hour, timeout.as_secs_f64(), &stopped))
                        .and_then(|file| inflate(&file[BLOCK..], hour, &stopped))
                        .map(|mut grib| {
                            grib.shrink_to_fit();
                            let bytes = Arc::new(grib);
                            if !stopped() {
                                provider.remember_hour(hour, Arc::clone(&bytes));
                            }
                            bytes
                        })
                };
                producer.complete_transient(result);
            });
        });
    }

    let wait = consumer.wait(|| cancelled().map(|done| done || Instant::now() >= deadline));
    match wait {
        Ok(result) => result.as_ref().clone(),
        Err(WaitError::Cancelled) if Instant::now() >= deadline => Err(timed_out()),
        Err(WaitError::Cancelled) => Err(Error::Cancelled),
        Err(WaitError::Failed(message)) => Err(Error::Invalid(message)),
        Err(WaitError::Abandoned) => Err(Error::Offline("NSSL transfer stopped".into())),
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Member {
    header_at: u64,
    size: usize,
}

struct ArchiveIndex {
    identity: Option<ObjectIdentity>,
    checked: Option<Instant>,
    next: u64,
    headers: u32,
    members: [Option<Member>; 85],
}

impl ArchiveIndex {
    fn new() -> Self {
        Self {
            identity: None,
            checked: None,
            next: 0,
            headers: 0,
            members: [None; 85],
        }
    }

    async fn find(
        &mut self,
        hour: Hour,
        url: &str,
        timeout: f64,
        stopped: &impl Fn() -> bool,
    ) -> Result<Member, Error> {
        if let Some(member) = self.members[hour.lead as usize] {
            return Ok(member);
        }
        let stamp = hour.run.stamp();
        let prefix = format!("{stamp}/{}_nssl_{stamp}f", hour.run.model.tag());
        loop {
            let start = self.next;
            let limit = if start == 0 {
                PREFIX_BYTES
            } else {
                HEADER_WINDOW_BYTES
            };
            let bytes = ModelClient::shared()
                .fetch_version(
                    url,
                    timeout,
                    limit,
                    Some((start, start + limit as u64 - 1)),
                    self.identity.as_ref(),
                    || Ok(stopped()),
                )
                .await?;
            if let Some(member) = self.scan(hour, &prefix, &stamp, start, &bytes)? {
                return Ok(member);
            }
            if self.next == start {
                return Err(Error::Unavailable(404));
            }
        }
    }

    fn scan(
        &mut self,
        hour: Hour,
        prefix: &str,
        stamp: &str,
        start: u64,
        bytes: &[u8],
    ) -> Result<Option<Member>, Error> {
        while let Some(block) = bytes
            .get((self.next - start) as usize..)
            .and_then(|tail| tail.get(..BLOCK))
        {
            let position = (self.next - start) as usize;
            if block.iter().all(|&byte| byte == 0) {
                if let Some(following) = bytes.get(position + BLOCK..position + 2 * BLOCK) {
                    if following.iter().all(|&byte| byte == 0) {
                        return Err(Error::Unavailable(404));
                    }
                    return Err(invalid("TAR contains a single zero block between headers"));
                }
                return Ok(None);
            }

            let header = tar_header(block)?;
            let member_at = self.next;
            let next = member_at + BLOCK as u64 + header.size.div_ceil(BLOCK) as u64 * BLOCK as u64;
            self.headers += 1;
            if next > MAX_ARCHIVE || self.headers > 2 * (hour.run.model.last_hour() + 1) + 16 {
                return Err(Error::Budget);
            }
            if let Some(lead) = member_lead(&header, prefix, stamp, hour.run.model.last_hour())? {
                let member = Member {
                    header_at: member_at,
                    size: header.size,
                };
                let slot = &mut self.members[lead as usize];
                if slot.is_some() {
                    return Err(invalid("duplicate forecast hour in TAR"));
                }
                *slot = Some(member);
                self.next = next;
                if lead == hour.lead {
                    return Ok(Some(member));
                }
            }
            self.next = next;
        }
        Ok(None)
    }
}

struct TarHeader<'a> {
    name: &'a str,
    size: usize,
    kind: u8,
}

fn tar_header(block: &[u8]) -> Result<TarHeader<'_>, Error> {
    if block.len() != BLOCK || &block[257..262] != b"ustar" {
        return Err(invalid("expected a GNU or POSIX TAR header"));
    }
    if block[262] == 0 && block[345] != 0 {
        return Err(invalid("unexpected USTAR path prefix"));
    }
    let octal = |field: &[u8]| {
        std::str::from_utf8(field)
            .ok()
            .and_then(|text| usize::from_str_radix(text.trim_matches(['\0', ' ']), 8).ok())
    };
    let checksum: usize = block[..148]
        .iter()
        .chain(&block[156..])
        .map(|&byte| usize::from(byte))
        .sum::<usize>()
        + 8 * usize::from(b' ');
    if octal(&block[148..156]) != Some(checksum) {
        return Err(invalid("invalid TAR header checksum"));
    }
    let size = octal(&block[124..136]).ok_or_else(|| invalid("invalid TAR member size"))?;
    if size > MAX_COMPRESSED {
        return Err(Error::Budget);
    }
    let name = std::str::from_utf8(
        block[..100]
            .split(|&byte| byte == 0)
            .next()
            .unwrap_or_default(),
    )
    .map_err(|_| invalid("invalid TAR member name"))?;
    Ok(TarHeader {
        name: name.strip_prefix("./").unwrap_or(name),
        size,
        kind: block[156],
    })
}

fn member_lead(
    header: &TarHeader<'_>,
    prefix: &str,
    stamp: &str,
    last: u32,
) -> Result<Option<u32>, Error> {
    if header.kind == b'5'
        && header.size == 0
        && (header.name.is_empty() || header.name.strip_suffix('/') == Some(stamp))
    {
        return Ok(None);
    }
    if !matches!(header.kind, b'0' | 0) {
        return Err(invalid("unsupported TAR entry type"));
    }
    let tail = header
        .name
        .strip_prefix(prefix)
        .ok_or_else(|| invalid("TAR member belongs to a different run or model"))?;
    let (lead, suffix) = tail
        .split_once('.')
        .ok_or_else(|| invalid("invalid TAR forecast member name"))?;
    if lead.len() != 2 || !lead.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(invalid("invalid TAR forecast hour"));
    }
    let lead = lead
        .parse::<u32>()
        .map_err(|_| invalid("invalid TAR forecast hour"))?;
    if lead > last {
        return Err(invalid("TAR forecast hour outside model schedule"));
    }
    match (suffix, header.size) {
        // This production marker does not carry the forecast.
        ("grib2_ready", 0) => Ok(None),
        ("grib2.gz", size) if size >= 18 => Ok(Some(lead)),
        _ => Err(invalid("invalid TAR forecast entry")),
    }
}

fn validate_member(bytes: &[u8], hour: Hour, member: Member) -> Result<(), Error> {
    if bytes.len() != BLOCK + member.size {
        return Err(invalid("incomplete TAR member"));
    }
    let header = tar_header(&bytes[..BLOCK])?;
    let stamp = hour.run.stamp();
    let prefix = format!("{stamp}/{}_nssl_{stamp}f", hour.run.model.tag());
    if header.size != member.size
        || member_lead(&header, &prefix, &stamp, hour.run.model.last_hour())? != Some(hour.lead)
        || !bytes[BLOCK..].starts_with(&[0x1f, 0x8b, 8])
    {
        return Err(invalid("TAR member no longer matches the forecast index"));
    }
    Ok(())
}

struct CancelReader<'a, F> {
    bytes: &'a [u8],
    stopped: &'a F,
}

impl<F: Fn() -> bool> Read for CancelReader<'_, F> {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        if (self.stopped)() {
            return Err(io::Error::other("forecast transfer cancelled"));
        }
        self.bytes.read(output)
    }
}

fn inflate(gzip: &[u8], hour: Hour, stopped: &impl Fn() -> bool) -> Result<Vec<u8>, Error> {
    let grib = match decompress_gzip_bounded(
        CancelReader {
            bytes: gzip,
            stopped,
        },
        MAX_GRIB,
    ) {
        Ok(bytes) => bytes,
        Err(_) if stopped() => return Err(Error::Cancelled),
        Err(BridgeCacheError::TooLarge) => return Err(Error::Budget),
        Err(error) => return Err(invalid(format!("invalid gzip member: {error:?}"))),
    };
    // Check the trailer at the end of the entire TAR payload against the
    // output of the bounded single-stream decoder.
    let size = gzip
        .last_chunk::<4>()
        .map(|trailer| u32::from_le_bytes(*trailer) as usize);
    if size != Some(grib.len()) {
        return Err(invalid("gzip size does not match the decoded forecast"));
    }
    validate_grib(&grib, hour, stopped)?;
    Ok(grib)
}

fn validate_grib(mut bytes: &[u8], hour: Hour, stopped: &impl Fn() -> bool) -> Result<(), Error> {
    let valid = hour
        .run
        .init
        .checked_add(time::Duration::hours(i64::from(hour.lead)))
        .ok_or_else(|| invalid("forecast valid time out of range"))?;
    let mut saw_forecast = false;
    while !bytes.is_empty() {
        if stopped() {
            return Err(Error::Cancelled);
        }
        let prefix = bytes
            .get(..16)
            .ok_or_else(|| invalid("truncated GRIB2 indicator"))?;
        if !prefix.starts_with(b"GRIB") || prefix[7] != 2 {
            return Err(invalid(
                "member does not contain concatenated GRIB2 messages",
            ));
        }
        let length = u64::from_be_bytes(prefix[8..16].try_into().unwrap());
        let length = usize::try_from(length)
            .ok()
            .filter(|&length| length >= 41 && length <= bytes.len())
            .ok_or_else(|| invalid("invalid GRIB2 message length"))?;
        let (message, rest) = bytes.split_at(length);
        if !message.ends_with(b"7777") {
            return Err(invalid("truncated GRIB2 message"));
        }
        let mut sections = &message[16..length - 4];
        if sections.get(4) != Some(&1) {
            return Err(invalid("missing GRIB2 identification section"));
        }
        while !sections.is_empty() {
            let length = sections
                .get(..4)
                .map(|size| u32::from_be_bytes(size.try_into().unwrap()) as usize)
                .filter(|&length| length >= 5 && length <= sections.len())
                .ok_or_else(|| invalid("invalid GRIB2 section length"))?;
            let (section, remaining) = sections.split_at(length);
            if section[4] == 1 && section.get(12..).and_then(grib_time) != Some(hour.run.init) {
                return Err(invalid("GRIB2 reference time does not match TAR member"));
            }
            if section[4] == 4 {
                let template = section
                    .get(7..9)
                    .map(|value| u16::from_be_bytes(value.try_into().unwrap()));
                match template {
                    Some(0) if section.len() >= 34 => {
                        let step = u32::from_be_bytes(section[18..22].try_into().unwrap());
                        // The NSSL instantaneous fields use hours in template 4.0.
                        if section[17] != 1 || step != hour.lead {
                            return Err(invalid("GRIB2 forecast hour does not match TAR member"));
                        }
                        saw_forecast = true;
                    }
                    Some(8) if section.len() >= 58 => {
                        if grib_time(&section[34..]) != Some(valid) {
                            return Err(invalid("GRIB2 interval end does not match TAR member"));
                        }
                        saw_forecast = true;
                    }
                    Some(0 | 8) | None => {
                        return Err(invalid("truncated GRIB2 product definition"))
                    }
                    _ => {}
                }
            }
            sections = remaining;
        }
        bytes = rest;
    }
    if !saw_forecast {
        return Err(invalid("no forecast records in GRIB2 archive member"));
    }
    Ok(())
}

fn grib_time(bytes: &[u8]) -> Option<OffsetDateTime> {
    let &[hi, lo, month, day, hour, minute, second, ..] = bytes else {
        return None;
    };
    timestamp(
        u16::from_be_bytes([hi, lo]),
        month,
        day,
        hour,
        minute,
        second,
    )
}

fn timestamp(
    year: u16,
    month: u8,
    day: u8,
    hour: u8,
    minute: u8,
    second: u8,
) -> Option<OffsetDateTime> {
    Some(
        Date::from_calendar_date(i32::from(year), Month::try_from(month).ok()?, day)
            .ok()?
            .with_hms(hour, minute, second)
            .ok()?
            .assume_utc(),
    )
}

async fn wait_for<F: Future>(future: F, stopped: &impl Fn() -> bool) -> Result<F::Output, Error> {
    tokio::pin!(future);
    loop {
        if stopped() {
            return Err(Error::Cancelled);
        }
        tokio::select! {
            result = &mut future => return Ok(result),
            _ = tokio::time::sleep(POLL) => {}
        }
    }
}

fn deadline(timeout: Duration) -> Result<Instant, Error> {
    Instant::now()
        .checked_add(timeout)
        .filter(|_| !timeout.is_zero())
        .ok_or_else(|| invalid("invalid timeout"))
}

fn timed_out() -> Error {
    Error::Offline("NSSL archive request timed out".into())
}

fn runtime_error(error: pyo3::PyErr) -> Error {
    Error::Offline(error.to_string())
}

fn invalid(message: impl Into<String>) -> Error {
    Error::Invalid(format!("NSSL MPAS: {}", message.into()))
}

fn possible_rewrite(error: &Error) -> bool {
    matches!(error, Error::Http(412 | 416) | Error::Invalid(_))
}

fn unpublished(error: Error) -> Error {
    match error {
        Error::Http(410) => Error::Unavailable(410),
        Error::Http(416) => Error::Unavailable(404),
        other => other,
    }
}

#[cfg(test)]
mod tests;

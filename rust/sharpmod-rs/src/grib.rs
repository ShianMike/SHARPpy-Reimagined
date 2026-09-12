//! Direct point extraction from GRIB files through a runtime-loaded ecCodes.
//!
//! The optional Python model stack already supplies ecCodes.  Loading that
//! exact shared library at runtime keeps the Rust wheel independent from a
//! build-machine ecCodes installation while preserving ecCodes' grid, packing,
//! and local-table behavior on Windows, Linux, and macOS.

use libloading::Library;
use memchr::memmem;
use memmap2::MmapOptions;
use std::collections::{HashMap, VecDeque};
use std::ffi::{c_char, c_int, c_long, c_ulong, c_void, CStr};
use std::fmt;
use std::fs::{File, Metadata};
use std::marker::PhantomData;
use std::path::{Path, PathBuf};
use std::ptr::{self, NonNull};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

#[cfg(unix)]
use std::os::unix::fs::MetadataExt as _;
#[cfg(windows)]
use std::os::windows::fs::MetadataExt as _;

const COLUMN_COUNT: usize = 9;
const G0: f64 = 9.80665;
const KELVIN_OFFSET: f64 = 273.15;
const MPS_TO_KNOTS: f64 = 1.94384449;
const EARTH_ROTATION_RATE: f64 = 7.2921159e-5;
const MIN_ECCODES_API_VERSION: c_long = 24700;
/// Maximum number of immutable message inventories retained per process.
pub const GRIB_INVENTORY_CACHE_MAX: usize = 8;

const EDITION_KEY: &[u8] = b"edition\0";
const SHORT_NAME_KEY: &[u8] = b"shortName\0";
const TYPE_OF_LEVEL_KEY: &[u8] = b"typeOfLevel\0";
const LEVEL_KEY: &[u8] = b"level\0";
const GRID_HASH_KEY: &[u8] = b"md5GridSection\0";
const VALUES_KEY: &[u8] = b"values\0";
const MISSING_VALUE_KEY: &[u8] = b"missingValue\0";
const NUMBER_OF_POINTS_KEY: &[u8] = b"numberOfPoints\0";
const FIRST_LATITUDE_KEY: &[u8] = b"latitudeOfFirstGridPointInDegrees\0";
const FIRST_LONGITUDE_KEY: &[u8] = b"longitudeOfFirstGridPointInDegrees\0";

static ECCODES_CALL_LOCK: Mutex<()> = Mutex::new(());
static ECCODES_API: OnceLock<EccodesApi> = OnceLock::new();
static GRIB_INVENTORY_CACHE: OnceLock<Mutex<InventoryCache>> = OnceLock::new();

/// Error returned by GRIB boundary parsing, dynamic loading, or ecCodes calls.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GribError(String);

impl GribError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for GribError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for GribError {}

/// One decoded point sounding ready to transfer as one NumPy matrix.
#[derive(Clone, Debug, PartialEq)]
pub struct DecodedPoint {
    /// Field-major values in `(9, level_count)` C order.
    pub matrix: Vec<f64>,
    pub level_count: usize,
    pub selected_latitude: f64,
    pub selected_longitude: f64,
    pub surface_relative_vorticity: Option<f64>,
    pub surface_merged: bool,
    pub below_ground_levels_removed: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct MessageBoundary {
    offset: usize,
    length: usize,
    edition: u8,
}

/// Observable state for the native message-inventory cache.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct GribInventoryCacheInfo {
    pub enabled: bool,
    pub size: usize,
    pub max_size: usize,
    pub hits: u64,
    pub misses: u64,
    pub evictions: u64,
    pub invalidations: u64,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct FileIdentity {
    canonical_path: PathBuf,
    size: u64,
    #[cfg(windows)]
    creation_time: u64,
    #[cfg(windows)]
    last_write_time: u64,
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(unix)]
    modified_seconds: i64,
    #[cfg(unix)]
    modified_nanoseconds: i64,
    #[cfg(unix)]
    changed_seconds: i64,
    #[cfg(unix)]
    changed_nanoseconds: i64,
}

impl FileIdentity {
    /// Build a cache key from the opened file's metadata rather than a second
    /// path lookup. This keeps atomic replacements distinct from the handle
    /// actually mapped for this decode. If canonicalization is unavailable,
    /// decoding continues without caching so cache identity never becomes an
    /// additional failure mode.
    fn from_open_file(path: &Path, metadata: &Metadata) -> Option<Self> {
        let canonical_path = path.canonicalize().ok()?;
        Some(Self {
            canonical_path,
            size: metadata.len(),
            #[cfg(windows)]
            creation_time: metadata.creation_time(),
            #[cfg(windows)]
            last_write_time: metadata.last_write_time(),
            #[cfg(unix)]
            device: metadata.dev(),
            #[cfg(unix)]
            inode: metadata.ino(),
            #[cfg(unix)]
            modified_seconds: metadata.mtime(),
            #[cfg(unix)]
            modified_nanoseconds: metadata.mtime_nsec(),
            #[cfg(unix)]
            changed_seconds: metadata.ctime(),
            #[cfg(unix)]
            changed_nanoseconds: metadata.ctime_nsec(),
        })
    }
}

#[derive(Clone, Debug)]
struct InventoryField {
    field_index: usize,
    order: usize,
    pressure_field: Option<(f64, FieldKind)>,
    surface_field: Option<SurfaceFieldKind>,
    grid_key: String,
    missing_value: Option<f64>,
}

#[derive(Clone, Debug)]
struct InventoryMessage {
    boundary: MessageBoundary,
    fields: Vec<InventoryField>,
}

#[derive(Clone, Debug)]
struct GribInventory {
    messages: Vec<InventoryMessage>,
}

impl GribInventory {
    fn matches_mapping(&self, bytes: &[u8]) -> bool {
        self.messages.iter().all(|message| {
            let boundary = message.boundary;
            let Some(end) = boundary.offset.checked_add(boundary.length) else {
                return false;
            };
            if end > bytes.len()
                || bytes.get(boundary.offset..boundary.offset + 4) != Some(b"GRIB")
                || bytes.get(boundary.offset + 7) != Some(&boundary.edition)
                || bytes.get(end.saturating_sub(4)..end) != Some(b"7777")
            {
                return false;
            }
            let encoded_length = match boundary.edition {
                1 => bytes
                    .get(boundary.offset + 4..boundary.offset + 7)
                    .map(read_be_u24),
                2 => bytes
                    .get(boundary.offset + 8..boundary.offset + 16)
                    .and_then(|value| read_be_u64(value).ok()),
                _ => None,
            };
            encoded_length == Some(boundary.length)
        })
    }
}

#[derive(Debug)]
struct InventoryCache {
    enabled: bool,
    entries: VecDeque<(FileIdentity, Arc<GribInventory>)>,
    hits: u64,
    misses: u64,
    evictions: u64,
    invalidations: u64,
}

impl Default for InventoryCache {
    fn default() -> Self {
        Self {
            enabled: true,
            entries: VecDeque::new(),
            hits: 0,
            misses: 0,
            evictions: 0,
            invalidations: 0,
        }
    }
}

impl InventoryCache {
    fn lookup(&mut self, key: &FileIdentity) -> Option<Arc<GribInventory>> {
        if !self.enabled {
            return None;
        }
        let Some(position) = self
            .entries
            .iter()
            .position(|(candidate, _)| candidate == key)
        else {
            self.misses = self.misses.saturating_add(1);
            return None;
        };
        let entry = self
            .entries
            .remove(position)
            .expect("the inventory entry position came from this deque");
        let inventory = Arc::clone(&entry.1);
        self.entries.push_back(entry);
        self.hits = self.hits.saturating_add(1);
        Some(inventory)
    }

    fn insert(&mut self, key: FileIdentity, inventory: Arc<GribInventory>) {
        if !self.enabled {
            return;
        }
        if let Some(position) = self
            .entries
            .iter()
            .position(|(candidate, _)| candidate == &key)
        {
            self.entries.remove(position);
        }

        let old_len = self.entries.len();
        self.entries
            .retain(|(candidate, _)| candidate.canonical_path != key.canonical_path);
        self.invalidations = self
            .invalidations
            .saturating_add((old_len - self.entries.len()) as u64);
        self.entries.push_back((key, inventory));
        while self.entries.len() > GRIB_INVENTORY_CACHE_MAX {
            self.entries.pop_front();
            self.evictions = self.evictions.saturating_add(1);
        }
    }

    fn invalidate(&mut self, key: &FileIdentity) {
        let old_len = self.entries.len();
        self.entries.retain(|(candidate, _)| candidate != key);
        self.invalidations = self
            .invalidations
            .saturating_add((old_len - self.entries.len()) as u64);
    }

    fn clear(&mut self, reset_stats: bool) {
        self.entries.clear();
        if reset_stats {
            self.hits = 0;
            self.misses = 0;
            self.evictions = 0;
            self.invalidations = 0;
        }
    }

    fn info(&self) -> GribInventoryCacheInfo {
        GribInventoryCacheInfo {
            enabled: self.enabled,
            size: self.entries.len(),
            max_size: GRIB_INVENTORY_CACHE_MAX,
            hits: self.hits,
            misses: self.misses,
            evictions: self.evictions,
            invalidations: self.invalidations,
        }
    }
}

fn inventory_cache() -> MutexGuard<'static, InventoryCache> {
    GRIB_INVENTORY_CACHE
        .get_or_init(|| Mutex::new(InventoryCache::default()))
        .lock()
        // A cache is only an optimization. Recovering its value avoids turning
        // an unrelated prior panic into a permanent decoder failure.
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Enable or bypass native GRIB inventory reuse for subsequent decodes.
///
/// Disabling does not destroy retained entries; callers that require a fully
/// cold state should call [`clear_grib_inventory_cache`] as well.
pub fn set_grib_inventory_cache_enabled(enabled: bool) {
    inventory_cache().enabled = enabled;
}

/// Drop every retained immutable inventory, optionally resetting counters.
pub fn clear_grib_inventory_cache(reset_stats: bool) {
    inventory_cache().clear(reset_stats);
}

/// Return deterministic native inventory-cache counters for adapters/tests.
pub fn grib_inventory_cache_info() -> GribInventoryCacheInfo {
    inventory_cache().info()
}

fn read_be_u24(bytes: &[u8]) -> usize {
    (usize::from(bytes[0]) << 16) | (usize::from(bytes[1]) << 8) | usize::from(bytes[2])
}

fn read_be_u64(bytes: &[u8]) -> Result<usize, GribError> {
    let encoded = u64::from_be_bytes(
        bytes
            .try_into()
            .map_err(|_| GribError::new("invalid GRIB2 length field"))?,
    );
    usize::try_from(encoded).map_err(|_| GribError::new("GRIB2 message length exceeds usize"))
}

/// Locate complete GRIB1/GRIB2 messages without copying the mapped file.
fn scan_message_boundaries(bytes: &[u8]) -> Result<Vec<MessageBoundary>, GribError> {
    let mut boundaries = Vec::new();
    let mut cursor = 0;

    while cursor < bytes.len() {
        let Some(relative) = memmem::find(&bytes[cursor..], b"GRIB") else {
            break;
        };
        let offset = cursor + relative;
        if bytes.len().saturating_sub(offset) < 8 {
            return Err(GribError::new(format!(
                "truncated GRIB indicator at byte {offset}"
            )));
        }

        let edition = bytes[offset + 7];
        let length = match edition {
            1 => read_be_u24(&bytes[offset + 4..offset + 7]),
            2 => {
                if bytes.len().saturating_sub(offset) < 16 {
                    return Err(GribError::new(format!(
                        "truncated GRIB2 indicator at byte {offset}"
                    )));
                }
                read_be_u64(&bytes[offset + 8..offset + 16])?
            }
            value => {
                return Err(GribError::new(format!(
                    "unsupported GRIB edition {value} at byte {offset}"
                )))
            }
        };

        let minimum_length = if edition == 1 { 12 } else { 20 };
        if length < minimum_length {
            return Err(GribError::new(format!(
                "invalid GRIB{edition} message length {length} at byte {offset}"
            )));
        }
        let end = offset.checked_add(length).ok_or_else(|| {
            GribError::new(format!("GRIB message length overflows at byte {offset}"))
        })?;
        if end > bytes.len() {
            return Err(GribError::new(format!(
                "truncated GRIB{edition} message at byte {offset}: expected {length} bytes, found {}",
                bytes.len() - offset
            )));
        }
        if &bytes[end - 4..end] != b"7777" {
            return Err(GribError::new(format!(
                "corrupt GRIB{edition} message at byte {offset}: missing 7777 terminator"
            )));
        }

        boundaries.push(MessageBoundary {
            offset,
            length,
            edition,
        });
        cursor = end;
    }

    if boundaries.is_empty() {
        Err(GribError::new("no complete GRIB messages were found"))
    } else {
        Ok(boundaries)
    }
}

type HandleNewFromMultiMessage =
    unsafe extern "C" fn(*mut c_void, *mut *mut c_void, *mut usize, *mut c_int) -> *mut c_void;
type MultiSupport = unsafe extern "C" fn(*mut c_void);
type HandleDelete = unsafe extern "C" fn(*mut c_void) -> c_int;
type GetLong = unsafe extern "C" fn(*const c_void, *const c_char, *mut c_long) -> c_int;
type GetDouble = unsafe extern "C" fn(*const c_void, *const c_char, *mut f64) -> c_int;
type GetLength = unsafe extern "C" fn(*const c_void, *const c_char, *mut usize) -> c_int;
type GetString =
    unsafe extern "C" fn(*const c_void, *const c_char, *mut c_char, *mut usize) -> c_int;
type GetDoubleElement =
    unsafe extern "C" fn(*const c_void, *const c_char, c_int, *mut f64) -> c_int;
type GetDoubleElements =
    unsafe extern "C" fn(*const c_void, *const c_char, *const c_int, c_long, *mut f64) -> c_int;
type NearestNew = unsafe extern "C" fn(*const c_void, *mut c_int) -> *mut c_void;
type NearestFind = unsafe extern "C" fn(
    *mut c_void,
    *const c_void,
    f64,
    f64,
    c_ulong,
    *mut f64,
    *mut f64,
    *mut f64,
    *mut f64,
    *mut c_int,
    *mut usize,
) -> c_int;
type NearestDelete = unsafe extern "C" fn(*mut c_void) -> c_int;
type GetErrorMessage = unsafe extern "C" fn(c_int) -> *const c_char;
type GetApiVersion = unsafe extern "C" fn() -> c_long;

struct EccodesApi {
    _library: Library,
    library_path: PathBuf,
    handle_new_from_multi_message: HandleNewFromMultiMessage,
    multi_support_on: MultiSupport,
    multi_support_off: MultiSupport,
    handle_delete: HandleDelete,
    get_long: GetLong,
    get_double: GetDouble,
    get_length: GetLength,
    get_string: GetString,
    get_double_element: GetDoubleElement,
    get_double_elements: GetDoubleElements,
    nearest_new: NearestNew,
    nearest_find: NearestFind,
    nearest_delete: NearestDelete,
    get_error_message: GetErrorMessage,
}

impl EccodesApi {
    fn cached(path: &Path) -> Result<&'static Self, GribError> {
        if let Some(api) = ECCODES_API.get() {
            return api.for_path(path);
        }

        let loaded = Self::load(path)?;
        let _ = ECCODES_API.set(loaded);
        ECCODES_API
            .get()
            .expect("ecCodes API was initialized")
            .for_path(path)
    }

    fn for_path(&self, path: &Path) -> Result<&Self, GribError> {
        if self.library_path == path {
            Ok(self)
        } else {
            Err(GribError::new(format!(
                "ecCodes is already loaded from {}; refusing a second runtime at {}",
                self.library_path.display(),
                path.display()
            )))
        }
    }

    fn load(path: &Path) -> Result<Self, GribError> {
        if !path.is_absolute() {
            return Err(GribError::new(format!(
                "ecCodes library path must be absolute: {}",
                path.display()
            )));
        }
        if !path.is_file() {
            return Err(GribError::new(format!(
                "ecCodes library does not exist: {}",
                path.display()
            )));
        }

        // SAFETY: the caller supplies the exact ecCodes library already loaded
        // and verified by the Python eccodes package. The Library is retained in
        // this struct for at least as long as every copied function pointer.
        let library = unsafe { Library::new(path) }.map_err(|error| {
            GribError::new(format!(
                "could not load ecCodes library {}: {error}",
                path.display()
            ))
        })?;

        // SAFETY: all symbol types below match the documented ecCodes C API.
        unsafe {
            let get_api_version: GetApiVersion = load_symbol(&library, b"codes_get_api_version\0")?;
            let version = get_api_version();
            if version < MIN_ECCODES_API_VERSION {
                return Err(GribError::new(format!(
                    "ecCodes {version} is too old; API {MIN_ECCODES_API_VERSION} or newer is required"
                )));
            }

            Ok(Self {
                handle_new_from_multi_message: load_symbol(
                    &library,
                    b"codes_grib_handle_new_from_multi_message\0",
                )?,
                multi_support_on: load_symbol(&library, b"codes_grib_multi_support_on\0")?,
                multi_support_off: load_symbol(&library, b"codes_grib_multi_support_off\0")?,
                handle_delete: load_symbol(&library, b"codes_handle_delete\0")?,
                get_long: load_symbol(&library, b"codes_get_long\0")?,
                get_double: load_symbol(&library, b"codes_get_double\0")?,
                get_length: load_symbol(&library, b"codes_get_length\0")?,
                get_string: load_symbol(&library, b"codes_get_string\0")?,
                get_double_element: load_symbol(&library, b"codes_get_double_element\0")?,
                get_double_elements: load_symbol(&library, b"codes_get_double_elements\0")?,
                nearest_new: load_symbol(&library, b"codes_grib_nearest_new\0")?,
                nearest_find: load_symbol(&library, b"codes_grib_nearest_find\0")?,
                nearest_delete: load_symbol(&library, b"codes_grib_nearest_delete\0")?,
                get_error_message: load_symbol(&library, b"codes_get_error_message\0")?,
                library_path: path.to_path_buf(),
                _library: library,
            })
        }
    }

    fn error_message(&self, code: c_int) -> String {
        // SAFETY: ecCodes returns either a static NUL-terminated string or NULL.
        let pointer = unsafe { (self.get_error_message)(code) };
        if pointer.is_null() {
            format!("ecCodes error {code}")
        } else {
            // SAFETY: a non-null ecCodes error string is NUL terminated.
            unsafe { CStr::from_ptr(pointer) }
                .to_string_lossy()
                .into_owned()
        }
    }

    fn check(&self, code: c_int, operation: &str) -> Result<(), GribError> {
        if code == 0 {
            Ok(())
        } else {
            Err(GribError::new(format!(
                "{operation}: {}",
                self.error_message(code)
            )))
        }
    }

    fn enable_multi_support(&self) -> MultiSupportGuard<'_> {
        // SAFETY: NULL selects the default context. The caller holds the
        // process-wide ecCodes mutex for the guard's complete lifetime.
        unsafe { (self.multi_support_on)(ptr::null_mut()) };
        MultiSupportGuard { api: self }
    }

    fn next_multi_handle<'api, 'message>(
        &'api self,
        _message: &'message [u8],
        cursor: &mut *mut c_void,
        remaining: &mut usize,
    ) -> Result<Option<CodesHandle<'api, 'message>>, GribError> {
        let mut error = 0;
        // SAFETY: cursor initially points into `message`, remaining is that
        // slice's length, and both remain writable across calls. With multi
        // support enabled ecCodes may return additional field handles after
        // remaining reaches zero, so callers iterate until NULL. This decoder
        // uses getters only and keeps the mmap alive until every handle drops.
        let pointer = unsafe {
            (self.handle_new_from_multi_message)(ptr::null_mut(), cursor, remaining, &mut error)
        };
        let Some(pointer) = NonNull::new(pointer) else {
            if error == 0 {
                return Ok(None);
            }
            self.check(error, "codes_grib_handle_new_from_multi_message")?;
            unreachable!("a nonzero ecCodes error cannot pass check")
        };
        Ok(Some(CodesHandle {
            api: self,
            pointer,
            _message: PhantomData,
        }))
    }

    fn get_long_value(&self, handle: *const c_void, key: &[u8]) -> Result<c_long, GribError> {
        let mut value = 0;
        // SAFETY: handle is live, key is NUL terminated, and value is writable.
        let code = unsafe { (self.get_long)(handle, key.as_ptr().cast(), &mut value) };
        self.check(code, "codes_get_long")?;
        Ok(value)
    }

    fn get_double_value(&self, handle: *const c_void, key: &[u8]) -> Result<f64, GribError> {
        let mut value = 0.0;
        // SAFETY: handle is live, key is NUL terminated, and value is writable.
        let code = unsafe { (self.get_double)(handle, key.as_ptr().cast(), &mut value) };
        self.check(code, "codes_get_double")?;
        Ok(value)
    }

    fn get_optional_double(&self, handle: *const c_void, key: &[u8]) -> Option<f64> {
        let mut value = 0.0;
        // SAFETY: handle is live, key is NUL terminated, and value is writable.
        let code = unsafe { (self.get_double)(handle, key.as_ptr().cast(), &mut value) };
        (code == 0).then_some(value)
    }

    fn get_string_value(&self, handle: *const c_void, key: &[u8]) -> Result<String, GribError> {
        let mut length = 0;
        // SAFETY: handle is live, key is NUL terminated, and length is writable.
        let code = unsafe { (self.get_length)(handle, key.as_ptr().cast(), &mut length) };
        self.check(code, "codes_get_length")?;

        let mut buffer = vec![0_u8; length.saturating_add(1).max(2)];
        let mut capacity = buffer.len();
        // SAFETY: the buffer has `capacity` writable bytes and the other
        // pointers remain valid for the duration of the call.
        let code = unsafe {
            (self.get_string)(
                handle,
                key.as_ptr().cast(),
                buffer.as_mut_ptr().cast(),
                &mut capacity,
            )
        };
        self.check(code, "codes_get_string")?;
        let end = buffer
            .iter()
            .position(|byte| *byte == 0)
            .unwrap_or(buffer.len());
        Ok(String::from_utf8_lossy(&buffer[..end]).into_owned())
    }

    fn get_optional_string(&self, handle: *const c_void, key: &[u8]) -> Option<String> {
        self.get_string_value(handle, key).ok()
    }

    fn get_element(&self, handle: *const c_void, index: c_int) -> Result<f64, GribError> {
        let mut value = 0.0;
        // SAFETY: handle is live, VALUES_KEY is NUL terminated, index came
        // from ecCodes nearest-point lookup, and value is writable.
        let code = unsafe {
            (self.get_double_element)(handle, VALUES_KEY.as_ptr().cast(), index, &mut value)
        };
        self.check(code, "codes_get_double_element")?;
        Ok(value)
    }

    fn get_elements(
        &self,
        handle: *const c_void,
        indexes: &[c_int],
    ) -> Result<Vec<f64>, GribError> {
        if indexes.len() == 1 {
            return self
                .get_element(handle, indexes[0])
                .map(|value| vec![value]);
        }
        let length = c_long::try_from(indexes.len())
            .map_err(|_| GribError::new("too many GRIB point indexes for ecCodes"))?;
        let mut values = vec![0.0; indexes.len()];
        // SAFETY: handle is live, VALUES_KEY is NUL terminated, indexes and
        // values both contain `length` readable/writable elements, and indexes
        // came directly from ecCodes nearest-point lookups.
        let code = unsafe {
            (self.get_double_elements)(
                handle,
                VALUES_KEY.as_ptr().cast(),
                indexes.as_ptr(),
                length,
                values.as_mut_ptr(),
            )
        };
        self.check(code, "codes_get_double_elements")?;
        Ok(values)
    }

    fn find_nearest(
        &self,
        handle: *const c_void,
        latitude: f64,
        longitude: f64,
    ) -> Result<GridPoint, GribError> {
        let mut error = 0;
        // SAFETY: handle is live and error is writable.
        let pointer = unsafe { (self.nearest_new)(handle, &mut error) };
        self.check(error, "codes_grib_nearest_new")?;
        let pointer = NonNull::new(pointer)
            .ok_or_else(|| GribError::new("codes_grib_nearest_new returned NULL"))?;
        let nearest = CodesNearest { api: self, pointer };

        let mut latitudes = [0.0; 4];
        let mut longitudes = [0.0; 4];
        let mut values = [0.0; 4];
        let mut distances = [0.0; 4];
        let mut indexes = [0; 4];
        let mut length = 4;
        // SAFETY: nearest and handle are live; every output buffer has four
        // elements and length advertises that capacity.
        let code = unsafe {
            (self.nearest_find)(
                nearest.pointer.as_ptr(),
                handle,
                latitude,
                longitude,
                0,
                latitudes.as_mut_ptr(),
                longitudes.as_mut_ptr(),
                values.as_mut_ptr(),
                distances.as_mut_ptr(),
                indexes.as_mut_ptr(),
                &mut length,
            )
        };
        self.check(code, "codes_grib_nearest_find")?;
        if length == 0 || length > 4 {
            return Err(GribError::new(format!(
                "ecCodes nearest lookup returned invalid length {length}"
            )));
        }

        let best = (0..length)
            .filter(|index| distances[*index].is_finite())
            .min_by(|left, right| {
                distances[*left]
                    .total_cmp(&distances[*right])
                    .then_with(|| indexes[*left].cmp(&indexes[*right]))
            })
            .ok_or_else(|| GribError::new("ecCodes nearest lookup returned no finite point"))?;

        Ok(GridPoint {
            index: indexes[best],
            latitude: latitudes[best],
            longitude: normalize_longitude(longitudes[best]),
        })
    }
}

unsafe fn load_symbol<T: Copy>(library: &Library, name: &[u8]) -> Result<T, GribError> {
    // SAFETY: callers provide the exact C function type for each documented
    // ecCodes symbol and retain `library` for every copied pointer's lifetime.
    unsafe { library.get::<T>(name) }
        .map(|symbol| *symbol)
        .map_err(|error| {
            let symbol_name = String::from_utf8_lossy(&name[..name.len().saturating_sub(1)]);
            GribError::new(format!("ecCodes is missing {symbol_name}: {error}"))
        })
}

struct MultiSupportGuard<'api> {
    api: &'api EccodesApi,
}

impl Drop for MultiSupportGuard<'_> {
    fn drop(&mut self) {
        // SAFETY: NULL selects the same default context enabled when this
        // guard was constructed, while the global ecCodes mutex is held.
        unsafe { (self.api.multi_support_off)(ptr::null_mut()) };
    }
}

struct CodesHandle<'api, 'message> {
    api: &'api EccodesApi,
    pointer: NonNull<c_void>,
    _message: PhantomData<&'message [u8]>,
}

impl CodesHandle<'_, '_> {
    fn as_ptr(&self) -> *const c_void {
        self.pointer.as_ptr()
    }
}

impl Drop for CodesHandle<'_, '_> {
    fn drop(&mut self) {
        // SAFETY: this handle is uniquely owned and deleted exactly once.
        let _ = unsafe { (self.api.handle_delete)(self.pointer.as_ptr()) };
    }
}

struct CodesNearest<'api> {
    api: &'api EccodesApi,
    pointer: NonNull<c_void>,
}

impl Drop for CodesNearest<'_> {
    fn drop(&mut self) {
        // SAFETY: this nearest object is uniquely owned and deleted once.
        let _ = unsafe { (self.api.nearest_delete)(self.pointer.as_ptr()) };
    }
}

#[derive(Clone, Copy, Debug)]
struct GridPoint {
    index: c_int,
    latitude: f64,
    longitude: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FieldKind {
    Temperature,
    GeopotentialHeight,
    Geopotential,
    RelativeHumidity,
    SpecificHumidity,
    UWind,
    VWind,
    Omega,
    RelativeVorticity,
    AbsoluteVorticity,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SurfaceFieldKind {
    Pressure,
    Height,
    Geopotential,
    Temperature,
    Dewpoint,
    RelativeHumidity,
    SpecificHumidity,
    UWind,
    VWind,
}

fn classify_field(short_name: &str) -> Option<FieldKind> {
    match short_name.trim().to_ascii_lowercase().as_str() {
        "t" | "tmp" | "temperature" => Some(FieldKind::Temperature),
        "gh" | "hgt" | "geopotential_height" => Some(FieldKind::GeopotentialHeight),
        "z" | "geopotential" => Some(FieldKind::Geopotential),
        "r" | "rh" | "relative_humidity" => Some(FieldKind::RelativeHumidity),
        "q" | "spfh" | "specific_humidity" => Some(FieldKind::SpecificHumidity),
        "u" | "ugrd" | "u_component_of_wind" => Some(FieldKind::UWind),
        "v" | "vgrd" | "v_component_of_wind" => Some(FieldKind::VWind),
        "w" | "vvel" | "dzdt" | "vertical_velocity" => Some(FieldKind::Omega),
        "vo" | "vort" | "relv" | "relative_vorticity" => Some(FieldKind::RelativeVorticity),
        "absv" | "absolute_vorticity" => Some(FieldKind::AbsoluteVorticity),
        _ => None,
    }
}

fn pressure_hpa(type_of_level: &str, level: f64) -> Option<f64> {
    if !level.is_finite() {
        return None;
    }
    let pressure = match type_of_level.trim().to_ascii_lowercase().as_str() {
        "isobaricinhpa" => level,
        "isobaricinpa" => level / 100.0,
        _ => return None,
    };
    (pressure > 0.0).then_some(pressure)
}

fn classify_surface_field(
    short_name: &str,
    type_of_level: &str,
    level: f64,
) -> Option<SurfaceFieldKind> {
    if !level.is_finite() {
        return None;
    }
    let short_name = short_name.trim().to_ascii_lowercase();
    let level_type = type_of_level.trim().to_ascii_lowercase();
    if level_type == "surface" && level == 0.0 {
        return match short_name.as_str() {
            "sp" | "pres" => Some(SurfaceFieldKind::Pressure),
            "z" | "geopotential" => Some(SurfaceFieldKind::Geopotential),
            "orog" | "gh" | "hgt" | "geopotential_height" => Some(SurfaceFieldKind::Height),
            _ => None,
        };
    }
    if level_type == "heightaboveground" && level == 2.0 {
        return match short_name.as_str() {
            "2t" | "t2m" | "tmp" | "t" | "temperature" => Some(SurfaceFieldKind::Temperature),
            "2d" | "d2m" | "dpt" | "dewpoint" => Some(SurfaceFieldKind::Dewpoint),
            "2r" | "rh2m" | "r" | "rh" | "relative_humidity" => {
                Some(SurfaceFieldKind::RelativeHumidity)
            }
            "2sh" | "sh2" | "q" | "spfh" | "specific_humidity" => {
                Some(SurfaceFieldKind::SpecificHumidity)
            }
            _ => None,
        };
    }
    if level_type == "heightaboveground" && level == 10.0 {
        return match short_name.as_str() {
            "10u" | "u10" | "ugrd" | "u" => Some(SurfaceFieldKind::UWind),
            "10v" | "v10" | "vgrd" | "v" => Some(SurfaceFieldKind::VWind),
            _ => None,
        };
    }
    None
}

#[derive(Clone, Debug)]
struct LevelRecord {
    pressure: f64,
    first_order: usize,
    temperature: Option<f64>,
    geopotential_height: Option<f64>,
    geopotential: Option<f64>,
    relative_humidity: Option<f64>,
    specific_humidity: Option<f64>,
    u_wind: Option<f64>,
    v_wind: Option<f64>,
    omega: Option<f64>,
    relative_vorticity: Option<f64>,
    absolute_vorticity: Option<f64>,
}

impl LevelRecord {
    fn new(pressure: f64, first_order: usize) -> Self {
        Self {
            pressure,
            first_order,
            temperature: None,
            geopotential_height: None,
            geopotential: None,
            relative_humidity: None,
            specific_humidity: None,
            u_wind: None,
            v_wind: None,
            omega: None,
            relative_vorticity: None,
            absolute_vorticity: None,
        }
    }

    fn insert(&mut self, kind: FieldKind, value: f64) {
        let slot = match kind {
            FieldKind::Temperature => &mut self.temperature,
            FieldKind::GeopotentialHeight => &mut self.geopotential_height,
            FieldKind::Geopotential => &mut self.geopotential,
            FieldKind::RelativeHumidity => &mut self.relative_humidity,
            FieldKind::SpecificHumidity => &mut self.specific_humidity,
            FieldKind::UWind => &mut self.u_wind,
            FieldKind::VWind => &mut self.v_wind,
            FieldKind::Omega => &mut self.omega,
            FieldKind::RelativeVorticity => &mut self.relative_vorticity,
            FieldKind::AbsoluteVorticity => &mut self.absolute_vorticity,
        };
        if slot.is_none() {
            *slot = Some(value);
        }
    }
}

#[derive(Default)]
struct RecordAssembler {
    levels: Vec<LevelRecord>,
    saw_geopotential_height: bool,
    saw_geopotential: bool,
    saw_temperature: bool,
    saw_relative_humidity: bool,
    saw_specific_humidity: bool,
    saw_u_wind: bool,
    saw_v_wind: bool,
    surface_pressure: Option<f64>,
    surface_height: Option<f64>,
    surface_geopotential: Option<f64>,
    surface_temperature: Option<f64>,
    surface_dewpoint: Option<f64>,
    surface_relative_humidity: Option<f64>,
    surface_specific_humidity: Option<f64>,
    surface_u_wind: Option<f64>,
    surface_v_wind: Option<f64>,
}

impl RecordAssembler {
    fn insert(&mut self, pressure: f64, kind: FieldKind, value: f64, order: usize) {
        match kind {
            FieldKind::GeopotentialHeight => self.saw_geopotential_height = true,
            FieldKind::Geopotential => self.saw_geopotential = true,
            FieldKind::Temperature => self.saw_temperature = true,
            FieldKind::RelativeHumidity => self.saw_relative_humidity = true,
            FieldKind::SpecificHumidity => self.saw_specific_humidity = true,
            FieldKind::UWind => self.saw_u_wind = true,
            FieldKind::VWind => self.saw_v_wind = true,
            _ => {}
        }

        let position = self
            .levels
            .iter()
            .position(|record| record.pressure == pressure);
        let record = if let Some(position) = position {
            &mut self.levels[position]
        } else {
            self.levels.push(LevelRecord::new(pressure, order));
            self.levels.last_mut().expect("a level was just inserted")
        };
        record.insert(kind, value);
    }

    fn insert_surface(&mut self, kind: SurfaceFieldKind, value: f64) {
        let slot = match kind {
            SurfaceFieldKind::Pressure => &mut self.surface_pressure,
            SurfaceFieldKind::Height => &mut self.surface_height,
            SurfaceFieldKind::Geopotential => &mut self.surface_geopotential,
            SurfaceFieldKind::Temperature => &mut self.surface_temperature,
            SurfaceFieldKind::Dewpoint => &mut self.surface_dewpoint,
            SurfaceFieldKind::RelativeHumidity => &mut self.surface_relative_humidity,
            SurfaceFieldKind::SpecificHumidity => &mut self.surface_specific_humidity,
            SurfaceFieldKind::UWind => &mut self.surface_u_wind,
            SurfaceFieldKind::VWind => &mut self.surface_v_wind,
        };
        if slot.is_none() {
            *slot = Some(value);
        }
    }

    fn assemble(
        mut self,
        selected: GridPoint,
        missing: Option<f64>,
    ) -> Result<DecodedPoint, GribError> {
        let mut missing_fields = Vec::new();
        if !self.saw_geopotential_height && !self.saw_geopotential {
            missing_fields.push("height");
        }
        if !self.saw_temperature {
            missing_fields.push("temperature");
        }
        if !self.saw_relative_humidity && !self.saw_specific_humidity {
            missing_fields.push("moisture");
        }
        if !self.saw_u_wind {
            missing_fields.push("u wind");
        }
        if !self.saw_v_wind {
            missing_fields.push("v wind");
        }
        if !missing_fields.is_empty() {
            return Err(GribError::new(format!(
                "missing required pressure-level fields: {}",
                missing_fields.join(", ")
            )));
        }

        self.levels.sort_by(|left, right| {
            right
                .pressure
                .total_cmp(&left.pressure)
                .then_with(|| left.first_order.cmp(&right.first_order))
        });
        self.levels
            .dedup_by(|left, right| left.pressure == right.pressure);
        if self.levels.is_empty() {
            return Err(GribError::new(
                "no pressure-level sounding fields were found",
            ));
        }

        let output_missing = missing
            .filter(|value| value.is_finite())
            .unwrap_or(f64::NAN);
        let surface_pressure = usable(self.surface_pressure, missing)
            .map(|value| if value > 2000.0 { value / 100.0 } else { value })
            .filter(|value| *value > 0.0);
        let surface_height = usable(self.surface_height, missing)
            .or_else(|| usable(self.surface_geopotential, missing).map(|value| value / G0));
        let surface_temperature = usable(self.surface_temperature, missing);
        let surface_dewpoint = surface_pressure
            .and_then(|pressure| {
                usable(self.surface_specific_humidity, missing)
                    .map(|humidity| dewpoint_from_specific_humidity(humidity, pressure))
            })
            .or_else(|| usable(self.surface_dewpoint, missing).map(|value| value - KELVIN_OFFSET))
            .or_else(|| {
                surface_temperature.and_then(|temperature| {
                    usable(self.surface_relative_humidity, missing)
                        .map(|humidity| dewpoint_from_rh(temperature - KELVIN_OFFSET, humidity))
                })
            });
        let surface_u = usable(self.surface_u_wind, missing);
        let surface_v = usable(self.surface_v_wind, missing);
        let surface = surface_pressure
            .zip(surface_height)
            .zip(surface_temperature.map(|value| value - KELVIN_OFFSET))
            .zip(surface_dewpoint)
            .zip(surface_u)
            .zip(surface_v)
            .map(|(((((pressure, height), temperature), dewpoint), u), v)| {
                (pressure, height, temperature, dewpoint, u, v)
            })
            .filter(|(pressure, height, temperature, dewpoint, u, v)| {
                (100.0..=1100.0).contains(pressure)
                    && (-1000.0..=10_000.0).contains(height)
                    && (-120.0..=70.0).contains(temperature)
                    && (-150.0..=*temperature + 1.0e-6).contains(dewpoint)
                    && u.abs() <= 200.0
                    && v.abs() <= 200.0
            });
        // Preserve a vorticity sample that is exactly coincident with the
        // verified ground. The profile row at that pressure is replaced by
        // the 2 m/10 m merge, but the pressure-level vorticity is still the
        // closest valid atmospheric diagnostic.
        let surface_relative_vorticity = first_usable(
            self.levels
                .iter()
                .filter(|record| {
                    surface_pressure
                        .map(|pressure| record.pressure <= pressure)
                        .unwrap_or(true)
                })
                .map(|record| record.relative_vorticity),
            missing,
        )
        .or_else(|| {
            first_usable(
                self.levels
                    .iter()
                    .filter(|record| {
                        surface_pressure
                            .map(|pressure| record.pressure <= pressure)
                            .unwrap_or(true)
                    })
                    .map(|record| record.absolute_vorticity),
                missing,
            )
            .map(|absolute| absolute - coriolis_parameter(selected.latitude))
        });
        let original_level_count = self.levels.len();
        if let Some((pressure, _, _, _, _, _)) = surface {
            self.levels.retain(|record| record.pressure < pressure);
        }
        let below_ground_levels_removed = original_level_count - self.levels.len();
        let surface_merged = surface.is_some();
        let profile_offset = usize::from(surface_merged);
        let level_count = self.levels.len() + profile_offset;
        let mut matrix = vec![output_missing; COLUMN_COUNT * level_count];

        if let Some((pressure, height, temperature, dewpoint, u, v)) = surface {
            let direction = (270.0 - v.atan2(u).to_degrees()).rem_euclid(360.0);
            let speed = u.hypot(v) * MPS_TO_KNOTS;
            matrix[0] = pressure;
            matrix[level_count] = height;
            matrix[2 * level_count] = temperature;
            matrix[3 * level_count] = dewpoint;
            matrix[4 * level_count] = direction;
            matrix[5 * level_count] = speed;
            matrix[7 * level_count] = u;
            matrix[8 * level_count] = v;
        }

        for (index, record) in self.levels.iter().enumerate() {
            let index = index + profile_offset;
            let temperature_kelvin = usable(record.temperature, missing);
            let temperature_c = temperature_kelvin
                .map(|value| value - KELVIN_OFFSET)
                .unwrap_or(output_missing);

            let height = if self.saw_geopotential_height {
                usable(record.geopotential_height, missing)
            } else if self.saw_geopotential {
                usable(record.geopotential, missing).map(|value| value / G0)
            } else {
                None
            }
            .unwrap_or(output_missing);

            let dewpoint = if self.saw_relative_humidity {
                usable(record.relative_humidity, missing)
                    .zip(usable(record.temperature, missing))
                    .map(|(rh, temp_k)| dewpoint_from_rh(temp_k - KELVIN_OFFSET, rh))
            } else if self.saw_specific_humidity {
                usable(record.specific_humidity, missing)
                    .map(|q| dewpoint_from_specific_humidity(q, record.pressure))
            } else {
                None
            }
            .filter(|value| value.is_finite())
            .unwrap_or(output_missing);

            let u = usable(record.u_wind, missing);
            let v = usable(record.v_wind, missing);
            let (direction, speed) = match u.zip(v) {
                Some((u_value, v_value)) => (
                    (270.0 - v_value.atan2(u_value).to_degrees()).rem_euclid(360.0),
                    u_value.hypot(v_value) * MPS_TO_KNOTS,
                ),
                None => (output_missing, output_missing),
            };

            matrix[index] = record.pressure;
            matrix[level_count + index] = height;
            matrix[2 * level_count + index] = temperature_c;
            matrix[3 * level_count + index] = dewpoint;
            matrix[4 * level_count + index] = direction;
            matrix[5 * level_count + index] = speed;
            matrix[6 * level_count + index] =
                usable(record.omega, missing).unwrap_or(output_missing);
            matrix[7 * level_count + index] = u.unwrap_or(output_missing);
            matrix[8 * level_count + index] = v.unwrap_or(output_missing);
        }

        Ok(DecodedPoint {
            matrix,
            level_count,
            selected_latitude: selected.latitude,
            selected_longitude: normalize_longitude(selected.longitude),
            surface_relative_vorticity,
            surface_merged,
            below_ground_levels_removed,
        })
    }
}

fn usable(value: Option<f64>, missing: Option<f64>) -> Option<f64> {
    value.filter(|candidate| {
        candidate.is_finite() && !missing.is_some_and(|sentinel| *candidate == sentinel)
    })
}

fn first_usable(
    values: impl IntoIterator<Item = Option<f64>>,
    missing: Option<f64>,
) -> Option<f64> {
    values.into_iter().find_map(|value| usable(value, missing))
}

fn dewpoint_from_rh(temperature_c: f64, relative_humidity: f64) -> f64 {
    let a = 17.625;
    let b = 243.04;
    let rh = relative_humidity.clamp(1.0e-3, 100.0);
    let gamma = (rh / 100.0).ln() + (a * temperature_c) / (b + temperature_c);
    (b * gamma) / (a - gamma)
}

fn dewpoint_from_specific_humidity(specific_humidity: f64, pressure_hpa: f64) -> f64 {
    let vapor_pressure =
        ((specific_humidity * pressure_hpa) / (0.622 + 0.378 * specific_humidity)).max(1.0e-6);
    let a = 17.625;
    let b = 243.04;
    let logarithm = (vapor_pressure / 6.112).ln();
    (b * logarithm) / (a - logarithm)
}

fn coriolis_parameter(latitude: f64) -> f64 {
    2.0 * EARTH_ROTATION_RATE * latitude.to_radians().sin()
}

fn normalize_longitude(longitude: f64) -> f64 {
    (longitude + 180.0).rem_euclid(360.0) - 180.0
}

fn find_nearest_wrapped(
    api: &EccodesApi,
    handle: *const c_void,
    latitude: f64,
    longitude: f64,
) -> Result<GridPoint, GribError> {
    let normalized = normalize_longitude(longitude);
    let mut last_error = None;
    for candidate in [normalized, normalized + 360.0, normalized - 360.0] {
        match api.find_nearest(handle, latitude, candidate) {
            Ok(point) => return Ok(point),
            Err(error) => last_error = Some(error),
        }
    }
    if api.get_long_value(handle, NUMBER_OF_POINTS_KEY).ok() == Some(1) {
        let grid_latitude = api.get_optional_double(handle, FIRST_LATITUDE_KEY);
        let grid_longitude = api.get_optional_double(handle, FIRST_LONGITUDE_KEY);
        if let (Some(grid_latitude), Some(grid_longitude)) = (grid_latitude, grid_longitude) {
            if (grid_latitude - latitude).abs() <= 1.0
                && normalize_longitude(grid_longitude - normalized).abs() <= 1.0
            {
                return Ok(GridPoint {
                    index: 0,
                    latitude: grid_latitude,
                    longitude: normalize_longitude(grid_longitude),
                });
            }
        }
    }
    Err(last_error.unwrap_or_else(|| GribError::new("ecCodes nearest lookup failed")))
}

#[derive(Default)]
struct PointDecodeState {
    assembler: RecordAssembler,
    selected_pressure_point: Option<GridPoint>,
    selected_surface_point: Option<GridPoint>,
}

fn validate_and_normalize_point(latitude: f64, longitude: f64) -> Result<(f64, f64), GribError> {
    if !latitude.is_finite() || !(-90.0..=90.0).contains(&latitude) {
        return Err(GribError::new(format!(
            "latitude {latitude} is outside [-90, 90]"
        )));
    }
    if !longitude.is_finite() {
        return Err(GribError::new("longitude must be finite"));
    }
    Ok((latitude, normalize_longitude(longitude)))
}

fn grid_points_for<'points>(
    api: &EccodesApi,
    handle: *const c_void,
    grid_key: &str,
    coordinates: &[(f64, f64)],
    grids: &'points mut HashMap<String, Vec<GridPoint>>,
) -> Result<&'points [GridPoint], GribError> {
    if !grids.contains_key(grid_key) {
        let points = coordinates
            .iter()
            .map(|(latitude, longitude)| find_nearest_wrapped(api, handle, *latitude, *longitude))
            .collect::<Result<Vec<_>, _>>()?;
        grids.insert(grid_key.to_owned(), points);
    }
    Ok(grids
        .get(grid_key)
        .expect("the requested GRIB grid points were just inserted"))
}

fn apply_field_values(
    field: &InventoryField,
    points: &[GridPoint],
    raw_values: &[f64],
    states: &mut [PointDecodeState],
    missing: Option<f64>,
) -> Result<(), GribError> {
    if points.len() != states.len() || raw_values.len() != states.len() {
        return Err(GribError::new(format!(
            "ecCodes returned {} point values for {} requested points",
            raw_values.len(),
            states.len()
        )));
    }
    let output_missing = missing
        .filter(|sentinel| sentinel.is_finite())
        .unwrap_or(f64::NAN);
    for ((state, point), raw_value) in states
        .iter_mut()
        .zip(points.iter().copied())
        .zip(raw_values.iter().copied())
    {
        if field.pressure_field.is_some() {
            state.selected_pressure_point.get_or_insert(point);
        } else {
            state.selected_surface_point.get_or_insert(point);
        }
        let value = if !raw_value.is_finite()
            || field
                .missing_value
                .is_some_and(|sentinel| raw_value == sentinel)
        {
            output_missing
        } else {
            raw_value
        };
        if let Some((pressure, kind)) = field.pressure_field {
            state.assembler.insert(pressure, kind, value, field.order);
        } else if let Some(kind) = field.surface_field {
            state.assembler.insert_surface(kind, value);
        }
    }
    Ok(())
}

fn decode_inventory_field(
    api: &EccodesApi,
    handle: *const c_void,
    field: &InventoryField,
    coordinates: &[(f64, f64)],
    grids: &mut HashMap<String, Vec<GridPoint>>,
    states: &mut [PointDecodeState],
    missing: Option<f64>,
) -> Result<(), GribError> {
    let points = grid_points_for(api, handle, &field.grid_key, coordinates, grids)?;
    let indexes = points.iter().map(|point| point.index).collect::<Vec<_>>();
    let values = api.get_elements(handle, &indexes)?;
    apply_field_values(field, points, &values, states, missing)
}

fn scan_inventory_and_decode(
    api: &EccodesApi,
    mapping: &[u8],
    boundaries: &[MessageBoundary],
    coordinates: &[(f64, f64)],
    missing: Option<f64>,
) -> Result<(Arc<GribInventory>, Vec<PointDecodeState>), GribError> {
    let _multi_support = api.enable_multi_support();
    let mut grids = HashMap::<String, Vec<GridPoint>>::new();
    let mut states = (0..coordinates.len())
        .map(|_| PointDecodeState::default())
        .collect::<Vec<_>>();
    let mut inventory_messages = Vec::new();
    let mut message_order = 0;

    for boundary in boundaries {
        let message = &mapping[boundary.offset..boundary.offset + boundary.length];
        let mut cursor = message.as_ptr().cast_mut().cast::<c_void>();
        let mut remaining = message.len();
        let mut field_index = 0;
        let mut inventory_fields = Vec::new();
        loop {
            let Some(handle) = api.next_multi_handle(message, &mut cursor, &mut remaining)? else {
                break;
            };
            let order = message_order;
            message_order += 1;

            let edition = api.get_long_value(handle.as_ptr(), EDITION_KEY)?;
            if edition != c_long::from(boundary.edition) {
                return Err(GribError::new(format!(
                    "ecCodes edition {edition} disagrees with GRIB{} boundary at byte {}",
                    boundary.edition, boundary.offset
                )));
            }

            let Ok(short_name) = api.get_string_value(handle.as_ptr(), SHORT_NAME_KEY) else {
                field_index += 1;
                continue;
            };
            let Ok(type_of_level) = api.get_string_value(handle.as_ptr(), TYPE_OF_LEVEL_KEY) else {
                field_index += 1;
                continue;
            };
            let Ok(level) = api.get_double_value(handle.as_ptr(), LEVEL_KEY) else {
                field_index += 1;
                continue;
            };
            let pressure_field =
                pressure_hpa(&type_of_level, level).zip(classify_field(&short_name));
            let surface_field = classify_surface_field(&short_name, &type_of_level, level);
            if pressure_field.is_none() && surface_field.is_none() {
                field_index += 1;
                continue;
            }

            let grid_key = api
                .get_optional_string(handle.as_ptr(), GRID_HASH_KEY)
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| format!("message:{}", boundary.offset));
            let points = grid_points_for(api, handle.as_ptr(), &grid_key, coordinates, &mut grids)?;
            let indexes = points.iter().map(|point| point.index).collect::<Vec<_>>();
            let raw_values = api.get_elements(handle.as_ptr(), &indexes)?;
            let field = InventoryField {
                field_index,
                order,
                pressure_field,
                surface_field,
                grid_key,
                missing_value: api.get_optional_double(handle.as_ptr(), MISSING_VALUE_KEY),
            };
            apply_field_values(&field, points, &raw_values, &mut states, missing)?;
            inventory_fields.push(field);
            field_index += 1;
        }
        if !inventory_fields.is_empty() {
            inventory_messages.push(InventoryMessage {
                boundary: *boundary,
                fields: inventory_fields,
            });
        }
    }

    Ok((
        Arc::new(GribInventory {
            messages: inventory_messages,
        }),
        states,
    ))
}

enum CachedInventoryDecodeError {
    Stale,
    Decode(GribError),
}

fn decode_from_inventory(
    api: &EccodesApi,
    mapping: &[u8],
    inventory: &GribInventory,
    coordinates: &[(f64, f64)],
    missing: Option<f64>,
) -> Result<Vec<PointDecodeState>, CachedInventoryDecodeError> {
    if !inventory.matches_mapping(mapping) {
        return Err(CachedInventoryDecodeError::Stale);
    }
    let _multi_support = api.enable_multi_support();
    let mut grids = HashMap::<String, Vec<GridPoint>>::new();
    let mut states = (0..coordinates.len())
        .map(|_| PointDecodeState::default())
        .collect::<Vec<_>>();

    for inventory_message in &inventory.messages {
        let boundary = inventory_message.boundary;
        let message = &mapping[boundary.offset..boundary.offset + boundary.length];
        let mut cursor = message.as_ptr().cast_mut().cast::<c_void>();
        let mut remaining = message.len();
        let mut field_index = 0;
        let mut selected_index = 0;
        loop {
            let Some(handle) = api
                .next_multi_handle(message, &mut cursor, &mut remaining)
                .map_err(CachedInventoryDecodeError::Decode)?
            else {
                break;
            };
            if inventory_message
                .fields
                .get(selected_index)
                .is_some_and(|field| field.field_index == field_index)
            {
                decode_inventory_field(
                    api,
                    handle.as_ptr(),
                    &inventory_message.fields[selected_index],
                    coordinates,
                    &mut grids,
                    &mut states,
                    missing,
                )
                .map_err(CachedInventoryDecodeError::Decode)?;
                selected_index += 1;
            }
            field_index += 1;
        }
        if selected_index != inventory_message.fields.len() {
            return Err(CachedInventoryDecodeError::Stale);
        }
    }
    Ok(states)
}

fn assemble_decoded_points(
    states: Vec<PointDecodeState>,
    missing: Option<f64>,
) -> Result<Vec<DecodedPoint>, GribError> {
    states
        .into_iter()
        .map(|state| {
            let selected = state
                .selected_pressure_point
                .or(state.selected_surface_point)
                .ok_or_else(|| {
                    GribError::new("no required pressure-level GRIB fields were found")
                })?;
            state.assembler.assemble(selected, missing)
        })
        .collect()
}

fn open_grib_mapping(
    grib_path: &Path,
) -> Result<(File, memmap2::Mmap, Option<FileIdentity>), GribError> {
    let file = File::open(grib_path).map_err(|error| {
        GribError::new(format!(
            "could not open GRIB file {}: {error}",
            grib_path.display()
        ))
    })?;
    let metadata = file.metadata().ok();
    if metadata.as_ref().map(Metadata::len).unwrap_or(0) == 0 {
        return Err(GribError::new(format!(
            "GRIB file is empty: {}",
            grib_path.display()
        )));
    }
    // SAFETY: model-cache leases keep downloaded subsets stable while this
    // decoder runs. The read-only map and its opened file both outlive all
    // ecCodes handles constructed from its bytes.
    let mapping = unsafe { MmapOptions::new().map(&file) }.map_err(|error| {
        GribError::new(format!(
            "could not memory-map GRIB file {}: {error}",
            grib_path.display()
        ))
    })?;
    // Re-read metadata after mapping so a mutation racing the open/map window
    // cannot associate cached offsets with the earlier snapshot. If size and
    // mapping disagree, this decode remains correct but deliberately bypasses
    // the inventory cache.
    let identity = file
        .metadata()
        .ok()
        .filter(|metadata| metadata.len() == mapping.len() as u64)
        .and_then(|metadata| FileIdentity::from_open_file(grib_path, &metadata));
    Ok((file, mapping, identity))
}

/// Decode several nearest-grid-point soundings in stable request order.
///
/// Every selected message is opened once and ecCodes retrieves all requested
/// grid indexes with one vector call. Duplicate requests and distinct requests
/// that resolve to the same grid cell remain duplicated in the returned vector.
pub fn decode_grib_points(
    grib_path: &Path,
    eccodes_library_path: &Path,
    points: &[(f64, f64)],
    missing: Option<f64>,
) -> Result<Vec<DecodedPoint>, GribError> {
    let coordinates = points
        .iter()
        .map(|(latitude, longitude)| validate_and_normalize_point(*latitude, *longitude))
        .collect::<Result<Vec<_>, _>>()?;
    if coordinates.is_empty() {
        return Ok(Vec::new());
    }

    let (_file, mapping, identity) = open_grib_mapping(grib_path)?;
    let cached_inventory = identity
        .as_ref()
        .and_then(|identity| inventory_cache().lookup(identity));
    let cold_boundaries = if cached_inventory.is_none() {
        Some(scan_message_boundaries(&mapping)?)
    } else {
        None
    };

    let _guard = ECCODES_CALL_LOCK
        .lock()
        .map_err(|_| GribError::new("ecCodes call lock was poisoned"))?;
    let api = EccodesApi::cached(eccodes_library_path)?;
    let states = if let Some(inventory) = cached_inventory {
        match decode_from_inventory(api, &mapping, &inventory, &coordinates, missing) {
            Ok(states) => states,
            Err(CachedInventoryDecodeError::Decode(error)) => return Err(error),
            Err(CachedInventoryDecodeError::Stale) => {
                if let Some(identity) = &identity {
                    inventory_cache().invalidate(identity);
                }
                let boundaries = scan_message_boundaries(&mapping)?;
                let (inventory, states) =
                    scan_inventory_and_decode(api, &mapping, &boundaries, &coordinates, missing)?;
                if let Some(identity) = identity.clone() {
                    inventory_cache().insert(identity, inventory);
                }
                states
            }
        }
    } else {
        let boundaries = cold_boundaries.expect("a cold decode scanned GRIB boundaries");
        let (inventory, states) =
            scan_inventory_and_decode(api, &mapping, &boundaries, &coordinates, missing)?;
        if let Some(identity) = identity {
            inventory_cache().insert(identity, inventory);
        }
        states
    };
    assemble_decoded_points(states, missing)
}

/// Decode one nearest-grid-point sounding with one Python-to-Rust call.
pub fn decode_grib_point(
    grib_path: &Path,
    eccodes_library_path: &Path,
    latitude: f64,
    longitude: f64,
    missing: Option<f64>,
) -> Result<DecodedPoint, GribError> {
    let mut decoded = decode_grib_points(
        grib_path,
        eccodes_library_path,
        &[(latitude, longitude)],
        missing,
    )?;
    Ok(decoded
        .pop()
        .expect("one requested GRIB point produces one decoded point"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_identity(path: &str, size: u64, stamp: u64) -> FileIdentity {
        FileIdentity {
            canonical_path: PathBuf::from(path),
            size,
            #[cfg(windows)]
            creation_time: stamp,
            #[cfg(windows)]
            last_write_time: stamp,
            #[cfg(unix)]
            device: 1,
            #[cfg(unix)]
            inode: stamp,
            #[cfg(unix)]
            modified_seconds: stamp as i64,
            #[cfg(unix)]
            modified_nanoseconds: 0,
            #[cfg(unix)]
            changed_seconds: stamp as i64,
            #[cfg(unix)]
            changed_nanoseconds: 0,
        }
    }

    fn empty_inventory() -> Arc<GribInventory> {
        Arc::new(GribInventory {
            messages: Vec::new(),
        })
    }

    fn grib1(length: usize) -> Vec<u8> {
        assert!((12..=0x00ff_ffff).contains(&length));
        let mut message = vec![0; length];
        message[..4].copy_from_slice(b"GRIB");
        message[4] = ((length >> 16) & 0xff) as u8;
        message[5] = ((length >> 8) & 0xff) as u8;
        message[6] = (length & 0xff) as u8;
        message[7] = 1;
        message[length - 4..].copy_from_slice(b"7777");
        message
    }

    fn grib2(length: usize) -> Vec<u8> {
        assert!(length >= 20);
        let mut message = vec![0; length];
        message[..4].copy_from_slice(b"GRIB");
        message[7] = 2;
        message[8..16].copy_from_slice(&(length as u64).to_be_bytes());
        message[length - 4..].copy_from_slice(b"7777");
        message
    }

    #[test]
    fn scans_grib1_and_grib2_with_non_message_bytes_between_them() {
        let first = grib1(24);
        let second = grib2(32);
        let mut bytes = b"WMO header\r\n".to_vec();
        let first_offset = bytes.len();
        bytes.extend_from_slice(&first);
        bytes.extend_from_slice(b"padding");
        let second_offset = bytes.len();
        bytes.extend_from_slice(&second);
        bytes.extend_from_slice(b"trailer");

        assert_eq!(
            scan_message_boundaries(&bytes).unwrap(),
            vec![
                MessageBoundary {
                    offset: first_offset,
                    length: first.len(),
                    edition: 1,
                },
                MessageBoundary {
                    offset: second_offset,
                    length: second.len(),
                    edition: 2,
                },
            ]
        );
    }

    #[test]
    fn rejects_truncated_and_corrupt_messages() {
        let mut truncated = grib2(32);
        truncated.truncate(28);
        assert!(scan_message_boundaries(&truncated)
            .unwrap_err()
            .to_string()
            .contains("truncated GRIB2"));

        let mut corrupt = grib1(24);
        corrupt[20..].copy_from_slice(b"0000");
        assert!(scan_message_boundaries(&corrupt)
            .unwrap_err()
            .to_string()
            .contains("missing 7777"));
    }

    #[test]
    fn rejects_unsupported_editions_and_empty_input() {
        let mut unsupported = grib2(24);
        unsupported[7] = 3;
        assert!(scan_message_boundaries(&unsupported)
            .unwrap_err()
            .to_string()
            .contains("unsupported GRIB edition 3"));
        assert_eq!(
            scan_message_boundaries(b"not grib").unwrap_err(),
            GribError::new("no complete GRIB messages were found")
        );
    }

    #[test]
    fn inventory_cache_is_bounded_lru_and_replaces_changed_file_identity() {
        let mut cache = InventoryCache::default();
        let identities = (0..=GRIB_INVENTORY_CACHE_MAX)
            .map(|index| test_identity(&format!("fixture-{index}.grib2"), index as u64 + 1, 1))
            .collect::<Vec<_>>();
        for identity in &identities[..GRIB_INVENTORY_CACHE_MAX] {
            cache.insert(identity.clone(), empty_inventory());
        }
        assert_eq!(cache.info().size, GRIB_INVENTORY_CACHE_MAX);

        // Refresh the oldest entry, then force one eviction. The second entry
        // becomes the least recently used item.
        assert!(cache.lookup(&identities[0]).is_some());
        cache.insert(
            identities[GRIB_INVENTORY_CACHE_MAX].clone(),
            empty_inventory(),
        );
        assert!(cache.lookup(&identities[1]).is_none());
        assert!(cache.lookup(&identities[0]).is_some());
        assert_eq!(cache.info().evictions, 1);

        let old = test_identity("changing.grib2", 10, 10);
        // Atomic replacement can preserve both the path and byte size.  The
        // platform file identity/timestamps must still evict the old entry.
        let changed = test_identity("changing.grib2", 10, 11);
        cache.insert(old.clone(), empty_inventory());
        cache.insert(changed.clone(), empty_inventory());
        assert!(cache.lookup(&old).is_none());
        assert!(cache.lookup(&changed).is_some());
        assert!(cache.info().invalidations >= 1);
        assert!(cache.info().size <= GRIB_INVENTORY_CACHE_MAX);
    }

    #[test]
    fn disabled_inventory_cache_bypasses_reads_and_writes_until_reenabled() {
        let identity = test_identity("fixture.grib2", 42, 7);
        let ignored = test_identity("ignored.grib2", 43, 8);
        let mut cache = InventoryCache::default();
        cache.insert(identity.clone(), empty_inventory());
        cache.enabled = false;
        let before = cache.info();
        assert!(cache.lookup(&identity).is_none());
        cache.insert(ignored.clone(), empty_inventory());
        let disabled = cache.info();
        assert_eq!(disabled.hits, before.hits);
        assert_eq!(disabled.misses, before.misses);
        assert_eq!(disabled.size, before.size);

        cache.enabled = true;
        assert!(cache.lookup(&identity).is_some());
        assert!(cache.lookup(&ignored).is_none());
        cache.clear(false);
        assert_eq!(cache.info().size, 0);
        assert!(cache.info().hits > 0);
        cache.clear(true);
        assert_eq!(
            cache.info(),
            GribInventoryCacheInfo {
                enabled: true,
                size: 0,
                max_size: GRIB_INVENTORY_CACHE_MAX,
                hits: 0,
                misses: 0,
                evictions: 0,
                invalidations: 0,
            }
        );
    }

    #[test]
    fn multi_point_field_routing_preserves_order_and_duplicate_cells() {
        let field = InventoryField {
            field_index: 0,
            order: 3,
            pressure_field: Some((850.0, FieldKind::Temperature)),
            surface_field: None,
            grid_key: "grid".to_owned(),
            missing_value: Some(9999.0),
        };
        let points = [
            GridPoint {
                index: 4,
                latitude: 35.0,
                longitude: -97.0,
            },
            GridPoint {
                index: 4,
                latitude: 35.0,
                longitude: -97.0,
            },
            GridPoint {
                index: 9,
                latitude: 36.0,
                longitude: -96.0,
            },
        ];
        let mut states = (0..points.len())
            .map(|_| PointDecodeState::default())
            .collect::<Vec<_>>();
        apply_field_values(
            &field,
            &points,
            &[280.0, 280.0, 9999.0],
            &mut states,
            Some(-9999.0),
        )
        .unwrap();

        assert_eq!(states[0].selected_pressure_point.unwrap().index, 4);
        assert_eq!(states[1].selected_pressure_point.unwrap().index, 4);
        assert_eq!(states[2].selected_pressure_point.unwrap().index, 9);
        assert_eq!(states[0].assembler.levels[0].temperature, Some(280.0));
        assert_eq!(states[1].assembler.levels[0].temperature, Some(280.0));
        assert_eq!(states[2].assembler.levels[0].temperature, Some(-9999.0));
        assert_eq!(states[0].assembler.levels[0].first_order, 3);
    }

    #[test]
    fn multi_point_field_routing_rejects_wrong_vector_length() {
        let field = InventoryField {
            field_index: 0,
            order: 0,
            pressure_field: Some((850.0, FieldKind::Temperature)),
            surface_field: None,
            grid_key: "grid".to_owned(),
            missing_value: None,
        };
        let point = GridPoint {
            index: 0,
            latitude: 0.0,
            longitude: 0.0,
        };
        let mut states = [PointDecodeState::default(), PointDecodeState::default()];
        assert_eq!(
            apply_field_values(&field, &[point, point], &[1.0], &mut states, None).unwrap_err(),
            GribError::new("ecCodes returned 1 point values for 2 requested points")
        );
    }

    #[test]
    fn fixed_fixture_multipoint_matches_scalars_when_configured() {
        let (Ok(fixture), Ok(library)) = (
            std::env::var("SHARPMOD_GRIB_TEST_FIXTURE"),
            std::env::var("SHARPMOD_ECCODES_LIBRARY"),
        ) else {
            return;
        };
        let fixture = Path::new(&fixture);
        let library = Path::new(&library);
        let requests = [(35.18, -97.44), (35.18, -97.44), (35.23, -97.39)];

        clear_grib_inventory_cache(true);
        set_grib_inventory_cache_enabled(true);
        let scalars = requests
            .iter()
            .map(|(latitude, longitude)| {
                decode_grib_point(fixture, library, *latitude, *longitude, Some(-9999.0))
            })
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        let decoded = decode_grib_points(fixture, library, &requests, Some(-9999.0)).unwrap();

        assert_eq!(decoded, scalars);
        assert_eq!(decoded[0], decoded[1]);
        let info = grib_inventory_cache_info();
        assert_eq!(info.size, 1);
        assert_eq!(info.misses, 1);
        assert!(info.hits >= requests.len() as u64);
    }

    #[test]
    fn field_classification_and_pressure_units_cover_model_aliases() {
        assert_eq!(classify_field("TMP"), Some(FieldKind::Temperature));
        assert_eq!(classify_field("HGT"), Some(FieldKind::GeopotentialHeight));
        assert_eq!(classify_field("SPFH"), Some(FieldKind::SpecificHumidity));
        assert_eq!(classify_field("UGRD"), Some(FieldKind::UWind));
        assert_eq!(classify_field("ABSV"), Some(FieldKind::AbsoluteVorticity));
        assert_eq!(classify_field("unknown"), None);
        assert_eq!(pressure_hpa("isobaricInhPa", 850.0), Some(850.0));
        assert_eq!(pressure_hpa("isobaricInPa", 85_000.0), Some(850.0));
        assert_eq!(pressure_hpa("surface", 0.0), None);
        assert_eq!(
            classify_surface_field("sp", "surface", 0.0),
            Some(SurfaceFieldKind::Pressure)
        );
        assert_eq!(
            classify_surface_field("2t", "heightAboveGround", 2.0),
            Some(SurfaceFieldKind::Temperature)
        );
        assert_eq!(
            classify_surface_field("10v", "heightAboveGround", 10.0),
            Some(SurfaceFieldKind::VWind)
        );
    }

    #[test]
    fn assembly_sorts_deduplicates_converts_and_uses_one_field_major_buffer() {
        let mut assembler = RecordAssembler::default();
        let samples = [
            (850.0, FieldKind::Temperature, 283.15),
            (1000.0, FieldKind::Temperature, 293.15),
            // Earliest exact duplicate wins.
            (1000.0, FieldKind::Temperature, 999.0),
            (850.0, FieldKind::GeopotentialHeight, 1500.0),
            (1000.0, FieldKind::GeopotentialHeight, 100.0),
            (850.0, FieldKind::RelativeHumidity, 50.0),
            (1000.0, FieldKind::RelativeHumidity, 75.0),
            (850.0, FieldKind::UWind, 3.0),
            (850.0, FieldKind::VWind, 4.0),
            (1000.0, FieldKind::UWind, 0.0),
            (1000.0, FieldKind::VWind, -10.0),
            (850.0, FieldKind::Omega, -0.25),
            (1000.0, FieldKind::AbsoluteVorticity, 2.0e-4),
        ];
        for (order, (pressure, kind, value)) in samples.into_iter().enumerate() {
            assembler.insert(pressure, kind, value, order);
        }

        let decoded = assembler
            .assemble(
                GridPoint {
                    index: 7,
                    latitude: 30.0,
                    longitude: 270.0,
                },
                Some(-9999.0),
            )
            .unwrap();

        assert_eq!(decoded.level_count, 2);
        assert_eq!(decoded.matrix.len(), COLUMN_COUNT * 2);
        assert_eq!(&decoded.matrix[0..2], &[1000.0, 850.0]);
        assert_eq!(&decoded.matrix[2..4], &[100.0, 1500.0]);
        assert!((decoded.matrix[4] - 20.0).abs() < 1.0e-12);
        assert!((decoded.matrix[5] - 10.0).abs() < 1.0e-12);
        assert_eq!(decoded.matrix[8], 0.0);
        assert!((decoded.matrix[10] - 10.0 * MPS_TO_KNOTS).abs() < 1.0e-12);
        assert_eq!(decoded.matrix[12], -9999.0);
        assert_eq!(decoded.matrix[13], -0.25);
        assert_eq!(&decoded.matrix[14..16], &[0.0, 3.0]);
        assert_eq!(&decoded.matrix[16..18], &[-10.0, 4.0]);
        assert_eq!(decoded.selected_longitude, -90.0);
        let expected_vorticity = 2.0e-4 - coriolis_parameter(30.0);
        assert!((decoded.surface_relative_vorticity.unwrap() - expected_vorticity).abs() < 1.0e-15);
        assert!(!decoded.surface_merged);
        assert_eq!(decoded.below_ground_levels_removed, 0);
    }

    #[test]
    fn verified_surface_removes_every_below_ground_isobar() {
        let mut assembler = RecordAssembler::default();
        let levels = [
            1013.2, 1000.0, 975.0, 950.0, 925.0, 900.0, 875.0, 850.0, 825.0, 840.6,
        ];
        for (order, pressure) in levels.into_iter().enumerate() {
            assembler.insert(pressure, FieldKind::GeopotentialHeight, 100.0, order);
            assembler.insert(pressure, FieldKind::Temperature, 293.15, order);
            assembler.insert(pressure, FieldKind::RelativeHumidity, 50.0, order);
            assembler.insert(pressure, FieldKind::UWind, 3.0, order);
            assembler.insert(pressure, FieldKind::VWind, 4.0, order);
            assembler.insert(
                pressure,
                FieldKind::RelativeVorticity,
                pressure * 1.0e-7,
                order,
            );
        }
        assembler.insert_surface(SurfaceFieldKind::Pressure, 84_060.0);
        assembler.insert_surface(SurfaceFieldKind::Height, 1655.0);
        assembler.insert_surface(SurfaceFieldKind::Temperature, 298.15);
        assembler.insert_surface(SurfaceFieldKind::Dewpoint, 288.15);
        assembler.insert_surface(SurfaceFieldKind::UWind, 5.0);
        assembler.insert_surface(SurfaceFieldKind::VWind, 0.0);

        let decoded = assembler
            .assemble(
                GridPoint {
                    index: 0,
                    latitude: 39.7,
                    longitude: -105.0,
                },
                Some(-9999.0),
            )
            .unwrap();

        assert!(decoded.surface_merged);
        assert_eq!(decoded.below_ground_levels_removed, 9);
        assert_eq!(decoded.level_count, 2);
        assert_eq!(&decoded.matrix[0..2], &[840.6, 825.0]);
        assert_eq!(&decoded.matrix[2..4], &[1655.0, 100.0]);
        assert!((decoded.matrix[4] - 25.0).abs() < 1.0e-12);
        assert!((decoded.matrix[6] - 15.0).abs() < 1.0e-12);
        assert_eq!(decoded.surface_relative_vorticity, Some(840.6 * 1.0e-7),);
    }

    #[test]
    fn missing_fields_remain_missing_and_relative_vorticity_has_precedence() {
        let mut assembler = RecordAssembler::default();
        assembler.insert(1000.0, FieldKind::Temperature, f64::NAN, 0);
        assembler.insert(1000.0, FieldKind::GeopotentialHeight, f64::NAN, 1);
        assembler.insert(1000.0, FieldKind::RelativeHumidity, f64::NAN, 2);
        assembler.insert(1000.0, FieldKind::UWind, f64::NAN, 3);
        assembler.insert(1000.0, FieldKind::VWind, f64::NAN, 4);
        assembler.insert(1000.0, FieldKind::RelativeVorticity, 1.0e-4, 5);
        assembler.insert(1000.0, FieldKind::AbsoluteVorticity, 9.0e-4, 6);
        let decoded = assembler
            .assemble(
                GridPoint {
                    index: 0,
                    latitude: 0.0,
                    longitude: 0.0,
                },
                Some(-9999.0),
            )
            .unwrap();

        assert_eq!(decoded.matrix[2], -9999.0);
        assert_eq!(decoded.matrix[1], -9999.0);
        assert_eq!(decoded.surface_relative_vorticity, Some(1.0e-4));
    }

    #[test]
    fn rejects_missing_required_core_columns_without_requiring_optional_fields() {
        let mut incomplete = RecordAssembler::default();
        incomplete.insert(1000.0, FieldKind::GeopotentialHeight, 100.0, 0);
        incomplete.insert(1000.0, FieldKind::Temperature, 293.15, 1);
        incomplete.insert(1000.0, FieldKind::RelativeHumidity, 50.0, 2);
        incomplete.insert(1000.0, FieldKind::UWind, 3.0, 3);
        let error = incomplete
            .assemble(
                GridPoint {
                    index: 0,
                    latitude: 0.0,
                    longitude: 0.0,
                },
                Some(-9999.0),
            )
            .unwrap_err();
        assert_eq!(
            error,
            GribError::new("missing required pressure-level fields: v wind")
        );

        let mut complete = RecordAssembler::default();
        complete.insert(1000.0, FieldKind::GeopotentialHeight, 100.0, 0);
        complete.insert(1000.0, FieldKind::Temperature, 293.15, 1);
        complete.insert(1000.0, FieldKind::RelativeHumidity, 50.0, 2);
        complete.insert(1000.0, FieldKind::UWind, 3.0, 3);
        complete.insert(1000.0, FieldKind::VWind, 4.0, 4);
        let decoded = complete
            .assemble(
                GridPoint {
                    index: 0,
                    latitude: 0.0,
                    longitude: 0.0,
                },
                Some(-9999.0),
            )
            .unwrap();
        assert_eq!(decoded.matrix[6], -9999.0);
        assert_eq!(decoded.surface_relative_vorticity, None);
    }
}

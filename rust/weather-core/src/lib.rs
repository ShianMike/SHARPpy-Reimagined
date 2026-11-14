//! Weather fetching and processing shared by the desktop and Python extension.

pub mod alerts;
pub mod australian_radar;
pub mod dwd_radar;
mod http;
pub mod map_fields;
pub mod model_http;
pub mod nexrad_level2;
pub mod opera_radar;
pub mod place_names;
pub mod polar_radar;
pub mod radar;
pub mod radar_level3;
pub mod radar_mosaic;
pub mod radar_provider;
pub mod radar_site_wms;
pub mod radar_sites;
pub mod radar_time;
pub mod range_http;
pub mod raster_png;
pub mod reports;
pub mod satellite;
pub mod spc_outlook;
pub mod surface;
pub mod threshold_catalog;
pub mod uwyo;

use time::{OffsetDateTime, UtcOffset};

pub fn base64_encode(bytes: &[u8]) -> String {
    use base64::Engine as _;
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

pub fn format_rfc3339_utc(instant: OffsetDateTime) -> String {
    let t = instant.to_offset(UtcOffset::UTC);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        t.year(),
        u8::from(t.month()),
        t.day(),
        t.hour(),
        t.minute(),
        t.second()
    )
}

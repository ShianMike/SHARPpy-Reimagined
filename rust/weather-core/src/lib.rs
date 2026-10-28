//! Weather fetching and processing shared by the desktop and Python extension.

pub mod alerts;
mod http;
pub mod radar;
pub mod radar_sites;

use time::{OffsetDateTime, UtcOffset};

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

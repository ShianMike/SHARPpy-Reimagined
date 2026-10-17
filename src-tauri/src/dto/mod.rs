//! Public DTOs returned by the Tauri commands and events (camelCase, stable).

pub mod analysis;
pub mod checks;
pub mod contours;
pub mod health;
pub mod map;
pub mod models;
pub mod overlays;
pub mod radar;
pub mod reports;
pub mod satellite;
pub mod sounding_progress;
pub mod soundings;
pub mod stations;
pub mod storage;
pub mod surface;
pub mod threshold_catalog;
pub mod thresholds;
pub mod wrf;

use time::{OffsetDateTime, UtcOffset};

/// The current time as RFC 3339 UTC with whole seconds, e.g. `2026-10-04T01:02:05Z`.
///
/// Every `checkedAt` and `since` value comes from here.
pub fn rfc3339_utc_now() -> String {
    format_rfc3339_utc(OffsetDateTime::now_utc())
}

/// Formats `instant` in UTC with whole seconds; sub-second digits are dropped.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whole_seconds_utc_even_with_nanoseconds() {
        // 2026-10-04T01:02:05.987654321Z
        let instant = OffsetDateTime::from_unix_timestamp_nanos(1_791_075_725_987_654_321).unwrap();
        assert_eq!(format_rfc3339_utc(instant), "2026-10-04T01:02:05Z");
        let shifted = instant.to_offset(UtcOffset::from_hms(-5, 0, 0).unwrap());
        assert_eq!(format_rfc3339_utc(shifted), "2026-10-04T01:02:05Z");
    }

    #[test]
    fn now_has_the_fixture_form() {
        let now = rfc3339_utc_now();
        let bytes = now.as_bytes();
        assert_eq!(bytes.len(), 20, "{now}");
        assert_eq!(
            (bytes[4], bytes[10], bytes[19]),
            (b'-', b'T', b'Z'),
            "{now}"
        );
    }
}

//! Level III elevation timestamps, including the substituted supplemental time.

use time::OffsetDateTime;

/// NOAA's SAILS products substitute elevation start for volume start in the PDB.
/// Adding the volume-relative delta again would put a valid scan in the future.
/// See https://www.roc.noaa.gov/public-documents/engineering-branch/new-technology/sails/level-two/pdf/SAILS_FAQs.pdf
pub fn level3(
    volume_time: OffsetDateTime,
    delta_seconds: i64,
    supplemental: bool,
) -> Result<OffsetDateTime, &'static str> {
    if !(0..=800).contains(&delta_seconds) {
        return Err("invalid Level III elevation time offset");
    }
    if supplemental {
        return Ok(volume_time);
    }
    volume_time
        .checked_add(time::Duration::seconds(delta_seconds))
        .ok_or("Level III elevation time is out of range")
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    #[test]
    fn supplemental_kdfx_scan_does_not_add_the_volume_offset_twice() {
        // Live KDFX product 99, 2026-10-30: produced at 02:50:53Z.
        let elevation = OffsetDateTime::parse("2026-10-30T02:49:55Z", &Rfc3339).unwrap();
        assert_eq!(level3(elevation, 270, true).unwrap(), elevation);
        let produced = OffsetDateTime::parse("2026-10-30T02:50:53Z", &Rfc3339).unwrap();
        assert!(level3(elevation, 270, true).unwrap() <= produced);
        assert!(level3(elevation, 270, false).unwrap() > produced);
    }

    #[test]
    fn normal_elevations_retain_their_offset_including_across_midnight() {
        let volume = OffsetDateTime::parse("2026-10-29T23:59:00Z", &Rfc3339).unwrap();
        assert_eq!(level3(volume, 0, false).unwrap(), volume);
        assert_eq!(
            level3(volume, 120, false).unwrap(),
            OffsetDateTime::parse("2026-10-30T00:01:00Z", &Rfc3339).unwrap()
        );
        assert_eq!(
            level3(volume, 800, false).unwrap(),
            volume + time::Duration::seconds(800)
        );
    }

    #[test]
    fn invalid_offsets_are_rejected_for_both_scan_kinds() {
        for supplemental in [false, true] {
            for seconds in [-1, 801, i64::MAX] {
                assert!(level3(OffsetDateTime::UNIX_EPOCH, seconds, supplemental).is_err());
            }
        }
    }
}

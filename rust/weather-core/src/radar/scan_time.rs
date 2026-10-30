//! Level III product timestamps, preserving each supplemental scan's time basis.

use time::OffsetDateTime;

#[derive(Clone, Copy, Debug)]
pub enum ScanKind {
    Ordinary,
    Sails,
    Mrle,
}

/// Return the source product's observation time, not its production time.
///
/// Ordinary products carry volume start plus a volume-relative elevation offset. SAILS
/// substitutes the supplemental split-cut start into the PDB volume-time field.
/// MRLE instead substitutes the first cut's start for the whole MRLE sequence:
/// later MRLE products retain that published sequence time, not an exact start
/// for their individual elevations. Their delta is still relative to the
/// original volume start, which a single MRLE product does not supply. Adding
/// it to the substituted time would therefore be incorrect for both kinds.
///
/// NOAA AWIPS OB 20.3.2 release notes, printed page 98 (CCR NA18-00193):
/// https://vlab.noaa.gov/documents/179645/22933652/AWIPS%2BRelease%2BNotes.pdf/6f95f5f2-7bb6-281e-3171-e7c424de706e?download=true&t=1655384917281&version=1.1
/// NOAA SAILS FAQ, page 2:
/// https://www.roc.noaa.gov/public-documents/engineering-branch/new-technology/sails/level-two/pdf/SAILS_FAQs.pdf
pub fn level3(
    volume_time: OffsetDateTime,
    delta_seconds: i64,
    kind: ScanKind,
) -> Result<OffsetDateTime, &'static str> {
    if !(0..=800).contains(&delta_seconds) {
        return Err("invalid Level III elevation time offset");
    }
    match kind {
        ScanKind::Sails | ScanKind::Mrle => Ok(volume_time),
        ScanKind::Ordinary => volume_time
            .checked_add(time::Duration::seconds(delta_seconds))
            .ok_or("Level III elevation time is out of range"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    #[test]
    fn sails_kdfx_scan_does_not_add_the_volume_offset_twice() {
        // Live KDFX product 99, 2026-10-30: produced at 02:50:53Z.
        let elevation = OffsetDateTime::parse("2026-10-30T02:49:55Z", &Rfc3339).unwrap();
        assert_eq!(level3(elevation, 270, ScanKind::Sails).unwrap(), elevation);
        let produced = OffsetDateTime::parse("2026-10-30T02:50:53Z", &Rfc3339).unwrap();
        assert!(level3(elevation, 270, ScanKind::Sails).unwrap() <= produced);
        assert!(level3(elevation, 270, ScanKind::Ordinary).unwrap() > produced);
    }

    #[test]
    fn normal_elevations_retain_their_offset_including_across_midnight() {
        let volume = OffsetDateTime::parse("2026-10-29T23:59:00Z", &Rfc3339).unwrap();
        assert_eq!(level3(volume, 0, ScanKind::Ordinary).unwrap(), volume);
        assert_eq!(
            level3(volume, 120, ScanKind::Ordinary).unwrap(),
            OffsetDateTime::parse("2026-10-30T00:01:00Z", &Rfc3339).unwrap()
        );
        assert_eq!(
            level3(volume, 800, ScanKind::Ordinary).unwrap(),
            volume + time::Duration::seconds(800)
        );
    }

    #[test]
    fn first_mrle_cut_retains_the_published_sequence_start() {
        // Synthetic metadata: the first MRLE cut starts 300 s into the volume.
        let sequence = OffsetDateTime::parse("2026-10-30T00:00:30Z", &Rfc3339).unwrap();
        assert_eq!(level3(sequence, 300, ScanKind::Mrle).unwrap(), sequence);
    }

    #[test]
    fn later_mrle_cut_retains_sequence_time_without_inventing_elevation_time() {
        // A later cut has a different volume-relative delta but the same PDB
        // sequence start. Neither delta supplies the missing volume start.
        let sequence = OffsetDateTime::parse("2026-10-30T00:00:30Z", &Rfc3339).unwrap();
        let first = level3(sequence, 300, ScanKind::Mrle).unwrap();
        let later = level3(sequence, 360, ScanKind::Mrle).unwrap();
        assert_eq!(later, first);
        assert_eq!(later, sequence);
        assert_ne!(later, sequence + time::Duration::seconds(360));
    }

    #[test]
    fn invalid_offsets_are_rejected_for_all_scan_kinds() {
        for kind in [ScanKind::Ordinary, ScanKind::Sails, ScanKind::Mrle] {
            for seconds in [-1, 801, i64::MAX] {
                assert!(level3(OffsetDateTime::UNIX_EPOCH, seconds, kind).is_err());
            }
        }
    }
}

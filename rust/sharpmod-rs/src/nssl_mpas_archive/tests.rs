use super::*;
use sharpmod_weather::bridge_cache::compress_gzip_level1;

fn run(model: Model, hour: u8) -> Run {
    Run::new(model, timestamp(2026, 11, 26, hour, 0, 0).unwrap()).unwrap()
}

#[test]
fn formats_only_exact_utc_cycles_for_the_two_feeds() {
    let htpo = run(Model::Htpo, 18).init;
    let rn = run(Model::Rn, 12).init;
    assert!(is_model("nssl-mpas-htpo"));
    assert!(is_model("nssl-mpas-rn"));
    assert!(!is_model("nssl-mpas-rn3"));
    assert_eq!(
        archive_url("nssl-mpas-htpo", htpo).as_deref(),
        Some("https://data.nssl.noaa.gov/thredds/fileServer/FRDD/NSSL-MPAS/2026/26112618_mpasht2.tar")
    );
    assert_eq!(
        archive_url(
            "nssl-mpas-rn",
            rn.to_offset(UtcOffset::from_hms(8, 0, 0).unwrap())
        )
        .as_deref(),
        Some(
            "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/NSSL-MPAS/2026/26112612_mpasrn.tar"
        )
    );
    assert_eq!(archive_url("nssl-mpas-rn", htpo), None);
    assert_eq!(
        archive_url("nssl-mpas-htpo", htpo + time::Duration::seconds(1)),
        None
    );
    let next_decade = timestamp(2036, 1, 1, 0, 0, 0).unwrap();
    assert!(archive_url("nssl-mpas-htpo", next_decade)
        .unwrap()
        .ends_with("/2036/36010100_mpasht2.tar"));
}

#[test]
fn catalogue_requires_expected_paths_and_uses_modification_time() {
    let catalog = br#"<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0">
  <dataset name="2026">
    <dataset name="26112618_mpasht2.tar" urlPath="FRDD/NSSL-MPAS/2026/26112618_mpasht2.tar">
      <dataSize units="Gbytes">4.404</dataSize>
      <date type="modified">2026-11-27T02:04:13.453Z</date>
    </dataset>
    <dataset name="26112612_mpasrn.tar" urlPath="FRDD/NSSL-MPAS/2026/26112612_mpasrn.tar">
      <dataSize units="Gbytes">7.727</dataSize>
      <date type="modified">2026-11-27T03:07:58.388Z</date>
    </dataset>
    <dataset name="wrong-feed" urlPath="FRDD/NSSL-MPAS/2026/26112612_mpasrn3.tar">
      <date type="modified">2026-11-27T03:09:00Z</date>
    </dataset>
    <dataset name="wrong-cycle" urlPath="FRDD/NSSL-MPAS/2026/26112618_mpasrn.tar">
      <date type="modified">2026-11-27T03:09:00Z</date>
    </dataset>
    <dataset name="wrong-year" urlPath="FRDD/NSSL-MPAS/2025/25112618_mpasht2.tar">
      <date type="modified">2025-11-27T02:09:00Z</date>
    </dataset>
    <dataset name="not-yet-generated" urlPath="FRDD/NSSL-MPAS/2026/26112700_mpasht2.tar">
      <date type="modified">2026-11-26T21:09:00Z</date>
    </dataset>
  </dataset>
</catalog>"#;
    let entries = parse_catalog(catalog, 2026).unwrap();
    assert_eq!(entries.len(), 2);
    assert_eq!(entries[0].run, run(Model::Htpo, 18));
    assert_eq!(entries[1].run, run(Model::Rn, 12));
    assert_eq!(
        entries[0].modified,
        OffsetDateTime::parse("2026-11-27T02:04:13.453Z", &Rfc3339).unwrap()
    );
    assert_eq!(
        entries[1].modified,
        OffsetDateTime::parse("2026-11-27T03:07:58.388Z", &Rfc3339).unwrap()
    );
    assert!(parse_catalog(&catalog[..catalog.len() - 10], 2026).is_err());
}

fn make_header(name: &str, size: usize, kind: u8) -> [u8; BLOCK] {
    let mut block = [0; BLOCK];
    block[..name.len()].copy_from_slice(name.as_bytes());
    block[124..135].copy_from_slice(format!("{size:011o}").as_bytes());
    block[148..156].fill(b' ');
    block[156] = kind;
    block[257..265].copy_from_slice(b"ustar  \0");
    let checksum = block.iter().map(|&byte| usize::from(byte)).sum::<usize>();
    block[148..154].copy_from_slice(format!("{checksum:06o}").as_bytes());
    block[154] = 0;
    block[155] = b' ';
    block
}

fn test_member(hr: u32) -> String {
    format!("./2026112618/mpasht2_nssl_2026112618f{hr:02}.grib2.gz")
}

#[test]
fn indexes_out_of_order_tar_headers_with_padded_absolute_offsets() {
    let run = run(Model::Htpo, 18);
    let hour = Hour { run, lead: 1 };
    let prefix = "2026112618/mpasht2_nssl_2026112618f";
    let stamp = "2026112618";

    let mut prefix_bytes = make_header("./2026112618/", 0, b'5').to_vec();
    prefix_bytes.extend(make_header(
        "./2026112618/mpasht2_nssl_2026112618f01.grib2_ready",
        0,
        b'0',
    ));
    prefix_bytes.extend(make_header(&test_member(46), 92_507_709, b'0'));
    let mut index = ArchiveIndex::new();
    assert_eq!(index.scan(hour, prefix, stamp, 0, &prefix_bytes), Ok(None));
    assert!(index.members[1].is_none());
    assert_eq!(
        index.members[46],
        Some(Member {
            header_at: 1024,
            size: 92_507_709
        })
    );

    let next = index.next;
    assert!(next > 92_507_709);
    assert_eq!(next % BLOCK as u64, 0);
    let offset_bytes = make_header(&test_member(1), 84_848_804, b'0');
    let found = index
        .scan(hour, prefix, stamp, next, &offset_bytes)
        .unwrap()
        .unwrap();
    assert_eq!(found.header_at, next);
    assert_eq!(found.size, 84_848_804);
    assert_eq!(index.members[1], Some(found));
    let end = index.next;
    assert_eq!(
        index.scan(Hour { run, lead: 48 }, prefix, stamp, end, &[0; 2 * BLOCK]),
        Err(Error::Unavailable(404))
    );
    assert_eq!(index.members[1], Some(found));
}

#[test]
fn distinguishes_an_unfinished_header_from_two_zero_blocks() {
    let run = run(Model::Htpo, 18);
    let hour = Hour { run, lead: 1 };
    let mut index = ArchiveIndex::new();
    let prefix = "2026112618/mpasht2_nssl_2026112618f";
    assert_eq!(
        index.scan(hour, prefix, "2026112618", 0, &[0; BLOCK + 1]),
        Ok(None)
    );
    assert_eq!(index.next, 0);
    let ready = make_header(&test_member(1), 400, b'0');
    assert!(matches!(
        index.scan(hour, prefix, "2026112618", 0, &ready),
        Ok(Some(_))
    ));
}

#[test]
fn checks_member_header_checksum_name_type_and_payload_length() {
    let hour = Hour {
        run: run(Model::Htpo, 18),
        lead: 1,
    };
    let member = Member {
        header_at: 512,
        size: 100,
    };
    let mut bytes = make_header(&test_member(1), 100, b'0').to_vec();
    bytes.resize(BLOCK + member.size, 0);
    bytes[BLOCK..BLOCK + 3].copy_from_slice(&[0x1f, 0x8b, 8]);
    assert_eq!(validate_member(&bytes, hour, member), Ok(()));
    assert!(validate_member(&bytes[..bytes.len() - 1], hour, member).is_err());
    bytes[..BLOCK].copy_from_slice(&make_header(&test_member(2), 100, b'0'));
    assert!(validate_member(&bytes, hour, member).is_err());
    bytes[..BLOCK].copy_from_slice(&make_header(&test_member(1), 100, b'S'));
    assert!(validate_member(&bytes, hour, member).is_err());
    bytes[..BLOCK].copy_from_slice(&make_header(&test_member(1), 100, b'0'));
    bytes[124] = b'1';
    assert!(validate_member(&bytes, hour, member).is_err());
}

fn put_time(buffer: &mut [u8], time: OffsetDateTime) {
    buffer[..2].copy_from_slice(&(time.year() as u16).to_be_bytes());
    buffer[2..7].copy_from_slice(&[
        u8::from(time.month()),
        time.day(),
        time.hour(),
        time.minute(),
        time.second(),
    ]);
}

fn grib_message(hour: Hour, interval: bool) -> Vec<u8> {
    let mut section1 = [0; 21];
    section1[0..4].copy_from_slice(&21_u32.to_be_bytes());
    section1[4] = 1;
    put_time(&mut section1[12..], hour.run.init);

    let mut section4 = vec![0; if interval { 58 } else { 34 }];
    let length = section4.len() as u32;
    section4[..4].copy_from_slice(&length.to_be_bytes());
    section4[4] = 4;
    section4[17] = 1;
    if interval {
        section4[8] = 8;
        put_time(
            &mut section4[34..],
            hour.run.init + time::Duration::hours(i64::from(hour.lead)),
        );
    } else {
        section4[18..22].copy_from_slice(&hour.lead.to_be_bytes());
    }
    let mut message = vec![0; 16];
    message[..4].copy_from_slice(b"GRIB");
    message[7] = 2;
    let length = (message.len() + section1.len() + section4.len() + 4) as u64;
    message[8..16].copy_from_slice(&length.to_be_bytes());
    message.extend(section1);
    message.extend(section4);
    message.extend_from_slice(b"7777");
    message
}

#[test]
fn validates_gzip_grib_reference_time_and_forecast_step() {
    let hour = Hour {
        run: run(Model::Htpo, 18),
        lead: 46,
    };
    let mut message = grib_message(hour, false);
    message.extend(grib_message(hour, true));
    let gzip = compress_gzip_level1(&message).unwrap();
    assert_eq!(inflate(&gzip, hour, &|| false), Ok(message.clone()));
    assert_eq!(inflate(&gzip, hour, &|| true), Err(Error::Cancelled));
    assert!(inflate(&gzip[..gzip.len() - 1], hour, &|| false).is_err());
    assert!(inflate(
        &gzip,
        Hour {
            run: run(Model::Htpo, 12),
            ..hour
        },
        &|| false
    )
    .is_err());

    let changed_step = Hour { lead: 45, ..hour };
    assert!(inflate(&gzip, changed_step, &|| false).is_err());
    let mut wrong_interval = grib_message(hour, false);
    wrong_interval.extend(grib_message(changed_step, true));
    assert!(validate_grib(&wrong_interval, hour, &|| false).is_err());
    assert!(validate_grib(&message[..message.len() - 1], hour, &|| false).is_err());
}

#[test]
fn rejects_out_of_range_hours_and_preserves_missing_archive_status() {
    assert!(matches!(
        hour_grib(
            "nssl-mpas-htpo",
            run(Model::Htpo, 18).init,
            49,
            Duration::from_secs(1),
            &|| Ok(false)
        ),
        Err(Error::Invalid(_))
    ));
    assert_eq!(
        hour_grib(
            "nssl-mpas-rn",
            run(Model::Rn, 12).init,
            84,
            Duration::from_secs(1),
            &|| Ok(true)
        ),
        Err(Error::Cancelled)
    );
    assert_eq!(
        unpublished(Error::Unavailable(404)),
        Error::Unavailable(404)
    );
    assert_eq!(unpublished(Error::Http(410)), Error::Unavailable(410));
}

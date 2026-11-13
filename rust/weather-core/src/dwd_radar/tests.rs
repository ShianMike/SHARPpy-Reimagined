use super::*;

const ESSEN: &[u8] = include_bytes!("../../resources/dwd_ess_20261109_0255.bin");

fn site() -> &'static RadarSite {
    crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "DESS")
        .unwrap()
}

fn instant(value: &str) -> OffsetDateTime {
    OffsetDateTime::parse(value, &Rfc3339).unwrap()
}

#[test]
fn official_dx_scan_preserves_angles_calibration_and_masks() {
    let sweep = decode::decode(ESSEN, site()).unwrap();
    assert_eq!(sweep.site_id, "DESS");
    assert_eq!(sweep.valid_time, "2026-11-09T02:55:00Z");
    assert_eq!(sweep.radials.len(), 360);
    let ray = &sweep.radials[0];
    assert_eq!(
        (
            ray.azimuth,
            ray.elevation_angle,
            ray.first_gate_m,
            ray.gate_spacing_m
        ),
        (0.0, Some(0.7), 500.0, 1000.0)
    );
    // The source's first seven encoded words are 0043, 1002, 000f,
    // 1002, 0036, 1007, 0025 (little-endian). The compression flag is 1000.
    assert_eq!(
        &ray.values[..7],
        &[1.0, -32.5, -32.5, -25.0, -32.5, -32.5, -5.5]
    );
    assert_eq!(ray.values[14], -14.0);
    assert!(sweep
        .radials
        .iter()
        .any(|ray| ray.elevation_angle != Some(0.7)));
    assert!(sweep.radials.iter().all(|ray| ray.values.len() == 128));
}

#[test]
fn rejects_wrong_station_truncated_files_and_oversized_runs() {
    let other = crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "DBOO")
        .unwrap();
    assert!(decode::decode(ESSEN, other).is_err());
    assert!(decode::decode(&ESSEN[..ESSEN.len() / 2], site()).is_err());
    let mut bad = ESSEN.to_vec();
    let header = bad.iter().position(|byte| *byte == 3).unwrap();
    let mut start = header + 1;
    while bad[start] == 3 {
        start += 1;
    }
    // First ray, after marker, azimuth, elevation and its first gate.
    bad[start + 8..start + 10].copy_from_slice(&0x1081_u16.to_le_bytes());
    assert!(decode::decode(&bad, site()).is_err());
}

#[test]
fn clutter_flag_is_not_presented_as_a_measurement() {
    let mut data = ESSEN.to_vec();
    let header = data.iter().position(|byte| *byte == 3).unwrap();
    let mut start = header + 1;
    while data[start] == 3 {
        start += 1;
    }
    data[start + 6..start + 8].copy_from_slice(&0x8043_u16.to_le_bytes());
    let sweep = decode::decode(&data, site()).unwrap();
    assert!(sweep.radials[0].values[0].is_nan());
    assert_eq!(sweep.radials[0].values[3], -25.0);
}

#[test]
fn listing_uses_exact_station_filenames_and_utc_times() {
    let html = br#"<a href="raa00-dx_10410-2611090255-ess---bin">x</a>
        <a href="raa00-dx_10410-latest-ess---bin">latest</a>
        <a href="../raa00-dx_10410-2611090300-ess---bin">unsafe</a>
        <a href="raa00-dx_10132-2611090300-boo---bin">other station</a>
        <a href="raa00-dx_10410-2611090300-ess---bin">x</a>"#;
    let scans = parse_listing(html, "10410", "ess");
    assert_eq!(scans.len(), 2);
    assert_eq!(scans[0].0, instant("2026-11-09T02:55:00Z"));
    assert_eq!(scans[1].0, instant("2026-11-09T03:00:00Z"));
}

#[test]
fn frame_rejects_wrong_time_and_keeps_numeric_lookup() {
    let valid = instant("2026-11-09T02:55:00Z");
    let now = instant("2026-11-09T03:00:00Z");
    assert!(make_frame(
        site(),
        ESSEN,
        now,
        now,
        now,
        "https://example.test".into(),
        time::Duration::minutes(10)
    )
    .is_err());
    let frame = make_frame(
        site(),
        ESSEN,
        valid,
        now,
        now,
        "https://example.test".into(),
        time::Duration::minutes(10),
    )
    .unwrap();
    let delayed = valid + time::Duration::minutes(15);
    assert!(make_frame(
        site(),
        ESSEN,
        valid,
        delayed,
        delayed,
        "https://example.test".into(),
        time::Duration::minutes(20)
    )
    .is_ok());
    assert!(matches!(
        make_frame(
            site(),
            ESSEN,
            valid,
            delayed,
            delayed,
            "https://example.test".into(),
            time::Duration::minutes(10)
        ),
        Err(Error::ScanMissing)
    ));
    assert_eq!(frame.elevation_angle, None);
    assert_eq!(frame.valid_time.as_deref(), Some("2026-11-09T02:55:00Z"));
    let png = STANDARD.decode(frame.png_base64).unwrap();
    let reader = png::Decoder::new(std::io::Cursor::new(png))
        .read_info()
        .unwrap();
    let info = reader.info();
    let metadata: serde_json::Value = serde_json::from_str(
        &info
            .uncompressed_latin1_text
            .iter()
            .find(|chunk| chunk.keyword == "sharppy-radar")
            .unwrap()
            .text,
    )
    .unwrap();
    assert!(metadata["values"]
        .as_array()
        .unwrap()
        .contains(&serde_json::json!(1.0)));
    assert!(metadata["values"]
        .as_array()
        .unwrap()
        .contains(&serde_json::json!(-32.5)));
}

#[tokio::test]
async fn rejects_unsupported_products_and_elevations_without_network() {
    for request in [
        Request {
            site_id: Some("DESS".into()),
            product: Some("velocity".into()),
            ..Default::default()
        },
        Request {
            site_id: Some("DESS".into()),
            tilt: Some("1".into()),
            ..Default::default()
        },
    ] {
        assert!(matches!(
            RadarClient::default()
                .fetch(request, instant("2026-11-09T03:00:00Z"))
                .await,
            Err(Error::Invalid(_))
        ));
    }
}

#[tokio::test]
#[ignore = "manual live-provider validation"]
async fn live_dwd_reflectivity() {
    let now = OffsetDateTime::now_utc();
    let frame = client()
        .fetch(
            Request {
                site_id: Some("DESS".into()),
                ..Default::default()
            },
            now,
        )
        .await
        .unwrap()
        .unwrap();
    assert!(frame.byte_length > 1000);
    let valid_time = frame.valid_time.as_deref().unwrap();
    assert!(instant(valid_time) <= now);
    eprintln!(
        "{} {} {} {}bytes",
        frame.site_id, frame.product, valid_time, frame.byte_length
    );
}

use super::*;

fn instant(value: &str) -> OffsetDateTime {
    OffsetDateTime::parse(value, &Rfc3339).unwrap()
}

#[test]
fn filenames_choose_only_preceding_scans_from_the_requested_site() {
    let html = br#"<a href="/polling/ABRD/ABRD_20261029_113000.ar2v">x</a>
    <a href="ABRD_20261029_113500.ar2v">x</a>
    <a href="KTLX_20261029_113100.ar2v">x</a>
    <a href="ABRD_bad.ar2v">x</a>
    <a href="ABRD_20261029_113000.ar2v">duplicate</a>"#;
    let scans = parse_listing(html, "ABRD");
    assert_eq!(scans.len(), 2);
    assert_eq!(
        candidate_scans(&scans, instant("2026-10-29T11:34:00Z"))
            .next()
            .unwrap()
            .1,
        "ABRD_20261029_113000.ar2v"
    );
    assert!(candidate_scans(&scans, instant("2026-10-29T11:29:00Z"))
        .next()
        .is_none());
    assert!(candidate_scans(&scans, instant("2026-10-29T11:56:00Z"))
        .next()
        .is_none());
}

#[tokio::test]
async fn rejects_unsupported_australian_products_and_cuts_without_network() {
    let now = instant("2026-10-29T11:34:00Z");
    let client = RadarClient::default();
    for request in [
        Request {
            site_id: Some("ABRD".into()),
            product: Some("correlation-coefficient".into()),
            ..Default::default()
        },
        Request {
            site_id: Some("ABRD".into()),
            tilt: Some("3".into()),
            ..Default::default()
        },
        Request {
            site_id: Some("ABRD".into()),
            valid_time: Some("2026-10-28T00:00:00Z".into()),
            ..Default::default()
        },
    ] {
        assert!(matches!(
            client.fetch(request, now).await,
            Err(Error::Invalid(_))
        ));
    }
    assert!(client
        .fetch(
            Request {
                site_id: Some("KTLX".into()),
                ..Default::default()
            },
            now
        )
        .await
        .unwrap()
        .is_none());
}

#[test]
fn indexed_image_preserves_gate_values_and_transparent_sectors() {
    let site = crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "ABRD")
        .unwrap();
    let sweep = decode::Sweep {
        site_id: site.id.clone(),
        valid_time: "2026-10-29T11:30:00Z".into(),
        elevation_angle: 0.5,
        radials: vec![
            decode::Radial {
                azimuth: 0.0,
                width: 90.0,
                first_gate_m: 25_000.0,
                gate_spacing_m: 50_000.0,
                values: vec![30.0],
            },
            decode::Radial {
                azimuth: 90.0,
                width: 90.0,
                first_gate_m: 25_000.0,
                gate_spacing_m: 50_000.0,
                values: vec![60.0],
            },
        ],
    };
    let palette: Palette = serde_json::from_str(include_str!(
        "../../resources/australian_reflectivity.palette.json"
    ))
    .unwrap();
    let (bounds, png) = render::render(&sweep, site, &palette).unwrap();
    assert!(
        bounds.west < site.lon
            && bounds.east > site.lon
            && bounds.south < site.lat
            && bounds.north > site.lat
    );
    let mut reader = png::Decoder::new(std::io::Cursor::new(&png))
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
    assert_eq!(
        &metadata["values"].as_array().unwrap()[..4],
        &[
            serde_json::Value::Null,
            serde_json::Value::Null,
            serde_json::json!(30.0),
            serde_json::json!(60.0)
        ]
    );
    let mut data = vec![0; reader.output_buffer_size().unwrap()];
    let image = reader.next_frame(&mut data).unwrap();
    let width = image.width as usize;
    assert_eq!(data[width / 2], 2, "north uses the 30 dBZ radial");
    assert_eq!(
        data[(image.height as usize / 2) * width + width - 2],
        3,
        "east uses the 60 dBZ radial"
    );
    assert_eq!(
        data[(image.height as usize - 2) * width + width / 2],
        0,
        "unobserved south remains transparent"
    );
    assert_eq!(metadata["values"].as_array().unwrap().len(), 256);
}

#[tokio::test]
#[ignore = "manual live-provider validation"]
async fn live_australian_reflectivity() {
    let site_id = std::env::var("SHARPPY_RADAR_TEST_SITE").unwrap_or_else(|_| "ABRD".into());
    let product =
        std::env::var("SHARPPY_RADAR_TEST_PRODUCT").unwrap_or_else(|_| "reflectivity".into());
    let now = OffsetDateTime::now_utc();
    let frame = client()
        .fetch(
            Request {
                site_id: Some(site_id),
                product: Some(product),
                ..Default::default()
            },
            now,
        )
        .await
        .unwrap()
        .unwrap();
    assert!(frame.byte_length > 1_000);
    assert!(frame.elevation_angle.is_finite());
    let actual = instant(&frame.valid_time);
    assert!(actual <= now && actual >= now - time::Duration::minutes(10));
    if let Ok(output) = std::env::var("SHARPPY_RADAR_TEST_PNG") {
        std::fs::write(output, STANDARD.decode(&frame.png_base64).unwrap()).unwrap();
    }
    eprintln!(
        "{} {} {} {}deg {}bytes",
        frame.site_id, frame.product, frame.valid_time, frame.elevation_angle, frame.byte_length
    );
}

#[test]
fn velocity_image_keeps_folded_missing_and_exact_unit_calibration() {
    let site = crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "ABRD")
        .unwrap();
    let sweep = decode::Sweep {
        site_id: site.id.clone(),
        valid_time: "2026-10-29T11:30:00Z".into(),
        elevation_angle: 0.5,
        radials: vec![
            decode::Radial {
                azimuth: 0.0,
                width: 90.0,
                first_gate_m: 25_000.0,
                gate_spacing_m: 50_000.0,
                values: vec![10.0],
            },
            decode::Radial {
                azimuth: 90.0,
                width: 90.0,
                first_gate_m: 25_000.0,
                gate_spacing_m: 50_000.0,
                values: vec![f32::INFINITY],
            },
        ],
    };
    let palette: Palette = serde_json::from_str(include_str!(
        "../../resources/australian_velocity.palette.json"
    ))
    .unwrap();
    let (_, png) = render::render(&sweep, site, &palette).unwrap();
    let mut reader = png::Decoder::new(std::io::Cursor::new(&png))
        .read_info()
        .unwrap();
    let info = reader.info();
    assert_eq!(&info.trns.as_ref().unwrap()[..2], &[0, 255]);
    assert_eq!(&info.palette.as_ref().unwrap()[3..6], &[128, 0, 128]);
    let metadata: serde_json::Value = serde_json::from_str(
        &info
            .uncompressed_latin1_text
            .iter()
            .find(|chunk| chunk.keyword == "sharppy-radar")
            .unwrap()
            .text,
    )
    .unwrap();
    assert_eq!(metadata["folded"], 1);
    assert!((metadata["values"][2].as_f64().unwrap() - 10.0 * 3600.0 / 1609.344).abs() < 1e-12);
    let mut data = vec![0; reader.output_buffer_size().unwrap()];
    let image = reader.next_frame(&mut data).unwrap();
    let width = image.width as usize;
    assert_eq!(data[width / 2], 2);
    assert_eq!(data[(image.height as usize / 2) * width + width - 2], 1);
    assert_eq!(data[(image.height as usize - 2) * width + width / 2], 0);
}

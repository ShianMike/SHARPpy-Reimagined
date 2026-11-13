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
        candidate_scans(
            &scans,
            instant("2026-10-29T11:34:00Z"),
            time::Duration::minutes(20)
        )
        .next()
        .unwrap()
        .1,
        "ABRD_20261029_113000.ar2v"
    );
    assert!(candidate_scans(
        &scans,
        instant("2026-10-29T11:29:00Z"),
        time::Duration::minutes(20)
    )
    .next()
    .is_none());
    assert!(candidate_scans(
        &scans,
        instant("2026-10-29T11:56:00Z"),
        time::Duration::minutes(20)
    )
    .next()
    .is_none());
    assert_eq!(
        parse_listing(
            br#"<a href="/polling/MF90/MF90_20261111_100445.ar2v">fr</a>
            <a href="/polling/MF90/dir.list">list</a>"#,
            "MF90"
        ),
        vec![(
            instant("2026-11-11T10:04:45Z"),
            "MF90_20261111_100445.ar2v".into()
        )]
    );
    assert_eq!(
        parse_listing(
            br#"<a href="/polling/SAPP/SAPP20261111_095308.ar2v">jp</a>"#,
            "SAPP"
        ),
        vec![(
            instant("2026-11-11T09:53:08Z"),
            "SAPP20261111_095308.ar2v".into()
        )]
    );
    assert_eq!(
        parse_listing(
            br#"<a href="/polling/FLER/FLER_20261111100000.ar2v">it</a>"#,
            "FLER"
        ),
        vec![(
            instant("2026-11-11T10:00:00Z"),
            "FLER_20261111100000.ar2v".into()
        )]
    );
    assert_eq!(
        parse_listing(
            br#"<a href="/polling/1852/1852CAN20261111_095643_V06">mx</a>
            <a href="/polling/1852/dir.list">list</a>"#,
            "1852"
        ),
        vec![(
            instant("2026-11-11T09:56:43Z"),
            "1852CAN20261111_095643_V06".into()
        )]
    );
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
        Request {
            site_id: Some("MF90".into()),
            product: Some("velocity".into()),
            ..Default::default()
        },
        Request {
            site_id: Some("MF90".into()),
            tilt: Some("1".into()),
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
                elevation_angle: None,
                first_gate_m: 25_000.0,
                gate_spacing_m: 50_000.0,
                values: vec![30.0],
            },
            decode::Radial {
                azimuth: 90.0,
                width: 90.0,
                elevation_angle: None,
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
    assert!(frame.elevation_angle.is_some_and(f64::is_finite));
    let valid_time = frame.valid_time.as_deref().unwrap();
    let actual = instant(valid_time);
    assert!(actual <= now && actual >= now - time::Duration::minutes(10));
    if let Ok(output) = std::env::var("SHARPPY_RADAR_TEST_PNG") {
        std::fs::write(output, STANDARD.decode(&frame.png_base64).unwrap()).unwrap();
    }
    eprintln!(
        "{} {} {} {}deg {}bytes",
        frame.site_id,
        frame.product,
        valid_time,
        frame.elevation_angle.unwrap(),
        frame.byte_length
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
                elevation_angle: None,
                first_gate_m: 25_000.0,
                gate_spacing_m: 50_000.0,
                values: vec![10.0],
            },
            decode::Radial {
                azimuth: 90.0,
                width: 90.0,
                elevation_angle: None,
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

#[test]
fn mrrl_noumea_sample_decodes_and_renders_with_licence_ouverte_metadata() {
    let bytes = include_bytes!("../../resources/mrrl_mf94_20261111_100933.ar2v");
    let site = crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "MF94")
        .unwrap();
    let other = crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "MF90")
        .unwrap();
    assert!(make_frame(
        other,
        "MF94_20261111_100933.ar2v",
        bytes,
        "reflectivity",
        instant("2026-11-11T10:12:00Z"),
        instant("2026-11-11T10:12:00Z"),
        time::Duration::minutes(10),
    )
    .is_err());
    assert!(matches!(
        make_frame(
            site,
            "MF94_20261111_100933.ar2v",
            bytes,
            "reflectivity",
            instant("2026-11-11T10:25:00Z"),
            instant("2026-11-11T10:25:00Z"),
            time::Duration::minutes(10),
        ),
        Err(Error::ScanMissing)
    ));
    assert!(make_frame(
        site,
        "MF94_20261111_100933.ar2v",
        bytes,
        "reflectivity",
        instant("2026-11-11T10:25:00Z"),
        instant("2026-11-11T10:25:00Z"),
        time::Duration::minutes(20)
    )
    .is_ok());
    let frame = make_frame(
        site,
        "MF94_20261111_100933.ar2v",
        bytes,
        "reflectivity",
        instant("2026-11-11T10:12:00Z"),
        instant("2026-11-11T10:12:00Z"),
        time::Duration::minutes(10),
    )
    .unwrap();
    assert_eq!(frame.site_id, "MF94");
    assert_eq!(frame.product, "reflectivity");
    assert_eq!(frame.label, "Reflectivity");
    assert_eq!(frame.units, "dBZ");
    assert_eq!(frame.valid_time.as_deref(), Some("2026-11-11T10:09:33Z"));
    assert_eq!(frame.update_interval_s, 300.0);
    assert_eq!(frame.palette.as_ref().unwrap().floor, Some(0.0));
    assert_eq!(
        frame.attribution,
        "Météo-France · Licence Ouverte 2.0 (data.gouv.fr/pages/legal/licences/etalab-2-0) · converted by MRRL, rendered by SHARPpy Reimagined"
    );
    assert_eq!(
        frame.source_url,
        "https://feeds.mrrl.net/polling/MF94/MF94_20261111_100933.ar2v"
    );
    assert!((frame.elevation_angle.unwrap() - 0.03).abs() < 0.001);
    let png = STANDARD.decode(&frame.png_base64).unwrap();
    let reader = png::Decoder::new(std::io::Cursor::new(&png))
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
    let values = metadata["values"].as_array().unwrap();
    assert_eq!(values[2].as_f64(), Some(-9.5));
    let trns = info.trns.as_ref().unwrap();
    assert_eq!(trns[2], 0, "sub-0 dBZ gates stay transparent visually");
}

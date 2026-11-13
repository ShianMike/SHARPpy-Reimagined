use super::*;
use crate::radar_sites::RadarSite;
use serde_json::json;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

fn site(id: &str, lat: f64, lon: f64) -> RadarSite {
    RadarSite {
        id: id.into(),
        lat,
        lon,
        half_span_deg: 0.0,
        products: vec!["reflectivity".into()],
        name: None,
        country: None,
        wmo: None,
        provider: Some("OPERA".into()),
    }
}

#[test]
fn decodes_norwegian_volume_calibration_geometry_and_time() {
    let bytes = include_bytes!("../../resources/opera_nohur_20261109_0330.h5");
    let target = OffsetDateTime::parse("2026-11-09T03:31:00Z", &Rfc3339).unwrap();
    let sweep =
        decode::decode(bytes, &site("0-578-0-nohur", 59.627083, 10.564472), target).unwrap();
    assert_eq!(sweep.valid_time, "2026-11-09T03:30:07Z");
    assert_eq!(sweep.elevation_angle, 0.5);
    assert_eq!(sweep.radials.len(), 720);
    assert_eq!(sweep.radials[0].azimuth, 0.25);
    assert_eq!(sweep.radials[0].first_gate_m, 125.0);
    assert_eq!(sweep.radials[0].gate_spacing_m, 250.0);
    assert_eq!(&sweep.radials[0].values[..4], &[29.5, 29.5, 30.5, 28.0]);
}

#[test]
fn decodes_finnish_scan_with_source_azimuth_arrays_and_multiple_moments() {
    let bytes = include_bytes!("../../resources/opera_fianj_20261109_0345.h5");
    let target = OffsetDateTime::parse("2026-11-09T03:46:00Z", &Rfc3339).unwrap();
    let sweep =
        decode::decode(bytes, &site("0-246-0-fianj", 60.903870, 27.108060), target).unwrap();
    assert_eq!(sweep.valid_time, "2026-11-09T03:45:27Z");
    assert_eq!(sweep.elevation_angle, 0.3);
    assert_eq!(sweep.radials.len(), 360);
    assert!(sweep
        .radials
        .iter()
        .any(|ray| ray.azimuth < 0.2 && (ray.width - 0.8569).abs() < 0.001));
    let north = sweep.radials.iter().find(|ray| ray.azimuth < 0.2).unwrap();
    // The first DBZH bins are undetect (zero); the colocated TH bins are not.
    assert!(north.values[..8].iter().all(|value| value.is_nan()));
    assert!(sweep
        .radials
        .iter()
        .any(|ray| ray.values.iter().any(|value| value.is_finite())));
}

#[test]
fn rejects_wrong_site_and_future_scans() {
    let bytes = include_bytes!("../../resources/opera_nohur_20261109_0330.h5");
    let target = OffsetDateTime::parse("2026-11-09T03:31:00Z", &Rfc3339).unwrap();
    assert!(decode::decode(bytes, &site("0-578-0-other", 59.627083, 10.564472), target).is_err());
    let early = OffsetDateTime::parse("2026-11-09T03:29:00Z", &Rfc3339).unwrap();
    assert!(decode::decode(bytes, &site("0-578-0-nohur", 59.627083, 10.564472), early).is_err());
}

#[test]
fn catalogue_filters_duplicate_german_sites_and_invalid_coordinates() {
    let feature = |id: &str, lon: f64, lat: f64| {
        json!({
            "id": id, "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": "Radar", "parameter-name": ["DBZH:scan"]}
        })
    };
    let bytes = serde_json::to_vec(&json!({"features": [
        feature("0-578-0-nohur", 10.5, 59.6), feature("0-578-0-nohur", 10.5, 59.6),
        feature("0-276-0-deess", 6.9, 51.4), feature("0-578-0-noand", 500.0, 59.6)
    ]}))
    .unwrap();
    let sites = parse_sites(&bytes).unwrap();
    assert_eq!(sites.len(), 1);
    assert_eq!(sites[0].id, "0-578-0-nohur");
    assert_eq!(sites[0].provider.as_deref(), Some("OPERA"));
}

#[test]
fn catalogue_uses_reported_places_or_verified_names_for_code_only_sites() {
    let feature = |id: &str, name: Option<&str>| {
        json!({
            "id": id, "geometry": {"type": "Point", "coordinates": [10.5, 59.6]},
            "properties": {"name": name, "parameter-name": ["DBZH:scan"]}
        })
    };
    let bytes = serde_json::to_vec(&json!({"features": [
        feature("0-578-0-nohur", Some("[nohur]")),
        feature("0-578-0-noand", Some("NOAND")),
        feature("0-246-0-fianj", Some("[fianj] Anjalankoski")),
        feature("0-578-0-nozzz", None)
    ]}))
    .unwrap();
    let sites = parse_sites(&bytes).unwrap();
    assert_eq!(sites[0].name.as_deref(), Some("Hurum"));
    assert_eq!(sites[1].name.as_deref(), Some("Andøya"));
    assert_eq!(sites[2].name.as_deref(), Some("Anjalankoski"));
    assert_eq!(sites[3].id, "0-578-0-nozzz");
    assert_eq!(sites[3].name, None);
}

#[test]
fn listing_checks_license_and_rejects_untrusted_download_links() {
    let station = site("0-578-0-nohur", 59.627083, 10.564472);
    let coverage = |license: &str, links: Vec<&str>| {
        json!({
            "parameters": {"DBZH:scan": {}}, "metocean:license": license,
            "domain": {"axes": {"z": {"values": [0.5]}, "t": {"values": ["2026-11-09T03:25:00Z", "2026-11-09T03:30:00Z"]}}},
            "links": links.into_iter().map(|href| json!({"type": "application/x-odim", "href": href})).collect::<Vec<_>>()
        })
    };
    let valid = "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/11/09/NO/nohur/PVOL/nohur@20261109T0330@0.5@DBZH.h5";
    let wrong_host = "https://example.com/openradar-24h/2026/11/09/NO/nohur/PVOL/nohur@20261109T0330@0.5@DBZH.h5";
    let wrong_site = "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/11/09/NO/noand/PVOL/noand@20261109T0330@0.5@DBZH.h5";
    let bytes = serde_json::to_vec(
        &json!({"coverages": [coverage(LICENSE, vec![valid, valid, wrong_host, wrong_site])]}),
    )
    .unwrap();
    let scans = parse_scans(&bytes, &station).unwrap();
    assert_eq!(scans.len(), 1);
    assert_eq!(scans[0].url, valid);
    assert_eq!(scans[0].interval_s, 300.0);
    assert_eq!(scans[0].attribution, CREDIT_CC);
    let restricted =
        serde_json::to_vec(&json!({"coverages": [coverage("restricted", vec![valid])]})).unwrap();
    assert!(matches!(
        parse_scans(&restricted, &station),
        Err(Error::Fetch(_))
    ));
    let mut french = site("0-250-0-frabb", 50.13595, 1.8347);
    french.country = Some("FR".into());
    let french_url = "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/11/09/FR/frabb/SCAN/frabb@20261109T0330@0.5@DBZH.h5";
    let french_listing =
        serde_json::to_vec(&json!({"coverages": [coverage(ETALAB, vec![french_url])]})).unwrap();
    assert_eq!(
        parse_scans(&french_listing, &french).unwrap()[0].attribution,
        CREDIT_FR
    );
    french.country = Some("NO".into());
    assert!(matches!(
        parse_scans(&french_listing, &french),
        Err(Error::Fetch(_))
    ));
}

#[tokio::test]
#[ignore = "requires the live OPERA catalogue and radar service"]
async fn live_opera_scans_are_available() {
    let client = RadarClient::default();
    let sites = client.sites().await;
    eprintln!("OPERA sites: {}", sites.len());
    assert!(sites.len() > 50);
    for id in [
        "0-578-0-nohur",
        "0-246-0-fianj",
        "0-250-0-frabb",
        "0-191-0-hrbil",
        "0-203-0-czbrd",
        "0-208-0-dkbor",
        "0-348-0-hubud",
        "0-372-0-iedub",
        "0-528-0-nldhl",
        "0-56-0-behel",
        "0-703-0-skjav",
        "0-756-0-chalb",
        "0-724-0-esahr",
        "0-752-0-seang",
    ] {
        let request = Request {
            site_id: Some(id.into()),
            product: Some("reflectivity".into()),
            ..Default::default()
        };
        let frame = client
            .fetch(request, OffsetDateTime::now_utc())
            .await
            .unwrap()
            .unwrap();
        eprintln!(
            "{}: {}, {} bytes",
            frame.site_id,
            frame.valid_time.as_deref().unwrap_or("untimed"),
            frame.byte_length
        );
        assert!(frame.byte_length > 0);
    }
}

#[test]
fn high_precision_sweeps_preserve_exact_values_in_compressed_metadata() {
    let station = site("0-752-0-seang", 56.3675, 12.8517);
    let radials = (0..360)
        .map(|ray| crate::polar_radar::Radial {
            azimuth: ray as f64 + 0.5,
            width: 1.0,
            elevation_angle: Some(0.5),
            first_gate_m: 250.0,
            gate_spacing_m: 500.0,
            values: vec![-25.0 + ray as f32 * 0.17, 12.34],
        })
        .collect();
    let sweep = crate::polar_radar::Sweep {
        site_id: station.id.clone(),
        valid_time: "2026-11-09T04:15:34Z".into(),
        elevation_angle: 0.5,
        radials,
    };
    let mut palette: Palette = serde_json::from_str(include_str!(
        "../../resources/australian_reflectivity.palette.json"
    ))
    .unwrap();
    palette.floor = Some(0.0);
    let (_, png) = polar_radar::render(&sweep, &station, &palette).unwrap();
    let reader = png::Decoder::new(std::io::Cursor::new(png))
        .read_info()
        .unwrap();
    let text = reader
        .info()
        .compressed_latin1_text
        .iter()
        .find(|chunk| chunk.keyword == "sharppy-radar")
        .unwrap()
        .get_text()
        .unwrap();
    let metadata: serde_json::Value = serde_json::from_str(&text).unwrap();
    let values = metadata["values"].as_array().unwrap();
    assert_eq!(values.len(), 363);
    assert!(values
        .iter()
        .any(|v| v.as_f64().is_some_and(|n| (n - 12.34).abs() < 1e-5)));
    let codes = STANDARD
        .decode(metadata["codes"].as_str().unwrap())
        .unwrap();
    assert_eq!(codes.len(), 64 * 64 * 2);
}

use super::*;
use bzip2::write::BzEncoder;
use bzip2::Compression;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread::{self, JoinHandle};
use tempfile_dir::TempDirGuard;
use time::Duration as TimeDuration;

mod tempfile_dir {
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicUsize, Ordering};

    static SEQ: AtomicUsize = AtomicUsize::new(0);

    pub(super) struct TempDirGuard(PathBuf);

    impl TempDirGuard {
        pub(super) fn new(prefix: &str) -> Self {
            let id = SEQ.fetch_add(1, Ordering::Relaxed);
            let dir = std::env::temp_dir().join(format!("{prefix}-{}-{id}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            std::fs::create_dir_all(&dir).unwrap();
            Self(dir)
        }

        pub(super) fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TempDirGuard {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
}

struct Reply {
    status: u16,
    body: Vec<u8>,
}

struct Server {
    base: String,
    calls: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Server {
    fn new(reply: impl Fn(&str) -> Reply + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let calls = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let (recorded, stopping, reply) = (calls.clone(), stop.clone(), Arc::new(reply));
        let thread = thread::spawn(move || {
            let mut connections = Vec::new();
            for connection in listener.incoming() {
                let mut stream = connection.unwrap();
                if stopping.load(Ordering::SeqCst) {
                    break;
                }
                let (recorded, reply) = (recorded.clone(), reply.clone());
                connections.push(thread::spawn(move || {
                    stream
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let mut reader = BufReader::new(&stream);
                    let mut request = String::new();
                    reader.read_line(&mut request).unwrap();
                    let path = request.split_whitespace().nth(1).unwrap().to_owned();
                    loop {
                        let mut header = String::new();
                        if reader.read_line(&mut header).unwrap() == 0 || header == "\r\n" {
                            break;
                        }
                    }
                    recorded.lock().unwrap().push(path.clone());
                    let reply = reply(&path);
                    let headers = format!(
                        "HTTP/1.1 {} Test\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
                        reply.status,
                        reply.body.len()
                    );
                    let _ = stream.write_all(headers.as_bytes());
                    let _ = stream.write_all(&reply.body);
                }));
            }
            for connection in connections {
                connection.join().unwrap();
            }
        });
        Self {
            base,
            calls,
            stop,
            thread: Some(thread),
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = std::net::TcpStream::connect(self.base.trim_start_matches("http://"));
        if let Some(thread) = self.thread.take() {
            thread.join().unwrap();
        }
    }
}

fn instant(value: &str) -> OffsetDateTime {
    OffsetDateTime::parse(value, &Rfc3339).unwrap()
}

fn volume_time_fields(when: OffsetDateTime) -> (u32, u32) {
    let epoch_nanos = when.unix_timestamp_nanos();
    let day = (epoch_nanos / 86_400_000_000_000) as u32 + 1;
    let millis = ((epoch_nanos % 86_400_000_000_000) / 1_000_000) as u32;
    (day, millis)
}

fn build_vcp_record(angles: &[f64]) -> Vec<u8> {
    let mut body = vec![0_u8; 22 + angles.len() * 46];
    let hw = ((body.len() + 16) / 2) as u16;
    body[0..2].copy_from_slice(&hw.to_be_bytes());
    body[6..8].copy_from_slice(&(angles.len() as u16).to_be_bytes());
    for (i, &angle) in angles.iter().enumerate() {
        let encoded = ((angle * 65_536.0 / 360.0).round() as u16).to_be_bytes();
        body[22 + i * 46..24 + i * 46].copy_from_slice(&encoded);
    }
    let mut record = vec![0_u8; 2432];
    record[12..14].copy_from_slice(&hw.to_be_bytes());
    record[15] = 5;
    record[28..28 + body.len()].copy_from_slice(&body);
    record
}

fn build_moment_block(
    name: &[u8; 3],
    first_gate_m: u16,
    gate_spacing_m: u16,
    scale: f32,
    offset: f32,
    gates: &[u8],
) -> Vec<u8> {
    let mut block = Vec::with_capacity(28 + gates.len());
    block.push(b'D');
    block.extend_from_slice(name);
    block.extend_from_slice(&0_u32.to_be_bytes());
    block.extend_from_slice(&(gates.len() as u16).to_be_bytes());
    block.extend_from_slice(&first_gate_m.to_be_bytes());
    block.extend_from_slice(&gate_spacing_m.to_be_bytes());
    block.extend_from_slice(&0_u16.to_be_bytes());
    block.extend_from_slice(&0_i16.to_be_bytes());
    block.push(0);
    block.push(8);
    block.extend_from_slice(&scale.to_be_bytes());
    block.extend_from_slice(&offset.to_be_bytes());
    block.extend_from_slice(gates);
    block
}

struct Msg31Spec<'a> {
    site: &'a [u8; 4],
    when: OffsetDateTime,
    el_num: u8,
    status: u8,
    az_num: u16,
    azimuth: f32,
    wobbly_el_angle: f32,
    lat: f32,
    lon: f32,
}

fn build_msg31_record(spec: &Msg31Spec<'_>, moments: &[Vec<u8>]) -> Vec<u8> {
    let Msg31Spec {
        site,
        when,
        el_num,
        status,
        az_num,
        azimuth,
        wobbly_el_angle,
        lat,
        lon,
    } = *spec;
    let (day, millis) = volume_time_fields(when);
    let block_count = 1 + moments.len();
    let header_end = 32 + block_count * 4;
    let mut rvol = vec![0_u8; 16];
    rvol[0..4].copy_from_slice(b"RVOL");
    rvol[4..6].copy_from_slice(&16_u16.to_be_bytes());
    rvol[8..12].copy_from_slice(&lat.to_be_bytes());
    rvol[12..16].copy_from_slice(&lon.to_be_bytes());

    let mut blocks = vec![rvol];
    blocks.extend(moments.iter().cloned());
    let mut payload = Vec::new();
    payload.extend_from_slice(site);
    payload.extend_from_slice(&millis.to_be_bytes());
    payload.extend_from_slice(&(day as u16).to_be_bytes());
    payload.extend_from_slice(&az_num.to_be_bytes());
    payload.extend_from_slice(&azimuth.to_be_bytes());
    payload.push(0);
    payload.push(0);
    let total_len = header_end + blocks.iter().map(Vec::len).sum::<usize>();
    payload.extend_from_slice(&(total_len as u16).to_be_bytes());
    payload.push(1); // 0.5 deg spacing
    payload.push(status);
    payload.push(el_num);
    payload.push(0);
    payload.extend_from_slice(&wobbly_el_angle.to_be_bytes());
    payload.push(0);
    payload.push(0);
    payload.extend_from_slice(&(block_count as u16).to_be_bytes());
    let mut ptr = header_end;
    for block in &blocks {
        payload.extend_from_slice(&(ptr as u32).to_be_bytes());
        ptr += block.len();
    }
    for block in blocks {
        payload.extend_from_slice(&block);
    }
    if payload.len() % 2 == 1 {
        payload.push(0);
    }
    let halfwords = ((16 + payload.len()) / 2) as u16;
    let mut record = vec![0_u8; 28 + payload.len()];
    record[12..14].copy_from_slice(&halfwords.to_be_bytes());
    record[15] = 31;
    record[24..26].copy_from_slice(&1_u16.to_be_bytes());
    record[26..28].copy_from_slice(&1_u16.to_be_bytes());
    record[28..].copy_from_slice(&payload);
    record
}

fn build_volume(
    site_id: &str,
    lat: f32,
    lon: f32,
    when: OffsetDateTime,
    include_rho: bool,
) -> Vec<u8> {
    let site_bytes: [u8; 4] = site_id.as_bytes().try_into().unwrap();
    let (day, millis) = volume_time_fields(when);
    let mut raw_records = Vec::new();
    raw_records.extend_from_slice(&build_vcp_record(&[0.5, 1.3]));
    let ref_gates = [0_u8, 1, 2, 136]; // missing, folded, -32.0 dBZ, 35.0 dBZ
    let rho_gates = [0_u8, 90, 240];
    for (sweep_idx, &wobble_angle) in [0.8_f32, 1.6_f32].iter().enumerate() {
        for (ray_idx, &az) in [0.25_f32, 0.75_f32].iter().enumerate() {
            let mut moments = vec![build_moment_block(b"REF", 2125, 250, 2.0, 66.0, &ref_gates)];
            if include_rho && sweep_idx == 0 {
                moments.push(build_moment_block(
                    b"RHO", 1125, 250, 300.0, -60.5, &rho_gates,
                ));
            }
            let status = if ray_idx == 0 { 0 } else { 2 };
            raw_records.extend_from_slice(&build_msg31_record(
                &Msg31Spec {
                    site: &site_bytes,
                    when,
                    el_num: (sweep_idx + 1) as u8,
                    status,
                    az_num: (ray_idx + 1) as u16,
                    azimuth: az,
                    wobbly_el_angle: wobble_angle,
                    lat,
                    lon,
                },
                &moments,
            ));
        }
    }
    let mut encoder = BzEncoder::new(Vec::new(), Compression::fast());
    encoder.write_all(&raw_records).unwrap();
    let compressed = encoder.finish().unwrap();

    let mut volume = Vec::with_capacity(24 + 4 + compressed.len());
    volume.extend_from_slice(b"AR2V0006.001");
    volume.extend_from_slice(&day.to_be_bytes());
    volume.extend_from_slice(&millis.to_be_bytes());
    volume.extend_from_slice(&site_bytes);
    volume.extend_from_slice(&(compressed.len() as i32).to_be_bytes());
    volume.extend_from_slice(&compressed);
    volume
}

fn s3_listing(keys: &[(&str, usize)]) -> Vec<u8> {
    let mut xml = String::from(
        "<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><IsTruncated>false</IsTruncated>",
    );
    for (key, size) in keys {
        xml.push_str(&format!(
            "<Contents><Key>{key}</Key><Size>{size}</Size></Contents>"
        ));
    }
    xml.push_str("</ListBucketResult>");
    xml.into_bytes()
}

#[test]
fn real_ar2v_volume_matches_metpy_reference_angles_and_values() {
    let site = crate::radar_sites::sites()
        .iter()
        .find(|s| s.id == "MF94")
        .unwrap();
    let bytes = include_bytes!("../../resources/mrrl_mf94_20261111_100933.ar2v");
    let decoded = decode_volume(bytes, site, None).unwrap();
    let cut = decoded.cuts.get(&(MomentKind::Reflectivity, "a")).unwrap();
    assert!((cut.sweep.elevation_angle - 1.0107421875).abs() < 1e-9);
    assert_eq!(cut.sweep.radials.len(), 720);
    assert!((cut.sweep.radials[0].azimuth - 0.0).abs() < 1e-6);
    assert!((cut.sweep.radials[0].values[3] - 11.5).abs() < 1e-6);
}

#[test]
fn native_decode_uses_commanded_vcp_angles_and_preserves_missing_folded_and_rho() {
    let site = crate::radar_sites::sites()
        .iter()
        .find(|s| s.id == "KTLX")
        .unwrap();
    let when = instant("2026-10-17T11:55:00Z");
    let payload = build_volume("KTLX", site.lat as f32, site.lon as f32, when, true);
    let decoded = decode_volume(&payload, site, Some(when)).unwrap();
    assert!(decoded.cuts.contains_key(&(MomentKind::Reflectivity, "0")));
    assert!(decoded.cuts.contains_key(&(MomentKind::Reflectivity, "1")));
    assert!(decoded
        .cuts
        .contains_key(&(MomentKind::CorrelationCoefficient, "0")));
    assert!(!decoded
        .cuts
        .contains_key(&(MomentKind::CorrelationCoefficient, "1")));

    let ref0 = decoded.cuts.get(&(MomentKind::Reflectivity, "0")).unwrap();
    assert!((ref0.sweep.elevation_angle - 0.5).abs() < 0.01);
    assert_eq!(ref0.sweep.radials[0].gate_spacing_m, 250.0);
    assert_eq!(ref0.sweep.radials[0].first_gate_m, 2125.0);
    assert!(ref0.sweep.radials[0].values[0].is_nan());
    assert_eq!(ref0.sweep.radials[0].values[1], f32::INFINITY);
    assert!((ref0.sweep.radials[0].values[2] - (-32.0)).abs() < 1e-5);
    assert!((ref0.sweep.radials[0].values[3] - 35.0).abs() < 1e-5);

    let wrong_site = crate::radar_sites::sites()
        .iter()
        .find(|s| s.id == "PGUA")
        .unwrap();
    assert!(decode_volume(&payload, wrong_site, Some(when)).is_err());
    assert!(decode_volume(&payload, site, Some(when - TimeDuration::hours(1))).is_err());
}

#[test]
fn listing_rejects_external_keys_bad_xml_and_incomplete_results() {
    let key = "2026/10/17/KTLX/KTLX20261017_115500_V06";
    let scans = parse_listing(
        &s3_listing(&[
            (key, 128),
            ("https://evil.test/volume", 128),
            ("2026/10/17/KTLX/../saved", 128),
            ("2026/10/17/KTLX/KTLX20261017_115500_V06", 0),
        ]),
        "2026/10/17/KTLX/",
        "KTLX",
    )
    .unwrap();
    assert_eq!(
        scans,
        vec![(instant("2026-10-17T11:55:00Z"), key.into(), 128)]
    );

    for bad in [
        b"<html>Error</html>".as_slice(),
        b"broken".as_slice(),
        b"<!DOCTYPE x><ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>"
            .as_slice(),
        b"<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>".as_slice(),
    ] {
        assert!(parse_listing(bad, "2026/10/17/KTLX/", "KTLX").is_err());
    }
}

#[tokio::test]
async fn live_uses_latest_completed_volume_even_when_publication_is_delayed() {
    let site = crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == "KTLX")
        .unwrap();
    let now = instant("2026-10-17T12:00:00Z");
    let when = instant("2026-10-17T11:41:00Z");
    let key = "2026/10/17/KTLX/KTLX20261017_114100_V06";
    let payload = build_volume("KTLX", site.lat as f32, site.lon as f32, when, true);
    let xml = s3_listing(&[
        ("2026/10/17/KTLX/KTLX20261017_113500_V06", payload.len()),
        (key, payload.len()),
        ("2026/10/17/KTLX/KTLX20261017_120100_V06", payload.len()),
    ]);
    let server = Server::new(move |path| Reply {
        status: 200,
        body: if path.starts_with("/?") {
            xml.clone()
        } else {
            payload.clone()
        },
    });
    let client = RadarClient {
        base: server.base.clone(),
        ..Default::default()
    };
    let request = Request {
        product: Some("reflectivity".into()),
        site_id: Some("KTLX".into()),
        tilt: Some("0".into()),
        ..Default::default()
    };
    let frame = client
        .fetch_site_with_pixels(request.clone(), now, site, 128, None)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(frame.actual_time, Some(when));
    assert!(frame.source_url.ends_with(key));
    let tilts = client
        .availability(site, "reflectivity", None, now, None)
        .await
        .unwrap();
    assert_eq!(
        tilts
            .tilts
            .iter()
            .find(|tilt| tilt.tilt == "0")
            .unwrap()
            .status,
        "available"
    );

    let timed = Request {
        valid_time: Some("2026-10-17T12:00:00Z".into()),
        ..request.clone()
    };
    assert!(matches!(
        client
            .fetch_site_with_pixels(timed, now, site, 128, None)
            .await,
        Err(Error::ScanMissing)
    ));
    assert!(matches!(
        client
            .fetch_site_with_pixels(request, now + TimeDuration::minutes(75), site, 128, None)
            .await,
        Err(Error::ScanMissing)
    ));
}

#[tokio::test]
async fn availability_and_frames_coalesce_volume_download_and_reuse_disk_cache() {
    let site = crate::radar_sites::sites()
        .iter()
        .find(|s| s.id == "KTLX")
        .unwrap();
    let now = instant("2026-10-17T12:00:00Z");
    let when = now - TimeDuration::minutes(5);
    let key = "2026/10/17/KTLX/KTLX20261017_115500_V06";
    let payload = Arc::new(build_volume(
        "KTLX",
        site.lat as f32,
        site.lon as f32,
        when,
        true,
    ));
    let volume_hits = Arc::new(AtomicUsize::new(0));
    let hits_clone = volume_hits.clone();
    let payload_clone = payload.clone();
    let xml = Arc::new(s3_listing(&[(key, payload.len())]));
    let xml_clone = xml.clone();
    let server = Server::new(move |path| {
        if path.starts_with("/?") {
            Reply {
                status: 200,
                body: (*xml_clone).clone(),
            }
        } else {
            hits_clone.fetch_add(1, Ordering::SeqCst);
            Reply {
                status: 200,
                body: (*payload_clone).clone(),
            }
        }
    });

    let temp = TempDirGuard::new("nexrad-level2-test");
    let client = RadarClient {
        base: server.base.clone(),
        ..Default::default()
    };

    let req0 = Request {
        product: Some("reflectivity".into()),
        site_id: Some("KTLX".into()),
        tilt: Some("0".into()),
        ..Default::default()
    };
    let req1 = Request {
        product: Some("reflectivity".into()),
        site_id: Some("KTLX".into()),
        tilt: Some("1".into()),
        ..Default::default()
    };
    let req_rho = Request {
        product: Some("correlation-coefficient".into()),
        site_id: Some("KTLX".into()),
        tilt: Some("0".into()),
        ..Default::default()
    };
    let (f0, f1, frho) = tokio::join!(
        client.fetch_site_with_pixels(req0.clone(), now, site, 128, Some(temp.path())),
        client.fetch_site_with_pixels(req1, now, site, 128, Some(temp.path())),
        client.fetch_site_with_pixels(req_rho, now, site, 128, Some(temp.path())),
    );
    let f0 = f0.unwrap().unwrap();
    let f1 = f1.unwrap().unwrap();
    let frho = frho.unwrap().unwrap();
    assert_eq!(volume_hits.load(Ordering::SeqCst), 1);
    assert!(f0.label.contains("Base Reflectivity"));
    assert!(f1.elevation_angle.unwrap() > f0.elevation_angle.unwrap());
    assert!(frho.label.contains("Correlation Coefficient"));
    assert!(f0.source_url.ends_with(key));

    let png_bytes = STANDARD.decode(&f0.png_base64).unwrap();
    let mut decoder = png::Decoder::new(std::io::Cursor::new(&png_bytes));
    decoder.set_transformations(png::Transformations::EXPAND);
    let reader = decoder.read_info().unwrap();
    let chunk = reader
        .info()
        .uncompressed_latin1_text
        .iter()
        .find(|entry| entry.keyword == "sharppy-radar")
        .unwrap();
    let meta: serde_json::Value = serde_json::from_str(&chunk.text).unwrap();
    assert_eq!(meta["folded"], serde_json::json!(1));
    assert_eq!(meta["values"][0], serde_json::Value::Null);
    assert_eq!(meta["values"][1], serde_json::Value::Null);

    let rho_png = STANDARD.decode(&frho.png_base64).unwrap();
    let rho_reader = png::Decoder::new(std::io::Cursor::new(&rho_png))
        .read_info()
        .unwrap();
    let rho_chunk = rho_reader
        .info()
        .uncompressed_latin1_text
        .iter()
        .find(|entry| entry.keyword == "sharppy-radar")
        .unwrap();
    let rho_meta: serde_json::Value = serde_json::from_str(&rho_chunk.text).unwrap();
    assert_eq!(rho_meta["folded"], serde_json::Value::Null);

    let tilts = client
        .availability(site, "reflectivity", None, now, Some(temp.path()))
        .await
        .unwrap();
    let available: Vec<&str> = tilts
        .tilts
        .iter()
        .filter(|row| row.status == "available")
        .map(|row| row.tilt.as_str())
        .collect();
    assert_eq!(available, vec!["0", "1"]);

    // Clear in-memory caches and verify disk cache avoids a second download.
    client.clear();
    client
        .fetch_site_with_pixels(req0.clone(), now, site, 128, Some(temp.path()))
        .await
        .unwrap();
    assert_eq!(volume_hits.load(Ordering::SeqCst), 1);

    // Corrupt the cached file on disk and verify it refetches cleanly.
    client.clear();
    let cached_file = temp.path().join("KTLX20261017_115500_V06.ar2");
    std::fs::write(&cached_file, vec![b'x'; payload.len()]).unwrap();
    client
        .fetch_site_with_pixels(req0, now, site, 128, Some(temp.path()))
        .await
        .unwrap();
    assert_eq!(volume_hits.load(Ordering::SeqCst), 2);
    assert!(!server.calls.lock().unwrap().is_empty());
}

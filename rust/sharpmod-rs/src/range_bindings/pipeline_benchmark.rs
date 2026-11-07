//! Opt-in full native HTTP-transfer-to-point comparison, using a complete local replay.
use super::*;
use crate::grib::{self, streaming};
use serde_json::{json, Value};
use std::time::Instant;

#[cfg(windows)]
fn process_facts() -> Value {
    #[repr(C)]
    struct Memory {
        size: u32,
        faults: u32,
        values: [usize; 9],
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut std::ffi::c_void;
        fn GetProcessTimes(
            process: *mut std::ffi::c_void,
            created: *mut u64,
            exited: *mut u64,
            kernel: *mut u64,
            user: *mut u64,
        ) -> i32;
        fn K32GetProcessMemoryInfo(
            process: *mut std::ffi::c_void,
            counters: *mut Memory,
            size: u32,
        ) -> i32;
        fn GetProcessHandleCount(process: *mut std::ffi::c_void, count: *mut u32) -> i32;
    }
    let mut memory = Memory {
        size: std::mem::size_of::<Memory>() as u32,
        faults: 0,
        values: [0; 9],
    };
    let (mut created, mut exited, mut kernel, mut user, mut handles) = (0, 0, 0, 0, 0);
    // SAFETY: the pseudo-handle belongs to this process; every output is a live,
    // correctly sized FILETIME/u32 or PROCESS_MEMORY_COUNTERS_EX-layout buffer.
    let size = memory.size;
    unsafe {
        let process = GetCurrentProcess();
        assert_ne!(
            GetProcessTimes(process, &mut created, &mut exited, &mut kernel, &mut user),
            0
        );
        assert_ne!(K32GetProcessMemoryInfo(process, &mut memory, size), 0);
        assert_ne!(GetProcessHandleCount(process, &mut handles), 0);
    }
    json!({"cpuSeconds":(kernel+user) as f64 / 10_000_000.0,
        "rss":memory.values[1],"peakRss":memory.values[0],
        "privateBytes":memory.values[8],"handles":handles})
}

#[cfg(not(windows))]
fn process_facts() -> Value {
    json!({"cpuSeconds":null,"rss":null,"peakRss":null,"privateBytes":null,"handles":null})
}

fn file_hash(path: &std::path::Path) -> (u64, [u8; 32]) {
    use std::io::Read;
    let mut file = std::fs::File::open(path).unwrap();
    let length = file.metadata().unwrap().len();
    let mut digest = ring::digest::Context::new(&ring::digest::SHA256);
    let mut buffer = vec![0; 256 * 1024];
    loop {
        let size = file.read(&mut buffer).unwrap();
        if size == 0 {
            break;
        }
        digest.update(&buffer[..size]);
    }
    (length, digest.finish().as_ref().try_into().unwrap())
}

#[test]
#[ignore = "requires SHARPMOD_GRIB_PIPELINE_URL loopback fixture server and local complete GRIB"]
fn complete_http_transfer_and_point_extraction() {
    let fixture = PathBuf::from(std::env::var_os("SHARPMOD_GRIB_BENCH_FIXTURE").unwrap());
    let library = PathBuf::from(std::env::var_os("SHARPMOD_ECCODES_LIBRARY").unwrap());
    let output_root = PathBuf::from(std::env::var_os("SHARPMOD_GRIB_PIPELINE_EVIDENCE").unwrap());
    let url = std::env::var("SHARPMOD_GRIB_PIPELINE_URL").unwrap();
    assert!(
        url.starts_with("http://127.0.0.1:"),
        "the benchmark must not multiply real-provider downloads"
    );
    assert!(output_root.is_absolute());
    std::fs::create_dir_all(&output_root).unwrap();
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let work = output_root.join(format!("native-http-{}-{nonce}", std::process::id()));
    std::fs::create_dir(&work).unwrap();
    let (length, expected_hash) = file_hash(&fixture);
    let iterations: usize = std::env::var("SHARPMOD_GRIB_PIPELINE_SAMPLES")
        .unwrap_or_else(|_| "5".into())
        .parse()
        .unwrap();
    assert!((3..=20).contains(&iterations));
    let client = Arc::new(RangeClient::new(10.0, 60.0).unwrap());
    let callbacks = Arc::new(Callbacks {
        cancelled: None,
        progress: None,
        bytes: None,
        error: Mutex::new(None),
    });
    let mut reference = None;
    let mut reports = Vec::new();
    for workers in [1, 4] {
        let plan = (0..workers)
            .map(|i| ByteRange {
                start: i as u64 * length / workers as u64,
                end: (i as u64 + 1) * length / workers as u64 - 1,
            })
            .collect::<Vec<_>>();
        let options = Options {
            workers,
            ..Options::default()
        };
        // Two excluded warmups, then AB/BA ordering under identical HTTP and byte limits.
        let order = [false, true]
            .into_iter()
            .map(|stream| (true, stream))
            .chain(
                (0..iterations)
                    .flat_map(|pair| [pair % 2 == 1, pair % 2 == 0].map(|stream| (false, stream))),
            );
        for (index, (warmup, stream)) in order.enumerate() {
            grib::set_grib_inventory_cache_enabled(true);
            grib::clear_grib_inventory_cache(true);
            let transport = NativeTransport {
                client: client.clone(),
                runtime: crate::weather::runtime().unwrap(),
                callbacks: callbacks.clone(),
                stop: Arc::new(AtomicBool::new(false)),
            };
            let output = work.join(format!("w{workers}-{index}-{stream}.grib2"));
            let mut metrics = Metrics::default();
            let facts_before = process_facts();
            let start = Instant::now();
            let request_url = format!("{url}?workers={workers}&sample={index}&stream={stream}");
            let prepared = if stream {
                let request =
                    streaming::Request::new(library.clone(), -27.1173, -52.4449, Some(-9999.0))
                        .unwrap()
                        .unwrap();
                range_transfer::download_with_reader(
                    &transport,
                    callbacks.as_ref(),
                    &request_url,
                    &plan,
                    &output,
                    &options,
                    &mut metrics,
                    Some((
                        streaming::Request::MIN_BYTES,
                        move |input: &mut dyn std::io::Read| request.decode(input).ok(),
                    )),
                )
                .unwrap()
            } else {
                range_transfer::download(
                    &transport,
                    callbacks.as_ref(),
                    &request_url,
                    &plan,
                    &output,
                    &options,
                    &mut metrics,
                )
                .unwrap();
                None
            };
            let acquired_ms = start.elapsed().as_secs_f64() * 1000.0;
            if stream {
                assert!(prepared
                    .expect("the real stream must prepare a point")
                    .finish(&output)
                    .unwrap());
            }
            let point =
                grib::decode_grib_point(&output, &library, -27.1173, -52.4449, Some(-9999.0))
                    .unwrap();
            let total_ms = start.elapsed().as_secs_f64() * 1000.0;
            let facts = process_facts();
            let columns = json!({
                "matrixBits":point.matrix.iter().map(|v|v.to_bits()).collect::<Vec<_>>(),
                "levels":point.level_count,"latitude":point.selected_latitude,
                "longitude":point.selected_longitude,"surfaceMerged":point.surface_merged,
                "removedLevels":point.below_ground_levels_removed,
                "vorticityBits":point.surface_relative_vorticity.map(f64::to_bits),
            });
            if let Some(reference) = &reference {
                assert_eq!(columns, *reference);
            } else {
                reference = Some(columns.clone());
            }
            let (_, full_hash) = file_hash(&output);
            assert_eq!(
                full_hash, expected_hash,
                "HTTP/assembly changed source bytes"
            );
            // Parallel transfers pin the representation with the existing
            // one-byte identity probe before requesting the data fragments.
            let probe_bytes = u64::from(workers > 1);
            assert_eq!(metrics.planned_bytes, length);
            assert_eq!(metrics.transferred_bytes, length + probe_bytes);
            assert_eq!(metrics.request_count, workers as u64 + probe_bytes);
            let cpu = facts["cpuSeconds"]
                .as_f64()
                .zip(facts_before["cpuSeconds"].as_f64())
                .map(|(after, before)| after - before);
            let report = json!({"workers":workers,"stream":stream,"warmup":warmup,"index":index,
                "acquisitionAndPreparationMs":acquired_ms,"completeMs":total_ms,
                "cpuSeconds":cpu,"memory":facts,"transferredBytes":metrics.transferred_bytes,
                "requests":metrics.request_count,"probeBytes":probe_bytes,
                "retries":metrics.retry_count});
            eprintln!("PIPELINE {}", report);
            reports.push(report);
            std::fs::write(
                output_root.join("native-http-results.json"),
                serde_json::to_vec_pretty(
                    &json!({"fixtureHash":expected_hash,"fixtureBytes":length,
                    "matrixReference":reference,"reports":reports}),
                )
                .unwrap(),
            )
            .unwrap();
            // Identical duplicate GRIBs are disposable; keep their hashes, columns and timing
            // evidence and preserve the original complete fixture.
            std::fs::remove_file(output).unwrap();
        }
    }
}

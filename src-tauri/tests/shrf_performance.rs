//! Opt-in transport microbenchmark; excluded from routine test execution.
//! cargo test --manifest-path src-tauri/Cargo.toml --release --test shrf_performance -- --ignored --nocapture --test-threads=1
//! SHARPPY_SHRF_ITERS controls paired samples (20 by default). No network or renderer.
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;
use std::time::Instant;

use base64::Engine as _;
use serde::Serialize;
use serde_json::{json, Value};
use sharppy_reimagined_lib::backend::map_ops::shrf_frame;

#[derive(Clone, Copy, Default, Serialize)]
struct Counts {
    allocations: usize,
    allocated_bytes: usize,
    live_bytes: usize,
    peak_live_bytes: usize,
}

thread_local! {
    static COUNTS: Cell<Option<Counts>> = const { Cell::new(None) };
}

struct CountedSystem;

fn account(added: usize, removed: usize) {
    let _ = COUNTS.try_with(|slot| {
        if let Some(mut counts) = slot.get() {
            if added > 0 {
                counts.allocations += 1;
                counts.allocated_bytes += added;
            }
            counts.live_bytes = counts.live_bytes.saturating_sub(removed) + added;
            counts.peak_live_bytes = counts.peak_live_bytes.max(counts.live_bytes);
            slot.set(Some(counts));
        }
    });
}

// Only this integration-test binary is instrumented; production keeps its allocator.
unsafe impl GlobalAlloc for CountedSystem {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            account(layout.size(), 0);
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        account(0, layout.size());
        unsafe { System.dealloc(ptr, layout) };
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        let result = unsafe { System.realloc(ptr, layout, size) };
        if !result.is_null() {
            account(size, layout.size());
        }
        result
    }
}

#[global_allocator]
static ALLOCATOR: CountedSystem = CountedSystem;

// Reference: pre-change map_ops::decode followed by shrf_frame at 0b505e64.
fn before(header: &Value, encoded: &str) -> Vec<u8> {
    let body = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .unwrap();
    let mut header = serde_json::to_vec(header).unwrap_or_default();
    while !(12 + header.len()).is_multiple_of(8) {
        header.push(b' ');
    }
    let length = u32::try_from(header.len()).unwrap_or(u32::MAX);
    let mut out = Vec::with_capacity(12 + header.len() + body.len());
    out.extend_from_slice(b"SHRF");
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&length.to_le_bytes());
    out.extend_from_slice(&header);
    out.extend_from_slice(&body);
    out
}

fn allocation_counts(work: impl FnOnce() -> Vec<u8>) -> Counts {
    COUNTS.with(|slot| slot.set(Some(Counts::default())));
    drop(black_box(work()));
    let counts = COUNTS.with(|slot| slot.replace(None)).unwrap();
    assert_eq!(counts.live_bytes, 0);
    counts
}

#[test]
fn allocation_counter_balances_one_owned_buffer() {
    let counts = allocation_counts(|| Vec::with_capacity(1234));
    assert_eq!(counts.allocations, 1);
    assert_eq!(counts.allocated_bytes, 1234);
    assert_eq!(counts.peak_live_bytes, 1234);
}

#[test]
#[ignore = "Opt in with --release --test shrf_performance -- --ignored --nocapture --test-threads=1"]
fn paired_encode_decode_and_assembly() {
    let iterations: usize = std::env::var("SHARPPY_SHRF_ITERS")
        .map(|value| value.parse().expect("positive integer SHARPPY_SHRF_ITERS"))
        .unwrap_or(20);
    assert!((2..=10_000).contains(&iterations));
    let mut results = Vec::new();
    for (route, fixture) in [
        (
            "field",
            &include_bytes!("../contracts/public/map/field.shrf")[..],
        ),
        (
            "radar",
            &include_bytes!("../contracts/public/map/radar.shrf")[..],
        ),
        (
            "radar-site",
            &include_bytes!("../contracts/public/map/radar-site.shrf")[..],
        ),
        (
            "satellite",
            &include_bytes!("../contracts/public/map/satellite.shrf")[..],
        ),
    ] {
        let offset = 12 + u32::from_le_bytes(fixture[8..12].try_into().unwrap()) as usize;
        let header: Value = serde_json::from_slice(&fixture[12..offset]).unwrap();
        let original = &fixture[offset..];
        for (dataset, length) in [
            ("committed-fixture", original.len()),
            (
                "synthetic-transport",
                if route == "field" {
                    4 * 1024 * 1024
                } else {
                    1024 * 1024
                },
            ),
        ] {
            let body: Vec<u8> = original.iter().copied().cycle().take(length).collect();
            let mut header = header.clone();
            if route == "field" && dataset == "synthetic-transport" {
                header["width"] = json!(2048);
                header["height"] = json!(1024);
            }
            let encoded = base64::engine::general_purpose::STANDARD.encode(&body);
            let reference = before(&header, &encoded);
            let (after, after_offset) = shrf_frame(&header, &encoded).unwrap();
            assert_eq!(
                reference, after,
                "{route}/{dataset}: complete frame changed"
            );
            assert_eq!(&after[after_offset..], body);
            assert!(after_offset.is_multiple_of(8));
            drop((reference, after));

            for phase in ["decode-assemble", "encode-decode-assemble"] {
                let work = |old: bool| {
                    let fresh;
                    let input = if phase == "encode-decode-assemble" {
                        fresh = base64::engine::general_purpose::STANDARD.encode(black_box(&body));
                        fresh.as_str()
                    } else {
                        encoded.as_str()
                    };
                    if old {
                        before(black_box(&header), black_box(input))
                    } else {
                        shrf_frame(black_box(&header), black_box(input)).unwrap().0
                    }
                };
                for _ in 0..3 {
                    drop(black_box(work(true)));
                    drop(black_box(work(false)));
                }
                let allocations_before = allocation_counts(|| work(true));
                let allocations_after = allocation_counts(|| work(false));
                assert!(allocations_before.allocations > allocations_after.allocations);
                assert!(allocations_before.peak_live_bytes > allocations_after.peak_live_bytes);
                let mut samples = [
                    Vec::with_capacity(iterations),
                    Vec::with_capacity(iterations),
                ];
                for iteration in 0..iterations {
                    for variant in if iteration % 2 == 0 { [0, 1] } else { [1, 0] } {
                        let start = Instant::now();
                        drop(black_box(work(variant == 0)));
                        samples[variant].push(start.elapsed().as_nanos() as u64);
                    }
                }
                results.push(json!({
                    "route": route, "dataset": dataset, "body_bytes": body.len(),
                    "encoded_bytes": encoded.len(), "phase": phase,
                    "complete_frame_equal": true, "body_equal": true,
                    "before_ns": samples[0], "after_ns": samples[1],
                    "before_allocations": allocations_before,
                    "after_allocations": allocations_after,
                }));
            }
        }
    }
    println!(
        "SHRF_BENCHMARK {}",
        json!({
            "baseline": "0b505e649c554dc718ae55498c177434c4bcfe65 decode + copied assembly",
            "profile": if cfg!(debug_assertions) { "debug" } else { "release" },
            "iterations": iterations,
            "memory_scope": "calling thread requested allocator bytes, excludes pre-existing inputs and OS/GPU memory; counting disabled during timings",
            "synthetic_scope": "repeated fixture body for transport only; no PNG codec or scientific kernel timing",
            "results": results,
        })
    );
}

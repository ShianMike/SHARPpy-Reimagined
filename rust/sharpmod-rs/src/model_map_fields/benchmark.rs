//! Opt-in comparison through the real provider and ecCodes, with fixed HTTP input.
use super::fixtures::{Provider, Server};
use super::*;
use ring::digest::{digest, SHA256};
use serde_json::json;
use std::{
    collections::BTreeMap,
    sync::atomic::AtomicBool,
    thread::JoinHandle,
    time::{Duration, Instant},
};

struct Monitor {
    done: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}
impl Drop for Monitor {
    fn drop(&mut self) {
        self.done.store(true, Ordering::Release);
        self.worker.take().unwrap().join().unwrap();
    }
}
fn sha(bytes: &[u8]) -> String {
    digest(&SHA256, bytes)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[test]
#[ignore = "requires SHARPMOD_MAP_BENCH_FIXTURE and the existing pinned ecCodes environment"]
fn compare_real_map_provider_loading() {
    let _guard = crate::lock_python_backend_test();
    let fixture = std::env::var("SHARPMOD_MAP_BENCH_FIXTURE").unwrap();
    let mode = std::env::var("SHARPMOD_MAP_BENCH_MODE").unwrap();
    let output = std::env::var("SHARPMOD_MAP_BENCH_OUTPUT").unwrap();
    let payload = std::fs::read(&fixture).unwrap();
    let fingerprint = sha(&payload);
    let server = Server::start(payload, std::fs::read(format!("{fixture}.idx")).unwrap());
    Python::initialize();
    let mut samples = Vec::new();
    let mut reference = None;
    Python::attach(|py| -> PyResult<()> {
        let api=PyModule::import(py,"sharpmod.providers.hrrr_field")?;
        let provider=PyModule::import(py,"sharpmod.providers.hrrr_field_fetch")?;
        let _provider=Provider::new(py,&server,None)?;
        let products=PyModule::import(py,"sharpmod.providers.hrrr_products")?;
        let datetime=PyModule::import(py,"datetime")?.getattr("datetime")?;
        let run=datetime.call_method1("fromisoformat",("2026-11-17T00:00:00+00:00",))?;
        api.call_method0("clear_cache")?;
        clear_model_field_cache();
        for pass in 0..3 {
            let before_bytes=server.bytes.load(Ordering::Relaxed);
            let before_requests=server.requests.load(Ordering::Relaxed);
            let before_decoded=DECODED.load(Ordering::Relaxed);
            let overlap=Arc::new(AtomicBool::new(false));
            let done=Arc::new(AtomicBool::new(false));
            let (observed,finished,active)=(overlap.clone(),done.clone(),server.active.clone());
            let monitor=std::thread::spawn(move || {
                while !finished.load(Ordering::Acquire) {
                    if DECODING.load(Ordering::Relaxed)>0 && active.load(Ordering::Relaxed)>0 {observed.store(true,Ordering::Release);}
                    std::thread::sleep(Duration::from_millis(1));
                }
            });
            let monitor=Monitor {done,worker:Some(monitor)};
            let started=Instant::now();
            let mut loaded=Vec::new();
            // Load the multi-range product first so the cold pass exercises
            // decoding one complete message while another range still arrives.
            for key in ["thetae-2m","tmp-2m","dpt-2m","wind-10m"] {
                let product=products.call_method1("get_product",(key,))?;
                let value=if mode=="baseline" {
                    let kwargs=PyDict::new(py);
                    kwargs.set_item("opener",api.getattr("_default_opener")?)?;
                    kwargs.set_item("timeout",30.)?;
                    kwargs.set_item("should_cancel",py.None())?;
                    provider.getattr("_download_fields")?.call((&product,&run,1),Some(&kwargs))?.cast_into::<PyDict>()?
                } else {
                    download_map_fields(py,&product,&run,1,30.,None,"hrrr".into())?.into_bound(py)
                };
                loaded.push((key,value));
            }
            let elapsed=started.elapsed().as_secs_f64();
            drop(monitor);
            let mut hashes=BTreeMap::new();
            for (key,grids) in loaded {
                for (name,array) in grids.iter() {
                    let array=array.cast::<PyArray2<f32>>()?.readonly();
                    let bytes:Vec<_>=array.as_array().iter().flat_map(|value|value.to_bits().to_le_bytes()).collect();
                    hashes.insert(format!("{key}/{}",name.extract::<String>()?),sha(&bytes));
                }
            }
            if let Some(reference)=&reference {assert_eq!(reference,&hashes);} else {reference=Some(hashes.clone());}
            samples.push(json!({"pass":pass,"seconds":elapsed,"transferredBytes":server.bytes.load(Ordering::Relaxed)-before_bytes,
                "requests":server.requests.load(Ordering::Relaxed)-before_requests,"decodedFields":DECODED.load(Ordering::Relaxed)-before_decoded,
                "downloadDecodeOverlap":overlap.load(Ordering::Acquire),"numericHashes":hashes}));
            if mode == "optimized" && pass == 0 {
                assert!(overlap.load(Ordering::Acquire), "the cold multi-range product must decode before all payloads arrive");
            }
        }
        Ok(())
    }).unwrap();
    std::fs::write(output,serde_json::to_vec_pretty(&json!({"mode":mode,"fixtureSha256":fingerprint,"samples":samples,
        "scope":"Real HRRR F001 messages through the provider and ecCodes; fixed loopback HTTP with 128 KiB chunks and 4 ms pacing. Scientific hashing is excluded from timing."})).unwrap()).unwrap();
}

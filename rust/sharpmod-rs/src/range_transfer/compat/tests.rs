use super::*;
use std::sync::atomic::AtomicBool;
struct Body(Option<Vec<u8>>);
impl super::super::Body for Body {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
        Ok(self.0.take())
    }
}
struct Network(Mutex<Vec<(u16, Vec<u8>)>>);
impl Transport for Network {
    fn get(
        &self,
        _worker: usize,
        _url: &str,
        _headers: &[(String, String)],
        _chunk: usize,
    ) -> Result<super::super::Response, Error> {
        let (status, bytes) = self.0.lock().unwrap().remove(0);
        Ok(super::super::Response {
            status,
            headers: Default::default(),
            body: Box::new(Body(Some(bytes))),
        })
    }
}
#[derive(Default)]
struct Events {
    cancel: AtomicBool,
    received: AtomicU64,
}
impl Hooks for Events {
    fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }
    fn progress(&self, _done: u64, _total: u64) -> Result<(), Error> {
        Ok(())
    }
    fn bytes(&self, count: u64) -> Result<(), Error> {
        self.received.fetch_add(count, Ordering::Relaxed);
        Ok(())
    }
}
#[test]
fn full_response_replaces_earlier_partial_bytes_and_invalid_download_preserves_output() {
    let root = std::env::temp_dir().join(format!("sharpmod-compat-{}", std::process::id()));
    std::fs::create_dir_all(&root).unwrap();
    let output = root.join("subset.grib2");
    let full = b"GRIBabcdefghijklmnopqrstuvwxyz7777".to_vec();
    let events = Events::default();
    let network = Network(Mutex::new(vec![
        (206, b"GRIBabcd".to_vec()),
        (200, full.clone()),
    ]));
    assert_eq!(
        download(
            &network,
            &events,
            "https://test/file",
            &[
                ByteRange { start: 0, end: 7 },
                ByteRange { start: 8, end: 15 }
            ],
            &output,
            4,
            false
        )
        .unwrap(),
        full.len() as u64
    );
    assert_eq!(std::fs::read(&output).unwrap(), full);
    assert_eq!(
        events.received.load(Ordering::Relaxed),
        8 + full.len() as u64
    );
    std::fs::write(&output, b"prior invalid output").unwrap();
    let broken = Network(Mutex::new(vec![(200, b"not GRIB".to_vec())]));
    assert!(download(&broken, &events, "https://test/file", &[], &output, 4, true).is_err());
    assert_eq!(std::fs::read(&output).unwrap(), b"prior invalid output");
    assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
    std::fs::remove_file(output).unwrap();
    std::fs::remove_dir(root).unwrap();
}

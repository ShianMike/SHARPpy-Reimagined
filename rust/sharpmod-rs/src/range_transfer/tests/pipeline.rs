use super::*;
use std::io::Read;
use std::sync::Condvar;
use std::time::{Duration, Instant};

struct GatedBody {
    body: Box<dyn Body>,
    gate: Arc<(Mutex<bool>, Condvar)>,
    chunks: usize,
}
impl Body for GatedBody {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
        if self.chunks == 1 {
            let accepted = self
                .gate
                .1
                .wait_timeout_while(
                    self.gate.0.lock().unwrap(),
                    Duration::from_secs(2),
                    |seen| !*seen,
                )
                .unwrap()
                .0;
            if !*accepted {
                return Err(Error::Unavailable(
                    "consumer waited for the whole download".into(),
                ));
            }
        }
        self.chunks += 1;
        self.body.next()
    }
    fn close(&mut self) {
        self.body.close();
    }
}

#[test]
fn consumer_reads_before_download_completion_and_sees_ordered_fragments() {
    let directory = Directory::new();
    let data = payload();
    let gate = Arc::new((Mutex::new(false), Condvar::new()));
    let producer_gate = gate.clone();
    let server = Fake::with(&data, move |request, data| {
        let mut response = answer(request, data);
        if request.range.start == 0 && request.range.end != 0 {
            response.body = Box::new(GatedBody {
                body: response.body,
                gate: producer_gate.clone(),
                chunks: 0,
            });
        }
        Ok(response)
    });
    let result = download_with_reader(
        &server,
        &Record::default(),
        "https://fixture/model",
        &halves(&data),
        &directory.output(),
        &Options {
            chunk_size: 4,
            ..options(2)
        },
        &mut Metrics::default(),
        Some((0, |input: &mut dyn Read| {
            let mut prefix = [0; 4];
            input.read_exact(&mut prefix).unwrap();
            assert_eq!(&prefix, b"GRIB");
            assert!(
                !directory.output().exists(),
                "the result must still be private"
            );
            *gate.0.lock().unwrap() = true;
            gate.1.notify_all();
            let mut bytes = prefix.to_vec();
            input.read_to_end(&mut bytes).unwrap();
            Some(bytes)
        })),
    )
    .unwrap();
    assert_eq!(result, Some(data.clone()));
    assert_eq!(fs::read(directory.output()).unwrap(), data);
    assert!(!directory.fragments().exists());
}

#[test]
fn a_declined_or_ineligible_consumer_keeps_the_normal_download() {
    for minimum in [0, 1024] {
        let directory = Directory::new();
        let data = payload();
        let called = AtomicUsize::new(0);
        let result: Option<()> = download_with_reader(
            &Fake::new(&data),
            &Record::default(),
            "https://fixture/model",
            &single(&data),
            &directory.output(),
            &options(1),
            &mut Metrics::default(),
            Some((minimum, |input: &mut dyn Read| {
                called.fetch_add(1, Ordering::SeqCst);
                let mut prefix = [0; 4];
                input.read_exact(&mut prefix).unwrap();
                None
            })),
        )
        .unwrap();
        assert_eq!(result, None);
        assert_eq!(called.load(Ordering::SeqCst), usize::from(minimum == 0));
        assert_eq!(fs::read(directory.output()).unwrap(), data);
        assert!(!directory.fragments().exists());
    }
}

#[test]
fn cancellation_wakes_a_reader_and_resume_consumes_existing_bytes_once() {
    let directory = Directory::new();
    let data = payload();
    let start = Instant::now();
    let result = download_with_reader(
        &Fake::new(&data),
        &Record {
            cancel_after: Some(4),
            ..Record::default()
        },
        "https://fixture/model",
        &single(&data),
        &directory.output(),
        &Options {
            chunk_size: 4,
            ..options(1)
        },
        &mut Metrics::default(),
        Some((0, |input: &mut dyn Read| {
            let mut data = Vec::new();
            input.read_to_end(&mut data).ok().map(|_| data)
        })),
    );
    assert_eq!(result, Err(Error::Cancelled));
    assert!(start.elapsed() < Duration::from_secs(2));
    assert!(!directory.output().exists());
    assert_eq!(
        fs::read(directory.fragments().join("0-31.part")).unwrap(),
        b"GRIB"
    );
    let mut metrics = Metrics::default();
    let resumed = download_with_reader(
        &Fake::new(&data),
        &Record::default(),
        "https://fixture/model",
        &single(&data),
        &directory.output(),
        &options(1),
        &mut metrics,
        Some((0, |input: &mut dyn Read| {
            let mut bytes = Vec::new();
            input.read_to_end(&mut bytes).unwrap();
            Some(bytes)
        })),
    )
    .unwrap();
    assert_eq!(resumed, Some(data.clone()));
    assert_eq!(metrics.reused_bytes, 4);
    // Resume first revalidates the saved ETag with the one-byte identity probe.
    assert_eq!(metrics.transferred_bytes, data.len() as u64 - 4 + 1);
    assert_eq!(metrics.request_count, 2);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
    assert!(!directory.fragments().exists());
}

#[test]
fn changed_source_discards_even_a_completed_consumer_result() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::with(&data, |request, data| {
        let mut response = answer(request, data);
        if request.range.start > 0 {
            response
                .headers
                .insert("etag".into(), "\"replaced\"".into());
        }
        Ok(response)
    });
    let result = download_with_reader(
        &server,
        &Record::default(),
        "https://fixture/model",
        &halves(&data),
        &directory.output(),
        &options(2),
        &mut Metrics::default(),
        Some((0, |_input: &mut dyn Read| Some("must not publish"))),
    );
    assert!(matches!(result, Err(Error::ObjectChanged(_))));
    assert!(!directory.output().exists());
    assert!(!directory.fragments().exists());
}

#[test]
fn cancellation_after_assembly_never_publishes_a_prepared_result() {
    let directory = Directory::new();
    let data = payload();
    let record = Record::default();
    let result = download_with_reader(
        &Fake::new(&data),
        &record,
        "https://fixture/model",
        &single(&data),
        &directory.output(),
        &options(1),
        &mut Metrics::default(),
        Some((0, |input: &mut dyn Read| {
            let mut bytes = Vec::new();
            input.read_to_end(&mut bytes).unwrap();
            let deadline = Instant::now() + Duration::from_secs(2);
            while !directory.output().exists() {
                assert!(Instant::now() < deadline, "assembly never completed");
                std::thread::yield_now();
            }
            record.cancel.store(true, Ordering::Release);
            Some(bytes)
        })),
    );
    assert_eq!(result, Err(Error::Cancelled));
    assert_eq!(fs::read(directory.output()).unwrap(), data);
    assert!(!directory.fragments().exists());
    let server = Fake::new(&data);
    download(
        &server,
        &Record::default(),
        "https://fixture/model",
        &single(&data),
        &directory.output(),
        &options(1),
        &mut Metrics::default(),
    )
    .unwrap();
    assert!(server.requests().is_empty());
}

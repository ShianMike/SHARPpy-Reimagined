use super::*;

#[test]
fn status_and_truncation_retry_only_failed_range() {
    let directory = Directory::new();
    let data = payload();
    let plan = halves(&data);
    let server = Fake::with(&data, |request, data| {
        let mut response = answer(request, data);
        if request.sequence == 1 {
            response.status = 503;
            response.headers.insert("retry-after".into(), "0".into());
        } else if request.sequence == 2 {
            response.body = Box::new(Chunks(VecDeque::from([Ok(data[16..21].to_vec())])));
        }
        Ok(response)
    });
    let mut metrics = Metrics::default();
    run(
        &server,
        &Record::default(),
        &plan,
        &directory,
        &options(1),
        &mut metrics,
    )
    .unwrap();
    let requested: Vec<_> = server.requests().iter().map(|r| r.range).collect();
    assert_eq!(
        requested,
        vec![plan[0], plan[1], plan[1], ByteRange { start: 21, end: 31 }]
    );
    assert_eq!(metrics.request_count, 4);
    assert_eq!(metrics.retry_count, 2);
    assert_eq!(metrics.transferred_bytes, data.len() as u64);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

#[test]
fn connection_and_mid_body_errors_resume_from_last_received_chunk() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::with(&data, |request, data| {
        if request.sequence == 0 {
            return Err(Error::Transient("connection failed".into(), 0.0));
        }
        let mut response = answer(request, data);
        if request.sequence == 1 {
            response.body = Box::new(Chunks(VecDeque::from([
                Ok(data[..7].to_vec()),
                Err(Error::Transient("read failed".into(), 0.0)),
            ])));
        }
        Ok(response)
    });
    let mut metrics = Metrics::default();
    run(
        &server,
        &Record::default(),
        &single(&data),
        &directory,
        &options(1),
        &mut metrics,
    )
    .unwrap();
    assert_eq!(server.requests()[2].range.start, 7);
    assert_eq!(server.requests()[2].conditional.as_deref(), Some("\"v1\""));
    assert_eq!(metrics.transferred_bytes, data.len() as u64);
    assert_eq!(metrics.retry_count, 2);
    assert_eq!(metrics.request_count, 3);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

#[test]
fn exhausted_transient_failure_keeps_fragment_and_final_metrics() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::with(&data, |request, data| {
        let mut response = answer(request, data);
        response.body = Box::new(Chunks(VecDeque::from([
            Ok(data[request.range.start as usize..request.range.start as usize + 3].to_vec()),
            Err(Error::Transient("stalled".into(), 0.0)),
        ])));
        Ok(response)
    });
    let mut metrics = Metrics::default();
    let result = run(
        &server,
        &Record::default(),
        &single(&data),
        &directory,
        &Options {
            retries: 1,
            ..options(1)
        },
        &mut metrics,
    );
    assert!(
        matches!(result,Err(Error::Unavailable(ref message)) if message.contains("HTTP range transfer failed"))
    );
    assert_eq!(metrics.request_count, 2);
    assert_eq!(metrics.retry_count, 1);
    assert_eq!(metrics.transferred_bytes, 6);
    assert_eq!(
        fs::read(directory.fragments().join("0-31.part")).unwrap(),
        data[..6]
    );
    let saved: serde_json::Value =
        serde_json::from_slice(&fs::read(directory.fragments().join("manifest.json")).unwrap())
            .unwrap();
    assert_eq!(saved["version"], 2);
    assert_eq!(saved["url"], "https://fixture/model");
    assert_eq!(saved["ranges"], serde_json::json!([[0, 31]]));
    assert_eq!(
        saved["identity"],
        serde_json::json!({"size":32,"etag":"\"v1\""})
    );
    assert!(!directory.output().exists());
}

#[test]
fn throttled_identity_probe_retries_before_any_worker_starts() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::with(&data, |request, data| {
        let mut response = answer(request, data);
        if request.sequence == 0 {
            response.status = 429;
            response.headers.insert("retry-after".into(), "0".into());
        }
        Ok(response)
    });
    let mut metrics = Metrics::default();
    run(
        &server,
        &Record::default(),
        &halves(&data),
        &directory,
        &options(2),
        &mut metrics,
    )
    .unwrap();
    assert!(server.requests()[..2]
        .iter()
        .all(|r| r.worker == 0 && r.range == ByteRange { start: 0, end: 0 }));
    assert_eq!(metrics.retry_count, 1);
    assert_eq!(metrics.request_count, 4);
    assert_eq!(metrics.transferred_bytes, data.len() as u64 + 1);
}

#[test]
fn oversized_body_is_counted_but_its_fragment_is_removed() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::with(&data, |request, data| {
        let mut response = answer(request, data);
        let mut oversized = data.to_vec();
        oversized.push(b'!');
        response.body = Box::new(Chunks(VecDeque::from([Ok(oversized)])));
        Ok(response)
    });
    let hooks = Record::default();
    let mut metrics = Metrics::default();
    let result = run(
        &server,
        &hooks,
        &single(&data),
        &directory,
        &options(1),
        &mut metrics,
    );
    assert!(
        matches!(result,Err(Error::Unavailable(ref message)) if message.contains("more bytes"))
    );
    assert_eq!(metrics.transferred_bytes, data.len() as u64 + 1);
    assert_eq!(
        hooks.bytes.load(Ordering::SeqCst),
        metrics.transferred_bytes
    );
    assert!(!directory.fragments().join("0-31.part").exists());
    assert_eq!(metrics.retry_count, 0);
}

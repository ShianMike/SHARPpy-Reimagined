use super::*;

#[test]
fn complete_output_is_reused_without_requests_or_callbacks() {
    let directory = Directory::new();
    let data = payload();
    fs::write(directory.output(), &data).unwrap();
    let server = Fake::new(&data);
    let hooks = Record::default();
    let mut metrics = Metrics {
        request_count: 77,
        fallback_used: true,
        ..Metrics::default()
    };
    run(
        &server,
        &hooks,
        &single(&data),
        &directory,
        &options(8),
        &mut metrics,
    )
    .unwrap();
    assert!(server.requests().is_empty());
    assert!(hooks.progress.lock().unwrap().is_empty());
    assert_eq!(
        metrics,
        Metrics {
            planned_bytes: data.len() as u64,
            reused_bytes: data.len() as u64,
            ..Metrics::default()
        }
    );
    assert!(!directory.fragments().exists());
    assert_eq!(server.closed.load(Ordering::SeqCst), 0);
}

#[test]
fn complete_fragment_is_assembled_and_atomically_replaces_invalid_output() {
    let directory = Directory::new();
    let data = payload();
    let plan = single(&data);
    directory.seed(plan[0], &data, None);
    fs::write(directory.output(), b"old invalid output").unwrap();
    let server = Fake::new(&data);
    let hooks = Record::default();
    let mut metrics = Metrics::default();
    run(
        &server,
        &hooks,
        &plan,
        &directory,
        &options(1),
        &mut metrics,
    )
    .unwrap();
    assert!(server.requests().is_empty());
    assert_eq!(fs::read(directory.output()).unwrap(), data);
    assert_eq!(metrics.reused_bytes, data.len() as u64);
    assert_eq!(metrics.transferred_bytes, 0);
    assert_eq!(
        *hooks.progress.lock().unwrap(),
        vec![(data.len() as u64, data.len() as u64)]
    );
    assert!(!directory.fragments().exists());
    assert!(directory.no_temporary_files());
}

#[test]
fn anonymous_partial_fragment_resumes_only_missing_bytes() {
    let directory = Directory::new();
    let data = payload();
    let plan = single(&data);
    directory.seed(plan[0], &data[..9], None);
    let server = Fake::new(&data);
    let hooks = Record::default();
    let mut metrics = Metrics::default();
    run(
        &server,
        &hooks,
        &plan,
        &directory,
        &options(1),
        &mut metrics,
    )
    .unwrap();
    let requests = server.requests();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].range.start, 9);
    assert_eq!(requests[0].conditional, None);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
    assert_eq!(metrics.reused_bytes, 9);
    assert_eq!(metrics.transferred_bytes, data.len() as u64 - 9);
    assert_eq!(
        hooks.bytes.load(Ordering::SeqCst),
        metrics.transferred_bytes
    );
}

#[test]
fn complete_pinned_fragment_only_requests_identity_validation() {
    let directory = Directory::new();
    let data = payload();
    let plan = single(&data);
    directory.seed(
        plan[0],
        &data,
        Some(manifest(
            &plan,
            serde_json::json!({"size":data.len(), "etag":"\"v1\""}),
        )),
    );
    let server = Fake::new(&data);
    let hooks = Record::default();
    let mut metrics = Metrics::default();
    run(
        &server,
        &hooks,
        &plan,
        &directory,
        &options(1),
        &mut metrics,
    )
    .unwrap();
    assert_eq!(server.requests().len(), 1);
    assert_eq!(server.requests()[0].range, ByteRange { start: 0, end: 0 });
    assert_eq!(metrics.reused_bytes, data.len() as u64);
    assert_eq!(metrics.transferred_bytes, 1);
    assert_eq!(hooks.bytes.load(Ordering::SeqCst), 0);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

#[test]
fn oversized_fragment_is_discarded_before_transfer() {
    let directory = Directory::new();
    let data = payload();
    directory.seed(single(&data)[0], &vec![b'!'; data.len() + 1], None);
    let server = Fake::new(&data);
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
    assert_eq!(server.requests()[0].range.start, 0);
    assert_eq!(metrics.reused_bytes, 0);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

#[test]
fn invalid_grib_preserves_previous_output_and_removes_assembly_temporary() {
    for data in [
        b"not a grib payload".as_slice(),
        b"GRIBbad-trailer",
        b"GRIB777",
    ] {
        let directory = Directory::new();
        fs::write(directory.output(), b"old output").unwrap();
        let server = Fake::new(data);
        let mut metrics = Metrics::default();
        let result = run(
            &server,
            &Record::default(),
            &single(data),
            &directory,
            &options(1),
            &mut metrics,
        );
        assert!(
            matches!(result, Err(Error::Unavailable(ref message)) if message.contains("valid GRIB"))
        );
        assert_eq!(fs::read(directory.output()).unwrap(), b"old output");
        assert!(directory.fragments().exists());
        assert!(directory.no_temporary_files());
        assert_eq!(metrics.transferred_bytes, data.len() as u64);
    }
}

#[test]
fn disjoint_ranges_are_assembled_in_plan_order() {
    let directory = Directory::new();
    let data = b"GRIBincluded__skip__tail7777";
    let plan = vec![
        ByteRange { start: 0, end: 11 },
        ByteRange { start: 20, end: 27 },
    ];
    let server = Fake::new(data);
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
    let expected: Vec<_> = data[..12]
        .iter()
        .chain(data[20..].iter())
        .copied()
        .collect();
    assert_eq!(fs::read(directory.output()).unwrap(), expected);
    assert_eq!(metrics.planned_bytes, 20);
}

#[test]
fn callback_error_stops_without_retry_and_keeps_received_fragment() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::new(&data);
    let hooks = Record {
        fail_bytes: true,
        ..Record::default()
    };
    let mut metrics = Metrics::default();
    let result = run(
        &server,
        &hooks,
        &single(&data),
        &directory,
        &Options {
            chunk_size: 7,
            ..options(1)
        },
        &mut metrics,
    );
    assert!(matches!(result, Err(Error::Callback(_))));
    assert_eq!(server.requests().len(), 1);
    assert_eq!(metrics.retry_count, 0);
    assert_eq!(metrics.transferred_bytes, 7);
    assert_eq!(
        fs::read(directory.fragments().join("0-31.part")).unwrap(),
        data[..7]
    );
    assert!(!directory.output().exists());
}

#[test]
fn malformed_plans_fail_before_files_or_requests() {
    for plan in [
        vec![],
        vec![ByteRange { start: 3, end: 2 }],
        vec![ByteRange {
            start: 0,
            end: u64::MAX,
        }],
        vec![ByteRange { start: 0, end: 3 }; 2],
    ] {
        let directory = Directory::new();
        let server = Fake::new(&payload());
        let mut metrics = Metrics::default();
        assert!(matches!(
            run(
                &server,
                &Record::default(),
                &plan,
                &directory,
                &options(8),
                &mut metrics
            ),
            Err(Error::Unavailable(_))
        ));
        assert!(server.requests().is_empty());
        assert!(!directory.fragments().exists());
    }
}

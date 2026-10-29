use super::*;
use serde_json::json;

#[test]
fn url_or_range_manifest_change_discards_stale_fragments() {
    for field in ["url", "ranges"] {
        let directory = Directory::new();
        let data = payload();
        let plan = single(&data);
        let mut old = manifest(&plan, json!({"etag":"\"v1\"", "size":data.len()}));
        old[field] = if field == "url" {
            json!("https://other/object")
        } else {
            json!([[0, 9]])
        };
        directory.seed(plan[0], b"stale", Some(old));
        let server = Fake::new(&data);
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
        assert_eq!(server.requests().len(), 1);
        assert_eq!(server.requests()[0].range, plan[0]);
        assert_eq!(metrics.reused_bytes, 0);
        assert_eq!(fs::read(directory.output()).unwrap(), data);
    }
}

#[test]
fn top_level_legacy_etag_is_probed_and_retains_matching_partial() {
    let directory = Directory::new();
    let data = payload();
    let plan = single(&data);
    directory.seed(
        plan[0],
        &data[..9],
        Some(json!({"url":"https://fixture/model","ranges":[[0,31]],"etag":"\"v1\""})),
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
    let requests = server.requests();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].range, ByteRange { start: 0, end: 0 });
    assert_eq!(requests[0].chunk_size, 1);
    assert_eq!(requests[1].range.start, 9);
    assert_eq!(requests[1].conditional.as_deref(), Some("\"v1\""));
    assert_eq!(metrics.reused_bytes, 9);
    assert_eq!(metrics.transferred_bytes, data.len() as u64 - 9 + 1);
    assert_eq!(
        hooks.bytes.load(Ordering::SeqCst),
        metrics.transferred_bytes - 1
    );
}

#[test]
fn changed_probe_identity_discards_even_complete_pinned_fragment() {
    let directory = Directory::new();
    let data = payload();
    let plan = single(&data);
    directory.seed(
        plan[0],
        &vec![b'!'; data.len()],
        Some(manifest(&plan, json!({"etag":"\"old\"","size":data.len()}))),
    );
    let server = Fake::new(&data);
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
    assert_eq!(server.requests().len(), 2);
    assert_eq!(server.requests()[1].range, plan[0]);
    assert_eq!(metrics.reused_bytes, 0);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

#[test]
fn parallel_transfer_discards_anonymous_fragments_before_reuse() {
    let directory = Directory::new();
    let data = payload();
    let plan = halves(&data);
    directory.seed(plan[0], b"wrong", None);
    let server = Fake::new(&data);
    let mut metrics = Metrics::default();
    run(
        &server,
        &Record::default(),
        &plan,
        &directory,
        &options(2),
        &mut metrics,
    )
    .unwrap();
    assert_eq!(metrics.reused_bytes, 0);
    assert_eq!(metrics.transferred_bytes, data.len() as u64 + 1);
    assert_eq!(server.requests().len(), 3);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

#[test]
fn pinned_identity_change_or_disappearance_cleans_all_fragments() {
    for variant in [
        "etag missing",
        "etag changed",
        "size changed",
        "modified missing",
        "conditional 200",
    ] {
        let directory = Directory::new();
        let data = payload();
        let plan = halves(&data);
        fs::write(directory.output(), b"previous invalid output").unwrap();
        let server = Fake::with(&data, move |request, data| {
            let mut response = answer(request, data);
            response.headers.insert(
                "last-modified".into(),
                "Wed, 22 Jul 2026 00:00:00 GMT".into(),
            );
            if request.sequence == 1 {
                match variant {
                    "etag missing" => {
                        response.headers.remove("etag");
                    }
                    "etag changed" => {
                        response.headers.insert("etag".into(), "\"new\"".into());
                    }
                    "size changed" => {
                        response.headers.insert(
                            "content-range".into(),
                            format!(
                                "bytes {}-{}/{}",
                                request.range.start,
                                request.range.end,
                                data.len() + 1
                            ),
                        );
                    }
                    "modified missing" => {
                        response.headers.remove("last-modified");
                    }
                    "conditional 200" => {
                        response.status = 200;
                    }
                    _ => unreachable!(),
                }
            }
            Ok(response)
        });
        let mut metrics = Metrics::default();
        let result = run(
            &server,
            &Record::default(),
            &plan,
            &directory,
            &options(1),
            &mut metrics,
        );
        assert!(
            matches!(result,Err(Error::ObjectChanged(ref message)) if message.contains("changed")),
            "{variant}: {result:?}"
        );
        assert_eq!(server.requests().len(), 2);
        assert!(!directory.fragments().exists(), "{variant}");
        assert_eq!(
            fs::read(directory.output()).unwrap(),
            b"previous invalid output"
        );
        assert_eq!(metrics.retry_count, 0);
    }
}

#[test]
fn missing_or_weak_only_validator_downgrades_to_coordinator_session() {
    for weak in [false, true] {
        let directory = Directory::new();
        let data = payload();
        let server = Fake::with(&data, move |request, data| {
            let mut response = answer(request, data);
            if weak {
                response.headers.insert("etag".into(), "W/\"v1\"".into());
            } else {
                response.headers.remove("etag");
            }
            Ok(response)
        });
        let hooks = Record::default();
        let mut metrics = Metrics::default();
        run(
            &server,
            &hooks,
            &halves(&data),
            &directory,
            &options(2),
            &mut metrics,
        )
        .unwrap();
        assert_eq!(metrics.worker_count, 1);
        assert_eq!(metrics.transferred_bytes, data.len() as u64 + 1);
        assert_eq!(hooks.bytes.load(Ordering::SeqCst), data.len() as u64);
        assert_eq!(server.requests().len(), 3);
        assert!(server
            .requests()
            .iter()
            .all(|r| r.worker == 0 && r.conditional.is_none()));
    }
}

#[test]
fn weak_etag_with_last_modified_uses_date_condition() {
    let directory = Directory::new();
    let data = payload();
    let date = "Wed, 22 Jul 2026 00:00:00 GMT";
    let server = Fake::with(&data, move |request, data| {
        let mut response = answer(request, data);
        response.headers.insert("etag".into(), "W/\"v1\"".into());
        response.headers.insert("last-modified".into(), date.into());
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
    assert_eq!(metrics.worker_count, 2);
    assert!(server.requests()[1..]
        .iter()
        .all(|r| r.conditional.as_deref() == Some(date)));
}

#[test]
fn invalid_content_ranges_and_unconditional_200_are_not_retried() {
    for header in [
        "bytes 1-31/32",
        "bytes 0-31/*",
        "bytes 0-31/31",
        "other 0-31/32",
        "invalid",
        "",
    ] {
        let directory = Directory::new();
        let data = payload();
        let server = Fake::with(&data, move |request, data| {
            let mut response = answer(request, data);
            if header.is_empty() {
                response.status = 200;
            } else {
                response
                    .headers
                    .insert("content-range".into(), header.into());
            }
            Ok(response)
        });
        let mut metrics = Metrics::default();
        let result = run(
            &server,
            &Record::default(),
            &single(&data),
            &directory,
            &options(1),
            &mut metrics,
        );
        assert!(
            matches!(result,Err(Error::Unavailable(ref message)) if message.contains("HTTP range"))
        );
        assert_eq!(server.requests().len(), 1);
        assert_eq!(metrics.transferred_bytes, 0);
        assert_eq!(metrics.retry_count, 0);
    }
}

#[test]
fn probe_requires_exactly_one_byte_and_counts_consumed_probe_payload() {
    for size in [0, 2] {
        let directory = Directory::new();
        let data = payload();
        let server = Fake::with(&data, move |request, data| {
            let mut response = answer(request, data);
            response.body = Box::new(Chunks(VecDeque::from([Ok(vec![b'!'; size])])));
            Ok(response)
        });
        let hooks = Record::default();
        let mut metrics = Metrics::default();
        let result = run(
            &server,
            &hooks,
            &halves(&data),
            &directory,
            &options(2),
            &mut metrics,
        );
        assert!(
            matches!(result,Err(Error::Unavailable(ref message)) if message.contains("identity range"))
        );
        assert_eq!(metrics.transferred_bytes, size as u64);
        assert_eq!(hooks.bytes.load(Ordering::SeqCst), 0);
        assert_eq!(server.requests().len(), 1);
    }
}

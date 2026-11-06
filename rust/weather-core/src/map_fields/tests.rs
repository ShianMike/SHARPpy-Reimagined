use super::*;
use std::{
    io::{BufRead, BufReader, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[test]
fn inventory_preserves_subfields_duplicate_offsets_and_the_unknown_tail() {
    let rows = parse_wgrib_inventory(
        "1.1:10:d=2026110600:UGRD:300 mb:6 hour fcst:\r\n\
         1.2:10:d=2026110600:VGRD:300 mb:6 hour fcst:\n\
         2:30:d=2026110600:TMP:500 mb:6 hour fcst:\n\
         1.3:10:d=2026110600:VVEL:300 mb:6 hour fcst:\n\
         3.1:50:d=2026110600:UGRD:surface:anl:\n\
         3.2:50:d=2026110600:VGRD:surface:anl:\n",
    );
    assert_eq!(rows.len(), 6);
    assert_eq!(
        rows.iter()
            .map(|row| (row.index, row.offset, row.length, row.field_index))
            .collect::<Vec<_>>(),
        [
            (1, 10, 20, 0),
            (1, 10, 20, 1),
            (2, 30, 20, 0),
            (1, 10, 20, 2),
            (3, 50, 0, 0),
            (3, 50, 0, 1)
        ]
    );
    assert_eq!(rows[1].variable, "VGRD");
    assert_eq!(rows[1].level, "300 mb");
    assert_eq!(rows[1].forecast, "6 hour fcst");
}

#[test]
fn inventory_skips_malformed_rows_without_changing_lengths() {
    for line in [
        "",
        "not an index",
        "1:0:d:TMP:surface",
        "no:10:d:TMP:surface:anl:",
        "1.0:20:d:TMP:surface:anl:",
        "1.-1:20:d:TMP:surface:anl:",
        "1.bad:20:d:TMP:surface:anl:",
        "1.1.2:20:d:TMP:surface:anl:",
        "1:-10:d:TMP:surface:anl:",
        "1:1.0:d:TMP:surface:anl:",
        "1: 10:d:TMP:surface:anl:",
        "1::d:TMP:surface:anl:",
        "1:18446744073709551616:d:TMP:surface:anl:",
    ] {
        assert!(parse_wgrib_inventory(line).is_empty(), "{line}");
    }
    let rows = parse_wgrib_inventory(
        "  -2.:0:date:TMP:surface:anl:\n\
         bad:20:date:TMP:surface:anl:\n\
         +3.+2:40:date:VGRD: surface : 1 hour fcst :ignored:\n",
    );
    assert_eq!(
        (rows[0].index, rows[0].length, rows[0].field_index),
        (-2, 40, 0)
    );
    assert_eq!(
        (rows[1].index, rows[1].length, rows[1].field_index),
        (3, 0, 1)
    );
    assert_eq!(rows[1].level, " surface ");
    assert_eq!(rows[1].forecast, " 1 hour fcst ");
}

#[test]
fn inventory_uses_first_seen_offset_order_and_saturates_decreasing_lengths() {
    let rows = parse_wgrib_inventory(
        "1:12:d:T:surface:anl:\n2:0:d:T:surface:anl:\n\
         3:8:d:T:surface:anl:\n1.2:12:d:U:surface:anl:\n",
    );
    assert_eq!(
        rows.iter().map(|row| row.length).collect::<Vec<_>>(),
        [0, 8, 0, 0]
    );
}

#[test]
fn range_groups_preserve_stable_caller_indices_for_overlaps_and_nearby_records() {
    assert_eq!(
        merge_map_ranges(
            &[(100, 10), (0, 10), (105, 30), (100, 10), (180, 2), (200, 2)],
            45
        )
        .unwrap(),
        [
            RangeGroup {
                start: 0,
                end: 9,
                members: vec![1]
            },
            RangeGroup {
                start: 100,
                end: 201,
                members: vec![0, 3, 2, 4, 5]
            },
        ]
    );
    let groups = merge_map_ranges(&[(0, 10), (10, 10), (65555, 2), (131094, 3)], 65536).unwrap();
    assert_eq!(
        (groups[0].start, groups[0].end, &groups[0].members),
        (0, 65556, &vec![0, 1, 2])
    );
    assert_eq!(groups[1].start, 131094);
}

#[test]
fn range_groups_keep_eof_records_separate_including_duplicate_offsets() {
    assert_eq!(
        merge_map_ranges(&[(100, 0), (0, 100), (100, 0), (100, 10), (110, 0)], 65536).unwrap(),
        [
            RangeGroup {
                start: 0,
                end: 99,
                members: vec![1]
            },
            RangeGroup {
                start: 100,
                end: 0,
                members: vec![0]
            },
            RangeGroup {
                start: 100,
                end: 0,
                members: vec![2]
            },
            RangeGroup {
                start: 100,
                end: 109,
                members: vec![3]
            },
            RangeGroup {
                start: 110,
                end: 0,
                members: vec![4]
            },
        ]
    );
    // End zero is also the existing sentinel for the one-byte range at offset zero.
    assert_eq!(merge_map_ranges(&[(0, 1), (1, 1)], 65536).unwrap().len(), 2);
    assert!(merge_map_ranges(&[], 65536).unwrap().is_empty());
}

#[test]
fn range_groups_support_negative_slack_and_reject_offset_overflow() {
    assert_eq!(
        merge_map_ranges(&[(0, 10), (5, 10), (15, 1)], -1)
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        merge_map_ranges(&[(u64::MAX, 2)], 65536),
        Err(Error::Invalid("model byte range is out of bounds".into()))
    );
    assert_eq!(
        merge_map_ranges(&[(u64::MAX, 1)], 65536).unwrap()[0].end,
        u64::MAX
    );
}

struct Request {
    path: String,
    range: String,
}

struct Reply {
    status: u16,
    range: Option<String>,
    body: Vec<u8>,
    pause: Duration,
    chunked: bool,
}

impl Reply {
    fn range(body: impl Into<Vec<u8>>) -> Self {
        Self {
            status: 206,
            range: Some("bytes 10-13/100".into()),
            body: body.into(),
            pause: Duration::ZERO,
            chunked: false,
        }
    }
}

struct Server {
    url: String,
    requests: Arc<Mutex<Vec<Request>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Server {
    fn new(reply: impl Fn(&Request) -> Reply + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let stop = Arc::new(AtomicBool::new(false));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let (stopping, recorded, reply) = (stop.clone(), requests.clone(), Arc::new(reply));
        let thread = thread::spawn(move || {
            let mut workers = Vec::new();
            for stream in listener.incoming() {
                let stream = stream.unwrap();
                if stopping.load(Ordering::SeqCst) {
                    break;
                }
                let (recorded, reply) = (recorded.clone(), reply.clone());
                workers.push(thread::spawn(move || {
                    stream
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let mut input = BufReader::new(stream);
                    let mut line = String::new();
                    if input.read_line(&mut line).is_err() {
                        return;
                    }
                    let mut request = Request {
                        path: line.split_whitespace().nth(1).unwrap_or("").into(),
                        range: String::new(),
                    };
                    loop {
                        line.clear();
                        match input.read_line(&mut line) {
                            Ok(0) | Err(_) => return,
                            Ok(_) if line == "\r\n" => break,
                            Ok(_) => {
                                if let Some((name, value)) = line.split_once(':') {
                                    if name.eq_ignore_ascii_case("range") {
                                        request.range = value.trim().into();
                                    }
                                }
                            }
                        }
                    }
                    recorded.lock().unwrap().push(Request {
                        path: request.path.clone(),
                        range: request.range.clone(),
                    });
                    let reply = reply(&request);
                    thread::sleep(reply.pause);
                    let mut headers =
                        format!("HTTP/1.1 {} Test\r\nConnection: close\r\n", reply.status);
                    if let Some(range) = reply.range {
                        headers.push_str(&format!("Content-Range: {range}\r\n"));
                    }
                    let body = if reply.chunked {
                        headers.push_str("Transfer-Encoding: chunked\r\n");
                        let mut body = format!("{:x}\r\n", reply.body.len()).into_bytes();
                        body.extend_from_slice(&reply.body);
                        body.extend_from_slice(b"\r\n0\r\n\r\n");
                        body
                    } else {
                        headers.push_str(&format!("Content-Length: {}\r\n", reply.body.len()));
                        reply.body
                    };
                    headers.push_str("\r\n");
                    let _ = input
                        .get_mut()
                        .write_all(headers.as_bytes())
                        .and_then(|()| input.get_mut().write_all(&body));
                }));
            }
            for worker in workers {
                worker.join().unwrap();
            }
        });
        Self {
            url,
            requests,
            stop,
            thread: Some(thread),
        }
    }

    fn jobs(&self, paths: &[&str]) -> Vec<RangeJob> {
        paths
            .iter()
            .map(|path| RangeJob {
                url: format!("{}{path}", self.url),
                limit: 4,
                start: 10,
                end: 13,
            })
            .collect()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        self.thread.take().unwrap().join().unwrap();
    }
}

#[tokio::test]
async fn batches_are_concurrent_bounded_and_return_input_order() {
    for workers in [0, 1, 2, 4, 99] {
        let active = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let (running, maximum) = (active.clone(), peak.clone());
        let server = Server::new(move |request| {
            maximum.fetch_max(running.fetch_add(1, Ordering::SeqCst) + 1, Ordering::SeqCst);
            // Unequal completion times exercise reordering while keeping requests overlapped.
            let index = request.path.trim_start_matches('/').parse::<u8>().unwrap();
            thread::sleep(Duration::from_millis(25 + u64::from(8 - index) * 5));
            running.fetch_sub(1, Ordering::SeqCst);
            Reply::range(vec![index; 4])
        });
        let jobs = server.jobs(&["/0", "/1", "/2", "/3", "/4", "/5", "/6", "/7", "/8"]);
        let output = fetch_model_ranges(&ModelClient::default(), jobs, 2., workers, || Ok(false))
            .await
            .unwrap();
        assert_eq!(
            output,
            (0..9).map(|index| vec![index; 4]).collect::<Vec<_>>()
        );
        assert_eq!(peak.load(Ordering::SeqCst), workers.clamp(1, 8));
        let requests = server.requests.lock().unwrap();
        assert_eq!(requests.len(), 9);
        assert!(requests
            .iter()
            .all(|request| request.range == "bytes=10-13"));
    }
}

#[tokio::test]
async fn first_failure_aborts_slow_siblings_and_leaves_queued_jobs_unstarted() {
    let server = Server::new(|request| {
        let mut reply = Reply::range(b"GRIB");
        if request.path == "/missing" {
            reply.status = 404;
        } else {
            reply.pause = Duration::from_millis(500);
        }
        reply
    });
    let jobs = server.jobs(&["/slow", "/missing", "/queued", "/queued"]);
    let started = Instant::now();
    let result = fetch_model_ranges(&ModelClient::default(), jobs, 2., 2, || Ok(false)).await;
    assert_eq!(result, Err(Error::Unavailable(404)));
    assert!(started.elapsed() < Duration::from_millis(400));
    assert!(server
        .requests
        .lock()
        .unwrap()
        .iter()
        .all(|request| request.path != "/queued"));
}

#[tokio::test]
async fn user_cancellation_and_callback_errors_stop_the_whole_batch() {
    let server = Server::new(|_| {
        let mut reply = Reply::range(b"GRIB");
        reply.pause = Duration::from_millis(500);
        reply
    });
    let client = ModelClient::default();
    assert_eq!(
        fetch_model_ranges(&client, server.jobs(&["/never"]), 2., 4, || Ok(true)).await,
        Err(Error::Cancelled)
    );
    assert!(server.requests.lock().unwrap().is_empty());
    assert_eq!(
        fetch_model_ranges(&client, server.jobs(&["/never"]), 2., 4, || Err(
            "callback failed".into()
        ))
        .await,
        Err(Error::Invalid("callback failed".into()))
    );
    assert!(server.requests.lock().unwrap().is_empty());
    let started = Instant::now();
    let result = fetch_model_ranges(
        &client,
        server.jobs(&["/slow", "/slow", "/queued"]),
        2.,
        2,
        || Ok(started.elapsed() >= Duration::from_millis(100)),
    )
    .await;
    assert_eq!(result, Err(Error::Cancelled));
    assert!(started.elapsed() < Duration::from_millis(400));
    assert!(server
        .requests
        .lock()
        .unwrap()
        .iter()
        .all(|request| request.path == "/slow"));
}

#[tokio::test]
async fn batches_retain_response_byte_budgets_and_range_validation() {
    for (status, range, body, limit, chunked, expected) in [
        (206, "bytes 10-13/100", "GRIB", 3, false, Error::Budget),
        (206, "bytes 10-13/100", "GRIB", 3, true, Error::Budget),
        (
            200,
            "bytes 10-13/100",
            "GRIB",
            4,
            false,
            Error::Invalid("model response does not match the requested byte range".into()),
        ),
        (
            206,
            "bytes 11-14/100",
            "GRIB",
            4,
            false,
            Error::Invalid("model response does not match the requested byte range".into()),
        ),
        (
            206,
            "bytes 10-13/100",
            "GRI",
            4,
            false,
            Error::Invalid("model response contains an incomplete byte range".into()),
        ),
        (422, "bytes 10-13/100", "", 4, false, Error::Http(422)),
    ] {
        let server = Server::new(move |_| {
            let mut reply = Reply::range(body);
            reply.status = status;
            reply.range = Some(range.into());
            reply.chunked = chunked;
            reply
        });
        let mut jobs = server.jobs(&["/field"]);
        jobs[0].limit = limit;
        assert_eq!(
            fetch_model_ranges(&ModelClient::default(), jobs, 2., 4, || Ok(false)).await,
            Err(expected)
        );
        assert_eq!(server.requests.lock().unwrap().len(), 1);
    }
}

#[tokio::test]
async fn transient_failures_retry_through_the_existing_model_client() {
    let attempts = Arc::new(AtomicUsize::new(0));
    let hits = attempts.clone();
    let server = Server::new(move |_| {
        let mut reply = Reply::range(b"GRIB");
        if hits.fetch_add(1, Ordering::SeqCst) == 0 {
            reply.status = 503;
        }
        reply
    });
    let output = fetch_model_ranges(
        &ModelClient::default(),
        server.jobs(&["/field"]),
        2.,
        4,
        || Ok(false),
    )
    .await
    .unwrap();
    assert_eq!(output, [b"GRIB"]);
    assert_eq!(attempts.load(Ordering::SeqCst), 2);
    assert!(
        fetch_model_ranges(&ModelClient::default(), vec![], 2., 4, || Ok(false))
            .await
            .unwrap()
            .is_empty()
    );
}

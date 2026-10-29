use super::*;
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Barrier, Mutex,
    },
    thread::{self, JoinHandle},
};

#[derive(Clone)]
struct Request {
    path: String,
    headers: HashMap<String, String>,
    port: u16,
}

struct Reply {
    status: u16,
    body: Vec<u8>,
    headers: Vec<(&'static str, String)>,
    chunks: usize,
    pause: Duration,
    close: bool,
}

impl Reply {
    fn new(status: u16, body: impl Into<Vec<u8>>) -> Self {
        Self {
            status,
            body: body.into(),
            headers: Vec::new(),
            chunks: 1,
            pause: Duration::ZERO,
            close: false,
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
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let (recorded, stopping, reply) = (requests.clone(), stop.clone(), Arc::new(reply));
        let thread = thread::spawn(move || {
            let mut workers = Vec::new();
            for stream in listener.incoming() {
                let stream = stream.unwrap();
                if stopping.load(Ordering::SeqCst) {
                    break;
                }
                let (recorded, stopping, reply) =
                    (recorded.clone(), stopping.clone(), reply.clone());
                workers.push(thread::spawn(move || {
                    stream
                        .set_read_timeout(Some(Duration::from_millis(100)))
                        .unwrap();
                    let port = stream.peer_addr().unwrap().port();
                    let mut input = BufReader::new(stream);
                    loop {
                        if stopping.load(Ordering::SeqCst) {
                            break;
                        }
                        let mut line = String::new();
                        match input.read_line(&mut line) {
                            Ok(0) => break,
                            Err(error)
                                if matches!(
                                    error.kind(),
                                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                                ) =>
                            {
                                continue
                            }
                            Err(_) => break,
                            Ok(_) => {}
                        }
                        let path = line.split_whitespace().nth(1).unwrap_or("").into();
                        let mut headers = HashMap::new();
                        loop {
                            let mut line = String::new();
                            match input.read_line(&mut line) {
                                Ok(0) | Err(_) => return,
                                Ok(_) if line == "\r\n" => break,
                                Ok(_) => {
                                    if let Some((name, value)) = line.split_once(':') {
                                        headers
                                            .insert(name.to_ascii_lowercase(), value.trim().into());
                                    }
                                }
                            }
                        }
                        let request = Request {
                            path,
                            headers,
                            port,
                        };
                        recorded.lock().unwrap().push(request.clone());
                        let response = reply(&request);
                        if response.status == 0 {
                            return;
                        }
                        let mut headers = format!("HTTP/1.1 {} Test\r\n", response.status);
                        if !response
                            .headers
                            .iter()
                            .any(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                            && !response.close
                        {
                            headers
                                .push_str(&format!("Content-Length: {}\r\n", response.body.len()));
                        }
                        if response.close {
                            headers.push_str("Connection: close\r\n");
                        }
                        for (name, value) in response.headers {
                            headers.push_str(&format!("{name}: {value}\r\n"));
                        }
                        headers.push_str("\r\n");
                        if input.get_mut().write_all(headers.as_bytes()).is_err() {
                            return;
                        }
                        let size = response.body.len().div_ceil(response.chunks).max(1);
                        for (index, chunk) in response.body.chunks(size).enumerate() {
                            if index > 0 {
                                thread::sleep(response.pause);
                            }
                            if input.get_mut().write_all(chunk).is_err() {
                                return;
                            }
                        }
                        if response.close {
                            return;
                        }
                    }
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

    fn count(&self) -> usize {
        self.requests.lock().unwrap().len()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        self.thread.take().unwrap().join().unwrap();
    }
}

fn client() -> ModelClient {
    let _ = rustls::crypto::ring::default_provider().install_default();
    ModelClient {
        client: Ok(Client::builder()
            .no_proxy()
            .pool_max_idle_per_host(32)
            .retry(reqwest::retry::never())
            .build()
            .unwrap()),
    }
}

#[tokio::test]
async fn a_single_attempt_does_not_retry_provider_statuses_or_transport_failures() {
    for status in [429, 500, 503, 422] {
        let server = Server::new(move |_| Reply::new(status, b""));
        assert_eq!(
            client().fetch_once(&server.url, 1., 100).await,
            Err(Error::Http(status))
        );
        assert_eq!(server.count(), 1);
    }
    let server = Server::new(|_| Reply::new(0, b""));
    assert!(matches!(
        client().fetch_once(&server.url, 1., 100).await,
        Err(Error::Offline(_))
    ));
    assert_eq!(server.count(), 1);
}

#[tokio::test]
async fn ranges_keep_exact_headers_bytes_and_connection_reuse() {
    let server = Server::new(|request| {
        assert_eq!(request.headers.get("range").unwrap(), "bytes=10-13");
        assert_eq!(request.headers.get("accept-encoding").unwrap(), "identity");
        Reply::new(206, b"GRIB")
    });
    let client = client();
    for _ in 0..2 {
        assert_eq!(
            client
                .fetch(
                    &format!("{}/field", server.url),
                    2.,
                    4,
                    Some((10, 13)),
                    || Ok(false)
                )
                .await
                .unwrap(),
            b"GRIB"
        );
    }
    let calls = server.requests.lock().unwrap();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].path, "/field");
    assert_eq!(calls[0].port, calls[1].port);
}

#[tokio::test]
async fn budgets_are_enforced_with_and_without_content_length() {
    for close in [false, true] {
        let server = Server::new(move |_| Reply {
            close,
            ..Reply::new(200, b"12345")
        });
        assert_eq!(
            client()
                .fetch(&server.url, 2., 4, None, || Ok(false))
                .await
                .unwrap_err(),
            Error::Budget
        );
    }
}

#[tokio::test]
async fn unpublished_http_errors_and_connection_failures_stay_distinct() {
    for status in [403, 404, 400, 500] {
        let server = Server::new(move |_| Reply::new(status, b"bad"));
        let error = client()
            .fetch(&server.url, 2., 100, None, || Ok(false))
            .await
            .unwrap_err();
        if status == 500 {
            assert!(matches!(error, Error::Offline(_)));
        } else {
            assert_eq!(
                error,
                if status == 403 || status == 404 {
                    Error::Unavailable(status)
                } else {
                    Error::Http(status)
                }
            );
        }
        assert_eq!(server.count(), if status == 500 { 3 } else { 1 });
    }
    let server = Server::new(|_| Reply::new(0, b""));
    assert!(matches!(
        client()
            .fetch(&server.url, 1., 100, None, || Ok(false))
            .await,
        Err(Error::Offline(_))
    ));
    assert_eq!(server.count(), 3);
}

#[tokio::test]
async fn transient_statuses_retry_twice_and_honour_retry_after() {
    for status in [429, 500, 502, 503, 504, 413] {
        let hits = Arc::new(AtomicUsize::new(0));
        let count = hits.clone();
        let server = Server::new(move |_| {
            let status = if count.fetch_add(1, Ordering::SeqCst) < 2 {
                status
            } else {
                200
            };
            Reply {
                headers: vec![("Retry-After", "0".into())],
                ..Reply::new(status, b"GRIB")
            }
        });
        assert_eq!(
            client()
                .fetch(&server.url, 2., 100, None, || Ok(false))
                .await
                .unwrap(),
            b"GRIB"
        );
        assert_eq!(server.count(), 3);
    }
    assert_eq!(
        retry_after(Some(&header::HeaderValue::from_static("2"))),
        Some(Duration::from_secs(2))
    );
    assert_eq!(
        retry_after(Some(&header::HeaderValue::from_static(
            "Wed, 21 Oct 2015 07:28:00 GMT"
        ))),
        Some(Duration::ZERO)
    );
    assert_eq!(
        retry_after(Some(&header::HeaderValue::from_static("invalid"))),
        None
    );
}

#[tokio::test]
async fn per_read_timeout_allows_a_long_transfer_with_active_chunks() {
    let server = Server::new(|_| Reply {
        chunks: 4,
        pause: Duration::from_millis(200),
        ..Reply::new(200, b"12345678")
    });
    let started = std::time::Instant::now();
    assert_eq!(
        client()
            .fetch(&server.url, 0.5, 8, None, || Ok(false))
            .await
            .unwrap(),
        b"12345678"
    );
    assert!(started.elapsed() > Duration::from_millis(500));
}

#[tokio::test]
async fn cancellation_stops_before_network_and_during_retry_and_reads() {
    let server = Server::new(|_| Reply::new(200, b"GRIB"));
    assert_eq!(
        client()
            .fetch(&server.url, 2., 100, None, || Ok(true))
            .await
            .unwrap_err(),
        Error::Cancelled
    );
    assert_eq!(server.count(), 0);
    let stop = Arc::new(AtomicBool::new(false));
    let stopping = stop.clone();
    let server = Server::new(move |_| {
        stopping.store(true, Ordering::SeqCst);
        Reply {
            headers: vec![("Retry-After", "10".into())],
            ..Reply::new(503, b"busy")
        }
    });
    assert_eq!(
        client()
            .fetch(&server.url, 2., 100, None, || Ok(
                stop.load(Ordering::SeqCst)
            ))
            .await
            .unwrap_err(),
        Error::Cancelled
    );
    assert_eq!(server.count(), 1);
    let server = Server::new(|_| Reply {
        chunks: 2,
        pause: Duration::from_secs(2),
        ..Reply::new(200, vec![42; 131072])
    });
    let started = std::time::Instant::now();
    assert_eq!(
        client()
            .fetch(&server.url, 2., 200000, None, || Ok(
                started.elapsed() > Duration::from_millis(80)
            ))
            .await
            .unwrap_err(),
        Error::Cancelled
    );
    assert!(started.elapsed() < Duration::from_secs(1));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn retains_all_connections_between_concurrent_bursts() {
    for workers in [16, 32] {
        let barrier = Arc::new(Barrier::new(workers));
        let server = Server::new(move |_| {
            barrier.wait();
            Reply::new(206, b"GRIB")
        });
        let client = Arc::new(client());
        for _ in 0..2 {
            let mut tasks = tokio::task::JoinSet::new();
            for _ in 0..workers {
                let (client, url) = (client.clone(), server.url.clone());
                tasks.spawn(async move { client.fetch(&url, 5., 4, None, || Ok(false)).await });
            }
            while let Some(result) = tasks.join_next().await {
                assert_eq!(result.unwrap().unwrap(), b"GRIB");
            }
        }
        let ports: std::collections::HashSet<_> = server
            .requests
            .lock()
            .unwrap()
            .iter()
            .map(|call| call.port)
            .collect();
        assert_eq!(ports.len(), workers);
    }
}

#[tokio::test]
async fn rejects_invalid_url_timeout_and_ranges_before_network() {
    let server = Server::new(|_| Reply::new(200, b""));
    for (url, timeout, range) in [
        ("file:///etc/passwd", 2., None),
        (server.url.as_str(), 0., None),
        (server.url.as_str(), f64::INFINITY, None),
        (server.url.as_str(), 2., Some((10, 9))),
    ] {
        assert!(matches!(
            client().fetch(url, timeout, 100, range, || Ok(false)).await,
            Err(Error::Invalid(_))
        ));
    }
    assert_eq!(server.count(), 0);
}

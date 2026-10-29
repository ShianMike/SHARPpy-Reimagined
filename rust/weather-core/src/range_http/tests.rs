use super::*;
use std::{
    collections::BTreeSet,
    io::{BufRead, BufReader, Write},
    net::{Shutdown, TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::Instant,
};

#[derive(Clone)]
struct Request {
    path: String,
    headers: HashMap<String, String>,
    port: u16,
}

struct Server {
    url: String,
    requests: Arc<Mutex<Vec<Request>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Server {
    fn new(reply: impl Fn(&Request, &mut TcpStream) + Send + Sync + 'static) -> Self {
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
                    stream
                        .set_write_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let port = stream.peer_addr().unwrap().port();
                    let mut input = BufReader::new(stream);
                    while !stopping.load(Ordering::SeqCst) {
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
                        let path = line.split_whitespace().nth(1).unwrap().to_string();
                        let mut headers = HashMap::new();
                        loop {
                            let mut line = String::new();
                            match input.read_line(&mut line) {
                                Ok(0) | Err(_) => return,
                                Ok(_) if line == "\r\n" => break,
                                Ok(_) => {
                                    let (name, value) = line.split_once(':').unwrap();
                                    headers.insert(name.to_ascii_lowercase(), value.trim().into());
                                }
                            }
                        }
                        let request = Request {
                            path,
                            headers,
                            port,
                        };
                        recorded.lock().unwrap().push(request.clone());
                        reply(&request, input.get_mut());
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
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        self.thread.take().unwrap().join().unwrap();
    }
}

fn reply(stream: &mut TcpStream, status: u16, headers: &[u8], body: &[u8]) {
    write!(
        stream,
        "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\n",
        body.len()
    )
    .unwrap();
    stream.write_all(headers).unwrap();
    stream.write_all(b"\r\n").unwrap();
    stream.write_all(body).unwrap();
}

async fn body(response: &mut RangeResponse) -> Vec<u8> {
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk(|| false).await.unwrap() {
        bytes.extend_from_slice(&chunk);
    }
    bytes
}

#[tokio::test]
async fn exposes_status_and_only_resume_headers_and_sends_exact_range_headers() {
    let server = Server::new(|_, stream| {
        reply(
            stream,
            206,
            b"Content-Range: bytes 5-8/20\r\nETag: \"same-object\"\r\nLast-Modified: Wed, 21 Oct 2015 07:28:00 GMT\r\nRetry-After: 2\r\nX-Unused: ignored\r\n",
            b"GRIB",
        );
    });
    let client = RangeClient::new(2., 2.).unwrap();
    let mut response = client
        .get(
            &server.url,
            &[
                ("Range".into(), "bytes=5-8".into()),
                ("If-Range".into(), "\"same-object\"".into()),
                ("Accept-Encoding".into(), "gzip".into()),
            ],
            || false,
        )
        .await
        .unwrap();
    assert_eq!(response.status, 206);
    assert_eq!(
        response.headers,
        HashMap::from([
            ("content-range".into(), "bytes 5-8/20".into()),
            ("etag".into(), "\"same-object\"".into()),
            (
                "last-modified".into(),
                "Wed, 21 Oct 2015 07:28:00 GMT".into()
            ),
            ("retry-after".into(), "2".into()),
        ])
    );
    assert_eq!(body(&mut response).await, b"GRIB");
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].headers["range"], "bytes=5-8");
    assert_eq!(requests[0].headers["if-range"], "\"same-object\"");
    assert_eq!(requests[0].headers["accept-encoding"], "identity");
}

#[tokio::test]
async fn skips_non_utf8_headers_and_does_not_retry_http_errors() {
    let server = Server::new(|_, stream| {
        reply(stream, 503, b"ETag: \xff\r\nRetry-After: 0\r\n", b"busy");
    });
    let client = RangeClient::new(2., 2.).unwrap();
    let mut response = client.get(&server.url, &[], || false).await.unwrap();
    assert_eq!(response.status, 503);
    assert_eq!(
        response.headers,
        HashMap::from([("retry-after".into(), "0".into())])
    );
    assert_eq!(body(&mut response).await, b"busy");
    assert_eq!(server.requests.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn active_chunks_can_outlive_the_read_deadline() {
    let server = Server::new(|_, stream| {
        stream
            .write_all(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 6\r\n\r\n")
            .unwrap();
        for index in 0..6 {
            if index > 0 {
                thread::sleep(Duration::from_millis(150));
            }
            if stream.write_all(b"x").is_err() {
                break;
            }
        }
    });
    let client = RangeClient::new(2., 0.5).unwrap();
    let started = Instant::now();
    let mut response = client.get(&server.url, &[], || false).await.unwrap();
    assert_eq!(body(&mut response).await, b"xxxxxx");
    assert!(started.elapsed() > Duration::from_millis(500));
}

#[tokio::test]
async fn cancels_before_network_or_before_consuming_a_ready_chunk() {
    let server = Server::new(|_, stream| reply(stream, 200, &[], b"GRIB"));
    let client = RangeClient::new(2., 2.).unwrap();
    assert_eq!(
        client.get(&server.url, &[], || true).await.err().as_deref(),
        Some("__cancelled__")
    );
    assert!(server.requests.lock().unwrap().is_empty());
    let mut response = client.get(&server.url, &[], || false).await.unwrap();
    assert_eq!(response.chunk(|| true).await, Err("__cancelled__".into()));
    assert_eq!(body(&mut response).await, b"GRIB");
}

#[tokio::test]
async fn cancellation_interrupts_stalled_headers() {
    let server = Server::new(|_, stream| {
        thread::sleep(Duration::from_millis(800));
        let _ = stream.shutdown(Shutdown::Both);
    });
    let client = RangeClient::new(2., 5.).unwrap();
    let started = Instant::now();
    let result = client
        .get(&server.url, &[], || {
            started.elapsed() >= Duration::from_millis(100)
        })
        .await;
    assert_eq!(result.err().as_deref(), Some("__cancelled__"));
    assert!(started.elapsed() < Duration::from_millis(600));
    assert_eq!(server.requests.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn cancellation_interrupts_a_stalled_body() {
    let server = Server::new(|_, stream| {
        stream
            .write_all(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 2\r\n\r\nx")
            .unwrap();
        thread::sleep(Duration::from_millis(800));
        let _ = stream.shutdown(Shutdown::Both);
    });
    let client = RangeClient::new(2., 5.).unwrap();
    let mut response = client.get(&server.url, &[], || false).await.unwrap();
    assert_eq!(response.chunk(|| false).await.unwrap().unwrap(), b"x");
    let started = Instant::now();
    assert_eq!(
        response
            .chunk(|| started.elapsed() >= Duration::from_millis(100))
            .await,
        Err("__cancelled__".into())
    );
    assert!(started.elapsed() < Duration::from_millis(600));
}

#[tokio::test]
async fn read_timeouts_apply_to_pending_headers_and_body_without_cancellation() {
    let server = Server::new(|request, stream| {
        if request.path == "/body" {
            stream
                .write_all(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 2\r\n\r\nx")
                .unwrap();
        }
        thread::sleep(Duration::from_millis(800));
        let _ = stream.shutdown(Shutdown::Both);
    });
    let client = RangeClient::new(2., 0.2).unwrap();
    let started = Instant::now();
    let error = client
        .get(&format!("{}/headers", server.url), &[], || false)
        .await
        .err()
        .unwrap();
    assert_ne!(error, "__cancelled__");
    assert!(started.elapsed() < Duration::from_millis(600));
    let mut response = client
        .get(&format!("{}/body", server.url), &[], || false)
        .await
        .unwrap();
    assert_eq!(response.chunk(|| false).await.unwrap().unwrap(), b"x");
    let started = Instant::now();
    let error = response.chunk(|| false).await.unwrap_err();
    assert_ne!(error, "__cancelled__");
    assert!(started.elapsed() < Duration::from_millis(600));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn shared_client_reuses_connections_across_concurrent_worker_bursts() {
    let received = Arc::new(AtomicUsize::new(0));
    let observed = received.clone();
    let server = Server::new(move |request, stream| {
        observed.fetch_add(1, Ordering::SeqCst);
        let goal = if request.path.starts_with("/0/") {
            4
        } else {
            8
        };
        let deadline = Instant::now() + Duration::from_secs(2);
        while observed.load(Ordering::SeqCst) < goal && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        reply(stream, 206, &[], b"GRIB");
    });
    let client = Arc::new(RangeClient::new(2., 3.).unwrap());
    for burst in 0..2 {
        let mut workers = tokio::task::JoinSet::new();
        for index in 0..4 {
            let client = client.clone();
            let url = format!("{}/{burst}/{index}", server.url);
            workers.spawn(async move {
                let mut response = client.get(&url, &[], || false).await.unwrap();
                body(&mut response).await
            });
        }
        while let Some(result) = workers.join_next().await {
            assert_eq!(result.unwrap(), b"GRIB");
        }
    }
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 8);
    let ports = |burst| {
        requests
            .iter()
            .filter(|request| request.path.starts_with(&format!("/{burst}/")))
            .map(|request| request.port)
            .collect::<BTreeSet<_>>()
    };
    assert_eq!(ports(0).len(), 4);
    assert_eq!(ports(0), ports(1));
}

#[test]
fn rejects_invalid_timeout_values() {
    for value in [0., -1., f64::NAN, f64::INFINITY, f64::MAX] {
        assert!(RangeClient::new(value, 1.).is_err());
        assert!(RangeClient::new(1., value).is_err());
    }
}

#[tokio::test]
async fn reqwest_rejects_non_http_urls_and_invalid_headers() {
    let client = RangeClient::new(2., 2.).unwrap();
    for url in ["file:///unused", "ftp://localhost/unused", "invalid"] {
        assert!(client.get(url, &[], || false).await.is_err());
    }
    let server = Server::new(|_, stream| reply(stream, 200, &[], b"GRIB"));
    for headers in [
        vec![("invalid\nname".into(), "value".into())],
        vec![("Range".into(), "bytes=0-3\r\nInjected: value".into())],
    ] {
        assert!(client.get(&server.url, &headers, || false).await.is_err());
    }
    assert!(server.requests.lock().unwrap().is_empty());
}

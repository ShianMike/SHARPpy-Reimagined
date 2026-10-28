use super::*;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};

struct Reply {
    status: u16,
    body: Vec<u8>,
    content_length: bool,
}

impl Reply {
    fn new(status: u16, body: impl Into<Vec<u8>>) -> Self {
        Self {
            status,
            body: body.into(),
            content_length: true,
        }
    }
}

struct Server {
    base: String,
    calls: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Server {
    fn new(reply: impl Fn(&str) -> Reply + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let calls = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let (recorded, stopping, reply) = (calls.clone(), stop.clone(), Arc::new(reply));
        let thread = thread::spawn(move || {
            let mut connections = Vec::new();
            for connection in listener.incoming() {
                let mut stream = connection.unwrap();
                if stopping.load(Ordering::SeqCst) {
                    break;
                }
                let (recorded, reply) = (recorded.clone(), reply.clone());
                connections.push(thread::spawn(move || {
                    stream
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let mut reader = BufReader::new(&stream);
                    let mut request = String::new();
                    reader.read_line(&mut request).unwrap();
                    let path = request.split_whitespace().nth(1).unwrap().to_owned();
                    loop {
                        let mut header = String::new();
                        if reader.read_line(&mut header).unwrap() == 0 || header == "\r\n" {
                            break;
                        }
                    }
                    recorded.lock().unwrap().push(path.clone());
                    let reply = reply(&path);
                    if reply.status == 0 {
                        return;
                    }
                    let mut headers =
                        format!("HTTP/1.1 {} Test\r\nConnection: close\r\n", reply.status);
                    if reply.content_length {
                        headers.push_str(&format!("Content-Length: {}\r\n", reply.body.len()));
                    }
                    headers.push_str("\r\n");
                    let _ = stream.write_all(headers.as_bytes());
                    let _ = stream.write_all(&reply.body);
                }));
            }
            for connection in connections {
                connection.join().unwrap();
            }
        });
        Self {
            base,
            calls,
            stop,
            thread: Some(thread),
        }
    }

    fn inventory(&self) -> VelocityInventory {
        let mut inventory = VelocityInventory::new();
        inventory.client = Ok(Client::builder()
            .no_proxy()
            .user_agent("sharpmod")
            .timeout(Duration::from_secs(8))
            .build()
            .unwrap());
        inventory.base = self.base.clone();
        inventory
    }

    fn count(&self) -> usize {
        self.calls.lock().unwrap().len()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.base.trim_start_matches("http://"));
        self.thread.take().unwrap().join().unwrap();
    }
}

fn site(id: &str) -> &'static crate::radar_sites::RadarSite {
    crate::radar_sites::sites()
        .iter()
        .find(|site| site.id == id)
        .unwrap()
}

fn now() -> OffsetDateTime {
    OffsetDateTime::parse(
        "2026-01-01T00:30:00Z",
        &time::format_description::well_known::Rfc3339,
    )
    .unwrap()
}

fn listing(date: OffsetDateTime) -> Vec<u8> {
    let format = time::format_description::parse_borrowed::<2>(
        "[day]-[month repr:short]-[year] [hour]:[minute]",
    )
    .unwrap();
    format!(
        "<title>Index of /radar</title><tr><td><a href=\"sn.0000\">sn.0000</a></td><td align=\"right\">{}  </td></tr>",
        date.format(&format).unwrap()
    )
    .into_bytes()
}

fn statuses(result: &RadarTilts) -> Vec<&str> {
    result.tilts.iter().map(|cut| cut.status.as_str()).collect()
}

#[test]
fn parses_full_utc_dates_and_skips_bad_rows_and_sn_last() {
    let payload = br#"<title>Index of /radar</title>
<tr><td><a href="sn.0000">sn.0000</a></td><td align="right">31-Dec-2025 23:59  </td></tr>
<a href="sn.0001">scan</a>01-jAn-2026 00:30
<a href="sn.last">last</a>01-Jan-2026 00:31
<a href="sn.0002">bad</a>31-Feb-2026 00:30
<a href="sn.0003">bad</a>01-Jan-2026 24:30
<a href="sn.0004">bad</a>01-Jan-2026 00:30<a href="sn.12345">bad name</a>01-Jan-2026 00:31
<a href="sn.0005">bad</a>
01-Jan-2026 00:31"#;
    assert_eq!(
        scans(payload).unwrap(),
        vec![now() - time::Duration::minutes(31), now(), now()]
    );
    assert!(scans(b"<title>Index of /empty</title>").unwrap().is_empty());
    assert!(scans(b"<html>Upstream error</html>").is_err());
    assert!(scans(br#"<a href="sn.0000">bad</a>31-Feb-2026 00:30"#).is_err());
}

#[tokio::test]
async fn checks_seven_ordered_cuts_and_preserves_unknown_failures() {
    let server = Server::new(|path| {
        if path.contains("p99vz/") {
            Reply::new(404, "missing")
        } else if path.contains("p99v0/") {
            Reply::new(200, listing(now() - time::Duration::minutes(3)))
        } else if path.contains("p99va/") {
            Reply::new(200, listing(now() - time::Duration::minutes(80)))
        } else if path.contains("p99v1/") {
            Reply::new(500, "offline")
        } else if path.contains("p99vb/") {
            Reply::new(200, "<html>not a directory</html>")
        } else if path.contains("p99v2/") {
            Reply::new(200, "<title>Index of /empty</title>")
        } else {
            Reply::new(200, listing(now() + time::Duration::minutes(1)))
        }
    });
    let inventory = server.inventory();
    let clock = now().replace_nanosecond(123_456_789).unwrap();
    let result = inventory
        .availability(site("KTLX"), None, clock)
        .await
        .unwrap();
    assert_eq!(result.tilts.len(), 7);
    assert_eq!(result.site_id, "KTLX");
    assert_eq!(result.product, "velocity");
    assert_eq!(result.checked_at, "2026-01-01T00:30:00Z");
    assert_eq!(
        result
            .tilts
            .iter()
            .map(|cut| cut.tilt.as_str())
            .collect::<Vec<_>>(),
        TILTS
    );
    assert_eq!(
        statuses(&result),
        [
            "unavailable",
            "available",
            "unavailable",
            "unknown",
            "unknown",
            "unavailable",
            "unavailable"
        ]
    );
    assert_eq!(server.count(), 7);
    assert!(server
        .calls
        .lock()
        .unwrap()
        .iter()
        .all(|path| path.ends_with("/SI.ktlx/")));
    assert_eq!(inventory.directories.lock().unwrap().len(), 5);
}

#[tokio::test]
async fn history_reuses_listings_and_keeps_inclusive_time_boundaries() {
    let server = Server::new(|path| {
        let minutes = if path.contains("p99v0/") { 3 } else { 65 };
        Reply::new(200, listing(now() - time::Duration::minutes(minutes)))
    });
    let inventory = server.inventory();
    assert!(statuses(
        &inventory
            .availability(site("KTLX"), None, now())
            .await
            .unwrap()
    )
    .iter()
    .all(|status| *status == "available"));
    let old = inventory
        .availability(
            site("KTLX"),
            Some(now() - time::Duration::minutes(55)),
            now(),
        )
        .await
        .unwrap();
    assert_eq!(
        statuses(&old),
        [
            "available",
            "unavailable",
            "available",
            "available",
            "available",
            "available",
            "available"
        ]
    );
    let edge = inventory
        .availability(
            site("KTLX"),
            Some(now() - time::Duration::minutes(70)),
            now(),
        )
        .await
        .unwrap();
    assert_eq!(edge.tilts[0].status, "available");
    assert!(inventory
        .availability(
            site("KTLX"),
            Some(now() + time::Duration::minutes(1)),
            now()
        )
        .await
        .is_ok());
    assert_eq!(server.count(), 7);
    for target in [
        now() - time::Duration::minutes(70) - time::Duration::nanoseconds(1),
        now() + time::Duration::minutes(1) + time::Duration::nanoseconds(1),
    ] {
        assert!(inventory
            .availability(site("KTLX"), Some(target), now())
            .await
            .is_err());
    }
    assert_eq!(server.count(), 7);
}

#[tokio::test]
async fn forbidden_requires_nonempty_parent_listing_that_excludes_site() {
    let server = Server::new(|path| {
        if path.contains("/SI.") {
            return Reply::new(403, "forbidden");
        }
        match path {
            "/DS.p99vz/" => Reply::new(200, r#"<a href="SI.pgua/">other</a>"#),
            "/DS.p99v0/" => Reply::new(
                200,
                r#"<a href="SI.pgua/">other</a><a href="SI.ktlx/">requested</a>"#,
            ),
            "/DS.p99va/" => Reply::new(200, "<title>Index of /empty</title>"),
            "/DS.p99v1/" => Reply::new(
                200,
                r#"<a href="SI.ktlx.evil/">bad</a><a href="SI.KTLX/">bad</a>"#,
            ),
            "/DS.p99vb/" => Reply::new(503, r#"<a href="SI.pgua/">other</a>"#),
            _ => Reply::new(200, "<html>Upstream error</html>"),
        }
    });
    let inventory = server.inventory();
    let result = inventory
        .availability(site("KTLX"), None, now())
        .await
        .unwrap();
    assert_eq!(
        statuses(&result),
        [
            "unavailable",
            "unknown",
            "unknown",
            "unknown",
            "unknown",
            "unknown",
            "unknown"
        ]
    );
    assert_eq!(inventory.directories.lock().unwrap().len(), 1);
    assert_eq!(server.count(), 14);
    inventory
        .availability(site("KTLX"), None, now())
        .await
        .unwrap();
    assert_eq!(server.count(), 26);
}

#[tokio::test]
async fn cache_hits_expire_at_45_seconds_and_clear_and_evict_oldest() {
    let server = Server::new(|_| Reply::new(200, listing(now())));
    let inventory = server.inventory();
    let path = "DS.p99v0/SI.ktlx/";
    inventory.directory_scans(path).await.unwrap();
    inventory.directory_scans(path).await.unwrap();
    assert_eq!(server.count(), 1);
    inventory.directories.lock().unwrap()[0].cached_at = Instant::now() - CACHE_TTL;
    inventory.directory_scans(path).await.unwrap();
    assert_eq!(server.count(), 2);
    assert_eq!(inventory.directories.lock().unwrap().len(), 1);
    inventory.clear();
    assert!(inventory.directories.lock().unwrap().is_empty());
    inventory.directory_scans(path).await.unwrap();
    assert_eq!(server.count(), 3);
    {
        let mut cache = inventory.directories.lock().unwrap();
        cache.clear();
        for index in 0..CACHE_LIMIT {
            cache.push_back(Directory {
                path: format!("entry-{index}"),
                cached_at: Instant::now(),
                scans: Vec::new(),
            });
        }
    }
    inventory.directory_scans(path).await.unwrap();
    let cache = inventory.directories.lock().unwrap();
    assert_eq!(cache.len(), CACHE_LIMIT);
    assert_eq!(cache.front().unwrap().path, "entry-1");
    assert_eq!(cache.back().unwrap().path, path);
}

#[tokio::test]
async fn transport_errors_are_unknown_and_are_never_cached() {
    let server = Server::new(|_| Reply::new(0, ""));
    let inventory = server.inventory();
    for _ in 0..2 {
        let result = inventory
            .availability(site("KTLX"), None, now())
            .await
            .unwrap();
        assert!(statuses(&result).iter().all(|status| *status == "unknown"));
    }
    assert!(inventory.directories.lock().unwrap().is_empty());
    assert!(server.count() >= 14);
}

#[tokio::test]
async fn enforces_byte_limit_with_and_without_content_length() {
    let server = Server::new(|path| {
        let mut body = b"Index of /radar\n".to_vec();
        body.resize(
            if path.contains("exact/") {
                BYTE_LIMIT
            } else {
                BYTE_LIMIT + 1
            },
            b' ',
        );
        Reply {
            status: 200,
            body,
            content_length: !path.contains("stream/"),
        }
    });
    let inventory = server.inventory();
    assert!(inventory
        .directory_scans("exact/SI.ktlx/")
        .await
        .unwrap()
        .is_empty());
    for path in ["large/SI.ktlx/", "stream/SI.ktlx/"] {
        let error = inventory.directory_scans(path).await.unwrap_err();
        assert!(error.contains("byte budget"), "{error}");
    }
    assert_eq!(inventory.directories.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn request_limit_is_shared_across_inventory_instances() {
    let active = Arc::new(AtomicUsize::new(0));
    let peak = Arc::new(AtomicUsize::new(0));
    let (pending, maximum) = (active.clone(), peak.clone());
    let server = Server::new(move |_| {
        let count = pending.fetch_add(1, Ordering::SeqCst) + 1;
        maximum.fetch_max(count, Ordering::SeqCst);
        thread::sleep(Duration::from_millis(15));
        pending.fetch_sub(1, Ordering::SeqCst);
        Reply::new(200, listing(now()))
    });
    let (first, second) = (server.inventory(), server.inventory());
    let (first, second) = tokio::join!(
        first.availability(site("KTLX"), None, now()),
        second.availability(site("PGUA"), None, now())
    );
    assert_eq!(first.unwrap().tilts.len(), 7);
    assert_eq!(second.unwrap().tilts.len(), 7);
    assert_eq!(server.count(), 14);
    assert!((1..=3).contains(&peak.load(Ordering::SeqCst)));
    assert_eq!(active.load(Ordering::SeqCst), 0);
}

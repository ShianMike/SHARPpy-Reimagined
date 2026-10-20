//! The supervisor against scripted fake children (DESIGN-bridge B6 and B8):
//! every frame class, exit before `hello`, the paused-clock request-phase and
//! backoff cases, the `restarts` counter, restart, and shutdown.

mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tokio::runtime::Handle;
use tokio::time::{sleep, Instant};

use common::{
    bridge, fixture, hello_result, is_unavailable, is_up, script, FakeLauncher, NotConfigured,
    RecordingSink,
};
use sharppy_reimagined_lib::dto::health::BridgeStatus;
use sharppy_reimagined_lib::error::{BackendError, BridgeFailure, ErrorCode, ErrorContext};
use sharppy_reimagined_lib::python::launcher::ProcessLauncher;
use sharppy_reimagined_lib::python::protocol::CatalogResult;
use sharppy_reimagined_lib::python::supervisor::{Supervisor, SupervisorConfig};

const SECOND: Duration = Duration::from_secs(1);

fn start(launcher: Arc<dyn ProcessLauncher>) -> (Supervisor, Arc<RecordingSink>) {
    let sink = Arc::new(RecordingSink::default());
    let supervisor = Supervisor::new(
        SupervisorConfig::default(),
        launcher,
        sink.clone(),
        Handle::current(),
    );
    supervisor.start();
    (supervisor, sink)
}

async fn catalog(supervisor: &Supervisor) -> Result<Value, BridgeFailure> {
    supervisor
        .request("stations.catalog", json!({}), 15 * SECOND)
        .await
}

fn public(failure: BridgeFailure) -> BackendError {
    failure.into_backend_error(&ErrorContext::new(
        "get_station_catalog",
        Some("python-bridge"),
        Some("bundled UWyo station catalogue"),
    ))
}

fn detail<'a>(error: &'a BackendError, key: &str) -> &'a Value {
    error
        .details
        .as_ref()
        .and_then(|d| d.get(key))
        .unwrap_or(&Value::Null)
}

#[tokio::test(start_paused = true)]
async fn starts_up_and_serves_typed_requests() {
    let launcher = FakeLauncher::with(vec![]);
    let (supervisor, sink) = start(launcher.clone());
    let result: CatalogResult = supervisor
        .request_typed("stations.catalog", json!({}), 15 * SECOND)
        .await
        .unwrap();
    assert_eq!(result.stations.len(), 6);
    assert_eq!(sink.names(), ["starting", "up"]);
    assert!(matches!(
        sink.statuses()[1],
        BridgeStatus::Up {
            protocol_version: 1,
            pid: 4242,
            restarts: 0,
            ..
        }
    ));
    assert_eq!(supervisor.hello().unwrap().python_version, "3.11.14");
    assert_eq!(launcher.spawns(), 1);
}

#[tokio::test(start_paused = true)]
async fn reported_errors_retain_request_correlation_and_explicit_cancellation() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        for cancelled in [true, false] {
            let request = io.expect("fields.grid").await.unwrap();
            io.reply_error(
                &request["id"],
                json!({
                    "code": "INTERNAL", "message": "original message", "retryable": false,
                    "details": {"cancelled": cancelled, "provider": "test"}
                }),
            )
            .await;
        }
        bridge(io, Arc::default()).await
    })]);
    let (supervisor, _sink) = start(launcher);
    for cancelled in [true, false] {
        let error = public(
            supervisor
                .request("fields.grid", json!({"request_id": "consumer123"}), SECOND)
                .await
                .unwrap_err(),
        );
        assert_eq!(error.code, ErrorCode::Internal);
        assert_eq!(detail(&error, "cancelled"), cancelled);
        assert_eq!(detail(&error, "provider"), "test");
        if cancelled {
            assert_eq!(error.message, "The request was cancelled.");
        } else {
            assert!(error.message.starts_with("Something went wrong"));
        }
        assert_eq!(detail(&error, "request_id"), "consumer123");
        assert_eq!(detail(&error, "method"), "fields.grid");
        assert!(detail(&error, "wire_id").as_u64().unwrap() > 0);
        assert_eq!(detail(&error, "generation"), 1);
    }
}

#[tokio::test(start_paused = true)]
async fn oversized_outbound_request_does_not_poison_other_replies() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let first = io.expect("health").await.unwrap();
        let second = io.expect("stations.catalog").await.unwrap();
        io.reply(&first["id"], fixture("bridge/health.result.ok.json"))
            .await;
        io.reply(
            &second["id"],
            fixture("bridge/stations.catalog.result.sample.json"),
        )
        .await;
        bridge(io, Arc::default()).await
    })]);
    let (supervisor, _sink) = start(launcher);
    let health = supervisor.request("health", json!({}), 20 * SECOND);
    let upload = async {
        sleep(Duration::from_millis(10)).await;
        let error = supervisor
            .request(
                "soundings.open_file",
                json!({"content_base64": "x".repeat(8 * 1024 * 1024)}),
                20 * SECOND,
            )
            .await
            .unwrap_err();
        assert!(matches!(error, BridgeFailure::Protocol { .. }));
        catalog(&supervisor).await
    };
    let (health, stations) = tokio::join!(health, upload);
    assert!(health.is_ok() && stations.is_ok());
}

#[tokio::test(start_paused = true)]
async fn replies_are_routed_by_id_out_of_order_and_accept_crlf() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let first = io.expect("health").await.unwrap();
        let second = io.expect("stations.catalog").await.unwrap();
        let catalog = serde_json::to_vec(&json!({"id": second["id"], "result": fixture("bridge/stations.catalog.result.sample.json")})).unwrap();
        io.send_raw(&[catalog, b"\r\n".to_vec()].concat()).await;
        io.reply(&first["id"], fixture("bridge/health.result.ok.json"))
            .await;
        bridge(io, Arc::default()).await
    })]);
    let (supervisor, _sink) = start(launcher);
    let health = supervisor.request("health", json!({}), 20 * SECOND);
    let stations = async {
        sleep(Duration::from_millis(10)).await;
        catalog(&supervisor).await
    };
    let (health, stations) = tokio::join!(health, stations);
    assert!(health.unwrap()["scientific_backend"]["status"] == "ok");
    assert_eq!(stations.unwrap()["stations"].as_array().unwrap().len(), 6);
}

#[tokio::test(start_paused = true)]
async fn late_reply_after_a_timeout_is_dropped_without_a_restart() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let request = io.expect("stations.catalog").await.unwrap();
        let cancel = io.expect("cancel").await.unwrap();
        assert_eq!(cancel["params"]["id"], request["id"]);
        io.reply(&cancel["id"], json!({"ok": true})).await;
        let ping = io.next_request().await.unwrap();
        assert_eq!(ping["method"], "hello");
        io.reply(&ping["id"], hello_result()).await;
        io.reply(
            &request["id"],
            fixture("bridge/stations.catalog.result.sample.json"),
        )
        .await;
        bridge(io, Arc::default()).await
    })]);
    let (supervisor, sink) = start(launcher.clone());
    let error = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(error.code, ErrorCode::BridgeTimeout);
    assert_eq!(detail(&error, "phase"), "awaitingReply");
    sleep(SECOND).await;
    assert!(catalog(&supervisor).await.is_ok());
    assert_eq!(launcher.spawns(), 1);
    assert_eq!(sink.names(), ["starting", "up"]);
}

#[tokio::test(start_paused = true)]
async fn bad_payloads_fail_only_their_own_request() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let a = io.expect("stations.catalog").await.unwrap();
        io.reply(&a["id"], json!({"stations": [], "origin": "bundled"}))
            .await;
        let b = io.expect("stations.catalog").await.unwrap();
        io.reply_error(
            &b["id"],
            json!({"code": "BACKEND_UNAVAILABLE", "message": "health-only", "retryable": false}),
        )
        .await;
        let c = io.expect("stations.catalog").await.unwrap();
        io.reply_error(&c["id"], json!({"code": "CATALOG_UNAVAILABLE", "message": "The station catalogue could not be loaded.", "retryable": true})).await;
        let d = io.expect("stations.catalog").await.unwrap();
        io.reply_error(
            &d["id"],
            json!({"code": "METHOD_NOT_FOUND", "message": "x", "retryable": false}),
        )
        .await;
        bridge(io, Arc::default()).await
    })]);
    let (supervisor, sink) = start(launcher.clone());
    let mismatch = supervisor
        .request_typed::<CatalogResult>("stations.catalog", json!({}), 15 * SECOND)
        .await;
    let mismatch = public(mismatch.unwrap_err());
    assert_eq!(mismatch.code, ErrorCode::BridgeProtocol);
    assert_eq!(detail(&mismatch, "method"), "stations.catalog");
    let unknown_code = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(
        (unknown_code.code, detail(&unknown_code, "method").clone()),
        (ErrorCode::BridgeProtocol, json!("stations.catalog"))
    );
    let python = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(
        (python.code, python.retryable),
        (ErrorCode::CatalogUnavailable, true)
    );
    let missing = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(missing.code, ErrorCode::MethodNotFound);
    assert!(
        missing.message.contains("get_station_catalog"),
        "{}",
        missing.message
    );
    assert!(catalog(&supervisor).await.is_ok());
    assert_eq!(launcher.spawns(), 1);
    assert_eq!(sink.names(), ["starting", "up"]);
}

#[tokio::test(start_paused = true)]
async fn null_id_fails_pending_requests_without_a_restart() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        io.expect("stations.catalog").await.unwrap();
        io.send(json!({"id": null, "error": {"code": "BRIDGE_PROTOCOL", "message": "Request frame is not valid JSON.", "retryable": false}})).await;
        bridge(io, Arc::default()).await
    })]);
    let (supervisor, sink) = start(launcher.clone());
    assert_eq!(
        public(catalog(&supervisor).await.unwrap_err()).code,
        ErrorCode::BridgeProtocol
    );
    assert!(catalog(&supervisor).await.is_ok());
    assert_eq!(launcher.spawns(), 1);
    assert_eq!(sink.names(), ["starting", "up"]);
}

#[tokio::test(start_paused = true)]
async fn a_corrupt_stream_fails_pending_requests_and_restarts_the_child() {
    let mut oversized = vec![b'x'; 8 * 1024 * 1024 + 4];
    oversized.push(b'\n');
    let corrupt: Vec<Vec<u8>> = vec![
        b"not json\n".to_vec(),
        b"{\"id\":1,\"result\":NaN}\n".to_vec(),
        b"\xff\xfe\n".to_vec(),
        b"{\"id\":1,\"result\":{},\"extra\":1}\n".to_vec(),
        oversized,
    ];
    for line in corrupt {
        let launcher = FakeLauncher::with(vec![script(move |mut io| async move {
            io.expect("stations.catalog").await.unwrap();
            io.send_raw(&line).await;
            std::future::pending::<i32>().await
        })]);
        let (supervisor, sink) = start(launcher.clone());
        let error = public(catalog(&supervisor).await.unwrap_err());
        assert_eq!(error.code, ErrorCode::BridgeProtocol);
        sink.wait_for(2, is_up).await;
        assert_eq!(launcher.spawns(), 2);
        assert_eq!(
            sink.names(),
            ["starting", "up", "restarting", "starting", "up"]
        );
        let BridgeStatus::Restarting {
            attempt: 1,
            retry_in_ms: 500,
            last_error,
        } = &sink.statuses()[2]
        else {
            panic!("{:?}", sink.statuses());
        };
        assert_eq!(last_error.code, ErrorCode::BridgeProtocol);
        assert!(catalog(&supervisor).await.is_ok());
    }
}

#[tokio::test(start_paused = true)]
async fn a_crash_mid_request_fails_it_with_bridge_crashed_and_restarts() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        io.expect("stations.catalog").await.unwrap();
        3
    })]);
    let (supervisor, sink) = start(launcher.clone());
    let error = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(
        (error.code, error.retryable),
        (ErrorCode::BridgeCrashed, true)
    );
    sink.wait_for(2, is_up).await;
    let statuses = sink.statuses();
    assert_eq!(
        sink.names(),
        ["starting", "up", "restarting", "starting", "up"]
    );
    assert!(
        matches!(&statuses[2], BridgeStatus::Restarting { attempt: 1, retry_in_ms: 500, last_error } if last_error.code == ErrorCode::BridgeCrashed)
    );
    assert!(matches!(statuses[4], BridgeStatus::Up { restarts: 1, .. }));
    assert!(catalog(&supervisor).await.is_ok());
}

#[tokio::test(start_paused = true)]
async fn exit_before_hello_is_unavailable_without_a_second_spawn() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let _ = io
            .stderr
            .write_all(b"ModuleNotFoundError: No module named 'sharpmod'\n")
            .await;
        1
    })]);
    let (supervisor, sink) = start(launcher.clone());
    sink.wait_for(1, is_unavailable).await;
    sleep(120 * SECOND).await;
    assert_eq!(launcher.spawns(), 1);
    let BridgeStatus::Unavailable { error } = supervisor.status() else {
        panic!()
    };
    assert_eq!(error.code, ErrorCode::BridgeSpawnFailed);
    assert_eq!(detail(&error, "exitCode"), 1);
    assert!(
        error.message.contains("before the handshake"),
        "{}",
        error.message
    );
    let request = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(request.code, ErrorCode::BridgeSpawnFailed);
    assert_eq!(request.context.unwrap().operation, "get_station_catalog");
}

#[tokio::test(start_paused = true)]
async fn handshake_problems_are_classified() {
    let wrong_protocol = script(|mut io| async move {
        let hello = io.next_request().await.unwrap();
        io.reply(&hello["id"], json!({"protocol_version": 2, "sharpmod_version": "9", "python_version": "3.11.0", "python_executable": "p", "pid": 1})).await;
        std::future::pending::<i32>().await
    });
    let old_python = script(|mut io| async move {
        let hello = io.next_request().await.unwrap();
        io.reply(&hello["id"], json!({"protocol_version": 1, "sharpmod_version": "3.0.0", "python_version": "3.10.12", "python_executable": "p", "pid": 1})).await;
        std::future::pending::<i32>().await
    });
    for (child, code) in [
        (wrong_protocol, ErrorCode::BridgeProtocol),
        (old_python, ErrorCode::BridgeSpawnFailed),
    ] {
        let (supervisor, sink) = start(FakeLauncher::with(vec![child]));
        sink.wait_for(1, is_unavailable).await;
        let BridgeStatus::Unavailable { error } = supervisor.status() else {
            panic!()
        };
        assert_eq!(error.code, code);
    }
    let (supervisor, sink) = start(Arc::new(NotConfigured));
    sink.wait_for(1, is_unavailable).await;
    let error = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(
        (error.code, error.retryable),
        (ErrorCode::PythonNotFound, false)
    );
}

/// B8 case 1: a request queued behind a slow start fails on its own deadline
/// without pinging, killing, or counting anything.
#[tokio::test(start_paused = true)]
async fn queued_request_times_out_in_waiting_for_bridge() {
    let hellos = Arc::new(AtomicUsize::new(0));
    let counted = Arc::clone(&hellos);
    let launcher = FakeLauncher::with(vec![
        script(|_io| std::future::pending::<i32>()),
        script(move |mut io| async move {
            let hello = io.next_request().await.unwrap();
            counted.fetch_add(1, Ordering::SeqCst);
            sleep(5 * SECOND).await;
            io.reply(&hello["id"], hello_result()).await;
            while let Some(request) = io.next_request().await {
                if request["method"] == "hello" {
                    counted.fetch_add(1, Ordering::SeqCst);
                }
            }
            0
        }),
    ]);
    let t0 = Instant::now();
    let (supervisor, sink) = start(launcher.clone());
    sleep(SECOND).await;
    let error = public(catalog(&supervisor).await.unwrap_err());
    assert_eq!(t0.elapsed(), 16 * SECOND);
    assert_eq!(error.code, ErrorCode::BridgeTimeout);
    assert_eq!(detail(&error, "phase"), "waitingForBridge");
    assert_eq!(detail(&error, "state"), "starting");
    sink.wait_for(1, is_up).await;
    assert_eq!(
        sink.time_of(0, is_up).unwrap() - t0,
        Duration::from_millis(20_500)
    );
    assert_eq!(launcher.spawns(), 2);
    assert_eq!(sink.names(), ["starting", "restarting", "starting", "up"]);
    assert!(matches!(
        sink.statuses()[3],
        BridgeStatus::Up { restarts: 0, .. }
    ));
    sleep(30 * SECOND).await;
    assert_eq!(hellos.load(Ordering::SeqCst), 1, "child 2 received a ping");
}

fn up_then_exit(after: Duration) -> common::Script {
    script(move |mut io| async move {
        let hello = io.next_request().await.unwrap();
        io.reply(&hello["id"], hello_result()).await;
        sleep(after).await;
        1
    })
}

/// B8 case 2: backoff doubles from 0.5 s and resets once a child stayed up 60 s.
#[tokio::test(start_paused = true)]
async fn backoff_doubles_and_resets_after_a_minute_up() {
    let zero = Duration::ZERO;
    let launcher = FakeLauncher::with(vec![
        up_then_exit(zero),
        up_then_exit(zero),
        up_then_exit(zero),
        up_then_exit(60 * SECOND),
    ]);
    let (_supervisor, sink) = start(launcher.clone());
    sink.wait_for(5, is_up).await;
    let restarting: Vec<(u32, u64)> = sink
        .statuses()
        .iter()
        .filter_map(|s| match s {
            BridgeStatus::Restarting {
                attempt,
                retry_in_ms,
                ..
            } => Some((*attempt, *retry_in_ms)),
            _ => None,
        })
        .collect();
    assert_eq!(restarting, [(1, 500), (2, 1000), (3, 2000), (1, 500)]);
    let is_restarting = |s: &BridgeStatus| matches!(s, BridgeStatus::Restarting { .. });
    let is_starting = |s: &BridgeStatus| matches!(s, BridgeStatus::Starting { .. });
    for (n, wait) in [(0, 500), (1, 1000), (2, 2000), (3, 500)] {
        let delay =
            sink.time_of(n + 1, is_starting).unwrap() - sink.time_of(n, is_restarting).unwrap();
        assert_eq!(delay, Duration::from_millis(wait), "restart {n}");
    }
    assert_eq!(launcher.spawns(), 5);
}

/// The fifth crash inside 60 s stops automatic restarts; only restart_backend leaves.
#[tokio::test(start_paused = true)]
async fn five_crashes_in_a_minute_pause_restarts_until_restart_backend() {
    let zero = Duration::ZERO;
    let launcher = FakeLauncher::with((0..5).map(|_| up_then_exit(zero)).collect());
    let (supervisor, sink) = start(launcher.clone());
    sink.wait_for(1, is_unavailable).await;
    let BridgeStatus::Unavailable { error } = supervisor.status() else {
        panic!()
    };
    assert_eq!(error.code, ErrorCode::BridgeUnavailable);
    assert_eq!(detail(&error, "lastErrorCode"), "BRIDGE_CRASHED");
    sleep(120 * SECOND).await;
    assert_eq!(launcher.spawns(), 5);
    assert_eq!(
        public(catalog(&supervisor).await.unwrap_err()).code,
        ErrorCode::BridgeUnavailable
    );
    let status = supervisor.restart().await;
    assert!(
        matches!(status, BridgeStatus::Up { restarts: 5, .. }),
        "{status:?}"
    );
    assert!(catalog(&supervisor).await.is_ok());
}

/// B8 case 3: two requests that time out together send exactly one ping.
#[tokio::test(start_paused = true)]
async fn simultaneous_timeouts_send_one_liveness_ping() {
    let pings = Arc::new(AtomicUsize::new(0));
    let counted = Arc::clone(&pings);
    let launcher = FakeLauncher::with(vec![script(move |mut io| async move {
        let hello = io.next_request().await.unwrap();
        io.reply(&hello["id"], hello_result()).await;
        while let Some(request) = io.next_request().await {
            if request["method"] == "hello" {
                counted.fetch_add(1, Ordering::SeqCst);
                io.reply(&request["id"], hello_result()).await;
            }
        }
        0
    })]);
    let (supervisor, sink) = start(launcher.clone());
    sink.wait_for(1, is_up).await;
    let (a, b) = tokio::join!(catalog(&supervisor), catalog(&supervisor));
    for error in [a, b] {
        let error = public(error.unwrap_err());
        assert_eq!(error.code, ErrorCode::BridgeTimeout);
        assert_eq!(detail(&error, "phase"), "awaitingReply");
    }
    sleep(10 * SECOND).await;
    assert_eq!(pings.load(Ordering::SeqCst), 1);
    assert_eq!(launcher.spawns(), 1);
    assert_eq!(sink.names(), ["starting", "up"]);
}

/// A failed ping kills the hung child and counts one crash.
#[tokio::test(start_paused = true)]
async fn a_failed_ping_restarts_a_hung_child() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let hello = io.next_request().await.unwrap();
        io.reply(&hello["id"], hello_result()).await;
        std::future::pending::<i32>().await
    })]);
    let (supervisor, sink) = start(launcher.clone());
    assert_eq!(
        public(catalog(&supervisor).await.unwrap_err()).code,
        ErrorCode::BridgeTimeout
    );
    sink.wait_for(2, is_up).await;
    assert_eq!(launcher.spawns(), 2);
    let BridgeStatus::Restarting { last_error, .. } = &sink.statuses()[2] else {
        panic!()
    };
    assert_eq!(detail(last_error, "reason"), "livenessPingFailed");
}

/// B8 case 4: `restarts` is 0, then 1 after a crash, then 2 after restart_backend.
#[tokio::test(start_paused = true)]
async fn restarts_counts_every_up_after_the_first() {
    let launcher = FakeLauncher::with(vec![up_then_exit(SECOND)]);
    let (supervisor, sink) = start(launcher.clone());
    sink.wait_for(2, is_up).await;
    let ups: Vec<u32> = sink
        .statuses()
        .iter()
        .filter_map(|s| match s {
            BridgeStatus::Up { restarts, .. } => Some(*restarts),
            _ => None,
        })
        .collect();
    assert_eq!(ups, [0, 1]);
    assert!(matches!(
        supervisor.restart().await,
        BridgeStatus::Up { restarts: 2, .. }
    ));
}

#[tokio::test(start_paused = true)]
async fn restart_backend_fails_pending_requests_and_joins_a_running_restart() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        io.expect("stations.catalog").await.unwrap();
        std::future::pending::<i32>().await
    })]);
    let (supervisor, sink) = start(launcher.clone());
    sink.wait_for(1, is_up).await;
    let pending = tokio::spawn({
        let supervisor = supervisor.clone();
        async move { catalog(&supervisor).await }
    });
    sleep(SECOND).await;
    let (first, second) = tokio::join!(supervisor.restart(), supervisor.restart());
    assert!(is_up(&first) && is_up(&second), "{first:?} {second:?}");
    assert_eq!(launcher.spawns(), 2);
    let error = public(pending.await.unwrap().unwrap_err());
    assert_eq!(error.code, ErrorCode::BridgeCrashed);
    assert_eq!(detail(&error, "reason"), "restartRequested");
    assert!(error
        .message
        .contains("restarted while this request was running"));
}

#[tokio::test(start_paused = true)]
async fn shutdown_asks_the_child_to_exit_and_kills_a_child_that_does_not() {
    let (supervisor, sink) = start(FakeLauncher::with(vec![]));
    sink.wait_for(1, is_up).await;
    assert_eq!(supervisor.finish_shutdown().await, Some(0));
    let deaf = script(|mut io| async move {
        let hello = io.next_request().await.unwrap();
        io.reply(&hello["id"], hello_result()).await;
        while io.next_request().await.is_some() {}
        std::future::pending::<i32>().await
    });
    let (supervisor, sink) = start(FakeLauncher::with(vec![deaf]));
    sink.wait_for(1, is_up).await;
    let t0 = Instant::now();
    supervisor.begin_shutdown();
    assert_eq!(supervisor.finish_shutdown().await, None);
    assert!(t0.elapsed() >= 2 * SECOND);
    assert_eq!(sink.names(), ["starting", "up"]);
}

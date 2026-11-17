use super::*;
use serde_json::json;

#[test]
fn retention_forwards_open_ids_and_rejects_invalid_input_or_acknowledgements() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let request = io.expect("soundings.retain").await.unwrap();
        assert_eq!(request["params"], json!({"sounding_ids":["abc123"]}));
        io.reply(&request["id"], json!({"retained":true})).await;
        let request = io.expect("soundings.retain").await.unwrap();
        assert_eq!(request["params"], json!({"sounding_ids":[]}));
        io.reply(&request["id"], json!({"retained":false})).await;
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    assert_eq!(
        invoke_args(
            &webview,
            "retain_soundings",
            json!({"soundingIds":["abc123"]})
        )
        .unwrap(),
        json!({"retained":true})
    );
    assert!(invoke_args(&webview, "retain_soundings", json!({"soundingIds":[]})).is_err());
    for ids in [json!(["../source"]), json!(vec!["abc123"; 25])] {
        let error: BackendError = parse(
            invoke_args(&webview, "retain_soundings", json!({"soundingIds":ids})).unwrap_err(),
        );
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}

#[test]
fn recovery_commands_forward_sources_and_validate_responses() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let request = io.expect("soundings.snapshot").await.unwrap();
        assert_eq!(request["params"], json!({"sounding_id":"abc123"}));
        io.reply(
            &request["id"],
            json!({"sounding_id":"abc123", "payload":"saved-source"}),
        )
        .await;
        let request = io.expect("soundings.restore").await.unwrap();
        assert_eq!(request["params"], json!({"payload":"saved-source"}));
        io.reply(
            &request["id"],
            fixture("bridge/soundings/open.result.example.json"),
        )
        .await;
        let request = io.expect("soundings.snapshot").await.unwrap();
        io.reply(
            &request["id"],
            json!({"sounding_id":"different", "payload":"saved-source"}),
        )
        .await;
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    assert_eq!(
        invoke_args(
            &webview,
            "get_sounding_snapshot",
            json!({"soundingId":"abc123"})
        )
        .unwrap(),
        json!({"soundingId":"abc123", "payload":"saved-source"})
    );
    assert_eq!(
        invoke_args(
            &webview,
            "restore_sounding_snapshot",
            json!({"payload":"saved-source"})
        )
        .unwrap(),
        fixture("public/soundings/summary.example.json")
    );
    assert!(invoke_args(
        &webview,
        "get_sounding_snapshot",
        json!({"soundingId":"abc123"})
    )
    .is_err());
    for (command, args) in [
        ("get_sounding_snapshot", json!({"soundingId":"../source"})),
        ("restore_sounding_snapshot", json!({"payload":""})),
    ] {
        let error: BackendError = parse(invoke_args(&webview, command, args).unwrap_err());
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}

"""Live transfer counters count received data, never a planned file size."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from sharpmod.bridge import sounding_progress as progress
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.public import bridge_request, public_result


def test_speed_uses_received_bytes_and_decays_when_stalled(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(progress, "monotonic", lambda: clock[0])
    with progress.track("download1") as download:
        download.stage("downloading", 9_000_000)
        assert download.snapshot()["downloaded_bytes"] == 0
        clock[0] = 1
        download.receive(500_000)
        clock[0] = 2
        download.receive(500_000)
        assert download.snapshot() == {
            "stage": "downloading", "downloaded_bytes": 1_000_000,
            "bytes_per_second": 500_000,
        }
        clock[0] = 6
        assert download.snapshot()["bytes_per_second"] == 0
        download.stage("decoding")
        assert download.snapshot()["stage"] == "decoding"
        download.stage("calculating")
        assert download.snapshot()["stage"] == "calculating"
        download.stage("complete")
        assert download.snapshot()["bytes_per_second"] == 0
    assert "download1" not in progress._active


@pytest.mark.parametrize("stage", ["decoding", "opening", "extracting", "cached"])
def test_decode_stages_preserve_received_bytes_without_transfer_speed(stage):
    with progress.track("decode1") as download:
        download.receive(500)
        download.stage(stage)
        assert download.snapshot() == {
            "stage": "decoding", "downloaded_bytes": 500, "bytes_per_second": 0,
        }


def test_parallel_chunks_are_isolated_and_failures_release_counters():
    with pytest.raises(RuntimeError), progress.track("one") as one:
        with progress.track("two") as two, ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one.receive, [1024] * 100))
            two.receive(4)
            assert progress.get_progress({"progress_id": "one"})["downloaded_bytes"] == 102_400
            assert progress.get_progress({"progress_id": "two"})["downloaded_bytes"] == 4
            with pytest.raises(BridgeError):
                with progress.track("one"):
                    pass
        raise RuntimeError("fetch failed")
    assert not progress._active


@pytest.mark.parametrize("invalid_id", ["", "../data", "a" * 65, "é", 1, None])
def test_progress_ids_are_validated(invalid_id):
    with pytest.raises(BridgeError):
        progress.get_progress({"progress_id": invalid_id})


def test_public_adapter_preserves_request_id_and_camel_case():
    method, params = bridge_request("open_sounding", {
        "request": {"kind": "model", "model": "hrrr"}, "progressId": "one",
    })
    assert method == "soundings.open_model"
    assert params == {"model": "hrrr", "progress_id": "one"}
    assert bridge_request("get_sounding_progress", {"progressId": "one"}) == (
        "soundings.progress", {"progress_id": "one"},
    )
    assert public_result("get_sounding_progress", progress.get_progress({"progress_id": "one"})) == {
        "stage": "waiting", "downloadedBytes": 0, "bytesPerSecond": 0,
    }
    with pytest.raises(BridgeError):
        progress.get_progress({"progress_id": "one", "extra": 1})

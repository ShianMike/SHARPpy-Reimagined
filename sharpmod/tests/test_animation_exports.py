"""Atomic fixed-scale GIF export contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json

from PIL import Image
import pytest

from sharpmod.animation_exports import (
    AnimationExportError,
    AnimationFrame,
    ExportCancelled,
    animation_frame_preview_png,
    prepare_animation_frames,
    write_animation_gif,
)


VALID = datetime(2026, 9, 13, 0, tzinfo=timezone.utc)


def _png(colour="navy", size=(80, 60)):
    image = Image.new("RGB", size, colour)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_gif_order_mode_labels_and_missing_frame_are_embedded(tmp_path):
    frames = (
        AnimationFrame("F000", _png("red"), VALID, VALID, 0),
        AnimationFrame(
            "F003",
            None,
            VALID + timedelta(hours=3),
            VALID,
            3,
            "provider returned no sounding",
        ),
        AnimationFrame(
            "F006", _png("blue"), VALID + timedelta(hours=6), VALID, 6
        ),
    )
    progress = []
    path = write_animation_gif(
        tmp_path / "timeline.gif",
        frames,
        mode="forecast-timeline",
        frame_duration_ms=400,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert path.read_bytes().startswith(b"GIF89a")
    with Image.open(path) as image:
        assert image.n_frames == 3
        manifest = json.loads(image.info["comment"].decode("utf-8"))
    assert manifest["mode"] == "forecast-timeline"
    assert [label.split(" · ")[0] for label in manifest["labels"]] == [
        "F000",
        "F003",
        "F006",
    ]
    assert manifest["missing"][1] == "provider returned no sounding"
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_gif_stores_prepared_frames_as_palettes(monkeypatch, tmp_path):
    frames = (
        AnimationFrame("F000", _png("red", size=(320, 240)), VALID),
        AnimationFrame("F001", _png("blue", size=(320, 240)), VALID),
    )
    original = Image.Image.save
    captured_modes = []

    def capture_modes(image, fp, *args, **kwargs):
        if kwargs.get("format") == "GIF":
            appended = tuple(kwargs.get("append_images", ()))
            captured_modes.append((image.mode, *(item.mode for item in appended)))
        return original(image, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", capture_modes)
    write_animation_gif(
        tmp_path / "palette.gif", frames, mode="forecast-timeline"
    )

    assert captured_modes == [("P", "P")]


def test_run_to_run_labels_name_both_run_and_valid_time(tmp_path):
    frames = (
        AnimationFrame("Newest", _png(), VALID, VALID - timedelta(hours=3), 3),
        AnimationFrame("Older", _png(), VALID, VALID - timedelta(hours=6), 6),
    )
    path = write_animation_gif(
        tmp_path / "runs.gif", frames, mode="run-to-run"
    )
    with Image.open(path) as image:
        manifest = json.loads(image.info["comment"].decode("utf-8"))
    assert "run 2026-09-12 21:00Z" in manifest["labels"][0]
    assert all("valid 2026-09-13 00:00Z" in item for item in manifest["labels"])


def test_cancelled_gif_leaves_existing_destination_unchanged(tmp_path):
    destination = tmp_path / "timeline.gif"
    destination.write_bytes(b"prior complete artifact")
    calls = []

    with pytest.raises(ExportCancelled, match="1/2"):
        write_animation_gif(
            destination,
            (
                AnimationFrame("one", _png(), VALID),
                AnimationFrame("two", _png(), VALID + timedelta(hours=1)),
            ),
            mode="forecast-timeline",
            cancelled=lambda: len(calls) >= 1,
            progress=lambda *_args: calls.append(True),
        )

    assert destination.read_bytes() == b"prior complete artifact"
    assert not list(tmp_path.glob("*.gif.*"))


def test_fixed_scale_contract_rejects_mixed_frame_sizes(tmp_path):
    with pytest.raises(AnimationExportError, match="different dimensions"):
        write_animation_gif(
            tmp_path / "bad.gif",
            (
                AnimationFrame("small", _png(size=(40, 30)), VALID),
                AnimationFrame("large", _png(size=(80, 60)), VALID),
            ),
            mode="forecast-timeline",
        )


@pytest.mark.parametrize("policy,expected_count", [("cards", 3), ("stop", 1)])
def test_nonconsecutive_selected_times_do_not_join_silently(
    tmp_path, policy, expected_count
):
    frames = (
        AnimationFrame("F000", _png("red"), VALID, VALID, 0),
        AnimationFrame(
            "F006", _png("blue"), VALID + timedelta(hours=6), VALID, 6
        ),
    )
    planned = prepare_animation_frames(
        frames, mode="forecast-timeline", gap_policy=policy,
        expected_interval_seconds=3 * 3600,
    )
    assert len(planned) == expected_count
    if policy == "cards":
        assert planned[1].png_bytes is None
        assert "discontinuity" in planned[1].missing_reason
    path = write_animation_gif(
        tmp_path / f"{policy}.gif", frames, mode="forecast-timeline",
        gap_policy=policy, expected_interval_seconds=3 * 3600,
        output_size=(640, 480), frame_duration_ms=350, theme="light",
    )
    with Image.open(path) as image:
        assert image.size == (640, 480)
        assert image.n_frames == expected_count
        assert image.info["duration"] == 350
        manifest = json.loads(image.info["comment"].decode("utf-8"))
        assert manifest["gap_policy"] == policy
        assert manifest["output_frame_count"] == expected_count
        assert manifest["dimensions"] == [640, 480]
        if policy == "cards":
            assert "discontinuity" in manifest["missing"][1]


def test_error_policy_preserves_prior_destination_and_rejects_failed_frame(tmp_path):
    existing = tmp_path / "existing.gif"
    existing.write_bytes(b"previous complete bytes")
    frames = (
        AnimationFrame("F000", _png(), VALID),
        AnimationFrame("F003", None, VALID + timedelta(hours=3), missing_reason="offline"),
    )
    with pytest.raises(AnimationExportError, match="Fail export"):
        write_animation_gif(
            existing, frames, mode="forecast-timeline", gap_policy="error",
            output_size=(640, 480),
        )
    assert existing.read_bytes() == b"previous complete bytes"
    assert list(tmp_path.iterdir()) == [existing]


def test_frame_preview_uses_same_composition_geometry_as_gif(tmp_path):
    frame = AnimationFrame("F000", _png("navy", (320, 240)), VALID, VALID, 0)
    preview = animation_frame_preview_png(
        frame, mode="forecast-timeline", output_size=(800, 600), theme="dark",
        context="KOUN · HRRR",
    )
    path = write_animation_gif(
        tmp_path / "preview.gif", (frame,), mode="forecast-timeline",
        output_size=(800, 600), theme="dark", context="KOUN · HRRR",
    )
    with Image.open(io.BytesIO(preview)) as image, Image.open(path) as gif:
        assert image.size == gif.size == (800, 600)
        assert image.getpixel((400, 200)) == gif.convert("RGB").getpixel((400, 200))
        assert image.getpixel((1, 598)) == gif.convert("RGB").getpixel((1, 598))
        manifest = json.loads(gif.info["comment"].decode("utf-8"))
        assert manifest["context"] == "KOUN · HRRR"
